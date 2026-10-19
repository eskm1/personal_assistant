"""
Clean-up tools for Review (Bryan approved 2026-10-19): keep the Studio machine's disk tidy
without ever losing anything that isn't kept somewhere else.

- Done: Remove a rejected draft (its renders are deleted; the production row stays as the
  record, with its small still, so the planner idea and the history still make sense), or all
  rejected ones at once; Archive an approved or posted draft (hidden from Done; it stays in
  OneDrive's "Studio Ready to post"; undo any time).
- Library: Archive a clip (hidden from the Library, ideas from footage, suggested topics and the
  planner's footage pickers; undo any time); Free up space: delete the server's copy of a clip's
  original video, only when OneDrive's archive holds it (checked in OneDrive at that moment, by
  name and exact size). Stills, preview, transcript and tags stay. If an edit, a carousel still
  or a Resolve project later needs that original, onedrive.ensure_originals() fetches it back.
- "Suggested to tidy": retakes, clips no post has used for 60 days, clips of archived projects.

Deleting is the owner's only (Remove, Clear rejected, Free up space); so is archiving. OneDrive's
own files are never touched. Everything freed is logged (worker.log and logs/cleanup.jsonl).
"""
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, g, jsonify

import catalog
import settings
from webutil import body, db, owner_only

log = logging.getLogger("studio.cleanup")
bp = Blueprint("cleanup", __name__)

RENDERS = settings.ROOT / "Renders"
UNUSED_DAYS = 60
DONE = ("approved", "posted", "rejected")


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _record(kind: str, count: int, freed: int, who: str = "") -> None:
    """What was freed, in worker.log and logs/cleanup.jsonl (one line each)."""
    log.info("Clean-up: %s %d, freed %.2f GB%s", kind, count, freed / 1e9, f" ({who})" if who else "")
    try:
        path = settings.ROOT / "logs" / "cleanup.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": catalog.now(), "what": kind, "count": count, "bytes": freed, "by": who}) + "\n")
    except OSError:
        pass


def freed_so_far() -> int:
    """Bytes freed by the clean-up, all time (from logs/cleanup.jsonl)."""
    total = 0
    try:
        for line in (settings.ROOT / "logs" / "cleanup.jsonl").read_text(encoding="utf-8").splitlines():
            total += int(json.loads(line).get("bytes") or 0)
    except (OSError, ValueError):
        pass
    return total


# ── Drafts ────────────────────────────────────────────────────────────────────

def production_files(row) -> list[Path]:
    """Every file and folder of a production's drafts in Renders (all versions, fix rounds,
    Resolve projects, carousel slides), except its current still: that stays as the record."""
    pid = int(row["id"])
    keep = Path(row["thumb_path"]).resolve() if row["thumb_path"] else None
    out = []
    if not RENDERS.is_dir():
        return out
    for p in RENDERS.iterdir():
        name = p.name
        if not (name.startswith(f"production_{pid}_v") or name.startswith(f"carousel_{pid}_v")):
            continue
        if p.is_dir() and keep and keep.is_relative_to(p.resolve()):
            out.extend(f for f in p.iterdir() if f.resolve() != keep)   # a carousel's folder: all but its still
        elif keep is None or p.resolve() != keep:
            out.append(p)
    return out


def removable_bytes(row) -> int:
    return sum(_size(p) for p in production_files(row))


def remove_production(conn, row) -> int:
    """Delete a rejected draft's renders; the row stays as the record. Returns the bytes freed."""
    if row["status"] != "rejected":
        raise ValueError("only a rejected draft can be removed")
    freed = 0
    for p in production_files(row):
        size = _size(p)
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            freed += size
        except OSError as e:
            log.warning("Clean-up: couldn't delete %s: %s", p, e)
    conn.execute("""UPDATE productions SET removed_at = ?, removed_bytes = COALESCE(removed_bytes, 0) + ?, video_path = NULL
                    WHERE id = ?""", (catalog.now(), freed, row["id"]))
    conn.commit()
    return freed


