"""Editfix unit tests: sentence guard, captions, cards, ducking expression, learned rules, trend.
Run: python tests/quality/suite_edit_units.py (from studio/)"""
import json, os, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ROOT, ok, finish, results
os.environ.update(ANTHROPIC_API_KEY="test", STUDIO_LEARNED_RULES_MAX="4")
import settings, catalog, editor, learning, playbook, self_fix

settings.ensure_folders(); conn = catalog.connect()

def W(word, s, e): return {"word": word, "start": s, "end": e}
def fake_clip(cid, words, duration=60):
    d = ROOT / "clips" / str(cid); d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(json.dumps({"words": words}), encoding="utf-8")
    return {"id": cid, "clip_dir": str(d), "duration": duration}

print("== 1. Sentence guard")
# Bryan's draft: it ended on a stray "AND" at 54.96-55.0 after "as well."
words = [W("You", 50.0, 50.2), W("need", 50.2, 50.5), W("an", 50.5, 50.6), W("overlay", 50.6, 51.1), W("as", 51.2, 51.4),
         W("well.", 51.4, 51.8), W("And", 54.96, 55.0)]
clips = {1: fake_clip(1, words, 56)}
cut = editor.snap({"clip_id": 1, "start": 49.9, "end": 54.98, "why": "x"}, clips[1])
ok(cut["end"] >= 55.0, f"snap keeps the stray word in ({cut['end']})")
cuts, done = editor.sentence_guard([cut], clips)
ok(abs(cuts[0]["end"] - (51.8 + editor.TAIL_PAD)) < 1e-6, f"ends right after 'well.' ({cuts[0]['end']})")
ok(any("stray" in d and "And" in d for d in done), f"says what it cut: {done}")
words_left = editor.timeline(cuts, clips)
ok(words_left[-1]["word"] == "well.", "the last word on the draft is 'well.'")
# Not when the leftover is longer than 1.5 s (a deliberate start of the next sentence)
long_tail = words[:-1] + [W("And", 52.0, 52.2), W("then", 52.2, 52.5), W("we", 52.5, 52.6), W("tile", 53.4, 53.9)]
clips = {1: fake_clip(1, long_tail, 56)}
cuts, done = editor.sentence_guard([{"clip_id": 1, "start": 49.9, "end": 54.0}], clips)
ok(cuts[0]["end"] == 54.0 and not any("stray" in d for d in done), "a 2.1 s tail is left alone")
# No sentence-final word at all: left alone
clips = {1: fake_clip(1, [W("so", 1.0, 1.2), W("we", 1.2, 1.4), W("go", 1.4, 1.8)])}
cuts, done = editor.sentence_guard([{"clip_id": 1, "start": 0.9, "end": 1.95}], clips)
ok(cuts[0]["start"] == 0.9 and cuts[0]["end"] == 1.95 and not done, "no punctuation: untouched")
# Start mid-sentence: "...the floor." tail of the previous sentence, then the hook
words = [W("We", 1.0, 1.2), W("level", 1.2, 1.5), W("the", 1.5, 1.6), W("floor.", 1.6, 2.0), W("Different", 2.3, 2.7),
         W("floor", 2.7, 3.0), W("heights?", 3.0, 3.4), W("One", 3.6, 3.8), W("is", 3.8, 3.9), W("an", 3.9, 4.0),
         W("overlay.", 4.0, 4.5)]
clips = {2: fake_clip(2, words)}
cuts, done = editor.sentence_guard([{"clip_id": 2, "start": 1.45, "end": 4.65}], clips)
ok(abs(cuts[0]["start"] - (2.3 - editor.HEAD_PAD)) < 1e-6, f"opens on 'Different' ({cuts[0]['start']})")
ok(any("opened on a full sentence" in d for d in done), f"{done}")
# A cut that starts at a sentence start is left alone
cuts, done = editor.sentence_guard([{"clip_id": 2, "start": 0.92, "end": 4.65}], clips)
ok(cuts[0]["start"] == 0.92 and not done, "a cut starting at a sentence start: untouched")
# Silence at the start and end
words = [W("Hello", 3.0, 3.4), W("there.", 3.4, 3.9)]
clips = {3: fake_clip(3, words)}
cuts, done = editor.sentence_guard([{"clip_id": 3, "start": 0.5, "end": 6.0}], clips)
ok(abs(cuts[0]["start"] - (3.0 - editor.HEAD_PAD)) < 1e-6 and abs(cuts[0]["end"] - (3.9 + editor.TAIL_PAD)) < 1e-6,
   f"dead air before and after trimmed ({cuts[0]['start']}-{cuts[0]['end']}): {done}")
