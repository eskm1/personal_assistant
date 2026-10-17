"""
Style study: measure reels you admire (yours and other accounts') and turn the
numbers into a house style the idea generator and editor follow.

    python worker.py --style-study reels.txt      measure them, write a report and a proposal
    python worker.py --style-apply <study>        put the approved proposal in the playbook

reels.txt holds links (Instagram reels, TikTok, YouTube Shorts) under two headings:

    Ours:
    https://www.instagram.com/reel/AAA/  most saves we've had, the kitchen reveal
    Inspiration:
    https://www.instagram.com/reel/BBB/  love the pace and the before/after

(or put video files in a folder and pass the folder; files whose name starts with
"ours" count as ours). Each reel is downloaded on this laptop (public posts only),
measured with no API cost (shot lengths, first cut, speaking pace, pauses,
loudness, music under the voice), then Claude looks at a dozen stills for the
things only eyes can judge (captions, layouts, B-roll, hooks, calls to action),
about US$0.05 a reel. Claude then writes a short report and a proposed "House
style (measured)" section; nothing reaches the playbook until --style-apply.
Downloaded videos are deleted after measuring; the stills and numbers stay.

The same pieces run the online "Reference guides" (cloud/CONTRACT.md): the
worker's sync analyses each reference the team adds with analyse(), and drafts
the brand's style guide with draft_guide() whenever the set of references
changes; once the owner approves it, apply_section() puts it in the playbook.
"""
import base64
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

import extract
import settings
from settings import ROOT

log = logging.getLogger("studio.style")

STUDIES = ROOT / "Style"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
URL_RE = re.compile(r"https?://\S+")
START, END = "<!-- style-study:start -->", "<!-- style-study:end -->"
SCENE_THRESHOLD = 0.3   # a cut to different footage
JUMP_MIN, JUMP_RATIO = 0.004, 4  # a jump cut / punch-in: a smaller spike, well above its neighbours
MAX_STILLS = 12
# Bump when measure()/look() change: the sync re-analyses references made by an older one
# (reference_analysis.analyser).
ANALYSER = 1
# Instagram sign-in for downloads on a machine with no browser (the server): a
# Netscape-format cookies.txt exported from a browser signed in to a spare account.
COOKIES_FILE = ROOT / "cookies.txt"


# ── Input ─────────────────────────────────────────────────────────────────────

def parse_links(text: str) -> list[dict]:
    """[{group: ours|inspiration, url, note}] from headed lists of links."""
    group, out = "inspiration", []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line:
            continue
        head = line.lower().rstrip(":")
        if not URL_RE.search(line) and head in ("ours", "our reels", "mine", "urban makers"):
            group = "ours"
            continue
        if not URL_RE.search(line) and head in ("inspiration", "reference", "references", "others", "theirs"):
            group = "inspiration"
            continue
        m = URL_RE.search(line)
        if m:
            url = m.group(0).rstrip(").,")
            prefix = line[:m.start()].lower()
            g = "ours" if prefix.startswith(("ours", "mine")) else group
            out.append({"group": g, "url": url, "note": line[m.end():].strip(" -–:")})
    return out


def download(url: str, out_dir: Path) -> Path:
    """Fetch a public reel with yt-dlp (≤1080 px wide, mp4)."""
    import yt_dlp
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(extractor)s_%(id)s.%(ext)s"),
        "quiet": True, "noprogress": True, "noplaylist": True, "restrictfilenames": True,
        "socket_timeout": 30, "retries": 2,
    }
    if settings.FFMPEG:
        opts["ffmpeg_location"] = str(Path(settings.FFMPEG).parent)
    browser = settings._env("STUDIO_YTDLP_COOKIES_BROWSER")  # e.g. "chrome" if Instagram asks for a login
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    elif COOKIES_FILE.is_file():  # on the server there's no browser: an exported cookies.txt instead
        opts["cookiefile"] = str(COOKIES_FILE)
        browser = "cookies.txt"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    except Exception as e:  # yt-dlp's DownloadError and friends: say what to do about it
        raise RuntimeError(download_problem(url, str(e), browser)) from None
    if not path.exists():
        found = sorted(out_dir.glob(f"*{info.get('id', '')}*"))
        if not found:
            raise RuntimeError("download finished but no video file appeared")
        path = found[0]
    return path


