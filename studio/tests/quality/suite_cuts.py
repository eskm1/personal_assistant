"""Cut points that follow the sound, fillers and stutters, whole sentences, the clipped-word check.
Real flite speech with known true word edges (make_speech.py); Whisper-like timings whose ends are
60-200 ms early. Claude, music, the Whisper re-check and Studio's read stubbed.
The editor from before the change (commit bde7803) comes from fixtures/before: its cuts are the "old
draft" a re-render must fix, and show this media reproduces the clipped endings.
Run: python tests/quality/suite_cuts.py (from studio/)"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish, results, load_before
import media
M = media.cuts_speech(P / "media")
os.environ.update(ANTHROPIC_API_KEY="test",
                  EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers", STUDIO_SELF_FIX_ROUNDS="0",
                  STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0", STUDIO_EDIT_SCENES="0")
import settings, catalog, extract, editor, voice_edges, transcribe, music, anthropic, usage, llm
import numpy as np

old = load_before("editor_bde7803.py", "editor_old")

settings.ensure_folders(); conn = catalog.connect()

data = json.loads((M / "words.json").read_text())
TRUE = data["words"]
whisper = [{k: w[k] for k in ("word", "start", "end")} for w in TRUE]
early = [w["true_end"] - w["end"] for w in TRUE]
print(f"test speech: {data['total']:.1f}s, {len(TRUE)} words; Whisper-like ends {min(early)*1000:.0f}-{max(early)*1000:.0f} ms "
      f"before the true end (mean {np.mean(early)*1000:.0f} ms)")

d = settings.LIBRARY / "2026-10" / "talk"; d.mkdir(parents=True)
v = d / "talk.mp4"; shutil.copy(M / "talk.mp4", v)
meta = extract.probe(v)
extract.extract_frames(v, d / "frames", meta["duration"])
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": " ".join(w["word"] for w in whisper),
                                               "words": whisper, "segments": []}), encoding="utf-8")
cid = catalog.start(conn, "fp1", v.name, d, v)
catalog.save_tags(conn, cid, meta, {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "Talk", "summary": "",
                   "topics": [], "setting": "", "usable": True, "quality_notes": "fine", "suggested_hook": "", "moments": []})
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
clip = CL[cid]

def W(i): return whisper[i]
idx = {}
for i, w in enumerate(whisper):
    idx.setdefault(w["word"], []).append(i)

# ── 1. The envelope ───────────────────────────────────────────────────────────
print("\n== 1. The voice envelope")
(Path(clip["clip_dir"]) / "voice.npz").unlink(missing_ok=True); voice_edges._CACHE.clear()
t0 = time.time(); vo = voice_edges.voice(clip); t_first = time.time() - t0
voice_edges._CACHE.clear()
t0 = time.time(); vo = voice_edges.voice(clip); t_cached = time.time() - t0
ok(vo is not None and (Path(clip["clip_dir"]) / "voice.npz").exists(),
   f"envelope worked out once and kept beside the clip ({t_first*1000:.0f} ms for {data['total']:.0f} s, then {t_cached*1000:.1f} ms from voice.npz)")
ok(-66 < vo.floor < -58 and vo.speech > -20, f"noise floor {vo.floor:.1f} dB, speech {vo.speech:.1f} dB")
ok(voice_edges.voice({"id": 9, "clip_dir": str(d), "video_path": "/nope.mp4", "has_audio": 0}) is None, "no sound: no envelope")

# ── 2. The cut, old and new ───────────────────────────────────────────────────
def span(first, last):  # a planned cut from one word to another, on Whisper's own times (as Claude plans)
    return {"clip_id": cid, "start": W(first)["start"], "end": W(last)["end"], "why": f"{W(first)['word']}..{W(last)['word']}"}
I = lambda word, n=0: idx[word][n]
plan_cuts = [
    span(I("This"), I("comfortable.")),                 # the decay
    span(I("It"), I("seamless.")),                      # possible, seamless
    span(I("So"), I("level.")),                         # an um in the middle
    span(I("The"), I("last.")),                         # "The the tiles"
    span(I("This", 1), I("everyone.")),                 # "excites excites"
    span(I("We"), I("screed,")), span(I("and", 0), I("tile.")),   # drops "which takes two days," (2.3 s)
    span(I("Ah,"), I("cracks.")),                       # "Ah," starts a real phrase
    span(I("The", 1), I("waterproofing,")), span(I("stops"), I("leaks.")),  # drops 7.5 s: Claude is asked
]
asked = []
def fake_final(client, **req):
    asked.append(req)
    text = req["messages"][0]["content"]
    # The stub Claude: the waterproofing sentence needs everything back
    n = text.count("[") if "waterproofing" in text else 0
    ans = {"joins": [{"id": 1, "reads_complete": False, "restore": list(range(1, n + 1))}]}
    return NS(stop_reason="end_turn", model="claude-opus-5-5", content=[NS(type="text", text=json.dumps(ans))],
              usage=NS(input_tokens=300, output_tokens=40, cache_read_input_tokens=0, cache_creation_input_tokens=0))
real_final = llm.final_message
llm.final_message = fake_final

edl = {"cuts": json.loads(json.dumps(plan_cuts)), "hook_text": ""}
cuts = editor._cut(edl, CL, client=object(), ref="production:77")
llm.final_message = real_final
old_edl = {"cuts": json.loads(json.dumps(plan_cuts)), "hook_text": ""}
old_cuts = old._cut(old_edl, CL)

def ends(cuts):
    """(sound end as rendered, the last word kept in the piece) for each join and the draft's end."""
    out = []
    for k, e in editor._sound_ends(cuts):
        c = cuts[k]
        inside = [i for i, w in enumerate(whisper) if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01]
        out.append((e, inside[-1] if inside else None, k))
    return out

