# tool_executor.py — async dispatcher for all David agent tools

from david_config import DAVID_DATA, DAVID_HOME, DAVID_VAULTS, load_policy, is_firewalled_vault
import asyncio
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

from ollama_config import num_ctx_for, OLLAMA_HOST

TASKS_DB = str(DAVID_DATA / "tasks.db")
LANCEDB_PATH = str(DAVID_DATA / "lancedb")
# voice_daemon.py consumes timestamped .txt files from speak_queue\ — the old
# voice_inbox.txt target had no reader (voice_inbox.py watches audio in inbox\),
# so every speak tool call was silently dropped.
SPEAK_QUEUE = str(DAVID_DATA / "speak_queue")

VAULT_ROOTS = {name: str(DAVID_VAULTS / folder) for name, folder in load_policy()["vaults"].items()}
# The old version of every note that write_note overwrites (2026-10-20, audit P1-1). Outside the vaults on purpose: never indexed,
# never shown in Obsidian as a note. Same folders as the note, file name <note>.<timestamp>.bak.
NOTE_BACKUP_DIR = str(DAVID_DATA / "note_backups")


def _refuse(name, agent, domain, reason):
    from david_router import audit
    audit({"action": "tool_refused", "tool": str(name)[:60], "agent": str(agent)[:60] if agent is not None else None,
           "domain": domain if isinstance(domain, str) else None, "reason": reason})


async def execute_tool(name: str, args: dict, agent: str | None = None, domain: str | None = None) -> str:
    # Per-agent tool scoping (agents/<agent>/role.md), checked here, immediately before anything runs, whatever the
    # model asked for. FAIL CLOSED (audit finding S-05, 2026-10-18): a call with no acting agent, an agent the router
    # does not know, or a tool that is not registered is refused; it used to run unscoped.
    import agent_spec
    from tools import TOOL_NAMES
    if not agent_spec.is_registered_agent(agent):
        _refuse(name, agent, domain, "no valid acting agent")
        return f"Tool not permitted: no valid acting agent for {name}"
    if name not in TOOL_NAMES:
        _refuse(name, agent, domain, "unknown tool")
        return f"Tool not permitted: unknown tool {str(name)[:60]}"
    if not agent_spec.tool_allowed(agent, domain, name):
        _refuse(name, agent, domain, "not in the agent's allowlist")
        return f"Tool not permitted for the {agent} agent: {name}"
    if not isinstance(args, dict):
        return f"Tool error ({name}): arguments must be an object"
    loop = asyncio.get_running_loop()
    try:
        if name == "search_web":
            return await loop.run_in_executor(None, _search_web, args["query"])
        elif name == "query_knowledge":
            # scoped by the agent that is actually acting, never by an `agent` value inside the model-written arguments
            return await loop.run_in_executor(None, _query_knowledge, args["query"], agent)
        elif name == "read_note":
            return await loop.run_in_executor(None, _read_note, args["vault"], args["path"])
        elif name == "write_note":
            return await loop.run_in_executor(None, _write_note, args["vault"], args["path"], args["content"], args.get("append", False), args.get("overwrite", False))
        elif name == "get_calendar":
            return await loop.run_in_executor(None, _get_calendar)
        elif name == "get_email":
            return await loop.run_in_executor(None, _get_email, args.get("count", 5))
        elif name == "get_tasks":
            return await loop.run_in_executor(None, _get_tasks)
        elif name == "create_task":
            return await loop.run_in_executor(None, _create_task, args["title"], args["description"], args["agent"])
        elif name == "run_war_room":
            return await _run_war_room(args["room"], args["message"])
        elif name == "delegate_to_agent":
            return await _delegate_to_agent(args["agent"], args["prompt"])
        elif name == "start_process":
            import process_manager
            return process_manager.start_process(args["name"])
        elif name == "stop_process":
            import process_manager
            return process_manager.stop_process(args["name"])
        elif name == "process_status":
            import process_manager
            name_arg = args.get("name", "all")
            if name_arg == "all":
                statuses = process_manager.get_all_status()
                return json.dumps(statuses, indent=2)
            return json.dumps(process_manager.get_status(name_arg))
        elif name == "get_metrics":
            return await loop.run_in_executor(None, _get_metrics)
        elif name == "speak":
            return await loop.run_in_executor(None, _speak, args["text"])
        elif name == "generate_image":
            return await loop.run_in_executor(None, _generate_image, args["prompt"], args.get("steps", 20))
        elif name in ("reconcile_cash", "check_meat_variance", "render_review_sheet"):
            import restaurant_tools          # deterministic Job 1 tools: pure computation, no side effects
            return await loop.run_in_executor(None, restaurant_tools.call_tool, name, args)
        elif name in ("get_menu_glossary", "match_menu_item", "validate_draft_package"):
            import menu_tools                # Job 1: reads the local Demo Bistro menu files; deterministic, read-only
            return await loop.run_in_executor(None, menu_tools.call_tool, name, args)
        elif name in ("list_adobe_templates", "fill_adobe_template", "export_adobe_preview", "review_adobe_preview"):
            import adobe_template_tools      # Job 2: registered templates only, plan first, copies only (see its docstring)
            return await loop.run_in_executor(None, adobe_template_tools.call_tool, name, args)
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        # the exception type only: messages can carry argument values or file contents
        return f"Tool error ({name}): {type(e).__name__}"


