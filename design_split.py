"""design_split.py -- the fixed layer-splitting operation of the Design portal (Piece 6b). The workflow is written here, by hand, and tested; a model never writes one.

  generate.split_layers   one picture -> several full RGBA layers (each a component of the picture on its own transparent layer, hidden parts filled in) with Qwen-Image-Layered on the
                          local ComfyUI. Delivers the layers as numbered PNGs, bottom first, plus a small JSON manifest, and checks that the layers recompose into the original picture.

The workflow is the one in ComfyUI's own template "Image to Layers (Qwen-Image-Layered)" (reference-latent conditioning on the VAE-encoded picture, an empty layered latent of layers + 1
frames, the first decoded frame being the full picture, which is dropped). Same safety as every operation: plan -> the designer's yes -> run on a COPY -> delivery. ComfyUI is started when
needed and stopped again, as for generate.image. Nothing leaves this computer.
"""
import os
import json
import time
import shutil
import random
import uuid

from PIL import Image, ImageChops, ImageStat

import design_com
import design_generate as G
import design_naming
import design_runner as R

COMFY_INPUT = os.path.join(G.COMFY_DIR, "input")
MAX_LAYERS = 8
TARGET = {"draft": 640, "standard": 1024}
STEPS = {"draft": 30, "standard": 50}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
RECOMPOSE_WARN = 12.0
JOB_TIMEOUT = 3600


class SplitError(G.GenerateError):
    pass


def layered_workflow(image_name, layers, width, height, steps, seed, prefix="david_layers"):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_layered_fp8mixed.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_layered_vae.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["6", 0], "latent": ["5", 0]}},
        "9": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["5", 0]}},
        "10": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 1.0}},
        "11": {"class_type": "EmptyQwenImageLayeredLatentImage", "inputs": {"width": width, "height": height, "layers": layers, "batch_size": 1}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["8", 0], "negative": ["9", 0], "latent_image": ["11", 0], "seed": seed, "steps": steps,
                                                    "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "LatentCutToBatch", "inputs": {"samples": ["12", 0], "dim": "t", "slice_size": 1}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": prefix}},
    }


def fit_size(w, h, side):
    """The picture's own shape at about side x side pixels in total, both sides multiples of 16 (the model's grid)."""
    k = (side * side / (w * h)) ** 0.5
    return max(16, round(w * k / 16) * 16), max(16, round(h * k / 16) * 16)


def recompose(layers):
    base = layers[0].copy()
    for im in layers[1:]:
        base = Image.alpha_composite(base, im)
    return base


def mean_diff(a, b):
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    return round(sum(ImageStat.Stat(d).mean) / 3, 1)


def order_layers(frames, original):
    """Layers as the model returned them, and which order recomposes the picture best. -> (layers bottom first, 'as given' | 'reversed', difference)."""
    a, b = recompose(frames), recompose(list(reversed(frames)))
    da, db = mean_diff(a, original), mean_diff(b, original)
    return (frames, "as given", da) if da <= db else (list(reversed(frames)), "reversed", db)


def default_split(spec, workdir):
    """-> {"frames": [paths of the layer PNGs in workdir, as returned], "seconds": n, "server_started": bool}"""
    t0 = time.time()
    token = uuid.uuid4().hex[:8]
    in_name = f"david_split_{token}.png"
    in_path = os.path.join(COMFY_INPUT, in_name)
    os.makedirs(COMFY_INPUT, exist_ok=True)
    shutil.copyfile(spec["image"], in_path)
    try:
        with G.comfy_session() as started:
            paths = G._run_prompt_all(layered_workflow(in_name, spec["layers"], spec["width"], spec["height"], spec["steps"], spec["seed"], f"david_layers_{token}"), JOB_TIMEOUT, "15")
            frames = []
            for i, src in enumerate(paths):
                if not os.path.isfile(src):
                    raise SplitError("ComfyUI said it saved the layers but a file is not there")
                dst = os.path.join(workdir, f"frame_{i:02d}.png")
                shutil.copyfile(src, dst)
                frames.append(dst)
    finally:
        try:
            os.unlink(in_path)
        except OSError:
            pass
    return {"frames": frames, "seconds": round(time.time() - t0), "server_started": started}


TRANSPORT = default_split


def _check(p):
    if os.path.splitext(p["picture"])[1].lower() not in IMAGE_EXT:
        raise R.Refused("The picture must be a " + ", ".join(IMAGE_EXT) + " file.")
    try:
        with Image.open(p["picture"]) as im:
            w, h = im.size
    except Exception:
        raise R.Refused("The picture could not be read as an image.")
    if min(w, h) < 256:
        raise R.Refused(f"The picture is {w} x {h} px; it must be at least 256 px on each side to be split into layers.")
    if max(w, h) / min(w, h) > 3:
        raise R.Refused("The picture is too narrow or too wide (more than 3 to 1) to be split into layers.")


def _label(p):
    try:
        return design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))


