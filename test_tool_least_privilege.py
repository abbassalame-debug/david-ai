"""
test_tool_least_privilege.py -- every agent x every tool, enforced at execution time (audit findings S-05 / S-06, P0-C).

The agent list comes from the router (agent_spec.registered_agents) and the tool list from tools.TOOL_NAMES, so a new
agent or tool without an expected policy fails this test instead of slipping through. Every tool implementation is
replaced by a recording sentinel: an allowed call must reach it, a denied call must be refused BEFORE it runs.
Synthetic data only; DAVID_DATA / DAVID_VAULTS are temporary; no model, network or HUD socket is used.

Run: python test_tool_least_privilege.py
"""
import os, sys, json, tempfile, shutil, asyncio, types

_TMP = tempfile.mkdtemp(prefix="david_test_lpriv_")
os.environ["DAVID_DATA"] = os.path.join(_TMP, "data")
os.environ["DAVID_VAULTS"] = os.path.join(_TMP, "vaults")
for _k in ("DAVID_AUDIT_CONTENT", "DAVID_APP_DB_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)
os.makedirs(os.environ["DAVID_DATA"])

from david_config import DAVID_HOME
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail and not ok else ""))


import david_router as dr          # noqa: E402  (fills agent_spec's registry)
import agent_spec as A             # noqa: E402
import tools as T                  # noqa: E402
import tool_executor as te         # noqa: E402
import process_manager             # noqa: E402
import restaurant_tools, menu_tools, adobe_template_tools   # noqa: E402,E401

# ── the approved policy (general domain). Changing a role.md must change this table on purpose. ──────────────────
EXPECTED = {
    "chief_of_staff":      {"search_web", "query_knowledge", "read_note", "write_note", "get_calendar", "get_email", "get_tasks", "create_task",
                            "run_war_room", "delegate_to_agent", "start_process", "stop_process", "process_status", "get_metrics", "speak"},
    "executive_advisor":   {"query_knowledge", "read_note", "search_web", "get_tasks"},
    "venture_architect":   {"query_knowledge", "read_note", "search_web"},
    "legal_counsel":       {"query_knowledge", "read_note", "search_web"},
    "brand_audience":      {"query_knowledge", "read_note", "search_web"},
    "beat_architect":      {"query_knowledge", "read_note", "write_note", "search_web"},
    "acoustician":         {"query_knowledge", "read_note", "search_web"},
    "lore_keeper":         {"query_knowledge", "read_note", "write_note", "generate_image"},
    "performance_coach":   {"query_knowledge", "read_note", "get_calendar", "get_tasks", "create_task"},
    "linguist":            {"query_knowledge", "read_note"},
    "language_specialist": {"query_knowledge", "read_note", "search_web"},
    # unchanged by this patch (already tailored before 2026-10-18)
    "analyst":             {"query_knowledge", "read_note", "search_web", "get_calendar", "get_email", "get_tasks", "get_metrics", "process_status",
                            "get_menu_glossary", "match_menu_item", "validate_draft_package", "render_review_sheet"},
    "cfo":                 {"query_knowledge", "read_note", "search_web", "get_tasks", "reconcile_cash", "check_meat_variance"},
    "creative_director":   {"query_knowledge", "read_note", "search_web", "list_adobe_templates", "fill_adobe_template", "export_adobe_preview"},
    "developer":           {"query_knowledge", "read_note", "get_calendar", "get_email", "get_tasks", "process_status", "get_metrics"},
    "prompt_architect":    {"query_knowledge", "read_note", "get_calendar", "get_email", "get_tasks", "process_status", "get_metrics"},
    "uiux_engineer":       {"query_knowledge", "read_note", "list_adobe_templates", "export_adobe_preview", "review_adobe_preview"},
}
# The technical domain is read-only: only read-only tools and the side-effect-free job tools survive there.
TECH_OK = T.READ_ONLY_TOOL_NAMES | {t["function"]["name"] for t in T.AGENT_ONLY_TOOLS}

AGENTS = sorted(A.registered_agents())
TOOLS = sorted(T.TOOL_NAMES)
check("every registered agent has an explicit expected policy (no agent slips through)", set(AGENTS) == set(EXPECTED), str(set(AGENTS) ^ set(EXPECTED)))
check("every expected tool is a registered tool", all(t in T.TOOL_NAMES for s in EXPECTED.values() for t in s))

