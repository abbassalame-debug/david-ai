"""
restaurant_learn.py -- Job 1 learns from what the manager has already decided, so every reviewed scan makes the next one need less review.

What it learns: the EXACT receipt spelling of a line the manager answered (ok to my guess, or the item he named) becomes a confirmed alias in
reference/menu_aliases.json, so the same misreading is an exact match next time and is not held again. Nothing else is learned automatically.

What it never does: learn from the model's own readings or guesses (only the manager's answers count), decide a ruling for him ("not an item" answers
are only LISTED as suggested rulings), change the cheat sheet, or touch the app / Supabase. A spelling is skipped, and reported, when it is already an
exact match, belongs to another item, was answered with two different items, is too short to be safe, or is a 'without / extra / free' line.

    py -3.11 restaurant_learn.py --workbook <...\\lines_to_review.xlsx>                  show what would be learned from his filled review sheet
    py -3.11 restaurant_learn.py --workbook <...\\lines_to_review.xlsx> --apply          write it (a note goes to reference/learning_log.json)
    py -3.11 restaurant_learn.py --answers <...\\manager_line_answers.json> [--apply]     the same from an answers file

Answers in the workbook's yellow column: `ok` (my guess is right), an item id (C20) or exact menu name, `qty 5` (the item is right, the quantity
is 5), `not an item`. A blank cell is NOT learned from unless --blank-is-ok is given (a blank means "I agree" for scoring, but it may also mean
"not looked at").
"""

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import sys
import tempfile

import menu_tools

ALIASES_JSON = "menu_aliases.json"
LEARNING_LOG = "learning_log.json"
MIN_SPELLING = 4                       # a normalised spelling shorter than this is never learned
_ITEM_ID = re.compile(r"^[A-Za-z]\d{2}$")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ══ the manager's answers ══════════════════════════════════════════════════════════════════════════════════════════════════════

def answers_from_workbook(xlsx_path, index_path=None, blank_is_ok=False, glossary=None):
    """Read his filled review sheet. -> (answers, report): answers = [{"id","day","name","qty","item","how"}] for lines whose ITEM he settled; report =
    {"unresolved": [...], "not_an_item": [...], "blank": n, "quantity_changes": n}. The sheet's '#' column ties each row to the index file written with it."""
    import openpyxl
    index_path = index_path or os.path.join(os.path.dirname(xlsx_path), "lines_to_review_index.json")
    index = _read_json(index_path)
    g = glossary or menu_tools.load_glossary()
    ws = openpyxl.load_workbook(xlsx_path, data_only=True)["Lines to review"]
    head = [str(c.value or "").strip() for c in ws[1]]
    col_n, col_ans = head.index("#"), head.index("YOUR ANSWER")
    answers, report = [], {"unresolved": [], "not_an_item": [], "blank": 0, "quantity_changes": 0}
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            n = int(row[col_n])
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(index):
            continue
        r, text = index[n - 1], str(row[col_ans] or "").strip()
        base = {"id": r["id"], "day": r["day"], "name": r["name"], "qty": r["qty"]}
        low = text.lower()
        if not text:
            report["blank"] += 1
            if blank_is_ok and r.get("gid"):
                answers.append({**base, "item": r["gid"], "how": "blank, taken as ok"})
            continue
        if re.match(r"^(not an item|not item|ليس)", low):
            report["not_an_item"].append({"id": r["id"], "name": r["name"], "qty": r["qty"]})
            continue
        if re.match(r"^ok\b", low) or low == "ok":
            if r.get("gid"):
                answers.append({**base, "item": r["gid"], "how": "ok"})
            else:
                report["unresolved"].append({"id": r["id"], "name": r["name"], "answer": text, "why": "'ok' but there was no guess to confirm"})
            continue
        m = re.match(r"^qty\s*([0-9]+(?:\.[0-9]+)?)", low)
        if m:
            report["quantity_changes"] += 1
            if r.get("gid"):
                answers.append({**base, "item": r["gid"], "how": f"qty {m.group(1)}"})
            continue
        item = None
        if _ITEM_ID.match(text):
            item = text.upper()
        else:
            hit = menu_tools.match_menu_item(raw_texts=[text], glossary=g)["results"][0]
            if hit["status"] == "matched" and hit["score"] == 100:
                item = hit["match"]["item_id"]
        if item and any(i["item_id"] == item for i in g["items"]):
            answers.append({**base, "item": item, "how": f"named: {text}"})
        else:
            report["unresolved"].append({"id": r["id"], "name": r["name"], "answer": text, "why": "not an item id or an exact menu name"})
    return answers, report


