"""
What kinds of posts work, rung 1 of the content analytics (Bryan, 2026-10-27: "understand what kinds of posts
work and tweak as we go"). Studio-only: it reads the catalogue (post_results' daily reads, kept here beyond
Instagram's 90 days, and what Studio knows about each post because it made it) and writes ROOT/insights/.
Analytics are about posts, never people: no client names, addresses or faces go into a finding, the memo or
its prompt (only kinds, formats, formulas and numbers).

  scores()    each post's fair score: at day 7 (the read nearest a week after it went out) and its latest read.
              Per 1,000 accounts reached: saves, shares, follows, profile visits and comments; for Reels how much
              of it people watch (average watch time / its length) and views per account reached. Each against
              the account's own typical post of the same format (the median of the 20 posts of that format
              before it), as an index: 100 = typical, 150 = half as good again. Likes are shown, never judged.
  features()  what Studio knows because it made the post (production -> edit plan / slides / idea): kind of
              post, format, hook formula, length or slides, look, voice-over, picture and drawn inserts, sound
              effects, music mood, first cut, cards, caption length, weekday and hour (Singapore), where the
              topic came from, Bryan's notes and rounds. A post made from the phone gets format and time only.
  findings()  each feature's values against the rest of the posts, small groups pulled toward "no
              difference" (the shrinkage, below), n per group, "proven" or "early signal" by a stated rule.
              One post is never a pattern: a group needs 3 posts to show at all.
  forecast() / grade()   the outside judge: each Monday every post going out in the next 7 days gets an
              expected index (from the same shrunk effects of the features that show a signal, all at once, so
              two that go together aren't counted twice; no signal = 100); once it has its day-7
              numbers the forecast is graded against them, and against the naive guess "it'll be typical (100)".
              The grade says whether the suggestions beat that guess yet. Target: 25% better within 12 weeks.
  weekly()    Monday (STUDIO_INSIGHTS_DAY at STUDIO_INSIGHTS_HOUR, Singapore, before the suggested topics):
              forecasts, grades, then one Claude call (usage kind "insights", ~US$0.10) turns the scores and
              findings into "Do more / Do less / Try next" and "What we can't tell yet", kept in
              ROOT/insights/<date>.json with every earlier one. Its top lines reach topics.py's prompt and,
              as soft guidance (never rules), Studio's own section of the playbook (learning.py).

Keyed by Instagram's media id (and Studio's post id where Studio posted it), so a later lead link (rung 2:
post id or a campaign code) joins the same rows. Nothing here changes a post.
"""
import json
import logging
import math
import re
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone

import settings

log = logging.getLogger("studio.insights")

SGT = timezone(timedelta(hours=8), "SGT")   # Singapore: no summer time
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# ── The scores ────────────────────────────────────────────────────────────────

AGE = 7                 # the fixed age a post is judged at (days after it went out)
AGE_UNTIL = 10          # a read up to this old still counts as "day 7"; later = "read late"
BASE_N = 20             # the typical post: the median of this many of the same format before it
BASE_MIN = 5            # fewer than this before it: the first BASE_N of the format instead
PSEUDO = 2.0            # counts get this many "typical" actions added either side (a post seen by few can't swing wild)
# Per 1,000 reached (counts) and the ratios, with how much each weighs in a post's index. Saves and shares
# are what the posts are for (kept and passed on); follows next; profile visits and comments are softer.
COUNTS = {"saves": 3.0, "shares": 3.0, "follows": 2.0, "profile_visits": 1.0, "comments": 1.0}
RATIOS = {"watch": 2.0, "views_per_reach": 1.0}
METRICS = [*COUNTS, *RATIOS]
LABELS = {"index": "Overall", "saves": "Saves", "shares": "Shares", "follows": "Follows",
          "profile_visits": "Profile visits", "comments": "Comments", "watch": "Watched",
          "views_per_reach": "Views per account reached"}
UNITS = {"saves": "per 1,000 reached", "shares": "per 1,000 reached", "follows": "per 1,000 reached",
         "profile_visits": "per 1,000 reached", "comments": "per 1,000 reached", "watch": "of its length",
         "views_per_reach": "views per account"}
CLIP = (10.0, 1000.0)   # an index is kept inside these

# ── The findings ──────────────────────────────────────────────────────────────
# Small-sample honesty (plain words): a value's average (say, the posts with a "Myth vs fact" hook) is pulled
# toward the average post before it's compared with the rest. How hard depends on what the account's own posts
# say: how much posts with the same value still differ (the noise) and how much the values' true averages
# differ beyond that (the signal). A value with 3 posts is pulled most of the way back; one with 30 keeps most
# of its difference; a feature whose values hardly differ is pulled back whatever its n. This is empirical
# Bayes (a one-way random-effects model per feature, the method of moments); a feature with only two or three
# values borrows part of its signal estimate from the other features, since two groups say little about it.
# Checked on invented accounts (tests/quality/suite_insights.py): a planted effect on 10 posts is proven, the
# same on 3 is an early signal at most, and ten accounts with nothing planted give about one false "proven".
SHOW_MIN = 3            # a group with fewer posts shows its n only, never a difference
PROVEN_N = 8            # "proven": at least this many posts in the group AND in the rest,
PROVEN_Z = 3.0          #   about 99.9% sure of the direction after the pull (some 100 comparisons are made
                        #   each week, so a 1-in-100 fluke would turn up every week),
PROVEN_LIFT = 0.15      #   and at least 15% better or worse
EVENTS_MIN = 20         #   and, for a count, at least 20 of them on each side (0, 1 or 2 follows a post is luck)
EARLY_Z = 1.64          # "early signal": about 95% sure of the direction after the pull,
EARLY_LIFT = 0.10       #   and at least 10%
# The overall index and the metrics a finding can be about (comments, profile visits and views per account
# count in the index but aren't judged alone: fewer comparisons, fewer flukes).
FIND_METRICS = ("saves", "shares", "follows", "watch")
TAU2_MIN = 0.0025       # the smallest "real differences" spread allowed (5%): with nothing real, all shrinks
FORECAST_Z = 2.0        # a feature value the forecast uses: about 98% sure of its direction
BORROW = 3.0            # how much a feature leans on the other features' signal (in values' worth)
TAU2_MAX = 1.0

# ── The judge ─────────────────────────────────────────────────────────────────
FORECAST_DAYS = 7
GRADE_MIN = 4           # graded forecasts before the grade says "beats average"
BEATS_BY = 0.05         # and at least this much better than the naive guess
TARGET = 0.25           # the plan's target: 25% better than average
TARGET_WEEKS = 12
BACKTEST_LAST = 30      # the past posts the backtest predicts (each only from the posts before it)
TRAIN_MIN = 8
MEMO_MIN_POSTS = 5      # fewer scored posts than this: no Claude call, a plain memo says so

# What a phone post (not made by Studio) can be compared on.
PHONE_FEATURES = ("format", "weekday", "hour", "studio")

