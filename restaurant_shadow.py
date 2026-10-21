"""
restaurant_shadow.py -- shadow-period runner for Job 1: run the pipeline on past days and compare each draft with what the manager actually
entered into the app. Nothing is written to the app, nothing is approved; the drafts stay in their own folder.

    py -3.11 restaurant_shadow.py 2026-09-01 2026-09-13            run every day folder in the range, then write the report
    py -3.11 restaurant_shadow.py 2026-09-01 2026-09-13 --report-only   rebuild the report from drafts already made
    py -3.11 restaurant_shadow.py --score <DAVID_HOME>\\jobs_drafts\\restaurant_shadow\\shadow_lines.csv    score the lines the manager reviewed

Ground truth = the newest `app\\backup\\<date>\\*.json` export of the app's database (read-only; the app's own backup script makes it). The
manager typed those days into the app from these same photos, so the cash figures can be compared to the cent. Item lines have NO stored
truth in the app, so the report writes `shadow_lines.csv`: one row per line with the system's reading and match, and two blank columns
(correct_item_id, correct_qty). The manager fills only the corrections; `--score` then counts a line as exactly right when it was
matched or suggested (not "needs human"), its item is the right one, and its quantity is right. Blank means "I agree", so review the
sheet before leaving columns blank.

The exit rule from agents/jobs/01_restaurant_ops/JOB.md: about 20 days, at least 98% exact line accuracy, every total mismatch flagged.
"""

from david_config import DAVID_DATA
import csv
import datetime
import glob
import json
import os
import re
import sys

import menu_tools
import restaurant_pipeline as P

RESTAURANT = menu_tools.RESTAURANT_ROOT
SHADOW_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant_shadow")
TOL = 0.005
CASH_FIELDS = ("pos_sales", "wallet", "expenses_total", "transfer_to_admin", "old_cash", "counted", "expected", "diff", "meat_received_chicken", "meat_received_beef",
               "meat_start_chicken", "meat_start_beef")
ROUNDING_NOTE = "a difference of 1 or 2 cents is shown as rounding, not as a failure (the manager rounds each figure he types; the pipeline keeps what the receipt prints)"
ROUNDING_TOLERANCE = 0.025


def latest_backup(restaurant_root=None):
    base = os.path.join(restaurant_root or RESTAURANT, "app", "backup")
    days = sorted(d for d in os.listdir(base) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and os.path.isfile(os.path.join(base, d, "days.json")))
    if not days:
        raise FileNotFoundError(f"no app backup under {base}")
    return os.path.join(base, days[-1])


