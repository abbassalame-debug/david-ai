"""
test_design_runner.py -- tests for design_runner.py: the safe runner of the Design portal, proven with FAKE apps (no Adobe app is opened).
Offline; temporary folders only.   Run: python test_design_runner.py
"""
from david_config import DAVID_HOME
import sys, os, re, json, glob, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import design_runner as R

TMP = tempfile.mkdtemp(prefix="dr_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)


def mkfile(name, data=b"ORIGINAL-DATA-" * 50):
    p = os.path.join(SRC, name)
    open(p, "wb").write(data)
    return p


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


def tree(p):
    return sorted(os.path.relpath(os.path.join(d, f), p) for d, _, fs in os.walk(p) for f in fs if "_job" not in d.split(os.sep) and f != "project.json")


ORIG = mkfile("logo.psd")
ORIG_SHA = R.sha256_file(ORIG)
SPY = {"ran": 0, "saw": None}


def op_ok(ctx):
    SPY["ran"] += 1
    SPY["saw"] = dict(ctx["params"])
    out = os.path.join(ctx["staging"], "Acme_Spring_Poster_v01.png")
    open(out, "wb").write(b"PNGDATA" + open(ctx["inputs"]["source"], "rb").read()[:20])
    prev = os.path.join(ctx["staging"], "preview.png")
    open(prev, "wb").write(b"PREVIEW")
    return {"outputs": {"Acme_Spring_Poster_v01.png": out}, "preview": prev, "notes": ["fake app ok"]}


def op_midway(ctx):
    open(os.path.join(ctx["staging"], "Acme_Spring_Poster_v01.png"), "wb").write(b"half")
    raise RuntimeError("the app crashed halfway")


def op_tamper(ctx):
    open(ORIG, "ab").write(b"VANDAL")
    out = os.path.join(ctx["staging"], "Acme_Spring_Poster_v01.png")
    open(out, "wb").write(b"X")
    return {"outputs": {"Acme_Spring_Poster_v01.png": out}}


def op_wrong_outputs(ctx):
    out = os.path.join(ctx["staging"], "Other_Name_v01.png")
    open(out, "wb").write(b"X")
    return {"outputs": {"Other_Name_v01.png": out}}


def op_escape(ctx):
    out = os.path.join(TMP, "escaped.png")
    open(out, "wb").write(b"X")
    return {"outputs": {"Acme_Spring_Poster_v01.png": out}}


def op_empty(ctx):
    out = os.path.join(ctx["staging"], "Acme_Spring_Poster_v01.png")
    open(out, "wb").write(b"")
    return {"outputs": {"Acme_Spring_Poster_v01.png": out}}


def spec(run, outputs=lambda p: ["Acme_Spring_Poster_v01.png"], params=None):
    return {"app": "photoshop", "title": "Fake poster export", "params": params or {"source": {"type": "file"}}, "plan": lambda p: ["Open a copy of the file in the fake app", "Export a PNG"], "outputs": outputs, "run": run}


for oid, fn in (("fake.ok", op_ok), ("fake.midway", op_midway), ("fake.tamper", op_tamper), ("fake.wrong_outputs", op_wrong_outputs), ("fake.escape", op_escape), ("fake.empty", op_empty)):
    R.register({"id": oid, **spec(fn)})
R.register({"id": "fake.badname", **spec(op_ok, outputs=lambda p: ["My Final Copy.png"])})
R.register({"id": "fake.typed", **spec(op_ok, params={"source": {"type": "file"}, "n": {"type": "int", "min": 1, "max": 5, "required": False, "default": 2}, "kind": {"type": "enum", "values": ["a", "b"], "required": False},
                                                      "note": {"type": "str", "required": False}, "flag": {"type": "bool", "required": False}, "ratio": {"type": "float", "min": 0, "max": 1, "required": False}})})

print("-- the project folder --")
pid = R.create_project("Acme", "Spring Poster", "2026-10-10", root=ROOT)
check("a project is named by the standard and has the numbered folders, a marker file and a job folder", pid == "2026-10-10_Acme_SpringPoster" and sorted(os.listdir(os.path.join(ROOT, pid))) == sorted(["01_Brief", "02_Assets", "03_Working", "04_Previews", "05_Exports", "06_Delivery", "07_Archive", "_job", "project.json"]))
check("the same project cannot be created twice", refused(R.create_project, "Acme", "Spring Poster", "2026-10-10", root=ROOT) is not None)
try:
    R.create_project("شاورما", "x", "2026-10-10", root=ROOT)
    arabic_refused = False
except Exception as e:
    arabic_refused = "English" in str(e)
check("...(the Arabic-only name is refused and says why)", arabic_refused)
check("a project id that tries to leave the design folder is refused", all(refused(R.project_dir, x, ROOT) for x in ("..", "..\\x", "a/b", "a\\b", "", None)))
check("the project list shows only real projects", R.list_projects(ROOT) == [pid])

print("\n-- a plan is a dry run: it touches nothing --")
before = tree(os.path.join(ROOT, pid))
plan = R.plan_job(pid, "fake.ok", {"source": ORIG}, "model", root=ROOT)
check("planning creates no copy and no output, and nothing runs", tree(os.path.join(ROOT, pid)) == before and SPY["ran"] == 0 and R.status(pid, plan["job_id"], ROOT)["state"] == "planned")
check("the plan lists steps, the file with its SHA-256 and where its copy will go, what it will make, and who asked", plan["steps"] == ["Open a copy of the file in the fake app", "Export a PNG"] and plan["inputs"][0]["sha256"] == ORIG_SHA and plan["inputs"][0]["copy"] == "02_Assets/logo.psd" and plan["outputs"] == ["Acme_Spring_Poster_v01.png"] and plan["requested_by"] == "model")
md = open(os.path.join(ROOT, pid, "_job", plan["job_id"], "plan.md"), encoding="utf-8").read()
check("the readable plan says that nothing has run and shows the fingerprint", "Nothing has run" in md and plan["plan_sha256"] in md and ORIG_SHA[:12] in md)
check("a model may ask for a plan but cannot run it: running a planned job is refused and nothing runs", "planned" in (refused(R.run_job, pid, plan["job_id"], ROOT) or "") and SPY["ran"] == 0 and tree(os.path.join(ROOT, pid)) == before)

print("\n-- the yes gate --")
check("nobody but the designer approves: '', 'David', 'AI', 'Claude', 'model', None are refused", all(refused(R.approve, pid, plan["job_id"], n, plan["plan_sha256"], ROOT) for n in ("", "David", "AI", "Claude", "model", None)))
check("a wrong or missing fingerprint is refused (he approves exactly the plan he saw)", refused(R.approve, pid, plan["job_id"], "Demo User", "0" * 64, ROOT) is not None and refused(R.approve, pid, plan["job_id"], "Demo User", None, ROOT) is not None)
check("after all those attempts the job is still only planned and still runs nothing", R.status(pid, plan["job_id"], ROOT)["state"] == "planned" and refused(R.run_job, pid, plan["job_id"], ROOT) is not None and SPY["ran"] == 0)
pj = os.path.join(ROOT, pid, "_job", plan["job_id"], "plan.json")
orig_plan_text = open(pj, encoding="utf-8").read()
tampered = json.loads(orig_plan_text)
tampered["outputs"] = ["Evil_v01.png"]
open(pj, "w", encoding="utf-8").write(json.dumps(tampered))
check("a plan edited on disk after it was made is refused", "edited" in (refused(R.approve, pid, plan["job_id"], "Demo User", plan["plan_sha256"], ROOT) or ""))
open(pj, "w", encoding="utf-8").write(orig_plan_text)
open(ORIG, "ab").write(b"CHANGED-BY-HAND")
check("a file changed between the plan and the approval is refused, with its path", ORIG in (refused(R.approve, pid, plan["job_id"], "Demo User", plan["plan_sha256"], ROOT) or ""))
ORIG = mkfile("logo.psd")
ORIG_SHA = R.sha256_file(ORIG)
plan = R.plan_job(pid, "fake.ok", {"source": ORIG}, "designer", root=ROOT)
R.approve(pid, plan["job_id"], "Demo User", plan["plan_sha256"], ROOT)
check("with his name and the right fingerprint the job is approved and records who", R.status(pid, plan["job_id"], ROOT)["state"] == "approved" and R.status(pid, plan["job_id"], ROOT)["approved_by"] == "Demo User" and SPY["ran"] == 0)
check("approving twice is refused", refused(R.approve, pid, plan["job_id"], "Demo User", plan["plan_sha256"], ROOT) is not None)

print("\n-- running an approved job --")
res = R.run_job(pid, plan["job_id"], ROOT)
P = os.path.join(ROOT, pid)
check("the job runs once and is done, with the output in 05_Exports and the preview in 04_Previews", res["ok"] and res["state"] == "done" and SPY["ran"] == 1 and os.path.isfile(os.path.join(P, "05_Exports", "Acme_Spring_Poster_v01.png")) and res["preview"] == f"04_Previews/{plan['job_id']}_preview.png" and os.path.isfile(os.path.join(P, res["preview"])))
check("the operation saw only the COPY inside the project, never the original's path", SPY["saw"]["source"] == os.path.join(P, "02_Assets", "logo.psd") and SPY["saw"]["source"] != ORIG)
check("the copy equals the original and the original is unchanged (SHA-256)", R.sha256_file(os.path.join(P, "02_Assets", "logo.psd")) == ORIG_SHA and R.sha256_file(ORIG) == ORIG_SHA)
check("nothing is written to 06_Delivery and no staging folder is left", os.listdir(os.path.join(P, "06_Delivery")) == [] and not os.path.exists(os.path.join(P, "_job", plan["job_id"], "staging")))
check("running the same job again is refused", "done" in (refused(R.run_job, pid, plan["job_id"], ROOT) or "") and SPY["ran"] == 1)
log = [json.loads(l) for l in open(os.path.join(P, "_job", "log.jsonl"), encoding="utf-8")]
check("the job log records the steps in order for this job", [e["event"] for e in log if e["job"] == plan["job_id"]] == ["planned", "approved", "running", "done"])

print("\n-- undo --")
removed = R.undo_job(pid, plan["job_id"], ROOT)
check("undo removes what the job delivered and the copy it made, nothing else", sorted(removed) == sorted([f"05_Exports/Acme_Spring_Poster_v01.png", res["preview"], "02_Assets/logo.psd"]) and tree(P) == [] and R.status(pid, plan["job_id"], ROOT)["state"] == "undone")
check("the original is still intact after undo, and a job that did not run cannot be undone", R.sha256_file(ORIG) == ORIG_SHA and "planned" in (refused(R.undo_job, pid, R.plan_job(pid, "fake.ok", {"source": ORIG}, root=ROOT)["job_id"], ROOT) or ""))


def approved_job(op, **params):
    pl = R.plan_job(pid, op, {"source": ORIG, **params}, root=ROOT)
    R.approve(pid, pl["job_id"], "Demo User", pl["plan_sha256"], ROOT)
    return pl["job_id"]


print("\n-- a failed run leaves nothing behind --")
j = approved_job("fake.midway")
res = R.run_job(pid, j, ROOT)
check("a crash halfway fails the job with a sentence, delivers nothing, and removes the staging folder and the copy", not res["ok"] and "crashed halfway" in res["error"] and tree(P) == [] and not os.path.exists(os.path.join(P, "_job", j, "staging")) and R.status(pid, j, ROOT)["state"] == "failed")
check("the original is unchanged after the crash", R.sha256_file(ORIG) == ORIG_SHA)
j = approved_job("fake.wrong_outputs")
res = R.run_job(pid, j, ROOT)
check("an operation that makes different files than the plan promised delivers nothing", not res["ok"] and "promised" in res["error"] and tree(P) == [])
j = approved_job("fake.escape")
res = R.run_job(pid, j, ROOT)
check("an output outside the staging folder is refused", not res["ok"] and "outside the staging" in res["error"] and tree(P) == [])
j = approved_job("fake.empty")
res = R.run_job(pid, j, ROOT)
check("an empty output file is refused", not res["ok"] and "empty" in res["error"] and tree(P) == [])
j = approved_job("fake.tamper")
res = R.run_job(pid, j, ROOT)
check("an operation that changes an original is caught: the job fails, says ORIGINAL CHANGED and delivers nothing", not res["ok"] and "ORIGINAL CHANGED" in res["flags"] and tree(P) == [] and R.sha256_file(ORIG) != ORIG_SHA)
ORIG = mkfile("logo.psd")
ORIG_SHA = R.sha256_file(ORIG)

print("\n-- a file changed after approval --")
j = approved_job("fake.ok")
open(ORIG, "ab").write(b"EDITED AFTER APPROVAL")
ran_before = SPY["ran"]
res = R.run_job(pid, j, ROOT)
check("the run stops before copying anything and flags it; the operation never ran", not res["ok"] and "ORIGINAL CHANGED BEFORE THE RUN" in res["flags"] and SPY["ran"] == ran_before and tree(P) == [])
ORIG = mkfile("logo.psd")

print("\n-- a name that breaks the standard, an operation that does not exist, a bad parameter --")
jobs_before = len(R.list_jobs(pid, ROOT))
check("an output name that breaks the naming standard is refused at the plan, and no job is created", "naming standard" in (refused(R.plan_job, pid, "fake.badname", {"source": ORIG}, root=ROOT) or "") and len(R.list_jobs(pid, ROOT)) == jobs_before)
check("a typed sentence or an unknown operation is refused: only registered operations exist", "not an operation" in (refused(R.plan_job, pid, "make me a poster with a red circle", {"source": ORIG}, root=ROOT) or "") and refused(R.plan_job, pid, "photoshop.run_script", {"source": ORIG}, root=ROOT) is not None and len(R.list_jobs(pid, ROOT)) == jobs_before)
check("a missing file, a relative path, a folder and an unknown parameter are refused", all(refused(R.plan_job, pid, "fake.ok", prm, root=ROOT) for prm in ({"source": os.path.join(SRC, "nope.psd")}, {"source": "logo.psd"}, {"source": SRC}, {}, {"source": ORIG, "evil": 1}, "not a dict")))
T = lambda **k: refused(R.plan_job, pid, "fake.typed", {"source": ORIG, **k}, root=ROOT)
check("typed parameters are checked: a whole number in range, an enum, a boolean, a float in range, text length", all(T(**k) for k in ({"n": 0}, {"n": 6}, {"n": 2.5}, {"n": True}, {"n": float("nan")}, {"kind": "c"}, {"flag": "yes"}, {"ratio": 2}, {"note": " "}, {"note": "x" * 501})))
check("and good values are accepted and the default fills in", T(n=3, kind="a", flag=True, ratio=0.5, note="hello") is None and R.plan_job(pid, "fake.typed", {"source": ORIG}, root=ROOT)["params"]["n"] == 2)
def reg_error(op):
    try:
        R.register(op)
        return None
    except ValueError as e:
        return str(e)


check("two operations cannot share an id, and an unknown app or a missing field is refused at registration", "already registered" in (reg_error({"id": "fake.ok", **spec(op_ok)}) or "") and "unknown app" in (reg_error({"id": "fake.x1", **{**spec(op_ok), "app": "gimp"}}) or "") and "missing" in (reg_error({"id": "fake.x2", "app": "photoshop"}) or ""))

print("\n-- delivered files are never replaced --")
j1 = approved_job("fake.ok")
R.run_job(pid, j1, ROOT)
keep = open(os.path.join(P, "05_Exports", "Acme_Spring_Poster_v01.png"), "rb").read()
j2 = approved_job("fake.ok")
res = R.run_job(pid, j2, ROOT)
check("a second job that would overwrite a delivered file fails and leaves the first file exactly as it was", not res["ok"] and "already delivered" in res["error"] and open(os.path.join(P, "05_Exports", "Acme_Spring_Poster_v01.png"), "rb").read() == keep)
check("the copy made by the first job is kept (the second job did not delete what it did not make)", os.path.isfile(os.path.join(P, "02_Assets", "logo.psd")))

print("\n-- cancel, interrupted runs, one job at a time --")
j3 = R.plan_job(pid, "fake.ok", {"source": ORIG}, root=ROOT)["job_id"]
R.cancel(pid, j3, ROOT)
check("a planned job can be cancelled and then cannot be approved or run; a finished job cannot be cancelled", R.status(pid, j3, ROOT)["state"] == "cancelled" and refused(R.run_job, pid, j3, ROOT) is not None and refused(R.cancel, pid, j1, ROOT) is not None)
j4 = approved_job("fake.ok")
R._set_state(pid, j4, ROOT, "running")
j5 = approved_job("fake.ok")
check("while a job is 'running' (or was interrupted) another cannot start", "one job at a time" in (refused(R.run_job, pid, j5, ROOT) or ""))
check("only the designer can release an interrupted job; then it is failed and the project is free", refused(R.release_interrupted, pid, j4, "Claude", ROOT) is not None and R.release_interrupted(pid, j4, "Demo User", ROOT) is None and R.status(pid, j4, ROOT)["state"] == "failed" and refused(R.release_interrupted, pid, j1, "Demo User", ROOT) is not None)

print("\n-- checking every line, and correcting a plan --")
pl = R.plan_job(pid, "fake.typed", {"source": ORIG, "n": 3, "note": "hello"}, root=ROOT)
check("a plan lists its lines: steps, files, other parameters, outputs", R.plan_items(pl) == ["step:1", "step:2", "input:source", "param:n", "param:note", "output:Acme_Spring_Poster_v01.png"] and R.unreviewed(pid, pl["job_id"], ROOT) == R.plan_items(pl))
check("approving with require_reviews is refused while any line is unchecked", "Check every line" in (refused(R.approve, pid, pl["job_id"], "Demo User", pl["plan_sha256"], ROOT, True) or ""))
check("a correction needs a note; a line not in the plan, a bad verdict and a non-person are refused", all(refused(R.review_item, pid, pl["job_id"], i, v, n, b, ROOT) for i, v, n, b in (("param:n", "corrected", "", "Demo User"), ("param:zzz", "approved", "", "Demo User"), ("param:n", "maybe", "", "Demo User"), ("param:n", "approved", "", "David"), ("step:1", "approved", "x" * 501, "Demo User"))))
for it in R.plan_items(pl)[:-1]:
    R.review_item(pid, pl["job_id"], it, "approved", "", "Demo User", ROOT)
R.review_item(pid, pl["job_id"], "param:n", "corrected", "should be 4", "Demo User", ROOT)
check("a line marked corrected is not approved: it stays on the list and blocks the yes, and the note is kept", R.unreviewed(pid, pl["job_id"], ROOT) == ["param:n", "output:Acme_Spring_Poster_v01.png"] and R.reviews(pid, pl["job_id"], ROOT)["param:n"]["note"] == "should be 4")
new = R.replan(pid, pl["job_id"], {"n": 4}, "Demo User", "n should be 4", ROOT)
check("replan makes a NEW plan with the corrected value and nothing checked; the old plan is cancelled and points to it", new["params"]["n"] == 4 and new["job_id"] != pl["job_id"] and R.status(pid, pl["job_id"], ROOT)["state"] == "cancelled" and R.status(pid, pl["job_id"], ROOT)["replaced_by"] == new["job_id"] and R.unreviewed(pid, new["job_id"], ROOT) == R.plan_items(new))
check("replan refuses a non-person, an empty change, an unknown parameter, a bad value, and a plan that already ran", all(refused(R.replan, pid, new["job_id"], c, b, "x", ROOT) for c, b in (({"n": 9}, "Demo User"), ({}, "Demo User"), ({"nope": 1}, "Demo User"), ({"n": 2}, "David"))) and refused(R.replan, pid, pl["job_id"], {"n": 2}, "Demo User", "x", ROOT) is not None)
for it in R.plan_items(new):
    R.review_item(pid, new["job_id"], it, "approved", "", "Demo User", ROOT)
R.approve(pid, new["job_id"], "Demo User", new["plan_sha256"], ROOT, True)
check("with every line approved the yes is accepted", R.status(pid, new["job_id"], ROOT)["state"] == "approved")
R.undo_job(pid, j1, ROOT)
R.run_job(pid, new["job_id"], ROOT)
check("a delivered file can be reviewed afterwards with a note; a file the job did not deliver cannot", R.review_item(pid, new["job_id"], "result:05_Exports/Acme_Spring_Poster_v01.png", "corrected", "too dark", "Demo User", ROOT)["result:05_Exports/Acme_Spring_Poster_v01.png"]["note"] == "too dark" and refused(R.review_item, pid, new["job_id"], "result:05_Exports/other.png", "approved", "", "Demo User", ROOT) is not None)

print("\n-- deleting a project --")
os.makedirs(os.path.join(ROOT, "not_a_project"))
check("a wrong id, or a folder that is not a design project, is refused and nothing is deleted", refused(R.delete_project, pid, "wrong", ROOT) is not None and refused(R.delete_project, "not_a_project", "not_a_project", ROOT) is not None and os.path.isdir(P) and os.path.isdir(os.path.join(ROOT, "not_a_project")))
check("typing the exact id deletes the whole project folder; the original file is still there", R.delete_project(pid, pid, ROOT) is True and not os.path.exists(P) and os.path.isfile(ORIG))

print("\n-- the model cannot start a job, and the old typed route is retired --")
skip = re.compile(r"\\(test_|design_)|\\evals\\|\\jobs_drafts\\|\\generated\\|node_modules|\\ComfyUI\\|\\\.git\\|\\scripts\\_")
users = [f for f in glob.glob((str(DAVID_HOME) + r"\**\*.py"), recursive=True) if not skip.search(f) and re.search(r"^\s*(import design_runner|from design_runner)", open(f, encoding="utf-8", errors="ignore").read(), re.M)]
MANUAL = {str(DAVID_HOME / "run_shots.py"), str(DAVID_HOME / "run_storyboard.py"), str(DAVID_HOME / "run_storyboard_v2.py"), str(DAVID_HOME / "make_animatic.py"), str(DAVID_HOME / "make_cut.py")}
check("no other module in David (agents, tools, server) imports the runner; the only exceptions are five named one-shot command-line scripts the developer runs from chat with the manager's approval (a new importer fails here)", sorted(set(users) - MANUAL) == [], str(sorted(set(users) - MANUAL)))
names = [os.path.splitext(os.path.basename(m))[0] for m in MANUAL]
importers = [f for f in glob.glob((str(DAVID_HOME) + r"\**\*.py"), recursive=True) if not skip.search(f) and f not in MANUAL and re.search(r"^\s*(import|from)\s+(" + "|".join(names) + r")\b", open(f, encoding="utf-8", errors="ignore").read(), re.M)]
check("those five scripts are not imported by anything: no agent, tool or server module can reach them", importers == [], str(importers))
import adobe_bridge as AB
check("adobe_bridge.design_request and execute_raw_jsx are retired and run nothing", AB.design_request("make a poster")["retired"] is True and AB.execute_raw_jsx("alert(1)")["ok"] is False and "retired" in AB.design_request("x")["message"])
sv = open(str(DAVID_HOME / "ui" / "server.py"), encoding="utf-8").read()
ap = sv[sv.index('@app.post("/api/adobe")'):sv.index('@app.post("/api/office")')]
check("the /api/adobe endpoint answers 410 Gone and never calls the bridge", "410" in ap and "RETIRED_MESSAGE" in ap and "design_request" not in ap)
st = open(str(DAVID_HOME / "ui" / "frontend" / "src" / "pages" / "studio-page.tsx"), encoding="utf-8").read()
check("the Studio page no longer has the typed Adobe command box", "/api/adobe" not in st and "sendAdobeCmd" not in st and "Retired" in st)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