# ── Clips ─────────────────────────────────────────────────────────────────────

def original_bytes(row) -> int:
    if row["freed_at"]:
        return 0
    return _size(Path(row["video_path"])) if row["video_path"] else 0


def _used_clip_ids(conn) -> set[int]:
    """Clips a post uses: a take on a planned shot, a draft's source, a library idea's segment."""
    used = {r[0] for r in conn.execute("SELECT DISTINCT clip_id FROM takes")}
    for r in conn.execute("SELECT source_clips FROM productions WHERE source_clips IS NOT NULL"):
        try:
            used.update(int(c) for c in json.loads(r[0] or "[]"))
        except (ValueError, TypeError):
            pass
    for r in conn.execute("SELECT segments FROM ideas WHERE segments IS NOT NULL AND segments <> '[]'"):
        try:
            used.update(int(s["clip_id"]) for s in json.loads(r[0]) if isinstance(s, dict) and "clip_id" in s)
        except (ValueError, TypeError):
            pass
    return used


def tidy_reasons(conn) -> dict[int, list[str]]:
    """Clips worth tidying, each with why: a retake, unused by any post for 60 days, its
    project archived. Archived clips aren't suggested again."""
    used = _used_clip_ids(conn)
    archived = {r[0] for r in conn.execute("SELECT id FROM projects WHERE status = 'archived'")}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=UNUSED_DAYS)).isoformat(timespec="seconds")
    out: dict[int, list[str]] = {}
    for r in conn.execute("""SELECT id, quality, usable, ingested_at, project_id FROM clips
                             WHERE status = 'tagged' AND archived_at IS NULL""").fetchall():
        why = []
        verdict = (json.loads(r["quality"]) if r["quality"] else {}).get("verdict")
        if verdict == "retake" or r["usable"] == 0:
            why.append("retake")
        if r["id"] not in used and (r["ingested_at"] or "") < cutoff:
            why.append("unused")
        if r["project_id"] in archived:
            why.append("archived project")
        if why:
            out[r["id"]] = why
    return out


def free_clips(conn, ids: list[int]) -> dict:
    """Delete the server's copy of these clips' originals where OneDrive's archive holds them.
    Returns {freed, bytes, skipped: [{id, why}]}. Never deletes what OneDrive doesn't have."""
    import onedrive
    rows = [r for r in (conn.execute("SELECT * FROM clips WHERE id = ?", (i,)).fetchone() for i in ids) if r]
    skipped, freed, total = [], 0, 0
    if not onedrive.configured():
        return {"freed": 0, "bytes": 0, "skipped": [{"id": r["id"], "why": "OneDrive isn't connected on this machine"} for r in rows]}
    # One listing of the archive for the clips whose place there isn't known yet.
    try:
        listing = onedrive.archive_listing() if any(not r["onedrive_path"] for r in rows) else None
    except onedrive.OneDriveError as e:
        return {"freed": 0, "bytes": 0, "skipped": [{"id": r["id"], "why": str(e)} for r in rows]}
    for r in rows:
        path = Path(r["video_path"] or "")
        if r["freed_at"] or not path.is_file():
            skipped.append({"id": r["id"], "why": "already freed"})
            continue
        if r["status"] != "tagged":
            skipped.append({"id": r["id"], "why": "still being processed"})
            continue
        try:
            remote = onedrive.original_in_archive(r, listing)
        except onedrive.OneDriveError as e:
            skipped.append({"id": r["id"], "why": str(e)})
            continue
        if not remote:
            skipped.append({"id": r["id"], "why": "OneDrive's archive doesn't have its original"})
            continue
        size = path.stat().st_size
        path.unlink()
        conn.execute("UPDATE clips SET freed_at = ?, freed_bytes = ?, onedrive_path = ?, onedrive_size = ? WHERE id = ?",
                     (catalog.now(), size, remote, size, r["id"]))
        conn.commit()
        freed += 1
        total += size
    return {"freed": freed, "bytes": total, "skipped": skipped}


