"""design_com.py -- how David runs ONE fixed script inside a Windows Adobe app (Photoshop, Illustrator, InDesign), shared by every operation module.

A small worker process (this file with the word "worker") connects through Windows COM and runs the script, so a stuck dialog can be killed on a timeout without taking the HUD
with it. An app that was not running is closed again afterwards (the call waits until its process has really gone, so the next job never meets a half-closed app); an app that
was already running (the designer's own session) is never closed. A connection that dies exactly because the app was still closing is retried once.
InDesign is reached by its own stable-build CLSID, never by a ProgID (the ProgIDs belong to the beta installs).
"""
import os
import sys
import json
import time
import subprocess

APPS = {
    "photoshop": {"label": "Photoshop", "com": "Photoshop.Application", "proc": "Photoshop.exe", "method": "DoJavaScriptFile"},
    "illustrator": {"label": "Illustrator", "com": "Illustrator.Application", "proc": "Illustrator.exe", "method": "DoJavaScriptFile"},
    "indesign": {"label": "InDesign", "com": "{45112D7C-822C-400A-BA8C-D682F7FD0B8E}", "proc": "InDesign.exe", "method": "DoScript",
                 "exe": r"C:\Program Files\Adobe\Adobe InDesign 2026\InDesign.exe"},
}
# Discovered builds and COM ids replace the 2026 literals above when found (adobe_install.py, 2026-10-22); the literals are the fallback.
import adobe_install  # noqa: E402
for _app, _spec in APPS.items():
    _spec["com"] = adobe_install.com_ids(_app, fallback=[_spec["com"]])[0]
    if "exe" in _spec:
        _spec["exe"] = adobe_install.exe_path(_app, _spec["exe"])
        _spec["proc"] = os.path.basename(_spec["exe"])
RETRY_SIGNS = ("-2147417848", "disconnected from its clients", "-2147023174", "-2147023170", "Server execution failed", "-2146959355")
EXIT_WAIT = 90
WORKER_TIMEOUT = 420


class AppError(Exception):
    pass


def running(app):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {APPS[app]['proc']}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return APPS[app]["proc"].lower() in out.lower()


def wait_gone(app, seconds=EXIT_WAIT):
    end = time.time() + seconds
    while time.time() < end and running(app):
        time.sleep(1.0)
    return not running(app)


def server_path(app):
    spec = APPS[app]
    if not spec["com"].startswith("{"):
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\{spec['com']}\LocalServer32") as k:
            return winreg.QueryValue(k, None).strip('"')
    except OSError:
        return ""


def check_target(app):
    spec = APPS[app]
    want = spec.get("exe")
    got = server_path(app) if want else None
    if want and (got or "").lower() != want.lower():
        raise AppError(f"{spec['label']}: Windows points the connection at '{got or 'nothing'}' instead of the stable program '{want}' (a beta install may have changed it); nothing was run")


def _worker(app, jsx_path, quit_after):
    import win32com.client
    spec = APPS[app]
    check_target(app)
    obj = win32com.client.Dispatch(spec["com"])
    if spec.get("exe") and not running(app):
        raise AppError(f"{spec['label']}: the connection opened a program other than the stable one; nothing was run")
    if spec["method"] == "DoScript":
        obj.DoScript(jsx_path, 1246973031)           # ScriptLanguage.JAVASCRIPT
    else:
        obj.DoJavaScriptFile(jsx_path)
    if quit_after:
        try:
            obj.Quit(1852776480) if app == "indesign" else obj.Quit()      # InDesign: SaveOptions.NO
        except Exception:
            pass


def run_script(app, jsx_text, workdir, result_path, timeout=WORKER_TIMEOUT):
    """Runs the script in the app and returns the result the script wrote. Raises AppError with a sentence when it cannot."""
    label = APPS[app]["label"]
    os.makedirs(workdir, exist_ok=True)
    jsx_path = os.path.join(workdir, f"{app}_job.jsx")
    open(jsx_path, "w", encoding="utf-8").write(jsx_text)
    last = None
    check_target(app)
    for attempt in (1, 2):
        if os.path.exists(result_path):
            os.remove(result_path)
        was = running(app)
        cmd = [sys.executable, os.path.abspath(__file__), "worker", app, jsx_path] + ([] if was else ["--quit"])
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            raise AppError(f"{label} did not finish within {timeout // 60} minutes (a dialog may be waiting in the app); the job was stopped")
        if not was:
            wait_gone(app)
        if p.returncode == 0 and os.path.isfile(result_path):
            try:
                return json.load(open(result_path, encoding="utf-8-sig"))
            except ValueError as e:
                raise AppError(f"{label}'s answer could not be read: {e}")
        text = (err or b"").decode("utf-8", "replace").strip()
        last = (text.splitlines()[-1:] or ["no message"])[0]
        if attempt == 1 and any(sig in text for sig in RETRY_SIGNS):
            wait_gone(app)
            continue
        break
    raise AppError(f"{label} could not run the script: " + last[:300])


def check_errors(res, label):
    errs = res.get("errors") or []
    if errs:
        raise AppError(f"{label} reported: " + "; ".join(str(e) for e in errs[:3]))
    return res


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "worker":
        _worker(sys.argv[2], sys.argv[3], "--quit" in sys.argv)
