"""How Studio studies a reference (Bryan, 2026-10-26, "yes ok": a creator's way of having Claude "watch" a video):
the stills and the timed transcript merged into one timeline of beats before anything is concluded, the structure
read from it (open, hold, turn, close; seen, heard or inferred), the gaps the sampling leaves, three observations
with their times, and the structure counted in the style guide and the taste report; YouTube references watched by
Gemini from their link (no download), its cost in the reference budget, a refusal a plain reason, failed for good.
A real reel made at run time (three coloured shots and a tone, ffmpeg); Whisper and Claude stubbed; Gemini, YouTube's
oEmbed and its thumbnail a local fake server.
Run: python tests/quality/suite_refstudy.py (from studio/)"""
import http.server, io, json, os, subprocess, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"  # the fakes are on this machine
os.environ.update(ANTHROPIC_API_KEY="test", STUDIO_REFERENCE_VIDEO_COST_USD="0.07")
from PIL import Image
import settings, catalog, usage, style_study, ref_youtube, ref_library, taste, transcribe, cloud_sync

settings.ensure_folders()
conn = catalog.connect()
ref_youtube.BACKOFF = 0.0


# ── Claude, stubbed ───────────────────────────────────────────────────────────
class Claude:
    """Answers a reel read (READ), a style guide and taste reads; keeps every request."""

    def __init__(self):
        self.calls = []

    @property
    def beta(self):
        return NS(messages=NS(create=self.create, stream=self.stream))

    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "editor-in-chief for the Instagram Reels" in sysp:
            text = json.dumps({"content": "## House style (measured)\n- open result-first", "report": "r", "changes": "c"})
        elif "reading part of the reference library" in sysp:
            text = json.dumps({"design": "d", "content": "c", "engagement": "e", "hooks": [], "notable": [], "fonts_seen": []})
        elif "Write the taste report" in sysp:
            sec = {"text": "t", "examples": []}
            text = json.dumps({"summary": "s", "design": sec, "content": sec, "engagement": sec, "fonts": [],
                               "recommendations": [], "settings": [], "guide_update": "g", "taste_section": "ts"})
        else:
            text = json.dumps(READ)
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=9000, output_tokens=1400, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def stream(self, **kw):
        msg = self.create(**kw)

        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()


_design = {"palette": ["oak"], "materials": ["oak"], "lighting": "daylight", "camera": "handheld", "framing": "wide",
           "shot_types": ["wide reveal"], "text_style": "bold sans", "closest_free_fonts": ["Figtree"]}
_captions = {"present": True, "words_per_chunk": "1-3", "case": "upper", "font": "heavy sans", "accent_colours": [],
             "box": "none", "position": "middle"}
READ = {"layouts": ["talking_full"] * 7, "captions": _captions, "hook": "three mistakes", "first_frame": "red wall",
        "broll": [], "graphics": "none", "cta": "follow", "standout": "colour changes", "design": _design,
        # Compact "a | b" strings (the schema's shape since 2026-10-26: as objects and enums it was too
        # big to compile). One beat per still, in order; Claude may paraphrase what is said in a field
        # it wasn't asked for, and the kept beat must carry the transcript's words instead.
        "beats": ["a red wall | first beat", "a blue wall | PARAPHRASE | cut to blue", "a green wall | cut to green"],
        "arc": {"open": "list_promise | 0.3 | heard | promises three mistakes",
                "hold": "numbered_steps, made_up_device, fast cuts | - | both | counts them",
                "turn": "reveal | 4.1 s | seen | the green room",
                "close": "ask_follow | 99 | guessed | asks to follow"},
        "gaps": ["the speaker's face is never shown"],
        "observations": ["0.3 | heard | the promise lands in the first second",
                         "2.0 | both | each cut matches a new point",
                         "5.5 | inferred | the ask may be why it is saved",
                         "5.9 | seen | a fourth one"]}


