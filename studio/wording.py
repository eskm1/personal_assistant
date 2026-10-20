"""
The words viewers read: post captions, hooks and on-screen titles, carousel slides, the drawn
scenes' text, suggested posts and footage ideas. They should sound like Bryan, not a machine.
Built on igkit (Jake Schincariol's instagram-agent-skill, MIT; see igkit/README.md).

  tidy(text)        the safe fixes, always: invisible characters and typography (an em dash
                    becomes a comma, curly quotes straight). Links, hashtags, @handles, number
                    ranges ("3-4") and Chinese punctuation are left as they are. Words are never
                    swapped by code: that can change the meaning.
  guide(...)        what the prompts that write those words are told: Bryan's voice file
                    (voice_file.py), the "never write these" list (igkit/slop.json, minus the
                    words in Studio/humanize_allow.txt), the caption rule, the hook formulas.
  polish(fields)    tidy() every field; then, with STUDIO_HUMANIZE on (the default), the fields
                    that still read machine-written (stock words or the shapes models default to:
                    detect.py's slop and structure checks) get ONE short Claude rewrite,
                    and each field keeps whichever version scores better. A rewrite that drops a
                    hashtag, a link, a number or a [check], adds a figure or a name, or changes
                    the language is thrown away. Never raises: the text as tidied is the fallback.
  pick_hook(...)    three hook options (the 26 formulas of igkit/hooks.json) scored with
                    hookscore.py; the best is the hook, the scores are kept (ideas.hook_options).
  hook_timing(...)  beats.py's check on a draft's spoken hook: does it land inside 3 seconds?
  caption_check()   what the feed shows before "... more", and caption.py's useful findings.

Chinese text gets the safe fixes only (the checks read English).
"""
import json
import logging
import re

import igkit
import settings
from igkit import beats, caption as caption_lint, detect, hookscore, humanize

log = logging.getLogger("studio.wording")

CJK = humanize.CJK_RE
# Structural tells that don't trigger a rewrite: a hashtag block is the caption lint's job, and a
# list of three is often just three things (tiles, grout and screed). Both stay in the prompts.
NO_REWRITE_FOR = {"hashtag-wall", "triad"}
# What triggers the rewrite is the panel's stock-word and shape checks (what a rewrite can fix
# without inventing anything). The panel's overall score only picks the better version: on Bryan's
# own site talk it reads "FLAGGED" (short even sentences, few contractions), so it can't be the trigger.

SEED_ALLOW = """# Words and phrases Studio's de-AI filter never flags, one per line (Bryan's own words: trade
# terms he really says, the content plan's own asks). Lines starting with # are notes.
# Studio reads this file every time; delete a line to have Studio avoid that word again.
hack
seamless
showcase
follow for more
"""


# ── The safe fixes ────────────────────────────────────────────────────────────

def tidy(text):
    """The text with invisible characters and machine typography fixed (anything else, as is)."""
    if not isinstance(text, str) or not text.strip():
        return text
    out, report = humanize.humanize(text, igkit.lexicon(), lexical=False)
    if not report["invisible"] and not report["typographic"]:
        return text
    return out.strip()


def tidy_all(obj):
    """tidy() every string inside a dict / list (a slide, a scene); keys stay as they are."""
    if isinstance(obj, str):
        return tidy(obj)
    if isinstance(obj, list):
        return [tidy_all(v) for v in obj]
    if isinstance(obj, dict):
        return {k: tidy_all(v) for k, v in obj.items()}
    return obj


def chinese(text) -> bool:
    return bool(CJK.search(text or ""))


# ── Bryan's own words (the allowlist) ─────────────────────────────────────────

def allowed() -> list[str]:
    """Studio/humanize_allow.txt: words and phrases never flagged (made with SEED_ALLOW first)."""
    path = settings.HUMANIZE_ALLOW
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SEED_ALLOW, encoding="utf-8")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning("Couldn't read the allowlist %s: %s", path, e)
        lines = SEED_ALLOW.splitlines()
    out = []
    for line in lines:
        line = " ".join(line.split()).strip().lower()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


def _seed_allowed() -> list[str]:
    return [x.strip().lower() for x in SEED_ALLOW.splitlines() if x.strip() and not x.startswith("#")]


