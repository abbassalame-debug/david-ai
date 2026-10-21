"""test_private_terms_check.py — scripts/private_terms_check.py and scripts/install_hooks.py (2026-10-21, workstream 5A).

Only made-up terms (Zorblax, Quintavelle, ...) are used, so this file can ship in a public export. Everything happens in
temporary folders and throwaway git repositories.
"""
import os, shutil, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "scripts", "private_terms_check.py")
INSTALL = os.path.join(HERE, "scripts", "install_hooks.py")

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" — {detail}" if detail else ""))

def run(*args, cwd=None):
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    return p.returncode, p.stdout + p.stderr

def git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")

tmp = tempfile.mkdtemp(prefix="ptc_")
try:
    terms = os.path.join(tmp, "terms.txt")
    open(terms, "w", encoding="utf-8").write(
        "# made-up terms only\n[allowed]\nallow:github.com/zorblax-dev/public-repo\n"
        "[names]\nZorblax\nكوينتافيل\n[business]\nQuintavelle Bakery\nre:\\bfrobn[io]tz\\b\n[paths]\nC:\\Users\\\n")
    tree = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(tree, "sub"))
    os.makedirs(os.path.join(tree, "node_modules"))
    open(os.path.join(tree, "clean.md"), "w", encoding="utf-8").write("nothing private here\n")
    open(os.path.join(tree, "node_modules", "skip.js"), "w", encoding="utf-8").write("Zorblax\n")

    print("\n── 1. clean tree, missing or empty terms ──")
    rc, out = run(CHECK, tree, "--terms", terms)
    check("a clean tree exits 0 (anything under node_modules is skipped)", rc == 0, out[-120:])
    rc, out = run(CHECK, tree, "--terms", os.path.join(tmp, "nope.txt"))
    check("a missing terms file exits 2", rc == 2)
    empty = os.path.join(tmp, "empty.txt"); open(empty, "w").write("# nothing\n")
    rc, out = run(CHECK, tree, "--terms", empty)
    check("a terms file without terms exits 2", rc == 2)

    print("\n── 2. matching ──")
    open(os.path.join(tree, "sub", "a.py"), "w", encoding="utf-8").write("x = 1\nowner = 'ZORBLAX'\n")                   # case
    open(os.path.join(tree, "b.md"), "w", encoding="utf-8").write("the ｑｕｉｎｔａｖｅｌｌｅ　ｂａｋｅｒｙ menu\n")       # full-width, NFKC
    open(os.path.join(tree, "c.txt"), "w", encoding="utf-8").write("نص عن كوينتافيل هنا\n")                                  # Arabic
    open(os.path.join(tree, "d.txt"), "w", encoding="utf-8").write("clone github.com/zorblax-dev/public-repo here\n")         # allowed
    open(os.path.join(tree, "e.txt"), "w", encoding="utf-8").write("call frobnitz now\npath C:\\Users\\me\\x\n")              # regex, path
    open(os.path.join(tree, "Zorblax_notes.md"), "w", encoding="utf-8").write("ok\n")                                         # file name
    rc, out = run(CHECK, tree, "--terms", terms)
    check("hits exit 1", rc == 1)
    check("case-insensitive: ZORBLAX found at sub/a.py:2", os.path.join("sub", "a.py") + ":2" in out, out[:300])
    check("NFKC: a full-width spelling of the business name is found", "b.md:1" in out)
    check("Arabic terms are found", "c.txt:1" in out)
    check("an allowed occurrence (the public repo URL) is not reported", "d.txt" not in out)
    check("re: terms work", "frobn[io]tz" in out and "e.txt:1" in out)
    check("a personal path is found", "e.txt:2" in out)
    check("a private term in a FILE NAME is found", "Zorblax_notes.md (file name)" in out)
    check("the matching line is NOT printed by default", "call frobnitz now" not in out and "'ZORBLAX'" not in out)
    rc, out = run(CHECK, tree, "--terms", terms, "--show-line")
    check("--show-line prints it", "call frobnitz now" in out)
    check("the summary lists hits by category", "names:" in out and "business:" in out and "paths:" in out)

    print("\n── 3. git modes: --tracked, --staged, --history ──")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    open(os.path.join(repo, ".gitignore"), "w").write("ignored.txt\n")
    open(os.path.join(repo, "ignored.txt"), "w").write("Zorblax\n")
    open(os.path.join(repo, "kept.md"), "w").write("clean\n")
    git(repo, "add", ".gitignore", "kept.md"); git(repo, "commit", "-q", "-m", "c1")
    rc, out = run(CHECK, repo, "--terms", terms, "--tracked")
    check("--tracked skips git-ignored files", rc == 0, out[-150:])
    open(os.path.join(repo, "kept.md"), "w").write("clean\nnow with Quintavelle Bakery\n")
    git(repo, "commit", "-q", "-am", "c2")
    open(os.path.join(repo, "kept.md"), "w").write("clean\n")
    git(repo, "commit", "-q", "-am", "c3 removes it again")
    rc, out = run(CHECK, repo, "--terms", terms, "--tracked")
    check("after removal the tree is clean", rc == 0)
    rc, out = run(CHECK, repo, "--terms", terms, "--tracked", "--history")
    check("--history still finds the term in the commit that added it", rc == 1 and "kept.md" in out)
    open(os.path.join(repo, "new.md"), "w").write("Zorblax was here\n")
    git(repo, "add", "new.md")
    rc, out = run(CHECK, repo, "--terms", terms, "--staged")
    check("--staged finds a term in a staged line", rc == 1 and "new.md (staged)" in out, out[:200])
    git(repo, "reset", "-q", "HEAD", "new.md")
    rc, out = run(CHECK, repo, "--terms", terms, "--staged")
    check("--staged ignores changes that are not staged", rc == 0)

    print("\n── 4. install_hooks.py ──")
    hook = os.path.join(repo, ".git", "hooks", "pre-commit")
    open(hook, "w").write("#!/bin/sh\necho old hook\n")
    rc, out = run(INSTALL, repo)
    body = open(hook, encoding="utf-8").read()
    check("the hook is installed and runs gitleaks on the staged changes", rc == 0 and "--pre-commit --staged" in body and "gitleaks" in body)
    check("it fails closed when gitleaks is missing", "commit refused" in body and "exit 1" in body)
    check("an existing foreign hook is kept as pre-commit.bak", os.path.exists(hook + ".bak") and "old hook" in open(hook + ".bak").read())
    check("without --terms there is no private-terms step", "--staged --terms" not in body)
    rc, out = run(INSTALL, repo, "--terms", terms, "--checker", CHECK)
    body = open(hook, encoding="utf-8").read()
    check("with --terms the private-terms check runs on the staged changes", rc == 0 and "--staged --terms" in body)
    check("re-installing over our own hook does not overwrite the .bak", "old hook" in open(hook + ".bak").read())
    rc, out = run(INSTALL, repo, "--terms", terms)
    check("--terms without --checker is refused", rc == 2)
    rc, out = run(INSTALL, os.path.join(tmp, "not_a_repo"))
    check("a folder that is not a git repository is refused", rc == 2)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
