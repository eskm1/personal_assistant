"""
Bryan's voice file (Studio/voice.md): who he is talking to, how he sounds on camera, the words
he uses and never uses, his positions, what's off limits and the proof he can use, in the shape
of igkit/voice_template.md (instagram-agent-skill's voice.md).

  draft()     one Claude call (effort medium, usage kind "voice") over about 20 of his own
              talking-head transcripts, picked to vary (different projects, months, lengths),
              and the playbook. Review's "Write the voice file from my videos" (Playbook >
              Your voice) runs it; so does Studio once by itself (tick()), when there's no
              voice file yet and at least MIN_CLIPS talking-head clips have transcripts.
  text()/save()  the file as it is; Bryan edits it in Review. The previous one is kept as
              voice.previous.md when a new draft replaces it.
  compact()   what the prompts get: the file without template notes and empty lines, cut to a
              few thousand characters. Every prompt that writes words viewers read includes it
              (wording.guide); cloud_sync pushes it to the planner's Edge Functions (context
              key "voice", once that database update is online).

The voice file never invents proof: numbers and stories only from Bryan's own words.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import settings

log = logging.getLogger("studio.voice")

MIN_CLIPS = 10          # transcribed talking-head clips before Studio writes it by itself
SAMPLE = 20             # transcripts Claude reads
CLIP_CHARS = 1800       # of each transcript
MIN_SPEECH = 200        # characters of speech for a clip to count
COMPACT_CHARS = 3000    # of the file in each prompt
AUTO_TRIES = 3          # automatic attempts (a day apart) before Studio leaves it to the button
STATE = settings.ROOT / "voice_state.json"

_lock = threading.Lock()
job = {"running": False, "error": None, "finished_at": None}


def path():
    return settings.VOICE_FILE


def text() -> str:
    try:
        return path().read_text(encoding="utf-8") if path().exists() else ""
    except OSError:
        return ""


def save(value: str) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value.strip() + "\n", encoding="utf-8")


def compact(limit: int = COMPACT_CHARS) -> str:
    """The voice file for a prompt: no template notes, no empty fields, at most `limit` characters."""
    lines = []
    for raw in text().splitlines():
        line = raw.rstrip()
        bare = line.strip()
        if not bare or bare in ("---", "-", "*") or bare.startswith(("<!--", "Copy this", "If you would rather")):
            continue
        if bare.startswith(">"):  # a note to the reader, not the voice
            continue
        if bare.rstrip(".:") in ("1", "2", "3", "4", "5") or (bare.startswith("- **") and bare.endswith(":**")):
            continue  # a template line nobody filled in
        lines.append(line)
    out = "\n".join(lines).strip()
    if len(out) > limit:
        out = out[:limit].rsplit("\n", 1)[0] + "\n[...]"
    return out


# ── The transcripts it's drafted from ─────────────────────────────────────────

def candidates(conn) -> list:
    """Clips of Bryan talking to camera with enough speech (English or Chinese), newest first."""
    return conn.execute(
        """SELECT * FROM clips WHERE status IN ('tagged', 'transcribed') AND format = 'talking_head'
           AND LENGTH(COALESCE(transcript, '')) >= ? AND COALESCE(language, 'en') IN ('en', 'zh')
           AND archived_at IS NULL ORDER BY COALESCE(recorded_at, ingested_at) DESC""", (MIN_SPEECH,)).fetchall()


def sample(rows: list, n: int = SAMPLE) -> list:
    """About n clips that vary: round-robin over projects (newest of each first), then over time."""
    by_project: dict = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(r)
    queues = sorted(by_project.values(), key=len, reverse=True)
    out = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                # alternate the newest and the oldest of a project, so a long project spans its months
                out.append(q.pop(0) if len(out) % 2 == 0 else q.pop())
    return out


def _clip_text(r) -> str:
    words = (r["transcript"] or "").strip()
    if len(words) > CLIP_CHARS:
        words = words[:CLIP_CHARS].rsplit(" ", 1)[0] + " [...]"
    when = (r["recorded_at"] or r["ingested_at"] or "")[:10]
    return f"Clip {r['id']} ({when}, {r['duration'] or 0:.0f}s, {r['language'] or 'en'}): {r['title'] or ''}\n{words}"


_SYSTEM = """You write the voice file for Urban Makers' Instagram (a Singapore renovation company; Bryan \
runs it and talks to camera). Every prompt that writes captions, hooks, slides and on-screen text reads \
this file, so a post sounds like Bryan, not a brand. Work only from his own words in the transcripts \
and the playbook below; never invent a fact, a number, a client, a result or a story.

Bryan's playbook (his rules, the house style):
<playbook>
{playbook}
</playbook>

{brands}

