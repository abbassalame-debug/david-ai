"""
test_ingest.py — offline tests for turbo_ingest RAG-integrity fixes (F7).

No CUDA / torch / lancedb: turbo_ingest imports those lazily inside run(), so
importing the module and testing the pure cache/bootstrap logic is safe. The
LanceDB table is replaced with a tiny fake that just returns a pydict.

Run: python test_ingest.py
"""

from david_config import DAVID_HOME
import sys, os, sqlite3, tempfile, datetime
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import turbo_ingest as ti


# ── fake LanceDB table returning a controlled column dict ────────────────────
class _FakeArrow:
    def __init__(self, d): self._d = d
    def to_pydict(self): return self._d
class _FakeLance:
    def __init__(self, d): self._d = d
    def to_table(self, columns=None): return _FakeArrow(self._d)
class _FakeTable:
    def __init__(self, d): self._d = d
    def to_lance(self): return _FakeLance(self._d)


print("\n── F7: _bootstrap_cache mtime/date logic ──")

_tmp = tempfile.mkdtemp(prefix="david_test_ingest_")
try:
    file_fresh = os.path.join(_tmp, "fresh.md")
    file_stale = os.path.join(_tmp, "stale.md")
    for p in (file_fresh, file_stale):
        with open(p, "w", encoding="utf-8") as f:
            f.write("body")
    # Both files were just written, so their real mtime is today.
    today = datetime.date.today().strftime("%Y-%m-%d")
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    gone = os.path.join(_tmp, "deleted.md")   # referenced in index, no longer on disk

    columns = {
        # fresh.md indexed today, matches current mtime -> should be seeded
        # stale.md indexed yesterday but file changed today -> must NOT be seeded
        # deleted.md missing on disk -> skipped without error
        "source_path":  [file_fresh, file_fresh, file_stale, gone],
        "date_modified": [today, today, yesterday, today],
    }

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE indexed (path TEXT PRIMARY KEY, mtime REAL, chunks INTEGER)")
    ti._bootstrap_cache(conn, _FakeTable(columns))
    seeded = {r[0] for r in conn.execute("SELECT path FROM indexed").fetchall()}

    check("unchanged file (index date == current) is seeded as cached",
          file_fresh in seeded)
    check("changed-since-ingest file is left OUT of cache (will re-index)",
          file_stale not in seeded)
    check("missing file does not crash the bootstrap and is not seeded",
          gone not in seeded)

    # chunk count for the seeded file reflects both of its index rows
    row = conn.execute("SELECT chunks FROM indexed WHERE path=?", (file_fresh,)).fetchone()
    check("seeded chunk count aggregates the file's index rows", row and row[0] == 2)

    # A second bootstrap is a no-op when the cache already has rows
    before = conn.execute("SELECT COUNT(*) FROM indexed").fetchone()[0]
    ti._bootstrap_cache(conn, _FakeTable(columns))
    after = conn.execute("SELECT COUNT(*) FROM indexed").fetchone()[0]
    check("bootstrap is a no-op when cache is already populated", before == after)
finally:
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)


print("\n── F7: detect_vault reclassification (unchanged behavior) ──")
check("audio always routes to music",
      ti.detect_vault(r"C:\x\beat.wav", "business") == "music")
check("code routes to system",
      ti.detect_vault(r"C:\x\script.py", "business") == "system")
check("plain doc keeps its assigned vault",
      ti.detect_vault(r"C:\x\notes.md", "business") == "business")


print("\n── Summary ──")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
