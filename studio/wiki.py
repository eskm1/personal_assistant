"""
Urban Makers' wiki as a source of post ideas (Bryan, 2026-10-23: "the wiki has a large database of
knowledge, contractor specific insight, specific knowledge that we can also use to make some of the reels
and carousels and suggest topics whenever we don't have enough like site footage").

The wiki lives in the quote engine's database. Studio reads it read-only with a role that may make one
request only: GET <STUDIO_WIKI_URL>/rest/v1/wiki_articles?select=slug,title,domain,tags,body_md,updated_at
(the published articles; select=* is refused). That happens at most once a day; the answer is kept in
Studio/wiki.json and used from there when the database can't be reached. The token and the anon key go in
headers only and never into a log, an error or the database. Nothing here raises into its callers.

The wiki is written for the team; what Studio makes goes public on Instagram. So every article passes
sanitise() before any of its words reach a prompt, and only the trade facts are left (sizes, sequences,
checks, times, mounting heights, rules of thumb). It drops:
  - whole articles that are directories (a title with Directory, Contacts, Showrooms, Shot list, Open
    questions or Editorial; or a quarter or more of their lines, at least three, addresses or numbers);
  - links into the app ("[12 Example Road](/projects?open=...)"): a project or client link with its
    text, any other app link down to its text; an outside link keeps its text, a bare web address its line;
  - image placeholders ("> 📷 Image needed [IMG-...]", "to be inserted") and open checklist items;
  - any line, callout or section marked internal or commercial;
  - any line with money in it (S$, $, price, cost, charge, quote, discount, commission, margin, trade
    differential, retail, fee, budget, top-up, payment...): a heading about money loses the heading only;
  - addresses (a street with a number, Blk, unit numbers like #04-02, postcodes S123456, estates and
    towns), phone numbers and emails, case studies and project records, people's names after Mr/Mrs/Ms,
    and the planner's project names;
  - supplier and brand names (a line naming one; a section about brands, suppliers or showrooms), while
    generic material names (quartz, sintered stone, epoxy grout, laminate) stay;
  - facts marked unverified.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import settings

log = logging.getLogger("studio.wiki")

COLUMNS = "slug,title,domain,tags,body_md,updated_at"   # exactly these: the role refuses select=*
REFRESH_HOURS = 24          # the copy on disk is used for this long before the wiki is asked again
RETRY_MINUTES = 60          # after a failed read, the copy on disk is used and the wiki left alone this long
TIMEOUT = (5, 20)           # connect, read (seconds): it runs inside the sync
FACT_WORDS = 120            # an article's key facts, as they go in the digest and on the idea
DIGEST_TOKENS = 3500        # the whole digest in the topics prompt (estimated at 3.5 characters a token)
CHARS_PER_TOKEN = 3.5
MIN_FACTS = 2               # an article with fewer facts left has nothing to teach


class WikiError(RuntimeError):
    """The wiki couldn't be read, in plain words (never with the token or the key in it)."""

    def __init__(self, message: str):
        super().__init__(_scrub(message))


def _scrub(text) -> str:
    """Never let the token or the anon key into a message, a log or the database."""
    out = str(text)
    for secret in (settings.WIKI_JWT, settings.WIKI_ANON_KEY):
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    return out


# ── Reading the wiki ──────────────────────────────────────────────────────────

def available() -> str | None:
    """None when Studio can read the wiki, else why not (plain words)."""
    missing = [name for name, value in (("STUDIO_WIKI_URL", settings.WIKI_URL), ("STUDIO_WIKI_JWT", settings.WIKI_JWT),
                                        ("STUDIO_WIKI_ANON_KEY", settings.WIKI_ANON_KEY)) if not value]
    if missing:
        return "needs " + ", ".join(missing) + " in studio/.env on the Studio machine"
    url = settings.WIKI_URL.lower()
    if not (url.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost)[:/]", url + "/")):
        return "STUDIO_WIKI_URL must be an https address (the token never goes over plain http)"
    return None


def _endpoint() -> str:
    base = settings.WIKI_URL.rstrip("/")
    base = base[:-len("/rest/v1")] if base.endswith("/rest/v1") else base
    return base + "/rest/v1/wiki_articles"


