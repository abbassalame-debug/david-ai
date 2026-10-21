"""
test_server_security.py — HUD perimeter tests (FastAPI TestClient).

Covers the Phase-1 findings that need the real app wired up:
  F1  cross-origin WebSocket handshakes are rejected on /ws/chat and /ws/taskrun
  F2  TrustedHostMiddleware rejects a foreign Host header (DNS-rebinding guard)

Importing ui/server.py pulls in psutil, the frontend build mount, and the
david_router/memory chain. If any of that isn't available in the current
environment, the whole file SKIPS rather than fails — it is meant to run
green on the machine David actually runs on.

The app is used WITHOUT the TestClient context manager on purpose, so the
startup task (_vault_watcher) and the shutdown handler (memory.save_episode →
Ollama + LanceDB) never fire.

Run: python test_server_security.py
"""

from david_config import DAVID_HOME
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
SKIP = "\033[93m  SKIP\033[0m"

results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

try:
    from starlette.testclient import TestClient
    import server as srv
except Exception as e:
    print(f"{SKIP}  server security tests skipped — cannot import app: {type(e).__name__}: {e}")
    sys.exit(0)

app = srv.app

print("\n── F2: TrustedHostMiddleware (DNS-rebinding guard) ──")
# Foreign Host header (what a rebinding page's request carries) → 400.
bad = TestClient(app, base_url="http://evil.example")
resp = bad.get("/api/status")
check("Foreign Host header is rejected with 400", resp.status_code == 400,
      f"got {resp.status_code}")

# Our own localhost Host → allowed (status endpoint is side-effect-free).
good = TestClient(app, base_url="http://localhost:8000")
resp = good.get("/api/status")
check("localhost Host header is accepted", resp.status_code == 200,
      f"got {resp.status_code}")

print("\n── F1: WebSocket same-origin gate ──")

# Real browsers always send Host on the WS upgrade; TestClient does not
# auto-populate it, so send it explicitly (else TrustedHostMiddleware, not the
# origin gate, is what rejects the connection).
_HOST = {"host": "localhost:8000"}

def ws_denied(path, origin):
    """True if the handshake is refused for this Origin."""
    try:
        with good.websocket_connect(path, headers={**_HOST, "origin": origin}):
            return False
    except Exception:
        return True

def ws_allowed(path, headers):
    """True if the handshake is accepted (then immediately closed)."""
    try:
        with good.websocket_connect(path, headers={**_HOST, **headers}):
            return True
    except Exception:
        return False

check("/ws/chat rejects a cross-origin handshake",
      ws_denied("/ws/chat", "http://evil.example"))
check("/ws/chat accepts a same-origin handshake",
      ws_allowed("/ws/chat", {"origin": "http://localhost:8000"}))
check("/ws/taskrun rejects a cross-origin handshake",
      ws_denied("/ws/taskrun", "http://evil.example"))
check("/ws/chat accepts a handshake with absent Origin (non-browser caller)",
      ws_allowed("/ws/chat", {}))

print("\n── Summary ──")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
