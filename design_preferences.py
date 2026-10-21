"""design_preferences.py -- the designer's preferences profile (Piece 0 of the Design portal). Pure functions, offline.

One JSON file (design_preferences.json). Every entry carries a status: "unset" (no value yet), "draft" (read from his files by the scan), "suggested"
(an industry default) or "confirmed" (the designer said yes or corrected it). Only the designer's own answers (confirm / correct / upsert / remove, each
with a name in approved_by) can make something confirmed; David and the seeding step never do. A bad value is refused with a sentence.

Run:  python design_preferences.py seed <scan.json> [font_inventory.json] [--force]     write the first draft profile from the metadata scan
      python design_preferences.py check                                             validate the saved profile
"""
from david_config import DAVID_DATA, DAVID_HOME
import os
import re
import sys
import json
import copy
import datetime

import design_naming

PATH = str(DAVID_DATA / "design_preferences.json")
# A fresh clone has no profile yet: until the designer saves one, load() reads this seeded demo profile (see examples/design_file_scan_demo.json).
EXAMPLE_PATH = str(DAVID_HOME / "design_preferences.example.json")
SCHEMA = "design_preferences/v1"
STATUSES = ("unset", "draft", "suggested", "confirmed")
NOT_A_PERSON = {"david", "ai", "model", "assistant", "system", "claude", "qwen", "bot", "agent", "llm", "auto", "automatic", "seed", "scan"}
LOG_MAX = 1000
SECTIONS = ("fonts", "palettes", "document_presets", "export_presets", "dos_donts", "reference_roots")


class Invalid(ValueError):
    pass


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and lo <= v <= hi


def _enum(*vals):
    return lambda v: v in vals


def _text(v):
    return isinstance(v, str) and 0 < len(v.strip()) <= 200


SETTINGS = {
    "arabic.digits": (_enum("western", "eastern", "ask"), "one of western, eastern, ask"),
    "arabic.direction": (_enum("rtl", "ltr", "ask"), "one of rtl, ltr, ask"),
    "arabic.keep_joining": (lambda v: isinstance(v, bool), "true or false"),
    "arabic.kashida": (_enum("never", "ask", "allowed"), "one of never, ask, allowed"),
    "color.default_rgb_profile": (_text, "a profile name"),
    "color.default_cmyk_profile": (_text, "a profile name"),
    "color.default_bit_depth": (_enum(8, 16), "8 or 16"),
    "print.bleed_mm": (lambda v: _num(v, 0, 10), "a number of millimetres from 0 to 10"),
    "print.margin_mm": (lambda v: _num(v, 0, 50), "a number of millimetres from 0 to 50"),
    "print.safe_area_mm": (lambda v: _num(v, 0, 50), "a number of millimetres from 0 to 50"),
    "naming.separator": (_enum("_", "-"), "'_' or '-'"),
    "naming.version_digits": (_enum(2, 3), "2 or 3"),
    "naming.ascii_only": (lambda v: isinstance(v, bool), "true or false"),
    "naming.allow_spaces": (lambda v: isinstance(v, bool), "true or false"),
    "folders.structure": (lambda v: isinstance(v, list) and 1 <= len(v) <= 20 and all(isinstance(x, str) and not design_naming.check_name(x) for x in v) and len(set(v)) == len(v), "a list of up to 20 different folder names that follow the naming rules"),
    "preview.max_side_px": (lambda v: isinstance(v, int) and not isinstance(v, bool) and 256 <= v <= 8192, "a whole number of pixels from 256 to 8192"),
    "logo.clear_space": (_text, "a short description"),
    "logo.min_size_mm": (lambda v: _num(v, 1, 500), "a number of millimetres from 1 to 500"),
    "video.frame_rate": (lambda v: v in (23.976, 24, 25, 29.97, 30, 50, 59.94, 60), "one of 23.976, 24, 25, 29.97, 30, 50, 59.94, 60"),
    "video.default_ratio": (_enum("16:9", "9:16", "1:1", "4:5", "21:9"), "one of 16:9, 9:16, 1:1, 4:5, 21:9"),
    "video.loudness_lufs": (lambda v: _num(v, -30, -5), "a number from -30 to -5"),
    "video.caption_style": (_text, "a short description"),
}

_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,59}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _is_id(v):
    return isinstance(v, str) and bool(_ID.match(v))