# B-roll after the last talking cut is kept; the guard works on the last cut that speaks
clips[4] = fake_clip(4, [])
cuts, done = editor.sentence_guard([{"clip_id": 3, "start": 2.9, "end": 4.05}, {"clip_id": 4, "start": 0, "end": 3}], clips)
ok(cuts[1] == {"clip_id": 4, "start": 0, "end": 3} and not done, "silent B-roll at the end untouched")

print("\n== 2. Captions")
ws = [W("We", 0.0, 0.2), W("level", 0.2, 0.5), W("the", 0.5, 0.6), W("floor.", 0.6, 1.0), W("Then", 1.5, 1.7),
      W("tile.", 1.7, 2.1)]
ch = editor.caption_chunks(ws, 3.0)
ok(all(c["text"] != c["text"].upper() for c in ch), f"sentence case, not capitals: {[c['text'] for c in ch]}")
ok(ch[0]["text"].startswith("We"), "first caption starts with a capital")
fast = [W("I", 0.0, 0.08), W("think,", 0.08, 0.2), W("yes", 0.2, 0.5), W("we", 0.5, 0.7), W("do.", 0.7, 1.0)]
ch = editor.caption_chunks(fast, 2.0)
ok(ch[0]["text"].startswith("I think"), f"a caption up for 0.2 s joins the next: {[c['text'] for c in ch]}")
ok(all(len(c["text"]) <= editor.JOINED_CHARS for c in ch), "joined captions stay short")
lower = editor.caption_chunks([W("and", 0, 0.3), W("so", 0.3, 0.6)], 1.0)
ok(lower[0]["text"] == "And so", f"first caption capitalised ({lower[0]['text']})")

print("\n== 3. Cards vs the caption line")
graphics = [{"style": "card", "start": 10.0, "duration": 3.0, "title": "To level the floor", "lines": ["Self-levelling", "Overlay"]}]
chunks = [{"text": "To level", "start": 10.2, "end": 10.6}, {"text": "the floor", "start": 10.6, "end": 11.0},
          {"text": "we pour", "start": 11.0, "end": 11.4}, {"text": "the floor", "start": 14.0, "end": 14.4}]
kept, dropped = editor.clear_of_cards(chunks, graphics)
ok(dropped == 2 and [c["text"] for c in kept] == ["we pour", "the floor"],
   f"captions repeating the card hidden while it's up, others kept: {[c['text'] for c in kept]}")
ok(editor.CARD_BOTTOM < editor.CAPTION_TOP, f"card text box ends at {editor.CARD_BOTTOM}px, captions start at {editor.CAPTION_TOP}px")
ok("bottom:%dpx" % (editor.H - editor.CARD_BOTTOM) in editor._OVERLAY_CSS, "card box bottom set in the CSS")
ok(editor._card_sizes("Short", ["a"])[0] > editor._card_sizes("A much longer card title here", ["a"])[0], "long titles get smaller type")

print("\n== 4. Fonts and the ducking expression")
# (2026-10-20, gallery titles: each font is now (family, faces); Inter is the default)
for key, (family, faces) in editor.CAPTION_FONTS.items():
    for path, weight in faces:
        ok(path.exists() and path.stat().st_size > 10000, f"{key}: {family} {weight} bundled ({path.name}, {path.stat().st_size} bytes)")
        lic = path.parent / f"OFL-{path.stem.split('-')[0]}.txt"
        ok(lic.exists() and "Open Font License" in lic.read_text(), f"{key}: licence {lic.name}")
