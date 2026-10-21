"""design_photoshop.py -- the fixed Photoshop operations of the Design portal (Piece 3). The scripts are written here, by hand, and tested; a model never writes one.

Three operations, each registered with design_runner (plan -> the designer's yes -> run on a COPY -> staging -> delivery):
  photoshop.inspect          read a file: size, mode, profile, layers, text and fonts (nothing is changed or saved)
  photoshop.export_preview   a PNG or JPG proof of the file, flattened, converted to sRGB, no larger than a set side (never enlarged)
  photoshop.compose_card     a NEW layered PSD from one of the designer's presets: the picture on its own layer and one live (editable) text layer, Arabic joined and right to left
  photoshop.resize_to_preset the file fitted to one of the designer's document presets (sizes in his preferences), cropped to fill or padded to fit, as a PNG

How it reaches Photoshop: a small worker process (this file with the word "worker") connects through Windows COM and runs ONE fixed script, so a stuck dialog can be
killed on a timeout without taking the HUD with it. Every path and number goes into the script as an escaped literal. The script opens the COPY the runner made
(never the original), works on a duplicate, and closes everything without saving. A Photoshop that was not running is closed again afterwards; one that was running is left alone.
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess

import design_com
import design_naming
import design_ops
import design_preferences as PREF
import design_runner as R
from design_handshake import PRELUDE

IMAGE_EXT = (".psd", ".psb", ".tif", ".tiff", ".png", ".jpg", ".jpeg")
MAX_SIDE_DEFAULT = 1600


class PhotoshopError(design_com.AppError):
    pass


def _lit(v):
    return json.dumps(v, ensure_ascii=True)


def _fwd(p):
    return p.replace("\\", "/")


# ---- the fixed scripts ----

COMMON_HEAD = r"""
var SRC=__SRC__,OUT=__OUT__;
R.app="photoshop";
var prevDialogs=app.displayDialogs;app.displayDialogs=DialogModes.NO;
var oldRuler=app.preferences.rulerUnits;app.preferences.rulerUnits=Units.PIXELS;
var doc=null;
try{doc=app.open(new File(SRC));}catch(e){R.errors.push("open: "+e);}
"""
COMMON_TAIL = r"""
try{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);}catch(e){}
app.preferences.rulerUnits=oldRuler;app.displayDialogs=prevDialogs;
W(__RES__,R);
"""

INSPECT_JSX = PRELUDE + COMMON_HEAD + r"""
function walk(layers,depth,acc){
 for(var i=0;i<layers.length;i++){
  if(acc.layers.length>=500){acc.truncated=true;return;}
  var l=layers[i];var o={name:l.name,type:l.typename,visible:l.visible,depth:depth};
  if(l.typename==="ArtLayer"){
   o.kind=String(l.kind);o.opacity=Math.round(l.opacity);
   if(l.kind===LayerKind.SMARTOBJECT)acc.smart_objects++;
   if(l.kind===LayerKind.TEXT){acc.text_layers++;
    try{o.text=String(l.textItem.contents).substr(0,200);o.font=String(l.textItem.font);o.size=String(l.textItem.size);acc.fonts[o.font]=true;}catch(e){}
   }
  }else{o.children=l.layers.length;acc.groups++;}
  acc.layers.push(o);
  if(l.typename==="LayerSet")walk(l.layers,depth+1,acc);
 }
}
step("inspect",function(){
 if(!doc)throw "the file did not open";
 var acc={layers:[],smart_objects:0,text_layers:0,groups:0,fonts:{},truncated:false};
 walk(doc.layers,0,acc);
 var fonts=[];for(var k in acc.fonts)fonts.push(k);
 return {width_px:doc.width.as("px"),height_px:doc.height.as("px"),resolution:doc.resolution,mode:String(doc.mode),bits:String(doc.bitsPerChannel),
  profile:String(doc.colorProfileName),top_level_layers:doc.layers.length,layers_listed:acc.layers.length,truncated:acc.truncated,text_layers:acc.text_layers,
  smart_objects:acc.smart_objects,groups:acc.groups,fonts:fonts,layers:acc.layers};
});
""" + COMMON_TAIL

RENDER_JSX = PRELUDE + COMMON_HEAD + r"""
var FMT=__FMT__,QUALITY=__QUALITY__,PROFILE=__PROFILE__,W1=__W1__,H1=__H1__,CW=__CW__,CH=__CH__;
step("render",function(){
 if(!doc)throw "the file did not open";
 var d2=doc.duplicate("david_copy",false);
 var r={};
 try{
  d2.flatten();
  if(String(d2.mode)!=="DocumentMode.RGB"){d2.changeMode(ChangeMode.RGB);}
  try{d2.convertProfile(PROFILE,Intent.RELATIVECOLORIMETRIC,true,false);r.profile_converted=true;}catch(e){R.errors.push("profile: "+e);r.profile_converted=false;}
  if(d2.bitsPerChannel!=BitsPerChannelType.EIGHT)d2.bitsPerChannel=BitsPerChannelType.EIGHT;
  if(W1){d2.resizeImage(UnitValue(W1,"px"),UnitValue(H1,"px"),null,ResampleMethod.BICUBICSHARPER);}
  if(CW){var prevBg=app.backgroundColor;var white=new SolidColor();white.rgb.red=255;white.rgb.green=255;white.rgb.blue=255;app.backgroundColor=white;
   d2.resizeCanvas(UnitValue(CW,"px"),UnitValue(CH,"px"),AnchorPosition.MIDDLECENTER);app.backgroundColor=prevBg;}
  if(FMT==="jpg"){var jo=new JPEGSaveOptions();jo.quality=Math.max(0,Math.min(12,Math.round(QUALITY*12/100)));jo.embedColorProfile=true;jo.formatOptions=FormatOptions.STANDARDBASELINE;d2.saveAs(new File(OUT),jo,true,Extension.LOWERCASE);}
  else{var po=new PNGSaveOptions();po.interlaced=false;try{po.compression=9;}catch(e){}d2.saveAs(new File(OUT),po,true,Extension.LOWERCASE);}
  r.width_px=d2.width.as("px");r.height_px=d2.height.as("px");r.profile=String(d2.colorProfileName);r.out=OUT;
 }finally{try{d2.close(SaveOptions.DONOTSAVECHANGES);}catch(e){}}
 return r;
});
""" + COMMON_TAIL


PS_COMPOSE_JSX = PRELUDE + r"""
var OUT=__OUT__,PNG=__PNG__,IMGS=__IMGS__,NAMES=__NAMES__,TEXT=__TEXT__,FAMS=__FAMS__,SIZE=__SIZE__,COLOR=__COLOR__,POS=__POS__,DW=__DW__,DH=__DH__,DPI=__DPI__;
R.app="photoshop";
var prevDialogs=app.displayDialogs;app.displayDialogs=DialogModes.NO;
var oldRuler=app.preferences.rulerUnits,oldType=app.preferences.typeUnits;
app.preferences.rulerUnits=Units.PIXELS;app.preferences.typeUnits=TypeUnits.PIXELS;
var doc=null;
step("compose",function(){
 doc=app.documents.add(DW,DH,DPI,"card",NewDocumentMode.RGB,DocumentFill.TRANSPARENT);
 var r={canvas_px:[DW,DH],scales:[]};
 var empty=doc.layers[0];
 for(var n=0;n<IMGS.length;n++){
  var src=app.open(new File(IMGS[n]));var sw=src.width.as("px"),sh=src.height.as("px");
  var k=Math.max(DW/sw,DH/sh);
  src.resizeImage(UnitValue(Math.round(sw*k),"px"),UnitValue(Math.round(sh*k),"px"),undefined,ResampleMethod.BICUBIC);
  src.resizeCanvas(UnitValue(DW,"px"),UnitValue(DH,"px"),AnchorPosition.MIDDLECENTER);
  var lyr=src.activeLayer.duplicate(doc,ElementPlacement.PLACEATBEGINNING);
  src.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=doc;
  lyr.name=NAMES[n];
  r.scales.push(Math.round(k*1000)/10);
 }
 try{empty.remove();}catch(e){}
 r.picture_scale_percent=r.scales[0];
 if(TEXT){
  var fh=Math.min(SIZE*2.6,DH*0.8);var fy;
  if(POS==="top")fy=DH*0.08;else if(POS==="bottom")fy=DH-DH*0.08-fh;else fy=(DH-fh)/2;
  var t=doc.artLayers.add();t.kind=LayerKind.TEXT;t.name="Title";
  var ti=t.textItem;ti.kind=TextType.PARAGRAPHTEXT;ti.contents=TEXT;ti.size=SIZE;ti.width=DW*0.88;ti.height=fh;ti.position=[DW*0.06,fy];
  var used="";
  for(var i=0;i<FAMS.length&&!used;i++){for(var j=0;j<app.fonts.length;j++){var f=app.fonts[j];if(f.family===FAMS[i]){try{ti.font=f.postScriptName;used=FAMS[i]+" ("+f.postScriptName+")";}catch(e){}break;}}}
  r.font=used||"(Photoshop's default font)";
  var c=new SolidColor();c.rgb.red=COLOR[0];c.rgb.green=COLOR[1];c.rgb.blue=COLOR[2];ti.color=c;
  ti.justification=Justification.CENTER;
  r.read_back_equals_sent=(ti.contents===TEXT);
  var tb=t.bounds;r.text_height_px=Math.round(tb[3].as("px")-tb[1].as("px"));r.frame_height_px=Math.round(fh);
 }
 r.layers=[];for(var k=0;k<doc.layers.length;k++){r.layers.push(doc.layers[k].name+(doc.layers[k].allLocked?" (locked)":"")+(doc.layers[k].kind==LayerKind.TEXT?" [text]":""));}
 doc.saveAs(new File(OUT),new PhotoshopSaveOptions(),true);
 var o=new ExportOptionsSaveForWeb();o.format=SaveDocumentType.PNG;o.PNG8=false;o.transparency=false;
 doc.exportDocument(new File(PNG),ExportType.SAVEFORWEB,o);
 return r;
});
try{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);}catch(e){}
app.preferences.rulerUnits=oldRuler;app.preferences.typeUnits=oldType;app.displayDialogs=prevDialogs;
W(__RES__,R);
"""


def build_compose_jsx(result, **p):
    return (PS_COMPOSE_JSX.replace("__OUT__", _lit(_fwd(p["out"]))).replace("__PNG__", _lit(_fwd(p["png"]))).replace("__IMGS__", json.dumps([_fwd(x) for x in p["imgs"]], ensure_ascii=True))
            .replace("__NAMES__", json.dumps(p["names"], ensure_ascii=True)).replace("__TEXT__", _lit(p.get("text") or ""))
            .replace("__FAMS__", json.dumps(p["fams"], ensure_ascii=True)).replace("__SIZE__", repr(float(p["size"]))).replace("__COLOR__", json.dumps(p["color"]))
            .replace("__POS__", _lit(p["position"])).replace("__DW__", str(int(p["width_px"]))).replace("__DH__", str(int(p["height_px"]))).replace("__DPI__", str(int(p["dpi"])))
            .replace("__RES__", _lit(_fwd(result))))


def build_jsx(kind, src, out, result, **p):
    """The fixed script for one operation with its values filled in as escaped literals."""
    if kind == "inspect":
        t = INSPECT_JSX
    elif kind == "render":
        t = (RENDER_JSX.replace("__FMT__", _lit(p["fmt"])).replace("__QUALITY__", str(int(p.get("quality", 90)))).replace("__PROFILE__", _lit(p["profile"]))
             .replace("__W1__", "null" if not p.get("w1") else str(int(p["w1"]))).replace("__H1__", "null" if not p.get("h1") else str(int(p["h1"])))
             .replace("__CW__", "null" if not p.get("cw") else str(int(p["cw"]))).replace("__CH__", "null" if not p.get("ch") else str(int(p["ch"]))))
    else:
        raise ValueError(kind)
    return t.replace("__SRC__", _lit(_fwd(src))).replace("__OUT__", _lit(_fwd(out) if out else "")).replace("__RES__", _lit(_fwd(result)))


# ---- the transport: one script, one worker process, a timeout (shared code: design_com.py) ----

def default_transport(jsx_text, workdir, result_path, timeout=design_com.WORKER_TIMEOUT):
    try:
        return design_com.run_script("photoshop", jsx_text, workdir, result_path, timeout)
    except design_com.AppError as e:
        raise PhotoshopError(str(e))


TRANSPORT = default_transport


def _call(jsx_text, workdir, result_path):
    res = TRANSPORT(jsx_text, workdir, result_path)
    try:
        return design_com.check_errors(res, "Photoshop")
    except design_com.AppError as e:
        raise PhotoshopError(str(e))


# ---- planning helpers (pure, tested) ----

def source_size(path):
    """(width_px, height_px) read from the file itself, without Photoshop."""
    r = design_ops.inspect_file(path)
    if "width_px" not in r:
        raise R.Refused("The size of this file could not be read; use a Photoshop (.psd, .psb), TIFF, PNG or JPG file.")
    return int(r["width_px"]), int(r["height_px"])


def preview_plan(w, h, max_side):
    """The size of a proof: the long side no larger than max_side, never enlarged. -> (new_w, new_h) or None when no resize is needed."""
    if max(w, h) <= max_side:
        return None
    s = max_side / max(w, h)
    return max(1, round(w * s)), max(1, round(h * s))


def preset_px(preset):
    """(width_px, height_px) of a document preset (px as they are; mm and in by the preset's dpi; pt at 72 per inch)."""
    u, w, h, dpi = preset["unit"], float(preset["width"]), float(preset["height"]), float(preset["dpi"])
    k = {"px": None, "mm": dpi / 25.4, "in": dpi, "pt": dpi / 72.0}[u]
    return (round(w), round(h)) if k is None else (round(w * k), round(h * k))


def fit_plan(w, h, W, H, fit):
    """Scale the source to cover (crop the overflow) or to be contained (pad the rest) inside W x H. -> (scaled_w, scaled_h); the canvas is then W x H, centred."""
    s = max(W / w, H / h) if fit == "cover" else min(W / w, H / h)
    return max(1, round(w * s)), max(1, round(h * s))


def _prefs():
    try:
        return PREF.load()
    except (OSError, ValueError):
        return None


def _max_side_default():
    p = _prefs()
    v = PREF.setting(p, "preview.max_side_px") if p else None
    return int(v) if isinstance(v, int) else MAX_SIDE_DEFAULT


def _rgb_profile():
    p = _prefs()
    v = PREF.setting(p, "color.default_rgb_profile") if p else None
    return v if isinstance(v, str) and v.strip() else "sRGB IEC61966-2.1"


def _find_preset(pid):
    p = _prefs()
    if not p:
        raise R.Refused("The preferences profile could not be read, so the document sizes are not known.")
    for r in p["document_presets"]:
        if r["id"] == pid:
            return r
    ids = ", ".join(r["id"] for r in p["document_presets"])
    raise R.Refused(f"'{pid}' is not one of your document presets. Your presets: {ids}.")


def _check_source(path):
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
        raise R.Refused("This operation opens Photoshop, TIFF, PNG or JPG files only (" + ", ".join(IMAGE_EXT) + ").")


def _stem(path):
    try:
        return design_naming.slug(os.path.splitext(os.path.basename(path))[0], "file")
    except design_naming.BadName:
        return "File"


# ---- the three operations ----

def _inspect_outputs(p):
    _check_source(p["source"])
    return [f"{_stem(p['source'])}_PsInspect_v01.json"]


def _inspect_run(ctx):
    name = ctx["outputs_expected"][0]
    res_path = os.path.join(ctx["staging"], "ps_result.json")
    jsx = build_jsx("inspect", ctx["inputs"]["source"], "", res_path)
    res = _call(jsx, ctx["staging"], res_path)
    info = (res.get("steps") or {}).get("inspect")
    if not isinstance(info, dict) or "error" in info:
        raise PhotoshopError("Photoshop could not read the file: " + str((info or {}).get("error", "no answer"))[:200])
    out = os.path.join(ctx["staging"], name)
    json.dump(info, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"outputs": {name: out}, "notes": [f"{info.get('width_px')}x{info.get('height_px')} px, {info.get('mode')}, {info.get('text_layers')} text layer(s), fonts: {', '.join(info.get('fonts') or []) or 'none'}"]}


def _preview_outputs(p):
    _check_source(p["source"])
    ext = "png" if p["format"] == "png" else "jpg"
    return [f"{_stem(p['source'])}_PsPreview_v01.{ext}"]


def _preview_plan_steps(p):
    w, h = source_size(p["source"])
    ms = p.get("max_side") or _max_side_default()
    pl = preview_plan(w, h, ms)
    return ["Copy the file into the project's 02_Assets folder (the original is never opened)",
            f"Open the COPY in Photoshop and work on a flattened duplicate (hidden layers are left out): {w} x {h} px",
            f"Convert it to {_rgb_profile()}, 8 bits per channel" + (f", and shrink it to {pl[0]} x {pl[1]} px (long side {ms})" if pl else f" (no resize: it is already within {ms} px)"),
            f"Save it as a {p['format'].upper()} in 05_Exports and show it as the preview; close everything without saving"]


def _render_run(ctx, out_name, fmt, quality, w1, h1, cw, ch):
    out = os.path.join(ctx["staging"], out_name)
    res_path = os.path.join(ctx["staging"], "ps_result.json")
    jsx = build_jsx("render", ctx["inputs"]["source"], out, res_path, fmt=fmt, quality=quality, profile=_rgb_profile(), w1=w1, h1=h1, cw=cw, ch=ch)
    res = _call(jsx, ctx["staging"], res_path)
    step = (res.get("steps") or {}).get("render")
    if not isinstance(step, dict) or "error" in step:
        raise PhotoshopError("Photoshop could not make the picture: " + str((step or {}).get("error", "no answer"))[:200])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise PhotoshopError("Photoshop said it saved the picture but the file is not there")
    prev = os.path.join(ctx["staging"], "preview." + ("jpg" if fmt == "jpg" else "png"))
    shutil.copyfile(out, prev)
    return {"outputs": {out_name: out}, "preview": prev, "notes": [f"{step.get('width_px')} x {step.get('height_px')} px, profile {step.get('profile')}"]}


def _preview_run(ctx):
    p = ctx["params"]
    w, h = source_size(p["source"])
    pl = preview_plan(w, h, p.get("max_side") or _max_side_default())
    return _render_run(ctx, ctx["outputs_expected"][0], p["format"], 90, pl[0] if pl else None, pl[1] if pl else None, None, None)


def _resize_outputs(p):
    _check_source(p["source"])
    pr = _find_preset(p["preset"])
    if pr["color_mode"] != "RGB":
        raise R.Refused(f"The preset '{pr['id']}' is {pr['color_mode']} (print). This operation saves an RGB PNG; pick an RGB preset. A print export comes later.")
    try:
        label = design_naming.slug(pr["id"].replace("-", " "), "preset")
    except design_naming.BadName:
        label = "Preset"
    return [f"{_stem(p['source'])}_{label}_v01.png"]


def _resize_plan_steps(p):
    pr = _find_preset(p["preset"])
    W, H = preset_px(pr)
    w, h = source_size(p["source"])
    sw, sh = fit_plan(w, h, W, H, p["fit"])
    how = "scale to cover the frame and crop the overflow from the centre" if p["fit"] == "cover" else "scale to fit inside the frame and pad the rest with white"
    return ["Copy the file into the project's 02_Assets folder (the original is never opened)",
            f"Open the COPY in Photoshop and work on a flattened duplicate: {w} x {h} px",
            f"Convert it to {_rgb_profile()}, 8 bits; {how}: {sw} x {sh} px, then a {W} x {H} px canvas ({pr['name']})",
            "Save it as a PNG in 05_Exports and show it as the preview; close everything without saving"]


def _resize_run(ctx):
    p = ctx["params"]
    pr = _find_preset(p["preset"])
    W, H = preset_px(pr)
    w, h = source_size(p["source"])
    sw, sh = fit_plan(w, h, W, H, p["fit"])
    return _render_run(ctx, ctx["outputs_expected"][0], "png", 90, sw, sh, W, H)


CARD_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _pics(p):
    return p["layers"] if "layers" in p else [p["picture"]]


def _compose_check(p):
    import design_illustrator as AI
    for pic in _pics(p):
        if os.path.splitext(pic)[1].lower() not in CARD_IMAGE_EXT:
            raise R.Refused("The pictures must be " + ", ".join(CARD_IMAGE_EXT) + " files.")
    if p.get("text") is not None and (not p["text"].strip() or len(p["text"]) > 200):
        raise R.Refused("The text must be between 1 and 200 characters.")
    if not COLOR_RE.match(p.get("color", "#FFFFFF")):
        raise R.Refused("The colour must be written like #F2C94C.")
    pr = _find_preset(p["preset"])
    w, h = preset_px(pr)
    if w * h > 40_000_000:
        raise R.Refused(f"{pr['name']} is {w} x {h} px, too large for a card (the limit is 40 million pixels).")
    AI.layer_names(p, len(_pics(p)))
    return pr


def _compose_outputs(p):
    _compose_check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_PsCard_v01.psd"]


def _compose_steps(p):
    import design_indesign as ID
    import design_illustrator as AI
    pr = _compose_check(p)
    w, h = preset_px(pr)
    names = AI.layer_names(p, len(_pics(p)))
    one = len(names) == 1
    L = ["Copy the picture" + ("" if one else "s") + " into the project's 02_Assets folder (the original" + ("" if one else "s") + " never opened)",
         f"Make a new transparent Photoshop document: {pr['name']} ({w} x {h} px, {pr['dpi']} dpi, RGB)",
         ("Put the picture on its own layer '" + names[0] + "'" if one else "Put each picture on its own layer, bottom first: " + ", ".join(f"'{n}'" for n in names))
         + " so it covers the whole canvas (centred, enlarged or reduced evenly, never stretched); nothing is locked"]
    if p.get("text"):
        L.append(f"Add one live text layer 'Title' ({p['position']}), {p['size']} px, colour {p.get('color', '#FFFFFF')}, centred; you can edit the words and move everything"
                 + ("; the text is Arabic, so it joins and runs right to left" if ID.has_arabic(p["text"]) else ""))
    L.append("Save it as a new layered PSD in 05_Exports and a PNG of the canvas as the preview; close the document without saving again")
    return L


def _compose_run(ctx):
    import design_indesign as ID
    import design_illustrator as AI
    p = ctx["params"]
    name = ctx["outputs_expected"][0]
    out = os.path.join(ctx["staging"], name)
    png = os.path.join(ctx["staging"], "preview.png")
    res_path = os.path.join(ctx["staging"], "ps_result.json")
    pr = _compose_check(p)
    w, h = preset_px(pr)
    imgs = ctx["inputs"]["layers"] if "layers" in ctx["inputs"] else [ctx["inputs"]["picture"]]
    names = AI.layer_names(p, len(imgs))
    text = p.get("text") or ""
    ar = ID.has_arabic(text)
    c = p.get("color", "#FFFFFF").lstrip("#")
    jsx = build_compose_jsx(res_path, out=out, png=png, imgs=imgs, names=names, text=text, fams=ID.font_candidates(p.get("font"), ar), size=p["size"],
                            color=[int(c[i:i + 2], 16) for i in (0, 2, 4)], position=p["position"], width_px=w, height_px=h, dpi=pr["dpi"])
    res = _call(jsx, ctx["staging"], res_path)
    step = (res.get("steps") or {}).get("compose")
    if not isinstance(step, dict) or "error" in step:
        raise PhotoshopError("Photoshop could not make the card: " + str((step or {}).get("error", "no answer"))[:200])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise PhotoshopError("Photoshop said it saved the file but it is not there")
    if text and step.get("read_back_equals_sent") is False:
        raise PhotoshopError("The text Photoshop holds is not the text that was sent")
    notes = [f"{pr['name']}; {len(imgs)} picture layer(s), first scaled to {step.get('picture_scale_percent')} %" + (f", font {step.get('font')}" if text else "") + f"; layers: {', '.join(step.get('layers') or [])}"]
    if any("locked" in x for x in step.get("layers") or []):
        notes.append("WARNING: a layer came out locked")
    if text and (step.get("text_height_px") or 0) > (step.get("frame_height_px") or 1e9) * 1.02:
        notes.append("WARNING: the text is taller than its box; use a smaller size or fewer words")
    return {"outputs": {name: out}, "preview": png, "notes": notes}


_CARD_PARAMS = {"preset": {"type": "str"}, "name": {"type": "str"}, "font": {"type": "str", "required": False},
                "size": {"type": "int", "min": 8, "max": 2000, "required": False, "default": 120}, "color": {"type": "str", "required": False, "default": "#FFFFFF"},
                "position": {"type": "enum", "values": ["top", "center", "bottom"], "required": False, "default": "center"}}
COMPOSE = {"id": "photoshop.compose_card", "app": "photoshop", "title": "Photoshop: a layered card from a picture and one line of text (new PSD from one of your presets)",
           "params": {"picture": {"type": "file"}, "text": {"type": "str"}, "names": {"type": "str", "required": False}, **_CARD_PARAMS},
           "plan": _compose_steps, "outputs": _compose_outputs, "run": _compose_run}
ASSEMBLE = {"id": "photoshop.assemble_layers", "app": "photoshop", "title": "Photoshop: a layered PSD from several pictures (one layer each, bottom first) and optional live text",
            "params": {"layers": {"type": "files", "max": 12}, "names": {"type": "str", "required": False}, "text": {"type": "str", "required": False}, **_CARD_PARAMS},
            "plan": _compose_steps, "outputs": _compose_outputs, "run": _compose_run}


INSPECT = {"id": "photoshop.inspect", "app": "photoshop", "title": "Photoshop: read a file (size, mode, profile, layers, text, fonts)", "params": {"source": {"type": "file"}},
           "plan": lambda p: ["Copy the file into the project's 02_Assets folder (the original is never opened)", "Open the COPY in Photoshop and read it: size, mode, profile, layers, text and fonts; change and save nothing",
                              "Write the report as a JSON file in 05_Exports; close the document without saving"],
           "outputs": _inspect_outputs, "run": _inspect_run}
PREVIEW = {"id": "photoshop.export_preview", "app": "photoshop", "title": "Photoshop: export a proof picture (PNG or JPG, never enlarged)",
           "params": {"source": {"type": "file"}, "format": {"type": "enum", "values": ["png", "jpg"], "required": False, "default": "png"},
                      "max_side": {"type": "int", "min": 256, "max": 8192, "required": False}},
           "plan": _preview_plan_steps, "outputs": _preview_outputs, "run": _preview_run}
RESIZE = {"id": "photoshop.resize_to_preset", "app": "photoshop", "title": "Photoshop: fit a file to one of your document presets (RGB PNG)",
          "params": {"source": {"type": "file"}, "preset": {"type": "str"}, "fit": {"type": "enum", "values": ["cover", "contain"], "required": False, "default": "cover"}},
          "plan": _resize_plan_steps, "outputs": _resize_outputs, "run": _resize_run}


def register():
    for op in (INSPECT, PREVIEW, RESIZE, COMPOSE, ASSEMBLE):
        if op["id"] not in R.OPERATIONS:
            R.register(op)
