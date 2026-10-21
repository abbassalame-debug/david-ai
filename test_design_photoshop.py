"""
test_design_photoshop.py -- tests for design_photoshop.py: the fixed Photoshop operations, proven with a FAKE Photoshop (no Adobe app is opened).
The fake reads the script it is handed and writes what Photoshop would: so the script text, the paths it contains, the plan, the yes gate, the copy, the staging
and the delivery are all the real code.   Run: python test_design_photoshop.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, re, json, copy, shutil, struct, tempfile, subprocess
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
import design_ops as O
import design_photoshop as P
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dps_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)

SEED = json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8"))
PROFILE = PREF.seed_profile(SEED)
P._prefs = lambda: PROFILE
O.register_builtin()


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


def sha(p):
    return R.sha256_file(p)


# a 1200x800 PNG, a 3508x2480 "PSD" (header only: the fake never opens it), a CMYK-looking preset is in the seed? no: add one
PNG = os.path.join(SRC, "poster.png")
Image.new("RGB", (1200, 800), (200, 40, 40)).save(PNG)
PSD = os.path.join(SRC, "banner.psd")
open(PSD, "wb").write(b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, 2480, 3508, 8, 3) + b"\0" * 64)
AR = os.path.join(SRC, "ملف عربي.png")
Image.new("RGB", (600, 600), (10, 10, 200)).save(AR)
PROFILE["document_presets"].append({"id": "print-a5-cmyk", "name": "A5 print", "kind": "print", "unit": "mm", "width": 148, "height": 210, "dpi": 300, "color_mode": "CMYK", "status": "draft", "source": "test"})
PROFILE["document_presets"].append({"id": "a4-mm-rgb", "name": "A4 mm", "kind": "print", "unit": "mm", "width": 210, "height": 297, "dpi": 300, "color_mode": "RGB", "status": "draft", "source": "test"})

print("-- the pure planning helpers --")
check("a proof is never enlarged and never larger than the long side: 3508x2480 at 1600 -> 1600x1131; 1200x800 at 1600 stays (None)", P.preview_plan(3508, 2480, 1600) == (1600, 1131) and P.preview_plan(1200, 800, 1600) is None and P.preview_plan(1600, 900, 1600) is None)
check("preset sizes: px as they are; A4 in mm at 300 dpi = 2480x3508; 8.5x11 in at 300 dpi = 2550x3300; 595x842 pt at 300 dpi = 2479x3508", P.preset_px({"unit": "px", "width": 1080, "height": 1350, "dpi": 72}) == (1080, 1350) and P.preset_px({"unit": "mm", "width": 210, "height": 297, "dpi": 300}) == (2480, 3508)
      and P.preset_px({"unit": "in", "width": 8.5, "height": 11, "dpi": 300}) == (2550, 3300) and P.preset_px({"unit": "pt", "width": 595, "height": 842, "dpi": 300}) == (2479, 3508))
check("cover scales until the frame is filled (3508x2480 into 1080x1080 -> 1528x1080, the overflow is cropped); contain scales until it fits (-> 1080x764, the rest is padded)", P.fit_plan(3508, 2480, 1080, 1080, "cover") == (1528, 1080) and P.fit_plan(3508, 2480, 1080, 1080, "contain") == (1080, 764))
check("a source smaller than the frame is scaled UP to cover it (1200x800 into 1080x1920 -> 2880x1920) and a tiny one never gives a zero side", P.fit_plan(1200, 800, 1080, 1920, "cover") == (2880, 1920) and min(P.fit_plan(2, 2000, 10, 10, "contain")) >= 1)

print("\n-- the scripts --")
tricky = r"C:\Users\x y\مجلد\ملف \"q\" 'a'.psd"
j = P.build_jsx("render", tricky, r"C:\out dir\a.png", r"C:\out dir\r.json", fmt="png", quality=90, profile="sRGB IEC61966-2.1", w1=1528, h1=1080, cw=1080, ch=1080)
check("every path and value goes in as an escaped literal: the script is plain ASCII even with Arabic, spaces, quotes and backslashes in the path, and no placeholder is left", j.isascii() and not re.search(r"__[A-Z0-9]+__", j) and "\\u0645" in j and "C:/Users/x y/" in j)
check("the source path appears only in the app.open call; the script never saves over it (the only save is to OUT, as a copy)", j.count("SRC") == 2 and "app.open(new File(SRC))" in j and "saveAs(new File(OUT)" in j and "true,Extension.LOWERCASE" in j)
check("every script closes what it opened without saving, and restores the app's dialog and ruler settings", all("DONOTSAVECHANGES" in P.build_jsx(k, PNG, "o.png", "r.json", fmt="png", quality=90, profile="x") and "app.displayDialogs=prevDialogs" in P.build_jsx(k, PNG, "o.png", "r.json", fmt="png", quality=90, profile="x") for k in ("inspect", "render")))
jn = P.build_jsx("render", PNG, "o.jpg", "r.json", fmt="jpg", quality=85, profile="sRGB IEC61966-2.1", w1=None, h1=None, cw=None, ch=None)
check("no resize and no canvas are 'null' in the script; a JPG gets its quality, mapped from 1-100 to Photoshop's own 0-12 scale (found by the live test: 90 was refused as 'greater than maximum')", "W1=null,H1=null,CW=null,CH=null" in jn and "QUALITY=85" in jn and 'FMT="jpg"' in jn and "Math.round(QUALITY*12/100)" in jn and "Math.min(12," in jn)
check("a PNG is saved with the strongest compression Photoshop offers (flat proofs were several MB without it), inside a try so an older Photoshop that lacks the setting still works", "po.compression=9" in j and "try{po.compression=9;}catch(e){}" in j)
check("the render script works on a duplicate and flattens it; it converts to the profile and 8 bits; hidden layers are left out (flatten)", all(t in j for t in ("doc.duplicate(", "d2.flatten()", "d2.convertProfile(PROFILE", "BitsPerChannelType.EIGHT", "d2.close(SaveOptions.DONOTSAVECHANGES)")))
ji = P.build_jsx("inspect", PSD, "", "r.json")
check("the inspect script only reads: no save, no flatten, no resize", not any(t in ji for t in ("saveAs", "flatten", "resizeImage", "resizeCanvas", "convertProfile")) and "doc.layers" in ji)

print("\n-- planning refusals and names --")
pl = lambda op, **prm: refused(R.plan_job, PID, op, {"source": PNG, **prm}, root=ROOT)
PID = R.create_project("Acme", "Spring", "2026-10-10", root=ROOT)
open(os.path.join(SRC, "x.docx"), "w").write("x")
check("a docx file is refused; an unknown preset is refused and the presets are listed; a CMYK preset is refused with the reason", "Photoshop, TIFF, PNG or JPG" in (refused(R.plan_job, PID, "photoshop.inspect", {"source": os.path.join(SRC, "x.docx")}, root=ROOT) or "")
      and "square-1080" in (pl("photoshop.resize_to_preset", preset="nope") or "") and "CMYK" in (pl("photoshop.resize_to_preset", preset="print-a5-cmyk") or ""))
check("bad choices are refused: fit must be cover or contain, format png or jpg, max_side 256 to 8192", all(pl("photoshop.resize_to_preset", preset="square-1080", fit="stretch"), ) and pl("photoshop.export_preview", format="gif") and pl("photoshop.export_preview", max_side=100) and pl("photoshop.export_preview", max_side=99999))
p1 = R.plan_job(PID, "photoshop.resize_to_preset", {"source": PNG, "preset": "square-1080"}, root=ROOT)
check("the output name follows the naming standard (Stem_Preset_v01.png); the plan says exactly what will happen, with the real numbers", p1["outputs"] == ["Poster_Square1080_v01.png"]
      and any("1620 x 1080" in s and "1080 x 1080" in s and "crop" in s for s in p1["steps"]) and p1["app"] == "photoshop" and p1["params"]["fit"] == "cover", str(p1["steps"]))
p2 = R.plan_job(PID, "photoshop.export_preview", {"source": PSD, "format": "jpg"}, root=ROOT)
check("a proof plan says the shrink (3508x2480 -> 1600x1131 at the preference's long side) and the profile; an Arabic-named file gets the stem 'File'", p2["outputs"] == ["Banner_PsPreview_v01.jpg"] and any("1600 x 1131" in s and "sRGB IEC61966-2.1" in s for s in p2["steps"])
      and R.plan_job(PID, "photoshop.inspect", {"source": AR}, root=ROOT)["outputs"] == ["File_PsInspect_v01.json"], str(p2["steps"]))
check("a proof of a file already within the limit says no resize", any("no resize" in s for s in R.plan_job(PID, "photoshop.export_preview", {"source": PNG}, root=ROOT)["steps"]))
check("an A4 millimetre preset is turned into pixels by its dpi", any("2480 x 3508" in s for s in R.plan_job(PID, "photoshop.resize_to_preset", {"source": PNG, "preset": "a4-mm-rgb", "fit": "contain"}, root=ROOT)["steps"]))

print("\n-- end to end with a fake Photoshop --")
CALLS = []


def fake_ps(jsx_text, workdir, result_path, timeout=None):
    CALLS.append({"jsx": jsx_text, "result": result_path})
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    src = q("SRC")
    if "walk(" in jsx_text:
        res = {"steps": {"inspect": {"width_px": 1200, "height_px": 800, "resolution": 72, "mode": "DocumentMode.RGB", "bits": "BitsPerChannelType.EIGHT", "profile": "sRGB IEC61966-2.1", "top_level_layers": 3,
                                     "layers_listed": 3, "truncated": False, "text_layers": 1, "smart_objects": 0, "groups": 0, "fonts": ["SakkalMajalla"], "layers": []}}, "errors": [], "app": "photoshop"}
    else:
        out = q("OUT")
        m = {k: re.search(k + r"=(null|\d+)", jsx_text).group(1) for k in ("W1", "H1", "CW", "CH")}
        w, h = Image.open(src).size
        size = (int(m["CW"]), int(m["CH"])) if m["CW"] != "null" else (int(m["W1"]), int(m["H1"])) if m["W1"] != "null" else (w, h)
        Image.new("RGB", size, (1, 2, 3)).save(out)
        res = {"steps": {"render": {"width_px": size[0], "height_px": size[1], "profile": "sRGB IEC61966-2.1", "out": out}}, "errors": [], "app": "photoshop"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


P.TRANSPORT = fake_ps


def approved(op, **prm):
    pln = R.plan_job(PID, op, {"source": PNG, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


orig_sha = sha(PNG)
jid = approved("photoshop.resize_to_preset", preset="square-1080")
res = R.run_job(PID, jid, ROOT)
out_png = os.path.join(ROOT, PID, "05_Exports", "Poster_Square1080_v01.png")
check("resize to a preset: the job is done, the PNG is delivered at exactly the preset's size, and a preview is in 04_Previews", res["ok"] and Image.open(out_png).size == (1080, 1080) and res["preview"].startswith("04_Previews/") and os.path.isfile(os.path.join(ROOT, PID, res["preview"])), str(res))
check("Photoshop (the fake) was handed the COPY inside the project, never the original; the original is unchanged", CALLS[-1]["jsx"].count(_s := json.dumps(os.path.join(ROOT, PID, "02_Assets", "poster.png").replace("\\", "/"))) >= 1 and PNG.replace("\\", "/") not in CALLS[-1]["jsx"] and sha(PNG) == orig_sha)
jid = approved("photoshop.export_preview", format="jpg", max_side=600)
res = R.run_job(PID, jid, ROOT)
check("a proof of a 1200x800 file at long side 600 is delivered as a 600x400 JPG", res["ok"] and Image.open(os.path.join(ROOT, PID, "05_Exports", "Poster_PsPreview_v01.jpg")).size == (600, 400), str(res))
jid = approved("photoshop.inspect")
res = R.run_job(PID, jid, ROOT)
rep = json.load(open(os.path.join(ROOT, PID, "05_Exports", "Poster_PsInspect_v01.json"), encoding="utf-8"))
check("inspect delivers the report Photoshop gave (size, mode, profile, text layers, fonts) and a note", res["ok"] and rep["width_px"] == 1200 and rep["fonts"] == ["SakkalMajalla"] and "1200x800" in res["notes"][0], str(res))
check("an unapproved Photoshop job runs nothing: the fake was not called for it", (lambda n: (R.plan_job(PID, "photoshop.inspect", {"source": PSD}, root=ROOT), refused(R.run_job, PID, R.list_jobs(PID, ROOT)[-1]["job"], ROOT), len(CALLS) == n)[2])(len(CALLS)))

print("\n-- when Photoshop fails, nothing is delivered --")
def failing(kind):
    def f(jsx_text, workdir, result_path, timeout=None):
        if kind == "raise":
            raise P.PhotoshopError("Photoshop did not finish within 7 minutes (a dialog may be waiting in the app); the job was stopped")
        if kind == "errors":
            return {"steps": {}, "errors": ["open: The file is corrupt"], "app": "photoshop"}
        if kind == "stepfail":
            return {"steps": {"render": {"error": "Error 8800: General Photoshop error"}}, "errors": ["render: Error 8800"], "app": "photoshop"}
        if kind == "nofile":
            return {"steps": {"render": {"width_px": 10, "height_px": 10, "profile": "x"}}, "errors": [], "app": "photoshop"}
    return f


for kind, expect in (("raise", "did not finish"), ("errors", "corrupt"), ("stepfail", "8800"), ("nofile", "not there")):
    P.TRANSPORT = failing(kind)
    before = sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports")))
    jid = approved("photoshop.resize_to_preset", preset="story-1080x1920")
    r = R.run_job(PID, jid, ROOT)
    check(f"'{kind}': the job fails with Photoshop's sentence, delivers nothing new, leaves no staging folder, and the original is unchanged",
          not r["ok"] and expect in r["error"] and sorted(os.listdir(os.path.join(ROOT, PID, "05_Exports"))) == before and not os.path.exists(os.path.join(ROOT, PID, "_job", jid, "staging")) and sha(PNG) == orig_sha, str(r))
P.TRANSPORT = fake_ps

print("\n-- the real transport (no Photoshop needed: the worker is stubbed) --")
class FakePopen:
    last = None

    def __init__(self, cmd, **kw):
        FakePopen.last = cmd
        self.pid, self.returncode, self.mode = 4242, 0, FakePopen.mode

    def communicate(self, timeout=None):
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired("w", timeout)
        if self.mode in ("flaky", "always_disconnected"):
            FakePopen.n += 1
            if self.mode == "always_disconnected" or FakePopen.n == 1:
                self.returncode = 1
                return b"", b"pywintypes.com_error: (-2147417848, 'The object invoked has disconnected from its clients.', None, None)"
            json.dump({"steps": {}, "errors": [], "app": "photoshop"}, open(FakePopen.result, "w"))
            return b"", b""
        if self.mode == "fail":
            self.returncode = 1
            return b"", b"Traceback...\npywintypes.com_error: Invalid class string"
        json.dump({"steps": {}, "errors": [], "app": "photoshop"}, open(FakePopen.result, "w"))
        return b"", b""


real_popen, real_run, real_running = subprocess.Popen, subprocess.run, C.running
killed = []
subprocess.Popen = FakePopen
subprocess.run = lambda cmd, **kw: killed.append(cmd) or type("X", (), {"stdout": "", "stderr": "", "returncode": 0})()
try:
    wd = os.path.join(TMP, "w1")
    FakePopen.result = os.path.join(wd, "r.json")
    FakePopen.mode = "ok"
    C.running = lambda app: False
    r = P.default_transport("x", wd, FakePopen.result, timeout=5)
    check("the worker runs the script in its own process; Photoshop was not running, so the worker is told to quit it afterwards", r["errors"] == [] and FakePopen.last[-1] == "--quit" and FakePopen.last[2] == "worker" and FakePopen.last[3] == "photoshop")
    C.running = lambda app: True
    P.default_transport("x", wd, FakePopen.result, timeout=5)
    check("when Photoshop was already running (the designer's own session) it is NOT told to quit", "--quit" not in FakePopen.last)
    FakePopen.mode = "timeout"
    try:
        P.default_transport("x", wd, FakePopen.result, timeout=60)
        msg = ""
    except P.PhotoshopError as ex:
        msg = str(ex)
    check("a stuck Photoshop is killed (the whole process tree) after the timeout and the sentence says a dialog may be waiting", "1 minutes" in msg and "dialog" in msg and any("taskkill" in c for c in killed[0]), msg)
    FakePopen.mode = "fail"
    try:
        P.default_transport("x", wd, FakePopen.result, timeout=5)
        msg = ""
    except P.PhotoshopError as ex:
        msg = str(ex)
    check("a worker that cannot start Photoshop gives a sentence with the last line of its error, not a traceback", msg.startswith("Photoshop could not run the script") and "Invalid class string" in msg and "Traceback" not in msg, msg)
    C.running = lambda app: False
    FakePopen.mode, FakePopen.n = "flaky", 0
    r = P.default_transport("x", wd, FakePopen.result, timeout=5)
    check("a connection that dies because the app was still closing from the last job is retried once, after the app has gone, and then works", r["errors"] == [] and FakePopen.n == 2)
    FakePopen.mode, FakePopen.n = "always_disconnected", 0
    try:
        P.default_transport("x", wd, FakePopen.result, timeout=5)
        msg = ""
    except P.PhotoshopError as ex:
        msg = str(ex)
    check("but only once: a second disconnect is reported as a sentence (no endless retry)", FakePopen.n == 2 and msg.startswith("Photoshop could not run the script") and "disconnected" in msg, msg)
    waits = []
    seq = iter([True, True, False])
    C.running = lambda app: next(seq, False)
    real_sleep = C.time.sleep
    C.time.sleep = lambda x: waits.append(x)
    try:
        gone = C.wait_gone("photoshop", 5)
    finally:
        C.time.sleep = real_sleep
    check("after closing an app it started, the transport waits until Photoshop's process is really gone", gone is True and len(waits) == 2)
finally:
    subprocess.Popen, subprocess.run, C.running = real_popen, real_run, real_running

print("\n-- registration, preferences, the page --")
check("the three operations are registered as Photoshop operations, once", all(k in R.OPERATIONS and R.OPERATIONS[k]["app"] == "photoshop" for k in ("photoshop.inspect", "photoshop.export_preview", "photoshop.resize_to_preset")) and (P.register() or True) and list(R.OPERATIONS).count("photoshop.inspect") == 1)
check("the default proof size and the colour profile come from the preferences profile", P._max_side_default() == 1600 and P._rgb_profile() == "sRGB IEC61966-2.1")
saved = P._prefs
P._prefs = lambda: None
check("without a readable profile the defaults still work (1600 px, sRGB), but a preset cannot be found and the refusal says why", P._max_side_default() == 1600 and "could not be read" in (refused(P._find_preset, "square-1080") or ""))
P._prefs = saved
src_text = open(str(DAVID_HOME / "design_photoshop.py"), encoding="utf-8").read()
check("nothing in the module can write the original: no save of the source, no os.remove/shutil.move of a designer's file, no model call", "ollama" not in src_text.lower() and "shutil.move" not in src_text and "os.remove(source" not in src_text)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
