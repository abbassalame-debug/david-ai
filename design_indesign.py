"""design_indesign.py -- the fixed InDesign operations of the Design portal (Piece 5). The scripts are written here, by hand, and tested; a model never writes one.

  indesign.inspect        read a file: pages, size, bleed, layers, fonts (and which are missing), text frames (direction, overset), links (and which are missing), swatches
  indesign.export_pdf     a PDF of a COPY with one of InDesign's own PDF presets, plus a PNG of the first page as the preview
  indesign.new_document   a new document from one of the designer's document presets (size, bleed and margins from his preferences), optionally right to left
  indesign.assemble_layers   a NEW document from one of the designer's presets with each picture on its own named layer (embedded, full page) and optional live text on a 'Text' layer
  indesign.place_content  Arabic or Latin text and/or one picture placed on a page of a COPY, saved as a NEW file (the copy itself is not changed)

Arabic: InDesign shapes Arabic only when the text has right-to-left character and paragraph direction and the World-Ready paragraph composer (measured on this computer on
2026-10-10: default settings draw the letters apart, with these settings they join and run right to left). Every operation that creates text sets them when the text has Arabic.
InDesign is reached by its own stable-build CLSID (design_com), never by a ProgID (those belong to the beta installs). Files are opened without a window and without alerts.
"""
import os
import re
import json
import shutil

import design_com
import design_naming
import design_ops
import design_preferences as PREF
import design_runner as R
from design_handshake import PRELUDE

SOURCE_EXT = (".indd", ".idml")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".ai")
ARABIC_RE = re.compile("[؀-ۿݐ-ݿ]")
PDF_PRESETS = {"high_quality": "[High Quality Print]", "press": "[Press Quality]", "pdfx4": "[PDF/X-4:2008]", "smallest": "[Smallest File Size]"}
ARABIC_FONTS = ["Adobe Arabic", "Sakkal Majalla"]
LATIN_FONTS = ["Myriad Pro", "Arial"]


class InDesignError(design_com.AppError):
    pass


def _lit(v):
    return json.dumps(v, ensure_ascii=True)


def _fwd(p):
    return p.replace("\\", "/")


# ---- the fixed scripts ----

HEAD = r"""
R.app="indesign";
var prevUI=app.scriptPreferences.userInteractionLevel;app.scriptPreferences.userInteractionLevel=UserInteractionLevels.NEVER_INTERACT;
"""
FONT_FN = r"""
function findFont(fam){var any=null;for(var i=0;i<app.fonts.length;i++){var f=app.fonts[i];if(f.fontFamily===fam){if(String(f.fontStyleName)==="Regular")return f;if(!any)any=f;}}return any;}
"""
RTL_FN = r"""
function rtlText(tx){var r={};
 try{tx.characterDirection=CharacterDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.character="set";}catch(e){r.character="not available: "+e;}
 try{tx.paragraphs[0].paragraphDirection=ParagraphDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.paragraph="set";}catch(e){r.paragraph="not available: "+e;}
 try{tx.composer="Adobe World-Ready Paragraph Composer";r.composer="set";}catch(e){r.composer="not available: "+e;}
 try{tx.justification=Justification.RIGHT_ALIGN;r.alignment="set";}catch(e){r.alignment="not available: "+e;}
 return r;}
function ltrText(tx){var r={};
 try{tx.characterDirection=CharacterDirectionOptions.LEFT_TO_RIGHT_DIRECTION;r.character="set";}catch(e){r.character="not available: "+e;}
 try{tx.paragraphs[0].paragraphDirection=ParagraphDirectionOptions.LEFT_TO_RIGHT_DIRECTION;r.paragraph="set";}catch(e){r.paragraph="not available: "+e;}
 try{tx.composer="Adobe Paragraph Composer";r.composer="set";}catch(e){r.composer="not available: "+e;}
 try{tx.justification=Justification.LEFT_ALIGN;r.alignment="set";}catch(e){r.alignment="not available: "+e;}
 return r;}
"""
PNG_FN = r"""
function png(d,page,path){app.pngExportPreferences.exportResolution=72;app.pngExportPreferences.transparentBackground=false;try{app.pngExportPreferences.pageString=String(page);}catch(e){}
 d.exportFile(ExportFormat.PNG_FORMAT,new File(path),false);}
"""
TAIL = r"""
try{if(doc)doc.close(SaveOptions.NO);}catch(e){}
app.scriptPreferences.userInteractionLevel=prevUI;
W(__RES__,R);
"""
OPEN = r"""
var doc=null;
try{doc=app.open(new File(SRC),false);}catch(e){R.errors.push("open: "+e);}
if(doc){doc.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.MILLIMETERS;doc.viewPreferences.verticalMeasurementUnits=MeasurementUnits.MILLIMETERS;}
function r2(x){return Math.round(x*100)/100;}
"""

