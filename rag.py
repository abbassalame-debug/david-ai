# rag.py -- David v9.0 Step 20
# LanceDB vector index for all 5 Obsidian vaults. Which embedder and which table
# are active is decided in embedding.py (Qwen3-Embedding via Ollama since 2026-10-03;
# bge-m3 + the "notes" table remain available via DAVID_EMBEDDER=bge-m3).
# Per-agent namespace filters enforce vault isolation in code.
# Run: python rag.py ingest        -- full ingest of all vaults
# Run: python rag.py query <agent> <text>  -- retrieve for an agent

from david_config import DAVID_DATA, DAVID_VAULTS, load_policy, is_firewalled_vault, firewalled_vaults
import sys
import os
import re
import json
import datetime

sys.stdout.reconfigure(encoding="utf-8")

import embedding
import library_rules

DB_PATH   = str(DAVID_DATA / "lancedb")
EMBED_MODEL = embedding.model_name()
TABLE_NAME  = embedding.table_name()
CHUNK_SIZE  = 400    # words per chunk
CHUNK_OVERLAP = 50

# turbo_ingest.py's "already indexed" cache — cleared whenever ingest_all()
# drops the notes table, so a stale cache never makes turbo_ingest.py skip
# files that no longer exist in a freshly rebuilt index.
TURBO_CACHE_DB = embedding.cache_db()

_POLICY = load_policy()      # config/vaults.toml, or config/vaults.example.toml (vault list, per-agent namespaces, cross-vault fallback)

# namespace id -> folder
VAULTS = {name: str(DAVID_VAULTS / folder) for name, folder in _POLICY["vaults"].items()}

# Per-agent primary vaults. An agent that is not listed gets no vault at all (fail closed).
AGENT_NAMESPACES = {agent: list(v) for agent, v in _POLICY["agent_namespaces"].items()}

# Cross-vault fallback: when primary search returns < MIN_RESULTS, also search these additional vaults automatically.
CROSS_VAULT_FALLBACK = {name: list(v) for name, v in _POLICY["cross_vault_fallback"].items()}
MIN_RESULTS_BEFORE_FALLBACK = 3

_embedder    = None
_db          = None
_table       = None
_fts_indexed = False


def get_embedder():
    return embedding.get_embedder()


def get_table():
    global _db, _table
    if _table is None:
        import lancedb
        import pyarrow as pa
        _db = lancedb.connect(DB_PATH)
        schema = pa.schema([
            pa.field("id",             pa.string()),
            pa.field("vault",          pa.string()),
            pa.field("file",           pa.string()),
            pa.field("chunk_index",    pa.int32()),
            pa.field("text",           pa.string()),
            pa.field("date_modified",  pa.string()),
            pa.field("source_path",    pa.string()),
            pa.field("asset_type",     pa.string()),
            pa.field("vector",         pa.list_(pa.float32(), 1024)),
        ])
        try:
            existing = _db.open_table(TABLE_NAME)
            if "source_path" not in existing.schema.names:
                print("  Upgrading index schema (+source_path, +asset_type)...", flush=True)
                _db.drop_table(TABLE_NAME)
                _table = _db.create_table(TABLE_NAME, schema=schema)
            else:
                _table = existing
        except Exception:
            _table = _db.create_table(TABLE_NAME, schema=schema)
    return _table


def chunk_text(text):
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunks.append(" ".join(words[start:end]))
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ── Paths that must never be embedded ─────────────────────────────────────────
# vaults/System/CRM/interactions/<date>.md is the HUD's chat log: every turn's
# unverified model reply gets appended there. Embedding it turns a wrong answer
# into "precedent" for the next similar question (feedback loop seen live
# 2026-09-29 via turbo_ingest, and again 2026-10-03 via the HUD vault watcher ->
# `rag.py import <that folder>`: 45 rows in notes_q3e). memory.py handles
# user-only episodic recall separately. EVERY ingest path -- ingest_file,
# import_folder, file_watcher.py, turbo_ingest.py and server._vault_watcher --
# must ask this one function, so a new path can't forget the rule.
EXCLUDED_PATH_MARKERS = ("crm\\interactions",)


