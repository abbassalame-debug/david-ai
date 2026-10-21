"""
test_design_generate.py -- tests for design_generate.py (generate.image): planning, refusals, delivery with a FAKE generator, and the start/stop logic of the real transport with the
ComfyUI calls faked (no ComfyUI is started, no model is loaded). Run: python test_design_generate.py
"""
from david_config import DAVID_HOME
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
import design_generate as G
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dgen_test_")
ROOT = os.path.join(TMP, "design")
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-11", root=ROOT)
AR = "\u0634\u0627\u0648\u0631\u0645\u0627"


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the workflow --")
w = G.qwen_workflow("a wrap", "text", 832, 1216, 20, 5, "pre")
check("the workflow is the proven Qwen-Image one: fp8 model, qwen_image text encoder and VAE, shift 3.1, euler/simple, cfg 2.5; size, steps, seed, prefix and both prompts are where they should be",
      w["1"]["inputs"]["unet_name"] == "qwen_image_fp8_e4m3fn.safetensors" and w["2"]["inputs"]["type"] == "qwen_image" and w["4"]["inputs"]["shift"] == 3.1 and w["8"]["inputs"]["cfg"] == 2.5
      and w["8"]["inputs"]["steps"] == 20 and w["8"]["inputs"]["seed"] == 5 and (w["7"]["inputs"]["width"], w["7"]["inputs"]["height"]) == (832, 1216) and w["10"]["inputs"]["filename_prefix"] == "pre"
      and w["5"]["inputs"]["text"] == "a wrap" and w["6"]["inputs"]["text"] == "text")
check("every size is a multiple of 16 (the model's grid) and the draft sizes are smaller than the standard ones",
      all(x % 16 == 0 for d in G.ASPECTS.values() for wh in d.values() for x in wh) and G.ASPECTS["draft"]["square"][0] < G.ASPECTS["standard"]["square"][0])
check("the negative prompt always forbids letters, words, logos and watermarks", all(t in G.NEGATIVE_BASE for t in ("text", "letters", "words", "watermark", "logo")))

print("\n-- planning --")
base = {"prompt": "a shawarma wrap on a dark wooden table, warm light", "name": "Wrap Hero"}
pl = lambda **prm: refused(R.plan_job, PID, "generate.image", {**base, **prm}, root=ROOT)
check("a prompt with Arabic letters is refused with the reason and what to do instead (set the words in Illustrator or Photoshop)", "Arabic" in (pl(prompt="a sign saying " + AR) or "") and "Illustrator or Photoshop" in (pl(prompt=AR) or ""))
check("refusals: empty prompt, bad shape, bad size, steps out of range, seed out of range, an unknown name",
      pl(prompt="  ") and pl(shape="round") and pl(size="huge") and pl(steps=3) and pl(steps=99) and pl(seed=-1) and pl(seed=2 ** 32))
check("a long professional prompt is accepted (up to 1,500 characters) but not longer; other text fields keep their 500-character limit", pl(prompt="a " * 700) is None and "1 to 1500" in (pl(prompt="a" * 1501) or "") and "1 to 500" in (pl(name="n" * 501) or ""))
pn = R.plan_job(PID, "generate.image", base, root=ROOT)
check("the output follows the naming standard: WrapHero_GenImage_v01.png; no input file is involved", pn["outputs"] == ["WrapHero_GenImage_v01.png"] and pn["inputs"] == [], (pn["outputs"], pn["inputs"]))
steps = " | ".join(pn["steps"])
check("the plan says ComfyUI is started only if needed and stopped again, gives the size and steps, says the picture never contains text, and says the seed is kept",
      "start it for this job and stop it again" in steps and "1024 x 1024 px" in steps and "20 steps" in steps and "never contain text" in steps and "seed" in steps, steps[:250])
pd = R.plan_job(PID, "generate.image", {**base, "size": "draft", "shape": "portrait", "steps": 15}, root=ROOT)
check("draft + portrait + 15 steps is planned as 512 x 768 px, 15 steps", "512 x 768 px" in " | ".join(pd["steps"]) and "15 steps" in " | ".join(pd["steps"]))

print("\n-- end to end with a fake generator --")
SPECS = []
MODE = {"fail": None, "started": True}


def fake_gen(spec, workdir):
    SPECS.append(spec)
    if MODE["fail"] == "raise":
        raise G.GenerateError("ComfyUI reported an error: out of memory")
    if MODE["fail"] == "nofile":
        return {"png": os.path.join(workdir, "missing.png"), "seconds": 1, "server_started": True}
    p = os.path.join(workdir, "generated.png")
    Image.new("RGB", (spec["width"], spec["height"]), (20, 90, 60)).save(p)
    return {"png": p, "seconds": 7, "server_started": MODE["started"]}


G.TRANSPORT = fake_gen


