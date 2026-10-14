"""
The clip catalogue: one SQLite row per clip, the index phase 2's idea tools will
search. The files themselves live in the library folder next to their sidecars
(transcript.json, analysis.json, frames/); the row points at them.
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
"""


def connect(db_path: Path = CATALOG_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
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
        (fingerprint, source_name, str(clip_dir), str(video_path),
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
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
        ("tagged" if analysis else "transcribed", meta.get("recorded_at"), meta.get("duration"), meta.get("width"), meta.get("height"),
         meta.get("orientation"), int(bool(meta.get("has_audio"))),
         (transcript or {}).get("language"), (transcript or {}).get("text"),
         a.get("brand"), a.get("brand_reason"), a.get("format"), a.get("title"), a.get("summary"),
         json.dumps(a.get("topics", [])), a.get("setting"),
         None if "usable" not in a else int(a["usable"]),
         a.get("quality_notes"), a.get("suggested_hook"), json.dumps(a.get("moments", [])),
         clip_id),
    )
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
