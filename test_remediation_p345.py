"""
test_remediation_p345.py — offline regression locks for Phases 3-5 (F20).

Covers, without any network / Ollama / cloud dependency:
  F14  Gemini 2.5 Flash pricing constants + budget math on a temp budget file
  F15  Tier 2/3 escalation plumbing accepts system/history payloads
  F16  memory.save_episode no longer rebuilds FTS per episode
  F12  Ableton + Layer-0 regex tables are anchored (no mid-sentence side effects)
  F5   Arabic DLP requires word context; dlp_flag audits the matched pattern
  F18  linguist out of R1_AGENTS; agent prompts state their boundaries
  F13  GPU Layer-0 routes constant; ws_chat blocks GPU work under studio lock
  F17  shared audit writer emits mandatory fields; HUD lines tagged surface=hud
  F9   pick_model: Mode B → reasoning model for every domain
  F11  agent_loop planning calls carry num_ctx=16384; speak targets speak_queue\\
  F10  _ollama_stream raises on an in-stream {"error": ...} chunk
  F19  starts_with prefix delete in rag.import_folder; _generate_image delegates
       to comfy_client; stuck-'running' task sweep resets to failed

Run: python test_remediation_p345.py
"""

from david_config import DAVID_HOME
import sys, os, re, json, asyncio, tempfile, sqlite3, inspect
from pathlib import Path
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

import david_router as dr

# Isolate every module-level constant this suite touches that is otherwise
# shared with the REAL live HUD/CLI process on this machine: audit_log.jsonl
# and studio_mode.lock. Patched here, before any check and before the later
# `import server as srv` (whose `from david_router import STUDIO_LOCK, ...`
# binds these by value at import time), so a test run never writes fake
# entries into production telemetry or transiently blocks live GPU inference
# via a stray lock file. Restored at the very end of the run.
_ORIG_LOG = dr.LOG
_ORIG_STUDIO_LOCK = dr.STUDIO_LOCK
_iso_log_fd, _ISO_LOG = tempfile.mkstemp(suffix=".jsonl")
os.close(_iso_log_fd)
_ISO_LOCK_DIR = tempfile.mkdtemp()
dr.LOG = _ISO_LOG
dr.STUDIO_LOCK = os.path.join(_ISO_LOCK_DIR, "studio_mode.lock")


# ── F14: pricing constants + budget math ─────────────────────────────────────
print("\n── F14: Gemini 2.5 Flash pricing + budget math ──")
check("Gemini input price is $0.30/1M", abs(dr._GEM_IN_K - 0.00030) < 1e-12)
check("Gemini output price is $2.50/1M", abs(dr._GEM_OUT_K - 0.00250) < 1e-12)
check("Claude input price is $3.00/1M", abs(dr._CLU_IN_K - 0.00300) < 1e-12)
check("Claude output price is $15.00/1M", abs(dr._CLU_OUT_K - 0.01500) < 1e-12)

_orig_budget = dr.BUDGET_FILE
try:
    fd, tmp_budget = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp_budget)
    dr.BUDGET_FILE = tmp_budget
    dr._budget_record("gemini", 100_000, 20_000)
    with open(tmp_budget, encoding="utf-8") as f:
        data = json.load(f)
    check("100k in + 20k out records $0.08 on Gemini",
          abs(data.get("gemini_usd", 0) - 0.08) < 1e-9, f"got {data.get('gemini_usd')}")
    dr._budget_record("claude", 10_000, 2_000)
    with open(tmp_budget, encoding="utf-8") as f:
        data = json.load(f)
    check("10k in + 2k out records $0.06 on Claude",
          abs(data.get("claude_usd", 0) - 0.06) < 1e-9, f"got {data.get('claude_usd')}")
finally:
    dr.BUDGET_FILE = _orig_budget
    try: os.remove(tmp_budget)
    except OSError: pass


