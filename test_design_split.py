"""
test_design_split.py -- tests for design_split.py (generate.split_layers): the workflow, planning, refusals, ordering/recompose check and delivery with a FAKE splitter, and the real
transport's temp-file and start/stop handling with the ComfyUI calls faked (no ComfyUI is started, no model is loaded). Run: python test_design_split.py
"""
from david_config import DAVID_HOME
import sys, os, json, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image, ImageDraw
import design_generate as G
import design_split as S
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dsplit_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-11", root=ROOT)

W, H = 640, 640
bg = Image.new("RGBA", (W, H), (20, 80, 60, 255))
mid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(mid).ellipse([150, 300, 520, 520], fill=(230, 200, 140, 255))
top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(top).ellipse([400, 100, 560, 220], fill=(240, 60, 40, 200))
ORIG = S.recompose([bg, mid, top]).convert("RGB")
PIC = os.path.join(SRC, "hero.png")
ORIG.save(PIC)
SHA = R.sha256_file(PIC)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the workflow and the maths --")
w = S.layered_workflow("in.png", 4, 640, 640, 30, 9, "pre")
check("the workflow is ComfyUI's own: fp8 layered model + layered VAE + the qwen_image text encoder, VAE-encoded picture as a reference latent on both prompts, an empty layered latent with the layer count, shift 1, cfg 4, euler/simple, cut to a batch, decoded, saved",
      w["1"]["inputs"]["unet_name"] == "qwen_image_layered_fp8mixed.safetensors" and w["3"]["inputs"]["vae_name"] == "qwen_image_layered_vae.safetensors" and w["2"]["inputs"]["type"] == "qwen_image"
      and w["8"]["class_type"] == "ReferenceLatent" and w["9"]["class_type"] == "ReferenceLatent" and w["8"]["inputs"]["latent"] == ["5", 0] and w["10"]["inputs"]["shift"] == 1.0
      and w["11"]["inputs"]["layers"] == 4 and (w["11"]["inputs"]["width"], w["11"]["inputs"]["height"]) == (640, 640) and w["12"]["inputs"]["cfg"] == 4.0 and w["12"]["inputs"]["steps"] == 30 and w["12"]["inputs"]["seed"] == 9
      and w["13"]["class_type"] == "LatentCutToBatch" and w["13"]["inputs"]["dim"] == "t" and w["15"]["inputs"]["filename_prefix"] == "pre" and w["4"]["inputs"]["image"] == "in.png")
check("fit_size keeps the shape, lands near the target pixels and is a multiple of 16 on both sides", all(a % 16 == 0 and b % 16 == 0 for a, b in (S.fit_size(1024, 1024, 640), S.fit_size(832, 1216, 640), S.fit_size(1216, 832, 1024)))
      and S.fit_size(1024, 1024, 640) == (640, 640) and abs(S.fit_size(832, 1216, 640)[0] * S.fit_size(832, 1216, 640)[1] - 640 * 640) < 640 * 640 * 0.1)
a, how, diff = S.order_layers([bg, mid, top], ORIG)
check("layers given bottom first recompose the picture exactly (difference 0)", how == "as given" and diff == 0.0, (how, diff))
a, how, diff = S.order_layers([top, mid, bg], ORIG)
check("layers given top first are detected and put bottom first (the reorder is reported)", how == "reversed" and diff == 0.0 and a[0] is bg, (how, diff))

print("\n-- planning --")
base = {"picture": PIC, "name": "Hero Split"}
pl = lambda **prm: refused(R.plan_job, PID, "generate.split_layers", {**base, **prm}, root=ROOT)
tiny = os.path.join(SRC, "tiny.png")
Image.new("RGB", (100, 100)).save(tiny)
wide = os.path.join(SRC, "wide.png")
Image.new("RGB", (1600, 400)).save(wide)
bad = os.path.join(SRC, "x.psd")
open(bad, "w").write("x")
check("refusals: a picture under 256 px, one wider than 3:1, a PSD, layers 1 and 9, size huge, steps 5 and 200, a bad seed",
      pl(picture=tiny) and pl(picture=wide) and pl(picture=bad) and pl(layers=1) and pl(layers=9) and pl(size="huge") and pl(steps=5) and pl(steps=200) and pl(seed=-3))
