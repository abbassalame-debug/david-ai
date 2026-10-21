"""Unit tests for chat_attachments.py — the deterministic parts only (no Ollama, no database).

These guard the behaviour that makes attachment answers trustworthy: nothing is truncated silently, quotes
are verified against the document, dates are computed in code, invented figures are flagged, and the
memory module can no longer turn questions into "facts".
Run: python test_chat_attachments.py
"""
import datetime
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import chat_attachments as ca

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"\033[92m  PASS\033[0m  {name}" if ok else f"\033[91m  FAIL\033[0m  {name}  {detail}")


TEXT = ("[Page 8]\nCost figures above are modeled placeholders; we recommend validating them against\nactual food and "
        "beverage cost data before finalizing the print run.\n\n[Page 39]\nThree inputs are needed before the print "
        "run: the median ticket per branch and daypart from the POS, a Pantone read of the existing bags, and the "
        "printer's answer on where variable data can run in the pass sequence.")

print("\n── Request detectors (typo-tolerant) ──")
for q, rep, ana in [("can you analiz this file for me", True, True), ("please summerize the attached notes", True, False),
                    ("give me a quick summury", True, False), ("write me a report summury", True, True),
                    ("What three inputs are needed before the print run?", False, False), ("who is the CEO", False, False)]:
    check(f"{q!r} -> report={rep}, analysis={ana}", (ca.wants_report(q), ca.wants_analysis(q)) == (rep, ana))

print("\n── Findings: parsing and quote verification ──")
passages = ca.make_passages(TEXT)
npas = [ca._norm(p["text"]) for p in passages]
ntext = ca._norm(TEXT)
rows = ca._parse_findings('''Here are my findings:
- Cost model is a placeholder || p.8 || "Cost figures above are modeled placeholders; we recommend validating them"
2. Three inputs are still open || p.39 || "Three inputs are needed before the print run"
- Invented risk || p.37 || "The plan overstates its uniqueness versus competitors in Downtown"
- malformed line
NONE''')
check("parser keeps the 3 well-formed findings, ignores chatter/NONE/malformed", len(rows) == 3)
check("real quote is found and its page is recovered from the document", ca._locate_quote(rows[0][1], passages, npas, ntext) == (True, 8))
check("fabricated quote is rejected", ca._locate_quote(rows[2][1], passages, npas, ntext) == (False, None))
check("quote with '...' must have every fragment in the document",
      ca._locate_quote("Three inputs are needed ... a Pantone read", passages, npas, ntext)[0]
      and not ca._locate_quote("Three inputs are needed ... and a lawyer signs off", passages, npas, ntext)[0])

print("\n── Claim checks that need no model ──")
check("every figure in a claim must be in its quote (kills invented arithmetic)",
      not ca._claim_numbers_in_quote("Net return is $821", "Net return +$935.50 (≈ 9.2× the promo cost)")
      and ca._claim_numbers_in_quote("Net return is $935.50", "Net return +$935.50 (≈ 9.2× the promo cost)"))
check("causal wording triggers the strict check, plain restatement does not",
      bool(ca._SPIN.search("errors due to the lack of a prefix")) and not ca._SPIN.search("The 5.7% figure is a ceiling")
      and not ca._SPIN.search("Staff may abandon the program in the rush"))
check("absence claims are detected (and ordinary ones are not)",
      all(ca._ABSENCE_CLAIM.search(c) for c in ["The odds are not explicitly stated.", "The plan lacks a budget.",
                                                 "The program does not capture near-qualifiers."])
      and not any(ca._ABSENCE_CLAIM.search(c) for c in ["Cards expire 30 days from issue.", "Prizes carry no cash value."]))
check("yes/no parser", ca._yes_no("YES") == "YES" and ca._yes_no("No.") == "NO" and ca._yes_no("") is None
      and ca._yes_no("<think>x</think> NO") == "NO" and ca._yes_no("perhaps") is None)

