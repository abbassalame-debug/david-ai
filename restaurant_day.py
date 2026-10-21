"""
restaurant_day.py -- Job 1: the DAILY RUNNER. One command per step, for one business day, from the photos to a checked SQL file the manager runs himself.

    py restaurant_day.py prepare 2026-10-08     read the day's photos and files, make the draft, write the questions only the manager can answer
    py restaurant_day.py finish  2026-10-08     (after he answered: answers.json) make the approval, the SQL, rehearse it, write the steps
    py restaurant_day.py check   2026-10-08     (after he ran it and exported a fresh backup) compare the app with the draft

Everything is local. Nothing here connects to Supabase or writes the app's database: the manager reads and runs `apply.sql` himself.
The folders: drafts and approvals in `<DAVID_HOME>\\jobs_drafts\\restaurant_day\\<day>\\`, what the manager needs in `<DAVID_HOME>\\for_review\\day_<day>\\`.
"""
from david_config import DAVID_DATA
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sys

import menu_tools
import restaurant_corrections as RC
import restaurant_pipeline as P
import restaurant_rehearsal as RH
import restaurant_shadow as SH
import restaurant_sql as S
import restaurant_weights as RW

DAYS_DIR = str(DAVID_DATA / "jobs_drafts" / "restaurant_day")
INBOX_DIR = str(DAVID_DATA / "restaurant_inbox")
REVIEW_DIR = str(DAVID_DATA / "for_review")
BIG_EXPENSE = 100


class DayError(Exception):
    pass


def _day(day):
    try:
        datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise DayError(f"{day!r} is not a date (YYYY-MM-DD)")
    return day


def day_dir(day, days_dir=None):
    return os.path.join(days_dir or DAYS_DIR, day)


def pack_dir(day, review_dir=None):
    return os.path.join(review_dir or REVIEW_DIR, f"day_{day}")


def source_folder(day, restaurant_root=None, inbox_dir=None):
    """(folder, where): the day's uploaded folder in David's own inbox when it has files, else the day's folder in the Demo Bistro tree. The Demo Bistro tree is only ever read."""
    box = os.path.join(inbox_dir or INBOX_DIR, day)
    if os.path.isdir(box) and any(os.path.isfile(os.path.join(box, f)) for f in os.listdir(box)):
        return box, "inbox"
    found = SH.day_folders(day, day, restaurant_root)
    if found:
        return found[0][1], "abo"
    raise DayError(f"there is no folder for {day}: upload the day's files, or put them in the Demo Bistro folder")


def sql_editor_url(restaurant_root=None):
    """The project's SQL editor address, from the app's own env files (the address only; no key is ever read or printed)."""
    root = restaurant_root or menu_tools.RESTAURANT_ROOT
    for name in (".env.production", ".env.local", ".env"):
        p = os.path.join(root, "app", name)
        try:
            m = re.search(r"https://([a-z0-9]+)\.supabase\.co", open(p, encoding="utf-8", errors="ignore").read())
        except OSError:
            continue
        if m:
            return f"https://supabase.com/dashboard/project/{m.group(1)}/sql/new"
    return "https://supabase.com/dashboard (open your project, then SQL Editor, then New query)"


def latest_draft(day, days_dir=None):
    found = sorted(glob.glob(os.path.join(day_dir(day, days_dir), f"DRAFT-{day}-*.json")))
    found = [f for f in found if not f.endswith(".approval.json")]
    if not found:
        raise DayError(f"no draft for {day} yet: run `py restaurant_day.py prepare {day}` first")
    return found[-1]


def _flags(pkg, prefix):
    return [f for f in pkg.get("flags") or [] if f.startswith(prefix)]


def _num(v):
    """A quantity for the questions text; a line whose quantity was not read shows ? instead of stopping the whole day."""
    return "?" if v is None else f"{v:g}"