# ── sentinels: every implementation records that it ran ──────────────────────────────────────────────────────────
RAN = []


def _sync(name):
    def f(*a, **k):
        RAN.append(name)
        return f"{name}-ok"
    return f


def _async(name):
    async def f(*a, **k):
        RAN.append(name)
        return f"{name}-ok"
    return f


for fn, tool in (("_search_web", "search_web"), ("_query_knowledge", "query_knowledge"), ("_read_note", "read_note"),
                 ("_write_note", "write_note"), ("_get_calendar", "get_calendar"), ("_get_email", "get_email"),
                 ("_get_tasks", "get_tasks"), ("_create_task", "create_task"), ("_get_metrics", "get_metrics"),
                 ("_speak", "speak"), ("_generate_image", "generate_image")):
    setattr(te, fn, _sync(tool))
te._run_war_room = _async("run_war_room")
te._delegate_to_agent_real = te._delegate_to_agent
te._delegate_to_agent = _async("delegate_to_agent")
process_manager.start_process = _sync("start_process")
process_manager.stop_process = _sync("stop_process")
process_manager.get_all_status = lambda: (RAN.append("process_status"), {})[1]
process_manager.get_status = lambda n: (RAN.append("process_status"), {})[1]
restaurant_tools.call_tool = lambda name, args: (RAN.append(name), "{}")[1]
menu_tools.call_tool = lambda name, args: (RAN.append(name), "{}")[1]
adobe_template_tools.call_tool = lambda name, args: (RAN.append(name), "{}")[1]
AUDITS = []
dr.audit = AUDITS.append           # tool_executor imports audit from david_router at call time

ARGS = {"search_web": {"query": "q"}, "query_knowledge": {"query": "q"}, "read_note": {"vault": "system", "path": "a.md"},
        "write_note": {"vault": "system", "path": "a.md", "content": "c"}, "create_task": {"title": "t", "description": "d", "agent": "cfo"},
        "run_war_room": {"room": "build-room", "message": "m"}, "delegate_to_agent": {"agent": "cfo", "prompt": "p"},
        "start_process": {"name": "file_watcher"}, "stop_process": {"name": "file_watcher"}, "speak": {"text": "hi"},
        "generate_image": {"prompt": "a bike"}}


def run(tool, agent, domain="general", args=None):
    RAN.clear()
    AUDITS.clear()
    a = dict(ARGS.get(tool, {})) if args is None else (dict(args) if isinstance(args, dict) else args)
    out = asyncio.run(te.execute_tool(tool, a, agent=agent, domain=domain))
    return out, list(RAN), list(AUDITS)


# ── 1. the full matrix ──────────────────────────────────────────────────────────────────────────────────────────
print("\n-- the matrix: every registered agent x every registered tool, general and technical domain --")
wrong, combos = [], 0
for domain in ("general", "technical"):
    for agent in AGENTS:
        allowed = EXPECTED[agent] if domain == "general" else EXPECTED[agent] & TECH_OK
        for tool in TOOLS:
            combos += 1
            out, ran, audits = run(tool, agent, domain)
            if tool in allowed:
                if ran != [tool]:
                    wrong.append(f"{domain}/{agent}/{tool}: allowed but did not run ({out[:60]})")
            else:
                if ran or "not permitted" not in out or not any(a.get("action") == "tool_refused" for a in audits):
                    wrong.append(f"{domain}/{agent}/{tool}: denied tool ran or was not refused+audited ({ran}, {out[:60]})")
check(f"all {combos} agent/tool/domain combinations follow the policy (allowed ones run, denied ones never reach the implementation)",
      not wrong, "; ".join(wrong[:6]))
check("the offered tool schemas equal the policy for every agent (what the model sees = what may run)",
      all({t["function"]["name"] for t in A.tools_for_agent(a, "general")} == EXPECTED[a] for a in AGENTS))
