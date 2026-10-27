"""Edit words (transcript_edit.py; Bryan, 2026-10-27: "I didn't mean to say SIDs, I meant to say 'as IDs'"): the
corrected transcript of a Reel waiting for review becomes caption corrections and the next version, with the same cut.

Part 0, units: the word diff and how it maps onto the words' times (a word replaced, one split in two, two joined,
case, a word taken out or put in, spaces and new lines ignored, Chinese by character), what is refused and why (a
new sentence, a phrase, most of the words, across two shots), the terms Remember this word keeps, the glossary's add.
Part 1, a real draft (real speech from media.py, real ffmpeg + HyperFrames renders): v1 through the editor with a
stubbed Claude; the team corrects three words through Review's route; the editor makes v2 with no Claude call and
no cost, the cut and the clock exactly as v1's, the captions saying the corrected words over the old words' times,
Studio's read of v1 carried over minus the caption fix the correction made, the glossary told once. Then what is
refused: not waiting for review, an old version, a carousel, nothing changed.
Run: python tests/quality/suite_transcript_edit.py (from studio/)"""
import json, os, shutil, sys, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as HERE, ok, finish, STUDIO
import media
CUTS, MEDIA = HERE, HERE / "media"
media.cuts_speech(CUTS / "media"); media.music_bed(MEDIA / "music.mp3")
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  TELEGRAM_BOT_TOKEN="", STUDIO_SUPABASE_URL="", STUDIO_SUPABASE_SERVICE_KEY="",
                  STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0", STUDIO_EDIT_SCENES="0", STUDIO_SFX="0",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_SPELL_CHECK="1")
import settings, catalog, extract, editor, draft_review, llm, usage, music, worker, notify, glossary, restyle
import transcript_edit as te

settings.ensure_folders(); conn = catalog.connect()
(settings.ROOT / "style_release.txt").write_text(restyle.RELEASE + "\n")  # no release restart in this test


def T(text, clip=1, gap=0.4):
    """Words on a made-up clock (for the units)."""
    out, t = [], 0.0
    for w in text.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2), "clip_id": clip, "src": round(10 + t, 2)})
        t += gap
    return out


def changes(words, text):
    return [(c["from"], c["to"]) for c in te.diff(words, text)]


def refused(words, text):
    try:
        te.diff(words, text)
        return ""
    except te.Refused as e:
        return str(e)


# ═══ 0. Units ═══
print("\n== 0. The diff and its corrections")
w = T("We use SIDs to track every thing in the mcst office then we tile.")
base = te.editable(w)
ok(base == "We use SIDs to track every thing in the mcst office then we tile.", "the box starts with the captions' words")
ok(te.diff(w, base) == [] and te.diff(w, "We  use SIDs\nto track every thing   in the mcst office then we tile.  ") == [],
   "spaces and new lines are never a change")
c = te.diff(w, base.replace("SIDs", "as IDs"))
ok([(x["first"], x["last"], x["from"], x["to"]) for x in c] == [(2, 2, "SIDs", "as IDs")], f"one word into two: {c}")
fx = te.corrections(w, c)
ok(fx == [{"clip_id": 1, "at": w[2]["src"], "from": "SIDs", "to": "as IDs", "by": "review"}],
   f"one correction on SIDs' own time in its clip ({fx})")
c = te.diff(w, base.replace("every thing", "everything"))
fx = te.corrections(w, c)
ok([(x["at"], x["to"]) for x in fx] == [(w[5]["src"], "everything"), (w[6]["src"], "")],
   f"two into one: the first says it, the second folds into it ({fx})")
cuts = [{"clip_id": 1, "start": 9.9, "end": 16.0}]
say = editor.attach_corrections(cuts, fx)[0]["say"]
ok(say == {f"{w[5]['src']:.2f}": "everything", f"{w[6]['src']:.2f}": ""}, f"on the piece as its say ({say})")
ok(changes(w, base.replace("mcst", "MCST")) == [("mcst", "MCST")], "a change of case is the change")
ok(changes(w, base.replace("office then", "office. Then")) == [("office", "office."), ("then", "Then")],
   "a full stop put in, words the same: allowed")
c = te.diff(w, base.replace("SIDs", "as IDs").replace("every thing", "everything").replace("mcst", "MCST"))
ok([(x["from"], x["to"]) for x in c] == [("SIDs", "as IDs"), ("every thing", "everything"), ("mcst", "MCST")],
   "three changes at once, each on its own words")
