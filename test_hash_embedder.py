"""test_hash_embedder.py — the model-free hash-demo embedder used by the clean-install CI job (2026-10-22, workstream 5B)."""
import os
import subprocess
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))


sys.path.insert(0, HERE)
import embedding

h = embedding._HashEmbedder()
a = h.encode("Which supplier delivers beef trimmings?")
check("one text gives one vector of the index width", a.shape == (embedding.EMBED_DIM,), str(a.shape))
check("vectors are normalised", abs(float(np.linalg.norm(a)) - 1.0) < 1e-5)
check("the same text gives the same vector (deterministic across calls)", np.array_equal(a, h.encode("Which supplier delivers beef trimmings?")))
batch = h.encode(["supplier beef", "music tempo", ""])
check("a batch gives one row per text, an empty text a zero row", batch.shape == (3, embedding.EMBED_DIM) and not batch[2].any())
doc_near, doc_far = h.encode("Supplier notes: beef trimmings arrive Monday"), h.encode("The tempo of the track is 120 BPM")
check("a text sharing words is closer than one sharing none", float(a @ doc_near) > float(a @ doc_far))
ar = h.encode("تقرير المبيعات اليومي")
check("Arabic words are hashed too", ar.any() and float(ar @ h.encode("المبيعات اليومي")) > 0.5)
code = ("import embedding, numpy; v = embedding.get_embedder().encode('beef supplier'); "
        "import hashlib; print(embedding.table_name(), embedding.max_distance(), hashlib.sha256(v.tobytes()).hexdigest()[:16])")
outs = [subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True,
                       env=dict(os.environ, DAVID_EMBEDDER="hash-demo", PYTHONHASHSEED=str(seed))).stdout.strip() for seed in (1, 2)]
check("DAVID_EMBEDDER=hash-demo selects it, with its own table", outs[0].startswith("notes_hash_demo "), outs[0])
check("the vectors do not depend on Python's hash seed", outs[0] == outs[1] and outs[0] != "", str(outs))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
