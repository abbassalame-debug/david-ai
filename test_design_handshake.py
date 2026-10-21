"""
test_design_handshake.py -- offline tests for design_handshake.py: the picture-reading of the Arabic test line, the fixed scripts, the pinned InDesign connection.
No Adobe app is opened.   Run: python test_design_handshake.py
"""
from david_config import DAVID_HOME
import sys, os, re, json, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image, ImageDraw, ImageFont
import design_handshake as H

TMP = tempfile.mkdtemp(prefix="hs_test_")


def synth(boxes, name):
    im = Image.new("L", (1400, 400), 255)
    d = ImageDraw.Draw(im)
    for b in boxes:
        d.rectangle(b, fill=0)
    p = os.path.join(TMP, name)
    im.save(p)
    return p


print("-- reading the Arabic picture back --")
# joined right-to-left: a long word at the left (one big joined body + a thin alef), a short word (one ligature) at the right, dots above and below
joined_rtl = synth([(100, 150, 500, 240), (520, 110, 535, 240), (180, 100, 190, 110), (300, 250, 310, 260), (640, 130, 750, 240), (700, 100, 710, 110)], "joined_rtl.png")
a = H.analyze_arabic(joined_rtl)
check("a joined line whose short word is at the right is read as joined, right to left, correct Arabic", a["ok"] and a["joining"] == "joined" and a["direction"] == "rtl" and a["arabic_ok"] is True and a["body_parts"] == 3, str(a))
mirror = synth([(1400 - x1, y0, 1400 - x0, y1) for (x0, y0, x1, y1) in [(100, 150, 500, 240), (520, 110, 535, 240), (180, 100, 190, 110), (300, 250, 310, 260), (640, 130, 750, 240), (700, 100, 710, 110)]], "joined_ltr.png")
m = H.analyze_arabic(mirror)
check("the mirror image (letters join but the order runs left to right) is flagged as the wrong direction", m["joining"] == "joined" and m["direction"] == "ltr" and m["arabic_ok"] is False and "direction is wrong" in m["reading"], str(m))
disjoint = synth([(60 + i * 90, 120, 100 + i * 90, 240) for i in range(9)], "disjoint.png")
dj = H.analyze_arabic(disjoint)
check("nine separate letters are read as 'disjoint': an engine without Arabic shaping", dj["joining"] == "disjoint" and dj["arabic_ok"] is False and dj["body_parts"] == 9 and "apart" in dj["reading"], str(dj))
# Any installed font with Arabic glyphs will do (the hosted CI runner has no Sakkal Majalla; found 2026-10-21).
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_arabic_font = next((os.path.join(_fonts_dir, f) for f in ("majalla.ttf", "arial.ttf", "tahoma.ttf", "segoeui.ttf")
                     if os.path.isfile(os.path.join(_fonts_dir, f))), None)
_label = "a real font drawn WITHOUT shaping (Pillow's basic layout, the behaviour of a Roman text engine) is read as disjoint and not correct"
if _arabic_font is None:
    import ci_env
    ci_env.skip(_label, "an installed font with Arabic glyphs")
else:
    im = Image.new("L", (1400, 400), 255)
    ImageDraw.Draw(im).text((60, 120), H.ARABIC, font=ImageFont.truetype(_arabic_font, 140), fill=0)
    p = os.path.join(TMP, "pillow_unshaped.png")
    im.save(p)
    pu = H.analyze_arabic(p)
    check(_label, pu["joining"] == "disjoint" and pu["arabic_ok"] is False, str(pu))
blank = os.path.join(TMP, "blank.png")
Image.new("L", (400, 200), 255).save(blank)
check("a blank picture is reported as blank, not as a result", H.analyze_arabic(blank) == {"ok": False, "reason": "the picture is blank"})

print("\n-- the fixed scripts --")
d = os.path.join(TMP, "run dir")
os.makedirs(d)
open(os.path.join(d, "sample_copy.ai"), "w").write("x")
for app in ("photoshop", "illustrator", "indesign", "aftereffects"):
    j = H.build_jsx(app, d)
    check(f"the {app} script has every placeholder filled, only ASCII characters (the Arabic is escaped, so no encoding can damage it), forward-slash paths and the Arabic line as escapes",
          not re.search(r"__[A-Z0-9]+__", j) and j.isascii() and "\\u0644\\u0627" in j and "run dir/" in j and "\\\\" not in j.split("var OUT")[1].split("\n")[0], app)