INSPECT_JSX = PRELUDE + "var SRC=__SRC__;" + HEAD + OPEN + r"""
step("inspect",function(){
 if(!doc)throw "the file did not open";
 var dp=doc.documentPreferences;
 var info={name:doc.name,pages:doc.pages.length,spreads:doc.spreads.length,width_mm:r2(dp.pageWidth),height_mm:r2(dp.pageHeight),facing_pages:dp.facingPages,binding:String(dp.pageBinding),
  bleed_mm:{top:r2(dp.documentBleedTopOffset),bottom:r2(dp.documentBleedBottomOffset),inside:r2(dp.documentBleedInsideOrLeftOffset),outside:r2(dp.documentBleedOutsideOrRightOffset)},version:String(app.version)};
 info.layers=[];for(var i=0;i<doc.layers.length&&i<100;i++){var l=doc.layers[i];info.layers.push({name:l.name,visible:l.visible,locked:l.locked});}
 info.fonts=[];for(var i=0;i<doc.fonts.length&&i<200;i++){var f=doc.fonts[i];info.fonts.push({name:String(f.name),family:String(f.fontFamily),status:String(f.status)});}
 info.texts=[];
 for(var i=0;i<doc.textFrames.length&&i<300;i++){var tf=doc.textFrames[i];var o={contents:String(tf.contents).substr(0,120),page:"",overset:tf.overflows,font:"",direction:"",composer:""};
  try{o.page=String(tf.parentPage.name);}catch(e){o.page="pasteboard";}
  try{o.font=String(tf.texts[0].appliedFont.fontFamily);o.direction=String(tf.texts[0].characterDirection);o.composer=String(tf.texts[0].composer);}catch(e){}
  info.texts.push(o);}
 info.links=[];for(var i=0;i<doc.links.length&&i<300;i++){var k=doc.links[i];info.links.push({name:String(k.name),path:String(k.filePath),status:String(k.status)});}
 info.swatches=[];for(var i=0;i<doc.swatches.length&&i<300;i++){var s=doc.swatches[i];info.swatches.push({name:s.name});}
 info.paragraph_styles=doc.paragraphStyles.length;info.character_styles=doc.characterStyles.length;
 return info;
});
""" + TAIL

EXPORT_JSX = PRELUDE + "var SRC=__SRC__,OUT=__OUT__,PNG=__PNG__,PRESET=__PRESET__,PAGES=__PAGES__;" + HEAD + PNG_FN + OPEN + r"""
step("export",function(){
 if(!doc)throw "the file did not open";
 var pr=app.pdfExportPresets.itemByName(PRESET);
 if(!pr.isValid)throw "InDesign has no PDF preset named "+PRESET;
 if(PAGES){app.pdfExportPreferences.pageRange=PAGES;}else{app.pdfExportPreferences.pageRange=PageRange.ALL_PAGES;}
 try{app.pdfExportPreferences.viewPDF=false;}catch(e){}
 doc.exportFile(ExportFormat.PDF_TYPE,new File(OUT),false,pr);
 var first=1;if(PAGES){var m=String(PAGES).match(/\d+/);if(m)first=parseInt(m[0],10);}
 png(doc,first,PNG);
 return {pages:doc.pages.length,out:OUT,png:PNG,preset:PRESET,page_range:PAGES||"all"};
});
""" + TAIL

NEW_JSX = PRELUDE + "var OUT=__OUT__,PNG=__PNG__,W1=__WIDTH__,H1=__HEIGHT__,UNIT=__UNIT__,BLEED=__BLEED__,MARGIN=__MARGIN__,PAGES=__PAGES__,RTL=__RTL__;" + HEAD + r"""
var doc=null;
step("create",function(){
 doc=app.documents.add(false);
 var mu=(UNIT==="mm")?MeasurementUnits.MILLIMETERS:MeasurementUnits.POINTS;
 doc.viewPreferences.horizontalMeasurementUnits=mu;doc.viewPreferences.verticalMeasurementUnits=mu;
 var dp=doc.documentPreferences;dp.facingPages=false;dp.pageWidth=W1;dp.pageHeight=H1;dp.pagesPerDocument=PAGES;
 if(BLEED>0){dp.documentBleedUniformSize=true;dp.documentBleedTopOffset=BLEED;}
 var mp=doc.marginPreferences;mp.top=MARGIN;mp.bottom=MARGIN;mp.left=MARGIN;mp.right=MARGIN;
 for(var i=0;i<doc.pages.length;i++){var p=doc.pages[i].marginPreferences;p.top=MARGIN;p.bottom=MARGIN;p.left=MARGIN;p.right=MARGIN;}
 for(var i=0;i<doc.masterSpreads[0].pages.length;i++){var q=doc.masterSpreads[0].pages[i].marginPreferences;q.top=MARGIN;q.bottom=MARGIN;q.left=MARGIN;q.right=MARGIN;}
 var r={rtl:{}};
 if(RTL){
  try{dp.pageBinding=PageBindingOptions.RIGHT_TO_LEFT;r.rtl.binding="set";}catch(e){r.rtl.binding="not available: "+e;}
  var ps=doc.paragraphStyles.itemByName("[Basic Paragraph]");
  try{ps.paragraphDirection=ParagraphDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.rtl.paragraph="set";}catch(e){r.rtl.paragraph="not available: "+e;}
  try{ps.characterDirection=CharacterDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.rtl.character="set";}catch(e){r.rtl.character="not available: "+e;}
  try{ps.composer="Adobe World-Ready Paragraph Composer";r.rtl.composer="set";}catch(e){r.rtl.composer="not available: "+e;}
  try{ps.justification=Justification.RIGHT_ALIGN;r.rtl.alignment="set";}catch(e){r.rtl.alignment="not available: "+e;}
 }
 doc.save(new File(OUT));
 r.pages=doc.pages.length;r.width=dp.pageWidth;r.height=dp.pageHeight;r.bleed=dp.documentBleedTopOffset;r.margin=mp.top;r.unit=UNIT;r.binding=String(dp.pageBinding);r.out=OUT;
 return r;
});
""" + TAIL

