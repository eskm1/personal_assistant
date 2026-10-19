"""
Posting approved drafts to Instagram (the Studio Director plan, phase 1). Studio posts
only what Bryan approved and scheduled, never on its own.

  schedule()   Bryan picks a date and time (Singapore) on an approved Reel or carousel, edits
               the caption, the Reel's cover (a moment of the video) and collaborators, or
               presses Post now. One post per draft; he can reschedule or cancel until it's out.
  tick()       the publisher (a thread of the worker, every 15 s): renews the Instagram token
               when due, publishes what's due, and reads results (post_results.py).
  publish()    the files are checked (Reels: H.264/AAC mp4, transcoded only when Instagram
               would refuse them; carousels: the PNG slides as JPEGs), served to Instagram on a
               short-lived link (/public/media/<signed token>/<file>, only while that post is
               going out, two hours at most), then: container(s) -> wait for FINISHED ->
               media_publish -> permalink. Passing problems are retried with backoff; the
               rest fail with a plain reason. Telegram: "Posted: ..." or "Couldn't post ...".
  Never twice: publish_at is saved before media_publish is sent. After a crash, a post with
  publish_at and no media id is first looked for on the account (its container says
  PUBLISHED, or a post with its caption appeared), and only published if it isn't there.

Kept in the catalogue (posts) and mirrored to the planner (Supabase `posts`) by mirror().
"""
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import catalog
import instagram
import settings
from instagram import IgError

log = logging.getLogger("studio.posting")

SGT = timezone(timedelta(hours=8), "SGT")   # Singapore has no summer time
CAPTION_MAX = 2200
HASHTAGS_MAX = 30
MENTIONS_MAX = 20
COLLABORATORS_MAX = 3
REEL_MIN_SECONDS, REEL_MAX_SECONDS = 3, 15 * 60
REEL_MAX_BYTES = 300 * 1024 * 1024
REEL_MAX_KBPS = 25_000
JPEG_MAX_BYTES = 8 * 1024 * 1024
LINK_SECONDS = 2 * 3600            # a public media link works for two hours
MAX_ATTEMPTS = 5                   # passing problems: tries before it fails for good
BACKOFF_MINUTES = (2, 5, 15, 30, 60)
POLL_GIVE_UP_SECONDS = 20 * 60     # a container still processing after this is tried again later
POLL_STEPS = (5, 5, 10, 10, 15, 20, 30)
ACTIVE = ("scheduled", "publishing", "posted")
STATES = ("scheduled", "publishing", "posted", "failed", "cancelled")
USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
HASHTAG = re.compile(r"(?<![\w&])#\w+", re.UNICODE)
MENTION = re.compile(r"(?<![\w.])@[A-Za-z0-9._]{1,30}")

_lock = threading.Lock()           # one publish at a time
_sleep = time.sleep                # tests make waiting instant
status: dict = {"last_tick": None, "publishing": None, "last_error": None}


class PostError(ValueError):
    """A request Bryan can fix (shown as it is)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(stamp) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=SGT)  # a time without a zone is Singapore time


def sg_day(t: datetime) -> date:
    return t.astimezone(SGT).date()


def when_text(t: datetime) -> str:
    """"Fri 24 Oct, 7:30pm" in Singapore time."""
    s = t.astimezone(SGT)
    return f"{s:%a} {s.day} {s:%b}, {s.strftime('%I:%M%p').lstrip('0').lower()}"


# ── Captions ──────────────────────────────────────────────────────────────────

def caption_check(text: str) -> dict:
    """Instagram's caption limits: 2,200 characters, 30 hashtags, 20 @mentions."""
    text = text or ""
    chars, tags, mentions = len(text), len(HASHTAG.findall(text)), len(MENTION.findall(text))
    problems = []
    if chars > CAPTION_MAX:
        problems.append(f"The caption is {chars - CAPTION_MAX} characters too long (Instagram takes {CAPTION_MAX}).")
    if tags > HASHTAGS_MAX:
        problems.append(f"{tags} hashtags: Instagram takes at most {HASHTAGS_MAX}.")
    if mentions > MENTIONS_MAX:
        problems.append(f"{mentions} @mentions: Instagram takes at most {MENTIONS_MAX}.")
    return {"chars": chars, "hashtags": tags, "mentions": mentions, "problems": problems,
            "limits": {"chars": CAPTION_MAX, "hashtags": HASHTAGS_MAX, "mentions": MENTIONS_MAX}}


def clean_collaborators(value) -> list[str]:
    names = value if isinstance(value, list) else re.split(r"[\s,]+", str(value or ""))
    out = []
    for n in names:
        n = str(n).strip().lstrip("@")
        if not n:
            continue
        if not USERNAME.match(n):
            raise PostError(f"\"{n}\" isn't an Instagram username (letters, numbers, dots and underscores).")
        if n.lower() not in [o.lower() for o in out]:
            out.append(n)
    if len(out) > COLLABORATORS_MAX:
        raise PostError(f"Instagram allows at most {COLLABORATORS_MAX} collaborators.")
    return out


