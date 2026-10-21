# david_router.py — David v0.3 (5-mode + Tier 2/3 routing + DLP pre-flight)
# Run: python david_router.py
# Requires: Ollama running with qwen3.5:9b (and qwen3-embedding:0.6b for retrieval)

from david_config import DAVID_AUDIO_PYTHON, DAVID_DATA, DAVID_HOME, DAVID_VAULTS, load_policy, private_vault_folders
import sys
import json
import os
import re
import time
import uuid
import datetime
import subprocess
import urllib.request
import portalocker

# Canonical num_ctx per model -- see ollama_config.py for why a single value per
# model matters (an 8.6 s reload on every mismatch).
from ollama_config import num_ctx_for, MODEL_NUM_CTX, DEFAULT_NUM_CTX, OLLAMA_HOST

sys.stdout.reconfigure(encoding="utf-8")

SPEAK = "--speak" in sys.argv

OLLAMA         = OLLAMA_HOST + "/api/chat"
LOG            = str(DAVID_DATA / "audit_log.jsonl")
INCUBATION_DIR = str(DAVID_VAULTS / "System" / "Incubation")
STUDIO_LOCK    = str(DAVID_DATA / "studio_mode.lock")
FOCUS_START    = 7   # 07:00 — work begins
FOCUS_END      = 22  # 22:00 — rest begins
MODE_FILE      = str(DAVID_DATA / "mode.state")
DEPTH_DIR      = str(DAVID_HOME / "depth")
SPEAK_QUEUE    = str(DAVID_DATA / "speak_queue")

# Agents that always use deepseek-r1:14b (loaded with depth files)
R1_AGENTS = {"cfo", "executive_advisor", "venture_architect", "legal_counsel"}
# linguist removed: corpus Q&A is retrieval + citation work, not multi-step
# reasoning — it never benefited from deepseek-r1 and paid the ~9GB model-swap
# cost on every religious question.
# analyst deliberately excluded: it's Command Center's default agent, so keeping it
# here meant every ordinary chat message (not just specialized queries) paid the full
# ~9GB/3x-slower deepseek-r1:14b cost — it doesn't fit in VRAM alongside the always-
# resident llama3.1:8b + qwen2.5-coder:7b, forcing Ollama to reactively swap models on
# nearly every request. Analyst now uses the fast resident model for everyday chat.

# ── Operating modes ──────────────────────────────────────────────────────────
MODES = {
    "A": "Daily Driver — qwen3.5 resident (+ the general model if you set a different one)",
    "B": "Deep Reasoning — all requests → qwen3.5:9b (thinking)",
    "C": "Studio — GPU suspended for Ableton ASIO, Beat Architect between takes",
    "D": "Design Review — minicpm-v active for mockups/screenshots",
    "E": "Render — ComfyUI/SDXL exclusive (image generation queue)",
}

# ── Models ───────────────────────────────────────────────────────────────────
# 2026-09-29: router/tool-decision/general/vision swapped llama3.1:8b -> qwen3.5:9b
# after passing both Phase-0 gates with real evidence — 25/26 (28/28 after a
# retest that distinguished a test-design artifact from a real miss) on a
# 26-case tool-calling suite built against the actual TOOLS schema, zero
# hallucinated tool calls on any negative case, and correct Arabic OCR
# transcription (with accurate translations) on a bilingual UI mockup that
# broke minicpm-v into an infinite repetition loop. Measured VRAM: 5.6 GB
# @ 8192 ctx, resident alongside david-ft (5.4 GB) at once — 11.0 GB combined,
# comfortable margin under the ~14.5 GiB budget. david-ft/qwen-ft (fine-tuned
# on the llama3.1/qwen2.5-coder bases) and qwen2.5-coder:7b (technical domain)
# are NOT part of this swap — untouched, unrelated to what was tested here.
VISION_MODEL = "qwen3.5:9b"

DOMAIN_MODELS = {
    # 2026-09-30: swapped qwen2.5-coder:7b -> qwen3.5:9b for uiux_engineer/
    # prompt_architect (the only two agents that actually reach this entry —
    # developer fell through to qwen-ft via AGENT_MODEL_OVERRIDES until 2026-10-05
    # and now lands here too, on qwen3.5:9b). A 6-case A/B against the real AGENT_PROMPTS
    # base prompts favored qwen3.5:9b on 5/6 cases, decisively on one (it
    # engineered a prompt against specific known LLM failure modes — CoT
    # leakage, mid-generation self-correction — with concrete forbidden/
    # required examples; qwen2.5-coder gave three generic sentences for the
    # same case). Cost: ~2.5x slower (9.4s vs 24.9s avg) — tolerable since
    # this isn't a per-message hot path like the router. qwen2.5-coder:7b
    # stays installed — the build-room war room still hardcodes it separately
    # for its own developer path, untouched by this change. See the
    # technical-domain A/B audit, 2026-09-30.
    "technical":  "qwen3.5:9b",
    "music":      "qwen3.5:9b",
    "business":   "qwen3.5:9b",
    # Pilot (2026-07-13): llama3.1:8b + QLoRA adapter (attn-only, r=16) trained
    # for strict JSON discipline and Python/Arabic-prosody anti-hallucination.
    # Scoped to "general" only — that's "analyst", the Command Center default
    # agent, and the domain closest to what the adapter was actually validated
    # on. NOT retrained on qwen3.5 — still the original llama3.1-based adapter,
    # untouched by the 2026-09-29 router swap above.
    # The "general" domain runs on the same model as everything else. To use a second, fine-tuned model for it, set DAVID_GENERAL_MODEL.
    "general":    os.environ.get("DAVID_GENERAL_MODEL", "qwen3.5:9b"),
    "lore":       "qwen3.5:9b",
    "research": "qwen3.5:9b",
    "personal":   "qwen3.5:9b",
}

# qwen-ft (2026-09-15 pilot: qwen2.5-coder:7b + QLoRA adapter, scoped to "developer") was SIDELINED
# 2026-10-05: the audit log shows 6 qwen-ft requests in four months, and keeping it warm next to
# qwen3.5:9b (5.8 GB) + david-ft (5.4 GB) is ~16 GB, over the 14.5 GiB budget, so alternating analyst
# and developer turns cost an 11-16 s model swap each time. Developer/coding traffic now runs on
# qwen3.5:9b via DOMAIN_MODELS["technical"]. To bring it back: add {"developer": "qwen-ft"} here and
# warm it in set_mode("A") -- and expect the swaps again unless david-ft is retired instead.
AGENT_MODEL_OVERRIDES = {}

# Models no longer part of any mode. They stay installed; set_mode unloads them if something left one
# resident, so the pre-warmed set (qwen3.5:9b + david-ft, ~11 GB) stays inside the budget.
SIDELINED_MODELS = ("qwen-ft", "qwen2.5-coder:7b")
# 2026-09-29: swapped deepseek-r1:14b -> qwen3.5:9b (thinking mode) after a
# corrected 8-case A/B against real depth-file scenarios (cfo/legal_counsel/
# executive_advisor/venture_architect) scored 0 failures for either model,
# qwen3.5:9b tied or won 6/8 cases (notably: reproducibly correct on the
# beachhead-market framework where R1 missed it twice; R1 had one
# miscalculated intermediate figure and one hallucinated-then-corrected PRO
# name across two runs). Decisive factor: measured VRAM at 16384 ctx is
# 5.8 GB vs R1's 11.4 GB — small enough to coexist with the router (also
# qwen3.5:9b) instead of fighting it for VRAM, which was the whole point of
# testing this candidate. An initial "catastrophic failure" (empty response,
# runaway thinking) was traced to a flawed test prompt (an identity conflict
# the real system doesn't have), not a real defect — re-verified with the
# exact production prompt construction, zero failures. See the reasoning
# A/B and num_ctx/think audits, both 2026-09-29.
REASONING_MODEL = "qwen3.5:9b"
GEMINI_MODEL    = "gemini-2.5-flash"
CLAUDE_MODEL    = "claude-sonnet-5"

# audio_tools.py's separate() needs demucs, which (along with torch+cu126)
# may live in a different Python than this one (DAVID_AUDIO_PYTHON) — this process runs under 3.11, and
# bare "python" resolves to 3.11 via PATH, so subprocess calls into
# audio_tools.py must target 3.14 explicitly rather than relying on PATH.
# librosa (used by analyze()) is installed in both, so both calls use this.
AUDIO_PYTHON = str(DAVID_AUDIO_PYTHON)

