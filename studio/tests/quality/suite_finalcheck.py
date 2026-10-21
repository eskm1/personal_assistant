"""Studio's listen-back and watch-through (final_check.py): each flag fires on a crafted bad render and
stays quiet on a clean one, the fixes come in the read's action shape and Studio's own rounds make them,
a failure never breaks a draft, and how long it takes on a ~45 s Reel.
Real flite speech with known word edges (the ending suite's), OpenCV's sample face placed high (under
the titles) or low (make_media.py), real ffmpeg, HyperFrames and OpenCV. Claude, music and Whisper are
stubbed; Whisper by a listener that hears a word only when most of its sound is in the draft.
Run: python tests/quality/suite_finalcheck.py (from studio/)"""
import importlib, json, os, shutil, subprocess, sys, threading, time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, STUDIO, ok, finish, results
import media
M = media.finalcheck_media(P / "media")
os.environ.update(ANTHROPIC_API_KEY="test",
                  EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers", STUDIO_SELF_FIX_ROUNDS="0",
                  STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0", STUDIO_EDIT_SCENES="0", STUDIO_SPELL_CHECK="0",
                  TELEGRAM_BOT_TOKEN="", STUDIO_SUPABASE_URL="", STUDIO_SUPABASE_SERVICE_KEY="", STUDIO_HUMANIZE="0")
os.environ["STUDIO_FINAL_CHECK"] = ""
import settings, catalog, extract, editor, voice_edges, transcribe, music, anthropic, draft_review, self_fix
import fix_actions, final_check, updater
import numpy as np

settings.ensure_folders(); conn = catalog.connect()

data = json.loads((M / "words.json").read_text())
TRUE = data["words"]
whisper = [{k: w[k] for k in ("word", "start", "end")} for w in TRUE]
long_true = json.loads((M / "long_words.json").read_text())["words"]

def add_clip(name, video, words, fmt="talking_head"):
    d = settings.LIBRARY / "2026-10" / name; d.mkdir(parents=True)
    v = d / f"{name}.mp4"; shutil.copy(video, v)
    meta = extract.probe(v)
    ws = [{k: w[k] for k in ("word", "start", "end")} for w in words if w["end"] <= meta["duration"] - 0.05]
    (d / "transcript.json").write_text(json.dumps({"language": "en", "text": " ".join(w["word"] for w in ws),
                                                   "words": ws, "segments": []}), encoding="utf-8")
    cid = catalog.start(conn, f"fp-{name}", v.name, d, v)
    catalog.save_tags(conn, cid, meta, {"language": "en", "text": ""},
                      {"brand": "urban_makers", "brand_reason": "", "format": fmt, "title": name, "summary": "",
                       "topics": [], "setting": "", "usable": True, "quality_notes": "fine", "suggested_hook": "",
                       "moments": []})
    return cid

HIGH = add_clip("high", M / "face_high.mp4", TRUE)
LOW = add_clip("low", M / "face_low.mp4", TRUE)
BROLL = add_clip("broll", M / "broll.mp4", [], fmt="broll")
LONG = add_clip("long", M / "long.mp4", long_true)
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
SPOKEN = {HIGH: TRUE, LOW: TRUE, LONG: long_true, BROLL: []}
idx = {}
for i, w in enumerate(whisper):
    idx.setdefault(w["word"], []).append(i)
I = lambda word, n=0: idx[word][n]
def span(first, last, c, why="x"):
    return {"clip_id": c, "start": whisper[first]["start"], "end": whisper[last]["end"], "why": why}

# ── Stubs: Claude, music, and a listener in Whisper's place ───────────────────
READ = {"verdict": "Ready to post", "scores": {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")},
        "fixes": [], "works": ["The pace is right"]}
class Fake:
    def __init__(self, plan, delay=0.0): self.calls, self.plan, self.delay = [], plan, delay
    @property
    def beta(self): return NS(messages=NS(stream=self.stream, create=self.create))
    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "Place names stay internal" in sysp:
            time.sleep(self.delay)
            text = json.dumps(READ)
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
    out_dir.mkdir(parents=True, exist_ok=True); p = out_dir / "tone.mp3"
    if not p.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=120",
                        "-c:a", "libmp3lame", str(p)], check=True)
    return p, {"title": "Test Tone", "artist": "ffmpeg", "recording_id": "t1"}
music.allowed_for = lambda b: True

CURRENT = {}
orig_render_cut = editor.render_cut
def spy_render_cut(cuts, clips, out):
    CURRENT["cuts"] = [dict(c) for c in cuts]; return orig_render_cut(cuts, clips, out)
editor.render_cut = spy_render_cut

