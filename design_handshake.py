"""design_handshake.py -- the live connection check of the five Adobe apps (Piece 2 of the Design portal).

For each app: launch it (only with the manager's yes), connect, run a FIXED hand-written script (never model-written), and report what really happened.
Photoshop, Illustrator and InDesign also draw one short Arabic line in a font that has Arabic, export it as a PNG, and this module READS THE PICTURE BACK:
  * joining: properly shaped Arabic has few connected ink parts (letters join); an engine without Arabic shaping draws every letter apart;
  * direction: the short word comes first in the text, so in a right-to-left layout it sits at the right.
After Effects is driven with AfterFX.exe -r, Premiere through the signed CEP panel (its debug port and the queue file).
Nothing here edits a designer's file: scratch documents are created and closed without saving, a sample file is a COPY, and an app that was already
running is left running (only a scratch document of ours is closed).

Run:  python design_handshake.py <photoshop|illustrator|indesign|aftereffects|premiere> [--sample-ai PATH] [--keep-open]
"""
from david_config import DAVID_DATA, DAVID_HOME
import os
import sys
import json
import time
import shutil
import hashlib
import datetime
import subprocess
import urllib.request

import numpy as np
from PIL import Image

OUT_ROOT = str(DAVID_DATA / "generated" / "design_handshake")
ARABIC = "\u0644\u0627 \u0627\u0644\u062a\u0635\u0645\u064a\u0645"  # "la at-tasmim": a short word first, then a long one
ARABIC_FONT_PS = "SakkalMajalla"
ARABIC_FONT_FAMILY = "Sakkal Majalla"
# The program files and COM ids are discovered (adobe_install.py): the newest STABLE yearly build whose program file exists (betas are
# never chosen), and the registered COM id that starts exactly that program file. The 2026 literals below are only the fallback.
import adobe_install

_FALLBACK_EXES = {"photoshop": r"C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe",
                  "illustrator": r"C:\Program Files\Adobe\Adobe Illustrator 2026\Support Files\Contents\Windows\Illustrator.exe",
                  "indesign": r"C:\Program Files\Adobe\Adobe InDesign 2026\InDesign.exe",
                  "aftereffects": r"C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\AfterFX.exe",
                  "premiere": r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe"}
# the stable InDesign can have no ProgID of its own (a beta install may own them), so the fallback pins its CLSID
_FALLBACK_COM_IDS = {"photoshop": ["Photoshop.Application"], "illustrator": ["Illustrator.Application"], "indesign": ["{45112D7C-822C-400A-BA8C-D682F7FD0B8E}"]}
CHOSEN = {app: adobe_install.choose(app) for app in _FALLBACK_EXES}
EXES = {app: CHOSEN[app].get("exe") or _FALLBACK_EXES[app] for app in _FALLBACK_EXES}
PROCS = {app: os.path.basename(EXES[app]) for app in EXES}
COM_IDS = {app: adobe_install.com_ids(app, fallback=ids) for app, ids in _FALLBACK_COM_IDS.items()}
PREMIERE_QUEUE = str(DAVID_DATA / "premiere_queue.json")
PREMIERE_DEBUG_PORT = 8088


# ---- reading the Arabic picture back ----

def analyze_arabic(path):
    """Reads an exported picture of the Arabic test line (dark text on a light background) and says whether it was shaped and which way it runs."""
    import cv2
    a = np.array(Image.open(path).convert("L"))
    ink = (a < 128).astype(np.uint8)
    if int(ink.sum()) < 200:
        return {"ok": False, "reason": "the picture is blank"}
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    comps = stats[1:]
    maxh = int(comps[:, 3].max())
    body = [c for c in comps if c[3] >= 0.35 * maxh]
    nb = len(body)
    joining = "joined" if nb <= 4 else "disjoint" if nb >= 7 else "partial"
    cols = ink.any(axis=0)
    runs, start = [], None
    for x, v in enumerate(cols):
        if v and start is None:
            start = x
        if not v and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, len(cols) - 1))
    direction, left_w, right_w = "unclear", None, None
    if len(runs) >= 2:
        gaps = [(runs[i + 1][0] - runs[i][1], i) for i in range(len(runs) - 1)]
        _, gi = max(gaps)
        left_w, right_w = runs[gi][1] - runs[0][0] + 1, runs[-1][1] - runs[gi + 1][0] + 1
        if right_w < 0.8 * left_w:
            direction = "rtl"
        elif left_w < 0.8 * right_w:
            direction = "ltr"
    shaped_rtl = joining == "joined" and direction == "rtl"
    return {"ok": True, "body_parts": nb, "joining": joining, "direction": direction, "left_group_px": left_w, "right_group_px": right_w, "arabic_ok": shaped_rtl,
            "reading": ("letters join and the short first word sits at the right: correct Arabic" if shaped_rtl else
                        "every letter is drawn apart (no Arabic shaping in this engine)" if joining == "disjoint" else
                        "letters join but the order runs left to right (direction is wrong)" if joining == "joined" and direction == "ltr" else
                        "could not decide: " + joining + " / " + direction)}