AGENTS = [
    "chief_of_staff", "developer", "uiux_engineer", "creative_director",
    "brand_audience", "beat_architect", "cfo", "executive_advisor",
    "venture_architect", "legal_counsel", "analyst", "linguist",
    "acoustician", "language_specialist", "lore_keeper",
    "performance_coach", "prompt_architect"
]

# ── Tier 2/3 escalation map ───────────────────────────────────────────────────
# Agents not listed here never go external (implicit firewall).
# None = agent exists but is firewalled.
#
# 2026-07-13: narrowed to "only if necessary" — local-first policy. Local
# models (llama3.1:8b / deepseek-r1:14b, 128k+ ctx) now handle everything
# except genuinely external-facing, high-consequence-if-wrong output (legal
# filings, investor/board-facing docs). Cloud stays wired up (nothing
# deleted, existing budget/setup intact) as a rare last resort, not a
# routine path. Every agent removed here already has a local reasoning
# floor: R1_AGENTS (cfo/executive_advisor/venture_architect/legal_counsel)
# always run on deepseek-r1:14b; every other agent gets a per-request
# deepseek-r1:14b escalation whenever needs_reasoning=true (see ws_chat's
# force_reasoning) — so this trades "Claude/Gemini" for "deepseek-r1:14b
# locally" on the hardest requests, not "no reasoning at all".
TIER2_AGENTS = {   # Gemini — large docs / research — reserved for size-driven risk
    "cfo":              "large financial documents",
    "legal_counsel":    "large legal documents",
}
TIER3_AGENTS = {   # Claude — high-stakes / client-facing output — external-facing only
    "executive_advisor":"board or investor-facing output",
    "venture_architect":"investor-facing documents",
    "legal_counsel":    "external filings",
}
# Firewalled agents (never external, even if API keys exist):
FIREWALLED = {"linguist", "beat_architect", "uiux_engineer", "lore_keeper",
              "performance_coach", "prompt_architect", "acoustician",
              "language_specialist", "chief_of_staff"}

# ── Budget gate ───────────────────────────────────────────────────────────────
BUDGET_FILE    = str(DAVID_DATA / "budget.json")
BUDGET_CAP     = 25.00   # USD / calendar month — raised from $10 (June 2026 headroom)
_BUDGET_WARNED = False   # kept for import compatibility — truth is now in budget.json["warned"]

# USD per 1 000 tokens (source: provider pricing pages)
_GEM_IN_K  = 0.000_30   # Gemini 2.5 Flash  input  $0.30/1M
_GEM_OUT_K = 0.002_50   # Gemini 2.5 Flash  output $2.50/1M
_CLU_IN_K  = 0.003_00   # Claude Sonnet 5   input  $3.00/1M
_CLU_OUT_K = 0.015_00   # Claude Sonnet 5   output $15.00/1M

# ── DLP — any match → local only, Tier 2/3 blocked ──────────────────────────

# Compiled on import; applied against whitespace-stripped text for numeric patterns.
_IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_CARD_RE = re.compile(r"\d{13,19}")
_SSN_RE  = re.compile(r"\d{3}-\d{2}-\d{4}")

# Regex/keyword matching only — catches accidental leaks of labeled or
# structured secrets (an explicit "password:", a card number, the literal
# word "confidential"). It has no semantic understanding, so sensitive
# content phrased without one of these markers will not be caught; treat
# this as a first filter, not a real confidentiality boundary.
_POLICY = load_policy()          # config/vaults.toml, or config/vaults.example.toml
DLP_PATTERNS = list(_POLICY["dlp"]["patterns"])
# a message that names a path inside a private vault folder stays local too; a firewalled vault's folder always counts as private,
# even if the policy forgot to list it in private_vault_folders (S-08, 2026-10-20)
DLP_PATTERNS.append(re.escape(str(DAVID_VAULTS)) + r"[\\/](?:" + "|".join(re.escape(f) for f in private_vault_folders()) + ")")


def _luhn_check(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed([int(c) for c in digits])):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0

# The agents' personas live in agents/<agent>/role.md (see agents/README.md and agent_spec.py), one file per agent.
# AGENTS (above) is the list of valid agent ids.
DEFAULT_PROMPT = (
    "You are David, the operator's AI chief of staff. "
    "Be helpful, direct, and concise."
)

# ── Focus / rest windows ─────────────────────────────────────────────────────

def check_focus_window():
    hour = datetime.datetime.now().hour
    if hour < FOCUS_START:
        return f"[Rest] It's {hour:02d}:00 — your day starts at {FOCUS_START:02d}:00. Answering anyway."
    if hour >= FOCUS_END:
        return f"[Rest] It's {hour:02d}:00 — past {FOCUS_END:02d}:00. Consider wrapping up soon."
    return None


# ── Voice output (XTTS-v2 via Python 3.11) ───────────────────────────────────

def speak_async(text: str) -> None:
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    clean = re.sub(r"\s+", " ", clean)[:800]
    if not clean:
        return
    os.makedirs(SPEAK_QUEUE, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        with open(os.path.join(SPEAK_QUEUE, f"{ts}.txt"), "w", encoding="utf-8") as f:
            f.write(clean)
    except OSError:
        pass


def execute_vision(image_path: str, prompt: str) -> None:
    import base64
    image_path = image_path.strip().strip('"')
    if not os.path.exists(image_path):
        print(f"  [Mode D] Image not found: {image_path}\n")
        return
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lstrip(".").lower()
    if ext == "jpg":
        ext = "jpeg"

    system_prompt = (
        "You are David's design critic. When shown a UI screenshot or mockup, "
        "give specific, actionable feedback on: layout hierarchy, spacing consistency, "
        "typography choices, color usage, and UX clarity. Be direct — one clear "
        "recommendation per issue. No vague praise."
    )
    body = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt or "Review this design. What are the top issues and one fix each?",
             "images": [b64]},
        ],
        "stream": True,
    }
    req  = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    full = []
    print(f"  [Mode D] Vision — analyzing {os.path.basename(image_path)}...\n")
    with urllib.request.urlopen(req, timeout=180) as resp:
        for line in resp:
            chunk = json.loads(line)
            if chunk.get("error"):
                print(f"\n  [Ollama error] {chunk['error']}\n")
                break
            tok   = chunk.get("message", {}).get("content", "")
            print(tok, end="", flush=True)
            full.append(tok)
    print("\n")
    audit({"mode": "D", "model": VISION_MODEL, "image": image_path})
    if SPEAK:
        speak_async("".join(full))


# ── Layer 0 — instant pattern matching, no LLM ──────────────────────────────
LAYER0 = [
    (r"^park (this )?idea[:,]?\s*",                               "incubation_vault"),
    (r"^mode\s+([abcde])\b",                                      "mode_switch"),
    (r"^(deep session|reasoning mode)\b",                         "mode_b"),
    (r"^studio (mode|on)\b",                                      "mode_c"),
    (r"\b(schedule|reschedule|calendar|meeting at)\b",            "chief_of_staff"),
    (r"\b(my events|what.*events|events today|events tomorrow)\b","chief_of_staff"),
    (r"\b(my emails?|check (my )?inbox|email digest|unread emails?)\b", "gmail"),
    (r"^(open|show) (the )?ledger",                               "task_ledger"),
    (r"\b(remind me|set a timer|alarm)\b",                        "chief_of_staff"),
    (r"(?i)\.(png|jpg|jpeg|bmp|gif|webp)\b",                      "vision_image"),
    (r"(?i)^(please\s+)?(separate (the\s+)?(stems?|vocals?|drums?|bass)|run demucs)\b", "audio_separate"),
    (r"(?i)^(please\s+)?analyze (this\s+|the\s+)?(audio|track|wav|mp3)\b",  "audio_analyze"),
    (r"(?i)\b(bpm|key) of\b[^?]*\.(wav|mp3|flac|aiff?|m4a|ogg)\b",          "audio_analyze"),
    (r"(?i)^(generate|render|draw|create|make)\s+(an?\s+)?(image|picture|photo|illustration|artwork|visual)\b", "generate_image"),
    (r"^(what can you do|who are you|are you (live|online|there|ready)|what('s| is) (david|your) (status|state|purpose))", "david_meta"),
    (r"^david status\b",                                          "david_meta"),
    (r"(?i)^(save (memory|session)|remember this( session)?)\b",  "save_memory"),
]