# ── 1. The beat timeline (no API) ─────────────────────────────────────────────
speech = {"words": [[4.3, "last,"], [0.3, "Three"], [0.6, "mistakes."], [2.2, "First:"], [2.5, "tiles."], [5.2, "Follow."]],
          "join": " "}
beats = style_study.merge_beats([2.6, 0.15, 5.1], speech, [2.0, 4.0], 6.0)
ok([b["t"] for b in beats] == [0.15, 2.6, 5.1] and [b["end"] for b in beats] == [2.6, 5.1, 6.0],
   "beats in time order, each running to the next still (the last to the end)")
ok([b["said"] for b in beats] == ["Three mistakes. First: tiles.", "last,", "Follow."],
   "what is said goes to the beat it starts in, words in time order, exactly as transcribed")
ok(beats[0]["cuts"] == [2.0] and beats[1]["cuts"] == [4.0] and beats[2]["cuts"] == [],
   "the cuts inside each beat")
ok(style_study.merge_beats([1.0], {"words": [[0.1, "early"]], "join": " "}, [], 3.0)[0]["said"] == "early",
   "words before the first still go to the first beat")
zh = style_study.merge_beats([0.0], {"words": [[0.1, "你"], [0.2, "好"]], "join": ""}, [], 1.0)
ok(zh[0]["said"] == "你好", "Chinese words join without spaces")
times = style_study.still_times([float(k) for k in range(1, 30)], 30.0)
ok(len(times) == style_study.MAX_STILLS and times[-1] == 29.5 and times[1:6] == [0.5, 1.5, 2.5, 3.5, 4.5],
   f"30 shots: every early shot has a still, the rest spread to the close, the last shot always ({times})")
ok(style_study.still_times([2.0, 4.0, 6.0, 8.0, 10.0], 12.0) == [0.15, 1.0, 3.0, 5.0, 7.0, 9.0, 11.0],
   "a few shots: one still in each, as before")
gaps = style_study.sampling_gaps(times, [float(k) for k in range(1, 30)], 30.0, heard=True)
unseen = sum(int(g.split(": ")[1].split()[0]) for g in gaps)
ok(gaps and all("no still landed in" in g for g in gaps) and gaps[0].startswith("6.0-10.0 s: 4 shots") and unseen == 30 - 11,
   f"the shots no still landed in are measured gaps, every one of them ({gaps[:2]}, {unseen} shots)")
long_gap = style_study.sampling_gaps([0.15, 1.0, 20.0], [], 22.0, heard=False)
ok(any("one still for 19 s" in g for g in long_gap) and any("no speech" in g for g in long_gap),
   "a long stretch with one still and no speech are gaps too")


# ── 2. A real reel through analyse(): one call, the timeline in, the read kept ─
video = P / "reel.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex",
                "color=c=red:s=540x960:d=2:r=25[a];color=c=blue:s=540x960:d=2:r=25[b];color=c=green:s=540x960:d=2:r=25[c];"
                "[a][b][c]concat=n=3:v=1:a=0[v];sine=f=220:d=6[s]", "-map", "[v]", "-map", "[s]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
WORDS = [{"start": s, "end": s + 0.25, "word": w} for s, w in
         [(0.3, "Three"), (0.6, "mistakes."), (2.2, "First:"), (2.5, "tiles."), (4.3, "Last,"), (4.6, "lights."), (5.3, "Follow.")]]
transcribe.transcribe = lambda wav: {"language": "en", "text": " ".join(w["word"] for w in WORDS),
                                     "segments": [{"start": 0.3, "end": 5.6, "text": "..."}], "words": WORDS}
claude = Claude()
res = style_study.analyse(video, P / "stills", client=claude, ref="ref:1")
reads = [c for c in claude.calls if "study short vertical videos" in c.get("system", "")]
ok(len(claude.calls) == 1 and len(reads) == 1, "one Claude call for the reel (no second call)")
content = reads[0]["messages"][0]["content"]
heads = [c["text"] for c in content if c["type"] == "text" and c["text"].startswith("Beat at")]
images = [c for c in content if c["type"] == "image"]
ok(len(heads) == len(images) == len(res["stills"]) and content[0]["text"].startswith("Beat at 0.1"),
   f"each still comes as a beat, its label first ({len(heads)} beats)")
