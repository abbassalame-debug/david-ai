# Models and third-party licenses

This repository ships **no model weights** and no third-party binaries. It talks to models through [Ollama](https://ollama.com) (local) and through
[ComfyUI](https://github.com/comfyanonymous/ComfyUI) (local, optional). Each model has its own license; **check the upstream license before redistributing a
weight file or a product that bundles one.** The table lists what the code is written and tested against; licenses are as declared upstream when this file was
written and may change.

| Role | Model (as named in the code) | Upstream license (verify) | Where used |
|---|---|---|---|
| Router, tool decision, vision, reasoning, general chat | `qwen3.5:9b` | Qwen family licence (check the model card) | `david_router.py`, `tools.py`, `restaurant_vision.py`, … |
| Embeddings (default) | `qwen3-embedding:0.6b` | Apache-2.0 | `embedding.py` |
| Embeddings (alternative) | `BAAI/bge-m3` | MIT | `embedding.py` with `DAVID_EMBEDDER=bge-m3` |
| Attachment analysis judge | `deepseek-r1:14b` | MIT | `chat_attachments.py` |
| Failover and fine-tuned experiments | `llama3.1:8b`, `qwen2.5-coder:7b`, local LoRA adapters | Llama 3.1 Community License; Qwen licence | `ollama_config.py`, `david_router.py` |
| Image generation / layer split (optional) | Qwen-Image, Qwen-Image-Layered, Z-Image Turbo | see each model card | `design_generate.py`, `design_split.py` |
| Video / music (optional, experimental) | LTX-2, ACE-Step, Wan 2.2 | see each model card | `design_video.py`, `design_music.py` |
| Speech-to-text (optional) | `faster-whisper` models | MIT (code), model cards for the weights | `chat_attachments.py` |

## Python dependencies with copyleft or special terms

| Package | License | How this repository treats it |
|---|---|---|
| PyMuPDF | AGPL-3.0 or commercial | **Optional extra** (`requirements-media.txt`, commented). Imported lazily through `extractors.optional_pymupdf()`; every PDF reader falls back to `pypdf`. Do not ship a build that includes it unless you accept AGPL-3.0 or buy a commercial licence. |
| mutagen | GPL-2.0-or-later | **Optional extra**, imported lazily; without it audio/video files are cataloged by name and size. |
| psycopg | LGPL-3.0 | Used as an unmodified installed library by the restaurant vertical only (`requirements-restaurant.txt`). |
| Coqui TTS / XTTS-v2 | MPL-2.0 (code), non-commercial model licence | **Not part of this repository.** The text-to-speech daemon was left out so the core stays free of non-commercial terms. |

Everything else in `requirements*.txt` is Apache-2.0, MIT, BSD, ISC, PSF or HPND licensed. Generate an exact list with `pip-licenses` for the environment you ship.
Adobe applications, Microsoft Office and Ableton Live are not included and need their own licences; `ZXPSignCmd` and signing certificates are not included either.
