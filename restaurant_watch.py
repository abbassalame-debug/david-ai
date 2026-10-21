"""
restaurant_watch.py -- the DAY-FOLDER WATCHER of Job 1.

The manager already keeps every day in  Demo Bistro\\<MM-YYYY>\\<DD-MM-YYYY>\\  (photos + the three Excel sheets). Nothing new is dropped anywhere. A scheduled
task runs one cycle of this script every 5 minutes. A cycle looks at the last 10 day folders that are NEWER than the newest day already uploaded through the page (older days were entered by hand and are history), and, for each one that is COMPLETE, asks for the same `prepare`
step the Restaurant page's button runs, so the draft and its questions are waiting when the manager opens the page.

A day folder is complete when its files have stopped changing (150 s), and it holds at least 4 photos and the 3 Excel sheets (consumption, itemized breakdown,
weights). Weekly schedule files (`schedule`, `جدول`) are not counted.

It READS the day folders and nothing else there: it never moves, renames, writes or deletes a file in the Demo Bistro tree. It never approves a day, builds or runs
SQL, uploads, or touches the app's database. A day that is already uploaded, or that the manager already approved, is not read again. If files change after a
read, the day is read again only while it is not approved.

  py -3.11 restaurant_watch.py once       one cycle (what the scheduled task runs)
  py -3.11 restaurant_watch.py status     print the current status
"""
from david_config import DAVID_DATA, DAVID_RESTAURANT_ROOT
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESTAURANT_ROOT = str(DAVID_RESTAURANT_ROOT)
STATE_DIR = str(DAVID_DATA / "restaurant_watch")
DAYS_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant_day")
SERVER = "http://127.0.0.1:8000"
LOOK_BACK_DAYS = 10
STABLE_SECS = 150
MIN_PHOTOS, MIN_SHEETS = 4, 3
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SHEET_EXT = {".xlsx", ".xlsm", ".xls", ".csv"}
SCHEDULE_NAME = re.compile(r"schedule|جدول", re.I)      # rota files carry a week, never a business day
LOCK_STALE_SECS = 3 * 3600


def now_iso(now=None):
    return (now or datetime.datetime.now()).isoformat(timespec="seconds")


def paths(cfg=None):
    cfg = cfg or {}
    state = cfg.get("state_dir") or STATE_DIR
    return {"abo": cfg.get("restaurant_root") or RESTAURANT_ROOT, "days": cfg.get("days_dir") or DAYS_DIR, "state": state, "status": os.path.join(state, "status.json"),
            "log": os.path.join(state, "log.txt"), "lock": os.path.join(state, "cycle.lock")}


def log(p, msg):
    os.makedirs(p["state"], exist_ok=True)
    with open(p["log"], "a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {msg}\n")


def load_status(p):
    try:
        return json.load(open(p["status"], encoding="utf-8"))
    except (OSError, ValueError):
        return {"updated": None, "days": {}}


def save_status(p, st):
    st["updated"] = now_iso()
    os.makedirs(p["state"], exist_ok=True)
    tmp = p["status"] + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p["status"])


def _pid_alive(pid):
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259


def take_lock(p, now=None):
    """One cycle at a time. A lock whose owner process is gone (killed, crashed, power cut) is taken over at once; one whose owner is alive blocks for up to 3 hours."""
    os.makedirs(p["state"], exist_ok=True)
    t = (now or datetime.datetime.now()).timestamp()
    if os.path.isfile(p["lock"]):
        try:
            pid = int(open(p["lock"]).read().strip() or 0)
            if pid and _pid_alive(pid) and t - os.path.getmtime(p["lock"]) < LOCK_STALE_SECS:
                return False
        except (OSError, ValueError):
            pass
    open(p["lock"], "w").write(str(os.getpid()))
    return True


def release_lock(p):
    try:
        os.remove(p["lock"])
    except OSError:
        pass


def day_folders(p, today, back=LOOK_BACK_DAYS):
    """[(iso day, folder)] for the last `back` days (today included) whose folder exists, newest first."""
    out = []
    for k in range(back):
        d = today - datetime.timedelta(days=k)
        folder = os.path.join(p["abo"], f"{d.month:02d}-{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}")
        if os.path.isdir(folder):
            out.append((d.isoformat(), folder))
    return out


def newest_uploaded(p):
    """The newest day that went through the page and was uploaded (its upload.json). Earlier days are history (many were entered by hand before the page existed)."""
    days = [d for d in (os.listdir(p["days"]) if os.path.isdir(p["days"]) else []) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and os.path.isfile(os.path.join(p["days"], d, "upload.json"))]
    return max(days) if days else None


def inspect(folder, now_ts):
    """What a day folder holds: counts, whether every file has stopped changing, and a fingerprint (names + sizes) to tell new files from the same ones."""
    photos = sheets = 0
    unstable, items = [], []
    for r, _, files in os.walk(folder):
        for f in files:
            if f.startswith(("~$", ".")) or f.lower() in ("desktop.ini", "thumbs.db") or SCHEDULE_NAME.search(f):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in PHOTO_EXT | SHEET_EXT | {".pdf"}:
                continue
            full = os.path.join(r, f)
            try:
                size, mt = os.path.getsize(full), os.path.getmtime(full)
            except OSError:
                continue
            items.append((os.path.relpath(full, folder), size))
            photos += ext in PHOTO_EXT
            sheets += ext in SHEET_EXT
            if now_ts - mt < STABLE_SECS:
                unstable.append(f)
    missing = []
    if photos < MIN_PHOTOS:
        missing.append(f"{MIN_PHOTOS - photos} more photo(s)")
    if sheets < MIN_SHEETS:
        missing.append(f"{MIN_SHEETS - sheets} more Excel sheet(s)")
    return {"photos": photos, "sheets": sheets, "unstable": unstable, "missing": missing, "fingerprint": json.dumps(sorted(items), ensure_ascii=False), "ready": not unstable and not missing}


