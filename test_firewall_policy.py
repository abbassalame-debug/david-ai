"""test_firewall_policy.py — config/vaults.toml [boundaries].firewalled is enforced by code (audit finding S-08, 2026-10-20).

  1. david_config reads the firewalled vaults from the policy and recognises them however they are spelled.
  2. Every gate protects Research (the example policy's firewalled vault): tool-loop writes, imports, the no-domain fact scan, DLP.
  3. The proof that the gates READ the policy: firewall a second vault (lore) in a modified policy and every gate protects it too;
     a firewalled vault the policy forgot to list under private_vault_folders still counts as private for DLP.
  4. Invariant on the shipped policy: only the agents that name a firewalled vault in [agent_namespaces] can receive its rows.

No network, no model: fake LanceDB tables, temporary folders, a modified copy of the policy (the real file is never touched).

Run: py -3.11 test_firewall_policy.py
"""

from david_config import DAVID_HOME
import copy, io, os, re, shutil, sys, tempfile
from contextlib import contextmanager, redirect_stdout
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DAVID_APP_DB_URL", "DAVID_AUDIT_CONTENT"):
    os.environ.pop(k, None)

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import david_config as cfg
import david_router as dr
import rag
import knowledge
import tool_executor as te


@contextmanager
def policy(**boundaries):
    """A modified copy of the loaded policy with these [boundaries] values, restored afterwards."""
    real = cfg.load_policy()
    p = copy.deepcopy(real)
    p.setdefault("boundaries", {}).update(boundaries)
    with mock.patch.dict(cfg._policy_cache, {"p": p}):
        yield p


# ── 1. the policy key ─────────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── 1. [boundaries].firewalled is read from the policy ──")
check("the example policy firewalls research", cfg.firewalled_vaults() == {"research"}, str(cfg.firewalled_vaults()))
for spelling in ("research", "Research", "RESEARCH"):
    check(f"recognised as firewalled: {spelling!r}", cfg.is_firewalled_vault(spelling))
for name in ("system", "Business", "music", "lore", "memory", "books", ""):
    check(f"not firewalled: {name!r}", not cfg.is_firewalled_vault(name))
with policy(firewalled=[]):
    check("a policy that firewalls nothing is honoured (no hidden hard-coded vault)", not cfg.is_firewalled_vault("research"))


# ── helpers: drive each gate without touching real data ─────────────────────────────────────────────────────────────────
def write_refused(vault_name, tmp):
    roots = {k: os.path.join(tmp, "vaults", f) for k, f in cfg.load_policy()["vaults"].items()}
    for p in roots.values():
        os.makedirs(p, exist_ok=True)
    with mock.patch.dict(te.VAULT_ROOTS, roots, clear=True), mock.patch.object(te, "NOTE_BACKUP_DIR", os.path.join(tmp, "nb")):
        r = te._write_note(vault_name, "probe.md", "x")
    folder = cfg.load_policy()["vaults"][vault_name.lower()]
    return "read-only" in r and not os.path.exists(os.path.join(tmp, "vaults", folder, "probe.md")), r

def import_refused(domain, tmp):
    src = os.path.join(tmp, "src")
    os.makedirs(src, exist_ok=True)
    open(os.path.join(src, "a.md"), "w", encoding="utf-8").write("hello")
    buf = io.StringIO()
    with mock.patch.object(rag, "get_table", side_effect=AssertionError("must not open the index")), redirect_stdout(buf):
        try:
            rag.import_folder(src, domain=domain)
            opened = False
        except AssertionError:
            opened = True
    return (not opened and "firewalled" in buf.getvalue()), buf.getvalue().strip()[:120]

def fact_scan_filter():
    seen = {}
    def scan(table, filt):
        seen["f"] = filt; return []
    with mock.patch.object(knowledge, "init_db", lambda: None), mock.patch.object(rag, "get_table", lambda: object()), \
            mock.patch.object(knowledge, "_scan_table", scan), redirect_stdout(io.StringIO()):
        knowledge.build_index()
    return seen.get("f", "")

