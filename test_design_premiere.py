"""
test_design_premiere.py -- tests for design_premiere.py (premiere.rough_cut) and the panel script's fixed actions, with a FAKE panel (no Premiere is opened): planning, refusals, the command sent,
delivery, failure sentences, and the real queue logic (write, wait, cancel an unanswered command) against a temporary queue file with a simulated panel.
Run: python test_design_premiere.py
"""
from david_config import DAVID_HOME
import sys, os, re, json, shutil, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image
import design_premiere as P
import design_aftereffects as AE
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dpr_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
# A stand-in for Premiere's H.264 export preset, so the suite does not depend on an installed Premiere (found by CI on a
# clean runner, 2026-10-21). The refusal when the preset is missing is checked below.
P.PRESET = os.path.join(TMP, "H264 Match Source - High bitrate.epr")
open(P.PRESET, "w", encoding="utf-8").write("<PremiereData/>")
CLIPS = []
for n in ("intro.mp4", "plate.png", "outro.mov"):
    p = os.path.join(SRC, n)
    if n.endswith(".png"):
        Image.new("RGB", (1024, 768), (20, 90, 60)).save(p)
    else:
        open(p, "wb").write(b"fake media " + n.encode() + b"x" * 200)
    CLIPS.append(p)
TITLE = os.path.join(SRC, "title.png")
Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(TITLE)
SHA = {p: R.sha256_file(p) for p in CLIPS + [TITLE]}
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-11", root=ROOT)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


print("-- the panel script (syntax and fixed actions) --")
jsx = open(str(DAVID_HOME / "premiere_cep" / "jsx" / "main.jsx"), encoding="utf-8").read()
check("the panel script has the fixed actions ping and rough_cut next to the old ones, and lists them in its unknown-action message", 'cmd.action === "ping"' in jsx and 'cmd.action === "rough_cut"' in jsx and "ping|rough_cut|info" in jsx
      and all(f'action === "{a}"' in jsx for a in ("create_bin", "import", "add_markers", "save")))
check("rough_cut refuses a project that has content, so it can never touch the designer's own work", "children.numItems > 0 || proj.sequences.numSequences > 0" in jsx and "never works in a project that has content" in jsx)
check("rough_cut imports the files, finds each by its media path, makes ONE sequence from the clips in order, lays the title on track 2 for the whole length, exports with the preset and saves the project as given",
      all(t in jsx for t in ("proj.importFiles(paths", "findByPath(proj.rootItem", "createNewSequenceFromClips", "seq.videoTracks[1].overwriteClip(titleItem, 0)", "ti.end = e", "exportAsMediaDirect(cmd.out_path, cmd.preset_path, 0)", "proj.saveAs(cmd.project_path)")))
check("the script uses only what Premiere's ExtendScript has (no eval, no file deletion, no shell)", not any(t in jsx for t in ("eval(", "system.callSystem", ".remove()", "File.remove", "Folder.remove")))
import subprocess
open(os.path.join(TMP, "chk.js"), "w", encoding="utf-8").write(jsx)
sc = subprocess.run(["node", "--check", os.path.join(TMP, "chk.js")], capture_output=True, text=True)
check("the panel script has no syntax error (checked with node)", sc.returncode == 0, sc.stderr[:200])

print("\n-- planning --")
base = {"clips": CLIPS, "name": "Hero Cut"}
pl = lambda **prm: refused(R.plan_job, PID, "premiere.rough_cut", {**base, **prm}, root=ROOT)
_preset = P.PRESET
P.PRESET = os.path.join(TMP, "no_such_preset.epr")
check("without Premiere's H.264 export preset the plan is refused with a sentence", "export preset was not found" in (pl() or ""), pl())
P.PRESET = _preset
txt = os.path.join(SRC, "x.txt")
open(txt, "w").write("x")
jpg_title = os.path.join(SRC, "t.jpg")
Image.new("RGB", (8, 8)).save(jpg_title)
check("refusals: a text file as a clip, an empty list, more than 20 clips, a JPG as the title (it must be a transparent PNG)", "must be" in (pl(clips=[txt]) or "") and pl(clips=[]) and pl(clips=[CLIPS[0]] * 21) and "PNG" in (pl(title=jpg_title) or ""))
pn = R.plan_job(PID, "premiere.rough_cut", {**base, "title": TITLE}, root=ROOT)
check("outputs follow the standard: HeroCut_PrCut_v01.mp4 and .prproj; every clip and the title is a hashed input, in order", pn["outputs"] == ["HeroCut_PrCut_v01.mp4", "HeroCut_PrCut_v01.prproj"] and len(pn["inputs"]) == 4
      and [i["path"] for i in pn["inputs"][:3]] == [os.path.normpath(c) for c in CLIPS], pn["outputs"])