# ---- the fixed scripts ----

PRELUDE = r"""
function J(v){var t=typeof v;if(v===null||v===undefined)return "null";if(t==="number")return isFinite(v)?String(v):"null";if(t==="boolean")return String(v);
if(t==="string")return '"'+v.replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/\r/g,"\\r").replace(/\n/g,"\\n").replace(/\t/g,"\\t")+'"';
if(v instanceof Array){var a=[];for(var i=0;i<v.length;i++)a.push(J(v[i]));return "["+a.join(",")+"]";}
var p=[];for(var k in v){if(v.hasOwnProperty(k))p.push(J(k)+":"+J(v[k]));}return "{"+p.join(",")+"}";}
function W(path,obj){var f=new File(path);f.encoding="UTF-8";f.open("w");f.write(J(obj));f.close();}
var R={steps:{},errors:[]};
function step(name,fn){try{R.steps[name]=fn();}catch(e){R.steps[name]={error:String(e)};R.errors.push(name+": "+e);}}
"""

PS_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG=__PNG__,PSD=__PSD__,TEXT=__TEXT__,FONT=__FONT__;
R.app="photoshop";
var prevDialogs=app.displayDialogs;app.displayDialogs=DialogModes.NO;
var oldRuler=app.preferences.rulerUnits,oldType=app.preferences.typeUnits;
app.preferences.rulerUnits=Units.PIXELS;app.preferences.typeUnits=TypeUnits.PIXELS;
step("info",function(){return {version:app.version,name:app.name,build:app.build,documents_open_before:app.documents.length};});
step("scratch_save_reopen",function(){
 var d=app.documents.add(800,600,72,"hs_blank",NewDocumentMode.RGB,DocumentFill.WHITE);
 d.saveAs(new File(PSD),new PhotoshopSaveOptions(),true);d.close(SaveOptions.DONOTSAVECHANGES);
 var d2=app.open(new File(PSD));var r={w:d2.width.as("px"),h:d2.height.as("px"),mode:String(d2.mode),layers:d2.layers.length};
 d2.close(SaveOptions.DONOTSAVECHANGES);return r;});
step("arabic",function(){
 var d=app.documents.add(1400,400,72,"hs_arabic",NewDocumentMode.RGB,DocumentFill.WHITE);
 var lyr=d.artLayers.add();lyr.kind=LayerKind.TEXT;var ti=lyr.textItem;
 ti.contents=TEXT;ti.size=120;var fontUsed="";
 try{ti.font=FONT;fontUsed=String(ti.font);}catch(e){fontUsed="font not set: "+e;}
 var c=new SolidColor();c.rgb.red=0;c.rgb.green=0;c.rgb.blue=0;ti.color=c;ti.position=[60,250];
 var back=ti.contents;
 var o=new ExportOptionsSaveForWeb();o.format=SaveDocumentType.PNG;o.PNG8=false;o.transparency=false;
 d.exportDocument(new File(PNG),ExportType.SAVEFORWEB,o);d.close(SaveOptions.DONOTSAVECHANGES);
 return {read_back_equals_sent:(back===TEXT),font:fontUsed,png:PNG};});
