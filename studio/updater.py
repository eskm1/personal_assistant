"""
Keeps Studio up to date by itself, so a new version never needs clicks.

Every few minutes (STUDIO_AUTO_UPDATE_MINUTES, default 10; 0 turns it off) the
worker checks the git branch it runs from. When new commits are there and Studio
is idle (nothing uploading, ingesting, editing or running a Claude job), it:
  1. fast-forwards to them (never overwrites edits made on the laptop: if git
     can't fast-forward cleanly, it stops and says so),
  2. installs new Python or HyperFrames packages if the update changed them,
  3. checks the new code imports cleanly, and goes back to the old version if not,
  4. restarts the worker in the background (the dashboard is back in seconds).
The owner can also press "Update now" in the dashboard's account menu.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import settings

log = logging.getLogger("studio.update")

HERE = Path(__file__).resolve().parent
STATE = settings.ROOT / "update.json"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# Never let git stop to ask for a password in the background.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
# Everything the worker loads; if any fails to import, the update is undone.
_MODULES = "worker, dashboard, plan_routes, plan, editor, framing, draft_review, quality, resolve_export, ideas, classify, music, auth, cloud_sync, style_study, onedrive, subtitles, uploads, cloud_auth, planner_site, footage_ideas, content_plan, carousel, slide_images, concepts, topics, self_fix, learning, ref_library, taste, ig_signin, cleanup, previews, instagram, posting, post_results, posting_routes, webassets, restyle, fix_actions, spelling, glossary, wording, voice_file, igkit, igkit.humanize, igkit.detect, igkit.hookscore, igkit.beats, igkit.caption, activity, help_desk, help_routes, usage_report"

requested = False  # set by the dashboard's "Update now"
restart_requested = False  # set by "Restart Studio" (say, after editing .env)
_last_error: str | None = None


class UpdateError(RuntimeError):
    pass


def _git(*args: str, timeout: int = 120) -> str:
    result = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, env=_GIT_ENV, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise UpdateError(f"git {args[0]}: {(result.stderr or result.stdout).strip()[-300:]}")
    return result.stdout.strip()


_running: dict | None = None


def version() -> dict:
    """The running code (read once: it only changes with a restart) and update status."""
    global _running
    if _running is None:
        try:
            commit, subject, date = _git("log", "-1", "--format=%h%x1f%s%x1f%cI").split("\x1f")
        except (UpdateError, OSError, ValueError):
            commit, subject, date = "unknown", "", ""
        last = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        _running = {"commit": commit, "subject": subject, "date": date, "last_update": last}
    return {**_running, "auto_minutes": settings.AUTO_UPDATE_MINUTES, "error": _last_error,
            "requested": requested or restart_requested}


def _upstream() -> str:
    try:
        return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except UpdateError:
        raise UpdateError("this checkout isn't tracking a branch on GitHub, so there's nothing to update from")


def pending() -> list[str]:
    """Subjects of commits waiting on the branch (fetches first)."""
    upstream = _upstream()
    remote, branch = upstream.split("/", 1)
    _git("fetch", "--quiet", remote, branch, timeout=180)
    out = _git("log", "--format=%s", f"HEAD..{upstream}")
    return [line for line in out.splitlines() if line]


def _run(args: list[str], cwd: Path, what: str, timeout: int = 1800) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise UpdateError(f"{what} failed: {(result.stderr or result.stdout).strip()[-300:]}")


def apply() -> dict:
    """Move to the new commits, install what changed and check the result.
    Returns what was applied; raises UpdateError (with the old code restored) if not."""
    upstream = _upstream()
    old = _git("rev-parse", "HEAD")
    changed = set(_git("diff", "--name-only", old, upstream).splitlines())
    subjects = _git("log", "--format=%s", f"{old}..{upstream}").splitlines()
    try:
        _git("merge", "--ff-only", upstream)
    except UpdateError as e:
        raise UpdateError("couldn't update cleanly (files on the laptop were changed, or the branch was "
                          f"rewritten). Run git status in the studio folder. {e}")
    try:
        if any(p.endswith("studio/requirements.txt") for p in changed):
            log.info("Update changed requirements.txt; installing")
            _run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], HERE, "pip install")
        if any(p.endswith("studio/render/package-lock.json") for p in changed):
            npm = shutil.which("npm")
            if npm:
                log.info("Update changed the renderer's packages; running npm ci")
                _run([npm, "ci", "--no-audit", "--no-fund"], HERE / "render", "npm ci")
                _ensure_browser()
        _run([sys.executable, "-c", f"import {_MODULES}"], HERE, "checking the new version", timeout=300)
    except (UpdateError, OSError, subprocess.TimeoutExpired) as e:
        # --keep goes back without touching anything else on the laptop.
        _git("reset", "--keep", old)
        raise UpdateError(f"{e}. Went back to the previous version.")
    info = {"from": old[:7], "to": _git("rev-parse", "--short", "HEAD"), "subjects": subjects[:10],
            "at": time.strftime("%Y-%m-%d %H:%M")}
    STATE.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def _ensure_browser() -> None:
    """A new HyperFrames may pin a newer Chrome: fetch it now rather than during the
    next render. Not fatal: the render would fetch it anyway."""
    import render
    try:
        result = render.hyperframes(["browser", "ensure"], timeout=900)
        if result.returncode != 0:
            log.warning("Couldn't fetch the renderer's Chrome after the update: %s", (result.stderr or "")[-300:])
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        log.warning("Couldn't fetch the renderer's Chrome after the update: %s", e)


def restart() -> None:
    """Start a fresh worker (it waits for this one to exit) and exit. On the server,
    systemd starts the fresh one (studio.service has Restart=always)."""
    if os.environ.get("INVOCATION_ID"):  # set by systemd for the services it runs
        log.info("Restarting the worker for the update")
        logging.shutdown()
        os._exit(0)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    subprocess.Popen([exe, str(HERE / "worker.py"), *sys.argv[1:]], cwd=HERE, creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env={**os.environ, "STUDIO_RESTARTED": "1"},
                     start_new_session=os.name != "nt")
    log.info("Restarting the worker for the update")
    logging.shutdown()
    os._exit(0)  # releases the lock and the dashboard's port at once


def tick(idle: bool) -> None:
    """Called from the worker's main loop. Updates and restarts when it's time."""
    global requested, restart_requested, _last_error
    if restart_requested and idle:
        restart_requested = False
        log.info("Restart requested from the dashboard")
        restart()
    if not (requested or (settings.AUTO_UPDATE_MINUTES and idle)):
        return
    now = time.time()
    if not requested and now - getattr(tick, "_last", 0) < settings.AUTO_UPDATE_MINUTES * 60:
        return
    if requested and not idle:
        return  # do it as soon as the current work finishes
    tick._last, requested = now, False
    try:
        if not pending():
            _last_error = None
            return
        info = apply()
    except (UpdateError, OSError, subprocess.TimeoutExpired) as e:
        message = str(e)
        if message != _last_error:  # tell Bryan once, not every check
            log.warning("Update skipped: %s", message)
            import notify
            notify.send(f"Studio couldn't update itself: {message}")
        _last_error = message
        return
    _last_error = None
    log.info("Updated %s -> %s: %s", info["from"], info["to"], "; ".join(info["subjects"]))
    restart()
