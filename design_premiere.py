"""design_premiere.py -- the fixed Premiere Pro operation of the Design portal (Piece 8). The script lives in the David panel (premiere_cep\\jsx\\main.jsx, action rough_cut), written by hand
and tested; a model never writes one.

  premiere.rough_cut   clips and stills, in the order given, become one sequence in a NEW (empty) Premiere project; an optional transparent title picture (made in Photoshop or Illustrator, so
                       Arabic is shaped there) is laid over the whole length on video track 2; the sequence is exported as H.264 and the project is saved next to it.

How it reaches Premiere: Premiere has no COM and no -r switch. The David panel (Window > Extensions > David AI) polls <DAVID_HOME>\\premiere_queue.json every 2 seconds and runs one fixed action per
command. The panel must be OPEN, and Premiere must hold an EMPTY project (File > New > Project): the action refuses to touch a project that has content. The runner works on COPIES of the
clips; the originals are never opened.
"""
from david_config import DAVID_DATA
import os
import json
import time
import datetime

import design_naming
import design_runner as R

QUEUE = str(DAVID_DATA / "premiere_queue.json")
PRESET = r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\MediaIO\systempresets\3F3F3F3F_4D6F6F56\H264 Match Source - High bitrate.epr"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mxf")
STILL_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
PING_WAIT = 25
JOB_WAIT = 1800
POLL = 1.0


class PremiereError(Exception):
    pass


def _read():
    try:
        with open(QUEUE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"commands": []}


def _write(data):
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def send(fields, wait):
    """Queues one command for the panel and waits for it. -> (status, result text). A command nobody picked up in time is marked 'cancelled' (never left to run later)."""
    cmd_id = "cmd_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    data = _read()
    data.setdefault("commands", []).append({"id": cmd_id, "status": "pending", "created_at": datetime.datetime.now().isoformat(), **fields})
    _write(data)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(POLL)
        for c in _read().get("commands", []):
            if c.get("id") == cmd_id and c.get("status") in ("done", "error"):
                return c["status"], str(c.get("result", ""))
    data = _read()
    for c in data.get("commands", []):
        if c.get("id") == cmd_id and c.get("status") == "pending":
            c["status"] = "cancelled"
            c["result"] = "nobody picked this up in time"
    _write(data)
    return "timeout", ""


def default_transport(fields, wait):
    return send(fields, wait)


TRANSPORT = default_transport
PING_HELP = "The David panel in Premiere did not answer. Open Premiere, make an empty project (File > New > Project), then open the panel from Window > Extensions > David AI, and run the job again."


def _stem(name):
    try:
        return design_naming.slug(name, "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))


def _check(p):
    for c in p["clips"]:
        if os.path.splitext(c)[1].lower() not in VIDEO_EXT + STILL_EXT:
            raise R.Refused("The clips must be " + ", ".join(VIDEO_EXT + STILL_EXT) + " files.")
    if p.get("title") and os.path.splitext(p["title"])[1].lower() != ".png":
        raise R.Refused("The title must be a PNG picture with a transparent background (make it in Photoshop or Illustrator).")
    if not os.path.isfile(PRESET):
        raise R.Refused("Premiere's H.264 export preset was not found on this computer, so the job cannot export.")


def _outputs(p):
    _check(p)
    label = _stem(p["name"])
    return [f"{label}_PrCut_v01.mp4", f"{label}_PrCut_v01.prproj"]


def _steps(p):
    L = ["Copy the clips" + (" and the title picture" if p.get("title") else "") + " into the project's 02_Assets folder (the originals are never opened)",
         "Check that the David panel in Premiere answers (Premiere must be open with an EMPTY project and the panel open: Window > Extensions > David AI)",
         f"Import the {len(p['clips'])} clip(s) and make one sequence from them in the order given (the sequence takes the settings of the first clip; stills last Premiere's default time and are scaled to fit the frame, never stretched)"]
    if p.get("title"):
        L.append("Lay the title picture over the whole length on video track 2 (it keeps its transparency, so the Arabic stays as it was made in Photoshop or Illustrator)")
    L += ["Export the sequence as H.264 (Premiere's own 'H264 Match Source - High bitrate' preset) and save the project next to it",
          "Deliver the MP4 and the .prproj in 05_Exports and a picture from the middle of the video as the preview; the originals are not changed"]
    return L


def _still_sizes(paths):
    """Pixel sizes of the still pictures (videos are left alone: Premiere sizes them itself), so the panel can scale each still to fit the frame."""
    from PIL import Image
    out = []
    for p in paths:
        if os.path.splitext(p)[1].lower() in STILL_EXT:
            try:
                with Image.open(p) as im:
                    out.append({"path": os.path.normpath(p), "w": im.size[0], "h": im.size[1]})
            except Exception:
                continue
    return out


def _run(ctx):
    from design_aftereffects import frame_from_video
    p = ctx["params"]
    mp4_name, prproj_name = ctx["outputs_expected"]
    out = os.path.join(ctx["staging"], mp4_name)
    proj = os.path.join(ctx["staging"], prproj_name)
    status, text = TRANSPORT({"action": "ping"}, PING_WAIT)
    if status != "done":
        raise PremiereError(PING_HELP)
    clips = [os.path.normpath(x) for x in ctx["inputs"]["clips"]]
    title = ctx["inputs"].get("title") or ""
    fields = {"action": "rough_cut", "clips": clips, "title_image": os.path.normpath(title) if title else "", "sequence_name": os.path.splitext(mp4_name)[0],
              "out_path": os.path.normpath(out), "project_path": os.path.normpath(proj), "preset_path": PRESET, "sizes": _still_sizes(clips + ([title] if title else []))}
    status, text = TRANSPORT(fields, JOB_WAIT)
    if status == "timeout":
        raise PremiereError(f"Premiere did not finish within {JOB_WAIT // 60} minutes; the job was stopped (check Premiere for a dialog)")
    if status != "done":
        raise PremiereError("Premiere said: " + text.replace("DAVID_ERR:", "").strip()[:300])
    try:
        info = json.loads(text.split("DAVID_OK:", 1)[1])
    except (IndexError, ValueError):
        raise PremiereError("Premiere's answer could not be read: " + text[:200])
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise PremiereError("Premiere said it exported the video but the file is not there")
    if not os.path.isfile(proj) or os.path.getsize(proj) == 0:
        raise PremiereError("Premiere did not save the project file")
    prev = frame_from_video(out, os.path.join(ctx["staging"], "preview.png"))
    notes = [f"sequence '{info.get('sequence')}' ({info.get('frame')}), {info.get('clips_on_v1')} clip(s) on video track 1, {info.get('seconds')} s; title: {info.get('title')}"
             + (f"; stills scaled to fit: {info.get('stills_scaled')} %" if info.get("stills_scaled") else "")]
    if prev is None:
        notes.append("no preview picture could be taken from the video")
    res = {"outputs": {mp4_name: out, prproj_name: proj}, "notes": notes}
    if prev:
        res["preview"] = prev
    return res


ROUGH_CUT = {"id": "premiere.rough_cut", "app": "premiere", "title": "Premiere: a rough cut from clips and stills, an optional title picture on top, exported as H.264",
             "params": {"clips": {"type": "files", "max": 20}, "title": {"type": "file", "required": False}, "name": {"type": "str"}},
             "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if ROUGH_CUT["id"] not in R.OPERATIONS:
        R.register(ROUGH_CUT)
