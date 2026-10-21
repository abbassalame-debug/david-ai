"""
test_embedding.py -- offline tests for embedding.py (the Qwen3-Embedding switch).

No Ollama, no lancedb table, no torch: urllib is replaced with a recorder so the
tests pin down the contract -- query vs document formatting, output width and
normalization, batching, retry, backend/table/cache selection -- not the model.

Run: python test_embedding.py
"""

from david_config import DAVID_HOME
import sys, os, json, inspect, importlib.util
import urllib.error
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))

import numpy as np
import embedding


class _Resp:
    def __init__(self, payload): self._p = payload
    def read(self): return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeOllama:
    """Replaces urllib.request.urlopen inside embedding.py; records request bodies."""
    def __init__(self, dim=1024, fail_first=0):
        self.dim, self.fail_first, self.calls = dim, fail_first, []
    def __call__(self, req, timeout=None):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise urllib.error.URLError("connection refused")
        body = json.loads(req.data)
        self.calls.append(body)
        rng = np.random.default_rng(len(self.calls))
        # Deliberately NOT unit-length, so normalization is observable.
        embs = (rng.standard_normal((len(body["input"]), self.dim)) * 3).tolist()
        return _Resp({"embeddings": embs})


def with_fake(fake, fn):
    real_open, real_sleep = embedding.urllib.request.urlopen, embedding.time.sleep
    embedding.urllib.request.urlopen = fake
    embedding.time.sleep = lambda s: None
    try:
        return fn()
    finally:
        embedding.urllib.request.urlopen, embedding.time.sleep = real_open, real_sleep


print("\n-- query vs document formatting --")
emb = embedding._OllamaEmbedder("qwen3-embedding:0.6b", True)

f = _FakeOllama()
with_fake(f, lambda: emb.encode("what is the rent?", is_query=True))
sent = f.calls[0]["input"][0]
check("query gets the Instruct/Query prefix",
      sent.startswith("Instruct: ") and sent.endswith("\nQuery:what is the rent?"), repr(sent[:60]))

f = _FakeOllama()
with_fake(f, lambda: emb.encode("rent is due on the 1st"))
check("document is sent verbatim, no prefix", f.calls[0]["input"] == ["rent is due on the 1st"])

noprefix = embedding._OllamaEmbedder("x", False)
f = _FakeOllama()
with_fake(f, lambda: noprefix.encode("q", is_query=True))
check("backend with query_instruct=False never prefixes", f.calls[0]["input"] == ["q"])

check("the runner options are pinned (varying them forces Ollama reloads)",
      f.calls[0]["options"] == {"num_ctx": 4096, "num_batch": 256})
check("num_ctx stays 4096: lowering it truncates real chunks (2048 changed 30 of 988 vectors, 1024 changed 143)",
      f.calls[0]["options"]["num_ctx"] >= 4096)
check("num_batch 256 keeps the embedder at ~1 GB of VRAM instead of 2 GB (measured 2026-10-05)",
      f.calls[0]["options"]["num_batch"] == 256)


print("\n-- output shape, width and normalization --")
f = _FakeOllama()
v = with_fake(f, lambda: emb.encode("hello"))
check("str input -> 1-D vector of EMBED_DIM", v.shape == (embedding.EMBED_DIM,), str(v.shape))
check("vector is unit length", abs(float(np.linalg.norm(v)) - 1.0) < 1e-5)
check(".tolist() works (every caller does this)", len(v.tolist()) == embedding.EMBED_DIM)

f = _FakeOllama()
m = with_fake(f, lambda: emb.encode(["a", "b", "c"]))
check("list input -> 2-D array", m.shape == (3, embedding.EMBED_DIM), str(m.shape))
check("[query][0].tolist() pattern used by tool_executor works",
      len(with_fake(_FakeOllama(), lambda: emb.encode(["q"], is_query=True))[0].tolist()) == embedding.EMBED_DIM)

f = _FakeOllama(dim=2560)
w = with_fake(f, lambda: emb.encode("wide model"))
check("a wider model is cut to EMBED_DIM", w.shape == (embedding.EMBED_DIM,), str(w.shape))
check("...and re-normalized after the cut", abs(float(np.linalg.norm(w)) - 1.0) < 1e-5)

try:
    with_fake(_FakeOllama(dim=512), lambda: emb.encode("too narrow"))
    check("a model narrower than the index is rejected", False)
except RuntimeError as e:
    check("a model narrower than the index is rejected", "needs 1024" in str(e))

raw = with_fake(_FakeOllama(), lambda: emb.encode("x", normalize_embeddings=False))
check("normalize_embeddings=False leaves the vector unscaled", abs(float(np.linalg.norm(raw)) - 1.0) > 0.5)


print("\n-- batching, blanks, retry --")
f = _FakeOllama()
with_fake(f, lambda: emb.encode([f"t{i}" for i in range(70)], batch_size=32))
check("70 items at batch_size=32 -> 3 requests of 32/32/6",
      [len(c["input"]) for c in f.calls] == [32, 32, 6], str([len(c["input"]) for c in f.calls]))

f = _FakeOllama()
with_fake(f, lambda: emb.encode(["", "   ", "ok"]))
check("empty/whitespace text is replaced (Ollama errors on a blank input)",
      f.calls[0]["input"] == [" ", " ", "ok"], repr(f.calls[0]["input"]))

f = _FakeOllama(fail_first=2)
r = with_fake(f, lambda: emb.encode("flaky"))
check("transient connection errors are retried", r.shape == (embedding.EMBED_DIM,) and len(f.calls) == 1)

try:
    with_fake(_FakeOllama(fail_first=99), lambda: emb.encode("dead"))
    check("gives up with a clear error after 3 attempts", False)
