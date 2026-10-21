"""
test_design_illustrator_compose.py -- tests for illustrator.compose_card (a picture + one line of text on a new document), proven with a FAKE Illustrator (no Adobe app is opened).
The script text, plans, yes gate, copy, staging and delivery are the real code. Run: python test_design_illustrator_compose.py
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
import design_illustrator as I
import design_indesign as ID
import design_ops as O
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="daic_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PIC = os.path.join(SRC, "background.png")
Image.new("RGB", (1024, 1024), (20, 90, 60)).save(PIC)
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
I._prefs = lambda: PROFILE
ID._prefs = lambda: PROFILE
O.register_builtin()
AR = "\u0634\u0627\u0648\u0631\u0645\u0627 \u062f\u064a\u0645\u0648"


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the script and the sizes --")
j = I.build_compose_jsx(r"C:\out dir\r.json", out=r"C:\out dir\\\u0645\u0644\u0641.ai", png=r"C:\out dir\p.png", imgs=[r"C:\in dir\bg 'x'.png"], names=["Background picture"], text=AR + ' "q"', fams=["Sakkal Majalla"], size=120.0,
                        color=[242, 201, 76], position="center", width_pt=1080.0, height_pt=1080.0, arabic=True)
check("every path and value is an escaped literal (plain ASCII, spaces, quotes and backslashes in the path); no placeholder is left", j.isascii() and not re.search(r"__[A-Z0-9]+__", j))
check("the picture is only placed from the copy (new File(IMG)), never opened as a document; the new document is closed without saving again; alerts are switched off and put back",
      "new File(IMGS[n])" in j and "app.open(" not in j and "doc.close(SaveOptions.DONOTSAVECHANGES)" in j and "DONTDISPLAYALERTS" in j and "app.userInteractionLevel=prevUI" in j)
check("the picture covers the page evenly (one scale factor for both sides, so it is never stretched) and is embedded", "Math.max(DW/pi.width,DH/pi.height)" in j and "pi.resize(sc,sc)" in j and "pi.embed()" in j)
check("Arabic text gets the Optyca composer and centred paragraphs; Latin text is told ARABIC=false", "optycaComposer" in j and "Justification.CENTER" in j and "ARABIC=true" in j
      and "ARABIC=false" in I.build_compose_jsx("r.json", out="o.ai", png="p.png", imgs=["b.png"], names=["Background picture"], text="Hello", fams=["Myriad Pro"], size=40.0, color=[0, 0, 0], position="top", width_pt=100.0, height_pt=100.0, arabic=False))
sq = I._pt_dims({"unit": "px", "width": 1080, "height": 1080, "dpi": 72})
a4 = I._pt_dims({"unit": "px", "width": 3508, "height": 2480, "dpi": 300})
mm = I._pt_dims({"unit": "mm", "width": 210, "height": 297, "dpi": 300})
check("sizes in points: 1080 px at 72 dpi is 1080 pt; A4 landscape at 300 dpi is about 842 x 595 pt; A4 portrait in mm is about 595 x 842 pt", sq == (1080.0, 1080.0) and a4 == (841.9, 595.2) and mm == (595.3, 841.9), (sq, a4, mm))

print("\n-- planning --")
PID = R.create_project("Acme", "Spring", "2026-10-10", root=ROOT)
base = {"picture": PIC, "text": AR, "preset": "square-1080", "name": "Sign Test"}
pl = lambda **prm: refused(R.plan_job, PID, "illustrator.compose_card", {**base, **prm}, root=ROOT)
open(os.path.join(SRC, "x.psd"), "w").write("x")
check("refusals: wrong picture type, empty text, text over 200 characters, bad colour, unknown preset, bad position",
      "must be" in (pl(picture=os.path.join(SRC, "x.psd")) or "") and pl(text="  ") and pl(text="a" * 201) and pl(color="gold") and pl(preset="nope") and pl(position="middle"))
pn = R.plan_job(PID, "illustrator.compose_card", base, root=ROOT)
check("the output follows the naming standard: SignTest_AiCard_v01.ai", pn["outputs"] == ["SignTest_AiCard_v01.ai"], pn["outputs"])
steps = " | ".join(pn["steps"])
check("the plan names the preset and size, says the picture covers the page without stretching, mentions the Optyca composer for Arabic text and that the original is never opened",
      "1080 x 1080 pt" in steps and "never stretched" in steps and "Optyca" in steps and "never opened" in steps, steps[:300])
pe = R.plan_job(PID, "illustrator.compose_card", {**base, "text": "Hello"}, root=ROOT)
check("for Latin text the plan does not mention the Optyca composer", "Optyca" not in " | ".join(pe["steps"]))

print("\n-- end to end with a fake Illustrator --")
CALLS = []
MODE = {"composer": "optyca", "lines": 1, "embedded": True, "equal": True, "fail": None}


def fake_ai(jsx_text, workdir, result_path, timeout=None):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    if MODE["fail"] == "step":
        res = {"steps": {"compose": {"error": "placedItems: the file could not be placed"}}, "errors": ["compose: placedItems"], "app": "illustrator"}
    else:
        out, png = q("OUT"), q("PNG")
        open(out, "wb").write(b"%PDF-1.5 fake ai " + b"x" * 300)
        Image.new("RGB", (1080, 1080), (30, 100, 70)).save(png)
        res = {"steps": {"compose": {"artboard_pt": [1080, 1080], "picture_scale_percent": 105.5, "embedded": MODE["embedded"], "font": "Sakkal Majalla (SakkalMajalla)",
                                     "composer": MODE["composer"], "read_back_equals_sent": MODE["equal"], "lines": MODE["lines"], "frame_height_pt": 600, "layers": MODE.get("layers", ["Text", "Background picture"])}}, "errors": [], "app": "illustrator"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


I.TRANSPORT = fake_ai


def approved(**prm):
    pln = R.plan_job(PID, "illustrator.compose_card", {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


sha0 = R.sha256_file(PIC)
res = R.run_job(PID, approved(), ROOT)
ai = os.path.join(ROOT, PID, "05_Exports", "SignTest_AiCard_v01.ai")
check("the card is delivered to 05_Exports with a page picture in 04_Previews; Illustrator was handed the COPY in 02_Assets, never the original; the original is unchanged",
      res["ok"] and os.path.isfile(ai) and res["preview"].startswith("04_Previews/") and json.dumps(os.path.join(ROOT, PID, "02_Assets", "background.png").replace("\\", "/")) in CALLS[-1]
      and PIC.replace("\\", "/") not in CALLS[-1] and R.sha256_file(PIC) == sha0, str(res))
check("the note names the font used and the picture scale, with no warning when all went well", any("Sakkal Majalla" in n and "105.5" in n for n in res["notes"]) and not any("WARNING" in n for n in res["notes"]), res["notes"])
check("the script puts the picture on its own layer 'Background picture' and the title on a layer 'Text' (live text, nothing locked), and the note lists the layers", 'ly.name=NAMES[n]' in CALLS[-1] and '"Background picture"' in CALLS[-1] and 'lyTx.name="Text"' in CALLS[-1] and "areaText" in CALLS[-1] and "createOutline" not in CALLS[-1] and any("layers: Text, Background picture" in n for n in res["notes"]), res["notes"])
check("the Arabic text reached the script exactly (as an escaped literal) and Arabic fonts were asked for", json.dumps(AR, ensure_ascii=True) in CALLS[-1] and "Sakkal Majalla" in CALLS[-1] or "Adobe Arabic" in CALLS[-1])
MODE.update(composer="not available: no such engine")
MODE0 = dict(MODE)
res = R.run_job(PID, approved(name="Sign Two"), ROOT)
check("if the Arabic composer could not be set the card is still delivered but the note says WARNING and that the letters may not join", res["ok"] and any("WARNING" in n and "may not join" in n for n in res["notes"]), res["notes"])
MODE.update(composer="optyca", lines=9, embedded="not embedded: x")
res = R.run_job(PID, approved(name="Sign Three"), ROOT)
check("too many lines for the frame is a WARNING (make it smaller); a picture that could not be embedded is mentioned", res["ok"] and any("may not fit" in n for n in res["notes"]) and any("could not be embedded" in n for n in res["notes"]), res["notes"])

print("\n-- when it fails, nothing is delivered --")
MODE.update(lines=1, embedded=True, equal=False)
res = R.run_job(PID, approved(name="Sign Four"), ROOT)
check("if the text Illustrator holds is not the text sent, the job fails and delivers nothing new", not res["ok"] and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "SignFour_AiCard_v01.ai")), str(res)[:200])
MODE.update(equal=True, fail="step")
res = R.run_job(PID, approved(name="Sign Five"), ROOT)
check("if Illustrator reports an error the job fails with its sentence and delivers nothing", not res["ok"] and "placedItems" in str(res) and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "SignFive_AiCard_v01.ai")), str(res)[:200])
src_text = open(str(DAVID_HOME / "design_illustrator.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "shutil.move" not in src_text and "os.remove(" not in src_text and "ollama" not in src_text.lower())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
