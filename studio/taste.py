"""
The taste report (cloud/CONTRACT.md, "Taste report"): what the team's whole
reference library says, and what Urban Makers should change because of it.

From every analysed reference (the imported library and the ones added by hand)
Claude writes, in a few calls (map-reduce: one read per 80 posts, then one to
pull them together):

  1. Design: palettes, materials, lighting, camera work, framing, shot types,
     on-screen text and fonts (with the closest free fonts Studio can bundle),
     caption styling, carousel layouts;
  2. Content: formats, hook patterns (quoted), structures, topics, length and
     pace, calls to action, voice;
  3. Engagement: what the better-performing posts share, with the numbers
     measured here (stats(): per view or per follower where Instagram gave them)
     and the caveats;
  4. Recommendations for Urban Makers, ranked, by kind of post (content_plan.KINDS)
     and Reels vs carousels; the editor and carousel settings to change; a
     proposed style guide (a draft in style_guides) and a short "Taste" section
     for the playbook. Which posts stand out and why (design, content or
     engagement) is Claude's inference: the team didn't tag them.

Nothing changes until the owner approves the report in the planner: then the
style guide becomes the one in force and the taste section goes into the
playbook (style_study.apply_section, its own markers). The report is also kept
as Markdown in Studio/Style/taste-report-v<n>.md.
"""
import hashlib
import json
import logging
import math
import statistics
from pathlib import Path

import settings

log = logging.getLogger("studio.taste")

CHUNK = 80                     # analyses per "map" read
BASE_USD = 0.6                 # the final pulling-together call (estimate)
PER_CHUNK_USD = 0.3            # each map read (estimate)
MIN_REFERENCES = 10
TASTE_START, TASTE_END = "<!-- taste:start -->", "<!-- taste:end -->"
WHY = ("design", "content", "engagement")
KIND_KEYS = ("knowhow", "ideas", "onsite", "finished", "offer")
STATE_KEY = "applied_taste"


def estimate_parts() -> dict:
    return {"base_usd": BASE_USD, "per_chunk_usd": PER_CHUNK_USD, "chunk": CHUNK}


def estimate(n: int) -> float:
    return round(BASE_USD + math.ceil(max(n, 1) / CHUNK) * PER_CHUNK_USD, 2)


def folder() -> Path:
    return settings.ROOT / "Style"


# ── The evidence, compact ─────────────────────────────────────────────────────

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def rate(eng: dict | None) -> dict:
    """Engagement per view and per follower, where Instagram gave the numbers."""
    eng = eng or {}
    likes, comments = _num(eng.get("likes")), _num(eng.get("comments"))
    acts = (likes or 0) + (comments or 0) if likes is not None or comments is not None else None
    views, followers = _num(eng.get("views")), _num(eng.get("followers"))
    return {"per_view": round(acts / views, 4) if acts is not None and views else None,
            "per_follower": round(acts / followers, 4) if acts is not None and followers else None}


def compact(ref: dict, a: dict, followers: dict | None = None) -> dict:
    """One reference as Claude reads it in the report: about 400 tokens."""
    m = a.get("measured") or {}
    look = a.get("look") or {}
    eng = dict(a.get("engagement") or {})
    acct = (eng.get("account") or ref.get("account") or "").lower()
    if eng.get("followers") is None and followers and (followers.get(acct) or {}).get("followers") is not None:
        eng["followers"] = followers[acct]["followers"]
    kind = a.get("kind") or m.get("kind") or "video"
    out = {"id": ref["id"], "account": ref.get("account") or eng.get("account") or "", "side": ref.get("side"),
           "type": ref.get("post_type") or ("carousel" if kind == "images" else "reel"), "analysed_as": kind,
           "why_team": (ref.get("why") or "")[:200], "tag": ref.get("purpose"),
           "engagement": {k: eng.get(k) for k in ("likes", "comments", "views", "followers", "uploaded") if eng.get(k) is not None},
           **({"rate": rate(eng)} if any(rate(eng).values()) else {}),
           "caption": (m.get("caption") or "")[:300]}
    if kind == "images":
        out["numbers"] = {k: m.get(k) for k in ("slides", "aspect", "caption_words", "hashtags") if m.get(k) is not None}
        out["look"] = {k: look.get(k) for k in ("structure", "cover_hook", "words_per_slide", "text_density", "typography",
                                                "layout", "cta", "standout", "design") if look.get(k) not in (None, "", [])}
    else:
        out["numbers"] = {k: m.get(k) for k in ("duration", "cuts", "first_cut", "shot_median", "shot_p90",
                                                "words_per_sec", "speech_start", "music_under_voice_db",
                                                "caption_words", "hashtags") if m.get(k) is not None}
        out["transcript"] = (m.get("transcript") or "")[:300]
        out["look"] = {k: look.get(k) for k in ("hook", "first_frame", "captions", "layouts", "broll", "graphics",
                                                "cta", "standout", "design") if look.get(k) not in (None, "", [])}
    return out