def fetch() -> list[dict]:
    """One read of every article the role may see. Raises WikiError."""
    why = available()
    if why:
        raise WikiError(why)
    headers = {"apikey": settings.WIKI_ANON_KEY, "Authorization": f"Bearer {settings.WIKI_JWT}",
               "Accept": "application/json"}
    try:
        r = requests.get(_endpoint(), params={"select": COLUMNS}, headers=headers, timeout=TIMEOUT,
                         allow_redirects=False)
    except requests.Timeout:
        raise WikiError("the wiki didn't answer in time") from None
    except requests.RequestException as e:
        raise WikiError(f"can't reach the wiki ({type(e).__name__})") from None
    if r.status_code in (401, 403):
        raise WikiError(f"the wiki refused Studio's key (HTTP {r.status_code}): check STUDIO_WIKI_JWT and "
                        "STUDIO_WIKI_ANON_KEY")
    if r.status_code != 200:
        raise WikiError(f"the wiki answered HTTP {r.status_code}")
    try:
        rows = r.json()
    except ValueError:
        raise WikiError("the wiki's answer wasn't JSON") from None
    if not isinstance(rows, list):
        raise WikiError("the wiki's answer wasn't a list of articles")
    out = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("title"), str) and isinstance(row.get("body_md"), str):
            out.append({k: row.get(k) for k in COLUMNS.split(",")})
    return out


def _cache() -> Path:
    return settings.ROOT / "wiki.json"


def _load() -> dict:
    try:
        data = json.loads(_cache().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _cache()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)   # the raw articles hold the team's internal notes
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Couldn't keep the wiki's copy on disk: %s", e)


