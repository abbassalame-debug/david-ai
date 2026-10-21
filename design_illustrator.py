"""design_illustrator.py -- the fixed Illustrator operations of the Design portal (Piece 4). The scripts are written here, by hand, and tested; a model never writes one.

  illustrator.inspect   read a file: artboards, layers, text frames and their fonts, placed files (and which are missing), swatches (nothing is changed or saved)
  illustrator.export    one artboard as a PNG (never enlarged, white background), the whole document as an SVG (text kept as outlines, so no font is needed), or as a PDF
  illustrator.compose_card  a NEW document from one of the designer's presets: a picture placed to cover the page and one text line (Arabic joined and right to left)
  illustrator.audit     the inspect result checked against the designer's preferences: fonts not in the profile or marked "avoid", swatches outside his palettes,
                        missing links, Arabic text (with an honest warning when this Illustrator is known not to shape Arabic)

Same safety as every operation: the script opens the COPY the runner made (never the original), works on that copy, saves only the exported file into the staging folder
and closes everything without saving. The app is reached by design_com (a worker process with a timeout).
"""
import os
import re
import json

import design_com
import design_naming
import design_ops
import design_preferences as PREF
import design_runner as R
from design_handshake import PRELUDE

SOURCE_EXT = (".ai", ".eps", ".svg")
ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F]")
DEFAULT_SWATCH = re.compile(r"^(C=\d+ M=\d+ Y=\d+ K=\d+|R=\d+ G=\d+ B=\d+)$")


class IllustratorError(design_com.AppError):
    pass


def _lit(v):
    return json.dumps(v, ensure_ascii=True)


def _fwd(p):
    return p.replace("\\", "/")


COMMON_HEAD = r"""
var SRC=__SRC__,OUT=__OUT__;
R.app="illustrator";
var prevUI=app.userInteractionLevel;app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
var doc=null;
try{doc=app.open(new File(SRC));}catch(e){R.errors.push("open: "+e);}
"""
COMMON_TAIL = r"""
try{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);}catch(e){}
app.userInteractionLevel=prevUI;
W(__RES__,R);
"""

INSPECT_JSX = PRELUDE + COMMON_HEAD + r"""
function r2(x){return Math.round(x*100)/100;}
step("inspect",function(){
 if(!doc)throw "the file did not open";
 var info={name:doc.name,color_space:String(doc.documentColorSpace),width_pt:r2(doc.width),height_pt:r2(doc.height)};
 info.artboards=[];
 for(var i=0;i<doc.artboards.length;i++){var ab=doc.artboards[i];var r=ab.artboardRect;info.artboards.push({index:i+1,name:ab.name,width:r2(r[2]-r[0]),height:r2(r[1]-r[3]),rect:[r2(r[0]),r2(r[1]),r2(r[2]),r2(r[3])]});}
 info.layers=[];
 for(var i=0;i<doc.layers.length&&i<100;i++){var l=doc.layers[i];info.layers.push({name:l.name,visible:l.visible,locked:l.locked,items:l.pageItems.length});}
 info.counts={paths:doc.pathItems.length,compound_paths:doc.compoundPathItems.length,groups:doc.groupItems.length,raster:doc.rasterItems.length,symbols:doc.symbolItems.length,placed:doc.placedItems.length,text_frames:doc.textFrames.length};
 info.texts=[];var fonts={};var budget=20000;
 for(var i=0;i<doc.textFrames.length&&i<300;i++){
  var tf=doc.textFrames[i];var o={contents:String(tf.contents).substr(0,120),kind:String(tf.kind),fonts:[],composer:""};
  try{o.composer=String(tf.textRange.paragraphAttributes.composerEngine);}catch(e){}
  try{var chars=tf.textRange.characters;var n=Math.min(chars.length,600);var prev="";
   for(var j=0;j<n&&budget>0;j++,budget--){var f=chars[j].characterAttributes.textFont;var nm=String(f.name);if(nm===prev)continue;prev=nm;if(!fonts[nm]){fonts[nm]=String(f.family);}var seen=false;for(var k=0;k<o.fonts.length;k++){if(o.fonts[k]===nm)seen=true;}if(!seen)o.fonts.push(nm);}}catch(e){}
  info.texts.push(o);
 }
 info.fonts=[];for(var nm in fonts){info.fonts.push({name:nm,family:fonts[nm]});}
 info.placed=[];
 for(var i=0;i<doc.placedItems.length&&i<200;i++){var p=doc.placedItems[i];var o={path:"",exists:false};try{o.path=String(p.file.fsName);o.exists=p.file.exists;}catch(e){o.exists=false;}info.placed.push(o);}
 info.swatches=[];
 for(var i=0;i<doc.swatches.length&&i<300;i++){
  var sw=doc.swatches[i];var c=sw.color;var t=c.typename;var o={name:sw.name,type:t};
  try{if(t==="CMYKColor"){o.c=r2(c.cyan);o.m=r2(c.magenta);o.y=r2(c.yellow);o.k=r2(c.black);}
   else if(t==="RGBColor"){o.r=Math.round(c.red);o.g=Math.round(c.green);o.b=Math.round(c.blue);}
   else if(t==="SpotColor"){o.spot=c.spot.name;o.tint=r2(c.tint);}
   else if(t==="GrayColor"){o.gray=r2(c.gray);}}catch(e){}
  info.swatches.push(o);
 }
 return info;
});
""" + COMMON_TAIL

