"""design_runner.py -- the safe runner of the Design portal (Piece 1). Offline; it never talks to an Adobe app itself.

Every piece of design work is a JOB made of one fixed, tested OPERATION (registered in code; a model never writes a script). A job goes
    planned  ->  approved (the designer's name + the exact plan he saw)  ->  running  ->  done | failed       (or cancelled / undone)
and nothing runs without the approval. The plan copies nothing and touches nothing; only run_job does, and only for an approved plan.

Safety rules enforced here:
  * originals are never given to an operation: it only sees COPIES inside the project folder; the originals' SHA-256 is checked before the copy,
    after the copy (copy == original) and after the run (unchanged), and a changed original fails the job with nothing delivered;
  * outputs are made in a staging folder and moved into 05_Exports / 04_Previews only after every output exists and every name follows the
    naming standard; if anything fails, nothing is delivered and no half-written file is left;
  * 06_Delivery is never written here (the designer moves a finished file there himself);
  * undo = delete the files the job delivered; delete_project removes the whole project folder (only inside the design root, only with the id typed).
Only the Design page's approve button is meant to call approve(): no tool or agent in David imports this module.
"""
from david_config import DAVID_DATA
import os
import re
import json
import time
import shutil
import hashlib
import datetime

import design_naming

ROOT = str(DAVID_DATA / "generated" / "design")
APPS = ("photoshop", "illustrator", "indesign", "premiere", "aftereffects", "audition", "comfyui", "none")
NOT_A_PERSON = {"david", "ai", "model", "assistant", "system", "claude", "qwen", "bot", "agent", "llm", "auto", "automatic"}
OPERATIONS = {}


class Refused(Exception):
    pass


# ---- operations (fixed, registered in code) ----

def register(op):
    """op = {id, app, title, params: {name: spec}, plan(params) -> [sentences], outputs(params) -> [file names], run(ctx) -> {outputs, preview, notes}}"""
    for k in ("id", "app", "title", "params", "plan", "outputs", "run"):
        if k not in op:
            raise ValueError(f"operation is missing '{k}'")
    if op["app"] not in APPS:
        raise ValueError(f"unknown app '{op['app']}'")
    if not re.fullmatch(r"[a-z0-9_.\-]{3,60}", op["id"]):
        raise ValueError("operation id must be lower-case letters, digits, '_', '-' or '.'")
    if op["id"] in OPERATIONS:
        raise ValueError(f"operation '{op['id']}' is already registered")
    OPERATIONS[op["id"]] = op


def unregister(op_id):
    OPERATIONS.pop(op_id, None)