def _lex(allow: list[str] | None = None) -> dict:
    """slop.json without the allowed words, and without the tells Studio leaves to other checks."""
    lex = igkit.lexicon()
    allow = set(allowed() if allow is None else allow)
    return {**lex, "words": [e for e in lex["words"] if e["find"].lower() not in allow],
            "phrases": [e for e in lex["phrases"] if e["find"].lower() not in allow],
            "structures": [s for s in lex["structures"] if s["id"] != "hashtag-wall"]}


def _mask(text: str) -> str:
    """The text with Bryan's allowed phrases blanked out, so no tell is found inside one."""
    for term in allowed():
        text = re.sub(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b", "x", text, flags=re.I)
    return text


# ── Scoring ───────────────────────────────────────────────────────────────────

def _pattern(find: str):
    return re.compile(r"\b" + re.escape(find).replace(r"\ ", r"\s+") + r"\b", re.IGNORECASE)


def score(text: str) -> dict:
    """detect.py's panel on one text, with what it found: {human (0-100, higher reads more
    human), verdict, slop (stock words found), tells [{id, name}], words, skip (Chinese / empty)}."""
    text = text or ""
    if not text.strip() or chinese(text):
        return {"skip": True, "human": None, "verdict": None, "slop": [], "tells": [], "words": 0}
    lex = _lex()
    masked = _mask(text)
    _, overall, verdict = detect.run(masked, lex)
    slop = sorted({e["find"] for e in lex["words"] + lex["phrases"] if _pattern(e["find"]).search(masked)})
    # "delve into" also matches "delve": keep the longer one only.
    slop = [s for s in slop if not any(o != s and s in o for o in slop)]
    tells = []
    for s in lex["structures"]:
        try:
            if re.search(s["regex"], masked, re.MULTILINE):
                tells.append({"id": s["id"], "name": s["name"]})
        except re.error:
            continue
    return {"skip": False, "human": round(overall, 1), "verdict": verdict, "slop": slop, "tells": tells,
            "words": len(detect.words(masked))}


def problems(s: dict) -> list[str]:
    """What a rewrite should fix, in plain words ([] = nothing)."""
    if s.get("skip"):
        return []
    out = [f'uses "{w}"' for w in s["slop"]]
    out += [f"the shape {t['name']}" for t in s["tells"] if t["id"] not in NO_REWRITE_FOR]
    return out


def _rank(s: dict) -> tuple:
    """Smaller is better: things to fix, then the panel's score."""
    if s.get("skip"):
        return (0, 0)
    return (len(problems(s)), -(s["human"] or 0))


# ── The "never write these" list, for the prompts ─────────────────────────────

def never_text(allow: list[str] | None = None) -> str:
    """The "never write these" block (Bryan's allowlist taken out; allow = another list)."""
    allow = allowed() if allow is None else allow
    lex = _lex(allow)
    words = ", ".join(e["find"] for e in lex["words"])
    phrases = "; ".join(f'"{e["find"]}"' for e in lex["phrases"])
    shapes = "; ".join(s["name"] for s in lex["structures"])
    return ("Never write these in anything a viewer reads (captions, hooks, titles, slides, on-screen text): "
            "they read as machine-written. Say it plainly instead.\n"
            f"- Words: {words}.\n- Phrases: {phrases}.\n- Shapes: {shapes}; a hashtag block of more than 5.\n"
            "- Typing: no em dashes (use a comma or a full stop), no curly quotes, no … (three dots), "
            "no invisible characters.\n- Sound like Bryan talking: contractions, plain words, sentences of "
            "different lengths, the concrete thing (the material, the step, the problem) over adjectives."
            + (f"\n- Fine for Bryan (his own words): {', '.join(allow)}." if allow else ""))


CAPTION_RULE = """Post captions: the feed shows about the first 125 characters, then "… more". So the first \
line must earn the tap on its own: the post's point or its ask, plainly, in under 125 characters; never a \
greeting, a hashtag, an @ or an emoji first. Then short paragraphs. One ask only. No links (they can't be \
tapped). 3 to 5 specific hashtags on the last line (never #fyp, #viral or #explore), none above the fold."""


def hook_formulas() -> str:
    """The 26 formulas, one line each: number, name, template."""
    return "\n".join(f"{h['id']}. {h['name']}: {h['template']}" for h in igkit.hooks()["hooks"])


HOOK_RULE = """hook_options: three hooks for this post, each on a different formula from the list below that \
genuinely fits it: text is the first line (what Bryan says in the first 3 seconds, or a carousel's cover \
line), at most 12 words, something Bryan would really say (quote his words when the transcript has them); \
formula is the formula's name as listed. hook: the one of the three you'd pick, word for word. A figure \
only when Bryan said it or the team wrote it; never a price, a discount or a promotion; no greeting, no \
"in this video", no hashtag or emoji. Studio scores the three and keeps the strongest.
Hook formulas (name: template):
{formulas}"""


def hook_rule() -> str:
    return HOOK_RULE.replace("{formulas}", hook_formulas())


def guide(caption: bool = False, hooks: bool = False) -> str:
    """The block a prompt that writes viewer-facing words gets: Bryan's voice, the never list,
    and (as asked) the caption rule and the hook formulas."""
    import voice_file
    parts = []
    voice = voice_file.compact()
    if voice:
        parts.append(f"{VOICE_HEAD}\n<voice>\n{voice}\n</voice>")
    parts.append(never_text())
    if caption:
        parts.append(CAPTION_RULE)
    if hooks:
        parts.append(hook_rule())
    return "\n\n".join(parts)


VOICE_HEAD = "How Bryan sounds (his voice file: every word a viewer reads should sound like him):"

TS_FILE = settings.HERE / "cloud" / "supabase" / "functions" / "_shared" / "wording.ts"


def ts_module() -> str:
    """The Edge Functions' copy (functions/_shared/wording.ts): the never list with the seed
    allowlist, the caption rule and the hook formulas. The Studio machine pushes the current
    list (with Bryan's allowlist) and his voice file to the context table; these are the
    fallbacks until then. Generated: write_ts() after a change; the igkit suite checks it."""
    return ("// GENERATED by studio/wording.py (ts_module) from studio/igkit: slop.json and hooks.json\n"
            "// (instagram-agent-skill, MIT, see studio/igkit/README.md). Don't edit by hand: change the\n"
            "// Python, then: python -c \"import wording; wording.write_ts()\" (from studio/).\n"
            "// The Studio machine pushes the current never list (with Bryan's own allowlist) and his voice\n"
            "// file into the context table (keys \"wording\", \"voice\"); these are the fallbacks until then.\n\n"
            f"export const NEVER_WRITE = {json.dumps(never_text(_seed_allowed()), ensure_ascii=False)};\n\n"
            f"export const CAPTION_RULE = {json.dumps(CAPTION_RULE, ensure_ascii=False)};\n\n"
            f"export const HOOK_RULE = {json.dumps(hook_rule(), ensure_ascii=False)};\n\n"
            f"export const VOICE_HEAD = {json.dumps(VOICE_HEAD, ensure_ascii=False)};\n")


def write_ts() -> None:
    TS_FILE.write_text(ts_module(), encoding="utf-8")


# ── The rewrite pass ──────────────────────────────────────────────────────────

_NUM = re.compile(r"\d[\d,.]*")
_TAG = re.compile(r"(?<![\w&])#[\w]+")
_LINK = re.compile(r"https?://\S+|www\.\S+|(?<![\w&])@[\w.]*\w")
_MARKERS = ("[check]", "[placeholder]")
_CAP = re.compile(r"(?<![.!?:\n]\s)(?<!^)\b[A-Z][a-z]{2,}\b")
_COMMON_CAPS = {"Bryan", "Urban", "Makers", "Singapore", "HDB", "Instagram", "Free", "Save", "Follow", "Book",
                "Send", "Swipe", "Tap", "Comment", "Which", "What", "Why", "How", "When", "The", "This", "Your"}


def safe(old: str, new: str) -> str | None:
    """Why a rewrite can't replace the original (None = it can)."""
    if not new or not new.strip():
        return "empty"
    if len(new) > max(len(old) * 1.6, len(old) + 80):
        return "much longer"
    if chinese(new) != chinese(old):
        return "changed language"
    if set(_NUM.findall(new)) - set(_NUM.findall(old)):
        return "added a figure"
    if {t.lower() for t in _TAG.findall(new)} != {t.lower() for t in _TAG.findall(old)}:
        return "changed the hashtags"
    if set(_LINK.findall(new)) != set(_LINK.findall(old)):
        return "changed a link or handle"
    if any(old.count(m) != new.count(m) for m in _MARKERS):
        return "lost a [check]"
    if old.count("*") != new.count("*"):
        return "lost the italic mark"
    names = {w for w in _CAP.findall(new) if w not in _CAP.findall(old) and w not in _COMMON_CAPS
             and w.lower() not in old.lower()}
    if names:
        return f"added a name ({', '.join(sorted(names)[:3])})"
    return None


_REWRITE_SYSTEM = """You make the words of Urban Makers' Instagram posts (a Singapore renovation \
company run by Bryan) sound like Bryan wrote them, not a machine. You get a few fields, each with what \
reads machine-written in it. Rewrite each one on its own:
- Fix exactly what is flagged; keep its meaning, its facts and every number, offer, hashtag, link, \
@handle, [check] and [placeholder] exactly as they are. Keep a word marked with *asterisks* marked.
- Keep its language (English stays English) and its kind and length: a title stays a short title, a \
caption keeps its first line's job (the point or the ask) and its hashtags on the last line.
- Never add a price, a figure, a promotion, an offer, a place name, a client or anything about quotes.
- Plain words, contractions, sentences of different lengths, no em dashes, no curly quotes.
Return every field you were given, by its key.

{guide}"""

_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"fields": {"type": "array", "items": {
        "type": "object", "properties": {"key": {"type": "string"}, "text": {"type": "string"}},
        "required": ["key", "text"], "additionalProperties": False}}},
    "required": ["fields"], "additionalProperties": False,
}


