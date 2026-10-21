"""
restaurant_fresh_score.py -- the exit-test scoreboard of Job 1 for the days entered through the Restaurant page (Oct 8 onward).

Read-only. For every day that has an approved draft and the manager's line answers it reports:
  1. LINE ACCURACY (first reading, before any help): matched lines + held lines the manager confirmed = right; held lines he corrected = wrong;
     lines the reader could not place ("needs human") = wrong. A matched line the manager did not touch counts as right (he approves the whole sheet).
  2. MEAT CROSS-CHECK (needs nobody's opinion): the kilos implied by the item lines against the company's own consumption sheet for that day.
     A misread line moves the kilos, so a small gap is independent evidence that the lines are right.
  3. TOTALS: cash expected vs counted, meat variance, and whether the live app still equals the approved draft (read-only check) when asked for.

  py scripts\\restaurant_fresh_score.py [--live] [--out report.md]
Exit rule (agents/jobs/01_restaurant_ops/JOB.md): about 20 fresh days at >= 98% exact lines, every total mismatch flagged.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA, DAVID_HOME, DAVID_RESTAURANT_ROOT
import argparse
import datetime
import glob
import json
import os
import sys

import openpyxl

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

DAYS_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant_day")
RESTAURANT = str(DAVID_RESTAURANT_ROOT)
TARGET_DAYS, TARGET_PCT = 20, 98.0
MEAT_OK_PCT = 2.0
MEAT_OK_KG = 0.5          # the scale drifts by a few hundred grams either way: the manager accepts up to this (2026-10-13, reference/tolerances.md)
# days the system had NOT learned from when it first read them (the manager's answers to earlier days taught it spellings)
FRESH_FROM = "2026-10-09"


def latest_draft(day):
    files = sorted(glob.glob(os.path.join(DAYS_DIR, day, "DRAFT-*-0*.json")))
    return json.load(open(files[-1], encoding="utf-8")) if files else None


def sheet_sold(day):
    """kg sold (chicken, beef) from the company's consumption sheet in the day's own folder."""
    d = datetime.date.fromisoformat(day)
    folder = os.path.join(RESTAURANT, f"{d.month:02d}-{d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}")
    files = glob.glob(os.path.join(folder, "Branch Beef*.xlsx"))
    if not files:
        return None
    ws = openpyxl.load_workbook(files[0], data_only=True).worksheets[0]
    out = {}
    for r in range(1, ws.max_row + 1):
        a, b = ws.cell(r, 1).value, ws.cell(r, 2).value
        if isinstance(a, datetime.datetime) and a.date() == d and b in ("Chicken", "Beef"):
            out[b.lower()] = ws.cell(r, 9).value
        elif isinstance(a, str) and a.strip() == f"{d.day:02d}/{d.month:02d}" and b in ("Chicken", "Beef"):
            out[b.lower()] = ws.cell(r, 9).value
    return out if len(out) == 2 else None


def score_day(day):
    draft = latest_draft(day)
    ap = os.path.join(DAYS_DIR, day, "manager_line_answers.json")
    if not draft or not os.path.isfile(ap):
        return None
    lines = draft["extraction"]["summary_by_items"]["lines"]
    answers = json.load(open(ap, encoding="utf-8"))["answers"]
    corrected = {a["id"].split("/")[1] for a in answers if a["answer"] == "corrected" and not a["id"].endswith("/alt")}
    confirmed = {a["id"].split("/")[1] for a in answers if a["answer"] == "ok"}
    st = {"matched": 0, "suggested": 0, "needs_human": 0}
    for ln in lines:
        st[ln["match_status"]] = st.get(ln["match_status"], 0) + 1
    wrong = [ln for ln in lines if ln["match_status"] == "needs_human" or str(ln["line"]) in corrected]
    right = len(lines) - len(wrong)
    disagree = sum(1 for ln in lines if not ln.get("readings_agree", True))
    comp = draft["computed"]
    mfl = comp["meat_from_lines"]["with_suggested"]
    sold = sheet_sold(day)
    meat = {}
    for m in ("chicken", "beef"):
        if sold and sold.get(m):
            gap = mfl[m] - sold[m]
            meat[m] = {"lines_kg": round(mfl[m], 2), "sheet_kg": round(sold[m], 2), "gap_kg": round(gap, 2), "gap_pct": round(100 * gap / sold[m], 1)}
    return {"day": day, "lines": len(lines), "matched": st["matched"], "suggested": st["suggested"], "needs_human": st["needs_human"], "readings_disagree": disagree,
            "confirmed": len(confirmed), "corrected": len(corrected), "right": right, "pct": round(100 * right / len(lines), 1),
            "fresh": day >= FRESH_FROM, "meat": meat, "cash_diff": comp.get("diff"), "cash_expected": comp.get("expected"), "cash_counted": comp.get("counted"),
            "meat_variance": {m: comp["meat_variance"][m]["variance_kg"] for m in ("chicken", "beef")}}


def live_status(days):
    import restaurant_upload as U
    out = {}
    for d in days:
        try:
            r = U.live_check(d)
            out[d] = "matches" if r["ok"] else "DIFFERS: " + "; ".join(r["differences"])[:200]
        except Exception as e:
            out[d] = "not checked: " + str(e)[:80]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also compare each day with the live app (read-only)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    days = sorted(d for d in os.listdir(DAYS_DIR) if d.startswith("2026-") and score_day(d))
    rows = [score_day(d) for d in days]
    live = live_status(days) if a.live else {}
    fresh = [r for r in rows if r["fresh"]]
    L = ["# Restaurant exit-test scoreboard", f"Made {datetime.datetime.now():%Y-%m-%d %H:%M}. Days scored: {len(rows)}; fresh (not learned from before they were read): {len(fresh)} of the {TARGET_DAYS} needed.", "",
         "| Day | Lines | Right first time | % | Held lines (confirmed / corrected) | Readings disagreed | Chicken: lines vs company sheet | Beef: lines vs company sheet | Cash diff | Live app |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        mt = lambda m: (f"{r['meat'][m]['lines_kg']} vs {r['meat'][m]['sheet_kg']} kg ({r['meat'][m]['gap_pct']:+}%)" if m in r["meat"] else "n/a")
        L.append(f"| {r['day']}{'' if r['fresh'] else ' (in-sample)'} | {r['lines']} | {r['right']} | {r['pct']} | {r['suggested']} ({r['confirmed']} / {r['corrected']}) | {r['readings_disagree']} | {mt('chicken')} | {mt('beef')} | {r['cash_diff']} | {live.get(r['day'], '-')} |")
    tl, tr = sum(r["lines"] for r in fresh), sum(r["right"] for r in fresh)
    pct = round(100 * tr / tl, 1) if tl else 0.0
    L += ["", f"**Fresh days, lines right first time: {tr} of {tl} = {pct}%** (target {TARGET_PCT}% over about {TARGET_DAYS} days; so far {len(fresh)} day(s)).", ""]
    flags = []
    for r in rows:
        for m, v in r["meat"].items():
            if abs(v["gap_pct"]) > MEAT_OK_PCT and abs(v["gap_kg"]) > MEAT_OK_KG:
                flags.append(f"{r['day']}: {m} kilos from the lines differ from the company sheet by {v['gap_kg']:+} kg ({v['gap_pct']:+}%)")
        if r["cash_diff"] is not None and abs(r["cash_diff"]) > 5:
            flags.append(f"{r['day']}: cash counted differs from expected by {r['cash_diff']:+}")
        if r["needs_human"]:
            flags.append(f"{r['day']}: {r['needs_human']} line(s) the reader could not place")
    L += ["## Flags (every total mismatch)"] + ([f"- {f}" for f in flags] or ["- none"])
    L += ["", "## Reading of the numbers", "- 'Right first time' counts a matched line the manager did not touch as right; the held lines are the only ones he individually answered, so this is a measure of agreement with what he approved, not a line-by-line audit.",
          "- The meat columns are the independent check: kilos implied by the lines against the company's own sheet. A gap is flagged only when it is above " + str(MEAT_OK_PCT) + "% AND above " + str(MEAT_OK_KG) + " kg (the scale drifts by a few hundred grams; see reference/tolerances.md)."]
    text = "\n".join(L)
    print(text)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text + "\n")
    return rows


if __name__ == "__main__":
    main()