def answers_from_file(path):
    doc = _read_json(path)
    return [a for a in doc.get("answers", []) if a.get("item")], doc


# ══ what to learn ══════════════════════════════════════════════════════════════════════════════════════════════════════════════

def learn_from_answers(answers, glossary=None):
    """-> {"new": [{"ar","item","name_ar"}], "skipped": [{"name","item","why"}]}: the spellings worth adding. Nothing is written."""
    g = glossary or menu_tools.load_glossary()
    keys = {i["item_id"] for i in g["items"]}
    names = {i["item_id"]: i["name_ar"] for i in g["items"]}
    seen, new, skipped, conflicts = {}, [], [], set()
    for a in answers:
        name, item = str(a.get("name") or "").strip(), a.get("item")
        n = menu_tools.normalize(name)
        if not n or not item:
            continue
        if item not in keys:
            skipped.append({"name": name, "item": item, "why": "the item is not on the menu"}); continue
        if menu_tools.ignored_line_kind(name, g):
            skipped.append({"name": name, "item": item, "why": "a 'without / extra / free' line, which is not an item"}); continue
        if len(n.replace(" ", "")) < MIN_SPELLING:
            skipped.append({"name": name, "item": item, "why": f"fewer than {MIN_SPELLING} letters: too short to be safe"}); continue
        if n in seen and seen[n]["item"] != item:
            conflicts.add(n)
            skipped.append({"name": name, "item": item, "why": f"answered as {seen[n]['item']} and as {item}"}); continue
        seen.setdefault(n, {"ar": name, "item": item})
    for n in conflicts:
        seen.pop(n, None)
    for n, c in seen.items():
        r = menu_tools.match_menu_item(raw_texts=[c["ar"]], glossary=g)["results"][0]
        if r["status"] == "matched" and r["score"] == 100:
            if r["match"]["item_id"] != c["item"]:
                skipped.append({"name": c["ar"], "item": c["item"], "why": f"it is already an exact match for {r['match']['item_id']}: check which is right"})
            continue                                   # already known (the common case): nothing to learn
        new.append({"ar": c["ar"], "item": c["item"], "name_ar": names.get(c["item"])})
    return {"new": new, "skipped": skipped}


def suggested_rulings(report):
    """'not an item' answers: the words that start them, as a SUGGESTION for an ignored_line_rules entry (the manager decides)."""
    words = {}
    for x in report.get("not_an_item", []):
        w = menu_tools.normalize(x["name"]).split()
        if w:
            words.setdefault(w[0], []).append(x["name"])
    return words