# Layer-0 routes that load a model onto the GPU (minicpm-v / demucs / SDXL /
# librosa decode) — blocked while the studio lock is armed, same as Layer 1.
GPU_LAYER0_ROUTES = {"vision_image", "audio_separate", "audio_analyze", "generate_image"}

SCHEMA = {
    "type": "object",
    "properties": {
        "domain":          {"type": "string", "enum": ["business", "music", "lore", "research", "personal", "technical", "general"]},
        "agent":           {"type": "string", "enum": AGENTS},
        "needs_reasoning": {"type": "boolean"},
        "confidence":      {"type": "number"},
        "intent":          {"type": "string", "enum": ["chat", "search"]}
    },
    "required": ["domain", "agent", "needs_reasoning", "confidence", "intent"]
}


# ── Ableton command parser ────────────────────────────────────────────────────

# Anchored to the start of the message (tolerating a short politeness prefix) so
# conversational sentences that merely mention "stop", "play", or "… 120 bpm"
# can't fire transport/track side effects mid-question.
_AB = r"^(?:(?:please|ok(?:ay)?|now|david)[\s,]+)*"
ABLETON_PATTERNS = [
    (_AB + r"set\s+(?:the\s+)?bpm\s+(?:to\s+)?(\d+(?:\.\d+)?)\b", "set_bpm"),
    (_AB + r"bpm\s+(?:to\s+)?(\d+(?:\.\d+)?)\b",                  "set_bpm"),
    (_AB + r"(\d+(?:\.\d+)?)\s+bpm\b",                            "set_bpm"),
    (_AB + r"play\b",                                             "play"),
    (_AB + r"stop\b",                                             "stop"),
    (_AB + r"new\s+midi\s+track\b",                               "create_midi_track"),
    (_AB + r"create\s+(?:a\s+)?(?:midi\s+)?track\b",              "create_midi_track"),
    (_AB + r"arm\s+track\s+(\d+)\b",                              "arm_track"),
    (_AB + r"quick\s+beat\s+(?:at\s+)?(\d+)\s*(?:bpm)?\b",        "quick_beat"),
    (_AB + r"drop\s+(?:a\s+)?beat\s+(?:at\s+)?(\d+)\b",           "quick_beat"),
]

def try_ableton_command(text):
    """
    Check if text is a direct Ableton control command.
    Returns result string if handled, None if not an Ableton command.
    """
    try:
        import ableton_bridge as ab
    except Exception as e:
        return f"[Ableton] bridge unavailable: {e}"

    t = text.lower()
    for pattern, action in ABLETON_PATTERNS:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            try:
                if action == "set_bpm":
                    return ab.set_bpm(float(m.group(1)))
                elif action == "play":
                    return ab.play()
                elif action == "stop":
                    return ab.stop()
                elif action == "create_midi_track":
                    return ab.create_midi_track()
                elif action == "arm_track":
                    return ab.arm_track(int(m.group(1)))
                elif action == "quick_beat":
                    return ab.quick_beat(float(m.group(1)))
            except Exception as e:
                return f"[Ableton] {action} failed: {e}"
    return None


# ── Depth file loader ─────────────────────────────────────────────────────────

