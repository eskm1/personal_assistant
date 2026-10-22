"""Voice-over Reels (voiceover.py; Bryan, 2026-10-21: film B-roll on site, talk to camera in the studio after, and
"I will do a talking head in my studio that you'll then cut and place in a part of the screen to show that it's still
me. Sometimes we'll do on site but i'll leave it to you to recognise that").
A talking head made at run time (fixtures/person.jpg, flite speech) filmed "in the studio" (a still camera, a plain
wall, clean sound) and "on site" (shaking, a busy background, the site's noise), and three B-roll clips (test
patterns with the site's sound, one with a few words in the background). Claude, music, the Whisper re-check and
Studio's read stubbed; everything else real: the footage check, the cut, the window, the mix, the Resolve project.
The editor from before the change (fixtures/before/editor_ca2fe96.py) edits the on-site footage too: the draft must
come out as it did.
Run: python tests/quality/suite_pip.py (from studio/)"""
import json, os, re, shutil, subprocess, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish, load_before
import media
t_media = time.time()
M = media.pip_media(P / "media")
print(f"(media made in {time.time() - t_media:.0f}s)")
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_EDIT_SCENES="0")
import settings, catalog, extract, editor, voiceover, transcribe, quality, music, anthropic, fix_actions, draft_review
import numpy as np
from PIL import Image

settings.ensure_folders()
conn = catalog.connect()
DATA = json.loads((M / "words.json").read_text())
WORDS = DATA["words"]
SAID = [w["word"] for w in WORDS]


