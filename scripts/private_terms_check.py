"""private_terms_check.py — find private terms (names, business names, private folders, personal paths) in a source tree.

The terms live in a separate file that is kept only in the private repository (default: <repo>/config/private_terms.txt) and is
git-ignored from any public export. This script holds no terms itself, so it can ship publicly.

Terms file format, one term per line; matching is case-insensitive and Unicode-normalised (NFKC), a plain substring match:
    # comment
    [category]          start a category (names, business, folders, paths, ...)
    term                one term
    re:pattern          a regular expression instead of a substring
    allow:text          an allowed occurrence: removed from each line before matching (e.g. the public repository's own URL)

Usage:
    py -3.11 scripts/private_terms_check.py <target-dir> [--terms FILE] [--tracked] [--history] [--show-line] [--max N]

    --tracked   only files tracked by git in <target-dir> (plus untracked files that are not ignored); default walks the tree
    --history   also scan every line ever added in the target's git history (all branches)
    --staged    only the staged changes (added lines and file names); used by the pre-commit hook
    --show-line print the matching line (off by default so the output can be pasted without repeating private text)

Exit code: 0 clean, 1 private terms found, 2 usage or read error.
"""
import argparse
import os
import re
import subprocess
import sys
import unicodedata

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dll", ".pyd", ".db", ".sqlite",
              ".lance", ".parquet", ".p12", ".zxp", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".flac", ".mp4", ".mov", ".psd",
              ".ai", ".indd", ".xlsx", ".xls", ".docx", ".pptx", ".npy", ".pt", ".bin", ".safetensors", ".gguf"}
MAX_BYTES = 5 * 1024 * 1024


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


ALLOWED = []


def load_terms(path):
    terms, cat = [], "uncategorised"
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                cat = line[1:-1].strip() or "uncategorised"
                continue
            if line.startswith("allow:"):
                ALLOWED.append(norm(line[6:].strip()))
            elif line.startswith("re:"):
                terms.append((cat, line, re.compile(line[3:], re.IGNORECASE)))
            else:
                terms.append((cat, line, norm(line)))
    return terms


def match(terms, text):
    n = norm(text)
    for a in ALLOWED:
        n = n.replace(a, " ")
    for cat, label, t in terms:
        if (t.search(n) if isinstance(t, re.Pattern) else t in n):
            yield cat, label


def tree_files(root, tracked):
    if tracked:
        out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             capture_output=True, check=True).stdout.decode("utf-8", "replace")
        for rel in filter(None, out.split("\0")):
            yield os.path.join(root, rel)
        return
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            yield os.path.join(dirpath, name)


def scan_tree(root, terms, tracked, show_line, hits):
    for path in tree_files(root, tracked):
        if os.path.splitext(path)[1].lower() in BINARY_EXT:
            continue
        rel = os.path.relpath(path, root)
        for cat, label in match(terms, rel):                      # a private term in the file NAME counts too
            hits.append((cat, label, f"{rel} (file name)", ""))
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    for cat, label in match(terms, line):
                        hits.append((cat, label, f"{rel}:{i}", line.strip()[:160] if show_line else ""))
        except OSError:
            continue


def scan_history(root, terms, show_line, hits):
    proc = subprocess.run(["git", "-C", root, "log", "--all", "-p", "--no-color", "--no-ext-diff", "--format=@@commit %h"],
                          capture_output=True)
    commit, path = "?", "?"
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("@@commit "):
            commit = line.split()[1]
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            for cat, label in match(terms, line[1:]):
                hits.append((cat, label, f"{commit} {path}", line[1:].strip()[:160] if show_line else ""))


def scan_staged(root, terms, show_line, hits):
    """Only what is about to be committed: added lines and file names in `git diff --cached` (the pre-commit hook mode)."""
    out = subprocess.run(["git", "-C", root, "diff", "--cached", "--no-color", "--no-ext-diff", "-U0"], capture_output=True, check=True)
    path = "?"
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            for cat, label in match(terms, path):
                hits.append((cat, label, f"{path} (file name, staged)", ""))
        elif line.startswith("+") and not line.startswith("+++"):
            for cat, label in match(terms, line[1:]):
                hits.append((cat, label, f"{path} (staged)", line[1:].strip()[:160] if show_line else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target")
    ap.add_argument("--terms", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "private_terms.txt"))
    ap.add_argument("--tracked", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--staged", action="store_true", help="only the staged changes (for a pre-commit hook)")
    ap.add_argument("--show-line", action="store_true")
    ap.add_argument("--max", type=int, default=200, help="print at most this many hits (all are counted)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.path.isfile(a.terms):
        print(f"terms file not found: {a.terms}", file=sys.stderr)
        return 2
    terms = load_terms(a.terms)
    if not terms:
        print("terms file has no terms", file=sys.stderr)
        return 2
    hits = []
    try:
        if a.staged:
            scan_staged(a.target, terms, a.show_line, hits)
        else:
            scan_tree(a.target, terms, a.tracked, a.show_line, hits)
        if a.history:
            scan_history(a.target, terms, a.show_line, hits)
    except subprocess.CalledProcessError as e:
        print(f"git failed: {e}", file=sys.stderr)
        return 2
    for cat, label, where, text in hits[:a.max]:
        print(f"{cat:12s} {label:30s} {where}" + (f"  | {text}" if text else ""))
    by_cat = {}
    for cat, label, _, _ in hits:
        by_cat.setdefault(cat, set()).add(label)
    print(f"\n{len(hits)} hit(s) for {len(terms)} term(s) in {a.target}" + (" (tree + history)" if a.history else ""))
    for cat, labels in sorted(by_cat.items()):
        print(f"  {cat}: {', '.join(sorted(labels))}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