ok([float(h.split()[2][:-1]) for h in heads] == sorted(float(h.split()[2][:-1]) for h in heads),
   "the beats go to Claude in time order")
ok('said: "Three mistakes."' in heads[0] and any('"First: tiles."' in h for h in heads) and any("cuts at 2.0" in h for h in heads),
   "the timed words and the cuts are merged into the beats Claude reads")
ok(not any(c["type"] == "text" and c["text"].startswith("Transcript") for c in content),
   "no separate transcript block when the words are timed (the beats carry them)")
ok(content[-1]["type"] == "text" and "Measured sampling gaps" in content[-1]["text"], "the measured gaps follow the timeline")
ok("beats" in style_study.LOOK_SCHEMA["required"] and "arc" in style_study.LOOK_SCHEMA["required"]
   and "observations" in style_study.LOOK_SCHEMA["required"], "the schema asks for beats, the arc and the observations")
ok("inferred" in reads[0]["system"] and "gaps" in reads[0]["system"], "the prompt asks to mark inference and gaps")
lk, m = res["look"], res["measured"]
ok("_speech" not in m and m["cuts"] == 2 and m["words"] == 7, "the timed words aren't kept in the analysis; the numbers are")
ok(all(set(b) <= {"t", "screen", "said", "changed", "cuts"} for b in lk["beats"]) and len(lk["beats"]) == len(res["stills"]),
   "the beat timeline is kept compact, one beat per still")
ok(lk["beats"][0]["said"] == "Three mistakes." and "PARAPHRASE" not in json.dumps(lk["beats"]),
   "a kept beat says what the transcript says, never Claude's paraphrase")
ok(lk["beats"][0]["screen"] == "a red wall" and any(b["screen"] == "a green wall" for b in lk["beats"]),
   "what's on screen comes from Claude's read of each beat")
ok(lk["arc"]["open"]["basis"] == "heard" and lk["arc"]["turn"]["basis"] == "seen" and lk["arc"]["hold"]["basis"] == "both",
   "seen / heard / both pass through")
ok(lk["arc"]["close"]["basis"] == "inferred" and lk["arc"]["close"]["at"] == m["duration"],
   "an unknown basis is kept as inferred, a time past the end is clamped to the reel")
ok(lk["arc"]["hold"]["devices"] == ["numbered_steps", "fast_cuts"], "a made-up device is dropped")
ok(lk["observations"][2]["basis"] == "inferred", "an inferred observation stays marked inferred")
ok([o["at"] for o in lk["observations"]] == [0.3, 2.0, 5.5] and len(lk["observations"]) == 3,
   "the three highest-signal observations are kept with their times (a fourth dropped)")
ok("the speaker's face is never shown" in lk["gaps"], "Claude's own gaps are kept after the measured ones")
ok(json.dumps(lk).__len__() < 4000, f"the whole read stays small ({len(json.dumps(lk))} characters)")

# Without timed words (an old transcript): the transcript goes as before and the gap says so.
claude2 = Claude()
lk2 = style_study.look([(t, Path(p)) for t, p in zip([0.15, 2.6], res["stills"])], "Three mistakes", "ref:2", claude2,
                       speech=None, cuts=[2.0], duration=6.0)
c2 = claude2.calls[0]["messages"][0]["content"]
ok(any(c["type"] == "text" and c["text"].startswith("Transcript (not timed)") for c in c2)
   and any("no times" in g for g in lk2["gaps"]) and all(b["said"] == "" for b in lk2["beats"]),
   "an untimed transcript is sent whole, the beats say nothing and a gap says why")


