"""Studio's own fix rounds make what its read finds, as patches (fix_actions.py), judged fix by fix,
under a cost cap, with a caption spell-check and the trade glossary.

Real speech (cuts/media, flite, known word edges), real ffmpeg + HyperFrames renders for part 1;
quick fake renders for the loop's edge cases. Claude is stubbed (llm.final_message) and its token
counts estimated from the prompt sizes (about 3.6 characters a token, each still ~620 tokens), so
the cost lines are realistic. The read is a stub that LOOKS at the draft (its captions, words,
cards, hook), so a fix is "gone on the re-read" only when the draft really changed.
Run: python tests/quality/suite_patches.py [--skip-real] (from studio/)"""
import json, os, shutil, subprocess, sys, time, wave
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as HERE, ok, finish, results
import media
REAL = "--skip-real" not in sys.argv
CUTS, MEDIA = HERE, HERE / "media"
media.cuts_speech(CUTS / "media"); media.music_bed(MEDIA / "music.mp3")
os.environ.update(ANTHROPIC_API_KEY="test",
                  EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers", TELEGRAM_BOT_TOKEN="",
                  STUDIO_SUPABASE_URL="", STUDIO_SUPABASE_SERVICE_KEY="",
                  STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0", STUDIO_EDIT_SCENES="0")
for k in ("STUDIO_SELF_FIX_ROUNDS", "STUDIO_SELF_FIX_MAX_USD", "STUDIO_SPELL_CHECK"):
    os.environ[k] = ""
import settings, catalog, extract, editor, draft_review, llm, learning, self_fix, usage, music, worker, notify
import fix_actions, spelling, glossary, transcribe, carousel

settings.ensure_folders(); conn = catalog.connect()

ok(settings.SELF_FIX_ROUNDS == 3 and settings.SELF_FIX_MAX_USD == 1.5 and settings.SPELL_CHECK,
   f"defaults: 3 rounds, US$1.50 cap, spell-check on ({settings.SELF_FIX_ROUNDS}, {settings.SELF_FIX_MAX_USD})")

# ── The clip: real speech, Whisper's words with a mis-hearing (MCSC) and a cut-short stutter (excite) ──
data = json.loads((CUTS / "media" / "words.json").read_text())
words = [{k: w[k] for k in ("word", "start", "end")} for w in data["words"]]
words[1]["word"] = "MCSC"            # "This MCSC is really comfortable." (Whisper mis-heard MCST)
words[27]["word"] = "excite"         # "This finish excite excites everyone." (a stutter the code doesn't catch)
assert words[28]["word"] == "excites" and words[37]["word"] == "days,"
d = settings.LIBRARY / "2026-10" / "talk"; d.mkdir(parents=True)
v = d / "talk.mp4"; shutil.copy(CUTS / "media" / "talk.mp4", v)
meta = extract.probe(v)
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": "", "words": words, "segments": []}), encoding="utf-8")
cid = catalog.start(conn, "fp-talk", v.name, d, v)
catalog.save_tags(conn, cid, meta, {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "Tiles",
                   "summary": "Bryan on tiles", "topics": [], "setting": "", "usable": True, "quality_notes": "fine",
                   "suggested_hook": "", "moments": []})
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
def W(word, n=0): return [w for w in words if w["word"] == word][n]

def new_production(title):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": title, "hook": "Why tiles crack",
        "angle": "x", "beats": [], "segments": [{"clip_id": cid, "start": 0, "end": data["total"], "purpose": "all"}],
        "caption": "cap", "est_seconds": 25}])
    return catalog.make_production(conn, conn.execute("select max(id) from ideas").fetchone()[0])
def row(pid): return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())

music.find_track = lambda brief, seconds, out_dir, ref="": (MEDIA / "music.mp3", {"title": "Bed", "artist": "ffmpeg", "recording_id": "t1"})
music.allowed_for = lambda b: True
editor.check_words = lambda final, expected, work: {"words_expected": len(expected), "match": 1.0, "missing": []}
notify.digest.add = lambda kind, n=1: None