# ── F15: escalation payload plumbing ─────────────────────────────────────────
print("\n── F15: Tier 2/3 escalation payload plumbing ──")
for fn_name in ("_gemini_chunks", "_claude_chunks", "execute_gemini", "execute_claude"):
    params = inspect.signature(getattr(dr, fn_name)).parameters
    check(f"{fn_name} accepts system= and history=",
          "system" in params and "history" in params)

_src = inspect.getsource(dr.route_and_execute)
check("CLI escalation builds a RAG-enriched system prompt", "rag_context" in _src and "cloud_system" in _src)
check("CLI escalation DLP-scans the full outbound payload", "dlp_block_escalation" in _src)


# ── F16: no per-episode FTS rebuild ──────────────────────────────────────────
print("\n── F16: save_episode does not rebuild FTS ──")
import memory as _memory
# the explanatory comment mentions _ensure_fts() by name — match the actual
# call form (module-qualified) so the comment doesn't trip the check
_mem_src = inspect.getsource(_memory.save_episode)
check("save_episode no longer calls _ensure_fts",
      not re.search(r"^\s*\w+\._ensure_fts\(", _mem_src, re.M))


# ── Memory episodes are the user's own words, never LLM-invented "facts" ─────
print("\n── Memory: verbatim episodes (no LLM summarizer) ──")
_t = lambda when, txt: {"time": when, "agent": "analyst", "domain": "general", "text": txt}
_ep = _memory._build_episode([
    _t("11:16", "In the attached document, what starting cash balance must the manager verify?"),
    _t("11:17", "I prefer short answers unless I ask for a report."),
    _t("11:18", "In the attached document, what starting cash balance must the manager verify?"),
], "test")
check("no LLM summarizer left in memory.py", not hasattr(_memory, "_summarize"))
check("question stays a question (listed under 'asked', never turned into a fact)",
      "starting cash balance must the manager verify?" in _ep.split("User asked / requested:")[1]
      and "[FACT]" not in _ep)
check("stated preference kept verbatim",
      "I prefer short answers unless I ask for a report." in _ep.split("User asked / requested:")[0])
check("repeated turns collapsed", _ep.count("starting cash balance") == 1)
check("episode is labelled as unverified user words", "not verified facts" in _ep.splitlines()[0])
check("empty log produces no episode", _memory._build_episode([_t("1:00", "   ")], "t") == "")


# ── F12: anchored regex tables ───────────────────────────────────────────────
print("\n── F12: Ableton / Layer-0 regex anchoring ──")

def _ableton_action(text):
    t = text.lower()
    for pattern, action in dr.ABLETON_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return action
    return None

for text, want in [
    ("stop", "stop"), ("please stop", "stop"), ("play", "play"),
    ("set bpm to 128", "set_bpm"), ("new midi track", "create_midi_track"),
    ("how do I stop a track from clipping", None),
    ("the song at 120 bpm sounds better", None),
    ("how do I create a track in ableton", None),
]:
    got = _ableton_action(text)
    check(f"Ableton: {text!r} → {want}", got == want, f"got {got}")

def _layer0_route(text):
    for pattern, route in dr.LAYER0:
        if re.search(pattern, text.lower()):
            return route
    return None

for text, want in [
    ("separate stems from C:/x/track.wav", "audio_separate"),
    ("how does demucs separate vocals from a song", None),
    ("what is the bpm of C:/x/a.wav", "audio_analyze"),
    ("the key of C minor is related to E flat major", None),
]:
    got = _layer0_route(text)
    check(f"Layer0: {text!r} → {want}", got == want, f"got {got}")


# ── F5: Arabic DLP word-context + pattern logging ────────────────────────────
print("\n── F5: Arabic DLP recalibration ──")
for text, want in [
    ("خاص جدا لا تشارك", True),
    ("كيف أعمل API endpoint بـ FastAPI؟", False),
    ("هذا سريع جدا", False),           # fast (sari3) ≠ secret (sirri)
    ("النوم على سرير مريح", False),     # bed (sarir) ≠ secret
    ("هذا ملف سري", True),              # standalone "secret"
    ("عندي حساب في البنك", False),      # bare "account" no longer flags
    ("رقم الحساب 12345", True),
    ("كلمة المرور هي 1234", True),
]:
    got = dr.dlp_flag(text)
    check(f"dlp_flag({text!r}) == {want}", got is want, f"got {got}")