holders = lambda tool: sorted(a for a in AGENTS if tool in EXPECTED[a])
check("process control (start/stop_process) belongs to chief_of_staff only", holders("start_process") == holders("stop_process") == ["chief_of_staff"])
check("note writing is limited to three agents", holders("write_note") == ["beat_architect", "chief_of_staff", "lore_keeper"], str(holders("write_note")))
check("delegation and war rooms belong to chief_of_staff only", holders("delegate_to_agent") == holders("run_war_room") == ["chief_of_staff"])
check("language_specialist keeps web search (approved) and it runs", "search_web" in EXPECTED["language_specialist"] and run("search_web", "language_specialist")[1] == ["search_web"])
check("no agent holds email unless its role is an operator or read-only assistant", holders("get_email") == ["analyst", "chief_of_staff", "developer", "prompt_architect"], str(holders("get_email")))

# ── 2. identities that must fail closed ─────────────────────────────────────────────────────────────────────────
print("\n-- missing, unknown and look-alike identities --")
for who in (None, "", "nobody", "ANALYST", "analyst ", "chief_of_staff​", 123):
    leaked = [t for t in TOOLS if run(t, who)[1]]
    check(f"agent {who!r}: every tool is refused before it runs", not leaked, str(leaked))
check("an unknown agent is offered no tool schemas", A.tools_for_agent("nobody", "general") == [] and A.tools_for_agent(None) == [])
for bad in ("rm_rf", "", "write_note ", "__import__"):
    out, ran, audits = run(bad, "chief_of_staff", args={})
    check(f"unregistered tool name {bad!r} is refused", not ran and "not permitted" in out and audits and audits[0].get("reason") == "unknown tool")

# ── 3. missing and malformed role files ─────────────────────────────────────────────────────────────────────────
print("\n-- missing and malformed role files --")
tmp_agents = os.path.join(_TMP, "agents")
shutil.copytree(A.AGENTS_DIR, tmp_agents)
cases = {
    "missing role.md": None,
    "tools_allow without brackets": "---\nid: chief_of_staff\ntools_allow: write_note, start_process\n---\nbody",
    "no tools_allow line": "---\nid: chief_of_staff\n---\nbody",
    "id does not match the folder": "---\nid: analyst\ntools_allow: [write_note, start_process]\n---\nbody",
    "no front-matter at all": "write_note start_process",
    "a wildcard that is not a tool": "---\nid: chief_of_staff\ntools_allow: [all, '*']\n---\nbody",
}
orig_dir = A.AGENTS_DIR
for label, content in cases.items():
    role = os.path.join(tmp_agents, "chief_of_staff", "role.md")
    if os.path.exists(role):
        os.remove(role)
    if content is not None:
        open(role, "w", encoding="utf-8").write(content)
    A.AGENTS_DIR = tmp_agents
    A._cache.clear()
    try:
        leaked = [t for t in TOOLS if run(t, "chief_of_staff")[1]]
        offered = A.tools_for_agent("chief_of_staff", "general")
    finally:
        A.AGENTS_DIR = orig_dir
        A._cache.clear()
    check(f"{label}: chief_of_staff gets no tools at all (nothing offered, nothing runs)", not leaked and offered == [], str(leaked))

# ── 4. identity cannot be changed by the model; policy re-checked at execution ──────────────────────────────────
print("\n-- the model cannot change who is acting --")


class _Resp:
    def __init__(self, d):
        self._d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def _client(calls, on_post=None):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            if on_post:
                on_post()
            return _Resp({"message": {"content": "", "tool_calls": calls}})

        def stream(self, method, url, json=None, **kw): return _Streamed(self.post(url, json=json))
    return C


class _Streamed:
    """The streamed form of a faked reply (the tool decision streams since 2026-10-20): one NDJSON line with the whole message."""
    def __init__(self, reply): self.reply = reply
    async def __aenter__(self):
        self.resp = await self.reply
        return self
    async def __aexit__(self, *a): return False
    def raise_for_status(self): self.resp.raise_for_status()
    async def aiter_lines(self):
        yield json.dumps({**self.resp.json(), "done": True})


