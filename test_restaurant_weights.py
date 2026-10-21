"""
test_restaurant_weights.py -- tests for restaurant_weights.py (the weights workbook reader) on a synthetic workbook, and against the app's own rows
for the sample days (spit sums and closing weigh-ins must equal what the manager entered).   Run: py -3.11 test_restaurant_weights.py
"""
from david_config import DAVID_HOME
import sys, os, json, tempfile, shutil, datetime, re, inspect
from decimal import Decimal
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import openpyxl
import restaurant_weights as W

TMP = tempfile.mkdtemp(prefix="weights_test_")


def make_book(path, tab="06-10-2026", chicken=(31.56, 10.12, 3.62), beef=(6.58,), chicken_total=None, beef_total=None, closing=True):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet(tab)
    ws.cell(3, 3).value = "قبل الشك"
    for c, v in ((4, 4.0), (5, 130.98), (15, 1.12), (16, 6.0), (17, 27.68)):
        ws.cell(3, c).value = v
    r = 6
    for w in chicken:
        ws.cell(r, 2).value = "الوزن السيخ"
        ws.cell(r, 4).value = w
        r += 1
    r = 6
    for w in beef:
        ws.cell(r, 14).value = "الوزن السيخ"
        ws.cell(r, 17).value = w
        r += 1
    ws.cell(20, 2).value = "مجموع السياخ دجاج"
    ws.cell(22, 2).value = chicken_total if chicken_total is not None else round(sum(chicken), 3)
    ws.cell(20, 14).value = "مجموع السياخ لحم"
    ws.cell(22, 14).value = beef_total if beef_total is not None else round(sum(beef), 3)
    if closing:
        ws.cell(30, 1).value = "end-of-shift closing summary"
        ws.cell(31, 3).value = "قبل الشك"
        for c, v in ((4, 6.36), (5, 148.66), (15, 2.86), (16, 6.0), (17, 30.0)):
            ws.cell(31, c).value = v
    wb.save(path)


p = os.path.join(TMP, "فوارق الشك مع الاوزان 06-10-2026.xlsx")
make_book(p)
r = W.read_day(p, "2026-10-06")
check("every spit is read, chicken (value two columns after the label) and beef (three columns after)", r["spits"]["chicken"] == [Decimal("31.56"), Decimal("10.12"), Decimal("3.62")] and r["spits"]["beef"] == [Decimal("6.58")])
check("the sheet's own total is read and agrees with the parts, so there is no flag", r["spit_sum_cell"] == {"chicken": Decimal("45.3"), "beef": Decimal("6.58")} and r["flags"] == [], str(r["flags"]))
check("the opening and closing weigh-ins: chicken trim / main, beef trim / tallow / main", r["opening"]["chicken"] == {"trim_kg": Decimal("4"), "main_kg": Decimal("130.98")} and r["closing"]["chicken"] == {"trim_kg": Decimal("6.36"), "main_kg": Decimal("148.66")}
      and r["closing"]["beef"] == {"trim_kg": Decimal("2.86"), "tallow_kg": Decimal("6"), "main_kg": Decimal("30")})
p2 = os.path.join(TMP, "فوارق الشك مع الاوزان 07-10-2026.xlsx")
make_book(p2, tab="06-10-2026", chicken_total=46.0)
r2 = W.read_day(p2, "2026-10-06")
check("a sheet total that is not the sum of the spits is flagged with both numbers", len(r2["flags"]) == 1 and "45.3" in r2["flags"][0] and "46" in r2["flags"][0], str(r2["flags"]))
p3 = os.path.join(TMP, "فوارق الشك مع الاوزان 08-10-2026.xlsx")
make_book(p3, closing=False)
r3 = W.read_day(p3, "2026-10-06")
check("no closing summary: closing is None and it says so (never guessed)", r3["closing"] is None and any("no closing weigh-in" in f for f in r3["flags"]))
try:
    W.read_day(p, "2026-10-07")
    ok = False
except ValueError:
    ok = True
check("a day with no tab is an error, not an empty answer", ok)
wb = openpyxl.Workbook()
wb.active.title = "04-10-2026 stale"
check("tabs are found by their date, so a tab with extra words is still found", W._tab(wb, "2026-10-04") is not None and W._tab(wb, "2026-10-05") is None)

