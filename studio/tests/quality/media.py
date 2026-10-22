"""Test media made at run time, so nothing large is committed: real speech (flite, through ffmpeg's flite
filter) with known word edges, a person on a plain background (fixtures/person.jpg, the person cut out of
OpenCV's Apache-2.0 sample photo messi5.jpg; OpenCV's face detector needs a real face, so this one small
picture is committed), B-roll with no face, a warm music bed and a tone, and a carousel's slides (flat colours,
drawn with Pillow).

The speech sets match the suites' originals exactly (same words, gaps and random seeds), so every
check sees the same timings it was written against."""
import json, random, shutil, subprocess
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FACE = FIXTURES / "person.jpg"
FACE_IN_CROP = (75.5, 72.5)  # the face centre in person.jpg (150 x 302)
R = 48000


def run(args):
    subprocess.run(args, check=True)


def dur(path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


# ── Speech with known word edges, one word at a time ──────────────────────────
# (word, gap before it in seconds). Sentences as Bryan might say them.
ENDING_SCRIPT = [  # the ending suite's (pauses inside sentences and between them)
    ("We", 0.5), ("strip", 0.03), ("the", 0.02), ("floor", 0.03), ("first.", 0.03),
    ("Then", 0.9), ("we", 0.03), ("pour", 0.03), ("a", 0.55), ("level", 0.03), ("screed", 0.03), ("over", 0.5),
    ("the", 0.03), ("whole", 0.03), ("room.", 0.03),
    ("It", 1.0), ("takes", 0.6), ("about", 0.03), ("two", 0.03), ("days", 0.03), ("to", 0.03), ("dry.", 0.03),
    ("For", 0.9), ("a", 0.03), ("kitchen,", 0.03), ("we", 0.8), ("use", 0.03), ("porcelain.", 0.03),
    ("After", 0.9), ("that", 0.03), ("the", 1.3), ("tiles", 0.03), ("go", 0.03), ("down.", 0.03),
    ("So", 0.9), ("the", 0.03), ("floor", 0.03), ("stays", 0.03), ("flat", 0.03), ("for", 0.03), ("years.", 0.03),
]
ENDING_DECAY = {"room.", "dry.", "years.", "tiles"}

CUTS_SCRIPT = [  # the cuts suite's (fillers, stutters, a decay on the long words, a long middle part)
    ("This", 0.4), ("sofa", 0.03), ("is", 0.02), ("really", 0.03), ("comfortable.", 0.02),
    ("It", 0.7), ("is", 0.02), ("possible", 0.03), ("to", 0.04), ("make", 0.02), ("the", 0.02), ("joints", 0.03), ("seamless.", 0.03),
    ("So", 0.7), ("the", 0.03), ("um", 0.25), ("floor", 0.3), ("stays", 0.03), ("level.", 0.02),
    ("The", 0.7), ("the", 0.05), ("tiles", 0.03), ("go", 0.03), ("on", 0.02), ("last.", 0.03),
    ("This", 0.7), ("finish", 0.03), ("excites", 0.03), ("excites", 0.08), ("everyone.", 0.03),
    ("We", 0.7), ("pour", 0.03), ("the", 0.02), ("screed,", 0.03), ("which", 0.2), ("takes", 0.03), ("two", 0.03), ("days,", 0.03),
    ("and", 0.25), ("then", 0.03), ("we", 0.03), ("tile.", 0.03),
    ("Ah,", 0.7), ("that", 0.08), ("is", 0.02), ("why", 0.03), ("it", 0.03), ("cracks.", 0.03),
    # A long middle part (over 4 s) that a planned cut drops: Claude is asked about it.
    ("The", 0.7), ("waterproofing,", 0.03), ("which", 0.2), ("we", 0.03), ("brush", 0.03), ("on", 0.03), ("in", 0.03),
    ("two", 0.03), ("coats", 0.03), ("and", 0.15), ("leave", 0.03), ("to", 0.03), ("dry", 0.03), ("overnight", 0.03),
    ("before", 0.15), ("anyone", 0.03), ("walks", 0.03), ("on", 0.03), ("it,", 0.03), ("stops", 0.25), ("leaks.", 0.03),
]
CUTS_DECAY = {"comfortable.", "possible", "seamless.", "tiles", "excites"}


def word_speech(out: Path, script, decay, tail: float):
    """Each word spoken by flite on its own (so its real start and end are known to the sample), laid
    out as connected sentences, decays on the long words, a quiet room floor. Then 'Whisper' timings:
    ends 60-200 ms early versus the true audio (the decay words 150-200 ms), starts within +-30 ms
    (faster-whisper's model isn't downloaded for a test). Writes talk.f32, talk.wav, talk.mp4 (1080x1920)
    and words.json into out."""
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    cache = {}

    def say(word):
        text = word.strip(".,").replace("'", "")
        if text not in cache:
            f = out / "w.wav"
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
                 "-ar", str(R), "-ac", "1", "-f", "f32le", str(f)])
            cache[text] = np.fromfile(f, "<f4").astype(np.float64)
        x = cache[text]
        env = np.sqrt(np.convolve(x * x, np.ones(240) / 240, "same"))
        on = np.nonzero(env > 10 ** (-50 / 20))[0]
        x = x[max(0, on[0] - 48): on[-1] + 48]
        x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
        if word in decay:  # a trailing decay: the word's last 40 ms rings on, fading over ~150 ms
            seg = x[-int(0.04 * R):]
            ring = np.tile(seg, 6)[: int(0.2 * R)]
            t = np.arange(len(ring)) / R
            ring = ring * np.exp(-t / 0.03)
            fade = np.linspace(1, 0, len(seg))
            x = np.concatenate([x[:-len(seg)], x[-len(seg):] * fade + ring[:len(seg)] * (1 - fade), ring[len(seg):]])
        return x

    pieces, words, t = [], [], 0.0
    for word, gap in script:
        t += gap
        x = say(word)
        pieces.append((int(round(t * R)), x))
        words.append({"word": word, "true_start": round(t, 4), "true_end": round(t + len(x) / R, 4)})
        t += len(x) / R
    total = t + tail
    sig = np.zeros(int(total * R))
    for at, x in pieces:
        sig[at:at + len(x)] += x
    noise = np.random.default_rng(3).normal(0, 1, len(sig))
    noise = np.convolve(noise, np.ones(8) / 8, "same")
    sig += noise / np.sqrt(np.mean(noise ** 2)) * 10 ** (-62 / 20)
    # True ends where each word's own sound falls below -55 dBFS (10 ms RMS), so a decay counts.
    for w, (at, x) in zip(words, pieces):
        e = np.sqrt(np.convolve(x * x, np.ones(480) / 480, "same"))
        loud = np.nonzero(e > 10 ** (-55 / 20))[0]
        w["true_start"] = round((at + loud[0]) / R, 4)
        w["true_end"] = round((at + loud[-1]) / R, 4)
        heard = np.nonzero(e > 10 ** (-48 / 20))[0]  # clearly audible (14 dB over the room)
        w["audible_start"], w["audible_end"] = round((at + heard[0]) / R, 4), round((at + heard[-1]) / R, 4)
    for w in words:
        early = rng.uniform(0.15, 0.20) if w["word"] in decay else rng.uniform(0.06, 0.20)
        w["start"] = round(w["true_start"] + rng.uniform(-0.03, 0.03), 2)
        w["end"] = round(max(w["start"] + 0.05, w["true_end"] - early), 2)
    wav = out / "talk.wav"
    sig.astype("<f4").tofile(out / "talk.f32")
    run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(R), "-ac", "1", "-i", str(out / "talk.f32"), str(wav)])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x4a5560:size=1080x1920:rate=30:duration={total:.3f}",
         "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-shortest", str(out / "talk.mp4")])
    (out / "words.json").write_text(json.dumps({"total": total, "words": words}, indent=0))
    return out


