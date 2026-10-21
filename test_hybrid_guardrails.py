"""
test_hybrid_guardrails.py — offline checks for Phase 6 (hybrid resilience).

Covers the three Phase-4-smoke-test findings, no GPU / Ollama / cloud needed:
  T2 fix  adaptive ComfyUI startup (backoff wait, progress callbacks,
          tri-state /api/comfy/status instead of a 30 s false-offline)
  T6 fix  code_guard static validation — catches the EXACT hallucinations the
          14B produced in testing (asyncio loop= param, fabricated signal.*
          functions, syntax errors) without executing model code
  Hybrid  local→cloud failover eligibility obeys the tier-escalation rules
          (firewalled agents and DLP-flagged payloads never leave the machine)

Run: python test_hybrid_guardrails.py
"""

from david_config import DAVID_HOME
import sys, os, asyncio, inspect, types
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"

results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))


# ── code_guard: the exact Test-6 hallucinations ──────────────────────────────
print("\n── code_guard: syntax + hallucinated-API detection ──")
import code_guard as cg

# 1. The deprecated/removed asyncio loop= parameter (verbatim failure class)
bad_loop = """```python
import asyncio

async def main():
    loop = asyncio.get_event_loop()
    result = await asyncio.wait_for(worker(), timeout=5, loop=loop)
```"""
issues = cg.review(bad_loop)
check("asyncio.wait_for(loop=...) flagged", any("loop=" in i for i in issues))
check("asyncio.get_event_loop() flagged", any("get_running_loop" in i for i in issues))

# 2. Fabricated stdlib function (verbatim from the test report)
bad_signal = """```python
import signal

def run():
    signal.start_loop_in_executor(handler)
```"""
issues = cg.review(bad_signal)
check("signal.start_loop_in_executor flagged as hallucinated",
      any("start_loop_in_executor" in i and "hallucinated" in i for i in issues))

# 3. Real stdlib attributes must NOT be flagged
good = """```python
import asyncio
import signal
import os

async def main():
    loop = asyncio.get_running_loop()
    await asyncio.sleep(0)
    signal.signal(signal.SIGTERM, lambda *a: None)
    print(os.getcwd())
```"""
check("correct stdlib code passes clean", cg.review(good) == [])

# 4. Syntax errors
broken = """```python
def f(:
    return 1
```"""
issues = cg.review(broken)
check("syntax error reported with line number", any("syntax error" in i for i in issues))

# 5. Plain prose and non-python blocks are ignored
prose = "Here is how it works.\n```bash\nls -la\n```\nNo python here."
check("non-python content produces no issues", cg.review(prose) == [])

# 6. Multiple blocks get block-numbered prefixes
two_blocks = bad_signal + "\n\n" + good
issues = cg.review(two_blocks)
check("multi-block responses prefix the failing block",
      any(i.startswith("code block 1:") for i in issues))

# 7. Never imports non-whitelisted modules (no code execution risk)
sneaky = """```python
import totally_fake_evil_module

totally_fake_evil_module.nonexistent_attr()
```"""
check("non-whitelisted imports are not imported (and not flagged)",
      cg.review(sneaky) == [])
check("whitelist is stdlib-only",
      all("." in m or not m.startswith(("requests", "numpy", "torch"))
          for m in cg.STDLIB_WHITELIST))

# 8. collections ABC move (common 14B training-data fossil)
old_abc = """```python
import collections

def f(x):
    return isinstance(x, collections.Mapping)
```"""
issues = cg.review(old_abc)
check("collections.Mapping (moved in 3.10) flagged", len(issues) > 0)


# ── Server-side checks ───────────────────────────────────────────────────────
try:
    from starlette.testclient import TestClient
    import server as srv
    import david_router as dr
    _SERVER_OK = True
except Exception as e:
    _SERVER_OK = False
    print(f"\n\033[93m  SKIP\033[0m  server checks skipped — cannot import app: {type(e).__name__}: {e}")

