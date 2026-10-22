"""Picture inserts in Reels (pictures.py; Bryan, 2026-10-20: "an image of a decorative glass panel instead of an
animation ... there are many instances where an animation just doesn't cut it and we should be using an AI image
instead"; 2026-10-22: "Gemini OK", capped per draft).
A talking head made at run time (fixtures/person.jpg, flite speech with known word edges). Gemini is a local fake
(a small HTTP server in Studio's GEMINI_URL shape: it draws a PNG and counts its calls, or fails as asked); Claude
(the plan, the picture check, the read), music and the Whisper re-check stubbed; everything else real: the
placement, the picture's render (push-in, Concept mark), the one encode, the watch-through, Resolve's layer.
Run: python tests/quality/suite_pictures.py (from studio/). STUDIO_PICTURES_STRIP=<folder> also saves a frame strip
of the picture draft there."""
import base64, http.server, io, json, os, re, shutil, subprocess, threading, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish
import media

t_media = time.time()
M = media.ending_speech(P / "media")
DATA = json.loads((M / "words.json").read_text())
TALK = media.person_video(P / "media" / "person.mp4", (1080, 1920), DATA["total"], "540", 640, 3.0, audio=M / "talk.wav")
print(f"(media made in {time.time() - t_media:.0f}s)")
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0",
                  GEMINI_API_KEY="test-gemini-key", STUDIO_IMAGE_COST_USD="0.04")
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"  # the fake Gemini is on this machine
import numpy as np
from PIL import Image
import settings, catalog, extract, editor, transcribe, music, anthropic, concepts, pictures, fix_actions, final_check
import draft_review, self_fix, usage

settings.ensure_folders()
conn = catalog.connect()
concepts.BACKOFF = 0.0  # the fake fails at once; no need to wait before the one retry


# ── The fake Gemini ───────────────────────────────────────────────────────────
GEM = {"calls": [], "mode": "ok"}


def fake_png(seed: str) -> bytes:
    """A 'photograph' 768x1376 (9:16-ish): warm vertical flutes in a colour of the brief's own, so two pictures
    differ and a frame shows which one is up."""
    h = int(re.sub(r"[^0-9a-f]", "", seed)[:6] or "a0", 16)
    base = np.array([190 + h % 50, 150 + (h >> 8) % 40, 100 + (h >> 16) % 30], dtype=np.float32)
    x = np.arange(768)[None, :, None]
    y = np.linspace(0, 1, 1376)[:, None, None]
    img = base * (0.82 + 0.12 * np.sin(x / 10.0)) * (1.05 - 0.2 * y)
    out = io.BytesIO()
    Image.fromarray(img.clip(0, 255).astype(np.uint8)).save(out, "PNG")
    return out.getvalue()


class Gemini(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        GEM["calls"].append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
        mode = GEM["mode"]
        if mode == "down":
            self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":{"message":"overloaded"}}')
            return
        if mode == "blocked":
            answer = {"promptFeedback": {"blockReason": "SAFETY"}}
        else:
            prompt = body["contents"][0]["parts"][0]["text"]
            import hashlib
            data = base64.b64encode(fake_png(hashlib.sha256(prompt.encode()).hexdigest())).decode()
            answer = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": data}}]},
                                      "finishReason": "STOP"}],
                      "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 1290}}
        raw = json.dumps(answer).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gemini)
threading.Thread(target=server.serve_forever, daemon=True).start()
settings.GEMINI_URL = f"http://127.0.0.1:{server.server_address[1]}/v1beta"
settings.GEMINI_API_KEY = "test-gemini-key"


