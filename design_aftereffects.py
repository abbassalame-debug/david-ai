"""design_aftereffects.py -- the fixed After Effects operation of the Design portal (Piece 9). The script is written here, by hand, and tested; a model never writes one.

  aftereffects.animate_layers   a LAYERED PSD (from photoshop.assemble_layers) becomes a short video: it is imported as a composition with every layer kept separate, each layer gets its
                                own motion by depth (the background moves least, the front layers most, the title fades in), and the result is rendered as an H.264 MP4 and saved as an
                                editable After Effects project (.aep). Motions: parallax (sideways drift), zoom_in (slow push in), fade_in (layers appear one by one, bottom first).

After Effects has no COM route here: it is started with AfterFX.exe -r <script>, which runs the fixed script in a fresh session. So the operation REFUSES to run while After Effects is open
(a running session may hold the designer's own project): close it first. The runner works on a COPY of the PSD; the original is never opened. The Arabic title stays as Photoshop made it
(a picture layer), so its letters stay joined and right to left. A session started here is closed again afterwards.
"""
import os
import json
import time
import subprocess

import design_naming
import design_runner as R
from design_handshake import PRELUDE, EXES, running, wait_until

AE_EXE = EXES["aftereffects"]
SOURCE_EXT = (".psd", ".psb")
MOTIONS = ("parallax", "zoom_in", "fade_in")
H264 = "H.264 - Match Render Settings - 15 Mbps"
RUN_TIMEOUT = 900
QUIT_WAIT = 90


class AfterEffectsError(Exception):
    pass


def _lit(v):
    return json.dumps(v, ensure_ascii=True)


def _fwd(p):
    return p.replace("\\", "/")


ANIMATE_JSX = PRELUDE + r"""
var SRC=__SRC__,OUT=__OUT__,AEP=__AEP__,MOTION=__MOTION__,SECS=__SECS__,FPS=__FPS__,TEMPLATE=__TEMPLATE__;
R.app="aftereffects";
var proj=null;
function ease2(prop,n){try{var e=new KeyframeEase(0,40);var arr=[];for(var q=0;q<n;q++)arr.push(e);for(var k=1;k<=prop.numKeys;k++){prop.setTemporalEaseAtKey(k,arr,arr);}}catch(e2){}}
step("animate",function(){
 proj=app.newProject();
 var io=new ImportOptions(new File(SRC));io.importAs=ImportAsType.COMP;
 var item=proj.importFile(io);var comp=null;
 for(var i=1;i<=proj.numItems;i++){if(proj.item(i) instanceof CompItem){comp=proj.item(i);break;}}
 if(!comp)throw "the file did not import as a composition";
 comp.duration=SECS;comp.frameRate=FPS;comp.workAreaStart=0;comp.workAreaDuration=SECS;
 var n=comp.numLayers;var r={comp:comp.name,width:comp.width,height:comp.height,layers:[],motion:MOTION,seconds:SECS,fps:FPS};
 for(var i2=1;i2<=n;i2++){
  var L=comp.layer(i2);L.outPoint=SECS;
  var depth=(n>1)?((n-i2)/(n-1)):0;
  var isTitle=(i2===1&&/^(Title|Text)$/i.test(L.name));
  var o=L.property("Opacity"),pos=L.property("Position"),sc=L.property("Scale");
  var p0=pos.value,m="static";
  if(MOTION==="fade_in"){
   var t0=(n-i2)*0.5;if(t0+0.6>SECS)t0=Math.max(0,SECS-0.6);
   o.setValueAtTime(0,0);o.setValueAtTime(t0,0);o.setValueAtTime(t0+0.6,100);m="fade";
  }else if(isTitle){
   o.setValueAtTime(0,0);o.setValueAtTime(0.8,100);m="title fade";
  }else if(MOTION==="parallax"){
   var amp=depth*28;sc.setValue([106,106,100]);
   pos.setValueAtTime(0,[p0[0]+amp,p0[1],p0[2]||0].slice(0,p0.length));pos.setValueAtTime(SECS,[p0[0]-amp,p0[1],p0[2]||0].slice(0,p0.length));
   ease2(pos,p0.length);m="drift "+Math.round(amp)+" px";
  }else if(MOTION==="zoom_in"){
   var end=106+8*depth;sc.setValueAtTime(0,[100,100,100]);sc.setValueAtTime(SECS,[end,end,100]);ease2(sc,3);m="zoom to "+Math.round(end)+" %";
  }
  r.layers.push(L.name+": "+m);
 }
 var rq=proj.renderQueue;var it=rq.items.add(comp);var om=it.outputModule(1);
 try{it.applyTemplate("Best Settings");}catch(e){}
 om.applyTemplate(TEMPLATE);
 om.file=new File(OUT);
 proj.save(new File(AEP));
 rq.render();
 r.render_status=String(it.status);r.out=String(om.file.fsName);r.aep=AEP;
 return r;
});
try{if(proj)proj.close(CloseOptions.DO_NOT_SAVE_CHANGES);}catch(e){}
W(__RES__,R);
if(__QUIT__){app.quit();}
"""