def _outputs(p):
    _check(p)
    label = _label(p)
    return [f"{label}_Layer{n:02d}_v01.png" for n in range(1, p["layers"] + 1)] + [f"{label}_Layers_v01.json"]


def _steps(p):
    with Image.open(p["picture"]) as im:
        w, h = fit_size(im.size[0], im.size[1], TARGET[p["size"]])
    st = p.get("steps") or STEPS[p["size"]]
    return ["Copy the picture into the project's 02_Assets folder (the original is never opened)",
            "Check that the local image generator (ComfyUI) is running; if it is not, start it for this job and stop it again afterwards (one that is already running is left alone)",
            f"Split the picture, scaled to {w} x {h} px, into {p['layers']} transparent layers with Qwen-Image-Layered ({st} steps). This is slow: expect many minutes",
            f"Check that the layers put back on top of each other look like your picture (the difference is written in the notes; over {RECOMPOSE_WARN:g} out of 255 is flagged) and put them in order, bottom first",
            f"Save {p['layers']} numbered PNG layers and a small JSON file (order, size, seed) in 05_Exports; the first layer is the bottom one. The layers have no names yet: you or David name them when they are assembled"]


def _run(ctx):
    p = ctx["params"]
    outs = ctx["outputs_expected"]
    src = ctx["inputs"]["picture"]
    with Image.open(src) as im:
        original = im.convert("RGB")
    w, h = fit_size(original.size[0], original.size[1], TARGET[p["size"]])
    work = os.path.join(ctx["staging"], "_split_in.png")
    original.resize((w, h), Image.LANCZOS).save(work)
    steps = p.get("steps") or STEPS[p["size"]]
    seed = p["seed"] if p.get("seed") is not None else random.SystemRandom().randrange(0, 2 ** 32)
    spec = {"image": work, "layers": p["layers"], "width": w, "height": h, "steps": steps, "seed": seed}
    try:
        res = TRANSPORT(spec, ctx["staging"])
    except SplitError:
        raise
    except design_com.AppError as e:
        raise SplitError(str(e))
    frames = res["frames"]
    if len(frames) != p["layers"] + 1:
        raise SplitError(f"The splitter returned {len(frames)} picture(s); {p['layers'] + 1} were expected (the full picture and {p['layers']} layers)")
    layers = [Image.open(x).convert("RGBA") for x in frames[1:]]
    if any(im.size != (w, h) for im in layers):
        raise SplitError("The layers do not all have the size of the picture")
    ordered, how, diff = order_layers(layers, original.resize((w, h)))
    notes = [f"{w} x {h} px, {p['layers']} layers, {steps} steps, seed {seed}, {res['seconds']} s; order: {how}; recomposed difference {diff} of 255"
             + (" (ComfyUI was started for this job and stopped again)" if res.get("server_started") else " (ComfyUI was already running and was left running)")]
    if diff > RECOMPOSE_WARN:
        notes.append(f"WARNING: the layers do not put back together into your picture (difference {diff}); look at them before using them")
    manifest = {"schema": "david_layers/v1", "source": os.path.basename(p["picture"]), "size": [w, h], "seed": seed, "steps": steps, "order": "bottom first", "model_order": how,
                "recomposed_difference": diff, "layers": []}
    delivered = {}
    for n, (im, name) in enumerate(zip(ordered, outs[:-1]), 1):
        path = os.path.join(ctx["staging"], name)
        im.save(path)
        a = im.getchannel("A")
        hist = a.histogram()
        visible = 1 - sum(hist[:11]) / (w * h)
        empty = visible < 0.001
        manifest["layers"].append({"file": name, "name": f"Layer {n}", "opaque_share": round(sum(hist[200:]) / (w * h), 3), "visible_share": round(visible, 4), "empty": empty})
        if empty:
            notes.append(f"WARNING: layer {n} is empty (nothing visible on it); a chain leaves such a layer out")
        delivered[name] = path
    mpath = os.path.join(ctx["staging"], outs[-1])
    json.dump(manifest, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    delivered[outs[-1]] = mpath
    prev = os.path.join(ctx["staging"], "preview.png")
    recompose(ordered).convert("RGB").save(prev)
    return {"outputs": delivered, "preview": prev, "notes": notes}


SPLIT = {"id": "generate.split_layers", "app": "comfyui", "title": "Split a picture into transparent layers, one per component (Qwen-Image-Layered; local, slow)",
         "params": {"picture": {"type": "file"}, "name": {"type": "str"}, "layers": {"type": "int", "min": 2, "max": MAX_LAYERS, "required": False, "default": 4},
                    "size": {"type": "enum", "values": ["draft", "standard"], "required": False, "default": "draft"},
                    "steps": {"type": "int", "min": 10, "max": 80, "required": False}, "seed": {"type": "int", "min": 0, "max": 4294967295, "required": False}},
         "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if SPLIT["id"] not in R.OPERATIONS:
        R.register(SPLIT)