pn = R.plan_job(PID, "generate.split_layers", base, root=ROOT)
check("output names: Layer01..Layer04 PNGs and a Layers JSON in the naming standard; the picture is one hashed input", pn["outputs"] == [f"HeroSplit_Layer{n:02d}_v01.png" for n in (1, 2, 3, 4)] + ["HeroSplit_Layers_v01.json"] and len(pn["inputs"]) == 1, pn["outputs"])
steps = " | ".join(pn["steps"])
check("the plan says the original is never opened, ComfyUI is started only if needed, the size and layer count, that it is slow, that the recompose check is made, and that the layers have no names yet",
      "never opened" in steps and "start it for this job" in steps and "640 x 640 px" in steps and "4 transparent layers" in steps and "slow" in steps and "put back on top" in steps and "no names yet" in steps, steps[:200])
p6 = R.plan_job(PID, "generate.split_layers", {**base, "layers": 6, "size": "standard"}, root=ROOT)
check("6 layers at standard size: 6 PNGs + JSON, 1024 x 1024 px, 50 steps", len(p6["outputs"]) == 7 and "1024 x 1024 px" in " | ".join(p6["steps"]) and "50 steps" in " | ".join(p6["steps"]))

print("\n-- end to end with a fake splitter --")
SPECS = []
MODE = {"frames": None, "count": None, "started": True}


def save_frames(workdir, imgs):
    out = []
    for i, im in enumerate(imgs):
        p = os.path.join(workdir, f"frame_{i:02d}.png")
        im.save(p)
        out.append(p)
    return out


def fake_split(spec, workdir):
    SPECS.append(spec)
    imgs = MODE["frames"] or [ORIG.convert("RGBA"), bg, mid, top]
    if MODE["count"] is not None:
        imgs = imgs[:MODE["count"]]
    return {"frames": save_frames(workdir, imgs), "seconds": 321, "server_started": MODE["started"]}


S.TRANSPORT = fake_split