# ── Default tool-decision layer ─────────────────────────────────────────────────
# See tools.py's TOOL_DECISION_MODEL/tools_for_domain comment for the full
# rationale. Single-shot: at most one tool call per turn, no ReAct iteration —
# agent_loop.py's deeper multi-step loop remains available separately for
# genuinely complex tasks. This covers the common case (one lookup, then
# answer) without the latency/complexity of a full planning loop.

_YES = re.compile(r"^\s*(yes|yep|yeah|y|ok|okay|sure|go ahead|go on|proceed|confirm|confirmed|do it|please do|نعم|ايوه|ايوا|أيوه|تمام|موافق|نفذ|ابدأ|اوكي|أوكي)[\s.!,؟?]*(please|thanks|thank you|شكرا|شكراً)?[\s.!,؟?]*$", re.I)
_NO = re.compile(r"^\s*(no|nope|cancel|stop|never ?mind|لا|الغاء|إلغاء|الغي|ألغي|توقف)[\s.!,؟?]*$", re.I)
_PLAN_IN_REPLY = re.compile(r"\((PLAN-\d{14}-[0-9a-f]{6})\)")


def pending_plan(history, msg):
    """('yes'|'no', plan_id) when the user's WHOLE message is a bare yes/no and the assistant's last reply was a fill plan still waiting
    for an answer; otherwise None. This is the human gate for the Adobe tools: only a person's own reply runs a plan."""
    if not history or not isinstance(msg, str) or len(msg) > 40:
        return None
    last = next((h for h in reversed(history) if isinstance(h, dict) and h.get("role") == "assistant"), None)
    if not last:
        return None
    text = str(last.get("content") or "")
    m = _PLAN_IN_REPLY.search(text)
    if not m or "Nothing has been created yet" not in text:
        return None
    if _YES.match(msg):
        return "yes", m.group(1)
    if _NO.match(msg):
        return "no", m.group(1)
    return None


# 2026-10-20 (audit P2-8): when no tool is needed the model often starts writing the whole answer inside the decision call, and that
# answer is thrown away (measured live: 454 tokens, 8.4 s, before the real answer even started). Ollama delivers a tool call as one
# finished message and streams prose as content, so the decision is read until a tool call arrives or the prose passes a few dozen
# characters, then the stream is closed, which stops Ollama generating (measured: the decision ends after about 1 s). A flat token
# cap was not used because it cuts long tool calls (a short write_note already needed 103 tokens); the cap here is only a backstop.
_DECISION_PROSE_STOP_CHARS = 120
_DECISION_MAX_TOKENS = 2048


