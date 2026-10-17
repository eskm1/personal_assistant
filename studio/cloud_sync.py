"""
The laptop side of online planning (cloud/CONTRACT.md, "The laptop").

The team plans on the online planner (Supabase + a static site), so planning
works while this laptop is off. Whenever the worker runs, every
STUDIO_CLOUD_SYNC_SECONDS (30 s) it:
  1. checks in (laptop_status), so the planner can say the laptop is on;
  2. pushes brands.md and the playbook (context) when they change, for the
     planner's Claude prompts;
  3. mirrors the plan (projects, arcs, ideas, shots, takes) into the local
     tables, matched by remote_id: the cloud wins, rows gone online are deleted
     here, and library ideas and other local-only rows are left alone. The
     tagger's shot matching and plan.make keep working on local ids;
  4. pushes a summary and a still of each library clip, and the takes the
     tagger matched (with a still near each take's start);
  5. runs the jobs only the laptop can do (music shortlists with the Epidemic
     key, "Make this"), and keeps each made idea's production status current;
  6. reference reels (CONTRACT.md, "Reference guides"): registers videos dropped
     in Studio Inbox\\References\\Ours / \\Inspiration, analyses new references
     (two per tick at most), drafts a new style guide when a brand's set of
     analysed references changes (or on a "style" job), and writes the approved
     guide into the playbook. If the online database predates these tables, it
     says "run cloud_setup.py --update" and skips this step.

Plain HTTPS to PostgREST (/rest/v1) and Storage (/storage/v1) with the service
key; no extra packages. The key never leaves for anywhere but
STUDIO_SUPABASE_URL (https only, http just for localhost tests), is never
logged, and redirects are never followed. A failed sync is logged once and
retried next tick: it never stops the worker.
"""
import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

import catalog
import settings

log = logging.getLogger("studio.cloud")

TIMEOUT = (10, 30)          # connect, read (seconds)
UPLOAD_TIMEOUT = (10, 90)
PAGE = 1000                 # PostgREST's usual max rows per request
CHUNK = 200                 # rows per bulk upsert
STILL_WIDTH = 360
JOB_STALE_MINUTES = 15      # a laptop job still 'running' this long died with a worker
BUCKET = "thumbs"
VERDICTS = ("good", "check", "retake")

# What the dashboard shows (Plan tab) and --cloud-check prints.
status: dict = {"last_ok": None, "last_error": None, "last_error_at": None, "last_attempt": None,
                "counts": {}, "jobs": [], "note": None, "refs": None, "ref_problems": []}
_tick_lock = threading.Lock()
_last_tick = 0.0
_context_hashes: dict[str, str] = {}
_no_still: set[int] = set()   # remote take ids with no frame to show (don't retry every tick)


class CloudError(RuntimeError):
    pass


# ── Configuration ─────────────────────────────────────────────────────────────

def url_allowed(url: str) -> bool:
    """https anywhere; plain http only to this machine (the local test stack)."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.username or u.password or not u.hostname:
        return False
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))


def enabled() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY and url_allowed(settings.SUPABASE_URL))


def config_problem() -> str | None:
    if not settings.SUPABASE_URL and not settings.SUPABASE_SERVICE_KEY:
        return "not configured (STUDIO_SUPABASE_URL / STUDIO_SUPABASE_SERVICE_KEY are blank)"
    if not settings.SUPABASE_URL:
        return "STUDIO_SUPABASE_URL is blank"
    if not settings.SUPABASE_SERVICE_KEY:
        return "STUDIO_SUPABASE_SERVICE_KEY is blank"
    if not url_allowed(settings.SUPABASE_URL):
        return "STUDIO_SUPABASE_URL must start with https:// (http:// only for localhost)"
    return None


def auth_headers(key: str) -> dict:
    """A legacy service_role JWT (eyJ...) goes in apikey and Authorization; a new
    secret key (sb_secret_...) is not a JWT and goes in apikey only."""
    key = key.strip()
    if key.startswith("eyJ") and key.count(".") == 2:
        return {"apikey": key, "Authorization": f"Bearer {key}"}
    return {"apikey": key}


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _brief(text: str) -> str:
    try:
        data = json.loads(text)
        text = data.get("message") or data.get("error") or text if isinstance(data, dict) else text
    except ValueError:
        pass
    return str(text).strip()[:300]


class Client:
    """PostgREST and Storage with the service key. Every call has a timeout and
    raises CloudError (never with the key in it)."""

    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or settings.SUPABASE_URL).rstrip("/")
        key = key or settings.SUPABASE_SERVICE_KEY
        if not url_allowed(self.url):
            raise CloudError("STUDIO_SUPABASE_URL must start with https:// (http:// only for localhost)")
        if not key:
            raise CloudError("STUDIO_SUPABASE_SERVICE_KEY is blank")
        self.session = requests.Session()
        self.session.headers.update(auth_headers(key))

    def request(self, method: str, path: str, *, params=None, json_body=None, data=None,
                headers=None, timeout=TIMEOUT) -> requests.Response:
        try:
            r = self.session.request(method, self.url + path, params=params, json=json_body, data=data,
                                     headers=headers, timeout=timeout, allow_redirects=False)
        except requests.RequestException as e:
            raise CloudError(f"can't reach Supabase ({type(e).__name__})") from None
        if r.status_code >= 300:
            raise CloudError(f"{method} {path}: HTTP {r.status_code} {_brief(r.text)}")
        return r

    # PostgREST
    def select(self, table: str, params: dict | None = None) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.request("GET", f"/rest/v1/{table}",
                                params={"order": "id", **(params or {}), "limit": PAGE, "offset": offset}).json()
            out += page
            if len(page) < PAGE:
                return out
            offset += PAGE

    def insert(self, table: str, rows: list[dict] | dict, *, on_conflict: str | None = None,
               resolution: str | None = None, returning: bool = True) -> list[dict]:
        prefer = ["return=representation" if returning else "return=minimal"]
        if resolution:
            prefer.append(f"resolution={resolution}")
        r = self.request("POST", f"/rest/v1/{table}", params={"on_conflict": on_conflict} if on_conflict else None,
                         json_body=rows, headers={"Prefer": ",".join(prefer)})
        return r.json() if returning and r.content else []

    def update(self, table: str, filters: dict, values: dict) -> list[dict]:
        return self.request("PATCH", f"/rest/v1/{table}", params=filters, json_body=values,
                            headers={"Prefer": "return=representation"}).json()

    # Storage
    def upload(self, path: str, content: bytes, content_type: str = "image/jpeg") -> None:
        self.request("POST", f"/storage/v1/object/{BUCKET}/{path}", data=content, timeout=UPLOAD_TIMEOUT,
                     headers={"Content-Type": content_type, "x-upsert": "true", "Cache-Control": "3600"})

    def download(self, path: str) -> bytes:
        return self.request("GET", f"/storage/v1/object/{BUCKET}/{path}", timeout=UPLOAD_TIMEOUT).content


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(value) -> str | None:
    """A timestamp Postgres accepts, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