def load_depth(agent):
    path = os.path.join(DEPTH_DIR, f"{agent}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


# The single prompt builder / tool scoper (agent_spec.py). Every agent has an agents/<agent>/role.md; the tables handed over
# here are only what an UNKNOWN agent name falls back to. Handed over here (not imported there) because this file also runs as __main__.
import agent_spec
from agent_spec import build_system_prompt
agent_spec.configure({}, R1_AGENTS, load_depth, DEFAULT_PROMPT)
agent_spec.register_agents(AGENTS, FIREWALLED)   # the identity registry used by the tool and memory policies

# A read-only view of the personas: agent id -> the body of its role.md (what the model is told it is). Edit the role files, not this.
AGENT_PROMPTS = {a: (agent_spec.load_spec(a).body if agent_spec.load_spec(a) else DEFAULT_PROMPT) for a in AGENTS}


# ── Ollama VRAM management ────────────────────────────────────────────────────

def _ollama_evict(model: str) -> None:
    """Unload a model from VRAM; blocks until /api/ps confirms it is gone (max 10 s)."""
    body = json.dumps({"model": model, "keep_alive": 0, "prompt": ""}).encode()
    req  = urllib.request.Request(
        OLLAMA_HOST + "/api/generate", body,
        {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception:
        return

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(OLLAMA_HOST + "/api/ps", timeout=5) as r:
                data = json.loads(r.read())
            running = [m.get("name", "") for m in data.get("models", [])]
            if not any(model in name or name in model for name in running):
                return
        except Exception:
            return
        time.sleep(0.5)


def _ollama_warm(model: str) -> None:
    """Pre-load a model into VRAM in a background thread (non-blocking)."""
    def _load():
        body = json.dumps({
            "model": model, "keep_alive": -1,
            "prompt": ".", "stream": False,
            "options": {"num_predict": 1}
        }).encode()
        req = urllib.request.Request(
            OLLAMA_HOST + "/api/generate", body,
            {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
        except Exception:
            pass
    import threading
    threading.Thread(target=_load, daemon=True).start()


# ── Mode management ───────────────────────────────────────────────────────────

def get_mode():
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as f:
            m = f.read().strip().lstrip("﻿").upper()
        return m if m in MODES else "A"
    except FileNotFoundError:
        return "A"


def set_mode(letter):
    letter = letter.upper()
    if letter not in MODES:
        print(f"  Unknown mode '{letter}'. Valid modes: {', '.join(MODES)}\n")
        return

    # VRAM enforcement — evict what the new mode doesn't need,
    # pre-warm what it does. OLLAMA_KEEP_ALIVE=-1 pins models indefinitely
    # without this, so explicit eviction is the only way to free VRAM.
    # 2026-09-29: REASONING_MODEL and VISION_MODEL both became "qwen3.5:9b" (same
    # string as most of DOMAIN_MODELS) when the router/tool-decision/vision/reasoning
    # swap landed. That model now runs Layer 1 + tool-decision on EVERY message in
    # EVERY mode — evicting "REASONING_MODEL" or "VISION_MODEL" would now evict the
    # router itself, forcing a cold-load just to classify the next message, and in
    # Mode D would evict the very model Mode D wants to KEEP for vision. Modes A-D
    # below no longer evict qwen3.5:9b for this reason. Mode E is the one exception —
    # it intentionally wants maximum VRAM for SDXL and doesn't expect chat traffic
    # during a render, so evicting the router there is still correct.
    ROUTER_MODEL = "qwen3.5:9b"  # == REASONING_MODEL == VISION_MODEL as of 2026-09-29
    # 2026-10-05: DOMAIN_MODELS["technical"] is qwen3.5:9b since 2026-09-30, so the old
    # `_ollama_evict(DOMAIN_MODELS["technical"])  # qwen2.5-coder` lines in B/C/D were evicting the
    # ROUTER. Sidelined models are now named explicitly (SIDELINED_MODELS) in every mode.
    if letter == "A":
        _ollama_warm(ROUTER_MODEL)                    # router/tool-decision/vision/reasoning + developer/technical
        if DOMAIN_MODELS["general"] != ROUTER_MODEL:
            _ollama_warm(DOMAIN_MODELS["general"])    # a second resident model, e.g. a fine-tuned general model
        for m in SIDELINED_MODELS:
            _ollama_evict(m)
        print(f"  [VRAM] Pre-warming {ROUTER_MODEL}" + (f" + {DOMAIN_MODELS['general']}" if DOMAIN_MODELS["general"] != ROUTER_MODEL else "") + " in background.")
    elif letter == "B":
        for m in SIDELINED_MODELS:
            _ollama_evict(m)
        print("  [VRAM] qwen-ft and qwen-coder stay unloaded. qwen3.5 (router + reasoning) stays resident.")
    elif letter == "C":
        if DOMAIN_MODELS["general"] != ROUTER_MODEL:      # never evict the router itself in Mode C
            _ollama_evict(DOMAIN_MODELS["general"])
        for m in SIDELINED_MODELS:
            _ollama_evict(m)
        print("  [VRAM] Evicting the second resident model — ASIO DPC budget protected. "
              "qwen3.5 (router) stays resident so routing doesn't stall on the next message.")
    elif letter == "D":
        for m in SIDELINED_MODELS:
            _ollama_evict(m)
        print("  [VRAM] qwen-ft and qwen-coder stay unloaded. qwen3.5 (router + vision) stays resident.")
    elif letter == "E":
        if DOMAIN_MODELS["general"] != ROUTER_MODEL:
            _ollama_evict(DOMAIN_MODELS["general"])
        for m in SIDELINED_MODELS:
            _ollama_evict(m)
        _ollama_evict(ROUTER_MODEL)                 # qwen3.5:9b — router/vision/reasoning, all evicted here on purpose
        print("  [VRAM] All models evicted, including the router — maximum VRAM headroom for SDXL.")

    with open(MODE_FILE, "w", encoding="utf-8") as f:
        f.write(letter)

    # Mode C manually arms the studio lock; studio_mode.py also manages it
    # based on actual AbletonOSC transport state.
    if letter == "C":
        open(STUDIO_LOCK, "w").close()
    elif os.path.exists(STUDIO_LOCK) and letter != "C":
        os.remove(STUDIO_LOCK)

    print(f"\n  ── Mode {letter}: {MODES[letter]} ──\n")
    audit({"layer": 0, "action": "mode_switch", "mode": letter})


# ── DLP pre-flight ────────────────────────────────────────────────────────────

def dlp_flag(text):
    matched = None
    if _SSN_RE.search(text):   # dashes are the signal here, so check before compacting
        matched = "ssn"
    if matched is None:
        compact = re.sub(r"[\s\-]", "", text)   # strip spaces/dashes to catch spaced IBANs / cards
        if _IBAN_RE.search(compact):
            matched = "iban"
        else:
            for m in _CARD_RE.finditer(compact):
                if _luhn_check(m.group()):
                    matched = "card_luhn"
                    break
    if matched is None:
        for p in DLP_PATTERNS:
            if re.search(p, text):
                matched = p
                break
    if matched is None:
        # 2026-10-20 (audit P1-5): the key and token shapes the audit log already masks (AWS, GitHub, sk-, Google keys, PEM
        # private keys, user:password URLs, bearer tokens, JWTs, "password is ...") must not leave the machine either.
        for rx in _SECRET_RES:
            if rx.search(text):
                matched = rx.pattern
                break
    if matched is None:
        return False
    # Log which pattern fired (never the text itself) so false positives are
    # diagnosable from the audit trail instead of a bare "DLP blocked" mystery.
    try:
        audit({"action": "dlp_match", "pattern": matched})
    except Exception:
        pass
    return True


# ── Budget gate ───────────────────────────────────────────────────────────────

def _budget_load() -> dict:
    month = datetime.date.today().strftime("%Y-%m")
    try:
        with open(BUDGET_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("month") != month:
            data = {"month": month, "gemini_usd": 0.0, "claude_usd": 0.0}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"month": month, "gemini_usd": 0.0, "claude_usd": 0.0}
    return data


def _budget_check(request_id: str = None) -> bool:
    """Returns False (hard stop) when monthly cap is reached; speaks warning once per month."""
    data      = _budget_load()
    spent     = data.get("gemini_usd", 0.0) + data.get("claude_usd", 0.0)
    remaining = BUDGET_CAP - spent
    if remaining <= 0:
        msg = f"Budget hard stop. Monthly cap of ${BUDGET_CAP:.0f} reached. All requests routing to local Tier 1 only."
        print(f"  [Budget] HARD STOP — {msg}")
        speak_async(msg)
        audit({"action": "budget_hard_stop", "spent_usd": round(spent, 4), "request_id": request_id})
        return False
    if remaining < BUDGET_CAP * 0.20 and not data.get("warned", False):
        msg = f"Budget warning. Only ${remaining:.2f} of your ${BUDGET_CAP:.0f} monthly limit remains."
        print(f"  [Budget] WARNING: {msg}")
        speak_async(msg)
        audit({"action": "budget_warning", "remaining_usd": round(remaining, 4), "request_id": request_id})
        try:
            with portalocker.Lock(BUDGET_FILE, mode="a+", encoding="utf-8", timeout=5) as fh:
                fh.seek(0)
                try:
                    d = json.load(fh)
                except (json.JSONDecodeError, ValueError):
                    d = data
                d["warned"] = True
                fh.seek(0)
                fh.truncate()
                json.dump(d, fh)
        except (portalocker.LockException, OSError):
            pass
    return True


def _budget_record(provider: str, in_tok: int, out_tok: int) -> None:
    month = datetime.date.today().strftime("%Y-%m")
    try:
        with portalocker.Lock(BUDGET_FILE, mode="a+", encoding="utf-8", timeout=5) as fh:
            fh.seek(0)
            try:
                data = json.load(fh)
                if data.get("month") != month:
                    data = {"month": month, "gemini_usd": 0.0, "claude_usd": 0.0}
            except (json.JSONDecodeError, ValueError):
                data = {"month": month, "gemini_usd": 0.0, "claude_usd": 0.0}
            if provider == "gemini":
                cost = in_tok * _GEM_IN_K / 1000 + out_tok * _GEM_OUT_K / 1000
                data["gemini_usd"] = round(data.get("gemini_usd", 0.0) + cost, 4)
            else:
                cost = in_tok * _CLU_IN_K / 1000 + out_tok * _CLU_OUT_K / 1000
                data["claude_usd"] = round(data.get("claude_usd", 0.0) + cost, 4)
            fh.seek(0)
            fh.truncate()
            json.dump(data, fh)
    except (portalocker.LockException, OSError):
        pass


# ── Audit ─────────────────────────────────────────────────────────────────────

# Privacy (audit finding S-01, 2026-10-18): the audit log records WHAT happened, never WHAT WAS SAID.
# Message text, replies, tool arguments and tool results are replaced by a size marker before anything is
# written, so a secret that passes through a tool or a chat turn is not copied into a file that is kept
# for months and served by /api/audit. DAVID_AUDIT_CONTENT=1 is a debugging switch only: it keeps a short,
# masked excerpt (credentials, card numbers and the DLP patterns are removed even then). It is never
# set by the code or the tests.
AUDIT_MAX_BYTES = int(float(os.environ.get("DAVID_AUDIT_MAX_MB", "10")) * 1024 * 1024)   # per file
AUDIT_BACKUPS   = int(os.environ.get("DAVID_AUDIT_BACKUPS", "5"))   # audit_log.jsonl.1 .. .5 -> at most 6 files in total

# Free-text fields: always replaced by a marker.
_AUDIT_CONTENT_KEYS = ("user", "response", "input", "prompt", "result", "args", "text", "query", "message",
                       "content", "summary", "file", "transcript", "reply")
# Short structured fields that are safe to keep as they are; any OTHER string longer than this is redacted too,
# so a future caller that adds a new free-text field cannot leak it by accident.
_AUDIT_FREE_TEXT_LIMIT = 80
if os.environ.get("DAVID_AUDIT_CONTENT") == "1":
    print("  [audit] DAVID_AUDIT_CONTENT=1: masked excerpts of messages and tool data are being written to the audit "
          "log (debugging only; unset it to stop)", file=sys.stderr)
_AUDIT_ARG_NAME = re.compile(r"[a-z_][a-z0-9_]{0,39}")
_SECRET_RES = [re.compile(p) for p in (
    r"AKIA[0-9A-Z]{16}",                                   # AWS access key id
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    r"\bsk-[A-Za-z0-9_\-]{16,}",                           # API keys of the sk-... form
    r"\bAIza[0-9A-Za-z_\-]{30,}",                          # Google API key
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+:[^\s@/]+@",   # scheme://user:password@host
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",   # JWT
    r"(?i)\b(?:password|passwd|pwd|passcode)\s*(?:is|was|:|=)\s*\S+",
)]


def _mask_text(text, limit=200):
    """Remove credentials, card/IBAN/SSN numbers and DLP-pattern matches, then truncate."""
    s = str(text)
    for rx in _SECRET_RES:
        s = rx.sub("[REDACTED]", s)
    s = _SSN_RE.sub("[REDACTED]", s)
    s = re.sub(r"(?:\d[ \-]?){13,19}",
               lambda m: "[REDACTED]" if _luhn_check(re.sub(r"\D", "", m.group())) else m.group(), s)
    s = re.sub(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", "[REDACTED]", s)
    for p in DLP_PATTERNS:
        try:
            s = re.sub(p, "[REDACTED]", s)
        except re.error:
            pass
    return s[:limit]


def _safe_error(err):
    """An error string reduced to its exception type plus a short, masked message: quoted values, paths, URLs,
    e-mail addresses and long numbers (the usual carriers of user input) are removed."""
    s = str(err)
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", s)
    s = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s:]*[\\/][^\s:]*", "<path>", s)
    s = re.sub(r"\b\w+://\S+", "<url>", s)
    s = re.sub(r"\S+@\S+\.\w+", "<email>", s)
    s = re.sub(r"\d{6,}", "<n>", s)
    return _mask_text(s, 160)


def _redact_value(value, keep_excerpt):
    return _mask_text(value) if keep_excerpt else f"<redacted:{len(str(value))} chars>"


def redact_entry(entry):
    """The audit-safe copy of an entry: content fields become size markers (or masked excerpts in debug mode),
    tool-argument values are dropped but their names kept, errors are reduced, long free text is redacted.
    Also used by /api/audit on lines read back from disk, so entries written before this change are never
    served raw."""
    if not isinstance(entry, dict):
        return None
    keep = os.environ.get("DAVID_AUDIT_CONTENT") == "1"
    out = {}
    for k, v in entry.items():
        key = str(k)[:40]
        if v is None or isinstance(v, (bool, int, float)):
            out[key] = v
        elif key == "args" and isinstance(v, dict):
            out[key] = {(a if _AUDIT_ARG_NAME.fullmatch(str(a)) else "<arg>"): _redact_value(x, keep) for a, x in list(v.items())[:20]}
        elif key in _AUDIT_CONTENT_KEYS:
            out[key] = _redact_value(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str), keep)
        elif key == "error":
            out[key] = _safe_error(v)
        elif isinstance(v, dict):
            out[key] = {str(a)[:40]: (x if isinstance(x, (bool, int, float)) or x is None
                                      else (str(x) if len(str(x)) <= _AUDIT_FREE_TEXT_LIMIT else f"<redacted:{len(str(x))} chars>"))
                        for a, x in list(v.items())[:20]}
        else:
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
            out[key] = s if len(s) <= _AUDIT_FREE_TEXT_LIMIT else f"<redacted:{len(s)} chars>"
    return out


_rotate_warned = False


def _rotate_audit_log():
    """Size-based retention: when the active file passes AUDIT_MAX_BYTES it becomes .1, older copies shift up and
    the one past AUDIT_BACKUPS is replaced, so the retained set is bounded by (AUDIT_BACKUPS + 1) x AUDIT_MAX_BYTES.
    A failed rotation (on Windows another process may hold the file for a moment) never drops an entry: the
    write already happened and the next write retries."""
    global _rotate_warned
    try:
        if os.path.getsize(LOG) < AUDIT_MAX_BYTES:
            return
        with portalocker.Lock(LOG + ".rotate.lock", mode="a", timeout=2):
            if not os.path.exists(LOG) or os.path.getsize(LOG) < AUDIT_MAX_BYTES:
                return                                  # another process rotated it meanwhile
            for i in range(AUDIT_BACKUPS - 1, 0, -1):
                if os.path.exists(f"{LOG}.{i}"):
                    os.replace(f"{LOG}.{i}", f"{LOG}.{i + 1}")
            if AUDIT_BACKUPS > 0:
                os.replace(LOG, f"{LOG}.1")
            else:
                open(LOG, "w").close()
    except (OSError, portalocker.LockException) as e:
        if not _rotate_warned:
            _rotate_warned = True
            print(f"  [audit] log rotation skipped ({type(e).__name__}); will retry on a later write", file=sys.stderr)


def audit(entry):
    """Shared audit writer for every surface (CLI + HUD server import this).

    Locked (the server writes to the same file from another process) and with a
    mandatory field set, so log consumers can rely on every line carrying:
    ts, surface, request_id, status, duration_ms. Content is redacted first (redact_entry).
    """
    entry.setdefault("ts", datetime.datetime.now().isoformat(timespec="milliseconds"))
    entry.setdefault("surface", "cli")
    entry.setdefault("request_id", None)
    entry.setdefault("status", "error" if ("error" in entry) else "ok")
    entry.setdefault("duration_ms", None)
    safe = redact_entry(entry)
    try:
        os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
        new_file = not os.path.exists(LOG)
        with portalocker.Lock(LOG, mode="a", encoding="utf-8", timeout=5) as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
        if new_file and os.name != "nt":
            os.chmod(LOG, 0o600)                        # owner-only on POSIX; Windows inherits the profile ACL
    except Exception as e:
        print(f"  [audit] write failed: {type(e).__name__}", file=sys.stderr)
        return
    _rotate_audit_log()


# ── Idea parking ──────────────────────────────────────────────────────────────

def park_idea(text):
    os.makedirs(INCUBATION_DIR, exist_ok=True)
    idea = re.sub(r"^park (this )?idea[:,]?\s*", "", text, flags=re.IGNORECASE).strip()
    if not idea:
        idea = "(no content — add detail here)"
    ts = datetime.datetime.now()
    filename = ts.strftime("%Y-%m-%d_%H-%M-%S") + "_idea.md"
    filepath = os.path.join(INCUBATION_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Parked Idea\n**Date:** {ts.strftime('%Y-%m-%d %H:%M')}\n\n{idea}\n")
    return filepath


# ── Layer 0 dispatch ──────────────────────────────────────────────────────────

def layer0(text):
    for pattern, route in LAYER0:
        if re.search(pattern, text, re.IGNORECASE):
            # For mode_switch, extract the letter
            if route == "mode_switch":
                m = re.search(r"mode\s+([abcde])", text, re.IGNORECASE)
                return ("mode_switch", m.group(1).upper() if m else None)
            return (route, None)
    return (None, None)


# ── Layer 1 — LLM classification ─────────────────────────────────────────────

def layer1(text, mode):
    hints = []
    if mode == "C":
        hints.append(
            "STUDIO MODE: Ableton is running. Prefer domain='music' agent='beat_architect' "
            "for ambiguous requests unless clearly technical, business, or urgent."
        )
    if mode == "D":
        hints.append(
            "DESIGN REVIEW MODE: User has dropped a screenshot or mockup. "
            "Prefer domain='technical' agent='uiux_engineer' for UI/UX analysis."
        )
    studio_hint = " ".join(hints)

    body = {
        "model": "qwen3.5:9b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are David's intake router. Classify each request into exactly one domain+agent pair.\n"
                    "technical — developer: code, scripts, APIs, debugging, file operations, software tasks\n"
                    "technical — uiux_engineer: Figma, mockup review, UI critique, CTA, wireframes, design systems\n"
                    "technical — prompt_architect: writing or designing an LLM system prompt — always pick this when user says 'write a system prompt' or 'design a prompt'\n"
                    "music — beat_architect: Ableton, beats, mixing, reverb plugins, EQ, compression, DAW, recording, sample rate, maqam, vocal production\n"
                    "music — acoustician: physical room treatment only — room modes, bass traps, speaker/mic placement (never DAW plugins)\n"
                    "business — executive_advisor: overall strategy, pricing your services, rate-setting, hiring, company direction\n"
                    "business — cfo: financial modeling, cash flow, P&L, investment ROI, budgets\n"
                    "business — venture_architect: new ventures, startups, expanding into new markets, pitch decks, real estate growth\n"
                    "business — legal_counsel: contract review, NDA clauses, IP, compliance, legal risk\n"
                    "business — brand_audience: WHO to reach and HOW — marketing, social media strategy, audience growth, campaign planning\n"
                    "business — creative_director: WHAT it looks/sounds like — visual identity, brand voice, creative direction\n"
                    "lore — lore_keeper: world-building, fiction writing, gaming narrative, speculative settings\n"
                    "research — linguist: close reading of the research corpus, terminology, patterns across primary sources\n"
                    "research — language_specialist: translation, dialect conversion, Gulf Arabic, linguistic register shifts, code-switching, etymology\n"
                    "personal — performance_coach: personal habits, routines, morning routines, sleep, burnout, health — including Arabic requests about personal improvement\n"
                    "general — analyst: factual questions, history, geography — ONLY when nothing above fits\n"
                    "RULES: the domain field MUST match the group of the chosen agent (e.g. uiux_engineer always uses domain=technical). "
                    "needs_reasoning=true only for complex financial modeling, multi-step legal analysis, or novel algorithms. "
                    "intent='search' for anything needing live or current information beyond your training data — "
                    "news, scores, results, prices, schedules, \"has X happened/started yet\", or any question about "
                    "recent or ongoing real-world events. intent='chat' for everything else, including questions you "
                    "can answer from general knowledge alone. "
                    "NEVER intent='search' for a question about the user's OWN data — their calendar, tasks, "
                    "email, notes, or running processes. Those are answered by a dedicated tool, not the open web; "
                    "a web search for someone's private calendar or task list returns nothing useful and pollutes "
                    "the answer with irrelevant results. These are always intent='chat' regardless of how the "
                    "question is phrased (e.g. \"right now\", \"today\" do not make it a search). "
                    "Return JSON only.\n"
                    "ROUTING EXAMPLES (follow these exactly):\n"
                    "\"What bass-trap placement fixes my 80Hz room mode?\" → acoustician, music, intent=chat\n"
                    "\"Design a TikTok content strategy\" → brand_audience, business, intent=chat\n"
                    "\"Define the visual identity for the brand\" → creative_director, business, intent=chat\n"
                    "\"Translate this to Gulf Arabic\" → language_specialist, research, intent=chat\n"
                    "\"Write a system prompt for a legal LLM\" → prompt_architect, technical, intent=chat\n"
                    "\"Review this Figma mockup hierarchy\" → uiux_engineer, technical, intent=chat\n"
                    "\"Summarize the games played in the World Cup 2026 so far\" → analyst, general, intent=search\n"
                    "\"What's Bitcoin trading at right now?\" → cfo, business, intent=search\n"
                    "\"What's on my calendar today?\" → analyst, general, intent=chat (own data, tool-backed)\n"
                    "\"What tasks are pending right now?\" → analyst, general, intent=chat (own data, tool-backed)\n"
                    "\"Check my unread emails\" → analyst, general, intent=chat (own data, tool-backed)"
                    + ("\n" + studio_hint if studio_hint else "")
                )
            },
            {"role": "user", "content": text}
        ],
        "format":  SCHEMA,
        "stream":  False,
        # 8192 matches every other llama3.1:8b call site (tool-decision, general
        # execution) — this used to be its own 4096, forcing a reload on the very
        # next llama3.1 call (tool-decision, 8192) on EVERY turn. See num_ctx audit
        # 2026-09-29.
        "options": {"num_ctx": num_ctx_for("qwen3.5:9b"), "temperature": 0},
        # qwen3.5:9b thinks by default (Modelfile: thinking, default=true) — a
        # live test on 2026-09-29 showed one classification call burn 26.9s and
        # 1674 tokens on an invisible reasoning trace for a routing decision that
        # needs none. This is a fixed-schema classification, not a task that
        # benefits from visible chain-of-thought.
        "think": False,
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        reply = json.loads(r.read())
    d = json.loads(reply["message"]["content"])
    if d.get("agent") == "analyst":
        d["confidence"] = min(d.get("confidence", 0.5), 0.5)
    return d


# ── RAG context retrieval ─────────────────────────────────────────────────────

def rag_context(agent, query):
    """Memory, facts and vault notes for `agent` -- each source scoped to what this agent may see (audit finding
    S-04, 2026-10-18): episodes by memory.visible_types(agent), facts by the agent's vault namespaces, notes by
    rag.retrieve's namespaces. An unknown agent gets nothing from any of them."""
    parts = []
    try:
        sys.path.insert(0, str(DAVID_HOME))
        import rag as rag_module
        namespaces = list(rag_module.AGENT_NAMESPACES.get(agent, [])) if isinstance(agent, str) else []
    except Exception as e:
        audit({"action": "rag_context_failure", "source": "policy", "agent": agent,
               "error": f"{type(e).__name__}: {e}"})
        return ""
    try:
        import memory as _mem
        mem = _mem.recall(query, agent=agent)
        if mem:
            parts.append(mem)
    except Exception as e:
        audit({"action": "rag_context_failure", "source": "memory", "agent": agent,
               "error": f"{type(e).__name__}: {e}"})
    try:
        import knowledge as _know
        facts = _know.recall(query, domains=namespaces)
        if facts:
            parts.append(facts)
    except Exception as e:
        audit({"action": "rag_context_failure", "source": "knowledge", "agent": agent,
               "error": f"{type(e).__name__}: {e}"})
    try:
        results = rag_module.retrieve(agent, query, top_k=3)
        if results:
            chunks = "\n\n".join([f"[{r['vault']}/{r['file']}]\n{r['text']}" for r in results])
            parts.append(f"--- Relevant notes from your vaults ---\n{chunks}\n--- End of notes ---")
    except Exception as e:
        audit({"action": "rag_context_failure", "source": "rag", "agent": agent,
               "error": f"{type(e).__name__}: {e}"})
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


# ── Execution — Tier 1 (local Ollama) ────────────────────────────────────────

def execute(domain, agent, text):
    # r1 agents always use deepseek-r1:14b + their depth file
    if agent in R1_AGENTS:
        system = build_system_prompt(agent, fallback=None)
        model  = REASONING_MODEL
        num_ctx = num_ctx_for(model)
        think   = True
        label   = f"{agent} / {REASONING_MODEL} [depth]"
    else:
        model  = AGENT_MODEL_OVERRIDES.get(agent) or DOMAIN_MODELS.get(domain, "qwen3.5:9b")
        system = build_system_prompt(agent, fallback=None)
        # 8192 — matches this model's every other call site (Layer 1, tool-decision).
        # Was 16384 (its own outlier), forcing a reload vs. the tool-decision call that
        # runs immediately before this on every turn. RAG injection is small post the
        # 2026-09-24 relevance-floor fix, so 8192 still holds system prompt + RAG chunks
        # comfortably. See num_ctx audit 2026-09-29. 2026-10-03: one value per MODEL
        # (ollama_config.num_ctx_for) so this never flips the runner against Layer 1.
        num_ctx = num_ctx_for(model)
        # False (not just omitted) so this general-chat branch never inherits qwen3.5:9b's
        # own thinking-on-by-default when it's the same model string as REASONING_MODEL.
        think   = False
        label   = f"{agent} / {model}"

    context   = rag_context(agent, text)
    user_text = text + context if context else text

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_text}
        ],
        "stream":  True,
        "think":   think,
        "options": {"num_ctx": num_ctx, "temperature": 0.7}
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    print(f"\n  David [{label} — Tier 1]:\n")
    full = []
    with urllib.request.urlopen(req, timeout=180) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                print(f"\n  [Ollama error] {chunk['error']}\n")
                break
            token = chunk.get("message", {}).get("content", "")
            if token:
                print(token, end="", flush=True)
                full.append(token)
            if chunk.get("done"):
                break
    print("\n")
    if SPEAK:
        speak_async("".join(full))


def execute_deep(text):
    system = (
        "You are David's deep reasoning agent for the operator. "
        "Think carefully, step by step. Show your reasoning process, then give a clear, actionable conclusion."
    )
    body = {
        "model": REASONING_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": text}
        ],
        "stream":  True,
        "think":   True,
        "options": {"num_ctx": num_ctx_for(REASONING_MODEL), "temperature": 0.6}
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    print(f"\n  David [{REASONING_MODEL} — Deep Reasoning / Tier 1]:\n")
    full = []
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                print(f"\n  [Ollama error] {chunk['error']}\n")
                break
            token = chunk.get("message", {}).get("content", "")
            if token:
                print(token, end="", flush=True)
                full.append(token)
            if chunk.get("done"):
                break
    print("\n")
    if SPEAK:
        speak_async("".join(full))


# ── Execution — Tier 2 (Gemini) ──────────────────────────────────────────────

def _gemini_chunks(agent, text, request_id=None, system=None, history=None):
    """
    Generator yielding text chunks from Gemini for `agent`. Enforces the budget
    cap and API-key presence as the single gate shared by the CLI and any other
    caller (e.g. the UI server); records spend + audits once streaming completes.
    Raises RuntimeError/API exceptions on failure — callers catch and fall back.
    `system` overrides the bare persona (callers pass persona+depth+RAG context);
    `history` is a list of {"role": "user"|"assistant", "content": str} turns.
    """
    if not _budget_check(request_id):
        raise RuntimeError("budget cap reached")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    from google import genai as google_genai
    from google.genai import types as genai_types
    client = google_genai.Client(api_key=api_key)
    system = system or build_system_prompt(agent, fallback=None)
    if history:
        contents = [
            {"role": "model" if h["role"] == "assistant" else "user",
             "parts": [{"text": h["content"]}]}
            for h in history
        ] + [{"role": "user", "parts": [{"text": text}]}]
    else:
        contents = text
    in_tok = out_tok = 0
    _t0 = time.monotonic()
    for chunk in client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=4096,
        )
    ):
        if chunk.text:
            yield chunk.text
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            in_tok  = chunk.usage_metadata.prompt_token_count or 0
            out_tok = chunk.usage_metadata.candidates_token_count or 0
    _budget_record("gemini", in_tok, out_tok)
    audit({"tier": 2, "agent": agent, "model": GEMINI_MODEL,
           "in_tok": in_tok, "out_tok": out_tok, "request_id": request_id,
           "duration_ms": int((time.monotonic() - _t0) * 1000)})


