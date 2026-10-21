"""
restaurant_upload.py -- Job 1: the manager's "Upload to the app" button, as safely as a button that writes can be.

The manager presses it on the Restaurant page after he confirmed or corrected every item and David made apply.sql. It runs THE SAME apply.sql he can read on the page, inside
ONE database transaction, and commits only if every line of the file's own check table is found. Never anywhere else, never on its own.

  * It needs a Postgres connection string in the environment variable DAVID_APP_DB_URL (Supabase: Project Settings, Database, Connection string, URI). The manager sets it
    himself, in his own Windows settings; nothing here reads it from a file, prints it, logs it or sends it anywhere but to the database. No variable, no button.
  * Before it writes it reads the LIVE tables, re-plans the day against them with the approved draft, and refuses unless the plan is exactly the saved apply.sql: if the app
    changed since the file was made (a day entered by hand, a number edited) it stops and says what is different.
  * `dry_run` does everything, including running the file on the real database, and then ROLLS BACK: nothing is saved. It is the real test that the SQL runs on the real tables.
  * After a real upload it reads the live tables again and re-plans: the day must now be "nothing to do" (the app holds exactly the draft), which is the proof.
  * It records what it did in upload.json next to the day's drafts. It never deletes anything, never touches a table apply.sql does not name, never runs undo.sql by itself.
"""
import datetime
import hashlib
import json
import os
import re
import threading
from decimal import Decimal
from urllib.parse import unquote, urlparse

import restaurant_day as D
import restaurant_sql as S

ENV_URL = "DAVID_APP_DB_URL"
_LOCK = threading.Lock()


class UploadError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or []


# ── a connection, behind a tiny interface so the tests can use a stand-in ──────────────────────────────────────────────────────────

class PgConn:
    """The real thing: psycopg 3, one statement per execute, autocommit off (a transaction starts with the first statement)."""

    def __init__(self, url):
        try:
            import psycopg
        except ImportError:
            raise UploadError("the Postgres driver (psycopg) is not installed on this computer: ask David to install it")
        self._c = psycopg.connect(url, connect_timeout=10, autocommit=False)

    def exec(self, sql):
        self._c.execute(sql)

    def query(self, sql):
        cur = self._c.execute(sql)
        return [d.name for d in cur.description], cur.fetchall()

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()


def configured():
    return bool(os.environ.get(ENV_URL, "").strip())


def default_connect():
    url = os.environ.get(ENV_URL, "").strip()
    if not url:
        raise UploadError(f"the upload is not switched on: set {ENV_URL} to the app database's connection string (see the page for how)")
    try:
        return PgConn(url)
    except UploadError:
        raise
    except Exception as e:
        raise UploadError("could not connect to the app's database: " + clean_error(e, url))


def clean_error(e, url=None):
    """An error as a sentence with the connection string and the password removed (the string never leaves this process)."""
    msg = f"{type(e).__name__}: {e}"
    url = url if url is not None else os.environ.get(ENV_URL, "")
    secrets = [url]
    try:
        pw = urlparse(url).password
        if pw:
            secrets += [pw, unquote(pw)]
    except ValueError:
        pass
    for sec in sorted({x for x in secrets if x}, key=len, reverse=True):
        msg = msg.replace(sec, "***")
    return re.sub(r"postgres(ql)?://\S+", "postgres://***", msg)[:300]


# ── SQL text handling ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def statements(sql):
    """The statements of a generated file, split on ';' outside quotes, with '--' comments dropped."""
    out, cur, i, n, inq = [], [], 0, len(sql), False
    while i < n:
        ch = sql[i]
        if inq:
            cur.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    cur.append("'")
                    i += 1
                else:
                    inq = False
        elif ch == "'":
            inq = True
            cur.append(ch)
        elif ch == "-" and sql[i:i + 2] == "--":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            st = "".join(cur).strip()
            if st:
                out.append(st)
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def split_file(sql):
    """(body statements, check query): what runs inside the transaction (without begin / commit) and the file's own check table."""
    body, check, seen_commit = [], None, False
    for st in statements(sql):
        low = st.lower()
        if low == "begin":
            continue
        if low == "commit":
            seen_commit = True
            continue
        if seen_commit:
            check = st
        else:
            body.append(st)
    if not body or check is None or not check.lower().startswith("select"):
        raise UploadError("this is not a file David made: it has no statements or no check table at the end")
    return body, check


def _norm(sql):
    return "\n".join(l.strip() for l in sql.splitlines() if l.strip() and not l.strip().startswith("--"))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(v):
    if isinstance(v, (Decimal, int, float, bool)) or v is None or isinstance(v, str):
        return v
    return str(v)


def live_backup(conn):
    """The tables apply.sql touches, read from the live database, shaped like a backup export."""
    out = {"_name": "the live database"}
    for t in S.TABLES:
        cols, rows = conn.query(f"select * from {t}")
        out[t] = [{c: _plain(v) for c, v in zip(cols, r)} for r in rows]
    return out


def _approved(day, days_dir):
    try:
        return D.load_approved(day, days_dir)
    except D.DayError as e:
        raise UploadError(str(e))


