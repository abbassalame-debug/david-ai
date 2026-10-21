"""ci_env.py — what a test may assume about the machine it runs on (2026-10-21, workstream 1).

On a developer machine every check runs. With DAVID_CI=1 (set by the GitHub Actions workflow) a check that needs something a
hosted runner does not have — a running Ollama, an NVIDIA GPU, Windows COM, an installed Adobe app — is reported as
"SKIP (needs X)" instead of failing, and scripts/run_all_tests.py counts it separately from passes and failures.

    import ci_env
    reason = ci_env.needs("ollama")          # None when available (or when not in CI), else e.g. "ollama"
    if reason:
        ci_env.skip("the router answers a real question", reason)
    else:
        check(...)

Today every suite fakes the model and the apps, so nothing is skipped; this is the switch for the first test that is not.
"""
import os
import shutil
import sys
import urllib.request


def in_ci() -> bool:
    return os.environ.get("DAVID_CI") == "1"


def _ollama_up() -> bool:
    try:
        from david_config import OLLAMA_HOST
        urllib.request.urlopen(OLLAMA_HOST + "/api/version", timeout=2).read()
        return True
    except Exception:
        return False


def _com_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def _adobe_installed(app: str) -> bool:
    """app: photoshop, illustrator, indesign, aftereffects, premiere. Looks for the COM ProgID in the registry."""
    if os.name != "nt":
        return False
    progid = {"photoshop": "Photoshop.Application", "illustrator": "Illustrator.Application", "indesign": "InDesign.Application",
              "aftereffects": "AfterEffects.Application", "premiere": "Premiere.Application"}.get(app.lower())
    if not progid:
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid))
        return True
    except OSError:
        return False


def needs(*what: str):
    """None if every requirement is met or we are not in CI; otherwise the first missing requirement's name.
    Requirements: "ollama", "gpu", "com", "adobe:<app>"."""
    if not in_ci():
        return None
    for w in what:
        ok = (_ollama_up() if w == "ollama" else
              shutil.which("nvidia-smi") is not None if w == "gpu" else
              _com_available() if w == "com" else
              _adobe_installed(w.split(":", 1)[1]) if w.startswith("adobe:") else False)
        if not ok:
            return w
    return None


def skip(name: str, reason: str) -> None:
    """The one line format scripts/run_all_tests.py counts as a skip."""
    print(f"  SKIP (needs {reason})  {name}")
    sys.stdout.flush()
