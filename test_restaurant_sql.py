"""
test_restaurant_sql.py -- offline tests for restaurant_sql.py (stage 4 of Job 1: approved draft -> apply.sql / undo.sql).

The generated SQL is really executed, on a throw-away SQLite copy of the app's tables built here (no Supabase, no network, no model); the rows
that come out are compared with exact values.   Run: py -3.11 test_restaurant_sql.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, re, copy, glob, shutil, sqlite3, tempfile, inspect, datetime
from decimal import Decimal
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import restaurant_sql as S

BR = "00000000-0000-0000-0000-000000000001"
TMP = tempfile.mkdtemp(prefix="sql_test_")

# ── a stand-in for the app's tables (columns from the app session's schema report) ───────────────────────────────────────────────────
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


def empty_backup():
    return {t: [] for t in S.TABLES} | {"_name": "test"}


def make_db(backup):
    con = sqlite3.connect(":memory:")
    con.create_function("now", 0, lambda: "2026-01-01T00:00:00+00:00")
    for t, cols in COLS.items():
        con.execute(f"create table {t} (" + ", ".join(f'"{c}"' + (" text primary key" if c == "id" else "") for c in cols) + UNIQUE.get(t, "") + ")")
        for r in backup[t]:
            ks = list(r)
            con.execute(f'insert into {t} ({",".join(ks)}) values ({",".join("?" * len(ks))})', [int(r[k]) if isinstance(r[k], bool) else r[k] for k in ks])
    con.commit()
    return con


def run(con, sql):
    body, _, tail = sql.partition("commit;")
    con.executescript(body + "commit;")
    tail = tail.strip().rstrip(";")
    return con.execute(tail).fetchall() if tail else []


def dump(con):
    out = {}
    for t in COLS:
        cur = con.execute(f"select * from {t}")
        names = [d[0] for d in cur.description]
        out[t] = sorted((dict(zip(names, r)) for r in cur.fetchall()), key=lambda x: str(x["id"]))
    return out


def as_backup(con):
    d = dump(con)
    return {t: [{k: (bool(v) if k in ("closed", "float_open_ok", "float_close_ok") else v) for k, v in r.items() if v is not None} for r in d[t]] for t in d} | {"_name": "roundtrip"}


def rows(con, table, where="1=1"):
    cur = con.execute(f"select * from {table} where {where}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ── a small valid-looking draft (the pieces restaurant_sql reads) ──────────────────────────────────────────────────────────────────
def draft(day="2026-10-06", **over):
    d = {"schema": "draft_package/v0", "draft_id": f"DRAFT-{day}-001", "business_day": day, "day_status": "ended", "rate_lbp_per_usd": 90000,
         "cash": {"old_cash": 1610.32, "pos_sales": 1799.0, "wallet": 26.0, "expenses": [{"label": "veg.", "amount": 125.10}, {"label": "دواء", "amount": 2.66}],
                  "transfer_to_admin": 1407.44, "counted_usd": 450.0, "counted_lbp": None, "actual_cash_count": 1850.0},
         "computed": {"expected": 1848.12, "counted": 1850.0, "diff": 1.88},
         "cfo_results": {"meat": {"chicken": {"ok": True, "raw_used_kg": 35.68, "pos_sold_kg": 15.65, "waste_kg": 2.4, "inputs": {"starting_kg": 227.196, "added_kg": 68.0, "ending_kg": 259.516}},
                                  "beef": {"ok": True, "raw_used_kg": 7.7, "pos_sold_kg": 2.92, "waste_kg": 1.3, "inputs": {"starting_kg": 54.58, "added_kg": 12.0, "ending_kg": 58.88}}}},
         "flags": []}
    for k, v in over.items():
        d[k] = v
    return d


def approve(d, **kw):
    kw.setdefault("ack_checks", True)
    return S.make_approval(d, "Demo User", today="2026-10-08", **kw)


def build(d, backup=None, approval=None, **kw):
    return S.build_sql(d, approval if approval is not None else approve(d), backup if backup is not None else empty_backup(), today="2026-10-08", validate=False, **kw)


WEIGHTS = {"chicken": {"spit_kg": 36.0, "received_kg": 68, "closing": {"trim_kg": 4, "main_kg": 130.5, "tallow_kg": 0}},
           "beef": {"spit_kg": 7.7, "received_kg": 2, "closing": {"trim_kg": 1.5, "main_kg": 27, "tallow_kg": 6}}}
DAY = "2026-10-06"
DID = S.stable_id(f"day:{BR}:{DAY}")

# ── 1. ids come out exactly as the app makes them ───────────────────────────────────────────────────────────────────────────────────
check("stable_id is deterministic, uuid-shaped, and different for another day", S.stable_id(f"day:{BR}:2026-09-22") == S.stable_id(f"day:{BR}:2026-09-22") != S.stable_id(f"day:{BR}:2026-09-23")
      and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", S.stable_id(f"day:{BR}:2026-09-22")))
check("the cash sheet id follows from the day id (deterministic, uuid-shaped)", S.stable_id("cash_sheet:" + S.stable_id(f"day:{BR}:2026-09-22")) == S.stable_id("cash_sheet:" + S.stable_id(f"day:{BR}:2026-09-22"))
      and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", S.stable_id("cash_sheet:" + S.stable_id(f"day:{BR}:2026-09-22"))))
check("the consumption id is 20 hex of the day plus 12 hex of the menu item (a chicken kilo row)", S.consumption_id("3af0158c-ca7b-0a47-9297-0ce3c27dbb2c", "c1c1c1c1-c1c1-4000-8000-0000000000c1") == "3af0158c-ca7b-0a47-9297-c1c1c1c1c1c1")
check("a stable id is uuid-shaped and the same every time", re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", DID) and DID == S.stable_id(f"day:{BR}:{DAY}") and DID != S.stable_id(f"day:{BR}:2026-10-07"))

# ── 2. literals: numbers from Decimals, text quoted, nothing injectable ─────────────────────────────────────────────────────────────
check("numbers are written plainly: 925, 0.94, -0.01, 1000, never 925.00 or 1E+3", [S.lit(Decimal(x)) for x in ("925.00", "0.940", "-0.01", "1E+3", "-0.00")] == ["925", "0.94", "-0.01", "1000", "0"])
check("null, true and false", [S.lit(None), S.lit(True), S.lit(False), S.lit(0)] == ["null", "true", "false", "0"])
check("a quote is doubled, Arabic stays as it is, newlines and control characters become spaces", S.lit("it's") == "'it''s'" and S.lit("دواء جلع") == "'دواء جلع'" and S.lit("a\nb\x00c\td") == "'a b c d'")
for bad in (float("nan"), float("inf")):
    try:
        S.dec(bad)
        ok = False
    except S.Refuse:
        ok = True
    check(f"{bad} is refused as a number", ok)
try:
    S.dec("1e12")
    ok = False
except S.Refuse:
    ok = True
check("an absurd number is refused", ok)
try:
    S.dec(True)
    ok = False
except S.Refuse:
    ok = True
check("a bool is refused as a number", ok)

# ── 3. the human gate ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
d0 = draft()
r = S.build_sql(d0, None, empty_backup(), validate=False)
check("a missing approval record is refused with the reason said", r["status"] == "refused" and r["apply_sql"] == "" and "not approved" in r["reasons"][0])
ap = approve(d0)
r = S.build_sql(draft(draft_id="DRAFT-2026-10-06-002"), ap, empty_backup(), validate=False)
check("an approval for another draft is refused", r["status"] == "refused" and any("not for" in x for x in r["reasons"]))
d_edit = copy.deepcopy(d0)
d_edit["cash"]["pos_sales"] = 1801.0
r = S.build_sql(d_edit, ap, empty_backup(), validate=False)
check("a draft changed after it was approved is refused (its fingerprint no longer matches)", r["status"] == "refused" and any("changed after it was approved" in x for x in r["reasons"]))
d_chk = draft(flags=["CHECK: the transfer is 1407.44 on one sheet but 1407.52 on the other", "NOTE: just a note", "BLOCKER: Main Reading 'payments_total': the two readings differ"])
r = S.build_sql(d_chk, S.make_approval(d_chk, "Demo User", today="2026-10-08"), empty_backup(), validate=False)
check("an unacknowledged CHECK and an uncleared BLOCKER each stop it, naming the flag", r["status"] == "refused" and len(r["reasons"]) == 2 and any("CHECK" in x for x in r["reasons"]) and any("BLOCKER" in x for x in r["reasons"]))
r = S.build_sql(d_chk, S.make_approval(d_chk, "Demo User", today="2026-10-08", ack_checks=True, clear_blockers={1: "payments row is a duplicate of the Helper"}), empty_backup(), validate=False)
check("acknowledging the CHECK and clearing the BLOCKER with a reason opens the gate (a NOTE never needs it)", r["status"] == "sql_ready")
try:
    S.make_approval(d_chk, "Demo User", clear_blockers={5: "x"})
    ok = False
except S.Refuse:
    ok = True
check("clearing a blocker number that does not exist is refused", ok)
r = S.build_sql(d_chk, S.make_approval(d_chk, "Demo User", today="2026-10-08", ack_checks=True, clear_blockers={1: "   "}), empty_backup(), validate=False)
check("a blocker cleared with a blank reason is not cleared", r["status"] == "refused")
a2 = dict(ap, approved_by=" ")
check("an approval with nobody's name on it is refused", S.build_sql(d0, a2, empty_backup(), validate=False)["status"] == "refused")
check("the draft's day must be ended: an open or unknown day is refused", build(draft(day_status="open"))["status"] == "refused" and "ended" in build(draft(day_status="open"))["reasons"][0])
check("only a draft_package/v0 is accepted", build(draft(schema="other"))["status"] == "refused")

# ── 4. the draft's own arithmetic ───────────────────────────────────────────────────────────────────────────────────────────────────
bad = draft(computed={"expected": 1848.14, "counted": 1850.0, "diff": 1.88})
check("a draft whose Expected is not old + sales - wallet - expenses - transfer is refused, with both numbers", build(bad)["status"] == "refused" and "1848.14" in build(bad)["reasons"][0] and "1848.12" in build(bad)["reasons"][0])
bad = draft(computed={"expected": 1848.12, "counted": 1850.0, "diff": 2.0})
check("a draft whose difference is not New - Expected is refused", build(bad)["status"] == "refused" and "Difference" in build(bad)["reasons"][0])
d_nonew = draft()
d_nonew["cash"]["actual_cash_count"] = None
check("a draft with no New (no total cash counted) is refused", build(d_nonew)["status"] == "refused" and "New" in build(d_nonew)["reasons"][0])
d_nomeat = draft()
d_nomeat["cfo_results"]["meat"]["beef"] = {"ok": False}
check("a draft whose beef check did not run is refused for the meat part, but cash-only still works", build(d_nomeat)["status"] == "refused" and build(d_nomeat, parts=("cash",))["status"] == "sql_ready")

# ── 5. an empty app: the whole day is inserted ─────────────────────────────────────────────────────────────────────────────────────
r = build(d0)
check("a day the app does not have yet gets SQL: the day row, cash sheet, transfer, 2 expenses, 2 sold lines, 2 waste lines (9 inserts)", r["status"] == "sql_ready" and len(r["ops"]) == 9 and r["ops"][0]["table"] == "days" and all(o["kind"] == "insert" for o in r["ops"]), str([(o["table"]) for o in r["ops"]]))
check("the day id is the app's own stable id", r["day_id"] == DID)
check("without the weights workbook, spits and received are NOT generated and the plan says why (4 notes) plus the closing weights (2 notes)", sum("NOT generated" in i for i in r["info"]) == 6 and not any(o["table"] in ("spit_loads", "meat_receipts", "meat_closing_weights") for o in r["ops"]))
con = make_db(empty_backup())
ver = run(con, r["apply_sql"])
check("the final check table shows rows_found = rows_expected on every line (the day's end-of-day stamp included)", len(ver) == 9 and all(v[1] == v[2] for v in ver), str(ver))
day_row = rows(con, "days")[0]
check("a day David creates is a finished day with its end-of-day stamp, like the app's own endBusinessDay: status ended, ended_at and submitted_at filled (now()), ended_by and closed_by = the approver's people id",
      day_row["status"] == "ended" and day_row["ended_at"] and day_row["submitted_at"] and day_row["ended_by"] == day_row["closed_by"] == "00000000-0000-4000-8000-0000000000a1", str(day_row))
check("now() is written as SQL, not as a quoted word, and the day row is still never deleted by undo", "'now()'" not in r["apply_sql"] and r["apply_sql"].count("now()") == 2 and "delete from days" not in r["undo_sql"])
r_v = build(d0, approval=S.make_approval(d0, "Visitor", today="2026-10-08", ack_checks=True))
check("an approver who is not a known person of the app leaves ended_by / closed_by empty (never a guess) and the plan says so; the day is still stamped ended",
      r_v["status"] == "sql_ready" and "null, now(), now(), null" in r_v["apply_sql"] and any("not a known person" in i for i in r_v["info"]))
sheet = rows(con, "cash_sheets")[0]
check("the cash sheet is exactly what the 0029 backfill writes for the same day: sales, wallet, Old, New, Expected, difference, transfer, value, closed",
      (sheet["sales_usd"], sheet["count_wallet_usd"], sheet["safe_old_usd"], sheet["safe_counted_usd"], sheet["safe_new_usd"], sheet["expected_usd"], sheet["diff_usd"], sheet["transfer_to_admin_usd"], sheet["value_usd"], sheet["closed"])
      == (1799.0, 26, 1610.32, 1850, 1850, 1848.12, 1.88, 1407.44, 450, 1) and sheet["id"] == S.stable_id("cash_sheet:" + DID) and sheet["day_id"] == DID
      and (sheet["count_cash_usd"], sheet["count_cash_lbp"], sheet["rate_used"], sheet["next_float_usd"], sheet["over_short_usd"]) == (0, 0, 90000, 211.11, None))
check("the transfer row and the two expense rows", [(t["amount_usd"], t["expenses_usd"], t["hold_usd"], t["wallet_usd"]) for t in rows(con, "cash_transfers")] == [(1407.44, 0, 0, 0)]
      and sorted((e["label"], e["amount_usd"], e["paid_from"]) for e in rows(con, "cash_expenses")) == sorted([("veg.", 125.10, "register"), ("دواء", 2.66, "register")]))
cons = {c["menu_item_id"]: c for c in rows(con, "consumption")}
k = S.KILO_ITEM
check("sold meat is one kilo line per meat: qty = sold kg, grams per unit 1000, id as the app makes it",
      cons[k["chicken"]["menu_item_id"]]["qty"] == 15.65 and cons[k["chicken"]["menu_item_id"]]["chicken_g"] == 1000 and cons[k["chicken"]["menu_item_id"]]["beef_g"] == 0
      and cons[k["beef"]["menu_item_id"]]["qty"] == 2.92 and cons[k["beef"]["menu_item_id"]]["beef_g"] == 1000
      and cons[k["chicken"]["menu_item_id"]]["id"] == S.consumption_id(DID, k["chicken"]["menu_item_id"]))
check("waste is stored in grams (2.4 kg -> 2400, 1.3 kg -> 1300)", {w["meat_type"]: w["grams"] for w in rows(con, "waste_log")} == {"chicken": 2400, "beef": 1300})
before = dump(con)
run(con, r["apply_sql"])
check("running apply.sql a second time changes nothing (fixed ids, on conflict)", dump(con) == before)
check("a re-run of the generator against what the app now holds finds nothing to do", S.build_sql(d0, ap, as_backup(con), today="2026-10-08", validate=False)["status"] == "nothing_to_do")
run(con, r["undo_sql"])
after = dump(con)
check("undo.sql removes every inserted row except the day row, which stays on purpose", all(after[t] == [] for t in COLS if t != "days") and len(after["days"]) == 1 and "left in place on purpose" in r["undo_sql"])
check("and the undo file never deletes from days", not re.search(r"delete\s+from\s+days", r["undo_sql"], re.I))

# ── 6. the weights workbook ─────────────────────────────────────────────────────────────────────────────────────────────────────────
ap_w = approve(d0, meat_weights=WEIGHTS)
r = build(d0, approval=ap_w)
check("with the workbook figures in the approval, spits, received and closing weights are generated too (9 + 2 + 2 + 2 = 15 inserts)", r["status"] == "sql_ready" and len(r["ops"]) == 15, str(len(r["ops"])))
con = make_db(empty_backup())
ver = run(con, r["apply_sql"])
check("all of them are found by the verification table", len(ver) == 15 and all(v[1] == v[2] for v in ver))
check("spits are the workbook's, not the company ledger's (chicken 36, not 35.68); received beef is the tallow-only 2", {s["meat_type"]: s["weight_kg"] for s in rows(con, "spit_loads")} == {"chicken": 36, "beef": 7.7}
      and {s["meat_type"]: s["received_kg"] for s in rows(con, "meat_receipts")} == {"chicken": 68, "beef": 2})
check("closing weights use the app's own id (stableId of meat_closing:<day>:<meat>) so its next save does not make a second row",
      {c["meat_type"]: (c["id"], c["trim_kg"], c["main_kg"], c["tallow_kg"]) for c in rows(con, "meat_closing_weights")}
      == {"chicken": (S.stable_id(f"meat_closing:{DID}:chicken"), 4, 130.5, 0), "beef": (S.stable_id(f"meat_closing:{DID}:beef"), 1.5, 27, 6)})
check("where the workbook and the company ledger differ by more than half a kilo the plan says the workbook wins (beef received 2 against 12)", any("beef received" in i and "the workbook wins" in i for i in r["info"]))
try:
    S.weights_figures({"meat_weights": {"fish": {"spit_kg": 1}}})
    ok = False
except S.Refuse:
    ok = True
check("a weight for a meat that is not chicken or beef is refused", ok)

# ── 7. a day that exists ───────────────────────────────────────────────────────────────────────────────────────────────────────────
OLD_ID = "aaaaaaaa-0000-4000-8000-000000000001"
bk = empty_backup()
bk["days"].append({"id": OLD_ID, "branch_id": BR, "on_date": DAY, "status": "open"})
check("an OPEN day is never written to", build(d0, bk)["status"] == "refused" and "OPEN" in build(d0, bk)["reasons"][0] and build(d0, bk)["apply_sql"] == "")
bk["days"][0]["status"] = "ended"
r = build(d0, bk)
check("an ended day that is empty (the app auto-fills those) gets its children and no new day row; it keeps ITS id, even if it is not the stable one",
      r["status"] == "sql_ready" and r["day_id"] == OLD_ID and not any(o["table"] == "days" for o in r["ops"]) and len(r["ops"]) == 8 and OLD_ID in r["apply_sql"] and DID not in r["apply_sql"])
con = make_db(bk)
run(con, r["apply_sql"])
check("and the rows land under that day", all(x["day_id"] == OLD_ID for t in ("cash_sheets", "cash_transfers", "cash_expenses", "consumption", "waste_log") for x in rows(con, t)) and len(rows(con, "cash_sheets")) == 1)

# a day that is already entered
ENTERED = copy.deepcopy(bk)
ENTERED["cash_sheets"].append({"id": "bbbbbbbb-0000-4000-8000-000000000001", "day_id": OLD_ID, "sales_usd": 1798.0, "count_cash_usd": 0, "count_cash_lbp": 0, "count_wallet_usd": 26, "rate_used": 90000, "safe_old_usd": 1610.32,
                               "safe_counted_usd": 1800, "next_float_usd": 211.11, "float_open_ok": False, "float_close_ok": False, "transfer_to_admin_usd": 1407.44, "value_usd": 450, "expected_usd": 1847.12,
                               "safe_new_usd": 1800, "diff_usd": -47.12, "over_short_usd": None, "closed": True, "note": None})
ENTERED["cash_transfers"].append({"id": "cccccccc-0000-4000-8000-000000000001", "day_id": OLD_ID, "amount_usd": 1407.44, "expenses_usd": 0, "hold_usd": 0, "wallet_usd": 0})
for i, (lab, amt) in enumerate((("veg", 125.10), ("x", 2.66)), 1):
    ENTERED["cash_expenses"].append({"id": f"dddddddd-0000-4000-8000-00000000000{i}", "day_id": OLD_ID, "label": lab, "amount_usd": amt, "paid_from": "register"})
r = build(d0, ENTERED)
check("changing a sheet the manager already entered is blocked unless the approval allows updating cash_sheets (nothing is written)", r["status"] == "needs_human" and r["apply_sql"] == "" and "does not allow updating cash_sheets" in r["needs_human"][0])
ap_u = approve(d0, allow_updates=["cash_sheets"])
r = build(d0, ENTERED, approval=ap_u)
check("with that permission the plan is ready", r["status"] == "sql_ready")
ups = [o for o in r["ops"] if o["kind"] == "update"]
check("exactly one update on the sheet (sales, safe counted, New, Expected, difference) and the two missing sold lines and two waste lines are inserted", len(ups) == 1 and sum(o["kind"] == "insert" for o in r["ops"]) == 4 and ups[0]["table"] == "cash_sheets")
check("the update line carries the old value as a guard", "update cash_sheets set sales_usd = 1799" in r["apply_sql"] and "where id = 'bbbbbbbb-0000-4000-8000-000000000001' and sales_usd = 1798" in r["apply_sql"])
con = make_db(ENTERED)
start = dump(con)
run(con, r["apply_sql"])
s2 = rows(con, "cash_sheets")[0]
check("after apply the sheet equals the draft; the fields the generator does not own (count_cash, rate, float) are untouched", (s2["sales_usd"], s2["safe_new_usd"], s2["expected_usd"], s2["diff_usd"]) == (1799.0, 1850, 1848.12, 1.88) and s2["next_float_usd"] == 211.11)
run(con, r["undo_sql"])
check("undo.sql puts every value back exactly as it was", dump(con) == start)
con = make_db(ENTERED)
con.execute("update cash_sheets set sales_usd = 1820")
ver = run(con, r["apply_sql"])
check("if the live sheet no longer looks like the backup, the guarded update touches nothing and the check table says MISMATCH (rows_found 0)", rows(con, "cash_sheets")[0]["sales_usd"] == 1820 and any(v[1] == 0 for v in ver))

bk2 = copy.deepcopy(ENTERED)
bk2["cash_expenses"][1]["amount_usd"] = 5.0
r = build(d0, bk2, approval=ap_u)
check("expenses the app holds that differ from the draft's are never rewritten: needs human, no SQL at all (all or nothing)", r["status"] == "needs_human" and r["apply_sql"] == "" and any("expenses" in x and "fix them in the app" in x for x in r["needs_human"]))
bk3 = copy.deepcopy(ENTERED)
bk3["cash_transfers"][0]["amount_usd"] = 1400
r = build(d0, bk3, approval=approve(d0, allow_updates=["cash_sheets", "cash_transfers"]))
check("a transfer row that differs is updated when allowed, with its old value as guard", r["status"] == "sql_ready" and "update cash_transfers set amount_usd = 1407.44" in r["apply_sql"] and "and amount_usd = 1400" in r["apply_sql"])

# paid from the safe
d_gaz = draft()
d_gaz["cash"]["expenses"] = [{"label": "veg.", "amount": 125.10}, {"label": "gaz", "amount": 1200.0}]
tot_ = 1610.32 + 1799.0 - 26 - 1325.10 - 1407.44
d_gaz["computed"] = {"expected": round(tot_, 2), "counted": 1850.0, "diff": round(1850.0 - tot_, 2)}
r = build(d_gaz, approval=approve(d_gaz, paid_from={"gaz": "safe"}))
con = make_db(empty_backup())
run(con, r["apply_sql"])
check("an expense the manager says came from the safe is written with paid_from 'safe', the others stay 'register'", r["status"] == "sql_ready" and {e["label"]: e["paid_from"] for e in rows(con, "cash_expenses")} == {"veg.": "register", "gaz": "safe"} and any("paid from the safe" in o["note"] for o in r["ops"]))
con2 = make_db(empty_backup())
run(con2, build(d_gaz)["apply_sql"])
check("with no paid_from in the approval everything is 'register' (the draft cannot tell)", all(e["paid_from"] == "register" for e in rows(con2, "cash_expenses")) and len(rows(con2, "cash_expenses")) == 2)
check("a paid_from naming an expense that is not in the draft is refused with the labels it has", build(d_gaz, approval=approve(d_gaz, paid_from={"gas": "safe"}))["status"] == "refused" and "'veg.'" in build(d_gaz, approval=approve(d_gaz, paid_from={"gas": "safe"}))["reasons"][0])
check("a paid_from other than register or safe is refused", build(d_gaz, approval=approve(d_gaz, paid_from={"gaz": "bank"}))["status"] == "refused")

# ── 8. several spit rows, tallow-only days ────────────────────────────────────────────────────────────────────────────────────────
bk4 = copy.deepcopy(bk)
for i, w in enumerate((20, 20, 8), 1):
    bk4["spit_loads"].append({"id": f"eeeeeeee-0000-4000-8000-00000000000{i}", "day_id": OLD_ID, "meat_type": "chicken", "lo_pieces": 0, "weight_kg": w})
r = build(d0, bk4, approval=approve(d0, meat_weights={"chicken": {"spit_kg": 48}}))
check("three per-spit rows that add up to the workbook's total need nothing", r["status"] == "sql_ready" and not any(o["table"] == "spit_loads" for o in r["ops"]))
r = build(d0, bk4, approval=approve(d0, meat_weights={"chicken": {"spit_kg": 49}}))
check("three per-spit rows that do NOT add up are never rewritten: needs human, no SQL", r["status"] == "needs_human" and r["apply_sql"] == "" and "3 rows adding up to 48" in r["needs_human"][0])
bk5 = copy.deepcopy(bk)
bk5["meat_receipts"].append({"id": "ffffffff-0000-4000-8000-000000000001", "day_id": OLD_ID, "meat_type": "beef", "received_kg": 12})
r = build(d0, bk5, approval=approve(d0, meat_weights={"beef": {"received_kg": 2}}, allow_updates=["meat_receipts"]))
check("the Sep 23 case: a beef delivery of 12 corrected to the tallow-only 2 is one guarded update, with its undo",
      "update meat_receipts set received_kg = 2 where id = 'ffffffff-0000-4000-8000-000000000001' and received_kg = 12;" in r["apply_sql"] and "update meat_receipts set received_kg = 12 where id = 'ffffffff-0000-4000-8000-000000000001' and received_kg = 2;" in r["undo_sql"])
r0 = build(d0, bk5, approval=approve(d0, meat_weights={"beef": {"received_kg": 2}}))
check("and without the permission to update meat_receipts it is held back", r0["status"] == "needs_human" and "meat_receipts" in r0["needs_human"][0])

# ── 9. the cash chain ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def prev_day(new):
    b = empty_backup()
    b["days"].append({"id": "11111111-0000-4000-8000-000000000001", "branch_id": BR, "on_date": "2026-10-05", "status": "ended"})
    b["cash_sheets"].append({"id": "22222222-0000-4000-8000-000000000001", "day_id": "11111111-0000-4000-8000-000000000001", "safe_new_usd": new, "sales_usd": 2, "count_wallet_usd": 0, "transfer_to_admin_usd": 0,
                             "safe_old_usd": 0, "safe_counted_usd": new, "value_usd": 0, "expected_usd": new, "diff_usd": 0, "closed": True})
    return b
r = build(d0, prev_day(1610.35))
check("yesterday's New 0.03 away from today's Old is kept as written and said in the notes (the Sep 27 / 28 case)", r["status"] == "sql_ready" and any("kept as written" in i for i in r["info"]))
r = build(d0, prev_day(1595))
check("yesterday's New 15 away from today's Old stops everything: one of the two sheets is wrong", r["status"] == "needs_human" and r["apply_sql"] == "" and "chain" in r["needs_human"][0])
r = build(d0, prev_day(1610.32))
check("a matching chain says nothing", r["status"] == "sql_ready" and not any("chain" in i for i in r["info"]))
nb = copy.deepcopy(ENTERED)
nb["days"].append({"id": "99999999-0000-4000-8000-000000000001", "branch_id": BR, "on_date": "2026-10-07", "status": "ended"})
nb["cash_sheets"].append({"id": "88888888-0000-4000-8000-000000000001", "day_id": "99999999-0000-4000-8000-000000000001", "sales_usd": 200, "count_wallet_usd": 0, "transfer_to_admin_usd": 0,
                          "safe_old_usd": 1800, "safe_counted_usd": 1900, "value_usd": 0, "expected_usd": 2000, "safe_new_usd": 1900, "diff_usd": -100, "closed": True})
r = build(d0, nb, approval=ap_u)
check("when a day's New changes, the next day's Old, Expected and difference follow (1850 + 200 = 2050 expected, 1900 - 2050 = -150), each guarded",
      "update cash_sheets set safe_old_usd = 1850, expected_usd = 2050, diff_usd = -150 where id = '88888888-0000-4000-8000-000000000001' and safe_old_usd = 1800 and expected_usd = 2000 and diff_usd = -100;" in r["apply_sql"])
con = make_db(nb)
start = dump(con)
run(con, r["apply_sql"])
nxt = rows(con, "cash_sheets", "id = '88888888-0000-4000-8000-000000000001'")[0]
check("run on the stand-in tables the follow-up lands", (nxt["safe_old_usd"], nxt["expected_usd"], nxt["diff_usd"], nxt["safe_new_usd"]) == (1850, 2050, -150, 1900))
run(con, r["undo_sql"])
check("and undo.sql restores both days exactly", dump(con) == start)

# ── 10. a hostile label ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
evil = ["x'); drop table cash_sheets; --", "a'b\"c\\d", "line1\nline2; delete from days", "كيس ' صوص"]
d_evil = draft()
d_evil["cash"]["expenses"] = [{"label": e, "amount": 2.0} for e in evil]
d_evil["cash"]["old_cash"] = 1610.32
exp_total = 8.0
d_evil["computed"] = {"expected": round(1610.32 + 1799.0 - 26 - exp_total - 1407.44, 2), "counted": 1850.0, "diff": round(1850.0 - (1610.32 + 1799.0 - 26 - exp_total - 1407.44), 2)}
r = build(d_evil)
con = make_db(empty_backup())
run(con, r["apply_sql"])
labels = sorted(e["label"] for e in rows(con, "cash_expenses"))
check("text from a photo cannot break out of its quotes: every table survives and each label is stored as text", r["status"] == "sql_ready" and len(rows(con, "cash_sheets")) == 1 and len(labels) == 4 and "x'); drop table cash_sheets; --" in labels and "كيس ' صوص" in labels,
      str(labels))
check("the hostile text sits only inside a values row, never at the start of a statement", sum("drop table cash_sheets" in l for l in r["apply_sql"].splitlines()) == 1 and all(l.startswith("  (") for l in r["apply_sql"].splitlines() if "drop table" in l))
r_n = build(d0, approval=ap_w)
check("in an ordinary file every line is a comment, begin / commit, an insert / update / select head, a values row, 'on conflict' or 'union all'",
      all(re.match(r"(?i)(--|begin;|commit;|insert into |update |select |union all|on conflict |values|  \()", l) for l in r_n["apply_sql"].splitlines() if l.strip()), str([l for l in r_n["apply_sql"].splitlines() if l.strip() and not re.match(r"(?i)(--|begin;|commit;|insert into |update |select |union all|on conflict |values|  \()", l)][:3]))

# ── 11. rounding like the app ──────────────────────────────────────────────────────────────────────────────────────────────────────
d_r = draft()
d_r["cfo_results"]["meat"]["chicken"]["pos_sold_kg"] = 36.515
d_r["cfo_results"]["meat"]["chicken"]["waste_kg"] = 2.0
r = build(d_r)
check("36.515 kg sold is written as 36.52 (the app's round2, as the Sep 4 correction), 2 kg waste as 2000 g", "36.52" in r["apply_sql"] and "36.515" not in r["apply_sql"] and ", 2000)" in r["apply_sql"])

# ── 12. parts ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
rc = build(d0, parts=("cash",))
rm = build(d0, parts=("meat",))
check("parts=cash writes no meat table, parts=meat writes no cash table", rc["status"] == "sql_ready" and not any(o["table"] in ("consumption", "waste_log") for o in rc["ops"])
      and rm["status"] == "sql_ready" and not any(o["table"] in ("cash_sheets", "cash_expenses", "cash_transfers") for o in rm["ops"]))
check("an empty parts list is refused", build(d0, parts=())["status"] == "refused")

# ── 13. the demo draft package (examples/demo_bistro), validated by the analyst's validator ─────────────────────────────────────────
real = json.load(open(str(DAVID_HOME / "examples" / "demo_bistro" / "draft_package.demo.json"), encoding="utf-8"))
check("the demo draft package is on disk", real["business_day"] == "2026-09-22")
blockers = {i + 1: "tested" for i in range(len(S._flags(real, "BLOCKER:")))}
ap_real = S.make_approval(real, "Demo User", today="2026-10-08", ack_checks=True, clear_blockers=blockers)
r = S.build_sql(real, ap_real, empty_backup(), today="2026-10-08")
check("the demo draft passes the validator and gives SQL for an empty app: the day, the cash sheet and its transfer / expense rows, with the draft's own figures",
      r["status"] == "sql_ready" and len(r["ops"]) >= 7 and f"{real['cash']['pos_sales']:g}" in r["apply_sql"] and f"{real['computed']['expected']:g}" in r["apply_sql"], str(r["reasons"])[:300])
bad_real = copy.deepcopy(real)
bad_real["extraction"]["summary_by_items"]["lines"][0]["match"]["item_id"] = "C38"
ap_bad = S.make_approval(bad_real, "Demo User", today="2026-10-08", ack_checks=True, clear_blockers=blockers)
r = S.build_sql(bad_real, ap_bad, empty_backup(), today="2026-10-08")
check("a draft the validator rejects (a line matched to the wrong item) gets no SQL, even with a valid approval", r["status"] == "refused" and "not valid" in r["reasons"][0] and r["apply_sql"] == "")

# ── 14. files and the command line ─────────────────────────────────────────────────────────────────────────────────────────────────
r = build(d0, approval=ap_w)
out = S.write_outputs(r, os.path.join(TMP, "out"), ap_w)
check("apply.sql, undo.sql and plan.md are written under the draft's own folder", sorted(os.path.basename(p) for p in out) == ["apply.sql", "plan.md", "undo.sql"] and all(os.path.isfile(p) for p in out) and DAY in out[0])
plan_text = open(out[2], encoding="utf-8").read()
check("the plan names what apply.sql does, the notes, and how to run it", "## What apply.sql does" in plan_text and "insert cash_sheets" in plan_text and "## How to run" in plan_text and "export-backup" in plan_text)
sql_text = open(out[0], encoding="utf-8").read()
check("apply.sql begins with who approved it and says nothing was run, and has begin / commit around the statements", sql_text.startswith("-- apply.sql for") and "approved by Demo User on 2026-10-08. Nothing here has been run." in sql_text and "\nbegin;\n" in sql_text and "\ncommit;\n" in sql_text)
check("a refused result writes no SQL files (only the plan)", sorted(os.path.basename(p) for p in S.write_outputs(S.build_sql(d0, None, empty_backup(), validate=False), os.path.join(TMP, "out2"))) == ["plan.md"])

bdir = os.path.join(TMP, "backup", "2026-10-06")
os.makedirs(bdir)
for t in S.TABLES:
    json.dump([], open(os.path.join(bdir, t + ".json"), "w", encoding="utf-8"))
dpath = os.path.join(TMP, "DRAFT-2026-10-06-001.json")
json.dump(d0, open(dpath, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(WEIGHTS, open(os.path.join(TMP, "w.json"), "w", encoding="utf-8"))
rc_ = S.main(["approve", dpath, "--by", "Demo User", "--ack-checks", "--weights-json", os.path.join(TMP, "w.json")])
apath = os.path.splitext(dpath)[0] + ".approval.json"
rec = json.load(open(apath, encoding="utf-8"))
check("the approve command writes the record: draft id, fingerprint, who, when, the weights", rc_ == 0 and rec["draft_id"] == d0["draft_id"] and rec["draft_sha256"] == S.draft_sha(d0) and rec["approved_by"] == "Demo User" and rec["meat_weights"]["beef"]["received_kg"] == 2)
rpath = os.path.join(TMP, real["draft_id"] + ".json")
json.dump(real, open(rpath, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(ap_real, open(os.path.join(TMP, "real.approval.json"), "w", encoding="utf-8"), ensure_ascii=False)
rc_ = S.main([rpath, "--approval", os.path.join(TMP, "real.approval.json"), "--backup", bdir, "--out", os.path.join(TMP, "cli_out")])
cli_files = sorted(os.listdir(os.path.join(TMP, "cli_out", real["draft_id"]))) if os.path.isdir(os.path.join(TMP, "cli_out", real["draft_id"])) else []
check("the command line builds the three files from a real draft, its approval and a backup folder, and exits 0", rc_ == 0 and cli_files == ["apply.sql", "plan.md", "undo.sql"])
rc_ = S.main([rpath, "--approval", apath, "--backup", bdir, "--out", os.path.join(TMP, "cli_out2")])
check("and exits 1 and writes nothing when the approval belongs to another draft", rc_ == 1 and not os.path.exists(os.path.join(TMP, "cli_out2")))
nclear = len(S._flags(real, "BLOCKER:"))
argv = ["approve", rpath, "--by", "Demo User", "--ack-checks", "--weights-from-workbook", "--out", os.path.join(TMP, "wb.approval.json")]
for n in range(1, nclear + 1):
    argv += ["--clear-blocker", str(n)]
rc_ = S.main(argv + ["--reason", "tested"])
rec_w = json.load(open(os.path.join(TMP, "wb.approval.json"), encoding="utf-8"))
check("approve --weights-from-workbook puts the real workbook's figures for the day in the approval (Sep 22: chicken spits 35.68, beef 7.7, closing weigh-ins present)",
      rc_ == 0 and rec_w["meat_weights"]["chicken"]["spit_kg"] == 35.68 and rec_w["meat_weights"]["beef"]["spit_kg"] == 7.7 and "closing" in rec_w["meat_weights"]["chicken"] and "closing" in rec_w["meat_weights"]["beef"], str(rec_w.get("meat_weights")))
r_w = S.build_sql(real, rec_w, empty_backup(), today="2026-10-08")
check("and that approval gives the complete meat set for the empty app: 9 + 2 spits + 2 closing weights (+ received where the fridge agrees)", r_w["status"] == "sql_ready" and sum(o["table"] == "spit_loads" for o in r_w["ops"]) == 2 and sum(o["table"] == "meat_closing_weights" for o in r_w["ops"]) == 2, str(r_w["reasons"]) + str([o["table"] for o in r_w["ops"]]))
check("the approve command refuses a blocker clear without a reason", S.main(["approve", dpath, "--by", "Demo User", "--clear-blocker", "1"]) == 2)

# ── 15. it writes text only ────────────────────────────────────────────────────────────────────────────────────────────────────────
code = "\n".join(l for l in inspect.getsource(S).splitlines() if not l.lstrip().startswith("#"))
check("it never connects to anything: no http, socket, supabase, psycopg or sqlite3 import", not re.search(r"(?m)^\s*(import|from)\s+(requests|httpx|socket|urllib|sqlite3|psycopg|supabase)", code))
check("it never reads or writes a credential or key", not re.search(r"(?i)service_role|secret_key|api[_-]?key|SUPABASE_", code))
check("the only files it ever opens for writing are the three outputs (one loop) and the approval record", len(re.findall(r"open\([^)]*, \"w\"", code)) == 2 and code.count("open(p, \"w\"") == 1 and code.count("open(path, \"w\"") == 1)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