steps = " | ".join(pn["steps"])
check("the plan says the panel must answer, Premiere needs an EMPTY project, the clips are imported in order, the title goes on video track 2 keeping transparency, the export preset, and that the originals are never opened",
      "David panel" in steps and "EMPTY project" in steps and "3 clip(s)" in steps and "video track 2" in steps and "H264 Match Source - High bitrate" in steps and "never opened" in steps, steps[:300])
check("without a title the plan has no title step", "video track 2" not in " | ".join(R.plan_job(PID, "premiere.rough_cut", base, root=ROOT)["steps"]))

print("\n-- end to end with a fake panel --")
SENT = []
MODE = {"ping": "done", "result": None, "no_mp4": False, "no_proj": False}


def fake_send(fields, wait):
    SENT.append((fields, wait))
    if fields["action"] == "ping":
        return (MODE["ping"], 'DAVID_OK:{"premiere":"26.0","project":"Untitled"}' if MODE["ping"] == "done" else "")
    if MODE["result"]:
        return MODE["result"]
    if not MODE["no_mp4"]:
        open(fields["out_path"], "wb").write(b"\x00\x00\x00 ftypmp42 fake " + b"x" * 400)
    if not MODE["no_proj"]:
        open(fields["project_path"], "wb").write(b"<prproj fake>" + b"x" * 300)
    return ("done", 'DAVID_OK:{"sequence":"HeroCut_PrCut_v01","video_tracks":3,"clips_on_v1":3,"seconds":16.5,"title":"placed on video track 2 for 16.5 s"}')


P.TRANSPORT = fake_send
AE.frame_from_video = lambda video, png, at=0.5: (Image.new("RGB", (64, 64), (1, 2, 3)).save(png) or png)
COUNT = [0]


def approved(**prm):
    COUNT[0] += 1
    pln = R.plan_job(PID, "premiere.rough_cut", {**base, "name": f"Hero Cut {chr(64 + COUNT[0])}", **prm}, root=ROOT)
    for it in R.plan_items(pln):
        R.review_item(PID, pln["job_id"], it, "approved", "", "Demo User", ROOT)
    R.approve(PID, pln["job_id"], "Demo User", pln["plan_sha256"], ROOT, True)
    return pln["job_id"]


res = R.run_job(PID, approved(title=TITLE), ROOT)
ex = os.path.join(ROOT, PID, "05_Exports")
f = SENT[-1][0]
copies = [os.path.normpath(os.path.join(ROOT, PID, "02_Assets", os.path.basename(c))) for c in CLIPS]
check("delivered: the MP4 and the .prproj in 05_Exports and a preview; the panel was asked to ping first, then given the COPIES in order and the title copy (never the originals), the export preset and output paths; originals unchanged",
      res["ok"] and os.path.isfile(os.path.join(ex, "HeroCutA_PrCut_v01.mp4")) and os.path.isfile(os.path.join(ex, "HeroCutA_PrCut_v01.prproj")) and res["preview"].startswith("04_Previews/")
      and SENT[-2][0]["action"] == "ping" and f["action"] == "rough_cut" and f["clips"] == copies and f["title_image"].endswith("title.png") and "02_Assets" in f["title_image"] and f["preset_path"] == P.PRESET
      and all(R.sha256_file(p) == SHA[p] for p in CLIPS + [TITLE]), str(res)[:300])
check("the note names the sequence, the clip count, the length and the title", any("3 clip(s) on video track 1, 16.5 s; title: placed on video track 2" in n for n in res["notes"]), res["notes"])
sz = {os.path.basename(x["path"]): (x["w"], x["h"]) for x in f["sizes"]}
check("the command carries the pixel sizes of the stills and of the title (not of the videos), so the panel can scale each to fit the frame", sz == {"plate.png": (1024, 768), "title.png": (64, 64)}, sz)
jsx2 = open(str(DAVID_HOME / "premiere_cep" / "jsx" / "main.jsx"), encoding="utf-8").read()
check("the panel script scales each still and the title with Motion > Scale to fit the frame, never stretched (the smaller of width and height ratios), and reports what it did",
      "function scaleToFit" in jsx2 and "Math.min(seqW / w, seqH / h)" in jsx2 and 'displayName === "Motion"' in jsx2 and 'displayName === "Scale"' in jsx2 and "info.stills_scaled" in jsx2 and "info.title_scale" in jsx2 and "seq.frameSizeHorizontal" in jsx2)
