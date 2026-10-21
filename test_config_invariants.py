"""
test_config_invariants.py — static consistency checks across David's config
dicts. Cheap, offline, and exactly the class of bug that silently corrupts the
RAG pipeline (F8) or misroutes agents. Extended over the remediation phases.

Skips gracefully if the server app can't be imported in this environment.

Run: python test_config_invariants.py
"""

from david_config import DAVID_HOME
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))
sys.path.insert(0, str(DAVID_HOME / "ui"))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
SKIP = "\033[93m  SKIP\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import rag

print("\n── F8: vault watcher domain map ⊆ rag.IMPORT_DOMAINS ──")
try:
    import server as srv
except Exception as e:
    print(f"{SKIP}  server import failed, skipping map checks: {type(e).__name__}: {e}")
    srv = None

if srv is not None:
    vmap = srv._VAULT_DOMAIN_MAP
    bad = {v: d for v, d in vmap.items() if d not in rag.IMPORT_DOMAINS}
    check("every _VAULT_DOMAIN_MAP domain is a valid import domain", not bad,
          f"invalid: {bad}" if bad else "")
    check("System maps to the system namespace (not business)",
          vmap.get("System") == "system", f"got {vmap.get('System')!r}")
    check("Lore maps to the lore namespace (not general)",
          vmap.get("Lore") == "lore", f"got {vmap.get('Lore')!r}")
    check("Research is omitted from auto-import (firewalled)",
          "Research" not in vmap)

print("\n── Summary ──")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