app.preferences.rulerUnits=oldRuler;app.preferences.typeUnits=oldType;app.displayDialogs=prevDialogs;
W(OUT,R);
"""

AI_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG1=__PNG1__,PNG2=__PNG2__,TEXT=__TEXT__,FONT=__FONT__,SAMPLE=__SAMPLE__;
R.app="illustrator";
var prevUI=app.userInteractionLevel;app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
step("info",function(){var n=[];for(var i=0;i<app.documents.length;i++)n.push(app.documents[i].name);return {version:app.version,name:app.name,documents_open_before:n};});
step("sample_open_copy",function(){
 if(!SAMPLE)return {skipped:true};
 var d=app.open(new File(SAMPLE));
 var r={name:d.name,artboards:d.artboards.length,layers:d.layers.length,text_frames:d.textFrames.length,path_items:d.pathItems.length,color_space:String(d.documentColorSpace)};
 d.close(SaveOptions.DONOTSAVECHANGES);return r;});
step("arabic",function(){
 var d=app.documents.add(DocumentColorSpace.RGB,1400,400);var ab=d.artboards[0].artboardRect;
 var tf=d.textFrames.add();tf.contents=TEXT;var ca=tf.textRange.characterAttributes;ca.size=120;var fontUsed="";
 try{ca.textFont=app.textFonts.getByName(FONT);fontUsed=FONT;}catch(e){fontUsed="font not set: "+e;}
 var black=new RGBColor();black.red=0;black.green=0;black.blue=0;ca.fillColor=black;
 tf.position=[ab[0]+60,ab[1]-60];var back=tf.contents;
 function shot(path){var o=new ExportOptionsPNG24();o.antiAliasing=true;o.artBoardClipping=true;o.transparency=false;o.horizontalScale=100;o.verticalScale=100;d.exportFile(new File(path),ExportType.PNG24,o);}
 var r={read_back_equals_sent:(back===TEXT),font:fontUsed,composer_default:String(tf.textRange.paragraphAttributes.composerEngine)};
 shot(PNG1);r.png_default=PNG1;
 try{tf.textRange.paragraphAttributes.composerEngine=ComposerEngineType.optycaComposer;r.composer_set="optyca";}catch(e){r.composer_set="not available: "+e;}
 shot(PNG2);r.png_with_rtl_attributes=PNG2;
 d.close(SaveOptions.DONOTSAVECHANGES);return r;});
app.userInteractionLevel=prevUI;
W(OUT,R);
"""

ID_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG1=__PNG1__,PNG2=__PNG2__,TEXT=__TEXT__,FAMILY=__FAMILY__;
R.app="indesign";
var prevUI=app.scriptPreferences.userInteractionLevel;app.scriptPreferences.userInteractionLevel=UserInteractionLevels.NEVER_INTERACT;
step("info",function(){return {version:app.version,name:app.name,locale:String(app.locale),documents_open_before:app.documents.length};});
function exportPng(d,path){
 app.pngExportPreferences.exportResolution=72;app.pngExportPreferences.transparentBackground=false;
 try{app.pngExportPreferences.pageString="1";}catch(e){}
 d.exportFile(ExportFormat.PNG_FORMAT,new File(path),false);}