# ── The posts ─────────────────────────────────────────────────────────────────

def _row(conn, post_id: int):
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def view(row) -> dict:
    """A post as the browser sees it (planner, Review, API)."""
    if row is None:
        return None
    d = dict(row)
    d["collaborators"] = json.loads(row["collaborators"] or "[]")
    d["share_to_feed"] = bool(row["share_to_feed"])
    for k in ("containers", "cloud_hash", "publish_at"):
        d.pop(k, None)
    at = parse(row["posted_at"] or row["scheduled_at"])
    d["when_text"] = when_text(at) if at else ""
    return d


def active_for(conn, production_id: int):
    return conn.execute(f"SELECT * FROM posts WHERE production_id = ? AND state IN ({','.join('?' * len(ACTIVE))}) "
                        "ORDER BY id DESC LIMIT 1", (production_id, *ACTIVE)).fetchone()


def latest_for(conn, production_id: int):
    """The draft's post: the live one, else the last failed or cancelled one."""
    return active_for(conn, production_id) or conn.execute(
        "SELECT * FROM posts WHERE production_id = ? ORDER BY id DESC LIMIT 1", (production_id,)).fetchone()


def per_week(api=None) -> int:
    """Posts a week the schedule spreads: STUDIO_POSTS_PER_WEEK, else the content plan's."""
    if settings.POSTS_PER_WEEK:
        return settings.POSTS_PER_WEEK
    try:
        import cloud_sync
        if api is None and cloud_sync.enabled():
            api = cloud_sync.Client()
        if api is not None:
            rows = api.select("content_plan", {"select": "id,posts_per_week"})
            if rows and rows[0].get("posts_per_week"):
                return max(1, int(rows[0]["posts_per_week"]))
    except Exception as e:
        log.debug("Posts a week from the content plan not available (%s)", e)
    return 3


def taken(conn, exclude: int | None = None) -> list[datetime]:
    """When the live posts go (or went) out."""
    rows = conn.execute(f"SELECT id, scheduled_at, posted_at FROM posts WHERE state IN ({','.join('?' * len(ACTIVE))})",
                        ACTIVE).fetchall()
    return [t for r in rows if r["id"] != exclude for t in [parse(r["posted_at"] or r["scheduled_at"])] if t]


def _post_time() -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", settings.POST_TIME.strip())
    h, mi = (int(m[1]), int(m[2])) if m else (19, 30)
    return (h if 0 <= h <= 23 else 19), (mi if 0 <= mi <= 59 else 30)