def download_problem(url: str, message: str, browser: str = "") -> str:
    """A yt-dlp error, in words that say what to do."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", message).replace("ERROR: ", "").strip()
    low = text.lower()
    site = "Instagram" if "instagram" in url.lower() else "The site"
    if any(k in low for k in ("login", "log in", "cookies", "rate-limit", "rate limit", "empty media response",
                              "authentication", "sign in")):
        if browser:
            return (f"{site} refused the download even with {browser}'s sign-in: open the link in {browser} "
                    f"and sign in, then it is retried within a day ({text[:150]})")
        if os.name != "nt":
            return (f"{site} needs a login: put an exported {COOKIES_FILE.name} (a spare account's sign-in) in "
                    f"{COOKIES_FILE.parent}, or save the video and drop it in OneDrive: "
                    "Studio Inbox > References > Inspiration")
        return (f"{site} needs a login: set STUDIO_YTDLP_COOKIES_BROWSER=chrome (or edge) in studio\\.env, "
                f"signed in to {site if site != 'The site' else 'it'} in that browser")
    if "unsupported url" in low:
        return "Studio can't download from that link (use an Instagram reel, TikTok or YouTube Shorts link)"
    if any(k in low for k in ("private", "not available", "unavailable", "404", "removed", "deleted")):
        return f"The post is private, deleted or not public ({text[:150]})"
    if any(k in low for k in ("timed out", "getaddrinfo", "connection", "network", "resolve")):
        return f"Couldn't reach {site.lower() if site != 'The site' else 'the site'} (no internet?): {text[:150]}"
    return f"Download failed: {text[:250]}"


# ── Measuring (no API cost) ───────────────────────────────────────────────────

def cut_times(video: Path) -> list[float]:
    """Hard cuts, including jump cuts and punch-ins on a talking head, which change the
    picture far less than a cut to new footage: a frame counts as a cut when its
    change score clearly spikes above the frames around it (or is large outright)."""
    result = subprocess.run(
        [settings.FFMPEG or "ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
         "-vf", "scale=270:-2,select='gte(scene,0)',metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    times = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", result.stdout)]
    scores = [float(v) for v in re.findall(r"lavfi\.scene_score=([0-9.]+)", result.stdout)]
    n = min(len(times), len(scores))
    cuts = []
    for i in range(1, n):
        if times[i] < 0.1:  # the first frames have nothing to differ from
            continue
        s = scores[i]
        window = scores[max(0, i - 15):i] + scores[i + 1:i + 16]
        local = float(np.median(window)) if window else 0.0
        peak = s >= max(scores[max(0, i - 3):i + 4])
        # A cut is a one-off spike; camera or subject motion stays high for several frames.
        after = scores[i + 2:i + 6]
        settles = not after or s >= 2.5 * float(np.median(after))
        if s >= SCENE_THRESHOLD or (peak and settles and s >= JUMP_MIN and s >= JUMP_RATIO * max(local, 0.001)):
            if not cuts or times[i] - cuts[-1] >= 0.2:  # one cut, not a flash of two
                cuts.append(round(times[i], 3))
    return cuts


def _pct(values, q):
    return round(float(np.percentile(values, q)), 2) if len(values) else None


def measure(video: Path) -> dict:
    """Everything that can be measured without looking."""
    import editor
    import quality
    import transcribe
    meta = extract.probe(video)
    duration = meta["duration"]
    cuts = [t for t in cut_times(video) if 0.05 < t < duration - 0.05]
    edges = [0.0, *cuts, duration]
    shots = [b - a for a, b in zip(edges, edges[1:]) if b - a > 0.05]
    out = {"duration": round(duration, 2), "width": meta["width"], "height": meta["height"], "fps": meta.get("fps"),
           "cuts": len(cuts), "first_cut": cuts[0] if cuts else None,
           "shot_mean": round(float(np.mean(shots)), 2) if shots else None,
           "shot_median": _pct(shots, 50), "shot_p90": _pct(shots, 90),
           "shot_max": round(max(shots), 2) if shots else None,
           "cut_times": cuts[:80]}
    if meta["has_audio"]:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="studio-style-") as tmp:
            wav = extract.extract_audio(video, Path(tmp) / "a.wav")
            samples = transcribe._load_wav(wav)
            t = transcribe.transcribe(wav)
        words = t.get("words", [])
        out["transcript"] = t.get("text", "")[:3000]
        if len(words) >= 5:
            span = words[-1]["end"] - words[0]["start"]
            gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
            out.update(words=len(words), words_per_sec=round(len(words) / span, 2) if span > 0 else None,
                       speech_start=round(words[0]["start"], 2), tail_after_last_word=round(duration - words[-1]["end"], 2),
                       gap_median=_pct(gaps, 50), gap_p90=_pct(gaps, 90),
                       long_pauses=sum(1 for g in gaps if g > 0.4))
        audio = quality.measure_audio(samples, words)
        # Between words a narrated reel is mostly its music bed, so voice minus "background"
        # approximates how far under the voice the music sits (only when there is a bed).
        if audio.get("snr_db") is not None and audio.get("background_db", -90) > -60:
            out["music_under_voice_db"] = audio["snr_db"]
        out["voice_db"] = audio.get("voice_db")
        out.update({f"loudness_{k}": v for k, v in editor.check_loudness(video).items()})
    else:
        out["transcript"] = ""
    return out


def stills(video: Path, cuts: list[float], duration: float, out_dir: Path) -> list[tuple[float, Path]]:
    """The first frame plus one frame inside each early shot (what a viewer sees)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    edges = [0.0, *cuts, duration]
    times = [0.15] + [round((a + b) / 2, 2) for a, b in zip(edges, edges[1:])][: MAX_STILLS - 1]
    if len(times) < 6:  # few cuts: spread evenly instead
        times = sorted({0.15, *[round(duration * k / 7, 2) for k in range(1, 7)]})
    frames = []
    for t in times[:MAX_STILLS]:
        path = out_dir / f"{t:07.2f}.jpg"
        extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                      "-vf", "scale=540:-2", "-q:v", "4", str(path)])
        if path.exists():
            frames.append((t, path))
    return frames