ok(editor.caption_font("nope")[0] == "Inter", "unknown font name falls back to Inter")
ok(editor.caption_font(None)[0] == "Inter", "default font Inter")
ok("Montserrat" not in editor._OVERLAY_CSS and "font-weight:900" not in editor._OVERLAY_CSS, "no system fonts, no 900 weight")
spans = editor.speech_spans([W("a", 1, 1.5), W("b", 1.9, 2.3), W("c", 4, 4.5)])
ok(spans == [(1, 2.3), (4, 4.5)], f"talking spans join short gaps: {spans}")
expr = editor._duck_expr([(1.0, 2.0)] * 9, -20, 11)
ok(expr.count("max(") == 8 and expr.startswith("pow(10,"), "ducking expression is a balanced max() tree")
import numpy as np
def ev(e, t):  # evaluate the ffmpeg expression with python
    py = e.replace("pow(", "_pow(").replace("clip(", "_clip(")
    return eval(py, {"_pow": pow, "_clip": lambda x, a, b: min(max(x, a), b), "max": max, "min": min, "t": t})
e = editor._duck_expr([(5.0, 8.0)], -20.0, 11.0)
db = lambda t: 20 * np.log10(ev(e, t))
ok(abs(db(2.0) + 20) < 0.01 and abs(db(6.0) + 31) < 0.01 and abs(db(4.875) - (-20 - 11 * 0.5)) < 0.01 and abs(db(10.0) + 20) < 0.01,
   f"music: -20 dB bed, -31 dB while talking, ramps between ({db(2):.1f}, {db(4.875):.1f}, {db(6):.1f}, {db(8.4):.1f}, {db(10):.1f})")

print("\n== 5. Learned rules")
playbook.ensure()
before = playbook.text()
bryan_part = before[before.index("## Notes from reviews"):]
playbook.remember("Bryan likes warm colour grades", "urban_makers / talking_head")
r1 = learning.learn([{"rule": "End on the last full sentence, never on a word that starts the next", "same_rule": 0}], "reel", "studio")
ok(len(r1) == 1 and r1[0]["new"] and r1[0]["id"] == 1, f"new rule R1: {r1}")
text = playbook.text()
ok(learning.START in text and "R1: End on the last full sentence" in text and "caught once" in text, "rule written to the playbook section")
ok(text.index(learning.START) < text.index("## Notes from reviews"), "Studio's section sits before Bryan's Notes from reviews")
ok("Bryan likes warm colour grades" in text, "Bryan's remembered note still there")
# Same rule again, by number: counted
r2 = learning.learn([{"rule": "Finish on a complete sentence", "same_rule": 1}], "reel", "studio")
ok(r2[0]["id"] == 1 and not r2[0]["new"], "same_rule = 1 counts R1 again")
# Near-duplicate wording, no number: counted, not added
r3 = learning.learn([{"rule": "End on the last full sentence; never on a word that starts a new one.", "same_rule": 0}], "reel", "bryan")
rules = learning.active("reel")
ok(len(rules) == 1 and rules[0]["count"] == 3 and rules[0]["from"] == {"studio": 2, "bryan": 1},
   f"near-duplicate counted on R1 (count {rules[0]['count']}, {rules[0]['from']})")
ok("caught 3 times" in playbook.text(), "count shown in the playbook")
# Twice in one call: counted once
learning.learn([{"rule": "Keep cards clear of the caption line", "same_rule": 0},
                {"rule": "Keep cards off the caption line", "same_rule": 0}], "reel", "studio")
