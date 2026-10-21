"""
test_restaurant_corrections.py -- tests for restaurant_corrections.py: the manager's corrections to what David read, validated and applied to a COPY of the draft.
Pure functions, offline.   Run: py -3.11 test_restaurant_corrections.py
"""
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, glob, copy, math
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import restaurant_corrections as RC

REAL = json.load(open(str(DAVID_HOME / "examples" / "demo_bistro" / "draft_package.demo.json"), encoding="utf-8"))   # the demo draft package (synthetic)


def bad(ov):
    try:
        RC.validate(ov)
        return None
    except RC.Invalid as e:
        return str(e)


check("an empty or missing correction is empty", RC.validate(None) == {} and RC.validate({}) == {})
check("a correct cash figure passes through as a float", RC.validate({"cash": {"pos_sales": 900, "wallet": 0}}) == {"cash": {"pos_sales": 900.0, "wallet": 0.0}})
check("a field that is not a cash field is refused, naming the allowed ones", "pos_sales" in (bad({"cash": {"x": 1}}) or "") and bad({"cash": {"x": 1}}) is not None)
check("negative, text, boolean, NaN and infinite numbers are refused", all(bad({"cash": {"pos_sales": v}}) for v in (-1, "5", True, float("nan"), float("inf"), 10 ** 9)))
check("an expense needs a name and a number; more than 40 rows is refused", bad({"expenses": [{"label": " ", "amount": 1}]}) and bad({"expenses": [{"label": "a", "amount": None}]}) and bad({"expenses": [{"label": "a", "amount": 1}] * 41}) and bad({"expenses": "x"}))
check("an expense amount is rounded to cents and 'safe' is a boolean", RC.validate({"expenses": [{"label": " veg ", "amount": 1.234, "safe": 1}]})["expenses"] == [{"label": "veg", "amount": 1.23, "safe": True}])
check("meat corrections: only chicken and beef, only the known figures", bad({"meat": {"fish": {}}}) and bad({"meat": {"beef": {"colour": 1}}}) and bad({"meat": {"beef": {"closing": {"x": 1}}}}) and RC.validate({"meat": {"beef": {"received_kg": 2, "closing": {"tallow_kg": 8}}}}) == {"meat": {"beef": {"received_kg": 2.0, "closing": {"tallow_kg": 8.0}}}})
check("a line correction is keyed by the line number and names a menu id and/or a quantity", RC.validate({"lines": {"3": {"item": "C20", "qty": 4}}}) == {"lines": {"3": {"item": "C20", "qty": 4.0}}} and bad({"lines": {"x": {}}}) and bad({"lines": {"3": {"item": 5}}}) and bad({"lines": {"3": {"item": "C" * 30}}}))

d2, ch = RC.apply_cash(REAL, {"cash": {"pos_sales": REAL["cash"]["pos_sales"] + 1}})
check("a cash correction gives a COPY: the original is untouched, the copy has the new figure, and Expected and the difference moved by exactly that amount",
      REAL["cash"]["pos_sales"] != d2["cash"]["pos_sales"] and d2["cash"]["pos_sales"] == REAL["cash"]["pos_sales"] + 1 and d2["computed"]["expected"] == round(REAL["computed"]["expected"] + 1, 2)
      and d2["computed"]["diff"] == round(REAL["computed"]["diff"] - 1, 2) and d2["cfo_results"]["cash"]["expected_usd"] == d2["computed"]["expected"])
