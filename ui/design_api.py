"""
design_api.py -- the HUD's Design page, backend side (Design portal piece 1b).

A FastAPI router the HUD includes (`app.include_router(design_api.router)`). A thin layer over design_runner.py: every action on a design file is a job
of a registered, fixed operation that goes plan -> the designer checks every line -> his yes (his name + the plan's fingerprint) -> run on copies. Nothing here
opens an Adobe app by itself, and nothing runs without the approve call. Preferences are read and confirmed through design_preferences.py.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA
import json
import os
import re
import sys
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

import design_naming  # noqa: E402
import design_ops  # noqa: E402
import design_pipeline as PL  # noqa: E402
import design_preferences as PREF  # noqa: E402
import design_runner as R  # noqa: E402

MAX_UPLOAD = 300 * 1024 * 1024
UPLOAD_EXT = {".psd", ".psb", ".ai", ".indd", ".idml", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".svg", ".gif", ".txt", ".md", ".mp4", ".mov", ".ttf", ".otf"}
SERVE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf", ".json", ".md", ".txt"}
SERVE_DIRS = ("01_Brief", "04_Previews", "05_Exports", "06_Delivery")
HANDSHAKE = str(DAVID_DATA / "design_data" / "live_handshake.json")


def safe_name(name):
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", base).strip(" .")[:150]
    return base or "file"


def make_router(cfg=None):
    """cfg: {root, prefs_path, handshake_path}: all optional (the real ones by default); tests pass temp folders."""
    cfg = dict(cfg or {})
    root = cfg.get("root") or R.ROOT
    prefs_path = cfg.get("prefs_path") or PREF.PATH
    handshake_path = cfg.get("handshake_path") or HANDSHAKE
    design_ops.register_builtin()
    router = APIRouter(prefix="/api/design")
    run_lock = threading.Lock()

    def pdir(pid):
        try:
            p = R.project_dir(pid, root)
        except R.Refused as e:
            raise HTTPException(400, str(e))
        if not os.path.isfile(os.path.join(p, "project.json")):
            raise HTTPException(404, "that project does not exist")
        return p

    def refuse(e):
        raise HTTPException(409, str(e))

    def read_json(path, default=None):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def job_view(pid, jid):
        plan = R.load_plan(pid, jid, root)
        st = R.status(pid, jid, root)
        rv = R.reviews(pid, jid, root)
        return {"job": jid, "state": st["state"], "approved_by": st.get("approved_by"), "error": st.get("error"), "flags": st.get("flags", []),
                "delivered": st.get("delivered", []), "preview": st.get("preview"), "notes": st.get("notes", []), "replaced_by": st.get("replaced_by"),
                "plan": plan, "items": R.plan_items(plan), "reviews": rv, "todo": R.unreviewed(pid, jid, root) if st["state"] == "planned" else []}

    @router.get("/overview")
    def overview():
        projects = []
        for pid in R.list_projects(root):
            meta = read_json(os.path.join(root, pid, "project.json"), {})
            jobs = R.list_jobs(pid, root)
            projects.append({"id": pid, "client": meta.get("client"), "project": meta.get("project"), "created": meta.get("created"), "jobs": len(jobs), "last_state": jobs[-1]["state"] if jobs else None})
        prefs, presets = None, []
        try:
            prof = PREF.load(prefs_path)
            prefs = {**PREF.summary(prof), "updated": prof.get("updated")}
            presets = [{"id": r["id"], "name": r["name"], "color_mode": r["color_mode"], "width": r["width"], "height": r["height"], "unit": r["unit"], "status": r["status"]} for r in prof["document_presets"]]
        except (OSError, ValueError):
            prefs = None
        ops = [{"id": o["id"], "app": o["app"], "title": o["title"], "params": o["params"]} for o in R.OPERATIONS.values()]
        return {"projects": projects, "operations": ops, "presets": presets, "preferences": prefs, "handshake": read_json(handshake_path), "root": root}

    @router.post("/projects")
    async def create_project(request: Request):
        body = await request.json()
        try:
            structure = None
            try:
                structure = PREF.setting(PREF.load(prefs_path), "folders.structure")
            except (OSError, ValueError):
                pass
            pid = R.create_project(str(body.get("client", "")), str(body.get("project", "")), body.get("date") or None, root=root, structure=structure)
        except (R.Refused, design_naming.BadName) as e:
            refuse(e)
        return {"ok": True, "id": pid}

    @router.delete("/projects/{pid}")
    async def delete_project(pid: str, request: Request):
        pdir(pid)
        body = await request.json()
        try:
            R.delete_project(pid, str(body.get("confirm", "")), root)
        except R.Refused as e:
            refuse(e)
        return {"ok": True}

    @router.get("/projects/{pid}")
    def project(pid: str):
        p = pdir(pid)
        meta = read_json(os.path.join(p, "project.json"), {})
        brief_path = os.path.join(p, "01_Brief", "brief.md")
        brief = open(brief_path, encoding="utf-8").read() if os.path.isfile(brief_path) else ""
        refs = sorted(f for f in os.listdir(os.path.join(p, "01_Brief")) if f != "brief.md") if os.path.isdir(os.path.join(p, "01_Brief")) else []
        jobs = [job_view(pid, j["job"]) for j in R.list_jobs(pid, root)]
        return {"id": pid, "meta": meta, "brief": brief, "references": refs, "jobs": jobs,
                "files": {d: sorted(os.listdir(os.path.join(p, d))) for d in SERVE_DIRS if os.path.isdir(os.path.join(p, d))}}

    @router.put("/projects/{pid}/brief")
    async def put_brief(pid: str, request: Request):
        p = pdir(pid)
        body = await request.json()
        text = str(body.get("text", ""))
        if len(text) > 20000:
            raise HTTPException(413, "the brief is longer than 20000 characters")
        os.makedirs(os.path.join(p, "01_Brief"), exist_ok=True)
        with open(os.path.join(p, "01_Brief", "brief.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return {"ok": True}

    @router.put("/projects/{pid}/references/{name}")
    async def put_reference(pid: str, name: str, request: Request):
        p = pdir(pid)
        fname = safe_name(name)
        if os.path.splitext(fname)[1].lower() not in UPLOAD_EXT:
            raise HTTPException(415, f"{fname}: this file type is not accepted as a reference")
        dest_dir = os.path.join(p, "01_Brief")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fname)
        if fname == "brief.md":
            raise HTTPException(409, "that name is reserved for the brief")
        tmp = dest + ".part"
        size = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, f"{fname} is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
                    f.write(chunk)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return {"ok": True, "name": fname, "size": size, "path": dest}

    @router.post("/projects/{pid}/plan")
    async def plan(pid: str, request: Request):
        pdir(pid)
        body = await request.json()
        try:
            pl = await run_in_threadpool(lambda: R.plan_job(pid, str(body.get("op", "")), body.get("params") or {}, "designer", root))
        except R.Refused as e:
            refuse(e)
        return job_view(pid, pl["job_id"])

    @router.post("/projects/{pid}/jobs/{jid}/review")
    async def review(pid: str, jid: str, request: Request):
        pdir(pid)
        b = await request.json()
        try:
            R.review_item(pid, jid, str(b.get("item", "")), str(b.get("verdict", "")), b.get("note"), b.get("by"), root)
        except R.Refused as e:
            refuse(e)
        return job_view(pid, jid)

    @router.post("/projects/{pid}/jobs/{jid}/replan")
    async def replan(pid: str, jid: str, request: Request):
        pdir(pid)
        b = await request.json()
        try:
            new = await run_in_threadpool(lambda: R.replan(pid, jid, b.get("changes"), b.get("by"), b.get("note"), root))
        except R.Refused as e:
            refuse(e)
        return job_view(pid, new["job_id"])

    @router.post("/projects/{pid}/jobs/{jid}/approve")
    async def approve(pid: str, jid: str, request: Request):
        pdir(pid)
        b = await request.json()
        try:
            R.approve(pid, jid, b.get("approved_by"), b.get("plan_sha256"), root, require_reviews=True)
        except R.Refused as e:
            refuse(e)
        return job_view(pid, jid)

    @router.post("/projects/{pid}/jobs/{jid}/run")
    async def run(pid: str, jid: str):
        pdir(pid)
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "another job is running; one at a time")
        try:
            try:
                res = await run_in_threadpool(lambda: R.run_job(pid, jid, root))
            except R.Refused as e:
                refuse(e)
        finally:
            run_lock.release()
        return {"result": res, "job": job_view(pid, jid)}

    @router.post("/projects/{pid}/jobs/{jid}/cancel")
    async def cancel(pid: str, jid: str):
        pdir(pid)
        try:
            R.cancel(pid, jid, root)
        except R.Refused as e:
            refuse(e)
        return job_view(pid, jid)

    @router.post("/projects/{pid}/jobs/{jid}/undo")
    async def undo(pid: str, jid: str):
        pdir(pid)
        try:
            removed = R.undo_job(pid, jid, root)
        except R.Refused as e:
            refuse(e)
        return {"removed": removed, "job": job_view(pid, jid)}

    @router.get("/projects/{pid}/file")
    def get_file(pid: str, rel: str):
        p = pdir(pid)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) < 2 or parts[0] not in SERVE_DIRS or ".." in parts:
            raise HTTPException(400, "that file cannot be shown")
        f = os.path.realpath(os.path.join(p, *parts))
        if not f.startswith(os.path.realpath(os.path.join(p, parts[0])) + os.sep) or not os.path.isfile(f) or os.path.splitext(f)[1].lower() not in SERVE_EXT:
            raise HTTPException(404, "no such file")
        return FileResponse(f)

    @router.get("/preferences")
    def preferences():
        try:
            prof = PREF.load(prefs_path)
        except (OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, 404)
        return {"profile": {k: v for k, v in prof.items() if k != "log"}, "summary": PREF.summary(prof), "log": prof["log"][-30:],
                "waiting": [{"section": s, "id": i, "status": st} for s, i, st in PREF.drafts(prof)], "settings_help": {k: v[1] for k, v in PREF.SETTINGS.items()}}

    @router.post("/preferences/confirm")
    async def pref_confirm(request: Request):
        b = await request.json()
        try:
            prof = PREF.load(prefs_path)
            PREF.confirm(prof, str(b.get("section", "")), str(b.get("id", "")), b.get("approved_by"), b.get("note", ""))
            PREF.save(prof, prefs_path)
        except PREF.Invalid as e:
            refuse(e)
        return {"ok": True, "summary": PREF.summary(prof)}

    @router.post("/preferences/correct")
    async def pref_correct(request: Request):
        b = await request.json()
        try:
            prof = PREF.load(prefs_path)
            PREF.correct(prof, str(b.get("id", "")), b.get("value"), b.get("approved_by"), b.get("note", ""))
            PREF.save(prof, prefs_path)
        except PREF.Invalid as e:
            refuse(e)
        return {"ok": True, "summary": PREF.summary(prof)}

    # ---- the end-to-end chain (design_pipeline.py): one plan, the designer's yes by name, then the stages run one after another in the background ----

    def pipeline_view(pid, plid):
        plan = PL.load_plan(pid, plid, root)
        md = ""
        try:
            md = open(os.path.join(PL._pdir(pid, plid, root), "plan.md"), encoding="utf-8").read()
        except OSError:
            pass
        return {"plan": plan, "state": PL.state(pid, plid, root), "markdown": md}

    @router.get("/projects/{pid}/pipelines")
    async def pipelines(pid: str):
        pdir(pid)
        return {"pipelines": PL.list_pipelines(pid, root), "params": PL.PARAMS}

    @router.post("/projects/{pid}/pipelines")
    async def pipeline_plan(pid: str, request: Request):
        pdir(pid)
        body = await request.json()
        try:
            plan = await run_in_threadpool(lambda: PL.plan_pipeline(pid, body.get("params") or {}, "designer", root))
        except R.Refused as e:
            refuse(e)
        return pipeline_view(pid, plan["pipeline_id"])

    @router.get("/projects/{pid}/pipelines/{plid}")
    async def pipeline_get(pid: str, plid: str):
        pdir(pid)
        try:
            return pipeline_view(pid, plid)
        except R.Refused as e:
            refuse(e)
        except OSError:
            raise HTTPException(404, "no such pipeline")

    @router.post("/projects/{pid}/pipelines/{plid}/approve")
    async def pipeline_approve(pid: str, plid: str, request: Request):
        pdir(pid)
        b = await request.json()
        try:
            PL.approve_pipeline(pid, plid, b.get("approved_by"), b.get("plan_sha256"), root)
        except R.Refused as e:
            refuse(e)
        return pipeline_view(pid, plid)

    @router.post("/projects/{pid}/pipelines/{plid}/run")
    async def pipeline_run(pid: str, plid: str, request: Request):
        pdir(pid)
        b = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        try:
            PL.load_plan(pid, plid, root)
            if PL.state(pid, plid, root)["state"] not in ("approved", "failed"):
                raise R.Refused("Only an approved (or failed) pipeline can run.")
        except R.Refused as e:
            refuse(e)
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "another job is running; one at a time")

        def work():
            try:
                PL.run_pipeline(pid, plid, root, resume=bool(b.get("resume")))
            except R.Refused as e:
                try:
                    PL._set(pid, plid, root, "failed", error=str(e))
                except Exception:
                    pass
            except Exception as e:
                try:
                    PL._set(pid, plid, root, "failed", error=f"{type(e).__name__}: {e}"[:300])
                except Exception:
                    pass
            finally:
                run_lock.release()

        threading.Thread(target=work, daemon=True).start()
        return {"started": True, **pipeline_view(pid, plid)}

    @router.post("/projects/{pid}/pipelines/{plid}/undo")
    async def pipeline_undo(pid: str, plid: str):
        pdir(pid)
        try:
            removed = await run_in_threadpool(lambda: PL.undo_pipeline(pid, plid, root))
        except R.Refused as e:
            refuse(e)
        return {"removed": removed, **pipeline_view(pid, plid)}

    return router


router = make_router()