def add_clip(name, video, words, setting, fmt="talking_head", recorded=None):
    """A clip in the library as ingest leaves it: stills, transcript, Claude's tags (stubbed), the quality check."""
    d = settings.LIBRARY / "2026-10" / name
    d.mkdir(parents=True)
    v = d / f"{name}.mp4"
    shutil.copy(video, v)
    meta = extract.probe(v)
    meta["recorded_at"] = recorded
    extract.extract_frames(v, d / "frames", meta["duration"])
    tr = {"language": "en", "text": " ".join(w["word"] for w in words), "words": words, "segments": []}
    (d / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    cid = catalog.start(conn, f"fp-{name}", v.name, d, v)
    catalog.save_tags(conn, cid, meta, tr, {"brand": "urban_makers", "brand_reason": "", "format": fmt, "title": name,
                                            "summary": "", "topics": [], "setting": setting, "usable": True,
                                            "quality_notes": "fine", "suggested_hook": "", "moments": []})
    wav = extract.extract_audio(v, d / "audio.wav")
    catalog.save_quality(conn, cid, quality.check_clip(v, meta, tr, transcribe._load_wav(wav), None))
    wav.unlink()
    return cid


FEW = [{"word": w, "start": 2.0 + 0.3 * i, "end": 2.25 + 0.3 * i} for i, w in enumerate(["Pass", "me", "the", "trowel."])]
STUDIO = add_clip("studio", M / "studio.mp4", WORDS, "Indoor studio, plain grey wall", recorded="2026-10-21T15:10:00+08:00")
SITE = add_clip("site", M / "site.mp4", WORDS, "Renovation site, a bare unit being hacked",
                recorded="2026-10-20T10:20:00+08:00")
BARE = add_clip("studio_untagged", M / "studio.mp4", WORDS, "")  # no setting, no time: what's measured alone
HACK = add_clip("hack", M / "hack.mp4", [], "Renovation site", "compilation", "2026-10-20T10:05:00+08:00")
POUR = add_clip("pour", M / "pour.mp4", FEW, "Renovation site", "compilation", "2026-10-20T10:40:00+08:00")
TILE = add_clip("tile", M / "tile.mp4", [], "Renovation site", "compilation", "2026-10-20T11:00:00+08:00")


def rows():
    return {r["id"]: dict(r) for r in conn.execute("select * from clips")}


CL = rows()

# ── 1. Recognising the footage ────────────────────────────────────────────────
print("\n== 1. What each clip is")
t0 = time.time()
rec = {cid: voiceover.recognise(CL[cid]) for cid in CL}
first = time.time() - t0
for cid, r in rec.items():
    print(f"  {CL[cid]['title']}: {r['kind']} face {r['face']} speech {r['speech']} words {r['words']} "
          f"snr {r.get('snr')} move {r.get('move')} signals {r.get('signals')}")
ok(rec[STUDIO]["kind"] == "talking_head" and rec[SITE]["kind"] == "talking_head" and rec[BARE]["kind"] == "talking_head",
   "the talking heads are talking heads (a face to camera and talking most of the time)")
ok(all(rec[c]["kind"] == "broll" for c in (HACK, POUR, TILE)), "the three site clips are B-roll (no face, little or no talk)")
ok(rec[POUR]["words"] == 4, "the B-roll with a few words in the background is still B-roll")
ok(rec[STUDIO]["signals"]["sound"] == 1 and rec[SITE]["signals"]["sound"] == -1,
   f"the sound: clean in the studio ({rec[STUDIO]['snr']} dB over the room), the site's noise on site ({rec[SITE]['snr']} dB)")
ok(rec[STUDIO]["signals"]["camera"] == 1 and rec[SITE]["signals"]["camera"] == -1,
   f"the camera: still in the studio ({rec[STUDIO]['move']}), held in the hand on site ({rec[SITE]['move']})")
t0 = time.time()
again = voiceover.recognise(CL[STUDIO])
ok(again == rec[STUDIO] and (Path(CL[STUDIO]["clip_dir"]) / "footage.json").exists() and time.time() - t0 < 0.2,
   f"kept beside the clip: {first:.1f}s for all six the first time, {1000 * (time.time() - t0):.0f} ms again")


def mode_of(*ids):
    return voiceover.mode({i: CL[i] for i in ids})


vo = mode_of(STUDIO, HACK, POUR, TILE)
print("  studio + B-roll:", {k: vo[k] for k in ("on", "why", "studio", "site", "broll")},
      {k: (f["where"], f["score"]) for k, f in vo["footage"].items() if f["kind"] == "talking_head"})
ok(vo["on"] and vo["studio"] == [STUDIO] and sorted(vo["broll"]) == sorted([HACK, POUR, TILE]),
   "studio talking head + site B-roll: a voice-over")
ok(vo["footage"][str(STUDIO)]["signals"]["when"] == 1, "filmed the day after the B-roll: a point for the studio")
vo_site = mode_of(SITE, HACK, POUR, TILE)
ok(not vo_site["on"] and vo_site["site"] == [SITE] and vo_site["footage"][str(SITE)]["where"] == "site",
   f"talking head on site + B-roll: the old way ({vo_site['why']})")
ok(vo_site["footage"][str(SITE)]["signals"]["when"] == -1, "filmed within the hour of the B-roll: a point for the site")
vo_bare = mode_of(BARE, HACK, TILE)
ok(vo_bare["on"] and vo_bare["footage"][str(BARE)]["where"] == "studio",
   "the studio clip with no setting and no time is still the studio: its sound and its camera say so")
ok(not mode_of(STUDIO)["on"] and mode_of(STUDIO)["why"] == "no B-roll to lay over the voice",
   "a studio talking head alone: the old way")
ok(not mode_of(HACK, TILE)["on"], "B-roll alone: the old way")
os.environ["STUDIO_EDIT_VOICEOVER"] = "0"
import importlib
importlib.reload(settings)
ok(not mode_of(STUDIO, HACK)["on"], "STUDIO_EDIT_VOICEOVER=0: always the old way")
os.environ.pop("STUDIO_EDIT_VOICEOVER")
importlib.reload(settings)

# ── 2. The plan: B-roll laid over the voice ───────────────────────────────────
print("\n== 2. The plan")
ends = [i for i, w in enumerate(WORDS) if w["word"].endswith(".")]
starts = [0] + [i + 1 for i in ends[:-1]]
SENT = list(zip(starts, ends))   # the seven sentences: (first word, last word)


def talk(a, b, cid=STUDIO):
    """A talking-head cut from sentence a to sentence b (as Claude plans: on the transcript's times)."""
    return {"clip_id": cid, "start": WORDS[SENT[a][0]]["start"], "end": WORDS[SENT[b][1]]["end"], "why": f"s{a}-s{b}"}


def roll(cid, a, b, why):
    return {"clip_id": cid, "start": a, "end": b, "why": why}


VOICE_CUTS = [talk(0, 0), talk(1, 2), talk(3, 4), talk(5, 6)]
PLAN_CUTS = [talk(0, 0), roll(HACK, 1.0, 5.0, "the old tiles hacked off"), talk(1, 2),
             roll(POUR, 0.5, 5.0, "the screed poured"), talk(3, 4), roll(TILE, 1.0, 5.5, "the tiles going down"),
             talk(5, 6)]
BASE = {"hook_text": "Floors *level* again", "key_words": ["screed"], "graphics": [],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "A voice-over.", "notes_addressed": [], "missing_shots": []}
e = {**json.loads(json.dumps(BASE)), "cuts": json.loads(json.dumps(PLAN_CUTS))}
voiceover.split_plan(e, vo, {"full_screen": [], "side": "auto"})
ok([(c["clip_id"], c["start"], c["end"]) for c in e["cuts"]] == [(c["clip_id"], c["start"], c["end"]) for c in VOICE_CUTS],
   "the voice is the talking-head cuts, in order, exactly as planned")
ok([(b["clip_id"], b["at_clip"], b["at"]) for b in e["broll"]]
   == [(HACK, STUDIO, VOICE_CUTS[1]["start"]), (POUR, STUDIO, VOICE_CUTS[2]["start"]), (TILE, STUDIO, VOICE_CUTS[3]["start"])],
   "each B-roll cut covers from the start of the talking-head cut after it")
e2 = {**json.loads(json.dumps(BASE)), "cuts": [talk(0, 6), roll(HACK, 1, 4, "x")]}
voiceover.split_plan(e2, vo, {"full_screen": [{"clip_id": STUDIO, "start": 5.0, "end": 7.0}], "side": "right"})
ok(e2["broll"][0]["place"] == "end" and e2["pip_asks"][0]["kind"] == "full" and e2["pip_side"] == "right",
   "B-roll after the last voice cut covers its end; the plan's picture field becomes asks")
ok(voiceover.note_asks("Full screen here please") == {"kind": "full", "whole": False}
   and voiceover.note_asks("move me to the left") == {"side": "left"}
   and voiceover.note_asks("Put the window on the right side") == {"side": "right"}
   and voiceover.note_asks("show the site footage here, not full screen")["kind"] == "window"
   and voiceover.note_asks("No window, just me the whole way") == {"kind": "full", "whole": True}
   and voiceover.note_asks("Cut the pause here") is None and voiceover.note_asks("Louder music") is None,
   "Bryan's notes read: full screen here, move me to the left/right, the window back, no window")

# ── 3. A voice-over draft ─────────────────────────────────────────────────────
print("\n== 3. A voice-over draft")


class Fake:
    """Claude, stubbed: the edit plan given, an empty sentence check, a clean read."""
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
    return media.tone_track(out_dir / "tone.mp3") if not (out_dir / "tone.mp3").exists() else out_dir / "tone.mp3", \
        {"title": "Test Tone", "artist": "ffmpeg", "recording_id": "t1"}


CURRENT = {}
transcribe.transcribe = lambda wav: {"words": editor.timeline(CURRENT["cuts"], CL)}
music.allowed_for = lambda b: True
orig_render, orig_overlay = editor.render_cut, editor.render_overlay


def counting_render(cuts, clips, out):
    CURRENT["cuts"] = cuts
    return orig_render(cuts, clips, out)


OVERLAYS = []


def spy_overlay(chunks, hook, total, out, graphics=(), *a, **kw):
    OVERLAYS.append({"chunks": chunks, "total": total, "pip": kw.get("pip"), "out": out})
    return orig_overlay(chunks, hook, total, out, graphics, *a, **kw)


editor.render_cut, editor.render_overlay = counting_render, spy_overlay


def production(ids, title, plan_cuts, picture=True):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": title, "hook": "h",
                                    "angle": "x", "beats": [],
                                    "segments": [{"clip_id": i, "start": 0, "end": CL[i]["duration"], "purpose": "a"} for i in ids],
                                    "caption": "cap", "est_seconds": 25, "cta": "Save this for your reno."}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    pid = catalog.make_production(conn, idea)
    plan = json.loads(conn.execute("select plan from productions where id=?", (pid,)).fetchone()[0])
    plan["cta"] = "Save this for your reno."
    conn.execute("update productions set plan=? where id=?", (json.dumps(plan), pid))
    conn.commit()
    plan = {**json.loads(json.dumps(BASE)), "cuts": json.loads(json.dumps(plan_cuts))}
    return pid, Fake({**plan, "picture": {"full_screen": [], "side": "auto"}} if picture else plan)