import httpx  # noqa: E402
_real_client = httpx.AsyncClient
seen_agent = {}
te._query_knowledge = lambda q, agent: (seen_agent.setdefault("agent", agent), RAN.append("query_knowledge"), "ok")[2]
try:
    httpx.AsyncClient = _client([{"function": {"name": "query_knowledge", "arguments": {"query": "x", "agent": "linguist"}}}])
    asyncio.run(te.decide_and_run_tool("analyst", "general", "find it", request_id="t"))
    check("a model-written agent name inside the arguments does not change the acting agent", seen_agent.get("agent") == "analyst", str(seen_agent))
    RAN.clear()
    out = asyncio.run(te.execute_tool("query_knowledge", {"query": "x", "agent": "linguist"}, agent="analyst"))
    check("execute_tool scopes query_knowledge by the caller, never by args['agent']", seen_agent.get("agent") == "analyst")
    # a model that asks for a tool its agent may not use, under another agent's name
    RAN.clear()
    httpx.AsyncClient = _client([{"function": {"name": "start_process", "arguments": {"name": "x", "agent": "chief_of_staff"}}}])
    r = asyncio.run(te.decide_and_run_tool("linguist", "research", "start it", request_id="t"))
    check("a forbidden tool requested 'as chief_of_staff' by the linguist's turn is ignored", r is None and not RAN)
    # the policy changes between the tool decision and the execution: the executor's own check wins
    RAN.clear()
    real_allowed = A.tool_allowed

    def _revoke():
        A.tool_allowed = lambda agent, domain, name: False
    httpx.AsyncClient = _client([{"function": {"name": "write_note", "arguments": ARGS["write_note"]}}], on_post=_revoke)
    r = asyncio.run(te.decide_and_run_tool("chief_of_staff", "general", "save it", request_id="t"))
    A.tool_allowed = real_allowed
    check("a permission revoked between decision and execution is enforced at execution", "not permitted" in (r or "") and "write_note" not in RAN, str(r))
finally:
    httpx.AsyncClient = _real_client

# ── 5. tool errors do not leak arguments or content ─────────────────────────────────────────────────────────────
print("\n-- tool errors --")
te._get_tasks = lambda: (_ for _ in ()).throw(ValueError("SECRET-TASK-CONTENT"))
out, ran, _ = run("get_tasks", "cfo")
check("a tool that raises returns its error type only, not the message", "ValueError" in out and "SECRET-TASK-CONTENT" not in out, out)
out, ran, _ = run("write_note", "chief_of_staff", args={"vault": "system"})
check("missing arguments give a clean error without echoing values", out.startswith("Tool error (write_note)"), out)
out, ran, _ = run("write_note", "chief_of_staff", args=["not", "a", "dict"])
check("non-object arguments are rejected before the tool runs", "arguments must be an object" in out and not ran, out)

# ── 6. delegation ───────────────────────────────────────────────────────────────────────────────────────────────
print("\n-- delegation --")
import urllib.request  # noqa: E402
sent = []


class _UResp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body


_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = lambda req, timeout=None: (sent.append(json.loads(req.data)), _UResp(json.dumps({"response": "delegated-ok"}).encode()))[1]
try:
    r = asyncio.run(te._delegate_to_agent_real("not_an_agent", "do it"))
    check("delegation to an unregistered agent is refused without any request", "refused" in r and not sent, r)
    r = asyncio.run(te._delegate_to_agent_real("CFO", "do it"))
    check("delegation target matching is exact (no case folding)", "refused" in r and not sent, r)
    r = asyncio.run(te._delegate_to_agent_real("cfo", "do it"))
    check("delegation to a registered agent goes to /api/chat_sync for that agent", r == "delegated-ok" and sent and sent[-1]["agent"] == "cfo")
finally:
    urllib.request.urlopen = _real_urlopen

try:
    from starlette.testclient import TestClient
    import server as srv
    _HUD = True
except Exception as ex:  # pragma: no cover
    _HUD = False
    check("the HUD app can be imported for the delegation and fallback tests", False, f"{type(ex).__name__}: {ex}")

