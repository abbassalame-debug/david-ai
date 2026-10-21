# memory.py — David episodic memory
# Tracks what the user said in a session and stores it, VERBATIM, in the LanceDB memory vault.
# Recalled automatically at query time and injected into the agent's context (shared turns for every agent;
# turns told to a firewalled agent only for firewalled agents -- see SHARED_TYPE / FIREWALLED_TYPE below).
#
# No LLM writes to this memory. An earlier version had llama "summarize" the session's
# questions into [FACT] bullets; it only ever saw the questions, so it invented facts from
# their wording ("starting cash must be verified at $12,500", "250 sandwiches sold") and
# recall() then fed them back to every agent as if true. Now an episode is the user's own
# words, quoted, so it cannot say anything the user didn't.
#
# Usage (automatic — called from david_router.py):
#   memory.add_turn(agent, domain, text)   — call after each routed query
#   memory.recall(query, agent=agent)      — returns formatted past context string the agent may see
#   memory.save_episode()                  — persist current session
#
# Manual save trigger: "save memory" / "remember this session" in david_router Layer 0.

from david_config import DAVID_HOME
import os
import re
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8")

VAULT     = "memory"
MAX_QUOTE = 220  # chars kept per turn — pasted documents must not fill the memory
MAX_TURNS = 15   # auto-save threshold (save mid-session if it gets long) — lowered
                 # from 40; this counter is in-process RAM only (see server.py's
                 # shutdown handler for why that matters) and shared across every
                 # conversation/agent, not per-thread, so 40 risked losing a lot on
                 # an unexpected crash before the shutdown save was added.

_session_turns = []   # list of {time, agent, domain, text}

# Visibility of saved episodes (audit finding S-04, 2026-10-18). Every row carries one of these in `asset_type`:
#   memory_shared -- only turns by agents that are NOT firewalled: recalled for every agent (shared memory, as before)
#   memory_fw     -- turns by a FIREWALLED agent (david_router.FIREWALLED), or by an unknown agent: recalled only for
#                    firewalled agents, so what was told to e.g. performance_coach never reaches cfo's context
#   memory        -- rows saved before this change. Their origin is unknown, so they are treated like memory_fw:
#                    firewalled agents still recall them, other agents no longer do. The rows are NOT changed.
SHARED_TYPE = "memory_shared"
FIREWALLED_TYPE = "memory_fw"
LEGACY_TYPE = "memory"

# Per-agent firewalled memory (2026-10-21, audit P1-2): a firewalled agent's turns are saved one row per agent, the agent named
# in the row id (memory::<time>::memory_fw::<agent>), and only that agent recalls them, so what was told to performance_coach no
# longer reaches chief_of_staff or linguist. Rows without an owner (every memory_fw row saved before this change, and turns by
# an unknown agent) stay visible to every firewalled agent, like the legacy rows (owner's decision, 2026-10-21).
OWNER_SEP = "::" + FIREWALLED_TYPE + "::"


def owner_of(row) -> str | None:
    """The agent a memory_fw row belongs to, or None for a row without an owner."""
    sp = str(row.get("source_path") or row.get("id") or "")
    return sp.split(OWNER_SEP, 1)[1] if OWNER_SEP in sp else None


def may_recall(agent, row) -> bool:
    """Final per-row check behind recall(): the row's type must be visible to `agent`, and a firewalled row with an owner
    is recalled by that owner only."""
    if row.get("asset_type") not in visible_types(agent):
        return False
    if row.get("asset_type") == FIREWALLED_TYPE:
        owner = owner_of(row)
        return owner is None or owner == agent
    return True


def _agent_sets():
    # From agent_spec's registry (filled by david_router at import), never by importing david_router here: when the
    # router runs as __main__ (the CLI), a second import would execute the whole module again.
    import agent_spec
    return set(agent_spec.registered_agents()), set(agent_spec.firewalled_agents())


def visible_types(agent) -> tuple:
    """The episode types `agent` may recall. Unknown or missing agent -> none (fail closed)."""
    agents, firewalled = _agent_sets()
    if not isinstance(agent, str) or agent not in agents:
        return ()
    if agent in firewalled:
        return (SHARED_TYPE, FIREWALLED_TYPE, LEGACY_TYPE)
    return (SHARED_TYPE,)


_last_save_idx = 0    # tracks which turns have already been saved


# ── Session tracking ──────────────────────────────────────────────────────────

def add_turn(agent: str, domain: str, text: str) -> None:
    _session_turns.append({
        "time":   datetime.datetime.now().strftime("%H:%M"),
        "agent":  agent,
        "domain": domain,
        "text":   text,
    })
    if len(_session_turns) - _last_save_idx >= MAX_TURNS:
        save_episode(label="auto-checkpoint")


# ── Build the episode (verbatim, no LLM) ──────────────────────────────────────

# A turn is a "statement" (preference, decision, personal/business fact, standing instruction)
# rather than a question or request when it isn't a question and contains one of these markers.
_STATEMENT_RE = re.compile(
    r"\bi (?:prefer|like|love|hate|want|need|always|never|usually|am|have)\b|\bi'm\b|\bi've\b"
    r"|\bwe (?:are|have|decided|use|need)\b|\bwe're\b"
    r"|\bmy (?:name|company|business|restaurant|shop|team|project|app|goal)\b"
    r"|\bour (?:company|business|restaurant|shop|team|project|app|goal)\b"
    r"|\bremember\b|\bfrom now on\b|\balways\b|\bnever\b|\bgoing forward\b|\blet's\b|\blet us\b|\bdecided\b",
    re.I,
)


def _is_statement(text: str) -> bool:
    return not text.rstrip().endswith("?") and bool(_STATEMENT_RE.search(text))


