"""design_layer_names.py -- the fixed layer-naming operation of the Design portal (Piece 6b+). David's local vision model looks at each layer picture (the layers that generate.split_layers makes)
and gives it a short designer's name ("Background", "Wrap", "Herbs"), so the layered file the designer opens has meaningful layer names instead of Layer 1, Layer 2.

  generate.name_layers   the layer pictures (bottom first) -> a small JSON file with one name per layer and the ready-to-paste "names" text for illustrator/photoshop/indesign assemble_layers

The model only sees pictures it is given; nothing is changed or sent anywhere (local Ollama). A completely opaque bottom layer is called Background without asking the model. If the model cannot
answer for a layer, that layer keeps its default name 'Layer N' and the note says so. Names are cleaned to the rules of a layer name (1-40 characters, no \\ / : * ? " < > |) and made unique.
"""
from ollama_config import OLLAMA_HOST
import os
import io
import re
import json
import time
import base64
import urllib.error
import urllib.request

from PIL import Image

import design_naming
import design_runner as R

OLLAMA_CHAT_URL = OLLAMA_HOST + "/api/chat"
MODEL = "qwen3.5:9b"
CALL_TIMEOUT = 240
CALL_ATTEMPTS = 2
THUMB = 512
BAD = re.compile(r"[\x00-\x1f\\/:*?\"<>|]")
IMAGE_EXT = (".png", ".webp")
PROMPT = ("The FIRST picture is ONE layer cut out of a bigger picture: the flat grey area is empty (transparent). The SECOND picture is the whole picture with every layer on top of each other, for context. "
          "Name what is on the FIRST picture's layer in one to three English words, the way a graphic designer labels a layer, for example: Background, Table, Wrap, Herbs, Tomato, Sky, Light, Shadow, "
          "Plate, Glass, Person, Logo. If it is a soft glow or light, say Light. Answer only as JSON: {\"name\": \"...\"}")
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}


class NameError_(Exception):
    pass


