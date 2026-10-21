"""
reembed_table.py -- copy a LanceDB table into a new one, re-embedding every row's
stored `text` with the ACTIVE embedder (embedding.py). No source files are read, so
switching embedders costs minutes instead of re-running the whole file ingest.

Non-destructive: the source table is only read. Resumable: ids already present in
the destination are skipped, so an interrupted run just continues.

  py -3.11 scripts\\reembed_table.py --src notes [--dst notes_q3e] [--batch 64] [--limit N]

--dst defaults to the active embedder's table. --limit is for smoke tests.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, time, argparse
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

import lancedb          # before anything torch-related -- see rag.py note
import pyarrow as pa
import embedding

COLS = ["id", "vault", "file", "chunk_index", "text", "date_modified", "source_path", "asset_type"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="notes")
    ap.add_argument("--dst", default=embedding.table_name())
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.src == a.dst:
        sys.exit("src and dst are the same table -- refusing (this tool never rewrites in place)")

    db = lancedb.connect(str(DAVID_DATA / "lancedb"))
    src = db.open_table(a.src)
    schema = pa.schema([
        pa.field("id", pa.string()), pa.field("vault", pa.string()), pa.field("file", pa.string()),
        pa.field("chunk_index", pa.int32()), pa.field("text", pa.string()),
        pa.field("date_modified", pa.string()), pa.field("source_path", pa.string()),
        pa.field("asset_type", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), embedding.EMBED_DIM)),
    ])
    try:
        dst = db.open_table(a.dst)
    except Exception:
        dst = db.create_table(a.dst, schema=schema)

    total = src.count_rows()
    print(f"source '{a.src}': {total} rows  ->  '{a.dst}' with {embedding.model_name()}", flush=True)

    rows = src.search().select(COLS).limit(total + 10).to_list()
    n_dst = dst.count_rows()
    done = {r["id"] for r in dst.search().select(["id"]).limit(n_dst + 10).to_list()} if n_dst else set()
    if done:
        print(f"resuming: {len(done)} rows already in '{a.dst}'", flush=True)
    todo = [r for r in rows if r["id"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"to embed: {len(todo)}", flush=True)

    emb = embedding.get_embedder()
    emb.encode("warmup")
    t0 = time.time()
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        vecs = emb.encode([embedding.doc_text(r["file"], r["text"]) for r in chunk], batch_size=a.batch, normalize_embeddings=True)
        dst.add([dict(r, vector=v.tolist()) for r, v in zip(chunk, vecs)])
        n = i + len(chunk)
        if (n // a.batch) % 25 == 0 or n == len(todo):
            rate = n / max(time.time() - t0, 1e-6)
            print(f"  {n}/{len(todo)}  {rate:.0f} rows/s  eta {(len(todo)-n)/max(rate,1e-6)/60:.1f} min", flush=True)

    print(f"destination now has {dst.count_rows()} rows (source {total})", flush=True)
    if not a.limit:
        import rag
        rag._ensure_fts(dst)


if __name__ == "__main__":
    main()