PLACE_JSX = PRELUDE + ("var SRC=__SRC__,OUT=__OUT__,PNG=__PNG__,PAGE=__PAGE__,TEXT=__TEXT__,TBOX=__TBOX__,FAMS=__FAMS__,SIZE=__SIZE__,RTL=__RTL__,LTR=__LTR__,IMG=__IMG__,IBOX=__IBOX__,FIT=__FIT__;" + HEAD + FONT_FN + RTL_FN + PNG_FN + OPEN) + r"""
step("place",function(){
 if(!doc)throw "the file did not open";
 if(PAGE<1||PAGE>doc.pages.length)throw "the file has "+doc.pages.length+" page(s); page "+PAGE+" does not exist";
 var page=doc.pages[PAGE-1];var r={};
 if(TEXT){
  var tf=page.textFrames.add();tf.geometricBounds=[TBOX[1],TBOX[0],TBOX[1]+TBOX[3],TBOX[0]+TBOX[2]];tf.contents=TEXT;
  var tx=tf.texts[0];tx.pointSize=SIZE;var used="";
  for(var i=0;i<FAMS.length&&!used;i++){var f=findFont(FAMS[i]);if(f){try{tx.appliedFont=f;used=FAMS[i];}catch(e){}}}
  r.font=used||"(the document's default font)";
  if(RTL){r.rtl=rtlText(tx);}else if(LTR){r.ltr=ltrText(tx);}
  r.read_back_equals_sent=(tf.contents===TEXT);r.overset=tf.overflows;
 }
 if(IMG){
  var rc=page.rectangles.add();rc.geometricBounds=[IBOX[1],IBOX[0],IBOX[1]+IBOX[3],IBOX[0]+IBOX[2]];rc.strokeWeight=0;
  rc.place(new File(IMG));
  rc.fit(FIT==="fill"?FitOptions.FILL_PROPORTIONALLY:FitOptions.PROPORTIONALLY);rc.fit(FitOptions.CENTER_CONTENT);
  r.image="placed";
 }
 doc.save(new File(OUT));
 png(doc,PAGE,PNG);
 r.out=OUT;r.png=PNG;return r;
});
""" + TAIL