def cut(a, b, why="kept"): return {"clip_id": cid, "start": a, "end": b, "why": why}
CARD = {"clip_id": cid, "at": W("screed,")["start"] + 0.05, "duration": 2.5, "style": "card", "title": "The *screed* first",
        "lines": ["Two days to dry"], "scene": "", "look": "cutaway", "role": "explain", "why": ""}
def plan(cuts, hook, card_at=None, notes=()):
    return {"cuts": cuts, "hook_text": hook, "key_words": ["screed"],
            "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
            "summary": "Kept the explanation.", "missing_shots": [], "notes_addressed": list(notes),
            "graphics": [{**CARD, **({"at": card_at} if card_at else {})}]}
# The first plan: the MCST line, the finish line (with the stutter), and the screed line cut off before "and then we tile."
EDL1 = plan([cut(0.30, 2.95), cut(14.90, 18.20), cut(18.95, 22.50)], "Floors that *crack*")
# The re-plan (asked only for the re-order): opens on the finish line; the patches before it are kept as asked.
EDL2 = plan([cut(14.90, 15.85), cut(16.80, 18.20), cut(0.30, 2.95), cut(18.95, 24.50)], "Why tiles *crack*",
            card_at=W("pour")["start"] + 0.02,
            notes=[{"note_id": 1, "how": "opened on the finish line", "kind": "editing", "rule": "", "same_rule": 0,
                    "animations": "none"}])

# ── Studio's read, stubbed: it looks at the draft it's given ──────────────────
SCORES = ("hook", "pace", "sound", "captions", "picture")
def A(kind, start=None, end=None, frm="", to="", item=None, field="", amount=None, reason=""):
    return {"type": kind, "start": start, "end": end, "from": frm, "to": to, "item": item, "field": field,
            "amount": amount, "reason": reason}
def F(what, area, start=None, end=None, action=None, can=True, when="this_draft", rule="", for_bryan=""):
    return {"what": what, "why": "because", "start": start, "end": end, "area": area, "when": when,
            "kind": "detail" if when == "next_shoot" else "none", "editor_can_fix": can, "rule": rule, "same_rule": 0,
            "illustrate": "", "action": action or A("none"), "for_bryan": for_bryan}
FOOTAGE = F("Film a close-up of the cracked tile", "picture", can=False, when="next_shoot", for_bryan="footage")
TASTE = F("Consider a warmer music track", "sound", can=False, for_bryan="taste")
def look(pid):
    r = row(pid)
    edl = json.loads(r["edl"])
    tl = editor.timeline(edl["cuts"], CL)
    return r, edl, tl
def dynamic_read(pid):
    r, edl, tl = look(pid)
    total = r["duration"]
    said = [w["word"] for w in tl]
    fixes = []
    mc = next((w for w in tl if w["word"].strip(".,") == "MCSC"), None)
    if mc:
        fixes.append(F("Correct the caption 'MCSC' to 'MCST'", "captions", mc["start"], mc["end"],
                       A("caption_word", mc["start"], frm="MCSC", to="MCST"), rule="Spell trade acronyms the way the glossary does"))
    ex = next((w for w in tl if w["word"] == "excite"), None)
    if ex:
        fixes.append(F("Cut the stutter 'excite excites'", "pace", ex["start"], ex["end"] + 0.5,
                       A("drop_words", ex["start"], ex["end"] + 0.5, frm="excite excites"), rule="Cut a stutter down to the word said once"))
    if not said[-1].endswith("tile."):
        fixes.append(F("Finish the last sentence: bring back 'and then we tile'", "story", total, None,
                       A("restore_words", total, frm="and then we tile"), rule="End on a finished sentence, never mid-thought"))
    card = next(g for g in edl["graphics"] if g.get("style") == "card")
    pour = next((w for w in tl if w["word"] == "pour"), None)
    if pour and abs(card["start"] - pour["start"]) > 0.4:
        fixes.append(F("Show the screed card when Bryan says 'pour'", "picture", card["start"], None,
                       A("graphic", pour["start"], item=1, field="time", amount=2.5), rule="Time a card to the words it shows"))
    if edl["hook_text"] != "Why tiles crack":
        fixes.append(F("Reword the hook to 'Why tiles crack'", "hook", 0.0, 2.5, A("hook_text", to="Why tiles *crack*")))
    if said[:2] == ["This", "MCSC"] or (said[0] == "This" and said[1] != "finish"):
        fixes.append(F("Open on 'This finish excites everyone' instead", "hook", 0.0, None,
                       A("replan", reason="re-order the opening")))
    fixes = (fixes + [FOOTAGE, TASTE])[:5]
    fixable = [f for f in fixes if f["editor_can_fix"]]
    areas = {f["area"] for f in fixable}
    return {"verdict": f"Nearly ready: {len(fixes)} things to fix" if fixes else "Ready to post",
            "scores": {k: "fix" if k in areas else "good" for k in SCORES}, "fixes": fixes,
            "works": ["Clear explanation"]}

