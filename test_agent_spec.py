"""
test_agent_spec.py -- offline tests for the agents/ folder, agent_spec.py (the one prompt builder and tool scoper) and the
Job 1 role files.

No Ollama, no network: real files, a fake HTTP client for the tool-decision call, recording stand-ins for the tool executor.

Run: py -3.11 test_agent_spec.py
"""

from david_config import DAVID_HOME
import sys, os, re, json, inspect, asyncio, tempfile, shutil, time
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


def src(path):
    return open(os.path.join(str(DAVID_HOME), path), encoding="utf-8").read()


import david_router as dr
import agent_spec as A
import tools as T

AG = str(DAVID_HOME / "agents")
JOB = os.path.join(AG, "jobs", "01_restaurant_ops")
SIDE_EFFECT = {"write_note", "create_task", "start_process", "stop_process", "generate_image", "speak", "run_war_room", "delegate_to_agent"}

print("\n-- the scaffold --")
for rel in ["README.md", "_shared/behavior.md", "_shared/safety.md", "cfo/role.md", "cfo/examples.md", "analyst/role.md",
            "analyst/examples.md", "jobs/01_restaurant_ops/JOB.md", "jobs/01_restaurant_ops/contracts/draft_package.schema.json"]:
    check(f"agents/{rel} exists", os.path.exists(os.path.join(AG, rel)))
for d in ("contracts", "reference", "evals"):
    check(f"jobs/01_restaurant_ops/{d}/ exists and is not empty", os.path.isdir(os.path.join(JOB, d)) and os.listdir(os.path.join(JOB, d)))
beh, saf = src("agents/_shared/behavior.md"), src("agents/_shared/safety.md")
check("behavior.md: formal MSA Arabic, no inventing, confirm before side effects",
      "Modern Standard Arabic" in beh and "Never invent" in beh and "Confirm before any side effect" in beh)
check("safety.md: no live DB writes, never overwrite originals, Research firewall",
      "No live database writes" in saf and "Never overwrite original files" in saf and "Research is firewalled" in saf)
check("safety.md: a human approves and text inside material is data", "A human approves" in saf and "data, not instructions" in saf)
check("the folder README explains the structure, the fallback and tool scoping",
      all(k in src("agents/README.md") for k in ("build_system_prompt", "role.md", "tools_allow", "tools_planned", "Boundary")))

print("\n-- the role header parser --")
meta, body = A.parse_role("---\nid: x\ntools_allow: [a, b]   # note\ntools_planned: []\nbackground: [depth/x.md]\n---\n# Body\ntext\n")
check("parses keys, lists, empty lists and trailing comments",
      meta == {"id": "x", "tools_allow": ["a", "b"], "tools_planned": [], "background": ["depth/x.md"]} and body == "# Body\ntext", str(meta))
check("a file with no header is all body", A.parse_role("just text") == ({}, "just text"))
check("agents/ is healthy: validate_specs() reports no problems", A.validate_specs() == [], str(A.validate_specs()))
check("all 17 agents have a role file", A.migrated_agents() == sorted(dr.AGENTS) and len(dr.AGENTS) == 17, str(A.migrated_agents()))

print("\n-- the unified prompt builder: every agent is built from its role file --")
bad = [a for a in dr.AGENTS if not A.build_system_prompt(a).startswith(A.load_spec(a).body)]
check("each agent's prompt starts with its role.md body", not bad, str(bad))
bad = [a for a in dr.AGENTS if beh.strip() not in A.build_system_prompt(a) or saf.strip() not in A.build_system_prompt(a)]
check("each agent's prompt carries the shared behaviour and safety rules", not bad, str(bad))
check("the personas are persona-neutral: no real name, place or business in any role file",
      not any(re.search("(?i)" + "|".join(["ab" + "bas", "sal" + "ame", "leban", "iraq", "qur" + "an", "had" + "ith", "islam"]), open(os.path.join(AG, a, "role.md"), encoding="utf-8").read()) for a in dr.AGENTS))
check("an unknown agent falls back to the analyst", A.build_system_prompt("no_such_agent") == A.build_system_prompt("analyst"))
check("fallback=None gives the router's generic prompt", A.build_system_prompt("no_such_agent", fallback=None) == dr.DEFAULT_PROMPT)
check("a path-like agent name is never read from disk", A.build_system_prompt("../_shared/behavior") == A.build_system_prompt("analyst") and A.load_spec("cfo/..") is None)

