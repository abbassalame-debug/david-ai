"""
reset_restaurant_day.py -- start one restaurant day over, from scratch. Nothing is deleted: everything David holds for that day is MOVED into
<DAVID_HOME>\\_reset_archive\\<day>_<time>\\ (and can be moved back).

  py -3.11 scripts\\reset_restaurant_day.py 2026-10-12            show what would be moved (changes nothing)
  py -3.11 scripts\\reset_restaurant_day.py 2026-10-12 --do       move it

Moved:  the drafts and questions   jobs_drafts\\restaurant_day\\<day>\\
        the review pack + answers  for_review\\day_<day>\\
        the cached photo readings of THIS day's files (so a fresh read really re-reads them, instead of returning the same cached values)
        this day's note in the watcher status
Never touched:  the day's folder in Demo Bistro (photos and sheets), the app, anything of another day.
Refuses a day that is already uploaded (upload.json): that day is in the app.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA, DAVID_HOME, DAVID_RESTAURANT_ROOT
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = str(DAVID_DATA)
RESTAURANT = str(DAVID_RESTAURANT_ROOT)
CACHE_DIR = os.path.join(ROOT, "jobs_state", "restaurant_vision")
ARCHIVE = os.path.join(ROOT, "_reset_archive")
WATCH_STATUS = os.path.join(ROOT, "restaurant_watch", "status.json")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def move_dir(src, target):
    """shutil.move, except that on Windows a folder held open by another process (even a shell sitting inside it) cannot be removed at the end: the content is already
    copied then, so an EMPTY leftover folder is accepted; a folder that still has files in it is a real failure."""
    try:
        shutil.move(src, target)
    except PermissionError:
        if any(fs for _, _, fs in os.walk(src)) or not os.path.isdir(target):
            raise


def plan(day, root=ROOT, abo=RESTAURANT, cache_dir=CACHE_DIR):
    d = datetime.date.fromisoformat(day)
    drafts = os.path.join(root, "jobs_drafts", "restaurant_day", day)
    pack = os.path.join(root, "for_review", f"day_{day}")
    folder = os.path.join(abo, f"{d.month:02d}-{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}")
    prefixes = set()
    if os.path.isdir(folder):
        for r, _, fs in os.walk(folder):
            for f in fs:
                if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    prefixes.add(sha(os.path.join(r, f))[:24])
    cache = sorted(c for c in glob.glob(os.path.join(cache_dir, "*.json")) if os.path.basename(c)[:24] in prefixes)
    return {"day": day, "uploaded": os.path.isfile(os.path.join(drafts, "upload.json")), "drafts": drafts if os.path.isdir(drafts) else None,
            "pack": pack if os.path.isdir(pack) else None, "cache": cache, "folder": folder}


def do_reset(day, root=ROOT, abo=RESTAURANT, cache_dir=CACHE_DIR, watch_status=WATCH_STATUS, archive=ARCHIVE, now=None):
    p = plan(day, root, abo, cache_dir)
    if p["uploaded"]:
        raise SystemExit(f"{day} is already uploaded to the app (upload.json); it is not reset.")
    dest = os.path.join(archive, f"{day}_{(now or datetime.datetime.now()):%Y%m%d_%H%M%S}")
    moved = []
    for key, sub in (("drafts", "drafts_and_questions"), ("pack", "review_pack_and_answers")):
        if p[key]:
            os.makedirs(os.path.join(dest, sub), exist_ok=True)
            target = os.path.join(dest, sub, os.path.basename(p[key]))
            move_dir(p[key], target)
            moved.append((p[key], target))
    if p["cache"]:
        os.makedirs(os.path.join(dest, "vision_cache"), exist_ok=True)
        for c in p["cache"]:
            shutil.move(c, os.path.join(dest, "vision_cache", os.path.basename(c)))
        moved.append((f"{len(p['cache'])} cached photo readings", os.path.join(dest, "vision_cache")))
    if os.path.isfile(watch_status):
        st = json.load(open(watch_status, encoding="utf-8"))
        if day in st.get("days", {}):
            os.makedirs(dest, exist_ok=True)
            json.dump({day: st["days"].pop(day)}, open(os.path.join(dest, "watcher_status_note.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            json.dump(st, open(watch_status, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            moved.append(("the watcher's note for the day", os.path.join(dest, "watcher_status_note.json")))
    return dest, moved


def main(argv=None):
    a = argv or sys.argv[1:]
    if not a or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a[0]):
        print(__doc__)
        return 2
    day = a[0]
    p = plan(day)
    print(f"Reset of {day}")
    print("  drafts and questions :", p["drafts"] or "none")
    print("  review pack, answers :", p["pack"] or "none")
    print(f"  cached photo readings: {len(p['cache'])} file(s)")
    print("  day folder (never touched):", p["folder"])
    if p["uploaded"]:
        print("  REFUSED: this day is already uploaded to the app.")
        return 1
    if "--do" not in a:
        print("\nNothing changed. Add --do to move these into _reset_archive.")
        return 0
    dest, moved = do_reset(day)
    print(f"\nMoved into {dest}:")
    for src, tgt in moved:
        print("  ", src, "->", tgt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