# ── Claude, stubbed, with token counts from the prompt sizes ──────────────────
CALLS, QUEUE = [], {"edit": [], "review": [], "spell": [], "join": [], "carousel_review": [], "carousel_revise": []}
WATCH = {"pid": None, "read": None}
OUT_TOKENS = {"edit": 6000, "review": 1500, "spell": 300, "join": 300, "carousel_review": 1500, "carousel_revise": 3000}
def kind_of(req):
    sysp = req.get("system", "")
    for k, mark in (("edit", "You are the editor for Bryan's Instagram Reels"), ("review", "You review draft Instagram Reels"),
                    ("spell", "You check the captions of an Instagram Reel"), ("join", "You check a Reel's cut for broken sentences"),
                    ("carousel_review", "You review draft Instagram carousels"), ("carousel_revise", "You revise a draft Instagram carousel")):
        if mark in sysp:
            return k
    raise AssertionError("unexpected call: " + sysp[:60])
def fake_final(client, **req):
    k = kind_of(req)
    content = req["messages"][0]["content"]
    text = "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text") if isinstance(content, list) else content
    images = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image") if isinstance(content, list) else 0
    tokens_in = int((len(req.get("system", "")) + len(text)) / 3.6) + images * 620
    CALLS.append({"kind": k, "text": text, "system": req.get("system", ""), "in": tokens_in, "model": req.get("model")})
    if k == "review" and WATCH["read"] and not QUEUE["review"]:
        answer = WATCH["read"](WATCH["pid"])
    elif QUEUE[k]:
        answer = QUEUE[k].pop(0)
    elif k == "spell":
        answer = {"fixes": []}
    elif k == "join":
        answer = {"joins": [{"id": i + 1, "reads_complete": True, "restore": []} for i in range(text.count("join "))]}
    else:
        raise AssertionError(f"no answer queued for {k}")
    if isinstance(answer, Exception):
        raise answer
    return NS(content=[NS(type="text", text=json.dumps(answer))], stop_reason="end_turn", model=req.get("model") or "claude-opus-5-5",
              usage=NS(input_tokens=tokens_in, output_tokens=OUT_TOKENS[k], cache_read_input_tokens=0, cache_creation_input_tokens=0))
llm.final_message = fake_final
def kinds(): return [c["kind"] for c in CALLS]
def cost_of(pid, kind=None):
    q = "select coalesce(sum(cost_usd),0) from usage where (ref = ? or ref like ?)" + (" and kind = ?" if kind else "")
    return conn.execute(q, (f"production:{pid}", f"production:{pid}:%", *([kind] if kind else []))).fetchone()[0]