ok(changes(w, base.replace("track every", "track")) == [("every", "")], "a word Whisper heard that wasn't said: taken out")
fx = te.corrections(w, te.diff(w, base.replace("track every", "track")))
ok(fx[0]["to"] == "" and fx[0]["at"] == w[5]["src"], "it folds into the word before")
ok(changes(w, base.replace("the mcst", "the new mcst")) == [("mcst", "new mcst")], "a word put in goes on the word after it")
ok(changes(w, base + " Yes") == [("tile.", "tile. Yes")] or "new sentence" in refused(w, base + " Yes."),
   "a word put in at the very end goes on the last word")
ok("new sentence" in refused(w, base + " And then we grout it all."), "a new sentence is refused: an edit, not a correction")
ok("phrase" in refused(w, base.replace("in the", "in the old and dusty")), "a phrase put in is refused")
ok("phrase" in refused(w, base.replace("to track every thing in", "in")), "a phrase taken out is refused")
ok("most of the words" in refused(w, "Totally different words now here okay friend yes please and thank you."),
   "most of the words changed: refused")
ok("empty" in refused(w, "   "), "an empty box is refused")
two = T("This MCSC is", 1) + [{**x, "clip_id": 2, "src": x["src"] + 50} for x in T("really good.", 2)]
ok("two shots" in refused(two, "This MCSC isreally good."), "a change across two shots is refused, saying so")
ok(changes(two, "This MCST is really good.") == [("MCSC", "MCST")], "a change inside one shot of two is fine")
note = refused(w, base + " And then we grout it all.")
ok("Add a note" in note and "“" in note, f"the reason quotes the words and says what to do: {note}")
# Chinese: by character, no spaces in the box.
z = [{"word": x, "start": i, "end": i + 0.5, "clip_id": 3, "src": float(i)} for i, x in enumerate(["我们", "用", "防水", "层", "。"])]
ok(te.editable(z) == "我们用防水层。", f"Chinese in the box without spaces ({te.editable(z)})")
c = te.diff(z, "我们用防水膜。")
ok([(x["first"], x["from"], x["to"]) for x in c] == [(3, "层", "膜")], f"a character changed in Chinese ({c})")
c = te.diff(z, "我们用防水膜层。")
ok([(x["from"], x["to"]) for x in c] == [("层", "膜层")] and te.corrections(z, c)[0]["to"] == "膜层", "a character put in")
ok(te.diff(z, "我们 用 防水 层 。") == [], "Chinese with spaces typed between: no change")
# What Remember this word keeps.
pick = lambda text: te.terms(te.diff(w, text))
ok(pick(base.replace("SIDs", "as IDs")) == ["IDs"], "SIDs -> as IDs remembers IDs, not 'as'")
ok(pick(base.replace("every thing", "everything")) == [], "a join of two words isn't a term")
ok(pick(base.replace("mcst", "MCST")) == ["MCST"], "a case fix of an acronym is")
ok(pick(base.replace("the mcst", "the very mcst")) == [] and pick(base.replace("track every", "track")) == [],
   "a small common word put in, or a word taken out: nothing")
ok(te.terms(te.diff(z, "我们用防潮层。")) == ["防潮"] and te.terms(te.diff(z, "我们用防水膜。")) == [],
   "Chinese: the word as it now reads, two characters or more")
ok(te.terms([{"from": "X", "to": "a", "put": "a"}, {"from": "y", "to": "15", "put": "15"}]) == [], "never one letter or a number")
# The glossary's add.
added = glossary.add(["IDs", "Zorbex panel", "MCST", "ids", "# not this", "a -> b", "x", "This is a whole sentence that goes on and on"])
text = glossary.PATH.read_text(encoding="utf-8")
ok(added == ["IDs", "Zorbex panel"], f"added once each, never a known term, a comment, a mis-hearing line, a letter or a sentence ({added})")
ok(text.count(glossary.ADDED) == 1 and text.rstrip().endswith("IDs\nZorbex panel") and "HDB" in text,
   "under one header at the end; the rest of the file as it was")
ok(glossary.add(["IDs", "Zorbex Panel"]) == [] and glossary.known("ids") and "IDs" in (glossary.hotwords() or ""),
   "the second time nothing; Whisper's hotwords and the spell-check know it")

