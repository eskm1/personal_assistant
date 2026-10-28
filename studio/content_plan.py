"""
The content plan (cloud/CONTRACT.md, "Content plan"): every post has a job, its kind,
and the month has a mix Bryan sets in the planner (how many of each kind, and the
offers a post may make). This module gives Claude's prompts the five kinds, this
month's gaps (target minus what's planned so far) and the offers, and fills in the
kind of older ideas that don't have one yet (cloud_sync, a few at a time).

Studio never invents an offer, price or promotion: a "Work with us" post, or any
call to action, may only use the offers listed in the planner.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import anthropic

import settings

log = logging.getLogger("studio.content_plan")

# The five kinds of post (ideas.post_type), in the planner's order. Keep in step with
# cloud/supabase/functions/_shared/prompts.ts and the planner (js/core.js).
KINDS = {
    "knowhow": {"label": "Know-how", "stage": "Get found",
                "job": "teach something practical people save and share (HDB rules, hidden costs, "
                       "materials that last in Singapore humidity)",
                "ask": "save this / follow for more"},
    "ideas": {"label": "Ideas", "stage": "Get found",
              "job": "design thinking: layouts, zoning, light, colour (\"3 ways to give an open-plan flat "
                     "a quiet corner\")",
              "ask": "save this / which would you pick?"},
    "onsite": {"label": "On site", "stage": "Build trust",
               "job": "show the craft and care while the work happens (waterproofing done right, a carpentry "
                      "detail, a problem solved)",
               "ask": "follow this project"},
    "finished": {"label": "Finished homes", "stage": "Build trust",
                 "job": "proof: the portfolio, before and after, the owners' reaction",
                 "ask": "a one-word question to the reader (no offer, ever: not even a free consultation)"},
    "offer": {"label": "Work with us", "stage": "Get enquiries",
              "job": "turn followers into enquiries: how we work, what's included, one of the offers below",
              "ask": "one of the offers below"},
}
DEFAULT_MIX = {"knowhow": 4, "ideas": 2, "onsite": 3, "finished": 2, "offer": 2}
DEFAULT_OFFERS = [
    {"name": "Free consultation", "details": "", "ask": "Book a free consultation"},
    {"name": "Site visit", "details": "", "ask": "Book a site visit"},
    {"name": "Floor plan review", "details": "", "ask": "Send us your floor plan"},
]
DEFAULT_PLAN = {"posts_per_week": 3, "mix": DEFAULT_MIX, "offers": DEFAULT_OFFERS}

BACKFILL_BATCH = 20          # ideas per Claude call
BACKFILL_GRACE_MINUTES = 10  # a brand-new idea gets this long for someone to pick its kind
BACKFILL_TRIES = 2           # Claude's attempts per idea before it's left for a person
BACKFILL_RETRY_SECONDS = 3600  # after a failed call, wait this long before the next one
_backfill_failed_at = 0.0


# ── The plan and this month ───────────────────────────────────────────────────

def load(api) -> dict:
    """The content_plan row (posts_per_week, mix, offers), or the defaults."""
    rows = api.select("content_plan", {"select": "posts_per_week,mix,offers", "order": "id"})
    row = rows[0] if rows else {}
    mix = {k: int(v) for k, v in (row.get("mix") or {}).items() if k in KINDS and isinstance(v, (int, float))}
    offers = [o for o in (row.get("offers") if isinstance(row.get("offers"), list) else DEFAULT_OFFERS)
              if isinstance(o, dict) and (o.get("name") or "").strip()]
    return {"posts_per_week": row.get("posts_per_week", 3), "mix": {**{k: 0 for k in KINDS}, **mix} if row else DEFAULT_MIX,
            "offers": offers}


def month(api) -> dict:
    """This month against the mix (the database's content_month(): counts per kind)."""
    return api.request("POST", "/rest/v1/rpc/content_month", json_body={}).json()


def _month_name(m: str) -> str:
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%B %Y")
    except (TypeError, ValueError):
        return "This month"


def prompt_text(plan: dict | None = None, now: dict | None = None) -> str:
    """The section Claude's prompts get as {content_plan}: the kinds, this month's gaps
    (when known) and the offers."""
    plan = plan or DEFAULT_PLAN
    lines = ["Kinds of post. Every post has exactly one kind (post_type), by the job it does:"]
    for key, k in KINDS.items():
        ask = k["ask"] if k["ask"].endswith("?") else k["ask"] + "."
        lines.append(f"- {key} ({k['label']}, to {k['stage'].lower()}): {k['job']}. Ask: {ask}")
    if now and isinstance(now.get("kinds"), dict):
        parts, short = [], []
        for key, k in KINDS.items():
            c = now["kinds"].get(key) or {}
            target, planned = int(c.get("target") or 0), int(c.get("planned") or 0)
            gap = max(target - planned, 0)
            parts.append(f"{k['label']} {planned} of {target}" + (f" ({gap} short)" if gap else ""))
            if gap:
                short.append(k["label"])
        lines += ["", f"{_month_name(now.get('month'))}: {now.get('planned', 0)} of {now.get('target', 0)} posts "
                  f"planned ({plan.get('posts_per_week', 3)} a week). " + "; ".join(parts) + "."]
        lines.append(("Short this month: " + ", ".join(short) + ". When the footage or the team's ideas allow, lean "
                      "toward these kinds; never force a post into a kind it isn't.") if short
                     else "Every kind is on target this month: pick whatever kind fits each post best.")
    offers = plan.get("offers") or []
    lines += ["", "Offers: the ONLY things a Work with us post, or any call to action, may offer. Never invent a "
              "price, discount, promotion, deadline or offer; no figures unless they're written here."]
    if offers:
        for o in offers:
            lines.append(f"- {o.get('name', '').strip()}" + (f": {o['details'].strip()}" if (o.get("details") or "").strip() else "")
                         + (f" (ask: \"{o['ask'].strip()}\")" if (o.get("ask") or "").strip() else ""))
    else:
        lines.append("- (none listed yet: a call to action asks people to follow, save or DM, and offers nothing)")
    return "\n".join(lines)


def context_text(api=None) -> str:
    """prompt_text from the planner (the plan and this month's counts); the defaults when
    planning isn't online or the database predates the content plan. Never raises."""
    if api is None:
        return prompt_text()
    try:
        return prompt_text(load(api), month(api))
    except Exception as e:
        log.debug("Content plan not available (%s): using the defaults", e)
        return prompt_text()


def clean_type(value) -> str | None:
    return value if value in KINDS else None


# ── Filling in the kind of older ideas ────────────────────────────────────────

_SCHEMA = {
    "type": "object",
    "properties": {"types": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "post_type": {"type": "string", "enum": list(KINDS)}},
        "required": ["id", "post_type"], "additionalProperties": False}}},
    "required": ["types"],
    "additionalProperties": False,
}

