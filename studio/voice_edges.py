"""
Where the voice really starts and stops, from the clip's own sound (not only Whisper's timings).

Whisper's word times are close but not exact: a word's end is often 60-200 ms before its sound
dies away (a trailing consonant, the decay of "comfortable"), so a cut on Whisper's end can clip
the word. Each clip gets a loudness envelope (10 ms steps of the voice band, 80 Hz to 7.5 kHz so
an "s" or "f" counts as voice), worked out once and kept in the clip's folder (voice.npz, like
faces.json), with its noise floor and speech level. The editor asks it:

  - end_point: after a word, where the voice really falls to the floor (and stays there SUSTAIN),
    plus a breath; never past the next word's real start.
  - start_point: before a word, where its sound really starts, minus a breath; never before the
    previous word's real end.
  - clipped: is the voice still going in the last moments before a cut (a clipped word)?

Nothing here ever moves a cut into a word: when the voice runs straight on from one word into the
next, the cut goes in the quietest moment between them.
"""
import logging
import subprocess
from pathlib import Path

import numpy as np

import extract
import settings

log = logging.getLogger("studio.voice")

VERSION = 1
HOP = 0.01           # seconds per step of the envelope
RATE = 16000
SUSTAIN = 0.06       # the voice has stopped once it stays at the floor this long
ONSET_QUIET = 0.02   # ... and a word starts after at least this much quiet before it
BREATH_OUT = 0.05    # air kept after the voice stops
BREATH_IN = 0.04     # air kept before the voice starts
CLIP_WINDOW = 0.04   # the clipped-word check: the last this-many seconds before a cut
# How far past Whisper's end a word's sound is looked for (its ends run up to ~0.2 s early), and
# how far before its start: past that it's noise, not the word, and the cut stays near Whisper's.
MAX_TAIL, MAX_HEAD = 0.6, 0.4
# A short quiet (a stop consonant's closure) followed by a short burst, both before the next
# word's real start, is the end of the same word ("i-t", "ma-ke"), not a new sound.
TAIL_QUIET, TAIL_BURST = 0.12, 0.15
_CACHE: dict = {}
_CACHE_MAX = 48


