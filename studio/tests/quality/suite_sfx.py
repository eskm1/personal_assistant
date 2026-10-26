"""Soft sound effects on a Reel's graphics (sfx.py; Bryan, 2026-10-26, "yes ok": "a gentle whoosh as a label slides in,
a tick on the numbers panel, a soft chime on the end card. Calm, never jarring"; Epidemic Sound's effects, Openverse
only its CC0 / public domain ones, no generators).
A talking head made at run time (fixtures/person.jpg, flite speech with known word edges). Epidemic Sound's MCP server
and Openverse's API are local fakes (small HTTP servers answering in their shapes and counting every request; their
effect files are soft test sounds drawn with numpy here, test media only); Claude (the plan, the read), music and the
Whisper re-check stubbed; everything else real: the search, the licence filter, the cache, the placement, the mix in
the draft's one encode, the read's material, the notes, the patch.
Run: python tests/quality/suite_sfx.py (from studio/)."""
import http.server, io, json, os, re, shutil, subprocess, threading, time, wave
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish, load_before
import media

import numpy as np

t_media = time.time()
M = media.ending_speech(P / "media")
DATA = json.loads((M / "words.json").read_text())
TALK = media.person_video(P / "media" / "person.mp4", (1080, 1920), DATA["total"], "540", 640, 3.0, audio=M / "talk.wav")
print(f"(media made in {time.time() - t_media:.0f}s)")


# ── Soft test sounds (test media only: Studio never ships or makes its own) ───
def _wav(x, rate=48000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def sound(kind: str, lead: float = 0.12) -> bytes:
    """A whoosh (noise swelling and fading over 0.8 s), a tick (a 25 ms click), a chime (two soft partials ringing
    out over 1.5 s), each after `lead` seconds of silence (Studio trims it)."""
    r = 48000
    rng = np.random.default_rng(len(kind))
    if kind == "whoosh":
        t = np.arange(int(0.8 * r)) / r
        n = np.convolve(rng.normal(0, 1, len(t)), np.ones(24) / 24, "same")
        x = 0.4 * n * np.sin(np.pi * t / 0.8) ** 2
    elif kind == "tick":
        t = np.arange(int(0.025 * r)) / r
        x = 0.5 * np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.006)
    else:
        t = np.arange(int(1.5 * r)) / r
        x = 0.3 * (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t)) * np.exp(-t / 0.45)
    return _wav(np.concatenate([np.zeros(int(lead * r)), x, np.zeros(int(0.1 * r))]))


FILES = {"soft-whoosh.wav": sound("whoosh"), "soft-tick.wav": sound("tick"), "soft-chime.wav": sound("chime"),
         "air.wav": sound("whoosh", 0.2), "by.wav": sound("whoosh"), "bync.wav": sound("chime"), "music.wav": sound("chime"),
         "impact.wav": sound("whoosh"), "badurl.wav": sound("tick"),
         "epi-whoosh.wav": sound("whoosh", 0.05), "epi-tick.wav": sound("tick"), "epi-chime.wav": sound("chime")}


# ── The fakes: Openverse's API and Epidemic Sound's MCP server ────────────────
NET = {"searches": [], "files": [], "headers": [], "mcp": [], "openverse": "ok", "epidemic_sfx": False}


def ov_item(i, title, licence, file, category="sound_effect", url=None, ms=900):
    return {"id": f"ov-{i}", "title": title, "creator": f"maker{i}", "license": licence, "license_version": "1.0",
            "license_url": url or {"cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
                                   "pdm": "https://creativecommons.org/publicdomain/mark/1.0/"}.get(
                                       licence, f"https://creativecommons.org/licenses/{licence}/4.0/"),
            "provider": "freesound", "source": "freesound", "category": category, "filetype": "wav", "duration": ms,
            "foreign_landing_url": f"https://freesound.org/s/{i}/", "url": f"{BASE}/files/{file}", "mature": False}


