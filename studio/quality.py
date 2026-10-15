"""
Quality gate for every clip: is it good to use, usable with issues, or a retake?

Measured here, with no API cost:
  - audio: how loud the voice is, how loud the background is between words
    (signal-to-noise), distortion (clipped samples), and Whisper's confidence
  - picture: exposure (too dark, blown highlights, flat/hazy) from keyframes
  - format: resolution, frame rate, orientation (Reels are vertical), length
Judged by Claude while tagging (classify.py "checks"), since they need eyes:
  focus, stability, framing, lighting on the subject.

Each problem becomes a flag with what's wrong and a tip for next time, so the
team learns what to change on site. One "fail" makes the verdict "retake".
"""
import subprocess

import numpy as np

import extract
import settings

GOOD, CHECK, RETAKE = "good", "check", "retake"

# What to do differently next time, by flag code.
TIPS = {
    "voice_quiet": "Get the mic closer: a clip-on mic, or hold the phone within arm's length when talking.",
    "voice_very_quiet": "Use a clip-on mic. From this distance the phone mic mostly hears the room.",
    "noisy": "Move away from tools and traffic, pause the noisy work for the take, or use a clip-on mic.",
    "very_noisy": "Re-record somewhere quieter or with a clip-on mic; the noise is nearly as loud as you.",
    "distorted": "Audio overloads: keep the mic out of wind and away from your mouth, or speak a bit softer.",
    "unclear_speech": "Speak toward the phone and slow down a little; Whisper struggled to make out the words.",
    "dark": "Face a window or turn on site lights; avoid filming with the light behind the subject.",
    "too_dark": "Far too dark to use. Add light, or tap-and-hold on the subject to lock exposure.",
    "blown": "Bright windows or sky are blown out. Turn so the window lights the subject instead of being behind it.",
    "overexposed": "Overexposed. Tap the subject and drag the exposure slider down before recording.",
    "flat": "Picture looks hazy. Wipe the lens (site dust) before filming.",
    "low_res": "Film in 4K or at least 1080p (Settings > Camera > Record Video).",
    "very_low_res": "This resolution is too low for Reels. Film in 4K or 1080p.",
    "low_fps": "Use 30 or 60 fps.",
    "horizontal": "Hold the phone upright. A Reel crops a horizontal clip to its middle third. "
                  "(Fine for Z fc B-roll in 4K if the subject is centred.)",
    "hdr": "Turn off HDR on the iPhone (Settings > Camera > Record Video > HDR Video) so colours match "
           "the other cameras.",
    "square": "Hold the phone upright for Reels.",
    "very_short": "Let the camera roll 2 to 3 seconds before and after the moment.",
    "focus": "Tap the subject to focus before recording, and wipe the lens.",
    "stability": "Brace the phone against something, use both hands, or move slower. A cheap gimbal helps for walking shots.",
    "framing": "Keep the subject in the middle of a vertical frame, with room above the head and the key action in view.",
    "lighting": "Light the subject from the front or side; avoid harsh overhead light and backlight.",
}

_CLAUDE_CHECKS = ("focus", "stability", "framing", "lighting")


def _db(x: float) -> float:
    return max(-90.0, float(20 * np.log10(max(x, 1e-9))))  # -90: digital silence