def level_db(x, rate=16000, hop=0.01):
    step = int(rate * hop); n = len(x) // step
    return 20 * np.log10(np.sqrt(np.mean(np.square(x[:n * step].reshape(n, step), dtype=np.float64), axis=1)) + 1e-9)

def listener(wav, cuts=None):
    """Whisper in this sandbox: a word of the draft is heard when at least 75% of its true sound is in the
    piece that holds it and that stretch of the finished sound is loud (within 20 dB of its speech)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    env = level_db(np.frombuffer(raw, "<f4"))
    speech = np.percentile(env, 95) if len(env) else -100
    out = []
    for p in final_check.pieces(cuts or CURRENT["cuts"]):
        for w in SPOKEN[p["clip_id"]]:
            a, b = max(w["true_start"], p["src0"]), min(w["true_end"], p["src1"])
            if b <= a:
                continue
            frac = (b - a) / (w["true_end"] - w["true_start"])
            da, db = p["d0"] + a - p["src0"], p["d0"] + b - p["src0"]
            seg = env[int(da * 100):max(int(db * 100), int(da * 100) + 1)]
            loud = len(seg) and 10 * np.log10(np.mean(10 ** (seg / 10))) > speech - 20
            if frac >= 0.75 and loud:
                out.append({"word": w["word"], "start": round(p["d0"] + w["start"] - p["src0"], 2),
                            "end": round(p["d0"] + w["end"] - p["src0"], 2)})
    return {"language": "en", "words": out}
transcribe.transcribe = listener

def production(title="t", plan_cta="Save this for your reno."):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": title, "hook": "h",
        "angle": "x", "beats": [], "segments": [{"clip_id": c, "start": 0, "end": data["total"], "purpose": "a"} for c in (HIGH, LOW, BROLL, LONG)],
        "caption": "cap", "est_seconds": 30, "cta": plan_cta}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    pid = catalog.make_production(conn, idea)
    plan = json.loads(conn.execute("select plan from productions where id=?", (pid,)).fetchone()[0])
    plan["cta"] = plan_cta
    conn.execute("update productions set plan=? where id=?", (json.dumps(plan), pid)); conn.commit()
    return pid
def row(pid): return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())
MUSIC = {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16}

def render(pid, cuts, graphics=(), hook="", placed=True, bad=False):
    """A draft from hand-made cuts and graphics (on the draft's clock when placed), rendered with every
    step of _render; bad: with the guards a slip would get past (the clipped-word fix, clear_of_end)
    turned off, then Studio's read (rounds 0)."""
    prod = row(pid)
    plan = json.loads(prod["plan"])
    edl = {"cuts": cuts, "hook_text": hook, "key_words": [], "look": editor.look_now(), "graphics": list(graphics), "music": dict(MUSIC),
           "summary": "x", "notes_addressed": [], "missing_shots": []}
    saved = editor.clipped_words, editor.clear_of_end
    if bad:
        editor.clipped_words = lambda cuts, clips: []
        editor.clear_of_end = lambda graphics, limit: (list(graphics), [])
    try:
        t0 = time.time()
        editor._render(conn, prod, CL, edl, plan, prod["version"] + 1, fake_track, placed=placed, client=FAKE)
        print(f"  render took {time.time() - t0:.0f}s")
    finally:
        editor.clipped_words, editor.clear_of_end = saved
    return row(pid)

def read(pid, rounds=0):
    t0 = time.time()
    r = self_fix.deliver(conn, pid, FAKE, fake_track, rounds=rounds)
    print(f"  read and rounds took {time.time() - t0:.0f}s")
    return r, json.loads(r["review"] or "{}")

def checks_of(review): return [f for f in review.get("fixes", []) if f.get("check")]
def draft_t(cuts, cid, src):
    """A clip moment on the draft's clock, as rendered."""
    for p in final_check.pieces(cuts):
        if p["clip_id"] == cid and p["src0"] - 0.001 <= src <= p["src1"] + 0.001:
            return p["d0"] + src - p["src0"]
    return None

ACTION_KEYS = {"type", "start", "end", "from", "to", "item", "field", "amount", "reason"}
def shaped(f):
    """A fix in the read's own shape (draft_review.SCHEMA's fields, the action in its vocabulary)."""
    a = f.get("action") or {}
    need = {"what", "why", "start", "end", "area", "when", "kind", "editor_can_fix", "rule", "same_rule", "action", "for_bryan"}
    return (need <= set(f) and set(a) == ACTION_KEYS and a["type"] in draft_review.REEL_ACTIONS
            and a["field"] in draft_review.ACTION_FIELDS and f["area"] in draft_review.AREAS and f["when"] == "this_draft"
            and (f["editor_can_fix"] == (a["type"] != "none")) and len(f["what"].split()) <= 14
            and (not f["editor_can_fix"] or f["rule"]) and f.get("check") in final_check.RULES and f.get("subject") is not None)

FAKE = Fake({})

# ── 0. Wiring ────────────────────────────────────────────────────────────────
print("\n== 0. Wiring")
ok(settings.FINAL_CHECK is True, "STUDIO_FINAL_CHECK defaults to on")
ok("final_check" in [m.strip() for m in updater._MODULES.split(",")], "final_check is in updater._MODULES")
env_example = (STUDIO / ".env.example").read_text()
ok("STUDIO_FINAL_CHECK=" in env_example, ".env.example has STUDIO_FINAL_CHECK")

# ── 1. A clean draft: nothing to say ─────────────────────────────────────────
print("\n== 1. A clean draft (face low, whole sentences, titles with their time)")
pid_clean = production("Clean")
CLEAN_PLAN = {"cuts": [span(I("We"), I("room."), LOW), span(I("It"), I("porcelain."), LOW), span(I("So"), I("years."), LOW)],
              "hook_text": "Floors *stripped*", "key_words": [],
              "graphics": [{"clip_id": LOW, "at": whisper[I("It")]["start"] + 0.2, "duration": 2.6, "style": "callout",
                            "title": "Two days to *dry*", "lines": ["Before tiling"], "scene": "", "look": "cutaway",
                            "role": "explain", "why": ""},
                           {"clip_id": LOW, "at": whisper[I("For")]["start"], "duration": 3.0, "style": "card",
                            "title": "Porcelain in *kitchens*", "lines": ["Hard wearing"], "scene": "", "look": "cutaway",
                            "role": "explain", "why": ""}],
              "music": dict(MUSIC), "summary": "x", "notes_addressed": [], "missing_shots": []}
FAKE = Fake(CLEAN_PLAN); anthropic.Anthropic = lambda **kw: FAKE
t0 = time.time()
editor.edit(conn, pid_clean, client=FAKE, find_track=fake_track)
print(f"  edit, read (rounds off) took {time.time() - t0:.0f}s")
r = row(pid_clean); rev = json.loads(r["review"]); qa = json.loads(r["qa"])
print("  final_check:", rev.get("final_check"), "| flags:", [(f["check"], f["what"]) for f in checks_of(rev)])
ok(isinstance(qa.get("heard"), list) and len(qa["heard"]) > 20, f"the render keeps what Whisper heard again ({len(qa.get('heard') or [])} words)")
fc = rev.get("final_check") or {}
ok(fc.get("listened") and fc.get("watched") and fc.get("heard") == "render", f"it listened (to the render's own hearing) and watched: {fc}")
ok(fc.get("flags") == 0 and not checks_of(rev), "a clean draft: no flags")
ok(rev["verdict"] == "Ready to post", f"the verdict stands: {rev['verdict']}")
ok(fc.get("frames", 0) >= int(r["duration"] * 10) - 2, f"it looked at 10 frames a second ({fc.get('frames')} frames, {r['duration']:.1f}s)")

# ── 2. Listen back: a bad cut ────────────────────────────────────────────────
print("\n== 2. Listen back: words cut short, said twice, sentences broken, the end mid-sentence")
pid_bad = production("Bad cuts")
sc = TRUE[I("screed")]
cut_in_screed = sc["true_start"] + 0.7 * (sc["true_end"] - sc["true_start"])
B_end = editor.snap(span(I("Then"), I("pour"), LOW), CL[LOW])["end"]
bad_cuts = [editor.snap(span(I("We"), I("first."), LOW), CL[LOW]),
            {**editor.snap(span(I("Then"), I("pour"), LOW), CL[LOW])},                         # "Then we pour"
            {**editor.snap(span(I("we"), I("screed"), LOW), CL[LOW]), "end": round(cut_in_screed, 3)},  # "we pour a level scr-"
            editor.snap(span(I("It"), I("about"), LOW), CL[LOW]),                               # "It takes about"
            editor.snap(span(I("dry."), I("dry."), LOW), CL[LOW]),                              # (two days to) "dry."
            editor.snap(span(I("For"), I("porcelain."), LOW), CL[LOW]),
            editor.snap(span(I("So"), I("stays"), LOW), CL[LOW])]                               # "So the floor stays"
render(pid_bad, bad_cuts, bad=True)
r_bad, rev = read(pid_bad)
edl = json.loads(r_bad["edl"]); cuts_r = edl["cuts"]
flags = checks_of(rev)
print("  flags:", [(f["check"], f["start"], f["what"], f["action"]["type"], f["words"]) for f in flags])
by = lambda kind: [f for f in flags if f["check"] == kind]
t_screed_cut = final_check.pieces(cuts_r)[2]["d1"]
cs = [f for f in by("cut_short") if "screed" in f["what"]]
ok(cs and abs(cs[0]["start"] - t_screed_cut) < 0.1 and cs[0]["action"]["type"] == "move_cut"
   and cs[0]["action"]["field"] == "before" and cs[0]["action"]["amount"] > 0,
   f"a word cut short: \"screed\" at the cut ({t_screed_cut:.2f}s), keep its end (move_cut +{cs[0]['action']['amount'] if cs else '?'}s)")
ok(cs and "|" in cs[0]["words"] and "level" in cs[0]["words"], f"  with the words around it: \"{cs[0]['words'] if cs else ''}\"")
db = by("doubled")
we_first = draft_t(cuts_r, LOW, whisper[I("we")]["start"])
ok(db and abs(db[0]["start"] - we_first) < 0.15 and db[0]["action"]["type"] == "drop_words"
   and db[0]["action"]["from"].lower().split() == ["we", "pour", "we", "pour"],
   f"words said twice (two takes either side of a join): \"{db[0]['action']['from'] if db else ''}\" at {db[0]['start'] if db else '?'}s")
br = [f for f in by("broken") if f["action"]["type"] == "restore_words" and "two" in f["action"]["from"]]
t_join = final_check.pieces(cuts_r)[4]["d0"]
ok(br and br[0]["action"]["from"] == "two days to" and abs(br[0]["start"] - t_join) < 0.05,
   f"a join that cut words out of a sentence: bring back \"{br[0]['action']['from'] if br else ''}\" at {t_join:.2f}s")
mid = [f for f in by("broken") if f["subject"] == "screed"]
ok(mid and mid[0]["action"]["type"] == "restore_words" and mid[0]["action"]["from"] == "over the whole room.",
   f"a shot that stops mid-sentence: \"{mid[0]['action']['from'] if mid else ''}\" comes back")
em = by("ends_mid")
ok(em and em[0]["action"]["type"] == "restore_words" and em[0]["action"]["from"] == "flat for years."
   and em[0]["subject"] == "stays", f"the voice ends mid-sentence (on \"stays\"): finish it with \"{em[0]['action']['from'] if em else ''}\"")
ok(all(shaped(f) for f in flags), "every flag is a fix in the read's own shape: " + str([f["check"] for f in flags if not shaped(f)]))
ok(all(f["start"] is not None and f["words"] for f in flags if f["check"] != "ends_mid") and all(f["words"] for f in flags),
   "every flag has its moment on the draft and the words around it")
ok(rev["verdict"].startswith("Nearly ready") and rev["scores"]["sound"] == "fix", f"the verdict and scores follow: {rev['verdict']}")
ok(rev["final_check"]["flags"] == len(flags) <= final_check.MAX_FLAGS, f"{len(flags)} flags, at most {final_check.MAX_FLAGS}")
# The patches take them as they are
new, res = fix_actions.apply(edl, CL, [{"n": n, "action": f["action"]} for n, f in enumerate(flags, 1)])
print("  patches:", [(r_["type"], r_.get("did") or r_.get("why")) for r_ in res])
ok(not [f for f in by("broken") if f["subject"] == "pour"], "a shot followed by a retake of its own words isn't a broken sentence (it's the repeat)")
ok(sum(1 for r_ in res if r_["ok"]) >= len(flags) - 1, f"the patches make them ({sum(1 for r_ in res if r_['ok'])} of {len(flags)})")
words_after = [w["word"] for w in editor.timeline(new["cuts"], CL)]
ok(words_after[-1] == "years." and "two" in words_after and words_after.count("we") == 2,
   f"after the patches: ends \"{' '.join(words_after[-3:])}\", \"two days to\" back, one \"we pour\"")

# ── 3. Studio's own rounds fix them before Bryan sees it ─────────────────────
print("\n== 3. Studio's own fix rounds")
first = len(flags)
r3, rev3 = read(pid_bad, rounds=2)
hist = json.loads(r3["self_fix"])
left = checks_of(rev3)
print("  rounds:", [(x["round"], x["how"], x.get("kept"), x.get("why"), [y.get("did") or y.get("why") for y in x.get("results", [])])
                    for x in hist["rounds"]])
print("  fixed:", [f["what"] for f in hist["fixed"]], "| left:", [(f["check"], f["what"], f.get("left_because")) for f in left])
ok(any(x.get("kept") and x["how"] == "patch" for x in hist["rounds"]), "a patch round was kept")
ok(len(hist["fixed"]) >= first - 1 and len(left) < first, f"Studio fixed {len(hist['fixed'])} of the {first} itself; {len(left)} left")
ok(all(f.get("left_because", {}).get("text") for f in rev3.get("fixes", [])), "anything left says why (left_because)")
final_words = [w["word"] for w in editor.timeline(json.loads(r3["edl"])["cuts"], CL)]
ok(final_words[-1] == "years.", f"the draft Bryan sees ends on a full sentence: \"... {' '.join(final_words[-4:])}\"")
ok(r3["status"] == "ready" and Path(r3["video_path"]).exists(), "the draft goes to To review")

# ── 4. Watch through: titles over the face, flashes, too fast, twice, late ───
print("\n== 4. Watch through")
pid_w = production("Bad titles")
W_PLAN = {"cuts": [span(I("We"), I("room."), HIGH), {"clip_id": BROLL, "start": 0.5, "end": 5.5, "why": "broll"},
                   span(I("It"), I("porcelain."), LOW), span(I("So"), I("years."), LOW)],
          "hook_text": "", "graphics": [], "music": dict(MUSIC)}
w_cuts = editor._cut(W_PLAN, CL)
parts = final_check.pieces(w_cuts)
high_end = max(p["d1"] for p in parts if p["clip_id"] == HIGH)
p_broll = next(p for p in parts if p["clip_id"] == BROLL)
p_low = [p for p in parts if p["clip_id"] == LOW]
low_a = p_low[0]["d0"]; low_end = p_low[-1]["d1"]
print("  pieces:", [(p["clip_id"], round(p["d0"], 2), round(p["d1"], 2)) for p in parts])
def callout(title, start, dur, lines=()):
    return {"clip_id": None, "at": None, "style": "callout", "title": title, "lines": list(lines), "start": round(start, 3),
            "duration": round(dur, 3), "scene": "", "look": "cutaway", "role": "explain", "why": ""}
G = [callout("Level *screed*", high_end - 2.6, 2.2, ["Over the whole room"]),     # 1 over the face
     callout("Two *days*", low_a + 0.5, 0.4),                                          # 2 a flash
     callout("Porcelain lasts in a busy *kitchen*", low_a + 2.0, 1.4),                 # 3 too fast to read
     callout("Use *porcelain*", low_a + 4.6, 1.5), callout("Use *porcelain*", low_a + 6.1, 1.5),  # 4, 5 twice
     callout("Flat for *years*", low_end - 0.9, 1.5)]                                  # 6 still on at the close
print("  graphics:", [(g["title"], g["start"], g["duration"]) for g in G])
r_w = render(pid_w, w_cuts, G, hook="Floors stripped back to the *screed*", bad=True)
r_w, rev = read(pid_w)
flags = checks_of(rev)
print("  flags:", [(f["check"], f["start"], f["what"], f["action"]["type"], f["action"].get("item"), f["action"].get("start"))
                   for f in flags])
print("  final_check:", rev.get("final_check"))
by = lambda kind: [f for f in flags if f["check"] == kind]
hook = [f for f in by("covers_face") if f["area"] == "hook"]
ok(hook and hook[0]["action"]["type"] == "replan" and hook[0]["start"] == 0, "the hook over the face: flagged (a re-plan for a shorter hook)")
g1 = [f for f in by("covers_face") if f["action"].get("item") == 1]
ok(g1 and g1[0]["action"]["type"] == "graphic" and g1[0]["action"]["field"] == "time"
   and p_broll["d0"] - 0.05 <= g1[0]["action"]["start"] and g1[0]["action"]["start"] + 2.2 <= p_broll["d1"] + 0.3,
   f"a callout over the face: moved to the B-roll, clear of it ({g1[0]['action']['start'] if g1 else '?'}s; B-roll {p_broll['d0']:.1f}-{p_broll['d1']:.1f}s)")
fl = by("flash")
ok(fl and fl[0]["action"]["item"] == 2 and fl[0]["action"]["amount"] >= 1.2, "a 0.4 s graphic: a flash, kept up long enough to read")
tf = by("too_fast")
ok(tf and tf[0]["action"]["item"] == 3 and abs(tf[0]["action"]["amount"] - (0.3 + 0.06 * len("Porcelain lasts in a busy kitchen"))) < 0.02,
   f"words that leave before they can be read: up {tf[0]['action']['amount'] if tf else '?'}s (0.3 + 0.06 a letter)")
rp = by("repeat")
ok(rp and rp[0]["action"]["field"] == "remove" and rp[0]["action"]["item"] == 5, "the same animation twice in a row: the second goes")
lt = [f for f in by("late") if f["action"]["type"] == "graphic"]
close_at = json.loads(r_w["edl"])["close"]["at"]
ok(lt and lt[0]["action"]["item"] == 6 and lt[0]["action"]["start"] + (lt[0]["action"]["amount"] or 0) <= close_at - 1.0 + 0.01,
   f"still on at the close: finishes a second before it ({close_at:.2f}s)")
ok(not [f for f in by("late") if f["subject"] == "the end card"], "the end card itself holds still at the very end (it's deliberate)")
ok(not [f for f in flags if f["check"] in ("cut_short", "doubled", "broken", "ends_mid")], "these cuts are clean: nothing to hear")
ok(all(shaped(f) for f in flags), "every flag is a fix in the read's own shape")
edl_w = json.loads(r_w["edl"])
new, res = fix_actions.apply(edl_w, CL, [{"n": n, "action": f["action"]} for n, f in enumerate(flags, 1)])
print("  patches:", [(r_["type"], r_.get("did") or r_.get("why")) for r_ in res])
ok(all(r_["ok"] for r_, f in zip(sorted(res, key=lambda x: x["n"]), flags) if f["action"]["type"] == "graphic"),
   "every graphic fix is made by the patches")
job = final_check._job(conn, {**row(pid_w), "edl": json.dumps(new)})
after_watch, _ = final_check.watch(job)
ok(not [f for f in after_watch if f["check"] in ("flash", "too_fast", "repeat", "late") or f["action"].get("item")],
   f"the graphics as patched pass the check ({[(f['check'], f['what']) for f in after_watch]})")

# ── 5. Pieces of the check on their own ──────────────────────────────────────
print("\n== 5. Pieces")
# A word the draft's sound lost (muted at a join), not heard again: missing
clean = row(pid_clean); ce = json.loads(clean["edl"]); cparts = final_check.pieces(ce["cuts"])
want = final_check.intended(cparts, CL)
lastw = [w for w in want if w["piece"] == 0][-1]
muted = P / "muted.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", clean["video_path"], "-c:v", "copy", "-af",
                f"volume=enable='between(t,{lastw['start'] - 0.02:.3f},{cparts[0]['d1'] + 0.02:.3f})':volume=0",
                "-c:a", "aac", str(muted)], check=True)
