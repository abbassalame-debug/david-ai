"""test_tier1_reliability.py — the Tier 1 fixes of 2026-10-20 (audit backlog P2-8, P1-1, P1-5, P1-6).

  P2-8  the tool-decision call stops reading as soon as the model starts writing prose (no wasted answer), and a long tool call
        still arrives whole;
  P1-1  overwriting a note first copies the old version to note_backups/, and a full write goes through a temp file + os.replace;
  P1-5  dlp_flag catches key, token, private-key, credential-URL, JWT and "password is" shapes, and benign text stays clean;
  P1-6  one failing chat turn sends an error and the same socket keeps working.

No network and no model: Ollama is faked, notes go to a temporary folder, the server runs on the TestClient without start-up hooks.
"""
from david_config import DAVID_HOME
import asyncio, json, os, shutil, sys, tempfile
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DAVID_APP_DB_URL", "DAVID_AUDIT_CONTENT"):
    os.environ.pop(k, None)

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import httpx
import tool_executor as te
import david_router as dr

# ── P2-8: the tool decision ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── P2-8: the tool decision stops at the first prose and keeps long tool calls whole ──")

def fake_stream(chunks, seen):
    """An httpx.AsyncClient whose stream() replays NDJSON chunks and records how many were read and whether it was closed."""
    class Resp:
        def raise_for_status(self): pass
        async def aiter_lines(self):
            for c in chunks:
                seen["read"] += 1
                yield json.dumps(c)
    class Ctx:
        async def __aenter__(self): return Resp()
        async def __aexit__(self, *a):
            seen["closed"] = True
            return False
    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, method, url, json=None, **kw):
            seen["payload"] = json
            return Ctx()
    return C

ran = []
async def rec_exec(name, args, agent=None, domain=None):
    ran.append((name, args)); return "ok"

def decide(chunks, msg="msg", agent="chief_of_staff"):
    seen = {"read": 0, "closed": False}
    audits = []
    with mock.patch.object(httpx, "AsyncClient", fake_stream(chunks, seen)), mock.patch.object(te, "execute_tool", rec_exec), \
            mock.patch("david_router.audit", audits.append):
        out = asyncio.run(te.decide_and_run_tool(agent, "general", msg, request_id="t"))
    return out, seen, audits

prose = [{"message": {"content": "Building a better evening routine "}, "done": False} for _ in range(200)] + [{"message": {}, "done": True}]
out, seen, _ = decide(prose)
check("a prose answer means no tool", out is None and not ran, str(out))
check("the prose is not read to the end: reading stops after a few dozen characters", seen["read"] <= 5, f"read {seen['read']} of {len(prose)}")
check("the stream is closed (closing it stops Ollama generating the discarded answer)", seen["closed"])
p = seen["payload"]
check("the request streams, keeps think off and temperature 0, and has only a backstop token cap",
      p["stream"] is True and p["think"] is False and p["options"]["temperature"] == 0 and p["options"]["num_predict"] >= 1024, str(p.get("options")))

long_content = "line of a long dictated note\n" * 400
tool_chunks = [{"message": {"content": "", "tool_calls": [{"function": {"name": "write_note", "arguments": {"vault": "System", "path": "Ideas/x.md", "content": long_content}}}]}, "done": False},
               {"message": {}, "done": True}]
ran.clear()
out, seen, _ = decide(tool_chunks)
check("a long tool call arrives whole (a flat 64-token cap would have cut it)",
      ran and ran[-1][0] == "write_note" and ran[-1][1]["content"] == long_content and out == "[TOOL: write_note]\nok", str(out)[:80])

ran.clear()
pre = [{"message": {"content": "Let me look."}, "done": False},
       {"message": {"content": "", "tool_calls": [{"function": {"name": "search_web", "arguments": {"query": "news"}}}]}, "done": False},
       {"message": {}, "done": True}]
out, _, _ = decide(pre)
check("a short preamble before the tool call does not lose the call", ran and ran[-1][0] == "search_web", str(ran))

