"""
Studio worker — phase 1 of the Content Studio plan ("capture and understand").

Watches the inbox folder the phone uploads into. For each new video:
  1. waits until the upload has finished syncing
  2. moves it into the library: Library/<YYYY-MM>/<date>_<time>_<name>/
  3. extracts audio and frames with ffmpeg
  4. transcribes locally with Whisper (word timestamps; eGPU when attached)
  5. has Claude tag brand, format, topics and best moments
  6. saves everything to the SQLite catalogue and pings Bryan on Telegram
It also serves the review dashboard (dashboard.py) while it runs.

Usage (from the studio folder, inside its venv):
  python worker.py                 run forever (what the scheduled task does)
  python worker.py --once          process whatever is in the inbox, then exit
  python worker.py --file C:\\x.mp4 process one file now (moves it into the library)
  python worker.py --retry-failed  reprocess clips that failed, were interrupted or skipped Claude
  python worker.py --check         check ffmpeg, GPU, API key and Telegram, then exit
  python worker.py --rebuild-previews  remake missing dashboard previews (also done at start)
  add --no-claude to transcribe and catalogue without tagging (no API calls)
  add --no-dashboard to skip the review site (http://127.0.0.1:8765 by default)
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import settings
from settings import INBOX, LIBRARY, LOGS, POLL_SECONDS, ROOT, STABLE_SECONDS, VIDEO_EXTS

log = logging.getLogger("studio")


# ── Setup ─────────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOGS / "worker.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw, where there is no console
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    # Keep third-party chatter (HTTP requests, model downloads) out of the log.
    for noisy in ("httpx", "httpx2", "anthropic", "faster_whisper", "urllib3", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_lock_handle = None


def _single_instance() -> bool:
    """Hold an OS lock on ROOT/worker.lock so a second copy (say, the scheduled
    task firing while one is already running) exits instead of double-processing."""
    global _lock_handle
    _lock_handle = open(ROOT / "worker.lock", "a+")
    _lock_handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


# ── Inbox ─────────────────────────────────────────────────────────────────────

def is_ready(path: Path) -> bool:
    """A video that has stopped changing and can be opened — i.e. fully synced."""
    try:
        stat = path.stat()
        if stat.st_size == 0 or time.time() - stat.st_mtime < STABLE_SECONDS:
            return False
        with open(path, "rb") as f:  # the sync client holds a lock while still writing
            f.read(1)
        return True
    except OSError:
        return False


def inbox_videos() -> list[Path]:
    if not INBOX.exists():
        return []
    return sorted(
        p for p in INBOX.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith((".", "~"))
        and is_ready(p)
    )


def fingerprint(path: Path) -> str:
    """Size plus the first and last 4 MB — enough to spot a re-upload of the same
    clip without hashing gigabytes of 4K video."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4 << 20))
        if size > 8 << 20:
            f.seek(-(4 << 20), os.SEEK_END)
            h.update(f.read())
    return h.hexdigest()[:32]


def _recorded_local(meta: dict, path: Path) -> datetime:
    raw = meta.get("recorded_at")
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone() if dt.tzinfo else dt
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _unique(base: Path) -> Path:
    """base, or base with _2, _3... before its extension if that name is taken."""
    candidate, n = base, 2
    while candidate.exists():
        candidate, n = base.with_name(f"{base.stem}_{n}{base.suffix}"), n + 1
    return candidate


def _set_aside(path: Path, folder: str, reason: str) -> None:
    """Move a file the worker can't use out of the inbox so it isn't retried forever."""
    dest_dir = ROOT / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(_unique(dest_dir / path.name)))
    log.warning("%s moved to %s: %s", path.name, folder, reason)


# ── Processing ────────────────────────────────────────────────────────────────