def _median(vals):
    vals = [v for v in vals if _num(v) is not None]
    return round(statistics.median(vals), 2) if vals else None


def _share(items, test) -> float | None:
    return round(sum(1 for i in items if test(i)) / len(items), 2) if items else None


FEATURES = {
    "video": [("duration", "length (s)"), ("first_cut", "first cut (s)"), ("shot_median", "typical shot (s)"),
              ("words_per_sec", "words per second"), ("cuts", "cuts"), ("music_under_voice_db", "music under voice (dB)"),
              ("caption_words", "caption words")],
    "images": [("slides", "slides"), ("caption_words", "caption words")],
}


def stats(items: list[dict]) -> dict:
    """The numbers the report quotes, measured here rather than guessed: what the library
    holds, its typical pace and length, and the better-performing quarter vs the rest."""
    out = {"posts": len(items), "accounts": len({i["account"].lower() for i in items if i.get("account")}),
           "by_type": {}, "engagement": {}}
    for t in sorted({i["type"] for i in items}):
        out["by_type"][t] = sum(1 for i in items if i["type"] == t)
    for kind in ("video", "images"):
        group = [i for i in items if i["analysed_as"] == kind]
        if not group:
            continue
        block = {"posts": len(group)}
        for key, label in FEATURES[kind]:
            block[label] = {"median": _median([i["numbers"].get(key) for i in group]),
                            "measured": sum(1 for i in group if _num(i["numbers"].get(key)) is not None)}
        if kind == "images":
            block["words per slide"] = {"median": _median([(i["look"] or {}).get("words_per_slide") for i in group])}
        else:
            block["has captions"] = _share(group, lambda i: ((i["look"].get("captions") or {}).get("present")) is True)
            block["captions in capitals"] = _share(group, lambda i: (i["look"].get("captions") or {}).get("case") == "upper")
        out[kind] = block
    # Engagement: who has which numbers, then the top quarter vs the rest per kind of post.
    have = lambda k: sum(1 for i in items if _num(i["engagement"].get(k)) is not None)  # noqa: E731
    out["engagement"]["coverage"] = {k: have(k) for k in ("likes", "comments", "views", "followers")}
    for kind in ("video", "images"):
        group = [i for i in items if i["analysed_as"] == kind]
        metric = "per_view" if kind == "video" and sum(1 for i in group if (i.get("rate") or {}).get("per_view")) >= 8 \
            else "per_follower"
        scored = [i for i in group if _num((i.get("rate") or {}).get(metric)) is not None]
        if len(scored) < 8:
            out["engagement"][kind] = {"metric": metric, "scored": len(scored),
                                       "note": "too few posts with the numbers to compare"}
            continue
        scored.sort(key=lambda i: i["rate"][metric], reverse=True)
        cut = max(2, len(scored) // 4)
        top, rest = scored[:cut], scored[cut:]
        cmp = {}
        for key, label in FEATURES[kind]:
            a, b = _median([i["numbers"].get(key) for i in top]), _median([i["numbers"].get(key) for i in rest])
            if a is not None and b is not None:
                cmp[label] = {"top": a, "rest": b}
        if kind == "video":
            for label, test in (("has captions", lambda i: (i["look"].get("captions") or {}).get("present") is True),
                                ("talking to camera", lambda i: any(l in ("talking_full", "punch_in")
                                                                    for l in i["look"].get("layouts") or [])),
                                ("before/after", lambda i: "before_after" in (i["look"].get("layouts") or []))):
                cmp[label] = {"top": _share(top, test), "rest": _share(rest, test), "share": True}
        else:
            cmp["text-led slides (over 15 words)"] = {
                "top": _share(top, lambda i: (_num((i["look"] or {}).get("words_per_slide")) or 0) > 15),
                "rest": _share(rest, lambda i: (_num((i["look"] or {}).get("words_per_slide")) or 0) > 15), "share": True}
        out["engagement"][kind] = {"metric": metric, "scored": len(scored), "top_quarter": len(top),
                                   "top_median_rate": _median([i["rate"][metric] for i in top]),
                                   "rest_median_rate": _median([i["rate"][metric] for i in rest]),
                                   "top_ids": [i["id"] for i in top[:12]], "compare": cmp}
    return out


# ── Claude ────────────────────────────────────────────────────────────────────

_EXAMPLES = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, "note": {"type": "string"}},
                                        "required": ["id", "note"], "additionalProperties": False}}

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "design": {"type": "string"},
        "content": {"type": "string"},
        "engagement": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "quote": {"type": "string"}, "pattern": {"type": "string"}},
            "required": ["id", "quote", "pattern"], "additionalProperties": False}},
        "notable": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "why": {"type": "string", "enum": list(WHY)}, "note": {"type": "string"}},
            "required": ["id", "why", "note"], "additionalProperties": False}},
        "fonts_seen": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["design", "content", "engagement", "hooks", "notable", "fonts_seen"],
    "additionalProperties": False,
}

