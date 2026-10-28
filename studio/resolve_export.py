"""
"Open in DaVinci Resolve": turns a finished draft into a project Bryan can finish
by hand in the free DaVinci Resolve, with every layer still editable.

    Studio/Renders/production_<id>_v<n>_resolve/
        timeline.fcpxml   the cut, pointing at the ORIGINAL clips (full quality,
                          every cut can be trimmed back out), plus the layers below
        captions.mov      the styled captions on a transparent layer (ProRes 4444)
        animations.mov    the drawn animations (motion library inserts), AI pictures (pictures.py) and the words behind Bryan
                          (behind.py: his shape cut out of them, so they match this cut), same, on their own track
        window.mov        a voice-over's window (Bryan over the site footage), same; the site footage itself is
                          on V2 as clips from the originals
        graphics.mov      the hook and B-roll cards, same
        captions.srt      the caption text, to import as an editable subtitle track
        music.mp3         the Epidemic Sound track (Urban Makers only)
        HOW TO OPEN.txt   the import steps

FCPXML 1.8 is used because every Resolve since 16 imports it (File > Import >
Timeline). Times are exact fractions of a second, and clip in-points are given in
each file's own timecode, which is what Resolve matches media by: an iPhone clip's
timecode is the time of day it was shot, not zero.
"""
import json
import logging
import re
import shutil
import tempfile
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr

import editor
import extract
import framing
import settings
from settings import ROOT

log = logging.getLogger("studio.resolve")

FPS = editor.FPS
FRAME = Fraction(1, FPS)


def folder_for(production: dict) -> Path:
    return editor.RENDERS / f"production_{production['id']}_v{production['version']}_resolve"


def existing(production: dict) -> Path | None:
    folder = folder_for(production)
    return folder if (folder / "timeline.fcpxml").exists() else None


# ── Source media ──────────────────────────────────────────────────────────────

