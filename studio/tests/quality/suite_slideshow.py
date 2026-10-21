"""Slideshow Reels (2026-10-21): a carousel's slides made into a 9:16 Reel with music, reviewed and posted like
any Reel. Generated PNG slides (media.slide_pngs: one colour each, so a frame tells which slide is up), Claude,
Epidemic Sound and Instagram stubbed; HyperFrames, ffmpeg and the mix are real. A carousel's own layout is held
against carousel.py as it was before slideshows (fixtures/before/carousel_9d1f570.py.xz).
Run: python tests/quality/suite_slideshow.py (from studio/)"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish, load_before
import media
os.environ.update(EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers", STUDIO_SELF_FIX_ROUNDS="1",
                  STUDIO_SERVICE_WORKER="0", STUDIO_DASHBOARD_URL="https://studio.example.com")
import numpy as np
from PIL import Image
import settings, catalog, editor, music, anthropic, restyle, posting, draft_review, final_check, self_fix, usage
import slideshow, carousel, cleanup, usage_report, updater, worker

settings.ensure_folders(); conn = catalog.connect()

# ── Stubs: Claude (the read, the notes), Epidemic Sound ───────────────────────
READ = {"verdict": "Ready to post", "scores": {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")},
        "fixes": [], "works": ["The cover stops the scroll"]}
MUSIC_FIX = {"what": "Turn the music down a little", "why": "It's a touch loud for a calm post.", "start": None,
             "end": None, "area": "sound", "when": "this_draft", "kind": "none", "editor_can_fix": True,
             "rule": "Keep a slideshow's music calm", "same_rule": 0, "illustrate": "",
             "action": {"type": "music_level", "start": None, "end": None, "from": "", "to": "", "item": None,
                        "field": "", "amount": -3, "reason": ""}, "for_bryan": ""}
class Fake:
    def __init__(self): self.calls, self.reads, self.change = [], [], None
    @property
    def beta(self): return NS(messages=NS(stream=self.stream, create=self.create))
    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "Place names stay internal" in sysp:
            text = json.dumps(self.reads.pop(0) if self.reads else READ)
        elif "You adjust a slideshow Reel" in sysp:
            text = json.dumps(self.change)
        else:
            raise AssertionError("an unexpected Claude call: " + sysp[:80])
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    def stream(self, **kw):
        msg = self.create(**kw)
        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()
    def kinds(self):
        return ["read" if "Place names stay internal" in c.get("system", "") else "change" for c in self.calls]
FAKE = Fake(); anthropic.Anthropic = lambda **kw: FAKE
TRACKS = []
def fake_track(brief, seconds, out_dir, ref=""):
    TRACKS.append({"brief": brief, "seconds": seconds, "ref": ref})
    p = media.tone_track(out_dir / f"tone{len(TRACKS)}.mp3")  # a soft chord, like a calm track
    return p, {"title": f"Test Tone {len(TRACKS)}", "artist": "ffmpeg", "recording_id": f"t{len(TRACKS)}"}
music.find_track = fake_track

# ── A carousel waiting for review, with generated slides ──────────────────────
COLOURS = [(214, 92, 64), (64, 140, 214), (232, 196, 70), (120, 190, 110), (170, 100, 200)]
SLIDES = [{"role": "cover", "heading": "Hidden costs in a BTO reno", "body": ""},
          {"role": "point", "heading": "Hacking", "body": "Old tiles out before new ones go in."},
          {"role": "point", "heading": "Electrical", "body": "Every new point is wired back to the board, and that takes a full day of work for two men, sometimes longer when the walls are concrete."},
          {"role": "ask", "heading": "Save this", "body": "for your reno."}]
cdir = settings.ROOT / "Renders" / "carousel_1_v1"
files = media.slide_pngs(cdir, [s["heading"] for s in SLIDES], COLOURS)
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "carousel", "title": "Hidden costs", "hook": "Hidden costs",
                                "angle": "x", "beats": [], "segments": [], "caption": "cap", "est_seconds": 30,
                                "cta": "Save this for your reno."}])
idea_id = conn.execute("select max(id) from ideas").fetchone()[0]
plan = {"idea_id": idea_id, "title": "Hidden costs", "brand": "urban_makers", "format": "carousel", "post_type": "knowhow",
        "hook": "Hidden costs in a BTO reno", "purpose": "x", "cta": "Save this for your reno.",
        "slides": [{**s, "image": {"source": "text"}} for s in SLIDES]}
# Slide 2's photo is gone from the Studio machine (the carousel's own PNG has to stand in for it); slide 3's is there.
import slide_images
GONE, PHOTO = "a" * 32, "b" * 32
media.flat_photo(slide_images.PHOTOS / f"{PHOTO}.jpg")
plan["slides"][1]["image"] = {"source": "photo", "photo_id": GONE}
plan["slides"][2]["image"] = {"source": "photo", "photo_id": PHOTO}
PICTURES = [{"n": 1, "source": "text"}, {"n": 2, "source": "photo"}, {"n": 3, "source": "photo"}, {"n": 4, "source": "text"}]
stamp = catalog.now()
conn.execute("""INSERT INTO productions (id, title, brand, format, status, version, idea, source_clips, caption, plan,
                    slides, thumb_path, created_at, updated_at)
                VALUES (1, 'Hidden costs', 'urban_makers', 'carousel', 'ready', 1, 'x', '[]', 'The hidden costs. #reno', ?, ?, ?, ?, ?)""",
             (json.dumps(plan), json.dumps({"version": 1, "slides": plan["slides"], "files": files, "summary": "s",
                                            "pictures": PICTURES}),
              str(cdir / "thumb.jpg"), stamp, stamp))
conn.execute("UPDATE ideas SET status='made', production_id=1 WHERE id=?", (idea_id,))
conn.execute("""INSERT INTO feedback (target, target_id, version, body, status, created_at)
                VALUES ('production', 1, 1, 'Nice cover', 'open', ?)""", (stamp,))
conn.commit()
CID = 1
import hashlib
def carousel_state():
    r = dict(conn.execute("select * from productions where id=1").fetchone())
    return r, [hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files], \
        [dict(x) for x in conn.execute("select * from feedback where target='production' and target_id=1")]
BEFORE = carousel_state()
(settings.ROOT / "style_release.txt").write_text(restyle.RELEASE + "\n")  # no release restart in this test

# ── 1. The press: owner only, one live slideshow per carousel ─────────────────
print("\n== 1. Make a slideshow Reel")
import auth, dashboard
uid = auth.create_user(conn, "bryan@x.com", "Bryan", "correct horse battery", auth.OWNER)
tid = auth.create_user(conn, "michelle@theurbanmakers.com", "Michelle", "correct horse battery", auth.TEAM)
def client(u):
    c = dashboard.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u; s["sv"] = 1
    return c
owner, team = client(uid), client(tid)
d = owner.get(f"/api/production/{CID}").json
ok(d["slideshow"]["why_not"] is None and d["slideshow"]["made"] is None, f"the carousel offers it to Bryan: {d['slideshow']}")
dt = team.get(f"/api/production/{CID}").json
ok(dt.get("slideshow") is None, "the team's carousel page doesn't offer it")
r = team.post(f"/api/production/{CID}/slideshow", json={})
ok(r.status_code == 403 and slideshow.made_from(conn, CID) is None, f"the team can't make one ({r.status_code})")
r = owner.post(f"/api/production/{CID}/slideshow", json={})
SID = (r.json or {}).get("id")
ok(r.status_code == 200 and SID and r.json.get("made") is True, f"Bryan makes one: {r.status_code} {r.json}")
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
ok(row["format"] == "slideshow" and row["status"] == "planned" and row["version"] == 0, "a new production, format slideshow, queued for the editor")
splan = json.loads(row["plan"])
ok(splan["from_carousel"] == CID and splan["idea_id"] == idea_id and splan["cta"] == "Save this for your reno."
   and len(splan["slides"]) == 4, "same idea, its CTA, the carousel's four slides")
ok(all(Path(s["file"]).parent == slideshow.folder(SID) and Path(s["file"]).is_file() for s in splan["slides"]),
   "the slides are copied into its own folder")
r2 = owner.post(f"/api/production/{CID}/slideshow", json={})
ok(r2.status_code == 200 and r2.json["id"] == SID and r2.json.get("made") is False
   and conn.execute("select count(*) from productions where format='slideshow'").fetchone()[0] == 1,
   "pressing it again opens the same one (no duplicate)")
d = owner.get(f"/api/production/{CID}").json
ok(d["slideshow"]["made"]["id"] == SID, "the carousel's page links to it")
# Why it can't be made
conn.execute("update productions set status='producing' where id=1"); conn.commit()
ok("laid out" in (slideshow.can_make(conn, conn.execute("select * from productions where id=1").fetchone()) or ""),
   "a carousel still being laid out: says why")
conn.execute("update productions set status='ready' where id=1"); conn.commit()
ok(owner.post("/api/production/999/slideshow", json={}).status_code == 404, "no such draft: 404")

# ── 2. The worker renders it ──────────────────────────────────────────────────
print("\n== 2. The render")
t0 = time.time()
worker.run_editor(stop_after_one=True)
print(f"  editor took {time.time() - t0:.0f}s")
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
ok(row["status"] == "ready" and row["version"] == 1 and not row["error"], f"in To review as v1 ({row['status']}, {row['error']})")
edl, qa, mus = json.loads(row["edl"]), json.loads(row["qa"]), json.loads(row["music"])
shown = edl["slideshow"]["slides"]
video = Path(row["video_path"])
def probe(p):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,"
                                      "pix_fmt,sample_rate,avg_frame_rate:format=duration", "-of", "json", str(p)],
                                     capture_output=True, text=True).stdout)
info = probe(video)
v = next(s for s in info["streams"] if s["codec_type"] == "video")
a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
ok(v["width"] == 1080 and v["height"] == 1920, f"9:16, 1080x1920 ({v['width']}x{v['height']})")
ok(v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and a and a["codec_name"] == "aac", "H.264 and AAC, like other Reels")
expect = [slideshow.seconds_for(s, cover=k == 0) for k, s in enumerate(SLIDES)]
ok([s["seconds"] for s in shown] == expect, f"times from the words: {expect}")
ok(expect[0] == round(slideshow.MIN_SECONDS + slideshow.COVER_EXTRA, 2) and expect[1] == round(0.3 + 0.06 * len(slideshow.slide_text(SLIDES[1])), 2) == 2.94
   and expect[2] == slideshow.MAX_SECONDS
   and expect[3] == slideshow.MIN_SECONDS, "short = 2.5 s, longer = 0.3 + 0.06 a letter, long = 6 s at most, the cover a little longer")
close = edl["close"]
want = sum(expect) + editor.END_TAIL + editor.END_CARD_SECONDS
dur = float(info["format"]["duration"])
ok(abs(dur - want) < 0.15 and abs(row["duration"] - want) < 0.01, f"duration {dur:.2f}s = slides {sum(expect):.2f} + beat {editor.END_TAIL} + end card {editor.END_CARD_SECONDS}")
ok(close["card"] and close["card"]["ask"] == "Save this for your reno" and close["card"]["handle"], f"the end card with the CTA and handle: {close['card']}")
ok(any("lot of words" in c["did"] for c in qa["checks"]), "the 6-second slide is noted in the checks")
ok(qa.get("lufs") is not None and abs(qa["lufs"] + 14) < 1.5, f"the music's at Instagram's level ({qa.get('lufs')} LUFS)")
ok(mus["source"] == "epidemic" and len(TRACKS) == 1 and "No voice" in TRACKS[0]["brief"]
   and abs(TRACKS[0]["seconds"] - want) < 0.01, f"one Epidemic track for the whole length, briefed for no voice: {TRACKS[0]['brief'][:90]}")

def frame(t, path=video):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
# ── The slides laid out again for the Reel: no Swipe cue, no counter; the carousel's own look unchanged ──
splan = json.loads(row["plan"])
FILES = {x["n"]: x["file"] for x in splan["slides"]}
folder = slideshow.folder(SID)
ok(all(Path(FILES[n]) == folder / f"{n:02d}.png" for n in (1, 3, 4)), "slides 1, 3 and 4 are laid out again for the Reel")
ok(Path(FILES[2]) == folder / "carousel_02.png" and splan["reel_layout"]["fallback"] == [2]
   and any("carousel's own slide 2" in c["did"] for c in qa["checks"]),
   "slide 2's photo is gone: it keeps the carousel's own slide, and the checks say so")
old_carousel = load_before("carousel_9d1f570.py", "carousel_before")  # carousel.py before slideshows (9d1f570^)
cs = carousel.clean_slides(plan["slides"])
pics = [(None, ""), (None, ""), (slide_images.PHOTOS / f"{PHOTO}.jpg", ""), (None, "")]
ok(carousel.page_html(cs, pics) == old_carousel.page_html(cs, pics), "a carousel's own layout is exactly as before")
reel_page = carousel.page_html(cs, pics, reel=True)
ok('class="swipe"' not in reel_page and 'class="num"' not in reel_page and reel_page.count('class="mark"') == 3
   and 'class="swipe"' in carousel.page_html(cs, pics) and carousel.page_html(cs, pics).count('class="num"') == 4,
   "the Reel's layout drops the Swipe cue and the counter, and keeps the Urban Makers mark")
own = carousel.lay_out(conn, cs[:2], P / "own_layout")
def amber(path, box):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)[box[1]:box[3], box[0]:box[2]]
    return int(((a[..., 0] > 200) & (a[..., 1] > 130) & (a[..., 1] < 200) & (a[..., 2] < 90)).sum())
def light(path, box):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)[box[1]:box[3], box[0]:box[2]]
    return int((a.sum(axis=2) > 600).sum())
SWIPE, NUM, MARK = (640, 1180, 1000, 1290), (880, 50, 1000, 120), (80, 50, 420, 120)
ok(amber(own["files"][0], SWIPE) > 200 and amber(FILES[1], SWIPE) == 0,
   f"the Reel's cover has no Swipe cue (amber in that corner: carousel {amber(own['files'][0], SWIPE)}, Reel {amber(FILES[1], SWIPE)})")
ok(light(own["files"][0], NUM) > 100 and light(FILES[1], NUM) == 0 and light(FILES[4 - 1], NUM) == 0,
   f"no slide counter on the Reel's slides (carousel {light(own['files'][0], NUM)}, Reel {light(FILES[1], NUM)})")
ok(light(FILES[1], MARK) > 100 and light(FILES[3], MARK) > 100, "the Urban Makers mark is still at the top")
ok(BEFORE[1] == [hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files], "the carousel's own PNGs aren't touched")

from PIL import Image as _I
_refs = {}
def _small(a):
    im = _I.fromarray(np.asarray(a, np.uint8)).convert("L").resize((54, 68), _I.BILINEAR)
    v = np.asarray(im, float); v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-9)
def which(img, files_=None):
    """The slide up in a frame: the slide picture that best matches what's in the slide's box (and how well)."""
    files_ = files_ or FILES
    x, y, w, h = edl["slideshow"]["box"]
    c = _small(img[y:y + h, x:x + w])
    scores = {}
    for n, f in files_.items():
        if f not in _refs:
            _refs[f] = _small(np.asarray(_I.open(f).convert("RGB")))
        scores[n] = float((c * _refs[f]).sum())
    n = max(scores, key=scores.get)
    return n, scores[n]
first = frame(0.0)
n0, d0 = which(first)
ok(n0 == 1 and d0 > 0.9, f"the first frame is the cover (slide {n0}, match {d0:.2f})")
order = [which(frame(s["start"] + s["seconds"] / 2)) for s in shown]
ok([o[0] for o in order] == [1, 2, 3, 4] and min(o[1] for o in order) > 0.8, f"slides in order: {order}")
mid_fade = shown[1]["start"] + slideshow.FADE / 2
blend = which(frame(mid_fade))[1]
ok(blend < 0.9, f"a crossfade between slides (halfway, it matches neither well: {blend:.2f})")
# Nothing cropped, clear of Instagram's buttons: at full push the slide's own edge is still all there
def slide_box(img):
    bg = img[60, 60]
    diff = np.abs(img - bg).sum(axis=2) > 60
    ys, xs = np.where(diff)
    return xs.min(), ys.min(), xs.max(), ys.max()
late = shown[1]["start"] + slideshow.FADE + 0.1   # slide 2 (the carousel's own, coloured), still pushed in
x0, y0, x1, y1 = slide_box(frame(late))
scale = (x1 - x0 + 1) / slideshow.SLIDE_W
ok(x0 >= slideshow.SAFE_SIDE and x1 <= 1080 - slideshow.SAFE_SIDE and y0 >= slideshow.SAFE_TOP and y1 <= 1920 - slideshow.SAFE_BOTTOM,
   f"the slide stays in the safe zones at full push: x {x0}-{x1}, y {y0}-{y1}")
ok(1.01 < scale <= slideshow.PUSH + 0.01, f"a very slow push-in grows the whole slide ({scale:.3f}x)")
import re as _re
_, anims_ = slideshow.page([{**s, "file": "x"} for s in shown], dur, None, True)
pairs = [tuple(map(float, _re.search(r"\{scale:([\d.]+)\},\{scale:([\d.]+)", a).groups())) for a in anims_ if ".frame" in a]
ok(len(pairs) == 4 and pairs[0][0] == 1 and all(pairs[k][1] == pairs[k + 1][0] for k in range(3)),
   f"in, then back out: each slide starts at the size the one before ended, so a crossfade shows one size {pairs}")
img = frame(late)
edge = img[y0 + 2:y1 - 2, x0:x0 + 3].mean()
ok(edge < 120, f"the slide's own dark edge is in the frame (not cropped): {edge:.0f}")
hb = img[(y0 + y1) // 2, x0:x1]
ok((np.abs(hb - [255, 255, 255]).sum(axis=1) < 90).sum() >= 2, "its white frame is there too")
endf = frame(dur - 0.15)
ok(which(endf)[1] < 0.6, f"the end card is up at the end (no slide: {which(endf)[1]:.2f})")
g = endf[100:1800, 100:980].reshape(-1, 3).mean(axis=0)
ok(abs(g[0] - 0x22) < 30 and abs(g[1] - 0x30) < 30, f"in the brand's green ({g.round()})")
bd = first[100, 540]
ok(abs(bd[0] - 0x2f) < 25 and abs(bd[1] - 0x45) < 25, f"the slides sit on the end card's brand background ({bd})")
def rms(t0, t1, path=video):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{t1 - t0}", "-i", str(path), "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, "<f4")
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
mid_db, end_db = rms(4, 6), rms(dur - 0.15, dur)
ok(mid_db > -30, f"music throughout ({mid_db:.0f} dB)")
ok(end_db < mid_db - 10, f"the music fades out at the end ({mid_db:.0f} -> {end_db:.0f} dB)")
thumb = np.asarray(Image.open(row["thumb_path"]).convert("RGB").resize((1080, 1920))).astype(int)
ok(which(thumb)[0] == 1, "its still in Review is the cover")

# ── 3. Studio's read and the watch-through ───────────────────────────────────
print("\n== 3. Studio's read")
review = json.loads(row["review"] or "{}")
reads = [c for c in FAKE.calls if "Place names stay internal" in c.get("system", "")]
ok(review.get("verdict") == "Ready to post" and len(reads) == 1, "one read, as any Reel")
ctx = reads[0]["messages"][0]["content"][-1]["text"]
ok("slideshow Reel" in ctx and "Hacking" in ctx and "no voice" in ctx.lower(), "the read is told it's a slideshow, with each slide's words and time")
stills = [c for c in reads[0]["messages"][0]["content"] if c.get("type") == "image"]
ok(len(stills) >= 4, f"it looks at stills of the finished Reel ({len(stills)})")
fc = review.get("final_check") or {}
ok(fc.get("watched") is True and fc.get("listened") is False and fc.get("heard") == "no voice", f"the watch-through ran; the listen-back skipped (no voice): {fc}")
ok(not [f for f in review.get("fixes", []) if f.get("check") in ("cut_short", "ends_mid", "broken", "doubled")],
   "nothing flagged about a missing voice")
job = final_check._job(conn, row)
flags, _ = final_check.watch(job)
ok(not flags, f"the watch-through finds nothing wrong with the default timings ({[f['what'] for f in flags]})")
fast = {**edl, "slideshow": {**edl["slideshow"], "slides": [{**s, "seconds": 1.5} if s["n"] == 2 else s for s in shown]}}
flags, _ = final_check.watch({**job, "edl": fast})
ok(any(f["check"] == "too_fast" and "slide 2" in f["what"] and f["action"]["type"] == "replan" for f in flags),
   f"a slide up too briefly is flagged: {[f['what'] for f in flags]}")
cost = usage.parts_for(conn, f"production:{SID}")
kinds = [r["kind"] for r in conn.execute("select kind from usage where ref like ?", (f"production:{SID}%",))]
ok(kinds == ["review"], f"no extra Claude calls: the read only (music is the stub) {kinds}")

# ── 4. Review shows it like any Reel ─────────────────────────────────────────
print("\n== 4. Review")
d = owner.get(f"/api/production/{SID}").json
ok(d["format"] == "slideshow" and d["video"] and d["status"] == "ready" and d["stage"] == "review", "in To review with its video")
ok(d["slideshow"]["from"]["id"] == CID and len(d["slideshow"]["slides"]) == 4 and d["resolve"] is None,
   "made from the carousel, its slides' times; no Resolve project")
board = owner.get("/api/board").json
items = [i for i in board["items"] if i.get("kind") == "production" and i["id"] == SID]
ok(items and items[0]["stage"] == "review" and items[0]["video"], "on the board in To review")

# ── 5. Send back with notes: times, order, music ─────────────────────────────
print("\n== 5. Send back")
def note(body, at=None):
    owner.post(f"/api/production/{SID}/notes", json={"body": body, "at_seconds": at})
import playbook
pb_before = playbook.text()
r = owner.post(f"/api/production/{SID}/notes", json={"body": "Slides felt slow here", "at_seconds": 2.0, "remember": True})
ok(r.status_code == 200 and conn.execute("select remember from feedback where id=?", (r.json["id"],)).fetchone()[0] == 0
   and playbook.text() == pb_before and "Slides felt slow" not in playbook.text(),
   "Remember for future edits is ignored on a slideshow note: nothing goes in the Reel playbook")
app_src = (Path(settings.__file__).parent / "web" / "app.js").read_text()
ok('d.kind === "production" && !isSlideshow(d) && isOwner()' in app_src and 'composerRowHTML("What should change? e.g. \\"Cut the first two seconds\\"", !isSlideshow(d))' in app_src,
   "and Review doesn't offer the tick on a slideshow draft")
note("Swap slides 2 and 3, and leave the electrical one up longer", at=4.0)
FAKE.change = {"order": [1, 3, 2, 4], "seconds": [{"n": 3, "seconds": 7.0}], "music": {"change": False, "mood": "soft",
               "search": "x", "volume_db": -16}, "summary": "Swapped slides 2 and 3 and gave slide 3 more time.",
               "notes_addressed": [{"note_id": 0, "how": "done"}]}
nid = conn.execute("select max(id) from feedback").fetchone()[0]
FAKE.change["notes_addressed"][0]["note_id"] = nid
r = team.post(f"/api/production/{SID}/decision", json={"decision": "changes"})
ok(r.status_code == 200, "the team may send it back with a note")
n_calls = len(FAKE.calls)
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
edl2 = json.loads(row["edl"])
shown2 = edl2["slideshow"]["slides"]
ok(row["version"] == 2 and row["status"] == "ready", f"v2 back in To review ({row['status']})")
ok([s["n"] for s in shown2] == [1, 3, 2, 4] and next(s for s in shown2 if s["n"] == 3)["seconds"] == 7.0,
   f"the new order and time: {[(s['n'], s['seconds']) for s in shown2]}")
ch = [c for c in FAKE.calls[n_calls:] if "You adjust a slideshow Reel" in c.get("system", "")]
ok(len(ch) == 1 and "Swap slides 2 and 3" in ch[0]["messages"][0]["content"] and "slide 2 on screen" in ch[0]["messages"][0]["content"],
   "one short Claude call read the notes (with the slide on screen at the note's moment)")
ok(len(TRACKS) == 1 and json.loads(row["music"])["file"] == mus["file"], "the music stays the same (not asked to change)")
v2 = Path(row["video_path"])
order2 = [which(frame(s["start"] + s["seconds"] / 2, v2))[0] for s in shown2]
FILES2 = {x["n"]: x["file"] for x in json.loads(row["plan"])["slides"]}
ok(FILES2 == FILES, "a send-back doesn't lay the slides out again")
ok(order2 == [1, 3, 2, 4], f"the frames show the new order: {order2}")
ok(abs(float(probe(v2)["format"]["duration"]) - (sum(s["seconds"] for s in shown2) + 2.6)) < 0.15, "its duration follows")
ok(conn.execute("select status from feedback where id=?", (nid,)).fetchone()[0] == "addressed", "the note is addressed")
# A track link alone: no Claude call, exactly that track
note("https://www.epidemicsound.com/music/tracks/1a2b3c-4d5e/")
owner.post(f"/api/production/{SID}/decision", json={"decision": "changes"})
n_calls = len(FAKE.calls)
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
m3 = json.loads(row["music"])
ok(not [c for c in FAKE.calls[n_calls:] if "You adjust" in c.get("system", "")], "a track link needs no Claude call")
ok(len(TRACKS) == 2 and "1a2b3c-4d5e" in TRACKS[-1]["brief"] and m3.get("link", "").endswith("1a2b3c-4d5e/"),
   f"exactly the linked track: {TRACKS[-1]['brief'][:110]}")
ok([s["n"] for s in json.loads(row["edl"])["slideshow"]["slides"]] == [1, 3, 2, 4], "the order Bryan asked for stays")

# ── 6. Studio's own fix round: the music level as a patch ────────────────────
print("\n== 6. Studio's own fix round")
FAKE.reads = [{**READ, "verdict": "Nearly ready: 1 thing to fix", "fixes": [MUSIC_FIX]}, READ]
before_db = json.loads(row["edl"])["music"]["volume_db"]
owner.post(f"/api/production/{SID}/decision", json={"decision": "redo"})
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
sf = json.loads(row["self_fix"])
e4 = json.loads(row["edl"])
ok(sf["rounds"] and sf["rounds"][0]["how"] == "patch" and sf["rounds"][0]["kept"] and len(sf["fixed"]) == 1,
   f"the read's music fix made as a patch and kept: {[(r['how'], r.get('kept')) for r in sf['rounds']]}")
ok(e4["music"]["volume_db"] == before_db - 3 and row["video_path"].endswith("_fix1.mp4"), f"music {before_db} -> {e4['music']['volume_db']} dB")
ok([s["seconds"] for s in e4["slideshow"]["slides"]] == [slideshow.seconds_for(SLIDES[0], True)] + [slideshow.seconds_for(SLIDES[k]) for k in (2, 1, 3)],
   "Edit it again: re-timed from the words, the order kept")
ok(len(TRACKS) == 3 and "1a2b3c" in TRACKS[-1]["brief"], "Edit it again picks the music again (the linked track)")
import learning
ok(not [r for r in learning.active("reel") if "slideshow" in r["text"].lower()], "a slideshow's fix isn't learned as a Reel rule")
FAKE.reads = []
# Start over: the first order again
owner.post(f"/api/production/{SID}/decision", json={"decision": "fresh"})
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (SID,)).fetchone())
ok([s["n"] for s in json.loads(row["edl"])["slideshow"]["slides"]] == [1, 2, 3, 4] and row["status"] == "ready",
   "Start over: the carousel's order and times again")

# ── 7. Approve, schedule, post: a Reel ───────────────────────────────────────
print("\n== 7. Posting")
ok(team.post(f"/api/production/{SID}/decision", json={"decision": "approve"}).status_code == 403, "only Bryan approves")
owner.post(f"/api/production/{SID}/decision", json={"decision": "approve"})
prod = conn.execute("select * from productions where id=?", (SID,)).fetchone()
ok(posting._kind(prod) == "reel" and posting.can_post(conn, prod) is None, "posting treats it as a Reel")
post = posting.schedule(conn, SID, when=None, caption="Hidden costs, with music. #reno", by="bryan@x.com")
prow = conn.execute("select * from posts where production_id=?", (SID,)).fetchone()
ok(prow["kind"] == "reel" and prow["share_to_feed"] == 1, "scheduled as a Reel, shared to the feed")
files_out = posting.prepare(conn, prow)
ok(len(files_out) == 1 and files_out[0].name == "reel.mp4" and not posting.reel_problems(posting.probe(files_out[0])),
   "its file is fit for Instagram as it is")
import instagram
made = []
instagram.create_container = lambda params: made.append(params) or "c1"
posting._build(conn, prow, files_out)
ok(made and made[0]["media_type"] == "REELS" and "video_url" in made[0], f"Instagram gets a REELS container ({made[0].get('media_type')})")
ok("thumb_offset" not in made[0] or made[0]["thumb_offset"] == 0, "the cover: the first frame (the cover slide)")
conn.execute("update ideas set remote_id = 77 where id = ?", (idea_id,)); conn.commit()
ok(posting._idea_remote(conn, SID) == 77, "its post links to the idea in the planner (the carousel's idea)")

# ── 8. The carousel is untouched ─────────────────────────────────────────────
print("\n== 8. The carousel")
after = carousel_state()
ok(after[0] == BEFORE[0], "the carousel's row is as it was")
ok(after[1] == BEFORE[1], "its slides' files are as they were")
ok(after[2] == BEFORE[2], "its notes are as they were")
ok(conn.execute("select production_id from ideas where id=?", (idea_id,)).fetchone()[0] == CID, "the idea still points at the carousel")

# ── 9. The rest: clean-up, usage, help, updater ──────────────────────────────
print("\n== 9. The rest")
prow_ = conn.execute("select * from productions where id=?", (SID,)).fetchone()
ok(any(p.name == f"slideshow_{SID}" for p in cleanup.production_files(prow_)), "Remove would take its slide copies too")
ok(any(f["key"] == "slideshow" and any("make-slideshow" in e[1] for e in f["events"]) and any("slideshow_made" in e[1] for e in f["events"])
       for f in usage_report.FEATURES), "usage_report.FEATURES counts the press and the action")
app_js = (Path(settings.__file__).parent / "web" / "app.js").read_text()
ok('mark("slideshow_made"' in app_js and 'id="make-slideshow"' in app_js, "Review records the press as an action")
help_js = (Path(settings.__file__).parent / "cloud" / "planner" / "help.js").read_text()
ok("slideshow" in help_js.lower() and "#make-slideshow" in help_js, "help.js teaches it, with Show me")
import help_desk
ok("slideshow" in help_desk.PAGE_WORDS["carousel"], "Ask Studio finds it from a carousel's page")
ok("slideshow" in [m.strip() for m in updater._MODULES.split(",")], "slideshow is in updater._MODULES")
readme = (Path(settings.__file__).parent / "README.md").read_text()
ok("Slideshow Reels" in readme, "the README has a section")

# ── 10. Without music or an end card (a personal post), and a release's restart ──
print("\n== 10. No music, no end card; a restart")
conn.execute("""INSERT INTO productions (id, title, brand, format, status, version, idea, source_clips, caption, plan,
                    slides, thumb_path, created_at, updated_at)
                SELECT 50, 'Personal one', 'personal', 'carousel', 'approved', 1, idea, source_clips, caption, plan, slides,
                       thumb_path, created_at, updated_at FROM productions WHERE id = 1""")
conn.commit()
r = owner.post("/api/production/50/slideshow", json={})
PID = r.json["id"]
n_tracks = len(TRACKS)
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (PID,)).fetchone())
e = json.loads(row["edl"]); m = json.loads(row["music"])
info = probe(Path(row["video_path"]))
want = sum(s["seconds"] for s in e["slideshow"]["slides"]) + editor.END_TAIL
ok(row["status"] == "ready" and e["close"]["card"] is None and abs(float(info["format"]["duration"]) - want) < 0.15,
   f"an approved carousel can be made into one too; a personal post has no end card ({float(info['format']['duration']):.2f}s)")
ok(m["source"] == "instagram" and len(TRACKS) == n_tracks and "Instagram app" in m["note"],
   "no Epidemic track for the personal account: a note to add music in Instagram")
ok(any(s_["codec_type"] == "audio" and s_["codec_name"] == "aac" for s_ in info["streams"]), "still H.264 with an AAC track")
new = restyle.restart(conn, [PID])
NID = new[0][1]
worker.run_editor(stop_after_one=True)
row = dict(conn.execute("select * from productions where id=?", (NID,)).fetchone())
nplan = json.loads(row["plan"])
ok(row["status"] == "ready" and row["format"] == "slideshow" and all(Path(s_["file"]).parent == slideshow.folder(NID) for s_ in nplan["slides"]),
   "a release's restart: the new draft copies the slides into its own folder and renders")
oldrow = conn.execute("select * from productions where id=?", (PID,)).fetchone()
cleanup.remove_production(conn, oldrow)
ok(all(Path(s_["file"]).is_file() for s_ in nplan["slides"]) and not slideshow.folder(PID).exists(),
   "removing the old one takes its own copies, not the new draft's")

finish()