_SECTION = {"type": "object", "properties": {"text": {"type": "string"}, "examples": _EXAMPLES},
            "required": ["text", "examples"], "additionalProperties": False}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "design": _SECTION,
        "content": _SECTION,
        "engagement": _SECTION,
        "fonts": {"type": "array", "items": {"type": "object", "properties": {
            "seen": {"type": "string"}, "closest_free_font": {"type": "string"}, "licence": {"type": "string"},
            "use_for": {"type": "string"}},
            "required": ["seen", "closest_free_font", "licence", "use_for"], "additionalProperties": False}},
        "recommendations": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "detail": {"type": "string"},
            "post_kinds": {"type": "array", "items": {"type": "string", "enum": list(KIND_KEYS)}},
            "format": {"type": "string", "enum": ["reels", "carousels", "both"]},
            "evidence": {"type": "string"}, "examples": {"type": "array", "items": {"type": "integer"}}},
            "required": ["title", "detail", "post_kinds", "format", "evidence", "examples"], "additionalProperties": False}},
        "settings": {"type": "array", "items": {"type": "object", "properties": {
            "setting": {"type": "string"}, "now": {"type": "string"}, "proposed": {"type": "string"},
            "why": {"type": "string"}},
            "required": ["setting", "now", "proposed", "why"], "additionalProperties": False}},
        "guide_update": {"type": "string"},
        "taste_section": {"type": "string"},
    },
    "required": ["summary", "design", "content", "engagement", "fonts", "recommendations", "settings", "guide_update",
                 "taste_section"],
    "additionalProperties": False,
}

_MAP_SYSTEM = """You are reading part of the reference library of {brand_name}, a Singapore \
renovation and interior design company: Instagram posts its team saved because they like them (Reels, \
carousels, photos), each already analysed (numbers measured from the video, Claude's read of the stills \
or slides, the caption, and whatever engagement Instagram showed). This is part {part} of {parts}.

Write short, specific notes (Markdown bullets, numbers and post ids like #123) on:
- design: palettes, materials, lighting, camera work, framing, shot types, on-screen text and fonts, \
caption styling, carousel layouts;
- content: formats, hook patterns, structures, topics, length and pace, calls to action, voice;
- engagement: which of these posts did better (per view or per follower where given) and what they share; \
say when the numbers are missing.
- hooks: up to 12 of the strongest opening lines, quoted exactly from a transcript, caption or cover \
(with the post id), each with its pattern (e.g. "mistake warning", "price reveal", "before/after promise").
- notable: up to 15 posts that stand out, each with why it is notable: design (the look), content (the \
idea, hook or structure) or engagement (it clearly did better). The team didn't tag them: this is your \
inference from the analysis.
- fonts_seen: the type styles you read about (e.g. "rounded geometric sans, bold, lower case").
Never invent numbers or quotes; use only what is in the evidence."""