step("arabic",function(){
 var r={};
 var d=app.documents.add();
 d.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;
 d.documentPreferences.facingPages=false;d.documentPreferences.pageWidth=700;d.documentPreferences.pageHeight=220;
 var pg=d.pages[0];pg.marginPreferences.top=0;pg.marginPreferences.bottom=0;pg.marginPreferences.left=0;pg.marginPreferences.right=0;
 var tf=pg.textFrames.add();tf.geometricBounds=[10,10,210,690];tf.contents=TEXT;
 var tx=tf.texts[0];tx.pointSize=90;
 var fontUsed="";
 try{tx.appliedFont=app.fonts.itemByName(FAMILY+"\tRegular");fontUsed=String(tx.appliedFont.name);}
 catch(e){try{for(var i=0;i<app.fonts.length;i++){if(app.fonts[i].fontFamily==FAMILY){tx.appliedFont=app.fonts[i];fontUsed=String(app.fonts[i].name);break;}}}catch(e2){fontUsed="font not set: "+e2;}}
 r.font=fontUsed;r.read_back_equals_sent=(tf.contents===TEXT);
 exportPng(d,PNG1);r.png_default=PNG1;
 r.me_features={};
 try{tx.characterDirection=CharacterDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.me_features.characterDirection="set";}catch(e){r.me_features.characterDirection="not available: "+e;}
 try{tf.paragraphs[0].paragraphDirection=ParagraphDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.me_features.paragraphDirection="set";}catch(e){r.me_features.paragraphDirection="not available: "+e;}
 try{tf.paragraphs[0].composer="Adobe World-Ready Paragraph Composer";r.me_features.composer="set";}catch(e){r.me_features.composer="not available: "+e;}
 try{tf.paragraphs[0].justification=Justification.RIGHT_ALIGN;r.me_features.alignment="set";}catch(e){r.me_features.alignment="not available: "+e;}
 exportPng(d,PNG2);r.png_with_rtl_attributes=PNG2;
 d.close(SaveOptions.NO);return r;});
app.scriptPreferences.userInteractionLevel=prevUI;
W(OUT,R);
"""

AE_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG=__PNG__,AEP=__AEP__,TEXT=__TEXT__,FONT=__FONT__;
R.app="aftereffects";
step("info",function(){return {version:app.version,build:app.buildName,language:String(app.isoLanguage)};});
step("comp_and_text",function(){
 var proj=app.newProject();
 var comp=proj.items.addComp("hs_comp",1400,400,1,4,25);
 var layer=comp.layers.addText(TEXT);var prop=layer.property("Source Text");var td=prop.value;
 var fontSet="";try{td.font=FONT;td.fontSize=120;td.fillColor=[0,0,0];prop.setValue(td);fontSet=FONT;}catch(e){fontSet="font not set: "+e;}
 var bg=comp.layers.addSolid([1,1,1],"bg",1400,400,1);bg.moveToEnd();
 var back=prop.value.text;var png="not written";
 try{comp.saveFrameToPng(0,new File(PNG));png=PNG;}catch(e){png="saveFrameToPng failed: "+e;}
 var aep="not saved";try{proj.save(new File(AEP));aep=AEP;}catch(e){aep="save failed: "+e;}
 var r={comp:comp.name,width:comp.width,height:comp.height,fps:comp.frameRate,duration:comp.duration,layers:comp.numLayers,read_back_equals_sent:(back===TEXT),font:fontSet,png:png,aep:aep};
 proj.close(CloseOptions.DO_NOT_SAVE_CHANGES);return r;});
W(OUT,R);
if(__QUIT__){app.quit();}
"""


def _lit(s):
    return json.dumps(s, ensure_ascii=True)


def _fwd(p):
    return p.replace("\\", "/")


def build_jsx(app, d, quit_after=True):
    """The fixed script for one app with its paths and the Arabic line filled in (as escaped literals, so no encoding can damage it)."""
    f = lambda n: _fwd(os.path.join(d, n))
    if app == "photoshop":
        return PS_JSX.replace("__OUT__", _lit(f("ps_result.json"))).replace("__PNG__", _lit(f("ps_arabic.png"))).replace("__PSD__", _lit(f("ps_scratch.psd"))).replace("__TEXT__", _lit(ARABIC)).replace("__FONT__", _lit(ARABIC_FONT_PS))
    if app == "illustrator":
        return AI_JSX.replace("__OUT__", _lit(f("ai_result.json"))).replace("__PNG1__", _lit(f("ai_arabic_default.png"))).replace("__PNG2__", _lit(f("ai_arabic_optyca.png"))).replace("__TEXT__", _lit(ARABIC)).replace("__FONT__", _lit(ARABIC_FONT_PS)).replace("__SAMPLE__", _lit(_fwd(d_sample(d)) if d_sample(d) else ""))
    if app == "indesign":
        return ID_JSX.replace("__OUT__", _lit(f("id_result.json"))).replace("__PNG1__", _lit(f("id_arabic_default.png"))).replace("__PNG2__", _lit(f("id_arabic_rtl.png"))).replace("__TEXT__", _lit(ARABIC)).replace("__FAMILY__", _lit(ARABIC_FONT_FAMILY))
    if app == "aftereffects":
        return AE_JSX.replace("__OUT__", _lit(f("ae_result.json"))).replace("__PNG__", _lit(f("ae_arabic.png"))).replace("__AEP__", _lit(f("ae_scratch.aep"))).replace("__TEXT__", _lit(ARABIC)).replace("__FONT__", _lit(ARABIC_FONT_PS)).replace("__QUIT__", "true" if quit_after else "false")
    raise ValueError(app)