def clean_name(raw, fallback):
    s = BAD.sub(" ", str(raw or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s)[:40].strip()
    if not s:
        return fallback
    return s[0].upper() + s[1:]


def unique(names):
    seen, out = {}, []
    for n in names:
        key = n.lower()
        seen[key] = seen.get(key, 0) + 1
        out.append(n if seen[key] == 1 else f"{n} {seen[key]}")
    return out


def _on_grey(path, side=THUMB):
    im = Image.open(path).convert("RGBA")
    im.thumbnail((side, side))
    bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def _composite(paths, side=THUMB):
    layers = [Image.open(p).convert("RGBA") for p in paths]
    base = layers[0].copy()
    for im in layers[1:]:
        base.alpha_composite(im.resize(base.size))
    base.thumbnail((side, side))
    return base.convert("RGB")


def _b64(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def hint_for(path, is_bottom, taken=()):
    """One sentence of context from the layer's transparency: how much of it is visible, and that only the bottom layer may be called Background."""
    a = Image.open(path).convert("RGBA").getchannel("A")
    h = a.histogram()
    total = sum(h) or 1
    covered = 1 - sum(h[:11]) / total
    strong = sum(h[200:]) / total
    parts = []
    if not is_bottom:
        parts.append("This is NOT the bottom layer, so do not call it Background.")
    if covered > 0.05 and strong < 0.02:
        parts.append("The layer is only faintly visible all over: it is a semi-transparent overlay such as a light, glow, haze, shadow or colour tint, so name it for that (for example Light or Glow).")
    elif covered < 0.6 and strong > 0.02:
        parts.append(f"The layer covers only about {round(covered * 100)}% of the picture: it is an object or group of objects, not a backdrop.")
    if taken:
        parts.append("These names are already used by other layers: " + ", ".join(taken) + ". Name what is on THIS layer, and give it a different, more specific name (never repeat one of them).")
    parts.append("Name only what you see on the FIRST picture, not the biggest thing in the second one.")
    return " ".join(parts)


def ask_model(layer_img, whole_img, hint=""):
    body = {"model": MODEL, "stream": False, "think": False, "format": SCHEMA, "keep_alive": "5m",
            "messages": [{"role": "user", "content": PROMPT + (" " + hint if hint else ""), "images": [_b64(layer_img), _b64(whole_img)]}],
            "options": {"temperature": 0, "num_predict": 60}}
    last = None
    for attempt in range(CALL_ATTEMPTS):
        try:
            req = urllib.request.Request(OLLAMA_CHAT_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
                out = json.loads(r.read() or b"{}")
            ans = json.loads(out.get("message", {}).get("content", ""))
            if isinstance(ans, dict) and str(ans.get("name", "")).strip():
                return str(ans["name"])
            last = "no name in the answer"
        except (urllib.error.URLError, ConnectionError, ValueError, OSError) as e:
            last = str(e)[:80]
        time.sleep(2)
    raise NameError_(last or "no answer")


ASK = ask_model


def name_layers(paths, ask=None):
    """-> (names, problems). Bottom first. A (nearly) opaque bottom layer is 'Background' and a layer with nothing visible is 'Empty layer', both without a model call. The model names every
    other layer on its own; when two layers get the same name the one with more visible content keeps it and the other is asked again, told which names are taken."""
    ask = ask or ASK
    whole = _composite(paths)
    raw, problems, strong, todo = [], [], [], []
    for i, p in enumerate(paths, 1):
        a = Image.open(p).convert("RGBA").getchannel("A")
        h = a.histogram()
        total = sum(h) or 1
        strong.append(sum(h[200:]) / total)
        if i == 1 and strong[-1] >= 0.995:
            raw.append("Background")
        elif 1 - sum(h[:11]) / total < 0.001:
            raw.append("Empty layer")
            problems.append(f"layer {i}: nothing is visible on it")
        else:
            raw.append(None)
            todo.append(i - 1)

    def ask_one(k, taken):
        try:
            return clean_name(ask(_on_grey(paths[k]), whole, hint_for(paths[k], k == 0, taken)), f"Layer {k + 1}")
        except NameError_ as e:
            problems.append(f"layer {k + 1}: the model gave no name ({e})")
            return f"Layer {k + 1}"

    for k in todo:
        raw[k] = ask_one(k, ())
    groups = {}
    for k in todo:
        groups.setdefault(raw[k].lower(), []).append(k)
    fixed = {raw[k].lower() for k in range(len(raw)) if k not in todo}
    for key, ks in groups.items():
        if len(ks) < 2 and key not in fixed:
            continue
        keep = None if key in fixed else max(ks, key=lambda k: strong[k])
        for k in ks:
            if k != keep:
                raw[k] = ask_one(k, tuple(x for j, x in enumerate(raw) if x and j != k))
    return unique(raw), problems


def _check(p):
    for x in p["layers"]:
        if os.path.splitext(x)[1].lower() not in IMAGE_EXT:
            raise R.Refused("The layer pictures must be .png or .webp files (transparent PNG layers from generate.split_layers).")
    if len(p["layers"]) < 2:
        raise R.Refused("Give at least two layer pictures to name.")


def _outputs(p):
    _check(p)
    try:
        label = design_naming.slug(p["name"], "name")
    except design_naming.BadName as e:
        raise R.Refused(str(e))
    return [f"{label}_LayerNames_v01.json"]


def _steps(p):
    return [f"Copy the {len(p['layers'])} layer pictures into the project's 02_Assets folder (the originals are never opened)",
            "Show David's local vision model each layer on a grey background, together with the whole picture for context, and ask for a one-to-three word designer's name (a fully opaque bottom layer is called Background without asking)",
            "Clean the names to the rules of a layer name and make them unique; a layer the model cannot name keeps 'Layer N'",
            "Save a small JSON file in 05_Exports with the names, bottom first, and the text to paste into the 'names' box of an assemble_layers operation; nothing else is changed"]


def _run(ctx):
    paths = ctx["inputs"]["layers"]
    out = os.path.join(ctx["staging"], ctx["outputs_expected"][0])
    names, problems = name_layers(paths)
    data = {"schema": "david_layer_names/v1", "model": MODEL, "order": "bottom first", "files": [os.path.basename(x) for x in paths],
            "names": names, "names_param": ", ".join(names)}
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    notes = ["names (bottom first): " + ", ".join(names) + "  -> paste into 'names' of an assemble_layers operation"]
    notes += ["WARNING: " + x for x in problems]
    return {"outputs": {ctx["outputs_expected"][0]: out}, "notes": notes}


NAME_LAYERS = {"id": "generate.name_layers", "app": "none", "title": "Name the layers of a split picture with David's vision model (local)",
               "params": {"layers": {"type": "files", "max": 12}, "name": {"type": "str"}}, "plan": _steps, "outputs": _outputs, "run": _run}


def register():
    if NAME_LAYERS["id"] not in R.OPERATIONS:
        R.register(NAME_LAYERS)
