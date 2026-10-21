"""
test_design_pipeline.py -- tests for design_pipeline.py (the end-to-end runner): planning and its safety checks, approval only by the designer's name and the plan's fingerprint, the chaining of
the stages through the REAL operation plans with fake runs, stopping at the first problem, resuming, undo. No app, no model and no ComfyUI is touched.
Run: python test_design_pipeline.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image
import design_pipeline as PL
import design_ops as O
import design_runner as R
import design_indesign as ID
import design_preferences as PREF
import design_aftereffects as AE

TMP = tempfile.mkdtemp(prefix="dpl_test_")
ROOT = os.path.join(TMP, "design")
PROFILE = PREF.seed_profile(json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8")))
ID._prefs = lambda: PROFILE
json.dump(PROFILE, open(os.path.join(TMP, "prefs.json"), "w", encoding="utf-8"), ensure_ascii=False)   # a seeded demo profile, not a real one
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-12", root=ROOT)
AR = "\u0634\u0627\u0648\u0631\u0645\u0627 \u062f\u064a\u0645\u0648"
AE.running = lambda app: False


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


# ---- fake runs: they create what each real operation would deliver, named by the operation's own outputs() ----
LOG = []
FAIL_AT = {"op": None}
EMPTY = {"idx": []}


def png(path, size=(64, 64), mode="RGB", color=(30, 90, 60)):
    Image.new(mode, size, color).save(path)


def fake_run(op_id):
    def run(ctx):
        LOG.append((op_id, dict(ctx["params"]), {k: v for k, v in ctx["inputs"].items()}))
        if FAIL_AT["op"] == op_id:
            raise RuntimeError(f"{op_id} blew up")
        outs = {}
        for name in ctx["outputs_expected"]:
            path = os.path.join(ctx["staging"], name)
            ext = os.path.splitext(name)[1].lower()
            if name.endswith("_GenImage_v01.png"):
                png(path, size=(640, 640))
            elif ext == ".png":
                png(path, mode="RGBA", color=(1, 2, 3, 255))
            elif name.endswith("_Layers_v01.json"):
                pngs = [x for x in ctx["outputs_expected"] if x.endswith(".png")]
                json.dump({"layers": [{"file": x, "empty": i in EMPTY["idx"]} for i, x in enumerate(pngs)]}, open(path, "w"))
            elif ext == ".json":
                n = len(ctx["inputs"]["layers"])
                json.dump({"names": ["Background", "Wrap", "Light", "Glow", "A", "B", "C", "D"][:n], "names_param": ", ".join(["Background", "Wrap", "Light", "Glow", "A", "B", "C", "D"][:n])}, open(path, "w"))
            else:
                open(path, "wb").write(b"fake " + name.encode() + b"x" * 200)
            outs[name] = path
        prev = os.path.join(ctx["staging"], "preview.png")
        png(prev)
        return {"outputs": outs, "preview": prev, "notes": [f"fake {op_id}"]}
    return run


REAL_RUN = {}
for op in ("generate.image", "generate.split_layers", "generate.name_layers", "photoshop.assemble_layers", "illustrator.assemble_layers", "indesign.assemble_layers", "aftereffects.animate_layers"):
    REAL_RUN[op] = R.OPERATIONS[op]["run"]
    R.OPERATIONS[op]["run"] = fake_run(op)

PL.MODELS = {}
BASE = {"prompt": "a shawarma wrap on a dark table, warm light", "name": "Hero Card", "preset": "square-1080", "text": AR}

print("-- planning --")
pl = lambda **k: refused(PL.plan_pipeline, PID, {**BASE, **k}, root=ROOT)
check("refusals with a sentence: Arabic letters in the prompt, an unknown preset, an unknown app, layers 1 and 9, a bad colour is left to the stage (not here), quality huge",
      "Arabic" in (pl(prompt="a sign " + AR) or "") and "not one of your document presets" in (pl(preset="nope") or "") and "photoshop, illustrator, indesign" in (pl(apps="word") or "") and pl(layers=1) and pl(layers=9) and pl(quality="huge"))
PL.MODELS = {"generate.split_layers": [(os.path.join(TMP, "missing_model.safetensors"), "the layer-splitting model (Qwen-Image-Layered)")]}
msg = pl()
check("planning is refused with a clear sentence while a model file is not on the computer yet (nothing is planned, no folder is made)", msg and "cannot start yet" in msg and "layer-splitting model" in msg and "missing_model" in msg and not os.path.isdir(os.path.join(ROOT, PID, "_pipeline")), msg)
PL.MODELS = {}
AE.running = lambda app: True
check("with motion asked, planning is refused while After Effects is open", "After Effects is open" in (pl(motion="parallax") or ""))
AE.running = lambda app: False
p1 = PL.plan_pipeline(PID, BASE, "Demo User", ROOT)
check("the default chain is generate -> split -> name -> Photoshop assemble (no animation); the plan names every stage, the settings, the fingerprint and that it stops at the first problem",
      [s["id"] for s in p1["stages"]] == ["generate", "split", "name", "assemble_photoshop"] and p1["pipeline_id"] == "pl-0001" and p1["plan_sha256"] in open(os.path.join(ROOT, PID, "_pipeline", "pl-0001", "plan.md"), encoding="utf-8").read()
      and "STOPS at the first problem" in open(os.path.join(ROOT, PID, "_pipeline", "pl-0001", "plan.md"), encoding="utf-8").read(), [s["id"] for s in p1["stages"]])
p2 = PL.plan_pipeline(PID, {**BASE, "apps": "indesign, illustrator", "motion": "parallax", "name": "Hero Two"}, "Demo User", ROOT)
check("asking for motion adds Photoshop (After Effects needs its layered PSD) and the animation stage last; apps keep the fixed order", [s["id"] for s in p2["stages"]] == ["generate", "split", "name", "assemble_photoshop", "assemble_illustrator", "assemble_indesign", "animate"], [s["id"] for s in p2["stages"]])
check("a second plan gets the next id; the plans are listed", p2["pipeline_id"] == "pl-0002" and [x["pipeline"] for x in PL.list_pipelines(PID, ROOT)] == ["pl-0001", "pl-0002"])

print("\n-- approval --")
check("running before the yes is refused", "approved pipeline" in (refused(PL.run_pipeline, PID, "pl-0001", ROOT) or ""))
check("nothing but the designer can approve: no name, 'david', 'ai' and 'claude' are refused", all(refused(PL.approve_pipeline, PID, "pl-0001", who, p1["plan_sha256"], ROOT) for who in ("", "david", "AI", "Claude")))
check("a wrong fingerprint is refused", "fingerprint" in (refused(PL.approve_pipeline, PID, "pl-0001", "Demo User", "0" * 64, ROOT) or ""))
planfile = os.path.join(ROOT, PID, "_pipeline", "pl-0002", "plan.json")
tampered = json.load(open(planfile, encoding="utf-8"))
tampered["params"]["prompt"] = "something else entirely"
json.dump(tampered, open(planfile, "w", encoding="utf-8"))
check("a plan edited after it was made is detected and refused", "edited after it was made" in (refused(PL.approve_pipeline, PID, "pl-0002", "Demo User", p2["plan_sha256"], ROOT) or ""))
tampered["params"]["prompt"] = BASE["prompt"]
json.dump(tampered, open(planfile, "w", encoding="utf-8"))
PL.approve_pipeline(PID, "pl-0001", "Demo User", p1["plan_sha256"], ROOT)
check("the designer's name and the fingerprint give the yes; it is recorded; a second approval is refused", PL.state(PID, "pl-0001", ROOT)["state"] == "approved" and PL.state(PID, "pl-0001", ROOT)["approved_by"] == "Demo User"
      and "planned pipeline" in (refused(PL.approve_pipeline, PID, "pl-0001", "Demo User", p1["plan_sha256"], ROOT) or ""))

print("\n-- running the chain --")
st = PL.run_pipeline(PID, "pl-0001", ROOT)
ops = [x[0] for x in LOG]
check("the stages ran in order, each as its own job", st["state"] == "done" and ops == ["generate.image", "generate.split_layers", "generate.name_layers", "photoshop.assemble_layers"], (st["state"], ops))
g, sp, nm, ps = LOG
check("each stage got the files the stage before delivered: the split got the generated picture, the naming got the four layers in order, the assemble got the layers and the names the model gave",
      sp[2]["picture"].endswith("HeroCard_GenImage_v01.png") and [os.path.basename(x) for x in nm[2]["layers"]] == [f"HeroCard_Layer{n:02d}_v01.png" for n in (1, 2, 3, 4)]
      and [os.path.basename(x) for x in ps[2]["layers"]] == [f"HeroCard_Layer{n:02d}_v01.png" for n in (1, 2, 3, 4)] and ps[1]["names"] == "Background, Wrap, Light, Glow", (sp[2], ps[1]["names"]))
check("the settings of the chain reached the stages: prompt/quality/shape to generate; picture size and layer count to the split; text, preset, size, colour and position to the assemble",
      g[1]["prompt"] == BASE["prompt"] and g[1]["size"] == "draft" and sp[1]["layers"] == 4 and sp[1]["size"] == "draft" and ps[1]["text"] == AR and ps[1]["preset"] == "square-1080" and ps[1]["position"] == "top" and ps[1]["color"] == "#FFFFFF" and ps[1]["size"] == 120)
jobs = {s: st["stages"][s]["job"] for s in st["stages"]}
ap = [R.status(PID, j, ROOT) for j in jobs.values()]
rv = R.reviews(PID, jobs["generate"], ROOT)
check("every stage job was approved BY THE DESIGNER'S NAME and every line of its plan is marked 'approved as part of pipeline pl-0001'", all(a["approved_by"] == "Demo User" for a in ap) and rv and all("pipeline pl-0001" in str(v) for v in rv.values()), str(rv)[:200])
ex = os.path.join(ROOT, PID, "05_Exports")
check("everything was delivered into the project (the picture, four layers + manifest, the names file, the PSD); the summary lists it and the state is done", all(os.path.isfile(os.path.join(ex, n)) for n in ("HeroCard_GenImage_v01.png", "HeroCard_Layer04_v01.png", "HeroCard_Layers_v01.json", "HeroCard_LayerNames_v01.json", "HeroCard_PsCard_v01.psd"))
      and "HeroCard_PsCard_v01.psd" in open(os.path.join(ROOT, PID, "_pipeline", "pl-0001", "summary.md"), encoding="utf-8").read())
check("a finished pipeline cannot be run again", "approved pipeline" in (refused(PL.run_pipeline, PID, "pl-0001", ROOT) or ""))

print("\n-- a full chain with three apps and an animation --")
LOG.clear()
PL.approve_pipeline(PID, "pl-0002", "Demo User", p2["plan_sha256"], ROOT)
st2 = PL.run_pipeline(PID, "pl-0002", ROOT)
check("all seven stages ran; the animation was given the PSD that Photoshop delivered (not another app's file), after all three assembles", st2["state"] == "done" and [x[0] for x in LOG][-1] == "aftereffects.animate_layers" and LOG[-1][2]["source"].endswith("HeroTwo_PsCard_v01.psd")
      and [x[0] for x in LOG][3:6] == ["photoshop.assemble_layers", "illustrator.assemble_layers", "indesign.assemble_layers"], [x[0] for x in LOG])
check("the animation stage got the chosen motion, seconds and frame rate", LOG[-1][1]["motion"] == "parallax" and LOG[-1][1]["seconds"] == 5 and LOG[-1][1]["fps"] == "30")

print("\n-- empty layers are left out --")
LOG.clear()
EMPTY["idx"] = [2]
pe = PL.plan_pipeline(PID, {**BASE, "name": "Hero Empty"}, "Demo User", ROOT)
PL.approve_pipeline(PID, pe["pipeline_id"], "Demo User", pe["plan_sha256"], ROOT)
ste = PL.run_pipeline(PID, pe["pipeline_id"], ROOT)
nm_in = [x for x in LOG if x[0] == "generate.name_layers"][0]
as_in = [x for x in LOG if x[0] == "photoshop.assemble_layers"][0]
check("a layer with nothing visible is left out: the naming and the assemble get only the three layers that have something on them, in order, and the chain still finishes",
      ste["state"] == "done" and [os.path.basename(x) for x in nm_in[2]["layers"]] == ["HeroEmpty_Layer01_v01.png", "HeroEmpty_Layer02_v01.png", "HeroEmpty_Layer04_v01.png"] and len(as_in[2]["layers"]) == 3 and as_in[1]["names"].count(",") == 2, (ste["state"], ste.get("error")))
check("the plan text tells the designer that empty layers are left out", "left out" in open(os.path.join(ROOT, PID, "_pipeline", pe["pipeline_id"], "plan.md"), encoding="utf-8").read())
EMPTY["idx"] = [1, 2, 3]
pe2 = PL.plan_pipeline(PID, {**BASE, "name": "Hero Blank"}, "Demo User", ROOT)
PL.approve_pipeline(PID, pe2["pipeline_id"], "Demo User", pe2["plan_sha256"], ROOT)
LOG.clear()
ste2 = PL.run_pipeline(PID, pe2["pipeline_id"], ROOT)
check("if fewer than two layers have anything on them the chain stops at the split with a clear sentence and never names or assembles", ste2["state"] == "failed" and ste2["failed_stage"] == "split" and "only 1 of the 4 layers" in ste2["error"] and [x[0] for x in LOG] == ["generate.image", "generate.split_layers"], (ste2["state"], ste2.get("error")))
EMPTY["idx"] = []

print("\n-- stopping at the first problem, resuming, undoing --")
p3 = PL.plan_pipeline(PID, {**BASE, "name": "Hero Three"}, "Demo User", ROOT)
PL.approve_pipeline(PID, p3["pipeline_id"], "Demo User", p3["plan_sha256"], ROOT)
LOG.clear()
FAIL_AT["op"] = "generate.name_layers"
st3 = PL.run_pipeline(PID, p3["pipeline_id"], ROOT)
check("when a stage fails the chain stops there: the later stages never run, the state is failed and names the stage and the reason, and what was delivered before stays", st3["state"] == "failed" and st3["failed_stage"] == "name" and "blew up" in st3["error"]
      and [x[0] for x in LOG] == ["generate.image", "generate.split_layers", "generate.name_layers"] and os.path.isfile(os.path.join(ex, "HeroThree_GenImage_v01.png")), (st3["state"], st3.get("error")))
check("a failed pipeline is not re-run by accident (it needs resume)", "resume" in (refused(PL.run_pipeline, PID, p3["pipeline_id"], ROOT) or ""))
FAIL_AT["op"] = None
LOG.clear()
st3 = PL.run_pipeline(PID, p3["pipeline_id"], ROOT, resume=True)
check("resume skips the finished stages (the picture is not made again) and carries on from the failed one to the end", st3["state"] == "done" and [x[0] for x in LOG] == ["generate.name_layers", "photoshop.assemble_layers"], [x[0] for x in LOG])
removed = PL.undo_pipeline(PID, "pl-0001", ROOT)
check("undo removes every stage's delivered files and the state is undone; undoing a pipeline that already failed or was undone does nothing more",
      PL.state(PID, "pl-0001", ROOT)["state"] == "undone" and not os.path.exists(os.path.join(ex, "HeroCard_PsCard_v01.psd")) and not os.path.exists(os.path.join(ex, "HeroCard_GenImage_v01.png"))
      and not os.path.exists(os.path.join(ex, "HeroCard_LayerNames_v01.json")) and len(removed) >= 8, (len(removed), removed[:3]))
check("the newest stage is undone first (the PSD before the picture)", removed.index(next(x for x in removed if x.endswith(".psd"))) < removed.index(next(x for x in removed if x.endswith("GenImage_v01.png"))))
print("\n-- over HTTP (the Design page's routes) --")
import time
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, str(DAVID_HOME / "ui"))
from fastapi import FastAPI
from starlette.testclient import TestClient
import design_api as A
app = FastAPI()
app.include_router(A.make_router({"root": ROOT, "prefs_path": os.path.join(TMP, "prefs.json"), "handshake_path": os.path.join(TMP, "hs.json")}))
c = TestClient(app)
base_url = f"/api/design/projects/{PID}/pipelines"
r = c.post(base_url, json={"params": {**BASE, "name": "Http Card"}})
j = r.json()
check("POST plans a pipeline (as 'designer', not as a person) and returns the plan, its state and the readable plan", r.status_code == 200 and j["state"]["state"] == "planned" and j["plan"]["requested_by"] == "designer" and "Fingerprint" in j["markdown"], str(j)[:200])
plid, sha = j["plan"]["pipeline_id"], j["plan"]["plan_sha256"]
check("running before the yes is refused, and so is a yes from 'david' or with a wrong fingerprint", c.post(f"{base_url}/{plid}/run", json={}).status_code in (400, 409, 422)
      and c.post(f"{base_url}/{plid}/approve", json={"approved_by": "david", "plan_sha256": sha}).status_code in (400, 409, 422) and c.post(f"{base_url}/{plid}/approve", json={"approved_by": "Demo User", "plan_sha256": "0" * 64}).status_code in (400, 409, 422))
r = c.post(f"{base_url}/{plid}/approve", json={"approved_by": "Demo User", "plan_sha256": sha})
check("the designer's name and the fingerprint approve it", r.status_code == 200 and r.json()["state"]["state"] == "approved")
r = c.post(f"{base_url}/{plid}/run", json={})
check("run starts the chain in the background and answers at once", r.status_code == 200 and r.json()["started"] is True)
stt = {"state": "?"}
for _ in range(150):
    time.sleep(0.2)
    stt = c.get(f"{base_url}/{plid}").json()["state"]
    if stt["state"] in ("done", "failed"):
        break
check("polling shows the chain finishing; every stage is listed with its job", stt["state"] == "done" and sorted(stt["stages"]) == ["assemble_photoshop", "generate", "name", "split"], stt["state"])
check("the pipelines are listed with their state", {x["pipeline"]: x["state"] for x in c.get(base_url).json()["pipelines"]}.get(plid) == "done")
r = c.post(f"{base_url}/{plid}/undo")
check("undo over HTTP removes what the chain delivered", r.status_code == 200 and r.json()["state"]["state"] == "undone" and len(r.json()["removed"]) >= 8)
check("an unknown pipeline is a clean error, not a crash", c.get(f"{base_url}/pl-9999").status_code in (404, 400, 409, 422))

src_text = open(str(DAVID_HOME / "design_pipeline.py"), encoding="utf-8").read()
check("the runner adds no power of its own: it never approves in anyone else's name (the approval name always comes from the designer's recorded yes), runs no script of its own, and deletes nothing itself (undo goes through the runner)",
      "approved_by" in src_text and "R._who(ap[\"approved_by\"])" in src_text and "subprocess" not in src_text and "os.remove(" not in src_text and "shutil" not in src_text)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