_lock = threading.Lock()
_ideas_seen = {"at": 0.0}
_tried = {"at": 0.0}       # the last Monday run started (a failing one is tried again an hour later, not every tick)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS insight_ideas (      -- the planner's idea behind a post: its hook and where it came from
    idea_remote  INTEGER PRIMARY KEY,
    hook         TEXT,
    formula      TEXT,                          -- the chosen hook option's formula; '' = written by hand
    source       TEXT,                          -- wiki | footage | basics | question | reference | calendar | plan
    post_type    TEXT,
    seen_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_forecasts (  -- the Monday forecasts and their grades (the outside judge)
    id           INTEGER PRIMARY KEY,
    week         TEXT NOT NULL,                 -- the Monday (Singapore) it was made
    post_id      INTEGER NOT NULL,              -- Studio's post (posts.id); media_id once it's out
    media_id     TEXT,
    format       TEXT,
    scheduled_at TEXT,
    expected     REAL NOT NULL,                 -- the forecast index (100 = typical)
    features     TEXT,                          -- JSON: what it was forecast from
    made_at      TEXT NOT NULL,
    actual       REAL,                          -- its day-7 index, once known
    graded_at    TEXT,
    UNIQUE (week, post_id)
);
"""


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


def _json(text, default):
    try:
        v = json.loads(text) if text else default
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def folder():
    path = settings.ROOT / "insights"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Each post's numbers ───────────────────────────────────────────────────────

def _rates(row, length_s: float | None) -> dict:
    """One read's rates: per 1,000 reached, watch-through and views per account reached."""
    reach = row["reach"] or 0
    out = {}
    if reach > 0:
        for k in COUNTS:
            if row[k] is not None:
                out[k] = row[k] * 1000.0 / reach
        if row["views"] is not None:
            out["views_per_reach"] = row["views"] / reach
    if row["avg_watch_ms"] and length_s and length_s > 0:
        out["watch"] = min(row["avg_watch_ms"] / 1000.0 / length_s, 3.0)
    return out


def _read(row, length_s, age: int, late: bool = False) -> dict:
    return {"day": row["day"], "age": age, "late": late, "reach": row["reach"], "views": row["views"],
            "likes": row["likes"], "counts": {k: row[k] for k in COUNTS}, "rates": _rates(row, length_s)}


def _lengths(conn) -> dict:
    """{post id: a Reel's length in seconds} for Studio's posts (watch-through needs it)."""
    return {r["id"]: r["duration"] for r in conn.execute(
        "SELECT p.id, d.duration FROM posts p JOIN productions d ON d.id = p.production_id WHERE d.duration > 0")}


def media(conn) -> list[dict]:
    """Every post Studio has numbers for, oldest first, with its day-7 and latest reads."""
    import post_results
    lengths = _lengths(conn)
    out = []
    rows = conn.execute("""SELECT m.*, p.kind AS post_kind, p.production_id, p.idea_remote, p.post_type,
                                  p.caption AS post_caption, p.title AS post_title, d.format AS prod_format
                           FROM ig_media m LEFT JOIN posts p ON p.id = m.post_id
                           LEFT JOIN productions d ON d.id = p.production_id
                           WHERE m.posted_at IS NOT NULL ORDER BY m.posted_at, m.media_id""").fetchall()
    for m in rows:
        posted = _parse(m["posted_at"])
        if posted is None:
            continue
        reads = conn.execute("SELECT * FROM post_results WHERE media_id = ? ORDER BY day", (m["media_id"],)).fetchall()
        if not reads:
            continue
        sg = posted.astimezone(SGT).date()
        length = lengths.get(m["post_id"])
        day7 = None
        for r in reads:
            age = (date.fromisoformat(r["day"]) - sg).days
            if AGE <= age <= AGE_UNTIL:
                day7 = _read(r, length, age)
                break
            if age > AGE_UNTIL:  # first read after day 10 (a phone post from before Studio read results)
                day7 = _read(r, length, age, late=True)
                break
        last = reads[-1]
        kind = fmt_of(m, post_results)
        out.append({"media_id": m["media_id"], "post_id": m["post_id"], "production_id": m["production_id"],
                    "idea_remote": m["idea_remote"], "post_type": m["post_type"], "format": kind,
                    "base_format": "reel" if kind == "slideshow" else kind,   # a slideshow is judged among the Reels
                    "by_studio": m["post_id"] is not None, "posted_at": m["posted_at"], "posted": posted,
                    "title": (m["post_title"] or (m["caption"] or "").split("\n")[0])[:120], "permalink": m["permalink"],
                    "caption_len": len(m["post_caption"] or m["caption"] or ""), "length_s": length,
                    "day7": day7, "latest": _read(last, length, (date.fromisoformat(last["day"]) - sg).days)})
    return out


def fmt_of(m, post_results) -> str:
    """reel | carousel | slideshow | image: Studio's own word for its posts, Instagram's for the rest."""
    if m["production_id"]:
        if m["prod_format"] == "slideshow":
            return "slideshow"
        return m["post_kind"] or post_results.fmt({"media_type": m["media_type"], "media_product_type": m["product_type"]})
    return post_results.fmt({"media_type": m["media_type"], "media_product_type": m["product_type"]})


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _baseline(prior: list[dict], metric: str) -> float | None:
    vals = [p["day7"]["rates"][metric] for p in prior if metric in p["day7"]["rates"]]
    if len(vals) < 3:
        return None
    return _median(vals)


def _index_of(read: dict, base: dict) -> dict:
    """Per-metric indices of one read against a baseline {metric: rate}, and the overall index."""
    per = {}
    for k, w in COUNTS.items():
        b = base.get(k)
        n = read["counts"].get(k)
        reach = read["reach"] or 0
        if b is None or n is None or reach <= 0:
            continue
        expected = b * reach / 1000.0
        per[k] = min(max(100.0 * (n + PSEUDO) / (expected + PSEUDO), CLIP[0]), CLIP[1])
    for k in RATIOS:
        b, v = base.get(k), read["rates"].get(k)
        if b and v is not None:
            per[k] = min(max(100.0 * v / b, CLIP[0]), CLIP[1])
    weights = {**COUNTS, **RATIOS}
    total = sum(weights[k] for k in per)
    overall = None
    if len(per) >= 2 and total >= 3:
        overall = 100.0 * math.exp(sum(weights[k] * math.log(v / 100.0) for k, v in per.items()) / total)
    return {"index": overall, "per": per}


def _prior(items: list[dict], i: int, same) -> tuple[list[dict], bool]:
    """The posts a post is compared with: the BASE_N before it that `same` accepts (with day-7 reads), or,
    with fewer than BASE_MIN of those, the first BASE_N of them (an early baseline, said so)."""
    me = items[i]
    before = [p for p in items[:i] if p["day7"] and same(p) and p["posted"] < me["posted"]][-BASE_N:]
    if len(before) >= BASE_MIN:
        return before, False
    return [p for p in items if p["day7"] and same(p) and p is not me][:BASE_N], True


def scores(conn, now: datetime | None = None) -> list[dict]:
    """Every post with its day-7 index (vs the same format, and vs every format for the format comparison)
    and its latest read's index. Oldest first."""
    items = media(conn)
    for i, p in enumerate(items):
        p["score"] = p["score_all"] = p["score_latest"] = None
        p["early_base"] = False
        prior, early = _prior(items, i, lambda q, f=p["base_format"]: q["base_format"] == f)
        if len(prior) < 3:
            prior, early = _prior(items, i, lambda q: True)
        base = {k: _baseline(prior, k) for k in METRICS}
        allp, _ = _prior(items, i, lambda q: True)
        base_all = {k: _baseline(allp, k) for k in METRICS}
        p["early_base"] = early
        if p["day7"]:
            p["score"] = _index_of(p["day7"], base)
            p["score_all"] = _index_of(p["day7"], base_all)
        p["score_latest"] = _index_of(p["latest"], base)
    return items