ASSEMBLE_JSX = PRELUDE + ("var OUT=__OUT__,PNG=__PNG__,IMGS=__IMGS__,NAMES=__NAMES__,TEXT=__TEXT__,FAMS=__FAMS__,SIZE=__SIZE__,COLOR=__COLOR__,POS=__POS__,W1=__WIDTH__,H1=__HEIGHT__,UNIT=__UNIT__,BLEED=__BLEED__,MARGIN=__MARGIN__,RTL=__RTL__;" + HEAD + FONT_FN + RTL_FN + PNG_FN) + r"""
var doc=null;
step("assemble",function(){
 doc=app.documents.add(false);
 var mu=(UNIT==="mm")?MeasurementUnits.MILLIMETERS:MeasurementUnits.POINTS;
 doc.viewPreferences.horizontalMeasurementUnits=mu;doc.viewPreferences.verticalMeasurementUnits=mu;
 var dp=doc.documentPreferences;dp.facingPages=false;dp.pageWidth=W1;dp.pageHeight=H1;dp.pagesPerDocument=1;
 if(BLEED>0){dp.documentBleedUniformSize=true;dp.documentBleedTopOffset=BLEED;}
 var mp=doc.marginPreferences;mp.top=MARGIN;mp.bottom=MARGIN;mp.left=MARGIN;mp.right=MARGIN;
 var r={rtl:{},scales:[],embedded:true};
 if(RTL){
  try{dp.pageBinding=PageBindingOptions.RIGHT_TO_LEFT;r.rtl.binding="set";}catch(e){r.rtl.binding="not available: "+e;}
  var ps=doc.paragraphStyles.itemByName("[Basic Paragraph]");
  try{ps.paragraphDirection=ParagraphDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.rtl.paragraph="set";}catch(e){r.rtl.paragraph="not available: "+e;}
  try{ps.characterDirection=CharacterDirectionOptions.RIGHT_TO_LEFT_DIRECTION;r.rtl.character="set";}catch(e){r.rtl.character="not available: "+e;}
  try{ps.composer="Adobe World-Ready Paragraph Composer";r.rtl.composer="set";}catch(e){r.rtl.composer="not available: "+e;}
 }
 var page=doc.pages[0];
 for(var n=0;n<IMGS.length;n++){
  var ly=(n===0)?doc.layers[0]:doc.layers.add();ly.name=NAMES[n];
  var rc=page.rectangles.add({itemLayer:ly});rc.geometricBounds=[0,0,H1,W1];rc.strokeWeight=0;rc.name=NAMES[n];
  rc.place(new File(IMGS[n]));
  rc.fit(FitOptions.FILL_PROPORTIONALLY);rc.fit(FitOptions.CENTER_CONTENT);
  try{rc.graphics[0].itemLink.unlink();}catch(e){r.embedded="not embedded: "+e;}
 }
 if(TEXT){
  var lyT=doc.layers.add();lyT.name="Text";
  var k=(UNIT==="mm")?0.3528:1;var fh=Math.min(SIZE*k*3,H1*0.8);var top;
  if(POS==="top")top=H1*0.08;else if(POS==="bottom")top=H1-H1*0.08-fh;else top=(H1-fh)/2;
  var tf=page.textFrames.add({itemLayer:lyT});tf.name="Title";tf.geometricBounds=[top,W1*0.06,top+fh,W1*0.94];tf.contents=TEXT;
  var tx=tf.texts[0];tx.pointSize=SIZE;var used="";
  for(var i=0;i<FAMS.length&&!used;i++){var f=findFont(FAMS[i]);if(f){try{tx.appliedFont=f;used=FAMS[i];}catch(e){}}}
  r.font=used||"(the document's default font)";
  if(RTL){r.rtl.text=rtlText(tx);}
  tx.justification=Justification.CENTER_ALIGN;
  var col=doc.colors.add({name:"Card text",model:ColorModel.PROCESS,space:ColorSpace.RGB,colorValue:[COLOR[0],COLOR[1],COLOR[2]]});tx.fillColor=col;
  r.read_back_equals_sent=(tf.contents===TEXT);r.overset=tf.overflows;
 }
 r.layers=[];for(var j=0;j<doc.layers.length;j++){r.layers.push(doc.layers[j].name+(doc.layers[j].locked?" (locked)":""));}
 r.links=doc.links.length;
 doc.save(new File(OUT));
 png(doc,1,PNG);
 r.out=OUT;r.png=PNG;r.width=dp.pageWidth;r.height=dp.pageHeight;r.unit=UNIT;return r;
});
""" + TAIL


def build_jsx(kind, result, **p):
    if kind == "inspect":
        t = INSPECT_JSX.replace("__SRC__", _lit(_fwd(p["src"])))
    elif kind == "export":
        t = (EXPORT_JSX.replace("__SRC__", _lit(_fwd(p["src"]))).replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit(_fwd(p["png"])))
             .replace("__PRESET__", _lit(p["preset"])).replace("__PAGES__", _lit(p.get("pages") or "")))
    elif kind == "new":
        t = (NEW_JSX.replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit("")).replace("__WIDTH__", repr(float(p["width"]))).replace("__HEIGHT__", repr(float(p["height"])))
             .replace("__UNIT__", _lit(p["unit"])).replace("__BLEED__", repr(float(p["bleed"]))).replace("__MARGIN__", repr(float(p["margin"]))).replace("__PAGES__", str(int(p["pages"])))
             .replace("__RTL__", "true" if p["rtl"] else "false"))
    elif kind == "place":
        t = (PLACE_JSX.replace("__SRC__", _lit(_fwd(p["src"]))).replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit(_fwd(p["png"]))).replace("__PAGE__", str(int(p["page"])))
             .replace("__TEXT__", _lit(p.get("text") or "")).replace("__TBOX__", json.dumps(p.get("tbox") or [0, 0, 0, 0])).replace("__FAMS__", json.dumps(p.get("fams") or [], ensure_ascii=True))
             .replace("__SIZE__", repr(float(p.get("size") or 12))).replace("__RTL__", "true" if p.get("rtl") else "false").replace("__LTR__", "true" if p.get("ltr") else "false").replace("__IMG__", _lit(_fwd(p["img"]) if p.get("img") else ""))
             .replace("__IBOX__", json.dumps(p.get("ibox") or [0, 0, 0, 0])).replace("__FIT__", _lit(p.get("fit") or "fit")))
    elif kind == "assemble":
        t = (ASSEMBLE_JSX.replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit(_fwd(p["png"]))).replace("__IMGS__", json.dumps([_fwd(x) for x in p["imgs"]], ensure_ascii=True))
             .replace("__NAMES__", json.dumps(p["names"], ensure_ascii=True)).replace("__TEXT__", _lit(p.get("text") or "")).replace("__FAMS__", json.dumps(p.get("fams") or [], ensure_ascii=True))
             .replace("__SIZE__", repr(float(p["size"]))).replace("__COLOR__", json.dumps(p["color"])).replace("__POS__", _lit(p["position"]))
             .replace("__WIDTH__", repr(float(p["width"]))).replace("__HEIGHT__", repr(float(p["height"]))).replace("__UNIT__", _lit(p["unit"]))
             .replace("__BLEED__", repr(float(p["bleed"]))).replace("__MARGIN__", repr(float(p["margin"]))).replace("__RTL__", "true" if p.get("rtl") else "false"))
    else:
        raise ValueError(kind)
    return t.replace("__RES__", _lit(_fwd(result)))


