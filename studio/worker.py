"""
Studio worker — phase 1 of the Content Studio plan ("capture and understand").

Watches the inbox folder the phone uploads into. For each new video:
  1. waits until the upload has finished syncing
  2. moves it into the library: Library/<YYYY-MM>/<date>_<time>_<name>/
  3. extracts audio and frames with ffmpeg
  4. transcribes locally with Whisper (word timestamps; eGPU when attached)
  5. has Claude tag brand, format, topics and best moments
  6. saves everything to the SQLite catalogue and pings Bryan on Telegram
It also serves the review dashboard (dashboard.py) and runs the editor queue
(editor.py: planned and sent-back productions become drafts) while it runs.

Usage (from the studio folder, inside its venv):
  python worker.py                 run forever (what the scheduled task does)
  python worker.py --once          process whatever is in the inbox, then exit
  python worker.py --file C:\\x.mp4 process one file now (moves it into the library)
  python worker.py --retry-failed  reprocess clips that failed, were interrupted or skipped Claude
  python worker.py --check         check ffmpeg, GPU, API key and Telegram, then exit
  python worker.py --rebuild-previews  remake missing dashboard previews (also done at start)
  python worker.py --edit 12       edit production 12 now (normally the editor queue does this)
  python worker.py --trial "talking head"  ideas -> one edited draft, with footage advice and costs
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
        analysis = classify.classify(meta, frames, transcript, source_name, ref=f"clip:{clip_id}")
        (clip_dir / "analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False),
                                                encoding="utf-8")

    catalog.save_tags(conn, clip_id, meta, transcript, analysis)
    notify.digest.add("processed")


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
        notify.digest.add("duplicate")
        return

    try:
        meta = extract.probe(path)
    except RuntimeError as e:
        _set_aside(path, "Unreadable", str(e))
        notify.digest.add("unreadable")
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
        notify.digest.add("failed")


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


def run_editor(stop_after_one: bool = False) -> None:
    """Background editing queue: one production at a time, oldest first."""
    import catalog
    import editor
    import notify

    missing = editor.ready()
    if missing:
        log.warning("Editor not started: %s (see README section 9)", missing)
        return
    conn = catalog.connect()
    # A crash mid-edit leaves 'producing'; put those back in the queue.
    conn.execute("UPDATE productions SET status = 'planned' WHERE status = 'producing'")
    conn.commit()
    log.info("Editor ready")
    while True:
        production_id = editor.next_job(conn)
        if production_id is None:
            if stop_after_one:
                return
            time.sleep(15)
            continue
        title = conn.execute("SELECT title FROM productions WHERE id = ?", (production_id,)).fetchone()["title"]
        log.info("Editing production %s: %s", production_id, title)
        try:
            editor.edit(conn, production_id)
            notify.digest.add("draft")
        except Exception as e:
            log.exception("Edit failed for production %s", production_id)
            conn.execute("UPDATE productions SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                         (f"{type(e).__name__}: {e}"[:2000], catalog.now(), production_id))
            conn.commit()
            notify.digest.add("edit_failed")
        if stop_after_one:
            return


def trial(conn, focus: str) -> int:
    """One end-to-end test: suggest ideas, make the best talking-head one, edit it,
    and print what came out, what footage would help, and what it all cost."""
    import catalog
    import editor
    import ideas
    import usage

    print(f"1/3 Suggesting ideas (focus: {focus}) ...", flush=True)
    batch, count = ideas.generate(conn, f"{focus}. Include at least one strong talking-head post.", count=3)
    rows = conn.execute("SELECT * FROM ideas WHERE batch = ? ORDER BY id", (batch,)).fetchall()
    for r in rows:
        print(f"   - [{r['brand']} / {r['format']}] {r['title']}: {r['hook']}")
    if not rows:
        print("No ideas came back. Add more clips, or try a different focus.")
        return 1
    talking = [r for r in rows if r["format"] == "talking_head"]
    with_music = [r for r in talking if r["brand"] in settings.MUSIC_ACCOUNTS]
    pick = (with_music or talking or list(rows))[0]
    print(f"\n2/3 Editing: {pick['title']} ({pick['brand']}, {pick['format']}). This takes a few minutes ...",
          flush=True)
    production_id = catalog.make_production(conn, pick["id"])
    editor.edit(conn, production_id)
    p = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    edl, qa, music_info = json.loads(p["edl"]), json.loads(p["qa"]), json.loads(p["music"])

    print(f"\n3/3 Draft ready: {p['video_path']} ({p['duration']:.0f}s)")
    print(f"   Editor: {edl['summary']}")
    print(f"   Graphics: {len(edl.get('graphics', []))}  |  Captions: on  |  Hook text: {edl['hook_text'] or '(none)'}")
    if music_info.get("source") == "epidemic":
        print(f"   Music: {music_info['title']} by {music_info['artist']} (Epidemic Sound)")
    else:
        print(f"   Music: {music_info.get('note') or 'none: ' + music_info.get('error', 'not available')}")
    match = f"{qa['match']:.0%}" if qa.get("match") is not None else "n/a"
    print(f"   Checks: {match} of words intact, {qa.get('lufs')} LUFS"
          + (f", possibly clipped: {', '.join(qa['missing'])}" if qa.get("missing") else ""))
    wishes = json.loads(pick["wishlist"] or "[]") + edl.get("missing_shots", [])
    if wishes:
        print("\n   Film these to make it better:")
        for w in wishes:
            print(f"   - {w['shot']}  ({w['why']})")
    idea_cost = usage.total_for(conn, f"ideas:{batch}")
    edit_cost = usage.total_for(conn, f"production:{production_id}")
    month = usage.month_summary(conn)
    print(f"\n   API cost: ideas ${idea_cost:.2f} (for {count} ideas) + edit and music ${edit_cost:.2f}"
          f" = ${idea_cost + edit_cost:.2f}. This month so far: ${month['usd']:.2f} "
          + "(" + ", ".join(f"{k} ${v['usd']:.2f}" for k, v in month["by_kind"].items()) + ")")
    print(f"\n   Review it on the dashboard: To review, or http://127.0.0.1:{settings.DASHBOARD_PORT}/#/production/{production_id}")
    return 0


def check_editor() -> bool:
    """Phase 5 prerequisites: Node, HyperFrames and its Chrome, a real test render,
    and the Epidemic Sound connection."""
    import music
    import render

    ok = True

    def line(label: str, fn, fix: str = "") -> bool:
        try:
            print(f"{label:<12}: {fn()}")
            return True
        except Exception as e:
            print(f"{label:<12}: NOT READY ({e}){f' - {fix}' if fix else ''}")
            return False

    ok &= line("Node.js", render.node_version)
    if line("HyperFrames", render.hyperframes_version, "in studio\\render run: npm ci"):
        if line("Chrome", render.browser_path):
            ok &= line("Render test", lambda: f"2-second vertical test rendered in {render.smoke_test():.0f}s")
        else:
            ok = False
    else:
        ok = False
    if settings.EPIDEMIC_SOUND_API_KEY:
        ok &= line("Epidemic", music.check)
    else:
        print("Epidemic    : no key yet (EPIDEMIC_SOUND_API_KEY); edits will have no music")
    print(f"Music for   : {', '.join(sorted(settings.MUSIC_ACCOUNTS)) or 'no account'}")
    return ok


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
    ok = check_editor() and ok
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
    mode.add_argument("--edit", type=int, metavar="ID", help="edit one production now (by its id)")
    mode.add_argument("--trial", metavar="FOCUS", help="ideas -> one edited draft, with advice and costs")
    parser.add_argument("--no-claude", action="store_true", help="skip Claude tagging")
    parser.add_argument("--no-dashboard", action="store_true", help="don't serve the review dashboard")
    parser.add_argument("--no-editor", action="store_true", help="don't edit planned productions")
    args = parser.parse_args()

    settings.ensure_folders()
    _setup_logging()
    if args.check:
        return check()
    if not _single_instance():
        log.info("Another studio worker is already running; exiting.")
        return 0

    import catalog
    import notify
    conn = catalog.connect()
    use_claude = not args.no_claude

    if args.file:
        ingest(conn, args.file.resolve(), use_claude)
        notify.digest.flush(force=True)
        return 0
    if args.retry_failed:
        retry_failed(conn, use_claude)
        notify.digest.flush(force=True)
        return 0
    if args.rebuild_previews:
        rebuild_previews(conn)
        return 0
    if args.trial:
        return trial(conn, args.trial)
    if args.edit:
        conn.execute("UPDATE productions SET status = 'planned' WHERE id = ? AND status NOT IN ('revising')",
                     (args.edit,))
        conn.commit()
        import editor
        editor.edit(conn, args.edit)
        return 0

    if not args.once:
        import threading
        if not args.no_dashboard:
            import dashboard
            threading.Thread(target=dashboard.run, name="dashboard", daemon=True).start()
        if not args.no_editor:
            threading.Thread(target=run_editor, name="editor", daemon=True).start()

    log.info("Studio worker started; watching %s every %ss", INBOX, POLL_SECONDS)
    if not args.once:
        retry_failed(conn, use_claude)  # pick up anything a crash or outage left behind
        rebuild_previews(conn)
    editor_on = not args.once and not args.no_editor
    while True:
        for path in inbox_videos():
            try:
                ingest(conn, path, use_claude)
            except Exception:  # never let one bad file stop the loop
                log.exception("Unexpected error on %s", path.name)
        if args.once:
            notify.digest.flush(force=True)
            return 0
        notify.digest.flush(busy=_busy(conn, editor_on))
        time.sleep(POLL_SECONDS)


def _busy(conn, editor_on: bool) -> bool:
    """Still mid-batch: uploads in the inbox (synced or not) or edits queued."""
    if INBOX.exists() and any(p.suffix.lower() in VIDEO_EXTS and not p.name.startswith((".", "~"))
                              for p in INBOX.iterdir()):
        return True
    statuses = ("planned", "revising", "producing") if editor_on else ("producing",)
    marks = ",".join("?" * len(statuses))
    return conn.execute(f"SELECT 1 FROM productions WHERE status IN ({marks}) LIMIT 1", statuses).fetchone() is not None


if __name__ == "__main__":
    sys.exit(main())