def ending_speech(out: Path):
    return word_speech(out, ENDING_SCRIPT, ENDING_DECAY, 2.2)


def cuts_speech(out: Path):
    return word_speech(out, CUTS_SCRIPT, CUTS_DECAY, 0.6)


# ── Phrase speech, a person, B-roll, music ────────────────────────────────────
def phrase_speech(parts, out: Path):
    """parts: strings (spoken) or floats (seconds of silence). Word times are shared out over each phrase
    by letters. Returns (wav, words, total)."""
    work = out.parent / (out.stem + "_parts"); work.mkdir(parents=True, exist_ok=True)
    files, words, t = [], [], 0.0
    for i, p in enumerate(parts):
        f = work / f"{i}.wav"
        if isinstance(p, (int, float)):
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{p:.3f}", str(f)])
            d = p
        else:
            safe = p.replace("'", "").replace(":", "")
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"flite=text='{safe}':voice=slt",
                 "-af", "aresample=48000,silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=1:stop_threshold=-50dB:stop_duration=0.05",
                 "-ac", "1", str(f)])
            d = dur(f)
            ws = p.split(); chars = sum(len(w) + 1 for w in ws); at = t
            for w in ws:
                wd = d * (len(w) + 1) / chars
                words.append({"start": round(at, 3), "end": round(at + wd, 3), "word": w}); at += wd
        files.append(f); t += d
    lst = work / "list.txt"; lst.write_text("".join(f"file '{f}'\n" for f in files))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "48000", "-ac", "1", str(out)])
    return out, words, dur(out)


