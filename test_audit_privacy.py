"""
test_audit_privacy.py -- the audit log records what happened, never what was said (audit finding S-01, P0-A).

Synthetic content only. DAVID_DATA / DAVID_VAULTS point at a temporary folder before any David module is imported,
DAVID_AUDIT_CONTENT and secrets are removed from the environment, and Ollama / the HUD are driven in-process with
fakes, so nothing here can write to a real audit log.

Run: python test_audit_privacy.py
"""
import os, sys, json, tempfile, shutil, asyncio

_TMP = tempfile.mkdtemp(prefix="david_test_auditpriv_")
os.environ["DAVID_DATA"] = os.path.join(_TMP, "data")
os.environ["DAVID_VAULTS"] = os.path.join(_TMP, "vaults")
for _k in ("DAVID_AUDIT_CONTENT", "DAVID_APP_DB_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DAVID_AUDIT_MAX_MB", "DAVID_AUDIT_BACKUPS"):
    os.environ.pop(_k, None)
os.makedirs(os.environ["DAVID_DATA"])
for _v in ("System", "Business", "Music", "Lore", "Research"):
    os.makedirs(os.path.join(os.environ["DAVID_VAULTS"], _v))

from david_config import DAVID_HOME
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail and not ok else ""))


import david_router as dr          # noqa: E402
import tool_executor as te         # noqa: E402

LOG = os.path.join(os.environ["DAVID_DATA"], "audit_log.jsonl")
check("the audit log path is inside this test's temporary folder", os.path.abspath(dr.LOG) == os.path.abspath(LOG) and dr.LOG.startswith(_TMP), dr.LOG)

USER_MSG = "my synthetic question ZEBRA-USER-7731 about the lease"
REPLY = "synthetic reply ZEBRA-REPLY-4410 with details"
SECRET_ARG = "password: Hunter2-ZEBRA-ARG"
SECRET_RESULT = "api_key = sk-ZEBRAresult0123456789abcdef"
AWS = "AKIAZEBRA0123456789A"
CARD = "4111 1111 1111 1111"


def log_text():
    return open(dr.LOG, encoding="utf-8").read() if os.path.exists(dr.LOG) else ""


def last_entry():
    return json.loads(log_text().strip().splitlines()[-1])


# 1-2: chat turn text (the shape ui/server.py writes after every turn)
print("\n-- default mode: message and reply text --")
dr.audit({"agent": "analyst", "model": "qwen3.5:9b", "mode": "A", "skill": "chat", "user": USER_MSG[:200],
          "response": REPLY[:200], "request_id": "req-1", "duration_ms": 1234, "surface": "hud"})
txt = log_text()
check("a synthetic user message is not written", "ZEBRA-USER-7731" not in txt)
check("a synthetic assistant reply is not written", "ZEBRA-REPLY-4410" not in txt)
e = last_entry()
check("safe metadata is kept (agent, model, skill, request id, duration, surface)",
      e["agent"] == "analyst" and e["model"] == "qwen3.5:9b" and e["skill"] == "chat" and e["request_id"] == "req-1"
      and e["duration_ms"] == 1234 and e["surface"] == "hud", str(e))
check("the message is replaced by a size marker, not a hash or excerpt", e["user"] == f"<redacted:{len(USER_MSG)} chars>", e["user"])
dr.audit({"layer": 1, "input": USER_MSG, "route": {"agent": "cfo", "domain": "business", "intent": "chat", "confidence": 0.9}})
e = last_entry()
check("CLI input is redacted while the routing decision stays readable", "ZEBRA-USER" not in json.dumps(e) and e["route"]["agent"] == "cfo")

# 3-4: tool arguments and results through the real tool-decision path (fake model)
print("\n-- tool arguments and results --")
open(os.path.join(os.environ["DAVID_VAULTS"], "System", "secret.md"), "w", encoding="utf-8").write(f"note body {SECRET_RESULT} {AWS} {CARD}")


class _Resp:
    def __init__(self, d):
        self._d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def _client(calls):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return _Resp({"message": {"content": "", "tool_calls": calls}})

        def stream(self, method, url, json=None, **kw): return _Streamed(self.post(url, json=json))
    return C


