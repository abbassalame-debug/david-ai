"""
restaurant_pipeline.py -- Job 1 master script: one business day's files in, a draft package and a review sheet out.

    py -3.11 restaurant_pipeline.py "C:\\...\\Demo Bistro\\09-2026\\10-09-2026"  [--day 2026-09-10] [--rate 90000] [--day-status open]

  1. vision        restaurant_vision.py reads the photos (qwen3.5:9b vision, two readings of each) and the consumption sheet (by cell; the NEWEST
                   saved version of the cumulative sheet that has the day, because older copies carry corrected-later beef figures)
  2. match         every item line is matched to the cheat-sheet menu by match_menu_item (100 exact / 85-99 suggested / else a human)
  3. math          reconcile_cash and check_meat_variance, in code, from the numbers that were read. No model does arithmetic.
  4. package       draft_package.json (schema v0) is written to <DAVID_HOME>\\jobs_drafts\\restaurant\\DRAFT-<day>-<nnn>.json, never over an older draft
  5. analyst       the analyst agent's own tools run on the saved draft, through the same permission check the HUD uses:
                   validate_draft_package, and if the package is valid, render_review_sheet -> <draft>.review.md

Nothing here writes to the live database, sends anything, approves anything or changes an input file. A draft is advice for the manager.
Two readings that disagree, a name that does not match, a figure that cannot be read, a document that is missing: each becomes a flag or a
"needs human" line on the review sheet. Nothing is silently chosen or invented.
"""

import asyncio
import collections
import datetime
import glob
import itertools
import json
import os
import re
import sys
from decimal import Decimal

import menu_tools
import restaurant_tools
import restaurant_vision as V

DRAFTS_DIR = restaurant_tools.DRAFTS_DIR
DEFAULT_RATE = 90000                       # head office's rate; the branch setting is 90000 as of 2026-09-14. Pass --rate when it changes.
DEFAULT_COOK_YIELD = 0.55
SCHEMA_ID = "draft_package/v0"
IMPORTANT_NUMBERS = ("grand_total", "net_total", "net_sales", "vat", "payments_total", "number_of_invoices", "number_of_customers")
_DAY_IN_NAME = re.compile(r"(?<!\d)(\d{1,2})-(\d{2})-(\d{4})(?!\d)")
_WHISH = re.compile(r"(?i)^\s*(wh?is?h|wallet|wish|ويش|وش)\b")
_TRANSFER = re.compile(r"(?i)^\s*(transfer|handed|تحويل)")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class PipelineError(Exception):
    pass


# ══ small helpers ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def day_from_folder(folder):
    """'10-09-2026' or '1-09-2026' -> '2026-09-10' / '2026-09-01' (day-month-year), or None."""
    m = _DAY_IN_NAME.search(os.path.basename(os.path.normpath(folder)))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def _field(doc, name):
    f = doc["comparison"]["fields"].get(name) if doc else None
    return f or {"value": None, "alt": None, "agree": True}


def _invoice_span(doc):
    """How many invoices a Main Reading printout covers (last - first + 1), 0 when it does not say. A day's folder holds several printouts taken at
    different hours (a single-invoice slip at 13:31, an LBP-only slip at 17:07, the end-of-day report after midnight): the one that covers the most
    invoices is the day's report."""
    f = (doc.get("comparison") or {}).get("fields") or {}
    a, b = (f.get("first_invoice") or {}).get("value"), (f.get("last_invoice") or {}).get("value")
    return int(b - a + 1) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a else 0


def _first(docs, kind):
    """The BEST document of a kind, then the other usable ones: the one with the most of its key figures read; between equals, for a Main Reading
    the printout that covers the most invoices, then the one the two readings disagree about least; the file name only breaks a final tie.
    A picture the model labelled with the kind but in which none of its key figures could be read is not that document and is ignored."""
    ds = sorted((d for d in docs if d.get("document_type") == kind and V.usable_score(d) > 0),
                key=lambda d: (-V.usable_score(d), -(_invoice_span(d) if kind == "pos_main_reading" else 0), (d.get("comparison") or {}).get("disagreements", 0), d["file"]))
    return (ds[0] if ds else None), ds[1:]


def central_files(day, restaurant_root=None):
    """{document_type: [paths]}: the Daily Helper screenshots kept in the central data folder (<Demo Bistro>\\data\\D-MM-YYYY.jpeg) for this day."""
    folder = os.path.join(restaurant_root or menu_tools.RESTAURANT_ROOT, "data")
    out = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            stem, ext = os.path.splitext(f)
            if ext.lower() in V.IMAGE_EXT and day_from_folder(stem) == day:
                out.append(os.path.join(folder, f))
    return {"daily_helper_sheet": out} if out else {}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _round2(x):
    return float(restaurant_tools.rnd(Decimal(str(x)), 2))


def _day_number(text):
    """The day-of-month written on the helper sheet ('9 Sept 2026', '10/09/2026') or None."""
    if not isinstance(text, str):
        return None
    m = re.match(r"\s*(\d{1,2})\s*[/\-. ]\s*([A-Za-z]+|\d{1,2})", text)
    return int(m.group(1)) if m else None


def next_draft_id(day, drafts_dir):
    os.makedirs(drafts_dir, exist_ok=True)
    taken = {int(m.group(1)) for f in os.listdir(drafts_dir) for m in [re.fullmatch(rf"DRAFT-{day}-(\d{{3}})\.json", f)] if m}
    return f"DRAFT-{day}-{(max(taken) + 1) if taken else 1:03d}"


# ══ building the package ══════════════════════════════════════════════════════════════════════════════════════════════════════

PHOTO_JOIN_WINDOW = 45                            # rows compared at the join of two photos of one list (photos of a scrolling screen overlap by many rows)


