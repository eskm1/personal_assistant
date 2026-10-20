"""
Clean-up tools for Review (Bryan approved 2026-10-19): keep the Studio machine's disk tidy
without ever losing anything that isn't kept somewhere else.

- Done: Remove a rejected draft (its renders are deleted; the production row stays as the
  record, with its small still, so the planner idea and the history still make sense), or all
  rejected ones at once; Archive an approved or posted draft (hidden from Done; it stays in
  OneDrive's "Studio Ready to post"; undo any time).
- Library: Archive a clip (hidden from the Library, ideas from footage, suggested topics and the
  planner's footage pickers; undo any time); Free up space: delete the server's copy of a clip's
  original video once OneDrive's archive holds it. An original OneDrive never had (uploaded from
  the planner, or from the laptop before the move) is copied there first, into Studio Archive/
  <project>/<post or Uploads>/, and checked (exact size, and OneDrive's fingerprint) before
  anything is deleted. It runs in the background with its progress in the Library's bar, one
  clip at a time, and carries on after a restart. Stills, the light preview, transcript and tags
  stay. If an edit, a carousel still or a Resolve project later needs that original,
  onedrive.ensure_originals() fetches it back.
- "Suggested to tidy": retakes, clips no post has used for 60 days, clips of archived projects.

Deleting is the owner's only (Remove, Clear rejected, Free up space); so is archiving. OneDrive's
own files are never touched (Free up space only adds originals to Studio Archive). Everything
freed is logged (worker.log and logs/cleanup.jsonl).
"""
import json
import logging
import shutil
import threading
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
    # With its files gone there's nothing left to look at: it moves to Archived.
    conn.execute("""UPDATE productions SET removed_at = ?, removed_bytes = COALESCE(removed_bytes, 0) + ?, video_path = NULL,
                        archived_at = COALESCE(archived_at, ?) WHERE id = ?""", (catalog.now(), freed, catalog.now(), row["id"]))
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


# OneDrive problems that would stop every clip the same way: the job stops instead of trying them all.
_STOPPERS = ("Can't reach OneDrive", "sign-in has expired", "isn't connected", "OneDrive is full",
             "rclone isn't installed", "connection settings")


def _free_one(conn, r, listing, say) -> int:
    """Free one clip: its original into OneDrive's archive if OneDrive doesn't have it yet,
    checked there, then the server's copy deleted. Returns the bytes freed; ValueError (why,
    in plain words) when it's left as it was. Never deletes without a checked OneDrive copy."""
    import onedrive
    import previews
    path = Path(r["video_path"] or "")
    if r["freed_at"]:
        if path.is_file() and r["onedrive_path"]:   # recorded, then cut short before the delete
            say("checking")
            onedrive.verify(path, r["onedrive_path"])   # checked again: still there, still the same
            size = path.stat().st_size
            path.unlink()
            return size
        raise ValueError("already freed")
    if not path.is_file():
        raise ValueError("the server has no copy of it")
    if r["status"] != "tagged":
        raise ValueError("still being processed")
    remote = onedrive.original_in_archive(r, listing())
    if remote:
        say("checking")
        try:
            onedrive.verify(path, remote)
        except onedrive.Mismatch:
            remote = None   # same name and size but not the same file: copy ours up as well
    if not remote:
        say("copying")
        remote = onedrive.upload_original(conn, r)
    clip_dir = Path(r["clip_dir"])
    if not any((clip_dir / n).is_file() for n in (previews.NAME, *previews.OLD_NAMES)):
        try:   # Review keeps something to play: once the original's gone it can't be made from it
            say("preview")
            previews.make(path, clip_dir / previews.NAME, background=True)
        except (RuntimeError, OSError) as e:
            log.info("Clean-up: no light copy for clip %s (%s); the still stays", r["id"], e)
    size = path.stat().st_size
    # Recorded first: if the server stops between these two lines, the clip is still marked
    # as kept in OneDrive and the next run finishes the delete.
    conn.execute("UPDATE clips SET freed_at = ?, freed_bytes = ?, onedrive_path = ?, onedrive_size = ? WHERE id = ?",
                 (catalog.now(), size, remote, size, r["id"]))
    conn.commit()
    path.unlink()
    return size + previews.drop_old(clip_dir)