def suggest(conn, now: datetime | None = None, n: int | None = None, exclude: int | None = None) -> datetime:
    """The next free slot that keeps about n posts a week, spread over the week: a day with no
    post near it (3 a week = every other day at least), its week not full yet, at the usual
    time (STUDIO_POST_TIME, Singapore)."""
    now = now or _now()
    n = max(1, n or per_week())
    days = [sg_day(t) for t in taken(conn, exclude)]
    gap = max(1, 7 // n) if n <= 7 else 0          # days between posts (2 for 3 a week)
    per_day = max(1, -(-n // 7))
    h, mi = _post_time()
    today = sg_day(now)
    for d in range(0, 60):
        day = today + timedelta(days=d)
        at = datetime(day.year, day.month, day.day, h, mi, tzinfo=SGT)
        if at < now + timedelta(minutes=30):
            continue
        week = day - timedelta(days=day.weekday())
        if sum(1 for x in days if week <= x < week + timedelta(days=7)) >= n:
            continue
        if sum(1 for x in days if x == day) >= per_day:
            continue
        if gap > 1 and any(0 < abs((x - day).days) < gap for x in days):
            continue
        return at.astimezone(timezone.utc)
    return (now + timedelta(days=1)).replace(microsecond=0)


def _production(conn, production_id: int):
    return conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()


def _kind(prod) -> str:
    return "carousel" if prod["format"] == "carousel" else "reel"


def _idea_remote(conn, production_id: int) -> int | None:
    row = conn.execute("SELECT remote_id FROM ideas WHERE production_id = ? AND remote_id IS NOT NULL", (production_id,)).fetchone()
    return row["remote_id"] if row else None


def _slides(prod) -> list[Path]:
    import carousel
    return [Path(p) for p in carousel.files(prod)]


def can_post(conn, prod) -> str | None:
    """Why this draft can't be scheduled (or None)."""
    if prod is None:
        return "That draft is gone."
    if prod["status"] not in ("approved", "posted"):
        return "Approve the draft first: only approved drafts can be posted."
    if prod["removed_at"]:
        return "Its files were removed in the clean-up."
    if _kind(prod) == "carousel":
        n = len(_slides(prod))
        if n < 2:
            return "A carousel needs at least 2 slides."
        if n > settings.INSTAGRAM_CAROUSEL_MAX:
            return (f"Instagram takes at most {settings.INSTAGRAM_CAROUSEL_MAX} slides in a carousel; this one has {n}. "
                    "Send it back with a note to cut it down first.")
    else:
        if not prod["video_path"] or not Path(prod["video_path"]).is_file():
            return "The draft's video file is missing."
        if prod["duration"] and not REEL_MIN_SECONDS <= prod["duration"] <= REEL_MAX_SECONDS:
            return f"Instagram takes Reels of {REEL_MIN_SECONDS} seconds to 15 minutes."
    return None


def _when(value, now: datetime) -> datetime:
    if value in (None, "", "now"):
        return now
    t = parse(value)
    if t is None:
        raise PostError("Pick a date and time.")
    if t < now - timedelta(minutes=2):
        raise PostError("That time has passed. Pick a later one, or Post now.")
    if t > now + timedelta(days=180):
        raise PostError("Pick a time within the next six months.")
    return t.replace(second=0, microsecond=0)


def schedule(conn, production_id: int, when=None, caption: str | None = None, cover_ms=None,
             share_to_feed: bool = True, collaborators=None, by: str = "", now: datetime | None = None) -> dict:
    """Schedule an approved draft (when=None or "now" = Post now). Raises PostError."""
    now = now or _now()
    prod = _production(conn, production_id)
    why = can_post(conn, prod)
    if why:
        raise PostError(why)
    live = active_for(conn, production_id)
    if live:
        raise PostError("This draft already went out." if live["state"] == "posted"
                        else "This draft is already scheduled. Change its time instead.")
    caption = (prod["caption"] or "") if caption is None else str(caption)
    check = caption_check(caption)
    if check["problems"]:
        raise PostError(" ".join(check["problems"]))
    at = _when(when, now)
    kind = _kind(prod)
    cover = None if kind == "carousel" or cover_ms in (None, "") else max(0, int(float(cover_ms)))
    if cover is not None and prod["duration"] and cover > prod["duration"] * 1000:
        raise PostError("The cover moment is past the end of the video.")
    stamp = _iso(now)
    cur = conn.execute(
        """INSERT INTO posts (production_id, idea_remote, kind, title, caption, version, scheduled_at, cover_ms,
               share_to_feed, collaborators, state, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)""",
        (production_id, _idea_remote(conn, production_id), kind, prod["title"], caption, prod["version"], _iso(at),
         cover, int(bool(share_to_feed)) if kind == "reel" else 1, json.dumps(clean_collaborators(collaborators)),
         by or None, stamp, stamp))
    if caption != (prod["caption"] or ""):
        conn.execute("UPDATE productions SET caption = ?, updated_at = ? WHERE id = ?", (caption, stamp, production_id))
    conn.commit()
    log.info("Post %s: %s scheduled for %s by %s", cur.lastrowid, prod["title"], _iso(at), by or "?")
    _wake()
    return view(_row(conn, cur.lastrowid))


def change(conn, post_id: int, when=None, caption: str | None = None, cover_ms=None, share_to_feed=None,
           collaborators=None, now: datetime | None = None) -> dict:
    """Reschedule or edit a post that hasn't gone out (also a failed one: try again)."""
    now = now or _now()
    row = _row(conn, post_id)
    if row is None:
        raise KeyError(post_id)
    if row["state"] == "publishing":
        raise PostError("It's going out right now.")
    if row["state"] == "posted":
        raise PostError("It's already on Instagram. Change it in the Instagram app.")
    if row["state"] == "cancelled":
        raise PostError("This post was cancelled. Schedule the draft again.")
    prod = _production(conn, row["production_id"])
    why = can_post(conn, prod)
    if why:
        raise PostError(why)
    values = {"scheduled_at": _iso(_when(when, now)), "state": "scheduled", "attempts": 0, "next_try_at": None,
              "error": None, "problem": None, "stage": None, "updated_at": _iso(now)}
    if caption is not None:
        check = caption_check(caption)
        if check["problems"]:
            raise PostError(" ".join(check["problems"]))
        values["caption"] = caption
    if cover_ms is not None and row["kind"] == "reel":
        values["cover_ms"] = None if cover_ms == "" else max(0, int(float(cover_ms)))
    if share_to_feed is not None and row["kind"] == "reel":
        values["share_to_feed"] = int(bool(share_to_feed))
    if collaborators is not None:
        values["collaborators"] = json.dumps(clean_collaborators(collaborators))
    if row["state"] == "failed" and not row["publish_at"]:
        values["containers"] = None  # a fresh start (an expired or refused container is no use)
    sets = ", ".join(f"{k} = ?" for k in values)
    n = conn.execute(f"UPDATE posts SET {sets} WHERE id = ? AND state IN ('scheduled', 'failed')",
                     (*values.values(), post_id)).rowcount
    conn.commit()
    if not n:
        raise PostError("It's going out right now.")
    log.info("Post %s rescheduled for %s", post_id, values["scheduled_at"])
    _wake()
    return view(_row(conn, post_id))


def cancel(conn, post_id: int) -> dict:
    row = _row(conn, post_id)
    if row is None:
        raise KeyError(post_id)
    if row["state"] == "posted":
        raise PostError("It's already on Instagram. Delete it in the Instagram app if you need to.")
    n = conn.execute("UPDATE posts SET state = 'cancelled', next_try_at = NULL, updated_at = ? "
                     "WHERE id = ? AND state IN ('scheduled', 'failed')", (_iso(_now()), post_id)).rowcount
    conn.commit()
    if not n and row["state"] != "cancelled":
        raise PostError("It's going out right now, so it can't be cancelled.")
    log.info("Post %s cancelled", post_id)
    return view(_row(conn, post_id))


def plan_for(conn, production_id: int) -> dict:
    """What the Schedule dialog needs for a draft: the draft, its post, a suggested slot and
    the busy days around it."""
    prod = _production(conn, production_id)
    if prod is None:
        raise KeyError(production_id)
    post = latest_for(conn, production_id)
    n = per_week()
    nxt = suggest(conn, n=n, exclude=post["id"] if post else None)
    import carousel
    return {"production": {"id": prod["id"], "title": prod["title"], "format": prod["format"], "kind": _kind(prod),
                           "status": prod["status"], "version": prod["version"], "duration": prod["duration"],
                           "caption": prod["caption"] or "",
                           "slides": len(carousel.files(prod)) if prod["format"] == "carousel" else None,
                           "thumb": f"/media/production/{prod['id']}/thumb?v={prod['version']}" if prod["thumb_path"] else None,
                           "video": f"/media/production/{prod['id']}/video?v={prod['version']}" if prod["video_path"] else None},
            "why_not": can_post(conn, prod), "post": view(post) if post else None,
            "suggested": _iso(nxt), "suggested_text": when_text(nxt), "per_week": n,
            "taken": sorted(_iso(t) for t in taken(conn, post["id"] if post else None) if t > _now() - timedelta(days=14)),
            "connected": instagram.connected(), "posting_on": settings.POSTING,
            "limits": caption_check("")["limits"], "collaborators_max": COLLABORATORS_MAX}


def listing(conn, since: datetime | None = None) -> list[dict]:
    since = since or (_now() - timedelta(days=62))
    rows = conn.execute("SELECT * FROM posts WHERE state <> 'cancelled' AND COALESCE(posted_at, scheduled_at) >= ? "
                        "ORDER BY COALESCE(posted_at, scheduled_at)", (_iso(since),)).fetchall()
    return [view(r) for r in rows]


# ── Files for Instagram ───────────────────────────────────────────────────────

def _folder(post_id: int) -> Path:
    return settings.POSTING_DIR / f"post_{post_id}"


def prepared(post_id: int) -> list[Path]:
    folder = _folder(post_id)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix in (".mp4", ".jpg") and not p.name.startswith("."))


def probe(path: Path) -> dict:
    r = subprocess.run([settings.FFPROBE, "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,sample_rate:"
                        "format=duration,size,bit_rate", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise IgError(f"The video can't be read: {r.stderr.strip()[-200:]}", "spec")
    return json.loads(r.stdout or "{}")


def _fps(rate: str | None) -> float:
    try:
        a, b = str(rate or "0/1").split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def reel_problems(info: dict) -> list[str]:
    """What Instagram would refuse in this video (Reels: MP4, H.264 or HEVC 4:2:0, 23-60 fps,
    AAC at 48 kHz or less, 25 Mbps at most, 300 MB, 3 s to 15 min). Empty = fine as it is."""
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format") or {}
    out = []
    if v is None:
        return ["no picture"]
    if v.get("codec_name") not in ("h264", "hevc"):
        out.append(f"video codec {v.get('codec_name')}")
    if v.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        out.append(f"pixel format {v.get('pix_fmt')}")
    if not 23 <= _fps(v.get("avg_frame_rate")) <= 60:
        out.append(f"{_fps(v.get('avg_frame_rate')):.0f} fps")
    if int(v.get("width") or 0) > 1920 or int(v.get("height") or 0) > 3840:
        out.append("too large a picture")
    if a is not None and (a.get("codec_name") != "aac" or int(a.get("sample_rate") or 0) > 48000):
        out.append(f"audio {a.get('codec_name')} {a.get('sample_rate')} Hz")
    if int(fmt.get("bit_rate") or 0) > REEL_MAX_KBPS * 1000:
        out.append(f"{int(fmt['bit_rate']) // 1000} kbps")
    if int(fmt.get("size") or 0) > REEL_MAX_BYTES:
        out.append(f"{int(fmt['size']) >> 20} MB")
    return out


def prepare_reel(src: Path, dest: Path) -> Path:
    """The full-quality draft as Instagram wants it: copied as it is (moov at the front) when it
    already fits, transcoded otherwise."""
    info = probe(src)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration and not REEL_MIN_SECONDS <= duration <= REEL_MAX_SECONDS:
        raise IgError(f"Instagram takes Reels of 3 seconds to 15 minutes; this one is {duration:.0f} s", "spec")
    problems = reel_problems(info)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}")
    if not problems:
        args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart"]
    else:
        log.info("Transcoding %s for Instagram (%s)", src.name, ", ".join(problems))
        fps = _fps(next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}).get("avg_frame_rate"))
        args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale='min(1080,iw)':-2:flags=lanczos,format=yuv420p",
                "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-crf", "20",
                "-maxrate", "20M", "-bufsize", "40M", "-g", "60", "-keyint_min", "30", "-sc_threshold", "0",
                *([] if 23 <= fps <= 60 else ["-r", "30"]),
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
    r = subprocess.run([settings.FFMPEG, "-y", "-v", "error", *args, "-f", "mp4", str(tmp)],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise IgError(f"Couldn't make the video for Instagram: {r.stderr.strip()[-200:]}", "other")
    left = reel_problems(probe(tmp))
    if left:
        tmp.unlink(missing_ok=True)
        raise IgError(f"The video still doesn't fit Instagram's rules ({', '.join(left)})", "spec")
    tmp.replace(dest)
    return dest


def prepare_slide(src: Path, dest: Path) -> Path:
    """One carousel slide as a JPEG (Instagram takes JPEG only), at most 1440 wide, 8 MB, an
    aspect between 4:5 and 1.91:1."""
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        ratio = w / h
        if not 0.79 <= ratio <= 1.92:
            raise IgError(f"Slide {src.name} is {w}x{h}; Instagram takes 4:5 to 1.91:1", "spec")
        if w > 1440:
            im = im.resize((1440, round(1440 * h / w)), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}")
        for quality in (92, 85, 75, 65):
            im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=False)
            if tmp.stat().st_size <= JPEG_MAX_BYTES:
                break
        tmp.replace(dest)
    return dest


def prepare(conn, row) -> list[Path]:
    """The post's files in ROOT/Posting/post_<id>/ (made once; reused on a retry)."""
    have = prepared(row["id"])
    if have:
        return have
    prod = _production(conn, row["production_id"])
    why = can_post(conn, prod)
    if why:
        raise IgError(why, "spec")
    folder = _folder(row["id"])
    folder.mkdir(parents=True, exist_ok=True)
    if row["kind"] == "reel":
        return [prepare_reel(Path(prod["video_path"]), folder / "reel.mp4")]
    return [prepare_slide(p, folder / f"{n:02d}.jpg") for n, p in enumerate(_slides(prod), 1)]


def drop_files(post_id: int) -> None:
    shutil.rmtree(_folder(post_id), ignore_errors=True)


# ── The short-lived public links Instagram fetches the files from ─────────────

def _signer():
    import auth
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(auth.secret_key(), salt="studio-public-media")


def public_base() -> str:
    """Where Instagram can reach this machine. https, except a local test address."""
    base = settings.PUBLIC_URL
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
    if base.startswith("https://") or (base.startswith("http://") and host in ("127.0.0.1", "localhost")):
        return base
    raise IgError("Studio's public address isn't set, so Instagram can't fetch the files "
                  "(STUDIO_DASHBOARD_URL in the server's .env, https).", "other")


def media_url(post_id: int, path: Path) -> str:
    import secrets
    token = _signer().dumps({"p": post_id, "f": path.name, "n": secrets.token_urlsafe(8)})
    return f"{public_base()}/public/media/{token}/{path.name}"


def public_file(conn, token: str, name: str) -> Path | None:
    """The file a public link points at, only while its post is going out and the link is
    under two hours old; else None (404)."""
    from itsdangerous import BadSignature, SignatureExpired
    try:
        data = _signer().loads(token, max_age=LINK_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("f") != name:
        return None
    row = conn.execute("SELECT state FROM posts WHERE id = ?", (data.get("p"),)).fetchone()
    if row is None or row["state"] != "publishing":
        return None
    return next((p for p in prepared(int(data["p"])) if p.name == name), None)


# ── Publishing ────────────────────────────────────────────────────────────────

_event = threading.Event()


def _wake() -> None:
    _event.set()


def _set(conn, post_id: int, **values) -> None:
    values["updated_at"] = _iso(_now())
    conn.execute(f"UPDATE posts SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), post_id))
    conn.commit()


def due(conn, now: datetime | None = None) -> list:
    """Posts whose time has come (and their retry time, after a passing problem), plus any left
    'publishing' by a crash."""
    now = _iso(now or _now())
    return conn.execute("""SELECT * FROM posts WHERE (state = 'scheduled' AND scheduled_at <= ?
                               AND (next_try_at IS NULL OR next_try_at <= ?)) OR state = 'publishing'
                           ORDER BY scheduled_at""", (now, now)).fetchall()


def _containers(row) -> dict:
    try:
        return json.loads(row["containers"] or "{}") or {}
    except ValueError:
        return {}


def _wait(container_id: str, give_up: float = POLL_GIVE_UP_SECONDS) -> str:
    """Poll a container until FINISHED (or PUBLISHED); raises IgError on ERROR / EXPIRED / too long."""
    started, i = time.monotonic(), 0
    while True:
        st = instagram.container_status(container_id)
        code = str(st.get("status_code") or "").upper()
        if code in ("FINISHED", "PUBLISHED"):
            return code
        if code == "ERROR":
            raise IgError(f"Instagram couldn't process it: {st.get('status') or 'no reason given'}", "spec")
        if code == "EXPIRED":
            raise IgError("Instagram's upload expired before it was published", "media")
        if time.monotonic() - started > give_up:
            raise IgError("Instagram is still processing the video", "transient")
        _sleep(POLL_STEPS[min(i, len(POLL_STEPS) - 1)])
        i += 1


def _find_published(row, around: datetime) -> dict | None:
    """After a crash between media_publish and recording it: the post on the account, by its
    container (PUBLISHED) or its caption, published after it was sent."""
    since = around - timedelta(minutes=15)
    caption = (row["caption"] or "").strip()
    for m in instagram.recent_media(since=since, limit=25):
        if (m.get("caption") or "").strip() == caption:
            return m
    return None


def _record(conn, row, media_id: str, media: dict | None = None) -> dict:
    """The post is on Instagram: keep its id and link, mark the draft posted, tell Bryan."""
    try:
        media = media or instagram.media(media_id)
    except IgError as e:
        log.warning("Post %s is out (media %s) but its link couldn't be read yet: %s", row["id"], media_id, e)
        media = {"id": media_id}
    posted = parse(media.get("timestamp")) or _now()
    permalink = media.get("permalink") or None
    _set(conn, row["id"], state="posted", stage=None, media_id=str(media_id), permalink=permalink,
         posted_at=_iso(posted), error=None, problem=None, next_try_at=None)
    conn.execute("UPDATE productions SET status = 'posted', archived_at = NULL, updated_at = ? WHERE id = ?",
                 (_iso(_now()), row["production_id"]))
    conn.execute("""INSERT INTO ig_media (media_id, post_id, media_type, product_type, permalink, caption, posted_at, seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (media_id) DO UPDATE SET post_id = excluded.post_id,
                    permalink = COALESCE(excluded.permalink, ig_media.permalink)""",
                 (str(media_id), row["id"], media.get("media_type"), media.get("media_product_type"), permalink,
                  (row["caption"] or "")[:500], _iso(posted), _iso(_now())))
    conn.commit()
    drop_files(row["id"])
    log.info("Post %s is on Instagram: %s", row["id"], permalink or media_id)
    import notify
    notify.send(f"Posted: {row['title']} {permalink or ''}".strip())
    return view(_row(conn, row["id"]))


def _build(conn, row, files: list[Path]) -> str:
    """Instagram's container for the post (reused when one from an earlier try still works)."""
    have = _containers(row)
    if have.get("parent"):
        try:
            code = str(instagram.container_status(have["parent"]).get("status_code") or "").upper()
            if code in ("FINISHED", "IN_PROGRESS", "PUBLISHED"):
                return have["parent"]
        except IgError as e:
            if e.kind in ("token", "offline"):
                raise
    caption = row["caption"] or ""
    collaborators = json.loads(row["collaborators"] or "[]")
    collab = json.dumps(collaborators) if collaborators else None
    _set(conn, row["id"], stage="uploading")
    if row["kind"] == "reel":
        params = {"media_type": "REELS", "video_url": media_url(row["id"], files[0]), "caption": caption,
                  "share_to_feed": "true" if row["share_to_feed"] else "false", "collaborators": collab}
        if row["cover_ms"] is not None:
            params["thumb_offset"] = int(row["cover_ms"])
        parent = instagram.create_container(params)
        _set(conn, row["id"], containers=json.dumps({"parent": parent}))
        return parent
    children = []
    for f in files:
        children.append(instagram.create_container({"image_url": media_url(row["id"], f), "is_carousel_item": "true"}))
        _set(conn, row["id"], containers=json.dumps({"children": children}))
    for c in children:
        _wait(c, give_up=5 * 60)
    parent = instagram.create_container({"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption,
                                         "collaborators": collab})
    _set(conn, row["id"], containers=json.dumps({"children": children, "parent": parent}))
    return parent


def publish(conn, post_id: int) -> dict:
    """Take one due post out to Instagram. Never raises: problems end up on the post."""
    with _lock:
        row = _row(conn, post_id)
        if row is None or row["state"] not in ("scheduled", "publishing"):
            return view(row)
        if row["state"] == "scheduled":
            n = conn.execute("UPDATE posts SET state = 'publishing', stage = 'preparing', attempts = attempts + 1, "
                             "updated_at = ? WHERE id = ? AND state = 'scheduled'", (_iso(_now()), post_id)).rowcount
            conn.commit()
            if not n:
                return view(_row(conn, post_id))
            row = _row(conn, post_id)
        status["publishing"] = post_id
        try:
            if not settings.POSTING:
                raise IgError("Posting is switched off on this machine (STUDIO_POSTING)", "other")
            if not instagram.connected():
                raise IgError("Instagram isn't connected. Connect it under More > Instagram.", "token")
            # Sent to Instagram before a crash: look before publishing again.
            if row["publish_at"] and not row["media_id"]:
                parent = _containers(row).get("parent")
                code = ""
                if parent:
                    try:
                        code = str(instagram.container_status(parent).get("status_code") or "").upper()
                    except IgError as e:
                        if e.kind in ("token", "offline"):
                            raise
                found = _find_published(row, parse(row["publish_at"]) or _now())
                if found:
                    log.info("Post %s was already on Instagram (found after a restart): not posting it again", post_id)
                    return _record(conn, row, str(found["id"]), found)
                if code == "PUBLISHED":
                    raise IgError("Instagram says it was published, but Studio can't find the post yet. It won't post "
                                  "it again: check the account, then mark it or try again.", "other")
            files = prepare(conn, row)
            parent = _build(conn, row, files)
            _set(conn, post_id, stage="processing")
            row = _row(conn, post_id)
            if _wait(parent) == "PUBLISHED":
                found = _find_published(row, parse(row["publish_at"]) or _now())
                if found:
                    return _record(conn, row, str(found["id"]), found)
                raise IgError("Instagram says it was published, but Studio can't find the post yet", "other")
            # Never twice: from here on a restart looks for the post before trying again.
            _set(conn, post_id, stage="publishing", publish_at=_iso(_now()))
            media_id = instagram.publish(parent)
            _set(conn, post_id, media_id=str(media_id))
            return _record(conn, _row(conn, post_id), media_id)
        except IgError as e:
            return _problem(conn, _row(conn, post_id), e)
        except Exception as e:  # never let one post stop the publisher
            log.exception("Post %s failed unexpectedly", post_id)
            return _problem(conn, _row(conn, post_id), IgError(f"{type(e).__name__}: {e}"[:300], "other"))
        finally:
            status["publishing"] = None


def _problem(conn, row, e: IgError) -> dict:
    """Retry passing problems with backoff; fail the rest (and after MAX_ATTEMPTS) and tell Bryan."""
    import notify
    instagram.note_problem(e)
    attempts = row["attempts"] or 1
    retry = e.transient or e.kind in ("media", "limit")
    if row["publish_at"] and not row["media_id"]:
        retry = retry and e.kind != "other"  # after a publish was sent, only a clear passing problem retries
    if retry and attempts < MAX_ATTEMPTS:
        minutes = 60 if e.kind == "limit" else BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
        at = _now() + timedelta(minutes=minutes)
        containers = None if e.kind == "media" else row["containers"]
        _set(conn, row["id"], state="scheduled", stage=None, next_try_at=_iso(at), problem=e.kind,
             error=f"{str(e).rstrip('. ')}. Trying again at {when_text(at)}.", containers=containers)
        log.warning("Post %s: %s (%s); trying again in %d min", row["id"], e, e.kind, minutes)
        return view(_row(conn, row["id"]))
    said = str(e).rstrip(". ")
    reason = {"token": f"{said}. Reconnect it under More > Instagram, then try again.",
              "spec": f"Instagram refused it: {said}.",
              "limit": f"{said}. Instagram's daily posting limit was reached."}.get(e.kind, f"{said}.")
    _set(conn, row["id"], state="failed", stage=None, next_try_at=None, problem=e.kind, error=reason[:500])
    log.warning("Post %s failed (%s): %s", row["id"], e.kind, e)
    notify.send(f"Couldn't post {row['title']}: {reason}")
    return view(_row(conn, row["id"]))


# ── The publisher thread ──────────────────────────────────────────────────────

def tick(conn) -> int:
    """One round: renew the token if due, publish what's due, read results. Never raises."""
    status["last_tick"] = _iso(_now())
    n = 0
    try:
        instagram.maybe_refresh()
        for row in due(conn):
            publish(conn, row["id"])
            n += 1
    except Exception as e:
        status["last_error"] = str(e)[:300]
        log.exception("Publishing round failed")
    try:
        import post_results
        post_results.tick(conn)
    except Exception:
        log.exception("Reading results failed")
    return n


def run_forever(stop: threading.Event | None = None, every: float = 15) -> None:
    """The worker's publisher thread (worker.py)."""
    conn = catalog.connect()
    log.info("Publisher on: %s", "posting allowed" if settings.POSTING else "posting switched off (STUDIO_POSTING)")
    while not (stop and stop.is_set()):
        tick(conn)
        _event.wait(every)
        _event.clear()


def busy() -> bool:
    return status.get("publishing") is not None


# ── The planner's copy (cloud_sync) ───────────────────────────────────────────

_online = False
_checked = 0.0


def online(api) -> bool:
    """Is the posting migration (20261025000000_posting) online? Asked again every 10 minutes."""
    global _online, _checked
    import cloud_sync
    if _online or time.time() - _checked < cloud_sync.SCHEMA_RECHECK_SECONDS:
        return _online
    _checked = time.time()
    try:
        api.request("GET", "/rest/v1/posts", params={"select": "id", "limit": 1})
        _online = True
    except cloud_sync.CloudError as e:
        if not cloud_sync._missing_table(e):
            raise
        log.warning("%s (the planner's posting calendar and results wait for it)", cloud_sync.NEEDS_UPDATE)
    return _online


def _still_path(prod) -> Path | None:
    if prod is None:
        return None
    if prod["format"] == "carousel":
        slides = _slides(prod)
        return slides[0] if slides else None
    return Path(prod["thumb_path"]) if prod["thumb_path"] and Path(prod["thumb_path"]).is_file() else None


def mirror(api, conn, remote_ideas: list[dict] | None = None) -> int:
    """Push changed posts (and a still of each) to the planner. Catalogue wins; returns how many."""
    import hashlib
    import cloud_sync
    kinds = {i["id"]: i.get("post_type") for i in (remote_ideas or []) if i.get("post_type")}
    n = 0
    for row in conn.execute("SELECT * FROM posts ORDER BY id").fetchall():
        post_type = row["post_type"] or kinds.get(row["idea_remote"])
        if post_type and post_type != row["post_type"]:
            conn.execute("UPDATE posts SET post_type = ? WHERE id = ?", (post_type, row["id"]))
            conn.commit()
        values = {"id": row["id"], "production_id": row["production_id"], "idea_id": row["idea_remote"],
                  "kind": row["kind"], "title": (row["title"] or "")[:300], "caption": row["caption"] or "",
                  "post_type": post_type, "version": row["version"], "scheduled_at": row["scheduled_at"],
                  "state": row["state"], "stage": row["stage"], "error": row["error"], "problem": row["problem"],
                  "next_try_at": row["next_try_at"], "permalink": row["permalink"], "media_id": row["media_id"],
                  "posted_at": row["posted_at"], "cover_ms": row["cover_ms"], "share_to_feed": bool(row["share_to_feed"]),
                  "collaborators": json.loads(row["collaborators"] or "[]"), "thumb_path": f"posts/{row['id']}.jpg",
                  "created_at": row["created_at"], "updated_at": row["updated_at"]}
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
        if digest == row["cloud_hash"]:
            continue
        if row["cloud_hash"] is None:  # first time: the still the calendar shows
            still = _still_path(_production(conn, row["production_id"]))
            if still:
                try:
                    api.upload(f"posts/{row['id']}.jpg", cloud_sync._still(still))
                except Exception as e:
                    log.debug("No still for post %s: %s", row["id"], e)
                    values["thumb_path"] = None
            else:
                values["thumb_path"] = None
        try:
            api.insert("posts", values, on_conflict="id", resolution="merge-duplicates", returning=False)
        except cloud_sync.CloudError as e:
            if "23503" in str(e) and values["idea_id"]:  # the idea was deleted online
                values["idea_id"] = None
                api.insert("posts", values, on_conflict="id", resolution="merge-duplicates", returning=False)
            else:
                raise
        conn.execute("UPDATE posts SET cloud_hash = ? WHERE id = ?", (digest, row["id"]))
        conn.commit()
        n += 1
    return n


def info() -> dict:
    """For laptop_status.info (the planner): connected or not, who, any problem. No secrets."""
    st = instagram.status()
    return {k: st.get(k) for k in ("connected", "username", "expired", "problem", "days_left")} | {"posting": settings.POSTING}
