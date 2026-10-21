"""
agent_spec.py -- the ONE place that builds an agent's system prompt and decides which tools it may use.

Before 2026-10-05 the prompt was assembled by hand in about ten places (ui/server.py, david_router.py) and every
agent was offered every tool. Now:

  build_system_prompt(agent)        -> the agent's persona prompt
  tools_for_agent(agent, domain)    -> the tool schemas this agent may be offered
  tool_allowed(agent, domain, name) -> may this agent call this tool?

An agent with agents/<agent>/role.md is "migrated": its prompt is role.md + background + shared behaviour + shared safety
(+ examples.md on request) and its tools are the role's `tools_allow` list. Every other agent behaves exactly as it
always did (AGENT_PROMPTS, depth/*.md for the four thinking agents, tools by domain). See agents/README.md.

david_router.py hands over its legacy tables once, via configure(), instead of this module importing it: the router
also runs as __main__ from the command line, and a second import would execute it twice.
"""

from david_config import DAVID_HOME
import os
import re

AGENTS_DIR = str(DAVID_HOME / "agents")
ROOT_DIR = str(DAVID_HOME)

_cfg = {"prompts": None, "r1": None, "load_depth": None, "default": None, "agents": None, "firewalled": None}

# Tools that only the named agents may ever hold, whatever a role.md lists. The Adobe template tools open applications and create
# files, so a typo or a copied header in another agent's role.md must not hand them out. fill is the creative director's alone
# (the UI/UX engineer reviews, it does not fill).
EXCLUSIVE_TOOL_OWNERS = {
    "list_adobe_templates": {"creative_director", "uiux_engineer"},
    "fill_adobe_template": {"creative_director"},
    "export_adobe_preview": {"creative_director", "uiux_engineer"},
    "review_adobe_preview": {"uiux_engineer"},
}
_cache = {}                       # path -> (mtime, text): files are re-read when they change, never otherwise
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def configure(prompts, r1_agents, load_depth, default_prompt):
    _cfg.update(prompts=prompts, r1=r1_agents, load_depth=load_depth, default=default_prompt)


def register_agents(agents, firewalled):
    """david_router hands over the valid agent ids (AGENTS) and the firewalled set once, next to configure()."""
    _cfg.update(agents=frozenset(agents), firewalled=frozenset(firewalled))


def registered_agents():
    _ensure()
    return _cfg["agents"] or frozenset()


def firewalled_agents():
    _ensure()
    return _cfg["firewalled"] or frozenset()


def _ensure():
    if _cfg["prompts"] is None:
        import david_router  # noqa: F401  (its import calls configure())
    if _cfg["prompts"] is None:
        raise RuntimeError("agent_spec is not configured: david_router never called configure()")