r = W.read_day(p, "2026-10-06")
fc = W.fridge_check(r, {"chicken": Decimal("68"), "beef": Decimal("10")}, {"chicken": 2.2, "beef": 0.1})
check("closing weigh-in against opening + received - spits - waste: chicken 134.98 + 68 - 45.3 - 2.2 = 155.48 against 155.02 is -0.46 kg, beef 28.8 + 10 - 6.58 - 0.1 = 32.12 against 32.86 is +0.74 kg; neither is large",
      fc["chicken"]["expected_kg"] == Decimal("155.48") and fc["chicken"]["gap_kg"] == Decimal("-0.46") and not fc["chicken"]["large"]
      and fc["beef"]["expected_kg"] == Decimal("32.12") and fc["beef"]["gap_kg"] == Decimal("0.74") and not fc["beef"]["large"], str(fc))
check("a delivery typed 80 instead of 68 puts the chicken gap at -12.46 kg and marks it large", W.fridge_check(r, {"chicken": Decimal("80")}, {"chicken": 2.2})["chicken"]["gap_kg"] == Decimal("-12.46") and W.fridge_check(r, {"chicken": Decimal("80")}, {"chicken": 2.2})["chicken"]["large"])
check("with no closing weigh-in the fridge cannot be checked", W.fridge_check(r3, {"chicken": Decimal("68")}) == {})

root = os.path.join(TMP, "abo")
os.makedirs(os.path.join(root, "10-2026", "06-10-2026"))
shutil.copy(p, os.path.join(root, "10-2026", "06-10-2026"))
os.makedirs(os.path.join(root, "10-2026", "07-10-2026"))
shutil.copy(p2, os.path.join(root, "10-2026", "07-10-2026"))
path, why = W.find_workbook("2026-10-06", root)
check("of two workbooks that both have the day, the one with the LATEST DATE IN ITS NAME is used", os.path.basename(path).endswith("07-10-2026.xlsx") and "latest-dated" in why, str(path))
check("no workbook for a day gives a reason, not a guess", W.find_workbook("2026-11-02", root)[0] is None and W.meat_weights_for_approval("2026-11-02", None, root)[0] is None)
os.remove(os.path.join(root, "10-2026", "07-10-2026", os.path.basename(p2)))
mw, notes, flags = W.meat_weights_for_approval("2026-10-06", {"chicken": 68.0, "beef": 10.0}, {"chicken": 2.2, "beef": 0.1}, root)
check("meat_weights: spits are the SUM of the workbook's, closing is trim / main / tallow, received is passed through from the ledger",
      mw["chicken"]["spit_kg"] == 45.3 and mw["chicken"]["closing"] == {"trim_kg": 6.36, "main_kg": 148.66, "tallow_kg": 0.0} and mw["beef"]["spit_kg"] == 6.58
      and mw["beef"]["closing"] == {"trim_kg": 2.86, "main_kg": 30.0, "tallow_kg": 6.0} and mw["chicken"]["received_kg"] == 68.0 and mw["beef"]["received_kg"] == 10.0, str(mw))
check("the notes say received comes from the ledger and must be confirmed, and give each fridge gap; nothing is flagged for an ordinary day", sum("comes from the company ledger" in n for n in notes) == 2 and any("chicken fridge" in n and "-0.46" in n for n in notes) and flags == [], str(notes) + str(flags))
mw3, notes3, flags3 = W.meat_weights_for_approval("2026-10-06", {"chicken": 80.0}, {"chicken": 2.2}, root)
check("a fridge gap over 5 kg is a flag that says to check the delivery or the weigh-in, but received is still passed through", any("unusually large" in f and "-12.46" in f for f in flags3) and mw3["chicken"]["received_kg"] == 80.0)
mw2, notes2, flags2 = W.meat_weights_for_approval("2026-10-06", None, None, root)
check("with no ledger figure, received is simply absent and there is no fridge line", "received_kg" not in mw2["chicken"] and not any("fridge" in x for x in notes2 + flags2))

# the demo restaurant root (examples/demo_bistro) holds one workbook, for 2026-09-22
path, why = W.find_workbook("2026-09-22")
rd = W.read_day(path, "2026-09-22") if path else None
check("the demo workbook for 2026-09-22 is found in the demo restaurant root and read: chicken spit 35.68 kg, beef spit 7.7 kg",
      bool(path) and rd and sum(rd["spits"]["chicken"], Decimal(0)) == Decimal("35.68") and sum(rd["spits"]["beef"], Decimal(0)) == Decimal("7.7"), str(why))
check("a day with no workbook in the demo root gives a reason, not a guess", W.find_workbook("2026-09-23")[0] is None)

code = "\n".join(l for l in inspect.getsource(W).splitlines() if not l.lstrip().startswith("#"))
check("it only reads: no write mode, no network", not re.search(r"open\([^)]*['\"]w|\.save\(|requests|socket|urllib", code))
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
