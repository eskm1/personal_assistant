"""
The server's link to OneDrive, through rclone (README: "Studio on a server").

Footage still arrives the way it always has: the team uploads to the OneDrive inbox
folder (STUDIO_ONEDRIVE_INBOX). The server has no OneDrive app, so this module:

  1. pulls finished uploads into the studio's Inbox (same folders: project folders and
     their post folders, References\\Ours, References\\Inspiration), then MOVES each
     OneDrive original into the archive folder (STUDIO_ONEDRIVE_ARCHIVE\\<YYYY-MM>\\...)
     so OneDrive keeps a backup and the inbox only shows what's still waiting;
  2. copies every approved reel to the "ready to post" folder (STUDIO_ONEDRIVE_READY),
     so it can be posted straight from the phone's OneDrive app; an approved carousel goes
     there as a folder of its own: "<title> (<id>)/01.png, 02.png ... caption.txt";
  3. for Review's clean-up (cleanup.py): remembers where each original went in the archive
     (catalog clips.onedrive_path), checks a clip's original is really there before the
     server's own copy may be deleted, copies it there first when OneDrive never had it
     (uploaded from the planner, or from the laptop before the move: upload_original), and
     fetches it back when an edit needs it again.

Nothing is ever deleted or changed in OneDrive: originals are only moved to the archive, or
copied into it as new files.
rclone keeps the OneDrive sign-in fresh by itself (in rclone.conf, set up by
server_move.py); this module never reads or prints that file.

The worker calls tick() every poll; the copying runs in a background thread so a
multi-GB upload never holds up the rest of the worker.

  python onedrive.py --check     is OneDrive connected, and what's waiting?
"""
import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import settings

log = logging.getLogger("studio.onedrive")

PULL_EVERY = 60          # seconds between looks at the OneDrive inbox
PUSH_EVERY = 300         # seconds between checks for newly approved reels
SETTLE_SECONDS = 120     # a file changed this recently may still be uploading
FILES_PER_PULL = 5       # files per pull; the next pull carries on a minute later
PULL_BUDGET = 20 * 60    # seconds: no new file starts after a pull has run this long
DISK_MARGIN = 2 << 30    # keep this much disk free on the server after a copy
STATE_FILE = "onedrive_state.json"
# The inbox subfolders cloud_sync.py turns into reference reels (cloud_sync.REF_SIDES).
REF_SIDES = {"ours": "Ours", "inspiration": "Inspiration"}

# Quiet, patient rclone: retries on its own, gives up on a dead connection, no progress output.
FLAGS = ["--retries", "3", "--low-level-retries", "10", "--contimeout", "30s", "--timeout", "5m",
         "--stats", "0", "--log-level", "ERROR"]
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

NOT_CONNECTED = "OneDrive isn't connected yet: run server_move.py again from the laptop"
SIGNED_OUT = "OneDrive's sign-in has expired: run server_move.py again from the laptop to reconnect it"


class OneDriveError(RuntimeError):
    """A problem worth showing Bryan, already in plain words."""


class NotFound(OneDriveError):
    """The folder or file isn't in OneDrive (rclone exit code 3 or 4)."""


# What the dashboard shows (status()). Only simple values, set from either thread.
_status = {"waiting": None, "settling": None, "pulling": None, "last_error": None}
_last = {"pull": 0.0, "push": 0.0}
_thread: threading.Thread | None = None
_pull_lock = threading.Lock()      # one pull (or push) at a time
_state_lock = threading.Lock()
_logged_error: str | None = None   # problems are logged once, not every minute
_logged_odd: set[str] = set()
_made_ref_folders = False


# ── rclone ────────────────────────────────────────────────────────────────────

def rclone_path() -> str | None:
    return shutil.which(settings.RCLONE)


def configured() -> bool:
    """rclone is installed and an OneDrive inbox folder is set."""
    return bool(settings.ONEDRIVE_INBOX) and rclone_path() is not None


def _remote(*parts: str) -> str:
    path = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return f"{settings.ONEDRIVE_REMOTE}:{path}"


def _redact(text: str) -> str:
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)
    return re.sub(r"(?i)(token|secret|password|authorization)[\"'\s:=]+(?!\*\*\*|Bearer \*)[^\s,}]+", r"\1=***", text)