def _rewrite(asks: dict[str, tuple[str, list[str]]], kind: str, ref: str, client=None) -> dict[str, str]:
    """One Claude call (effort low) over the flagged fields: {key: new text}. Cost as `kind`."""
    import anthropic

    import llm
    import usage
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    user = "Fields to rewrite:\n\n" + "\n\n".join(
        f"key: {key}\nflagged: {'; '.join(found)}\ntext:\n<<<\n{text}\n>>>" for key, (text, found) in asks.items())
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=6000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _REWRITE_SCHEMA}},
        system=_REWRITE_SYSTEM.replace("{guide}", guide()), messages=[{"role": "user", "content": user}])
    usage.record(kind, ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"the rewrite stopped ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    return {f["key"]: f["text"] for f in answer.get("fields", []) if isinstance(f, dict)
            and isinstance(f.get("key"), str) and isinstance(f.get("text"), str) and f["key"] in asks}


def polish(fields: dict[str, str], kind: str, ref: str, client=None, limits: dict[str, int] | None = None,
           rewrite: bool | None = None) -> tuple[dict[str, str], dict]:
    """fields {key: text} tidied, and (STUDIO_HUMANIZE) the machine-sounding ones rewritten once.
    Returns (fields, report {flagged, rewritten: {key: {before, after}}, kept: {key: why}, error})."""
    out = {k: tidy(v) if isinstance(v, str) else v for k, v in fields.items()}
    report = {"flagged": {}, "rewritten": {}, "kept": {}, "error": None}
    asks, scores = {}, {}
    for key, text in out.items():
        if not isinstance(text, str) or not text.strip():
            continue
        s = scores[key] = score(text)
        found = problems(s)
        if found:
            asks[key] = (text, found)
            report["flagged"][key] = found
    if not asks or not (settings.HUMANIZE if rewrite is None else rewrite):
        return out, report
    try:
        new = _rewrite(asks, kind, ref, client)
    except Exception as e:  # the tidied text stands; a rewrite never stops the work
        log.warning("The wording rewrite for %s didn't work (%s); the text stays as written", ref, e)
        report["error"] = f"{type(e).__name__}: {e}"[:300]
        return out, report
    for key, (old, _) in asks.items():
        text = tidy((new.get(key) or "").strip())
        if limits and key in limits:
            text = text[:limits[key]]
        why = safe(old, text) if key in new else "not returned"
        if why is None and _rank(score(text)) >= _rank(scores[key]):
            why = "not better"
        if why:
            report["kept"][key] = why
            continue
        out[key] = text
        report["rewritten"][key] = {"before": old, "after": text}
    if report["rewritten"]:
        log.info("Wording for %s: rewrote %s", ref, ", ".join(report["rewritten"]))
    return out, report