def approved(**prm):
    pln = R.plan_job(PID, "generate.split_layers", {**base, "layers": 3, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(seed=11), ROOT)
ex = os.path.join(ROOT, PID, "05_Exports")
man = json.load(open(os.path.join(ex, "HeroSplit_Layers_v01.json"), encoding="utf-8")) if res["ok"] else {}
check("delivered: three transparent layer PNGs and the manifest in 05_Exports, a recomposed preview in 04_Previews; the splitter got the picture scaled to 640 x 640, 3 layers, 30 steps and the seed asked for; the original is unchanged",
      res["ok"] and all(os.path.isfile(os.path.join(ex, f"HeroSplit_Layer{n:02d}_v01.png")) for n in (1, 2, 3)) and res["preview"].startswith("04_Previews/") and (SPECS[-1]["width"], SPECS[-1]["height"]) == (640, 640)
      and SPECS[-1]["layers"] == 3 and SPECS[-1]["steps"] == 30 and SPECS[-1]["seed"] == 11 and R.sha256_file(PIC) == SHA, str(res)[:300])
check("every layer PNG is RGBA at 640 x 640; the first is the opaque bottom, the others have transparency", all(Image.open(os.path.join(ex, f"HeroSplit_Layer{n:02d}_v01.png")).mode == "RGBA" for n in (1, 2, 3))
      and Image.open(os.path.join(ex, "HeroSplit_Layer01_v01.png")).getchannel("A").getextrema() == (255, 255) and Image.open(os.path.join(ex, "HeroSplit_Layer02_v01.png")).getchannel("A").getextrema()[0] == 0)
check("the manifest names the order (bottom first), the size, the seed and each layer with default names; the note gives size, layers, steps, seed, seconds, order and a difference of 0",
      man.get("order") == "bottom first" and man["size"] == [640, 640] and man["seed"] == 11 and [x["name"] for x in man["layers"]] == ["Layer 1", "Layer 2", "Layer 3"] and man["recomposed_difference"] == 0.0
      and "seed 11" in res["notes"][0] and "321 s" in res["notes"][0] and "difference 0.0" in res["notes"][0] and "started for this job and stopped again" in res["notes"][0] and not any("WARNING" in n for n in res["notes"]), res["notes"])
MODE["frames"] = [ORIG.convert("RGBA"), top, mid, bg]
res = R.run_job(PID, approved(name="Rev Split"), ROOT)
l1 = Image.open(os.path.join(ex, "RevSplit_Layer01_v01.png"))
check("when the model returns the layers top first they are delivered bottom first (layer 1 is the opaque background) and the manifest says reversed", res["ok"] and l1.getchannel("A").getextrema() == (255, 255)
      and json.load(open(os.path.join(ex, "RevSplit_Layers_v01.json"), encoding="utf-8"))["model_order"] == "reversed", res["notes"])
MODE["frames"] = [ORIG.convert("RGBA"), Image.new("RGBA", (W, H), (255, 0, 0, 255)), Image.new("RGBA", (W, H), (0, 0, 255, 255)), Image.new("RGBA", (W, H), (0, 0, 0, 0))]
res = R.run_job(PID, approved(name="Bad Split"), ROOT)
check("layers that do not recompose into the picture are delivered but flagged WARNING with the difference; a completely empty layer is flagged too", res["ok"] and any("do not put back together" in n for n in res["notes"]) and any("layer 3 is empty" in n for n in res["notes"]), res["notes"])
MODE.update(frames=None, count=3)
res = R.run_job(PID, approved(name="Short Split"), ROOT)
check("a wrong number of pictures back from the splitter fails the job and delivers nothing", not res["ok"] and "expected" in str(res) and not os.path.exists(os.path.join(ex, "ShortSplit_Layer01_v01.png")), str(res)[:200])
MODE.update(count=None, started=False)
res = R.run_job(PID, approved(name="Run Split"), ROOT)
check("a ComfyUI that was already running is reported as left running", res["ok"] and "already running and was left running" in res["notes"][0], res["notes"])
check("two jobs without a seed get different seeds", SPECS[-1]["seed"] != SPECS[-2]["seed"])

print("\n-- the real transport (ComfyUI calls faked) --")
calls = []
S.COMFY_INPUT = os.path.join(TMP, "comfy_input")
G._start_server = lambda: calls.append("start") or "proc"
G._stop_server = lambda proc: calls.append("stop")
G._api = lambda *a, **k: calls.append("api") or {}
G._is_up = lambda: False
seen = {}


def run_all(wf, timeout, out_node):
    calls.append("run")
    seen["wf"], seen["node"], seen["input_exists"] = wf, out_node, os.path.isfile(os.path.join(S.COMFY_INPUT, wf["4"]["inputs"]["image"]))
    outs = []
    for i in range(4):
        p = os.path.join(TMP, f"comfy_out_{i}.png")
        Image.new("RGBA", (64, 64), (i, i, i, 255)).save(p)
        outs.append(p)
    return outs


G._run_prompt_all = run_all
wd = os.path.join(TMP, "wd")
os.makedirs(wd)
spec = {"image": PIC, "layers": 3, "width": 640, "height": 640, "steps": 30, "seed": 1}
r = S.default_split(spec, wd)
check("the picture is put in ComfyUI's input folder under a unique name for the run, the output node 15 is read, four frames are copied into the job folder, ComfyUI is started and stopped, and the temporary input is removed afterwards",
      calls == ["start", "run", "stop"] and seen["input_exists"] and seen["node"] == "15" and len(r["frames"]) == 4 and all(os.path.isfile(x) for x in r["frames"]) and r["server_started"] is True and os.listdir(S.COMFY_INPUT) == [], (calls, os.listdir(S.COMFY_INPUT)))


def run_bad(wf, timeout, out_node):
    calls.append("run")
    raise G.GenerateError("ComfyUI reported an error: out of memory")


G._run_prompt_all = run_bad
calls.clear()
try:
    S.default_split(spec, wd)
    err = None
except G.GenerateError as e:
    err = str(e)
check("a failed job still stops the ComfyUI this job started and removes the temporary input", err and "out of memory" in err and calls[0] == "start" and calls[-1] == "stop" and os.listdir(S.COMFY_INPUT) == [], calls)
src_text = open(str(DAVID_HOME / "design_split.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file (it only removes its own temporary input)", "shutil.move" not in src_text and "os.remove(" not in src_text and src_text.count("os.unlink(") == 1)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