def _overlap_direction(rows, nxt, edge=4):
    """'forward' when the end of `rows` is the start of `nxt` (nxt continues rows), 'backward' when the end of `nxt` is the start of `rows`, else None.
    The rows both show must be the same row (name alike, same quantity), at least two of them or one identical, and they must sit at the END of
    one list and the START of the other, give or take `edge` rows that a photo's boundary cuts."""
    pairs = [(a, b) for a, b in V.align(rows, nxt, "name") if a is not None and b is not None and a.get("qty") == b.get("qty") and V._sim(a.get("name"), b.get("name")) >= 0.75]
    if len(pairs) < 2 and not any(V._sim(a.get("name"), b.get("name")) >= 0.9 for a, b in pairs):
        return None
    ia = [next(k for k, x in enumerate(rows) if x is a) for a, _ in pairs]
    ib = [next(k for k, x in enumerate(nxt) if x is b) for _, b in pairs]
    forward = max(ia) >= len(rows) - 1 - edge and min(ib) <= edge
    backward = max(ib) >= len(nxt) - 1 - edge and min(ia) <= edge
    return "forward" if forward else "backward" if backward else None


def _join_photos(chunks):
    """The item rows of several photos of ONE list as one list. Photos of a list that is longer than one picture (the POS program's screen, scrolled)
    overlap: the rows both photos show are counted once, whichever photo is the top one. For each next photo the direction of the overlap is found
    (see _overlap_direction) and the photos are joined with the strip stitcher; with no shared rows the photos are joined in the order given (as
    before) and the join is counted. Returns (rows, joins_with_no_shared_rows, rows_dropped_because_two_photos_show_them)."""
    rows, unverified = list(chunks[0]), 0
    before = sum(len(c) for c in chunks)
    for nxt in chunks[1:]:
        way = _overlap_direction(rows, list(nxt))
        if way == "forward":
            rows = V.stitch([rows, list(nxt)], "name", window=PHOTO_JOIN_WINDOW)[0]
        elif way == "backward":
            rows = V.stitch([list(nxt), rows], "name", window=PHOTO_JOIN_WINDOW)[0]
        else:
            rows, unverified = rows + list(nxt), unverified + 1
    return rows, unverified, before - len(rows)


def _one_row_twice(a, ma, b, mb):
    """Two neighbouring item rows, each seen by one reading only, with the same quantity and the same menu item: one receipt row spelled two ways."""
    for r in (a, b):
        if r.get("readings_agree") or r.get("name_alt") or "qty_alt" in r or _num(r.get("qty")) is None:
            return False
    if _num(a.get("qty")) != _num(b.get("qty")):
        return False
    ia, ib = (ma.get("package_match") or {}).get("item_id"), (mb.get("package_match") or {}).get("item_id")
    sure = lambda x: x["status"] in ("matched", "suggested") and x["score"] >= 85
    # one spelling is a sure match; the other is a garble whose best candidate is the SAME item (at least 60), so it is that row read again, not a different item
    return bool(ia) and ia == ib and (sure(ma) or sure(mb)) and ma["score"] >= 60 and mb["score"] >= 60