# ── Looking (Claude) ──────────────────────────────────────────────────────────

LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "layouts": {"type": "array", "items": {"type": "string", "enum": [
            "talking_full", "punch_in", "split_broll_top", "broll_full", "screen", "text_card", "before_after", "other"]}},
        "captions": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "words_per_chunk": {"type": "string"},
                "case": {"type": "string", "enum": ["upper", "mixed", "lower", "none"]},
                "font": {"type": "string"},
                "accent_colours": {"type": "array", "items": {"type": "string"}},
                "box": {"type": "string"},
                "position": {"type": "string"},
            },
            "required": ["present", "words_per_chunk", "case", "font", "accent_colours", "box", "position"],
            "additionalProperties": False,
        },
        "hook": {"type": "string"},
        "first_frame": {"type": "string"},
        "broll": {"type": "array", "items": {"type": "string"}},
        "graphics": {"type": "string"},
        "cta": {"type": "string"},
        "standout": {"type": "string"},
    },
    "required": ["layouts", "captions", "hook", "first_frame", "broll", "graphics", "cta", "standout"],
    "additionalProperties": False,
}

_LOOK_SYSTEM = """You study short vertical videos (Reels) to describe their editing style precisely. \
You get stills in time order (labelled with their time) and the transcript. Describe only what you can \
see or read; say "unclear" rather than guess.
- layouts: one per still, in order (talking_full = speaker fills the frame; punch_in = a tighter crop \
of the speaker; split_broll_top = footage above, speaker below; broll_full; screen = phone/computer \
screen; text_card; before_after).
- captions: words per on-screen chunk (e.g. "1-3"), case, font look (e.g. "heavy sans, all caps, \
serif accent words"), accent colours and what they mark, box/outline/shadow, position.
- hook: what the first 3 seconds say and show. first_frame: what the very first frame shows.
- broll: kinds of cutaway footage used. graphics: motion graphics, cards, arrows, counters (or "none").
- cta: how it ends and asks for something. standout: the one editing choice that makes this reel work."""