def run(title, read=None, edits=(), reviews=(), rounds=None, cap=None, spell=()):
    CALLS.clear()
    pid = new_production(title); WATCH.update(pid=pid, read=read)
    QUEUE["edit"][:] = list(edits); QUEUE["review"][:] = list(reviews); QUEUE["spell"][:] = list(spell)
    old = (settings.SELF_FIX_ROUNDS, settings.SELF_FIX_MAX_USD)
    if rounds is not None: settings.SELF_FIX_ROUNDS = rounds
    if cap is not None: settings.SELF_FIX_MAX_USD = cap
    t0 = time.time()
    try:
        worker.run_editor(stop_after_one=True)
    finally:
        settings.SELF_FIX_ROUNDS, settings.SELF_FIX_MAX_USD = old
    print(f"  ({time.time() - t0:.0f}s, calls: {kinds()})")
    return pid, row(pid)

# ═══ 0. Units: the read's actions, fix matching, the glossary, the spell-check's guard ═══
print("\n== 0. Units")
dirty = draft_review._clean({"verdict": "Nearly ready", "scores": {}, "works": [], "fixes": [
    {**F("Correct 'MCSC'", "captions", 1.0, None, A("caption_word", 99.0, frm="MCSC", to="MCST")), "for_bryan": "taste"},
    {**F("Something", "pace", 2.0, None, A("nonsense"))},
    F("A judgement call", "story", None, None, A("hook_text", to="x"), can=False, for_bryan="message")]}, 30.0, {})
f1, f2, f3 = dirty["fixes"]
ok(f1["action"]["type"] == "caption_word" and f1["action"]["start"] == 30.0 and f1["for_bryan"] == "",
   "an action's times are clamped to the draft; an editor fix isn't Bryan's")
ok(f2["action"]["type"] == "replan" and f2["action"]["reason"], "an editor fix with no usable action falls back to a re-plan")
ok(f3["action"]["type"] == "none" and f3["for_bryan"] == "message", "Bryan's fix: no action, and why it's his")
ok(self_fix.same_fix(F("Correct the caption 'MCSC' to 'MCST'", "captions", 1.2, None, A("caption_word", 1.2, frm="MCSC", to="MCST")),
                     F("Fix the misspelt acronym MCSC in the captions", "captions", 1.0, None, A("caption_word", 1.0, frm="MCSC.", to="MCST"))),
   "the same fix, worded differently by a fresh read, is matched")
ok(not self_fix.same_fix(F("Correct the caption 'MCSC' to 'MCST'", "captions", 1.2, None, A("caption_word", 1.2, frm="MCSC", to="MCST")),
                         F("Correct the caption 'grounds' to 'grout'", "captions", 1.5, None, A("caption_word", 1.5, frm="grounds", to="grout"))),
   "a different caption word close by is a different fix")
ok(not self_fix.same_fix(F("Trim the pause", "pace", 3.0), F("Trim the pause", "pace", 12.0)), "the same words at another moment are a different fix")
ok(glossary.known("mcst") and glossary.known("Lamitak") and glossary.PATH.exists(), "the glossary is seeded (MCST, brands) in Studio/glossary.txt")
brief = {"tiles", "crack"}
ok(spelling.safe("MCSC", "MCST", brief) == "MCST", "spell-check guard: a glossary term is trusted")
ok(spelling.safe("two", "2", brief) is None and spelling.safe("15mm", "50mm", brief) is None, "never a number")
ok(spelling.safe("Bryan", "Brian", brief) is None and spelling.safe("sofa", "Sophia", brief) is None, "never a new name")
ok(spelling.safe("grounds", "grout", brief) == "grout", "a mis-heard trade word -> the glossary's")
ok(spelling.safe("waterprofing", "waterproofing", brief) == "waterproofing", "a misspelling")
ok(spelling.safe("cracks", "breaks", brief) is None, "never a change of meaning")

