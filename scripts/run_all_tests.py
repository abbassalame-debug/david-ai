"""run_all_tests.py -- runs every test_*.py in <DAVID_HOME> one after the other (own process, 15-minute limit each) and writes a summary.
  python scripts\run_all_tests.py            -> prints one line per suite and a total; exit 1 if any suite fails

Test isolation (2026-10-18): every suite runs with DAVID_DATA / DAVID_VAULTS pointed at a fresh folder under
<DAVID_HOME>/data/_test_runs/ and without secrets or DAVID_AUDIT_CONTENT, so no suite can write to your real
audit log, tasks.db or index. The real audit log is fingerprinted before and after the run; if it changed, the run
fails (a HUD that is running against the same data folder at the same time would also change it).
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA, DAVID_HOME
import glob, hashlib, os, re, shutil, subprocess, sys, tempfile, time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = str(DAVID_HOME)
SCRUB = ("DAVID_APP_DB_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DAVID_AUDIT_CONTENT")
LIVE_LOG = os.path.join(str(DAVID_DATA), "audit_log.jsonl")


def _fingerprint(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return os.path.getsize(path), h.hexdigest()


# The scratch root lives inside the git-ignored data/ folder (not under %TEMP%: file_watcher skips any path with an
# 'appdata' folder in it, which would make two suites fail for reasons unrelated to the code under test).
RUNS_ROOT = os.path.abspath(os.environ.get("DAVID_TEST_TMP") or os.path.join(ROOT, "data", "_test_runs"))
os.makedirs(RUNS_ROOT, exist_ok=True)
run_dir = tempfile.mkdtemp(prefix="run_", dir=RUNS_ROOT)
child_env = {k: v for k, v in os.environ.items() if k not in SCRUB}
child_env["DAVID_DATA"] = os.path.join(run_dir, "data")
child_env["DAVID_VAULTS"] = os.path.join(run_dir, "vaults")
# The suites run the HUD app in-process with no password in their scratch data folder, so they run with the login off
# (2026-10-21, HUD login). test_hud_auth.py removes this and tests the login on.
child_env["DAVID_AUTH"] = "off"
os.makedirs(child_env["DAVID_DATA"])
os.makedirs(child_env["DAVID_VAULTS"])
# Every suite gets a temp folder inside the run folder, as a fully resolved long path (2026-10-21, found by CI): on the hosted
# Windows runner %TEMP% is an 8.3 short path (a "RUNNER~1"-style folder name), so tempfile paths and their resolved forms differed
# and every "is this file inside that folder" comparison failed.
child_env["TEMP"] = child_env["TMP"] = child_env["TMPDIR"] = os.path.realpath(os.path.join(run_dir, "tmp"))
os.makedirs(child_env["TEMP"])
live_before = _fingerprint(LIVE_LOG)

# CI (2026-10-21): with DAVID_CI=1 a check that needs Ollama, a GPU, COM or an Adobe app prints "SKIP (needs X)" (ci_env.py).
# Skips are counted apart from passes. A suite that exits 0 but reports no check at all FAILS (it tested nothing), unless
# everything in it was skipped. The full output of every suite goes to a log file (uploaded as a CI artifact) and, when
# GITHUB_STEP_SUMMARY is set, a per-suite table goes to the job summary.
SKIP_LINE = re.compile(r"^\s*(?:\x1b\[[0-9;]*m)?\s*SKIP\b", re.M)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG_PATH = os.environ.get("DAVID_TEST_LOG") or os.path.join(RUNS_ROOT, "last_run.log")
log = open(LOG_PATH, "w", encoding="utf-8")

# DAVID_SUITES="test_a.py,test_b.py" runs only those suites (the non-blocking Ubuntu CI job runs the core set this way).
ONLY = {s.strip() for s in os.environ.get("DAVID_SUITES", "").split(",") if s.strip()}

rows, total, skipped_checks, failed, failed_detail = [], 0, 0, [], []
for f in sorted(glob.glob(os.path.join(ROOT, "test_*.py"))):
    name = os.path.basename(f)
    if ONLY and name not in ONLY:
        continue
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, f], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900, env=child_env)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        code = p.returncode
    except subprocess.TimeoutExpired:
        out, code = "", -9
    nums = re.findall(r"(\d+)\s*(?:/\s*\d+\s*)?(?:checks?|tests?|passed)\b", out, re.I)
    n = max((int(x) for x in nums), default=0)
    skips = len(SKIP_LINE.findall(out))
    if code != 0:
        status = "FAIL"
    elif n == 0 and skips == 0:
        status, out = "FAIL", out + "\n[run_all_tests] the suite exited 0 but reported no checks: it tested nothing"
    elif n == 0:
        status = "SKIP"
    else:
        status = "PASS"
    secs = time.time() - t0
    total += n if status == "PASS" else 0
    skipped_checks += skips
    last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
    if status == "FAIL":
        failed.append(name)
        bad = [l.strip()[:300] for l in out.splitlines() if re.search(r"FAIL|Traceback|Error|reported no checks", l)][:6]
        failed_detail.append((name, bad))
    rows.append((name, status, n, skips, secs))
    plain = ANSI.sub("", out)
    log.write(f"\n===== {status}  {name}  checks={n} skips={skips} exit={code} {secs:.0f}s =====\n{plain}\n")
    print(f"{status}  {name:<40} {n:>5} checks {skips:>3} skipped  {secs:5.0f}s  {last[0][:60]}", flush=True)

live_after = _fingerprint(LIVE_LOG)
live_ok = live_before == live_after
summary = (f"{len(rows)} suites, {total} checks counted, {skipped_checks} skipped, failed: {failed or 'none'}")
print(f"\n{summary}")
print(f"real audit log untouched by the run: {'yes' if live_ok else 'NO -- it changed during the run'} ({LIVE_LOG})")
print(f"full output of every suite: {LOG_PATH}")
log.write(f"\n{summary}\nreal audit log untouched: {live_ok}\n")
log.close()
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as s:
        s.write(f"### Test suites\n\n**{summary}**\n\n| Suite | Result | Checks | Skipped | Seconds |\n|---|---|---:|---:|---:|\n")
        for name, status, n, skips, secs in rows:
            s.write(f"| `{name}` | {'✅' if status == 'PASS' else '⏭️' if status == 'SKIP' else '❌'} {status} | {n} | {skips} | {secs:.0f} |\n")
        for name, bad in failed_detail:
            s.write(f"\n**{name}**\n```\n" + "\n".join(bad) + "\n```\n")
# remove only the folder this run created, and only if it really is inside the scratch root
if os.path.commonpath([os.path.abspath(run_dir), RUNS_ROOT]) == RUNS_ROOT and os.path.basename(run_dir).startswith("run_"):
    shutil.rmtree(run_dir, ignore_errors=True)
sys.exit(1 if failed or not live_ok else 0)