check("the change is a sentence with read and corrected values, and the draft carries a NOTE that it was corrected", len(ch) == 1 and "Sales" in ch[0] and "(read)" in ch[0] and "(you)" in ch[0] and d2["flags"][-1].startswith("NOTE: the manager corrected"))
d3, ch3 = RC.apply_cash(REAL, {"cash": {"pos_sales": REAL["cash"]["pos_sales"]}})
check("a 'correction' to the same value changes nothing and returns the draft itself", d3 is REAL and ch3 == [])
d4, ch4 = RC.apply_cash(REAL, {"expenses": [{"label": "veg.", "amount": 10, "safe": False}]})
check("replacing the expense list recomputes Expected from the new total", d4["cash"]["expenses"] == [{"label": "veg.", "amount": 10.0}] and d4["computed"]["expected"] == round(REAL["computed"]["expected"] + sum(e["amount"] for e in REAL["cash"]["expenses"]) - 10, 2))
w, figs, mch = RC.split_meat({"meat": {"beef": {"received_kg": 2, "sold_kg": 3, "waste_kg": 0.5, "closing": {"tallow_kg": 8}}}}, {"beef": {"spit_kg": 7.7, "received_kg": 12, "closing": {"trim_kg": 1.0, "main_kg": 30.0, "tallow_kg": 6.0}}})
check("meat corrections: received and closing go to the weights, sold and waste to the figures; closing merges with the workbook's (only tallow changed)",
      w["beef"]["received_kg"] == 2 and w["beef"]["spit_kg"] == 7.7 and w["beef"]["closing"] == {"trim_kg": 1.0, "main_kg": 30.0, "tallow_kg": 8.0} and figs == {"beef": {"sold_kg": 3.0, "waste_kg": 0.5}}
      and any("beef received: 12 (read) -> 2 (you)" in c for c in mch) and any("closing weights" in c for c in mch))
q = {"expenses": [{"label": "veg.", "amount": 62.55}, {"label": "gaz", "amount": 600}], "meat": {"chicken": {"closing": {"trim_kg": 1}}, "beef": {"closing": None}}, "checks": ["CHECK: a", "CHECK: b"], "held_lines": [{"line": 4, "name": "x"}]}
req = RC.required_items(q)
check("required items: 5 cash, 2 expenses, 4 + closing for chicken and 4 for beef (no closing figure for beef), 2 warnings, 1 held line", len(req) == 5 + 2 + 5 + 4 + 2 + 1 and ("meat.chicken.closing", "Chicken closing weights") in req and not any(k == "meat.beef.closing" for k, _ in req))
allc = {k: True for k, _ in req}
check("everything confirmed leaves nothing open", RC.unsettled(q, allc, {}) == [])
check("a confirmation must be exactly true: 'yes', 1 and false do not count", len(RC.unsettled(q, {k: "yes" for k, _ in req}, {})) == len(req) and len(RC.unsettled(q, {k: 1 for k, _ in req}, {})) == len(req) and len(RC.unsettled(q, {k: False for k, _ in req}, {})) == len(req))
check("a corrected cash figure settles that figure only", RC.unsettled(q, {k: True for k, _ in req if k != "cash.wallet"}, {"cash": {"wallet": 5}}) == [] and RC.unsettled(q, {k: True for k, _ in req if k != "cash.wallet"}, {"cash": {"pos_sales": 5}}) == ["Wallet"])
check("an expense row settles itself only if its name or amount was changed", RC.unsettled(q, {k: True for k, _ in req if k != "expense.0"}, {"expenses": [{"label": "veg.", "amount": 62.55, "safe": False}, {"label": "gaz", "amount": 600, "safe": True}]}) == ["Expense: veg. 62.55"]
      and RC.unsettled(q, {k: True for k, _ in req if k != "expense.0"}, {"expenses": [{"label": "veg.", "amount": 60, "safe": False}, {"label": "gaz", "amount": 600, "safe": False}]}) == [])
check("a corrected meat figure settles that figure; the closing weights settle with a closing correction", RC.unsettled(q, {k: True for k, _ in req if k != "meat.chicken.sold_kg"}, {"meat": {"chicken": {"sold_kg": 5}}}) == []
      and RC.unsettled(q, {k: True for k, _ in req if k != "meat.chicken.closing"}, {"meat": {"chicken": {"closing": {"trim_kg": 2}}}}) == [])
check("a corrected line settles that line", RC.unsettled(q, {k: True for k, _ in req if k != "line.4"}, {"lines": {"4": {"item": "C20"}}}) == [])
check("nothing here touches a file or the network", all(x not in open(RC.__file__, encoding="utf-8").read() for x in ("open(", "import requests", "import socket", "sqlite3")))

print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
