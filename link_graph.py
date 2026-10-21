# link_graph.py -- Obsidian wikilink graph + betweenness centrality bridge detection
# Finds notes that bridge two domain clusters and surfaces them as candidate insights.
# Plan reference: "Compute betweenness centrality; notes bridging two domain clusters
#                  are surfaced as candidate insights. NetworkX, ~50 lines."
#
# Run: python link_graph.py          -- print top bridge notes
#      python link_graph.py brief    -- append bridges to today's briefing

from david_config import DAVID_VAULTS
import sys
import os
import re
import json
import datetime
import urllib.request
from ollama_config import num_ctx_for, OLLAMA_HOST
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import networkx as nx
except ImportError:
    sys.exit("Run: pip install networkx")

VAULTS = {
    "system":         str(DAVID_VAULTS / "System"),
    "business":       str(DAVID_VAULTS / "Business"),
    "music":          str(DAVID_VAULTS / "Music"),
    "lore":           str(DAVID_VAULTS / "Lore"),
    "research": str(DAVID_VAULTS / "Research"),
}

BRIEFINGS    = str(DAVID_VAULTS / "System" / "Briefings")
OLLAMA       = OLLAMA_HOST + "/api/chat"
TOP_K        = 12
WIKILINK_RE  = re.compile(r"\[\[([^\]|#\n]+)")


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph():
    G          = nx.DiGraph()
    node_vault = {}

    # Pass 1: register every note as a node
    for vault_name, vault_path in VAULTS.items():
        root = Path(vault_path)
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            name = md.stem
            if name not in G:
                G.add_node(name)
            node_vault[name] = vault_name   # last vault wins for cross-vault names

    # Pass 2: extract wikilinks and add edges
    for vault_name, vault_path in VAULTS.items():
        root = Path(vault_path)
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            source = md.stem
            try:
                content = md.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in WIKILINK_RE.finditer(content):
                target = m.group(1).strip()
                if not target:
                    continue
                if target not in node_vault:
                    G.add_node(target)
                    node_vault[target] = vault_name  # dangling link, assign to source vault
                G.add_edge(source, target)

    return G, node_vault


# ── Bridge detection ──────────────────────────────────────────────────────────

def find_bridges(G, node_vault):
    if len(G.nodes) < 3:
        return []

    centrality = nx.betweenness_centrality(G, normalized=True)

    bridges = []
    for node, score in sorted(centrality.items(), key=lambda x: x[1], reverse=True):
        if score == 0:
            continue
        home = node_vault.get(node, "unknown")
        cross_vaults = set()
        for nb in list(G.predecessors(node)) + list(G.successors(node)):
            v = node_vault.get(nb, "unknown")
            if v != home:
                cross_vaults.add(v)
        if cross_vaults:
            bridges.append({
                "note":       node,
                "vault":      home,
                "centrality": round(score, 4),
                "bridges_to": sorted(cross_vaults),
            })
        if len(bridges) >= TOP_K:
            break

    return bridges


# ── LLM synthesis ─────────────────────────────────────────────────────────────

def ask_ollama(prompt):
    body = {
        "model": "qwen3.5:9b",
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": num_ctx_for("qwen3.5:9b"), "temperature": 0.3},
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["message"]["content"]


def synthesize(bridges):
    lines = "\n".join([
        f"- [{b['vault']}] \"{b['note']}\" bridges to: {', '.join(b['bridges_to'])}  (centrality {b['centrality']})"
        for b in bridges
    ])
    prompt = f"""You are David, the operator's AI chief of staff.
The wikilink graph across his Obsidian vaults has identified {len(bridges)} notes that act as bridges between different knowledge domains.
High betweenness centrality means these notes are on many shortest paths between other notes — they are structural connectors.

For each bridge note, suggest one concrete action or question Demo User should consider based on the connection it implies.
Be specific. One sentence per note maximum.

BRIDGE NOTES:
{lines}"""
    return ask_ollama(prompt)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    brief_mode = len(sys.argv) > 1 and sys.argv[1] == "brief"

    print("  Building wikilink graph...", flush=True)
    G, node_vault = build_graph()
    print(f"  Graph: {G.number_of_nodes()} notes, {G.number_of_edges()} links", flush=True)

    if G.number_of_nodes() < 3:
        print("  Not enough notes to compute bridges. Add notes to your vaults first.")
        return

    print("  Computing betweenness centrality...", flush=True)
    bridges = find_bridges(G, node_vault)

    if not bridges:
        print("  No cross-domain bridges found.")
        return

    print(f"\n  Top {len(bridges)} bridge notes:\n")
    for b in bridges:
        print(f"  [{b['vault']:>14}]  {b['note']:<40}  -> {', '.join(b['bridges_to'])}")

    print("\n  Synthesizing insights...", flush=True)
    synthesis = synthesize(bridges)
    print(f"\n{synthesis}\n")

    if brief_mode:
        date_str = datetime.date.today().strftime("%Y-%m-%d")
        filepath = os.path.join(BRIEFINGS, f"{date_str}.md")
        section  = f"\n\n## Wikilink Bridge Insights\n\n{synthesis}\n"
        os.makedirs(BRIEFINGS, exist_ok=True)
        if os.path.exists(filepath):
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(section)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# Briefing -- {date_str}\n{section}")
        print(f"  Appended to: {filepath}")


if __name__ == "__main__":
    main()
