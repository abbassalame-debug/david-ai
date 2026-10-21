# knowledge.py — David structured knowledge extraction
# Maintains a SQLite facts database built from LanceDB content.
# LLM extracts (entity, predicate, value) triples from each document chunk.
# At query time, FTS5 gives sub-millisecond precise lookups alongside RAG.
#
# Usage:
#   python knowledge.py build [domain]   — extract facts from all/one domain
#   python knowledge.py query <text>     — test a lookup
#   python knowledge.py stats            — row counts
#   python knowledge.py clear [domain]   — delete rows for a domain

from david_config import DAVID_DATA, DAVID_HOME, load_policy, default_scan_filter
import os
import re
import sys
import json
import sqlite3
import datetime
import urllib.request
from ollama_config import num_ctx_for, OLLAMA_HOST

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = str(DAVID_DATA / "knowledge.db")
OLLAMA  = OLLAMA_HOST + "/api/chat"
MODEL   = "qwen3.5:9b"

EXTRACT_PROMPT = """\
Extract structured facts from the text below.
Output ONLY blocks in this exact format:
ENTITY: <the subject: a person, place, organisation, product, or concept>
PREDICATE: <the attribute or relationship, 2-5 words, no numbers>
VALUE: <the stated value itself, one short sentence max>
---
Rules: ENTITY is who/what the fact is ABOUT; PREDICATE names the attribute; VALUE holds the actual number, name or detail.
Never put the value inside the predicate. Extract facts only from what is explicitly stated. Skip vague filler.
If the text is only a catalog stub (filename, path, size, date), output nothing.

Example:
TEXT: The Roland TR-808 was released in 1980 and has 16 steps.
ENTITY: Roland TR-808
PREDICATE: release year
VALUE: 1980
---
ENTITY: Roland TR-808
PREDICATE: number of steps
VALUE: 16
---

TEXT:
{text}"""


# Raw people tables (staff and student sheets): facts from them are never shown by query() and never extracted (nothing is deleted; they can be
# shown again by removing a marker). A fact is also hidden when its predicate is clearly personal, wherever it came from.
_PRIVACY = load_policy()["privacy"]          # config/vaults.toml [privacy]
SENSITIVE_PATH_MARKERS = tuple(_PRIVACY["sensitive_path_markers"])
SENSITIVE_PREDICATE = re.compile(_PRIVACY["sensitive_predicate"], re.I)


def is_sensitive_source(path: str) -> bool:
    return any(m in (path or "") for m in SENSITIVE_PATH_MARKERS)


def is_sensitive(source: str, predicate: str) -> bool:
    return is_sensitive_source(source) or bool(SENSITIVE_PREDICATE.search(predicate or ""))


def _is_catalog_stub(text: str) -> bool:
    # Index rows that carry only the "File: ... | Path: ..." header have nothing to extract; models
    # (llama3.1 and qwen3.5 alike) invent facts from them, so skip before spending an LLM call.
    body = "\n".join(l for l in text.splitlines() if not l.startswith(("File:", "Path:")))
    return len(body.strip()) < 40