_IDEA_TEXT = ("hook", "caption", "cta")
_PART_TEXT = {"slides": ("heading", "body"), "shots": ("line", "on_screen")}


def polish_ideas(ideas: list[dict], kind: str, ref: str, client=None) -> dict:
    """polish() over a batch of new ideas in place, in one rewrite call: each idea's hook, caption,
    cta, its slides' heading and body and its shots' line and on-screen text. Returns the report."""
    fields = {}
    for n, idea in enumerate(ideas):
        for k in _IDEA_TEXT:
            if isinstance(idea.get(k), str):
                fields[f"{n}.{k}"] = idea[k]
        for part, keys in _PART_TEXT.items():
            for m, row in enumerate(idea.get(part) or []):
                for k in keys:
                    if isinstance(row, dict) and isinstance(row.get(k), str):
                        fields[f"{n}.{part}.{m}.{k}"] = row[k]
    hooks = [idea.get("hook") for idea in ideas]
    out, report = polish(fields, kind, ref, client)
    for key, text in out.items():
        bits = key.split(".")
        if len(bits) == 2:
            ideas[int(bits[0])][bits[1]] = text
        else:
            ideas[int(bits[0])][bits[1]][int(bits[2])][bits[3]] = text
    for idea, old in zip(ideas, hooks):  # a rewritten hook stays one of its options, scored again
        if idea.get("hook") != old and isinstance(idea.get("hook_options"), list):
            for o in idea["hook_options"]:
                if isinstance(o, dict) and o.get("text") == old:
                    o.update(text=idea["hook"], score=hook_score(idea["hook"]))
    return report


