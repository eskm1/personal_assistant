"""
Framing for the editor: where each piece of footage is cropped for the 9:16 Reel.

  - Reframing: horizontal footage (Z fc, GoPro) is cropped around the person in it
    instead of down the middle. OpenCV's face finder (bundled with it, nothing is
    downloaded) looks at a few small frames a second; the crop follows the face's
    usual position, holding still within a shot. If the person walks a long way
    across a long shot, the shot is split into a few pieces with their own crop,
    never a moving pan. No face (B-roll of a wall, say) = the centre, as before.
  - Punch-ins: on a long talking shot, every 4 to 7 seconds the picture cuts
    between the normal framing and a slightly closer one (112%, centred on the
    face), at a natural break (a jump cut or the end of a sentence), so the shot
    never sits still. Never on B-roll, and the first shot starts at normal framing.

Each cut in the edit plan records its framing, so a re-render or the DaVinci
Resolve project frames it the same way:
    crop_x, crop_y  where the picture is centred, as a fraction of the source's
                    width and height (0.5 = the middle)
    zoom            1.0 = the full-height 9:16 crop, 1.12 = punched in
What the face finder saw is kept in the clip's folder (faces.json), so it only
looks at each stretch of footage once.
"""
import json
import logging
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import extract
import settings

log = logging.getLogger("studio.framing")

SAMPLE_FPS = 4          # frames a second the face finder looks at
ANALYSE_SIZE = 640      # the long side of those frames, in pixels
TARGET = 9 / 16         # the Reel's shape (width / height)
PUNCH_GAP = (4.0, 7.0)  # seconds between punch-in changes (so never two within 3 s)
PUNCH_TARGET = 5.5      # aim for about this
MIN_HOLD = 1.5          # a framing lasts at least this long before the shot ends
HOOK_CLEAR = 1.5        # no punch-in while the hook text is up
SENTENCE_END = (".", "!", "?", "。", "！", "？")
CACHE_VERSION = 2


# ── The face finder ───────────────────────────────────────────────────────────

_detectors = None


