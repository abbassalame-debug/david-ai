from david_config import DAVID_HOME
import os, sys, json, tempfile, datetime, hashlib
sys.path.insert(0, str(DAVID_HOME)); sys.path.insert(0, str(DAVID_HOME / "scripts"))
import reset_restaurant_day as R

n = 0
def ok(c, m):
    global n
    n += 1
    assert c, "FAIL: " + m
    print("ok", n, m)

t = tempfile.mkdtemp()
root, abo, cache, arch = (os.path.join(t, x) for x in ("root", "abo", "cache", "arch"))
day = "2026-10-12"
folder = os.path.join(abo, "10-2026", "12-10-2026"); os.makedirs(folder); os.makedirs(cache)
photo = os.path.join(folder, "a.jpeg"); open(photo, "wb").write(b"photo-bytes")
sheet = os.path.join(folder, "s.xlsx"); open(sheet, "wb").write(b"sheet")
other = os.path.join(folder, "b.jpeg"); open(other, "wb").write(b"other-photo")
pre = hashlib.sha256(b"photo-bytes").hexdigest()[:24]
other_day_pre = "f" * 24
for nm in (f"{pre}_type_v1.json", f"{pre}_read_v1.json", f"{other_day_pre}_type_v1.json"):
    open(os.path.join(cache, nm), "w").write("{}")
for d in (os.path.join(root, "jobs_drafts", "restaurant_day", day), os.path.join(root, "jobs_drafts", "restaurant_day", "2026-10-11"), os.path.join(root, "for_review", f"day_{day}"), os.path.join(root, "for_review", "day_2026-10-11")):
    os.makedirs(d); open(os.path.join(d, "x.json"), "w").write("{}")
ws = os.path.join(t, "status.json")
json.dump({"days": {day: {"state": "ready"}, "2026-10-11": {"state": "uploaded"}}}, open(ws, "w"))
before_folder = sorted(os.listdir(folder))

p = R.plan(day, root, abo, cache)
ok(len(p["cache"]) == 2 and p["drafts"] and p["pack"] and not p["uploaded"], "the plan finds the day's drafts, review pack and only THIS day's cached readings")
ok(all(os.path.exists(x) for x in (p["drafts"], p["pack"])) and len(os.listdir(cache)) == 3, "planning changes nothing")
dest, moved = R.do_reset(day, root, abo, cache, ws, arch, datetime.datetime(2026, 10, 13, 12, 0, 0))
ok(not os.path.exists(p["drafts"]) and not os.path.exists(p["pack"]) and os.listdir(cache) == [f"{other_day_pre}_type_v1.json"], "the day's folders and its cached readings are gone from their places")
ok(os.path.isdir(os.path.join(dest, "drafts_and_questions", day)) and os.path.isdir(os.path.join(dest, "review_pack_and_answers", f"day_{day}")) and len(os.listdir(os.path.join(dest, "vision_cache"))) == 2, "all of it is in the archive (moved, not deleted)")
ok(os.path.isdir(os.path.join(root, "jobs_drafts", "restaurant_day", "2026-10-11")) and os.path.isdir(os.path.join(root, "for_review", "day_2026-10-11")), "another day is not touched")
ok(sorted(os.listdir(folder)) == before_folder and open(photo, "rb").read() == b"photo-bytes", "the day's folder with the photos and sheets is not touched")
st = json.load(open(ws))
ok(day not in st["days"] and "2026-10-11" in st["days"] and os.path.isfile(os.path.join(dest, "watcher_status_note.json")), "only that day's note leaves the watcher status, and it is kept in the archive")
os.makedirs(os.path.join(root, "jobs_drafts", "restaurant_day", "2026-10-09")); open(os.path.join(root, "jobs_drafts", "restaurant_day", "2026-10-09", "upload.json"), "w").write("{}")
try:
    R.do_reset("2026-10-09", root, abo, cache, ws, arch); refused = False
except SystemExit as e:
    refused = "already uploaded" in str(e)
ok(refused and os.path.isdir(os.path.join(root, "jobs_drafts", "restaurant_day", "2026-10-09")), "a day already uploaded to the app is refused and left alone")
# --- a folder held open by another process (Windows refuses to remove it at the end of a move)
t2 = tempfile.mkdtemp()
root2, abo2, cache2, arch2 = (os.path.join(t2, x) for x in ("root", "abo", "cache", "arch"))
os.makedirs(os.path.join(abo2, "10-2026", "12-10-2026")); os.makedirs(cache2)
dd = os.path.join(root2, "jobs_drafts", "restaurant_day", day); os.makedirs(dd); open(os.path.join(dd, "x.json"), "w").write("{}")
pk = os.path.join(root2, "for_review", f"day_{day}"); os.makedirs(pk); open(os.path.join(pk, "answers.json"), "w").write("{}")
here = os.getcwd(); os.chdir(dd)
try:
    d2, mv2 = R.do_reset(day, root2, abo2, cache2, os.path.join(t2, "none.json"), arch2)
finally:
    os.chdir(here)
ok(os.path.isfile(os.path.join(d2, "drafts_and_questions", day, "x.json")) and os.path.isfile(os.path.join(d2, "review_pack_and_answers", f"day_{day}", "answers.json")) and not os.path.exists(pk),
   "a drafts folder that is open in another process no longer stops the reset: the content is archived and the review pack is still moved")
print(f"\n{n} checks passed")