def approved(**prm):
    pln = R.plan_job(PID, "generate.image", {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(seed=42), ROOT)
png = os.path.join(ROOT, PID, "05_Exports", "WrapHero_GenImage_v01.png")
check("the picture is delivered to 05_Exports with the preview in 04_Previews; the generator got the prompt, the forbidden-text negative prompt, 1024 x 1024, 20 steps and the seed asked for",
      res["ok"] and os.path.isfile(png) and Image.open(png).size == (1024, 1024) and res["preview"].startswith("04_Previews/") and SPECS[-1]["seed"] == 42 and SPECS[-1]["steps"] == 20
      and SPECS[-1]["negative"] == G.NEGATIVE_BASE and SPECS[-1]["prompt"] == base["prompt"], str(res))
check("the note gives the size, steps, seed, seconds and that ComfyUI was started and stopped for the job", any("seed 42" in n and "7 s" in n and "started for this job and stopped again" in n for n in res["notes"]), res["notes"])
MODE["started"] = False
res = R.run_job(PID, approved(name="Wrap Two"), ROOT)
check("with no seed asked a random one is used and written in the note (so the picture can be made again); a ComfyUI that was already running is reported as left running",
      res["ok"] and isinstance(SPECS[-1]["seed"], int) and f"seed {SPECS[-1]['seed']}" in res["notes"][0] and "already running and was left running" in res["notes"][0], res["notes"])
check("two jobs without a seed do not get the same seed", SPECS[-1]["seed"] != SPECS[-2]["seed"])
res = R.run_job(PID, approved(name="Wrap Three", size="draft", shape="landscape"), ROOT)
check("draft landscape is made at 768 x 512 with 12 steps", res["ok"] and (SPECS[-1]["width"], SPECS[-1]["height"], SPECS[-1]["steps"]) == (768, 512, 12), SPECS[-1])

print("\n-- when it fails, nothing is delivered --")
MODE["fail"] = "raise"
res = R.run_job(PID, approved(name="Wrap Four"), ROOT)
check("a generator error becomes the job's sentence and nothing is delivered", not res["ok"] and "out of memory" in str(res) and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "WrapFour_GenImage_v01.png")), str(res)[:200])
MODE["fail"] = "nofile"
res = R.run_job(PID, approved(name="Wrap Five"), ROOT)
check("a picture that is not there is caught and nothing is delivered", not res["ok"] and "not there" in str(res) and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "WrapFive_GenImage_v01.png")), str(res)[:200])

print("\n-- the real transport's start/stop logic (ComfyUI calls faked) --")
calls = []
G._start_server = lambda: calls.append("start") or "proc"
G._stop_server = lambda proc: calls.append("stop")


def run_prompt_ok(wf, timeout):
    calls.append("run")
    src = os.path.join(TMP, "comfy_out.png")
    Image.new("RGB", (64, 64), (1, 2, 3)).save(src)
    return src


def run_prompt_bad(wf, timeout):
    calls.append("run")
    raise G.GenerateError("ComfyUI reported an error: boom")


G._api = lambda *a, **k: calls.append("api") or {}
spec = {"prompt": "x", "negative": "y", "width": 64, "height": 64, "steps": 8, "seed": 1}
wd = os.path.join(TMP, "wd")
os.makedirs(wd)
G._run_prompt = run_prompt_ok
G._is_up = lambda: False
calls.clear()
r = G.default_generate(spec, wd)
check("ComfyUI not running: it is started, the job runs, the picture is copied into the job folder, and ComfyUI is stopped again", calls == ["start", "run", "stop"] and os.path.isfile(r["png"]) and r["server_started"] is True, calls)
G._is_up = lambda: True
calls.clear()
r = G.default_generate(spec, wd)
check("ComfyUI already running: nothing is started and nothing is stopped", calls == ["run"] and r["server_started"] is False, calls)
G._is_up = lambda: False
G._run_prompt = run_prompt_bad
calls.clear()
try:
    G.default_generate(spec, wd)
    err = None
except G.GenerateError as e:
    err = str(e)
check("a failed job still stops the ComfyUI that this job started (and tries to interrupt it first)", err and "boom" in err and calls[0] == "start" and calls[-1] == "stop" and "api" in calls, calls)

