# Security

David is built to run **on one machine, for one operator**. Its safety model is simple on purpose; this file says what it does and what it does not.

## What is enforced in code

- **Loopback only.** The HUD server is meant to run on `127.0.0.1`. A `TrustedHost` middleware rejects any `Host` header that is not a loopback name
  (DNS-rebinding guard, HTTP and WebSocket), and an `Origin` guard rejects state-changing requests from any other origin.
  Requests without an `Origin` header (scripts, curl) pass; they already have local access.
- **Data boundaries** (`config/vaults.toml`). Each agent can search only the vaults listed for it; an agent that is not listed gets no vault at all
  (fail closed). The firewalled vault is excluded from cloud tiers and from the Obsidian MCP bridge unless explicitly opted in.
- **DLP pre-flight.** Before any cloud call the request (and any attached context) is matched against the patterns in `config/vaults.toml` (credentials,
  confidentiality labels, card and IBAN numbers with a Luhn check, bilingual keywords). A match keeps the request local. **This is a regex first filter, not a
  confidentiality boundary:** it has no semantic understanding, so sensitive text phrased without one of those markers is not caught.
- **Cloud is opt-in.** No key, no cloud call. With a key, a call needs: a non-firewalled agent, no DLP match, and room in the monthly budget (`budget.json`).
  Every escalation and every refusal is written to `audit_log.jsonl`.
- **Per-agent tool allowlists.** `agents/<id>/role.md` lists the tools an agent may use; `tool_executor.execute_tool` refuses any other tool and audits the refusal.
  The technical domain is read-only.
- **No model-written scripts in creative apps.** Photoshop, Illustrator, InDesign, After Effects and Premiere are driven only through fixed, tested operations
  (`design_*.py`). A job runs only after a named designer approves the exact plan (SHA-256 fingerprint), works on copies, verifies the original is unchanged
  and can be undone. The older route that ran model-written ExtendScript was retired (`/api/adobe` returns 410).
- **The database is written only by a human action.** `restaurant_upload.py` runs the same `apply.sql` the manager can read, in one transaction, only when the
  button is pressed, and rolls everything back unless every check row is found. The connection string comes from `DAVID_APP_DB_URL` and is read in exactly one place;
  error messages are scrubbed of it. David never holds credentials otherwise.
- **Exact arithmetic.** Money and meat weights are computed in `decimal` code, never by a model.
- **Chat transcripts are kept for a limited time.** The HUD appends each exchange (the first 300 characters of your message and 400 of the reply) to
  `<DAVID_VAULTS>/System/CRM/interactions/<date>.md`. `DAVID_TRANSCRIPTS` decides how long: unset keeps 90 days, a number keeps that many days, `keep` keeps
  everything, `off` writes nothing. Older day files are deleted when the HUD starts and once a day (`transcripts.py`); the first deletion copies the files
  to `<DAVID_DATA>/transcript_backups/` first. The folder is never indexed and never readable by the tools.

## What is not covered

- Authentication and multi-user isolation: there is none. Do not expose the server beyond loopback.
- A malicious local process or a malicious document that persuades a local model to misbehave: tool allowlists and the approval gates limit the damage but do not make
  untrusted documents safe. Text found inside files is treated as data, not as instructions (see `agents/_shared/safety.md`), which lowers the risk without removing it.
- Prompt-injection through cloud-bound text that the DLP patterns do not match.

## Secrets

Nothing in this repository is a secret. Keys, tokens and connection strings are read from the environment (`.env.example` lists them) and from files that are
git-ignored (`.env`, `google_credentials*.json`, `google_token*.json`, certificates). Run a secret scanner (for example `gitleaks`) before publishing a fork.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainers (add a contact address here before publishing) rather than in a public issue.