def _friendly(stderr: str, code: int) -> str:
    """rclone's complaint, as one short line Bryan can act on."""
    text = _redact(stderr)
    low = text.lower()
    if "didn't find section in config" in low or ("config file" in low and "not found" in low
                                                  and "failed to create file system" in low):
        return NOT_CONNECTED
    if ("token" in low and any(w in low for w in ("expired", "invalid", "refresh", "fetch"))) \
            or "invalid_grant" in low or "unauthenticated" in low or "aadsts" in low:
        return SIGNED_OUT
    # Offline first: rclone says "failed to create file system" then too (it can't reach OneDrive at all).
    if any(w in low for w in ("no such host", "network is unreachable", "i/o timeout", "connection refused",
                              "tls handshake timeout")):
        return "Can't reach OneDrive right now (network); trying again shortly"
    if "failed to create file system" in low:  # the remote exists but its settings are broken
        return "OneDrive's connection settings on the server are incomplete: run server_move.py again from the laptop"
    if ("insufficient" in low and "storage" in low) or "quota" in low:
        return "OneDrive is full: free up space (the archive folder can be tidied)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "NOTICE" not in ln]
    last = lines[-1] if lines else f"rclone stopped with code {code}"
    last = re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+(ERROR|CRITICAL|NOTICE)\s*:\s*", "", last)
    return f"OneDrive problem: {last[:200]}"