_captured = []
_orig_audit = dr.audit
try:
    dr.audit = lambda e: _captured.append(e)
    dr.dlp_flag("my password: hunter2")
finally:
    dr.audit = _orig_audit
check("dlp_flag audits which pattern matched",
      any(e.get("action") == "dlp_match" and e.get("pattern") for e in _captured))


# ── F18: agent roster + prompt boundaries ────────────────────────────────────
print("\n── F18: R1 roster and prompt differentiation ──")
check("linguist removed from R1_AGENTS", "linguist" not in dr.R1_AGENTS)
check("R1_AGENTS keeps cfo/exec/venture/legal",
      dr.R1_AGENTS == {"cfo", "executive_advisor", "venture_architect", "legal_counsel"})
check("creative_director prompt cedes market work to brand_audience",
      "brand_audience" in dr.AGENT_PROMPTS["creative_director"])
check("brand_audience prompt cedes craft work to creative_director",
      "creative_director" in dr.AGENT_PROMPTS["brand_audience"])
check("executive_advisor prompt cedes new ventures to venture_architect",
      "venture_architect" in dr.AGENT_PROMPTS["executive_advisor"])
check("venture_architect prompt cedes existing-business work to executive_advisor",
      "executive_advisor" in dr.AGENT_PROMPTS["venture_architect"])


# ── F13: studio lock over GPU routes ─────────────────────────────────────────
print("\n── F13: GPU Layer-0 routes constant ──")
check("GPU_LAYER0_ROUTES names the four GPU routes",
      dr.GPU_LAYER0_ROUTES == {"vision_image", "audio_separate", "audio_analyze", "generate_image"})
_route_src = inspect.getsource(dr.route_and_execute)
check("route_and_execute blocks GPU routes under studio lock",
      "GPU_LAYER0_ROUTES" in _route_src and "studio_lock_block" in _route_src)


# ── F17: shared audit writer mandatory fields ────────────────────────────────
print("\n── F17: standardized audit writer ──")
_orig_log = dr.LOG
try:
    fd, tmp_log = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    dr.LOG = tmp_log
    dr.audit({"action": "unit_test"})
    with open(tmp_log, encoding="utf-8") as f:
        entry = json.loads(f.readline())
    for field in ("ts", "surface", "request_id", "status", "duration_ms"):
        check(f"audit line carries mandatory field {field!r}", field in entry)
    check("default surface is 'cli'", entry.get("surface") == "cli")
    check("default status is 'ok'", entry.get("status") == "ok")
    dr.audit({"action": "unit_test_err", "error": "boom"})
    with open(tmp_log, encoding="utf-8") as f:
        entry2 = json.loads(f.readlines()[-1])
    check("entries carrying 'error' default status to 'error'", entry2.get("status") == "error")
finally:
    dr.LOG = _orig_log
    try: os.remove(tmp_log)
    except OSError: pass


# ── F11: agent loop num_ctx + speak queue ────────────────────────────────────
print("\n── F11: agent loop context window + speak wiring ──")
import agent_loop

_captured_body = {}
def _fake_urlopen(req, timeout=None):
    _captured_body["body"] = json.loads(req.data.decode())
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"message": {"content": "ok"}}'
    return _R()

with mock.patch("urllib.request.urlopen", _fake_urlopen):
    agent_loop._sync_call([{"role": "user", "content": "hi"}], agent_loop.PLANNING_MODEL)
check("planning calls set num_ctx=16384",
      _captured_body["body"].get("options", {}).get("num_ctx") == 16384)

import tool_executor
_orig_queue = tool_executor.SPEAK_QUEUE
try:
    tmp_queue = tempfile.mkdtemp()
    tool_executor.SPEAK_QUEUE = tmp_queue
    out = tool_executor._speak("hello world")
    files = list(Path(tmp_queue).glob("*.txt"))
    check("speak writes a timestamped file into speak_queue",
          len(files) == 1 and files[0].read_text(encoding="utf-8") == "hello world",
          out)
