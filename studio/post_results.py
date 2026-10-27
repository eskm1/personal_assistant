"""
How posts did on Instagram (the Studio Director plan, phase 1: the data phases 3 and 4 learn
from). Read with the same connection as posting (instagram.py):

  - once a day (STUDIO_RESULTS_HOUR, Singapore) every post of the last 90 days, including posts
    made from the phone, and the account's day (followers, reach, views, interactions);
  - about an hour after Studio posts something, that post again (its first hour).

Each read is kept per post per day (post_results), so a post's numbers can be followed as
they grow; the account in account_results. The daily read also keeps a small still of each post
(IGThumbs/<media id>.jpg, from Instagram's media_url / a Reel's thumbnail_url, whose links run out),
whether a Reel shows in the profile grid (is_shared_to_feed), and the account as its profile shows it
(name, handle, bio, counts, picture: profile_info()), for the planner's profile grid. The planner gets the latest numbers of each post
and the account's days (Supabase post_results / account_results, mirror()). summary() groups
them by kind of post, format and month for Review and the API.

Metric names (Meta's Instagram Media Insights, Instagram API with Instagram Login):
  Reels:  views, reach, likes, comments, saved, shares, total_interactions,
          ig_reels_avg_watch_time, ig_reels_video_view_total_time (milliseconds)
  Feed (carousels, photos): views, reach, likes, comments, saved, shares, total_interactions,
          follows, profile_visits
A metric Instagram stops offering is dropped for that post (instagram.media_insights asks
again without it), so a renamed metric costs one number, not the whole read.
"""
import hashlib
import io
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import instagram
import settings
from instagram import IgError
from posting import SGT

log = logging.getLogger("studio.results")

DAYS = 90
REEL_METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
                "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
FEED_METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
                "follows", "profile_visits"]
ACCOUNT_METRICS = ["reach", "views", "total_interactions"]
# Instagram's names -> the columns kept here.
COLUMNS = {"views": "views", "reach": "reach", "likes": "likes", "comments": "comments", "saved": "saves",
           "shares": "shares", "total_interactions": "interactions", "ig_reels_avg_watch_time": "avg_watch_ms",
           "ig_reels_video_view_total_time": "total_watch_ms", "follows": "follows", "profile_visits": "profile_visits"}
NUMBERS = ["views", "reach", "likes", "comments", "saves", "shares", "interactions", "avg_watch_ms",
           "total_watch_ms", "follows", "profile_visits"]
FIRST_HOUR_AFTER = timedelta(minutes=55)    # a new post is read again this long after it went out
FIRST_HOUR_UNTIL = timedelta(hours=6)

THUMB_WIDTH = 540                          # the grid's tiles are about 130 px wide; 540 covers a sharp phone
THUMB_MAX_BYTES = 20 * 1024 * 1024         # a picture from Instagram bigger than this isn't read
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_state: dict = {"daily": None, "early": [], "last_error": None, "last_ok": None}


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


def _state_file():
    return settings.ROOT / "results_state.json"