class Voice:
    """A clip's loudness envelope (dB per HOP) and its levels."""

    def __init__(self, db: np.ndarray):
        self.db = np.maximum(db.astype(np.float32), -100.0)
        self.duration = len(self.db) * HOP
        heard = self.db[self.db > -95.0]  # digital silence (padding) isn't the room's floor
        if len(heard) < 20:
            self.floor, self.speech = -100.0, -100.0
        else:
            self.floor = float(np.percentile(heard, 10))
            self.speech = float(np.percentile(heard, 95))
        span = self.speech - self.floor
        self.usable = span >= 8.0  # no clear speech above the floor: fall back to Whisper's times
        # "Quiet": back at the floor (a little above it, so room tone and hiss count as quiet).
        self.quiet_db = self.floor + min(max(0.2 * span, 4.0), 9.0)
        # "Talking": clearly above the floor; a cut never lands while the voice is this loud.
        self.active_db = self.floor + min(max(0.45 * span, 8.0), 24.0)
        self.quiet = self.db < self.quiet_db

    def i(self, t: float) -> int:
        return min(max(int(round(t / HOP)), 0), len(self.db))

    def t(self, i: int) -> float:
        return round(i * HOP, 3)

    def level(self, a: float, b: float) -> float:
        """Mean loudness (dB of the mean power) between a and b."""
        i, j = self.i(a), max(self.i(b), self.i(a) + 1)
        seg = self.db[i:j]
        if not len(seg):
            return -100.0
        return float(10 * np.log10(np.mean(10 ** (seg / 10.0)) + 1e-12))

    def active(self, a: float, b: float) -> bool:
        return self.level(a, b) > self.active_db

    def valley(self, a: float, b: float) -> float:
        """The middle of the quietest 30 ms between a and b (a when the range is empty)."""
        i, j = self.i(a), self.i(b)
        if j - i < 1:
            return round(a, 3)
        seg = self.db[i:j]
        if len(seg) >= 3:
            seg = np.convolve(seg, np.ones(3) / 3, mode="same")
        return self.t(i + int(np.argmin(seg)))

    def quiet_from(self, t: float, hi: float) -> float | None:
        """Where the voice sounding at t (Whisper's end of a word) really stops: the start of the
        first stretch of SUSTAIN at the floor at or after t, before hi (the next word's real start,
        when there is one; a quiet that runs into hi counts however short). A short burst after a
        short quiet, ending by hi, still belongs to this word (a stop consonant's release). None
        when the voice runs on to hi."""
        q, n = self.quiet, len(self.quiet)
        i, ihi = self.i(t), self.i(hi)
        sus = max(1, int(round(SUSTAIN / HOP)))
        while i < ihi:
            if not q[i]:
                i += 1
                continue
            j = i
            while j < n and q[j]:
                j += 1
            if j - i >= sus or j >= ihi:
                if j - i < TAIL_QUIET / HOP and j < ihi:  # a closure, then the word's last consonant?
                    k = j
                    while k < n and not q[k]:
                        k += 1
                    if (k - j) * HOP <= TAIL_BURST and k <= ihi:
                        i = k
                        continue
                return self.t(i)
            i = j
        return None

    def sound_from(self, t: float, lo: float) -> float | None:
        """Where the word Whisper starts at t really starts: walking back from t while the voice
        sounds, to the end of the first quiet of at least ONSET_QUIET (never before lo). When it's quiet at
        t (Whisper's start is early), the first sound in the next 0.2 s (else t). None when the
        voice runs straight back to lo."""
        q = self.quiet
        i, ilo = min(self.i(t), len(q) - 1), self.i(min(lo, t))
        if i < 0:
            return None
        if q[i]:
            k = i
            while k < min(len(q), i + int(0.2 / HOP)) and q[k]:
                k += 1
            return self.t(k) if k < len(q) and not q[k] else round(t, 3)
        need = max(1, int(round(ONSET_QUIET / HOP)))
        while True:
            j = i
            while j > ilo and not q[j - 1]:
                j -= 1
            if j <= ilo:
                return None
            k = j
            while k > ilo and q[k - 1]:
                k -= 1
            if j - k >= need or k <= ilo:
                return self.t(j)
            i = k - 1  # a dip of a frame inside the sound: keep walking back


def _cache_file(clip: dict) -> Path | None:
    d = clip.get("clip_dir")
    return Path(d) / "voice.npz" if d else None


def _key(clip: dict) -> str:
    path = Path(clip.get("video_path") or "")
    size = path.stat().st_size if path.is_file() else 0
    return f"{VERSION}:{path.name}:{size}"