# ═══ 1. The whole loop on a real draft (real renders) ═══
if REAL:
    print("\n== 1. Studio's read finds 5 + 1 + 2; patches fix 5 in one round, the re-plan the re-order, 2 left for Bryan")
    pid, r = run("Why tiles crack", read=dynamic_read, edits=[EDL1, EDL2])
    sf = json.loads(r["self_fix"])
    rounds = sf["rounds"]
    ok(r["status"] == "ready" and r["version"] == 1, f"v1 ready ({r['status']})")
    ok([x["how"] for x in rounds] == ["patch", "replan"] and all(x["kept"] for x in rounds),
       f"round 1 patches, round 2 re-plans: {[(x['how'], x['kept'], x.get('why')) for x in rounds]}")
    i1, i2 = [i for i, c in enumerate(CALLS) if c["kind"] == "review"][:2]
    ok("edit" not in kinds()[i1:i2], f"no edit plan in the patch round ({kinds()[i1:i2]})")
    ok(kinds().count("edit") == 2, "one re-plan in all (plus the first plan)")
    res = rounds[0]["results"]
    # Since 2026-10-21 the editor finishes a last sentence left short by itself (sentence_guard), so the read
    # no longer needs restore_words for "and then we tile": four patches, or five on an older draft.
    ok(len(res) in (4, 5) and all(x["ok"] for x in res), "every action applied: " + "; ".join(x.get("did") or x.get("why") for x in res))
    ok(rounds[0]["fixed"] == len(res), f"all {len(res)} gone on the re-read")
    replan_call = [c for c in CALLS if c["kind"] == "edit"][1]
    ok("Open on 'This finish excites everyone'" in replan_call["text"] and "MCSC" not in replan_call["text"].split("found these problems")[-1],
       "the re-plan was asked only for what no patch can do")
    r, edl, tl = look(pid)
    said = [w["word"] for w in tl]
    ok("MCST" in said and "MCSC" not in said, "captions say MCST (through the re-plan too)")
    ok("excite" not in said and said.count("excites") == 1, "the stutter is gone")
    ok(said[-1] == "tile.", f"the draft ends on the finished sentence ({said[-4:]})")
    ok(said[:2] == ["This", "finish"], f"opens on the finish line ({said[:3]})")
    ok(edl["hook_text"] == "Why tiles crack" and edl["hook_italic"] == "crack", "the hook reworded, its italic word kept apart")
    chunks = editor.caption_chunks(editor.timeline(edl["cuts"], CL), r["duration"])
    ok(any("MCST" in c["text"] for c in chunks) and not any("MCSC" in c["text"] for c in chunks),
       "the captions (and the Resolve project, which reads the same) show MCST")
    ok(any(c.get("say") for c in edl["cuts"]) and edl.get("corrections"), "the correction is kept on the edit plan, by clip")
    qa = json.loads(r["qa"])
    ok(qa["joins"]["checked"] >= 3, f"the clipped-word check ran on the fixed draft ({qa['joins']})")
    rv = draft_review.current(r)
    whats = [f["what"] for f in rv["fixes"]]
    ok(whats == [FOOTAGE["what"], TASTE["what"]], f"only the 2 for Bryan are left: {whats}")
    lb = {f["what"]: f["left_because"] for f in rv["fixes"]}
    ok(lb[FOOTAGE["what"]]["kind"] == "footage" and "film it next time" in lb[FOOTAGE["what"]]["text"], f"footage: {lb[FOOTAGE['what']]}")
    ok(lb[TASTE["what"]] == {"kind": "yours", "text": "Your call: it's a matter of taste."}, f"taste: {lb[TASTE['what']]}")
    learned = {x["text"] for x in learning.active("reel")}
    ok("Spell trade acronyms the way the glossary does." in learned and "Cut a stutter down to the word said once." in learned,
       f"rules from the patched fixes learned: {sorted(learned)}")
    sm = self_fix.summary(r)
    ok(len(sm["fixed"]) >= 6 and sm["rounds"] == 2, f"Review: Studio fixed {len(sm['fixed'])} things in {sm['rounds']} rounds")
    usd_after = sf["usd"]
    ok(0 < usd_after <= settings.SELF_FIX_MAX_USD and "US$" in sm["cost_line"], f"cost line: {sm['cost_line']}")
    spells = [c for c in CALLS if c["kind"] == "spell"]
    ok(spells and "MCST" in spells[0]["text"].split("Brief:")[0] and "MCSC" in spells[0]["text"],
       "the spell-check saw the glossary (MCST) and the caption words (MCSC)")
    ok(len(spells) <= 2, f"words already checked aren't sent again ({len(spells)} spell-checks over 3 renders)")
    files = sorted(p.name for p in editor.RENDERS.glob(f"production_{pid}_v1*.mp4") if ".light" not in p.name)
    ok(files == [f"production_{pid}_v1_fix2.mp4"], f"one file left, the delivered one ({files})")
    # The cost, before -> after (same stub token counts): before, every round was a full re-plan and the
    # caption word, the stutter and the restored ending had no tool, so they stayed.
    per = {k: conn.execute("select avg(cost_usd) from usage where kind = ? and ref like ?", (k, f"production:{pid}%")).fetchone()[0]
           for k in ("edit", "review")}
    spell_usd = sum(usage.cost(c["model"], c["in"], OUT_TOKENS["spell"]) for c in spells)
    plan_usd = max(usage.cost("claude-opus-5-5", c["in"], OUT_TOKENS["edit"]) for c in CALLS if c["kind"] == "edit")
    read_usd = usage.cost("claude-opus-5-5", [c for c in CALLS if c["kind"] == "review"][0]["in"], OUT_TOKENS["review"])
    before = read_usd + 2 * (plan_usd + read_usd)
    print(f"  COST per draft (read + fix rounds + spell-check), stub tokens: before (2 re-plan rounds) ~US${before:.2f}, "
          f"after US${usd_after:.2f} (read US${read_usd:.3f}, re-plan US${plan_usd:.3f}, spell-checks US${spell_usd:.3f})")
    print(f"  A draft whose fixes all patch: before ~US${read_usd + plan_usd + read_usd:.2f} (1 re-plan round), "
          f"after ~US${read_usd + read_usd + spell_usd:.2f} (1 patch round)")
    print(f"  self_fix: {[(x['round'], x['how'], x['kept'], x.get('fixed')) for x in rounds]}, left {sf['left']}, stopped {sf['stopped']}")

