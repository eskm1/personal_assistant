"""
Planner accounts (Supabase Auth), as the Studio machine sees them.

- verify(token): who a planner access token belongs to, and their planner role
  (the upload API, uploads.py). Checked with Supabase and cached for 5 minutes.
- password_login(email, password): the planner's email and password, for the
  dashboard's sign-in page (one login for both).
- planner_session(email): a fresh planner session for someone already signed in
  to the dashboard, so the planner served at /plan/ opens signed in.
- anon_key(): the planner's public key, for /plan/config.js.

Everything goes to STUDIO_SUPABASE_URL only, with the service key headers
cloud_sync uses. Tokens and keys are never logged.
"""
import base64
import hashlib
import json
import logging
import re
import threading
import time

import requests

import cloud_sync
import settings

log = logging.getLogger("studio.cloud")

TIMEOUT = (10, 20)
CACHE_SECONDS = 300
_cache: dict[str, tuple[float, dict | None]] = {}   # sha256(token) -> (until, user or None)
_cache_lock = threading.Lock()
ANON_FILE = "planner_config.json"
_anon: dict = {"key": None, "problem": None, "checked": 0.0}


class AuthError(RuntimeError):
    """status: the HTTP status to answer with; message: plain words."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _headers() -> dict:
    return cloud_sync.auth_headers(settings.SUPABASE_SERVICE_KEY)


def _call(method: str, path: str, **kw) -> requests.Response:
    if not cloud_sync.enabled():
        raise AuthError(503, "Online planning isn't set up on this Studio machine")
    try:
        return requests.request(method, settings.SUPABASE_URL + path, timeout=TIMEOUT, allow_redirects=False,
                                **kw)
    except requests.RequestException as e:
        raise AuthError(502, f"Can't reach the planner's database right now ({type(e).__name__})") from None


def profile(user_id: str) -> dict | None:
    """The planner member's profile (id, name, email, role), or None: not a member."""
    r = _call("GET", "/rest/v1/profiles", headers=_headers(),
              params={"id": f"eq.{user_id}", "select": "id,name,email,role"})
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't read planner profiles (HTTP {r.status_code})")
    rows = r.json()
    return rows[0] if rows else None


def verify(token: str) -> dict:
    """{id, email, name, role} for a planner access token. AuthError 401 for a bad
    or expired session, 403 for someone who isn't a planner member."""
    token = (token or "").strip()
    if not token or len(token) > 4096:
        raise AuthError(401, "Sign in to the planner again")
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            if hit[1] is None:
                raise AuthError(403, "You're not a member of the planner")
            return hit[1]
    r = _call("GET", "/auth/v1/user", headers={**_headers(), "Authorization": f"Bearer {token}"})
    if 400 <= r.status_code < 500:
        raise AuthError(401, "Sign in to the planner again")
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't check your planner session (HTTP {r.status_code})")
    user = r.json()
    prof = profile(user.get("id", ""))
    found = None if prof is None else {"id": prof["id"], "email": (user.get("email") or prof["email"] or "").lower(),
                                       "name": prof["name"], "role": prof["role"]}
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        # Never trust a token past its own expiry, even inside the 5 minutes.
        until = min(now + CACHE_SECONDS, _expiry(token) or now + CACHE_SECONDS)
        _cache[key] = (until, found)
    if found is None:
        raise AuthError(403, "You're not a member of the planner")
    return found


def _expiry(token: str) -> float | None:
    """The exp claim of a JWT access token (not verified: Supabase did that)."""
    try:
        return float((_jwt_payload(token) or {})["exp"])
    except (KeyError, ValueError, TypeError):
        return None


def password_login(email: str, password: str) -> dict | None:
    """{name, role} when the planner accepts this email and password and the person is
    a member; None when it doesn't. AuthError when Supabase can't be asked."""
    r = _call("POST", "/auth/v1/token", params={"grant_type": "password"}, headers=_headers(),
              json={"email": email, "password": password})
    if r.status_code in (400, 401, 403, 422):
        return None
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't check the planner password (HTTP {r.status_code})")
    user = r.json().get("user") or {}
    prof = profile(user.get("id", ""))
    if prof is None:
        return None
    return {"name": prof["name"] or email.split("@")[0], "role": prof["role"]}


