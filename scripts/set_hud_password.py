"""set_hud_password.py — set (or change) the HUD login password. Stores only an Argon2id hash in <data dir>\\auth\\hud_auth.json.

    py -3.11 scripts\\set_hud_password.py

Asks for the password twice without echoing it (at least 12 characters). Setting a password signs out every open session.
The login is enforced once a password is set and the HUD is started WITHOUT DAVID_AUTH=off.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hud_auth  # noqa: E402


def main():
    print(f"HUD password store: {hud_auth.AUTH_FILE}")
    first = getpass.getpass("New HUD password (at least 12 characters): ")
    if len(first) < 12:
        print("Too short: use at least 12 characters (a few words work well).")
        return 1
    if getpass.getpass("Type it again: ") != first:
        print("The two entries differ; nothing was changed.")
        return 1
    hud_auth.set_password(first)
    print("Saved (Argon2id hash only). Every open HUD session has been signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
