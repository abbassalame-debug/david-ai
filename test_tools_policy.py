"""
test_tools_policy.py -- offline tests for which tools each domain may use (tools.py).

2026-10-03: the technical domain no longer skips the tool-decision layer; it gets the
READ-ONLY tools so internal-state questions ("is my file watcher running?") are grounded,
while a coder prompt can never trigger a side-effect tool. Measured live: 0/60 false
positives on coding prompts, 15/15 internal-state questions grounded, 108/108 overall.

Run: py -3.11 test_tools_policy.py
"""
from david_config import DAVID_HOME
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import tools

SIDE_EFFECT = {"write_note", "start_process", "stop_process", "create_task", "speak",
               "generate_image", "run_war_room", "delegate_to_agent"}
names = lambda lst: {t["function"]["name"] for t in lst}
all_names = names(tools.TOOLS)

tech = tools.tools_for_domain("technical")
check("technical domain gets a tool list (it no longer skips the layer)", tech is not None and len(tech) > 0)
check("technical domain is offered NO side-effect tool", not (names(tech) & SIDE_EFFECT), str(sorted(names(tech) & SIDE_EFFECT)))
check("technical domain can look up internal state (process_status, get_metrics)",
      {"process_status", "get_metrics"} <= names(tech))
check("technical domain can search the user's library", "query_knowledge" in names(tech))
check("technical domain gets no live-web search", "search_web" not in names(tech))
check("every other domain still gets every tool",
      all(names(tools.tools_for_domain(d)) == all_names for d in ("general", "business", "music", "lore", "personal", "research")))
check("read-only set only names tools that exist", tools.READ_ONLY_TOOL_NAMES <= all_names,
      str(sorted(tools.READ_ONLY_TOOL_NAMES - all_names)))
check("side-effect tools and the read-only set are disjoint", not (tools.READ_ONLY_TOOL_NAMES & SIDE_EFFECT))
check("every tool is classified as exactly one of read-only / side-effect / web",
      all_names <= (tools.READ_ONLY_TOOL_NAMES | SIDE_EFFECT | {"search_web"}),
      str(sorted(all_names - (tools.READ_ONLY_TOOL_NAMES | SIDE_EFFECT | {"search_web"}))))

qk = next(t for t in tools.TOOLS if t["function"]["name"] == "query_knowledge")["function"]["description"].lower()
check("query_knowledge says named manuals/documents belong to the user's library", "manual" in qk and "named" in qk)
check("the decision prompt routes named-document questions away from search_web",
      "named manual" in tools.TOOL_DECISION_SYSTEM.lower() and "never search_web" in tools.TOOL_DECISION_SYSTEM.lower())
check("tool-decision model is qwen3.5:9b", tools.TOOL_DECISION_MODEL == "qwen3.5:9b")

print("\n-- Arabic language hint (ui/server.language_hint) --")
sys.path.insert(0, str(DAVID_HOME / "ui"))
import server as _srv
h = _srv.language_hint
check("an Arabic question gets the formal-Arabic hint", "Arabic" in h("ما هي أهم الأمور التي يجب مراجعتها عند إغلاق الصندوق؟"))
check("an English question gets no hint", h("what should I check when closing the register?") == "")
check("French gets no hint", h("Quels sont les risques d'ouvrir un deuxième restaurant ?") == "")
check("Arabic mixed with an English term still gets the hint", "Arabic" in h("اشرح لي sidechain compression بالتفصيل"))
check("asking for an English answer in Arabic suppresses the hint", h("اشرح لي هذا بالإنجليزية") == "")
check("asking 'in English' suppresses the hint", h("ما معنى كلمة الرحمن؟ answer in English") == "")
check("empty / punctuation-only input is safe", h("") == "" and h("?!...") == "")
_srv_src = open(str(DAVID_HOME / "ui" / "server.py"), encoding="utf-8").read()
check("the hint is appended to the user message that reaches the model",
      "_lang_hint = language_hint(msg)" in _srv_src and 'user_content += "\\n\\n" + _lang_hint' in _srv_src)

print("\n-- query_knowledge access control (tool_executor) --")
import types, inspect
import numpy as np
import tool_executor as _te


# 2026-10-18 (audit finding S-03): query_knowledge no longer runs its own search with a "not research" filter (that
# filter was the only boundary, so every other vault, memory episodes included, reached every agent). It now calls
# rag.retrieve(agent, ...), the function that enforces each agent's namespaces. These checks keep the original intent
# (no firewalled vault without permission, unknown agents fail closed, the linguist keeps research, 8 hits) against
# the new implementation; the end-to-end boundary is tested on real LanceDB rows in test_namespace_isolation.py.
_NS = {"linguist": ["research"], "cfo": ["business", "system"]}
_ROWS = {"research": {"vault": "research", "file": "letter.md", "text": "RESEARCH-ROW"},
         "business": {"vault": "business", "file": "lease.md", "text": "BUSINESS-ROW"},
         "system": {"vault": "system", "file": "wifi.md", "text": "SYSTEM-ROW"}}


def _run_kq(agent):
    log = {}
    fake = types.ModuleType("rag")
    fake.AGENT_NAMESPACES = _NS

    def retrieve(a, query, top_k=5):
        log.update(agent=a, query=query, top_k=top_k)
        return [_ROWS[v] for v in _NS.get(a, [])]
    fake.retrieve = retrieve
    saved = sys.modules.get("rag"); sys.modules["rag"] = fake
    try:
        log["out"] = _te._query_knowledge("anything", agent)
    finally:
        if saved is not None: sys.modules["rag"] = saved
        else: sys.modules.pop("rag", None)
    return log


kq = _run_kq("cfo")
check("an agent without research access cannot see the firewalled vault (scoped by rag.retrieve for the acting agent)",
      kq.get("agent") == "cfo" and "RESEARCH-ROW" not in kq["out"] and "BUSINESS-ROW" in kq["out"], str(kq))
unk = _run_kq("made_up_agent")
check("an unknown agent name is treated the same (fail closed: no vault at all)",
      unk.get("agent") == "made_up_agent" and unk["out"] == "No relevant knowledge found.", str(unk))
check("the linguist, whose namespace includes research, gets its research rows", "RESEARCH-ROW" in _run_kq("linguist")["out"])
check("knowledge search returns KNOWLEDGE_TOP_K (8) hits", kq.get("top_k") == _te.KNOWLEDGE_TOP_K == 8)
check("decide_and_run_tool replaces the model-written agent with the routed agent",
      'tool_args = dict(tool_args, agent=agent)' in inspect.getsource(_te.decide_and_run_tool))

print("\n-- vision turn regression (ui/server.py) --")
_vs = open(str(DAVID_HOME / "ui" / "server.py"), encoding="utf-8").read()
_ws_chat = _vs[_vs.index("async def ws_chat"):]
_img_branch = _ws_chat.index("            if image_b64:\n                model   = VISION_MODEL")
_pre = _ws_chat[:_img_branch]
check("is_r1_agent is initialised before the image/non-image branch",
      "            is_r1_agent = False\n" in _pre)
check("force_reasoning is initialised before the image/non-image branch",
      "            force_reasoning = False\n" in _pre)
check("the generate call reads both names (so the initialisation is load-bearing)",
      "_is_reasoning_call = is_r1_agent or force_reasoning" in _ws_chat)

print("\n-- Summary --")
passed = sum(1 for r in results if r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
