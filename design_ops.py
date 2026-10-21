"""design_ops.py -- the fixed operations the Design portal can run (each one is code, tested, and takes parameters; a model never writes a script).

This first set needs no Adobe app: file.inspect reads a COPY of a design file and reports what it is (type, size, SHA-256, and for Photoshop / Illustrator
files the colour mode, size, profile and fonts the file declares). Operations that drive an app are added piece by piece as each is built and tested.
"""
import os
import re
import json
import mmap
import struct
import hashlib
import datetime

import design_naming
import design_runner

PSD_MODES = {0: "bitmap", 1: "gray", 2: "indexed", 3: "RGB", 4: "CMYK", 7: "multichannel", 8: "duotone", 9: "Lab"}
KINDS = {".psd": "Photoshop", ".psb": "Photoshop (large)", ".ai": "Illustrator", ".indd": "InDesign", ".idml": "InDesign (IDML)", ".prproj": "Premiere project",
         ".aep": "After Effects project", ".pdf": "PDF", ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image", ".tif": "image", ".tiff": "image",
         ".svg": "vector image", ".mp4": "video", ".mov": "video"}
_XMP = re.compile(rb"<\?xpacket begin.*?<\?xpacket end[^>]*\?>", re.S)
_FONT = re.compile(rb"<stFnt:fontFamily>([^<]+)</stFnt:fontFamily>")
_MODE = re.compile(rb"<xmpG:mode>([^<]+)</xmpG:mode>")
_ICC = re.compile(rb"<photoshop:ICCProfile>([^<]+)</photoshop:ICCProfile>")
_TOOL = re.compile(rb"<xmp:CreatorTool>([^<]+)</xmp:CreatorTool>")


def _sha(path):
    return design_runner.sha256_file(path)


def inspect_file(path):
    ext = os.path.splitext(path)[1].lower()
    st = os.stat(path)
    r = {"file": os.path.basename(path), "extension": ext, "kind": KINDS.get(ext, "other"), "size_bytes": st.st_size, "sha256": _sha(path),
         "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
    if ext in (".psd", ".psb"):
        with open(path, "rb") as f:
            h = f.read(26)
        if h[:4] == b"8BPS":
            _, ch, hh, ww, depth, mode = struct.unpack(">H6xHIIHH", h[4:26])
            r.update({"width_px": ww, "height_px": hh, "channels": ch, "bit_depth": depth, "color_mode": PSD_MODES.get(mode, str(mode))})
        else:
            r["problem"] = "the file does not start like a Photoshop file"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff"):
        from PIL import Image
        with Image.open(path) as im:
            r.update({"width_px": im.size[0], "height_px": im.size[1], "color_mode": im.mode})
            dpi = im.info.get("dpi")
            if dpi:
                r["dpi"] = [round(float(dpi[0])), round(float(dpi[1]))]
    if ext in (".psd", ".psb", ".ai", ".indd", ".pdf") and st.st_size > 0:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            m = _XMP.search(mm)
            if m:
                x = m.group(0)
                dec = lambda b: b.decode("utf-8", "replace")
                fonts = sorted({dec(v) for v in _FONT.findall(x)})
                if fonts:
                    r["fonts_declared"] = fonts
                modes = sorted({dec(v) for v in _MODE.findall(x)})
                if modes:
                    r["swatch_color_modes"] = modes
                icc = sorted({dec(v) for v in _ICC.findall(x)})
                if icc:
                    r["icc_profile"] = icc[0]
                tool = _TOOL.search(x)
                if tool:
                    r["created_with"] = dec(tool.group(1))
            else:
                r["note"] = "no embedded metadata packet"
    return r


def _inspect_outputs(p):
    stem = os.path.splitext(os.path.basename(p["source"]))[0]
    try:
        label = design_naming.slug(stem, "file")
    except design_naming.BadName:
        label = "File"
    return [f"{label}_Inspect_v01.json"]


def _inspect_run(ctx):
    name = ctx["outputs_expected"][0]
    report = inspect_file(ctx["inputs"]["source"])
    out = os.path.join(ctx["staging"], name)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return {"outputs": {name: out}, "notes": [f"{report['kind']}, {report['size_bytes']} bytes"]}


INSPECT = {"id": "file.inspect", "app": "none", "title": "Inspect a design file (no app is opened)",
           "params": {"source": {"type": "file"}},
           "plan": lambda p: ["Copy the file into the project's 02_Assets folder (the original is never opened for writing)",
                              "Read the copy's header and embedded metadata: type, size, colour mode, profile, fonts declared",
                              "Write the report as a JSON file in 05_Exports"],
           "outputs": _inspect_outputs, "run": _inspect_run}


def register_builtin():
    if INSPECT["id"] not in design_runner.OPERATIONS:
        design_runner.register(INSPECT)
    import design_photoshop
    design_photoshop.register()
    import design_illustrator
    design_illustrator.register()
    import design_indesign
    design_indesign.register()
    import design_generate
    design_generate.register()
    import design_split
    design_split.register()
    import design_layer_names
    design_layer_names.register()
    import design_aftereffects
    design_aftereffects.register()
    import design_premiere
    design_premiere.register()
    import design_video
    design_video.register()
    import design_music
    design_music.register()