if _SERVER_OK:
    # The eligibility check below feeds a real DLP-matching string through
    # dlp_flag(), which calls dr.audit() and writes to whatever dr.LOG points
    # at — the real production audit_log.jsonl unless redirected here.
    import tempfile as _tempfile
    _ORIG_LOG = dr.LOG
    _iso_fd, _ISO_LOG = _tempfile.mkstemp(suffix=".jsonl")
    os.close(_iso_fd)
    dr.LOG = _ISO_LOG
    print("\n── Hybrid failover eligibility (tier rules preserved) ──")
    check("firewalled agent never fails over to cloud",
          srv._cloud_failover_eligible("beat_architect", "how do I sidechain?") is False)
    check("non-escalation agent never fails over to cloud",
          srv._cloud_failover_eligible("lore_keeper", "tell me a story") is False)
    check("tier-2 agent with a clean payload is eligible",
          srv._cloud_failover_eligible("cfo", "summarize this quarterly cash flow") is True)
    check("tier-2 agent with a DLP-flagged payload is blocked",
          srv._cloud_failover_eligible("cfo", "my password: hunter2") is False)
    check("recoverable-error keywords cover OOM/CUDA/missing-model",
          all(k in srv._RECOVERABLE_ERR for k in ("oom", "cuda", "not found")))

    print("\n── Adaptive ComfyUI startup ──")
    import comfy_client

    async def _run_ensure(sequence, budget, progress=None):
        """Drive _ensure_comfy_running with a scripted is_comfy_running and
        instant sleeps; Popen is stubbed so no real ComfyUI launches."""
        calls = iter(sequence)
        srv._comfy_started_at = None
        with mock.patch.object(comfy_client, "is_comfy_running",
                               side_effect=lambda: next(calls, sequence[-1])), \
             mock.patch.object(srv.subprocess, "Popen") as popen, \
             mock.patch.object(srv.asyncio, "sleep", new=lambda s: _instant()):
            ok = await srv._ensure_comfy_running(progress=progress, budget=budget)
        return ok, popen

    async def _instant():
        return None

    ok, popen = asyncio.run(_run_ensure([False, False, False, True], budget=600))
    check("comes up on a later poll instead of failing at a fixed 30 s",
          ok is True)
    check("launch happens exactly once", popen.call_count == 1)
    check("in-flight marker cleared after success", srv._comfy_started_at is None)

    ok, _ = asyncio.run(_run_ensure([True], budget=600))
    check("already-running short-circuits without launching", ok is True)

    # Budget exhaustion: is_comfy_running always False; with instant sleeps the
    # wall clock still advances past a tiny budget.
    ok, _ = asyncio.run(_run_ensure([False], budget=0))
    check("returns False (not hang) once the budget is exhausted", ok is False)
    check("in-flight marker cleared after failure", srv._comfy_started_at is None)

    check("startup budget is configurable and defaults well past 30 s",
          srv.COMFY_STARTUP_BUDGET_S >= 120, f"got {srv.COMFY_STARTUP_BUDGET_S}")

    print("\n── /api/comfy/status tri-state ──")
    client = TestClient(srv.app, base_url="http://localhost:8000")
    with mock.patch.object(comfy_client, "is_comfy_running", return_value=False):
        srv._comfy_started_at = None
        body = client.get("/api/comfy/status").json()
        check("not running + not launching → state 'offline'",
              body.get("state") == "offline" and body.get("running") is False)
        srv._comfy_started_at = srv.time.monotonic() - 42
        body = client.get("/api/comfy/status").json()
        check("not running + launch in flight → state 'starting' with elapsed",
              body.get("state") == "starting" and 40 <= (body.get("elapsed_s") or 0) <= 60,
              str(body))
        srv._comfy_started_at = None
    with mock.patch.object(comfy_client, "is_comfy_running", return_value=True):
        body = client.get("/api/comfy/status").json()
        check("running → state 'online'", body.get("state") == "online")

    print("\n── Code guardrail wiring ──")
    check("code_guard imported by the server", srv._CODE_GUARD_OK is True)
    _ws_src = inspect.getsource(srv.ws_chat)
    check("ws_chat runs the guard for technical/code agents",
          "_guard_code_response" in _ws_src)
    check("ws_chat attempts failover on recoverable local errors",
          "_local_failover" in _ws_src)
    _fo_src = inspect.getsource(srv._local_failover)
    check("failover reuses the existing gated escalation path (no new providers)",
          "_gemini_chunks" in _fo_src and "openrouter" not in _fo_src.lower()
          and "deepseek.com" not in _fo_src.lower())

    print("\n── Prompt hardening (Test 6 hallucination classes) ──")
    check("developer prompt forbids inventing APIs",
          "Never invent APIs" in dr.AGENT_PROMPTS["developer"])
    check("developer prompt pins the asyncio 3.10+ rules",
          "loop=" in dr.AGENT_PROMPTS["developer"])
    check("linguist prompt forbids fabricated terms/citations",
          "Never fabricate" in dr.AGENT_PROMPTS["linguist"])

    print("\n── Empty-response guard (live-observed deepseek-r1 bug) ──")
    # Found via live monitoring 2026-07-12: deepseek-r1:14b answered "are you
    # still online" with response="" after 32.5s (audit log), no error raised —
    # the reasoning model spent its whole turn "thinking" with zero visible
    # content tokens. The client marked that blank bubble "complete" with no
    # explanation. This guards against shipping empty output silently.
    check("empty response + no prior error -> substitute notice",
          srv._guard_empty_response("", False) == srv._EMPTY_RESPONSE_NOTICE)
    check("whitespace-only response is treated as empty",
          srv._guard_empty_response("   \n\n  ", False) == srv._EMPTY_RESPONSE_NOTICE)
    check("non-empty response -> no substitution",
          srv._guard_empty_response("Hello!", False) is None)
    check("empty response but an error was already sent -> no double-message",
          srv._guard_empty_response("", True) is None)

    dr.LOG = _ORIG_LOG
    try:
        os.remove(_ISO_LOG)
    except OSError:
        pass


print("\n── Summary ──")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