# ── 3. The structure reaches the style guide and the taste report ─────────────
def ref_row(i, side, opens, close, turn_at=4.0, inferred=False, estimated=False):
    look = {k: v for k, v in res["look"].items()}
    look["arc"] = {"open": {"pattern": opens, "what": "o", "at": 0.3, "basis": "inferred" if inferred else "seen"},
                   "hold": {"devices": ["fast_cuts"], "what": "h", "basis": "seen"},
                   "turn": {"pattern": "reveal", "what": "t", "at": turn_at, "basis": "seen"},
                   "close": {"pattern": close, "what": "c", "at": 5.5, "basis": "heard"}}
    meas = dict(m, duration=8.0, first_cut=99.0 if estimated else 1.0)
    if estimated:
        meas["estimated_by"] = "gemini-test"
    return {"id": i, "title": f"r{i}", "side": side, "why": "pace", "url": f"https://x/{i}", "measured": meas, "look": look}


refs = [ref_row(1, "ours", "result_first", "ask_follow"), ref_row(2, "ours", "result_first", "ask_save_share"),
        ref_row(3, "ours", "question", "ask_follow", inferred=True), ref_row(4, "inspiration", "result_first", "loop"),
        ref_row(5, "inspiration", "problem", "loop", estimated=True)]
summary = style_study.summarise(refs)
st = summary["ours"]["structure"]
ok(st["read"] == 3 and st["opens"] == {"result_first": 2, "question": 1} and st["closes"] == {"ask_follow": 2, "ask_save_share": 1},
   f"the open and close patterns are counted across references ({st['opens']}, {st['closes']})")
ok(st["inferred"]["open"] == 1 and st["turn_at_s_median"] == 4.0 and st["turn_at_share_median"] == 0.5,
   "inferred findings are counted apart; where it turns, in seconds and share of the length")
ok(summary["inspiration"]["first_cut"]["median"] == 1.0 and summary["inspiration"]["estimated_not_measured"] == 1,
   "a YouTube reference's estimated numbers stay out of the medians (counted apart)")
gc = Claude()
style_study.draft_guide("urban_makers", refs, None, added=refs, client=gc, ref="guide:test")
gsys, gev = gc.calls[0]["system"], gc.calls[0]["messages"][0]["content"]
ev = json.loads(gev.split("Evidence:\n", 1)[1])
ok(ev["summary"]["ours"]["structure"]["opens"]["result_first"] == 2, "the structure counts reach the style guide's evidence")
ok("open, hold, turn and close" in gsys and "inferred" in gsys, "the guide prompt asks for structure rules with their counts")
ok(all("beats" not in r["look"] for r in ev["references"]) and all("arc" in r["look"] for r in ev["references"]),
   "the guide gets each reference's structure and observations, not its whole beat timeline")
ok(any(r.get("measured_by", "").startswith("estimated by gemini-test") for r in ev["references"]),
   "the guide is told which numbers were estimated by watching")

items = []
for k in range(12):
    r = ref_row(100 + k, "inspiration", "result_first" if k % 3 else "question", "ask_follow" if k % 2 else "loop")
    a = {"measured": r["measured"], "look": r["look"], "kind": "video",
         "engagement": {"likes": 100 + 50 * k, "comments": 5, "views": 1000, "account": f"a{k}"}}
    items.append(taste.compact({"id": r["id"], "account": f"a{k}", "side": "inspiration", "post_type": "reel"}, a))
ok(items[0]["arc"]["open"]["pattern"] == "question" and items[0]["arc"]["signal"]["at"] == 0.3
   and len(json.dumps(items[0]["arc"])) < 600, "the taste report's compact read carries the arc and the strongest signal, short")
tst = taste.stats(items)
ok(tst["video"]["structure"]["read"] == 12 and tst["video"]["structure"]["opens"] == {"result_first": 8, "question": 4},
   "the taste report's stats count the structure")
ok(any(k.startswith("opens: ") for k in tst["engagement"]["video"]["compare"]),
   "the better-performing quarter vs the rest by how they open")