def d_sample(d):
    p = os.path.join(d, "sample_copy.ai")
    return p if os.path.isfile(p) else ""


# ---- helpers ----

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def running(app):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PROCS[app]}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return PROCS[app].lower() in out.lower()


def wait_until(fn, seconds, every=2.0):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def exe_version(app):
    try:
        v = adobe_install.file_version(EXES[app])           # win32api: no shell
        if v:
            return v
        # fallback: the path travels in an environment variable, never inside the command text (a quote in it cannot run code)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Item -LiteralPath $env:DAVID_EXE_PATH).VersionInfo.ProductVersion"],
                             capture_output=True, text=True, timeout=20, env=dict(os.environ, DAVID_EXE_PATH=EXES[app])).stdout.strip()
        return out
    except Exception:
        return ""


def com_connect(app, launch_wait=240):
    import win32com.client
    last = None
    for cid in COM_IDS[app]:
        try:
            return win32com.client.Dispatch(cid), cid
        except Exception as e:
            last = e
    raise RuntimeError(f"cannot connect through COM: {last}")


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def read_com_app(app, d, report):
    t0 = time.time()
    app_obj, cid = com_connect(app)
    report["com_id"] = cid
    report["connect_seconds"] = round(time.time() - t0, 1)
    # the process-name stem travels in an environment variable, never inside the command text
    exe = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process | Where-Object { $_.Path -and (Split-Path $_.Path -Leaf) -like ('*' + $env:DAVID_PROC_STEM + '*') } | Select-Object -First 3 -ExpandProperty Path) -join '|'"],
                         capture_output=True, text=True, timeout=30, env=dict(os.environ, DAVID_PROC_STEM=os.path.splitext(PROCS[app])[0])).stdout.strip()
    report["connected_exe"] = exe
    if EXES[app].lower() not in exe.lower().split("|"):
        raise RuntimeError(f"the connection opened a different program than the stable one ({exe}); nothing was run")
    return app_obj


def run_scripted_app(app, d, report, keep_open):
    was = running(app)
    report["was_running_before"] = was
    app_obj = read_com_app(app, d, report)
    jsx = os.path.join(d, f"{app}_handshake.jsx")
    with open(jsx, "w", encoding="utf-8") as f:
        f.write(build_jsx(app, d))
    out = os.path.join(d, {"photoshop": "ps", "illustrator": "ai", "indesign": "id"}[app] + "_result.json")
    t0 = time.time()
    if app == "indesign":
        app_obj.DoScript(jsx, 1246973031)
    else:
        app_obj.DoJavaScriptFile(jsx)
    report["script_seconds"] = round(time.time() - t0, 1)
    if not os.path.isfile(out):
        raise RuntimeError("the script ran but wrote no result file")
    report["script_result"] = read_json(out)
    report["script_ran"] = True
    report["arabic"] = {}
    for label, p in _walk_pngs(report["script_result"]):
        p = p.replace("/", os.sep)
        report["arabic"][label] = analyze_arabic(p) if os.path.isfile(p) else {"ok": False, "reason": "no picture was exported"}
    if not was and not keep_open:
        try:
            app_obj.Quit() if app != "indesign" else app_obj.Quit(1852776480)
            report["closed_by_us"] = bool(wait_until(lambda: not running(app), 60))
        except Exception as e:
            report["closed_by_us"] = f"could not close: {e}"
    else:
        report["closed_by_us"] = False
    return report


