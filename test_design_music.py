"""
test_design_music.py -- tests for design_music.py (music.generate): the ACE-Step workflow, planning and refusals, the automatic measurements on tracks of known behaviour, delivery with a FAKE
generator, and the start/stop of the real transport with ComfyUI faked. No model is loaded. Run: python test_design_music.py
"""
from david_config import DAVID_HOME
import sys, os, re, json, shutil, tempfile, hashlib
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import numpy as np
import soundfile as sf
from PIL import Image
import design_gpu as GPU
import design_generate as G
import design_music as M
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dmus_test_")
ROOT = os.path.join(TMP, "design")
GPU._wait_loaded = lambda *a, **k: True        # the hand-back's real wait is tested in test_design_video.py
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-13", root=ROOT)
AR = "\u0634\u0627\u0648\u0631\u0645\u0627"
SR = 22050


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


def clicks(bpm, seconds=15, amp=0.15, bed=0.02):
    """clicks on the beat over a low continuous bed (real music has no silent stretches between beats)"""
    y = (np.random.default_rng(2).standard_normal(int(SR * seconds)) * bed).astype(np.float32)
    step = int(SR * 60 / bpm)
    rng = np.random.default_rng(1)
    for i in range(0, len(y) - 2000, step):
        y[i:i + 1500] += (rng.standard_normal(1500) * np.exp(-np.arange(1500) / 250)).astype(np.float32) * amp
    return np.clip(y, -1, 1)


def write(path, y):
    sf.write(path, y, SR, format="WAV")        # the analysis reads by content, so the .mp3 name is only a name here


D = os.path.join(TMP, "tracks"); os.makedirs(D)
T = {"click120": clicks(120), "silent": np.zeros(SR * 15, dtype=np.float32), "clipped": np.clip(np.sin(np.arange(SR * 15) * 0.05) * 3, -1, 1).astype(np.float32), "quiet": clicks(120, amp=0.0008, bed=0.0004)}
for n, y in T.items():
    write(os.path.join(D, n + ".mp3"), y)

print("-- the workflow --")
wf = M.ace_workflow("pop", 15, 9, "pre")
check("the workflow is the one measured on 13 Oct: ACE-Step checkpoint, instrumental lyrics, zeroed negative, shift 5, 50 steps, cfg 5, euler/simple, seconds as a float, seed, SaveAudioMP3 on node 8",
      wf["1"]["inputs"]["ckpt_name"] == "ace_step_v1_3.5b.safetensors" and wf["2"]["inputs"]["lyrics"] == "[instrumental]" and wf["3"]["class_type"] == "ConditioningZeroOut" and wf["5"]["inputs"]["shift"] == 5.0
      and wf["6"]["inputs"]["steps"] == 50 and wf["6"]["inputs"]["cfg"] == 5.0 and wf["6"]["inputs"]["sampler_name"] == "euler" and wf["4"]["inputs"]["seconds"] == 15.0 and isinstance(wf["4"]["inputs"]["seconds"], float)
      and wf["6"]["inputs"]["seed"] == 9 and wf["8"]["class_type"] == "SaveAudioMP3" and wf["2"]["inputs"]["tags"] == "pop")

print("\n-- planning and refusals --")
base = {"prompt": "arabic pop, middle eastern percussion, darbuka, upbeat dance, lute and synth hook, energetic, instrumental", "name": "Reel Music"}
pl = lambda **prm: refused(R.plan_job, PID, "music.generate", {**base, **prm}, root=ROOT)
check("refusals: Arabic letters, a request for vocals or lyrics, an empty prompt, 7 candidates, 0 candidates, 4 or 61 seconds, a tempo of 30 or 300",
      "Arabic" in (pl(prompt="music " + AR) or "") and "instrumental" in (pl(prompt="pop with vocals") or "") and pl(prompt="lyrics about food") and pl(prompt=" ") and pl(count=7) and pl(count=0)
      and pl(seconds=4) and pl(seconds=61) and pl(bpm=30) and pl(bpm=300))
p1 = R.plan_job(PID, "music.generate", base, root=ROOT)
check("the output follows the naming standard: ReelMusic_Music_v01.mp3, no input file", p1["outputs"] == ["ReelMusic_Music_v01.mp3"] and p1["inputs"] == [])
st = " | ".join(p1["steps"])
check("the plan says the card is cleared with Mode E and restored, ComfyUI is stopped again, the tempo is only roughly followed, and the sound is for the manager's ears", "Mode E" in st and "stopped again" in st and "only roughly" in st and "your ears" in st, st[:200])
pb = R.plan_job(PID, "music.generate", {**base, "bpm": 128, "count": 3}, root=ROOT)
check("a target tempo is added to the description as '128 bpm' and the plan names three candidates", ", 128 bpm" in " | ".join(pb["steps"]) and "3 candidate instrumental tracks" in " | ".join(pb["steps"]) and len(pb["outputs"]) == 3)
check("a tempo already written in the description is not added twice", " | ".join(R.plan_job(PID, "music.generate", {**base, "prompt": "pop, 128 bpm, claps", "bpm": 128}, root=ROOT)["steps"]).count("128 bpm") >= 1
      and M._tags({"prompt": "pop, 128 bpm, claps", "bpm": 128}).count("bpm") == 1)

