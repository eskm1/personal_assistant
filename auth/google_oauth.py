import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import HEADLESS

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_DIR = os.path.dirname(__file__)
CREDS_FILE = os.path.join(_DIR, "credentials.json")
TOKEN_FILE = os.path.join(_DIR, "token_gmail.json")

# Shown whenever the stored token can no longer be used. This text reaches the
# model through the Gmail tools' error strings, so it has to say what to DO —
# given only "invalid_grant" the model invents recovery steps for a settings
# screen that doesn't exist. Points at reauth_gmail.py rather than a bare
# consent flow: that script forces prompt="consent" and verifies a refresh
# token was actually issued.
_REAUTH_HELP = (
    "Gmail needs re-authentication - the stored token was expired or revoked. "
    "On a machine with a browser, from the repo root run:\n"
    "  py -m pip install google-auth-oauthlib   (macOS/Linux: python3 -m pip ...)\n"
    "  py reauth_gmail.py\n"
    "then copy the token to the server:\n"
    "  scp auth/token_gmail.json assistant@<server>:/home/assistant/telegram-assistant/auth/\n"
    "No restart needed. If it reports no refresh token was issued, revoke the app at "
    "https://myaccount.google.com/permissions and run it again."
)


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                # invalid_grant: the refresh token is dead (revoked, password
                # change, or Google's 7-day expiry while the OAuth app is in
                # Testing). Re-raise with the recovery steps — the raw error
                # says only "invalid_grant", which is useless to the caller.
                # Deliberately does NOT fall through to the token write below:
                # the existing file is no worse than what we'd replace it with.
                raise RuntimeError(f"{_REAUTH_HELP}\n\n(Google rejected the stored token: {e})") from e
        else:
            # A fresh browser consent is required. The headless server has no
            # browser, so fail loudly with instructions instead of hanging.
            if HEADLESS:
                raise RuntimeError(_REAUTH_HELP)
            if not os.path.exists(CREDS_FILE):
                raise FileNotFoundError(
                    f"Missing {CREDS_FILE} — download OAuth credentials from Google Cloud Console "
                    "(APIs & Services → Credentials → Desktop app) and place the file there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            # prompt="consent" is required: Google only issues a refresh token on
            # the FIRST authorization of a client, so a repeat run without it
            # yields refresh_token=None — which works for ~1 hour and then fails
            # with the very invalid_grant this flow exists to recover from.
            creds = flow.run_local_server(port=0, prompt="consent")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)