# ═══ 1. A real draft ═══
print("\n== 1. A real draft: v1, the team corrects three words, v2 with the same cut and no Claude")
data = json.loads((CUTS / "media" / "words.json").read_text())
words = [{k: x[k] for k in ("word", "start", "end")} for x in data["words"]]
assert words[1]["word"] == "sofa" and words[11]["word"] == "joints" and words[29]["word"] == "everyone."
words[1]["word"] = "MCSC"          # "This MCSC is really comfortable."
words[11]["word"] = "SIDs"         # "It is possible to make the SIDs seamless." (he meant "as IDs")
e = words[29]                      # Whisper heard "everyone." as two words
mid = round((e["start"] + e["end"]) / 2, 3)
words[29:30] = [{"word": "every", "start": e["start"], "end": mid}, {"word": "one.", "start": mid, "end": e["end"]}]
d = settings.LIBRARY / "2026-10" / "talk"; d.mkdir(parents=True)
v = d / "talk.mp4"; shutil.copy(CUTS / "media" / "talk.mp4", v)
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": "", "words": words, "segments": []}), encoding="utf-8")
cid = catalog.start(conn, "fp-talk", v.name, d, v)
catalog.save_tags(conn, cid, extract.probe(v), {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "Joints",
                   "summary": "Bryan on joints", "topics": [], "setting": "", "usable": True, "quality_notes": "fine",
                   "suggested_hook": "", "moments": []})
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
def W(word, n=0): return [x for x in words if x["word"] == word][n]
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Seamless joints",
    "hook": "Joints you can't see", "angle": "x", "beats": [], "segments": [{"clip_id": cid, "start": 0, "end": data["total"],
    "purpose": "all"}], "caption": "cap", "est_seconds": 15}])
pid = catalog.make_production(conn, conn.execute("select max(id) from ideas").fetchone()[0])
def row(): return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())

music.find_track = lambda brief, seconds, out_dir, ref="": (MEDIA / "music.mp3", {"title": "Bed", "artist": "ffmpeg", "recording_id": "t1"})
music.allowed_for = lambda b: True
editor.check_words = lambda final, expected, work: {"words_expected": len(expected), "match": 1.0, "missing": []}
notify.digest.add = lambda kind, n=1: None
import previews
previews.for_draft = lambda path: None
CHUNKS = []
real_overlay = editor.render_overlay
def overlay(chunks, *a, **k):
    CHUNKS.append([dict(c) for c in chunks]); return real_overlay(chunks, *a, **k)
editor.render_overlay = overlay

EDL = {"cuts": [{"clip_id": cid, "start": W("This")["start"] - 0.2, "end": W("seamless.")["end"] + 0.25, "why": "kept"},
                {"clip_id": cid, "start": W("This", 1)["start"] - 0.2, "end": W("one.")["end"] + 0.25, "why": "kept"}],
       "hook_text": "Joints you *can't* see", "key_words": ["seamless"],
       "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
       "summary": "Kept the explanation.", "missing_shots": [], "notes_addressed": [],
       "graphics": [{"clip_id": cid, "at": W("finish")["start"] + 0.05, "duration": 2.5, "style": "card",
                     "title": "The *finish*", "lines": ["Seamless"], "scene": "", "look": "cutaway", "role": "explain", "why": ""}]}
def A(kind, start=None, frm="", to=""):
    return {"type": kind, "start": start, "end": None, "from": frm, "to": to, "item": None, "field": "", "amount": None, "reason": ""}
def F(what, area, start=None, action=None, can=True, when="this_draft", for_bryan=""):
    return {"what": what, "why": "because", "start": start, "end": None, "area": area, "when": when, "kind": "none",
            "editor_can_fix": can, "rule": "", "same_rule": 0, "illustrate": "", "action": action or A("none"), "for_bryan": for_bryan}
FOOTAGE = F("Film a close-up of the joint", "picture", can=False, when="next_shoot", for_bryan="footage")
CALLS, QUEUE = [], {"edit": [EDL], "review": [], "spell": []}
def kind_of(req):
    sysp = req.get("system", "")
    for k, mark in (("edit", "You are the editor for Bryan's Instagram Reels"), ("review", "You review draft Instagram Reels"),
                    ("spell", "You check the captions of an Instagram Reel"), ("join", "You check a Reel's cut for broken sentences")):
        if mark in sysp:
            return k
    raise AssertionError("unexpected call: " + sysp[:60])
