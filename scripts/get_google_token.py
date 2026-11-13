#!/usr/bin/env python3
"""Generate token.json for the PO sheet sync.

Run this once, locally, on a machine with a browser. It opens Google's consent
screen, then writes token.json next to credentials.json. The contents of that
file go into the GOOGLE_TOKEN_JSON secret (GitHub Actions) and .env (local).

    python scripts/get_google_token.py

Needs credentials.json in the repo root: an OAuth client of type "Desktop app"
from Google Cloud -> APIs & Services -> Credentials. Both files are gitignored.

SCOPES is imported from the connector rather than repeated here, so the token
is always granted exactly the scope the sync will later ask for. A token minted
with different scopes fails at refresh time, which is how the previously
available token.json (granted the read/write `spreadsheets` scope) was unusable
for this read-only pipeline.
"""
import json
import pathlib
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from connectors.po_sheet import SCOPES  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLIENT_SECRETS = ROOT / "credentials.json"
TOKEN = ROOT / "token.json"


def main() -> int:
    if not CLIENT_SECRETS.exists():
        print(
            f"{CLIENT_SECRETS} not found.\n"
            "Create an OAuth client of type 'Desktop app' in Google Cloud "
            "(APIs & Services -> Credentials), download the JSON, and save it there.",
            file=sys.stderr,
        )
        return 1

    if TOKEN.exists():
        existing = json.loads(TOKEN.read_text()).get("scopes")
        print(f"{TOKEN} already exists with scopes {existing}; it will be overwritten.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN.write_text(creds.to_json())

    granted = sorted(creds.scopes or [])
    print(f"\nWrote {TOKEN}")
    print(f"  granted scopes : {granted}")
    print(f"  refresh_token  : {'present' if creds.refresh_token else 'MISSING'}")

    if set(granted) != set(SCOPES):
        print(
            f"\nWARNING: granted scopes differ from the requested {SCOPES}. "
            "The sync refreshes with the requested scope and will fail if they "
            "do not match. Revoke this app at "
            "https://myaccount.google.com/permissions and run this again.",
            file=sys.stderr,
        )
        return 1
    if not creds.refresh_token:
        print(
            "\nWARNING: no refresh_token was issued, so the sync cannot run "
            "unattended. Revoke the app at "
            "https://myaccount.google.com/permissions and run this again — "
            "Google only returns a refresh token on first consent.",
            file=sys.stderr,
        )
        return 1

    print(
        "\nNext: copy the file's contents (one line) into\n"
        "  - the GOOGLE_TOKEN_JSON GitHub secret, and\n"
        "  - GOOGLE_TOKEN_JSON in your local .env\n"
        "Do not commit token.json; it is gitignored."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
