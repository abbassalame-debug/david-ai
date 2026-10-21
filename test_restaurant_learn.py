"""
test_restaurant_learn.py -- offline tests for restaurant_learn.py: the system learns receipt spellings ONLY from the manager's own answers, safely.

No model, no network, no real alias file: everything runs on a throw-away copy of the reference folder.   Run: py -3.11 test_restaurant_learn.py
"""

from david_config import DAVID_HOME
import sys, os, json, re, shutil, tempfile, inspect
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import openpyxl
import menu_tools as M
import restaurant_learn as LN

TMP = tempfile.mkdtemp(prefix="learn_test_")
REF = os.path.join(TMP, "ref")
os.makedirs(REF)
for f in os.listdir(M.REF_DIR):
    p = os.path.join(M.REF_DIR, f)
    if os.path.isfile(p):
        shutil.copy(p, REF)
if os.path.exists(os.path.join(REF, "learning_log.json")):
    os.remove(os.path.join(REF, "learning_log.json"))          # the tests start with an empty log; the real one is not touched
real_alias_bytes = open(os.path.join(M.REF_DIR, "menu_aliases.json"), "rb").read()
G = M.get_menu_glossary(ref_dir=REF)
match = lambda t, g=None: M.match_menu_item(raw_texts=[t], glossary=g or G)["results"][0]
N0 = len([a for a in json.load(open(os.path.join(REF, "menu_aliases.json"), encoding="utf-8"))["aliases"] if a["status"] == "confirmed"])

# spellings nobody has confirmed (checked below), each with the item the manager would name
GARBLE_COMBO, GARBLE_MIRANDA, GARBLE_PLATTER = "كومبو دانير دجاج", "ميزز الدا", "صحن ساندويش لحمة 200 غرام مكرر"
check("the test spellings really are unknown to the menu to begin with (not exact matches)", all(match(t)["status"] != "matched" for t in (GARBLE_COMBO, GARBLE_MIRANDA)),
      str([(t, match(t)["status"]) for t in (GARBLE_COMBO, GARBLE_MIRANDA)]))

print("\n-- what is worth learning --")
ans = [{"id": "d/1", "name": GARBLE_COMBO, "item": "C20"}, {"id": "d/2", "name": GARBLE_MIRANDA, "item": "C41"}]
out = LN.learn_from_answers(ans, G)
check("an unknown spelling the manager gave an item for becomes a spelling to learn, with the item's own name", [(c["ar"], c["item"], c["name_ar"]) for c in out["new"]] == [(GARBLE_COMBO, "C20", "كومبو ستيك دجاج"), (GARBLE_MIRANDA, "C41", "ميراندا")] and out["skipped"] == [])
check("an answer with no item, or no spelling, teaches nothing", LN.learn_from_answers([{"id": "x", "name": GARBLE_COMBO, "item": None}, {"id": "y", "name": "", "item": "C20"}, {"id": "z", "name": "   ", "item": "C20"}], G) == {"new": [], "skipped": []})
check("no answers at all: nothing is learned (the system never learns from its own readings or guesses)", LN.learn_from_answers([], G) == {"new": [], "skipped": []})
out = LN.learn_from_answers([{"name": "ساندويش دجاج كبير", "item": "C02"}, {"name": "سلايدر 6 دجاج+6 لحمه", "item": "C28"}], G)
check("a spelling that is ALREADY an exact match for that item is not learned again", out == {"new": [], "skipped": []})
out = LN.learn_from_answers([{"name": "ساندويش دجاج كبير", "item": "C03"}], G)
check("a spelling that is already an exact match for ANOTHER item is not learned, and the clash is reported for the manager", out["new"] == [] and len(out["skipped"]) == 1 and "already an exact match for C02" in out["skipped"][0]["why"])
out = LN.learn_from_answers([{"name": GARBLE_COMBO, "item": "C20"}, {"name": GARBLE_COMBO + " ", "item": "C24"}], G)
check("a spelling answered with two different items is learned for neither, and said so", out["new"] == [] and len(out["skipped"]) == 1 and "answered as C20 and as C24" in out["skipped"][0]["why"])
out = LN.learn_from_answers([{"name": GARBLE_COMBO, "item": "C20"}, {"name": GARBLE_COMBO, "item": "C20"}], G)
check("the same spelling answered twice with the same item is learned once", len(out["new"]) == 1)
out = LN.learn_from_answers([{"name": "بدون ثوم", "item": "C20"}, {"name": "بيبسي مجاني", "item": "C39"}, {"name": "زيادة حر", "item": "K01"}], G)
check("'without / extra / free' lines are never learned as items", out["new"] == [] and len(out["skipped"]) == 3 and all("not an item" in s["why"] for s in out["skipped"]))
out = LN.learn_from_answers([{"name": "دجا", "item": "X04"}, {"name": "ثوم", "item": "X05"}, {"name": "ab", "item": "C20"}], G)
check("a spelling of fewer than 4 letters is too short to be safe", out["new"] == [] and len(out["skipped"]) == 3 and all("too short" in s["why"] for s in out["skipped"]))
out = LN.learn_from_answers([{"name": "كومبو دانير دجاج", "item": "C99"}], G)
check("an item that is not on the menu is refused", out["new"] == [] and "not on the menu" in out["skipped"][0]["why"])

