"""
restaurant_tools.py -- deterministic tools for Job 1 (restaurant operations, Demo Bistro).

Language models are unreliable at arithmetic, so everything numeric in the restaurant pipeline is done here, in exact
decimal arithmetic, and the agents only read the results:

  reconcile_cash       the day's cash check           (agent: cfo)
  check_meat_variance  the stock-ledger meat variance (agent: cfo)
  render_review_sheet  the review sheet a manager reads, built from the extraction, the matches and the two checks
                       above                          (agent: analyst)

The rules are the Manager's Day app's own (app/src/lib/money.ts reconcile() and meat.ts stockLedgerRow()), including its
rounding (JavaScript Math.round: halves go toward +infinity). The tools never guess: a missing input gives status
"incomplete" and no number, an impossible input gives "invalid". They never write anything and never touch the network.
"""

from david_config import DAVID_DATA
import json
import math
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, getcontext

getcontext().prec = 40

DRAFTS_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant")          # where the pipeline saves draft packages (planned)
DRAFT_ID = re.compile(r"^DRAFT-\d{4}-\d{2}-\d{2}-\d{3}$")
DEFAULT_THRESHOLDS = {"matched": 100, "suggested": 85, "margin": 5}
COOK_YIELD_DEFAULT = Decimal("0.55")                      # the branch setting (45% shrink)
FOOTER = "Prepared for review. Nothing has been booked or written."

CASH_FORMULA = ("Expected = carried cash + POS sales - Wallet - expenses - transfer to admin. "
                "Counted = the full cash count (registers + safe) if entered, else the register count "
                "(USD notes + L.L / rate). Variance = Counted - Expected (positive = over, negative = short).")
MEAT_FORMULA = ("Raw used = starting + added - ending. Cooked yield = raw used x cook yield (0.55). "
                "Variance = POS sold + waste - cooked yield (positive = loss, the app's convention: more was sold and "
                "wasted than the raw weight used could yield).")

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_INSTRUCTION_LIKE = re.compile(
    r"(?i)\b(ignore|disregard|override|approve[ds]?|mark (the )?day|previous instructions|system prompt|instructions?|"
    r"you must|execute|insert into|drop table|delete from)\b|تجاهل|اعتمد|وافق على|تعليمات")


class BadInput(ValueError):
    pass


# ── number handling ──────────────────────────────────────────────────────────

