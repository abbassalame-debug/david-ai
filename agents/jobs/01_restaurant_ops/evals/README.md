# evals/

Golden cases: a draft package plus what a correct review must say. They are how we know an agent edit made things
better and not worse, and what the shadow-mode comparison is measured against.

Each case is one JSON file: `{"name", "synthetic": true|false, "package": {...}, "must_flag": [...], "must_not_say": [...]}`.

| File | What |
|---|---|
| `synthetic_001_clean_day.json` | the clean day from the CFO's first example (arithmetic checked by `test_agent_spec.py`) |
| `synthetic_002_blocked_day.json` | the blocked day: total mismatch, rate mismatch, unlabeled expense |

Real cases come from the Sep 1-13 backfilled days once the manager has checked them. A real case is never committed with
anything personal in it. Mark every invented case `"synthetic": true`.