_REPORT_SYSTEM = """You are the editor-in-chief for the Instagram of {brand_name}, a Singapore \
renovation and interior design company (HDB flats and condos). Its team saved {n} Instagram posts they \
like, "for design, content and engagement". Each was analysed; you get the numbers measured across the \
library (stats: computed, so quote them as they are), notes from reading every post in parts, quoted \
hooks, and which posts stand out and why (inferred, not tagged by the team). Write the taste report.

{brands}

Kinds of post Urban Makers makes (use these keys in post_kinds):
{kinds}

The playbook the idea generator and editor follow now (the house style is part of it):
<playbook>
{playbook}
</playbook>

Settings the editor and carousels use now:
{settings}
Caption fonts bundled with the editor (STUDIO_CAPTION_FONT): {fonts}. Carousels use Bricolage Grotesque \
for headlines and Figtree for text (render/carousel/fonts). A new font must be free to bundle (SIL Open \
Font License or similar): name it exactly as on Google Fonts.

Write (Markdown in every text field; plain words for Bryan, the owner, who isn't technical):
- summary: the 5 to 7 things the library says about the team's taste, in bullets.
- design / content / engagement: each a section of short headed bullets with numbers, quotes (hooks \
quoted exactly with the post id) and examples (post ids with a short note: they show as thumbnails \
linking to the post). Engagement: what the higher-engagement posts share versus the rest, with the \
numbers from stats and the caveats (how many posts had views or followers, saved posts are a biased \
sample, accounts differ in size, correlation is not cause).
- fonts: the type styles seen and the closest free font for each, what to use it for (Reel captions, \
carousel headlines, body), so Bryan can pick a softer caption font that matches his taste.
- recommendations: 8 to 14, ranked by expected impact, concrete ("first cut by 0.8 s", "cover slide: \
a 5-8 word question over a photo"), each mapped to kinds of post and to reels, carousels or both, with \
its evidence and example post ids. Fit renovation in Singapore and Urban Makers' voice; don't copy what \
only suits other niches. Never invent prices, offers or promotions.
- settings: the specific editor and carousel settings to change (setting name as listed above, its \
value now, the proposed value, why), only where the evidence supports a change.
- guide_update: the proposed "## House style (measured)" playbook section, updated with what the \
library shows (keep what the current house style gets right; say targets, not laws; under 70 lines).
- taste_section: a short "## Taste (from the reference library)" playbook section (under 25 lines): \
the look and feel to aim for (palette, materials, light, camera, type, voice) that the idea generator \
and editor should follow.
Use only post ids that appear in the evidence."""