def ov_results(q: str) -> list[dict]:
    """Every search first offers what Studio must never take: CC BY, CC BY-NC, a CC0 "music" item, a harsh title, a
    "CC0" whose licence link says otherwise; then the soft one that fits."""
    bad = [ov_item(1, "Soft whoosh (attribution)", "by", "by.wav"), ov_item(2, "Gentle chime", "by-nc", "bync.wav"),
           ov_item(3, "Soft music loop", "cc0", "music.wav", category="music"),
           ov_item(4, "Heavy impact whoosh", "cc0", "impact.wav"),
           ov_item(5, "Soft tick", "cc0", "badurl.wav", url="https://creativecommons.org/licenses/by/4.0/")]
    good = {"whoosh": ov_item(10, "Soft whoosh", "cc0", "soft-whoosh.wav"), "swish": ov_item(10, "Soft whoosh", "cc0", "soft-whoosh.wav"),
            "tick": ov_item(11, "Soft tick", "pdm", "soft-tick.wav", ms=150), "click": ov_item(11, "Soft tick", "pdm", "soft-tick.wav"),
            "chime": ov_item(12, "Soft chime", "cc0", "soft-chime.wav", ms=1700)}
    return bad + [v for k, v in good.items() if k in q][:1]


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if u.path == "/v1/audio/":
            q = parse_qs(u.query)
            NET["searches"].append({k: v[0] for k, v in q.items()})
            NET["headers"].append(dict(self.headers))
            if NET["openverse"] == "down":
                return self._send(503, b'{"detail":"down"}')
            res = ov_results(q.get("q", [""])[0])
            return self._send(200, json.dumps({"result_count": len(res), "results": res}).encode())
        if u.path.startswith("/files/") or u.path.startswith("/epi/"):
            name = u.path.split("/")[-1]
            NET["files"].append(name)
            NET["headers"].append(dict(self.headers))
            if u.path.startswith("/epi/") and self.headers.get("Authorization") != "Bearer test-epidemic":
                return self._send(401, b"{}")
            return self._send(200, FILES[name], "audio/wav")
        self._send(404, b"{}")

    def do_POST(self):  # Epidemic Sound's MCP server (Streamable HTTP, JSON-RPC)
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        NET["mcp"].append(msg)
        if "id" not in msg:
            return self._send(202, b"")
        m, result = msg["method"], {}
        if m == "initialize":
            result = {"serverInfo": {"name": "Epidemic Sound (fake)"}, "capabilities": {"tools": {}}}
        elif m == "tools/list":
            tools = [{"name": "SearchRecordings", "description": "Search Epidemic Sound's music catalogue",
                      "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
                     {"name": "DownloadRecording", "description": "Download a music track",
                      "inputSchema": {"type": "object", "properties": {"recordingId": {"type": "string"}}}}]
            if NET["epidemic_sfx"]:
                tools += [{"name": "SearchSoundEffects", "description": "Search Epidemic Sound's sound effects library",
                           "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                                                                            "limit": {"type": "integer"}},
                                           "required": ["query"]}},
                          {"name": "DownloadSoundEffect", "description": "A download link for a sound effect",
                           "inputSchema": {"type": "object", "properties": {"soundEffectId": {"type": "string"}},
                                           "required": ["soundEffectId"]}}]
            result = {"tools": tools}
        elif m == "tools/call":
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            if name == "SearchSoundEffects":
                q = args["query"]
                kind = "whoosh" if ("whoosh" in q or "swish" in q) else "tick" if "tick" in q else "chime" if "chime" in q else ""
                items = [{"id": "sfx-impact", "title": "Heavy Impact Hit", "durationMs": 900}] + (
                    [{"id": f"sfx-{kind}", "title": f"Soft {kind.title()}", "durationMs": 800}] if kind else [])
                result = {"content": [{"type": "text", "text": json.dumps({"soundEffects": items})}]}
            elif name == "DownloadSoundEffect":
                result = {"content": [{"type": "text", "text": json.dumps(
                    {"url": f"{BASE}/epi/epi-{args['soundEffectId'].split('-', 1)[1]}.wav"})}]}
            else:
                result = {"content": [{"type": "text", "text": "[]"}]}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}).encode())


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test-epidemic", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0",
                  EPIDEMIC_SOUND_MCP_URL=f"{BASE}/mcp", STUDIO_OPENVERSE_URL=f"{BASE}/v1/audio/")
os.environ.pop("STUDIO_SFX", None); os.environ.pop("STUDIO_SFX_LEVEL_DB", None); os.environ.pop("STUDIO_SFX_MAX", None)
os.environ.pop("STUDIO_TITLE_STYLE", None)
import settings, catalog, extract, editor, transcribe, music, anthropic, sfx, fix_actions, draft_review, self_fix

settings.ensure_folders()
conn = catalog.connect()


def reset_net():
    for k in ("searches", "files", "headers", "mcp"):
        NET[k].clear()


def clear_cache():
    shutil.rmtree(sfx.folder(), ignore_errors=True)


# ── Claude, music and Whisper stubbed ─────────────────────────────────────────
class Claude:
    """The edit plan given; a clean read; no joins asked."""

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


def with_music(brief, seconds, out_dir, ref=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    return media.music_bed(out_dir / "bed.mp3", 60), {"title": "Test Bed", "artist": "ffmpeg", "recording_id": "t1"}


def no_music(brief, seconds, out_dir, ref=""):
    raise RuntimeError("no music in this test")


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
NUMBERS = {"clip_id": CID, "at": at("For"), "duration": 3.0, "style": "card", "title": "Drying times",
           "lines": ["Screed 2 days", "Tiles 1 day"], "scene": "", "look": "cutaway", "role": "explain", "why": "the numbers"}
CALLOUT = {"clip_id": CID, "at": at("It"), "duration": 2.4, "style": "callout", "title": "Porcelain", "lines": [],
           "scene": "", "look": "cutaway", "role": "explain", "why": "names it"}
PLAN = {"cuts": CUTS, "hook_text": "Floors *level* again", "key_words": ["screed"],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "A short talk.", "notes_addressed": [], "missing_shots": [], "graphics": [CALLOUT, NUMBERS]}


def production():
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Floors", "hook": "h",
                                    "angle": "x", "beats": [],
                                    "segments": [{"clip_id": CID, "start": 0, "end": DATA["total"], "purpose": "a"}],
                                    "caption": "cap", "est_seconds": 25, "cta": "Save this for your reno."}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    conn.commit()
    pid = catalog.make_production(conn, idea)
    plan = json.loads(conn.execute("select plan from productions where id=?", (pid,)).fetchone()[0])
    plan["cta"] = "Save this for your reno."
    conn.execute("update productions set plan=? where id=?", (json.dumps(plan), pid))
    conn.commit()
    return pid, Claude(json.loads(json.dumps(PLAN)))