def _check_params(op, params):
    if not isinstance(params, dict):
        raise Refused("The parameters must be a set of named values.")
    unknown = sorted(set(params) - set(op["params"]))
    if unknown:
        raise Refused(f"The operation '{op['id']}' has no parameter {', '.join(unknown)}.")
    clean = {}
    for name, spec in op["params"].items():
        if name not in params or params[name] is None:
            if spec.get("required", True):
                raise Refused(f"The parameter '{name}' is required.")
            if "default" in spec:
                clean[name] = spec["default"]
            continue
        v, t = params[name], spec["type"]
        if t == "file":
            if not (isinstance(v, str) and os.path.isabs(v) and os.path.isfile(v)):
                raise Refused(f"'{name}' must be the full path of a file that exists.")
            clean[name] = os.path.normpath(v)
        elif t == "files":
            top = spec.get("max", 12)
            if not (isinstance(v, (list, tuple)) and 1 <= len(v) <= top and all(isinstance(x, str) and os.path.isabs(x) and os.path.isfile(x) for x in v)):
                raise Refused(f"'{name}' must be a list of 1 to {top} files, each given with its full path and each existing.")
            clean[name] = [os.path.normpath(x) for x in v]
        elif t == "str":
            top = spec.get("max", 500)
            if not (isinstance(v, str) and 0 < len(v.strip()) <= top):
                raise Refused(f"'{name}' must be text of 1 to {top} characters.")
            clean[name] = v.strip()
        elif t in ("int", "float"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or (t == "int" and v != int(v)):
                raise Refused(f"'{name}' must be a {'whole ' if t == 'int' else ''}number.")
            if not spec.get("min", float("-inf")) <= v <= spec.get("max", float("inf")):
                raise Refused(f"'{name}' must be between {spec.get('min')} and {spec.get('max')}.")
            clean[name] = int(v) if t == "int" else float(v)
        elif t == "bool":
            if not isinstance(v, bool):
                raise Refused(f"'{name}' must be true or false.")
            clean[name] = v
        elif t == "enum":
            if v not in spec["values"]:
                raise Refused(f"'{name}' must be one of {', '.join(map(str, spec['values']))}.")
            clean[name] = v
        else:
            raise ValueError(f"unknown parameter type {t}")
    return clean


# ---- small helpers ----

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _who(approved_by):
    n = str(approved_by or "").strip()
    if not n or len(n) > 60 or n.lower() in NOT_A_PERSON:
        raise Refused("Only the designer can approve a job: approved_by needs his name.")
    return n


def project_dir(project_id, root=ROOT):
    if not (isinstance(project_id, str) and design_naming.SAFE.match(project_id)) or len(project_id) > 120:
        raise Refused("That is not a project id.")
    base = os.path.realpath(root)
    p = os.path.realpath(os.path.join(base, project_id))
    if os.path.dirname(p) != base:
        raise Refused("That project is outside the design folder.")
    return p


def _job_dir(project_id, job_id, root):
    if not re.fullmatch(r"job-\d{4}", str(job_id)):
        raise Refused("That is not a job id.")
    return os.path.join(project_dir(project_id, root), "_job", job_id)


def _log(project_id, root, job_id, event, **details):
    p = os.path.join(project_dir(project_id, root), "_job", "log.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now(), "job": job_id, "event": event, **details}, ensure_ascii=False) + "\n")


def _state(project_id, job_id, root):
    return _read_json(os.path.join(_job_dir(project_id, job_id, root), "state.json"))


def _set_state(project_id, job_id, root, state, **extra):
    d = _job_dir(project_id, job_id, root)
    s = _read_json(os.path.join(d, "state.json")) if os.path.exists(os.path.join(d, "state.json")) else {}
    s.update(extra)
    s["state"] = state
    s["updated"] = _now()
    _write_json(os.path.join(d, "state.json"), s)
    _log(project_id, root, job_id, state, **{k: v for k, v in extra.items() if k in ("error", "flags")})
    return s


# ---- projects ----

def create_project(client, project, date=None, root=ROOT, structure=None):
    pid = design_naming.project_id(client, project, date)
    p = project_dir(pid, root)
    if os.path.exists(p):
        raise Refused(f"The project {pid} already exists.")
    folders = structure or design_naming.FOLDERS
    bad = [f for f in folders if design_naming.check_name(f)]
    if bad:
        raise Refused(f"The folder names {bad} do not follow the naming rules.")
    for f in folders:
        os.makedirs(os.path.join(p, f))
    os.makedirs(os.path.join(p, "_job"))
    _write_json(os.path.join(p, "project.json"), {"schema": "design_project/v1", "id": pid, "client": client, "project": project, "created": _now(), "folders": list(folders)})
    _log(pid, root, None, "project created")
    return pid


def list_projects(root=ROOT):
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "project.json")))


def delete_project(project_id, confirm, root=ROOT):
    p = project_dir(project_id, root)
    if confirm != project_id:
        raise Refused("To delete a project, type its id exactly.")
    if not os.path.isfile(os.path.join(p, "project.json")):
        raise Refused("That folder is not a design project.")
    shutil.rmtree(p)
    return True


# ---- jobs ----