def _check_fonts(r):
    p = []
    if not _text(r.get("family")):
        p.append("needs a family name")
    if r.get("script") not in ("latin", "arabic", "both", "unknown"):
        p.append("script must be latin, arabic, both or unknown")
    if r.get("role") not in ("heading", "body", "display", "script", "any"):
        p.append("role must be heading, body, display, script or any")
    if r.get("use") not in ("preferred", "allowed", "avoid"):
        p.append("use must be preferred, allowed or avoid")
    if not isinstance(r.get("seen_in_files", 0), int) or isinstance(r.get("seen_in_files", 0), bool) or r.get("seen_in_files", 0) < 0:
        p.append("seen_in_files must be a whole number")
    if not isinstance(r.get("files", []), list) or not all(isinstance(x, str) and os.path.isabs(x) for x in r.get("files", [])):
        p.append("files must be a list of full paths")
    return p


def _check_palettes(r):
    p = []
    if not _text(r.get("name")):
        p.append("needs a name")
    if r.get("mode") not in ("RGB", "CMYK", "SPOT"):
        p.append("mode must be RGB, CMYK or SPOT")
    cols = r.get("colors")
    if not isinstance(cols, list) or not 1 <= len(cols) <= 64:
        p.append("needs 1 to 64 colours")
    else:
        for c in cols:
            if not isinstance(c, dict) or not _text(c.get("name")):
                p.append("every colour needs a name")
                break
            if r.get("mode") == "CMYK":
                v = c.get("cmyk")
                if not (isinstance(v, list) and len(v) == 4 and all(_num(x, 0, 100) for x in v)):
                    p.append(f"colour '{c.get('name')}' needs cmyk as four numbers from 0 to 100")
                    break
            elif not (isinstance(c.get("hex"), str) and _HEX.match(c["hex"])):
                p.append(f"colour '{c.get('name')}' needs a hex value like #1A2B3C")
                break
    if not isinstance(r.get("forbidden", False), bool):
        p.append("forbidden must be true or false")
    return p


def _check_docs(r):
    p = []
    if not _text(r.get("name")):
        p.append("needs a name")
    if r.get("kind") not in ("print", "social", "video", "web"):
        p.append("kind must be print, social, video or web")
    if r.get("unit") not in ("px", "mm", "in", "pt"):
        p.append("unit must be px, mm, in or pt")
    if not (_num(r.get("width"), 1, 100000) and _num(r.get("height"), 1, 100000)):
        p.append("width and height must be positive numbers")
    if not (_num(r.get("dpi"), 36, 2400)):
        p.append("dpi must be a number from 36 to 2400")
    if r.get("color_mode") not in ("RGB", "CMYK"):
        p.append("color_mode must be RGB or CMYK")
    return p


def _check_exports(r):
    p = []
    if not _text(r.get("name")):
        p.append("needs a name")
    if r.get("format") not in ("PNG", "JPG", "PDF", "SVG", "TIFF", "MP4", "MOV"):
        p.append("format must be PNG, JPG, PDF, SVG, TIFF, MP4 or MOV")
    if "quality" in r and not _num(r["quality"], 1, 100):
        p.append("quality must be a number from 1 to 100")
    if r.get("color_mode") not in ("RGB", "CMYK", "source"):
        p.append("color_mode must be RGB, CMYK or source")
    if "dpi" in r and not _num(r["dpi"], 36, 2400):
        p.append("dpi must be a number from 36 to 2400")
    return p


def _check_dos(r):
    p = []
    if r.get("kind") not in ("do", "dont"):
        p.append("kind must be do or dont")
    if not _text(r.get("text")):
        p.append("needs the rule written as a sentence")
    return p


def _check_roots(r):
    p = []
    if not (isinstance(r.get("path"), str) and os.path.isabs(r["path"])):
        p.append("needs a full path")
    if r.get("read_only") is not True:
        p.append("a reference folder is always read-only (read_only must be true)")
    return p


CHECKERS = {"fonts": _check_fonts, "palettes": _check_palettes, "document_presets": _check_docs,
            "export_presets": _check_exports, "dos_donts": _check_dos, "reference_roots": _check_roots}


def new_profile():
    return {"schema": SCHEMA, "updated": None, "settings": {}, "font_order": {"latin": [], "arabic": []}, "log": [],
            **{s: [] for s in SECTIONS}}


def _entry(value, status, source):
    return {"value": value, "status": status, "source": source}


