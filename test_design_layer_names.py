"""
test_design_layer_names.py -- tests for design_layer_names.py (generate.name_layers) with a FAKE vision model: the cleaning and uniqueness rules, the Background shortcut, fallbacks, the pictures
sent to the model, planning, delivery and the real model call's request shape (the HTTP call is faked). No model is loaded.
Run: python test_design_layer_names.py
"""
from david_config import DAVID_HOME
import sys, os, json, base64, io, shutil, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


from PIL import Image, ImageDraw
import design_layer_names as N
import design_illustrator as AI
import design_ops as O
import design_runner as R

TMP = tempfile.mkdtemp(prefix="dln_test_")
ROOT = os.path.join(TMP, "design")
SRC = os.path.join(TMP, "originals")
os.makedirs(SRC)
O.register_builtin()
PID = R.create_project("Acme", "Spring", "2026-10-12", root=ROOT)

W = 128
bg = Image.new("RGBA", (W, W), (20, 80, 60, 255))
wrap = Image.new("RGBA", (W, W), (0, 0, 0, 0))
ImageDraw.Draw(wrap).ellipse([20, 60, 110, 110], fill=(230, 200, 140, 255))
herb = Image.new("RGBA", (W, W), (0, 0, 0, 0))
ImageDraw.Draw(herb).ellipse([5, 5, 40, 40], fill=(60, 160, 60, 255))
glow = Image.new("RGBA", (W, W), (255, 220, 120, 60))
PATHS = []
for i, (nm, im) in enumerate((("1_background", bg), ("2_wrap", wrap), ("3_herb", herb), ("4_glow", glow)), 1):
    p = os.path.join(SRC, f"HeroSplit_Layer{i:02d}_v01.png")
    im.save(p)
    PATHS.append(p)
SHA = {p: R.sha256_file(p) for p in PATHS}

print("-- cleaning and uniqueness --")
check("names are cleaned: forbidden characters become spaces, whitespace collapses, the first letter is a capital, 40 characters at most, an empty answer falls back",
      N.clean_name('  wrap/plate: "x" ', "Layer 2") == "Wrap plate x" and N.clean_name("", "Layer 3") == "Layer 3" and N.clean_name(None, "Layer 3") == "Layer 3" and len(N.clean_name("a" * 80, "L")) == 40 and N.clean_name("herbs.", "L") == "Herbs")
check("equal names (case does not matter) are made unique with a number: Light, Light 2, light -> Light 3", N.unique(["Light", "Light", "light"]) == ["Light", "Light 2", "light 3"], N.unique(["Light", "Light", "light"]))
check("every cleaned name passes the layer-name rules of the assemble operations", all(AI.layer_names({"names": ", ".join(N.unique([N.clean_name(x, "L") for x in ('a/b', 'Wrap', 'wrap', 'x' * 99)]))}, 4)))

print("\n-- naming with a fake model --")
SEEN = []
HINTS = []


def fake_ask(layer_img, whole_img, hint=""):
    HINTS.append(hint)
    SEEN.append((layer_img.size, whole_img.size, layer_img.mode, whole_img.mode, layer_img.getpixel((0, 0))))
    return {2: "wrap", 3: "herbs", 4: "Warm light"}[len(SEEN) + 1]


names, problems = N.name_layers(PATHS, ask=fake_ask)
check("a fully opaque bottom layer is called Background WITHOUT asking the model; the others get the model's cleaned names, bottom first", names == ["Background", "Wrap", "Herbs", "Warm light"] and len(SEEN) == 3 and not problems, (names, len(SEEN)))
check("the model is shown each layer on a mid-grey background (so empty areas are grey, not black) and the whole picture for context, both RGB and no larger than 512 px",
      all(s[2] == "RGB" and s[3] == "RGB" and max(s[0]) <= 512 and max(s[1]) <= 512 for s in SEEN) and SEEN[0][4] == (128, 128, 128), SEEN[0])


def flaky(layer_img, whole_img, hint=""):
    raise N.NameError_("model busy")


names, problems = N.name_layers(PATHS, ask=flaky)
check("when the model cannot answer, those layers keep their default names and every failure is reported; the Background shortcut still works", names == ["Background", "Layer 2", "Layer 3", "Layer 4"] and len(problems) == 3 and "layer 2" in problems[0], (names, problems))
names, _ = N.name_layers(PATHS, ask=lambda a, b, h="": "Wrap")
check("each layer gets a hint from its transparency: not the bottom layer -> never Background; the faint glow layer is flagged as a semi-transparent overlay (light/glow/haze); the cut-out is flagged as an object covering only part of the picture",
      all("do not call it Background" in h for h in HINTS) and "semi-transparent overlay" in HINTS[2] and "semi-transparent" not in HINTS[0] and "object or group of objects" in HINTS[0], HINTS)
check("a model that answers the same name for several layers still gives unique names", names == ["Background", "Wrap", "Wrap 2", "Wrap 3"], names)

print("\n-- real-world layers: nearly opaque bottom, empty layer, taken names --")
near = Image.new("RGBA", (W, W), (20, 80, 60, 254))
near_p = os.path.join(SRC, "near_bottom.png")
near.save(near_p)
blank_p = os.path.join(SRC, "blank.png")
Image.new("RGBA", (W, W), (0, 0, 0, 0)).save(blank_p)
faint_p = os.path.join(SRC, "faint.png")
Image.new("RGBA", (W, W), (255, 255, 255, 5)).save(faint_p)
SEEN2 = []


def ask2(layer_img, whole_img, hint=""):
    SEEN2.append(hint)
    return "Wrap" if len(SEEN2) == 1 else "Herbs"


