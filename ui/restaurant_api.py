"""
restaurant_api.py -- the HUD's Restaurant page, backend side (Job 1 daily runner).

A FastAPI router the HUD includes (`app.include_router(restaurant_api.router)`). It is a thin layer over restaurant_day.py:
upload a day's files into David's own INBOX (never into the Demo Bistro folder), run `prepare` as a background job, keep the manager's answers, run `finish`
(approval + SQL + rehearsal) and `check`. Nothing here connects to Supabase or writes the app's database: the manager reads and runs apply.sql himself.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA
import datetime
import glob
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import menu_tools  # noqa: E402
import restaurant_corrections as RC  # noqa: E402
import restaurant_day as D  # noqa: E402
import restaurant_watch as WATCH  # noqa: E402
import restaurant_sql as S  # noqa: E402
import restaurant_upload as UP  # noqa: E402

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".xlsx", ".xlsm", ".xls", ".csv", ".pdf"}
MAX_FILE_BYTES = 60 * 1024 * 1024
MAX_DAY_BYTES = 600 * 1024 * 1024
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_KINDS = {"apply": "apply.sql", "undo": "undo.sql", "plan": "plan.md", "steps": "RUN_STEPS.md", "check": "check.md", "questions": "questions.md"}
ANSWER_FIELDS = ("approved_by", "deliveries_confirmed", "acknowledge_checks", "paid_from_safe", "cleared_blockers", "allow_updates", "held_lines_all_correct", "confirmed", "overrides", "notes")


def _day(day):
    if not DAY_RE.match(day or ""):
        raise HTTPException(400, "the day must look like 2026-10-08")
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "that is not a real date")
    return day


def safe_name(name):
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", base).strip(" .")[:150]
    return base or "file"


def make_router(cfg=None):
    """cfg: {days_dir, review_dir, inbox_dir, restaurant_root, backup}: every path optional (defaults are the real ones); tests pass temp folders."""
    cfg = dict(cfg or {})
    days_dir = cfg.get("days_dir") or D.DAYS_DIR
    review_dir = cfg.get("review_dir") or D.REVIEW_DIR
    inbox_dir = cfg.get("inbox_dir") or D.INBOX_DIR
    restaurant_root = cfg.get("restaurant_root")
    backup_dir = cfg.get("backup")
    connect = cfg.get("connect")
    router = APIRouter(prefix="/api/restaurant")
    lock = threading.Lock()
    job = {"state": "idle"}
    kw = dict(restaurant_root=restaurant_root, backup=backup_dir, days_dir=days_dir, review_dir=review_dir)

    def ddir(day):
        return D.day_dir(day, days_dir)

    def pdir(day):
        return D.pack_dir(day, review_dir)

    def backup_rows():
        try:
            return S.load_backup(backup_dir or S.latest_backup(restaurant_root))
        except (OSError, FileNotFoundError, ValueError):
            return None

    def in_app(day, bk=None):
        bk = bk or backup_rows()
        if not bk:
            return False
        d = next((x for x in bk["days"] if x.get("on_date") == day), None)
        return bool(d and any(s.get("day_id") == d["id"] for s in bk["cash_sheets"]))

    def inbox_files(day):
        box = os.path.join(inbox_dir, day)
        if not os.path.isdir(box):
            return []
        return [{"name": f, "size": os.path.getsize(os.path.join(box, f))} for f in sorted(os.listdir(box)) if os.path.isfile(os.path.join(box, f))]

    def files_state(day):
        p = pdir(day)
        return {k: os.path.isfile(os.path.join(p, f)) for k, f in FILE_KINDS.items()}

    def step_of(day, fs):
        if fs["check"]:
            return 4
        if fs["apply"]:
            return 3
        if os.path.isfile(os.path.join(ddir(day), "questions.json")):
            return 2
        return 1

    def load_json(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def job_view():
        with lock:
            return {k: v for k, v in job.items()}

    @router.get("/overview")
    def overview():
        bk = backup_rows()
        days = set()
        for base in (days_dir, inbox_dir):
            if os.path.isdir(base):
                days |= {d for d in os.listdir(base) if DAY_RE.match(d)}
        for i in range(0, 12):
            d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
            try:
                D.source_folder(d, restaurant_root, inbox_dir)
                days.add(d)
            except D.DayError:
                pass
        out = []
        for d in sorted(days, reverse=True):
            fs = files_state(d)
            out.append({"day": d, "step": step_of(d, fs), "in_app": in_app(d, bk), "inbox_files": len(inbox_files(d)), "files": fs})
        last_in_app = max((x["on_date"] for x in (bk or {}).get("days", []) if in_app(x["on_date"], bk)), default=None)
        nxt = (datetime.date.fromisoformat(last_in_app) + datetime.timedelta(days=1)).isoformat() if last_in_app else datetime.date.today().isoformat()
        return {"days": out, "default_day": nxt, "last_day_in_app": last_in_app, "backup": os.path.basename(str(bk["_name"])) if bk else None, "job": job_view()}

    @router.put("/upload")
    async def upload(request: Request, day: str, name: str):
        _day(day)
        fname = safe_name(name)
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(415, f"{ext or 'that kind of file'} is not a day file (photos, Excel sheets, PDF and CSV are)")
        box = os.path.join(inbox_dir, day)
        os.makedirs(box, exist_ok=True)
        used = sum(f["size"] for f in inbox_files(day))
        target, n = os.path.join(box, fname), 2
        stem, e = os.path.splitext(fname)
        while os.path.exists(target):
            target = os.path.join(box, f"{stem} ({n}){e}")
            n += 1
        tmp = target + ".part"
        size = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, "that file is over 60 MB")
                    if used + size > MAX_DAY_BYTES:
                        raise HTTPException(413, "this day's upload is over 600 MB")
                    f.write(chunk)
            if size == 0:
                raise HTTPException(400, "that file is empty")
            os.replace(tmp, target)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return {"ok": True, "name": os.path.basename(target), "size": size}

    @router.delete("/inbox/{day}")
    def clear_inbox(day: str):
        _day(day)
        box = os.path.join(inbox_dir, day)
        n = len(inbox_files(day))
        if os.path.isdir(box) and os.path.realpath(box).startswith(os.path.realpath(inbox_dir)):
            shutil.rmtree(box, ignore_errors=True)
        return {"ok": True, "removed": n}

    @router.post("/prepare")
    async def prepare(request: Request):
        body = await request.json()
        day = _day(str(body.get("day") or ""))
        with lock:
            if job.get("state") == "running":
                raise HTTPException(409, f"{job.get('day')} is being read right now; wait for it to finish")
            try:
                D.source_folder(day, restaurant_root, inbox_dir)
            except D.DayError as e:
                raise HTTPException(422, str(e))
            job.clear()
            job.update({"id": uuid.uuid4().hex[:8], "day": day, "state": "running", "started": time.time(), "log": [], "message": "reading the photos"})

        def run():
            def log(m):
                with lock:
                    job["log"] = (job["log"] + [str(m)])[-40:]
                    job["message"] = str(m)[:200]
            try:
                r = D.prepare(day, log=log, inbox_dir=inbox_dir, **kw)
                with lock:
                    job.update(state="done", message="the draft is ready", finished=time.time(), valid=r["valid"])
            except Exception as e:
                with lock:
                    job.update(state="error", message=f"{type(e).__name__}: {e}"[:400], finished=time.time())
        threading.Thread(target=run, daemon=True, name=f"restaurant-prepare-{day}").start()
        return job_view()

    @router.get("/menu")
    def menu():
        g = menu_tools.get_menu_glossary()
        return {"items": [{"id": i["item_id"], "name_ar": i["name_ar"], "name_en": i.get("name_en"), "kind": i.get("kind")} for i in g.get("items", []) if i.get("name_ar")]}

    @router.get("/job")
    def get_job():
        return job_view()

    @router.get("/watch")
    def watch_status():
        return WATCH.load_status(WATCH.paths({"state_dir": cfg.get("watch_dir")}))

    @router.get("/folders")
    def day_folders_status():
        """What is in the recent day folders of Demo Bistro, read-only: how many photos and sheets, whether the files have stopped arriving, and how far the day has got in David."""
        wp = WATCH.paths({"restaurant_root": restaurant_root, "days_dir": days_dir, "state_dir": cfg.get("watch_dir")})
        floor = WATCH.newest_uploaded(wp)
        now = datetime.datetime.now()
        out = []
        for d, folder in WATCH.day_folders(wp, now.date(), back=WATCH.LOOK_BACK_DAYS if floor else 3):
            dd = os.path.join(days_dir, d)
            uploaded = os.path.isfile(os.path.join(dd, "upload.json"))
            if floor and d <= floor and not uploaded:
                continue
            info = WATCH.inspect(folder, now.timestamp())
            drafted = bool(glob.glob(os.path.join(dd, "DRAFT-*.json")))
            if uploaded:
                state, msg = "uploaded", "already in the app"
            elif os.path.isfile(os.path.join(dd, "approval.json")):
                state, msg = "approved", "approved, waiting for the upload"
            elif drafted:
                state, msg = "read", "read: open it and answer the questions"
            elif info["unstable"]:
                state, msg = "arriving", "files are still arriving: " + ", ".join(info["unstable"][:2])
            elif info["missing"]:
                state, msg = "waiting", "waiting for " + " and ".join(info["missing"])
            else:
                state, msg = "ready", "complete: press Read when you want it read"
            out.append({"day": d, "state": state, "message": msg, "photos": info["photos"], "sheets": info["sheets"]})
        return {"folders": out, "need": {"photos": WATCH.MIN_PHOTOS, "sheets": WATCH.MIN_SHEETS}}

    @router.get("/day/{day}")
    def get_day(day: str):
        _day(day)
        fs = files_state(day)
        j = job_view()
        apply_path = os.path.join(pdir(day), "apply.sql")
        apply_sha = UP.sha(open(apply_path, encoding="utf-8").read()) if os.path.isfile(apply_path) else None
        return {"day": day, "apply_sha": apply_sha, "upload": load_json(os.path.join(ddir(day), "upload.json")), "step": step_of(day, fs), "in_app": in_app(day), "inbox": inbox_files(day), "source": (load_json(os.path.join(ddir(day), "meta.json")) or {}).get("source"),
                "questions": load_json(os.path.join(ddir(day), "questions.json")), "answers": load_json(os.path.join(pdir(day), "answers.json")), "files": fs,
                "job": j if j.get("day") == day else None}

    @router.put("/day/{day}/answers")
    async def put_answers(day: str, request: Request):
        _day(day)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "send the answers as an object")
        clean = {"day": day}
        for k in ANSWER_FIELDS:
            if k in body:
                clean[k] = body[k]
        if not isinstance(clean.get("approved_by", ""), str) or len(clean.get("approved_by", "")) > 80:
            raise HTTPException(400, "approved_by must be a name")
        for k in ("deliveries_confirmed", "acknowledge_checks", "held_lines_all_correct"):
            if k in clean and not isinstance(clean[k], bool):
                raise HTTPException(400, f"{k} must be true or false")
        for k in ("paid_from_safe", "allow_updates"):
            if k in clean and (not isinstance(clean[k], list) or not all(isinstance(x, str) and len(x) <= 80 for x in clean[k]) or len(clean[k]) > 30):
                raise HTTPException(400, f"{k} must be a short list of names")
        if "allow_updates" in clean and not set(clean["allow_updates"]) <= set(S.TABLES):
            raise HTTPException(400, "allow_updates names a table David does not write")
        if "cleared_blockers" in clean and (not isinstance(clean["cleared_blockers"], dict) or not all(isinstance(v, str) and len(v) <= 400 for v in clean["cleared_blockers"].values())):
            raise HTTPException(400, "cleared_blockers must map a blocker number to a reason")
        if "confirmed" in clean and (not isinstance(clean["confirmed"], dict) or len(clean["confirmed"]) > 300 or not all(isinstance(k, str) and len(k) <= 40 and v is True for k, v in clean["confirmed"].items())):
            raise HTTPException(400, "confirmed must map an item to true")
        if "notes" in clean and (not isinstance(clean["notes"], dict) or len(clean["notes"]) > 80 or not all(isinstance(k, str) and len(k) <= 40 and isinstance(v, str) and len(v) <= 300 for k, v in clean["notes"].items())):
            raise HTTPException(400, "notes must map an item to a short text (300 characters)")
        if "overrides" in clean:
            try:
                clean["overrides"] = RC.validate(clean["overrides"])
            except RC.Invalid as e:
                raise HTTPException(400, f"a correction is not valid: {e}")
        os.makedirs(pdir(day), exist_ok=True)
        path = os.path.join(pdir(day), "answers.json")
        cur = load_json(path) or {}
        cur.update(clean)
        json.dump(cur, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return {"ok": True, "answers": cur}

    @router.post("/day/{day}/finish")
    def finish(day: str):
        _day(day)
        try:
            r = D.finish(day, **kw)
        except D.DayError as e:
            return JSONResponse({"ok": False, "status": "answers_missing", "errors": str(e).split("\n")}, status_code=422)
        except (S.Refuse, FileNotFoundError) as e:
            return JSONResponse({"ok": False, "status": "stopped", "errors": [str(e)]}, status_code=422)
        return {k: r.get(k) for k in ("ok", "status", "reasons", "needs_human", "info", "rehearsal", "learn_command")} | {"files": files_state(day)}

    @router.get("/db/status")
    def db_status():
        import importlib.util
        return {"configured": UP.configured() or connect is not None, "driver": importlib.util.find_spec("psycopg") is not None or connect is not None, "variable": UP.ENV_URL}

    @router.post("/db/test")
    def db_test():
        return UP.test_connection(connect)

    @router.post("/day/{day}/upload")
    async def upload_day(day: str, request: Request):
        _day(day)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("dry_run"), bool) or not isinstance(body.get("confirm_day"), str) or not isinstance(body.get("sql_sha"), str):
            raise HTTPException(400, "send dry_run (true or false), confirm_day and sql_sha")
        try:
            return await run_in_threadpool(lambda: UP.upload(day, dry_run=body["dry_run"], sql_sha=body["sql_sha"], confirm_day=body["confirm_day"], days_dir=days_dir, review_dir=review_dir, connect=connect))
        except UP.UploadError as e:
            return JSONResponse({"ok": False, "status": "refused", "message": str(e), "details": e.details}, status_code=422)

    @router.post("/day/{day}/check")
    def check(day: str, live: bool = False):
        _day(day)
        try:
            if live:
                return UP.live_check(day, days_dir=days_dir, connect=connect)
            return D.check(day, **kw)
        except (D.DayError, FileNotFoundError, UP.UploadError) as e:
            return JSONResponse({"ok": False, "status": "stopped", "message": str(e)}, status_code=422)

    @router.get("/day/{day}/file/{which}")
    def get_file(day: str, which: str):
        _day(day)
        if which not in FILE_KINDS:
            raise HTTPException(404, "unknown file")
        path = os.path.join(pdir(day), FILE_KINDS[which])
        if not os.path.isfile(path):
            raise HTTPException(404, "that file has not been made yet")
        return {"name": FILE_KINDS[which], "path": path, "text": open(path, encoding="utf-8").read()}

    return router


router = make_router()