def execute_gemini(agent, text, request_id=None, system=None, history=None):
    try:
        print(f"\n  David [{agent} / {GEMINI_MODEL} — Tier 2]:\n")
        full = []
        for tok in _gemini_chunks(agent, text, request_id, system=system, history=history):
            print(tok, end="", flush=True)
            full.append(tok)
        print("\n")
        if SPEAK:
            speak_async("".join(full))
        return True
    except Exception as e:
        print(f"  [Tier 2] {type(e).__name__}: {e} — falling back to Tier 1")
        audit({"action": "tier_fallback", "tier": 2, "agent": agent,
               "error": f"{type(e).__name__}: {e}", "request_id": request_id})
        return False


# ── Execution — Tier 3 (Claude) ──────────────────────────────────────────────

def _claude_chunks(agent, text, request_id=None, system=None, history=None):
    """Generator yielding text chunks from Claude for `agent`. See _gemini_chunks."""
    if not _budget_check(request_id):
        raise RuntimeError("budget cap reached")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    system = system or build_system_prompt(agent, fallback=None)
    messages = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
    messages.append({"role": "user", "content": text})
    _t0 = time.monotonic()
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=system,
        messages=messages
    ) as stream:
        for token in stream.text_stream:
            yield token
        msg     = stream.get_final_message()
        in_tok  = msg.usage.input_tokens
        out_tok = msg.usage.output_tokens
    _budget_record("claude", in_tok, out_tok)
    audit({"tier": 3, "agent": agent, "model": CLAUDE_MODEL,
           "in_tok": in_tok, "out_tok": out_tok, "request_id": request_id,
           "duration_ms": int((time.monotonic() - _t0) * 1000)})


