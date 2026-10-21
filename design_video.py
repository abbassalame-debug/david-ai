"""design_video.py -- the fixed video operation of the Design portal (Piece 10): video.image_to_video.

  video.image_to_video   one or more short candidate clips (1 or 2 seconds, 24 fps, with generated sound) that start from a picture of the project, made by LTX-2 (19B distilled, fp8)
                         on the local ComfyUI. The first frame is the picture (the composition is yours); the movement is asked for in the prompt.

Measured 2026-10-13 on this laptop (RTX 3080 16 GB): about 130-150 s per 2 s clip at 480x832, and the whole card (16.1 of 16.4 GB). So it is a SPECIALIST job: the language models are
evicted first (the VRAM controller's Mode E) and the previous mode comes back afterwards, ComfyUI is started for the job and stopped again. Movement can drift after about a second
(faces overlap, or the clip cuts to another scene), so every candidate is checked automatically for hard cuts and sudden jumps and the notes say which ones to look at.
Same safety as every operation: plan -> the designer's yes -> run on a copy -> delivery; the original picture is never opened. Nothing leaves this computer.
"""
import os
import re
import sys
import time
import random
import shutil
import uuid

import design_com
import design_gpu
import design_generate as G
import design_naming
import design_runner as R

SOURCE_EXT = (".png", ".jpg", ".jpeg", ".webp")
CKPT = "ltx-2-19b-distilled-fp8.safetensors"
TEXT_ENCODER = "gemma_3_12B_it_fp8_scaled.safetensors"
SIZES = {"portrait": (480, 832), "landscape": (832, 480)}
FRAMES = {"1": 25, "2": 49}
FPS = 24
MAX_CANDIDATES = 4
JOB_TIMEOUT = 3600
HARD_CUT_STEP = 20.0       # mean change between two neighbouring frames (0-255): above this the clip cut to another scene (measured: 28 on the food clip)
STILL_MOTION = 0.2         # average change per frame below this: the clip is practically a still (measured: 0.09-0.10 on a gentle push-in; 2.3-3.5 on clips with people)
SUDDEN_STEP = 8.0          # a jump this size AND 10x the clip's usual step: something moved suddenly (measured: 11 on the friends clip, <= 6 on a clean clip)
NEGATIVE = "blurry, distorted, low quality, text, watermark, subtitles"


class VideoError(design_com.AppError):
    pass


def ltx_workflow(image_name, prompt, width, height, frames, fps, seed, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": CKPT}},
        "3": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": TEXT_ENCODER, "ckpt_name": CKPT, "device": "default"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": NEGATIVE}},
        "6": {"class_type": "LTXVConditioning", "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(fps)}},
        "7": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1}},
        "8": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": frames, "frame_rate": fps, "batch_size": 1, "audio_vae": ["2", 0]}},
        "20": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "21": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["1", 2], "image": ["20", 0], "latent": ["7", 0], "strength": 1.0, "bypass": False}},
        "9": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["21", 0], "audio_latent": ["8", 0]}},
        "10": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["6", 0], "negative": ["6", 1], "cfg": 1.0}},
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "12": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1., 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
        "13": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["13", 0], "guider": ["10", 0], "sampler": ["11", 0], "sigmas": ["12", 0], "latent_image": ["9", 0]}},
        "15": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["14", 0]}},
        "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["1", 2]}},
        "17": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["15", 1], "audio_vae": ["2", 0]}},
        "18": {"class_type": "CreateVideo", "inputs": {"images": ["16", 0], "fps": float(fps), "audio": ["17", 0]}},
        "19": {"class_type": "SaveVideo", "inputs": {"video": ["18", 0], "filename_prefix": "video/" + prefix, "format": "mp4", "codec": "h264"}},
    }


