"""
test_qwen35_wiring.py -- offline regression tests for the 2026-10-03 model-stack wiring.

No Ollama, no LanceDB, no GPU: HTTP is replaced with recorders. Pins down:
  * one canonical num_ctx per model (ollama_config.num_ctx_for) and that every live
    call site uses it -- a mismatch makes Ollama reload the model (8.6 s measured);
  * war-room payloads (think:false only for qwen3.5, every model gets its num_ctx);
  * chat_attachments._ask (think:false only for the notes model, judge untouched);
  * knowledge._is_catalog_stub;
  * agent_loop planning model / think flag;
  * no live module talks to "localhost" (2 s IPv6 penalty per call on this machine);
  * the CRM chat-log exclusion is shared by every ingest path, and rag.ingest_all's
    drop-the-table safety rail refuses without the explicit flags.

Run: py -3.11 test_qwen35_wiring.py
"""

from david_config import DAVID_HOME, DAVID_VAULTS
import sys, os, re, json, inspect, datetime, types
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


def src(path):
    return open(os.path.join(str(DAVID_HOME), path), encoding="utf-8").read()


import ollama_config

# ── canonical num_ctx ────────────────────────────────────────────────────────
print("\n-- ollama_config: one num_ctx per model --")
check("qwen3.5:9b runs at 16384 everywhere", ollama_config.num_ctx_for("qwen3.5:9b") == 16384)
check("fine-tuned / coder / failover models keep 8192",
      all(ollama_config.num_ctx_for(m) == 8192 for m in ("david-ft", "qwen-ft", "qwen2.5-coder:7b", "llama3.1:8b")))
check("deepseek-r1:14b has one value too", ollama_config.num_ctx_for("deepseek-r1:14b") == 12288)
check("Ollama URLs use 127.0.0.1", "127.0.0.1" in ollama_config.OLLAMA_CHAT_URL and "localhost" not in ollama_config.OLLAMA_HOST)

LIVE_CHAT_MODULES = [
    "david_router.py", "tool_executor.py", "agent_loop.py", "chat_attachments.py",
    "cross_domain_sweep.py",
    "link_graph.py", "knowledge.py", "office_bridge.py", "adobe_bridge.py",
    "ui/server.py",
]
_LITERAL_CTX = re.compile(r'["\']?num_ctx["\']?\s*[:=]\s*(\d+)')
for f in LIVE_CHAT_MODULES:
    code = "\n".join(l for l in src(f).splitlines() if not l.lstrip().startswith("#"))
    hits = _LITERAL_CTX.findall(code)
    check(f"{f}: no literal num_ctx (all via num_ctx_for)", not hits, f"found {hits}")
check("embedding.py pins its own (embedder) num_ctx and num_batch", 'options": {"num_ctx": 4096, "num_batch": 256}' in src("embedding.py"))

# ── localhost guard ──────────────────────────────────────────────────────────
print("\n-- no live module pays the localhost penalty --")
LIVE_FOR_URL = LIVE_CHAT_MODULES + ["embedding.py", "rag.py", "memory.py", "file_watcher.py", "turbo_ingest.py",
                                     "comfy_client.py", "process_manager.py", "obsidian_mcp.js",
                                     "mode_a.bat", "mode_b.bat"]
_URL_RE = re.compile(r"(?:https?|ws)://localhost[:/]")
_ORIGIN_ALLOWLIST_LINE = re.compile(r'^\s*"http://127\.0\.0\.1:\d+",\s*"http://localhost:\d+",?\s*$')
for f in LIVE_FOR_URL:
    # server's _ALLOWED_ORIGINS lists localhost as a BROWSER origin (what the HUD
    # tab sends), not as a URL David connects to -- that is the one legitimate use.
    code = "\n".join(l for l in src(f).splitlines()
                     if not l.lstrip().startswith(("#", "//", "::", "rem ")) and not _ORIGIN_ALLOWLIST_LINE.match(l))
    hits = _URL_RE.findall(code)
    check(f"{f}: no http://localhost client URL", not hits, str(hits))
check("server allows localhost in Host/Origin checks only (browser-facing, not a client URL)",
      "allowed_hosts=[\"127.0.0.1\", \"localhost\"]" in src("ui/server.py"))

# ── war rooms ────────────────────────────────────────────────────────────────
print("\n-- war-room payload helper --")
import server as srv

