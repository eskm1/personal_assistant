"""
The caption spell-check, before a draft renders: captions come straight from Whisper's words, and
Whisper mis-hears trade words ("MCSC" for MCST, "grounds" for grout). Two passes, both on text:

1. Bryan's own "wrong -> right" lines in the trade glossary (glossary.py), by code, free.
2. One short Claude call (text only, effort low, settings.SPELL_MODEL): the draft's words, the
   glossary and the brief; it names the words likely mis-heard and the right ones. Each answer is
   checked by code before it's used: never a change to a number, never a new name or term unless the
   glossary or the brief has it, never more than a few words, never a different-sounding word unless
   the glossary or brief backs it. A word already checked in an earlier render of this draft isn't
   sent again, so a fix round that only moves a cut costs nothing here.

What it finds becomes caption corrections (edl["corrections"], editor.attach_corrections): the
words stay as said, only the captions change. Its cost is recorded with the edit (kind "edit").
"""
import difflib
import json
import logging
import re

import settings

log = logging.getLogger("studio.spelling")

MAX_WORDS = 600       # a Reel has far fewer; a longer draft is checked up to here
MAX_FIX_WORDS = 3     # words one fix may replace
SIMILAR = 0.8         # how alike a fix must look when neither the glossary nor the brief backs it

_SCHEMA = {
    "type": "object",
    "properties": {"fixes": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "count": {"type": "integer"},
                       "to": {"type": "string"}, "why": {"type": "string"}},
        "required": ["index", "count", "to", "why"], "additionalProperties": False}}},
    "required": ["fixes"],
    "additionalProperties": False,
}

_SYSTEM = """You check the captions of an Instagram Reel for words the speech recogniser mis-heard. \
The speaker is Bryan, who runs Urban Makers, a renovation company in Singapore; he speaks English \
(sometimes Singlish) or Mandarin. You get the trade glossary (words and names spelt the way captions \
should show them), the brief for the post, and the captions word by word, numbered.

List only words that are clearly wrong for what he meant: a mis-heard trade word or acronym ("MCSC" \
for "MCST", "grounds" where he means "grout"), a misspelling, a glossary term spelt another way. For \
each: index = the first word's number, count = how many words it replaces (1 to 3), to = the right \
word(s), why = a few words.

Never change a number, what he means, his grammar or his Singlish, and never add a name or term \
that isn't in the glossary or the brief. If you're not sure a word is wrong, leave it. Most drafts \
have nothing to fix: then fixes is []."""


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", str(text or ""))


def _brief_words(production: dict, plan: dict) -> set[str]:
    text = " ".join(str(x or "") for x in (production.get("title"), production.get("idea"), plan.get("title"),
                                           plan.get("hook"), plan.get("angle"), plan.get("cta"),
                                           " ".join(plan.get("beats") or [])))
    return {_norm(w) for w in re.findall(r"[\w']+", text) if len(w) > 1}


def _brief_text(production: dict, plan: dict) -> str:
    parts = [f"Title: {plan.get('title') or production.get('title') or ''}"]
    if plan.get("hook"):
        parts.append(f"Hook: {plan['hook']}")
    if production.get("idea"):
        parts.append(f"Idea:\n{str(production['idea'])[:1500]}")
    return "\n".join(parts)


def safe(said: str, to: str, brief: set[str]) -> str | None:
    """The correction as captions should show it, or None when code won't trust it."""
    import glossary
    to = re.sub(r"\s+", " ", str(to or "").replace("*", "")).strip()
    if not to or len(to) > 40 or len(to.split()) > 4 or _norm(to) == _norm(said):
        return None
    if _digits(to) != _digits(said):
        return None  # never a number
    backed = glossary.known(to) or all(_norm(w) in brief for w in to.split() if _norm(w))
    if backed:
        return to
    # Not in the glossary or the brief: only a spelling of the same word, and never a name (a word
    # with a capital, given or taken: "Bryan" stays, "sofa" never becomes "Sophia").
    if any(w[:1].isupper() for w in to.split() + said.split()) and to.lower() != said.lower():
        return None
    return to if difflib.SequenceMatcher(None, _norm(said), _norm(to)).ratio() >= SIMILAR else None


def _keep_ending(said_last: str, to: str) -> str:
    """The caption keeps the punctuation the last word ended on ("MCSC." -> "MCST.")."""
    tail = re.search(r"[^\w']+$", said_last or "")
    return to + tail.group(0) if tail and not re.search(r"[^\w']$", to) else to


def corrections_for(words: list[dict], index: int, count: int, to: str, by: str) -> list[dict]:
    """The corrections that make words[index:index+count] read as `to` (all in one clip)."""
    span = words[index:index + count]
    if not span or any(w.get("clip_id") != span[0].get("clip_id") or "src" not in w for w in span):
        return []
    to = _keep_ending(span[-1]["word"], to)
    return ([{"clip_id": span[0]["clip_id"], "at": round(span[0]["src"], 2), "from": span[0]["word"], "to": to, "by": by}]
            + [{"clip_id": w["clip_id"], "at": round(w["src"], 2), "from": w["word"], "to": "", "by": by} for w in span[1:]])


