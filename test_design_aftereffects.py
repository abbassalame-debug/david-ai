"""
test_design_aftereffects.py -- tests for design_aftereffects.py (aftereffects.animate_layers) with a FAKE After Effects: the script text, planning, refusals (including "After Effects is open"),
delivery, warnings, failure paths, the preview-frame helper and the real transport's launch/timeout/close logic with the process calls faked. No Adobe app is opened.
Run: python test_design_aftereffects.py
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
import design_aftereffects as AE
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dae_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PSD = os.path.join(SRC, "layered card.psd")
open(PSD, "wb").write(b"8BPS fake psd " + b"x" * 300)
PNG = os.path.join(SRC, "flat.png")
Image.new("RGB", (64, 64)).save(PNG)
SHA = R.sha256_file(PSD)
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-11", root=ROOT)
AE.running = lambda app: False


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the script --")
j = AE.build_jsx(r"C:\out dir\r.json", src=r"C:\in dir\layered 'x'.psd", out=r"C:\out dir\v.mp4", aep=r"C:\out dir\v.aep", motion="parallax", seconds=5, fps=30, template=AE.H264)
check("every path is an escaped literal (plain ASCII, spaces and quotes in the path); no placeholder is left", j.isascii() and not re.search(r"__[A-Z0-9]+__", j))
check("the PSD is imported as a COMPOSITION (every layer kept separate), the duration and frame rate are set, the project is saved before the render, the H.264 template is applied and the result is written",
      "ImportAsType.COMP" in j and "comp.duration=SECS" in j and "comp.frameRate=FPS" in j and j.index("proj.save(") < j.index("rq.render()") and "om.applyTemplate(TEMPLATE)" in j and "H.264 - Match Render Settings - 15 Mbps" in j and "W(" in j)
check("each motion is in the script: parallax by depth, zoom_in by depth, fade_in staggered bottom first; the title (top layer called Title or Text) only fades in, except in fade_in where it is last",
      all(m in j for m in ('MOTION==="fade_in"', 'MOTION==="parallax"', 'MOTION==="zoom_in"')) and "isTitle" in j and "(n-i2)*0.5" in j and "depth*28" in j)
check("the session is closed without saving the project's changes after the render, and After Effects is quit only when asked", "CloseOptions.DO_NOT_SAVE_CHANGES" in j and "if(true){app.quit();}" in j
      and "if(false){app.quit();}" in AE.build_jsx("r.json", src="a.psd", out="o.mp4", aep="o.aep", motion="zoom_in", seconds=2, fps=24, template=AE.H264, quit=False))

print("\n-- planning --")
base = {"source": PSD}
pl = lambda **prm: refused(R.plan_job, PID, "aftereffects.animate_layers", {**base, **prm}, root=ROOT)
check("refusals: a flat PNG (only layered Photoshop files), a bad motion, seconds 1 and 21, fps 60", "layered Photoshop files only" in (pl(source=PNG) or "") and pl(motion="spin") and pl(seconds=1) and pl(seconds=21) and pl(fps="60"))
AE.running = lambda app: True
check("planning is refused with a clear sentence when After Effects is open", "After Effects is open" in (pl() or ""))
AE.running = lambda app: False
pn = R.plan_job(PID, "aftereffects.animate_layers", base, root=ROOT)
check("outputs follow the standard: LayeredCard_AeMotion_v01.mp4 and .aep; the PSD is the one hashed input", pn["outputs"] == ["LayeredCard_AeMotion_v01.mp4", "LayeredCard_AeMotion_v01.aep"] and len(pn["inputs"]) == 1, pn["outputs"])
steps = " | ".join(pn["steps"])
check("the plan says the original is never opened, After Effects must not already be open and is closed again, every layer stays separate, the duration/rate/motion, the render and the .aep, and the preview",
      "never opened" in steps and "must not be open already" in steps and "every layer kept separate" in steps and "5 seconds at 30 frames" in steps and "H.264" in steps and ".aep" in steps and "preview" in steps and "background barely moves" in steps, steps[:300])
check("each motion has its own sentence in the plan", all(k in " | ".join(R.plan_job(PID, "aftereffects.animate_layers", {**base, "motion": m}, root=ROOT)["steps"]) for m, k in (("zoom_in", "slow push in"), ("fade_in", "one by one"))))

print("\n-- end to end with a fake After Effects --")
CALLS = []
MODE = {"status": "3019", "no_aep": False, "no_mp4": False, "errors": None, "step_error": None}


def fake_ae(jsx_text, workdir, result_path):
    CALLS.append(jsx_text)
    q = lambda name: json.loads(re.search(name + r"=(\"(?:[^\"\\]|\\.)*\")", jsx_text).group(1))
    if MODE["errors"]:
        return {"steps": {}, "errors": MODE["errors"], "app": "aftereffects"}
    if MODE["step_error"]:
        return {"steps": {"animate": {"error": MODE["step_error"]}}, "errors": [], "app": "aftereffects"}
    out, aep = q("OUT"), q("AEP")
    if not MODE["no_mp4"]:
        open(out, "wb").write(b"\x00\x00\x00 ftypmp42 fake " + b"x" * 400)
    if not MODE["no_aep"]:
        open(aep, "wb").write(b"RIFX fake aep " + b"x" * 300)
    return {"steps": {"animate": {"comp": "c", "width": 1080, "height": 1080, "seconds": 5, "fps": 30, "motion": "parallax", "layers": ["Title: title fade", "Wrap: drift 14 px", "Background: drift 0 px"],
                                  "render_status": MODE["status"], "out": out, "aep": aep}}, "errors": [], "app": "aftereffects"}


AE.TRANSPORT = fake_ae
AE.frame_from_video = lambda video, png, at=0.5: (Image.new("RGB", (1080, 1080), (1, 2, 3)).save(png) or png)


COUNT = [0]


def approved(**prm):
    COUNT[0] += 1
    src = PSD if COUNT[0] == 1 else os.path.join(SRC, f"layered card {COUNT[0]}.psd")
    if not os.path.exists(src):
        shutil.copy2(PSD, src)
    pln = R.plan_job(PID, "aftereffects.animate_layers", {**base, "source": src, **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(), ROOT)
ex = os.path.join(ROOT, PID, "05_Exports")
check("delivered: the MP4 and the editable .aep in 05_Exports and a picture in 04_Previews; After Effects was handed the COPY in 02_Assets, never the original; the original is unchanged",
      res["ok"] and os.path.isfile(os.path.join(ex, "LayeredCard_AeMotion_v01.mp4")) and os.path.isfile(os.path.join(ex, "LayeredCard_AeMotion_v01.aep")) and res["preview"].startswith("04_Previews/")
      and json.dumps(os.path.join(ROOT, PID, "02_Assets", "layered card.psd").replace("\\", "/")) in CALLS[-1] and PSD.replace("\\", "/") not in CALLS[-1] and R.sha256_file(PSD) == SHA, str(res)[:300])
check("the note gives size, length, rate, motion and what each layer does, with no warning", any("1080 x 1080 px, 5 s at 30 fps, motion parallax" in n and "Wrap: drift 14 px" in n for n in res["notes"]) and not any("WARNING" in n for n in res["notes"]), res["notes"])
res = R.run_job(PID, approved(motion="zoom_in", seconds=8, fps="24"), ROOT)
check("the chosen motion, seconds and frame rate reach the script", res["ok"] and 'MOTION="zoom_in"' in CALLS[-1] and "SECS=8.0" in CALLS[-1] and "FPS=24" in CALLS[-1], CALLS[-1][:60])
MODE["status"] = "3015"
res = R.run_job(PID, approved(), ROOT)
check("a render status other than done is a WARNING", res["ok"] and any("WARNING" in n and "3015" in n for n in res["notes"]), res["notes"])
MODE["status"] = "3019"
AE.frame_from_video = lambda video, png, at=0.5: None
res = R.run_job(PID, approved(), ROOT)
check("when no preview frame can be taken the job still delivers and says so", res["ok"] and res["preview"] is None and any("no preview picture" in n for n in res["notes"]), res["notes"])

print("\n-- when it fails, nothing is delivered --")
for what, key, expect in (("the project file is missing", "no_aep", "did not save the project"), ("the video is missing", "no_mp4", "not there")):
    MODE[key] = True
    before = set(os.listdir(ex))
    res = R.run_job(PID, approved(), ROOT)
    check(f"{what}: the job fails with a sentence and nothing new is delivered", not res["ok"] and expect in str(res) and set(os.listdir(ex)) == before, str(res)[:200])
    MODE[key] = False
MODE["errors"] = ["open: the file is damaged"]
res = R.run_job(PID, approved(), ROOT)
check("an error reported by After Effects becomes the job's sentence", not res["ok"] and "damaged" in str(res), str(res)[:200])
MODE.update(errors=None, step_error="the file did not import as a composition")
res = R.run_job(PID, approved(), ROOT)
check("a failed step becomes the job's sentence", not res["ok"] and "did not import as a composition" in str(res), str(res)[:200])
MODE["step_error"] = None

print("\n-- the real transport (process calls faked) --")
launched = []
killed = []
state = {"alive": False}
AE.running = lambda app: state["alive"]
AE.wait_until = lambda fn, seconds, every=2.0: fn() if seconds < 1000 and not state.get("hang") else False
real_popen, real_run = AE.subprocess.Popen, AE.subprocess.run


def fake_popen(cmd, **k):
    launched.append(cmd)
    with open(cmd[2], encoding="utf-8") as f:
        t = f.read()
    rp = json.loads(re.search(r"W\((\"(?:[^\"\\]|\\.)*\")", t).group(1))
    json.dump({"steps": {"animate": {}}, "errors": []}, open(rp, "w", encoding="utf-8"))
    return None


AE.subprocess.Popen = fake_popen
AE.subprocess.run = lambda cmd, **k: killed.append(cmd) or None
wd = os.path.join(TMP, "wd")
os.makedirs(wd)
rp = os.path.join(wd, "r.json")
jx = AE.build_jsx(rp, src="a.psd", out="o.mp4", aep="o.aep", motion="parallax", seconds=2, fps=24, template=AE.H264)
r = AE.default_transport(jx, wd, rp)
check("the real transport writes the script, starts AfterFX.exe -r on it and returns what the script wrote", launched and launched[0][1] == "-r" and launched[0][0] == AE.AE_EXE and launched[0][2].endswith("aftereffects_job.jsx") and r.get("errors") == [])
state["alive"] = True
try:
    AE.default_transport(jx, wd, rp)
    msg = None
except AE.AfterEffectsError as e:
    msg = str(e)
check("it refuses to start when After Effects is already open (it may hold the designer's own project)", msg and "After Effects is open" in msg and len(launched) == 1, msg)
state.update(alive=False, hang=True)
os.remove(rp)
try:
    AE.default_transport(jx, wd, rp)
    msg = None
except AE.AfterEffectsError as e:
    msg = str(e)
check("a session that never answers is killed and the job fails with a sentence", msg and "did not finish" in msg and any("AfterFX.exe" in " ".join(k) for k in killed), (msg, killed))
AE.subprocess.Popen, AE.subprocess.run = real_popen, real_run
src_text = open(str(DAVID_HOME / "design_aftereffects.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "shutil.move" not in src_text and "ollama" not in src_text.lower() and src_text.count("os.remove(") == 1)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
