"""
A reference's own files (cloud/CONTRACT.md, "Reference downloads: the queue, the sign-in and uploads"):
the video, or the slides, of a post Studio didn't have to download, because someone uploaded them on
the reference's page ("Upload the file instead") or dropped them in OneDrive (Studio Inbox >
References > Ours / Inspiration).

They are kept on the Studio machine in References/.files/<reference id>/ (current/ + manifest.json),
and the sync analyses them exactly as if they had been downloaded (style_study.analyse_files: the
same measuring and the same look), keeping the reference's link for its likes and views.

Uploads use the planner's upload pieces (uploads.py): POST /api/uploads with {reference_id, set,
index, name, size, type}, the same PUTs, then

  POST /api/uploads/references/<id>/ready  {set}  -> {kind, files}: that set becomes the reference's
                                                     files and it is analysed again
  GET  /api/uploads/references/<id>                -> {kind, files, at, from} or {}

From OneDrive: a file or folder whose name carries an Instagram post's code (DeKIm8tsyWj, matched
against the references' links) attaches to that reference instead of making a new one; a folder of
pictures is one carousel reference (slides in name order), taken once it has been quiet for
FOLDER_QUIET seconds and OneDrive has nothing more coming for it. A file reference made from such a
file before (a duplicate of a link reference) is merged into it once: its notes, analysis and file
go across and the extra one is retired. Loose pictures in References/Ours or /Inspiration are left
alone, as before.
"""
import json
import logging
import random
import re
import shutil
import string
import time
from pathlib import Path

from flask import Blueprint, g, jsonify

import catalog
import settings

log = logging.getLogger("studio.reffiles")
bp = Blueprint("ref_files", __name__)

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_VIDEO = 2 << 30          # biggest video for a reference
MAX_IMAGE = 60 << 20         # biggest picture
MAX_SLIDES = 20              # pictures in one set (Claude looks at the first style_study.MAX_SLIDES)
FOLDER_QUIET = 300           # seconds a dropped folder must be still before it's taken
CODE_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]{5,})", re.I)
MIN_CODE = 10                # a shorter code could match a name by chance


def _root() -> Path:
    return settings.REFERENCES / ".files"


def _dir(ref_id: int) -> Path:
    return _root() / str(int(ref_id))


def natural(name: str):
    """Sort key: the numbers in a name read as numbers (IMG_2 before IMG_10)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(name))]


def kind_of(files: list[Path]) -> str | None:
    """"video" (exactly one video), "images" (pictures only) or None (nothing usable, or a mix)."""
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    images = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
    if len(videos) == 1 and not images:
        return "video"
    if images and not videos:
        return "images"
    return None


def files_for(ref_id: int) -> dict | None:
    """{kind, files: [paths in order], at, from} when the reference has files of its own, else None."""
    d = _dir(ref_id)
    try:
        meta = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = [d / "current" / n for n in meta.get("files") or []]
    files = [f for f in files if f.is_file()]
    if not files or meta.get("kind") not in ("video", "images"):
        return None
    return {**meta, "files": files}


def attach(ref_id: int, sources: list[Path], how: str, by: str = "", move: bool = True, extra: dict | None = None) -> dict:
    """Make these files the reference's own (replacing any it had): one video, or pictures in the
    order given. Raises ValueError with a plain reason."""
    sources = [Path(s) for s in sources]
    kind = kind_of(sources)
    if not kind:
        raise ValueError("Pick one video, or the post's pictures (JPEG, PNG, WebP or HEIC), not both")
    if kind == "images" and len(sources) > MAX_SLIDES:
        raise ValueError(f"That's {len(sources)} pictures: a post has at most {MAX_SLIDES}")
    d = _dir(ref_id)
    d.mkdir(parents=True, exist_ok=True)
    fresh = d / f".new-{int(time.time() * 1000)}"
    fresh.mkdir()
    names = []
    try:
        for n, src in enumerate(sources, 1):
            name = f"{n:02d}{src.suffix.lower()}"
            (shutil.move if move else shutil.copy2)(str(src), str(fresh / name))
            names.append(name)
        old = d / "current"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        fresh.replace(old)
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
    meta = {"kind": kind, "files": names, "at": catalog.now(), "from": how, "by": by, **(extra or {})}
    (d / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log.info("Reference %s has its own %s now (%d file%s, from %s)", ref_id, kind, len(names),
             "" if len(names) == 1 else "s", how)
    return meta


def check(files: list[Path]) -> str | None:
    """Why these files can't be analysed (a video that won't play, a picture that won't open), or None."""
    import style_study
    kind = kind_of(files)
    if kind == "video":
        wrong = style_study.video_problem(files[0])
        return f"Studio can't play that video ({wrong})" if wrong else None
    if kind != "images":
        return "Pick one video, or the post's pictures, not both"
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    for f in files:
        try:
            with Image.open(f) as im:
                im.verify()
        except Exception:
            return f"Studio can't open the picture {f.name}"
    return None