# ── Stills ────────────────────────────────────────────────────────────────────

def _frames(clip_dir) -> list[tuple[float, Path]]:
    """The clip's extracted frames, by time (their names carry it: frame_03_0004.50s.jpg)."""
    out = []
    for f in (Path(clip_dir) / "frames").glob("*.jpg"):
        m = re.search(r"_(\d+(?:\.\d+)?)s\.jpg$", f.name)
        if m:
            out.append((float(m.group(1)), f))
    return sorted(out)


def _middle_frame(clip_dir) -> Path | None:
    frames = _frames(clip_dir)
    return frames[len(frames) // 2][1] if frames else None


def _frame_near(clip_dir, t: float) -> Path | None:
    frames = _frames(clip_dir)
    return min(frames, key=lambda f: abs(f[0] - t))[1] if frames else None


def _still(frame: Path) -> bytes:
    """A small JPEG of the frame (~360 px wide) for the planner."""
    result = subprocess.run(
        [settings.FFMPEG, "-v", "error", "-i", str(frame), "-vf", f"scale={STILL_WIDTH}:-2",
         "-frames:v", "1", "-q:v", "5", "-f", "mjpeg", "pipe:1"],
        capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffmpeg couldn't make a still: {result.stderr.decode(errors='replace')[-200:]}")
    return result.stdout


def _frame_key(frame: Path | None) -> str:
    if frame is None:
        return ""
    st = frame.stat()
    return f"{frame.name}:{st.st_size}:{int(st.st_mtime)}"


# ── Step 1-2: heartbeat and context ──────────────────────────────────────────

def _heartbeat(api: Client) -> None:
    import updater
    v = updater.version()
    api.insert("laptop_status", {
        "id": 1, "last_seen": _now(), "version": f"{v['commit']} {v['date'][:10]}".strip(),
        "info": {"subject": v.get("subject", ""), "sync": status.get("counts", {}), "note": status.get("note")},
    }, on_conflict="id", resolution="merge-duplicates", returning=False)


def _push_context(api: Client) -> int:
    import playbook
    values = {"brands": settings.brands_text(), "playbook": playbook.text()}
    changed = []
    for key, value in values.items():
        digest = hashlib.sha256(value.encode()).hexdigest()
        if _context_hashes.get(key) != digest:
            changed.append((key, value, digest))
    if changed:
        api.insert("context", [{"key": k, "value": v, "updated_at": _now()} for k, v, _ in changed],
                   on_conflict="key", resolution="merge-duplicates", returning=False)
        for key, _, digest in changed:
            _context_hashes[key] = digest
    return len(changed)


# ── Step 3: mirror the plan ──────────────────────────────────────────────────

def _brand(brand: str | None) -> str:
    """A brand Studio makes content for: with one account (settings.SINGLE_BRAND),
    everything from the planner is filed under it."""
    return settings.SINGLE_BRAND or (brand if brand in settings.BRANDS else "urban_makers")


def _safe_folder(folder: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", folder or "").strip(" .")[:80]


def _make_inbox_folder(folder: str) -> None:
    folder = _safe_folder(folder)
    try:
        if folder and settings.INBOX.exists():
            target = settings.INBOX / folder
            if target.resolve().parent == settings.INBOX.resolve():
                target.mkdir(exist_ok=True)
    except OSError as e:
        log.warning("Couldn't make inbox folder %s: %s", folder, e)


def _mirror_table(conn, table: str, remote_rows: list[dict], values, extra: dict | None = None,
                  adopt=None) -> tuple[dict[int, int], int, int]:
    """Insert or update the local copy of each remote row (values(row) -> local
    columns, or None to skip). Returns ({remote id: local id}, inserted, updated)."""
    local = {r["remote_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM {table} WHERE remote_id IS NOT NULL")}
    mapping, inserted, updated = {}, 0, 0
    for row in remote_rows:
        vals = values(row)
        if vals is None:
            continue
        mine = local.get(row["id"])
        if mine is None and adopt:
            mine = adopt(row, vals)  # an unsynced local row that is the same thing
            if mine is not None:
                conn.execute(f"UPDATE {table} SET remote_id = ? WHERE id = ?", (row["id"], mine["id"]))
        if mine is None:
            cols = {**(extra or {}), **vals, "remote_id": row["id"]}
            if "created_at" in cols and cols["created_at"] is None:
                cols["created_at"] = catalog.now()
            cur = conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               list(cols.values()))
            mapping[row["id"]] = cur.lastrowid
            inserted += 1
            continue
        mapping[row["id"]] = mine["id"]
        changed = {k: v for k, v in vals.items() if mine.get(k) != v}
        if changed:
            conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                         [*changed.values(), mine["id"]])
            updated += 1
    return mapping, inserted, updated


def _gone(conn, table: str, remote_rows: list[dict]) -> list[int]:
    ids = {r["id"] for r in remote_rows}
    return [r["id"] for r in conn.execute(f"SELECT id, remote_id FROM {table} WHERE remote_id IS NOT NULL")
            if r["remote_id"] not in ids]


def _in(ids: list[int]) -> str:
    return ",".join("?" * len(ids))


def _delete_shots(conn, shot_ids: list[int]) -> None:
    if shot_ids:
        conn.execute(f"DELETE FROM takes WHERE shot_id IN ({_in(shot_ids)})", shot_ids)
        conn.execute(f"DELETE FROM shots WHERE id IN ({_in(shot_ids)})", shot_ids)


def _delete_ideas(conn, idea_ids: list[int]) -> None:
    if idea_ids:
        _delete_shots(conn, [r[0] for r in conn.execute(
            f"SELECT id FROM shots WHERE idea_id IN ({_in(idea_ids)})", idea_ids)])
        conn.execute(f"DELETE FROM votes WHERE idea_id IN ({_in(idea_ids)})", idea_ids)
        conn.execute(f"DELETE FROM ideas WHERE id IN ({_in(idea_ids)})", idea_ids)


def pull(api: Client, conn) -> dict:
    """Mirror the online plan into the local tables. Returns the remote rows
    (for the push steps) and what changed."""
    remote = {t: api.select(t) for t in ("projects", "arcs", "ideas", "shots", "takes")}
    remote["clip_ids"] = {r["id"] for r in api.select("clips", {"select": "id"})}
    stats = {}
    try:
        projects, ins, upd = _mirror_table(
            conn, "projects", remote["projects"],
            lambda r: {"name": r["name"], "brand": _brand(r["brand"]), "folder": _safe_folder(r["folder"]) or r["name"],
                       "brief": r["brief"], "status": r["status"]},
            extra={"created_at": None})
        stats["projects"] = [ins, upd]
        if ins or upd:  # an inbox subfolder per project, as the dashboard makes
            for r in remote["projects"]:
                if r["status"] == "active":
                    _make_inbox_folder(r["folder"] or r["name"])
        arcs, *stats["arcs"] = _mirror_table(
            conn, "arcs", remote["arcs"],
            lambda r: None if r["project_id"] not in projects else {
                "project_id": projects[r["project_id"]], "title": r["title"], "purpose": r["purpose"],
                "position": r["position"]},
            extra={"created_at": None})

        made = {r["remote_id"]: r["production_id"] for r in conn.execute(
            "SELECT remote_id, production_id FROM ideas WHERE remote_id IS NOT NULL")}

        def idea_values(r):
            if r["project_id"] not in projects:
                return None
            status_ = r["status"]
            if status_ == "new" and made.get(r["id"]):
                status_ = "made"  # made on this laptop; the push below tells the cloud
            return {"project_id": projects[r["project_id"]], "arc_id": arcs.get(r["arc_id"]),
                    "title": r["title"], "brand": _brand(r["brand"]), "format": r["format"], "hook": r["hook"],
                    "purpose": r["purpose"], "cta": r["cta"], "caption": r["caption"],
                    "est_seconds": r["est_seconds"], "music_plan": json.dumps(r["music"] or {}),
                    "decision": r["decision"], "status": status_, "position": r["position"],
                    "updated_at": r["updated_at"]}
        ideas, *stats["ideas"] = _mirror_table(
            conn, "ideas", remote["ideas"], idea_values,
            extra={"batch": "plan", "origin": "plan", "angle": "", "beats": "[]", "segments": "[]",
                   "wishlist": "[]", "created_at": None})

        shots, *stats["shots"] = _mirror_table(
            conn, "shots", remote["shots"],
            lambda r: None if r["idea_id"] not in ideas else {
                "idea_id": ideas[r["idea_id"]], "position": r["position"], "kind": r["kind"],
                "framing": r["framing"], "camera": r["camera"], "description": r["description"],
                "line": r["line"], "on_screen": r["on_screen"], "seconds": r["seconds"], "why": r["why"],
                "filmed_at": r["filmed_at"]},
            extra={"created_at": None})

        clips = {r[0] for r in conn.execute("SELECT id FROM clips")}

        def adopt_take(r, vals):
            row = conn.execute("SELECT * FROM takes WHERE shot_id = ? AND clip_id = ? AND remote_id IS NULL",
                               (vals["shot_id"], vals["clip_id"])).fetchone()
            return dict(row) if row else None
        takes, *stats["takes"] = _mirror_table(
            conn, "takes", remote["takes"],
            lambda r: None if r["shot_id"] not in shots or r["clip_id"] not in clips else {
                "shot_id": shots[r["shot_id"]], "clip_id": r["clip_id"], "start": r["start"], "end": r["end"],
                "source": r["source"], "note": r["note"]},
            extra={"created_at": None}, adopt=adopt_take)

        # Rows deleted online, children first.
        gone = {t: _gone(conn, t, remote[t]) for t in ("takes", "shots", "ideas", "arcs", "projects")}
        if gone["takes"]:
            conn.execute(f"DELETE FROM takes WHERE id IN ({_in(gone['takes'])})", gone["takes"])
        _delete_shots(conn, gone["shots"])
        _delete_ideas(conn, gone["ideas"])
        if gone["arcs"]:
            conn.execute(f"UPDATE ideas SET arc_id = NULL WHERE arc_id IN ({_in(gone['arcs'])})", gone["arcs"])
            conn.execute(f"DELETE FROM arcs WHERE id IN ({_in(gone['arcs'])})", gone["arcs"])
        if gone["projects"]:
            p = gone["projects"]
            _delete_ideas(conn, [r[0] for r in conn.execute(
                f"SELECT id FROM ideas WHERE project_id IN ({_in(p)}) AND origin = 'plan'", p)])
            conn.execute(f"DELETE FROM arcs WHERE project_id IN ({_in(p)})", p)
            conn.execute(f"UPDATE clips SET project_id = NULL WHERE project_id IN ({_in(p)})", p)
            conn.execute(f"DELETE FROM projects WHERE id IN ({_in(p)})", p)
        # A clip removed online (say, a reset database) is pushed again.
        conn.execute("UPDATE clips SET cloud_hash = NULL WHERE cloud_hash IS NOT NULL AND id NOT IN ("
                     + (",".join(str(int(i)) for i in remote["clip_ids"]) or "NULL") + ")")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    stats["deleted"] = {t: len(v) for t, v in gone.items() if v}
    remote["maps"] = {"projects": projects, "arcs": arcs, "ideas": ideas, "shots": shots, "takes": takes}
    return {"remote": remote, "stats": stats}


# ── Step 4: push footage ─────────────────────────────────────────────────────

def _project_remote_ids(conn) -> dict[int, int]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, remote_id FROM projects WHERE remote_id IS NOT NULL")}


def _clip_summary(r, project_remote: dict[int, int]) -> dict:
    q = json.loads(r["quality"] or "{}")
    return {
        "id": r["id"], "title": (r["title"] or r["source_name"] or "")[:300], "recorded_at": _ts(r["recorded_at"]),
        "duration": r["duration"], "project_id": project_remote.get(r["project_id"]),
        "verdict": q.get("verdict") if q.get("verdict") in VERDICTS else None,
        "flags": [{"level": f.get("level", ""), "text": f.get("text", ""), "tip": f.get("tip", "")}
                  for f in q.get("flags", [])],
    }


def _digest(summary: dict, still_key: str) -> str:
    return hashlib.sha256((json.dumps(summary, sort_keys=True) + still_key).encode()).hexdigest()[:32]


def push_clips(api: Client, conn) -> int:
    """Summaries (and stills, when new or changed) of finished library clips."""
    project_remote = _project_remote_ids(conn)
    batch = []
    for r in conn.execute("SELECT * FROM clips WHERE status IN ('tagged', 'transcribed') ORDER BY id").fetchall():
        summary = _clip_summary(r, project_remote)
        frame = _middle_frame(r["clip_dir"])
        frame_key = _frame_key(frame)
        uploaded = r["cloud_thumb"]
        summary["thumb_path"] = f"clips/{r['id']}.jpg" if (uploaded or frame) else None
        if _digest(summary, frame_key) == r["cloud_hash"]:
            continue  # unchanged since the last push
        if frame and frame_key != uploaded:
            try:
                api.upload(f"clips/{r['id']}.jpg", _still(frame))
                uploaded = frame_key
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                if isinstance(e, CloudError) and "can't reach" in str(e):
                    raise
                log.warning("No still for clip %s: %s", r["id"], e)
                if not uploaded:
                    summary["thumb_path"] = None
        # What was actually sent: a failed still doesn't match next time, so it's retried.
        batch.append((summary, _digest(summary, uploaded or ""), uploaded))
    for i in range(0, len(batch), CHUNK):
        part = batch[i:i + CHUNK]
        api.insert("clips", [s for s, _, _ in part], on_conflict="id", resolution="merge-duplicates",
                   returning=False)
        stamp = catalog.now()
        conn.executemany("UPDATE clips SET cloud_hash = ?, cloud_thumb = ?, cloud_pushed_at = ? WHERE id = ?",
                         [(d, u, stamp, s["id"]) for s, d, u in part])
        conn.commit()
    return len(batch)


def push_takes(api: Client, conn) -> list[dict]:
    """Takes made on this laptop (the tagger's matches) that the cloud doesn't
    have yet. Returns the remote rows created."""
    rows = conn.execute(
        """SELECT t.*, s.remote_id AS shot_remote FROM takes t JOIN shots s ON s.id = t.shot_id
           JOIN clips c ON c.id = t.clip_id
           WHERE t.remote_id IS NULL AND s.remote_id IS NOT NULL AND c.cloud_hash IS NOT NULL""").fetchall()
    if not rows:
        return []
    created = api.insert("takes", [{"shot_id": r["shot_remote"], "clip_id": r["clip_id"], "start": r["start"],
                                    "end": r["end"], "source": r["source"], "note": r["note"] or ""} for r in rows],
                         on_conflict="shot_id,clip_id", resolution="ignore-duplicates")
    by_key = {(r["shot_remote"], r["clip_id"]): r["id"] for r in rows}
    for c in created:  # a duplicate (attached online meanwhile) is linked by the next pull
        local_id = by_key.get((c["shot_id"], c["clip_id"]))
        if local_id:
            conn.execute("UPDATE takes SET remote_id = ? WHERE id = ?", (c["id"], local_id))
    conn.commit()
    return created


def push_take_stills(api: Client, conn, remote_takes: list[dict]) -> int:
    """Upload takes/<id>.jpg (the frame nearest the take's start) for every take
    online without one: the tagger's and the ones attached in the planner."""
    n = 0
    for t in remote_takes:
        if t.get("thumb_path") or t["id"] in _no_still:
            continue
        clip = conn.execute("SELECT clip_dir FROM clips WHERE id = ?", (t["clip_id"],)).fetchone()
        frame = _frame_near(clip["clip_dir"], t.get("start") or 0) if clip else None
        if frame is None:
            _no_still.add(t["id"])
            continue
        try:
            still = _still(frame)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            log.warning("No still for take %s: %s", t["id"], e)
            _no_still.add(t["id"])
            continue
        path = f"takes/{t['id']}.jpg"
        api.upload(path, still)
        api.update("takes", {"id": f"eq.{t['id']}"}, {"thumb_path": path})
        n += 1
    return n


# ── Step 5: jobs and production status ───────────────────────────────────────

def _production(conn, production_id: int) -> dict | None:
    p = conn.execute("SELECT id, status, version, updated_at FROM productions WHERE id = ?",
                     (production_id,)).fetchone()
    return dict(p) if p else None


def _finish(api: Client, job: dict, result=None, error: str | None = None) -> None:
    api.update("jobs", {"id": f"eq.{job['id']}"},
               {"state": "failed" if error else "done", "result": result, "error": error})


def run_jobs(api: Client, conn, idea_map: dict[int, int]) -> list[str]:
    """Claim and run queued laptop jobs. Returns a line per job run."""
    import plan
    stale = (datetime.now(timezone.utc) - timedelta(minutes=JOB_STALE_MINUTES)).isoformat()
    api.update("jobs", {"runner": "eq.laptop", "state": "eq.running", "started_at": f"lt.{stale}"},
               {"state": "queued", "started_at": None})
    done = []
    for job in api.select("jobs", {"runner": "eq.laptop", "state": "eq.queued", "kind": "neq.style"}):
        local_id = idea_map.get(job["idea_id"])
        if local_id is None:
            created = _ts(job["created_at"])
            young = created and datetime.fromisoformat(created) > datetime.now(timezone.utc) - timedelta(minutes=2)
            if young:
                continue  # the idea arrives with the next pull
        # Claim it: only one worker gets the row back.
        claimed = api.update("jobs", {"id": f"eq.{job['id']}", "state": "eq.queued"},
                             {"state": "running", "started_at": _now()})
        if not claimed:
            continue
        log.info("Cloud job %s: %s for idea %s", job["id"], job["kind"], job["idea_id"])
        try:
            if local_id is None:
                raise RuntimeError("the laptop doesn't have this idea (was it deleted?)")
            if job["kind"] == "music":
                music_plan = plan.shortlist_music(conn, local_id)
                api.update("ideas", {"id": f"eq.{job['idea_id']}"}, {"music": music_plan})
                _finish(api, job, music_plan)
            elif job["kind"] == "make":
                caption = (job.get("payload") or {}).get("caption")
                production_id = plan.make(conn, local_id, caption if isinstance(caption, str) else None)
                api.update("ideas", {"id": f"eq.{job['idea_id']}"},
                           {"status": "made", "production": _production(conn, production_id)})
                _finish(api, job, {"production_id": production_id})
            else:
                raise RuntimeError(f"the laptop doesn't run {job['kind']} jobs")
            done.append(f"{job['kind']} #{job['id']}: done")
        except CloudError:
            raise  # offline: the stale check puts it back in the queue
        except Exception as e:
            if isinstance(e, (RuntimeError, KeyError, ValueError)):
                log.warning("Cloud job %s failed: %s", job["id"], e)
            else:
                log.exception("Cloud job %s failed", job["id"])
            message = str(e) or type(e).__name__
            _finish(api, job, error=message[:1000])
            done.append(f"{job['kind']} #{job['id']}: failed ({message[:100]})")
    return done


def push_productions(api: Client, conn, remote_ideas: list[dict]) -> int:
    """Keep ideas.production (draft ready, approved, posted...) current online."""
    by_id = {r["id"]: r for r in remote_ideas}
    n = 0
    for idea in conn.execute("SELECT remote_id, production_id FROM ideas WHERE remote_id IS NOT NULL "
                             "AND production_id IS NOT NULL").fetchall():
        remote = by_id.get(idea["remote_id"])
        prod = _production(conn, idea["production_id"])
        if remote is None or prod is None:
            continue
        values = {}
        if remote.get("production") != prod:
            values["production"] = prod
        if remote["status"] == "new":
            values["status"] = "made"
        if values:
            api.update("ideas", {"id": f"eq.{idea['remote_id']}"}, values)
            n += 1
    return n


# ── Step 6: reference reels and the style guide ──────────────────────────────

REF_SIDES = {"ours": "Ours", "inspiration": "Inspiration"}   # inbox subfolders of Studio Inbox\References
REFS_PER_TICK = 2            # analyses per tick, so ingest isn't starved
REF_TICK_BUDGET = 150        # seconds: no new analysis starts after the tick has run this long
REF_RETRY_HOURS = 24         # a failed analysis is retried after this long
REF_STALE_MINUTES = 30       # an analysis still 'running' this long died with a worker
SCHEMA_RECHECK_SECONDS = 600
NEEDS_UPDATE = "Online database needs an update: run cloud_setup.py --update"
_refs_missing_until = 0.0    # the reference tables weren't there; don't ask again before this
DRAFT_RETRY_SECONDS = 3600   # an automatic draft that failed is retried after this long (a job retries now)
_draft_failed: dict[str, tuple[list[int], float]] = {}
STATE_FILE = "cloud_state.json"


def _missing_table(e: Exception) -> bool:
    text = str(e)
    return isinstance(e, CloudError) and ("PGRST205" in text or "Could not find the table" in text
                                          or ("HTTP 404" in text and "/rest/v1/" in text))


def _state() -> dict:
    try:
        return json.loads((settings.ROOT / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(values: dict) -> None:
    path = settings.ROOT / STATE_FILE
    data = {**_state(), **values}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)




def _title_from(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()[:200] or stem


def reference_intake(api: Client, refs: list[dict]) -> list[str]:
    """Videos dropped in Studio Inbox\\References\\Ours or \\Inspiration become
    references (brand urban_makers); the file moves to Studio\\References so the
    inbox stays tidy. Dropping a file with a name already in use replaces that
    reference's video and analyses it again."""
    store = settings.REFERENCES
    by_name = {r["file_name"]: r for r in refs if r.get("file_name")}
    added = []
    for side, folder_name in REF_SIDES.items():
        folder = settings.REFERENCE_INBOX / folder_name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in settings.VIDEO_EXTS
                           and not p.name.startswith((".", "~")))
        except OSError as e:
            log.warning("Couldn't read %s: %s", folder, e)
            continue
        for f in files:
            try:
                st = f.stat()
                if st.st_size == 0 or time.time() - st.st_mtime < settings.STABLE_SECONDS:
                    continue  # still syncing
                with open(f, "rb") as fh:
                    fh.read(1)
            except OSError:
                continue
            name = f.name[:200]
            store.mkdir(parents=True, exist_ok=True)
            row = by_name.get(name)
            if row is None:
                row = api.insert("reference_videos", {"brand": "urban_makers", "side": side, "file_name": name,
                                                      "title": _title_from(name), "added_by": None})[0]
                by_name[name] = row
                refs.append(row)
                added.append(f"{side}: {name}")
            else:
                if row["side"] != side or row["status"] != "active":
                    api.update("reference_videos", {"id": f"eq.{row['id']}"}, {"side": side, "status": "active"})
                api.update("reference_analysis", {"reference_id": f"eq.{row['id']}"},
                           {"state": "pending", "error": None})
                added.append(f"{side}: {name} (replaced)")
            f.replace(store / name)
            log.info("Reference added from the inbox: %s (%s)", name, side)
    return added


def _due(analysis: dict, now: datetime) -> bool:
    """Should this reference be analysed (again) now?"""
    import style_study
    state = analysis["state"]
    if state == "pending":
        return True
    updated = _ts(analysis.get("updated_at"))
    age = now - datetime.fromisoformat(updated) if updated else timedelta(days=999)
    if state == "failed":
        return age > timedelta(hours=REF_RETRY_HOURS) or (analysis.get("analyser") or 0) < style_study.ANALYSER
    if state == "done":
        return (analysis.get("analyser") or 0) < style_study.ANALYSER
    return state == "running" and age > timedelta(minutes=REF_STALE_MINUTES)


def analyse_reference(api: Client, ref: dict, analysis: dict, client=None) -> str:
    """Claim one reference's analysis, run it and write the result. Returns a line for the log."""
    import style_study
    claimed = api.update("reference_analysis", {"reference_id": f"eq.{ref['id']}", "state": f"eq.{analysis['state']}"},
                         {"state": "running", "error": None})
    if not claimed:
        return ""
    name = ref.get("title") or ref.get("url") or ref.get("file_name")
    work = settings.REFERENCES / ".work" / str(ref["id"])
    try:
        if ref.get("url"):
            source = ref["url"]
        else:
            source = settings.REFERENCES / ref["file_name"]
            if not source.exists():
                raise RuntimeError(f"{ref['file_name']} isn't in Studio\\References on the laptop any more: "
                                   "drop it in Studio Inbox\\References again")
        result = style_study.analyse(source, work / "stills", download_dir=work, client=client,
                                     ref=f"ref:{ref['id']}")
        paths = []
        for n, still in enumerate(result["stills"], 1):
            path = f"refs/{ref['id']}/{n}.jpg"
            api.upload(path, Path(still).read_bytes())
            paths.append(path)
        old = set(claimed[0].get("stills") or [])
        stale = sorted(old - set(paths))
        if stale:  # a shorter strip than last time: remove the extras
            try:
                api.request("DELETE", f"/storage/v1/object/{BUCKET}", json_body={"prefixes": stale})
            except CloudError as e:
                log.warning("Couldn't remove old stills of reference %s: %s", ref["id"], e)
        done = api.update("reference_analysis", {"reference_id": f"eq.{ref['id']}", "state": "eq.running"},
                          {"state": "done", "measured": result["measured"], "look": result["look"], "stills": paths,
                           "error": None, "analyser": style_study.ANALYSER, "analysed_at": _now()})
        m = result["measured"]
        log.info("Reference %s analysed: %s: %.0fs, %s cuts, %d stills", ref["id"], name, m["duration"], m["cuts"],
                 len(paths))
        return f"reference #{ref['id']}: analysed" + ("" if done else " (changed meanwhile: again next tick)")
    except CloudError as e:
        if "can't reach" in str(e):
            raise  # offline: the stale check puts it back
        message = f"Couldn't save the analysis: {e}"
    except Exception as e:
        if isinstance(e, RuntimeError):
            log.warning("Reference %s (%s) not analysed: %s", ref["id"], name, e)
        else:
            log.exception("Reference %s (%s) not analysed", ref["id"], name)
        message = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    api.update("reference_analysis", {"reference_id": f"eq.{ref['id']}", "state": "eq.running"},
               {"state": "failed", "error": (message or "failed")[:1000], "analyser": style_study.ANALYSER})
    return f"reference #{ref['id']}: failed ({message[:100]})"


def _guide_inputs(refs: list[dict], analyses: dict[int, dict], brand: str) -> list[dict]:
    """The brand's current set: active references whose analysis is done, with their numbers."""
    out = []
    for r in refs:
        a = analyses.get(r["id"])
        if r["brand"] == brand and r["status"] == "active" and a and a["state"] == "done" and a.get("measured"):
            out.append({**r, "measured": a["measured"], "look": a.get("look")})
    return sorted(out, key=lambda r: r["id"])


def draft_style_guide(api: Client, brand: str, refs: list[dict], analyses: dict[int, dict],
                      guides: list[dict], client=None) -> dict:
    """Draft a new style guide for a brand from its current set. Returns the inserted row."""
    import style_study
    current = _guide_inputs(refs, analyses, brand)
    if not current:
        raise RuntimeError(f"No analysed references for {style_study.BRAND_NAMES.get(brand, brand)} yet: "
                           "add some on the References tab (the laptop analyses them first)")
    mine = [g for g in guides if g["brand"] == brand]
    active = next((g for g in mine if g["status"] == "active"), None)
    ids = [r["id"] for r in current]
    before = set(active["reference_ids"]) if active else set()
    by_id = {r["id"]: r for r in refs}
    added = [r for r in current if r["id"] not in before]
    removed_ids = sorted(before - set(ids))
    removed = [by_id.get(i) or {"id": i, "title": f"reference #{i} (deleted)"} for i in removed_ids]
    version = max((g["version"] for g in mine), default=0) + 1
    result = style_study.draft_guide(brand, current, active, added, removed, client=client,
                                     ref=f"guide:{brand}:v{version}")
    row = api.insert("style_guides", {
        "brand": brand, "version": version, "status": "draft", "content": result["content"],
        "report": result["report"], "changes": result["changes"], "reference_ids": ids,
        "added_ids": [r["id"] for r in added], "removed_ids": removed_ids, "usage": result["usage"]})[0]
    guides.append(row)
    log.info("Style guide v%s drafted for %s (%d references, %d added, %d retired; US$%.2f)", version, brand,
             len(ids), len(added), len(removed_ids), result["usage"]["cost_usd"])
    return row


def apply_active_guide(guides: list[dict]) -> str | None:
    """Write the active Urban Makers guide into the playbook when it changed. Returns a line, or None."""
    import style_study
    active = next((g for g in guides if g["brand"] == "urban_makers" and g["status"] == "active"), None)
    if not active:
        return None  # nothing approved yet: the playbook stays as it is
    digest = hashlib.sha256(active["content"].encode()).hexdigest()[:16]
    applied = _state().get("applied_guide") or {}
    if applied.get("id") == active["id"] and applied.get("hash") == digest:
        return None
    when = (_ts(active.get("approved_at")) or _now())[:10]
    style_study.apply_section(active["content"], f"Style guide v{active['version']} (Urban Makers), approved {when}, "
                                                 "from the planner's References.")
    _save_state({"applied_guide": {"id": active["id"], "version": active["version"], "hash": digest,
                                   "applied_at": _now()}})
    log.info("Style guide v%s is in the playbook now", active["version"])
    return f"style guide v{active['version']} applied to the playbook"


def sync_references(api: Client, started: float, client=None) -> dict:
    """Intake, analyses, drafts and applying, each step on its own: one failing
    never stops the others or the rest of the sync. Returns counts."""
    global _refs_missing_until
    out: dict = {}
    if time.time() < _refs_missing_until:
        return {"skipped": "database needs an update"}
    try:
        refs = api.select("reference_videos")
        analyses = {a["reference_id"]: a for a in api.select("reference_analysis", {"order": "reference_id"})}
        guides = api.select("style_guides", {"order": "version.desc"})
    except CloudError as e:
        if not _missing_table(e):
            raise
        if status.get("note") != NEEDS_UPDATE:
            log.warning("%s (the reference guide tables aren't online yet; checking again in 10 minutes)",
                        NEEDS_UPDATE)
        status["note"] = NEEDS_UPDATE
        _refs_missing_until = time.time() + SCHEMA_RECHECK_SECONDS
        return {"skipped": "database needs an update"}
    if status.get("note") == NEEDS_UPDATE:
        status["note"] = None
        log.info("Reference guide tables found online")
    lines: list[str] = []
    problems: list[str] = []

    def step(name, fn):
        try:
            return fn()
        except Exception as e:
            message = str(e) if isinstance(e, CloudError) else f"{type(e).__name__}: {e}"
            problems.append(f"{name}: {message[:200]}")
            if not isinstance(e, (CloudError, RuntimeError)):
                log.exception("Cloud sync, %s failed", name)
            return None

    # Folder intake
    intake = step("reference intake", lambda: reference_intake(api, refs)) or []
    if intake:
        out["refs_added"] = len(intake)
        analyses = {a["reference_id"]: a for a in api.select("reference_analysis", {"order": "reference_id"})}

    # Analyses (active references only; at most REFS_PER_TICK, within the tick's time budget)
    now = datetime.now(timezone.utc)
    due = [r for r in refs if r["status"] == "active" and analyses.get(r["id"]) and _due(analyses[r["id"]], now)]
    due.sort(key=lambda r: (analyses[r["id"]]["state"] != "pending", r["id"]))
    for r in due[:REFS_PER_TICK]:
        if time.time() - started > REF_TICK_BUDGET:
            break
        line = step(f"reference #{r['id']}", lambda r=r: analyse_reference(api, r, analyses[r["id"]], client))
        if line:
            lines.append(line)
            out["analysed"] = out.get("analysed", 0) + 1
    if out.get("analysed"):
        analyses = {a["reference_id"]: a for a in api.select("reference_analysis", {"order": "reference_id"})}

    # Style jobs ("Update style guide"): draft even when the set didn't change
    drafted: set[str] = set()

    def run_style_jobs():
        for job in api.select("jobs", {"runner": "eq.laptop", "state": "eq.queued", "kind": "eq.style"}):
            claimed = api.update("jobs", {"id": f"eq.{job['id']}", "state": "eq.queued"},
                                 {"state": "running", "started_at": _now()})
            if not claimed:
                continue
            brand = (job.get("payload") or {}).get("brand") or "urban_makers"
            try:
                if brand not in settings.BRANDS:
                    raise RuntimeError(f"unknown brand {brand!r}")
                row = draft_style_guide(api, brand, refs, analyses, guides, client)
                drafted.add(brand)
                _finish(api, job, {"guide_id": row["id"], "version": row["version"]})
                lines.append(f"style #{job['id']}: v{row['version']} drafted")
            except CloudError as e:
                if "can't reach" in str(e):
                    raise
                _finish(api, job, error=str(e)[:1000])
                lines.append(f"style #{job['id']}: failed ({str(e)[:100]})")
            except Exception as e:
                if not isinstance(e, RuntimeError):
                    log.exception("Style job %s failed", job["id"])
                message = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
                _finish(api, job, error=message[:1000])
                lines.append(f"style #{job['id']}: failed ({message[:100]})")
    step("style jobs", run_style_jobs)

    # Automatic drafts: when a brand's set of analysed references differs from its newest guide's
    def auto_drafts():
        for brand in settings.BRANDS:
            if brand in drafted:
                continue
            if any(r["brand"] == brand and r["status"] == "active" and analyses.get(r["id"])
                   and (analyses[r["id"]]["state"] in ("pending", "running") or _due(analyses[r["id"]], now))
                   for r in refs):
                continue  # wait until every analysis is in
            ids = [r["id"] for r in _guide_inputs(refs, analyses, brand)]
            newest = next((g for g in guides if g["brand"] == brand), None)  # by version, any status
            if not ids or (newest and sorted(newest["reference_ids"]) == ids):
                continue
            failed = _draft_failed.get(brand)
            if failed and failed[0] == ids and time.time() - failed[1] < DRAFT_RETRY_SECONDS:
                continue  # it failed for this set a moment ago: don't pay for it every 30 s
            try:
                row = draft_style_guide(api, brand, refs, analyses, guides, client)
            except Exception:
                _draft_failed[brand] = (ids, time.time())
                raise
            _draft_failed.pop(brand, None)
            lines.append(f"style guide v{row['version']} drafted ({brand})")
    step("style guide draft", auto_drafts)

    # The approved guide into the playbook
    applied = step("applying the style guide", lambda: apply_active_guide(
        api.select("style_guides", {"order": "version.desc", "status": "eq.active"})))
    if applied:
        lines.append(applied)
        out["applied"] = 1
        step("context", lambda: _push_context(api))

    status["refs"] = _refs_summary(refs, analyses, guides)
    if lines:
        out["lines"] = lines
    if problems:  # logged once each; the status (and --cloud-check) keep them
        for p in problems:
            if p not in status.get("ref_problems", []):
                log.warning("Cloud sync: %s", p)
    status["ref_problems"] = problems
    return out


def _refs_summary(refs: list[dict], analyses: dict[int, dict], guides: list[dict]) -> dict:
    active = [r for r in refs if r["status"] == "active"]
    states = [analyses.get(r["id"], {}).get("state") for r in active]
    um = [g for g in guides if g["brand"] == "urban_makers"]
    live = next((g for g in um if g["status"] == "active"), None)
    draft = next((g for g in um if g["status"] == "draft"), None)
    return {"references": len(active), "ours": sum(r["side"] == "ours" for r in active),
            "retired": len(refs) - len(active), "waiting": states.count("pending") + states.count("running"),
            "failed": states.count("failed"), "guide_version": live["version"] if live else None,
            "draft_version": draft["version"] if draft else None}


# ── The tick ──────────────────────────────────────────────────────────────────

def _file_online(api: Client) -> int:
    """With one account (settings.SINGLE_BRAND), whatever the planner holds under
    another one (older ideas, an idea Claude or a teammate filed as personal) is
    filed under it. Returns how many rows changed."""
    brand = settings.SINGLE_BRAND
    if not brand:
        return 0
    n = 0
    for table in ("projects", "ideas", "reference_videos"):
        try:
            n += len(api.update(table, {"brand": f"neq.{brand}"}, {"brand": brand}))
        except CloudError:
            if table != "reference_videos":  # that one waits for cloud_setup.py --update
                raise
    return n


def sync_once(conn, api: Client | None = None) -> dict:
    """One full sync. Raises on failure (tick() catches)."""
    api = api or Client()
    started = time.time()
    _heartbeat(api)
    counts = {"context": _push_context(api)}
    counts["refiled"] = _file_online(api)
    pulled = pull(api, conn)
    remote, maps = pulled["remote"], pulled["remote"]["maps"]
    counts["pulled"] = pulled["stats"]
    counts["clips_pushed"] = push_clips(api, conn)
    new_takes = push_takes(api, conn)
    counts["takes_pushed"] = len(new_takes)
    counts["stills"] = push_take_stills(api, conn, remote["takes"] + new_takes)
    jobs = run_jobs(api, conn, maps["ideas"])
    counts["jobs"] = len(jobs)
    counts["productions"] = push_productions(api, conn, remote["ideas"])
    try:  # reference reels and the style guide: never stops the rest of the sync
        refs = sync_references(api, started)
    except Exception as e:
        message = str(e) if isinstance(e, CloudError) else f"{type(e).__name__}: {e}"
        if [message] != status.get("ref_problems"):
            log.warning("Cloud sync, references failed (will retry): %s", message)
        status["ref_problems"] = [message]
        refs = {}
    jobs += refs.pop("lines", [])
    counts["refs"] = {k: v for k, v in refs.items() if k != "skipped"}
    counts["online"] = {"projects": len(remote["projects"]), "ideas": len(remote["ideas"]),
                        "shots": len(remote["shots"]), "takes": len(remote["takes"]) + len(new_takes),
                        "clips": conn.execute("SELECT COUNT(*) FROM clips WHERE cloud_hash IS NOT NULL").fetchone()[0]}
    counts["seconds"] = round(time.time() - started, 1)
    if jobs:
        status["jobs"] = (jobs + status.get("jobs", []))[:10]
    return counts


def tick(conn, force: bool = False) -> dict:
    """Called from the worker loop: sync if it's time. Never raises."""
    global _last_tick
    if not enabled():
        return status
    if not force and time.time() - _last_tick < settings.CLOUD_SYNC_SECONDS:
        return status
    if not _tick_lock.acquire(blocking=False):
        return status
    try:
        _last_tick = time.time()
        status["last_attempt"] = _now()
        counts = sync_once(conn)
        changed = {k: v for k, v in counts.items() if v and k not in ("online", "seconds", "pulled")}
        pulled = {k: v for k, v in counts["pulled"].items() if v and (k == "deleted" or any(v))}
        if changed or pulled:
            log.info("Cloud sync: %s", json.dumps({**changed, **pulled}))
        if status["last_error"]:
            log.info("Cloud sync working again")
        status.update(last_ok=_now(), last_error=None, last_error_at=None, counts=counts)
    except Exception as e:
        message = str(e) if isinstance(e, CloudError) else f"{type(e).__name__}: {e}"
        if message != status["last_error"]:  # once, not every 30 s
            if isinstance(e, CloudError):
                log.warning("Cloud sync failed (will retry): %s", message)
            else:
                log.exception("Cloud sync failed (will retry)")
        status.update(last_error=message[:500], last_error_at=_now())
    finally:
        _tick_lock.release()
    return status


def dashboard_info() -> dict | None:
    """For the dashboard's Plan tab: where planning lives now, and how sync is going."""
    if not enabled():
        return None
    online = (status.get("counts") or {}).get("online", {})
    return {"planner_url": settings.PLANNER_URL if url_allowed(settings.PLANNER_URL or "") else "",
            "last_ok": status["last_ok"], "last_error": status["last_error"],
            "last_error_at": status["last_error_at"], "online": online, "jobs": status.get("jobs", [])[:5],
            "note": status.get("note"), "references": status.get("refs")}


# ── Command line: --cloud-check and --cloud-import ───────────────────────────

def _unsynced(conn) -> dict:
    return {
        "projects": conn.execute("SELECT COUNT(*) FROM projects WHERE remote_id IS NULL").fetchone()[0],
        "arcs": conn.execute("SELECT COUNT(*) FROM arcs WHERE remote_id IS NULL").fetchone()[0],
        "ideas": conn.execute("SELECT COUNT(*) FROM ideas WHERE origin = 'plan' AND remote_id IS NULL "
                              "AND project_id IS NOT NULL").fetchone()[0],
        "shots": conn.execute("SELECT COUNT(*) FROM shots s JOIN ideas i ON i.id = s.idea_id "
                              "WHERE s.remote_id IS NULL AND i.origin = 'plan'").fetchone()[0],
        "takes": conn.execute("SELECT COUNT(*) FROM takes WHERE remote_id IS NULL").fetchone()[0],
    }


def check(conn) -> int:
    """Print the connection and what the next sync would do."""
    problem = config_problem()
    print(f"Online plan : {'ON, ' + urlsplit(settings.SUPABASE_URL).hostname if not problem else problem}")
    if problem:
        return 0 if "not configured" in problem else 1
    print(f"Planner     : {settings.PLANNER_URL or '(STUDIO_PLANNER_URL blank: the dashboard has no link)'}")
    try:
        api = Client()
        laptop = api.select("laptop_status")
        counts = {t: len(api.select(t, {"select": "id"})) for t in ("projects", "ideas", "shots", "takes", "clips")}
        queued = api.select("jobs", {"select": "id,kind", "runner": "eq.laptop", "state": "eq.queued"})
    except CloudError as e:
        print(f"Connection  : FAILED ({e})")
        return 1
    print(f"Connection  : OK (last check-in {laptop[0]['last_seen'] if laptop else 'never'})")
    print("Online      : " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    print(f"Laptop jobs : {len(queued)} queued" + (f" ({', '.join(j['kind'] for j in queued)})" if queued else ""))
    _check_references(api)
    clips = conn.execute("SELECT COUNT(*) FROM clips WHERE status IN ('tagged', 'transcribed')").fetchone()[0]
    pushed = conn.execute("SELECT COUNT(*) FROM clips WHERE cloud_hash IS NOT NULL").fetchone()[0]
    print(f"Clips       : {pushed} of {clips} library clips pushed (the next sync sends new or changed ones)")
    local = _unsynced(conn)
    if any(local.values()):
        print("Local plan  : not online yet: " + ", ".join(f"{n} {t}" for t, n in local.items() if n)
              + ". Run: python worker.py --cloud-import")
    else:
        print("Local plan  : nothing left to import")
    return 0


def _check_references(api: Client) -> None:
    try:
        refs = api.select("reference_videos", {"select": "id,brand,side,status"})
        analyses = {a["reference_id"]: a for a in api.select(
            "reference_analysis", {"select": "reference_id,state,error,analyser,updated_at", "order": "reference_id"})}
        guides = api.select("style_guides", {"select": "id,brand,version,status,reference_ids", "order": "version.desc"})
    except CloudError as e:
        print(f"References  : {NEEDS_UPDATE}" if _missing_table(e) else f"References  : FAILED ({e})")
        return
    s = _refs_summary(refs, analyses, guides)
    print(f"References  : {s['references']} active ({s['ours']} ours, {s['references'] - s['ours']} inspiration)"
          + (f", {s['retired']} retired" if s["retired"] else "")
          + f"; {s['waiting']} waiting for analysis" + (f", {s['failed']} failed" if s["failed"] else ""))
    for r in refs:
        a = analyses.get(r["id"]) or {}
        if r["status"] == "active" and a.get("state") == "failed":
            print(f"              #{r['id']} failed: {(a.get('error') or '')[:150]}")
    guide = (f"v{s['guide_version']} active" if s["guide_version"] else "none approved yet")
    if s["draft_version"]:
        guide += f"; v{s['draft_version']} draft waiting for approval"
    print(f"Style guide : {guide}")
    applied = _state().get("applied_guide")
    if applied:
        print(f"Playbook    : style guide v{applied.get('version')} applied {applied.get('applied_at', '')[:16]}")


def import_local(conn, api: Client | None = None) -> dict:
    """Push the local plan (made before planning moved online) up once. Votes and
    comments stay local: they belong to dashboard accounts, not planner ones."""
    import plan
    api = api or Client()
    out = {"projects": 0, "arcs": 0, "ideas": 0, "shots": 0, "takes": 0}

    def put(table: str, local_id: int, row: dict) -> int:
        remote_id = api.insert(table, row)[0]["id"]
        conn.execute(f"UPDATE {table} SET remote_id = ? WHERE id = ?", (remote_id, local_id))
        conn.commit()
        out[table] += 1
        return remote_id

    for p in conn.execute("SELECT * FROM projects WHERE remote_id IS NULL ORDER BY id").fetchall():
        put("projects", p["id"], {"name": p["name"], "brand": p["brand"] if p["brand"] in ("urban_makers", "personal")
                                  else "urban_makers", "folder": p["folder"] or "", "brief": p["brief"] or "",
                                  "status": p["status"] if p["status"] in ("active", "archived") else "active"})
    projects = _project_remote_ids(conn)
    for a in conn.execute("SELECT * FROM arcs WHERE remote_id IS NULL ORDER BY id").fetchall():
        if a["project_id"] in projects:
            put("arcs", a["id"], {"project_id": projects[a["project_id"]], "title": a["title"],
                                  "purpose": a["purpose"] or "", "position": a["position"] or 0})
    arcs = {r[0]: r[1] for r in conn.execute("SELECT id, remote_id FROM arcs WHERE remote_id IS NOT NULL")}
    for i in conn.execute("SELECT * FROM ideas WHERE origin = 'plan' AND remote_id IS NULL ORDER BY id").fetchall():
        if i["project_id"] not in projects:
            continue
        music = json.loads(i["music_plan"] or "{}")
        put("ideas", i["id"], {
            "project_id": projects[i["project_id"]], "arc_id": arcs.get(i["arc_id"]), "title": i["title"],
            "brand": i["brand"] if i["brand"] in ("urban_makers", "personal") else "urban_makers",
            "format": i["format"] if i["format"] in ("talking_head", "day_in_the_life", "compilation")
            else "talking_head",
            "hook": i["hook"] or "", "purpose": i["purpose"] or i["angle"] or "", "cta": i["cta"] or "",
            "caption": i["caption"] or "", "est_seconds": i["est_seconds"] or 30, "music": music,
            "decision": i["decision"] if i["decision"] in ("proposed", "go", "parked") else "proposed",
            "status": i["status"] if i["status"] in ("new", "made", "dismissed") else "new",
            "production": _production(conn, i["production_id"]) if i["production_id"] else None,
            "position": i["position"] or 0})
    ideas = {r[0]: r[1] for r in conn.execute("SELECT id, remote_id FROM ideas WHERE remote_id IS NOT NULL")}
    for s in conn.execute("SELECT * FROM shots WHERE remote_id IS NULL ORDER BY idea_id, position, id").fetchall():
        if s["idea_id"] in ideas:
            put("shots", s["id"], {
                "idea_id": ideas[s["idea_id"]], "position": s["position"] or 0,
                "kind": s["kind"] if s["kind"] in plan.SHOT_KINDS else None,
                "framing": s["framing"] if s["framing"] in plan.FRAMINGS else None,
                "camera": s["camera"] if s["camera"] in plan.CAMERAS else None,
                "description": s["description"] or "Shot", "line": s["line"] or "",
                "on_screen": s["on_screen"] or "", "seconds": s["seconds"], "why": s["why"] or "",
                "filmed_at": _ts(s["filmed_at"])})
    push_clips(api, conn)  # takes point at clips, so those go first
    created = push_takes(api, conn)
    out["takes"] = len(created)
    push_take_stills(api, conn, created)
    return out
