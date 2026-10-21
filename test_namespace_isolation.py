"""
test_namespace_isolation.py -- what one agent may see stays with that agent (audit findings S-03 / S-04, P0-B).

Every record is SYNTHETIC and carries a canary string that names its class. DAVID_DATA / DAVID_VAULTS point at a
temporary folder before any David module is imported; the embedder is a deterministic offline fake (no Ollama), and
the relevance ceiling is opened wide on purpose, so only the ACCESS POLICY decides what comes back.

Covers: rag.retrieve, the query_knowledge tool, structured facts, episodic memory (shared / firewalled / unknown
origin / legacy rows), david_router.rag_context, read_note on chat transcripts, error messages, and the HUD's cloud
escalation with mocked providers (no network, no API key).

Run: python test_namespace_isolation.py
"""
import os, sys, json, tempfile, shutil, asyncio, hashlib, re, datetime

_TMP = tempfile.mkdtemp(prefix="david_test_isolation_")
os.environ["DAVID_DATA"] = os.path.join(_TMP, "data")
os.environ["DAVID_VAULTS"] = os.path.join(_TMP, "vaults")
for _k in ("DAVID_AUDIT_CONTENT", "DAVID_APP_DB_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)
os.makedirs(os.environ["DAVID_DATA"])

from david_config import DAVID_HOME, load_policy
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

import numpy as np  # noqa: E402

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail and not ok else ""))


