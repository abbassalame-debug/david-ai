"""
test_design_api.py -- offline tests for the HUD's Design page backend (ui/design_api.py) and the fixed operation file.inspect, through FastAPI's TestClient.
Temp folders only; no Adobe app is opened; the real preferences file is not touched.   Run: python test_design_api.py
"""
from david_config import DAVID_HOME
import sys, os, re, json, shutil, hashlib, struct, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import warnings
warnings.filterwarnings("ignore")
from fastapi import FastAPI
from starlette.testclient import TestClient
from PIL import Image
import design_api as A
import design_ops as O
import design_runner as R
import design_preferences as P

TMP = tempfile.mkdtemp(prefix="design_api_test_")
ROOT = os.path.join(TMP, "design")
PREFS = os.path.join(TMP, "prefs.json")
HS = os.path.join(TMP, "handshake.json")
REAL_PREFS = P.EXAMPLE_PATH   # the seeded demo profile
real_sha = hashlib.sha256(open(REAL_PREFS, "rb").read()).hexdigest()
shutil.copy2(REAL_PREFS, PREFS)
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
PNG = os.path.join(SRC, "poster.png")
Image.new("RGB", (300, 200), (200, 30, 30)).save(PNG, dpi=(150, 150))
PSD = os.path.join(SRC, "logo.psd")
open(PSD, "wb").write(b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, 3508, 2480, 8, 4) + b"\0" * 100)
PNG_SHA, PSD_SHA = R.sha256_file(PNG), R.sha256_file(PSD)

app = FastAPI()
app.include_router(A.make_router({"root": ROOT, "prefs_path": PREFS, "handshake_path": HS}))
c = TestClient(app)

print("-- file.inspect, the first fixed operation (no app opened) --")
r1 = O.inspect_file(PNG)
check("a PNG is reported with its size, mode and dpi", (r1["width_px"], r1["height_px"], r1["color_mode"], r1["dpi"], r1["kind"]) == (300, 200, "RGB", [150, 150], "image") and r1["sha256"] == PNG_SHA)
r2 = O.inspect_file(PSD)
check("a Photoshop file's header gives its pixel size, colour mode and bit depth", (r2["width_px"], r2["height_px"], r2["color_mode"], r2["bit_depth"], r2["kind"]) == (2480, 3508, "CMYK", 8, "Photoshop"))
ai = os.path.join(SRC, "art.ai")
open(ai, "wb").write(b"%PDF-1.5 junk <?xpacket begin='x'?><x><stFnt:fontFamily>Sakkal Majalla</stFnt:fontFamily><xmpG:mode>CMYK</xmpG:mode><xmp:CreatorTool>Adobe Illustrator 30.8</xmp:CreatorTool></x><?xpacket end='w'?>")
r3 = O.inspect_file(ai)
check("an Illustrator file's embedded fonts, colour mode and creator are read", r3["fonts_declared"] == ["Sakkal Majalla"] and r3["swatch_color_modes"] == ["CMYK"] and r3["created_with"] == "Adobe Illustrator 30.8")
check("an unknown or empty file is reported without failing", O.inspect_file(os.path.join(SRC, "poster.png"))["extension"] == ".png" and (open(os.path.join(SRC, "n.xyz"), "w").write("a") or True) and O.inspect_file(os.path.join(SRC, "n.xyz"))["kind"] == "other")