def _ask(client, system: str, payload: dict, schema: dict, ref: str, effort: str, max_tokens: int) -> tuple[dict, float]:
    import llm
    import usage
    from settings import CLAUDE_MODEL
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=max_tokens, betas=["server-side-fallback-2026-07-01"],
        fallbacks="default", output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        system=system, messages=[{"role": "user", "content": "Evidence:\n" + json.dumps(payload, indent=0)}])
    usd = usage.record("taste", ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't write the taste report ({response.stop_reason})")
    return json.loads(next(b.text for b in response.content if b.type == "text")), usd


def current_settings() -> dict:
    names = ("CAPTION_FONT", "TITLE_STYLE", "MUSIC_GAP_DB", "EDIT_JUMP_CUTS", "EDIT_MAX_PAUSE", "EDIT_REFRAME", "EDIT_PUNCH_INS",
             "EDIT_PUNCH_ZOOM")
    return {f"STUDIO_{n}": getattr(settings, n) for n in names if hasattr(settings, n)}


def draft(brand: str, items: list[dict], client=None, ref: str = "taste") -> dict:
    """The taste report from compact() items: one map read per CHUNK posts, then the
    report. Returns {sections, usage: {cost_usd, calls}}."""
    import anthropic

    import content_plan
    import playbook
    import style_study
    from settings import ANTHROPIC_API_KEY
    if len(items) < MIN_REFERENCES:
        raise RuntimeError(f"only {len(items)} analysed references: the taste report needs at least {MIN_REFERENCES}")
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    brand_name = style_study.BRAND_NAMES.get(brand, brand)
    parts = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]
    notes, spent = [], 0.0
    for k, part in enumerate(parts, 1):
        system = (_MAP_SYSTEM.replace("{brand_name}", brand_name).replace("{part}", str(k))
                  .replace("{parts}", str(len(parts))))
        got, usd = _ask(client, system, {"posts": part}, MAP_SCHEMA, f"{ref}:part{k}", "medium", 16000)
        spent += usd
        notes.append(got)
    numbers = stats(items)
    ids = {i["id"] for i in items}
    notable = [{**n, "part": k} for k, got in enumerate(notes, 1) for n in got.get("notable") or []
               if n.get("id") in ids and n.get("why") in WHY]
    hooks = [h for got in notes for h in got.get("hooks") or [] if h.get("id") in ids]
    try:
        import editor
        fonts = ", ".join(f"{name} ({key})" for key, (name, _) in editor.CAPTION_FONTS.items())
    except Exception:
        fonts = "Figtree (figtree)"
    kinds = "\n".join(f"- {key}: {k['label']} ({k['stage']}): {k['job']}" for key, k in content_plan.KINDS.items())
    system = (_REPORT_SYSTEM.replace("{brand_name}", brand_name).replace("{n}", str(len(items)))
              .replace("{brands}", settings.brands_text()).replace("{kinds}", kinds)
              .replace("{playbook}", playbook.text()).replace("{fonts}", fonts)
              .replace("{settings}", "\n".join(f"- {k} = {v}" for k, v in current_settings().items())))
    payload = {"stats": numbers, "notes": [{k: n.get(k) for k in ("design", "content", "engagement", "fonts_seen")}
                                           for n in notes],
               "hooks": hooks[:60], "notable": [{k: n[k] for k in ("id", "why", "note")} for n in notable][:120],
               "posts": [{"id": i["id"], "account": i["account"], "type": i["type"]} for i in items]}
    report, usd = _ask(client, system, payload, REPORT_SCHEMA, f"{ref}:report", "high", 32000)
    spent += usd
    by = {i["id"]: i for i in items}
    report["notable"] = {w: [{"id": n["id"], "note": n["note"]} for n in notable if n["why"] == w] for w in WHY}
    report["stats"] = numbers
    report["examples"] = {str(i): {"account": by[i]["account"], "type": by[i]["type"]} for i in sorted(ids)}
    for key in ("design", "content", "engagement"):
        report[key]["examples"] = [e for e in report[key]["examples"] if e.get("id") in ids]
    for r in report["recommendations"]:
        r["examples"] = [e for e in r["examples"] if e in ids]
    return {"sections": report, "usage": {"cost_usd": round(spent, 4), "calls": len(parts) + 1}}


# ── Markdown (the copy kept in the Studio folder) ─────────────────────────────

KIND_LABEL = {"knowhow": "Know-how", "ideas": "Ideas", "onsite": "On site", "finished": "Finished homes",
              "offer": "Work with us"}
FORMAT_LABEL = {"reels": "Reels", "carousels": "Carousels", "both": "Reels and carousels"}