def path_is_private(folder):
    """The DLP path pattern exactly as david_router builds it, from the current policy."""
    pat = re.escape(str(cfg.DAVID_VAULTS)) + r"[\\/](?:" + "|".join(re.escape(f) for f in cfg.private_vault_folders()) + ")"
    return re.search(pat, os.path.join(str(cfg.DAVID_VAULTS), folder, "note.md")) is not None


# ── 2. every gate protects Research ─────────────────────────────────────────────────────────────────────────────────────
print("\n── 2. every gate protects the firewalled Research vault ──")
tmp = tempfile.mkdtemp(prefix="firewall_policy_")
try:
    ok, r = write_refused("Research", tmp)
    check("the tool loop cannot write into Research", ok, r)
    ok, r = write_refused("system", tmp)
    check("…while an ordinary vault is still writable", not ok and "written" in r, r)
    ok, out = import_refused("research", tmp)
    check("an outside folder cannot be imported into research (the index is never opened)", ok, out)
    check("research is not an import domain; the other policy vaults are", rag.IMPORT_DOMAINS == {"system", "business", "music", "lore"},
          str(sorted(rag.IMPORT_DOMAINS)))
    f = fact_scan_filter()
    check("the no-domain fact scan leaves out memory and research", f == "vault != 'memory' AND vault != 'research'", f)
    with mock.patch.object(dr, "audit", lambda e: None):
        check("DLP flags a path inside the Research folder", dr.dlp_flag("read " + os.path.join(str(cfg.DAVID_VAULTS), "Research", "ch1.md")) is True)
        check("DLP leaves an ordinary vault path alone", dr.dlp_flag("open " + os.path.join(str(cfg.DAVID_VAULTS), "Music", "idea.md")) is False)

    # ── 3. the gates read the policy ──────────────────────────────────────────────────────────────────────────────────
    print("\n── 3. firewall lore in a modified policy: every gate protects it too ──")
    with policy(firewalled=["research", "lore"]):
        ok, r = write_refused("Lore", tmp)
        check("the tool loop cannot write into Lore", ok, r)
        ok, out = import_refused("lore", tmp)
        check("an outside folder cannot be imported into lore", ok, out)
        f = fact_scan_filter()
        check("the no-domain fact scan leaves out lore", "vault != 'lore'" in f and "vault != 'research'" in f, f)
        check("a firewalled vault missing from private_vault_folders still counts as private for DLP", path_is_private("Lore"),
              str(cfg.private_vault_folders()))
    with policy(firewalled=["research"], private_vault_folders=["Business"]):
        check("Research stays private for DLP even when private_vault_folders forgets it", path_is_private("Research"),
              str(cfg.private_vault_folders()))
    ok, r = write_refused("Lore", tmp)
    check("with the real policy back, Lore is writable again (nothing else was hard-coded)", not ok and "written" in r, r)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 4. invariant on the shipped policy ────────────────────────────────────────────────────────────────────────────────
print("\n── 4. who may read a firewalled vault ──")
readers = sorted(a for a in rag.AGENT_NAMESPACES if rag.permitted_vaults(a) & cfg.firewalled_vaults())
named = sorted(a for a, ns in rag.AGENT_NAMESPACES.items() if set(ns) & cfg.firewalled_vaults())
check("only agents that name a firewalled vault in [agent_namespaces] can receive its rows", readers == named, f"{readers} vs {named}")
check("in the example policy those are linguist and language_specialist", readers == ["language_specialist", "linguist"], str(readers))
bad_fallback = {v: t for v, t in rag.CROSS_VAULT_FALLBACK.items() if not cfg.is_firewalled_vault(v) and any(cfg.is_firewalled_vault(x) for x in t)}
check("no ordinary vault falls back into a firewalled one", not bad_fallback, str(bad_fallback))
src = "".join(open(os.path.join(str(DAVID_HOME), f), encoding="utf-8").read() for f in ("tool_executor.py", "knowledge.py", "rag.py"))
check("no gate compares against the literal 'research' any more", not re.search(r"==\s*['\"]research['\"]|vault != 'research'|\{\"system\", \"business\", \"music\", \"lore\"\}", src))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
