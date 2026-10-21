"""
test_design_naming.py -- tests for design_naming.py: industry-standard project, folder and file names.
Pure functions, offline.   Run: python test_design_naming.py
"""
from david_config import DAVID_HOME
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import design_naming as N


def bad(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except N.BadName as e:
        return str(e)


check("a project id is date_Client_Project with CamelCase English labels", N.project_id("acme corp", "spring menu 2", "2026-10-10") == "2026-10-10_AcmeCorp_SpringMenu2")
check("a made-up date or an empty/Arabic-only label is refused with a sentence", bad(N.project_id, "x", "y", "2026-13-40") is not None and bad(N.project_id, "", "y", "2026-10-10") is not None and "English" in (bad(N.project_id, "شاورما", "y", "2026-10-10") or ""))
check("a file name is Client_Project_Asset_Spec_vNN.ext, lower-case extension, two-digit version", N.file_name("Acme", "Spring", "poster", "A3", 1, ".PSD") == "Acme_Spring_Poster_A3_v01.psd" and N.file_name("Acme", "Spring", "logo", None, 12, "ai") == "Acme_Spring_Logo_v12.ai")
check("a version of 0, 100, 1.5 or True is refused; a bad extension is refused", all(bad(N.file_name, "A", "B", "C", None, v, "ai") for v in (0, 100, 1.5, True)) and bad(N.file_name, "A", "B", "C", None, 1, "a b") is not None)
check("a standard name passes both checks", N.check_name("Acme_Spring_Poster_A3_v01.psd") == [] and N.check_file_name("Acme_Spring_Poster_A3_v01.psd") == [])
check("a space, Arabic letters or a trailing dot are each reported", any("space" in s for s in N.check_name("my file.psd")) and any("non-English" in s for s in N.check_name("ملف_v01.psd")) and any("dot" in s for s in N.check_name("name.")))
check("'final', 'copy', 'new' and 'untitled' are reported and point to a version number", all(any("version" in s for s in N.check_name(n)) for n in ("poster_FINAL.psd", "poster_copy.psd", "new_poster.psd", "Untitled-1".replace("-", "_") + ".psd")))
check("a reserved Windows name and a name longer than 100 characters are reported", any("reserved" in s for s in N.check_name("CON.txt")) and any("longer" in s for s in N.check_name("a" * 101)))
check("an empty name or one with a leading space is reported", N.check_name("") != [] and N.check_name(" a") != [])
check("a file name without a version suffix or without an extension is reported", any("version" in s for s in N.check_file_name("Acme_Poster.psd")) and any("extension" in s for s in N.check_file_name("Acme_Poster_v01")))
check("the project folder skeleton is the numbered studio standard", N.FOLDERS == ["01_Brief", "02_Assets", "03_Working", "04_Previews", "05_Exports", "06_Delivery", "07_Archive"] and all(N.check_name(f) == [] for f in N.FOLDERS))

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
