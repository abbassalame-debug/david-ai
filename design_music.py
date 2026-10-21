"""design_music.py -- the fixed music operation of the Design portal: music.generate.

  music.generate   one or more candidate instrumental tracks (5 to 60 seconds) from a text description, made by ACE-Step v1 (3.5B) on the local ComfyUI.

Measured 2026-10-13 on this laptop: 9-27 s for 15 s of music, 8 GB of the card. Candidates are cheap, so ask for several and choose by ear: the manager liked an "arabic pop,
darbuka, lute" description and not a plain "pop 160". The operation cannot hear. What it measures for every candidate: length, loudness, silence, clipping, and the tempo against a target
(ACE-Step follows a tempo only roughly: 160 asked, 152 got; 128 asked, 81 got), and it says which candidate is closest. A broken track is flagged, a good-sounding one is not promised.
Same safety as every operation: plan -> the designer's yes -> run -> delivery. The card is handed over like the video operation (Mode E and back). Nothing leaves this computer.
"""
import os
import re
import time
import random
import shutil
import uuid

import design_com
import design_gpu
import design_generate as G
import design_naming
import design_runner as R

CKPT = "ace_step_v1_3.5b.safetensors"
LYRICS = "[instrumental]"
MAX_CANDIDATES = 6
JOB_TIMEOUT = 1800
QUIET_DB = -32.0           # average level below this: too quiet to use without boosting
SILENT_DB = -50.0
SILENCE_SHARE = 0.10       # more than this share of the track below SILENT_DB: gaps
CLIP_PEAK = 0.99


class MusicError(design_com.AppError):
    pass


def ace_workflow(tags, seconds, seed, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "TextEncodeAceStepAudio", "inputs": {"clip": ["1", 1], "tags": tags, "lyrics": LYRICS, "lyrics_strength": 0.99}},
        "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "4": {"class_type": "EmptyAceStepLatentAudio", "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "seed": seed, "steps": 50, "cfg": 5.0,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAudioMP3", "inputs": {"audio": ["7", 0], "filename_prefix": "audio/" + prefix, "quality": "V0"}},
    }


def tempo_error(measured, target):
    """Relative distance of the measured tempo to the target, forgiving the half / double ambiguity every beat tracker has."""
    if not target or not measured:
        return None
    return min(abs(measured * k - target) / target for k in (0.5, 1.0, 2.0))


def analyze_track(path, target_bpm=None):
    """What can be measured without ears. -> dict with a plain-words verdict."""
    import numpy as np
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    if len(y) == 0:
        return {"seconds": 0.0, "verdict": "EMPTY"}
    rms = librosa.feature.rms(y=y)[0]
    db_frames = 20 * np.log10(rms + 1e-9)
    avg_db = float(20 * np.log10(np.sqrt((y ** 2).mean()) + 1e-9))
    silence = float((db_frames < SILENT_DB).mean())
    peak = float(np.abs(y).max())
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    bt = librosa.frames_to_time(beats, sr=sr)
    steadiness = float(np.std(np.diff(bt)) / np.mean(np.diff(bt))) if len(bt) > 3 else None
    err = tempo_error(tempo, target_bpm)
    problems = []
    if avg_db < QUIET_DB:
        problems.append(f"very quiet ({avg_db:.0f} dBFS)")
    if silence > SILENCE_SHARE:
        problems.append(f"gaps of silence ({silence:.0%} of the track)")
    if peak >= CLIP_PEAK:
        problems.append("clipping")
    if err is not None and err > 0.15:
        problems.append(f"tempo {tempo:.0f} BPM is {err:.0%} from the {target_bpm} asked")
    return {"seconds": round(len(y) / sr, 1), "level_db": round(avg_db, 1), "peak": round(peak, 2), "silence": round(silence, 2), "tempo": round(tempo), "steadiness": None if steadiness is None else round(steadiness, 3),
            "tempo_error": None if err is None else round(err, 3), "problems": problems, "verdict": "ok" if not problems else "; ".join(problems)}


