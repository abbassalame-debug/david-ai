"""
test_design_photoshop_compose.py -- tests for photoshop.compose_card (a layered PSD: the picture on its own layer + one live text layer), proven with a FAKE Photoshop (no Adobe app is opened).
The script text, plans, yes gate, copy, staging and delivery are the real code. Run: python test_design_photoshop_compose.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, re, json, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image
import design_photoshop as P
import design_indesign as ID
import design_ops as O
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dpsc_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PIC = os.path.join(SRC, "background.png")
Image.new("RGB", (1024, 1024), (20, 90, 60)).save(PIC)
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
P._prefs = lambda: PROFILE
ID._prefs = lambda: PROFILE
O.register_builtin()
AR = "\u0634\u0627\u0648\u0631\u0645\u0627 \u062f\u064a\u0645\u0648"

print("-- the script --")
kw = dict(out=r"C:\out dir\card.psd", png=r"C:\out dir\p.png", imgs=[r"C:\in dir\bg 'x'.png"], names=["Background picture"], text=AR + ' "q"', fams=["Sakkal Majalla"], size=150.0, color=[242, 201, 76], position="top",
          width_px=1080, height_px=1080, dpi=72)
j = P.build_compose_jsx(r"C:\out dir\r.json", **kw)
check("every path and value is an escaped literal (plain ASCII, spaces, quotes, backslashes); no placeholder is left", j.isascii() and not re.search(r"__[A-Z0-9]+__", j))
check("the picture is copied INTO the new document from the copy (never the original); the source picture is closed without saving; the new document is closed without saving again; dialogs and units are put back",
      "app.open(new File(IMGS[n]))" in j and "src.close(SaveOptions.DONOTSAVECHANGES)" in j and "doc.close(SaveOptions.DONOTSAVECHANGES)" in j and "app.displayDialogs=prevDialogs" in j and "app.preferences.rulerUnits=oldRuler" in j)
check("the canvas starts transparent (no locked Background layer); the picture layer is named 'Background picture' and covers the canvas evenly (one scale factor, never stretched); the title is a live text layer",
      "DocumentFill.TRANSPARENT" in j and "lyr.name=NAMES[n]" in j and '"Background picture"' in j and "Math.max(DW/sw,DH/sh)" in j and "src.resizeImage(" in j and "src.resizeCanvas(" in j and "LayerKind.TEXT" in j and 't.name="Title"' in j and "rasterize" not in j.lower())
check("the text is centred paragraph text in a box 88 % of the width, the font is looked up by family and set by its PostScript name; the file is saved as a layered PSD and a PNG is exported",
      "TextType.PARAGRAPHTEXT" in j and "Justification.CENTER" in j and "f.family===FAMS[i]" in j and "postScriptName" in j and "PhotoshopSaveOptions" in j and "SAVEFORWEB" in j)
check("every value is where it should be in the text of the script", "DW=1080" in j and "DH=1080" in j and "DPI=72" in j and "SIZE=150.0" in j and "COLOR=[242, 201, 76]" in j and 'POS="top"' in j)

print("\n-- planning --")
PID = R.create_project("Acme", "Spring", "2026-10-10", root=ROOT)
base = {"picture": PIC, "text": AR, "preset": "square-1080", "name": "Sign Test"}
pl = lambda **prm: _refused(R.plan_job, PID, "photoshop.compose_card", {**base, **prm}, root=ROOT)


def _refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


open(os.path.join(SRC, "x.psd"), "w").write("x")
check("refusals: wrong picture type, empty text, text over 200 characters, bad colour, unknown preset, bad position",
      "must be" in (pl(picture=os.path.join(SRC, "x.psd")) or "") and pl(text="  ") and pl(text="a" * 201) and pl(color="gold") and pl(preset="nope") and pl(position="middle"))
pn = R.plan_job(PID, "photoshop.compose_card", base, root=ROOT)
check("the output follows the naming standard: SignTest_PsCard_v01.psd", pn["outputs"] == ["SignTest_PsCard_v01.psd"], pn["outputs"])
steps = " | ".join(pn["steps"])
check("the plan names the preset and pixel size, says the picture and the text are on their own layers, nothing is locked, Arabic joins and runs right to left, and the original is never opened",
      "1080 x 1080 px" in steps and "'Background picture'" in steps and "live text layer" in steps and "nothing is locked" in steps and "right to left" in steps and "never opened" in steps, steps[:300])
check("for Latin text the plan does not mention right to left", "right to left" not in " | ".join(R.plan_job(PID, "photoshop.compose_card", {**base, "text": "Hello"}, root=ROOT)["steps"]))

print("\n-- end to end with a fake Photoshop --")
CALLS = []
MODE = {"equal": True, "layers": ["Title [text]", "Background picture"], "text_h": 200, "fail": False}


def fake_ps(jsx_text, workdir, result_path, timeout=None):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    if MODE["fail"]:
        res = {"steps": {"compose": {"error": "the picture could not be opened"}}, "errors": ["compose: open"], "app": "photoshop"}
    else:
        open(q("OUT"), "wb").write(b"8BPS fake psd " + b"x" * 300)
        Image.new("RGB", (1080, 1080), (30, 100, 70)).save(q("PNG"))
        res = {"steps": {"compose": {"canvas_px": [1080, 1080], "picture_scale_percent": 105.5, "font": "Sakkal Majalla (SakkalMajalla)", "read_back_equals_sent": MODE["equal"],
                                     "text_height_px": MODE["text_h"], "frame_height_px": 390, "layers": MODE["layers"]}}, "errors": [], "app": "photoshop"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


P.TRANSPORT = fake_ps


def approved(**prm):
    pln = R.plan_job(PID, "photoshop.compose_card", {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


sha0 = R.sha256_file(PIC)
res = R.run_job(PID, approved(), ROOT)
psd = os.path.join(ROOT, PID, "05_Exports", "SignTest_PsCard_v01.psd")
check("the layered PSD is delivered to 05_Exports with a page picture in 04_Previews; Photoshop was handed the COPY in 02_Assets, never the original; the original is unchanged",
      res["ok"] and os.path.isfile(psd) and res["preview"].startswith("04_Previews/") and json.dumps(os.path.join(ROOT, PID, "02_Assets", "background.png").replace("\\", "/")) in CALLS[-1]
      and PIC.replace("\\", "/") not in CALLS[-1] and R.sha256_file(PIC) == sha0, str(res))
check("the note names the font, the picture scale and the layers, with no warning when all went well", any("Sakkal Majalla" in n and "105.5" in n and "layers: Title [text], Background picture" in n for n in res["notes"]) and not any("WARNING" in n for n in res["notes"]), res["notes"])
check("the Arabic text reached the script exactly (as an escaped literal)", json.dumps(AR, ensure_ascii=True) in CALLS[-1])
MODE.update(layers=["Title [text]", "Background picture (locked)"])
res = R.run_job(PID, approved(name="Sign Two"), ROOT)
check("a locked layer is reported with a WARNING (the designer wants to move everything)", res["ok"] and any("WARNING" in n and "locked" in n for n in res["notes"]), res["notes"])
MODE.update(layers=["Title [text]", "Background picture"], text_h=900)
res = R.run_job(PID, approved(name="Sign Three"), ROOT)
check("text taller than its box is a WARNING (use a smaller size)", res["ok"] and any("taller than its box" in n for n in res["notes"]), res["notes"])

print("\n-- when it fails, nothing is delivered --")
MODE.update(text_h=200, equal=False)
res = R.run_job(PID, approved(name="Sign Four"), ROOT)
check("if the text Photoshop holds is not the text sent, the job fails and delivers nothing new", not res["ok"] and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "SignFour_PsCard_v01.psd")), str(res)[:200])
MODE.update(equal=True, fail=True)
res = R.run_job(PID, approved(name="Sign Five"), ROOT)
check("if Photoshop reports an error the job fails with its sentence and delivers nothing", not res["ok"] and "could not be opened" in str(res) or "compose: open" in str(res) and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "SignFive_PsCard_v01.psd")), str(res)[:200])
src_text = open(str(DAVID_HOME / "design_photoshop.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "shutil.move" not in src_text and "os.remove(" not in src_text and "ollama" not in src_text.lower())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