def analyze_clip(path):
    """What a person would check first: length, sound, how much moves, and whether the clip cuts or jumps. -> dict (no model involved)."""
    import av
    import numpy as np
    with av.open(path) as c:
        v = c.streams.video[0]
        fr = [np.asarray(x.to_image().resize((96, 160)), dtype=np.float32) for x in c.decode(v)]
        fps = float(v.average_rate)
    has_audio = False
    try:
        with av.open(path) as c2:
            has_audio = bool(c2.streams.audio)
    except Exception:
        pass
    steps = [float(np.abs(fr[i + 1] - fr[i]).mean()) for i in range(len(fr) - 1)] or [0.0]
    med, mx = float(np.median(steps)), max(steps)
    at = int(np.argmax(steps)) + 1
    verdict = "ok"
    if mx > HARD_CUT_STEP:
        verdict = f"hard cut to another scene at {at / fps:.1f} s (frame {at})"
    elif mx > SUDDEN_STEP and mx > 10 * max(med, 0.05):
        verdict = f"sudden jump at {at / fps:.1f} s (frame {at}): look at it"
    if verdict == "ok" and float(np.mean(steps)) < STILL_MOTION:
        verdict = "ok, but almost no movement (it looks like a still): ask for more motion if you want it"
    return {"frames": len(fr), "seconds": round(len(fr) / fps, 2), "audio": has_audio, "motion": round(float(np.mean(steps)), 2), "max_step": round(mx, 1), "verdict": verdict,
            "drift_from_first": round(float(np.abs(fr[-1] - fr[0]).mean()), 1)}


def frames_sheet(mp4s, out, height=300):
    """Preview: one row per candidate, first / middle / last frame, numbered v01, v02, ..."""
    import av
    from PIL import Image, ImageDraw
    rows = []
    for k, f in enumerate(mp4s, 1):
        with av.open(f) as c:
            ims = [x.to_image().convert("RGB") for x in c.decode(c.streams.video[0])]
        pick = [ims[0], ims[len(ims) // 2], ims[-1]]
        pick = [i.resize((max(1, int(i.width * height / i.height)), height)) for i in pick]
        row = Image.new("RGB", (sum(i.width for i in pick) + 8 * 2, height), (30, 30, 30))
        x = 0
        for i in pick:
            row.paste(i, (x, 0))
            x += i.width + 8
        ImageDraw.Draw(row).rectangle([0, 0, 54, 22], fill=(0, 0, 0))
        ImageDraw.Draw(row).text((6, 5), f"v{k:02d}", fill=(255, 255, 255))
        rows.append(row)
    sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows) + 8 * (len(rows) - 1)), (30, 30, 30))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 8
    sheet.save(out)


def default_video(spec, workdir):
    """-> {"mp4s": [...], "seconds": n, "server_started": bool, "card": "mode E"|"unload"}. Candidate i uses seed + i. One ComfyUI session, the card handed over for the whole job."""
    t0 = time.time()
    count = int(spec.get("count") or 1)
    mp4s = []
    with design_gpu.heavy_job() as card, G.comfy_session() as started:
        name = f"david_video_{uuid.uuid4().hex[:10]}{os.path.splitext(spec['image'])[1].lower()}"
        shutil.copyfile(spec["image"], os.path.join(G.COMFY_DIR, "input", name))
        for i in range(count):
            seed = (int(spec["seed"]) + i) % (2 ** 32)
            wf = ltx_workflow(name, spec["prompt"], spec["width"], spec["height"], spec["frames"], FPS, seed, f"david_{uuid.uuid4().hex[:8]}")
            pid = G._api("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
            end = time.time() + JOB_TIMEOUT
            src = None
            while time.time() < end and src is None:
                time.sleep(3)
                h = G._api("/history/" + pid)
                if pid in h:
                    st = h[pid].get("status", {})
                    if st.get("status_str") != "success":
                        msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m and m[0] == "execution_error"]
                        raise VideoError("ComfyUI reported an error: " + (msgs[0] if msgs else "the job did not finish")[:200])
                    f = h[pid]["outputs"]["19"]["images"][0]
                    src = os.path.join(G.COMFY_DIR, "output", f.get("subfolder", ""), f["filename"])
            if src is None:
                try:
                    G._api("/interrupt", {}, timeout=5)
                except Exception:
                    pass
                raise VideoError(f"ComfyUI did not finish within {JOB_TIMEOUT // 60} minutes; the job was stopped")
            dst = os.path.join(workdir, f"clip_{i + 1:02d}.mp4")
            shutil.copyfile(src, dst)
            mp4s.append(dst)
    return {"mp4s": mp4s, "seconds": round(time.time() - t0), "server_started": started, "card": card.via}


TRANSPORT = default_video


def _stem(path):
    try:
        return design_naming.slug(os.path.splitext(os.path.basename(path))[0], "file")
    except design_naming.BadName:
        return "File"