def _when(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def articles(now: datetime | None = None) -> list[dict]:
    """Every article as the wiki holds it (not yet sanitised): the copy on disk while it's under a day old,
    else read fresh; the copy on disk when that fails; [] when there's neither. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        data = _load()
        have = data.get("articles") if isinstance(data.get("articles"), list) else []
        fetched, tried = _when(data.get("fetched_at")), _when(data.get("tried_at"))
        if fetched and now - fetched < timedelta(hours=REFRESH_HOURS):
            return have
        if available():
            return have
        if tried and data.get("error") and now - tried < timedelta(minutes=RETRY_MINUTES):
            return have
        try:
            rows = fetch()
        except WikiError as e:
            log.warning("Couldn't read the wiki (%s); %s", e, f"using the copy from {data['fetched_at'][:16]}"
                        if have and data.get("fetched_at") else "no copy on disk yet")
            _save({**data, "articles": have, "tried_at": now.isoformat(timespec="seconds"), "error": str(e)[:300]})
            return have
        stamp = now.isoformat(timespec="seconds")
        _save({"fetched_at": stamp, "tried_at": stamp, "error": None, "articles": rows})
        log.info("Read %d wiki articles", len(rows))
        return rows
    except Exception as e:  # never into the caller: topics carries on without the wiki
        log.warning("The wiki's copy couldn't be used: %s", _scrub(f"{type(e).__name__}: {e}"))
        return []


def info() -> dict:
    """For the planner's status (laptop_status.info.topics.wiki): on or why not, how many articles, when read."""
    data = _load()
    return {"on": available() is None, "why": available(), "articles": len(data.get("articles") or []),
            "fetched_at": data.get("fetched_at"), "error": data.get("error")}


# ── The sanitiser ─────────────────────────────────────────────────────────────

_DIRECTORY = re.compile(r"\b(director(y|ies)|contacts?|address(es)?|showrooms?|vendor list|supplier list|"
                        r"open questions|editorial|shot list)\b", re.I)   # lists, and the wiki's own housekeeping
# Money: a currency sign anywhere, or a word about price.
_MONEY = re.compile(
    r"S\$|US\$|\$|\bSGD\b|\b(pric\w*|cost\w*|charges?|charged|chargeable|quot(?:e|es|ed|ing|ation\w*)|discount\w*|commission\w*|"
    r"margins?|trade differential|retail\w*|fees?|budget\w*|upsell\w*|top-?ups?|surcharge\w*|invoic\w*|gst|paynow|"
    r"payments?|pay|pays|paying|paid|rebates?|mark-?ups?|cheap\w*|afford\w*)\b", re.I)
_INTERNAL = re.compile(r"\*\*\s*internal\b|^\W*internal\b\s*[-–—:.]|\(internal\)|\binternal (only|use|note)\b|"
                       r"\bcommercial\b", re.I)
_IMAGE = re.compile(r"📷|\bimage needed\b|\[IMG-[^\]]*\]|\bto be inserted\b|\bphotos? needed\b", re.I)
_CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*\[[ xX]\]")
_UNVERIFIED = re.compile(r"\*verify\*|\(verify\)|\bunverified\b|\bnot yet verified\b", re.I)
_CASE = re.compile(r"\bcase stud(y|ies)\b|\breference project\b|\bproject records?\b|\bpast project", re.I)
_POSTCODE = re.compile(r"\b(?:S|Singapore)\s?\(?\d{6}\)?(?!\d)|(?<![\d,.])\d{6}(?![\d,.])", re.I)
_UNIT = re.compile(r"#\s?\d{1,3}\s?-\s?\d{1,4}")
_BLOCK = re.compile(r"\bBlk\b|\bblock\s+\d+[a-z]?\b", re.I)
_STREET = re.compile(
    r"\b\d{1,4}[A-Z]?,?\s+(?:[A-Z][\w'’]*\s+){1,4}(?:Ave(?:nue)?|Road|Rd|Street|St|Lane|Ln|Loop|Link|Drive|Dr|"
    r"Crescent|Cres|Close|Way|Walk|Place|Pl|Rise|Hill|Park|View|Terrace|Central|Heights?|Grove|Gardens?|Green|"
    r"Vale|Boulevard|Quay|Square|Sq)\b")
_PLACE = re.compile(
    r"\b(?:Jalan|Jln|Lorong|Lor|Taman|Kampong|Kg|Bukit|Tanjong|Ang Mo Kio|Bedok|Bishan|Boon Lay|Buona Vista|Choa Chu "
    r"Kang|Clementi|Defu|Dover|Geylang|Holland|Hougang|Joo Chiat|Jurong|Kallang|Katong|Kranji|Lavender|Mandai|Marine "
    r"Parade|Novena|Orchard|Pasir Ris|Punggol|Queenstown|Redhill|Sembawang|Sengkang|Senoko|Serangoon|Siglap|Tampines|"
    r"Tengah|Thomson|Tiong Bahru|Toa Payoh|Ubi|Woodlands|Yishun|Sentosa|Bugis|Marina Bay|Kembangan|Eunos|Paya Lebar|"
    r"Simei|Changi|Telok Blangah|Pasir Panjang)\b")
_PHONE = re.compile(r"\+65[\s-]?\d{4}[\s-]?\d{4}|(?<![\d,.])[3689]\d{3}[\s-]?\d{4}(?![\d,.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL = re.compile(r"https?://|www\.", re.I)
_PERSON = re.compile(r"\b(?:Mr|Mrs|Ms|Mdm|Madam|Miss)\.?\s+[A-Z]")
# Suppliers, brands and their product lines seen in the wiki and the trade (a name, never a material).
_BRANDS = re.compile(
    r"\b(?:Blum|H[aä]fele|EcoWear|Excel|Hager|MK|Schneider|Legrand|Jennings|Lamitak|EDL|Euro De Luxe|Admira|Arova|"
    r"Lo & Co|Buster & Punch|Armac Martin|Hendel|Lazada|Shopee|Taobao|Tandembox|Aventos|Muse|Matex|MediFresh|"
    r"Nippon|Weatherbond|Benjamin Moore|Dulux|Jotun|Vinilex|NutriBullets?|Gush|Sika|Mapei|Laticrete|Davco|Weber|"
    r"Bostik|Toto|TOTO|Grohe|Hansgrohe|Kohler|Duravit|Geberit|American Standard|Rinnai|Joven|Rheem|Ariston|"
    r"Philips|Panasonic|Mitsubishi|Daikin|Caesarstone|Dekton|Neolith|Laminam|Corian|Silestone|Formica|Wilsonart|"
    r"IKEA|Ikea|Hettich|Grass|Salice|Kronospan|Egger|Rehau|Teka|Bosch|Electrolux|Fujioh|Elica|Smeg|Samsung|LG)\b"
    r"|[™®]")
# A heading like these drops its whole section; a heading about money drops only itself.
_SECTION_OUT = re.compile(r"\b(brands?|suppliers?|showrooms?|vendors? list|directory|contacts?|case stud(y|ies)|"
                          r"internal|commercial)\b", re.I)
_RELATED = re.compile(r"^\s*(related|see also|companion)\s*:", re.I)
_APP_PRIVATE = re.compile(r"^/(projects?|clients?|leads?|quotes?|quotation|q|portal|vo|signoff|invoices?|hub)\b|open=",
                          re.I)
_MARKER = re.compile(r"\*\*\[[A-Z][A-Z /&-]*\]\*\*\s*|^\[[A-Z][A-Z /&-]*\]\s+")


def _links(line: str) -> str:
    """Links as plain words: an app link to a project or client goes with its text (and its brackets), any
    other app link keeps its text, an outside link keeps its text, a wiki link [[Title]] its title."""
    def app(m):
        return "" if _APP_PRIVATE.search(m.group(2)) else m.group(1)
    line = re.sub(r"\s*\(\s*\[([^\]]*)\]\((/[^)\s]*)\)\s*\)", lambda m: "" if _APP_PRIVATE.search(m.group(2))
                  else f" ({m.group(1)})", line)
    line = re.sub(r"\[([^\]]*)\]\((/[^)\s]*)\)", app, line)
    line = re.sub(r"\[([^\]]+)\]\((?:https?://|mailto:)[^)\s]*\)", r"\1", line)
    line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", line)
    line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
    line = re.sub(r"\(\s*\)", "", line)
    return re.sub(r"\s+([.,;:])", r"\1", line)


def _names_pattern(names) -> re.Pattern | None:
    words = sorted({n.strip() for n in names or () if isinstance(n, str) and len(n.strip()) >= 4}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None


def _private(line: str, names: re.Pattern | None) -> str | None:
    """Why this line may never reach a prompt (a short reason), or None when it may."""
    for why, pattern in (("internal", _INTERNAL), ("image", _IMAGE), ("checklist", _CHECKBOX),
                         ("unverified", _UNVERIFIED), ("case", _CASE), ("money", _MONEY), ("address", _POSTCODE),
                         ("address", _UNIT), ("address", _BLOCK), ("address", _STREET), ("address", _PLACE),
                         ("phone", _PHONE), ("email", _EMAIL), ("web", _URL), ("person", _PERSON),
                         ("brand", _BRANDS)):
        if pattern.search(line):
            return why
    if names and names.search(line):
        return "name"
    return None


def _heading(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def clean_text(body: str, names=()) -> tuple[str, dict]:
    """The article's text with only what may go public left, and {reason: lines dropped}."""
    pattern = _names_pattern(names)
    dropped: dict[str, int] = {}
    def drop(why, n=1):
        dropped[why] = dropped.get(why, 0) + n
    lines = (body or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    skip_level = 0            # inside a dropped section: until a heading at this level or above
    quote: list[str] = []     # the callout being read (consecutive "> " lines)
    table: list[str] = []
    def flush_quote():
        if not quote:
            return
        whole = " ".join(quote)
        if _INTERNAL.search(whole) or _IMAGE.search(whole):
            drop("internal" if _INTERNAL.search(whole) else "image", len(quote))
        else:
            for q in quote:
                why = _private(q, pattern)
                drop(why) if why else out.append(q)
        quote.clear()
    def flush_table():
        if not table:
            return
        rows = [t for t in table if not re.match(r"^\|[\s|:-]+\|?\s*$", t)]
        header = rows[0] if rows else ""
        if not header or _private(header, pattern) in ("internal", "money", "brand", "address", "phone"):
            drop(_private(header, pattern) or "table", len(table))
        else:
            kept = [header]
            for row in rows[1:]:
                why = _private(row, pattern)
                drop(why) if why else kept.append(row)
            if len(kept) > 1:
                out.extend(kept)
        table.clear()
    for raw in lines:
        if re.match(r"^\s*(?:[-*+]\s+)?(?:\[\[[^\]]+\]\][\s·,.]*)+$", raw):
            drop("related")   # a line that is only links to other articles
            continue
        line = _MARKER.sub("", _links(raw.rstrip()))
        level = _heading(line)
        if skip_level:
            if level and level <= skip_level:
                skip_level = 0
            else:
                drop("section")
                continue
        if not line.lstrip().startswith(">"):
            flush_quote()
        if not line.lstrip().startswith("|"):
            flush_table()
        if level:
            why = _private(line, pattern)
            if _SECTION_OUT.search(line) or why not in (None, "money"):
                skip_level = level
                drop(why or "section")
            elif why == "money":
                drop("money")   # the heading only: its lines are checked one by one
            else:
                out.append(line)
            continue
        if line.lstrip().startswith(">"):
            quote.append(line)
            continue
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        if _RELATED.match(line):
            drop("related")
            continue
        why = _private(line, pattern)
        if why:
            drop(why)
            continue
        out.append(line)
    flush_quote()
    flush_table()
    # Headings with nothing left under them go too.
    tidy: list[str] = []
    for n, line in enumerate(out):
        level = _heading(line)
        if level:
            rest = next((x for x in out[n + 1:] if x.strip()), "")
            if not rest or 0 < _heading(rest) <= level:
                continue
        tidy.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(tidy)).strip()
    return text, dropped


def _slug(article: dict) -> str:
    slug = article.get("slug")
    if isinstance(slug, str) and slug.strip():
        return slug.strip()[:120]
    return re.sub(r"[^a-z0-9]+", "-", (article.get("title") or "").lower()).strip("-")[:120]


def sanitise(article: dict, names=()) -> dict | None:
    """The article as it may reach a prompt: {slug, title, domain, text, updated_at}, or None when none of it
    may (a directory, a title that isn't safe, nothing left)."""
    title = re.sub(r"\s+", " ", str(article.get("title") or "")).strip()
    if not title or _DIRECTORY.search(title):
        return None
    pattern = _names_pattern(names)
    # "Commercial" in a title is the kind of property (condo, MCST and commercial permits), not a marker.
    if _private(re.sub(r"\bcommercial\b", "", title, flags=re.I), pattern):
        return None
    text, dropped = clean_text(article.get("body_md") or "", names)
    contacts = dropped.get("address", 0) + dropped.get("phone", 0) + dropped.get("email", 0)
    lines = sum(1 for x in (article.get("body_md") or "").split("\n") if x.strip())
    if contacts >= 3 and contacts >= 0.25 * lines:
        return None   # mostly addresses and numbers: a directory whatever its title says
    if not text:
        return None
    domain = str(article.get("domain") or "General").strip()[:60]
    return {"slug": _slug(article), "title": title[:160], "domain": domain if not _private(domain, pattern) else "General",
            "text": text, "updated_at": article.get("updated_at")}


# ── Key facts ─────────────────────────────────────────────────────────────────

_UNITS = re.compile(r"\d[\d,.]*\s?(?:-\s?\d[\d,.]*\s?)?(?:mm|cm|m|kg|g|ft|in|days?|working days|hours?|hrs?|weeks?|"
                    r"months?|years?|°C?|%|A|W|V|mA|L|lux|K|mins?|minutes?|layers?|coats?)\b")
_RULE = re.compile(r"\b(never|always|must|before|after|check|rule|minimum|maximum|at least|at most|don't|do not|"
                   r"first|then|only|every|avoid|ensure|confirm|test)\b", re.I)


def _plain(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–—:;")


FACT_MAX = 45   # words in one fact: a longer one keeps its first sentences


def _fit(text: str, limit: int = FACT_MAX) -> str:
    """A fact cut to whole sentences within the limit (a single long sentence is kept whole)."""
    if len(text.split()) <= limit:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        if out and len((out + " " + sentence).split()) > limit:
            break
        out = f"{out} {sentence}".strip()
    return out


def facts(text: str) -> list[tuple[int, int, str]]:
    """The sanitised text as facts: [(position, score, fact)]. A table row, a list item (with the items
    nested under it, or the list a line ending in ":" leads into), a callout or a paragraph is one fact; one
    with a size, a time or a count, or a rule word, scores higher."""
    items = []   # {n, text, rule, indent (None = not a list item), lead}
    header = False
    for n, line in enumerate(text.split("\n")):
        s = line.strip()
        if not s or _heading(s):
            header = header and not _heading(s)
            if _heading(s):
                items.append(None)   # a heading ends any list
            continue
        if s.startswith("|"):
            cells = [_plain(c) for c in s.strip("|").split("|")]
            if not header:
                header = True
                continue
            cells = [c for c in cells if c and c not in ("-", "—", "–")]
            if len(cells) >= 2:
                items.append({"n": n, "text": cells[0] + ": " + ", ".join(cells[1:]), "rule": False, "indent": None,
                              "lead": False})
            continue
        header = False
        rule, indent = False, None
        if s.startswith(">"):
            s = s.lstrip("> ").strip()
            rule = bool(re.match(r"\**\s*hard rule", s, re.I))
            s = re.sub(r"^\**\s*(hard rule|house view|note|tip)\.?\**\s*", "", s, flags=re.I)
        m = re.match(r"^(\s*)(?:[-*+]|\d+\.)\s+", line)
        if m and not line.strip().startswith(">"):
            indent = len(m.group(1).replace("\t", "    "))
            s = line[m.end():]
        lead = s.rstrip(" *_").endswith(":")
        items.append({"n": n, "text": _plain(s), "rule": rule, "indent": indent, "lead": lead})
    # Children: the list under a lead line, the items nested under a list item.
    out, parent = [], None
    for it in items:
        if it is None:
            parent = None
            continue
        child = parent is not None and it["indent"] is not None and (
            (parent["lead"] and parent["indent"] is None) or
            (parent["indent"] is not None and it["indent"] > parent["indent"]))
        if child:
            parent["kids"].append(it["text"])
            continue
        parent = {**it, "kids": []}
        out.append(parent)
    facts_ = []
    for it in out:
        text_ = it["text"]
        kids = list(it["kids"])
        while kids:
            joined = f"{text_.rstrip(':')}: {'; '.join(k.rstrip('.') for k in kids)}." if it["lead"] else \
                f"{text_.rstrip('.')} ({'; '.join(k.rstrip('.') for k in kids)})."
            if len(joined.split()) <= FACT_MAX:
                text_ = joined
                break
            kids.pop()
        text_ = _fit(_plain(text_))
        if text_.endswith(":") or len(text_.split()) < 4:
            continue
        words = len(text_.split())
        score = 3 * bool(_UNITS.search(text_)) + min(len(_RULE.findall(text_)), 2) + 2 * it["rule"] - (words > 40)
        facts_.append((it["n"], score, ("Rule: " if it["rule"] else "") + text_))
    return facts_


def key_facts(text: str, words: int = FACT_WORDS) -> list[str]:
    """The strongest facts within the word budget, in the article's order."""
    picked, used = [], 0
    for n, score, fact in sorted(facts(text), key=lambda f: (-f[1], f[0])):
        size = len(fact.split())
        if used + size > words:
            continue   # a shorter one may still fit
        picked.append((n, fact))
        used += size
    return [f for _, f in sorted(picked)]


def brief(facts_: list[str]) -> str:
    """The facts as one line for an idea's text."""
    return "; ".join(f.rstrip(".") for f in facts_) + ("." if facts_ else "")


# ── The digest for topics ─────────────────────────────────────────────────────

def digest(rows: list[dict], used: dict | None = None, names=(), skip=(), tokens: int = DIGEST_TOKENS) -> tuple[str, dict]:
    """The wiki as the topics prompt reads it: the least used articles first, each its title, domain and key
    facts, within the token budget. used: {slug: [titles of posts already made from it]}; skip: slugs not to
    offer now (an open suggestion is waiting on them). Returns (text, {slug: {slug, title, domain, facts,
    brief}}); ("", {}) when there's nothing."""
    used = used or {}
    budget = int(tokens * CHARS_PER_TOKEN)
    pool = []
    for row in rows or []:
        try:
            a = sanitise(row, names)
        except Exception as e:  # one odd article never stops the rest
            log.warning("Skipped a wiki article: %s", _scrub(e))
            continue
        if not a or a["slug"] in skip:
            continue
        f = key_facts(a["text"])
        if len(f) < MIN_FACTS:
            continue
        score = sum(x[1] for x in facts(a["text"]))
        pool.append({**a, "facts": f, "brief": brief(f), "uses": len(used.get(a["slug"]) or []), "score": score})
    # The least used first; among those, the richest in facts, then the newest.
    pool.sort(key=lambda a: a["slug"])
    pool.sort(key=lambda a: str(a.get("updated_at") or ""), reverse=True)
    pool.sort(key=lambda a: (a["uses"], -a["score"]))
    parts, picked, size = [], {}, 0
    for a in pool:
        lines = [f"[wiki:{a['slug']}] {a['title']} ({a['domain']})"]
        if a["uses"]:
            lines.append("  already in the planner: " + "; ".join(f"\"{t}\"" for t in (used.get(a["slug"]) or [])[:3])
                         + " (a different point only)")
        lines += [f"  - {f}" for f in a["facts"]]
        block = "\n".join(lines)
        if size + len(block) + 2 > budget:
            continue
        parts.append(block)
        size += len(block) + 2
        picked[a["slug"]] = {k: a[k] for k in ("slug", "title", "domain", "facts", "brief")}
    return "\n\n".join(parts), picked


def tokens_of(text: str) -> int:
    """About how many tokens a text is (for the budget and the cost line)."""
    return int(len(text or "") / CHARS_PER_TOKEN + 0.5)
