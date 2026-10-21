"""
test_design_preferences.py -- tests for design_preferences.py: the designer's preferences profile (validation, edit/save round trip, seeding, confirm/correct).
Offline; uses temporary files and the saved scan.   Run: python test_design_preferences.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, copy, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import design_preferences as P

TMP = tempfile.mkdtemp(prefix="dp_test_")
SCAN = json.load(open(str(DAVID_HOME / "examples" / "design_file_scan_demo.json"), encoding="utf-8"))
INV = {"fonts": [{"path": r"C:\Windows\Fonts\majalla.ttf", "family": "Sakkal Majalla", "ps": "SakkalMajalla", "arabic": True},
                 {"path": r"C:\Windows\Fonts\Montserrat-VF.ttf", "family": "Montserrat", "ps": "Montserrat-Regular", "arabic": False}]}
seed = P.seed_profile(SCAN, INV)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except P.Invalid as e:
        return str(e)


print("-- the seed from the metadata scan --")
check("the seeded profile is valid", P.validate(seed) == [], str(P.validate(seed)[:3]))
sts = {e["status"] for e in seed["settings"].values()} | {r["status"] for s in P.SECTIONS for r in seed[s]}
check("nothing in the seed is confirmed: only draft, suggested or unset (the scan and industry defaults never speak for the designer)", sts <= {"draft", "suggested", "unset"} and "confirmed" not in sts, str(sts))
check("fonts come from his Illustrator files, most used first, each with how many files used it", seed["fonts"][0]["family"] == "Myriad Pro" and seed["fonts"][0]["seen_in_files"] == 19 and [f["seen_in_files"] for f in seed["fonts"]] == sorted((f["seen_in_files"] for f in seed["fonts"]), reverse=True))
check("his Arabic fonts are in the draft (Athiya, Britannic Bold, Sakkal Majalla, Adobe Arabic, AbdoLine)", {"Athiya", "Britannic Bold", "Sakkal Majalla", "Adobe Arabic", "AbdoLine"} <= {f["family"] for f in seed["fonts"]})
sm = [f for f in seed["fonts"] if f["family"] == "Sakkal Majalla"][0]
check("a font found in the inventory gets its script and file, one not found stays 'unknown' with no file", sm["script"] == "arabic" and sm["files"] == [os.path.normpath(r"C:\Windows\Fonts\majalla.ttf")] and [f for f in seed["fonts"] if f["family"] == "Athiya"][0]["script"] == "unknown" and [f for f in seed["fonts"] if f["family"] == "Athiya"][0]["files"] == [])
ds = {r["id"]: r for r in seed["document_presets"]}
check("document sizes seen in his Photoshop files are 'draft' with the count, industry sizes are 'suggested' (A4 landscape 3508x2480 seen in 14 files)", ds["a4-landscape-300"]["status"] == "draft" and "14 Photoshop files" in ds["a4-landscape-300"]["source"] and ds["uhd-3840x2160"]["status"] == "suggested" and (ds["a4-landscape-300"]["width"], ds["a4-landscape-300"]["height"]) == (3508, 2480))
check("colour defaults are read from his files: sRGB, 8-bit, SWOP marked as weak evidence", seed["settings"]["color.default_rgb_profile"]["value"] == "sRGB IEC61966-2.1" and seed["settings"]["color.default_bit_depth"]["value"] == 8 and "only 3" in seed["settings"]["color.default_cmyk_profile"]["source"])
check("the naming defaults are the industry standard and the folder skeleton is the numbered one", seed["settings"]["naming.allow_spaces"]["value"] is False and seed["settings"]["naming.ascii_only"]["value"] is True and seed["settings"]["folders.structure"]["value"][0] == "01_Brief")
check("what the scan cannot know stays unset: logo, video frame rate, ratio, loudness and caption style", all(seed["settings"][k]["status"] == "unset" and seed["settings"][k]["value"] is None for k in ("logo.clear_space", "logo.min_size_mm", "video.frame_rate", "video.default_ratio", "video.loudness_lufs", "video.caption_style")))
check("Arabic digits and kashida are 'ask' (not guessed)", seed["settings"]["arabic.digits"]["value"] == "ask" and seed["settings"]["arabic.kashida"]["value"] == "ask")
check("the reference folders are read-only", all(r["read_only"] is True for r in seed["reference_roots"]) and len(seed["reference_roots"]) == 1)
check("the summary counts what waits for him", P.summary(seed)["confirmed"] == 0 and P.summary(seed)["unset"] == 6 and P.summary(seed)["waiting"] == len(P.drafts(seed)) > 40)

print("\n-- save and load: the edit/save round trip --")
path = os.path.join(TMP, "prefs.json")
p = copy.deepcopy(seed)
P.save(p, path)
back = P.load(path)
check("save then load gives the same profile (the saved time is the only addition)", {k: v for k, v in back.items() if k != "updated"} == {k: v for k, v in seed.items() if k != "updated"} and back["updated"])
P.correct(p, "print.bleed_mm", 5, "Demo User", "my printer wants 5")
P.save(p, path)
check("a second save keeps the previous file as .bak and leaves no .tmp behind", os.path.exists(path + ".bak") and not os.path.exists(path + ".tmp") and P.load(path + ".bak")["settings"]["print.bleed_mm"]["value"] == 3)
check("the edit survives a reload and carries his name", P.load(path)["settings"]["print.bleed_mm"]["value"] == 5 and P.load(path)["settings"]["print.bleed_mm"]["status"] == "confirmed" and "Demo User" in P.load(path)["settings"]["print.bleed_mm"]["source"])

print("\n-- a bad value is refused with a sentence and changes nothing --")
before = copy.deepcopy(p)
bads = [("print.bleed_mm", 11), ("print.bleed_mm", -1), ("print.bleed_mm", True), ("print.bleed_mm", float("nan")), ("print.bleed_mm", "3"), ("video.frame_rate", 26), ("color.default_bit_depth", 12),
        ("arabic.digits", "arabic"), ("arabic.keep_joining", "yes"), ("naming.separator", " "), ("preview.max_side_px", 100), ("folders.structure", ["01 Brief"]), ("folders.structure", ["A", "A"]),
        ("video.default_ratio", "3:2"), ("print.margin_mm", None), ("nope.setting", 1)]
msgs = [refused(P.correct, p, k, v, "Demo User") for k, v in bads]
check("all 16 bad settings are refused", all(msgs), str([b for b, m in zip(bads, msgs) if not m]))
check("the refusal names the setting and says what is allowed", "print.bleed_mm" in msgs[0] and "0 to 10" in msgs[0] and "one of" in msgs[5] and "not a known setting" in msgs[15])
check("after all those refusals the profile is exactly as it was", p == before)
check("good values are accepted: 25 fps, 9:16, -23 LUFS, bleed 0", all(refused(P.correct, p, k, v, "Demo User") is None for k, v in (("video.frame_rate", 25), ("video.default_ratio", "9:16"), ("video.loudness_lufs", -23), ("print.bleed_mm", 0))) and p["settings"]["video.frame_rate"]["value"] == 25)

print("\n-- only the designer's own answers teach --")
q = copy.deepcopy(seed)
check("a missing name, 'David', 'AI', 'Claude' or 'scan' cannot confirm or correct", all(refused(P.confirm, q, "settings", "naming.separator", n) and refused(P.correct, q, "print.bleed_mm", 4, n) for n in ("", "  ", "David", "AI", "Claude", "scan", None)))
check("and the profile is still unconfirmed after those attempts", P.summary(q)["confirmed"] == 0 and q["log"] == [])
P.confirm(q, "settings", "naming.separator", "Demo User")
P.confirm(q, "fonts", "sakkal-majalla", "Demo User", "yes, my Arabic body font")
check("his confirm makes the entry confirmed and logs who, what and when", q["settings"]["naming.separator"]["status"] == "confirmed" and P.is_confirmed(q, "fonts", "sakkal-majalla") and q["log"][-1]["by"] == "Demo User" and q["log"][-1]["action"] == "confirm" and q["log"][-1]["note"] == "yes, my Arabic body font" and q["log"][-1]["at"])
check("a setting with no value cannot be confirmed; neither can an entry that does not exist", refused(P.confirm, q, "settings", "video.frame_rate", "Demo User") is not None and refused(P.confirm, q, "fonts", "no-such-font", "Demo User") is not None and refused(P.confirm, q, "settings", "nope", "Demo User") is not None)
check("the summary moves: 2 confirmed, 2 fewer waiting", P.summary(q)["confirmed"] == 2 and P.summary(q)["waiting"] == P.summary(seed)["waiting"] - 2)
P.correct(q, "color.default_cmyk_profile", "Coated FOGRA39", "Demo User", "my printer uses FOGRA39")
check("a correction replaces the value, keeps the old one in the log and is confirmed", q["settings"]["color.default_cmyk_profile"]["value"] == "Coated FOGRA39" and q["log"][-1]["old"] == "U.S. Web Coated (SWOP) v2" and q["log"][-1]["new"] == "Coated FOGRA39")
check("setting() gives the value, or None for an unset one", P.setting(q, "color.default_cmyk_profile") == "Coated FOGRA39" and P.setting(q, "video.frame_rate") is None)

print("\n-- tables: fonts, palettes, sizes, exports, rules, reference folders --")
r = copy.deepcopy(seed)
font = {"id": "athiya", "family": "Athiya", "script": "arabic", "role": "heading", "use": "preferred"}
P.upsert(r, "fonts", font, "Demo User", "found the folder")
check("upserting a font as the designer replaces the draft with a confirmed entry (no duplicate)", [f["id"] for f in r["fonts"]].count("athiya") == 1 and P.is_confirmed(r, "fonts", "athiya") and [f for f in r["fonts"] if f["id"] == "athiya"][0]["role"] == "heading")
before = copy.deepcopy(r)
ref = [refused(P.upsert, r, "fonts", {**font, "script": "klingon"}, "Demo User"), refused(P.upsert, r, "fonts", {**font, "id": "Bad Id"}, "Demo User"), refused(P.upsert, r, "fonts", {**font, "use": "love"}, "Demo User"),
       refused(P.upsert, r, "palettes", {"id": "p1", "name": "Brand", "mode": "CMYK", "colors": [{"name": "Red", "cmyk": [0, 100, 100, 150]}]}, "Demo User"),
       refused(P.upsert, r, "palettes", {"id": "p1", "name": "Brand", "mode": "RGB", "colors": [{"name": "Red", "hex": "red"}]}, "Demo User"),
       refused(P.upsert, r, "palettes", {"id": "p1", "name": "Brand", "mode": "RGB", "colors": []}, "Demo User"),
       refused(P.upsert, r, "document_presets", {"id": "d1", "name": "X", "kind": "print", "unit": "cm", "width": 10, "height": 10, "dpi": 300, "color_mode": "RGB"}, "Demo User"),
       refused(P.upsert, r, "document_presets", {"id": "d1", "name": "X", "kind": "print", "unit": "mm", "width": 0, "height": 10, "dpi": 300, "color_mode": "RGB"}, "Demo User"),
       refused(P.upsert, r, "export_presets", {"id": "e1", "name": "X", "format": "BMP", "color_mode": "RGB"}, "Demo User"),
       refused(P.upsert, r, "export_presets", {"id": "e1", "name": "X", "format": "JPG", "color_mode": "RGB", "quality": 101}, "Demo User"),
       refused(P.upsert, r, "dos_donts", {"id": "r1", "kind": "maybe", "text": "x"}, "Demo User"),
       refused(P.upsert, r, "reference_roots", {"id": "ref1", "path": r"/data/art", "read_only": False}, "Demo User"),
       refused(P.upsert, r, "reference_roots", {"id": "ref1", "path": "art", "read_only": True}, "Demo User"),
       refused(P.upsert, r, "nope", {"id": "x"}, "Demo User")]
check("14 bad table entries are all refused, each with a sentence", all(ref), str([i for i, m in enumerate(ref) if not m]))
check("and the profile is untouched by them", r == before)
P.upsert(r, "palettes", {"id": "brand-main", "name": "Brand main", "mode": "CMYK", "colors": [{"name": "Red", "cmyk": [0, 100, 100, 0]}, {"name": "Black", "cmyk": [0, 0, 0, 100]}]}, "Demo User")
P.upsert(r, "palettes", {"id": "web", "name": "Web", "mode": "RGB", "colors": [{"name": "Teal", "hex": "#0E7C7B"}], "forbidden": False}, "Demo User")
P.upsert(r, "dos_donts", {"id": "no-stretch", "kind": "dont", "text": "Never stretch a logo."}, "Demo User")
P.upsert(r, "reference_roots", {"id": "mine", "path": r"/data/art", "read_only": True}, "Demo User")
check("good palettes (CMYK and RGB), a rule and a reference folder are accepted and confirmed", P.validate(r) == [] and P.is_confirmed(r, "palettes", "brand-main") and P.is_confirmed(r, "dos_donts", "no-stretch") and P.is_confirmed(r, "reference_roots", "mine"))
r["font_order"]["arabic"] = ["athiya", "sakkal-majalla"]
check("the Arabic fallback order may use only known fonts, each once", P.validate(r) == [] and (lambda x: x.update({"font_order": {"latin": [], "arabic": ["athiya", "athiya"]}}) or P.validate(x))(copy.deepcopy(r)) != [] and (lambda x: x.update({"font_order": {"latin": ["ghost"], "arabic": []}}) or P.validate(x))(copy.deepcopy(r)) != [])
P.remove(r, "fonts", "athiya", "Demo User", "wrong font")
check("removing a font also removes it from the fallback order, and the removal is logged", "athiya" not in [f["id"] for f in r["fonts"]] and r["font_order"]["arabic"] == ["sakkal-majalla"] and r["log"][-1]["action"] == "remove" and P.validate(r) == [])
check("removing something that is not there, or as a non-person, is refused", refused(P.remove, r, "fonts", "athiya", "Demo User") is not None and refused(P.remove, r, "fonts", "myriad-pro", "David") is not None)

print("\n-- validation of a whole file --")
def broken(mut):
    x = copy.deepcopy(seed)
    mut(x)
    return P.validate(x)
check("a wrong schema is reported", P.validate({"schema": "other"}) != [] and P.validate([]) != [] and P.validate(None) != [])
check("an unknown setting, a bad status and an unset setting that has a value are each reported", broken(lambda x: x["settings"].update({"made.up": {"value": 1, "status": "draft", "source": ""}})) != []
      and broken(lambda x: x["settings"]["print.bleed_mm"].update({"status": "maybe"})) != [] and broken(lambda x: x["settings"]["video.frame_rate"].update({"value": 25})) != [])
check("a duplicate id, a missing id and a table entry with status 'unset' are each reported", broken(lambda x: x["fonts"].append(copy.deepcopy(x["fonts"][0]))) != [] and broken(lambda x: x["fonts"][0].pop("id")) != [] and broken(lambda x: x["fonts"][0].update({"status": "unset"})) != [])
check("a stored value that is out of range is reported with the setting's name", any("print.bleed_mm" in s for s in broken(lambda x: x["settings"]["print.bleed_mm"].update({"value": 99}))))
before_file = open(path, "rb").read()
bad_profile = copy.deepcopy(p)
bad_profile["settings"]["print.bleed_mm"]["value"] = 99
m = refused(P.save, bad_profile, path)
check("save refuses an invalid profile with the first problems as sentences and leaves the file on disk exactly as it was", m is not None and "print.bleed_mm" in m and open(path, "rb").read() == before_file and not os.path.exists(path + ".tmp"))
open(os.path.join(TMP, "broken.json"), "w", encoding="utf-8").write(json.dumps({**seed, "settings": {"print.bleed_mm": {"value": 99, "status": "draft", "source": ""}}}))
check("load refuses a saved file that is not valid", refused(P.load, os.path.join(TMP, "broken.json")) is not None)
long_log = copy.deepcopy(seed)
long_log["log"] = [{"at": "x", "by": "Demo User", "action": "confirm", "section": "settings", "id": "k", "old": None, "new": None, "note": ""}] * (P.LOG_MAX + 50)
P.save(long_log, os.path.join(TMP, "long.json"))
check("the learning log is trimmed to the last 1000 entries on save", len(P.load(os.path.join(TMP, "long.json"))["log"]) == P.LOG_MAX)

print("\n-- the real saved profile --")
real = P.load()
check("the saved (or, in a fresh clone, the example) design_preferences.json is valid", P.validate(real) == [])
check("every entry marked confirmed in it names a person as its source, never the scan or an industry default", all(not str(e.get("source", "")).startswith(("file scan", "industry", "not known", "Arabic", "common", "size of")) for e in list(real["settings"].values()) + [r for s in P.SECTIONS for r in real[s]] if e.get("status") == "confirmed"))
check("it holds the fonts, sizes, presets and settings seeded from the scan", len(real["fonts"]) >= 10 and len(real["document_presets"]) == 9 and len(real["export_presets"]) == 7 and len(real["settings"]) == 22)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