def _item_lines(docs, glossary, flags):
    """The item list of the day with its matches. Returns (lines, voids)."""
    primary, extra = _first(docs, "pos_summary_by_items")
    if primary is None:
        flags.append("BLOCKER: no 'Summary By Items' photo was found, so there are no item lines to review")
        return [], []
    sums = [primary] + extra
    rows, voids = [], []
    for d in sums:
        voids += d["comparison"]["lists"].get("voids", [])
        if d["comparison"].get("strip_quantities_realigned"):
            flags.append(f"CHECK: {d['file']}: in one reading the quantities of {d['comparison']['strip_quantities_realigned']} rows were found one row out of place after a cut (the numbers of a tilted photo sit above their names) and were moved back; the other reading is the check")
        if d["comparison"]["strip_joins_without_overlap"]:
            flags.append(f"CHECK: {d['file']} was read in strips and {d['comparison']['strip_joins_without_overlap']} strip join(s) share no line: a line may be missing or doubled there")
    rows, no_overlap, dropped = _join_photos([d["comparison"]["lists"].get("lines", []) for d in sums])
    if extra:
        files = ", ".join(d["file"] for d in sums)
        if no_overlap:
            flags.append(f"CHECK: the item list was read from {len(sums)} photos ({files}); {no_overlap} of the joins share no row, so those photos were joined in file-name order: check that nothing is missing or doubled")
        else:
            flags.append(f"NOTE: the item list was read from {len(sums)} photos ({files}); the {dropped} rows that two photos both show were counted once")
    # lines the manager ruled are not items ('without garlic', 'extra sauce', 'free Pepsi'): left out, and named in a NOTE so nothing is hidden
    kept, left_out = [], collections.OrderedDict()
    for r in rows:
        kind = menu_tools.ignored_line_kind(r.get("name"), glossary) or menu_tools.ignored_line_kind(r.get("name_alt"), glossary)
        if kind is None:
            kept.append(r)
        else:
            nm = str(r.get("name") or r.get("name_alt") or "")
            left_out.setdefault(kind, []).append(f"{nm} x{_num(r.get('qty')):g}" if _num(r.get("qty")) is not None else nm)
    if left_out:
        n_out = sum(len(v) for v in left_out.values())
        flags.append(f"NOTE: {n_out} receipt line(s) are 'without ...', 'extra ...' or '... free' lines, which the manager ruled are not items (2026-10-08); they are left out of the item lines and the meat: "
                     + "; ".join(f"{k}: " + ", ".join(v) for k, v in left_out.items()))
    rows = kept
    names = [str(r.get("name") or "") for r in rows]
    alts = [str(r.get("name_alt") or "") for r in rows]
    matches = menu_tools.match_menu_item(raw_texts=names, glossary=glossary)["results"] if names else []
    alt_matches = menu_tools.match_menu_item(raw_texts=[a if a else " " for a in alts], glossary=glossary)["results"] if names else []
    # A 'Summary By Items' roll lists every item ONCE. Two neighbouring rows that each only one reading saw, with the same quantity and the same menu item,
    # are one row spelled two ways (found on 7 days: the Miranda row sits where the two halves overlap, the readings garble it differently, and the
    # alignment keeps both). They are merged into one line that keeps both spellings; two DIFFERENT items that share a quantity are never merged.
    merged_rows, merged_matches, merged_alts, same_row = [], [], [], []
    i = 0
    while i < len(rows):
        r, m, ma = rows[i], matches[i], alt_matches[i]
        if i + 1 < len(rows) and _one_row_twice(rows[i], matches[i], rows[i + 1], matches[i + 1]):
            nxt = rows[i + 1]
            same_row.append(f"{r.get('name')} / {nxt.get('name')} x{_num(r.get('qty')):g}")
            r = dict(r, name_alt=nxt.get("name"))
            ma = matches[i + 1]
            i += 1
        merged_rows.append(r)
        merged_matches.append(m)
        merged_alts.append(ma)
        i += 1
    if same_row:
        flags.append(f"NOTE: {len(same_row)} receipt row(s) were seen once and spelled two ways by the two readings (same quantity, same menu item, next to each other; the roll lists each item once), so each is ONE line: " + "; ".join(same_row))
    rows, matches, alt_matches = merged_rows, merged_matches, merged_alts
    lines = []
    for n, (r, m, ma) in enumerate(zip(rows, matches, alt_matches), 1):
        name, alt, agree = r.get("name") or "", r.get("name_alt"), bool(r.get("readings_agree"))
        # The two readings may spell a NAME differently while agreeing on the quantity (the second reading often adds a stray letter). The menu
        # settles that when exactly one reading is an exact sheet name and the other does not match a DIFFERENT item; a quantity that differs, or a
        # row only one reading saw, is never settled here.
        settled = None
        if not agree and alt and "qty_alt" not in r:
            exact = lambda x: x["score"] == 100 and x["status"] == "matched"
            other_item = lambda x, y: x["package_match"]["item_id"] != y["package_match"]["item_id"]
            if exact(m) and not (ma["score"] >= 85 and other_item(m, ma)):
                settled = "name_ar"
            elif exact(ma) and not exact(m) and not (m["score"] >= 85 and other_item(m, ma)):
                settled = "name_ar_alt"
        if settled == "name_ar_alt":
            name, alt, m, ma = alt, name, ma, m
        use_alt = settled is None and bool(alt) and ma["score"] > m["score"]
        best = ma if use_alt else m
        line = {"line": n, "name_ar": name, "qty": _num(r.get("qty")), "amount": None, "readings_agree": agree or settled is not None,
                "match": best["package_match"], "match_status": best["status"], "match_from": "name_ar_alt" if use_alt else "name_ar"}
        if alt:
            line["name_ar_alt"] = alt
        if settled:
            line["name_settled_by_menu"] = True
        if "qty_alt" in r:
            line["qty_alt"] = _num(r["qty_alt"])
        elif not line["readings_agree"] and not alt:
            # a row only one reading saw: the other reading has nothing for it. A null second reading says so; without one the validator
            # (rightly) refuses a disagreement that shows no second reading.
            line["qty_alt"] = None
        lines.append(line)
    n_dis = sum(1 for l in lines if not l["readings_agree"])
    if n_dis:
        flags.append(f"CHECK: {n_dis} of {len(lines)} item lines were read differently by the two readings (both are shown on the sheet)")
    if voids:
        flags.append(f"NOTE: the receipt lists {len(voids)} void line(s); they are shown in the package and are not subtracted from the quantities")
    return lines, voids


def _row_options(r):
    """What a Daily Helper row can be, given what the two readings said: [(kept, amount)]. First is what reading A said."""
    amt, alt = _num(r.get("amount")), _num(r.get("amount_alt"))
    if r["readings_agree"] or (alt is None and "subject_alt" in r):       # agreed, or only the LABEL was read two ways
        return [(True, amt)]
    if alt is not None and amt is not None and alt != amt:               # two different amounts
        return [(True, amt), (True, alt)]
    return [(True, amt), (False, 0.0)]                                   # a row only one reading found: it is there, or it is not


def _settle_rows(rows, total):
    """The Daily Helper prints its own Total (the sum of the Transfer, Wish and expense rows). When the two readings disagree about rows, the choice
    that makes the rows add up to that printed Total, and ONLY that choice, settles it (a sum the sheet itself proves). Returns (chosen, settled):
    chosen[i] = (kept, amount) for each row; settled = True only when the Total picked exactly one combination."""
    opts = [_row_options(r) for r in rows]
    default = [o[0] for o in opts]
    variable = [i for i, o in enumerate(opts) if len(o) > 1]
    if not variable or total is None or len(variable) > 10 or any(a is None for o in opts for _, a in o):
        return default, False
    hits = []
    for combo in itertools.product(*[opts[i] for i in variable]):
        pick = list(default)
        for i, c in zip(variable, combo):
            pick[i] = c
        if abs(sum(a for kept, a in pick if kept) - total) <= 0.005:
            hits.append(pick)
    return (hits[0], True) if len(hits) == 1 else (default, False)


