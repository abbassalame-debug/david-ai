"""test_restaurant_watch.py -- offline tests for the day-folder watcher (restaurant_watch.py). Temp folders only; no Ollama, no server, no app."""
from david_config import DAVID_HOME
import os, re, sys, json, time, hashlib, tempfile, datetime
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
import restaurant_watch as W

n = 0
def ok(c, m):
    global n
    n += 1
    assert c, "FAIL: " + m
    print("ok", n, m)

NOW = datetime.datetime(2026, 10, 13, 9, 0, 0)

def fresh():
    t = tempfile.mkdtemp()
    cfg = {"restaurant_root": os.path.join(t, "abo"), "days_dir": os.path.join(t, "days"), "state_dir": os.path.join(t, "state")}
    os.makedirs(cfg["restaurant_root"]); os.makedirs(cfg["days_dir"])
    return cfg

def day_dir(cfg, d):
    return os.path.join(cfg["restaurant_root"], f"{d.month:02d}-{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}")

def put(folder, name, size=500, age=1000):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    open(p, "wb").write(name.encode("utf-8") * (size // 8 + 1))
    t = NOW.timestamp() - age
    os.utime(p, (t, t))
    return p

def fill(folder, photos=7, sheets=3, **kw):
    for i in range(photos):
        put(folder, f"WhatsApp Image 2026-10-12 at 9.1{i}.jpeg", **kw)
    for nm in ("Branch Beef ^0 Chicken Daily consumption 12-10-2026.xlsx", "itemized beef and chicken breakdown 12-10-2026.xlsx", "فوارق الشك مع الاوزان 12-10-2026.xlsx")[:sheets]:
        put(folder, nm, **kw)

def tree_hash(root):
    h = hashlib.sha256()
    for r, ds, fs in sorted(os.walk(root)):
        for f in sorted(fs):
            p = os.path.join(r, f); h.update(p.encode()); h.update(open(p, "rb").read()); h.update(str(os.path.getmtime(p)).encode())
        h.update(("|".join(sorted(ds))).encode())
    return h.hexdigest()

calls = []
def direct(day, log_fn, cfg=None):
    calls.append(day)
    return "direct", {"valid": True}

D12 = datetime.date(2026, 10, 12)

# --- complete day: read once
cfg = fresh(); fill(day_dir(cfg, D12))
put(day_dir(cfg, D12), "Restaurant_Manager_Schedule 5-10-2026  _11 -10-2026.xlsx")
before = tree_hash(cfg["restaurant_root"])
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == ["2026-10-12"] and st["days"]["2026-10-12"]["state"] == "ready", "a complete day folder is read once and marked ready")
ok(tree_hash(cfg["restaurant_root"]) == before, "the Demo Bistro folder is byte-for-byte and timestamp-for-timestamp unchanged: nothing moved, renamed, written or deleted")
W.cycle(cfg, now=NOW + datetime.timedelta(minutes=5), prepare=direct)
ok(calls == ["2026-10-12"], "the next cycle with the same files does nothing")

# --- new file arrives later
put(day_dir(cfg, D12), "WhatsApp Image 2026-10-12 at 9.99.jpeg")
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=10), prepare=direct)
ok(calls == ["2026-10-12", "2026-10-12"], "a new file in the folder (not approved yet) is read again")

# --- approved or uploaded: never read again
os.makedirs(os.path.join(cfg["days_dir"], "2026-10-12")); open(os.path.join(cfg["days_dir"], "2026-10-12", "approval.json"), "w").write("{}")
put(day_dir(cfg, D12), "WhatsApp Image 2026-10-12 at 9.98.jpeg")
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=15), prepare=direct)
ok(len(calls) == 2 and st["days"]["2026-10-12"]["state"] == "approved" and "changed after the read" in st["days"]["2026-10-12"]["message"], "once you approved, new files are reported but never trigger a read")
open(os.path.join(cfg["days_dir"], "2026-10-12", "upload.json"), "w").write("{}")
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=20), prepare=direct)
ok(len(calls) == 2 and st["days"]["2026-10-12"]["state"] == "uploaded", "an uploaded day is left alone")

# --- incomplete or still arriving
calls.clear()
cfg = fresh(); fill(day_dir(cfg, D12), photos=3, sheets=2)
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == [] and st["days"]["2026-10-12"]["state"] == "waiting" and "1 more photo(s)" in st["days"]["2026-10-12"]["message"] and "1 more Excel sheet(s)" in st["days"]["2026-10-12"]["message"], "an incomplete day waits and says what is missing")
cfg = fresh(); fill(day_dir(cfg, D12), age=20)
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == [] and "still arriving" in st["days"]["2026-10-12"]["message"], "files that changed a moment ago are still arriving")
cfg = fresh(); fill(day_dir(cfg, D12), sheets=2); put(day_dir(cfg, D12), "Weekly Schedule 12-10-2026.xlsx")
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == [] and st["days"]["2026-10-12"]["state"] == "waiting", "a rota sheet does not count as one of the three day sheets")


