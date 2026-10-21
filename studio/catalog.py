"""
The studio catalogue (SQLite), shared by the worker, the dashboard and the editor.

- clips: one row per raw phone video, the index phase 2's idea tools will
  search. The files live in the library folder next to their sidecars
  (transcript.json, analysis.json, frames/); the row points at them.
- productions: one row per edited post. The editor renders a draft and sets
  status 'ready'; Bryan reviews it on the dashboard.
- feedback: Bryan's notes on a clip or a production version, optionally pinned
  to a moment in the video.
- ideas: post ideas. Either Claude proposes them from the library (ideas.py,
  origin 'library'), or the team plans them before filming (plan.py, origin
  'plan', inside a project and an arc). Making one creates a 'planned'
  production whose plan (segments, hook, beats) the editor follows.
- projects / arcs / shots / takes: the planning board. A project (say a client
  job) holds content arcs (series), each holding planned ideas; each idea's
  storyboard is a list of shots to film. Footage that lands in the library is
  matched to shots as takes, so the board shows what's filmed and what's missing.
- users / invites / votes: team logins, invite links, and the team's vote on
  planned ideas. Bryan (owner) has the final say; the team plans and comments.
- idea_runs: what each "ideas from new footage" run did (footage_ideas.py);
  clips.ideas_run points at the run that read a clip, so it's read once. The
  suggested topics' runs are recorded here too (topics.py, trigger "topics:...").

- posts / ig_media / post_results / account_results: Instagram (posting.py, post_results.py).
  A post is an approved draft Bryan scheduled (scheduled -> publishing -> posted, or failed /
  cancelled); ig_media every post on the account Studio reads results for; post_results their
  numbers per day; account_results the account's day.

The review contract between the dashboard and the editor:
  ready --(request changes)--> revising --(editor re-renders, version + 1,
  marks that version's notes addressed)--> ready --(approve)--> approved
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from settings import CATALOG_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id             INTEGER PRIMARY KEY,
    fingerprint    TEXT UNIQUE NOT NULL,
    source_name    TEXT NOT NULL,
    clip_dir       TEXT NOT NULL,
    video_path     TEXT NOT NULL,
    status         TEXT NOT NULL,          -- processing | transcribed (no Claude) | tagged | failed
    error          TEXT,
    ingested_at    TEXT NOT NULL,
    recorded_at    TEXT,
    duration       REAL,
    width          INTEGER,
    height         INTEGER,
    orientation    TEXT,
    has_audio      INTEGER,
    language       TEXT,
    transcript     TEXT,
    brand          TEXT,                   -- personal | urban_makers | unsure
    brand_reason   TEXT,
    format         TEXT,                   -- talking_head | day_in_the_life | compilation | other
    title          TEXT,
    summary        TEXT,
    topics         TEXT,                   -- JSON array
    setting        TEXT,
    usable         INTEGER,
    quality_notes  TEXT,
    suggested_hook TEXT,
    moments        TEXT                    -- JSON array of {start, end, kind, description}
);
CREATE INDEX IF NOT EXISTS clips_brand_format ON clips (brand, format);

CREATE TABLE IF NOT EXISTS productions (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    brand        TEXT,                     -- personal | urban_makers
    format       TEXT,
    status       TEXT NOT NULL,            -- planned | producing | ready | revising | approved | rejected | posted | failed
    version      INTEGER NOT NULL DEFAULT 0,
    idea         TEXT,                     -- the brief the edit was made from
    source_clips TEXT,                     -- JSON array of clip ids
    video_path   TEXT,                     -- latest rendered draft
    thumb_path   TEXT,
    duration     REAL,
    caption      TEXT,                     -- the post caption; Bryan has the final say
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY,
    target      TEXT NOT NULL,             -- clip | production
    target_id   INTEGER NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    at_seconds  REAL,                      -- moment in the video the note is about, if any
    body        TEXT NOT NULL,
    remember    INTEGER NOT NULL DEFAULT 0, -- 1 = also added to the playbook for future edits
    status      TEXT NOT NULL DEFAULT 'open', -- open | addressed
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_target ON feedback (target, target_id);

CREATE TABLE IF NOT EXISTS ideas (
    id            INTEGER PRIMARY KEY,
    batch         TEXT NOT NULL,           -- one "Suggest posts" run
    status        TEXT NOT NULL DEFAULT 'new', -- new | made | dismissed
    brand         TEXT,
    format        TEXT,
    title         TEXT NOT NULL,
    hook          TEXT,
    angle         TEXT,                    -- why it should work
    beats         TEXT,                    -- JSON array: the post's structure, in order
    segments      TEXT,                    -- JSON array of {clip_id, start, end, purpose}
    caption       TEXT,
    est_seconds   REAL,
    production_id INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    brand       TEXT,                      -- the account most of it is for
    folder      TEXT,                      -- inbox subfolder whose uploads belong to it
    brief       TEXT,                      -- what the job is, who it's for, what to show
    status      TEXT NOT NULL DEFAULT 'active', -- active | archived
    created_by  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arcs (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    title       TEXT NOT NULL,
    purpose     TEXT,
    position    REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (            -- one storyboard frame = one shot to film
    id          INTEGER PRIMARY KEY,
    idea_id     INTEGER NOT NULL,
    position    REAL NOT NULL DEFAULT 0,
    kind        TEXT,                      -- talking | broll | detail | before_after | reaction | screen
    framing     TEXT,                      -- wide | medium | close | detail | pov | overhead
    description TEXT NOT NULL,             -- what the camera sees
    line        TEXT,                      -- what's said over it, if anything
    on_screen   TEXT,                      -- text on screen, if any
    seconds     REAL,
    why         TEXT,
    filmed_at   TEXT,                      -- ticked off on site
    filmed_by   INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS shots_idea ON shots (idea_id, position);

CREATE TABLE IF NOT EXISTS takes (            -- footage that fulfils a shot
    id          INTEGER PRIMARY KEY,
    shot_id     INTEGER NOT NULL,
    clip_id     INTEGER NOT NULL,
    start       REAL,
    end         REAL,
    source      TEXT NOT NULL,             -- auto (matched on ingest) | manual
    note        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (shot_id, clip_id)
);
CREATE INDEX IF NOT EXISTS takes_clip ON takes (clip_id);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL,         -- owner | team
    password_hash   TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 1, -- bumped to sign a user out everywhere
    disabled        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT UNIQUE NOT NULL,
    role        TEXT NOT NULL,
    note        TEXT,
    created_by  INTEGER,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_by     INTEGER,
    used_at     TEXT
);

CREATE TABLE IF NOT EXISTS upload_pins (     -- footage uploaded from the planner to one shot (uploads.py)
    upload_id   TEXT PRIMARY KEY,
    path        TEXT NOT NULL,             -- where it waits, relative to the inbox (a/b/c.mp4)
    shot_remote INTEGER NOT NULL,          -- the shot's id in the online planner
    user_id     TEXT,                      -- the planner user who uploaded it
    state       TEXT NOT NULL,             -- received | ingested | failed
    clip_id     INTEGER,
    error       TEXT,
    reported    TEXT,                      -- the state last written to shot_uploads online
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS upload_pins_path ON upload_pins (path, state);

CREATE TABLE IF NOT EXISTS idea_runs (       -- "ideas from new footage" (footage_ideas.py), one row per project per run
    id          INTEGER PRIMARY KEY,
    trigger     TEXT NOT NULL,             -- auto (quiet after uploads) | job (asked in the planner) | baseline
                                           -- | topics:weekly | topics:behind | topics:job (topics.py)
    place       TEXT,                      -- cloud (the online planner) | local (this dashboard's plan)
    project_ref INTEGER,                   -- the project's id there
    project     TEXT,                      -- its name
    clip_ids    TEXT,                      -- JSON: the clips this run looked at
    state       TEXT NOT NULL,             -- running | done | failed
    result      TEXT,                      -- JSON: {new_ideas: [ids], attached: [...], skipped, notes}
    error       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS posts (           -- an approved draft scheduled for Instagram (posting.py)
    id            INTEGER PRIMARY KEY,
    production_id INTEGER NOT NULL,
    idea_remote   INTEGER,                 -- the planner idea it was made from, if any
    kind          TEXT NOT NULL,           -- reel | carousel
    title         TEXT,
    caption       TEXT,
    post_type     TEXT,                    -- the kind of post (knowhow, ideas, onsite, finished, offer), if known
    version       INTEGER,                 -- the draft version that goes out
    scheduled_at  TEXT NOT NULL,           -- UTC; "Post now" = the moment it was asked
    cover_ms      INTEGER,                 -- a Reel's cover: this moment of the video (0 = the first frame)
    share_to_feed INTEGER NOT NULL DEFAULT 1,
    collaborators TEXT,                    -- JSON: Instagram usernames invited as collaborators
    state         TEXT NOT NULL,           -- scheduled | publishing | posted | failed | cancelled
    stage         TEXT,                    -- while publishing: preparing | uploading | processing | publishing
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_try_at   TEXT,                    -- after a passing problem, when it tries again
    error         TEXT,                    -- why it failed or is waiting, in plain words
    problem       TEXT,                    -- token | limit | spec | media | transient | other
    containers    TEXT,                    -- JSON: {parent, children: [...]} Instagram's containers for it
    publish_at    TEXT,                    -- when media_publish was first sent (never posts twice after a crash)
    media_id      TEXT,
    permalink     TEXT,
    posted_at     TEXT,
    created_by    TEXT,
    cloud_hash    TEXT,                    -- what the planner was last sent (cloud_sync)
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_state ON posts (state, scheduled_at);
CREATE INDEX IF NOT EXISTS posts_production ON posts (production_id);

CREATE TABLE IF NOT EXISTS ig_media (        -- every post on the account Studio reads results for (post_results.py)
    media_id      TEXT PRIMARY KEY,
    post_id       INTEGER,                 -- Studio's post, when Studio posted it
    media_type    TEXT,                    -- VIDEO | CAROUSEL_ALBUM | IMAGE (Instagram's words)
    product_type  TEXT,                    -- REELS | FEED | ...
    permalink     TEXT,
    caption       TEXT,
    posted_at     TEXT,
    seen_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_results (    -- one post's numbers, as read on one day (Singapore)
    media_id       TEXT NOT NULL,
    day            TEXT NOT NULL,
    post_id        INTEGER,
    views          INTEGER,
    reach          INTEGER,
    likes          INTEGER,
    comments       INTEGER,
    saves          INTEGER,
    shares         INTEGER,
    interactions   INTEGER,
    avg_watch_ms   INTEGER,                -- Reels: average time a view lasted
    total_watch_ms INTEGER,                -- Reels: all viewing time, replays included
    follows        INTEGER,
    profile_visits INTEGER,
    raw            TEXT,                   -- JSON: every metric Instagram answered, by its own name
    pulled_at      TEXT NOT NULL,
    PRIMARY KEY (media_id, day)
);

CREATE TABLE IF NOT EXISTS account_results ( -- the account, one row per day (Singapore)
    day           TEXT PRIMARY KEY,
    followers     INTEGER,
    reach         INTEGER,
    views         INTEGER,
    interactions  INTEGER,
    raw           TEXT,
    pulled_at     TEXT NOT NULL,
    cloud_hash    TEXT
);

CREATE TABLE IF NOT EXISTS votes (
    idea_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    vote        INTEGER NOT NULL,          -- 1 = pursue, -1 = skip
    created_at  TEXT NOT NULL,
    PRIMARY KEY (idea_id, user_id)
);

CREATE TABLE IF NOT EXISTS usage_events (    -- what people do in Review and the planner, never content (activity.py)
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    user_id     TEXT NOT NULL,             -- the signed-in person's email (Review's cookie or the planner's token)
    user_name   TEXT,
    role        TEXT,                      -- owner | team
    session     TEXT,                      -- random per browser tab
    page        TEXT,                      -- home, ideas, idea, draft, ... (help.js PAGES)
    route       TEXT,                      -- the address with ids replaced: #/production/:id
    kind        TEXT NOT NULL,             -- view | click | dead | rage | dialog | error | api_error | action
    target      TEXT,                      -- the control: #id, [data-x=y], aria label or tag.class
    detail      TEXT,                      -- small JSON
    qa          INTEGER                    -- 1 = a test walk (localStorage studio.qa = 1): left out of the report
);
CREATE INDEX IF NOT EXISTS usage_events_at ON usage_events (at);

CREATE TABLE IF NOT EXISTS friction_flags (  -- "Something not working?" (help_desk.py)
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    user_name   TEXT,
    page        TEXT,
    route       TEXT,
    control     TEXT,                      -- the part they tapped (a descriptor, never its text), or empty
    words       TEXT,                      -- what they typed, if anything
    viewport    TEXT,
    version     TEXT,
    done_at     TEXT,
    done_by     TEXT,
    qa          INTEGER
);

CREATE TABLE IF NOT EXISTS help_questions (  -- Ask Studio (help_desk.py)
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    user_name   TEXT,
    page        TEXT,
    question    TEXT,
    answer      TEXT,
    answered    INTEGER,                   -- 1 = Studio's notes covered it, 0 = they didn't or Claude wasn't there
    error       TEXT,
    helpful     INTEGER,                   -- the person's "Did that help?": 1, 0 or NULL
    qa          INTEGER
);
"""