print("\n-- the overview and projects --")
ov = c.get("/api/design/overview").json()
check("the overview lists the registered operations (file.inspect with no app, the Photoshop, Illustrator and InDesign ones), the document presets, the preferences summary, and no handshake yet", sorted(o["id"] for o in ov["operations"]) == ["aftereffects.animate_layers", "file.inspect", "generate.image", "generate.name_layers", "generate.split_layers", "illustrator.assemble_layers", "illustrator.audit", "illustrator.compose_card", "illustrator.export", "illustrator.inspect", "indesign.assemble_layers", "indesign.export_pdf", "indesign.inspect", "indesign.new_document", "indesign.place_content", "music.generate", "photoshop.assemble_layers", "photoshop.compose_card", "photoshop.export_preview", "photoshop.inspect", "photoshop.resize_to_preset", "premiere.rough_cut", "video.image_to_video"] and {o["id"]: o["app"] for o in ov["operations"]}["file.inspect"] == "none" and {o["id"]: o["app"] for o in ov["operations"]}["photoshop.inspect"] == "photoshop" and "square-1080" in [x["id"] for x in ov["presets"]] and ov["preferences"]["confirmed"] == 0 and ov["preferences"]["waiting"] > 40 and ov["handshake"] is None and ov["projects"] == [])
r = c.post("/api/design/projects", json={"client": "Acme", "project": "Spring Poster", "date": "2026-10-10"})
pid = r.json().get("id")
check("a project is created with the standard name and the numbered folders from the preferences", r.status_code == 200 and pid == "2026-10-10_Acme_SpringPoster" and os.path.isdir(os.path.join(ROOT, pid, "05_Exports")))
check("the same project twice, an Arabic-only name and a made-up date are each refused with a sentence (409)", all(c.post("/api/design/projects", json=b).status_code == 409 for b in ({"client": "Acme", "project": "Spring Poster", "date": "2026-10-10"}, {"client": "شاورما", "project": "x"}, {"client": "A", "project": "B", "date": "2026-13-40"})) and "English" in c.post("/api/design/projects", json={"client": "شاورما", "project": "x"}).json()["detail"])
check("an id that is not a project is refused (400) and a project that does not exist is 404", c.get("/api/design/projects/..").status_code in (400, 404) and c.get("/api/design/projects/2026-01-01_Nope_Nope").status_code == 404)
check("the brief is saved, read back, and capped at 20000 characters", c.put(f"/api/design/projects/{pid}/brief", json={"text": "A3 poster, red and cream, Arabic headline."}).status_code == 200 and c.get(f"/api/design/projects/{pid}").json()["brief"] == "A3 poster, red and cream, Arabic headline." and c.put(f"/api/design/projects/{pid}/brief", json={"text": "x" * 20001}).status_code == 413)
up = c.put(f"/api/design/projects/{pid}/references/poster.png", content=open(PNG, "rb").read())
check("a reference file is uploaded into 01_Brief; the original is untouched", up.status_code == 200 and R.sha256_file(os.path.join(ROOT, pid, "01_Brief", "poster.png")) == PNG_SHA and R.sha256_file(PNG) == PNG_SHA)
check("an unaccepted type is 415, the reserved brief name is 409, and a path in the name is flattened", c.put(f"/api/design/projects/{pid}/references/run.exe", content=b"x").status_code == 415 and c.put(f"/api/design/projects/{pid}/references/brief.md", content=b"x").status_code == 409
      and c.put(f"/api/design/projects/{pid}/references/..%5C..%5Cevil.png", content=b"x").json().get("name") == "evil.png" and os.path.isfile(os.path.join(ROOT, pid, "01_Brief", "evil.png")) and not os.path.exists(os.path.join(ROOT, "evil.png")))