async def _read_tool_decision(client, payload: dict) -> dict:
    """Streams the tool-decision call and returns {"message": {"tool_calls": [...], "content": ...}} like the non-streaming reply."""
    tool_calls, content = [], ""
    async with client.stream("POST", OLLAMA_HOST + "/api/chat", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            message = chunk.get("message") or {}
            tool_calls += message.get("tool_calls") or []
            content += message.get("content") or ""
            if tool_calls or chunk.get("done") or len(content.strip()) >= _DECISION_PROSE_STOP_CHARS:
                break
    return {"message": {"tool_calls": tool_calls, "content": content}}


async def decide_and_run_tool(agent: str, domain: str, msg: str, request_id: str | None = None, history: list | None = None) -> str | None:
    """Runs the tool-decision call for one user message. Returns a formatted
    context block to fold into the final answer's system prompt, or None if
    no tool was needed (or this domain skips the layer entirely)."""
    import httpx
    from tools import TOOL_DECISION_MODEL, TOOL_DECISION_SYSTEM, TOOL_NAMES
    from agent_spec import tools_for_agent, tool_allowed, is_registered_agent

    # No tool decision at all for an identity the router does not know (it would be offered nothing anyway).
    if not is_registered_agent(agent):
        return None

    # A person's own "yes" / "no" right after a fill plan: run or cancel that plan. No model is involved and the model's tool list has
    # no way to run a plan, so a plan can only ever run because a person said so.
    answer = pending_plan(history, msg) if tool_allowed(agent, domain, "fill_adobe_template") else None
    if answer:
        import adobe_template_tools
        from david_router import audit
        verdict, plan_id = answer
        if verdict == "yes":
            raw = await asyncio.get_running_loop().run_in_executor(None, lambda: adobe_template_tools.fill_adobe_template(plan_id=plan_id))
            audit({"action": "plan_confirmed", "tool": "fill_adobe_template", "plan_id": plan_id, "agent": agent, "request_id": request_id})
            text = adobe_template_tools.verbatim_text("fill_adobe_template", raw)
        else:
            adobe_template_tools.cancel_plan(plan_id)
            audit({"action": "plan_cancelled", "tool": "fill_adobe_template", "plan_id": plan_id, "agent": agent, "request_id": request_id})
            text = f"# Plan cancelled\n\nPlan {plan_id} was cancelled. Nothing was created. The original templates were not changed."
        return f"[VERBATIM:fill_adobe_template]\n{text}"

    # Only the tools THIS agent may use (agents/<agent>/role.md), never more than its domain allows. An agent without a
    # role.md keeps the old rule: all tools, or the read-only subset in the technical domain.
    tools = tools_for_agent(agent, domain)
    if not tools:
        return None

    payload = {
        "model": TOOL_DECISION_MODEL,
        "messages": [
            {"role": "system", "content": TOOL_DECISION_SYSTEM},
            {"role": "user", "content": msg},
        ],
        "tools": tools,
        "stream": True,
        "options": {"num_ctx": num_ctx_for(TOOL_DECISION_MODEL), "temperature": 0, "num_predict": _DECISION_MAX_TOKENS},
        # Same reasoning as layer1()'s think:false in david_router.py — this is a
        # single-shot tool/no-tool decision, not a task that benefits from a
        # visible reasoning trace. See num_ctx/think audit 2026-09-29.
        "think": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            data = await _read_tool_decision(client, payload)
    except Exception as e:
        from david_router import audit
        audit({"action": "tool_decision_error", "agent": agent, "domain": domain,
               "error": f"{type(e).__name__}: {e}", "request_id": request_id})
        return None

    tool_calls = data.get("message", {}).get("tool_calls") or []
    if not tool_calls:
        return None

    # Single-shot: only the first call, matching the "at most one lookup" scope.
    tc = tool_calls[0]
    fn = tc.get("function", {})
    tool_name = fn.get("name", "")
    tool_args = fn.get("arguments", {}) or {}
    if tool_name not in TOOL_NAMES or tool_name not in {t["function"]["name"] for t in tools}:
        return None
    if tool_name == "query_knowledge":
        # the model-written `agent` is untrusted input: scope by the agent that actually routed this turn
        tool_args = dict(tool_args, agent=agent)

    result = await execute_tool(tool_name, tool_args, agent=agent, domain=domain)

    from david_router import audit
    audit({"action": "tool_decision_call", "tool": tool_name, "args": tool_args,
           "result": result[:500], "agent": agent, "domain": domain,
           "request_id": request_id})

    from tools import VERBATIM_TOOL_NAMES
    if tool_name in VERBATIM_TOOL_NAMES:
        # code-built output: the reply IS this text, no model rewords it. The review sheet comes back as Markdown; the two checks
        # are rendered into the CFO's answer format. A sheet error (JSON) takes the normal path so the model can explain it.
        from tools import ADOBE_TOOL_NAMES, MENU_TOOL_NAMES
        if tool_name in ADOBE_TOOL_NAMES:
            import adobe_template_tools as _verb
        elif tool_name in MENU_TOOL_NAMES:
            import menu_tools as _verb
        else:
            import restaurant_tools as _verb
        text = _verb.verbatim_text(tool_name, result)
        if text:
            return f"[VERBATIM:{tool_name}]\n{text}"
    return f"[TOOL: {tool_name}]\n{result}"


# ── Web search ────────────────────────────────────────────────────────────────

def _search_web(query: str) -> str:
    # The query here is model-composed and may fold in content from a note or
    # email the planner just read — DLP has to gate this egress the same way it
    # gates the HUD's search and the cloud LLM tiers, or it's an exfil channel.
    from david_router import dlp_flag
    if dlp_flag(query):
        return "Search blocked: the query contains sensitive content (DLP) and was not sent to the web."
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append(f"**{r['title']}**\n{r['href']}\n{r['body']}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


# ── Knowledge base ────────────────────────────────────────────────────────────

# 8, not 5: measured recall of the right chunk on 100 questions was 83% at 5 and 87-89% at 8.
KNOWLEDGE_TOP_K = 8


def _query_knowledge(query: str, agent: str) -> str:
    """Search the acting agent's OWN vaults. Goes through rag.retrieve(), the one function that enforces the
    per-agent namespaces (config/vaults.toml), the relevance ceiling and the BM25+RRF fusion, so this tool can
    never see more than ordinary retrieval does (audit finding S-03, 2026-10-18: the old body searched every
    vault but 'research', memory episodes included, and always returned 8 rows whatever their relevance).
    An agent with no namespace (or an unknown name) gets nothing."""
    try:
        import sys as _sys
        if str(DAVID_HOME) not in _sys.path:
            _sys.path.insert(0, str(DAVID_HOME))
        import rag as _rag
        results = _rag.retrieve(agent, query, top_k=KNOWLEDGE_TOP_K)
        if not results:
            return "No relevant knowledge found."
        out = []
        for r in results:
            out.append(f"[{r.get('vault','?')}] {r.get('file','?')}\n{r.get('text','')[:400]}")
        return "\n\n---\n\n".join(out)
    except Exception as e:
        return f"Knowledge query failed ({type(e).__name__})."


# ── Notes ─────────────────────────────────────────────────────────────────────

def _safe_join(root: str, path: str) -> Path | None:
    """Resolve `path` under `root` and reject anything that escapes it —
    via ../ traversal or via an absolute path, which pathlib's `/` operator
    otherwise lets silently override `root` entirely."""
    # No ":" anywhere (2026-10-20, audit S-13 / SEC-B10): on NTFS "note.md:hidden" writes an alternate data stream, invisible
    # in Explorer, Obsidian and the indexer, and a drive letter ("C:x") is never a vault-relative path either.
    if ":" in str(path):
        return None
    base = Path(root).resolve()
    full = (base / path).resolve()
    if full != base and base not in full.parents:
        return None
    return full


def _strip_redundant_vault_prefix(path: str, vault: str) -> str:
    """The model sometimes repeats the vault name as the first path segment
    even though `vault` is already its own argument (e.g. path='business/x.md',
    vault='Business') — join that as-is and you get .../Business/business/x.md,
    a path that doesn't exist even though the note does. Strip the redundant
    segment before joining."""
    parts = path.replace("\\", "/").lstrip("/").split("/", 1)
    if len(parts) == 2 and parts[0].strip().lower() == vault.strip().lower():
        return parts[1]
    return path


def _read_note(vault: str, path: str) -> str:
    root = VAULT_ROOTS.get(vault.lower().replace(" ", "").replace("-", ""))
    if not root:
        return f"Unknown vault: {vault}. Valid: {list(VAULT_ROOTS.keys())}"
    path = _strip_redundant_vault_prefix(path, vault)
    full = _safe_join(root, path)
    if full is None:
        return f"Invalid path: escapes vault '{vault}'"
    # The HUD's chat transcripts (System/CRM/interactions) hold every agent's turns, firewalled agents included.
    # They are never indexed for the same reason (rag.is_excluded_path); the tool loop must not read them either,
    # or one agent could read what was said to another (audit finding S-04, 2026-10-18).
    import rag as _rag
    if _rag.is_excluded_path(str(full)) or _rag.is_excluded_path(str(full) + os.sep):
        return "Refusing to read: chat transcripts are not available to the tools."
    if not full.exists():
        return f"Note not found: {path}"
    return full.read_text(encoding="utf-8", errors="ignore")


def _write_note(vault: str, path: str, content: str, append: bool = False, overwrite: bool = False) -> str:
    vkey = vault.lower().replace(" ", "").replace("-", "")
    root = VAULT_ROOTS.get(vkey)
    if not root:
        return f"Unknown vault: {vault}"
    # A firewalled vault (config/vaults.toml [boundaries].firewalled; Research in the example policy) is never imported,
    # never sent to Tier 2/3. The tool loop is the surface most exposed to prompt injection,
    # so it must not be able to write there.
    if is_firewalled_vault(vkey):
        return f"Refusing to write: the {vault} vault is firewalled and read-only from the tool loop."
    path = _strip_redundant_vault_prefix(path, vault)
    full = _safe_join(root, path)
    if full is None:
        return f"Invalid path: escapes vault '{vault}'"
    # Overwrite is destructive and the vaults have no version history, so a
    # single hallucinated or injected write would permanently destroy a real
    # note. Require an explicit overwrite=true; steer ordinary edits to append.
    if full.exists() and not append and not overwrite:
        return (f"Refusing to overwrite the existing note '{path}' in {vault}. "
                f"Pass append=true to add to it, or overwrite=true to replace it deliberately.")
    full.parent.mkdir(parents=True, exist_ok=True)
    if append:
        # an interrupted append can only leave a partial tail; the existing text is never touched
        with open(full, "a", encoding="utf-8") as f:
            f.write("\n" + content)
        return f"Note appended: {full}"
    # A deliberate overwrite still keeps the old version: one bad tool call must not destroy a note for good.
    backup = _backup_note(root, full) if full.exists() else None
    _atomic_write_text(full, content)
    return f"Note written: {full}" + (f" (the previous version was saved as backup {backup})" if backup else "")


def _backup_note(root: str, full: Path) -> Path:
    """Copies a note about to be overwritten into NOTE_BACKUP_DIR/<vault folder>/<same subfolders>/<name>.<timestamp>.bak."""
    import shutil
    from datetime import datetime
    # Both sides resolved (2026-10-21): `full` comes resolved from _safe_join, and a root spelled differently (an 8.3 short
    # name, a junction) made relpath climb out with "..\.." and the backup land outside NOTE_BACKUP_DIR.
    base = Path(root).resolve()
    rel = Path(full).resolve().relative_to(base)
    folder = Path(NOTE_BACKUP_DIR) / base.name / rel.parent
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = folder / f"{rel.name}.{stamp}.bak"
    n = 1
    while backup.exists():
        n += 1
        backup = folder / f"{rel.name}.{stamp}-{n}.bak"
    shutil.copy2(full, backup)
    return backup


def _atomic_write_text(full: Path, content: str) -> None:
    """Writes through a temp file next to the note and swaps it in with os.replace, so a crash or a full disk mid-write leaves
    either the old note or the new one, never half of it. The temp name ends in .tmp, which the ingest never indexes."""
    tmp = full.with_name(f".{full.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(3):
            try:
                os.replace(tmp, full)
                break
            except PermissionError:          # Windows: another process (indexer, Obsidian) may hold the note for a moment
                if attempt == 2:
                    raise
                import time
                time.sleep(0.2)
    finally:
        if tmp.exists():
            tmp.unlink()


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_calendar() -> str:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        import datetime as dt

        token_path = str(DAVID_DATA / "google_token.json")
        if not os.path.exists(token_path):
            return "Google Calendar not connected. Token not found."
        creds = Credentials.from_authorized_user_file(token_path)
        service = build("calendar", "v3", credentials=creds)
        now = dt.datetime.utcnow().isoformat() + "Z"
        end = (dt.datetime.utcnow() + dt.timedelta(hours=24)).isoformat() + "Z"
        events_result = service.events().list(
            calendarId="primary", timeMin=now, timeMax=end,
            maxResults=10, singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        if not events:
            return "No events today."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            lines.append(f"• {start[:16]} — {e.get('summary', '(no title)')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Calendar error: {e}"


# ── Email ─────────────────────────────────────────────────────────────────────

def _get_email(count: int = 5) -> str:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        import base64

        token_path = str(DAVID_DATA / "google_token.json")
        if not os.path.exists(token_path):
            return "Gmail not connected. Token not found."
        creds = Credentials.from_authorized_user_file(token_path)
        service = build("gmail", "v1", credentials=creds)
        count = min(count, 10)
        result = service.users().messages().list(
            userId="me", labelIds=["INBOX", "UNREAD"], maxResults=count
        ).execute()
        messages = result.get("messages", [])
        if not messages:
            return "No unread emails."
        lines = []
        for msg in messages:
            m = service.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                                metadataHeaders=["From", "Subject", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            lines.append(f"From: {headers.get('From','?')}\nSubject: {headers.get('Subject','?')}\nDate: {headers.get('Date','?')}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"Email error: {e}"


# ── Task ledger ───────────────────────────────────────────────────────────────

def _get_tasks() -> str:
    try:
        conn = sqlite3.connect(TASKS_DB)
        rows = conn.execute(
            "SELECT id, title, status, agent, created_at FROM tasks ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        if not rows:
            return "No tasks in ledger."
        lines = []
        for r in rows:
            lines.append(f"[{r[0]}] {r[2].upper()} — {r[1]} (agent: {r[3]}, {r[4][:16]})")
        return "\n".join(lines)
    except Exception as e:
        return f"Task ledger error: {e}"


def _create_task(title: str, description: str, agent: str) -> str:
    # The assignee is model-written and the task later runs with that agent's prompt and vault access: registered agents only,
    # the same rule as delegate_to_agent (2026-10-20, audit P1-3).
    import agent_spec
    if not agent_spec.is_registered_agent(agent):
        return "Task creation refused: unknown agent."
    try:
        conn = sqlite3.connect(TASKS_DB)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, "
            "agent TEXT, status TEXT DEFAULT 'pending', created_at TEXT)"
        )
        import datetime as dt
        conn.execute(
            "INSERT INTO tasks (title, description, agent, created_at) VALUES (?,?,?,?)",
            (title, description, agent, dt.datetime.now().isoformat())
        )
        conn.commit()
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return f"Task #{task_id} created: '{title}' → assigned to {agent}"
    except Exception as e:
        return f"Task creation error: {e}"


# ── War rooms & agents ────────────────────────────────────────────────────────

async def _run_war_room(room: str, message: str) -> str:
    # The room name is model-written. /api/warroom accepts only its known rooms; this refuses anything that is not a plain room
    # name before it is sent at all (2026-10-20, audit P1-3).
    if not isinstance(room, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", room):
        return "War room refused: unknown room."
    try:
        import urllib.request
        import json as _json

        payload = _json.dumps({"room": room, "message": message}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/warroom",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        loop = asyncio.get_running_loop()
        resp_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=120).read()
        )
        data = _json.loads(resp_bytes)
        return data.get("synthesis", str(data))
    except Exception as e:
        return f"War room error: {e}"


async def _delegate_to_agent(agent: str, prompt: str) -> str:
    # The target must be a registered agent. The delegated agent answers through /api/chat_sync, which runs no tools,
    # so delegation can never hand the caller's tools (or any tools) to another agent.
    import agent_spec
    if not agent_spec.is_registered_agent(agent):
        return "Agent delegation refused: unknown agent."
    try:
        import urllib.request
        import json as _json

        payload = _json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "agent": agent,
            "mode": "A",
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/chat_sync",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        loop = asyncio.get_running_loop()
        resp_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=60).read()
        )
        data = _json.loads(resp_bytes)
        return data.get("response", str(data))
    except Exception as e:
        return f"Agent delegation error: {e}"


# ── Metrics ───────────────────────────────────────────────────────────────────

def _get_metrics() -> str:
    lines = []
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        lines.append(f"CPU: {cpu:.1f}%")
        lines.append(f"RAM: {ram.percent:.1f}% used ({ram.used // (1024**3):.1f}/{ram.total // (1024**3):.1f} GB)")
    except Exception:
        lines.append("CPU/RAM: psutil not available")
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            vals = result.stdout.strip().split(", ")
            if len(vals) >= 4:
                lines.append(f"GPU: {vals[0]}% load, VRAM: {int(vals[1])}/{int(vals[2])} MiB, Temp: {vals[3]}°C")
    except Exception:
        lines.append("GPU: nvidia-smi not available")
    return "\n".join(lines) if lines else "Metrics unavailable"


# ── Voice ─────────────────────────────────────────────────────────────────────

def _speak(text: str) -> str:
    try:
        from datetime import datetime
        queue = Path(SPEAK_QUEUE)
        queue.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        (queue / f"{ts}.txt").write_text(text.strip()[:800], encoding="utf-8")
        return f"Queued for speech: {text[:60]}{'...' if len(text) > 60 else ''}"
    except Exception as e:
        return f"Speak error: {e}"


# ── Image generation ──────────────────────────────────────────────────────────

def _generate_image(prompt: str, steps: int = 20) -> str:
    # Delegate to comfy_client — the previous hand-rolled workflow here was a
    # drifted duplicate of build_sdxl_workflow (fixed seed 42, no completion
    # polling, filename_prefix pointed outside ComfyUI's output dir).
    try:
        import sys as _sys
        if str(DAVID_HOME) not in _sys.path:
            _sys.path.insert(0, str(DAVID_HOME))
        from comfy_client import generate
        out_path = generate(prompt, steps=steps)
        if out_path:
            return f"Image generated: {out_path}"
        return "Image generation failed — is ComfyUI running?"
    except Exception as e:
        return f"Image generation error: {e}"
