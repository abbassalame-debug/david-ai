"""
restaurant_sql.py -- Job 1, stage 4: turns an APPROVED draft package into two plain text files, `apply.sql` and `undo.sql`.

Nothing here connects to Supabase, ever. The generator reads the draft, the approval record and the newest `app\\backup\\<date>\\*.json`
export (read-only), and writes text. The manager reads the files and runs them himself in the SQL editor (the same way he ran migration 0031).

    py restaurant_sql.py DRAFT.json --approval APPROVAL.json [--backup DIR] [--out DIR] [--parts cash,meat]
    py restaurant_sql.py approve DRAFT.json --by "Demo User" [--ack-checks] [--clear-blocker N --reason "..."] [--weights-from-workbook | --weights-json FILE] [--allow-update TABLE] [--paid-from-safe LABEL]

Rules (from the app's schema report, contracts/sql_mapping.md):
  * No approval, or an approval for another draft or a draft that was edited afterwards, or an unacknowledged CHECK / BLOCKER flag: no SQL.
  * An open day (someone is working in the app) is never written. An ended day is allowed (the app's lock is screen-only).
  * Row ids are fixed (stable hashes), inserts are `on conflict (id) do nothing`, fixes are `update ... where id = ... and <col> = <old>`.
  * Raw used (spits), received and closing weights come ONLY from the weights workbook (the manager's rule: the workbook wins over the company
    ledger the draft reads). They are written only when the approval record carries them (`meat_weights`); otherwise they are listed as not generated.
  * Changing something the app already holds needs the approval to say so (`allow_updates`: the table names). Inserts into an empty day need nothing more.
  * Existing rows are never deleted or rebuilt: a day that already has different expenses, several spit rows that do not add up, etc. is
    reported as NEEDS HUMAN and NO SQL is produced (all or nothing). `days` rows are never deleted.
  * Every number is pre-rounded with the app's JavaScript rule (halves toward +infinity) and written from a Decimal, every text is quoted.
"""
from david_config import DAVID_DATA
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

import menu_tools
import restaurant_tools as RT
import restaurant_weights as RW

BRANCH_ID = "00000000-0000-0000-0000-000000000001"
# people.id of the managers who end a day in the app (days.ended_by / closed_by); the approval's approved_by picks one. An unknown name leaves them empty and says so.
MANAGER_PEOPLE = {n: "00000000-0000-4000-8000-0000000000a1" for n in ("demo user", "demo manager")}
OUT_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant_sql")
KILO_ITEM = {
    "chicken": {"menu_item_id": "c1c1c1c1-c1c1-4000-8000-0000000000c1", "chicken_g": 1000, "beef_g": 0},
    "beef": {"menu_item_id": "c2c2c2c2-c2c2-4000-8000-0000000000c2", "chicken_g": 0, "beef_g": 1000},
}
MEATS = ("chicken", "beef")
NEXT_FLOAT = Decimal("211.11")
CHAIN_OK = Decimal("0.05")
APPROVAL_SCHEMA = "sql_approval/v1"
TABLES = ("days", "cash_sheets", "cash_expenses", "cash_transfers", "spit_loads", "waste_log", "consumption", "meat_receipts", "meat_closing_weights")
SHEET_COMPARE = ("sales_usd", "count_wallet_usd", "safe_old_usd", "safe_counted_usd", "transfer_to_admin_usd", "value_usd", "expected_usd", "safe_new_usd", "diff_usd", "closed")
SHEET_INSERT = ("id", "day_id", "sales_usd", "count_cash_usd", "count_cash_lbp", "count_wallet_usd", "rate_used", "safe_old_usd", "safe_counted_usd", "next_float_usd",
                "float_open_ok", "float_close_ok", "transfer_to_admin_usd", "value_usd", "expected_usd", "safe_new_usd", "diff_usd", "over_short_usd", "closed", "note")
SCALE = {"sales_usd": 2, "count_wallet_usd": 2, "safe_old_usd": 2, "safe_counted_usd": 2, "transfer_to_admin_usd": 2, "value_usd": 2, "expected_usd": 2, "safe_new_usd": 2,
         "diff_usd": 2, "amount_usd": 2, "weight_kg": 3, "received_kg": 3, "grams": 2, "qty": 2, "trim_kg": 3, "main_kg": 3, "tallow_kg": 3}
DEFAULT_PARTS = ("cash", "meat")


class Refuse(Exception):
    pass


# ── ids, literals, numbers ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def stable_id(seed):
    """The app's stableId (src/lib/day.ts): FNV-1a spread over 32 hex, uuid-shaped. Checked against every id in the real backup."""
    h, out = 0x811C9DC5, []
    for i in range(32):
        for j, ch in enumerate(seed):
            h = (h ^ (ord(ch) + i * 131 + j)) & 0xFFFFFFFF
            h = (h * 0x01000193) & 0xFFFFFFFF
        out.append((h >> ((i % 4) * 8)) & 0xFF)
    hx = "".join(f"{b:02x}" for b in out)[:32]
    return f"{hx[:8]}-{hx[8:12]}-{hx[12:16]}-{hx[16:20]}-{hx[20:32]}"


