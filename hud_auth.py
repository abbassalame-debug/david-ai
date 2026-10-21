"""hud_auth.py — the HUD login (2026-10-21, workstream 3). One middleware guards every HTTP route, mount and WebSocket.

Modes (mode()):
  on            a password is set and DAVID_AUTH is not "off": every request needs a session cookie or a scoped token
  off           DAVID_AUTH=off: no login, and only on a loopback bind (a request reaching another interface gets 503)
  unconfigured  no password and DAVID_AUTH unset: the server refuses to start (startup_check), and any request gets 503

Store: <data dir>/auth/hud_auth.json (git-ignored), written atomically:
  password      Argon2id hash (argon2-cffi), set with scripts/set_hud_password.py
  sessions      SHA-256 of each random 256-bit session id -> {created, last_seen}; 12 h idle, 7 days absolute; logout deletes
  tokens        scoped bearer tokens for non-browser callers (scripts/hud_tokens.py): SHA-256 + name + scopes, never the token
  lockout       5 failed logins -> 15 minutes locked
The cookie is david_session: HttpOnly, SameSite=Strict, Path=/. The HUD's calls to itself (the war-room and delegation tools)
use INTERNAL_TOKEN: random per server process, held in memory only, scoped to those two routes.
Audit lines (auth_ok, auth_failed, auth_locked, auth_logout, auth_refused) never contain a password, cookie or token.
"""
from david_config import DAVID_DATA
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie

AUTH_DIR = str(DAVID_DATA / "auth")              # inside DAVID_DATA (git-ignored), next to the index and the audit log
AUTH_FILE = os.path.join(AUTH_DIR, "hud_auth.json")

COOKIE = "david_session"
IDLE_SECONDS = 12 * 3600
ABSOLUTE_SECONDS = 7 * 24 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
LAST_SEEN_WRITE_EVERY = 60            # seconds: how stale last_seen may get on disk before it is rewritten

OPEN_PATHS = {"/login", "/healthz", "/favicon.svg", "/icons.svg"}
OPEN_PREFIXES = ("/api/auth/", "/assets/")
# "testserver" is the fixed placeholder Starlette's TestClient reports for WebSockets; it never resolves, so it can never be a
# real bound interface (a real server reports the address the client connected to, e.g. 127.0.0.1 or 192.168.x.x).
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testserver"}

INTERNAL_TOKEN = secrets.token_urlsafe(32)
INTERNAL_SCOPES = ("POST /api/warroom", "POST /api/chat_sync")

AUDIT = None                          # set by the server: a function taking one dict (david_router.audit via write_audit_log)
_lock = threading.Lock()


def now():
    return time.time()


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _audit(action, **fields):
    if AUDIT:
        try:
            AUDIT({"action": action, **fields})
        except Exception:
            pass


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _load():
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(AUTH_DIR, exist_ok=True)
    tmp = AUTH_FILE + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, AUTH_FILE)


def mode():
    if os.environ.get("DAVID_AUTH", "").strip().lower() == "off":
        return "off"
    return "on" if _load().get("password") else "unconfigured"


def startup_check():
    """Called when the HUD starts: refuse to run without a password unless the login was switched off on purpose."""
    if mode() == "unconfigured":
        raise RuntimeError("HUD login: no password is set. Set one with `py -3.11 scripts\\set_hud_password.py`, "
                           "or start with DAVID_AUTH=off (only allowed on 127.0.0.1).")


# ── password and lockout ─────────────────────────────────────────────────────────────────────────────────────────────────

def set_password(password: str) -> None:
    """Stores an Argon2id hash and signs every session out (a new password ends old sessions)."""
    from argon2 import PasswordHasher
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("the password must have at least 12 characters")
    with _lock:
        data = _load()
        data["password"] = PasswordHasher().hash(password)
        data["sessions"] = {}
        data["failures"], data["locked_until"] = 0, 0
        _save(data)
    _audit("auth_password_set")


def login(password: str):
    """Returns (status, session_id). status: "ok", "failed", "locked" or "unconfigured"."""
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError, InvalidHashError
    with _lock:
        data = _load()
        if not data.get("password"):
            return "unconfigured", None
        t = now()
        if data.get("locked_until", 0) > t:
            _audit("auth_locked", seconds_left=int(data["locked_until"] - t))
            return "locked", None
        ph = PasswordHasher()
        try:
            ph.verify(data["password"], password if isinstance(password, str) else "")
        except (VerificationError, InvalidHashError):
            data["failures"] = data.get("failures", 0) + 1
            if data["failures"] >= MAX_FAILURES:
                data["failures"], data["locked_until"] = 0, t + LOCK_SECONDS
                _save(data)
                _audit("auth_locked", seconds_left=LOCK_SECONDS)
                return "locked", None
            _save(data)
            _audit("auth_failed", failures=data["failures"])
            return "failed", None
        if ph.check_needs_rehash(data["password"]):
            data["password"] = ph.hash(password)
        sid = secrets.token_urlsafe(32)                      # 256 bits
        sessions = {k: v for k, v in data.get("sessions", {}).items() if _alive(v, t)}
        sessions[_sha(sid)] = {"created": t, "last_seen": t}
        data["sessions"], data["failures"], data["locked_until"] = sessions, 0, 0
        _save(data)
    _audit("auth_ok")
    return "ok", sid


