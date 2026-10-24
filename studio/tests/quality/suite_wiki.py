"""Urban Makers' wiki as a source of topics (wiki.py, topics.py; Bryan, 2026-10-23: "the wiki has a large
database of knowledge ... that we can also use to make some of the reels and carousels and suggest topics").
A local fake of the wiki's REST API with the read-only role's rules (exactly the named columns, select=*
refused with 403, the token and the anon key in headers), the copy on disk and the offline fallback, the
sanitiser's rules on invented snippets, the token and key never in a log or an error, and a whole topics
run with a stub Claude: the digest in the prompt and no dropped text, the suggestion carrying its article.
Run: python tests/quality/suite_wiki.py (from studio/)"""
import json, logging, os, socket, stat, threading, types
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from common import ROOT, ok, finish

JWT = "eyJhbGciOiJIUzI1NiJ9.fake-readonly-SECRET-token.4f9a8b7c6d5e"
ANON = "anon-SECRET-key-0123456789abcdef"
COLUMNS = {"slug", "title", "domain", "tags", "body_md", "updated_at"}

# Every log line anything writes (urllib3's request lines included), to look for the secrets in.
LOGS = []


class Catch(logging.Handler):
    def emit(self, record):
        try:
            LOGS.append(record.getMessage() + (self.formatter.formatException(record.exc_info) if record.exc_info else ""))
        except Exception:
            LOGS.append(str(record.msg))


_catch = Catch(); _catch.setFormatter(logging.Formatter())
logging.getLogger().addHandler(_catch)
logging.getLogger().setLevel(logging.DEBUG)

# ── The invented wiki (short fixtures; nothing from the real articles) ────────

TILES = """**[BOTH]** Plan the layout before the first tile goes down.

## Cuts at the edges

- Avoid slivers: for a 600x600mm tile keep every cut piece above 150mm.
- Start from the centre line of the room, then work outwards to both walls.
- Measure the floor at three points before setting out ([12 Pretend Rise](/projects?open=abc-123)).
- Check [the tile planner](/tools/tiles) before the first box is opened.
- The setting-out rule comes from [the tile council](https://tiles.example.org/rules) guide.

> 📷 **Image needed [IMG-901]** - a centre line chalked on the floor.

> **Internal - commercial.** Our tiler rate sits well under the shop's.
> Keep that between the team and the tiler.

- **Internal - commercial:** we book the tiler under another firm's name.
- Every internal corner gets a sealant bead after grouting.

## Pricing and grout

- Grout lines run 2-3mm for most porcelain floor tiles.
- Epoxy grout costs about S$12 a kilo more.
- A flood test runs 24 hours before any floor tile is laid.

## Case study: 88 Example Road, #05-12

- The homeowner wanted a 1mm grout line there; it cracked within weeks.

## Wet areas

| Tile | Thickness | Typical price |
|---|---|---|
| Wall | 8-10mm | $3 |

| Area | Tile size |
|---|---|
| Wall | 300x600mm, matt glaze |
| Floor (wet) | 600x600mm, R10 anti-slip |
| Showroom stock | $40 per box |

- [ ] Confirm the adhesive's name with the supplier.
- Quartz and sintered stone tops sit on a 20mm slab with epoxy grout joints.
- Samples come from the Lamitak gallery on weekdays.
- Call Mr Tan for the samples.
- The tiler's number is +65 6123 4567 for site visits.
- Mail tan.tiles@example.com for the site visit form.
- Our retail mark-up on tiles is a third.
- The quote lists every tile by room.
- Ask for a discount on bulk tile orders.
- The designer's commission lands every month.
- Keep a healthy margin on tiling jobs.
- The trade differential covers the coordination work.
- Charge the client for every extra cut.
- The adhesive store sits at Blk 123 near the market.
- The warehouse postcode S654321 takes deliveries.
- Unit #07-15 holds the spare tiles.
- The spare grout lives at 27 Pretend Road for now.
- The tile shop near Tampines opens early.
- Deliveries park along Jalan Contoh on weekends.
- More on the subject at https://example.com/tiles for later.
- Waterproofing goes 300mm up the shower wall (*verify*).
- *[Photos of the corner detail to be inserted.]*
- Acacia had hollow tiles under the sink.

Related: [[Grout Systems]] · [[Tile Formats]]
"""