def planner_session(email: str, owner: bool = False) -> dict:
    """{access_token, refresh_token, expires_at, expires_in, token_type} of a new planner
    session for this member (an admin magic link, verified straight away: no email is
    sent). AuthError 403 when the email isn't a planner member. The dashboard's owner
    opens the planner's owner account even when the two use different emails."""
    email = (email or "").strip().lower()
    r = _call("GET", "/rest/v1/profiles", headers=_headers(), params={"email": f"eq.{email}", "select": "id"})
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't read planner profiles (HTTP {r.status_code})")
    if not r.json() and owner:
        r = _call("GET", "/rest/v1/profiles", headers=_headers(),
                  params={"role": "eq.owner", "select": "email", "order": "created_at"})
        owners = r.json() if r.status_code == 200 else []
        if not owners:
            raise AuthError(403, "The planner has no owner account yet: run cloud_setup.py on the laptop")
        email = owners[0]["email"].strip().lower()
    elif not r.json():
        raise AuthError(403, "Ask Bryan to invite you to the planner")
    r = _call("POST", "/auth/v1/admin/generate_link", headers=_headers(), json={"type": "magiclink", "email": email})
    if r.status_code == 404 or r.status_code == 422:
        raise AuthError(403, "Ask Bryan to invite you to the planner")
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't open a planner session (HTTP {r.status_code})")
    data = r.json()
    token_hash = data.get("hashed_token") or (data.get("properties") or {}).get("hashed_token")
    if not token_hash:
        raise AuthError(502, "Couldn't open a planner session (no link came back)")
    r = _call("POST", "/auth/v1/verify", headers=_headers(), json={"type": "magiclink", "token_hash": token_hash})
    if r.status_code != 200:
        raise AuthError(502, f"Couldn't open a planner session (HTTP {r.status_code})")
    s = r.json()
    if not s.get("access_token"):
        raise AuthError(502, "Couldn't open a planner session")
    return {k: s.get(k) for k in ("access_token", "refresh_token", "expires_at", "expires_in", "token_type")}


# ── The planner's public key (/plan/config.js) ───────────────────────────────

def _public_key_ok(key: str) -> bool:
    """An anon JWT or a publishable key; never a secret or service_role key."""
    if not key or key.startswith("sb_secret_"):
        return False
    if key.startswith("sb_publishable_"):
        return True
    payload = _jwt_payload(key)
    return payload is not None and payload.get("role") == "anon"


def _jwt_payload(key: str) -> dict | None:
    try:
        part = key.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return data if isinstance(data, dict) else None
    except (IndexError, ValueError):
        return None


def anon_key() -> str | None:
    """STUDIO_SUPABASE_ANON_KEY, or the key in the Netlify planner's public config.js
    (fetched once and kept in ROOT/planner_config.json). None (and anon_problem()
    says why) when neither works."""
    if settings.SUPABASE_ANON_KEY:
        if _public_key_ok(settings.SUPABASE_ANON_KEY):
            return settings.SUPABASE_ANON_KEY
        _set_problem("STUDIO_SUPABASE_ANON_KEY isn't a public (anon or publishable) key")
        return None
    if _anon["key"]:
        return _anon["key"]
    path = settings.ROOT / ANON_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("supabase_url") == settings.SUPABASE_URL and _public_key_ok(saved.get("anon_key", "")):
            _anon["key"] = saved["anon_key"]
            return _anon["key"]
    except (OSError, ValueError):
        pass
    if time.time() - _anon["checked"] < 300:
        return None  # tried a moment ago
    _anon["checked"] = time.time()
    if not settings.PLANNER_URL or not cloud_sync.url_allowed(settings.PLANNER_URL):
        _set_problem("Set STUDIO_SUPABASE_ANON_KEY in .env (the planner's anon or publishable key) "
                     "to open the planner here")
        return None
    try:
        r = requests.get(settings.PLANNER_URL + "/config.js", timeout=TIMEOUT, allow_redirects=False)
        text = r.text if r.status_code == 200 else ""
    except requests.RequestException as e:
        text = ""
        log.debug("Planner config.js: %s", type(e).__name__)
    url = re.search(r'"supabaseUrl"\s*:\s*"([^"]*)"', text)
    key = re.search(r'"supabaseAnonKey"\s*:\s*"([^"]*)"', text)
    if not key or not _public_key_ok(key.group(1)):
        _set_problem(f"Couldn't read the planner's public key from {settings.PLANNER_URL}/config.js: "
                     "set STUDIO_SUPABASE_ANON_KEY in .env")
        return None
    if url and url.group(1).rstrip("/") != settings.SUPABASE_URL:
        _set_problem(f"The planner at {settings.PLANNER_URL} uses another Supabase project: "
                     "set STUDIO_SUPABASE_ANON_KEY in .env")
        return None
    _anon.update(key=key.group(1), problem=None)
    try:
        path.write_text(json.dumps({"supabase_url": settings.SUPABASE_URL, "anon_key": key.group(1)}),
                        encoding="utf-8")
    except OSError:
        pass
    log.info("Planner public key read from %s/config.js", settings.PLANNER_URL)
    return _anon["key"]


def _set_problem(message: str) -> None:
    if _anon["problem"] != message:
        log.warning("Planner on this machine: %s", message)
    _anon["problem"] = message


def anon_problem() -> str | None:
    return None if anon_key() else _anon["problem"]