def is_excluded_path(path: str) -> bool:
    p = str(path).replace("/", "\\").lower()
    return any(m in p for m in EXCLUDED_PATH_MARKERS)


def ingest_file(vault_name, filepath):
    if is_excluded_path(filepath) or library_rules.is_junk_file(filepath):
        return 0

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
    except Exception:
        return 0

    # Strip Obsidian front-matter
    content = re.sub(r"^---[\s\S]*?---\n", "", content).strip()
    if not content:
        return 0

    chunks   = chunk_text(content)
    embedder = get_embedder()
    table    = get_table()
    mtime    = datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d")
    rel_path = os.path.relpath(filepath, VAULTS[vault_name])

    rows = []
    for i, chunk in enumerate(chunks):
        vec = embedder.encode(embedding.doc_text(rel_path, chunk), normalize_embeddings=True).tolist()
        rows.append({
            "id":            f"{vault_name}::{rel_path}::{i}",
            "vault":         vault_name,
            "file":          rel_path,
            "chunk_index":   i,
            "text":          chunk,
            "date_modified": mtime,
            "source_path":   filepath,
            "asset_type":    "note",
            "vector":        vec,
        })

    if rows:
        table.add(rows)
    return len(rows)


def ingest_vault(vault_name):
    root  = VAULTS[vault_name]
    if not os.path.exists(root):
        print(f"  Vault not found: {root}")
        return 0
    total = 0
    for dirpath, _, files in os.walk(root):
        for fname in files:
            if fname.endswith(".md"):
                n = ingest_file(vault_name, os.path.join(dirpath, fname))
                total += n
    return total


# ── Safety rail for the full rebuild ──────────────────────────────────────────
# ingest_all() DROPS the active table and rebuilds only the 5 native vaults. On
# 2026-09-25 an automated tool ran `rag.py ingest` believing it was a harmless
# re-index and destroyed the ~103k-file corpus (61 rows survived). So:
#   * confirm=True is mandatory (CLI: --confirm-drop) -- a bare call refuses;
#   * a table with more than INGEST_DROP_THRESHOLD rows is refused unless
#     force_drop_populated=True (CLI: --force-drop-populated);
#   * before any drop, the current rows are exported to a timestamped Parquet
#     file (vectors included, restorable with restore_backup()) and the row
#     count is logged.
INGEST_DROP_THRESHOLD = 1000
BACKUP_DIR = str(DAVID_DATA / "lancedb_backups")


def backup_table(table_name=None):
    """Export every row of `table_name` (current version only) to
    BACKUP_DIR\\<table>_<timestamp>.parquet. Returns (path, row_count)."""
    import lancedb
    import pyarrow.parquet as pq
    table_name = table_name or TABLE_NAME
    db = lancedb.connect(DB_PATH)
    tbl = db.open_table(table_name)
    n = tbl.count_rows()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"{table_name}_{ts}.parquet")
    ds = tbl.to_lance()
    writer = None
    try:
        for batch in ds.to_batches():
            if writer is None:
                writer = pq.ParquetWriter(path, batch.schema)
            writer.write_batch(batch)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:   # empty table: still leave a (schema-only) file
        pq.write_table(ds.to_table(), path)
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump({"table": table_name, "rows": n, "backed_up_at": ts}, f)
    print(f"  Backup written: {path} ({n} rows)", flush=True)
    return path, n


def restore_backup(parquet_path, table_name=None):
    """Recreate `table_name` from a backup_table() file (drops the current table)."""
    import lancedb
    import pyarrow.parquet as pq
    table_name = table_name or TABLE_NAME
    db = lancedb.connect(DB_PATH)
    data = pq.read_table(parquet_path)
    try:
        db.drop_table(table_name)
    except Exception:
        pass
    tbl = db.create_table(table_name, data=data)
    _ensure_fts(tbl)
    print(f"  Restored {tbl.count_rows()} rows into {table_name}", flush=True)
    return tbl.count_rows()