def _run(args: list[str], timeout: float) -> str:
    """Run rclone and return its output; OneDriveError (plain words) when it fails."""
    exe = rclone_path()
    if exe is None:
        raise OneDriveError("rclone isn't installed on this machine (the server setup installs it)")
    try:
        result = subprocess.run([exe, *args, *FLAGS], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OneDriveError(f"OneDrive took too long (over {int(timeout // 60) or 1} min); trying again later")
    except OSError as e:
        raise OneDriveError(f"Couldn't start rclone: {e}")
    if result.returncode in (3, 4):   # directory / file not found
        raise NotFound(_friendly(result.stderr, result.returncode))
    if result.returncode != 0:
        raise OneDriveError(_friendly(result.stderr, result.returncode))
    return result.stdout


def _lsjson(*parts: str, recursive: bool = False, dirs: bool = False, timeout: float = 300) -> list[dict]:
    args = ["lsjson", "--no-mimetype", "--dirs-only" if dirs else "--files-only"]
    if recursive:
        args.append("-R")
    return json.loads(_run([*args, _remote(*parts)], timeout) or "[]")


def _parse_time(value: str | None) -> float | None:
    """rclone's ModTime (nanoseconds, a zone or Z) as a timestamp."""
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


# ── State (ROOT/onedrive_state.json) ──────────────────────────────────────────

def _state() -> dict:
    try:
        return json.loads((settings.ROOT / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _update_state(fn) -> None:
    """Read, change (fn edits the dict in place) and save the state file."""
    with _state_lock:
        data = _state()
        fn(data)
        path = settings.ROOT / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)


# ── Pull: OneDrive inbox -> studio Inbox ─────────────────────────────────────

def _local_rel(parts: list[str]) -> Path | None:
    """Where an upload goes in the studio's Inbox: the same place, except that the
    worker only looks two folders deep (a project, then a post: Kensington/Shower
    reveal/a.mp4), so deeper folders are folded into the file name
    (Kensington/Shower reveal/Day 1/a.mp4 -> Kensington/Shower reveal/Day 1 - a.mp4).
    None for files the worker would never use (loose files in References)."""
    if parts[0].lower() == "references":
        side = REF_SIDES.get(parts[1].lower()) if len(parts) >= 3 else None
        return Path("References", side, " - ".join(parts[2:])) if side else None
    if len(parts) <= 3:
        return Path(*parts)
    return Path(parts[0], parts[1], " - ".join(parts[2:]))


def _waiting(entries: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Split the inbox listing into videos ready to pull, ones still uploading, and
    videos in a place the worker doesn't read."""
    now = time.time()
    ready, settling, odd = [], [], []
    for e in entries:
        rel = e.get("Path") or ""
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p.startswith((".", "~")) for p in parts):
            continue  # hidden or temporary (Office's ~$ files, .partial uploads, ...)
        if Path(parts[-1]).suffix.lower() not in settings.VIDEO_EXTS:
            continue
        local = _local_rel(parts)
        if local is None:
            odd.append(rel)
            continue
        mtime = _parse_time(e.get("ModTime"))
        item = {"rel": rel, "local": local, "size": int(e.get("Size") or 0), "mod": e.get("ModTime") or ""}
        if item["size"] <= 0 or mtime is None or abs(now - mtime) < SETTLE_SECONDS:
            settling.append(item)
        else:
            ready.append(item)
    ready.sort(key=lambda i: i["rel"])
    return ready, settling, odd


def _unique(path: Path) -> Path:
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def _clean_partials() -> None:
    """Remove half-copies left by a pull that was cut off (a restart mid-copy).
    rclone writes to <name>.<hash>.partial and renames when done; a live one is
    still being written, so only stale ones go."""
    if not settings.INBOX.exists():
        return
    for p in settings.INBOX.rglob("*.partial"):
        try:
            if time.time() - p.stat().st_mtime > 3600:
                p.unlink()
                log.info("Removed an unfinished OneDrive copy: %s", p.name)
        except OSError:
            pass


def _copy_down(item: dict, local: Path) -> None:
    free = shutil.disk_usage(settings.INBOX).free
    if free < item["size"] + DISK_MARGIN:
        raise OneDriveError(f"The server's disk is nearly full ({free / 2**30:.1f} GB free): "
                            f"{item['rel']} ({item['size'] / 2**30:.1f} GB) waits in OneDrive")
    local.parent.mkdir(parents=True, exist_ok=True)
    # copyto downloads to a .partial name next to the target and renames at the end, so
    # the worker never sees half a file; it keeps OneDrive's modified time.
    _run(["copyto", _remote(settings.ONEDRIVE_INBOX, item["rel"]), str(local)],
         timeout=600 + item["size"] / 1_000_000)   # at least 1 MB/s
    try:
        size = local.stat().st_size
    except FileNotFoundError:
        return  # the worker already took it (rclone has checked size and hash itself)
    if size != item["size"]:
        local.unlink(missing_ok=True)
        raise OneDriveError(f"{item['rel']} changed while it was being copied; trying again later")


def _archive(item: dict, month: str, taken: dict[str, set]) -> str:
    """Move the OneDrive original into ARCHIVE/<month>/<same path> (a move inside
    OneDrive: nothing is downloaded or uploaded again). Never overwrites an archived file."""
    rel_dir, _, name = item["rel"].rpartition("/")
    folder = "/".join(p for p in (settings.ONEDRIVE_ARCHIVE, month, rel_dir) if p)
    if folder not in taken:
        try:
            taken[folder] = {e["Name"].lower() for e in _lsjson(folder, timeout=120)}
        except NotFound:
            taken[folder] = set()
    stem, dot, ext = name.rpartition(".")
    target, n = name, 2
    while target.lower() in taken[folder]:
        target = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
        n += 1
    _run(["moveto", _remote(settings.ONEDRIVE_INBOX, item["rel"]), _remote(folder, target)], timeout=300)
    taken[folder].add(target.lower())
    return f"{folder}/{target}"


def _make_ref_folders() -> None:
    """Make References/Ours and /Inspiration in the OneDrive inbox once, so the team
    sees where reference reels go. Harmless when they exist; ignored if not allowed."""
    global _made_ref_folders
    if _made_ref_folders:
        return
    try:
        for side in REF_SIDES.values():
            _run(["mkdir", _remote(settings.ONEDRIVE_INBOX, "References", side)], timeout=120)
        _made_ref_folders = True
    except OneDriveError as e:
        log.debug("Couldn't make the References folders in OneDrive: %s", e)


def make_folder(*parts: str) -> None:
    """Make a folder in the OneDrive inbox (a post folder: project, post). Harmless
    when it exists; OneDriveError when OneDrive can't be reached."""
    _run(["mkdir", _remote(settings.ONEDRIVE_INBOX, *parts)], timeout=120)


def pull(max_files: int = FILES_PER_PULL, budget: float = PULL_BUDGET) -> list[str]:
    """Copy finished uploads from the OneDrive inbox into the studio's Inbox, then move
    each original into the archive. Returns what was pulled. Raises OneDriveError when
    OneDrive can't be read; a file that fails is retried on the next pull."""
    if not _pull_lock.acquire(blocking=False):
        return []  # one is already running
    try:
        _clean_partials()
        try:
            entries = _lsjson(settings.ONEDRIVE_INBOX, recursive=True)
        except NotFound:
            raise OneDriveError(f"The OneDrive folder \"{settings.ONEDRIVE_INBOX}\" wasn't found: create it "
                                "in OneDrive or fix STUDIO_ONEDRIVE_INBOX (run server_move.py again)")
        ready, settling, odd = _waiting(entries)
        _status.update(waiting=len(ready), settling=len(settling))
        for rel in set(odd) - _logged_odd:
            _logged_odd.add(rel)
            log.warning("%s in OneDrive isn't in References/Ours or References/Inspiration, so it's left there", rel)
        _make_ref_folders()
        copied = _state().get("copied", {})
        month = datetime.now().strftime("%Y-%m")
        taken: dict[str, set] = {}
        started = time.time()
        done, problems = [], []
        for item in ready[:max_files]:
            if time.time() - started > budget:
                break
            _status["pulling"] = item["rel"]
            try:
                record = copied.get(item["rel"])
                if not (record and record["size"] == item["size"] and record["mod"] == item["mod"]):
                    local = settings.INBOX / item["local"]
                    if local.exists() and local.stat().st_size == item["size"]:
                        pass  # copied on an earlier pull whose archive step failed
                    else:
                        local = _unique(local)
                        _copy_down(item, local)
                    record = {"size": item["size"], "mod": item["mod"], "local": str(local)}
                    _update_state(lambda d, k=item["rel"], v=record: d.setdefault("copied", {}).__setitem__(k, v))
                where = _archive(item, month, taken)
                _update_state(lambda d, k=item["rel"], v={"remote": where, "size": item["size"], "local": record["local"]}:
                              (d.setdefault("copied", {}).pop(k, None), d.setdefault("archived", {}).__setitem__(v["local"], v)))
                log.info("From OneDrive: %s -> %s (original moved to %s)", item["rel"], record["local"], where)
                done.append(item["rel"])
            except OneDriveError as e:
                problems.append(str(e))
            except OSError as e:
                problems.append(f"Couldn't save {item['rel']} on the server: {e}")
        _status.update(pulling=None, waiting=len(ready) - len(done))
        _update_state(lambda d: d.__setitem__("last_pull", datetime.now().astimezone().isoformat(timespec="seconds")))
        if problems:
            raise OneDriveError(problems[0] + (f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""))
        return done
    finally:
        _status["pulling"] = None
        _pull_lock.release()


# ── Push: approved reels -> "ready to post" folder ────────────────────────────

def _ready_name(title: str, production_id: int, suffix: str | None) -> str:
    """A file name OneDrive accepts: no \\ / : * ? " < > | and no trailing dot or space.
    suffix None = a folder's name (a carousel)."""
    safe = re.sub(r'[\\/:*?"<>|#%\x00-\x1f]+', " ", title or "")
    safe = re.sub(r"\s+", " ", safe).strip(" .")[:80].strip(" .") or "Reel"
    return f"{safe} ({production_id})" + ("" if suffix is None else suffix or ".mp4")


def _carousel_item(row, pushed: dict) -> dict | None:
    """An approved carousel's slides and caption, if they aren't in the ready folder as they are now."""
    import hashlib

    import carousel
    pngs = carousel.files(row)
    if not pngs:
        return None
    caption = carousel.caption_text(row)
    sig = "|".join([*(f"{p}:{p.stat().st_size}:{int(p.stat().st_mtime)}" for p in pngs),
                    hashlib.sha256(caption.encode()).hexdigest()[:16]])
    if pushed.get(str(row["id"]), {}).get("sig") == sig:
        return None
    return {"id": row["id"], "files": [str(p) for p in pngs], "caption": caption, "sig": sig,
            "name": _ready_name(row["title"], row["id"], None)}


def ready_to_push(conn) -> list[dict]:
    """Approved productions whose current video isn't in the ready folder yet.
    A reel re-approved after changes is copied again (its video changed); so is a
    carousel whose slides or caption changed."""
    pushed = _state().get("pushed", {})
    items = []
    for row in conn.execute("SELECT * FROM productions WHERE status = 'approved' AND format = 'carousel' "
                            "AND slides IS NOT NULL ORDER BY id").fetchall():
        item = _carousel_item(row, pushed)
        if item:
            items.append(item)
    for row in conn.execute("SELECT id, title, video_path FROM productions WHERE status = 'approved' "
                            "AND video_path IS NOT NULL AND video_path != '' ORDER BY id").fetchall():
        path = Path(row[2])
        try:
            st = path.stat()
        except OSError:
            continue  # rendered on the laptop and not moved over, or cleaned up
        sig = f"{path}|{st.st_size}|{int(st.st_mtime)}"
        if pushed.get(str(row[0]), {}).get("sig") != sig:
            items.append({"id": row[0], "path": str(path), "sig": sig,
                          "name": _ready_name(row[1], row[0], path.suffix)})
    return items


def _push(items: list[dict]) -> list[str]:
    """Copy reels into the ready folder (overwriting an older render of the same reel)."""
    done, problems = [], []
    with _pull_lock:
        for item in items:
            try:
                if item.get("files"):
                    _push_folder(item)
                else:
                    size = Path(item["path"]).stat().st_size if Path(item["path"]).exists() else 0
                    _run(["copyto", item["path"], _remote(settings.ONEDRIVE_READY, item["name"])],
                         timeout=600 + size / 1_000_000)
            except OneDriveError as e:
                problems.append(str(e))
                continue
            entry = {"sig": item["sig"], "name": item["name"], "at": datetime.now().astimezone().isoformat(timespec="seconds")}
            _update_state(lambda d, k=str(item["id"]), v=entry: d.setdefault("pushed", {}).__setitem__(k, v))
            log.info("Ready to post from OneDrive: %s/%s", settings.ONEDRIVE_READY, item["name"])
            done.append(item["name"])
    if problems:
        raise OneDriveError(problems[0])
    return done


def _push_folder(item: dict) -> None:
    """A carousel: its slides (01.png, 02.png ...) and caption.txt into a folder of its own
    in the ready folder, overwriting an earlier version's files of the same name."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="studio-ready-") as tmp:
        folder = Path(tmp)
        for f in item["files"]:
            shutil.copyfile(f, folder / Path(f).name)
        (folder / "caption.txt").write_text(item["caption"], encoding="utf-8")
        _run(["copy", str(folder), _remote(settings.ONEDRIVE_READY, item["name"])], timeout=600)


def push_ready(conn) -> list[str]:
    """Copy each approved reel's video to the OneDrive ready folder, once."""
    return _push(ready_to_push(conn))


# ── Originals in the archive (Review's clean-up, cleanup.py) ──────────────────

ARCHIVED_KEEP = 2000   # where recent pulls went, kept in the state file until their clip claims them


def claim(conn, clip_id: int, path: Path) -> None:
    """A pulled file became a clip (worker.ingest): note where OneDrive keeps its original."""
    entry = _state().get("archived", {}).get(str(path))
    if not entry:
        return
    conn.execute("UPDATE clips SET onedrive_path = ?, onedrive_size = ? WHERE id = ?",
                 (entry["remote"], int(entry.get("size") or 0), clip_id))
    conn.commit()

    def drop(d):
        archived = d.setdefault("archived", {})
        archived.pop(str(path), None)
        for k in list(archived)[:-ARCHIVED_KEEP]:   # the oldest, never claimed (duplicates, unreadable)
            archived.pop(k, None)
    _update_state(drop)


def _same_name(name: str, wanted: str) -> bool:
    """Name, or the name the archive gave it when one was there already ("a (2).mov")."""
    if name.lower() == wanted.lower():
        return True
    stem, dot, ext = wanted.rpartition(".")
    return bool(dot) and re.fullmatch(re.escape(stem) + r" \(\d+\)\." + re.escape(ext), name, re.I) is not None


def original_in_archive(row, listing: list[dict] | None = None) -> str | None:
    """Where OneDrive's archive holds this clip's original (its path in OneDrive), or None.
    Checked in OneDrive itself, by name and exact size: the path noted when it was pulled, else
    a search of the archive (`listing`: one recursive listing of it, shared by a batch).
    OneDriveError when OneDrive can't be asked."""
    size = int(row["onedrive_size"] or 0)
    if not size:
        try:
            size = Path(row["video_path"]).stat().st_size
        except OSError:
            return None
    known = row["onedrive_path"]
    if known:
        folder, _, name = known.rpartition("/")
        try:
            entries = _lsjson(folder, timeout=120)
        except NotFound:
            entries = []
        if any(e.get("Name") == name and int(e.get("Size") or -1) == size for e in entries):
            return known
    if listing is None:
        listing = archive_listing()
    name = Path(row["video_path"]).name
    hits = [e["Path"] for e in listing if int(e.get("Size") or -1) == size
            and (_same_name(e.get("Name") or "", name) or _same_name(e.get("Name") or "", row["source_name"] or ""))]
    return f"{settings.ONEDRIVE_ARCHIVE}/{hits[0]}" if len(hits) == 1 else None


def archive_listing() -> list[dict]:
    """Every file in OneDrive's archive folder (one rclone call)."""
    try:
        return _lsjson(settings.ONEDRIVE_ARCHIVE, recursive=True, timeout=600)
    except NotFound:
        return []


class Mismatch(OneDriveError):
    """OneDrive's copy isn't the same file as the server's (size or fingerprint)."""


HASHES = ("quickxor", "sha1", "md5")   # OneDrive gives quickxor; the first one both sides know is used


def _safe(name: str | None) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip(" .")[:80]


def archive_folder(conn, row) -> str:
    """Where Free up space puts an original OneDrive never had (one uploaded from the planner, or
    from the laptop before the move): Studio Archive/<project>/<post, or Uploads>/, or
    Studio Archive/Uploads/ for a clip of no project."""
    project = idea = None
    if row["project_id"]:
        project = conn.execute("SELECT name, folder FROM projects WHERE id = ?", (row["project_id"],)).fetchone()
    if project and row["idea_id"]:
        idea = conn.execute("SELECT title, folder FROM ideas WHERE id = ?", (row["idea_id"],)).fetchone()
    parts = [settings.ONEDRIVE_ARCHIVE]
    name = project and (_safe(project["folder"]) or _safe(project["name"]))
    if name:
        parts += [name, (idea and (_safe(idea["folder"]) or _safe(idea["title"]))) or "Uploads"]
    else:
        parts.append("Uploads")
    return "/".join(parts)


def _remote_file(path: str) -> dict | None:
    """One file in OneDrive with its size and fingerprints, or None when it isn't there."""
    args = ["lsjson", "--files-only", "--no-mimetype", *(a for h in HASHES for a in ("--hash-type", h))]
    try:
        entries = json.loads(_run([*args, _remote(path)], timeout=300) or "[]")
    except NotFound:
        return None
    name = path.rpartition("/")[2]
    return next((e for e in entries if e.get("Name") == name), None)


def local_hash(path: Path, kind: str) -> str:
    out = _run(["hashsum", kind, str(path)], timeout=600 + path.stat().st_size / 50_000_000)
    return out.split()[0].lower() if out.strip() else ""


def verify(local: Path, remote: str) -> str:
    """Check OneDrive's file at `remote` is the same as `local`: exact size, and the same
    fingerprint when OneDrive gives one. Returns how it was checked ("size", "size and quickxor").
    Mismatch when it differs; OneDriveError when OneDrive can't be asked."""
    entry = _remote_file(remote)
    if entry is None:
        raise Mismatch(f"OneDrive doesn't show {remote}")
    size = local.stat().st_size
    if int(entry.get("Size") or -1) != size:
        raise Mismatch(f"OneDrive's copy is {int(entry.get('Size') or 0):,} bytes, the server's {size:,}")
    theirs = {k.lower(): str(v).lower() for k, v in (entry.get("Hashes") or {}).items() if v}
    for kind in HASHES:
        if kind in theirs:
            if local_hash(local, kind) != theirs[kind]:
                raise Mismatch("OneDrive's copy has a different fingerprint")
            return f"size and {kind}"
    return "size"


def upload_original(conn, row) -> str:
    """Copy a clip's original into OneDrive's archive (archive_folder), never over a different
    file (a name taken by another file gets " (2)"), and check the copy (verify). Returns its
    path in OneDrive. Only adds a file: nothing in OneDrive is changed or removed."""
    local = Path(row["video_path"])
    size = local.stat().st_size
    folder = archive_folder(conn, row)
    try:
        taken = {e["Name"].lower(): e for e in _lsjson(folder, timeout=120)}
    except NotFound:
        taken = {}
    stem, ext = local.stem, local.suffix
    target, n = local.name, 2
    while target.lower() in taken:
        if int(taken[target.lower()].get("Size") or -1) == size:
            try:   # the same file already (a copy made before a restart cut the job short)
                verify(local, f"{folder}/{taken[target.lower()]['Name']}")
                return f"{folder}/{taken[target.lower()]['Name']}"
            except Mismatch:
                pass
        target, n = f"{stem} ({n}){ext}", n + 1
    remote = f"{folder}/{target}"
    # --immutable: rclone refuses to change a file that's already there. rclone checks the
    # upload itself too; verify() checks it again before anything is deleted.
    _run(["copyto", "--immutable", str(local), _remote(remote)], timeout=600 + size / 1_000_000)
    try:
        how = verify(local, remote)
    except Mismatch as e:
        raise Mismatch(f"{e}: kept the server's copy") from None
    log.info("Copied clip %s's original to OneDrive: %s (%.2f GB, checked by %s)", row["id"], remote, size / 1e9, how)
    return remote


def fetch_original(conn, clip_id: int) -> Path | None:
    """Bring a clip's original back from OneDrive's archive after the server's copy was freed
    (an edit, a still or a Resolve project needs it). Returns the file, or None when the clip
    wasn't freed or OneDrive doesn't have it. OneDriveError when OneDrive can't be reached."""
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if row is None:
        return None
    local = Path(row["video_path"])
    if local.is_file():
        return local
    if not row["freed_at"] or not configured():
        return None
    remote = original_in_archive(row)
    if not remote:
        raise OneDriveError(f"OneDrive's archive doesn't have the original of clip {clip_id} any more")
    size = int(row["onedrive_size"] or row["freed_bytes"] or 0)
    free = shutil.disk_usage(local.parent).free
    if free < size + DISK_MARGIN:
        raise OneDriveError(f"The server's disk is nearly full ({free / 2**30:.1f} GB free): can't fetch clip {clip_id}'s original")
    local.parent.mkdir(parents=True, exist_ok=True)
    _run(["copyto", _remote(remote), str(local)], timeout=600 + size / 1_000_000)
    if not local.is_file() or (size and local.stat().st_size != size):
        local.unlink(missing_ok=True)
        raise OneDriveError(f"Clip {clip_id}'s original didn't come back whole from OneDrive; try again")
    conn.execute("UPDATE clips SET freed_at = NULL, freed_bytes = NULL, onedrive_path = ? WHERE id = ?", (remote, clip_id))
    conn.commit()
    log.info("Fetched clip %s's original back from OneDrive (%s, %.1f GB)", clip_id, remote, local.stat().st_size / 2**30)
    return local


def ensure_originals(conn, clip_ids) -> list[int]:
    """Before an edit: fetch back any of these clips' originals the clean-up freed. Returns the
    ids still missing (logged; the caller carries on without them, as for any missing file)."""
    missing = []
    for cid in dict.fromkeys(int(c) for c in clip_ids):
        row = conn.execute("SELECT video_path, freed_at FROM clips WHERE id = ?", (cid,)).fetchone()
        if row is None or not row["freed_at"] or Path(row["video_path"]).is_file():
            continue
        try:
            if fetch_original(conn, cid) is None:
                missing.append(cid)
        except OneDriveError as e:
            log.warning("%s", e)
            missing.append(cid)
    return missing


# ── Worker hook ───────────────────────────────────────────────────────────────

def _log_once(key: str, message: str) -> None:
    global _logged_error
    if key != _logged_error:
        _logged_error = key
        log.warning("%s", message)


def _problem(message: str) -> None:
    _status["last_error"] = message
    _log_once(message, message)


def _all_well() -> None:
    global _logged_error
    if _status["last_error"]:
        log.info("OneDrive is working again")
    _status["last_error"] = None
    _logged_error = None


def _jobs(do_pull: bool, items: list[dict]) -> None:
    problems = []
    for wanted, job in ((do_pull, pull), (bool(items), lambda: _push(items))):
        if not wanted:
            continue
        try:
            job()
        except OneDriveError as e:
            problems.append(str(e))
        except Exception as e:  # never let OneDrive take the worker down
            problems.append(f"OneDrive problem: {type(e).__name__}: {_redact(str(e))[:200]}")
            log.debug("OneDrive job failed", exc_info=True)
    if problems:
        _problem(problems[0])
    else:
        _all_well()


def tick(conn, force: bool = False, background: bool = True) -> None:
    """Called from the worker loop every poll. Pulls new uploads about once a minute
    and copies approved reels every few minutes, in a background thread (background=False
    runs them here, e.g. for --once). Never raises."""
    global _thread
    try:
        if not configured():
            if settings.ONEDRIVE_INBOX:
                _problem("rclone isn't installed on this machine (the server setup installs it)")
            return
        if _thread is not None and _thread.is_alive():
            return
        now = time.time()
        do_pull = force or now - _last["pull"] >= PULL_EVERY
        items = []
        if force or now - _last["push"] >= PUSH_EVERY:
            _last["push"] = now
            try:
                items = ready_to_push(conn)   # read here: the database connection belongs to this thread
            except Exception as e:  # a database hiccup mustn't stop the pull
                _problem(f"Couldn't check for approved reels: {type(e).__name__}: {e}"[:250])
        if not do_pull and not items:
            return
        if do_pull:
            _last["pull"] = now
        if background:
            _thread = threading.Thread(target=_jobs, args=(do_pull, items), name="onedrive", daemon=True)
            _thread.start()
        else:
            _jobs(do_pull, items)
    except Exception as e:
        try:
            _problem(f"OneDrive problem: {type(e).__name__}: {_redact(str(e))[:200]}")
        except Exception:
            pass


def busy() -> bool:
    """A copy to or from OneDrive is running (the updater should wait)."""
    return _thread is not None and _thread.is_alive()


def status() -> dict:
    """For the dashboard. Cheap: no OneDrive calls."""
    state = _state()
    return {
        "configured": configured(),
        "enabled": bool(settings.ONEDRIVE_INBOX),
        "rclone": rclone_path() is not None,
        "inbox": settings.ONEDRIVE_INBOX,
        "archive": settings.ONEDRIVE_ARCHIVE,
        "ready": settings.ONEDRIVE_READY,
        "last_pull": state.get("last_pull"),       # last time the inbox was read (ISO, server time)
        "waiting": _status["waiting"],             # videos in the OneDrive inbox ready to pull (None = not looked yet)
        "uploading": _status["settling"],          # videos there still arriving (changed < 2 min ago)
        "pulling": _status["pulling"],             # file being copied now, or None
        "pushed": len(state.get("pushed", {})),    # reels copied to the ready folder so far
        "last_error": _status["last_error"],
    }


# ── python onedrive.py --check ────────────────────────────────────────────────

def check() -> int:
    print("OneDrive check")
    exe = rclone_path()
    if exe is None:
        print("  rclone       : NOT INSTALLED. The server setup installs it: "
              "sudo bash studio/server/setup.sh install <domain>")
        return 1
    try:
        version = _run(["version"], timeout=30).splitlines()[0]
    except (OneDriveError, IndexError):
        version = "installed"
    print(f"  rclone       : {version}")
    if not settings.ONEDRIVE_INBOX:
        print("  OneDrive     : intake is off (STUDIO_ONEDRIVE_INBOX isn't set). "
              "Run server_move.py again from the laptop.")
        return 1
    print(f"  Folders      : inbox \"{settings.ONEDRIVE_INBOX}\", archive \"{settings.ONEDRIVE_ARCHIVE}\", "
          f"ready to post \"{settings.ONEDRIVE_READY}\" (on remote \"{settings.ONEDRIVE_REMOTE}\")")
    try:
        top = {e["Name"] for e in _lsjson("", dirs=True, timeout=120)}
    except OneDriveError as e:
        print(f"  Connected    : NO. {e}")
        return 1
    print("  Connected    : yes")
    for label, name in (("Archive", settings.ONEDRIVE_ARCHIVE), ("Ready folder", settings.ONEDRIVE_READY)):
        first = name.split("/")[0]
        print(f"  {label:<13}: " + ("found" if first in top else "not there yet (made automatically when needed)"))
    try:
        entries = _lsjson(settings.ONEDRIVE_INBOX, recursive=True)
    except NotFound:
        print(f"  Inbox        : NOT FOUND. Create a folder called \"{settings.ONEDRIVE_INBOX}\" in OneDrive "
              "(or fix STUDIO_ONEDRIVE_INBOX)")
        return 1
    except OneDriveError as e:
        print(f"  Inbox        : couldn't read it. {e}")
        return 1
    ready, settling, odd = _waiting(entries)
    print(f"  Inbox        : {len(ready)} video(s) ready to pull, {len(settling)} still uploading")
    for item in ready + settling:
        note = "" if item in ready else "  (still uploading)"
        print(f"     {item['rel']}  {item['size'] / 2**20:,.1f} MB{note}")
    for rel in odd:
        print(f"     {rel}  (ignored: put reference reels in References/Ours or References/Inspiration)")
    state = _state()
    print(f"  Server inbox : {settings.INBOX}")
    print(f"  Last pull    : {state.get('last_pull') or 'never'}; reels copied to the ready folder: "
          f"{len(state.get('pushed', {}))}")
    if state.get("copied"):
        print(f"  Waiting to archive: {', '.join(state['copied'])} (copied; the move in OneDrive failed last time)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="The studio's OneDrive link (rclone).")
    parser.add_argument("--check", action="store_true", help="is OneDrive connected, and what's waiting?")
    parser.add_argument("--pull", action="store_true", help="pull what's waiting now (and archive it)")
    args = parser.parse_args()
    if args.pull:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        try:
            pulled = pull(max_files=10_000, budget=float("inf"))
        except OneDriveError as e:
            print(e)
            return 1
        print(f"Pulled {len(pulled)} file(s)")
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
