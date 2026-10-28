"""
Photo carousels from a folder in OneDrive (the content lead's showcase brief, 2026-10-28: the Finished homes
carousels are photos only, every slide 4:5, a small cover line on slide 1, no text on the others).

A carousel job (cloud_sync, "Make this") may carry, in its payload,

    "photos": {"folder": "<the folder's path in OneDrive>", "files": ["Other 12", "11", "GIF 1"], "cover": "One long room."}

and Studio then sets the idea's slides from those photos, in that order, before it lays the carousel out. The folder
is its full path from OneDrive's top (e.g. "<...>/Showcases/<home>"), and its subfolders are read too. A name is a
picture's name without its extension (case and extra spaces ignored): "11" is a picture at the top of the folder, and
"Other 12" a picture called 12 in a subfolder whose name starts with "Other" ("Other photos/12.jpg"); the picture's
path without its extension ("Other photos/12") works as well. A .jpg, .jpeg, .png, .heic, .heif, .webp or .gif (a
GIF's first frame). The cover line goes on slide 1 only. Nothing is changed unless every photo is found and read: a
missing name, or one that fits more than one picture, stops the job with the names said.
The folder name is never written into the idea or the draft (it may name a place); only the photos are kept.

showcase_batch.py (run on the laptop) queues these jobs, with the posting dates, titles and captions.
"""
import logging
import re
import secrets
import tempfile
from pathlib import Path

import onedrive
import settings
import slide_images

log = logging.getLogger("studio.showcase")

EXTS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif")
COVER_MAX = 120          # characters of the cover line
_BAD_PART = re.compile(r"(^|/)\.\.?(/|$)")


class ShowcaseError(RuntimeError):
    """Plain words for the job's error (the planner shows them)."""


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def check_spec(spec) -> dict:
    """The payload's photos part, checked: {folder, files, cover}. Raises ShowcaseError."""
    if not isinstance(spec, dict):
        raise ShowcaseError("the photos to use weren't given as a list")
    folder = str(spec.get("folder") or "").replace("\\", "/").strip().strip("/")
    files = spec.get("files")
    if not folder or _BAD_PART.search(folder) or len(folder) > 300 or re.search(r"[<>]", folder):
        raise ShowcaseError("the OneDrive folder for the photos isn't a folder path")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
        raise ShowcaseError("no photos were named")
    cap = settings.INSTAGRAM_CAROUSEL_MAX
    if len(files) < 2 or len(files) > cap:
        raise ShowcaseError(f"a carousel takes 2 to {cap} photos ({len(files)} named)")
    names = [_norm(f) for f in files]
    if len(set(names)) != len(names):
        raise ShowcaseError("the same photo is named twice")
    cover = re.sub(r"\s+", " ", str(spec.get("cover") or "")).strip()
    if len(cover) > COVER_MAX:
        raise ShowcaseError(f"the cover line is over {COVER_MAX} characters")
    return {"folder": folder, "files": [f.strip() for f in files], "cover": cover}


def names_of(path: str) -> set[str]:
    """The names a picture answers to, from its path in the folder: "11.jpg" -> {"11"}; "Other photos/12.jpg" ->
    {"other photos/12", "other 12"} (the subfolder's first word and the name)."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    stem = Path(parts[-1]).stem
    if len(parts) == 1:
        return {_norm(stem)}
    first = _norm(parts[-2]).split(" ")[0]
    return {_norm("/".join(parts[:-1]) + "/" + stem), _norm(f"{first} {stem}")}


def match(entries: list[dict], files: list[str]) -> list[dict]:
    """The folder's listing (rclone lsjson -R) matched to the names, in their order. Raises ShowcaseError naming
    every photo that isn't there or matches more than one file."""
    by_stem: dict[str, list[dict]] = {}
    for e in entries:
        path = str(e.get("Path") or e.get("Name") or "")
        if Path(path).suffix.lower() in EXTS and not e.get("IsDir"):
            for key in names_of(path):
                by_stem.setdefault(key, []).append(e)
    missing, doubled, out = [], [], []
    for f in files:
        hits = by_stem.get(_norm(f), [])
        if not hits:
            missing.append(f)
        elif len(hits) > 1:
            doubled.append(f)
        else:
            out.append(hits[0])
    if missing or doubled:
        parts = []
        if missing:
            parts.append(f"not in the folder: {', '.join(missing)}")
        if doubled:
            parts.append(f"more than one file for: {', '.join(doubled)}")
        raise ShowcaseError("Some photos weren't found (" + "; ".join(parts) + "). Nothing was changed.")
    return out


def listing(folder: str) -> list[dict]:
    """Every file in the folder and its subfolders (rclone lsjson -R). Raises ShowcaseError when the folder isn't
    there."""
    try:
        return onedrive._lsjson(folder, recursive=True, timeout=180)
    except onedrive.OneDriveError as e:
        if "not found" in str(e).lower() or "directory not found" in str(e).lower():
            raise ShowcaseError("that OneDrive folder wasn't found (give its full path from OneDrive's top)") from None
        raise


def _key() -> str:
    return "s" + secrets.token_hex(4)


def import_photos(api, spec: dict) -> list[dict]:
    """The slides for the photos named in spec (check_spec's), fetched from OneDrive, kept as Studio's photos and
    their previews uploaded for the planner. Raises ShowcaseError (or onedrive.OneDriveError) before anything is
    kept on the idea."""
    spec = check_spec(spec)
    if onedrive.rclone_path() is None:
        raise ShowcaseError("OneDrive isn't connected on the Studio machine")
    found = match(listing(spec["folder"]), spec["files"])
    slides = []
    with tempfile.TemporaryDirectory(prefix="showcase-", dir=settings.ROOT) as tmp:
        for n, e in enumerate(found, 1):
            rel = str(e.get("Path") or e["Name"])
            local = Path(tmp) / f"{n:02d}{Path(rel).suffix.lower()}"
            onedrive._run(["copyto", onedrive._remote(spec["folder"], rel), str(local)], timeout=300)
            try:
                photo = slide_images.save_photo(local)
            except ValueError as err:
                raise ShowcaseError(f"Studio couldn't read the photo {spec['files'][n - 1]}: {err}") from None
            thumb = f"photos/{photo['id']}.jpg"
            try:
                api.upload(thumb, slide_images.preview(photo["path"]))
            except Exception as err:  # the slide still works; the planner shows no preview for it
                log.warning("Photo %s kept, but its preview didn't upload: %s", photo["id"], err)
                thumb = None
            slides.append({"key": _key(), "role": "cover" if n == 1 else "point",
                           "heading": spec["cover"] if n == 1 else "", "body": "",
                           "image": {"source": "photo", "photo_id": photo["id"], **({"thumb_path": thumb} if thumb else {})}})
    log.info("Showcase: %d photos kept for a carousel", len(slides))
    return slides