def fake_final(client, **req):
    k = kind_of(req)
    content = req["messages"][0]["content"]
    text = "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text") if isinstance(content, list) else content
    CALLS.append(k)
    if k == "review":
        tl = draft_review.draft_words(conn, row())
        sids = next((x for x in tl if x["word"] == "SIDs"), None)
        fixes = ([F("Correct the caption 'SIDs' to 'IDs'", "captions", sids["start"], A("caption_word", sids["start"], "SIDs", "IDs"))]
                 if sids else []) + [FOOTAGE]
        answer = {"verdict": "Nearly ready: 2 things to fix", "scores": {s: "good" for s in ("hook", "pace", "sound", "captions", "picture")},
                  "fixes": fixes, "works": ["Clear"]}
    elif k == "spell":
        answer = {"fixes": []}
    elif k == "join":
        answer = {"joins": [{"id": i + 1, "reads_complete": True, "restore": []} for i in range(text.count("join "))]}
    else:
        answer = QUEUE[k].pop(0)
    return NS(content=[NS(type="text", text=json.dumps(answer))], stop_reason="end_turn", model=req.get("model") or "claude-opus-5-5",
              usage=NS(input_tokens=2000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0))
llm.final_message = fake_final
def spent(): return usage.total_for(conn, f"production:{pid}")

t0 = time.time()
worker.run_editor(stop_after_one=True)
r1 = row()
print(f"  (v1 in {time.time() - t0:.0f}s, calls {CALLS})")
ok(r1["status"] == "ready" and r1["version"] == 1 and "edit" in CALLS and "review" in CALLS, f"v1 waiting for review ({r1['status']}, {CALLS})")
edl1 = json.loads(r1["edl"])
tl1 = draft_review.draft_words(conn, r1)
said1 = [x["word"] for x in tl1]
ok("SIDs" in said1 and "MCSC" in said1 and "every" in said1, f"v1's captions say what Whisper heard ({' '.join(said1)})")
cost1, file1 = spent(), Path(r1["video_path"])
ok(cost1 > 0 and file1.exists(), f"v1 cost US${cost1:.3f} (the plan, the spell-check, the read)")

# The team, through Review.
import auth, dashboard
uid = auth.create_user(conn, "bryan@x.com", "Bryan", "correct horse battery", auth.OWNER)
tid = auth.create_user(conn, "michelle@theurbanmakers.com", "Michelle", "correct horse battery", auth.TEAM)
def client(u):
    c = dashboard.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u; s["sv"] = 1
    return c
owner, team = client(uid), client(tid)
det = team.get(f"/api/production/{pid}").json
we = det.get("words_edit") or {}
ok(we.get("why_not") is None and we.get("text") == te.editable(tl1), "the draft offers Edit words to the team, with its words")
box = we["text"]
fixed = box.replace("MCSC", "MCST").replace("SIDs", "as IDs").replace("every one.", "everyone.")
r = team.post(f"/api/production/{pid}/words", json={"text": box, "version": 1})
ok(r.status_code == 400 and "Nothing changed" in r.json["error"], f"nothing changed: {r.json}")
r = team.post(f"/api/production/{pid}/words", json={"text": fixed + " And then we tile it all.", "version": 1})
ok(r.status_code == 400 and "new sentence" in r.json["error"] and row()["status"] == "ready", f"a new sentence: {r.json['error']}")
r = team.post(f"/api/production/{pid}/words", json={"text": fixed, "version": 0})
ok(r.status_code == 409 and "v1" in r.json["error"], f"an older version: 409, {r.json['error']}")
gl_before = glossary.PATH.read_text(encoding="utf-8")
calls_before = len(CALLS)
r = team.post(f"/api/production/{pid}/words", json={"text": fixed, "version": 1, "remember": True})
ok(r.status_code == 200 and [(c["from"], c["to"]) for c in r.json["changes"]] == [("MCSC", "MCST"), ("SIDs", "as IDs"), ("every one.", "everyone.")],
   f"Fix captions: three changes ({r.json})")
ok(r.json["remembered"] == [], f"MCST and IDs are already in the glossary: nothing new ({r.json['remembered']})")
ok(glossary.PATH.read_text(encoding="utf-8") == gl_before, "the glossary unchanged")
r2 = row()
pend = json.loads(r2["edl"]).get("word_fixes") or {}
ok(r2["status"] == "revising" and r2["version"] == 1 and te.pending(r2) and pend.get("by") == "Michelle",
   "queued for the editor (Being edited), the corrections held with the draft")