def _rate(text: str | None) -> Fraction | None:
    try:
        rate = Fraction(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return rate if 1 <= rate <= 240 else None


def _timecode_seconds(tc: str, rate: Fraction) -> Fraction:
    """Start timecode ("14:23:10:05", or "14:23:10;05" for drop-frame) as seconds."""
    m = re.fullmatch(r"(\d+):(\d+):(\d+)([:;.,])(\d+)", tc.strip())
    if not m:
        return Fraction(0)
    h, mi, s, sep, f = int(m[1]), int(m[2]), int(m[3]), m[4], int(m[5])
    base = round(rate)
    frames = ((h * 60 + mi) * 60 + s) * base + f
    if sep in ";," and base in (30, 60):  # drop-frame skips labels, not frames
        drop = base // 15
        minutes = h * 60 + mi
        frames -= drop * (minutes - minutes // 10)
    return frames / rate


def media_info(path: Path) -> dict:
    """What FCPXML needs to describe a source file."""
    raw = json.loads(extract._run([settings.FFPROBE, "-v", "error", "-print_format", "json",
                                   "-show_format", "-show_streams", str(path)]).stdout)
    streams, fmt = raw.get("streams", []), raw.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info = {"duration": Fraction(str(fmt.get("duration") or "0")).limit_denominator(1_000_000),
            "has_video": video is not None, "has_audio": audio is not None,
            "audio_rate": int(audio.get("sample_rate", 48000)) if audio else 48000,
            "audio_channels": int(audio.get("channels", 2)) if audio else 2,
            "start": Fraction(0)}
    if video:
        rate = _rate(video.get("r_frame_rate")) or _rate(video.get("avg_frame_rate")) or Fraction(FPS)
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        rotation = int(video.get("tags", {}).get("rotate", 0))
        for side in video.get("side_data_list", []):
            rotation = int(side.get("rotation", rotation))
        if abs(rotation) % 180 == 90:
            width, height = height, width
        timecode = fmt.get("tags", {}).get("timecode") or next(
            (s["tags"]["timecode"] for s in streams if s.get("tags", {}).get("timecode")), None)
        info.update(rate=rate, width=width, height=height,
                    start=_timecode_seconds(timecode, rate) if timecode else Fraction(0))
    return info


# ── Writing ───────────────────────────────────────────────────────────────────

def t(value: Fraction) -> str:
    """An FCPXML time: a whole or rational number of seconds."""
    value = Fraction(value)
    return f"{value.numerator}s" if value.denominator == 1 else f"{value.numerator}/{value.denominator}s"


def frames_to_cuts(cuts: list[dict]) -> list[tuple[int, int]]:
    """Each cut's (timeline offset, length) in frames. Boundaries are rounded from
    the running total, so the cut never drifts more than half a frame from the
    draft (whose captions and graphics are timed on that unrounded clock)."""
    out, running, prev = [], 0.0, 0
    for c in cuts:
        running += c["end"] - c["start"]
        edge = round(running * FPS)
        out.append((prev, edge - prev))
        prev = edge
    return out


def _source_in(cut_start: float, info: dict) -> Fraction:
    """A cut's in-point in the file's own timecode, on one of its frames."""
    rate = info.get("rate") or Fraction(FPS)
    return info["start"] + Fraction(round(cut_start * rate)) / rate


def transform(cut: dict, info: dict) -> str:
    """The draft's framing of a cut (crop around the face, punch-in) as an FCPXML
    transform on top of "fill": scale relative to the filled frame, and position
    in percent of the timeline's height (FCPXML's unit), y pointing up. Empty when
    the cut is simply the centre of the frame."""
    sw, sh = int(info.get("width") or 0), int(info.get("height") or 0)
    if not (sw and sh) or ("crop_x" not in cut and (cut.get("zoom") or 1.0) == 1.0):
        return ""
    w, h, x, y = framing.exact_window(cut, sw, sh)
    fill = max(editor.W / sw, editor.H / sh)
    total = editor.W / w
    dx, dy = x + w / 2 - sw / 2, y + h / 2 - sh / 2
    px, py = -dx * total / editor.H * 100, dy * total / editor.H * 100
    scale = total / fill
    if abs(px) < 0.01 and abs(py) < 0.01 and abs(scale - 1) < 0.001:
        return ""
    return f'<adjust-transform position="{px:.4f} {py:.4f}" scale="{scale:.4f} {scale:.4f}"/>'


def srt(chunks: list[dict]) -> str:
    def stamp(s: float) -> str:
        ms = round(s * 1000)
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    return "\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}\n"
                     for i, c in enumerate(chunks, 1))


def fcpxml(title: str, cuts: list[dict], clips: dict[int, dict], sources: dict[int, dict],
           layers: list[tuple[str, Path, int]], music: tuple[Path, dict, float] | None, after: float = 0.0,
           broll: list[dict] = (), broll_db: float = 0.0) -> str:
    """layers: (name, file, lane) transparent video layers lasting the whole timeline.
    music: (file, media info, volume dB). after: seconds the timeline runs on past the cut (the
    close: the draft holds its last frame, then the end card, which is on the graphics layer).
    broll: a voice-over's site shots (voiceover.layout: clip_id, src, start, end) on lane 1, from the
    originals, their sound at broll_db."""
    placed = frames_to_cuts(cuts)
    cut_frames = sum(n for _, n in placed)
    gap = max(round(after * FPS), 0)
    total = cut_frames + gap
    formats = {(FRAME, editor.W, editor.H): "r1"}
    resources, assets = [], {}

    def format_id(rate: Fraction, width: int, height: int) -> str:
        key = (1 / rate, width, height)
        if key not in formats:
            formats[key] = f"r{len(formats) + 1}"
        return formats[key]

    def add_asset(key, name: str, path: Path, info: dict) -> str:
        aid = f"a{len(assets) + 1}"
        attrs = [f"id={quoteattr(aid)}", f"name={quoteattr(name)}", f"src={quoteattr(path.resolve().as_uri())}",
                 f'start="{t(info["start"])}"', f'duration="{t(info["duration"])}"',
                 f'hasVideo="{int(info["has_video"])}"', f'hasAudio="{int(info["has_audio"])}"']
        if info["has_video"]:
            attrs.append(f'format="{format_id(info["rate"], info["width"], info["height"])}"')
        if info["has_audio"]:
            attrs += ['audioSources="1"', f'audioChannels="{info["audio_channels"]}"',
                      f'audioRate="{info["audio_rate"]}"']
        assets[key] = (aid, f"<asset {' '.join(attrs)}/>")
        return aid

    for cid in dict.fromkeys([c["clip_id"] for c in cuts] + [b["clip_id"] for b in broll]):
        add_asset(("clip", cid), clips[cid]["source_name"] or Path(clips[cid]["video_path"]).name,
                  Path(clips[cid]["video_path"]), sources[cid])
    layer_infos = [(name, add_asset(("layer", name), path.name, path, {
        "start": Fraction(0), "duration": Fraction(total, FPS), "has_video": True, "has_audio": False,
        "rate": Fraction(FPS), "width": editor.W, "height": editor.H}), lane) for name, path, lane in layers]
    music_id = add_asset(("music",), music[0].name, music[0], music[1]) if music else None

    spine = []
    for k, (c, (offset, length)) in enumerate(zip(cuts, placed)):
        src = sources[c["clip_id"]]
        aid = assets[("clip", c["clip_id"])][0]
        start = _source_in(c["start"], src)
        inner = ['<adjust-conform type="fill"/>' + transform(c, src)]
        if k == 0:
            # Whole-length layers ride on the first clip; their offsets are in that
            # clip's own time, so "the start of the timeline" is the clip's in-point.
            for b in broll:  # the site footage, each shot where it was in the draft
                bsrc = sources[b["clip_id"]]
                f0, f1 = round(b["start"] * FPS), round(b["end"] * FPS)
                inner.append(f'<asset-clip ref="{assets[("clip", b["clip_id"])][0]}" lane="1" '
                             f'offset="{t(start + Fraction(f0, FPS))}" '
                             f'name={quoteattr(clips[b["clip_id"]]["title"] or clips[b["clip_id"]]["source_name"] or "Site footage")} '
                             f'start="{t(_source_in(b["src"], bsrc))}" duration="{t(Fraction(f1 - f0, FPS))}">'
                             f'<adjust-conform type="fill"/><adjust-volume amount="{broll_db:.1f}dB"/></asset-clip>')
            for name, lid, lane in layer_infos:
                inner.append(f'<asset-clip ref="{lid}" lane="{lane}" offset="{t(start)}" name={quoteattr(name)} '
                             f'start="0s" duration="{t(Fraction(total, FPS))}"/>')
            if music:
                path, info, volume_db = music
                length_m = min(Fraction(total, FPS), info["duration"])
                inner.append(f'<asset-clip ref="{music_id}" lane="-1" offset="{t(start)}" name="Music" '
                             f'start="{t(info["start"])}" duration="{t(length_m)}" audioRole="music">'
                             f'<adjust-volume amount="{volume_db:.1f}dB"/></asset-clip>')
        spine.append(f'<asset-clip ref="{aid}" offset="{t(Fraction(offset, FPS))}" '
                     f'name={quoteattr(clips[c["clip_id"]]["title"] or clips[c["clip_id"]]["source_name"] or "Clip")} '
                     f'start="{t(start)}" duration="{t(Fraction(length, FPS))}" tcFormat="NDF">'
                     + "".join(inner) + "</asset-clip>")
    if gap:
        spine.append(f'<gap name="End card" offset="{t(Fraction(cut_frames, FPS))}" start="3600s" '
                     f'duration="{t(Fraction(gap, FPS))}"/>')

    for (duration, width, height), fid in formats.items():
        resources.append(f'<format id="{fid}" frameDuration="{t(duration)}" width="{width}" height="{height}"/>')
    resources += [xml for _, xml in assets.values()]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.8">\n'
        "<resources>\n" + "\n".join(resources) + "\n</resources>\n"
        f'<library><event name="Studio"><project name={quoteattr(title)}>\n'
        f'<sequence format="r1" duration="{t(Fraction(total, FPS))}" tcStart="0s" tcFormat="NDF" '
        'audioLayout="stereo" audioRate="48k"><spine>\n' + "\n".join(spine) +
        "\n</spine></sequence></project></event></library>\n</fcpxml>\n"
    )


