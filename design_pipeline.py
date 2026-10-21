"""design_pipeline.py -- the end-to-end runner of the Design portal: one request drives the whole layered-art chain.

  layered_art   a text prompt -> generate.image (no text in it) -> generate.split_layers (one transparent layer per component) -> generate.name_layers (David's vision model names them)
                -> assemble_layers in Photoshop / Illustrator / InDesign (one named, unlocked layer each + the live Arabic text) -> optionally aftereffects.animate_layers (each layer moves, MP4)

It adds NO new power: every stage is one of the fixed operations, run by the safe runner on copies, delivering into the project's folders, undoable. What it adds is the chaining:
  1. plan_pipeline   checks that the models/apps are ready and writes ONE plan that lists every stage and its settings, with a fingerprint;
  2. approve_pipeline  only the designer, by name and that fingerprint, can give the yes (nobody and nothing else can approve it);
  3. run_pipeline    for each stage makes the operation's own plan from the previous stage's files, checks it is the operation and settings the designer approved, records the designer's yes
                     for it ("approved as part of pipeline pl-0001"), runs it, and STOPS at the first problem (what was already delivered stays; undo_pipeline removes it);
  4. undo_pipeline   undoes the finished stages, newest first.
A stage that failed can be run again with resume=True: finished stages are kept.
"""
from david_config import COMFYUI_DIR
import os
import re
import json
import hashlib
import datetime

import design_generate
import design_runner as R

