# ollama_config.py -- the ONE place that says which context size each Ollama model
# runs at, and where the local Ollama server lives.
#
# Why this exists (measured 2026-10-03): Ollama starts a brand-new runner -- a full
# model reload, 8.6 s for qwen3.5:9b on this machine -- whenever a request's num_ctx
# differs from the num_ctx the model is currently loaded with. Before this file, the
# HUD ran qwen3.5:9b at 8192 for the router / tool-decision / ordinary chat and at
# 16384 for the reasoning agents, so every cfo/exec/venture/legal turn paid TWO
# reloads (router back to 8k, answer back to 16k) and the next ordinary turn paid a
# third. At 16384 qwen3.5:9b costs 5.7 GB of VRAM vs 5.6 GB at 8192 (ollama ps), so
# the larger size is simply used everywhere.
#
# Rule: never write a literal num_ctx for a chat model. Call num_ctx_for(model).
# (test_qwen35_wiring.py enforces this across the live modules.)
#
# Always use 127.0.0.1, never "localhost": on this machine "localhost" resolves
# IPv6-first and costs a flat ~2 s per HTTP call for urllib/requests/httpx.

from david_config import OLLAMA_HOST   # env OLLAMA_HOST, default http://127.0.0.1:11434 (audit P3-2: no module hard-codes it)
OLLAMA_CHAT_URL = OLLAMA_HOST + "/api/chat"
OLLAMA_GENERATE_URL = OLLAMA_HOST + "/api/generate"
OLLAMA_EMBED_URL = OLLAMA_HOST + "/api/embed"

MODEL_NUM_CTX = {
    # router, tool-decision, vision, reasoning agents (depth files + think:true),
    # most domain agents, war rooms, digests, bridges, attachment notes.
    "qwen3.5:9b": 16384,
    # chat_attachments deep-analysis judge: its calls used 10240 and 12288
    # alternately, i.e. an 11 GB reload between them. One value.
    "deepseek-r1:14b": 12288,
}
DEFAULT_NUM_CTX = 8192   # david-ft, qwen-ft, qwen2.5-coder:7b, llama3.1:8b (failover)


def num_ctx_for(model: str) -> int:
    """Canonical num_ctx for `model`. Every call site must use this."""
    return MODEL_NUM_CTX.get(model, DEFAULT_NUM_CTX)