EXPORT_JSX = PRELUDE + COMMON_HEAD + r"""
var FMT=__FMT__,AB=__AB__,MAXSIDE=__MAXSIDE__;
step("export",function(){
 if(!doc)throw "the file did not open";
 if(AB<1||AB>doc.artboards.length)throw "the file has "+doc.artboards.length+" artboard(s); artboard "+AB+" does not exist";
 var r={artboards:doc.artboards.length,out:OUT};
 if(FMT==="png"){
  doc.artboards.setActiveArtboardIndex(AB-1);
  var rc=doc.artboards[AB-1].artboardRect;var w=rc[2]-rc[0],h=rc[1]-rc[3];var scale=100;
  if(MAXSIDE&&Math.max(w,h)>MAXSIDE){scale=MAXSIDE/Math.max(w,h)*100;}
  var o=new ExportOptionsPNG24();o.antiAliasing=true;o.artBoardClipping=true;o.transparency=false;o.horizontalScale=scale;o.verticalScale=scale;
  doc.exportFile(new File(OUT),ExportType.PNG24,o);
  r.width_px=Math.round(w*scale/100);r.height_px=Math.round(h*scale/100);r.scale_percent=Math.round(scale*100)/100;
 }else if(FMT==="svg"){
  var so=new ExportOptionsSVG();so.embedRasterImages=true;so.fontType=SVGFontType.OUTLINEFONT;so.compressed=false;
  doc.exportFile(new File(OUT),ExportType.SVG,so);
 }else{
  var po=new PDFSaveOptions();po.preserveEditability=false;po.viewAfterSaving=false;
  doc.saveAs(new File(OUT),po);
 }
 return r;
});
""" + COMMON_TAIL