tc = Claude()
taste.draft("urban_makers", items, tc, ref="taste:test")
maps = [c for c in tc.calls if "reading part of the reference library" in c["system"]]
rep = [c for c in tc.calls if "Write the taste report" in c["system"]]
ok(maps and '"arc"' in maps[0]["messages"][0]["content"] and "arc" in maps[0]["system"],
   "each map read sees the arcs and is told what they are")
ok(rep and '"structure"' in rep[0]["messages"][0]["content"] and "stats.video.structure" in rep[0]["system"],
   "the report call gets the structure counts and is asked to use them")


# ── 4. YouTube: Gemini watches it from the link ───────────────────────────────
GEM = {"calls": [], "modes": [], "gets": []}


def gemini_answer():
    return {"layouts": ["talking_full", "broll_full"], "captions": _captions, "hook": "h", "first_frame": "f",
            "broll": ["tiles"], "graphics": "none", "cta": "follow", "standout": "s", "design": _design,
            "beats": ["0 | a man in a kitchen | Stop doing this | first beat",
                      "6.5 | tiles close up |  | cut to the tiles"],
            "arc": {"open": "mistake_warning | 0 | both | stop doing this",
                    "hold": "demonstration | - | seen | shows it",
                    "turn": "none | 3 | inferred | never turns",
                    "close": "summary | 30 | heard | sums up"},
            "gaps": ["the text at 12 s was too small to read"],
            "observations": ["0 | heard | a warning first", "6.5 | seen | the close-up proves it",
                             "25 | inferred | probably saved for the tip"],
            "numbers": {"duration": 31.0, "cuts": 9, "first_cut": 1.2, "shot_median": 2.8, "words": 80,
                        "speech_start": 0.2, "speech_end": 30.2},
            "transcript": "Stop doing this ..."}


def thumb_jpeg():
    out = io.BytesIO()
    Image.new("RGB", (480, 360), (200, 160, 120)).save(out, "JPEG")
    return out.getvalue()


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        GEM["gets"].append(self.path)
        if self.path.startswith("/oembed"):
            self._send(200, {"title": "Tile mistakes", "author_name": "Tile Guy", "author_url": "https://www.youtube.com/@tileguy",
                             "thumbnail_url": f"http://127.0.0.1:{PORT}/vi/abc/hq.jpg"})
        else:
            self._send(200, thumb_jpeg(), "image/jpeg")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        GEM["calls"].append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
        mode = GEM["modes"].pop(0) if GEM["modes"] else "ok"
        meta = {"promptTokenCount": 9300, "candidatesTokenCount": 2100, "thoughtsTokenCount": 800}
        if mode == "blocked":
            return self._send(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "usageMetadata": meta})
        if mode == "safety":
            return self._send(200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}], "usageMetadata": meta})
        if mode == "busy":
            return self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "slow down"}})
        if mode == "down":
            return self._send(503, {"error": {"code": 503, "message": "overloaded"}})
        if mode == "nomodel":
            return self._send(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "models/x is not found"}})
        if mode == "private":
            return self._send(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                              "message": "Cannot fetch content from the provided URL: the video is private"}})
        parts = [{"text": "thinking...", "thought": True}, {"text": json.dumps(gemini_answer())}]
        self._send(200, {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}], "usageMetadata": meta})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
settings.GEMINI_URL = f"http://127.0.0.1:{PORT}/v1beta"
settings.GEMINI_API_KEY = "test-gemini-key"
settings.REFERENCE_VIDEO_MODEL = "gemini-test-video"
ref_youtube.OEMBED_URL = f"http://127.0.0.1:{PORT}/oembed"
downloads = []
style_study.download_post = lambda *a, **k: downloads.append(a) or (_ for _ in ()).throw(AssertionError("downloaded"))
style_study.fetch_slides = lambda *a, **k: downloads.append(a) or (_ for _ in ()).throw(AssertionError("downloaded"))

for u in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "https://youtu.be/dQw4w9WgXcQ",
          "https://youtube.com/shorts/dQw4w9WgXcQ"):
    ok(ref_youtube.is_youtube(u) and ref_youtube.canonical(u) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
       f"{u.split('//')[1][:22]}... is a YouTube reference")