ran.clear()
out, seen, _ = decide([{"message": {"content": "Sure."}, "done": True}])
check("a short answer that ends without a tool call means no tool", out is None and not ran)

class Boom:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def stream(self, *a, **kw): raise httpx.ConnectError("refused")
audits = []
with mock.patch.object(httpx, "AsyncClient", Boom), mock.patch("david_router.audit", audits.append):
    out = asyncio.run(te.decide_and_run_tool("chief_of_staff", "general", "msg", request_id="t"))
check("Ollama down: no tool, and the error is audited", out is None and any(a.get("action") == "tool_decision_error" for a in audits))

# ── P1-1: note overwrite backups and atomic writes ───────────────────────────────────────────────────────────────────────────
print("\n── P1-1: overwrite keeps a backup, full writes are atomic ──")
tmp = tempfile.mkdtemp(prefix="tier1_notes_")
vault = os.path.join(tmp, "vaults", "System")
backups = os.path.join(tmp, "note_backups")
os.makedirs(vault)
with mock.patch.dict(te.VAULT_ROOTS, {"system": vault}), mock.patch.object(te, "NOTE_BACKUP_DIR", backups):
    note = os.path.join(vault, "Ideas", "plan.md")
    r = te._write_note("System", "Ideas/plan.md", "version one")
    check("a new note is written", "written" in r and open(note, encoding="utf-8").read() == "version one", r)
    check("writing a new note makes no backup", not os.path.exists(backups))

    r = te._write_note("System", "Ideas/plan.md", "CLOBBER")
    check("an existing note is still refused without append or overwrite", "Refusing" in r and open(note, encoding="utf-8").read() == "version one")

    r = te._write_note("System", "Ideas/plan.md", "version two", overwrite=True)
    baks = [os.path.join(dp, f) for dp, _, fs in os.walk(backups) for f in fs]
    check("overwrite replaces the note", open(note, encoding="utf-8").read() == "version two", r)
    check("overwrite first saves the old version in note_backups/<vault>/<same folders>",
          len(baks) == 1 and open(baks[0], encoding="utf-8").read() == "version one"
          and os.path.dirname(baks[0]) == os.path.join(backups, "System", "Ideas"), str(baks))
    check("the tool result tells the user where the backup is", "backup" in r.lower() and baks and os.path.basename(baks[0]) in r, r)
    check("the backup is outside the vaults (so it is never indexed or synced as a note)", not baks[0].startswith(os.path.join(tmp, "vaults")))

    te._write_note("System", "Ideas/plan.md", "version three", overwrite=True)
    baks = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(backups) for f in fs)
    check("two overwrites in quick succession keep two separate backups",
          len(baks) == 2 and sorted(open(b, encoding="utf-8").read() for b in baks) == ["version one", "version two"], str(baks))

    r = te._write_note("System", "Ideas/plan.md", "more", append=True)
    check("append still adds to the note and makes no backup",
          open(note, encoding="utf-8").read() == "version three\nmore" and len([1 for _, _, fs in os.walk(backups) for f in fs]) == 2, r)

    def boom(*a, **k): raise OSError("disk full")
    with mock.patch.object(te.os, "replace", boom):
        try:
            r = te._write_note("System", "Ideas/plan.md", "half-written", overwrite=True)
        except OSError as e:
            r = f"raised {e}"
    check("a failed write leaves the note exactly as it was", open(note, encoding="utf-8").read() == "version three\nmore", r)
    left = [f for f in os.listdir(os.path.dirname(note)) if f != "plan.md"]
    check("a failed write leaves no temp file behind in the vault", left == [], str(left))

    r = te._write_note("System", "../../escape.md", "x", overwrite=True)
    check("path traversal is still refused", "Invalid path" in r and not os.path.exists(os.path.join(tmp, "escape.md")), r)
    r = te._write_note("Research", "x.md", "x", overwrite=True)
    check("the firewalled Research vault is still read-only", "read-only" in r, r)