heard = listener(muted, ce["cuts"])["words"]
job = {"id": 0, "video": muted, "edl": ce, "qa": {"heard": heard}, "clips": CL, "total": clean["duration"]}
fl, src = final_check.listen(job)
miss = [f for f in fl if f["check"] == "cut_short" and f["subject"] == lastw["raw"].strip()]
ok(src == "render" and miss and "isn't heard" in miss[0]["why"], f"a word missing from the finished sound (\"{lastw['raw']}\" muted): flagged ({[f['what'] for f in fl]})")
job["qa"] = {"heard": listener(clean["video_path"], ce["cuts"])["words"]}; job["video"] = Path(clean["video_path"])
ok(not final_check.listen(job)[0], "the same draft unmuted: nothing")
# Without qa["heard"] it hears the file itself
job["qa"] = {}
CURRENT["cuts"] = ce["cuts"]
fl2, src2 = final_check.listen(job)
ok(src2 == "here" and not fl2, "an older draft (no heard words kept): it re-transcribes the file itself")
# The end card still moving at the very end
moving = P / "moving.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x18231f:size=1080x1920:rate=30:duration=3",
                "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3", "-filter_complex",
                "[0:v][1:v]overlay=enable='gte(t,2.4)':shortest=0[v]", "-map", "[v]", "-t", "3", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(moving)], check=True)