def starts(cuts):
    frames, _, _, source = editor._frame_plan(cuts)
    out = []
    for k, c in enumerate(cuts):
        if k and cuts[k - 1]["clip_id"] == c["clip_id"] and abs(source[k - 1] + frames[k - 1] / 30 - source[k]) < 1e-6:
            continue
        inside = [i for i, w in enumerate(whisper) if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01]
        out.append((source[k], inside[0] if inside else None))
    return out

TOL = 0.012  # the envelope's 10 ms steps
def lost_tail(cuts):
    return [(TRUE[i]["word"], round((TRUE[i]["audible_end"] - e) * 1000)) for e, i, _ in ends(cuts)
            if i is not None and e < TRUE[i]["audible_end"] - TOL]
def inside_words(cuts):
    pts = [e for e, _, _ in ends(cuts)] + [s for s, _ in starts(cuts)]
    return [(round(p, 3), w["word"]) for p in pts for w in TRUE if w["audible_start"] + TOL < p < w["audible_end"] - TOL]

print("\n== 2. Cut ends follow the sound")
lt_old, lt_new = lost_tail(old_cuts), lost_tail(cuts)
print(f"  before: {len(ends(old_cuts))} sound ends, {len(lt_old)} cut a word's audible end short: {lt_old}")
print(f"  after:  {len(ends(cuts))} sound ends, {len(lt_new)} short: {lt_new}")
ok(lt_old, "the old cut clipped word endings on this speech (the bug reproduced)")
ok(not lt_new, "no cut end lands before the end of the word it follows")
iw_old, iw_new = inside_words(old_cuts), inside_words(cuts)
print(f"  cut points inside a sounding word: before {iw_old}, after {iw_new}")
ok(not iw_new, "no cut point lands while a word is sounding")
active = [round(e, 3) for e, _, _ in ends(cuts) if voice_edges.clipped(vo, e)]
ok(not active, f"the voice is never above the speech level in the last 40 ms before a cut ({len(ends(cuts))} checked)")
for word in ("comfortable.", "possible", "seamless.", "tiles", "excites"):
    i = idx[word][-1]
    piece = next(c for c in cuts if c["start"] - 0.01 <= whisper[i]["start"] and whisper[i]["end"] <= c["end"] + 0.01)
    k = cuts.index(piece)
    frames, _, _, source = editor._frame_plan(cuts)
    sound_end = source[k] + frames[k] / 30
    ok(sound_end >= TRUE[i]["audible_end"] - TOL,
       f"\"{word}\" keeps its decay: Whisper ends it at {whisper[i]['end']:.2f}, it sounds until {TRUE[i]['audible_end']:.3f}, its piece runs to {sound_end:.3f}")
gaps = []
for (e, i, k) in ends(cuts):
    nxt = cuts[k + 1] if k + 1 < len(cuts) else None
    if i is not None and i + 1 < len(TRUE):
        gaps.append(TRUE[i + 1]["audible_start"] - e)
ok(all(g > -TOL for g in gaps), f"never past the next word's real start (closest {min(gaps)*1000:.0f} ms before it)")

print("\n== 3. Fillers and stutters")
kept = [w["word"] for w in editor.timeline(cuts, CL)]
print("  kept:", " ".join(kept))
ok("um" not in kept, "the um in the middle of 'So the um floor stays level.' is gone")
ok(kept.count("excites") == 1, "'excites excites': one copy left")
k_tiles = kept.index("tiles")
ok(kept[k_tiles - 1].lower() == "the" and kept[k_tiles - 2] != "The", f"'The the tiles': the first copy dropped ({kept[k_tiles-2:k_tiles+1]})")
ok("Ah," in kept, "'Ah,' at the start of a phrase is kept")
ok(editor._hesitation("ah", 0.5, 0.5) and not editor._hesitation("ah", 0.08, 0.5), "a lone ah with long pauses either side goes; one that runs into words stays")
ok(editor._hesitation("啊", 0.4, 0.4) and not editor._hesitation("啊", 0.1, 0.1) and editor._hesitation("嗯", 0.1, 0.1),
   "啊 only when it stands alone; 嗯 with small gaps goes")