COMPOSE_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG=__PNG__,IMGS=__IMGS__,NAMES=__NAMES__,TEXT=__TEXT__,FAMS=__FAMS__,SIZE=__SIZE__,COLOR=__COLOR__,POS=__POS__,DW=__DW__,DH=__DH__,ARABIC=__ARABIC__;
R.app="illustrator";
var prevUI=app.userInteractionLevel;app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
var doc=null;
step("compose",function(){
 doc=app.documents.add(DocumentColorSpace.RGB,DW,DH);
 var ab=doc.artboards[0].artboardRect;var L=ab[0],T=ab[1];var r={artboard_pt:[DW,DH],scales:[],embedded:true};
 for(var n=0;n<IMGS.length;n++){
  var ly=(n===0)?doc.layers[0]:doc.layers.add();ly.name=NAMES[n];
  var pi=ly.placedItems.add();pi.file=new File(IMGS[n]);pi.name=NAMES[n];
  var sc=Math.max(DW/pi.width,DH/pi.height)*100;pi.resize(sc,sc);
  pi.left=L+(DW-pi.width)/2;pi.top=T-(DH-pi.height)/2;
  r.scales.push(Math.round(sc*10)/10);
  try{pi.embed();}catch(e){r.embedded="not embedded: "+e;}
 }
 r.picture_scale_percent=r.scales[0];
 if(TEXT){
  var fh=Math.min(SIZE*5,DH*0.8);var fy;
  if(POS==="top")fy=T-DH*0.08;else if(POS==="bottom")fy=T-DH+fh+DH*0.08;else fy=T-(DH-fh)/2;
  var lyTx=doc.layers.add();lyTx.name="Text";var rect=lyTx.pathItems.rectangle(fy,L+DW*0.06,DW*0.88,fh);
  var tf=lyTx.textFrames.areaText(rect);tf.name="Title";tf.contents=TEXT;
  var ca=tf.textRange.characterAttributes;ca.size=SIZE;var used="";
  for(var i=0;i<FAMS.length&&!used;i++){for(var j=0;j<app.textFonts.length;j++){var f=app.textFonts[j];if(f.family===FAMS[i]){ca.textFont=f;used=FAMS[i]+" ("+f.name+")";break;}}}
  r.font=used||"(Illustrator's default font)";
  var c=new RGBColor();c.red=COLOR[0];c.green=COLOR[1];c.blue=COLOR[2];ca.fillColor=c;
  tf.textRange.paragraphAttributes.justification=Justification.CENTER;
  if(ARABIC){try{tf.textRange.paragraphAttributes.composerEngine=ComposerEngineType.optycaComposer;r.composer="optyca";}catch(e){r.composer="not available: "+e;}}
  r.read_back_equals_sent=(tf.contents===TEXT);r.lines=tf.lines.length;r.frame_height_pt=Math.round(fh);
 }
 r.layers=[];for(var k=0;k<doc.layers.length;k++){r.layers.push(doc.layers[k].name+(doc.layers[k].locked?" (locked)":""));}
 doc.saveAs(new File(OUT),new IllustratorSaveOptions());
 var o=new ExportOptionsPNG24();o.antiAliasing=true;o.artBoardClipping=true;o.transparency=false;o.horizontalScale=100;o.verticalScale=100;
 doc.exportFile(new File(PNG),ExportType.PNG24,o);
 return r;
});
try{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);}catch(e){}
app.userInteractionLevel=prevUI;
W(__RES__,R);
"""


def build_compose_jsx(result, **p):
    return (COMPOSE_JSX.replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit(_fwd(p["png"]))).replace("__IMGS__", json.dumps([_fwd(x) for x in p["imgs"]], ensure_ascii=True))
            .replace("__NAMES__", json.dumps(p["names"], ensure_ascii=True)).replace("__TEXT__", _lit(p.get("text") or ""))
            .replace("__FAMS__", json.dumps(p["fams"], ensure_ascii=True)).replace("__SIZE__", repr(float(p["size"]))).replace("__COLOR__", json.dumps(p["color"]))
            .replace("__POS__", _lit(p["position"])).replace("__DW__", repr(float(p["width_pt"]))).replace("__DH__", repr(float(p["height_pt"])))
            .replace("__ARABIC__", "true" if p["arabic"] else "false").replace("__RES__", _lit(_fwd(result))))


def build_jsx(kind, src, out, result, **p):
    if kind == "inspect":
        t = INSPECT_JSX
    elif kind == "export":
        t = EXPORT_JSX.replace("__FMT__", _lit(p["fmt"])).replace("__AB__", str(int(p["artboard"]))).replace("__MAXSIDE__", "null" if not p.get("max_side") else str(int(p["max_side"])))
    else:
        raise ValueError(kind)
    return t.replace("__SRC__", _lit(_fwd(src))).replace("__OUT__", _lit(_fwd(out) if out else "")).replace("__RES__", _lit(_fwd(result)))


def default_transport(jsx_text, workdir, result_path, timeout=design_com.WORKER_TIMEOUT):
    try:
        return design_com.run_script("illustrator", jsx_text, workdir, result_path, timeout)
    except design_com.AppError as e:
        raise IllustratorError(str(e))


TRANSPORT = default_transport


def _call(jsx_text, workdir, result_path):
    res = TRANSPORT(jsx_text, workdir, result_path)
    try:
        return design_com.check_errors(res, "Illustrator")
    except design_com.AppError as e:
        raise IllustratorError(str(e))


def _prefs():
    try:
        return PREF.load()
    except (OSError, ValueError):
        return None


def _check_source(path):
    if os.path.splitext(path)[1].lower() not in SOURCE_EXT:
        raise R.Refused("This operation opens Illustrator files only (" + ", ".join(SOURCE_EXT) + ").")


def _stem(path):
    try:
        return design_naming.slug(os.path.splitext(os.path.basename(path))[0], "file")
    except design_naming.BadName:
        return "File"


def _read_inspect(ctx, tag):
    res_path = os.path.join(ctx["staging"], "ai_result.json")
    res = _call(build_jsx("inspect", ctx["inputs"]["source"], "", res_path), ctx["staging"], res_path)
    info = (res.get("steps") or {}).get("inspect")
    if not isinstance(info, dict) or "error" in info:
        raise IllustratorError("Illustrator could not read the file: " + str((info or {}).get("error", "no answer"))[:200])
    return info


# ---- the audit (pure, tested) ----

def _norm(s):
    return re.sub(r"[^a-z0-9\u0600-\u06FF]", "", str(s).lower())


def _hex_of(sw):
    return "#%02X%02X%02X" % (sw["r"], sw["g"], sw["b"])


def build_audit(info, profile, when=""):
    """The inspect result against the designer's profile -> (markdown, findings dict). Findings are what needs his eye; nothing here is changed anywhere."""
    known = {}
    if profile:
        for f in profile.get("fonts", []):
            known[_norm(f["family"])] = f
    findings = {"fonts_not_in_profile": [], "fonts_to_avoid": [], "fonts_known": [], "swatches_outside_palettes": [], "swatches_in_palettes": [], "missing_links": [], "arabic_frames": 0}
    for f in info.get("fonts", []):
        rec = known.get(_norm(f.get("family")))
        if rec is None:
            findings["fonts_not_in_profile"].append(f"{f.get('family')} ({f.get('name')})")
        elif rec.get("use") == "avoid":
            findings["fonts_to_avoid"].append(f.get("family"))
        else:
            findings["fonts_known"].append(f"{f.get('family')} [{rec['status']}]")
    palette_cmyk, palette_hex = [], set()
    for pal in (profile or {}).get("palettes", []):
        for c in pal.get("colors", []):
            if "cmyk" in c:
                palette_cmyk.append(tuple(float(x) for x in c["cmyk"]))
            elif "hex" in c:
                palette_hex.add(c["hex"].upper())
    custom = [s for s in info.get("swatches", []) if not DEFAULT_SWATCH.match(s.get("name", "")) and not str(s.get("name", "")).startswith("[") and s.get("type") in ("CMYKColor", "RGBColor", "SpotColor", "GrayColor")]
    for s in custom:
        label = s["name"]
        if s["type"] == "CMYKColor":
            inside = any(all(abs(a - b) <= 1.0 for a, b in zip((s["c"], s["m"], s["y"], s["k"]), p)) for p in palette_cmyk)
        elif s["type"] == "RGBColor":
            inside = _hex_of(s) in palette_hex
        else:
            inside = False
        (findings["swatches_in_palettes"] if inside else findings["swatches_outside_palettes"]).append(label)
    findings["missing_links"] = [p["path"] or "(a link with no path)" for p in info.get("placed", []) if not p.get("exists")]
    arabic = [t for t in info.get("texts", []) if ARABIC_RE.search(t.get("contents", ""))]
    findings["arabic_frames"] = len(arabic)
    findings["arabic_wrong_composer"] = sum(1 for t in arabic if t.get("composer") and "optyca" not in t["composer"].lower())
    findings["arabic_composer_unknown"] = sum(1 for t in arabic if not t.get("composer"))
    L = [f"# Illustrator audit: {info.get('name', '')}", "", f"Colour mode {info.get('color_space', '?').replace('DocumentColorSpace.', '')}; {len(info.get('artboards', []))} artboard(s); {len(info.get('layers', []))} layer(s); "
         f"{info.get('counts', {}).get('text_frames', 0)} text frame(s); {info.get('counts', {}).get('placed', 0)} placed file(s). Checked against your preferences profile{(' of ' + when) if when else ''}. Nothing was changed.", "", "## Fonts"]
    if not info.get("fonts"):
        L.append("- No text found.")
    L += [f"- OK, in your profile: {x}" for x in findings["fonts_known"]]
    L += [f"- NOT in your profile: {x}" for x in findings["fonts_not_in_profile"]]
    L += [f"- Marked 'avoid' in your profile: {x}" for x in findings["fonts_to_avoid"]]
    L += ["", "## Swatches"]
    if not profile or not (palette_cmyk or palette_hex):
        L.append(f"- Your profile has no palettes yet, so {len(custom)} custom swatch(es) cannot be judged. Add a palette on the Design page.")
    else:
        L += [f"- In a palette of yours: {', '.join(findings['swatches_in_palettes']) or 'none'}", f"- Outside every palette: {', '.join(findings['swatches_outside_palettes']) or 'none'}"]
    L += ["", "## Links"]
    if findings["missing_links"]:
        L += [f"- MISSING: {x}" for x in findings["missing_links"]]
    else:
        L.append("- All placed files are found." if info.get("placed") else "- No placed files.")
    L += ["", "## Arabic"]
    if findings["arabic_frames"] == 0:
        L.append("- No Arabic text in this file.")
    else:
        n, bad, unk = findings["arabic_frames"], findings["arabic_wrong_composer"], findings["arabic_composer_unknown"]
        L.append(f"- {n} text frame(s) contain Arabic.")
        if bad:
            L.append(f"- WARNING: {bad} of them use the Latin paragraph composer, so Illustrator draws their Arabic letters apart. Select them and choose 'Adobe World-Ready Paragraph Composer' in the Paragraph panel menu (frames you type yourself already use it).")
        if unk:
            L.append(f"- {unk} of them: the composer could not be read, so the joining is not checked. Look at them in Illustrator.")
        if not bad and not unk:
            L.append("- All of them use the World-Ready (Optyca) composer, which joins Arabic letters and sets them right to left.")
    return "\n".join(L) + "\n", findings


