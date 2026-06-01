import os
import msal
import requests
from config import MS_CLIENT_ID, MS_TENANT_ID

SCOPES = [
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Tasks.ReadWrite",
]

_DIR = os.path.dirname(__file__)
_CACHE_FILE = os.path.join(_DIR, "token_ms.json")

_cache = msal.SerializableTokenCache()
if os.path.exists(_CACHE_FILE):
    _cache.deserialize(open(_CACHE_FILE).read())

_app: msal.PublicClientApplication | None = None


def _get_app() -> msal.PublicClientApplication:
    global _app
    if _app is None:
        _app = msal.PublicClientApplication(
            client_id=MS_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}",
            token_cache=_cache,
        )
    return _app


def _save_cache() -> None:
    if _cache.has_state_changed:
        with open(_CACHE_FILE, "w") as f:
            f.write(_cache.serialize())


def get_access_token() -> str:
    app = _get_app()
    accounts = app.get_accounts()

    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache()
            return result["access_token"]

    # First-time or expired — device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow initiation failed: {flow}")

    # This prints the URL + code to the terminal for one-time setup
    print("\n" + flow["message"] + "\n")
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    _save_cache()
    return result["access_token"]


# ── Authenticated HTTP helpers ────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


def graph_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def graph_post(path: str, body: dict) -> dict:
    resp = requests.post(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def graph_patch(path: str, body: dict) -> dict:
    resp = requests.patch(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def graph_delete(path: str) -> None:
    resp = requests.delete(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
