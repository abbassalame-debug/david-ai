# contracts/

The shapes of the files that move between pipeline stages, and between David and the app.

| File | What | State |
|---|---|---|
| `draft_package.schema.json` | the draft package, version 0 | **Draft v0.** Finalise with the app's session before code depends on it. |
| `sql_mapping.md` | which draft field goes to which table and column, the gates, and the `apply.sql` / `undo.sql` shape | **Written 2026-10-08** from the app session's schema report; implemented by `restaurant_sql.py`. |

## Optional fields added to v0 while the pipeline was built

Both are optional, so a package written without them is still valid.

| Field | Why |
|---|---|
| `extraction.summary_by_items.amounts_printed` (boolean, default true) | The real POS "Summary By Items" roll prints one number per item, the quantity, and no price. When false, lines carry `amount: null`, there is no sum of lines to check, and the review sheet says so instead of raising a blocker for every line. |
| `cash.actual_cash_count` (number or null) | The app's "New": ONE grand total of all cash counted (registers + safe). The cash check uses it when present; `counted_usd` / `counted_lbp` are then the register notes only. |

The pipeline also writes extra keys the schema does not list and does not forbid: `cfo_results` (the cash and meat results the sheet shows), `files_not_read`, `computed.meat_from_lines`, and per-line `match_status` / `match_from`. Package-level `flags` are strings that start with `BLOCKER:`, `CHECK:` or `NOTE:`; the review sheet shows them in its summary.

Rules: a contract changes only with a version bump (`draft_package/v1`); both sides must agree; nothing here may contain
a credential or real customer data.