def row(pid):
    return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())


def note(pid, body, at_s=None):
    conn.execute("update productions set status = 'revising' where id = ?", (pid,))
    conn.execute("""INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                    VALUES ('production', ?, ?, ?, ?, 'open', ?)""", (pid, row(pid)["version"], at_s, body, catalog.now()))
    conn.commit()


def sound_of(video: Path):
    """The finished file's sound, K-weighted mono samples (as the mix measures it)."""
    raw = P / "final.f32"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le",
                    str(raw)], check=True)
    return editor._read_raw(raw)


def peak(x, a, b, rate=48000):
    return float(editor._window_db(x[int(a * rate):int(b * rate)]).max())


# ── 1. Where the effects come from ────────────────────────────────────────────
print("\n== 1. Sources: Epidemic Sound's server when it offers sound effects, else Openverse's CC0 ones")
music_only = [{"name": "SearchRecordings", "description": "Search the music catalogue",
               "inputSchema": {"properties": {"query": {"type": "string"}}}},
              {"name": "DownloadRecording", "description": "Download a track",
               "inputSchema": {"properties": {"recordingId": {"type": "string"}}}}]
with_sfx = music_only + [{"name": "SearchSoundEffects", "description": "Search sound effects",
                          "inputSchema": {"properties": {"query": {"type": "string"}}, "required": ["query"]}}]
filtered = [{"name": "Search", "description": "Search Epidemic Sound",
             "inputSchema": {"properties": {"term": {"type": "string"},
                                            "type": {"type": "string", "enum": ["music", "sound_effects"]}}}}]
ok(sfx.sfx_tool(music_only) is None, "a server with music tools only: no sound effects tool")
ok(sfx.sfx_tool(with_sfx) == {"name": "SearchSoundEffects", "query": "query", "fixed": {}},
   "a sound effects search tool is found by its name")
ok(sfx.sfx_tool(filtered) == {"name": "Search", "query": "term", "fixed": {"type": "sound_effects"}},
   "or the search tool's own filter for sound effects")