def consumption_id(day_id, menu_item_id):
    """src/pages/meat/meat.ts consumptionId: 20 hex of the day id + 12 hex of the menu item id."""
    hx = day_id.replace("-", "")[:20] + menu_item_id.replace("-", "")[:12]
    return f"{hx[:8]}-{hx[8:12]}-{hx[12:16]}-{hx[16:20]}-{hx[20:32]}"


def own_id(day_id, kind, key):
    return stable_id(f"david:{kind}:{day_id}:{key}")


def dec(value, name="value"):
    if value is None or isinstance(value, bool):
        raise Refuse(f"{name} is missing or not a number")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise Refuse(f"{name} is not a number: {value!r}")
    if not d.is_finite() or abs(d) >= Decimal("1e9"):
        raise Refuse(f"{name} is not a sensible number: {value!r}")
    return d


def rnd(d, places):
    return RT.rnd(d, places)


def maybe_dec(value):
    if value is None:
        return None
    return Decimal(str(value))


class Raw(str):
    """A piece of SQL written as it is (for example now())."""


def lit(v):
    """A SQL literal. Numbers come from Decimals, text is quoted with doubled quotes, control characters are dropped."""
    if v is None:
        return "null"
    if isinstance(v, Raw):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, Decimal)):
        d = Decimal(v)
        if not d.is_finite():
            raise Refuse("a non-finite number reached the SQL writer")
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    if isinstance(v, str):
        s = re.sub(r"[\x00-\x1f\x7f  ]+", " ", v).strip()
        return "'" + s.replace("'", "''") + "'"
    raise Refuse(f"cannot write a {type(v).__name__} into SQL")


def same(a, b, places):
    if a is None or b is None:
        return a is None and b is None
    return rnd(Decimal(str(a)), places) == rnd(Decimal(str(b)), places)


