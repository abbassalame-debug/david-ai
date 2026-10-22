"""test_hud_auth.py — the HUD login (hud_auth.py + ui/server.py, 2026-10-21, workstream 3).

  1. every route in app.routes (and both mounts) answers 401 to an anonymous request; both WebSockets are closed with 1008
     before they are accepted; only /login, /healthz, /api/auth/*, the icons and /assets are open
  2. login, the cookie flags, an authenticated request, an authenticated WebSocket, logout
  3. idle (12 h) and absolute (7 days) expiry
  4. lockout: 5 wrong passwords -> 15 minutes, then the right password works again
  5. scoped tokens (create, use on its route only, revoke) and the HUD's in-memory internal token
  6. the modes: off only on a loopback bind; no password and no DAVID_AUTH=off -> refuses to start, 503 except /healthz
  7. the store holds an Argon2id hash only, and audit lines never contain the password, the cookie or a token

Runs against a temporary data folder; no Ollama call is made.
"""
import os, re, sys, tempfile, shutil, json, asyncio
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp(prefix="hud_auth_test_")
os.environ["DAVID_DATA"] = os.path.join(TMP, "data")       # set before david_config is imported
os.environ["DAVID_VAULTS"] = os.path.join(TMP, "vaults")
os.makedirs(os.environ["DAVID_DATA"])
for v in ("System", "Business", "Music", "Lore", "Research"):
    os.makedirs(os.path.join(TMP, "vaults", v))
os.environ.pop("DAVID_AUTH", None)                       # this suite tests the login ON (the runner sets it off for the others)
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DAVID_APP_DB_URL", "DAVID_AUDIT_CONTENT"):
    os.environ.pop(k, None)
from david_config import DAVID_HOME
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import hud_auth
check("the auth store lives in this test's temporary data folder", hud_auth.AUTH_FILE.startswith(TMP), hud_auth.AUTH_FILE)
from starlette.testclient import TestClient
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.websockets import WebSocketDisconnect
import server as srv

AUDIT = []
hud_auth.AUDIT = AUDIT.append
PASSWORD = "correct horse battery staple 42"
H = {"host": "localhost:8000"}

def client():
    return TestClient(srv.app, base_url="http://localhost:8000")   # no context manager: start-up hooks (daemons) stay off


# ── 6a. no password yet ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── no password, DAVID_AUTH unset ──")
check("mode is 'unconfigured'", hud_auth.mode() == "unconfigured")
try:
    hud_auth.startup_check(); refused = False
except RuntimeError as e:
    refused = "set_hud_password" in str(e)
check("the server refuses to start, and says how to set a password", refused)
c = client()
check("every request gets 503 …", c.get("/api/status", headers=H).status_code == 503)
check("… except /healthz", c.get("/healthz", headers=H).status_code == 200)

# ── password store ───────────────────────────────────────────────────────────────────────────────────────────────────
try:
    hud_auth.set_password("short")
    short_ok = True
except ValueError:
    short_ok = False
check("a password under 12 characters is refused", not short_ok)
hud_auth.set_password(PASSWORD)
store = open(hud_auth.AUTH_FILE, encoding="utf-8").read()
check("the store holds an Argon2id hash, never the password", "$argon2id$" in store and PASSWORD not in store)
check("mode is now 'on'", hud_auth.mode() == "on")
try:
    hud_auth.startup_check(); starts = True
except RuntimeError:
    starts = False
check("with a password set the server starts", starts)

# ── 1. every route is closed to an anonymous caller ──────────────────────────────────────────────────────────────────
print("\n── 1. anonymous requests to every route ──")
OPEN_OK = {"/healthz", "/api/auth/status", "/api/auth/login", "/api/auth/logout", "/favicon.svg", "/icons.svg"}
def concrete(path):
    return re.sub(r"\{[^}]+\}", "x", path).replace("/x", "/somepage") if "{full_path" in path else re.sub(r"\{[^}]+\}", "x", path)
def http_routes(routes):
    """Every HTTP route, also inside included routers: FastAPI 0.146 keeps an included router as one nested entry
    (effective_candidates()) instead of copying its routes into app.routes."""
    for r in routes:
        if hasattr(r, "effective_candidates"):
            yield from http_routes(r.effective_candidates())
        elif isinstance(r, Route) or (hasattr(r, "path") and getattr(r, "methods", None)):
            yield r


c = client()
leaks, checked = [], 0
for r in http_routes(srv.app.routes):
    if True:
        p = concrete(r.path)
        if r.path in OPEN_OK:
            continue
        for m in sorted((r.methods or {"GET"}) - {"HEAD"}):
            resp = c.request(m, p, headers=H)
            checked += 1
            if resp.status_code != 401:
                leaks.append(f"{m} {p} -> {resp.status_code}")