def ingest_all(confirm=False, force_drop_populated=False):
    import lancedb
    if not confirm:
        print("  REFUSED: ingest_all() drops the whole active table "
              f"('{TABLE_NAME}') and rebuilds only the 5 native vaults. "
              "Call with confirm=True / run `rag.py ingest --confirm-drop` on purpose.",
              flush=True)
        return None
    db = lancedb.connect(DB_PATH)
    try:
        existing_rows = db.open_table(TABLE_NAME).count_rows()
    except Exception:
        existing_rows = 0
    if existing_rows > INGEST_DROP_THRESHOLD and not force_drop_populated:
        print(f"  REFUSED: table '{TABLE_NAME}' holds {existing_rows} rows "
              f"(> {INGEST_DROP_THRESHOLD}). A full ingest would drop them all and "
              "rebuild only the 5 native vaults. Pass force_drop_populated=True / "
              "`--force-drop-populated` if that is really what you want.", flush=True)
        return None
    if existing_rows:
        backup_table(TABLE_NAME)
    print(f"  Dropping table '{TABLE_NAME}' ({existing_rows} rows) for a clean ingest...", flush=True)
    # Drop and recreate table for a clean ingest
    try:
        db.drop_table(TABLE_NAME)
    except Exception:
        pass
    global _table
    _table = None

    # The table was just wiped — turbo_ingest.py's cache of "already indexed"
    # files is now entirely stale (it would skip files that no longer exist
    # in the index at all). Clear it so the next turbo_ingest.py run re-scans
    # everything instead of silently under-indexing.
    if os.path.exists(TURBO_CACHE_DB):
        import sqlite3
        try:
            conn = sqlite3.connect(TURBO_CACHE_DB)
            conn.execute("DELETE FROM indexed")
            conn.commit()
            conn.close()
        except Exception:
            pass

    total = 0
    for vault_name in VAULTS:
        print(f"  Ingesting vault: {vault_name}...", flush=True)
        n = ingest_vault(vault_name)
        print(f"    {n} chunks", flush=True)
        total += n
    print(f"\n  Total chunks indexed: {total}")
    _ensure_fts(get_table())
    return total


# ── Import arbitrary folders (documents + creative assets) ────────────────────

# A firewalled vault ([boundaries].firewalled; research in the example policy) is deliberately NOT importable.
IMPORT_DOMAINS = {v for v in VAULTS if not is_firewalled_vault(v)}

SKIP_DIRS = {
    ".obsidian", ".git", ".cache", "node_modules", "__pycache__",
    "$recycle.bin", "system volume information", "appdata",
    "comfyui", "lancedb", "stems", "generated",
    ".smart-env",   # Obsidian Smart Connections' own cache: 28 content-free stubs that outranked the real notes (2026-10-17)
}
MAX_BODY_BYTES = 200 * 1024 * 1024   # above this: catalog filename/path only