# ═══ 2..6: the loop's edge cases (quick fake renders) ═══
print("\n== 2. Edge cases (fake renders)")
small = media.small_clip(MEDIA / "small.mp4")
editor.render_cut = lambda cuts, clips, out: sum(c["end"] - c["start"] for c in cuts)
editor.render_overlay = lambda *a, **k: None
def fake_mix(cut_, overlay, music_file, volume_db, total, out, words=(), **k):
    shutil.copy(small, out); return {"gap_db": 20.0, "voice_gain_db": 2.0}
editor.mix_final = fake_mix
editor.check_loudness = lambda f: {"lufs": -14.0, "true_peak": -2.0}
editor.clipped_words = lambda cuts, clips: []
editor.bright_spans = lambda video: []
import previews
previews.for_draft = lambda path: None

# a) The spell-check catches MCSC before the read: the read never sees it. Its untrusted answers are dropped.
mcsc = 1  # the word's number in the draft (the first cut starts at "This")
pid, r = run("Spell-check", read=dynamic_read, edits=[EDL1], rounds=0,
             spell=[{"fixes": [{"index": mcsc, "count": 1, "to": "MCST", "why": "glossary"},
                               {"index": 0, "count": 1, "to": "Brian", "why": "name"}]}])
r, edl, tl = look(pid)
said = [w["word"] for w in tl]
ok(said[1] == "MCST" and said[0] == "This", f"spell-check: MCST in, the untrusted 'Brian' left out ({said[:3]})")
qa = json.loads(r["qa"])
ok(any("corrected 1 caption word Whisper mis-heard (\"MCST\")" in c["did"] for c in qa["checks"]), "the draft's checks say so")
ok(not any(f["action"]["type"] == "caption_word" for f in draft_review.current(r)["fixes"]), "the read had nothing to correct")
CALLS.clear(); QUEUE["review"][:] = []
editor.rerender(conn, pid)
ok("spell" not in kinds(), "a re-render of the same words doesn't spell-check again (free)")