print("\n-- the migrated agents --")
cfo, an = A.build_system_prompt("cfo"), A.build_system_prompt("analyst")
cfo_role = A.load_spec("cfo").body
check("cfo: role body, then shared behaviour, then shared safety",
      cfo.startswith(cfo_role) and cfo.index(beh.strip()[:40]) > len(cfo_role) - 1 and cfo.index(saf.strip()[:40]) > cfo.index(beh.strip()[:40]))
check("analyst: shared behaviour and safety are included", beh.strip() in an and saf.strip() in an)
check("analyst keeps the old everyday instruction verbatim", dr.AGENT_PROMPTS["analyst"] in an)
check("the header is never sent to the model", "tools_allow" not in cfo and "tools_allow" not in an and not cfo.lstrip().startswith("---"))
check("examples are loaded only on request", "# CFO examples" not in cfo and "# CFO examples" in A.build_system_prompt("cfo", examples=True)
      and "# Analyst examples" in A.build_system_prompt("analyst", examples=True) and "# Analyst examples" not in an)
check("the examples are labelled synthetic", "synthetic" in src("agents/cfo/examples.md").lower() and "synthetic" in src("agents/analyst/examples.md").lower())

print("\n-- files are re-read when they change (no restart) --")
tmp = tempfile.mkdtemp(prefix="agents_test_")
os.makedirs(os.path.join(tmp, "_shared")); os.makedirs(os.path.join(tmp, "demo"))
open(os.path.join(tmp, "_shared", "behavior.md"), "w", encoding="utf-8").write("SHARED-BEHAVIOR")
open(os.path.join(tmp, "_shared", "safety.md"), "w", encoding="utf-8").write("SHARED-SAFETY")
rp = os.path.join(tmp, "demo", "role.md")
open(rp, "w", encoding="utf-8").write("---\nid: demo\ntools_allow: [get_tasks]\n---\nVERSION ONE")
with mock.patch.object(A, "AGENTS_DIR", tmp):
    p1 = A.build_system_prompt("demo")
    open(rp, "w", encoding="utf-8").write("---\nid: demo\ntools_allow: [get_tasks]\n---\nVERSION TWO")
    os.utime(rp, (time.time() + 5, time.time() + 5))
    p2 = A.build_system_prompt("demo")
    names = [t["function"]["name"] for t in A.tools_for_agent("demo", "general")]
shutil.rmtree(tmp, ignore_errors=True)
check("an edited role.md shows up on the next call", "VERSION ONE" in p1 and "VERSION TWO" in p2 and "SHARED-SAFETY" in p2)
check("a temporary agent gets only its listed tool", names == ["get_tasks"], str(names))

print("\n-- per-agent tool scoping --")
names_for = lambda a, d=None: [t["function"]["name"] for t in A.tools_for_agent(a, d)]
cfo_tools, an_tools = names_for("cfo", "general"), names_for("analyst", "general")
check("cfo gets its four read-only tools plus the two arithmetic tools", sorted(cfo_tools) == sorted(["query_knowledge", "read_note", "search_web", "get_tasks", "reconcile_cash", "check_meat_variance"]), str(cfo_tools))
check("analyst gets the everyday read-only tools plus the review-sheet tool and the three menu tools", sorted(an_tools) == sorted(["query_knowledge", "read_note", "search_web", "get_calendar", "get_email", "get_tasks", "get_metrics", "process_status", "render_review_sheet", "get_menu_glossary", "match_menu_item", "validate_draft_package"]), str(an_tools))
check("neither Job 1 agent has any side-effect tool", not (SIDE_EFFECT & set(cfo_tools)) and not (SIDE_EFFECT & set(an_tools)))
check("every other agent is offered exactly its own role list, never the full tool set (fail-closed policy, 2026-10-18)",
      sorted(names_for("chief_of_staff", "general")) == sorted(A.load_spec("chief_of_staff").tools_allow)
      and len(names_for("chief_of_staff", "general")) < len(T.TOOLS)
      and set(names_for("developer", "technical")) == set(A.load_spec("developer").tools_allow) & T.READ_ONLY_TOOL_NAMES)
