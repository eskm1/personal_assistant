"""
Light copies for watching in Review (Bryan, 2026-10-19: "even a small video will stop a few times
to buffer while on wifi"). The server is in Europe and Review is watched from Singapore, so what
plays in the browser is a light copy: 720p H.264 at about 2 Mbps (capped), 128k sound, with its
index at the front (faststart) so it starts at once and seeks without fetching the whole file.
The full-quality file stays for "Full quality", the download and OneDrive's Ready to post.

- A draft's light copy (Renders/production_<id>_v<n>.light.mp4) is made as the draft goes to
  review (self_fix.deliver); a clip's (its folder's preview-720.mp4) as the clip is ingested.
- Older drafts and clips get theirs in the background: when one is played (the full file plays
  meanwhile) and, one at a time, while the worker has nothing else to do. Never all at once.
"""
import logging
import os
import shutil
import struct
import threading
from pathlib import Path

import extract
import settings

log = logging.getLogger("studio.previews")

NAME = "preview-720.mp4"                      # a clip's light copy, in its folder
OLD_NAMES = ("preview-1080.mp4", "preview.mp4")  # earlier recipes: played until the new one is made
SHORT_SIDE = 720
VIDEO_KBPS = 2000      # the usual rate; quiet shots come out lower (CRF 23)
MAX_KBPS = 2500        # never above this, so wifi far from the server keeps up
AUDIO_KBPS = 128
QUEUE_MAX = 30


def light_for(video: str | Path) -> Path:
    """Where a draft's light copy lives: beside it, production_5_v2.mp4 -> production_5_v2.light.mp4."""
    video = Path(video)
    return video.with_name(video.stem + ".light.mp4")


def _cmd(src: Path, out: Path, background: bool) -> list[str]:
    side = SHORT_SIDE
    # The short side to 720 (never enlarged), even sizes, at most 30 frames a second.
    scale = (f"scale=w='if(gt(iw,ih),-2,trunc(min({side},iw)/2)*2)'"
             f":h='if(gt(iw,ih),trunc(min({side},ih)/2)*2,-2)'")
    cmd = [settings.FFMPEG, "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", scale, "-fpsmax", "30",
           "-c:v", "libx264", "-preset", "fast", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-crf", "23", "-maxrate", f"{MAX_KBPS}k", "-bufsize", f"{MAX_KBPS * 2}k",
           "-g", "60", "-keyint_min", "30",            # a keyframe every 2 s: quick seeks
           "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2",
           "-movflags", "+faststart", "-f", "mp4"]
    if background:
        cmd += ["-threads", "2"]
    cmd.append(str(out))
    if background and os.name != "nt" and shutil.which("nice"):
        cmd = ["nice", "-n", "15", extract.resolve(cmd[0]), *cmd[1:]]   # behind the editor and the dashboard
    return cmd


def make(src: str | Path, out: str | Path, background: bool = False) -> Path:
    """Make the light copy of src at out. Written under a temporary name and moved into place
    at the end, so a half-made file is never served. RuntimeError when ffmpeg fails."""
    src, out = Path(src), Path(out)
    part = out.with_name(out.name + ".part")
    try:
        extract._run(_cmd(src, part, background))
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return out


def faststart(path: str | Path) -> bool:
    """True when an MP4's index (moov) comes before its media (mdat): the player can start
    before the whole file has arrived."""
    try:
        with open(path, "rb") as f:
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return False
                size, kind = struct.unpack(">I4s", head)
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0] - 8
                if kind == b"moov":
                    return True
                if kind == b"mdat":
                    return False
                if size < 8:
                    return False
                f.seek(size - 8, 1)
    except OSError:
        return False


# ── What plays ────────────────────────────────────────────────────────────────

def clip_source(row) -> Path | None:
    """The best local file to make a clip's light copy from: its original, else an older preview
    (the original's server copy may have been freed)."""
    clip_dir = Path(row["clip_dir"])
    for p in (Path(row["video_path"] or ""), *(clip_dir / n for n in OLD_NAMES)):
        if p.name and p.is_file():
            return p
    return None


