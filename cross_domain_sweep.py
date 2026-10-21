# cross_domain_sweep.py -- weekly ANN sweep for cross-domain insights
# Embeds the week's new chunks, compares against other-domain centroids,
# surfaces top-k cross-domain neighbors into the Monday briefing.
# Run: python cross_domain_sweep.py   (add to Task Scheduler for Mondays 07:00)

from david_config import DAVID_HOME, DAVID_VAULTS
import sys
import os
import json
import datetime
import urllib.request
from ollama_config import num_ctx_for, OLLAMA_HOST
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(DAVID_HOME))
import rag as rag_module

BRIEFINGS  = str(DAVID_VAULTS / "System" / "Briefings")
OLLAMA     = OLLAMA_HOST + "/api/chat"
SIM_THRESH = 0.58   # chunk-vs-other-vault-centroid cosine; ~p99.9 of Qwen3-Embedding pairs (0.72 was bge-m3's and never fired)
TOP_K      = 8      # max candidates to surface


def cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def get_week_chunks():
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    table  = rag_module.get_table()
    rows   = table.search([0.0] * 1024).limit(5000).to_list()
    return [r for r in rows if r.get("date_modified", "") >= cutoff]


def get_domain_centroids(all_rows):
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in all_rows:
        buckets[r["vault"]].append(r["vector"])
    centroids = {}
    for vault, vecs in buckets.items():
        arr = np.array(vecs)
        centroids[vault] = arr.mean(axis=0).tolist()
    return centroids


def find_cross_domain_hits(new_chunks, centroids):
    hits = []
    for chunk in new_chunks:
        chunk_vault = chunk["vault"]
        for other_vault, centroid in centroids.items():
            if other_vault == chunk_vault:
                continue
            sim = cosine(chunk["vector"], centroid)
            if sim >= SIM_THRESH:
                hits.append({
                    "sim":         round(sim, 3),
                    "from_vault":  chunk_vault,
                    "to_vault":    other_vault,
                    "file":        chunk["file"],
                    "text":        chunk["text"][:300],
                })
    hits.sort(key=lambda x: x["sim"], reverse=True)
    return hits[:TOP_K]


def ask_ollama(prompt):
    body = {
        "model": "qwen3.5:9b",
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": num_ctx_for("qwen3.5:9b"), "temperature": 0.3}
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["message"]["content"]


def main():
    print("  Running cross-domain sweep...")
    table = rag_module.get_table()
    all_rows    = table.search([0.0] * 1024).limit(10000).to_list()
    new_chunks  = get_week_chunks()
    centroids   = get_domain_centroids(all_rows)
    hits        = find_cross_domain_hits(new_chunks, centroids)

    if not hits:
        print("  No cross-domain connections found this week.")
        return

    hits_text = "\n\n".join([
        f"[{h['from_vault']} -> {h['to_vault']}] similarity {h['sim']}\nFile: {h['file']}\n{h['text']}"
        for h in hits
    ])

    prompt = f"""You are David, the operator's AI chief of staff.
The system has detected {len(hits)} potential cross-domain connections between different knowledge vaults this week.
Summarize these as actionable insights for Demo User — what do these connections suggest he should consider?
Be specific and direct. 2-3 sentences per connection maximum.

CROSS-DOMAIN HITS:
{hits_text}"""

    print("  Synthesizing insights with qwen3.5:9b...")
    synthesis = ask_ollama(prompt)

    date_str  = datetime.date.today().strftime("%Y-%m-%d")
    filepath  = os.path.join(BRIEFINGS, f"{date_str}.md")
    section   = f"\n\n## Cross-Domain Insights -- Weekly Sweep\n\n{synthesis}\n"

    os.makedirs(BRIEFINGS, exist_ok=True)
    if os.path.exists(filepath):
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(section)
        print(f"  Appended to: {filepath}")
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Monday Briefing -- {date_str}\n{section}")
        print(f"  Created: {filepath}")

    print(f"\n{synthesis}\n")


def run_link_graph():
    try:
        import link_graph
        print("\n  Running wikilink bridge detection...")
        G, node_vault = link_graph.build_graph()
        bridges = link_graph.find_bridges(G, node_vault)
        if bridges:
            synthesis = link_graph.synthesize(bridges)
            date_str  = datetime.date.today().strftime("%Y-%m-%d")
            filepath  = os.path.join(BRIEFINGS, f"{date_str}.md")
            section   = f"\n\n## Wikilink Bridge Insights\n\n{synthesis}\n"
            os.makedirs(BRIEFINGS, exist_ok=True)
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(section)
            print(f"  Bridge insights appended to briefing.")
    except Exception as e:
        print(f"  Link graph skipped: {e}")


if __name__ == "__main__":
    main()
    run_link_graph()
