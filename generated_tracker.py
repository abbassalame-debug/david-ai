# generated_tracker.py — Central log for every file David creates
# Called by adobe_bridge.py and office_bridge.py after each successful execution.
# Writes to two places:
#   1. <DAVID_HOME>\generated\index.json  — searchable manifest of all generated files
#   2. <DAVID_HOME>\vaults\Business\David-Generated\  — Obsidian notes (picked up by rag.py on next ingest)

from david_config import DAVID_DATA, DAVID_VAULTS
import os
import json
import datetime

MANIFEST    = str(DAVID_DATA / "generated" / "index.jsonl")
VAULT_NOTES = str(DAVID_VAULTS / "Business" / "David-Generated")

os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
os.makedirs(VAULT_NOTES, exist_ok=True)

APP_TAGS = {
    "photoshop":    ["adobe", "photoshop", "image", "design"],
    "illustrator":  ["adobe", "illustrator", "vector", "design"],
    "indesign":     ["adobe", "indesign", "print", "design"],
    "aftereffects": ["adobe", "after-effects", "motion", "video"],
    "premiere":     ["adobe", "premiere", "video"],
    "word":         ["microsoft", "word", "document", "office"],
    "excel":        ["microsoft", "excel", "spreadsheet", "office"],
    "powerpoint":   ["microsoft", "powerpoint", "presentation", "office"],
}

_SKIP_SPEC_KEYS = {"close_after", "native_save", "save_path", "layer_groups",
                   "content", "slides", "sheets"}


def log_file(app: str, file_path: str, request: str = "", spec: dict = None) -> str:
    """
    Append a record to the manifest and write an Obsidian note.
    Returns the path to the note that was written.
    """
    now      = datetime.datetime.now()
    ts_iso   = now.isoformat(timespec="seconds")
    ts_file  = now.strftime("%Y%m%d_%H%M%S")
    app_key  = app.lower()
    fname    = os.path.basename(file_path) if file_path else "(queue)"
    ext      = os.path.splitext(fname)[1].lower()
    tags     = APP_TAGS.get(app_key, ["david-generated"])

    # ── 1. Manifest ───────────────────────────────────────────────────────────
    entry = {
        "ts":      ts_iso,
        "app":     app,
        "file":    file_path,
        "name":    fname,
        "ext":     ext,
        "request": (request or "")[:300],
    }
    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 2. Obsidian note ──────────────────────────────────────────────────────
    safe = fname.replace(" ", "-")
    note_path = os.path.join(VAULT_NOTES, f"{ts_file}-{safe}.md")
    tag_yaml  = ", ".join(f'"{t}"' for t in ["david-generated"] + tags)

    # Summarise spec — skip binary/large keys, show high-level counts
    spec_lines = []
    if spec:
        for k, v in spec.items():
            if k in _SKIP_SPEC_KEYS:
                if isinstance(v, list):
                    spec_lines.append(f"- **{k}:** {len(v)} item(s)")
            else:
                spec_lines.append(f"- **{k}:** {v}")
    spec_block = "\n".join(spec_lines) if spec_lines else "*(default spec)*"

    note = f"""---
created: {ts_iso}
app: {app}
file: "{file_path}"
ext: "{ext}"
tags: [{tag_yaml}]
---

# {app.title()} — {now.strftime("%Y-%m-%d %H:%M")}

**File:** `{file_path}`
**App:** {app.title()}
**Request:** {request or "*(default spec)*"}

## Spec
{spec_block}
"""
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)

    return note_path


def recent(n: int = 20) -> list[dict]:
    """Return the n most recently generated files from the manifest."""
    if not os.path.exists(MANIFEST):
        return []
    try:
        with open(MANIFEST, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        return entries[-n:][::-1]
    except Exception:
        return []


def search(keyword: str) -> list[dict]:
    """Simple keyword search over manifest (app, name, request fields)."""
    kw = keyword.lower()
    results = []
    for e in recent(1000):
        if (kw in e.get("app", "").lower() or
                kw in e.get("name", "").lower() or
                kw in e.get("request", "").lower()):
            results.append(e)
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for e in recent(20):
            print(f"  {e['ts']}  {e['app']:14s}  {e['name']}")
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        for e in search(" ".join(sys.argv[2:])):
            print(f"  {e['ts']}  {e['app']:14s}  {e['name']}  — {e['request'][:60]}")
    else:
        print("Usage: python generated_tracker.py list")
        print("       python generated_tracker.py search <keyword>")