def import_folder(root, domain="business", analyze_audio=False):
    import extractors
    root = os.path.abspath(root)
    if domain not in IMPORT_DOMAINS or is_firewalled_vault(domain):   # checked again at call time, not only at start-up
        print(f"  Domain must be one of {sorted(IMPORT_DOMAINS)} "
              f"(firewalled: {', '.join(sorted(firewalled_vaults()))}). Got: {domain!r}")
        return
    if not os.path.isdir(root):
        print(f"  Not a folder: {root}")
        return

    # Load lancedb/pyarrow BEFORE torch — reversing this order segfaults on Windows
    # (native OpenMP/threading clash between torch and arrow).
    table = get_table()

    if is_excluded_path(root + os.sep):
        print(f"  Refusing to import {root}: chat transcripts are never embedded "
              "(see is_excluded_path).")
        return

    # Idempotent re-import: clear any prior rows from this root first.
    # The old `LIKE '{root}%'` predicate never matched Windows paths at all —
    # DataFusion treats backslash as the LIKE escape character, and `_` in a
    # folder name is a single-char wildcard — so re-imports silently accumulated
    # duplicate chunks. starts_with() is a literal prefix compare (verified
    # against lancedb: deletes exactly the rows under root, nothing else); the
    # trailing separator keeps "<DAVID_HOME>" from also clearing "<DAVID_HOME>_test".
    esc = (root + os.sep).replace("'", "''")
    try:
        table.delete(f"starts_with(source_path, '{esc}')")
    except Exception:
        pass

    embedder = get_embedder()

    print(f"  Importing {root}  →  domain '{domain}'", flush=True)
    n_files = n_chunks = n_assets = 0
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS
                       and not is_excluded_path(os.path.join(dirpath, d, ""))]
        for fname in files:
            fpath = os.path.join(dirpath, fname)
            if is_excluded_path(fpath) or library_rules.is_junk_file(fpath):
                continue
            try:
                catalog_only = os.path.getsize(fpath) > MAX_BODY_BYTES
            except OSError:
                continue
            text, atype = extractors.describe(
                fpath, analyze_audio=analyze_audio, catalog_only=catalog_only
            )
            if not text:
                continue
            rel  = os.path.relpath(fpath, root)
            rows = []
            for i, chunk in enumerate(chunk_text(text)):
                vec = embedder.encode(embedding.doc_text(rel, chunk), normalize_embeddings=True).tolist()
                rows.append({
                    # Relative-path ID (not fpath) so this matches the scheme
                    # turbo_ingest.py uses for the same logical import — a
                    # folder ingested by either tool produces the same IDs
                    # and re-ingesting via the other tool overwrites cleanly
                    # instead of creating a duplicate row set.
                    "id":            f"{domain}::{rel}::{i}",
                    "vault":         domain,
                    "file":          rel,
                    "chunk_index":   i,
                    "text":          chunk,
                    "date_modified": _file_mtime(fpath),
                    "source_path":   fpath,
                    "asset_type":    atype,
                    "vector":        vec,
                })
            if rows:
                table.add(rows)
                n_files  += 1
                n_chunks += len(rows)
                if atype not in ("document", "note"):
                    n_assets += 1
                if n_files % 25 == 0:
                    print(f"    ...{n_files} files, {n_chunks} chunks", flush=True)

    print(f"\n  Imported {n_files} files — {n_chunks} chunks "
          f"({n_assets} non-text assets cataloged) into '{domain}'.")
    _ensure_fts(table)


def _file_mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        return "?"


def stats():
    table = get_table()
    print(f"  Index: {table.count_rows()} chunks total\n")
    print("  By domain:")
    for v in ["system", "business", "music", "lore", "research", "books"]:
        try:
            c = table.count_rows(filter=f"vault = '{v}'")
        except Exception:
            c = 0
        if c:
            print(f"    {v:16s} {c}")
    print("\n  By asset type:")
    for t in ["note", "document", "design", "image", "audio", "video", "file"]:
        try:
            c = table.count_rows(filter=f"asset_type = '{t}'")
        except Exception:
            c = 0
        if c:
            print(f"    {t:16s} {c}")


def _ensure_fts(table):
    """Build or rebuild the full-text (BM25) index on the text column."""
    global _fts_indexed
    try:
        table.create_fts_index("text", replace=True)
        _fts_indexed = True
        print("  FTS index built.", flush=True)
    except Exception as e:
        _fts_indexed = False
        print(f"  FTS index skipped: {e}", flush=True)


# Calibrated 2026-09-24 against this deployment's real bge-m3 embeddings, not
# guessed: genuinely relevant matches (a query with real on-topic vault content)
# scored 0.38-0.42 cosine distance; queries with NOTHING relevant in the vaults
# still returned a "top match" (vector search always returns something) at
# 0.50-0.57 every time, regardless of query topic — e.g. a plain math question
# with no business/personal relevance pulled in an unrelated memory plus a
# minified Obsidian plugin .js file at 0.53-0.57, which then measurably
# degraded david-ft's answer (see session notes: this was root cause of a live
# wrong-answer bug). 0.45 sits cleanly between the two clusters.
# That calibration is bge-m3's. Distance distributions differ per embedder, so the
# live value now comes from embedding.BACKENDS (re-calibrated for Qwen3-Embedding on
# 2026-10-03 -- see the handoff note for the measured clusters).
MAX_RELEVANT_DISTANCE = embedding.max_distance()

