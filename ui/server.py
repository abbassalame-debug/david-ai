# David UI — FastAPI server
# Run: python -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
# or:  launch_david.bat

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import COMFYUI_DIR, DAVID_DATA, DAVID_HOME, DAVID_VAULTS, OLLAMA_HOST, load_policy
import sys, os, json, subprocess, asyncio, time, datetime, re, psutil, socket, sqlite3, threading, tempfile, uuid
import portalocker
sys.path.insert(0, str(DAVID_HOME))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.websockets import WebSocketState
import urllib.request

app = FastAPI(title="David UI")
# DNS-rebinding guard: a malicious page can re-resolve its own hostname to
# 127.0.0.1, but the browser still sends that hostname in the Host header.
# Rejecting any Host that isn't our own localhost names blocks rebinding on
# every route (GET included, where the Origin middleware below never runs) and
# on the WebSocket handshake. Applies to http + websocket scopes.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
os.makedirs(str(DAVID_DATA / "generated"), exist_ok=True)
app.mount("/generated", StaticFiles(directory=str(DAVID_DATA / "generated")), name="generated")
FRONTEND_DIST = str(DAVID_HOME / "ui" / "frontend" / "dist")
# The built HUD (ui/frontend/dist) is optional: a fresh clone has not run `npm run build` yet, and the API must still start.
if os.path.isdir(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="frontend-assets")

# Binding to 127.0.0.1 keeps other machines out, but not other browser tabs on
# this one — any page the user has open can still fire a cross-origin POST at
# localhost. Block state-changing requests whose Origin isn't this app itself
# (dev Vite server included); requests with no Origin header (curl, scripts,
# the desktop launcher) are non-browser callers and pass through.
_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:5173", "http://localhost:5173",
}

@app.middleware("http")
async def _origin_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in _ALLOWED_ORIGINS:
            return JSONResponse({"ok": False, "error": "Origin not allowed"}, status_code=403)
    return await call_next(request)

OLLAMA   = OLLAMA_HOST + "/api/chat"
MODE_FILE= str(DAVID_DATA / "mode.state")
LOG_FILE = str(DAVID_DATA / "audit_log.jsonl")
VAULTS   = str(DAVID_VAULTS)
WAR_ROOMS= str(DAVID_HOME / "war-rooms")
BUDGET   = str(DAVID_DATA / "budget.json")
TASKS_DB        = str(DAVID_DATA / "tasks.db")
BRIEFING_STATE  = str(DAVID_DATA / "briefing_state.json")
_RAG_OK         = os.path.isdir(str(DAVID_DATA / "lancedb"))
# turbo_ingest.py stops Ollama for the duration of a run — Process.terminate() on
# Windows is a hard TerminateProcess() that bypasses SIGTERM/KeyboardInterrupt, so
# it never reaches the cleanup that restarts Ollama. This sentinel lets the kill
# endpoints ask the script to stop itself gracefully instead.
STOP_INGEST_SENTINEL = os.path.join(tempfile.gettempdir(), "david_stop_ingest.flag")
_RENDER_PATTERN = re.compile(
    r'\b(render|generate|create|draw|make|design|image|picture|visual|artwork|photo|illustration|concept)\b',
    re.I
)
# Commands must START the message (an optional politeness prefix is tolerated).
# Unanchored, these fired mid-sentence: "what's another word for happy" matched
# the bare app-name branch ("word"), "when I open photoshop it crashes" launched
# Photoshop, "should I clear chat history?" wiped it.
_L0_PREFIX = r'^(?:(?:please|hey|ok(?:ay)?|david|can you|could you)[\s,]+)*'
_L0_DESIGN = re.compile(
    _L0_PREFIX +
    r'(?:'
    r'(?:create|make|design|generate|write|build|open and create|open and make)\b'
    r'.{0,60}'
    r'\b(?:word|excel|powerpoint|ableton(?:\s+live)?)\b'
    r'|(?:word|excel|powerpoint|ableton(?:\s+live)?)\b'
    r'.{0,60}'
    r'\b(?:create|make|design|generate|write|build)\b'
    r'|(?:word|excel|powerpoint)\b'
    r'.{0,40}'
    r'\b(?:document|file|sheet|presentation|design|graphic|logo|layout|project|template)\b'
    r')',
    re.I
)
# 2026-10-05: the Adobe keyword shortcut ("create a poster in photoshop" -> an LLM-written script run straight in the app, no
# preview, no agent) was removed. Adobe work is now done by the creative_director / uiux_engineer agents through the template
# tools in adobe_template_tools.py (plan first, a copy only, a preview, never an original). This shortcut keeps Office and Ableton.
_DESIGN_APP_MAP = {
    "word":        ("office", "word"),
    "excel":       ("office", "excel"),
    "powerpoint":  ("office", "powerpoint"),
    "ableton":     ("none",   "ableton"),
}

def _parse_design_request(msg: str):
    ml = msg.lower()
    # "powerpoint" before "word" so "word" is not matched inside it
    _ORDER = [
        ("powerpoint",     ("office", "powerpoint")),
        ("excel",          ("office", "excel")),
        ("word",           ("office", "word")),
        ("ableton",        ("none",   "ableton")),
    ]
    for key, val in _ORDER:
        if key in ml:
            return val
    return None

_L0_WAR_ROOM = re.compile(
    _L0_PREFIX +
    r'(?:'
    r'(?:take this to|bring this to|escalate to|war.?room this|spin up|route (?:this )?to)\b.{0,40}'
    r'\b(?:executive|build|creative|design|calibration|sound|render)\b'
    r'|(?:enter|open|join)\s+(?:the\s+)?'
    r'(?:executive|build|creative|design.?review|calibration|sound.?story|render)\s+(?:room|studio)\b'
    r')',
    re.I
)
_ROOM_KEYWORDS = [
    ("executive",   "executive-room"),
    ("build",       "build-room"),
    ("creative",    "creative-studio"),
    ("design",      "design-review-room"),
    ("render",      "render-room"),
    ("sound",       "sound-story-room"),
    ("calibration", "calibration-room"),
]

def _parse_room_request(msg: str) -> str:
    ml = msg.lower()
    for kw, room in _ROOM_KEYWORDS:
        if kw in ml:
            return room
    return "executive-room"

_SEARCH_FORCE = re.compile(
    r'\b(search|find|look up|pull|fetch|get|show|check|give me|tell me)\b.{0,50}'
    r'\b(news|latest|update|scores?|results?|happening|standings|winner|loser|match)\b'
    r'|\b(what.s|what is|what are).{0,40}(latest|happening|current|today|scores?|results?|standings)\b'
    r'|\b(latest|recent|current|breaking)\b.{0,30}\b(news|update|scores?|results?)\b'
    r'|\b(scores?|results?|standings|who won|who lost|who scored)\b.{0,40}(today|yesterday|latest|current|now|recent)\b',
    re.I
)

def _tasks_init():
    conn = sqlite3.connect(TASKS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            description  TEXT NOT NULL,
            agent        TEXT DEFAULT 'analyst',
            status       TEXT DEFAULT 'pending',
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            started_at   TEXT,
            completed_at TEXT,
            result       TEXT
        )
    """)
    conn.commit()
    conn.close()

_tasks_init()


def _briefing_due() -> bool:
    today = datetime.date.today().isoformat()
    try:
        return json.load(open(BRIEFING_STATE)).get("date") != today
    except Exception:
        return True


def _mark_briefing_sent():
    with open(BRIEFING_STATE, "w") as f:
        json.dump({"date": datetime.date.today().isoformat()}, f)


# Single source of truth for modes/agents/prompts/models lives in david_router.py —
# importing rather than duplicating is what keeps CLI and HUD routing in sync.
from david_router import (
    MODES, DOMAIN_MODELS, AGENT_MODEL_OVERRIDES, REASONING_MODEL, VISION_MODEL, AGENTS,
    R1_AGENTS, FIREWALLED, TIER2_AGENTS, TIER3_AGENTS,
    GEMINI_MODEL, CLAUDE_MODEL, STUDIO_LOCK, audit as _router_audit,
    build_system_prompt, layer0 as _router_layer0, layer1 as _router_layer1,
    route_tier, rag_context, park_idea, _gemini_chunks, _claude_chunks,
    dlp_flag, set_mode as _router_set_mode, redact_entry as _redact_audit_entry,
)
import david_router as _router_mod   # the audit log path is read from here at call time (tests redirect it)
from ollama_config import num_ctx_for
import memory as _memory
import chat_attachments
import transcripts as _transcripts   # the chat-log writer (P1-7); "transcripts" is a local list name in the war-room code

try:
    import code_guard as _code_guard
    _CODE_GUARD_OK = True
except Exception:
    _CODE_GUARD_OK = False

WAR_ROOM_NAMES = [
    "executive-room","build-room","creative-studio",
    "design-review-room","render-room","sound-story-room","calibration-room"
]

ROOM_AGENTS = {
    "executive-room":    ["cfo", "executive_advisor", "venture_architect", "legal_counsel"],
    "build-room":        ["developer", "prompt_architect", "uiux_engineer"],
    "creative-studio":   ["creative_director", "brand_audience", "lore_keeper"],
    "design-review-room":["uiux_engineer", "creative_director"],
    "render-room":       ["prompt_architect"],
    "sound-story-room":  ["beat_architect", "acoustician", "lore_keeper"],
    "calibration-room":  ["analyst", "performance_coach", "chief_of_staff"],
}

def _global_behavior() -> str:
    # A function, not a constant — the date must be computed fresh per request,
    # not baked in once at import time.
    now = datetime.datetime.now()
    return (
        "\n\nCRITICAL — you are David, Demo User's AI running locally on his Windows machine, talking to him "
        "through a text chat interface that renders full Markdown (headings, tables, bullet/numbered "
        "lists, code blocks, bold/italic)."
        f"\n- The real current date and time is {now.strftime('%A, %B %d, %Y, %I:%M %p')}. "
        "Always use this for \"today\"/\"now\"/date questions — never guess from training data, "
        "which is stale relative to the real date above."
        "\n- Your capabilities include: local image generation (Stable Diffusion XL via ComfyUI on the RTX 3080), "
        "voice input/output (Web Speech API), web search, RAG knowledge base, code execution, music production tools, "
        "file access, and multi-agent routing. Everything runs locally — nothing is sent to external servers."
        "\n- Match response length and structure to what's actually being asked: a greeting or quick factual "
        "question gets 1-2 sentences; a request for data, a list, a comparison, or a comprehensive answer "
        "(e.g. \"give me all the results\", \"summarize everything\") should be as complete and well-"
        "structured as the data supports — use a Markdown table for tabular data (scores, prices, "
        "schedules), bullet/numbered lists for multi-item answers, and code blocks for code. Don't "
        "artificially truncate a real answer to keep it short."
        "\n- Never claim you lack audio, voice, image, search, or any capability listed above."
        "\n- Never fabricate schedules, data, or facts you don't actually have — say you don't have that info instead."
        "\n- Sound like a knowledgeable person talking to Demo User directly, not a generic corporate assistant."
    )


async def _ollama_stream(payload: bytes, timeout: int = 120):
    """Async generator that streams Ollama chunks without blocking the event loop."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _reader():
        try:
            req = urllib.request.Request(
                OLLAMA, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for line in resp:
                    loop.call_soon_threadsafe(q.put_nowait, line)
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_reader, daemon=True).start()
    while True:
        item = await q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        try:
            chunk = json.loads(item.decode())
        except json.JSONDecodeError:
            continue
        # Ollama reports failures (model missing, OOM) as an in-stream
        # {"error": ...} chunk with HTTP 200 — swallowing it yields a silent
        # empty response. Raise so consumers' existing error paths surface it.
        if chunk.get("error"):
            raise RuntimeError(f"Ollama: {chunk['error']}")
        yield chunk