def _load(backup, name):
    with open(os.path.join(backup, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def load_truth(backup, day):
    """What the app recorded for one business day, or None if the app has no such day."""
    days = [d for d in _load(backup, "days") if d["on_date"] == day]
    if not days:
        return None
    did = days[0]["id"]
    sheets = [c for c in _load(backup, "cash_sheets") if c["day_id"] == did]
    receipts = {}
    for m in _load(backup, "meat_receipts"):
        if m["day_id"] == did:
            receipts[m["meat_type"]] = receipts.get(m["meat_type"], 0.0) + float(m["received_kg"])
    try:
        bs = (_load(backup, "branch_settings") or [{}])[0]
    except (OSError, ValueError, IndexError):
        bs = {}
    return {"baseline": {"date": bs.get("beef_stock_baseline_date"), "beef": bs.get("beef_stock_baseline_kg"), "chicken": bs.get("chicken_stock_baseline_kg")},
            "day": days[0], "cash_sheet": sheets[0] if sheets else None, "expenses": [e for e in _load(backup, "cash_expenses") if e["day_id"] == did],
            "transfers": [t for t in _load(backup, "cash_transfers") if t["day_id"] == did], "meat_received": receipts}


MEAT_FIELDS = ("meat_received_chicken", "meat_received_beef", "meat_start_chicken", "meat_start_beef")


def day_of(truth):
    return truth["day"]["on_date"]


def compare_day(pkg, truth):
    """[{field, draft, app, result}] result: match | rounding | differs | not_read (the draft has no value) | not_in_app.
    Money is compared to half a cent; a gap of up to 2.5 cents on a money figure is 'rounding' (the manager rounds each figure he types, the
    pipeline keeps what the receipt prints), shown separately and never a failure. Weights are never given that allowance."""
    cs = truth["cash_sheet"] if truth else None
    c, comp = pkg["cash"], pkg["computed"]
    exp = [e["amount"] for e in c.get("expenses") or []]
    draft = {"pos_sales": c.get("pos_sales"), "wallet": c.get("wallet"), "expenses_total": round(sum(exp), 2) if exp and all(x is not None for x in exp) else (0.0 if c.get("expenses") == [] else None),
             "transfer_to_admin": c.get("transfer_to_admin"), "old_cash": c.get("old_cash"), "counted": c.get("actual_cash_count"), "expected": comp.get("expected"), "diff": comp.get("diff"),
             "meat_received_chicken": ((pkg["cfo_results"]["meat"].get("chicken") or {}).get("inputs") or {}).get("added_kg"),
             "meat_received_beef": ((pkg["cfo_results"]["meat"].get("beef") or {}).get("inputs") or {}).get("added_kg"),
             "meat_start_chicken": ((pkg["cfo_results"]["meat"].get("chicken") or {}).get("inputs") or {}).get("starting_kg"),
             "meat_start_beef": ((pkg["cfo_results"]["meat"].get("beef") or {}).get("inputs") or {}).get("starting_kg")}
    app = {}
    if truth:
        app = {"pos_sales": cs and cs["sales_usd"], "wallet": cs and cs["count_wallet_usd"], "expenses_total": round(sum(float(e["amount_usd"]) for e in truth["expenses"]), 2),
               "transfer_to_admin": round(sum(float(t["amount_usd"]) for t in truth["transfers"]), 2) if truth["transfers"] else (cs and cs["transfer_to_admin_usd"]),
               "old_cash": cs and cs["safe_old_usd"], "counted": cs and cs["safe_counted_usd"], "expected": cs and cs["expected_usd"], "diff": cs and cs["diff_usd"],
               "meat_received_chicken": truth["meat_received"].get("chicken"), "meat_received_beef": truth["meat_received"].get("beef"),
               # the app's baseline is the stock on its first day and INCLUDES the beef tallow (24.14 kg = 21.14 + 3 kg), so it is only a truth for that day
               "meat_start_chicken": truth["baseline"]["chicken"] if truth["baseline"]["date"] == day_of(truth) else None,
               "meat_start_beef": truth["baseline"]["beef"] if truth["baseline"]["date"] == day_of(truth) else None}
    # the app's cash count of a day is 0 while the app itself expects cash in the safe: the count was not entered (yet), so it is no truth to compare with
    unfinished = bool(truth and cs and float(cs.get("safe_counted_usd") or 0) == 0 and float(cs.get("expected_usd") or 0) > 0)
    if unfinished:
        app["counted"] = app["expected"] = app["diff"] = None
    rows = []
    for f in CASH_FIELDS:
        d, a = draft[f], app.get(f)
        gap = None if (truth is None or a is None or d is None) else abs(float(d) - float(a))
        res = "not_in_app" if (truth is None or a is None) else "not_read" if d is None else "match" if gap <= TOL else "rounding" if (gap <= ROUNDING_TOLERANCE and f not in MEAT_FIELDS) else "differs"
        rows.append({"field": f, "draft": d, "app": a, "result": res})
        if unfinished and f in ("counted", "expected", "diff"):
            rows[-1]["note"] = "the app's cash count of this day is 0: not entered yet, so not compared"
    return rows


def day_folders(start, end, restaurant_root=None):
    a, b = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    out, d = [], a
    while d <= b:
        for pat in (f"{d.day}-{d.month:02d}-{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}"):
            p = os.path.join(restaurant_root or RESTAURANT, f"{d.month:02d}-{d.year}", pat)
            if os.path.isdir(p):
                out.append((d.isoformat(), p))
                break
        d += datetime.timedelta(days=1)
    return out


def _summary(pkg):
    lines = pkg["extraction"]["summary_by_items"]["lines"]
    st = {"matched": 0, "suggested": 0, "needs human": 0}
    for l in lines:
        s = l.get("match_status", "needs human")
        if not l.get("readings_agree"):
            s = "needs human"
        st[s if s in st else "needs human"] += 1
    sev = {"BLOCKER": 0, "CHECK": 0, "NOTE": 0}
    for f in pkg["flags"]:
        sev[f.split(":")[0] if f.split(":")[0] in sev else "CHECK"] += 1
    return {"lines": len(lines), **st, "disagree": sum(1 for l in lines if not l.get("readings_agree")), "flags": sev}


def build_report(entries, out_dir):
    """entries: [{day, package, rows, error}] -> (markdown, lines_csv_rows). Also the worklist of names that need a human, grouped."""
    md, csv_rows, names = ["# Shadow run: drafts against the app", ""], [], {}
    md += ["| Day | Draft | Fields matched | Rounding | Not read | Differs | Lines | matched | suggested | human | readings differ | BLOCKER | CHECK |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = {"match": 0, "rounding": 0, "differs": 0, "not_read": 0, "not_in_app": 0}
    for e in entries:
        if e.get("error"):
            md.append(f"| {e['day']} | ERROR: {e['error']} | | | | | | | | | | |")
            continue
        pkg, rows = e["package"], e["rows"]
        cnt = {k: sum(1 for r in rows if r["result"] == k) for k in tot}
        for k in tot:
            tot[k] += cnt[k]
        s = _summary(pkg)
        md.append(f"| {e['day']} | {pkg['draft_id']} | {cnt['match'] + cnt['rounding']}/{len(rows) - cnt['not_in_app']} | {cnt['rounding']} | {cnt['not_read']} | {cnt['differs']} | {s['lines']} | {s['matched']} | {s['suggested']} | {s['needs human']} | {s['disagree']} | {s['flags']['BLOCKER']} | {s['flags']['CHECK']} |")
        for l in pkg["extraction"]["summary_by_items"]["lines"]:
            m = l.get("match") or {}
            csv_rows.append({"day": e["day"], "draft_id": pkg["draft_id"], "line": l["line"], "name_as_read": l["name_ar"], "name_other_reading": l.get("name_ar_alt", ""), "qty": l["qty"],
                             "qty_other_reading": l.get("qty_alt", ""), "readings_agree": l["readings_agree"], "status": l.get("match_status", ""), "matched_item_id": m.get("item_id") or "",
                             "matched_name": m.get("name_ar") or "", "score": m.get("score", ""), "correct_item_id": "", "correct_qty": ""})
            if l.get("match_status") != "matched" or not l.get("readings_agree"):
                key = menu_tools.normalize(l["name_ar"])
                n = names.setdefault(key, {"name": l["name_ar"], "times": 0, "qty": 0.0, "best": m.get("name_ar") or "", "score": m.get("score", 0)})
                n["times"] += 1
                n["qty"] += l["qty"] or 0
    md += ["", f"Cash and meat fields over all days: **{tot['match']} match**, {tot['rounding']} within rounding (1 to 2 cents), {tot['differs']} differ, {tot['not_read']} not read (the app has a value), {tot['not_in_app']} not in the app.", f"_{ROUNDING_NOTE}._", ""]
    bad = [(e["day"], r) for e in entries if not e.get("error") for r in e["rows"] if r["result"] in ("differs", "not_read", "rounding")]
    if bad:
        md += ["## Fields that differ from the app, were rounded differently, or were not read", "| Day | Field | Draft | App | Result |", "|---|---|---|---|---|"]
        md += [f"| {d} | {r['field']} | {r['draft']} | {r['app']} | {r['result']} |" for d, r in bad]
        md.append("")
    unfinished_days = [e["day"] for e in entries if not e.get("error") and any(r.get("note") for r in e["rows"])]
    if unfinished_days:
        md += ["## Days whose entry in the app looks unfinished", "The app's cash count is 0 for: " + ", ".join(unfinished_days) + ". The count, the expected cash and the difference of those days are not compared; the other figures are. "
               "Check the day in the app: if its sales or wallet differ from the receipts, it may simply not be completed.", ""]
    work = sorted(names.values(), key=lambda n: (-n["times"], -n["qty"]))
    md += ["## Receipt names that were not matched exactly (most frequent first)", "These are what to confirm in `reference/menu_aliases.json`, or fix on the sheet.", "",
           "| Name as read | Days | Total qty | Best candidate | Score |", "|---|---|---|---|---|"]
    md += [f"| {n['name']} | {n['times']} | {n['qty']:g} | {n['best']} | {n['score']} |" for n in work[:60]]
    md += ["", f"Item lines have no stored truth in the app: review `shadow_lines.csv` (fill correct_item_id / correct_qty only where the system is wrong) and score it with `--score`."]
    return "\n".join(md), csv_rows


def write_lines_csv(path, rows):
    cols = ["day", "draft_id", "line", "name_as_read", "name_other_reading", "qty", "qty_other_reading", "readings_agree", "status", "matched_item_id", "matched_name", "score", "correct_item_id", "correct_qty"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)


def score_line_review(path):
    """Line accuracy from the manager's reviewed CSV. Exactly right = status matched/suggested AND readings agree AND item right AND qty right
    (blank correction = agrees). Also reports how many lines the system sent to a human, and how many it got right when it did not."""
    n = ok = human = wrong_item = wrong_qty = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            n += 1
            sure = r["status"] in ("matched", "suggested") and str(r["readings_agree"]).lower() == "true"
            item_ok = (r["correct_item_id"].strip() in ("", r["matched_item_id"]))
            qty_ok = (r["correct_qty"].strip() == "" or abs(float(r["correct_qty"]) - float(r["qty"] or 0)) < 1e-9)
            human += not sure
            if sure and item_ok and qty_ok:
                ok += 1
            wrong_item += sure and not item_ok
            wrong_qty += sure and not qty_ok
    return {"lines": n, "exactly_right": ok, "sent_to_a_human": human, "confident_but_wrong_item": wrong_item, "confident_but_wrong_qty": wrong_qty,
            "exact_accuracy": round(ok / n, 4) if n else None, "accuracy_of_the_lines_it_was_sure_about": round(ok / (n - human), 4) if n > human else None}


def run_shadow(start, end, *, out_dir=SHADOW_DIR, restaurant_root=None, rate=P.DEFAULT_RATE, report_only=False, client=None, log=print, backup=None, meat_sheet=None):
    os.makedirs(out_dir, exist_ok=True)
    backup = backup or latest_backup(restaurant_root)
    entries = []
    for day, folder in day_folders(start, end, restaurant_root):
        e = {"day": day, "folder": folder}
        try:
            if report_only:
                found = sorted(glob.glob(os.path.join(out_dir, f"DRAFT-{day}-*.json")))
                if not found:
                    raise P.PipelineError("no draft yet for this day")
                e["package"] = json.load(open(found[-1], encoding="utf-8"))
            else:
                log(f"== {day}")
                r = P.run_day(folder, business_day=day, rate=rate, day_status="ended", correction_reason="shadow run: compared with the app, nothing is entered", drafts_dir=out_dir, client=client, log=log, meat_sheet=meat_sheet, restaurant_root=restaurant_root)
                e["package"] = r["package"]
            e["rows"] = compare_day(e["package"], load_truth(backup, day))
        except (P.PipelineError, OSError, ValueError) as ex:
            e["error"] = str(ex)
        entries.append(e)
    md, rows = build_report(entries, out_dir)
    stamp = f"{start}_{end}"
    open(os.path.join(out_dir, f"shadow_report_{stamp}.md"), "w", encoding="utf-8").write(md)
    write_lines_csv(os.path.join(out_dir, "shadow_lines.csv"), rows)
    return {"entries": entries, "report": md, "csv_rows": rows, "out_dir": out_dir}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("start", nargs="?")
    ap.add_argument("end", nargs="?")
    ap.add_argument("--out", default=SHADOW_DIR)
    ap.add_argument("--rate", type=float, default=P.DEFAULT_RATE)
    ap.add_argument("--meat-sheet", help="read every day's meat weights from this consumption .xlsx (default: the latest-dated version that has the day)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--score", metavar="CSV", help="score a reviewed shadow_lines.csv instead of running")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.score:
        print(json.dumps(score_line_review(a.score), ensure_ascii=False, indent=1))
        return 0
    if not (a.start and a.end):
        ap.error("give START and END dates (YYYY-MM-DD)")
    r = run_shadow(a.start, a.end, out_dir=a.out, rate=a.rate, report_only=a.report_only, meat_sheet=a.meat_sheet, log=lambda m: print(m, file=sys.stderr, flush=True))
    print(r["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