def _next_job_id(project_id, root):
    jd = os.path.join(project_dir(project_id, root), "_job")
    n = max([int(d[4:]) for d in os.listdir(jd) if re.fullmatch(r"job-\d{4}", d)] + [0]) + 1
    if n > 9999:
        raise Refused("This project has too many jobs; start a new project.")
    return f"job-{n:04d}"


def plan_markdown(plan):
    L = [f"# Plan {plan['job_id']}: {plan['title']}", "", f"Project: {plan['project_id']}  |  App: {plan['app']}  |  Asked by: {plan['requested_by']}", "", "## Steps"]
    L += [f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)]
    L += ["", "## Files it will copy (the originals are never changed)"]
    L += [f"- {i['path']}  (sha256 {i['sha256'][:12]}..., {i['size']} bytes) -> {i['copy']}" for i in plan["inputs"]] or ["- none"]
    L += ["", "## What it will make"] + [f"- {o}" for o in plan["outputs"]]
    L += ["", f"Plan fingerprint: {plan['plan_sha256']}", "", "Nothing has run. It runs only after the designer approves this exact plan."]
    return "\n".join(L)


def plan_job(project_id, op_id, params, requested_by="designer", root=ROOT):
    """A dry run: validates, fingerprints the files, writes the plan. Copies nothing and runs nothing."""
    p = project_dir(project_id, root)
    if not os.path.isfile(os.path.join(p, "project.json")):
        raise Refused("That project does not exist.")
    op = OPERATIONS.get(op_id)
    if op is None:
        raise Refused(f"'{op_id}' is not an operation David has. Operations are fixed and tested; free text is not accepted.")
    clean = _check_params(op, params)
    outputs = list(op["outputs"](clean))
    for o in outputs:
        probs = design_naming.check_file_name(o)
        if probs:
            raise Refused(f"The output name '{o}' breaks the naming standard: {probs[0]}")
    if len(set(outputs)) != len(outputs):
        raise Refused("The operation names two outputs the same.")
    inputs, used = [], set()
    for name, spec in op["params"].items():
        if spec["type"] in ("file", "files") and name in clean:
            for idx, src in enumerate([clean[name]] if spec["type"] == "file" else clean[name]):
                base = os.path.basename(src)
                copy_name = base
                k = 2
                while copy_name in used:
                    stem, ext = os.path.splitext(base)
                    copy_name, k = f"{stem}_c{k}{ext}", k + 1
                used.add(copy_name)
                entry = {"param": name, "path": src, "sha256": sha256_file(src), "size": os.path.getsize(src), "copy": f"02_Assets/{copy_name}"}
                if spec["type"] == "files":
                    entry["index"] = idx
                inputs.append(entry)
    job_id = _next_job_id(project_id, root)
    plan = {"schema": "design_plan/v1", "job_id": job_id, "project_id": project_id, "op": op_id, "app": op["app"], "title": op["title"], "params": clean,
            "inputs": inputs, "outputs": outputs, "steps": list(op["plan"](clean)), "requested_by": str(requested_by)[:30], "created": _now()}
    plan["plan_sha256"] = hashlib.sha256(_canon(plan).encode("utf-8")).hexdigest()
    d = _job_dir(project_id, job_id, root)
    os.makedirs(d)
    _write_json(os.path.join(d, "plan.json"), plan)
    with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
        f.write(plan_markdown(plan))
    _set_state(project_id, job_id, root, "planned")
    return plan


def load_plan(project_id, job_id, root=ROOT):
    plan = _read_json(os.path.join(_job_dir(project_id, job_id, root), "plan.json"))
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if hashlib.sha256(_canon(body).encode("utf-8")).hexdigest() != plan.get("plan_sha256"):
        raise Refused("The saved plan was edited after it was made; make a new plan.")
    return plan