def validate(profile):
    """Every problem as a sentence; an empty list means the profile is valid."""
    p = []
    if not isinstance(profile, dict) or profile.get("schema") != SCHEMA:
        return [f"This is not a {SCHEMA} profile."]
    for k, e in (profile.get("settings") or {}).items():
        if k not in SETTINGS:
            p.append(f"'{k}' is not a known setting.")
            continue
        if not isinstance(e, dict) or e.get("status") not in STATUSES:
            p.append(f"Setting {k}: the status must be one of {', '.join(STATUSES)}.")
            continue
        if e["status"] == "unset":
            if e.get("value") is not None:
                p.append(f"Setting {k}: an unset setting has no value.")
            continue
        ok, hint = SETTINGS[k]
        if e.get("value") is None or not ok(e["value"]):
            p.append(f"Setting {k}: the value must be {hint}.")
    for s in SECTIONS:
        seen = set()
        for r in profile.get(s) or []:
            rid = r.get("id") if isinstance(r, dict) else None
            if not _is_id(rid):
                p.append(f"{s}: every entry needs an id of lower-case letters, digits, '_', '-' or '.'.")
                continue
            if rid in seen:
                p.append(f"{s}: the id '{rid}' is used twice.")
            seen.add(rid)
            if r.get("status") not in STATUSES or r.get("status") == "unset":
                p.append(f"{s}/{rid}: the status must be draft, suggested or confirmed.")
            for msg in CHECKERS[s](r):
                p.append(f"{s}/{rid}: {msg}.")
    ids = {r.get("id") for r in profile.get("fonts") or [] if isinstance(r, dict)}
    for lang in ("latin", "arabic"):
        order = (profile.get("font_order") or {}).get(lang, [])
        if not isinstance(order, list) or any(i not in ids for i in order) or len(set(order)) != len(order):
            p.append(f"font_order/{lang}: must list each known font id at most once.")
    if not isinstance(profile.get("log"), list):
        p.append("The learning log must be a list.")
    return p


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        bak = path + ".bak"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(path, bak)
    os.replace(tmp, path)