still = P / "still.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x18231f:size=1080x1920:rate=30:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(still)], check=True)
card_edl = {"cuts": [], "graphics": [], "hook_text": "", "close": {"at": 1.0, "card": {"seconds": 2.0}}}
fm, _ = final_check.watch({"id": 0, "video": moving, "edl": card_edl, "qa": {}, "clips": {}, "total": 3.0})
fs, _ = final_check.watch({"id": 0, "video": still, "edl": card_edl, "qa": {}, "clips": {}, "total": 3.0})
ok(fm and fm[0]["check"] == "late" and not fm[0]["editor_can_fix"] and fm[0].get("left_note") and not fs,
   f"the video ending mid-animation: flagged for Bryan to look at ({fm[0]['what'] if fm else ''}); a still end: nothing")
# The same drawing twice
grey = np.zeros((60, final_check.FRAME_H, final_check.FRAME_W), np.uint8)
d1 = {"kind": "drawing", "text": "Layers", "start": 1.0, "end": 3.5, "g": {"scene": {"type": "layers", "x": 1}, "type": "layers", "look": "cutaway"}}
d2 = {**d1, "text": "Other", "start": 3.6, "end": 6.0}
d3 = {**d1, "text": "Other", "start": 3.6, "end": 6.0, "g": {"scene": {"type": "steps"}, "type": "steps", "look": "cutaway"}}
grey2 = grey.copy(); grey2[36:] = 200
ok(final_check._same(d1, {**d2, "g": dict(d1["g"])}, grey) and final_check._same(d1, {**d2, "g": {**d1["g"], "scene": {"type": "layers", "x": 2}}}, grey)
   and not final_check._same(d1, d3, grey) and not final_check._same(d1, {**d2, "g": {**d1["g"], "scene": {"type": "layers", "x": 2}}}, grey2),
   "the same animation: the same drawing, or one of the same kind that looks the same; a different kind or look: no")
