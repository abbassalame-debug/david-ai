"""
restaurant_corrections.py -- Job 1: the manager's corrections to what David read, and the list of things he has to settle before SQL is made.

Everything David reads is shown to the manager as an item he can CONFIRM or CORRECT (cash figures, each expense, deliveries, each meat figure, each warning, each item
line David is not sure of). A correction never edits a saved draft: it is applied to a COPY (corrected_draft.json), the cash is recomputed by the same code, and the plan
lists every change as "read -> corrected". Pure functions, no files, no network.

overrides = {
  "cash":     {"old_cash": 805.16, "pos_sales": 899.5, "wallet": 13, "transfer_to_admin": 703.72, "actual_cash_count": 925, "counted_usd": 225},
  "expenses": [{"label": "veg.", "amount": 62.55, "safe": false}, ...]            # the WHOLE list when present
  "meat":     {"chicken": {"received_kg", "spit_kg", "sold_kg", "waste_kg", "closing": {"trim_kg", "main_kg", "tallow_kg"}}, "beef": {...}},
  "lines":    {"<line number>": {"item": "C20", "qty": 10}}                          # held item lines (feeds learning, not the SQL)
}
"""
import copy
import math

import restaurant_tools as RT

CASH_FIELDS = ("old_cash", "pos_sales", "wallet", "transfer_to_admin", "actual_cash_count", "counted_usd")
CASH_LABELS = {"old_cash": "Opening cash", "pos_sales": "Sales", "wallet": "Wallet", "transfer_to_admin": "Transfer to admin", "actual_cash_count": "Cash counted (New)", "counted_usd": "USD notes in the registers"}
MEAT_KEYS = ("received_kg", "spit_kg", "sold_kg", "waste_kg")
MEAT_LABELS = {"received_kg": "received", "spit_kg": "used (spits)", "sold_kg": "sold", "waste_kg": "waste"}
CLOSING_KEYS = ("trim_kg", "main_kg", "tallow_kg")
MEATS = ("chicken", "beef")
MAX_EXPENSES = 40


class Invalid(ValueError):
    pass


def _num(v, name, lo=0, hi=1_000_000):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Invalid(f"{name} must be a number")
    if not lo <= v <= hi:
        raise Invalid(f"{name} is outside {lo} to {hi}")
    return float(v)


def validate(ov):
    """The overrides with every value checked; raises Invalid with a sentence for the first problem."""
    if ov in (None, {}):
        return {}
    if not isinstance(ov, dict):
        raise Invalid("corrections must be an object")
    out = {}
    if "cash" in ov:
        if not isinstance(ov["cash"], dict) or set(ov["cash"]) - set(CASH_FIELDS):
            raise Invalid("cash corrections may only name: " + ", ".join(CASH_FIELDS))
        out["cash"] = {k: _num(v, CASH_LABELS[k]) for k, v in ov["cash"].items() if v is not None}
    if "expenses" in ov:
        rows = ov["expenses"]
        if not isinstance(rows, list) or len(rows) > MAX_EXPENSES:
            raise Invalid(f"expenses must be a list of at most {MAX_EXPENSES} rows")
        out["expenses"] = []
        for i, r in enumerate(rows, 1):
            if not isinstance(r, dict) or not isinstance(r.get("label"), str) or not r["label"].strip() or len(r["label"]) > 80:
                raise Invalid(f"expense {i} needs a name of up to 80 characters")
            out["expenses"].append({"label": r["label"].strip(), "amount": round(_num(r.get("amount"), f"expense {i} amount"), 2), "safe": bool(r.get("safe"))})
    if "meat" in ov:
        if not isinstance(ov["meat"], dict) or set(ov["meat"]) - set(MEATS):
            raise Invalid("meat corrections are for chicken and beef")
        out["meat"] = {}
        for m, v in ov["meat"].items():
            if not isinstance(v, dict) or set(v) - set(MEAT_KEYS) - {"closing"}:
                raise Invalid(f"{m}: unknown meat figure")
            o = {k: _num(v[k], f"{m} {MEAT_LABELS[k]}") for k in MEAT_KEYS if v.get(k) is not None}
            if v.get("closing") is not None:
                c = v["closing"]
                if not isinstance(c, dict) or set(c) - set(CLOSING_KEYS):
                    raise Invalid(f"{m}: closing weights are trim_kg, main_kg and tallow_kg")
                o["closing"] = {k: _num(c[k], f"{m} closing {k}") for k in CLOSING_KEYS if c.get(k) is not None}
            out["meat"][m] = o
    if "lines" in ov:
        if not isinstance(ov["lines"], dict) or len(ov["lines"]) > 200:
            raise Invalid("line corrections must be an object keyed by line number")
        out["lines"] = {}
        for k, v in ov["lines"].items():
            if not str(k).isdigit() or not isinstance(v, dict):
                raise Invalid("a line correction is {item, qty} under its line number")
            e = {}
            if v.get("item") is not None:
                if not isinstance(v["item"], str) or not v["item"].strip() or len(v["item"]) > 12:
                    raise Invalid(f"line {k}: the item must be a menu id")
                e["item"] = v["item"].strip()
            if v.get("qty") is not None:
                e["qty"] = _num(v["qty"], f"line {k} quantity")
            out["lines"][str(k)] = e
    return out


def _fmt(v):
    return "nothing" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)