print("\n-- plan, check every line, his yes, run --")
pl = c.post(f"/api/design/projects/{pid}/plan", json={"op": "file.inspect", "params": {"source": PNG}, "requested_by": "model"})
j = pl.json()
jid = j["job"]
check("a plan is made as 'designer' whatever the body says; nothing was copied or run", pl.status_code == 200 and j["state"] == "planned" and j["plan"]["requested_by"] == "designer" and os.listdir(os.path.join(ROOT, pid, "02_Assets")) == [] and os.listdir(os.path.join(ROOT, pid, "05_Exports")) == [])
check("the plan has steps, the file with its SHA-256, and the output name; every line is a checkable item", j["plan"]["inputs"][0]["sha256"] == PNG_SHA and j["plan"]["outputs"] == ["Poster_Inspect_v01.json"] and j["items"] == ["step:1", "step:2", "step:3", "input:source", "output:Poster_Inspect_v01.json"] and j["todo"] == j["items"])
check("an unknown operation or a typed sentence is refused (409), no job created", c.post(f"/api/design/projects/{pid}/plan", json={"op": "make me a poster", "params": {}}).status_code == 409 and len(c.get(f"/api/design/projects/{pid}").json()["jobs"]) == 1)
ap = lambda body: c.post(f"/api/design/projects/{pid}/jobs/{jid}/approve", json=body)
check("the yes is refused while any line is unchecked, even with his name and the right fingerprint (409)", ap({"approved_by": "Demo User", "plan_sha256": j["plan"]["plan_sha256"]}).status_code == 409 and "Check every line" in ap({"approved_by": "Demo User", "plan_sha256": j["plan"]["plan_sha256"]}).json()["detail"])
rv = lambda item, verdict, note="", by="Demo User": c.post(f"/api/design/projects/{pid}/jobs/{jid}/review", json={"item": item, "verdict": verdict, "note": note, "by": by})
check("a correction needs a note; a line that is not in the plan, a bad verdict and a non-person are refused", rv("step:1", "corrected").status_code == 409 and rv("step:99", "approved").status_code == 409 and rv("step:1", "maybe").status_code == 409 and rv("step:1", "approved", by="David").status_code == 409)
for it in j["items"]:
    last = rv(it, "approved", "ok" if it == "output:Poster_Inspect_v01.json" else "")
check("with every line approved the list of unchecked lines is empty and each check is stored with his name", last.json()["todo"] == [] and all(v["by"] == "Demo User" and v["verdict"] == "approved" for v in last.json()["reviews"].values()) and last.json()["reviews"]["output:Poster_Inspect_v01.json"]["note"] == "ok")
check("'David', 'AI' or a missing name cannot give the yes; a wrong fingerprint cannot either", all(ap({"approved_by": n, "plan_sha256": j["plan"]["plan_sha256"]}).status_code == 409 for n in ("David", "AI", "", None)) and ap({"approved_by": "Demo User", "plan_sha256": "0" * 64}).status_code == 409)
check("running a job that is not approved is refused (409) and nothing is created", c.post(f"/api/design/projects/{pid}/jobs/{jid}/run").status_code == 409 and os.listdir(os.path.join(ROOT, pid, "02_Assets")) == [] and os.listdir(os.path.join(ROOT, pid, "05_Exports")) == [])
a = ap({"approved_by": "Demo User", "plan_sha256": j["plan"]["plan_sha256"]})
check("with all lines checked, his name and the fingerprint, the job is approved", a.status_code == 200 and a.json()["state"] == "approved" and a.json()["approved_by"] == "Demo User")
run = c.post(f"/api/design/projects/{pid}/jobs/{jid}/run").json()
check("it runs on a COPY and delivers the report; the original is unchanged", run["result"]["ok"] and run["job"]["state"] == "done" and run["job"]["delivered"] == ["05_Exports/Poster_Inspect_v01.json"] and R.sha256_file(PNG) == PNG_SHA and R.sha256_file(os.path.join(ROOT, pid, "02_Assets", "poster.png")) == PNG_SHA)
rep = c.get(f"/api/design/projects/{pid}/file", params={"rel": "05_Exports/Poster_Inspect_v01.json"})
check("the delivered report can be opened and says what the file is", rep.status_code == 200 and rep.json()["width_px"] == 300 and rep.json()["sha256"] == PNG_SHA)
check("running it again is refused (409)", c.post(f"/api/design/projects/{pid}/jobs/{jid}/run").status_code == 409)
res = lambda item, verdict, note="": c.post(f"/api/design/projects/{pid}/jobs/{jid}/review", json={"item": item, "verdict": verdict, "note": note, "by": "Demo User"})
check("a delivered file can be approved or marked corrected with a note (his feedback is stored); a file the job did not deliver cannot", res("result:05_Exports/Poster_Inspect_v01.json", "corrected", "also list the dpi").status_code == 200 and res("result:05_Exports/Nope.json", "approved").status_code == 409
      and c.get(f"/api/design/projects/{pid}").json()["jobs"][0]["reviews"]["result:05_Exports/Poster_Inspect_v01.json"]["note"] == "also list the dpi")