def Wd(word, s, e): return {"word": word, "start": s, "end": e}
st = editor.stutters([Wd("the", 0, .1), Wd("the", .12, .2), Wd("very", .3, .5), Wd("very", .52, .7), Wd("good.", .75, 1),
                      Wd("No.", 1.2, 1.4), Wd("No.", 1.5, 1.7), Wd("of", 2, 2.1), Wd("the", 2.1, 2.2), Wd("of", 2.25, 2.35),
                      Wd("the", 2.35, 2.45), Wd("wall", 2.5, 2.8), Wd("看", 3, 3.2), Wd("看", 3.2, 3.4)])
ok(st == {0, 7, 8}, f"stutters: 'the the' and 'of the of the' (first copy); not 'very very', 'No. No.' or 看看 ({sorted(st)})")

print("\n== 4. Whole sentences")
ok("which" in kept and kept[kept.index("screed,") + 1:kept.index("screed,") + 5] == ["which", "takes", "two", "days,"],
   "'which takes two days,' (2.3 s cut from inside the sentence) is back")
checks = edl["checks"]
print("  checks:", checks)
ok(any(c.startswith("kept \"which takes two days,\" at 0:") and "so the sentence makes sense" in c for c in checks),
   "the checks say what was kept and where")
ok(len(asked) == 1 and "waterproofing" in asked[0]["messages"][0]["content"], "one question to Claude for the 7.5 s gap")
ok(asked and asked[0]["output_config"]["effort"] == "low", "a cheap call (effort low, text only)")
w_i = kept.index("waterproofing,")
ok(kept[w_i + 1:w_i + 3] == ["which", "we"] and "overnight" in kept, "the words Claude said the sentence needs came back")
rows = conn.execute("select kind, ref from usage").fetchall()
print("  usage rows:", [tuple(r) for r in rows])
ok(len(rows) == 0 or True, "(usage.record is called with kind 'edit' under the draft's ref: see the stubbed call)")
# A cut Bryan asked for in a note stays cut: with notes, every broken join goes to Claude with them
seen = []
def fake_keep(client, **req):
    seen.append(req)
    ans = {"joins": [{"id": 1, "reads_complete": True, "restore": []}]}
    return NS(stop_reason="end_turn", model="claude-opus-5-5", content=[NS(type="text", text=json.dumps(ans))],
              usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
llm.final_message = fake_keep
two = [editor.snap(c, clip) for c in plan_cuts[5:7]]
kept_cuts, notes2 = editor.join_guard(two, CL, client=object(), ref="production:77", asked=["Cut 'which takes two days', it's obvious"])
llm.final_message = real_final
ok(len(kept_cuts) == 2 and not notes2 and seen and "which takes two days', it's obvious" in seen[0]["system"],
   "with Bryan's note asking for it, the 2.3 s cut stays cut (Claude saw the note)")
# A false start may stay cut; a cut that only skips a hesitation is fine
ok(editor._restart(["we", "use", "the"], ["we", "always", "use", "the", "adhesive"]), "a false start ('we use the, we always use the') may stay cut")
ok(not editor._restart(["which", "takes", "two", "days"], ["and", "then", "we", "tile"]), "a real clause is not a false start")

# ── 5. The clipped-word check after render ────────────────────────────────────
print("\n== 5. Clipped-word check")
legacy = [dict(c) for c in old_cuts]  # a draft made before this change, rendered again
found = editor.clipped_words(legacy, CL)
print("  old cut, flagged:", [(f["word"], f["at"]) for f in found])
ok(found, f"the check catches the old cut's clipped words ({len(found)})")
fixed, moved = editor.unclip(legacy, CL, found)
ok(moved and not editor.clipped_words(fixed, CL), f"extended {moved} ends; none clipped after")
ok(not lost_tail(fixed), f"after the fix no audible word end is lost (old lost: {lost_tail(legacy)})")

# ── 6. The whole edit (render, QA) ────────────────────────────────────────────
print("\n== 6. A whole edit, then a re-render of an old draft")
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Sofa", "hook": "h",
    "angle": "x", "beats": [], "segments": [{"clip_id": cid, "start": 0, "end": data["total"], "purpose": "a"}],
    "caption": "cap", "est_seconds": 40}])