def pending(api, ref_id: int) -> None:
    """Analyse the reference again (from its own files): its failed state and problem clear."""
    import cloud_sync
    values = {"state": "pending", "error": None}
    if cloud_sync.check_library(api):
        values["problem"] = None
    api.update("reference_analysis", {"reference_id": f"eq.{ref_id}"}, values)


# ── Analysing ─────────────────────────────────────────────────────────────────

def analyse(ref: dict, own: dict, work: Path, client=None) -> dict:
    """style_study's analysis of the reference's own files, as if downloaded; the link's likes and
    views when Instagram may be asked (one look, no download, paced like the rest)."""
    import ref_library
    import style_study
    numbers = None
    url = ref.get("url") or ""
    if "instagram.com" in url.lower() and ref_library.instagram_free():
        try:
            numbers = style_study.post_info(url)
            ref_library.record("ok", library=False)
        except style_study.DownloadFailed as e:
            ref_library.record(e.problem if e.problem in ("rate", "login", "net") else "other", library=False)
            log.info("Reference %s: its numbers weren't read (%s); analysed without them", ref["id"], e.problem)
    return style_study.analyse_files(own["files"], work / "stills", client=client, ref=f"ref:{ref['id']}",
                                     info=numbers)


# ── From OneDrive (cloud_sync.reference_intake) ───────────────────────────────

def codes(refs: list[dict]) -> dict[str, dict]:
    """{Instagram post code: its link reference}, active ones winning over retired ones."""
    out: dict[str, dict] = {}
    for r in sorted(refs, key=lambda r: (r.get("status") == "active", -r["id"])):
        found = {m.group(1) for m in CODE_RE.finditer(r.get("url") or "")}
        if r.get("url") and r.get("shortcode"):
            found.add(str(r["shortcode"]))
        for c in found:
            if len(c) >= MIN_CODE:
                out[c] = r
    return out


def match(name: str, by_code: dict[str, dict]) -> dict | None:
    """The link reference whose post code is in this file or folder name (the longest code wins)."""
    hits = [c for c in by_code if c in str(name)]
    return by_code[max(hits, key=len)] if hits else None


def _quiet(folder: Path, now: float) -> bool:
    """Nothing in the folder has changed for FOLDER_QUIET seconds (ctime: a copy can't backdate it),
    and OneDrive has nothing more on its way into it."""
    try:
        stamps = [folder.stat().st_ctime, folder.stat().st_mtime] + [p.stat().st_ctime for p in folder.iterdir()]
    except OSError:
        return False
    if now - max(stamps) < FOLDER_QUIET:
        return False
    try:
        import onedrive
        return not onedrive.ref_folder_busy(folder)
    except Exception:
        return True


def _images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
                   and not p.name.startswith((".", "~"))), key=lambda p: natural(p.name))


def intake(api, refs: list[dict], sides: dict[str, str], now: float | None = None) -> list[str]:
    """Before the usual video intake: files and folders in Studio Inbox\\References\\<side> that name a
    link reference's post attach to it; a folder of pictures becomes one carousel reference; earlier
    duplicates are merged. Returns lines for the log."""
    import cloud_sync
    now = now or time.time()
    by_code = codes(refs)
    by_name = {r["file_name"]: r for r in refs if r.get("file_name")}
    lines = []
    for side, folder_name in sides.items():
        folder = settings.REFERENCE_INBOX / folder_name
        try:
            entries = sorted(folder.iterdir()) if folder.is_dir() else []
        except OSError:
            continue
        for p in entries:
            if p.name.startswith((".", "~")):
                continue
            try:
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                    target = match(p.name, by_code)
                    st = p.stat()
                    if not target or st.st_size == 0 or now - st.st_mtime < settings.STABLE_SECONDS:
                        continue  # not a known post's (the usual intake makes it a reference), or still syncing
                    attach(target["id"], [p], "onedrive", extra={"name": p.name[:200]})
                    pending(api, target["id"])
                    lines.append(f"{side}: {p.name} -> reference #{target['id']}")
                elif p.is_dir():
                    images = _images(p)
                    if not images or not _quiet(p, now):
                        continue
                    target = match(p.name, by_code)
                    name = p.name[:200]
                    if not target:
                        target = by_name.get(name)
                    if not target:
                        values = {"brand": "urban_makers", "side": side, "file_name": name,
                                  "title": cloud_sync._title_from(name), "added_by": None}
                        if cloud_sync.check_library(api):
                            values["post_type"] = "carousel"
                        target = api.insert("reference_videos", values)[0]
                        refs.append(target)
                        by_name[name] = target
                    attach(target["id"], images[:MAX_SLIDES], "onedrive", extra={"name": name})
                    shutil.rmtree(p, ignore_errors=True)
                    pending(api, target["id"])
                    lines.append(f"{side}: {name}/ ({len(images)} pictures) -> reference #{target['id']}")
            except (OSError, ValueError) as e:
                log.warning("Reference inbox: %s not taken: %s", p.name, e)
    lines += merge_duplicates(api, refs, by_code)
    _tidy_incoming(now)
    for line in lines:
        log.info("Reference from the inbox: %s", line)
    return lines