def draft_sha(draft):
    return hashlib.sha256(json.dumps(draft, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── the app's backup (read-only) ────────────────────────────────────────────────────────────────────────────────────────────────────

def latest_backup(restaurant_root=None):
    base = os.path.join(restaurant_root or menu_tools.RESTAURANT_ROOT, "app", "backup")
    days = sorted(d for d in os.listdir(base) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and os.path.isfile(os.path.join(base, d, "days.json")))
    if not days:
        raise FileNotFoundError(f"no app backup under {base}")
    return os.path.join(base, days[-1])


def load_backup(backup):
    if isinstance(backup, dict):
        return {t: list(backup.get(t) or []) for t in TABLES} | {"_name": backup.get("_name", "(in memory)")}
    out = {"_name": backup}
    for t in TABLES:
        p = os.path.join(backup, t + ".json")
        with open(p, encoding="utf-8") as f:
            out[t] = json.load(f)
    return out


# ── the gates ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _flags(draft, prefix):
    return [f for f in draft.get("flags") or [] if isinstance(f, str) and f.startswith(prefix)]


def check_approval(draft, approval):
    """The human gate. Returns a list of reasons the draft may NOT be turned into SQL (empty = approved)."""
    if not isinstance(approval, dict):
        return ["there is no approval record: the manager has not approved this draft"]
    out = []
    if approval.get("schema") != APPROVAL_SCHEMA:
        out.append(f"the approval record is not {APPROVAL_SCHEMA}")
    if approval.get("draft_id") != draft.get("draft_id"):
        out.append(f"the approval is for {approval.get('draft_id')!r}, not for {draft.get('draft_id')!r}")
    if approval.get("draft_sha256") != draft_sha(draft):
        out.append("the draft was changed after it was approved (its fingerprint no longer matches the approval)")
    if not str(approval.get("approved_by") or "").strip() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(approval.get("approved_on") or "")):
        out.append("the approval needs who approved it and the date")
    acked = set(approval.get("acknowledged_checks") or [])
    for f in _flags(draft, "CHECK:"):
        if f not in acked:
            out.append(f"a CHECK flag was not acknowledged by the manager: {f[:140]}")
    cleared = {c.get("flag"): c.get("reason") for c in approval.get("cleared_blockers") or [] if isinstance(c, dict)}
    for f in _flags(draft, "BLOCKER:"):
        if not str(cleared.get(f) or "").strip():
            out.append(f"a BLOCKER flag was not cleared with a reason: {f[:140]}")
    return out


def make_approval(draft, by, today=None, ack_checks=False, clear_blockers=None, meat_weights=None, allow_updates=None, paid_from=None, meat_figures=None, corrections=None, manager_notes=None):
    """The record the manager's yes becomes. ack_checks acknowledges every CHECK flag of this exact draft; blockers are cleared one by one."""
    blockers = _flags(draft, "BLOCKER:")
    cleared = []
    for n, reason in (clear_blockers or {}).items():
        if not 1 <= n <= len(blockers):
            raise Refuse(f"there is no blocker number {n} (this draft has {len(blockers)})")
        cleared.append({"flag": blockers[n - 1], "reason": reason})
    rec = {"schema": APPROVAL_SCHEMA, "draft_id": draft.get("draft_id"), "draft_sha256": draft_sha(draft), "approved_by": by,
           "approved_on": today or datetime.date.today().isoformat(),
           "acknowledged_checks": _flags(draft, "CHECK:") if ack_checks else [], "cleared_blockers": cleared}
    if meat_weights:
        rec["meat_weights"] = meat_weights
    if allow_updates:
        rec["allow_updates"] = sorted(set(allow_updates))
    if paid_from:
        rec["paid_from"] = dict(paid_from)
    if meat_figures:
        rec["meat_figures"] = meat_figures
    if corrections:
        rec["corrections"] = list(corrections)
    if manager_notes:
        rec["manager_notes"] = list(manager_notes)
    return rec


# ── the figures the draft carries ───────────────────────────────────────────────────────────────────────────────────────────────────

def cash_figures(draft):
    c = draft.get("cash") or {}
    comp = draft.get("computed") or {}
    old, sales, wallet, transfer = (dec(c.get(k), "cash." + k) for k in ("old_cash", "pos_sales", "wallet", "transfer_to_admin"))
    new = dec(c.get("actual_cash_count"), "cash.actual_cash_count (the app's New: all cash counted)")
    exp_rows = []
    for e in c.get("expenses") or []:
        if not isinstance(e, dict) or not str(e.get("label") or "").strip():
            raise Refuse("an expense row has no label")
        exp_rows.append((str(e["label"]).strip(), rnd(dec(e.get("amount"), "expense amount"), 2)))
    expenses = sum((a for _, a in exp_rows), Decimal(0))
    expected = rnd(rnd(old, 2) + rnd(sales, 2) - rnd(wallet, 2) - rnd(expenses, 2) - rnd(transfer, 2), 2)
    diff = rnd(rnd(new, 2) - expected, 2)
    if comp.get("expected") is not None and rnd(dec(comp["expected"]), 2) != expected:
        raise Refuse(f"the draft says Expected {comp['expected']} but old + sales - wallet - expenses - transfer is {expected}")
    if comp.get("diff") is not None and rnd(dec(comp["diff"]), 2) != diff:
        raise Refuse(f"the draft says Difference {comp['diff']} but New - Expected is {diff}")
    value = c.get("counted_usd")
    return {"old": rnd(old, 2), "sales": rnd(sales, 2), "wallet": rnd(wallet, 2), "transfer": rnd(transfer, 2), "new": rnd(new, 2), "expected": expected, "diff": diff,
            "value": None if value is None else rnd(dec(value), 2), "expenses": exp_rows}


def meat_figures(draft, approval=None):
    """Sold and waste per meat from the draft; the manager's corrected figures (approval["meat_figures"], kg) win."""
    res = (draft.get("cfo_results") or {}).get("meat") or {}
    fix = (approval or {}).get("meat_figures") or {}
    out = {}
    for m in MEATS:
        r = res.get(m)
        if not isinstance(r, dict) or not r.get("ok"):
            raise Refuse(f"the {m} meat check did not run (its starting, received or ending weight is missing)")
        inp = r.get("inputs") or {}
        used = dec(r.get("raw_used_kg"), f"{m} raw used")
        added = dec(inp.get("added_kg"), f"{m} received")
        if abs((dec(inp.get("starting_kg"), f"{m} starting") + added - dec(inp.get("ending_kg"), f"{m} ending")) - used) > Decimal("0.0015"):
            raise Refuse(f"{m}: raw used does not equal starting + received - ending")
        sold = fix.get(m, {}).get("sold_kg", r.get("pos_sold_kg"))
        waste = fix.get(m, {}).get("waste_kg", r.get("waste_kg"))
        out[m] = {"used": rnd(used, 3), "received": rnd(added, 3), "sold": rnd(dec(sold, f"{m} sold"), 2), "waste_g": rnd(dec(waste, f"{m} waste") * 1000, 2)}
    return out


def paid_from_map(approval, expense_rows):
    """{label: 'register' | 'safe'} the manager gave for expenses paid from the safe (the draft cannot tell; the default is the register). Checked against the draft's labels."""
    out = {}
    labels = {label for label, _ in expense_rows}
    for label, where in ((approval or {}).get("paid_from") or {}).items():
        if where not in ("register", "safe"):
            raise Refuse(f"paid_from for {label!r} must be register or safe, not {where!r}")
        if label not in labels:
            raise Refuse(f"paid_from names an expense {label!r} that is not in the draft ({sorted(labels)})")
        out[label] = where
    return out


def weights_figures(approval):
    """The weights workbook's figures the manager put in the approval: {meat: {spit_kg, received_kg, closing: {trim_kg, main_kg, tallow_kg}}}, every part optional."""
    out = {}
    for m, v in ((approval or {}).get("meat_weights") or {}).items():
        if m not in MEATS or not isinstance(v, dict):
            raise Refuse(f"meat_weights: {m!r} is not chicken or beef")
        o = {}
        for k in ("spit_kg", "received_kg"):
            if v.get(k) is not None:
                o[k] = rnd(dec(v[k], f"{m} {k}"), 3)
        if v.get("closing") is not None:
            c = v["closing"]
            o["closing"] = {k: rnd(dec(c.get(k, 0), f"{m} closing {k}"), 3) for k in ("trim_kg", "main_kg", "tallow_kg")}
        out[m] = o
    return out


# ── the plan: what has to change, row by row ────────────────────────────────────────────────────────────────────────────────────────

class Plan:
    def __init__(self, day, day_id):
        self.day, self.day_id = day, day_id
        self.ops, self.human, self.info = [], [], []

    def insert(self, table, row, note):
        self.ops.append({"kind": "insert", "table": table, "row": row, "id": row["id"], "note": note})

    def update(self, table, row_id, changes, note, key="id"):
        """changes = {column: (old, new)}; old values guard the update, so it only touches a row that still looks like the backup."""
        self.ops.append({"kind": "update", "table": table, "id": row_id, "changes": changes, "note": note, "key": key})


def _rows(backup, table, day_id):
    return [r for r in backup[table] if r.get("day_id") == day_id]


def _grouped(plan, backup, table, column, meat, desired, new_row, note, label):
    """One value per meat: no row -> insert, one row -> update if different, several rows -> fine only if they add up."""
    rows = [r for r in _rows(backup, table, plan.day_id) if r.get("meat_type") == meat]
    places = SCALE[column]
    if not rows:
        if desired == 0:
            plan.info.append(f"{label} {meat}: 0, nothing to add")
        else:
            plan.insert(table, new_row, f"{label} {meat}: {desired}")
        return
    total = sum((Decimal(str(r.get(column) or 0)) for r in rows), Decimal(0))
    if len(rows) == 1:
        if not same(rows[0].get(column), desired, places):
            plan.update(table, rows[0]["id"], {column: (rnd(Decimal(str(rows[0].get(column) or 0)), places), desired)}, f"{label} {meat}: {rnd(total, places)} -> {desired}")
        return
    if rnd(total, places) != desired:
        plan.human.append(f"{label} {meat}: the app has {len(rows)} rows adding up to {rnd(total, places)}, the draft says {desired}. Fix it in the app (per-spit rows are not rewritten here).")


def build_plan(draft, approval, backup, parts):
    day = draft["business_day"]
    drow = next((d for d in backup["days"] if d.get("on_date") == day and d.get("branch_id", BRANCH_ID) == BRANCH_ID), None)
    if drow and drow.get("status") == "open":
        raise Refuse(f"{day} is an OPEN day in the app (someone may be working in it); it is never written to. End the day in the app first.")
    day_id = drow["id"] if drow else stable_id(f"day:{BRANCH_ID}:{day}")
    plan = Plan(day, day_id)
    if not drow:
        rate = rnd(dec(draft.get("rate_lbp_per_usd") or 90000, "rate"), 2)
        who = MANAGER_PEOPLE.get(str((approval or {}).get("approved_by") or "").strip().lower())
        if not who:
            plan.info.append(f"the approver {(approval or {}).get('approved_by')!r} is not a known person of the app: the day's ended_by / closed_by are left empty")
        plan.insert("days", {"id": day_id, "branch_id": BRANCH_ID, "on_date": day, "status": "ended", "rate_usd_to_lbp": rate, "closed_by": who, "submitted_at": Raw("now()"), "ended_at": Raw("now()"), "ended_by": who},
                    f"the day {day} does not exist in the app yet: created as an ended day, with its end-of-day stamp (ended_at, submitted_at: now; ended_by, closed_by: the approver)")
        plan.days_new = True
    else:
        plan.days_new = False
    if "cash" in parts:
        _plan_cash(plan, draft, backup, approval)
    if "meat" in parts:
        _plan_meat(plan, draft, approval, backup)
    allowed = set((approval or {}).get("allow_updates") or [])
    for op in plan.ops:
        if op["kind"] == "update" and op["table"] not in allowed:
            plan.human.append(f"this would change something the app already holds ({op['table']}: {op['note']}), and the approval does not allow updating {op['table']}. Approve it with --allow-update {op['table']} if you really want that.")
    return plan


def _plan_cash(plan, draft, backup, approval=None):
    f = cash_figures(draft)
    where = paid_from_map(approval, f["expenses"])
    rate = rnd(dec(draft.get("rate_lbp_per_usd") or 90000, "rate"), 2)
    want = {"sales_usd": f["sales"], "count_wallet_usd": f["wallet"], "safe_old_usd": f["old"], "safe_counted_usd": f["new"], "transfer_to_admin_usd": f["transfer"],
            "value_usd": f["value"], "expected_usd": f["expected"], "safe_new_usd": f["new"], "diff_usd": f["diff"], "closed": True}
    sheets = _rows(backup, "cash_sheets", plan.day_id)
    if len(sheets) > 1:
        plan.human.append(f"{plan.day}: the app has {len(sheets)} cash sheets for one day")
    elif not sheets:
        sid = stable_id("cash_sheet:" + plan.day_id)
        row = {"id": sid, "day_id": plan.day_id, "sales_usd": f["sales"], "count_cash_usd": 0, "count_cash_lbp": 0, "count_wallet_usd": f["wallet"], "rate_used": rate,
               "safe_old_usd": f["old"], "safe_counted_usd": f["new"], "next_float_usd": NEXT_FLOAT, "float_open_ok": False, "float_close_ok": False,
               "transfer_to_admin_usd": f["transfer"], "value_usd": f["value"], "expected_usd": f["expected"], "safe_new_usd": f["new"], "diff_usd": f["diff"],
               "over_short_usd": None, "closed": True, "note": None}
        plan.insert("cash_sheets", row, f"cash sheet: sales {f['sales']}, wallet {f['wallet']}, transfer {f['transfer']}, New {f['new']}, Expected {f['expected']}, difference {f['diff']}")
        _chain_before(plan, backup, f)
        _chain_after_new(plan, backup, f)
    else:
        s = sheets[0]
        ch = {}
        for col in SHEET_COMPARE:
            if col == "closed":
                if s.get(col) is not True:
                    ch[col] = (bool(s.get(col)), True)
            elif not same(s.get(col), want[col], 2):
                ch[col] = (None if s.get(col) is None else rnd(Decimal(str(s[col])), 2), want[col])
        if ch:
            plan.update("cash_sheets", s["id"], ch, "cash sheet: " + ", ".join(f"{c} {o} -> {n}" for c, (o, n) in ch.items()))
            if "safe_new_usd" in ch:
                _chain_follow(plan, backup, f, ch["safe_new_usd"][0])
    rows = _rows(backup, "cash_transfers", plan.day_id)
    if not rows:
        if f["transfer"] == 0:
            plan.info.append("transfer to admin: 0, no transfer row added")
        else:
            plan.insert("cash_transfers", {"id": own_id(plan.day_id, "transfer", "1"), "day_id": plan.day_id, "amount_usd": f["transfer"], "expenses_usd": 0, "hold_usd": 0, "wallet_usd": 0},
                        f"transfer to admin {f['transfer']}")
    else:
        tot = sum((Decimal(str(r.get("amount_usd") or 0)) for r in rows), Decimal(0))
        if len(rows) == 1 and not same(rows[0].get("amount_usd"), f["transfer"], 2):
            plan.update("cash_transfers", rows[0]["id"], {"amount_usd": (rnd(tot, 2), f["transfer"])}, f"transfer to admin {rnd(tot, 2)} -> {f['transfer']}")
        elif len(rows) > 1 and rnd(tot, 2) != f["transfer"]:
            plan.human.append(f"{plan.day}: the app has {len(rows)} transfer rows adding up to {rnd(tot, 2)}, the draft says {f['transfer']}")
    rows = _rows(backup, "cash_expenses", plan.day_id)
    if not rows:
        for i, (label, amount) in enumerate(f["expenses"], 1):
            plan.insert("cash_expenses", {"id": own_id(plan.day_id, "expense", f"{i}:{label}:{amount}"), "day_id": plan.day_id, "label": label, "amount_usd": amount, "paid_from": where.get(label, "register")},
                        f"expense {label}: {amount}" + (" (paid from the safe)" if where.get(label) == "safe" else ""))
    else:
        have = sorted(rnd(Decimal(str(r.get("amount_usd") or 0)), 2) for r in rows)
        want_amt = sorted(a for _, a in f["expenses"])
        if have != want_amt:
            plan.human.append(f"{plan.day}: the app's expenses are {[str(a) for a in have]} but the draft's are {[str(a) for a in want_amt]}. Existing expense rows are never rewritten here; fix them in the app.")


def _sheet_on(backup, date):
    d = next((x for x in backup["days"] if x.get("on_date") == date), None)
    if not d:
        return None
    return next((s for s in backup["cash_sheets"] if s.get("day_id") == d["id"]), None)


def _chain_before(plan, backup, f):
    prev = _sheet_on(backup, (datetime.date.fromisoformat(plan.day) - datetime.timedelta(days=1)).isoformat())
    if prev is None or prev.get("safe_new_usd") is None:
        plan.info.append("chain: the day before has no finished cash sheet in the backup, so this day's Old could not be checked against its New")
        return
    gap = abs(f["old"] - Decimal(str(prev["safe_new_usd"])))
    if gap == 0:
        return
    if gap <= CHAIN_OK:
        plan.info.append(f"chain: Old {f['old']} differs from the day before's New {prev['safe_new_usd']} by {gap}; kept as written on the sheet")
    else:
        plan.human.append(f"chain: this day's Old is {f['old']} but the day before closed with New {prev['safe_new_usd']} (gap {gap}). One of the two sheets is wrong; settle it before any SQL is made.")


def _chain_after_new(plan, backup, f):
    nxt = _sheet_on(backup, (datetime.date.fromisoformat(plan.day) + datetime.timedelta(days=1)).isoformat())
    if nxt is not None and nxt.get("safe_old_usd") is not None and abs(Decimal(str(nxt["safe_old_usd"])) - f["new"]) > CHAIN_OK:
        plan.human.append(f"chain: the next day's Old is {nxt['safe_old_usd']} but this day's New would be {f['new']}. Settle it before any SQL is made.")


def _chain_follow(plan, backup, f, old_new):
    nxt = _sheet_on(backup, (datetime.date.fromisoformat(plan.day) + datetime.timedelta(days=1)).isoformat())
    if nxt is None or nxt.get("safe_old_usd") is None:
        return
    if abs(Decimal(str(nxt["safe_old_usd"])) - Decimal(str(old_new))) > CHAIN_OK:
        plan.info.append(f"chain: the next day's Old ({nxt['safe_old_usd']}) did not equal this day's old New ({old_new}); left alone")
        return
    nd = next(d for d in backup["days"] if d["id"] == nxt["day_id"])
    exp = sum((Decimal(str(e.get("amount_usd") or 0)) for e in backup["cash_expenses"] if e.get("day_id") == nd["id"]), Decimal(0))
    new_old = f["new"]
    expected = rnd(new_old + Decimal(str(nxt["sales_usd"])) - Decimal(str(nxt["count_wallet_usd"])) - exp - Decimal(str(nxt["transfer_to_admin_usd"])), 2)
    diff = rnd(Decimal(str(nxt["safe_new_usd"])) - expected, 2)
    ch = {}
    for col, val in (("safe_old_usd", new_old), ("expected_usd", expected), ("diff_usd", diff)):
        if not same(nxt.get(col), val, 2):
            ch[col] = (None if nxt.get(col) is None else rnd(Decimal(str(nxt[col])), 2), val)
    if ch:
        plan.update("cash_sheets", nxt["id"], ch, f"chain follow-up on {nd['on_date']}: " + ", ".join(f"{c} {o} -> {n}" for c, (o, n) in ch.items()))


def _plan_meat(plan, draft, approval, backup):
    m = meat_figures(draft, approval)
    weights = weights_figures(approval)
    for meat in MEATS:
        v = m[meat]
        w = weights.get(meat, {})
        for key, col, table, figure, label in (("spit_kg", "weight_kg", "spit_loads", "used", "raw used (spits)"), ("received_kg", "received_kg", "meat_receipts", "received", "received")):
            if key in w:
                row = {"id": own_id(plan.day_id, "spit" if key == "spit_kg" else "receipt", meat), "day_id": plan.day_id, "meat_type": meat, col: w[key]}
                if key == "spit_kg":
                    row["lo_pieces"] = 0
                _grouped(plan, backup, table, col, meat, w[key], row, "", label)
                if abs(w[key] - v[figure]) > Decimal("0.5"):
                    plan.info.append(f"{meat} {label}: the weights workbook says {w[key]}, the company ledger the draft reads says {v[figure]}; the workbook wins")
            else:
                plan.info.append(f"{meat} {label} NOT generated: the draft reads the company ledger ({v[figure]} kg), and the weights workbook overrides it. Put the workbook's figure in the approval record (meat_weights) to write it")
        _grouped(plan, backup, "waste_log", "grams", meat, v["waste_g"],
                 {"id": own_id(plan.day_id, "waste", meat), "day_id": plan.day_id, "meat_type": meat, "grams": v["waste_g"]}, "", "waste (g)")
        k = KILO_ITEM[meat]
        cid = consumption_id(plan.day_id, k["menu_item_id"])
        rows = [r for r in _rows(backup, "consumption", plan.day_id) if r.get("menu_item_id") == k["menu_item_id"]]
        if not rows:
            plan.insert("consumption", {"id": cid, "day_id": plan.day_id, "menu_item_id": k["menu_item_id"], "qty": v["sold"], "chicken_g": k["chicken_g"], "beef_g": k["beef_g"], "sausage_g": 0},
                        f"sold {meat} (kilo line): {v['sold']}")
        elif not same(rows[0].get("qty"), v["sold"], 2):
            plan.update("consumption", rows[0]["id"], {"qty": (rnd(Decimal(str(rows[0].get("qty") or 0)), 2), v["sold"])}, f"sold {meat} (kilo line): {rnd(Decimal(str(rows[0].get('qty') or 0)), 2)} -> {v['sold']}")
    for meat in MEATS:
        w = weights.get(meat, {})
        if "closing" not in w:
            plan.info.append(f"{meat} closing weights (trim / main / tallow) were NOT generated: the draft does not carry them. Enter them in the app's Meat page, or put them in the approval record (meat_weights) and run again")
    for meat, w in weights.items():
        if "closing" not in w:
            continue
        v = w["closing"]
        rows = [r for r in _rows(backup, "meat_closing_weights", plan.day_id) if r.get("meat_type") == meat]
        if not rows:
            plan.insert("meat_closing_weights", {"id": stable_id(f"meat_closing:{plan.day_id}:{meat}"), "day_id": plan.day_id, "meat_type": meat, **v}, f"closing weights {meat}: {v['trim_kg']} / {v['main_kg']} / {v['tallow_kg']}")
        elif len(rows) > 1:
            plan.human.append(f"closing weights {meat}: the app has {len(rows)} rows for this day")
        else:
            ch = {c: (rnd(Decimal(str(rows[0].get(c) or 0)), 3), v[c]) for c in v if not same(rows[0].get(c), v[c], 3)}
            if ch:
                plan.update("meat_closing_weights", rows[0]["id"], ch, f"closing weights {meat}: " + ", ".join(f"{c} {o} -> {n}" for c, (o, n) in ch.items()))


# ── writing the SQL ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _insert_sql(table, rows):
    cols = list(rows[0].keys())
    body = ",\n".join("  (" + ", ".join(lit(r[c]) for c in cols) + ")" for r in rows)
    return f"insert into {table} ({', '.join(cols)}) values\n{body}\non conflict (id) do nothing;" if table != "days" else \
        f"insert into {table} ({', '.join(cols)}) values\n{body}\non conflict (branch_id, on_date) do nothing;"


def _set_where(changes, which, row_id):
    idx = 0 if which == "old" else 1
    new_idx = 1 - idx
    sets = ", ".join(f"{c} = {lit(v[new_idx])}" for c, v in changes.items())
    guard = " and ".join(f"{c} {'is null' if v[idx] is None else '= ' + lit(v[idx])}" for c, v in changes.items())
    return f"update @@T@@ set {sets} where id = {lit(row_id)} and {guard};"


def render(plan):
    apply, undo, verify = [], [], []
    inserts = {}
    for op in plan.ops:
        if op["kind"] == "insert":
            inserts.setdefault(op["table"], []).append(op["row"])
    order = list(TABLES)
    for t in order:
        if t in inserts:
            apply.append(_insert_sql(t, inserts[t]))
    for op in plan.ops:
        if op["kind"] == "update":
            apply.append(_set_where(op["changes"], "old", op["id"]).replace("@@T@@", op["table"]))
    for t in reversed(order):
        for op in plan.ops:
            if op["kind"] == "update" and op["table"] == t:
                undo.append(_set_where(op["changes"], "new", op["id"]).replace("@@T@@", t))
    for t in reversed(order):
        if t == "days" or t not in inserts:
            continue
        ids = ", ".join(lit(r["id"]) for r in inserts[t])
        undo.append(f"delete from {t} where id in ({ids});")
    if "days" in inserts:
        undo.append(f"-- the day row {plan.day} ({plan.day_id}) is left in place on purpose: deleting a day cascades to everything under it.")
    if "days" in inserts:
        d_ = inserts["days"][0]
        verify.append(f"select {lit('day ' + plan.day + ' ended')} as what, count(*) as rows_found, 1 as rows_expected from days where id = {lit(d_['id'])} and status = 'ended' and ended_at is not null")
    for op in plan.ops:
        if op["kind"] == "insert" and op["table"] != "days":
            verify.append(f"select {lit(op['table'] + ' ' + op['id'][:8])} as what, count(*) as rows_found, 1 as rows_expected from {op['table']} where id = {lit(op['id'])}")
        elif op["kind"] == "update":
            for c, (_, new) in op["changes"].items():
                if new is None:
                    continue
                if isinstance(new, bool):
                    verify.append(f"select {lit(op['table'] + ' ' + c + ' ' + op['id'][:8])} as what, count(*) as rows_found, 1 as rows_expected from {op['table']} where id = {lit(op['id'])} and {c} = {lit(new)}")
                else:
                    verify.append(f"select {lit(op['table'] + ' ' + c + ' ' + op['id'][:8])} as what, count(*) as rows_found, 1 as rows_expected from {op['table']} where id = {lit(op['id'])} and round({c}, 3) = {lit(new)}")
    return apply, undo, verify


def build_sql(draft, approval, backup, today=None, parts=DEFAULT_PARTS, validate=True):
    """The whole decision. Returns {"ok", "status": sql_ready|nothing_to_do|refused|needs_human, "reasons", "apply_sql", "undo_sql", "plan", ...}. Never writes."""
    res = {"ok": False, "status": "refused", "reasons": [], "needs_human": [], "info": [], "apply_sql": "", "undo_sql": "", "ops": [], "day": None, "draft_id": None}
    try:
        if not isinstance(draft, dict) or draft.get("schema") != "draft_package/v0":
            raise Refuse("this is not a draft_package/v0")
        res["draft_id"], res["day"] = draft.get("draft_id"), draft.get("business_day")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(res["day"] or "")):
            raise Refuse("the draft has no business day")
        parts = tuple(p for p in parts if p in DEFAULT_PARTS)
        if not parts:
            raise Refuse("nothing to do: parts must include cash and/or meat")
        gate = check_approval(draft, approval)
        if gate:
            res["reasons"] = gate
            return res
        if draft.get("day_status") != "ended":
            raise Refuse(f"the draft says the day is {draft.get('day_status')!r}; only an ended day is written")
        if validate:
            v = menu_tools.validate_draft_package(package=draft)
            if not v.get("valid"):
                raise Refuse("the draft package is not valid: " + "; ".join(str(e) for e in (v.get("errors") or [])[:3]))
        bk = load_backup(backup)
        plan = build_plan(draft, approval, bk, parts)
    except Refuse as e:
        res["reasons"] = [str(e)]
        return res
    res["info"], res["needs_human"] = plan.info, plan.human
    res["ops"] = [{"kind": o["kind"], "table": o["table"], "id": o["id"], "note": o["note"]} for o in plan.ops]
    res["day_id"] = plan.day_id
    if plan.human:
        res["status"] = "needs_human"
        res["reasons"] = ["some figures conflict with what the app already holds; no SQL is made until they are settled"]
        return res
    if not plan.ops:
        res["ok"], res["status"] = True, "nothing_to_do"
        return res
    apply, undo, verify = render(plan)
    today = today or datetime.date.today().isoformat()
    head = (f"-- apply.sql for {draft['draft_id']} ({res['day']}), generated {today} by restaurant_sql.py\n"
            f"-- approved by {approval['approved_by']} on {approval['approved_on']}. Nothing here has been run.\n"
            f"-- Every insert has a fixed id and 'on conflict', every update checks the old value, so running it twice changes nothing the second time.\n"
            f"-- The result table at the end must show rows_found = rows_expected on every line.\n")
    res["apply_sql"] = head + "\nbegin;\n\n" + "\n\n".join(apply) + "\n\ncommit;\n\n" + "\nunion all\n".join(verify) + ";\n"
    res["undo_sql"] = (f"-- undo.sql for {draft['draft_id']} ({res['day']}), generated {today}. Reverts apply.sql: updates only if the new value is still there,\n"
                       f"-- inserted rows are deleted by id. Rows deleted on the server stay in phones' local cache until their next refresh.\n"
                       f"\nbegin;\n\n" + "\n\n".join(undo) + "\n\ncommit;\n")
    res["ok"], res["status"] = True, "sql_ready"
    return res