# ---- asking for the read (the page's server when it is up, otherwise this process)
def _http(method, path, body=None, timeout=8):
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def request_prepare(day, log_fn, cfg=None):
    """-> ('server', job) | ('busy', msg) | ('direct', result); raises on a real failure."""
    try:
        return "server", _http("POST", "/api/restaurant/prepare", {"day": day})
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return "busy", "the page is reading another day"
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        import restaurant_day as D
        c = cfg or {}
        r = D.prepare(day, log=log_fn, restaurant_root=c.get("restaurant_root") or RESTAURANT_ROOT, days_dir=c.get("days_dir") or DAYS_DIR)
        return "direct", r


def server_job():
    try:
        return _http("GET", "/api/restaurant/job")
    except Exception:
        return None


def cycle(cfg=None, now=None, prepare=request_prepare, job_probe=server_job):
    p = paths(cfg)
    now = now or datetime.datetime.now()
    if not take_lock(p, now):
        return {"busy": True}
    try:
        st = load_status(p)
        st.setdefault("days", {})
        # reads handed to the page's server earlier
        for day, e in st["days"].items():
            if e.get("state") == "reading" and e.get("via") == "server":
                j = job_probe()
                if j and j.get("day") == day and j.get("state") == "done":
                    e.update(state="ready", message="the draft is ready: open the Restaurant page and answer the questions", at=now_iso(now))
                    log(p, f"{day}: read finished (page job)")
                elif j and j.get("day") == day and j.get("state") == "error":
                    e.update(state="error", message="the read failed: " + str(j.get("message"))[:200] + " (the page's Read button tries again)", at=now_iso(now))
                    log(p, f"{day}: read failed: {j.get('message')}")
        floor = newest_uploaded(p)
        for day, folder in day_folders(p, now.date(), back=LOOK_BACK_DAYS if floor else 3):
            if floor and day <= floor and not os.path.isfile(os.path.join(p["days"], day, "upload.json")):
                continue          # older than the newest uploaded day: history, never read again
            dd = os.path.join(p["days"], day)
            e = st["days"].get(day, {})
            if os.path.isfile(os.path.join(dd, "upload.json")):
                st["days"][day] = {"state": "uploaded", "message": f"{day} is already in the app", "at": e.get("at") or now_iso(now)}
                continue
            info = inspect(folder, now.timestamp())
            if os.path.isfile(os.path.join(dd, "approval.json")):
                msg = f"{day} is approved and waiting for your upload"
                if e.get("fingerprint") and e["fingerprint"] != info["fingerprint"]:
                    msg += "; files in its folder changed after the read (use the page's Read button if they matter)"
                st["days"][day] = dict(e, state="approved", message=msg)
                continue
            if info["unstable"] or info["missing"]:
                msg = "files are still arriving: " + ", ".join(info["unstable"][:3]) if info["unstable"] else "waiting for " + " and ".join(info["missing"])
                st["days"][day] = {"state": "waiting", "message": f"{day}: {msg}", "photos": info["photos"], "sheets": info["sheets"], "at": now_iso(now)}
                continue
            if e.get("fingerprint") == info["fingerprint"] and e.get("state") in ("reading", "ready", "error"):
                continue          # these very files were already read (or the read failed on them); the page's Read button is the way to retry
            entry = {"state": "reading", "fingerprint": info["fingerprint"], "photos": info["photos"], "sheets": info["sheets"], "at": now_iso(now), "message": "starting the read"}
            try:
                how, res = prepare(day, lambda m, d=day: log(p, f"{d}: {m}"), cfg)
            except Exception as ex:
                entry.update(state="error", message=f"the read failed: {type(ex).__name__}: {ex}"[:300] + " (the page's Read button tries again)")
                st["days"][day] = entry
                log(p, f"{day}: read failed: {ex}")
                save_status(p, st)
                continue
            if how == "busy":
                entry.update(message=f"{day} is complete; waiting for the page to finish another day")
                entry["fingerprint"] = None          # try again next cycle
            elif how == "direct":
                entry.update(state="ready", via="direct", message="the draft is ready: open the Restaurant page and answer the questions")
                log(p, f"{day}: read finished")
            else:
                entry.update(via="server", message="reading the photos (the page is doing it)")
            st["days"][day] = entry
            save_status(p, st)
        save_status(p, st)
        return st
    finally:
        release_lock(p)


def status_text(cfg=None):
    st = load_status(paths(cfg))
    rows = [f"  [{d['state']}] {day}: {d['message']}" for day, d in sorted(st.get("days", {}).items(), reverse=True)]
    return "\n".join([f"updated {st.get('updated')}"] + rows) if rows else "no day folder seen yet"


def main(argv=None):
    a = (argv or sys.argv[1:]) or ["status"]
    if a[0] == "once":
        r = cycle()
        print("busy (another cycle is running)" if r.get("busy") else status_text())
    else:
        print(status_text())


if __name__ == "__main__":
    main()