def plan_items(plan):
    file_params = {i["param"] for i in plan["inputs"]}
    return ([f"step:{n}" for n in range(1, len(plan["steps"]) + 1)] + [f"input:{i['param']}" + (f"[{i['index'] + 1}]" if "index" in i else "") for i in plan["inputs"]]
            + [f"param:{k}" for k in plan["params"] if k not in file_params] + [f"output:{o}" for o in plan["outputs"]])


def _reviews_path(project_id, job_id, root):
    return os.path.join(_job_dir(project_id, job_id, root), "reviews.json")


def reviews(project_id, job_id, root=ROOT):
    p = _reviews_path(project_id, job_id, root)
    return _read_json(p) if os.path.isfile(p) else {}


def review_item(project_id, job_id, item, verdict, note, by, root=ROOT):
    """The designer checks one line of a plan (or one delivered file) and approves it or marks it corrected with a note."""
    who = _who(by)
    plan = load_plan(project_id, job_id, root)
    st = _state(project_id, job_id, root)["state"]
    if verdict not in ("approved", "corrected"):
        raise Refused("A check is either 'approved' or 'corrected'.")
    note = str(note or "").strip()
    if len(note) > 500:
        raise Refused("The note is longer than 500 characters.")
    if verdict == "corrected" and not note:
        raise Refused("Say what is wrong in the note when you mark something corrected.")
    if str(item).startswith("result:"):
        stt = _state(project_id, job_id, root)
        if st != "done" or item[7:] not in list(stt.get("delivered", [])):
            raise Refused("That is not a file this job delivered.")
    elif item not in plan_items(plan) or st not in ("planned", "approved"):
        raise Refused("That is not a line of this plan, or the plan is no longer open.")
    r = reviews(project_id, job_id, root)
    r[item] = {"verdict": verdict, "note": note, "by": who, "at": _now()}
    _write_json(_reviews_path(project_id, job_id, root), r)
    _log(project_id, root, job_id, "reviewed", item=item, verdict=verdict)
    return r


def unreviewed(project_id, job_id, root=ROOT):
    r = reviews(project_id, job_id, root)
    return [i for i in plan_items(load_plan(project_id, job_id, root)) if r.get(i, {}).get("verdict") != "approved"]


def replan(project_id, job_id, changes, by, note, root=ROOT):
    """A correction to a parameter makes a NEW plan (the old one is cancelled); the designer then checks and approves the new one."""
    who = _who(by)
    plan = load_plan(project_id, job_id, root)
    if _state(project_id, job_id, root)["state"] not in ("planned", "approved"):
        raise Refused("Only a plan that has not run can be changed.")
    if not isinstance(changes, dict) or not changes:
        raise Refused("Say which parameter to change.")
    unknown = sorted(set(changes) - set(plan["params"]) - set(OPERATIONS[plan["op"]]["params"]))
    if unknown:
        raise Refused(f"There is no parameter {', '.join(unknown)}.")
    params = {**plan["params"], **changes}
    new = plan_job(project_id, plan["op"], params, requested_by="designer", root=root)
    _set_state(project_id, job_id, root, "cancelled", replaced_by=new["job_id"])
    _log(project_id, root, new["job_id"], "replanned", from_job=job_id, by=who, note=str(note or "")[:300], changed=sorted(changes))
    return new


def approve(project_id, job_id, approved_by, plan_sha256, root=ROOT, require_reviews=False):
    who = _who(approved_by)
    plan = load_plan(project_id, job_id, root)
    st = _state(project_id, job_id, root)
    if st["state"] != "planned":
        raise Refused(f"Only a planned job can be approved; this one is {st['state']}.")
    if plan_sha256 != plan["plan_sha256"]:
        raise Refused("The fingerprint does not match the plan; approve the plan exactly as shown.")
    if require_reviews:
        todo = unreviewed(project_id, job_id, root)
        if todo:
            raise Refused(f"Check every line of the plan first; {len(todo)} are not approved yet (for example {todo[0]}).")
    changed = [i["path"] for i in plan["inputs"] if not os.path.isfile(i["path"]) or sha256_file(i["path"]) != i["sha256"]]
    if changed:
        raise Refused("A file changed since the plan was made: " + ", ".join(changed) + ". Make a new plan.")
    _write_json(os.path.join(_job_dir(project_id, job_id, root), "approval.json"), {"approved_by": who, "plan_sha256": plan["plan_sha256"], "at": _now()})
    _set_state(project_id, job_id, root, "approved", approved_by=who)
    return plan


