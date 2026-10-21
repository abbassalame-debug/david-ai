"""design_generate.py -- the fixed picture-generation operation of the Design portal (Piece 6a). The workflow is written here, by hand, and tested; a model never writes one.

  generate.image   one or more candidate pictures from a text prompt on the local ComfyUI, with Qwen-Image (default; the engine of the layered chain) or Z-Image Turbo (about 5x faster). ALWAYS without text: Qwen-Image cannot spell Arabic (measured, plan section 18), so a prompt that
                   contains Arabic letters is refused and the negative prompt always forbids letters and words. Every word of a design is set later in an Adobe app with a real font.

Same safety as every operation: plan -> the designer's yes -> run -> delivery. There is no input file to protect. ComfyUI is started by the operation when it is not running and stopped
again afterwards; a ComfyUI that was already running (the designer's own session) is left alone. Nothing leaves this computer.
"""
from david_config import COMFYUI_DIR
import os
import re
import sys
import json
import time
import uuid
import random
import shutil
import contextlib
import subprocess
import urllib.request

import design_com
import design_naming
import design_runner as R

COMFY_URL = "http://127.0.0.1:8188"
COMFY_DIR = str(COMFYUI_DIR)
ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F]")
NEGATIVE_BASE = "text, letters, words, writing, watermark, logo, typography, numbers, blurry, low quality, deformed"
ASPECTS = {"draft": {"square": (640, 640), "portrait": (512, 768), "landscape": (768, 512)},
           "standard": {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832)}}
STEPS = {"draft": 12, "standard": 20}
ZIMAGE_STEPS = 8
NO_TEXT_REQUEST = ", no text, no letters, no words, no numbers, no watermark, no logo"
MAX_CANDIDATES = 6
SERVER_WAIT = 180
JOB_TIMEOUT = 1500


class GenerateError(design_com.AppError):
    pass


def qwen_workflow(prompt, negative, width, height, steps, seed, prefix="david_gen"):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed, "steps": steps,
                                                   "cfg": 2.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