def baselines(items: list[dict]) -> dict:
    """The typical post of each format now (the median of its last BASE_N with day-7 reads), for the page."""
    out = {}
    for f in sorted({p["base_format"] for p in items}):
        done = [p for p in items if p["base_format"] == f and p["day7"]][-BASE_N:]
        out[f] = {"n": len(done), **{k: _baseline(done, k) for k in METRICS}}
    return out


# ── What Studio knows about each post ─────────────────────────────────────────

FEATURES = {   # key: label (the order the page shows them in)
    "post_type": "Kind of post", "format": "Format", "hook": "Hook formula", "length": "Length",
    "slides": "Slides", "look": "Look", "voiceover": "Voice-over", "pictures": "Picture inserts",
    "drawings": "Drawn inserts", "sfx": "Sound effects", "music": "Music mood", "first_cut": "First cut",
    "cards": "Cards and labels", "caption": "Caption length", "weekday": "Day posted", "hour": "Time posted",
    "source": "Where the topic came from", "notes": "Bryan's notes", "rounds": "Drafts before approval",
    "studio": "Made by",
}
KIND_LABEL = {"knowhow": "Know-how", "ideas": "Ideas", "onsite": "On site", "finished": "Finished homes",
              "offer": "Work with us"}
FORMAT_LABEL = {"reel": "Reel", "carousel": "Carousel", "slideshow": "Slideshow Reel", "image": "Photo"}
SOURCE_LABEL = {"wiki": "The wiki", "footage": "Bryan's footage", "basics": "Renovation basics",
                "question": "A client question", "reference": "References", "calendar": "The shoot calendar",
                "plan": "Planned by the team"}


def _band(v, edges: list[tuple[float, str]], last: str) -> str:
    for edge, label in edges:
        if v < edge:
            return label
    return last


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def formula_of(hook: str, options) -> str | None:
    """The formula of the hook option that is the idea's hook; '' when the hook was written by hand, None
    when there were no options."""
    options = [o for o in (options or []) if isinstance(o, dict) and o.get("text")]
    if not options:
        return None
    h = _norm(hook)
    for o in options:
        if h and _norm(o.get("text")) == h:
            return str(o.get("formula") or "").strip()[:80]
    return ""


def remember_ideas(conn, remote_ideas: list[dict] | None) -> int:
    """Keep the hook, its formula and the topic's source of every planner idea that has a post (from the
    sync's own read of the ideas, no extra request). Returns rows changed."""
    if not remote_ideas:
        return 0
    ensure(conn)
    wanted = {r[0] for r in conn.execute("SELECT DISTINCT idea_remote FROM posts WHERE idea_remote IS NOT NULL")}
    n = 0
    stamp = _iso(_now())
    for i in remote_ideas:
        if i.get("id") not in wanted:
            continue
        sugg = i.get("suggestion") if isinstance(i.get("suggestion"), dict) else {}
        source = {"transcript": "footage"}.get(sugg.get("source"), sugg.get("source")) or None
        values = (i.get("hook"), formula_of(i.get("hook"), i.get("hook_options")), source, i.get("post_type"))
        old = conn.execute("SELECT hook, formula, source, post_type FROM insight_ideas WHERE idea_remote = ?",
                           (i["id"],)).fetchone()
        if old and tuple(old) == values:
            continue
        conn.execute("""INSERT INTO insight_ideas (idea_remote, hook, formula, source, post_type, seen_at)
                        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (idea_remote) DO UPDATE SET hook = excluded.hook,
                        formula = excluded.formula, source = COALESCE(excluded.source, insight_ideas.source),
                        post_type = excluded.post_type, seen_at = excluded.seen_at""", (i["id"], *values, stamp))
        n += 1
    conn.commit()
    return n


def _footage_ideas(conn) -> set:
    """Planner ideas that "ideas from new footage" proposed (idea_runs), so their topic came from footage."""
    out = set()
    for r in conn.execute("SELECT trigger, result FROM idea_runs WHERE state = 'done' AND place = 'cloud' "
                          "AND trigger NOT LIKE 'topics:%'"):
        out.update(i for i in _json(r["result"], {}).get("new_ideas", []) if isinstance(i, int))
    return out


def _topic_sources(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT result FROM idea_runs WHERE state = 'done' AND trigger LIKE 'topics:%'"):
        for s in _json(r["result"], {}).get("suggestions", []):
            if isinstance(s, dict) and s.get("id"):
                out[s["id"]] = {"transcript": "footage"}.get(s.get("source"), s.get("source"))
    return out


def _when_features(t: datetime) -> dict:
    s = t.astimezone(SGT)
    return {"weekday": WEEKDAYS[s.weekday()],
            "hour": _band(s.hour, [(12, "Morning (before 12)"), (17, "Afternoon (12 to 5pm)"),
                                   (21, "Evening (5 to 9pm)")], "Late (after 9pm)"),
            "hour_exact": s.hour}


def features_for(conn, item: dict, ctx: dict | None = None) -> dict:
    """The feature row of one post (values are the page's own words; None = not known)."""
    ctx = ctx if ctx is not None else _context(conn)
    row = {k: None for k in FEATURES}
    row.update(format=FORMAT_LABEL.get(item["format"], item["format"]), studio="Studio" if item["by_studio"] else "Phone",
               **_when_features(item["posted"]))
    if not item.get("production_id"):
        return row
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (item["production_id"],)).fetchone()
    if prod is None:
        return row
    idea = ctx["ideas"].get(item.get("idea_remote")) or {}
    local = conn.execute("SELECT post_type FROM ideas WHERE production_id = ?", (prod["id"],)).fetchone()
    kind = item.get("post_type") or idea.get("post_type") or (local["post_type"] if local else None)
    row["post_type"] = KIND_LABEL.get(kind) if kind else None
    formula = idea.get("formula")
    row["hook"] = (formula or "Written by hand") if formula is not None else None
    remote = item.get("idea_remote")
    source = idea.get("source") or ctx["topics"].get(remote) or ("footage" if remote in ctx["footage"] else None)
    if not source and remote:
        source = "plan"
    row["source"] = SOURCE_LABEL.get(source, source) if source else None
    notes = conn.execute("SELECT COUNT(*) FROM feedback WHERE target = 'production' AND target_id = ?",
                         (prod["id"],)).fetchone()[0]
    row["notes_n"] = notes
    row["notes"] = _band(notes, [(1, "None"), (4, "1 to 3")], "4 or more")
    row["rounds_n"] = prod["version"] or 1
    row["rounds"] = _band(row["rounds_n"], [(2, "First draft"), (3, "Two drafts")], "Three or more")
    row["caption"] = _band(item["caption_len"], [(126, "One line (under 125 characters)"),
                                                 (501, "A paragraph or two")], "Long (over 500)")
    if item["format"] == "carousel":
        slides = _json(prod["slides"], {}).get("slides") or []
        row["slides_n"] = len(slides) or None
        if slides:
            row["slides"] = _band(len(slides), [(5, "2 to 4"), (8, "5 to 7")], "8 or more")
        return row
    length = prod["duration"]
    if length:
        row["length_s"] = round(length, 1)
        row["length"] = _band(length, [(20, "Under 20 s"), (40, "20 to 40 s"), (60, "40 to 60 s")], "Over 60 s")
    edl = _json(prod["edl"], {})
    if not edl:
        return row
    look = (edl.get("look") or {}).get("titles") or "classic"
    row["look"] = {"site": "Site Notes", "gallery": "Gallery", "classic": "Classic"}.get(look, look)
    row["voiceover"] = "Yes" if (edl.get("voiceover") or {}).get("on") else "No"
    graphics = [g for g in edl.get("graphics") or [] if isinstance(g, dict)]
    pics = sum(1 for g in graphics if g.get("style") == "picture")
    drawn = sum(1 for g in graphics if g.get("style") == "scene")
    cards = sum(1 for g in graphics if g.get("style") in ("card", "callout"))
    row.update(pictures_n=pics, drawings_n=drawn, cards_n=cards,
               pictures=_band(pics, [(1, "None"), (3, "1 or 2")], "3 or more"),
               drawings=_band(drawn, [(1, "None"), (2, "One")], "2 or more"),
               cards=_band(cards, [(1, "None"), (3, "1 or 2")], "3 or more"))
    sfx = edl.get("sfx")
    row["sfx"] = ("On" if sfx.get("on") and sfx.get("placed") else "Off") if isinstance(sfx, dict) else "Off"
    music = _json(prod["music"], {})
    mood = str((edl.get("music") or {}).get("mood") or music.get("mood") or "").strip().lower()
    row["music"] = mood.split(",")[0].split(" and ")[0].strip()[:30].capitalize() or None
    row["music_track"] = str(music.get("title") or "")[:80] or None   # shown with the post, never grouped
    cuts = [c for c in edl.get("cuts") or [] if isinstance(c, dict)]
    if cuts:
        first = max(0.0, float(cuts[0].get("end", 0)) - float(cuts[0].get("start", 0)))
        row["first_cut_s"] = round(first, 1)
        row["first_cut"] = _band(first, [(2, "Under 2 s"), (4, "2 to 4 s")], "Over 4 s")
    return row


