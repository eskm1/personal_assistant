"""
Colour grades for a carousel's photos (Bryan's team, 2026-10-31: the long condo's photos "could be warmer", like the
interior photographers they follow; "most i'd pick the middle, some right, 1-2 left"). A grade is the kind of change
Lightroom makes, never a picture redrawn: a tone curve on lightness and warm offsets by brightness, in CIE Lab, plus
for the two dramatic looks local contrast (clarity) and a soft vignette. Measured against five of Marcus Ip's photos:
his mid-tones are about three times as warm as ours (b* 18 against 6), his whites cream, not white (b* 6), and his
photos about 15% darker with deeper shadows.

- original: the photo as it came.
- soft: cream whites, gently warmer, the same brightness.
- golden: clearly warmer, a little darker and richer.
- marcus: the closest to his numbers (the team's pick for most photos).
- dramatic, deeper: darker rooms with the windows kept bright, the edges a little darker; a photo already warm from
  its lights gets less extra warmth (it would turn orange).

grade_file(src, look) -> Studio's photo for the slide (slide_images.save_photo). The looks were picked on the long
condo's photos side by side (https://claude.ai/artifact/6Wqup39GdvzxH8aaLAHjTr); the numbers here are the ones drawn
there, so what the server makes is what was picked.
"""
import tempfile
from pathlib import Path

import numpy as np

import settings
import slide_images

NAMES = {"original": "Original", "soft": "Soft warm", "golden": "Golden", "marcus": "Marcus match",
         "dramatic": "Dramatic", "deeper": "Dramatic, deeper"}

_N = [0, 25, 50, 75, 90, 100]   # lightness nodes the looks are given at
# look: (lightness out at _N, a* offsets, b* offsets, chroma gain, clarity, vignette, eased on warm photos)
LOOKS = {
    "soft": ([0, 25, 50, 75, 89, 97], [0, .5, 1, 1, .7, .3], [1, 3, 6, 6, 5, 3], 1.04, 0, 0, False),
    "golden": ([0, 23, 47.5, 72.5, 87, 94.5], [.5, 1.5, 2.5, 2, 1.2, .5], [2, 6, 10, 9, 7, 4.5], 1.08, 0, 0, False),
    "marcus": ([0, 21, 44, 69, 85, 93], [0, 2.5, 2, 1, .6, .4], [.5, 8.5, 11, 8, 6, 4.5], 1.10, 0, 0, False),
    "dramatic": ([0, 15, 35, 60, 83, 94], [0, 2.5, 2.5, 1.8, 1, .4], [1, 9, 13, 13, 10, 6], 1.08, .35, .18, True),
    "deeper": ([0, 11, 29, 54, 80, 93], [0, 3, 3, 2, 1.2, .5], [1, 10, 14, 14, 11, 6.5], 1.08, .45, .26, True),
}

# sRGB (D65) <-> CIE Lab
_M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]], np.float32)
_MI = np.linalg.inv(_M).astype(np.float32)
_WP = np.array([0.95047, 1.0, 1.08883], np.float32)


def _lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _gam(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def _f(t):
    return np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)


def _fi(t):
    return np.where(t > 0.206893, t ** 3, (t - 16 / 116) / 7.787)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    xyz = _lin(rgb) @ _M.T / _WP
    fx, fy, fz = (_f(xyz[..., i]) for i in range(3))
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], -1).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    xyz = np.stack([_fi(fy + lab[..., 1] / 500), _fi(fy), _fi(fy - lab[..., 2] / 200)], -1) * _WP
    return _gam(xyz @ _MI.T).astype(np.float32)


def _blur(light: np.ndarray) -> np.ndarray:
    """The lightness blurred wide (about 1/45 of the width): what clarity lifts detail against."""
    from PIL import Image, ImageFilter
    h, w = light.shape
    small = Image.fromarray(np.clip(light * 2.55, 0, 255).astype(np.uint8), "L").resize(
        (max(1, w // 8), max(1, h // 8)), Image.BILINEAR)
    return np.asarray(small.filter(ImageFilter.GaussianBlur(w / 360)).resize((w, h), Image.BICUBIC), np.float32) / 2.55


def grade(rgb: np.ndarray, look: str) -> np.ndarray:
    """rgb: float32 0-1, (h, w, 3). The photo in that look (original: unchanged)."""
    if look == "original":
        return rgb
    out_l, a_off, b_off, gain, clarity, vignette, eased = LOOKS[look]
    lab = rgb_to_lab(rgb)
    light = lab[..., 0]
    ease = 1.0
    if eased:  # already warm from its lights (b* of the mid-tones over ~6): less added
        mids = (light > 40) & (light < 80)
        ease = float(np.clip((14 - lab[..., 2][mids].mean()) / 8, .6, 1)) if mids.any() else 1.0
    tone = light + clarity * (light - _blur(light)) if clarity else light
    tone = np.interp(tone, _N, out_l)
    if vignette:
        h, w = light.shape
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        tone = tone * (1 - vignette * (((xx / w - .5) ** 2 + (yy / h - .5) ** 2) / .5) ** 1.3)
    out = np.stack([tone, lab[..., 1] * gain + ease * np.interp(light, _N, a_off),
                    lab[..., 2] * gain + ease * np.interp(light, _N, b_off)], -1)
    return lab_to_rgb(out)


def grade_file(src: Path, look: str) -> dict:
    """Studio's photo src (a JPEG in Studio/Photos) graded in that look, kept as a new photo:
    {id, path, width, height}. Raises ValueError for a look Studio doesn't have."""
    from PIL import Image
    if look not in LOOKS:
        raise ValueError(f"there's no look called {look!r}")
    with Image.open(src) as img:
        rgb = np.asarray(img.convert("RGB"), np.float32) / 255
    graded = grade(rgb, look)
    # Half a step of noise so smooth walls don't band in 8 bits; kept lossless until Studio keeps it as its JPEG.
    noise = np.random.default_rng(0).uniform(-.5, .5, graded.shape)
    pixels = np.clip(graded * 255 + noise, 0, 255).astype(np.uint8)
    with tempfile.TemporaryDirectory(prefix="grade-", dir=settings.ROOT) as tmp:
        out = Path(tmp) / "graded.png"
        Image.fromarray(pixels).save(out)
        return slide_images.save_photo(out)
