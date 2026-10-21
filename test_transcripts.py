"""test_transcripts.py — chat transcript retention (transcripts.py, 2026-10-21, audit P1-7 / S-02).

The setting DAVID_TRANSCRIPTS (unset = 90 days, <n> days, keep, off), the one writer, the prune (only <date>.md files, only
older than the retention), the one-time backup before the first prune, and that the HUD writes through this module.

No live data: every path points at a temporary folder.
"""
from david_config import DAVID_HOME
import datetime, os, shutil, sys, tempfile
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
os.environ.pop("DAVID_TRANSCRIPTS", None)

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import transcripts as tr

TODAY = datetime.date(2026, 10, 21)

def env(value):
    return mock.patch.dict(os.environ, {"DAVID_TRANSCRIPTS": value} if value is not None else {}, clear=False)

def sandbox():
    """A temporary transcript folder and backup folder, patched into the module."""
    tmp = tempfile.mkdtemp(prefix="transcripts_")
    d, b = os.path.join(tmp, "interactions"), os.path.join(tmp, "transcript_backups")
    os.makedirs(d)
    patches = [mock.patch.object(tr, "DIR", d), mock.patch.object(tr, "BACKUP_DIR", b),
               mock.patch.object(tr, "FIRST_PRUNE_MARKER", os.path.join(b, ".first_prune_done")), mock.patch.object(tr, "_last_prune_day", None)]
    for p in patches:
        p.start()
    return tmp, d, b, patches

def close(tmp, patches):
    for p in reversed(patches):
        p.stop()
    shutil.rmtree(tmp, ignore_errors=True)

def make_day(d, day, text=None):
    path = os.path.join(d, f"{day:%Y-%m-%d}.md")
    open(path, "w", encoding="utf-8").write(text or f"## 10:00 — analyst\n**You:** hello {day}\n")
    return path

# ── 1. the setting ───────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 1. DAVID_TRANSCRIPTS ──")
os.environ.pop("DAVID_TRANSCRIPTS", None)
check("unset -> keep 90 days (the owner's default)", tr.setting() == ("days", 90))
for value, want in (("30", ("days", 30)), ("365", ("days", 365)), ("keep", ("keep", None)), ("forever", ("keep", None)),
                    ("off", ("off", None)), (" OFF ", ("off", None)), ("0", ("days", 90)), ("-5", ("days", 90)), ("ten", ("days", 90)), ("", ("days", 90))):
    with env(value):
        check(f"DAVID_TRANSCRIPTS={value!r} -> {want}", tr.setting() == want, str(tr.setting()))

# ── 2. the writer ────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 2. append ──")
tmp, d, b, patches = sandbox()
try:
    now = datetime.datetime(2026, 10, 21, 9, 30)
    with env(None):
        ok = tr.append("\n## 09:30 — analyst\n**You:** hi\n**David:** hello\n", now)
        ok2 = tr.append("\n## 09:31 — cfo\n**You:** numbers\n**David:** here\n", now)
    text = open(os.path.join(d, "2026-10-21.md"), encoding="utf-8").read()
    check("append writes to today's <date>.md and keeps earlier entries", ok and ok2 and "— analyst" in text and "— cfo" in text)
    with env("off"):
        ok = tr.append("\n## 09:40 — analyst\n**You:** secret\n", now)
    check("DAVID_TRANSCRIPTS=off writes nothing", not ok and "secret" not in open(os.path.join(d, "2026-10-21.md"), encoding="utf-8").read())
    check("…and leaves the existing file as it was", "— cfo" in open(os.path.join(d, "2026-10-21.md"), encoding="utf-8").read())
finally:
    close(tmp, patches)