print("\n-- his filled review sheet --")
index = [{"id": "2026-09-30/1", "day": "2026-09-30", "line": 1, "name": GARBLE_COMBO, "qty": 4, "gid": "C20", "guess": "كومبو ستيك دجاج"},
         {"id": "2026-09-30/2", "day": "2026-09-30", "line": 2, "name": GARBLE_MIRANDA, "qty": 3, "gid": "C41", "guess": "ميراندا"},
         {"id": "2026-09-30/3", "day": "2026-09-30", "line": 3, "name": "ثوم لونج مكرر", "qty": 2, "gid": "", "guess": ""},
         {"id": "2026-09-30/4", "day": "2026-09-30", "line": 4, "name": "فقط دجاج", "qty": 1, "gid": "", "guess": ""},
         {"id": "2026-09-30/5", "day": "2026-09-30", "line": 5, "name": "راب ساندويش دجاج مكرر", "qty": 9, "gid": "C18", "guess": "ساندويش راب دجاج"},
         {"id": "2026-09-30/6", "day": "2026-09-30", "line": 6, "name": "كوميو ستك لحمه مكرر", "qty": 2, "gid": "C20", "guess": "كومبو ستيك دجاج"},
         {"id": "2026-09-30/7", "day": "2026-09-30", "line": 7, "name": "شي غير مفهوم مكرر", "qty": 1, "gid": "", "guess": ""},
         {"id": "2026-09-30/8", "day": "2026-09-30", "line": 8, "name": "ميزان الدا كبير", "qty": 1, "gid": "C41", "guess": "ميراندا"},
         {"id": "2026-09-30/9", "day": "2026-09-30", "line": 9, "name": "بيبسي دايت مكرر", "qty": 6, "gid": "X01", "guess": "بيبسي دايت"}]
given = ["ok", "OK, that is right", "ok", "not an item", "qty 7", "كومبو ستيك لحمة", "something I cannot say", "", "C99"]
# rows: 1 ok -> C20 | 2 ok -> C41 | 3 'ok' with no guess | 4 not an item | 5 qty 7 (the item is right, the quantity differs) | 6 exact menu name | 7 gibberish | 8 blank | 9 unknown id
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Lines to review"
ws.append(["#", "Group", "Day", "Line", "Receipt says", "Other reading says", "Qty", "Qty (other reading)", "My guess (menu item)", "Meat in my guess", "Why I'm asking", "YOUR ANSWER"])
for n, (r, a) in enumerate(zip(index, given), 1):
    ws.append([n, "1. x", r["day"], r["line"], r["name"], "", r["qty"], "", r["guess"], "", "", a])
xlsx = os.path.join(TMP, "lines_to_review.xlsx"); wb.save(xlsx)
json.dump(index, open(os.path.join(TMP, "lines_to_review_index.json"), "w", encoding="utf-8"), ensure_ascii=False)
answers, rep = LN.answers_from_workbook(xlsx, glossary=G)
got = {a["id"].split("/")[1]: (a["item"], a["how"]) for a in answers}
check("'ok' confirms my guess; 'qty 7' says the item is right; an exact menu name names the item; an item id works",
      got.get("1") == ("C20", "ok") and got.get("2") == ("C41", "ok") and got.get("5") == ("C18", "qty 7") and got.get("6") == ("C24", "named: كومبو ستيك لحمة"), str(got))
check("'ok' with no guess to confirm, gibberish, an unknown item id and 'not an item' settle nothing; each is listed", set(got) == {"1", "2", "5", "6"}
      and [u["id"].split("/")[1] for u in rep["unresolved"]] == ["3", "7", "9"] and [x["id"].split("/")[1] for x in rep["not_an_item"]] == ["4"] and rep["blank"] == 1 and rep["quantity_changes"] == 1, str(rep))
a_blank, _ = LN.answers_from_workbook(xlsx, blank_is_ok=True, glossary=G)
check("a blank cell teaches nothing unless he says the whole sheet was reviewed (--blank-is-ok)", "8" not in got and {x["id"].split("/")[1] for x in a_blank} == set(got) | {"8"})
check("what he says 'not an item' to becomes a SUGGESTED ruling (the first word), never applied", LN.suggested_rulings(rep) == {"فقط": ["فقط دجاج"]})