def _tidy_incoming(now: float) -> None:
    """Sets uploaded but never finished (the planner closed mid-way): gone after 3 days."""
    try:
        for d in _root().glob("*/incoming-*"):
            if now - d.stat().st_mtime > 3 * 86400:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def merge_duplicates(api, refs: list[dict], by_code: dict[str, dict] | None = None) -> list[str]:
    """A file reference made from an inbox file that is a link reference's post (before files could
    attach): its file, notes and analysis go to the link reference, and it is retired. Once: a
    retired one is never looked at again."""
    by_code = by_code if by_code is not None else codes(refs)
    out = []
    for dup in refs:
        if dup.get("url") or not dup.get("file_name") or dup.get("status") != "active":
            continue
        target = match(dup["file_name"], by_code)
        if not target or target["id"] == dup["id"]:
            continue
        src = settings.REFERENCES / dup["file_name"]
        if not files_for(target["id"]) and (src.is_file() or src.is_dir()):
            files = _images(src) if src.is_dir() else [src]
            try:
                attach(target["id"], files, "merged", move=False, extra={"name": dup["file_name"], "merged_from": dup["id"]})
            except ValueError as e:
                log.warning("Reference #%s's file not merged into #%s: %s", dup["id"], target["id"], e)
        why_dup, why = (dup.get("why") or "").strip(), (target.get("why") or "").strip()
        changes = {}
        if why_dup and why_dup not in why:
            changes["why"] = f"{why}\n\n{why_dup}".strip()
        if changes:
            api.update("reference_videos", {"id": f"eq.{target['id']}"}, changes)
            target.update(changes)
        rows = api.select("reference_analysis", {"reference_id": f"in.({dup['id']},{target['id']})"})
        mine = next((a for a in rows if a["reference_id"] == target["id"]), None)
        theirs = next((a for a in rows if a["reference_id"] == dup["id"]), None)
        if mine and mine.get("state") != "done":
            if theirs and theirs.get("state") == "done":
                keep = {k: theirs.get(k) for k in ("measured", "look", "stills", "analyser", "analysed_at")}
                keep.update(state="done", error=None)
                import cloud_sync
                if cloud_sync.check_library(api):
                    keep.update(problem=None, kind=theirs.get("kind") or "video",
                                engagement={**(theirs.get("engagement") or {}), "source": "upload"})
                api.update("reference_analysis", {"reference_id": f"eq.{target['id']}"}, keep)
            elif files_for(target["id"]):
                pending(api, target["id"])
        api.update("reference_videos", {"id": f"eq.{dup['id']}"}, {"status": "retired"})
        dup["status"] = "retired"
        out.append(f"#{dup['id']} ({dup['file_name']}) merged into #{target['id']} and retired")
    return out


# ── Uploads (uploads.py hands these over) ─────────────────────────────────────

def _ref(ref_id: int) -> dict | None:
    import cloud_sync
    rows = cloud_sync.Client().select("reference_videos", {"id": f"eq.{ref_id}", "select": "id,url,status,title"})
    return rows[0] if rows else None


def _set_name(value) -> str:
    s = str(value or "")
    return s if re.fullmatch(r"[A-Za-z0-9_-]{4,40}", s) else ""