# ---- the operations ----

def _inspect_outputs(p):
    _check_source(p["source"])
    return [f"{_stem(p['source'])}_AiInspect_v01.json"]


def _inspect_run(ctx):
    name = ctx["outputs_expected"][0]
    info = _read_inspect(ctx, "inspect")
    out = os.path.join(ctx["staging"], name)
    json.dump(info, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    c = info.get("counts", {})
    return {"outputs": {name: out}, "notes": [f"{len(info.get('artboards', []))} artboard(s), {c.get('text_frames', 0)} text frame(s), fonts: {', '.join(f['family'] for f in info.get('fonts', [])) or 'none'}"]}


def _audit_outputs(p):
    _check_source(p["source"])
    return [f"{_stem(p['source'])}_AiAudit_v01.md"]


def _audit_run(ctx):
    name = ctx["outputs_expected"][0]
    info = _read_inspect(ctx, "audit")
    md, findings = build_audit(info, _prefs())
    out = os.path.join(ctx["staging"], name)
    open(out, "w", encoding="utf-8").write(md)
    return {"outputs": {name: out}, "notes": [f"{len(findings['fonts_not_in_profile'])} font(s) not in your profile, {len(findings['missing_links'])} missing link(s), {findings['arabic_frames']} Arabic frame(s), {findings['arabic_wrong_composer']} on the Latin composer"]}


def _export_outputs(p):
    _check_source(p["source"])
    return [f"{_stem(p['source'])}_AiExport_v01.{p['format']}"]


def _export_steps(p):
    fmt = p["format"]
    what = {"png": f"artboard {p['artboard']} as a PNG on a white background" + (f", scaled down if its long side is over {p['max_side']} px (never enlarged)" if p.get("max_side") else " at its own size"),
            "svg": "the whole document as an SVG with the text turned to outlines (no font is needed to show it)", "pdf": "the whole document as a PDF (all artboards; the copy is saved as a PDF, not your original)"}[fmt]
    return ["Copy the file into the project's 02_Assets folder (the original is never opened)", "Open the COPY in Illustrator (alerts off) and export " + what,
            f"Save it as a {fmt.upper()} in 05_Exports" + (" and show it as the preview" if fmt == "png" else "") + "; close the document without saving"]


def _export_run(ctx):
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    res_path = os.path.join(ctx["staging"], "ai_result.json")
    res = _call(build_jsx("export", p["source"], out, res_path, fmt=p["format"], artboard=p["artboard"], max_side=p.get("max_side")), ctx["staging"], res_path)
    step = (res.get("steps") or {}).get("export")
    if not isinstance(step, dict) or "error" in step:
        raise IllustratorError("Illustrator could not export: " + str((step or {}).get("error", "no answer"))[:200])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise IllustratorError("Illustrator said it exported the file but it is not there")
    ret = {"outputs": {name: out}, "notes": [f"{step.get('artboards')} artboard(s)" + (f"; {step.get('width_px')} x {step.get('height_px')} px" if p["format"] == "png" else "")]}
    if p["format"] == "png":
        import shutil
        prev = os.path.join(ctx["staging"], "preview.png")
        shutil.copyfile(out, prev)
        ret["preview"] = prev
    return ret


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
NAME_BAD = re.compile(r"[\x00-\x1f\\/:*?\"<>|]")


def _pt_dims(preset):
    u, w, h, dpi = preset["unit"], float(preset["width"]), float(preset["height"]), float(preset["dpi"])
    k = {"mm": 72 / 25.4, "in": 72.0, "pt": 1.0}.get(u, 72.0 / dpi)
    return round(w * k, 1), round(h * k, 1)


def layer_names(p, count, first="Background picture"):
    """The names asked for, one per picture (comma separated), or defaults. Refuses a wrong count or a name that is empty, long or has characters a layer name cannot hold."""
    raw = p.get("names")
    if not raw:
        return [first] if count == 1 else [f"Layer {n + 1}" for n in range(count)]
    names = [x.strip() for x in raw.split(",")]
    if len(names) != count:
        raise R.Refused(f"You gave {len(names)} layer name(s) for {count} picture(s); give one name per picture, bottom layer first, separated by commas.")
    for n in names:
        if not n or len(n) > 40 or NAME_BAD.search(n):
            raise R.Refused("A layer name must be 1 to 40 characters and cannot contain \\ / : * ? \" < > |.")
    if len({n.lower() for n in names + (["text"] if p.get("text") else [])}) != len(names) + (1 if p.get("text") else 0):
        raise R.Refused("Two layers have the same name" + (" (the text layer is called Text)" if p.get("text") else "") + ".")
    return names


def _pics(p):
    return p["layers"] if "layers" in p else [p["picture"]]


def _compose_check(p):
    for pic in _pics(p):
        if os.path.splitext(pic)[1].lower() not in IMAGE_EXT:
            raise R.Refused("The pictures must be " + ", ".join(IMAGE_EXT) + " files.")
    if p.get("text") is not None and (not p["text"].strip() or len(p["text"]) > 200):
        raise R.Refused("The text must be between 1 and 200 characters.")
    if not COLOR_RE.match(p.get("color", "#FFFFFF")):
        raise R.Refused("The colour must be written like #F2C94C.")
    import design_indesign as ID
    pr = ID._find_preset(p["preset"])
    layer_names(p, len(_pics(p)))
    return pr


def _compose_outputs(p):
    _compose_check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_AiCard_v01.ai"]


def _compose_steps(p):
    import design_indesign as ID
    pr = _compose_check(p)
    w, h = _pt_dims(pr)
    names = layer_names(p, len(_pics(p)))
    one = len(names) == 1
    L = ["Copy the picture" + ("" if one else "s") + " into the project's 02_Assets folder (the original" + ("" if one else "s") + " never opened)",
         f"Open a new Illustrator document: {pr['name']} ({w:g} x {h:g} pt, RGB)",
         ("Place the picture on its own layer '" + names[0] + "'" if one else "Place each picture on its own layer, bottom first: " + ", ".join(f"'{n}'" for n in names))
         + " so it covers the whole page (centred, enlarged or reduced evenly, never stretched) and embed it; nothing is locked"]
    if p.get("text"):
        L.append(f"Add one live text frame on its own layer 'Text' ({p['position']}), {p['size']} pt, colour {p.get('color', '#FFFFFF')}, centred; you can edit the words and move everything"
                 + ("; the text is Arabic, so the Optyca composer is set (joined letters, right to left)" if ID.has_arabic(p["text"]) else ""))
    L.append("Save it as a new Illustrator file in 05_Exports and a PNG of the page as the preview; close the document without saving again")
    return L


def _compose_run(ctx):
    import design_indesign as ID
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    png = os.path.join(ctx["staging"], "preview.png")
    res_path = os.path.join(ctx["staging"], "ai_result.json")
    pr = _compose_check(p)
    w, h = _pt_dims(pr)
    imgs = ctx["inputs"]["layers"] if "layers" in ctx["inputs"] else [ctx["inputs"]["picture"]]
    names = layer_names(p, len(imgs))
    text = p.get("text") or ""
    ar = ID.has_arabic(text)
    c = p.get("color", "#FFFFFF").lstrip("#")
    jsx = build_compose_jsx(res_path, out=out, png=png, imgs=imgs, names=names, text=text, fams=ID.font_candidates(p.get("font"), ar), size=p["size"],
                            color=[int(c[i:i + 2], 16) for i in (0, 2, 4)], position=p["position"], width_pt=w, height_pt=h, arabic=ar)
    res = _call(jsx, ctx["staging"], res_path)
    step = (res.get("steps") or {}).get("compose")
    if not isinstance(step, dict) or "error" in step:
        raise IllustratorError("Illustrator could not make the card: " + str((step or {}).get("error", "no answer"))[:200])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise IllustratorError("Illustrator said it saved the file but it is not there")
    if text and step.get("read_back_equals_sent") is False:
        raise IllustratorError("The text Illustrator holds is not the text that was sent")
    notes = [f"{pr['name']}; {len(imgs)} picture layer(s), first scaled to {step.get('picture_scale_percent')} %" + (f", font {step.get('font')}" if text else "") + f"; layers: {', '.join(step.get('layers') or [])}"]
    if any("locked" in x for x in step.get("layers") or []):
        notes.append("WARNING: a layer came out locked")
    if text and ar and not str(step.get("composer", "")).startswith("optyca"):
        notes.append("WARNING: the Arabic composer could not be set (" + str(step.get("composer")) + "); the letters may not join")
    if text and (step.get("lines") or 0) * p["size"] * 1.2 > (step.get("frame_height_pt") or 1e9):
        notes.append("WARNING: the text may not fit its frame; use a smaller size or fewer words")
    if step.get("embedded") is not True:
        notes.append("a picture could not be embedded (it stays linked to the copy in 02_Assets)")
    return {"outputs": {name: out}, "preview": png, "notes": notes}


_CARD_PARAMS = {"preset": {"type": "str"}, "name": {"type": "str"}, "font": {"type": "str", "required": False},
                "size": {"type": "int", "min": 8, "max": 600, "required": False, "default": 120}, "color": {"type": "str", "required": False, "default": "#FFFFFF"},
                "position": {"type": "enum", "values": ["top", "center", "bottom"], "required": False, "default": "center"}}
COMPOSE = {"id": "illustrator.compose_card", "app": "illustrator", "title": "Illustrator: a card from a picture and one line of text (new document from one of your presets)",
           "params": {"picture": {"type": "file"}, "text": {"type": "str"}, "names": {"type": "str", "required": False}, **_CARD_PARAMS},
           "plan": _compose_steps, "outputs": _compose_outputs, "run": _compose_run}
ASSEMBLE = {"id": "illustrator.assemble_layers", "app": "illustrator", "title": "Illustrator: a layered file from several pictures (one layer each, bottom first) and optional live text",
            "params": {"layers": {"type": "files", "max": 12}, "names": {"type": "str", "required": False}, "text": {"type": "str", "required": False}, **_CARD_PARAMS},
            "plan": _compose_steps, "outputs": _compose_outputs, "run": _compose_run}


INSPECT = {"id": "illustrator.inspect", "app": "illustrator", "title": "Illustrator: read a file (artboards, layers, text, fonts, links, swatches)", "params": {"source": {"type": "file"}},
           "plan": lambda p: ["Copy the file into the project's 02_Assets folder (the original is never opened)", "Open the COPY in Illustrator (alerts off) and read it; change and save nothing",
                              "Write the report as a JSON file in 05_Exports; close the document without saving"], "outputs": _inspect_outputs, "run": _inspect_run}
AUDIT = {"id": "illustrator.audit", "app": "illustrator", "title": "Illustrator: check a file against your preferences (fonts, swatches, links, Arabic)", "params": {"source": {"type": "file"}},
         "plan": lambda p: ["Copy the file into the project's 02_Assets folder (the original is never opened)", "Open the COPY in Illustrator (alerts off) and read its fonts, swatches, links and text; change and save nothing",
                            "Compare them with your preferences profile and write a readable report (Markdown) in 05_Exports; close the document without saving"], "outputs": _audit_outputs, "run": _audit_run}
EXPORT = {"id": "illustrator.export", "app": "illustrator", "title": "Illustrator: export a picture (PNG of one artboard, SVG or PDF of the document)",
          "params": {"source": {"type": "file"}, "format": {"type": "enum", "values": ["png", "svg", "pdf"], "required": False, "default": "png"},
                     "artboard": {"type": "int", "min": 1, "max": 100, "required": False, "default": 1}, "max_side": {"type": "int", "min": 64, "max": 8192, "required": False}},
          "plan": _export_steps, "outputs": _export_outputs, "run": _export_run}


def register():
    for op in (INSPECT, AUDIT, EXPORT, COMPOSE, ASSEMBLE):
        if op["id"] not in R.OPERATIONS:
            R.register(op)