HEIGHTS = """Quick heights for sockets and fittings, as rules of thumb.

## Sockets

- Sockets above a desk sit 800-900mm from the floor.
- Bedside sockets sit 500-650mm from the floor, within easy reach.
- Always state whether a height is to the top or the bottom of the socket.

## Hardware brands

- Hinges: European ones last longer than most.
- Soft-close dampers are worth asking for on every door.

## Toilet

- A toilet paper holder sits with its top at 900-950mm from the floor.
"""

ARTICLES = [
    {"slug": "tile-layout-basics", "title": "Tile Layout Basics", "domain": "Tiling & waterproofing", "tags": ["tiling"],
     "body_md": TILES, "updated_at": "2026-10-01T00:00:00+00:00"},
    {"slug": "mounting-heights", "title": "Mounting Heights", "domain": "Standards & specs", "tags": [],
     "body_md": HEIGHTS, "updated_at": "2026-10-02T00:00:00+00:00"},
    {"slug": "tile-shops-directory", "title": "Tile Shops Directory", "domain": "Suppliers & materials", "tags": [],
     "body_md": "- Big Tiles: open daily, walk-ins welcome.\n- Small Tiles: weekdays only.", "updated_at": None},
    {"slug": "where-we-get-stone", "title": "Where we get our stone", "domain": "Suppliers & materials", "tags": [],
     "body_md": "- Stone One, 5 Pretend Loop, S111111.\n- Stone Two, 9 Pretend Way, S222222.\n"
                "- Stone Three, 2 Pretend Lane, S333333.\n- Slabs are 20mm thick as a rule of thumb.", "updated_at": None},
]


# ── The fake wiki REST API ────────────────────────────────────────────────────

class Wiki:
    mode = "ok"         # ok | down | garbage
    requests = []       # (path, query, headers)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        Wiki.requests.append((url.path, url.query, dict(self.headers)))
        if url.path != "/rest/v1/wiki_articles":
            return self._send(404, {"message": "not found"})
        if self.headers.get("apikey") != ANON or self.headers.get("Authorization") != f"Bearer {JWT}":
            return self._send(401, {"message": "invalid JWT"})
        cols = (q.get("select") or ["*"])[0].split(",")
        if cols == ["*"] or not set(cols) <= COLUMNS:   # the role's column grant: select=* is refused
            return self._send(403, {"code": "42501", "message": "permission denied for table wiki_articles"})
        if Wiki.mode == "down":
            return self._send(503, {"message": "upstream down"})
        if Wiki.mode == "garbage":
            self.send_response(200); self.send_header("Content-Length", "9"); self.end_headers(); self.wfile.write(b"not json!")
            return
        self._send(200, [{c: a.get(c) for c in cols} for a in ARTICLES])


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

os.environ.update(STUDIO_WIKI_URL="", STUDIO_WIKI_JWT="", STUDIO_WIKI_ANON_KEY="", STUDIO_HUMANIZE="0")
ROOT.mkdir(parents=True, exist_ok=True)
import requests
import settings, wiki

# ── Settings and reading ──────────────────────────────────────────────────────

why = wiki.available()
ok(why and "STUDIO_WIKI_URL" in why and "STUDIO_WIKI_JWT" in why and "STUDIO_WIKI_ANON_KEY" in why,
   "unset: available() names the three missing settings")
ok(wiki.articles() == [] and not Wiki.requests, "unset: no articles and nothing asked")
settings.WIKI_URL, settings.WIKI_JWT, settings.WIKI_ANON_KEY = "http://wiki.example.net", JWT, ANON
ok("https" in (wiki.available() or ""), "a plain-http address other than this machine is refused (the token never goes over it)")
settings.WIKI_URL = BASE + "/rest/v1/"
ok(wiki.available() is None and wiki._endpoint() == BASE + "/rest/v1/wiki_articles",
   "set: available; a base that already ends in /rest/v1 isn't doubled")