def waveform_sheet(files, out, width=900, row=70):
    """Preview: a loudness curve per candidate, numbered v01, v02, ..."""
    import numpy as np
    import librosa
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (width, row * len(files) + 6 * (len(files) - 1)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for k, f in enumerate(files):
        y, sr = librosa.load(f, sr=11025, mono=True)
        bins = np.array_split(np.abs(y), min(width - 70, max(1, len(y))))
        env = np.array([b.max() if len(b) else 0 for b in bins])
        env = env / (env.max() + 1e-9)
        top = k * (row + 6)
        d.rectangle([0, top, 54, top + 22], fill=(0, 0, 0))
        d.text((6, top + 5), f"v{k + 1:02d}", fill=(255, 255, 255))
        for x, v in enumerate(env):
            h = int(v * (row - 6) / 2)
            d.line([60 + x, top + row // 2 - h, 60 + x, top + row // 2 + h], fill=(255, 200, 60))
    sheet.save(out)


def default_music(spec, workdir):
    """-> {"mp3s": [...], "seconds": n, "server_started": bool, "card": ...}. Candidate i uses seed + i."""
    t0 = time.time()
    mp3s = []
    with design_gpu.heavy_job() as card, G.comfy_session() as started:
        for i in range(int(spec["count"])):
            seed = (int(spec["seed"]) + i) % (2 ** 32)
            wf = ace_workflow(spec["tags"], spec["seconds"], seed, f"david_{uuid.uuid4().hex[:8]}")
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
                        raise MusicError("ComfyUI reported an error: " + (msgs[0] if msgs else "the job did not finish")[:200])
                    f = h[pid]["outputs"]["8"]["audio"][0]
                    src = os.path.join(G.COMFY_DIR, "output", f.get("subfolder", ""), f["filename"])
            if src is None:
                try:
                    G._api("/interrupt", {}, timeout=5)
                except Exception:
                    pass
                raise MusicError(f"ComfyUI did not finish within {JOB_TIMEOUT // 60} minutes; the job was stopped")
            dst = os.path.join(workdir, f"track_{i + 1:02d}.mp3")
            shutil.copyfile(src, dst)
            mp3s.append(dst)
    return {"mp3s": mp3s, "seconds": round(time.time() - t0), "server_started": started, "card": card.via}


TRANSPORT = default_music


def _check(p):
    if G.ARABIC_RE.search(p["prompt"]):
        raise R.Refused("The description contains Arabic letters. Describe the music in English (for example 'arabic pop, darbuka, lute'): the model reads English tags best.")
    if "vocal" in p["prompt"].lower() or "lyrics" in p["prompt"].lower():
        raise R.Refused("This operation makes instrumental music only (the lyrics are fixed to [instrumental]); take the words out of the description.")


def _outputs(p):
    _check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_Music_v{i:02d}.mp3" for i in range(1, int(p.get("count") or 1) + 1)]


def _tags(p):
    t = p["prompt"].strip()
    if p.get("bpm") and not re.search(r"\b\d{2,3}\s*bpm\b", t, re.I):
        t += f", {p['bpm']} bpm"
    return t


def _steps(p):
    n, sec = int(p.get("count") or 1), int(p.get("seconds") or 15)
    return ["Clear the graphics card for this job (the language models are evicted with the VRAM controller's Mode E and the previous mode is put back afterwards) and start ComfyUI if it is not running (stopped again afterwards)",
            f"Make {'one instrumental track' if n == 1 else f'{n} candidate instrumental tracks (seed, seed+1, ...)'} of {sec} s with ACE-Step from: \"{_tags(p)}\"; each takes about 10 to 30 seconds",
            "Measure every track (length, loudness, silence, clipping" + (f", tempo against the {p['bpm']} BPM asked" if p.get("bpm") else ", tempo") + ") and flag the broken ones and the closest to the tempo; ACE-Step follows a tempo only roughly, and the sound itself is for your ears",
            "Deliver the MP3s in 05_Exports and a loudness sheet of all candidates as the preview; the seed used is written in the notes"]


def _run(ctx):
    p = ctx["params"]
    names = ctx["outputs_expected"]
    seed = p["seed"] if p.get("seed") is not None else random.SystemRandom().randrange(0, 2 ** 32)
    spec = {"tags": _tags(p), "seconds": int(p.get("seconds") or 15), "seed": seed, "count": len(names)}
    try:
        res = TRANSPORT(spec, ctx["staging"])
    except MusicError:
        raise
    except design_com.AppError as e:
        raise MusicError(str(e))
    mp3s = res.get("mp3s") or []
    if len(mp3s) != len(names) or any(not os.path.isfile(x) or os.path.getsize(x) == 0 for x in mp3s):
        raise MusicError("The generator said it made the tracks but a file is missing or empty")
    outputs, notes, reports = {}, [], []
    for k, (name, src) in enumerate(zip(names, mp3s), 1):
        out = os.path.join(ctx["staging"], name)
        shutil.copyfile(src, out)
        outputs[name] = out
        try:
            a = analyze_track(out, p.get("bpm"))
            reports.append(a)
            notes.append(f"v{k:02d}: {a['seconds']} s, {a['level_db']} dBFS, tempo {a['tempo']} BPM, {a['verdict']}")
        except Exception as e:
            reports.append(None)
            notes.append(f"v{k:02d}: could not be analysed ({type(e).__name__})")
    if p.get("bpm"):
        scored = [(r["tempo_error"], k) for k, r in enumerate(reports, 1) if r and r.get("tempo_error") is not None]
        if scored:
            notes.append(f"closest to {p['bpm']} BPM: v{min(scored)[1]:02d} (still only roughly; listen)")
    prev = os.path.join(ctx["staging"], "preview.png")
    try:
        waveform_sheet([outputs[n] for n in names], prev)
    except Exception:
        prev = None
    seeds = ", ".join(str((int(seed) + i) % (2 ** 32)) for i in range(len(names)))
    notes.insert(0, f"ACE-Step v1 3.5B, {spec['seconds']} s, {len(names)} track(s), seed{'s' if len(names) > 1 else ''} {seeds}, {res['seconds']} s, card cleared via {res.get('card')}"
                 + (" (ComfyUI was started for this job and stopped again)" if res.get("server_started") else " (ComfyUI was already running and was left running)"))
    return {"outputs": outputs, "preview": prev, "notes": notes}


MUSIC = {"id": "music.generate", "app": "comfyui", "title": "Make candidate instrumental tracks with ACE-Step (you choose by ear; measured for length, loudness, silence, clipping, tempo; local)",
         "params": {"prompt": {"type": "str", "max": 1500}, "name": {"type": "str"},
                    "seconds": {"type": "int", "min": 5, "max": 60, "required": False, "default": 15},
                    "bpm": {"type": "int", "min": 60, "max": 200, "required": False},
                    "count": {"type": "int", "min": 1, "max": MAX_CANDIDATES, "required": False},
                    "seed": {"type": "int", "min": 0, "max": 4294967295, "required": False}},
         "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if MUSIC["id"] not in R.OPERATIONS:
        R.register(MUSIC)