# --- history is never touched (days entered by hand before the page existed)
calls.clear()
cfg = fresh()
for d in (datetime.date(2026, 10, 7), datetime.date(2026, 10, 6), datetime.date(2026, 10, 11), D12):
    fill(day_dir(cfg, d))
os.makedirs(os.path.join(cfg["days_dir"], "2026-10-11")); open(os.path.join(cfg["days_dir"], "2026-10-11", "upload.json"), "w").write("{}")
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == ["2026-10-12"] and "2026-10-07" not in st["days"] and "2026-10-06" not in st["days"], "only days newer than the newest uploaded day are read; older hand-entered days are never touched")
calls.clear()
cfg = fresh()
for d in (datetime.date(2026, 10, 7), datetime.date(2026, 10, 11), D12):
    fill(day_dir(cfg, d))
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(calls == ["2026-10-12", "2026-10-11"] and "2026-10-07" not in st["days"], "with no uploaded day yet, only the last 3 days are looked at")

# --- several days, old days, empty month
calls.clear()
cfg = fresh()
fill(day_dir(cfg, D12)); fill(day_dir(cfg, datetime.date(2026, 10, 11)))
fill(day_dir(cfg, datetime.date(2026, 10, 3)))
st = W.cycle(cfg, now=NOW, prepare=direct)
ok(sorted(calls) == ["2026-10-11", "2026-10-12"], "each complete recent day is read; a day older than that is ignored")
ok(W.cycle(fresh(), now=NOW, prepare=direct)["days"] == {}, "no day folders: nothing to do")

# --- busy, server, failure
calls.clear()
cfg = fresh(); fill(day_dir(cfg, D12))
seq = iter(["busy", "direct"])
def busy_then(day, l, c=None):
    calls.append(day); return ("busy", "x") if next(seq) == "busy" else ("direct", {})
st = W.cycle(cfg, now=NOW, prepare=busy_then)
ok(st["days"]["2026-10-12"]["state"] == "reading" and "waiting for the page" in st["days"]["2026-10-12"]["message"], "when the page is busy the day is retried")
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=5), prepare=busy_then)
ok(st["days"]["2026-10-12"]["state"] == "ready" and len(calls) == 2, "the next cycle gets it done")

cfg = fresh(); fill(day_dir(cfg, D12))
st = W.cycle(cfg, now=NOW, prepare=lambda d, l, c=None: ("server", {}), job_probe=lambda: {"day": "2026-10-12", "state": "running"})
ok(st["days"]["2026-10-12"]["state"] == "reading", "the page's server took the read")
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=5), job_probe=lambda: {"day": "2026-10-12", "state": "done"})
ok(st["days"]["2026-10-12"]["state"] == "ready", "and the status becomes ready when its job is done")

cfg = fresh(); fill(day_dir(cfg, D12))
def boom(d, l, c=None): raise RuntimeError("vision model not answering")
st = W.cycle(cfg, now=NOW, prepare=boom)
ok(st["days"]["2026-10-12"]["state"] == "error", "a failed read is reported")
calls.clear()
st = W.cycle(cfg, now=NOW + datetime.timedelta(minutes=5), prepare=direct)
ok(calls == [], "and it does not retry in a loop on the same files (the page's Read button is the retry)")

# --- lock
p = W.paths(fresh())
ok(W.take_lock(p) is True and W.take_lock(p) is False, "a second cycle is refused while one runs")
os.utime(p["lock"], (time.time() - 5 * 3600,) * 2)
ok(W.take_lock(p) is True, "a lock older than 3 hours is taken over even if its owner is alive")
open(p["lock"], "w").write("999999")
ok(W.take_lock(p) is True, "a lock whose owner process is gone is taken over at once (a killed or crashed cycle never blocks the next one)")
W.release_lock(p)

# --- the safety boundary
src = open(str(DAVID_HOME / "restaurant_watch.py"), encoding="utf-8").read()
ok(not re.search(r"^\s*(import|from)\s+(restaurant_upload|restaurant_sql|psycopg|restaurant_corrections)", src, re.M), "the watcher imports nothing that can upload, build SQL or approve")
body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
body = body.split('"""', 2)[2]
writes = re.findall(r'open\(([^,()]+(?:\[[^\]]*\])?),\s*"([wa])"', body)
ok(writes and all(a.strip() in ('tmp', 'p["lock"]', 'p["log"]') for a, _ in writes), 'every file the watcher opens for writing is its own temp-status, lock or log: ' + str(writes))
ok(not re.search(r'shutil\.|os\.(unlink|rename)\(|rmtree', body) and re.findall(r'os\.remove\(([^)]*)\)', body) == ['p["lock"]'], 'it moves, renames and deletes nothing; the only removal is its own lock')
print(f"\n{n} checks passed")
