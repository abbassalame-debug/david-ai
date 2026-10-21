"""
turbo_ingest.py — GPU-accelerated batch ingest for David v9.0

With the Ollama embedder (default since 2026-10-03) Ollama stays up and embedding
goes through /api/embed; the legacy bge-m3 path stops Ollama, embeds on CUDA, restarts it.
Writes live progress to <DAVID_HOME>\ingest_progress.json every 2 seconds.
Knowledge extraction is NOT run — do that separately after this completes.

Usage:
  python turbo_ingest.py [--batch-size 256] [--vaults-only]
"""

from ollama_config import OLLAMA_HOST
from david_config import DAVID_DATA, DAVID_HOME, DAVID_VAULTS
import sys, os, re, json, time, datetime, subprocess, argparse, signal, sqlite3, threading, tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import extractors
import embedding
import library_rules
import rag

PROGRESS_FILE  = str(DAVID_DATA / "ingest_progress.json")
# One "already indexed" cache per table: the cache describes what is IN a specific
# table, so sharing one across embedders would make a fresh table skip files it
# doesn't contain. The legacy "notes" table keeps the original filename.
CACHE_DB       = embedding.cache_db()
DB_PATH        = str(DAVID_DATA / "lancedb")
EMBED_MODEL    = embedding.model_name()
TABLE_NAME     = embedding.table_name()
CHUNK_SIZE     = 400
CHUNK_OVERLAP  = 50

# Polled every loop iteration so the UI's Stop button can ask this script to exit
# gracefully — a hard TerminateProcess() (what Process.terminate() does on
# Windows) would skip straight past the cleanup below that restarts Ollama.
STOP_SENTINEL = os.path.join(tempfile.gettempdir(), "david_stop_ingest.flag")


def _clear_stop_sentinel():
    try:
        os.remove(STOP_SENTINEL)
    except FileNotFoundError:
        pass
    except Exception:
        pass

VAULTS = {
    "system":          str(DAVID_VAULTS / "System"),
    "business":        str(DAVID_VAULTS / "Business"),
    "music":           str(DAVID_VAULTS / "Music"),
    "lore":            str(DAVID_VAULTS / "Lore"),
    "research":  str(DAVID_VAULTS / "Research"),
}

# Extra folders to index besides the vaults: <DAVID_DATA>/ingest_paths.json, or config/ingest_paths.example.json when that does not exist.
# Format: [{"vault": "business", "path": "/data/docs"}, {"vault": "music", "path": "samples", "mode": "docs_only"}]
# A relative path is relative to DAVID_HOME.
EXTRA_PATHS_FILE = str(DAVID_DATA / "ingest_paths.json")
if not os.path.exists(EXTRA_PATHS_FILE):
    EXTRA_PATHS_FILE = str(DAVID_HOME / "config" / "ingest_paths.example.json")

SKIP_DIRS   = {
    ".obsidian", ".git", ".cache", "node_modules", "__pycache__",
    "$recycle.bin", "system volume information", "appdata",
    "comfyui", "lancedb", "stems", "generated",
    ".smart-env",   # Obsidian Smart Connections' own cache; see rag.SKIP_DIRS
}

# Source/config file types extractors.py doesn't cover — read raw, no dispatch needed.
RAW_TEXT_EXTS = {'.py','.js','.ts','.html','.css',
                 '.yaml','.yml','.toml','.rst','.sh','.bat','.ps1',
                 '.c','.cpp','.h','.java','.xml','.ini','.cfg'}
AUDIO_EXTS  = {'.mp3','.flac','.wav','.m4a','.ogg','.aiff','.opus','.wma','.mid','.midi'}
VIDEO_EXTS  = {'.mp4','.avi','.mov','.mkv','.wmv','.m4v'}
DESIGN_EXTS = {'.psd','.ai','.indd','.sketch','.fig','.xd','.eps','.svg','.afdesign','.afphoto'}
CODE_EXTS   = {'.py','.js','.ts','.sh','.bat','.ps1','.c','.cpp','.h','.java','.cs'}