def to_markdown(sections: dict, version: int, n: int, urls: dict[int, str] | None = None) -> str:
    urls = urls or {}

    def ex(items):
        lines = []
        for e in items:
            i = e["id"] if isinstance(e, dict) else e
            note = e.get("note", "") if isinstance(e, dict) else ""
            link = urls.get(i)
            lines.append(f"- [#{i}]({link}){': ' + note if note else ''}" if link else f"- #{i}{': ' + note if note else ''}")
        return "\n".join(lines)

    out = [f"# Taste report v{version}", "", f"_From {n} analysed references._", "", "## Summary", "",
           sections.get("summary", "").strip()]
    for key, title in (("design", "1. Design"), ("content", "2. Content"), ("engagement", "3. Engagement")):
        sec = sections.get(key) or {}
        out += ["", f"## {title}", "", (sec.get("text") or "").strip()]
        if sec.get("examples"):
            out += ["", "Examples:", ex(sec["examples"])]
    if sections.get("fonts"):
        out += ["", "## Fonts", "", "| Seen | Closest free font | Licence | Use for |", "|---|---|---|---|"]
        out += [f"| {f['seen']} | {f['closest_free_font']} | {f['licence']} | {f['use_for']} |" for f in sections["fonts"]]
    out += ["", "## Recommendations for Urban Makers", ""]
    for k, r in enumerate(sections.get("recommendations") or [], 1):
        kinds = ", ".join(KIND_LABEL.get(x, x) for x in r.get("post_kinds") or [])
        out += [f"{k}. **{r['title']}** ({FORMAT_LABEL.get(r.get('format'), r.get('format'))}"
                f"{'; ' + kinds if kinds else ''}): {r['detail']} _Evidence: {r['evidence']}_"
                + (f" ({', '.join('#' + str(i) for i in r['examples'])})" if r.get("examples") else "")]
    if sections.get("settings"):
        out += ["", "## Settings to change", "", "| Setting | Now | Proposed | Why |", "|---|---|---|---|"]
        out += [f"| {s['setting']} | {s['now']} | {s['proposed']} | {s['why']} |" for s in sections["settings"]]
    notable = sections.get("notable") or {}
    if any(notable.get(w) for w in WHY):
        out += ["", "## Why these posts stand out (inferred by Studio; the team didn't tag them)"]
        for w in WHY:
            if notable.get(w):
                out += ["", f"### {w.capitalize()}", ex(notable[w])]
    out += ["", "## Proposed style guide", "", (sections.get("guide_update") or "").strip(),
            "", "## Proposed taste section for the playbook", "", (sections.get("taste_section") or "").strip(), ""]
    return "\n".join(out)


# ── With the planner ──────────────────────────────────────────────────────────

def run_jobs(api, refs: list[dict], guides: list[dict], conn, client=None) -> list[str]:
    """The owner's "Write the taste report" (job kind 'taste')."""
    import cloud_sync
    lines = []
    for job in api.select("jobs", {"runner": "eq.laptop", "state": "eq.queued", "kind": "eq.taste"}):
        claimed = api.update("jobs", {"id": f"eq.{job['id']}", "state": "eq.queued"},
                             {"state": "running", "started_at": cloud_sync._now()})
        if not claimed:
            continue
        brand = (job.get("payload") or {}).get("brand") or "urban_makers"
        try:
            row = write(api, brand, refs, guides, conn, client)
            cloud_sync._finish(api, job, {"report_id": row["id"], "version": row["version"],
                                          "guide_id": row.get("guide_id")})
            lines.append(f"taste #{job['id']}: v{row['version']} written")
        except Exception as e:
            if isinstance(e, cloud_sync.CloudError) and "can't reach" in str(e):
                raise
            if not isinstance(e, (RuntimeError, cloud_sync.CloudError)):
                log.exception("Taste job %s failed", job["id"])
            message = str(e) if isinstance(e, (RuntimeError, cloud_sync.CloudError)) else f"{type(e).__name__}: {e}"
            cloud_sync._finish(api, job, error=message[:1000])
            lines.append(f"taste #{job['id']}: failed ({message[:80]})")
    return lines