# A join Claude judged reads complete stays as it is
class Joins(Fake):
    def create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=json.dumps(
            {"joins": [{"id": 1, "reads_complete": True, "restore": []}]}))],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
gap_cuts = [editor.snap(span(I("Then"), I("level"), LOW), CL[LOW]), editor.snap(span(I("whole"), I("room."), LOW), CL[LOW])]
long_gap = [{**gap_cuts[0]}, {**gap_cuts[1]}]
jc = Joins({})
out, notes = editor.join_guard(long_gap, CL, jc, "t", ["drop the screed bit"])
ok(len(out) == 2 and out[1].get("sentence_ok") and jc.calls, "join_guard marks a join Claude says reads complete (sentence_ok)")
bj = final_check._broken(final_check.pieces(out), CL, final_check.intended(final_check.pieces(out), CL))
bj0 = final_check._broken(final_check.pieces(long_gap), CL, final_check.intended(final_check.pieces(long_gap), CL))
ok(not bj and bj0, "the listen-back leaves that join alone (it flags the same join when nobody judged it)")
# A join whose cut words are a stutter of what follows ("excite | excites") isn't a broken sentence
ws = lambda *t: [{"word": x} for x in t]
ok(final_check._retaken({"words": ws("This", "excite", "excites", "everyone."), "real": [1], "first": 2})
   and final_check._retaken({"words": ws("we", "use", "the", "we", "always", "use", "the", "glue."), "real": [0, 1, 2], "first": 3})
   and not final_check._retaken({"words": ws("about", "two", "days", "to", "dry."), "real": [1, 2, 3], "first": 4}),
   "a stutter or a false start cut at a join is left cut; real words cut from a sentence aren't")