Write the file in this shape (Markdown, the same headings; fill every field you can from the \
transcripts, leave a field out when they don't say; short lines, no essays):

<template>
{template}
</template>

How to fill it:
- Who I am: name Bryan, handle @{handle}, Urban Makers, who he talks to (as he talks about them: \
homeowners in HDB flats and condos, first-time renovators...), what Urban Makers does.
- What I sound like: three short quotes that sound most like him (his exact words, a sentence or two \
each, from different clips); on camera (pace, tone); words and phrases he really uses (his trade \
terms, his Singapore English, the Chinese he mixes in, if any); words he'd never say (marketing words \
missing from all of his speech: "elevate", "seamless", "dream home"... only when he never says them); \
swearing; emoji in captions (Studio writes none on slides); face on camera; to-camera or voiceover; \
his pace in words per minute when the timings show it.
- My positions: three to five things he says that some homeowners wouldn't agree with, in his words.
- Off limits: place names (estates, streets, condos, clients' addresses) and clients' names never \
appear; never prices, discounts or promotions unless the planner lists the offer; nothing about \
quotes or quotations; anything else his talk shows he keeps private.
- Proof I can use: only things he says in the transcripts (a problem he solved, a step he insists on), \
each with its clip number in brackets. No numbers he didn't say.
- The ask: what he asks people to do, from the playbook and the offers (never a made-up offer).
- English or Simplified Chinese only. Return only the file."""


def prompt_parts(conn) -> tuple[str, str, list[int]]:
    """(system, user, clip ids) for the draft."""
    import playbook
    rows = sample(candidates(conn))
    template = settings.HERE.joinpath("igkit", "voice_template.md").read_text(encoding="utf-8")
    template = template.split("---", 1)[-1].strip()  # the instructions above the line are for people
    handle = settings.INSTAGRAM_HANDLE if settings.INSTAGRAM_HANDLE not in ("", "off") else "urbanmakers_sg"
    system = (_SYSTEM.replace("{playbook}", playbook.text()[:12000]).replace("{brands}", settings.brands_text())
              .replace("{template}", template).replace("{handle}", handle))
    user = (f"Bryan talking to camera, {len(rows)} clips picked to vary (projects, months, length):\n\n"
            + "\n\n".join(_clip_text(r) for r in rows))
    return system, user, [r["id"] for r in rows]


def draft(conn, client=None) -> str:
    """Write the voice file from his videos (one Claude call) and save it. Returns the text."""
    import anthropic

    import llm
    import usage
    import wording
    rows = candidates(conn)
    if not rows:
        raise RuntimeError("No talking-head videos with speech yet: film one or two and try again")
    system, user, ids = prompt_parts(conn)
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium"}, system=system, messages=[{"role": "user", "content": user}])
    usage.record("voice", f"voice:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't write the voice file this time ({response.stop_reason})")
    body = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if body.startswith("```"):
        body = body.strip("`").split("\n", 1)[-1].strip()
    if len(body) < 200:
        raise RuntimeError("Claude's voice file came back too short; try again")
    body = wording.tidy(body)
    old = text()
    if old.strip():
        path().with_name("voice.previous.md").write_text(old, encoding="utf-8")
    save(body)
    log.info("Wrote the voice file from %d talking-head clips (%s)", len(ids), path())
    return body


# ── On demand (Review) and once by itself ─────────────────────────────────────

def busy() -> bool:
    return job["running"]


def start(conn_factory, client=None) -> bool:
    """Draft it in the background (Review's button). False if one is already being written."""
    with _lock:
        if job["running"]:
            return False
        job.update(running=True, error=None)

    def work():
        conn = conn_factory()
        try:
            draft(conn, client)
            job["error"] = None
        except Exception as e:
            job["error"] = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            log.warning("The voice file couldn't be written: %s", job["error"])
        finally:
            conn.close()
            job.update(running=False, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    threading.Thread(target=work, name="voice-file", daemon=True).start()
    return True


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tick(conn, client=None) -> bool:
    """Once by itself: no voice file yet, MIN_CLIPS talking-head transcripts, a Claude key, and
    fewer than AUTO_TRIES tries (a day apart). True when it wrote one."""
    if path().exists() or job["running"] or not settings.ANTHROPIC_API_KEY:
        return False
    state = _state()
    if state.get("tries", 0) >= AUTO_TRIES or time.time() - state.get("tried_at", 0) < 86400:
        return False
    if len(candidates(conn)) < MIN_CLIPS:
        return False
    state.update(tries=state.get("tries", 0) + 1, tried_at=time.time())
    try:
        STATE.write_text(json.dumps(state), encoding="utf-8")
        draft(conn, client)
        return True
    except Exception as e:
        log.warning("Studio couldn't write the voice file by itself yet (will try again tomorrow): %s", e)
        return False


def info(conn) -> dict:
    """For Review: the file, how many clips it can learn from, and the background draft's state."""
    return {"text": text(), "exists": path().exists(), "clips": len(candidates(conn)), "min_clips": MIN_CLIPS,
            "sample": SAMPLE, "running": job["running"], "error": job["error"], "finished_at": job["finished_at"]}