def write(api, brand: str, refs: list[dict], guides: list[dict], conn, client=None) -> dict:
    """Read every analysed reference of the brand, write the report, keep it as Markdown,
    and put it (with its proposed style guide as a draft) in the planner."""
    import cloud_sync
    import ref_library
    if brand not in settings.BRANDS:
        raise RuntimeError(f"unknown brand {brand!r}")
    analyses = {a["reference_id"]: a for a in api.select("reference_analysis", {"order": "reference_id",
                                                                                "state": "eq.done"})}
    mine = [r for r in refs if r["brand"] == brand and r["status"] == "active" and r["id"] in analyses]
    followers = ref_library.accounts()
    items = [compact(r, analyses[r["id"]], followers) for r in sorted(mine, key=lambda r: r["id"])]
    if len(items) < MIN_REFERENCES:
        raise RuntimeError(f"Only {len(items)} references are analysed so far: the taste report needs at least "
                           f"{MIN_REFERENCES}. Press Analyse on the References page first.")
    cost = estimate(len(items))
    left = ref_library.budget(conn)["left_usd"]
    if cost > left:
        raise RuntimeError(f"The taste report would cost about US${cost:.2f} and only US${left:.2f} is left of this "
                           "month's reference budget (STUDIO_REFERENCE_BUDGET_USD).")
    reports = api.select("taste_reports", {"brand": f"eq.{brand}", "select": "version", "order": "version.desc"})
    version = (reports[0]["version"] if reports else 0) + 1
    result = draft(brand, items, client, ref=f"taste:{brand}:v{version}")
    sections = result["sections"]
    urls = {r["id"]: r.get("url") for r in mine if r.get("url")}
    md = to_markdown(sections, version, len(items), urls)
    path = folder() / f"taste-report-v{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    # The proposed style guide, as a draft: approving the report puts it in force.
    hand = [r["id"] for r in cloud_sync._guide_inputs(refs, analyses, brand)]
    mine_guides = [g for g in guides if g["brand"] == brand]
    active = next((g for g in mine_guides if g["status"] == "active"), None)
    g_version = max((g["version"] for g in mine_guides), default=0) + 1
    recs = "\n".join(f"- {r['title']}" for r in sections["recommendations"][:6])
    guide = api.insert("style_guides", {
        "brand": brand, "version": g_version, "status": "draft", "content": sections["guide_update"].strip(),
        "report": sections["summary"].strip(),
        "changes": (f"Proposed by taste report v{version}, from {len(items)} references in the library "
                    f"(approve the report to put it in force). The main changes:\n{recs}"),
        "reference_ids": hand, "added_ids": [i for i in hand if not active or i not in (active.get("reference_ids") or [])],
        "removed_ids": [], "usage": {"cost_usd": 0, "from": f"taste report v{version}"}})[0]
    guides.append(guide)
    row = api.insert("taste_reports", {
        "brand": brand, "version": version, "status": "draft", "report": md, "sections": sections,
        "taste_section": sections["taste_section"].strip(), "guide_id": guide["id"],
        "reference_ids": [i["id"] for i in items], "usage": result["usage"]})[0]
    log.info("Taste report v%s written for %s from %d references (US$%.2f, %d calls): %s", version, brand, len(items),
             result["usage"]["cost_usd"], result["usage"]["calls"], path)
    return row


def sync_approved(api) -> str | None:
    """Keep each report in step with its style guide (approved on the Style guide page instead:
    the report counts as approved too; rejected there: rejected), then write the approved
    report's taste section into the playbook once. Returns a line, or None."""
    import cloud_sync
    import style_study
    reports = api.select("taste_reports", {"select": "id,brand,version,status,guide_id,taste_section,approved_at",
                                           "order": "version.desc"})
    drafts = [r for r in reports if r["status"] == "draft" and r.get("guide_id")]
    if drafts:
        guides = {g["id"]: g for g in api.select("style_guides", {
            "select": "id,status", "id": f"in.({','.join(str(r['guide_id']) for r in drafts)})"})}
        for r in drafts:
            g = guides.get(r["guide_id"])
            if g and g["status"] == "active":
                api.update("taste_reports", {"id": f"eq.{r['id']}", "status": "eq.draft"}, {"status": "approved"})
                r["status"] = "approved"
            elif g and g["status"] == "rejected":
                api.update("taste_reports", {"id": f"eq.{r['id']}", "status": "eq.draft"}, {"status": "rejected"})
    approved = next((r for r in reports if r["brand"] == "urban_makers" and r["status"] == "approved"), None)
    if not approved or not (approved.get("taste_section") or "").strip():
        return None
    digest = hashlib.sha256(approved["taste_section"].encode()).hexdigest()[:16]
    done = cloud_sync._state().get(STATE_KEY) or {}
    if done.get("id") == approved["id"] and done.get("hash") == digest:
        return None
    when = (cloud_sync._ts(approved.get("approved_at")) or cloud_sync._now())[:10]
    style_study.apply_section(approved["taste_section"], f"Taste report v{approved['version']}, approved {when}.",
                              (TASTE_START, TASTE_END))
    cloud_sync._save_state({STATE_KEY: {"id": approved["id"], "version": approved["version"], "hash": digest,
                                        "applied_at": cloud_sync._now()}})
    log.info("Taste report v%s is in the playbook now", approved["version"])
    return f"taste report v{approved['version']} applied to the playbook"