def _check(p):
    if os.path.splitext(p["source"])[1].lower() not in SOURCE_EXT:
        raise R.Refused("This operation starts from a picture (" + ", ".join(SOURCE_EXT) + "): make or choose one first, for example with generate.image.")
    if G.ARABIC_RE.search(p["prompt"]):
        raise R.Refused("The prompt contains Arabic letters. Describe the movement and the sounds in English; any Arabic text is added afterwards with a real font in an Adobe app.")


def _outputs(p):
    _check(p)
    return [f"{_stem(p['source'])}_Video_v{i:02d}.mp4" for i in range(1, int(p.get("count") or 1) + 1)]


def _steps(p):
    w, h = SIZES[p["shape"]]
    n, sec = int(p.get("count") or 1), p["seconds"]
    return ["Copy the picture into the project's 02_Assets folder (the original is never opened)",
            "Clear the graphics card for this job (the language models are evicted with the VRAM controller's Mode E and the previous mode is put back afterwards) and start ComfyUI if it is not running (stopped again afterwards)",
            f"Make {'one clip' if n == 1 else f'{n} candidate clips (seed, seed+1, ...)'} of {sec} s at {FPS} fps, {w} x {h} px, with LTX-2: the first frame is your picture (centre-cropped to the shape), "
            "the movement and sounds come from your description; each clip takes about 2.5 minutes and has generated sound",
            "Check every clip automatically for a hard cut to another scene and for sudden jumps, and say which ones to look at (movement can drift after about a second)",
            "Deliver the MP4s in 05_Exports and a sheet of first / middle / last frames per candidate as the preview; the seed used is written in the notes"]


def _run(ctx):
    p = ctx["params"]
    names = ctx["outputs_expected"]
    w, h = SIZES[p["shape"]]
    seed = p["seed"] if p.get("seed") is not None else random.SystemRandom().randrange(0, 2 ** 32)
    spec = {"image": ctx["inputs"]["source"], "prompt": p["prompt"], "width": w, "height": h, "frames": FRAMES[p["seconds"]], "seed": seed, "count": len(names)}
    try:
        res = TRANSPORT(spec, ctx["staging"])
    except VideoError:
        raise
    except design_com.AppError as e:
        raise VideoError(str(e))
    mp4s = res.get("mp4s") or []
    if len(mp4s) != len(names) or any(not os.path.isfile(x) or os.path.getsize(x) == 0 for x in mp4s):
        raise VideoError("The generator said it made the clips but a file is missing or empty")
    outputs, notes = {}, []
    for k, (name, src) in enumerate(zip(names, mp4s), 1):
        out = os.path.join(ctx["staging"], name)
        shutil.copyfile(src, out)
        outputs[name] = out
        try:
            a = analyze_clip(out)
            notes.append(f"v{k:02d}: {a['seconds']} s, sound {'yes' if a['audio'] else 'NO'}, movement {a['motion']}, {a['verdict']}")
        except Exception as e:
            notes.append(f"v{k:02d}: could not be analysed ({type(e).__name__})")
    prev = os.path.join(ctx["staging"], "preview.png")
    try:
        frames_sheet([outputs[n] for n in names], prev)
    except Exception:
        prev = None
    seeds = ", ".join(str((int(seed) + i) % (2 ** 32)) for i in range(len(names)))
    notes.insert(0, f"LTX-2 distilled fp8, {w} x {h} px, {p['seconds']} s, {len(names)} clip(s), seed{'s' if len(names) > 1 else ''} {seeds}, {res['seconds']} s, card cleared via {res.get('card')}"
                 + (" (ComfyUI was started for this job and stopped again)" if res.get("server_started") else " (ComfyUI was already running and was left running)"))
    return {"outputs": outputs, "preview": prev, "notes": notes}


VIDEO = {"id": "video.image_to_video", "app": "comfyui", "title": "Make short video clips from a picture with LTX-2 (with generated sound; uses the whole graphics card; local)",
         "params": {"source": {"type": "file"}, "prompt": {"type": "str", "max": 1500},
                    "shape": {"type": "enum", "values": list(SIZES), "required": False, "default": "portrait"},
                    "seconds": {"type": "enum", "values": list(FRAMES), "required": False, "default": "2"},
                    "count": {"type": "int", "min": 1, "max": MAX_CANDIDATES, "required": False},
                    "seed": {"type": "int", "min": 0, "max": 4294967295, "required": False}},
         "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if VIDEO["id"] not in R.OPERATIONS:
        R.register(VIDEO)