ok(len(learning.active("reel")) == 2 and learning.active("reel")[1]["count"] == 1, "two items naming the same rule in one draft: counted once")
# Different scopes stay apart
learning.learn([{"rule": "Keep every slide under 30 words", "same_rule": 0}], "carousel", "studio")
ok(len(learning.active("carousel")) == 1 and "### Carousels" in playbook.text(), "carousel rules in their own list")
ok("R4: Keep every slide" in learning.rules_text("carousel") or "Keep every slide" in learning.rules_text("carousel"), "rules_text for the carousel read")
# Cap: at most 4 per kind (STUDIO_LEARNED_RULES_MAX=4 here); the least caught retire
for i, t in enumerate(["Open on the strongest claim, not a slow setup", "Music sits well under the voice",
                       "Cut dead air longer than half a second", "Captions never repeat words already on screen"]):
    learning.learn([{"rule": t, "same_rule": 0}], "reel", "studio", when=f"2026-10-{10 + i:02d}")
act = learning.active("reel")
ok(len(act) == 4 and act[0]["id"] == 1, f"capped at 4 reel rules, most caught first: {[r['text'][:20] for r in act]}")
data = json.loads(learning.RULES_FILE.read_text())
ok(sum(1 for r in data["rules"] if r["status"] == "retired" and r["scope"] == "reel") == 2, "the two least caught retired")
# Retired rule caught again comes back
learning.learn([{"rule": "Keep cards clear of the caption line", "same_rule": 0}], "reel", "studio")
ok(any("cards clear" in r["text"] for r in learning.active("reel")), "a retired rule caught again is active again")
# Stale: not seen for 120+ days and caught < 3 retires
data = json.loads(learning.RULES_FILE.read_text())
for r in data["rules"]:
    if "dead air" in r["text"]:
        r.update(status="active", last="2026-01-01")
learning.tidy(data, "2026-10-19")
ok(next(r for r in data["rules"] if "dead air" in r["text"])["status"] == "retired", "a rule not seen since January retires")
# Merge: two active near-duplicates merge, counts add up
data = {"next_id": 3, "rules": [
    {"id": 1, "scope": "reel", "text": "Music sits well under the voice.", "count": 3, "first": "2026-10-01", "last": "2026-10-10", "from": {"studio": 3}, "status": "active"},
    {"id": 2, "scope": "reel", "text": "The music sits well under the voice.", "count": 2, "first": "2026-09-01", "last": "2026-10-12", "from": {"bryan": 2}, "status": "active"}]}
learning.tidy(data, "2026-10-19")
ok(data["rules"][0]["count"] == 5 and data["rules"][1]["status"] == "merged" and data["rules"][0]["last"] == "2026-10-12"
   and data["rules"][0]["from"] == {"studio": 3, "bryan": 2}, "near-duplicates merge: counts and sources add up")
# Forget: gone from the playbook, never comes back
rid = learning.active("reel")[1]["id"]
rtext = learning.active("reel")[1]["text"]
ok(learning.forget(rid) and rtext not in playbook.text(), f"Forget R{rid}: gone from the playbook")
again = learning.learn([{"rule": rtext, "same_rule": rid}], "reel", "studio")
ok(again and again[0]["status"] == "forgotten" and rtext not in playbook.text(), "a forgotten rule caught again stays forgotten")
# Bryan's part untouched by any of this
text = playbook.text()
ok(text.replace(text[text.index(learning.START):text.index(learning.END) + len(learning.END)], "").replace("\n\n\n", "\n\n").count("Bryan likes warm colour grades") == 1
   and before[:before.index("## Notes from reviews")].strip() in text, "Bryan's sections are word for word as they were")
# Bryan edits his part: Studio's section survives in place
mine = learning.bryan_text()
ok(learning.START not in mine and "## Notes from reviews" in mine, "Bryan's editable text excludes Studio's section")
learning.save_bryan_text(mine.replace("## Story", "## Story\n\n- Always show the finished room last."))
text = playbook.text()
ok("Always show the finished room last." in text and learning.START in text and text.index(learning.START) < text.index("## Notes from reviews"),
   "Bryan's edit saved, Studio's section kept before Notes from reviews")
