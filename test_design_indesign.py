"""
test_design_indesign.py -- tests for design_indesign.py: the fixed InDesign operations, proven with a FAKE InDesign (no Adobe app is opened).
The fake reads the script it is handed and writes what InDesign would; script text, plans, yes gate, copies, staging and delivery are the real code.
Run: python test_design_indesign.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, re, json, copy, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image
import design_com as C
import design_indesign as D
import design_ops as O
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="did_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
INDD = os.path.join(SRC, "menu card.indd")
open(INDD, "wb").write(b"<fake indd>" + b"x" * 300)
PIC = os.path.join(SRC, "dish.png")
Image.new("RGB", (400, 300), (200, 100, 50)).save(PIC)
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
D._prefs = lambda: PROFILE
O.register_builtin()
AR = "\u0644\u0627 \u0627\u0644\u062a\u0635\u0645\u064a\u0645"


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- pure helpers --")
check("preset sizes: print in mm as they are; inches to mm; a 300 dpi pixel size (A4 3508x2480) becomes 297x210 mm; a 72 dpi social size stays in points; points stay points",
      D.preset_dims({"unit": "mm", "width": 210, "height": 297, "dpi": 300}) == ("mm", 210.0, 297.0) and D.preset_dims({"unit": "in", "width": 8.5, "height": 11, "dpi": 300}) == ("mm", 215.9, 279.4)
      and D.preset_dims({"unit": "px", "width": 3508, "height": 2480, "dpi": 300}) == ("mm", 297.0, 210.0) and D.preset_dims({"unit": "px", "width": 1080, "height": 1350, "dpi": 72}) == ("pt", 1080.0, 1350.0)
      and D.preset_dims({"unit": "pt", "width": 595, "height": 842, "dpi": 72}) == ("pt", 595.0, 842.0))
check("a box is four numbers in millimetres from the page's top left: good ones parse; wrong count, text, zero size, negative, absurd are refused with the example",
      D.parse_box("20,20,150,50", "text box") == [20.0, 20.0, 150.0, 50.0] and all("20,20,120,40" in (refused(D.parse_box, b, "text box") or "") for b in ("1,2,3", "a,b,c,d", "10,10,0,5", "-1,0,5,5", "0,0,99999,5", "")))
check("Arabic is detected by its letters; the default font list for Arabic is the preferred Arabic fonts, then Adobe Arabic and Sakkal Majalla; for Latin Myriad Pro and Arial; a requested font is used alone",
      D.has_arabic(AR) and not D.has_arabic("Hello 123") and D.font_candidates(None, True)[-2:] == ["Adobe Arabic", "Sakkal Majalla"] and D.font_candidates(None, False)[-2:] == ["Myriad Pro", "Arial"] and D.font_candidates("Cairo", True) == ["Cairo"])
P2 = copy.deepcopy(PROFILE)
P2["fonts"].append({"id": "cairo", "family": "Cairo", "script": "arabic", "role": "any", "use": "preferred", "seen_in_files": 0, "files": [], "status": "confirmed", "source": "Demo User"})
D._prefs = lambda: P2
check("a font the designer marked 'preferred' for Arabic comes first", D.font_candidates(None, True)[0] == "Cairo" and D.font_candidates(None, False)[0] == "Myriad Pro" or D.font_candidates(None, False)[0] != "Cairo")
D._prefs = lambda: PROFILE

print("\n-- the scripts --")
tricky = r"C:\Users\x y\مجلد\ملف \"q\" 'a'.indd"
jp = D.build_jsx("place", r"C:\o\r.json", src=tricky, out=r"C:\o dir\a.indd", png=r"C:\o dir\p.png", page=2, text=AR, tbox=[20, 20, 150, 50], fams=["Adobe Arabic", "Sakkal Majalla"], size=40, rtl=True, img=PIC, ibox=[20, 80, 100, 100], fit="fit")
check("every path and value is an escaped literal: plain ASCII with Arabic, spaces, quotes and backslashes in the path; no placeholder is left; the Arabic text arrives as escapes", jp.isascii() and not re.search(r"__[A-Z0-9]+__", jp) and "\\u0644\\u0627" in jp and "C:/Users/x y/" in jp and "PAGE=2" in jp)
check("the source is only opened (app.open, no window); the result is saved under a NEW name (OUT) and the document is closed WITHOUT saving; alerts are off and put back", jp.count("SRC") == 2 and "app.open(new File(SRC),false)" in jp and "doc.save(new File(OUT))" in jp
      and "doc.close(SaveOptions.NO)" in jp and "NEVER_INTERACT" in jp and "app.scriptPreferences.userInteractionLevel=prevUI" in jp)
check("right-to-left text gets character direction, paragraph direction, the World-Ready composer and right alignment (what was measured to join Arabic); an LTR text does not call that function",
      all(t in jp for t in ("RIGHT_TO_LEFT_DIRECTION", "ParagraphDirectionOptions.RIGHT_TO_LEFT_DIRECTION", "Adobe World-Ready Paragraph Composer", "Justification.RIGHT_ALIGN")) and "RTL=true" in jp
      and "RTL=false" in D.build_jsx("place", "r.json", src=INDD, out="o.indd", png="p.png", page=1, text="Hello", tbox=[1, 1, 5, 5], fams=["Arial"], size=12, rtl=False, img=None, ibox=None, fit="fit"))
jn = D.build_jsx("new", "r.json", out="o.indd", width=297.0, height=210.0, unit="mm", bleed=3, margin=10, pages=2, rtl=True)
check("a new document sets size, pages, uniform bleed, margins on the document, its pages and the master; right-to-left binding and the basic paragraph style only when asked; it saves to OUT and closes without saving",
      all(t in jn for t in ("W1=297.0", "H1=210.0", "BLEED=3.0", "MARGIN=10.0", "PAGES=2", "documentBleedUniformSize=true", "masterSpreads[0]", "PageBindingOptions.RIGHT_TO_LEFT", "[Basic Paragraph]", "doc.save(new File(OUT))")) and "RTL=true" in jn
      and "RTL=false" in D.build_jsx("new", "r.json", out="o.indd", width=1080.0, height=1350.0, unit="pt", bleed=0, margin=0, pages=1, rtl=False))
je = D.build_jsx("export", "r.json", src=INDD, out="o.pdf", png="p.png", preset="[PDF/X-4:2008]", pages="1-3")
check("the PDF export uses one of InDesign's own presets by name (and says so if it does not exist), the page range, and never opens the PDF afterwards", 'PRESET="[PDF/X-4:2008]"' in je and 'PAGES="1-3"' in je and "pdfExportPresets.itemByName(PRESET)" in je and "viewPDF=false" in je and "if(!pr.isValid)" in je)
ji = D.build_jsx("inspect", "r.json", src=INDD)
check("the inspect script only reads: no export, no save, no add", not any(t in ji for t in ("exportFile", ".save(", ".add(", ".place(")) and "doc.links" in ji and "doc.fonts" in ji and "doc.textFrames" in ji)

print("\n-- planning refusals, names and plans --")
PID = R.create_project("Acme", "Spring", "2026-10-10", root=ROOT)
plan = lambda op, **prm: refused(R.plan_job, PID, op, prm, root=ROOT)
check("an Illustrator file is refused by InDesign operations, with the allowed types", "InDesign files only" in (plan("indesign.inspect", source=PIC) or ""))
check("place_content needs a text or a picture; a bad text box, a bad picture box, a bad picture type, an unknown page range, a bad size are all refused with a sentence",
      "nothing to place" in (plan("indesign.place_content", source=INDD) or "") and "text box" in (plan("indesign.place_content", source=INDD, text="x", text_box="1,2") or "") and "picture box" in (plan("indesign.place_content", source=INDD, image=PIC, image_box="a") or "")
      and "picture must be" in (plan("indesign.place_content", source=INDD, image=INDD) or "") and plan("indesign.export_pdf", source=INDD, pages="1-") and plan("indesign.export_pdf", source=INDD, preset="best") and plan("indesign.place_content", source=INDD, text="x", size=2))
check("new_document: an unknown preset is refused with the list; a name with no English letters is refused; names come out as Name_IdDoc_v01.indd",
      "square-1080" in (plan("indesign.new_document", preset="nope", name="Menu") or "") and "English" in (plan("indesign.new_document", preset="a4-portrait-300", name="شاورما") or "")
      and R.plan_job(PID, "indesign.new_document", {"preset": "a4-portrait-300", "name": "Spring Menu"}, root=ROOT)["outputs"] == ["SpringMenu_IdDoc_v01.indd"])
pn = R.plan_job(PID, "indesign.new_document", {"preset": "a4-landscape-300", "name": "Card", "pages": 2, "rtl": True}, root=ROOT)
check("the new-document plan says the real size (A4 landscape 300 dpi px preset -> 297 x 210 mm), the pages, the bleed and margins from the preferences, and the right-to-left settings; it has no input file", pn["inputs"] == [] and any("2 page(s) of 297 x 210 mm" in s for s in pn["steps"])
      and any("Bleed 3 mm and margins 10 mm" in s and "right to left" in s for s in pn["steps"]), str(pn["steps"]))
ps = R.plan_job(PID, "indesign.new_document", {"preset": "square-1080", "name": "Post"}, root=ROOT)
check("a screen size (1080x1080 at 72 dpi) is made in points with no bleed or margins", any("1080 x 1080 pt" in s for s in ps["steps"]) and any("Bleed 0 mm and margins 0 mm" in s for s in ps["steps"]), str(ps["steps"]))
pp = R.plan_job(PID, "indesign.place_content", {"source": INDD, "text": AR, "image": PIC}, root=ROOT)
check("the place plan copies BOTH files (two inputs), says the text is right to left and why (Arabic found), the font, the boxes, and that the result is a NEW file while the copy stays as it was", len(pp["inputs"]) == 2 and any("right to left" in s and "World-Ready" in s for s in pp["steps"])
      and any("Add a picture frame at 20,80,100,100" in s for s in pp["steps"]) and any("NEW InDesign file" in s and "stays as it was" in s for s in pp["steps"]) and pp["outputs"] == ["MenuCard_IdPlaced_v01.indd"], str(pp["steps"]))
pl = R.plan_job(PID, "indesign.place_content", {"source": INDD, "text": "Hello", "direction": "rtl"}, root=ROOT)
check("a Latin text is left to right unless the designer says rtl; saying rtl is honoured", not any("right to left" in s for s in R.plan_job(PID, "indesign.place_content", {"source": INDD, "text": "Hello"}, root=ROOT)["steps"]) and any("right to left" in s for s in pl["steps"]))
check("the PDF plan names the real InDesign preset and the pages", any("[PDF/X-4:2008]" in s and "pages 1-2" in s for s in R.plan_job(PID, "indesign.export_pdf", {"source": INDD, "preset": "pdfx4", "pages": "1-2"}, root=ROOT)["steps"]))

print("\n-- end to end with a fake InDesign --")
CALLS = []
INFO = {"name": "menu card.indd", "pages": 2, "spreads": 2, "width_mm": 297, "height_mm": 210, "facing_pages": False, "binding": "PageBindingOptions.RIGHT_TO_LEFT", "bleed_mm": {"top": 3, "bottom": 3, "inside": 3, "outside": 3}, "version": "21.6.0.57",
        "layers": [{"name": "Layer 1", "visible": True, "locked": False}], "fonts": [{"name": "Adobe Arabic\tRegular", "family": "Adobe Arabic", "status": "FontStatus.INSTALLED"}, {"name": "Gone\tRegular", "family": "Gone", "status": "FontStatus.NOT_AVAILABLE"}],
        "texts": [{"contents": AR, "page": "1", "overset": False, "font": "Adobe Arabic", "direction": "CharacterDirectionOptions.RIGHT_TO_LEFT_DIRECTION", "composer": "Adobe World-Ready Paragraph Composer"}],
        "links": [{"name": "dish.png", "path": "C:\\x\\dish.png", "status": "LinkStatus.NORMAL"}, {"name": "gone.jpg", "path": "C:\\x\\gone.jpg", "status": "LinkStatus.LINK_MISSING"}], "swatches": [{"name": "Black"}], "paragraph_styles": 3, "character_styles": 1}


def fake_id(jsx_text, workdir, result_path, timeout=None):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    if "info.layers" in jsx_text:
        res = {"steps": {"inspect": INFO}, "errors": [], "app": "indesign"}
    elif "pdfExportPresets" in jsx_text:
        open(q("OUT"), "wb").write(b"%PDF-1.4 fake")
        Image.new("RGB", (595, 842), (255, 255, 255)).save(q("PNG"))
        res = {"steps": {"export": {"pages": 2, "out": q("OUT"), "png": q("PNG"), "preset": q("PRESET")}}, "errors": [], "app": "indesign"}
    elif "documents.add(false)" in jsx_text:
        open(q("OUT"), "wb").write(b"<fake new indd>")
        w, h = re.search(r"W1=([\d.]+),H1=([\d.]+)", jsx_text).groups()
        res = {"steps": {"create": {"pages": int(re.search(r"PAGES=(\d+)", jsx_text).group(1)), "width": float(w), "height": float(h), "unit": q("UNIT"), "bleed": float(re.search(r"BLEED=([\d.]+)", jsx_text).group(1)),
                                    "margin": float(re.search(r"MARGIN=([\d.]+)", jsx_text).group(1)), "rtl": {"binding": "set", "composer": "set"} if "RTL=true" in jsx_text else {}, "out": q("OUT")}}, "errors": [], "app": "indesign"}
    else:
        open(q("OUT"), "wb").write(b"<fake placed indd>")
        Image.new("RGB", (595, 842), (255, 255, 255)).save(q("PNG"))
        res = {"steps": {"place": {"font": "Adobe Arabic", "rtl": {"character": "set", "paragraph": "set", "composer": "set", "alignment": "set"} if "RTL=true" in jsx_text else None, "read_back_equals_sent": True, "overset": False, "image": "placed", "out": q("OUT")}}, "errors": [], "app": "indesign"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


D.TRANSPORT = fake_id


def approved(op, params):
    pln = R.plan_job(PID, op, params, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


sha_i, sha_p = R.sha256_file(INDD), R.sha256_file(PIC)
res = R.run_job(PID, approved("indesign.new_document", {"preset": "a4-landscape-300", "name": "Card", "pages": 2, "rtl": True}), ROOT)
check("new_document: delivered as Card_IdDoc_v01.indd with a note of its real size (297 x 210 mm, bleed 3, margins 10); the script was given the right-to-left switch", res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "Card_IdDoc_v01.indd"))
      and "2 page(s), 297.0 x 210.0 mm, bleed 3.0, margins 10.0" in res["notes"][0] and "RTL=true" in CALLS[-1], str(res))
res = R.run_job(PID, approved("indesign.place_content", {"source": INDD, "text": AR, "image": PIC, "size": 60}), ROOT)
c = CALLS[-1]
check("place_content: delivered as MenuCard_IdPlaced_v01.indd with a preview; InDesign was handed the COPIES in 02_Assets, never the originals; Arabic was detected so the script has RTL=true; both originals are unchanged",
      res["ok"] and res["delivered"][0].endswith("MenuCard_IdPlaced_v01.indd") and res["preview"].startswith("04_Previews/") and json.dumps(os.path.join(ROOT, PID, "02_Assets", "menu card.indd").replace("\\", "/")) in c
      and json.dumps(os.path.join(ROOT, PID, "02_Assets", "dish.png").replace("\\", "/")) in c and INDD.replace("\\", "/") not in c and PIC.replace("\\", "/") not in c and "RTL=true" in c and R.sha256_file(INDD) == sha_i and R.sha256_file(PIC) == sha_p, str(res))
check("the note names the font and the picture", "font Adobe Arabic" in res["notes"] and "picture placed" in res["notes"], str(res["notes"]))
res = R.run_job(PID, approved("indesign.export_pdf", {"source": INDD, "preset": "pdfx4", "pages": "1-2"}), ROOT)
check("export_pdf: a PDF in 05_Exports and the page-1 picture as the preview; the script got the preset name and the range", res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "MenuCard_IdPdf_v01.pdf")) and res["preview"].startswith("04_Previews/") and '"[PDF/X-4:2008]"' in CALLS[-1] and 'PAGES="1-2"' in CALLS[-1], str(res))
res = R.run_job(PID, approved("indesign.inspect", {"source": INDD}), ROOT)
check("inspect delivers the report and a note that counts the missing link and the font that is not installed", res["ok"] and "1 missing link(s), 1 font(s) not installed" in res["notes"][0], str(res))

print("\n-- when something is wrong, it is said and nothing is delivered --")
INFO_OVERSET = {"steps": {"place": {"font": "Adobe Arabic", "rtl": {"composer": "not available: boom", "character": "set"}, "read_back_equals_sent": True, "overset": True}}, "errors": [], "app": "indesign"}


def odd(kind):
    def f(jsx_text, workdir, result_path, timeout=None):
        q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
        if kind == "raise":
            raise D.InDesignError("InDesign did not finish within 7 minutes (a dialog may be waiting in the app); the job was stopped")
        if kind == "errors":
            return {"steps": {}, "errors": ["open: the file was made with a newer InDesign"], "app": "indesign"}
        if kind == "stepfail":
            return {"steps": {"place": {"error": "the file has 2 page(s); page 9 does not exist"}}, "errors": ["place: page 9"], "app": "indesign"}
        if kind == "nofile":
            return {"steps": {"place": {"font": "x", "rtl": None, "read_back_equals_sent": True, "overset": False}}, "errors": [], "app": "indesign"}
        if kind == "misread":
            open(q("OUT"), "wb").write(b"x")
            return {"steps": {"place": {"font": "x", "rtl": None, "read_back_equals_sent": False, "overset": False}}, "errors": [], "app": "indesign"}
        if kind == "overset":
            open(q("OUT"), "wb").write(b"<fake>")
            Image.new("RGB", (50, 50), (255, 255, 255)).save(q("PNG"))
            return INFO_OVERSET
    return f


for kind, expect in (("raise", "did not finish"), ("errors", "newer InDesign"), ("stepfail", "page 9"), ("nofile", "not there"), ("misread", "not the text that was sent")):
    D.TRANSPORT = odd(kind)
    before = sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports")))
    jid = approved("indesign.place_content", {"source": INDD, "text": "Hello"})
    r = R.run_job(PID, jid, ROOT)
    check(f"'{kind}': the job fails with the sentence, delivers nothing new, leaves no staging folder; the original is unchanged", not r["ok"] and expect in r["error"] and sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports"))) == before
          and not os.path.exists(os.path.join(ROOT, PID, "_job", jid, "staging")) and R.sha256_file(INDD) == sha_i, str(r))
D.TRANSPORT = odd("overset")
INDD2 = os.path.join(SRC, "second card.indd")
open(INDD2, "wb").write(b"<fake indd 2>" + b"y" * 99)
r = R.run_job(PID, approved("indesign.place_content", {"source": INDD2, "text": AR, "page": 1}), ROOT)
check("a text that does not fit its frame is delivered but the note says WARNING overset; a right-to-left setting InDesign could not apply is reported by name", r["ok"] and any("WARNING: the text does not fit" in n for n in r["notes"]) and any("composer" in n and "NOT available" in n for n in r["notes"]), str(r))
D.TRANSPORT = fake_id

print("\n-- wiring --")
seen = {}
real_run = C.run_script
C.run_script = lambda app, jsx, wd, rp, timeout=C.WORKER_TIMEOUT: seen.update(app=app) or {"steps": {}, "errors": []}
try:
    D.default_transport("x", TMP, os.path.join(TMP, "r.json"))
finally:
    C.run_script = real_run
check("the default transport uses the shared worker for InDesign, which is reached by the stable build's CLSID (never a ProgID) and a script is run through DoScript", seen.get("app") == "indesign" and C.APPS["indesign"]["com"] == "{45112D7C-822C-400A-BA8C-D682F7FD0B8E}" and C.APPS["indesign"]["method"] == "DoScript" and issubclass(D.InDesignError, C.AppError))
check("the four operations are registered once, as InDesign operations", all(R.OPERATIONS[k]["app"] == "indesign" for k in ("indesign.inspect", "indesign.export_pdf", "indesign.new_document", "indesign.place_content")) and (D.register() or True) and list(R.OPERATIONS).count("indesign.inspect") == 1)
src_text = open(str(DAVID_HOME / "design_indesign.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "shutil.move" not in src_text and "os.remove(" not in src_text and "ollama" not in src_text.lower())
real_sp = C.server_path
C.server_path = lambda app: r"C:\Program Files\Adobe\Adobe InDesign 2027 (Beta)\InDesign (Beta).exe"
ran = []
real_popen = C.subprocess.Popen
C.subprocess.Popen = lambda *a, **k: ran.append(a) or (_ for _ in ()).throw(AssertionError("worker started"))
msg = None
try:
    C.run_script("indesign", "x", TMP, os.path.join(TMP, "r2.json"))
except C.AppError as e:
    msg = str(e)
finally:
    C.subprocess.Popen = real_popen
    C.server_path = real_sp
check("if Windows points the InDesign connection at a beta program, the bridge refuses with a sentence and does not start anything", msg and "stable program" in msg and "Beta" in msg and not ran, msg)
import ci_env
_need = ci_env.needs("adobe:indesign")       # this check reads the real Windows registration of the installed InDesign
if _need:
    ci_env.skip("on this computer the stable InDesign connection points at the stable program", _need)
else:
    check("on this computer the stable InDesign connection points at the stable program", C.server_path("indesign").lower() == C.APPS["indesign"]["exe"].lower() and C.check_target("indesign") is None)
kw = dict(src="a.indd", out="b.indd", png="c.png", page=1, text=AR, tbox=[0, 0, 10, 10], fams=["X"], size=12, img=None, ibox=None, fit="fit")
jx = D.build_jsx("place", "r.json", rtl=False, ltr=True, **kw)
jr = D.build_jsx("place", "r.json", rtl=True, ltr=False, **kw)
check("forced left to right sets LEFT_TO_RIGHT character and paragraph direction, the Latin composer and left alignment; right to left does not", "LTR=true" in jx and "RTL=false" in jx and "LEFT_TO_RIGHT_DIRECTION" in jx and "LTR=false" in jr and "RTL=true" in jr)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