def plan_markdown(res, approval=None):
    lines = [f"# SQL plan for {res['draft_id']} ({res['day']})", "", f"Status: **{res['status']}**", ""]
    if res["reasons"]:
        lines += ["## Refused / blocked", ""] + [f"- {r}" for r in res["reasons"]] + [""]
    if res["needs_human"]:
        lines += ["## Needs a human (no SQL was made)", ""] + [f"- {r}" for r in res["needs_human"]] + [""]
    if (approval or {}).get("corrections"):
        lines += ["## Corrections you made to what David read", ""] + [f"- {c}" for c in approval["corrections"]] + [""]
    if (approval or {}).get("manager_notes"):
        lines += ["## Your notes", ""] + [f"- {n}" for n in approval["manager_notes"]] + [""]
    if res["ops"]:
        lines += ["## What apply.sql does", ""] + [f"- {o['kind']} {o['table']}: {o['note']}" for o in res["ops"]] + [""]
    if res["info"]:
        lines += ["## Notes", ""] + [f"- {i}" for i in res["info"]] + [""]
    if res["status"] == "sql_ready":
        lines += ["## How to run", "", "1. Read `apply.sql`.", "2. Paste it in the Supabase SQL editor and run it.", "3. The last table must show rows_found = rows_expected on every line.",
                  "4. If anything is wrong, run `undo.sql`.", "5. Export a fresh backup (`node scripts\\export-backup.mjs`) so David can check the result.", ""]
    return "\n".join(lines)