def row(pid):
    return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())


def frame(video, t, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(float)


SHOTS = P / "frames"
SHOTS.mkdir(exist_ok=True)
pid, fake = production([STUDIO, HACK, POUR, TILE], "Voice-over", PLAN_CUTS)
anthropic.Anthropic = lambda **kw: fake
t0 = time.time()
final = editor.edit(conn, pid, client=fake, find_track=fake_track)
print(f"  edit took {time.time() - t0:.1f}s")
r = row(pid)
edl = json.loads(r["edl"])
pip = edl.get("pip")
print("  pip:", json.dumps({k: v for k, v in (pip or {}).items() if k != "busy"})[:900])
print("  checks:", [c["did"] for c in json.loads(r["qa"])["checks"]])
ok(pip and edl["voiceover"]["on"], "the draft is a voice-over")
prompt = fake.calls[0]["system"]
user = fake.calls[0]["messages"][0]["content"]
ok("This post is a voice-over" in prompt and "talking head, filmed in the studio" in user
   and "B-roll: site footage" in user, "the plan prompt says it's a voice-over and what each clip is")
ok("picture" in fake.calls[0]["output_config"]["format"]["schema"]["properties"], "the plan's schema has the picture field")

probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final)],
                                  capture_output=True, text=True, check=True).stdout)
vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
au = next(s for s in probe["streams"] if s["codec_type"] == "audio")
ok(vs["codec_name"] == "h264" and au["codec_name"] == "aac" and (vs["width"], vs["height"]) == (1080, 1920),
   f"the draft is 9:16 H.264/AAC ({vs['codec_name']} {vs['width']}x{vs['height']}, {au['codec_name']})")
words = editor.timeline(edl["cuts"], CL, sound=True)
hook_end, close_start = pip["hook"][1], pip["close"][0]
first_end = next(w for w in words if w["word"].endswith("."))
last_start = words[-(SENT[6][1] - SENT[6][0] + 1)]
ok(abs(hook_end - first_end["end"]) < 0.45 and abs(close_start - last_start["start"]) < 0.45,
   f"full screen for the hook (0-{hook_end:.2f}s: his first sentence ends {first_end['end']:.2f}s) and the close "
   f"(from {close_start:.2f}s: his last sentence starts {last_start['start']:.2f}s)")
spans = pip["spans"]
mid = (pip["shots"][0]["start"] + pip["shots"][0]["end"]) / 2
ok(spans and all(hook_end - 0.04 <= a and b <= close_start + 0.04 for a, b in spans),
   f"his window is up only between them: {spans}")