# Columns added after the first release, applied to existing databases on connect.
_ADDED_COLUMNS = {
    "clips": {
        "reviewed_at": "TEXT",
        "quality": "TEXT",      # JSON: quality.assess() — verdict, flags with tips, measurements
        "project_id": "INTEGER",
        "cloud_hash": "TEXT",      # hash of the summary last pushed to the online planner
        "cloud_thumb": "TEXT",     # the frame last uploaded as its still
        "cloud_pushed_at": "TEXT",
        "idea_id": "INTEGER",      # the post folder it came from (Inbox/<project>/<post>/), if any
        "ideas_run": "INTEGER",    # the idea_runs row that read it for ideas (footage_ideas.py); NULL = not yet
        "ideas_tries": "INTEGER",  # failed attempts at that (it stops trying after a few)
        # Clean-up (Review's Library): archived = hidden from the Library, ideas from footage, topics and
        # pickers (undo puts it back); freed = the server's copy of the original video was deleted
        # because OneDrive's archive holds it (fetched back by itself when an edit needs it).
        "archived_at": "TEXT",
        "onedrive_path": "TEXT",   # where OneDrive keeps the original (Studio Archive/<month>/...), when known
        "onedrive_size": "INTEGER",
        "freed_at": "TEXT",
        "freed_bytes": "INTEGER",
    },
    "productions": {
        "plan": "TEXT",   # JSON: the approved idea the editor follows
        "edl": "TEXT",    # JSON: the latest draft's cut list, hook text and music brief
        "qa": "TEXT",     # JSON: checks on the latest draft (words kept, loudness)
        "music": "TEXT",  # JSON: the Epidemic track used, or the note for Instagram music
        "error": "TEXT",  # why the last edit failed, if it did
        "review": "TEXT",          # JSON: Studio's read of a draft (draft_review.py)
        "review_version": "INTEGER",  # the draft version that read describes
        # JSON, carousels only (format 'carousel'): the current draft's slides and their
        # rendered PNGs {slides: [...], files: [...], summary, notes_addressed} (carousel.py)
        "slides": "TEXT",
        # JSON: Studio's own fix rounds on the current version before Bryan saw it (self_fix.py):
        # {version, caught, fixed: [...], rounds: [...], usd}
        "self_fix": "TEXT",
        # Clean-up (Review's Done): archived = hidden from Done (still in OneDrive's ready folder; undo);
        # removed = a rejected draft's files were deleted, this row stays as the record.
        "archived_at": "TEXT",
        "removed_at": "TEXT",
        "removed_bytes": "INTEGER",
        # 1 = made again because Studio's look changed (restyle.py): a carousel's slides are rewritten
        # in the current style guide even without notes. 2 = started over (a fresh edit, not a
        # revision of the last cut). Cleared once the editor has taken it.
        "restyle": "INTEGER",
    },
    "ideas": {
        "wishlist": "TEXT",     # JSON: shots Bryan could film to make the idea stronger
        "origin": "TEXT",       # library (from footage) | plan (planned before filming)
        "project_id": "INTEGER",
        "arc_id": "INTEGER",
        "purpose": "TEXT",
        "cta": "TEXT",
        "music_plan": "TEXT",   # JSON: {mood, tempo_bpm, search, note, shortlist: [...], chosen}
        "decision": "TEXT",     # proposed | go | parked (Bryan's call on a planned idea)
        "position": "REAL",
        "created_by": "INTEGER",
        "updated_at": "TEXT",
        "remote_id": "INTEGER",  # its row in the online planner (cloud_sync.py)
        "folder": "TEXT",        # its post folder in the inbox (Inbox/<project>/<folder>/), if it has one
        "post_type": "TEXT",     # knowhow | ideas | onsite | finished | offer: the job the post does (content_plan.py)
        "slides": "TEXT",        # JSON: a carousel's slides, as in the planner (carousel.py)
    },
    "feedback": {"author": "TEXT",
                 "slide": "INTEGER",   # a carousel note about one slide (1 = the cover), if any
                 "kind": "TEXT",       # editing | taste, as the editor sorted it on the send-back (learning.py)
                 "rule": "TEXT"},      # the rule Studio learned from it, for an editing note
    "shots": {"camera": "TEXT",  # iphone | zfc | gopro | any: which of the team's cameras films it
              "remote_id": "INTEGER"},
    # remote_id: the row's id in the online planner, when planning is online (cloud_sync.py).
    "projects": {"remote_id": "INTEGER"},
    "arcs": {"remote_id": "INTEGER"},
    "takes": {"remote_id": "INTEGER"},
    "post_results": {"cloud_hash": "TEXT"},
    # 1 = this email is proven to be theirs in the planner (they signed in with the planner's
    # password), so the dashboard may open the planner for them (planner_site.py).
    "users": {"planner_ok": "INTEGER"},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path = CATALOG_DB) -> sqlite3.Connection:
    # The worker and the dashboard write from different threads; WAL lets the
    # dashboard read while the worker writes, and the timeout rides out locks.
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    # Rejected drafts whose files were removed before that moved them to Archived (2026-10-20).
    conn.execute("""UPDATE productions SET archived_at = removed_at
                    WHERE status = 'rejected' AND removed_at IS NOT NULL AND archived_at IS NULL""")
    conn.commit()
    return conn