ok(sfx.free({"license": "cc0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "category": "sound_effect"})
   and sfx.free({"license": "pdm", "category": "sound_effect"})
   and not sfx.free({"license": "by", "category": "sound_effect"})
   and not sfx.free({"license": "by-nc", "category": "sound_effect"})
   and not sfx.free({"license": "cc0", "license_url": "https://creativecommons.org/licenses/by/4.0/"})
   and not sfx.free({"license": "cc0", "category": "music"}),
   "Openverse: CC0 and the public domain mark only, sound effects only")

# Epidemic's server with no sound effects tool: it is asked, then Openverse.
reset_net(); clear_cache()
found, notes = sfx.ensure(["in", "count", "end"], "urban_makers")
listed_asks = [m for m in NET["mcp"] if m.get("method") == "tools/list"]
ok(len(listed_asks) == 3 and not any(m.get("method") == "tools/call" for m in NET["mcp"]),
   f"Epidemic's server is asked what it offers (tools/list, {len(listed_asks)}x), no sound effects tool: nothing called")
ok(set(found) == {"in", "count", "end"} and all(e["source"] == "openverse" for e in found.values()),
   f"so Openverse's: {[(r, e['title'], e['licence']) for r, e in found.items()]}")
ok(all(e["licence"] in ("cc0", "pdm") for e in found.values())
   and not {"by.wav", "bync.wav", "music.wav", "impact.wav", "badurl.wav"} & set(NET["files"]),
   f"only CC0 / public domain effects: nothing else even downloaded (fetched {sorted(set(NET['files']))})")
ok(all(s.get("license") == "cc0,pdm" and s.get("category") == "sound_effect" and s.get("length") == "shortest"
       for s in NET["searches"]) and {s["q"] for s in NET["searches"]} <= {q for r in sfx.ROLES.values() for q in r["queries"]}
   and NET["searches"][0]["q"].startswith("soft"),
   f"searched for short CC0 sound effects with calm, soft words: {sorted({s['q'] for s in NET['searches']})}")
ok(not any("Authorization" in h or "authorization" in h for h in NET["headers"]),
   "no key is ever sent to Openverse or its files' hosts")
idx = json.loads((sfx.folder() / "index.json").read_text())
e = idx["effects"]["end"][0]
ok(e["id"] == "ov-12" and e["title"] == "Soft chime" and e["licence"] == "cc0" and e["page"].startswith("https://freesound.org")
   and e["creator"] == "maker12" and (sfx.folder() / e["file"]).exists() and (sfx.folder() / e["original"]).exists(),
   f"kept in Studio/SoundEffects with its source, id, title, maker and licence: {e['id']} {e['title']!r} {e['licence']}")
ok(abs(found["in"]["seconds"] - 0.8) < 0.08 and found["count"]["seconds"] <= 0.3 and found["end"]["seconds"] <= 1.6,
   f"made ready: silence at the start trimmed, at most each role's length ({found['in']['seconds']}, "
   f"{found['count']['seconds']}, {found['end']['seconds']} s)")
n_files, n_search, n_mcp = len(NET["files"]), len(NET["searches"]), len(NET["mcp"])
again, _ = sfx.ensure(["in", "count", "end"], "urban_makers")
ok(again == found and len(NET["files"]) == n_files and len(NET["searches"]) == n_search and len(NET["mcp"]) == n_mcp,
   "the next draft uses the kept effects: no search, no download, Epidemic not asked")

# Epidemic's server offering sound effects: its own, searched softly, downloaded with the key.
reset_net(); clear_cache()
NET["epidemic_sfx"] = True
found, _ = sfx.ensure(["in", "count", "end"], "urban_makers")
calls = [m["params"] for m in NET["mcp"] if m.get("method") == "tools/call"]
ok(all(e["source"] == "epidemic" for e in found.values()) and not NET["searches"],
   f"Epidemic's sound effects when its server offers them; Openverse not asked: {[(r, e['title']) for r, e in found.items()]}")
ok([c["arguments"]["query"] for c in calls if c["name"] == "SearchSoundEffects"][:1] == ["soft whoosh"]
   and not any(c["arguments"].get("soundEffectId") == "sfx-impact" for c in calls),
   f"searched with calm words; a harsh one (\"Heavy Impact Hit\") never taken: {[c['name'] for c in calls]}")
ok(found["in"]["licence_name"].startswith("Epidemic Sound subscription") and found["in"]["id"] == "sfx-whoosh",
   f"its licence is Bryan's subscription: {found['in']['licence_name']}")
ok(any(h.get("Authorization") == "Bearer test-epidemic" for h in NET["headers"]),
   "Epidemic's own file link gets the key (it asked for it)")
music.allowed_for = lambda b: b == "urban_makers"
reset_net()
other, _ = sfx.ensure(["in"], "personal")
ok(other["in"]["source"] == "openverse" and not NET["mcp"],
   "an account Epidemic's licence doesn't cover: Openverse's, Epidemic never asked")
music.allowed_for = lambda b: True
NET["epidemic_sfx"] = False

# Openverse down: no effect, no draft broken, not asked again for a while.
reset_net(); clear_cache()
NET["openverse"] = "down"
found, notes = sfx.ensure(["in"], "urban_makers")
n = len(NET["searches"])
found2, _ = sfx.ensure(["in"], "urban_makers")
ok(not found and notes and n == 1 and len(NET["searches"]) == 1,
   f"Openverse down: no whoosh this time ({notes[0]}), not asked again for {sfx.RETRY_HOURS} hours")
NET["openverse"] = "ok"
clear_cache()

# ── 2. Where they go ──────────────────────────────────────────────────────────
print("\n== 2. Placement: sparse, clear of the hook, the cuts and his loud words; the end chime")
FX = {"in": {"seconds": 0.8, "body": [0.1, 0.6]}, "count": {"seconds": 0.2, "body": [0.0, 0.05]},
      "end": {"seconds": 1.5, "body": [0.0, 0.4]}, "picture": {"seconds": 1.0, "body": [0.1, 0.6]}}


def cue(role, t, ticks=(0.0,), title="x"):
    return {"role": role, "at": t, "ticks": list(ticks), "for": f"{role}:{t}", "title": title}


placed, left = sfx.place([cue("in", 0.5), cue("in", 4.0)], FX, [], [], 30.0)
ok([p["at"] for p in placed] == [4.0], f"none in the hook's first second ({left})")
placed, _ = sfx.place([cue("in", 3.0), cue("in", 3.8), cue("in", 5.0)], FX, [], [], 30.0)
ok([p["at"] for p in placed] == [3.0, 5.0], f"never two within {sfx.GAP} s: {[p['at'] for p in placed]}")
many = [cue("in", 2.0 + 2.0 * k) for k in range(14)] + [cue("end", 29.0)]
placed, _ = sfx.place(many, FX, [], [], 30.0)
ok(len(placed) == 5 and placed[-1]["role"] == "end", f"at most one per {sfx.EVERY:.0f} s: 5 in a 30 s draft, the end chime "
                                                     f"among them ({[p['at'] for p in placed]})")
placed, _ = sfx.place([cue("in", 3.0), cue("in", 6.0), cue("end", 9.0)], FX, [], [], 10.0)
ok([p["role"] for p in placed] == ["end"], "a 10 s draft: one, and it's the end chime")
settings.SFX_MAX = 2
placed, _ = sfx.place(many, FX, [], [], 30.0)
ok(len(placed) == 2, "and never more than STUDIO_SFX_MAX")
settings.SFX_MAX = 5
placed, _ = sfx.place([cue("in", 5.1), cue("in", 10.0)], FX, [], [5.0, 10.1], 30.0)
ok([p["at"] for p in placed] == [5.4] and placed[0]["moved"] == 0.3,
   f"none within {sfx.CUT_CLEAR} s of a cut: one moved clear (5.1 -> {placed[0]['at']}), one that can't be is left out")
word = [{"word": "screed", "start": 7.0, "end": 7.6}]
loud = sfx.loud_parts(word)
placed, _ = sfx.place([cue("in", 7.2)], FX, word, [], 30.0)
p = placed[0]
ok(abs(p["moved"]) <= sfx.SHIFT and sfx._overlap(p["at"] + 0.1, p["at"] + 0.6, loud) == 0 and p["duck_db"] == sfx.DUCK_DB,
   f"off a word's loudest part ({loud[0]}): moved {p['moved']:+.2f} s, and quieter where it still overlaps the word")
two = [{"word": "a", "start": 4.4, "end": 5.0}, {"word": "b", "start": 5.1, "end": 5.7}]
placed, _ = sfx.place([cue("in", 4.9)], FX, two, [], 30.0)
p = placed[0] if placed else {}
on = sfx._overlap(p.get("at", 0) + 0.1, p.get("at", 0) + 0.6, sfx.loud_parts(two))
ok(placed and abs(p["moved"]) <= sfx.SHIFT and p["dips"] and 0 < on <= sfx.MAX_ON_LOUD * 0.5
   and all(any(a <= max(x, p["at"]) + 1e-6 and min(y, p["at"] + 0.8) <= b + 1e-6 for a, b in p["dips"])
           for x, y in sfx.loud_parts(two) if x < p["at"] + 0.8 and p["at"] < y),
   f"no gap near: it touches the least it can ({on:.2f} s of its loud part) and dips under every loud moment it "
   f"meets ({p.get('dips')})")
talky = [{"word": f"w{k}", "start": 6.0 + 0.4 * k, "end": 6.0 + 0.4 * k + 0.39} for k in range(10)]
placed, left = sfx.place([cue("in", 7.0)], FX, talky, [], 30.0, np.ones(30000))  # his voice as loud all through
ok(not placed, f"in the middle of fast talk, where it would mostly sit on loud words: left out ({left})")
# The mix's own part: an effect at its level, and dipped over a loud word.
(P / "fx").mkdir(exist_ok=True)
(P / "fx" / "w.wav").write_bytes(sound("whoosh", 0.0))
vraw = P / "fx" / "voice.f32"
np.zeros(48000 * 3 * 2, dtype="<f4").tofile(vraw)
one = {"file": P / "fx" / "w.wav", "at": 1.0, "seconds": 0.8, "duck_db": 0.0}
rep0 = editor.effects_stem([one], vraw, [], 3.0, 24.0, P / "fx" / "a.f32")
s0 = editor._read_raw(P / "fx" / "a.f32")
rep1 = editor.effects_stem([{**one, "dips": [[1.3, 1.5]]}], vraw, [], 3.0, 24.0, P / "fx" / "b.f32")
s1 = editor._read_raw(P / "fx" / "b.f32")
drop = peak(s0, 1.34, 1.46) - peak(s1, 1.34, 1.46)
ok(abs(rep0["effect_peak_db"] - (editor.VOICE_PEAK_DB - 24.0)) < 0.6 and rep0["lowered_db"] == 0
   and peak(s0, 0.0, 0.99) < -100 and peak(s0, 1.0, 1.8) > -60,
   f"the mix lays it from its moment at its level ({rep0['effect_peak_db']} dB, {rep0['below_db']} under the voice)")
ok(abs(drop - sfx.DIP_DB) < 1.5 and abs(peak(s0, 1.6, 1.75) - peak(s1, 1.6, 1.75)) < 0.5,
   f"and dips it {drop:.1f} dB over the loud word only (asked {sfx.DIP_DB:.0f})")
placed, _ = sfx.place([cue("count", 4.0, [0.0, 0.14, 0.28])], FX, [], [], 30.0)
g5 = {"style": "card", "title": "Five", "lines": ["A 1 day", "B 2 days", "C 3 days", "D 4 days", "E 5 days"], "start": 4.0,
      "duration": 3.0}
c5 = sfx.cues([g5], None, "site")
ok(placed[0]["ticks"] == [0.0, 0.14, 0.28] and c5[0]["role"] == "count" and len(c5[0]["ticks"]) == sfx.COUNT_MAX
   and c5[0]["at"] == 4.3, f"a numbers panel: a tick as each bar grows (from +0.3 s, 0.14 s apart), at most "
                           f"{sfx.COUNT_MAX} ({c5[0]['ticks']})")
cg = sfx.cues([{**g5, "lines": ["Screed first", "Then tiles"]}, {"style": "callout", "title": "C", "lines": [], "start": 9.0},
               {"style": "picture", "title": "P", "start": 14.0}, {"style": "scene", "title": "S", "start": 20.0}],
              {"start": 26.0, "duration": 1.8}, "site")
ok([c["role"] for c in cg] == ["in", "in", "picture", "picture", "end"] and cg[-1]["at"] == 26.12,
   "a card, a callout: a whoosh; a picture, a drawing: an air whoosh; the end card: a chime as the mark rises")
ok(sfx.cues([g5], None, "gallery")[0]["role"] == "in", "the gallery look (no bars): its card gets the whoosh")

# ── 3. A real draft ───────────────────────────────────────────────────────────
print("\n== 3. A real draft with sound effects (Openverse's, no music so they can be heard on their own)")
reset_net()
pid, fake = production()
anthropic.Anthropic = lambda **kw: fake
t0 = time.time()
final = editor.edit(conn, pid, client=fake, find_track=no_music)
print(f"  edit took {time.time() - t0:.1f}s")
r = row(pid)
edl, qa = json.loads(r["edl"]), json.loads(r["qa"])
fx = edl.get("sfx") or {}
placed = fx.get("placed") or []
print("  effects:", [(p["role"], p["at"], p["ticks"], p["duck_db"], p["effect"]) for p in placed])
print("  sound:", qa["sound"].get("sfx"))
roles = [p["role"] for p in placed]
close = edl["close"]
ok(fx.get("on") and "end" in roles and "count" in roles and "in" in roles,
   f"the draft has its effects: {roles}")
end_fx = next(p for p in placed if p["role"] == "end")
ok(abs(end_fx["at"] - (close["at"] + 0.12)) < 0.01, f"one chime on the end card, as the mark rises ({end_fx['at']:.2f}s, "
                                                   f"the card at {close['at']:.2f}s)")
cards = {g["title"]: g for g in edl["graphics"]}
cnt = next(p for p in placed if p["role"] == "count")
ok(abs(cnt["at"] - (cards["Drying times"]["start"] + 0.3)) <= sfx.SHIFT + 0.15 and 1 <= len(cnt["ticks"]) <= 2,
   f"ticks on the numbers panel's bars ({cnt['at']:.2f}s, {len(cnt['ticks'])} ticks for 2 bars)")
inn = next(p for p in placed if p["role"] == "in")
ok(abs(inn["at"] - cards["Porcelain"]["start"]) <= sfx.SHIFT + 0.001, f"a whoosh as the callout slides in ({inn['at']:.2f}s)")
ok(all(p["source"] == "openverse" and p["licence"] in ("cc0", "pdm") and p["id"] and p["effect"] for p in placed),
   "each one's source, id, title and licence kept beside the draft")
words = editor.timeline(edl["cuts"], CL, sound=True)
voice_env = sfx.voice_level(final)
loud = sfx.loud_parts(words, voice_env)
cut_at = sfx.joins(edl["cuts"])
spans = sorted((p["at"], p["at"] + p["ticks"][-1]) for p in placed)
ok(all(p["at"] >= sfx.HOOK_CLEAR for p in placed) and all(b[0] - a[1] >= sfx.GAP - 0.001 for a, b in zip(spans, spans[1:]))
   and len(placed) <= max(1, int(r["duration"] // sfx.EVERY)),
   f"sparse: none in the first second, {sfx.GAP} s apart at least, {len(placed)} in {r['duration']:.0f} s")
FXI = {p["role"]: sfx.effect_for(p["role"], "urban_makers") for p in placed}
ok(all(abs(p["at"] + off - j) >= sfx.CUT_CLEAR - 0.001 for p in placed for off in p["ticks"] for j in cut_at),
   f"none within {sfx.CUT_CLEAR} s of a cut in his speech (cuts at {cut_at})")
hits = [(p, x, y) for p in placed for off in p["ticks"] for x, y in loud
        if p["at"] + off < y and x < p["at"] + off + FXI[p["role"]]["seconds"]]
ok(all(sfx._overlap(p["at"] + off + FXI[p["role"]]["body"][0], p["at"] + off + FXI[p["role"]]["body"][1], loud)
       <= sfx.MAX_ON_LOUD * (FXI[p["role"]]["body"][1] - FXI[p["role"]]["body"][0]) + 0.02
       for p in placed for off in p["ticks"])
   and all(any(a - 0.05 <= max(x, p["at"]) and min(y, p["at"] + 2) <= b + 0.05 for a, b in p["dips"]) for p, x, y in hits),
   f"never riding on the loudest part of a spoken word (measured in the draft's own sound): {len(hits)} touch one and "
   "dip under it")
ok(any("soft sound effect" in c["did"] and "Openverse" in c["did"] and "CC0" in c["did"] for c in qa["checks"]),
   f"the draft's checks say what was added and from where: {next((c['did'] for c in qa['checks'] if 'sound effect' in c['did']), '')[:160]}")
read_call = next(c for c in fake.calls if "Place names stay internal" in c.get("system", ""))
read_text = " ".join(c.get("text", "") for c in read_call["messages"][0]["content"] if c.get("type") == "text")
ok("Sound effects (Studio adds a few on purpose" in read_text and "E1 " in read_text and "- Sound effects:" in read_call["system"]
   and "never jarring" in read_call["system"] and "sound_effects: only when the material lists" in read_call["system"],
   "Studio's read is told they're deliberate and how to flag one (too loud, jarring)")
# The level, measured in the finished file: the chime's loudest moment against the loud parts of his voice.
x = sound_of(final)
voice_db = editor.voice_peak_db(x, words)
chime_db = peak(x, end_fx["at"], end_fx["at"] + 1.2)
ok(abs((voice_db - chime_db) - settings.SFX_LEVEL_DB) <= 2.0,
   f"in the finished draft the chime sits {voice_db - chime_db:.1f} dB under his voice's loud parts "
   f"(asked: {settings.SFX_LEVEL_DB:.0f})")
s = qa["sound"]["sfx"]
ok(s["below_db"] >= settings.SFX_LEVEL_DB - 1.0 and s["count"] == sum(len(p["ticks"]) for p in placed),
   f"and the mix measured every effect at least that far under: {s}")

# A re-render: the same effects, nothing fetched.
reset_net()
editor.rerender(conn, pid, find_track=no_music)
edl2 = json.loads(row(pid)["edl"])
ok([(p["role"], p["at"]) for p in edl2["sfx"]["placed"]] == [(p["role"], p["at"]) for p in placed]
   and not NET["searches"] and not NET["files"] and not NET["mcp"],
   "a re-render: the same effects at the same moments, no search and no download (one fetch per effect)")

# ── 4. Bryan's note: no sound effects ─────────────────────────────────────────
print("\n== 4. Notes")
pref, did = sfx.pref_from_notes([{"body": "The whoosh is a bit too loud"}, {"body": "no chime at the end please"}], None)
ok(pref["level"] == -6.0 and pref["roles_off"] == ["end"] and len(did) == 2,
   f"\"the whoosh is too loud\" turns them down, \"no chime\" takes the chime off: {did}")
pref2, did2 = sfx.pref_from_notes([{"body": "bring back the sound effects"}], {**pref, "off": True})
ok(not pref2["off"] and pref2["roles_off"] == [] and pref2["level"] == -6.0, "\"bring back the sound effects\": back on")
ok(all(sfx.note_ask(t) is None for t in ["Cut the pause here, the whoosh is nice", "Turn the music down",
                                          "This ticket price is wrong", "adding a card here"]),
   "notes about other things are not about the effects")
note(pid, "No sound effects please", 3.0)
editor.edit(conn, pid, client=fake, find_track=no_music)
r5 = row(pid)
edl5, qa5 = json.loads(r5["edl"]), json.loads(r5["qa"])
ok(edl5["sfx"] == {"on": False, "why": "Bryan asked for none on this post"} and "sfx" not in qa5["sound"]
   and any("took the sound effects off, as your note asked" == c["did"] for c in qa5["checks"]),
   "\"No sound effects please\": the next draft has none, and says so")
x5 = sound_of(Path(r5["video_path"]))
quiet5 = peak(x5, end_fx["at"], end_fx["at"] + 1.2)
ok(quiet5 < chime_db - 10, f"no chime in its sound (the end card {quiet5:.1f} dB, with the chime it was {chime_db:.1f})")
note(pid, "Tighten the start a little", 1.0)
editor.edit(conn, pid, client=fake, find_track=no_music)
ok(json.loads(row(pid)["edl"])["sfx"]["on"] is False, "and they stay off for that post in the drafts after")

# ── 5. STUDIO_SFX=0: exactly the draft as before ──────────────────────────────
print("\n== 5. Off: the plan and the mix exactly as before sound effects")
old = load_before("editor_832a076.py", "editor_before_sfx")
RUNS, PAIRS = [], []
real_mix, real_run = editor.mix_final, extract._run


def recording(args, *a, **kw):
    if RUNS:
        RUNS[-1].append([re.sub(r"studio-mix-[A-Za-z0-9_]+", "MIX", str(x)) for x in args])
    return real_run(args, *a, **kw)


def mix_both(*a, **kw):
    RUNS.append([])
    rep = real_mix(*a, **kw)
    new_cmds = RUNS.pop()
    if COMPARE:
        out = Path(a[5])
        before = out.with_name(out.stem + "_before.mp4")
        RUNS.append([])
        rep_old = old.mix_final(*a[:5], before, *a[6:], **kw)
        PAIRS.append({"kw": set(kw), "new": new_cmds, "old": [[c.replace(before.name, out.name) for c in x] for x in RUNS.pop()],
                      "out": out, "before": before, "rep": rep, "rep_old": rep_old})
    return rep


extract._run, editor.mix_final, COMPARE = recording, mix_both, True
settings.SFX = False
pidB, fakeB = production()
anthropic.Anthropic = lambda **kw: fakeB
reset_net()
editor.edit(conn, pidB, client=fakeB, find_track=with_music)
COMPARE = False
eB, qB = json.loads(row(pidB)["edl"]), json.loads(row(pidB)["qa"])
pair = PAIRS[0]


def md5(path, stream):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{stream}", "-f", "md5", "-"],
                          capture_output=True, text=True, check=True).stdout.strip()


ok("sfx" not in eB and "sfx_pref" not in eB and "sfx" not in qB["sound"] and not NET["searches"] and not NET["mcp"]
   and not (pair["kw"] & {"effects", "effects_below"}),
   "STUDIO_SFX=0: no effects in the plan, the mix or the checks, nothing searched")
ok(pair["new"] == pair["old"] and len(pair["new"]) >= 5,
   f"the mix runs exactly the commands it ran before sound effects ({len(pair['new'])} ffmpeg runs, editor at 832a076)")
ok(md5(pair["out"], "a") == md5(pair["before"], "a") and md5(pair["out"], "v") == md5(pair["before"], "v")
   and pair["rep"] == pair["rep_old"], "and makes the same draft, sound and picture, sample for sample")
settings.SFX = True
editor.mix_final, extract._run = real_mix, real_run

# The same plan with them on: only the effects added (the cut, the graphics, the music the same).
pidC, fakeC = production()
anthropic.Anthropic = lambda **kw: fakeC
editor.edit(conn, pidC, client=fakeC, find_track=with_music)
eC, qC = json.loads(row(pidC)["edl"]), json.loads(row(pidC)["qa"])
same = lambda e: {k: v for k, v in e.items() if k not in ("sfx",)}  # noqa: E731
ok(same(eC) == same(eB) and eC["sfx"]["placed"],
   "with them on, the plan is the same but for the effects (cut, graphics, close, captions, music)")
ok(qC["sound"]["sfx"]["below_db"] >= settings.SFX_LEVEL_DB - 1.0
   and abs(qC["sound"]["gap_db"] - qB["sound"]["gap_db"]) < 0.05,
   f"with music: the effects well under his voice ({qC['sound']['sfx']['below_db']:.1f} dB), the music where it was")

# ── 6. The read's fix: one off, all quieter ───────────────────────────────────
print("\n== 6. Studio's read flags one: a patch")
eC_placed = eC["sfx"]["placed"]
k = next(p["n"] for p in eC_placed if p["role"] == "in")
a1 = {"type": "sound_effects", "item": k, "field": "remove", "amount": None}
a2 = {"type": "sound_effects", "item": None, "field": "", "amount": -6}
clean = draft_review._clean_action({"editor_can_fix": True, "action": {**a1, "start": None, "end": None, "from": "", "to": "",
                                                                       "reason": ""}}, draft_review.REEL_ACTIONS, 30.0)
ok(clean["type"] == "sound_effects" and "sound_effects" in fix_actions.PATCHES, "the read can ask for it as a patch")
made, res = editor.self_patch(conn, pidC, [{"n": 1, "action": a1}, {"n": 2, "action": a2}], 1, client=fakeC,
                              find_track=with_music)
eD, qD = json.loads(row(pidC)["edl"]), json.loads(row(pidC)["qa"])
print("  patched:", [r_.get("did") or r_.get("why") for r_ in res])
ok(made and all(r_["ok"] for r_ in res) and "in" not in [p["role"] for p in eD["sfx"]["placed"]]
   and "end" in [p["role"] for p in eD["sfx"]["placed"]],
   f"\"take off E{k}\": the callout's whoosh gone, the rest kept ({[r_['did'] for r_ in res]})")
ok(eD["sfx"]["level_db"] == settings.SFX_LEVEL_DB + 6 and qD["sound"]["sfx"]["below_db"] >= settings.SFX_LEVEL_DB + 5,
   f"\"all 6 dB quieter\": {qD['sound']['sfx']['below_db']:.1f} dB under his voice now")
_, res = fix_actions.apply({**eD, "sfx_pref": {**eD["sfx_pref"], "level": 6.0}}, CL, [
    {"n": 1, "action": {"type": "sound_effects", "item": 9, "field": "remove", "amount": None}},
    {"n": 2, "action": {"type": "sound_effects", "item": None, "field": "", "amount": 4}}])
ok(not res[0]["ok"] and "no sound effect E9" in res[0]["why"] and not res[1]["ok"],
   f"one that isn't there, or louder past the limit: not made ({res[1]['why']})")
ok(sfx.level(30) == 6.0 and sfx.level(-40) == -18.0 and (settings.SFX_LEVEL_DB - sfx.level(30)) >= 12,
   "the limits: at most 6 dB louder than usual (never closer than 12 dB to his voice), 18 dB quieter")
ok(self_fix.same_fix({"action": a1}, {"action": a1}) and not self_fix.same_fix({"action": a1}, {"action": {**a1, "item": k + 1}}),
   "two reads naming the same effect are the same fix")

# ── 7. Settings, docs and what never gets them ────────────────────────────────
print("\n== 7. Settings and docs")
studio = Path(editor.__file__).parent
readme, envx = (studio / "README.md").read_text(), (studio / ".env.example").read_text()
import updater, help_desk, slideshow, inspect
ok(settings.SFX and settings.SFX_LEVEL_DB == 24.0 and settings.SFX_MAX == 5, "on by default, 24 dB under the voice, at most 5")
ok("### Sound effects" in readme and "STUDIO_SFX_LEVEL_DB" in readme and "CC0" in readme and "Openverse" in readme
   and all(f"{k}=" in envx for k in ("STUDIO_SFX", "STUDIO_SFX_LEVEL_DB", "STUDIO_SFX_MAX")),
   "the README and .env.example explain them")
ok("sfx" in updater._MODULES.split(", ") and "sound" in help_desk.PAGE_WORDS["draft"],
   "the updater's import check and Ask Studio know them")
ok("sfx" not in inspect.getsource(slideshow) and "effects=" not in inspect.getsource(slideshow),
   "slideshow Reels get none (voice and music only)")
ok(not re.search(r"aevalsrc|anoisesrc|sine=|generat", inspect.getsource(sfx).split('"""', 2)[2]),
   "Studio never makes its own effects (no generator in sfx.py)")

server.shutdown()
finish()
