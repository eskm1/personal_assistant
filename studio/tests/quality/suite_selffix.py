"""Studio fixes its own list before Bryan sees a draft, and learns the rules.
Real ffmpeg + HyperFrames renders (part 1, 2, 5), Claude stubbed (llm.final_message), the Whisper
re-check stubbed. Parts 3-4 use quick fake renders to drive the loop's edge cases.
Run: python tests/quality/suite_selffix.py [--skip-real] (from studio/)"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as E, ok, finish, results
import media
M = E / "media"
REAL = "--skip-real" not in sys.argv
os.environ.update(ANTHROPIC_API_KEY="test",
                  EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers", TELEGRAM_BOT_TOKEN="",
                  STUDIO_SUPABASE_URL="", STUDIO_SUPABASE_SERVICE_KEY="")
os.environ["STUDIO_SELF_FIX_ROUNDS"] = ""
# 2026-10-21: Studio's listen-back and watch-through (final_check.py) adds its own fixes to every read
# (here: the 'To level the floor' card is up 2.6 s for 3.1 s of words, one more fix round). This suite pins the fixes and rounds its scripted read gives, so it runs without it;
# finalcheck/finalcheck_test.py covers the check and how its fixes go through the rounds.
os.environ["STUDIO_FINAL_CHECK"] = "0"
import settings, catalog, extract, editor, draft_review, llm, learning, playbook, self_fix, usage, notify, music, worker
import flask

settings.ensure_folders(); conn = catalog.connect()
media.quiet_talk(M, editor.loudness)

# ── The clip: the quiet, noisy talking head from sound_fonts_test (editfix/media) ──
words = json.loads((M / "clipdir" / "transcript.json").read_text())["words"]
vlen = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(M / "talk.mp4")],
                            capture_output=True, text=True).stdout)
# Whisper heard a stray "And" just before the end (the start of a new sentence), like Bryan's draft.
words = words + [{"word": "And", "start": round(vlen - 1.25, 3), "end": round(vlen - 1.2, 3)}]
d = settings.LIBRARY / "2026-10" / "talk"; d.mkdir(parents=True)
v = d / "talk.mp4"; shutil.copy(M / "talk.mp4", v)
meta = extract.probe(v)
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": "", "words": words, "segments": []}), encoding="utf-8")
cid = catalog.start(conn, "fp-talk", v.name, d, v)
catalog.save_tags(conn, cid, meta, {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "Floor levels",
                   "summary": "Bryan on uneven floors", "topics": [], "setting": "", "usable": True, "quality_notes": "fine",
                   "suggested_hook": "", "moments": []})

def new_production(title):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": title, "hook": "Uneven floors",
        "angle": "x", "beats": [], "segments": [{"clip_id": cid, "start": 0, "end": vlen, "purpose": "all"}],
        "caption": "cap", "est_seconds": 25}])
    return catalog.make_production(conn, conn.execute("select max(id) from ideas").fetchone()[0])

def row(pid): return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())

def fake_track(brief, seconds, out_dir, ref=""):
    return M / "music.mp3", {"title": "Test bed", "artist": "ffmpeg", "recording_id": "t1"}
music.find_track = fake_track
music.allowed_for = lambda b: True
editor.check_words = lambda final, expected, work: {"words_expected": len(expected), "match": 1.0, "missing": []}

def at(text):  # a word's start time in the clip
    return next(w["start"] for w in words if w["word"].lower().startswith(text.lower()))

def edl(hook, card_at, volume=-16.0, notes=()):
    return {"cuts": [{"clip_id": cid, "start": 0.0, "end": round(vlen - 0.4, 3), "why": "the whole explanation"}],
            "hook_text": hook, "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": volume},
            "summary": "Kept the explanation.", "missing_shots": [], "notes_addressed": list(notes),
            "graphics": [{"clip_id": cid, "at": card_at, "duration": 2.6, "style": "card", "title": "To level the floor",
                          "lines": ["We pour a screed", "Then we tile"]}]}

SCORES_OK = {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")}
def fix(what, fixable, rule="", when="this_draft", area="other", start=None, same=0):
    return {"what": what, "why": "because", "start": start, "end": None, "area": area, "when": when,
            "kind": "detail" if when == "next_shoot" else "none", "editor_can_fix": fixable, "rule": rule, "same_rule": same}
NEXT_SHOOT = fix("Film a slow, low close-up of the step at the doorway", False, when="next_shoot", area="picture")
JUDGEMENT = fix("Decide whether to mention the overlay's price", False, area="story")
READ1 = {"verdict": "Nearly ready: 4 things to fix", "scores": {**SCORES_OK, "hook": "fix", "captions": "fix", "sound": "fix"},
         "fixes": [fix("Open on a stronger claim: 'Different floor heights? One is an overlay.'", True,
                       "Open on the strongest claim, never a slow setup", area="hook", start=0.0),
                   fix("Move the 'To level the floor' card off the caption line", True, "Keep cards clear of the caption line",
                       area="captions", start=15.0),
                   fix("Turn the music down under the voice", True, "Keep the music well under the voice", area="sound"),
                   NEXT_SHOOT, JUDGEMENT], "works": ["Clear explanation", "Good light"]}
READ2 = {"verdict": "Nearly ready: 1 thing to fix", "scores": SCORES_OK, "fixes": [NEXT_SHOOT, JUDGEMENT], "works": ["Strong opening"]}

# Claude, stubbed: a queue of answers per kind of call; every call is kept with the draft's status then.
QUEUE = {"edit": [], "review": [], "carousel_revise": [], "carousel_review": []}
CALLS = []
def kind_of(req):
    sysp = req.get("system", "")
    if "You are the editor for Bryan's Instagram Reels" in sysp: return "edit"
    if "You review draft Instagram Reels" in sysp: return "review"
    if "You revise a draft Instagram carousel" in sysp: return "carousel_revise"
    if "You review draft Instagram carousels" in sysp: return "carousel_review"
    raise AssertionError("unexpected call")
WATCH = {"pid": None}
def fake_final(client, **req):
    k = kind_of(req)
    status = row(WATCH["pid"])["status"] if WATCH["pid"] else None
    text = "\n".join(b["text"] for b in req["messages"][0]["content"] if isinstance(b, dict) and b.get("type") == "text") \
        if isinstance(req["messages"][0]["content"], list) else req["messages"][0]["content"]
    CALLS.append({"kind": k, "status": status, "system": req.get("system", ""), "text": text})
    answer = QUEUE[k].pop(0)
    if isinstance(answer, Exception):
        raise answer
    return NS(content=[NS(type="text", text=json.dumps(answer))], stop_reason="end_turn", model="claude-opus-5-5",
              usage=NS(input_tokens=10000, output_tokens=2000, cache_read_input_tokens=0, cache_creation_input_tokens=0))
llm.final_message = fake_final

NOTIFIED = []
orig_add = notify.digest.add
def fake_add(kind, n=1):
    NOTIFIED.append((kind, row(WATCH["pid"])["status"] if WATCH["pid"] else None))
notify.digest.add = fake_add

app = __import__("dashboard").app
def detail(pid):
    import dashboard
    with app.test_request_context(f"/api/production/{pid}"):
        flask.g.user = {"id": 1, "role": "owner", "name": "Bryan"}
        return dashboard.production_detail(pid).get_json()

if REAL:
    # ═══ 1. A draft with things Studio can fix itself (real renders) ════════
    print("\n== 1. Self-fix: read -> one round -> read is clean of editor fixes")
    pid = new_production("Uneven floor levels mean an overlay"); WATCH["pid"] = pid
    QUEUE["edit"] += [edl("Uneven floors", at("To")),
                      edl("Different floor heights? One is an overlay.", at("That"), volume=-18.0, notes=[
                          {"note_id": i, "how": h, "kind": "editing", "rule": "", "same_rule": 0}
                          for i, h in ((1, "new hook"), (2, "card moved to the doorway line"), (3, "music lower"))])]
    QUEUE["review"] += [READ1, READ2]
    t0 = time.time()
    worker.run_editor(stop_after_one=True)
    print(f"  ({time.time() - t0:.0f}s)")
    r = row(pid)
    ok(r["status"] == "ready" and r["version"] == 1, f"draft ready as v1 ({r['status']}, v{r['version']})")
    ok([c["kind"] for c in CALLS] == ["edit", "review", "edit", "review"], f"calls: {[c['kind'] for c in CALLS]}")
    ok(all(c["status"] == "producing" for c in CALLS), f"still 'producing' during every call: {[c['status'] for c in CALLS]}")
    ok(NOTIFIED == [("draft", "ready")], f"Bryan told once, at the end: {NOTIFIED}")
    p2 = CALLS[2]["text"]
    ok("a round of Studio's own fixes" in p2 and "fix 1 at 0.0s: Open on a stronger claim" in p2 and "fix 3" in p2
       and "fix 4" not in p2, "the round's plan gets the 3 editor fixes (not the next-shoot or judgement ones)")
    ok("Previous graphics:" in p2 and "To level the floor" in p2, "the round's plan sees where the card was")
    ok(r["video_path"].endswith("_v1_fix1.mp4") and Path(r["video_path"]).exists(), f"Bryan's v1 is the fixed render ({Path(r['video_path']).name})")
    ok(not (editor.RENDERS / f"production_{pid}_v1.mp4").exists(), "the first render's file was removed")
    e = json.loads(r["edl"])
    ok(e["hook_text"].startswith("Different floor heights") and e["music"]["volume_db"] == -18.0, "the fixed edit is the stored one")
    rv = draft_review.current(r)
    ok(rv and rv["verdict"] == "Nearly ready: 1 thing to fix" and len(rv["fixes"]) == 2, "the read Bryan sees is the second one (what's left)")
    sf = json.loads(r["self_fix"])
    ok(sf["version"] == 1 and len(sf["rounds"]) == 1 and sf["rounds"][0]["kept"] and sf["rounds"][0]["score"] < sf["rounds"][0]["before"],
       f"history kept for debugging: {[(x['round'], x['kept'], x['score']) for x in sf['rounds']]}")
    ok(sf["first_read"]["fixable"] == 3 and sf["rounds"][0]["file"].endswith("_fix1.mp4"), "first read and the round's file in the history")
    qa = json.loads(r["qa"])
    did = [c["did"] for c in qa["checks"]]
    ok(any("stray \"And\"" in x for x in did), f"code check: ended on the last full sentence: {did}")
    ok(any("brought your voice up" in x for x in did), "code check: the quiet voice brought up")
    ok(qa["sound"]["gap_db"] >= 19 and abs(qa["lufs"] + 14) <= 1, f"the draft's sound: voice {qa['sound']['gap_db']} dB over the music, {qa['lufs']} LUFS")
    tl = editor.timeline(e["cuts"], {cid: dict(conn.execute("select * from clips where id=?", (cid,)).fetchone())})
    ok(tl[-1]["word"] == "well.", f"the draft ends on 'as well.' ({tl[-1]['word']})")
    caught = 3 + sum(1 for c in qa["checks"] if c["counted"])
    ok(sf["caught"] == caught, f"caught {sf['caught']} editing problems before Bryan saw it (3 from the read + code checks)")
    uses = conn.execute("select kind, ref, cost_usd from usage where ref like ?", (f"production:{pid}%",)).fetchall()
    own = [u for u in uses if u["ref"] == self_fix.ref(pid)]
    ok(sorted(u["kind"] for u in own) == ["edit", "review"] and all(u["cost_usd"] > 0 for u in own),
       f"the round's calls recorded apart, same kinds: {[(u['kind'], u['ref']) for u in uses]}")
    rules = learning.active("reel")
    ok({x["text"] for x in rules} == {"Open on the strongest claim, never a slow setup.", "Keep cards clear of the caption line.",
                                      "Keep the music well under the voice."}, f"3 rules learned: {[x['text'] for x in rules]}")
    ok("## Editing rules Studio learned" in playbook.text() and "Keep cards clear of the caption line." in playbook.text(), "in the playbook")
    dj = detail(pid)
    ok(dj["self_fix"]["fixed"][:3] == [f["what"] for f in READ1["fixes"][:3]] and len(dj["self_fix"]["fixed"]) >= 4,
       f"Review: 'Studio fixed {len(dj['self_fix']['fixed'])} things' list: {dj['self_fix']['fixed']}")
    ok(len(dj["review"]["fixes"]) == 2, "Review: only what's left as fixes")
    ok(dj["self_fix_cost"] > 0 and dj["cost"] >= dj["self_fix_cost"] + 0.01, f"cost line: total {dj['cost']}, Studio's own fixes {dj['self_fix_cost']}")
    ok(len(dj["rules"]["learned"]) == 3 and dj["rules"]["code"], "Review: learned rules and code checks")
    ok(dj["self_fix_trend"]["recent"]["drafts"] == 1, f"trend logged: {dj['self_fix_trend']}")
    ok(dj["version"] == 1, "one version in front of Bryan")
    p1 = CALLS[0]["system"]
    ok(learning.START not in p1 and "R1:" not in p1, "(first plan came before any rules)")

    # ═══ 2. Send back: Bryan's notes, one editing and one taste ═══════════
    print("\n== 2. Send back")
    CALLS.clear(); NOTIFIED.clear()
    n_music = catalog.add_feedback(conn, "production", pid, "The music is too loud vs the voice", 5.0)
    n_take = catalog.add_feedback(conn, "production", pid, "Use the take where I smile")
    catalog.set_production_status(conn, pid, "revising")
    music_rule = next(x for x in rules if "music" in x["text"])
    QUEUE["edit"] += [edl("Different floor heights? One is an overlay.", at("That"), volume=-20.0, notes=[
        {"note_id": n_music, "how": "music lower", "kind": "editing", "rule": "Music always sits well below Bryan's voice", "same_rule": music_rule["id"]},
        {"note_id": n_take, "how": "used the smiling take", "kind": "taste", "rule": "", "same_rule": 0}])]
    QUEUE["review"] += [{"verdict": "Ready to post", "scores": SCORES_OK, "fixes": [], "works": ["All good"]}]
    worker.run_editor(stop_after_one=True)
    r = row(pid)
    ok(r["status"] == "ready" and r["version"] == 2 and r["video_path"].endswith("_v2.mp4"), f"v2 ready ({Path(r['video_path']).name})")
    ok([c["kind"] for c in CALLS] == ["edit", "review"], "no self-fix round when nothing is left to fix")
    ok("Bryan's notes on that draft" in CALLS[0]["text"] and "The music is too loud" in CALLS[0]["text"], "Bryan's notes in the plan, as before")
    ok("Editing rules Studio learned" in CALLS[0]["system"] and "Keep cards clear of the caption line" in CALLS[0]["system"],
       "the editor's plan now includes the learned rules")
    ok("Bryan asked for these changes on the last version" in CALLS[1]["text"], "the read is told not to undo Bryan's notes")
    fb = {x["id"]: dict(x) for x in conn.execute("select * from feedback where target_id = ?", (pid,))}
    ok(fb[n_music]["status"] == "addressed" and fb[n_music]["kind"] == "editing" and fb[n_music]["rule"] == music_rule["text"],
       f"editing note -> counted on the existing rule: {fb[n_music]['rule']}")
    ok(fb[n_take]["kind"] == "taste" and fb[n_take]["rule"] is None, "taste note: no rule")
    ok(next(x for x in learning.active("reel") if x["id"] == music_rule["id"])["count"] == 2, "the music rule now caught twice")
    ok(NOTIFIED == [("draft", "ready")], f"notified once: {NOTIFIED}")
    dj = detail(pid)
    note = next(x for x in dj["notes"] if x["id"] == n_music)
    ok(note["rule"] == music_rule["text"], "Review notes carry the rule (the one line)")
    ok(dj["review"]["verdict"] == "Ready to post", "Review shows the read")

# ═══ 3. Loop edge cases (quick fake renders) ═════════════════════════════════
print("\n== 3. Loop edge cases (fake renders)")
small = media.small_clip(E / "media" / "small.mp4")
editor.render_cut = lambda cuts, clips, out: sum(c["end"] - c["start"] for c in cuts)
editor.render_overlay = lambda *a, **k: None
def fake_mix(cut, overlay, music_file, volume_db, total, out, words=(), **kw):
    shutil.copy(small, out); return {"gap_db": 20.0, "voice_gain_db": 2.0}
editor.mix_final = fake_mix
editor.check_loudness = lambda f: {"lufs": -14.0, "true_peak": -2.0}

def run_case(title, edits, reviews, rounds=None):
    global CALLS
    CALLS.clear(); NOTIFIED.clear()
    pid = new_production(title); WATCH["pid"] = pid
    QUEUE["edit"][:] = edits; QUEUE["review"][:] = reviews
    if rounds is not None:
        settings.SELF_FIX_ROUNDS = rounds
    try:
        worker.run_editor(stop_after_one=True)
    finally:
        settings.SELF_FIX_ROUNDS = 3
    return pid, row(pid)

READ_A = {**READ1, "fixes": READ1["fixes"][:2] + [NEXT_SHOOT]}
def whats(review): return [f["what"] for f in (review or {}).get("fixes", [])] if review else None
before_rules = {x["id"]: x["count"] for x in learning.active("reel")}
# a) A round that doesn't improve the read: the version before stays
pid, r = run_case("Not better", [edl("h", at("To")), edl("h2", at("That"))], [READ_A, READ_A])
sf = json.loads(r["self_fix"])
ok(r["status"] == "ready" and r["video_path"].endswith(f"production_{pid}_v1.mp4") and Path(r["video_path"]).exists(),
   "not improved: the first render stays")
ok(not (editor.RENDERS / f"production_{pid}_v1_fix1.mp4").exists(), "the dropped round's file removed")
ok(json.loads(r["edl"])["hook_text"] == "h" and whats(draft_review.current(r)) == whats(READ_A) and not sf["fixed"]
   and sf["rounds"][0]["kept"] is False, "its edit plan and read restored, nothing marked fixed")
ok({x["id"]: x["count"] for x in learning.active("reel")} == before_rules, "nothing learned from a dropped round")
ok([c["kind"] for c in CALLS] == ["edit", "review", "edit", "review"] and NOTIFIED == [("draft", "ready")], "stopped after that round; notified once")
# b) A round that fails: the version before stays, the draft is still delivered
pid, r = run_case("Round fails", [edl("h", at("To")), RuntimeError("Claude's edit plan was cut off")], [READ_A])
sf = json.loads(r["self_fix"])
ok(r["status"] == "ready" and r["video_path"].endswith("_v1.mp4") and "cut off" in sf["rounds"][0]["error"], "failed round: first render delivered")
# c) Setting 0: off
pid, r = run_case("Off", [edl("h", at("To"))], [READ_A], rounds=0)
sf = json.loads(r["self_fix"])
ok([c["kind"] for c in CALLS] == ["edit", "review"] and r["status"] == "ready" and not sf["rounds"], "STUDIO_SELF_FIX_ROUNDS=0: no rounds")
ok(len(draft_review.current(r)["fixes"]) == 3 and sf["caught"] >= 2, "with it off, all fixes stay for Bryan (still counted for the trend)")
# d) At most the set number of rounds (2 here), each better but with a new fixable point each time.
# (2026-10-20: rounds are judged fix by fix, and a fix a re-plan didn't fix isn't re-planned again.)
NEW_B = fix("Tighten the opening so the claim comes sooner", True, "Get to the claim within a second", area="pace", start=1.0)
NEW_C = fix("Cut the stray breath before the last line", True, "", area="sound", start=18.0)
READ_B = {**READ1, "verdict": "Nearly ready: 3 things to fix", "fixes": [NEW_B, NEXT_SHOOT, JUDGEMENT],
          "scores": {**SCORES_OK, "pace": "fix"}}
READ_C = {**READ_B, "scores": SCORES_OK, "fixes": [NEW_C, NEXT_SHOOT]}
pid, r = run_case("Two rounds", [edl("h", at("To")), edl("h2", at("To")), edl("h3", at("To"))], [READ1, READ_B, READ_C],
                  rounds=2)
sf = json.loads(r["self_fix"])
ok(len(sf["rounds"]) == 2 and all(x["kept"] for x in sf["rounds"]) and r["video_path"].endswith("_v1_fix2.mp4"),
   f"two rounds kept, v1 = the second fix ({Path(r['video_path']).name})")
ok(not (editor.RENDERS / f"production_{pid}_v1.mp4").exists() and not (editor.RENDERS / f"production_{pid}_v1_fix1.mp4").exists(),
   "superseded renders removed")
ok([c["kind"] for c in CALLS].count("edit") == 3 and self_fix.fixable(draft_review.current(r)) and sf["stopped"] == "rounds",
   "stops at 2 rounds even with a fixable point left")
ok(len(sf["fixed"]) == 4, f"fixed 3 + 1 across the rounds: {[f['what'][:25] for f in sf['fixed']]}")
left = next(f for f in draft_review.current(r)["fixes"] if f["what"] == NEW_C["what"])
ok(left["left_because"]["kind"] == "stopped" and "last fix round" in left["left_because"]["text"],
   f"what's left says why: {left['left_because']}")
# e) The first read fails: no rounds, the draft is delivered with "Get Studio's read"
pid, r = run_case("Read fails", [edl("h", at("To"))], [RuntimeError("overloaded")])
ok(r["status"] == "ready" and draft_review.state(r) == "failed" and not json.loads(r["self_fix"])["rounds"], "first read fails: delivered, no rounds")
# f) The round's read fails: the version before stays
pid, r = run_case("Round read fails", [edl("h", at("To")), edl("h2", at("To"))], [READ_A, RuntimeError("overloaded")])
ok(r["video_path"].endswith("_v1.mp4") and whats(draft_review.current(r)) == whats(READ_A),
   "round's read fails: the first render and its read stay")
# g) Re-render (subtitle fix): Studio's read, no rounds, not counted for the trend
n_logs = conn.execute("select count(*) from self_fix_log").fetchone()[0]
CALLS.clear(); WATCH["pid"] = pid
QUEUE["review"][:] = [READ_A]
editor.rerender(conn, pid, find_track=fake_track)
r = row(pid)
ok([c["kind"] for c in CALLS] == ["review"] and r["status"] == "ready" and r["version"] == 2, "re-render: a read, no rounds, v2")
ok(conn.execute("select count(*) from self_fix_log").fetchone()[0] == n_logs, "re-render not counted in the trend")

# h) The worker restarts during Studio's rounds: the rendered draft goes to review, not edited again
conn.execute("update productions set status='producing', self_fix=? where id=?", (json.dumps({"version": r["version"], "pending": True}), pid))
conn.commit()
QUEUE["edit"][:] = []; QUEUE["review"][:] = []
worker.run_editor(stop_after_one=True)
r2 = row(pid)
ok(r2["status"] == "ready" and r2["version"] == r["version"], "restart mid-rounds: the rendered draft goes to review as it is")

# ═══ 4. Carousels: the same loop (real Chrome layout) ════════════════════════
print("\n== 4. Carousel self-fix")
import carousel
iid = conn.execute("INSERT INTO ideas (batch, title, brand, format, status, post_type, created_at) VALUES ('t', 'Floor levels', 'urban_makers', 'carousel', 'new', 'knowhow', ?)",
                   (catalog.now(),)).lastrowid
conn.commit()
slides = [{"role": "cover", "heading": "Uneven floors at home?", "body": "", "image": {"source": "text"}},
          {"role": "point", "heading": "Why floors differ", "body": "Old tiles were laid on thick screed, so one room sits higher than the next and leaves a step at the doorway that people trip on every day.", "image": {"source": "text"}},
          {"role": "point", "heading": "The overlay fix", "body": "We lay new tiles over the old ones.", "image": {"source": "text"}},
          {"role": "ask", "heading": "Save this for your reno", "body": "", "image": {"source": "text"}}]
cpid = carousel.make(conn, iid, slides, "A caption")
WATCH["pid"] = cpid; CALLS.clear(); NOTIFIED.clear()
def cslide(role, heading, body, keep):
    return {"role": role, "heading": heading, "body": body, "keep_image": keep, "clip_id": 0, "at": 0, "image_idea": "",
            "concept": False, "redraw": "x" if keep == 3 else ""}
CREAD1 = {"verdict": "Nearly ready: 2 things to fix", "scores": {"cover": "fix", "legibility": "fix", "spelling": "good", "claims": "good", "ask": "good"},
          "fixes": [{"what": "Shorten slide 2's text to two lines", "why": "Too long on a phone", "slide": 2, "area": "legibility",
                     "editor_can_fix": True, "rule": "Keep a slide's body to two short lines", "same_rule": 0},
                    {"what": "Make the cover a promise", "why": "Stops the scroll", "slide": 1, "area": "cover",
                     "editor_can_fix": True, "rule": "Make the cover a promise, not a question", "same_rule": 0},
                    {"what": "Add a real photo of a doorway step", "why": "Real work", "slide": 3, "area": "picture",
                     "editor_can_fix": False, "rule": "", "same_rule": 0}], "works": ["Clear"]}
CREAD2 = {"verdict": "Nearly ready: 1 thing to fix", "scores": {k: "good" for k in carousel.draft_review.CAROUSEL_SCORES} if hasattr(carousel, "draft_review") else
          {"cover": "good", "legibility": "good", "spelling": "good", "claims": "good", "ask": "good"},
          "fixes": [CREAD1["fixes"][2]], "works": ["Clear"]}
QUEUE["carousel_review"] += [CREAD1, CREAD2]
QUEUE["carousel_revise"] += [{"slides": [cslide("cover", "Level floors without ripping up tiles", "", 1),
                                         cslide("point", "Why floors differ", "Old screed was thick. One room ends up higher.", 2),
                                         cslide("point", "The overlay fix", "We lay new tiles over the old ones.", 3),
                                         cslide("ask", "Save this for your reno", "", 4)],
                              "caption_changed": False, "caption": "", "summary": "Shorter slide 2, a promise on the cover.",
                              "notes_addressed": [{"note_id": 1, "how": "shortened", "kind": "editing", "rule": "", "same_rule": 0},
                                                  {"note_id": 2, "how": "new cover", "kind": "editing", "rule": "", "same_rule": 0}]}]
worker.run_editor(stop_after_one=True)
r = row(cpid)
data = carousel.current(r)
ok(r["status"] == "ready" and r["version"] == 1, f"carousel v1 ready ({r['status']})")
ok([c["kind"] for c in CALLS] == ["carousel_review", "carousel_revise", "carousel_review"] and all(c["status"] == "producing" for c in CALLS),
   f"read -> revise -> read, all while 'producing': {[(c['kind'], c['status']) for c in CALLS]}")
ok("Studio's own read of these slides" in CALLS[1]["text"] and "Add a real photo" not in CALLS[1]["text"], "only the editor-fixable points sent")
ok(data["slides"][0]["heading"].startswith("Level floors") and len(carousel.files(r)) == 4
   and "_fix1" in str(carousel.files(r)[0].parent), f"Bryan's v1 is the fixed layout ({carousel.files(r)[0].parent.name})")
ok(not (carousel.RENDERS / f"carousel_{cpid}_v1").exists(), "the first layout's folder removed")
ok(not data["slides"][2]["image"].get("redraw") and data["slides"][2]["image"]["source"] == "text", "no picture redraw in Studio's own round")
sf = json.loads(r["self_fix"])
ok(len(sf["fixed"]) == 2 and len(draft_review.current(r)["fixes"]) == 1, "2 fixed itself, 1 left for Bryan")
ok({x["text"] for x in learning.active("carousel")} >= {"Keep a slide's body to two short lines.", "Make the cover a promise, not a question."},
   "carousel rules learned")
ok(NOTIFIED == [("draft", "ready")], f"notified once: {NOTIFIED}")
cu = conn.execute("select kind from usage where ref = ?", (self_fix.ref(cpid),)).fetchall()
ok(sorted(x["kind"] for x in cu) == ["carousel", "review"], "carousel round's cost recorded apart")
import dashboard
with app.test_request_context(f"/api/production/{cpid}"):
    flask.g.user = {"id": 1, "role": "owner", "name": "Bryan"}
    cj = dashboard.production_detail(cpid).get_json()
ok(cj["self_fix"]["fixed"] and cj["self_fix_cost"] > 0 and cj["rules"]["learned"], "carousel Review data: fixed list, cost, rules")
finish()