ok(not ref_youtube.is_youtube("https://www.instagram.com/reel/ABC/") and not ref_youtube.is_youtube("https://youtube.com/@chan"),
   "an Instagram link or a channel page isn't")

spent0 = ref_library.month_spent(conn)
yt = style_study.analyse("https://youtube.com/shorts/dQw4w9WgXcQ", P / "yt-stills", client=Claude(), ref="ref:7",
                         kind="images")
call = GEM["calls"][-1]
part = call["body"]["contents"][0]["parts"][0]
ok(not downloads, "no download was attempted")
ok(part["file_data"]["file_uri"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
   and part["video_metadata"]["end_offset"] == f"{settings.REFERENCE_VIDEO_MAX_SECONDS}s",
   "the video goes to Gemini as file_data with file_uri = the YouTube link, clipped to the setting")
ok(call["path"] == "/v1beta/models/gemini-test-video:generateContent" and call["key"] == "test-gemini-key",
   "the model is the setting, the key in its header")
gc_ = call["body"]["generationConfig"]
schema_text = json.dumps(gc_["responseSchema"])
ok(gc_["responseMimeType"] == "application/json" and gc_["responseSchema"]["type"] == "OBJECT"
   and "additionalProperties" not in schema_text and "anyOf" not in schema_text and '"nullable": true' in schema_text,
   "structured JSON with a responseSchema in Gemini's shape (capital types, nullable, no additionalProperties)")
props = gc_["responseSchema"]["properties"]
ok(all(k in props for k in ("beats", "arc", "gaps", "observations", "numbers", "layouts", "captions", "design")),
   "it asks for the beat timeline, the structure and the visual measures")
ok(yt["kind"] == "video" and yt["measured"]["estimated_by"] == "gemini-test-video" and yt["measured"]["cuts"] == 9
   and yt["measured"]["words_per_sec"] == 2.67, "the numbers are kept, marked as Gemini's estimates")
ok(yt["look"]["beats"][0] == {"t": 0.0, "screen": "a man in a kitchen", "said": "Stop doing this", "changed": "first beat"},
   "Gemini's beats are kept in the same compact shape")
ok(yt["look"]["arc"]["turn"]["pattern"] == "none" and yt["look"]["arc"]["turn"]["at"] is None
   and yt["look"]["arc"]["turn"]["basis"] == "inferred" and yt["look"]["arc"]["close"]["at"] == 30.0,
   "the structure tidied the same way (no turn has no time)")
ok(yt["look"]["gaps"][0].startswith("Gemini watched it at about one frame a second")
   and "the text at 12 s was too small to read" in yt["look"]["gaps"] and len(yt["look"]["observations"]) == 3,
   "the sampling gap is marked, Gemini's own gaps and three observations kept")
ok(yt["engagement"]["account"] == "tileguy" and yt["engagement"]["source"] == "youtube" and yt["engagement"]["views"] is None,
   "the channel comes from YouTube's oEmbed; numbers YouTube didn't give are None")
ok(len(yt["stills"]) == 1 and Image.open(yt["stills"][0]).size[0] == 480, "YouTube's thumbnail is the one still")
row = conn.execute("SELECT kind, ref, model, cost_usd, input_tokens, output_tokens FROM usage WHERE ref = 'ref:7'").fetchall()
ok(len(row) == 1 and tuple(row[0])[:4] == ("style", "ref:7", "gemini-test-video", 0.07) and row[0][4] == 9300 and row[0][5] == 2900,
   "one call recorded at the per-call estimate, kind 'style', with its tokens")
ok(abs(ref_library.month_spent(conn) - spent0 - 0.07) < 1e-6, "its cost counts toward the month's reference budget")

# A refusal, a private video, no key, an unknown model: a plain reason, failed for good where it is the video.
def fails(mode, url="https://youtu.be/dQw4w9WgXcQ"):
    GEM["modes"] = list(mode) if isinstance(mode, list) else [mode]
    before = len(GEM["calls"])
    try:
        style_study.analyse(url, P / "yt2", ref="ref:8")
        return None, len(GEM["calls"]) - before
    except style_study.DownloadFailed as e:
        return e, len(GEM["calls"]) - before


e, n = fails("blocked")
ok(e and e.problem == "gone" and "wouldn't describe this video" in str(e) and n == 1,
   f"Gemini's filter: a plain reason, failed for good, one call ({e})")
e, n = fails("safety")
ok(e and e.problem == "gone" and "SAFETY" in str(e) and n == 1, "a refused answer (finishReason SAFETY) the same")
e, n = fails("private")
ok(e and e.problem == "gone" and "private, age-restricted" in str(e), "a private video: a plain reason, failed for good")
e, n = fails("nomodel")
ok(e and e.problem == "other" and "STUDIO_REFERENCE_VIDEO_MODEL" in str(e) and n == 1, "an unknown model names the setting")
e, n = fails(["down", "down", "down"])
ok(e and n == 2 and "tried again later" in str(e), "Gemini down: one more try, then a plain reason (not retried on and on)")
e, n = fails(["busy", "ok"])
ok(e is None and n == 2, "busy once: the second try works")
settings.GEMINI_API_KEY = ""
e, n = fails("ok")
ok(e and n == 0 and "no Gemini key" in str(e) and "Studio Inbox > References" in str(e),
   "no key: a plain reason with what to do, and no call")
settings.GEMINI_API_KEY = "test-gemini-key"
paid = conn.execute("SELECT COUNT(*) FROM usage WHERE ref = 'ref:8'").fetchone()[0]
ok(paid == 3, f"only the calls Gemini answered are counted as spent (two refusals and one good answer: {paid})")

# Through the sync: stored like any analysis; a refusal fails for good, not retried by itself.
class Api:
    def __init__(self):
        self.rows, self.uploads = {}, []

    def update(self, table, where, values):
        if table != "reference_analysis":
            return []
        rid = int(where["reference_id"][3:])
        row = self.rows.setdefault(rid, {"reference_id": rid, "state": "pending", "stills": []})
        if "state" in where and where["state"] != f"eq.{row['state']}":
            return []
        row.update(values)
        return [dict(row)]

    def upload(self, path, data):
        self.uploads.append(path)

    def request(self, method, path, **kw):
        return []


api = Api()
cloud_sync._library_online, cloud_sync._library_checked = False, 0.0
GEM["modes"] = ["ok"]
line, how = cloud_sync.analyse_one(api, {"id": 9, "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "yt"},
                                   {"state": "pending"}, client=Claude())
r9 = api.rows[9]
ok(how == "ok" and r9["state"] == "done" and r9["look"]["arc"]["open"]["pattern"] == "mistake_warning"
   and r9["measured"]["estimated_by"] and r9["stills"] == ["refs/9/1.jpg"] and r9["kind"] == "video",
   "the sync stores a YouTube analysis in the same columns (measured, look, stills)")
GEM["modes"] = ["blocked"]
line, how = cloud_sync.analyse_one(api, {"id": 10, "url": "https://youtu.be/dQw4w9WgXcQ", "title": "yt", "import_id": 3},
                                   {"state": "pending"}, client=Claude(), library=True)
r10 = api.rows[10]
ok(r10["state"] == "failed" and r10["problem"] == "gone" and "wouldn't describe" in r10["error"],
   "a library reference Gemini refuses fails with the plain reason (not back in the queue)")
old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
now = datetime.now(timezone.utc)
ok(not cloud_sync._due({**r10, "updated_at": old}, now), "a refused (gone) reference isn't retried by itself, days later")
ok(cloud_sync._due({"state": "failed", "problem": "other", "analyser": style_study.ANALYSER, "updated_at": old}, now),
   "another failure (say, no key yet) is still tried again after a day")

finish()