if _HUD:
    c = TestClient(srv.app, base_url="http://127.0.0.1:8000")
    sent.clear()
    urllib.request.urlopen = lambda req, timeout=None: (sent.append(json.loads(req.data)), _UResp(json.dumps({"message": {"content": "hi"}}).encode()))[1]
    try:
        r = c.post("/api/chat_sync", json={"agent": "not_an_agent", "messages": [{"role": "user", "content": "x"}]})
        check("/api/chat_sync refuses an unregistered agent (400) and calls no model", r.status_code == 400 and not sent)
        r = c.post("/api/chat_sync", json={"agent": "cfo", "messages": [{"role": "user", "content": "x"}]})
        check("a delegated agent gets no tools at all (the chat_sync payload carries no tool schemas)",
              r.status_code == 200 and sent and "tools" not in sent[-1], str(sent[-1:])[:200])
    finally:
        urllib.request.urlopen = _real_urlopen

    # ── 7. router failure -> ordinary-chat fallback, with no privileges ─────────────────────────────────────────
    print("\n-- router failure and the ordinary-chat fallback --")
    AUDITS.clear()
    real_l1 = srv._router_layer1
    srv._router_layer1 = lambda msg, mode: (_ for _ in ()).throw(TimeoutError("router down"))
    route = asyncio.run(srv.layer1_route("hello", "chief_of_staff", "general"))
    check("a router error yields the analyst fallback flagged as such, not the requested agent",
          route["agent"] == "analyst" and route.get("fallback") is True, str(route))
    check("the fallback is audited as a routing error without the message", any(a.get("action") == "routing_fallback" for a in AUDITS)
          and "hello" not in json.dumps(AUDITS))
    srv._router_layer1 = lambda msg, mode: {"agent": "root", "domain": "general", "needs_reasoning": False, "confidence": 1.0, "intent": "chat"}
    route = asyncio.run(srv.layer1_route("hello", "cfo", "business"))
    check("a router that names an unknown agent also yields the flagged fallback", route["agent"] == "analyst" and route.get("fallback") is True)
    srv._router_layer1 = lambda msg, mode: {"agent": "cfo", "domain": "business", "needs_reasoning": False, "confidence": 0.9, "intent": "chat"}
    route = asyncio.run(srv.layer1_route("hello", "analyst", "general"))
    check("a normal routing decision is not a fallback", route["agent"] == "cfo" and not route.get("fallback"))

    # the fallback turn end to end: answered, but no tool decision, no retrieval, no tier escalation
    calls = {"decide": 0, "rag": 0, "tier": 0}

    async def _decide(*a, **k):
        calls["decide"] += 1
        return None

    def _rag(*a, **k):
        calls["rag"] += 1
        return ""

    def _tier(*a, **k):
        calls["tier"] += 1
        return 1

    async def _stream(payload, timeout=120):
        yield {"message": {"content": "plain chat answer"}, "done": True}

    saved = {k: getattr(srv, k) for k in ("rag_context", "route_tier", "_ollama_stream", "_RAG_OK")}
    real_decide = te.decide_and_run_tool
    te.decide_and_run_tool, srv.rag_context, srv.route_tier, srv._ollama_stream, srv._RAG_OK = _decide, _rag, _tier, _stream, True
    srv._router_layer1 = lambda msg, mode: (_ for _ in ()).throw(ConnectionError("router down"))
    try:
        with c.websocket_connect("/ws/chat", headers={"host": "127.0.0.1"}) as ws:
            ws.send_json({"message": "what's a good name for a cat?", "agent": "chief_of_staff", "history": []})
            ev = []
            while True:
                e = ws.receive_json()
                ev.append(e)
                if e.get("type") in ("done", "error"):
                    break
        done = next((e for e in ev if e.get("type") == "done"), {})
        check("ordinary chat still works on the fallback turn (an answer is delivered)", done.get("full") == "plain chat answer", str(ev[-1:]))
        check("the fallback turn runs no tool decision", calls["decide"] == 0)
        check("the fallback turn gets no vault, memory or fact retrieval", calls["rag"] == 0)
        check("the fallback turn is never offered a cloud tier", calls["tier"] == 0)
        # the same turn with a working router does get tools and retrieval (the restriction is fallback-only)
        srv._router_layer1 = lambda msg, mode: {"agent": "chief_of_staff", "domain": "general", "needs_reasoning": False, "confidence": 0.9, "intent": "chat"}
        with c.websocket_connect("/ws/chat", headers={"host": "127.0.0.1"}) as ws:
            ws.send_json({"message": "what's on my list?", "agent": "chief_of_staff", "history": []})
            while ws.receive_json().get("type") not in ("done", "error"):
                pass
        check("a normally routed turn still gets its tool decision and retrieval", calls["decide"] == 1 and calls["rag"] == 1 and calls["tier"] == 1, str(calls))
    finally:
        for k, v in saved.items():
            setattr(srv, k, v)
        te.decide_and_run_tool = real_decide
        srv._router_layer1 = real_l1

shutil.rmtree(_TMP, ignore_errors=True)
passed = sum(1 for r in results if r)
print(f"\n  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