# Same calibration exercise for BM25: a genuine keyword match on this deployment
# (a query sharing real content with an indexed document) scored _score≈34;
# short generic tokens shared only by coincidence (plain numbers like "15" or
# "340" matching an unrelated audio filename or course chapter's page numbers)
# scored _score≈10-12 every time. BM25 doesn't have vector search's "always
# returns something" problem, but a query built entirely from generic tokens
# (a bare arithmetic question) still pulls in coincidental low-score noise.
MIN_FTS_SCORE = 15


def permitted_vaults(agent):
    """Every vault `agent` may receive rows from: its namespaces plus the cross-vault fallback configured for them
    (config/vaults.toml [cross_vault_fallback] -- an explicit policy). Unknown agent -> empty set."""
    allowed = AGENT_NAMESPACES.get(agent, []) if isinstance(agent, str) else []
    out = set(allowed)
    for v in allowed:
        out.update(CROSS_VAULT_FALLBACK.get(v, []))
    return out


def _authorized(rows, agent):
    """Final authorization check on what retrieve() hands back, whichever search path produced the rows (vector,
    BM25, the BM25 fallback without a prefilter, RRF fusion, cross-vault fallback)."""
    ok = permitted_vaults(agent)
    return [r for r in rows if r.get("vault") in ok]


def retrieve(agent, query, top_k=5):
    # Fail CLOSED: unknown agent gets no vaults, never everything.
    allowed = AGENT_NAMESPACES.get(agent, [])
    if not allowed:
        return []

    # lancedb/pyarrow before torch — avoids Windows native segfault on import order.
    table    = get_table()
    embedder = get_embedder()

    vec          = embedder.encode(query, normalize_embeddings=True, is_query=True).tolist()
    vault_filter = " OR ".join([f"vault = '{v}'" for v in allowed])
    fetch        = top_k * 3

    # ── Vector search (semantic similarity) ──────────────────────────────────
    # Cosine search always returns `fetch` rows even when nothing is actually
    # relevant (there's no "no match" outcome, only "closest available") — so
    # filter by distance before this feeds anything downstream. FTS hits below
    # don't carry a comparable score and are left as-is: BM25 only returns rows
    # that share actual keywords, so it doesn't have the "always returns
    # something" problem vector search does.
    vec_hits = [
        r for r in (
            table.search(vec, vector_column_name="vector")
                 .metric("cosine")
                 .where(vault_filter, prefilter=True)
                 .limit(fetch)
                 .to_list()
        )
        if r.get("_distance", 0) <= MAX_RELEVANT_DISTANCE
    ]

    # ── BM25 full-text search (keyword precision) ─────────────────────────────
    fts_hits = []
    try:
        raw = (
            table.search(query, query_type="fts")
                 .where(vault_filter, prefilter=True)
                 .limit(fetch)
                 .to_list()
        )
        fts_hits = [r for r in raw if r.get("_score", 0) >= MIN_FTS_SCORE]
    except Exception:
        try:
            raw = (
                table.search(query, query_type="fts")
                     .limit(fetch)
                     .to_list()
            )
            fts_hits = [r for r in raw if r.get("vault") in allowed and r.get("_score", 0) >= MIN_FTS_SCORE]
        except Exception:
            pass  # FTS index not built yet — vector-only this call

    if not fts_hits:
        # Vector-only this call. This used to return here, which skipped the configured cross-vault fallback below
        # in exactly the "thin results" case it exists for (audit finding M-03; fixed with P0-B on 2026-10-18).
        primary = vec_hits[:top_k]
    else:
        # ── Reciprocal Rank Fusion (RRF k=60) ─────────────────────────────────
        # Each result scores 1/(60+rank). Appearing in both lists doubles the score.
        RRF_K  = 60
        scores = {}
        by_id  = {}
        for rank, r in enumerate(vec_hits):
            rid = r["id"]
            scores[rid] = scores.get(rid, 0) + 1.0 / (RRF_K + rank + 1)
            by_id[rid]  = r
        for rank, r in enumerate(fts_hits):
            rid = r["id"]
            scores[rid] = scores.get(rid, 0) + 1.0 / (RRF_K + rank + 1)
            by_id.setdefault(rid, r)

        ranked = sorted(by_id.values(), key=lambda r: scores[r["id"]], reverse=True)
        primary = ranked[:top_k]

    # ── Cross-vault fallback ───────────────────────────────────────────────────
    # If primary results are thin, silently expand into related vaults.
    if len(primary) < MIN_RESULTS_BEFORE_FALLBACK:
        extra_vaults = []
        for v in allowed:
            for fv in CROSS_VAULT_FALLBACK.get(v, []):
                if fv not in allowed and fv not in extra_vaults:
                    extra_vaults.append(fv)
        if extra_vaults:
            fallback_filter = " OR ".join([f"vault = '{v}'" for v in extra_vaults])
            needed = top_k - len(primary)
            try:
                fb_hits = [
                    r for r in (
                        table.search(vec, vector_column_name="vector")
                             .metric("cosine")
                             .where(fallback_filter, prefilter=True)
                             .limit(needed * 2)
                             .to_list()
                    )
                    if r.get("_distance", 0) <= MAX_RELEVANT_DISTANCE
                ]
                seen = {r["id"] for r in primary}
                for r in fb_hits:
                    if r["id"] not in seen and len(primary) < top_k:
                        primary.append(r)
                        seen.add(r["id"])
            except Exception:
                pass

    return _authorized(primary, agent)