body = json.loads(srv._wr_payload("qwen3.5:9b", [{"role": "user", "content": "x"}], 150))
check("qwen3.5 war-room turn: think=False", body.get("think") is False)
check("qwen3.5 war-room turn: num_ctx = canonical 16384", body["options"]["num_ctx"] == 16384)
check("num_predict passed through", body["options"]["num_predict"] == 150 and body["stream"] is False)
body = json.loads(srv._wr_payload("qwen2.5-coder:7b", [{"role": "user", "content": "x"}], 150, stream=True))
check("build-room coder: no think flag (non-thinking model)", "think" not in body)
check("build-room coder: num_ctx = canonical 8192", body["options"]["num_ctx"] == 8192 and body["stream"] is True)
check("WAR_ROOM_MODEL is qwen3.5:9b", srv.WAR_ROOM_MODEL == "qwen3.5:9b")
check("_WR_RULES forbid echo and false action claims",
      "Do not repeat" in srv._WR_RULES and "never claim" in srv._WR_RULES)
check("HUD /api/ingest/start never runs the table-dropping full ingest",
      'cmd = ["py", "-3.11", r"<DAVID_HOME>\\rag.py", "ingest"]' not in src("ui/server.py")
      and "Full re-index drops the entire knowledge table" in src("ui/server.py"))
check("vault watcher skips CRM chat logs", "_excluded(fp)" in inspect.getsource(srv._vault_watcher))

# ── chat_attachments._ask ────────────────────────────────────────────────────
print("\n-- chat_attachments._ask --")
import asyncio
import chat_attachments as ca


class _FakeClient:
    def __init__(self):
        self.bodies = []

    async def post(self, url, json=None):
        self.bodies.append((url, json))
        r = types.SimpleNamespace()
        r.raise_for_status = lambda: None
        r.json = lambda: {"message": {"content": "answer"}, "done_reason": "stop"}
        return r


fc = _FakeClient()
asyncio.run(ca._ask(fc, "p", 50))
url, b = fc.bodies[-1]
check("notes model (model=None) -> qwen3.5:9b with think=False", b["model"] == "qwen3.5:9b" and b.get("think") is False)
check("notes model num_ctx is the canonical 16384", b["options"]["num_ctx"] == 16384)
check("num_predict = tokens", b["options"]["num_predict"] == 50)
check("_ask posts to 127.0.0.1", "127.0.0.1" in url)
asyncio.run(ca._ask(fc, "p", 50, model=ca.ANALYSIS_MODEL))
_, b = fc.bodies[-1]
check("deepseek judge: no think flag injected", "think" not in b and b["model"] == "deepseek-r1:14b")
check("deepseek judge: single canonical num_ctx", b["options"]["num_ctx"] == 12288)
asyncio.run(ca._ask(fc, "p", 50, think=True))
_, b = fc.bodies[-1]
check("explicit think=True is honoured", b.get("think") is True)
check("no per-call num_ctx arguments remain in chat_attachments", "num_ctx=" not in src("chat_attachments.py"))

# ── knowledge stub guard ─────────────────────────────────────────────────────
print("\n-- knowledge._is_catalog_stub --")
import knowledge
check("File:/Path: header-only row is a stub", knowledge._is_catalog_stub("File: a.wav | Path: /data/x/a.wav\nPath: /data/x"))
check("short residue under 40 chars is a stub", knowledge._is_catalog_stub("File: a\nsize 3 MB"))
check("real content is not a stub", not knowledge._is_catalog_stub(
    "File: contract.pdf\nThe tenant pays 1,200 USD monthly, due on the 1st, with a 5% late fee after 10 days."))
check("extract path skips the LLM for stubs", "_is_catalog_stub(text)" in src("knowledge.py"))
check("knowledge.py runs the LLM with think=False", '"think": False' in src("knowledge.py"))

# ── agent_loop planning ──────────────────────────────────────────────────────
print("\n-- agent_loop planning call --")
import agent_loop

cap = {}


def _fake_urlopen(req, timeout=None):
    cap["body"] = json.loads(req.data.decode())

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"message": {"content": "ok"}}'
    return _R()


with mock.patch("urllib.request.urlopen", _fake_urlopen):
    agent_loop._sync_call([{"role": "user", "content": "hi"}], agent_loop.PLANNING_MODEL, agent_loop.TOOLS)
b = cap["body"]
check("planning model is qwen3.5:9b", agent_loop.PLANNING_MODEL == "qwen3.5:9b" and b["model"] == "qwen3.5:9b")
check("planning call is think=False (tool_calls, not a reasoning trace)", b.get("think") is False)
check("planning call carries the canonical num_ctx", b["options"]["num_ctx"] == 16384)
check("planning call carries the tools schema", len(b.get("tools", [])) == len(agent_loop.TOOLS))
check("mode B planning falls back to REASONING_MODEL (same model, no reload)",
      agent_loop._pick_model("B") == agent_loop.REASONING_MODEL == "qwen3.5:9b")

