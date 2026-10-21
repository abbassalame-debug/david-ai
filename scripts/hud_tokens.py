"""hud_tokens.py — scoped bearer tokens for programs (not people) that call the HUD. Only a SHA-256 of each token is stored.

    py -3.11 scripts\\hud_tokens.py create <name> "<METHOD> <path>" ["<METHOD> <path/*>" ...] [--save]
    py -3.11 scripts\\hud_tokens.py list
    py -3.11 scripts\\hud_tokens.py revoke <name>

A scope is "GET /api/restaurant/job" or "GET /api/design/*" (prefix); METHOD may be "*". `create` prints the token once.
--save also writes it to <data dir>\\auth\\callers\\<name>.token (git-ignored) for a local program to read. The HUD's calls to
itself do not need a stored token: they use a per-process in-memory token (hud_auth.INTERNAL_TOKEN).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hud_auth  # noqa: E402

CALLERS_DIR = os.path.join(hud_auth.AUTH_DIR, "callers")


def caller_token_path(name: str) -> str:
    return os.path.join(CALLERS_DIR, f"{name}.token")


def main(argv):
    if not argv or argv[0] not in ("create", "list", "revoke"):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "list":
        toks = hud_auth.list_tokens()
        if not toks:
            print("no tokens")
        for name, t in sorted(toks.items()):
            print(f"{name:20s} created {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created']))}  scopes: {', '.join(t['scopes'])}")
        return 0
    if cmd == "revoke":
        if len(argv) != 2:
            print("usage: hud_tokens.py revoke <name>")
            return 2
        ok = hud_auth.revoke_token(argv[1])
        p = caller_token_path(argv[1])
        if os.path.exists(p):
            os.remove(p)
        print("revoked" if ok else "no such token")
        return 0 if ok else 1
    save = "--save" in argv
    args = [a for a in argv[1:] if a != "--save"]
    if len(args) < 2:
        print('usage: hud_tokens.py create <name> "<METHOD> <path>" [...] [--save]')
        return 2
    try:
        token = hud_auth.create_token(args[0], args[1:])
    except ValueError as e:
        print(e)
        return 2
    if save:
        os.makedirs(CALLERS_DIR, exist_ok=True)
        with open(caller_token_path(args[0]), "w", encoding="utf-8") as f:
            f.write(token)
        print(f"token saved for local callers: {caller_token_path(args[0])}")
    else:
        print(f"token (shown once): {token}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
