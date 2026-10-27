"""
Studio's connection to Instagram: "Instagram API with Instagram Login"
(graph.instagram.com; a Business account, no Facebook Page), used only to post
what Bryan approved and scheduled (posting.py) and to read how posts did
(post_results.py).

The owner pastes three things once, under More > Instagram (Review or the
planner's More > Instagram): the access token, the Instagram app ID and the app
secret (Meta's App Dashboard > Instagram > API setup with Instagram login). They are
kept only on this machine, in ROOT/instagram.json (readable by Studio only, like
ig_signin.py's cookies.txt): never online, never logged, never sent back to a browser.

  connect()       checks the token with /me (who it is), swaps a short-lived token for
                  a long-lived one (ig_exchange_token, with the app secret) and saves it.
  maybe_refresh() the long-lived token lasts 60 days; Studio renews it (ig_refresh_token)
                  every ~10 days, well before then, and tells Bryan on Telegram if it can't.
  Graph           the calls posting and results make, with Instagram's errors sorted into
                  token | limit | spec | media | transient | other.

Endpoints (Meta's Instagram Platform docs, checked 2026-10-19):
  GET  /access_token?grant_type=ig_exchange_token&client_secret=..&access_token=..  (unversioned)
  GET  /refresh_access_token?grant_type=ig_refresh_token&access_token=..           (token >= 24 h old)
  GET  /<v>/me?fields=user_id,username,account_type
  GET  /<v>/<ig-user-id>/content_publishing_limit?fields=quota_usage,config
  POST /<v>/<ig-user-id>/media, GET /<v>/<container>?fields=status_code,status, POST /<v>/<ig-user-id>/media_publish
  GET  /<v>/<media>/insights?metric=..., GET /<v>/<ig-user-id>/insights?metric=..&period=day&metric_type=total_value
"""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import requests

import settings

log = logging.getLogger("studio.instagram")

TIMEOUT = (10, 60)
TOKEN_DAYS = 60              # a long-lived token's life
REFRESH_WHEN_DAYS_LEFT = 50  # renew once fewer days than this are left (so about every 10 days)
MIN_AGE_HOURS = 25           # Instagram renews a token only once it is a day old
WARN_DAYS_LEFT = 7           # past this, a failed renewal is urgent
ALERT_EVERY_HOURS = 24       # one Telegram per problem per day, not every minute
ACCOUNT_TYPES = {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}

_lock = threading.RLock()


