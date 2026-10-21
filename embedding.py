# embedding.py -- the one place that decides which embedder and which LanceDB
# table David uses. rag.py, memory.py, file_watcher.py, tool_executor.py and
# turbo_ingest.py all go through get_embedder()/table_name() so they can never
# disagree (a vector from one model is meaningless against another model's index).
#
# DAVID_EMBEDDER=bge-m3 switches everything back to the old model AND the old
# table ("notes", left untouched by the switch) -- no code edit needed to roll back.

from ollama_config import OLLAMA_HOST
from david_config import DAVID_DATA
import os
import json
import time
import urllib.request
import urllib.error

import numpy as np

OLLAMA_EMBED_URL = OLLAMA_HOST + "/api/embed"

# Width of the LanceDB `vector` column. Fixed so schemas, the zero-vector scans in
# cross_domain_sweep.py / prepare_lora_dataset.py and existing tools keep working.
# A wider model is cut to this length (Qwen3-Embedding is trained Matryoshka-style,
# so a prefix of the vector is a valid lower-dimensional embedding) and re-normalized.
EMBED_DIM = 1024

# Qwen3-Embedding is instruction-aware on the QUERY side only; documents get no
# prefix. Qwen's own tests: omitting it costs ~1-5% retrieval quality.
QUERY_TASK = "Given a user question or request, retrieve the relevant notes, documents and records that answer it"

BACKENDS = {
    "qwen3-embedding-0.6b": {
        "kind": "ollama", "model": "qwen3-embedding:0.6b",
        "table": "notes_q3e", "query_instruct": True,
        # Cosine distance above which a vector hit is treated as "nothing relevant".
        # Measured 2026-10-03 on 100 questions over the live corpus + 30 off-topic queries:
        # 0.50 keeps 94% of true chunks and passes 13% of off-topic (bge-m3 @0.45: 87% / 17%).
        "max_distance": 0.50,
    },
    "bge-m3": {
        "kind": "st", "model": "BAAI/bge-m3",
        "table": "notes", "query_instruct": False,
        "max_distance": 0.45,
    },
    # No model at all: words hashed into a fixed vector (2026-10-22, workstream 5B). For the clean-install CI job and for trying
    # the pipeline without Ollama. It matches shared words, not meaning; never use it for a real index.
    "hash-demo": {
        "kind": "hash", "model": "hash-demo",
        "table": "notes_hash_demo", "query_instruct": False,
        "max_distance": 0.95,
    },
}

ACTIVE = os.environ.get("DAVID_EMBEDDER", "qwen3-embedding-0.6b")
if ACTIVE not in BACKENDS:
    raise RuntimeError(f"DAVID_EMBEDDER={ACTIVE!r} is not one of {sorted(BACKENDS)}")

_cfg = BACKENDS[ACTIVE]
_embedder = None


def table_name():
    return _cfg["table"]


def cache_db():
    """Path of turbo_ingest's 'already indexed' cache for the active table."""
    t = _cfg["table"]
    return str(DAVID_DATA / ("ingest_cache.db" if t == "notes" else f"ingest_cache_{t}.db"))   # portable (was a hard-coded backslash)


def max_distance():
    return _cfg["max_distance"]


def model_name():
    return _cfg["model"]