check(f"every HTTP route outside the open list answers 401 anonymously ({checked} route/method pairs)", not leaks and checked > 50, "; ".join(leaks[:6]))
check("the generated-files mount answers 401", c.get("/generated/anything.png", headers=H).status_code == 401)
check("FastAPI's /docs and /openapi.json are closed too", c.get("/docs", headers=H).status_code == 401 and c.get("/openapi.json", headers=H).status_code == 401)
page = c.get("/system", headers={**H, "accept": "text/html"})
check("a browser asking for a page gets 401 with a page that forwards to /login", page.status_code == 401 and 'url=/login' in page.text)
api = c.get("/api/status", headers={**H, "accept": "text/html"})
check("an /api route answers JSON, never the HTML page", api.status_code == 401 and api.headers["content-type"].startswith("application/json"))
# 200 with a built frontend, 503 ("the HUD is not built") on a fresh clone: either way the login middleware let it through
r = c.get("/login", headers={**H, "accept": "text/html"}, follow_redirects=False)
check("the login page itself is open", r.status_code in (200, 503), str(r.status_code))
check("the React bundle folder is open (it holds no data)", c.get("/assets/nope.js", headers=H).status_code in (200, 404))
for ws_path in [r.path for r in srv.app.routes if isinstance(r, WebSocketRoute)]:
    code = None
    try:
        with c.websocket_connect(ws_path, headers=H) as ws:
            ws.receive_json()
    except WebSocketDisconnect as e:
        code = e.code
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
    check(f"{ws_path}: an anonymous WebSocket is closed with 1008 before it is accepted", code == 1008, str(code))

# ── 2. login, cookie, logout ─────────────────────────────────────────────────────────────────────────────────────────
print("\n── 2. login and logout ──")
c = client()
bad = c.post("/api/auth/login", json={"password": "wrong password here"}, headers=H)
check("a wrong password is 401 with a sentence", bad.status_code == 401 and "Wrong password" in bad.text)
good = c.post("/api/auth/login", json={"password": PASSWORD}, headers=H)
sc = good.headers.get("set-cookie", "")
check("the right password signs in", good.status_code == 200 and good.json().get("ok") is True)
check("the cookie is david_session, HttpOnly, SameSite=Strict, Path=/", sc.startswith("david_session=") and "HttpOnly" in sc and "SameSite=Strict" in sc and "Path=/" in sc, sc[:20] + "…")
sid = sc.split(";")[0].split("=", 1)[1]
check("the session id is long and random (256 bits, url-safe base64)", len(sid) >= 43)
check("only a hash of the session id is stored", sid not in open(hud_auth.AUTH_FILE, encoding="utf-8").read())
COOK = {**H, "cookie": f"david_session={sid}"}
check("a signed-in request works", c.get("/api/status", headers=COOK).status_code == 200)
st = c.get("/api/auth/status", headers=COOK).json()
check("/api/auth/status reports the login on and this browser signed in", st == {"login": "on", "signed_in": True}, str(st))
accepted = False
with mock.patch.object(srv, "layer0_intercept", lambda msg: (True, "pong", "test")), mock.patch.object(srv, "_router_audit", lambda e: None):
    try:
        with c.websocket_connect("/ws/chat", headers=COOK) as ws:
            ws.send_json({"message": "ping"})
            accepted = ws.receive_json() == {"type": "token", "token": "pong"}
    except Exception as e:
        accepted = False
check("a signed-in WebSocket is accepted and works", accepted)
out = c.post("/api/auth/logout", headers=COOK)
check("logout clears the cookie", out.status_code == 200 and "Max-Age=0" in out.headers.get("set-cookie", ""))
check("after logout the old cookie no longer works (server-side)", c.get("/api/status", headers=COOK).status_code == 401)

# ── 3. expiry ────────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 3. expiry ──")
status, sid2 = hud_auth.login(PASSWORD)
t0 = hud_auth.now()
with mock.patch.object(hud_auth, "now", lambda: t0 + 11 * 3600):
    check("11 h idle: still signed in (and the request refreshes last_seen)", hud_auth.session_valid(sid2))
with mock.patch.object(hud_auth, "now", lambda: t0 + 11 * 3600 + 12 * 3600 + 60):
    check("12 h idle after the last request: signed out", not hud_auth.session_valid(sid2))