# A repeat across a join can be dropped by the patch (the words heard after it repeat it)
rep_cuts = [editor.snap(span(I("Then"), I("pour"), LOW), CL[LOW]), editor.snap(span(I("we"), I("room."), LOW), CL[LOW])]
d = fix_actions.Draft(rep_cuts, CL)
wa = next(w for w in d.words if w["raw"] == "we")
ops, did = fix_actions._plan_drop(d, {"from": "we pour we pour", "start": wa["start"], "end": wa["end"] + 0.5})
ok(ops and "we pour" in did, f"drop_words takes a repeat across a join: {did}")
# self_fix: matching and why it's left
a = {"check": "cut_short", "subject": "screed", "start": 5.0, "what": "Keep the end of \"screed\" at 0:05", "area": "sound"}
ok(self_fix.same_fix(a, {**a, "start": 5.6, "what": "Keep the end of \"screed\" at 0:05"})
   and not self_fix.same_fix(a, {**a, "subject": "room."}) and not self_fix.same_fix(a, {**a, "check": "doubled"})
   and not self_fix.same_fix(a, {**a, "start": 9.0}), "self_fix tells one flag from another by its kind, word and moment")
lb = self_fix.left_because({**fm[0]}, [], None, 2, 1.5)
ok(lb["text"] == fm[0]["left_note"], f"a flag Studio can't fix says why: {lb}")