def apply_cash(draft, ov):
    """(corrected copy of the draft, [changes as sentences]). Only cash figures and the expense list change; Expected / counted / difference are recomputed by the code the
    pipeline uses (reconcile_cash), so the draft stays internally consistent and the validator accepts it."""
    ov = validate(ov)
    if not ov.get("cash") and "expenses" not in ov:
        return draft, []
    d = copy.deepcopy(draft)
    c, changes = d["cash"], []
    for k, v in (ov.get("cash") or {}).items():
        if c.get(k) is None or abs(float(c[k]) - v) > 1e-9:
            changes.append(f"{CASH_LABELS[k]}: {_fmt(c.get(k))} (read) -> {_fmt(v)} (you)")
            c[k] = v
    if "expenses" in ov:
        old = [(e["label"], e["amount"]) for e in c.get("expenses") or []]
        new = [(e["label"], e["amount"]) for e in ov["expenses"]]
        if old != new:
            changes.append("Expenses: " + ", ".join(f"{l} {a:g}" for l, a in old) + " (read) -> " + ", ".join(f"{l} {a:g}" for l, a in new) + " (you)")
        c["expenses"] = [{"label": e["label"], "amount": e["amount"]} for e in ov["expenses"]]
    if not changes:
        return draft, []
    rate = d.get("rate_lbp_per_usd")
    res = RT.reconcile_cash(carried_cash=c.get("old_cash"), pos_sales=c.get("pos_sales"), wallet=c.get("wallet"), expenses=[e["amount"] for e in c["expenses"]],
                            transfer_to_admin=c.get("transfer_to_admin"), actual_cash_count=c.get("actual_cash_count"), counted_usd=c.get("counted_usd"),
                            rate_lbp_per_usd=rate, branch_rate_lbp_per_usd=rate)
    d["cfo_results"]["cash"] = res
    comp = d["computed"]
    for k, src in (("expected", "expected_usd"), ("counted", "counted_usd"), ("diff", "variance_usd")):
        if res.get("ok") and res.get(src) is not None:
            comp[k] = res[src]
        else:
            comp.pop(k, None)
    d.setdefault("flags", []).append("NOTE: the manager corrected figures David had read (" + "; ".join(changes) + "); Expected and the difference were recomputed")
    return d, changes


def split_meat(ov, weights):
    """(meat_weights for the approval, meat_figures for the approval, [changes]). The workbook's weights are the base; the manager's corrections win."""
    ov = validate(ov)
    w = copy.deepcopy(weights or {})
    figures, changes = {}, []
    for m, v in (ov.get("meat") or {}).items():
        for k in ("received_kg", "spit_kg"):
            if k in v:
                old = (w.get(m) or {}).get(k)
                if old is None or abs(old - v[k]) > 1e-9:
                    changes.append(f"{m} {MEAT_LABELS[k]}: {_fmt(old)} (read) -> {_fmt(v[k])} (you)")
                w.setdefault(m, {})[k] = v[k]
        for k in ("sold_kg", "waste_kg"):
            if k in v:
                figures.setdefault(m, {})[k] = v[k]
        if "closing" in v:
            cur = (w.get(m) or {}).get("closing") or {"trim_kg": 0.0, "main_kg": 0.0, "tallow_kg": 0.0}
            new = {**cur, **v["closing"]}
            if new != cur:
                changes.append(f"{m} closing weights: {cur['trim_kg']:g} / {cur['main_kg']:g} / {cur['tallow_kg']:g} (read) -> {new['trim_kg']:g} / {new['main_kg']:g} / {new['tallow_kg']:g} (you)")
            w.setdefault(m, {})["closing"] = new
    return w, figures, changes


def required_items(q):
    """[(key, label)]: everything the manager has to confirm or correct before SQL is made, from the saved questions. A key is settled when it is confirmed or corrected."""
    items = [(f"cash.{k}", CASH_LABELS[k]) for k in ("old_cash", "pos_sales", "wallet", "transfer_to_admin", "actual_cash_count")]
    items += [(f"expense.{i}", f"Expense: {e['label']} {e['amount']:g}") for i, e in enumerate(q.get("expenses") or [])]
    for m in MEATS:
        if (q.get("meat") or {}).get(m) is not None:
            items += [(f"meat.{m}.{k}", f"{m.capitalize()} {MEAT_LABELS[k]}") for k in MEAT_KEYS]
            if (q["meat"][m] or {}).get("closing"):
                items.append((f"meat.{m}.closing", f"{m.capitalize()} closing weights"))
    items += [(f"check.{i}", "Warning: " + c.replace("CHECK: ", "")[:100]) for i, c in enumerate(q.get("checks") or [])]
    items += [(f"line.{h['line']}", f"Item line {h['line']}: {h['name']}") for h in q.get("held_lines") or []]
    return items


def unsettled(q, confirmed, ov):
    """The labels of the required items that are neither confirmed nor corrected."""
    ov = validate(ov)
    out = []
    for key, label in required_items(q):
        if (confirmed or {}).get(key) is True:
            continue
        kind = key.split(".")
        if kind[0] == "cash" and kind[1] in (ov.get("cash") or {}):
            continue
        if kind[0] == "expense" and "expenses" in ov:
            i = int(kind[1])
            read = (q.get("expenses") or [])[i] if i < len(q.get("expenses") or []) else None
            row = ov["expenses"][i] if i < len(ov["expenses"]) else None
            if row is not None and read is not None and (row["label"] != read["label"] or abs(row["amount"] - read["amount"]) > 1e-9):
                continue
        if kind[0] == "meat" and kind[1] in (ov.get("meat") or {}):
            m = ov["meat"][kind[1]]
            if (kind[2] == "closing" and "closing" in m) or kind[2] in m:
                continue
        if kind[0] == "line" and kind[1] in (ov.get("lines") or {}):
            continue
        out.append(label)
    return out