def _read(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    _cache[path] = (mtime, text)
    return text


def parse_role(text):
    """(header dict, body). Header lines are `key: value` or `key: [a, b, c]`; a trailing `  # comment` is ignored."""
    m = _FRONT.match(text)
    if not m:
        return {}, text.strip()
    meta = {}
    for line in m.group(1).splitlines():
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line or ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value
    return meta, text[m.end():].strip()


class Spec:
    def __init__(self, agent, meta, body):
        self.agent, self.meta, self.body = agent, meta, body

    def _list(self, key):
        v = self.meta.get(key, [])
        return v if isinstance(v, list) else [v]

    tools_allow = property(lambda self: self._list("tools_allow"))
    tools_planned = property(lambda self: self._list("tools_planned"))
    background = property(lambda self: self._list("background"))


def load_spec(agent):
    """The Spec for a migrated agent, or None."""
    if not isinstance(agent, str) or not _NAME.fullmatch(agent):
        return None
    text = _read(os.path.join(AGENTS_DIR, agent, "role.md"))
    if text is None:
        return None
    meta, body = parse_role(text)
    return Spec(agent, meta, body)


def migrated_agents():
    try:
        return sorted(d for d in os.listdir(AGENTS_DIR)
                      if _NAME.fullmatch(d) and os.path.exists(os.path.join(AGENTS_DIR, d, "role.md")))
    except OSError:
        return []


def _compose(spec, examples):
    parts = [spec.body]
    background = [t.strip() for t in (_read(os.path.join(ROOT_DIR, rel)) for rel in spec.background) if t]
    if background:
        parts.append("## Background reference\n\n" + "\n\n".join(background))
    for shared in ("behavior.md", "safety.md"):
        text = _read(os.path.join(AGENTS_DIR, "_shared", shared))
        if text:
            parts.append(text.strip())
    if examples:
        text = _read(os.path.join(AGENTS_DIR, spec.agent, "examples.md"))
        if text:
            parts.append(text.strip())
    return "\n\n".join(p for p in parts if p)


def build_system_prompt(agent, *, examples=False, fallback="analyst"):
    """The persona prompt for `agent`. Callers append their own surface-specific tail.

    fallback: which agent's prompt to use for an unknown name ("analyst" in the HUD); None means the router's
    generic DEFAULT_PROMPT (the command-line router's old behaviour)."""
    _ensure()
    spec = load_spec(agent)
    if spec:
        return _compose(spec, examples)
    prompts = _cfg["prompts"]
    if agent in prompts:
        base = prompts[agent]
        if agent in _cfg["r1"]:
            depth = _cfg["load_depth"](agent)
            return f"{depth}\n\n---\n\n{base}" if depth else base
        return base
    if fallback and fallback != agent:
        return build_system_prompt(fallback, examples=examples, fallback=None)
    return _cfg["default"]


def is_registered_agent(agent):
    """True only for the agent ids the router knows (david_router.AGENTS). Exact match: no case folding, no trimming."""
    if not isinstance(agent, str):
        return False
    return agent in registered_agents()


def tools_for_agent(agent, domain=None):
    """Tool schemas this agent may be offered: exactly its `tools_allow` list, and never more than the domain allows
    (the technical domain stays read-only). FAIL CLOSED (audit finding S-05, 2026-10-18): no role.md, a header
    without a `tools_allow` list, or a header whose id does not match the folder -> no tools at all (this used to
    hand out every general tool)."""
    import tools as T
    spec = load_spec(agent)
    if not spec or not isinstance(spec.meta.get("tools_allow"), list) or spec.meta.get("id") != agent:
        return []
    base = T.tools_for_domain(domain) if domain else T.TOOLS
    allowed = set(spec.tools_allow)
    # Agent-only tools (tools.AGENT_ONLY_TOOLS) are offered to an agent that lists them, in any domain, unless the tool has
    # exclusive owners and this agent is not one of them.
    return [t for t in list(base) + list(T.AGENT_ONLY_TOOLS)
            if t["function"]["name"] in allowed and agent in EXCLUSIVE_TOOL_OWNERS.get(t["function"]["name"], {agent})]


def tool_allowed(agent, domain, name):
    return any(t["function"]["name"] == name for t in tools_for_agent(agent, domain))


def validate_specs():
    """Problems in the agents/ folder (empty list = healthy). Run by test_agent_spec.py."""
    import tools as T
    problems = []
    for shared in ("behavior.md", "safety.md"):
        if not os.path.exists(os.path.join(AGENTS_DIR, "_shared", shared)):
            problems.append(f"missing agents/_shared/{shared}")
    for agent in migrated_agents():
        spec = load_spec(agent)
        if spec.meta.get("id") != agent:
            problems.append(f"{agent}: header id is {spec.meta.get('id')!r}, must equal the folder name")
        if not spec.body:
            problems.append(f"{agent}: role.md has no body")
        if "tools_allow" not in spec.meta:
            problems.append(f"{agent}: header has no tools_allow (use [] for no tools)")
        for name in spec.tools_allow:
            if name not in T.TOOL_NAMES:
                problems.append(f"{agent}: tools_allow names {name!r}, which is not in tools.py (a planned tool belongs in tools_planned)")
        for name in spec.tools_allow:
            if name in EXCLUSIVE_TOOL_OWNERS and agent not in EXCLUSIVE_TOOL_OWNERS[name]:
                problems.append(f"{agent}: {name!r} is exclusive to {sorted(EXCLUSIVE_TOOL_OWNERS[name])}")
        for name in spec.tools_planned:
            if name in T.TOOL_NAMES:
                problems.append(f"{agent}: {name!r} now exists; move it from tools_planned to tools_allow")
        for rel in spec.background:
            if not os.path.exists(os.path.join(ROOT_DIR, rel)):
                problems.append(f"{agent}: background file {rel} not found")
    return problems