class _Streamed:
    """The streamed form of a faked reply (the tool decision streams since 2026-10-20): one NDJSON line with the whole message."""
    def __init__(self, reply): self.reply = reply
    async def __aenter__(self):
        self.resp = await self.reply
        return self
    async def __aexit__(self, *a): return False
    def raise_for_status(self): self.resp.raise_for_status()
    async def aiter_lines(self):
        yield json.dumps({**self.resp.json(), "done": True})


import httpx  # noqa: E402
_real = httpx.AsyncClient
try:
    httpx.AsyncClient = _client([{"function": {"name": "read_note", "arguments": {"vault": "system", "path": "secret.md"}}}])
    out = asyncio.run(te.decide_and_run_tool("chief_of_staff", "general", "read my secret note", request_id="req-2"))
    httpx.AsyncClient = _client([{"function": {"name": "write_note", "arguments": {"vault": "system", "path": "n.md", "content": f"{SECRET_ARG} {CARD}"}}}])
    asyncio.run(te.decide_and_run_tool("chief_of_staff", "general", "save it", request_id="req-3"))
finally:
    httpx.AsyncClient = _real
txt = log_text()
check("the tool still returned the note to the caller (behaviour unchanged)", out and "ZEBRAresult" in out)
check("tool result text is not in the log", "ZEBRAresult" not in txt and "note body" not in txt)
check("tool argument values are not in the log", "Hunter2-ZEBRA-ARG" not in txt)
check("credentials, API keys, AWS keys and card numbers are never logged", all(s not in txt for s in ("Hunter2", "sk-ZEBRA", AWS, CARD)))
calls = [json.loads(l) for l in txt.splitlines() if json.loads(l).get("action") == "tool_decision_call"]
check("tool name and argument NAMES remain for diagnosis", calls and calls[-1]["tool"] == "write_note"
      and set(calls[-1]["args"]) == {"vault", "path", "content"} and all(v.startswith("<redacted:") for v in calls[-1]["args"].values()), str(calls[-1:]))
dr.audit({"action": "probe", "args": {"Robert'); DROP": "x", "ok_name": "y"}})
check("an odd argument name is not kept verbatim", "DROP" not in log_text())

# error strings
dr.audit({"action": "rag_context_failure", "error": r"ValueError: bad value 'ZEBRA-ERR-99' in C:\Users\someone\vault\note.md (password: zz)"})
e = last_entry()
check("error text keeps the exception type but drops quoted values, paths and secrets",
      e["error"].startswith("ValueError") and "ZEBRA-ERR" not in e["error"] and "someone" not in e["error"] and "zz" not in e["error"]
      and e["status"] == "error", e["error"])

# a future free-text field that nobody listed is still redacted
dr.audit({"action": "new_feature", "notes_for_later": "x" * 300})
check("an unlisted long text field is redacted too (fail-safe)", last_entry()["notes_for_later"] == "<redacted:300 chars>")

# 12: debug mode
print("\n-- DAVID_AUDIT_CONTENT=1 (debug mode) --")
check("debug mode is off by default (not set by this test or by the code)", os.environ.get("DAVID_AUDIT_CONTENT") is None)
os.environ["DAVID_AUDIT_CONTENT"] = "1"
try:
    dr.audit({"agent": "analyst", "user": f"debug excerpt ZEBRA-DBG {SECRET_ARG} {AWS} {CARD} " + "y" * 400})
finally:
    os.environ.pop("DAVID_AUDIT_CONTENT", None)
e = last_entry()
check("debug mode keeps a short excerpt", "ZEBRA-DBG" in e["user"] and len(e["user"]) <= 200, e["user"][:80])
check("debug mode still removes credentials, AWS keys and card numbers", all(s not in e["user"] for s in ("Hunter2", AWS, CARD)), e["user"][:120])
dr.audit({"agent": "analyst", "user": "back to normal ZEBRA-AFTER"})
check("switching debug mode off restores full redaction", "ZEBRA-AFTER" not in last_entry()["user"])