# ── 6. It never breaks a draft ───────────────────────────────────────────────
print("\n== 6. Never breaks a draft")
orig_check, orig_listen = final_check.check, final_check.listen
final_check.check = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
rv = draft_review.run(conn, pid_clean, FAKE)
ok(rv and rv["verdict"] == "Ready to post" and "final_check" not in rv, "the check failing: the read goes on without it")
final_check.check = orig_check
final_check.listen = lambda job: (_ for _ in ()).throw(ValueError("bad"))
res6 = final_check.run(conn, row(pid_w))
ok(res6 and not res6["listened"] and res6["watched"] and res6["flags"], "listening failing: watching still runs")
final_check.listen = orig_listen
ok(final_check.start(conn, {**row(pid_clean), "video_path": str(P / "nope.mp4")}) is None, "no video: nothing starts")
ok(final_check.start(conn, {**row(pid_clean), "format": "carousel"}) is None, "a carousel: nothing (Reels only)")
os.environ["STUDIO_FINAL_CHECK"] = "0"; importlib.reload(settings)
ok(final_check.start(conn, row(pid_clean)) is None, "STUDIO_FINAL_CHECK=0: off")
os.environ["STUDIO_FINAL_CHECK"] = ""; importlib.reload(settings)
never = Future()
ok(final_check.merge({"fixes": [], "verdict": "Ready to post"}, never, wait=0.2) == {"fixes": [], "verdict": "Ready to post"},
   "a check that doesn't finish in time: the read stands")