# ── Schema ────────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                domain     TEXT    NOT NULL,
                entity     TEXT    NOT NULL,
                predicate  TEXT    NOT NULL,
                value      TEXT    NOT NULL,
                source     TEXT,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_chunks (
                chunk_id   TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
                USING fts5(entity, predicate, value,
                           content='facts', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, entity, predicate, value)
                VALUES (new.id, new.entity, new.predicate, new.value);
            END;
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, entity, predicate, value)
                VALUES ('delete', old.id, old.entity, old.predicate, old.value);
            END;
        """)


# ── Extraction ────────────────────────────────────────────────────────────────

def _call_llm(text: str) -> str:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": EXTRACT_PROMPT.format(text=text[:4000])}
        ],
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx_for(MODEL), "temperature": 0}
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["message"]["content"].strip()


def _parse_facts(raw: str) -> list[dict]:
    facts = []
    current = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("ENTITY:"):
            current = {"entity": line[7:].strip()}
        elif line.startswith("PREDICATE:") and "entity" in current:
            current["predicate"] = line[10:].strip()
        elif line.startswith("VALUE:") and "predicate" in current:
            current["value"] = line[6:].strip()
            if all(current.get(k) for k in ("entity", "predicate", "value")):
                facts.append(dict(current))
            current = {}
    return facts


def extract_facts(text: str, domain: str, source: str, chunk_id: str) -> int:
    """Extract and store facts from one chunk. Returns number of new facts."""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM processed_chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        if row:
            return 0

    try:
        raw   = "" if _is_catalog_stub(text) else _call_llm(text)
        facts = _parse_facts(raw)
    except Exception as e:
        print(f"    [Knowledge] LLM error: {e}", flush=True)
        return 0

    if not facts:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO processed_chunks VALUES (?,?)",
                (chunk_id, datetime.datetime.now().isoformat())
            )
        return 0

    now = datetime.datetime.now().isoformat()
    with _conn() as c:
        for f in facts:
            c.execute(
                "INSERT INTO facts (domain,entity,predicate,value,source,created_at) VALUES (?,?,?,?,?,?)",
                (domain, f["entity"], f["predicate"], f["value"], source, now)
            )
        c.execute(
            "INSERT OR IGNORE INTO processed_chunks VALUES (?,?)",
            (chunk_id, now)
        )
    return len(facts)


# ── Build index from LanceDB ──────────────────────────────────────────────────

def _scan_table(table, filt: str) -> list:
    try:
        return table.to_lance().to_table(filter=filt).to_pylist()
    except Exception as e:
        print(f"  [Knowledge] Table scan failed: {e}")
        return []


def build_index(domain: str = None):
    """Iterate LanceDB chunks and extract structured facts. Idempotent."""
    sys.path.insert(0, str(DAVID_HOME))
    import rag as rag_module

    init_db()
    table = rag_module.get_table()

    filt = f"vault = '{domain}'" if domain else default_scan_filter()   # no domain: memory and every firewalled vault stay out
    rows = _scan_table(table, filt)

    total_facts = 0
    total_chunks = 0
    skipped = 0

    print(f"  Scanning {len(rows)} chunks (domain={domain or 'all'})...", flush=True)
    for i, row in enumerate(rows):
        chunk_id = row.get("id") or row.get("id_", "")
        text     = row.get("text", "")
        dom      = row.get("vault", domain or "system")
        source   = row.get("source_path", "")

        if not chunk_id or not text or len(text) < 60 or is_sensitive_source(source):
            skipped += 1
            continue

        n = extract_facts(text, dom, source, chunk_id)
        if n > 0:
            total_facts += n
        total_chunks += 1

        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(rows)} chunks processed, {total_facts} facts extracted", flush=True)

    print(f"\n  Done: {total_chunks} chunks → {total_facts} new facts ({skipped} skipped).")


# ── Query ─────────────────────────────────────────────────────────────────────

def _fts_query(question: str) -> str:
    """Convert a natural-language query to FTS5 OR-match expression."""
    import re
    # keep only word characters (FTS5 syntax chars would break the query)
    words = re.findall(r"\w+", question)
    # filter stop-words shorter than 3 chars and generic terms
    STOP = {"the","is","in","of","for","a","an","to","on","at","and","or","my","me","i"}
    words = [w for w in words if len(w) >= 3 and w.lower() not in STOP]
    if not words:
        return question
    return " OR ".join(words)


def query(question: str, domain: str = None, top_k: int = 8, domains: list | None = None) -> str:
    """FTS5 keyword search over facts. Returns formatted context string or ''.
    domains: when given, only facts whose domain (= the vault the fact was extracted from) is in this list are
    returned; an empty list returns '' (an agent with no vault gets no facts)."""
    if domains is not None:
        domains = [d for d in domains if isinstance(d, str)]
        if domain:
            domains = [d for d in domains if d == domain]
        if not domains:
            return ""
    fts_expr = _fts_query(question)
    try:
        with _conn() as c:
            if domains is not None:
                marks = ",".join("?" * len(domains))
                rows = c.execute(f"""
                    SELECT f.entity, f.predicate, f.value, f.domain, f.source
                    FROM facts_fts fts
                    JOIN facts f ON fts.rowid = f.id
                    WHERE facts_fts MATCH ? AND f.domain IN ({marks})
                    ORDER BY rank
                    LIMIT ?
                """, (fts_expr, *domains, top_k * 6)).fetchall()
            elif domain:
                rows = c.execute("""
                    SELECT f.entity, f.predicate, f.value, f.domain, f.source
                    FROM facts_fts fts
                    JOIN facts f ON fts.rowid = f.id
                    WHERE facts_fts MATCH ? AND f.domain = ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_expr, domain, top_k * 6)).fetchall()
            else:
                rows = c.execute("""
                    SELECT f.entity, f.predicate, f.value, f.domain, f.source
                    FROM facts_fts fts
                    JOIN facts f ON fts.rowid = f.id
                    WHERE facts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_expr, top_k * 6)).fetchall()
        if domains is not None:
            rows = [r for r in rows if r["domain"] in domains]     # belt and braces on top of the SQL filter
        rows = [r for r in rows if not is_sensitive(r["source"], r["predicate"])][:top_k]

        if not rows:
            return ""

        lines = ["--- Structured facts from knowledge base ---"]
        for r in rows:
            lines.append(f"• {r['entity']} — {r['predicate']}: {r['value']}")
        lines.append("--- End of facts ---")
        return "\n".join(lines)

    except Exception:
        return ""


def recall(question: str, domain: str = None, top_k: int = 8, domains: list | None = None) -> str:
    """The agent-facing lookup used by david_router.rag_context. It must be told which vaults the agent may see
    (domains=AGENT_NAMESPACES[agent]); without that list it returns nothing (fail closed, audit finding S-04).
    The operator's own CLI (`python knowledge.py query ...`) uses query(), which keeps its all-domain default."""
    if domains is None:
        return ""
    return query(question, domain=domain, top_k=top_k, domains=domains)


# ── Stats / Maintenance ───────────────────────────────────────────────────────

def stats():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        chunks = c.execute("SELECT COUNT(*) FROM processed_chunks").fetchone()[0]
        print(f"  Facts:  {total}")
        print(f"  Chunks processed: {chunks}")
        hidden = sum(1 for r in c.execute("SELECT predicate, source FROM facts") if is_sensitive(r["source"], r["predicate"]))
        print(f"  Hidden by the privacy filter (staff/student sheets, personal details): {hidden}")
        print("\n  By domain:")
        for row in c.execute("SELECT domain, COUNT(*) as n FROM facts GROUP BY domain ORDER BY n DESC"):
            print(f"    {row['domain']:16s} {row['n']}")
        print("\n  Sample facts:")
        for row in c.execute("SELECT entity, predicate, value FROM facts ORDER BY RANDOM() LIMIT 5"):
            print(f"    {row['entity']} | {row['predicate']} | {row['value']}")


def clear(domain: str = None):
    with _conn() as c:
        if domain:
            ids = [r[0] for r in c.execute("SELECT id FROM facts WHERE domain=?", (domain,))]
            c.execute("DELETE FROM facts WHERE domain=?", (domain,))
            c.execute("DELETE FROM processed_chunks WHERE chunk_id LIKE ?", (f"{domain}::%",))
            print(f"  Cleared {len(ids)} facts for domain '{domain}'.")
        else:
            c.execute("DELETE FROM facts")
            c.execute("DELETE FROM processed_chunks")
            print("  Cleared all facts.")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python knowledge.py build [domain]   — extract facts from LanceDB")
        print("  python knowledge.py query <text>     — test a lookup")
        print("  python knowledge.py stats            — counts + sample")
        print("  python knowledge.py clear [domain]   — delete facts")
        sys.exit(0)

    init_db()

    if args[0] == "build":
        dom = args[1] if len(args) > 1 else None
        build_index(dom)

    elif args[0] == "query" and len(args) > 1:
        q = " ".join(args[1:])
        result = query(q)
        print(result or "(no facts found)")

    elif args[0] == "stats":
        stats()

    elif args[0] == "clear":
        dom = args[1] if len(args) > 1 else None
        clear(dom)

    else:
        print(f"Unknown command: {args[0]}")