def default_transport(jsx_text, workdir, result_path, timeout=design_com.WORKER_TIMEOUT):
    try:
        return design_com.run_script("indesign", jsx_text, workdir, result_path, timeout)
    except design_com.AppError as e:
        raise InDesignError(str(e))


TRANSPORT = default_transport


def _call(jsx_text, workdir, result_path):
    res = TRANSPORT(jsx_text, workdir, result_path)
    try:
        return design_com.check_errors(res, "InDesign")
    except design_com.AppError as e:
        raise InDesignError(str(e))


# ---- planning helpers (pure, tested) ----

def _prefs():
    try:
        return PREF.load()
    except (OSError, ValueError):
        return None


def _setting(key, default):
    p = _prefs()
    v = PREF.setting(p, key) if p else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def preset_dims(preset):
    """(unit, width, height) InDesign should use: millimetres for print sizes, points for screen sizes (1 px = 1 pt at 72 ppi). Pixel presets at 150 dpi or more are print-sized."""
    u, w, h, dpi = preset["unit"], float(preset["width"]), float(preset["height"]), float(preset["dpi"])
    if u == "mm":
        return "mm", w, h
    if u == "in":
        return "mm", round(w * 25.4, 1), round(h * 25.4, 1)
    if u == "pt":
        return "pt", w, h
    if dpi >= 150:
        return "mm", round(w / dpi * 25.4, 1), round(h / dpi * 25.4, 1)
    return "pt", w, h


def parse_box(text, what):
    """'x,y,w,h' in millimetres from the page's top left -> [x, y, w, h]. Refuses anything else with a sentence."""
    parts = [x.strip() for x in str(text).split(",")]
    try:
        v = [float(x) for x in parts]
    except ValueError:
        v = []
    if len(v) != 4 or v[0] < 0 or v[1] < 0 or v[2] <= 0 or v[3] <= 0 or max(v) > 5000:
        raise R.Refused(f"The {what} must be four numbers in millimetres: x, y, width, height (for example 20,20,120,40), measured from the page's top left.")
    return v


def has_arabic(text):
    return bool(ARABIC_RE.search(text or ""))


def font_candidates(requested, arabic):
    if requested:
        return [requested]
    p = _prefs()
    pref = []
    if p:
        for f in p["fonts"]:
            if f["use"] == "preferred" and (f["script"] in ("arabic", "both") if arabic else f["script"] in ("latin", "both")):
                pref.append(f["family"])
    return pref + (ARABIC_FONTS if arabic else LATIN_FONTS)


def _check_source(path):
    if os.path.splitext(path)[1].lower() not in SOURCE_EXT:
        raise R.Refused("This operation opens InDesign files only (" + ", ".join(SOURCE_EXT) + ").")


def _stem(path):
    try:
        return design_naming.slug(os.path.splitext(os.path.basename(path))[0], "file")
    except design_naming.BadName:
        return "File"


def _find_preset(pid):
    p = _prefs()
    if not p:
        raise R.Refused("The preferences profile could not be read, so the document sizes are not known.")
    for r in p["document_presets"]:
        if r["id"] == pid:
            return r
    raise R.Refused(f"'{pid}' is not one of your document presets. Your presets: " + ", ".join(r["id"] for r in p["document_presets"]) + ".")


def _read(ctx, kind, **p):
    res_path = os.path.join(ctx["staging"], "id_result.json")
    res = _call(build_jsx(kind, res_path, **p), ctx["staging"], res_path)
    name = {"inspect": "inspect", "export": "export", "new": "create", "place": "place", "assemble": "assemble"}[kind]
    step = (res.get("steps") or {}).get(name)
    if not isinstance(step, dict) or "error" in step:
        raise InDesignError("InDesign could not do it: " + str((step or {}).get("error", "no answer"))[:250])
    return step


