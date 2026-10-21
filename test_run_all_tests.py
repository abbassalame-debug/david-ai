"""test_run_all_tests.py — scripts/run_all_tests.py and ci_env.py (2026-10-21, workstream 1: CI).

The real runner is run on a throwaway folder of fake suites (pass, fail, no checks at all, everything skipped) and must:
fail the run; report each status; count skips apart from passes; fail a suite that reports no checks; write the per-suite
table to GITHUB_STEP_SUMMARY and every suite's output to the log file. ci_env.needs() must only skip under DAVID_CI=1.
"""
from david_config import DAVID_HOME
import os, shutil, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
RUNNER = os.path.join(str(DAVID_HOME), "scripts", "run_all_tests.py")

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

tmp = tempfile.mkdtemp(prefix="runner_test_")
try:
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    suites = {
        "test_a_pass.py": 'print("  PASS  one"); print("  PASS  two"); print("\\n3/3 checks passed")',
        "test_b_fail.py": 'print("  FAIL  broken thing"); print("1/2 checks passed"); raise SystemExit(1)',
        "test_c_empty.py": 'print("hello, but no checks")',
        "test_d_skipped.py": 'print("  SKIP (needs ollama)  the router answers a real question")\nprint("  SKIP (needs gpu)  a render")',
    }
    for name, body in suites.items():
        open(os.path.join(home, name), "w", encoding="utf-8").write(body + "\n")
    summary = os.path.join(tmp, "summary.md")
    logf = os.path.join(tmp, "run.log")
    env = {k: v for k, v in os.environ.items() if not k.startswith("DAVID_")}
    env.update(DAVID_HOME=home, DAVID_DATA=os.path.join(tmp, "data"), DAVID_VAULTS=os.path.join(tmp, "vaults"),
               DAVID_TEST_TMP=os.path.join(tmp, "runs"), DAVID_TEST_LOG=logf, GITHUB_STEP_SUMMARY=summary)
    p = subprocess.run([sys.executable, RUNNER], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
    out = p.stdout + p.stderr

    print("\n── the runner on fake suites ──")
    check("a failing suite fails the run (exit 1)", p.returncode == 1, out[-300:])
    check("a passing suite is PASS with its 3 checks", "PASS  test_a_pass.py" in out and "    3 checks" in out)
    check("a failing suite is FAIL", "FAIL  test_b_fail.py" in out)
    check("a suite that exits 0 but reports no checks FAILS", "FAIL  test_c_empty.py" in out)
    check("a suite whose checks were all skipped is SKIP, not FAIL", "SKIP  test_d_skipped.py" in out)
    check("skips are counted apart from passes", "3 checks counted, 2 skipped" in out, [l for l in out.splitlines() if "suites," in l])
    check("the summary names exactly the failed suites", "failed: ['test_b_fail.py', 'test_c_empty.py']" in out)
    s = open(summary, encoding="utf-8").read() if os.path.exists(summary) else ""
    check("GITHUB_STEP_SUMMARY gets a per-suite table", "| Suite | Result | Checks | Skipped | Seconds |" in s and s.count("| `test_") == 4, s[:200])
    check("…with the failure lines of each failed suite", "broken thing" in s and "reported no checks" in s)
    lg = open(logf, encoding="utf-8").read() if os.path.exists(logf) else ""
    check("the log holds every suite's full output", all(n in lg for n in suites) and "hello, but no checks" in lg and "SKIP (needs gpu)" in lg)
    check("the scratch run folder is removed afterwards", os.path.isdir(os.path.join(tmp, "runs")) and not any(
        d.startswith("run_") for d in os.listdir(os.path.join(tmp, "runs"))))

    print("\n── ci_env ──")
    import importlib, ci_env
    os.environ.pop("DAVID_CI", None)
    check("outside CI nothing is ever skipped", ci_env.needs("ollama", "gpu", "com", "adobe:photoshop") is None)
    os.environ["DAVID_CI"] = "1"
    os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9"
    import david_config
    importlib.reload(david_config)
    check("in CI an unreachable Ollama is reported as the missing requirement", ci_env.needs("ollama") == "ollama")
    check("an unknown Adobe app is reported missing", ci_env.needs("adobe:nosuchapp") == "adobe:nosuchapp")
    check("an unknown requirement is reported missing (fail closed)", ci_env.needs("teleporter") == "teleporter")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ci_env.skip("a real-model check", "ollama")
    check("ci_env.skip prints the line format the runner counts", buf.getvalue().strip() == "SKIP (needs ollama)  a real-model check")
finally:
    os.environ.pop("DAVID_CI", None)
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