_HOW_TO = """How to open "{title}" in DaVinci Resolve (free version is fine)
=================================================================

1. Open DaVinci Resolve and create or open a project (any name).
2. File > Import > Timeline... and choose timeline.fcpxml in this folder.
   In the dialog that pops up, leave "Automatically import source clips into
   media pool" ticked and press OK.
3. The timeline opens at 1080x1920, 30 fps:
     V1  the cut, from your ORIGINAL clips (full quality; drag a cut's edge to
         get back footage the draft trimmed away){gap}
{layers}{music}4. Captions you want to retype: File > Import > Subtitle... > captions.srt,
   drag it onto the timeline, and turn off the captions track (the eye icon).
5. Export: Deliver page > preset "H.264 Master" (or YouTube 1080p), set the
   resolution to 1080x1920 if it isn't already, then Render. Instagram is happy
   with anything around -14 LUFS loudness, which is what the Studio draft used.

Framing: each cut carries the draft's framing (horizontal shots cropped around you,
and the punch-ins) as Zoom and Position in the Inspector's Transform settings. If
one looks a little off, select the cut and adjust Inspector > Video > Transform.

If a clip shows black bars or looks zoomed oddly: Project Settings (cog, bottom
right) > Image Scaling > "Mismatched resolution files" > "Scale full frame with crop".
If Resolve says media is offline (red clips), the original clips aren't where the
timeline expects them, e.g. after downloading this as a zip: select the clips in
the Media Pool, right-click > Relink Selected Clips..., and choose the Footage
folder next to this file (or your Studio Library folder).

The Studio draft this came from: {draft}
"""