def write_outputs(res, out_dir=None, approval=None):
    out = os.path.join(out_dir or OUT_DIR, res["draft_id"] or "unknown")
    os.makedirs(out, exist_ok=True)
    written = []
    for name, text in (("apply.sql", res["apply_sql"]), ("undo.sql", res["undo_sql"]), ("plan.md", plan_markdown(res, approval))):
        if name.endswith(".sql") and not text:
            continue
        p = os.path.join(out, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append(p)
    return written


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "approve":
        ap = argparse.ArgumentParser(prog="restaurant_sql.py approve")
        ap.add_argument("draft")
        ap.add_argument("--by", required=True)
        ap.add_argument("--ack-checks", action="store_true")
        ap.add_argument("--clear-blocker", action="append", default=[], metavar="N")
        ap.add_argument("--reason", default="")
        ap.add_argument("--weights-from-workbook", action="store_true", help="read spits and closing weights from the weights workbook of the draft's day (received is passed through from the ledger and flagged for the manager to confirm)")
        ap.add_argument("--weights-json", help="the weights workbook figures: {chicken: {spit_kg, received_kg, closing: {trim_kg, main_kg, tallow_kg}}, beef: {...}}")
        ap.add_argument("--allow-update", action="append", default=[], metavar="TABLE")
        ap.add_argument("--paid-from-safe", action="append", default=[], metavar="LABEL", help="an expense label (exactly as in the draft) that was paid from the safe, not the register")
        ap.add_argument("--out")
        a = ap.parse_args(argv[1:])
        draft = json.load(open(a.draft, encoding="utf-8"))
        weights = json.load(open(a.weights_json, encoding="utf-8")) if a.weights_json else None
        if a.weights_from_workbook:
            ledger = {m: (draft.get("cfo_results", {}).get("meat", {}).get(m, {}).get("inputs") or {}).get("added_kg") for m in MEATS}
            waste = {m: (draft.get("cfo_results", {}).get("meat", {}).get(m, {})).get("waste_kg") for m in MEATS}
            weights, notes, flags = RW.meat_weights_for_approval(draft["business_day"], ledger, waste)
            for n in notes:
                print("NOTE:", n)
            for f in flags:
                print("CHECK THIS:", f)
            if weights is None:
                return 1
        clear = {int(n): a.reason for n in a.clear_blocker}
        if clear and not a.reason.strip():
            print("--clear-blocker needs --reason")
            return 2
        rec = make_approval(draft, a.by, ack_checks=a.ack_checks, clear_blockers=clear, meat_weights=weights, allow_updates=a.allow_update, paid_from={l: "safe" for l in a.paid_from_safe})
        path = a.out or os.path.splitext(a.draft)[0] + ".approval.json"
        json.dump(rec, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"approval written: {path}")
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("draft")
    ap.add_argument("--approval", required=True)
    ap.add_argument("--backup")
    ap.add_argument("--out")
    ap.add_argument("--parts", default="cash,meat")
    a = ap.parse_args(argv)
    draft = json.load(open(a.draft, encoding="utf-8"))
    approval = json.load(open(a.approval, encoding="utf-8"))
    res = build_sql(draft, approval, a.backup or latest_backup(), parts=tuple(a.parts.split(",")))
    print(plan_markdown(res, approval))
    if res["status"] == "sql_ready":
        for p in write_outputs(res, a.out, approval):
            print("written:", p)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
