"""
adobe_template_tools.py -- Job 2 (brand and marketing): fill the branch's EXISTING Illustrator templates, preview first.

Four agent tools (scoped to creative_director / uiux_engineer, see agent_spec.EXCLUSIVE_TOOLS):

  list_adobe_templates    the registered templates and the fields each one has                    (read-only, no app)
  fill_adobe_template     PLAN first (no app, nothing created); with the plan_id: make a COPY, fill it, save the copy,
                          export a PNG preview                                                      (creative_director)
  export_adobe_preview    re-export a preview of a file this tool generated                          (both)
  review_adobe_preview    qwen3.5 vision checks an exported preview (Arabic layout, clipping...)     (uiux_engineer)

Safety, enforced here in code and not left to a model:
  * an ORIGINAL template is never opened by Illustrator and never written: Python copies it, the app opens only the copy,
    and the original's SHA-256 is compared before and after every run;
  * every output goes into a brand-new folder under <DAVID_HOME>\\generated\\brand\\ and no existing file is ever overwritten;
  * export/review only accept files inside that folder, so an original can never be passed in;
  * fields are found by the template's own placeholder text (the real files have no named frames, and their order differs
    from file to file), matched exactly, and only when exactly ONE frame matches;
  * nothing is created until a second call presents the plan_id of an unexpired plan (the tool-decision layer makes one
    call per turn, so the second call is a separate turn: the user's go-ahead);
  * nothing is sent, printed, published or approved. A person approves.

Arabic: the tool never sets a font, direction or text engine. It replaces the contents of the template's own frame, so the
frame keeps the template's Arabic font, size, alignment and shaping. It normalises the text (presentation forms, bidi override
characters), refuses Arabic letters in a frame whose font is declared Latin-only, reads the text back from the document, notes
a changed font or a frame that grew wider, and exports a preview for the vision review.
"""

from david_config import DAVID_DATA, DAVID_HOME
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata

GENERATED_ROOT = str(DAVID_DATA / "generated" / "brand")
REGISTRY_PATH = str(DAVID_HOME / "agents" / "jobs" / "02_brand_marketing" / "reference" / "templates.json")
PLAN_TTL_SECONDS = 30 * 60
PREVIEW_MAX_SIDE = 1600
COM_TIMEOUT_SECONDS = 240
FOOTER = "Prepared for review. Nothing has been sent, printed, published or approved. The original templates were not changed."
PLAN_ID = re.compile(r"^PLAN-\d{14}-[0-9a-f]{6}$")
_LOCK = threading.Lock()           # one Illustrator job at a time

_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN = re.compile(r"[A-Za-z]")
_PRESENTATION = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
_BIDI_CONTROLS = re.compile(r"[\u202A-\u202E\u2066-\u2069]")        # embeddings, overrides, isolates: used to spoof text order
_INVISIBLE = re.compile(r"[\u200B\uFEFF\u2060]")                      # zero-width space, BOM, word joiner (ZWJ/ZWNJ are KEPT: Arabic needs them)
_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_TO_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TO_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def flag(code, severity, message):
    return {"code": code, "severity": severity, "message": message}


def _fail(tool, errors, **extra):
    return {"tool": tool, "ok": False, "errors": errors, **extra}


# ── paths and hashes ─────────────────────────────────────────────────────────

def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside(path, root):
    """True if `path` is `root` or sits under it (symlinks and .. resolved)."""
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except ValueError:
        return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _unique_dir(base_name):
    os.makedirs(GENERATED_ROOT, exist_ok=True)
    for n in range(1, 100):
        d = os.path.join(GENERATED_ROOT, base_name if n == 1 else f"{base_name}-{n}")
        try:
            os.makedirs(d, exist_ok=False)          # exist_ok=False: a folder that exists is never reused
            return d
        except FileExistsError:
            continue
    raise RuntimeError("could not create a new output folder")


def _free_name(folder, stem, ext):
    """A file name that does not exist yet (never overwrite)."""
    for n in range(1, 1000):
        p = os.path.join(folder, f"{stem}{'' if n == 1 else f'-{n}'}{ext}")
        if not os.path.exists(p):
            return p
    raise RuntimeError("could not find a free file name")


# ── the registry ─────────────────────────────────────────────────────────────