def _context(conn) -> dict:
    ensure(conn)
    ideas = {r["idea_remote"]: dict(r) for r in conn.execute("SELECT * FROM insight_ideas")}
    return {"ideas": ideas, "topics": _topic_sources(conn), "footage": _footage_ideas(conn)}


def table(conn, now: datetime | None = None) -> list[dict]:
    """Every post: its scores and its feature row. Oldest first."""
    items = scores(conn, now)
    ctx = _context(conn)
    for p in items:
        p["features"] = features_for(conn, p, ctx)
    return items


# ── The findings ──────────────────────────────────────────────────────────────

def _y(p: dict, metric: str, feature: str | None = None) -> float | None:
    """A post's log index for a metric (0 = typical). The format comparison uses the index against every
    format (against its own format, every format is typical by definition)."""
    s = p.get("score_all") if feature == "format" else p.get("score")
    if not s:
        return None
    v = s["index"] if metric == "index" else s["per"].get(metric)
    return math.log(v / 100.0) if v else None


def _avg(values) -> float:
    values = list(values)
    return math.fsum(values) / len(values)


def _var(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _avg(values)
    return math.fsum((v - m) ** 2 for v in values) / (len(values) - 1)


def _groups(items: list[dict], metric: str, feature: str) -> dict:
    """{value: [log index]} of the posts where this feature is known."""
    out: dict = {}
    for p in items:
        v = p["features"].get(feature)
        y = _y(p, metric, feature) if v is not None else None
        if y is not None:
            out.setdefault(v, []).append(y)
    return out


def spread(groups: dict) -> dict:
    """One feature's noise and signal, from the account's own posts (a one-way random-effects model, the
    method of moments): s2 = how much posts with the same value still differ (the noise), tau2 = how much the
    values' true averages differ beyond that (the signal), mu = the average post."""
    ys = [y for g in groups.values() for y in g]
    n = len(ys)
    if n < 2:
        return {"mu": ys[0] if ys else 0.0, "s2": 0.0, "raw": 0.0, "dof": 0}
    mu = _avg(ys)
    means = {v: _avg(g) for v, g in groups.items()}
    dof = sum(len(g) - 1 for g in groups.values())
    s2 = (sum((y - means[v]) ** 2 for v, g in groups.items() for y in g) / dof) if dof > 0 else _var(ys)
    s2 = s2 or _var(ys) or 0.05
    k = len(groups)
    between = sum(len(g) * (means[v] - mu) ** 2 for v, g in groups.items())
    scale = n - sum(len(g) ** 2 for g in groups.values()) / n
    tau2 = (between - (k - 1) * s2) / scale if k > 1 and scale > 0 else 0.0
    return {"mu": mu, "s2": s2, "raw": tau2, "dof": k - 1}


def label(n: int, n_rest: int, z: float, lift: float, events: int | None = None) -> str:
    """The stated rule: proven | early | none | few. events: for a count (saves, shares, follows), how many
    of them the fewer side had (the group's posts or the rest's)."""
    if n < SHOW_MIN or n_rest < SHOW_MIN:
        return "few"
    if (n >= PROVEN_N and n_rest >= PROVEN_N and abs(z) >= PROVEN_Z and abs(lift) >= PROVEN_LIFT
            and (events is None or events >= EVENTS_MIN)):
        return "proven"
    if abs(z) >= EARLY_Z and abs(lift) >= EARLY_LIFT:
        return "early"
    return "none"


def metric_findings(items: list[dict], metric: str, skip: tuple = ()) -> dict:
    """{features: {feature: {mu, s2, tau2}}, rows} for one metric. Each value's average is pulled toward the
    average post by kept = tau2 / (tau2 + s2 / n) (empirical Bayes: a value with few posts, or a feature whose
    values hardly differ, is pulled most of the way back), then compared with the rest, pulled the same way."""
    rows, feats, all_groups = [], {}, {}
    for f in FEATURES:
        groups = _groups(items, metric, f) if f not in skip else {}
        if groups:
            all_groups[f] = groups
            feats[f] = spread(groups)
    # A feature with two or three values says little about its own signal: it borrows from the others (the
    # account's typical signal over every feature), as if they were BORROW more values of its own.
    pooled = _avg([max(sp["raw"], 0.0) for sp in feats.values()]) if feats else 0.0
    for sp in feats.values():
        own = max(sp["raw"], 0.0)
        sp["tau2"] = min(max((sp["dof"] * own + BORROW * pooled) / (sp["dof"] + BORROW), TAU2_MIN), TAU2_MAX)
    for f, groups in all_groups.items():
        sp = feats[f]
        mu, s2, tau2 = sp["mu"], sp["s2"], sp["tau2"]
        theta, kept = {}, {}
        for v, g in groups.items():
            kept[v] = tau2 / (tau2 + s2 / len(g))
            theta[v] = mu + kept[v] * (_avg(g) - mu)
        total = sum(len(g) for g in groups.values())
        for v, g in sorted(groups.items()):
            n, n_rest = len(g), total - len(g)
            base = {"feature": f, "value": v, "metric": metric, "n": n, "n_rest": n_rest}
            if n < SHOW_MIN or n_rest < SHOW_MIN:   # never one post's result (nor two)
                rows.append({**base, "lift": None, "raw": None, "z": 0.0, "label": "few"})
                continue
            others = [(w, len(h)) for w, h in groups.items() if w != v]
            rest = sum(theta[w] * m for w, m in others) / n_rest
            raw_rest = sum(sum(h) for w, h in groups.items() if w != v) / n_rest
            var = kept[v] * s2 / n + sum(kept[w] * s2 / m * (m / n_rest) ** 2 for w, m in others)
            d = theta[v] - rest
            z = d / math.sqrt(var) if var > 0 else 0.0
            lift = math.exp(d) - 1
            # The fewer side's count of saves / shares / follows (0, 1 or 2 a post is luck, however many posts).
            events = (min(sum(p["day7"]["counts"].get(metric) or 0 for p in items if p.get("day7") and p["features"].get(f) == v),
                          sum(p["day7"]["counts"].get(metric) or 0 for p in items
                              if p.get("day7") and p["features"].get(f) not in (None, v))) if metric in COUNTS else None)
            rows.append({**base, "lift": round(lift, 4), "raw": round(math.exp(_avg(g) - raw_rest) - 1, 4),
                         "kept": round(kept[v], 3), "z": round(z, 3), "label": label(n, n_rest, z, lift, events),
                         **({"events": events} if events is not None else {})})
    return {"features": feats, "rows": rows}


def findings(items: list[dict]) -> dict:
    """{metric: {tau2, rows}} for the overall index and each metric. A finding: feature, value, n, n_rest,
    lift (shrunk, e.g. 0.38 = 38% better than the rest), raw lift, kept (the share of the difference kept
    after the pull), z (after the pull) and its label."""
    return {metric: metric_findings(items, metric) for metric in ["index", *FIND_METRICS]}


def _public(c: dict) -> dict:
    return {k: c[k] for k in ("feature", "value", "metric", "n", "n_rest")}


def headline_findings(found: dict, limit: int = 40) -> list[dict]:
    """One line per feature value: its overall finding, or a metric's when that one says more (a stronger
    label first, then the bigger z). Proven and early signals first."""
    rank = {"proven": 3, "early": 2, "none": 1, "few": 0}
    best = {}
    for metric, data in found.items():
        for r in data["rows"]:
            key = (r["feature"], r["value"])
            score = (rank[r["label"]], abs(r["z"]) + (0.2 if metric == "index" else 0))
            if key not in best or score > best[key][0]:
                best[key] = (score, r)
    rows = [r for _, r in best.values()]
    order = list(FEATURES)
    rows.sort(key=lambda r: (-rank[r["label"]], order.index(r["feature"]), -abs(r["z"])))
    return rows[:limit] if limit else rows


# ── Forecasts (the features that show a signal, all at once: ridge regression, the same pull) ──

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """a x = b (Gauss-Jordan with partial pivoting; a is small and symmetric positive definite)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                row_c, row_r = m[c], m[r]
                for k in range(c, n + 1):
                    row_r[k] -= f * row_c[k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit(items: list[dict]) -> dict | None:
    """The forecast model: log index ~ the feature values whose overall finding is about 98% sure of its
    direction (FORECAST_Z), all at once (so two that go together aren't counted twice), each pulled toward 0 as hard as the findings
    pull (ridge, lambda = noise / signal). With no signal it forecasts the account's typical post. None with too
    few posts. (Every feature value at once overfitted: on numbers with nothing in them it did worse than
    "every post is typical".)"""
    train = [p for p in items if _y(p, "index") is not None]
    if len(train) < TRAIN_MIN:
        return None
    found = metric_findings(train, "index", skip=("format",))
    cols = sorted((r["feature"], r["value"]) for r in found["rows"] if abs(r["z"]) >= FORECAST_Z and r["label"] != "few")
    ys = [_y(p, "index") for p in train]
    mean_y = _avg(ys)
    # Each column pulled as hard as its feature's findings: lambda = noise / signal (a generalised ridge).
    lams = [min(max(found["features"][f]["s2"] / found["features"][f]["tau2"], 1.0), 400.0) for f, _ in cols]
    xs = [[1.0 if p["features"].get(f) == v else 0.0 for f, v in cols] for p in train]
    means = [_avg(x[j] for x in xs) for j in range(len(cols))] if cols else []
    xc = [[x[j] - means[j] for j in range(len(cols))] for x in xs]
    yc = [y - mean_y for y in ys]
    k = len(cols)
    a = [[sum(r[i] * r[j] for r in xc) + (lams[i] if i == j else 0.0) for j in range(k)] for i in range(k)]
    rhs = [sum(r[i] * yy for r, yy in zip(xc, yc)) for i in range(k)]
    beta = _solve(a, rhs) if k else []
    return {"cols": cols, "means": means, "beta": beta, "mean_y": mean_y, "lambdas": lams, "n": len(train)}


def predict(model: dict | None, feats: dict) -> float:
    """The expected index (100 = typical) of a post with these features."""
    if not model:
        return 100.0
    # Centred on the typical post (100), not on the training posts' average: by definition the typical post is
    # 100, and an estimated average only added noise (it did worse than "typical" on numbers with nothing in them).
    y = sum(b * ((1.0 if feats.get(f) == v else 0.0) - m)
            for (f, v), m, b in zip(model["cols"], model["means"], model["beta"]))
    return round(min(max(100.0 * math.exp(y), 25.0), 400.0), 1)


def backtest(items: list[dict]) -> dict:
    """The judge on past posts: each of the last BACKTEST_LAST posts predicted only from the posts whose day 7
    had passed when it went out, against the naive "typical (100)". Mean absolute error in index points."""
    scored = [p for p in items if p.get("score") and p["score"]["index"]]
    errs, naive = [], []
    for p in scored[-BACKTEST_LAST:]:
        train = [q for q in scored if q["posted"] <= p["posted"] - timedelta(days=AGE)]
        if len(train) < TRAIN_MIN:
            continue
        guess = predict(fit(train), p["features"])
        actual = p["score"]["index"]
        errs.append(abs(actual - guess))
        naive.append(abs(actual - 100.0))
    return _grade_numbers(errs, naive)


def _grade_numbers(errs: list[float], naive: list[float]) -> dict:
    if not errs:
        return {"n": 0, "mae": None, "mae_naive": None, "better": None}
    mae, mae_naive = _avg(errs), _avg(naive)
    return {"n": len(errs), "mae": round(mae, 1), "mae_naive": round(mae_naive, 1),
            "better": round(1 - mae / mae_naive, 3) if mae_naive > 0 else None}


def _week(now: datetime | None = None) -> str:
    local = (now or _now()).astimezone(SGT)
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def forecast(conn, items: list[dict], now: datetime | None = None) -> list[dict]:
    """Forecast every Studio post going out in the next FORECAST_DAYS (scheduled or going out now), once a
    week. Returns the forecasts made."""
    ensure(conn)
    now = now or _now()
    week = _week(now)
    model = fit(items)
    ctx = _context(conn)
    made = []
    rows = conn.execute("""SELECT p.*, d.duration, d.format AS prod_format FROM posts p LEFT JOIN productions d ON d.id = p.production_id
                           WHERE p.state IN ('scheduled', 'publishing') AND p.scheduled_at <= ?""",
                        (_iso(now + timedelta(days=FORECAST_DAYS)),)).fetchall()
    for r in rows:
        at = _parse(r["scheduled_at"])
        if at is None:
            continue
        kind = "slideshow" if r["prod_format"] == "slideshow" else r["kind"]
        item = {"media_id": None, "post_id": r["id"], "production_id": r["production_id"], "idea_remote": r["idea_remote"],
                "post_type": r["post_type"], "format": kind, "by_studio": True, "posted": at,
                "caption_len": len(r["caption"] or "")}
        feats = features_for(conn, item, ctx)
        expected = predict(model, feats)
        cur = conn.execute("""INSERT INTO insight_forecasts (week, post_id, format, scheduled_at, expected, features, made_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (week, post_id) DO NOTHING""",
                           (week, r["id"], kind, r["scheduled_at"], expected,
                            json.dumps({k: v for k, v in feats.items() if k in FEATURES}), _iso(now)))
        if cur.rowcount:
            made.append({"post_id": r["id"], "format": kind, "expected": expected, "scheduled_at": r["scheduled_at"]})
    conn.commit()
    return made


def grade(conn, items: list[dict], now: datetime | None = None) -> dict:
    """Grade past forecasts whose posts have their day-7 numbers; the judge's numbers over all graded ones."""
    ensure(conn)
    now = now or _now()
    by_post = {p["post_id"]: p for p in items if p["post_id"] is not None}
    for f in conn.execute("SELECT * FROM insight_forecasts WHERE actual IS NULL").fetchall():
        p = by_post.get(f["post_id"])
        if p and p.get("score") and p["score"]["index"]:
            conn.execute("UPDATE insight_forecasts SET actual = ?, media_id = ?, graded_at = ? WHERE id = ?",
                         (round(p["score"]["index"], 1), p["media_id"], _iso(now), f["id"]))
    conn.commit()
    done = conn.execute("SELECT expected, actual FROM insight_forecasts WHERE actual IS NOT NULL").fetchall()
    out = _grade_numbers([abs(r["actual"] - r["expected"]) for r in done], [abs(r["actual"] - 100.0) for r in done])
    first = conn.execute("SELECT MIN(week) FROM insight_forecasts").fetchone()[0]
    out["waiting"] = conn.execute("SELECT COUNT(*) FROM insight_forecasts WHERE actual IS NULL").fetchone()[0]
    out["started"] = first
    out["target_by"] = (date.fromisoformat(first) + timedelta(weeks=TARGET_WEEKS)).isoformat() if first else None
    return out


def grade_line(live: dict, past: dict | None = None) -> dict:
    """The line at the top of the memo and the page: {ok, text}."""
    target = f"Target: {round(TARGET * 100)}% better than average" + (
        f" by {_day_text(live['target_by'])}" if live.get("target_by") else f" within {TARGET_WEEKS} weeks")
    past_text = ""
    if past and past.get("n"):
        word = "beat" if (past.get("better") or 0) > 0 else "did not beat"
        past_text = (f" On past posts (each predicted only from the posts before it) it {word} average by "
                     f"{abs(round((past['better'] or 0) * 100))}% (n={past['n']}).")
    if live.get("n", 0) >= GRADE_MIN and (live.get("better") or 0) >= BEATS_BY:
        pct = round(live["better"] * 100)
        return {"ok": True, "text": f"Beats average by {pct}%: off by {live['mae']} points a post against "
                                    f"{live['mae_naive']} for \"every post is typical\" (n={live['n']} forecasts). "
                                    f"{target}." + past_text}
    if live.get("n", 0) >= GRADE_MIN:
        return {"ok": False, "text": f"Not better than average yet: treat suggestions as hunches. Off by {live['mae']} "
                                     f"points a post against {live['mae_naive']} for \"every post is typical\" "
                                     f"(n={live['n']} forecasts). {target}." + past_text}
    waiting = live.get("waiting") or 0
    return {"ok": False, "text": f"Not better than average yet: treat suggestions as hunches. {live.get('n', 0)} "
                                 f"forecasts graded so far ({waiting} waiting for their day-7 numbers; it needs "
                                 f"{GRADE_MIN}). {target}." + past_text}


def _day_text(iso: str | None) -> str:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{d.day} {d:%b %Y}"


# ── The memo ──────────────────────────────────────────────────────────────────

_SYSTEM = """You write Studio's Monday memo for Bryan (Urban Makers, a Singapore renovation company): what kinds \
of Instagram posts work for his account, from the numbers Studio measured. Bryan is not technical: plain English, \
short bullets, the numbers inline.

How the numbers work (say them this way):
- A post's index compares it with the account's own typical post of the same format: 100 = typical, 150 = half \
as good again, 70 = 30% worse. It is judged at day 7 on saves, shares, follows, profile visits and comments per \
1,000 accounts reached, and for Reels how much of each Reel people watch. Likes are never used.
- A finding compares the posts with one feature (say, a hook formula) with the rest. "lift" is after Studio \
pulled small groups toward no difference. n is how many posts are in the group, n_rest the rest.
- Labels: "proven" = at least 8 posts on each side and very likely real; "early" = a hunch worth testing; \
"none" = no clear difference; "few" = fewer than 3 posts, so nothing can be said.

Write:
- headline: one sentence, the single most useful thing this week.
- do_more, do_less, try_next: 3 to 5 bullets in total across the three (try_next gets at least one). Each bullet \
cites its numbers and n, like "Carousels with a Myth vs fact hook: saves 38% above the rest (n=9 vs 31, proven)". \
strength: "proven" only for a proven finding; "early" for an early signal (say "early signal" in the text); "idea" \
for a test to run (try_next), which names what to post and why the numbers can't tell yet.
- cant_tell: 1 to 3 bullets: what the numbers can't answer yet and how many more posts would help.

Rules:
- Never present a single post's result as a pattern, and never call an early signal proven.
- When the forecast grade says "not better than average yet", say the suggestions are hunches.
- Never invent a number: use only the numbers below.
- Posts are about kinds of post, never people: no names, addresses, estates or faces.
- Bryan's playbook and his calls stand: this memo suggests, it never sets rules.

{voice}"""


def _schema() -> dict:
    item = {"type": "object", "properties": {"text": {"type": "string"},
                                             "strength": {"type": "string", "enum": ["proven", "early", "idea"]}},
            "required": ["text", "strength"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "headline": {"type": "string"},
        "do_more": {"type": "array", "items": item}, "do_less": {"type": "array", "items": item},
        "try_next": {"type": "array", "items": item}, "cant_tell": {"type": "array", "items": {"type": "string"}}},
        "required": ["headline", "do_more", "do_less", "try_next", "cant_tell"], "additionalProperties": False}


def _pct(lift) -> str:
    if lift is None:
        return "-"
    return f"{'+' if lift >= 0 else ''}{round(lift * 100)}%"


def _num(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def _fval(feature: str, value: str) -> str:
    return f"{FEATURES[feature]}: {value}"


def followers(conn, days: int = 120) -> list[dict]:
    rows = conn.execute("SELECT day, followers FROM account_results WHERE followers IS NOT NULL ORDER BY day DESC LIMIT ?",
                        (days,)).fetchall()
    return [{"day": r["day"], "followers": r["followers"]} for r in reversed(rows)]


def _follower_text(trend: list[dict]) -> str:
    if len(trend) < 2:
        return "(not enough days read yet)"
    last = trend[-1]
    parts = [f"{last['followers']} followers on {last['day']}"]
    for span in (7, 30, 90):
        cut = (date.fromisoformat(last["day"]) - timedelta(days=span)).isoformat()
        old = [t for t in trend if t["day"] <= cut]
        if old:
            parts.append(f"{last['followers'] - old[-1]['followers']:+d} in {span} days")
    return ", ".join(parts)


def prompt(data: dict) -> str:
    """The memo's message: the grade, the baselines, the findings with n, the posts' scores (by number, no
    titles or captions), the followers and this week's forecasts."""
    items, found = data["items"], data["headline"]
    base_lines = []
    for f, b in data["baselines"].items():
        rates = ", ".join(f"{LABELS[k].lower()} {_num(b.get(k), 2 if k in RATIOS else 1)} {UNITS[k]}"
                          for k in METRICS if b.get(k) is not None)
        base_lines.append(f"- {FORMAT_LABEL.get(f, f)} (median of its last {b['n']}): {rates or 'not enough reads'}")
    finding_lines = [f"- {_fval(r['feature'], r['value'])} | {LABELS[r['metric']].lower()} | lift {_pct(r['lift'])} "
                     f"(raw {_pct(r['raw'])}) | n={r['n']} vs {r['n_rest']} | {r['label']}" for r in found]
    scored = [p for p in items if p.get("score") and p["score"]["index"]]
    post_lines = []
    for i, p in enumerate(scored[-40:], 1):
        s = p["score"]
        feats = "; ".join(f"{FEATURES[k].lower()} {v}" for k, v in p["features"].items()
                          if k in FEATURES and v is not None and k not in ("studio",))
        post_lines.append(f"- post {i}: index {s['index']:.0f} ({', '.join(f'{LABELS[k].lower()} {v:.0f}' for k, v in s['per'].items())})"
                          f"{' [read late]' if p['day7']['late'] else ''} | {feats}")
    fc = [f"- a {FORMAT_LABEL.get(f['format'], f['format'])} on {_day_text(str(f['scheduled_at'])[:10])}: expected index "
          f"{f['expected']:.0f}" for f in data.get("forecasts") or []]
    return (f"Today: {data['today']} (Singapore).\n\n"
            f"Forecast grade (the outside judge): {data['grade']['text']}\n\n"
            f"Posts with day-7 scores: {len(scored)} ({sum(1 for p in scored if p['by_studio'])} made by Studio).\n\n"
            "The typical post of each format:\n" + ("\n".join(base_lines) or "(none yet)") + "\n\n"
            "Findings (each feature value against the rest; strongest first):\n" + ("\n".join(finding_lines) or "(none)")
            + "\n\nEvery scored post (oldest first; numbers only):\n" + ("\n".join(post_lines) or "(none)")
            + f"\n\nFollowers: {_follower_text(data['followers'])}\n\n"
            f"This week's forecasts:\n" + ("\n".join(fc) or "(nothing scheduled in the next 7 days)"))


def system_text() -> str:
    import wording
    return _SYSTEM.replace("{voice}", wording.guide())


def _ask(user: str, ref: str, client=None) -> tuple[dict, float]:
    """One structured Claude call; its cost is recorded as "insights"."""
    import anthropic
    import llm
    import usage
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.INSIGHTS_MODEL, max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _schema()}},
        system=system_text(), messages=[{"role": "user", "content": user}])
    usd = usage.record("insights", ref, response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write the memo this time")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's memo was cut off (max_tokens)")
    return json.loads(next(b.text for b in response.content if b.type == "text")), usd


_ADDRESS = re.compile(r"#\s?\d{1,3}-\d{1,4}|\b(?:blk|block)\s?\d+[a-z]?\b|\b\d{6}\b", re.IGNORECASE)


def scrub(text: str, names: list[str]) -> str:
    """No unit numbers, blocks, postal codes or project (client) names in the memo."""
    text = _ADDRESS.sub("", str(text or ""))
    for n in sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True):
        text = re.sub(re.escape(n), "a project", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def _names(conn) -> list[str]:
    try:
        rows = conn.execute("SELECT name, folder FROM projects").fetchall()
    except Exception:
        return []
    return [v for r in rows for v in (r["name"], r["folder"]) if v and v.lower() not in ("topics", "everyday posts")]


def clean_memo(answer: dict, found: list[dict], names: list[str]) -> dict:
    """Claude's memo, checked: words tidied, nothing personal, every bullet cites a number, 3 to 5 bullets,
    nothing called proven when no finding is."""
    import wording
    any_proven = any(r["label"] == "proven" for r in found)
    out = {"headline": scrub(wording.tidy(str(answer.get("headline") or ""))[:300], names)}
    for key in ("do_more", "do_less", "try_next"):
        rows = []
        for b in answer.get(key) or []:
            text = scrub(wording.tidy(str((b or {}).get("text") or "")), names)[:400]
            strength = (b or {}).get("strength") if (b or {}).get("strength") in ("proven", "early", "idea") else "early"
            if strength == "proven" and not any_proven:
                strength = "early"
            if text and re.search(r"\d", text):
                rows.append({"text": text, "strength": strength})
        out[key] = rows
    while sum(len(out[k]) for k in ("do_more", "do_less", "try_next")) > 5:
        longest = max(("do_more", "do_less", "try_next"), key=lambda k: (len(out[k]), k != "try_next"))
        out[longest].pop()
    out["cant_tell"] = [scrub(wording.tidy(str(t)), names)[:400] for t in (answer.get("cant_tell") or []) if str(t).strip()][:3]
    return out


def plain_memo(found: list[dict], grade_text: str, scored: int) -> dict:
    """The memo without Claude (too few posts, no key, or Claude failed): the findings as they are."""
    def line(r):
        return (f"{_fval(r['feature'], r['value'])}: {LABELS[r['metric']].lower()} {_pct(r['lift'])} against the rest "
                f"(n={r['n']} vs {r['n_rest']}, {'proven' if r['label'] == 'proven' else 'early signal'}).")
    good = [r for r in found if r["label"] in ("proven", "early") and (r["lift"] or 0) > 0][:2]
    bad = [r for r in found if r["label"] in ("proven", "early") and (r["lift"] or 0) < 0][:2]
    few = [r for r in found if r["label"] == "few" and r["n"] >= 1][:1]
    memo = {"headline": (f"{scored} posts scored so far: " + ("early signals only, so treat these as hunches."
                                                               if not any(r["label"] == "proven" for r in found)
                                                               else "some findings are proven.")),
            "do_more": [{"text": line(r), "strength": r["label"]} for r in good],
            "do_less": [{"text": line(r), "strength": r["label"]} for r in bad],
            "try_next": [{"text": f"Post 2 or 3 more with {_fval(r['feature'], r['value']).lower()} (n={r['n']} so far): "
                                  "too few to tell yet.", "strength": "idea"} for r in few],
            "cant_tell": []}
    if scored < MEMO_MIN_POSTS:
        memo["cant_tell"].append(f"Only {scored} posts have day-7 numbers; the memo needs at least {MEMO_MIN_POSTS} "
                                 "to compare kinds of post.")
    if not good and not bad:
        memo["cant_tell"].append("No feature stands out from the rest yet: more posts of each kind will tell.")
    memo["grade"] = grade_text
    return memo


def data(conn, now: datetime | None = None) -> dict:
    """Everything the memo and the page work from."""
    now = now or _now()
    items = table(conn, now)
    found = findings(items)
    return {"today": now.astimezone(SGT).strftime("%A %d %B %Y"), "items": items, "findings": found,
            "headline": headline_findings(found), "baselines": baselines(items), "followers": followers(conn)}


def memo_path(day: str):
    return folder() / f"{day}.json"


def memos() -> list[str]:
    """The dates of every memo kept, newest first."""
    try:
        return sorted((p.stem for p in folder().glob("????-??-??.json")), reverse=True)
    except OSError:
        return []


def load_memo(day: str | None = None) -> dict | None:
    days = memos()
    day = day or (days[0] if days else None)
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return None
    return _json(memo_path(day).read_text(encoding="utf-8") if memo_path(day).is_file() else "", {}) or None


def weekly(conn, client=None, now: datetime | None = None, use_claude: bool = True) -> dict:
    """The Monday run: forecast the week, grade what's graded, then the memo (one Claude call). Kept as
    ROOT/insights/<date>.json. Returns the memo record."""
    with _lock:
        now = now or _now()
        ensure(conn)
        d = data(conn, now)
        items = d["items"]
        made = forecast(conn, items, now)
        live = grade(conn, items, now)
        past = backtest(items)
        g = grade_line(live, past)
        d["grade"], d["forecasts"] = g, made
        scored = sum(1 for p in items if p.get("score") and p["score"]["index"])
        day = now.astimezone(SGT).date().isoformat()
        record = {"date": day, "made_at": _iso(now), "grade": g, "live": live, "backtest": past,
                  "forecasts": made, "scored": scored, "by": "studio", "usd": 0.0, "error": None}
        names = _names(conn)
        memo = None
        if use_claude and scored >= MEMO_MIN_POSTS and (client is not None or bool(settings.ANTHROPIC_API_KEY)):
            try:
                answer, usd = _ask(prompt(d), f"insights:{day}", client)
                memo = clean_memo(answer, d["headline"], names)
                record.update(by="claude", usd=round(usd, 4))
                if sum(len(memo[k]) for k in ("do_more", "do_less", "try_next")) < 3:
                    extra = plain_memo(d["headline"], g["text"], scored)
                    for k in ("do_more", "do_less", "try_next"):
                        memo[k] += [b for b in extra[k] if b not in memo[k]][:max(0, 3 - sum(len(memo[x]) for x in
                                                                                            ("do_more", "do_less", "try_next")))]
            except Exception as e:
                log.warning("The insights memo fell back to the plain one: %s", e)
                record["error"] = str(e)[:300]
        memo = memo or plain_memo(d["headline"], g["text"], scored)
        memo["grade"] = g["text"]
        record["memo"] = memo
        record["findings"] = [r for r in d["headline"] if r["label"] in ("proven", "early")][:20]
        tmp = memo_path(day).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(memo_path(day))
        log.info("Insights memo for %s (%s): %d posts scored, %d forecasts made", day, record["by"], scored, len(made))
    try:
        import learning
        learning.sync_playbook()   # the memo's soft guidance in Studio's own section
    except Exception as e:
        log.debug("Playbook not updated with the memo: %s", e)
    return record


def due(now: datetime | None = None) -> bool:
    """This week's memo isn't written yet and its slot (STUDIO_INSIGHTS_DAY at STUDIO_INSIGHTS_HOUR) has come."""
    if not settings.INSIGHTS or settings.INSIGHTS_DAY not in DAYS:
        return False
    local = (now or _now()).astimezone(SGT)
    back = (local.weekday() - DAYS.index(settings.INSIGHTS_DAY)) % 7
    slot = (local - timedelta(days=back)).replace(hour=settings.INSIGHTS_HOUR, minute=0, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=7)
    return not any(d >= slot.date().isoformat() for d in memos())


def run_due(conn, remote_ideas: list[dict] | None = None, client=None, now: datetime | None = None) -> bool:
    """From the sync, every tick: remember the ideas behind posts (at most every 10 minutes, only when they
    changed), and the Monday run when it's due and Instagram has numbers. True when the memo was written."""
    if not settings.INSIGHTS:
        return False
    if remote_ideas is not None and time.time() - _ideas_seen["at"] > 600:
        _ideas_seen["at"] = time.time()
        remember_ideas(conn, remote_ideas)
    if not due(now):
        return False
    if not conn.execute("SELECT 1 FROM post_results LIMIT 1").fetchone():
        return False
    if time.time() - _tried["at"] < 3600:
        return False
    _tried["at"] = time.time()
    weekly(conn, client, now)
    return True


# ── Where it's used ───────────────────────────────────────────────────────────

def _bullets(memo: dict, keys=("do_more", "do_less", "try_next"), limit: int = 5) -> list[str]:
    tag = {"proven": "proven", "early": "early signal", "idea": "to test"}
    head = {"do_more": "Do more", "do_less": "Do less", "try_next": "Try next"}
    out = []
    for k in keys:
        for b in memo.get(k) or []:
            out.append(f"{head[k]} ({tag.get(b.get('strength'), 'early signal')}): {b.get('text', '')}")
    return out[:limit]


def prompt_lines(limit: int = 5) -> str:
    """The latest memo's top lines for the suggested topics' prompt ("" when there's no memo)."""
    rec = load_memo()
    if not rec or not rec.get("memo"):
        return ""
    lines = _bullets(rec["memo"], limit=limit)
    if not lines:
        return ""
    return "\n".join([f"(Memo of {_day_text(rec['date'])}. {rec['grade']['text'].split(':')[0]}.)", *(f"- {ln}" for ln in lines)])[:1200]


def guidance_lines(limit: int = 4) -> list[str]:
    """The latest memo's Do more / Do less, for Studio's playbook section (soft guidance, never rules)."""
    rec = load_memo()
    if not rec or not rec.get("memo"):
        return []
    return _bullets(rec["memo"], keys=("do_more", "do_less"), limit=limit)


def _row_out(p: dict) -> dict:
    s, s7 = p.get("score_latest") or {}, p.get("score") or {}
    d7 = p.get("day7") or {}
    rates = (d7 or p["latest"])["rates"]
    reach = (d7 or p["latest"]).get("reach")
    feats = {k: v for k, v in p["features"].items() if v is not None}
    status = "" if d7 else "too_new"
    if d7 and d7.get("late"):
        status = "late"
    return {"media_id": p["media_id"], "post_id": p["post_id"], "title": p["title"], "permalink": p["permalink"],
            "posted_at": p["posted_at"], "format": p["format"], "by_studio": p["by_studio"], "status": status,
            "age": p["latest"]["age"], "index": round(s7["index"], 1) if s7.get("index") else None,
            "index_latest": round(s["index"], 1) if s.get("index") else None,
            "per": {k: round(v, 1) for k, v in (s7.get("per") or {}).items()},
            "rates": {k: round(v, 3) for k, v in rates.items()}, "reach": reach, "likes": (d7 or p["latest"]).get("likes"),
            "early_base": p.get("early_base"), "features": feats}


def page(conn, memo_day: str | None = None) -> dict:
    """What the planner's Insights page shows (GET /api/instagram/insights). Reads only."""
    ensure(conn)
    d = data(conn)
    rec = load_memo(memo_day)
    live = grade(conn, d["items"])
    # The grade as it stands now (graded forecasts), with the backtest the latest memo measured.
    g = grade_line(live, (load_memo() or {}).get("backtest"))
    upcoming = [dict(r) for r in conn.execute(
        "SELECT post_id, format, scheduled_at, expected, actual FROM insight_forecasts ORDER BY scheduled_at DESC LIMIT 20")]
    return {"grade": g, "memo": rec, "memos": memos(), "posts": [_row_out(p) for p in reversed(d["items"])],
            "findings": d["headline"], "baselines": d["baselines"], "followers": d["followers"][-90:],
            "forecasts": upcoming, "labels": {"features": FEATURES, "metrics": LABELS, "units": UNITS},
            "rules": {"age": AGE, "base_n": BASE_N, "show_min": SHOW_MIN, "proven_n": PROVEN_N,
                      "proven_lift": PROVEN_LIFT, "early_lift": EARLY_LIFT, "target": TARGET, "grade_min": GRADE_MIN},
            "next_memo": _next_slot(), "on": settings.INSIGHTS}


def _next_slot() -> str | None:
    if not settings.INSIGHTS or settings.INSIGHTS_DAY not in DAYS:
        return None
    local = _now().astimezone(SGT)
    ahead = (DAYS.index(settings.INSIGHTS_DAY) - local.weekday()) % 7
    slot = (local + timedelta(days=ahead)).replace(hour=settings.INSIGHTS_HOUR, minute=0, second=0, microsecond=0)
    if slot <= local:
        slot += timedelta(days=7)
    return _iso(slot)
