"""
The server's link to OneDrive, through rclone (README: "Studio on a server").

Footage still arrives the way it always has: the team uploads to the OneDrive inbox
folder (STUDIO_ONEDRIVE_INBOX). The server has no OneDrive app, so this module:

  1. pulls finished uploads into the studio's Inbox (same folders: project folders,
     References\\Ours, References\\Inspiration), then MOVES each OneDrive original into
     the archive folder (STUDIO_ONEDRIVE_ARCHIVE\\<YYYY-MM>\\...) so OneDrive keeps a
     backup and the inbox only shows what's still waiting;
  2. copies every approved reel to the "ready to post" folder (STUDIO_ONEDRIVE_READY),
     so it can be posted straight from the phone's OneDrive app.

Nothing is ever deleted from OneDrive: originals are only moved to the archive.
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
    if "failed to create file system" in low:  # the remote exists but its settings are broken
        return "OneDrive's connection settings on the server are incomplete: run server_move.py again from the laptop"
    if any(w in low for w in ("no such host", "network is unreachable", "i/o timeout", "connection refused",
                              "tls handshake timeout")):
        return "Can't reach OneDrive right now (network); trying again shortly"
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
    worker only looks one folder deep (References: two), so deeper folders are folded
    into the file name (Kensington/Day 1/a.mp4 -> Kensington/Day 1 - a.mp4). None for
    files the worker would never use (loose files in References)."""
    if parts[0].lower() == "references":
        side = REF_SIDES.get(parts[1].lower()) if len(parts) >= 3 else None
        return Path("References", side, " - ".join(parts[2:])) if side else None
    if len(parts) <= 2:
        return Path(*parts)
    return Path(parts[0], " - ".join(parts[1:]))


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
                _update_state(lambda d, k=item["rel"]: d.setdefault("copied", {}).pop(k, None))
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

def _ready_name(title: str, production_id: int, suffix: str) -> str:
    """A file name OneDrive accepts: no \\ / : * ? " < > | and no trailing dot or space."""
    safe = re.sub(r'[\\/:*?"<>|#%\x00-\x1f]+', " ", title or "")
    safe = re.sub(r"\s+", " ", safe).strip(" .")[:80].strip(" .") or "Reel"
    return f"{safe} ({production_id}){suffix or '.mp4'}"


def ready_to_push(conn) -> list[dict]:
    """Approved productions whose current video isn't in the ready folder yet.
    A reel re-approved after changes is copied again (its video changed)."""
    pushed = _state().get("pushed", {})
    items = []
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
            size = Path(item["path"]).stat().st_size if Path(item["path"]).exists() else 0
            try:
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


def push_ready(conn) -> list[str]:
    """Copy each approved reel's video to the OneDrive ready folder, once."""
    return _push(ready_to_push(conn))


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