settings.WIKI_URL = BASE

r = requests.get(BASE + "/rest/v1/wiki_articles", params={"select": "*"},
                 headers={"apikey": ANON, "Authorization": f"Bearer {JWT}"})
ok(r.status_code == 403, "the fake keeps the role's rule: select=* answers 403")
r = requests.get(BASE + "/rest/v1/wiki_articles", params={"select": "slug,title,status"},
                 headers={"apikey": ANON, "Authorization": f"Bearer {JWT}"})
ok(r.status_code == 403, "the fake keeps the role's rule: a column outside the six answers 403")
Wiki.requests.clear()

rows = wiki.fetch()
path, query, headers = Wiki.requests[-1]
ok(len(rows) == 4 and set(rows[0]) == COLUMNS, "fetch: every article, with exactly the six columns")
ok(parse_qs(query) == {"select": ["slug,title,domain,tags,body_md,updated_at"]},
   "fetch: asks for the columns by name, nothing else in the query")
ok(headers.get("apikey") == ANON and headers.get("Authorization") == f"Bearer {JWT}" and JWT not in query
   and ANON not in query, "fetch: the token and the anon key go in headers, never in the address")

T0 = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)
Wiki.requests.clear()
got = wiki.articles(T0)
cache = ROOT / "wiki.json"
ok(len(got) == 4 and len(Wiki.requests) == 1 and cache.exists(), "articles(): read once and kept on disk")
ok(stat.S_IMODE(cache.stat().st_mode) == 0o600, "the copy on disk is private (0600)")
got = wiki.articles(T0 + timedelta(hours=23))
ok(len(got) == 4 and len(Wiki.requests) == 1, "within a day: the copy on disk, the wiki isn't asked again")
got = wiki.articles(T0 + timedelta(hours=25))
ok(len(Wiki.requests) == 2, "after a day: read again")

Wiki.mode = "down"
got = wiki.articles(T0 + timedelta(hours=50))
ok(len(got) == 4 and len(Wiki.requests) == 3, "the wiki down (503): the copy on disk is used, no error raised")
data = json.loads(cache.read_text())
ok("503" in (data.get("error") or "") and data["fetched_at"].startswith("2026-10-25T02"),
   "the failure is noted; the copy keeps its date")
wiki.articles(T0 + timedelta(hours=50, minutes=30))
ok(len(Wiki.requests) == 3, "after a failure the wiki is left alone for an hour")
Wiki.mode = "garbage"
got = wiki.articles(T0 + timedelta(hours=52))
ok(len(got) == 4 and "JSON" in (json.loads(cache.read_text()).get("error") or ""), "an answer that isn't JSON: the copy, and why")
Wiki.mode = "ok"

# Offline: nothing listening on that port.
s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()
settings.WIKI_URL = f"http://127.0.0.1:{dead}"
got = wiki.articles(T0 + timedelta(hours=60))
ok(len(got) == 4, "offline: the copy on disk")
try:
    wiki.fetch(); ok(False, "offline: fetch raises a plain WikiError")
except wiki.WikiError as e:
    ok("can't reach the wiki" in str(e), "offline: fetch raises a plain WikiError")
cache.unlink()
ok(wiki.articles(T0 + timedelta(hours=61)) == [], "offline with no copy: no articles, nothing raised")
cache.write_text("{not json")
ok(wiki.articles(T0 + timedelta(hours=62)) == [], "a broken copy on disk: no articles, nothing raised")
settings.WIKI_URL = BASE

# Refused: the token and the key never in the error or the logs.
settings.WIKI_JWT = JWT + "x"
try:
    wiki.fetch(); ok(False, "a wrong token: refused")
except wiki.WikiError as e:
    ok("401" in str(e) and "STUDIO_WIKI_JWT" in str(e) and JWT not in str(e), "a wrong token: refused, in plain words")