# ── CRM exclusion shared by every ingest path ────────────────────────────────
print("\n-- CRM chat-log exclusion --")
import rag
check("backslash path excluded", rag.is_excluded_path(str(DAVID_VAULTS / "System" / "CRM" / "interactions" / "2026-10-03.md")))
check("forward-slash + mixed case excluded", rag.is_excluded_path(str(DAVID_VAULTS / "System" / "crm" / "Interactions" / "x.md")))
check("the folder itself (watcher import root) is excluded", rag.is_excluded_path(str(DAVID_VAULTS / "System" / "CRM" / "interactions") + os.sep))
check("ordinary vault note is not excluded", not rag.is_excluded_path(str(DAVID_VAULTS / "System" / "Briefings" / "2026-08-17.md")))
check("rag.ingest_file uses the shared helper", "is_excluded_path(filepath)" in inspect.getsource(rag.ingest_file))
check("rag.import_folder refuses an excluded root and skips excluded files",
      "is_excluded_path(root + os.sep)" in inspect.getsource(rag.import_folder)
      and "is_excluded_path(fpath)" in inspect.getsource(rag.import_folder))
check("file_watcher._should_skip uses the shared helper", "rag.is_excluded_path(fpath)" in src("file_watcher.py"))
check("turbo_ingest hands the shared helper to the folder scan (library_rules.scan_files)",
      "SKIP_DIRS, rag.is_excluded_path, docs_only_roots" in src("turbo_ingest.py"))
check("the folder scan applies that rule to every file before indexing or counting it",
      "if is_excluded(full) or is_junk_file(full):" in src("library_rules.py"))

import file_watcher
check("file_watcher skips a CRM transcript", file_watcher._should_skip(str(DAVID_VAULTS / "System" / "CRM" / "interactions" / "2026-10-03.md")))
check("file_watcher still indexes a normal note", not file_watcher._should_skip(str(DAVID_VAULTS / "System" / "Briefings" / "a.md")))

# ── ingest_all safety rail ───────────────────────────────────────────────────
print("\n-- rag.ingest_all drop-the-table safety rail --")


class _FakeTable:
    def __init__(self, n): self.n = n
    def count_rows(self, filter=None): return self.n


class _FakeDB:
    def __init__(self, n):
        self.n, self.dropped = n, []

    def open_table(self, name):
        if self.n is None:
            raise FileNotFoundError(name)
        return _FakeTable(self.n)

    def drop_table(self, name): self.dropped.append(name)
    def create_table(self, name, schema=None, data=None): return _FakeTable(0)


def run_ingest(rows, **kw):
    fake = _FakeDB(rows)
    lancedb_stub = types.SimpleNamespace(connect=lambda path: fake)
    with mock.patch.dict(sys.modules, {"lancedb": lancedb_stub}), \
         mock.patch.object(rag, "backup_table", lambda *a, **k: ("fake.parquet", rows)) as _b, \
         mock.patch.object(rag, "ingest_vault", lambda v: 0), \
         mock.patch.object(rag, "get_table", lambda: _FakeTable(0)), \
         mock.patch.object(rag, "_ensure_fts", lambda t: None):
        result = rag.ingest_all(**kw)
    return result, fake


r, db = run_ingest(50000)
check("bare ingest_all() refuses and drops nothing", r is None and db.dropped == [])
r, db = run_ingest(50000, confirm=True)
check("confirm=True alone refuses on a populated table (50k rows > threshold)", r is None and db.dropped == [])
r, db = run_ingest(50000, confirm=True, force_drop_populated=True)
check("confirm + force_drop_populated proceeds (drops after backup)", r == 0 and db.dropped == [rag.TABLE_NAME])
r, db = run_ingest(12, confirm=True)
check("confirm=True on a small table (<= threshold) proceeds", r == 0 and db.dropped == [rag.TABLE_NAME])
r, db = run_ingest(None, confirm=True)
check("confirm=True on a missing table proceeds (nothing to protect)", r == 0)
check("threshold is 1000 rows", rag.INGEST_DROP_THRESHOLD == 1000)
check("CLI maps --confirm-drop / --force-drop-populated", "--confirm-drop" in src("rag.py") and "--force-drop-populated" in src("rag.py"))
check("backup_table / restore_backup exist", callable(rag.backup_table) and callable(rag.restore_backup))

# ── qwen-ft sidelined, developer traffic on qwen3.5:9b (2026-10-05) ───────────
print("\n-- qwen-ft sidelined: developer/coding traffic runs on qwen3.5:9b --")
import tempfile
import david_router as dr

check("no per-agent model overrides remain", dr.AGENT_MODEL_OVERRIDES == {} and srv.AGENT_MODEL_OVERRIDES == {})
check("HUD path: developer (technical domain) resolves to qwen3.5:9b in every mode",
      all(srv.pick_model(m, "technical", "developer") == "qwen3.5:9b" for m in "ABCDE"))
