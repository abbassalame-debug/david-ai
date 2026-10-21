"""
reclaim_lance_versions.py -- delete the old versions of a LanceDB table and give the disk space back.

Every write to a LanceDB table leaves the previous version's manifest behind. The legacy `notes` table had
77,000 of them: 135 GB for 0.4 GB of data. This keeps only the newest versions (rows, indexes and search are
unchanged); older versions can no longer be opened ("time travel" is lost).

Why two steps: table.optimize(cleanup_older_than=timedelta(0)) on 77,000 versions makes LanceDB read all of
their 1.7 MB manifests at once and it died with "memory allocation failed" (2026-10-05, 31 GB machine). So:
  1. delete the oldest manifest files in batches, keeping the newest KEEP (a manifest is an independent file; the
     newest one alone is a complete table), re-opening the table after every batch;
  2. then run LanceDB's own optimize(cleanup_older_than=0) on what is left, in a separate process so a crash
     cannot take this script (or the table) down.

Safety: refuses to run unless a Parquet backup of the same table, written in the last 24 hours, exists in
<DAVID_HOME>\\lancedb_backups\\ with the same number of rows; checks row count + a search before, between and after.
Close the HUD / anything else that has the table open first.

Usage:  py -3.11 scripts\\reclaim_lance_versions.py [table] [--keep N]      (default table: notes, keep 200)
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from david_config import DAVID_DATA, DAVID_HOME
import sys, os, time, glob, shutil, subprocess
from datetime import timedelta
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

LANCE = str(DAVID_DATA / "lancedb")
BACKUPS = str(DAVID_DATA / "lancedb_backups")
BATCH = 5000


def free_gb():
    return shutil.disk_usage(os.path.splitdrive(LANCE)[0] + "\\").free / 1024 ** 3


def recent_backup_rows(name):
    import pyarrow.parquet as pq
    cutoff = time.time() - 24 * 3600
    for p in sorted(glob.glob(os.path.join(BACKUPS, f"{name}_2*.parquet")), reverse=True):
        if os.path.getmtime(p) >= cutoff:
            return p, pq.ParquetFile(p).metadata.num_rows
    return None, 0


def check(name, expect_rows):
    """Open the table fresh; rows unchanged and a search returns a full-size vector."""
    import lancedb
    t = lancedb.connect(LANCE).open_table(name)
    hit = t.search().limit(1).to_list()
    return t.count_rows() == expect_rows and bool(hit) and len(hit[0]["vector"]) == 1024


def prune_manifests(name, keep, rows, log=print):
    vdir = os.path.join(LANCE, name + ".lance", "_versions")
    manifests = [f for f in os.listdir(vdir) if f.endswith(".manifest")]
    manifests.sort(key=lambda f: int(f.split(".")[0]))     # V2 names count down: smallest number = newest version
    doomed = manifests[keep:]
    log(f"{len(manifests):,} manifests; keeping the newest {min(keep, len(manifests)):,}, removing {len(doomed):,}")
    for i in range(0, len(doomed), BATCH):
        for f in doomed[i:i + BATCH]:
            os.remove(os.path.join(vdir, f))
        if not check(name, rows):
            raise RuntimeError(f"table '{name}' failed the open/count/search check after removing batch {i // BATCH + 1}; stopping")
        log(f"  removed {min(i + BATCH, len(doomed)):,}/{len(doomed):,}  (table still opens, {rows:,} rows)  disk free {free_gb():.2f} GB")
    return len(doomed)


def main(name, keep):
    import lancedb
    rows = lancedb.connect(LANCE).open_table(name).count_rows()
    backup, brows = recent_backup_rows(name)
    if not backup or brows != rows:
        print(f"REFUSING: no Parquet backup of '{name}' from the last 24 h with {rows:,} rows "
              f"(found {backup} with {brows:,}). Run: py -3.11 {DAVID_HOME}\\rag.py backup {name}")
        return 2
    print(f"backup found: {backup} ({brows:,} rows, matches the table)")
    free0 = free_gb()
    print(f"before: {rows:,} rows | disk free {free0:.2f} GB", flush=True)
    if not check(name, rows):
        print("table fails the open/search check before any change; stopping")
        return 1

    prune_manifests(name, keep, rows)

    print("running LanceDB optimize(cleanup_older_than=0) on the remainder (separate process) ...", flush=True)
    code = ("import lancedb; from datetime import timedelta; "
            f"t=lancedb.connect(r'{LANCE}').open_table('{name}'); print(t.optimize(cleanup_older_than=timedelta(seconds=0)))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print("optimize exit code", r.returncode, "|", (r.stdout or r.stderr).strip()[-400:], flush=True)

    ok = check(name, rows)
    vdir = os.path.join(LANCE, name + ".lance", "_versions")
    print(f"after:  {rows:,} rows | {len(os.listdir(vdir)):,} files in _versions | disk free {free_gb():.2f} GB "
          f"(+{free_gb() - free0:.2f} GB) | opens, same rows, searchable: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 200
    args = [a for a in args if a != str(keep)]
    sys.exit(main(args[0] if args else "notes", keep))