def _helper_cash(helper, rate, flags=None):
    """Cash inputs from the Daily Helper screenshot: {old, sales, wallet, expenses[], transfer, new, expected, diff, value_usd}."""
    flags = flags if flags is not None else []
    rows = helper["comparison"]["lists"].get("rows", [])
    f = lambda k: _field(helper, k)["value"]
    total_fld = _field(helper, "total")
    chosen, settled = _settle_rows(rows, _num(total_fld["value"]) if total_fld["agree"] else None)
    unsettled = [r for r in rows if not r["readings_agree"] and not (settled and len(_row_options(r)) > 1) and not (_num(r.get("amount_alt")) is None and "subject_alt" in r)]
    label_only = [r for r in rows if not r["readings_agree"] and _num(r.get("amount_alt")) is None and "subject_alt" in r and _num(r.get("amount")) is not None]
    if settled:
        for r, (kept, amt) in zip(rows, chosen):
            if len(_row_options(r)) > 1:
                flags.append(f"NOTE: the two readings of the Daily Helper row '{r.get('subject')}' differ ({r.get('amount')} / {r.get('amount_alt')}"
                             f"{'' if r.get('amount_alt') is not None else ', found by one reading only'}); the sheet's own Total {_num(total_fld['value']):,.2f} is reached only with "
                             f"{'this row dropped' if not kept else f'{amt:,.2f}'}, so that is used")
    if label_only:
        flags.append("NOTE: a Daily Helper row was read with two different labels but the same amount (" + "; ".join(f"{r.get('subject')} / {r.get('subject_alt')}: {r.get('amount')}" for r in label_only) + "); counted once")
    out = {"old": _num(f("old")), "sales": _num(f("sales")), "wallet": None, "expenses": [], "transfer": None, "new": _num(f("new")), "expected": _num(f("expected")),
           "diff": _num(f("diff")), "value_usd": _num(f("value_usd")), "row_disagreements": unsettled}
    for r, (kept, amount) in zip(rows, chosen):
        subject = str(r.get("subject") or "")
        if not kept:
            continue
        if _TRANSFER.match(subject):
            out["transfer"] = amount
        elif _WHISH.match(subject):
            out["wallet"] = (out["wallet"] or 0.0) + (amount or 0.0) if amount is not None else out["wallet"]
        else:
            out["expenses"].append({"label": subject, "amount": amount})
    blank = [e for e in out["expenses"] if e["amount"] is None]
    total_v = _num(total_fld["value"]) if total_fld["agree"] else None
    if blank and total_v is not None:
        # The sheet has a Note column beside the amounts; text in it can be read as a row of its own with no amount. A row with no amount is a NOTE only when the
        # sheet's own printed Total is reached without it. If the Total is not reached, it stays an expense with a missing amount and blocks (it may be a real one).
        counted = (out["transfer"] or 0.0) + (out["wallet"] or 0.0) + sum(e["amount"] for e in out["expenses"] if e["amount"] is not None)
        if abs(counted - total_v) <= 0.015:
            out["expenses"] = [e for e in out["expenses"] if e["amount"] is not None]
            flags.append("NOTE: the Daily Helper has text in its Note column (" + "; ".join(f"«{e['label']}»" for e in blank) + "): these are notes beside the amounts, not expenses; "
                         f"the sheet's own Total {total_v:,.2f} is reached without them, so they are not counted")
    return out


def _form_cash(form, rate, flags):
    """Cash inputs from the handwritten Daily Cash form (the fallback). L.L. amounts are converted at the day's rate and said so."""
    rows = form["comparison"]["lists"].get("expenses", [])
    out = {"old": _num(_field(form, "opening_cash_balance")["value"]), "sales": _num(_field(form, "total_pos_sales")["value"]), "wallet": None, "expenses": [],
           "transfer": _num(_field(form, "total_to_be_handed")["value"]), "new": None, "row_disagreements": [r for r in rows if not r["readings_agree"]]}
    for r in rows:
        usd, lbp = _num(r.get("amount_usd")), _num(r.get("amount_lbp"))
        if usd is None and lbp is not None:
            usd = _round2(lbp / rate)
            flags.append(f"NOTE: expense '{r.get('description')}' was written in L.L. ({lbp:,.0f}); converted at {rate:,.0f} to {usd:,.2f} USD")
        if _WHISH.match(str(r.get("description") or "")):
            out["wallet"] = (out["wallet"] or 0.0) + (usd or 0.0)
        else:
            out["expenses"].append({"label": r.get("description") or "", "amount": usd})
    return out


def _digits(x):
    return f"{abs(x):.8f}".replace(".", "").strip("0")


def _resolve_decimal_slips(main, other, flags):
    """A Main Reading figure the two readings wrote with the SAME digits but the decimal point elsewhere (1.37725 and 1377.25) is a decimal slip.
    If the Daily Helper's sales (`other`) is within 0.5% of exactly one of the two, that one is used, and the field counts as agreed."""
    if not main or other is None:
        return
    for k, f in main["comparison"]["fields"].items():
        a, b = _num(f["value"]), _num(f["alt"])
        if f["agree"] or a is None or b is None or a == b or _digits(a) != _digits(b):
            continue
        pick = min((a, b), key=lambda v: abs(v - other))
        if abs(pick - other) > max(0.01, 0.005 * abs(other)):
            continue
        wrong = b if pick == a else a
        f.update({"value": pick, "alt": wrong, "agree": True, "resolved": "decimal point"})
        flags.append(f"NOTE: Main Reading '{k}': one reading wrote {wrong:g} and the other {pick:g}, the same digits with the decimal point elsewhere; {pick:,.2f} is used because the Daily Helper's sales ({other:,.2f}) agree with it")


