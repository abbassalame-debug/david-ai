"""test_adobe_install.py — Adobe version discovery with stand-in installs (2026-10-22, fix/adobe-version-discovery).

  A  the newest STABLE yearly build whose program file exists is chosen; betas are listed, never chosen; a folder without its program
     file is skipped; DAVID_ADOBE_<APP>_EXE wins; nothing installed -> None and the caller's fallback;
  B  the COM id is the registered one that starts exactly the chosen program file (the InDesign case: ProgIDs owned by betas);
  C  design_handshake, design_com and adobe_bridge take the discovered build and report which one they used.

No Adobe app is started: the installs are empty stand-in files in a temp folder and the registry is a dictionary.
"""
import importlib
import os
import sys
import tempfile
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))


ROOT = tempfile.mkdtemp(prefix="adobe_root_")


def standin(folder, rel):
    p = os.path.join(ROOT, folder, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").close()
    return p


ps26 = standin("Adobe Photoshop 2026", "Photoshop.exe")
ps27 = standin("Adobe Photoshop 2027", "Photoshop.exe")
ai26 = standin("Adobe Illustrator 2026", r"Support Files\Contents\Windows\Illustrator.exe")
os.makedirs(os.path.join(ROOT, "Adobe Illustrator 2027"))                     # an unfinished install: folder, no program file
id26 = standin("Adobe InDesign 2026", "InDesign.exe")
id26b = standin("Adobe InDesign 2026 (Beta)", "InDesign (Beta).exe")
id27b = standin("Adobe InDesign 2027 (Beta)", "InDesign (Beta).exe")
ae26 = standin("Adobe After Effects 2026", r"Support Files\AfterFX.exe")
standin("Adobe Audition 2027", "Adobe Audition.exe")                           # another app's folder is ignored

os.environ["ADOBE_ROOT"] = ROOT
for k in list(os.environ):
    if k.startswith("DAVID_ADOBE_"):
        del os.environ[k]
import adobe_install as A

print("\n── A. which build ──")
c = A.choose("photoshop")
check("the newest stable year is chosen (2027 over 2026)", c["exe"] == ps27 and c["year"] == 2027, c["exe"])
c = A.choose("illustrator")
check("a newer folder without its program file is skipped", c["exe"] == ai26 and c["year"] == 2026, c["exe"])
c = A.choose("indesign")
check("a newer beta is never chosen; the stable 2026 is", c["exe"] == id26, c["exe"])
check("the betas beside it are reported", sorted(c["betas_beside"]) == ["Adobe InDesign 2026 (Beta)", "Adobe InDesign 2027 (Beta)"], str(c["betas_beside"]))
check("the beta's own program name is used for the listing", any(d["exe"] == id27b and d["exists"] for d in A.installs("indesign")))
c = A.choose("premiere")
check("an app that is not installed gives no program file", c["exe"] is None and c["source"] == "not installed")
check("…and exe_path returns the caller's fallback", A.exe_path("premiere", "FALLBACK") == "FALLBACK")
with mock.patch.dict(os.environ, {"DAVID_ADOBE_PHOTOSHOP_EXE": ps26}):
    c = A.choose("photoshop")
check("DAVID_ADOBE_<APP>_EXE pins a build by hand and wins", c["exe"] == ps26 and c["source"] == "DAVID_ADOBE_PHOTOSHOP_EXE")
check("the choice says how it was made", A.choose("photoshop")["source"].startswith("newest stable install (Adobe Photoshop 2027)"))
check("older stable installs are listed", A.choose("photoshop")["older_stable"] == ["Adobe Photoshop 2026"])
check("a stand-in without a version resource reports version None (no crash)", A.choose("aftereffects")["version"] is None)
with mock.patch.dict(os.environ, {"ADOBE_ROOT": os.path.join(ROOT, "missing")}):
    check("a missing install root means nothing installed (no crash)", A.choose("photoshop")["exe"] is None)

print("\n── B. COM ids ──")
STABLE_ID = "{45112D7C-822C-400A-BA8C-D682F7FD0B8E}"
REG = {
    r"InDesign.Application\CLSID": "{BETA-2027}",
    r"InDesign.Application.2027\CLSID": "{BETA-2027}",
    r"InDesign.Application.2026\CLSID": "{BETA-2026}",
    r"CLSID\{BETA-2027}\LocalServer32": f'"{id27b}"',
    r"CLSID\{BETA-2026}\LocalServer32": id26b,
    rf"CLSID\{STABLE_ID}\LocalServer32": id26,
    r"Photoshop.Application\CLSID": "{PS}",
    r"CLSID\{PS}\LocalServer32": f"{ps27} /Automation",
}
reg = REG.get
cid, how = A.com_id("indesign", reg=reg)
check("InDesign: the ProgIDs start a beta, so the stable build's own CLSID is used", cid == STABLE_ID, how)
cid, how = A.com_id("photoshop", reg=reg)
check("Photoshop: the ProgID is used when it starts the chosen build (arguments like /Automation ignored)", cid == "Photoshop.Application", how)
cid, how = A.com_id("photoshop", exe=ps26, reg=reg)
check("a ProgID that starts another build is not used", cid is None, how)
with mock.patch.object(A, "_reg_default", reg):
    check("com_ids falls back to the old literal list when nothing registered matches", A.com_ids("illustrator", fallback=["Illustrator.Application"]) == ["Illustrator.Application"])
    check("com_ids gives the matching id", A.com_ids("indesign", fallback=["x"]) == [STABLE_ID])

print("\n── C. the bridges use it ──")
with mock.patch.object(A, "_reg_default", reg):
    import design_handshake as H
    H = importlib.reload(H)
    check("design_handshake uses the discovered Photoshop 2027", H.EXES["photoshop"] == ps27, H.EXES["photoshop"])
    check("…the stable InDesign, not a beta", H.EXES["indesign"] == id26 and H.COM_IDS["indesign"] == [STABLE_ID])
    check("…and the 2026 literal for an app discovery cannot find (Premiere)", H.EXES["premiere"] == H._FALLBACK_EXES["premiere"])
    check("the process name follows the chosen program file", H.PROCS["indesign"] == "InDesign.exe")
    with mock.patch.object(H, "run_scripted_app", lambda app, d, report, keep_open: report.update(connected=True)):
        rep = H.handshake("photoshop", out_dir=tempfile.mkdtemp(prefix="hs_"))
    bu = rep.get("build_used") or {}
    check("the handshake report says which build it used and how", bu.get("exe") == ps27 and bu.get("year") == 2027 and "newest stable" in bu.get("how", ""), str(bu)[:160])
    check("…and the betas it did not use (InDesign)", "Adobe InDesign 2027 (Beta)" in H.CHOSEN["indesign"]["betas_beside"])
    import design_com as C
    C = importlib.reload(C)
    check("design_com reaches InDesign by the discovered id and program file", C.APPS["indesign"]["com"] == STABLE_ID and C.APPS["indesign"]["exe"] == id26)
    import adobe_bridge as B
    B = importlib.reload(B)
    check("adobe_bridge uses the discovered builds", B.APP_CONFIG["photoshop"]["exe"] == ps27 and B.APP_CONFIG["indesign"]["com_ids"] == [STABLE_ID])

print("\n── D. the Premiere panel installer matches its manifest ──")
import re
manifest = open(os.path.join(HERE, "premiere_cep", "CSXS", "manifest.xml"), encoding="utf-8").read()
need = re.search(r'RequiredRuntime Name="CSXS" Version="(\d+)', manifest).group(1)
ps1 = open(os.path.join(HERE, "premiere_cep", "install.ps1"), encoding="utf-8").read()
vers = re.findall(r'"(\d+)"', re.search(r"foreach \(\$ver in @\(([^)]*)\)\)", ps1).group(1))
check(f"install.ps1 enables debug mode for the CSXS version the manifest requires ({need})", need in vers, f"sets {vers}")

print("\n── E. After Effects: the Arabic frame is rendered with aerender ──")
import subprocess as _sp
H = importlib.reload(importlib.import_module("design_handshake"))
check("the After Effects script saves its scratch project for aerender", "proj.save(new File(AEP))" in H.AE_JSX and "__AEP__" not in H.build_jsx("aftereffects", ROOT, True))
calls = []


def fake_run(cmd, **kw):
    calls.append(cmd[cmd.index("-OMtemplate") + 1])
    if cmd[cmd.index("-OMtemplate") + 1] == "TIFF Sequence with Alpha":          # this version has no PNG template
        open(cmd[cmd.index("-output") + 1].replace("[#####]", "00000") + ".tif", "wb").close()
    return _sp.CompletedProcess(cmd, 0 if calls[-1] != "PNG Sequence" else 1, stdout="aerender ERROR: no output module template", stderr="")


out = tempfile.mkdtemp(prefix="aer_")
with mock.patch.object(H.subprocess, "run", fake_run):
    pic, how = H.aerender_frame(os.path.join(out, "x.aep"), "hs_comp", out)
check("aerender: the first template that renders is used (PNG missing -> TIFF)", pic and pic.endswith(".tif") and "TIFF Sequence with Alpha" in how and calls[:2] == ["PNG Sequence", "TIFF Sequence with Alpha"], f"{how} {calls}")
calls.clear()
with mock.patch.object(H.subprocess, "run", lambda cmd, **kw: (calls.append(1), _sp.CompletedProcess(cmd, 1, stdout="", stderr=""))[1]):
    out2 = tempfile.mkdtemp(prefix="aer2_")
    pic, how = H.aerender_frame(os.path.join(out2, "y.aep"), "hs_comp", out2)
check("aerender: nothing rendered -> no picture and a reason naming every template tried", pic is None and how.count("exit 1") == len(H.AERENDER_TEMPLATES), how)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