# Keys of a drawn scene (motion.py) that hold words a viewer reads; the rest are settings.
_SCENE_SETTINGS = {"type", "tone", "size", "kind", "style", "icon", "icons", "look", "role", "side", "align",
                   "color", "d", "path", "paths", "image", "file", "unit_kind", "layout", "shape"}


def scene_texts(scene, path: str = "") -> dict[str, str]:
    """A scene's words as {path: text} ("layers.0.label"), for polish()."""
    out = {}
    if isinstance(scene, dict):
        for k, v in scene.items():
            if k in _SCENE_SETTINGS:
                continue
            out.update(scene_texts(v, f"{path}{k}."))
    elif isinstance(scene, list):
        for i, v in enumerate(scene):
            out.update(scene_texts(v, f"{path}{i}."))
    elif isinstance(scene, str) and scene.strip() and not re.fullmatch(r"[\d\s.,%/x×-]*", scene):
        out[path.rstrip(".")] = scene
    return out


def set_scene_text(scene, path: str, text: str) -> None:
    parts = path.split(".")
    node = scene
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = text
    else:
        node[last] = text


# ── Hooks ─────────────────────────────────────────────────────────────────────

def hook_score(text: str) -> int | None:
    """hookscore.py's score (0-100), or None for a Chinese or empty hook (it reads English)."""
    text = (text or "").strip()
    if not text or chinese(text) or not hookscore.words(text):
        return None
    return round(hookscore.run(text)[1])


_FORMULAS = None


def _formula(name) -> str:
    global _FORMULAS
    if _FORMULAS is None:
        _FORMULAS = {h["name"].lower(): h["name"] for h in igkit.hooks()["hooks"]}
    name = re.sub(r"^\s*\d+[.)]?\s*", "", str(name or "")).strip()
    return _FORMULAS.get(name.lower(), "")


def pick_hook(options, hook: str = "", limit: int = 600) -> tuple[str, list[dict]]:
    """(the hook to keep, the options scored best first [{text, score, formula}]). The best score
    wins; a tie keeps Claude's own pick; with no usable options the hook stays as written."""
    hook = tidy((hook or "").strip())
    scored, seen = [], set()
    for o in options if isinstance(options, list) else []:
        if not isinstance(o, dict):
            continue
        text = tidy(str(o.get("text") or "").strip())[:limit]
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        scored.append({"text": text, "score": hook_score(text), "formula": _formula(o.get("formula"))})
        if len(scored) == 3:
            break
    if hook and hook.lower() not in seen and len(scored) < 3:
        scored.append({"text": hook[:limit], "score": hook_score(hook), "formula": ""})
    if not scored:
        return hook, []
    order = sorted(range(len(scored)), key=lambda i: (-(scored[i]["score"] if scored[i]["score"] is not None else -1),
                                                      scored[i]["text"].lower() != hook.lower(), i))
    ranked = [scored[i] for i in order]
    return ranked[0]["text"], ranked