def _cash_block(docs, rate, flags):
    helper, helper_x = _first(docs, "daily_helper_sheet")
    form, form_x = _first(docs, "daily_cash_form")
    main, main_x = _first(docs, "pos_main_reading")
    for chosen, extras, what in ((helper, helper_x, "Daily Helper"), (form, form_x, "Daily Cash form"), (main, main_x, "Main Reading")):
        if extras:
            flags.append(f"CHECK: more than one {what} photo was found; only {chosen['file']} was used")
    if main is None:
        flags.append("CHECK: no 'Main Reading' photo was found, so the day's POS sales rest on the other sheets only")
    src = None
    if helper:
        src, cash = "the Daily Helper screenshot", _helper_cash(helper, rate, flags)
    elif form:
        src, cash = "the handwritten Daily Cash form", _form_cash(form, rate, flags)
        flags.append("CHECK: no Daily Helper screenshot was found; the cash inputs come from the handwritten form and no full cash count is available")
    else:
        flags.append("BLOCKER: neither a Daily Helper screenshot nor a Daily Cash form was found, so the cash check cannot be done")
        cash = {"old": None, "sales": None, "wallet": None, "expenses": [], "transfer": None, "new": None, "row_disagreements": []}
    _resolve_decimal_slips(main, cash.get("sales"), flags)
    sales_main = _num(_field(main, "net_sales")["value"]) if main else None
    if main and sales_main is None:
        sales_main = _num(_field(main, "grand_total")["value"])
    pos_sales = sales_main if sales_main is not None else cash.get("sales")
    # the readings must agree on every figure the cash check rests on
    used = {}
    if helper:
        used.update({f"Daily Helper '{k}'": _field(helper, k) for k in ("old", "sales", "new", "value_usd")})
    if form:
        used.update({f"Daily Cash form '{k}'": _field(form, k) for k in ("opening_cash_balance", "total_to_be_handed", "total_pos_sales")} if not helper else {})
    if main:
        used.update({f"Main Reading '{k}'": _field(main, k) for k in ("net_sales", "grand_total")})
    for label, fld in used.items():
        if not fld["agree"]:
            flags.append(f"BLOCKER: {label}: the two readings differ ({fld['value']} against {fld['alt']}); the cash check below uses the first")
    for r in cash["row_disagreements"]:
        flags.append(f"BLOCKER: a cash row was read differently by the two readings: {r}")
    # the sheets must tell the same story
    if helper and sales_main is not None and cash["sales"] is not None and abs(cash["sales"] - sales_main) > 0.005:
        flags.append(f"CHECK: the Daily Helper sheet says sales {cash['sales']:,.2f} but the Main Reading says {sales_main:,.2f}")
    if helper and form:
        fo, ft = _num(_field(form, "opening_cash_balance")["value"]), _num(_field(form, "total_to_be_handed")["value"])
        if fo is not None and cash["old"] is not None and abs(fo - cash["old"]) > 0.005:
            flags.append(f"CHECK: the opening cash is {cash['old']:,.2f} on the Daily Helper sheet but {fo:,.2f} on the Daily Cash form")
        if ft is not None and cash["transfer"] is not None and abs(ft - cash["transfer"]) > 0.005:
            flags.append(f"CHECK: the transfer is {cash['transfer']:,.2f} on the Daily Helper sheet but {ft:,.2f} on the Daily Cash form")
        fs = _num(_field(form, "total_pos_sales")["value"])
        if fs is not None and pos_sales is not None and abs(fs - pos_sales) > 0.005:
            flags.append(f"NOTE: the Daily Cash form shows POS sales {fs:,.2f} (probably written part-way through the day) against {pos_sales:,.2f} on the Main Reading; the Main Reading is used")
    return helper, form, main, cash, pos_sales, src


def _sheet_has_day(path, day):
    try:
        r = V.read_consumption_xlsx(path, day)
    except Exception:
        return False
    return any(r.get(m) for m in ("beef", "chicken"))


def _sheet_name_date(path):
    """The date a consumption sheet is named for (`... 30-09-2026.xlsx`) as a sortable (y, m, d); (0, 0, 0) when the name has none."""
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})(?!.*\d{1,2}-\d{1,2}-\d{4})", os.path.basename(path))
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (0, 0, 0)


def find_consumption_sheet(folder, day, restaurant_root=None, meat_sheet=None):
    """(path, note) of the branch's 'Daily Beef & Chicken Stock Consumption' sheet to read the day's meat from, or (None, note).

    Every day's folder holds its own copy of the month's CUMULATIVE sheet, saved at different times, and the manager corrects older rows later
    (the beef opening of 2026-09-01 is 21.14 kg in the copy saved that day and 24.14 kg in the corrected copy of 30 Sep, which is the app's own
    baseline). The sheet is named for the day it was saved (`... 30-09-2026.xlsx`), so the right sheet is the one with the LATEST DATE IN ITS NAME that
    has a row for the day, wherever it is in the Demo Bistro folder; the file's own save time only breaks a tie. The save time alone cannot be trusted:
    an old copy re-downloaded or re-synced later looks newer than the corrected one (the 04-09 copies were re-saved on 6 Oct and carried the old beef).
    An explicit meat_sheet always wins."""
    if meat_sheet:
        if not os.path.isfile(meat_sheet):
            return None, f"the consumption sheet {meat_sheet} given for this run does not exist"
        return meat_sheet, "the consumption sheet given for this run"
    root = restaurant_root or menu_tools.RESTAURANT_ROOT
    own = sorted(glob.glob(os.path.join(folder, "Branch*Daily consumption*.xlsx")))
    pool = set(own)
    if os.path.isdir(root):
        pool |= set(glob.glob(os.path.join(root, "*-20??", "*", "Branch*Daily consumption*.xlsx")))
    for path in sorted(pool, key=lambda p: (_sheet_name_date(p), os.path.getmtime(p), p), reverse=True):
        if _sheet_has_day(path, day):
            saved = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d %b %H:%M")
            where = "the day's own folder" if path in own else os.path.basename(os.path.dirname(path))
            return path, f"the latest-dated version that has {day} ({os.path.basename(path)} in {where}, saved {saved})"
    return None, "no consumption sheet with a row for this day was found"


def _meat_block(folder, day, flags, meat_sheet=None, restaurant_root=None):
    """Meat inputs from the branch's consumption sheet, and the two code checks. ({"chicken": res, "beef": res}, raw rows) or ({}, {})."""
    path, why = find_consumption_sheet(folder, day, restaurant_root, meat_sheet)
    if path is None:
        flags.append(f"CHECK: {why}, so the meat variance was not checked")
        return {}, {}, None
    flags.append(f"NOTE: the meat weights are from {why}")
    paths = [path]
    try:
        rows = V.read_consumption_xlsx(paths[0], day)
    except Exception as e:
        flags.append(f"CHECK: the consumption sheet {os.path.basename(paths[0])} could not be read ({e}); the meat variance was not checked")
        return {}, {}, paths[0]
    res = {}
    if rows.get("beef"):
        flags.append("NOTE: the beef starting, received and ending weights are the sheet's totals and must include the fat/tallow (لية), not just the meat: "
                     "the older copies of the sheet left it out (about 3 kg of opening stock and 2 kg a day received), which is why the latest-dated version of the sheet is read. "
                     "check_meat_variance flags beef totals that come without their parts")
    for meat in ("chicken", "beef"):
        r = rows.get(meat)
        if r is None:
            flags.append(f"CHECK: the consumption sheet has no {meat} row for {day}, so the {meat} variance was not checked")
            continue
        shrink = r.get("shrinkage")
        out = restaurant_tools.check_meat_variance(meat=meat, starting_kg=r["opening_raw_kg"], added_kg=r["received_raw_kg"], pos_sold_kg=r["sold_kg"], ending_kg=r["remaining_raw_kg"],
                                                   waste_kg=r["waste_kg"], cook_yield=(round(1 - shrink, 6) if shrink is not None and abs((1 - shrink) - DEFAULT_COOK_YIELD) > 1e-9 else None))
        res[meat] = out
        if out.get("ok") and r.get("used_in_cooking_raw_kg") is not None and r["opening_raw_kg"] is not None:
            code_used = r["opening_raw_kg"] + r["received_raw_kg"] - r["remaining_raw_kg"]
            if abs(code_used - r["used_in_cooking_raw_kg"]) > 0.01:
                flags.append(f"CHECK: the consumption sheet's 'used in cooking' for {meat} is {r['used_in_cooking_raw_kg']:.3f} kg but opening + received - remaining is {code_used:.3f} kg")
    return res, rows, paths[0]