settings.WIKI_JWT = JWT
e = wiki.WikiError(f"oops {JWT} and {ANON}")
ok(JWT not in str(e) and ANON not in str(e) and "***" in str(e), "an error message is scrubbed of the token and the key")
wiki.articles(T0 + timedelta(hours=70))   # read again for what follows
ok(json.loads(cache.read_text()).get("articles"), "back online: read again")
ok(any("wiki_articles" in m for m in LOGS), "the log lines looked at include the HTTP library's own request lines")
ok(not any(JWT in m or ANON in m for m in LOGS), f"no log line has the token or the key ({len(LOGS)} lines looked at)")
ok(JWT not in cache.read_text() and ANON not in cache.read_text(), "the copy on disk has neither")
info = wiki.info()
ok(info["on"] and info["articles"] == 4 and JWT not in json.dumps(info), "info(): on, the count, no secrets")

# ── The sanitiser ─────────────────────────────────────────────────────────────

clean = wiki.sanitise(ARTICLES[0], names=["Acacia Residence", "Acacia"])
text = clean["text"]
kept = ["600x600mm tile keep every cut piece above 150mm", "centre line of the room",
        "Measure the floor at three points before setting out.", "Check the tile planner before",
        "comes from the tile council guide", "Every internal corner gets a sealant bead",
        "Grout lines run 2-3mm", "A flood test runs 24 hours", "300x600mm", "R10 anti-slip",
        "Quartz and sintered stone tops sit on a 20mm slab with epoxy grout joints", "Plan the layout"]
for k in kept:
    ok(k in text, f"kept: the trade fact \"{k}\"")
gone = {"a link to a project, with its text": "Pretend Rise", "the project link itself": "/projects",
        "a web address (an outside link keeps its words)": "tiles.example.org", "an image placeholder": "IMG-901",
        "an internal callout, both lines": "tiler rate", "the callout's second line": "between the team",
        "an internal line": "another firm", "money: S$": "S$12", "money: $ in a table": "$40",
        "a table whose header is about price": "Typical price", "a heading about money (its facts stay)": "Pricing",
        "a case study section, address and all": "Example Road", "a unit number": "#05-12",
        "the case study's story": "cracked within weeks", "an open checklist item": "adhesive's name",
        "a brand": "Lamitak", "a person's name": "Mr Tan", "a phone number": "6123 4567", "an email": "example.com",
        "retail": "mark-up", "quote": "lists every tile", "discount": "bulk tile", "commission": "lands every month",
        "margin": "healthy", "trade differential": "coordination work", "charge": "extra cut", "Blk": "Blk 123",
        "a postcode": "S654321", "a unit number without a street": "#07-15", "a street address": "27 Pretend",
        "a town": "Tampines", "a Jalan": "Contoh", "a bare web address": "https://", "an unverified fact": "300mm up",
        "a photo to be inserted": "to be inserted", "a project's name": "Acacia", "the Related line": "Grout Systems",
        "the role markers": "[BOTH]"}
for what, s in gone.items():
    ok(s not in text, f"dropped: {what}")
ok("## Pricing and grout" not in text and "## Cuts at the edges" in text, "a money heading goes, a plain one stays")
ok(wiki.sanitise(ARTICLES[2]) is None, "a directory (by its title) is dropped whole")
ok(wiki.sanitise(ARTICLES[3]) is None, "a list of addresses (three or more) is dropped whole, whatever its title")
h = wiki.sanitise(ARTICLES[1])["text"]
ok("Soft-close" not in h and "European" not in h and "800-900mm" in h and "900-950mm" in h,
   "a section about brands goes whole; the sections after it stay")
ok(wiki.sanitise({"title": "Renovation Permits: Condo and Commercial", "body_md": "- Book the lift 3 days ahead of hacking."}),
   "\"Commercial\" in a title is the kind of property, not a marker: the article stays")
ok(wiki.sanitise({"title": "Pricing your tiles", "body_md": "- Tiles sit 2mm apart."}) is None, "a title about money: dropped")
ok(wiki.sanitise({"title": "Acacia handover", "body_md": "- Tiles sit 2mm apart."}, names=["Acacia"]) is None,
   "a title with a project's name: dropped")
