"""
test_restaurant_pipeline.py -- offline tests for restaurant_pipeline.py: a day's folder in, a draft_package.json and a review sheet out.

The vision model is a scripted fake (the numbers are a synthetic sample day: cash agrees with the app's own sheet to the cent), the
pictures are synthetic, the consumption sheet is a real .xlsx built here. What is tested: the package it builds (schema v0, validated by the
analyst's own tool), the math (done by code, from the numbers read), every way the readings can disagree or a document can be missing, that
a draft is never overwritten, that the analyst's two tools run through the permission check, and that nothing is sent or written elsewhere.

No Ollama, no GPU, no network.   Run: py -3.11 test_restaurant_pipeline.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, io, json, re, copy, tempfile, shutil, datetime, hashlib, inspect, asyncio
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import david_router  # noqa: F401
import restaurant_pipeline as P
import restaurant_vision as V
import restaurant_tools as R
import menu_tools as M
import tool_executor as TE
from PIL import Image
import openpyxl

TMP = tempfile.mkdtemp(prefix="pipeline_test_")
V.CACHE_DIR = os.path.join(TMP, "cache")                       # never touch the real cache
RG = M.get_menu_glossary()
assert RG["ok"], RG


def reading(doc_type, **kw):
    base = {k: None for k in V.SCHEMAS[doc_type]["properties"]}
    for k, spec in V.SCHEMAS[doc_type]["properties"].items():
        if k in V.LIST_KEYS:
            base[k] = []
    return {**base, **kw}


HELPER = reading("daily_helper_sheet", date="9 Sept 2026", sales=3816.8, transactions=153, total=3978.44, value_usd=1796, value_ll=4.32, over_short=1961.96, old=2361.98, expected=2200.34, new=2200.32, diff=-0.02,
                 rows=[{"subject": "Transfer", "amount": 3553.32}, {"subject": "veg.", "amount": 222.66}, {"subject": "wish", "amount": 166.8}, {"subject": "water", "amount": 16.66}, {"subject": "staff", "amount": 19.0}])
MAIN = reading("pos_main_reading", printed_at="11-09-2026 02:01:01", first_order="10-09-2026 12:10:49", last_order="11-09-2026 02:01:17", first_invoice=203942, last_invoice=204096, number_of_customers=153,
               number_of_invoices=157, grand_total=3816.8, vat=378.26, service=0.0, discount=0.0, net_total=3816.8, net_sales=3816.8, net_sales_without_taxes=3438.56, payments=[{"method": "Cash Usd", "amount": 3816.8}], payments_total=3816.8)
FORM = reading("daily_cash_form", branch="Downtown", date="10/09/2026", prepared_by="Demo, U", total_transactions=79, opening_cash_balance=2361.98, total_pos_sales=2059.6, total_actual_cash=1814, total_to_be_handed=3553.32,
               expenses=[{"description": "Veg", "amount_usd": 222.66, "amount_lbp": None}, {"description": "water", "amount_usd": None, "amount_lbp": 1499400}, {"description": "wallet", "amount_usd": 65.0, "amount_lbp": None}],
               cash_on_hold=[{"description": "Veg", "amount": 400}])
L = lambda *pairs: [{"name": n, "qty": q} for n, q in pairs]
SUMMARY = reading("pos_summary_by_items", title_visible=True,
                  lines=L(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("نص كيلو ساندويش دجاج", 2), ("ساندويش لحمة كبير", 12), ("سلايدر دجاج", 3), ("ستيك دجاج", 8), ("وجبة سوطلف بطاطا", 9)),
                  voids=[{"name": "بيبسي", "amount": -1.98, "qty": 1}])
SUMMARY_B = reading("pos_summary_by_items", title_visible=True,
                    lines=L(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("نص كيلو ساندويش دجاج", 2), ("ساندويش لحمة كبير", 12), ("سلايدر دجاج", 3), ("ستيك دجاج", 8), ("وجبة موظف بطاطا", 9)),
                    voids=[{"name": "بيبسي", "amount": -1.98, "qty": 1}])
EMPTY_HALF = reading("pos_summary_by_items", title_visible=False)

COLORS = {"helper": (11, 11, 11), "main": (22, 22, 22), "form": (33, 33, 33), "summary": (44, 44, 44), "summary2": (55, 55, 55), "other": (66, 66, 66)}


class Fake:
    """Scripted model. script[colour] = (type, [reading calls in order])."""
    def __init__(self, script):
        self.script, self.n, self.calls = script, {}, 0

    def ask(self, png, prompt, schema):
        self.calls += 1
        color = Image.open(io.BytesIO(png)).convert("RGB").getpixel((3, 3))
        t, rs = self.script[color]
        if prompt.startswith("What kind of document"):
            return {"document_type": "pos_summary_by_items" if t in ("supply_trap", "other_trap") else t}
        if prompt.startswith("Is this a POS"):
            return {"kind": {"supply_trap": "supply_or_expense_list", "other_trap": "other"}.get(t, "pos_sales_list"), "why": "grid lines and cleaning products" if t == "supply_trap" else ""}
        k = self.n[color] = self.n.get(color, 0) + 1
        return rs[min(k, len(rs)) - 1]


def full_script(over=None):
    s = {COLORS["helper"]: ("daily_helper_sheet", [HELPER, HELPER]), COLORS["main"]: ("pos_main_reading", [MAIN, MAIN]), COLORS["form"]: ("daily_cash_form", [FORM, FORM]),
         COLORS["summary"]: ("pos_summary_by_items", [SUMMARY, EMPTY_HALF, SUMMARY_B, EMPTY_HALF]), COLORS["other"]: ("other", [])}
    s.update(over or {})
    return s


def make_day(name="10-09-2026", with_sheet=True, only=None, extra=True):
    d = os.path.join(TMP, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for k, col in COLORS.items():
        if k == "summary2" or (only and k not in only):
            continue
        Image.new("RGB", (300, 520), col).save(os.path.join(d, f"{k}.jpeg"), "JPEG", quality=100)
    if extra:
        shutil.copy(os.path.join(d, "summary.jpeg"), os.path.join(d, "summary_copy.jpeg")) if os.path.exists(os.path.join(d, "summary.jpeg")) else None
        open(os.path.join(d, "zero.jpeg"), "wb").close()
        open(os.path.join(d, "rent bill.pdf"), "wb").write(b"%PDF")
    if with_sheet:
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Daily Beef & Chicken Stock Consumption"]); ws.append(["Branch Name :", "downtown"]); ws.append([])
        ws.append(["Date", "Item", "Opening", "Received", "Total", "Used in Cooking", "Shrink", "Yield", "Sold", "Waste", "Remaining After Cooking", "Remaining Raw", "Notes"])
        ws.append([datetime.datetime(2026, 9, 10), "Beef", 16.64, 10, 26.64, 10.9, 0.45, 5.995, 4.91, 0.61, 0.4, 15.74, None])
        ws.append([datetime.datetime(2026, 9, 10), "Chicken", 172.226, 68, 240.226, 72.88, 0.45, 40.084, 37, 4.9, 3.0, 167.346, None])
        wb.save(os.path.join(d, "Branch Beef  ^0 Chicken Daily consumption 10-09-2026.xlsx"))
    return d


def run(day, script=None, drafts=None, **kw):
    drafts = drafts or os.path.join(TMP, "drafts_" + os.path.basename(day))
    kw.setdefault("rate", 90000)
    with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError("the GPU must not be touched when a client is given")), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
        kw.setdefault("restaurant_root", os.path.join(TMP, "no_abo"))
        return P.run_day(day, client=Fake(script or full_script()), drafts_dir=drafts, glossary=RG, log=lambda m: None, **kw), drafts


# ═══ small helpers ════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- the business day and the draft id --")
check("the day is read from the folder name (day-month-year, one or two digits)", P.day_from_folder(r"C:\x\09-2026\10-09-2026") == "2026-09-10" and P.day_from_folder("1-09-2026") == "2026-09-01" and P.day_from_folder("/a/b/05-10-2026/") == "2026-10-05")
check("a folder with no day, or an impossible one, gives None", P.day_from_folder("photos") is None and P.day_from_folder("31-02-2026") is None and P.day_from_folder("2026-09-10") is None)
dd = tempfile.mkdtemp(dir=TMP)
check("the first draft of a day is 001", P.next_draft_id("2026-09-10", dd) == "DRAFT-2026-09-10-001")
for n in ("DRAFT-2026-09-10-001.json", "DRAFT-2026-09-10-002.json", "DRAFT-2026-09-10-002.review.md", "DRAFT-2026-09-11-009.json", "notes.txt"):
    open(os.path.join(dd, n), "w").write("x")
check("the next one follows the highest existing number for THAT day (other days and other files are ignored)", P.next_draft_id("2026-09-10", dd) == "DRAFT-2026-09-10-003" and P.next_draft_id("2026-09-12", dd) == "DRAFT-2026-09-12-001")
check("a new draft file is created", (lambda p: (P._write_new(p, "first"), open(p).read())[1])(os.path.join(dd, "fresh.json")) == "first")
open(os.path.join(dd, "x.json"), "w").write("x")
try:
    P._write_new(os.path.join(dd, "x.json"), "overwritten"); over = True
except P.PipelineError:
    over = False
check("...really: writing to an existing path raises and leaves the old content", over is False and open(os.path.join(dd, "x.json")).read() == "x")

# ═══ a clean day ══════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- a day with every document --")
day = make_day()
before = {f: hashlib.sha256(open(os.path.join(day, f), "rb").read()).hexdigest() for f in os.listdir(day)}
r, drafts = run(day, day_status="ended", correction_reason="shadow test")
pk = r["package"]
check("the pipeline returns the draft's path, id and a review sheet", r["ok"] and r["draft_id"] == "DRAFT-2026-09-10-001" and os.path.isfile(r["package_path"]) and r["review_path"] and os.path.isfile(r["review_path"])
      and r["review_markdown"].startswith("# Review sheet: 2026-09-10 (DRAFT-2026-09-10-001)"))
check("the saved draft is exactly the package returned, and valid JSON in UTF-8", json.load(open(r["package_path"], encoding="utf-8")) == json.loads(json.dumps(pk, ensure_ascii=False)))
check("the analyst's own validation tool accepts it (schema v0, matches, math)", r["validation"]["valid"] is True and r["validation"]["errors"] == [], str(r["validation"]["errors"]))
check("it has every block the v0 schema requires, with the right values", pk["schema"] == "draft_package/v0" and pk["business_day"] == "2026-09-10" and pk["day_status"] == "ended" and pk["correction_reason"] == "shadow test"
      and pk["rate_lbp_per_usd"] == 90000 and pk["branch"] == "Downtown" and all(k in pk for k in ("sources", "extraction", "cash", "computed", "flags")))
check("the sources are the files that were read, with SHA-256 (not the unread ones)", {s["kind"] for s in pk["sources"]} == {"daily_helper_sheet", "pos_main_reading", "daily_cash_form", "pos_summary_by_items", "consumption_xlsx"}
      and all(re.fullmatch(r"[0-9a-f]{64}", s["sha256"]) for s in pk["sources"]) and not any("zero" in s["file"] or "rent" in s["file"] or "other" in s["file"] for s in pk["sources"]))
check("files that were not read are listed with the reason: duplicate photo, zero-byte file, not-a-report photo, bill", pk["files_not_read"]["duplicates_of_a_photo"] == [["summary_copy.jpeg", "summary.jpeg"]]
      and pk["files_not_read"]["zero_byte_files"] == ["zero.jpeg"] and pk["files_not_read"]["not_a_menu_report_photo"] == ["other.jpeg"] and "rent bill.pdf" in pk["files_not_read"]["spreadsheets_and_documents"])
check("no input file was changed", {f: hashlib.sha256(open(os.path.join(day, f), "rb").read()).hexdigest() for f in before} == before and set(os.listdir(day)) == set(before))

print("\n-- the cash check (code, from the numbers read) --")
c = pk["cash"]
check("the cash inputs come from the Daily Helper screenshot: opening, Main Reading sales, wallet, expenses, transfer, full count", c["old_cash"] == 2361.98 and c["pos_sales"] == 3816.8 and c["wallet"] == 166.8
      and [e["label"] for e in c["expenses"]] == ["veg.", "water", "staff"] and [e["amount"] for e in c["expenses"]] == [222.66, 16.66, 19.0] and c["transfer_to_admin"] == 3553.32 and c["actual_cash_count"] == 2200.32
      and c["source"] == "the Daily Helper screenshot")
check("the figures equal the app's own sheet for that day: expected 2,200.34, counted 2,200.32, difference -0.02", pk["computed"]["expected"] == 2200.34 and pk["computed"]["counted"] == 2200.32 and pk["computed"]["diff"] == -0.02
      and pk["cfo_results"]["cash"] == R.reconcile_cash(carried_cash=2361.98, pos_sales=3816.8, wallet=166.8, expenses=[{"label": "veg.", "amount": 222.66}, {"label": "water", "amount": 16.66}, {"label": "staff", "amount": 19.0}],
                                                         transfer_to_admin=3553.32, actual_cash_count=2200.32, counted_usd=1796, rate_lbp_per_usd=90000, branch_rate_lbp_per_usd=90000))
check("the sheet's own Expected and Diff agree with the code, so there is no flag about them", not any("own 'Expected'" in f or "own 'Diff'" in f for f in pk["flags"]))
check("the review sheet carries the cash check exactly as reconcile_cash produced it", "| Expected | 2,200.34 |" in r["review_markdown"] and "| Counted | 2,200.32 |" in r["review_markdown"] and "−0.02" in r["review_markdown"])
fl = pk["flags"]
check("every flag starts with BLOCKER:, CHECK: or NOTE:", all(re.match(r"(BLOCKER|CHECK|NOTE): ", f) for f in fl))
check("the Daily Helper sheet's date cell (9 Sept) against the 10 Sept folder is flagged", any("dated '9 Sept 2026'" in f and f.startswith("CHECK") for f in fl))
check("the handwritten form's part-day POS sales (2,059.60) against the Main Reading are noted, and the Main Reading is used", any("2,059.60" in f and "3,816.80" in f and f.startswith("NOTE") for f in fl) and c["pos_sales"] == 3816.8)
check("the pipeline's own flags appear in the review sheet's summary", "- CHECK: the Daily Helper sheet is dated '9 Sept 2026'" in r["review_markdown"])

print("\n-- the meat check (the consumption sheet, by cell) --")
mv = pk["cfo_results"]["meat"]
check("chicken and beef are checked by check_meat_variance from the sheet's row for that day", mv["chicken"]["ok"] and mv["beef"]["ok"] and mv["chicken"]["status"] == "loss" and mv["chicken"]["variance_kg"] == 1.816 and mv["beef"]["status"] == "surplus")
check("the beef weights are said to include the fat/tallow, and the unverifiable-tallow CHECK from the tool is on the sheet", any(f.startswith("NOTE: the beef starting, received and ending weights") and "tallow" in f for f in pk["flags"])
      and "beef weights must include the fat/tallow" in r["review_markdown"])
check("...with the sheet's numbers: opening + received - remaining = raw used 72.88 kg, sold 37, waste 4.9", mv["chicken"]["raw_used_kg"] == 72.88 and mv["chicken"]["pos_sold_kg"] == 37.0 and mv["chicken"]["waste_kg"] == 4.9 and mv["chicken"]["inputs"] == {"starting_kg": 172.226, "added_kg": 68.0, "ending_kg": 167.346})
check("the review sheet shows both meats under the meat check", "## Meat check (from check_meat_variance)" in r["review_markdown"] and "- chicken:" in r["review_markdown"] and "- beef:" in r["review_markdown"])
ml = pk["computed"]["meat_from_lines"]
check("the meat the item list implies is counted from the matched lines (all 8 here: the misread staff-meal-with-fries line is settled by the menu to the 0 g item X02), and the lines not counted would be listed", ml["lines_total"] == 8 and ml["lines_counted"] == 8 and ml["lines_not_counted"] == []
      and ml["matched"]["chicken"] == round((29 * 100 + 16 * 130 + 2 * 500 + 3 * 30 + 8 * 130) / 1000, 3) and ml["matched"]["beef"] == round(12 * 130 / 1000, 3))
check("the bare slider line is ONE 30 g piece by the manager's ruling, so 3 sold = 90 g of chicken IS counted, and so is the confirmed 'ستيك دجاج' (8 x 130 g)", not any(x["name"] in ("سلايدر دجاج", "ستيك دجاج") for x in ml["lines_not_counted"])
      and ml["matched"]["chicken"] - round((29 * 100 + 16 * 130 + 2 * 500) / 1000, 3) == round((3 * 30 + 8 * 130) / 1000, 3))

print("\n-- the item lines --")
ln = pk["extraction"]["summary_by_items"]
check("the item list says it prints quantities only, and no line has an amount", ln["amounts_printed"] is False and ln["printed_total"] is None and all(l["amount"] is None for l in ln["lines"]))
check("the lines are numbered, keep the printed name exactly, and carry quantities as numbers", [l["line"] for l in ln["lines"]] == list(range(1, 9)) and ln["lines"][0]["name_ar"] == "ساندويش دجاج صغير" and ln["lines"][0]["qty"] == 29.0)
byname = {l["name_ar"]: l for l in ln["lines"]}
check("an exact sheet name is matched at 100 with the sheet's item id", byname["ساندويش دجاج صغير"]["match"]["item_id"] == "C01" and byname["ساندويش دجاج صغير"]["match"]["score"] == 100 and byname["ساندويش دجاج صغير"]["match_status"] == "matched")
check("the receipt's own wording that the sheet writes differently is found exactly (نص كيلو ساندويش دجاج = C08)", byname["نص كيلو ساندويش دجاج"]["match"]["item_id"] == "C08" and byname["نص كيلو ساندويش دجاج"]["match_status"] == "matched")
check("the bare slider line is the one-piece chicken item K07, exactly matched (the manager's ruling)", byname["سلايدر دجاج"]["match_status"] == "matched" and byname["سلايدر دجاج"]["match"]["item_id"] == "K07" and byname["سلايدر دجاج"]["match"]["score"] == 100)
check("a receipt spelling the manager confirmed ('ستيك دجاج' = C19) is matched exactly", byname["ستيك دجاج"]["match_status"] == "matched" and byname["ستيك دجاج"]["match"]["item_id"] == "C19")
sl = byname["وجبة موظف بطاطا"]
check("the second reading 'وجبة موظف بطاطا' is the manager's staff-meal-with-fries (X02, 0 g meat) and the first is a misread of it: the menu settles it, both spellings are kept", sl["readings_agree"] is True and sl["name_settled_by_menu"] is True and sl["name_ar_alt"] == "وجبة سوطلف بطاطا" and sl["match_status"] == "matched" and sl["match"]["item_id"] == "X02")
check("the voids are kept in the package (not subtracted) and noted", ln["voids"][0]["name"] == "بيبسي" and ln["voids"][0]["amount"] == -1.98 and any("void" in f and f.startswith("NOTE") for f in fl))
check("the review sheet counts follow the lines: matched / suggested / needs human", re.search(r"Reading: 8 lines, (\d+) matched, (\d+) suggested, (\d+) needs human", r["review_markdown"]) and
      sum(int(x) for x in re.search(r"Reading: 8 lines, (\d+) matched, (\d+) suggested, (\d+) needs human", r["review_markdown"]).groups()) == 8)
check("the totals check says it does not apply (no amounts printed), and no AMOUNT blocker is raised", "Not applicable: the POS item list prints quantities only" in r["review_markdown"] and "AMOUNT" not in r["review_markdown"])
check("the review sheet is the analyst's tool output, unchanged, and ends with the standard line", r["review_markdown"].rstrip().endswith("Prepared for review. Nothing has been booked or written.") and open(r["review_path"], encoding="utf-8").read() == r["review_markdown"])

print("\n-- a second run never overwrites the first --")
h1 = hashlib.sha256(open(r["package_path"], "rb").read()).hexdigest()
r2, _ = run(day, drafts=drafts, day_status="ended", correction_reason="shadow test")
check("it writes DRAFT-...-002 and leaves 001 and its review sheet untouched", r2["draft_id"] == "DRAFT-2026-09-10-002" and hashlib.sha256(open(r["package_path"], "rb").read()).hexdigest() == h1 and os.path.isfile(r["review_path"]))

# ═══ the analyst's path ═══════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- the analyst validates and renders through the permission check --")
spy = []
real_exec = TE.execute_tool
async def spying(name, args, agent=None, domain=None):
    spy.append((name, agent, sorted(args)))
    return await real_exec(name, args, agent=agent, domain=domain)
with mock.patch.object(TE, "execute_tool", spying):
    r3, _ = run(day, drafts=drafts, day_status="ended", correction_reason="spy")
check("validate_draft_package then render_review_sheet are called as the ANALYST, in that order", [(n, a) for n, a, _ in spy] == [("validate_draft_package", "analyst"), ("render_review_sheet", "analyst")])
check("the same two tools are REFUSED for the CFO and for an agent with no role file (the permission check is real)", all("not permitted" in asyncio.run(real_exec(n, {"package": pk}, agent=a, domain="general")) for n in ("validate_draft_package", "render_review_sheet") for a in ("cfo", "chief_of_staff")))
spy.clear()
with mock.patch.object(TE, "execute_tool", spying):
    r4, d4 = run(day, drafts=os.path.join(TMP, "bad_rate"), rate=-5)
check("an INVALID package is not rendered: the validation report comes back instead, and no review file is written", r4["validation"]["valid"] is False and r4["review_markdown"].startswith("# Draft package check") and "**NOT VALID**" in r4["review_markdown"]
      and r4["review_path"] is None and [n for n, _, _ in spy] == ["validate_draft_package"] and not any(f.endswith(".review.md") for f in os.listdir(d4)))
check("the invalid draft itself is still saved (so the problem can be looked at)", os.path.isfile(r4["package_path"]))
r5, _ = run(day, drafts=os.path.join(TMP, "norev"), review=False)
check("review=False saves the draft and does not call the analyst", r5["validation"] is None and r5["review_markdown"] is None and os.path.isfile(r5["package_path"]))

# ═══ readings that disagree ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- when the two readings disagree --")
helper_b = copy.deepcopy(HELPER); helper_b["new"] = 2201.22
r6, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [HELPER, helper_b])}), drafts=os.path.join(TMP, "dis1"))
check("a figure the cash check rests on that the readings disagree about is a BLOCKER that names both values", any(f.startswith("BLOCKER") and "'new'" in f and "2200.32" in f and "2201.22" in f for f in r6["package"]["flags"]))
check("...and the blocker is on the review sheet", "- BLOCKER: Daily Helper 'new'" in r6["review_markdown"] or "BLOCKER: Daily Helper sheet 'new'" in r6["review_markdown"] or "'new': the two readings differ" in r6["review_markdown"])
main_b = copy.deepcopy(MAIN); main_b["net_sales"] = 3960.8
r7, _ = run(make_day("10-09-2026"), script=full_script({COLORS["main"]: ("pos_main_reading", [MAIN, main_b])}), drafts=os.path.join(TMP, "dis2"))
check("a Main Reading total that differs is a BLOCKER too, and the first reading is what the cash check used", any(f.startswith("BLOCKER") and "net_sales" in f for f in r7["package"]["flags"]) and r7["package"]["cash"]["pos_sales"] == 3816.8)
helper_r = copy.deepcopy(HELPER); helper_r["rows"][1] = {"subject": "veg.", "amount": 222.76}
r8, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [HELPER, helper_r])}), drafts=os.path.join(TMP, "dis3"))
check("an expense row that differs between readings (222.66 / 222.76) is settled by the sheet's own printed Total, which only 222.66 reaches: A's amount is used, a NOTE says so, and there is no BLOCKER",
      not any(f.startswith("BLOCKER") and "cash row" in f for f in r8["package"]["flags"]) and any(f.startswith("NOTE: the two readings of the Daily Helper row 'veg.' differ (222.66 / 222.76)") and "3,978.44" in f for f in r8["package"]["flags"])
      and r8["package"]["cash"]["expenses"][0]["amount"] == 222.66)
helper_nt, helper_nt_r = copy.deepcopy(HELPER), copy.deepcopy(HELPER)
helper_nt["total"] = helper_nt_r["total"] = None
helper_nt_r["rows"][1] = {"subject": "veg.", "amount": 222.76}
r8b, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [helper_nt, helper_nt_r])}), drafts=os.path.join(TMP, "dis3b"))
check("with no printed Total to prove it, an expense row that differs between readings is still a BLOCKER (and A's amount is used)", any(f.startswith("BLOCKER") and "cash row" in f for f in r8b["package"]["flags"]) and r8b["package"]["cash"]["expenses"][0]["amount"] == 222.66)
helper_both, helper_both_r = copy.deepcopy(HELPER), copy.deepcopy(HELPER)
helper_both["rows"][1] = {"subject": "veg.", "amount": 222.80}; helper_both_r["rows"][1] = {"subject": "veg.", "amount": 222.76}
r8c, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [helper_both, helper_both_r])}), drafts=os.path.join(TMP, "dis3c"))
check("when NEITHER reading reaches the printed Total it settles nothing: BLOCKER", any(f.startswith("BLOCKER") and "cash row" in f for f in r8c["package"]["flags"]))
# the 17 Sep case: Transfer 1358.76 / 1758.76, and one expense row labelled 'سيف' in one reading and '—' in the other
ROWS17 = [{"subject": "Transfer", "amount": 1358.76}, {"subject": "veg.", "amount": 396}, {"subject": "wish", "amount": 210.8}, {"subject": "سيف", "amount": 4.44}, {"subject": "water", "amount": 13.32}, {"subject": "salary", "amount": 1000}]
h17a = reading("daily_helper_sheet", date="17 Sept 2026", sales=2464.50, transactions=140, total=2983.32, value_usd=210, old=1124.76, expected=605.94, new=610, diff=4.06, rows=copy.deepcopy(ROWS17))
h17b = copy.deepcopy(h17a); h17b["rows"][0]["amount"] = 1758.76; h17b["rows"][3]["subject"] = "—"
r17, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [h17a, h17b])}), drafts=os.path.join(TMP, "h17"))
c17 = r17["package"]["cash"]
check("17 Sep: the Transfer read 1358.76 / 1758.76 is settled by the sheet's Total 2,983.32 (only 1358.76 reaches it), and the row labelled 'سيف' / '—' is ONE row: expenses 1413.76, not 1418.20, and no BLOCKER on the cash rows",
      c17["transfer_to_admin"] == 1358.76 and round(sum(e["amount"] for e in c17["expenses"]), 2) == 1413.76 and len(c17["expenses"]) == 4 and not any(f.startswith("BLOCKER") and "cash row" in f for f in r17["package"]["flags"]), str(c17))
check("...and says both things: the Total settled the Transfer, and a row was read with two labels and counted once", any("'Transfer' differ (1358.76 / 1758.76)" in f and "2,983.32" in f for f in r17["package"]["flags"]) and any("read with two different labels but the same amount" in f and "سيف / —" in f for f in r17["package"]["flags"]))
# 12 Oct: the Daily Helper has a Note column; text in it (two Arabic comments beside water and cleaning) was read as two extra rows with no amount
ROWS12 = [{"subject": "transfer", "amount": 1677.44}, {"subject": "veg.", "amount": 282.22}, {"subject": "wish", "amount": 135.88}, {"subject": "water", "amount": 34}, {"subject": "فاطورتين", "amount": None},
          {"subject": "ادوات تنظيف", "amount": 38.88}, {"subject": "للباق الشهر", "amount": None}]
h12a = reading("daily_helper_sheet", date="12 Oct 2026", sales=1914.4, transactions=69, total=2168.42, value_usd=550, value_ll=1.54, over_short=805.56, old=1504.22, expected=1250.2, new=1250.26, diff=0.06, rows=copy.deepcopy(ROWS12))
h12b = copy.deepcopy(h12a); h12b["rows"][4]["subject"] = "فاطورين"; h12b["rows"][6]["subject"] = "لباقي الشهر"
r12, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [h12a, h12b])}), drafts=os.path.join(TMP, "note12"))
c12 = r12["package"]["cash"]
check("12 Oct: the two Note-column comments (rows with no amount) are NOT expenses when the sheet's own Total 2,168.42 is reached without them: 3 expenses (282.22, 17, 38.88), no BLOCKER",
      [e["label"] for e in c12["expenses"]] == ["veg.", "water", "ادوات تنظيف"] and not any(f.startswith("BLOCKER") and "no readable amount" in f for f in r12["package"]["flags"]), str(c12["expenses"]))
check("...and a NOTE tells the manager they were treated as notes (not as expenses), and the odd 'two labels, same amount' note does not fire for them",
      any(f.startswith("NOTE: the Daily Helper has text in its Note column") and "فاطورتين" in f and "2,168.42" in f for f in r12["package"]["flags"]) and not any("two different labels but the same amount" in f for f in r12["package"]["flags"]))
h12c = copy.deepcopy(h12a); h12c["total"] = 1100.0; h12d = copy.deepcopy(h12c)
r12c, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [h12c, h12d])}), drafts=os.path.join(TMP, "note12c"))
check("guard: when the sheet's Total is NOT reached without the blank rows, they stay expenses with a missing amount (a real expense with an unreadable number is never silently dropped)",
      len(r12c["package"]["cash"]["expenses"]) == 5 and any(e["amount"] is None for e in r12c["package"]["cash"]["expenses"]) and not any(f.startswith("NOTE: the Daily Helper has text in its Note column") for f in r12c["package"]["flags"]))
h_ph = copy.deepcopy(HELPER); h_ph_r = copy.deepcopy(HELPER); h_ph_r["rows"].append({"subject": "extra", "amount": 7.0})
r_ph, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [h_ph, h_ph_r])}), drafts=os.path.join(TMP, "phantom"))
check("a row only ONE reading found is dropped when the sheet's Total is reached without it, and says so", len(r_ph["package"]["cash"]["expenses"]) == 3 and any("found by one reading only" in f and "this row dropped" in f for f in r_ph["package"]["flags"]) and not any(f.startswith("BLOCKER") and "cash row" in f for f in r_ph["package"]["flags"]))
h_in = copy.deepcopy(HELPER); h_in_r = copy.deepcopy(HELPER); h_in_r["rows"].append({"subject": "extra", "amount": 7.0}); h_in["total"] = h_in_r["total"] = 3985.44
r_in, _ = run(make_day("10-09-2026"), script=full_script({COLORS["helper"]: ("daily_helper_sheet", [h_in, h_in_r])}), drafts=os.path.join(TMP, "kept"))
check("...and kept when the Total is only reached WITH it, and says so", len(r_in["package"]["cash"]["expenses"]) == 4 and "NOTE: the two readings of the Daily Helper row 'extra' differ (7.0 / None, found by one reading only); the sheet's own Total 3,985.44 is reached only with 7.00, so that is used" in r_in["package"]["flags"], str([f for f in r_in["package"]["flags"] if "extra" in f]))
# a decimal slip in the Main Reading (15 Sep: 1.37725 and 2754.50)
main_slip = copy.deepcopy(MAIN)
for k_ in ("net_sales", "grand_total", "net_total"):
    main_slip[k_] = 3.8168
r_sl, _ = run(make_day("10-09-2026"), script=full_script({COLORS["main"]: ("pos_main_reading", [main_slip, MAIN])}), drafts=os.path.join(TMP, "slip15"))
check("15 Sep: a Main Reading figure one reading wrote 3.8168 and the other 3816.8 (same digits, decimal point elsewhere) is settled by the Daily Helper's sales 3,816.80: POS sales 3816.8, NOTEs, and NO BLOCKER on net_sales, grand_total or net_total",
      r_sl["package"]["cash"]["pos_sales"] == 3816.8 and not any(f.startswith("BLOCKER") and "Main Reading" in f for f in r_sl["package"]["flags"])
      and sum(f.startswith("NOTE: Main Reading") and "decimal point elsewhere" in f for f in r_sl["package"]["flags"]) == 3 and "readings_differ" not in r_sl["package"]["extraction"]["main_reading"], str([f for f in r_sl["package"]["flags"] if "Main Reading" in f]))
main_off = copy.deepcopy(MAIN); main_off["net_sales"] = 14000.2
r_of, _ = run(make_day("10-09-2026"), script=full_script({COLORS["main"]: ("pos_main_reading", [main_off, MAIN])}), drafts=os.path.join(TMP, "notslip"))
check("two readings that merely differ (14000.2 / 3816.8: not the same digits) are NOT treated as a slip: BLOCKER, as before", any(f.startswith("BLOCKER") and "net_sales" in f for f in r_of["package"]["flags"]))
main_far = copy.deepcopy(MAIN); main_far["net_sales"] = 38.168
r_fa, _ = run(make_day("10-09-2026"), script=full_script({COLORS["main"]: ("pos_main_reading", [main_far, copy.deepcopy(MAIN) | {"net_sales": 381.68}])}), drafts=os.path.join(TMP, "slipfar"))
check("a slip is only settled when the Daily Helper's sales agree with one of the two (38.168 / 381.68 against 3,816.80: neither): BLOCKER, no guess", any(f.startswith("BLOCKER") and "net_sales" in f for f in r_fa["package"]["flags"]))

# 'without ...', 'extra ...' and '... free' lines (the manager's ruling of 2026-10-08): not items, left out, named in a NOTE
mod_a = copy.deepcopy(SUMMARY); mod_b = copy.deepcopy(SUMMARY_B)
for sm in (mod_a, mod_b):
    sm["lines"] += L(("بدون ثوم", 6), ("زيادة حر", 4), ("بيبسي مجاني", 18))
mod_b["lines"][-1]["name"] = "بيبيسي مجاني"
r_mod, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [mod_a, EMPTY_HALF, mod_b, EMPTY_HALF])}), drafts=os.path.join(TMP, "mod"))
r_plain, _ = run(make_day("10-09-2026"), drafts=os.path.join(TMP, "plain"))
ml = r_mod["package"]["extraction"]["summary_by_items"]["lines"]
check("lines that say 'without', 'extra' or 'free' are left out of the item lines: the day has exactly the lines of the plain day, with the same quantities and the same matches",
      [(l["name_ar"], l["qty"], l["match"]["item_id"]) for l in ml] == [(l["name_ar"], l["qty"], l["match"]["item_id"]) for l in r_plain["package"]["extraction"]["summary_by_items"]["lines"]] and len(ml) == 8
      and not any("بدون" in l["name_ar"] or "زيادة" in l["name_ar"] or "مجاني" in l["name_ar"] for l in ml))
note = [f for f in r_mod["package"]["flags"] if "'without ...', 'extra ...' or '... free'" in f]
check("one NOTE names each left-out line with its quantity, grouped by kind (the free line was spelled two ways and counted once)", len(note) == 1 and "3 receipt line(s)" in note[0] and "without: بدون ثوم x6" in note[0]
      and "extra: زيادة حر x4" in note[0] and "free: بيبسي مجاني x18" in note[0], str(note))
check("a day without such lines has no such NOTE, and nothing else about the readings changed (no extra 'differ' flags)", not any("'without ...'" in f for f in r_plain["package"]["flags"])
      and sorted(f for f in r_mod["package"]["flags"] if "'without ...'" not in f) == sorted(r_plain["package"]["flags"]))
check("the draft with left-out lines still validates", r_mod["validation"]["valid"] if isinstance(r_mod.get("validation"), dict) and "valid" in r_mod["validation"] else True)

sum_q = copy.deepcopy(SUMMARY_B); sum_q["lines"][2]["qty"] = 35
r9, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [SUMMARY, EMPTY_HALF, sum_q, EMPTY_HALF])}), drafts=os.path.join(TMP, "dis4"))
pepsi = [l for l in r9["package"]["extraction"]["summary_by_items"]["lines"] if l["name_ar"] == "بيبسي"][0]
check("a quantity the readings disagree about keeps both numbers on the line, and the sheet shows 'or'", pepsi["qty"] == 34.0 and pepsi["qty_alt"] == 35.0 and pepsi["readings_agree"] is False and "34.0 or 35.0" in r9["review_markdown"])
check("such a line is held for a human even though its NAME matches exactly", pepsi["match_status"] == "matched" and re.search(r"\| بيبسي \|[^\n]*\| needs human \|", r9["review_markdown"]) is not None)

# ═══ missing documents ═════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- when a document is missing --")
r10, _ = run(make_day("10-09-2026", only=["summary"]), drafts=os.path.join(TMP, "miss1"))
f10 = r10["package"]["flags"]
check("with no cash document at all the cash check is a BLOCKER and is 'not usable' on the sheet, never invented", any(f.startswith("BLOCKER") and "cannot be done" in f for f in f10) and r10["package"]["cfo_results"]["cash"]["ok"] is False
      and "Not usable" in r10["review_markdown"] and "expected" not in r10["package"]["computed"])
check("no Main Reading photo is flagged", any("no 'Main Reading' photo" in f for f in f10))
r11, _ = run(make_day("10-09-2026", only=["helper", "main", "form"]), drafts=os.path.join(TMP, "miss2"))
check("no item-list photo: a BLOCKER, an empty list of lines, and the cash check still runs", any(f.startswith("BLOCKER") and "Summary By Items" in f for f in r11["package"]["flags"]) and r11["package"]["extraction"]["summary_by_items"]["lines"] == []
      and r11["package"]["computed"]["expected"] == 2200.34)
r12, _ = run(make_day("10-09-2026", with_sheet=False), drafts=os.path.join(TMP, "miss3"))
check("no consumption sheet: the meat variance is not checked, and that is said", r12["package"]["cfo_results"]["meat"] == {} and any("no consumption sheet with a row for this day was found" in f for f in r12["package"]["flags"]))
check("...and the review sheet says 'Not run' instead of showing a meat check that did not happen", "## Meat check" + chr(10) + "Not run: no check_meat_variance result was supplied." in r12["review_markdown"] and "- chicken:" not in r12["review_markdown"])

print("\n-- which consumption sheet the meat weights come from --")
def xlsx(path, rows, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Daily Beef & Chicken Stock Consumption"]); ws.append([]); ws.append(["Branch Name :", "downtown"]); ws.append([])
    ws.append(["Date", "Item", "Opening", "Received", "Total", "Used", "Shrink", "Yield", "Sold", "Waste", "After", "Remaining Raw", "Notes"])
    for d, meat, o, rcv, used, sold, waste, rem in rows:
        ws.append([datetime.datetime.fromisoformat(d), meat, o, rcv, o + rcv, used, 0.45, round(used * 0.55, 3), sold, waste, 0, rem, None])
    wb.save(path)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path
abo = os.path.join(TMP, "abo_tree")
old_v = xlsx(os.path.join(abo, "09-2026", "10-09-2026", "Branch Beef  ^0 Chicken Daily consumption 10-09-2026.xlsx"),
             [("2026-09-10", "Beef", 21.14, 10, 10.9, 4.91, 0.61, 15.74), ("2026-09-10", "Chicken", 172.226, 68, 72.88, 37, 4.9, 167.346)], mtime=1_788_000_000)
new_v = xlsx(os.path.join(abo, "09-2026", "30-09-2026", "Branch Beef  ^0 Chicken Daily consumption 30-09-2026.xlsx"),
             [("2026-09-10", "Beef", 24.14, 12, 10.9, 4.91, 0.61, 25.34), ("2026-09-10", "Chicken", 172.226, 68, 72.88, 37, 4.9, 167.346), ("2026-09-30", "Beef", 1, 1, 1, 1, 0, 1)], mtime=1_789_000_000)
other_month = xlsx(os.path.join(abo, "10-2026", "05-10-2026", "Branch Beef  ^0 Chicken Daily consumption 05-10-2026.xlsx"), [("2026-10-05", "Beef", 5, 1, 1, 1, 0, 5)], mtime=1_790_000_000)
f1, n1 = P.find_consumption_sheet(os.path.dirname(old_v), "2026-09-10", abo)
check("the sheet with the LATEST DATE IN ITS NAME that has the day is used, not the day's own older copy", f1 == new_v and "30-09-2026" in n1 and "latest-dated version" in n1, n1)
check("a newer sheet that has no row for that day (October) is ignored", f1 != other_month)
resynced = xlsx(os.path.join(abo, "09-2026", "04-09-2026", "Branch Beef  & Chicken Daily consumption 04-09-2026 (1).xlsx"),
                [("2026-09-10", "Beef", 21.14, 10, 10.9, 4.91, 0.61, 15.74)], mtime=1_795_000_000)
f1b, n1b = P.find_consumption_sheet(os.path.dirname(old_v), "2026-09-10", abo)
check("an older-named copy that was re-saved LATER (the 04-09 copies were re-synced on 6 Oct) does not beat the corrected 30-09 sheet", f1b == new_v and os.path.getmtime(resynced) > os.path.getmtime(new_v), n1b)
check("the date is read from the end of the name: 04-09-2026 (1) -> (2026, 9, 4), '30-09-2026' -> (2026, 9, 30), no date -> (0, 0, 0)", P._sheet_name_date(resynced) == (2026, 9, 4) and P._sheet_name_date(new_v) == (2026, 9, 30) and P._sheet_name_date("C:/x/sheet.xlsx") == (0, 0, 0))
f2, n2 = P.find_consumption_sheet(os.path.dirname(old_v), "2026-09-10", os.path.join(TMP, "nothing_here"))
check("with no Demo Bistro folder to search, the day's own copy is used and the note says so", f2 == old_v and "the day's own folder" in n2)
f3, n3 = P.find_consumption_sheet(os.path.dirname(old_v), "2026-09-10", abo, meat_sheet=old_v)
check("an explicit sheet always wins", f3 == old_v and "given for this run" in n3)
f4, n4 = P.find_consumption_sheet(os.path.dirname(old_v), "2026-09-10", abo, meat_sheet=os.path.join(abo, "missing.xlsx"))
check("an explicit sheet that does not exist is an error, never a silent fallback", f4 is None and "does not exist" in n4)
f5, n5 = P.find_consumption_sheet(os.path.dirname(old_v), "2026-12-25", abo)
check("a day that no sheet has gives None and says so", f5 is None and "no consumption sheet" in n5)
d_new = make_day("10-09-2026", with_sheet=False)
shutil.copy(old_v, d_new)
for f_ in os.listdir(d_new):
    if f_.endswith('.xlsx'):
        os.utime(os.path.join(d_new, f_), (1_788_000_000, 1_788_000_000))      # the day's own copy was saved before the corrected one
rn, _ = run(d_new, drafts=os.path.join(TMP, "meat_new"), restaurant_root=abo)
mc = rn["package"]["cfo_results"]["meat"]["beef"]
check("the draft uses the corrected beef figures: opening 24.14 kg, received 12 kg (not the 21.14 / 10 of the older copy)", mc["inputs"]["starting_kg"] == 24.14 and mc["inputs"]["added_kg"] == 12.0)
check("...and says which file they came from, and lists that file as a source", any(f.startswith("NOTE: the meat weights are from the latest-dated version") and "30-09-2026" in f for f in rn["package"]["flags"]) and any(x["file"].endswith("30-09-2026.xlsx") and x["kind"] == "consumption_xlsx" for x in rn["package"]["sources"]))
rm, _ = run(d_new, drafts=os.path.join(TMP, "meat_explicit"), restaurant_root=abo, meat_sheet=old_v)
check("with --meat-sheet pointing at the older copy the older figures are used", rm["package"]["cfo_results"]["meat"]["beef"]["inputs"]["starting_kg"] == 21.14)

print("\n-- a name the two readings spell differently: the menu settles it only when one reading is an exact menu name --")
sA = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("نص كيلو ساندويش دجاج", 2), ("تساندويش لحمة كبير", 12), ("غير مفهوم", 3), ("ساندويش لحمة صغير", 6)), voids=[])
sB = reading("pos_summary_by_items", title_visible=True, lines=L(("تساندويش دجاج صغير", 29), ("ساندويش دجاج صغير", 16), ("بيبسي", 35), ("نص كيلو تساندويش دجاج", 2), ("ساندويش لحمة كبير", 12), ("خير مفهوم", 3), ("ساندويش لحمة صغير", 6)), voids=[])
rsn, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [sA, EMPTY_HALF, sB, EMPTY_HALF])}), drafts=os.path.join(TMP, "settle"))
bl = {l["line"]: l for l in rsn["package"]["extraction"]["summary_by_items"]["lines"]}
check("an exact menu name in the first reading settles a stray letter in the second: the line agrees, keeps BOTH spellings, and is matched", bl[1]["readings_agree"] is True and bl[1]["name_ar"] == "ساندويش دجاج صغير" and bl[1]["name_ar_alt"] == "تساندويش دجاج صغير"
      and bl[1]["name_settled_by_menu"] is True and bl[1]["match_status"] == "matched" and bl[4]["readings_agree"] is True and bl[4]["name_settled_by_menu"] is True)
check("the quantity still has to agree: the same exact name with a different quantity is held for a human", bl[3]["readings_agree"] is False and bl[3]["qty"] == 34.0 and bl[3]["qty_alt"] == 35.0 and "name_settled_by_menu" not in bl[3])
check("if the SECOND reading is the exact menu name and the first is not, the exact one becomes the line's name and the other is kept as the alternative", bl[5]["readings_agree"] is True and bl[5]["name_ar"] == "ساندويش لحمة كبير" and bl[5]["name_ar_alt"] == "تساندويش لحمة كبير" and bl[5]["name_settled_by_menu"] is True and bl[5]["match"]["item_id"] == "C11")
check("when the two readings name DIFFERENT menu items (كبير vs صغير, same quantity), the menu settles nothing: the line stays disagreeing", bl[2]["readings_agree"] is False and bl[2]["name_ar_alt"] == "ساندويش دجاج صغير" and "name_settled_by_menu" not in bl[2])
check("when neither reading is an exact menu name, the line stays disagreeing", bl[6]["readings_agree"] is False and "name_settled_by_menu" not in bl[6])
check("a line the readings agree on needs no settling", bl[7]["readings_agree"] is True and "name_settled_by_menu" not in bl[7] and "name_ar_alt" not in bl[7])
check("the validator accepts the settled lines, and the sheet says the other reading spelled it differently", rsn["validation"]["valid"] is True and "Other reading spelled it differently; this one is an exact menu name" in rsn["review_markdown"], str(rsn["validation"]["errors"][:2]))
check("the sheet still shows both spellings of a settled line", "ساندويش دجاج صغير or تساندويش دجاج صغير" in rsn["review_markdown"])

print("\n-- a row that only ONE of the two readings saw --")
sA1 = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("نص كيلو ساندويش دجاج", 2)))
sB1 = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34)))
r1s, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [sA1, EMPTY_HALF, sB1, EMPTY_HALF])}), drafts=os.path.join(TMP, "oneside"))
ol = {l["line"]: l for l in r1s["package"]["extraction"]["summary_by_items"]["lines"]}
check("a row only one reading saw is shown as a disagreement for a human (once on the sheet), and its second reading is an explicit null (the other reading had nothing)", ol[4]["readings_agree"] is False and "qty_alt" in ol[4] and ol[4]["qty_alt"] is None and r1s["review_markdown"].count("Readings disagree") == 1)
check("the analyst's validator accepts it (a disagreement with no second reading used to make the whole draft invalid), and the sheet is rendered", r1s["validation"]["valid"] is True and r1s["review_markdown"], str(r1s["validation"].get("errors", [])[:2]))
check("the rows both readings saw are unchanged", all(ol[n]["readings_agree"] is True and "qty_alt" not in ol[n] for n in (1, 2, 3)))

print("\n-- one receipt row spelled two ways by the two readings (the Miranda row of 7 days) --")
sM_A = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ميرندا", 3), ("بيبسي", 34), ("ساندويش دجاج كبير", 5)))
sM_B = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ميRANDA", 3), ("بيبسي", 34), ("بيبسي دايت", 5)))
rmn, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [sM_A, EMPTY_HALF, sM_B, EMPTY_HALF])}), drafts=os.path.join(TMP, "twice"))
ml = rmn["package"]["extraction"]["summary_by_items"]["lines"]
mir = [l for l in ml if l["match"]["item_id"] == "C41"]
check("two neighbouring rows, each seen by one reading, the same quantity and the same menu item (Miranda 3 and Miranda 3) are ONE line that keeps both spellings",
      len(mir) == 1 and mir[0]["qty"] == 3.0 and {mir[0]["name_ar"], mir[0].get("name_ar_alt")} == {"ميرندا", "ميRANDA"}, str([(l["name_ar"], l["qty"]) for l in ml]))
check("the draft says so in a NOTE that names both spellings and the quantity", any(f.startswith("NOTE:") and "ONE line" in f and "ميرندا" in f and "ميRANDA" in f and "x3" in f for f in rmn["package"]["flags"]))
dc = [l for l in ml if l["match"]["item_id"] in ("C02", "X01")]
check("two DIFFERENT items that happen to share a quantity (large chicken sandwich 5 and Diet Pepsi 5, each seen by one reading) are never merged", len(dc) == 2 and all(l["qty"] == 5.0 for l in dc), str([(l["name_ar"], l["qty"], l["match"]["item_id"]) for l in ml]))
check("five lines in all: small chicken, Miranda (once), Pepsi, and the two different items", len(ml) == 5, str(len(ml)))
check("the draft with the merged row is valid", rmn["validation"]["valid"] is True, str(rmn["validation"].get("errors", [])[:2]))

sG_A = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ميزاندا", 2), ("بيبسي", 34)))
sG_B = reading("pos_summary_by_items", title_visible=True, lines=L(("ساندويش دجاج صغير", 29), ("ميارددا", 2), ("بيبسي", 34)))
rgb, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [sG_A, EMPTY_HALF, sG_B, EMPTY_HALF])}), drafts=os.path.join(TMP, "twice_garble"))
gl = rgb["package"]["extraction"]["summary_by_items"]["lines"]
check("the same row also merges when one spelling is a sure match and the other is a bad garble (score under 85) of the SAME item (the Oct 8 Miranda row: 'ميزاندا' and 'ميارددا')",
      [(l["name_ar"], l["qty"]) for l in gl if l["match"]["item_id"] == "C41"] == [("ميزاندا", 2.0)] and len(gl) == 3 and {gl[1].get("name_ar_alt")} == {"ميارددا"}, str([(l["name_ar"], l["name_ar_alt"] if "name_ar_alt" in l else None, l["qty"], l["match"]["item_id"]) for l in gl]))

print("\n-- the Daily Helper screenshot kept in the central data folder, and picking the best document of a kind --")
abo2 = os.path.join(TMP, "abo_central")
os.makedirs(os.path.join(abo2, "data"), exist_ok=True)
for name, col in (("10-09-2026.jpeg", (77, 77, 77)), ("1-09-2026.jpeg", (78, 78, 78)), ("11-09-2026.jpeg", (79, 79, 79))):
    Image.new("RGB", (300, 520), col).save(os.path.join(abo2, "data", name), "JPEG", quality=100)
open(os.path.join(abo2, "data", "10-09-2026.txt"), "w").write("not a photo")
cf = P.central_files("2026-09-10", abo2)
check("central_files finds the day's screenshot by its name (with or without a leading zero) and ignores other days and other file types", [os.path.basename(x) for x in cf["daily_helper_sheet"]] == ["10-09-2026.jpeg"]
      and [os.path.basename(x) for x in P.central_files("2026-09-01", abo2)["daily_helper_sheet"]] == ["1-09-2026.jpeg"] and P.central_files("2026-09-20", abo2) == {} and P.central_files("2026-09-10", os.path.join(TMP, "none")) == {})
central_helper = reading("daily_helper_sheet", date="10 Sept 2026", sales=3816.8, old=2361.98, new=2200.32, expected=2200.34, diff=-0.02, value_usd=1796,
                         rows=[{"subject": "Transfer", "amount": 3553.32}, {"subject": "veg.", "amount": 222.66}, {"subject": "wish", "amount": 166.8}, {"subject": "water", "amount": 16.66}, {"subject": "staff", "amount": 19.0}])
script_c = {**full_script(), (77, 77, 77): ("daily_helper_sheet", [central_helper, central_helper])}
no_helper_day = make_day("10-09-2026", only=["main", "form", "summary"])
rc, _ = run(no_helper_day, script=script_c, drafts=os.path.join(TMP, "central1"), restaurant_root=abo2)
check("a day whose folder has no Daily Helper screenshot takes it from the central data folder, says so, and does the full cash check with it", rc["package"]["cash"]["source"] == "the Daily Helper screenshot"
      and rc["package"]["computed"]["expected"] == 2200.34 and rc["package"]["cash"]["actual_cash_count"] == 2200.32 and any(f.startswith("NOTE: the daily helper sheet 10-09-2026.jpeg was not in the day's folder") for f in rc["package"]["flags"])
      and any(x["file"] == "10-09-2026.jpeg" and x["kind"] == "daily_helper_sheet" for x in rc["package"]["sources"]))
spy_calls = Fake(script_c)
with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    P.run_day(make_day("10-09-2026"), client=spy_calls, drafts_dir=os.path.join(TMP, "central2"), glossary=RG, log=lambda m: None, restaurant_root=abo2)
check("when the day's own folder HAS a usable Daily Helper screenshot the central one is never read", (77, 77, 77) not in spy_calls.n)
dup_central = reading("daily_helper_sheet")
rd, _ = run(make_day("10-09-2026", only=["main", "form", "summary"]), script={**full_script(), (77, 77, 77): ("daily_helper_sheet", [dup_central, dup_central])}, drafts=os.path.join(TMP, "central3"), restaurant_root=abo2)
check("a central file that turns out to have no usable figures is ignored and flagged, never trusted", any("looked like a daily helper sheet but none of its key figures could be read" in f for f in rd["package"]["flags"]) and rd["package"]["cash"]["source"] == "the handwritten Daily Cash form")

bogus = reading("daily_cash_form", branch=None, expenses=[{"description": "Bioclare dishwashing 25 L.", "amount_usd": None, "amount_lbp": None}, {"description": "Bioclare floor cleaner 5.5 L", "amount_usd": None, "amount_lbp": None}])
bogus_b = copy.deepcopy(bogus); bogus_b["expenses"][0]["description"] = "Bioclar dishwashing 25 L."
day_b = make_day("10-09-2026", only=["main", "form", "summary"])
Image.new("RGB", (300, 520), (88, 88, 88)).save(os.path.join(day_b, "a_order_sheet.jpeg"), "JPEG", quality=100)
rb, _ = run(day_b, script={**full_script(), (88, 88, 88): ("daily_cash_form", [bogus, bogus_b])}, drafts=os.path.join(TMP, "bogus"), restaurant_root=os.path.join(TMP, "no_abo"))
fl_b = rb["package"]["flags"]
check("an order-request sheet labelled 'Daily Cash form' (every key figure empty) is IGNORED even though its file name sorts first, and is flagged", any("a_order_sheet.jpeg looked like a daily cash form but none of its key figures could be read" in f for f in fl_b)
      and not any("Bioclare" in f for f in fl_b) and not any(f.startswith("BLOCKER") for f in fl_b) and rb["package"]["cash"]["old_cash"] == 2361.98 and rb["package"]["cash"]["transfer_to_admin"] == 3553.32)
check("...it is not a source, and it is listed as classified-but-unusable", all(x["file"] != "a_order_sheet.jpeg" for x in rb["package"]["sources"]) and rb["package"]["files_not_read"]["classified_but_unusable"] == [["a_order_sheet.jpeg", "daily_cash_form"]])
thin = reading("daily_cash_form", date="10/09/2026", opening_cash_balance=2361.98)
day_t = make_day("10-09-2026", only=["main", "form", "summary"])
Image.new("RGB", (300, 520), (89, 89, 89)).save(os.path.join(day_t, "0_thin_form.jpeg"), "JPEG", quality=100)
rt, _ = run(day_t, script={**full_script(), (89, 89, 89): ("daily_cash_form", [thin, thin])}, drafts=os.path.join(TMP, "thin"), restaurant_root=os.path.join(TMP, "no_abo"))
check("of two usable forms the one with MORE of its key figures read wins, whatever the file names", rt["package"]["cash"]["transfer_to_admin"] == 3553.32 and any("more than one Daily Cash form photo was found; only form.jpeg was used" in f for f in rt["package"]["flags"]))
slip = reading("pos_main_reading", printed_at="10-09-2026 13:31:33", first_invoice=203950, last_invoice=203950, number_of_invoices=1, grand_total=1799580.0, net_total=20.22, net_sales=1799580.0, payments=[{"method": "Cash", "amount": 1799580.0}], payments_total=1799580.0)
for nm_, col_ in (("0_slip.jpeg", (88, 88, 88)), ("z_slip.jpeg", (87, 87, 87))):
    Image.new("RGB", (300, 520), col_).save(os.path.join(day_t, nm_), "JPEG", quality=100)
rs_, _ = run(day_t, script={**full_script(), (88, 88, 88): ("pos_main_reading", [slip, slip]), (87, 87, 87): ("pos_main_reading", [slip, slip]), (89, 89, 89): ("daily_cash_form", [thin, thin])}, drafts=os.path.join(TMP, "slip"), restaurant_root=os.path.join(TMP, "no_abo"))
check("of several Main Reading printouts with the same number of figures read, the end-of-day report (the most invoices) wins over a single-invoice slip, whatever the file names", rs_["package"]["cash"]["pos_sales"] == 3816.8
      and any("only main.jpeg was used" in f for f in rs_["package"]["flags"]) and P._invoice_span(slip and {"comparison": {"fields": {"first_invoice": {"value": 5}, "last_invoice": {"value": 9}}}}) == 5
      and P._invoice_span({"comparison": {"fields": {}}}) == 0)
day_q = make_day("10-09-2026")
Image.new("RGB", (300, 520), (91, 91, 91)).save(os.path.join(day_q, "bioclare_order_sheet.jpeg"), "JPEG", quality=100)
Image.new("RGB", (300, 520), (92, 92, 92)).save(os.path.join(day_q, "schedule.jpeg"), "JPEG", quality=100)
rq, _ = run(day_q, script={**full_script(), (91, 91, 91): ("supply_trap", []), (92, 92, 92): ("other_trap", [])}, drafts=os.path.join(TMP, "supply"), restaurant_root=os.path.join(TMP, "no_abo"))
rq0, _ = run(make_day("10-09-2026"), drafts=os.path.join(TMP, "supply0"), restaurant_root=os.path.join(TMP, "no_abo"))
check("a supply order sheet that looks like an item list adds NO lines: the draft's item lines are exactly those of the same day without it", rq["package"]["extraction"]["summary_by_items"]["lines"] == rq0["package"]["extraction"]["summary_by_items"]["lines"]
      and len(rq["package"]["extraction"]["summary_by_items"]["lines"]) == 8)
check("it is not a source, it is listed as a supply/expense list (and the other non-sales photo as not a report photo), and the draft says why", all(x["file"] not in ("bioclare_order_sheet.jpeg", "schedule.jpeg") for x in rq["package"]["sources"])
      and rq["package"]["files_not_read"]["supply_or_expense_lists"] == ["bioclare_order_sheet.jpeg"] and "schedule.jpeg" in rq["package"]["files_not_read"]["not_a_menu_report_photo"]
      and any(f.startswith("NOTE: bioclare_order_sheet.jpeg first looked like the POS item list but is not one (grid lines and cleaning products)") for f in rq["package"]["flags"]))
check("the draft with the supply sheet is as valid as the one without it, and no 'item lines joined from several photos' warning appears", rq["validation"]["valid"] is True and not any("item list was read from" in f for f in rq["package"]["flags"]))

T20 = [("وجبة موظف بطاطا", 16), ("وجبة موظف دجاج", 18), ("ديمو ناجتس دجاج ولحمه", 2), ("عرض 2 ساندويش دجاج تورتيلا", 5), ("عرض 2 ساندويش كبير", 6), ("عرض ديمو ناجتس", 6), ("عرض دجاج تورتيلا+صغير", 1),
       ("سلايدر 6 دجاج+18 قطعه تورتيلا", 19), ("سلايدر دجاج", 8), ("بيبسي", 46), ("بيبسي دايت", 6), ("سفن اب", 14), ("لبن عيران", 22), ("مياه معدنية", 24), ("ميراندا", 4), ("بطاطا صغير", 10),
       ("بطاطا كبير", 5), ("سندويش بطاطا كلاسيكي", 1), ("ستيك دجاج", 13), ("راب ساندويش دجاج", 5)]
def part20(lo, hi, shift=False):
    return reading("pos_summary_by_items", title_visible=True, lines=[{"name": T20[k][0], "qty": (T20[k + 1][1] if k + 1 < len(T20) else None) if shift else T20[k][1]} for k in range(lo, hi)])
rr_, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [part20(0, 15), part20(6, 20), part20(0, 15), part20(6, 20, shift=True)])}), drafts=os.path.join(TMP, "realign"))
ll_ = rr_["package"]["extraction"]["summary_by_items"]["lines"]
check("a second reading whose lower piece has every quantity one row out of place (tilted photo) still gives the 20 true lines, each with both readings agreeing: no duplicated rows, no wall of disagreements",
      [(l["name_ar"], l["qty"]) for l in ll_] == [(n, float(q)) for n, q in T20] and all(l["readings_agree"] for l in ll_))
check("the draft says a reading was realigned (CHECK), and that the other reading is the check", any(f.startswith("CHECK:") and "were found one row out of place after a cut" in f and "quantities of 5 rows" in f for f in rr_["package"]["flags"]) and rr_["validation"]["valid"] is True)
sA2 = reading("pos_summary_by_items", title_visible=True, lines=L(("كيلو ساندويش دجاج", 1), ("نص كيلو ساندوييش دجاجج", 2), ("ساندويش دجاج صغير", 10), ("ساندويش لحمة كبير", 4), ("وجبة دجاج تورتيلا مقطعة", 5)))
sB2 = reading("pos_summary_by_items", title_visible=True, lines=L(("كيلو ساندويش دجاج", 1), ("نص كيلو ساندويش دجاجج", 2), ("ساندويش دجاج صغير", 11), ("ساندويش لحمة كبير", 4), ("وجبة دجاج تورتيلا مقطعة", 5)))
rsp, _ = run(make_day("10-09-2026"), script=full_script({COLORS["summary"]: ("pos_summary_by_items", [sA2, EMPTY_HALF, sB2, EMPTY_HALF])}), drafts=os.path.join(TMP, "spelling"))
mlp = rsp["package"]["computed"]["meat_from_lines"]
check("a line the two readings only SPELL differently (same quantity, the name still suggested) is counted in the 'with suggestions' figure but NOT in the confirmed one: kilo 1.0 + 3 matched meals 0.7 confirmed; + half-kilo x2 = 1.0 with suggestions",
      mlp["matched"]["chicken"] == round(1.0 + 5 * 0.14, 3) and mlp["with_suggested"]["chicken"] == round(1.0 + 5 * 0.14 + 1.0, 3) and [x["name"] for x in mlp["lines_spelling_only"]] == ["نص كيلو ساندوييش دجاجج"], str(mlp))
check("a line whose two readings give DIFFERENT quantities (10 or 11) is counted in neither, and is listed with that reason", [x["name"] for x in mlp["lines_not_counted"]] == ["ساندويش دجاج صغير"] and "quantity" in mlp["lines_not_counted"][0]["why"]
      and mlp["lines_counted"] == 3 and mlp["lines_counted_with_suggestions"] == 4 and mlp["lines_total"] == 5)
check("the beef line both readings agree on is counted as before, and the draft says how many lines were counted only with the suggestions (exact wording, with the sheet's figure)",
      mlp["matched"]["beef"] == 0.52 and "NOTE: chicken: the item list implies 1.7 kg sold from 3 confirmed lines, and 2.7 kg when the suggested lines and the 1 lines the readings only spelled differently are counted too (4 lines); the consumption sheet says 37.0 kg sold" in rsp["package"]["flags"], str([f for f in rsp["package"]["flags"] if "chicken:" in f]))

def rows_of(lo, hi):
    return [{"name": T20[k][0], "qty": float(T20[k][1]), "readings_agree": True} for k in range(lo, hi)]
top, bottom = rows_of(0, 15), rows_of(6, 20)
j1, u1, d1 = P._join_photos([top, bottom])
j2, u2, d2 = P._join_photos([bottom, top])
check("two photos of one scrolling list that overlap are joined into ONE list, rows both show counted once: the top photo first", [r["name"] for r in j1] == [n for n, _ in T20] and u1 == 0 and d1 == 9)
check("...and the same when the photos arrive in the other order (the bottom photo first): the list comes out top to bottom, nothing doubled", [r["name"] for r in j2] == [n for n, _ in T20] and u2 == 0 and d2 == 9, str(len(j2)))
check("photos with no row in common are joined in the order given and the join is counted (a CHECK in the draft), as before", P._join_photos([rows_of(0, 6), rows_of(12, 20)])[1:] == (1, 0) and len(P._join_photos([rows_of(0, 6), rows_of(12, 20)])[0]) == 14)
wide, w_un, w_dr = P._join_photos([rows_of(0, 30 if False else 20)[:20], rows_of(0, 20)])
check("a long overlap (20 shared rows, more than the strip window of 14) is still recognised", w_un == 0 and w_dr == 20 and len(wide) == 20)
check("one photo is just returned", P._join_photos([top]) == (top, 0, 0))
dq = make_day("10-09-2026", only=["main", "form", "summary", "helper"])
Image.new("RGB", (300, 520), COLORS["summary2"]).save(os.path.join(dq, "summary2.jpeg"), "JPEG", quality=100)
def part_rows(lo, hi):
    return reading("pos_summary_by_items", title_visible=True, lines=[{"name": T20[k][0], "qty": T20[k][1]} for k in range(lo, hi)])
for first_is_bigger, nm in ((True, "overlap_a"), (False, "overlap_b")):
    big, small = (part_rows(0, 15), part_rows(6, 20)) if first_is_bigger else (part_rows(6, 20), part_rows(0, 12))
    ro, _ = run(dq, script={**full_script(), COLORS["summary"]: ("pos_summary_by_items", [big, EMPTY_HALF, big, EMPTY_HALF]), COLORS["summary2"]: ("pos_summary_by_items", [small, EMPTY_HALF, small, EMPTY_HALF])},
                drafts=os.path.join(TMP, nm), restaurant_root=os.path.join(TMP, "no_abo"))
    lo = ro["package"]["extraction"]["summary_by_items"]["lines"]
    check(f"end to end ({nm}): two photos of one list give the 20 true lines once each, in order, and the draft says how many rows both photos showed",
          [(l["name_ar"], l["qty"]) for l in lo] == [(n, float(q)) for n, q in T20] and any(f.startswith("NOTE: the item list was read from 2 photos") and "rows that two photos both show were counted once" in f for f in ro["package"]["flags"]), str(len(lo)))

one_row = reading("pos_summary_by_items", title_visible=True, lines=L(("بيبسي", 1)))
day_s = make_day("10-09-2026")
Image.new("RGB", (300, 520), (90, 90, 90)).save(os.path.join(day_s, "0_stray.jpeg"), "JPEG", quality=100)
rs, _ = run(day_s, script={**full_script(), (90, 90, 90): ("pos_summary_by_items", [one_row, EMPTY_HALF, one_row, EMPTY_HALF])}, drafts=os.path.join(TMP, "stray"))
check("a picture labelled 'item list' with fewer than 3 rows is not an item list: ignored and flagged, the real list is used alone", len(rs["package"]["extraction"]["summary_by_items"]["lines"]) == 8 and any("0_stray.jpeg looked like a pos summary by items" in f for f in rs["package"]["flags"]))

print("\n-- cash from the handwritten form when there is no Daily Helper screenshot --")
r13, _ = run(make_day("10-09-2026", only=["main", "form", "summary"]), drafts=os.path.join(TMP, "form"))
c13 = r13["package"]["cash"]
check("the form's rows are used: opening, wallet, transfer; the L.L. expense is converted at the day's rate and SAID", c13["old_cash"] == 2361.98 and c13["wallet"] == 65.0 and c13["transfer_to_admin"] == 3553.32
      and {e["label"]: e["amount"] for e in c13["expenses"]} == {"Veg": 222.66, "water": 16.66} and any("water" in f and "1,499,400" in f and "16.66" in f for f in r13["package"]["flags"]))
check("with no full cash count there is no variance: the check is incomplete and says what is missing", r13["package"]["cfo_results"]["cash"]["status"] == "incomplete" and "diff" not in r13["package"]["computed"]
      and any("no full cash count" in f for f in r13["package"]["flags"]))
check("and its POS sales come from the Main Reading, not the form's part-day figure", c13["pos_sales"] == 3816.8 and c13["source"] == "the handwritten Daily Cash form")

print("\n-- several photos of one kind --")
day2 = make_day("10-09-2026")
Image.new("RGB", (300, 520), COLORS["summary2"]).save(os.path.join(day2, "summary2.jpeg"), "JPEG", quality=100)
more = reading("pos_summary_by_items", title_visible=False, lines=L(("لبن عيران", 16), ("مياه معدنية", 14), ("بطاطا صغير", 7)))
r14, _ = run(day2, script={**full_script(), COLORS["summary2"]: ("pos_summary_by_items", [more, EMPTY_HALF, more, EMPTY_HALF])}, drafts=os.path.join(TMP, "multi"))
check("two item-list photos are joined in file-name order and the join is flagged for a human to check", len(r14["package"]["extraction"]["summary_by_items"]["lines"]) == 11 and any("read from 2 photos" in f for f in r14["package"]["flags"]))
helper2 = Image.new("RGB", (300, 520), (12, 12, 12)); helper2.save(os.path.join(day2, "helper2.jpeg"), "JPEG", quality=100)
r15, _ = run(day2, script={**full_script(), COLORS["summary2"]: ("pos_summary_by_items", [more, EMPTY_HALF, more, EMPTY_HALF]), (12, 12, 12): ("daily_helper_sheet", [HELPER, HELPER])}, drafts=os.path.join(TMP, "multi2"))
check("two Daily Helper photos: the first (by file name) is used and the extra one is flagged", any("more than one Daily Helper photo" in f and "helper.jpeg" in f for f in r15["package"]["flags"]))

# ═══ failure modes ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- bad input and failures --")
def raises(**kw):
    try:
        P.run_day(**kw); return None
    except P.PipelineError as e:
        return str(e)
check("a folder that does not exist is refused", "not a folder" in (raises(folder=os.path.join(TMP, "nope"), client=Fake({}), glossary=RG, drafts_dir=TMP) or ""))
check("a folder with no date in its name needs --day", "business day is not in the folder name" in (raises(folder=make_day("photos"), client=Fake({}), glossary=RG, drafts_dir=TMP) or ""))
check("an impossible --day is refused", "not a date" in (raises(folder=day, business_day="2026-02-30", client=Fake({}), glossary=RG, drafts_dir=TMP) or ""))
check("an ended day needs a correction reason (the app's correction mode asks for one)", "correction reason" in (raises(folder=day, day_status="ended", client=Fake({}), glossary=RG, drafts_dir=TMP) or ""))
check("a day status other than open or ended is refused", "open or ended" in (raises(folder=day, day_status="closed", client=Fake({}), glossary=RG, drafts_dir=TMP) or ""))
with mock.patch.object(M, "get_menu_glossary", lambda *a, **k: {"ok": False, "errors": ["the cheat sheet PDF changed"]}):
    msg = raises(folder=day, client=Fake({}), drafts_dir=TMP, log=lambda m: None)
check("if the menu glossary cannot be loaded (for example the PDF changed) nothing is matched and nothing is saved", msg and "menu glossary cannot be loaded" in msg and "changed" in msg)
class Flaky:
    def ask(self, png, prompt, schema):
        color = Image.open(io.BytesIO(png)).convert("RGB").getpixel((3, 3))
        if color == COLORS["summary"]:
            raise V.VisionError("no answer")
        return Fake(full_script()).ask(png, prompt, schema)
with mock.patch.object(V, "prepare_gpu", side_effect=AssertionError), mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    r16 = P.run_day(make_day("10-09-2026"), client=Flaky(), drafts_dir=os.path.join(TMP, "flaky"), glossary=RG, log=lambda m: None, restaurant_root=os.path.join(TMP, "no_abo"))
check("a photo the model could not read becomes a BLOCKER in the draft (the rest of the day is still done)", any(f.startswith("BLOCKER") and "could not be read" in f for f in r16["package"]["flags"]) and r16["package"]["computed"]["expected"] == 2200.34)

# ═══ boundaries ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- boundaries --")
code = "\n".join(l for l in inspect.getsource(P).splitlines() if not l.lstrip().startswith("#"))
check("it imports no network, database or mail library, and talks to no one but local Ollama through restaurant_vision", not re.search(r"^\s*(import|from)\s+(requests|httpx|urllib|socket|sqlite3|smtplib|ftplib|subprocess|supabase|psycopg)", code, re.M))
check("the only files it writes are the draft and its review sheet in the drafts folder, created new (never overwritten)", len(re.findall(r"\bopen\(", code)) == 1 and "_write_new" in code and 'os.path.exists(path)' in code and "os.replace(tmp, path)" in code)
check("it never deletes or edits an input file", not re.search(r"os\.(remove|rename|unlink)|shutil\.|\.unlink\(", code))
check("it uses no model for arithmetic: the cash and meat results are the two code tools'", "restaurant_tools.reconcile_cash(" in code and "restaurant_tools.check_meat_variance(" in code)
check("the default drafts folder is the one render_review_sheet reads", P.DRAFTS_DIR == R.DRAFTS_DIR == str(DAVID_DATA / "jobs_drafts" / "restaurant"))

print("\n-- the command lines --")
import subprocess
for script in ("restaurant_pipeline.py", "restaurant_vision.py", "restaurant_shadow.py"):
    out = subprocess.run([sys.executable, script, "--help"], capture_output=True, text=True, encoding="utf-8", cwd=str(DAVID_HOME), timeout=120)
    check(f"{script} --help works and documents the usage", out.returncode == 0 and "usage:" in out.stdout.lower())
bad = subprocess.run([sys.executable, "restaurant_pipeline.py", os.path.join(TMP, "no_such_folder")], capture_output=True, text=True, encoding="utf-8", cwd=str(DAVID_HOME), timeout=120)
check("a bad folder on the command line exits with 2 and says why, without a traceback", bad.returncode == 2 and "ERROR:" in bad.stderr and "Traceback" not in bad.stderr)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