def _held_lines(pkg):
    out = []
    for l in pkg["extraction"]["summary_by_items"]["lines"]:
        if l.get("match_status") != "matched" or not l.get("readings_agree"):
            m = l.get("match") or {}
            out.append({"line": l["line"], "name": l["name_ar"], "alt": l.get("name_ar_alt"), "qty": l.get("qty"), "qty_alt": l.get("qty_alt"),
                        "guess": m.get("name_ar"), "item": m.get("item_id"), "score": m.get("score"), "status": l.get("match_status")})
    return out


def _chain(day, backup):
    """(text, ok) about the day before's New against today's Old, from the backup."""
    prev = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
    d = next((x for x in backup["days"] if x.get("on_date") == prev), None)
    sh = next((s for s in backup["cash_sheets"] if d and s.get("day_id") == d["id"]), None)
    return (prev, sh.get("safe_new_usd") if sh else None)


def questions_for(pkg, weights_notes, weights_flags, backup, truth, weights=None):
    day = pkg["business_day"]
    c, comp = pkg["cash"], pkg["computed"]
    meat = pkg["cfo_results"]["meat"]
    q = {"figures": {}, "checks": _flags(pkg, "CHECK:"), "blockers": _flags(pkg, "BLOCKER:"), "held_lines": _held_lines(pkg), "deliveries": {}, "expenses": [], "notes": [], "warnings": []}
    q["figures"] = {"old_cash": c.get("old_cash"), "sales": c.get("pos_sales"), "wallet": c.get("wallet"), "transfer": c.get("transfer_to_admin"), "expenses_total": round(sum((e["amount"] or 0) for e in c.get("expenses") or []), 2),
                    "expected": comp.get("expected"), "counted": comp.get("counted"), "diff": comp.get("diff"),
                    "chicken": (meat.get("chicken") or {}).get("summary"), "beef": (meat.get("beef") or {}).get("summary")}
    q["deliveries"] = {m: (meat.get(m) or {}).get("inputs", {}).get("added_kg") for m in ("chicken", "beef")}
    for m in ("chicken", "beef"):
        spit = ((weights or {}).get(m) or {}).get("spit_kg")
        r = meat.get(m) or {}
        if spit and r.get("pos_sold_kg") is not None and r.get("raw_used_kg") is not None and abs(spit - r["raw_used_kg"]) > 0.5:
            cook = r.get("cook_yield") or 0.55
            yld, out = spit * cook, float(r["pos_sold_kg"]) + float(r.get("waste_kg") or 0)
            q["figures"][f"{m}_workbook"] = (f"With the weights workbook's {spit:g} kg on the spits (the ledger says {r['raw_used_kg']:g}): cooked yield {yld:.3f} kg against sold + waste {out:.3f} kg, "
                                             f"variance {out - yld:+.3f} kg ({(out - yld) / yld * 100:+.1f}%). The workbook wins, so this is the figure that matters.")
            q["warnings"].append(f"{m}: the company ledger's raw used ({r['raw_used_kg']:g} kg) is not the workbook's spit total ({spit:g} kg); the SQL uses the workbook's")
    q["expenses"] = [{"label": e["label"], "amount": e["amount"], "big": (e["amount"] or 0) >= BIG_EXPENSE} for e in c.get("expenses") or []]
    for e in c.get("expenses") or []:
        if e["amount"] is None:
            q["warnings"].append(f"the Daily Helper row «{e['label']}» has no amount I can read: tell me the amount (or that there was none). It is left out of the totals until then")
    q["cash"] = {k: c.get(k) for k in RC.CASH_FIELDS}
    q["meat"] = {mm: {"received_kg": (meat.get(mm) or {}).get("inputs", {}).get("added_kg"), "spit_kg": (weights or {}).get(mm, {}).get("spit_kg"),
                      "sold_kg": (meat.get(mm) or {}).get("pos_sold_kg"), "waste_kg": (meat.get(mm) or {}).get("waste_kg"), "closing": (weights or {}).get(mm, {}).get("closing")}
                 for mm in ("chicken", "beef") if meat.get(mm)}
    prev, prev_new = _chain(day, backup)
    if prev_new is None:
        q["notes"].append(f"yesterday ({prev}) is not in the newest backup, so the opening cash could not be checked against its closing")
    elif abs(float(prev_new) - float(c.get("old_cash") or 0)) > 0.005:
        q["warnings"].append(f"opening cash is {c.get('old_cash')} but {prev} closed with {prev_new}")
    else:
        q["notes"].append(f"opening cash {c.get('old_cash')} equals {prev}'s closing")
    if truth is not None and truth.get("cash_sheet") is not None:
        q["warnings"].append(f"{day} is ALREADY in the app (a cash sheet exists): the SQL would be corrections to what you entered, and each table needs your explicit allow-update")
    q["notes"] += weights_notes
    q["warnings"] += weights_flags
    return q


