# google_auth_setup.py — one-time interactive OAuth consent flow
# Run this whenever google_token.json is missing or its refresh token has
# expired (google_client.py only knows how to refresh an existing token, not
# create one from scratch).
#
# Run: py -3.11 google_auth_setup.py
#
# This opens your default browser to Google's consent screen. Log in and
# approve access, and the browser tab will redirect back to a local server
# this script starts temporarily (http://localhost) to capture the result.
# On success, a fresh google_token.json is written and this script exits.

from david_config import DAVID_HOME
import sys
import os

sys.path.insert(0, str(DAVID_HOME))
from google_client import CREDS_FILE, TOKEN_FILE, SCOPES


def main():
    if not os.path.exists(CREDS_FILE):
        print(f"  Missing {CREDS_FILE} — download an OAuth Desktop app "
              "client from Google Cloud Console and save it there first.")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("  Opening your browser for Google consent (Calendar + Gmail read)...")
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"  Success — wrote a fresh token to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