# ── the things the page can ask ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_connection(connect=None):
    """Reads the database only: can David connect, how many days the app holds, the newest. Nothing is written."""
    try:
        conn = (connect or default_connect)()
    except UploadError as e:
        return {"ok": False, "message": str(e)}
    try:
        conn.exec("set transaction read only")
        _, r1 = conn.query("select count(*) from days")
        _, r2 = conn.query("select max(on_date) from days")
        conn.rollback()
        return {"ok": True, "days": int(r1[0][0]), "last_day": str(r2[0][0]) if r2[0][0] is not None else None}
    except Exception as e:
        return {"ok": False, "message": "connected, but the read failed: " + clean_error(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def live_check(day, *, days_dir=None, connect=None):
    """The app (live) against the approved draft: nothing to do means the app holds exactly the draft. Reads only."""
    draft, approval = _approved(day, days_dir)
    conn = (connect or default_connect)()
    try:
        conn.exec("set transaction read only")
        live = live_backup(conn)
        conn.rollback()
    except Exception as e:
        raise UploadError("could not read the app's database: " + clean_error(e))
    finally:
        conn.close()
    res = S.build_sql(draft, approval, live, validate=False)   # pinned by the approval's fingerprint; validated when approved (the menu memory may have grown since)
    ok = res["status"] == "nothing_to_do"
    return {"ok": ok, "status": "matches" if ok else res["status"], "differences": [o["note"] for o in res["ops"]] + res["needs_human"] + res["reasons"], "source": "the live database"}


def upload(day, *, dry_run, sql_sha, confirm_day, days_dir=None, review_dir=None, connect=None):
    D._day(day)
    if confirm_day != day:
        raise UploadError("the day typed to confirm is not this day")
    pdir, ddir = D.pack_dir(day, review_dir), D.day_dir(day, days_dir)
    apply_path = os.path.join(pdir, "apply.sql")
    if not os.path.isfile(apply_path):
        raise UploadError("there is no apply.sql for this day: make the SQL files first")
    text = open(apply_path, encoding="utf-8").read()
    if sha(text) != sql_sha:
        raise UploadError("the SQL on the page is not the SQL on disk (it was made again): reload the page and look at it again")
    draft, approval = _approved(day, days_dir)
    body, check = split_file(text)
    audit_path = os.path.join(ddir, "upload.json")
    try:
        prior = json.load(open(audit_path, encoding="utf-8"))
    except (OSError, ValueError):
        prior = None
    if not dry_run and prior and prior.get("committed") and prior.get("sql_sha") == sql_sha:
        raise UploadError(f"this SQL was already uploaded ({prior.get('at')}): use Check to see the app")
    if not _LOCK.acquire(blocking=False):
        raise UploadError("another upload is running right now")
    conn = None
    try:
        conn = (connect or default_connect)()
        live = live_backup(conn)
        res = S.build_sql(draft, approval, live, validate=False)
        if res["status"] == "nothing_to_do":
            conn.rollback()
            return {"ok": True, "status": "already_in_app", "committed": False, "dry_run": dry_run, "message": "the app already holds exactly this day: nothing to upload"}
        if res["status"] != "sql_ready":
            conn.rollback()
            raise UploadError("the app has changed since the SQL was made and the day no longer fits: " + "; ".join(res["reasons"] + res["needs_human"])[:300], res["reasons"] + res["needs_human"])
        if _norm(res["apply_sql"]) != _norm(text):
            conn.rollback()
            raise UploadError("the app has changed since the SQL was made (the plan is different now): press 'Make the SQL files' again and look at it", [o["note"] for o in res["ops"]])
        for st in body:
            conn.exec(st)
        _, rows = conn.query(check)
        not_found = [str(r[0]) for r in rows if r[1] != r[2]]
        committed = False
        if not_found or dry_run:
            conn.rollback()
        else:
            conn.commit()
            committed = True
        verified, differences = None, []
        if committed:
            res2 = S.build_sql(draft, approval, live_backup(conn), validate=False)
            conn.rollback()
            verified = res2["status"] == "nothing_to_do"
            differences = [o["note"] for o in res2["ops"]] + res2["needs_human"] + res2["reasons"]
        out = {"ok": not not_found, "status": "committed" if committed else "dry_run_ok" if (dry_run and not not_found) else "rolled_back", "committed": committed, "dry_run": dry_run,
               "checked": len(rows), "not_found": not_found, "verified": verified, "differences": differences, "rows": len(rows)}
        record = {"day": day, "at": datetime.datetime.now().isoformat(timespec="seconds"), "sql_sha": sql_sha, "approved_by": approval.get("approved_by"), "dry_run": dry_run,
                  "committed": committed, "checked": len(rows), "not_found": not_found, "verified": verified}
        os.makedirs(ddir, exist_ok=True)
        if committed or not prior or not prior.get("committed"):
            json.dump(record, open(audit_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out
    except UploadError:
        raise
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        raise UploadError("the database refused it and nothing was saved: " + clean_error(e))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _LOCK.release()