def save(profile, path=PATH):
    probs = validate(profile)
    if probs:
        raise Invalid("The profile was not saved: " + " ".join(probs[:6]) + (f" (and {len(probs) - 6} more)" if len(probs) > 6 else ""))
    profile["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    profile["log"] = profile["log"][-LOG_MAX:]
    _atomic_write(path, profile)


def load(path=None):
    path = path or PATH
    if path == PATH and not os.path.exists(path):
        path = EXAMPLE_PATH
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    probs = validate(profile)
    if probs:
        raise Invalid("The saved profile is not valid: " + " ".join(probs[:6]))
    return profile


def _who(approved_by):
    n = str(approved_by or "").strip()
    if not n or len(n) > 60 or n.lower() in NOT_A_PERSON:
        raise Invalid("Only the designer can confirm or correct a preference: approved_by needs his name.")
    return n


def _log(profile, who, action, section, key, old, new, note):
    profile["log"].append({"at": datetime.datetime.now().isoformat(timespec="seconds"), "by": who, "action": action, "section": section, "id": key,
                           "old": copy.deepcopy(old), "new": copy.deepcopy(new), "note": str(note or "")[:300]})


def _find(profile, section, rid):
    for r in profile[section]:
        if r.get("id") == rid:
            return r
    return None


def setting(profile, key):
    e = (profile.get("settings") or {}).get(key)
    return e["value"] if e and e["status"] != "unset" else None


def is_confirmed(profile, section, key):
    e = profile["settings"].get(key) if section == "settings" else _find(profile, section, key)
    return bool(e) and e.get("status") == "confirmed"


def confirm(profile, section, key, approved_by, note=""):
    who = _who(approved_by)
    e = profile["settings"].get(key) if section == "settings" else _find(profile, section, key) if section in SECTIONS else None
    if e is None:
        raise Invalid(f"There is no {section} entry '{key}' to confirm.")
    if e["status"] == "unset" or (section == "settings" and e.get("value") is None):
        raise Invalid(f"{key} has no value yet; give it a value first.")
    old = e["status"]
    e["status"] = "confirmed"
    _log(profile, who, "confirm", section, key, old, "confirmed", note)
    return profile


def correct(profile, key, value, approved_by, note=""):
    """Set one setting to the designer's value (it becomes confirmed). The new value is checked first; the profile is untouched if it is refused."""
    who = _who(approved_by)
    if key not in SETTINGS:
        raise Invalid(f"'{key}' is not a known setting.")
    ok, hint = SETTINGS[key]
    if value is None or not ok(value):
        raise Invalid(f"Setting {key}: the value must be {hint}.")
    old = copy.deepcopy(profile["settings"].get(key))
    profile["settings"][key] = _entry(value, "confirmed", f"{who}, {datetime.date.today().isoformat()}")
    _log(profile, who, "correct", "settings", key, old and old.get("value"), value, note)
    return profile


def upsert(profile, section, record, approved_by, note=""):
    """Add or replace one table entry as the designer's confirmed answer. The profile is untouched if the entry is refused."""
    who = _who(approved_by)
    if section not in SECTIONS:
        raise Invalid(f"'{section}' is not a section of the profile.")
    r = copy.deepcopy(record)
    r["status"] = "confirmed"
    r["source"] = f"{who}, {datetime.date.today().isoformat()}"
    probs = []
    if not _is_id(r.get("id")):
        probs.append("needs an id of lower-case letters, digits, '_', '-' or '.'")
    probs += CHECKERS[section](r)
    if probs:
        raise Invalid(f"{section}: " + "; ".join(probs) + ".")
    old = _find(profile, section, r["id"])
    if old is not None:
        profile[section][profile[section].index(old)] = r
    else:
        profile[section].append(r)
    _log(profile, who, "upsert", section, r["id"], old, r, note)
    return profile


def remove(profile, section, rid, approved_by, note=""):
    who = _who(approved_by)
    if section not in SECTIONS:
        raise Invalid(f"'{section}' is not a section of the profile.")
    old = _find(profile, section, rid)
    if old is None:
        raise Invalid(f"There is no {section} entry '{rid}' to remove.")
    profile[section].remove(old)
    if section == "fonts":
        for lang in ("latin", "arabic"):
            profile["font_order"][lang] = [i for i in profile["font_order"][lang] if i != rid]
    _log(profile, who, "remove", section, rid, old, None, note)
    return profile


def drafts(profile):
    """What still waits for the designer: (section, id, status) for every entry that is not confirmed and has a value."""
    out = [("settings", k, e["status"]) for k, e in profile["settings"].items() if e["status"] in ("draft", "suggested")]
    for s in SECTIONS:
        out += [(s, r["id"], r["status"]) for r in profile[s] if r["status"] != "confirmed"]
    return out


def summary(profile):
    return {"confirmed": sum(1 for e in profile["settings"].values() if e["status"] == "confirmed") + sum(1 for s in SECTIONS for r in profile[s] if r["status"] == "confirmed"),
            "waiting": len(drafts(profile)), "unset": sum(1 for e in profile["settings"].values() if e["status"] == "unset")}


# ---- seeding from the metadata scan (never confirmed) ----

def _counts(lines):
    out = []
    for s in lines or []:
        name, _, n = str(s).rpartition(":")
        if name and n.strip().isdigit():
            out.append((name.strip(), int(n)))
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _font_id(family):
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")[:50] or "font"


def seed_profile(scan, inventory=None, min_files=3, max_fonts=30):
    pr = new_profile()
    src = "file scan 2026-10-10"
    by_fam = {}
    for fam, n in _counts(scan.get(".ai family")):
        by_fam[fam] = by_fam.get(fam, 0) + n
    inv_by_norm = {}
    for f in (inventory or {}).get("fonts", []):
        inv_by_norm.setdefault(_norm(f.get("family")), []).append(f)
        inv_by_norm.setdefault(_norm(f.get("ps")), []).append(f)
    used = set()
    for fam, n in sorted(by_fam.items(), key=lambda kv: -kv[1]):
        if n < min_files or len(pr["fonts"]) >= max_fonts or "+" in fam:
            continue
        rid = _font_id(fam)
        if rid in used:
            continue
        used.add(rid)
        found = inv_by_norm.get(_norm(fam), [])
        script = "unknown"
        if found:
            script = "arabic" if any(f.get("arabic") for f in found) else "latin"
        pr["fonts"].append({"id": rid, "family": fam, "script": script, "role": "any", "use": "allowed", "seen_in_files": n,
                            "files": sorted({os.path.normpath(f["path"]) for f in found})[:5], "status": "draft", "source": f"{src}: used in {n} Illustrator files"})
    docs = [("a4-landscape-300", "A4 landscape 300 dpi", "print", 3508, 2480, 300, "RGB"), ("a4-portrait-300", "A4 portrait 300 dpi", "print", 2480, 3508, 300, "RGB"),
            ("square-1080", "Square 1080", "social", 1080, 1080, 72, "RGB"), ("story-1080x1920", "Story / reel 9:16", "social", 1080, 1920, 72, "RGB"),
            ("portrait-1080x1350", "Portrait 4:5", "social", 1080, 1350, 72, "RGB"), ("hd-1920x1080", "Full HD 16:9", "video", 1920, 1080, 72, "RGB"),
            ("uhd-3840x2160", "4K UHD 16:9", "video", 3840, 2160, 72, "RGB"), ("portrait-900x1600", "Portrait 9:16 900x1600", "social", 900, 1600, 72, "RGB"),
            ("wide-3556x2000", "Wide 16:9 3556x2000", "web", 3556, 2000, 72, "RGB")]
    seen = {tuple(int(x) for x in re.findall(r"\d+", l.split(":")[0])[:2]): int(l.rsplit(":", 1)[1]) for l in scan.get("psd_top_sizes", [])}
    for rid, name, kind, w, h, dpi, mode in docs:
        n = seen.get((w, h))
        pr["document_presets"].append({"id": rid, "name": name, "kind": kind, "unit": "px", "width": w, "height": h, "dpi": dpi, "color_mode": mode,
                                       "status": "draft" if n else "suggested", "source": f"{src}: {n} Photoshop files at this size" if n else "industry standard size"})
    exports = [("web-png", "Web PNG (sRGB)", "PNG", None, "RGB"), ("web-jpg-q85", "Web JPG quality 85", "JPG", 85, "RGB"), ("social-jpg-q90", "Social JPG quality 90", "JPG", 90, "RGB"),
               ("print-pdf-x4", "Print PDF/X-4", "PDF", None, "CMYK"), ("print-tiff-300", "Print TIFF 300 dpi", "TIFF", None, "CMYK"),
               ("vector-svg", "Vector SVG", "SVG", None, "RGB"), ("video-h264", "Video MP4 (H.264)", "MP4", None, "source")]
    for rid, name, fmt, q, mode in exports:
        r = {"id": rid, "name": name, "format": fmt, "color_mode": mode, "status": "suggested", "source": "industry standard preset"}
        if q:
            r["quality"] = q
        if fmt in ("PDF", "TIFF"):
            r["dpi"] = 300
        pr["export_presets"].append(r)
    S = pr["settings"]
    S["arabic.keep_joining"] = _entry(True, "suggested", "Arabic letters must never be broken apart")
    S["arabic.direction"] = _entry("rtl", "suggested", "Arabic text runs right to left")
    S["arabic.digits"] = _entry("ask", "suggested", "not known; David asks each time until you choose")
    S["arabic.kashida"] = _entry("ask", "suggested", "not known; David asks each time until you choose")
    S["color.default_rgb_profile"] = _entry("sRGB IEC61966-2.1", "draft", f"{src}: 38 of 53 profiled Photoshop files")
    S["color.default_cmyk_profile"] = _entry("U.S. Web Coated (SWOP) v2", "draft", f"{src}: only 3 Photoshop files; confirm with your printer")
    S["color.default_bit_depth"] = _entry(8, "draft", f"{src}: all 69 Photoshop files are 8-bit")
    S["print.bleed_mm"] = _entry(3, "suggested", "industry standard bleed")
    S["print.margin_mm"] = _entry(10, "suggested", "common safe margin")
    S["print.safe_area_mm"] = _entry(5, "suggested", "common safe area")
    S["naming.separator"] = _entry("_", "suggested", "industry standard")
    S["naming.version_digits"] = _entry(2, "suggested", "industry standard (_v01)")
    S["naming.ascii_only"] = _entry(True, "suggested", "industry standard")
    S["naming.allow_spaces"] = _entry(False, "suggested", "industry standard")
    S["folders.structure"] = _entry(list(design_naming.FOLDERS), "suggested", "industry standard project skeleton")
    S["preview.max_side_px"] = _entry(1600, "suggested", "size of a proof image")
    for k in ("logo.clear_space", "logo.min_size_mm", "video.frame_rate", "video.default_ratio", "video.loudness_lufs", "video.caption_style"):
        S[k] = _entry(None, "unset", "ask the designer")
    pr["reference_roots"] = [
        {"id": "reference-designs", "path": str(DAVID_DATA / "reference_designs"), "read_only": True, "note": "a folder of past work used as a style reference (read-only)",
         "status": "suggested", "source": src}]
    return pr


def main(argv):
    if len(argv) >= 2 and argv[0] == "seed":
        scan = json.load(open(argv[1], encoding="utf-8"))
        inv = json.load(open(argv[2], encoding="utf-8")) if len(argv) >= 3 and not argv[2].startswith("--") else None
        if os.path.exists(PATH) and "--force" not in argv:
            print(f"{PATH} already exists and may hold your answers; add --force to replace it.")
            return 1
        pr = seed_profile(scan, inv)
        save(pr)
        print(f"Seeded {len(pr['fonts'])} fonts, {len(pr['document_presets'])} document sizes, {len(pr['export_presets'])} export presets, {len(pr['settings'])} settings; nothing is confirmed yet.")
        return 0
    if argv[:1] == ["check"]:
        probs = validate(load())
        print("valid" if not probs else "\n".join(probs))
        return 0 if not probs else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