snippets = {"S$1,200 for the set": None, "about $80 a point": None, "the price goes up": None, "it costs more": None,
            "we charge for it": None, "in the quotation": None, "a 10% discount": None, "her commission": None,
            "the margin": None, "the trade differential": None, "at retail": None, "Tile 600x600mm floors in 2 days": "kept",
            "Blk 45 is near": None, "#12-34 is the unit": None, "Singapore 123456 is it": None, "call 9123 4567": None,
            "a@b.co for it": None, "Ms Lee asked": None,
            "fitted with Blum hinges": None, "a ® brand": None, "Lay tiles after the screed cures for 7 days": "kept"}
for line, want in snippets.items():
    out, _ = wiki.clean_text(f"- {line}")
    ok(bool(out) == bool(want), f"one line: \"{line}\" {'kept' if want else 'dropped'}")
out, _ = wiki.clean_text("- Level the floor first ([Smith's flat](/projects?open=1)), then screed.\n"
                         "- Ask the tiler ([Wong residence](/clients/9)) for a dry lay.")
ok("Smith" not in out and "Wong" not in out and "/projects" not in out and "Level the floor first, then screed." in out,
   "a project or client link goes with its text; the sentence around it stays")
out, dropped = wiki.clean_text("> **Hard rule.** Never tile before the flood test passes.\n\n> 📷 Image needed [IMG-1]")
ok("Never tile before the flood test" in out and "IMG" not in out and dropped.get("image") == 1,
   "a rule callout stays; an image callout goes; the reasons are counted")
facts = wiki.key_facts(text)
words = sum(len(f.split()) for f in facts)
ok(facts and words <= wiki.FACT_WORDS, f"key facts within {wiki.FACT_WORDS} words ({words})")
ok(any("150mm" in f for f in facts) and any("24 hours" in f for f in facts), "key facts favour sizes and times")
ok(all(len(f.split()) >= 4 for f in facts) and not any(f.endswith(":") for f in facts), "no fragments")

# The digest: least used first, within the budget.
big = [{"slug": f"a{n:02d}", "title": f"Article {n}", "domain": "Carpentry", "updated_at": f"2026-09-{n % 28 + 1:02d}",
        "body_md": "\n".join(f"- Rule {n}.{k}: set the board {k * 10 + 300}mm from the floor, always before tiling."
                             for k in range(12))} for n in range(60)]
dtext, picked = wiki.digest(big)
ok(0 < wiki.tokens_of(dtext) <= wiki.DIGEST_TOKENS and 5 < len(picked) < 60,
   f"the digest keeps to its budget ({wiki.tokens_of(dtext)} tokens, {len(picked)} of 60 articles)")
used = {a["slug"]: ["Made before"] for a in big[:59]}
dtext2, picked2 = wiki.digest(big, used)
ok(dtext2.startswith("[wiki:a59]"), "the least used article goes first")
ok("already in the planner: \"Made before\"" in dtext2, "a used article says what was made from it")
_, picked3 = wiki.digest(big, skip={"a59"})
ok("a59" not in picked3, "an article with a suggestion waiting isn't offered again")
ok(all(set(p) == {"slug", "title", "domain", "facts", "brief"} for p in picked.values()), "each pick has its facts and brief")

# ── A whole topics run (stub Claude, fake planner) ────────────────────────────

import catalog, cloud_sync, content_plan, footage_ideas, topics
cloud_sync._content_plan_online = cloud_sync._carousels_online = True
cloud_sync._hook_options_online = False
footage_ideas._inbox_busy = lambda: False