def measure_audio(samples: np.ndarray, words: list[dict], segments: list[dict] | None = None,
                  rate: int = 16000) -> dict:
    """Voice level, background level between words, distortion and speech clarity."""
    out: dict = {}
    if samples.size == 0:
        return out
    out["clipped_fraction"] = round(float(np.mean(np.abs(samples) >= 0.985)), 6)
    speech = np.zeros(samples.size, dtype=bool)
    for w in words:
        speech[max(0, int(w["start"] * rate)):int(w["end"] * rate)] = True
    if len(words) >= 5 and speech.any():
        out["voice_db"] = round(_db(np.sqrt(np.mean(samples[speech] ** 2))), 1)
        # Background: 100 ms windows well clear of any word (0.15 s margin).
        margin = int(0.15 * rate)
        near = np.convolve(speech.astype(np.int8), np.ones(2 * margin + 1, dtype=np.int8), "same") > 0
        win = rate // 10
        levels = []
        for i in range(0, samples.size - win, win):
            if not near[i:i + win].any():
                levels.append(_db(np.sqrt(np.mean(samples[i:i + win] ** 2))))
        if len(levels) >= 5:  # at least half a second of gaps
            out["background_db"] = round(float(np.median(levels)), 1)
            out["snr_db"] = round(out["voice_db"] - out["background_db"], 1)
        logprobs = [s["logprob"] for s in segments or [] if s.get("logprob") is not None]
        if logprobs:
            out["speech_confidence"] = round(float(np.mean(logprobs)), 2)
    return out


def measure_picture(video, meta: dict, width: int = 180) -> dict:
    """Exposure from keyframes, decoded small and grey (fast even for 4K HEVC)."""
    w = width if meta["width"] <= meta["height"] else round(width * meta["width"] / meta["height"] / 2) * 2
    h = round(w * meta["height"] / meta["width"] / 2) * 2 if meta["width"] else w

    def grab(keyframes_only: bool) -> np.ndarray:
        args = [settings.FFMPEG or "ffmpeg", "-v", "error"]
        if keyframes_only:
            args += ["-skip_frame", "nokey", "-i", str(video), "-vf", f"scale={w}:{h},format=gray",
                     "-fps_mode", "passthrough"]
        else:  # few keyframes (re-encoded or messaging-app files): one frame every 2 s instead
            args += ["-i", str(video), "-vf", f"fps=1/2,scale={w}:{h},format=gray"]
        proc = subprocess.run(args + ["-frames:v", "240", "-f", "rawvideo", "-"], capture_output=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=900)
        data = np.frombuffer(proc.stdout, dtype=np.uint8)
        return data if proc.returncode == 0 else np.zeros(0, dtype=np.uint8)

    data = grab(True)
    n = data.size // (w * h)
    if n < min(5, (meta.get("duration") or 0) / 2):
        data = grab(False)
        n = data.size // (w * h)
    if n == 0:
        return {}
    frames = np.clip((data[: n * w * h].reshape(n, w * h).astype(np.float32) - 16) / 219, 0, 1)  # limited range
    means = frames.mean(axis=1)
    high = (frames >= 0.97).mean(axis=1)
    p5, p95 = np.percentile(frames, 5, axis=1), np.percentile(frames, 95, axis=1)
    return {"frames_checked": int(n), "brightness": round(float(np.median(means)), 3),
            "blown_fraction": round(float(np.median(high)), 3),
            "contrast": round(float(np.median(p95 - p5)), 3)}