bad_png = os.path.join(SRC, "bad.png")
open(bad_png, "wb").write(b"not an image")
check("an unreadable still does not stop the job (it is simply left unscaled)", P._still_sizes([os.path.join(SRC, "intro.mp4"), bad_png]) == [])
res = R.run_job(PID, approved(), ROOT)
check("without a title the command carries an empty title", res["ok"] and SENT[-1][0]["title_image"] == "")
MODE["ping"] = "timeout"
res = R.run_job(PID, approved(), ROOT)
check("if the panel does not answer the ping the job fails at once with the instructions (open Premiere, empty project, the panel in Window > Extensions) and nothing is delivered", not res["ok"] and "Window > Extensions > David AI" in str(res) and SENT[-1][0]["action"] == "ping", str(res)[:300])
MODE["ping"] = "done"
MODE["result"] = ("error", "DAVID_ERR:The open project (My Edit) is not empty. Close it.")
res = R.run_job(PID, approved(), ROOT)
check("a refusal from the panel (a project with content is open) becomes the job's sentence and nothing is delivered", not res["ok"] and "not empty" in str(res), str(res)[:300])
MODE["result"] = ("timeout", "")
res = R.run_job(PID, approved(), ROOT)
check("a Premiere that never finishes is a clear failure", not res["ok"] and "did not finish" in str(res), str(res)[:200])
MODE["result"] = ("done", "DAVID_OK:not json")
res = R.run_job(PID, approved(), ROOT)
check("an answer that cannot be read is a clear failure", not res["ok"] and "could not be read" in str(res), str(res)[:200])
MODE["result"] = None
for key, expect in (("no_mp4", "not there"), ("no_proj", "did not save the project")):
    MODE[key] = True
    before = set(os.listdir(ex))
    res = R.run_job(PID, approved(), ROOT)
    check(f"{key}: the job fails with a sentence and nothing new is delivered", not res["ok"] and expect in str(res) and set(os.listdir(ex)) == before, str(res)[:200])
    MODE[key] = False

print("\n-- the real queue logic (temporary queue file, simulated panel) --")
P.QUEUE = os.path.join(TMP, "queue.json")
P.POLL = 0.05
json.dump({"commands": [{"id": "old", "status": "done", "action": "info", "result": "x"}]}, open(P.QUEUE, "w"))


def panel(delay, status, result):
    def run():
        time.sleep(delay)
        for _ in range(100):
            d = json.load(open(P.QUEUE))
            pend = [c for c in d["commands"] if c["status"] == "pending"]
            if pend:
                pend[0]["status"], pend[0]["result"] = status, result
                json.dump(d, open(P.QUEUE, "w"))
                return
            time.sleep(0.02)
    t = threading.Thread(target=run)
    t.start()
    return t


t = panel(0.2, "done", "DAVID_OK:ok")
st, tx = P.send({"action": "ping"}, 5)
t.join()
q = json.load(open(P.QUEUE))
check("a command is appended after the old ones, picked up by the panel and its result returned; the old commands are kept", st == "done" and tx == "DAVID_OK:ok" and len(q["commands"]) == 2 and q["commands"][0]["id"] == "old" and q["commands"][1]["action"] == "ping")
t = panel(0.2, "error", "DAVID_ERR:no project")
st, tx = P.send({"action": "rough_cut"}, 5)
t.join()
check("an error result is returned as an error", st == "error" and "no project" in tx)
st, tx = P.send({"action": "ping"}, 0.5)
q = json.load(open(P.QUEUE))
check("a command nobody picks up in time is marked cancelled, so it can never run later by surprise", st == "timeout" and q["commands"][-1]["status"] == "cancelled", q["commands"][-1])
src_text = open(str(DAVID_HOME / "design_premiere.py"), encoding="utf-8").read()
check("nothing in the module deletes or moves a designer's file or calls a model", "os.remove(" not in src_text and "shutil.move" not in src_text and "ollama" not in src_text.lower())
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