def look(frames: list[tuple[float, Path]], transcript: str, ref: str, client=None) -> dict:
    import anthropic

    import usage
    from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
    content = []
    for t, path in frames:
        content.append({"type": "text", "text": f"Still at {t:.1f}s"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(path.read_bytes()).decode()}})
    content.append({"type": "text", "text": "Transcript:\n" + (transcript or "(no speech)")})
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    response = client.beta.messages.create(
        model=CLAUDE_MODEL, max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": LOOK_SCHEMA}},
        system=_LOOK_SYSTEM, messages=[{"role": "user", "content": content}])
    usage.record("style", ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't describe this reel ({response.stop_reason})")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


# ── One reference: measure it and look at it ──────────────────────────────────

def analyse(source: str | Path, stills_dir: Path | None = None, *, download_dir: Path | None = None,
            keep_video: bool = False, client=None, ref: str = "style") -> dict:
    """Analyse one reel: a link (downloaded, then deleted unless keep_video) or a
    local video file (never deleted). Returns {measured, look, stills: [local
    paths in time order]}. Raises RuntimeError with a readable reason."""
    is_url = isinstance(source, str) and URL_RE.match(source.strip())
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(source))[-60:].strip("_") or "reel"
    stills_dir = stills_dir or STUDIES / "stills" / slug
    if is_url:
        video = download(source.strip(), download_dir or STUDIES / "downloads")
    else:
        video = Path(source)
        if not video.exists():
            raise RuntimeError(f"the video file {video.name} isn't there any more")
    try:
        try:
            measured = measure(video)
        except (RuntimeError, OSError, KeyError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"couldn't measure the video ({type(e).__name__}: {str(e)[:200]})") from e
        if stills_dir.exists():
            shutil.rmtree(stills_dir, ignore_errors=True)
        frames = stills(video, measured["cut_times"], measured["duration"], stills_dir)
        if not frames:
            raise RuntimeError("couldn't take any stills from the video")
        seen = look(frames, measured.get("transcript", ""), ref, client)
    finally:
        if is_url and not keep_video:
            video.unlink(missing_ok=True)
    return {"measured": measured, "look": seen, "stills": [str(p) for _, p in frames]}


# ── Summary ───────────────────────────────────────────────────────────────────

NUMBERS = ("duration", "cuts", "first_cut", "shot_mean", "shot_median", "shot_p90", "shot_max", "words_per_sec",
           "speech_start", "tail_after_last_word", "gap_median", "gap_p90", "long_pauses", "music_under_voice_db",
           "loudness_lufs", "loudness_true_peak")


def _side(r: dict) -> str:
    return r.get("side") or r.get("group") or "inspiration"


def summarise(reels: list[dict]) -> dict:
    """Median and range of each measurement, per side (ours / inspiration)."""
    out = {}
    for side in ("ours", "inspiration"):
        mine = [r for r in reels if _side(r) == side and r.get("measured")]
        if not mine:
            continue
        stats = {"reels": len(mine)}
        for key in NUMBERS:
            vals = [r["measured"][key] for r in mine if isinstance(r["measured"].get(key), (int, float))]
            if vals:
                stats[key] = {"median": _pct(vals, 50), "low": _pct(vals, 10), "high": _pct(vals, 90)}
        layouts = [lay for r in mine for lay in (r.get("look") or {}).get("layouts", [])]
        if layouts:
            stats["layout_share"] = {k: round(layouts.count(k) / len(layouts), 2) for k in sorted(set(layouts))}
        out[side] = stats
    return out


# ── The style guide (Claude) ──────────────────────────────────────────────────

GUIDE_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}, "report": {"type": "string"}, "changes": {"type": "string"}},
    "required": ["content", "report", "changes"],
    "additionalProperties": False,
}

BRAND_NAMES = {"urban_makers": "Urban Makers", "personal": "Bryan's personal page"}

_GUIDE_SYSTEM = """You are the editor-in-chief for the Instagram Reels of {brand_name}. The team keeps \
a library of reference reels: their own best ones ("ours") and other accounts' they admire \
("inspiration"), each with the team's note on why it works. Every reel was measured on the laptop \
(cuts, shot lengths, speaking pace, pauses, loudness, how far the music sits under the voice) and its \
stills were described (captions, layouts, B-roll, hook, call to action). Write the house style for \
{brand_name} from the active references.

{brands}

The rest of the playbook the idea generator and editor follow (the house style sits next to it; \
don't repeat these rules, and say so where the references contradict one):
<playbook>
{playbook}
</playbook>

{previous}

Write three Markdown texts:
- content: the "## House style (measured)" playbook section the editor will follow: concrete, \
measurable rules with numbers and ranges (first cut, shot lengths, longest shot, speaking pace, \
pauses, music level under the voice, loudness, caption chunking/case/accents/box/position, layout \
mix, hook and first frame, B-roll order of preference, call to action). Fit renovation and the \
brand's own voice; don't copy devices that only suit other niches. Where our reels and the \
inspiration disagree, say which to follow and why (our own best reels are proof it works for this \
audience; the team's notes say what they admire). Mark numbers as targets, not laws. Under 60 lines.
- report: for the team to read in two minutes. What our best reels have in common, how the \
inspiration differs, and the 5 to 8 changes most likely to improve our reels, each tied to evidence \
(numbers or what the stills show) and to the team's notes. Plain words, short bullets, no hype.
- changes: what changed since the house style in force and why, one short bullet per change, each \
tied to the references added or retired and their notes, naming them by title, for example: \
"Added 'kitchen reveal' (ours, most saves): first cut moved earlier, from 1.5 s to 0.8 s." Say when \
a rule stays although the reference behind it was retired, because others still support it. If \
nothing in force yet, say this is the first guide and which references weigh most.
Never invent measurements; if something wasn't measured, say so or leave it out."""