_SYSTEM = """You sort the planned Instagram posts of Urban Makers, a Singapore renovation \
company, by the job each post does. Give every post listed exactly one kind.

{kinds}

How to tell:
- A post about this client's renovation while the work is going on (a phase, a fix, the team \
at work) is onsite. The finished result, a walkthrough, a before/after or the owners' words is \
finished.
- A post that teaches something general (rules, costs, materials, how something works, \
mistakes to avoid) is knowhow, even when it's filmed on site. A post about design choices \
(layout, zoning, light, colour, storage ideas) is ideas.
- A post whose main point is getting people to enquire (how we work, what's included, an offer, \
slots this quarter) is offer.
- Judge by the title, hook and purpose first; the project and series are context."""


def _kinds_text() -> str:
    return "\n".join(f"- {key} ({k['label']}): {k['job']}." for key, k in KINDS.items())


def _post_text(i: dict, projects: dict, arcs: dict) -> str:
    p = projects.get(i.get("project_id")) or {}
    a = arcs.get(i.get("arc_id")) or {}
    bits = [f"Post {i['id']}: \"{(i.get('title') or '').strip()[:300]}\""]
    for label, value in (("hook", i.get("hook")), ("purpose", i.get("purpose")), ("format", i.get("format"))):
        if (value or "").strip():
            bits.append(f"  {label}: {str(value).strip()[:400]}")
    bits.append(f"  project: {p.get('name') or '-'}" + (" (standing project, not one client's job)" if p.get("standing") else "")
                + (f"; brief: {(p.get('brief') or '').strip()[:200]}" if (p.get("brief") or "").strip() else ""))
    if a:
        bits.append(f"  series: {a.get('title')}" + (f" ({a['purpose'][:150]})" if a.get("purpose") else ""))
    return "\n".join(bits)