def _load_state() -> dict:
    try:
        _state.update(json.loads(_state_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return _state


def _save_state() -> None:
    try:
        _state_file().write_text(json.dumps({k: _state.get(k) for k in ("daily", "early", "last_ok", "avatar_pushed")}),
                                 encoding="utf-8")
    except OSError as e:
        log.debug("Results state not saved: %s", e)


def fmt(media: dict) -> str:
    """reel | carousel | image, from Instagram's media type."""
    t, p = (media.get("media_type") or "").upper(), (media.get("media_product_type") or "").upper()
    if t == "CAROUSEL_ALBUM":
        return "carousel"
    if t == "VIDEO" or p == "REELS":
        return "reel"
    return "image"


def _save_media(conn, m: dict) -> None:
    post = conn.execute("SELECT id FROM posts WHERE media_id = ?", (str(m["id"]),)).fetchone()
    reel = fmt(m) == "reel"
    shared = m.get("is_shared_to_feed")
    in_grid = (1 if shared else 0) if reel and isinstance(shared, bool) else None
    conn.execute("""INSERT INTO ig_media (media_id, post_id, media_type, product_type, permalink, caption, posted_at, seen_at,
                        in_grid, full_caption)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (media_id) DO UPDATE SET
                    post_id = COALESCE(excluded.post_id, ig_media.post_id), media_type = excluded.media_type,
                    product_type = excluded.product_type, permalink = COALESCE(excluded.permalink, ig_media.permalink),
                    caption = excluded.caption, posted_at = COALESCE(excluded.posted_at, ig_media.posted_at),
                    in_grid = COALESCE(excluded.in_grid, ig_media.in_grid), full_caption = excluded.full_caption""",
                 (str(m["id"]), post["id"] if post else None, m.get("media_type"), m.get("media_product_type"),
                  m.get("permalink"), (m.get("caption") or "")[:500], _iso(_parse(m.get("timestamp")) or _now()),
                  _iso(_now()), in_grid, (m.get("caption") or "")[:5000]))
    _keep_thumb(conn, m)


def _picture_url(m: dict) -> str:
    """Where Instagram has the post's picture: a Reel's (a video's) cover, else the picture (a carousel's
    media_url is its first slide). Only https links."""
    url = m.get("thumbnail_url") if fmt(m) == "reel" or (m.get("media_type") or "").upper() == "VIDEO" else None
    url = url or m.get("media_url") or m.get("thumbnail_url") or ""
    return url if str(url).startswith(("https://", "http://127.0.0.1", "http://localhost")) else ""


def small_jpeg(data: bytes, width: int = THUMB_WIDTH) -> bytes:
    """A picture as a small JPEG (never made bigger than it is)."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()


def _download(url: str) -> bytes | None:
    import requests
    try:
        r = requests.get(url, timeout=(10, 30), stream=True)
        if r.status_code != 200:
            return None
        data = r.raw.read(THUMB_MAX_BYTES + 1, decode_content=True)
        return data if 0 < len(data) <= THUMB_MAX_BYTES else None
    except Exception as e:  # a picture is a nicety: never stops the read
        log.debug("Picture not fetched: %s", type(e).__name__)
        return None


def _keep_thumb(conn, m: dict) -> bool:
    """A small still of the post, fetched once (IGThumbs/<media id>.jpg)."""
    media_id = str(m.get("id") or "")
    if not _MEDIA_ID.match(media_id):
        return False
    path = settings.IG_THUMBS / f"{media_id}.jpg"
    if path.is_file():
        conn.execute("UPDATE ig_media SET thumb = ? WHERE media_id = ? AND thumb IS NULL", (path.name, media_id))
        return True
    url = _picture_url(m)
    data = _download(url) if url else None
    if not data:
        return False
    try:
        small = small_jpeg(data)
    except Exception as e:
        log.debug("Picture of %s not readable: %s", media_id, type(e).__name__)
        return False
    settings.IG_THUMBS.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}")
    tmp.write_bytes(small)
    tmp.replace(path)
    conn.execute("UPDATE ig_media SET thumb = ? WHERE media_id = ?", (path.name, media_id))
    return True


# ── The account as its profile shows it ──────────────────────────────────────

def _profile_file():
    return settings.ROOT / "instagram_profile.json"


def read_profile(now: datetime | None = None) -> dict | None:
    """Name, handle, bio, counts and picture of the connected account (with the daily read). Kept in
    ROOT/instagram_profile.json and IGThumbs/avatar.jpg; nothing secret."""
    try:
        p = instagram.profile()
    except IgError as e:
        if e.kind in ("token", "offline"):
            raise
        log.debug("Profile not read: %s", e)
        return None
    num = lambda k: int(p[k]) if isinstance(p.get(k), (int, float)) else None
    out = {"username": str(p.get("username") or "")[:30], "name": str(p.get("name") or "")[:60],
           "biography": str(p.get("biography") or "")[:300], "followers": num("followers_count"),
           "follows": num("follows_count"), "media_count": num("media_count"), "read_at": _iso(now or _now())}
    pic = p.get("profile_picture_url") or ""
    data = _download(pic) if str(pic).startswith(("https://", "http://127.0.0.1", "http://localhost")) else None
    if data:
        try:
            settings.IG_THUMBS.mkdir(parents=True, exist_ok=True)
            (settings.IG_THUMBS / "avatar.jpg").write_bytes(small_jpeg(data, 240))
            out["avatar"] = hashlib.sha256((settings.IG_THUMBS / "avatar.jpg").read_bytes()).hexdigest()[:12]
        except Exception as e:
            log.debug("Profile picture not readable: %s", type(e).__name__)
    _profile_file().write_text(json.dumps(out), encoding="utf-8")
    return out


def profile_info() -> dict:
    """For laptop_status.info.instagram.profile (the grid's header): the account's own when Studio has read
    it, else from settings (STUDIO_INSTAGRAM_HANDLE / _NAME / _BIO). avatar_path once it's in the bucket."""
    try:
        saved = json.loads(_profile_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict) and saved.get("username") and instagram.connected():
        out = {k: saved.get(k) for k in ("username", "name", "biography", "followers", "follows", "media_count", "read_at")}
        out["source"] = "account"
        pushed = _load_state().get("avatar_pushed")
        if saved.get("avatar") and pushed == saved["avatar"]:
            out["avatar_path"] = f"profile/{pushed}.jpg"
        return out
    handle = settings.INSTAGRAM_HANDLE if settings.INSTAGRAM_HANDLE.lower() != "off" else ""
    return {"source": "settings", "username": handle, "name": settings.INSTAGRAM_NAME, "biography": settings.INSTAGRAM_BIO}


def read_post(conn, media_id: str, media: dict | None = None, day: date | None = None) -> dict:
    """One post's numbers now, kept for today (Singapore)."""
    row = conn.execute("SELECT * FROM ig_media WHERE media_id = ?", (str(media_id),)).fetchone()
    kind = fmt(media or (dict(row) | {"media_product_type": row["product_type"]} if row else {}))
    values = instagram.media_insights(str(media_id), REEL_METRICS if kind == "reel" else FEED_METRICS)
    cols = {COLUMNS[k]: int(round(v)) for k, v in values.items() if k in COLUMNS}
    day = (day or _now().astimezone(SGT).date()).isoformat()
    post = conn.execute("SELECT post_id FROM ig_media WHERE media_id = ?", (str(media_id),)).fetchone()
    names = ["media_id", "day", "post_id", *NUMBERS, "raw", "pulled_at"]
    vals = [str(media_id), day, post["post_id"] if post else None, *[cols.get(c) for c in NUMBERS],
            json.dumps(values, sort_keys=True), _iso(_now())]
    conn.execute(f"""INSERT INTO post_results ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})
                     ON CONFLICT (media_id, day) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in names[2:])}""", vals)
    conn.commit()
    return cols


def read_account(conn, now: datetime | None = None) -> dict:
    """Yesterday's reach, views and interactions (Singapore days) and the followers now."""
    now = now or _now()
    today = now.astimezone(SGT).date()
    day = today - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=SGT)
    values = instagram.account_insights(start, start + timedelta(days=1), ACCOUNT_METRICS)
    try:
        values["followers"] = instagram.followers()
    except IgError as e:
        if e.kind in ("token", "offline"):
            raise
        log.debug("Followers not read: %s", e)
    row = {"day": day.isoformat(), "followers": values.get("followers"), "reach": values.get("reach"),
           "views": values.get("views"), "interactions": values.get("total_interactions")}
    conn.execute("""INSERT INTO account_results (day, followers, reach, views, interactions, raw, pulled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (day) DO UPDATE SET followers = excluded.followers,
                    reach = excluded.reach, views = excluded.views, interactions = excluded.interactions,
                    raw = excluded.raw, pulled_at = excluded.pulled_at""",
                 (row["day"], *[int(v) if isinstance(v, (int, float)) else None for v in
                                (row["followers"], row["reach"], row["views"], row["interactions"])],
                  json.dumps(values, sort_keys=True), _iso(now)))
    conn.commit()
    return row


def pull(conn, now: datetime | None = None) -> dict:
    """The daily read: every post of the last 90 days and the account. Returns counts."""
    now = now or _now()
    media = instagram.recent_media(since=now - timedelta(days=DAYS), limit=400)
    done, failed = 0, 0
    for m in media:
        _save_media(conn, m)
        conn.commit()
        try:
            read_post(conn, m["id"], m)
            done += 1
        except IgError as e:
            if e.kind in ("token", "offline"):
                raise
            failed += 1
            log.info("Results for %s not read: %s", m.get("id"), e)
    account = read_account(conn, now)
    profile = read_profile(now)
    return {"posts": done, "failed": failed, "account": account, "profile": bool(profile)}


def tick(conn, now: datetime | None = None) -> None:
    """From the publisher: the daily read when it's time, and each new post's first hour."""
    if not instagram.connected():
        return
    now = now or _now()
    _load_state()
    local = now.astimezone(SGT)
    today = local.date().isoformat()
    try:
        early = set(_state.get("early") or [])
        for row in conn.execute("SELECT id, media_id, posted_at FROM posts WHERE state = 'posted' AND media_id IS NOT NULL "
                                "AND posted_at >= ?", (_iso(now - FIRST_HOUR_UNTIL),)).fetchall():
            posted = _parse(row["posted_at"])
            if row["id"] in early or not posted or now - posted < FIRST_HOUR_AFTER:
                continue
            read_post(conn, row["media_id"])
            early.add(row["id"])
            _state["early"] = sorted(early)[-200:]
            _save_state()
        if _state.get("daily") != today and local.hour >= settings.RESULTS_HOUR:
            counts = pull(conn, now)
            _state.update(daily=today, last_ok=_iso(now), last_error=None)
            _save_state()
            log.info("Results read: %d posts (%d not read), followers %s", counts["posts"], counts["failed"],
                     counts["account"].get("followers"))
    except IgError as e:
        instagram.note_problem(e)
        if str(e) != _state.get("last_error"):
            log.warning("Reading results failed (will retry): %s", e)
        _state["last_error"] = str(e)


# ── What Review, the API and the planner read ────────────────────────────────

def latest(conn, media_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM post_results WHERE media_id = ? ORDER BY day DESC LIMIT 1", (str(media_id),)).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in ("day", "pulled_at", *NUMBERS)}


def for_post(conn, post_id: int) -> dict | None:
    """A posted draft's latest numbers (Review's posted block)."""
    row = conn.execute("SELECT media_id FROM posts WHERE id = ? AND media_id IS NOT NULL", (post_id,)).fetchone()
    return latest(conn, row["media_id"]) if row else None


def rows(conn, days: int = DAYS, now: datetime | None = None) -> list[dict]:
    """Every post of the last `days` with its latest numbers, kind and format."""
    since = _iso((now or _now()) - timedelta(days=days))
    out = []
    for m in conn.execute("""SELECT m.*, p.post_type, p.title, p.kind AS post_kind FROM ig_media m
                             LEFT JOIN posts p ON p.id = m.post_id WHERE m.posted_at >= ? ORDER BY m.posted_at DESC""",
                          (since,)).fetchall():
        numbers = latest(conn, m["media_id"]) or {}
        posted = _parse(m["posted_at"])
        out.append({"media_id": m["media_id"], "post_id": m["post_id"], "title": m["title"] or (m["caption"] or "")[:80],
                    "permalink": m["permalink"], "posted_at": m["posted_at"],
                    "month": posted.astimezone(SGT).strftime("%Y-%m") if posted else None,
                    "format": m["post_kind"] or fmt({"media_type": m["media_type"], "media_product_type": m["product_type"]}),
                    "post_type": m["post_type"], "by_studio": m["post_id"] is not None, **{k: numbers.get(k) for k in NUMBERS}})
    return out


def group(items: list[dict], key: str) -> list[dict]:
    """Posts grouped by `key` (post_type, format, month): count, totals and per-post averages."""
    groups: dict = {}
    for r in items:
        g = groups.setdefault(r.get(key) or "", {"key": r.get(key) or "", "posts": 0, "totals": {k: 0 for k in NUMBERS},
                                                  "counted": {k: 0 for k in NUMBERS}})
        g["posts"] += 1
        for k in NUMBERS:
            if isinstance(r.get(k), (int, float)):
                g["totals"][k] += r[k]
                g["counted"][k] += 1
    out = []
    for g in groups.values():
        g["average"] = {k: round(g["totals"][k] / g["counted"][k], 1) if g["counted"][k] else None for k in NUMBERS}
        out.append(g)
    return sorted(out, key=lambda g: (-(g["average"].get("views") or 0), g["key"]))


def summary(conn, days: int = DAYS) -> dict:
    items = rows(conn, days)
    acct = [dict(r) for r in conn.execute("SELECT day, followers, reach, views, interactions FROM account_results "
                                          "ORDER BY day DESC LIMIT 90").fetchall()]
    return {"posts": items, "by_kind": group(items, "post_type"), "by_format": group(items, "format"),
            "by_month": sorted(group(items, "month"), key=lambda g: g["key"]), "account": acct,
            "last_read": _load_state().get("last_ok"), "problem": _state.get("last_error")}


# ── The planner's copy (cloud_sync) ───────────────────────────────────────────

def mirror(api, conn, grid: bool = False) -> int:
    """The latest numbers of each post and the account's days, to the planner. grid: the profile grid's
    migration is online, so each post's still (results/<media id>.jpg), whether it shows in the grid and its
    whole caption go too, and the account's picture (profile/<hash>.jpg)."""
    n = 0
    for m in conn.execute("""SELECT m.*, p.post_type, p.title, p.idea_remote FROM ig_media m
                             LEFT JOIN posts p ON p.id = m.post_id""").fetchall():
        latest_row = conn.execute("SELECT * FROM post_results WHERE media_id = ? ORDER BY day DESC LIMIT 1",
                                  (m["media_id"],)).fetchone()
        if latest_row is None:
            continue
        values = {"media_id": m["media_id"], "post_id": m["post_id"], "idea_id": m["idea_remote"],
                  "format": fmt({"media_type": m["media_type"], "media_product_type": m["product_type"]}),
                  "post_type": m["post_type"], "title": (m["title"] or (m["caption"] or "")[:120])[:300],
                  "permalink": m["permalink"], "posted_at": m["posted_at"], "day": latest_row["day"],
                  **{k: latest_row[k] for k in NUMBERS}, "pulled_at": latest_row["pulled_at"]}
        if grid:
            values.update(in_grid=None if m["in_grid"] is None else bool(m["in_grid"]),
                          caption=(m["full_caption"] or m["caption"] or "")[:5000],
                          thumb_path=_push_thumb(api, conn, m))
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
        if digest == latest_row["cloud_hash"]:
            continue
        api.insert("post_results", values, on_conflict="media_id", resolution="merge-duplicates", returning=False)
        conn.execute("UPDATE post_results SET cloud_hash = ? WHERE media_id = ? AND day = ?",
                     (digest, m["media_id"], latest_row["day"]))
        conn.commit()
        n += 1
    for r in conn.execute("SELECT * FROM account_results ORDER BY day").fetchall():
        values = {"day": r["day"], "followers": r["followers"], "reach": r["reach"], "views": r["views"],
                  "interactions": r["interactions"], "pulled_at": r["pulled_at"]}
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
        if digest == r["cloud_hash"]:
            continue
        api.insert("account_results", values, on_conflict="day", resolution="merge-duplicates", returning=False)
        conn.execute("UPDATE account_results SET cloud_hash = ? WHERE day = ?", (digest, r["day"]))
        conn.commit()
        n += 1
    if grid:
        _push_avatar(api)
    return n


def _push_thumb(api, conn, m) -> str | None:
    """The post's still in the thumbs bucket (uploaded once), or None when Studio has none."""
    name = m["thumb"]
    path = settings.IG_THUMBS / name if name else None
    if not path or not path.is_file() or not _MEDIA_ID.match(str(m["media_id"])):
        return None
    key = f"results/{m['media_id']}.jpg"
    if m["thumb_pushed"] != name:
        try:
            api.upload(key, path.read_bytes())
        except Exception as e:
            log.debug("Still of %s not uploaded: %s", m["media_id"], e)
            return None
        conn.execute("UPDATE ig_media SET thumb_pushed = ? WHERE media_id = ?", (name, m["media_id"]))
        conn.commit()
    return key


def _push_avatar(api) -> None:
    try:
        saved = json.loads(_profile_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    tag, path = saved.get("avatar"), settings.IG_THUMBS / "avatar.jpg"
    if not tag or _load_state().get("avatar_pushed") == tag or not path.is_file():
        return
    try:
        api.upload(f"profile/{tag}.jpg", path.read_bytes())
    except Exception as e:
        log.debug("Profile picture not uploaded: %s", e)
        return
    _state["avatar_pushed"] = tag
    _save_state()