def merge(edl: dict, found: list[dict], replace: bool = True) -> int:
    """Add corrections to the draft's (one per word: a new one for the same word wins when
    replace). Returns how many words changed."""
    have = {(int(c["clip_id"]), f"{float(c['at']):.2f}"): c for c in edl.get("corrections") or []}
    changed = 0
    for c in found:
        k = (int(c["clip_id"]), f"{float(c['at']):.2f}")
        if k in have and (not replace or have[k].get("to") == c["to"]):
            continue
        have[k] = c
        changed += 1
    edl["corrections"] = list(have.values())
    return changed


def glossary_fixes(words: list[dict]) -> list[dict]:
    """Bryan's "wrong -> right" lines, matched on one to four words in a row."""
    import glossary
    table = glossary.fixes()
    if not table:
        return []
    out, i = [], 0
    while i < len(words):
        for size in (4, 3, 2, 1):
            if i + size > len(words):
                continue
            said = " ".join(w["word"] for w in words[i:i + size])
            k = glossary.key(said)
            if k in table and said.strip(".,!?;: ") != table[k]:
                out += corrections_for(words, i, size, table[k], "glossary")
                i += size
                break
        else:
            i += 1
    return out


def _ask(words: list[dict], production: dict, plan: dict, client, ref: str) -> tuple[list[dict], float]:
    import anthropic

    import glossary
    import llm
    import usage
    lines, line = [], []
    for n, w in enumerate(words[:MAX_WORDS]):
        line.append(f"[{n}] {w['word']}")
        if re.search(r"[.!?。！？]['\"”’)]*$", w["word"]) or len(line) >= 24:
            lines.append(" ".join(line))
            line = []
    if line:
        lines.append(" ".join(line))
    user = ("Trade glossary: " + ", ".join(glossary.terms()) + "\n\nBrief:\n" + _brief_text(production, plan)
            + "\n\nCaptions, word by word:\n" + "\n".join(lines))
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.SPELL_MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
        system=_SYSTEM, messages=[{"role": "user", "content": user}])
    usd = usage.record("edit", ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"the spell-check stopped ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    return [f for f in answer.get("fixes") or [] if isinstance(f, dict)], usd


def run(edl: dict, cuts: list[dict], clips: dict[int, dict], production: dict, plan: dict,
        client=None, ref: str = "") -> dict:
    """Check the draft's caption words and put what's wrong in edl["corrections"]. Returns
    {"fixed": [{from, to, at}], "usd"}. Never raises: captions as heard beat no draft."""
    import editor
    out = {"fixed": [], "usd": 0.0}
    try:
        words = editor.timeline(editor.attach_corrections(cuts, edl.get("corrections")), clips)
        if not words:
            return out
        found = glossary_fixes(words)
        spell = edl.get("spell") or {}
        seen = {f"{c}:{float(t):.2f}" for c, t in spell.get("seen") or []}
        fresh = [w for w in words if f"{w['clip_id']}:{w['src']:.2f}" not in seen]
        if settings.SPELL_CHECK and fresh and (client or settings.ANTHROPIC_API_KEY):
            brief = _brief_words(production, plan)
            asked, usd = _ask(words, production, plan, client, ref)
            out["usd"] = usd
            for f in asked:
                i, n = f.get("index"), f.get("count") or 1
                if not isinstance(i, int) or not isinstance(n, int) or not 0 <= i < len(words) or not 1 <= n <= MAX_FIX_WORDS:
                    continue
                said = " ".join(w["word"] for w in words[i:i + n])
                to = safe(said, f.get("to"), brief)
                if to:
                    found += corrections_for(words, i, n, to, "spell")
                else:
                    log.info("Spell-check: left \"%s\" as heard (not trusted: \"%s\")", said, f.get("to"))
            seen |= {f"{w['clip_id']}:{w['src']:.2f}" for w in words}
            spell = {"seen": sorted(([int(k.split(":")[0]), float(k.split(":")[1])] for k in seen)),
                     "usd": round(float(spell.get("usd") or 0) + usd, 4)}
            edl["spell"] = spell
        if found:
            merge(edl, found, replace=False)  # a fix Studio's read made stays
            out["fixed"] = [{"from": c["from"], "to": c["to"], "at": c["at"]} for c in found if c["to"]]
            log.info("Caption spell-check: %s", "; ".join(f"{c['from']} -> {c['to']}" for c in out["fixed"]))
    except Exception as e:
        log.warning("The caption spell-check didn't run: %s: %s", type(e).__name__, e)
    return out