def _walk_pngs(res):
    out = []
    for stepname, v in (res.get("steps") or {}).items():
        if isinstance(v, dict):
            for k, val in v.items():
                if k.startswith("png") and isinstance(val, str) and val.lower().endswith(".png"):
                    out.append((f"{stepname}:{k}", val))
    return sorted(out)


def run_aftereffects(d, report, keep_open):
    was = running("aftereffects")
    report["was_running_before"] = was
    jsx = os.path.join(d, "aftereffects_handshake.jsx")
    with open(jsx, "w", encoding="utf-8") as f:
        f.write(build_jsx("aftereffects", d, quit_after=not (was or keep_open)))
    out = os.path.join(d, "ae_result.json")
    t0 = time.time()
    proc = subprocess.Popen([EXES["aftereffects"], "-r", jsx], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = wait_until(lambda: os.path.isfile(out), 300, 3)
    report["script_seconds"] = round(time.time() - t0, 1)
    if not ok:
        report["script_ran"] = False
        raise RuntimeError("After Effects did not write the result within 5 minutes (a dialog may be waiting, or the script was not accepted)")
    time.sleep(1)
    report["script_ran"] = True
    report["script_result"] = read_json(out)
    report["arabic"] = {}
    for label, p in _walk_pngs(report["script_result"]):
        p = p.replace("/", os.sep)
        report["arabic"][label] = analyze_arabic(p) if os.path.isfile(p) else {"ok": False, "reason": "no picture was exported"}
    try:
        ar = subprocess.run([os.path.join(os.path.dirname(EXES["aftereffects"]), "aerender.exe"), "-version"], capture_output=True, text=True, timeout=60)
        report["aerender_version"] = (ar.stdout or ar.stderr).strip().splitlines()[0] if (ar.stdout or ar.stderr).strip() else ""
    except Exception as e:
        report["aerender_version"] = f"failed: {e}"
    if not was and not keep_open:
        report["closed_by_us"] = bool(wait_until(lambda: not running("aftereffects"), 60))
    else:
        report["closed_by_us"] = False
    # comp.saveFrameToPng() finishes in the background and the script quits right after, so its PNG is never written (10 and
    # 22 Oct). The script also saves its scratch project; aerender then renders frame 0 of it, and that picture is read back.
    aep = ((report["script_result"].get("steps") or {}).get("comp_and_text") or {}).get("aep", "")
    if aep and os.path.isfile(aep.replace("/", os.sep)) and not any(v.get("ok") for v in report["arabic"].values()):
        frame, how = aerender_frame(aep.replace("/", os.sep), "hs_comp", d)
        report["aerender_frame"] = how
        report["arabic"]["comp_and_text:aerender"] = analyze_arabic(frame) if frame else {"ok": False, "reason": how}
    return report


AERENDER_TEMPLATES = ("PNG Sequence", "TIFF Sequence with Alpha", "Photoshop")   # names differ between versions: first one that renders wins


def aerender_frame(aep, comp, out_dir, timeout=300):
    """Renders frame 0 of `comp` in the scratch project `aep` with aerender. Returns (picture path, how) or (None, why)."""
    import glob as _glob
    exe = os.path.join(os.path.dirname(EXES["aftereffects"]), "aerender.exe")
    tried = []
    for tmpl in AERENDER_TEMPLATES:
        stem = os.path.join(out_dir, "ae_frame_" + tmpl.split()[0].lower())
        cmd = [exe, "-project", aep, "-comp", comp, "-s", "0", "-e", "0", "-RStemplate", "Best Settings", "-OMtemplate", tmpl, "-output", stem + "_[#####]"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        except Exception as e:
            tried.append(f"{tmpl}: {type(e).__name__}")
            continue
        pics = sorted(_glob.glob(stem + "_*"))
        if pics:
            return pics[0], f"rendered with aerender, output module '{tmpl}'"
        tail = [l for l in (r.stdout or r.stderr or "").splitlines() if "error" in l.lower()][-1:]
        tried.append(f"{tmpl}: exit {r.returncode}" + (f" ({tail[0][:120]})" if tail else ""))
    return None, "aerender rendered nothing: " + "; ".join(tried)


def _premiere_panel_pages():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PREMIERE_DEBUG_PORT}/json", timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _queue_info_command():
    sys.path.insert(0, str(DAVID_HOME))
    import adobe_bridge
    return adobe_bridge._premiere_queue_command({"action": "info"})


def run_premiere(d, report, keep_open):
    was = running("premiere")
    report["was_running_before"] = was
    manifest = os.path.join(os.environ.get("APPDATA", ""), "Adobe", "CEP", "extensions", "david-premiere-panel", "CSXS", "manifest.xml")
    report["panel_installed"] = os.path.isfile(manifest)
    report["panel_manifest_host"] = ""
    if report["panel_installed"]:
        txt = open(manifest, encoding="utf-8").read()
        import re
        m = re.search(r'<Host Name="PPRO" Version="([^"]+)"', txt)
        report["panel_manifest_host"] = m.group(1) if m else ""
    report["player_debug_mode"] = {}
    try:
        import winreg
        for v in (11, 12):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Adobe\CSXS.{v}") as k:
                    report["player_debug_mode"][f"CSXS.{v}"] = winreg.QueryValueEx(k, "PlayerDebugMode")[0]
            except OSError:
                report["player_debug_mode"][f"CSXS.{v}"] = None
    except ImportError:
        pass
    t0 = time.time()
    if not was:
        subprocess.Popen([EXES["premiere"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report["launched_process"] = bool(wait_until(lambda: running("premiere"), 120))
    pages = wait_until(_premiere_panel_pages, 240, 5)
    report["seconds_to_panel_port"] = round(time.time() - t0, 1)
    report["panel_debug_port_answers"] = pages is not None
    report["panel_pages"] = [{"title": p.get("title"), "url": (p.get("url") or "")[:120]} for p in (pages or [])]
    if pages is None:
        report["script_ran"] = False
        report["panel_loaded"] = False
        report["note"] = "Premiere started but the David panel is not running (its debug port does not answer). The panel is not open in the current workspace: it must be opened once from the Window menu (Extensions), or it is blocked."
    else:
        cmd = _queue_info_command()
        cid = cmd.get("cmd_id")
        def done():
            try:
                q = read_json(PREMIERE_QUEUE)
            except Exception:
                return None
            for c in q.get("commands", []):
                if c.get("id") == cid and c.get("status") in ("done", "error"):
                    return c
            return None
        c = wait_until(done, 40, 2)
        report["panel_loaded"] = True
        report["script_ran"] = bool(c)
        report["queue_result"] = (c or {}).get("result", "no answer within 40 seconds")
        report["queue_status"] = (c or {}).get("status", "pending")
        report["roundtrip_proves_host_script"] = bool(c) and str(c.get("result", "")).startswith(("DAVID_OK:", "DAVID_ERR:"))
    report["premiere_window_titles"] = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process 'Adobe Premiere Pro' -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle} | ForEach-Object {$_.MainWindowTitle}) -join ' | '"], capture_output=True, text=True, timeout=30).stdout.strip()
    logs = []
    for base in (os.environ.get("TEMP", ""), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp")):
        try:
            logs += [os.path.join(base, f) for f in os.listdir(base) if f.lower().startswith(("csxs", "cep")) and f.lower().endswith(".log")]
        except OSError:
            pass
    report["cep_logs"] = sorted(set(logs))[:6]
    tail = []
    for lg in report["cep_logs"]:
        try:
            lines = open(lg, encoding="utf-8", errors="ignore").read().splitlines()
            tail += [l for l in lines[-200:] if "david" in l.lower() or "error" in l.lower() or "signature" in l.lower()][-6:]
        except OSError:
            pass
    report["cep_log_lines"] = tail[-10:]
    if not was and not keep_open:
        subprocess.run(["taskkill", "/IM", PROCS["premiere"]], capture_output=True)
        report["closed_by_us"] = bool(wait_until(lambda: not running("premiere"), 90))
    else:
        report["closed_by_us"] = False
    return report


def handshake(app, out_dir=None, sample_ai=None, keep_open=False):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    d = out_dir or os.path.join(OUT_ROOT, f"{stamp}_{app}")
    os.makedirs(d, exist_ok=True)
    report = {"app": app, "at": datetime.datetime.now().isoformat(timespec="seconds"), "exe": EXES[app], "installed_version": exe_version(app), "folder": d, "originals_unchanged": True,
              "build_used": {"exe": EXES[app], "year": CHOSEN[app].get("year"), "version": CHOSEN[app].get("version"),
                             "how": CHOSEN[app].get("source") if CHOSEN[app].get("exe") else "fallback literal (discovery found nothing)",
                             "betas_beside_not_used": CHOSEN[app].get("betas_beside", []), "com_id": (COM_IDS.get(app) or [None])[0]}}
    print(f"  build used: {report['build_used']['how']} | version {report['build_used']['version']} | {EXES[app]}", flush=True)
    originals = {}
    try:
        if sample_ai and app == "illustrator":
            copy = os.path.join(d, "sample_copy.ai")
            originals[sample_ai] = sha256_file(sample_ai)
            shutil.copy2(sample_ai, copy)
            report["sample"] = {"original": sample_ai, "sha256": originals[sample_ai]}
        if not os.path.isfile(EXES[app]):
            raise RuntimeError("the program file was not found")
        if app in ("photoshop", "illustrator", "indesign"):
            run_scripted_app(app, d, report, keep_open)
        elif app == "aftereffects":
            run_aftereffects(d, report, keep_open)
        else:
            run_premiere(d, report, keep_open)
        report["ok"] = True
    except Exception as e:
        report["ok"] = False
        report["error"] = str(e)[:500]
    for p, h in originals.items():
        if sha256_file(p) != h:
            report["originals_unchanged"] = False
    with open(os.path.join(d, "handshake.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def summarize_report(app, r):
    """One honest sentence (or two) about what the handshake of one app showed."""
    if not r.get("ok"):
        return f"Did not work: {r.get('error', 'unknown error')}"
    parts = []
    if app in ("photoshop", "illustrator", "indesign"):
        parts.append(f"Connected through Windows COM ({r.get('connect_seconds', '?')} s) and ran the fixed script.")
        steps = (r.get("script_result") or {}).get("steps", {})
        if "scratch_save_reopen" in steps and "error" not in steps["scratch_save_reopen"]:
            parts.append("A scratch document was saved, reopened and read back.")
        if "sample_open_copy" in steps and "error" not in steps["sample_open_copy"] and not steps["sample_open_copy"].get("skipped"):
            parts.append("A copy of a real file opened and was read (original unchanged).")
        for label, a in (r.get("arabic") or {}).items():
            name = "default text settings" if "default" in label else "with right-to-left settings" if "rtl" in label else "plain text"
            parts.append(f"Arabic ({name}): " + ("correct" if a.get("arabic_ok") else "NOT correct") + f", {a.get('reading', '')}.")
    elif app == "aftereffects":
        parts.append(f"Launched with AfterFX.exe -r and ran the fixed script in {r.get('script_seconds', '?')} s; made a composition with Arabic text and read it back; {r.get('aerender_version', 'aerender not checked')}.")
        parts.append("The Arabic picture could not be exported by this version's script call, so Arabic shaping in After Effects was not measured.")
    elif app == "premiere":
        parts.append("Premiere launched." if r.get("launched_process") else "Premiere did not start.")
        if r.get("panel_loaded"):
            parts.append(f"The David panel answered and ran a script inside Premiere ({r.get('queue_result', '')[:80]}).")
        else:
            parts.append("The David panel is not running: it loads only after it is opened from Premiere's Window menu, and it has not been opened.")
    if r.get("originals_unchanged") is False:
        parts.append("WARNING: an original changed.")
    return " ".join(parts)


def save_results(reports, path):
    out = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "apps": reports, "summary": {a: summarize_report(a, r) for a, r in reports.items()}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv):
    if not argv or argv[0] not in EXES:
        print(__doc__)
        return 2
    sample = argv[argv.index("--sample-ai") + 1] if "--sample-ai" in argv else None
    r = handshake(argv[0], sample_ai=sample, keep_open="--keep-open" in argv)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