ok(r2["video_path"] == r1["video_path"] and len(CALLS) == calls_before, "nothing rendered or asked yet")
r = owner.post(f"/api/production/{pid}/words", json={"text": fixed, "version": 1})
ok(r.status_code == 400 and "working on it" in r.json["error"], f"while it's being edited: {r.json['error']}")
ok(team.get(f"/api/production/{pid}").json["words_edit"]["why_not"].startswith("The editor is working"),
   "and Review's button says why")

# The editor: v2.
CALLS.clear(); CHUNKS.clear()
t0 = time.time()
worker.run_editor(stop_after_one=True)
v2 = row()
print(f"  (v2 in {time.time() - t0:.0f}s, calls {CALLS})")
ok(CALLS == [], f"no Claude call: no plan, no spell-check, no read ({CALLS})")
ok(abs(spent() - cost1) < 1e-9, f"no cost: the draft's total is still US${spent():.3f}")
ok(v2["status"] == "ready" and v2["version"] == 2 and Path(v2["video_path"]).exists()
   and Path(v2["video_path"]).name == f"production_{pid}_v2.mp4", f"v2 back in To review ({v2['status']}, {v2['video_path']})")
ok(file1.exists(), "v1's file stays")
edl2 = json.loads(v2["edl"])
key = lambda e: [(c["clip_id"], round(c["start"], 3), round(c["end"], 3)) for c in e["cuts"]]
ok(key(edl2) == key(edl1), f"the same cut, piece for piece ({key(edl2)})")
ok(abs(v2["duration"] - r1["duration"]) < 0.01 and abs(extract.probe(Path(v2["video_path"]))["duration"] - extract.probe(file1)["duration"]) < 0.05,
   f"the same length ({r1['duration']} s, file {extract.probe(Path(v2['video_path']))['duration']:.2f} s)")
ok([(g["title"], g["start"], g["duration"]) for g in edl2["graphics"]] == [(g["title"], g["start"], g["duration"]) for g in edl1["graphics"]]
   and edl2["hook_text"] == edl1["hook_text"] and edl2["music"]["volume_db"] == edl1["music"]["volume_db"],
   "the card, the hook and the music as they were")
tl2 = draft_review.draft_words(conn, v2)
said2 = [x["word"] for x in tl2]
ok("MCST" in said2 and "as IDs" in said2 and "everyone." in said2 and not {"MCSC", "SIDs", "every", "one."} & set(said2),
   f"its words are the corrected ones ({' '.join(said2)})")
old = {x["word"]: x for x in tl1}
new = {x["word"]: x for x in tl2}
ok(abs(new["as IDs"]["start"] - old["SIDs"]["start"]) < 1e-6 and abs(new["as IDs"]["end"] - old["SIDs"]["end"]) < 1e-6,
   "'as IDs' over SIDs' own time")
ok(abs(new["everyone."]["start"] - old["every"]["start"]) < 1e-6 and abs(new["everyone."]["end"] - old["one."]["end"]) < 1e-6,
   "'everyone.' over both words' time")
ok(len(tl2) == len(tl1) - 1 and [x["start"] for x in tl2 if x["word"] not in ("everyone.",)]
   == [x["start"] for x in tl1 if x["word"] not in ("every", "one.")], "every other word where it was")
shown = " | ".join(c["text"] for c in (CHUNKS[-1] if CHUNKS else []))
ok(CHUNKS and "MCST" in shown and "as IDs" in shown and "everyone" in shown and "SIDs" not in shown and "MCSC" not in shown,
   f"the rendered captions say them ({shown})")
corr = edl2.get("corrections") or []
ok(sum(1 for c in corr if c.get("by") == "review" and c.get("who") == "Michelle") == 4 and "word_fixes" not in edl2
   and edl2["words_fixed"]["version"] == 2, f"kept on the edit plan by clip (4 words, Michelle's); nothing pending ({len(corr)})")
ok(not te.pending(v2), "nothing queued any more")
rv = draft_review.current(v2)
ok(rv is not None and v2["review_version"] == 2 and [f["what"] for f in rv["fixes"]] == [FOOTAGE["what"]]
   and rv["carried"] == "Read on v1. Only the captions changed since.",
   f"Studio's read of v1 carried over, minus the caption fix the correction made ({rv and [f['what'] for f in rv['fixes']]})")