class Api:
    def __init__(self):
        self.t = {"projects": [{"id": 1, "name": "Topics", "folder": "Topics", "brief": "", "status": "active", "standing": "topics"},
                               {"id": 2, "name": "Acacia Residence", "folder": "Acacia", "brief": "", "status": "active", "standing": None}],
                  "ideas": [{"id": 10, "project_id": 2, "arc_id": None, "title": "Our favourite kitchen handles", "hook": "",
                             "status": "new", "decision": "go", "post_type": "ideas", "format": "talking_head", "suggestion": None}],
                  "arcs": [], "shots": [], "takes": [], "client_questions": [], "profiles": [], "reference_videos": [],
                  "site_days": [], "phases": [], "content_plan": []}
        self.n = 100

    def select(self, table, params=None):
        rows = self.t.get(table, [])
        for k, v in (params or {}).items():
            if isinstance(v, str) and v.startswith("eq.") and k not in ("select", "order"):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return [dict(r) for r in rows]

    def insert(self, table, rows, on_conflict=None, resolution=None, returning=True):
        out = []
        for r in rows if isinstance(rows, list) else [rows]:
            self.n += 1
            out.append({"id": self.n, **r})
            self.t.setdefault(table, []).append(out[-1])
        return [dict(r) for r in out]

    def update(self, table, filters, values):
        return []


api = Api()
content_plan.month = lambda api: {"month": "2026-10", "ends_on": "2026-10-31",
                                  "kinds": {"knowhow": {"short": 3}, "ideas": {"short": 1}, "offer": {"short": 0}}}
SEEN = []


def slide(role, heading, body):
    return {"role": role, "heading": heading, "body": body, "picture": ""}


def suggestion(title, slug, source="wiki", hook="Your tiles shouldn't end in slivers"):
    return {"title": title, "hook": hook, "hook_options": [], "post_type": "knowhow", "format": "carousel",
            "why_now": "Fills October's Know-how gap", "source": source, "clip_id": 0, "start": 0, "end": 0,
            "question_id": 0, "wiki_slug": slug, "purpose": "Homeowners about to tile, so they ask for a layout first.",
            "cta": "Save this for your reno", "caption": "Plan the cuts first. #tiling #renovation #singapore",
            "est_seconds": 30, "shots": [],
            "slides": [slide("cover", hook, ""), slide("point", "Start from the centre", "Then work outwards to both walls."),
                       slide("point", "No slivers", "Keep every cut piece above 150mm on a 600x600mm tile."),
                       slide("ask", "Save this", "")]}


class Stream:
    def __init__(self, answer): self.answer = answer
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self):
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=json.dumps(self.answer))],
                                     stop_reason="end_turn", model="claude-opus-5-5",
                                     usage=types.SimpleNamespace(input_tokens=9000, output_tokens=3000,
                                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


class Claude:
    def __init__(self, answer): self.answer = answer; self.beta = types.SimpleNamespace(messages=self)
    def stream(self, **req):
        SEEN.append(req)
        return Stream(self.answer(req) if callable(self.answer) else self.answer)


def answer(req):
    user = req["messages"][0]["content"]
    slug = "tile-layout-basics" if "[wiki:tile-layout-basics]" in user else ""
    return {"suggestions": [suggestion("Tile cuts that look right", slug),
                            suggestion("Socket heights that work", "made-up-slug", hook="Where your sockets should really sit")],
            "notes": ""}


conn = catalog.connect()
out = topics.run(api, conn, "job", Claude(answer), now=datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc))
req = SEEN[-1]
prompt = req["system"] + "\n" + req["messages"][0]["content"]
ok(len(SEEN) == 1, "still one Claude call per run")
ok("[wiki:tile-layout-basics] Tile Layout Basics (Tiling & waterproofing)" in prompt and "150mm" in prompt
   and "[wiki:mounting-heights]" in prompt, "the prompt carries the wiki digest: tags, titles and facts")
ok("Urban Makers' wiki" in req["system"] and "wiki_slug" in req["system"], "the system prompt names the wiki as a source")
leaked = [s for s in ("Pretend Rise", "S$12", "$40", "Lamitak", "Mr Tan", "6123 4567", "tan.tiles", "S654321", "#07-15",
                      "#05-12", "Example Road", "Tampines", "Contoh", "tiler rate", "another firm", "IMG-901", "300mm up",
                      "Tile Shops Directory", "Stone One", "S111111", "Soft-close", "mark-up", "healthy margin")
          if s in prompt]
