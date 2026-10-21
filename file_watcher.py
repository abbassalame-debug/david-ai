# file_watcher.py — David continuous file indexer
# Watches configured folders and incrementally updates LanceDB when files
# are added, modified, or deleted — without re-scanning the whole folder.
#
# Usage:
#   py -3.11 file_watcher.py          — start watching (runs forever)
#   pythonw file_watcher.py           — start silently in background (no console)
#   py -3.11 file_watcher.py --status — show active watches and index stats
#
# Config: <DAVID_HOME>\watch_config.json
# Log:    <DAVID_HOME>\file_watcher.log

from david_config import DAVID_DATA, DAVID_HOME
import os
import sys
import json
import time
import logging
import threading
import embedding

sys.path.insert(0, str(DAVID_HOME))

WATCH_CONFIG = str(DAVID_DATA / "watch_config.json")
LOG_PATH     = str(DAVID_DATA / "file_watcher.log")
DEBOUNCE_SEC = 10      # seconds of quiet after last event before indexing
MAX_FILE_MB  = 200     # files larger than this get catalog stub only

INDEXED_EXTS = {
    # Documents — full text extracted
    ".pdf", ".ai", ".docx", ".pptx", ".xlsx", ".xlsm", ".rtf",
    ".txt", ".md", ".markdown", ".csv", ".log", ".json", ".srt", ".vtt",
    # Design — layer names + text extracted
    ".psd", ".psb",
    # Images — metadata cataloged
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg",
    # Audio — duration, BPM, key cataloged
    ".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus",
    # Video — duration cataloged
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    # Adobe project files — catalog stub (filename/path/size)
    ".prproj", ".aep", ".indd", ".indt",
}

SKIP_DIRS = {
    ".obsidian", ".git", ".cache", "node_modules", "__pycache__",
    "$recycle.bin", "system volume information", "appdata",
    "comfyui", "lancedb", "stems",
    ".smart-env",   # Obsidian Smart Connections' own cache; see rag.SKIP_DIRS
}

log = logging.getLogger("david.watcher")

# ── Shared state ──────────────────────────────────────────────────────────────

_pending_lock = threading.Lock()
_pending      = {}   # {fpath: (timestamp, domain, "add"|"delete")}
_embedder     = None
_table        = None


# ── RAG helpers ───────────────────────────────────────────────────────────────

def _init_rag():
    global _embedder, _table
    import rag
    _table    = rag.get_table()
    _embedder = rag.get_embedder()
    log.info("RAG system ready.")


def _should_skip(fpath: str) -> bool:
    import rag
    if rag.is_excluded_path(fpath):   # CRM chat transcripts, see rag.is_excluded_path
        return True
    if rag.library_rules.is_junk_file(fpath):   # Office ~$ lock files and zero-byte placeholders
        return True
    parts = fpath.lower().replace("\\", "/").split("/")
    for part in parts[:-1]:   # only check directory components, not the filename
        if part in SKIP_DIRS:
            return True
    ext = os.path.splitext(fpath)[1].lower()
    return ext not in INDEXED_EXTS


def _index_file(fpath: str, domain: str):
    """Delete old LanceDB chunks for fpath, extract, and re-insert."""
    import rag
    import extractors

    esc = fpath.replace("'", "''")
    try:
        _table.delete(f"source_path = '{esc}'")
    except Exception:
        pass

    try:
        size = os.path.getsize(fpath)
    except OSError:
        return

    catalog_only = size > MAX_FILE_MB * 1024 * 1024
    text, atype  = extractors.describe(fpath, catalog_only=catalog_only)
    if not text:
        return

    mtime = rag._file_mtime(fpath)
    rows  = []
    for i, chunk in enumerate(rag.chunk_text(text)):
        vec = _embedder.encode(embedding.doc_text(os.path.basename(fpath), chunk), normalize_embeddings=True).tolist()
        rows.append({
            "id":            f"{domain}::{fpath}::{i}",
            "vault":         domain,
            "file":          os.path.basename(fpath),
            "chunk_index":   i,
            "text":          chunk,
            "date_modified": mtime,
            "source_path":   fpath,
            "asset_type":    atype,
            "vector":        vec,
        })

    if rows:
        _table.add(rows)
        log.info(f"Indexed  {os.path.basename(fpath)} ({atype}, {len(rows)} chunk(s)) [{domain}]")