class _OllamaEmbedder:
    def __init__(self, model, query_instruct):
        self.model = model
        self.query_instruct = query_instruct

    def _post(self, inputs):
        # The options are pinned so every caller hits the same runner config -- a different
        # value per call site forces Ollama to reload the model (learned the hard way
        # with the chat models on 2026-09-29).
        # num_ctx 4096 must NOT be lowered: real chunks are long (Arabic ones especially: median 815
        # tokens, 5% over 1,900). Measured 2026-10-05 on 988 real chunks: at 2048, 30 of them embedded
        # to a different vector (cosine down to 0.85, i.e. their tails were cut off); at 1024, 143.
        # num_batch 256 is what shrinks the card footprint (2.00 -> 0.97 GB; the compute buffer scales
        # with the batch, not just the context). Worst chunk cosine vs the old setting 0.998, no chunk
        # under 0.99; on the 100-question benchmark recall@1/5/8 and 99 of 100 top-8 lists are unchanged.
        body = json.dumps({"model": self.model, "input": inputs,
                           "options": {"num_ctx": 4096, "num_batch": 256}}).encode()
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(OLLAMA_EMBED_URL, body,
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read())["embeddings"]
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Ollama embed call failed after 3 attempts: {last}")

    def encode(self, texts, normalize_embeddings=True, is_query=False, batch_size=32, **_ignored):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        # An empty string makes Ollama return an error for the whole batch.
        items = [t if t and t.strip() else " " for t in items]
        if is_query and self.query_instruct:
            items = [f"Instruct: {QUERY_TASK}\nQuery:{t}" for t in items]
        vecs = []
        for i in range(0, len(items), batch_size):
            vecs.extend(self._post(items[i:i + batch_size]))
        arr = np.asarray(vecs, dtype=np.float32)
        if arr.shape[1] < EMBED_DIM:
            raise RuntimeError(f"{self.model} returned {arr.shape[1]}-dim vectors; "
                               f"the index needs {EMBED_DIM}")
        arr = arr[:, :EMBED_DIM]
        if normalize_embeddings:
            arr = arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)
        return arr[0] if single else arr


class _STEmbedder:
    """The original bge-m3 path (sentence-transformers), kept for rollback and A/B."""

    def __init__(self, model):
        # lancedb/pyarrow must be imported before torch on Windows (native segfault
        # otherwise) -- callers already do that via rag.get_table().
        from sentence_transformers import SentenceTransformer
        device = os.environ.get("DAVID_EMBED_DEVICE", "cpu")
        print(f"  Loading {model} embedder on {device}...", flush=True)
        self._m = SentenceTransformer(model, device=device)
        print(f"  {model} ready.", flush=True)

    def encode(self, texts, normalize_embeddings=True, is_query=False, batch_size=32, **kw):
        return self._m.encode(texts, normalize_embeddings=normalize_embeddings,
                              batch_size=batch_size, **kw)


def doc_text(file, text):
    """The string a document chunk is EMBEDDED as (the stored `text` stays untouched).

    Only a document's first chunk carries the "File: ..." header; a mid-document chunk such
    as "6.3 TUNE: The TUNE knob changes the pitch..." never names its manual, so a query
    that names the manual ("what does the EPM manual say about tuning?") ranked it 34th-36th
    and the answer came back "the manual doesn't cover tuning". Prefixing the document name
    at embed time moved it to 6th-10th (measured 2026-10-03; recall@8 on the generic
    benchmark 87% -> 89%, recall@5 unchanged). Queries are never prefixed."""
    if text.startswith("File:") or not file:
        return text
    return f"Document: {file}\n{text}"


class _HashEmbedder:
    """Deterministic bag-of-words vectors: each word (lower-cased, Unicode letters and digits) adds +-1 at a hashed position."""

    def encode(self, texts, normalize_embeddings=True, is_query=False, batch_size=32, **_ignored):
        import hashlib
        import re
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        arr = np.zeros((len(items), EMBED_DIM), dtype=np.float32)
        for i, t in enumerate(items):
            for w in re.findall(r"\w+", str(t or "").lower()):
                h = int.from_bytes(hashlib.blake2b(w.encode("utf-8"), digest_size=8).digest(), "little")
                arr[i, h % EMBED_DIM] += 1.0 if (h >> 63) & 1 else -1.0
        if normalize_embeddings:
            arr = arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)
        return arr[0] if single else arr


def get_embedder():
    global _embedder
    if _embedder is None:
        if _cfg["kind"] == "ollama":
            _embedder = _OllamaEmbedder(_cfg["model"], _cfg["query_instruct"])
        elif _cfg["kind"] == "hash":
            _embedder = _HashEmbedder()
        else:
            _embedder = _STEmbedder(_cfg["model"])
    return _embedder