async def _cloud_stream(chunks_fn, agent: str, text: str, request_id: str = None,
                        system: str = None, history: list = None):
    """
    Bridge a blocking generator (david_router._gemini_chunks / _claude_chunks) onto
    the asyncio loop, the same way _ollama_stream bridges Ollama's HTTP stream.
    Budget check, API-key check, spend recording, and audit logging all happen
    inside chunks_fn — this only handles the thread-to-async handoff.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _reader():
        try:
            for tok in chunks_fn(agent, text, request_id, system=system, history=history):
                loop.call_soon_threadsafe(q.put_nowait, tok)
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_reader, daemon=True).start()
    while True:
        item = await q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# Local-inference failures that are worth a failover attempt rather than a
# bare error: VRAM/OOM, a model Ollama can't load, and load timeouts. These
# surface as RuntimeError from _ollama_stream since the F10 fix.
_RECOVERABLE_ERR = ("memory", "oom", "cuda", "vram", "not found",
                    "unable to load", "timed out", "timeout", "connection refused")


def _cloud_failover_eligible(agent: str, egress_text: str) -> bool:
    """Cloud failover obeys exactly the tier-escalation rules: never for
    firewalled agents, only for agents with an escalation mapping, and never
    for a DLP-flagged payload. Everything else falls back locally."""
    if agent in FIREWALLED:
        return False
    if agent not in TIER2_AGENTS and agent not in TIER3_AGENTS:
        return False
    return not dlp_flag(egress_text)


_EMPTY_RESPONSE_NOTICE = (
    "_(No visible answer was generated for this message — the model may have "
    "spent its turn reasoning without producing a final response. Try "
    "rephrasing, or a shorter question.)_"
)


def _guard_empty_response(full_response: str, error_sent: bool) -> str | None:
    """Returns a substitute chat message when the model produced no visible
    output and no error was already surfaced this turn, else None.

    Observed live with deepseek-r1:14b, and reproduced again 2026-09-29 with
    qwen3.5:9b (a runaway thinking trace spiraled on an identity-framing
    confusion, hit the context limit, and produced zero content — see the
    num_ctx/think audit) — any thinking-capable model can spend its entire
    turn in "thinking" content and stream zero "content" tokens, ending the
    turn cleanly with no error. The client then marks a blank bubble
    "complete" with nothing to explain it. `error_sent` guards against
    double-messaging when failover already reported an error and the client
    closed the socket on it.
    """
    if error_sent or full_response.strip():
        return None
    return _EMPTY_RESPONSE_NOTICE


async def _local_failover(ws, agent, messages, msg, ctx_blocks, failed_model, req_id, exc):
    """Hybrid failover when local inference fails (VRAM OOM, missing model,
    load timeout). Order: (1) Tier 2 cloud through the existing audited
    escalation path — same DLP / firewall / budget gates, no new providers —
    then (2) the small resident local model. Returns (handled, text, model)."""
    write_audit_log({"action": "local_failover", "failed_model": failed_model,
                     "agent": agent, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                     "request_id": req_id})
    egress = "\n".join(list(ctx_blocks) + [m["content"] for m in messages[1:-1]] + [msg])
    if _cloud_failover_eligible(agent, egress):
        try:
            await ws.send_json({"type": "token", "token":
                "⚡ Local model unavailable — continuing on Tier 2 cloud...\n\n"})
            full = ""
            async for tok in _cloud_stream(_gemini_chunks, agent, messages[-1]["content"],
                                           req_id, system=messages[0]["content"],
                                           history=messages[1:-1]):
                full += tok
                await ws.send_json({"type": "token", "token": tok})
            return True, full, GEMINI_MODEL
        except Exception as cloud_err:
            write_audit_log({"action": "failover_cloud_failed", "agent": agent,
                             "error": f"{type(cloud_err).__name__}: {cloud_err}",
                             "request_id": req_id})
    fallback = os.environ.get("DAVID_FAILOVER_MODEL", "qwen3.5:9b")     # the small resident model to retry on
    if fallback == failed_model:
        return False, "", failed_model
    try:
        await ws.send_json({"type": "token", "token": f"⚡ Retrying on {fallback}...\n\n"})
        payload = json.dumps({"model": fallback, "messages": messages,
                              "stream": True, "options": {"num_ctx": num_ctx_for(fallback)}}).encode()
        full = ""
        async for chunk in _ollama_stream(payload):
            token = chunk.get("message", {}).get("content", "")
            if token:
                full += token
                await ws.send_json({"type": "token", "token": token})
            if chunk.get("done"):
                break
        return True, full, fallback
    except Exception as retry_err:
        write_audit_log({"action": "failover_local_failed", "agent": agent,
                         "error": f"{type(retry_err).__name__}: {retry_err}",
                         "request_id": req_id})
        return False, "", failed_model


async def _guard_code_response(ws, messages, model, full_response, req_id):
    """Static-validate model-written Python (code_guard) and run ONE repair
    pass when it fails — the 14B writes plausible code with hallucinated APIs
    (signal.start_loop_in_executor, asyncio loop= params). Returns the
    possibly-extended response text."""
    issues = _code_guard.review(full_response)
    if not issues:
        return full_response
    write_audit_log({"action": "code_guard", "model": model,
                     "issues": issues[:10], "request_id": req_id})
    bullet = "\n".join(f"- {i}" for i in issues)
    await ws.send_json({"type": "token", "token":
        f"\n\n🔧 **Code validation found {len(issues)} issue(s)** — regenerating:\n{bullet}\n\n"})
    repair_messages = messages + [
        {"role": "assistant", "content": full_response},
        {"role": "user", "content":
            "Your code failed static validation with these exact errors:\n"
            f"{bullet}\n\n"
            "Rewrite the code fixing ALL of the listed issues. Do not invent "
            "APIs — if you are unsure a function or parameter exists, use a "
            "documented alternative. Output the corrected code block(s) only."},
    ]
    # Deliberately left at 16384 (not unified to 8192 like the chat call sites):
    # a repair pass needs the full original code + explanation + issue list in
    # context, and it's a rare path (only fires when code_guard finds issues),
    # not a per-turn hot path. See num_ctx audit 2026-09-29.
    payload = json.dumps({"model": model, "messages": repair_messages,
                          "stream": True, "options": {"num_ctx": num_ctx_for(model)}}).encode()
    repaired = ""
    try:
        async for chunk in _ollama_stream(payload):
            token = chunk.get("message", {}).get("content", "")
            if token:
                repaired += token
                await ws.send_json({"type": "token", "token": token})
            if chunk.get("done"):
                break
    except Exception:
        repaired = ""
    if not repaired:
        return full_response
    remaining = _code_guard.review(repaired)
    if remaining:
        note = ("\n\n⚠️ The corrected code still fails validation ("
                + "; ".join(remaining[:3])
                + ") — treat it as a draft and verify before running.")
        await ws.send_json({"type": "token", "token": note})
        repaired += note
        write_audit_log({"action": "code_guard_unresolved", "model": model,
                         "issues": remaining[:10], "request_id": req_id})
    return full_response + "\n\n" + repaired


def get_mode():
    try:
        with portalocker.Lock(MODE_FILE, mode="r", timeout=5) as f:
            return f.read().strip()
    except Exception:
        return "A"


def set_mode(m):
    # Write mode.state synchronously (callers read it back immediately), then run
    # the router's set_mode in a background thread for VRAM enforcement — a HUD
    # mode switch must evict/pre-warm models like the CLI does, but _ollama_evict
    # blocks up to ~20 s on /api/ps and must not stall the event loop.
    with portalocker.Lock(MODE_FILE, mode="w", timeout=5) as f:
        f.write(m)
    threading.Thread(target=lambda: _router_set_mode(m), daemon=True).start()


_ASKS_OTHER_LANGUAGE = re.compile(
    r"(?i)\b(?:in|into|to)\s+(?:english|french|spanish|german)\b|بالإنجليزية|بالانجليزية|بالفرنسية|بالإنكليزية")


def language_hint(msg: str) -> str:
    """One line appended next to an Arabic question telling the model to answer in formal Arabic.
    Measured 2026-10-03: with identical input and tool call, 1 of 6 answers to an Arabic question
    came back in English; an instruction next to the question is what small models obey."""
    letters = [c for c in msg if c.isalpha()]
    if not letters or _ASKS_OTHER_LANGUAGE.search(msg):
        return ""
    if sum("؀" <= c <= "ۿ" for c in letters) / len(letters) < 0.4:
        return ""
    return "(Reply in Modern Standard Arabic - the same language as this message.)"


def write_audit_log(entry: dict) -> None:
    # Single audit writer lives in david_router.audit (locked, mandatory fields:
    # ts / surface / request_id / status / duration_ms) — this wrapper only tags
    # the surface so CLI and HUD lines are distinguishable in the shared file.
    entry.setdefault("surface", "hud")
    _router_audit(entry)


def pick_model(mode, domain="general", agent=None):
    # Mode B is "all requests → deepseek-r1" (MODES["B"]) — the old domain list
    # ("business", "creative", "legal") included two domains that don't exist and
    # silently left technical/music/general on small models in deep-reasoning mode.
    if mode == "B":
        return REASONING_MODEL
    # Bug found 2026-09-24: this function is the ONLY model-selection path real
    # HUD/web traffic goes through, but AGENT_MODEL_OVERRIDES (the qwen-ft pilot,
    # scoped to the developer agent) was only ever checked in david_router.py's
    # execute() — a separate, CLI-only function. The pilot was validated and
    # marked live, but never actually served a single real production request.
    if agent:
        override = AGENT_MODEL_OVERRIDES.get(agent)
        if override:
            return override
    return DOMAIN_MODELS.get(domain, "qwen3.5:9b")


# ─── Layer 0: regex pre-router (no LLM invoked) ───────────────────────────────

# Anchored like _L0_DESIGN above: "the failure mode a system has…" must not
# switch modes, and "switch to a different tactic" matched ([ABCDEabcde]) as
# a bare "a" — the word "mode" is now required and the command must start the
# message.
_L0_MODE = re.compile(
    _L0_PREFIX + r'(?:mode\s*([ABCDEabcde])|switch\s+(?:to\s+)?mode\s*([ABCDEabcde]))\b', re.I
)
_L0_CLEAR = re.compile(
    _L0_PREFIX + r'(?:clear|reset)\s+(?:the\s+)?(?:chat|history|conversation)\b', re.I
)
_L0_OPEN_APP = re.compile(
    _L0_PREFIX +
    r'(?:open|launch|start|run|load)\s+(?:up\s+)?'
    r'(photoshop|illustrator|indesign|after\s*effects?|premiere(?:\s+pro)?|audition|'
    r'word|excel|powerpoint|ableton(?:\s+live)?)\b',
    re.I
)

_APP_EXES = {
    "photoshop":    r"C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe",
    "illustrator":  r"C:\Program Files\Adobe\Adobe Illustrator 2026\Support Files\Contents\Windows\Illustrator.exe",
    "indesign":     r"C:\Program Files\Adobe\Adobe InDesign 2026\InDesign.exe",
    "aftereffects": r"C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\AfterFX.exe",
    "premiere":     r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe",
    "audition":     r"C:\Program Files\Adobe\Adobe Audition 2026\Adobe Audition.exe",
    "word":         r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "excel":        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
    "powerpoint":   r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
    "ableton":      r"C:\ProgramData\Ableton\Live 12 Suite\Program\Ableton Live 12 Suite.exe",
}

def _resolve_app_key(raw: str) -> str:
    n = re.sub(r'\s+', '', raw.lower())
    return {"aftereffect": "aftereffects", "aftereffects": "aftereffects",
            "premierepro": "premiere", "premiere": "premiere",
            "abletonlive": "ableton", "ableton": "ableton"}.get(n, n)

def layer0_intercept(msg: str):
    """
    Returns (handled, token_text, log_skill) or (False, None, None).
    Executes deterministically — zero LLM calls.
    """
    m = _L0_MODE.search(msg)
    if m:
        letter = (m.group(1) or m.group(2)).upper()
        if letter in MODES:
            set_mode(letter)
            return True, f"✓ Mode {letter} — {MODES[letter]}", "mode_switch"
    if _L0_CLEAR.search(msg):
        return True, "✓ Chat history cleared.", "clear_history"
    m = _L0_OPEN_APP.search(msg)
    if m:
        key = _resolve_app_key(m.group(1))
        exe = _APP_EXES.get(key)
        label = re.sub(r'\s+', ' ', m.group(1)).title()
        if exe and os.path.exists(exe):
            subprocess.Popen([exe])
            return True, f"✓ Opening {label}.", "open_app"
        return True, f"Can't find {label} — it may not be installed.", "open_app_fail"

    # Three CLI-only routes with no HUD equivalent yet — everything else david_router's
    # layer0 covers (calendar/email/ledger/audio/vision/image-gen) already has a better,
    # UI-native path elsewhere in this file, so we don't blanket-import the whole table.
    router_route, _arg = _router_layer0(msg)
    if router_route == "incubation_vault":
        filepath = park_idea(msg)
        return True, f"✓ Idea parked → {os.path.basename(filepath)}", "park_idea"
    if router_route == "david_meta":
        m = get_mode()
        return True, (
            f"David online. Mode {m}: {MODES.get(m, m)}. "
            "Layers: 0 (regex) → 1 (qwen3.5:9b) → Tier 1/2/3. DLP: active."
        ), "david_meta"
    if router_route == "save_memory":
        try:
            summary = _memory.save_episode(label="manual save (HUD)")
        except Exception as e:
            return True, f"Memory save failed: {e}", "save_memory"
        if summary:
            return True, f"✓ Session saved.\n\n{summary}", "save_memory"
        return True, "Nothing to save yet — ask me something first.", "save_memory"

    return False, None, None


# ─── Layer 1: resident router — delegates to david_router.layer1 ──────────────
# (schema-constrained JSON + routing examples + mode hints; shared with the CLI
# so both surfaces classify identically instead of drifting apart)

_ROUTE_DEFAULT = {
    "intent": "chat", "domain": "general", "agent": "analyst",
    "war_room": None, "tier": 1, "needs_reasoning": False, "confidence": 0.5,
}

# Domain groupings, exactly matching david_router.py's Layer 1 classifier
# system prompt (the authoritative agent→domain assignments) — used to
# resolve domain when force_agent skips classification below.
AGENT_DOMAIN = {
    "developer": "technical", "uiux_engineer": "technical", "prompt_architect": "technical",
    "beat_architect": "music", "acoustician": "music",
    "executive_advisor": "business", "cfo": "business", "venture_architect": "business",
    "legal_counsel": "business", "brand_audience": "business", "creative_director": "business",
    "lore_keeper": "lore",
    "linguist": "research", "language_specialist": "research",
    "performance_coach": "personal",
    "analyst": "general",
    "chief_of_staff": "general",
}


FALLBACK_AGENT = "analyst"


def fallback_route(reason: str) -> dict:
    """Ordinary-chat fallback when routing fails or names no valid agent (audit finding S-05, 2026-10-18).
    The turn is answered as FALLBACK_AGENT, but `fallback: True` strips it of every privilege for that turn:
    no tool decision, no vault / memory / fact retrieval, Tier 1 only. It never inherits the identity the client
    asked for, and the event is audited without the message."""
    write_audit_log({"action": "routing_fallback", "reason": str(reason)[:60], "fallback_agent": FALLBACK_AGENT})
    return {**_ROUTE_DEFAULT, "agent": FALLBACK_AGENT, "domain": "general", "needs_reasoning": False, "fallback": True}


async def layer1_route(msg: str, agent: str, domain: str, mode: str = "A") -> dict:
    try:
        loop = asyncio.get_running_loop()
        d = await loop.run_in_executor(None, lambda: _router_layer1(msg, mode))
        route = {**_ROUTE_DEFAULT, **d}
        route["confidence"] = float(route.get("confidence", 0.5))
        route["needs_reasoning"] = bool(route.get("needs_reasoning", False))
        if route["agent"] not in AGENTS:
            return fallback_route("router returned an unknown agent")
        return route
    except Exception as e:
        return fallback_route(f"router error: {type(e).__name__}")


def get_metrics():
    cpu  = psutil.cpu_percent(interval=0.2)
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    net  = psutil.net_io_counters()
    gpu_pct   = -1
    vram_used = -1
    vram_total= -1
    gpu_temp  = -1
    gpu_power = -1
    cpu_temp  = -1
    cpu_power = -1
    # Read from cpu_sensor_daemon.ps1 output (runs as admin, polls LHM DLL every 2s)
    try:
        with open(str(DAVID_DATA / "cpu_sensors.json"), "r") as f:
            sensors = json.loads(f.read())
        cpu_temp  = round(float(sensors.get("cpu_temp",  -1)), 1)
        cpu_power = round(float(sensors.get("cpu_power", -1)), 1)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            parts      = r.stdout.strip().split(",")
            gpu_pct    = float(parts[0].strip())
            vram_used  = float(parts[1].strip())
            vram_total = float(parts[2].strip())
            gpu_temp   = float(parts[3].strip())
            gpu_power  = round(float(parts[4].strip()), 1)
    except Exception:
        pass
    vram_pct = round(vram_used / vram_total * 100, 1) if vram_total > 0 else -1
    return {
        "cpu":        round(cpu, 1),
        "ram_pct":    round(mem.percent, 1),
        "ram_used":   round(mem.used / 1024**3, 1),
        "ram_total":  round(mem.total / 1024**3, 1),
        "disk_pct":   round(disk.percent, 1),
        "gpu_pct":    round(gpu_pct, 1),
        "gpu_temp":   round(gpu_temp, 1),
        "gpu_power":  gpu_power,
        "vram_used":  round(vram_used / 1024, 1) if vram_used >= 0 else -1,
        "vram_total": round(vram_total / 1024, 1) if vram_total > 0 else -1,
        "vram_pct":   vram_pct,
        "net_sent":   round(net.bytes_sent / 1024**2, 1),
        "net_recv":   round(net.bytes_recv / 1024**2, 1),
        "cpu_temp":   cpu_temp,
        "cpu_power":  cpu_power,
    }


async def check_ollama():
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=1)
        )
        return True
    except Exception:
        return False


# SDXL startup under VRAM pressure (LLM + embedder resident on the 16GB 3080)
# routinely exceeds the old fixed 30 s window — the queue was healthy but the
# UI showed a false "failed to start". Wait adaptively up to this budget.
COMFY_STARTUP_BUDGET_S = int(os.environ.get("DAVID_COMFY_BUDGET_S", "180"))
_comfy_started_at = None   # monotonic ts of a launch in flight (None = not launching)


async def _ensure_comfy_running(progress=None, budget: int = None) -> bool:
    """Launch ComfyUI if it isn't already up and wait adaptively for readiness.

    Polls with exponential backoff (1 s → 8 s cap) up to `budget` seconds and
    reports elapsed time through `progress` (async callable taking a string)
    every ~15 s, so slow VRAM model swaps read as "warming up", not "offline".
    """
    global _comfy_started_at
    from comfy_client import is_comfy_running
    loop = asyncio.get_running_loop()
    if await loop.run_in_executor(None, is_comfy_running):
        _comfy_started_at = None
        return True
    if _comfy_started_at is None:
        subprocess.Popen(
            [sys.executable, str(COMFYUI_DIR / "main.py"), "--listen", "--lowvram"],
            cwd=str(COMFYUI_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _comfy_started_at = time.monotonic()
    budget = budget if budget is not None else COMFY_STARTUP_BUDGET_S
    t0, delay, last_report = _comfy_started_at, 1.0, 0.0
    while (time.monotonic() - t0) < budget:
        await asyncio.sleep(delay)
        if await loop.run_in_executor(None, is_comfy_running):
            _comfy_started_at = None
            return True
        delay = min(delay * 1.6, 8.0)
        elapsed = time.monotonic() - t0
        if progress and (elapsed - last_report) >= 15:
            last_report = elapsed
            try:
                await progress(
                    f"⏳ ComfyUI still warming up ({int(elapsed)}s — SDXL model "
                    f"swap under VRAM pressure is normal on this GPU)...\n"
                )
            except Exception:
                pass
    _comfy_started_at = None
    return False


AUDIT_TAIL_MAX = 200                 # entries per /api/audit call
_AUDIT_TAIL_BYTES = 512 * 1024       # never read more than this from the end of the file


def get_audit_tail(n=20):
    """The last n audit entries, each passed through david_router.redact_entry, so lines written before the
    redaction change (raw text) are never served as they are. Reads only the end of the file; a malformed or
    non-object line is skipped and counted, never echoed. Returns (entries, skipped)."""
    n = max(1, min(int(n), AUDIT_TAIL_MAX))
    path = _router_mod.LOG
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - _AUDIT_TAIL_BYTES)
            f.seek(start)
            raw = f.read()
    except OSError:
        return [], 0
    lines = raw.split(b"\n")
    if start > 0:
        lines = lines[1:]                # the first line is probably cut in half
    entries, skipped = [], 0
    for line in lines[-(n + 50):]:
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            skipped += 1
            continue
        safe = _redact_audit_entry(e)
        if safe is None:
            skipped += 1
            continue
        entries.append(safe)
    return entries[-n:], skipped


_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_local_client(request) -> bool:
    """True when the TCP peer is this machine (or Starlette's in-process TestClient). The HUD is meant for
    127.0.0.1; this keeps audit data private even if someone starts uvicorn on 0.0.0.0."""
    host = getattr(getattr(request, "client", None), "host", None)
    return host in _LOCAL_CLIENTS


def list_vault(vault_name):
    path = os.path.join(VAULTS, vault_name)
    files = []
    if os.path.isdir(path):
        for root, dirs, fnames in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in fnames:
                if fn.endswith(".md"):
                    rel = os.path.relpath(os.path.join(root, fn), path)
                    files.append(rel.replace("\\", "/"))
    return files


def read_vault_note(vault_name, note_path):
    base = os.path.realpath(VAULTS)
    full = os.path.realpath(os.path.join(VAULTS, vault_name, note_path))
    if not full.startswith(base + os.sep):
        return "Error: path outside vault directory"
    try:
        return open(full, encoding="utf-8").read()
    except Exception as e:
        return f"Error: {e}"


# ─── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/metrics")
async def api_metrics():
    # get_metrics shells out to nvidia-smi — off the event loop
    return await asyncio.get_running_loop().run_in_executor(None, get_metrics)


@app.get("/api/status")
async def api_status():
    return {
        "ollama":  await check_ollama(),
        "mode":    get_mode(),
        "mode_label": MODES.get(get_mode(), "Unknown"),
        "hostname": socket.gethostname(),
        "time":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/api/mode/{m}")
async def api_set_mode(m: str):
    if m in MODES:
        set_mode(m)
        return {"ok": True, "mode": m, "label": MODES[m]}
    return JSONResponse({"ok": False, "error": "Invalid mode"}, 400)


@app.post("/api/park-idea")
async def api_park_idea(req: Request):
    body = await req.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "text is required"}, 400)
    filepath = park_idea(text)
    return {"ok": True, "path": filepath}


@app.get("/api/vault/{vault_name}/list")
async def api_vault_list(vault_name: str):
    return {"files": list_vault(vault_name)}


@app.get("/api/vault/{vault_name}/read")
async def api_vault_read(vault_name: str, path: str):
    return {"content": read_vault_note(vault_name, path)}


@app.get("/api/audit")
async def api_audit(request: Request, n: int = 30):
    # Audit metadata stays on this machine: refuse non-loopback peers and cross-site browser reads.
    if not _is_local_client(request):
        return JSONResponse({"ok": False, "error": "audit log is available to local clients only"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in _ALLOWED_ORIGINS:
        return JSONResponse({"ok": False, "error": "Origin not allowed"}, status_code=403)
    entries, skipped = get_audit_tail(n)
    return {"entries": entries, "skipped": skipped}


@app.get("/api/war-room/{name}")
async def api_war_room(name: str):
    # Known rooms only (2026-10-20, audit SEC-F4): the name became a file path, so "..\\x" read any .md file on the disk.
    if name not in WAR_ROOM_NAMES:
        return JSONResponse({"error": "unknown war room"}, 404)
    md_path = os.path.join(WAR_ROOMS, f"{name}.md")
    try:
        content = open(md_path, encoding="utf-8").read()
        return {"name": name, "content": content}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 404)


@app.post("/api/adobe")
async def api_adobe(req: Request):
    from adobe_bridge import RETIRED_MESSAGE
    return JSONResponse({"ok": False, "retired": True, "message": RETIRED_MESSAGE}, 410)


@app.post("/api/office")
async def api_office(req: Request):
    body    = await req.json()
    text    = body.get("text", "")
    app_name= body.get("app", "word")
    try:
        from office_bridge import design_request as office_design_request
        path = await asyncio.get_running_loop().run_in_executor(
            None, lambda: office_design_request(text, app_name=app_name)
        )
        return {"ok": True, "path": path, "message": f"Created: {os.path.basename(path)}"}
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 500)


@app.post("/api/comfy")
async def api_comfy(req: Request):
    body   = await req.json()
    prompt = body.get("prompt", "")
    steps  = int(body.get("steps", 30))
    cfg_sc = float(body.get("cfg", 7.0))
    width  = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    try:
        from comfy_client import generate
        if not await _ensure_comfy_running():
            return {"ok": False, "message":
                    f"ComfyUI did not come up within {COMFY_STARTUP_BUDGET_S}s."}
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: generate(prompt, steps=steps, cfg=cfg_sc, width=width, height=height)
        )
        if result:
            fname = os.path.basename(result)
            return {"ok": True, "path": result, "filename": fname, "url": f"/generated/{fname}"}
        return {"ok": False, "message": "Generation timed out or ComfyUI not running."}
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 500)


@app.get("/api/comfy/status")
async def api_comfy_status():
    # Tri-state so the frontend badge can distinguish a slow VRAM warm-up
    # ("starting", with elapsed seconds) from a genuinely dead backend.
    try:
        from comfy_client import is_comfy_running
        running = await asyncio.get_running_loop().run_in_executor(None, is_comfy_running)
        if running:
            state, elapsed = "online", None
        elif _comfy_started_at is not None:
            state, elapsed = "starting", int(time.monotonic() - _comfy_started_at)
        else:
            state, elapsed = "offline", None
        return {"running": running, "state": state, "elapsed_s": elapsed,
                "budget_s": COMFY_STARTUP_BUDGET_S}
    except Exception:
        return {"running": False, "state": "offline", "elapsed_s": None,
                "budget_s": COMFY_STARTUP_BUDGET_S}


@app.post("/api/rag")
async def api_rag(req: Request):
    body = await req.json()
    query = body.get("query", "")
    try:
        sys.path.insert(0, str(DAVID_HOME))
        from rag import retrieve
        # "analyst" has the broadest non-firewalled vault namespace — used here
        # as the default scope for general knowledge search with no agent context.
        results = retrieve("analyst", query, top_k=10)
        return {"results": results}
    except Exception as e:
        return {"results": [], "error": str(e)}


@app.get("/api/ingest/status")
async def api_ingest_status():
    # Prune PIDs that exited on their own — only /api/ingest/kill removed
    # entries before, so a job that finished naturally lingered in this set
    # forever. This endpoint is polled regularly, so it doubles as the
    # natural place to garbage-collect it.
    _spawned_pids.intersection_update(
        pid for pid in _spawned_pids if psutil.pid_exists(pid)
    )

    # A single ingest job is usually two OS processes — the "py" launcher
    # (whose PID is what subprocess.Popen returns and _spawned_pids tracks)
    # and the interpreter it execs into — and both match the cmdline check
    # below. Collect every match first, keyed by pid, so we can collapse
    # each launcher/interpreter pair into one row before returning.
    matched = {}
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        try:
            cmd = proc.info.get("cmdline") or []
            cmdstr = " ".join(cmd)
            is_rag = "rag.py" in cmdstr and ("import" in cmdstr or "ingest" in cmdstr)
            is_turbo = "turbo_ingest.py" in cmdstr
            if is_rag or is_turbo:
                marker = "turbo_ingest.py" if is_turbo else "rag.py"
                matched[proc.info["pid"]] = {
                    "pid":         proc.info["pid"],
                    "ppid":        proc.info["ppid"],
                    "cmd":         cmdstr[cmdstr.find(marker):],
                    "create_time": proc.info["create_time"],
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    jobs = []
    for pid, info in matched.items():
        # Skip processes whose parent is also in this set — only the
        # topmost match (the launcher, which is what's actually killable
        # via _spawned_pids) surfaces as a row.
        if info["ppid"] in matched:
            continue
        elapsed = int(time.time() - info["create_time"])
        jobs.append({
            "pid":     pid,
            "cmd":     info["cmd"],
            "elapsed": elapsed,
            "elapsed_fmt": f"{elapsed//3600}h {(elapsed%3600)//60}m" if elapsed > 3600 else f"{elapsed//60}m {elapsed%60}s",
        })
    return {"jobs": jobs, "count": len(jobs)}


@app.post("/api/ingest/start")
async def api_ingest_start(req: Request):
    body   = await req.json()
    mode   = body.get("mode", "import")
    folder = body.get("folder", "")
    domain = body.get("domain", "business")
    audio  = body.get("analyze_audio", False)

    if mode == "ingest":
        # rag.py ingest DROPS the whole active table and rebuilds only the 5 native
        # vaults (it destroyed the ~103k-file corpus once, 2026-09-25). It now needs
        # explicit flags and refuses on a populated table; the HUD never passes them.
        return JSONResponse({"ok": False, "message":
            "Full re-index drops the entire knowledge table and rebuilds only the 5 vaults. "
            "Run it from a terminal on purpose: python rag.py ingest --confirm-drop "
            "(add --force-drop-populated if the table is not empty). Use import mode here instead."}, 400)
    elif mode == "import":
        if not folder:
            return JSONResponse({"ok": False, "message": "folder is required for import mode"}, 400)
        if folder.startswith("vault:"):      # the HUD buttons name a vault; the folder comes from config/vaults.toml
            folder = str(DAVID_VAULTS / load_policy()["vaults"].get(folder[6:], folder[6:]))
        cmd = [sys.executable, str(DAVID_HOME / "rag.py"), "import", folder, domain]
        if audio:
            cmd.append("--analyze-audio")
    else:
        return JSONResponse({"ok": False, "message": f"Unknown mode: {mode}"}, 400)

    try:
        proc = subprocess.Popen(cmd, cwd=str(DAVID_HOME),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _spawned_pids.add(proc.pid)
        return {"ok": True, "pid": proc.pid, "cmd": " ".join(cmd)}
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 500)


@app.post("/api/ingest/kill")
async def api_ingest_kill(req: Request):
    body = await req.json()
    pid  = body.get("pid")
    if not isinstance(pid, int) or pid not in _spawned_pids:
        return JSONResponse({"ok": False, "message": "Unknown or untracked PID"}, 400)
    try:
        is_turbo = False
        try:
            is_turbo = "turbo_ingest.py" in " ".join(psutil.Process(pid).cmdline())
        except psutil.NoSuchProcess:
            pass

        if is_turbo:
            # Ask turbo_ingest.py to stop itself so it still reaches start_ollama() —
            # only hard-kill if it doesn't exit gracefully within ~30s.
            open(STOP_INGEST_SENTINEL, "w").close()
            for _ in range(15):
                await asyncio.sleep(2)
                if not psutil.pid_exists(pid):
                    break
            else:
                try:
                    psutil.Process(pid).terminate()
                except psutil.NoSuchProcess:
                    pass
        else:
            psutil.Process(pid).terminate()

        _spawned_pids.discard(pid)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 500)


@app.get("/api/ingest/stats")
async def api_ingest_stats():
    try:
        r = await asyncio.get_running_loop().run_in_executor(None, lambda: subprocess.run(
            [sys.executable, str(DAVID_HOME / "rag.py"), "count"],
            capture_output=True, text=True, timeout=15, cwd=str(DAVID_HOME)
        ))
        line = r.stdout.strip()
        # Extract integer chunk count from output like "  Index: 22969 chunks total"
        import re
        m = re.search(r"(\d+)", line)
        count = int(m.group(1)) if m else 0
        return {"ok": True, "output": line, "count": count}
    except Exception as e:
        return {"ok": False, "output": str(e), "count": 0}


@app.get("/api/browse-folder")
async def api_browse_folder():
    try:
        r = await asyncio.get_running_loop().run_in_executor(None, lambda: subprocess.run(
            [sys.executable, "-c",
             "import tkinter as tk; from tkinter import filedialog; "
             "root=tk.Tk(); root.withdraw(); root.wm_attributes('-topmost',1); "
             "p=filedialog.askdirectory(title='Select folder to ingest'); print(p)"],
            capture_output=True, text=True, timeout=120
        ))
        path = r.stdout.strip()
        if path:
            return {"ok": True, "path": path}
        return {"ok": False, "path": ""}
    except Exception as e:
        return {"ok": False, "path": "", "error": str(e)}


@app.get("/api/budget")
async def api_budget():
    try:
        return json.load(open(BUDGET))
    except Exception:
        return {}


@app.get("/api/calendar/today")
async def api_calendar_today():
    try:
        sys.path.insert(0, str(DAVID_HOME))
        from google_client import get_today_events, format_events_for_david
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(None, get_today_events)
        return {"ok": True, "events": events, "formatted": format_events_for_david(events)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/gmail/unread")
async def api_gmail_unread():
    try:
        sys.path.insert(0, str(DAVID_HOME))
        from google_client import get_unread_emails, format_emails_for_david
        loop = asyncio.get_running_loop()
        emails = await loop.run_in_executor(None, lambda: get_unread_emails(10))
        return {"ok": True, "emails": emails, "formatted": format_emails_for_david(emails)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/briefing")
async def api_briefing():
    if not _briefing_due():
        return {"pending": False}
    now = datetime.datetime.now()
    hour = now.hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    parts = [f"{greeting}, Demo User. Here is your briefing for {now.strftime('%A, %B %d')}."]
    try:
        sys.path.insert(0, str(DAVID_HOME))
        from google_client import (get_today_events, get_unread_emails,
                                   format_events_for_david, format_emails_for_david)
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(None, get_today_events)
        cal_text = format_events_for_david(events)
        if cal_text and "No events" not in cal_text:
            parts.append(f"Calendar: {cal_text}")
        else:
            parts.append("No events scheduled today.")
        emails = await loop.run_in_executor(None, lambda: get_unread_emails(5))
        email_text = format_emails_for_david(emails)
        if email_text and "No unread" not in email_text:
            parts.append(f"Inbox: {email_text}")
    except Exception:
        parts.append("Calendar and email data are unavailable.")
    try:
        yfile = _transcripts.day_file(datetime.date.today() - datetime.timedelta(days=1))
        if os.path.exists(yfile):
            count = open(yfile, encoding="utf-8").read().count("**You:**")
            if count:
                parts.append(f"Yesterday we had {count} exchange{'s' if count != 1 else ''}.")
    except Exception:
        pass
    _mark_briefing_sent()
    return {"pending": True, "text": " ".join(parts)}


_CALENDAR_PATTERN = re.compile(
    r'\b(schedule|calendar|appointment|meeting|event|today|plan|agenda|email|inbox|unread)\b', re.I
)

# ─── Turbo ingest ─────────────────────────────────────────────────────────────

_turbo_proc  = None
_spawned_pids: set = set()   # PIDs of processes David itself started

@app.post("/api/ingest/turbo/start")
async def api_turbo_start():
    global _turbo_proc
    if _turbo_proc and _turbo_proc.poll() is None:
        return {"ok": False, "error": "Already running"}
    with open(str(DAVID_DATA / "ingest_progress.json"), "w") as f:
        json.dump({"status": "starting", "phase": "initializing"}, f)
    _turbo_proc = subprocess.Popen(
        [sys.executable, str(DAVID_HOME / "turbo_ingest.py")],
        cwd=str(DAVID_HOME),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _spawned_pids.add(_turbo_proc.pid)
    return {"ok": True, "pid": _turbo_proc.pid}

@app.post("/api/ingest/turbo/stop")
async def api_turbo_stop():
    global _turbo_proc
    if _turbo_proc and _turbo_proc.poll() is None:
        # Same graceful-then-hard-kill sequence as /api/ingest/kill — a straight
        # terminate() here would leave Ollama stopped (see STOP_INGEST_SENTINEL).
        open(STOP_INGEST_SENTINEL, "w").close()
        for _ in range(15):
            await asyncio.sleep(2)
            if _turbo_proc.poll() is not None:
                break
        else:
            _turbo_proc.terminate()
        return {"ok": True}
    return {"ok": False, "error": "Not running"}

@app.get("/api/ingest/turbo/status")
async def api_turbo_status():
    try:
        with open(str(DAVID_DATA / "ingest_progress.json"), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"status": "idle"}
    data["running"] = bool(_turbo_proc and _turbo_proc.poll() is None)
    return data


# ─── Vault watcher ────────────────────────────────────────────────────────────

_VAULT_WATCH_STATE = str(DAVID_DATA / "vault_watch.json")
# Each domain MUST be one of rag.import_folder's IMPORT_DOMAINS
# {system, business, music, lore} or the import silently no-ops. System was
# mis-mapped to 'business' (migrating system notes out of their namespace) and
# Lore/Research to the invalid 'general' (never indexed). Research is
# firewalled and deliberately omitted so the watcher never auto-imports it.
_VAULT_DOMAIN_MAP  = {
    "System": "system", "Business": "business",
    "Music": "music", "Lore": "lore",
}

async def _vault_watcher():
    await asyncio.sleep(45)
    while True:
        try:
            try:
                state = json.load(open(_VAULT_WATCH_STATE))
            except Exception:
                state = {}
            changed = []
            for vault, domain in _VAULT_DOMAIN_MAP.items():
                vault_path = os.path.join(VAULTS, vault)
                if not os.path.isdir(vault_path):
                    continue
                from rag import is_excluded_path as _excluded
                for root, dirs, files in os.walk(vault_path):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fn in files:
                        if not fn.endswith(".md"):
                            continue
                        fp = os.path.join(root, fn)
                        # The HUD appends every chat turn to CRM\interactions\<date>.md;
                        # importing that folder re-embedded model replies as knowledge
                        # (45 such rows found in notes_q3e on 2026-10-03).
                        if _excluded(fp):
                            continue
                        mtime = str(os.path.getmtime(fp))
                        if state.get(fp) != mtime:
                            changed.append((os.path.dirname(fp), domain))
                            state[fp] = mtime
            seen_dirs = set()
            for folder, domain in changed[:8]:
                if folder in seen_dirs:
                    continue
                seen_dirs.add(folder)
                try:
                    subprocess.Popen(
                        [sys.executable, str(DAVID_HOME / "rag.py"), "import", folder, domain],
                        cwd=str(DAVID_HOME),
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    await asyncio.sleep(3)
                except Exception:
                    pass
            if changed:
                with open(_VAULT_WATCH_STATE, "w") as f:
                    json.dump(state, f)
        except Exception:
            pass
        await asyncio.sleep(60)


def _sweep_stuck_tasks():
    # A crash or client disconnect mid-run leaves tasks stuck in 'running', and
    # ws_taskrun's already-running guard then blocks re-running them forever.
    # At startup no task can genuinely be running, so anything still marked
    # 'running' is stale.
    try:
        conn = sqlite3.connect(TASKS_DB)
        conn.execute(
            "UPDATE tasks SET status='failed', "
            "completed_at=datetime('now','localtime'), "
            "result=COALESCE(NULLIF(result,''), '[interrupted: server restarted mid-run]') "
            "WHERE status='running'"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@app.on_event("startup")
async def startup():
    await asyncio.get_running_loop().run_in_executor(None, _sweep_stuck_tasks)
    await asyncio.get_running_loop().run_in_executor(None, _transcripts.prune_daily)   # chat-log retention (P1-7)
    asyncio.create_task(_vault_watcher())


@app.on_event("shutdown")
async def shutdown():
    # Episodic memory accumulates in-process (memory._session_turns) and is only
    # written to LanceDB at the 40-turn threshold or an explicit "save memory" —
    # without this, any unsaved turns are silently lost on every restart, which
    # happens routinely (--reload, updates, reboots), not just on crashes.
    try:
        _memory.save_episode(label="graceful shutdown")
    except Exception:
        pass


# ─── Task queue API ───────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def api_tasks_list():
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id,title,agent,status,created_at,completed_at FROM tasks ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return {"tasks": [dict(r) for r in rows]}


@app.post("/api/tasks")
async def api_tasks_create(req: Request):
    body  = await req.json()
    title = body.get("title", "").strip()
    desc  = body.get("description", "").strip()
    agent = body.get("agent", "analyst")
    if not title or not desc:
        return JSONResponse({"ok": False, "error": "title and description required"}, 400)
    conn = sqlite3.connect(TASKS_DB)
    cur  = conn.execute("INSERT INTO tasks (title,description,agent) VALUES (?,?,?)", (title, desc, agent))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": task_id}


@app.get("/api/tasks/{task_id}")
async def api_tasks_get(task_id: int):
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    row  = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "Not found"}, 404)
    return dict(row)


@app.delete("/api/tasks/{task_id}")
async def api_tasks_delete(task_id: int):
    conn = sqlite3.connect(TASKS_DB)
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Process management ───────────────────────────────────────────────────────

@app.get("/api/processes")
async def api_processes_list():
    import process_manager as _pm
    return _pm.get_all_status()

@app.post("/api/processes/{name}/start")
async def api_process_start(name: str):
    import process_manager as _pm
    msg = _pm.start_process(name)
    return {"ok": True, "message": msg}

@app.post("/api/processes/{name}/stop")
async def api_process_stop(name: str):
    import process_manager as _pm
    msg = _pm.stop_process(name)
    return {"ok": True, "message": msg}


# ─── Sync chat / war room for tool executor ───────────────────────────────────

@app.post("/api/chat_sync")
async def api_chat_sync(request: Request):
    data = await request.json()
    messages_in = data.get("messages", [])
    agent_name  = data.get("agent", "analyst")
    mode        = data.get("mode", "A")
    if agent_name not in AGENTS:
        # delegation targets are registered agents only; this endpoint runs no tools either way
        return JSONResponse({"error": "unknown agent"}, status_code=400)
    loop = asyncio.get_running_loop()
    is_reasoning_agent = agent_name in R1_AGENTS
    model = REASONING_MODEL if is_reasoning_agent else pick_model(mode, "general", agent=agent_name)
    sys_prompt = build_system_prompt(agent_name) + _global_behavior()
    messages = [{"role": "system", "content": sys_prompt}] + messages_in
    # 16384 for the reasoning agents (depth files + longer reasoning chains need the
    # room), 8192 for every other model — matches their canonical ctx everywhere else
    # (Layer 1, tool-decision, ws_chat, taskrun) so the SAME model loaded via this
    # endpoint (delegate_to_agent / run_war_room) doesn't force a reload. This was a
    # flat 8192 that silently truncated R1's depth-file context on this path only.
    # Branches on is_reasoning_agent, NOT `model == REASONING_MODEL` — as of
    # 2026-09-29 REASONING_MODEL and most of DOMAIN_MODELS are the same string
    # (qwen3.5:9b), so a string comparison here would misfire on every non-reasoning
    # agent that also happens to run on qwen3.5:9b. See num_ctx/think audit 2026-09-29.
    payload = json.dumps({"model": model, "messages": messages, "stream": False,
                           "think": is_reasoning_agent,
                           "options": {"num_ctx": num_ctx_for(model)}}).encode()
    try:
        resp_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(
                urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"}),
                timeout=60
            ).read()
        )
        resp = json.loads(resp_bytes)
        content = resp.get("message", {}).get("content", "")
    except Exception as e:
        content = f"Error: {e}"
    return {"response": content}


# War rooms run on qwen3.5:9b (was llama3.1:8b). Without think=False a 150-token budget is spent
# entirely on thinking and every agent returns empty text; num_ctx 8192 matches the resident model.
# The build-room developer used to run on qwen2.5-coder:7b; since 2026-10-05 every war-room agent is on qwen3.5:9b
# (no third model next to qwen3.5 + david-ft, which would not fit the 14.5 GiB budget).
WAR_ROOM_MODEL = "qwen3.5:9b"
_WR_RULES = (
    " Do not repeat or restate what prior agents said; add only what is new from your own expertise."
    " You only write text in this session: you cannot run tools, queue jobs, generate files or send anything,"
    " so never claim that you did."
)


def _wr_payload(model: str, messages: list, num_predict: int, stream: bool = False) -> bytes:
    body = {"model": model, "messages": messages, "stream": stream,
            "options": {"num_predict": num_predict, "num_ctx": num_ctx_for(model)}}
    if model == WAR_ROOM_MODEL:
        body["think"] = False
    return json.dumps(body).encode()


@app.post("/api/warroom")
async def api_warroom(request: Request):
    data    = await request.json()
    room    = data.get("room", "executive-room")
    message = data.get("message", "")
    # Known rooms only (2026-10-20, audit P1-3): the room name became a file path, and the model can choose it through the
    # run_war_room tool, so "../vaults/Research/<note>" would have fed a firewalled note to a non-firewalled agent.
    if room not in WAR_ROOM_NAMES:
        return JSONResponse({"error": "unknown war room"}, 400)
    agents  = ROOM_AGENTS.get(room, ["analyst"])
    md_path = os.path.join(WAR_ROOMS, f"{room}.md")
    try:
        room_ctx = open(md_path, encoding="utf-8").read()
    except Exception:
        room_ctx = ""
    transcripts = []
    loop = asyncio.get_running_loop()
    for wr_agent in agents:
        wr_model = WAR_ROOM_MODEL
        wr_sys = (
            build_system_prompt(wr_agent)
            + f"\n\n[WAR ROOM CONTEXT]\n{room_ctx}"
            + "\n\nYou are in a war room session. Respond in 2-3 focused sentences from your role's perspective."
            " No bullet points. No markdown. Lead with your key insight." + _WR_RULES
        )
        if transcripts:
            prior = "\n".join(f"[{a.replace('_',' ').upper()}]: {t}" for a, t in transcripts)
            wr_sys += f"\n\n[PRIOR AGENTS]\n{prior}"
        wr_msgs = [{"role": "system", "content": wr_sys}, {"role": "user", "content": message}]
        wr_payload = _wr_payload(wr_model, wr_msgs, 150)
        try:
            resp_bytes = await loop.run_in_executor(
                None, lambda p=wr_payload: urllib.request.urlopen(
                    urllib.request.Request(OLLAMA, data=p, headers={"Content-Type": "application/json"}),
                    timeout=60
                ).read()
            )
            resp = json.loads(resp_bytes)
            wr_resp = resp.get("message", {}).get("content", "")
        except Exception as e:
            wr_resp = f"[{wr_agent} error: {e}]"
        transcripts.append((wr_agent, wr_resp))
    sy_name = "analyst" if room == "calibration-room" else "chief_of_staff"
    prior_full = "\n\n".join(f"[{a.replace('_',' ').upper()}]: {t}" for a, t in transcripts)
    sy_sys = (
        f"You are the {sy_name.replace('_',' ').title()} synthesising a {room.replace('-',' ')} session. "
        "Write exactly 3 sentences: (1) the core finding, (2) the key risk, (3) the single next action. No bullets."
    )
    sy_msgs = [{"role": "system", "content": sy_sys},
               {"role": "user", "content": f"Task: {message}\n\nAgent responses:\n{prior_full}\n\nSynthesise:"}]
    sy_payload = _wr_payload(WAR_ROOM_MODEL, sy_msgs, 180)
    try:
        resp_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(
                urllib.request.Request(OLLAMA, data=sy_payload, headers={"Content-Type": "application/json"}),
                timeout=60
            ).read()
        )
        synthesis = json.loads(resp_bytes).get("message", {}).get("content", "")
    except Exception as e:
        synthesis = f"[Synthesis error: {e}]"
    return {"synthesis": synthesis, "transcripts": {a: t for a, t in transcripts}}


# ─── WebSocket streaming chat ─────────────────────────────────────────────────

@app.put("/api/upload")
async def api_upload(request: Request, name: str):
    import chat_attachments as ca
    from fastapi import HTTPException
    await asyncio.get_running_loop().run_in_executor(None, ca.purge_old_uploads)
    safe = re.sub(r"[^\w.\- ]", "_", os.path.basename(name))[:120] or "file"
    uid = uuid.uuid4().hex
    d = os.path.join(ca.UPLOAD_DIR, uid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, safe)
    size = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > ca.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large (limit 2GB).")
                f.write(chunk)
    except BaseException:
        try:
            os.remove(path)
            os.rmdir(d)
        except OSError:
            pass
        raise
    return {"id": uid, "name": safe, "size": size}


WS_CHAT_MAX_CONSECUTIVE_ERRORS = 3


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    # Same-origin gate: WebSockets are exempt from the browser's same-origin
    # policy and from the HTTP Origin middleware above, so any page the user
    # has open could otherwise drive the full pipeline. Reject the handshake
    # for a cross-origin Origin; absent Origin (desktop launcher, scripts) is
    # a non-browser caller and passes, matching the HTTP guard's contract.
    _origin = ws.headers.get("origin")
    if _origin is not None and _origin not in _ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    # 2026-10-20 (audit P1-6): the try is per message now. One unexpected error used to end the whole socket, and the reason was
    # never logged; now the failing turn gets an error reply and an audit line, and the next message on the same socket works.
    _errors_in_a_row, _turn_failed = 0, False
    while True:
        if not _turn_failed:
            _errors_in_a_row = 0
        _turn_failed, req_id = False, None
        try:
            data = await ws.receive_json()
            req_id   = uuid.uuid4().hex[:12]
            _fallback_turn = False   # set by the routing step when this turn runs as the privilege-free fallback
            _req_t0  = time.monotonic()
            msg      = data.get("message", "")
            agent    = data.get("agent", "analyst")
            domain   = data.get("domain", "general")
            history  = data.get("history", [])
            image_b64= data.get("image", None)
            files_att= data.get("files", None)

            mode = get_mode()

            # Mode C/D/E: default agent when user left it at "analyst"
            if agent == "analyst" and not image_b64:
                if mode == "C":
                    agent = "creative_director"
                elif mode == "D":
                    agent = "uiux_engineer"
                elif mode == "E":
                    agent = "prompt_architect"

            # ── Layer 0: regex pre-router — zero LLM calls ──────────────────
            if not image_b64:
                handled, token_text, log_skill = layer0_intercept(msg)
                if handled:
                    await ws.send_json({"type": "token", "token": token_text})
                    await ws.send_json({"type": "done", "full": token_text, "model": "layer0"})
                    write_audit_log({
                        "agent": "layer0", "model": "layer0",
                        "mode": get_mode(), "skill": log_skill,
                        "user": msg[:200], "response": token_text,
                        "request_id": req_id,
                    })
                    continue

            # ── Studio lock: mirror the CLI's gate ──────────────────────────
            # Any Ollama/SDXL call risks an ASIO DPC spike while the Ableton
            # transport runs. Layer 0 above is regex-only, so "mode A" and
            # "park idea: …" still work to disarm the lock or save the thought.
            if os.path.exists(STUDIO_LOCK):
                notice = ("🎛️ Studio lock active — GPU inference is paused while the "
                          "Ableton transport runs. Say `mode A` to resume, or "
                          "`park idea: …` to save this thought for later.")
                await ws.send_json({"type": "token", "token": notice})
                await ws.send_json({"type": "done", "full": notice, "model": "studio-lock"})
                write_audit_log({"action": "studio_lock_block", "surface": "hud",
                                 "user": msg[:200], "request_id": req_id})
                continue

            # ── War room auto-routing (before Layer 1 to save LLM call) ────────
            forced_room = data.get("room")
            if forced_room not in WAR_ROOM_NAMES:
                forced_room = None
            if not image_b64 and (forced_room or _L0_WAR_ROOM.search(msg)):
                room    = forced_room or _parse_room_request(msg)
                agents  = ROOM_AGENTS.get(room, ["analyst"])
                md_path = os.path.join(WAR_ROOMS, f"{room}.md")
                try:
                    room_ctx = open(md_path, encoding="utf-8").read()
                except Exception:
                    room_ctx = ""
                await ws.send_json({"type": "room_start", "room": room, "agents": agents})
                transcripts = []
                for wr_agent in agents:
                    await ws.send_json({"type": "agent_start", "agent": wr_agent})
                    wr_model = WAR_ROOM_MODEL
                    wr_sys = (
                        build_system_prompt(wr_agent)
                        + f"\n\n[WAR ROOM CONTEXT]\n{room_ctx}"
                        + "\n\nYou are in a war room session for the operator."
                        " Respond in 2-3 short focused sentences from your role's perspective."
                        " No bullet points. No markdown. Lead with your key insight." + _WR_RULES
                    )
                    wr_msgs = [{"role": "system", "content": wr_sys}]
                    if transcripts:
                        prior = "\n".join(f"[{a.replace('_',' ').upper()}]: {t}" for a, t in transcripts)
                        wr_msgs.append({"role": "system", "content": f"[PRIOR AGENTS]\n{prior}"})
                    wr_msgs.append({"role": "user", "content": msg})
                    wr_payload = _wr_payload(wr_model, wr_msgs, 150, stream=True)
                    wr_resp = ""
                    try:
                        async for chunk in _ollama_stream(wr_payload):
                            tok = chunk.get("message", {}).get("content", "")
                            if tok:
                                wr_resp += tok
                                await ws.send_json({"type": "token", "agent": wr_agent, "token": tok})
                            if chunk.get("done"):
                                break
                    except Exception as e:
                        await ws.send_json({"type": "agent_error", "agent": wr_agent, "error": str(e)})
                    transcripts.append((wr_agent, wr_resp))
                    await ws.send_json({"type": "agent_done", "agent": wr_agent, "full": wr_resp})
                # Synthesis
                await ws.send_json({"type": "synthesis_start"})
                sy_name = "analyst" if room == "calibration-room" else "chief_of_staff"
                sy_sys  = (
                    f"You are the {sy_name.replace('_',' ').title()} synthesising this "
                    f"{room.replace('-',' ')} session for the operator. "
                    "Write exactly 3 sentences: (1) the core finding, (2) the key risk, "
                    "(3) the single next action. Spoken language only. No bullets."
                )
                prior_full = "\n\n".join(f"[{a.replace('_',' ').upper()}]: {t}" for a, t in transcripts)
                sy_msgs = [
                    {"role": "system", "content": sy_sys},
                    {"role": "user", "content": f"Task: {msg}\n\nAgent responses:\n{prior_full}\n\nSynthesise:"},
                ]
                sy_payload = _wr_payload(WAR_ROOM_MODEL, sy_msgs, 180, stream=True)
                wr_synthesis = ""
                try:
                    async for chunk in _ollama_stream(sy_payload):
                        tok = chunk.get("message", {}).get("content", "")
                        if tok:
                            wr_synthesis += tok
                            await ws.send_json({"type": "synthesis_token", "token": tok})
                        if chunk.get("done"):
                            break
                except Exception as e:
                    await ws.send_json({"type": "synthesis_error", "error": str(e)})
                await ws.send_json({"type": "room_done", "synthesis": wr_synthesis})
                write_audit_log({
                    "agent": f"war_room:{room}", "model": WAR_ROOM_MODEL,
                    "mode": get_mode(), "skill": "war_room",
                    "user": msg[:200], "response": wr_synthesis[:200],
                    "request_id": req_id,
                })
                if wr_synthesis:
                    try:
                        now = datetime.datetime.now()
                        _transcripts.append(
                            f"\n## {now.strftime('%H:%M')} — war_room:{room}\n"
                            f"**You:** {msg[:300]}\n"
                            f"**David:** {wr_synthesis[:400]}\n", now)
                    except Exception:
                        pass
                continue

            # ── Office / Ableton shortcut (Adobe now goes through the agents' template tools) ──
            if not image_b64 and _L0_DESIGN.search(msg):
                app_info = _parse_design_request(msg)
                if app_info:
                    bridge_type, app_name = app_info
                    label = app_name.title()
                    if bridge_type == "office":
                        await ws.send_json({"type": "token", "token": f"Opening {label} and working on your request…\n"})
                        try:
                            from office_bridge import design_request as _office_req
                            loop = asyncio.get_running_loop()
                            path = await loop.run_in_executor(
                                None, lambda: _office_req(msg, app_name=app_name)
                            )
                            reply = f"Done. Created: {os.path.basename(str(path))}"
                        except Exception as e:
                            reply = f"Could not complete the {label} request: {e}"
                        await ws.send_json({"type": "token", "token": reply})
                        await ws.send_json({"type": "done", "full": reply, "model": "layer0-office"})
                        continue
                    elif bridge_type == "none" and app_name == "ableton":
                        await ws.send_json({"type": "token", "token": "Sending to Ableton…\n"})
                        try:
                            from david_router import try_ableton_command
                            loop = asyncio.get_running_loop()
                            reply = await loop.run_in_executor(None, lambda: try_ableton_command(msg))
                            reply = reply or "Done in Ableton."
                        except Exception as e:
                            reply = f"Could not reach Ableton: {e}"
                        await ws.send_json({"type": "token", "token": reply})
                        await ws.send_json({"type": "done", "full": reply, "model": "layer0-ableton"})
                        continue

            # ── Vision path ──────────────────────────────────────────────────
            tier = 1  # vision and image-gen never escalate to cloud
            # Set on every path: the non-image branch below overwrites them, but an image turn
            # skips that branch and the generate call further down reads both (an image sent to
            # the HUD crashed the turn with UnboundLocalError until 2026-10-03).
            is_r1_agent = False
            force_reasoning = False
            if image_b64:
                model   = VISION_MODEL
                route   = {**_ROUTE_DEFAULT, "intent": "vision"}
                vision_prompt = msg or "Describe this image in full detail. Note every element, layout, color, text, and structure."
                messages = [{"role": "user", "content": vision_prompt, "images": [image_b64]}]

            else:
                force_agent = bool(data.get("force_agent")) and agent in AGENTS

                if force_agent:
                    # Explicit agent selection (e.g. from the Agents page) — skip
                    # Layer 1 classification entirely rather than let it silently
                    # reclassify to a different agent than the one requested.
                    route = {**_ROUTE_DEFAULT, "agent": agent, "domain": AGENT_DOMAIN.get(agent, "general"), "needs_reasoning": False}
                else:
                    # ── Layer 1: resident router ─────────────────────────────────
                    route = await layer1_route(msg, agent, domain, mode)

                # Use router's agent/domain if valid; anything else becomes the privilege-free fallback turn
                if route["agent"] in AGENTS:
                    agent = route["agent"]
                else:
                    route = fallback_route("no valid agent for this turn")
                    agent = route["agent"]
                if route["domain"] in DOMAIN_MODELS:
                    domain = route["domain"]
                _fallback_turn = bool(route.get("fallback"))

                # Make HUD turns visible to memory.py (mirrors david_router.py's CLI
                # path) so they get summarized/embedded via save_episode() and are
                # findable by future recall() calls — previously only the CLI did this.
                try:
                    # a fallback turn has no trusted agent: recorded with no agent, i.e. firewalled (memory.py)
                    _memory.add_turn(None if _fallback_turn else agent, domain, msg)
                except Exception:
                    pass

                is_r1_agent = agent in R1_AGENTS

                # Escalate: needs_reasoning=true on a non-R1 agent → use the reasoning
                # model for THIS request only. The old version wrote Mode B to the global
                # mode.state and reverted after — racing concurrent requests and flickering
                # every connected HUD for a single exchange.
                # An analysis of an attached document also goes to the reasoning model: measured on the
                # same digest it found real weaknesses (omitted labour cost, FX dependence, POS-data
                # lead time) where the tuned analyst model invented a what-if figure and llama gave
                # generic points (rubric 7/9 vs 5/9 vs 4/9).
                force_reasoning = (route["needs_reasoning"]
                                   or bool(files_att and chat_attachments.wants_analysis(msg))) and not is_r1_agent
                if force_reasoning and mode != "B":
                    await ws.send_json({
                        "type": "token",
                        "token": f"⚡ **Routing to {REASONING_MODEL}**\n\n",
                    })

                # Mode E — Render: force image generation for visual requests
                if mode == "E" and route.get("intent") != "image" and _RENDER_PATTERN.search(msg):
                    route["intent"] = "image"

                # ── Image generation branch ──────────────────────────────────
                if route.get("intent") == "image":
                    try:
                        from comfy_client import generate, is_comfy_running
                        loop = asyncio.get_running_loop()
                        if not await loop.run_in_executor(None, is_comfy_running):
                            await ws.send_json({"type": "token", "token": "⏳ ComfyUI starting — model load can take a few minutes under VRAM pressure...\n"})

                            async def _comfy_progress(text):
                                await ws.send_json({"type": "token", "token": text})

                            if not await _ensure_comfy_running(progress=_comfy_progress):
                                await ws.send_json({"type": "error", "error":
                                    f"ComfyUI did not come up within {COMFY_STARTUP_BUDGET_S}s — "
                                    f"check its window/logs (VRAM exhaustion is the usual cause)."})
                                continue
                        await ws.send_json({"type": "token", "token": "🎨 Generating image — please wait...\n"})
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None, lambda: generate(msg, steps=20, cfg=7.0, width=1024, height=1024)
                        )
                        if result:
                            fname = os.path.basename(result)
                            await ws.send_json({"type": "image", "url": f"/generated/{fname}", "prompt": msg})
                            await ws.send_json({"type": "done", "full": f"[Image: {fname}]", "model": "sdxl"})
                        else:
                            await ws.send_json({"type": "error", "error": "Image generation timed out."})
                    except Exception as img_err:
                        await ws.send_json({"type": "error", "error": f"Image gen: {img_err}"})
                    continue

                # ── DLP + Tier 2/3 escalation gate (shared with CLI via david_router.route_tier) ──
                # Firewalled agents and DLP-flagged text always resolve to Tier 1 here.
                loop = asyncio.get_running_loop()
                tier = 1 if _fallback_turn else await loop.run_in_executor(
                    None, lambda: route_tier(agent, msg, route["needs_reasoning"], req_id)
                )

                if is_r1_agent:
                    model = REASONING_MODEL
                else:
                    model = REASONING_MODEL if force_reasoning else pick_model(mode, domain, agent=agent)
                system_prompt = build_system_prompt(agent) + _global_behavior()
                messages = [{"role": "system", "content": system_prompt}]

                # Some models use a strict chat template that only tolerates ONE
                # system message followed by strictly alternating user/assistant
                # turns — a second system-role message throws a 400 from Ollama
                # (confirmed on the REASONING_MODEL alias formerly used here). So
                # every context injection below accumulates into one block folded
                # into the single
                # system message instead of being appended as its own message.
                _ctx_blocks = []

                if files_att:
                    try:
                        async def _attach_progress(text):
                            await ws.send_json({"type": "token", "token": text})

                        _ctx_blocks.append(await chat_attachments.build_context(
                            files_att, msg, _attach_progress, history))
                    except Exception as e:
                        _ctx_blocks.append(
                            f"[ATTACHED FILES — could not be read: {e}] Tell the user the attachment failed to load.")

                # ── Auto-inject Google Calendar / Gmail data ─────────────────
                if _CALENDAR_PATTERN.search(msg):
                    try:
                        sys.path.insert(0, str(DAVID_HOME))
                        from google_client import (get_today_events, get_unread_emails,
                                                   format_events_for_david, format_emails_for_david)
                        loop = asyncio.get_running_loop()
                        want_email = bool(re.search(r'\b(email|inbox|unread|mail)\b', msg, re.I))
                        context_lines = []
                        if not want_email:
                            events = await loop.run_in_executor(None, get_today_events)
                            context_lines.append("TODAY'S CALENDAR:\n" + format_events_for_david(events))
                        else:
                            events = await loop.run_in_executor(None, get_today_events)
                            emails = await loop.run_in_executor(None, lambda: get_unread_emails(10))
                            context_lines.append("TODAY'S CALENDAR:\n" + format_events_for_david(events))
                            context_lines.append("UNREAD EMAILS:\n" + format_emails_for_david(emails))
                        _ctx_blocks.append("[LIVE DATA]\n" + "\n\n".join(context_lines))
                    except Exception:
                        pass

                # ── Knowledge injection: vault RAG + memory.py recall + knowledge.py facts ──
                if _RAG_OK and not _fallback_turn:
                    try:
                        loop = asyncio.get_running_loop()
                        ctx = await loop.run_in_executor(
                            None, lambda: rag_context(agent, msg)
                        )
                        if ctx:
                            _ctx_blocks.append("[KNOWLEDGE]" + ctx)
                    except Exception:
                        pass

                # ── Default tool-decision layer (replaces the old _AGENTIC_PATTERN
                # regex gate below, now removed) ─────────────────────────────
                # Runs for every message in every domain. The "technical" domain gets
                # only the READ-ONLY tools (tools.py tools_for_domain): it can look
                # things up but can never trigger a side effect from a coding request
                # (0/60 false positives measured on qwen3.5). At
                # most one tool call per turn; the fine-tuned/routed model below
                # never sees the `tools` schema itself — passing it directly
                # broke david-ft/qwen-ft's tuned behavior in testing.
                if not image_b64 and not _fallback_turn:
                    try:
                        from tool_executor import decide_and_run_tool
                        tool_ctx = await decide_and_run_tool(agent, domain, msg, request_id=req_id, history=history)
                        if tool_ctx and tool_ctx.startswith("[VERBATIM:"):
                            # Code-built output (the review sheet) goes to the user unchanged; no model rewrites it.
                            _vname, _, _vbody = tool_ctx[len("[VERBATIM:"):].partition("]\n")
                            await ws.send_json({"type": "token", "token": _vbody})
                            await ws.send_json({"type": "done", "full": _vbody, "model": f"tool:{_vname}"})
                            write_audit_log({"action": "verbatim_tool_reply", "tool": _vname, "agent": agent,
                                             "domain": domain, "request_id": req_id})
                            continue
                        if tool_ctx:
                            _ctx_blocks.append(tool_ctx)
                    except Exception as e:
                        write_audit_log({"action": "tool_decision_exception", "agent": agent,
                                         "domain": domain, "error": str(e), "request_id": req_id})

                # ── Mode C/D context injection ───────────────────────────────
                if mode == "C":
                    try:
                        lore_path = os.path.join(VAULTS, "Lore")
                        if os.path.isdir(lore_path):
                            snippets = []
                            for fn in sorted(os.listdir(lore_path))[:3]:
                                if fn.endswith(".md"):
                                    txt = open(os.path.join(lore_path, fn), encoding="utf-8").read()
                                    snippets.append(f"[{fn}]\n{txt[:400]}")
                            if snippets:
                                _ctx_blocks.append("[CREATIVE LORE]\n" + "\n\n".join(snippets))
                    except Exception:
                        pass
                elif mode == "D":
                    try:
                        dr = open(os.path.join(WAR_ROOMS, "design-review-room.md"), encoding="utf-8").read()
                        if dr:
                            _ctx_blocks.append(f"[DESIGN REVIEW CONTEXT]\n{dr[:1500]}")
                    except Exception:
                        pass

                # ── Web search injection ─────────────────────────────────────
                _search_injected = False
                _do_search = route.get("intent") == "search" or bool(_SEARCH_FORCE.search(msg))
                # The "answer ONLY from these web results" framing below would override the
                # attached file, so an attachment wins unless the user explicitly asks to search.
                if files_att and not _SEARCH_FORCE.search(msg):
                    _do_search = False
                # DLP applies to egress, not just cloud LLMs: the raw user message
                # is what gets shipped to DuckDuckGo, so a message the tier gate
                # blocked from Gemini/Claude must not leak to DDG either.
                if _do_search and dlp_flag(msg):
                    _do_search = False
                    write_audit_log({"action": "dlp_block_search", "surface": "hud", "request_id": req_id})
                if _do_search:
                    try:
                        from ddgs import DDGS
                        loop = asyncio.get_running_loop()
                        hits = await loop.run_in_executor(
                            None, lambda: list(DDGS().text(msg, max_results=20))
                        )
                        if hits:
                            search_ctx = (
                                "[LIVE WEB SEARCH RESULTS — today's date is "
                                + datetime.date.today().isoformat()
                                + "]\n"
                                "Summarise ONLY what these results say. Do NOT fall back to training data.\n\n"
                                + "\n\n".join(
                                    f"[{i+1}] {h.get('title','')}\n{h.get('body','')}"
                                    for i, h in enumerate(hits)
                                )
                            )
                            _ctx_blocks.append(search_ctx)
                            _search_injected = True
                    except Exception:
                        pass

                if _ctx_blocks:
                    messages[0]["content"] += "\n\n" + "\n\n".join(_ctx_blocks)

                # deepseek-r1 was found to ignore injected facts and reason from training
                # data instead, for search queries specifically — fall back to a model
                # that reliably follows injected context. Re-tested 2026-09-29 against
                # qwen3.5:9b (now REASONING_MODEL) on one fabricated-fact probe and it
                # correctly used the injected context — but that's one test, not
                # exhaustive, so the safety net stays. Condition is (is_r1_agent or
                # force_reasoning), NOT `model == REASONING_MODEL` — REASONING_MODEL and
                # most of DOMAIN_MODELS are the same string (qwen3.5:9b) as of the same
                # date, so a string comparison here would misfire on every non-reasoning
                # agent that also happens to run on qwen3.5:9b. See think audit 2026-09-29.
                if _search_injected and (is_r1_agent or force_reasoning):
                    model = "llama3.1:8b"

                for h in history[-10:]:
                    messages.append({"role": h["role"], "content": h["content"]})

                # Context stuffed into the system message gets less attention than
                # content right next to the actual question — small local models in
                # particular default back to a strong training prior (e.g. "the World
                # Cup hasn't started") unless the grounding is repeated right here.
                user_content = msg
                _lang_hint = language_hint(msg)
                if _lang_hint:
                    user_content += "\n\n" + _lang_hint
                if files_att:
                    user_content += "\n\n(Answer from the [ATTACHED FILE] content provided above.)"
                    if chat_attachments.wants_report(msg):
                        user_content += "\n\n" + (chat_attachments.ANALYSIS_HINT if chat_attachments.wants_analysis(msg)
                                                  else chat_attachments.REPORT_HINT)
                if _search_injected:
                    user_content += (
                        "\n\n(Answer using ONLY the [LIVE WEB SEARCH RESULTS] above — it reflects "
                        "today's real date and current information, which your training data does not. "
                        "If any result describes an event as having already happened, treat it as having "
                        "happened — do not claim it \"hasn't started yet\" while also citing that event.)"
                    )
                messages.append({"role": "user", "content": user_content})

            # NOTE: the old _AGENTIC_PATTERN-gated ReAct loop (agent_loop.py) that
            # used to live here is superseded by the default tool-decision layer
            # above, which now runs for every message instead of only ones that
            # happened to match a narrow regex. agent_loop.py itself is untouched
            # and still importable for a future deeper multi-step loop, but has a
            # known separate bug (its final answer always used hardcoded bare
            # llama3.1:8b, never the actual routed/fine-tuned model) not fixed here.

            # ── Tier 2/3 cloud escalation, falling back to local Tier 1 on failure ──
            # (mirrors david_router.route_and_execute's exact fallback chain: Tier 3
            # tries Claude then Gemini then local; Tier 2 tries Gemini then local.
            # A no-op until GEMINI_API_KEY/ANTHROPIC_API_KEY are actually configured —
            # _gemini_chunks/_claude_chunks raise cleanly and we fall through below.)
            full_response = ""
            cloud_done = False
            _error_sent = False

            # Analysis of an attached document: findings that must quote the document, verified
            # (chat_attachments.deep_analysis) — runs locally, so it also skips cloud escalation.
            # Any failure falls through to the normal model call below.
            if files_att and chat_attachments.wants_analysis(msg) and not image_b64:
                try:
                    async def _deep_progress(text):
                        await ws.send_json({"type": "token", "token": text})

                    _deep = await chat_attachments.deep_analysis(files_att, msg, _deep_progress)
                    if _deep.strip():
                        for _i in range(0, len(_deep), 400):
                            await ws.send_json({"type": "token", "token": _deep[_i:_i + 400]})
                        full_response, model, tier, cloud_done = _deep, chat_attachments.ANALYSIS_MODEL, 1, True
                except Exception as e:
                    write_audit_log({"action": "deep_analysis_exception", "agent": agent,
                                     "error": f"{type(e).__name__}: {e}", "request_id": req_id})

            if tier in (2, 3):
                # Escalation carries the same payload the local model gets: the full
                # system prompt (persona/depth + injected RAG/live context, folded into
                # messages[0] above) and the trailing history — not just the bare msg.
                # DLP re-scans everything user-derived that will leave the machine;
                # route_tier only saw the bare message, but context blocks and history
                # can carry sensitive content too. The static persona is trusted.
                cloud_system  = messages[0]["content"]
                cloud_history = messages[1:-1]
                _egress = "\n".join(
                    _ctx_blocks + [m["content"] for m in cloud_history] + [msg]
                )
                if dlp_flag(_egress):
                    write_audit_log({"action": "dlp_block_escalation", "tier": tier,
                                     "agent": agent, "request_id": req_id})
                    tier = 1
            if tier in (2, 3):
                attempts = [(CLAUDE_MODEL, _claude_chunks)] if tier == 3 else []
                attempts.append((GEMINI_MODEL, _gemini_chunks))
                for cloud_model_name, chunks_fn in attempts:
                    full_response = ""
                    try:
                        async for tok in _cloud_stream(
                            chunks_fn, agent, messages[-1]["content"], req_id,
                            system=cloud_system, history=cloud_history,
                        ):
                            full_response += tok
                            await ws.send_json({"type": "token", "token": tok})
                        model = cloud_model_name
                        cloud_done = True
                        break
                    except Exception as cloud_err:
                        write_audit_log({
                            "action": "tier_fallback", "tier": tier, "agent": agent,
                            "error": f"{type(cloud_err).__name__}: {cloud_err}",
                            "request_id": req_id,
                        })
                        continue

            if not cloud_done:
                full_response = ""
                _is_reasoning_call = is_r1_agent or force_reasoning
                payload = json.dumps({
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "think": _is_reasoning_call,
                    # Answers grounded in an attachment shouldn't vary run to run: at the default
                    # temperature the same "what printer did they pick?" question sometimes
                    # invented a company and sometimes correctly said the file doesn't say.
                    # 16384 for the reasoning agents, 8192 for everything else — see the
                    # matching comment on /api/chat_sync. This was a flat 16384 for
                    # every model; non-reasoning models (qwen3.5/david-ft/qwen-ft/qwen2.5-coder)
                    # now match their canonical ctx everywhere else in the codebase
                    # instead of reloading between this call and Layer 1/tool-decision.
                    # Branches on _is_reasoning_call, NOT `model == REASONING_MODEL` — as of
                    # 2026-09-29 REASONING_MODEL and most of DOMAIN_MODELS are the same
                    # string (qwen3.5:9b), so a string comparison here would misfire on
                    # every non-reasoning agent that also happens to run on qwen3.5:9b.
                    # See num_ctx/think audit 2026-09-29.
                    "options": {"num_ctx": num_ctx_for(model),
                                **({"temperature": 0.3 if _is_reasoning_call else 0.2} if files_att else {})},
                }).encode()
                try:
                    async for chunk in _ollama_stream(payload):
                        chunk_msg = chunk.get("message", {})
                        # Thinking-capable models (deepseek-r1, qwen3.5) return their
                        # chain-of-thought trace in a separate "thinking" field, not
                        # embedded <think> tags — stream it as its own event so the
                        # frontend can render it as a distinct, collapsible reasoning
                        # section.
                        reasoning = chunk_msg.get("thinking", "")
                        if reasoning:
                            await ws.send_json({"type": "reasoning_token", "token": reasoning})
                        token = chunk_msg.get("content", "")
                        if token:
                            full_response += token
                            await ws.send_json({"type": "token", "token": token})
                        if chunk.get("done"):
                            break
                except Exception as e:
                    # ── Hybrid failover: cloud (gated) → small local model ──
                    handled = False
                    if not image_b64 and any(k in str(e).lower() for k in _RECOVERABLE_ERR):
                        handled, full_response, model = await _local_failover(
                            ws, agent, messages, msg, _ctx_blocks, model, req_id, e)
                    if not handled:
                        _error_sent = True
                        try:
                            await ws.send_json({"type": "error", "error": str(e)})
                        except Exception:
                            pass

                # ── Code guardrail: validate + one repair pass (local models only) ──
                if (full_response and _CODE_GUARD_OK and not image_b64
                        and model not in (GEMINI_MODEL, CLAUDE_MODEL)
                        and (domain == "technical" or agent in ("developer", "uiux_engineer"))):
                    try:
                        full_response = await _guard_code_response(
                            ws, messages, model, full_response, req_id)
                    except Exception:
                        pass

            # A reasoning model can spend its whole turn on <think>/"thinking"
            # content and stream zero visible "content" tokens — observed live
            # with deepseek-r1:14b on short prompts, and reproduced 2026-09-29
            # with qwen3.5:9b on an identity-framing confusion that spiraled
            # until it hit the context limit. The stream still ends cleanly
            # with no error, so this is NOT caught by any except/error path
            # above, cloud or local. Without this, the user gets a blank chat
            # bubble marked "complete" with nothing to explain it.
            _empty_notice = _guard_empty_response(full_response, _error_sent)
            if _empty_notice:
                full_response = _empty_notice
                try:
                    await ws.send_json({"type": "token", "token": full_response})
                except Exception:
                    pass

            if files_att and full_response and not _error_sent:
                try:
                    note = chat_attachments.grounding_note(files_att, full_response)
                    if note:
                        full_response += note
                        await ws.send_json({"type": "token", "token": note})
                except Exception:
                    pass

            try:
                await ws.send_json({"type": "done", "full": full_response, "model": model})
            except Exception:
                pass

            write_audit_log({
                "agent": agent, "model": model, "mode": mode,
                "skill": route.get("intent", "vision" if image_b64 else "chat"),
                "route": None if image_b64 else route,
                "user": msg[:200], "response": full_response[:200],
                "request_id": req_id,
                "duration_ms": int((time.monotonic() - _req_t0) * 1000),
            })

            # ── Chat transcript — append exchange to the daily file (transcripts.py: DAVID_TRANSCRIPTS, 90-day default) ──
            if full_response and not image_b64:
                try:
                    now = datetime.datetime.now()
                    _transcripts.append(
                        f"\n## {now.strftime('%H:%M')} — {agent}\n"
                        f"**You:** {msg[:300]}\n"
                        f"**David:** {full_response[:400]}\n", now)
                except Exception:
                    pass

        except WebSocketDisconnect:
            return
        except Exception as e:
            _turn_failed = True
            _errors_in_a_row += 1
            # the exception type only: messages can carry what the user typed
            write_audit_log({"action": "ws_chat_turn_error", "error_type": type(e).__name__, "request_id": req_id})
            if ws.client_state != WebSocketState.CONNECTED or ws.application_state != WebSocketState.CONNECTED:
                return
            try:
                await ws.send_json({"type": "error", "error": str(e)})
            except Exception:
                return
            if _errors_in_a_row >= WS_CHAT_MAX_CONSECUTIVE_ERRORS:
                # something is broken for good (or a client keeps sending garbage): stop instead of looping on errors
                try:
                    await ws.close(code=1011)
                except Exception:
                    pass
                return


# ─── WebSocket task executor ───────────────────────────────────────────────────

@app.websocket("/ws/taskrun")
async def ws_taskrun(ws: WebSocket):
    _origin = ws.headers.get("origin")
    if _origin is not None and _origin not in _ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            data    = await ws.receive_json()
            task_id = int(data.get("task_id", 0))
            if not task_id:
                continue

            conn = sqlite3.connect(TASKS_DB)
            conn.row_factory = sqlite3.Row
            row  = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            conn.close()

            if not row:
                await ws.send_json({"type": "error", "error": "Task not found"})
                continue

            task = dict(row)

            if task["status"] == "running":
                await ws.send_json({"type": "error", "error": "Task already running"})
                continue

            conn = sqlite3.connect(TASKS_DB)
            conn.execute(
                "UPDATE tasks SET status='running', started_at=datetime('now','localtime') WHERE id=?",
                (task_id,)
            )
            conn.commit()
            conn.close()

            await ws.send_json({"type": "task_start", "task_id": task_id, "title": task["title"]})

            agent  = task["agent"]
            mode   = get_mode()
            domain = ("technical" if agent in ("developer","uiux_engineer","prompt_architect") else
                      "music"     if agent in ("beat_architect","acoustician","lore_keeper")   else
                      "business"  if agent in ("cfo","executive_advisor","venture_architect","legal_counsel") else
                      "general")

            loop = asyncio.get_running_loop()
            _task_is_reasoning = agent in R1_AGENTS
            model = REASONING_MODEL if _task_is_reasoning else pick_model(mode, domain, agent=agent)
            base_prompt = build_system_prompt(agent)

            system_prompt = (
                base_prompt
                + "\n\nYou are executing a background task for the operator."
                + " Be thorough and complete. Do not truncate your response."
            )

            # Fold RAG context into the single system message rather than a second
            # system-role message — some reasoning models 400 on multiple system
            # messages (see the identical fix and comment in ws_chat above).
            if _RAG_OK:
                try:
                    ctx = await loop.run_in_executor(
                        None, lambda: rag_context(agent, task["description"])
                    )
                    if ctx:
                        system_prompt += "\n\n[KNOWLEDGE]" + ctx
                except Exception:
                    pass

            messages = [{"role": "system", "content": system_prompt}]
            messages.append({"role": "user", "content": f"Task: {task['title']}\n\n{task['description']}"})

            # 16384 for the reasoning agents, 8192 for everything else — see the
            # matching comment on /api/chat_sync and the main ws_chat streaming call.
            # Branches on _task_is_reasoning, NOT `model == REASONING_MODEL` — see
            # the same note there. See num_ctx/think audit 2026-09-29.
            payload = json.dumps({
                "model": model,
                "messages": messages,
                "stream": True,
                "think": _task_is_reasoning,
                "options": {"num_predict": 1500,
                            "num_ctx": num_ctx_for(model)},
            }).encode()

            full_result = ""
            status = "done"
            try:
                async for chunk in _ollama_stream(payload, timeout=300):
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_result += token
                        await ws.send_json({"type": "token", "token": token})
                    if chunk.get("done"):
                        break
            except Exception as e:
                status = "failed"
                full_result = str(e)
                # A disconnect mid-stream lands here too — the send below would
                # re-raise on the dead socket and skip the status UPDATE,
                # leaving the task stuck in 'running'. Never let it.
                try:
                    await ws.send_json({"type": "error", "error": str(e)})
                except Exception:
                    pass

            conn = sqlite3.connect(TASKS_DB)
            conn.execute(
                "UPDATE tasks SET status=?, completed_at=datetime('now','localtime'), result=? WHERE id=?",
                (status, full_result, task_id)
            )
            conn.commit()
            conn.close()

            try:
                await ws.send_json({"type": "task_done", "task_id": task_id, "status": status})
            except Exception:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass


# ─── Restaurant page (Job 1 daily runner): upload a day's files, answer, get the SQL files ──────────────
import restaurant_api
app.include_router(restaurant_api.router)

# ─── Design page (Design portal piece 1b): projects, brief, plan, per-line checks, the designer's yes, safe runner ──────────────
import design_api
app.include_router(design_api.router)


# ─── SPA fallback (must stay last) ────────────────────────────────────────────

@app.get("/favicon.svg")
async def frontend_favicon():
    return FileResponse(os.path.join(FRONTEND_DIST, "favicon.svg"))


@app.get("/icons.svg")
async def frontend_icons():
    return FileResponse(os.path.join(FRONTEND_DIST, "icons.svg"))


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith(("api/", "ws/", "static/", "generated/", "assets/")):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    index = os.path.join(FRONTEND_DIST, "index.html")
    if not os.path.exists(index):
        return JSONResponse({"ok": False, "error": "The HUD is not built. Run: cd ui/frontend && npm install && npm run build"}, status_code=503)
    return FileResponse(index)
