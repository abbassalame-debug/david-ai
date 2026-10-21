# David AI

[![tests](https://github.com/abbassalame-debug/david-ai/actions/workflows/tests.yml/badge.svg)](https://github.com/abbassalame-debug/david-ai/actions/workflows/tests.yml)
[![frontend](https://github.com/abbassalame-debug/david-ai/actions/workflows/frontend.yml/badge.svg)](https://github.com/abbassalame-debug/david-ai/actions/workflows/frontend.yml)
[![secrets](https://github.com/abbassalame-debug/david-ai/actions/workflows/secrets.yml/badge.svg)](https://github.com/abbassalame-debug/david-ai/actions/workflows/secrets.yml)
[![clean install](https://github.com/abbassalame-debug/david-ai/actions/workflows/clean-install.yml/badge.svg)](https://github.com/abbassalame-debug/david-ai/actions/workflows/clean-install.yml)

**A local-first multi-agent orchestration, retrieval (RAG) and cross-application automation framework.**
Apache-2.0 · Python 3.11 · Ollama · LanceDB · FastAPI · React

David routes every request through a three-layer intake (a regex table, a schema-constrained local classifier, a single-shot tool decision) to one of 17
specialist agents. Each agent has a role file (`agents/<id>/role.md`), a retrieval namespace that is enforced in code, and an explicit tool allowlist that the
executor checks again before anything runs. Memory is hybrid: a LanceDB vector table (Qwen3-Embedding via Ollama, 1024-d) fused with BM25 by reciprocal-rank
fusion, a SQLite FTS5 table of extracted facts with a privacy filter, and episodic session notes written back into the index. Inference is Ollama-first. Gemini
and Claude are wired as audited Tier 2/3 escalations behind an agent firewall, a DLP pre-flight, a monthly budget and a local failover chain, but nothing
requires a cloud key. The same framework drives Photoshop, Illustrator, InDesign, After Effects and Premiere through a **deterministic design portal**: every
action is a fixed, tested operation (a model never writes a script), runs only after a named human approves the exact plan fingerprint, works on copies, and can
be undone.

**What it solves.** Zero cloud lock-in for agent routing and retrieval; scarce-VRAM management on one consumer GPU (one `num_ctx` per model, a GPU hand-over to
heavy jobs and back); hallucinated tool calls and unsafe automation (per-agent allowlists, audited refusals, exact-decimal arithmetic instead of model arithmetic,
approval gates, run-on-copy with undo); and retrieval that returns noise (an embedder-calibrated distance ceiling, a BM25 floor, fail-closed vault namespaces).

> **Status, honestly.** David was built for one operator on one Windows machine. It is a working system with a large offline test suite, not a polished product:
> single-user (one password login), Windows-bound for the Adobe/Office bridges, and every agent persona is a generic example you are expected to rewrite. See
> [Limits](#limits).

## What is in the box

| Tier | Contents | Needs |
|---|---|---|
| **A. Core engine** | `david_router.py` (routing, modes, DLP, budget), `agent_spec.py` + `agents/`, `tools.py` / `tool_executor.py`, `rag.py` + `embedding.py` + `memory.py` + `knowledge.py`, `turbo_ingest.py` / `file_watcher.py`, `chat_attachments.py`, `ui/server.py` + `ui/frontend/` | Python 3.11, Ollama |
| **B. Deterministic creative-app bridge** | `design_runner.py` (plan → approve → run on a copy → undo), the per-app operations (`design_photoshop.py`, `design_illustrator.py`, `design_indesign.py`, `design_aftereffects.py`, `design_premiere.py`), `adobe_template_tools.py`, local generation on ComfyUI (`design_generate.py`, `design_split.py`, `design_video.py`, `design_music.py`) | Windows + the Adobe apps; ComfyUI for generation |
| **C. Example vertical: restaurant operations** | dual-pass vision reading of POS printouts, exact-decimal cash reconciliation and meat variance, menu matching, a reviewable `apply.sql` and a guarded, transactional upload (`restaurant_*.py`, `menu_tools.py`, `ui/restaurant_api.py`). All names, menus and amounts in the examples are synthetic ("Demo Bistro"). | PostgreSQL only for the upload step |

`ARCHITECTURE.md` has the module inventory with a maturity rating for each part and the data-flow diagram.

## Quickstart (no GPU, no cloud)

```bash
git clone https://github.com/abbassalame-debug/david-ai
cd david-ai
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# local models (Ollama must be running: https://ollama.com)
ollama pull qwen3-embedding:0.6b
ollama pull qwen3.5:9b

# index the five demo notes into a scratch data folder, then ask about them
export DAVID_DATA=/tmp/david-demo DAVID_VAULTS=examples/demo_vault        # PowerShell: $env:DAVID_DATA = "..."
python turbo_ingest.py --vaults-only
python -c "import rag; print(rag.retrieve('analyst', 'Which supplier delivers beef trimmings?', top_k=2))"

# the HUD needs a login password first (stored only as an Argon2id hash under DAVID_DATA/auth/)
python scripts/set_hud_password.py
# (or, for a quick local try without a login: DAVID_AUTH=off, which the server accepts only on 127.0.0.1)
# the API starts even without a frontend build; `cd ui/frontend && npm install && npm run build` adds the pages
python -m uvicorn --app-dir ui server:app --host 127.0.0.1 --port 8000
```

No Ollama yet? `DAVID_EMBEDDER=hash-demo` swaps the embedder for a model-free word hash (its own table), enough to try the ingest,
retrieval and the HUD; it matches shared words, not meaning. The `clean install` CI job runs exactly this Quickstart that way on a
fresh Ubuntu and Windows runner.

Then open `http://127.0.0.1:8000/login`. Without a password and without `DAVID_AUTH=off` the server refuses to start rather than run
unprotected. Programs that call the HUD (scripts, scheduled jobs) use scoped bearer tokens: `python scripts/hud_tokens.py create <name> "GET /api/..."`.

Configuration is environment variables (see `.env.example`) and one TOML file for the data-boundary policy (`config/vaults.example.toml`: the vault list, which
agent may read which vault, the DLP patterns, the knowledge-privacy markers). `david_config.py` is the only module that knows where things live; no other file
hard-codes a drive letter or a user name.

## Optional extras

| File | Adds |
|---|---|
| `requirements-windows.txt` | `pywin32`: COM automation of Office and the Adobe apps |
| `requirements-media.txt` | Pillow, PSD files, faster-whisper, audio analysis, OpenCV, AbletonOSC. PyMuPDF (AGPL-3.0) and mutagen (GPL-2.0+) are listed **commented out** on purpose |
| `requirements-restaurant.txt` | `psycopg` for the guarded upload step |
| `requirements-cloud.txt` | Gemini / Claude tiers, web search, Google Calendar and Gmail tools |
| `requirements-ml.txt` | the bge-m3 embedder (sentence-transformers) |
| `requirements-dev.txt` | everything the test suite imports |

## Tests

```bash
pip install -r requirements-dev.txt
python scripts/run_all_tests.py
```

**Last run (v1.1.0, 21 Oct 2026; Windows 11, Python 3.11, Ollama running, `DAVID_CI=1`): 61 suites, 2,785 checks, 1 skipped, 0 failed (4 min 20 s).** The suites are plain Python scripts that use scripted
fakes for models, COM servers and databases, so they need neither a GPU nor a cloud account. They were run on Windows with the optional extras above installed;
a run on another platform or without the extras has not been tried. The Adobe suites test the operations against recording stand-ins, not the real applications.

## Limits

- **Single user.** One password, one operator; there are no user accounts or roles. The server is meant for `127.0.0.1` (see `SECURITY.md`). Do not expose it to a network.
- **Windows-bound in places.** The Adobe, Office and Premiere bridges use COM / CEP. The core engine is portable in principle, but the repository is only tested on Windows.
- **The DLP filter is regex.** It catches labelled secrets, card numbers and listed keywords; it does not understand meaning.
- **Fine-tuned adapters are not included.** Every default points at `qwen3.5:9b`. The router can run a second, fine-tuned model for the `general` domain (`DAVID_GENERAL_MODEL`) and a different failover model (`DAVID_FAILOVER_MODEL`); the training code and adapters are not part of this repository.
- **Calibration is local.** The retrieval thresholds (`max_distance` 0.50, BM25 floor 15) were measured on one corpus. They are settings (`[retrieval]` in
  `config/vaults.toml`); re-measure them for yours. BM25 scores grow with the corpus, so a large corpus needs a higher floor.
- **Personas are examples.** The 17 role files are generic; the quality of the agents depends on how well you describe your own work in them.
- **Video and music generation are experimental** and use the whole GPU while they run.

## Licensing

The code is Apache-2.0 (`LICENSE`; put your own copyright line in `NOTICE`). Model weights, ComfyUI, the Adobe/Office/Ableton applications and the optional
copyleft packages are separate works under their own terms: see `MODELS.md`. No model weights, credentials or personal data are in this repository.