# Everything extractors.py knows how to dispatch (document/design/image/audio/video) —
# built from its own extension groups so this can't silently drift the way the old
# hand-duplicated DOC_EXTS/TEXT_EXTS did (e.g. claiming .xlsx support with no .xlsx
# branch in extract_text(), or never scanning .pptx/.rtf/.psd at all).
_EXTRACTOR_EXTS = (
    extractors.DOC_PDF | extractors.DOC_DOCX | extractors.DOC_PPTX |
    extractors.DOC_XLSX | extractors.DOC_RTF | extractors.TEXT_EXT |
    extractors.DESIGN | extractors.IMAGE | extractors.AUDIO | extractors.VIDEO
)

# Smart vault classifier — file type overrides directory assignment
def detect_vault(filepath: str, assigned_vault: str) -> str:
    ext = Path(filepath).suffix.lower()
    name = Path(filepath).stem.lower()
    if ext in AUDIO_EXTS:
        return 'music'
    if ext in DESIGN_EXTS:
        # Design files: music artwork stays in music, everything else in business
        music_hints = {'cover','artwork','art','sleeve','poster','album','track','beat','mix','ep','single'}
        if any(h in name for h in music_hints) or assigned_vault == 'music':
            return 'music'
        return 'business'
    if ext in CODE_EXTS:
        return 'system'
    if ext in VIDEO_EXTS:
        # Music videos → music; everything else → business
        music_hints = {'music','video','mv','clip','beat','studio','session','live','performance'}
        if any(h in name for h in music_hints) or assigned_vault == 'music':
            return 'music'
        return 'business'
    return assigned_vault

_stop = threading.Event()

# ── Progress ──────────────────────────────────────────────────────────────────

_prog = {
    "status": "idle", "phase": "", "files_total": 0, "files_done": 0,
    "files_skipped_cached": 0,   # already-ingested files this run found and skipped —
                                 # tracked separately so files_done/chunks_done=0 reads
                                 # as "nothing new to do", not "broken"
    "chunks_done": 0, "chunks_per_sec": 0.0, "eta_seconds": 0,
    "started_at": "", "errors": 0, "current_file": "",
}

def _write():
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(_prog, f)
    except Exception:
        pass

# ── Text extraction ───────────────────────────────────────────────────────────

def _body_words(text: str) -> int:
    """Words of real content, not counting the 'File: ... | path ...' header extractors.describe adds."""
    return len("\n".join(l for l in text.splitlines() if not l.startswith(("File:", "Path:"))).split())


def min_words_for(text: str, asset_type: str) -> int:
    """A file with nothing extractable (a photo, a drawing, a scanned PDF) still deserves a file-name
    entry. The 20-word floor is for real text; the header alone sits just under it when the name and
    path are short, which silently dropped A1.ai and D2.pdf in the 2026-10-04 test slice."""
    if asset_type in ("audio", "video", "image", "design", "file") or _body_words(text) < 5:
        return 3
    return 20


def extract_text(filepath: str) -> tuple:
    """Returns (text, asset_type). Document/design/image/audio/video formats
    delegate to extractors.describe() — the exact logic rag.py's import_folder
    uses — so this fast path and that deep path can't silently diverge on what
    a file's content looks like (this is also where front-matter stripping and
    real .xlsx/.pptx/.rtf/.psd extraction come from). Source/config files and a
    handful of legacy audio/video formats outside extractors.py's extension
    groups fall back to a direct or lightweight read.
    """
    ext = Path(filepath).suffix.lower()

    if ext in RAW_TEXT_EXTS:
        try:
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                return f.read().strip(), "note"
        except Exception:
            return "", "note"

    if ext in _EXTRACTOR_EXTS:
        return extractors.describe(filepath)

    if ext in AUDIO_EXTS:   # .wma/.mid/.midi — outside extractors.py's AUDIO set
        try:
            import mutagen
            meta = mutagen.File(filepath, easy=True)
            name = Path(filepath).stem
            lines = [f"Audio: {name}"]
            if meta:
                for tag in ("title", "artist", "album", "genre", "date", "tracknumber"):
                    val = meta.get(tag)
                    if val:
                        lines.append(f"{tag.capitalize()}: {val[0] if isinstance(val, list) else val}")
                if hasattr(meta, "info") and hasattr(meta.info, "length"):
                    m, s = divmod(int(meta.info.length), 60)
                    lines.append(f"Duration: {m}:{s:02d}")
            return "\n".join(lines), "audio"
        except Exception:
            return f"Audio: {Path(filepath).stem}", "audio"

    if ext in VIDEO_EXTS:   # .wmv — outside extractors.py's VIDEO set
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        return f"Video: {Path(filepath).stem} ({size_mb:.1f} MB)", "video"

    return "", "file"

# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, min_words: int = 20) -> list:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk.split()) >= min_words:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

# ── Ollama ────────────────────────────────────────────────────────────────────

def stop_ollama():
    for exe in ("ollama.exe", "ollama app.exe", "ollama_llama_server.exe", "llama-server.exe"):
        subprocess.run(["taskkill", "/f", "/im", exe],
                       capture_output=True, check=False)
    time.sleep(3)
    print("  Ollama stopped.", flush=True)

def start_ollama():
    try:
        subprocess.Popen(["ollama", "serve"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(3)
        print("  Ollama restarted.", flush=True)
    except Exception as e:
        print(f"  Ollama restart failed: {e}", flush=True)

def _ollama_alive():
    import urllib.request
    try:
        urllib.request.urlopen(OLLAMA_HOST + "/api/version", timeout=3).read()
        return True
    except Exception:
        return False

# ── Cache DB (skip unchanged files) ──────────────────────────────────────────

def _cache_conn():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS indexed "
        "(path TEXT PRIMARY KEY, mtime REAL, chunks INTEGER)"
    )
    conn.commit()
    return conn

def _bootstrap_cache(conn, table):
    """Seed cache from existing LanceDB entries so first run skips already-indexed files.

    Only seed a file as 'cached' when its current modification date still matches
    the date_modified stored at ingest time. Stamping the *current* mtime
    unconditionally (the old behavior) marked any file changed between its
    original ingest and this bootstrap as fresh forever, pinning a stale index
    entry. Files that changed since ingest are left out of the cache so the next
    run re-indexes them.
    """
    if conn.execute("SELECT COUNT(*) FROM indexed").fetchone()[0] > 0:
        return
    print("  Bootstrapping cache from existing index...", flush=True)
    try:
        rows = table.to_lance().to_table(
            columns=["source_path", "date_modified"]).to_pydict()
        paths = rows.get("source_path", [])
        dates = rows.get("date_modified", [])
        seen = {}   # path -> [chunk_count, indexed_date]
        for path, d in zip(paths, dates):
            if not path:
                continue
            if path in seen:
                seen[path][0] += 1
            else:
                seen[path] = [1, d]
        seeded = stale = 0
        for path, (n, indexed_date) in seen.items():
            try:
                mtime = os.path.getmtime(path)
                current_date = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                if current_date == indexed_date:
                    conn.execute("INSERT OR IGNORE INTO indexed VALUES (?,?,?)",
                                 (path, mtime, n))
                    seeded += 1
                else:
                    stale += 1   # changed since ingest — leave uncached, re-index next run
            except Exception:
                pass
        conn.commit()
        print(f"  Cache seeded with {seeded} files "
              f"({stale} changed-since-ingest left for re-index).", flush=True)
    except Exception as e:
        print(f"  Cache bootstrap skipped: {e}", flush=True)

def is_cached(path, mtime, conn) -> bool:
    row = conn.execute("SELECT mtime FROM indexed WHERE path=?", (path,)).fetchone()
    return row is not None and abs(row[0] - mtime) < 1.0

def mark_cached(path, mtime, n, conn):
    conn.execute("INSERT OR REPLACE INTO indexed VALUES (?,?,?)", (path, mtime, n))
    conn.commit()

# ── Main ──────────────────────────────────────────────────────────────────────

def run(batch_size: int = 32, vaults_only: bool = False, only_path: str = None,
        limit_files: int = None, seed: int = 0, only_files: list = None):
    """only_path: scan just that folder from ingest_paths.json (skips the five vaults).
    limit_files: process a random sample of exactly that many files (seeded, repeatable) -- a
    test slice. Files in a slice are cached afterwards, so the full run skips them.
    only_files: process just these files (absolute paths) -- for re-ingesting specific files
    after an extraction fix. They must sit under a folder this run scans."""
    import lancedb, pyarrow as pa

    # A sentinel left over from a run that crashed before reaching cleanup would
    # otherwise abort this run instantly.
    _clear_stop_sentinel()

    if embedding.BACKENDS[embedding.ACTIVE]["kind"] == "ollama":
        # The embedder IS an Ollama model, so Ollama has to stay up (the old path
        # killed it to free VRAM for CUDA bge-m3). A 0.6B embedder is ~1 GB and
        # coexists with the resident chat models, so nothing needs evicting.
        _prog.update({"status": "starting", "phase": "loading model",
                      "started_at": datetime.datetime.now().isoformat()})
        _write()
        print(f"  Embedding with {EMBED_MODEL} via Ollama (server stays up).", flush=True)
        embedder = embedding.get_embedder()
        embedder.encode("warmup")
        print("  Model ready.", flush=True)
    else:
        from sentence_transformers import SentenceTransformer
        _prog.update({"status": "starting", "phase": "stopping ollama",
                      "started_at": datetime.datetime.now().isoformat()})
        _write()

        stop_ollama()

        _prog["phase"] = "loading model"
        _write()
        print(f"  Loading {EMBED_MODEL} on CUDA (fp16)...", flush=True)
        import torch
        embedder = SentenceTransformer(
            EMBED_MODEL, device="cuda",
            model_kwargs={"torch_dtype": torch.float16},
        )
        print("  Model ready.", flush=True)

    # Collect paths
    scan = {} if only_path else dict(VAULTS)
    docs_only_roots = set()   # folders marked "mode": "docs_only" -- see library_rules.py
    if only_path or not vaults_only:
        try:
            extra = json.load(open(EXTRA_PATHS_FILE, encoding="utf-8"))
            for e in extra:
                vault = e.get("vault", "system")
                path  = e.get("path", "")
                if path and not os.path.isabs(path):
                    path = str(DAVID_HOME / path)
                if only_path and os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(only_path)):
                    continue
                if path and os.path.isdir(path):
                    # Use a unique key to allow multiple paths per vault
                    key = f"{vault}|{path}"
                    scan[key] = (vault, path)
                    if e.get("mode") == "docs_only":
                        docs_only_roots.add(path)
        except Exception:
            pass
    if only_path and not scan:
        raise SystemExit(f"--only-path {only_path!r} is not an existing folder listed in {EXTRA_PATHS_FILE}")

    # Scan files. The CRM exclusion inside is rag.is_excluded_path -- raw chat transcripts hold
    # unverified AI replies and must never become RAG ground truth (a wrong logged answer was
    # once re-ingested here and fed back into the next identical question, 2026-09-29).
    _prog["phase"] = "scanning"
    _write()
    supported = RAW_TEXT_EXTS | _EXTRACTOR_EXTS | AUDIO_EXTS | VIDEO_EXTS
    all_files, library_entries = library_rules.scan_files(
        scan, supported, SKIP_DIRS, rag.is_excluded_path, docs_only_roots)   # [(vault, root, path, is_extra)]
    if only_files:
        wanted = {os.path.normcase(os.path.normpath(p)) for p in only_files}
        all_files = [f for f in all_files if os.path.normcase(os.path.normpath(f[2])) in wanted]
        found = {os.path.normcase(os.path.normpath(f[2])) for f in all_files}
        for p in sorted(wanted - found):
            print(f"  NOT FOUND in the scanned folders (skipped): {p}", flush=True)
        library_entries = []   # re-ingesting named files must not rewrite a folder's summary entry
    if limit_files and len(all_files) > limit_files:
        import random
        print(f"  Test slice: {limit_files} of {len(all_files):,} files (random sample, seed {seed}).", flush=True)
        all_files = sorted(random.Random(seed).sample(all_files, limit_files), key=lambda f: f[2])
        library_entries = []   # a slice must not rewrite a folder's summary entry

    _prog["files_total"] = len(all_files)
    print(f"  Found {len(all_files):,} files.", flush=True)
    _write()

    # Open LanceDB
    schema = pa.schema([
        pa.field("id",            pa.string()),
        pa.field("vault",         pa.string()),
        pa.field("file",          pa.string()),
        pa.field("chunk_index",   pa.int32()),
        pa.field("text",          pa.string()),
        pa.field("date_modified", pa.string()),
        pa.field("source_path",   pa.string()),
        pa.field("asset_type",    pa.string()),
        pa.field("vector",        pa.list_(pa.float32(), 1024)),
    ])
    db = lancedb.connect(DB_PATH)
    try:
        table = db.open_table(TABLE_NAME)
        if "source_path" not in table.schema.names:
            db.drop_table(TABLE_NAME)
            table = db.create_table(TABLE_NAME, schema=schema)
    except Exception:
        table = db.create_table(TABLE_NAME, schema=schema)

    cache = _cache_conn()
    _bootstrap_cache(cache, table)

    if library_entries:
        library_rules.add_library_entries(table, embedder, library_entries, chunk_text, embedding.doc_text)
        print(f"  Wrote {len(library_entries)} library-folder summary entr{'y' if len(library_entries) == 1 else 'ies'}.", flush=True)

    # Batch accumulators
    pending_text = []
    pending_meta = []
    t_start = time.time()
    last_write = t_start

    def flush():
        if not pending_text:
            return
        bs = batch_size
        while bs >= 4:
            try:
                vecs = embedder.encode(
                    pending_text, batch_size=bs,
                    normalize_embeddings=True, show_progress_bar=False,
                )
                break
            except RuntimeError as e:
                if "out of memory" in str(e).lower():
                    if bs <= 4:
                        raise
                    import torch
                    torch.cuda.empty_cache()
                    bs = max(bs // 2, 4)
                    print(f"  OOM — retrying with batch_size={bs}", flush=True)
                else:
                    raise
        rows = [dict(m, vector=v.tolist()) for m, v in zip(pending_meta, vecs)]
        table.add(rows)
        _prog["chunks_done"] += len(rows)
        pending_text.clear()
        pending_meta.clear()

    _prog["phase"] = "embedding"
    _prog["status"] = "running"

    for fi, (vault_name, vault_root, filepath, is_extra) in enumerate(all_files):
        if _stop.is_set() or os.path.exists(STOP_SENTINEL):
            break

        _prog["files_done"] = fi
        _prog["current_file"] = Path(filepath).name

        try:
            mtime = os.path.getmtime(filepath)
        except Exception:
            continue

        if is_cached(filepath, mtime, cache):
            _prog["files_skipped_cached"] += 1
            # Update progress periodically even when skipping
            now = time.time()
            if now - last_write >= 2.0:
                _write()
                last_write = now
            continue

        text, asset_type = extract_text(filepath)
        min_words = min_words_for(text, asset_type)
        if not text or len(text.split()) < min_words:
            mark_cached(filepath, mtime, 0, cache)
            continue

        chunks = chunk_text(text, min_words=min_words)
        if not chunks:
            mark_cached(filepath, mtime, 0, cache)
            continue

        # Reclassify by file type ONLY for extra import paths (D:\...). The five
        # canonical vaults keep their assigned namespace — reclassifying them
        # would leak firewalled content (e.g. a Research .wav) into 'music'
        # and diverge IDs from what `rag.py import` produces for the same file.
        if is_extra:
            vault_name = detect_vault(filepath, vault_name)
        # This file is not cache-fresh (new or modified), so any prior rows for
        # it are stale — delete before re-adding, or a modified file accumulates
        # duplicate chunks next to the outdated ones (source_path is an exact
        # match here, so no LIKE-wildcard escaping is needed).
        try:
            table.delete("source_path = '{}'".format(filepath.replace("'", "''")))
        except Exception:
            pass
        mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        try:
            rel = os.path.relpath(filepath, vault_root)
        except ValueError:
            rel = filepath

        for ci, chunk in enumerate(chunks):
            pending_meta.append({
                "id":            f"{vault_name}::{rel}::{ci}",
                "vault":         vault_name,
                "file":          rel,
                "chunk_index":   ci,
                "text":          chunk,
                "date_modified": mtime_str,
                "source_path":   filepath,
                "asset_type":    asset_type,
            })
            pending_text.append(embedding.doc_text(rel, chunk))
            if len(pending_text) >= batch_size:
                flush()

        flush()
        mark_cached(filepath, mtime, len(chunks), cache)

        now = time.time()
        if now - last_write >= 2.0:
            elapsed = max(now - t_start, 1)
            cps = _prog["chunks_done"] / elapsed
            _prog["chunks_per_sec"] = round(cps, 1)
            # Rough ETA: remaining files proportional to done so far
            frac = max(fi, 1) / len(all_files)
            total_est = _prog["chunks_done"] / max(frac, 0.001)
            remaining = total_est - _prog["chunks_done"]
            _prog["eta_seconds"] = int(remaining / max(cps, 1))
            _write()
            last_write = now

    flush()
    cache.close()

    # rag.py's retrieve() does hybrid vector+BM25 search — without rebuilding
    # the FTS index here, everything turbo_ingest.py just embedded is
    # invisible to the keyword half of every query until someone separately
    # runs `rag.py ingest`.
    _prog["phase"] = "building search index"
    _write()
    rag._ensure_fts(table)

    elapsed = time.time() - t_start
    _prog.update({
        "status":        "done",
        "phase":         "complete",
        "files_done":    len(all_files),
        "chunks_per_sec": round(_prog["chunks_done"] / max(elapsed, 1), 1),
        "eta_seconds":   0,
        "current_file":  "",
    })
    _write()
    print(
        f"\n  Done — {_prog['chunks_done']:,} chunks embedded, "
        f"{_prog['files_skipped_cached']:,} files already cached and skipped, "
        f"in {elapsed/60:.1f} min.", flush=True
    )
    # start_ollama() is intentionally NOT called here — the caller's finally
    # (see __main__) guarantees it on every exit path, so a crash anywhere above
    # no longer leaves Ollama (and therefore the router, HUD chat, and memory)
    # permanently stopped.


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--vaults-only", action="store_true")
    ap.add_argument("--only-path", default=None, help="index just this folder from ingest_paths.json")
    ap.add_argument("--limit-files", type=int, default=None, help="process a random sample of exactly N files (test slice)")
    ap.add_argument("--seed", type=int, default=0, help="seed for --limit-files, so a slice is repeatable")
    ap.add_argument("--only-files", default=None, help="JSON file holding a list of absolute paths to (re)ingest, and nothing else")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: _stop.set())

    try:
        only_files = json.load(open(args.only_files, encoding="utf-8")) if args.only_files else None
        run(args.batch_size, args.vaults_only, args.only_path, args.limit_files, args.seed, only_files)
    except KeyboardInterrupt:
        _prog["status"] = "cancelled"
        _write()
    except Exception as e:
        _prog["status"] = "error"
        _prog["phase"] = f"crashed: {type(e).__name__}: {e}"
        _write()
        print(f"  turbo_ingest crashed: {type(e).__name__}: {e}", flush=True)
        raise
    finally:
        # Always bring Ollama back and clear the stop flag — a crash, OOM, or
        # cancellation mid-run must never leave the model server dead. Only start
        # a server if none is answering: with the Ollama embedder it never went down,
        # and a second `ollama serve` just fights the first one for the port.
        if not _ollama_alive():
            start_ollama()
        _clear_stop_sentinel()
