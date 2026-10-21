# Security

David is built to run **on one machine, for one operator**. Its safety model is simple on purpose; this file says what it does and what it does not.

## What is enforced in code

- **Login.** One middleware (`hud_auth.py`) guards every HTTP route, static mount and WebSocket of the HUD. The password is stored only as an
  Argon2id hash (`scripts/set_hud_password.py`, at least 12 characters) in `<DAVID_DATA>/auth/hud_auth.json`, which is git-ignored. A login sets the
  `david_session` cookie (HttpOnly, SameSite=Strict); the server keeps only a SHA-256 of each session id. Sessions end after 12 hours idle or
  7 days in all, and at logout; changing the password ends every session. Five failed logins lock the login for 15 minutes. Programs that call the HUD
  use bearer tokens limited to named routes (`scripts/hud_tokens.py`; only a hash is stored). The HUD's calls to itself use a token that is random per
  server process and held in memory only. Login events are audited without the password, cookie or token.
  `DAVID_AUTH=off` turns the login off, and is honoured only on a loopback address; with no password and no `DAVID_AUTH=off` the server refuses to start.

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

- Multi-user isolation: there is one password and no user accounts or roles. Do not expose the server beyond loopback; the login is a second line
  of defence for the machine it runs on, not a reason to put it on a network. There is no TLS: the cookie is not marked Secure because the server speaks plain HTTP on loopback.
- A malicious local process or a malicious document that persuades a local model to misbehave: tool allowlists and the approval gates limit the damage but do not make
  untrusted documents safe. Text found inside files is treated as data, not as instructions (see `agents/_shared/safety.md`), which lowers the risk without removing it.
- Prompt-injection through cloud-bound text that the DLP patterns do not match.

## Secrets

Nothing in this repository is a secret. Keys, tokens and connection strings are read from the environment (`.env.example` lists them) and from files that are
git-ignored (`.env`, `google_credentials*.json`, `google_token*.json`, certificates, `auth/`). Every push and pull request is scanned with gitleaks
(`.github/workflows/secrets.yml`). `python scripts/install_hooks.py` installs a pre-commit hook that runs gitleaks on staged changes and fails closed when
gitleaks is missing; `scripts/private_terms_check.py` checks a fork against your own git-ignored list of private names and paths
(`config/private_terms.txt`) before you publish it.

## Reporting a vulnerability

Please report suspected vulnerabilities through GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Do not open a public issue for a vulnerability. Reports are read by the maintainer; there is no bounty.