print("\n-- Z-Image Turbo as an engine, and several candidates (13 Oct) --")
zw = G.zimage_workflow("a wrap", 832, 1216, 7, "pre")
check("the Z-Image workflow is the one measured on 13 Oct: z_image_turbo model, qwen_3_4b encoder of type lumina2, ae VAE, shift 3, 8 steps, cfg 1, res_multistep/simple, a zeroed negative, SaveImage on node 10",
      zw["1"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors" and zw["2"]["inputs"]["clip_name"] == "qwen_3_4b.safetensors" and zw["2"]["inputs"]["type"] == "lumina2" and zw["3"]["inputs"]["vae_name"] == "ae.safetensors"
      and zw["4"]["inputs"]["shift"] == 3.0 and zw["8"]["inputs"]["steps"] == 8 and zw["8"]["inputs"]["cfg"] == 1.0 and zw["8"]["inputs"]["sampler_name"] == "res_multistep"
      and zw["6"]["class_type"] == "ConditioningZeroOut" and zw["10"]["class_type"] == "SaveImage" and zw["8"]["inputs"]["seed"] == 7)
p_default = R.plan_job(PID, "generate.image", {**base, "name": "Plain"}, root=ROOT)
check("with no engine asked the plan is still Qwen-Image, one picture (nothing existing changed)", "Qwen-Image" in " | ".join(p_default["steps"]) and p_default["outputs"] == ["Plain_GenImage_v01.png"])
p_z = R.plan_job(PID, "generate.image", {**base, "name": "Fast", "engine": "zimage"}, root=ROOT)
check("the Z-Image plan says so, gives 8 steps, and warns that 'no text' is only asked for in the prompt and is not guaranteed", "Z-Image Turbo, 8 steps" in " | ".join(p_z["steps"]) and "not guaranteed" in " | ".join(p_z["steps"]), " | ".join(p_z["steps"])[:300])
p_n = R.plan_job(PID, "generate.image", {**base, "name": "Many", "count": 3, "engine": "zimage"}, root=ROOT)
check("three candidates: three outputs v01..v03, the plan says candidates with seed, seed+1 and a contact sheet", p_n["outputs"] == ["Many_GenImage_v01.png", "Many_GenImage_v02.png", "Many_GenImage_v03.png"]
      and "3 candidate pictures" in " | ".join(p_n["steps"]) and "contact sheet" in " | ".join(p_n["steps"]))
check("refusals: an unknown engine, 0 candidates, more than 6 candidates", pl(engine="dalle") and pl(count=0) and pl(count=7))
check("Arabic letters are refused with the Z-Image engine too", "Arabic" in (pl(prompt="a sign " + AR, engine="zimage") or ""))


def fake_multi(spec, workdir):
    SPECS.append(spec)
    pngs = []
    for i in range(spec["count"]):
        f = os.path.join(workdir, f"g{i}.png")
        Image.new("RGB", (spec["width"], spec["height"]), (40 * (i + 1), 20, 90)).save(f)
        pngs.append(f)
    return {"png": pngs[0], "pngs": pngs, "seconds": 11, "server_started": True}


G.TRANSPORT = fake_multi
res = R.run_job(PID, approved(name="Cands", count=3, engine="zimage", seed=42), ROOT)
outs = [os.path.join(ROOT, PID, "05_Exports", f"Cands_GenImage_v0{i}.png") for i in (1, 2, 3)]
check("three candidates are delivered as v01..v03; the generator was asked for engine zimage and count 3; the notes list seeds 42, 43, 44",
      res["ok"] and all(os.path.isfile(o) for o in outs) and SPECS[-1]["engine"] == "zimage" and SPECS[-1]["count"] == 3 and "seeds 42, 43, 44" in res["notes"][0], str(res)[:250])
prev_w = Image.open(os.path.join(ROOT, PID, res["preview"])).size[0]
check("the preview is ONE contact sheet wider than a single picture (all candidates side by side)", prev_w > 3 * 300, prev_w)
res1 = R.run_job(PID, approved(name="Solo", engine="zimage"), ROOT)
check("one picture with Z-Image: delivered as v01, notes say zimage", res1["ok"] and os.path.isfile(os.path.join(ROOT, PID, "05_Exports", "Solo_GenImage_v01.png")) and res1["notes"][0].startswith("zimage:"), res1["notes"])


def fake_short(spec, workdir):
    f = os.path.join(workdir, "only.png")
    Image.new("RGB", (64, 64)).save(f)
    return {"png": f, "pngs": [f], "seconds": 1, "server_started": True}


G.TRANSPORT = fake_short
resx = R.run_job(PID, approved(name="Short", count=2), ROOT)
check("if the generator returns fewer candidates than planned, nothing is delivered", not resx["ok"] and not os.path.exists(os.path.join(ROOT, PID, "05_Exports", "Short_GenImage_v01.png")), str(resx)[:200])

WFS = []
G._is_up = lambda: True
G._run_prompt = lambda wf, timeout: (WFS.append(wf), run_prompt_ok(wf, timeout))[1]
G.default_generate({"prompt": "a cup", "negative": "n", "width": 64, "height": 64, "steps": 8, "seed": 100, "engine": "zimage", "count": 2}, wd)
check("the real transport makes one run per candidate with seed, seed+1, sends the Z-Image workflow, and adds the 'no text' request to the prompt",
      len(WFS) == 2 and WFS[0]["8"]["inputs"]["seed"] == 100 and WFS[1]["8"]["inputs"]["seed"] == 101 and WFS[0]["1"]["inputs"]["unet_name"].startswith("z_image") and "no text" in WFS[0]["5"]["inputs"]["text"])
WFS.clear()
G.default_generate({"prompt": "a cup", "negative": "n", "width": 64, "height": 64, "steps": 8, "seed": 5}, wd)
check("a spec with no engine still runs the Qwen-Image workflow (the layered chain is unaffected)", len(WFS) == 1 and WFS[0]["1"]["inputs"]["unet_name"].startswith("qwen_image") and WFS[0]["6"]["inputs"]["text"] == "n")
src_text = open(str(DAVID_HOME / "design_generate.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file, and no Arabic text can reach the model", "os.remove(" not in src_text and "shutil.move" not in src_text and "ARABIC_RE.search(p[\"prompt\"])" in src_text)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