# b) A patch that can't be made goes to the re-plan; the reason is kept.
BAD = F("Correct the caption 'Zorbex' to 'Sorbex'", "captions", 1.0, None, A("caption_word", 1.0, frm="Zorbex", to="Sorbex"))
CLEAN = {"verdict": "Ready to post", "scores": {k: "good" for k in SCORES}, "fixes": [FOOTAGE], "works": ["ok"]}
pid, r = run("Patch can't", edits=[EDL1, EDL2], reviews=[{**CLEAN, "verdict": "Nearly ready: 2", "fixes": [BAD, FOOTAGE]}, CLEAN])
sf = json.loads(r["self_fix"])
ok([(x["how"], x["kept"], bool(x.get("skipped"))) for x in sf["rounds"]] == [("patch", False, True), ("replan", True, False)],
   f"patch couldn't be made (no render, no read, round not counted), then the re-plan made it: {[(x['how'], x.get('skipped'), x['kept']) for x in sf['rounds']]}")
ok("couldn't find \"Zorbex\"" in sf["rounds"][0]["results"][0]["why"], f"why: {sf['rounds'][0]['results'][0]['why']}")
ok(kinds().count("review") == 2, "one read for the first draft, one for the re-plan")

# c) A round that makes things worse is dropped; then the re-plan; then Studio tried and couldn't.
HOOK = F("Reword the hook to 'Why tiles crack'", "hook", 0.0, 2.5, A("hook_text", to="Why tiles *crack*"))
R1 = {"verdict": "Nearly ready: 2", "scores": {**{k: "good" for k in SCORES}, "hook": "fix"}, "fixes": [HOOK, FOOTAGE], "works": []}
WORSE = {"verdict": "Needs work: the hook now reads as clickbait", "scores": {**{k: "good" for k in SCORES}, "hook": "fix"},
         "fixes": [F("Make the hook say what the post gives", "hook", 0.0, 2.5, A("hook_text", to="How tiles *crack*")), FOOTAGE], "works": []}
pid, r = run("Worse", edits=[EDL1, EDL1], reviews=[R1, WORSE, R1])
sf = json.loads(r["self_fix"])
ok([(x["how"], x["kept"]) for x in sf["rounds"]] == [("patch", False), ("replan", False)] and "needs work" in sf["rounds"][0]["why"].lower(),
   f"dropped: {[(x['how'], x['kept'], x.get('why')) for x in sf['rounds']]}")
r, edl, tl = look(pid)
ok(edl["hook_text"] == "Floors that crack" and r["video_path"].endswith(f"production_{pid}_v1.mp4"), "the first version stays")
lb = next(f for f in draft_review.current(r)["fixes"] if f["what"] == HOOK["what"])["left_because"]
ok(lb["kind"] == "tried" and "a quick fix made something else worse" in lb["text"] and "a re-edit didn't change" in lb["text"],
   f"left for Bryan, saying what Studio tried: {lb['text']}")

# d) The cost cap: when the next round would pass it, Studio stops and says so.
pid, r = run("Cap", read=dynamic_read, edits=[EDL1], cap=0.06)
sf = json.loads(r["self_fix"])
ok(sf["stopped"] == "cap" and not sf["rounds"], f"no rounds past the cap (spent US${sf['usd']:.3f} of US$0.06)")
rv = draft_review.current(r)
lbs = [f["left_because"] for f in rv["fixes"] if f["editor_can_fix"]]
ok(lbs and all(x["kind"] == "stopped" and "US$0.06 limit" in x["text"] for x in lbs), f"each says why: {lbs[0]}")
ok("stopped fixing at that limit" in self_fix.summary(r)["cost_line"], self_fix.summary(r)["cost_line"])

# e) Rounds stop at the setting, and when nothing changed.
pid, r = run("Off", read=dynamic_read, edits=[EDL1], rounds=0)
ok(not json.loads(r["self_fix"])["rounds"] and all(f["left_because"]["text"] == "Studio's own fixes are turned off."
                                                   for f in draft_review.current(r)["fixes"] if f["editor_can_fix"]), "0 rounds: off, and it says so")