def file_under(conn: sqlite3.Connection, brand: str) -> int:
    """With one account (settings.SINGLE_BRAND), file everything already in the
    catalogue under it. A clip keeps a note of what it was tagged before. Returns
    how many rows changed; 0 once done."""
    n = conn.execute(
        """UPDATE clips SET brand_reason = 'Tagged ' || brand || ' before everything became ' || ? || '. '
               || COALESCE(brand_reason, ''), brand = ?
           WHERE brand IS NOT NULL AND brand <> ?""",
        (brand.replace("_", " ").title(), brand, brand)).rowcount
    for table in ("productions", "ideas", "projects"):
        n += conn.execute(f"UPDATE {table} SET brand = ? WHERE brand IS NOT NULL AND brand <> ?",
                          (brand, brand)).rowcount
    conn.commit()
    return n


def find(conn: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clips WHERE fingerprint = ?", (fingerprint,)).fetchone()


def start(conn: sqlite3.Connection, fingerprint: str, source_name: str, clip_dir: Path,
          video_path: Path) -> int:
    """Record a clip as soon as it leaves the inbox, so a crash mid-processing
    leaves a 'processing'/'failed' row to retry instead of a lost file."""
    cur = conn.execute(
        """INSERT INTO clips (fingerprint, source_name, clip_dir, video_path, status, ingested_at)
           VALUES (?, ?, ?, ?, 'processing', ?)
           ON CONFLICT (fingerprint) DO UPDATE SET status = 'processing', error = NULL,
               source_name = excluded.source_name, clip_dir = excluded.clip_dir,
               video_path = excluded.video_path
           RETURNING id""",
        (fingerprint, source_name, str(clip_dir), str(video_path), now()),
    )
    clip_id = cur.fetchone()[0]
    conn.commit()
    return clip_id


def save_tags(conn: sqlite3.Connection, clip_id: int, meta: dict, transcript: dict | None,
              analysis: dict | None) -> None:
    a = analysis or {}
    conn.execute(
        """UPDATE clips SET status = ?, error = NULL, recorded_at = ?, duration = ?,
               width = ?, height = ?, orientation = ?, has_audio = ?, language = ?, transcript = ?,
               brand = ?, brand_reason = ?, format = ?, title = ?, summary = ?, topics = ?,
               setting = ?, usable = ?, quality_notes = ?, suggested_hook = ?, moments = ?
           WHERE id = ?""",
        ("tagged" if analysis else "transcribed", meta.get("recorded_at"), meta.get("duration"),
         meta.get("width"), meta.get("height"), meta.get("orientation"), int(bool(meta.get("has_audio"))),
         (transcript or {}).get("language"), (transcript or {}).get("text"),
         a.get("brand"), a.get("brand_reason"), a.get("format"), a.get("title"), a.get("summary"),
         json.dumps(a.get("topics", [])), a.get("setting"),
         None if "usable" not in a else int(a["usable"]),
         a.get("quality_notes"), a.get("suggested_hook"), json.dumps(a.get("moments", [])),
         clip_id),
    )
    conn.commit()


def save_quality(conn: sqlite3.Connection, clip_id: int, quality: dict) -> None:
    conn.execute("UPDATE clips SET quality = ? WHERE id = ?", (json.dumps(quality), clip_id))
    conn.commit()


def mark_failed(conn: sqlite3.Connection, clip_id: int, error: str) -> None:
    conn.execute("UPDATE clips SET status = 'failed', error = ? WHERE id = ?", (error[:2000], clip_id))
    conn.commit()


def unfinished(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Clips that failed, were interrupted, or were saved with --no-claude — what
    --retry-failed (and every worker start) reprocesses."""
    return conn.execute(
        "SELECT * FROM clips WHERE status IN ('failed', 'processing', 'transcribed') ORDER BY id"
    ).fetchall()


# ── Review (dashboard) ────────────────────────────────────────────────────────

def add_feedback(conn: sqlite3.Connection, target: str, target_id: int, body: str,
                 at_seconds: float | None = None, remember: bool = False, author: str | None = None,
                 slide: int | None = None) -> int:
    version = 0
    if target == "production":
        row = conn.execute("SELECT version FROM productions WHERE id = ?", (target_id,)).fetchone()
        version = row["version"] if row else 0
    cur = conn.execute(
        """INSERT INTO feedback (target, target_id, version, at_seconds, body, remember, author, slide, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (target, target_id, version, at_seconds, body, int(remember), author, slide, now()),
    )
    conn.commit()
    return cur.lastrowid


def feedback_for(conn: sqlite3.Connection, target: str, target_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM feedback WHERE target = ? AND target_id = ? ORDER BY version, COALESCE(at_seconds, slide, 1e9), id",
        (target, target_id),
    ).fetchall()


def set_production_status(conn: sqlite3.Connection, production_id: int, status: str,
                          caption: str | None = None) -> None:
    if caption is None:
        conn.execute("UPDATE productions SET status = ?, updated_at = ? WHERE id = ?",
                     (status, now(), production_id))
    else:
        conn.execute("UPDATE productions SET status = ?, caption = ?, updated_at = ? WHERE id = ?",
                     (status, caption, now(), production_id))
    conn.commit()


def update_clip_review(conn: sqlite3.Connection, clip_id: int, brand: str, fmt: str,
                       usable: bool) -> None:
    """Bryan's corrections to Claude's tags win; reviewed_at marks them as checked."""
    conn.execute("UPDATE clips SET brand = ?, format = ?, usable = ?, reviewed_at = ? WHERE id = ?",
                 (brand, fmt, int(usable), now(), clip_id))
    conn.commit()


# ── Ideas ─────────────────────────────────────────────────────────────────────

def save_ideas(conn: sqlite3.Connection, batch: str, ideas: list[dict]) -> None:
    for i in ideas:
        conn.execute(
            """INSERT INTO ideas (batch, brand, format, title, hook, angle, beats, segments, caption,
                   est_seconds, wishlist, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch, i["brand"], i["format"], i["title"], i["hook"], i["angle"], json.dumps(i["beats"]),
             json.dumps(i["segments"]), i["caption"], i["est_seconds"], json.dumps(i.get("wishlist", [])), now()),
        )
    conn.commit()


def make_production(conn: sqlite3.Connection, idea_id: int, caption: str | None = None) -> int:
    """Approve an idea: a 'planned' production the editor will pick up."""
    idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if idea is None:
        raise KeyError(idea_id)
    if idea["production_id"]:
        return idea["production_id"]
    segments = json.loads(idea["segments"] or "[]")
    import wording  # the safe fixes on words written online (invisible characters, em dashes...)
    plan = {k: idea[k] for k in ("title", "brand", "format", "hook", "angle", "est_seconds")}
    plan["hook"] = wording.tidy(plan["hook"])
    plan.update(beats=json.loads(idea["beats"] or "[]"), segments=segments, idea_id=idea_id,
                wishlist=json.loads(idea["wishlist"] or "[]"), cta=idea["cta"] or "",
                music_plan=json.loads(idea["music_plan"] or "{}"))
    if not plan["angle"] and idea["purpose"]:
        plan["angle"] = idea["purpose"]
    clip_ids = list(dict.fromkeys(seg["clip_id"] for seg in segments))
    stamp = now()
    cur = conn.execute(
        """INSERT INTO productions (title, brand, format, status, version, idea, source_clips, caption,
               plan, created_at, updated_at) VALUES (?, ?, ?, 'planned', 0, ?, ?, ?, ?, ?, ?)""",
        (idea["title"], idea["brand"], idea["format"], f"{idea['hook']}\n\n{idea['angle'] or idea['purpose'] or ''}",
         json.dumps(clip_ids), wording.tidy(caption if caption is not None else idea["caption"]), json.dumps(plan),
         stamp, stamp),
    )
    conn.execute("UPDATE ideas SET status = 'made', production_id = ? WHERE id = ?", (cur.lastrowid, idea_id))
    conn.commit()
    return cur.lastrowid
