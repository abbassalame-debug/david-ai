"""
test_smart_env_skip.py -- the Obsidian Smart Connections cache folder (.smart-env) is never indexed (2026-10-17).

Its .ajson files are the plugin's own rewritten-all-the-time cache. In the index they were 28 content-free stubs ("File: x.ajson | 14KB | modified ...") that ranked above the real
notes ("Task Ledger" returned Task_Ledger_md.ajson before Task Ledger.md). rag.import_folder and file_watcher now skip the folder like .obsidian. No index is touched.
Run: python test_smart_env_skip.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import rag
import turbo_ingest
import file_watcher
import library_rules

print("-- the skip lists --")
for label, mod in (("rag.py", rag), ("turbo_ingest.py", turbo_ingest), ("file_watcher.py", file_watcher)):
    check(f"{label} skips .smart-env next to .obsidian", ".smart-env" in mod.SKIP_DIRS and ".obsidian" in mod.SKIP_DIRS)

print("\n-- the folder walk in rag.import_folder uses that list --")
src = open(str(DAVID_HOME / "rag.py"), encoding="utf-8").read()
check("import_folder drops directories named in SKIP_DIRS (lower-cased) while walking", "d.lower() not in SKIP_DIRS" in src)

print("\n-- the file watcher --")
# not the system temp folder: its path contains "AppData", which the watcher skips on purpose; evals\ is git-ignored scratch space
os.makedirs(str(DAVID_DATA / "evals"), exist_ok=True)
tmp = tempfile.mkdtemp(prefix="smartenv_", dir=str(DAVID_DATA / "evals"))
try:
    inside = os.path.join(tmp, "System", ".smart-env", "multi")
    normal = os.path.join(tmp, "System", "Notes")
    os.makedirs(inside); os.makedirs(normal)
    f_in = os.path.join(inside, "Task_Ledger.md"); f_ok = os.path.join(normal, "Task Ledger.md")
    for f in (f_in, f_ok):
        open(f, "w", encoding="utf-8").write("# Task ledger\n- a real note with enough content to be indexed\n")
    check("a file inside .smart-env is skipped by the watcher", file_watcher._should_skip(f_in) is True)
    check("the same kind of file in an ordinary folder is NOT skipped", file_watcher._should_skip(f_ok) is False)

    print("\n-- the batch scan (turbo_ingest) --")
    files, _ = library_rules.scan_files({"system": os.path.join(tmp, "System")}, {".md"}, turbo_ingest.SKIP_DIRS, rag.is_excluded_path, ())
    got = {os.path.basename(p) for _, _, p, _ in files}
    check("the scan returns the ordinary note and not the .smart-env one", got == {"Task Ledger.md"}, str(sorted(got)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