# ══ writing ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def apply_aliases(new, source_label, ref_dir=None, today=None, confirmed_by="Demo User (manager)"):
    """Append the spellings to the alias file as confirmed, but only those that really resolve exactly to their item afterwards. Idempotent: a spelling
    already present is not added twice. -> {"written": [...], "refused": [...]}"""
    ref_dir = ref_dir or menu_tools.REF_DIR
    today = today or datetime.date.today().isoformat()
    path = os.path.join(ref_dir, ALIASES_JSON)
    doc = _read_json(path)
    have = {menu_tools.normalize(a["ar"]) for a in doc.get("aliases", []) if a.get("status") == "confirmed"}
    fresh = [c for c in new if menu_tools.normalize(c["ar"]) not in have]
    entries = [{"ar": c["ar"], "item": c["item"], "status": "confirmed", "confirmed_by": confirmed_by, "confirmed_on": today,
                "evidence": f"A receipt spelling the manager confirmed as this item when he answered {source_label}. Learned automatically by restaurant_learn.py on {today}."} for c in fresh]
    # prove each one first, on a throw-away copy of the reference folder: the real file is only touched when the proof passes
    probe = tempfile.mkdtemp(prefix="learn_probe_")
    try:
        for f in os.listdir(ref_dir):
            p = os.path.join(ref_dir, f)
            if os.path.isfile(p):
                shutil.copy(p, probe)
        pdoc = copy.deepcopy(doc)
        pdoc["aliases"] = list(pdoc.get("aliases", [])) + entries
        _write_json(os.path.join(probe, ALIASES_JSON), pdoc)
        g = menu_tools.get_menu_glossary(ref_dir=probe)
        refused, good = [], []
        for e in entries:
            r = menu_tools.match_menu_item(raw_texts=[e["ar"]], glossary=g)["results"][0]
            if r["status"] == "matched" and r["score"] == 100 and r["match"]["item_id"] == e["item"]:
                good.append(e)
            else:
                refused.append({"name": e["ar"], "item": e["item"], "why": "it does not resolve exactly to that item once added"})
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    if good:
        doc["aliases"] = list(doc.get("aliases", [])) + good
        _write_json(path, doc)
    log_path = os.path.join(ref_dir, LEARNING_LOG)
    log = _read_json(log_path) if os.path.isfile(log_path) else []
    log.append({"on": today, "from": source_label, "added": len(good), "refused": len(refused), "aliases_total": len([a for a in doc.get("aliases", []) if a.get("status") == "confirmed"])})
    _write_json(log_path, log)
    return {"written": good, "refused": refused}


# ══ command line ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="learn receipt spellings from the manager's review answers")
    ap.add_argument("--workbook", help="his filled lines_to_review.xlsx")
    ap.add_argument("--index", help="the lines_to_review_index.json written with it (default: next to the workbook)")
    ap.add_argument("--answers", help="a manager_line_answers.json instead of a workbook")
    ap.add_argument("--blank-is-ok", action="store_true", help="learn from blank cells too (he reviewed the whole sheet and left the right ones blank)")
    ap.add_argument("--apply", action="store_true", help="write the aliases (otherwise only show them)")
    a = ap.parse_args(argv)
    if bool(a.workbook) == bool(a.answers):
        ap.error("give exactly one of --workbook or --answers")
    report = {}
    if a.workbook:
        answers, report = answers_from_workbook(a.workbook, a.index, a.blank_is_ok)
        label = f"the review sheet {os.path.basename(a.workbook)}"
    else:
        answers, _ = answers_from_file(a.answers)
        label = f"the answers file {os.path.basename(a.answers)}"
    out = learn_from_answers(answers)
    print(f"{len(answers)} answers settle an item | {len(out['new'])} spellings to learn | {len(out['skipped'])} skipped")
    for c in out["new"]:
        print(f"  + {c['ar']}  ->  {c['item']} {c['name_ar']}")
    for s in out["skipped"]:
        print(f"  skipped {s['name']} ({s['item']}): {s['why']}")
    if report:
        print(f"blank: {report['blank']} | quantity corrections: {report['quantity_changes']} | unresolved answers: {len(report['unresolved'])}")
        for u in report["unresolved"]:
            print(f"  ? {u['name']}: '{u['answer']}' ({u['why']})")
        sug = suggested_rulings(report)
        if sug:
            print("answered 'not an item' (a suggestion for a ruling, never applied by me): " + "; ".join(f"{w} ({len(v)} lines)" for w, v in sug.items()))
    if a.apply and out["new"]:
        res = apply_aliases(out["new"], label)
        print(f"written: {len(res['written'])} | refused: {len(res['refused'])}")
        for r in res["refused"]:
            print(f"  refused {r['name']}: {r['why']}")
    elif out["new"]:
        print("nothing written (add --apply to write them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