names, problems = N.name_layers([near_p, PATHS[1], blank_p, faint_p, PATHS[2]], ask=ask2)
check("a bottom layer that is opaque except for a pixel or two (alpha 254) is still the Background, without asking the model", names[0] == "Background" and len(SEEN2) >= 1, names)
check("a layer with nothing visible on it (fully transparent, or faint alpha 5) is called 'Empty layer' without asking the model, and is reported", names[2] == "Empty layer" and names[3] == "Empty layer 2" and sum("nothing is visible" in p for p in problems) == 2, (names, problems))
SEEN3, ANS3 = [], iter(["Wrap", "Wrap", "Wrap", "Herbs", "Light"])


def ask3(layer_img, whole_img, hint=""):
    SEEN3.append(hint)
    return next(ANS3)


names3, _ = N.name_layers(PATHS, ask=ask3)
check("when the model gives the same name to several layers, the layer with the most visible content keeps it (the wrap, not the small herb or the faint glow) and each other layer is asked again, told which names are taken and to give a more specific name",
      names3 == ["Background", "Wrap", "Herbs", "Light"] and len(SEEN3) == 5 and "already used" not in "".join(SEEN3[:3]) and all("already used by other layers" in h and "different, more specific name" in h for h in SEEN3[3:]) and "Wrap" in SEEN3[3], (names3, [h[-90:] for h in SEEN3]))
check("every hint asks the model to name only what is on the first picture", all("only what you see on the FIRST picture" in h for h in SEEN2 + SEEN3))

print("\n-- planning and delivery --")
base = {"layers": PATHS, "name": "Hero Names"}
pl = lambda **prm: _ref(R.plan_job, PID, "generate.name_layers", {**base, **prm}, root=ROOT)


def _ref(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except R.Refused as e:
        return str(e)


psd = os.path.join(SRC, "x.psd")
open(psd, "w").write("x")
check("refusals: a PSD as a layer picture, a single layer, more than 12", "png or .webp" in (pl(layers=[psd, PATHS[0]]) or "") and "at least two" in (pl(layers=[PATHS[0]]) or "") and pl(layers=PATHS * 4))
pn = R.plan_job(PID, "generate.name_layers", base, root=ROOT)
check("output name follows the standard: HeroNames_LayerNames_v01.json; every layer is a hashed input in order", pn["outputs"] == ["HeroNames_LayerNames_v01.json"] and len(pn["inputs"]) == 4 and pn["app"] == "none")
steps = " | ".join(pn["steps"])
check("the plan says the originals are never opened, what the model is shown, that Background needs no model, and that the result is a small JSON file with text to paste", "never opened" in steps and "grey background" in steps and "Background without asking" in steps and "paste" in steps, steps[:200])
N.ASK = fake_ask
SEEN.clear()
pid_job = R.plan_job(PID, "generate.name_layers", base, root=ROOT)
for it in R.plan_items(pid_job):
    R.review_item(PID, pid_job["job_id"], it, "approved", "", "Demo User", ROOT)
R.approve(PID, pid_job["job_id"], "Demo User", pid_job["plan_sha256"], ROOT, True)
res = R.run_job(PID, pid_job["job_id"], ROOT)
out = os.path.join(ROOT, PID, "05_Exports", "HeroNames_LayerNames_v01.json")
data = json.load(open(out, encoding="utf-8")) if res["ok"] else {}
check("delivered: a JSON with the names bottom first and the ready-to-paste text; the note shows it; the original layer pictures are unchanged",
      res["ok"] and data.get("names") == ["Background", "Wrap", "Herbs", "Warm light"] and data.get("names_param") == "Background, Wrap, Herbs, Warm light" and any("paste into 'names'" in n for n in res["notes"])
      and all(R.sha256_file(p) == SHA[p] for p in PATHS), str(res)[:300])
check("the pasted text is accepted by the assemble operations as layer names", AI.layer_names({"names": data.get("names_param", "")}, 4) == ["Background", "Wrap", "Herbs", "Warm light"])

print("\n-- the real model call (HTTP faked) --")
sent = {}


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


real_urlopen = N.urllib.request.urlopen


def fake_urlopen(req, timeout=None):
    sent["body"] = json.loads(req.data)
    sent["url"] = req.full_url
    return FakeResp({"message": {"content": json.dumps({"name": "Tomato"})}})


N.urllib.request.urlopen = fake_urlopen
nm = N.ask_model(Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
b = sent["body"]
check("the request goes to the local Ollama chat endpoint with the vision model, thinking off, a JSON schema, temperature 0, two images (the layer first) and the naming prompt",
      nm == "Tomato" and sent["url"].startswith("http://127.0.0.1:") and b["model"] == "qwen3.5:9b" and b["think"] is False and b["format"]["required"] == ["name"] and b["options"]["temperature"] == 0 and len(b["messages"][0]["images"]) == 2
      and "FIRST picture" in b["messages"][0]["content"], b["model"])
N.urllib.request.urlopen = lambda req, timeout=None: FakeResp({"message": {"content": "not json"}})
N.time.sleep = lambda s: None
try:
    N.ask_model(Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
    err = None
except N.NameError_ as e:
    err = str(e)
check("an answer that is not valid JSON is a NameError_ after the retries (never a crash)", err is not None, err)
N.urllib.request.urlopen = real_urlopen
src_text = open(str(DAVID_HOME / "design_layer_names.py"), encoding="utf-8").read()
import ollama_config as _oc
check("nothing in the module deletes or moves a file, and it only talks to the configured Ollama (no hard-coded address, audit P3-2)",
      "os.remove(" not in src_text and "shutil.move" not in src_text and src_text.count("http://") == 0
      and N.OLLAMA_CHAT_URL == _oc.OLLAMA_HOST + "/api/chat")
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