def free_clips(conn, ids: list[int], progress=None, stop=None) -> dict:
    """Free up space for these clips (_free_one each). Returns {freed, bytes, skipped: [{id, why}],
    stopped}. progress(n, total, phase, row) reports each step; stop() True ends it after a clip."""
    import onedrive
    rows = [r for r in (conn.execute("SELECT * FROM clips WHERE id = ?", (i,)).fetchone() for i in ids) if r]
    out = {"freed": 0, "bytes": 0, "skipped": [], "stopped": None}
    if not onedrive.configured():
        out["skipped"] = [{"id": r["id"], "why": "OneDrive isn't connected on this machine"} for r in rows]
        return out
    cache = {}

    def listing():   # one listing of the archive, only if a clip's place there isn't known
        if "all" not in cache:
            cache["all"] = onedrive.archive_listing() if any(not r["onedrive_path"] for r in rows) else []
        return cache["all"]

    for n, r in enumerate(rows, 1):
        if stop and stop():
            out["stopped"] = "stopped"
            out["skipped"] += [{"id": x["id"], "why": "stopped before it"} for x in rows[n - 1:]]
            break
        if progress:
            progress(n, len(rows), "start", r)
        try:
            out["bytes"] += _free_one(conn, r, listing, lambda phase: progress and progress(n, len(rows), phase, r))
            out["freed"] += 1
        except ValueError as e:
            out["skipped"].append({"id": r["id"], "why": str(e)})
        except onedrive.OneDriveError as e:
            out["skipped"].append({"id": r["id"], "why": str(e)})
            log.warning("Clean-up: clip %s left as it was: %s", r["id"], e)
            if any(w in str(e) for w in _STOPPERS):
                out["stopped"] = str(e)
                out["skipped"] += [{"id": x["id"], "why": "not tried: " + str(e)} for x in rows[n:]]
                break
        except OSError as e:
            out["skipped"].append({"id": r["id"], "why": f"couldn't read or delete it ({e.strerror or e})"})
    return out


# ── Free up space as a job: progress in the Library's bar, safe across a restart ──

JOB_FILE = "cleanup_job.json"
_job_lock = threading.Lock()
_job_thread: threading.Thread | None = None
_stop = threading.Event()


def _job_path() -> Path:
    return settings.ROOT / JOB_FILE


def job_status() -> dict | None:
    """The last Free up space job ({state: running | done | stopped, n, total, phase, freed,
    bytes, skipped, ...}), or None."""
    try:
        job = json.loads(_job_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if job.get("state") == "running" and not busy():
        job["state"] = "waiting"   # cut short by a restart; resume() carries on
    return job


def _save_job(job: dict) -> None:
    path = _job_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job), encoding="utf-8")
    tmp.replace(path)


def busy() -> bool:
    return _job_thread is not None and _job_thread.is_alive()


def start_free(ids: list[int], who: str) -> dict:
    """Start Free up space for these clips in the background. ValueError if one is running."""
    global _job_thread
    with _job_lock:
        if busy():
            raise ValueError("Free up space is already running")
        _stop.clear()
        job = {"state": "running", "ids": [int(i) for i in ids], "done": [], "total": len(ids), "n": 0,
               "phase": "start", "name": "", "freed": 0, "bytes": 0, "skipped": [], "by": who,
               "started_at": catalog.now(), "finished_at": None, "stopped": None}
        _save_job(job)
        _job_thread = threading.Thread(target=_run_job, args=(job,), name="free-up-space", daemon=True)
        _job_thread.start()
        return job


def stop_job() -> bool:
    if not busy():
        return False
    _stop.set()
    return True


def resume() -> None:
    """At start: carry on a Free up space job a restart cut short (the clips not done yet)."""
    global _job_thread
    try:
        job = json.loads(_job_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if job.get("state") != "running":
        return
    with _job_lock:
        if busy():
            return
        _stop.clear()
        log.info("Clean-up: carrying on Free up space (%d of %d clips left)",
                 len(job["ids"]) - len(job["done"]), len(job["ids"]))
        _job_thread = threading.Thread(target=_run_job, args=(job,), name="free-up-space", daemon=True)
        _job_thread.start()


def _run_job(job: dict) -> None:
    conn = catalog.connect()
    try:
        todo = [i for i in job["ids"] if i not in set(job["done"])]
        base = len(job["done"])

        def progress(n, total, phase, row):
            job.update(n=base + n, phase=phase, name=row["title"] or row["source_name"] or "")
            _save_job(job)

        result = free_clips(conn, todo, progress=lambda n, t, ph, r: progress(n, t, ph, r), stop=_stop.is_set)
        job["freed"] += result["freed"]
        job["bytes"] += result["bytes"]
        job["skipped"] += result["skipped"]
        job["done"] = job["ids"]
        job.update(state="stopped" if result["stopped"] else "done", stopped=result["stopped"],
                   finished_at=catalog.now(), phase="")
    except Exception as e:   # never leave the job "running" for ever
        log.exception("Clean-up: Free up space stopped")
        job.update(state="stopped", stopped=f"{type(e).__name__}: {e}"[:200], finished_at=catalog.now(), phase="")
    finally:
        conn.close()
        _save_job(job)
    if job["freed"]:
        _record("freed clip originals (kept in OneDrive)", job["freed"], job["bytes"], job.get("by", ""))


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
        "job": _job_view(job_status()),
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
    """Free up space for the picked clips, in the background (GET /api/clips/free for progress)."""
    try:
        ids = _ids()
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    try:
        job = start_free(ids, g.user["email"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True, "job": _job_view(job)}), 202


@bp.get("/api/clips/free")
@owner_only
def free_status():
    response = jsonify({"job": _job_view(job_status())})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/clips/free/stop")
@owner_only
def free_stop():
    """Stop after the clip it's on (nothing half-done: that clip finishes or is left as it was)."""
    return jsonify({"ok": True, "stopping": stop_job()})


def _job_view(job: dict | None) -> dict | None:
    if not job:
        return None
    return {k: job.get(k) for k in ("state", "n", "total", "phase", "name", "freed", "bytes", "skipped",
                                    "stopped", "started_at", "finished_at")}
