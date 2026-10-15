"""
ffmpeg helpers: read a clip's metadata, pull its audio for Whisper, and grab
still frames for Claude to look at.

Every call goes through _run so ffmpeg never flashes a console window when the
worker runs headless under pythonw on Windows.
"""
import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from settings import FFMPEG, FFPROBE, FRAME_EVERY_SECONDS, FRAME_WIDTH, MAX_FRAMES

# Without this, each ffmpeg call from pythonw pops up (and closes) a black window.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@lru_cache(maxsize=None)
def resolve(binary: str) -> str:
    """Full path of the real executable. winget installs ffmpeg as a link in
    WinGet\\Links; launching the link's target directly is what Windows expects."""
    found = shutil.which(binary)
    return os.path.realpath(found) if found else binary


def _run(args: list[str]) -> subprocess.CompletedProcess:
    if not args[0]:
        raise RuntimeError("ffmpeg/ffprobe path is empty; check FFMPEG_PATH / FFPROBE_PATH in .env")
    cmd = [resolve(args[0]), *args[1:]]
    # UTF-8 explicitly: Windows would otherwise decode ffmpeg's output with the
    # legacy code page and choke on non-ASCII metadata or file names.
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {' | '.join(tail)}")
    return result


def tool_versions() -> dict[str, str]:
    """First line of `ffmpeg -version` / `ffprobe -version` and where it was found,
    for the setup check."""
    return {
        Path(binary).name: f"{_run([binary, '-version']).stdout.splitlines()[0][:40]} ({resolve(binary)})"
        for binary in (FFMPEG, FFPROBE)
    }


def probe(path: Path) -> dict:
    """Duration, display size, orientation, audio presence and recording time."""
    raw = json.loads(_run([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout)
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("no video stream")

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    # Phones store portrait video as landscape pixels plus a rotation flag.
    rotation = int(video.get("tags", {}).get("rotate", 0))
    for side in video.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) % 180 == 90:
        width, height = height, width

    tags = raw.get("format", {}).get("tags", {})
    recorded = (
        tags.get("com.apple.quicktime.creationdate")
        or tags.get("creation_time")
        or video.get("tags", {}).get("creation_time")
    )
    return {
        "duration": float(raw.get("format", {}).get("duration") or video.get("duration") or 0),
        "width": width,
        "height": height,
        "orientation": "vertical" if height > width else "horizontal" if width > height else "square",
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "recorded_at": recorded,
    }


def extract_audio(path: Path, wav_path: Path) -> Path:
    """16 kHz mono WAV — the format Whisper works in natively."""
    _run([FFMPEG, "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "pcm_s16le", str(wav_path)])
    return wav_path


def make_preview(path: Path, out_path: Path) -> Path:
    """A small H.264 copy (960 px on the long side) for the dashboard: plays in any
    browser, seeks instantly, and streams fine over Wi-Fi or Tailscale, unlike
    a 4K HEVC original."""
    _run([FFMPEG, "-y", "-v", "error", "-i", str(path),
          "-vf", "scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)'",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out_path)])
    return out_path


def extract_frames(path: Path, out_dir: Path, duration: float) -> list[tuple[float, Path]]:
    """Evenly spaced stills across the clip: one per FRAME_EVERY_SECONDS, at most
    MAX_FRAMES. Returns (timestamp_seconds, jpg_path) pairs in time order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, min(MAX_FRAMES, int(duration // FRAME_EVERY_SECONDS) + 1))
    frames = []
    for i in range(count):
        # Centre of each slice, so the first frame isn't a black fade-in.
        t = duration * (i + 0.5) / count if duration > 0 else 0.0
        jpg = out_dir / f"frame_{i:02d}_{t:07.2f}s.jpg"
        # -ss before -i seeks fast; ffmpeg applies the phone's rotation itself.
        _run([FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
              "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "4", str(jpg)])
        if jpg.exists():
            frames.append((round(t, 2), jpg))
    return frames