_PREVIOUS = """The house style in force now ({label}). Keep what the active references still support; \
change what they contradict or no longer support:
<previous_guide>
{content}
</previous_guide>"""

_NO_PREVIOUS = "There is no house style in force yet: this is the first one."


def _without_style(text: str) -> str:
    """The playbook minus an applied house style (that is given separately)."""
    if START in text and END in text:
        return (text[:text.index(START)] + text[text.index(END) + len(END):]).strip()
    return text


def _evidence(r: dict, full: bool = True) -> dict:
    m = r.get("measured") or {}
    out = {"id": r.get("id"), "title": r.get("title") or "", "side": _side(r),
           "why": r.get("why") or r.get("note") or "", "source": r.get("url") or r.get("file_name") or r.get("file")}
    if full and m:
        out["measured"] = {k: m.get(k) for k in NUMBERS if m.get(k) is not None}
        out["transcript_start"] = (m.get("transcript") or "")[:400]
        out["look"] = r.get("look")
    return out


def _usage(response, usd: float) -> dict:
    u = getattr(response, "usage", None)
    return {"model": getattr(response, "model", "") or "",
            "input_tokens": getattr(u, "input_tokens", 0) or 0 if u else 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0 if u else 0,
            "cost_usd": round(usd, 4)}


def draft_guide(brand: str, references: list[dict], previous_guide: dict | None = None,
                added: list[dict] | None = None, removed: list[dict] | None = None, client=None,
                ref: str | None = None) -> dict:
    """Write the house style for a brand from its active, analysed references.

    references: [{id?, title, side, why, url/file_name, measured, look}];
    previous_guide: {version?, content} of the guide in force, or None;
    added/removed: the references (same shape; removed may lack numbers) that
    differ from the previous guide. Returns {content, report, changes, usage}."""
    import anthropic

    import llm
    import playbook
    import usage
    from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
    usable = [r for r in references if r.get("measured")]
    if not usable:
        raise RuntimeError("no analysed references to learn from")
    brands = settings.brands_text()
    if previous_guide and (previous_guide.get("content") or "").strip():
        label = f"version {previous_guide['version']}" if previous_guide.get("version") else "applied earlier"
        previous = _PREVIOUS.replace("{label}", label).replace("{content}", previous_guide["content"].strip())
    else:
        previous = _NO_PREVIOUS
    brand_name = BRAND_NAMES.get(brand, brand)
    system = (_GUIDE_SYSTEM.replace("{brand_name}", brand_name).replace("{brands}", brands)
              .replace("{playbook}", _without_style(playbook.text())).replace("{previous}", previous))
    evidence = {
        "brand": brand,
        "summary": summarise(usable),
        "references": [_evidence(r) for r in usable],
        "added_since_previous": [_evidence(r, full=False) for r in (added or [])],
        "retired_since_previous": [_evidence(r, full=False) for r in (removed or [])],
    }
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=32000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": GUIDE_SCHEMA}},
        system=system, messages=[{"role": "user", "content": "Evidence:\n" + json.dumps(evidence, indent=1)}])
    usd = usage.record("style", ref or f"guide:{brand}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't write the style guide ({response.stop_reason})")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    return {"content": data["content"].strip(), "report": data["report"].strip(),
            "changes": data["changes"].strip(), "usage": _usage(response, usd)}


# ── Running a study from the command line ─────────────────────────────────────