check("an agent with no role.md, an unknown name or no name is offered no tools at all",
      names_for("no_such_agent", "general") == [] and names_for("no_such_agent") == [] and names_for(None, "general") == [] and names_for("", "general") == [])
check("a migrated agent never gets more than its domain allows: in the technical domain only read-only tools, plus the job tools, which are pure computation with no side effects",
      set(names_for("cfo", "technical")) <= T.READ_ONLY_TOOL_NAMES | {t["function"]["name"] for t in T.AGENT_ONLY_TOOLS}
      and not ({"write_note", "create_task", "speak", "generate_image"} & set(names_for("cfo", "technical"))))
check("tool_allowed agrees with the lists", A.tool_allowed("cfo", "general", "query_knowledge") and not A.tool_allowed("cfo", "general", "write_note")
      and A.tool_allowed("chief_of_staff", "general", "write_note"))
cs, ans = A.load_spec("cfo"), A.load_spec("analyst")
check("every allowed tool exists and every planned tool does not yet",
      all(n in T.TOOL_NAMES for s in (cs, ans) for n in s.tools_allow) and not any(n in T.TOOL_NAMES for s in (cs, ans) for n in s.tools_planned))
check("every Job 1 tool is built: nothing is left in tools_planned, and the menu tools are in the analyst's tools_allow",
      cs.tools_planned == [] and ans.tools_planned == []
      and {"reconcile_cash", "check_meat_variance"} <= set(cs.tools_allow) and {"render_review_sheet", "get_menu_glossary", "match_menu_item", "validate_draft_package"} <= set(ans.tools_allow))

print("\n-- the tool-decision layer and the executor obey the scoping --")
import tool_executor as TE
class _Resp:
    def __init__(self, name): self.name = name
    def raise_for_status(self): pass
    def json(self): return {"message": {"tool_calls": [{"function": {"name": self.name, "arguments": {"query": "x", "title": "t", "description": "d", "agent": "model-wrote-this"}}}]}}
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
def fake_client(name, seen):
    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **kw):
            seen.append([t["function"]["name"] for t in json["tools"]]); return _Resp(name)
        def stream(self, method, url, json=None, **kw): return _Streamed(self.post(url, json=json))
    return C
ran = []
async def rec_exec(name, args, agent=None, domain=None):
    ran.append((name, args.get("agent"))); return "ok"
import httpx
async def decide(agent, domain, tool):
    seen = []
    with mock.patch.object(httpx, "AsyncClient", fake_client(tool, seen)), mock.patch.object(TE, "execute_tool", rec_exec),          mock.patch("david_router.audit", lambda e: None):
        return await TE.decide_and_run_tool(agent, domain, "msg"), seen
r, seen = asyncio.run(decide("cfo", "general", "write_note"))
check("the model is offered only the cfo's tools", sorted(seen[0]) == sorted(cfo_tools), str(seen))
check("a tool outside the cfo's list is dropped even if the model asks for it", r is None and not ran, str(ran))
r, _ = asyncio.run(decide("cfo", "general", "query_knowledge"))
check("an allowed tool runs, scoped to the routed agent (not the model-written one)", r == "[TOOL: query_knowledge]\nok" and ran[-1] == ("query_knowledge", "cfo"), str(ran))
ran.clear()
r, seen = asyncio.run(decide("chief_of_staff", "general", "create_task"))
check("chief_of_staff is offered its own list (not everything) and can still use create_task",
      sorted(seen[0]) == sorted(A.load_spec("chief_of_staff").tools_allow) and ran and ran[-1][0] == "create_task", str(seen[:1]))
ran.clear()
r_unknown, seen_unknown = asyncio.run(decide("no_such_agent", "general", "create_task"))
check("an unknown agent gets no tool decision at all and nothing runs", r_unknown is None and seen_unknown == [] and not ran)
check("the technical domain is still read-only for everyone", asyncio.run(decide("developer", "technical", "write_note"))[0] is None)
audits = []
with mock.patch("david_router.audit", audits.append):
    refused = asyncio.run(TE.execute_tool("write_note", {"vault": "system", "path": "x.md", "content": "c"}, agent="analyst", domain="general"))