def format_results(results):
    lines = []
    for r in results:
        lines.append(f"[{r['vault']} / {r['file']}]\n{r['text']}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python rag.py ingest --confirm-drop       — DROP the table and re-index the 5 vaults")
        print("        (+ --force-drop-populated if the table has > 1000 rows; a Parquet backup is written first)")
        print("  python rag.py backup [table]              — export the table to lancedb_backups\\")
        print("  python rag.py restore <file.parquet>      — recreate the table from a backup")
        print("  python rag.py import \"<folder>\" <domain>   — add a folder (domain: system|business|music|lore)")
        print("  python rag.py import \"<folder>\" music --analyze-audio   — + BPM/key on audio")
        print("  python rag.py query <agent> <text>        — retrieve as an agent")
        print("  python rag.py stats                       — index breakdown")
        print("  python rag.py count                       — total chunks")
        sys.exit(0)

    if args[0] == "ingest":
        ingest_all(confirm="--confirm-drop" in args,
                   force_drop_populated="--force-drop-populated" in args)

    elif args[0] == "backup":
        backup_table(args[1] if len(args) >= 2 else None)

    elif args[0] == "restore" and len(args) >= 2:
        restore_backup(args[1], args[2] if len(args) >= 3 else None)

    elif args[0] == "import" and len(args) >= 2:
        folder        = args[1]
        domain        = args[2] if len(args) >= 3 and not args[2].startswith("--") else "business"
        analyze_audio = "--analyze-audio" in args
        import_folder(folder, domain, analyze_audio=analyze_audio)

    elif args[0] == "stats":
        stats()

    elif args[0] == "query" and len(args) >= 3:
        agent = args[1]
        query = " ".join(args[2:])
        table  = get_table()
        count  = table.count_rows()
        print(f"  Index has {count} chunks. Querying as agent: {agent}")
        results = retrieve(agent, query)
        print(f"  {len(results)} results found.\n")
        print(format_results(results))

    elif args[0] == "count":
        table = get_table()
        print(f"  Index: {table.count_rows()} chunks total")

    else:
        print("Usage: python rag.py ingest | query <agent> <text>")