def zimage_workflow(prompt, width, height, seed, prefix="david_gen"):
    """Z-Image Turbo (distilled: 8 steps, cfg 1). It has no negative prompt (cfg 1 ignores it), so the 'no text' rule is a request added to the prompt, not a ban like Qwen-Image's."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed, "steps": ZIMAGE_STEPS, "cfg": 1.0,
                                                   "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


def _api(path, data=None, timeout=30):
    req = urllib.request.Request(COMFY_URL + path, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _is_up():
    try:
        urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=3)
        return True
    except Exception:
        return False


def _start_server():
    proc = subprocess.Popen([sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"], cwd=COMFY_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    end = time.time() + SERVER_WAIT
    while time.time() < end:
        if proc.poll() is not None:
            raise GenerateError("ComfyUI stopped while starting (check <DAVID_HOME>\\ComfyUI)")
        if _is_up():
            return proc
        time.sleep(2)
    _stop_server(proc)
    raise GenerateError(f"ComfyUI did not start within {SERVER_WAIT // 60} minutes")


def _stop_server(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    end = time.time() + 30
    while time.time() < end and _is_up():
        time.sleep(1)


def _run_prompt(workflow, timeout):
    return _run_prompt_all(workflow, timeout)[0]


def _run_prompt_all(workflow, timeout, out_node="10"):
    pid = _api("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())})["prompt_id"]
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(3)
        h = _api("/history/" + pid)
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") != "success":
                msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
                raise GenerateError("ComfyUI reported an error: " + (msgs[0] if msgs else "the job did not finish")[:200])
            return [os.path.join(COMFY_DIR, "output", im.get("subfolder", ""), im["filename"]) for im in h[pid]["outputs"][out_node]["images"]]
    raise GenerateError(f"ComfyUI did not finish within {timeout // 60} minutes; the job was stopped")


@contextlib.contextmanager
def comfy_session():
    """Yields True when this call started ComfyUI. A ComfyUI that was already running is never stopped; one started here is interrupted on an error and always stopped."""
    started = None
    if not _is_up():
        started = _start_server()
    try:
        yield started is not None
    except Exception:
        if started:
            try:
                _api("/interrupt", {}, timeout=5)
            except Exception:
                pass
        raise
    finally:
        if started:
            _stop_server(started)


def default_generate(spec, workdir):
    """-> {"png": first picture, "pngs": all candidates (in workdir), "seconds": n, "server_started": bool}. Candidate i uses seed + i; one ComfyUI session serves them all."""
    t0 = time.time()
    count = int(spec.get("count") or 1)
    pngs = []
    with comfy_session() as started:
        for i in range(count):
            seed = (int(spec["seed"]) + i) % (2 ** 32)
            if spec.get("engine") == "zimage":
                wf = zimage_workflow(spec["prompt"] + NO_TEXT_REQUEST, spec["width"], spec["height"], seed)
            else:
                wf = qwen_workflow(spec["prompt"], spec["negative"], spec["width"], spec["height"], spec["steps"], seed)
            src = _run_prompt(wf, JOB_TIMEOUT)
            if not os.path.isfile(src):
                raise GenerateError("ComfyUI said it saved the picture but the file is not there")
            dst = os.path.join(workdir, "generated.png" if count == 1 else f"generated_{i + 1:02d}.png")
            shutil.copyfile(src, dst)
            pngs.append(dst)
    return {"png": pngs[0], "pngs": pngs, "seconds": round(time.time() - t0), "server_started": started}


TRANSPORT = default_generate


def _check(p):
    if ARABIC_RE.search(p["prompt"]):
        raise R.Refused("The prompt contains Arabic letters. The generator cannot spell Arabic (it makes well-formed but wrong words), so describe the picture without words: "
                        "the Arabic text is added afterwards with a real font in Illustrator or Photoshop.")


def _outputs(p):
    _check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_GenImage_v{i:02d}.png" for i in range(1, int(p.get("count") or 1) + 1)]


def _dims(p):
    return ASPECTS[p["size"]][p["shape"]]


def _steps(p):
    w, h = _dims(p)
    count = int(p.get("count") or 1)
    if p.get("engine") == "zimage":
        how = f"Z-Image Turbo, {ZIMAGE_STEPS} steps (fast; it has no negative prompt, so 'no text' is asked for in the prompt and is not guaranteed)"
    else:
        how = f"Qwen-Image, {p.get('steps') or STEPS[p['size']]} steps (the picture can never contain text: letters and words are forbidden in the settings)"
    return [f"Check that the local image generator (ComfyUI) is running; if it is not, start it for this job and stop it again afterwards (one that is already running is left alone)",
            f"Make {'one picture' if count == 1 else f'{count} candidate pictures (seed, seed+1, ...)'} of {w} x {h} px with {how}, from your description",
            "Save " + ("it as a PNG" if count == 1 else "them as PNGs v01, v02, ...") + " in 05_Exports and show " + ("it" if count == 1 else "a contact sheet of all candidates") +
            " as the preview; nothing of yours is read or changed. The seed used is written in the notes so the same picture can be made again"]


def _run(ctx):
    p = ctx["params"]
    names = ctx["outputs_expected"]
    w, h = _dims(p)
    engine = p.get("engine") or "qwen"
    steps = ZIMAGE_STEPS if engine == "zimage" else (p.get("steps") or STEPS[p["size"]])
    seed = p["seed"] if p.get("seed") is not None else random.SystemRandom().randrange(0, 2 ** 32)
    spec = {"prompt": p["prompt"], "negative": NEGATIVE_BASE, "width": w, "height": h, "steps": steps, "seed": seed, "engine": engine, "count": len(names)}
    try:
        res = TRANSPORT(spec, ctx["staging"])
    except GenerateError:
        raise
    except design_com.AppError as e:
        raise GenerateError(str(e))
    pngs = res.get("pngs") or [res["png"]]
    if len(pngs) != len(names) or any(not os.path.isfile(x) or os.path.getsize(x) == 0 for x in pngs):
        raise GenerateError("The generator said it made a picture but the file is not there" if len(names) == 1 else "The generator said it made the pictures but a file is missing or empty")
    outputs = {}
    for name, src in zip(names, pngs):
        out = os.path.join(ctx["staging"], name)
        shutil.copyfile(src, out)
        outputs[name] = out
    prev = os.path.join(ctx["staging"], "preview.png")
    if len(pngs) == 1:
        shutil.copyfile(pngs[0], prev)
    else:
        contact_sheet(pngs, prev)
    seeds = ", ".join(str((int(seed) + i) % (2 ** 32)) for i in range(len(pngs)))
    notes = [f"{engine}: {len(pngs)} picture(s) {w} x {h} px, {steps} steps, seed{'s' if len(pngs) > 1 else ''} {seeds}, {res['seconds']} s"
             + (" (ComfyUI was started for this job and stopped again)" if res.get("server_started") else " (ComfyUI was already running and was left running)")]
    return {"outputs": outputs, "preview": prev, "notes": notes}


def contact_sheet(files, out, height=480):
    """One preview image with every candidate side by side, numbered v01, v02, ..."""
    from PIL import Image, ImageDraw
    ims = []
    for f in files:
        im = Image.open(f).convert("RGB")
        ims.append(im.resize((max(1, int(im.width * height / im.height)), height)))
    sheet = Image.new("RGB", (sum(i.width for i in ims) + 8 * (len(ims) - 1), height), (30, 30, 30))
    x = 0
    for k, im in enumerate(ims, 1):
        sheet.paste(im, (x, 0))
        ImageDraw.Draw(sheet).rectangle([x, 0, x + 54, 22], fill=(0, 0, 0))
        ImageDraw.Draw(sheet).text((x + 6, 5), f"v{k:02d}", fill=(255, 255, 255))
        x += im.width + 8
    sheet.save(out)


GENERATE = {"id": "generate.image", "app": "comfyui", "title": "Generate one or more candidate pictures (Qwen-Image or the faster Z-Image Turbo; no text asked for; local, nothing leaves this computer)",
            "params": {"prompt": {"type": "str", "max": 1500}, "name": {"type": "str"}, "shape": {"type": "enum", "values": ["square", "portrait", "landscape"], "required": False, "default": "square"},
                       "size": {"type": "enum", "values": ["draft", "standard"], "required": False, "default": "standard"},
                       "steps": {"type": "int", "min": 8, "max": 50, "required": False}, "seed": {"type": "int", "min": 0, "max": 4294967295, "required": False},
                       "engine": {"type": "enum", "values": ["qwen", "zimage"], "required": False, "default": "qwen"},
                       "count": {"type": "int", "min": 1, "max": MAX_CANDIDATES, "required": False}},
            "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if GENERATE["id"] not in R.OPERATIONS:
        R.register(GENERATE)