middle = close_start - hook_end
covered = sum(b - a for a, b in spans)
ok(covered >= 0.7 * middle, f"the site footage covers most of the middle ({covered:.1f}s of {middle:.1f}s)")
cut_points = voiceover.edges(words) + [hook_end, close_start]
off = [(t, min(abs(t - p) for p in cut_points)) for s in pip["shots"] for t in (s["start"], s["end"])]
ok(all(d <= 1.5 / 30 for _, d in off), f"every shot changes at the end of a sentence or clause ({[round(d, 3) for _, d in off]})")
anchors = {HACK: words[next(i for i, w in enumerate(words) if w["src"] >= VOICE_CUTS[1]["start"] - 0.05)]["start"],
           POUR: words[next(i for i, w in enumerate(words) if w["src"] >= VOICE_CUTS[2]["start"] - 0.05)]["start"],
           TILE: words[next(i for i, w in enumerate(words) if w["src"] >= VOICE_CUTS[3]["start"] - 0.05)]["start"]}
placed = {}
for s_ in pip["shots"]:
    placed.setdefault(s_["clip_id"], [s_["start"], s_["end"]])[1] = s_["end"]
ok([s_["clip_id"] for s_ in pip["shots"]] == [HACK, POUR, TILE]
   and all(c in placed and placed[c][0] <= anchors[c] + 1.3 and placed[c][1] >= anchors[c] + 0.5 for c in anchors),
   f"each site shot is up as his words about it start, in the planned order: "
   f"{[(CL[c]['title'], [round(x, 2) for x in placed.get(c, [])], round(anchors[c], 2)) for c in anchors]}")
import framing


def med(a, b, x0, y0, x1, y1):
    """How far two frames are apart in a region (median of the per-pixel difference, 0-255)."""
    return float(np.median(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean(axis=2)))


