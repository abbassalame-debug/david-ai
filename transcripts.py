"""transcripts.py — the HUD's chat log, vaults/System/CRM/interactions/<date>.md: one writer and a retention rule (2026-10-21,
audit P1-7 / S-02).

Every chat turn and war-room result used to be appended to that folder forever. The setting DAVID_TRANSCRIPTS decides now:

  (unset)    keep DEFAULT_DAYS days (90)        a day file older than that is deleted by prune()
  <n>        keep n days                        e.g. DAVID_TRANSCRIPTS=30
  keep       keep forever (the old behaviour)
  off        write no transcript at all         existing files are left as they are

prune() runs when the HUD starts and at most once a day after that (from append()). It only ever touches files named
<YYYY-MM-DD>.md directly inside the transcript folder. The very first prune copies what it is about to delete into
DAVID_DATA/transcript_backups/ and leaves a marker there; later prunes delete without a copy.

The folder is never indexed (rag.EXCLUDED_PATH_MARKERS) and never readable by the tools (tool_executor._read_note).
"""
from david_config import DAVID_DATA, DAVID_VAULTS
import datetime
import os
import re
import shutil

DIR = str(DAVID_VAULTS / "System" / "CRM" / "interactions")
BACKUP_DIR = str(DAVID_DATA / "transcript_backups")
FIRST_PRUNE_MARKER = os.path.join(BACKUP_DIR, ".first_prune_done")
DEFAULT_DAYS = 90

_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_last_prune_day = None


def setting():
    """("off", None), ("keep", None) or ("days", n). Unset or unreadable -> the default number of days."""
    v = os.environ.get("DAVID_TRANSCRIPTS", "").strip().lower()
    if v == "off":
        return "off", None
    if v in ("keep", "forever"):
        return "keep", None
    if v.isdigit() and int(v) > 0:
        return "days", int(v)
    return "days", DEFAULT_DAYS


def day_file(day) -> str:
    return os.path.join(DIR, f"{day:%Y-%m-%d}.md")


def append(entry: str, now=None) -> bool:
    """Appends one entry to today's file, unless transcripts are off. Returns whether it wrote."""
    if setting()[0] == "off":
        return False
    now = now or datetime.datetime.now()
    os.makedirs(DIR, exist_ok=True)
    with open(day_file(now), "a", encoding="utf-8") as f:
        f.write(entry)
    prune_daily(now.date())
    return True


def prune_daily(today=None) -> list:
    """prune(), at most once per calendar day per process."""
    global _last_prune_day
    today = today or datetime.date.today()
    if _last_prune_day == today:
        return []
    _last_prune_day = today
    return prune(today)


def prune(today=None) -> list:
    """Deletes day files older than the retention. Returns the deleted file names. Never raises."""
    mode, days = setting()
    if mode != "days":
        return []
    today = today or datetime.date.today()
    try:
        names = os.listdir(DIR)
    except OSError:
        return []
    old = []
    for name in sorted(names):
        m = _DAY_FILE.match(name)
        if not m or not os.path.isfile(os.path.join(DIR, name)):
            continue
        try:
            day = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if (today - day).days > days:
            old.append(name)
    if not old:
        return []
    removed = []
    try:
        if not os.path.exists(FIRST_PRUNE_MARKER):
            # first prune: keep a copy of everything it removes; nothing is deleted unless every copy succeeded
            os.makedirs(BACKUP_DIR, exist_ok=True)
            for name in old:
                shutil.copy2(os.path.join(DIR, name), os.path.join(BACKUP_DIR, name))
            with open(FIRST_PRUNE_MARKER, "w", encoding="utf-8") as f:
                f.write(f"first transcript prune {datetime.datetime.now().isoformat(timespec='seconds')}: {len(old)} files copied here\n")
        for name in old:
            os.remove(os.path.join(DIR, name))
            removed.append(name)
    except OSError:
        pass
    return removed