check("none of the scripts saves a designer's document or quits without being asked: scratch documents are closed with 'do not save'", all(("DONOTSAVECHANGES" in H.build_jsx(a, d)) for a in ("photoshop", "illustrator")) and "SaveOptions.NO" in H.build_jsx("indesign", d) and "DO_NOT_SAVE_CHANGES" in H.build_jsx("aftereffects", d))
check("After Effects is told to quit only when we started it", "if(true){app.quit();}" in H.build_jsx("aftereffects", d) and "if(false){app.quit();}" in H.build_jsx("aftereffects", d, quit_after=False))
check("the Illustrator script opens the sample COPY inside the run folder, never the original path", "run dir/sample_copy.ai" in H.build_jsx("illustrator", d))
check("the Arabic test line is a short first word followed by a long one, and the font is one that has Arabic (Sakkal Majalla)", H.ARABIC.split(" ")[0] == "\u0644\u0627" and len(H.ARABIC.split(" ")[1]) > 5 and H.ARABIC_FONT_PS == "SakkalMajalla")

print("\n-- connections --")
check("InDesign is pinned to the stable build's own CLSID, never to a ProgID (the ProgIDs belong to the beta installs)", H.COM_IDS["indesign"] == ["{45112D7C-822C-400A-BA8C-D682F7FD0B8E}"] and all(not re.search(r"InDesign\.Application", x) for x in H.COM_IDS["indesign"]))
ab = open(str(DAVID_HOME / "adobe_bridge.py"), encoding="utf-8").read()
check("adobe_bridge uses the same pin for InDesign and no longer lists a ProgID that opens a beta", "45112D7C-822C-400A-BA8C-D682F7FD0B8E" in ab and '"InDesign.Application.2026"' not in ab and '"InDesign.Application"]' not in ab)
check("every program path in the handshake is the stable 2026 install and exists on this computer", all(os.path.isfile(p) for p in H.EXES.values()) and "Beta" not in "".join(H.EXES.values()))
check("the connected program is checked: a connection that opens a different program than the stable one stops the run", "opened a different program" in open(str(DAVID_HOME / "design_handshake.py"), encoding="utf-8").read())

print("\n-- what is reported --")
good = {"ok": True, "connect_seconds": 9.9, "originals_unchanged": True, "script_result": {"steps": {"scratch_save_reopen": {"w": 800}, "sample_open_copy": {"name": "x"}}}, "arabic": {"arabic:png": {"arabic_ok": True, "reading": "letters join and the short first word sits at the right: correct Arabic"}}}
s = H.summarize_report("photoshop", good)
check("the Photoshop summary says it connected, saved and reopened a scratch file, opened a copy, and that Arabic is correct", "Connected through Windows COM (9.9 s)" in s and "saved, reopened" in s and "copy of a real file" in s and "Arabic (plain text): correct" in s, s)
bad = {"ok": True, "connect_seconds": 0.0, "originals_unchanged": True, "script_result": {"steps": {}}, "arabic": {"arabic:png_default": {"arabic_ok": False, "reading": "every letter is drawn apart (no Arabic shaping in this engine)"}, "arabic:png_with_rtl_attributes": {"arabic_ok": True, "reading": "ok"}}}
s2 = H.summarize_report("indesign", bad)
check("an engine that fails is said to fail: 'NOT correct' for the default settings, 'correct' with the right-to-left settings", "default text settings): NOT correct" in s2 and "right-to-left settings): correct" in s2, s2)
check("a failed app reports why; a changed original is shouted", H.summarize_report("indesign", {"ok": False, "error": "boom"}) == "Did not work: boom" and "WARNING: an original changed" in H.summarize_report("photoshop", {**good, "originals_unchanged": False}))
check("Premiere without the panel is said plainly: launched, panel not running, must be opened from the Window menu", "launched" in H.summarize_report("premiere", {"ok": True, "launched_process": True, "panel_loaded": False}) and "not running" in H.summarize_report("premiere", {"ok": True, "launched_process": True, "panel_loaded": False}))
out = H.save_results({"photoshop": good}, os.path.join(TMP, "hs.json"))
check("results are saved with the time, every app's report and one sentence each", json.load(open(os.path.join(TMP, "hs.json"), encoding="utf-8"))["summary"]["photoshop"] == out["summary"]["photoshop"] and out["at"])

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
