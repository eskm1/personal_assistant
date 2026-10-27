"""
How Instagram has been answering the server (cloud/CONTRACT.md, "Reference downloads: the queue,
the sign-in and uploads").

Every call Studio makes to Instagram for a reference (yt-dlp, gallery-dl, a follower count) leaves
one small line here: when, which tool, which endpoint (the post's media info, graphql, a profile, a
challenge or login page), the HTTP status or what the answer said, and whether it went with the
cookies.txt sign-in. Never a cookie, never the raw answer. The last OUTCOMES_KEPT are kept in
ROOT/Style/ig_outcomes.json.

From them, one rule tells a temporary limit from a dead sign-in: the sign-in "needs attention" when
a signed-in call meets a checkpoint, a challenge, login_required or a redirect to /challenge or
/accounts/login, or when every one of the last SIGNIN_FAILS signed-in calls failed (a 400 on
/api/v1/media/<id>/info/ counts) and they span SIGNIN_HOURS or more. A signed-in success clears it,
and so does a fresh cookies.txt (calls made with the old file stop counting); a signed-out success
says nothing about the sign-in. A deleted post or no internet isn't the sign-in's fault, so neither
counts.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

import settings

log = logging.getLogger("studio.ighealth")

OUTCOMES_KEPT = 40
SIGNIN_FAILS = 8          # this many signed-in calls in a row failed ...
SIGNIN_HOURS = 2.0        # ... over at least this long: the sign-in needs attention
ATTENTION_PAGE = ("The sign-in account needs attention: open Instagram with it on a phone, clear any check, "
                  "then upload a fresh cookies.txt here.")
ATTENTION_ERROR = ("The sign-in account needs attention: open Instagram with it on a phone, clear any check, then "
                   "upload a fresh cookies.txt on the References page (Instagram sign-in for downloads). Or upload "
                   "the file instead.")
_lock = threading.Lock()
_CHALLENGE = re.compile(r"checkpoint|challenge|login_required|/accounts/login|login page", re.I)
# Cookie values must never reach a log or a file: name=value pairs of Instagram's cookies, blanked.
_COOKIE = re.compile(r"\b(sessionid|csrftoken|ds_user_id|mid|ig_did|ig_nrcb|rur|shbid|shbts|datr|wd)=([^;\s'\"]+)",
                     re.I)


def scrub(text: str) -> str:
    """Text with any Instagram cookie value taken out (sessionid=... -> sessionid=…)."""
    return _COOKIE.sub(lambda m: f"{m.group(1)}=…", str(text or ""))


def _file():
    return settings.ROOT / "Style" / "ig_outcomes.json"


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _cookies_mtime() -> float:
    import style_study
    try:
        return style_study.COOKIES_FILE.stat().st_mtime
    except OSError:
        return 0.0


def endpoint_of(message: str, tool: str = "") -> str:
    """Which Instagram endpoint an error names: media_info, graphql, profile, challenge, login_page, or the tool."""
    low = str(message or "").lower()
    if "/challenge" in low or "challenge page" in low or "checkpoint" in low:
        return "challenge"
    if "/accounts/login" in low or "login page" in low:
        return "login_page"
    if re.search(r"/api/v1/media/[^/\s]+/info", low):
        return "media_info"
    if "graphql" in low:
        return "graphql"
    if "/api/v1/users/" in low or "web_profile_info" in low:
        return "profile"
    return tool or "other"


def status_of(message: str) -> int | str | None:
    """The HTTP status in a downloader's error (yt-dlp "HTTP Error 400", gallery-dl "HttpError: '400 ...'"),
    or what the answer said (challenge, login_required), or None."""
    text = str(message or "")
    m = re.search(r"(?:HTTP\s*Error|HttpError:?)\s*'?(\d{3})\b", text, re.I) or re.search(r"\b([45]\d\d) (?:Client|Server) Error",
                                                                                           text)
    if m:
        return int(m.group(1))
    low = text.lower()
    if "login_required" in low:
        return "login_required"
    if _CHALLENGE.search(low):
        return "challenge"
    return None


def record(tool: str, signed_in: bool, problem: str = "ok", message: str = "", now: float | None = None) -> dict:
    """One Instagram call: problem "ok" or style_study.problem_of's kind (login, rate, gone, net, other),
    message the downloader's error (only its status and endpoint are kept). Returns the attention state."""
    now = now or time.time()
    line = {"at": round(now, 1), "tool": tool, "signed_in": bool(signed_in), "ok": problem == "ok",
            "problem": problem, "status": None if problem == "ok" else status_of(message),
            "endpoint": endpoint_of(message, tool) if message else tool}
    if problem != "ok" and signed_in and _CHALLENGE.search(str(message or "")):
        line["challenge"] = True
    with _lock:
        data = _load()
        data["outcomes"] = (list(data.get("outcomes") or []) + [line])[-OUTCOMES_KEPT:]
        data["attention"] = _judge(data, now)
        _write(data)
    if problem != "ok":
        log.info("Instagram %s %s: %s (%s, %s)", tool, "signed in" if signed_in else "signed out", problem,
                 line["status"], line["endpoint"])
    return data["attention"] or {}


def _judge(data: dict, now: float) -> dict | None:
    """The sign-in rule over the record (see the module's docstring)."""
    since = _cookies_mtime()
    if not since:
        return None  # no sign-in file: nothing to need attention (the login hint says to add one)
    mine = [o for o in data.get("outcomes") or [] if o.get("signed_in") and o.get("at", 0) >= since]
    counted = [o for o in mine if o.get("ok") or o.get("problem") not in ("gone", "net")]
    current = data.get("attention")
    if current and current.get("since", 0) < since:
        current = None  # flagged for the old sign-in file
    if counted and counted[-1].get("ok"):
        return None
    if counted and counted[-1].get("challenge"):
        return current or {"since": counted[-1]["at"], "why": "challenge"}
    last = counted[-SIGNIN_FAILS:]
    if len(last) >= SIGNIN_FAILS and not any(o.get("ok") for o in last) \
            and last[-1]["at"] - last[0]["at"] >= SIGNIN_HOURS * 3600:
        return current or {"since": last[-1]["at"], "why": "failing"}
    return current


def attention() -> dict | None:
    """{since, why: challenge | failing} while the sign-in needs attention, else None. A cookies.txt
    uploaded since then clears it."""
    current = _load().get("attention")
    if not current:
        return None
    since = _cookies_mtime()
    if not since or since > current.get("since", 0):
        return None
    return current


def info() -> dict:
    """For the heartbeat (laptop_status.info.references.signin.attention) and the page: never a cookie."""
    a = attention()
    outcomes = _load().get("outcomes") or []
    last_ok = next((o["at"] for o in reversed(outcomes) if o.get("ok") and o.get("signed_in")), None)

    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t else None
    return {"attention": {"since": iso(a["since"]), "why": a.get("why"), "text": ATTENTION_PAGE} if a else None,
            "last_signed_in_ok": iso(last_ok)}