bad_result = Future(); bad_result.set_result({"flags": "not a list"})
ok(final_check.merge({"fixes": [], "verdict": "Ready to post"}, bad_result)["verdict"] == "Ready to post", "a garbled result: the read stands")
broken_draft = production("Broken")
render(broken_draft, [editor.snap(span(I("We"), I("first."), LOW), CL[LOW])])
orig_frames = final_check.frames
final_check.frames = lambda video: (_ for _ in ()).throw(OSError("disk"))
r6, rev6 = read(broken_draft)
ok(r6["status"] == "ready" and rev6.get("verdict") and rev6.get("final_check", {}).get("watched") is False,
   "a draft whose watch-through fails still goes to To review with its read (and its listen-back)")
final_check.frames = orig_frames

# ── 7. How long it takes on a ~45 s Reel ─────────────────────────────────────
print("\n== 7. Runtime on a ~45 s Reel")
transcribe.transcribe = listener
pid_long = production("Long")
lw = [{k: w[k] for k in ("word", "start", "end")} for w in long_true]
li = {}
for i, w in enumerate(lw):
    li.setdefault(w["word"], []).append(i)
LP = {"cuts": [{"clip_id": LONG, "start": lw[li["We"][0]]["start"], "end": lw[li["years."][0]]["end"], "why": "a"},
               {"clip_id": LONG, "start": lw[li["We"][1]]["start"], "end": lw[li["down."][1]]["end"], "why": "b"}],
      "hook_text": "Floors *stripped*", "key_words": [],
      "graphics": [{"clip_id": LONG, "at": lw[li["It"][0]]["start"], "duration": 2.5, "style": "callout", "title": "Two days to *dry*",
                    "lines": [], "scene": "", "look": "cutaway", "role": "explain", "why": ""},
                   {"clip_id": LONG, "at": lw[li["For"][1]]["start"], "duration": 3.0, "style": "card", "title": "Porcelain in *kitchens*",
                    "lines": ["Hard wearing"], "scene": "", "look": "cutaway", "role": "explain", "why": ""}],
      "music": dict(MUSIC), "summary": "x", "notes_addressed": [], "missing_shots": []}
FAKE = Fake(LP); anthropic.Anthropic = lambda **kw: FAKE
draft_review.safe_run = lambda *a, **k: None  # rendered first; the check is timed on its own below
editor.edit(conn, pid_long, client=FAKE, find_track=fake_track)
importlib.reload(draft_review)
rl = row(pid_long)
print(f"  draft: {rl['duration']:.1f}s")
t0 = time.time(); fr = final_check.frames(Path(rl["video_path"])); t_dec = time.time() - t0
t0 = time.time(); res7 = final_check.run(conn, rl); t_all = time.time() - t0
print(f"  frames decoded in {t_dec:.1f}s; listen-back + watch-through {t_all:.1f}s ({res7['seconds']}s inside), "
      f"{res7['frames']} frames, flags {[(f['check'], f['what']) for f in res7['flags']]}")
ok(38 <= rl["duration"] <= 58, f"a {rl['duration']:.0f}s Reel")
ok(t_all < 90, f"the check takes {t_all:.1f}s on this machine (budget 60-90 s)")
slow = Fake(LP, delay=8.0)
settings.FINAL_CHECK = False
t0 = time.time(); draft_review.run(conn, pid_long, slow); t_off = time.time() - t0
settings.FINAL_CHECK = True
t0 = time.time(); rv7 = draft_review.run(conn, pid_long, slow); t_on = time.time() - t0
print(f"  a read whose Claude call takes 8 s: {t_off:.1f}s without the check, {t_on:.1f}s with it alongside "
      f"({rv7.get('final_check')})")
ok(rv7.get("final_check") and t_on - t_off < 0.6 * rv7["final_check"]["seconds"],
   f"the check runs while Claude reads: it adds {t_on - t_off:.1f}s of its {rv7['final_check']['seconds']}s")

finish()