def _inputs(source: Path) -> list[dict]:
    if source.is_dir():
        return [{"group": "ours" if p.name.lower().startswith(("ours", "mine")) else "inspiration",
                 "file": str(p), "note": ""}
                for p in sorted(source.iterdir()) if p.suffix.lower() in VIDEO_EXTS]
    return parse_links(source.read_text(encoding="utf-8"))


def applied_section() -> str | None:
    """The house style in the playbook now (between the markers), if any."""
    import playbook
    text = playbook.text()
    if START in text and END in text:
        return text[text.index(START) + len(START):text.index(END)].strip()
    return None


def run(source: Path, keep_videos: bool = False, client=None, say=print) -> Path:
    """Measure every reel, then write report.md and house_style.md. Returns the study folder."""
    reels = _inputs(source)
    if not reels:
        raise RuntimeError("no links or videos found (see the example at the top of style_study.py)")
    study = STUDIES / datetime.now().strftime("%Y-%m-%d_%H%M")
    study.mkdir(parents=True, exist_ok=True)
    say(f"Studying {len(reels)} reels ({sum(r['group'] == 'ours' for r in reels)} ours) in {study}")
    for i, reel in enumerate(reels, 1):
        name = reel.get("url") or Path(reel["file"]).name
        reel["title"] = name if reel.get("url") else Path(reel["file"]).stem
        try:
            result = analyse(reel.get("url") or Path(reel["file"]), study / "stills" / f"{i:02d}",
                             download_dir=study / "videos", keep_video=keep_videos, client=client,
                             ref=f"style:{study.name}")
            reel.update(result)
            m = result["measured"]
            say(f"  {i}/{len(reels)} {reel['group']}: {name}: {m['duration']:.0f}s, {m['cuts']} cuts, "
                f"shots ~{m['shot_median']}s, {m.get('words_per_sec') or '-'} words/s")
        except Exception as e:  # one bad link shouldn't sink the study
            reel["error"] = str(e)[:300] if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"[:300]
            say(f"  {i}/{len(reels)} skipped {name}: {reel['error']}")
        (study / "reels.json").write_text(json.dumps(reels, indent=1), encoding="utf-8")
    if not any(r.get("measured") for r in reels):
        raise RuntimeError("none of the reels could be measured; see the reasons above")
    say("Writing the report and the proposed house style ...")
    current = applied_section()
    measured = [r for r in reels if r.get("measured")]
    result = draft_guide("urban_makers", measured, {"content": current} if current else None,
                         added=measured, client=client, ref=f"style:{study.name}")
    (study / "summary.json").write_text(json.dumps(summarise(reels), indent=1), encoding="utf-8")
    (study / "report.md").write_text(result["report"], encoding="utf-8")
    (study / "house_style.md").write_text(result["content"], encoding="utf-8")
    (study / "changes.md").write_text(result["changes"], encoding="utf-8")
    if not keep_videos:
        shutil.rmtree(study / "videos", ignore_errors=True)
    return study


# ── Into the playbook ─────────────────────────────────────────────────────────

def apply_section(section: str, origin: str) -> Path:
    """Put a house style into the playbook between the markers, replacing an earlier one."""
    import playbook
    text = playbook.text()
    block = f"{START}\n{section.strip()}\n\n_{origin}_\n{END}"
    if START in text and END in text:
        text = text[:text.index(START)] + block + text[text.index(END) + len(END):]
    elif "## Notes from reviews" in text:
        i = text.index("## Notes from reviews")
        text = text[:i] + block + "\n\n" + text[i:]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path = playbook.ensure()
    path.write_text(text, encoding="utf-8")
    return path


def apply(study: str | Path) -> Path:
    """Put a study's house style into the playbook, replacing an earlier one."""
    folder = Path(study)
    if not folder.is_absolute():
        folder = STUDIES / study
    section = (folder / "house_style.md").read_text(encoding="utf-8")
    return apply_section(section, f"From the style study of {folder.name}.")


def latest() -> Path | None:
    done = sorted(p for p in STUDIES.glob("*") if (p / "house_style.md").exists()) if STUDIES.exists() else []
    return done[-1] if done else None


if __name__ == "__main__":  # tiny self-check of the link parser
    sample = "Ours:\n- https://www.instagram.com/reel/AAA/ kitchen reveal\nInspiration:\nhttps://www.instagram.com/reel/BBB/\n"
    print(parse_links(sample))