# 6-7: rotation and retention
print("\n-- rotation and retention --")
rot_dir = os.path.join(_TMP, "rot")
os.makedirs(rot_dir)
_orig_log, _orig_max, _orig_bk = dr.LOG, dr.AUDIT_MAX_BYTES, dr.AUDIT_BACKUPS
dr.LOG, dr.AUDIT_MAX_BYTES, dr.AUDIT_BACKUPS = os.path.join(rot_dir, "audit_log.jsonl"), 2000, 3
try:
    for i in range(400):
        dr.audit({"action": "rotation_probe", "i": i, "agent": "analyst"})
    files = sorted(os.listdir(rot_dir))
    logs = [f for f in files if f.startswith("audit_log.jsonl") and not f.endswith(".lock")]
    check("the log rotates at the size limit", "audit_log.jsonl.1" in logs, str(files))
    check("retention is bounded: at most AUDIT_BACKUPS + 1 files", len(logs) <= 4 and "audit_log.jsonl.4" not in logs, str(logs))
    check("each file stays near the limit (one entry of slack)", all(os.path.getsize(os.path.join(rot_dir, f)) < 2000 + 400 for f in logs))
    def newest():
        # rotation runs right after a write, so the newest line is in the active file or, just after a rotation, in .1
        for f in (dr.LOG, dr.LOG + ".1"):
            if os.path.exists(f) and os.path.getsize(f):
                return json.loads(open(f, encoding="utf-8").read().strip().splitlines()[-1])
    check("the newest entry survives rotation", newest()["i"] == 399)
    # a rotation that fails (Windows: file held by another process) must not lose the entry
    _real_replace = os.replace

    def _boom(*a, **k):
        raise PermissionError("held by another process")
    os.replace = _boom
    try:
        for i in range(400, 460):
            dr.audit({"action": "rotation_probe", "i": i})
    finally:
        os.replace = _real_replace
    tail = [json.loads(l)["i"] for l in open(dr.LOG, encoding="utf-8").read().strip().splitlines()]
    check("a failed rotation keeps writing (no entry lost, no exception)", tail[-1] == 459 and 400 in tail)
    dr.audit({"action": "rotation_probe", "i": 460})
    check("rotation resumes on a later write", newest()["i"] == 460 and (not os.path.exists(dr.LOG) or os.path.getsize(dr.LOG) < 2000 + 400)
          and len([f for f in os.listdir(rot_dir) if f.startswith("audit_log.jsonl") and not f.endswith(".lock")]) <= 4)
    # missing directory
    dr.LOG = os.path.join(rot_dir, "new_sub_dir", "audit_log.jsonl")
    dr.audit({"action": "missing_dir_probe"})
    check("a missing log directory is created", os.path.exists(dr.LOG))
finally:
    dr.LOG, dr.AUDIT_MAX_BYTES, dr.AUDIT_BACKUPS = _orig_log, _orig_max, _orig_bk

# 8-11, 14: the /api/audit endpoint
print("\n-- /api/audit --")
try:
    from starlette.testclient import TestClient
    import server as srv
    _HUD_OK = True
except Exception as ex:  # pragma: no cover
    _HUD_OK = False
    check("the HUD app can be imported for the endpoint tests", False, f"{type(ex).__name__}: {ex}")

