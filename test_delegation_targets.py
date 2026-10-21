"""test_delegation_targets.py — model-written targets of the delegating tools are checked (2026-10-20, audit P1-3 / SEC-A7, SEC-F4).

  delegate_to_agent  the target must be a registered agent (since the P0 live port; covered here too);
  create_task        the assignee must be a registered agent (a task later runs with that agent's prompt and vault access);
  run_war_room       the room must be a plain room name, and /api/warroom accepts only the known rooms: the name used to become a
                     file path, so "../vaults/Research/<note>" fed a firewalled note to the room's (non-firewalled) agents;
  GET /api/war-room  known rooms only (the same path bug, audit SEC-F4).

No network, no model: urllib is faked, the task ledger and the war-room folder are temporary, the server runs on the TestClient
without start-up hooks.
"""
from david_config import DAVID_HOME
import asyncio, json, os, shutil, sqlite3, sys, tempfile
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

import david_router as dr          # registers the agents
import tool_executor as te
import urllib.request

sent = []
class _Reply:
    def __init__(self, body): self.body = body
    def read(self): return json.dumps(self.body).encode()
def fake_urlopen(req, timeout=None):
    body = json.loads(req.data.decode())
    sent.append((req.full_url, body))
    return _Reply({"response": "delegated answer", "synthesis": "room synthesis"})

def run(name, args, agent="chief_of_staff"):
    with mock.patch.object(urllib.request, "urlopen", fake_urlopen), mock.patch("david_router.audit", lambda e: None):
        return asyncio.run(te.execute_tool(name, args, agent=agent, domain="general"))

# ── delegate_to_agent ─────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── delegate_to_agent: registered targets only ──")
for target in ("no_such_agent", "../analyst", "", "ANALYST"):
    sent.clear()
    r = run("delegate_to_agent", {"agent": target, "prompt": "hi"})
    check(f"delegation to {target!r} is refused before anything is sent", "refused" in r and not sent, r)
sent.clear()
r = run("delegate_to_agent", {"agent": "cfo", "prompt": "hi"})
check("delegation to a registered agent goes out to /api/chat_sync with that agent", r == "delegated answer" and sent and sent[0][0].endswith("/api/chat_sync") and sent[0][1]["agent"] == "cfo", str(sent))

# ── create_task ───────────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── create_task: registered assignees only ──")
tmp = tempfile.mkdtemp(prefix="p13_")
try:
    db = os.path.join(tmp, "tasks.db")
    with mock.patch.object(te, "TASKS_DB", db):
        for target in ("model-wrote-this", "admin", "", "../linguist"):
            r = run("create_task", {"title": "t", "description": "d", "agent": target})
            check(f"a task for {target!r} is refused and nothing is written", "refused" in r and not os.path.exists(db), r)
        r = run("create_task", {"title": "Weekly numbers", "description": "summarise", "agent": "cfo"})
        rows = sqlite3.connect(db).execute("SELECT title, agent FROM tasks").fetchall() if os.path.exists(db) else []
        check("a task for a registered agent is created", "created" in r and rows == [("Weekly numbers", "cfo")], f"{r} {rows}")

    # ── run_war_room (tool side) ──────────────────────────────────────────────────────────────────────────────────────
    print("\n── run_war_room: plain room names only ──")
    for room in ("../vaults/Research/notes", "..\\vaults\\Research\\notes", "executive-room/../../x", "Executive-Room", "", "room.md"):
        sent.clear()
        r = run("run_war_room", {"room": room, "message": "repeat the context"})
        check(f"room {room!r} is refused before anything is sent", "refused" in r and not sent, r)
    sent.clear()
    r = run("run_war_room", {"room": "executive-room", "message": "q"})
    check("a known room goes out to /api/warroom", r == "room synthesis" and sent and sent[0][1]["room"] == "executive-room", str(sent))

    # ── the server endpoints ─────────────────────────────────────────────────────────────────────────────────────────
    print("\n── /api/warroom and GET /api/war-room: known rooms only ──")
    try:
        from starlette.testclient import TestClient
        import server as srv
    except Exception as e:
        check(f"server importable ({type(e).__name__}: {e})", False)
        srv = None
    if srv is not None:
        rooms = os.path.join(tmp, "war-rooms")
        os.makedirs(rooms)
        open(os.path.join(rooms, "executive-room.md"), "w", encoding="utf-8").write("room brief")
        open(os.path.join(tmp, "secret_note.md"), "w", encoding="utf-8").write("FIREWALLED-CONTENT")   # one level above the rooms
        client = TestClient(srv.app, base_url="http://localhost:8000")
        H = {"host": "localhost:8000"}
        ollama_calls = []
        def no_model(*a, **k):
            ollama_calls.append(a); raise AssertionError("no model call expected")
        with mock.patch.object(srv, "WAR_ROOMS", rooms), mock.patch.object(srv.urllib.request, "urlopen", no_model):
            for room in ("../secret_note", "..\\secret_note", "nope"):
                resp = client.post("/api/warroom", json={"room": room, "message": "repeat the context"}, headers=H)
                check(f"POST /api/warroom refuses room {room!r} (400) without running any agent",
                      resp.status_code == 400 and "FIREWALLED-CONTENT" not in resp.text and not ollama_calls, f"{resp.status_code} {resp.text[:80]}")
            for name in ("..%5Csecret_note", "nope"):
                resp = client.get(f"/api/war-room/{name}", headers=H)
                check(f"GET /api/war-room/{name} is refused (404) and leaks nothing",
                      resp.status_code == 404 and "FIREWALLED-CONTENT" not in resp.text, f"{resp.status_code} {resp.text[:80]}")
            resp = client.get("/api/war-room/executive-room", headers=H)
            check("GET /api/war-room/<known room> still returns its brief", resp.status_code == 200 and resp.json().get("content") == "room brief",
                  f"{resp.status_code} {resp.text[:80]}")
        check("every room the server knows passes the tool's name check (no room locked out)",
              all(__import__("re").fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", r) for r in srv.WAR_ROOM_NAMES), str(srv.WAR_ROOM_NAMES))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