def questions_markdown(day, q, draft_path):
    L = [f"# {day}: what David needs from you", "", f"Draft: `{os.path.basename(draft_path)}`", "", "## The figures it read", ""]
    f = q["figures"]
    L += [f"- Opening cash {f['old_cash']}, sales {f['sales']}, wallet {f['wallet']}, expenses {f['expenses_total']}, transfer {f['transfer']}",
          f"- Expected {f['expected']}, counted {f['counted']}, difference {f['diff']}",
          f"- Chicken: {f['chicken']}", f"- Beef: {f['beef']}", ""]
    if q["warnings"]:
        L += ["## Look at these first", ""] + [f"- {w}" for w in q["warnings"]] + [""]
    n = 1
    L += ["## Questions", ""]
    d = q["deliveries"]
    L.append(f"{n}. **Deliveries.** Chicken received {d['chicken']} kg and beef {d['beef']} kg (from the company ledger; a beef delivery of 12 is 10 meat + 2 tallow). Are both right?")
    n += 1
    big = [e for e in q["expenses"] if e["big"]]
    L.append(f"{n}. **Expenses.** " + ", ".join(f"{e['label']} {e['amount'] if e['amount'] is not None else '(no amount read)'}" for e in q["expenses"]) + ". Which of them were paid from the SAFE and not the register? " + ("(Large ones: " + ", ".join(e["label"] for e in big) + ".) " if big else "") + "Everything else is written as the register.")
    n += 1
    if q["checks"]:
        L.append(f"{n}. **Warnings in the draft** (say 'ok' once you have looked, or tell me what is wrong):")
        L += [f"    - {c}" for c in q["checks"]]
        n += 1
    if q["blockers"]:
        L.append(f"{n}. **Blockers** (each needs a reason in your own words to be cleared):")
        L += [f"    - {i}. {b}" for i, b in enumerate(q["blockers"], 1)]
        n += 1
    if q["held_lines"]:
        L.append(f"{n}. **Item lines I am not sure of** ({len(q['held_lines'])}); do they match the receipt?")
        L += [f"    - line {h['line']}: '{h['name']}'" + (f" / '{h['alt']}'" if h["alt"] else "") + " x" + _num(h["qty"]) + (" (other reading x" + _num(h["qty_alt"]) + ")" if h["qty_alt"] is not None else "") + f" -> my guess: {h['guess']}" for h in q["held_lines"]]
        n += 1
    if q["notes"]:
        L += ["", "## For your information", ""] + [f"- {x}" for x in q["notes"]]
    return "\n".join(L) + "\n"


def answers_template(day, q):
    return {"day": day, "approved_by": "", "deliveries_confirmed": False, "acknowledge_checks": False, "paid_from_safe": [],
            "cleared_blockers": {str(i): "" for i in range(1, len(q["blockers"]) + 1)}, "allow_updates": [], "held_lines_all_correct": False,
            "confirmed": {}, "overrides": {}, "notes": {}}