# ── 3. the prune ─────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 3. prune: only <date>.md files older than the retention ──")
tmp, d, b, patches = sandbox()
try:
    old1 = make_day(d, TODAY - datetime.timedelta(days=130), "OLDEST")
    old2 = make_day(d, TODAY - datetime.timedelta(days=91), "JUST-TOO-OLD")
    edge = make_day(d, TODAY - datetime.timedelta(days=90))
    recent = make_day(d, TODAY - datetime.timedelta(days=1))
    other = os.path.join(d, "notes.md"); open(other, "w").write("not a day file")
    bad_date = os.path.join(d, "2026-13-40.md"); open(bad_date, "w").write("not a real date")
    old_dir = os.path.join(d, "2026-01-01.md"); os.makedirs(old_dir)          # a folder that looks like a day file
    nested = os.path.join(d, "sub"); os.makedirs(nested); make_day(nested, TODAY - datetime.timedelta(days=400))

    with env("keep"):
        check("keep: nothing is pruned", tr.prune(TODAY) == [] and os.path.exists(old1))
    with env("off"):
        check("off: nothing is pruned (off only stops writing)", tr.prune(TODAY) == [] and os.path.exists(old1))
    check("no backup folder exists before the first real prune", not os.path.exists(b))

    with env(None):
        removed = tr.prune(TODAY)
    check("90 days: the two files older than 90 days are deleted", sorted(removed) == sorted([os.path.basename(old1), os.path.basename(old2)])
          and not os.path.exists(old1) and not os.path.exists(old2), str(removed))
    check("a file exactly 90 days old and yesterday's file are kept", os.path.exists(edge) and os.path.exists(recent))
    check("anything that is not a <date>.md file directly in the folder is left alone",
          os.path.exists(other) and os.path.exists(bad_date) and os.path.isdir(old_dir) and len(os.listdir(nested)) == 1)
    baks = sorted(f for f in os.listdir(b) if f.endswith(".md"))
    check("the first prune copied exactly what it deleted into transcript_backups/", baks == sorted(removed), str(baks))
    check("…with identical content", open(os.path.join(b, os.path.basename(old1)), encoding="utf-8").read() == "OLDEST"
          and open(os.path.join(b, os.path.basename(old2)), encoding="utf-8").read() == "JUST-TOO-OLD")
    check("…and left the first-prune marker", os.path.exists(tr.FIRST_PRUNE_MARKER))

    later = make_day(d, TODAY - datetime.timedelta(days=200), "LATER")
    with env(None):
        removed = tr.prune(TODAY)
    check("a later prune deletes without making another copy", removed == [os.path.basename(later)] and not os.path.exists(later)
          and os.path.basename(later) not in os.listdir(b), str(removed))
    with env("30"):
        removed = tr.prune(TODAY)
    check("DAVID_TRANSCRIPTS=30 prunes the 90-day-old file too", os.path.basename(edge) in removed and os.path.exists(recent), str(removed))
finally:
    close(tmp, patches)

print("\n── 4. a failed backup deletes nothing; once a day ──")
tmp, d, b, patches = sandbox()
try:
    old = make_day(d, TODAY - datetime.timedelta(days=120), "KEEP-ME")
    with env(None), mock.patch.object(tr.shutil, "copy2", side_effect=OSError("disk full")):
        removed = tr.prune(TODAY)
    check("if the first-prune backup cannot be made, nothing is deleted", removed == [] and os.path.exists(old)
          and open(old, encoding="utf-8").read() == "KEEP-ME" and not os.path.exists(tr.FIRST_PRUNE_MARKER))
    with env(None):
        first = tr.prune_daily(TODAY)
        make_day(d, TODAY - datetime.timedelta(days=150))
        second = tr.prune_daily(TODAY)
        third = tr.prune_daily(TODAY + datetime.timedelta(days=1))
    check("prune_daily runs once per day: the first call prunes, a second call the same day does nothing, the next day prunes again",
          first == [os.path.basename(old)] and second == [] and len(third) == 1, f"{first} {second} {third}")
finally:
    close(tmp, patches)

# ── 5. the HUD writes through this module ────────────────────────────────────────────────────────────────────────────
print("\n── 5. the HUD uses transcripts.py ──")
src = open(os.path.join(str(DAVID_HOME), "ui", "server.py"), encoding="utf-8").read()
check("the HUD has no transcript writer of its own any more (no CRM/interactions path built in server.py)",
      '"CRM"' not in src and "mem_file" not in src)
check("both HUD writers (chat turn, war room in chat) go through _transcripts.append", src.count("_transcripts.append(") == 2)
check("the HUD prunes when it starts", "_transcripts.prune_daily" in src.split("async def startup")[1][:400])
check("the morning briefing reads yesterday's file through _transcripts.day_file", "_transcripts.day_file(" in src)
# server.py also has a LOCAL list called `transcripts` in the war-room code. A local name shadows a module for the whole
# function (Python decides at compile time), which made a first draft's writes fail silently. Ask Python's own symbol table.
import symtable
top = symtable.symtable(src, "server.py", "exec")
def scopes(table):
    yield table
    for child in table.get_children():
        yield from scopes(child)
users = [t for t in scopes(top) if t.get_type() == "function" and "_transcripts" in [s.get_name() for s in t.get_symbols()]]
check("in every function that uses it, _transcripts is the module (global), never a local name",
      users and all(t.lookup("_transcripts").is_global() for t in users), str([(t.get_name(), t.lookup("_transcripts").is_global()) for t in users]))
check("…including ws_chat (chat turns and war rooms), the briefing and start-up", {"ws_chat", "api_briefing", "startup"} <= {t.get_name() for t in users},
      str(sorted({t.get_name() for t in users})))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