ok(not leaked, f"no dropped text reaches the prompt ({leaked or 'none'})")
digest_part = req["messages"][0]["content"].split("Urban Makers' wiki", 1)[1].split("Saved references", 1)[0]
ok("Acacia" not in digest_part, "the planner's project names never get into the digest")
ok(wiki.tokens_of(digest_part) <= wiki.DIGEST_TOKENS + 50, f"the digest adds about {wiki.tokens_of(digest_part)} tokens")
schema = req["output_config"]["format"]["schema"]["properties"]["suggestions"]["items"]
ok("wiki_slug" in schema["required"] and "wiki" in schema["properties"]["source"]["enum"], "the answer's shape has wiki_slug and source wiki")

ideas = {i["title"]: i for i in api.t["ideas"]}
w = ideas.get("Tile cuts that look right")
ok(w and w["suggestion"]["source"] == "wiki" and w["suggestion"]["wiki"] == {"slug": "tile-layout-basics", "title": "Tile Layout Basics"},
   "a wiki suggestion records its article (slug and title) in ideas.suggestion")
ok(w and w["suggestion"]["why"].startswith("From the wiki: Tile Layout Basics.") and len(w["suggestion"]["why"]) <= 300,
   "its card line says \"From the wiki: <title>\"")
ok(w and "From the wiki: Tile Layout Basics." in w["purpose"] and "150mm" in w["purpose"] and len(w["purpose"]) <= topics.PURPOSE_MAX
   and w["purpose"].startswith("Homeowners about to tile"), "its purpose (what Write slides reads) carries the key facts after Claude's purpose")
block = w["purpose"].split("may be used): ", 1)[1] if w else ""
ok(len(block.split()) <= wiki.FACT_WORDS + 2 and "Pretend" not in block and "$" not in block, "the facts on the idea: sanitised, at most ~120 words")
other = ideas.get("Socket heights that work")
ok(other and other["suggestion"]["source"] == "basics" and "wiki" not in other["suggestion"]
   and "From the wiki" not in other["purpose"], "an article Claude made up: no wiki source, nothing claimed")
ok(w and w["project_id"] == 1 and w.get("slides"), "a wiki carousel goes to the Topics project with its outline")
mem = topics.remembered(conn)
ok(any(m.get("wiki") == "tile-layout-basics" for m in mem), "the run remembers which article each suggestion came from")
st = topics.status(conn)
ok(st["wiki"]["on"] and st["wiki"]["articles"] == 4, "the planner's status says the wiki is on")

# The next run: the article with a suggestion waiting isn't offered; once decided, it goes after the unused ones.
SEEN.clear()
api.t["ideas"] = [i for i in api.t["ideas"] if i["title"] != "Socket heights that work"]
ctx = topics.context(api, conn)
ok("tile-layout-basics" not in ctx["wiki"] and "mounting-heights" in ctx["wiki"],
   "an article whose suggestion is still waiting isn't offered again")
w["decision"] = "go"
ctx = topics.context(api, conn)
ok(ctx["wiki_text"].startswith("[wiki:mounting-heights]") and "already in the planner: \"Tile cuts that look right\"" in ctx["wiki_text"],
   "once decided, the used article goes after the unused one and says what was made from it")
dup = {"suggestions": [suggestion("Tile cuts that look right", "tile-layout-basics")], "notes": ""}
picks, skipped = topics.clean(dup, ctx)
ok(not picks and skipped and "already planned" in skipped[0]["why"], "a post already in the planner isn't suggested again")

# Without the wiki the prompt says so and nothing else changes.
settings.WIKI_URL = ""
cache.unlink()
ctx = topics.context(api, conn)
ok(ctx["wiki"] == {} and "Urban Makers' wiki (the team's trade knowledge" in topics.prompt(ctx) and "(none)" in topics.prompt(ctx),
   "no wiki: the prompt's wiki section says (none)")
ok(not any(JWT in m or ANON in m for m in LOGS), "after the whole run, still no log line with the token or the key")
server.shutdown()
finish()