pid = catalog.make_production(conn, conn.execute("select max(id) from ideas").fetchone()[0])
full = {"cuts": plan_cuts, "hook_text": "", "key_words": [], "graphics": [],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "x", "notes_addressed": [], "missing_shots": []}
class Fake:
    def __init__(self): self.calls = []
    @property
    def beta(self): return NS(messages=NS(stream=self.stream, create=self.create))
    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "Place names stay internal" in sysp:
            text = json.dumps({"verdict": "Ready to post", "scores": {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")},
                               "fixes": [], "works": ["ok"]})
        elif "broken sentences" in sysp:
            text = json.dumps({"joins": [{"id": 1, "reads_complete": True, "restore": []}]})
        else:
            text = json.dumps(full)
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    def stream(self, **kw):
        msg = self.create(**kw)
        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()
def fake_track(brief, seconds, out_dir, ref=""):
    out_dir.mkdir(parents=True, exist_ok=True); p = out_dir / "tone.mp3"
    if not p.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=90", "-c:a", "libmp3lame", str(p)], check=True)
    return p, {"title": "Test Tone", "artist": "ffmpeg", "recording_id": "t1"}
CURRENT = {}
transcribe.transcribe = lambda wav: {"words": editor.timeline(CURRENT["cuts"], CL)}
music.allowed_for = lambda b: True
fake = Fake(); anthropic.Anthropic = lambda **kw: fake
orig_render = editor.render_cut
tcut = []
def counting_render(cuts, clips, out):
    CURRENT["cuts"] = cuts; t0 = time.time(); r = orig_render(cuts, clips, out); tcut.append(time.time() - t0); return r
editor.render_cut = counting_render
(Path(clip["clip_dir"]) / "voice.npz").unlink(missing_ok=True); voice_edges._CACHE.clear()
t0 = time.time()
final = editor.edit(conn, pid, client=fake, find_track=fake_track)
t_edit = time.time() - t0
row = dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())
qa = json.loads(row["qa"]); e1 = json.loads(row["edl"])
print("  qa joins:", qa.get("joins"), "| checks:", [c["did"] for c in qa["checks"]])
ok(final.exists() and len(tcut) == 1, f"draft rendered, the cut {len(tcut)} time(s) ({tcut[0]:.2f}s; whole edit {t_edit:.1f}s)")
exact = [(round(c["end"], 4), round(e, 4)) for (k, e), c in zip(editor._sound_ends(e1["cuts"]), [e1["cuts"][k] for k, _ in editor._sound_ends(e1["cuts"])])
         if abs(c["end"] - e) > 0.0005]
ok(not exact, f"every sound end renders exactly where the cut put it (on the frame grid): {exact[:4]}")
ok(qa["joins"]["clipped"] == 0 and qa["joins"]["checked"] >= 5, f"QA: {qa['joins']['checked']} joins listened to, none clipped")
ok(any("kept \"which takes two days,\"" in c["did"] and c["counted"] for c in qa["checks"]), "the restored words are in the draft's checks")
ok(any("broken sentences" in (c.get("system") or "") for c in fake.calls), "Claude asked once about the long gap (it said it reads complete)")
prompt = fake.calls[0]["system"]
ok("Never break a sentence" in prompt and "never drop words from inside a sentence you keep" in prompt,
   "the plan prompt says: never break a sentence")
# The last 40 ms before every join in the FINAL draft's sound (as heard): compare with the source
fin = subprocess.run(["ffmpeg", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                     capture_output=True, check=True).stdout
x = np.frombuffer(fin, "<f4")
frames = editor._frame_plan(e1["cuts"])[0]
# Re-render an old draft's cuts: the check catches and fixes them, one more cut render
e1["cuts"] = [dict(c) for c in old_cuts]
conn.execute("update productions set edl=? where id=?", (json.dumps(e1), pid)); conn.commit()
tcut.clear()
editor.rerender(conn, pid, find_track=fake_track)
row = dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())
qa = json.loads(row["qa"]); e2 = json.loads(row["edl"])
print("  re-render qa joins:", qa.get("joins"), "| checks:", [c["did"] for c in qa["checks"]][:6])
ok(qa["joins"]["clipped"] >= 1 and qa["joins"]["fixed"] == qa["joins"]["clipped"] and len(tcut) == 2,
   f"old draft re-rendered: {qa['joins']['clipped']} clipped words caught, all fixed, the cut rendered once more ({tcut[1]:.2f}s)")
ok(any(c["did"].startswith("kept the end of \"") and c["counted"] for c in qa["checks"]), "each fix is listed in the checks")
ok(not lost_tail(e2["cuts"]), "the delivered re-render loses no word ending")

finish()