# ── Claude, music and Whisper stubbed ─────────────────────────────────────────
class Fake:
    """Claude: the edit plan given; the picture check (ok, or wrong as CHECK says); a clean read; no joins."""

    def __init__(self, plan):
        self.calls, self.plan, self.check = [], plan, "ok"

    @property
    def beta(self):
        return NS(messages=NS(stream=self.stream, create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "You check AI concept pictures" in sysp:
            labels = [re.match(r"Picture (\S+):", c["text"]).group(1) for c in kw["messages"][0]["content"]
                      if c.get("type") == "text" and c["text"].startswith("Picture ")]
            bad = self.check == "bad" or (self.check == "bad_once" and not any(
                "You check AI concept pictures" in c.get("system", "") for c in self.calls[:-1]))
            text = json.dumps({"checks": [{"picture": lb, "ok": not bad, "problem": "the panel is warped" if bad else ""}
                                          for lb in labels]})
        elif "Place names stay internal" in sysp:
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

    def checks(self):
        return sum(1 for c in self.calls if "You check AI concept pictures" in c.get("system", ""))


def fake_track(brief, seconds, out_dir, ref=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    return media.music_bed(out_dir / "bed.mp3", 60), {"title": "Test Bed", "artist": "ffmpeg", "recording_id": "t1"}


CURRENT = {}
transcribe.transcribe = lambda wav: {"words": editor.timeline(CURRENT["cuts"], CL)}
music.allowed_for = lambda b: True
orig_render = editor.render_cut


def counting_render(cuts, clips, out):
    CURRENT["cuts"] = cuts
    return orig_render(cuts, clips, out)


editor.render_cut = counting_render

# ── The footage ───────────────────────────────────────────────────────────────
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


CUTS = [{"clip_id": CID, "start": WORDS[0]["start"], "end": WORDS[I["porcelain."]]["end"], "why": "all of it"},
        {"clip_id": CID, "start": at("So"), "end": WORDS[-1]["end"], "why": "the payoff"}]
GLASS = {"clip_id": CID, "at": at("It"), "duration": 3.5, "style": "picture", "title": "Fluted glass panel",
         "lines": [], "image": "A fluted glass panel between a living room and a kitchen, close enough to see its "
                               "flutes, soft daylight through it, light oak frame, warm white walls.",
         "scene": json.dumps({"type": "headline", "text": "Fluted glass panel"}), "look": "cutaway", "role": "explain",
         "why": "shows the panel he means"}
STEPS = {"clip_id": CID, "at": at("For"), "duration": 4.0, "style": "scene", "title": "Levelling the floor", "lines": [],
         "image": "", "scene": json.dumps({"type": "steps", "title": "Levelling the floor",
                                           "items": ["Strip the floor", "Pour a level screed", "Lay the tiles"]}),
         "look": "cutaway", "role": "explain", "why": "shows the steps"}
BASE = {"cuts": CUTS, "hook_text": "Floors *level* again", "key_words": ["screed"],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "A short talk.", "notes_addressed": [], "missing_shots": []}


def production(graphics, kind=None):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Floors", "hook": "h",
                                    "angle": "x", "beats": [],
                                    "segments": [{"clip_id": CID, "start": 0, "end": DATA["total"], "purpose": "a"}],
                                    "caption": "cap", "est_seconds": 25, "cta": "Save this for your reno."}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    if kind:
        conn.execute("update ideas set post_type = ? where id = ?", (kind, idea))
    conn.commit()
    pid = catalog.make_production(conn, idea)
    plan = json.loads(conn.execute("select plan from productions where id=?", (pid,)).fetchone()[0])
    plan["cta"] = "Save this for your reno."
    conn.execute("update productions set plan=? where id=?", (json.dumps(plan), pid))
    conn.commit()
    return pid, Fake({**json.loads(json.dumps(BASE)), "graphics": json.loads(json.dumps(graphics))})


def row(pid):
    return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())


def frame(video, t, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(float)


def warm(img, box=(560, 1150, 900, 1230)):
    """Is the picture (warm flutes) up in this region (beside the person, above the captions)? R well over B."""
    x0, y0, x1, y1 = box
    r = img[y0:y1, x0:x1].mean(axis=(0, 1))
    return r[0] - r[2] > 40, r


SHOTS = P / "frames"
SHOTS.mkdir(exist_ok=True)

# ── 1. When a picture, when a drawing ─────────────────────────────────────────
print("\n== 1. The plan: when a picture, when a drawing")
asks = {t: pictures.note_ask(t) for t in [
    "use an image of a decorative glass panel here instead of the animation", "Show a photo of fluted glass here please",
    "Can we get an AI image of a terrazzo floor?", "a picture here instead of the animation",
    "The picture is too dark here, use the other shot instead", "the picture is blurry", "Cut the pause here",
    "Too many animations"]}
ok(asks["use an image of a decorative glass panel here instead of the animation"] == {"subject": "decorative glass panel"}
   and asks["Show a photo of fluted glass here please"] == {"subject": "fluted glass"}
   and asks["Can we get an AI image of a terrazzo floor?"] == {"subject": "terrazzo floor"}
   and asks["a picture here instead of the animation"] == {"subject": ""},
   "Bryan's notes asking for an image, picture or photo of something are read, with what to show")
ok(all(asks[t] is None for t in ["The picture is too dark here, use the other shot instead", "the picture is blurry",
                                 "Cut the pause here", "Too many animations"]),
   "notes about the video's picture, cuts or animations in general are not picture asks")
ok(pictures.available() is None and pictures.available("finished") == concepts.NOT_IN_FINISHED,
   "pictures on with the image key; never in a Finished homes post")
rule = pictures.rule()
ok("real-looking thing" in rule and "terrazzo" in rule and "Processes, steps" in rule and "stay drawn animations" in rule
   and "image, picture or photo" in rule and "Concept" in rule and pictures.rule("finished") == "",
   "the prompt: real things (a panel, a finish, a look) are pictures, processes and numbers drawings; a note asking "
   "for one makes it a picture")
sch = editor._schema(True)["properties"]["graphics"]["items"]
ok("picture" in sch["properties"]["style"]["enum"] and "image" in sch["required"]
   and "picture" not in editor._schema(False)["properties"]["graphics"]["items"]["properties"]["style"]["enum"]
   and "image" not in editor._schema(False)["properties"]["graphics"]["items"]["properties"],
   "the plan's schema has the picture style and its brief only when pictures are on")
ok(settings.REEL_PICTURES and settings.REEL_PICTURES_MAX == 4, "on by default, at most 4 new pictures a draft")

# ── 2. A draft with a picture ─────────────────────────────────────────────────
print("\n== 2. A draft with a picture insert")
pid, fake = production([GLASS])
anthropic.Anthropic = lambda **kw: fake
t0 = time.time()
final = editor.edit(conn, pid, client=fake, find_track=fake_track)
print(f"  edit took {time.time() - t0:.1f}s")
r = row(pid)
edl = json.loads(r["edl"])
pics = [g for g in edl["graphics"] if g.get("style") == "picture"]
print("  pictures:", json.dumps(edl.get("pictures")), [(g["start"], g["duration"], g["look"]) for g in pics])
plan_call = fake.calls[0]
ok("Pictures (style \"picture\")" in plan_call["system"]
   and "picture" in plan_call["output_config"]["format"]["schema"]["properties"]["graphics"]["items"]["properties"]["style"]["enum"],
   "the plan was offered pictures")
ok(len(pics) == 1 and pics[0]["title"] == "Fluted glass panel" and pics[0].get("file"), "the draft has the picture insert")
ok(len(GEM["calls"]) == 1 and fake.checks() == 1, f"one picture drawn (one option, not two) and checked once: "
                                                  f"{len(GEM['calls'])} Gemini call, {fake.checks()} check")
call = GEM["calls"][0]
prompt = call["body"]["contents"][0]["parts"][0]["text"]
ok(call["body"]["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16" and call["key"] == "test-gemini-key"
   and call["path"].endswith(f"/models/{settings.IMAGE_MODEL}:generateContent"),
   "drawn 9:16, the key in its header, concepts.py's request")
ok(GLASS["image"] in prompt and "No text, letters" in prompt and "No people and no faces" in prompt
   and "Not a real address" in prompt and "logos" in prompt, "the brief plus the rules: no words, faces, logos or places")
g = pics[0]
close = edl["close"]["at"]
ok(g["start"] >= editor.SCENE_CLEAR and g["start"] + g["duration"] <= close - editor.END_CLEAR + 0.01,
   f"clear of the hook's opening and of the close: {g['start']:.2f}-{g['start'] + g['duration']:.2f}s, close at {close:.2f}s")
ok(abs(g["start"] - next(w["start"] for w in editor.timeline(edl["cuts"], CL) if w["word"] == "It")) < 0.25
   and 2.5 <= g["duration"] <= 6.0, f"timed to his words (from \"It\", {g['duration']:.1f}s on screen)")
mid = g["start"] + g["duration"] / 2
inside, before = frame(final, mid, SHOTS / "mid.png"), frame(final, g["start"] - 0.6, SHOTS / "before.png")
w_in, rgb_in = warm(inside)
w_before, rgb_before = warm(before)
ok(w_in and not w_before, f"a frame in the middle shows the picture (rgb {rgb_in.round()}), a frame before it the footage "
                          f"(rgb {rgb_before.round()})")
lab = pictures.label_image(titles=g.get("titles"))  # the draft's look (Site Notes' label since 2026-10-22)
lx, ly = pictures.label_at(g.get("titles"))
tag = inside[ly:ly + lab.height, lx:lx + lab.width]
beside_tag = inside[ly:ly + lab.height, lx + lab.width + 40:lx + 2 * lab.width + 40]
ok(tag.mean() < beside_tag.mean() - 40 and tag.max() > 200,
   f"the Concept mark is on it, top left (its tag {tag.mean():.0f}, the picture beside it {beside_tag.mean():.0f})")
early = frame(final, g["start"] + 0.5, SHOTS / "early.png")
late = frame(final, g["start"] + g["duration"] - 0.5, SHOTS / "late.png")
c0 = early[700:1100, 300:780]
c1 = late[700:1100, 300:780]
ok(float(np.abs(c0 - c1).mean()) > 1.0 and warm(early)[0] and warm(late)[0],
   f"a slow push-in: the picture moves between its first and last second ({float(np.abs(c0 - c1).mean()):.1f} levels)")
ok(edl["pictures"]["drawn"] == 1 and abs(edl["pictures"]["usd"] - 0.04) < 1e-6 and edl["pictures"]["paid"] == 1,
   f"its cost kept with the draft: {edl['pictures']}")
rows = conn.execute("select kind, cost_usd from usage where ref like ? and kind = 'image'", (f"production:{pid}%",)).fetchall()
parts = usage.parts_for(conn, f"production:{pid}")
ok(len(rows) == 1 and abs(rows[0]["cost_usd"] - 0.04) < 1e-6 and parts["image_count"] == 1,
   f"recorded as Gemini spend in usage (kind image, US$0.04), counted in the draft's cost: {parts}")
sf = self_fix.summary(row(pid))
ok(sf and "Pictures: 1 shown, 1 drawn by Gemini (US$0.04)" in sf["cost_line"], f"the cost line says so: {sf and sf['cost_line']}")
ok("AI picture" in edl["summary"] and "Concept" in edl["summary"] and "Fluted glass panel" in edl["summary"],
   f"\"What the editor did\" says it: {edl['summary']!r}")
read_call = next(c for c in fake.calls if "Place names stay internal" in c.get("system", ""))
read_text = " ".join(c.get("text", "") for c in read_call["messages"][0]["content"] if c.get("type") == "text")
ok("Pictures over the footage" in read_text and "Fluted glass panel" in read_text and "- Pictures:" in read_call["system"]
   and "field \"picture\"" in read_call["system"], "Studio's read is told about the picture and how to judge it")
stills = [c["text"] for c in read_call["messages"][0]["content"] if c.get("type") == "text" and c["text"].startswith("Still at")]
ok(any(abs(float(re.findall(r"[\d.]+", s)[0]) - mid) < 0.6 or g["start"] <= float(re.findall(r"[\d.]+", s)[0]) <= g["start"] + g["duration"]
       for s in stills), "the read sees a still while the picture is up")
lit = [c for c in editor.lit_captions(editor.caption_chunks(editor.timeline(edl["cuts"], CL), close), edl["bright"])
       if c["start"] >= g["start"] and c["end"] <= g["start"] + g["duration"]]
ok(lit and all(c.get("lit") for c in lit), "captions over the photo get the stronger glow")
shown = final_check.shown(edl, r["duration"])
x = next(s for s in shown if s["kind"] == "picture")
job = final_check._job(conn, r)
flags, n = final_check.watch(job)
print("  watch-through flags:", [(f.get("check"), f["what"]) for f in flags])
ok(x["box"] is None and x["start"] == g["start"] and not any(f.get("start") is not None and abs(f["start"] - g["start"]) < 0.2
                                                             for f in flags),
   "the watch-through sees the picture (full screen) and finds nothing wrong with it")

# ── 3. A re-render and a fix round reuse it ───────────────────────────────────
print("\n== 3. Re-render: no new picture")
calls_before, checks_before = len(GEM["calls"]), fake.checks()
webms = {p.name: p.stat().st_mtime_ns for p in editor.INSERTS.glob("*.webm")}
final2 = editor.rerender(conn, pid, find_track=fake_track)
edl2 = json.loads(row(pid)["edl"])
pics2 = [g for g in edl2["graphics"] if g.get("style") == "picture"]
ok(len(GEM["calls"]) == calls_before and fake.checks() == checks_before,
   f"a re-render makes no Gemini call and no new check ({len(GEM['calls']) - calls_before}, {fake.checks() - checks_before})")
ok(pics2 and pics2[0]["file"] == g["file"] and pics2[0]["picture"] == g["picture"] and edl2["pictures"]["reused"] == 1
   and edl2["pictures"]["drawn"] == 0 and set(webms) == {p.name for p in editor.INSERTS.glob("*.webm")},
   "the same picture and the same rendered insert, from the cache")
ok(warm(frame(final2, mid, SHOTS / "mid2.png"))[0], "and it's still in the re-rendered draft")
ok(len(conn.execute("select 1 from usage where ref like ? and kind = 'image'", (f"production:{pid}%",)).fetchall()) == 1,
   "never paid twice")
patched, res = editor.self_patch(conn, pid, [{"n": 1, "action": {"type": "graphic", "item": next(
    k for k, x in enumerate(edl2["graphics"], 1) if x.get("style") == "picture"), "field": "time",
    "start": g["start"] + 0.3, "amount": None}}], 1, client=fake, find_track=fake_track)
edl3 = json.loads(row(pid)["edl"])
ok(patched and res[0].get("ok") and len(GEM["calls"]) == calls_before and edl3["pictures"]["paid"] == edl2["pictures"]["paid"]
   and edl3["pictures"]["drawn"] == 0, f"a fix round that moves it reuses it too ({res[0].get('did')})")
pics3 = [x for x in edl3["graphics"] if x.get("style") == "picture"]
for f_ in editor.INSERTS.glob("*.webm"):
    f_.unlink()  # the cache cleared (45 days unused): Resolve's layer renders it again from the kept picture
calls_before = len(GEM["calls"])
held = editor.scenes_layer(pics3, row(pid)["duration"], P / "animations.mov")
ok(held == 1 and (P / "animations.mov").exists() and len(GEM["calls"]) == calls_before,
   "the Resolve project's insert layer holds the picture, rendered again from the kept picture, not drawn again")

# ── 4. The cap ────────────────────────────────────────────────────────────────
print("\n== 4. The cap")
many_words = [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.3} for i in range(120)]
many_cuts = [{"clip_id": 1, "start": 0.0, "end": 60.0}]
wanted = [{**GLASS, "clip_id": 1, "at": a, "title": f"Picture {a:.0f}", "image": f"brief {a}"} for a in (5, 13, 21, 29, 37, 45)]
placed, notes = pictures.place(wanted, many_cuts, {1: {}}, 60.0, many_words, "", room=6)
ok(len(placed) == 4 and any("keeps to 4 pictures" in n for n in notes),
   f"at most 4 pictures in a 60 s Reel ({len(placed)} of 6; {notes[-1:]})")
placed, notes = pictures.place(wanted, many_cuts, {1: {}}, 30.0, many_words[:60], "", room=6)
ok(len(placed) == 2, f"fewer in a shorter Reel: 2 in 30 s ({len(placed)})")
placed, _ = pictures.place(wanted, many_cuts, {1: {}}, 60.0, many_words, "", room=1)
ok(len(placed) == 1, "with the drawings, about one insert per 10 seconds")
settings.REEL_PICTURES_MAX = 2
pid4, fake4 = production([])
three = [{**GLASS, "start": s, "duration": 3.0, "title": f"Finish {s}", "image": f"A terrazzo finish, tone {s}."}
         for s in (3.0, 9.0, 15.0)]
calls_before = len(GEM["calls"])
done, instead, notes4, tally = pictures.make(json.loads(json.dumps(three)), conn, row(pid4), 1, f"production:{pid4}", fake4)
ok(len(done) == 2 and len(instead) == 1 and len(GEM["calls"]) - calls_before == 2 and tally["paid"] == 2
   and "as many new pictures" in instead[0]["instead_of_picture"],
   f"STUDIO_REEL_PICTURES_MAX=2: two drawn, the third is a drawing ({notes4[-1:]})")
conn.execute("update productions set version = 1, edl = ? where id = ?", (json.dumps({"pictures": tally}), pid4))
conn.commit()
done, instead, _, tally2 = pictures.make([{**GLASS, "start": 3.0, "duration": 3.0, "title": "Another", "image": "A limewash wall."}],
                                         conn, row(pid4), 1, f"production:{pid4}", fake4)
ok(not done and instead and len(GEM["calls"]) - calls_before == 2, "the budget holds across Studio's own rounds of the same draft")
done, instead, _, tally3 = pictures.make(json.loads(json.dumps(three[:2])), conn, row(pid4), 1, f"production:{pid4}", fake4)
ok(len(done) == 2 and not instead and tally3["reused"] == 2, "pictures already drawn still show (the cache costs nothing)")
done, instead, _, _ = pictures.make([{**GLASS, "start": 3.0, "duration": 3.0, "title": "New", "image": "Oak veneer."}],
                                    conn, row(pid4), 2, f"production:{pid4}", fake4)
ok(len(done) == 1, "a new version starts its own budget")
settings.REEL_PICTURES_MAX = 4

# ── 5. Gemini failing: a drawing instead, never a broken draft ───────────────
print("\n== 5. When Gemini fails")
import motion, tempfile
with tempfile.TemporaryDirectory() as t_:
    spec_ = motion.validate({"scenes": [{"type": "headline", "text": "Reeded glass", "duration": 3.0}]}, insert=True)
    rep_ = motion.check(motion.build_project(spec_, Path(t_) / "i", "cutaway"), spec_)
ok(not rep_["problems"], f"a drawing that only shows a headline passes the motion library's check (every bundled font "
                         f"loaded on purpose; before, a face it didn't use failed it): {rep_['problems']}")
GEM["mode"] = "down"
calls_before = len(GEM["calls"])
pid5, fake5 = production([{**GLASS, "title": "Reeded glass", "image": "A reeded glass panel, a new brief."}])
final5 = editor.edit(conn, pid5, client=fake5, find_track=fake_track)
edl5 = json.loads(row(pid5)["edl"])
drawn5 = [g for g in edl5["graphics"] if g.get("style") == "scene"]
ok(final5.exists() and row(pid5)["status"] == "ready", "Gemini down: the draft is still made")
ok(len(GEM["calls"]) - calls_before == 2 and not [g for g in edl5["graphics"] if g.get("style") == "picture"]
   and drawn5 and drawn5[0].get("instead_of_picture") and drawn5[0]["scene"].get("type") == "headline",
   f"one try and one retry, then its drawing instead ({drawn5 and drawn5[0]['instead_of_picture']})")
ok("instead of showing a picture" in edl5["summary"] and edl5["summary"].count("instead of showing") == 1,
   f"\"What the editor did\" says so once: {edl5['summary']!r}")
ok(not conn.execute("select 1 from usage where ref like ? and kind = 'image'", (f"production:{pid5}%",)).fetchall(),
   "nothing paid for a picture that wasn't drawn")
d5 = drawn5[0]
ok(not warm(frame(final5, d5["start"] + d5["duration"] / 2, SHOTS / "drawn5.png"))[0], "the frame shows the drawing, not a picture")
GEM["mode"] = "blocked"
res = concepts.reel_picture("A brief the safety filter refuses.", "x", "test:blocked", fake5)
ok(res["path"] is None and "safety filter" in res["why"] and res["drawn"] == 0, f"refused by Gemini's filter: {res['why']}")
GEM["mode"] = "ok"
fake5.check = "bad"
calls_before = len(GEM["calls"])
res = concepts.reel_picture("A warped thing, twice.", "warped thing", "test:bad", fake5)
ok(res["path"] is None and len(GEM["calls"]) - calls_before == 2 and res["drawn"] == 2 and "wrong twice" in res["why"],
   f"wrong by the check: drawn once more with the problem named, then a drawing ({res['why']})")
ok("the panel is warped" in GEM["calls"][-1]["body"]["contents"][0]["parts"][0]["text"], "the redraw names the problem")
again = concepts.reel_picture("A warped thing, twice.", "warped thing", "test:bad", fake5)
ok(again["path"] is None and len(GEM["calls"]) - calls_before == 2, "a picture that failed is not paid for again")
fake5.check = "bad_once"
fake5.calls = []
res = concepts.reel_picture("Wrong once, then right.", "thing", "test:once", fake5)
ok(res["path"] and res["drawn"] == 2, "wrong once, right the second time: shown")

# ── 6. Bryan's note: an image instead of the animation ───────────────────────
print("\n== 6. \"Use an image of ... instead of the animation\"")
pid6, fake6 = production([STEPS])
final6 = editor.edit(conn, pid6, client=fake6, find_track=fake_track)
edl6 = json.loads(row(pid6)["edl"])
s6 = next(g for g in edl6["graphics"] if g.get("style") == "scene")
ok(not [g for g in edl6["graphics"] if g.get("style") == "picture"], f"v1 draws the steps at {s6['start']:.1f}s")
conn.execute("update productions set status = 'revising' where id = ?", (pid6,))
conn.execute("""INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                VALUES ('production', ?, 1, ?, ?, 'open', ?)""",
             (pid6, s6["start"] + 1.0, "use an image of a decorative glass panel here instead of the animation", catalog.now()))
conn.commit()
calls_before = len(GEM["calls"])
final6b = editor.edit(conn, pid6, client=fake6, find_track=fake_track)  # the plan comes back with the drawing again
edl6b = json.loads(row(pid6)["edl"])
p6 = [g for g in edl6b["graphics"] if g.get("style") == "picture"]
ok(p6 and p6[0]["title"] == "decorative glass panel" and abs(p6[0]["start"] - s6["start"]) < 0.5 and p6[0].get("asked"),
   f"v2 shows a picture of a decorative glass panel at that moment ({p6 and p6[0]['start']:.1f}s), whatever the plan did")
ok(not [g for g in edl6b["graphics"] if g.get("style") == "scene"] and len(GEM["calls"]) - calls_before == 1,
   "not the animation: one picture drawn")
ok(warm(frame(final6b, p6[0]["start"] + p6[0]["duration"] / 2, SHOTS / "asked.png"))[0], "the frame shows the picture")
checks6 = [c["did"] for c in json.loads(row(pid6)["qa"])["checks"]]
ok(any("as your note asked" in c for c in checks6), f"the draft's checks say it was his note: {checks6[:3]}")
# The same through the read's patch (fix_actions): a drawn animation made a picture.
pid7, fake7 = production([STEPS])
editor.edit(conn, pid7, client=fake7, find_track=fake_track)
e7 = json.loads(row(pid7)["edl"])
k = next(k for k, x in enumerate(e7["graphics"], 1) if x.get("style") == "scene")
new, res7 = fix_actions.apply(e7, CL, [{"n": 1, "action": {"type": "graphic", "item": k, "field": "picture",
                                                            "to": "a decorative glass panel"}}])
ok(res7[0]["ok"] and new["graphics"][k - 1]["style"] == "picture" and new["graphics"][k - 1]["title"] == "a decorative glass panel"
   and new["graphics"][k - 1]["start"] == e7["graphics"][k - 1]["start"], f"the read's graphic fix \"picture\": {res7[0].get('did')}")
calls_before = len(GEM["calls"])
made7, res7b = editor.self_patch(conn, pid7, [{"n": 1, "action": {"type": "graphic", "item": k, "field": "picture",
                                                                   "to": "a decorative glass panel"}}], 1, client=fake7,
                                 find_track=fake_track)
e7b = json.loads(row(pid7)["edl"])
ok(made7 and [g for g in e7b["graphics"] if g.get("style") == "picture"] and len(GEM["calls"]) - calls_before == 1
   and warm(frame(made7, e7["graphics"][k - 1]["start"] + 1.5, SHOTS / "patched.png"))[0],
   "made in Studio's own round as a patch: rendered with the picture")
ok("picture" in draft_review.ACTION_FIELDS and "\"picture\" (to = what the picture shows" in draft_review._REEL_ACTIONS,
   "the read can ask for it")

# ── 7. No key, or turned off: drawings, said once ─────────────────────────────
print("\n== 7. No image key: drawings")
settings.GEMINI_API_KEY = ""
calls_before = len(GEM["calls"])
pid8, fake8 = production([STEPS])
editor.edit(conn, pid8, client=fake8, find_track=fake_track)
ok("Pictures (style" not in fake8.calls[0]["system"]
   and "picture" not in fake8.calls[0]["output_config"]["format"]["schema"]["properties"]["graphics"]["items"]["properties"]["style"]["enum"],
   "no key: the plan isn't offered pictures")
e8 = json.loads(row(pid8)["edl"])
ok([g for g in e8["graphics"] if g.get("style") == "scene"] and "needs the image key" in e8["summary"]
   and e8["summary"].count("image key") == 1, f"the inserts are drawn and the draft says why, once: {e8['summary']!r}")
conn.execute("update productions set status = 'revising' where id = ?", (pid8,))
s8 = next(g for g in e8["graphics"] if g.get("style") == "scene")
conn.execute("""INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                VALUES ('production', ?, 1, ?, ?, 'open', ?)""",
             (pid8, s8["start"] + 0.5, "show a photo of a terrazzo floor here", catalog.now()))
conn.commit()
editor.edit(conn, pid8, client=fake8, find_track=fake_track)
e8b = json.loads(row(pid8)["edl"])
ok(len(GEM["calls"]) == calls_before and not [g for g in e8b["graphics"] if g.get("style") == "picture"]
   and [g for g in e8b["graphics"] if g.get("instead_of_picture")] and "needs the image key" in e8b["summary"]
   and e8b["summary"].count("image key") == 1,
   f"his note asking for a photo: drawn instead, said once, no Gemini call ({e8b['summary']!r})")
settings.GEMINI_API_KEY = "test-gemini-key"
settings.REEL_PICTURES = False
ok(pictures.available() == "pictures are turned off (STUDIO_REEL_PICTURES)" and pictures.rule() == "",
   "STUDIO_REEL_PICTURES=0: off, the plan isn't offered them")
settings.REEL_PICTURES = True
pid9, fake9 = production([GLASS], kind="finished")
calls_before = len(GEM["calls"])
editor.edit(conn, pid9, client=fake9, find_track=fake_track)
e9 = json.loads(row(pid9)["edl"])
ok(not [g for g in e9["graphics"] if g.get("style") == "picture"] and len(GEM["calls"]) == calls_before
   and "Pictures (style" not in fake9.calls[0]["system"], "a Finished homes post: no pictures (real work only)")

# ── 8. The docs ───────────────────────────────────────────────────────────────
print("\n== 8. Docs")
readme = (editor.HERE if hasattr(editor, "HERE") else Path(editor.__file__).parent).joinpath("README.md").read_text()
envx = Path(editor.__file__).with_name(".env.example").read_text()
import updater, help_desk
ok("### Pictures instead of drawings" in readme and "STUDIO_REEL_PICTURES_MAX" in readme and "Concept" in readme,
   "the README explains picture inserts")
ok("STUDIO_REEL_PICTURES=" in envx and "STUDIO_REEL_PICTURES_MAX=" in envx and "pictures" in updater._MODULES
   and "picture" in help_desk.PAGE_WORDS["draft"], ".env.example, the updater's import check and Ask Studio know them")

strip = os.environ.get("STUDIO_PICTURES_STRIP")
if strip:  # a strip of the picture draft: before, the picture coming in, its middle, going out, after
    out = Path(strip)
    out.mkdir(parents=True, exist_ok=True)
    times = [g["start"] - 0.8, g["start"] + 0.15, g["start"] + 0.6, mid, g["start"] + g["duration"] - 0.15,
             g["start"] + g["duration"] + 0.8]
    for i, t in enumerate(times):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1",
                        "-vf", "scale=360:-2", str(out / f"f{i}.png")], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", *sum([["-i", str(out / f"f{i}.png")] for i in range(len(times))], []),
                    "-filter_complex", f"hstack=inputs={len(times)}", str(out / "strip.png")], check=True)
    shutil.copy(final, out / "draft.mp4")
    print(f"  strip: {out / 'strip.png'} at {[round(t, 2) for t in times]}")

server.shutdown()
finish()