def luma(video, t, out, vf="null"):
    """A frame's brightness (the Y plane as stored: no colour matrix, which a test pattern tags and a draft doesn't),
    as three equal channels so it compares like a frame."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                    "-vf", f"{vf},format=gray", str(out)], check=True)
    return np.repeat(np.asarray(Image.open(out).convert("L")).astype(float)[:, :, None], 3, axis=2)


def broll_frame(s_, t, out):
    """What a shot's clip shows at draft time t, framed for the Reel as the draft frames it (brightness)."""
    return luma(CL[s_["clip_id"]]["video_path"], s_["src"] + t - s_["start"], out,
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")


def face_in(img, b):
    grey = np.asarray(Image.fromarray(img.astype(np.uint8)).convert("L"))
    x, y, w, h = b
    return bool(framing.find_faces(grey[y:y + h, x:x + w]))


box = pip["box"]
bx, by, bw, bh = box
# Each shot shows its own clip from its in-point, with him in the window.
for k, s_ in enumerate(pip["shots"]):
    t = (s_["start"] + s_["end"]) / 2
    got = luma(final, t, SHOTS / f"shot{k}.png")
    want = broll_frame(s_, t, SHOTS / f"want{k}.png")
    away = med(got, want, 80, 300, 1000, 800)
    win = med(got, want, bx + 30, by + 30, bx + bw - 30, by + bh - 30)
    ok(away < 8 and win > 30 and face_in(got, box),
       f"shot {k + 1} at {t:.1f}s: {CL[s_['clip_id']]['title']} from {s_['src']:.1f}s full screen ({away:.0f} levels off), "
       f"his face in the window ({win:.0f} levels from the footage there)")
    if k == 0:
        got0, want0 = got, want
corner = med(got0, want0, bx, by, bx + 5, by + 5)
edge = med(got0, want0, bx + 170, by + 3, bx + 190, by + 8)
ok(corner < 8 and edge > 30, f"the window's corners are rounded: the footage shows at its very corner ({corner:.0f} levels "
   f"off it), not along its top edge ({edge:.0f})")
ok(box[1] + box[3] <= 1540 and box[0] >= 48 and box[0] + box[2] <= 1080 - 120 and box[2] == 360,
   f"the window {box} is a third of the width, in a bottom corner above Instagram's caption area and clear of its buttons")
ov = OVERLAYS[-1]
ok(ov["pip"] and ov["pip"]["side"] == pip["side"] and ov["pip"]["spans"] == spans, "the captions know where the window is")
beside = [c for c in ov["chunks"] if any(c["start"] < b and c["end"] > a for a, b in spans)]
ok(beside and all(c.get("lit") for c in beside), f"the {len(beside)} captions beside the window get the stronger glow and shade "
   "(site footage is busy)")
said = [c for c in ov["chunks"] if any(a + 0.2 < (c["start"] + c["end"]) / 2 < b - 0.2 for a, b in spans)
        and c["end"] - c["start"] > 0.3]
t_cap = (said[len(said) // 2]["start"] + said[len(said) // 2]["end"]) / 2
f_cap = frame(final, t_cap, SHOTS / "caption.png")
lit = (f_cap[1240:1500] > 225).all(axis=2)
text_beside = lit[:, bx + bw + 20:960].mean() if pip["side"] == "left" else lit[:, 96:bx - 20].mean()
text_over = lit[:, bx + 20:bx + bw - 20].mean()
ok(text_beside > 0.002 and text_over < 0.001,
   f"the caption \"{said[len(said) // 2]['text']}\" at {t_cap:.1f}s sits beside the window, not over it "
   f"({text_beside:.2%} beside, {text_over:.2%} over)")
ok(pip.get("site_gain_db") is not None and json.loads(r["qa"])["sound"].get("site_gain_db") is not None,
   f"the site's sound is mixed in under him ({pip['site_gain_db']:+.1f} dB on the clips' own)")

# The read is told it's on purpose; the watch-through doesn't call the window a problem
read_call = next(c for c in fake.calls if "Place names stay internal" in c.get("system", ""))
material = " ".join(x.get("text", "") for x in read_call["messages"][0]["content"] if isinstance(x, dict))
ok("This is a voice-over, on purpose" in material and "deliberate" in material and f"bottom-{pip['side']}" in material,
   "Studio's read is told the window is deliberate, and where it is")
review = json.loads(r["review"] or "{}")
ok(not any("face" in (f.get("what") or "").lower() for f in review.get("fixes", [])),
   f"the watch-through doesn't flag the window ({[f.get('what') for f in review.get('fixes', [])]})")
ok("small window" in edl["summary"], f"Review's \"What Studio did\" says so: {edl['summary'][-160:]}")

# ── 4. The voice is cut exactly as a plain talking-head edit of the same footage ──
print("\n== 4. The same voice as a plain talking-head edit")
pid_b, fake_b = production([STUDIO], "Plain", VOICE_CUTS)
anthropic.Anthropic = lambda **kw: fake_b
final_b = editor.edit(conn, pid_b, client=fake_b, find_track=fake_track)
rb = row(pid_b)
edl_b = json.loads(rb["edl"])
key = lambda cuts: [(c["clip_id"], c["start"], c["end"], c.get("zoom"), c.get("crop_x"), c.get("crop_y")) for c in cuts]  # noqa: E731
ok("voiceover" not in edl_b and "pip" not in edl_b, "a studio talking head with no B-roll: no window, edited the old way")
ok(key(edl["cuts"]) == key(edl_b["cuts"]), f"the voice-over's cut list is the plain edit's, piece for piece ({len(edl['cuts'])} pieces)")
ok([(w["word"], round(w["start"], 3)) for w in editor.timeline(edl["cuts"], CL)]
   == [(w["word"], round(w["start"], 3)) for w in editor.timeline(edl_b["cuts"], CL)], "every word at the same moment")
ok(edl["close"] == edl_b["close"] and abs(r["duration"] - rb["duration"]) < 0.01,
   f"the same close and length ({r['duration']:.2f}s)")
qa_a, qa_b = json.loads(r["qa"]), json.loads(rb["qa"])
ok(qa_a["joins"] == qa_b["joins"], f"the same joins checked for clipped words ({qa_a['joins']})")
ok("This post is a voice-over" not in fake_b.calls[0]["system"], "the plain edit's prompt is the old one")
t_hook, t_close = (2.6 + hook_end) / 2, (close_start + pip["close"][1]) / 2
f_hook, b_hook = frame(final, t_hook, SHOTS / "hook.png"), frame(final_b, t_hook, SHOTS / "plain_hook.png")
f_close, b_close = frame(final, t_close, SHOTS / "close.png"), frame(final_b, t_close, SHOTS / "plain_close.png")
f_mid2, b_mid = frame(final, mid, SHOTS / "middle.png"), frame(final_b, mid, SHOTS / "plain_middle.png")
d_hook, d_close, d_mid = med(f_hook, b_hook, 0, 0, 1080, 1920), med(f_close, b_close, 0, 0, 1080, 1920), med(f_mid2, b_mid, 0, 0, 1080, 1920)
ok(d_hook < 4 and d_close < 4 and d_mid > 25,
   f"the hook ({t_hook:.1f}s) and the close ({t_close:.1f}s) are him full screen, as in the plain edit ({d_hook:.0f}, "
   f"{d_close:.0f} levels apart); the middle ({mid:.1f}s) is not ({d_mid:.0f})")

# ── 5. Bryan's notes: full screen here, move me to the other side ─────────────
print("\n== 5. Notes on the picture")
side1 = pip["side"]
other = "left" if side1 == "right" else "right"
shot2 = pip["shots"][1]
t_note = round((shot2["start"] + shot2["end"]) / 2, 2)
asked = voiceover.stretch_at(edl, CL, t_note)
n_full = catalog.add_feedback(conn, "production", pid, "Full screen here, I want them to see me say this", t_note)
n_side = catalog.add_feedback(conn, "production", pid, f"Move me to the {other}")
catalog.set_production_status(conn, pid, "revising")
fake2 = Fake({**json.loads(json.dumps(BASE)), "cuts": json.loads(json.dumps(PLAN_CUTS)),
              "picture": {"full_screen": [], "side": "auto"},
              "notes_addressed": [{"note_id": n_full, "how": "full screen", "kind": "taste", "rule": "", "same_rule": 0, "animations": "none"},
                                  {"note_id": n_side, "how": "moved", "kind": "taste", "rule": "", "same_rule": 0, "animations": "none"}]})
anthropic.Anthropic = lambda **kw: fake2
final2 = editor.edit(conn, pid, client=fake2, find_track=fake_track)
r2 = row(pid)
edl2 = json.loads(r2["edl"])
pip2 = edl2["pip"]
checks2 = [c["did"] for c in json.loads(r2["qa"])["checks"]]
print("  v2 pip:", {k: pip2[k] for k in ("side", "full", "spans")}, "| asks:", edl2.get("pip_asks"))
print("  checks:", checks2)
ok(r2["version"] == 2 and pip2["side"] == other, f"\"Move me to the {other}\": the window is on the {other} in v2")
ok(pip2["full"] and any(a <= asked[0] + 0.5 and b >= asked[1] - 0.5 for a, b in pip2["full"]),
   f"\"Full screen here\" at {t_note:.1f}s: that sentence ({asked[0]:.1f}-{asked[1]:.1f}s) is full screen ({pip2['full']})")
ok(not any(a < asked[1] - 0.1 and b > asked[0] + 0.1 for a, b in pip2["spans"]), "no window and no site footage over it")
ok(any("showed you full screen" in c for c in checks2) and any(f"moved your window to the {other}" in c for c in checks2),
   "the draft's checks say what Studio did with the notes")
ok(key(edl2["cuts"]) == key(edl["cuts"]), "the voice is cut the same as v1")
f_full = frame(final2, (asked[0] + asked[1]) / 2, SHOTS / "v2_full.png")
spans2 = [s_ for s_ in pip2["spans"] if s_[1] - s_[0] > 1.0]
t_win = (spans2[0][0] + spans2[0][1]) / 2
f_win = frame(final2, t_win, SHOTS / "v2_window.png")
bx2, by2, bw2, bh2 = pip2["box"]
f_plain = frame(final_b, (asked[0] + asked[1]) / 2, SHOTS / "plain_full.png")
ok(med(f_full, f_plain, 0, 0, 1080, 1920) < 4, "the frame there is him, full screen (as in the plain edit)")
ok(face_in(f_win, pip2["box"]) and not face_in(f_win, box), f"elsewhere the window is on the {other} ({pip2['box']})")
prompt2 = " ".join(x for x in [fake2.calls[0]["messages"][0]["content"]] if isinstance(x, str))
ok("Previous picture (a voice-over" in prompt2 and "Full screen here" in prompt2,
   "the re-plan is told the previous picture and Bryan's notes")
rr = editor.rerender(conn, pid, find_track=fake_track)
edl3 = json.loads(row(pid)["edl"])
ok({k: edl3["pip"][k] for k in ("side", "full", "spans", "shots")} == {k: pip2[k] for k in ("side", "full", "spans", "shots")},
   "a re-render keeps the window, its side, the full-screen stretch and every shot")

# ── 6. Studio's own fixes: the read's "layout" action ─────────────────────────
print("\n== 6. The read's layout fix")
a_, b_ = pip2["full"][0]
new, res = fix_actions.apply(edl2, CL, [{"n": 1, "action": {"type": "layout", "field": "window", "start": a_, "end": b_,
                                                            "from": "", "to": "", "item": None, "amount": None, "reason": ""}}])
lay = voiceover.layout(new, new["cuts"], CL, editor.timeline(new["cuts"], CL, sound=True), pip2["close"][1],
                       json.loads(r2["plan"]))
ok(res[0]["ok"] and not lay["full"] and any(s_[0] <= a_ + 0.3 and s_[1] >= b_ - 0.3 for s_ in lay["spans"]),
   f"\"window\" puts the site footage and his window back there ({res[0].get('did')})")
new2, res2 = fix_actions.apply(new, CL, [{"n": 1, "action": {"type": "layout", "field": "full", "start": 1.0 + hook_end,
                                                             "end": None, "from": "", "to": "", "item": None,
                                                             "amount": None, "reason": ""}}])
lay2 = voiceover.layout(new2, new2["cuts"], CL, editor.timeline(new2["cuts"], CL, sound=True), pip2["close"][1],
                        json.loads(r2["plan"]))
ok(res2[0]["ok"] and lay2["full"] and lay2["full"][0][0] <= hook_end + 1.0 <= lay2["full"][0][1],
   f"\"full\" at a moment: the sentence said there goes full screen ({lay2['full']})")
_, res3 = fix_actions.apply(edl2, CL, [{"n": 1, "action": {"type": "layout", "field": other, "start": None, "end": None,
                                                           "from": "", "to": "", "item": None, "amount": None, "reason": ""}}])
ok(not res3[0]["ok"] and "already" in res3[0]["why"], f"moving the window where it is already is refused: {res3[0]['why']!r}")
_, res4 = fix_actions.apply(edl_b, CL, [{"n": 1, "action": {"type": "layout", "field": "full", "start": 3.0, "end": 5.0,
                                                            "from": "", "to": "", "item": None, "amount": None, "reason": ""}}])
ok(not res4[0]["ok"] and "isn't a voice-over" in res4[0]["why"], "a draft that isn't a voice-over has no window to change")
import self_fix
ok("layout" in draft_review.REEL_ACTIONS and {"full", "window", "left", "right"} <= set(draft_review.ACTION_FIELDS)
   and "layout: only in a voice-over" in draft_review._REEL_ACTIONS
   and self_fix._method({"action": {"type": "layout"}, "editor_can_fix": True}, []) == "patch",
   "the read can ask for it, and Studio's rounds make it as a patch")

# ── 7. On site: edited exactly as before ──────────────────────────────────────
print("\n== 7. A talking head filmed on site, as before")
old = load_before("editor_ca2fe96.py", "editor_old")
SITE_CUTS = [talk(0, 0, SITE), talk(1, 2, SITE), talk(3, 4, SITE), talk(5, 6, SITE)]
o_render = old.render_cut


def old_render(cuts, clips, out):
    CURRENT["cuts"] = cuts
    return o_render(cuts, clips, out)


old.render_cut = old_render
# In the look the editor before made (gallery): Site Notes (2026-10-22, suite_look.py) changed the look on purpose
# since, and the prompt's three lines on titles, key words and cards with it (LOOK_WORDING); nothing else.
settings.TITLE_STYLE = "gallery"
pid_o, fake_o = production([SITE], "On site (before)", SITE_CUTS, picture=False)
anthropic.Anthropic = lambda **kw: fake_o
final_o = old.edit(conn, pid_o, client=fake_o, find_track=fake_track)
pid_n, fake_n = production([SITE], "On site (now)", SITE_CUTS, picture=False)
anthropic.Anthropic = lambda **kw: fake_n
final_n = editor.edit(conn, pid_n, client=fake_n, find_track=fake_track)
settings.TITLE_STYLE = "site"
eo, en = json.loads(row(pid_o)["edl"]), json.loads(row(pid_n)["edl"])
ok(eo == en, "on site, talking head only: the edit plan is exactly what the editor before made"
   + ("" if eo == en else f" (differs in {sorted(k for k in set(eo) | set(en) if eo.get(k) != en.get(k))})"))
LOOK_WORDING = [
    ('are set in a serif with at most one word in italic: mark that word with *asterisks*, e.g. "Floors *stripped*", '
     '"The *screed* goes first". Only a real key word',
     "pick out at most one word (Studio sets it apart in the look's accent: brass, or a serif italic): mark that word "
     'with *asterisks*, e.g. "Floors *stripped*", "The *screed* goes first". Only a real key word'),
    ("the captions set in italic for emphasis", "the captions pick out for emphasis (in the accent)"),
    ('style "card" fills the screen with a headline and up to 3 short points (good for steps, lists or the key '
     'takeaway); style',
     'style "card" shows a headline and up to 3 short points (good for steps, lists, numbers or the key takeaway: a '
     'point with one number, as "Older HDB 30A", is drawn as a bar; "Now: ..." and "The plan: ..." as labelled pairs); '
     'style'),
]
before_prompt = fake_o.calls[0]["system"]
for was, now in LOOK_WORDING:
    before_prompt = before_prompt.replace(was, now)
ok(before_prompt == fake_n.calls[0]["system"], "the same prompt (but for Site Notes' three lines on titles, key words, cards)")
diffs = []
for t in (0.8, 5.0, 10.0, row(pid_n)["duration"] - 0.5):
    a1 = frame(final_o, t, SHOTS / f"old_{t:.1f}.png")
    a2 = frame(final_n, t, SHOTS / f"new_{t:.1f}.png")
    diffs.append(round(float(np.abs(a1 - a2).mean()), 2))
ok(max(diffs) < 1.5 and abs(row(pid_o)["duration"] - row(pid_n)["duration"]) < 0.001,
   f"and the same picture ({diffs} levels apart at four moments) and length")
# On site with B-roll: the B-roll is cut in between his sentences, as before (the plan only, no render)
MIX = [talk(0, 0, SITE), roll(HACK, 1.0, 4.0, "x"), talk(1, 2, SITE)]
pid_m, fake_m = production([SITE, HACK], "On site with B-roll", MIX, picture=False)
prod_m = row(pid_m)
clips_m = {i: CL[i] for i in (SITE, HACK)}
plan_new = editor.plan_edit(conn, prod_m, clips_m, client=fake_m)
plan_old = old.plan_edit(conn, prod_m, clips_m, client=fake_m)
ok(plan_new == plan_old and "voiceover" not in plan_new and [c["clip_id"] for c in plan_new["cuts"]] == [SITE, HACK, SITE],
   "on site with B-roll: the same plan as before, the B-roll in between his sentences")
ok(editor._cut(json.loads(json.dumps(plan_new)), clips_m) == old._cut(json.loads(json.dumps(plan_old)), clips_m),
   "and the same cut")
plain_new = editor._cut({"cuts": json.loads(json.dumps(VOICE_CUTS)), "hook_text": "x"}, CL)
plain_old = old._cut({"cuts": json.loads(json.dumps(VOICE_CUTS)), "hook_text": "x"}, CL)
ok(plain_new == plain_old, "a studio talking head with no B-roll is cut as before")

# ── 8. DaVinci Resolve ────────────────────────────────────────────────────────
print("\n== 8. Resolve")
import resolve_export


def small_mov(chunks, hook, total, out, graphics=(), *a, **kw):
    """The hook, cards and captions layers (the ending suite renders those for real): a stand-in, so this suite's
    folder stays small (each real ProRes layer is a few hundred megabytes)."""
    OVERLAYS.append({"chunks": chunks, "total": total, "pip": kw.get("pip"), "out": out, "fmt": kw.get("fmt")})
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black@0.0:s=64x64:d=0.1,format=yuva444p",
                    "-c:v", "prores_ks", "-profile:v", "4444", str(out)], check=True)


editor.render_overlay = small_mov
folder = resolve_export.build(conn, pid)
editor.render_overlay = spy_overlay
cap_layer = OVERLAYS[-1]
ok(cap_layer["fmt"] == "mov" and cap_layer["pip"] and cap_layer["pip"]["side"] == pip2["side"]
   and all(c.get("lit") for c in cap_layer["chunks"] if any(c["start"] < b and c["end"] > a for a, b in pip2["spans"])),
   "the captions layer has them beside the window too")
xml = (folder / "timeline.fcpxml").read_text()
broll_clips = re.findall(r'<asset-clip ref="(a\d+)" lane="1" offset="[^"]+" name="([^"]+)"', xml)
ok(len(broll_clips) == len(pip2["shots"]) and {n for _, n in broll_clips} <= {"hack", "pour", "tile"},
   f"the site shots are on V2 as clips from the originals ({[n for _, n in broll_clips]})")
ok((folder / "window.mov").exists() and 'name="Your window over the site footage"' in xml,
   "his window is a layer of its own above them")
wf = SHOTS / "window_layer.png"
mid2 = (spans2[0][0] + spans2[0][1]) / 2
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{mid2:.2f}", "-i", str(folder / "window.mov"), "-frames:v", "1",
                "-pix_fmt", "rgba", str(wf)], check=True)
