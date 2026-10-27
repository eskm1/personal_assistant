"""
Pictures for carousel slides (cloud/CONTRACT.md, "Carousels"). Each slide's image says
where its picture comes from (image["source"]):

  text     no picture: the slide is laid out as text on the Urban Makers green
  clip     a still from one of the library's clips (clip_id, at seconds): Studio grabs the
           sharpest frame within half a second of that moment from the original video
  photo    a photo uploaded from the planner (photo_id): kept here as a JPEG (an iPhone's
           HEIC is converted when it arrives, uploads.py)
  concept  an AI concept picture Gemini drew for this slide (concepts.py): drawn, checked and
           cached when the carousel is made, two options Bryan picks between; always shown
           with the "Concept" mark, never in a Finished homes post

Each source is a small class (Source) with available() and picture(); carousel.py asks for
a slide's picture through picture() and never needs to know where it came from. Only Urban
Makers' own footage and photos are ever used (and concept pictures drawn for the slide),
never another company's images.
"""
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import settings

log = logging.getLogger("studio.slides")

PHOTOS = settings.ROOT / "Photos"       # uploaded photos, <photo id>.jpg
STILLS = settings.ROOT / "Stills"       # stills grabbed from clips, cached by clip and moment
PHOTO_MAX = 2700                        # longest side kept of an uploaded photo (px): 2160x2700 (4:5) stays as it is
PREVIEW_WIDTH = 540                     # the planner's preview of a picture
SHARP_WINDOW = 0.5                      # look this many seconds either side of the moment
SHARP_FRAMES = 7                        # frames compared for sharpness
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
PHOTO_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
_ID = re.compile(r"^[0-9a-f]{32}$")


class Unavailable(RuntimeError):
    """This picture can't be had (missing clip or photo, or a source that isn't ready)."""


# ── Stills from footage ───────────────────────────────────────────────────────

def _grab(video: Path, t: float, out: Path) -> bool:
    """One full-size frame at t seconds (the phone's rotation applied)."""
    result = subprocess.run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(video),
                             "-frames:v", "1", "-q:v", "2", str(out)],
                            capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    return result.returncode == 0 and out.exists() and out.stat().st_size > 0