def tag_clip(conn, clip_id: int, clip_dir: Path, video: Path, source_name: str,
             use_claude: bool) -> None:
    """Steps 3-6 for a clip already in the library. Safe to rerun (--retry-failed)."""
    import catalog
    import extract
    import notify
    import transcribe

    meta = extract.probe(video)
    frames = extract.extract_frames(video, clip_dir / "frames", meta["duration"])
    try:
        extract.make_preview(video, clip_dir / extract.PREVIEW_NAME)
    except RuntimeError as e:  # the dashboard falls back to the original
        log.warning("No preview for %s: %s", source_name, e)

    transcript = None
    if meta["has_audio"]:
        wav = extract.extract_audio(video, clip_dir / "audio.wav")
        try:
            transcript = transcribe.transcribe(wav)
        finally:
            wav.unlink(missing_ok=True)  # big and easy to regenerate
        (clip_dir / "transcript.json").write_text(json.dumps(transcript, indent=2, ensure_ascii=False),
                                                  encoding="utf-8")
        log.info("Transcribed %s on %s: %d words", source_name, transcript["device"],
                 len(transcript["words"]))

    analysis = None
    if use_claude:
        import classify
        analysis = classify.classify(meta, frames, transcript, source_name)
        (clip_dir / "analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False),
                                                encoding="utf-8")

    catalog.save_tags(conn, clip_id, meta, transcript, analysis)
    link = f"{settings.DASHBOARD_URL}/#/clip/{clip_id}" if settings.DASHBOARD_URL else ""
    notify.send(notify.clip_tagged(meta, analysis, source_name, link))


def ingest(conn, path: Path, use_claude: bool) -> None:
    """Steps 1-6 for one file in the inbox."""
    import catalog
    import extract
    import notify

    source_name = path.name
    fp = fingerprint(path)
    existing = catalog.find(conn, fp)
    if existing is not None and existing["status"] in ("tagged", "transcribed"):
        _set_aside(path, "Duplicates", f"same clip as {existing['video_path']}")
        notify.send(f"Studio: {source_name} is a duplicate of a clip already in the library; set aside.")
        return

    try:
        meta = extract.probe(path)
    except RuntimeError as e:
        _set_aside(path, "Unreadable", str(e))
        notify.send(f"Studio: couldn't read {source_name} as a video; moved to Unreadable "
                    "(details in worker.log).")
        return

    when = _recorded_local(meta, path)
    clip_dir = _unique(LIBRARY / when.strftime("%Y-%m") / f"{when:%Y-%m-%d_%H%M}_{path.stem}")
    clip_dir.mkdir(parents=True)
    video = clip_dir / path.name
    shutil.move(str(path), str(video))  # copy + delete when the inbox is on another drive
    clip_id = catalog.start(conn, fp, source_name, clip_dir, video)
    log.info("Ingesting %s -> %s", source_name, clip_dir)

    try:
        tag_clip(conn, clip_id, clip_dir, video, source_name, use_claude)
    except Exception as e:
        log.exception("Failed on %s", source_name)
        catalog.mark_failed(conn, clip_id, f"{type(e).__name__}: {e}")
        notify.send(f"Studio: {source_name} is saved in the library but tagging failed "
                    f"({type(e).__name__}: {e}). It is retried next time the worker starts.")


def retry_failed(conn, use_claude: bool) -> None:
    import catalog
    for row in catalog.unfinished(conn):
        video = Path(row["video_path"])
        if not video.exists():
            log.warning("Skipping clip %s: %s no longer exists", row["id"], video)
            continue
        log.info("Retrying %s", row["source_name"])
        catalog.start(conn, row["fingerprint"], row["source_name"], Path(row["clip_dir"]), video)
        try:
            tag_clip(conn, row["id"], Path(row["clip_dir"]), video, row["source_name"], use_claude)
        except Exception as e:
            log.exception("Retry failed on %s", row["source_name"])
            catalog.mark_failed(conn, row["id"], f"{type(e).__name__}: {e}")


