# studio_mode.py — AbletonOSC transport watcher
# Polls the ACTUAL Ableton transport state via OSC (not just process presence).
# Writes studio_mode.lock when transport is playing — route_and_execute() reads this
# to block GPU inference and protect ASIO latency for the RODE interface.
# Run in background: pythonw studio_mode.py
# Requires: AbletonOSC Max for Live device loaded and active in Ableton Live 12.

from david_config import DAVID_DATA, DAVID_HOME, DAVID_VAULTS
import os
import sys
import time
import subprocess
import datetime

sys.path.insert(0, str(DAVID_HOME))

# Use a separate OSC recv port so studio_mode.py and david_ui.py can both run
# without colliding on the same UDP bind. david_ui.py keeps the default 11001.
os.environ.setdefault("DAVID_OSC_RECV_PORT", "11002")

LOCK_FILE = str(DAVID_DATA / "studio_mode.lock")
LOG_FILE  = str(DAVID_VAULTS / "System" / "studio_mode.log")
POLL_SEC  = 1   # OSC poll interval in seconds — 1s for tight ASIO protection


def _is_ableton_running() -> bool:
    """Fast pre-check: skip OSC round-trip if Ableton isn't even open."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode(errors="ignore")
        return "ableton live" in out.lower()
    except Exception:
        return False


def _transport_playing() -> bool:
    """Query AbletonOSC for transport state. Returns False if OSC is unreachable."""
    try:
        import ableton_bridge
        return ableton_bridge.is_playing()
    except Exception:
        return False


def _log(msg: str) -> None:
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _set_lock(active: bool) -> None:
    if active:
        if not os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "w", encoding="utf-8") as f:
                f.write(datetime.datetime.now().isoformat())
            _log("STUDIO LOCK ON  — Ableton transport active, GPU inference blocked.")
    else:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            _log("STUDIO LOCK OFF — transport stopped, GPU inference restored.")


def main():
    # Clear any stale lock from a previous crash
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
        _log("Cleared stale lock on startup.")

    _log("Studio mode watcher started (AbletonOSC transport polling).")

    while True:
        try:
            if not _is_ableton_running():
                _set_lock(False)
            else:
                _set_lock(_transport_playing())
        except Exception as e:
            _log(f"Error in poll loop: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _set_lock(False)
        print("\nStudio mode watcher stopped.")
