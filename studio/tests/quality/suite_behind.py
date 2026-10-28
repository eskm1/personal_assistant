"""Words behind Bryan (behind.py; Bryan, 2026-10-28: "Have words pop up behind my head together in the background
(making it appear that I'm in front of the words)").

Synthetic footage made at run time: a textured wall and a drawn person (shirt, neck, hair) carrying the real face of
fixtures/person.jpg (so OpenCV's face finder sees a face), swaying a little; two people; nobody; a light wall; a
close-up; a panning camera. The person finder is a fake for most checks (background subtraction against the known wall,
or the true masks the media was drawn with), so every pixel check knows exactly where he is; and the real one
(MediaPipe's selfie multiclass model on LiteRT, fetched once from Google's model store and checked) on a real-looking
mid-shot made from the fixture photo. Claude, music and the Whisper re-check stubbed in the drafts; everything else real:
the placement, the matte, the layer, the one encode, the watch-through, the patches.
Run: python tests/quality/suite_behind.py (from studio/). STUDIO_BEHIND_STRIP=<folder> saves frames and short MP4s.
The real model needs storage.googleapis.com once (kept in ~/.cache/studio-quality after)."""
import json, os, re, shutil, subprocess, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, FIXTURES, ok, finish
import media

t_media = time.time()
M = media.ending_speech(P / "media")
DATA = json.loads((M / "words.json").read_text())
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0")
import numpy as np
import cv2
from PIL import Image
import settings, catalog, extract, editor, transcribe, music, anthropic, behind, fix_actions, final_check
import draft_review, framing, updater, help_desk

settings.ensure_folders()
conn = catalog.connect()
W, H, FPS = editor.W, editor.H, editor.FPS
STRIP = Path(os.environ["STUDIO_BEHIND_STRIP"]) if os.environ.get("STUDIO_BEHIND_STRIP") else None
if STRIP:
    STRIP.mkdir(parents=True, exist_ok=True)

# ── The footage ───────────────────────────────────────────────────────────────
PHOTO = np.asarray(Image.open(FIXTURES / "person.jpg").convert("RGB"))
FACE = cv2.resize(PHOTO[24:106, 38:114], (250, 270), interpolation=cv2.INTER_CUBIC)
RNG = np.random.default_rng(7)


def wall(light=False, width=W):
    """A painted board wall (grain, boards, a skirting), still."""
    img = np.ones((H, width, 3), np.float32) * np.array([236, 233, 226] if light else [150, 142, 132], np.float32)
    img += RNG.normal(0, 5, (H, width, 1))
    for x in range(60, width, 130):
        img[:, x:x + 4] *= 0.86
    img[1650:] *= 0.8
    return img.clip(0, 255).astype(np.uint8)


def figure(frame, cx, hy, s=1.0):
    """A person on the frame: shirt, neck, hair, and the fixture's face. Returns where he is (a bool mask)."""
    mask = np.zeros(frame.shape[:2], np.uint8)
    pts = np.array([[cx - 330 * s, H], [cx - 320 * s, hy + 340 * s], [cx - 210 * s, hy + 245 * s],
                    [cx + 210 * s, hy + 245 * s], [cx + 320 * s, hy + 340 * s], [cx + 330 * s, H]], np.int32)
    for img, cols in ((frame, [(40, 92, 112), (196, 150, 122), (45, 36, 30)]), (mask, [1, 1, 1])):
        cv2.fillPoly(img, [pts], cols[0])
        cv2.rectangle(img, (int(cx - 60 * s), int(hy + 120 * s)), (int(cx + 60 * s), int(hy + 260 * s)), cols[1], -1)
        cv2.ellipse(img, (int(cx), int(hy - 30 * s)), (int(140 * s), int(175 * s)), 0, 0, 360, cols[2], -1)
    face = cv2.resize(FACE, (int(250 * s), int(270 * s)), interpolation=cv2.INTER_CUBIC)
    fh, fw = face.shape[:2]
    em = np.zeros((fh, fw), np.float32)
    cv2.ellipse(em, (fw // 2, fh // 2), (int(fw * 0.47), int(fh * 0.49)), 0, 0, 360, 1.0, -1)
    em = cv2.GaussianBlur(em, (0, 0), 6)[..., None]
    y0, x0 = int(hy - fh / 2), int(cx - fw / 2)
    region = frame[y0:y0 + fh, x0:x0 + fw].astype(np.float32)
    frame[y0:y0 + fh, x0:x0 + fw] = (region * (1 - em) + face * em).astype(np.uint8)
    return mask.astype(bool)


def sway(i):
    return 540 + 10 * np.sin(i / 30 * 1.7), 700 + 3 * np.sin(i / 30 * 2.3)


def write_clip(path, frames_fn, n, audio=None, keep=True):
    """n frames from frames_fn(i) -> RGB, as H.264 (with the audio when given). Returns the masks it gave back (keep)."""
    args = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio:
        args += ["-i", str(audio), "-c:a", "aac", "-ar", "48000"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-t", f"{n / FPS:.3f}", str(path)]
    p = subprocess.Popen(args, stdin=subprocess.PIPE)
    masks = []
    for i in range(n):
        frame, mask = frames_fn(i)
        if keep:
            masks.append(mask)
        p.stdin.write(np.ascontiguousarray(frame).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return masks


WALL, LIGHT = wall(), wall(light=True)
PAN = wall(width=W + 900)


def one(i, back=None):
    f = (WALL if back is None else back).copy()
    cx, hy = sway(i)
    return f, figure(f, int(cx), int(hy))


def two(i):
    f = WALL.copy()
    a = figure(f, 300, 760, 0.7)
    b = figure(f, 790, 740, 0.7)
    return f, a | b


def nobody(i):
    return WALL.copy(), np.zeros((H, W), bool)


def close(i):
    f = WALL.copy()
    cx, hy = sway(i)
    return f, figure(f, int(cx), int(hy) - 60, 3.0)


def panning(i):
    f = PAN[:, i * 14:i * 14 + W].copy()
    return f, figure(f, 540, 700)


N = 75  # 2.5 s
CLIPS = {}
for name, fn in (("one", one), ("two", two), ("nobody", nobody), ("light", lambda i: one(i, LIGHT)), ("close", close),
                 ("pan", panning)):
    path = P / "media" / f"{name}.mp4"
    CLIPS[name] = (path, write_clip(path, fn, N, keep=name not in ("nobody", "light")))
TALK = P / "media" / "talk_figure.mp4"
write_clip(TALK, one, int(DATA["total"] * FPS), audio=M / "talk.wav", keep=False)
print(f"(media made in {time.time() - t_media:.0f}s)")


# ── Person finders for the tests ──────────────────────────────────────────────
class BgSub(behind.Segmenter):
    """The person is whatever differs from the known wall (a still camera, no reframing)."""
    name = "test-wall"

    def __init__(self, back):
        self.back, self.sized = back, {}
        self.name = f"test-wall-{float(back.mean()):.2f}"  # a kept layer is the finder's own

    def person(self, rgb):
        h, w = rgb.shape[:2]
        if (w, h) not in self.sized:  # the wall at the size the stretch is read at
            self.sized[(w, h)] = cv2.resize(self.back, (w, h), interpolation=cv2.INTER_AREA).astype(np.int16)
        m = (np.abs(rgb.astype(np.int16) - self.sized[(w, h)]).sum(2) > 45).astype(np.uint8)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        return cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k).astype(np.float32), None


class Truth(behind.Segmenter):
    """The masks the footage was drawn with, in order; jitter: each one grown or shrunk a few pixels at random;
    flaky: every other one shrunk by half (a finder that can't make up its mind)."""
    name = "test-truth"

    def __init__(self, masks, jitter=0, flaky=False):
        self.masks, self.i, self.jitter, self.flaky = masks, 0, jitter, flaky
        self.rng = np.random.default_rng(3)
        self.given = []

    def person(self, rgb):
        m = self.masks[min(self.i, len(self.masks) - 1)].astype(np.uint8)
        self.i += 1
        if self.jitter:
            r = int(self.rng.integers(-self.jitter, self.jitter + 1))
            if r:
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(r) + 1, 2 * abs(r) + 1))
                m = cv2.dilate(m, k) if r > 0 else cv2.erode(m, k)
        if self.flaky and self.i % 2:
            m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_RECT, (121, 121)))
        self.given.append(m.astype(bool))
        return m.astype(np.float32), None