KIND = "layered_art"
APPS_ALL = ("photoshop", "illustrator", "indesign")
MOTIONS = ("none", "parallax", "zoom_in", "fade_in")
MODELS = {"generate.image": [(str(COMFYUI_DIR / "models" / "diffusion_models" / "qwen_image_fp8_e4m3fn.safetensors"), "the Qwen-Image model")],
          "generate.split_layers": [(str(COMFYUI_DIR / "models" / "diffusion_models" / "qwen_image_layered_fp8mixed.safetensors"), "the layer-splitting model (Qwen-Image-Layered)"),
                                    (str(COMFYUI_DIR / "models" / "vae" / "qwen_image_layered_vae.safetensors"), "the layer-splitting VAE")]}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _canon(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _dir(project_id, root=R.ROOT):
    return os.path.join(R.project_dir(project_id, root), "_pipeline")


def _pdir(project_id, pid, root=R.ROOT):
    if not re.fullmatch(r"pl-\d{4}", str(pid)):
        raise R.Refused("That is not a pipeline id.")
    return os.path.join(_dir(project_id, root), pid)


def _next_id(project_id, root):
    d = _dir(project_id, root)
    os.makedirs(d, exist_ok=True)
    n = [int(x[3:]) for x in os.listdir(d) if re.fullmatch(r"pl-\d{4}", x)]
    return f"pl-{(max(n) + 1) if n else 1:04d}"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


# ---- the parameters of the chain ----

PARAMS = {"prompt": {"type": "str", "max": 1500}, "name": {"type": "str"}, "preset": {"type": "str"},
          "layers": {"type": "int", "min": 2, "max": 8, "required": False, "default": 4},
          "quality": {"type": "enum", "values": ["draft", "standard"], "required": False, "default": "draft"},
          "shape": {"type": "enum", "values": ["square", "portrait", "landscape"], "required": False, "default": "square"},
          "seed": {"type": "int", "min": 0, "max": 4294967295, "required": False},
          "text": {"type": "str", "required": False}, "font": {"type": "str", "required": False},
          "size": {"type": "int", "min": 8, "max": 600, "required": False, "default": 120}, "color": {"type": "str", "required": False, "default": "#FFFFFF"},
          "position": {"type": "enum", "values": ["top", "center", "bottom"], "required": False, "default": "top"},
          "apps": {"type": "str", "required": False, "default": "photoshop"},
          "motion": {"type": "enum", "values": list(MOTIONS), "required": False, "default": "none"},
          "seconds": {"type": "int", "min": 2, "max": 20, "required": False, "default": 5}, "fps": {"type": "enum", "values": ["24", "25", "30"], "required": False, "default": "30"}}


def clean_params(params):
    clean = R._check_params({"id": KIND, "params": PARAMS}, params)
    apps = [a.strip().lower() for a in clean["apps"].split(",") if a.strip()]
    if not apps or any(a not in APPS_ALL for a in apps) or len(set(apps)) != len(apps):
        raise R.Refused("'apps' must name one or more of photoshop, illustrator, indesign, separated by commas.")
    if clean["motion"] != "none" and "photoshop" not in apps:
        apps.insert(0, "photoshop")
    clean["apps"] = [a for a in APPS_ALL if a in apps]
    return clean


def stages(p):
    """The stages in order: [{id, op, title}] (the settings of each are derived from p and the earlier stages' files)."""
    out = [{"id": "generate", "op": "generate.image"}, {"id": "split", "op": "generate.split_layers"}, {"id": "name", "op": "generate.name_layers"}]
    out += [{"id": "assemble_" + a, "op": f"{a}.assemble_layers"} for a in p["apps"]]
    if p["motion"] != "none":
        out.append({"id": "animate", "op": "aftereffects.animate_layers"})
    for s in out:
        s["title"] = R.OPERATIONS[s["op"]]["title"]
    return out


def preflight(p):
    """Sentences about what is not ready (empty list = ready)."""
    import design_indesign as ID
    problems = []
    for op in {s["op"] for s in stages(p)}:
        for path, what in MODELS.get(op, []):
            if not os.path.isfile(path):
                problems.append(f"{what} is not on this computer yet ({os.path.basename(path)}).")
    try:
        design_generate._check({"prompt": p["prompt"]})
    except R.Refused as e:
        problems.append(str(e))
    try:
        ID._find_preset(p["preset"])
    except R.Refused as e:
        problems.append(str(e))
    if p["motion"] != "none":
        import design_aftereffects as AE
        if AE.running("aftereffects"):
            problems.append("After Effects is open. Close it first: the animation runs in a fresh session.")
    return problems


# ---- plan / approve ----

def plan_pipeline(project_id, params, requested_by="designer", root=R.ROOT):
    R.project_dir(project_id, root)
    p = clean_params(params)
    problems = preflight(p)
    if problems:
        raise R.Refused("The chain cannot start yet: " + " ".join(problems))
    sts = stages(p)
    pid = _next_id(project_id, root)
    plan = {"schema": "design_pipeline_plan/v1", "pipeline_id": pid, "project_id": project_id, "kind": KIND, "params": p, "stages": sts, "requested_by": str(requested_by)[:30], "created": _now()}
    plan["plan_sha256"] = hashlib.sha256(_canon(plan).encode("utf-8")).hexdigest()
    d = _pdir(project_id, pid, root)
    os.makedirs(d)
    _write(os.path.join(d, "plan.json"), plan)
    _write(os.path.join(d, "state.json"), {"state": "planned", "updated": _now(), "stages": {}})
    open(os.path.join(d, "plan.md"), "w", encoding="utf-8").write(plan_markdown(plan))
    return plan


def plan_markdown(plan):
    p = plan["params"]
    L = [f"# Pipeline {plan['pipeline_id']}: a layered picture from a description", "", f"Project: {plan['project_id']}  |  Asked by: {plan['requested_by']}", "",
         f"Description: {p['prompt']}", f"Picture: {p['shape']}, {p['quality']} quality; {p['layers']} layers; document preset {p['preset']}; name {p['name']}",
         f"Text on top: {p['text']!r} ({p['position']}, {p['size']} pt, {p['color']})" if p.get("text") else "Text on top: none",
         f"Assembled in: {', '.join(p['apps'])}" + (f"; then animated in After Effects: {p['motion']}, {p['seconds']} s at {p['fps']} fps" if p["motion"] != "none" else "; no animation"), "",
         "## The stages (each is one of the fixed operations, run on copies and delivered into this project)", ""]
    for i, s in enumerate(plan["stages"], 1):
        L.append(f"{i}. {s['title']}  [{s['op']}]")
    L += ["", "Each stage makes its own plan from the files the stage before delivered, and runs only if that plan is the operation and the settings above. The chain STOPS at the first problem;",
          "what was already delivered stays and can be undone. A layer with nothing visible on it is left out of the layered files. Nothing is sent anywhere. The picture itself never contains text: the words are set in the Adobe apps with a real font.", "",
          f"Fingerprint: {plan['plan_sha256']}"]
    return "\n".join(L)


def load_plan(project_id, pid, root=R.ROOT):
    plan = _read(os.path.join(_pdir(project_id, pid, root), "plan.json"))
    p = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if hashlib.sha256(_canon(p).encode("utf-8")).hexdigest() != plan["plan_sha256"]:
        raise R.Refused("The saved pipeline plan was edited after it was made; make a new plan.")
    return plan


def state(project_id, pid, root=R.ROOT):
    return _read(os.path.join(_pdir(project_id, pid, root), "state.json"))


def _set(project_id, pid, root, st, **extra):
    d = _pdir(project_id, pid, root)
    s = _read(os.path.join(d, "state.json"))
    s.update(extra)
    s["state"], s["updated"] = st, _now()
    _write(os.path.join(d, "state.json"), s)
    return s


def approve_pipeline(project_id, pid, approved_by, plan_sha256, root=R.ROOT):
    who = R._who(approved_by)
    plan = load_plan(project_id, pid, root)
    if state(project_id, pid, root)["state"] != "planned":
        raise R.Refused(f"Only a planned pipeline can be approved; this one is {state(project_id, pid, root)['state']}.")
    if plan_sha256 != plan["plan_sha256"]:
        raise R.Refused("The fingerprint does not match the plan; approve the plan exactly as shown.")
    _write(os.path.join(_pdir(project_id, pid, root), "approval.json"), {"approved_by": who, "plan_sha256": plan_sha256, "at": _now()})
    _set(project_id, pid, root, "approved", approved_by=who)
    return plan


# ---- running ----

def _abs(project_id, rel, root):
    return os.path.join(R.project_dir(project_id, root), *rel.split("/"))


def _delivered(res, project_id, root, pattern):
    rx = re.compile(pattern, re.I)
    return [_abs(project_id, rel, root) for rel in res.get("delivered", []) if rx.search(rel.split("/")[-1])]


def stage_params(sid, p, ctx, root):
    """The settings of one stage, from the chain's settings and the files of the stages before it."""
    q = p["quality"]
    if sid == "generate":
        d = {"prompt": p["prompt"], "name": p["name"], "shape": p["shape"], "size": q}
        if p.get("seed") is not None:
            d["seed"] = p["seed"]
        return d
    if sid == "split":
        return {"picture": ctx["picture"], "name": p["name"], "layers": p["layers"], "size": q}
    if sid == "name":
        return {"layers": ctx["layer_files"], "name": p["name"]}
    if sid.startswith("assemble_"):
        d = {"layers": ctx["layer_files"], "names": ctx["names_param"], "preset": p["preset"], "name": p["name"], "size": p["size"], "color": p["color"], "position": p["position"]}
        if p.get("text"):
            d["text"] = p["text"]
        if p.get("font"):
            d["font"] = p["font"]
        return d
    if sid == "animate":
        return {"source": ctx["psd"], "motion": p["motion"], "seconds": p["seconds"], "fps": p["fps"]}
    raise R.Refused(f"Unknown stage {sid}.")


def _collect(sid, res, ctx, project_id, root):
    if sid == "generate":
        ctx["picture"] = (_delivered(res, project_id, root, r"_GenImage_v\d\d\.png$") or [None])[0]
    elif sid == "split":
        files = sorted(_delivered(res, project_id, root, r"_Layer\d\d_v\d\d\.png$"))
        man = (_delivered(res, project_id, root, r"_Layers_v\d\d\.json$") or [None])[0]
        empty = {x["file"] for x in _read(man)["layers"] if x.get("empty")} if man else set()
        kept = [x for x in files if os.path.basename(x) not in empty]
        if len(kept) < 2:
            raise R.Refused(f"only {len(kept)} of the {len(files)} layers have anything on them; a layered file needs at least two")
        ctx["layer_files"] = kept
        ctx["left_out"] = sorted(empty)
    elif sid == "name":
        f = (_delivered(res, project_id, root, r"_LayerNames_v\d\d\.json$") or [None])[0]
        ctx["names_param"] = _read(f)["names_param"] if f else ""
    elif sid == "assemble_photoshop":
        ctx["psd"] = (_delivered(res, project_id, root, r"\.psd$") or [None])[0]
    return ctx


def _approve_stage(project_id, job_id, who, pid, root):
    plan = R.load_plan(project_id, job_id, root)
    for it in R.plan_items(plan):
        R.review_item(project_id, job_id, it, "approved", f"approved as part of pipeline {pid}", who, root)
    R.approve(project_id, job_id, who, plan["plan_sha256"], root, True)
    return plan


def run_pipeline(project_id, pid, root=R.ROOT, resume=False):
    plan = load_plan(project_id, pid, root)
    st = state(project_id, pid, root)
    if st["state"] not in ("approved", "failed") or (st["state"] == "failed" and not resume):
        raise R.Refused(f"Only an approved pipeline can run (this one is {st['state']}" + ("; run it again with resume to continue a failed one" if st["state"] == "failed" else "") + ").")
    ap = _read(os.path.join(_pdir(project_id, pid, root), "approval.json"))
    if ap["plan_sha256"] != plan["plan_sha256"]:
        raise R.Refused("The approval is not for this plan.")
    who = R._who(ap["approved_by"])
    p = plan["params"]
    problems = preflight(p)
    if problems:
        raise R.Refused("The chain cannot continue yet: " + " ".join(problems))
    _set(project_id, pid, root, "running")
    ctx = st.get("ctx", {})
    done = st.get("stages", {})
    for s in plan["stages"]:
        sid = s["id"]
        if done.get(sid, {}).get("ok"):
            continue
        try:
            params = stage_params(sid, p, ctx, root)
            if any(v in (None, "", []) for k, v in params.items() if k in ("picture", "layers", "source", "names")):
                raise R.Refused("the files the previous stage should have delivered are missing")
            pl = R.plan_job(project_id, s["op"], params, requested_by=who, root=root)
            if pl["op"] != s["op"]:
                raise R.Refused("the stage plan is not the operation the designer approved")
            _approve_stage(project_id, pl["job_id"], who, pid, root)
            res = R.run_job(project_id, pl["job_id"], root)
        except R.Refused as e:
            done[sid] = {"ok": False, "error": str(e)}
            _set(project_id, pid, root, "failed", stages=done, ctx=ctx, failed_stage=sid, error=f"Stage '{sid}' could not start: {e}")
            return state(project_id, pid, root)
        done[sid] = {"ok": bool(res.get("ok")), "job": pl["job_id"], "delivered": res.get("delivered", []), "notes": res.get("notes", []), "error": res.get("error")}
        if not res.get("ok"):
            _set(project_id, pid, root, "failed", stages=done, ctx=ctx, failed_stage=sid, error=f"Stage '{sid}' failed: {res.get('error')}")
            return state(project_id, pid, root)
        try:
            ctx = _collect(sid, res, ctx, project_id, root)
        except R.Refused as e:
            done[sid]["ok"] = False
            done[sid]["error"] = str(e)
            _set(project_id, pid, root, "failed", stages=done, ctx=ctx, failed_stage=sid, error=f"Stage '{sid}' finished but its result cannot be used: {e}")
            return state(project_id, pid, root)
        _set(project_id, pid, root, "running", stages=done, ctx=ctx)
    summary = summary_markdown(plan, done, project_id, root)
    open(os.path.join(_pdir(project_id, pid, root), "summary.md"), "w", encoding="utf-8").write(summary)
    return _set(project_id, pid, root, "done", stages=done, ctx=ctx, failed_stage=None, error=None)


def summary_markdown(plan, done, project_id, root):
    L = [f"# Pipeline {plan['pipeline_id']} finished", ""]
    for s in plan["stages"]:
        r = done.get(s["id"], {})
        L.append(f"## {s['title']}")
        L += [f"- {x}" for x in r.get("delivered", [])] + [f"- note: {n}" for n in r.get("notes", [])] + [""]
    return "\n".join(L)


def undo_pipeline(project_id, pid, root=R.ROOT):
    st = state(project_id, pid, root)
    removed = []
    for s in reversed(load_plan(project_id, pid, root)["stages"]):
        r = st.get("stages", {}).get(s["id"], {})
        if r.get("ok") and r.get("job") and R.status(project_id, r["job"], root)["state"] == "done":
            removed += R.undo_job(project_id, r["job"], root)
    _set(project_id, pid, root, "undone", removed=removed)
    return removed


def list_pipelines(project_id, root=R.ROOT):
    d = _dir(project_id, root)
    if not os.path.isdir(d):
        return []
    out = []
    for x in sorted(os.listdir(d)):
        if re.fullmatch(r"pl-\d{4}", x):
            plan = _read(os.path.join(d, x, "plan.json"))
            st = _read(os.path.join(d, x, "state.json"))
            out.append({"pipeline": x, "state": st["state"], "title": plan["params"]["name"], "stages": len(plan["stages"]), "error": st.get("error"), "plan_sha256": plan["plan_sha256"]})
    return out
