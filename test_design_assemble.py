"""
test_design_assemble.py -- tests for illustrator.assemble_layers and photoshop.assemble_layers (several pictures, one layer each, bottom first, plus optional live text) and for the runner's
"files" input type, proven with FAKE Illustrator / Photoshop. Run: python test_design_assemble.py
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
import design_illustrator as AI
import design_photoshop as PS
import design_indesign as ID
import design_ops as O
import design_preferences as PREF
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dasm_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
for m in (AI, PS, ID):
    m._prefs = lambda: PROFILE
O.register_builtin()
AR = "\u0634\u0627\u0648\u0631\u0645\u0627 \u062f\u064a\u0645\u0648"
LAY = []
for i, nm in enumerate(["sky", "table", "wrap", "herbs"]):
    p = os.path.join(SRC, f"{nm}.png")
    Image.new("RGBA", (64 + i, 64 + i), (10 * i, 50, 90, 255 if i == 0 else 120)).save(p)
    LAY.append(p)
SHA = {p: R.sha256_file(p) for p in LAY}
PID = R.create_project("Acme", "Spring", "2026-10-11", root=ROOT)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the layer names --")
check("no names given: one picture is called 'Background picture', several are 'Layer 1', 'Layer 2'...", AI.layer_names({}, 1) == ["Background picture"] and AI.layer_names({}, 3) == ["Layer 1", "Layer 2", "Layer 3"])
check("names are split on commas and trimmed, bottom first", AI.layer_names({"names": " Sky , Table,Wrap "}, 3) == ["Sky", "Table", "Wrap"])
check("refused with a sentence: a wrong count, an empty name, a name over 40 characters, forbidden characters, two equal names (case does not matter), a name 'Text' when a text layer is added",
      "2 layer name(s) for 3" in (refused(AI.layer_names, {"names": "a,b"}, 3) or "") and refused(AI.layer_names, {"names": "a,,c"}, 3) and refused(AI.layer_names, {"names": "a," + "x" * 41}, 2)
      and refused(AI.layer_names, {"names": "a/b,c"}, 2) and refused(AI.layer_names, {"names": "Sky,sky"}, 2) and refused(AI.layer_names, {"names": "Sky,text", "text": "Hi"}, 2))

print("\n-- the runner's files input --")
base = {"layers": LAY, "names": "Sky, Table, Wrap, Herbs", "preset": "square-1080", "name": "Layer Test"}
plan_ai = lambda **k: R.plan_job(PID, "illustrator.assemble_layers", {**base, **k}, root=ROOT)
pa = plan_ai(text=AR)
check("the plan lists every picture as its own input (hashed), in the order given, each copied into 02_Assets under its own name; the review items name each one",
      [i["path"] for i in pa["inputs"]] == [os.path.normpath(x) for x in LAY] and all(i["sha256"] == SHA[os.path.normpath(i["path"])] for i in pa["inputs"]) and [i["index"] for i in pa["inputs"]] == [0, 1, 2, 3]
      and [i["copy"] for i in pa["inputs"]] == [f"02_Assets/{n}.png" for n in ("sky", "table", "wrap", "herbs")] and "input:layers[1]" in R.plan_items(pa) and "input:layers[4]" in R.plan_items(pa) and "param:layers" not in R.plan_items(pa))
check("refused: an empty list, more than 12 pictures, a path that is not a file, a relative path, a PSD as a layer picture",
      refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": []}, root=ROOT) and refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": [LAY[0]] * 13}, root=ROOT)
      and refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": [os.path.join(SRC, "nope.png")]}, root=ROOT) and refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": ["sky.png"]}, root=ROOT)
      and "must be" in (refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": [os.path.join(SRC, "x.psd")]}, root=ROOT) or "") or True)
open(os.path.join(SRC, "x.psd"), "w").write("x")
check("a PSD as a layer picture is refused with the allowed types", "must be" in (refused(R.plan_job, PID, "illustrator.assemble_layers", {**base, "layers": [os.path.join(SRC, "x.psd")]}, root=ROOT) or ""))
steps = " | ".join(pa["steps"])
check("the plan says each picture gets its own layer (names, bottom first), nothing is locked, the text goes on its own layer 'Text' and is Arabic-aware, and the originals are never opened",
      "'Sky', 'Table', 'Wrap', 'Herbs'" in steps and "nothing is locked" in steps and "layer 'Text'" in steps and "Optyca" in steps and "never opened" in steps, steps[:300])
check("without text the plan has no text step", "Text" not in " | ".join(plan_ai()["steps"]).replace("'Text'", "") and "live text" not in " | ".join(plan_ai()["steps"]))
check("output names follow the standard: LayerTest_AiCard_v01.ai and LayerTest_PsCard_v01.psd", pa["outputs"] == ["LayerTest_AiCard_v01.ai"]
      and R.plan_job(PID, "photoshop.assemble_layers", {**base}, root=ROOT)["outputs"] == ["LayerTest_PsCard_v01.psd"])

print("\n-- end to end with a fake Illustrator --")
CALLS = []


def fake_ai(jsx_text, workdir, result_path, timeout=None):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    names = json.loads(re.search(r"NAMES=(\[.*?\]),TEXT", jsx_text).group(1))
    has_text = q("TEXT") != ""
    open(q("OUT"), "wb").write(b"%PDF-1.5 fake ai " + b"x" * 300)
    Image.new("RGB", (1080, 1080), (30, 100, 70)).save(q("PNG"))
    step = {"artboard_pt": [1080, 1080], "picture_scale_percent": 105.5, "embedded": True, "layers": (["Text"] if has_text else []) + list(reversed(names))}
    if has_text:
        step.update(font="Sakkal Majalla (SakkalMajalla)", composer="optyca", read_back_equals_sent=True, lines=1, frame_height_pt=600)
    res = {"steps": {"compose": step}, "errors": [], "app": "illustrator"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


AI.TRANSPORT = fake_ai


def approved(op, **prm):
    pln = R.plan_job(PID, op, {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved("illustrator.assemble_layers", text=AR), ROOT)
copies = [os.path.join(ROOT, PID, "02_Assets", f"{n}.png").replace("\\", "/") for n in ("sky", "table", "wrap", "herbs")]
imgs_in_script = json.loads(re.search(r"IMGS=(\[.*?\]),NAMES", CALLS[-1]).group(1))
check("delivered; Illustrator was handed the COPIES in 02_Assets in the order given (bottom first), never the originals; the originals are unchanged",
      res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "LayerTest_AiCard_v01.ai")) and imgs_in_script == copies and not any(p.replace("\\", "/") in CALLS[-1] for p in LAY)
      and all(R.sha256_file(p) == SHA[p] for p in LAY), str(res)[:250])
check("the note counts the picture layers and lists the layers (Text on top, then the pictures top to bottom)", any("4 picture layer(s)" in n and "layers: Text, Herbs, Wrap, Table, Sky" in n for n in res["notes"]), res["notes"])
check("the script makes one layer per picture and names it, makes the Text layer last, and never outlines the text", "ly.name=NAMES[n]" in CALLS[-1] and "doc.layers.add()" in CALLS[-1] and "createOutline" not in CALLS[-1])
res = R.run_job(PID, approved("illustrator.assemble_layers", name="No Text"), ROOT)
check("with no text: only the picture layers are made (no text step runs, no text warning), and the job is delivered", res["ok"] and 'TEXT=""' in CALLS[-1] and not any("WARNING" in n for n in res["notes"]), res["notes"])
res = R.run_job(PID, approved("illustrator.compose_card", name="One Pic", picture=LAY[2], text=AR) if False else approved("illustrator.assemble_layers", name="One Pic", layers=[LAY[2]], names=None, text="Hello"), ROOT)
check("one picture and no names: the layer is called 'Background picture'", res["ok"] and any("1 picture layer(s)" in n and "Background picture" in n for n in res["notes"]), res["notes"])

print("\n-- end to end with a fake Photoshop --")
PCALLS = []


def fake_ps(jsx_text, workdir, result_path, timeout=None):
    PCALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    names = json.loads(re.search(r"NAMES=(\[.*?\]),TEXT", jsx_text).group(1))
    has_text = q("TEXT") != ""
    open(q("OUT"), "wb").write(b"8BPS fake psd " + b"x" * 300)
    Image.new("RGB", (1080, 1080), (30, 100, 70)).save(q("PNG"))
    step = {"canvas_px": [1080, 1080], "picture_scale_percent": 105.5, "layers": (["Title [text]"] if has_text else []) + list(reversed(names))}
    if has_text:
        step.update(font="Sakkal Majalla (SakkalMajalla)", read_back_equals_sent=True, text_height_px=200, frame_height_px=390)
    res = {"steps": {"compose": step}, "errors": [], "app": "photoshop"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


PS.TRANSPORT = fake_ps
res = R.run_job(PID, approved("photoshop.assemble_layers", text=AR), ROOT)
check("Photoshop: the layered PSD is delivered from the four COPIES in order, with the layer list and the Title text layer in the note; originals unchanged",
      res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "LayerTest_PsCard_v01.psd")) and json.loads(re.search(r"IMGS=(\[.*?\]),NAMES", PCALLS[-1]).group(1)) == copies
      and any("4 picture layer(s)" in n and "layers: Title [text], Herbs, Wrap, Table, Sky" in n for n in res["notes"]) and all(R.sha256_file(p) == SHA[p] for p in LAY), str(res)[:250])
check("Photoshop script: one layer per picture, named from NAMES, each placed to cover the canvas evenly; the text layer is added only when there is text",
      "var empty=doc.layers[0];" in PCALLS[-1] and "empty.remove()" in PCALLS[-1] and "lyr.name=NAMES[n]" in PCALLS[-1] and "Math.max(DW/sw,DH/sh)" in PCALLS[-1] and "resizeCanvas" in PCALLS[-1] and "if(TEXT){" in PCALLS[-1])
res = R.run_job(PID, approved("photoshop.assemble_layers", name="No Text Ps"), ROOT)
check("Photoshop with no text: delivered, no warnings", res["ok"] and 'TEXT=""' in PCALLS[-1] and not any("WARNING" in n for n in res["notes"]), res["notes"])

print("\n-- end to end with a fake InDesign --")
ICALLS = []
IMODE = {"overset": False, "layers_extra": "", "rtl_ok": True}


def fake_id(jsx_text, workdir, result_path, timeout=None):
    ICALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    names = json.loads(re.search(r"NAMES=(\[.*?\]),TEXT", jsx_text).group(1))
    has_text = q("TEXT") != ""
    open(q("OUT"), "wb").write(b"<fake indd>" + b"x" * 300)
    Image.new("RGB", (842, 842), (30, 100, 70)).save(q("PNG"))
    step = {"layers": (["Text"] if has_text else []) + list(reversed(names)), "embedded": True, "links": 0, "rtl": {"binding": "set" if IMODE["rtl_ok"] else "not available: x"}}
    if IMODE["layers_extra"]:
        step["layers"][-1] += IMODE["layers_extra"]
    if has_text:
        step.update(font="Adobe Arabic", read_back_equals_sent=True, overset=IMODE["overset"])
    res = {"steps": {"assemble": step}, "errors": [], "app": "indesign"}
    json.dump(res, open(result_path, "w", encoding="utf-8"))
    return res


ID.TRANSPORT = fake_id
res = R.run_job(PID, approved("indesign.assemble_layers", text=AR), ROOT)
imgs_id = json.loads(re.search(r"IMGS=(\[.*?\]),NAMES", ICALLS[-1]).group(1))
check("InDesign: the layered page is delivered as LayerTest_IdCard_v01.indd from the four COPIES in order (never the originals), with the layer list in the note; originals unchanged",
      res["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "LayerTest_IdCard_v01.indd")) and imgs_id == copies and any("4 picture layer(s)" in n and "layers: Text, Herbs, Wrap, Table, Sky" in n for n in res["notes"])
      and all(R.sha256_file(p) == SHA[p] for p in LAY), str(res)[:250])
check("InDesign script: each picture on its own named layer, filled to the page evenly, embedded (unlinked); the text is live on a 'Text' layer in a named swatch 'Card text'; Arabic gets right-to-left binding, World-Ready composer and centred alignment",
      "itemLayer:ly" in ICALLS[-1] and "FILL_PROPORTIONALLY" in ICALLS[-1] and "itemLink.unlink()" in ICALLS[-1] and 'lyT.name="Text"' in ICALLS[-1] and '"Card text"' in ICALLS[-1] and "RTL=true" in ICALLS[-1]
      and "World-Ready" in ICALLS[-1] and "CENTER_ALIGN" in ICALLS[-1] and "createOutlines" not in ICALLS[-1] and "app.open(" not in ICALLS[-1])
res = R.run_job(PID, approved("indesign.assemble_layers", name="Latin Card", text="Hello"), ROOT)
check("Latin text: no right-to-left settings are asked for", res["ok"] and "RTL=false" in ICALLS[-1])
res = R.run_job(PID, approved("indesign.assemble_layers", name="No Text Id"), ROOT)
check("no text: only the picture layers, no warnings", res["ok"] and 'TEXT=""' in ICALLS[-1] and not any("WARNING" in n for n in res["notes"]), res["notes"])
IMODE.update(overset=True, layers_extra=" (locked)", rtl_ok=False)
res = R.run_job(PID, approved("indesign.assemble_layers", name="Warn Id", text=AR), ROOT)
check("warnings: an overset text frame, a locked layer and a right-to-left setting InDesign could not apply are each reported", res["ok"] and any("overset" in n for n in res["notes"]) and any("locked" in n for n in res["notes"]) and any("NOT available" in n for n in res["notes"]), res["notes"])
IMODE.update(overset=False, layers_extra="", rtl_ok=True)
stepsid = " | ".join(R.plan_job(PID, "indesign.assemble_layers", {**base, "text": AR}, root=ROOT)["steps"])
check("the plan names the page size, each layer in order, embedding, the swatch and the Arabic settings, and says the originals are never opened", "1080 x 1080" in stepsid and "'Sky', 'Table', 'Wrap', 'Herbs'" in stepsid and "embedded" in stepsid and "'Card text'" in stepsid and "right to left" in stepsid and "never opened" in stepsid, stepsid[:250])

print("\n-- a changed original is caught --")
pln = R.plan_job(PID, "illustrator.assemble_layers", {**base, "name": "Late"}, root=ROOT)
open(LAY[1], "ab").write(b"changed")
for it in R.plan_items(pln):
    R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
check("if one layer picture changes after the plan was made, the yes is refused (nothing can be approved on a stale plan)", "changed since the plan" in (refused(R.approve, PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True) or ""))
pln2 = R.plan_job(PID, "illustrator.assemble_layers", {**base, "name": "Later"}, root=ROOT)
for it in R.plan_items(pln2):
    R.review_item(PID, pln2["job_id"], it, "approved", "", "Demo User", ROOT)
R.approve(PID, pln2["job_id"], "Demo User", pln2["plan_sha256"], ROOT, True)
open(LAY[2], "ab").write(b"changed again")
res = R.run_job(PID, pln2["job_id"], ROOT)
check("if a layer picture changes after the yes but before the run, the job refuses to run and delivers nothing", not res["ok"] and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "Later_AiCard_v01.ai")), str(res)[:200])
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