class _Redact(logging.Filter):
    """urllib3 logs each request's address at DEBUG level, query string included: the token
    and the secret are blanked out of it before anything can write it down."""
    _pattern = re.compile(r"((?:access_token|client_secret)=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "access_token=" in message or "client_secret=" in message:
            record.msg, record.args = self._pattern.sub(r"\1***", message), ()
        return True


logging.getLogger("urllib3.connectionpool").addFilter(_Redact())


class IgError(RuntimeError):
    """An Instagram problem in plain words, sorted by what to do about it:
    token (reconnect), limit (wait), spec (the file or caption is refused), media (Instagram
    couldn't fetch or read the file), transient (try again soon), other, offline (no connection)."""

    def __init__(self, message: str, kind: str = "other", code=None, subcode=None, status: int | None = None):
        super().__init__(message)
        self.kind, self.code, self.subcode, self.status = kind, code, subcode, status

    @property
    def transient(self) -> bool:
        return self.kind in ("transient", "offline")


# Instagram's error codes (the Graph API's shared codes and the publishing subcodes).
_TOKEN_CODES = {102, 190, 463, 467}                  # session or token invalid / expired
_PERMISSION_CODES = {10, 200, 210, 230, 299}         # a permission the token lacks
_RATE_CODES = {4, 17, 32, 341, 613, 80002}           # call limits: wait and try again
_TRANSIENT_CODES = {1, 2, -1, 9007}                  # unknown / service down / media not ready yet
_LIMIT_SUBCODES = {2207042}                          # 100 (or 50) API posts in 24 hours reached
_SPEC_SUBCODES = {2207026, 2207004, 2207005, 2207009, 2207010, 2207023, 2207028, 2207035, 2207036,
                  2207037, 2207040, 2207050, 2207051, 2207054, 2207057, 36000, 36001, 36003, 36004}
_MEDIA_SUBCODES = {2207003, 2207052, 2207020, 2207006}  # couldn't fetch / timed out / expired container
_TRANSIENT_SUBCODES = {2207001, 2207008, 2207027, 2207032, 2207053}


def classify(status: int | None, err: dict) -> IgError:
    """Instagram's error object -> IgError (message from Instagram's own words)."""
    code, sub = err.get("code"), err.get("error_subcode")
    text = (err.get("error_user_msg") or err.get("message") or f"HTTP {status}").strip()
    title = (err.get("error_user_title") or "").strip()
    message = f"{title}: {text}" if title and title not in text else text
    message = _scrub(message)[:400]
    if sub in _LIMIT_SUBCODES:
        kind = "limit"
    elif code in _TOKEN_CODES or sub in (458, 459, 460, 463, 464, 467, 492):
        kind = "token"
    elif code in _PERMISSION_CODES:
        kind = "token"
    elif code in _RATE_CODES:
        kind = "transient"
    elif sub in _SPEC_SUBCODES:
        kind = "spec"
    elif sub in _MEDIA_SUBCODES:
        kind = "media"
    elif sub in _TRANSIENT_SUBCODES or code in _TRANSIENT_CODES or err.get("is_transient") or (status or 0) >= 500:
        kind = "transient"
    else:
        kind = "other"
    return IgError(message, kind, code, sub, status)


# ── The stored connection ─────────────────────────────────────────────────────

def _file():
    return settings.INSTAGRAM_FILE


def load() -> dict | None:
    """The saved connection (with its secrets): only for this module and posting."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def _write(data: dict) -> None:
    """Atomically, readable by this user only."""
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _update(**values) -> dict | None:
    with _lock:
        data = load()
        if data is None:
            return None
        data.update(values)
        _write(data)
        return data


def connected() -> bool:
    return load() is not None


def disconnect() -> None:
    _file().unlink(missing_ok=True)


def _secrets() -> list[str]:
    data = load() or {}
    return [v for v in (data.get("access_token"), data.get("app_secret")) if v]


def _scrub(text: str, extra: tuple = ()) -> str:
    """Never let a token or the secret into a message, a log or the database."""
    out = str(text)
    for secret in (*_secrets(), *extra):
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(stamp) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def status() -> dict:
    """What a browser may see: who's connected, since when, how the renewal goes. Never the
    token or the secret."""
    data = load()
    if not data:
        return {"connected": False}
    expires = _parse(data.get("expires_at"))
    left = (expires - _now()).total_seconds() / 86400 if expires else None
    return {"connected": True, "username": data.get("username"), "user_id": data.get("user_id"),
            "account_type": data.get("account_type"), "app_id": data.get("app_id"),
            "added_at": data.get("added_at"), "refreshed_at": data.get("refreshed_at"),
            "expires_at": data.get("expires_at"), "days_left": None if left is None else round(left, 1),
            "next_refresh": _iso(_refresh_due_at(data)) if expires else None,
            "expired": left is not None and left <= 0,
            "problem": data.get("problem"), "problem_at": data.get("problem_at"),
            "api_version": settings.INSTAGRAM_API_VERSION}


# ── Calls ─────────────────────────────────────────────────────────────────────

def _url(path: str, versioned: bool = True) -> str:
    base = settings.INSTAGRAM_GRAPH_URL
    return f"{base}/{settings.INSTAGRAM_API_VERSION}/{path.lstrip('/')}" if versioned else f"{base}/{path.lstrip('/')}"


def call(method: str, path: str, params: dict | None = None, token: str | None = None,
         versioned: bool = True, timeout=TIMEOUT, extra_secrets: tuple = ()) -> dict:
    """One Graph API call. GET parameters go in the query (as Instagram documents), POST
    parameters in the form body. Raises IgError, whose message never holds a secret."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    if token is None:
        data = load()
        if not data:
            raise IgError("Instagram isn't connected yet. Connect it under More > Instagram.", "token")
        token = data["access_token"]
    params["access_token"] = token
    secrets = (token, *extra_secrets)
    try:
        if method == "GET":
            r = requests.get(_url(path, versioned), params=params, timeout=timeout, allow_redirects=False)
        else:
            r = requests.request(method, _url(path, versioned), data=params, timeout=timeout, allow_redirects=False)
    except requests.Timeout:
        raise IgError("Instagram didn't answer in time", "transient") from None
    except requests.RequestException as e:
        raise IgError(f"Couldn't reach Instagram ({type(e).__name__})", "offline") from None
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            e = classify(r.status_code, err)
        elif isinstance(err, str):  # the token endpoints sometimes answer {"error_type", "code", "error_message"}
            e = classify(r.status_code, {"message": body.get("error_message") or err, "code": body.get("code")})
        else:
            e = IgError(f"Instagram answered HTTP {r.status_code}",
                        "transient" if r.status_code >= 500 or r.status_code == 429 else "other", status=r.status_code)
        e.args = (_scrub(str(e), secrets),)
        raise e
    if not isinstance(body, dict):
        raise IgError("Instagram's answer wasn't readable", "transient", status=r.status_code)
    return body


def _ig_id(data: dict | None = None) -> str:
    data = data or load() or {}
    return str(data.get("user_id") or data.get("id") or "me")


# ── Connecting and keeping the token alive ────────────────────────────────────

def connect(access_token: str, app_id: str, app_secret: str, by: str = "") -> dict:
    """Check and save a new connection (replacing any old one). Raises ValueError with a
    plain reason; the old connection stays until a new one checks out."""
    token, app_id, secret = (access_token or "").strip(), (app_id or "").strip(), (app_secret or "").strip()
    if len(token) < 20 or any(c.isspace() for c in token):
        raise ValueError("That doesn't look like an access token. Copy the whole token from Meta's App Dashboard.")
    if not app_id.isdigit() or not 5 <= len(app_id) <= 25:
        raise ValueError("The Instagram app ID is a number (Meta's App Dashboard > Instagram > API setup).")
    if len(secret) < 16 or any(c.isspace() for c in secret):
        raise ValueError("That doesn't look like an app secret. It's next to the Instagram app ID (Show).")
    expires_in = None
    # A token from Meta's dashboard is usually long-lived already; a short-lived one (1 hour)
    # is swapped for a long-lived one with the app secret.
    try:
        got = call("GET", "access_token", {"grant_type": "ig_exchange_token", "client_secret": secret},
                   token=token, versioned=False, extra_secrets=(secret,))
        if got.get("access_token"):
            token, expires_in = got["access_token"], got.get("expires_in")
    except IgError as e:
        if e.kind == "offline":
            raise ValueError(f"{e}. Try again in a minute.")
        # Already long-lived (or the exchange isn't offered): /me below says whether the token works.
        log.info("Instagram token exchange not used (%s); checking the token as it is", e.kind)
    try:
        me = call("GET", "me", {"fields": "user_id,username,account_type"}, token=token, extra_secrets=(secret,))
    except IgError as e:
        if e.kind == "token":
            raise ValueError(f"Instagram didn't accept that token: {e}")
        raise ValueError(f"Couldn't check the token with Instagram: {e}")
    account_type = str(me.get("account_type") or "").upper()
    if account_type and account_type not in ACCOUNT_TYPES:
        raise ValueError(f"@{me.get('username')} is a {account_type.lower()} account. Instagram only lets Business or "
                         "Creator accounts post through the API (Instagram: Settings > Account type).")
    if expires_in is None:
        # Learn when it runs out by renewing it now (works once the token is a day old).
        try:
            got = call("GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, token=token, versioned=False)
            if got.get("access_token"):
                token, expires_in = got["access_token"], got.get("expires_in")
        except IgError as e:
            log.info("Instagram token not renewed at connect (%s): assuming %d days", e.kind, TOKEN_DAYS)
    now = _now()
    data = {"access_token": token, "app_id": app_id, "app_secret": secret,
            "user_id": str(me.get("user_id") or me.get("id") or ""), "app_user_id": str(me.get("id") or ""),
            "username": me.get("username") or "", "account_type": account_type or None,
            "added_at": _iso(now), "added_by": by, "refreshed_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=int(expires_in))) if expires_in else _iso(now + timedelta(days=TOKEN_DAYS)),
            "expiry_known": bool(expires_in), "problem": None, "problem_at": None, "alerted": {}}
    with _lock:
        _write(data)
    log.info("Instagram connected as @%s", data["username"])
    return status()


def _refresh_due_at(data: dict) -> datetime:
    expires = _parse(data.get("expires_at")) or _now()
    refreshed = _parse(data.get("refreshed_at")) or _now()
    return max(expires - timedelta(days=REFRESH_WHEN_DAYS_LEFT), refreshed + timedelta(hours=MIN_AGE_HOURS))


def maybe_refresh(force: bool = False) -> bool:
    """Renew the long-lived token when it's due (about every 10 days). On failure, the
    problem is kept for the Instagram box and Bryan gets one Telegram a day. Returns True
    when it was renewed. Never raises."""
    with _lock:
        data = load()
        if not data:
            return False
        now = _now()
        if not force and now < _refresh_due_at(data):
            return False
        last_try = _parse(data.get("refresh_tried_at"))
        if not force and last_try and now - last_try < timedelta(hours=1):
            return False  # a failed try waits an hour before the next
        _update(refresh_tried_at=_iso(now))
        try:
            got = call("GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, token=data["access_token"],
                       versioned=False)
            if not got.get("access_token"):
                raise IgError("Instagram didn't send a new token back", "transient")
        except IgError as e:
            expires = _parse(data.get("expires_at"))
            left = max(0, round((expires - now).total_seconds() / 86400)) if expires else 0
            problem = f"Couldn't renew the Instagram connection: {e}"
            _update(problem=problem, problem_at=_iso(now))
            log.warning("Instagram token renewal failed (%s, %d days left)", e.kind, left)
            if left <= 0:
                alert("expired", "Studio: the Instagram connection has run out, so scheduled posts can't go out. "
                      "Paste a new token under More > Instagram.")
            elif e.kind == "token" or left <= WARN_DAYS_LEFT:
                alert("refresh", f"Studio: couldn't renew the Instagram connection ({e}). It runs out in {left} days. "
                      "Paste a new token under More > Instagram.")
            return False
        expires_in = int(got.get("expires_in") or TOKEN_DAYS * 86400)
        _update(access_token=got["access_token"], refreshed_at=_iso(now), expiry_known=True, refresh_tried_at=None,
                expires_at=_iso(now + timedelta(seconds=expires_in)), problem=None, problem_at=None)
        log.info("Instagram connection renewed (good for %d days)", expires_in // 86400)
        return True


def alert(key: str, text: str) -> bool:
    """A Telegram to Bryan, at most once a day per kind of problem."""
    with _lock:
        data = load() or {}
        sent = (data.get("alerted") or {}).get(key)
        when = _parse(sent)
        if when and _now() - when < timedelta(hours=ALERT_EVERY_HOURS):
            return False
        if data:
            _update(alerted={**(data.get("alerted") or {}), key: _iso(_now())})
    import notify
    return notify.send(text)


def note_problem(e: IgError) -> None:
    """A token problem seen while posting or reading results: shown in the Instagram box."""
    if e.kind == "token":
        _update(problem=f"Instagram refused the connection: {e}", problem_at=_iso(_now()))


# ── What posting and results use ──────────────────────────────────────────────

def account() -> dict:
    """/me: who the token is for (used to re-check the connection)."""
    return call("GET", "me", {"fields": "user_id,username,account_type"})


def publishing_limit() -> dict:
    """{used, total, hours}: API posts in the last 24 hours against Instagram's limit."""
    body = call("GET", f"{_ig_id()}/content_publishing_limit", {"fields": "quota_usage,config"})
    row = (body.get("data") or [{}])[0]
    config = row.get("config") or {}
    return {"used": int(row.get("quota_usage") or 0), "total": int(config.get("quota_total") or 100),
            "hours": round(int(config.get("quota_duration") or 86400) / 3600)}


def create_container(params: dict) -> str:
    body = call("POST", f"{_ig_id()}/media", params, timeout=(10, 120))
    if not body.get("id"):
        raise IgError("Instagram didn't return a container", "transient")
    return str(body["id"])


def container_status(container_id: str) -> dict:
    """{status_code: IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED, status: words}."""
    return call("GET", container_id, {"fields": "status_code,status"})


def publish(container_id: str) -> str:
    body = call("POST", f"{_ig_id()}/media_publish", {"creation_id": container_id}, timeout=(10, 120))
    if not body.get("id"):
        raise IgError("Instagram didn't say what it published", "transient")
    return str(body["id"])


MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,shortcode"
# The profile grid also wants each post's picture (media_url; a Reel's cover is thumbnail_url, both links run
# out after a while, so post_results.py keeps a small copy) and whether a Reel shows in the grid
# (is_shared_to_feed, Reels only). Asked with the rest; if Instagram refuses one of them, the plain fields.
GRID_FIELDS = "media_url,thumbnail_url,is_shared_to_feed"
_grid_fields = {"on": True}


def _fields_refused(e: IgError, fields: str) -> bool:
    return e.code == 100 and any(f in str(e) for f in fields.split(","))


def media(media_id: str) -> dict:
    return call("GET", media_id, {"fields": MEDIA_FIELDS})


def recent_media(since: datetime | None = None, limit: int = 200) -> list[dict]:
    """The account's posts, newest first, back to `since` (follows Instagram's paging)."""
    out, after = [], None
    while len(out) < limit:
        fields = f"{MEDIA_FIELDS},{GRID_FIELDS}" if _grid_fields["on"] else MEDIA_FIELDS
        try:
            body = call("GET", f"{_ig_id()}/media", {"fields": fields, "limit": 50, "after": after})
        except IgError as e:
            if _grid_fields["on"] and _fields_refused(e, GRID_FIELDS):
                log.info("Instagram refused the grid's fields (%s): reading posts without their pictures", e)
                _grid_fields["on"] = False
                continue
            raise
        page = body.get("data") or []
        for m in page:
            t = _parse(m.get("timestamp"))
            if since and t and t < since:
                return out
            out.append(m)
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
        if not page or not after or not (body.get("paging") or {}).get("next"):
            return out
    return out


# The account as its profile shows it (the planner's grid header). Instagram API with Instagram Login
# documents these on the IG user; biography isn't on every account type's list, so a field Instagram
# refuses is dropped and the rest asked again.
PROFILE_FIELDS = ["user_id", "username", "name", "biography", "profile_picture_url", "followers_count",
                  "follows_count", "media_count"]


def profile() -> dict:
    wanted = list(PROFILE_FIELDS)
    while True:
        try:
            return call("GET", "me", {"fields": ",".join(wanted)})
        except IgError as e:
            bad = [f for f in wanted if f in str(e) and f not in ("user_id", "username")]
            if e.code == 100 and bad:
                wanted = [f for f in wanted if f not in bad]
                continue
            raise


def media_insights(media_id: str, metrics: list[str]) -> dict[str, float]:
    """{metric: value} for one post. A metric Instagram doesn't offer for this post is left
    out (asked again without it), so one retired name can't lose the rest."""
    wanted = list(metrics)
    while wanted:
        try:
            body = call("GET", f"{media_id}/insights", {"metric": ",".join(wanted)})
        except IgError as e:
            bad = [m for m in wanted if m in str(e)]
            if e.code == 100 and bad:
                wanted = [m for m in wanted if m not in bad]
                continue
            raise
        out = {}
        for row in body.get("data") or []:
            value = row.get("total_value", {}).get("value") if isinstance(row.get("total_value"), dict) else None
            if value is None:
                values = row.get("values") or []
                value = values[-1].get("value") if values and isinstance(values[-1], dict) else None
            if isinstance(value, (int, float)):
                out[row.get("name")] = value
        return out
    return {}


def account_insights(day_start: datetime, day_end: datetime, metrics: list[str]) -> dict[str, float]:
    """The account's totals for one day (metric_type=total_value)."""
    wanted = list(metrics)
    while wanted:
        try:
            body = call("GET", f"{_ig_id()}/insights", {"metric": ",".join(wanted), "period": "day",
                                                        "metric_type": "total_value",
                                                        "since": int(day_start.timestamp()), "until": int(day_end.timestamp())})
        except IgError as e:
            bad = [m for m in wanted if m in str(e)]
            if e.code == 100 and bad:
                wanted = [m for m in wanted if m not in bad]
                continue
            raise
        out = {}
        for row in body.get("data") or []:
            tv = row.get("total_value")
            value = tv.get("value") if isinstance(tv, dict) else None
            if value is None:
                values = row.get("values") or []
                value = sum(v.get("value") or 0 for v in values if isinstance(v, dict)) if values else None
            if isinstance(value, (int, float)):
                out[row.get("name")] = value
        return out
    return {}


def followers() -> int | None:
    body = call("GET", _ig_id(), {"fields": "followers_count,media_count"})
    n = body.get("followers_count")
    return int(n) if isinstance(n, (int, float)) else None
