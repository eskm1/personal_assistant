"""What kinds of posts work (insights.py, insights_routes.py; Bryan, 2026-10-27: "understand what kinds of posts work
and tweak as we go"). Synthetic Instagram numbers for ~40 posts with planted effects (carousels with a "Myth vs fact"
hook really do get about twice the saves): the day-7 read and its rates, the index against the typical post of the
same format, the shrinkage finding the planted effect as "proven" only when there are enough posts and calling a
small group "early" (and a group of one nothing), no false "proven" on numbers with nothing planted, the forecast
and its grade against "every post is typical", the memo's prompt carrying numbers and n and nothing personal, a
stub Claude's memo checked and kept, its lines in the topics' prompt and Studio's playbook section, and the API
route's sign-in (none, a bad token, a planner token).
Run: python tests/quality/suite_insights.py (from studio/)"""
import json, math, random, statistics, types
from datetime import datetime, timedelta, timezone
from common import ROOT, ok, finish

import catalog
import insights
import settings

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 26, 7, 5, tzinfo=SGT).astimezone(timezone.utc)   # a Monday, 7:05 Singapore
CLIENT = "Tan Residence"          # a project (client) name that must never reach the memo
ADDRESS = "Blk 123 Tampines #05-12"
FORMULAS = ["Question", "Number list", "Mistake", "Before and after"]
BASE = {"carousel": {"saves": 15, "shares": 5, "follows": 1.2, "profile_visits": 8, "comments": 2},
        "reel": {"saves": 6, "shares": 4, "follows": 0.8, "profile_visits": 6, "comments": 1.5}}


def poisson(rng, lam):
    if lam < 30:
        l, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= l:
                return k
            k += 1
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def fresh():
    """An empty catalogue in its own folder."""
    ROOT.mkdir(parents=True, exist_ok=True)
    db = ROOT / f"catalog-{random.random():.9f}.db"
    conn = catalog.connect(db)
    insights.ensure(conn)
    return conn