print("\n-- correcting a plan makes a new plan; undo; cancel --")
p2 = c.post(f"/api/design/projects/{pid}/plan", json={"op": "file.inspect", "params": {"source": PNG}}).json()
rp = c.post(f"/api/design/projects/{pid}/jobs/{p2['job']}/replan", json={"changes": {"source": PSD}, "by": "Demo User", "note": "wrong file, I meant the logo"})
n = rp.json()
jobs = {x["job"]: x for x in c.get(f"/api/design/projects/{pid}").json()["jobs"]}
check("correcting the file makes a NEW plan for the logo; the old plan is cancelled and points to the new one; the new one starts with nothing checked", rp.status_code == 200 and n["plan"]["inputs"][0]["path"] == os.path.normpath(PSD) and n["plan"]["outputs"] == ["Logo_Inspect_v01.json"] and jobs[p2["job"]]["state"] == "cancelled" and jobs[p2["job"]]["replaced_by"] == n["job"] and n["todo"] == n["items"])
check("a replan with no change, an unknown parameter, or as a non-person is refused", all(c.post(f"/api/design/projects/{pid}/jobs/{n['job']}/replan", json=b).status_code == 409 for b in ({"changes": {}, "by": "Demo User"}, {"changes": {"evil": 1}, "by": "Demo User"}, {"changes": {"source": PSD}, "by": "David"})))
cn = c.post(f"/api/design/projects/{pid}/jobs/{n['job']}/cancel")
check("a plan can be cancelled and then cannot be approved", cn.json()["state"] == "cancelled" and ap and c.post(f"/api/design/projects/{pid}/jobs/{n['job']}/approve", json={"approved_by": "Demo User", "plan_sha256": n["plan"]["plan_sha256"]}).status_code == 409)
un = c.post(f"/api/design/projects/{pid}/jobs/{jid}/undo").json()
check("undo removes the delivered report and the copy; the original and the project survive", sorted(un["removed"]) == ["02_Assets/poster.png", "05_Exports/Poster_Inspect_v01.json"] and un["job"]["state"] == "undone" and R.sha256_file(PNG) == PNG_SHA and os.path.isdir(os.path.join(ROOT, pid)))

print("\n-- showing files safely --")
get = lambda rel: c.get(f"/api/design/projects/{pid}/file", params={"rel": rel}).status_code
check("only files in the brief, previews, exports and delivery folders, of a showable type, are served", get("01_Brief/poster.png") == 200 and get("01_Brief/../project.json") == 400 and get("_job/log.jsonl") == 400 and get("project.json") == 400 and get("02_Assets/anything.png") == 400 and get("01_Brief/nope.png") == 404 and get("../x.png") == 400 and get("01_Brief/..\\..\\x") == 400)
open(os.path.join(ROOT, pid, "05_Exports", "x.psd"), "wb").write(b"x")
check("a file type the page cannot show is 404", get("05_Exports/x.psd") == 404)

