import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import HEADLESS

# One Google token covers every Google API the bot uses, so this is the UNION of
# what each tool module needs. Google never adds scopes to an already-issued
# token, so appending to this list invalidates the stored one — it must be
# re-consented (see _missing_scope_help), and reauth_gmail.py keeps a copy of
# this list that has to be updated in the same commit.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    # Read AND write calendar events. Not calendar.readonly alone: Ava also
    # creates events, e.g. blocking out a flight she found in an e-ticket.
    "https://www.googleapis.com/auth/calendar.events",
    # calendar.events covers events but NOT the calendar list itself, so without
    # this "which calendars do I have" is a 403 and only 'primary' is reachable.
    "https://www.googleapis.com/auth/calendar.readonly",
]

_DIR = os.path.dirname(__file__)
CREDS_FILE = os.path.join(_DIR, "credentials.json")
# Named token_gmail.json from when Gmail was the only Google integration. It now
# holds one token for all the scopes above; renaming it would orphan the file
# already sitting on the server, so the name stays.
TOKEN_FILE = os.path.join(_DIR, "token_gmail.json")

# How to mint a fresh token. Reached by the model through the Gmail/Calendar
# tools' error strings, so it has to say what to DO — given only "invalid_grant"
# the model invents recovery steps for a settings screen that doesn't exist.
# Points at reauth_gmail.py rather than a bare consent flow: that script forces
# prompt="consent" and verifies a refresh token was actually issued.
_REAUTH_STEPS = (
    "On a machine with a browser, from the repo root run:\n"
    "  py -m pip install google-auth-oauthlib   (macOS/Linux: python3 -m pip ...)\n"
    "  py reauth_gmail.py\n"
    "then copy the token to the server:\n"
    "  scp auth/token_gmail.json assistant@<server>:/home/assistant/telegram-assistant/auth/\n"
    "No restart needed. If it reports no refresh token was issued, revoke the app at "
    "https://myaccount.google.com/permissions and run it again."
)

# Shown whenever the stored token can no longer be used.
_REAUTH_HELP = (
    "Google access needs re-authentication - the stored token was expired or revoked. "
    + _REAUTH_STEPS
)


def _missing_scope_help(missing: list[str]) -> str:
    """Message for a token that predates a scope the code now needs.

    Checked up front because the alternative is a refresh that either succeeds
    with too little access (then fails deep inside an API call with a bare 403
    insufficientPermissions) or dies with google-auth's "Scope has changed"
    — neither of which tells anyone that a re-consent is the fix.
    """
    return (
        "Google needs to be re-authorised: the stored token was issued before Ava asked for "
        + ", ".join(s.rsplit("/", 1)[-1] for s in missing)
        + ". Google cannot add permissions to an existing token, so a one-off re-consent is "
        "required (this is expected the first time Calendar access is deployed).\n"
        + _REAUTH_STEPS
    )


def _credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        # No scopes= override here: that would just echo back what we asked for.
        # Loading the scopes Google actually GRANTED is what lets the check below
        # distinguish "token predates a new scope" from "token expired".
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)

        missing = [s for s in SCOPES if not creds.has_scopes([s])]
        if missing:
            raise RuntimeError(_missing_scope_help(missing))

        if creds.valid:
            return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # invalid_grant: the refresh token is dead (revoked, password change,
            # or Google's 7-day expiry while the OAuth app is in Testing).
            # Deliberately does NOT fall through to the token write below: the
            # existing file is no worse than what we'd replace it with.
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

    return creds


def get_gmail_service():
    return build("gmail", "v1", credentials=_credentials())


def get_calendar_service():
    return build("calendar", "v3", credentials=_credentials())