def _preview(ctx, png):
    if not os.path.isfile(png) or os.path.getsize(png) == 0:
        raise InDesignError("InDesign said it made the picture of the page but the file is not there")
    return png


# ---- inspect ----

def _inspect_outputs(p):
    _check_source(p["source"])
    return [f"{_stem(p['source'])}_IdInspect_v01.json"]


def _inspect_run(ctx):
    name = ctx["outputs_expected"][0]
    info = _read(ctx, "inspect", src=ctx["inputs"]["source"])
    out = os.path.join(ctx["staging"], name)
    json.dump(info, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    missing = [k for k in info.get("links", []) if "MISSING" in k.get("status", "").upper()]
    bad_fonts = [f for f in info.get("fonts", []) if "INSTALLED" not in f.get("status", "").upper()]
    return {"outputs": {name: out}, "notes": [f"{info.get('pages')} page(s), {info.get('width_mm')} x {info.get('height_mm')} mm, {len(info.get('texts', []))} text frame(s), {len(missing)} missing link(s), {len(bad_fonts)} font(s) not installed"]}


# ---- export a PDF ----

def _export_outputs(p):
    _check_source(p["source"])
    if p.get("pages") and not re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", p["pages"].replace(" ", "")):
        raise R.Refused("The pages must be like 1 or 1-3 or 1,3,5-6.")
    return [f"{_stem(p['source'])}_IdPdf_v01.pdf"]


def _export_steps(p):
    return ["Copy the file into the project's 02_Assets folder (the original is never opened)",
            f"Open the COPY in InDesign (no window, alerts off) and export {'pages ' + p['pages'] if p.get('pages') else 'all pages'} as a PDF with InDesign's own preset {PDF_PRESETS[p['preset']]}",
            "Save the PDF in 05_Exports and a PNG of the first exported page as the preview; close the document without saving"]


def _export_run(ctx):
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    png = os.path.join(ctx["staging"], "preview.png")
    step = _read(ctx, "export", src=p["source"], out=out, png=png, preset=PDF_PRESETS[p["preset"]], pages=(p.get("pages") or "").replace(" ", ""))
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise InDesignError("InDesign said it exported the PDF but the file is not there")
    return {"outputs": {name: out}, "preview": _preview(ctx, png), "notes": [f"{step.get('pages')} page(s) in the file; preset {step.get('preset')}"]}


# ---- new document ----

def _new_outputs(p):
    _find_preset(p["preset"])
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_IdDoc_v01.indd"]


def _new_steps(p):
    pr = _find_preset(p["preset"])
    unit, w, h = preset_dims(pr)
    bleed, margin = _setting("print.bleed_mm", 3) if unit == "mm" else 0, _setting("print.margin_mm", 10) if unit == "mm" else 0
    return [f"Open a new document in InDesign (no window): {p['pages']} page(s) of {w:g} x {h:g} {unit} ({pr['name']})",
            f"Bleed {bleed:g} mm and margins {margin:g} mm on every side (from your preferences; screen sizes get none)" + ("; page binding right to left, and the basic paragraph style set to right-to-left with the World-Ready composer" if p.get("rtl") else ""),
            "Save it as a new InDesign file in 05_Exports (nothing is opened or changed on disk besides that file); close it"]


def _new_run(ctx):
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    pr = _find_preset(p["preset"])
    unit, w, h = preset_dims(pr)
    bleed, margin = (_setting("print.bleed_mm", 3), _setting("print.margin_mm", 10)) if unit == "mm" else (0, 0)
    step = _read(ctx, "new", out=out, width=w, height=h, unit=unit, bleed=bleed, margin=margin, pages=p["pages"], rtl=bool(p.get("rtl")))
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise InDesignError("InDesign said it saved the document but the file is not there")
    bad = [k for k, v in (step.get("rtl") or {}).items() if str(v).startswith("not available")]
    return {"outputs": {name: out}, "notes": [f"{step.get('pages')} page(s), {step.get('width')} x {step.get('height')} {step.get('unit')}, bleed {step.get('bleed')}, margins {step.get('margin')}" + (f"; right-to-left settings NOT available: {', '.join(bad)}" if bad else "")]}


# ---- place content ----

def _place_outputs(p):
    _check_source(p["source"])
    if not p.get("text") and not p.get("image"):
        raise R.Refused("Give a text, a picture, or both: there is nothing to place.")
    if p.get("image") and os.path.splitext(p["image"])[1].lower() not in IMAGE_EXT:
        raise R.Refused("The picture must be one of " + ", ".join(IMAGE_EXT) + ".")
    if p.get("text"):
        parse_box(p.get("text_box", "20,20,150,50"), "text box")
    if p.get("image"):
        parse_box(p.get("image_box", "20,80,100,100"), "picture box")
    return [f"{_stem(p['source'])}_IdPlaced_v01.indd"]


def _place_steps(p):
    L = ["Copy the file (and the picture) into the project's 02_Assets folder (the originals are never opened)", f"Open the COPY in InDesign (no window, alerts off) and go to page {p['page']}"]
    if p.get("text"):
        ar = has_arabic(p["text"]) if p.get("direction", "auto") == "auto" else p["direction"] == "rtl"
        L.append(f"Add a text frame at {p.get('text_box', '20,20,150,50')} mm ({p['size']} pt, font: {font_candidates(p.get('font'), has_arabic(p['text']))[0]}) with your text"
                 + ("; the text is right to left: character and paragraph direction, the World-Ready composer and right alignment are set" if ar else ""))
    if p.get("image"):
        L.append(f"Add a picture frame at {p.get('image_box', '20,80,100,100')} mm and place the picture, " + ("filling the frame (cropping)" if p["fit"] == "fill" else "fitting it inside the frame") + ", centred")
    L.append("Save the result as a NEW InDesign file in 05_Exports and a PNG of that page as the preview; the copy is closed without saving, so it stays as it was")
    return L


def _place_run(ctx):
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    png = os.path.join(ctx["staging"], "preview.png")
    text = p.get("text") or ""
    d = p.get("direction", "auto")
    rtl = bool(text) and (has_arabic(text) if d == "auto" else d == "rtl")
    step = _read(ctx, "place", src=p["source"], out=out, png=png, page=p["page"], text=text, tbox=parse_box(p.get("text_box", "20,20,150,50"), "text box") if text else None,
                 fams=font_candidates(p.get("font"), has_arabic(text)) if text else [], size=p["size"], rtl=rtl, ltr=bool(text) and d == "ltr", img=p.get("image"), ibox=parse_box(p.get("image_box", "20,80,100,100"), "picture box") if p.get("image") else None, fit=p["fit"])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise InDesignError("InDesign said it saved the file but it is not there")
    notes = []
    if text:
        if step.get("read_back_equals_sent") is False:
            raise InDesignError("The text InDesign holds is not the text that was sent")
        if step.get("overset"):
            notes.append("WARNING: the text does not fit its frame (overset); make the box bigger")
        bad = [k for k, v in (step.get("rtl") or {}).items() if str(v).startswith("not available")]
        if bad:
            notes.append(f"right-to-left settings NOT available: {', '.join(bad)}")
        notes.append(f"font {step.get('font')}")
    if p.get("image"):
        notes.append("picture placed")
    return {"outputs": {name: out}, "preview": _preview(ctx, png), "notes": notes}


INSPECT = {"id": "indesign.inspect", "app": "indesign", "title": "InDesign: read a file (pages, fonts, text frames, links, swatches)", "params": {"source": {"type": "file"}},
           "plan": lambda p: ["Copy the file into the project's 02_Assets folder (the original is never opened)", "Open the COPY in InDesign (no window, alerts off) and read it; change and save nothing",
                              "Write the report as a JSON file in 05_Exports; close the document without saving"], "outputs": _inspect_outputs, "run": _inspect_run}
EXPORT = {"id": "indesign.export_pdf", "app": "indesign", "title": "InDesign: export a PDF with one of InDesign's own presets",
          "params": {"source": {"type": "file"}, "preset": {"type": "enum", "values": list(PDF_PRESETS), "required": False, "default": "high_quality"}, "pages": {"type": "str", "required": False}},
          "plan": _export_steps, "outputs": _export_outputs, "run": _export_run}
NEW = {"id": "indesign.new_document", "app": "indesign", "title": "InDesign: a new document from one of your document presets",
       "params": {"preset": {"type": "str"}, "name": {"type": "str"}, "pages": {"type": "int", "min": 1, "max": 200, "required": False, "default": 1}, "rtl": {"type": "bool", "required": False, "default": False}},
       "plan": _new_steps, "outputs": _new_outputs, "run": _new_run}
PLACE = {"id": "indesign.place_content", "app": "indesign", "title": "InDesign: place text and/or a picture on a page (saved as a new file)",
         "params": {"source": {"type": "file"}, "page": {"type": "int", "min": 1, "max": 500, "required": False, "default": 1}, "text": {"type": "str", "required": False},
                    "text_box": {"type": "str", "required": False, "default": "20,20,150,50"}, "font": {"type": "str", "required": False}, "size": {"type": "int", "min": 6, "max": 400, "required": False, "default": 24},
                    "direction": {"type": "enum", "values": ["auto", "rtl", "ltr"], "required": False, "default": "auto"}, "image": {"type": "file", "required": False},
                    "image_box": {"type": "str", "required": False, "default": "20,80,100,100"}, "fit": {"type": "enum", "values": ["fit", "fill"], "required": False, "default": "fit"}},
         "plan": _place_steps, "outputs": _place_outputs, "run": _place_run}


# ---- assemble layers ----

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _asm_check(p):
    import design_illustrator as AI
    for pic in p["layers"]:
        if os.path.splitext(pic)[1].lower() not in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            raise R.Refused("The pictures must be .png, .jpg, .jpeg, .tif or .tiff files.")
    if p.get("text") is not None and (not p["text"].strip() or len(p["text"]) > 200):
        raise R.Refused("The text must be between 1 and 200 characters.")
    if not COLOR_RE.match(p.get("color", "#FFFFFF")):
        raise R.Refused("The colour must be written like #F2C94C.")
    pr = _find_preset(p["preset"])
    AI.layer_names(p, len(p["layers"]))
    return pr


def _asm_outputs(p):
    _asm_check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_IdCard_v01.indd"]


def _asm_steps(p):
    import design_illustrator as AI
    pr = _asm_check(p)
    unit, w, h = preset_dims(pr)
    names = AI.layer_names(p, len(p["layers"]))
    L = ["Copy the pictures into the project's 02_Assets folder (the originals are never opened)",
         f"Open a new InDesign document (no window): one page of {w:g} x {h:g} {unit} ({pr['name']})",
         "Put each picture on its own layer, bottom first: " + ", ".join(f"'{n}'" for n in names) + "; each fills the whole page evenly (never stretched), is embedded so no link can go missing, and nothing is locked"]
    if p.get("text"):
        ar = has_arabic(p["text"])
        L.append(f"Add one live text frame on its own layer 'Text' ({p['position']}), {p['size']} pt, centred, in a colour swatch 'Card text' ({p.get('color', '#FFFFFF')}); you can edit the words and move everything"
                 + ("; the text is Arabic: right to left, World-Ready composer, page binding right to left" if ar else ""))
    L.append("Save it as a new InDesign file in 05_Exports and a PNG of the page as the preview; close it")
    return L


def _asm_run(ctx):
    import design_illustrator as AI
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    png = os.path.join(ctx["staging"], "preview.png")
    pr = _asm_check(p)
    unit, w, h = preset_dims(pr)
    bleed, margin = (_setting("print.bleed_mm", 3), _setting("print.margin_mm", 10)) if unit == "mm" else (0, 0)
    imgs = ctx["inputs"]["layers"]
    names = AI.layer_names(p, len(imgs))
    text = p.get("text") or ""
    ar = has_arabic(text)
    c = p.get("color", "#FFFFFF").lstrip("#")
    step = _read(ctx, "assemble", out=out, png=png, imgs=imgs, names=names, text=text, fams=font_candidates(p.get("font"), ar) if text else [], size=p["size"],
                 color=[int(c[i:i + 2], 16) for i in (0, 2, 4)], position=p["position"], width=w, height=h, unit=unit, bleed=bleed, margin=margin, rtl=bool(text) and ar)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise InDesignError("InDesign said it saved the document but the file is not there")
    if text and step.get("read_back_equals_sent") is False:
        raise InDesignError("The text InDesign holds is not the text that was sent")
    notes = [f"{pr['name']}; {len(imgs)} picture layer(s)" + (f", font {step.get('font')}" if text else "") + f"; layers: {', '.join(step.get('layers') or [])}"]
    if any("locked" in x for x in step.get("layers") or []):
        notes.append("WARNING: a layer came out locked")
    if text and step.get("overset"):
        notes.append("WARNING: the text does not fit its frame (overset); use a smaller size or fewer words")
    bad = [k for k, v in (step.get("rtl") or {}).items() if isinstance(v, str) and v.startswith("not available")]
    if bad:
        notes.append(f"right-to-left settings NOT available: {', '.join(bad)}")
    if step.get("embedded") is not True:
        notes.append("a picture could not be embedded (it stays linked to the copy in 02_Assets)")
    return {"outputs": {name: out}, "preview": _preview(ctx, png), "notes": notes}


ASSEMBLE = {"id": "indesign.assemble_layers", "app": "indesign", "title": "InDesign: a layered page from several pictures (one layer each, bottom first) and optional live text",
            "params": {"layers": {"type": "files", "max": 12}, "names": {"type": "str", "required": False}, "text": {"type": "str", "required": False}, "preset": {"type": "str"}, "name": {"type": "str"},
                       "font": {"type": "str", "required": False}, "size": {"type": "int", "min": 6, "max": 600, "required": False, "default": 120}, "color": {"type": "str", "required": False, "default": "#FFFFFF"},
                       "position": {"type": "enum", "values": ["top", "center", "bottom"], "required": False, "default": "center"}},
            "plan": _asm_steps, "outputs": _asm_outputs, "run": _asm_run}


def register():
    for op in (INSPECT, EXPORT, NEW, PLACE, ASSEMBLE):
        if op["id"] not in R.OPERATIONS:
            R.register(op)
