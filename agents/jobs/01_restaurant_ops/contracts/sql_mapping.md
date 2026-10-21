# SQL mapping: draft package field to the app's tables (stage 4)

Written 2026-10-08 from the app session's schema report (`reference/app_schema_facts_2026-10-08.md`, from migrations 0001-0031 and the app code) and checked against
the hand-written backfills 0029 (cash) and 0030 (meat). Code: `restaurant_sql.py`; tests: `test_restaurant_sql.py` (85 checks, the generated SQL is executed on a
stand-in copy of the tables). David never connects to Supabase: it writes `apply.sql` and `undo.sql`, the manager reads and runs them.

## Gates (no SQL without all of these)
1. An **approval record** (`<draft>.approval.json`, schema `sql_approval/v1`) made by `py restaurant_sql.py approve DRAFT.json --by "Demo User" --ack-checks`. It holds the draft
   id, a fingerprint of the draft (any later edit voids it), who and when, every acknowledged `CHECK:` flag, and each `BLOCKER:` flag cleared with a written reason. A model
   cannot make it: the generator is a command-line tool, not an agent tool.
2. The draft passes `validate_draft_package`, says `day_status: ended`, and its own arithmetic holds (Expected = Old + sales - wallet - expenses - transfer; difference =
   New - Expected; raw used = starting + received - ending).
3. The day is not `open` in the app's backup (the lock for ended days is screen-only in the app, so SQL could write there; an open day may be in someone's hands).
4. **Nothing the app already holds is changed** unless the approval lists the table in `allow_updates`. Rows that differ and have no safe one-row update (expenses, per-spit
   rows that do not add up, two cash sheets) stop everything: status `needs_human`, no SQL (all or nothing).

## Which draft field goes where
| Draft field | Table.column | Notes |
|---|---|---|
| `business_day`, `rate_lbp_per_usd` | `days` (on_date, status `ended`, rate_usd_to_lbp) | only if the backup has no row for that date; the day id is the backup's if it exists (look-up by date), else the app's `stableId('day:<branch>:<date>')`. A day row is never deleted by undo (it cascades). |
| `cash.pos_sales` | `cash_sheets.sales_usd` | Main Reading net sales |
| `cash.wallet` | `cash_sheets.count_wallet_usd` | never an expense |
| `cash.old_cash` | `cash_sheets.safe_old_usd` | the previous day's New, hand-copied in the app |
| `cash.actual_cash_count` | `cash_sheets.safe_counted_usd` AND `safe_new_usd` | the app's "New" (all cash counted) |
| `cash.counted_usd` | `cash_sheets.value_usd` | the Helper's Value |
| `computed.expected` / `computed.diff` | `expected_usd` / `diff_usd` | recomputed from the numbers with the app's JavaScript rounding; a draft that disagrees is refused |
| `cash.transfer_to_admin` | `cash_sheets.transfer_to_admin_usd` and one `cash_transfers` row (amount_usd) | |
| (constants) | `count_cash_usd` 0, `count_cash_lbp` 0, `rate_used`, `next_float_usd` 211.11, floats false, `closed` true | same as backfill 0029 |
| `cash.expenses[]` | `cash_expenses` (label, amount_usd, `paid_from` 'register') | one row per line; ids are `stableId('david:expense:...')` |
| `cfo_results.meat.<m>.pos_sold_kg` | `consumption` kilo line (qty; chicken_g or beef_g 1000) | kilo menu item ids `daab6d80...` (chicken), `5e4275df...` (beef); row id = the app's `consumptionId` |
| `cfo_results.meat.<m>.waste_kg` | `waste_log.grams` (kg x 1000) | |
| spits, received, closing weights | `spit_loads.weight_kg`, `meat_receipts.received_kg`, `meat_closing_weights` | **only from the weights workbook, carried in the approval (`meat_weights`)**. The draft reads the company ledger, which the manager's rule says the workbook overrides; on Sep 27, Oct 1 and Oct 3 the ledger's "used" was 10 kg under the workbook's, and Sep 23 still says 12 kg beef received. Without the workbook figures these rows are listed as NOT generated. `restaurant_weights.py` reads them (`approve --weights-from-workbook`): spits = the sum of the day's spit weights, closing = the closing weigh-in, received = the ledger's figure passed through with a note to confirm it (the workbook has no deliveries). Closing weights use the app's `stableId('meat_closing:<day>:<meat>')` so the app's next save does not add a second row. A tallow-only delivery is the beef `received_kg` (Sep 23 = 2). |
| item lines | (nothing) | the app stores no item lines; they only feed the meat cross-check |
| holds, Wallet rows, payroll, bills | (nothing) | not in the draft; payroll has no deduction column and bills live outside the till |

## What the files look like
- `apply.sql`: header (draft, who approved, "nothing has been run"), `begin;`, inserts with fixed ids and `on conflict (id) do nothing` (the `days` insert uses
  `(branch_id, on_date)`), updates `set <col> = <new> where id = ... and <col> = <old>` (a row that changed since the backup is left alone), `commit;`, then ONE result
  table: every line must show `rows_found = rows_expected`.
- `undo.sql`: guarded updates back to the old values (taken from the backup), inserted rows deleted by id, children before parents, no delete from `days`. The app has no
  soft delete; deleted rows stay in phones' local cache until their next refresh, so corrections are updates, not delete-and-insert.
- `plan.md`: what the file does in words, the notes (chain gaps, workbook against ledger, what was not generated), how to run, and the reminder to export a fresh backup.

## The cash chain
Each day's Old is the previous day's New, typed by hand. Before a new sheet is inserted the previous day's New is compared with this Old: a gap up to 0.05 is kept as written
and noted (the Sep 27 / 28 case), a bigger gap is `needs_human`. If an update changes a day's New, the next day's Old, Expected and difference are updated in the same file.

## Measured on the author's own deployment (2026-10-08; that data is not part of this repository)
Sep 19 - Oct 5, latest draft per day, each day's rows removed from a copy of the backup and rebuilt from the draft plus the workbook figures: the generated SQL runs, every
verification line is found, the rows equal the ones the manager entered on 13 of 17 days, and undo restores the starting rows on all 17. The other 4 differ by a cent on
sales (Sep 19, 29, Oct 2, 4: a digit the vision reading got wrong; Sep 19 also has three per-spit rows where the rebuild makes one); nothing is hidden: the plan shows old to new.


## End-of-day stamp on a day David creates (2026-10-10)

A day the app does not have yet is inserted as `status = 'ended'` AND with the stamp the app itself writes when a day is ended (`endBusinessDay` in src/lib/day.ts): `ended_at = now()`, `submitted_at = now()`, `ended_by` and `closed_by` = the approver's `people.id` (the operator: 00000000-0000-4000-8000-0000000000a1; a name that is not in `MANAGER_PEOPLE` leaves them empty and the plan says so). The final check table has a line for it. Days entered before this change (Oct 6 to 9) were fixed by `for_review/fix_ended_days_2026-10-06_to_09/apply.sql` (ended_at / submitted_at = opened_at, the rule migration 0010 used). An existing day is never re-stamped by the generator.