def rebuild_previews(conn) -> None:
    """Make the current dashboard preview for clips that lack it (older clips got a
    smaller, softer one), and drop the old preview once replaced."""
    import extract
    for row in conn.execute("SELECT * FROM clips WHERE status != 'processing' ORDER BY id"):
        clip_dir, video = Path(row["clip_dir"]), Path(row["video_path"])
        target = clip_dir / extract.PREVIEW_NAME
        if target.exists() or not video.exists():
            continue
        log.info("Making a 1080p preview for %s", row["source_name"])
        try:
            extract.make_preview(video, target)
            (clip_dir / "preview.mp4").unlink(missing_ok=True)
        except RuntimeError as e:
            log.warning("No preview for %s: %s", row["source_name"], e)


def check() -> int:
    """Print what the worker will use, and send a Telegram test message."""
    import extract
    import notify
    import transcribe

    ok = True
    print(f"Studio root : {ROOT}")
    print(f"Inbox       : {INBOX} ({'exists' if INBOX.exists() else 'MISSING'})")
    try:
        for name, version in extract.tool_versions().items():
            print(f"{name:<12}: {version}")
    except (OSError, RuntimeError) as e:
        ok = False
        print(f"ffmpeg      : NOT FOUND ({e}) - install it or set FFMPEG_PATH/FFPROBE_PATH")
    try:
        gpus = transcribe.gpu_count()
        print(f"CUDA GPUs   : {gpus}" + ("" if gpus else " (Whisper will run on the CPU)"))
    except Exception as e:
        print(f"CUDA GPUs   : check failed ({e}); Whisper will run on the CPU")
    print(f"Whisper     : device={settings.WHISPER_DEVICE} model={settings.WHISPER_MODEL or 'auto'}")
    print(f"Claude      : {settings.CLAUDE_MODEL}, effort {settings.CLAUDE_EFFORT}, API key "
          f"{'set' if settings.ANTHROPIC_API_KEY else 'MISSING'}")
    ok = ok and bool(settings.ANTHROPIC_API_KEY)
    print(f"Brands file : {settings.BRANDS_FILE} ({'found' if settings.BRANDS_FILE.exists() else 'MISSING'})")
    if notify.configured():
        sent = notify.send("Studio worker check: Telegram notifications are working.")
        print(f"Telegram    : {'test message sent' if sent else 'SEND FAILED - check token and chat ID'}")
        ok = ok and sent
    else:
        print("Telegram    : not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio worker: tag phone footage as it arrives.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process the inbox once, then exit")
    mode.add_argument("--file", type=Path, help="process one video file now")
    mode.add_argument("--retry-failed", action="store_true", help="reprocess failed clips")
    mode.add_argument("--check", action="store_true", help="check the setup, then exit")
    mode.add_argument("--rebuild-previews", action="store_true", help="remake missing dashboard previews")
    parser.add_argument("--no-claude", action="store_true", help="skip Claude tagging")
    parser.add_argument("--no-dashboard", action="store_true", help="don't serve the review dashboard")
    args = parser.parse_args()

    settings.ensure_folders()
    _setup_logging()
    if args.check:
        return check()
    if not _single_instance():
        log.info("Another studio worker is already running; exiting.")
        return 0

    import catalog
    conn = catalog.connect()
    use_claude = not args.no_claude

    if args.file:
        ingest(conn, args.file.resolve(), use_claude)
        return 0
    if args.retry_failed:
        retry_failed(conn, use_claude)
        return 0
    if args.rebuild_previews:
        rebuild_previews(conn)
        return 0

    if not args.once and not args.no_dashboard:
        import threading
        import dashboard
        threading.Thread(target=dashboard.run, name="dashboard", daemon=True).start()

    log.info("Studio worker started; watching %s every %ss", INBOX, POLL_SECONDS)
    if not args.once:
        retry_failed(conn, use_claude)  # pick up anything a crash or outage left behind
        rebuild_previews(conn)
    while True:
        for path in inbox_videos():
            try:
                ingest(conn, path, use_claude)
            except Exception:  # never let one bad file stop the loop
                log.exception("Unexpected error on %s", path.name)
        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