except RuntimeError as e:
    check("gives up with a clear error after 3 attempts", "failed after 3 attempts" in str(e))

check("show_progress_bar kwarg from turbo_ingest's call is accepted",
      with_fake(_FakeOllama(), lambda: emb.encode(["a"], batch_size=8, show_progress_bar=False)).shape[0] == 1)


print("\n-- backend / table / cache selection --")
def load_with_env(value):
    old = os.environ.get("DAVID_EMBEDDER")
    if value is None: os.environ.pop("DAVID_EMBEDDER", None)
    else: os.environ["DAVID_EMBEDDER"] = value
    try:
        spec = importlib.util.spec_from_file_location("embedding_probe", str(DAVID_HOME / "embedding.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None: os.environ.pop("DAVID_EMBEDDER", None)
        else: os.environ["DAVID_EMBEDDER"] = old

d = load_with_env(None)
check("default backend is Qwen3-Embedding-0.6B on its own table",
      d.ACTIVE == "qwen3-embedding-0.6b" and d.table_name() == "notes_q3e" and d.model_name() == "qwen3-embedding:0.6b")
check("its ingest cache is separate from the legacy one",
      d.cache_db().endswith("ingest_cache_notes_q3e.db"))

b = load_with_env("bge-m3")
check("DAVID_EMBEDDER=bge-m3 rolls back to the legacy table",
      b.table_name() == "notes" and b.model_name() == "BAAI/bge-m3")
check("...and the legacy cache file", os.path.basename(b.cache_db()) == "ingest_cache.db")

try:
    load_with_env("nonsense")
    check("an unknown DAVID_EMBEDDER value fails loudly", False)
except RuntimeError as e:
    check("an unknown DAVID_EMBEDDER value fails loudly", "nonsense" in str(e))

check("EMBED_DIM matches the LanceDB schema width in rag.py",
      "pa.list_(pa.float32(), 1024)" in open(str(DAVID_HOME / "rag.py"), encoding="utf-8").read()
      and embedding.EMBED_DIM == 1024)


print("\n-- every consumer follows embedding.py --")
import rag, memory, tool_executor, turbo_ingest as ti
check("rag.TABLE_NAME comes from embedding.py", rag.TABLE_NAME == embedding.table_name())
check("rag threshold comes from embedding.py", rag.MAX_RELEVANT_DISTANCE == embedding.max_distance())
check("rag.get_embedder() is the shared singleton", rag.get_embedder() is embedding.get_embedder())
check("turbo_ingest uses the same table and cache", ti.TABLE_NAME == embedding.table_name() and ti.CACHE_DB == embedding.cache_db())
check("rag.ingest_all clears the cache of the table it actually rebuilds", rag.TURBO_CACHE_DB == embedding.cache_db())
check("rag.retrieve embeds the QUERY as a query", "is_query=True" in inspect.getsource(rag.retrieve))
check("memory.recall embeds the QUERY as a query", "is_query=True" in inspect.getsource(memory.recall))
# since 2026-10-18 (audit S-03) the knowledge tool delegates to rag.retrieve, so it inherits rag.retrieve's query embedding
check("tool_executor knowledge search embeds the QUERY as a query (through rag.retrieve)",
      "_rag.retrieve(" in inspect.getsource(tool_executor._query_knowledge) and "is_query=True" in inspect.getsource(rag.retrieve))
check("document ingest paths do NOT use the query prefix",
      "is_query" not in inspect.getsource(rag.ingest_file) and "is_query" not in inspect.getsource(rag.import_folder)
      and "is_query" not in inspect.getsource(memory.save_episode))


print("\n-- document-name context (embedding.doc_text) --")
check("a mid-document chunk is embedded with its document name",
      embedding.doc_text(r"Eastern Percussion\Documentation\EPM Manual.pdf", "6.3 TUNE: changes the pitch")
      == "Document: Eastern Percussion\\Documentation\\EPM Manual.pdf\n6.3 TUNE: changes the pitch")
check("a first chunk (already carries the File: header) is left as is",
      embedding.doc_text("x.pdf", "File: x.pdf | document | 1KB\nIntro") == "File: x.pdf | document | 1KB\nIntro")
check("an empty file name never produces a dangling prefix", embedding.doc_text("", "text") == "text")
check("doc_text does not touch the query side (queries are formatted by the embedder only)",
      "doc_text" not in inspect.getsource(embedding._OllamaEmbedder.encode))
_src = {n: open(os.path.join(str(DAVID_HOME), *n.split("/")), encoding="utf-8").read()       # portable paths (Linux CI)
        for n in ("rag.py", "file_watcher.py", "turbo_ingest.py", "scripts/reembed_table.py")}
check("rag.ingest_file embeds chunks via doc_text", "embedding.doc_text(rel_path, chunk)" in _src["rag.py"])
check("rag.import_folder embeds chunks via doc_text", "embedding.doc_text(rel, chunk)" in _src["rag.py"])
check("file_watcher embeds chunks via doc_text", "embedding.doc_text(os.path.basename(fpath), chunk)" in _src["file_watcher.py"])
check("turbo_ingest embeds chunks via doc_text", "pending_text.append(embedding.doc_text(rel, chunk))" in _src["turbo_ingest.py"])
check("reembed_table.py re-embeds via doc_text (a migration must not undo the context)",
      "embedding.doc_text(r[\"file\"], r[\"text\"])" in _src["scripts/reembed_table.py"])
check("queries still go through is_query=True, not doc_text",
      "doc_text" not in inspect.getsource(rag.retrieve) and "doc_text" not in inspect.getsource(memory.recall))

print("\n-- Summary --")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(1 if failed else 0)