def assess(meta: dict, audio: dict, picture: dict, checks: dict | None = None) -> dict:
    """Turn measurements (and Claude's checks) into a verdict and flags with tips."""
    flags = []

    def flag(code, area, level, text):
        flags.append({"code": code, "area": area, "level": level, "text": text, "tip": TIPS.get(code, "")})

    # Audio: only judged on clips with speech, except distortion.
    if "voice_db" in audio:
        v = audio["voice_db"]
        if v < -48:
            flag("voice_very_quiet", "audio", "fail", f"Voice is very quiet ({v:.0f} dB)")
        elif v < -38:
            flag("voice_quiet", "audio", "warn", f"Voice is quiet ({v:.0f} dB)")
    if "snr_db" in audio:
        snr = audio["snr_db"]
        if snr < 10:
            flag("very_noisy", "audio", "fail", f"Background noise almost as loud as the voice ({snr:.0f} dB apart)")
        elif snr < 18:
            flag("noisy", "audio", "warn", f"Noticeable background noise ({snr:.0f} dB below the voice)")
    clipped = audio.get("clipped_fraction", 0)
    if clipped > 0.005:
        flag("distorted", "audio", "fail", "Audio distorts badly (overloaded)")
    elif clipped > 0.0005:
        flag("distorted", "audio", "warn", "Audio distorts in places")
    if audio.get("speech_confidence", 0) < -0.9:
        flag("unclear_speech", "audio", "warn", "Speech is hard to make out")

    # Picture.
    if picture:
        b = picture["brightness"]
        if b < 0.12:
            flag("too_dark", "light", "fail", "Too dark")
        elif b < 0.22:
            flag("dark", "light", "warn", "On the dark side")
        if b > 0.85:
            flag("overexposed", "light", "fail", "Overexposed")
        elif picture["blown_fraction"] > 0.25:
            flag("blown", "light", "warn", "Bright areas blown out")
        if picture["contrast"] < 0.18 and 0.12 <= b <= 0.85:
            flag("flat", "light", "warn", "Picture looks flat or hazy")

    # Format.
    short = min(meta.get("width") or 0, meta.get("height") or 0)
    if short and short < 720:
        flag("very_low_res", "format", "fail", f"Low resolution ({meta['width']}x{meta['height']})")
    elif short and short < 1080:
        flag("low_res", "format", "warn", f"Below 1080p ({meta['width']}x{meta['height']})")
    if meta.get("fps") and meta["fps"] < 23.5:
        flag("low_fps", "format", "warn", f"Low frame rate ({meta['fps']:.0f} fps)")
    if meta.get("orientation") == "horizontal":
        flag("horizontal", "format", "warn", "Horizontal: a Reel keeps only the middle")
    elif meta.get("orientation") == "square":
        flag("square", "format", "warn", "Square, not vertical")
    if meta.get("hdr"):
        flag("hdr", "format", "warn", "Filmed in HDR: colours may look washed out next to other cameras")
    if meta.get("duration") and meta["duration"] < 2:
        flag("very_short", "format", "warn", f"Very short ({meta['duration']:.1f}s)")

    # Claude's eyes.
    for name in _CLAUDE_CHECKS:
        c = (checks or {}).get(name)
        if c and c.get("rating") in ("fix", "retake"):
            flag(name, "picture" if name != "lighting" else "light",
                 "fail" if c["rating"] == "retake" else "warn", c.get("note") or name.capitalize())

    verdict = RETAKE if any(f["level"] == "fail" for f in flags) else CHECK if flags else GOOD
    return {"verdict": verdict, "flags": flags, "audio": audio, "picture": picture,
            "judged": bool(checks)}


def check_clip(video, meta: dict, transcript: dict | None, samples: np.ndarray | None,
               checks: dict | None = None) -> dict:
    """Everything for one clip. samples: its 16 kHz mono audio, if it has any."""
    audio = {}
    if samples is not None:
        audio = measure_audio(samples, (transcript or {}).get("words", []), (transcript or {}).get("segments"))
    try:
        picture = measure_picture(video, meta)
    except (subprocess.SubprocessError, OSError, ValueError):
        picture = {}
    return assess(meta, audio, picture, checks)


def recheck(row, checks: dict | None = None) -> dict:
    """Re-measure a clip already in the library (worker.py --check-quality)."""
    import json
    import tempfile
    from pathlib import Path

    import transcribe
    video, clip_dir = Path(row["video_path"]), Path(row["clip_dir"])
    meta = extract.probe(video)
    transcript_path = clip_dir / "transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else None
    samples = None
    if meta["has_audio"]:
        with tempfile.TemporaryDirectory(prefix="studio-quality-") as tmp:
            wav = extract.extract_audio(video, Path(tmp) / "audio.wav")
            samples = transcribe._load_wav(wav)
    if checks is None:
        analysis_path = clip_dir / "analysis.json"
        if analysis_path.exists():
            checks = json.loads(analysis_path.read_text(encoding="utf-8")).get("checks")
    return check_clip(video, meta, transcript, samples, checks)