def build(conn, production_id: int) -> Path:
    """Write the Resolve folder for a production's current draft. Returns it."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone() or {})
    if not production.get("edl"):
        raise RuntimeError("this production has no finished draft yet")
    edl = json.loads(production["edl"])
    cuts = edl["cuts"]
    pip = edl.get("pip") or {}
    shots = pip.get("shots") or []
    ids = list(dict.fromkeys([c["clip_id"] for c in cuts] + [b["clip_id"] for b in shots]))
    import onedrive
    onedrive.ensure_originals(conn, ids)  # the timeline points at the originals: freed ones come back first
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    clips = {r["id"]: dict(r) for r in rows}
    gone = [i for i in ids if i not in clips or not Path(clips[i]["video_path"]).exists()]
    if gone:
        raise RuntimeError(f"original footage is missing for clip(s) {', '.join(map(str, gone))}")

    folder = folder_for(production)
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=folder.name + ".", dir=folder.parent))
    try:
        sources = {cid: media_info(Path(clips[cid]["video_path"])) for cid in ids}
        exact = sum(c["end"] - c["start"] for c in cuts)
        # The close (editor.close_ending): the last frame held, then the end card on the graphics layer.
        close = edl.get("close") or {}
        close_at = float(close.get("at") or exact)
        card = close.get("card") or None
        whole = round(close_at + (card["seconds"] if card else 0.0), 3)
        end = {"start": close_at, "duration": card["seconds"], "ask": card.get("ask", ""),
               "handle": card.get("handle", "")} if card else None
        chunks = editor.caption_chunks(editor.timeline(cuts, clips), close_at)
        accent = editor.ACCENT.get(production["brand"], "#ff4d6d")
        look = editor.draft_look(edl)  # the draft's own titles and caption font
        if look["titles"] in ("gallery", "site"):
            chunks = editor.lit_captions(editor.key_word_captions(chunks, edl.get("key_words") or []), edl.get("bright"))
            if pip.get("spans"):
                import voiceover
                chunks = voiceover.lit_beside(chunks, pip)
        layers, lane = [], 1
        if shots:  # a voice-over: the site footage on V2, his window above it
            import voiceover
            lane = 2
            if voiceover.resolve_layer(edl, cuts, clips, whole, work / "window.mov"):
                layers.append(("Your window over the site footage", work / "window.mov", lane))
                lane += 1
        # The drawn animations get a track of their own, under the cards and the captions.
        import behind
        inserts = [g for g in edl.get("graphics", []) if g.get("style") in ("scene", "picture") and "start" in g]
        scenes = [g for g in inserts if g.get("style") == "scene"]
        inserts += behind.for_resolve(edl)
        if inserts and editor.scenes_layer(inserts, whole, work / "animations.mov"):
            pics = any(g.get("style") == "picture" for g in inserts)
            name = "Drawn animations and pictures" if pics else "Drawn animations"
            if any(g.get("style") == behind.STYLE for g in inserts):
                name = "Words behind you" if len(inserts) == len(behind.for_resolve(edl)) else name + ", words behind you"
            layers.append((name, work / "animations.mov", lane))
            lane += 1
        cards = [g for g in edl.get("graphics", []) if g.get("style") not in ("scene", "picture")]
        if edl.get("hook_text", "").strip() or cards or end:
            editor.render_overlay([], edl.get("hook_text", ""), whole, work / "graphics.mov",
                                  cards, accent, fmt="mov", font=look["caption_font"], style=look["titles"],
                                  hook_italic=edl.get("hook_italic"), end=end)
            layers.append(("Hook, graphics and end card" if end else "Hook and graphics", work / "graphics.mov", lane))
        if chunks:
            paper = [(g["start"], g["start"] + g["duration"]) for g in scenes if g.get("look") == "cutaway"]
            editor.render_overlay(chunks, "", whole, work / "captions.mov", accent=accent, fmt="mov", paper=paper,
                                  font=look["caption_font"], style=look["titles"],
                                  **({"pip": {"side": pip["side"], "spans": pip["spans"]}} if pip.get("spans") else {}))
            layers.append(("Captions", work / "captions.mov", lane + 1))
            (work / "captions.srt").write_text(srt(chunks), encoding="utf-8")

        music = None
        info = json.loads(production["music"] or "{}")
        track = Path(info["file"]) if info.get("file") else (
            ROOT / "Music" / f"epidemic_{re.sub(r'[^A-Za-z0-9_-]', '', info.get('recording_id', ''))[:40]}.mp3"
            if info.get("recording_id") else None)
        if info.get("source") == "epidemic" and track and track.exists():
            shutil.copy2(track, work / "music.mp3")
            # The level the draft's mix gave the track (older drafts: the plan's music level).
            music = (work / "music.mp3", media_info(work / "music.mp3"), info.get("gain_db", edl["music"]["volume_db"]))

        # Write the timeline against the final folder's paths, then move it into place.
        final_layers = [(n, folder / p.name, lane) for n, p, lane in layers]
        final_music = (folder / "music.mp3", music[1], music[2]) if music else None
        title = f"{production['title'] or 'Studio post'} v{production['version']}"
        (work / "timeline.fcpxml").write_text(
            fcpxml(title, cuts, clips, sources, final_layers, final_music, after=whole - exact, broll=shots,
                   broll_db=float(pip.get("site_gain_db") or -20.0)), encoding="utf-8")
        lane_lines = (("     V2  the site footage, from your original clips (its sound low under your voice)\n"
                       if shots else "")
                      + "".join(f"     V{lane + 1}  {name.lower()} (transparent layer)\n" for name, _, lane in layers))
        music_line = (f"     A2  music: {info.get('title', 'Epidemic Sound track')} by {info.get('artist', '?')}, "
                      f"at {music[2]:.0f} dB (the draft also dipped it under your voice)\n"
                      if music else
                      "     (no music: personal posts get theirs in the Instagram app)\n")
        (work / "HOW TO OPEN.txt").write_text(
            _HOW_TO.format(title=title, layers=lane_lines, music=music_line, draft=production["video_path"],
                           gap=(". It ends in a short gap: the draft holds the last\n         frame there"
                                + (" and the end card covers it." if end else ".")) if whole - exact >= 0.5 / FPS else ""),
            encoding="utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        work.rename(folder)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    log.info("Resolve project for production %s written to %s", production_id, folder)
    return folder


def download_files(conn, production: dict) -> list[tuple[str, Path]]:
    """What the zip download holds, as (name in the zip, file): the project folder,
    plus the original clips under Footage/ to relink to (the timeline points at
    where they are on the Studio machine)."""
    folder = existing(production)
    if folder is None:
        return []
    top = folder.name
    files = [(f"{top}/{p.relative_to(folder).as_posix()}", p) for p in sorted(folder.rglob("*")) if p.is_file()]
    edl = json.loads(production["edl"] or "{}")
    ids = list(dict.fromkeys([c["clip_id"] for c in edl.get("cuts", [])]
                             + [b["clip_id"] for b in (edl.get("pip") or {}).get("shots") or []]))
    seen = set()
    for cid in ids:
        row = conn.execute("SELECT video_path FROM clips WHERE id = ?", (cid,)).fetchone()
        path = Path(row["video_path"]) if row else None
        if path and path.is_file() and path.name.lower() not in seen:
            seen.add(path.name.lower())  # Resolve relinks by file name
            files.append((f"{top}/Footage/{path.name}", path))
    return files