print("\n-- the preferences on the page --")
pf = c.get("/api/design/preferences").json()
check("the preferences come with what waits for the designer and a hint for every setting", pf["summary"]["confirmed"] == 0 and len(pf["waiting"]) == pf["summary"]["waiting"] and "log" not in pf["profile"] and pf["settings_help"]["print.bleed_mm"].startswith("a number of millimetres"))
cf = lambda body: c.post("/api/design/preferences/confirm", json=body)
co = lambda body: c.post("/api/design/preferences/correct", json=body)
check("confirming as 'David' or with no name is refused (409) and nothing changes", cf({"section": "settings", "id": "naming.separator", "approved_by": "David"}).status_code == 409 and cf({"section": "settings", "id": "naming.separator"}).status_code == 409 and P.load(PREFS)["settings"]["naming.separator"]["status"] == "suggested")
check("his confirm is saved to the file with his name and the summary moves", cf({"section": "settings", "id": "naming.separator", "approved_by": "Demo User", "note": "yes"}).json()["summary"]["confirmed"] == 1 and P.load(PREFS)["settings"]["naming.separator"]["status"] == "confirmed" and P.load(PREFS)["log"][-1]["by"] == "Demo User")
check("a correction with a bad value is refused (409) and the file is unchanged; a good one is saved", co({"id": "print.bleed_mm", "value": 99, "approved_by": "Demo User"}).status_code == 409 and P.load(PREFS)["settings"]["print.bleed_mm"]["value"] == 3 and co({"id": "print.bleed_mm", "value": 5, "approved_by": "Demo User"}).status_code == 200 and P.load(PREFS)["settings"]["print.bleed_mm"]["value"] == 5)
check("the REAL preferences file was not touched by these tests", hashlib.sha256(open(REAL_PREFS, "rb").read()).hexdigest() == real_sha)

print("\n-- the live handshake on the page --")
json.dump({"app": "photoshop", "ok": True}, open(HS, "w"))
check("a saved handshake result is shown in the overview", c.get("/api/design/overview").json()["handshake"] == {"app": "photoshop", "ok": True})

print("\n-- deleting a project --")
check("a wrong confirmation is refused (409); the exact id deletes the project folder", c.request("DELETE", f"/api/design/projects/{pid}", json={"confirm": "nope"}).status_code == 409 and os.path.isdir(os.path.join(ROOT, pid)) and c.request("DELETE", f"/api/design/projects/{pid}", json={"confirm": pid}).status_code == 200 and not os.path.exists(os.path.join(ROOT, pid)) and os.path.isfile(PNG))

print("\n-- how the page reaches Adobe apps --")
def _flat(routes):
    for r in routes:   # FastAPI 0.146 nests an included router as one entry (effective_candidates())
        yield from (_flat(r.effective_candidates()) if hasattr(r, "effective_candidates") else [r])
routes = [(m, r.path) for r in _flat(app.routes) for m in (getattr(r, "methods", None) or [])]
check("there is exactly one route that gives the yes for a job and exactly one for a pipeline (nothing else can approve), and exactly one run route for each",
      sorted(p for m, p in routes if p.endswith("/approve")) == ["/api/design/projects/{pid}/jobs/{jid}/approve", "/api/design/projects/{pid}/pipelines/{plid}/approve"]
      and sorted(p for m, p in routes if p.endswith("/run")) == ["/api/design/projects/{pid}/jobs/{jid}/run", "/api/design/projects/{pid}/pipelines/{plid}/run"])
src_api = open(str(DAVID_HOME / "ui" / "design_api.py"), encoding="utf-8").read()
check("the pipeline routes plan as 'designer' (never as a person), approve only with the name and fingerprint the caller sends, and run in a background thread under the same one-job-at-a-time lock",
      'PL.plan_pipeline(pid, body.get("params") or {}, "designer", root)' in src_api and 'PL.approve_pipeline(pid, plid, b.get("approved_by"), b.get("plan_sha256"), root)' in src_api
      and "threading.Thread(target=work, daemon=True).start()" in src_api and src_api.count("run_lock.acquire(blocking=False)") == 2)
src = open(str(DAVID_HOME / "ui" / "design_api.py"), encoding="utf-8").read() + open(str(DAVID_HOME / "design_ops.py"), encoding="utf-8").read()
check("the page layer and the built-in operation never open an app: no adobe_bridge, COM, subprocess or script runner in them", not re.search(r"adobe_bridge|win32com|subprocess|DoJavaScript|os\.system|execute_jsx", src))
sv = open(str(DAVID_HOME / "ui" / "server.py"), encoding="utf-8").read()
check("the HUD server includes the Design router", "import design_api" in sv and "app.include_router(design_api.router)" in sv)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
