# agent_loop.py — ReAct (Reason + Act) loop for David agentic mode

import asyncio
import json
import re
import urllib.request

from tool_executor import execute_tool
from tools import TOOLS
from agent_spec import tools_for_agent
from david_router import REASONING_MODEL, audit
from ollama_config import num_ctx_for, OLLAMA_HOST

OLLAMA_URL = OLLAMA_HOST + "/api/chat"
PLANNING_MODEL = "qwen3.5:9b"
MAX_ITER = 5

# Strips everything through the first </think> — covers a clean <think>...</think>
# block AND a bare stray </think> with garbled reasoning-model tokens before it
# (observed from deepseek-r1:14b in the max_iter fallback below, which replays a
# tool-call-laden history through a plain chat call with no separate "thinking"
# field to lean on). No-ops when there's no </think> at all.
_THINK_LEAK_RE = re.compile(r"^[\s\S]*?</think>\s*")


# ── Ollama calls ──────────────────────────────────────────────────────────────

def _sync_call(messages: list, model: str, tools: list | None = None) -> dict:
    """Blocking non-streaming Ollama call. Run in executor, not on the event loop."""
    # Without num_ctx Ollama uses its small default context; planning turns that
    # carry several tool results silently truncate from the front, and the model
    # loses the original instruction mid-loop.
    payload = {"model": model, "messages": messages, "stream": False,
               "think": False, "options": {"num_ctx": num_ctx_for(model)}}
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode()
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


# ── Main ReAct loop ───────────────────────────────────────────────────────────

async def run(
    initial_messages: list,
    ws,
    mode: str = "A",
    agent: str = "analyst",
    domain: str = "general",
    model: str | None = None,
    max_iter: int = MAX_ITER,
    request_id: str | None = None,
) -> str:
    """
    Run the ReAct loop.

    Sends WebSocket events:
      {"type": "tool_use",    "tool": name, "args": {...}}
      {"type": "tool_result", "tool": name, "result": str}
      {"type": "token",       "token": str}
      {"type": "done",        "full": str, "model": str}

    Returns the final full response text.
    """
    if model is None:
        model = _pick_model(mode)

    loop = asyncio.get_running_loop()
    messages = list(initial_messages)
    full_response = ""
    agent_tools = tools_for_agent(agent, domain)    # this agent's own list (agents/<agent>/role.md), not every tool

    for iteration in range(max_iter):
        # Non-streaming planning call with tools
        try:
            resp = await loop.run_in_executor(
                None, lambda: _sync_call(messages, PLANNING_MODEL, agent_tools)
            )
        except Exception as e:
            await ws.send_json({"type": "token", "token": f"\n[Agent planning error: {e}]"})
            final_model = PLANNING_MODEL
            break

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Model decided to respond directly — stream the content
            content = msg.get("content") or ""
            if content:
                # Word-chunk the content as streaming tokens
                words = content.split(" ")
                for word in words:
                    token = word + " "
                    await ws.send_json({"type": "token", "token": token})
                    full_response += token
                # Trim trailing space
                full_response = full_response.rstrip()
            final_model = PLANNING_MODEL
            break

        # Append the assistant planning turn (with tool_calls)
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

        # Execute each tool call
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            tool_args = fn.get("arguments", {})

            await ws.send_json({"type": "tool_use", "tool": tool_name, "args": tool_args})

            result = await execute_tool(tool_name, tool_args, agent=agent, domain=domain)

            await ws.send_json({"type": "tool_result", "tool": tool_name, "result": result[:500]})

            # Tool calls (write_note, start_process, etc.) are otherwise only
            # visible over this websocket and vanish once it closes — this is
            # the only durable record of what the agent actually did.
            audit({
                "action": "tool_call", "tool": tool_name, "args": tool_args,
                "result": result[:500], "agent": agent, "request_id": request_id,
            })

            # Inject tool result into conversation
            messages.append({"role": "tool", "content": result})

    else:
        # max_iter reached — this message history now has assistant turns with
        # tool_calls and tool-role results mixed in, but no `tools` schema is
        # passed this time (it's a plain chat completion, not another planning
        # call). A reasoning model's chat template can react to that odd shape
        # by dropping its <think>...</think> separation entirely, so the raw
        # tag (or a stray fragment of it) ends up in content — get the whole
        # answer first and sanitize it before anything reaches the user, same
        # as the word-chunk reveal above.
        await ws.send_json({"type": "token", "token": "\n"})
        try:
            resp = await loop.run_in_executor(None, lambda: _sync_call(messages, model))
            content = resp.get("message", {}).get("content") or ""
        except Exception as e:
            content = f"[Agent fallback error: {e}]"
        content = _THINK_LEAK_RE.sub("", content).strip()
        if content:
            words = content.split(" ")
            for word in words:
                token = word + " "
                await ws.send_json({"type": "token", "token": token})
                full_response += token
            full_response = full_response.rstrip()
        final_model = model

    if not full_response:
        full_response = "[No response generated]"

    await ws.send_json({"type": "done", "full": full_response, "model": f"agent/{final_model}"})
    return full_response


# ── Model picker ──────────────────────────────────────────────────────────────

def _pick_model(mode: str) -> str:
    if mode == "B":
        return REASONING_MODEL
    return PLANNING_MODEL