def person_video(out: Path, size, seconds, face_x_expr, face_y, scale, audio=None, bg="0x4a5560", noise=6):
    """A plain background with the person on it; face_x_expr is the face centre's x in pixels (ffmpeg expr
    of t), face_y its y; scale the person's size (3.0: the face about 230 px across); noise: the sensor's
    grain on the background."""
    w, h = size
    cw, ch = 150, 302
    fx, fy = FACE_IN_CROP[0] * scale, FACE_IN_CROP[1] * scale
    ox = f"({face_x_expr})-{fx:.1f}"
    oy = f"{face_y - fy:.1f}"
    inputs = ["-f", "lavfi", "-i", f"color=c={bg}:size={w}x{h}:rate=30:duration={seconds:.3f}", "-loop", "1", "-i", str(FACE)]
    graph = (f"[1:v]scale={round(cw * scale)}:{round(ch * scale)}[p];"
             f"[0:v]noise=alls={noise}:allf=t[bg];[bg][p]overlay=x='{ox}':y={oy}:shortest=1,format=yuv420p[v]")
    args = ["ffmpeg", "-y", "-v", "error", *inputs]
    if audio:
        args += ["-i", str(audio)]
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "2:a", "-c:a", "aac", "-ar", "48000"]
    args += ["-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    run(args)
    return out


def broll(out: Path, seconds):
    """Horizontal B-roll, no face (ffmpeg's test pattern), a quiet room sound."""
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"anoisesrc=a=0.01:d={seconds}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ar", "48000", "-t", str(seconds), str(out)])
    return out


def music_bed(out: Path, seconds=40):
    """A warm chord bed with a soft beat (stereo mp3)."""
    if not out.exists():
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "aevalsrc='0.22*(sin(2*PI*220*t)+sin(2*PI*277.18*t)+sin(2*PI*329.63*t)+0.5*sin(2*PI*440*t))/3.5*(0.7+0.3*sin(2*PI*0.5*t))"
             f"+0.35*sin(2*PI*55*t)*exp(-9*mod(t,0.5))+0.04*(random(0)-0.5)*exp(-30*mod(t+0.25,0.5))':s=48000:d={seconds}",
             "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out)])
    return out


def small_clip(out: Path):
    """A tiny 2 s clip with sound, the 'render' of the suites' fake-render cases."""
    if not out.exists():
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=108x192:d=2", "-f", "lavfi", "-i",
             "sine=d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)])
    return out


# ── The quiet talking head in a noisy room (the self-fix suite's clip) ────────
QUIET_LINES = ["Different floor heights?", 0.4, "One of them is an overlay.", 0.5, "When you renovate an old flat,", 0.3,
               "the kitchen floor is often higher than the living room.", 4.0,
               "That step at the doorway is a trip hazard.", 0.4, "To level the floor we pour a screed,", 0.3,
               "and then we tile over it as well.", 3.5]