print("\n── Dates are computed in code, never by the model ──")
today_d, today = datetime.date(2026, 9, 28), "Monday 28 September 2026"
check("'end of September 2026' -> 2 days from today",
      "2026-09-30, 2 days from today" in ca._days_annotation("Draw date End of September 2026", today_d))
check("past ISO date -> N days ago", "2026-09-17, 11 days ago" in ca._days_annotation("dated 2026-09-17", today_d))
check("end of February uses the true last day", "2026-02-28" in ca._days_annotation("end of February 2026", today_d))
check("document age line flags stale 'as of this report' timing",
      "11 days before today" in ca._doc_age_line("Plan\n2026-09-17 · x", today_d, today))
check("no date -> no annotation", ca._days_annotation("nothing", today_d) == "" and ca._doc_age_line("nothing", today_d, today) == "")

print("\n── Figures not in the document are flagged ──")
SRC = "Cost $114.50 per 100 cards, 5.7% of revenue. Redemption 60-80%, modeled at 70%. Net return +$935.50 (≈ 9.2×)."
check("grounded figures pass", ca._ungrounded_figures(SRC, "Cost is $114.50 (5.7%), return $935.50, about 9.2×.") == [])
check("invented figure is flagged", ca._ungrounded_figures(SRC, "The net return would be $467.75 at 50%.") == ["$467.75", "50%"])
check("figures computed in code are not blamed on the model",
      ca._ungrounded_figures(SRC, "Soon *[calculated: 2026-09-30, 2 days from today]*") == [])

print("\n── Long documents: chunking and search ──")
long_text = "\n\n".join(f"[Page {i}]\n" + ("filler sentence about routine matters. " * 40) for i in range(1, 31))
long_text += "\n\n[Page 31]\nThe warehouse door code was changed to 4417 after the audit."
chunks = ca.split_chunks(long_text)
check("chunks cover the whole text with nothing lost", "".join(chunks) == long_text or len("".join(chunks)) >= len(long_text) - len(chunks))
check("chunk labels carry page ranges", ca._label(chunks[0], 1, len(chunks)).startswith("pages 1"))
top = ca.top_passages(ca.make_passages(long_text), "what is the warehouse door code?")
check("search finds the one relevant passage among 30 pages of filler", any("4417" in p["text"] for p in top))
check("search result is cited by page", any(p["page"] == 31 for p in top))
check("missing figures in notes are recovered verbatim",
      any("$935.50" in x for x in ca._missing_figures("Net return\n+$935.50 (≈ 9.2× the promo cost)\nother", "cost only")))

print("\n── Readers ──")
d = tempfile.mkdtemp()
with zipfile.ZipFile(os.path.join(d, "flyer.idml"), "w") as z:
    z.writestr("Stories/Story_u1.xml", "<Story><ParagraphStyleRange><Content>Grand Opening</Content></ParagraphStyleRange>"
                                       "<ParagraphStyleRange><Content>Fri &amp; Sat only</Content></ParagraphStyleRange></Story>")
check("IDML text is extracted", "Grand Opening" in ca._idml(os.path.join(d, "flyer.idml"))
      and "Fri & Sat only" in ca._idml(os.path.join(d, "flyer.idml")))
open(os.path.join(d, "t.csv"), "w").write("name,qty\nbeef,10\n")
text, caveats = ca._body_for(os.path.join(d, "t.csv"), ".csv")
check("CSV is read in full with no caveat", "beef,10" in text and caveats == [])
open(os.path.join(d, "t.bin"), "wb").write(bytes(range(256)) * 4)
text, caveats = ca._body_for(os.path.join(d, "t.bin"), ".bin")
check("unknown binary is reported honestly, not guessed", "no reader" in text and caveats)
check("upload ids are validated (no path tricks)", ca.resolve_upload("../../etc") is None and ca.resolve_upload("z" * 32) is None)

print("\n── Summary ──")
passed = sum(results)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