def _meat_from_lines(lines, glossary):
    """Kilograms of meat the item list implies (quantity x the cheat sheet's grams). `matched` is the confirmed figure: matched lines the two readings agree on.
    `with_suggested` also counts suggested lines and the lines whose two readings differ ONLY in how the name is spelled (both readings give the same
    quantity and the name still matches or is suggested): a garbled Arabic spelling does not change how many were sold, and leaving such lines out hid
    most of the chicken on 6 and 10 Sep (the 19 'كايزر 6 دجاج+18 قطعة مرقوق' alone are 10.8 kg). A line with different quantities, found by only one
    reading, or with no good match is never counted."""
    by = {i["item_id"]: i for i in glossary["items"]}
    tot = {"matched": {"chicken": 0.0, "beef": 0.0, "sojok": 0.0}, "with_suggested": {"chicken": 0.0, "beef": 0.0, "sojok": 0.0}}
    left, spelling_only = [], []
    for l in lines:
        m, st = l["match"], l.get("match_status")
        it = by.get(m.get("item_id")) if m else None
        if st not in ("matched", "suggested") or it is None:
            left.append({"line": l["line"], "name": l["name_ar"], "qty": l["qty"], "why": "not matched" if st != "suggested" else "no item"})
            continue
        if it["grams_status"] in ("ambiguous", "unspecified") or it["grams"] is None:
            left.append({"line": l["line"], "name": l["name_ar"], "qty": l["qty"], "why": f"the cheat sheet leaves its meat weight open ({it['grams_status']})"})
            continue
        names_only = (not l["readings_agree"]) and "qty_alt" not in l and "amount_alt" not in l and l["qty"] is not None
        if not l["readings_agree"] and not names_only:
            left.append({"line": l["line"], "name": l["name_ar"], "qty": l["qty"], "why": "the two readings differ on the quantity or only one of them found the line"})
            continue
        if l["qty"] is None:
            left.append({"line": l["line"], "name": l["name_ar"], "qty": None, "why": "no quantity"})
            continue
        if names_only:
            spelling_only.append({"line": l["line"], "name": l["name_ar"], "qty": l["qty"]})
        for p in ("chicken", "beef", "sojok"):
            kg = l["qty"] * it["grams"][p] / 1000.0
            tot["with_suggested"][p] += kg
            if st == "matched" and not names_only:
                tot["matched"][p] += kg
    n_conf = len(lines) - len(left) - len(spelling_only)
    return {k: {p: round(v, 3) for p, v in d.items()} for k, d in tot.items()} | {"lines_not_counted": left, "lines_counted": n_conf, "lines_counted_with_suggestions": len(lines) - len(left),
                                                                                  "lines_spelling_only": spelling_only, "lines_total": len(lines)}