# ── sessions ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _alive(s, t):
    return t - s.get("created", 0) < ABSOLUTE_SECONDS and t - s.get("last_seen", 0) < IDLE_SECONDS


def session_valid(sid) -> bool:
    if not sid:
        return False
    key, t = _sha(sid), now()
    with _lock:
        data = _load()
        s = data.get("sessions", {}).get(key)
        if not s:
            return False
        if not _alive(s, t):
            del data["sessions"][key]
            _save(data)
            return False
        if t - s.get("last_seen", 0) > LAST_SEEN_WRITE_EVERY:
            s["last_seen"] = t
            _save(data)
    return True


def logout(sid) -> None:
    if not sid:
        return
    with _lock:
        data = _load()
        if data.get("sessions", {}).pop(_sha(sid), None) is not None:
            _save(data)
    _audit("auth_logout")


def cookie_header(sid) -> str:
    return f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={ABSOLUTE_SECONDS}"


def clear_cookie_header() -> str:
    return f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"


# ── scoped tokens ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _scope_allows(scope: str, method: str, path: str) -> bool:
    """scope "METHOD /path" or "METHOD /prefix/*"; METHOD may be "*"."""
    m, _, p = scope.partition(" ")
    if m != "*" and m.upper() != method.upper():
        return False
    return path.startswith(p[:-1]) if p.endswith("*") else path == p


def create_token(name: str, scopes) -> str:
    """Returns the token ONCE; only its SHA-256 is stored."""
    scopes = [s.strip() for s in scopes if s and s.strip()]
    if not name or not scopes or any(" " not in s for s in scopes):
        raise ValueError("a token needs a name and at least one scope like 'GET /api/restaurant/job'")
    token = "dvt_" + secrets.token_urlsafe(32)
    with _lock:
        data = _load()
        data.setdefault("tokens", {})[name] = {"hash": _sha(token), "scopes": scopes, "created": now()}
        _save(data)
    _audit("auth_token_created", token_name=name, scopes=len(scopes))
    return token


def list_tokens():
    return {n: {"scopes": t["scopes"], "created": t["created"]} for n, t in _load().get("tokens", {}).items()}


def revoke_token(name: str) -> bool:
    with _lock:
        data = _load()
        gone = data.get("tokens", {}).pop(name, None) is not None
        if gone:
            _save(data)
    if gone:
        _audit("auth_token_revoked", token_name=name)
    return gone


def token_allows(token: str, method: str, path: str):
    """The name of the token if it is valid AND scoped to this request, else None."""
    if not token:
        return None
    if hmac.compare_digest(token, INTERNAL_TOKEN):
        return "internal" if any(_scope_allows(s, method, path) for s in INTERNAL_SCOPES) else None
    h = _sha(token)
    for name, t in _load().get("tokens", {}).items():
        if hmac.compare_digest(h, t.get("hash", "")):
            return name if any(_scope_allows(s, method, path) for s in t.get("scopes", [])) else None
    return None


def internal_headers() -> dict:
    """For the HUD calling itself (tool_executor's war-room and delegation tools)."""
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}"}


# ── the middleware ───────────────────────────────────────────────────────────────────────────────────────────────────────

def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


_LOGIN_REDIRECT = (b"<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=\"0;url=/login\">"
                   b"<title>Login required</title><p>Login required. <a href=\"/login\">Go to the login page</a>.</p>")


class AuthMiddleware:
    """Pure ASGI, so it sees HTTP, WebSocket and mounted apps alike. Unauthenticated: HTTP 401 (JSON for /api, a page that
    forwards to /login otherwise); WebSocket closed with 1008 before it is accepted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        m = mode()
        if m == "off":
            host = (scope.get("server") or ("",))[0]
            if host and host not in LOOPBACK:
                return await self._deny(scope, receive, send, 503, "HUD login is off: only allowed on 127.0.0.1")
            return await self.app(scope, receive, send)
        if m == "unconfigured" and path != "/healthz":
            return await self._deny(scope, receive, send, 503, "HUD login: no password is set")
        if is_open(path):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        method = scope.get("method", "GET") if kind == "http" else "WS"
        auth = headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            name = token_allows(auth[7:].strip(), method, path)
            if name:
                scope.setdefault("state", {})["auth"] = f"token:{name}"
                return await self.app(scope, receive, send)
            _audit("auth_refused", reason="token not valid for this route", path=path[:80])
            return await self._deny(scope, receive, send, 401, "token not valid for this route")
        sid = None
        if headers.get("cookie"):
            c = SimpleCookie()
            try:
                c.load(headers["cookie"])
                sid = c[COOKIE].value if COOKIE in c else None
            except Exception:
                sid = None
        if session_valid(sid):
            scope.setdefault("state", {})["auth"] = "session"
            return await self.app(scope, receive, send)
        return await self._deny(scope, receive, send, 401, "login required")

    async def _deny(self, scope, receive, send, status, message):
        if scope["type"] == "websocket":
            await receive()                                   # the client's websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": message[:120]})
            return
        path = scope.get("path", "")
        accept = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}.get("accept", "")
        if not path.startswith("/api/") and "text/html" in accept and status == 401:
            body, ctype = _LOGIN_REDIRECT, b"text/html; charset=utf-8"
        else:
            body, ctype = json.dumps({"ok": False, "error": message}).encode(), b"application/json"
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
