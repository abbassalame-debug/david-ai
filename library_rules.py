"""
library_rules.py -- how turbo_ingest treats "library" folders (sample libraries and the like).

A sample-library folder can hold hundreds of thousands of audio/preset files. Embedding one
index entry per file costs hours of GPU time and returns nothing a person could search for, so a
folder marked  "mode": "docs_only"  in ingest_paths.json is indexed as:
  * its manuals and readmes, file by file (real text worth searching), and
  * ONE summary entry for the folder: how many files, how big, which sub-libraries are in it,
    which file types, and a note that audio/sample/preset files are not listed one by one.
Everything else in the folder is counted for the summary but never embedded.
"""

import os
import re
import datetime
from collections import Counter
from pathlib import Path

LIB_DOC_EXTS = {".pdf", ".doc", ".docx", ".rtf", ".odt"}
LIB_TEXT_EXTS = {".txt", ".md", ".html", ".htm", ".nfo"}
# A .txt in a sample library is usually a script or preset list, not documentation, so plain-text
# files only count when their NAME says they are a manual/readme/guide.
LIB_DOC_NAME_RE = re.compile(
    r"read[ _-]?me|manual|guide|instruction|handbook|how[ _-]?to|quick[ _-]?start|documentation", re.I)

# License agreements are not manuals: a Native Instruments library ships its EULA as .rtf in a
# dozen languages. Matched against the file name AND every folder above it.
LIB_EXCLUDE_RE = re.compile(r"licen[cs]e|eula|agreement|legal", re.I)

WORD_BUDGET = 300   # keeps the summary a single chunk (chunking splits above 400 words)
COUNTED_LIBS = 10   # libraries listed with their file counts; the rest are listed by name only
MAX_TYPES = 12


def is_junk_file(path):
    """Files that hold nothing to index and only pollute it: Office lock files (~$name.docx, the
    ~160-byte stubs Word/Excel leave while a file is open) and zero-byte files (placeholders such
    as 'New Microsoft Word Document.docx'). In the 2026-10-04 business-folder run, 15 of these were
    indexed with their error message ('[extraction failed ...]') as the text."""
    if os.path.basename(path).startswith("~$"):
        return True
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return False


def is_library_doc(path):
    """path: a file name, or a path relative to the library root."""
    p = Path(path)
    if any(LIB_EXCLUDE_RE.search(part) for part in p.parts[:-1]) or LIB_EXCLUDE_RE.search(p.stem):
        return False
    ext = p.suffix.lower()
    if ext in LIB_DOC_EXTS:
        return True
    return ext in LIB_TEXT_EXTS and bool(LIB_DOC_NAME_RE.search(p.stem))


class LibraryStats:
    def __init__(self, root):
        self.root = root
        self.ext = Counter()
        self.sub = Counter()
        self.bytes = 0
        self.files = 0
        self.docs = 0

    def add(self, full_path, is_doc):
        self.files += 1
        self.ext[Path(full_path).suffix.lower() or "(none)"] += 1
        try:
            self.bytes += os.path.getsize(full_path)
        except OSError:
            pass
        parts = os.path.relpath(full_path, self.root).split(os.sep)
        # a library is usually two levels down (Kontakt/<Library name>/...), so group by the first two folders
        self.sub["/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(top level)")] += 1
        if is_doc:
            self.docs += 1

    def summary(self):
        name = os.path.basename(self.root.rstrip("/\\")) or self.root
        ranked = self.sub.most_common()
        types = ", ".join(f"{e} {c:,}" for e, c in self.ext.most_common(MAX_TYPES))
        head = (f"Library folder: {name} | path {self.root} | {self.files:,} files, {self.bytes / 1e9:,.0f} GB | "
                f"{len(ranked)} libraries (folders two levels deep). Largest: "
                + ", ".join(f"{n} ({c:,} files)" for n, c in ranked[:COUNTED_LIBS]) + ". Others: ")
        tail = (f". File types: {types}. Only manuals and readmes ({self.docs:,} files) are indexed one by one; "
                "sample, audio, preset and MIDI files are deliberately not listed individually.")
        names, budget = [], WORD_BUDGET - len((head + tail).split())
        for n, _ in ranked[COUNTED_LIBS:]:
            cost = len(n.split()) + 1
            if budget - cost < 0:
                break
            names.append(n)
            budget -= cost
        rest = len(ranked) - COUNTED_LIBS - len(names)
        return head + ", ".join(names) + (f", and {rest} more" if rest > 0 else "") + tail


def scan_files(scan, supported, skip_dirs, is_excluded, docs_only_roots=()):
    """Walk the folders in `scan` and return (files_to_index, library_summaries).

    files_to_index: list of (vault, root, full_path, is_extra)
    library_summaries: list of (vault, root, summary_text), one per docs_only root.
    """
    all_files, stats = [], {}
    for key, val in scan.items():
        if isinstance(val, tuple):
            vault_name, root = val
            is_extra = True
        else:
            vault_name, root = key, val
            is_extra = False
        if not os.path.isdir(root):
            continue
        st = LibraryStats(root) if root in docs_only_roots else None
        if st is not None:
            stats[root] = (vault_name, st)
        for dirpath, dirnames, fnames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs and not d.startswith(".")]
            for fname in fnames:
                full = os.path.join(dirpath, fname)
                if is_excluded(full) or is_junk_file(full):
                    continue                   # never indexed, so never counted in the summary either
                if st is not None:
                    doc = is_library_doc(os.path.relpath(full, root))
                    st.add(full, doc)          # every file is counted for the summary...
                    if not doc:
                        continue               # ...but only manuals/readmes are indexed
                if Path(fname).suffix.lower() not in supported:
                    continue
                all_files.append((vault_name, root, full, is_extra))
    return all_files, [(v, r, s.summary()) for r, (v, s) in stats.items()]


def add_library_entries(table, embedder, entries, chunk_text, doc_text):
    """Write (or refresh) the one summary entry per library folder."""
    for vault_name, root, text in entries:
        name = os.path.basename(root.rstrip("/\\")) or root
        label = f"{name} (library folder)"
        try:
            table.delete("source_path = '{}'".format(root.replace("'", "''")))
        except Exception:
            pass
        chunks = chunk_text(text, min_words=3) or [text]
        vecs = embedder.encode([doc_text(label, c) for c in chunks],
                               normalize_embeddings=True, show_progress_bar=False)
        today = datetime.date.today().isoformat()
        table.add([{
            "id": f"{vault_name}::{root}::library::{i}", "vault": vault_name, "file": label,
            "chunk_index": i, "text": c, "date_modified": today, "source_path": root,
            "asset_type": "library", "vector": v.tolist(),
        } for i, (c, v) in enumerate(zip(chunks, vecs))])