def load_registry(path=None):
    with open(path or REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _template_source(reg, tpl):
    root = reg["template_root"]
    if not os.path.isabs(root):          # a relative root is relative to the repository (DAVID_HOME)
        root = str(DAVID_HOME / root)
    return os.path.join(root, tpl["source"])


def list_adobe_templates():
    try:
        reg = load_registry()
    except (OSError, ValueError) as e:
        return _fail("list_adobe_templates", [f"the template registry could not be read: {e}"])
    ready = []
    for tid, t in reg["templates"].items():
        ready.append({"template_id": tid, "title": t["title"], "kind": t["kind"], "app": t["app"], "language": t["language"],
                      "fields": [{"name": n, "label": f["label"], "currently": f.get("anchor_text") or f.get("frame_name"),
                                  "script": f["script"], "max_chars": f["max_chars"], "digits": f.get("digits", "keep"),
                                  "case": f.get("case", "keep")} for n, f in t["fields"].items()]})
    return {"tool": "list_adobe_templates", "ok": True, "templates": ready, "not_ready": reg.get("not_ready", [])}


# ── field values ─────────────────────────────────────────────────────────────

def normalize_field_value(value, field):
    """(clean text, flags). A BLOCKER flag means the value must not be used."""
    flags = []
    if value is None or isinstance(value, (dict, list, bool)):
        return None, [flag("VALUE_MISSING", "BLOCKER", "no text was given for this field")]
    text = str(value)
    if _BIDI_CONTROLS.search(text):
        text = _BIDI_CONTROLS.sub("", text)
        flags.append(flag("BIDI_CONTROLS_REMOVED", "CHECK", "invisible text-direction override characters were removed (they can reorder text)"))
    if _INVISIBLE.search(text):
        text = _INVISIBLE.sub("", text)
        flags.append(flag("INVISIBLE_REMOVED", "NOTE", "zero-width characters were removed"))
    if _CONTROL.search(text):
        text = _CONTROL.sub("", text)
        flags.append(flag("CONTROL_REMOVED", "NOTE", "control characters were removed"))
    if _PRESENTATION.search(text):
        text = unicodedata.normalize("NFKC", text)
        flags.append(flag("PRESENTATION_FORMS_NORMALIZED", "CHECK", "pre-shaped Arabic letter forms (usually from pasted PDF text) were converted to normal letters so the font can shape them"))
    else:
        text = unicodedata.normalize("NFC", text)
    if re.search(r"[\r\n\u2028\u2029]", text):
        text = re.sub(r"\s+", " ", text)
        flags.append(flag("LINE_BREAKS_REPLACED", "NOTE", "line breaks were replaced by spaces (this field is one line)"))
    text = re.sub(r"[ \t]+", " ", text).strip()
    digits = field.get("digits", "keep")
    if digits == "western":
        text = text.translate(_TO_WESTERN)
    elif digits == "arabic_indic":
        text = text.translate(_TO_ARABIC_INDIC)
    if field.get("case") == "upper":
        text = text.upper()
    if not text:
        return None, flags + [flag("VALUE_EMPTY", "BLOCKER", "the text is empty after cleaning")]
    if len(text) > field["max_chars"]:
        flags.append(flag("TOO_LONG", "BLOCKER", f"{len(text)} characters; this field holds at most {field['max_chars']}"))
    has_ar, has_lat = bool(_ARABIC.search(text)), bool(_LATIN.search(text))
    script = field["script"]
    if script == "latin" and has_ar:
        flags.append(flag("ARABIC_IN_LATIN_FIELD", "BLOCKER", f"this frame uses a Latin-only font ({field.get('font', 'unknown')}); Arabic letters would show as empty boxes or in a substitute font. Use an Arabic template."))
    elif script == "arabic" and has_lat and not has_ar:
        flags.append(flag("LATIN_IN_ARABIC_FIELD", "CHECK", "Latin text in a frame set up for Arabic: confirm how it looks in the preview"))
    if has_ar and has_lat:
        flags.append(flag("MIXED_SCRIPTS", "CHECK", "Arabic and Latin text together: the order of the words must be checked in the preview"))
    return text, flags


# ── JSX (generated by code, never by a model) ────────────────────────────────

_JSX_HELPERS = r'''
function esc(s){ s=String(s); var o=""; for (var i=0;i<s.length;i++){ var c=s.charCodeAt(i);
  if (c==34) o+='\\"'; else if (c==92) o+='\\\\'; else if (c<32||c>126) o+='\\u'+('0000'+c.toString(16)).slice(-4); else o+=s.charAt(i);} return o; }
function q(s){ return '"'+esc(s)+'"'; }
function trim(s){ return String(s).replace(/^\s+|\s+$/g, ""); }
function num(x){ return (isNaN(x) || x === null) ? "null" : String(Math.round(x*100)/100); }
'''

def _js(value):
    """A JavaScript literal for a Python value. ensure_ascii=True makes it pure ASCII (\\uXXXX escapes), so no quote, backslash,
    line break or non-ASCII character in a field value can break out of the string."""
    return json.dumps(value, ensure_ascii=True)


def build_fill_jsx(work_path, png_path, fields, max_side=PREVIEW_MAX_SIDE):
    """The JSX that opens the COPY, replaces the contents of each matched frame, reads it back, saves the copy and exports a
    preview. `fields` = [{"name", "anchor", "frame_name", "value"}]."""
    return (_JSX_HELPERS + r'''
function exportPng(doc, pngPath, maxSide) {
  var ab = doc.artboards[doc.artboards.getActiveArtboardIndex()].artboardRect;
  var w = Math.abs(ab[2]-ab[0]), h = Math.abs(ab[1]-ab[3]);
  var scale = Math.min(400, Math.max(25, 100 * maxSide / Math.max(w, h)));
  var o = new ExportOptionsPNG24();
  o.antiAliasing = true; o.artBoardClipping = true; o.transparency = false; o.horizontalScale = scale; o.verticalScale = scale;
  doc.exportFile(new File(pngPath), ExportType.PNG24, o);
  return {w: w, h: h, scale: scale};
}
var WORK = ''' + _js(work_path.replace("\\", "/")) + r''';
var PNG = ''' + _js(png_path.replace("\\", "/")) + r''';
var FIELDS = ''' + _js(fields) + r''';
var MAXSIDE = ''' + str(int(max_side)) + r''';
var prevLevel = app.userInteractionLevel;
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var out = [], ok = true, saved = false, shot = null, err = "";
var doc = app.open(new File(WORK));
try {
  var ab0 = doc.artboards[doc.artboards.getActiveArtboardIndex()].artboardRect;
  for (var i = 0; i < FIELDS.length; i++) {
    var F = FIELDS[i], hits = [];
    for (var j = 0; j < doc.textFrames.length; j++) {
      var tf = doc.textFrames[j], c = "";
      try { c = tf.contents; } catch (e) { continue; }
      if (F.frame_name) { if (tf.name == F.frame_name) hits.push(tf); }
      else if (trim(c) == F.anchor) hits.push(tf);
    }
    if (hits.length != 1) { ok = false; out.push('{"name":'+q(F.name)+',"status":"anchor_matches_'+hits.length+'"}'); continue; }
    var t = hits[0], b0 = t.visibleBounds, f0 = "?", s0 = 0;
    try { f0 = t.textRange.characterAttributes.textFont.name; } catch (e) {}
    try { s0 = t.textRange.characterAttributes.size; } catch (e) {}
    t.contents = F.value;
    var b1 = t.visibleBounds, f1 = "?", s1 = 0, back = "";
    try { f1 = t.textRange.characterAttributes.textFont.name; } catch (e) {}
    try { s1 = t.textRange.characterAttributes.size; } catch (e) {}
    try { back = t.contents; } catch (e) {}
    out.push('{"name":'+q(F.name)+',"status":"filled","readback":'+q(back)+',"font_before":'+q(f0)+',"font_after":'+q(f1)+',"size_before":'+num(s0)+',"size_after":'+num(s1)+
      ',"width_before":'+num(b0[2]-b0[0])+',"width_after":'+num(b1[2]-b1[0])+',"left_after":'+num(b1[0])+',"right_after":'+num(b1[2])+'}');
  }
  if (ok) { doc.save(); saved = true; shot = exportPng(doc, PNG, MAXSIDE); }
} catch (e) { ok = false; err = e.message; }
finally { doc.close(SaveOptions.DONOTSAVECHANGES); app.userInteractionLevel = prevLevel; }
var ab = ab0;
'{"ok":'+(ok&&saved)+',"saved":'+saved+',"error":'+q(err)+',"fields":['+out.join(",")+'],"artboard":['+num(ab[0])+','+num(ab[1])+','+num(ab[2])+','+num(ab[3])+'],"preview":'+(shot?('{"w":'+num(shot.w)+',"h":'+num(shot.h)+',"scale":'+num(shot.scale)+'}'):'null')+'}';
''')


def build_export_jsx(file_path, png_path, max_side=PREVIEW_MAX_SIDE):
    return (_JSX_HELPERS + r'''
var FILE = ''' + _js(file_path.replace("\\", "/")) + r''';
var PNG = ''' + _js(png_path.replace("\\", "/")) + r''';
var prevLevel = app.userInteractionLevel;
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var ok = false, err = "", w = 0, h = 0, scale = 0;
var doc = app.open(new File(FILE));
try {
  var ab = doc.artboards[doc.artboards.getActiveArtboardIndex()].artboardRect;
  w = Math.abs(ab[2]-ab[0]); h = Math.abs(ab[1]-ab[3]);
  scale = Math.min(400, Math.max(25, 100 * ''' + str(int(max_side)) + r''' / Math.max(w, h)));
  var o = new ExportOptionsPNG24();
  o.antiAliasing = true; o.artBoardClipping = true; o.transparency = false; o.horizontalScale = scale; o.verticalScale = scale;
  doc.exportFile(new File(PNG), ExportType.PNG24, o);
  ok = true;
} catch (e) { err = e.message; }
finally { doc.close(SaveOptions.DONOTSAVECHANGES); app.userInteractionLevel = prevLevel; }
'{"ok":'+ok+',"error":'+q(err)+',"preview":{"w":'+num(w)+',"h":'+num(h)+',"scale":'+num(scale)+'}}';
''')


# ── running JSX in Illustrator ───────────────────────────────────────────────

def _illustrator_running():
    import subprocess
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout.lower()
    return '"illustrator.exe"' in out


def _com_run(jsx_text, run_dir):
    """Run JSX in Illustrator over COM and return its result string. Launches Illustrator if it is closed and closes it again
    afterwards if this call launched it."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    jsx_path = os.path.join(run_dir, f"_job_{secrets.token_hex(3)}.jsx")
    try:
        was_running = _illustrator_running()
        ill = win32com.client.Dispatch("Illustrator.Application")
        with open(jsx_path, "w", encoding="utf-8") as f:
            f.write(jsx_text)
        result = ill.DoJavaScriptFile(jsx_path)
        if not was_running:
            try:
                ill.Quit()
            except Exception:
                pass
        return str(result)
    finally:
        try:
            os.remove(jsx_path)
        except OSError:
            pass
        pythoncom.CoUninitialize()


RUNNER = _com_run          # tests replace this


def _run_with_timeout(jsx_text, run_dir):
    box = {}

    def target():
        try:
            box["out"] = RUNNER(jsx_text, run_dir)
        except Exception as e:                                # noqa: BLE001
            box["err"] = f"{type(e).__name__}: {str(e)[:300]}"
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(COM_TIMEOUT_SECONDS)
    if th.is_alive():
        return None, "Illustrator did not answer in time (a dialog may be waiting on screen). Nothing was saved."
    return box.get("out"), box.get("err")


# ── plans ────────────────────────────────────────────────────────────────────

def _plans_dir():
    d = os.path.join(GENERATED_ROOT, "plans")
    os.makedirs(d, exist_ok=True)
    return d


def _now():
    return datetime.datetime.now()


def _choose_template(reg, fields):
    """The template for a request that did not name one: by the alphabet of the text (Arabic letters -> a template whose field
    font is Arabic; otherwise a Latin one). Returns (template_id or None, error or None, candidates)."""
    joined = " ".join(str(v) for v in fields.values() if v is not None)
    want = "arabic" if _ARABIC.search(joined) else "latin" if _LATIN.search(joined) else None
    if want is None:
        return None, "I could not tell which certificate to use. Say which template (see list_adobe_templates).", []
    cands = [tid for tid, t in reg["templates"].items() if all(k in t["fields"] and t["fields"][k]["script"] == want for k in fields)]
    if len(cands) == 1:
        return cands[0], None, cands
    if not cands:
        return None, f"no registered template takes {'Arabic' if want == 'arabic' else 'Latin'} text for those fields.", []
    names = "; ".join(f"{c} ({reg['templates'][c]['title']})" for c in cands)
    return None, f"several certificates fit. Say which one: {names}.", cands


def plan_fill(template_id, fields):
    """Validate everything and store a plan. Launches nothing and creates only the plan record."""
    tool = "fill_adobe_template"
    try:
        reg = load_registry()
    except (OSError, ValueError) as e:
        return _fail(tool, [f"the template registry could not be read: {e}"])
    chosen_note = None
    if not template_id and isinstance(fields, dict) and fields:
        template_id, why, _cands = _choose_template(reg, fields)
        if why:
            return _fail(tool, [why])
        chosen_note = flag("TEMPLATE_CHOSEN", "NOTE", f"no template was named, so {template_id} was chosen from the alphabet of the text")
    tpl = reg["templates"].get(template_id)
    if tpl is None:
        known = ", ".join(reg["templates"]) or "none"
        return _fail(tool, [f"unknown template_id {template_id!r}. Registered templates: {known}. Brochures and posters are not registered yet."])
    if not isinstance(fields, dict) or not fields:
        return _fail(tool, ["give the fields to fill as {field_name: text}. Fields of this template: " + ", ".join(tpl["fields"])])
    source = _template_source(reg, tpl)
    if _inside(source, GENERATED_ROOT):
        return _fail(tool, ["the registered template path points into the generated folder; refusing"])
    if not os.path.isfile(source):
        return _fail(tool, [f"the template file is missing: {source}"])
    flags, rows, items = [], [], []
    unknown = [k for k in fields if k not in tpl["fields"]]
    if unknown:
        return _fail(tool, [f"unknown field(s) {', '.join(unknown)}. Fields of this template: {', '.join(tpl['fields'])}"])
    for name, spec in tpl["fields"].items():
        if name not in fields:
            continue
        clean, f = normalize_field_value(fields[name], spec)
        flags += [dict(x, field=name) for x in f]
        rows.append({"field": name, "label": spec["label"], "currently": spec.get("anchor_text") or spec.get("frame_name"), "new": clean})
        if clean is not None:
            items.append({"name": name, "anchor": spec.get("anchor_text"), "frame_name": spec.get("frame_name"), "value": clean})
    current = sha256_file(source)
    if tpl.get("sha256") and tpl["sha256"] != current:
        flags.append(flag("TEMPLATE_CHANGED", "CHECK", "the template file has changed since it was registered; the placeholder text is re-checked when it is opened"))
    if chosen_note:
        flags.append(chosen_note)
    blockers = [f for f in flags if f["severity"] == "BLOCKER"]
    out = {"tool": tool, "ok": not blockers, "stage": "plan", "template_id": template_id, "title": tpl["title"], "app": tpl["app"],
           "fields": rows, "flags": flags, "template_sha256": current}
    if blockers:
        out["errors"] = [f"{f.get('field', '')}: {f['message']}" for f in blockers]
        return out
    plan_id = f"PLAN-{_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    record = {"plan_id": plan_id, "created": _now().isoformat(timespec="seconds"), "created_ns": time.time_ns(), "template_id": template_id, "items": items,
              "template_sha256": current, "used": False}
    with open(os.path.join(_plans_dir(), plan_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    out.update(plan_id=plan_id, expires_minutes=PLAN_TTL_SECONDS // 60)
    return out


def _load_plan(plan_id):
    if not isinstance(plan_id, str) or not PLAN_ID.match(plan_id):
        return None, "plan_id must look like PLAN-20261005120000-a1b2c3"
    path = os.path.join(_plans_dir(), plan_id + ".json")
    if not os.path.isfile(path):
        return None, f"no plan {plan_id}. Ask for a new plan first."
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if rec.get("used"):
        return None, f"{plan_id} was already used. Ask for a new plan."
    age = (_now() - datetime.datetime.fromisoformat(rec["created"])).total_seconds()
    if age > PLAN_TTL_SECONDS:
        return None, f"{plan_id} expired ({int(age // 60)} minutes old). Ask for a new plan."
    return (rec, path), None


# ── fill (execute a plan) ────────────────────────────────────────────────────

def _post_checks(items, jsx_fields):
    flags = []
    by = {f["name"]: f for f in jsx_fields}
    for it in items:
        r = by.get(it["name"])
        if not r or r.get("status") != "filled":
            flags.append(flag("FIELD_NOT_FOUND", "BLOCKER", f"{it['name']}: " + (f"{r['status'].replace('anchor_matches_', 'the placeholder text matched ')} frame(s); exactly one is required" if r else "not processed")))
            continue
        if r["readback"] != it["value"]:
            flags.append(flag("READBACK_DIFFERS", "BLOCKER", f"{it['name']}: the text in the document is not the text that was sent"))
        if r["font_before"] != r["font_after"]:
            flags.append(flag("FONT_CHANGED", "CHECK", f"{it['name']}: the font changed from {r['font_before']} to {r['font_after']}"))
        if r["width_before"] and r["width_after"] > r["width_before"] * 1.25:
            flags.append(flag("FIELD_WIDER", "CHECK", f"{it['name']}: the text is {r['width_after'] / r['width_before']:.0%} as wide as the placeholder; check it fits"))
    return flags


def fill_adobe_template(template_id=None, fields=None, plan_id=None, recipient_name=None):
    """Without plan_id: a PLAN (nothing launched, nothing created but the plan record). With plan_id: do exactly that plan.
    The agents' tool schema has NO plan_id: only the HUD's confirmation handler (tool_executor.pending_plan, after a person's
    own "yes") ever runs a plan. recipient_name is a flat shortcut for fields={"recipient_name": ...}."""
    tool = "fill_adobe_template"
    if not plan_id:
        if recipient_name is not None and not fields:
            fields = {"recipient_name": recipient_name}
        return plan_fill(template_id, fields)
    loaded, err = _load_plan(plan_id)
    if err:
        return _fail(tool, [err])
    rec, plan_path = loaded
    reg = load_registry()
    tpl = reg["templates"].get(rec["template_id"])
    if tpl is None:
        return _fail(tool, ["the template is no longer registered"])
    source = _template_source(reg, tpl)
    if not os.path.isfile(source):
        return _fail(tool, [f"the template file is missing: {source}"])
    if not _LOCK.acquire(blocking=False):
        return _fail(tool, ["another Adobe job is running; try again in a minute"])
    try:
        before = sha256_file(source)
        run_dir = _unique_dir(f"{_now().strftime('%Y%m%d-%H%M%S')}-{rec['template_id']}")
        work = os.path.join(run_dir, rec["template_id"] + os.path.splitext(source)[1].lower())
        png = _free_name(run_dir, rec["template_id"] + "__preview", ".png")
        assert not _inside(work, os.path.dirname(source)) and _inside(work, GENERATED_ROOT) and _norm(work) != _norm(source)
        shutil.copy2(source, work)                                  # the app only ever sees the copy
        jsx = build_fill_jsx(work, png, rec["items"])
        raw, run_err = _run_with_timeout(jsx, run_dir)
        after = sha256_file(source)
        result = {"tool": tool, "stage": "executed", "plan_id": rec["plan_id"], "template_id": rec["template_id"], "title": tpl["title"],
                  "run_folder": run_dir, "filled_copy": work, "preview_png": None, "original_path": source,
                  "original_sha256": before, "original_unchanged": before == after, "flags": []}
        if before != after:
            result["flags"].append(flag("ORIGINAL_CHANGED", "BLOCKER", "the original template's hash changed during the run. This must not happen; stop and check the file."))
        data = None
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                run_err = run_err or f"Illustrator returned something unreadable: {raw[:120]!r}"
        if data is None:
            result.update(ok=False, errors=[run_err or "Illustrator returned nothing"])
        else:
            result["fields"] = data.get("fields", [])
            result["flags"] += _post_checks(rec["items"], result["fields"])
            if data.get("ok") and os.path.isfile(png) and os.path.getsize(png) > 0:
                result["preview_png"] = png
            else:
                result["flags"].append(flag("NO_PREVIEW", "BLOCKER", data.get("error") or "no preview picture was produced"))
            if data.get("preview"):
                result["preview_size_px"] = [round(data["preview"]["w"] * data["preview"]["scale"] / 100), round(data["preview"]["h"] * data["preview"]["scale"] / 100)]
            result["ok"] = not [f for f in result["flags"] if f["severity"] == "BLOCKER"]
        rec["used"] = True
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        return result
    finally:
        _LOCK.release()


def cancel_plan(plan_id):
    """A person said no: the plan can never run."""
    loaded, err = _load_plan(plan_id)
    if err:
        return {"tool": "cancel_plan", "ok": False, "errors": [err]}
    rec, path = loaded
    rec["used"], rec["cancelled"] = True, True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return {"tool": "cancel_plan", "ok": True, "plan_id": plan_id}


# ── preview export ───────────────────────────────────────────────────────────

def export_adobe_preview(file=None):
    tool = "export_adobe_preview"
    if not file or not isinstance(file, str):
        return _fail(tool, ["give the path of a file this tool generated (under " + GENERATED_ROOT + ")"])
    if not _inside(file, GENERATED_ROOT):
        return _fail(tool, [f"only files this tool generated can be previewed (inside {GENERATED_ROOT}); an original template is never opened"])
    if os.path.splitext(file)[1].lower() != ".ai" or not os.path.isfile(file):
        return _fail(tool, ["the file must be an existing generated .ai file"])
    if not _LOCK.acquire(blocking=False):
        return _fail(tool, ["another Adobe job is running; try again in a minute"])
    try:
        folder = os.path.dirname(os.path.realpath(file))
        png = _free_name(folder, os.path.splitext(os.path.basename(file))[0] + "__preview", ".png")
        before = sha256_file(file)
        raw, run_err = _run_with_timeout(build_export_jsx(os.path.realpath(file), png), folder)
        unchanged = sha256_file(file) == before
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        ok = bool(data and data.get("ok") and os.path.isfile(png) and os.path.getsize(png) > 0)
        out = {"tool": tool, "ok": ok and unchanged, "file": file, "preview_png": png if ok else None, "file_unchanged": unchanged, "flags": []}
        if not ok:
            out["errors"] = [run_err or (data or {}).get("error") or "no preview picture was produced"]
        if not unchanged:
            out["flags"].append(flag("FILE_CHANGED", "BLOCKER", "the file changed during the export; this must not happen"))
        return out
    finally:
        _LOCK.release()


# ── preview review (vision) ──────────────────────────────────────────────────

REVIEW_CHECKS = [
    ("arabic_letters_connected", "Arabic words use joined letter forms (not separate, isolated letters)"),
    ("arabic_direction", "Arabic reads right to left, and any digits or Latin words inside it are in the right order"),
    ("no_missing_glyphs", "no empty boxes, question marks or substituted-looking letters"),
    ("no_overflow_or_clipping", "no text is cut off, runs outside the page or collides with other elements"),
    ("old_text_gone", "none of the OLD placeholder texts is still visible"),
    ("new_text_present", "each NEW text is visible and spelled as given"),
    ("alignment_and_spacing", "the new text is aligned and spaced like the surrounding design (e.g. centred where the design is centred)"),
    ("legibility", "the new text is easy to read against its background"),
]


def _image_stats(path):
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        small = im.resize((max(1, w // 8), max(1, h // 8)))
        var = sum(ImageStat.Stat(small).var) / 3
        return {"width": w, "height": h, "variance": round(var, 1), "blank": var < 5}


def _vision_json(png_path, prompt):
    import urllib.request
    import ollama_config
    from PIL import Image
    import io
    with Image.open(png_path) as im:
        im = im.convert("RGB")
        if max(im.size) > PREVIEW_MAX_SIDE:
            k = PREVIEW_MAX_SIDE / max(im.size)
            im = im.resize((round(im.size[0] * k), round(im.size[1] * k)))
        buf = io.BytesIO()
        im.save(buf, "PNG")
    body = {"model": "qwen3.5:9b", "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}],
            "options": {"temperature": 0, "num_ctx": ollama_config.num_ctx_for("qwen3.5:9b"), "num_predict": 1200}}
    req = urllib.request.Request(ollama_config.OLLAMA_CHAT_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        text = json.loads(r.read())["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def review_adobe_preview(preview=None, new_text=None, old_text=None, language=None):
    """Vision review of an exported preview. Advisory: it can say `looks_ok`, `needs_changes` or `needs_a_human_look`; it can
    never say approved."""
    tool = "review_adobe_preview"
    if not preview or not isinstance(preview, str) or not _inside(preview, GENERATED_ROOT) or not preview.lower().endswith(".png") or not os.path.isfile(preview):
        return _fail(tool, [f"give the path of a preview .png this tool generated (inside {GENERATED_ROOT})"])
    new_list = [str(x) for x in (new_text if isinstance(new_text, list) else [new_text]) if x]
    old_list = [str(x) for x in (old_text if isinstance(old_text, list) else [old_text]) if x]
    stats = _image_stats(preview)
    if stats["blank"]:
        return {"tool": tool, "ok": True, "verdict": "needs_changes", "stats": stats, "checks": [],
                "summary": "the preview is blank or one flat colour, so the template did not render"}
    prompt = ("You are checking a PREVIEW IMAGE of a certificate or brochure for a language institute. Look at the image and answer "
              "each check with exactly one of: pass, fail, unclear. Use unclear when you cannot tell. Do not guess.\n"
              + (f"OLD placeholder texts that must no longer be visible: {json.dumps(old_list, ensure_ascii=False)}\n" if old_list else "")
              + (f"NEW texts that must be visible, spelled exactly: {json.dumps(new_list, ensure_ascii=False)}\n" if new_list else "")
              + "Checks:\n" + "\n".join(f"- {cid}: {desc}" for cid, desc in REVIEW_CHECKS)
              + '\nReply with ONLY this JSON: {"checks": {"<check id>": {"result": "pass|fail|unclear", "note": "<short reason>"}}, '
                '"summary": "<one sentence>"}')
    try:
        data = _vision_json(preview, prompt)
    except Exception as e:                                         # noqa: BLE001
        return _fail(tool, [f"the vision model could not be reached: {type(e).__name__}: {str(e)[:120]}"], stats=stats)
    if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
        return {"tool": tool, "ok": True, "verdict": "needs_a_human_look", "stats": stats, "checks": [], "summary": "the review could not be read; look at the preview yourself"}
    checks = []
    for cid, desc in REVIEW_CHECKS:
        c = data["checks"].get(cid) or {}
        res = c.get("result") if c.get("result") in ("pass", "fail", "unclear") else "unclear"
        checks.append({"id": cid, "check": desc, "result": res, "note": str(c.get("note", ""))[:200]})
    results = [c["result"] for c in checks]
    verdict = "needs_changes" if "fail" in results else "needs_a_human_look" if "unclear" in results else "looks_ok"
    return {"tool": tool, "ok": True, "verdict": verdict, "advisory": True, "model": "qwen3.5:9b", "stats": stats,
            "checks": checks, "summary": str(data.get("summary", ""))[:300]}


# ── finished replies (the HUD shows these unchanged) ─────────────────────────

def _cell(t):
    return str(t if t is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _flags_table(flags):
    if not flags:
        return ["No findings."]
    order = {"BLOCKER": 0, "CHECK": 1, "NOTE": 2}
    rows = sorted(flags, key=lambda f: order.get(f["severity"], 3))
    return ["| Severity | Field | What |", "|---|---|---|"] + [f"| {f['severity']} | {_cell(f.get('field', ''))} | {_cell(f['message'])} |" for f in rows]


def plan_markdown(r):
    if not r.get("ok"):
        out = [f"# Template fill: not possible yet ({r.get('template_id') or 'no template'})", ""]
        out += [f"- BLOCKER: {e}" for e in r.get("errors", [])]
        if r.get("flags"):
            out += ["", *_flags_table(r["flags"])]
        return "\n".join(out + ["", FOOTER])
    out = [f"# Template fill plan: {r['template_id']} ({r['plan_id']})",
           f"Template: {r['title']} ({r['app']}). The original file is never opened or changed: a copy is filled.", "",
           "| Field | Now in the template | New text |", "|---|---|---|"]
    out += [f"| {_cell(x['label'])} | {_cell(x['currently'])} | {_cell(x['new'])} |" for x in r["fields"]]
    out += ["", *_flags_table(r["flags"]), "",
            f"Nothing has been created yet. To make the filled copy and its preview picture, reply **yes** (or **no** to cancel). "
            f"This is plan ({r['plan_id']}); it stays valid for {r['expires_minutes']} minutes.", "", FOOTER]
    return "\n".join(out)


def result_markdown(r):
    if not r.get("ok") and not r.get("filled_copy"):
        return "\n".join([f"# Template fill: failed ({r.get('plan_id', '')})", "", *[f"- BLOCKER: {e}" for e in r.get("errors", [])], "", FOOTER])
    head = "Filled copy and preview created" if r.get("ok") else "Filled copy NOT usable"
    out = [f"# {head}: {r['template_id']} ({r['plan_id']})", f"Template: {r['title']}",
           f"Original file unchanged: **{'yes' if r['original_unchanged'] else 'NO'}** (SHA-256 {r['original_sha256'][:12]}...)",
           f"Filled copy: {r['filled_copy']}", f"Preview picture: {r['preview_png'] or 'none'}"
           + (f" ({r['preview_size_px'][0]} x {r['preview_size_px'][1]} px)" if r.get("preview_size_px") else ""), ""]
    if r.get("fields"):
        out += ["| Field | Status | Text now in the document | Font | Width before > after |", "|---|---|---|---|---|"]
        for f in r["fields"]:
            out.append(f"| {_cell(f['name'])} | {f['status']} | {_cell(f.get('readback', ''))} | {_cell(f.get('font_after', ''))} | "
                       f"{f.get('width_before', '')} > {f.get('width_after', '')} |")
        out.append("")
    out += [*_flags_table(r["flags"]), "", "Next: the UI/UX engineer reviews the preview picture, then a person approves. Nothing is final until then.", "", FOOTER]
    return "\n".join(out)


def review_markdown(r):
    if not r.get("ok"):
        return "\n".join(["# Preview review: failed", "", *[f"- {e}" for e in r.get("errors", [])], "", FOOTER])
    out = [f"# Preview review: **{r['verdict']}**", "",
           "This is an automatic check by the vision model. It is advice, not approval: look at the picture yourself before it is used."]
    if r.get("summary"):
        out += ["", r["summary"]]
    if r.get("checks"):
        out += ["", "| Check | Result | Note |", "|---|---|---|"] + [f"| {_cell(c['check'])} | {c['result']} | {_cell(c['note'])} |" for c in r["checks"]]
    return "\n".join(out + ["", FOOTER])


def export_markdown(r):
    if not r.get("ok"):
        return "\n".join(["# Preview export: failed", "", *[f"- BLOCKER: {e}" for e in r.get("errors", [])], *( [""] + _flags_table(r["flags"]) if r.get("flags") else []), "", FOOTER])
    return "\n".join(["# Preview exported", f"File: {r['file']} (unchanged: {'yes' if r['file_unchanged'] else 'NO'})", f"Preview picture: {r['preview_png']}", "", FOOTER])


def list_markdown(r):
    if not r.get("ok"):
        return "\n".join(["# Templates: unavailable", "", *[f"- {e}" for e in r.get("errors", [])]])
    out = ["# Registered templates", "", "| Template | What | Language | Fields you can fill (now in the template) |", "|---|---|---|---|"]
    for t in r["templates"]:
        fl = "; ".join(f"{f['name']} = {f['label']} ({f['script']}, up to {f['max_chars']} characters; now \"{f['currently']}\")" for f in t["fields"])
        out.append(f"| {t['template_id']} | {_cell(t['title'])} ({t['kind']}, {t['app']}) | {t['language']} | {_cell(fl)} |")
    if r.get("not_ready"):
        out += ["", "Not registered yet:"] + [f"- {n['what']}: {n['why']}" for n in r["not_ready"]]
    return "\n".join(out + ["", FOOTER])


def verbatim_text(name, result):
    """The finished reply for list / fill / export results (None => let a model explain it). The review is model judgement and
    is not verbatim."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    if name == "list_adobe_templates":
        return list_markdown(result)
    if name == "fill_adobe_template":
        return result_markdown(result) if result.get("stage") == "executed" else plan_markdown(result)
    if name == "export_adobe_preview":
        return export_markdown(result)
    if name == "review_adobe_preview":
        return review_markdown(result)
    return None


# ── dispatch ─────────────────────────────────────────────────────────────────

_FUNCS = {"list_adobe_templates": list_adobe_templates, "fill_adobe_template": fill_adobe_template,
          "export_adobe_preview": export_adobe_preview, "review_adobe_preview": review_adobe_preview}


def call_tool(name, args):
    fn = _FUNCS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    args = dict(args or {})
    allowed = set(fn.__code__.co_varnames[:fn.__code__.co_argcount])
    if name == "fill_adobe_template":
        allowed.discard("plan_id")      # a model can never run a plan; only a person's "yes" does (tool_executor.pending_plan)
    ignored = sorted(k for k in args if k not in allowed)
    result = fn(**{k: v for k, v in args.items() if k in allowed})
    if ignored:
        result["ignored_args"] = ignored
    return json.dumps(result, ensure_ascii=False, indent=2)