def execute_claude(agent, text, request_id=None, system=None, history=None):
    try:
        print(f"\n  David [{agent} / {CLAUDE_MODEL} — Tier 3]:\n")
        full = []
        for tok in _claude_chunks(agent, text, request_id, system=system, history=history):
            print(tok, end="", flush=True)
            full.append(tok)
        print("\n")
        if SPEAK:
            speak_async("".join(full))
        return True
    except Exception as e:
        print(f"  [Tier 3] {type(e).__name__}: {e} — falling back to Tier 1")
        audit({"action": "tier_fallback", "tier": 3, "agent": agent,
               "error": f"{type(e).__name__}: {e}", "request_id": request_id})
        return False


# ── Tier routing decision ─────────────────────────────────────────────────────

def route_tier(agent, text, needs_reasoning, request_id=None):
    """
    Returns the tier to use (1, 2, or 3) based on agent, request complexity, and DLP.
    Tier 2 = Gemini (large docs / research)
    Tier 3 = Claude (high-stakes / client-facing)
    Falls back toward Tier 1 if DLP blocks, agent is firewalled, or keys missing.
    """
    if agent in FIREWALLED:
        return 1
    if dlp_flag(text):
        print("  [DLP] Confidential content detected — routing local only")
        audit({"action": "dlp_block", "agent": agent, "request_id": request_id})
        return 1
    # Long input → Tier 2 candidate (research/large doc scenario).
    # 2026-07-13: raised from 4,000 to 30,000 tokens — local-first policy.
    # llama3.1:8b/deepseek-r1:14b have 128k+ context; empirically verified
    # this session at num_ctx=40960 (VRAM: 4.96GB@4k -> 7.6GB@40k, safe
    # alongside qwen2.5-coder resident) with exact-fact recall confirmed at
    # ~19.6k actual tokens processed (needle-in-haystack test). 30,000 tokens
    # (~120k chars) now only catches genuinely huge documents — a normal
    # multi-page contract/report stays local; Tier 2 is the size-driven
    # last resort, not the default for "a few pages pasted in".
    if len(text) // 4 > 30000 and agent in TIER2_AGENTS:
        return 2
    # Deep reasoning needed + high-stakes agent → Tier 3
    if needs_reasoning and agent in TIER3_AGENTS:
        return 3
    return 1