class FakeEmbedder:
    """Deterministic hashed bag of words, 1024-d, unit length. Offline and repeatable."""
    def encode(self, texts, normalize_embeddings=True, is_query=False, **_):
        single = isinstance(texts, str)
        out = []
        for t in ([texts] if single else texts):
            v = np.zeros(1024, dtype=np.float32)
            for w in re.findall(r"[a-z0-9]+", str(t).lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return out[0] if single else np.array(out)


import embedding  # noqa: E402
embedding._embedder = FakeEmbedder()
import rag  # noqa: E402
import david_router as dr  # noqa: E402
import knowledge  # noqa: E402
import memory  # noqa: E402
import tool_executor as te  # noqa: E402

rag.MAX_RELEVANT_DISTANCE = 0.99          # open the relevance gate: only the policy may filter
_POL = load_policy()
FOLDER = _POL["vaults"]                  # namespace id -> folder

# ── synthetic corpus: one note per vault, each with a canary naming its vault ─────────────────────────────────────
TOPIC = "quarterly planning schedule review notes"
DOCS = {"system": "SYSCANARY", "business": "BIZCANARY", "music": "MUSCANARY", "lore": "LORECANARY", "research": "RESCANARY"}
for vault, canary in DOCS.items():
    d = os.path.join(os.environ["DAVID_VAULTS"], FOLDER[vault])
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{vault}_note.md")
    open(p, "w", encoding="utf-8").write(f"# {vault} note\n\n{TOPIC} {canary} {vault} vault record.\n")
    rag.ingest_file(vault, p)
crm = os.path.join(os.environ["DAVID_VAULTS"], "System", "CRM", "interactions")
os.makedirs(crm)
open(os.path.join(crm, "2026-10-18.md"), "w", encoding="utf-8").write("**You:** TRANSCRIPTCANARY told to performance_coach\n")
table = rag.get_table()
rag._ensure_fts(table)

# ── synthetic facts, one per domain (domain = the vault a fact was extracted from) ────────────────────────────────
knowledge.init_db()
FACTS = {"system": "FSYSCANARY", "business": "FBIZCANARY", "music": "FMUSCANARY", "lore": "FLORECANARY",
         "research": "FRESCANARY", "memory": "FMEMCANARY"}
with knowledge._conn() as c:
    for dom, canary in FACTS.items():
        c.execute("INSERT INTO facts (domain,entity,predicate,value,source,created_at) VALUES (?,?,?,?,?,?)",
                  (dom, f"{TOPIC} entity", "planning value", canary, f"{dom}/note.md", datetime.datetime.now().isoformat()))

# ── synthetic episodic memory ──────────────────────────────────────────────────────────────────────────────────────
# a legacy row (origin unknown), written exactly as the pre-patch code wrote rows
legacy_text = f"Session: legacy [session] -- {TOPIC} LEGACYCANARY"
table.add([{"id": "memory::legacy-1", "vault": "memory", "file": "episode_legacy.md", "chunk_index": 0, "text": legacy_text,
            "date_modified": "2026-10-01", "source_path": "memory::legacy-1", "asset_type": "memory",
            "vector": embedding._embedder.encode(legacy_text).tolist()}])
legacy_before = sorted((r["id"], r["text"], r["asset_type"]) for r in table.search().where("asset_type = 'memory'").limit(50).to_list())
# a firewalled row as the 2026-10-18..20 code wrote it: no owner in the id
oldfw_text = f"Session: fw [session] -- {TOPIC} OLDFWCANARY"
table.add([{"id": "memory::2026-10-20T17:37:29::memory_fw", "vault": "memory", "file": "episode_20261020_173729_memory_fw.md", "chunk_index": 0,
            "text": oldfw_text, "date_modified": "2026-10-20", "source_path": "memory::2026-10-20T17:37:29::memory_fw", "asset_type": "memory_fw",
            "vector": embedding._embedder.encode(oldfw_text).tolist()}])

memory._session_turns.clear()
memory._last_save_idx = 0
memory.add_turn("performance_coach", "personal", f"I have {TOPIC} and I take FWCANARY medicine")   # firewalled agent
memory.add_turn("analyst", "general", f"I prefer {TOPIC} as SHAREDCANARY bullet points")          # shared agent
memory.add_turn("not_an_agent", "general", f"I always {TOPIC} UNKCANARY")                          # unknown origin
saved = memory.save_episode("isolation-test")

AGENTS = list(dr.AGENTS)
FIREWALLED = set(dr.FIREWALLED)
MEM_CANARIES = {"SHAREDCANARY": "memory_shared", "FWCANARY": "memory_fw", "UNKCANARY": "memory_fw", "LEGACYCANARY": "memory"}
Q = f"{TOPIC} canary"

print("\n-- storage layout of the new episodes --")
rows = table.search().where("vault = 'memory'").limit(50).to_list()
by_kind = {}
for r in rows:
    by_kind.setdefault(r["asset_type"], []).append(r["text"])
new_fw = [r for r in rows if r["asset_type"] == "memory_fw" and "OLDFWCANARY" not in r["text"]]
check("a mixed session is saved as one shared row, one row for the firewalled agent and one unowned row for the unknown agent (P1-2)",
      len(by_kind.get("memory_shared", [])) == 1 and len(new_fw) == 2, str({k: len(v) for k, v in by_kind.items()}))
check("the shared row contains no firewalled or unknown-origin turn (no derived row mixes classes)",
      all("FWCANARY" not in t and "UNKCANARY" not in t for t in by_kind.get("memory_shared", [])))
own = [r for r in new_fw if memory.owner_of(r) == "performance_coach"]
check("performance_coach's turn is in its own row, named in the id and file (memory::<time>::memory_fw::performance_coach)",
      len(own) == 1 and "FWCANARY" in own[0]["text"] and "UNKCANARY" not in own[0]["text"]
      and own[0]["id"].endswith("::memory_fw::performance_coach") and own[0]["file"].endswith("_memory_fw_performance_coach.md"), str([r["id"] for r in new_fw]))
check("a turn by an unknown agent is stored as firewalled without an owner (fail closed)",
      any("UNKCANARY" in r["text"] and memory.owner_of(r) is None for r in new_fw))
check("legacy rows are left exactly as they were (no relabel, no rewrite)",
      sorted((r["id"], r["text"], r["asset_type"]) for r in table.search().where("asset_type = 'memory'").limit(50).to_list()) == legacy_before)

# ── 1. the two leaks the audit reproduced, shown at the storage layer first, then blocked ─────────────────────────
print("\n-- the original leaks (audit 2026-10-17) --")
unscoped = knowledge.query(Q)                            # the old call shape: no domain filter
check("[before] the business fact is reachable by an unscoped fact lookup (the old rag_context call)", "FBIZCANARY" in unscoped)
ctx = dr.rag_context("performance_coach", Q)
check("[after] performance_coach (vault: system) no longer receives the business fact", "FBIZCANARY" not in ctx and "BIZCANARY" not in ctx)
check("[after] ...but still receives its own system-vault fact and note", "FSYSCANARY" in ctx and "SYSCANARY" in ctx)
raw_fw = [r for r in rows if "FWCANARY" in r["text"]]
check("[before] the firewalled turn exists in the memory table", bool(raw_fw))
ctx = dr.rag_context("cfo", Q)
check("[after] cfo (cloud-eligible) no longer receives a turn told to performance_coach", "FWCANARY" not in ctx)
check("[after] cfo still receives shared memory", "SHAREDCANARY" in ctx)
old_style = [r["vault"] for r in table.search(embedding._embedder.encode(Q).tolist()).where("vault != 'research'", prefilter=True).limit(8).to_list()]
check("[before] the old query_knowledge search (all vaults but research) reaches business and memory rows", "business" in old_style or "memory" in old_style, str(old_style))
out = te._query_knowledge(Q, "performance_coach")
check("[after] query_knowledge for performance_coach returns only its own vault", "[system]" in out and not re.search(r"\[(business|music|lore|research|memory|books)\]", out), out[:200])

# ── 2. every agent x every class of record, derived from the live agent list and policy ───────────────────────────
print("\n-- policy matrix: every agent, every vault, every memory class --")
bad = []
for agent in AGENTS:
    allowed_vaults = rag.permitted_vaults(agent)
    namespaces = set(rag.AGENT_NAMESPACES.get(agent, []))
    ctx = dr.rag_context(agent, Q)
    qk = te._query_knowledge(Q, agent)
    for vault, canary in DOCS.items():
        for where, text in (("rag_context", ctx), ("query_knowledge", qk)):
            if canary in text and vault not in allowed_vaults:
                bad.append(f"{agent}: {where} leaked {vault} note")
    for dom, canary in FACTS.items():
        if canary in ctx and dom not in namespaces:
            bad.append(f"{agent}: fact from {dom}")
    if "[memory]" in qk:
        bad.append(f"{agent}: query_knowledge returned a memory episode")
    for canary, kind in MEM_CANARIES.items():
        if canary in ctx and kind not in memory.visible_types(agent):
            bad.append(f"{agent}: memory {kind}")
check("no agent receives a note, fact or memory outside its policy (17 agents x 5 vaults x 6 fact domains x 4 memory classes)", not bad, "; ".join(bad[:8]))
nonfw = [a for a in AGENTS if a not in FIREWALLED]
check("no non-firewalled agent receives a firewalled or unknown-origin turn",
      all("FWCANARY" not in dr.rag_context(a, Q) and "UNKCANARY" not in dr.rag_context(a, Q) for a in nonfw))
check("no non-firewalled agent receives legacy (unknown-provenance) memory", all("LEGACYCANARY" not in dr.rag_context(a, Q) for a in nonfw))
check("performance_coach recalls shared memory, its own firewalled turns, unowned firewalled rows and legacy rows",
      all(c in memory.recall(Q, top_k=8, agent="performance_coach") for c in ("SHAREDCANARY", "FWCANARY", "UNKCANARY", "OLDFWCANARY", "LEGACYCANARY")))
check("chief_of_staff (also firewalled) recalls shared, unowned and legacy memory, but NOT what was told to performance_coach (P1-2)",
      all(c in memory.recall(Q, top_k=8, agent="chief_of_staff") for c in ("SHAREDCANARY", "UNKCANARY", "OLDFWCANARY", "LEGACYCANARY"))
      and "FWCANARY" not in memory.recall(Q, top_k=8, agent="chief_of_staff").replace("OLDFWCANARY", ""))
holders = [a for a in AGENTS if "FWCANARY" in dr.rag_context(a, Q).replace("OLDFWCANARY", "")]
check("no agent other than performance_coach receives the turn it was told (rag_context, all 17 agents)",
      set(holders) <= {"performance_coach"}, str(holders))
check("may_recall: an owned firewalled row is for its owner only; an unowned one for every firewalled agent",
      memory.may_recall("performance_coach", own[0]) and not memory.may_recall("chief_of_staff", own[0]) and not memory.may_recall("cfo", own[0])
      and memory.may_recall("linguist", {"asset_type": "memory_fw", "source_path": "memory::x::memory_fw"})
      and not memory.may_recall("cfo", {"asset_type": "memory_fw", "source_path": "memory::x::memory_fw"}))
check("authorised retrieval still works: analyst gets business, system, music and lore notes",
      all(c in dr.rag_context("analyst", Q) + te._query_knowledge(Q, "analyst") for c in ("BIZCANARY", "SYSCANARY", "MUSCANARY", "LORECANARY")))
check("the research vault is reachable only by the agents that list it",
      all(("RESCANARY" in te._query_knowledge(Q, a)) == ("research" in rag.permitted_vaults(a)) for a in AGENTS))

# hybrid path: BM25 + RRF (+ cross-vault fallback) must hit the same final check
rag.MIN_FTS_SCORE = 0
try:
    leaks = [a for a in AGENTS for v, c in DOCS.items() if c in te._query_knowledge(Q, a) and v not in rag.permitted_vaults(a)]
    check("with BM25 + RRF fusion enabled the boundary still holds", not leaks, str(leaks[:5]))
finally:
    rag.MIN_FTS_SCORE = 15
# M-03: the configured cross-vault fallback also runs when BM25 finds nothing (it used to be skipped). lore_keeper reads
# lore + music; config gives lore a business fallback, so with only 2 own notes it reaches the business note. Research
# has no fallback and is never reached that way.
lk = te._query_knowledge(Q, "lore_keeper")
check("the configured cross-vault fallback works without BM25 hits (lore_keeper reaches business through lore's fallback)",
      "BIZCANARY" in lk and "LORECANARY" in lk and "RESCANARY" not in lk and "SYSCANARY" not in lk, lk[:160])
forged = [{"vault": "research", "text": "x", "id": "r"}, {"vault": "system", "text": "y", "id": "s"}]
check("the final authorization check drops rows from any vault the agent may not see", [r["vault"] for r in rag._authorized(forged, "performance_coach")] == ["system"])

# ── 3. missing / unknown identities fail closed ──────────────────────────────────────────────────────────────────
print("\n-- unknown and missing agents --")
for who in (None, "", "nobody", "ANALYST", "analyst "):
    check(f"agent {who!r}: rag_context, query_knowledge, memory and facts all return nothing",
          dr.rag_context(who, Q) == "" and te._query_knowledge(Q, who) == "No relevant knowledge found."
          and memory.recall(Q, agent=who) == "" and memory.visible_types(who) == ())
check("memory.recall without an agent returns nothing", memory.recall(Q) == "")
check("knowledge.recall without the agent's vault list returns nothing", knowledge.recall(Q) == "")
check("knowledge.recall with an empty vault list returns nothing", knowledge.recall(Q, domains=[]) == "")
check("the operator's CLI lookup (knowledge.query) keeps its all-domain default", "FBIZCANARY" in knowledge.query(Q))

# ── 4. secondary paths: transcripts, errors ──────────────────────────────────────────────────────────────────────
print("\n-- secondary access paths --")
te.VAULT_ROOTS = {name: os.path.join(os.environ["DAVID_VAULTS"], folder) for name, folder in FOLDER.items()}
r1 = te._read_note("system", "CRM/interactions/2026-10-18.md")
check("read_note refuses the chat transcripts (they hold every agent's turns)", "TRANSCRIPTCANARY" not in r1 and "Refusing" in r1, r1[:80])
check("read_note refuses the transcript folder spelled with backslashes", "TRANSCRIPTCANARY" not in te._read_note("system", "CRM\\interactions\\2026-10-18.md"))
check("read_note still reads an ordinary note", "SYSCANARY" in te._read_note("system", "system_note.md"))
check("a missing note's error names the relative path, not the absolute one", _TMP not in te._read_note("system", "nope.md"))
_real_retrieve = rag.retrieve
rag.retrieve = lambda *a, **k: (_ for _ in ()).throw(ValueError("RESCANARY secret detail"))
try:
    msg = te._query_knowledge(Q, "analyst")
finally:
    rag.retrieve = _real_retrieve
check("a retrieval error does not echo protected text", "RESCANARY" not in msg and "failed" in msg, msg)
log = open(dr.LOG, encoding="utf-8").read() if os.path.exists(dr.LOG) else ""
check("no protected canary reached the (temporary) audit log", not any(c in log for c in list(DOCS.values()) + list(FACTS.values()) + list(MEM_CANARIES)))

# ── 5. HUD cloud escalation with mocked providers ────────────────────────────────────────────────────────────────
print("\n-- cloud escalation (mocked providers, no network) --")
try:
    from starlette.testclient import TestClient
    import server as srv
    _OK = True
except Exception as ex:  # pragma: no cover
    _OK = False
    check("the HUD app can be imported for the escalation tests", False, f"{type(ex).__name__}: {ex}")

if _OK:
    captured = []

    def fake_cloud(agent, text, request_id=None, system=None, history=None):
        captured.append({"agent": agent, "text": text, "system": system or "", "history": history or []})
        yield "cloud answer"

    async def fake_stream(payload, timeout=120):
        yield {"message": {"content": "local answer"}, "done": True}

    async def no_tool(*a, **k):
        return None

    saved_attrs = {k: getattr(srv, k) for k in ("_claude_chunks", "_gemini_chunks", "_ollama_stream", "route_tier", "_RAG_OK")}
    real_decide = te.decide_and_run_tool
    srv._claude_chunks = srv._gemini_chunks = fake_cloud
    srv._ollama_stream = fake_stream
    srv.route_tier = lambda agent, text, needs_reasoning, req_id=None: 3
    srv._RAG_OK = True
    te.decide_and_run_tool = no_tool
    try:
        client = TestClient(srv.app, base_url="http://127.0.0.1:8000")

        def turn(agent, message):
            with client.websocket_connect("/ws/chat", headers={"host": "127.0.0.1"}) as ws:
                ws.send_json({"message": message, "agent": agent, "force_agent": True, "history": []})
                ev = []
                while True:
                    e = ws.receive_json()
                    ev.append(e)
                    if e.get("type") in ("done", "error"):
                        return ev

        ev = turn("executive_advisor", Q)
        payload = json.dumps(captured[-1]) if captured else ""
        check("the turn escalated to the (mocked) cloud provider", bool(captured) and captured[-1]["agent"] == "executive_advisor")
        check("the outbound payload carries the agent's authorised context (shared memory, its own vault note)",
              "SHAREDCANARY" in payload and "BIZCANARY" in payload, payload[:200])
        check("the outbound payload excludes firewalled, unknown-origin and legacy memory",
              all(c not in payload for c in ("FWCANARY", "UNKCANARY", "LEGACYCANARY")))
        check("the outbound payload excludes vaults outside the agent's policy (research, lore) and transcripts",
              all(c not in payload for c in ("RESCANARY", "FRESCANARY", "LORECANARY", "TRANSCRIPTCANARY")))
        # DLP still runs on the final payload: a secret that only arrives through injected (authorised) memory blocks escalation
        memory._session_turns.clear()
        memory._last_save_idx = 0
        memory.add_turn("analyst", "general", f"I keep {TOPIC} password: hunter2zebra in my notes")
        memory.save_episode("dlp-test")
        n_before = len(captured)
        ev = turn("executive_advisor", Q)
        full = next((e.get("full", "") for e in ev if e.get("type") == "done"), "")
        check("a DLP match inside injected context keeps the turn local (cloud provider not called)", len(captured) == n_before and "local answer" in full, f"cloud calls {len(captured) - n_before}")
        audit_log = open(dr.LOG, encoding="utf-8").read()
        check("the DLP block is audited without the secret", "dlp_block_escalation" in audit_log and "hunter2zebra" not in audit_log)
    finally:
        for k, v in saved_attrs.items():
            setattr(srv, k, v)
        te.decide_and_run_tool = real_decide

# ── 6. this test touched nothing outside its folder ──────────────────────────────────────────────────────────────
check("every write of this test stayed inside its temporary folder", rag.DB_PATH.startswith(_TMP) and dr.LOG.startswith(_TMP) and str(knowledge.DB_PATH if hasattr(knowledge, "DB_PATH") else _TMP).startswith(_TMP))

shutil.rmtree(_TMP, ignore_errors=True)
passed = sum(1 for r in results if r)
print(f"\n  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