def read_rgb(video, t, n=1):
    return behind.read_frames(video, t, n)


def layer_rgba(file):
    """A layer's frames as RGBA (full size)."""
    rgb = subprocess.run(["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(file), "-f", "rawvideo", "-pix_fmt",
                          "rgba", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(rgb, np.uint8).reshape(-1, H, W, 4)


def over(video, file, t0=0.0):
    """The footage with the layer laid over it the way the mix does (overlay, format auto), as RGB frames (no lossy
    encode in between)."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-c:v", "libvpx-vp9", "-i", str(file),
                          "-filter_complex", f"[1:v]setpts=PTS-STARTPTS+{t0:.3f}/TB[s];[0:v][s]overlay=0:0:format=auto:eof_action=pass[v]",
                          "-map", "[v]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, H, W, 3)


LOOK = {"titles": "site", "caption_font": "archivo"}
CODEC = 2  # alpha (of 255) the VP9 encode may leave where the layer is clear: no pixel moves a level

# ── 1. The plan, the schema, the setting ──────────────────────────────────────
print("\n== 1. The plan offers words behind him")
ok(behind.available() is None, "available: the person finder is installed (ai-edge-litert)")
sch = editor._schema(False)
ok("behind" in sch["properties"]["graphics"]["items"]["properties"]["style"]["enum"]
   and "behind" not in editor.EDL_SCHEMA["properties"]["graphics"]["items"]["properties"]["style"]["enum"],
   "the edit plan's schema offers the style \"behind\" (on a copy, the base schema untouched)")
r = behind.rule()
ok('style "behind"' in r and "At most 2 per Reel" in r and 'role "hook"' in r and "1 to 4 words" in r,
   "the prompt's rule: the hook or a key line, at most 2, the hook's role, a few words")
settings.EDIT_BEHIND = False
ok(behind.available() and behind.rule() == "" and "behind" not in editor._schema(False)["properties"]["graphics"]["items"]["properties"]["style"]["enum"],
   "STUDIO_EDIT_BEHIND off: no rule, no style")
settings.EDIT_BEHIND = True
ok(settings.EDIT_BEHIND_MAX == 2, "two a Reel by default")
edl = {"graphics": [{"style": "card", "title": "A card"},
                    {"style": "behind", "title": "Pour a level screed over the whole room", "italic": "screed",
                     "clip_id": 1, "at": 3.0, "duration": 2, "role": "explain", "why": "*key* line"}]}
behind.take(edl, {})
ok([g["style"] for g in edl["graphics"]] == ["card"] and edl["behind_plan"][0]["words"] == "Pour a level screed"
   and edl["behind_plan"][0]["italic"] == "screed" and edl["behind_plan"][0]["why"] == "key line",
   f"take(): out of the graphics into behind_plan, at most 4 words ({edl['behind_plan'][0]['words']!r})")
ok(behind.words_of("Waterproofing membranes everywhere") == "Waterproofing membranes", "at most 28 characters, whole words")
edl2 = {"graphics": [{"style": "behind", "title": "Level", "clip_id": 1, "at": 1.0, "duration": 2, "role": "hook"}]}
behind.take(edl2, {"behind_off": True})
ok(edl2["behind_off"] and not edl2.get("behind_plan"), "\"no words behind me\" from before holds: the plan's are dropped")

# ── 2. Bryan's notes ──────────────────────────────────────────────────────────
print("\n== 2. Notes: \"words behind me here\", \"no words behind me\"")
cases = {"words behind me here": ("add", ""), "Put 'Screed first' behind me": ("add", "Screed first"),
         "can you show the text behind my head here": ("add", ""), "no words behind me please": ("remove", None),
         "take the words behind me off": ("remove", None), "the picture is too dark here": (None, None),
         "move the plant behind the sofa": (None, None), "the mess that's behind me is distracting": (None, None),
         "no, put words behind me here": ("add", ""), "don't want the text behind my head": ("remove", None),
         "words behind me saying two days": ("add", "Two days")}
got = {k: behind.note_ask(k) for k in cases}
ok(all((g or {}).get("op") == v[0] and (v[1] is None or g["words"] == v[1]) for (k, v), g in zip(cases.items(), got.values())),
   "notes read: " + "; ".join(f"{k!r} -> {g}" for k, g in got.items()))

# ── 3. Placement on the draft's clock ─────────────────────────────────────────
print("\n== 3. Placement")
cuts = [{"clip_id": 7, "start": 0.0, "end": 6.0, "zoom": 1.0}, {"clip_id": 7, "start": 6.5, "end": 12.0, "zoom": 1.0},
        {"clip_id": 7, "start": 12.0, "end": 20.0, "zoom": 1.12}]
words = [{"word": f"w{i}", "start": i * 0.4 + 0.2, "end": i * 0.4 + 0.5} for i in range(48)]
items = [{"clip_id": 7, "at": 0.3, "duration": 2.6, "words": "Floors level again", "role": "hook"},
         {"clip_id": 7, "at": 7.0, "duration": 2.5, "words": "Two days", "role": "explain"},
         {"clip_id": 7, "at": 14.0, "duration": 2.5, "words": "Porcelain", "role": "explain"}]
placed, off = behind.place(items, cuts, 18.5, words, "Floors level again")
ok(len(placed) == 2 and placed[0]["start"] == 0.0 and placed[0]["role"] == "hook", f"the hook from the first frame "
   f"({[(p['start'], p['duration']) for p in placed]})")
ok(abs(placed[1]["start"] - 6.4) < 0.25 and behind.SHORTEST <= placed[1]["duration"] <= behind.LONGEST,
   "a key line timed to his words (clip 7.0 s is 6.5 s on the draft, snapped to the word before)")
ok(len(off) == 1 and "keeps to 2" in off[0]["why_not"], f"never more than 2 a Reel: {off[0]['why_not']!r}")
p2, o2 = behind.place(items[1:2], cuts, 18.5, words, "", busy=[{"start": 6.0, "duration": 3.0}])
ok(not p2 and "card" in o2[0]["why_not"], "not with a card up")
p3, o3 = behind.place(items[1:2], cuts, 18.5, words, "", window=[(5.0, 9.0)])
ok(not p3 and "window" in o3[0]["why_not"], "not in a voice-over's window")
p4, _ = behind.place([{**items[1], "at": 9.5, "duration": 4.0}], cuts, 18.5, words, "")
ok(p4 and p4[0]["start"] + p4[0]["duration"] <= 11.5 + 0.001 and p4[0]["duration"] >= behind.SHORTEST,
   f"it ends where the framing changes (the punch-in at 11.5 s): {p4 and (p4[0]['start'], p4[0]['duration'])}")
p5, o5 = behind.place([{**items[1], "at": 11.0}], cuts[:2] + [{"clip_id": 7, "start": 12.0, "end": 12.6}], 11.9, words, "")
ok(not p5 and o5, "too close to the close: shown on top instead")
ok(all(abs(p["start"] * FPS - round(p["start"] * FPS)) < 1e-6 for p in placed), "starts on the frame grid")
callouts, hook, notes = behind.on_top([{**placed[0], "why_not": "x"}, {**placed[1], "why_not": "y"}], [], 18.5)
ok(hook == "Floors level again" and len(callouts) == 1 and callouts[0]["style"] == "callout"
   and callouts[0]["title"] == "Two days" and len(notes) == 2, "on top instead: the hook as the hook, a key line as a callout")
callouts, _, notes = behind.on_top([{**placed[1], "why_not": "y"}], [{"start": placed[1]["start"], "duration": 2.0}], 18.5)
ok(not callouts and "no room" in notes[0], "a callout that would clash with a card is left out, and says so")

# ── 4. The pop and the layout ─────────────────────────────────────────────────
print("\n== 4. The pop, the size, the frame's edges")
s0, a0 = behind.pop(0.0, 2.5)
s1, a1 = behind.pop(behind.POP_IN, 2.5)
s2, a2 = behind.pop(2.5, 2.5)
ok(abs(s0 - behind.POP_FROM) < 1e-6 and a0 == 0.0 and abs(s1 - 1.0) < 1e-6 and a1 == 1.0 and a2 == 0.0,
   f"they pop up together: {s0:.2f} -> {s1:.2f} scale, 0 -> 1 opacity, gone at the end")
peak = max(behind.pop(t / 100, 2.5)[0] for t in range(250))
ok(peak <= behind.POP_OVER, f"the pop's overshoot ({peak:.3f}) stays inside what the layout allows for")
font = behind.display_font(LOOK)
ok(font.name == "Archivo-latin-800.woff2" and behind.display_font({"titles": "gallery"}).name.startswith("InstrumentSerif"),
   "the look's display face: Archivo in Site Notes, the serif in the gallery look")
pal = behind.palette("urban_makers")
ok(pal["accent"] == tuple(int(editor.THEME["urban_makers"]["brass"][i:i + 2], 16) for i in (1, 3, 5)),
   "the key word in the theme's brass")

# ── 5. A short clip, end to end, with a known person ──────────────────────────
print("\n== 5. Layer order on a short clip (he's where the footage drew him)")
one_clip, one_masks = CLIPS["one"]
behind.use(BgSub(WALL))
b = {"words": "Level floors first", "italic": "floors", "start": 0.0, "duration": N / FPS, "role": "explain"}
t0 = time.time()
done, failed, tally = behind.make([b], one_clip, LOOK, "urban_makers")
spent = time.time() - t0
ok(len(done) == 1 and not failed, f"made: {done[0]['check'] if done else failed}")
chk = done[0]["check"]
print(f"  {spent:.1f}s for {N / FPS:.1f}s of effect, {chk.get('cpu_per_second')} CPU s a second")
ok(chk["people"] == 1 and chk["steady"] > 0.9 and chk["camera_step"] < 2, "the confidence check passed: one person, steady, still")
layer = editor.INSERTS / f"{done[0]['file']}.webm"
rgba = layer_rgba(layer)
ok(len(rgba) == N, f"one layer frame for each picture frame ({len(rgba)})")
comp = over(one_clip, layer)
src = read_rgb(one_clip, 0.0, N)
k = 45  # 1.5 s: held
mask = one_masks[k]
core = cv2.erode(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19))).astype(bool)
x0, y0, x1, y1 = chk["words_at"]
inbox = np.zeros_like(mask)
inbox[y0:y1, x0:x1] = True
ink = rgba[k, ..., 3] > 240
bgw = ink & ~cv2.dilate(mask.astype(np.uint8), np.ones((19, 19), np.uint8)).astype(bool)
ok(bgw.sum() > 20000 and comp[k][bgw].mean() > 225 and src[k][bgw].mean() < 175,
   f"words visible in the background: {bgw.sum()} px bright ({comp[k][bgw].mean():.0f}) over the wall ({src[k][bgw].mean():.0f})")
him = core & inbox
diff = np.abs(comp[k].astype(int) - src[k].astype(int))[him]
ok(him.sum() > 3000 and diff.max() <= 3, f"his pixels inside the words' box unchanged ({him.sum()} px, most {diff.max()} levels): "
   "he stands in front of them")
solid_hits = max(int(rgba[i, ..., 3][cv2.erode(one_masks[i].astype(np.uint8), np.ones((19, 19), np.uint8)).astype(bool)].max())
                 for i in range(N))
# The layer's alpha goes through VP9 (lossy): a stray 1/255 here and there moves no pixel by even one level.
ok(solid_hits <= CODEC, f"no word on him in any frame (the layer is clear wherever he is solid: at most {solid_hits}/255)")
head = np.zeros_like(mask)
cx, hy = sway(k)
cv2.ellipse(head.view(np.uint8), (int(cx), int(hy)), (110, 125), 0, 0, 360, 1, -1)
ok(rgba[k, ..., 3][head].max() <= CODEC, "nothing over his face")
edge = (cv2.dilate(mask.astype(np.uint8), np.ones((7, 7), np.uint8)) - cv2.erode(mask.astype(np.uint8), np.ones((7, 7), np.uint8))).astype(bool)
soft = ((rgba[k, ..., 3] > 15) & (rgba[k, ..., 3] < 240) & edge & inbox).sum()
ok(soft > 150, f"a feathered edge round him ({soft} soft pixels where the words meet him)")
cols = np.nonzero((rgba[..., 3] > 128).any(axis=(0, 1)))[0]
rows = np.nonzero((rgba[..., 3] > 128).any(axis=(0, 2)))[0]
ok(cols.min() >= behind.SAFE_X - 8 and cols.max() <= W - behind.SAFE_X + 8 and rows.min() >= behind.SAFE_TOP - 40
   and rows.max() < editor.CAPTION_TOP, f"never cut off at the frame's edge, clear of the captions (x {cols.min()}-{cols.max()}, "
   f"y {rows.min()}-{rows.max()})")
ink_n = [(rgba[i, ..., 3] > 128).sum() for i in (0, 3, 6, 12, 45, N - 1)]
widths = [np.ptp(np.nonzero((rgba[i, ..., 3] > 128).any(0))[0]) if (rgba[i, ..., 3] > 128).any() else 0 for i in (3, 12)]
ok(ink_n[0] < ink_n[3] <= ink_n[4] * 1.05 and ink_n[-1] < ink_n[4] / 2 and widths[0] < widths[1],
   f"they pop up together (ink {ink_n}, width {widths}) and fade at the end")
ok(chk["dark"] is False and rgba[k, ..., :3][ink & bgw].mean() > 225, f"light words on the mid-grey wall "
   f"({rgba[k, ..., :3][ink & bgw].mean():.0f})")
t1 = time.time()
again, _, tally2 = behind.make([b], one_clip, LOOK, "urban_makers")
ok(tally2["reused"] == 1 and tally2["made"] == 0 and again[0]["file"] == done[0]["file"] and time.time() - t1 < spent / 2,
   "the same stretch again: the kept layer, nothing made")
ok(0 < chk.get("cpu_per_second", 0) < 60, f"its cost recorded: {chk.get('cpu_per_second')} CPU s a second of effect")
if STRIP:
    for i, kk in enumerate((2, 6, 12, 45, 72)):
        Image.fromarray(comp[kk]).save(STRIP / f"synthetic_{i}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                    "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(STRIP / "synthetic.mp4")],
                   input=comp.tobytes(), check=True)
del rgba, comp, src  # a few hundred MB each: the gate runs four suites at once

print("\n== 6. No flicker: the outline smoothed over time")
jit = Truth(one_masks, jitter=3)
behind.use(jit)
dj, _, _ = behind.make([{**b, "words": "Level floors"}], one_clip, LOOK, "urban_makers")
la = layer_rgba(editor.INSERTS / f"{dj[0]['file']}.webm")[..., 3].copy()  # uint8, 0..255
hold = range(int(behind.POP_IN * FPS) + 3, N - int(behind.FADE_OUT * FPS) - 3)
band = np.zeros((H, W), bool)
for i in hold:
    m8 = one_masks[i].astype(np.uint8)
    band |= (cv2.dilate(m8, np.ones((9, 9), np.uint8)) - cv2.erode(m8, np.ones((9, 9), np.uint8))).astype(bool)
sel = band & (la[list(hold)].max(0) > 127) & (la[list(hold)].min(0) < 242)
raw_flicker = float(np.mean([(jit.given[i][sel] != jit.given[i - 1][sel]).mean() for i in hold]))
out_flicker = float(np.mean([np.abs(la[i][sel].astype(np.float32) - la[i - 1][sel]).mean() / 255 for i in hold]))
del la, jit
ok(sel.sum() > 500 and out_flicker < raw_flicker * 0.5, f"a finder whose outline jumps 3 px a frame: the words' edge "
   f"moves {out_flicker:.3f} a frame against {raw_flicker:.3f} (smoothed over {len(behind.SMOOTH)} frames)")

ring = np.zeros((behind.WORK_H, behind.WORK_W), np.float32)
cv2.circle(ring, (135, 200), 80, 1.0, -1)
cv2.circle(ring, (135, 200), 6, 0.0, -1)
loop = ring.copy()
cv2.circle(loop, (135, 200), 60, 0.0, -1)
ok(behind.fill_holes(ring)[200, 135] == 1.0 and behind.fill_holes(loop)[200, 135] == 0.0,
   "a small gap the finder left inside him is filled; a big one (an arm on a hip) is background and stays")

# ── 7. When it falls back ─────────────────────────────────────────────────────
print("\n== 7. When Studio isn't sure, the words go on top")


def why(clip, seg, words="Level floors first"):
    behind.use(seg)
    d, f, _ = behind.make([{**b, "words": words}], CLIPS[clip][0], LOOK, "urban_makers")
    return (f[0]["why_not"] if f else None), d


w, _ = why("nobody", BgSub(WALL))
ok(w and "couldn't find you" in w, f"nobody there: {w!r}")
w, _ = why("two", Truth(CLIPS["two"][1]))
ok(w and "more than one person" in w, f"two people: {w!r}")
w, _ = why("one", Truth(one_masks, flaky=True))
ok(w and "outline" in w, f"an outline that keeps changing: {w!r}")
w, _ = why("pan", Truth(CLIPS["pan"][1]))
ok(w and "camera moves" in w, f"a panning camera: {w!r}")
w, _ = why("close", Truth(CLIPS["close"][1]))
ok(w and "fill too much" in w, f"a close-up he fills: {w!r}")
w, d = why("light", BgSub(LIGHT))
ok(w is None and d and d[0]["check"]["dark"], f"a light wall: dark words ({w or d[0]['check']})")
behind.use(BgSub(WALL))
settings.EDIT_BEHIND = False
d, f, _ = behind.make([b], one_clip, LOOK, "urban_makers")
ok(not d and "turned off" in f[0]["why_not"], "turned off: on top, saying why")
settings.EDIT_BEHIND = True

# ── 8. The real person finder ─────────────────────────────────────────────────
print("\n== 8. The real person finder (MediaPipe selfie multiclass on LiteRT)")
behind.use(None)
cache = Path.home() / ".cache" / "studio-quality" / behind.MODEL["file"]
if cache.exists() and cache.stat().st_size == behind.MODEL["bytes"]:
    behind.MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy(cache, behind.MODELS / behind.MODEL["file"])
real = None
try:
    t0 = time.time()
    path = behind.model_path()
    real = behind.segmenter()
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        shutil.copy(path, cache)
    ok(path.stat().st_size == behind.MODEL["bytes"], f"the model is in Studio/Models, checked ({time.time() - t0:.1f}s)")
except Exception as e:
    ok(False, f"the model could be had (storage.googleapis.com, or ~/.cache/studio-quality): {e}")
import requests
real_get = requests.get


class BadDownload:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def raise_for_status(self): pass
    def iter_content(self, n): yield b"not a model"


saved_models = behind.MODELS
behind.MODELS = P / "models-bad"
behind._failed_at = 0.0
requests.get = BadDownload
try:
    behind.model_path()
    ok(False, "a model that comes down wrong is refused")
except RuntimeError as e:
    ok("fingerprint" in str(e) and not list(behind.MODELS.glob("*")), f"a model that comes down wrong is refused, nothing kept: {e}")
try:
    behind.model_path()
    ok(False, "not fetched again for an hour")
except RuntimeError as e:
    ok("last hour" in str(e), "not fetched again within the hour")
requests.get = real_get
behind.MODELS, behind._failed_at = saved_models, 0.0
# A real-looking mid-shot: the fixture photo, sharp, over a blurred enlargement of itself, drifting a little.
back = cv2.GaussianBlur(cv2.resize(PHOTO, (1400, 2600), interpolation=cv2.INTER_CUBIC), (0, 0), 30)
fg = cv2.resize(PHOTO, (round(150 * 4.6), round(302 * 4.6)), interpolation=cv2.INTER_CUBIC).astype(np.float32)
fh, fw = fg.shape[:2]
fade = np.ones((fh - 40, fw), np.float32)
for e in range(60):
    v = (e + 1) / 61
    fade[e, :] = np.minimum(fade[e, :], v)
    fade[:, e] = np.minimum(fade[:, e], v)
    fade[:, fw - 1 - e] = np.minimum(fade[:, fw - 1 - e], v)


def mid(i):
    frame = back[300:2220, 160:1240].astype(np.float32).copy()
    x, y = int(540 - fw / 2 + 14 * np.sin(i / N * 2 * np.pi)), 1920 - fh + 40
    frame[y:, x:x + fw] = frame[y:, x:x + fw] * (1 - fade[..., None]) + fg[:fh - 40] * fade[..., None]
    return frame.clip(0, 255).astype(np.uint8), None


real_clip = P / "media" / "real.mp4"
write_clip(real_clip, mid, N)
if real is not None:
    first = read_rgb(real_clip, 0.0, 1)[0]
    pmap, fmap = real.person(first)
    pm = cv2.resize(pmap, (W, H)) > 0.5
    fm = cv2.resize(fmap, (W, H)) > 0.5
    g = cv2.resize(cv2.cvtColor(first, cv2.COLOR_RGB2GRAY), (270, 480), interpolation=cv2.INTER_AREA)
    face = framing.find_face(g)
    fx, fy = (int(face[0] * W), int(face[1] * H)) if face else (0, 0)
    ok(face is not None and 0.08 < pm.mean() < 0.45 and pm[fy, fx] and fm[fy - 20:fy + 20, fx - 20:fx + 20].mean() > 0.5,
       f"on a real-looking frame: him found ({pm.mean():.0%} of the frame), the face the face finder sees inside him "
       "and marked as face")
    t0, c0 = time.time(), os.times()
    rd, rf, rt = behind.make([{**b, "words": "Level floors first"}], real_clip, LOOK, "urban_makers")
    ok(rd and not rf and rd[0]["check"]["people"] == 1 and rd[0]["check"]["faces"] == 1,
       f"made with the real finder: {rd[0]['check'] if rd else rf}")
    if rd:
        cps = rd[0]["check"]["cpu_per_second"]
        print(f"  real finder: {time.time() - t0:.1f}s for {N / FPS:.1f}s, {cps} CPU s a second of effect")
        ok(cps < 40, f"render time sane: {cps} CPU s a second of effect ({rd[0]['check']['seconds']}s for 2.5 s)")
        la = layer_rgba(editor.INSERTS / f"{rd[0]['file']}.webm")[..., 3]
        rc = over(real_clip, editor.INSERTS / f"{rd[0]['file']}.webm")
        hits = []
        for i in range(0, N, 5):
            gi = cv2.resize(cv2.cvtColor(rc[i], cv2.COLOR_RGB2GRAY), (270, 480), interpolation=cv2.INTER_AREA)
            f2 = framing.find_face(gi)
            if f2:
                cx2, cy2, sz = f2[0] * W, f2[1] * H, f2[2] * W * 0.3
                hits.append(int(la[i, int(cy2 - sz):int(cy2 + sz), int(cx2 - sz):int(cx2 + sz)].max()))
        ok(len(hits) >= 10 and max(hits) <= CODEC, f"his face found in the finished frames ({len(hits)} of {len(range(0, N, 5))}) "
           "with no word on it")
        ok(rd[0]["check"]["showing"] >= behind.VISIBLE_MIN and rd[0]["check"]["worst_word"] >= behind.WORD_MIN,
           f"readable round him: {rd[0]['check']['showing']:.0%} of the words showing, the least-shown word "
           f"{rd[0]['check']['worst_word']:.0%}")
        if STRIP:
            for i, kk in enumerate((3, 8, 30, 55, 72)):
                Image.fromarray(rc[kk]).save(STRIP / f"real_{i}.png")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r",
                            str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(STRIP / "real.mp4")],
                           input=rc.tobytes(), check=True)
        del la, rc
else:
    for _ in range(5):
        ok(False, "the real finder's checks need its model")

# ── 9. A real draft ───────────────────────────────────────────────────────────
print("\n== 9. A draft with the hook and a key line behind him")
behind.use(BgSub(WALL))


class Fake:
    """Claude: the edit plan given; a clean read; no joins."""

    def __init__(self, plan):
        self.calls, self.plan = [], plan

    @property
    def beta(self):
        return NS(messages=NS(stream=self.stream, create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "Place names stay internal" in sysp:
            text = json.dumps({"verdict": "Ready to post", "scores": {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")},
                               "fixes": [], "works": ["ok"]})
        elif "broken sentences" in sysp:
            text = json.dumps({"joins": []})
        else:
            text = json.dumps(self.plan)
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def stream(self, **kw):
        msg = self.create(**kw)

        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()


def fake_track(brief, seconds, out_dir, ref=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    return media.music_bed(out_dir / "bed.mp3", 60), {"title": "Test Bed", "artist": "ffmpeg", "recording_id": "t1"}


CURRENT = {}
transcribe.transcribe = lambda wav: {"words": editor.timeline(CURRENT["cuts"], CL)}
music.allowed_for = lambda br: True
orig_render = editor.render_cut


def counting_render(cuts, clips, out):
    CURRENT["cuts"] = cuts
    return orig_render(cuts, clips, out)


editor.render_cut = counting_render
WORDS = [{k: w[k] for k in ("word", "start", "end")} for w in DATA["words"]]
d = settings.LIBRARY / "2026-10" / "talk"
d.mkdir(parents=True)
v = d / "talk.mp4"
shutil.copy(TALK, v)
meta = extract.probe(v)
extract.extract_frames(v, d / "frames", meta["duration"])
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": " ".join(w["word"] for w in WORDS),
                                               "words": WORDS, "segments": []}), encoding="utf-8")
CID = catalog.start(conn, "fp-talk", v.name, d, v)
catalog.save_tags(conn, CID, meta, {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "talk", "summary": "",
                   "topics": [], "setting": "", "usable": True, "quality_notes": "fine", "suggested_hook": "", "moments": []})
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
I = {}
for i, w in enumerate(WORDS):
    I.setdefault(w["word"], i)


def at(word):
    return WORDS[I[word]]["start"]


CUTS = [{"clip_id": CID, "start": WORDS[0]["start"], "end": WORDS[-1]["end"], "why": "all of it"}]


def g_behind(word, title, role="explain", duration=2.5):
    return {"clip_id": CID, "at": at(word), "duration": duration, "style": "behind", "title": title, "lines": [],
            "scene": "", "look": "cutaway", "role": role, "why": "the line that matters"}


HOOK_B = g_behind("We", "Floors *level* again", "hook", 2.6)
KEY_B = g_behind("It", "Two days to *dry*")
THIRD_B = g_behind("For", "Porcelain for kitchens")
BASE = {"cuts": CUTS, "hook_text": "Floors *level* again", "key_words": ["screed"],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "A short talk.", "notes_addressed": [], "missing_shots": []}


def production(graphics):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Floors", "hook": "h",
                                    "angle": "x", "beats": [],
                                    "segments": [{"clip_id": CID, "start": 0, "end": DATA["total"], "purpose": "a"}],
                                    "caption": "cap", "est_seconds": 25, "cta": "Save this for your reno."}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    conn.commit()
    pid = catalog.make_production(conn, idea)
    return pid, Fake({**json.loads(json.dumps(BASE)), "graphics": json.loads(json.dumps(graphics))})


def row(pid):
    return dict(conn.execute("select * from productions where id = ?", (pid,)).fetchone())


pid, fake = production([HOOK_B, KEY_B, THIRD_B])
anthropic.Anthropic = lambda **kw: fake
t0 = time.time()
final = editor.edit(conn, pid, client=fake, find_track=fake_track)
print(f"  edit took {time.time() - t0:.1f}s")
r = row(pid)
edl = json.loads(r["edl"])
qa = json.loads(r["qa"])
bs = edl.get("behind") or []
print("  behind:", [(x["words"], x["start"], x["duration"], x["role"]) for x in bs])
plan_call = fake.calls[0]
ok('Words behind Bryan (style "behind")' in plan_call["system"]
   and "behind" in plan_call["output_config"]["format"]["schema"]["properties"]["graphics"]["items"]["properties"]["style"]["enum"],
   "the plan was offered words behind him")
ok(len(bs) == 2 and bs[0]["role"] == "hook" and bs[0]["start"] == 0.0 and bs[1]["words"] == "Two days to dry"
   and all(x.get("file") for x in bs), "the hook and the key line are behind him: at most 2 in the Reel")
ok(edl["hook_text"] == "" and bs[0]["hook_text"] == "Floors level again" and bs[0]["words"] == "Floors level again",
   "the hook is behind him, not on top as well (kept, so taking it off puts it back)")
third = [g for g in edl["graphics"] if g.get("title") == "Porcelain for kitchens"]
ok(third and third[0]["style"] == "callout", "the third goes on top as a callout")
ok(not [g for g in edl["graphics"] if g.get("style") == "behind"] and "behind_plan" not in edl,
   "no graphic of the style \"behind\" left for the rest of the editor")
did = [c["did"] for c in qa["checks"]]
ok(any("behind you" in x and "Floors level again" in x for x in did) and any("Porcelain for kitchens" in x and "on top" in x for x in did),
   "the draft's checks say what went behind him and what went on top, and why")
ok("behind you" in edl["summary"], "\"What the editor did\" says so")
kb = bs[1]
tm = round((kb["start"] + 1.4) * FPS) / FPS
fin = read_rgb(final, tm, 1)[0]
src_t = fix_actions.Draft(edl["cuts"], CL).to_source(tm)[1]
srcf, smask = one(round(src_t * FPS))
lay = layer_rgba(editor.INSERTS / f"{kb['file']}.webm")[round((tm - kb["start"]) * FPS)]
wink = (lay[..., 3] > 240) & ~cv2.dilate(smask.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool)
ok(wink.sum() > 15000 and fin[wink].mean() > 215, f"in the finished draft the words show over the wall ({fin[wink].mean():.0f})")
x0, y0, x1, y1 = kb["check"]["words_at"]
sc = cv2.erode(smask.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool)
sc[:y0] = False
sc[y1:] = False
dd = np.abs(fin.astype(int) - srcf.astype(int))[sc]
ok(sc.sum() > 3000 and np.median(dd) <= 6, f"and he is in front of them: his pixels in their box as filmed (median {np.median(dd):.0f} levels)")
band = slice(editor.CAPTION_TOP, editor.CAPTION_TOP + 180)
capd = np.abs(fin[band].astype(int) - srcf[band].astype(int)).max(2)
ok((capd > 80).sum() > 800 and (lay[editor.CAPTION_TOP:, :, 3] == 0).all(), "the captions on top as usual, the words never in their band")
fc = final_check.run(conn, r)
covers = [f for f in (fc or {}).get("flags", []) if f["check"] == "covers_face"]
ok(fc and fc["watched"] and not covers, f"the watch-through finds nothing over his face ({len((fc or {}).get('flags', []))} flags)")
# A cut-out that missed him: the layer's words over the middle of his face.
bad = dict(kb)
fa = framing.find_face(cv2.resize(cv2.cvtColor(fin, cv2.COLOR_RGB2GRAY), (270, 480), interpolation=cv2.INTER_AREA))
bx, by = int(fa[0] * W), int(fa[1] * H)
frames_bad = np.zeros((round(kb["duration"] * FPS), H, W, 4), np.uint8)
frames_bad[:, by - 120:by + 120, bx - 200:bx + 200] = 255
bad_file = editor.INSERTS / "behind-broken-test.webm"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-deadline", "realtime",
                "-cpu-used", "8", str(bad_file)], input=frames_bad.tobytes(), check=True)
broken = json.loads(json.dumps(edl))
broken["behind"][1]["file"] = "behind-broken-test"
job = final_check._job(conn, {**r, "edl": json.dumps(broken)})
flags, _ = final_check.watch(job)
flag = next((f for f in flags if f["check"] == "covers_face" and f["action"]["type"] == "behind"), None)
ok(flag and flag["action"]["item"] == 2 and flag["action"]["field"] == "remove" and flag["editor_can_fix"],
   f"a broken cut-out (words over his face) is flagged, and the fix takes those words off: {flag and flag['what']}")
ok("behind" in draft_review.REEL_ACTIONS and "behind" in fix_actions.PATCHES, "the read and the patches know the action")
material = draft_review._context(conn, r)
ok("Words behind Bryan" in material and "B2" in material and "(the hook)" in material, "Studio's read is told they're deliberate")
ok("Hook text on screen for the first ~2.5s: \"Floors level again\", behind Bryan (B1, not on top)" in material,
   "and that the hook is there, behind him (so it doesn't ask for one on top)")

print("\n== 10. A fix round takes them off, or adds them, with no new plan")
calls = len(fake.calls)
patched, res = editor.self_patch(conn, pid, [{"n": 1, "action": flag["action"]}], 1, client=fake, find_track=fake_track)
pe = json.loads(row(pid)["edl"])
ok(res[0]["ok"] and len(pe.get("behind") or []) == 1 and pe["behind"][0]["role"] == "hook" and pe["hook_text"] == ""
   and not any(c.get("system", "").startswith("You are the editor") for c in fake.calls[calls:]),
   f"the round took \"Two days to dry\" off and kept the hook behind him, no new plan ({res[0].get('did')})")
new, res2 = fix_actions.apply(pe, CL, [{"n": 1, "action": {"type": "behind", "field": "remove", "item": 1}}])
ok(res2[0]["ok"] and not new["behind"] and new["hook_text"] == "Floors level again", f"the hook taken off: back on top ({res2[0]['did']})")
new, res3 = fix_actions.apply(pe, CL, [{"n": 1, "action": {"type": "behind", "field": "", "start": kb["start"],
                                                             "end": kb["start"] + 2.4, "to": "Two *days*"}}])
ok(res3[0]["ok"] and len(new["behind"]) == 2 and new["behind"][1]["words"] == "Two days" and new["behind"][1]["italic"] == "days",
   f"the read can put words behind him ({res3[0]['did']})")
new2, res4 = fix_actions.apply(new, CL, [{"n": 1, "action": {"type": "behind", "field": "", "start": 14.0, "end": 16.0,
                                                               "to": "Porcelain"}}])
ok(not res4[0]["ok"] and "keeps to 2" in res4[0]["why"], f"never a third: {res4[0].get('why')!r}")
new3, res5 = fix_actions.apply(pe, CL, [{"n": 1, "action": {"type": "hook_text", "to": "Floors *flat* again"}}])
ok(res5[0]["ok"] and new3["hook_text"] == "" and new3["behind"][0]["words"] == "Floors flat again"
   and new3["behind"][0]["italic"] == "flat", f"the read's new hook words go behind him, not a second hook on top ({res5[0].get('did')})")

print("\n== 11. When the person can't be found, the draft has the words on top")
behind.use(BgSub(LIGHT))  # the wrong wall: everything differs, he fills the frame
pid2, fake2 = production([HOOK_B, KEY_B])
final2 = editor.edit(conn, pid2, client=fake2, find_track=fake_track)
e2 = json.loads(row(pid2)["edl"])
q2 = json.loads(row(pid2)["qa"])
ok(not e2.get("behind") and e2["hook_text"] == "Floors level again"
   and any(g.get("style") == "callout" and g["title"] == "Two days to dry" for g in e2["graphics"]),
   "nothing behind him: the hook on top as the hook, the key line as a callout")
ok(any("on top" in c["did"] and "instead of behind you" in c["did"] for c in q2["checks"]),
   "and the draft's checks say why: " + "; ".join(c["did"] for c in q2["checks"] if "behind" in c["did"]))

print("\n== 12. Bryan's notes add them and take them off")
behind.use(BgSub(WALL))
pid3, fake3 = production([])
editor.edit(conn, pid3, client=fake3, find_track=fake_track)
e3 = json.loads(row(pid3)["edl"])
ok(not e3.get("behind"), "v1: none")
t_note = round(fix_actions.Draft(e3["cuts"], CL).to_draft(CID, at("It")) + 0.5, 2)
conn.execute("update productions set status = 'revising' where id = ?", (pid3,))
conn.execute("""INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                VALUES ('production', ?, 1, ?, ?, 'open', ?)""", (pid3, t_note, "words behind me here", catalog.now()))
conn.commit()
editor.edit(conn, pid3, client=fake3, find_track=fake_track)
e3b = json.loads(row(pid3)["edl"])
nb = e3b.get("behind") or []
ok(len(nb) == 1 and nb[0].get("asked") and abs(nb[0]["start"] - t_note) < 1.0 and nb[0]["words"],
   f"v2: words behind him where the note was ({nb and nb[0]['words']!r} at {nb and nb[0]['start']}), from what he says there")
ok(any("as your note asked" in c["did"] for c in json.loads(row(pid3)["qa"])["checks"]), "the checks say the note did it")
fake3.plan["graphics"] = [KEY_B]  # the plan would put them back
conn.execute("update productions set status = 'revising' where id = ?", (pid3,))
conn.execute("""INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                VALUES ('production', ?, 2, NULL, ?, 'open', ?)""", (pid3, "no words behind me please", catalog.now()))
conn.commit()
editor.edit(conn, pid3, client=fake3, find_track=fake_track)
e3c = json.loads(row(pid3)["edl"])
ok(not e3c.get("behind") and e3c.get("behind_off"), "v3: \"no words behind me\" takes them off, whatever the plan says")
conn.execute("update productions set status = 'revising' where id = ?", (pid3,))
conn.commit()
editor.edit(conn, pid3, client=fake3, find_track=fake_track)
e3d = json.loads(row(pid3)["edl"])
ok(not e3d.get("behind") and e3d.get("behind_off"), "v4: and they stay off for this post")
ok(bool(nb) and "Previous words behind Bryan" in behind.describe(e3b) and nb[0]["words"] in behind.describe(e3b),
   "the revision prompt's line for the words behind him before")
rev = [c for c in fake3.calls if c.get("system", "").startswith("You are the editor")]
ok(any("Previous words behind Bryan" in c["messages"][0]["content"] for c in rev), "a re-plan is shown the words behind him before")

print("\n== 13. Docs, help, settings, the updater")
root = Path(settings.__file__).resolve().parent
readme = (root / "README.md").read_text()
claude_md = (root / "CLAUDE.md").read_text()
help_js = (root / "cloud" / "planner" / "help.js").read_text()
envx = (root / ".env.example").read_text()
reqs = (root / "requirements.txt").read_text()
ok("### Words behind you" in readme and "words behind me here" in readme and "STUDIO_EDIT_BEHIND_MAX" in readme,
   "the README explains it, and how to ask")
ok("words behind me here" in help_js and "behind" in help_desk.PAGE_WORDS["draft"], "help.js and Ask Studio know it")
ok("not yet proven on real footage on the server" in claude_md and "behind.py" in claude_md, "CLAUDE.md's open item")
ok("STUDIO_EDIT_BEHIND=" in envx and re.search(r"^ai-edge-litert==\d", reqs, re.M) and "behind" in updater._MODULES,
   ".env.example, the pinned requirement (the updater installs it) and the updater's import check")
finish()
