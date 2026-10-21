# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-10-21

### Added
- **HUD login** (`hud_auth.py`). One middleware guards every HTTP route, static mount and WebSocket. Argon2id password hash,
  `david_session` cookie (HttpOnly, SameSite=Strict) with 12 h idle / 7 day absolute expiry, a 15-minute lockout after five failed logins,
  scoped bearer tokens for programs (`scripts/hud_tokens.py`), a per-process in-memory token for the HUD's calls to itself, and audited
  login events that never contain a secret. A login page and a sign-out button in the frontend. `DAVID_AUTH=off` turns the login off,
  only on a loopback address. `/healthz` is an open liveness check.
- **Continuous integration.** The full suite on Windows (required), the platform-independent core on Ubuntu (non-blocking), a frontend
  type-check and build, and a gitleaks scan of every push and pull request. `DAVID_CI=1` makes checks that need Ollama, a GPU, COM or an
  Adobe app report `SKIP` instead of failing; `run_all_tests.py` counts skips apart from passes, fails a suite that tested nothing, writes
  a full log and a per-suite job summary.
- **Retrieval settings.** `[retrieval] min_fts_score` and `max_distance` in `config/vaults.toml` replace the hard-coded thresholds.
- **Private-data checks for forks.** `scripts/install_hooks.py` (a pre-commit hook that runs gitleaks and fails closed) and
  `scripts/private_terms_check.py` (checks the tree, the tracked files, the staged changes or the whole history against your own
  git-ignored list of private terms).
- `SECURITY.md` names GitHub private vulnerability reporting as the way to report a vulnerability. `CHANGELOG.md`, `CITATION.cff`.

### Changed
- `retrieve()` keeps one row per passage: a copy of a higher-ranked passage (same text in another file, or with other header lines) gives
  its slot to the next distinct passage, in the vector path, after BM25 fusion and in the cross-vault fallback.
- The HUD refuses to start with neither a password nor `DAVID_AUTH=off`, instead of running without protection.
- Requirements: `argon2-cffi==25.1.0`.

### Fixed
- A note backup could be written outside `note_backups/` when the vault path and the note path were spelt differently (an 8.3 short
  name against the long name). Found by the first clean CI run.
- Tests that assumed a long `%TEMP%` path, an installed Premiere preset, InDesign, a particular font, a Windows path or Windows-only process
  flags now pass on a clean runner.

## [1.0.0] — 2026-10-21

First public release (untagged): the core engine, the deterministic creative-app bridge, the restaurant-operations example vertical,
the offline test suite and the documentation.

[1.1.0]: https://github.com/abbassalame-debug/david-ai/releases/tag/v1.1.0
[1.0.0]: https://github.com/abbassalame-debug/david-ai/commit/09f8866
