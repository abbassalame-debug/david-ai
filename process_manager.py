# process_manager.py — registry and control for David background daemons

from david_config import DAVID_HOME
import os
import subprocess
import sys
from pathlib import Path

DAVID_DIR = Path(str(DAVID_HOME))

_REGISTRY = {
    "voice_daemon": {
        "script": str(DAVID_DIR / "voice_daemon.py"),
        "description": "XTTS-v2 voice synthesis daemon",
    },
    "voice_inbox": {
        "script": str(DAVID_DIR / "voice_inbox.py"),
        "description": "Voice note transcriber (watches audio files in inbox\\)",
    },
    "file_watcher": {
        "script": str(DAVID_DIR / "file_watcher.py"),
        "description": "File system watcher — triggers ingest on vault changes",
    },
    "david_live": {
        "script": str(DAVID_DIR / "david_live.py"),
        "description": "Live context aggregator (calendar, email, metrics)",
    },
    "turbo_ingest": {
        "script": str(DAVID_DIR / "turbo_ingest.py"),
        "description": "GPU-accelerated knowledge base ingest",
    },
    "morning_briefing": {
        "script": str(DAVID_DIR / "morning_briefing.py"),
        "description": "Scheduled morning briefing generator",
    },
}

# PID store — maps process name → subprocess.Popen. Bounded by len(_REGISTRY);
# _log_handles mirrors it 1:1 so each process's log file can be closed explicitly
# instead of relying on GC to close a handle subprocess.Popen never took ownership of.
_procs: dict[str, subprocess.Popen] = {}
_log_handles: dict[str, "object"] = {}


def _find_external_pid(name: str):
    # _procs only knows children of THIS process. A daemon started by a previous
    # server run (or by hand) is invisible to it — without this fallback,
    # start_process spawns a duplicate and get_status reports "stopped" while
    # the daemon is running. Scan process cmdlines for the registered script.
    script = _REGISTRY[name]["script"]
    try:
        import psutil
    except ImportError:
        return None
    target = os.path.normcase(script)
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any(os.path.normcase(arg) == target for arg in (p.info["cmdline"] or [])):
                return p.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _is_alive(name: str) -> bool:
    proc = _procs.get(name)
    if proc is not None and proc.poll() is None:
        return True
    return _find_external_pid(name) is not None


def _close_stale_log(name: str) -> None:
    fh = _log_handles.pop(name, None)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def start_process(name: str) -> str:
    if name not in _REGISTRY:
        return f"Unknown process '{name}'. Available: {list(_REGISTRY.keys())}"
    if _is_alive(name):
        proc = _procs.get(name)
        pid = proc.pid if proc is not None and proc.poll() is None else _find_external_pid(name)
        return f"{name} is already running (PID {pid})."
    _close_stale_log(name)  # a prior run of this name may have exited without stop_process()
    entry = _REGISTRY[name]
    script = entry["script"]
    if not Path(script).exists():
        return f"Script not found: {script}"
    try:
        log_fh = open(str(DAVID_DIR / f"logs/{name}.log"), "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, script],
            cwd=str(DAVID_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        _procs[name] = proc
        _log_handles[name] = log_fh
        return f"Started {name} (PID {proc.pid})."
    except Exception as e:
        return f"Failed to start {name}: {e}"


def stop_process(name: str) -> str:
    if name not in _REGISTRY:
        return f"Unknown process '{name}'."
    proc = _procs.get(name)
    if proc is None or proc.poll() is not None:
        pid = _find_external_pid(name)
        if pid is None:
            return f"{name} is not running."
        try:
            import psutil
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                p.kill()
            return f"Stopped {name} (external PID {pid})."
        except Exception as e:
            return f"Failed to stop {name}: {e}"
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        del _procs[name]
        _close_stale_log(name)
        return f"Stopped {name}."
    except Exception as e:
        return f"Failed to stop {name}: {e}"


def get_status(name: str) -> dict:
    if name not in _REGISTRY:
        return {"name": name, "status": "unknown", "pid": None}
    proc = _procs.get(name)
    if proc is not None and proc.poll() is None:
        alive, pid = True, proc.pid
    else:
        pid = _find_external_pid(name)
        alive = pid is not None
    return {
        "name": name,
        "description": _REGISTRY[name]["description"],
        "status": "running" if alive else "stopped",
        "pid": pid,
    }


def get_all_status() -> list[dict]:
    return [get_status(name) for name in _REGISTRY]


def ensure_log_dir():
    (DAVID_DIR / "logs").mkdir(exist_ok=True)


ensure_log_dir()