def prepare(day, *, restaurant_root=None, backup=None, client=None, log=print, days_dir=None, review_dir=None, rate=P.DEFAULT_RATE, inbox_dir=None):
    _day(day)
    folder, where = source_folder(day, restaurant_root, inbox_dir)
    ddir = day_dir(day, days_dir)
    os.makedirs(ddir, exist_ok=True)
    json.dump({"folder": folder, "source": where, "prepared_at": datetime.datetime.now().isoformat(timespec="seconds")}, open(os.path.join(ddir, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    r = P.run_day(folder, business_day=day, rate=rate, day_status="ended", correction_reason="daily runner", drafts_dir=ddir, client=client, log=log, restaurant_root=restaurant_root)
    pkg = r["package"]
    bk = S.load_backup(backup or S.latest_backup(restaurant_root))
    truth = SH.load_truth(bk["_name"], day) if isinstance(bk["_name"], str) and os.path.isdir(bk["_name"]) else None
    m = pkg["cfo_results"]["meat"]
    led = {k: (m.get(k) or {}).get("inputs", {}).get("added_kg") for k in ("chicken", "beef")}
    waste = {k: (m.get(k) or {}).get("waste_kg") for k in ("chicken", "beef")}
    w, notes, flags = RW.meat_weights_for_approval(day, led, waste, restaurant_root, [folder])
    if w is None:
        flags = [f"no weights workbook for {day}: spits and closing weights cannot be written (" + "; ".join(flags) + ")"]
        notes = []
    q = questions_for(pkg, notes, flags, bk, truth, w)
    if not (r.get("validation") or {}).get("valid"):
        q["warnings"].insert(0, "the draft did NOT pass the analyst's validation: " + "; ".join(str(e) for e in ((r.get("validation") or {}).get("errors") or [])[:3]))
    pdir = pack_dir(day, review_dir)
    os.makedirs(pdir, exist_ok=True)
    md = questions_markdown(day, q, r["package_path"])
    open(os.path.join(pdir, "questions.md"), "w", encoding="utf-8").write(md)
    json.dump({"day": day, "draft": os.path.basename(r["package_path"]), "source": where, "valid": bool((r.get("validation") or {}).get("valid")), **q}, open(os.path.join(ddir, "questions.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    answers_path = os.path.join(pdir, "answers.json")
    if not os.path.exists(answers_path):
        json.dump(answers_template(day, q), open(answers_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"ok": True, "day": day, "draft_path": r["package_path"], "questions": q, "markdown": md, "answers_path": answers_path, "pack_dir": pdir, "valid": bool((r.get("validation") or {}).get("valid"))}


def _extra_dirs(day, days_dir=None):
    try:
        return [json.load(open(os.path.join(day_dir(day, days_dir), "meta.json"), encoding="utf-8"))["folder"]]
    except (OSError, ValueError, KeyError):
        return []


def _questions(day, days_dir=None):
    try:
        return json.load(open(os.path.join(day_dir(day, days_dir), "questions.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _approval_from_answers(draft, a, restaurant_root=None, extra_dirs=None, q=None):
    """(approval, notes, flags, corrected draft, [corrections]). Two ways to answer: the page's per-item way (`confirmed` + `overrides`: every item confirmed or corrected)
    and the older one (`deliveries_confirmed` + `acknowledge_checks`)."""
    errors = []
    if not str(a.get("approved_by") or "").strip():
        errors.append("answers.json: approved_by is empty (who is approving this day?)")
    try:
        ov = RC.validate(a.get("overrides"))
    except RC.Invalid as e:
        raise DayError(f"a correction is not valid: {e}")
    if "confirmed" in a:
        if q is None:
            errors.append("the day has not been read yet (no questions): run `prepare` first")
        else:
            left = RC.unsettled(q, a.get("confirmed"), ov)
            if left:
                errors.append(f"still to confirm or correct ({len(left)}): " + "; ".join(left))
    else:
        if a.get("deliveries_confirmed") is not True:
            errors.append("answers.json: deliveries_confirmed is not true (the ledger's received chicken and beef are the manager's to confirm)")
        if _flags(draft, "CHECK:") and a.get("acknowledge_checks") is not True:
            errors.append(f"answers.json: the draft has {len(_flags(draft, 'CHECK:'))} CHECK flag(s) and acknowledge_checks is not true")
    blockers = _flags(draft, "BLOCKER:")
    cleared = {}
    for i, b in enumerate(blockers, 1):
        why = str((a.get("cleared_blockers") or {}).get(str(i)) or "").strip()
        if not why:
            errors.append(f"answers.json: BLOCKER {i} has no reason: {b[:120]}")
        else:
            cleared[i] = why
    if errors:
        raise DayError("\n".join(errors))
    draft2, ch1 = RC.apply_cash(draft, ov)
    m = draft["cfo_results"]["meat"]
    led = {k: (m.get(k) or {}).get("inputs", {}).get("added_kg") for k in ("chicken", "beef")}
    waste = {k: (m.get(k) or {}).get("waste_kg") for k in ("chicken", "beef")}
    weights, notes, flags = RW.meat_weights_for_approval(draft["business_day"], led, waste, restaurant_root, extra_dirs)
    weights2, figs, ch2 = RC.split_meat(ov, weights)
    safe = set(a.get("paid_from_safe") or []) | {e["label"] for e in ov.get("expenses") or [] if e.get("safe")}
    if "expenses" in ov:
        safe = {l for l in safe if l in {e["label"] for e in ov["expenses"]}}
    acked = ("confirmed" in a) or bool(a.get("acknowledge_checks"))
    labels = dict(RC.required_items(q)) if q else {}
    notes_out = [f"{labels.get(k, k)}: {str(v).strip()}" for k, v in (a.get("notes") or {}).items() if isinstance(v, str) and v.strip()]
    approval = S.make_approval(draft2, a["approved_by"].strip(), ack_checks=acked, clear_blockers=cleared, meat_weights=weights2 or None, allow_updates=a.get("allow_updates") or None,
                               paid_from={l: "safe" for l in safe} or None, meat_figures=figs or None, corrections=ch1 + ch2 or None, manager_notes=notes_out or None)
    return approval, notes, flags, draft2, ch1 + ch2


def steps_markdown(day, plan_text, url, rehearsal):
    return f"""# Run {day}: step by step

David has checked this file on a throw-away copy of your tables (rehearsal: {rehearsal['checked']} check lines found, running it twice changes nothing, undo restores everything).
That does not replace the check table at the end of the real run.

1. Open the app and make sure {day} shows nothing entered and is not an open day.
2. Open the SQL editor: {url}
3. Open `apply.sql` (same folder as this file) in a text editor, read it, select ALL, copy.
4. Paste it into the SQL editor and press Run.
5. The table at the bottom must show `rows_found` = `rows_expected` on EVERY line ({rehearsal['checked']} lines). If something errors, do not retry: send me the message (the run is one transaction, nothing is half-written).
6. If anything looks wrong, paste `undo.sql` and run it instead (it leaves an empty {day} in place, on purpose).
7. Export a fresh backup, in your own PowerShell window (put your key in place of PASTE_KEY_HERE, never in chat):

```powershell
cd "<your app folder>"; $env:SUPABASE_SECRET_KEY = "PASTE_KEY_HERE"; node scripts\\export-backup.mjs
```

8. Tell me it is done, and David checks the app against the draft (`py restaurant_day.py check {day}`).

---

{plan_text}
"""


def finish(day, *, answers=None, restaurant_root=None, backup=None, days_dir=None, review_dir=None):
    _day(day)
    pdir = pack_dir(day, review_dir)
    a = answers if answers is not None else json.load(open(os.path.join(pdir, "answers.json"), encoding="utf-8"))
    dpath = latest_draft(day, days_dir)
    draft = json.load(open(dpath, encoding="utf-8"))
    approval, notes, flags, draft, changes = _approval_from_answers(draft, a, restaurant_root, _extra_dirs(day, days_dir), _questions(day, days_dir))
    bk = S.load_backup(backup or S.latest_backup(restaurant_root))
    res = S.build_sql(draft, approval, bk)
    out = {"ok": False, "status": res["status"], "reasons": res["reasons"], "needs_human": res["needs_human"], "info": res["info"] + notes + flags, "draft_path": dpath}
    if res["status"] == "nothing_to_do":
        out["ok"] = True
        return out
    if res["status"] != "sql_ready":
        return out
    reh = RH.rehearse(res["apply_sql"], res["undo_sql"], bk)
    out["rehearsal"] = reh
    if not reh["ok"]:
        out["status"], out["reasons"] = "rehearsal_failed", reh["problems"]
        return out
    ddir = day_dir(day, days_dir)
    json.dump(approval, open(os.path.join(ddir, "approval.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cpath = os.path.join(ddir, "corrected_draft.json")
    if changes:
        json.dump(draft, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elif os.path.exists(cpath):
        os.remove(cpath)
    written = S.write_outputs(res, os.path.join(ddir, "sql"), approval)
    os.makedirs(pdir, exist_ok=True)
    for p in written:
        if p.endswith(".sql") or p.endswith("plan.md"):
            shutil.copy(p, os.path.join(pdir, os.path.basename(p)))
    plan_text = S.plan_markdown(res, approval)
    open(os.path.join(pdir, "RUN_STEPS.md"), "w", encoding="utf-8").write(steps_markdown(day, plan_text, sql_editor_url(restaurant_root), reh))
    out.update(ok=True, files=[os.path.join(pdir, f) for f in ("apply.sql", "undo.sql", "plan.md", "RUN_STEPS.md")], pack_dir=pdir, plan=res["ops"])
    ov = RC.validate(a.get("overrides"))
    all_ok = a.get("held_lines_all_correct") is True
    ans = []
    for h in _held_lines(draft):
        fix = (ov.get("lines") or {}).get(str(h["line"]))
        item = (fix or {}).get("item") or (h["item"] if (fix is not None or all_ok or (a.get("confirmed") or {}).get(f"line.{h['line']}") is True) else None)
        if not item:
            continue
        qty = (fix or {}).get("qty", h["qty"])
        ans.append({"id": f"{day}/{h['line']}", "day": day, "name": h["name"], "qty": qty, "answer": "ok" if item == h["item"] else "corrected", "item": item, "note": "manager: held line" + (" corrected" if fix else " confirmed")})
        if h["alt"]:
            ans.append({"id": f"{day}/{h['line']}/alt", "day": day, "name": h["alt"], "qty": qty, "answer": "ok" if item == h["item"] else "corrected", "item": item, "note": "other reading of the same line"})
    if ans:
        ap = os.path.join(ddir, "manager_line_answers.json")
        json.dump({"by": a["approved_by"].strip(), "on": datetime.date.today().isoformat(), "review": f"{day} held lines the manager confirmed or corrected", "answers": ans}, open(ap, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        out["learn_command"] = f'py restaurant_learn.py --answers "{ap}" --apply'
    return out


def load_approved(day, days_dir=None):
    """(draft, approval) of a day whose SQL was made: the draft the approval's fingerprint belongs to (the corrected copy when the manager corrected figures, else the saved draft)."""
    ddir = day_dir(day, days_dir)
    apf = os.path.join(ddir, "approval.json")
    if not os.path.isfile(apf):
        raise DayError(f"no approval.json for {day}: `finish` was not run")
    approval = json.load(open(apf, encoding="utf-8"))
    cands = [os.path.join(ddir, "corrected_draft.json"), latest_draft(day, days_dir)]
    for c in cands:
        if os.path.isfile(c):
            d = json.load(open(c, encoding="utf-8"))
            if S.draft_sha(d) == approval.get("draft_sha256"):
                return d, approval
    raise DayError(f"the approval of {day} does not belong to any saved draft (a draft was made again after the SQL): make the SQL files again")


def check(day, *, restaurant_root=None, backup=None, days_dir=None, review_dir=None):
    _day(day)
    bpath = backup or S.latest_backup(restaurant_root)
    bk = S.load_backup(bpath)
    dpath = latest_draft(day, days_dir)
    draft = json.load(open(dpath, encoding="utf-8"))
    ddir = day_dir(day, days_dir)
    cpath = os.path.join(ddir, "corrected_draft.json")
    if os.path.isfile(cpath):
        corrected = json.load(open(cpath, encoding="utf-8"))
        apf = os.path.join(ddir, "approval.json")
        if os.path.isfile(apf) and json.load(open(apf, encoding="utf-8")).get("draft_sha256") == S.draft_sha(corrected):
            draft = corrected
    if not any(d.get("on_date") == day for d in bk["days"]):
        return {"ok": False, "status": "not_in_backup", "message": f"{day} is not in the newest backup ({os.path.basename(bpath)}): export a fresh one after running apply.sql"}
    ap = os.path.join(ddir, "approval.json")
    if not os.path.isfile(ap):
        raise DayError(f"no approval.json for {day}: `finish` was not run")
    approval = json.load(open(ap, encoding="utf-8"))
    res = S.build_sql(draft, approval, bk, validate=False)   # the approval's fingerprint pins this draft; it was validated when approved, and the menu memory may have grown since
    rows = SH.compare_day(draft, SH.load_truth(bpath, day)) if isinstance(bpath, str) and os.path.isdir(bpath) else []
    counts = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    out = {"ok": res["status"] == "nothing_to_do", "status": "matches" if res["status"] == "nothing_to_do" else res["status"], "field_counts": counts,
           "differences": [o["note"] for o in res["ops"]] + res["needs_human"] + res["reasons"], "backup": os.path.basename(str(bpath))}
    md = [f"# Check {day}: {out['status']}", "", f"Backup: {out['backup']}", f"Fields against the app: {counts}", ""]
    md += ["The app holds exactly what the draft said (every row)." if out["ok"] else "Differences:"] + [f"- {d}" for d in out["differences"]]
    pdir = pack_dir(day, review_dir)
    os.makedirs(pdir, exist_ok=True)
    open(os.path.join(pdir, "check.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    return out


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["prepare", "finish", "check"])
    ap.add_argument("day")
    a = ap.parse_args(argv)
    try:
        if a.step == "prepare":
            r = prepare(a.day, log=lambda m: print(m, file=sys.stderr, flush=True))
            print(r["markdown"])
            print(f"Fill in {r['answers_path']} (or tell David your answers), then: py restaurant_day.py finish {a.day}")
            return 0
        if a.step == "finish":
            r = finish(a.day)
            if r["status"] == "nothing_to_do":
                print("Nothing to do: the app already holds everything this draft says.")
                return 0
            if not r["ok"]:
                print("NO SQL was made:", r["status"])
                for x in r["reasons"] + r["needs_human"]:
                    print(" -", x)
                return 1
            print("Ready. Files in", r["pack_dir"])
            for f in r["files"]:
                print(" ", f)
            for i in r["info"]:
                print("NOTE:", i)
            if r.get("learn_command"):
                print("To teach David the spellings you confirmed:", r["learn_command"])
            return 0
        r = check(a.day)
        print(r.get("message") or f"{a.day}: {r['status']} {r.get('field_counts')}")
        for d in r.get("differences") or []:
            print(" -", d)
        return 0 if r["ok"] else 1
    except (DayError, S.Refuse, P.PipelineError, FileNotFoundError) as e:
        print("STOPPED:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