alpha = np.asarray(Image.open(wf).convert("RGBA"))[:, :, 3].astype(float)
ok(alpha[by2 + 100:by2 + bh2 - 100, bx2 + 60:bx2 + bw2 - 60].mean() > 250 and alpha[200:800, 100:900].mean() < 5,
   "the window layer is solid in the window and clear everywhere else")
how = (folder / "HOW TO OPEN.txt").read_text()
ok("site footage" in how and "V2" in how, "HOW TO OPEN says where the site footage is")
files = [n for n, _ in resolve_export.download_files(conn, row(pid))]
ok(any(n.endswith("/Footage/hack.mp4") for n in files) and any(n.endswith("/Footage/studio.mp4") for n in files),
   "the zip carries the B-roll originals too, to relink")
shutil.rmtree(folder, ignore_errors=True)  # its ProRes layers are most of a gigabyte

# ── 9. The site's sound, low under his voice ──────────────────────────────────
print("\n== 9. The site's sound")
mx = P / "mix"
mx.mkdir(exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(M / "voice_room.wav"), "-f", "lavfi", "-i",
                "color=c=gray:s=1080x1920:r=30:d=8", "-filter_complex", "[0:a]atrim=0:5,apad=whole_dur=8[a]", "-map", "1:v",
                "-map", "[a]", "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(mx / "cut.mp4")],
               check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:d=8", "-af", "volume=-12dB",
                "-ac", "2", "-ar", "48000", "-c:a", "pcm_f32le", str(mx / "site.wav")], check=True)