def _delete_file(fpath: str):
    esc = fpath.replace("'", "''")
    try:
        _table.delete(f"source_path = '{esc}'")
        log.info(f"Removed  {os.path.basename(fpath)}")
    except Exception as e:
        log.warning(f"Delete failed {fpath}: {e}")


# ── Watchdog handler ──────────────────────────────────────────────────────────

def _make_handler(domain: str):
    from watchdog.events import FileSystemEventHandler

    class _Handler(FileSystemEventHandler):
        def _queue(self, fpath, action):
            if _should_skip(fpath):
                return
            with _pending_lock:
                _pending[fpath] = (time.time(), domain, action)

        def on_created(self, event):
            if not event.is_directory:
                self._queue(event.src_path, "add")

        def on_modified(self, event):
            if not event.is_directory:
                self._queue(event.src_path, "add")

        def on_deleted(self, event):
            if not event.is_directory:
                self._queue(event.src_path, "delete")

        def on_moved(self, event):
            if not event.is_directory:
                self._queue(event.src_path,  "delete")
                self._queue(event.dest_path, "add")

    return _Handler()


# ── Debounce processor ────────────────────────────────────────────────────────

def _process_loop():
    """Drain the pending queue every 5s, processing files quiet for DEBOUNCE_SEC."""
    while True:
        time.sleep(5)
        now       = time.time()
        to_do     = {}
        with _pending_lock:
            for fpath, (ts, domain, action) in list(_pending.items()):
                if now - ts >= DEBOUNCE_SEC:
                    to_do[fpath] = (domain, action)
                    del _pending[fpath]

        for fpath, (domain, action) in to_do.items():
            try:
                if action == "add" and os.path.isfile(fpath):
                    _index_file(fpath, domain)
                elif action == "delete":
                    _delete_file(fpath)
            except Exception as e:
                log.error(f"Error processing {fpath}: {e}")


# ── Entry points ──────────────────────────────────────────────────────────────

def start():
    from watchdog.observers import Observer

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [Watcher] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    log.info("Loading RAG system (embedder from embedding.py)...")
    _init_rag()

    with open(WATCH_CONFIG, "r", encoding="utf-8") as f:
        watches = json.load(f)["watches"]

    observer = Observer()
    active   = 0
    for w in watches:
        path   = w["path"]
        domain = w["domain"]
        if not os.path.isdir(path):
            log.warning(f"Skipped (not found): {path}")
            continue
        observer.schedule(_make_handler(domain), path, recursive=True)
        log.info(f"Watching: {path}  →  domain '{domain}'")
        active += 1

    if not active:
        log.error("No valid watch paths found. Check watch_config.json.")
        return

    threading.Thread(target=_process_loop, daemon=True, name="debounce").start()

    observer.start()
    log.info(f"Active. Monitoring {active} folder(s). Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Stopping...")
        observer.stop()
    observer.join()
    log.info("Stopped.")


if __name__ == "__main__":
    if "--status" in sys.argv:
        import rag
        t = rag.get_table()
        print(f"\n  Index total: {t.count_rows()} chunks\n")
        with open(WATCH_CONFIG) as f:
            watches = json.load(f)["watches"]
        print("  Configured watches:")
        for w in watches:
            exists = "OK " if os.path.isdir(w["path"]) else "MISSING"
            print(f"    [{exists}] {w['domain']:10s}  {w['path']}")
        print()
        try:
            with open(LOG_PATH) as lf:
                lines = lf.readlines()
                print(f"  Last 5 log entries:")
                for line in lines[-5:]:
                    print("   ", line.rstrip())
        except FileNotFoundError:
            print("  (no log yet — watcher hasn't run)")
    else:
        start()