def _build_episode(turns: list, label: str) -> str:
    seen, stated, asked = set(), [], []
    for t in turns:
        text = " ".join(str(t["text"]).split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        line = f"- [{t['time']}] {text[:MAX_QUOTE]}" + ("…" if len(text) > MAX_QUOTE else "")
        (stated if _is_statement(text) else asked).append(line)
    if not stated and not asked:
        return ""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"Session: {date_str} [{label}] — the user's own words from the chat log, quoted "
             "(these are what the user said or asked, not verified facts)"]
    if stated:
        parts += ["User statements:"] + stated[:12]
    if asked:
        parts += ["User asked / requested:"] + asked[:12]
    return "\n".join(parts)


# ── Persist ───────────────────────────────────────────────────────────────────

def save_episode(label: str = "session") -> str:
    global _last_save_idx, _session_turns
    save_point = len(_session_turns)   # snapshot first so turns appended mid-save aren't marked saved-but-lost
    unsaved = _session_turns[_last_save_idx:save_point]
    if not unsaved:
        return ""

    # One shared episode, plus one firewalled episode per firewalled agent (P1-2). Turns by an unknown agent go to a firewalled
    # episode without an owner. A firewalled agent's turns never share a row with shared turns or with another agent's turns.
    agents, firewalled = _agent_sets()
    groups = {}
    for t in unsaved:
        a = t.get("agent")
        if a in agents and a not in firewalled:
            key = (SHARED_TYPE, None)
        else:
            key = (FIREWALLED_TYPE, a if a in firewalled else None)
        groups.setdefault(key, []).append(t)
    episodes = [(kind, owner, _build_episode(turns, label)) for (kind, owner), turns in groups.items() if turns]
    episodes = [(kind, owner, text) for kind, owner, text in episodes if text]
    if not episodes:
        _session_turns = _session_turns[save_point:]
        _last_save_idx = 0
        return ""

    try:
        sys.path.insert(0, str(DAVID_HOME))
        import rag as rag_module

        # lancedb/pyarrow must load before torch — see rag.py note
        table    = rag_module.get_table()
        embedder = rag_module.get_embedder()
        now      = datetime.datetime.now()
        rows = []
        for kind, owner, text in episodes:
            ep_id = f"memory::{now.isoformat()}::{kind}" + (f"::{owner}" if owner else "")
            rows.append({
                "id":            ep_id,
                "vault":         VAULT,
                "file":          f"episode_{now.strftime('%Y%m%d_%H%M%S')}_{kind}" + (f"_{owner}" if owner else "") + ".md",
                "chunk_index":   0,
                "text":          text,
                "date_modified": now.strftime("%Y-%m-%d"),
                "source_path":   ep_id,
                "asset_type":    kind,
                "vector":        embedder.encode(text, normalize_embeddings=True).tolist(),
            })
        table.add(rows)                  # one call: both rows are written or neither is
        # No FTS rebuild here — _ensure_fts() re-indexes the whole table, which
        # pegged the CPU every MAX_TURNS turns. New episodes are still found by
        # vector search immediately; they join the FTS index on the next explicit
        # ingest job (rag.ingest / turbo_ingest), which is where _ensure_fts lives.
        # Drop everything through save_point — it's persisted now and no longer
        # needs to live in RAM; recall() reads from LanceDB, not from this list.
        _session_turns = _session_turns[save_point:]
        _last_save_idx = 0
        print(f"  [Memory] Episode saved ({len(unsaved)} turns → {label}).")
    except Exception as e:
        print(f"  [Memory] Save failed ({type(e).__name__}).")
        return ""

    return "\n\n".join(text for _, _, text in episodes)


# ── Recall ────────────────────────────────────────────────────────────────────

def recall(query: str, top_k: int = 2, agent: str = None) -> str:
    """Past episodes relevant to `query` that `agent` may see (visible_types). No agent -> nothing."""
    kinds = visible_types(agent)
    if not kinds:
        return ""
    where = f"vault = '{VAULT}' AND asset_type IN ({', '.join(repr(k) for k in kinds)})"
    if FIREWALLED_TYPE in kinds:
        # another firewalled agent's own rows are left out before the search (may_recall() checks every row again below)
        where += (f" AND (asset_type != '{FIREWALLED_TYPE}' OR source_path NOT LIKE '%{OWNER_SEP}%'"
                  f" OR source_path LIKE '%{OWNER_SEP}{agent}')")
    try:
        sys.path.insert(0, str(DAVID_HOME))
        import rag as rag_module

        table = rag_module.get_table()
        try:
            count = table.count_rows(filter=where)
        except Exception:
            count = 0
        if count == 0:
            return ""

        embedder = rag_module.get_embedder()
        vec      = embedder.encode(query, normalize_embeddings=True, is_query=True).tolist()

        # Same fix as rag.retrieve(): plain cosine search always returns
        # `top_k` rows even when nothing is actually relevant — this is a
        # separate query path that bypassed rag.retrieve()'s relevance filter
        # entirely, so it needs the identical threshold applied here too
        # (reuses rag.MAX_RELEVANT_DISTANCE, calibrated against this same
        # embedding model, rather than a second guessed constant).
        results = [
            r for r in (
                table.search(vec, vector_column_name="vector")
                     .metric("cosine")
                     .where(where, prefilter=True)
                     .limit(top_k)
                     .to_list()
            )
            if r.get("_distance", 0) <= rag_module.MAX_RELEVANT_DISTANCE and may_recall(agent, r)
        ]
        if not results:
            return ""

        lines = ["[Relevant memory from past sessions — the user's own earlier words, quoted. They are NOT verified facts; do not present them as true:]"]
        for r in results:
            lines.append(r["text"])
        return "\n\n".join(lines)

    except Exception:
        return ""