def _measure(video: Path) -> np.ndarray:
    """The envelope: 16 kHz mono of the voice band, RMS per HOP, in dB."""
    cmd = [extract.resolve(settings.FFMPEG), "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(RATE),
           "-af", "highpass=f=80,lowpass=f=7500", "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, creationflags=getattr(extract, "_NO_WINDOW", 0))
    if out.returncode != 0:
        raise RuntimeError((out.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "ffmpeg failed")
    x = np.frombuffer(out.stdout, dtype="<f4")
    step = int(RATE * HOP)
    n = len(x) // step
    if not n:
        return np.zeros(0, dtype=np.float32)
    rms = np.sqrt(np.mean(np.square(x[:n * step].reshape(n, step), dtype=np.float64), axis=1))
    return (20 * np.log10(rms + 1e-9)).astype(np.float32)


def voice(clip: dict) -> Voice | None:
    """The clip's Voice, worked out once (kept in its folder as voice.npz); None when the clip has
    no sound, no file, or no clear speech above its floor (callers then use Whisper's times)."""
    if not clip or not clip.get("clip_dir") or ("has_audio" in clip and not clip.get("has_audio")):
        return None
    video = Path(clip.get("video_path") or "")
    mem = (clip.get("id"), str(video), clip.get("clip_dir"))
    if mem in _CACHE:
        return _CACHE[mem]
    found, key, path = None, None, _cache_file(clip)
    try:
        key = _key(clip)
        with np.load(path, allow_pickle=False) as data:
            # A missing original (freed to OneDrive) keeps its envelope: the file can't have changed.
            if str(data["key"]) == key or (not video.is_file() and str(data["key"]).startswith(f"{VERSION}:")):
                found = data["db"]
    except (OSError, KeyError, ValueError):
        pass
    if found is None:
        if not video.is_file():
            _CACHE[mem] = None
            return None
        try:
            found = _measure(video)
            try:
                with open(path, "wb") as f:
                    np.savez_compressed(f, db=found.astype(np.float16), key=np.array(key))
            except OSError as e:
                log.warning("Couldn't keep the voice envelope of clip %s: %s", clip.get("id"), e)
        except Exception as e:  # no sound track, unreadable file: Whisper's times stand
            log.info("No voice envelope for clip %s: %s", clip.get("id"), e)
            found = None
    v = Voice(found) if found is not None and len(found) else None
    v = v if v is not None and v.usable else None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[mem] = v
    return v


def end_point(v: Voice | None, word_end: float, base: float, next_start: float | None = None,
              limit: float | None = None) -> float:
    """A cut after a word that (by Whisper) ends at word_end. base: the cut Whisper's times gave
    (word end plus air). Moved on to where the voice really stops plus BREATH_OUT; never back from
    base unless the next word really starts sooner, never past the next word's real start
    (next_start: Whisper's start of it) or limit."""
    if v is None:
        return base
    limit = v.duration if limit is None else min(limit, v.duration)
    cap = limit
    if next_start is not None:
        onset = v.sound_from(next_start, lo=word_end)
        cap = min(cap, onset - 0.01 if onset is not None else v.valley(word_end, max(word_end, next_start)))
    hi = max(min(cap, word_end + MAX_TAIL), word_end)
    off = v.quiet_from(word_end, hi)
    # No quiet before the next word (or for MAX_TAIL): the quietest moment in between.
    want = off + BREATH_OUT if off is not None else v.valley(word_end, hi) if hi > word_end else hi
    end = min(max(base, want), cap)
    if end < word_end and next_start is not None:  # the next word is already sounding: the quietest moment
        end = v.valley(min(word_end, next_start), max(word_end, next_start))
    return round(end, 3)


def start_point(v: Voice | None, word_start: float, base: float, prev_end: float | None = None,
                limit: float | None = None) -> float:
    """A cut before a word that (by Whisper) starts at word_start. base: the cut Whisper's times
    gave. Moved back to where its sound really starts minus BREATH_IN, never forward from base,
    never before the previous word's real end (prev_end: Whisper's end of it) or limit."""
    if v is None:
        return base
    limit = 0.0 if limit is None else max(limit, 0.0)
    floor = limit
    lo = min(max(limit, prev_end if prev_end is not None else limit, word_start - MAX_HEAD), word_start)
    onset = v.sound_from(word_start, lo=lo)
    if prev_end is not None:
        hi = max(prev_end, min(onset if onset is not None else word_start, prev_end + MAX_TAIL))
        off = v.quiet_from(prev_end, hi)
        floor = max(floor, off + 0.01 if off is not None else v.valley(min(prev_end, hi), hi))
    # No quiet before the word (for MAX_HEAD): the quietest moment there.
    want = onset - BREATH_IN if onset is not None else max(floor, v.valley(lo, word_start))
    return round(max(min(base, want), floor), 3)


def clipped(v: Voice | None, end: float) -> bool:
    """Is the voice still going right up to a cut at `end` (a clipped word)? Loud over the last
    CLIP_WINDOW, and not back at the floor in the last step before the cut (a cut in the quiet
    between two close words is fine even when the word before ended 20 ms earlier)."""
    if v is None or end - CLIP_WINDOW <= 0 or not v.active(end - CLIP_WINDOW, end):
        return False
    i = v.i(end) - 1
    return 0 <= i < len(v.quiet) and not v.quiet[i]