def world(conn, seed=1, n=40, myth=10, plant=2.2, phone=3, scheduled=True, planted=("saves",)):
    """n Studio posts every ~2.5 days up to a week ago (half carousels, half Reels), `myth` of the carousels with
    the "Myth vs fact" hook whose saves are `plant` times the rest; a few phone posts; two posts scheduled this week."""
    rng = random.Random(seed)
    stamp = NOW.isoformat()
    conn.execute("INSERT INTO projects (name, folder, status, created_at) VALUES (?, ?, 'active', ?)", (CLIENT, CLIENT, stamp))
    remote = []
    carousels = [i for i in range(n) if i % 2 == 0]
    myth_ids = set(rng.sample(carousels, min(myth, len(carousels))))
    for i in range(n):
        kind = "carousel" if i % 2 == 0 else "reel"
        formula = "Myth vs fact" if i in myth_ids else FORMULAS[rng.randrange(len(FORMULAS))]
        posted = NOW - timedelta(days=8 + (n - i) * 2.5, hours=rng.random() * 3)
        length = rng.choice([18, 28, 35, 45, 70])
        edl = {"look": {"titles": rng.choice(["site", "gallery"])}, "voiceover": {"on": rng.random() < .4},
               "graphics": [{"style": "card"}] * rng.randrange(3) + [{"style": "scene"}] * rng.randrange(2),
               "sfx": {"on": True, "placed": [1]} if rng.random() < .5 else {"on": False},
               "music": {"mood": rng.choice(["calm", "upbeat"])}, "cuts": [{"clip_id": 1, "start": 0, "end": rng.choice([1.5, 3, 5])}]}
        cur = conn.execute("""INSERT INTO productions (title, brand, format, status, version, duration, caption, edl, music,
                                  slides, created_at, updated_at) VALUES (?, 'urban_makers', ?, 'posted', ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (f"{CLIENT} kitchen {i}", "carousel" if kind == "carousel" else "talking_head", rng.choice([1, 2, 3]),
                            None if kind == "carousel" else length, "Caption", json.dumps(edl) if kind == "reel" else None,
                            json.dumps({"title": "Track A", "mood": edl["music"]["mood"]}),
                            json.dumps({"slides": [{}] * rng.choice([4, 6, 9])}) if kind == "carousel" else None, stamp, stamp))
        prod = cur.lastrowid
        hook = f"hook {i}"
        remote.append({"id": 1000 + i, "hook": hook, "post_type": rng.choice(["knowhow", "ideas"]),
                       "hook_options": [{"text": hook, "formula": formula}, {"text": "other", "formula": "Question"}],
                       "suggestion": {"source": rng.choice(["wiki", "transcript", "basics"])}})
        media = f"m{seed}_{i}"
        cur = conn.execute("""INSERT INTO posts (production_id, idea_remote, kind, title, caption, version, scheduled_at, state,
                                  media_id, permalink, posted_at, created_at, updated_at)
                              VALUES (?, ?, ?, ?, ?, 1, ?, 'posted', ?, ?, ?, ?, ?)""",
                           (prod, 1000 + i, kind, f"{CLIENT} kitchen {i}", f"Our {ADDRESS} job " + "x" * rng.choice([60, 300, 700]),
                            posted.isoformat(), media, f"https://ig.example/p/{media}", posted.isoformat(), stamp, stamp))
        conn.execute("""INSERT INTO ig_media (media_id, post_id, media_type, product_type, permalink, caption, posted_at, seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (media, cur.lastrowid, "CAROUSEL_ALBUM" if kind == "carousel" else "VIDEO",
                                                             "FEED" if kind == "carousel" else "REELS", f"https://ig.example/p/{media}",
                                                             f"{CLIENT} at {ADDRESS}", posted.isoformat(), stamp))
        quality = math.exp(rng.gauss(0, .3))
        reach = int(1500 * math.exp(rng.gauss(0, .4)))
        rates = dict(BASE[kind])
        if i in myth_ids:
            for k in planted:
                rates[k] *= plant
        counts = {k: poisson(rng, v * reach / 1000 * quality) for k, v in rates.items()}
        sg = posted.astimezone(SGT).date()
        for age, part in ((1, .6), (7, 1.0), (30, 1.05)):
            day = sg + timedelta(days=age)
            if day > NOW.astimezone(SGT).date():
                continue
            vals = {k: int(v * part) for k, v in counts.items()}
            conn.execute("""INSERT INTO post_results (media_id, day, post_id, views, reach, likes, comments, saves, shares,
                                interactions, avg_watch_ms, total_watch_ms, follows, profile_visits, pulled_at)
                            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?, ?, ?)""",
                         (media, day.isoformat(), int(reach * 1.3 * part), int(reach * part), int(reach * .05), vals["comments"],
                          vals["saves"], vals["shares"], int(length * 450 * quality) if kind == "reel" else None,
                          vals["follows"], vals["profile_visits"], stamp))
    for j in range(phone):
        posted = NOW - timedelta(days=60 + j * 3)
        media = f"phone{seed}_{j}"
        conn.execute("""INSERT INTO ig_media (media_id, post_id, media_type, product_type, permalink, caption, posted_at, seen_at)
                        VALUES (?, NULL, 'IMAGE', 'FEED', ?, 'From the phone', ?, ?)""", (media, f"https://ig.example/p/{media}",
                                                                                          posted.isoformat(), stamp))
        conn.execute("""INSERT INTO post_results (media_id, day, views, reach, likes, comments, saves, shares, follows,
                            profile_visits, pulled_at) VALUES (?, ?, 1200, 1000, 50, 2, 9, 3, 1, 6, ?)""",
                     (media, (posted.astimezone(SGT).date() + timedelta(days=40)).isoformat(), stamp))   # read late
    for d in range(60):
        conn.execute("INSERT INTO account_results (day, followers, reach, views, interactions, pulled_at) VALUES (?, ?, 0, 0, 0, ?)",
                     ((NOW.date() - timedelta(days=60 - d)).isoformat(), 2000 + d * 3, stamp))
    if scheduled:
        for k, formula in enumerate(["Myth vs fact", "Question"]):
            cur = conn.execute("""INSERT INTO productions (title, brand, format, status, version, slides, created_at, updated_at)
                                  VALUES ('Next', 'urban_makers', 'carousel', 'approved', 1, ?, ?, ?)""",
                               (json.dumps({"slides": [{}] * 6}), stamp, stamp))
            remote.append({"id": 2000 + k, "hook": f"next {k}", "post_type": "knowhow",
                           "hook_options": [{"text": f"next {k}", "formula": formula}]})
            conn.execute("""INSERT INTO posts (production_id, idea_remote, kind, title, caption, version, scheduled_at, state,
                                created_at, updated_at) VALUES (?, ?, 'carousel', 'Next', 'Short', 1, ?, 'scheduled', ?, ?)""",
                         (cur.lastrowid, 2000 + k, (NOW + timedelta(days=2 + k * 2)).isoformat(), stamp, stamp))
    conn.commit()
    insights.remember_ideas(conn, remote)
    return myth_ids


def find(rows, feature, value, metric=None):
    return next((r for r in rows if r["feature"] == feature and r["value"] == value and (metric is None or r["metric"] == metric)), None)


# ── 1. The day-7 read, rates and the index ────────────────────────────────────
conn = fresh()
world(conn, seed=1)
items = insights.table(conn, NOW)
studio = [p for p in items if p["by_studio"]]
phone = [p for p in items if not p["by_studio"]]
ok(len(studio) == 40 and len(phone) == 3, f"every post read: {len(studio)} Studio, {len(phone)} phone")
ok(all(p["day7"] and p["day7"]["age"] == 7 and not p["day7"]["late"] for p in studio),
   "Studio's posts are judged on the read 7 days after they went out (not day 1, not day 30)")
ok(all(p["day7"]["late"] and p["day7"]["age"] == 40 for p in phone), "a post first read at day 40 is marked 'read late'")
p0 = studio[0]
r = p0["day7"]
ok(abs(r["rates"]["saves"] - r["counts"]["saves"] * 1000 / r["reach"]) < 1e-9, "saves per 1,000 reached = saves / reach x 1000")
reel = next(p for p in studio if p["format"] == "reel")
ok("watch" in reel["day7"]["rates"] and 0.2 < reel["day7"]["rates"]["watch"] < 0.9,
   f"a Reel's watch-through is average watch time over its length ({reel['day7']['rates']['watch']:.2f})")
ok("watch" not in p0["day7"]["rates"] or p0["format"] == "reel", "a carousel has no watch-through")
ok("likes" not in insights.COUNTS and "likes" not in insights.RATIOS, "likes are shown but never part of the judgement")
norm = [p["score"]["index"] for p in studio if p["format"] == "reel"]
med = sorted(norm)[len(norm) // 2]
ok(70 < med < 135, f"the typical Reel scores about 100 against its own format (median {med:.0f})")
myth = [p for p in studio if p["features"]["hook"] == "Myth vs fact"]
rest = [p for p in studio if p["format"] == "carousel" and p["features"]["hook"] != "Myth vs fact"]
ok(sum(p["score"]["per"]["saves"] for p in myth) / len(myth) > 1.4 * sum(p["score"]["per"]["saves"] for p in rest) / len(rest),
   "the planted carousels' saves index is well above the other carousels'")
f = studio[2]["features"]
ok(f["format"] == "Carousel" and f["hook"] in [*FORMULAS, "Myth vs fact"] and f["slides"] and f["source"] in insights.SOURCE_LABEL.values(),
   f"a Studio carousel's feature row: format, hook formula, slides, topic source ({f['slides']}, {f['source']})")
fr = next(p for p in studio if p["format"] == "reel")["features"]
ok(all(fr.get(k) for k in ("length", "look", "voiceover", "pictures", "drawings", "sfx", "music", "first_cut", "cards", "rounds", "notes")),
   "a Studio Reel's feature row has length, look, voice-over, inserts, sound effects, music, first cut, cards, rounds, notes")
ok(fr["weekday"] in insights.WEEKDAYS and "(" in fr["hour"], f"weekday and hour in Singapore ({fr['weekday']}, {fr['hour']})")
pf = phone[0]["features"]
ok(all(pf[k] is None for k in insights.FEATURES if k not in insights.PHONE_FEATURES) and pf["format"] == "Photo" and pf["studio"] == "Phone",
   "a phone post gets only what Instagram gives: format and time")
cs = fresh()
world(cs, seed=1, scheduled=False)
cs.execute("UPDATE productions SET format = 'slideshow' WHERE id = (SELECT production_id FROM posts WHERE kind = 'reel' LIMIT 1)")
cs.commit()
ss = next(p for p in insights.table(cs, NOW) if p["format"] == "slideshow")
ok(ss["base_format"] == "reel" and ss["features"]["format"] == "Slideshow Reel" and ss["score"]["index"],
   "a slideshow Reel is its own format, judged among the Reels")
ok(fr.get("music_track") == "Track A" and fr["music"] in ("Calm", "Upbeat"), "music: the mood is compared, the track title only shown")

# ── 2. Findings: the planted effect proven with enough posts, early with few ───
found = insights.findings(items)
hit = find(found["saves"]["rows"], "hook", "Myth vs fact")
ok(hit and hit["label"] == "proven" and hit["lift"] > 0.3,
   f"10 planted posts: Myth vs fact's saves found and proven (lift {hit and hit['lift']:+.2f}, n={hit and hit['n']} vs {hit and hit['n_rest']})")
ok(hit and hit["lift"] < hit["raw"], "the shrunk lift is smaller than the raw difference (pulled toward no difference)")
heads = insights.headline_findings(found)
ok(heads and heads[0]["label"] == "proven", "the proven finding comes first in the headline list")
ok(all(r["lift"] is None for r in heads if r["n"] < insights.SHOW_MIN), "a group under 3 posts never shows a difference")

conn2 = fresh()
world(conn2, seed=2, myth=3, scheduled=False)
items2 = insights.table(conn2, NOW)
hit2 = find(insights.findings(items2)["saves"]["rows"], "hook", "Myth vs fact")
ok(hit2 and hit2["n"] == 3 and hit2["label"] in ("early", "none"),
   f"the same effect on 3 posts is at most an early signal ({hit2 and hit2['label']}, lift {hit2 and hit2['lift']:+.2f})")
ok(insights.label(1, 39, 9.0, 2.0) == "few" and insights.label(7, 30, 5.0, 0.5) == "early" and insights.label(30, 30, 1.2, 0.5) == "none"
   and insights.label(8, 8, 3.1, 0.2) == "proven" and insights.label(20, 20, 2.9, 0.5) == "early"
   and insights.label(20, 20, 5.0, 0.5, events=12) == "early",
   "the stated rule: proven needs 8 a side, z 3, 15% and (a count) 20 of them; one post is never a pattern")

false_proven, null_bt = 0, []
for seed in range(10, 20):
    c = fresh()
    world(c, seed=seed, myth=10, plant=1.0, scheduled=False)
    it = insights.table(c, NOW)
    false_proven += sum(1 for r in insights.headline_findings(insights.findings(it)) if r["label"] == "proven"
                        and r["feature"] != "format")     # carousels and Reels really differ (BASE): that one is true
    null_bt.append(insights.backtest([p for p in it if p["by_studio"]])["better"])
    c.close()
ok(false_proven <= 1, f"nothing planted, 10 accounts: {false_proven} false 'proven' findings in all of them")
ok(statistics.mean(null_bt) > -0.05, f"nothing planted: the forecast is no worse than 'typical' on average "
                                     f"({statistics.mean(null_bt) * 100:+.1f}%), it doesn't chase noise")

# ── 3. The judge: forecasts, grades, the backtest ─────────────────────────────
# An all-round better kind of post (Myth vs fact carousels kept, passed on and followed from 2.5 times as often).
cb = fresh()
world(cb, seed=4, n=60, myth=14, plant=2.5, planted=("saves", "shares", "follows"))
items_b = insights.table(cb, NOW)
studio_b = [p for p in items_b if p["by_studio"]]
model = insights.fit(studio_b)
a = insights.predict(model, {**studio_b[0]["features"], "hook": "Myth vs fact", "format": "Carousel"})
b = insights.predict(model, {**studio_b[0]["features"], "hook": "Question", "format": "Carousel"})
ok(a > 115 and a > b, f"the forecast expects more of a Myth vs fact carousel ({a:.0f}) than a Question one ({b:.0f})")
ok(insights.predict(None, {}) == 100.0, "no model = the naive forecast, 100")
null_model = insights.fit([p for p in items if p["by_studio"]][:9])
ok(null_model is not None and not null_model["cols"] and insights.predict(null_model, studio[0]["features"]) == 100.0,
   "no signal yet: the forecast is the typical post, 100")
made = insights.forecast(cb, items_b, NOW)
ok(len(made) == 2 and made[0]["expected"] > made[1]["expected"],
   f"Monday forecasts both posts going out this week, the Myth vs fact one higher ({[m['expected'] for m in made]})")
ok(not insights.forecast(cb, items_b, NOW + timedelta(hours=1)), "the same week's posts are forecast once")
pid = studio_b[-1]["post_id"]
cb.execute("INSERT INTO insight_forecasts (week, post_id, format, scheduled_at, expected, made_at) VALUES ('2026-10-12', ?, 'reel', '', 120, '')", (pid,))
cb.commit()
g = insights.grade(cb, items_b, NOW)
actual = round(studio_b[-1]["score"]["index"], 1)
row = cb.execute("SELECT actual FROM insight_forecasts WHERE post_id = ?", (pid,)).fetchone()
ok(row["actual"] == actual and g["n"] == 1 and g["mae"] == round(abs(actual - 120), 1) and g["mae_naive"] == round(abs(actual - 100), 1),
   f"a past forecast is graded on its day-7 index: off by {g['mae']} against {g['mae_naive']} for 'typical'")
ok(g["waiting"] == 2 and g["target_by"] == "2027-01-04", f"two still waiting; the target date is 12 weeks on ({g['target_by']})")
n4 = insights._grade_numbers([10, 20, 10, 20], [20, 40, 20, 40])
ok(n4["better"] == 0.5 and n4["mae"] == 15 and n4["mae_naive"] == 30, "beats average by 1 - MAE / naive MAE (50% here)")
good = insights.grade_line({**n4, "target_by": "2027-01-18"})
ok(good["ok"] and good["text"].startswith("Beats average by 50%") and "n=4" in good["text"] and "25%" in good["text"],
   f"grade line: {good['text'][:70]}...")
bad = insights.grade_line({"n": 5, "mae": 30, "mae_naive": 29, "better": -0.03})
few = insights.grade_line({"n": 1, "waiting": 2})
ok(not bad["ok"] and "hunches" in bad["text"] and not few["ok"] and "hunches" in few["text"] and "2 waiting" in few["text"],
   "not better yet, or too few graded: 'treat suggestions as hunches'")
past = insights.backtest(studio_b)
ok(past["n"] >= 20 and past["better"] is not None and past["better"] > 0.05,
   f"the backtest (each post from the posts before it) beats average by {past['better'] and round(past['better'] * 100)}% (n={past['n']})")

# ── 4. The memo: prompt, a stub Claude, kept, used ────────────────────────────
SEEN = []


class Stream:
    def __init__(self, answer): self.answer = answer
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self):
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=json.dumps(self.answer))],
                                     stop_reason="end_turn", model="claude-opus-5-5",
                                     usage=types.SimpleNamespace(input_tokens=7000, output_tokens=2500,
                                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


class Claude:
    def __init__(self, answer): self.answer = answer; self.beta = types.SimpleNamespace(messages=self)
    def stream(self, **req):
        SEEN.append(req)
        return Stream(self.answer)


ANSWER = {"headline": f"Myth vs fact carousels get kept, like the {CLIENT} one at {ADDRESS}.",
          "do_more": [{"text": "Carousels with a Myth vs fact hook: saves 45% above the rest (n=10 vs 30, proven).", "strength": "proven"},
                      {"text": "Post more carousels, people like them.", "strength": "early"}],
          "do_less": [{"text": "Reels over 60 s: 20% fewer shares (n=4 vs 36, early signal).", "strength": "early"}],
          "try_next": [{"text": "Try 2 Reels with a voice-over (n=2 so far).", "strength": "idea"},
                       {"text": "Try 3 posts on Saturday mornings.", "strength": "idea"},
                       {"text": "Try 4 slides instead of 9.", "strength": "idea"}],
          "cant_tell": [f"Whether the hour matters at {CLIENT}: 6 more posts."]}
insights._tried["at"] = 0
rec = insights.weekly(conn, Claude(ANSWER), NOW)
user = SEEN[-1]["messages"][0]["content"] if SEEN else ""
ok(len(SEEN) == 1, "the memo is one Claude call")
ok("n=10 vs 30" in user or ("Myth vs fact" in user and "n=" in user), "the memo's prompt carries the findings with their n")
ok("index" in user and "per 1,000 reached" in user and "Forecast grade" in user and "Followers:" in user,
   "the prompt carries the indices, the baselines per 1,000 reached, the grade and the followers")
ok(CLIENT.lower() not in user.lower() and "tampines" not in user.lower() and "kitchen" not in user.lower(),
   "nothing personal in the prompt: no titles, captions, project names or addresses")
ok("never" in SEEN[-1]["system"].lower() and "How Bryan sounds" in SEEN[-1]["system"] or "Never write these" in SEEN[-1]["system"],
   "the system prompt carries Bryan's voice rules (wording.guide)")
ok(SEEN[-1]["model"] == settings.INSIGHTS_MODEL and SEEN[-1]["max_tokens"] <= 8000, f"model {settings.INSIGHTS_MODEL}, capped tokens")
m = rec["memo"]
allm = json.dumps(m)
ok(CLIENT.lower() not in allm.lower() and "#05-12" not in allm and "blk 123" not in allm.lower(),
   "nothing personal in the kept memo (the project name and the address scrubbed)")
ok(all(any(ch.isdigit() for ch in b["text"]) for k in ("do_more", "do_less", "try_next") for b in m[k]),
   "every bullet cites a number (the one without was dropped)")
ok(sum(len(m[k]) for k in ("do_more", "do_less", "try_next")) == 5, "3 to 5 bullets: six cut to five")
ok(m["grade"] == rec["grade"]["text"] and rec["grade"]["text"], "the grade is at the top of the memo")
path = settings.ROOT / "insights" / f"{NOW.astimezone(SGT).date().isoformat()}.json"
ok(path.is_file() and json.loads(path.read_text())["by"] == "claude", f"kept as {path.name}")
cost = catalog.connect().execute("SELECT kind, cost_usd FROM usage WHERE kind = 'insights'").fetchall()
ok(cost and 0.03 < cost[0]["cost_usd"] < 0.15, f"recorded as usage kind 'insights' (US${cost and cost[0]['cost_usd']:.3f})")

no_proven = insights.clean_memo(ANSWER, [{"label": "early"}], [CLIENT])
ok(no_proven["do_more"][0]["strength"] == "early", "a bullet called proven when no finding is becomes an early signal")

lines = insights.prompt_lines()
ok("Myth vs fact" in lines and lines.count("\n- ") <= 5 and len(lines) <= 1200, "the memo's top lines for the topics' prompt (small)")
import topics
ctx = {"ideas": [], "month": {}, "plan_text": "", "gaps": {}, "count": 4, "planned": [], "dismissed": [], "transcripts_text": "",
       "questions_text": "", "references_text": "", "calendar_text": "", "wiki_text": "", "insights_text": topics._insights_text()}
ok("What's working on Instagram" in topics.prompt(ctx) and "Myth vs fact" in topics.prompt(ctx), "topics' prompt carries them")
ok("What's working on Instagram" not in topics.prompt({**ctx, "insights_text": ""}), "and says nothing when there's no memo")
import learning
sec = learning.section()
ok(learning.INSIGHTS_HEADING in sec and "not rules" in sec and "Myth vs fact" in sec,
   "Studio's playbook section carries the Do more / Do less as soft guidance, not rules")
ok("Try 2 Reels" not in sec, "only Do more / Do less reach the playbook (Try next stays in the memo)")
import playbook
ok(learning.INSIGHTS_HEADING in playbook.text(), "written into the playbook's Studio section after the memo")
ok(insights.load_memo()["date"] == rec["date"] and insights.memos() == [rec["date"]], "the latest memo and the history")

# Too few posts: no Claude call, a plain memo that says so.
c3 = fresh()
world(c3, seed=3, n=4, myth=1, phone=0, scheduled=False)
before = len(SEEN)
plain = insights.weekly(c3, Claude(ANSWER), NOW + timedelta(days=7))
ok(len(SEEN) == before and plain["by"] == "studio" and any("Only" in t for t in plain["memo"]["cant_tell"]),
   "fewer than 5 scored posts: no Claude call, the plain memo says why")

# When it runs.
settings.INSIGHTS_DAY, settings.INSIGHTS_HOUR = "monday", 7
for p_ in insights.folder().glob("*.json"):
    p_.unlink()
ok(insights.due(datetime(2026, 10, 22, 6, 30, tzinfo=SGT)), "no memo yet: due at once (a missed Monday is caught up)")
(insights.folder() / "2026-10-19.json").write_text(json.dumps({"date": "2026-10-19"}))
ok(not insights.due(datetime(2026, 10, 26, 6, 30, tzinfo=SGT)), "last week's memo kept: not due before Monday 7am")
ok(insights.due(datetime(2026, 10, 26, 7, 1, tzinfo=SGT)), "due Monday from 7am")
insights._tried["at"] = 0
ran = insights.run_due(conn, None, Claude(ANSWER), datetime(2026, 10, 26, 7, 1, tzinfo=SGT))
ok(ran and not insights.due(datetime(2026, 10, 28, 9, 0, tzinfo=SGT)), "written once, then not due until next Monday")
ok(insights.due(datetime(2026, 11, 2, 7, 30, tzinfo=SGT)), "due again the next Monday")

# ── 5. The API route: sign-in ─────────────────────────────────────────────────
import cloud_auth
import dashboard
import uploads

dashboard.app.config["TESTING"] = True
web = dashboard.app.test_client()


def fake_verify(token):
    if token == "good-token":
        return {"id": "u1", "email": "michelle@theurbanmakers.com", "name": "Michelle", "role": "team"}
    raise cloud_auth.AuthError(401, "Sign in to the planner again")


cloud_auth.verify = fake_verify
uploads.cloud_auth.verify = fake_verify
r0 = web.get("/api/instagram/insights")
ok(r0.status_code == 401, f"no sign-in: refused ({r0.status_code})")
r1 = web.get("/api/instagram/insights", headers={"Authorization": "Bearer nope"})
ok(r1.status_code == 401, f"a bad planner token: refused ({r1.status_code})")
r2 = web.get("/api/instagram/insights", headers={"Authorization": "Bearer good-token"})
j = r2.get_json() or {}
ok(r2.status_code == 200 and {"grade", "memo", "posts", "findings", "followers", "baselines"} <= set(j),
   f"a team member's planner token reads it ({r2.status_code})")
ok(r2.headers.get("Cache-Control") == "no-store", "never kept by a cache")
r3 = web.get("/api/instagram/insights?memo=../../etc", headers={"Authorization": "Bearer good-token"})
ok(r3.status_code == 400, "a memo is asked for by date only")
r4 = web.post("/api/instagram/insights", headers={"Authorization": "Bearer good-token"})
ok(r4.status_code == 405, "nothing to change: GET only")
finish()
