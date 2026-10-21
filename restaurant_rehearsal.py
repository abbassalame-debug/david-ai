"""
restaurant_rehearsal.py -- Job 1: runs a generated apply.sql / undo.sql on a THROW-AWAY in-memory copy of the app's tables before the manager sees them.

The copy is SQLite, built in memory from the newest app backup (read-only); nothing touches Supabase or any file. The rehearsal proves what can be proven
without the real database: the file runs without an error, every line of its final check table is found, running it twice changes nothing, and undo.sql puts
the rows back exactly as they were (a day row that apply.sql created stays, on purpose). It cannot prove Postgres accepts the SQL or that the live data still
equals the backup: the check table at the end of apply.sql is for that.
"""
import json
import sqlite3

import restaurant_sql as S

COLS = {
    "days": ["id", "branch_id", "on_date", "status", "rate_usd_to_lbp", "opened_by", "closed_by", "closed_at", "submitted_at", "note", "ended_at", "ended_by", "corrected_at", "corrected_by", "correction_note"],
    "cash_sheets": ["id", "day_id", "sales_usd", "count_cash_usd", "count_cash_lbp", "count_wallet_usd", "rate_used", "safe_old_usd", "safe_counted_usd", "next_float_usd", "float_open_ok",
                    "float_close_ok", "transfer_to_admin_usd", "value_usd", "expected_usd", "safe_new_usd", "diff_usd", "over_short_usd", "closed", "note"],
    "cash_expenses": ["id", "day_id", "label", "amount_usd", "paid_from"],
    "cash_transfers": ["id", "day_id", "amount_usd", "expenses_usd", "hold_usd", "wallet_usd"],
    "spit_loads": ["id", "day_id", "meat_type", "lo_pieces", "weight_kg"],
    "waste_log": ["id", "day_id", "meat_type", "grams"],
    "consumption": ["id", "day_id", "menu_item_id", "qty", "chicken_g", "beef_g", "sausage_g"],
    "meat_receipts": ["id", "day_id", "meat_type", "received_kg"],
    "meat_closing_weights": ["id", "day_id", "meat_type", "trim_kg", "main_kg", "tallow_kg"],
}
UNIQUE = {"days": ", unique (branch_id, on_date)", "cash_sheets": ", unique (day_id)", "consumption": ", unique (day_id, menu_item_id)"}


def make_db(backup):
    """An in-memory SQLite copy of the tables apply.sql touches, filled from a loaded backup ({table: [rows]})."""
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.create_function("now", 0, lambda: "2026-01-01T00:00:00+00:00")   # Postgres now(): the rehearsal only needs it to exist and be the same on every call
    for t, cols in COLS.items():
        con.execute(f"create table {t} (" + ", ".join(f'"{c}"' + (" text primary key" if c == "id" else "") for c in cols) + UNIQUE.get(t, "") + ")")
        for r in backup.get(t) or []:
            ks = [k for k in r if k in cols]
            con.execute(f'insert into {t} ({",".join(ks)}) values ({",".join("?" * len(ks))})',
                        [int(r[k]) if isinstance(r[k], bool) else (json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], (dict, list)) else r[k]) for k in ks])
    con.commit()
    return con


def dump(con):
    out = {}
    for t in COLS:
        cur = con.execute(f"select * from {t}")
        names = [d[0] for d in cur.description]
        out[t] = sorted((dict(zip(names, r)) for r in cur.fetchall()), key=lambda x: str(x["id"]))
    return out


def run(con, sql):
    """Runs a generated file (statements up to its commit, then its final check select). Returns the check table's rows."""
    body, _, tail = sql.partition("commit;")
    con.executescript(body + "commit;")
    tail = tail.strip().rstrip(";")
    return con.execute(tail).fetchall() if tail else []


def rehearse(apply_sql, undo_sql, backup):
    """{"ok", "problems": [...], "checked": n, "rows_added": {table: n}}. backup is a loaded backup dict or a backup folder."""
    bk = S.load_backup(backup)
    problems, added = [], {}
    try:
        con = make_db(bk)
        start = dump(con)
        check = run(con, apply_sql)
        if not check:
            problems.append("apply.sql has no check table at the end")
        bad = [r[0] for r in check if r[1] != r[2]]
        if bad:
            problems.append("lines of the check table not found after apply: " + ", ".join(map(str, bad)))
        after = dump(con)
        added = {t: len(after[t]) - len(start[t]) for t in after if len(after[t]) != len(start[t])}
        run(con, apply_sql)
        if dump(con) != after:
            problems.append("running apply.sql a second time changed the rows")
        run(con, undo_sql)
        restored = dump(con)
        gone_ok = all(restored[t] == start[t] for t in COLS if t != "days") and [d for d in restored["days"] if d not in start["days"]] == [d for d in after["days"] if d not in start["days"]]
        if not gone_ok:
            problems.append("undo.sql did not put the rows back as they were")
    except Exception as e:
        problems.append(f"the SQL failed on the rehearsal copy: {type(e).__name__}: {e}")
        check = []
    return {"ok": not problems, "problems": problems, "checked": len(check), "rows_added": added}