def to_dec(value, name):
    """Decimal from a number or a string ("1,480.00", Arabic-Indic digits). None / empty means 'not given'."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadInput(f"{name} must be a number, not true/false")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise BadInput(f"{name} is not a finite number")
        d = Decimal(repr(value))
    elif isinstance(value, str):
        s = value.strip().translate(_DIGITS).replace(",", "").replace("$", "").replace(" ", "")
        if not s:
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise BadInput(f"{name} is not a number: {value!r}")
        if not d.is_finite():
            raise BadInput(f"{name} is not a finite number")
    else:
        raise BadInput(f"{name} must be a number")
    return d


def rnd(d, places):
    """Round like JavaScript Math.round(x * 10^p) / 10^p: halves go toward +infinity (the app's round2/r3/r1)."""
    scale = Decimal(10) ** places
    out = ((d * scale) + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR) / scale
    return out.quantize(Decimal(1).scaleb(-places)) + Decimal(0)


def num(d, places=2):
    """JSON number for a Decimal (normalises -0)."""
    f = float(rnd(d, places))
    return 0.0 if f == 0 else f


def money(d, signed=False):
    d = rnd(d, 2)
    if signed:
        return f"{'+' if d > 0 else '−' if d < 0 else ''}{abs(d):,.2f}"
    return f"{d:,.2f}" if d >= 0 else f"−{abs(d):,.2f}"


def flag(code, severity, message):
    return {"code": code, "severity": severity, "message": message}


def _fail(tool, errors, status="invalid", **extra):
    return {"tool": tool, "ok": False, "status": status, "errors": errors, **extra}


# ── 1. reconcile_cash ────────────────────────────────────────────────────────

def reconcile_cash(carried_cash=None, pos_sales=None, wallet=None, expenses=None, expenses_total=None,
                   transfer_to_admin=None, actual_cash_count=None, counted_usd=None, counted_lbp=None,
                   rate_lbp_per_usd=None, branch_rate_lbp_per_usd=None, tolerance_usd=None):
    """The day's cash check. All amounts are USD except counted_lbp (L.L). Returns a dict; never raises.

    status: clean (variance 0, or within the tolerance if one is given) | over | short   (counted - expected)
            incomplete (a required input or the cash count is missing; no number is invented) | invalid (bad input)
    """
    tool = "reconcile_cash"
    try:
        carried, sales, wh = to_dec(carried_cash, "carried_cash"), to_dec(pos_sales, "pos_sales"), to_dec(wallet, "wallet")
        transfer = to_dec(transfer_to_admin, "transfer_to_admin")
        actual, c_usd, c_lbp = to_dec(actual_cash_count, "actual_cash_count"), to_dec(counted_usd, "counted_usd"), to_dec(counted_lbp, "counted_lbp")
        rate, branch_rate, tol = to_dec(rate_lbp_per_usd, "rate_lbp_per_usd"), to_dec(branch_rate_lbp_per_usd, "branch_rate_lbp_per_usd"), to_dec(tolerance_usd, "tolerance_usd")
        items, unlabeled = [], 0
        if expenses is not None:
            if not isinstance(expenses, list):
                raise BadInput("expenses must be a list of {label, amount}; use expenses_total for a single figure")
            for i, e in enumerate(expenses, 1):
                if isinstance(e, dict):
                    amount = to_dec(e.get("amount"), f"expenses[{i}].amount")
                    if amount is None:
                        raise BadInput(f"expenses[{i}] has no amount")
                    if not str(e.get("label") or "").strip():
                        unlabeled += 1
                else:
                    amount = to_dec(e, f"expenses[{i}]")
                    unlabeled += 1
                items.append(amount)
        exp_sum = sum(items, Decimal(0)) if expenses is not None else None
        exp_total = to_dec(expenses_total, "expenses_total")
        if exp_sum is not None and exp_total is not None and rnd(exp_sum, 2) != rnd(exp_total, 2):
            raise BadInput(f"expenses add up to {money(exp_sum)} but expenses_total says {money(exp_total)}")
        expenses_usd = exp_sum if exp_sum is not None else exp_total
    except BadInput as e:
        return _fail(tool, [str(e)])

    negatives = [n for n, v in (("carried_cash", carried), ("pos_sales", sales), ("wallet", wh), ("transfer_to_admin", transfer),
                                ("actual_cash_count", actual), ("counted_usd", c_usd), ("counted_lbp", c_lbp),
                                ("expenses", expenses_usd), ("tolerance_usd", tol)) if v is not None and v < 0]
    negatives += [f"expenses[{i}]" for i, v in enumerate(items, 1) if v < 0]
    if negatives:
        return _fail(tool, [f"negative amounts are not accepted: {', '.join(negatives)}"],
                     flags=[flag("NEGATIVE_INPUT", "BLOCKER", f"negative amount in {', '.join(negatives)}")])
    missing = [n for n, v in (("carried_cash", carried), ("pos_sales", sales), ("wallet", wh),
                              ("expenses (or expenses_total)", expenses_usd), ("transfer_to_admin", transfer)) if v is None]
    if missing:
        return _fail(tool, [f"missing: {', '.join(missing)}. Enter 0 if the amount really was zero; nothing is assumed."],
                     status="incomplete", missing=missing)

    flags = []
    if (c_lbp or 0) > 0 and (rate is None or rate <= 0):
        return _fail(tool, ["rate_lbp_per_usd is needed to convert counted_lbp"],
                     flags=[flag("RATE_MISSING", "BLOCKER", "counted_lbp was given without an exchange rate")])
    lbp_usd = rnd(c_lbp / rate, 2) if (c_lbp or 0) > 0 else Decimal("0.00")
    register_count = rnd((c_usd or Decimal(0)) + lbp_usd, 2)
    if actual is not None and actual > 0:
        counted, basis = rnd(actual, 2), "actual_cash_count"
        if register_count > counted:
            # The full count INCLUDES the registers, so it cannot be smaller. This is what happens when USD notes are put in
            # actual_cash_count while L.L. is counted separately. Computing a "short" from it would be a false result.
            expected_only = rnd(carried + sales - wh - expenses_usd - transfer, 2)
            return _fail(tool, [f"the full cash count ({money(counted)}) is smaller than the register count ({money(register_count)}), "
                                "but the full count includes the registers. Give either one grand total in actual_cash_count, or the "
                                "register counts (counted_usd, counted_lbp, rate) and no actual_cash_count."],
                         flags=[flag("REGISTER_COUNT_EXCEEDS_TOTAL", "BLOCKER",
                                     f"the registers hold {money(register_count)} but the full count is only {money(counted)}")],
                         expected_usd=num(expected_only), register_count_usd=num(register_count), actual_cash_count_usd=num(counted))
    elif register_count > 0:
        counted, basis = register_count, "register_count"
    else:
        counted, basis = None, None

    expected = rnd(carried + sales - wh - expenses_usd - transfer, 2)
    if wh > sales:
        flags.append(flag("WHISH_EXCEEDS_SALES", "BLOCKER", f"Wallet {money(wh)} is more than POS sales {money(sales)}; Wallet is part of sales"))
    if unlabeled:
        flags.append(flag("UNLABELED_EXPENSE", "CHECK", f"{unlabeled} expense(s) have no label"))
    if branch_rate is not None and rate is not None and rate != branch_rate:
        flags.append(flag("RATE_MISMATCH", "CHECK", f"the rate used is {rate:,.0f} but the branch setting is {branch_rate:,.0f}"))

    out = {"tool": tool, "ok": True, "formula": CASH_FORMULA, "expected_usd": num(expected), "counted_basis": basis,
           "register_count_usd": num(register_count), "lbp_counted_in_usd": num(lbp_usd), "flags": flags,
           "inputs": {"carried_cash": num(carried), "pos_sales": num(sales), "wallet": num(wh), "expenses": num(expenses_usd),
                      "transfer_to_admin": num(transfer), "rate_lbp_per_usd": num(rate, 0) if rate is not None else None,
                      "counted_usd_notes": num(c_usd) if c_usd is not None else None, "counted_lbp": num(c_lbp, 0) if c_lbp is not None else None}}
    if counted is None:
        out.update(status="incomplete", counted_usd=None, variance_usd=None, within_tolerance=None,
                   missing=["actual_cash_count (or counted_usd / counted_lbp)"],
                   summary=f"Expected {money(expected)} | nothing counted yet, so no variance")
        return out
    variance = rnd(counted - expected, 2)
    within = None if tol is None else abs(variance) <= tol
    status = "clean" if (variance == 0 or within) else "over" if variance > 0 else "short"
    out.update(status=status, counted_usd=num(counted), variance_usd=num(variance), within_tolerance=within,
               summary=f"Expected {money(expected)} | Counted {money(counted)} | Difference {money(variance, signed=True)}"
                       + ("" if variance == 0 else " (over)" if variance > 0 else " (short)"))
    return out


# ── 2. check_meat_variance ───────────────────────────────────────────────────

# A beef weight is NOT just the meat: the branch's beef stock also holds the fat/tallow (لية) and a rich cut (رتش لحم), and every beef
# figure (opening stock, received, ending) must include all of them. The beef sheets once added up the meat and the rich cut only, which left
# about 3 kg of tallow out of the opening stock and 2 kg a day out of "received". When a weight is given as its parts, the tool adds ALL the
# parts itself, so the tallow cannot be left out of a total; for beef the tallow must be stated (0 is allowed) and the tool never assumes it.
_PART_NAMES = {
    "beef": {"meat": "meat", "لحم": "meat", "rich": "rich", "رتش لحم": "rich", "رتش": "rich", "tallow": "tallow", "fat": "tallow", "لية": "tallow"},
    "chicken": {"chicken": "chicken", "دجاج": "chicken", "rich": "rich", "رتش دجاج": "rich", "رتش": "rich"},
}
_REQUIRED_PARTS = {"beef": ("meat", "tallow"), "chicken": ("chicken",)}


def _weight_from_parts(label, parts, meat, total_given):
    """(total Decimal, {part: Decimal}, [missing part names]) for a weight given as parts. Raises BadInput."""
    if meat not in _PART_NAMES:
        raise BadInput(f"{label}: weights given as parts need meat to be 'beef' or 'chicken'")
    if not isinstance(parts, dict) or not parts:
        raise BadInput(f"{label} must be an object of part weights, for example {{\"meat\": 20, \"rich\": 1.1, \"tallow\": 3}}")
    got = {}
    for k, v in parts.items():
        name = _PART_NAMES[meat].get(str(k).strip().lower() if str(k).isascii() else str(k).strip())
        if name is None:
            raise BadInput(f"{label}: '{k}' is not a part of {meat} (allowed: {', '.join(sorted({n for n in _PART_NAMES[meat].values()}))})")
        d = to_dec(v, f"{label}.{k}")
        if d is None:
            continue
        if d < 0:
            raise BadInput(f"{label}.{k} is negative")
        if name in got:
            raise BadInput(f"{label}: '{name}' is given twice")
        got[name] = d
    missing = [n for n in _REQUIRED_PARTS[meat] if n not in got]
    total = sum(got.values(), Decimal(0))
    if total_given is not None and not missing and abs(total_given - total) > Decimal("0.005"):
        without_tallow = total - got.get("tallow", Decimal(0))
        hint = (f" The total equals the parts WITHOUT the tallow ({without_tallow:.3f} kg): the tallow must be included." if meat == "beef" and abs(total_given - without_tallow) <= Decimal("0.005") and got.get("tallow") else "")
        raise BadInput(f"{label}: the total {total_given:.3f} kg is not the sum of its parts ({total:.3f} kg).{hint}")
    return total, got, missing


def check_meat_variance(starting_kg=None, added_kg=None, pos_sold_kg=None, ending_kg=None, waste_kg=None,
                        cook_yield=None, tolerance_pct=None, meat=None, starting_parts=None, added_parts=None, ending_parts=None):
    """One meat type's daily stock-ledger variance (the app's stockLedgerRow). Weights are kilograms.

    starting_kg: raw stock at the start of the day.   added_kg: raw received during the day.
    ending_kg:   raw stock actually weighed at the end.   pos_sold_kg: cooked weight the POS says was sold.
    starting_parts / added_parts / ending_parts: the same three weights as their parts, {"meat": .., "rich": .., "tallow": ..} for beef
                 ("chicken", "rich" for chicken; Arabic names لحم / رتش لحم / لية / دجاج work too). Beef weights include the tallow:
                 with parts the tool adds every part itself, the tallow must be stated (0 if none), and a total that is given as well
                 must equal the sum of the parts. Without parts a beef result carries a CHECK that the tallow could not be verified.
    waste_kg:    cooked weight wasted (optional; if left out it counts as 0 and a flag says so).
    status: balanced | loss | surplus  (variance = sold + waste - cooked yield; positive = loss)
            incomplete | invalid       (a weight is missing, or the weights cannot balance)

    Rounding is exact decimal arithmetic (halves toward +infinity, like the app). The app computes in JavaScript floats, so on a
    day whose cooked yield lands on an exact half-gram tie (e.g. 236.95 kg x 0.55 = 130.3225) the app can show 0.001 kg less;
    in a 40,000-day comparison that happened on 7 days (checked 2026-10-05), and cash agreed on every day.
    """
    tool = "check_meat_variance"
    components, tallow_kg, parts_missing = {}, {}, []
    try:
        start, added, sold, end = (to_dec(starting_kg, "starting_kg"), to_dec(added_kg, "added_kg"),
                                   to_dec(pos_sold_kg, "pos_sold_kg"), to_dec(ending_kg, "ending_kg"))
        waste, y, tol = to_dec(waste_kg, "waste_kg"), to_dec(cook_yield, "cook_yield"), to_dec(tolerance_pct, "tolerance_pct")
        for label, parts, total in (("starting", starting_parts, start), ("added", added_parts, added), ("ending", ending_parts, end)):
            if parts is None:
                continue
            tot, got, miss = _weight_from_parts(f"{label}_parts", parts, meat, total)
            if miss:
                parts_missing += [f"{label}_parts.{m}" + (" (enter 0 if there was none)" if m == "tallow" else "") for m in miss]
                continue
            components[label] = {**{k: num(v, 3) for k, v in got.items()}, "total": num(tot, 3)}
            if "tallow" in got:
                tallow_kg[label] = num(got["tallow"], 3)
            if label == "starting":
                start = tot
            elif label == "added":
                added = tot
            else:
                end = tot
    except BadInput as e:
        return _fail(tool, [str(e)])
    if parts_missing:
        return _fail(tool, [f"missing: {', '.join(parts_missing)}. The tallow is part of the beef weight and is never assumed."], status="incomplete", missing=parts_missing)
    negatives = [n for n, v in (("starting_kg", start), ("added_kg", added), ("pos_sold_kg", sold), ("ending_kg", end),
                                ("waste_kg", waste), ("tolerance_pct", tol)) if v is not None and v < 0]
    if negatives:
        return _fail(tool, [f"negative weights are not accepted: {', '.join(negatives)}"],
                     flags=[flag("NEGATIVE_INPUT", "BLOCKER", f"negative weight in {', '.join(negatives)}")])
    missing = [n for n, v in (("starting_kg", start), ("added_kg", added), ("pos_sold_kg", sold), ("ending_kg", end)) if v is None]
    if missing:
        return _fail(tool, [f"missing: {', '.join(missing)}. Enter 0 if the weight really was zero; nothing is assumed."],
                     status="incomplete", missing=missing)
    if y is not None and not (0 < y <= 1):
        return _fail(tool, ["cook_yield must be a fraction between 0 and 1 (the branch uses 0.55), not a percentage"])
    cook_yield_used = COOK_YIELD_DEFAULT if y is None else y
    used = rnd(start + added - end, 3)
    if used < 0:
        return _fail(tool, [f"the ending weight ({end} kg) is more than starting + added ({start + added} kg): the weights cannot balance"],
                     flags=[flag("ENDING_EXCEEDS_AVAILABLE", "BLOCKER", "ending weight is more than starting + added")])
    flags = []
    if meat == "beef" and not tallow_kg:
        flags.append(flag("BEEF_TALLOW_UNVERIFIED", "CHECK", "beef weights must include the fat/tallow (لية); these totals were given without their parts, so that cannot be verified"))
    if waste is None:
        flags.append(flag("WASTE_NOT_PROVIDED", "CHECK", "waste was not given and is counted as 0"))
    if y is not None and y != COOK_YIELD_DEFAULT:
        flags.append(flag("YIELD_NOT_STANDARD", "CHECK", f"cook yield {y} differs from the branch default {COOK_YIELD_DEFAULT}"))
    if used == 0:
        flags.append(flag("NOTHING_USED", "CHECK", "no raw meat was used (starting + added = ending)"))
    waste_d = waste or Decimal(0)
    yield_kg = rnd(used * cook_yield_used, 3)
    remain_cooked = rnd(yield_kg - sold - waste_d, 3)
    variance = rnd(-remain_cooked, 3)
    pct = rnd(variance / yield_kg * 100, 1) if yield_kg else Decimal("0.0")
    realized = rnd((sold + waste_d) / used * 100, 1) if used else Decimal("0.0")
    within = None if tol is None else abs(pct) <= tol
    if variance == 0 or (within and yield_kg):
        status = "balanced"
    else:
        status = "loss" if variance > 0 else "surplus"
    sign = "" if variance == 0 else "+" if variance > 0 else "−"
    accounted = sold + waste_d
    meaning = {
        "balanced": f"Sold plus waste ({accounted:.3f} kg) equals what the raw weight used could yield ({yield_kg:.3f} kg).",
        "loss": f"Sold and wasted ({accounted:.3f} kg) exceed what the raw weight used could yield ({yield_kg:.3f} kg) by {abs(variance):.3f} kg. "
                "The app calls this a loss. It is arithmetic, not a cause.",
        "surplus": f"The raw weight used would yield {yield_kg:.3f} kg cooked, {abs(variance):.3f} kg more than was sold and wasted "
                   f"({accounted:.3f} kg): that much cooked meat is unaccounted for. It is arithmetic, not a cause.",
    }[status]
    return {"tool": tool, "ok": True, "meat": meat, "formula": MEAT_FORMULA, "status": status, "meaning": meaning,
            "raw_used_kg": num(used, 3), "cooked_yield_kg": num(yield_kg, 3), "pos_sold_kg": num(sold, 3),
            "waste_kg": num(waste_d, 3), "variance_kg": num(variance, 3), "variance_pct": num(pct, 1),
            "inputs": {"starting_kg": num(start, 3), "added_kg": num(added, 3), "ending_kg": num(end, 3)},
            "components": components, "tallow_kg": tallow_kg,
            "remaining_cooked_kg": num(remain_cooked, 3), "realized_yield_pct": num(realized, 1),
            "cook_yield": float(cook_yield_used), "within_tolerance": within, "flags": flags,
            "summary": f"Raw used {used:.3f} kg | Cooked yield {yield_kg:.3f} kg | Sold {sold:.3f} + waste {waste_d:.3f} | "
                       f"Variance {sign}{abs(variance):.3f} kg ({sign}{abs(pct):.1f}%)"
                       + ("" if variance == 0 else " (loss)" if variance > 0 else " (surplus)")}


# ── 3. render_review_sheet ───────────────────────────────────────────────────

def _cell(text):
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _show(value, alt, has_alt):
    """A value, or BOTH readings when the two readings differ."""
    if value is None and not has_alt:
        return "—"
    if has_alt and alt != value:
        return f"{'—' if value is None else value} or {'—' if alt is None else alt}"
    return "—" if value is None else str(value)


def _fmt_amount(v):
    return f"{rnd(v, 2):,.2f}" if isinstance(v, Decimal) else str(v)


def _classify(line, th, glossary_ids, amounts_printed=True):
    """(status, notes, reasons_for_the_needs_human_list, flags) for one extracted line. Shows only what was provided."""
    n = line.get("line", "?")
    notes, reasons, flags, human = [], [], [], False
    missing = [k for k in ("line", "name_ar", "qty", "amount", "readings_agree") if k not in line and not (k == "amount" and not amounts_printed)]
    if missing:
        human = True
        reasons.append(f"the extraction left out: {', '.join(missing)}")
        flags.append(flag("LINE_FIELD_MISSING", "BLOCKER", f"line {n} is missing {', '.join(missing)}"))
    text = f"{line.get('name_ar') or ''} {line.get('name_ar_alt') or ''}"
    if _INSTRUCTION_LIKE.search(text):
        human = True
        reasons.append("the printed text reads like an instruction; it is treated as data and was not obeyed")
        flags.append(flag("SUSPICIOUS_TEXT", "BLOCKER", f"line {n}: text that looks like an instruction"))
    if line.get("readings_agree") is True and line.get("name_settled_by_menu") is True and line.get("name_ar_alt"):
        notes.append("Other reading spelled it differently; this one is an exact menu name")
    if line.get("readings_agree") is not True:
        human = True
        has_alt = any(f"{k}_alt" in line for k in ("name_ar", "qty", "amount"))
        reasons.append("the two readings disagree" if has_alt else "the two readings disagree and the second reading is missing")
        notes.append("Readings disagree")
        if not has_alt:
            flags.append(flag("DISAGREEMENT_WITHOUT_SECOND_READING", "BLOCKER", f"line {n}: readings disagree but no second reading was supplied"))
    if "qty" in line and line["qty"] is None and "qty_alt" not in line:
        human = True
        reasons.append("the quantity could not be read")
    if amounts_printed and "amount" in line and line["amount"] is None and "amount_alt" not in line:
        human = True
        reasons.append("the amount could not be read")
    m = line.get("match") or {}
    item_id, score = m.get("item_id"), m.get("score")
    cands = sorted([c for c in (m.get("candidates") or []) if isinstance(c.get("score"), (int, float))], key=lambda c: -c["score"])
    status = None
    if item_id is None:
        human = True
        reasons.append("no menu item matched")
    elif not isinstance(score, (int, float)):
        human = True
        reasons.append("the match has no score")
    elif glossary_ids is not None and item_id not in glossary_ids:
        human = True
        reasons.append(f"{item_id} is not in the menu glossary")
        flags.append(flag("MATCH_NOT_IN_GLOSSARY", "BLOCKER", f"line {n}: {item_id} is not in the glossary"))
    else:
        rivals = [c for c in cands if c.get("item_id") != item_id and score - c["score"] <= th["margin"]]
        if rivals:
            human = True
            reasons.append(f"another item scored within {th['margin']} points")
        elif score >= th["matched"]:
            status = "matched"
        elif score >= th["suggested"]:
            status = "suggested"
            notes.append("Confirm the match")
        else:
            human = True
            reasons.append(f"best score {score} is below {th['suggested']}")
    if human:
        status = "needs human"
    shown = []
    if cands and (human or status == "suggested"):
        shown = cands if item_id is None else [c for c in cands if c.get("item_id") != item_id]
    return status, notes, reasons, flags, shown[:3]


def render_review_sheet(draft_id=None, package=None, cash_result=None, meat_results=None, thresholds=None, glossary_ids=None):
    """The review sheet (Markdown) for one draft. Pure formatting plus the line-total sum: it only shows matches it was
    given, always shows BOTH readings when the two readings disagreed, and takes its counts from the lines themselves.

    Give draft_id (a saved package in DRAFTS_DIR; preferred, so nobody retypes a package) or package. cash_result is a
    reconcile_cash result and meat_results is check_meat_variance result(s); both may also sit inside the package under
    "cfo_results". Returns {"ok", "markdown", "counts", "blockers", "needs_human_lines", "flags"}."""
    tool = "render_review_sheet"
    if package is None and draft_id:
        if not DRAFT_ID.match(str(draft_id)):
            return _fail(tool, [f"draft_id must look like DRAFT-2026-09-20-001, not {draft_id!r}"])
        path = os.path.join(DRAFTS_DIR, f"{draft_id}.json")
        if not os.path.exists(path):
            return _fail(tool, [f"no saved package for {draft_id}"])
        with open(path, encoding="utf-8") as f:
            package = json.load(f)
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except ValueError:
            return _fail(tool, ["the package is not valid JSON"])
    if not isinstance(package, dict):
        return _fail(tool, ["give a draft_id or a package"])
    sbi = (package.get("extraction") or {}).get("summary_by_items")
    if not isinstance(sbi, dict) or not isinstance(sbi.get("lines"), list):
        return _fail(tool, ["the package has no extraction.summary_by_items.lines, so there is nothing to review"])

    th = {**DEFAULT_THRESHOLDS, **(package.get("thresholds") or {}), **(thresholds or {})}
    amounts_printed = sbi.get("amounts_printed", True) is not False
    cfo = package.get("cfo_results") or {}
    cash_result = cash_result if cash_result is not None else cfo.get("cash")
    meat_results = meat_results if meat_results is not None else cfo.get("meat")
    day, did = package.get("business_day", "unknown day"), package.get("draft_id", draft_id or "no draft id")
    sources = ", ".join(s.get("file", "?") if isinstance(s, dict) else str(s) for s in package.get("sources") or []) or "none listed"

    rows, human_section, human_lines, flags, questions = [], [], [], [], []
    counts = {"matched": 0, "suggested": 0, "needs human": 0}
    amounts, amount_missing = Decimal(0), 0
    for i, line in enumerate(sbi["lines"], 1):
        n = line.get("line", i)
        status, notes, reasons, lflags, cands = _classify(line, th, glossary_ids, amounts_printed)
        flags += lflags
        m = line.get("match") or {}
        has_alt = {k: f"{k}_alt" in line for k in ("name_ar", "qty", "amount")}
        name = _show(line.get("name_ar"), line.get("name_ar_alt"), has_alt["name_ar"])
        qty = _show(line.get("qty"), line.get("qty_alt"), has_alt["qty"])
        try:
            a, a_alt = to_dec(line.get("amount"), "amount"), to_dec(line.get("amount_alt"), "amount_alt")
        except BadInput:
            a, a_alt = None, None
            reasons.append("the amount is not a number")
            status = "needs human"
        amt = _show(_fmt_amount(a) if a is not None else None, _fmt_amount(a_alt) if a_alt is not None else None, has_alt["amount"])
        if a is None:
            amount_missing += 1 if amounts_printed else 0
        else:
            amounts += a
        counts[status] += 1
        if m.get("item_id") and status in ("matched", "suggested"):
            matched = f"{m['item_id']} {m.get('name_ar', '')}".strip() + (f" (score {m['score']})" if status == "suggested" else "")
        elif m.get("item_id"):
            matched = f"not confirmed: {m['item_id']} {m.get('name_ar', '')}".strip()
        else:
            matched = "none"
        rows.append(f"| {n} | {_cell(name)} | {_cell(matched)} | {_cell(qty)} | {_cell(amt)} | {status} | {_cell('; '.join(notes))} |")
        if status == "needs human":
            human_lines.append(n)
            cand_txt = "; ".join(f"{c.get('item_id')} {c.get('name_ar', '')} ({c['score']})".replace("  ", " ") for c in cands)
            human_section.append(f"- line {n}: {'; '.join(reasons)}" + (f"; candidates: {cand_txt}" if cand_txt else ""))
            if line.get("readings_agree") is not True:
                questions.append(f"Line {n}: which reading is right: name {name}, quantity {qty}, amount {amt}?")
            elif any(f["code"] == "SUSPICIOUS_TEXT" for f in lflags):
                questions.append(f"Line {n} has text that reads like an instruction and was not obeyed: what is it?")
            else:
                questions.append(f"Line {n}: which menu item and quantity is this?")
        elif status == "suggested":
            questions.append(f"Is line {n} {m.get('name_ar') or m.get('item_id')}?")

    printed = to_dec(sbi.get("printed_total"), "printed_total") if sbi.get("printed_total") is not None else None
    sum_lines = amounts if (not amount_missing and amounts_printed) else None
    gap = (sum_lines - printed) if (sum_lines is not None and printed is not None) else None
    if not amounts_printed:
        flags.append(flag("AMOUNTS_NOT_PRINTED", "NOTE", "the POS item list prints quantities only, so the lines cannot be added up against a printed total"))
    if gap is not None and rnd(gap, 2) != 0:
        flags.append(flag("LINE_TOTAL_GAP", "BLOCKER", f"the lines add up to {money(sum_lines)} but the printed total is {money(printed)} (gap {money(gap, signed=True)})"))
        questions.append("The lines do not add up to the printed total: is a line missing or misread?")
    if amount_missing:
        flags.append(flag("AMOUNT_MISSING", "BLOCKER", f"{amount_missing} line(s) have no readable amount, so the lines cannot be totalled"))
    if printed is None and amounts_printed:
        flags.append(flag("PRINTED_TOTAL_MISSING", "CHECK", "no printed total was read"))
    comp = package.get("computed") or {}
    if sum_lines is not None and comp.get("sum_of_lines") is not None and rnd(to_dec(comp["sum_of_lines"], "computed.sum_of_lines"), 2) != rnd(sum_lines, 2):
        flags.append(flag("COMPUTED_BLOCK_DISAGREES", "CHECK", f"the package's computed sum_of_lines ({comp['sum_of_lines']}) differs from the lines ({money(sum_lines)})"))

    for text in package.get("flags") or []:
        if isinstance(text, str) and text.strip():
            sev, _, msg = text.partition(": ")
            flags.append(flag("PIPELINE", sev if sev in ("BLOCKER", "CHECK", "NOTE") and msg else "CHECK", msg if sev in ("BLOCKER", "CHECK", "NOTE") and msg else text))

    # the two checks, shown exactly as the tools returned them (never recomputed here)
    cash_md, meat_md = [], []
    if isinstance(cash_result, dict):
        cash_md = ["## Cash check (from reconcile_cash)"]
        if cash_result.get("ok"):
            cash_md += [f"Status: **{cash_result.get('status')}**", "", "| Reconciliation | USD |", "|---|---|",
                        f"| Expected | {money(Decimal(str(cash_result['expected_usd'])))} |"]
            if cash_result.get("counted_usd") is not None:
                cash_md += [f"| Counted | {money(Decimal(str(cash_result['counted_usd'])))} |",
                            f"| Difference (counted − expected) | {money(Decimal(str(cash_result['variance_usd'])), signed=True)} |"]
            for f in cash_result.get("flags") or []:
                flags.append(dict(f))
            if cash_result.get("status") in ("over", "short"):
                questions.append(f"The cash count is {cash_result['status']} by {money(abs(Decimal(str(cash_result['variance_usd']))))}: is that explained?")
        else:
            cash_md += [f"Not usable: {'; '.join(cash_result.get('errors') or [])} (status {cash_result.get('status')})"]
            flags.append(flag("CASH_CHECK_NOT_USABLE", "BLOCKER", "; ".join(cash_result.get("errors") or ["the cash check did not complete"])))
    else:
        cash_md = ["## Cash check", "Not run: no reconcile_cash result was supplied."]
    if isinstance(meat_results, dict) and meat_results:
        results = [meat_results] if "status" in meat_results else [dict(v, meat=k) for k, v in meat_results.items() if isinstance(v, dict)]
        meat_md = ["## Meat check (from check_meat_variance)"]
        for r in results:
            label = r.get("meat") or "meat"
            if r.get("ok"):
                meat_md.append(f"- {label}: **{r.get('status')}**. {r.get('summary')}")
                for f in r.get("flags") or []:
                    flags.append(dict(f, message=f"{label}: {f.get('message')}"))
            else:
                meat_md.append(f"- {label}: not usable: {'; '.join(r.get('errors') or [])}")
                flags.append(flag("MEAT_CHECK_NOT_USABLE", "BLOCKER", f"{label}: {'; '.join(r.get('errors') or [])}"))

    if not meat_md:
        meat_md = ["## Meat check", "Not run: no check_meat_variance result was supplied."]

    order = {"BLOCKER": 0, "CHECK": 1, "NOTE": 2}
    shown = sorted(flags, key=lambda f: order.get(f.get("severity"), 3))
    summary = [f"- {f['severity']}: {f['message']}" for f in shown]
    if human_lines:
        summary.append(f"- CHECK: {len(human_lines)} line(s) need a human: {', '.join(str(x) for x in human_lines)}")
    if not summary:
        summary = ["- No blockers or checks. Nothing has been approved."]
    human_section = human_section or ["- none"]
    totals = (f"Sum of lines: {money(sum_lines) if sum_lines is not None else 'cannot be totalled'}  |  "
              f"Printed total: {money(printed) if printed is not None else 'not read'}  |  "
              f"Gap: {money(gap, signed=True) if gap is not None else 'n/a'}") if amounts_printed else \
        "Not applicable: the POS item list prints quantities only (no amount per item), so there is no sum of lines to compare with a total."
    md = [f"# Review sheet: {day} ({did})",
          f"Sources: {sources}   Reading: {len(sbi['lines'])} lines, {counts['matched']} matched, {counts['suggested']} suggested, {counts['needs human']} needs human",
          "", "## Summary", *summary, "",
          "## Item lines", "| # | Name as printed | Matched menu item | Qty | Amount | Status | Note |", "|---|-----------------|-------------------|-----|--------|--------|------|", *rows, "",
          "## Needs a human", *human_section, "",
          "## Totals check (computed by code)", totals, "", *cash_md, "", *(meat_md + [""] if meat_md else []),
          "## Questions for the manager", *(f"{i}. {q}" for i, q in enumerate(questions or ["none"], 1)), "", FOOTER]
    blockers = [f["message"] for f in shown if f["severity"] == "BLOCKER"]
    return {"tool": tool, "ok": True, "markdown": "\n".join(md), "counts": {**counts, "lines": len(sbi["lines"])},
            "blockers": blockers, "needs_human_lines": human_lines, "flags": shown}


# ── the two checks as a finished reply (what the HUD shows; no model rewords it) ─────────────────────────────────────────

_WHERE_TODO = {
    "WHISH_EXCEEDS_SALES": ("cash.wallet, cash.pos_sales", "Re-read both figures from the printout."),
    "UNLABELED_EXPENSE": ("cash.expenses", "Add the label."),
    "RATE_MISMATCH": ("rate_lbp_per_usd", "Confirm which rate applies today."),
    "RATE_MISSING": ("rate_lbp_per_usd", "Give the exchange rate used."),
    "NEGATIVE_INPUT": ("inputs", "Correct the figure."),
    "REGISTER_COUNT_EXCEEDS_TOTAL": ("actual_cash_count, counted_usd, counted_lbp", "Give one grand total OR the register counts, not both."),
    "WASTE_NOT_PROVIDED": ("waste_kg", "Give the waste weight, or confirm it was zero."),
    "YIELD_NOT_STANDARD": ("cook_yield", "Confirm the branch setting."),
    "NOTHING_USED": ("weights", "Check the starting and ending weights."),
    "ENDING_EXCEEDS_AVAILABLE": ("ending_kg", "Re-weigh, or re-check the starting and added weights."),
}


def _findings(rows):
    """(status word, Markdown table) from [(severity, what, where, todo)]. blocked > needs attention > clean."""
    order = {"BLOCKER": 0, "CHECK": 1, "NOTE": 2}
    rows = sorted(rows, key=lambda r: order[r[0]])
    status = "blocked" if any(r[0] == "BLOCKER" for r in rows) else "needs attention" if any(r[0] == "CHECK" for r in rows) else "clean"
    table = ["| # | Severity | What | Where | What to do |", "|---|----------|------|-------|------------|"]
    table += [f"| {i} | {sev} | {_cell(what)} | {_cell(where)} | {_cell(todo)} |" for i, (sev, what, where, todo) in enumerate(rows, 1)]
    return status, (table if rows else ["No findings."])


def _flag_rows(r):
    return [(f["severity"], f["message"][0].upper() + f["message"][1:] + ("." if not f["message"].endswith(".") else ""), *_WHERE_TODO.get(f["code"], ("—", "Check it.")))
            for f in r.get("flags") or []]


def _problem_rows(r):
    """Rows for a result that did not complete: what is missing, or why the input is unusable."""
    if r.get("status") == "incomplete":
        return [("BLOCKER", f"Missing: {m}.", "inputs", "Enter the figure (0 if it really was zero). Nothing is assumed.") for m in r.get("missing") or []]
    return [("BLOCKER", e if e.endswith(".") else e + ".", "inputs", "Correct it and run the check again.") for e in r.get("errors") or []]


FOOT = FOOTER


def cash_markdown(r):
    """The CFO's answer format for one reconcile_cash result. Everything comes from the result; nothing is recomputed."""
    rows = _flag_rows(r)
    if not r.get("ok") or r.get("status") == "incomplete":
        rows = _problem_rows(r) + [x for x in rows if x not in _problem_rows(r)]
    elif r["status"] in ("over", "short") or r.get("variance_usd"):
        amount = money(abs(Decimal(str(r["variance_usd"]))))
        way = "over expected" if r["variance_usd"] > 0 else "short of expected"
        if r.get("within_tolerance") is None:
            rows.append(("NOTE", f"Counted cash is ${amount} {way}. No tolerance is configured, so I am not judging it.", "computed.variance", "The manager decides."))
        elif not r["within_tolerance"]:
            rows.append(("CHECK", f"Counted cash is ${amount} {way}, outside the tolerance that was set.", "computed.variance", "The manager decides."))
        else:
            rows.append(("NOTE", f"Counted cash is ${amount} {way}, inside the tolerance that was set.", "computed.variance", "No action needed."))
    status, table = _findings(rows)
    out = [f"Status: {status}", "", *table, ""]
    if r.get("expected_usd") is not None:
        i = r.get("inputs") or {}
        d = lambda k: money(Decimal(str(i[k]))) if i.get(k) is not None else "?"
        out += ["| Reconciliation | USD |", "|---|---|",
                f"| Carried cash {d('carried_cash')} + POS sales {d('pos_sales')} − Wallet {d('wallet')} − expenses {d('expenses')} − transfer to admin {d('transfer_to_admin')} = Expected | {money(Decimal(str(r['expected_usd'])))} |"]
        if r.get("counted_usd") is not None:
            if r.get("counted_basis") == "actual_cash_count":
                how = "the full cash count (registers + safe)"
            elif i.get("counted_lbp"):
                how = (f"{d('counted_usd_notes')} notes + {i['counted_lbp']:,.0f} L.L ÷ {i['rate_lbp_per_usd']:,.0f} (= {money(Decimal(str(r['lbp_counted_in_usd'])))})")
            else:
                how = f"{d('counted_usd_notes')} notes"
            out += [f"| Counted: {how} | {money(Decimal(str(r['counted_usd'])))} |",
                    f"| Difference (counted − expected) | {money(Decimal(str(r['variance_usd'])), signed=True)}"
                    + ("" if not r["variance_usd"] else " (over)" if r["variance_usd"] > 0 else " (short)") + " |"]
        out.append("")
    return "\n".join(out + [FOOT])


def meat_markdown(r):
    """The CFO's answer format for one check_meat_variance result."""
    rows = _flag_rows(r)
    if not r.get("ok") or r.get("status") == "incomplete":
        rows = _problem_rows(r) + [x for x in rows if x not in _problem_rows(r)]
    elif r["status"] in ("loss", "surplus"):
        if r.get("within_tolerance") is None:
            rows.append(("NOTE", r["meaning"] + " No tolerance is configured, so I am not judging it.", "computed.variance", "The manager decides."))
        elif not r["within_tolerance"]:
            rows.append(("CHECK", r["meaning"] + " It is outside the tolerance that was set.", "computed.variance", "The manager decides."))
        else:
            rows.append(("NOTE", r["meaning"] + " It is inside the tolerance that was set.", "computed.variance", "No action needed."))
    status, table = _findings(rows)
    out = [f"Status: {status}", "", *table, ""]
    if r.get("ok") and r.get("raw_used_kg") is not None:
        i, kg = r["inputs"], lambda v: f"{v:,.3f}"
        sign = "" if not r["variance_kg"] else "+" if r["variance_kg"] > 0 else "−"
        out += [f"| Meat check ({r.get('meat') or 'meat'}) | kg |", "|---|---|",
                f"| Raw used: starting {kg(i['starting_kg'])} + added {kg(i['added_kg'])} − ending {kg(i['ending_kg'])} | {kg(r['raw_used_kg'])} |",
                *[f"| Beef {lab} {kg(c['total'])} = " + " + ".join(f"{k} {kg(v)}" for k, v in c.items() if k != "total") + " (tallow included) | |" for lab, c in (r.get("components") or {}).items() if "tallow" in c],
                f"| Cooked yield: raw used × {r['cook_yield']} | {kg(r['cooked_yield_kg'])} |",
                f"| POS sold {kg(r['pos_sold_kg'])} + waste {kg(r['waste_kg'])} | {kg(r['pos_sold_kg'] + r['waste_kg'])} |",
                f"| Variance (sold + waste − cooked yield) | {sign}{kg(abs(r['variance_kg']))} ({sign}{abs(r['variance_pct']):.1f}%)"
                + ("" if not r["variance_kg"] else " (loss)" if r["variance_kg"] > 0 else " (surplus)") + " |", ""]
    return "\n".join(out + [FOOT])


def verbatim_text(name, result):
    """The finished reply for a tool result that must reach the user unchanged, or None if it should go through a model.
    The review sheet is already Markdown; the two checks are rendered here. Errors from the sheet tool are not verbatim."""
    if isinstance(result, str):
        if result.lstrip().startswith("#"):
            return result
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    if name == "reconcile_cash":
        return cash_markdown(result)
    if name == "check_meat_variance":
        return meat_markdown(result)
    return None


# ── dispatch for tool_executor ───────────────────────────────────────────────

_SIGNATURES = {
    "reconcile_cash": reconcile_cash, "check_meat_variance": check_meat_variance, "render_review_sheet": render_review_sheet,
}


def call_tool(name, args):
    """Run one of the three tools from a model-written argument dict. Returns text: JSON for the two checks, Markdown for the
    review sheet. Unknown argument names are ignored and reported, never guessed at."""
    fn = _SIGNATURES.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    args = dict(args or {})
    allowed = set(fn.__code__.co_varnames[:fn.__code__.co_argcount])
    ignored = sorted(k for k in args if k not in allowed)
    result = fn(**{k: v for k, v in args.items() if k in allowed})
    if name == "render_review_sheet" and result.get("ok"):
        return result["markdown"] + (f"\n\n(ignored unknown arguments: {', '.join(ignored)})" if ignored else "")
    if ignored:
        result["ignored_args"] = ignored
    return json.dumps(result, ensure_ascii=False, indent=2)
