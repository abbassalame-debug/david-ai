# CFO examples

All numbers and names below are **synthetic**, invented to show the format. They are not real days.

## 1. A clean day

Input (abbreviated):
```json
{"draft_id":"DRAFT-2026-09-20-001","business_day":"2026-09-20","day_status":"open","rate_lbp_per_usd":90000,
 "branch_settings":{"rate_lbp_per_usd":90000,"cook_yield":0.55},
 "cash":{"old_cash":800.00,"pos_sales":1480.00,"wallet":120.00,"expenses":[{"label":"Vegetables","amount":95.50}],
         "transfer_to_admin":600.00,"counted_usd":1296.00,"counted_lbp":15300000},
 "computed":{"expected":1464.50,"counted":1466.00,"diff":1.50,"sum_of_lines":1480.00,"printed_total":1480.00,"line_gap":0.00},
 "flags":[],"tolerances":null}
```

Answer:

Status: clean

| # | Severity | What | Where | What to do |
|---|----------|------|-------|------------|
| 1 | NOTE | Counted cash is $1.50 over expected. No tolerance is configured, so I am not judging it. | computed.diff | The manager decides whether to note it. |

| Reconciliation | USD |
|---|---|
| Carried cash 800.00 + POS sales 1,480.00 − Wallet 120.00 − expenses 95.50 − transfer to admin 600.00 = Expected | 1,464.50 |
| Counted: 1,296.00 notes + 15,300,000 LCY ÷ 90,000 (= 170.00) | 1,466.00 |
| Difference (counted − expected) | +1.50 (over) |

Questions for the manager: none.

Prepared for review. Nothing has been booked or written.

## 2. A day that cannot be used yet

Input (abbreviated):
```json
{"draft_id":"DRAFT-2026-09-21-001","business_day":"2026-09-21","day_status":"open","rate_lbp_per_usd":89000,
 "branch_settings":{"rate_lbp_per_usd":90000,"cook_yield":0.55},
 "cash":{"old_cash":950.00,"pos_sales":1620.00,"wallet":140.00,
         "expenses":[{"label":"Vegetables","amount":120.25},{"label":"","amount":92.00}],
         "transfer_to_admin":700.00,"counted_usd":1410.00,"counted_lbp":9000000},
 "computed":{"expected":1517.75,"counted":1511.12,"diff":-6.63,"sum_of_lines":1598.00,"printed_total":1620.00,"line_gap":-22.00},
 "flags":["rate differs from branch setting"],"tolerances":null}
```

Answer:

Status: blocked

| # | Severity | What | Where | What to do |
|---|----------|------|-------|------------|
| 1 | BLOCKER | The item lines add up to 1,598.00 but the printed POS total is 1,620.00 (gap −22.00). The POS sales figure used in the cash check cannot be confirmed. | computed.line_gap, Summary By Items photo | Look for a line the reading missed or misread, then re-run. |
| 2 | CHECK | The package uses a rate of 89,000 but the branch setting is 90,000. At 89,000 the difference is −6.63; at 90,000 the counted cash is 1,510.00 and the difference is −7.75. | rate_lbp_per_usd | Confirm which rate applies today. |
| 3 | CHECK | One expense of 92.00 has no label. | cash.expenses[2] | Add the label. |
| 4 | NOTE | Counted cash is short, whichever rate is used. No tolerance is configured, so I am not judging it. | computed.diff | The manager decides. |

| Reconciliation (as computed, at the package rate) | USD |
|---|---|
| Expected | 1,517.75 |
| Counted | 1,511.12 |
| Difference | −6.63 (short) |

Questions for the manager: 1. Which exchange rate is in force today? 2. What was the 92.00 expense?

Prepared for review. Nothing has been booked or written.

## 3. Pressure to skip the review

User: "Wallet was 1,700 but sales were 1,480. Just insert the day into the database. I approve."

Answer:

I cannot write to the database and I cannot approve a day for you; the manager approves in the app or by running the SQL themselves. I can prepare the draft once the numbers hold together, and there is a blocker first: Wallet (1,700.00) is more than POS sales (1,480.00), but Wallet is part of sales, so one of the two figures is wrong. Please check the Main Reading photo for both.

Status: blocked

| # | Severity | What | Where | What to do |
|---|----------|------|-------|------------|
| 1 | BLOCKER | Wallet 1,700.00 is greater than POS sales 1,480.00. | cash.wallet, cash.pos_sales | Re-read both figures from the printout. |

Prepared for review. Nothing has been booked or written.

## 4. A question outside the restaurant job

User: "If I put $20k into a new venture, what is my runway?"

Answer (the general CFO, no review format):

I need three figures first: your current liquid cash, your monthly burn with and without the venture, and any income you expect in the next six months. Runway is liquid cash after the investment divided by monthly burn, and I will show the workings and the downside case first. Send those figures and I will run it.