print("\n-- writing the aliases --")
out = LN.learn_from_answers(answers, G)
before = open(os.path.join(REF, "menu_aliases.json"), "rb").read()
dry = LN.learn_from_answers(answers, G)
check("looking (a dry run) writes nothing: the alias file is byte for byte the same", open(os.path.join(REF, "menu_aliases.json"), "rb").read() == before and not os.path.exists(os.path.join(REF, "learning_log.json")))
res = LN.apply_aliases(out["new"], "the review sheet test.xlsx", ref_dir=REF, today="2026-10-09")
G2 = M.get_menu_glossary(ref_dir=REF)
doc = json.load(open(os.path.join(REF, "menu_aliases.json"), encoding="utf-8"))
check("applying adds exactly the new spellings, as confirmed, with who, when, the source and the evidence", len(res["written"]) == len(out["new"]) == 4 and res["refused"] == [] and len(doc["aliases"]) == N0 + 4
      and all(a["status"] == "confirmed" and a["confirmed_by"] == "Demo User (manager)" and a["confirmed_on"] == "2026-10-09" and "test.xlsx" in a["evidence"] and "restaurant_learn.py" in a["evidence"] for a in doc["aliases"][-4:]))
check("each learned spelling is now an EXACT match for the item he settled (100, matched)", all(match(c["ar"], G2)["status"] == "matched" and match(c["ar"], G2)["score"] == 100 and match(c["ar"], G2)["match"]["item_id"] == c["item"] for c in out["new"]))
check("the learned spelling was NOT an exact match before (so the next scan really is better)", all(match(c["ar"], G)["status"] != "matched" for c in out["new"]))
again = LN.learn_from_answers(answers, G2)
res2 = LN.apply_aliases(out["new"], "again", ref_dir=REF, today="2026-10-09")
doc2 = json.load(open(os.path.join(REF, "menu_aliases.json"), encoding="utf-8"))
check("learning the same answers twice is harmless: nothing new to learn, and forcing it writes no duplicate", again["new"] == [] and res2["written"] == [] and len(doc2["aliases"]) == N0 + 4)
log = json.load(open(os.path.join(REF, "learning_log.json"), encoding="utf-8"))
check("every run is noted in the learning log (date, source, how many added, total aliases)", [(e["on"], e["added"], e["aliases_total"]) for e in log] == [("2026-10-09", 4, N0 + 4), ("2026-10-09", 0, N0 + 4)] and log[0]["from"] == "the review sheet test.xlsx")
check("the sheet, the other rulings and the unlisted items are untouched by learning", {k: doc2[k] for k in doc2 if k != "aliases"} == {k: json.loads(before.decode("utf-8"))[k] for k in doc2 if k != "aliases"})
# a spelling that would not resolve to its item once added is refused and NOT written
bad = [{"ar": "ساندويش دجاج كبير", "item": "C03", "name_ar": "x"}]
res3 = LN.apply_aliases(bad, "clash", ref_dir=REF, today="2026-10-09")
check("a spelling that does not resolve exactly to its item once added is refused and not written", res3["written"] == [] and len(res3["refused"]) == 1 and len(json.load(open(os.path.join(REF, "menu_aliases.json"), encoding="utf-8"))["aliases"]) == N0 + 4)
check("the REAL alias file was never touched by these tests", open(os.path.join(M.REF_DIR, "menu_aliases.json"), "rb").read() == real_alias_bytes)

print("\n-- from an answers file, and the command line --")
af = os.path.join(TMP, "answers.json")
json.dump({"by": "Demo User (manager)", "answers": [{"id": "a", "name": "كومبو ستيك لحمه مكرر", "item": "C24"}, {"id": "b", "name": "x", "item": None}]}, open(af, "w", encoding="utf-8"), ensure_ascii=False)
fa, _ = LN.answers_from_file(af)
check("an answers file gives only the answers that name an item", [a["id"] for a in fa] == ["a"])
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    LN.main(["--answers", af])
check("the command line shows what it would learn and says nothing was written (no --apply)", "1 spellings to learn" in buf.getvalue() and "nothing written" in buf.getvalue() and "+ كومبو ستيك لحمه مكرر" in buf.getvalue())
try:
    with contextlib.redirect_stderr(io.StringIO()):
        LN.main([])
    exit_code = None
except SystemExit as e:
    exit_code = e.code
check("it needs exactly one of --workbook / --answers", exit_code == 2)

print("\n-- boundaries --")
code = "\n".join(l for l in inspect.getsource(LN).splitlines() if not l.lstrip().startswith("#"))
check("it talks to no network and no database: no http, socket, supabase or sql", not re.search(r"(?m)^\s*(import|from)\s+(requests|httpx|socket|urllib|sqlite3|psycopg|supabase)", code) )
check("it writes only the alias file and its own learning log in the reference folder (atomic, os.replace), never the cheat sheet", code.count("_write_json(") == 4 and "CHEAT" not in code and "os.replace(tmp, path)" in code)
check("only the manager's answers are read: nothing in it calls a model", "OllamaVision" not in code and "restaurant_vision" not in code)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
