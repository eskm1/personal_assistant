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

CREATE TABLE IF NOT EXISTS votes (
    idea_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    vote        INTEGER NOT NULL,          -- 1 = pursue, -1 = skip
    created_at  TEXT NOT NULL,
    PRIMARY KEY (idea_id, user_id)
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
    },
    "productions": {
        "plan": "TEXT",   # JSON: the approved idea the editor follows
        "edl": "TEXT",    # JSON: the latest draft's cut list, hook text and music brief
        "qa": "TEXT",     # JSON: checks on the latest draft (words kept, loudness)
        "music": "TEXT",  # JSON: the Epidemic track used, or the note for Instagram music
        "error": "TEXT",  # why the last edit failed, if it did
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
    },
    "feedback": {"author": "TEXT"},
    "shots": {"camera": "TEXT",  # iphone | zfc | gopro | any: which of the team's cameras films it
              "remote_id": "INTEGER"},
    # remote_id: the row's id in the online planner, when planning is online (cloud_sync.py).
    "projects": {"remote_id": "INTEGER"},
    "arcs": {"remote_id": "INTEGER"},
    "takes": {"remote_id": "INTEGER"},
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
    conn.commit()
    return conn


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
                 at_seconds: float | None = None, remember: bool = False, author: str | None = None) -> int:
    version = 0
    if target == "production":
        row = conn.execute("SELECT version FROM productions WHERE id = ?", (target_id,)).fetchone()
        version = row["version"] if row else 0
    cur = conn.execute(
        """INSERT INTO feedback (target, target_id, version, at_seconds, body, remember, author, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (target, target_id, version, at_seconds, body, int(remember), author, now()),
    )
    conn.commit()
    return cur.lastrowid


def feedback_for(conn: sqlite3.Connection, target: str, target_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM feedback WHERE target = ? AND target_id = ? ORDER BY version, COALESCE(at_seconds, 1e9), id",
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
    plan = {k: idea[k] for k in ("title", "brand", "format", "hook", "angle", "est_seconds")}
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
         json.dumps(clip_ids), caption if caption is not None else idea["caption"], json.dumps(plan),
         stamp, stamp),
    )
    conn.execute("UPDATE ideas SET status = 'made', production_id = ? WHERE id = ?", (cur.lastrowid, idea_id))
    conn.commit()
    return cur.lastrowid