print("\n-- the measurements on tracks of known behaviour --")
a = {n: M.analyze_track(os.path.join(D, n + ".mp3"), 120) for n in T}
check("a steady 120 BPM click track measures about 120 BPM, is not quiet, silent or clipped, and is 'ok' against a 120 target", a["click120"]["verdict"] == "ok" and abs(a["click120"]["tempo"] - 120) <= 6 and a["click120"]["seconds"] == 15.0, a["click120"])
check("a silent track is flagged for silence (and as very quiet)", "silence" in a["silent"]["verdict"] and "quiet" in a["silent"]["verdict"], a["silent"]["verdict"])
check("a clipped track is flagged for clipping", "clipping" in a["clipped"]["verdict"], a["clipped"]["verdict"])
check("a very quiet track is flagged as very quiet", "very quiet" in a["quiet"]["verdict"], a["quiet"]["verdict"])
check("the tempo check forgives the half / double ambiguity (60 or 240 against 120 is no error) and reports a real miss (81 against 128 is 27%)",
      M.tempo_error(60, 120) == 0 and M.tempo_error(240, 120) == 0 and abs(M.tempo_error(81, 128) - 0.27) < 0.01 and M.tempo_error(100, None) is None)
off = M.analyze_track(os.path.join(D, "click120.mp3"), 175)
check("a track far from the asked tempo is flagged with both numbers", "BPM" in off["verdict"] and "175" in off["verdict"], off["verdict"])

print("\n-- end to end with a fake generator --")
SPECS = []
MODE = {"short": False}


def fake_gen(spec, workdir):
    SPECS.append(spec)
    names = ["click120", "silent", "clipped"]
    out = []
    for i in range(spec["count"] - (1 if MODE["short"] else 0)):
        f = os.path.join(workdir, f"track_{i + 1:02d}.mp3")
        write(f, T[names[i]])
        out.append(f)
    return {"mp3s": out, "seconds": 40, "server_started": True, "card": "mode E"}


M.TRANSPORT = fake_gen


def approved(**prm):
    pln = R.plan_job(PID, "music.generate", {**base, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(count=3, bpm=120, seed=2001, name="Three Tracks"), ROOT)
outs = [os.path.join(ROOT, PID, "05_Exports", f"ThreeTracks_Music_v0{i}.mp3") for i in (1, 2, 3)]
check("three tracks are delivered; the generator got the description with '120 bpm', 15 s, seed 2001 and count 3", res["ok"] and all(os.path.isfile(o) for o in outs) and SPECS[-1]["count"] == 3
      and SPECS[-1]["seconds"] == 15 and SPECS[-1]["seed"] == 2001 and SPECS[-1]["tags"].endswith(", 120 bpm"), str(res)[:250])
nt = res["notes"]
check("the notes give seeds 2001, 2002, 2003 and the card hand-over; v01 is ok, v02 is flagged (silence), v03 is flagged (clipping); v01 is named as closest to 120 BPM",
      "seeds 2001, 2002, 2003" in nt[0] and "mode E" in nt[0] and nt[1].endswith("ok") and "silence" in nt[2] and "clipping" in nt[3] and "closest to 120 BPM: v01" in nt[-1], nt)
sh = Image.open(os.path.join(ROOT, PID, res["preview"]))
check("the preview is a loudness sheet with a row per candidate", sh.size[1] >= 3 * 70, sh.size)
MODE["short"] = True
bad = R.run_job(PID, approved(count=2, name="Short"), ROOT)
check("if the generator returns fewer tracks than planned, nothing is delivered", not bad["ok"] and "missing or empty" in str(bad), str(bad)[:200])
MODE["short"] = False

print("\n-- the real transport (ComfyUI faked) --")
CD = os.path.join(TMP, "comfy"); os.makedirs(os.path.join(CD, "output", "audio"))
G.COMFY_DIR = CD
CALLS, PROMPTS = [], []
G._start_server = lambda: CALLS.append("start") or "proc"
G._stop_server = lambda proc: CALLS.append("stop")
G._is_up = lambda: False


def fake_api(path, data=None, timeout=30):
    if path == "/prompt":
        PROMPTS.append(data["prompt"]); CALLS.append("prompt")
        n = len(PROMPTS)
        write(os.path.join(CD, "output", "audio", f"a_{n}.mp3"), T["click120"])
        return {"prompt_id": f"p{n}"}
    if path.startswith("/history/"):
        pid = path.split("/")[-1]; n = int(pid[1:])
        return {pid: {"status": {"status_str": "success"}, "outputs": {"8": {"audio": [{"filename": f"a_{n}.mp3", "subfolder": "audio"}]}}}}
    CALLS.append("api " + path)
    return {}


class FakeDR:
    def __init__(self): self.mode, self.log = "A", []
    def get_mode(self): return self.mode
    def set_mode(self, m): self.log.append(m); self.mode = m


G._api = fake_api
M.time.sleep = lambda s: None
fdr = FakeDR()
GH = GPU.heavy_job
M.design_gpu.heavy_job = lambda: GH(fdr)
wd = os.path.join(TMP, "wd"); os.makedirs(wd)
r = M.default_music({"tags": "pop", "seconds": 15, "seed": 50, "count": 2}, wd)
check("the real transport: clears the card, starts ComfyUI, runs one job per candidate with seed and seed+1, stops ComfyUI, restores the mode",
      CALLS == ["start", "prompt", "prompt", "stop"] and fdr.log == ["E", "A"] and len(r["mp3s"]) == 2 and PROMPTS[0]["6"]["inputs"]["seed"] == 50 and PROMPTS[1]["6"]["inputs"]["seed"] == 51 and r["card"] == "mode E", (CALLS, fdr.log))

src_text = open(str(DAVID_HOME / "design_music.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a file of the designer's, and the operation is registered with the others",
      "os.remove(" not in src_text and "shutil.move" not in src_text and "music.generate" in R.OPERATIONS and "import design_music" in open(str(DAVID_HOME / "design_ops.py"), encoding="utf-8").read())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