def cancel(project_id, job_id, root=ROOT):
    st = _state(project_id, job_id, root)
    if st["state"] not in ("planned", "approved"):
        raise Refused(f"Only a job that has not run can be cancelled; this one is {st['state']}.")
    _set_state(project_id, job_id, root, "cancelled")


def _fail(project_id, job_id, root, staging, message, flags=None):
    shutil.rmtree(staging, ignore_errors=True)
    _set_state(project_id, job_id, root, "failed", error=message, flags=flags or [])
    return {"ok": False, "state": "failed", "error": message, "flags": flags or []}


def run_job(project_id, job_id, root=ROOT):
    d = _job_dir(project_id, job_id, root)
    st = _state(project_id, job_id, root)
    if st["state"] != "approved":
        raise Refused(f"A job runs only after the designer approves it; this one is {st['state']}.")
    plan = load_plan(project_id, job_id, root)
    ap = _read_json(os.path.join(d, "approval.json"))
    if ap.get("plan_sha256") != plan["plan_sha256"] or not ap.get("approved_by"):
        raise Refused("The approval does not match this plan.")
    for other in os.listdir(os.path.join(project_dir(project_id, root), "_job")):
        if other != job_id and re.fullmatch(r"job-\d{4}", other) and _state(project_id, other, root)["state"] == "running":
            raise Refused(f"{other} is still running (or was interrupted); one job at a time.")
    op = OPERATIONS.get(plan["op"])
    if op is None:
        raise Refused(f"The operation '{plan['op']}' is no longer available.")
    p = project_dir(project_id, root)
    staging = os.path.join(d, "staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    _set_state(project_id, job_id, root, "running")
    made_copies = []

    def fail(message, flags=None):
        for c in made_copies:
            try:
                os.remove(os.path.join(p, *c.split("/")))
            except OSError:
                pass
        return _fail(project_id, job_id, root, staging, message, flags)

    try:
        for i in plan["inputs"]:
            if not os.path.isfile(i["path"]) or sha256_file(i["path"]) != i["sha256"]:
                return fail(f"The file {i['path']} changed after the approval; nothing was copied or run.", ["ORIGINAL CHANGED BEFORE THE RUN"])
        ctx_inputs = {}
        for i in plan["inputs"]:
            dst = os.path.join(p, *i["copy"].split("/"))
            if os.path.exists(dst):
                if sha256_file(dst) != i["sha256"]:
                    return fail(f"{i['copy']} already exists in the project with different content; nothing was run.")
            else:
                shutil.copy2(i["path"], dst)
                made_copies.append(i["copy"])
                if sha256_file(dst) != i["sha256"]:
                    os.remove(dst)
                    return fail(f"The copy of {i['path']} does not match the original; nothing was run.")
            if "index" in i:
                ctx_inputs.setdefault(i["param"], []).append(dst)
            else:
                ctx_inputs[i["param"]] = dst
        run_params = {k: ctx_inputs.get(k, v) for k, v in plan["params"].items()}
        ctx = {"project_dir": p, "job_id": job_id, "params": run_params, "inputs": ctx_inputs, "staging": staging, "outputs_expected": list(plan["outputs"])}
        try:
            result = op["run"](ctx) or {}
        except Exception as e:
            return fail(f"The operation stopped: {str(e)[:300]}")
        changed = [i["path"] for i in plan["inputs"] if not os.path.isfile(i["path"]) or sha256_file(i["path"]) != i["sha256"]]
        if changed:
            return fail("An original changed during the run: " + ", ".join(changed) + ". Nothing was delivered.", ["ORIGINAL CHANGED"])
        made = result.get("outputs") or {}
        if sorted(made) != sorted(plan["outputs"]):
            return fail(f"The operation made {sorted(made)} but the plan promised {sorted(plan['outputs'])}. Nothing was delivered.")
        sroot = os.path.realpath(staging)
        for name, path in made.items():
            rp = os.path.realpath(path) if isinstance(path, str) else ""
            if os.path.dirname(rp) != sroot or not os.path.isfile(rp) or os.path.getsize(rp) == 0:
                return fail(f"The output '{name}' is missing, empty or outside the staging folder. Nothing was delivered.")
        preview = result.get("preview")
        if preview:
            rp = os.path.realpath(preview)
            if os.path.dirname(rp) != sroot or not os.path.isfile(rp):
                return fail("The preview is missing or outside the staging folder. Nothing was delivered.")
        exports = os.path.join(p, "05_Exports")
        previews = os.path.join(p, "04_Previews")
        targets = [(os.path.realpath(made[n]), os.path.join(exports, n)) for n in plan["outputs"]]
        if preview:
            pname = f"{job_id}_{os.path.basename(preview)}"
            targets.append((os.path.realpath(preview), os.path.join(previews, pname)))
        clash = [t for _, t in targets if os.path.exists(t)]
        if clash:
            return fail("A file with the same name is already delivered: " + ", ".join(os.path.basename(c) for c in clash) + ". Nothing was replaced.")
        moved = []
        try:
            for src, dst in targets:
                os.replace(src, dst)
                moved.append(dst)
        except OSError as e:
            for m in moved:
                os.remove(m)
            return fail(f"Delivering the files failed ({e}); nothing was left behind.")
        shutil.rmtree(staging, ignore_errors=True)
        delivered = [os.path.relpath(m, p).replace("\\", "/") for m in moved]
        _set_state(project_id, job_id, root, "done", delivered=delivered, copies=made_copies, notes=list(result.get("notes") or []), preview=(delivered[-1] if preview else None))
        return {"ok": True, "state": "done", "delivered": delivered, "preview": delivered[-1] if preview else None, "notes": list(result.get("notes") or [])}
    except Exception as e:
        return fail(f"The runner stopped: {str(e)[:300]}")


def undo_job(project_id, job_id, root=ROOT):
    """Removes what the job delivered and the copies it made. The originals were never touched."""
    st = _state(project_id, job_id, root)
    if st["state"] != "done":
        raise Refused(f"Only a finished job can be undone; this one is {st['state']}.")
    p = project_dir(project_id, root)
    removed = []
    for rel in list(st.get("delivered", [])) + list(st.get("copies", [])):
        f = os.path.realpath(os.path.join(p, *rel.split("/")))
        if os.path.dirname(os.path.dirname(f)) != os.path.realpath(p) or not os.path.isfile(f):
            continue
        os.remove(f)
        removed.append(rel)
    _set_state(project_id, job_id, root, "undone", removed=removed)
    return removed


def release_interrupted(project_id, job_id, approved_by, root=ROOT):
    """A job left 'running' by a crash blocks the project; the designer releases it (it becomes failed)."""
    _who(approved_by)
    if _state(project_id, job_id, root)["state"] != "running":
        raise Refused("That job is not running.")
    shutil.rmtree(os.path.join(_job_dir(project_id, job_id, root), "staging"), ignore_errors=True)
    _set_state(project_id, job_id, root, "failed", error="The run was interrupted and released by the designer.")


def status(project_id, job_id, root=ROOT):
    return _state(project_id, job_id, root)


def list_jobs(project_id, root=ROOT):
    jd = os.path.join(project_dir(project_id, root), "_job")
    return [{"job": d, **_state(project_id, d, root)} for d in sorted(os.listdir(jd)) if re.fullmatch(r"job-\d{4}", d)]