def clip_video(row, want: bool = True) -> str | None:
    """What a clip plays in Review: its light copy, else an older preview, else the original.
    want: a missing light copy is queued (made in the background)."""
    clip_dir = Path(row["clip_dir"])
    light = clip_dir / NAME
    if light.is_file():
        return str(light)
    src = clip_source(row)
    if want and src is not None and row["status"] == "tagged" and settings.LIGHT_COPIES:
        request(src, light)
    for n in OLD_NAMES:
        if (clip_dir / n).is_file():
            return str(clip_dir / n)
    return row["video_path"]


def draft_video(path: str | None, want: bool = True) -> str | None:
    """What a draft plays in Review: its light copy, else (queued) the full file."""
    if not path:
        return None
    light = light_for(path)
    if light.is_file():
        return str(light)
    if want and Path(path).is_file() and settings.LIGHT_COPIES:
        request(path, light)
    return path


def for_draft(path: str | Path) -> Path | None:
    """Make a new draft's light copy now (as it goes to review). None if it couldn't be made:
    Review then plays the full file."""
    if not settings.LIGHT_COPIES:
        return None
    try:
        return make(path, light_for(path))
    except (RuntimeError, OSError) as e:
        log.warning("No light copy for %s: %s", Path(path).name, e)
        return None


def drop_old(clip_dir: str | Path, keep_one: bool = True) -> int:
    """Once a clip has its light copy: delete the older, bigger previews. Returns the bytes freed."""
    clip_dir = Path(clip_dir)
    if not (clip_dir / NAME).is_file():
        return 0
    freed = 0
    for n in OLD_NAMES:
        p = clip_dir / n
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
        except OSError:
            pass
    return freed


# ── Background: one at a time ─────────────────────────────────────────────────

_queue: list[tuple[Path, Path]] = []
_failed: set[str] = set()        # never retried in this run (a broken file mustn't loop)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_current: str | None = None


def request(src: str | Path, out: str | Path) -> None:
    """Queue a light copy (played ones first: they're asked for when someone presses play)."""
    src, out = Path(src), Path(out)
    key = str(out)
    with _lock:
        if key in _failed or key == _current or any(str(o) == key for _, o in _queue):
            return
        _queue.insert(0, (src, out))
        del _queue[QUEUE_MAX:]   # the least recently asked for wait for the idle backfill
        _start()


def _start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_work, name="previews", daemon=True)
        _thread.start()


def _work() -> None:
    global _current
    while True:
        with _lock:
            if not _queue:
                _current = None
                return
            src, out = _queue.pop(0)
            _current = str(out)
        if out.is_file() or not src.is_file():
            continue
        try:
            make(src, out, background=True)
            if out.name == NAME and Path(src).parent == out.parent:
                drop_old(out.parent)
            log.info("Light copy made for %s", out.parent.name if out.name == NAME else out.name)
        except Exception as e:  # never let a preview take the worker down
            with _lock:
                _failed.add(str(out))
            log.warning("No light copy for %s: %s", src.name, e)


def busy() -> bool:
    return _thread is not None and _thread.is_alive()


def tick(conn, idle: bool) -> None:
    """From the worker loop: while it has nothing else to do, queue the next light copy still
    missing (drafts waiting for review first, then the newest clips). One at a time. Never raises."""
    if not idle or busy() or not settings.LIGHT_COPIES:
        return
    try:
        for row in conn.execute("""SELECT video_path FROM productions WHERE video_path IS NOT NULL
                                   AND COALESCE(format, '') <> 'carousel' AND removed_at IS NULL
                                   ORDER BY (status = 'ready') DESC, updated_at DESC"""):
            light = light_for(row["video_path"])
            if not light.is_file() and str(light) not in _failed and Path(row["video_path"]).is_file():
                request(row["video_path"], light)
                return
        for row in conn.execute("SELECT * FROM clips WHERE status = 'tagged' ORDER BY id DESC"):
            light = Path(row["clip_dir"]) / NAME
            if light.is_file() or str(light) in _failed:
                continue
            src = clip_source(row)
            if src is not None:
                request(src, light)
                return
    except Exception as e:
        log.debug("Light copies: %s", e)