check("execute_tool itself refuses (and audits) a tool outside the agent's list", "not permitted" in refused and audits and audits[0]["action"] == "tool_refused", refused)
_wrote = []
for _who in (None, "", "no_such_agent"):
    audits = []
    with mock.patch("david_router.audit", audits.append), mock.patch.object(TE, "_write_note", lambda *a, **k: _wrote.append(a) or "written"):
        _r = asyncio.run(TE.execute_tool("write_note", {"vault": "system", "path": "x.md", "content": "c"}, agent=_who, domain="general"))
    check(f"execute_tool with agent={_who!r} is refused and audited, and the tool never runs",
          "not permitted" in _r and audits and audits[0]["action"] == "tool_refused" and not _wrote, _r)
al = src("agent_loop.py")
check("the agent loop offers and executes only the agent's tools", "tools_for_agent(agent, domain)" in al and "execute_tool(tool_name, tool_args, agent=agent, domain=domain)" in al and "_sync_call(messages, PLANNING_MODEL, agent_tools)" in al)

print("\n-- every caller goes through the one builder --")
sv, rt = src("ui/server.py"), src("david_router.py")
check("ui/server.py builds no prompt by hand any more", "AGENT_PROMPTS.get(" not in sv and "AGENT_PROMPTS[" not in sv and "load_depth(" not in sv)
check("ui/server.py uses build_system_prompt in the chat, REST, task and both war-room paths", sv.count("build_system_prompt(") >= 5, str(sv.count("build_system_prompt(")))
check("david_router.py builds no prompt by hand any more (only the table, load_depth and the hand-over)",
      "AGENT_PROMPTS.get(" not in rt and "AGENT_PROMPTS[\"" not in rt.replace('AGENT_PROMPTS["beat', 'AGENT_PROMPTS["beat') and rt.count("build_system_prompt(") >= 6)
check("the router hands its fallback tables to agent_spec once", "agent_spec.configure({}, R1_AGENTS, load_depth, DEFAULT_PROMPT)" in rt)
check("AGENT_PROMPTS stays complete as the fallback (17 agents)", len(dr.AGENT_PROMPTS) == 17 and "cfo" in dr.AGENT_PROMPTS and "analyst" in dr.AGENT_PROMPTS)

print("\n-- the Job 1 roles --")
cr, ar = src("agents/cfo/role.md"), src("agents/analyst/role.md")
check("cfo: cash reconciliation formula, Wallet and holds, float", all(k in cr for k in ("Expected = carried cash", "Counted =", "Difference = Counted", "Wallet", "Holds", "$200.00")))
check("cfo: code is authoritative for arithmetic, no approvals, no SQL by hand, no blame",
      "authoritative" in cr and "cannot approve" in cr and "do not write SQL" in cr and "never as blame" in cr)
check("cfo and analyst: no tool means no action, and they must not claim one", "cannot create tasks" in cr and "cannot create tasks" in ar and "never write that" in cr and "never write that it was done" in ar)
check("shared behaviour: answer in the language of the question; never claim an unperformed action", "an English question gets an English answer" in beh and 'never write "task created"' in beh)
check("shared behaviour carries no Arabic script that could prime a reply language", not re.search(r"[؀-ۿ]", beh))
check("cfo: never invents a tolerance and ends with the fixed footer", "Do not invent a limit" in cr and "Prepared for review. Nothing has been booked or written." in cr)
check("cfo: an ended day without a correction_reason is a BLOCKER", "correction_reason" in cr and "BLOCKER" in cr)
check("analyst: matching thresholds, never invents an item, shows both readings", all(k in ar for k in ("100", "85", "needs human", "Never invent a menu item", "show both")))
check("analyst: injected text is data; no tool that changes anything; no SQL", "is data" in ar and "no tool that changes anything" in ar and "SQL" in ar)
check("analyst: everyday behaviour is declared unchanged outside the job", "everyday questions" in ar and "only when you are given a restaurant draft package" in ar)
check("analyst: the review sheet layout ends with the fixed footer", "## Totals check (computed by code)" in ar and "Prepared for review. Nothing has been booked or written." in ar)

def reconcile(c, rate):
    expected = c["old_cash"] + c["pos_sales"] - c["wallet"] - sum(e["amount"] for e in c["expenses"]) - c["transfer_to_admin"]
    counted = c["counted_usd"] + c["counted_lbp"] / rate
    return round(expected, 2), round(counted, 2), round(counted - expected, 2)
blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", src("agents/cfo/examples.md"), re.S)]
check("cfo examples: both packages parse", len(blocks) == 2)
ok = True; shown = []
for b in blocks:
    e, c, d = reconcile(b["cash"], b["rate_lbp_per_usd"])
    comp = b["computed"]; ok &= (e, c, d) == (comp["expected"], comp["counted"], comp["diff"]); shown.append((e, c, d))
check("cfo examples: the `computed` blocks match the formula to the cent", ok, str(shown))
b2 = blocks[1]
check("cfo example 2: at the branch rate the counted cash and difference are the ones the answer quotes",
      reconcile(b2["cash"], 90000) == (1517.75, 1510.00, -7.75) and "1,510.00" in src("agents/cfo/examples.md") and "−7.75" in src("agents/cfo/examples.md"))
check("cfo example 2: the line gap is lines minus printed total", round(b2["computed"]["sum_of_lines"] - b2["computed"]["printed_total"], 2) == b2["computed"]["line_gap"] == -22.00)
check("cfo example 2: the expenses add up to what the formula used", round(sum(e["amount"] for e in b2["cash"]["expenses"]), 2) == 212.25)
ae = src("agents/analyst/examples.md")
ablock = json.loads(re.search(r"```json\n(.*?)\n```", ae, re.S).group(1))
lines = ablock["extraction"]["summary_by_items"]["lines"]
th = ablock["thresholds"]
def status(l):
    m = l["match"]
    if not l["readings_agree"] or m.get("item_id") is None: return "needs human"
    return "matched" if m["score"] >= th["matched"] else "suggested" if m["score"] >= th["suggested"] else "needs human"
counts = {s: sum(1 for l in lines if status(l) == s) for s in ("matched", "suggested", "needs human")}
check("analyst example: the statuses follow the stated thresholds",
      [status(l) for l in lines] == ["matched", "matched", "suggested", "needs human"] and f"{counts['matched']} matched, {counts['suggested']} suggested, {counts['needs human']} needs human" in ae, str(counts))
check("analyst example: lines add up to the printed total",
      round(sum(l["amount"] for l in lines), 2) == ablock["computed"]["sum_of_lines"] == ablock["extraction"]["summary_by_items"]["printed_total"] == 409.00)

print("\n-- the job and its contract --")
job = src("agents/jobs/01_restaurant_ops/JOB.md")
check("JOB.md has the five stages in order", [job.index(s) for s in ("Vision extraction", "Code arithmetic", "Agent review", "Draft SQL", "Human approval")] == sorted(job.index(s) for s in ("Vision extraction", "Code arithmetic", "Agent review", "Draft SQL", "Human approval")))
check("JOB.md: agents never hold credentials, nothing is booked before a human, null beats a guess",
      "no database credentials" in job and "nothing is booked until this step" in job and "Null beats a guess" in job)
check("JOB.md says what exists and what is only planned", "Planned" in job and "**Written**" in job and "shadow mode" in job.lower())
schema = json.load(open(os.path.join(JOB, "contracts", "draft_package.schema.json"), encoding="utf-8"))
case1 = json.load(open(os.path.join(JOB, "evals", "synthetic_001_clean_day.json"), encoding="utf-8"))
case2 = json.load(open(os.path.join(JOB, "evals", "synthetic_002_blocked_day.json"), encoding="utf-8"))
need = set(schema["required"]); cash_need = set(schema["properties"]["cash"]["required"])
check("the draft schema is valid JSON and names a correction_reason", "correction_reason" in schema["properties"] and schema["properties"]["schema"]["const"] == "draft_package/v0")
check("both synthetic eval cases carry every required block", all(need <= set(c["package"]) and cash_need <= set(c["package"]["cash"]) for c in (case1, case2)))
check("the eval cases are marked synthetic and their numbers satisfy the formula",
      case1["synthetic"] and case2["synthetic"]
      and all(reconcile(c["package"]["cash"], c["package"]["rate_lbp_per_usd"])[0] == c["package"]["computed"]["expected"] for c in (case1, case2)))
check("the eval cases are the CFO's two examples", case1["package"]["computed"] == blocks[0]["computed"] and case2["package"]["computed"] == blocks[1]["computed"])
check("tolerances are empty on purpose", "NOT SET" in src("agents/jobs/01_restaurant_ops/reference/README.md"))

print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