def start(user: dict, data: dict):
    """POST /api/uploads with reference_id: one file of a set (the video, or one of the slides)."""
    import uploads
    try:
        ref_id, size, index = int(data.get("reference_id")), int(data.get("size")), int(data.get("index") or 1)
    except (TypeError, ValueError):
        return uploads._error(400, "reference_id and size are needed")
    upload_set = _set_name(data.get("set"))
    if not upload_set:
        return uploads._error(400, "set is needed")
    name = uploads.safe_name(str(data.get("name") or ""))
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS | IMAGE_EXTS:
        return uploads._error(415, "Only the post's video or its pictures (JPEG, PNG, WebP or HEIC)")
    if size <= 0:
        return uploads._error(400, "the file is empty")
    if size > (MAX_VIDEO if ext in VIDEO_EXTS else MAX_IMAGE):
        return uploads._error(413, "That file is too big for a reference")
    if not 1 <= index <= MAX_SLIDES:
        return uploads._error(400, f"a post has at most {MAX_SLIDES} pictures")
    mine = [s for s in uploads._all() if s["user"] == user["id"] and s["state"] == "uploading"]
    for s in mine:  # resume
        if s.get("reference_id") == ref_id and s.get("set") == upload_set and s.get("index") == index and s["size"] == size:
            return jsonify({"id": s["id"], "offset": uploads._received(s), "chunk": uploads.CHUNK}), 201
    if len(mine) >= uploads.MAX_OPEN:
        return uploads._error(429, "You have too many unfinished uploads: finish or cancel some first")
    if uploads._limited(user["id"], "new"):
        return uploads._error(429, "Too many new uploads at once: try again in a few minutes")
    if shutil.disk_usage(uploads._folder()).free - size < uploads.MIN_FREE:
        return uploads._error(507, "Studio's disk is too full for this file. Tell Bryan.")
    import cloud_sync
    try:
        ref = _ref(ref_id)
    except cloud_sync.CloudError as e:
        log.warning("Reference upload not started: %s", e)
        return uploads._error(502, "Can't reach the planner's database right now; try again")
    if ref is None:
        return uploads._error(404, "That reference isn't in the planner any more")
    import uuid
    upload_id = str(uuid.uuid4())
    uploads._part(upload_id).touch()
    uploads._save({"id": upload_id, "reference_id": ref_id, "set": upload_set, "index": index, "shot_id": None,
                   "user": user["id"], "email": user["email"], "name": name, "size": size,
                   "type": str(data.get("type") or "")[:100], "state": "uploading", "created": catalog.now(),
                   "path": None, "kind": "reference"})
    log.info("Reference upload %s started by %s: %s (%.1f MB) for reference %s", upload_id, user["email"], name,
             size / 1e6, ref_id)
    return jsonify({"id": upload_id, "offset": 0, "chunk": uploads.CHUNK}), 201


def finish(state: dict) -> str | None:
    """The whole file is here: it waits in its set until the planner says the set is complete."""
    import uploads
    incoming = _dir(state["reference_id"]) / f"incoming-{state['set']}"
    try:
        incoming.mkdir(parents=True, exist_ok=True)
        target = incoming / f"{int(state['index']):02d}{Path(state['name']).suffix.lower()}"
        shutil.move(str(uploads._part(state["id"])), str(target))
    except OSError as e:
        log.exception("Reference upload %s couldn't be kept", state["id"])
        return f"Studio couldn't save the file: {e}"
    state.update(state="received", received=catalog.now(), path=str(target))
    uploads._save(state)
    log.info("Reference upload %s received (reference %s, set %s, %s)", state["id"], state["reference_id"],
             state["set"], target.name)
    return None


@bp.post("/api/uploads/references/<int:ref_id>/ready")
def ready(ref_id: int):
    """Every file of a set is here: it becomes the reference's own files, analysed next."""
    import cloud_sync
    import uploads
    from webutil import body
    upload_set = _set_name(body().get("set"))
    incoming = _dir(ref_id) / f"incoming-{upload_set}"
    if not upload_set or not incoming.is_dir():
        return uploads._error(404, "Studio doesn't have those files: upload them again")
    files = sorted((p for p in incoming.iterdir() if p.is_file()), key=lambda p: natural(p.name))
    wrong = check(files) if files else "No files arrived"
    if wrong:
        shutil.rmtree(incoming, ignore_errors=True)
        return uploads._error(415, wrong)
    user = getattr(g, "planner_user", None) or {}
    try:
        meta = attach(ref_id, files, "upload", by=user.get("email") or "")
    except ValueError as e:
        return uploads._error(415, str(e))
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    try:
        pending(cloud_sync.Client(), ref_id)
    except cloud_sync.CloudError as e:
        log.warning("Reference %s's files kept, but it couldn't be queued online: %s", ref_id, e)
        return uploads._error(502, "Studio has the files, but couldn't reach the planner's database: it analyses "
                                   "them once it can")
    return jsonify({"kind": meta["kind"], "files": len(meta["files"]), "at": meta["at"]})


@bp.get("/api/uploads/references/<int:ref_id>")
def status(ref_id: int):
    own = files_for(ref_id)
    if not own:
        return jsonify({})
    return jsonify({"kind": own["kind"], "files": len(own["files"]), "at": own.get("at"), "from": own.get("from")})


def new_set() -> str:
    """A set id (the planner makes its own; tests use this)."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
