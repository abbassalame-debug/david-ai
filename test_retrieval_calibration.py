"""test_retrieval_calibration.py — retrieval thresholds as settings, and one row per passage (2026-10-21, audit P2-1).

  A  config/vaults.toml [retrieval] min_fts_score / max_distance are read; keyword hits below the floor are ignored, hits
     above it are fused; the floor is a per-corpus setting (BM25 scores grow with the corpus);
  B  retrieve() keeps one row per passage: a copy of a higher-ranked text (same body, any file or header) gives its slot to
     the next distinct passage, also in the cross-vault fallback; authorization still applies to everything.

No index, no model: a fake LanceDB table and a fake embedder are patched into rag.
"""
from david_config import DAVID_HOME, load_policy
import sys
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

import rag
import embedding


def row(rid, vault, body, file=None, distance=None, score=None):
    r = {"id": rid, "vault": vault, "file": file or f"{rid}.md", "text": f"File: {file or rid}\nPath: x\n{body}"}
    if distance is not None:
        r["_distance"] = distance
    if score is not None:
        r["_score"] = score
    return r


class Query:
    def __init__(self, rows): self.rows = rows
    def metric(self, *_): return self
    def where(self, flt, prefilter=False):
        allowed = {p.split("'")[1] for p in flt.split(" OR ")}
        return Query([r for r in self.rows if r["vault"] in allowed])
    def limit(self, n): return Query(self.rows[:n])
    def to_list(self): return [dict(r) for r in self.rows]


class Table:
    def __init__(self, vec_rows, fts_rows): self.vec_rows, self.fts_rows = vec_rows, fts_rows
    def search(self, q, vector_column_name=None, query_type=None):
        return Query(self.fts_rows if query_type == "fts" else self.vec_rows)


class Embedder:
    def encode(self, *a, **k):
        class V:
            def tolist(self): return [0.0]
        return V()


def retrieve(vec_rows, fts_rows=(), agent="analyst", top_k=3):
    with mock.patch.object(rag, "get_table", lambda: Table(list(vec_rows), list(fts_rows))), mock.patch.object(rag, "get_embedder", lambda: Embedder()):
        return [r["id"] for r in rag.retrieve(agent, "q", top_k=top_k)]


# ── A. thresholds are settings ───────────────────────────────────────────────────────────────────────────────────────
print("\n── A. [retrieval] settings ──")
ret = load_policy().get("retrieval", {})
check("the BM25 floor comes from [retrieval] min_fts_score", rag.MIN_FTS_SCORE == float(ret.get("min_fts_score", 15)), str(rag.MIN_FTS_SCORE))
check("the vector ceiling comes from [retrieval] max_distance, else the embedder's calibrated default",
      rag.MAX_RELEVANT_DISTANCE == float(ret.get("max_distance", embedding.max_distance())), str(rag.MAX_RELEVANT_DISTANCE))
vec = [row("v1", "business", "alpha one", distance=0.20), row("v2", "business", "alpha two", distance=0.30), row("v3", "business", "alpha three", distance=0.35)]
floor = rag.MIN_FTS_SCORE
below = [row("n1", "business", "coincidental number", score=floor - 0.5)]
check("a keyword hit just below the floor is ignored: vector order unchanged", retrieve(vec, below) == ["v1", "v2", "v3"])
above = [row("k1", "business", "exact keyword match", score=floor + 0.5)]
check("a keyword hit just above the floor is fused into the results", "k1" in retrieve(vec, above))
with mock.patch.object(rag, "MIN_FTS_SCORE", 30.0):
    check("raising the floor (a large corpus) drops a 29.8 hit that the default keeps", "k2" not in retrieve(vec, [row("k2", "business", "x", score=29.8)]))
check("…which the default floor fuses", floor < 29.8 and "k2" in retrieve(vec, [row("k2", "business", "x", score=29.8)]))
check("the vector ceiling still drops rows beyond it", retrieve([row("x1", "business", "far away", distance=rag.MAX_RELEVANT_DISTANCE + 0.2)]) == [])

# ── B. one row per passage ───────────────────────────────────────────────────────────────────────────────────────────
print("\n── B. duplicate passages give their slot to the next distinct one ──")
vec = [row("a1", "business", "Monthly supplier terms: pay within 30 days.", file="2024/terms.md", distance=0.10),
       row("a2", "business", "Monthly supplier terms:   pay within 30 days.", file="backup/terms copy.md", distance=0.11),
       row("a3", "business", "MONTHLY SUPPLIER TERMS: PAY WITHIN 30 DAYS.", file="old/TERMS.md", distance=0.12),
       row("b1", "business", "Fryer maintenance every Monday.", distance=0.20),
       row("c1", "business", "Price list for October.", distance=0.25)]
got = retrieve(vec)
check("three copies of one passage keep only the best-ranked; the next two distinct passages fill the slots", got == ["a1", "b1", "c1"], str(got))
fused = [row("a2", "business", "Monthly supplier terms: pay within 30 days.", file="backup/terms copy.md", score=60.0)]
got = retrieve(vec, fused)
check("…also after BM25 fusion (a copy that ranks first through RRF still counts once)", len(got) == 3 and sum(i in ("a1", "a2", "a3") for i in got) == 1, str(got))
check("header lines (File:/Path:) are ignored when comparing", rag._text_key("File: a\nPath: b\nsame body") == rag._text_key("File: z\nPath: y\nsame  body"))
check("different passages are all kept", rag._distinct([{"text": "one"}, {"text": "two"}, {"text": "three"}]) == [{"text": "one"}, {"text": "two"}, {"text": "three"}])
check("rows without text are kept as they are", len(rag._distinct([{"id": 1}, {"id": 2, "text": ""}, {"id": 3, "text": None}])) == 3)
vec = [row("l1", "lore", "The lighthouse keeper's oath.", distance=0.10),
       row("m1", "business", "The lighthouse keeper's oath.", file="copy.md", distance=0.12),
       row("m2", "business", "A different business note.", distance=0.20)]
got = retrieve(vec, agent="lore_keeper", top_k=3)
check("the cross-vault fallback does not add a copy of a passage already in the results", "m1" not in got and "l1" in got and "m2" in got, str(got))
vec = [row("r1", "research", "firewalled text", distance=0.05), row("s1", "system", "system text", distance=0.10)]
got = retrieve(vec, agent="performance_coach")
check("rows from a vault the agent may not see are still dropped", got == ["s1"], str(got))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
