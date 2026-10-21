from david_config import DAVID_HOME
import os, sys, json, tempfile
sys.path.insert(0, str(DAVID_HOME)); sys.path.insert(0, str(DAVID_HOME / "scripts"))
import restaurant_fresh_score as S

tmp = tempfile.mkdtemp(); S.DAYS_DIR = tmp
n = 0
def ok(c, m):
    global n
    n += 1
    assert c, "FAIL: " + m
    print("ok", n, m)

def mk(day, statuses, answers, lines_kg, sheet):
    d = os.path.join(tmp, day); os.makedirs(d)
    lines = [{"line": i + 1, "match_status": s, "readings_agree": True} for i, s in enumerate(statuses)]
    draft = {"extraction": {"summary_by_items": {"lines": lines}}, "computed": {"diff": 1.0, "expected": 10, "counted": 11,
             "meat_from_lines": {"with_suggested": lines_kg}, "meat_variance": {"chicken": {"variance_kg": -0.5}, "beef": {"variance_kg": 0.2}}}}
    json.dump(draft, open(os.path.join(d, "DRAFT-%s-001.json" % day), "w"))
    json.dump({"answers": answers}, open(os.path.join(d, "manager_line_answers.json"), "w"))
    S.sheet_sold = lambda _d, sheet=sheet: sheet

mk("2026-10-20", ["matched"] * 8 + ["suggested", "suggested"], [{"id": "2026-10-20/9", "answer": "ok"}, {"id": "2026-10-20/10", "answer": "corrected"}, {"id": "2026-10-20/10/alt", "answer": "corrected"}],
   {"chicken": 10.0, "beef": 5.0}, {"chicken": 10.1, "beef": 5.0})
r = S.score_day("2026-10-20")
ok(r["lines"] == 10 and r["right"] == 9 and r["pct"] == 90.0, "a corrected held line counts as wrong, a confirmed one as right")
ok(r["corrected"] == 1 and r["confirmed"] == 1, "the /alt row of a correction is not counted twice")
ok(r["meat"]["chicken"]["gap_pct"] == -1.0 and r["meat"]["beef"]["gap_kg"] == 0.0, "meat kilos from the lines are compared with the company sheet")
mk("2026-10-21", ["matched", "needs_human"], [], {"chicken": 10.0, "beef": 5.0}, {"chicken": 10.0, "beef": 5.0})
r2 = S.score_day("2026-10-21")
ok(r2["right"] == 1 and r2["needs_human"] == 1, "a line the reader could not place counts as wrong")
ok(S.score_day("2026-10-99") is None, "a day without a draft and answers is not scored")
ok(r["fresh"] is True and S.score_day("2026-10-08") is None, "days from Oct 9 onward are fresh")
# a gap of a few hundred grams is scale noise even when it is a large percentage (manager's tolerance of 2026-10-13)
r3 = S.score_day("2026-10-20")
import io, contextlib
mk("2026-10-22", ["matched"] * 6, [], {"chicken": 3.0, "beef": 3.3}, {"chicken": 3.0, "beef": 3.55})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    S.main(["--out", os.path.join(tmp, "o.md")])
ok("2026-10-22: beef kilos from the lines differ" not in buf.getvalue(), "a 0.25 kg (7%) gap is within the scale's tolerance and is not flagged")
mk("2026-10-23", ["matched"] * 6, [], {"chicken": 3.0, "beef": 3.0}, {"chicken": 3.0, "beef": 3.8})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    S.main(["--out", os.path.join(tmp, "o2.md")])
ok("2026-10-23: beef kilos from the lines differ" in buf.getvalue(), "a 0.8 kg (21%) gap is still flagged")
print(f"\n{n} checks passed")
