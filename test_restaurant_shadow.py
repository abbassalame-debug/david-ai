"""
test_restaurant_shadow.py -- offline tests for restaurant_shadow.py: the comparison of a draft with what the app recorded, the day-folder
lookup, the report and the line-review CSV, and the scoring of a reviewed CSV. Uses the scripted day from test_restaurant_pipeline.py
(synthetic sample-day numbers) and a synthetic copy of the app's backup. No Ollama, no GPU, no network.

Run: py -3.11 test_restaurant_shadow.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, csv, copy, tempfile, shutil, inspect, re
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

# the scripted fixtures (Fake model, make_day, HELPER ...) live at the top of the pipeline test
_src = open(str(DAVID_HOME / "test_restaurant_pipeline.py"), encoding="utf-8").read().split("# ═══ small helpers")[0]
exec(compile(_src, "test_restaurant_pipeline.py(head)", "exec"))
import restaurant_shadow as S

TMP2 = tempfile.mkdtemp(prefix="shadow_test_")


def build_backup(root, *, beef_received=12.0, sales=3816.8, expenses=((222.66, "veg"), (16.66, "water"), (19.0, "staff")), with_day=True, day="2026-09-10"):
    b = os.path.join(root, "app", "backup", "2026-10-05")
    os.makedirs(b, exist_ok=True)
    did, other = "day-1", "day-2"
    days = [{"id": did, "on_date": day, "status": "ended"}] if with_day else []
    days.append({"id": other, "on_date": "2026-09-11", "status": "ended"})
    sheets = [{"day_id": did, "sales_usd": sales, "count_wallet_usd": 166.8, "safe_old_usd": 2361.98, "safe_counted_usd": 2200.32, "expected_usd": 2200.34, "diff_usd": -0.02, "transfer_to_admin_usd": 3553.32},
              {"day_id": other, "sales_usd": 2.0, "count_wallet_usd": 0, "safe_old_usd": 0, "safe_counted_usd": 0, "expected_usd": 0, "diff_usd": 0, "transfer_to_admin_usd": 0}]
    files = {"days": days, "cash_sheets": sheets, "cash_expenses": [{"day_id": did, "label": l, "amount_usd": a} for a, l in expenses] + [{"day_id": other, "label": "x", "amount_usd": 1998}],
             "cash_transfers": [{"day_id": did, "amount_usd": 3553.32}], "meat_receipts": [{"day_id": did, "meat_type": "chicken", "received_kg": 68}, {"day_id": did, "meat_type": "beef", "received_kg": beef_received}]}
    files["branch_settings"] = [{"beef_stock_baseline_kg": 24.14, "beef_stock_baseline_date": "2026-09-10", "chicken_stock_baseline_kg": 172.226}]
    for k, v in files.items():
        json.dump(v, open(os.path.join(b, k + ".json"), "w", encoding="utf-8"))
    return b


print("\n-- the app's records for a day --")
root = os.path.join(TMP2, "abo")
bk = build_backup(root)
check("the newest backup that has a days table is found", S.latest_backup(root) == bk)
os.makedirs(os.path.join(root, "app", "backup", "2026-11-01"))
check("a newer folder without a days table is ignored", S.latest_backup(root) == bk)
try:
    S.latest_backup(os.path.join(TMP2, "nothing")); none = False
except (FileNotFoundError, OSError):
    none = True
check("no backup at all is an error, not a guess", none)
t = S.load_truth(bk, "2026-09-10")
check("it gathers that day's sheet, expenses, transfers and meat received, and nothing from other days", t["cash_sheet"]["sales_usd"] == 3816.8 and len(t["expenses"]) == 3 and len(t["transfers"]) == 1 and t["meat_received"] == {"chicken": 68.0, "beef": 12.0})
check("a day the app does not have gives None", S.load_truth(bk, "2026-09-20") is None)

print("\n-- comparing a draft with the app --")
day = make_day()
r, drafts = run(day, day_status="ended", correction_reason="shadow")
pkg = r["package"]
rows = S.compare_day(pkg, t)
by = {x["field"]: x for x in rows}
check("every cash field of the sample day matches the app to the cent: sales, wallet, expenses, transfer, opening, counted, expected, difference", all(by[f]["result"] == "match" for f in ("pos_sales", "wallet", "expenses_total", "transfer_to_admin", "old_cash", "counted", "expected", "diff")), str([x for x in rows if x["result"] != "match"]))
check("the beef received on the consumption sheet (10 kg) against the app (12 kg) is reported as a difference, the chicken matches", by["meat_received_beef"]["result"] == "differs" and by["meat_received_beef"]["draft"] == 10.0 and by["meat_received_beef"]["app"] == 12.0 and by["meat_received_chicken"]["result"] == "match")
off = copy.deepcopy(pkg); off["cash"]["pos_sales"] = 3816.81
def res_of(p_, t_=None, field="pos_sales"):
    return {x["field"]: x["result"] for x in S.compare_day(p_, t_ or t)}[field]
near, cent, three = copy.deepcopy(pkg), copy.deepcopy(pkg), copy.deepcopy(pkg)
near["cash"]["pos_sales"], cent["cash"]["pos_sales"], three["cash"]["pos_sales"] = 3816.804, 3816.81, 3816.83
check("under half a cent is a match; 1 to 2 cents is shown as ROUNDING (not a failure); 3 cents is a real difference", res_of(near) == "match" and res_of(cent) == "rounding" and res_of(three) == "differs")
beefy = copy.deepcopy(pkg); beefy["cfo_results"]["meat"]["beef"]["inputs"]["added_kg"] = 12.01
check("...a weight that is off by 10 g is a real difference", res_of(beefy, field="meat_received_beef") == "differs")
gone = copy.deepcopy(pkg); gone["cash"]["wallet"] = None; gone["cash"]["expenses"] = [{"label": "veg", "amount": None}]
res = {x["field"]: x["result"] for x in S.compare_day(gone, t)}
check("a value the draft could not read is 'not_read', never counted as a match", res["wallet"] == "not_read" and res["expenses_total"] == "not_read")
check("with no day in the app every field is 'not_in_app'", {x["result"] for x in S.compare_day(pkg, None)} == {"not_in_app"})
check("a day with no expense rows has an expense total of 0.00, not 'not read'", {x["field"]: x["draft"] for x in S.compare_day({**pkg, "cash": {**pkg["cash"], "expenses": []}}, t)}["expenses_total"] == 0.0)

print("\n-- a day whose cash count the app does not have yet --")
t_unf = copy.deepcopy(t); t_unf["cash_sheet"]["safe_counted_usd"] = 0; t_unf["cash_sheet"]["expected_usd"] = 510.2; t_unf["cash_sheet"]["diff_usd"] = 0
rows_u = S.compare_day(pkg, t_unf)
ru = {x["field"]: x for x in rows_u}
check("when the app's cash count of a day is 0 but it expects cash in the safe, the count, the expected cash and the difference are NOT compared (not in the app), and each says why",
      all(ru[f]["result"] == "not_in_app" and "not entered yet" in ru[f]["note"] for f in ("counted", "expected", "diff")))
check("...while every other figure of that day is still compared (a real difference there is still shown)", ru["pos_sales"]["result"] == "match" and "note" not in ru["pos_sales"] and all("note" not in x for x in S.compare_day(pkg, t)))
md_u, _ = S.build_report([{"day": "2026-09-10", "package": pkg, "rows": rows_u}], TMP2)
check("the report lists the day under 'Days whose entry in the app looks unfinished' and counts it out of the matched figures", "## Days whose entry in the app looks unfinished" in md_u and "2026-09-10" in md_u.split("## Days whose entry")[1].split("##")[0])
check("a day with a real zero count and nothing expected (a closed, empty day) is not called unfinished", all("note" not in x for x in S.compare_day(pkg, {**t, "cash_sheet": {**t["cash_sheet"], "safe_counted_usd": 0, "expected_usd": 0}})))

print("\n-- the app's stock baseline (it includes the beef tallow) --")
rows_b = {x["field"]: x for x in S.compare_day(pkg, S.load_truth(bk, "2026-09-10"))}
check("on the app's baseline day the draft's opening stock is compared with it: chicken matches, and beef shows whether the sheet's opening includes the tallow", rows_b["meat_start_chicken"]["result"] == "match" and rows_b["meat_start_beef"]["app"] == 24.14)
pk_t = copy.deepcopy(pkg); pk_t["cfo_results"]["meat"]["beef"]["inputs"]["starting_kg"] = 21.14
check("an opening beef stock WITHOUT the 3 kg of tallow (21.14 against the app's 24.14) is reported as a difference", {x["field"]: x["result"] for x in S.compare_day(pk_t, S.load_truth(bk, "2026-09-10"))}["meat_start_beef"] == "differs")
t_other = S.load_truth(bk, "2026-09-11")
check("on any other day the baseline is not a truth: those fields are 'not_in_app' and are left out of the counts", {x["result"] for x in S.compare_day(pkg, t_other) if x["field"].startswith("meat_start")} == {"not_in_app"})
bkx = build_backup(os.path.join(TMP2, "abo_nosettings")); os.remove(os.path.join(bkx, "branch_settings.json"))
check("a backup without branch_settings still works (no baseline)", S.load_truth(bkx, "2026-09-10")["baseline"]["date"] is None)

print("\n-- finding the day folders --")
for sub in ("09-2026/1-09-2026", "09-2026/02-09-2026", "09-2026/20-19-2026", "10-2026/05-10-2026"):
    os.makedirs(os.path.join(root, *sub.split("/")))
f = S.day_folders("2026-09-01", "2026-09-03", root)
check("folders named with or without a leading zero are found, in date order; days with no folder are skipped", [d for d, _ in f] == ["2026-09-01", "2026-09-02"] and f[0][1].endswith("1-09-2026"))
check("a range can cross months", [d for d, _ in S.day_folders("2026-09-30", "2026-10-06", root)] == ["2026-10-05"])

print("\n-- running a range, the report, the line review --")
out = os.path.join(TMP2, "out")
abo = os.path.join(TMP2, "abo2")
shutil.copytree(make_day("10-09-2026"), os.path.join(abo, "09-2026", "10-09-2026"))
build_backup(abo)
with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP2)):
    # the last item is a name that is on neither the sheet nor the manager's list (a plastic fork), so the report has something to put on its worklist
    sum_a = copy.deepcopy(SUMMARY); sum_a["lines"][-1] = {"name": "شوك", "qty": 9}
    run1 = S.run_shadow("2026-09-09", "2026-09-11", out_dir=out, restaurant_root=abo, client=Fake(full_script({COLORS["summary"]: ("pos_summary_by_items", [sum_a, EMPTY_HALF, sum_a, EMPTY_HALF])})), log=lambda m: None)
check("only the days that have a folder are run", [e["day"] for e in run1["entries"]] == ["2026-09-10"] and not run1["entries"][0].get("error"))
check("the draft is saved in the shadow folder as ended, with the shadow correction reason, and nothing else is written there but reports", os.path.isfile(os.path.join(out, "DRAFT-2026-09-10-001.json")) and run1["entries"][0]["package"]["day_status"] == "ended"
      and sorted(f for f in os.listdir(out) if not f.startswith("DRAFT-")) == ["shadow_lines.csv", "shadow_report_2026-09-09_2026-09-11.md"])
md = run1["report"]
check("the report has one row per day with the draft id and the cash fields matched out of those the app has (10 of 12 here: the beef received and the beef opening differ in this fixture)", "| 2026-09-10 | DRAFT-2026-09-10-001 | 10/12 | 0 |" in md)
check("it lists the fields that differ, with both values", "| 2026-09-10 | meat_received_beef | 10.0 | 12.0 | differs |" in md and "| 2026-09-10 | meat_start_beef | 16.64 | 24.14 | differs |" in md)
check("it lists receipt names that were not matched exactly, most frequent first, as the worklist for aliases (a confirmed spelling is no longer on it)", "## Receipt names that were not matched exactly" in md and "| شوك | 1 | 9 |" in md and md.index("| شوك |") > md.index("## Receipt names") and "| ستيك دجاج |" not in md and "| وجبة موظف بطاطا |" not in md and md.count("| Name as read |") == 1)
check("it points the manager to the line review file", "shadow_lines.csv" in md)
with open(os.path.join(out, "shadow_lines.csv"), encoding="utf-8-sig", newline="") as fh:
    lines = list(csv.DictReader(fh))
check("the line review has one row per item line with the system's reading and match, and two EMPTY columns for corrections", len(lines) == 8 and lines[0]["name_as_read"] == "ساندويش دجاج صغير" and lines[0]["matched_item_id"] == "C01" and all(l["correct_item_id"] == "" and l["correct_qty"] == "" for l in lines))
with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP2)):
    run2 = S.run_shadow("2026-09-10", "2026-09-10", out_dir=out, restaurant_root=abo, client=Fake(full_script()), log=lambda m: None)
check("running again makes DRAFT-...-002 and leaves 001 alone", run2["entries"][0]["package"]["draft_id"] == "DRAFT-2026-09-10-002" and os.path.isfile(os.path.join(out, "DRAFT-2026-09-10-001.json")))
rep = S.run_shadow("2026-09-10", "2026-09-10", out_dir=out, restaurant_root=abo, report_only=True, log=lambda m: None)
check("--report-only rebuilds the report from the latest draft without reading any photo", rep["entries"][0]["package"]["draft_id"] == "DRAFT-2026-09-10-002" and "10/12" in rep["report"])
rep2 = S.run_shadow("2026-09-10", "2026-09-10", out_dir=os.path.join(TMP2, "empty_out"), restaurant_root=abo, report_only=True, log=lambda m: None)
check("a day with no draft yet is reported as an ERROR row, not skipped silently", rep2["entries"][0]["error"] and "ERROR: no draft yet" in rep2["report"])
with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP2)):
    bad = S.run_shadow("2026-09-10", "2026-09-10", out_dir=os.path.join(TMP2, "out_bad"), restaurant_root=abo, rate=-1, client=Fake(full_script()), log=lambda m: None)
check("a day whose draft is not valid still produces a report row (the draft exists, the flags are counted)", not bad["entries"][0].get("error") and "| 2026-09-10 | DRAFT-2026-09-10-001 |" in bad["report"])

print("\n-- scoring the reviewed lines --")
rv = os.path.join(TMP2, "reviewed.csv")
def rows_for(edit):
    rs = [dict(l) for l in lines]
    edit(rs)
    S.write_lines_csv(rv, rs)
    return S.score_line_review(rv)
agree = rows_for(lambda rs: None)
sure = [l for l in lines if l["status"] in ("matched", "suggested") and l["readings_agree"] == "True"]
check("with every correction left blank, a line counts as right when the system was sure of it; the rest are counted as sent to a human", agree["lines"] == 8 and agree["exactly_right"] == len(sure) and agree["sent_to_a_human"] == 8 - len(sure) and agree["confident_but_wrong_item"] == 0)
def wrong_item(rs):
    s0 = [x for x in rs if x["status"] == "matched" and x["readings_agree"] == "True"][0]
    s0["correct_item_id"] = "C99"
sc = rows_for(wrong_item)
check("a confident match the manager corrected is counted as wrong, and as 'confident but wrong item'", sc["exactly_right"] == len(sure) - 1 and sc["confident_but_wrong_item"] == 1)
def wrong_qty(rs):
    s0 = [x for x in rs if x["status"] == "matched" and x["readings_agree"] == "True"][0]
    s0["correct_qty"] = str(float(s0["qty"]) + 1)
sc = rows_for(wrong_qty)
check("a corrected quantity is counted as wrong too", sc["exactly_right"] == len(sure) - 1 and sc["confident_but_wrong_qty"] == 1)
sc = rows_for(lambda rs: [x.update(correct_item_id=x["matched_item_id"], correct_qty=x["qty"]) for x in rs])
check("explicitly confirming a line gives the same score as leaving it blank", sc["exactly_right"] == agree["exactly_right"] and sc["exact_accuracy"] == agree["exact_accuracy"])
check("the accuracy of the lines the system was sure about is reported separately from the share it sent to a human", sc["accuracy_of_the_lines_it_was_sure_about"] == 1.0 and sc["exact_accuracy"] == round(len(sure) / 8, 4))

print("\n-- boundaries --")
code = "\n".join(l for l in inspect.getsource(S).splitlines() if not l.lstrip().startswith("#"))
check("it reads the app's backup files and writes only to its own output folder (reports, csv, drafts)", len(re.findall(r"open\([^)]*['\"]w", code)) == 1 and "write_text" not in code and S.SHADOW_DIR == str(DAVID_DATA / "jobs_drafts" / "restaurant_shadow"))
check("it imports no network, database or mail library, and never calls the app", not re.search(r"^\s*(import|from)\s+(requests|httpx|urllib|socket|sqlite3|smtplib|supabase|psycopg)", code, re.M))
check("the shadow drafts are always 'ended' with a reason that says nothing was entered", 'day_status="ended"' in code and "nothing is entered" in code)

shutil.rmtree(TMP2, ignore_errors=True)
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