def build_jsx(result, **p):
    return (ANIMATE_JSX.replace("__SRC__", _lit(_fwd(p["src"]))).replace("__OUT__", _lit(_fwd(p["out"]))).replace("__AEP__", _lit(_fwd(p["aep"]))).replace("__MOTION__", _lit(p["motion"]))
            .replace("__SECS__", repr(float(p["seconds"]))).replace("__FPS__", str(int(p["fps"]))).replace("__TEMPLATE__", _lit(p["template"]))
            .replace("__RES__", _lit(_fwd(result))).replace("__QUIT__", "true" if p.get("quit", True) else "false"))


def default_transport(jsx_text, workdir, result_path):
    """Runs the script in a fresh After Effects (AfterFX.exe -r) and returns the result the script wrote."""
    if running("aftereffects"):
        raise AfterEffectsError("After Effects is open. Close it first (it may hold your own project); the job runs in a fresh session and closes it again.")
    jsx = os.path.join(workdir, "aftereffects_job.jsx")
    open(jsx, "w", encoding="utf-8").write(jsx_text)
    if os.path.exists(result_path):
        os.remove(result_path)
    subprocess.Popen([AE_EXE, "-r", jsx], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = wait_until(lambda: os.path.isfile(result_path), RUN_TIMEOUT, 3)
    if not ok:
        subprocess.run(["taskkill", "/IM", "AfterFX.exe", "/T", "/F"], capture_output=True)
        raise AfterEffectsError(f"After Effects did not finish within {RUN_TIMEOUT // 60} minutes (a dialog may be waiting); the session was closed and the job stopped")
    time.sleep(1)
    wait_until(lambda: not running("aftereffects"), QUIT_WAIT, 2)
    try:
        return json.load(open(result_path, encoding="utf-8-sig"))
    except ValueError as e:
        raise AfterEffectsError(f"After Effects' answer could not be read: {e}")


TRANSPORT = default_transport


def frame_from_video(video, png, at=0.5):
    """A picture of the video at `at` (0..1 of its length), or None if it cannot be decoded."""
    try:
        import av
        with av.open(video) as c:
            s = c.streams.video[0]
            total = float(s.duration * s.time_base) if s.duration else float(c.duration) / 1e6
            c.seek(int(total * at / float(s.time_base)), stream=s)
            for fr in c.decode(s):
                fr.to_image().save(png)
                return png
    except Exception:
        return None
    return None


def _stem(path):
    try:
        return design_naming.slug(os.path.splitext(os.path.basename(path))[0], "file")
    except design_naming.BadName:
        return "File"


def _check(p):
    if os.path.splitext(p["source"])[1].lower() not in SOURCE_EXT:
        raise R.Refused("This operation opens layered Photoshop files only (" + ", ".join(SOURCE_EXT) + "): make one with photoshop.assemble_layers.")
    if running("aftereffects"):
        raise R.Refused("After Effects is open. Close it first: the job runs in a fresh session (so it cannot touch a project you are working on) and closes it again.")


def _outputs(p):
    _check(p)
    return [f"{_stem(p['source'])}_AeMotion_v01.mp4", f"{_stem(p['source'])}_AeMotion_v01.aep"]


def _steps(p):
    what = {"parallax": "each layer drifts sideways by its depth: the background barely moves, the front layers most, and the title fades in",
            "zoom_in": "a slow push in: every layer zooms in a little, the front layers more, and the title fades in",
            "fade_in": "the layers appear one by one, bottom first, and the title last"}[p["motion"]]
    return ["Copy the Photoshop file into the project's 02_Assets folder (the original is never opened)",
            "Start a fresh After Effects session (it must not be open already) and import the file as a composition with every layer kept separate",
            f"Make it {p['seconds']} seconds at {p['fps']} frames per second; {what}",
            f"Render it as an H.264 video ({H264}) and save the editable After Effects project next to it; close After Effects again",
            "Deliver the MP4 and the .aep in 05_Exports and a picture from the middle of the video as the preview"]


def _run(ctx):
    p = ctx["params"]
    mp4_name, aep_name = ctx["outputs_expected"]
    out = os.path.join(ctx["staging"], mp4_name)
    aep = os.path.join(ctx["staging"], aep_name)
    res_path = os.path.join(ctx["staging"], "ae_result.json")
    jsx = build_jsx(res_path, src=ctx["inputs"]["source"], out=out, aep=aep, motion=p["motion"], seconds=p["seconds"], fps=int(p["fps"]), template=H264)
    try:
        res = TRANSPORT(jsx, ctx["staging"], res_path)
    except AfterEffectsError:
        raise
    errs = res.get("errors") or []
    if errs:
        raise AfterEffectsError("After Effects reported: " + "; ".join(str(e) for e in errs[:3]))
    step = (res.get("steps") or {}).get("animate")
    if not isinstance(step, dict) or "error" in step:
        raise AfterEffectsError("After Effects could not make the video: " + str((step or {}).get("error", "no answer"))[:250])
    made = step.get("out") or out
    if made and os.path.isfile(made) and os.path.abspath(made) != os.path.abspath(out):
        os.replace(made, out)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise AfterEffectsError("After Effects said it rendered the video but the file is not there")
    if not os.path.isfile(aep) or os.path.getsize(aep) == 0:
        raise AfterEffectsError("After Effects did not save the project file")
    prev = frame_from_video(out, os.path.join(ctx["staging"], "preview.png"))
    notes = [f"{step.get('width')} x {step.get('height')} px, {step.get('seconds')} s at {step.get('fps')} fps, motion {step.get('motion')}; layers: " + "; ".join(step.get("layers") or [])]
    if str(step.get("render_status", "")) != "3019":
        notes.append(f"WARNING: After Effects' render status is {step.get('render_status')}; check the video")
    if prev is None:
        notes.append("no preview picture could be taken from the video")
    out_res = {"outputs": {mp4_name: out, aep_name: aep}, "notes": notes}
    if prev:
        out_res["preview"] = prev
    return out_res


ANIMATE = {"id": "aftereffects.animate_layers", "app": "aftereffects", "title": "After Effects: move the layers of a layered PSD (parallax, zoom or fade) and render a video",
           "params": {"source": {"type": "file"}, "motion": {"type": "enum", "values": list(MOTIONS), "required": False, "default": "parallax"},
                      "seconds": {"type": "int", "min": 2, "max": 20, "required": False, "default": 5}, "fps": {"type": "enum", "values": ["24", "25", "30"], "required": False, "default": "30"}},
           "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if ANIMATE["id"] not in R.OPERATIONS:
        R.register(ANIMATE)