def _cascades():
    """OpenCV's bundled face finders: straight on, then side on (both ways)."""
    global _detectors
    if _detectors is None:
        import cv2
        base = Path(cv2.data.haarcascades)
        _detectors = (cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml")),
                      cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml")))
        if _detectors[0].empty():
            _detectors = None
            raise RuntimeError("OpenCV's face finder files are missing")
    return _detectors


def find_faces(gray) -> list[tuple[float, float, float]]:
    """Everything that looks like a face in a small greyscale frame: (x, y, size)
    as fractions of the frame's width, height and width. Straight on first; if
    nothing, the same with the contrast evened out (dim rooms), then side on."""
    import cv2
    frontal, profile = _cascades()
    h, w = gray.shape[:2]
    size = max(20, int(min(w, h) * 0.06))
    look = lambda finder, img: list(finder.detectMultiScale(img, scaleFactor=1.15, minNeighbors=5,  # noqa: E731
                                                             minSize=(size, size)))
    faces = look(frontal, gray) or look(frontal, cv2.equalizeHist(gray))
    if not faces:
        faces = look(profile, gray) + [(w - x - fw, y, fw, fh) for x, y, fw, fh in look(profile, cv2.flip(gray, 1))]
    return [((x + fw / 2) / w, (y + fh / 2) / h, fw / w) for x, y, fw, fh in faces]


def find_face(gray) -> tuple[float, float, float] | None:
    """The biggest face in one frame, or None."""
    faces = find_faces(gray)
    return max(faces, key=lambda f: f[2]) if faces else None


def _consistent(candidates: dict[float, list]) -> dict[float, tuple | None]:
    """One face per moment: the candidate that turns up in the same place in the
    most other moments (a person stays put; a pattern that fools the finder for
    one frame doesn't). Ties go to the bigger face. None where nothing is backed
    up by at least one other moment."""
    flat = [(t, f) for t, fs in candidates.items() for f in fs]
    out = {}
    for t, fs in candidates.items():
        best, best_key = None, (0, 0.0)
        for f in fs:
            support = len({u for u, g in flat if abs(g[0] - f[0]) <= 0.06 and abs(g[1] - f[1]) <= 0.08})
            if support >= 2 and (support, f[2]) > best_key:
                best, best_key = tuple(f), (support, f[2])
        out[t] = best
    return out


def _dims(clip: dict) -> tuple[int, int]:
    """The clip's picture size as shown (portrait phone video already turned upright)."""
    w, h = int(clip.get("width") or 0), int(clip.get("height") or 0)
    if not (w and h):
        meta = extract.probe(Path(clip["video_path"]))
        w, h = meta["width"], meta["height"]
    return w, h


def _frames(clip: dict, start: float, seconds: float):
    """Small greyscale frames, SAMPLE_FPS a second from start, as one numpy array."""
    import numpy as np
    sw, sh = _dims(clip)
    scale = ANALYSE_SIZE / max(sw, sh)
    aw, ah = max(2, round(sw * scale / 2) * 2), max(2, round(sh * scale / 2) * 2)
    cmd = [extract.resolve(settings.FFMPEG), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
           "-i", clip["video_path"], "-an", "-vf", f"fps={SAMPLE_FPS},scale={aw}:{ah},format=gray",
           "-f", "rawvideo", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=getattr(extract, "_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + result.stderr.decode("utf-8", "replace").strip()[-200:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    n = len(data) // (aw * ah)
    return data[:n * aw * ah].reshape(n, ah, aw)


def _cache_path(clip: dict) -> Path:
    return Path(clip["clip_dir"]) / "faces.json"


def _cache_key(clip: dict) -> str:
    path = Path(clip["video_path"])
    size = path.stat().st_size if path.exists() else 0
    return f"{clip['id']}:{path.name}:{size}"


def faces(clip: dict, start: float, end: float, every: int = 1) -> dict[float, tuple | None]:
    """Where the face is at each sampled moment between start and end (clip
    seconds): {time: (x, y, size) or None}; the most consistent face when there
    are several. every=2 looks half as often (B-roll). Looks only at moments it
    hasn't looked at before; the rest come from the clip's faces.json."""
    path = _cache_path(clip)
    key = _cache_key(clip)
    cache = {}
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if cache.get("version") != CACHE_VERSION or cache.get("key") != key:
        cache = {"version": CACHE_VERSION, "key": key, "samples": {}}
    samples = cache["samples"]
    first, last = int(start * SAMPLE_FPS + 0.999), int(end * SAMPLE_FPS)
    wanted = [k for k in range(max(0, first), last + 1) if k % every == 0]
    missing = [k for k in wanted if str(k) not in samples]
    # Look at each run of missing moments in one go.
    runs, run = [], []
    for k in missing:
        if run and k != run[-1] + every:
            runs.append(run)
            run = []
        run.append(k)
    if run:
        runs.append(run)
    for run in runs:
        frames = _frames(clip, run[0] / SAMPLE_FPS, (run[-1] - run[0] + 1) / SAMPLE_FPS + 0.01)
        for k in run:  # nothing decoded = past the end of the file
            i = k - run[0]
            samples[str(k)] = [[round(v, 4) for v in f] for f in find_faces(frames[i])] if i < len(frames) else []
    if runs:
        try:
            path.write_text(json.dumps(cache), encoding="utf-8")
        except OSError as e:
            log.warning("Couldn't keep the face positions for clip %s: %s", clip["id"], e)
    return _consistent({k / SAMPLE_FPS: samples[str(k)] for k in wanted})


# ── Geometry ──────────────────────────────────────────────────────────────────

def base_size(sw: int, sh: int) -> tuple[float, float]:
    """The biggest 9:16 window that fits the source, in source pixels."""
    return (sh * TARGET, sh) if sw / sh > TARGET else (sw, sw / TARGET)


def exact_window(cut: dict, sw: int, sh: int) -> tuple[float, float, float, float]:
    """The part of the source picture a cut shows: (width, height, x, y) in source
    pixels, from its crop_x, crop_y and zoom (the centre at 100% when not set)."""
    bw, bh = base_size(sw, sh)
    zoom = max(1.0, float(cut.get("zoom") or 1.0))
    w, h = bw / zoom, bh / zoom
    x = min(max(float(cut.get("crop_x", 0.5)) * sw - w / 2, 0), sw - w)
    y = min(max(float(cut.get("crop_y", 0.5)) * sh - h / 2, 0), sh - h)
    return w, h, x, y


def window(cut: dict, sw: int, sh: int) -> tuple[int, int, int, int]:
    """exact_window in whole, even pixels, for ffmpeg's crop."""
    w, h, x, y = exact_window(cut, sw, sh)
    w, h = max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
    return w, h, min(int(round(x)), sw - w), min(int(round(y)), sh - h)


def _clamp_centre(cx: float, cy: float, w: float, h: float, sw: int, sh: int) -> tuple[float, float]:
    """A window centre (fractions) moved just enough to keep the window inside the picture."""
    x = min(max(cx * sw, w / 2), sw - w / 2)
    y = min(max(cy * sh, h / 2), sh - h / 2)
    return round(x / sw, 4), round(y / sh, 4)


def _smooth(values: list[float], size: int = 5) -> list[float]:
    """A running median: one stray detection doesn't move the crop."""
    half = size // 2
    return [statistics.median(values[max(0, i - half):i + half + 1]) for i in range(len(values))]


# ── Reframing ─────────────────────────────────────────────────────────────────

def _word_gaps(words: list[dict], start: float, end: float) -> list[float]:
    """The middle of each gap between words inside start..end (safe places to split)."""
    inside = [w for w in words if w["start"] >= start - 0.01 and w["end"] <= end + 0.01]
    return [(a["end"] + b["start"]) / 2 for a, b in zip(inside, inside[1:]) if b["start"] >= a["end"]]


def reframe(cut: dict, clip: dict, words: list[dict], seen: dict[float, tuple | None]) -> list[dict]:
    """Choose the crop for one cut of horizontal footage: still within the cut,
    centred on the face's usual place. A long cut where the person moves a lot is
    split into up to four pieces, each with its own still crop. Returns the
    pieces; each also carries "face" (x, y) for the punch-ins when one was seen."""
    sw, sh = _dims(clip)
    bw, _ = base_size(sw, sh)
    wf = bw / sw  # the window's width as a fraction of the source's
    times = sorted(t for t in seen if cut["start"] <= t <= cut["end"])
    found = [(t, seen[t]) for t in times if seen[t]]
    if len(found) < 2 or len(found) < 0.3 * len(times):
        return [{**cut, "crop_x": 0.5, "crop_y": 0.5}]
    xs = _smooth([f[0] for _, f in found])
    ys = _smooth([f[1] for _, f in found])
    track = list(zip([t for t, _ in found], xs, ys))

    def piece(part, start, end):
        px = [x for _, x, _ in part]
        cx = (min(px) + max(px)) / 2 if max(px) - min(px) <= wf * 0.5 else statistics.median(px)
        fy = statistics.median(y for _, _, y in part)
        cx, _ = _clamp_centre(cx, 0.5, bw, sh, sw, sh)
        return {**cut, "start": round(start, 3), "end": round(end, 3), "crop_x": cx, "crop_y": 0.5,
                "face": [round(statistics.median(px), 4), round(fy, 4)]}

    spread = max(xs) - min(xs)
    if spread <= wf * 0.5 or cut["end"] - cut["start"] < 3.0:
        return [piece(track, cut["start"], cut["end"])]
    # The person moves a lot: split where they've moved further than half the window.
    groups, current = [], [track[0]]
    for point in track[1:]:
        trial = [x for _, x, _ in current] + [point[1]]
        if max(trial) - min(trial) > wf * 0.5 and current[-1][0] - current[0][0] >= 2.0:
            groups.append(current)
            current = []
        current.append(point)
    groups.append(current)
    merged = [groups[0]]
    for g in groups[1:]:  # neighbours that ended up in about the same place are one piece
        if abs(statistics.median(x for _, x, _ in merged[-1]) - statistics.median(x for _, x, _ in g)) <= wf * 0.25:
            merged[-1] = merged[-1] + g
        else:
            merged.append(g)
    groups = merged
    while len(groups) > 4:  # merge the two closest neighbours
        i = min(range(len(groups) - 1), key=lambda j: abs(statistics.median(x for _, x, _ in groups[j]) -
                                                        statistics.median(x for _, x, _ in groups[j + 1])))
        groups[i:i + 2] = [groups[i] + groups[i + 1]]
    gaps = _word_gaps(words, cut["start"], cut["end"])
    edges = [cut["start"]]
    for a, b in zip(groups, groups[1:]):
        at = (a[-1][0] + b[0][0]) / 2
        near = [g for g in gaps if abs(g - at) <= 0.6]  # split between words when there's a gap close by
        at = min(near, key=lambda g: abs(g - at)) if near else at
        if at - edges[-1] >= 1.5 and cut["end"] - at >= 1.5:
            edges.append(at)
    edges.append(cut["end"])
    out = []
    for s, e in zip(edges, edges[1:]):
        part = [p for p in track if s <= p[0] < e] or [min(track, key=lambda p: abs(p[0] - s))]
        out.append(piece(part, s, e))
    return out


def _steady(cuts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """One crop for back-to-back pieces of the same shot whose crops are close, so
    the picture doesn't nudge sideways at every jump cut."""
    out, group = [], []

    def flush():
        if group:
            xs = [c["crop_x"] for c in group]
            mid = round((min(xs) + max(xs)) / 2, 4)
            out.extend({**c, "crop_x": mid} for c in group)
            group.clear()
    for c in cuts:
        if not c.get("face"):
            flush()
            out.append(c)
            continue
        if group and group[-1]["clip_id"] == c["clip_id"]:
            sw, sh = _dims(clips[c["clip_id"]])
            xs = [g["crop_x"] for g in group] + [c["crop_x"]]
            if max(xs) - min(xs) > 0.25 * base_size(sw, sh)[0] / sw:
                flush()
        else:
            flush()
        group.append(c)
    flush()
    return out


# ── Punch-ins ─────────────────────────────────────────────────────────────────

def _zoomed(cut: dict, clip: dict, zoom: float) -> dict:
    """The cut punched in by zoom, centred on the face (else on the picture)."""
    sw, sh = _dims(clip)
    bw, bh = base_size(sw, sh)
    cx, cy = float(cut.get("crop_x", 0.5)), float(cut.get("crop_y", 0.5))
    fx, fy = cut.get("face") or (cx, cy)
    # Zoom about the face: it stays where it was on screen, everything else grows around it.
    x0, y0 = cx * sw - bw / 2, cy * sh - bh / 2
    ax, ay = min(max(fx * sw, x0), x0 + bw), min(max(fy * sh, y0), y0 + bh)
    w, h = bw / zoom, bh / zoom
    nx, ny = ax - (ax - x0) / zoom, ay - (ay - y0) / zoom
    px, py = _clamp_centre((nx + w / 2) / sw, (ny + h / 2) / sh, w, h, sw, sh)
    return {**cut, "crop_x": px, "crop_y": py, "zoom": zoom}


def punch_ins(cuts: list[dict], clips: dict[int, dict], speech: list[bool], hook: bool,
              zoom: float = 1.12) -> list[dict]:
    """Alternate normal and punched-in framing on talking shots, every 4-7 s, at
    natural breaks: a jump cut, else the end of a sentence, else a gap between
    words. A run of talking from one clip starts at normal framing; B-roll and a
    change of clip start afresh. Returns the cuts (some split) with zoom set."""
    import editor
    starts, offset = [], 0.0
    for c in cuts:
        starts.append(offset)
        offset += c["end"] - c["start"]
    # Runs of talking pieces from the same clip, back to back.
    runs, current = [], []
    for i, c in enumerate(cuts):
        if speech[i] and current and cuts[current[-1]]["clip_id"] == c["clip_id"]:
            current.append(i)
        else:
            if current:
                runs.append(current)
            current = [i] if speech[i] else []
    if current:
        runs.append(current)

    changes: dict[int, list[float]] = {}  # piece -> draft times where the zoom flips inside or at its start
    for run in runs:
        run_start = starts[run[0]]
        run_end = starts[run[-1]] + cuts[run[-1]]["end"] - cuts[run[-1]]["start"]
        strong, weak = [], []
        for i in run:
            if i != run[0]:
                strong.append((starts[i], i))
            c = cuts[i]
            words = [w for w in editor._words(clips[c["clip_id"]])
                     if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01]
            for a, b in zip(words, words[1:]):
                middle = (a["end"] + b["start"]) / 2
                if b["start"] < a["end"] or middle - c["start"] < 0.3 or c["end"] - middle < 0.3:
                    continue  # no gap, or too near the piece's ends to split it there
                at = starts[i] + middle - c["start"]
                (strong if a["word"].strip().endswith(SENTENCE_END) else weak).append((at, i))
        last = run_start
        while True:
            lo, hi = max(last + PUNCH_GAP[0], HOOK_CLEAR if hook else 0.0), last + PUNCH_GAP[1]
            usable = lambda pts, a, b: [p for p in pts if a <= p[0] <= b and p[0] <= run_end - MIN_HOLD]  # noqa: E731
            pick = (usable(strong, lo, hi) or usable(weak, lo, hi))
            if pick:
                at, i = min(pick, key=lambda p: abs(p[0] - (last + PUNCH_TARGET)))
            else:
                later = usable(strong, hi, run_end)
                if not later:
                    break
                at, i = later[0]
            changes.setdefault(i, []).append(at)
            last = at

    out = []
    in_run = {i: n for n, r in enumerate(runs) for i in r}
    state = {}  # run -> its zoom so far
    for i, c in enumerate(cuts):
        if i not in in_run:
            out.append({**c, "zoom": 1.0})
            continue
        run = in_run[i]
        z = state.get(run, 1.0)
        pieces, edge = [], c["start"]
        for at in sorted(changes.get(i, [])):
            src = round(c["start"] + at - starts[i], 3)
            if src - edge > 0.001:  # a flip inside this piece: split it there
                pieces.append(({**c, "start": round(edge, 3), "end": src}, z))
                edge = src
            z = zoom if z == 1.0 else 1.0
        pieces.append(({**c, "start": round(edge, 3), "end": c["end"]}, z))
        state[run] = z
        for p, pz in pieces:
            out.append(_zoomed(p, clips[c["clip_id"]], pz) if pz > 1.0 else {**p, "zoom": 1.0})
    return out


# ── Everything, for one edit ──────────────────────────────────────────────────

def frame_cuts(cuts: list[dict], clips: dict[int, dict], hook: bool, reframe_on: bool = True,
               punch_on: bool = True, zoom: float = 1.12) -> tuple[list[dict], dict]:
    """Framing for a whole cut list: reframes horizontal footage, then adds punch-ins
    on talking shots. Returns the new cut list and a short tally for the edit plan."""
    import editor
    stats = {"reframed": 0, "punch_ins": 0, "faces_found": 0, "analysed": 0}
    wanted: dict[int, list[dict]] = {}  # clip -> the cuts whose faces are needed
    for c in cuts:
        clip = clips[c["clip_id"]]
        talks = any(w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01 for w in editor._words(clip))
        try:
            horizontal = _dims(clip)[0] > _dims(clip)[1]
        except Exception:
            continue
        if (reframe_on and horizontal) or (punch_on and talks):
            wanted.setdefault(c["clip_id"], []).append(c)

    def look(cid):
        """One clip per thread, so its faces.json has one writer. Pieces close together
        (a stretch with its pauses cut) are looked at in one go: one read of the file."""
        words = editor._words(clips[cid])
        spans = []
        for c in sorted(wanted[cid], key=lambda c: c["start"]):
            talks = any(w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01 for w in words)
            if spans and c["start"] - spans[-1][1] <= 2.0:
                spans[-1][1] = max(spans[-1][1], c["end"])
                spans[-1][2] = spans[-1][2] or talks
            else:
                spans.append([c["start"], c["end"], talks])
        seen = {}
        for start, end, talks in spans:
            try:
                seen.update(faces(clips[cid], start, end, every=1 if talks else 2))
            except Exception as e:  # no OpenCV, unreadable frames: frame it the old way
                log.warning("Face finder failed on clip %s: %s", cid, e)
        return cid, {(c["start"], c["end"]): {t: f for t, f in seen.items() if c["start"] <= t <= c["end"]}
                     for c in wanted[cid]}
    with ThreadPoolExecutor(max_workers=3) as pool:  # ffmpeg and OpenCV work outside Python's lock
        seen_by_cut = {(cid, k): v for cid, found in pool.map(look, list(wanted)) for k, v in found.items()}

    framed, speech = [], []
    for c in cuts:
        clip = clips[c["clip_id"]]
        words = [w for w in editor._words(clip) if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01]
        try:
            sw, sh = _dims(clip)
        except Exception as e:
            log.warning("No picture size for clip %s (%s); framing it in the middle", clip["id"], e)
            framed.append({**c, "crop_x": 0.5, "crop_y": 0.5})
            speech.append(bool(words))
            continue
        horizontal = sw > sh
        seen = seen_by_cut.get((c["clip_id"], (c["start"], c["end"])), {})
        stats["analysed"] += bool(seen)
        if reframe_on and horizontal and seen:
            pieces = reframe(c, clip, editor._words(clip), seen)
            stats["reframed"] += sum(1 for p in pieces if p.get("face"))
        else:
            piece = {**c, "crop_x": 0.5, "crop_y": 0.5}
            hits = [f for t, f in seen.items() if f and c["start"] <= t <= c["end"]]
            if len(hits) >= 2 and len(hits) >= 0.3 * len(seen):
                piece["face"] = [round(statistics.median(f[0] for f in hits), 4),
                                 round(statistics.median(f[1] for f in hits), 4)]
            pieces = [piece]
        stats["faces_found"] += sum(1 for p in pieces if p.get("face"))
        framed += pieces
        speech += [bool([w for w in words if w["start"] >= p["start"] - 0.01 and w["end"] <= p["end"] + 0.01])
                   for p in pieces]
    framed = _steady(framed, clips)
    if punch_on:
        framed = punch_ins(framed, clips, speech, hook, zoom)
        stats["punch_ins"] = sum(1 for a, b in zip([None] + framed, framed)
                                 if (b.get("zoom") or 1.0) > 1.0 and (a is None or (a.get("zoom") or 1.0) == 1.0))
    out = []
    for c in framed:
        c = {k: v for k, v in c.items() if k != "face"}
        c.setdefault("zoom", 1.0)
        out.append(c)
    return out, stats
