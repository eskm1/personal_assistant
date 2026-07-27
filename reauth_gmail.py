"""
One-off Gmail re-authentication helper. Run this on a machine with a browser
(your laptop), then copy the resulting token to the server.

    py -m pip install google-auth-oauthlib
    py reauth_gmail.py

Why this exists as a standalone script rather than the one-liner in
auth/google_oauth.py's error message:

1. It forces prompt="consent". Google only issues a refresh token on the FIRST
   authorization of a client; a repeat run without this returns a token with
   refresh_token=None, which works for ~1 hour and then fails with the same
   invalid_grant error. This script verifies the refresh token is present and
   says so loudly.
2. It imports nothing from the repo, so it needs only google-auth-oauthlib -
   importing auth.google_oauth would pull in config.py and python-dotenv too.

SCOPES below must stay in sync with auth/google_oauth.py: a token minted with
different scopes is rejected when the bot loads it.
"""
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_DIR = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE = os.path.join(_DIR, "auth", "credentials.json")
TOKEN_FILE = os.path.join(_DIR, "auth", "token_gmail.json")


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError:
        print("google-auth-oauthlib is not installed for this interpreter.\n")
        print(f"  Interpreter: {sys.executable}\n")
        print("Install it with the SAME command you use to run python, e.g.")
        print("  py -m pip install google-auth-oauthlib      (Windows)")
        print("  python3 -m pip install google-auth-oauthlib (macOS/Linux)")
        return 1

    if not os.path.exists(CREDS_FILE):
        print(f"Missing OAuth client file: {CREDS_FILE}\n")
        print("Download it from Google Cloud Console -> APIs & Services -> Credentials")
        print("-> your Desktop OAuth client -> Download JSON, and save it to that path.")
        return 1

    print(f"Using OAuth client: {CREDS_FILE}")
    print("A browser window will open for consent.")
    print('If you see "Google hasn\'t verified this app", click Advanced -> Go to app.\n')

    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    if not creds.refresh_token:
        print("\nNo refresh token was issued - NOT saving (the old token still works better).")
        print("Revoke this app's access at https://myaccount.google.com/permissions")
        print("and run this script again to force a fresh grant.")
        return 1

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print("\nrefresh_token present: True")
    print(f"Saved: {TOKEN_FILE}\n")
    print("Now copy it to the server:")
    print(f"  scp {os.path.join('auth', 'token_gmail.json')} "
          "assistant@<server>:/home/assistant/telegram-assistant/auth/")
    print("\nNo restart needed - the token is read on every Gmail call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