if _HUD_OK:
    api_dir = os.path.join(_TMP, "api")
    os.makedirs(api_dir)
    _orig_log = dr.LOG
    dr.LOG = os.path.join(api_dir, "audit_log.jsonl")
    try:
        legacy = [
            json.dumps({"ts": "2026-10-01T10:00:00", "agent": "cfo", "user": "LEGACY-RAW-QUESTION-123", "response": "LEGACY-RAW-REPLY-456",
                        "args": {"content": "LEGACY-ARG-789"}, "result": "LEGACY-RESULT-000", "status": "ok"}),
            '{"ts": "2026-10-01T10:01:00", "agent": "cfo", "user": "TRUNCATED-LEGACY-LINE',      # partial write
            "this is not json at all PLAIN-TEXT-LEAK",
            json.dumps(["a", "list", "LIST-LEAK"]),
        ]
        with open(dr.LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(legacy) + "\n")
        for i in range(260):
            dr.audit({"agent": "analyst", "skill": "chat", "user": f"new question {i}", "surface": "hud"})
        c = TestClient(srv.app, base_url="http://127.0.0.1:8000")
        r = c.get("/api/audit", params={"n": 10 ** 6})
        body = r.json()
        check("the endpoint caps the number of entries", r.status_code == 200 and len(body["entries"]) == srv.AUDIT_TAIL_MAX, str(len(body.get("entries", []))))
        r = c.get("/api/audit", params={"n": 300})
        check("a malformed or partial log does not crash the endpoint", r.status_code == 200)
        # legacy raw lines at the end of the file are sanitised on the way out
        with open(dr.LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(legacy) + "\n")
        r = c.get("/api/audit", params={"n": 50})
        raw = r.text
        check("legacy raw entries are sanitised before output", all(s not in raw for s in ("LEGACY-RAW-QUESTION", "LEGACY-RAW-REPLY", "LEGACY-ARG", "LEGACY-RESULT")), raw[:200])
        check("malformed lines are skipped and counted, never echoed", all(s not in raw for s in ("TRUNCATED-LEGACY", "PLAIN-TEXT-LEAK", "LIST-LEAK")) and r.json()["skipped"] >= 3)
        legacy_out = [x for x in r.json()["entries"] if x.get("ts") == "2026-10-01T10:00:00"]
        check("a sanitised legacy entry keeps its safe fields", legacy_out and legacy_out[0]["agent"] == "cfo" and legacy_out[0]["user"].startswith("<redacted:"))
        check("no response field carries raw text from new entries either", "new question" not in raw)
        # access boundary
        far = TestClient(srv.app, base_url="http://127.0.0.1:8000", client=("192.168.1.50", 50000))
        r = far.get("/api/audit")
        check("a non-loopback peer is refused (403)", r.status_code == 403 and "entries" not in r.text)
        r = c.get("/api/audit", headers={"Origin": "http://evil.example"})
        check("a cross-site browser read is refused (403)", r.status_code == 403)
        r = TestClient(srv.app, base_url="http://evil.example").get("/api/audit")
        check("a foreign Host header is refused (DNS-rebinding guard still applies)", r.status_code == 400)
        r = c.get("/api/audit", headers={"Origin": "http://127.0.0.1:8000"})
        check("the HUD's own page can still read it", r.status_code == 200)
        # HUD compatibility: the activity feeds read ts / agent / user; the label falls back to skill/action
        e0 = next(x for x in reversed(r.json()["entries"]) if x.get("skill") == "chat")
        check("entries keep the fields the HUD activity feeds read (ts, agent, user, skill)", {"ts", "agent", "user", "skill"} <= set(e0), str(e0))
        tsx = open(DAVID_HOME / "ui" / "frontend" / "src" / "pages" / "system-page.tsx", encoding="utf-8").read()
        tsx2 = open(DAVID_HOME / "ui" / "frontend" / "src" / "pages" / "business-page.tsx", encoding="utf-8").read()
        check("both HUD feeds show the action instead of a redaction marker", all("auditLabel(e)" in t and 'startsWith("<redacted")' in t for t in (tsx, tsx2)))
        # a huge log file is not read whole
        with open(dr.LOG, "a", encoding="utf-8") as f:
            pad = json.dumps({"action": "pad", "agent": "x"}) + "\n"
            f.write(pad * 60000)                        # ~2 MB
        import time as _t
        t0 = _t.time()
        entries, skipped = srv.get_audit_tail(20)
        check("only the end of a large file is read", len(entries) == 20 and _t.time() - t0 < 2.0)
    finally:
        dr.LOG = _orig_log

# 13: nothing reached a real audit log
print("\n-- isolation --")
check("every audit write of this test stayed inside its temporary folder", dr.LOG.startswith(_TMP))

shutil.rmtree(_TMP, ignore_errors=True)
passed = sum(1 for r in results if r)
print(f"\n  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