status, sid3 = hud_auth.login(PASSWORD)
t0 = hud_auth.now()
alive = True
for day in range(1, 8):
    with mock.patch.object(hud_auth, "now", lambda d=day: t0 + d * 24 * 3600 - 3600):
        alive = alive and hud_auth.session_valid(sid3)   # used every day, never idle 12 h
with mock.patch.object(hud_auth, "now", lambda: t0 + 7 * 24 * 3600 + 60):
    expired = not hud_auth.session_valid(sid3)
check("used daily, a session still ends after 7 days (absolute limit)", expired, f"alive during the week: {alive}")

# ── 4. lockout ───────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 4. lockout ──")
c = client()
codes = [c.post("/api/auth/login", json={"password": f"nope {i}"}, headers=H).status_code for i in range(5)]
check("4 wrong passwords are 401, the 5th locks (429)", codes == [401, 401, 401, 401, 429], str(codes))
locked = c.post("/api/auth/login", json={"password": PASSWORD}, headers=H)
check("while locked even the right password is refused", locked.status_code == 429 and "15 minutes" in locked.text)
t0 = hud_auth.now()
with mock.patch.object(hud_auth, "now", lambda: t0 + 15 * 60 + 5):
    s, _ = hud_auth.login(PASSWORD)
check("after 15 minutes the right password works again", s == "ok")
check("the lockout is audited as auth_locked", any(a.get("action") == "auth_locked" for a in AUDIT))

# ── 5. tokens ────────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 5. scoped tokens ──")
tok = hud_auth.create_token("system-scan", ["GET /api/restaurant/*", "GET /api/design/overview"])
TH = {**H, "authorization": f"Bearer {tok}"}
check("the token is not stored in the clear", tok not in open(hud_auth.AUTH_FILE, encoding="utf-8").read())
check("a token works on a route in its scope (prefix scope)", c.get("/api/restaurant/overview", headers=TH).status_code != 401)
check("…and on an exact-path scope", c.get("/api/design/overview", headers=TH).status_code != 401)
check("…but not on any other route", c.get("/api/status", headers=TH).status_code == 401)
check("…and not with another method on its own route", c.post("/api/restaurant/overview", headers=TH).status_code == 401)
check("a made-up token is refused", c.get("/api/restaurant/overview", headers={**H, "authorization": "Bearer dvt_nope"}).status_code == 401)
check("list_tokens shows names and scopes, never a hash or token", "hash" not in json.dumps(hud_auth.list_tokens()) and "system-scan" in hud_auth.list_tokens())
hud_auth.revoke_token("system-scan")
check("a revoked token is refused", c.get("/api/restaurant/overview", headers=TH).status_code == 401)
IH = {**H, **hud_auth.internal_headers()}
check("the internal token reaches the war-room route (an unknown room: 400, not 401)", c.post("/api/warroom", json={"room": "nope"}, headers=IH).status_code == 400)
check("…and the delegation route (an unknown agent: 400, not 401)", c.post("/api/chat_sync", json={"agent": "nope"}, headers=IH).status_code == 400)
check("…but nothing else", c.get("/api/status", headers=IH).status_code == 401 and c.get("/api/audit", headers=IH).status_code == 401)
import tool_executor as te
check("tool_executor sends the internal token on its calls to the HUD", te._hud_auth_headers() == hud_auth.internal_headers())

# ── 6b. login switched off ───────────────────────────────────────────────────────────────────────────────────────────
print("\n── 6. DAVID_AUTH=off ──")
async def _app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []}); await send({"type": "http.response.body", "body": b"ok"})
def call(mw, server_host):
    out = {}
    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
    async def receive():
        return {"type": "http.request", "body": b""}
    asyncio.run(mw({"type": "http", "path": "/api/status", "method": "GET", "headers": [], "server": (server_host, 8000)}, receive, send))
    return out.get("status")
with mock.patch.dict(os.environ, {"DAVID_AUTH": "off"}):
    mw = hud_auth.AuthMiddleware(_app)
    check("off on a loopback bind: requests pass without a session", call(mw, "127.0.0.1") == 200 and call(mw, "localhost") == 200)
    check("off on any other interface: refused (503)", call(mw, "192.168.1.20") == 503)
    check("off: the full app answers without login", client().get("/api/status", headers=H).status_code == 200)

# ── 7. audit hygiene ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 7. audit lines ──")
blob = json.dumps(AUDIT)
check("auth_ok, auth_failed, auth_locked and auth_logout are audited", {"auth_ok", "auth_failed", "auth_locked", "auth_logout"} <= {a.get("action") for a in AUDIT})
check("no audit line holds the password, a session id or a token", PASSWORD not in blob and sid not in blob and tok not in blob and hud_auth.INTERNAL_TOKEN not in blob)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