import self_fix
sm = self_fix.summary(v2)
ok(sm is not None and json.loads(v2["self_fix"])["words"] and "US$" in sm["cost_line"], "Review's summary describes v2")
ok(team.get(f"/api/production/{pid}").json["words_edit"]["text"] == te.editable(tl2), "Edit words offers v2's words next time")
# A later revision keeps his words (they're tied to the clip's words, not the cut).
later = {"cuts": edl2["cuts"]}
editor._keep_corrections(later, v2, CL)
ok(later.get("corrections") == corr, "a later re-plan or start over keeps the corrections")

# Remember this word, on a word the glossary doesn't have.
fixed2 = te.editable(tl2).replace("seamless.", "Lamiseal.")
r = owner.post(f"/api/production/{pid}/words", json={"text": fixed2, "version": 2, "remember": True})
ok(r.status_code == 200 and r.json["remembered"] == ["Lamiseal"], f"Remember this word adds the new term ({r.json})")
gtext = glossary.PATH.read_text(encoding="utf-8")
ok(gtext.count("\nLamiseal\n") == 1 and glossary.known("lamiseal"), "once, in Studio/glossary.txt")
conn.execute("UPDATE productions SET status = 'ready', edl = ? WHERE id = ?", (v2["edl"], pid)); conn.commit()  # take it back
r = owner.post(f"/api/production/{pid}/words", json={"text": fixed2, "version": 2, "remember": True})
ok(r.status_code == 200 and r.json["remembered"] == [] and glossary.PATH.read_text(encoding="utf-8").count("\nLamiseal\n") == 1,
   "the same term again: not added twice")
conn.execute("UPDATE productions SET status = 'ready', edl = ? WHERE id = ?", (v2["edl"], pid)); conn.commit()

# What is refused, in plain words.
conn.execute("UPDATE productions SET status = 'approved' WHERE id = ?", (pid,)); conn.commit()
r = owner.post(f"/api/production/{pid}/words", json={"text": fixed2, "version": 2})
ok(r.status_code == 400 and r.json["error"] == "Edit words works on a draft waiting for review.", f"approved: {r.json['error']}")
ok(owner.get(f"/api/production/{pid}").json["words_edit"]["why_not"] == "Edit words works on a draft waiting for review.",
   "the button says why")
conn.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (pid,)); conn.commit()
ok(te.why_not({"format": "carousel", "status": "ready"}).startswith("A carousel has no transcript")
   and te.why_not({"format": "slideshow", "status": "ready"}).startswith("A slideshow Reel"), "a carousel and a slideshow Reel say why")
r = owner.post(f"/api/production/{pid}/words", json={"text": 12, "version": 2})
ok(r.status_code == 400, "not text: refused")
r = owner.post(f"/api/production/{pid}/words", json={"text": fixed2, "version": "2"})
ok(r.status_code == 400, "a version that isn't a number: refused")
ok(owner.post("/api/production/999/words", json={"text": "x"}).status_code == 404, "a draft that isn't there: 404")
anon = dashboard.app.test_client()
ok(anon.post(f"/api/production/{pid}/words", json={"text": fixed2, "version": 2}).status_code in (401, 302, 403),
   "signed out: refused")
ok(row()["status"] == "ready" and row()["version"] == 2, "none of that touched the draft")

# The docs and the page.
app_js = (STUDIO / "web" / "app.js").read_text(encoding="utf-8")
ok(all(s in app_js for s in ('id="words-edit"', 'id="words-fix"', 'id="words-cancel"', 'id="words-remember"', 'mark("words_edit")',
                             'mark("words_fixed"', 'mark("words_cancel")')), "Review: Edit words, Fix captions, Cancel, the tick, usage marks")
import usage_report
row_ = next((f for f in usage_report.FEATURES if f["key"] == "edit_words"), None)
ok(row_ and any(usage_report._matches({"kind": "click", "target": "#words-fix in #panel", "page": "draft", "d": {}}, x)
                for x in row_["events"]), "the weekly report's FEATURES row counts a press as capture.js names it")
ok("Edit words" in (STUDIO / "cloud" / "planner" / "help.js").read_text(encoding="utf-8")
   and "**Edit words**" in (STUDIO / "README.md").read_text(encoding="utf-8"), "help and the README say how")
finish()