shutil.rmtree(tmp, ignore_errors=True)

# A vault root spelled differently from its resolved path (an 8.3 short name on Windows) must not push the backup out of
# note_backups/ (found by CI on 2026-10-21: relpath climbed out with "..\..").
def _short(p):
    if os.name != "nt":
        return p
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024) else p
tmp2 = tempfile.mkdtemp(prefix="tier1_shortpath_root_")
try:
    vault_long = os.path.join(tmp2, "VaultWithALongFolderName")
    os.makedirs(os.path.join(vault_long, "Ideas"))
    vault_short = _short(vault_long)
    backups2 = os.path.join(tmp2, "note_backups")
    if vault_short == vault_long:
        print("  SKIP (needs 8.3 short names on this volume)  a short-form vault root keeps backups inside note_backups/")
    else:
        with mock.patch.dict(te.VAULT_ROOTS, {"system": vault_short}), mock.patch.object(te, "NOTE_BACKUP_DIR", backups2):
            te._write_note("System", "Ideas/plan.md", "v1")
            te._write_note("System", "Ideas/plan.md", "v2", overwrite=True)
        baks = [os.path.join(dp, f) for dp, _, fs in os.walk(tmp2) for f in fs if f.endswith(".bak")]
        check("a vault root given as an 8.3 short path still puts the backup inside note_backups/<vault>/<folders>",
              len(baks) == 1 and os.path.realpath(os.path.dirname(baks[0])).lower() == os.path.realpath(os.path.join(backups2, "VaultWithALongFolderName", "Ideas")).lower(),
              str(baks))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

# ── P1-5: DLP ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── P1-5: DLP catches common secret shapes and leaves benign text alone ──")
# Built from parts so this file never holds a complete key-shaped string.
SECRETS = {
    "AWS access key id":    "my key is " + "AKIA" + "ABCDEFGHIJKLMNOP",
    "GitHub token":         "token " + "gh" + "p_" + "a1B2" * 9,
    "GitHub fine-grained":  "github" + "_pat_" + "11ABCDE" * 4,
    "sk- API key":          "use " + "sk" + "-" + "proj" + "X9" * 12,
    "Google API key":       "AI" + "za" + "Sy" + "B" * 33,
    "PEM private key":      "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE...",
    "credential URL":       "connect to postgres://admin:" + "s3cretpw" + "@db.internal:5432/app",
    "bearer token":         "Authorization: Bearer " + "abcDEF123456ghiJKL789",
    "JWT":                  "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
    "password is":          "the wifi password is " + "Tr0ub4dor&3",
}
for label, text in SECRETS.items():
    audits = []
    with mock.patch.object(dr, "audit", audits.append):
        hit = dr.dlp_flag(text)
    check(f"DLP flags a {label}", hit is True)
    check(f"  …and the audit line names the pattern, not the secret ({label})",
          audits and all(text not in json.dumps(a) for a in audits), str(audits)[:120])

BENIGN = [
    "How do I build a better evening routine?",
    "Read https://example.com/docs/page?id=42 and summarise it",
    "sk-learn is a Python library",                       # not a key: too short after sk-
    "She was the bearer of good news",
    "AKIA is not a key on its own",
    "My password manager is great",                        # 'password' without 'is <value>'
    "اريد طلب سريع من فضلك",                                # Arabic 'fast' must not hit the 'secret' keyword
    "git clone https://github.com/user/repo.git",
]
for text in BENIGN:
    with mock.patch.object(dr, "audit", lambda e: None):
        check(f"benign text stays clean: {text[:40]!r}", dr.dlp_flag(text) is False)