def set_archived(conn, table: str, ids: list[int], on: bool) -> int:
    stamp = catalog.now() if on else None
    marks = ",".join("?" * len(ids))
    n = conn.execute(f"UPDATE {table} SET archived_at = ? WHERE id IN ({marks})", (stamp, *ids)).rowcount
    conn.commit()
    return n


# ── What Review shows ─────────────────────────────────────────────────────────

def summary(conn) -> dict:
    """The disk, what each action would free, the suggestions to tidy."""
    import onedrive
    disk = shutil.disk_usage(settings.ROOT)
    rejected = conn.execute("SELECT * FROM productions WHERE status = 'rejected' AND removed_at IS NULL").fetchall()
    reasons = tidy_reasons(conn)
    clips = {r["id"]: r for r in conn.execute("SELECT * FROM clips WHERE status = 'tagged'").fetchall()}
    sizes = {i: original_bytes(r) for i, r in clips.items()}
    library = sum(sizes.values())
    return {
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "onedrive": onedrive.configured(),
        "rejected": {"count": len(rejected), "bytes": sum(removable_bytes(r) for r in rejected),
                     "each": {r["id"]: removable_bytes(r) for r in rejected}},
        "originals": {"bytes": library, "each": {i: s for i, s in sizes.items() if s}},
        "tidy": [{"id": i, "why": why, "bytes": sizes.get(i, 0), "known": bool(clips[i]["onedrive_path"])}
                 for i, why in reasons.items() if i in clips],
        "freed_total": freed_so_far(),
    }


# ── Routes (owner only) ───────────────────────────────────────────────────────

def _ids() -> list[int]:
    raw = body().get("ids")
    if not isinstance(raw, list) or not raw or len(raw) > 2000:
        raise ValueError("pick at least one")
    return [int(i) for i in raw]


@bp.get("/api/cleanup")
@owner_only
def cleanup_summary():
    response = jsonify(summary(db()))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/production/<int:production_id>/remove")
@owner_only
def remove_one(production_id: int):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        return jsonify({"error": "no such draft"}), 404
    if row["status"] != "rejected":
        return jsonify({"error": "only a rejected draft can be removed"}), 409
    freed = remove_production(db(), row)
    _record("removed rejected draft", 1, freed, g.user["email"])
    return jsonify({"removed": 1, "bytes": freed})


@bp.post("/api/productions/clear-rejected")
@owner_only
def clear_rejected():
    rows = db().execute("SELECT * FROM productions WHERE status = 'rejected' AND removed_at IS NULL").fetchall()
    freed = sum(remove_production(db(), r) for r in rows)
    if rows:
        _record("cleared rejected drafts", len(rows), freed, g.user["email"])
    return jsonify({"removed": len(rows), "bytes": freed})


@bp.post("/api/production/<int:production_id>/archive")
@owner_only
def archive_production(production_id: int):
    row = db().execute("SELECT status FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        return jsonify({"error": "no such draft"}), 404
    on = body().get("archived", True) is not False
    if on and row["status"] not in ("approved", "posted"):
        return jsonify({"error": "only approved or posted drafts are archived"}), 409
    set_archived(db(), "productions", [production_id], on)
    return jsonify({"ok": True, "archived": on})


@bp.post("/api/clips/archive")
@owner_only
def archive_clips():
    try:
        ids = _ids()
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    on = body().get("archived", True) is not False
    n = set_archived(db(), "clips", ids, on)
    log.info("Clean-up: %s %d clip(s) (%s)", "archived" if on else "brought back", n, g.user["email"])
    return jsonify({"ok": True, "count": n, "archived": on})


@bp.post("/api/clips/free")
@owner_only
def free():
    try:
        ids = _ids()
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    result = free_clips(db(), ids)
    if result["freed"]:
        _record("freed clip originals (kept in OneDrive)", result["freed"], result["bytes"], g.user["email"])
    return jsonify(result)