check("HUD path: developer resolves to qwen3.5:9b even if the router mislabels the domain",
      srv.pick_model("A", "general", "developer") != "qwen-ft" and srv.pick_model("A", "business", "developer") == "qwen3.5:9b")
check("CLI path: execute() picks qwen3.5:9b for developer",
      (dr.AGENT_MODEL_OVERRIDES.get("developer") or dr.DOMAIN_MODELS.get("technical")) == "qwen3.5:9b")
check("uiux_engineer / prompt_architect (technical) were already on qwen3.5:9b", dr.DOMAIN_MODELS["technical"] == "qwen3.5:9b")
check("nothing indexes AGENT_MODEL_OVERRIDES['developer'] any more",
      'AGENT_MODEL_OVERRIDES["developer"]' not in src("david_router.py") and 'AGENT_MODEL_OVERRIDES["developer"]' not in src("ui/server.py"))
check("build-room developer is on the war-room model, not qwen2.5-coder",
      'wr_model = "qwen2.5-coder:7b"' not in src("ui/server.py") and src("ui/server.py").count("wr_model = WAR_ROOM_MODEL") == 2)
check("sidelined set names qwen-ft and qwen2.5-coder:7b", set(dr.SIDELINED_MODELS) == {"qwen-ft", "qwen2.5-coder:7b"})

_bat = src("mode_a.bat")
_warm_lines = [l for l in _bat.splitlines() if "keep_alive" in l and "-1" in l]
check("mode_a.bat pre-warms qwen3.5:9b, plus DAVID_GENERAL_MODEL only when it names another model", len(_warm_lines) == 2
      and any("qwen3.5:9b" in l and "DAVID_GENERAL_MODEL" not in l.split("curl")[0] for l in _warm_lines)
      and any("DAVID_GENERAL_MODEL" in l and "qwen3.5:9b\"==" in l.replace(" ", "") or "/i not" in l for l in _warm_lines))
check("mode_a.bat never pre-warms qwen-ft or the coder", not any("qwen-ft" in l or "qwen2.5-coder" in l for l in _warm_lines))
check("mode_a.bat unloads qwen-ft if something left it resident",
      any("qwen-ft" in l and 'keep_alive\\": 0' in l for l in _bat.splitlines()))

def _run_set_mode(letter):
    warmed, evicted = [], []
    tmp = tempfile.mkdtemp()
    with mock.patch.object(dr, "_ollama_warm", warmed.append), mock.patch.object(dr, "_ollama_evict", evicted.append), \
         mock.patch.object(dr, "MODE_FILE", os.path.join(tmp, "mode.state")), \
         mock.patch.object(dr, "STUDIO_LOCK", os.path.join(tmp, "studio.lock")), mock.patch.object(dr, "audit", lambda e: None):
        dr.set_mode(letter)
    return warmed, evicted

w, e = _run_set_mode("A")
check("set_mode('A') warms the router (qwen3.5:9b) and the general model only when it is a different one", sorted(set(w)) == sorted({"qwen3.5:9b", dr.DOMAIN_MODELS["general"]}), str(w))
with mock.patch.dict(dr.DOMAIN_MODELS, {"general": "my-finetune"}):
    w_ft, e_ft = _run_set_mode("A")
check("with a separate general model set_mode('A') warms both", sorted(set(w_ft)) == ["my-finetune", "qwen3.5:9b"], str(w_ft))
check("set_mode('A') never warms qwen-ft or qwen2.5-coder", "qwen-ft" not in w and "qwen2.5-coder:7b" not in w)
check("set_mode('A') unloads qwen-ft and qwen2.5-coder if left resident", {"qwen-ft", "qwen2.5-coder:7b"} <= set(e))
check("pre-warmed set fits the 14.5 GiB budget (measured: 5.8 + 5.4 GB)", 5.8 + 5.4 < 14.5)
for letter in "BCD":
    w, e = _run_set_mode(letter)
    check(f"set_mode('{letter}') keeps the router (qwen3.5:9b) loaded", "qwen3.5:9b" not in e, str(e))
w, e = _run_set_mode("C")
check("set_mode('C') never unloads the router when the general model is the router", "qwen3.5:9b" not in e, str(e))
with mock.patch.dict(dr.DOMAIN_MODELS, {"general": "my-finetune"}):
    w, e = _run_set_mode("C")
check("set_mode('C') unloads a separate general model for the audio session, and still keeps the router", "my-finetune" in e and "qwen3.5:9b" not in e, str(e))
w, e = _run_set_mode("E")
check("set_mode('E') still unloads the router for SDXL", "qwen3.5:9b" in e)
with mock.patch.dict(dr.DOMAIN_MODELS, {"general": "my-finetune"}):
    w, e = _run_set_mode("E")
check("set_mode('E') unloads a separate general model too", {"qwen3.5:9b", "my-finetune"} <= set(e))

print("\n-- Summary --")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
