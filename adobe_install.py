"""adobe_install.py — which Adobe apps are installed, and which build the bridges should use (2026-10-22, fix/adobe-version-discovery).

The bridges used to hard-code "C:\\Program Files\\Adobe\\Adobe Photoshop 2026\\..." and a fixed InDesign CLSID. A yearly update (2027)
would have left them pointing at a folder that no longer exists, or at an old build. This module looks instead:

  * the install root (C:\\Program Files\\Adobe, or ADOBE_ROOT for tests) holds one folder per build: "Adobe Photoshop 2026",
    "Adobe InDesign 2027 (Beta)", ...; the newest stable year whose program file exists is used, betas are listed but never chosen;
  * DAVID_ADOBE_<APP>_EXE (e.g. DAVID_ADOBE_INDESIGN_EXE) pins a program file by hand and wins over discovery;
  * the COM id is the registered one whose LocalServer32 starts exactly that program file. This matters for InDesign: here the
    plain ProgID and both year ProgIDs belong to the beta installs, and only the stable build's own CLSID opens the stable app.

    py -3.11 adobe_install.py          prints what is installed and what each bridge would use
"""
import os
import re
import sys

ADOBE_ROOT_DEFAULT = r"C:\Program Files\Adobe"

APPS = {
    "photoshop":    {"folder": "Adobe Photoshop",    "exe": "Photoshop.exe",                                      "beta_exe": "Photoshop.exe",
                     "progids": ["Photoshop.Application"]},
    "illustrator":  {"folder": "Adobe Illustrator",  "exe": r"Support Files\Contents\Windows\Illustrator.exe",     "beta_exe": r"Support Files\Contents\Windows\Illustrator.exe",
                     "progids": ["Illustrator.Application"]},
    "indesign":     {"folder": "Adobe InDesign",     "exe": "InDesign.exe",                                       "beta_exe": "InDesign (Beta).exe",
                     "progids": ["InDesign.Application"], "known_clsids": ["{45112D7C-822C-400A-BA8C-D682F7FD0B8E}"]},
    "aftereffects": {"folder": "Adobe After Effects", "exe": r"Support Files\AfterFX.exe",                          "beta_exe": r"Support Files\AfterFX (Beta).exe",
                     "progids": []},
    "premiere":     {"folder": "Adobe Premiere Pro", "exe": "Adobe Premiere Pro.exe",                             "beta_exe": "Adobe Premiere Pro (Beta).exe",
                     "progids": []},
}
FOLDER_RE = re.compile(r"^(?P<name>.+?) (?P<year>20\d\d)(?P<beta> \(Beta\))?$")


def root():
    return os.environ.get("ADOBE_ROOT") or ADOBE_ROOT_DEFAULT


def file_version(path):
    """The program file's version ("27.11.0.26"), or None when it has no version resource or pywin32 is missing."""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def installs(app, base=None):
    """Every build of `app` under the install root, newest first: [{year, beta, folder, exe, exists}]."""
    spec = APPS[app]
    base = base or root()
    out = []
    try:
        names = os.listdir(base)
    except OSError:
        return out
    for name in names:
        m = FOLDER_RE.match(name)
        if not m or m.group("name") != spec["folder"]:
            continue
        beta = bool(m.group("beta"))
        exe = os.path.join(base, name, spec["beta_exe"] if beta else spec["exe"])
        out.append({"year": int(m.group("year")), "beta": beta, "folder": name, "exe": exe, "exists": os.path.isfile(exe)})
    out.sort(key=lambda d: (d["year"], not d["beta"]), reverse=True)
    return out


def choose(app, base=None):
    """The build the bridges use: a DAVID_ADOBE_<APP>_EXE pin, else the newest stable year whose program file exists.
    Returns {app, exe, year, version, source, betas_beside, newer_stable_ignored} (exe None when nothing is installed)."""
    found = installs(app, base)
    pin = os.environ.get(f"DAVID_ADOBE_{app.upper()}_EXE")
    betas = [d["folder"] for d in found if d["beta"]]
    if pin:
        return {"app": app, "exe": pin, "year": None, "version": file_version(pin), "source": f"DAVID_ADOBE_{app.upper()}_EXE",
                "betas_beside": betas, "exists": os.path.isfile(pin)}
    stable = [d for d in found if not d["beta"] and d["exists"]]
    if not stable:
        return {"app": app, "exe": None, "year": None, "version": None, "source": "not installed", "betas_beside": betas, "exists": False}
    best = stable[0]
    return {"app": app, "exe": best["exe"], "year": best["year"], "version": file_version(best["exe"]),
            "source": f"newest stable install ({best['folder']})", "betas_beside": betas, "exists": True,
            "older_stable": [d["folder"] for d in stable[1:]]}


def exe_path(app, fallback=None):
    """The chosen program file, or `fallback` when the app is not installed (callers keep their old literal as the fallback)."""
    return choose(app).get("exe") or fallback


# ── COM ids ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _reg_default(path):
    """The default value of HKEY_CLASSES_ROOT\\<path>, or None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path) as k:
            return winreg.QueryValueEx(k, "")[0]
    except Exception:
        return None


def _server_exe(clsid, reg=None):
    reg = reg or _reg_default
    cmd = reg(rf"CLSID\{clsid}\LocalServer32")
    if not cmd:
        return None
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    m = re.match(r"(.+?\.exe)\b", cmd, re.I)
    return m.group(1) if m else cmd


def com_id(app, exe=None, reg=None, base=None):
    """The COM id that starts exactly `exe` (default: the chosen build). Candidates: the app's ProgIDs, its year ProgIDs for every
    installed year, and its known CLSIDs. Returns (com_id, how) or (None, why) when no registered id starts that program file."""
    spec = APPS[app]
    reg = reg or _reg_default
    exe = exe or choose(app, base).get("exe")
    if not exe:
        return None, "not installed"
    want = os.path.normcase(os.path.normpath(exe))
    years = sorted({d["year"] for d in installs(app, base)}, reverse=True)
    progids = list(spec["progids"]) + [f"{p}.{y}" for p in spec["progids"] for y in years]
    candidates = [(p, reg(rf"{p}\CLSID")) for p in progids] + [(c, c) for c in spec.get("known_clsids", [])]
    seen = set()
    for name, clsid in candidates:
        if not clsid or clsid in seen:
            continue
        seen.add(clsid)
        srv = _server_exe(clsid, reg)
        if srv and os.path.normcase(os.path.normpath(srv)) == want:
            return (name if not name.startswith("{") else clsid), f"{name} -> {clsid} starts {exe}"
    return None, f"no registered COM id starts {exe}"


def com_ids(app, fallback=None):
    """[com_id] for the bridges' Dispatch lists, falling back to the old literal list when nothing registered matches."""
    cid, _ = com_id(app)
    return [cid] if cid else list(fallback or [])


def report():
    rows = []
    for app in APPS:
        c = choose(app)
        cid, how = com_id(app) if APPS[app]["progids"] or APPS[app].get("known_clsids") else (None, "no COM (driven another way)")
        rows.append({**c, "com_id": cid, "com_how": how})
    return rows


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for r in report():
        print(f"{r['app']:<13} {str(r['version']):<14} {r['source']}")
        print(f"{'':13} exe: {r['exe']}")
        if r["betas_beside"]:
            print(f"{'':13} betas installed beside it (never used): {', '.join(r['betas_beside'])}")
        print(f"{'':13} COM: {r['com_id'] or '-'} ({r['com_how']})")