learning.save_bryan_text(mine + "\n" + learning.START + "\nsneaky\n" + learning.END)
ok(playbook.text().count(learning.START) == 1 and "sneaky" not in playbook.text(), "pasted markers can't make a second section")
playbook.remember("Another of Bryan's notes", "x")
ok(playbook.text().rstrip().endswith("(x, " + __import__("datetime").date.today().isoformat() + ")"), "remember() still appends to Notes from reviews")
# Setting off: nothing learned
settings.LEARN_RULES = False
ok(learning.learn([{"rule": "Brand new rule", "same_rule": 0}], "reel", "studio") == [], "STUDIO_LEARN_RULES=0: nothing learned")
settings.LEARN_RULES = True

print("\n== 6. Bryan's notes sorted")
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "T", "hook": "h", "angle": "a",
                                "beats": [], "segments": [], "caption": "", "est_seconds": 30}])
pid = catalog.make_production(conn, 1)
n1 = catalog.add_feedback(conn, "production", pid, "The music is too loud vs the voice")
n2 = catalog.add_feedback(conn, "production", pid, "Use the take where I smile")
notes = [dict(r) for r in conn.execute("select * from feedback")]
out = learning.from_notes(conn, notes, [
    {"note_id": n1, "how": "lowered", "kind": "editing", "rule": "Keep the music well under the voice", "same_rule": 0},
    {"note_id": n2, "how": "swapped", "kind": "taste", "rule": "", "same_rule": 0},
    {"note_id": 999, "how": "x", "kind": "editing", "rule": "Not a real note", "same_rule": 0}], "reel")
rows = {r["id"]: dict(r) for r in conn.execute("select * from feedback")}
ok(rows[n1]["kind"] == "editing" and rows[n1]["rule"] and "music" in rows[n1]["rule"].lower(), f"editing note became a rule: {rows[n1]['rule']}")
ok(rows[n2]["kind"] == "taste" and rows[n2]["rule"] is None, "taste note: no rule")
ok(not any("Not a real note" in r["text"] for r in learning.active()), "only Bryan's own notes count")
ok(len(out) == 1, "one rule from the send-back")

print("\n== 7. Trend")
now = datetime(2026, 10, 19, tzinfo=timezone.utc)
conn.executescript(learning._LOG)
for days, caught in [(3, 2), (10, 1), (20, 3), (40, 4), (45, 3), (50, 3)]:
    at = (now - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("insert into self_fix_log (at, production_id, version, scope, caught, fixed, left_over, rounds, usd) values (?,?,?,?,?,?,?,?,?)",
                 (at, 100 + days, 1, "reel", caught, caught, 0, 1, 0.1))
conn.commit()
t = learning.trend(conn, "reel", now=now)
ok(t["recent"] == {"drafts": 3, "avg": 2.0} and t["before"] == {"drafts": 3, "avg": 3.3}, f"30-day average 2.0, down from 3.3: {t}")
ok(learning.trend(conn, "carousel", now=now)["recent"]["drafts"] == 0, "carousel trend apart")
learning.log_draft(conn, 103, 1, "reel", 5, 4, 1, 2, 0.3)
learning.log_draft(conn, 103, 1, "reel", 6, 4, 1, 2, 0.3)
ok(conn.execute("select count(*), max(caught) from self_fix_log where production_id = 103").fetchone()[:] == (1, 6), "one log row per version (latest wins)")

print("\n== 8. Scores")
r_a = {"verdict": "Nearly ready: 4 things to fix", "scores": {"hook": "fix", "pace": "fix", "sound": "fix", "captions": "fix", "picture": "good"},
       "fixes": [{"when": "this_draft", "editor_can_fix": True}] * 3 + [{"when": "next_shoot", "editor_can_fix": False}]}
r_b = {"verdict": "Nearly ready: 1 thing to fix", "scores": {"hook": "good", "pace": "good", "sound": "good", "captions": "good", "picture": "good"},
       "fixes": [{"when": "this_draft", "editor_can_fix": False}]}
ok(self_fix.score(r_b) < self_fix.score(r_a) and len(self_fix.fixable(r_a)) == 3 and not self_fix.fixable(r_b), "score and fixable")
ok(self_fix.score({"verdict": "Needs work: x", "scores": {}, "fixes": []}) > self_fix.score(r_a), "needs work is worst")
finish()
