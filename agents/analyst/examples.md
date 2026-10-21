# Analyst examples

All names, numbers and item ids below are **synthetic**, invented to show the format. They are not a real day or the real menu.

## 1. An everyday question stays everyday

User: "What's 15 percent of 340?"

Answer:
1. 10% of 340 is 34.
2. 5% of 340 is 17.
3. 34 + 17 = 51.

15 percent of 340 is 51.

## 2. Reviewing an extraction

Input (abbreviated, a small synthetic day):
```json
{"draft_id":"DRAFT-2026-09-21-001","business_day":"2026-09-21","sources":["summary_by_items.jpg"],
 "extraction":{"summary_by_items":{"printed_total":409.00,"lines":[
   {"line":1,"name_ar":"ساندويش دجاج كبير","qty":42,"amount":210.00,"readings_agree":true,
    "match":{"item_id":"M-014","name_ar":"ساندويش دجاج كبير","score":100}},
   {"line":2,"name_ar":"وجبة لحمة","qty":18,"amount":126.00,"readings_agree":true,
    "match":{"item_id":"M-031","name_ar":"وجبة لحمة","score":100}},
   {"line":3,"name_ar":"سلايدر دجاج","qty":30,"amount":45.00,"readings_agree":true,
    "match":{"item_id":"M-022","name_ar":"سلايدر دجاج صغير","score":88}},
   {"line":4,"name_ar":"عرض 4$","qty":7,"qty_alt":1,"amount":28.00,"readings_agree":false,
    "match":{"item_id":null,"candidates":[{"item_id":"M-050","name_ar":"عرض 4$","score":81},
                                           {"item_id":"M-051","name_ar":"عرض 4$ لحمة","score":79}]}}]}},
 "computed":{"sum_of_lines":409.00,"printed_total":409.00,"line_gap":0.00},
 "thresholds":{"matched":100,"suggested":85,"margin":5}}
```

Answer:

# Review sheet: 2026-09-21 (DRAFT-2026-09-21-001)
Sources: summary_by_items.jpg   Reading: 4 lines, 2 matched, 1 suggested, 1 needs human

## Summary
- Line 4 needs a human: the two readings of the quantity disagree (7 and 1) and no menu item matches well.
- Line 3 is a suggested match, not a certain one.
- The lines add up to the printed total.

## Item lines
| # | Name as printed | Matched menu item | Qty | Amount | Status | Note |
|---|-----------------|-------------------|-----|--------|--------|------|
| 1 | ساندويش دجاج كبير | M-014 ساندويش دجاج كبير | 42 | 210.00 | matched | |
| 2 | وجبة لحمة | M-031 وجبة لحمة | 18 | 126.00 | matched | |
| 3 | سلايدر دجاج | M-022 سلايدر دجاج صغير (score 88) | 30 | 45.00 | suggested | Confirm the match |
| 4 | عرض 4$ | none | 7 or 1 | 28.00 | needs human | Readings disagree |

## Needs a human
- line 4: the quantity was read as 7 and as 1, and the name matches no item with a clear lead; candidates: M-050 عرض 4$ (81), M-051 عرض 4$ لحمة (79). For reference, 28.00 is 7 × 4.00; I am not choosing a reading.

## Totals check (computed by code)
Sum of lines: 409.00  |  Printed total: 409.00  |  Gap: 0.00

## Questions for the manager
1. Is line 3 the small chicken slider (M-022)?
2. Which quantity is right on line 4, and which offer is it?

Prepared for review. Nothing has been booked or written.

## 3. A package that cannot be reviewed

Input: `{"draft_id":"DRAFT-2026-09-22-001","business_day":"2026-09-22"}`

Answer:

This package has no `extraction` block, so there is nothing to review. I have not assumed what it should contain. Please re-run the reading step for 2026-09-22.
