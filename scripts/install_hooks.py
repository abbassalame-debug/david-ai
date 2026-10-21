"""install_hooks.py — install the pre-commit hook that blocks secrets (and, for a public export, private terms) before a commit.

    py -3.11 scripts/install_hooks.py <repo>                                   gitleaks on the staged changes
    py -3.11 scripts/install_hooks.py <repo> --terms <file> --checker <script>  + private_terms_check.py --staged with that terms file

The hook is written to <repo>/.git/hooks/pre-commit (not versioned, machine-local). It FAILS CLOSED: if gitleaks cannot be
found the commit is refused with a message. An existing hook that this script did not write is kept as pre-commit.bak.
Bypassing it on purpose is `git commit --no-verify`.
"""
import argparse
import os
import shutil
import sys

MARK = "# installed by scripts/install_hooks.py"

HOOK = r"""#!/bin/sh
{mark}
# 1. secrets: gitleaks on the staged changes
GL="$(command -v gitleaks 2>/dev/null)"
if [ -z "$GL" ]; then
  for p in "$LOCALAPPDATA"/Microsoft/WinGet/Links/gitleaks.exe "$LOCALAPPDATA"/Microsoft/WinGet/Packages/Gitleaks.Gitleaks_*/gitleaks.exe; do
    [ -x "$p" ] && GL="$p" && break
  done
fi
if [ -z "$GL" ]; then
  echo "pre-commit: gitleaks not found, commit refused (install it, or commit with --no-verify on purpose)" >&2
  exit 1
fi
"$GL" git --pre-commit --staged --redact --no-banner -v . || {{ echo "pre-commit: gitleaks found a secret in the staged changes" >&2; exit 1; }}
{terms_block}
exit 0
"""

TERMS_BLOCK = r"""# 2. private terms (names, business names, private folders, personal paths) in the staged lines
PY="$(command -v py 2>/dev/null)"; [ -n "$PY" ] && PY="$PY -3.11" || PY="$(command -v python)"
$PY "{checker}" . --staged --terms "{terms}" || {{ echo "pre-commit: private terms in the staged changes (see above)" >&2; exit 1; }}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo")
    ap.add_argument("--terms")
    ap.add_argument("--checker")
    a = ap.parse_args()
    hooks = os.path.join(a.repo, ".git", "hooks")
    if not os.path.isdir(hooks):
        print(f"not a git repository (no {hooks})", file=sys.stderr)
        return 2
    if bool(a.terms) != bool(a.checker):
        print("--terms and --checker go together", file=sys.stderr)
        return 2
    for p in filter(None, (a.terms, a.checker)):
        if not os.path.isfile(p):
            print(f"not found: {p}", file=sys.stderr)
            return 2
    block = TERMS_BLOCK.format(checker=os.path.abspath(a.checker).replace("\\", "/"), terms=os.path.abspath(a.terms).replace("\\", "/")) if a.terms else ""
    target = os.path.join(hooks, "pre-commit")
    if os.path.exists(target) and MARK not in open(target, encoding="utf-8", errors="ignore").read():
        shutil.copy2(target, target + ".bak")
        print(f"kept the previous hook as {target}.bak")
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK.format(mark=MARK, terms_block=block))
    print(f"installed {target}" + (" (gitleaks + private terms)" if a.terms else " (gitleaks)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