finally:
    tool_executor.SPEAK_QUEUE = _orig_queue

_speak_src = inspect.getsource(tool_executor._speak)
check("speak no longer targets voice_inbox.txt", "voice_inbox" not in _speak_src.lower())


# ── F19: source-level regression locks ───────────────────────────────────────
print("\n── F19: one-liner fixes ──")
_rag_text = Path(str(DAVID_HOME / "rag.py")).read_text(encoding="utf-8")
check("import_folder prefix delete uses starts_with (no LIKE wildcards)",
      "starts_with(source_path" in _rag_text and "LIKE '{esc}%'" not in _rag_text)

_gen_src = inspect.getsource(tool_executor._generate_image)
check("_generate_image delegates to comfy_client.generate",
      "from comfy_client import generate" in _gen_src and "KSampler" not in _gen_src)

_qk_src = inspect.getsource(tool_executor._query_knowledge)
import rag
# since 2026-10-18 (audit S-03) _query_knowledge delegates to rag.retrieve, which uses the cached singletons
check("_query_knowledge reuses rag's cached embedder/table (through rag.retrieve)",
      "_rag.retrieve(" in _qk_src and "SentenceTransformer(" not in _qk_src
      and "get_embedder()" in inspect.getsource(rag.retrieve) and "get_table()" in inspect.getsource(rag.retrieve))


# ── Server-side checks (skip cleanly if the app can't import) ────────────────
try:
    from starlette.testclient import TestClient
    import server as srv
    _SERVER_OK = True
except Exception as e:
    _SERVER_OK = False
    print(f"\n\033[93m  SKIP\033[0m  server-side checks skipped — cannot import app: {type(e).__name__}: {e}")