# ── Main pipeline ─────────────────────────────────────────────────────────────

def route_and_execute(text):
    text = text.lstrip("\ufeff").strip()
    mode = get_mode()
    # Threaded through every audit() call below so one user turn's layer0/1
    # classification, tier escalation, and fallback events can be joined
    # back together when reading audit_log.jsonl after the fact.
    req_id = uuid.uuid4().hex[:12]

    # Focus / rest window reminder (non-blocking)
    rest_note = check_focus_window()
    if rest_note:
        print(f"\n  {rest_note}\n")

    studio_locked = os.path.exists(STUDIO_LOCK)

    # Mode B: every request goes to deep reasoning regardless of classification
    if mode == "B":
        if studio_locked:
            print("  [Studio Lock] GPU inference blocked — Ableton transport is active.\n"
                  "  Stop the transport or switch to Mode A to use LLM queries.\n")
            return True
        print(f"  [Mode B] Deep Reasoning")
        execute_deep(text)
        audit({"mode": "B", "input": text, "request_id": req_id})
        return True

    # Mode C: utility routes pass through; text queries go to Beat Architect (CPU only)
    PASSTHROUGH_ROUTES = {
        "audio_separate", "audio_analyze", "vision_image",
        "incubation_vault", "mode_switch", "mode_b", "mode_c",
        "chief_of_staff", "gmail", "task_ledger", "david_meta",
    }
    if mode == "C":
        route_c, _ = layer0(text)
        if route_c not in PASSTHROUGH_ROUTES:
            if studio_locked:
                print("  [Studio Lock] All GPU inference suspended — transport is rolling.\n"
                      "  Park ideas with 'park idea: ...' or stop transport first.\n")
                return True
            # (the old print claimed "GPU suspended" right before running
            # llama3.1:8b on the GPU — inference is allowed here precisely
            # because the transport is NOT rolling)
            print("  [Mode C] Studio — transport idle; Beat Architect between takes.\n")
            system = build_system_prompt("beat_architect", fallback=None)
            body = {
                "model": "qwen3.5:9b",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": text}
                ],
                "stream":  True,
                "think":   False,
                "options": {"num_ctx": num_ctx_for("qwen3.5:9b"), "temperature": 0.7}
            }
            req = urllib.request.Request(
                OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
            )
            print(f"  David [beat_architect / qwen3.5:9b -- Studio]:\n")
            with urllib.request.urlopen(req, timeout=120) as r:
                for line in r:
                    line = line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        print(f"\n  [Ollama error] {chunk['error']}\n")
                        break
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        print(token, end="", flush=True)
                    if chunk.get("done"):
                        break
            print("\n")
            audit({"mode": "C", "input": text})
            return True

    if mode == "E":
        route_e, _ = layer0(text)
        if route_e not in PASSTHROUGH_ROUTES:
            if studio_locked:
                print("  [Studio Lock] GPU inference blocked — stop transport before rendering.\n")
                return True
            subprocess.run(["python", str(DAVID_HOME / "comfy_client.py"), "generate", text])
            audit({"mode": "E", "input": text})
            return True

    # Layer 0
    route, arg = layer0(text)
    if route:
        # Studio lock hoisted above route execution: vision/audio/image-gen
        # routes hit the GPU just like Layer 1 inference does, so they must
        # not run while the Ableton transport is active. Non-GPU routes
        # (mode switch, park idea, calendar, ledger) still work.
        if studio_locked and route in GPU_LAYER0_ROUTES:
            print("  [Studio Lock] GPU route blocked — Ableton transport is active.\n"
                  "  Stop the transport or switch to Mode A first.\n")
            audit({"action": "studio_lock_block", "route": route, "request_id": req_id})
            return True
        if route == "incubation_vault":
            filepath = park_idea(text)
            print(f"  [Layer 0] idea parked → {filepath}\n")
            audit({"layer": 0, "input": text, "route": route, "file": filepath, "request_id": req_id})
        elif route == "mode_switch":
            if arg:
                set_mode(arg)
            else:
                print("  Specify a mode: mode A / B / C / D / E\n")
        elif route == "mode_b":
            set_mode("B")
        elif route == "mode_c":
            set_mode("C")
        elif route == "david_meta":
            mode_label = f"{mode}: {MODES[mode]}"
            print(f"\n  David v0.3 online.\n  Mode: {mode_label}\n  Layers: 0 (regex) → 1 (qwen3.5:9b) → Tier 1/2/3\n  DLP: active\n")
            audit({"layer": 0, "route": "david_meta", "request_id": req_id})
        elif route == "chief_of_staff":
            print("  [Calendar] Checking your schedule...\n")
            subprocess.run(["python", str(DAVID_HOME / "chief_of_staff.py"), "today"])
            audit({"layer": 0, "route": "chief_of_staff", "request_id": req_id})
        elif route == "gmail":
            print("  [Gmail] Fetching inbox digest...\n")
            subprocess.run(["python", str(DAVID_HOME / "gmail_digest.py")])
            audit({"layer": 0, "route": "gmail", "request_id": req_id})
        elif route == "task_ledger":
            print("  [Ledger] Opening task ledger...\n")
            subprocess.run(["python", str(DAVID_HOME / "chief_of_staff.py"), "ledger"])
            audit({"layer": 0, "route": "task_ledger", "request_id": req_id})
        elif route == "vision_image":
            img_match = re.search(r'([A-Za-z]:[^\s"\']+\.(?:png|jpg|jpeg|bmp|gif|webp))', text, re.IGNORECASE)
            if not img_match:
                img_match = re.search(r'"([^"]+\.(?:png|jpg|jpeg|bmp|gif|webp))"', text, re.IGNORECASE)
            if img_match:
                img_path = img_match.group(1)
                prompt   = text[:img_match.start()].strip() or text[img_match.end():].strip()
                execute_vision(img_path, prompt)
            else:
                print("  [Mode D] Paste the image path (e.g. C:\\path\\to\\screenshot.png)\n")
            audit({"layer": 0, "route": "vision_image", "request_id": req_id})
        elif route == "audio_separate":
            audio_match = re.search(r'([A-Za-z]:[^\s"\']+\.(?:wav|mp3|flac|aiff|m4a))', text, re.IGNORECASE)
            if audio_match:
                subprocess.run([AUDIO_PYTHON, str(DAVID_HOME / "audio_tools.py"), "separate", audio_match.group(1)])
            else:
                print("  [Audio] Paste the full path to your audio file.\n")
            audit({"layer": 0, "route": "audio_separate", "request_id": req_id})
        elif route == "audio_analyze":
            audio_match = re.search(r'([A-Za-z]:[^\s"\']+\.(?:wav|mp3|flac|aiff|m4a))', text, re.IGNORECASE)
            if audio_match:
                subprocess.run([AUDIO_PYTHON, str(DAVID_HOME / "audio_tools.py"), "analyze", audio_match.group(1)])
            else:
                print("  [Audio] Paste the full path to your audio file.\n")
            audit({"layer": 0, "route": "audio_analyze", "request_id": req_id})
        elif route == "generate_image":
            prompt = re.sub(r"(?i)^(generate|render|draw|create|make)\s+(an?\s+)?(image|picture|photo|illustration|artwork|visual)\s*(of\s*)?", "", text).strip()
            subprocess.run(["python", str(DAVID_HOME / "comfy_client.py"), "generate", prompt or text])
            audit({"layer": 0, "route": "generate_image", "prompt": prompt, "request_id": req_id})
        elif route == "save_memory":
            try:
                import memory as _mem
                summary = _mem.save_episode(label="manual save")
                if summary:
                    print(f"\n  [Memory] Session saved.\n\n{summary}\n")
                    speak_async("Session memory saved.")
                else:
                    print("  [Memory] Nothing to save yet — ask me something first.\n")
            except Exception as e:
                print(f"  [Memory] Error: {e}\n")
            audit({"layer": 0, "route": "save_memory", "request_id": req_id})
        else:
            print(f"  [Layer 0] → {route}\n")
            audit({"layer": 0, "input": text, "route": route, "request_id": req_id})
        return True

    # Layer 1 — blocked when studio lock is active (any Ollama call risks ASIO DPC spike)
    if studio_locked:
        print("  [Studio Lock] GPU inference blocked — Ableton transport is active.\n"
              "  Use 'park idea: ...' to save this thought for later.\n")
        return True

    try:
        print("  [routing...]", end="\r")
        d = layer1(text, mode)
    except Exception as e:
        print(f"  [error] Layer 1 failed: {e}\n")
        return False

    audit({"layer": 1, "input": text, "route": d, "mode": mode, "request_id": req_id})
    try:
        import memory as _mem
        _mem.add_turn(d["agent"], d["domain"], text)
    except Exception:
        pass

    low = " (low confidence)" if d["confidence"] < 0.6 else ""
    print(f"  [Layer 1] {d['agent']} / {d['domain']}  confidence: {d['confidence']:.2f}{low}")

    if d["confidence"] < 0.6 and d["agent"] != "analyst":
        print("  Could you say more? I want to route this correctly.\n")
        return True

    # Beat Architect: try direct Ableton command before LLM
    if d["agent"] == "beat_architect":
        result = try_ableton_command(text)
        if result:
            print(f"\n  [Ableton] {result}\n")
            audit({"action": "ableton_command", "input": text, "result": result, "request_id": req_id})
            return True

    # Tier routing
    tier = route_tier(d["agent"], text, d["needs_reasoning"], req_id)

    cloud_system = cloud_history = None
    if tier in (2, 3):
        # Escalation payload gets the same context the local model would:
        # persona (+depth for R1 agents), RAG context, and recent session turns —
        # not just the bare message. The static persona is trusted; DLP re-scans
        # everything user-derived (RAG context + history) since route_tier only
        # saw the bare message.
        cloud_system = build_system_prompt(d["agent"], fallback=None)
        ctx = rag_context(d["agent"], text)
        if ctx:
            cloud_system += "\n\n[KNOWLEDGE]" + ctx
        cloud_history = []
        try:
            import memory as _mem
            cloud_history = [
                {"role": "user", "content": t["text"]}
                for t in _mem._session_turns[:-1][-6:]
            ]
        except Exception:
            pass
        egress = "\n".join([ctx or ""] + [h["content"] for h in cloud_history] + [text])
        if dlp_flag(egress):
            print("  [DLP] Sensitive content in escalation payload — routing local only")
            audit({"action": "dlp_block_escalation", "tier": tier,
                   "agent": d["agent"], "request_id": req_id})
            tier = 1

    if tier == 2:
        reason = TIER2_AGENTS.get(d["agent"], "large document")
        print(f"  [Tier 2] escalating to Gemini ({reason})")
        if not execute_gemini(d["agent"], text, req_id, system=cloud_system, history=cloud_history):
            execute(d["domain"], d["agent"], text)
    elif tier == 3:
        reason = TIER3_AGENTS.get(d["agent"], "complex request")
        print(f"  [Tier 3] escalating to Claude ({reason})")
        if not execute_claude(d["agent"], text, req_id, system=cloud_system, history=cloud_history):
            if not execute_gemini(d["agent"], text, req_id, system=cloud_system, history=cloud_history):
                execute(d["domain"], d["agent"], text)
    else:
        # Tier 1 — R1 agents always get their depth-file + persona via execute()
        # regardless of needs_reasoning; execute_deep() (generic, no persona) is
        # only the fallback for non-R1 agents that still need deep reasoning.
        if d["agent"] in R1_AGENTS:
            execute(d["domain"], d["agent"], text)
        elif d["needs_reasoning"]:
            print("  [deep reasoning — loading deepseek-r1:14b...]")
            execute_deep(text)
        else:
            execute(d["domain"], d["agent"], text)

    return True


def main():
    mode = get_mode()
    voice_label = "  Voice: ON (XTTS-v2)" if SPEAK else ""
    print(f"David v0.3 online.  Mode {mode}: {MODES[mode]}{voice_label}\nType a request or 'mode A/B/C/D/E' to switch. Ctrl+C to quit.\n")
    while True:
        try:
            text = input("> ").strip()
        except KeyboardInterrupt:
            try:
                import memory as _mem
                if _mem._session_turns:
                    print("\n  [Memory] Saving session before exit...")
                    _mem.save_episode(label="session end")
            except Exception:
                pass
            print("\nShutting down.")
            break
        if not text:
            continue
        route_and_execute(text)


if __name__ == "__main__":
    main()