# ── The spoken hook's timing (beats.py) ───────────────────────────────────────

_END = re.compile(r"[.!?。！？]$")


def hook_timing(words: list[dict]) -> dict | None:
    """A draft's spoken hook (its first sentence) timed with beats.py at Bryan's own pace in this
    draft: {text, words, seconds, over (past 3 s)}. None with no speech."""
    words = [w for w in words or [] if str(w.get("word", "")).strip()]
    if not words:
        return None
    first = []
    for i, w in enumerate(words):
        first.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if _END.search(str(w["word"]).strip()) or len(first) >= 30 or (nxt and nxt["start"] - w["end"] > 0.7):
            break
    text = " ".join(str(w["word"]).strip() for w in first)
    measured = max(first[-1]["end"] - first[0]["start"], 0.0)
    if chinese(text) or not beats.words(text):
        seconds = measured
    else:
        span = max(words[-1]["end"] - words[0]["start"], 0.5)
        wpm = min(max(len(beats.words(" ".join(str(w["word"]) for w in words))) / span * 60, 120.0), 240.0)
        sheet = beats.analyse(text, wpm=wpm)
        seconds = sheet["beats"][0]["dur"] if sheet and sheet["beats"] else measured
    return {"text": text[:300], "words": len(first), "seconds": round(seconds, 1),
            "over": seconds > beats.HOOK_WINDOW}


# ── The caption's first line (caption.py) ─────────────────────────────────────

def caption_check(text: str) -> dict:
    """What the feed shows before "... more" and up to two useful findings in plain words:
    {visible, truncated, findings}."""
    text = (text or "").strip()
    if not text:
        return {"visible": "", "truncated": False, "findings": []}
    a = caption_lint.analyse(text)
    by = {c["check"]: c for c in a["checks"]}
    tags, cut = a["hashtags"], a["truncate_at"]
    found = []

    def add(rank, words):
        found.append((rank, words))

    if by["LENGTH"]["status"] == "FAIL":
        add(0, f"It's over Instagram's {caption_lint.LIMIT:,} characters: cut {a['characters'] - caption_lint.LIMIT}.")
    first = by["FIRST LINE"]
    if first["status"] == "FAIL":
        add(1, "It starts with an empty line: the feed shows nothing worth tapping."
            if not a["first_line_chars"] else "It starts with a hashtag or @: lead with the point instead.")
    if by["HASHTAGS"]["status"] == "FAIL":
        add(2, f"{len(tags)} hashtags: Instagram counts 5 at most. Keep the best 3 to 5.")
    if by["LINKS"]["status"] == "WARN":
        add(3, "Links in a caption can't be tapped: put it in the bio.")
    first_line = text.split("\n", 1)[0]
    if by["TAG PLACEMENT"]["status"] == "WARN" and first["status"] != "FAIL" \
            and (a["truncated"] or any(t in first_line for t in tags)):
        add(4, "A hashtag shows before … more: move the tags to the end.")
    if first["status"] == "WARN":
        add(5, f"The first line runs past {cut} characters, so the feed cuts it mid-thought.")
    if by["HASHTAGS"]["status"] == "WARN":
        add(6, "Generic tags like #fyp or #viral say nothing: use topic tags.")
    if len(a["asks"]) > 1:
        add(7, f"It asks for {len(a['asks'])} things ({', '.join(a['asks'])}): pick one.")
    if by["HOOK IS CONCRETE"]["status"] == "WARN" and a["truncated"]:
        add(8, "Nothing concrete before … more (the point, a number, the thing): give people a reason to tap.")
    if not a["asks"]:
        add(9, "No ask: say the one thing people should do (save, follow, DM).")
    return {"visible": a["visible"], "truncated": a["truncated"],
            "findings": [w for _, w in sorted(found)[:2]]}