def build_package(docs, folder, day, *, rate, day_status, correction_reason, glossary, drafts_dir, draft_id=None, meat_sheet=None, restaurant_root=None):
    """The draft package (a dict) and a small report, from the vision readings of the day's photos. Pure apart from reading the sheet."""
    flags = []
    for d in docs:
        if d.get("comparison") and V.usable_score(d) == 0:
            flags.append(f"CHECK: {d['file']} looked like a {d['document_type'].replace('_', ' ')} but none of its key figures could be read; it was ignored")
        if d.get("not_a_sales_list"):
            flags.append(f"NOTE: {d['file']} first looked like the POS item list but is not one ({d['not_a_sales_list'].get('why') or d['document_type']}); no item was read from it")
        if d.get("from_central") and V.usable_score(d) > 0:
            flags.append(f"NOTE: the {d['document_type'].replace('_', ' ')} {d['file']} was not in the day's folder; it came from the central data folder")
    lines, voids = _item_lines(docs, glossary, flags)
    helper, form, main, cash, pos_sales, cash_src = _cash_block(docs, rate, flags)
    meat, meat_rows, meat_path = _meat_block(folder, day, flags, meat_sheet, restaurant_root)

    # the day the sheets say they are about
    if helper:
        dn = _day_number(_field(helper, "date")["value"])
        if dn is not None and dn != int(day[8:10]):
            flags.append(f"CHECK: the Daily Helper sheet is dated '{_field(helper, 'date')['value']}' but the folder is {day}; the sheet's date cell may not have been updated")
    if form:
        dn = _day_number(_field(form, "date")["value"])
        if dn is not None and dn != int(day[8:10]):
            flags.append(f"CHECK: the Daily Cash form is dated '{_field(form, 'date')['value']}' but the folder is {day}")
    if main:
        if not _field(main, "first_order")["agree"] or not _field(main, "last_order")["agree"]:
            flags.append("NOTE: the two readings of the Main Reading differ in the seconds of the first or last order time; nothing depends on them")
        fo = str(_field(main, "first_order")["value"] or "")
        if fo and not fo.startswith(f"{day[8:10]}-{day[5:7]}-{day[0:4]}"):
            flags.append(f"CHECK: the Main Reading's first order is '{fo}', which is not on {day}")

    # cash: the same code the CFO uses
    cash_args = {"carried_cash": cash["old"], "pos_sales": pos_sales, "wallet": cash["wallet"] if cash["wallet"] is not None else (0 if (helper or form) else None),
                 "expenses": [e for e in cash["expenses"] if e["amount"] is not None] if (helper or form) else None,
                 "transfer_to_admin": cash["transfer"], "actual_cash_count": cash.get("new"), "counted_usd": cash.get("value_usd"),
                 "rate_lbp_per_usd": rate, "branch_rate_lbp_per_usd": rate}
    if any(e["amount"] is None for e in cash["expenses"]):
        flags.append("BLOCKER: an expense row has no readable amount, so the expenses total is unknown")
        cash_args["expenses"] = None
    cash_res = restaurant_tools.reconcile_cash(**cash_args)
    if helper and cash_res.get("ok") and cash_res.get("expected_usd") is not None:
        if cash["expected"] is not None and abs(cash["expected"] - cash_res["expected_usd"]) > 0.011:
            flags.append(f"CHECK: the Daily Helper sheet's own 'Expected' is {cash['expected']:,.2f} but the code gets {cash_res['expected_usd']:,.2f} from the same figures")
        if cash["diff"] is not None and cash_res.get("variance_usd") is not None and abs(cash["diff"] - cash_res["variance_usd"]) > 0.011:
            flags.append(f"CHECK: the Daily Helper sheet's own 'Diff' is {cash['diff']:+,.2f} but the code gets {cash_res['variance_usd']:+,.2f}")

    meat_lines = _meat_from_lines(lines, glossary)
    if meat_lines["lines_not_counted"]:
        flags.append(f"NOTE: {len(meat_lines['lines_not_counted'])} of {meat_lines['lines_total']} item lines are not counted in the meat implied by the item list at all (no good match, no stated weight, a different quantity in the two readings, or found by one reading only); that figure is a lower bound")
    for meat_name, key in (("chicken", "chicken"), ("beef", "beef")):
        r = meat_rows.get(meat_name) if meat_rows else None
        if r and r.get("sold_kg") is not None:
            flags.append(f"NOTE: {meat_name}: the item list implies {meat_lines['matched'][key]:.1f} kg sold from {meat_lines['lines_counted']} confirmed lines, and {meat_lines['with_suggested'][key]:.1f} kg when the suggested lines and the {len(meat_lines['lines_spelling_only'])} lines the readings only spelled differently are counted too ({meat_lines['lines_counted_with_suggestions']} lines); the consumption sheet says {r['sold_kg']:.1f} kg sold")

    sources = [{"file": d["file"], "kind": d["document_type"], "sha256": d["sha256"]} for d in docs if d.get("document_type") not in (None, *V.NOT_READ_TYPES) and V.usable_score(d) > 0]
    if meat_path:
        sources.append({"file": os.path.basename(meat_path), "kind": "consumption_xlsx", "sha256": V.file_sha256(meat_path)})
    main_block = None
    if main:
        main_block = {k: v["value"] for k, v in main["comparison"]["fields"].items() if v["value"] is not None}
        differ = {k: {"a": v["value"], "b": v["alt"]} for k, v in main["comparison"]["fields"].items() if not v["agree"]}
        if differ:
            main_block["readings_differ"] = differ
        main_block["payments"] = [{k: v for k, v in r.items() if k != "readings_agree"} for r in main["comparison"]["lists"].get("payments", [])]
        for k in IMPORTANT_NUMBERS:
            if k in differ:
                flags.append(f"BLOCKER: Main Reading '{k}': the two readings differ ({differ[k]['a']} against {differ[k]['b']})")
    pkg = {
        "schema": SCHEMA_ID, "draft_id": draft_id or next_draft_id(day, drafts_dir), "branch": "Downtown", "business_day": day, "day_status": day_status,
        "rate_lbp_per_usd": rate, "branch_settings": {"rate_lbp_per_usd": rate, "cook_yield": DEFAULT_COOK_YIELD},
        "sources": sources,
        "extraction": {"summary_by_items": {"printed_total": None, "amounts_printed": False, "lines": lines, "voids": voids}, "main_reading": main_block or {},
                       "daily_helper": ({k: v["value"] for k, v in helper["comparison"]["fields"].items()} | {"rows": helper["comparison"]["lists"].get("rows", [])}) if helper else None,
                       "daily_cash_form": ({k: v["value"] for k, v in form["comparison"]["fields"].items()} | {"expenses": form["comparison"]["lists"].get("expenses", []),
                                                                                                               "cash_on_hold": form["comparison"]["lists"].get("cash_on_hold", [])}) if form else None},
        "cash": {"old_cash": cash["old"], "pos_sales": pos_sales, "wallet": cash_args["wallet"], "expenses": cash["expenses"], "transfer_to_admin": cash["transfer"],
                 "counted_usd": cash.get("value_usd"), "counted_lbp": None, "actual_cash_count": cash.get("new"), "source": cash_src},
        "computed": {"expected": cash_res.get("expected_usd") if cash_res.get("ok") else None, "counted": cash_res.get("counted_usd") if cash_res.get("ok") else None,
                     "diff": cash_res.get("variance_usd") if cash_res.get("ok") else None, "meat_variance": {k: {"status": v.get("status"), "variance_kg": v.get("variance_kg"), "variance_pct": v.get("variance_pct")} for k, v in meat.items() if v.get("ok")},
                     "meat_from_lines": meat_lines},
        "cfo_results": {"cash": cash_res, "meat": meat},
        "flags": flags, "tolerances": None,
    }
    if day_status == "ended":
        pkg["correction_reason"] = correction_reason
    for k in ("expected", "counted", "diff"):
        if pkg["computed"][k] is None:
            del pkg["computed"][k]
    return pkg, {"lines": len(lines), "documents": len(sources)}


