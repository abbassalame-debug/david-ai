# rag_watcher.py -- watches Obsidian vaults, re-embeds changed .md files nightly
# Run in background: pythonw rag_watcher.py
# Changed files are queued and flushed once per hour (CPU embeddings, non-blocking)

from david_config import DAVID_DATA, DAVID_HOME
import sys
import os
import time
import datetime
import threading

# Task Scheduler launches this via pythonw.exe (no console attached), which
# leaves sys.stdout/stderr as None — reconfigure() and every print() below
# would crash immediately on startup. Redirect to a log file in that case;
# keep using the real console (with proper encoding) when one is attached.
if sys.stdout is None:
    _log_dir = str(DAVID_DATA / "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = open(os.path.join(_log_dir, "rag_watcher.log"), "a", encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file
else:
    sys.stdout.reconfigure(encoding="utf-8")

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

QUEUE_FILE = str(DAVID_DATA / "rag_reembed_queue.json")
FLUSH_INTERVAL = 3600  # re-embed queued files every hour

sys.path.insert(0, str(DAVID_HOME))
import rag as rag_module

_queue = set()
_lock  = threading.Lock()


class VaultHandler(FileSystemEventHandler):
    def __init__(self, vault_name):
        self.vault_name = vault_name

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            with _lock:
                _queue.add((self.vault_name, event.src_path))

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            with _lock:
                _queue.add((self.vault_name, event.src_path))


def flush_queue():
    while True:
        time.sleep(FLUSH_INTERVAL)
        with _lock:
            items = list(_queue)
            _queue.clear()

        if not items:
            continue

        print(f"\n  [{datetime.datetime.now().strftime('%H:%M')}] Re-embedding {len(items)} changed files...")
        table = rag_module.get_table()
        for vault_name, filepath in items:
            rel = os.path.relpath(filepath, rag_module.VAULTS[vault_name])
            # Remove existing chunks for this file
            try:
                table.delete(f"vault = '{vault_name}' AND file = '{rel}'")
            except Exception:
                pass
            n = rag_module.ingest_file(vault_name, filepath)
            print(f"    {vault_name}/{rel}: {n} chunks", flush=True)
        print("  Re-embed complete.", flush=True)


def main():
    print("RAG watcher started. Monitoring vaults for changes.", flush=True)
    observer = Observer()
    for vault_name, path in rag_module.VAULTS.items():
        if os.path.exists(path):
            handler = VaultHandler(vault_name)
            observer.schedule(handler, path, recursive=True)
            print(f"  Watching: {path}", flush=True)

    observer.start()
    flush_thread = threading.Thread(target=flush_queue, daemon=True)
    flush_thread.start()

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("RAG watcher stopped.")


if __name__ == "__main__":
    main()