if _SERVER_OK:
    print("\n── F9: pick_model + HUD mode plumbing ──")
    check("Mode B routes technical to the reasoning model",
          srv.pick_model("B", "technical") == srv.REASONING_MODEL)
    check("Mode B routes general to the reasoning model",
          srv.pick_model("B", "general") == srv.REASONING_MODEL)
    check("Mode A keeps technical on qwen3.5 (uiux_engineer/prompt_architect, swapped 2026-09-30)",
          srv.pick_model("A", "technical") == "qwen3.5:9b")
    check("unknown domain falls back to qwen3.5 (router default, updated 2026-09-30)",
          srv.pick_model("A", "nonsense") == "qwen3.5:9b")
    _sm_src = inspect.getsource(srv.set_mode)
    check("HUD set_mode delegates VRAM enforcement to the router",
          "_router_set_mode" in _sm_src)
    _ws_src = Path(str(DAVID_HOME / "ui" / "server.py")).read_text(encoding="utf-8")
    check("per-request reasoning escalation no longer writes Mode B globally",
          'set_mode("B")' not in _ws_src)

    print("\n── F12: HUD Layer-0 regex anchoring ──")
    for name, text, want in [
        ("_L0_MODE", "mode b", True),
        ("_L0_MODE", "the failure mode a system exhibits", False),
        ("_L0_MODE", "let's switch to a different approach", False),
        ("_L0_CLEAR", "clear chat", True),
        ("_L0_CLEAR", "should I clear chat history in the app?", False),
        ("_L0_OPEN_APP", "open photoshop", True),
        ("_L0_OPEN_APP", "whenever I open photoshop it crashes", False),
        ("_L0_DESIGN", "create a poster in photoshop", False),     # removed 2026-10-05: Adobe work goes through the agents' template tools
        ("_L0_DESIGN", "make a design in illustrator", False),
        ("_L0_DESIGN", "create a word document", True),            # Office and Ableton keep the shortcut
        ("_L0_DESIGN", "what's another word for happy", False),
        ("_L0_WAR_ROOM", "take this to the executive room", True),
        ("_L0_WAR_ROOM", "we redecorated the creative studio last week", False),
    ]:
        got = bool(getattr(srv, name).search(text))
        check(f"{name}: {text!r} → {'match' if want else 'no match'}", got == want)

    print("\n── F13: ws_chat studio-lock gate ──")
    client = TestClient(srv.app, base_url="http://localhost:8000")
    _HOST = {"host": "localhost:8000", "origin": "http://localhost:8000"}
    _had_lock = os.path.exists(dr.STUDIO_LOCK)
    try:
        open(dr.STUDIO_LOCK, "w").close()
        with client.websocket_connect("/ws/chat", headers=_HOST) as ws:
            ws.send_json({"message": "hello there, quick question", "agent": "analyst"})
            model_seen = None
            for _ in range(10):
                evt = ws.receive_json()
                if evt.get("type") == "done":
                    model_seen = evt.get("model")
                    break
            check("locked HUD chat is answered by the studio-lock gate (no LLM)",
                  model_seen == "studio-lock", f"got {model_seen}")
    finally:
        if not _had_lock:
            try: os.remove(dr.STUDIO_LOCK)
            except OSError: pass

    print("\n── F10: _ollama_stream surfaces in-stream errors ──")
    class _FakeResp:
        def __enter__(self): return iter([b'{"error": "model not found"}'])
        def __exit__(self, *a): return False

    async def _drain():
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp()):
            try:
                async for _ in srv._ollama_stream(b"{}"):
                    pass
            except RuntimeError as e:
                return str(e)
        return None

    err = asyncio.run(_drain())
    check("stream error chunk raises instead of yielding empty output",
          err is not None and "model not found" in err, f"got {err!r}")

    print("\n── F19: stuck-'running' task sweep ──")
    _orig_db = srv.TASKS_DB
    try:
        fd, tmp_db = tempfile.mkstemp(suffix=".db"); os.close(fd)
        conn = sqlite3.connect(tmp_db)
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, agent TEXT, "
                     "description TEXT, status TEXT, started_at TEXT, completed_at TEXT, result TEXT)")
        conn.execute("INSERT INTO tasks (title, agent, description, status, result) "
                     "VALUES ('stuck', 'analyst', 'x', 'running', '')")
        conn.execute("INSERT INTO tasks (title, agent, description, status, result) "
                     "VALUES ('fine', 'analyst', 'y', 'done', 'ok')")
        conn.commit(); conn.close()
        srv.TASKS_DB = tmp_db
        srv._sweep_stuck_tasks()
        conn = sqlite3.connect(tmp_db)
        rows = dict(conn.execute("SELECT title, status FROM tasks").fetchall())
        conn.close()
        check("stale 'running' task swept to 'failed'", rows.get("stuck") == "failed")
        check("completed tasks untouched by the sweep", rows.get("fine") == "done")
    finally:
        srv.TASKS_DB = _orig_db
        try: os.remove(tmp_db)
        except OSError: pass

    print("\n── F17: HUD audit lines tagged surface=hud ──")
    _orig_log = dr.LOG
    try:
        fd, tmp_log = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        dr.LOG = tmp_log
        srv.write_audit_log({"action": "unit_test_hud"})
        with open(tmp_log, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        check("write_audit_log delegates to the shared writer with surface=hud",
              entry.get("surface") == "hud" and "duration_ms" in entry and "request_id" in entry)
    finally:
        dr.LOG = _orig_log
        try: os.remove(tmp_log)
        except OSError: pass


# Restore the real paths and clean up the isolated ones — never leave the
# live audit log / studio lock pointed at a temp file, and never leave test
# scratch files behind.
dr.LOG = _ORIG_LOG
dr.STUDIO_LOCK = _ORIG_STUDIO_LOCK
try:
    os.remove(_ISO_LOG)
except OSError:
    pass
try:
    import shutil
    shutil.rmtree(_ISO_LOCK_DIR, ignore_errors=True)
except Exception:
    pass


print("\n── Summary ──")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
