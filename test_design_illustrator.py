"""
test_design_illustrator.py -- tests for design_illustrator.py: the fixed Illustrator operations, proven with a FAKE Illustrator (no Adobe app is opened).
The fake reads the script it is handed and writes what Illustrator would; the script text, plans, yes gate, copy, staging and delivery are the real code.
Run: python test_design_illustrator.py
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
import design_illustrator as I
import design_ops as O
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dai_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
AI = os.path.join(SRC, "logo sheet.ai")
open(AI, "wb").write(b"%PDF-1.5 fake illustrator file " + b"x" * 200)
AR = os.path.join(SRC, "ملف.ai")
open(AR, "wb").write(b"%PDF-1.5 fake " + b"y" * 50)
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
I._prefs = lambda: PROFILE
O.register_builtin()


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the scripts --")
tricky = r"C:\Users\x y\مجلد\ملف \"q\" 'a'.ai"
j = I.build_jsx("export", tricky, r"C:\out dir\a.png", r"C:\out dir\r.json", fmt="png", artboard=2, max_side=600)
check("every path and value is an escaped literal: plain ASCII with Arabic, spaces, quotes and backslashes in the path; no placeholder is left", j.isascii() and not re.search(r"__[A-Z0-9]+__", j) and "\\u0645" in j and "C:/Users/x y/" in j and "AB=2" in j and "MAXSIDE=600" in j)
check("the source path is used only to open the file; the exports write to OUT; alerts are switched off and put back; the document is closed without saving", j.count("SRC") == 2 and "app.open(new File(SRC))" in j and "new File(OUT)" in j
      and "DONTDISPLAYALERTS" in j and "app.userInteractionLevel=prevUI" in j and "doc.close(SaveOptions.DONOTSAVECHANGES)" in j)
check("a PNG is never enlarged: the scale only drops below 100 when the long side is over the limit; the background is white; only the chosen artboard is exported", "if(MAXSIDE&&Math.max(w,h)>MAXSIDE)" in j and "transparency=false" in j and "artBoardClipping=true" in j and "setActiveArtboardIndex(AB-1)" in j)
jv = I.build_jsx("export", AI, "o.svg", "r.json", fmt="svg", artboard=1, max_side=None)
jp = I.build_jsx("export", AI, "o.pdf", "r.json", fmt="pdf", artboard=1, max_side=None)
check("SVG keeps the text as outlines (no font needed) and PDF is saved as a copy; with no max_side the limit is null", "SVGFontType.OUTLINEFONT" in jv and "PDFSaveOptions" in jv and "MAXSIDE=null" in jv and "doc.saveAs(new File(OUT),po)" in jp)
ji = I.build_jsx("inspect", AI, "", "r.json")
check("the inspect script only reads: no export, no save, no change to the document", not any(t in ji for t in ("exportFile", "saveAs", ".remove(", "fillColor=", "contents=")) and "doc.swatches" in ji and "doc.placedItems" in ji and "characters" in ji and "textRuns" not in ji and "budget=20000" in ji)

print("\n-- the audit (pure) --")
INFO = {"name": "logo sheet.ai", "color_space": "DocumentColorSpace.CMYK", "artboards": [{"index": 1, "name": "A", "width": 595, "height": 842}], "layers": [{"name": "L1", "visible": True, "locked": False, "items": 4}],
        "counts": {"text_frames": 4, "placed": 2}, "fonts": [{"name": "MyriadPro-Regular", "family": "Myriad Pro"}, {"name": "Papyrus", "family": "Papyrus"}, {"name": "BritannicBold", "family": "Britannic Bold"}],
        "texts": [{"contents": "Hello", "kind": "TextType.POINTTEXT", "fonts": ["MyriadPro-Regular"], "composer": "ComposerEngineType.latinCJKComposer"}, {"contents": "مرحبا بكم", "kind": "TextType.AREATEXT", "fonts": ["BritannicBold"], "composer": "ComposerEngineType.latinCJKComposer"},
                  {"contents": "أحمد سمير", "kind": "TextType.AREATEXT", "fonts": ["BritannicBold"], "composer": "ComposerEngineType.optycaComposer"}, {"contents": "قديم", "kind": "k", "fonts": [], "composer": ""}],
        "placed": [{"path": r"C:\a\ok.png", "exists": True}, {"path": r"C:\a\gone.jpg", "exists": False}],
        "swatches": [{"name": "[None]", "type": "NoColor"}, {"name": "C=0 M=100 Y=100 K=0", "type": "CMYKColor", "c": 0, "m": 100, "y": 100, "k": 0}, {"name": "Brand red", "type": "CMYKColor", "c": 0, "m": 95, "y": 90, "k": 0},
                     {"name": "Brand teal", "type": "RGBColor", "r": 14, "g": 124, "b": 123}, {"name": "Gold", "type": "SpotColor", "spot": "Gold", "tint": 100}]}
prof = copy.deepcopy(PROFILE)
prof["fonts"] = [f for f in prof["fonts"] if f["family"] != "Britannic Bold"] + [{"id": "papyrus", "family": "Papyrus", "script": "latin", "role": "any", "use": "avoid", "seen_in_files": 0, "files": [], "status": "confirmed", "source": "Demo User"}]
prof["palettes"] = [{"id": "brand", "name": "Brand", "mode": "CMYK", "colors": [{"name": "Red", "cmyk": [0, 95, 90, 0]}], "status": "confirmed", "source": "Demo User"},
                    {"id": "web", "name": "Web", "mode": "RGB", "colors": [{"name": "Teal", "hex": "#0E7C7B"}], "status": "confirmed", "source": "Demo User"}]
md, fd = I.build_audit(INFO, prof)
check("fonts: one in the profile is OK (with its status), one not in the profile is named, one marked 'avoid' is flagged", fd["fonts_known"] == ["Myriad Pro [draft]"] and fd["fonts_not_in_profile"] == ["Britannic Bold (BritannicBold)"] and fd["fonts_to_avoid"] == ["Papyrus"], str(fd))
check("swatches: the app's default library swatches and [None] are ignored; a CMYK and an RGB swatch that match a palette are 'in'; the spot colour is 'outside'", fd["swatches_in_palettes"] == ["Brand red", "Brand teal"] and fd["swatches_outside_palettes"] == ["Gold"], str(fd))
check("a missing link is listed with its path; the found one is not", fd["missing_links"] == [r"C:\a\gone.jpg"] and "MISSING: C:\\a\\gone.jpg" in md and "ok.png" not in md)
check("Arabic: three frames contain Arabic; the one on the Latin composer is flagged WARNING with the fix (the World-Ready composer), the one on the Optyca composer is fine, the one whose composer could not be read is said to be unchecked",
      fd["arabic_frames"] == 3 and fd["arabic_wrong_composer"] == 1 and fd["arabic_composer_unknown"] == 1 and "WARNING: 1 of them use the Latin paragraph composer" in md and "World-Ready Paragraph Composer" in md and "composer could not be read" in md)
fine = copy.deepcopy(INFO)
fine["texts"] = [t for t in fine["texts"] if t["composer"].endswith("optycaComposer")]
check("when every Arabic frame is on the Optyca composer the report says Arabic is joined and set right to left, with no warning", "WARNING" not in I.build_audit(fine, prof)[0] and "joins Arabic letters" in I.build_audit(fine, prof)[0])
md2, fd2 = I.build_audit(INFO, PROFILE)
check("with no palettes in the profile the report says swatches cannot be judged yet and how to fix it (never a false 'all fine')", "no palettes yet" in md2 and "cannot be judged" in md2 and "Outside every palette" not in md2)
check("a file with no text, no links and no custom swatches says so plainly", "No text found" in I.build_audit({"name": "x.ai", "color_space": "DocumentColorSpace.RGB", "artboards": [], "layers": [], "counts": {}, "fonts": [], "texts": [], "placed": [], "swatches": []}, PROFILE)[0]
      and "No placed files" in I.build_audit({"name": "x.ai", "color_space": "a", "artboards": [], "layers": [], "counts": {}, "fonts": [], "texts": [], "placed": [], "swatches": []}, PROFILE)[0])
check("the report says nothing was changed, and names the file", "Nothing was changed" in md and "logo sheet.ai" in md)
print("\n-- planning refusals and names --")
PID = R.create_project("Acme", "Spring", "2026-10-10", root=ROOT)
pl = lambda op, **prm: refused(R.plan_job, PID, op, {"source": AI, **prm}, root=ROOT)
open(os.path.join(SRC, "x.psd"), "w").write("x")
check("a Photoshop file is refused for an Illustrator operation, with the allowed types; bad format, artboard and max_side are refused", "Illustrator files only" in (refused(R.plan_job, PID, "illustrator.inspect", {"source": os.path.join(SRC, "x.psd")}, root=ROOT) or "")
      and pl("illustrator.export", format="gif") and pl("illustrator.export", artboard=0) and pl("illustrator.export", artboard=101) and pl("illustrator.export", max_side=10) and pl("illustrator.export", max_side=99999))
check("output names follow the standard: Stem_AiExport_v01.png / .svg / .pdf, Stem_AiInspect_v01.json, Stem_AiAudit_v01.md; an Arabic file name gets the stem 'File'",
      R.plan_job(PID, "illustrator.export", {"source": AI}, root=ROOT)["outputs"] == ["LogoSheet_AiExport_v01.png"] and R.plan_job(PID, "illustrator.export", {"source": AI, "format": "svg"}, root=ROOT)["outputs"] == ["LogoSheet_AiExport_v01.svg"]
      and R.plan_job(PID, "illustrator.export", {"source": AI, "format": "pdf"}, root=ROOT)["outputs"] == ["LogoSheet_AiExport_v01.pdf"] and R.plan_job(PID, "illustrator.inspect", {"source": AI}, root=ROOT)["outputs"] == ["LogoSheet_AiInspect_v01.json"]
      and R.plan_job(PID, "illustrator.audit", {"source": AR}, root=ROOT)["outputs"] == ["File_AiAudit_v01.md"])
pe = R.plan_job(PID, "illustrator.export", {"source": AI, "artboard": 2, "max_side": 800}, root=ROOT)
check("the export plan says which artboard, the limit, white background, 'never enlarged', and that the original is never opened", any("artboard 2" in s and "800 px" in s and "never enlarged" in s and "white" in s for s in pe["steps"]) and any("original is never opened" in s for s in pe["steps"]), str(pe["steps"]))

print("\n-- end to end with a fake Illustrator --")
CALLS = []


def fake_ai(jsx_text, workdir, result_path, timeout=None):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    if "info.artboards" in jsx_text:
        res = {"steps": {"inspect": INFO}, "errors": [], "app": "illustrator"}
    else:
        out = q("OUT")
        fmt = q("FMT")
        ab = int(re.search(r"AB=(\d+)", jsx_text).group(1))
        ms = re.search(r"MAXSIDE=(null|\d+)", jsx_text).group(1)
        w, h = 1200, 800
        scale = 100.0 if ms == "null" or max(w, h) <= int(ms) else int(ms) / max(w, h) * 100
        sz = (round(w * scale / 100), round(h * scale / 100))
        if fmt == "png":
            Image.new("RGB", sz, (9, 9, 9)).save(out)
        else:
            open(out, "wb").write(b"<svg/>" if fmt == "svg" else b"%PDF-1.4 fake")
        res = {"steps": {"export": {"artboards": 3, "out": out, "width_px": sz[0], "height_px": sz[1]}}, "errors": [], "app": "illustrator"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


I.TRANSPORT = fake_ai


def approved(op, **prm):
    pln = R.plan_job(PID, op, {"source": AI, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


sha0 = R.sha256_file(AI)
res = R.run_job(PID, approved("illustrator.export", artboard=2, max_side=600), ROOT)
png = os.path.join(ROOT, PID, "05_Exports", "LogoSheet_AiExport_v01.png")
check("PNG export: delivered at 600x400 (1200x800 scaled to the long side 600), with a preview; Illustrator was handed the COPY in 02_Assets, never the original; the original is unchanged",
      res["ok"] and Image.open(png).size == (600, 400) and res["preview"].startswith("04_Previews/") and json.dumps(os.path.join(ROOT, PID, "02_Assets", "logo sheet.ai").replace("\\", "/")) in CALLS[-1] and AI.replace("\\", "/") not in CALLS[-1] and R.sha256_file(AI) == sha0, str(res))
R.undo_job(PID, res["delivered"][0].split("/")[-1] and R.list_jobs(PID, ROOT)[-1]["job"], ROOT)
res = R.run_job(PID, approved("illustrator.export", format="svg"), ROOT)
check("SVG export is delivered (no preview: it is not a picture the page can show)", res["ok"] and res["preview"] is None and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "LogoSheet_AiExport_v01.svg")), str(res))
res = R.run_job(PID, approved("illustrator.export", format="pdf"), ROOT)
check("PDF export is delivered", res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "LogoSheet_AiExport_v01.pdf")), str(res))
res = R.run_job(PID, approved("illustrator.inspect"), ROOT)
rep = json.load(open(os.path.join(ROOT, PID, "05_Exports", "LogoSheet_AiInspect_v01.json"), encoding="utf-8"))
check("inspect delivers what Illustrator reported, with a note naming the fonts", res["ok"] and rep["counts"]["text_frames"] == 4 and "Myriad Pro" in res["notes"][0], str(res))
res = R.run_job(PID, approved("illustrator.audit"), ROOT)
mdf = open(os.path.join(ROOT, PID, "05_Exports", "LogoSheet_AiAudit_v01.md"), encoding="utf-8").read()
check("audit delivers the readable report against the profile (fonts not in it, missing link, the Arabic warning) and a note with the counts", res["ok"] and "NOT in your profile" in mdf and "MISSING" in mdf and "WARNING" in mdf and "1 missing link" in res["notes"][0] and "1 on the Latin composer" in res["notes"][0], str(res))

print("\n-- when Illustrator fails, nothing is delivered --")


def failing(kind):
    def f(jsx_text, workdir, result_path, timeout=None):
        if kind == "raise":
            raise I.IllustratorError("Illustrator did not finish within 7 minutes (a dialog may be waiting in the app); the job was stopped")
        if kind == "errors":
            return {"steps": {}, "errors": ["open: The file is damaged"], "app": "illustrator"}
        if kind == "stepfail":
            return {"steps": {"export": {"error": "the file has 3 artboard(s); artboard 9 does not exist"}}, "errors": ["export: artboard 9"], "app": "illustrator"}
        return {"steps": {"export": {"artboards": 1, "out": "x"}}, "errors": [], "app": "illustrator"}
    return f


for kind, expect in (("raise", "did not finish"), ("errors", "damaged"), ("stepfail", "artboard"), ("nofile", "not there")):
    I.TRANSPORT = failing(kind)
    before = sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports")))
    jid = approved("illustrator.export", artboard=3, format="svg" if kind == "nofile" else "png")
    r = R.run_job(PID, jid, ROOT)
    check(f"'{kind}': the job fails with Illustrator's sentence, delivers nothing new, leaves no staging folder; the original is unchanged", not r["ok"] and expect in r["error"] and sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports"))) == before
          and not os.path.exists(os.path.join(ROOT, PID, "_job", jid, "staging")) and R.sha256_file(AI) == sha0, str(r))
I.TRANSPORT = fake_ai

print("\n-- wiring --")
seen = {}
real_run = C.run_script
C.run_script = lambda app, jsx, wd, rp, timeout=C.WORKER_TIMEOUT: seen.update(app=app) or {"steps": {}, "errors": []}
try:
    I.default_transport("x", TMP, os.path.join(TMP, "r.json"))
finally:
    C.run_script = real_run
check("the default transport runs the script in Illustrator through the shared worker, and an app error becomes an Illustrator error", seen.get("app") == "illustrator" and issubclass(I.IllustratorError, C.AppError))
check("the three operations are registered as Illustrator operations, once", all(R.OPERATIONS[k]["app"] == "illustrator" for k in ("illustrator.inspect", "illustrator.audit", "illustrator.export")) and (I.register() or True) and list(R.OPERATIONS).count("illustrator.inspect") == 1)
src_text = open(str(DAVID_HOME / "design_illustrator.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "shutil.move" not in src_text and "os.remove(" not in src_text and "ollama" not in src_text.lower())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