sent = []
class _NoNetDDGS:
    """Stands in for the web search client: records the query and never touches the network, even if DLP lets it through."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def text(self, query, **kw):
        sent.append(query); return []
import types
with mock.patch.object(dr, "audit", lambda e: None), mock.patch.dict(sys.modules, {"ddgs": types.SimpleNamespace(DDGS=_NoNetDDGS)}):
    r = te._search_web("find docs for " + SECRETS["GitHub token"])
check("a web search carrying a GitHub token is blocked before it leaves the machine", r.startswith("Search blocked") and not sent, r[:60])

# ── P1-6: one failing turn does not end the chat socket ────────────────────────────────────────────────────────────────────
print("\n── P1-6: a failing turn sends an error and the socket keeps working ──")
try:
    from starlette.testclient import TestClient
    import server as srv
except Exception as e:
    check(f"server importable ({type(e).__name__}: {e})", False)
    srv = None

if srv is not None:
    client = TestClient(srv.app, base_url="http://localhost:8000")   # no context manager: start-up hooks (daemons) stay off
    HOST = {"host": "localhost:8000"}
    calls = {"n": 0}
    def flaky_layer0(msg):
        calls["n"] += 1
        if msg == "explode":
            raise RuntimeError("boom inside the turn: SECRET-CONTENT-123")
        return True, "pong", "test"
    def _ws_scenarios():
        audits = []
        with mock.patch.object(srv, "layer0_intercept", flaky_layer0), mock.patch.object(srv, "_router_audit", audits.append):
            with client.websocket_connect("/ws/chat", headers=HOST) as ws:
                ws.send_json({"message": "explode"})
                first = ws.receive_json()
                check("the failing turn reports an error to the client", first.get("type") == "error", str(first))
                ws.send_json({"message": "ping"})
                tok, done = ws.receive_json(), ws.receive_json()
                check("the SAME socket answers the next message", tok == {"type": "token", "token": "pong"} and done.get("type") == "done", f"{tok} {done}")
                ws.send_text("this is not json")
                bad = ws.receive_json()
                check("a malformed message gets an error, not a dropped connection", bad.get("type") == "error", str(bad))
                ws.send_json({"message": "ping"})
                tok = ws.receive_json(); ws.receive_json()
                check("…and the socket still works after it", tok.get("token") == "pong")
        errs = [a for a in audits if a.get("action") == "ws_chat_turn_error"]
        check("the failure is written to the audit log (it used to vanish)", len(errs) == 2, str(errs))
        check("the audit line names the exception type and keeps the message content out",
              errs and errs[0].get("error_type") == "RuntimeError" and "SECRET-CONTENT-123" not in json.dumps(errs), str(errs[:1]))

        calls["n"] = 0
        with mock.patch.object(srv, "layer0_intercept", flaky_layer0), mock.patch.object(srv, "_router_audit", lambda e: None):
            with client.websocket_connect("/ws/chat", headers=HOST) as ws:
                got = []
                for _ in range(srv.WS_CHAT_MAX_CONSECUTIVE_ERRORS):
                    ws.send_json({"message": "explode"})
                    got.append(ws.receive_json().get("type"))
                closed = False
                try:
                    ws.send_json({"message": "ping"})
                    ws.receive_json()
                except Exception:
                    closed = True
        check(f"after {srv.WS_CHAT_MAX_CONSECUTIVE_ERRORS} failures in a row the server closes the socket (no endless error loop)",
              got == ["error"] * srv.WS_CHAT_MAX_CONSECUTIVE_ERRORS and closed, str(got))

    # A time limit: on the old code the socket went dead after the first error and the test client waited for ever.
    import threading
    def _guarded():
        try:
            _ws_scenarios()
        except Exception as e:
            check(f"the chat socket scenarios ran without an exception ({type(e).__name__}: {e})", False)
    t = threading.Thread(target=_guarded, daemon=True)
    t.start()
    t.join(90)
    check("the chat socket scenarios finished in time (a dead socket used to hang the client)", not t.is_alive())

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.stdout.flush()
os._exit(0 if all(results) else 1)   # not sys.exit: a hung scenario thread (old code) must not keep the process alive