def quiet_talk(m: Path, loudness):
    """Flite phrases recorded quietly (about -41 LUFS) over a room's hiss and a mains hum, on a person
    video: m/talk.mp4, m/clipdir/transcript.json, and the music bed m/music.mp3. loudness: editor.loudness."""
    m.mkdir(parents=True, exist_ok=True)
    voice, words, vlen = phrase_speech(QUIET_LINES, m / "voice_clean.wav")
    lufs_clean = loudness(voice)
    quiet = m / "voice_quiet.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.0035:d={vlen:.3f}:seed=7",
         "-f", "lavfi", "-i", f"sine=f=50:d={vlen:.3f}", "-filter_complex",
         f"[0:a]volume={-41 - lufs_clean:.2f}dB[v];[2:a]volume=0.003[h];[v][1:a][h]amix=inputs=3:normalize=0:duration=first,aformat=channel_layouts=mono",
         "-ar", "48000", str(quiet)])
    person_video(m / "talk.mp4", (1080, 1920), vlen, "540", 700, 3.0, audio=quiet)
    music_bed(m / "music.mp3")
    d = m / "clipdir"; d.mkdir(exist_ok=True)
    (d / "transcript.json").write_text(json.dumps({"words": words}), encoding="utf-8")
    shutil.rmtree(m / "voice_clean_parts", ignore_errors=True)
    return m


# ── The listen-back and watch-through suite's clips ───────────────────────────
def finalcheck_media(m: Path):
    """The ending suite's speech on footage with the face high (under the titles) and low (clear of
    them), the same talk twice over (a ~45 s draft), and B-roll."""
    e = ending_speech(m / "speech")
    data = json.loads((e / "words.json").read_text())
    total = data["total"]
    person_video(m / "face_high.mp4", (1080, 1920), total, "540", 520, 3.2, audio=e / "talk.wav")
    person_video(m / "face_low.mp4", (1080, 1920), total, "540", 1440, 3.2, audio=e / "talk.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(e / "talk.wav"), "-i", str(e / "talk.wav"), "-filter_complex",
         "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", str(m / "long.wav")])
    person_video(m / "long.mp4", (1080, 1920), 2 * total, "540", 760, 2.6, audio=m / "long.wav")
    words2 = data["words"] + [{**w, **{k: round(w[k] + total, 4) for k in w if k != "word"}} for w in data["words"]]
    (m / "long_words.json").write_text(json.dumps({"total": 2 * total, "words": words2}))
    shutil.copy(e / "words.json", m / "words.json")
    broll(m / "broll.mp4", 6)
    return m


# ── A carousel's slides (the slideshow suite's) ───────────────────────────────
def slide_pngs(folder: Path, headings, colours, font_size=70):
    """Carousel slides as 1080x1350 PNGs (01.png, 02.png, ...), each one flat colour so a frame tells which slide
    is up: a white frame at the slide's own margins, a dark line at its very edge (to see it's all there), and
    "n. heading" in the carousel's Figtree. thumb.jpg is the last slide, small. Returns the files' paths."""
    from PIL import Image, ImageDraw, ImageFont
    folder.mkdir(parents=True, exist_ok=True)
    fig = Path(__file__).resolve().parents[2] / "render" / "carousel" / "fonts" / "Figtree-latin.woff2"
    try:
        font = ImageFont.truetype(str(fig), font_size)
    except Exception:
        font = ImageFont.load_default()
    files = []
    for n, (heading, col) in enumerate(zip(headings, colours), 1):
        im = Image.new("RGB", (1080, 1350), col)
        d = ImageDraw.Draw(im)
        d.rectangle([84, 84, 996, 1266], outline=(255, 255, 255), width=10)
        d.rectangle([0, 0, 1079, 1349], outline=(20, 20, 20), width=4)
        d.text((120, 500), f"{n}. {heading}", fill=(255, 255, 255), font=font)
        f = folder / f"{n:02d}.png"; im.save(f); files.append(str(f))
    im.convert("RGB").resize((540, 675)).save(folder / "thumb.jpg")
    return files


def flat_photo(out: Path, size=(1200, 900), colour=(150, 120, 90)):
    """A plain photo (one colour), as an uploaded picture on the Studio machine."""
    from PIL import Image
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(out)
    return out


