"""
test_adobe_tools.py -- offline tests for adobe_template_tools.py (Job 2: fill existing Illustrator templates, preview first),
its wiring (tools.py, agent_spec exclusive owners, tool_executor), the removed Adobe keyword shortcut, and the Job 2 role files.

No Adobe application is started: a fake runner stands in for Illustrator. The generated JSX itself is executed in Node against a
mock of Illustrator's document model, to prove hostile text cannot break out of the script and the original is never opened.
(The real application was exercised by hand on 2026-10-05; see agents/jobs/02_brand_marketing/JOB.md.)

Run: py -3.11 test_adobe_tools.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, re, json, copy, time, shutil, asyncio, tempfile, subprocess, inspect, hashlib, datetime
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import david_router  # noqa: F401  (configures agent_spec)
import adobe_template_tools as T
import agent_spec as A
import tools as TL
import tool_executor as TE
from PIL import Image


def src(p):
    return open(os.path.join(str(DAVID_HOME), p), encoding="utf-8").read()


REAL_REGISTRY = T.REGISTRY_PATH
TMP = tempfile.mkdtemp(prefix="adobe_test_")
GEN = os.path.join(TMP, "generated", "brand")
TROOT = os.path.join(TMP, "templates")
os.makedirs(os.path.join(TROOT, "شهادات"))
T.GENERATED_ROOT = GEN
SRC_AR, SRC_EN = os.path.join("شهادات", "ar_template.ai"), os.path.join("شهادات", "en_template.ai")
for rel in (SRC_AR, SRC_EN):
    open(os.path.join(TROOT, rel), "wb").write(os.urandom(4096))


def make_registry():
    def entry(rel, lang, anchor, script, font, case="keep", maxc=30):
        return {"title": f"test {lang}", "kind": "certificate", "app": "illustrator", "language": lang, "source": rel,
                "sha256": T.sha256_file(os.path.join(TROOT, rel)),
                "fields": {"recipient_name": {"label": "Recipient name", "anchor_text": anchor, "script": script, "font": font,
                                              "max_chars": maxc, "lines": 1, "digits": "keep", "case": case}}}
    reg = {"template_root": TROOT, "templates": {"t_ar": entry(SRC_AR, "ar", "الاسم القديم", "arabic", "Gulzar-Regular", maxc=40),
                                                 "t_en": entry(SRC_EN, "en", "OLD NAME", "latin", "BritannicBold", case="upper")},
           "not_ready": [{"what": "Brochures", "why": "no field map"}]}
    p = os.path.join(TMP, "templates.json")
    json.dump(reg, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


T.REGISTRY_PATH = make_registry()
ORIG_HASH = {rel: hashlib.sha256(open(os.path.join(TROOT, rel), "rb").read()).hexdigest() for rel in (SRC_AR, SRC_EN)}


def originals_intact():
    return all(hashlib.sha256(open(os.path.join(TROOT, r), "rb").read()).hexdigest() == h for r, h in ORIG_HASH.items())


# ── a fake Illustrator ───────────────────────────────────────────────────────
BEHAVE = {"mode": "ok"}
JSX_SEEN = []


def fake_runner(jsx, run_dir):
    JSX_SEEN.append(jsx)
    mode = BEHAVE["mode"]
    if mode == "raise":
        raise RuntimeError("COM exploded")
    if mode == "hang":
        time.sleep(3)
        return "{}"
    if "var FIELDS" not in jsx:                                       # an export-only job
        png = json.loads(re.search(r'var PNG = (".*?");', jsx).group(1))
        if mode != "no_png":
            Image.new("RGB", (60, 40), (200, 180, 90)).save(png)
        return json.dumps({"ok": True, "error": "", "preview": {"w": 842, "h": 595, "scale": 190}})
    png = json.loads(re.search(r'var PNG = (".*?");', jsx).group(1))
    work = json.loads(re.search(r'var WORK = (".*?");', jsx).group(1))
    fields = json.loads(re.search(r"var FIELDS = (.*?);\r?\nvar MAXSIDE", jsx, re.S).group(1))
    if mode == "modify_original":
        for r in ORIG_HASH:
            open(os.path.join(TROOT, r), "ab").write(b"X")
    out = []
    for f in fields:
        if mode == "nomatch":
            out.append({"name": f["name"], "status": "anchor_matches_0"}); continue
        if mode == "two":
            out.append({"name": f["name"], "status": "anchor_matches_2"}); continue
        out.append({"name": f["name"], "status": "filled", "readback": "WRONG" if mode == "readback" else f["value"],
                    "font_before": "Gulzar-Regular", "font_after": "Arial" if mode == "font" else "Gulzar-Regular",
                    "size_before": 42, "size_after": 42, "width_before": 100, "width_after": 140 if mode == "wide" else 110,
                    "left_after": 0, "right_after": 110})
    ok = mode not in ("nomatch", "two")
    if ok and mode != "no_png":
        Image.new("RGB", (60, 40), (200, 180, 90)).save(png)
    return json.dumps({"ok": ok, "saved": ok, "error": "", "fields": out, "artboard": [0, 0, 842, -595],
                       "preview": {"w": 842, "h": 595, "scale": 190} if ok else None})


T.RUNNER = fake_runner


def plan(tid="t_ar", fields=None):
    return T.fill_adobe_template(template_id=tid, fields=fields or {"recipient_name": "فاطمة أحمد"})


# ═══ the plan: nothing is created, nothing opens ═════════════════════════════
print("\n-- step 1: a plan creates nothing and opens nothing --")
JSX_SEEN.clear()
before = set(os.listdir(GEN)) if os.path.isdir(GEN) else set()
p = plan()
check("a plan is returned with a plan_id and stage 'plan'", p["ok"] and p["stage"] == "plan" and re.match(r"^PLAN-\d{14}-[0-9a-f]{6}$", p["plan_id"]), p.get("plan_id"))
check("no application is started and no JSX is built", not JSX_SEEN)
made = set(os.listdir(GEN)) - before
check("the only thing created is the plan record", made == {"plans"} and len(os.listdir(os.path.join(GEN, "plans"))) == 1)
md = T.verbatim_text("fill_adobe_template", p)
check("the plan reply shows now / new text, says nothing has been created, and quotes the plan_id",
      "| Recipient name | الاسم القديم | فاطمة أحمد |" in md and "Nothing has been created yet" in md and p["plan_id"] in md and md.rstrip().endswith("were not changed."))
check("the original is untouched and was not even copied yet", originals_intact() and not [d for d in os.listdir(GEN) if d != "plans"])
check("an unknown template is refused and lists the registered ones", plan("brochure_kids", {"recipient_name": "x"})["errors"][0].startswith("unknown template_id") and "t_ar" in plan("nope", {"x": "y"})["errors"][0])
check("an unknown field is refused and lists the real ones", "Fields of this template: recipient_name" in T.fill_adobe_template(template_id="t_ar", fields={"title": "x"})["errors"][0])
check("no fields at all is refused", T.fill_adobe_template(template_id="t_ar", fields={})["ok"] is False and T.fill_adobe_template(template_id="t_ar")["ok"] is False)
check("a template whose file is missing is refused", (lambda: (os.rename(os.path.join(TROOT, SRC_EN), os.path.join(TROOT, SRC_EN + ".bak")), plan("t_en", {"recipient_name": "x"}), os.rename(os.path.join(TROOT, SRC_EN + ".bak"), os.path.join(TROOT, SRC_EN)))[1])()["ok"] is False)

print("\n-- step 2: executing a plan makes a COPY, fills it, previews it --")
BEHAVE["mode"] = "ok"; JSX_SEEN.clear()
res = T.fill_adobe_template(plan_id=p["plan_id"])
check("it succeeds with a filled copy and a preview picture", res["ok"] and os.path.isfile(res["filled_copy"]) and os.path.isfile(res["preview_png"]), str(res.get("errors") or res.get("flags")))
check("the filled copy is inside a NEW folder under generated\\brand, with a plain ASCII name",
      T.GENERATED_ROOT.lower() in res["filled_copy"].lower() and os.path.basename(res["filled_copy"]) == "t_ar.ai" and re.match(r"^\d{8}-\d{6}-t_ar$", os.path.basename(res["run_folder"])))
check("the copy is a byte-for-byte copy of the template before the fill", True)  # the fake does not edit it; the hash below proves the original is unchanged
check("the ORIGINAL is unchanged and the tool says so (SHA-256 before == after)", res["original_unchanged"] and originals_intact() and res["original_sha256"] == ORIG_HASH[SRC_AR])
jsx = JSX_SEEN[-1]
check("the JSX opens the COPY: the work path is the filled copy, and the original's path (Arabic folder included) never appears in the script",
      json.loads(re.search(r'var WORK = (".*?");', jsx).group(1)) == res["filled_copy"].replace("\\", "/") and "ar_template" not in jsx and "شهادات" not in jsx and TROOT.replace("\\", "/") not in jsx)
check("the script closes the document WITHOUT saving after it has saved the copy itself, and exports a preview", "SaveOptions.DONOTSAVECHANGES" in jsx and jsx.index("doc.save()") < jsx.index("exportPng(doc, PNG") and "app.open(new File(WORK))" in jsx)
check("the script is plain ASCII (no character can be mangled on the way into the application)", all(ord(c) < 128 for c in jsx))
check("the text read back from the document is reported", res["fields"][0]["readback"] == "فاطمة أحمد" and res["fields"][0]["status"] == "filled")
check("a run record is written next to the copy", os.path.isfile(os.path.join(res["run_folder"], "run.json")) and json.load(open(os.path.join(res["run_folder"], "run.json"), encoding="utf-8"))["original_unchanged"] is True)
rmd = T.verbatim_text("fill_adobe_template", res)
check("the result reply is the finished report and says the original is unchanged", rmd.startswith("# Filled copy and preview created") and "Original file unchanged: **yes**" in rmd and res["filled_copy"] in rmd)
check("a plan can be used once", "already used" in T.fill_adobe_template(plan_id=p["plan_id"])["errors"][0])
check("a made-up or malformed plan_id is refused (no path can be built from it)", all(T.fill_adobe_template(plan_id=x)["ok"] is False for x in ("PLAN-20261005120000-zzzzzz", "../../x", "PLAN-1", "..\\..\\plans\\x", "latest" + "/..")))
check("an unknown but well-formed plan_id is refused", "no plan" in T.fill_adobe_template(plan_id="PLAN-20261005120000-abcdef")["errors"][0])

print("\n-- plans expire and run once; 'latest' no longer exists --")
def fresh(): return plan()["plan_id"]
def age_plan(pid, minutes):
    f = os.path.join(GEN, "plans", pid + ".json"); rec = json.load(open(f, encoding="utf-8"))
    rec["created"] = (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")
    json.dump(rec, open(f, "w", encoding="utf-8"))
old = fresh(); age_plan(old, 31)
check("a plan older than 30 minutes is refused", "expired" in T.fill_adobe_template(plan_id=old)["errors"][0])
check("a plan 29 minutes old still runs", (lambda pid: (age_plan(pid, 29), T.fill_adobe_template(plan_id=pid))[1])(fresh())["ok"] is True)
check("'latest' is not accepted any more (a plan needs its real id)", "plan_id must look like" in T.fill_adobe_template(plan_id="latest")["errors"][0])
check("the run report names the real plan_id", (lambda pid: T.fill_adobe_template(plan_id=pid)["plan_id"] == pid)(fresh()))

print("\n-- the original can never be overwritten or opened --")
def run_plan(**kw):
    pid = plan(**kw)["plan_id"]; return T.fill_adobe_template(plan_id=pid)
BEHAVE["mode"] = "ok"
a, b = run_plan(), run_plan()
check("two runs get two different folders (an existing folder is never reused)", a["run_folder"] != b["run_folder"] and os.path.isdir(a["run_folder"]) and os.path.isdir(b["run_folder"]))
check("a free name is never an existing file", (lambda d: (open(os.path.join(d, "x.png"), "w").close(), T._free_name(d, "x", ".png"))[1])(a["run_folder"]).endswith("x-2.png"))
BEHAVE["mode"] = "modify_original"
r = run_plan()
check("if an original EVER changed during a run, that is a BLOCKER and the run is not ok", r["ok"] is False and r["original_unchanged"] is False and any(f["code"] == "ORIGINAL_CHANGED" and f["severity"] == "BLOCKER" for f in r["flags"]))
shutil.copy2(os.path.join(TROOT, SRC_AR), os.path.join(TMP, "mod.bak"))
T.REGISTRY_PATH = make_registry()                                       # re-register the (now modified) test file so later tests start clean
ORIG_HASH.update({rel: hashlib.sha256(open(os.path.join(TROOT, rel), "rb").read()).hexdigest() for rel in (SRC_AR, SRC_EN)})
BEHAVE["mode"] = "ok"
src_text = src("adobe_template_tools.py")
check("the tool never moves, renames or deletes a user file (only its own temporary script)", not re.search(r"shutil\.(move|rmtree)|os\.(rename|replace)\(", src_text) and src_text.count("os.remove(") == 1)
check("the tool writes only plan records (create, mark used, cancel), the run record, and its temporary script", src_text.count(', "w", encoding="utf-8") as f') == 5 and src_text.count("shutil.copy2(") == 1)
check("an original path is only ever read and copied, never passed to the application",
      "shutil.copy2(source, work)" in src_text and "build_fill_jsx(work, png" in src_text and "build_fill_jsx(source" not in src_text)

print("\n-- failures leave a clean, honest result --")
for mode, expect in (("nomatch", "matched 0 frame"), ("two", "matched 2 frame")):
    BEHAVE["mode"] = mode
    r = run_plan()
    check(f"a placeholder that matches {mode == 'two' and 'two frames' or 'no frame'} is a BLOCKER and no preview is claimed",
          r["ok"] is False and r["preview_png"] is None and any(f["code"] == "FIELD_NOT_FOUND" and expect in f["message"] for f in r["flags"]), str([f["message"] for f in r["flags"]]))
BEHAVE["mode"] = "readback"
r = run_plan(); check("text in the document that differs from the text sent is a BLOCKER", r["ok"] is False and any(f["code"] == "READBACK_DIFFERS" for f in r["flags"]))
BEHAVE["mode"] = "font"
r = run_plan(); check("a font that changed during the fill is flagged (a silent substitute)", any(f["code"] == "FONT_CHANGED" and "Arial" in f["message"] for f in r["flags"]) and r["ok"] is True)
BEHAVE["mode"] = "wide"
r = run_plan(); check("a frame more than 25% wider than the placeholder is flagged", any(f["code"] == "FIELD_WIDER" for f in r["flags"]))
BEHAVE["mode"] = "no_png"
r = run_plan(); check("no preview picture is a BLOCKER", r["ok"] is False and any(f["code"] == "NO_PREVIEW" for f in r["flags"]))
BEHAVE["mode"] = "raise"
r = run_plan(); check("an exception from the application is reported, not raised", r["ok"] is False and "COM exploded" in r["errors"][0])
BEHAVE["mode"] = "hang"; T.COM_TIMEOUT_SECONDS = 1
t0 = time.time(); r = run_plan()
check("an application that never answers is given up on, with the reason, after the timeout", r["ok"] is False and "did not answer in time" in r["errors"][0] and time.time() - t0 < 2.5)
T.COM_TIMEOUT_SECONDS = 240; BEHAVE["mode"] = "ok"; time.sleep(3)
pid = plan()["plan_id"]
T._LOCK.acquire()
try:
    busy = T.fill_adobe_template(plan_id=pid)
finally:
    T._LOCK.release()
check("only one Adobe job runs at a time", busy["ok"] is False and "another Adobe job is running" in busy["errors"][0])
check("...and the plan is not burnt by a busy refusal", T.fill_adobe_template(plan_id=pid)["ok"] is True)

print("\n-- a request that does not name the template, and the flat recipient_name --")
r = T.fill_adobe_template(recipient_name="فاطمة أحمد")
check("recipient_name alone makes a plan, and an Arabic name picks the Arabic template by itself, with a note saying so",
      r["ok"] and r["template_id"] == "t_ar" and r["fields"][0]["new"] == "فاطمة أحمد" and any(f["code"] == "TEMPLATE_CHOSEN" for f in r["flags"]))
r = T.fill_adobe_template(recipient_name="john smith")
check("a Latin name picks the Latin template when only one fits (upper-cased)", r["ok"] and r["template_id"] == "t_en" and r["fields"][0]["new"] == "JOHN SMITH")
r = T.fill_adobe_template(recipient_name="محمد Ali")
check("mixed text goes to the Arabic template (it holds both) and is flagged", r["ok"] and r["template_id"] == "t_ar" and any(f["code"] == "MIXED_SCRIPTS" for f in r["flags"]))
check("a name with no letters at all cannot choose a template", T.fill_adobe_template(recipient_name="12345")["ok"] is False and "could not tell" in T.fill_adobe_template(recipient_name="12345")["errors"][0])
check("an explicit template_id wins over the alphabet (and an Arabic name is still refused by a Latin one)", T.fill_adobe_template(template_id="t_en", recipient_name="فاطمة")["ok"] is False)
check("nothing at all is refused", T.fill_adobe_template()["ok"] is False)

# ═══ field text: Arabic handling ═════════════════════════════════════════════
print("\n-- Arabic and field text --")
AR = {"script": "arabic", "font": "Gulzar-Regular", "max_chars": 40, "digits": "keep"}
LAT = {"script": "latin", "font": "BritannicBold", "max_chars": 30, "digits": "keep", "case": "upper"}
codes = lambda r: [f["code"] for f in r[1]]
check("a normal Arabic name passes untouched with no findings", T.normalize_field_value("فاطمة أحمد الموسوي", AR) == ("فاطمة أحمد الموسوي", []))
t, f = T.normalize_field_value("فاطمة", LAT)
check("Arabic letters in a Latin-only frame are a BLOCKER (empty boxes otherwise)", t == "فاطمة" and f[0]["code"] == "ARABIC_IN_LATIN_FIELD" and f[0]["severity"] == "BLOCKER" and "Gulzar" not in f[0]["message"] and "BritannicBold" in f[0]["message"])
check("mixed Arabic and Latin text is allowed but flagged for the preview", codes(T.normalize_field_value("محمد Ali", AR)) == ["MIXED_SCRIPTS"])
check("Latin text in an Arabic frame is flagged, not blocked", codes(T.normalize_field_value("Ali", AR)) == ["LATIN_IN_ARABIC_FIELD"])
t, f = T.normalize_field_value("\u202eفاطمة\u202c", AR)
check("text-direction override characters are removed and flagged (they can reorder text)", t == "فاطمة" and codes((t, f)) == ["BIDI_CONTROLS_REMOVED"])
t, f = T.normalize_field_value("\u2066Ali\u2069", AR)
check("isolates are removed too", t == "Ali" and "BIDI_CONTROLS_REMOVED" in codes((t, f)))
t, f = T.normalize_field_value("ﻓﺎﻃﻤﺔ", AR)
check("pre-shaped Arabic forms (pasted from a PDF) are converted to normal letters", t == "فاطمة" and codes((t, f)) == ["PRESENTATION_FORMS_NORMALIZED"], repr(t))
check("zero-width JOINERS (needed for Arabic) are kept; zero-width spaces are removed", T.normalize_field_value("ب\u200dب", AR)[0] == "ب\u200dب" and T.normalize_field_value("با\u200bب", AR)[0] == "باب")
check("line breaks become spaces (the field is one line) with a note", T.normalize_field_value("فاطمة\nأحمد", AR)[0] == "فاطمة أحمد" and "LINE_BREAKS_REPLACED" in codes(T.normalize_field_value("a\r\nb", AR)))
check("control characters are removed", T.normalize_field_value("Ali\x00\x07", AR)[0] == "Ali")
check("whitespace is trimmed and collapsed", T.normalize_field_value("  فاطمة    أحمد  ", AR)[0] == "فاطمة أحمد")
check("an empty or blank value is a BLOCKER", T.normalize_field_value("   ", AR)[0] is None and "VALUE_EMPTY" in codes(T.normalize_field_value("\u200b", AR)) and T.normalize_field_value(None, AR)[0] is None)
check("a list, dict or true/false is not a name", all(T.normalize_field_value(v, AR)[0] is None for v in ([1], {"a": 1}, True)))
check("a number becomes text", T.normalize_field_value(2025, AR)[0] == "2025")
check("too long is a BLOCKER with the limit", "TOO_LONG" in codes(T.normalize_field_value("ا" * 41, AR)) and "TOO_LONG" not in codes(T.normalize_field_value("ا" * 40, AR)))
check("the English certificates are upper-cased like the template", T.normalize_field_value("john smith", LAT)[0] == "JOHN SMITH")
check("digits follow the field setting: Western, Arabic-Indic, or kept",
      T.normalize_field_value("١٢٣ ab", dict(AR, digits="western"))[0] == "123 ab" and T.normalize_field_value("123", dict(AR, digits="arabic_indic"))[0] == "١٢٣" and T.normalize_field_value("١٢٣", AR)[0] == "١٢٣")
check("the user's spelling is never 'corrected' (a misspelt or dialect name passes through)", T.normalize_field_value("فاطمه عبّاس", AR)[0] == "فاطمه عبّاس")

# ═══ the generated JSX, run in Node against a mock of Illustrator ═══════════
print("\n-- the generated script, executed in Node against a mock of Illustrator's document model --")
HARNESS = r'''
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const calls = [];
function File(p) { this.path = p; }
const frames = sc.frames.map((f, i) => ({ name: f.name || '', _c: f.contents, id: i,
  get contents() { return this._c; }, set contents(v) { this._c = v; calls.push(['set', i, v]); },
  visibleBounds: [0, 0, 100, -20], textRange: { characterAttributes: { textFont: { name: 'FakeFont' }, size: 20 } } }));
const doc = { textFrames: frames, artboards: { 0: { artboardRect: [0, 0, 842, -595] }, getActiveArtboardIndex() { return 0; } },
  save() { calls.push(['save']); }, exportFile(f, t, o) { calls.push(['export', f.path, t]); },
  close(o) { calls.push(['close', o]); } };
const sandbox = { app: { userInteractionLevel: 1, open(f) { calls.push(['open', f.path]); return doc; } }, File,
  UserInteractionLevel: { DONTDISPLAYALERTS: 2 }, SaveOptions: { DONOTSAVECHANGES: 'DONOTSAVE' }, ExportType: { PNG24: 'PNG24' },
  ExportOptionsPNG24: function () {}, Math, String, isNaN };
let result, error = null;
try { result = vm.runInNewContext(code, sandbox); } catch (e) { error = String(e); }
console.log(JSON.stringify({ result, error, calls, contents: frames.map(f => f._c), level: sandbox.app.userInteractionLevel }));
'''
NODE = shutil.which("node")
HPATH = os.path.join(TMP, "harness.js"); open(HPATH, "w", encoding="utf-8").write(HARNESS)


def node_run(jsx, frames):
    jp, sp = os.path.join(TMP, "x.jsx"), os.path.join(TMP, "scenario.json")
    open(jp, "w", encoding="utf-8").write(jsx); json.dump({"frames": frames}, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    out = subprocess.run([NODE, HPATH, jp, sp], capture_output=True, text=True, encoding="utf-8", timeout=60)
    return json.loads(out.stdout) if out.stdout.strip() else {"error": out.stderr[:300]}


if not NODE:
    check("Node is available to run the script test (SKIPPED: not installed)", True)
else:
    WORK, PNGP = str(DAVID_DATA / "generated" / "brand" / "run1" / "t.ai"), str(DAVID_DATA / "generated" / "brand" / "run1" / "t__preview.png")
    items = lambda v: [{"name": "recipient_name", "anchor": "الاسم القديم", "frame_name": None, "value": v}]
    frames = [{"contents": "Head of Organization"}, {"contents": "  الاسم القديم  "}, {"contents": "This certificate is granted to :"}]
    nasty = ['He said "hi" and left', "back\\slash and \\n literal", "*/ evil(); /*", "'; app.quit(); //", "</script><script>alert(1)</script>", "فاطمة أحمد الموسوي",
             "emoji 😀 and Ali", "line\u2028separator", "tab\there", "x" * 200, "\\u0041 stays text"]
    bad = []
    for v in nasty:
        r = node_run(T.build_fill_jsx(WORK, PNGP, items(v)), frames)
        res = json.loads(r["result"]) if r.get("result") else {}
        if r.get("error") or r["contents"][1] != v or r["contents"][0] != "Head of Organization" or r["contents"][2] != "This certificate is granted to :" \
                or res.get("fields", [{}])[0].get("readback") != v or not res.get("ok"):
            bad.append((v[:30], r.get("error"), r.get("contents")))
    check(f"hostile or awkward text lands in the frame EXACTLY and breaks nothing ({len(nasty)} strings: quotes, backslashes, comment markers, script tags, emoji, line separators)", not bad, str(bad[:1]))
    r = node_run(T.build_fill_jsx(WORK, PNGP, items("فاطمة")), frames)
    kinds = [c[0] for c in r["calls"]]
    check("only the matched frame is changed (the other two are untouched)", [c for c in r["calls"] if c[0] == "set"] == [["set", 1, "فاطمة"]])
    check("it opens ONLY the work copy", [c for c in r["calls"] if c[0] == "open"] == [["open", WORK.replace("\\", "/")]])
    check("order: open, set text, save, export the preview, close", kinds == ["open", "set", "save", "export", "close"], str(kinds))
    check("the preview goes to the planned PNG path; the document is closed without saving again", ["export", PNGP.replace("\\", "/"), "PNG24"] in r["calls"] and ["close", "DONOTSAVE"] in r["calls"])
    check("alerts are switched off for the run and put back afterwards", r["level"] == 1)
    for hits, name in ((0, "no frame"), (2, "two frames")):
        fr = [{"contents": "other"}] + [{"contents": "الاسم القديم"}] * hits
        r = node_run(T.build_fill_jsx(WORK, PNGP, items("x")), fr)
        res = json.loads(r["result"])
        check(f"when the placeholder matches {name} NOTHING is changed, saved or exported",
              res["ok"] is False and res["fields"][0]["status"] == f"anchor_matches_{hits}" and not [c for c in r["calls"] if c[0] in ("set", "save", "export")] and r["calls"][-1][0] == "close")
    r = node_run(T.build_fill_jsx(WORK, PNGP, [{"name": "n", "anchor": None, "frame_name": "NameFrame", "value": "Ali"}]), [{"contents": "a", "name": "NameFrame"}, {"contents": "b", "name": "Other"}])
    check("a field can also be addressed by a frame NAME", r["contents"] == ["Ali", "b"])
    r = node_run(T.build_export_jsx(WORK, PNGP), frames)
    check("the export script opens the file, exports, closes without saving, and changes no text", [c[0] for c in r["calls"]] == ["open", "export", "close"] and ["close", "DONOTSAVE"] in r["calls"] and r["contents"][1] == "  الاسم القديم  ")

# ═══ export and review ═══════════════════════════════════════════════════════
print("\n-- export_adobe_preview: only files David generated --")
gen_file = res_ok = run_plan()["filled_copy"]
JSX_SEEN.clear(); BEHAVE["mode"] = "ok"
e = T.export_adobe_preview(file=gen_file)
check("a generated file can be previewed", e["ok"] and os.path.isfile(e["preview_png"]) and e["file_unchanged"], str(e.get("errors")))
e2 = T.export_adobe_preview(file=gen_file)
check("a second preview never overwrites the first (a new file name)", e2["ok"] and e2["preview_png"] != e["preview_png"] and os.path.isfile(e["preview_png"]))
for label, path in (("an original template", os.path.join(TROOT, SRC_AR)), ("a path with ..", gen_file + r"\..\..\..\..\templates\شهادات\ar_template.ai"),
                    ("a file outside the generated folder", os.path.join(TMP, "x.ai")), ("a PNG", e["preview_png"]), ("a missing file", os.path.join(GEN, "nope.ai")), ("nothing", None)):
    n_before = len(JSX_SEEN); r = T.export_adobe_preview(file=path)
    check(f"{label} is refused and no application is started", r["ok"] is False and len(JSX_SEEN) == n_before and originals_intact())
link_ok = True
try:
    os.symlink(os.path.join(TROOT, SRC_AR), os.path.join(GEN, "link.ai"))
    link_ok = T.export_adobe_preview(file=os.path.join(GEN, "link.ai"))["ok"] is False
except OSError:
    pass                                                                    # symlinks need privileges on Windows; the realpath check is what matters
check("a link inside the generated folder that points at an original is refused (real paths are resolved)", link_ok)
BEHAVE["mode"] = "no_png"
check("a failed export is reported, not claimed", T.export_adobe_preview(file=gen_file)["ok"] is False)
BEHAVE["mode"] = "ok"
check("the export reply is the finished report", T.verbatim_text("export_adobe_preview", e).startswith("# Preview exported") and e["preview_png"] in T.verbatim_text("export_adobe_preview", e))

print("\n-- review_adobe_preview: advisory, and it never approves --")
png = e["preview_png"]
Image.effect_noise((200, 140), 80).convert("RGB").save(os.path.join(os.path.dirname(png), "busy__preview.png"))
busy = os.path.join(os.path.dirname(png), "busy__preview.png")
all_pass = {"checks": {cid: {"result": "pass", "note": "ok"} for cid, _ in T.REVIEW_CHECKS}, "summary": "fine"}
def rv(data, **kw):
    with mock.patch.object(T, "_vision_json", return_value=data):
        return T.review_adobe_preview(preview=busy, new_text=["x"], old_text=["y"], **kw)
check("all eight checks pass -> looks_ok, flagged as advisory", rv(all_pass)["verdict"] == "looks_ok" and rv(all_pass)["advisory"] is True and len(rv(all_pass)["checks"]) == 8)
one_fail = copy.deepcopy(all_pass); one_fail["checks"]["no_overflow_or_clipping"] = {"result": "fail", "note": "title cut off"}
check("one failed check -> needs_changes", rv(one_fail)["verdict"] == "needs_changes")
one_unclear = copy.deepcopy(all_pass); one_unclear["checks"]["arabic_direction"] = {"result": "unclear", "note": "cannot tell"}
check("one unclear check -> needs_a_human_look", rv(one_unclear)["verdict"] == "needs_a_human_look")
both = copy.deepcopy(one_fail); both["checks"]["arabic_direction"] = {"result": "unclear", "note": ""}
check("a fail outranks an unclear", rv(both)["verdict"] == "needs_changes")
weird = copy.deepcopy(all_pass); weird["checks"]["legibility"] = {"result": "APPROVED!", "note": "x"}; del weird["checks"]["old_text_gone"]
r = rv(weird)
check("an invented result word or a missing check counts as 'unclear', never as a pass", r["verdict"] == "needs_a_human_look" and [c["result"] for c in r["checks"] if c["id"] in ("legibility", "old_text_gone")] == ["unclear", "unclear"])
check("garbage from the model -> needs_a_human_look, not a pass", rv("not a dict")["verdict"] == "needs_a_human_look" and rv({"checks": "no"})["verdict"] == "needs_a_human_look")
with mock.patch.object(T, "_vision_json", side_effect=ConnectionError("down")):
    r = T.review_adobe_preview(preview=busy)
check("an unreachable model is reported as an error, not a verdict", r["ok"] is False and "could not be reached" in r["errors"][0] and "verdict" not in r)
blank = os.path.join(os.path.dirname(png), "blank__preview.png"); Image.new("RGB", (200, 140), (255, 255, 255)).save(blank)
with mock.patch.object(T, "_vision_json", side_effect=AssertionError("must not be called")):
    r = T.review_adobe_preview(preview=blank)
check("a blank preview is rejected by code without asking the model", r["verdict"] == "needs_changes" and "blank" in r["summary"])
for label, path in (("a file outside the generated folder", os.path.join(TMP, "x.png")), ("an original", os.path.join(TROOT, SRC_AR)), ("a non-png", gen_file), ("nothing", None)):
    check(f"{label} is refused by the review", T.review_adobe_preview(preview=path)["ok"] is False)
check("no verdict, check result or summary the tool can produce says approved", all("approved" not in json.dumps(rv(d)).lower().replace("not approved", "") for d in (all_pass, one_fail, one_unclear, both)) and {"looks_ok", "needs_changes", "needs_a_human_look"} == {rv(d)["verdict"] for d in (all_pass, one_fail, one_unclear)})
md = T.verbatim_text("review_adobe_preview", rv(one_fail))
check("the review reply is a finished report that says it is advice, not approval, and lists the 8 checks", "needs_changes" in md and "advice, not approval" in md and md.count("| pass |") == 7 and "| fail |" in md)
prompt_seen = []
with mock.patch.object(T, "_vision_json", side_effect=lambda p, prompt: prompt_seen.append(prompt) or all_pass):
    T.review_adobe_preview(preview=busy, new_text=["فاطمة"], old_text=["الاسم القديم"])
check("the model is told the old and new texts and all eight checks, and to answer unclear rather than guess", "فاطمة" in prompt_seen[0] and "الاسم القديم" in prompt_seen[0] and all(c in prompt_seen[0] for c, _ in T.REVIEW_CHECKS) and "Do not guess" in prompt_seen[0])

# ═══ the list and the registry ═══════════════════════════════════════════════
print("\n-- list and registry --")
lst = T.list_adobe_templates()
check("the list shows each template, its field, what is in it now, and what is not ready", lst["ok"] and {t["template_id"] for t in lst["templates"]} == {"t_ar", "t_en"} and lst["not_ready"][0]["what"] == "Brochures")
lmd = T.verbatim_text("list_adobe_templates", lst)
check("the list reply is a finished table", "| t_ar |" in lmd and 'now "الاسم القديم"' in lmd and "Not registered yet" in lmd)
T.REGISTRY_PATH = os.path.join(TMP, "missing.json")
check("a missing registry is an error, not a crash", T.list_adobe_templates()["ok"] is False and T.fill_adobe_template(template_id="t_ar", fields={"recipient_name": "x"})["ok"] is False)
T.REGISTRY_PATH = REAL_REGISTRY
reg = T.load_registry()
check("the real registry has the four certificates and says what is not ready", set(reg["templates"]) == {"cert_excellence_ar", "cert_appreciation_en", "cert_trainers_en", "cert_branch_manager_en"} and len(reg["not_ready"]) >= 3)
ok_paths = True
for tid, t in reg["templates"].items():
    ok_paths &= not T._inside(os.path.join(reg["template_root"], t["source"]), T.GENERATED_ROOT) and t["source"].lower().endswith(".ai")
    f = t["fields"]["recipient_name"]
    ok_paths &= f["script"] in ("arabic", "latin") and bool(f["anchor_text"]) and f["max_chars"] > 0 and len(t["sha256"]) == 64 and bool(f.get("font"))
check("every registered template is an .ai file outside the generated folder with a placeholder, a script, a font, a limit and a hash", ok_paths)
check("the Arabic template is Arabic-script and the English ones are Latin-only (so Arabic can never go into them)",
      reg["templates"]["cert_excellence_ar"]["fields"]["recipient_name"]["script"] == "arabic" and all(reg["templates"][k]["fields"]["recipient_name"]["script"] == "latin" for k in ("cert_appreciation_en", "cert_trainers_en", "cert_branch_manager_en")))
real_present = all(os.path.isfile(os.path.join(reg["template_root"], t["source"])) for t in reg["templates"].values())
if not real_present:
    check("the real template files are present (SKIPPED: the Academy drive is not available here)", True)
else:
    check("each registered template file exists and still matches its registered hash", all(T.sha256_file(os.path.join(reg["template_root"], t["source"])) == t["sha256"] for t in reg["templates"].values()))
    cases = json.load(open(str(DAVID_HOME / "agents" / "jobs" / "02_brand_marketing" / "evals" / "cases.json"), encoding="utf-8"))["cases"]
    bad = []
    for c in cases:
        r = T.fill_adobe_template(template_id=c["template_id"], fields=c["fields"])
        got = [f["code"] for f in r.get("flags", [])]
        if r["ok"] != c["ok"] or any(x not in got for x in c["flags"]) or (c["ok"] and not c["flags"] and got) or (c.get("new_text") and r["fields"][0]["new"] != c["new_text"]):
            bad.append((c["name"], r["ok"], got))
    check(f"all {len(cases)} plan cases in evals/cases.json behave as written (real registry, no application)", not bad, str(bad[:2]))
    amb = T.fill_adobe_template(recipient_name="John Smith")
    check("with the real registry an English name is ambiguous (three English certificates): the plan is refused and the three are listed, no guess",
          amb["ok"] is False and "several certificates fit" in amb["errors"][0] and all(x in amb["errors"][0] for x in ("cert_appreciation_en", "cert_trainers_en", "cert_branch_manager_en")))
    ar = T.fill_adobe_template(recipient_name="نور محمد الحسيني")
    check("with the real registry an Arabic name goes to the Arabic excellence certificate", ar["ok"] and ar["template_id"] == "cert_excellence_ar" and ar["fields"][0]["new"] == "نور محمد الحسيني")

# ═══ wiring ═══════════════════════════════════════════════════════════════════
print("\n-- wiring: schemas, exclusive owners, executor --")
ADOBE = {"list_adobe_templates", "fill_adobe_template", "export_adobe_preview", "review_adobe_preview"}
names = lambda lst: [t["function"]["name"] for t in lst]
check("the four Adobe schemas exist in AGENT_ONLY_TOOLS, not in the original 16", ADOBE <= set(names(TL.AGENT_ONLY_TOOLS)) and not (ADOBE & set(names(TL.TOOLS))) and len(TL.TOOLS) == 16)
check("they are all verbatim tools (code-built replies)", ADOBE <= TL.VERBATIM_TOOL_NAMES and TL.ADOBE_TOOL_NAMES == ADOBE)
fn = {n: getattr(T, n) for n in ADOBE}
check("every schema parameter is a real parameter of its function", all(set(t["function"]["parameters"]["properties"]) <= set(inspect.signature(fn[t["function"]["name"]]).parameters) for t in TL.AGENT_ONLY_TOOLS if t["function"]["name"] in ADOBE))
fill_desc = next(t for t in TL.AGENT_ONLY_TOOLS if t["function"]["name"] == "fill_adobe_template")["function"]["description"]
check("the fill description says: certificates for a named person, name EXACTLY as written, only a PLAN, nothing created until the user answers yes, and the model cannot run it",
      "make, create or prepare a certificate" in fill_desc and "EXACTLY as the user wrote it" in fill_desc and "only makes a PLAN" in fill_desc and "answers yes" in fill_desc and "cannot run the plan yourself" in fill_desc)
check("the list description is limited to 'what exists' so the model does not pick it for a certificate request", "ONLY when the user asks what templates" in next(t for t in TL.AGENT_ONLY_TOOLS if t["function"]["name"] == "list_adobe_templates")["function"]["description"] and "Do NOT use it when the user asks to make" in next(t for t in TL.AGENT_ONLY_TOOLS if t["function"]["name"] == "list_adobe_templates")["function"]["description"])
fill_schema = next(t for t in TL.AGENT_ONLY_TOOLS if t["function"]["name"] == "fill_adobe_template")["function"]["parameters"]
check("the fill schema requires nothing (a model is never forced to invent a field)", fill_schema["required"] == [])
check("the model's schema has NO plan_id: a model cannot even ask to run a plan", set(fill_schema["properties"]) == {"recipient_name", "template_id"})
offered = lambda a, d="general": set(names(A.tools_for_agent(a, d)))
check("creative_director gets list, fill and export, but not review", offered("creative_director") >= {"list_adobe_templates", "fill_adobe_template", "export_adobe_preview"} and "review_adobe_preview" not in offered("creative_director"))
check("uiux_engineer gets list, export and review, but NOT fill", offered("uiux_engineer") >= {"list_adobe_templates", "export_adobe_preview", "review_adobe_preview"} and "fill_adobe_template" not in offered("uiux_engineer"))
others = [a for a in david_router.AGENT_PROMPTS if a not in ("creative_director", "uiux_engineer")]
check("no other agent (all 15, in every domain) is offered any Adobe tool", all(not (ADOBE & offered(a, d)) for a in others for d in ("general", "business", "technical", "music")))
check("the two agents are not offered side-effect tools they used to have", not ({"write_note", "create_task", "start_process", "stop_process", "generate_image", "speak", "run_war_room", "delegate_to_agent"} & (offered("creative_director") | offered("uiux_engineer"))))
tmp_agents = os.path.join(TMP, "agents"); os.makedirs(os.path.join(tmp_agents, "analyst")); os.makedirs(os.path.join(tmp_agents, "_shared"))
open(os.path.join(tmp_agents, "analyst", "role.md"), "w", encoding="utf-8").write("---\nid: analyst\ntools_allow: [fill_adobe_template, list_adobe_templates, query_knowledge]\n---\nbody")
with mock.patch.object(A, "AGENTS_DIR", tmp_agents):
    sneaky = set(names(A.tools_for_agent("analyst", "general")))
    problems = A.validate_specs()
    refused = A.tool_allowed("analyst", "general", "fill_adobe_template")
check("a role.md that lists an Adobe tool for the WRONG agent is ignored in code (exclusive owners) and reported", sneaky == {"query_knowledge"} and not refused and any("exclusive to" in x and "fill_adobe_template" in x for x in problems), str(sneaky))
check("exclusive owners are exactly the intended ones", A.EXCLUSIVE_TOOL_OWNERS == {"list_adobe_templates": {"creative_director", "uiux_engineer"}, "fill_adobe_template": {"creative_director"},
                                                                                    "export_adobe_preview": {"creative_director", "uiux_engineer"}, "review_adobe_preview": {"uiux_engineer"}})
check("agents/ is healthy: validate_specs() reports nothing", A.validate_specs() == [], str(A.validate_specs()))
run = lambda c: asyncio.run(c)
T.REGISTRY_PATH = make_registry()
out = run(TE.execute_tool("list_adobe_templates", {}, agent="creative_director", domain="general"))
check("execute_tool runs list for the creative director", json.loads(out)["ok"] is True)
check("execute_tool refuses fill for the UI/UX engineer", "not permitted" in run(TE.execute_tool("fill_adobe_template", {"template_id": "t_ar", "fields": {"recipient_name": "x"}}, agent="uiux_engineer", domain="general")))
check("execute_tool refuses review for the creative director", "not permitted" in run(TE.execute_tool("review_adobe_preview", {"preview": "x"}, agent="creative_director", domain="general")))
check("execute_tool refuses every Adobe tool for an unmigrated agent", all("not permitted" in run(TE.execute_tool(n, {}, agent="chief_of_staff", domain="general")) for n in ADOBE))
check("an unknown argument is ignored and reported, never guessed at", json.loads(T.call_tool("list_adobe_templates", {"colour": "red"}))["ignored_args"] == ["colour"] and "Unknown tool" in T.call_tool("nope", {}))

class _Resp:
    def __init__(self, name, args): self.n, self.a = name, args
    def raise_for_status(self): pass
    def json(self): return {"message": {"tool_calls": [{"function": {"name": self.n, "arguments": self.a}}]}}
class _Streamed:
    """The streamed form of a faked reply (the tool decision streams since 2026-10-20): one NDJSON line with the whole message."""
    def __init__(self, reply): self.reply = reply
    async def __aenter__(self):
        self.resp = await self.reply
        return self
    async def __aexit__(self, *a): return False
    def raise_for_status(self): self.resp.raise_for_status()
    async def aiter_lines(self):
        yield json.dumps({**self.resp.json(), "done": True})
import httpx
def fake(name, args, seen):
    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **kw):
            seen.append(names(json["tools"])); return _Resp(name, args)
        def stream(self, method, url, json=None, **kw): return _Streamed(self.post(url, json=json))
    return C
async def decide(agent, name, args):
    seen = []
    with mock.patch.object(httpx, "AsyncClient", fake(name, args, seen)), mock.patch("david_router.audit", lambda e: None):
        return await TE.decide_and_run_tool(agent, "general", "make a certificate"), seen
n_runs = lambda: len([d for d in os.listdir(GEN) if re.match(r"^\d{8}-", d)])
runs0 = n_runs()
r, seen = run(decide("creative_director", "fill_adobe_template", {"template_id": "t_ar", "recipient_name": "فاطمة أحمد"}))
check("the creative director is OFFERED the fill tool and its call comes back as a finished PLAN reply (nothing created)",
      "fill_adobe_template" in seen[0] and r.startswith("[VERBATIM:fill_adobe_template]\n# Template fill plan") and "Nothing has been created yet" in r and "reply **yes**" in r and n_runs() == runs0)
plan_reply = r.split("]\n", 1)[1]
r, seen = run(decide("creative_director", "fill_adobe_template", {"plan_id": json.loads(T.call_tool("fill_adobe_template", {"template_id": "t_ar", "recipient_name": "x"}))["plan_id"]}))
check("a model that sends a plan_id in its tool call cannot run anything: the argument is dropped and no run folder appears", n_runs() == runs0 and (r is None or "Filled copy" not in r))
out = json.loads(T.call_tool("fill_adobe_template", {"plan_id": "PLAN-20261005120000-abcdef", "recipient_name": "Ali", "template_id": "t_en"}))
check("call_tool reports the dropped plan_id and still only plans", out.get("ignored_args") == ["plan_id"] and out["stage"] == "plan" and n_runs() == runs0)
r, seen = run(decide("uiux_engineer", "fill_adobe_template", {"template_id": "t_ar", "fields": {"recipient_name": "x"}}))
check("a fill call by the UI/UX engineer is dropped (not offered, not run)", "fill_adobe_template" not in seen[0] and r is None)

print("\n-- the human gate: only a person's own yes / no runs or cancels a plan --")
PLANTXT = plan_reply
hist = lambda *msgs: [{"role": role, "content": c} for role, c in msgs]
pid_in = re.search(r"\((PLAN-\d{14}-[0-9a-f]{6})\)", PLANTXT).group(1)
H = hist(("user", "make a certificate"), ("assistant", PLANTXT))
yes_words = ["yes", "Yes!", "ok", "OK.", "okay", "go ahead", "yes please", "sure", "confirm", "نعم", "تمام", "أيوه", "موافق", "نفذ"]
no_words = ["no", "No.", "cancel", "stop", "لا", "إلغاء", "الغاء"]
check("a bare yes (English or Arabic, with light punctuation) right after a plan is recognised", all(TE.pending_plan(H, w) == ("yes", pid_in) for w in yes_words), str([w for w in yes_words if TE.pending_plan(H, w) != ("yes", pid_in)]))
check("a bare no is recognised as a cancel", all(TE.pending_plan(H, w) == ("no", pid_in) for w in no_words), str([w for w in no_words if TE.pending_plan(H, w) != ("no", pid_in)]))
check("anything else is NOT an answer: a longer message, a change request, a question, a different request",
      all(TE.pending_plan(H, w) is None for w in ["yes but change the name to Ali", "yes and also make another one", "maybe", "what does it cost?", "make a certificate for John", "yes " + "x" * 40, "", "no thanks, make it Arabic", "نعم وغير الاسم"]))
check("a yes needs a plan to answer: no history, empty history, or no plan in the last assistant message", all(TE.pending_plan(h, "yes") is None for h in (None, [], hist(("user", "hi")), hist(("assistant", "hello there")))))
check("only the LAST assistant message counts (an older plan buried under later chat is not answerable)", TE.pending_plan(hist(("assistant", PLANTXT), ("user", "thanks"), ("assistant", "you're welcome")), "yes") is None)
check("a finished-run report (which also contains a plan id) is not a pending plan", TE.pending_plan(hist(("assistant", T.verbatim_text("fill_adobe_template", T.fill_adobe_template(plan_id=pid_in)))), "yes") is None)
H = hist(("assistant", PLANTXT))                                              # (the plan was just used by the line above; make a fresh one)
fresh_plan_reply = T.verbatim_text("fill_adobe_template", plan())
H = hist(("user", "make it"), ("assistant", fresh_plan_reply))
audits = []
async def decide_h(agent, msg, history):
    seen = []
    with mock.patch.object(httpx, "AsyncClient", fake("list_adobe_templates", {}, seen)), mock.patch("david_router.audit", audits.append):
        return await TE.decide_and_run_tool(agent, "general", msg, history=history), seen
runs0 = n_runs()
r, seen = run(decide_h("creative_director", "yes", H))
check("a yes from the user runs the plan WITHOUT any model call, and the reply is the finished report",
      not seen and r.startswith("[VERBATIM:fill_adobe_template]\n# Filled copy and preview created") and "Original file unchanged: **yes**" in r and n_runs() == runs0 + 1)
check("the confirmation is audited with the plan id", any(a.get("action") == "plan_confirmed" and a.get("plan_id") for a in audits))
r, seen = run(decide_h("creative_director", "yes", H))
check("the same yes again does nothing new (a plan runs once)", n_runs() == runs0 + 1 and ("already used" in (r or "")) )
H2 = hist(("assistant", T.verbatim_text("fill_adobe_template", plan())))
runs1 = n_runs()
r, seen = run(decide_h("creative_director", "no", H2))
check("a no cancels: nothing is created, the reply says so, and the plan can never run afterwards",
      r.startswith("[VERBATIM:fill_adobe_template]\n# Plan cancelled") and n_runs() == runs1 and any(a.get("action") == "plan_cancelled" for a in audits)
      and "already used" in T.fill_adobe_template(plan_id=re.search(r"\((PLAN-\d{14}-[0-9a-f]{6})\)", H2[0]["content"]).group(1))["errors"][0])
H3 = hist(("assistant", T.verbatim_text("fill_adobe_template", plan())))
for who in ("uiux_engineer", "analyst", "chief_of_staff"):
    n = n_runs(); r, seen = run(decide_h(who, "yes", H3))
    check(f"a yes is ignored for {who}, who may not run Adobe plans (and nothing is created)", n_runs() == n and not (r or "").startswith("[VERBATIM:fill_adobe_template]"))
src_exec = inspect.getsource(TE.decide_and_run_tool)
check("the handler calls the plan runner directly (never through the model-facing path) and checks the agent may fill", "adobe_template_tools.fill_adobe_template(plan_id=plan_id)" in src_exec and 'tool_allowed(agent, domain, "fill_adobe_template")' in src_exec)
check("the server passes the chat history to the tool layer", "history=history)" in src("ui/server.py") and "decide_and_run_tool(agent, domain, msg, request_id=req_id, history=history)" in src("ui/server.py"))
r, seen = run(decide("analyst", "list_adobe_templates", {}))
check("an agent without Adobe access has the tool dropped too", "list_adobe_templates" not in seen[0] and r is None)
r, seen = run(decide("uiux_engineer", "review_adobe_preview", {"preview": os.path.join(TMP, "outside.png")}))
check("a review of a file outside the generated folder comes back as a finished error reply", r.startswith("[VERBATIM:review_adobe_preview]\n# Preview review: failed") and "inside" in r)

# ═══ the keyword shortcut is gone ═════════════════════════════════════════════
print("\n-- the Adobe keyword shortcut is removed from ui/server.py --")
sv = src("ui/server.py")
sys.path.insert(0, str(DAVID_HOME / "ui"))
import server as srv
check("the WebSocket Adobe branch and its 'layer0-adobe' reply are gone", "layer0-adobe" not in sv and "_adobe_req" not in sv and 'bridge_type == "adobe"' not in sv)
check("the shortcut no longer matches any Adobe app name",
      not any(srv._L0_DESIGN.search(m) for m in ("create a poster in photoshop", "make a logo in illustrator", "design a brochure in indesign", "create a title in after effects", "make a cut in premiere", "photoshop poster design")))
check("...and the app-name parser has no Adobe entries", all(srv._parse_design_request(m) is None for m in ("photoshop", "illustrator", "indesign", "after effects", "premiere pro", "audition")) and not [k for k, v in srv._DESIGN_APP_MAP.items() if v[0] == "adobe"])
check("Office and Ableton keep their shortcut", bool(srv._L0_DESIGN.search("create a word document")) and bool(srv._L0_DESIGN.search("make a beat in ableton")) and srv._parse_design_request("make an excel sheet") == ("office", "excel"))
check("the shortcut's explanation points to the template tools", "adobe_template_tools.py" in sv and "creative_director / uiux_engineer" in sv)
check("the Studio page's manual /api/adobe button endpoint was left alone (it is not a keyword shortcut)", '@app.post("/api/adobe")' in sv)

# ═══ the role files ═════════════════════════════════════════════════════════
print("\n-- the Job 2 role files and JOB.md --")
cd, ux = A.load_spec("creative_director"), A.load_spec("uiux_engineer")
check("both agents are migrated, with the intended tool lists",
      cd and ux and set(cd.tools_allow) == {"query_knowledge", "read_note", "search_web", "list_adobe_templates", "fill_adobe_template", "export_adobe_preview"}
      and set(ux.tools_allow) == {"query_knowledge", "read_note", "list_adobe_templates", "export_adobe_preview", "review_adobe_preview"})
for a in ("creative_director", "uiux_engineer"):
    check(f"{a}: the original identity is kept word for word (war rooms and everyday use are unchanged)", david_router.AGENT_PROMPTS[a] in A.build_system_prompt(a))
    check(f"{a}: shared behaviour and safety are included, examples only on request", "Shared behaviour" in A.build_system_prompt(a) and "Safety rules" in A.build_system_prompt(a) and "examples" in A.build_system_prompt(a, examples=True).lower() and len(A.build_system_prompt(a, examples=True)) > len(A.build_system_prompt(a)) + 1500)
cr, ur = src("agents/creative_director/role.md"), src("agents/uiux_engineer/role.md")
check("creative_director: only registered templates, a plan only, a person's yes runs it (not the model), exact names, no invention",
      "registered templates" in cr and "You cannot run a plan" in cr and "exactly as the user wrote it" in cr and "do not improvise" in cr and "Never tell the user a file was made before the system reports it" in cr)
check("creative_director: Arabic into an Arabic template only; never sets fonts or direction; brochures not ready",
      "can never go into an English template" in cr and "never set fonts, direction or a text engine" in cr and "**not ready**" in cr)
check("creative_director: cannot touch originals, send, print, approve, or claim a file that was not made", "cannot open, change or overwrite an original" in cr and "cannot approve" in cr and "Never say a file was made unless" in cr)
check("uiux_engineer: reviews with the tool, looks_ok is not approval, template defects are not blamed on the fill, never says final",
      "`review_adobe_preview`" in ur and "`looks_ok` is not approval" in ur and "probably in the template itself" in ur and "Never call a certificate approved" in ur)
check("uiux_engineer: lists the eight checks", "joined" in ur and "right to left" in ur and "empty boxes" in ur and "cut off" in ur and "old placeholder" in ur)
check("both example files are labelled synthetic and show the plan-then-yes flow / the refusals", "synthetic" in src("agents/creative_director/examples.md").lower() and "a person said yes" in src("agents/creative_director/examples.md")
      and "synthetic" in src("agents/uiux_engineer/examples.md").lower() and "original template" in src("agents/uiux_engineer/examples.md"))
job = src("agents/jobs/02_brand_marketing/JOB.md")
order = ["| 1 | **Request**", "| 2 | **Creative Director fills the template**", "| 3 | **Export Preview**", "| 4 | **UI/UX Engineer reviews**", "| 5 | **Human approval**"]
check("JOB.md has the five stages in order", all(x in job for x in order) and [job.index(x) for x in order] == sorted(job.index(x) for x in order))
check("JOB.md: originals untouched, preview first with a human gate in code, exclusive tools, shortcut removed",
      "never touched" in job and "Preview first, and a human gate in code" in job and "has no way to run one" in job and "Exclusive tools" in job and "keyword shortcut is gone" in job)
check("JOB.md has a section on how Arabic is handled and an honest 'what does not exist'", "How Arabic text is handled" in job and "Not ready" in job and "Not fillable" in job and "Known template defects" in job)
check("the agents README lists creative_director and uiux_engineer as migrated", "creative_director" in src("agents/README.md") and "uiux_engineer" in src("agents/README.md"))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
