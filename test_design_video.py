"""
test_design_video.py -- tests for design_video.py (video.image_to_video) and design_gpu.py: the LTX-2 workflow, planning and refusals, delivery with a FAKE generator (synthetic clips),
the automatic cut / jump check on clips whose behaviour is known, the card hand-over, and the start/stop of the real transport with ComfyUI faked. No model is loaded.
Run: python test_design_video.py
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


import av
import numpy as np
from fractions import Fraction
from PIL import Image
import design_gpu as GPU
import design_generate as G
import design_video as V
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dvid_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PNG = os.path.join(SRC, "Two Friends.png")
Image.new("RGB", (832, 1216), (200, 160, 40)).save(PNG)
import hashlib
H0 = hashlib.sha256(open(PNG, "rb").read()).hexdigest()
PSD = os.path.join(SRC, "x.psd")
open(PSD, "wb").write(b"8BPS")
_REAL_WAIT = GPU._wait_loaded
GPU._wait_loaded = lambda *a, **k: True        # the hand-back's real wait is tested on its own below
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-13", root=ROOT)
AR = "\u0634\u0627\u0648\u0631\u0645\u0627"


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


def make_mp4(path, frame_fn, n=49, size=(96, 160)):
    c = av.open(path, "w")
    s = c.add_stream("libx264", rate=24)
    s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
    s.options = {"crf": "10"}
    for i in range(n):
        fr = av.VideoFrame.from_ndarray(frame_fn(i, size), format="rgb24")
        for pkt in s.encode(fr):
            c.mux(pkt)
    for pkt in s.encode():
        c.mux(pkt)
    c.close()


def flat(level):
    return lambda i, size: np.full((size[1], size[0], 3), level, dtype=np.uint8)


def clean_fn(i, size):
    a = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    a[:, :, 0] = 100 + i
    a[:, :, 1] = 120
    return a


def cut_fn(i, size):
    return flat(40 if i < 30 else 210)(i, size)


def jump_fn(i, size):
    return flat(100 if i < 20 else 114)(i, size)


print("-- the workflow --")
wf = V.ltx_workflow("pic.png", "they laugh", 480, 832, 49, 24, 77, "pre")
check("the workflow is the one measured on 13 Oct: distilled fp8 checkpoint + Gemma encoder + audio VAE, the picture goes into the video latent (LTXVImgToVideoInplace) before the audio is joined, "
      "8 manual sigmas, cfg 1, euler, seed, size, frames, 24 fps, SaveVideo on node 19",
      wf["1"]["inputs"]["ckpt_name"] == "ltx-2-19b-distilled-fp8.safetensors" and wf["3"]["inputs"]["text_encoder"] == "gemma_3_12B_it_fp8_scaled.safetensors"
      and wf["21"]["class_type"] == "LTXVImgToVideoInplace" and wf["21"]["inputs"]["image"] == ["20", 0] and wf["20"]["inputs"]["image"] == "pic.png"
      and wf["9"]["inputs"]["video_latent"] == ["21", 0] and wf["12"]["inputs"]["sigmas"].count(",") == 8 and wf["10"]["inputs"]["cfg"] == 1.0 and wf["11"]["inputs"]["sampler_name"] == "euler"
      and wf["13"]["inputs"]["noise_seed"] == 77 and (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"], wf["7"]["inputs"]["length"]) == (480, 832, 49)
      and wf["6"]["inputs"]["frame_rate"] == 24.0 and wf["19"]["class_type"] == "SaveVideo" and wf["4"]["inputs"]["text"] == "they laugh")
check("every size is a multiple of 32 and every length is 8k+1 (the model's grid)", all(x % 32 == 0 for wh in V.SIZES.values() for x in wh) and all((f - 1) % 8 == 0 for f in V.FRAMES.values()))

print("\n-- planning and refusals --")
base = {"source": PNG, "prompt": "the two friends laugh and raise their cups, the camera pushes in slowly"}
pl = lambda **prm: refused(R.plan_job, PID, "video.image_to_video", {**base, **prm}, root=ROOT)
check("refusals: a Photoshop file instead of a picture, an empty prompt, 5 candidates, 0 candidates, 3 seconds (not measured), a square shape, a negative seed",
      "picture" in (pl(source=PSD) or "") and pl(prompt="  ") and pl(count=5) and pl(count=0) and pl(seconds="3") and pl(shape="square") and pl(seed=-1))
check("Arabic letters in the prompt are refused and the reason says to set the text in an Adobe app", "Arabic" in (pl(prompt="a sign " + AR) or "") and "Adobe" in (pl(prompt="a sign " + AR) or ""))
p1 = R.plan_job(PID, "video.image_to_video", base, root=ROOT)
check("the output follows the naming standard from the picture's name: TwoFriends_Video_v01.mp4", p1["outputs"] == ["TwoFriends_Video_v01.mp4"], p1["outputs"])
st = " | ".join(p1["steps"])
check("the plan says: the original is never opened; the card is cleared with Mode E and restored; ComfyUI is stopped again; about 2.5 minutes a clip; first frame is the picture; checked for cuts and jumps",
      "never opened" in st and "Mode E" in st and "stopped again" in st and "2.5 minutes" in st and "first frame is your picture" in st and "hard cut" in st, st[:200])
p3 = R.plan_job(PID, "video.image_to_video", {**base, "count": 3, "shape": "landscape", "seconds": "1"}, root=ROOT)
check("three candidates: v01..v03; landscape is planned as 832 x 480 and 1 second", p3["outputs"] == [f"TwoFriends_Video_v0{i}.mp4" for i in (1, 2, 3)] and "832 x 480" in " | ".join(p3["steps"]) and "1 s at 24 fps" in " | ".join(p3["steps"]))

print("\n-- the automatic check on clips of known behaviour --")
d = os.path.join(TMP, "clips"); os.makedirs(d)
make_mp4(os.path.join(d, "clean.mp4"), clean_fn); make_mp4(os.path.join(d, "cut.mp4"), cut_fn); make_mp4(os.path.join(d, "jump.mp4"), jump_fn); make_mp4(os.path.join(d, "still.mp4"), flat(120))
a_clean, a_cut, a_jump = (V.analyze_clip(os.path.join(d, n)) for n in ("clean.mp4", "cut.mp4", "jump.mp4"))
check("a calm clip is 'ok'; length 49 frames = 2.04 s", a_clean["verdict"] == "ok" and a_clean["frames"] == 49 and a_clean["seconds"] == 2.04, a_clean)
check("a clip that changes scene at frame 30 is a HARD CUT and says where (about 1.2 s)", "hard cut" in a_cut["verdict"] and "frame 30" in a_cut["verdict"], a_cut["verdict"])
check("a sudden move (a jump of 14 grey levels at frame 20) is flagged as a sudden jump to look at, not as a cut", "sudden jump" in a_jump["verdict"] and "hard cut" not in a_jump["verdict"], a_jump["verdict"])
check("a clip without a sound track is reported as having no audio", a_clean["audio"] is False)
a_still = V.analyze_clip(os.path.join(d, "still.mp4"))
check("a clip that hardly moves is 'ok' but says so: almost no movement, it looks like a still", a_still["verdict"].startswith("ok, but almost no movement"), a_still["verdict"])

print("\n-- end to end with a fake generator --")
SPECS = []
MODE = {"short": False}


def fake_gen(spec, workdir):
    SPECS.append(spec)
    kinds = [clean_fn, cut_fn, jump_fn, clean_fn]
    mp4s = []
    for i in range(spec["count"] - (1 if MODE["short"] else 0)):
        f = os.path.join(workdir, f"clip_{i + 1:02d}.mp4")
        make_mp4(f, kinds[i], n=spec["frames"], size=(96, 160))
        mp4s.append(f)
    return {"mp4s": mp4s, "seconds": 140, "server_started": True, "card": "mode E"}


V.TRANSPORT = fake_gen


def approved(**prm):
    pln = R.plan_job(PID, "video.image_to_video", {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(count=2, seed=500), ROOT)
mp = [os.path.join(ROOT, PID, "05_Exports", f"TwoFriends_Video_v0{i}.mp4") for i in (1, 2)]
check("two candidates are delivered; the generator got the copied picture, the prompt, 480 x 832, 49 frames, seed 500 and count 2",
      res["ok"] and all(os.path.isfile(m) for m in mp) and SPECS[-1]["count"] == 2 and SPECS[-1]["frames"] == 49 and (SPECS[-1]["width"], SPECS[-1]["height"]) == (480, 832) and SPECS[-1]["seed"] == 500
      and os.path.isfile(SPECS[-1]["image"]) and SPECS[-1]["image"] != PNG, str(res)[:300])
nt = " | ".join(res["notes"])
check("the notes give the seeds 500, 501, the card hand-over, and say v01 is ok and v02 is a hard cut", "seeds 500, 501" in nt and "mode E" in nt and "v01:" in nt and "ok" in res["notes"][1] and "hard cut" in res["notes"][2], res["notes"])
sh = Image.open(os.path.join(ROOT, PID, res["preview"]))
check("the preview is one sheet with a row per candidate (taller than a single row)", sh.size[1] >= 2 * 300, sh.size)
check("the original picture is untouched (same SHA-256 as before the job)", hashlib.sha256(open(PNG, "rb").read()).hexdigest() == H0)
MODE["short"] = True
bad = R.run_job(PID, approved(count=2, seed=9), ROOT)
check("if the generator returns fewer clips than planned, nothing is delivered", not bad["ok"] and "missing or empty" in str(bad), str(bad)[:200])
MODE["short"] = False

print("\n-- the card hand-over (design_gpu) --")


class FakeDR:
    def __init__(self, fail=False):
        self.mode, self.log, self.fail = "A", [], fail

    def get_mode(self):
        if self.fail:
            raise RuntimeError("no controller")
        return self.mode

    def set_mode(self, m):
        self.log.append(m)
        self.mode = m


dr = FakeDR()
with GPU.heavy_job(dr) as h:
    during = list(dr.log)
check("on entry the card is cleared with Mode E, on exit the previous mode (A) is restored", during == ["E"] and dr.log == ["E", "A"] and h.via == "mode E", dr.log)
dr2 = FakeDR()
try:
    with GPU.heavy_job(dr2):
        raise RuntimeError("job failed")
except RuntimeError:
    pass
check("the previous mode is restored even when the job fails", dr2.log == ["E", "A"], dr2.log)
WAITS = []
GPU._ollama = lambda path, body=None, timeout=20: {"models": [{"name": "qwen3.5:9b"}]} if len(WAITS) >= 2 else (WAITS.append(1) or {"models": []})
check("after the mode is restored, the hand-back waits until a language model shows as loaded (here after 2 polls) and returns True", _REAL_WAIT(45, sleep=lambda s: None) is True and len(WAITS) == 2, WAITS)
GPU._ollama = lambda path, body=None, timeout=20: {"models": []}
slept = []
check("and it gives up after the limit (45 s) when nothing loads, instead of hanging", _REAL_WAIT(45, sleep=lambda s: slept.append(s)) is False and sum(slept) >= 44, sum(slept))
UNLOADED = []
GPU._unload_all = lambda: UNLOADED.append(1) or []
with GPU.heavy_job(FakeDR(fail=True)) as h3:
    pass
check("if the controller cannot be used, the loaded models are unloaded directly and nothing is restored", UNLOADED == [1] and h3.via == "unload")

print("\n-- the real transport (ComfyUI faked) --")
CD = os.path.join(TMP, "comfy"); os.makedirs(os.path.join(CD, "input")); os.makedirs(os.path.join(CD, "output", "video"))
G.COMFY_DIR = CD
CALLS = []
G._start_server = lambda: CALLS.append("start") or "proc"
G._stop_server = lambda proc: CALLS.append("stop")
G._is_up = lambda: False
PROMPTS = []


def fake_api(path, data=None, timeout=30):
    if path == "/prompt":
        PROMPTS.append(data["prompt"])
        CALLS.append("prompt")
        n = len(PROMPTS)
        make_mp4(os.path.join(CD, "output", "video", f"out_{n}.mp4"), clean_fn, n=25)
        return {"prompt_id": f"p{n}"}
    if path.startswith("/history/"):
        pid = path.split("/")[-1]
        n = int(pid[1:])
        return {pid: {"status": {"status_str": "success"}, "outputs": {"19": {"images": [{"filename": f"out_{n}.mp4", "subfolder": "video"}]}}}}
    CALLS.append("api " + path)
    return {}


G._api = fake_api
time_sleep = V.time.sleep
V.time.sleep = lambda s: None
fdr = FakeDR()
GPU_heavy = GPU.heavy_job
V.design_gpu.heavy_job = lambda: GPU_heavy(fdr)
wd = os.path.join(TMP, "wd"); os.makedirs(wd)
r = V.default_video({"image": PNG, "prompt": "p", "width": 480, "height": 832, "frames": 25, "seed": 100, "count": 2}, wd)
check("the real transport: clears the card, starts ComfyUI, copies the picture into ComfyUI's input, runs one job per candidate with seed and seed+1, stops ComfyUI, restores the mode",
      CALLS == ["start", "prompt", "prompt", "stop"] and fdr.log == ["E", "A"] and len(r["mp4s"]) == 2 and PROMPTS[0]["13"]["inputs"]["noise_seed"] == 100 and PROMPTS[1]["13"]["inputs"]["noise_seed"] == 101
      and len(os.listdir(os.path.join(CD, "input"))) == 1 and r["card"] == "mode E", (CALLS, fdr.log))
G._is_up = lambda: True
CALLS.clear(); PROMPTS.clear()
V.default_video({"image": PNG, "prompt": "p", "width": 480, "height": 832, "frames": 25, "seed": 1, "count": 1}, wd)
check("with ComfyUI already running nothing is started or stopped", CALLS == ["prompt"], CALLS)
V.time.sleep = time_sleep

src_text = open(str(DAVID_HOME / "design_video.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a file of the designer's, and the operation is registered with the other operations",
      "os.remove(" not in src_text and "shutil.move" not in src_text and "video.image_to_video" in R.OPERATIONS and "import design_video" in open(str(DAVID_HOME / "design_ops.py"), encoding="utf-8").read())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