def tone_track(out: Path, seconds=90):
    """A soft steady chord (262 + 392 Hz) as an mp3, the stand-in for a calm Epidemic track."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=262:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=392:duration={seconds}", "-filter_complex", "amix=inputs=2", "-c:a", "libmp3lame", str(out)])
    return out


# ── A voice-over's footage (the pip suite's) ──────────────────────────────────
# Bryan in his studio (a still camera, a plain wall, clean sound) and on site (held in the hand, a busy
# background, the site's noise), saying the same lines; and three B-roll clips, each its own picture (so a frame
# tells which is up) with the site's sound, one with a few words said in the background.
STUDIO_LINES = ["Most floors in old flats are not level.", 0.55, "Here is how we fix that.", 0.6,
                "First we hack off the old tiles,", 0.35, "right down to the screed.", 0.6,
                "Then we pour a new screed over the whole room.", 0.6, "It takes about two days to dry.", 0.6,
                "After that, the new tiles go down.", 0.6, "So the floor stays flat for years.", 0.9]
BROLL_LOOKS = {"hack": "testsrc2=size=1920x1080:rate=30", "pour": "smptehdbars=size=1920x1080:rate=30",
               "tile": "rgbtestsrc=size=1920x1080:rate=30"}


def site_person(out: Path, seconds, audio, scale=3.0):
    """The person on a busy background (the test pattern, dimmed), the picture shaking as a hand-held phone does
    (a window moving 40 px about), with the given sound."""
    w, h, pad = 1080, 1920, 60
    cw, ch = 150, 302
    fx, fy = FACE_IN_CROP[0] * scale, FACE_IN_CROP[1] * scale
    graph = (f"[0:v]eq=brightness=-0.25:saturation=0.35[bg];[1:v]scale={round(cw * scale)}:{round(ch * scale)}[p];"
             f"[bg][p]overlay=x={(w + 2 * pad) / 2 - fx:.1f}:y={760 + pad - fy:.1f}:shortest=1,"
             f"crop={w}:{h}:x='{pad}+40*sin(2*PI*t*1.3)':y='{pad}+40*sin(2*PI*t*1.7+1)',format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={w + 2 * pad}x{h + 2 * pad}:rate=30:duration={seconds:.3f}",
         "-loop", "1", "-i", str(FACE), "-i", str(audio), "-filter_complex", graph, "-map", "[v]", "-map", "2:a",
         "-c:a", "aac", "-ar", "48000", "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])
    return out


def site_broll(out: Path, look: str, seconds: float = 8.0, words: str = ""):
    """Horizontal B-roll (one of BROLL_LOOKS: no face), the site's sound (a low rumble and hiss, about -32 dBFS), and
    when words is given, someone saying them quietly in the background."""
    inputs = ["-f", "lavfi", "-i", f"{BROLL_LOOKS[look]}:duration={seconds}",
              "-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=0.06:d={seconds}:seed=11"]
    mix = "[1:a]aformat=channel_layouts=mono,aresample=48000[a]"
    if words:
        inputs += ["-f", "lavfi", "-i", f"flite=text='{words}':voice=kal"]
        mix = ("[2:a]aresample=48000,volume=0.35,adelay=2000|2000,apad[w];[1:a]aformat=channel_layouts=mono,"
               "aresample=48000[n];[n][w]amix=inputs=2:normalize=0:duration=first[a]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
         "-t", str(seconds), str(out)])
    return out


def pip_media(m: Path):
    """m/studio.mp4 (Bryan in the studio: still, plain wall, clean sound) and m/site.mp4 (the same lines on site:
    shaking, busy background, the site's noise about 10 dB under him), their word times (m/words.json), and the
    B-roll m/hack.mp4, m/pour.mp4 (a few words in the background) and m/tile.mp4."""
    m.mkdir(parents=True, exist_ok=True)
    voice, words, vlen = phrase_speech(STUDIO_LINES, m / "voice.wav")
    clean = m / "voice_room.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.0006:d={vlen:.3f}:seed=5",
         "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0:duration=first,aformat=channel_layouts=mono", "-ar", "48000", str(clean)])
    person_video(m / "studio.mp4", (1080, 1920), vlen, "540", 700, 3.0, audio=clean, bg="0x5a6470", noise=1)
    noisy = m / "voice_site.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.3:d={vlen:.3f}:seed=9",
         "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0:duration=first,aformat=channel_layouts=mono", "-ar", "48000", str(noisy)])
    site_person(m / "site.mp4", vlen, noisy)
    site_broll(m / "hack.mp4", "hack")
    site_broll(m / "pour.mp4", "pour", words="pass me the trowel")
    site_broll(m / "tile.mp4", "tile")
    (m / "words.json").write_text(json.dumps({"total": vlen, "words": words}))
    shutil.rmtree(m / "voice_parts", ignore_errors=True)
    return m