def classify(ideas: list[dict], projects: dict, arcs: dict, client=None, ref: str = "") -> dict[int, str]:
    """One Claude call (effort low): {idea id: kind} for these ideas."""
    import llm
    import usage
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    user = "Posts to sort:\n\n" + "\n\n".join(_post_text(i, projects, arcs) for i in ideas)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
        system=_SYSTEM.replace("{kinds}", _kinds_text()),
        messages=[{"role": "user", "content": user}])
    usage.record("kinds", ref or f"kinds:{len(ideas)}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't sort these posts ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    wanted = {i["id"] for i in ideas}
    return {t["id"]: t["post_type"] for t in answer.get("types", [])
            if t.get("id") in wanted and t.get("post_type") in KINDS}


def _parse(stamp) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def waiting(ideas: list[dict], tried: dict | None = None, now: datetime | None = None) -> list[dict]:
    """Ideas still without a kind that Studio should sort: not dismissed, older than the
    grace period, not given up on. Go ideas first, then the newest."""
    now = now or datetime.now(timezone.utc)
    tried = tried or {}
    grace = now - timedelta(minutes=BACKFILL_GRACE_MINUTES)
    out = [i for i in ideas if not i.get("post_type") and i.get("status") != "dismissed"
           and (_parse(i.get("created_at")) or now) <= grace and tried.get(str(i["id"]), 0) < BACKFILL_TRIES]
    return sorted(out, key=lambda i: (i.get("decision") != "go", -i["id"]))


def backfill(api, ideas: list[dict], projects: list[dict], arcs: list[dict], client=None) -> int:
    """Sort one batch of ideas that have no kind yet (the remote rows from the pull) and
    write the kinds online, only where post_type is still empty, so a kind someone
    picked meanwhile is never overwritten. Returns how many were set."""
    global _backfill_failed_at
    import cloud_sync
    state = cloud_sync._state()
    tried = dict(state.get("post_type_tried") or {})
    batch = waiting(ideas, tried)[:BACKFILL_BATCH]
    if not batch or time.time() - _backfill_failed_at < BACKFILL_RETRY_SECONDS:
        return 0
    try:
        picks = classify(batch, {p["id"]: p for p in projects}, {a["id"]: a for a in arcs}, client,
                         ref=f"kinds:{batch[0]['id']}-{batch[-1]['id']}")
    except Exception:
        _backfill_failed_at = time.time()  # Claude unreachable or no answer: try again in an hour
        raise
    _backfill_failed_at = 0.0
    # An idea Claude answered for but didn't sort is tried again, BACKFILL_TRIES times at most.
    for i in batch:
        if i["id"] not in picks:
            tried[str(i["id"])] = tried.get(str(i["id"]), 0) + 1
    known = {str(i["id"]) for i in ideas}
    cloud_sync._save_state({"post_type_tried": {k: v for k, v in tried.items() if k in known}})
    by_kind: dict[str, list[int]] = {}
    for idea_id, kind in picks.items():
        by_kind.setdefault(kind, []).append(idea_id)
    n = 0
    for kind, ids in by_kind.items():
        rows = api.update("ideas", {"id": f"in.({','.join(str(i) for i in sorted(ids))})", "post_type": "is.null"},
                          {"post_type": kind})
        n += len(rows)
    if n:
        log.info("Kinds of post: sorted %d of %d older ideas", n, len(batch))
    return n