# ═══ 7. The glossary reaches Whisper ═══
print("\n== 7. Whisper gets the glossary")
seen = {}
class FakeModel:
    def detect_language(self, audio, **kw): return "en", 0.99, [("en", 0.99), ("zh", 0.01)]
    def transcribe(self, audio, **kw):
        seen.update(kw)
        return iter([NS(start=0.0, end=1.0, text=" MCST meeting", avg_logprob=-0.2,
                        words=[NS(start=0.0, end=0.5, word=" MCST"), NS(start=0.5, end=1.0, word=" meeting")])]), NS(language="en", language_probability=0.99)
wav = HERE / "tone.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(wav)], check=True)
transcribe._model, transcribe._device = FakeModel(), "cpu"
out = transcribe._transcribe_once(wav)
ok("MCST" in (seen.get("hotwords") or "") and "HDB" in seen["hotwords"] and len(seen["hotwords"]) <= glossary.HOTWORDS_CHARS,
   f"hotwords to faster-whisper: {seen.get('hotwords', '')[:70]}...")
ok(out["words"][0]["word"] == "MCST", "transcript as before")
(glossary.PATH).write_text(glossary.PATH.read_text() + "\nZorbex\nM C S T -> MCST\n", encoding="utf-8")
ok("Zorbex" in glossary.hotwords() and glossary.fixes()[glossary.key("M C S T")] == "MCST", "Bryan's own lines are read (no restart)")
fx = spelling.glossary_fixes([{"word": "M", "clip_id": 1, "src": 1.0}, {"word": "C", "clip_id": 1, "src": 1.1},
                              {"word": "S", "clip_id": 1, "src": 1.2}, {"word": "T.", "clip_id": 1, "src": 1.3}])
ok([(c["to"]) for c in fx] == ["MCST.", "", "", ""], f"his 'wrong -> right' line corrects the captions by code ({[c['to'] for c in fx]})")

# ═══ 8. Carousels: slide_text patches, no Claude rewrite ═══
print("\n== 8. Carousel patch")
iid = conn.execute("INSERT INTO ideas (batch, title, brand, format, status, post_type, created_at) VALUES ('t', 'Tiles', 'urban_makers', 'carousel', 'new', 'knowhow', ?)",
                   (catalog.now(),)).lastrowid
conn.commit()
slides = [{"role": "cover", "heading": "Why tiles crack", "body": "", "image": {"source": "text"}},
          {"role": "point", "heading": "The screed", "body": "Old screed was laid too thin, so the tiles above it crack when the floor flexes under weight every day.", "image": {"source": "text"}},
          {"role": "ask", "heading": "Save this for your reno", "body": "", "image": {"source": "text"}}]
cpid = carousel.make(conn, iid, slides, "A caption")
CALLS.clear(); WATCH.update(pid=cpid, read=None)
CR1 = {"verdict": "Nearly ready: 1 thing to fix", "scores": {k: "good" for k in draft_review.CAROUSEL_SCORES},
       "fixes": [{"what": "Shorten slide 2's text to two lines", "why": "Too long", "slide": 2, "area": "legibility",
                  "editor_can_fix": True, "rule": "Keep a slide's body to two short lines", "same_rule": 0,
                  "action": A("slide_text", item=2, field="body", to="Thin screed flexes. The tiles above it crack."),
                  "for_bryan": ""}], "works": ["Clear"]}
QUEUE["carousel_review"][:] = [CR1, {**CR1, "verdict": "Ready to post", "fixes": []}]
worker.run_editor(stop_after_one=True)
r = row(cpid)
sf = json.loads(r["self_fix"])
ok(kinds() == ["carousel_review", "carousel_review"], f"read -> patch -> read, no rewrite call ({kinds()})")
ok(carousel.current(r)["slides"][1]["body"] == "Thin screed flexes. The tiles above it crack." and sf["rounds"][0]["how"] == "patch"
   and sf["rounds"][0]["kept"], "slide 2's body changed and kept")
ok(r["status"] == "ready" and len(carousel.files(r)) == 3 and "_fix1" in str(carousel.files(r)[0].parent), "laid out again as v1")

finish()