# ══ run a day ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _write_new(path, text):
    if os.path.exists(path):
        raise PipelineError(f"{path} already exists; a draft is never overwritten")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def analyst_review(draft_id=None, package=None):
    """The analyst's own two tools on a draft, through the permission check the HUD uses. Give draft_id (a draft saved in the default folder, as an
    analyst would) or package. Returns (validation_result_dict, reply_text): the review sheet if the package is valid, else the validation report."""
    import tool_executor
    args = {"draft_id": draft_id} if package is None else {"package": package}

    async def go():
        check = await tool_executor.execute_tool("validate_draft_package", args, agent="analyst", domain="general")
        try:
            res = json.loads(check)
        except ValueError:
            return {"ok": False, "valid": False, "errors": [check]}, check
        if not res.get("valid"):
            return res, (menu_tools.validation_markdown(res) if res.get("ok") else check)
        return res, await tool_executor.execute_tool("render_review_sheet", args, agent="analyst", domain="general")
    return asyncio.run(go())


def run_day(folder, *, business_day=None, rate=DEFAULT_RATE, day_status="open", correction_reason=None, drafts_dir=None, client=None, use_cache=True,
            review=True, log=print, glossary=None, meat_sheet=None, restaurant_root=None):
    """Run the whole pipeline for one day folder. Returns a dict with the draft's path, its validation, and the review sheet."""
    folder = os.path.normpath(folder)
    if not os.path.isdir(folder):
        raise PipelineError(f"{folder} is not a folder")
    day = business_day or day_from_folder(folder)
    if not day:
        raise PipelineError("the business day is not in the folder name (expected D-MM-YYYY); pass --day YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        raise PipelineError(f"{day!r} is not a date (YYYY-MM-DD)")
    if day_status not in ("open", "ended"):
        raise PipelineError("day_status must be open or ended")
    if day_status == "ended" and not (correction_reason or "").strip():
        raise PipelineError("an ended day needs a correction reason (the app's correction mode asks for one)")
    drafts_dir = drafts_dir or DRAFTS_DIR
    if glossary is None:
        g = menu_tools.get_menu_glossary()
        if not g.get("ok"):
            raise PipelineError("the menu glossary cannot be loaded: " + "; ".join(g.get("errors") or []))
        glossary = g
    log(f"[1/5] reading the photos of {day} ({folder})")
    read = V.read_day(folder, client=client, use_cache=use_cache, log=log, fallback_files=central_files(day, restaurant_root))
    docs = read["documents"]
    log("[2/5] matching items and [3/5] doing the math")
    pkg, rep = build_package(docs, folder, day, rate=rate, day_status=day_status, correction_reason=correction_reason, glossary=glossary, drafts_dir=drafts_dir, meat_sheet=meat_sheet, restaurant_root=restaurant_root)
    for e in read["errors"]:
        pkg["flags"].append(f"BLOCKER: {e['file']} could not be read ({e['error']})")
    pkg["files_not_read"] = {"duplicates_of_a_photo": [list(d) for d in read["files"]["duplicates"]], "zero_byte_files": read["files"]["empty"], "not_a_menu_report_photo": [d["file"] for d in docs if d.get("document_type") == "other"],
                             "supply_or_expense_lists": [d["file"] for d in docs if d.get("document_type") == "supply_order_list"],
                             "classified_but_unusable": [[d["file"], d["document_type"]] for d in docs if d.get("comparison") and V.usable_score(d) == 0],
                             "spreadsheets_and_documents": [os.path.basename(p) for p in read["files"]["sheets"]] + read["files"]["other"]}
    log("[4/5] saving the draft package")
    path = os.path.join(drafts_dir, pkg["draft_id"] + ".json")
    _write_new(path, json.dumps(pkg, ensure_ascii=False, indent=1))
    result = {"ok": True, "business_day": day, "draft_id": pkg["draft_id"], "package_path": path, "package": pkg, "validation": None, "review_path": None, "review_markdown": None, **rep}
    if review:
        log("[5/5] the analyst validates the draft and renders the review sheet")
        if os.path.normcase(drafts_dir) == os.path.normcase(DRAFTS_DIR):
            validation, text = analyst_review(draft_id=pkg["draft_id"])
        else:
            validation, text = analyst_review(package=pkg)
        result["validation"], result["review_markdown"] = validation, text
        if validation.get("valid") and isinstance(text, str) and text.lstrip().startswith("#"):
            result["review_path"] = os.path.join(drafts_dir, pkg["draft_id"] + ".review.md")
            _write_new(result["review_path"], text)
    return result


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="one business day's folder (photos, spreadsheets)")
    ap.add_argument("--day", help="YYYY-MM-DD (default: read from the folder name D-MM-YYYY)")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE, help="L.L. per USD for the day (default %(default)s)")
    ap.add_argument("--day-status", choices=("open", "ended"), default="open")
    ap.add_argument("--correction-reason", help="required with --day-status ended")
    ap.add_argument("--drafts-dir", help="where to save the draft (default %s)" % DRAFTS_DIR)
    ap.add_argument("--meat-sheet", help="the consumption .xlsx to read the meat weights from (default: the latest-dated version that has the day)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-review", action="store_true")
    a = ap.parse_args(argv)
    err = lambda m: print(m, file=sys.stderr, flush=True)
    try:
        r = run_day(a.folder, business_day=a.day, rate=a.rate, day_status=a.day_status, correction_reason=a.correction_reason, drafts_dir=a.drafts_dir, meat_sheet=a.meat_sheet,
                    use_cache=not a.no_cache, review=not a.no_review, log=err)
    except (PipelineError, V.VisionError) as e:
        err(f"ERROR: {e}")
        return 2
    err(f"draft: {r['package_path']}")
    if r["review_path"]:
        err(f"review sheet: {r['review_path']}")
    if r["review_markdown"]:
        sys.stdout.reconfigure(encoding="utf-8")
        print(r["review_markdown"])
    return 0 if (r["validation"] is None or r["validation"].get("valid")) else 1


if __name__ == "__main__":
    sys.exit(main())