gain = voiceover.site_gain(mx / "site.wav")
words5 = [w for w in WORDS if w["end"] <= 5.0]


def levels(out):
    """His voice (while he talks) and the site's sound (its 1 kHz tone, picked out of the mix), in dB."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, "<f4").astype(np.float64)
    talk_ = x[int(0.5 * 16000):int(4.5 * 16000)]
    spec = np.fft.rfft(x[int(5.5 * 16000):int(7.5 * 16000)])
    freq = np.fft.rfftfreq(int(2.0 * 16000), 1 / 16000)
    spec[(freq < 950) | (freq > 1050)] = 0
    tone = np.fft.irfft(spec, n=int(2.0 * 16000))
    return 20 * np.log10(np.sqrt(np.mean(talk_ ** 2))), 20 * np.log10(np.sqrt(np.mean(tone ** 2)) + 1e-9)


for label, music_file in (("no music", None), ("with music", media.tone_track(mx / "tone.mp3"))):
    out = mx / f"mix_{label.replace(' ', '_')}.mp4"
    rep = editor.mix_final(mx / "cut.mp4", None, music_file, -40.0 if music_file else -16.0, 8.0, out, words5,
                           under=(mx / "site.wav", gain))
    v_db, s_db = levels(out)
    ok(rep.get("site_gain_db") == gain and 12.0 <= v_db - s_db <= 26.0,
       f"{label}: the site's sound plays {v_db - s_db:.1f} dB under his voice (aim {voiceover.SITE_BELOW:.0f})")

# ── 10. Where people learn it ─────────────────────────────────────────────────
print("\n== 10. Docs, help and the planner")
root = Path(settings.__file__).parent
readme = (root / "README.md").read_text()
ok("### Voice-overs: film on site, talk in the studio" in readme and "STUDIO_EDIT_VOICEOVER" in readme
   and "full screen here" in readme, "README explains it (and how to ask for full screen or the other side)")
help_js = (root / "cloud" / "planner" / "help.js").read_text()
ok("full screen here" in help_js and "talk later" in help_js and "film the work with no talking" in help_js,
   "help.js teaches it on a draft, an idea and Today on site")
import help_desk
ok("voice" in help_desk.PAGE_WORDS["draft"] and "studio" in help_desk.PAGE_WORDS["today"], "Ask Studio finds it")
for f in (root / "plan.py", root / "cloud" / "supabase" / "functions" / "_shared" / "prompts.ts"):
    ok("filmed in Bryan's studio after the site day" in f.read_text(),
       f"{f.name}: the shot list offers B-roll only on site, the talking in the studio")
import updater
ok("voiceover" in updater._MODULES.split(", "), "the updater checks the new module imports")

finish()