def sharpness(jpg: Path) -> float:
    """How sharp a frame is (the variance of its edges); higher is sharper."""
    import cv2
    img = cv2.imread(str(jpg), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return -1.0
    h, w = img.shape
    if w > 720:
        img = cv2.resize(img, (720, max(1, round(h * 720 / w))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def sharp_still(video: Path, at: float, out: Path, duration: float | None = None) -> float:
    """The sharpest of a few frames around `at` (a still in the middle of a pan or a
    shake is blurred), saved as out. Returns the moment it was taken (seconds)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    end = max(duration - 0.05, 0) if duration else None
    times = sorted({round(min(max(at + SHARP_WINDOW * (2 * k / (SHARP_FRAMES - 1) - 1), 0), end if end is not None else 1e9), 2)
                    for k in range(SHARP_FRAMES)})
    with tempfile.TemporaryDirectory(prefix="studio-still-") as tmp:
        found = []
        for n, t in enumerate(times):
            jpg = Path(tmp) / f"{n}.jpg"
            if _grab(video, t, jpg):
                found.append((sharpness(jpg), t, jpg))
        if not found:
            raise Unavailable("couldn't read a frame from the clip")
        # The sharpest; of frames about as sharp (within 3%), the one nearest the moment asked for.
        top = max(f[0] for f in found)
        _, taken, jpg = min((f for f in found if f[0] >= top * 0.97), key=lambda f: abs(f[1] - at))
        shutil.copyfile(jpg, out)
    return taken


def preview(jpg: Path, width: int = PREVIEW_WIDTH) -> bytes:
    """A small JPEG of a picture, for the planner."""
    from PIL import Image
    import io
    with Image.open(jpg) as img:
        img = img.convert("RGB")
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue()


def _clip(conn, clip_id) -> dict:
    row = conn.execute("SELECT id, title, video_path, clip_dir, duration FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if row is None:
        raise Unavailable(f"clip {clip_id} isn't in the library")
    return dict(row)


def still_path(clip_id: int, at: float) -> Path:
    return STILLS / f"clip_{int(clip_id)}_{int(round(max(at, 0) * 1000))}.jpg"


def clip_still(conn, clip_id: int, at: float) -> tuple[Path, float]:
    """A sharp full-size still from the clip near `at` (cached). Falls back to the
    library's own frame nearest that moment when the original video is gone."""
    clip = _clip(conn, clip_id)
    out = still_path(clip_id, at)
    if out.exists():
        return out, at
    video = Path(clip["video_path"] or "")
    if video.is_file():
        taken = sharp_still(video, at, out, clip.get("duration"))
        return out, taken
    import cloud_sync
    frame = cloud_sync._frame_near(clip["clip_dir"], at)
    if frame is None:
        raise Unavailable(f"clip {clip_id}'s video and frames are gone")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(frame, out)
    return out, at


# ── Uploaded photos ───────────────────────────────────────────────────────────

def is_photo(name: str, kind: str = "") -> bool:
    return Path(name).suffix.lower() in PHOTO_EXTS and (not kind or kind in PHOTO_TYPES
                                                        or kind == "application/octet-stream")


def save_photo(src: Path) -> dict:
    """Keep an uploaded photo as a JPEG (HEIC/HEIF and PNG converted, turned upright,
    at most PHOTO_MAX px). Returns {id, path, width, height}. Raises ValueError when it
    isn't a picture Studio can read."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:  # HEIC needs pillow-heif (requirements.txt); JPEG and PNG work without it
        pass
    photo_id = uuid.uuid4().hex
    PHOTOS.mkdir(parents=True, exist_ok=True)
    out = PHOTOS / f"{photo_id}.jpg"
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.getchannel("A"))
                img = flat
            else:
                img = img.convert("RGB")
            img.thumbnail((PHOTO_MAX, PHOTO_MAX), Image.LANCZOS)
            img.save(out, "JPEG", quality=90, optimize=True)
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError) as e:
        out.unlink(missing_ok=True)
        raise ValueError(f"Studio couldn't read that photo ({type(e).__name__})") from None
    return {"id": photo_id, "path": out, "width": width, "height": height}


def photo_path(photo_id: str) -> Path:
    if not _ID.match(str(photo_id or "")):
        raise Unavailable("that photo id isn't one of Studio's")
    return PHOTOS / f"{photo_id}.jpg"


# ── The sources ───────────────────────────────────────────────────────────────

class Source:
    """Where a slide's picture comes from. picture() returns a full-size image on disk;
    mark is a label the layout must show on the slide (a concept image says "Concept")."""
    key = ""
    label = ""
    mark = ""

    def available(self) -> str | None:
        """None when pictures can be had from here, else why not (plain words)."""
        return None

    def picture(self, image: dict, conn) -> Path:
        raise NotImplementedError


class ClipStill(Source):
    key, label = "clip", "Still from your footage"

    def picture(self, image: dict, conn) -> Path:
        if not isinstance(image.get("clip_id"), int):
            raise Unavailable("no clip picked")
        if not still_path(image["clip_id"], float(image.get("at") or 0)).exists():
            import onedrive
            onedrive.ensure_originals(conn, [image["clip_id"]])  # a sharp still needs the original: freed ones come back
        return clip_still(conn, image["clip_id"], float(image.get("at") or 0))[0]


class UploadedPhoto(Source):
    key, label = "photo", "Photo"

    def picture(self, image: dict, conn) -> Path:
        path = photo_path(image.get("photo_id"))
        if not path.is_file():
            raise Unavailable("the photo isn't on the Studio machine any more")
        return path


class Concept(Source):
    """AI concept pictures (Gemini, GEMINI_API_KEY; concepts.py). Always marked "Concept",
    never presented as Urban Makers' built work, never in a Finished homes post. The
    pictures are drawn and checked by concepts.prepare() when the carousel is made (that's
    where the spending is); here they're only ever read from its cache, so a layout never
    pays. Pictures already drawn still lay out if the key is taken away."""
    key, label, mark = "concept", "AI concept image", "Concept"

    def picture(self, image: dict, conn) -> Path:
        import concepts
        path = concepts.chosen_path(image)
        if path is None:
            raise Unavailable(image.get("why") or concepts.available() or "the concept picture isn't drawn yet")
        return path


SOURCES: dict[str, Source] = {s.key: s for s in (ClipStill(), UploadedPhoto(), Concept())}


def picture(image: dict | None, conn) -> tuple[Path | None, str, str | None]:
    """(the picture on disk or None for a text slide, the mark to show, why there's no
    picture when one was asked for). Never raises."""
    source = SOURCES.get((image or {}).get("source") or "text")
    if source is None:
        return None, "", None
    why = source.available()
    if why:
        return None, "", why
    try:
        return source.picture(image, conn), source.mark, None
    except Unavailable as e:
        return None, "", str(e)
    except Exception as e:  # a broken file mustn't stop the carousel: that slide goes text-only
        log.warning("No picture for a slide (%s): %s: %s", source.key, type(e).__name__, e)
        return None, "", f"{type(e).__name__}"
