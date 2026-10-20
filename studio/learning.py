"""
Editing rules Studio teaches itself, so Bryan only has to give judgement and taste, never
the same editing note twice.

  - When one of Studio's own fix rounds (self_fix.py) fixes a problem its read found, the
    read's general rule for that problem is kept, or the rule it repeats is counted again.
  - When Bryan sends a draft back, the editor's plan sorts each of his notes into an editing
    problem (it becomes a rule the same way) or taste (his own; it goes to the playbook's
    "Notes from reviews" only when he ticks Remember, as before). Review tells him in one
    line when a note became a rule.
  - The rules live in Studio/learned_rules.json and are written into their own section of the
    playbook, "Editing rules Studio learned", which the editor's plan, Studio's read and the
    planner read on every post. Bryan's own sections are never touched. At most
    settings.LEARNED_RULES_MAX rules per kind of post: near-duplicates merge (their counts
    add up), a rule not seen for RETIRE_DAYS that fired only once or twice retires, and one
    Bryan forgets never comes back.
  - The mechanical ones are checked by code on every draft (CODE_CHECKS; editor.sentence_guard,
    clear_of_cards, caption_chunks, mix_final, fix_loudness), so they can't come back.
  - Bryan's notes asking for fewer or more drawn animations ("too many animations", "more
    diagrams") move a lean the editor follows: its guidance and how many it may draw per draft
    (insert_guidance, insert_cap). It shows as one line in Studio's playbook section.
  - Every draft logs how many editing problems Studio caught itself before Bryan saw it
    (self_fix_log in the catalogue), for Review's trend line.
"""
import difflib
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone

import settings

log = logging.getLogger("studio.learning")

RULES_FILE = settings.ROOT / "learned_rules.json"
START, END = "<!-- studio-rules:start -->", "<!-- studio-rules:end -->"
HEADING = "## Editing rules Studio learned"
SCOPES = {"reel": "Reels", "carousel": "Carousels"}
RETIRE_DAYS = 120
SIMILAR = 0.72
_lock = threading.Lock()

# What Studio checks by code on every draft, whatever Claude plans (no Claude call).
CODE_CHECKS = {
    "reel": [
        "Ends on the last full sentence: a stray word that starts the next one is cut.",
        "Opens on a full sentence, with no dead air before the first word or after the last.",
        "Cards stay above the caption line, and captions that repeat a card's words are hidden while it's up.",
        "Captions are at most 18 characters and stay up long enough to read.",
        "Your voice is levelled and cleaned up; the music sits well under it while you talk.",
        "Loudness is Instagram's level (-14), with no harsh peaks.",
    ],
    "carousel": [
        "Every word fits its slide (the text shrinks to fit; nothing is cut off).",
    ],
}


# ── The rules ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"next_id": 1, "rules": []}


def _save(data: dict) -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(RULES_FILE)
    sync_playbook(data)


def clean_rule(text) -> str:
    """One short line, a capital first and a full stop last."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip("-•* ").strip()
    text = re.sub(r"^R\d+[:.)]\s*", "", text)
    if not text:
        return ""
    text = text[:160].rstrip()
    text = text[0].upper() + text[1:]
    return text if re.search(r"[.!?]$", text) else text + "."


_STOP = {"the", "a", "an", "and", "or", "of", "to", "on", "in", "it", "its", "is", "be", "for", "with", "never",
         "always", "not", "no", "that", "this", "your", "his", "so", "at", "by", "from", "as", "one", "keep"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def similarity(a: str, b: str) -> float:
    """How alike two rules read (0-1): the better of their letters' and their key words' overlap."""
    wa, wb = _words(a), _words(b)
    ratio = difflib.SequenceMatcher(a=" ".join(wa), b=" ".join(wb), autojunk=False).ratio()
    ka, kb = set(wa) - _STOP, set(wb) - _STOP
    jaccard = len(ka & kb) / len(ka | kb) if ka | kb else 0.0
    return max(ratio, jaccard)


def _match(data: dict, scope: str, text: str, same_rule) -> dict | None:
    rules = [r for r in data["rules"] if r["scope"] == scope and r["status"] != "merged"]
    if isinstance(same_rule, int) and same_rule > 0:
        named = next((r for r in rules if r["id"] == same_rule), None)
        # Claude names the rule it repeats; trust it unless the words are nothing alike.
        if named and similarity(named["text"], text) >= 0.25:
            return named
    best = max(rules, key=lambda r: similarity(r["text"], text), default=None)
    return best if best and similarity(best["text"], text) >= SIMILAR else None


def learn(items: list[dict], scope: str, source: str, when: str | None = None) -> list[dict]:
    """items: [{rule, same_rule}]. Each becomes a new rule, or counts again for the rule it
    repeats (once per call, however many items name it). source: studio (its own fixes) or
    bryan (his notes). Returns [{id, text, new, status}]. Never raises."""
    if not settings.LEARN_RULES or scope not in SCOPES:
        return []
    try:
        with _lock:
            data = _load()
            today = when or date.today().isoformat()
            touched = {}
            for item in items:
                text = clean_rule(item.get("rule"))
                if not text:
                    continue
                rule = _match(data, scope, text, item.get("same_rule"))
                if rule is None:
                    rule = {"id": data["next_id"], "scope": scope, "text": text, "count": 0, "first": today,
                            "last": today, "from": {}, "status": "active"}
                    data["next_id"] += 1
                    data["rules"].append(rule)
                if rule["id"] in touched:
                    continue
                rule["count"] += 1
                rule["last"] = max(rule["last"], today)
                rule["from"][source] = rule["from"].get(source, 0) + 1
                if rule["status"] == "retired":  # back after a long time: it matters again
                    rule["status"] = "active"
                touched[rule["id"]] = rule
            if not touched:
                return []
            tidy(data, today)
            _save(data)
            out = [{"id": r["id"], "text": r["text"], "new": r["count"] == 1, "status": r["status"]}
                   for r in touched.values()]
        log.info("Learned rules (%s, %s): %s", scope, source, "; ".join(
            f"R{r['id']} {'new' if r['new'] else 'again'}" for r in out))
        return out
    except Exception as e:  # learning must never break a draft
        log.warning("Couldn't keep the learned rules: %s: %s", type(e).__name__, e)
        return []


def tidy(data: dict, today: str) -> None:
    """Merge near-duplicates (counts add up), retire stale rules, and keep at most
    settings.LEARNED_RULES_MAX per kind of post."""
    day = date.fromisoformat(today)
    for scope in SCOPES:
        active = sorted((r for r in data["rules"] if r["scope"] == scope and r["status"] == "active"),
                        key=lambda r: (-r["count"], r["id"]))
        for i, keep in enumerate(active):
            if keep["status"] != "active":
                continue
            for other in active[i + 1:]:
                if other["status"] == "active" and similarity(keep["text"], other["text"]) >= SIMILAR:
                    keep["count"] += other["count"]
                    keep["last"] = max(keep["last"], other["last"])
                    keep["first"] = min(keep["first"], other["first"])
                    for k, v in other["from"].items():
                        keep["from"][k] = keep["from"].get(k, 0) + v
                    other.update(status="merged", merged_into=keep["id"])
        for r in data["rules"]:
            if (r["scope"] == scope and r["status"] == "active" and r["count"] < 3
                    and (day - date.fromisoformat(r["last"])).days > RETIRE_DAYS):
                r["status"] = "retired"
        active = sorted((r for r in data["rules"] if r["scope"] == scope and r["status"] == "active"),
                        key=lambda r: (-r["count"], r["last"], r["id"]))
        for r in active[settings.LEARNED_RULES_MAX:]:
            r["status"] = "retired"


def forget(rule_id: int) -> bool:
    """Bryan drops a rule: it leaves the playbook and never comes back (a repeat is counted
    but stays dropped)."""
    with _lock:
        data = _load()
        rule = next((r for r in data["rules"] if r["id"] == rule_id and r["status"] != "merged"), None)
        if rule is None:
            return False
        rule["status"] = "forgotten"
        _save(data)
    log.info("Learned rule R%s forgotten", rule_id)
    return True


def active(scope: str | None = None) -> list[dict]:
    rules = [r for r in _load()["rules"] if r["status"] == "active" and (scope is None or r["scope"] == scope)]
    return sorted(rules, key=lambda r: (-r["count"], r["id"]))


def _when(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.day} {d:%b %Y}"


def _line(r: dict) -> str:
    times = "once" if r["count"] == 1 else "twice" if r["count"] == 2 else f"{r['count']} times"
    return f"- R{r['id']}: {r['text']} (caught {times}, last {_when(r['last'])})"


def rules_text(scope: str) -> str:
    """The rules for one kind of post, for a prompt that doesn't read the whole playbook."""
    rules = active(scope)
    if not rules:
        return ""
    return ("Editing rules Studio learned for " + SCOPES[scope].lower() + " (problems caught in earlier drafts; "
            "R numbers for same_rule):\n" + "\n".join(_line(r) for r in rules))


# ── How many drawn animations Bryan wants ─────────────────────────────────────
# Bryan's notes like "too many animations" or "more diagrams" move a lean, -3 (fewer) to +3
# (more), kept with the rules. The editor's guidance and its limit per draft follow it
# (insert_guidance, insert_cap); a note the other way moves it back.

LEAN_MIN, LEAN_MAX = -3, 3
_ANIM = r"(animation|animated|diagram|drawing|drawn|illustration|graphic|insert)s?"
_FEWER = re.compile(rf"(too many|fewer|less|no more|not so many|without( the)?|drop (the |all )?|lose (the )?)\s*(\w+\s+)?{_ANIM}"
                    rf"|\b{_ANIM}\b[^.!?]{{0,30}}(too many|too much|distract|unnecessary|overkill|cluttered|busy)", re.I)
_MORE = re.compile(rf"\b(more|add (an?|some)|could use (an?|some)|needs? (an?|some))\s+(\w+\s+)?{_ANIM}", re.I)


def note_lean(text: str) -> str:
    """"fewer", "more" or "" for a note's words, when Claude didn't say (a fallback)."""
    text = text or ""
    if _FEWER.search(text):
        return "fewer"
    if _MORE.search(text):
        return "more"
    return ""


def insert_lean(data: dict | None = None) -> int:
    lean = (data or _load()).get("inserts", {}).get("lean", 0)
    return lean if isinstance(lean, int) else 0


def insert_feedback(direction: str, note: str = "", when: str | None = None) -> int:
    """One of Bryan's notes asked for fewer or more drawn animations. Returns the new lean."""
    if direction not in ("fewer", "more") or not settings.LEARN_RULES:
        return insert_lean()
    with _lock:
        data = _load()
        pref = data.setdefault("inserts", {"lean": 0, "fewer": 0, "more": 0, "notes": []})
        pref["lean"] = min(max(pref.get("lean", 0) + (-1 if direction == "fewer" else 1), LEAN_MIN), LEAN_MAX)
        pref[direction] = pref.get(direction, 0) + 1
        pref["last"] = when or date.today().isoformat()
        pref["notes"] = ([re.sub(r"\s+", " ", note).strip()[:120]] + pref.get("notes", []))[:5]
        _save(data)
    log.info("Bryan asked for %s drawn animations; the lean is now %+d", direction, pref["lean"])
    return pref["lean"]


def insert_cap(limit: int, data: dict | None = None) -> int:
    """The most drawn animations a draft gets: the setting's, fewer when Bryan asked for fewer."""
    lean = insert_lean(data)
    return min(limit, 1) if lean <= -2 else min(limit, 2) if lean == -1 else limit


def insert_guidance(data: dict | None = None) -> str:
    """A line for the editor's prompt on how freely to draw, from Bryan's notes ("" when he hasn't said)."""
    lean = insert_lean(data)
    if lean <= -2:
        return ("Bryan has asked for fewer drawn animations more than once: draw one only when the footage truly "
                "can't show what he explains, never to fill time.")
    if lean == -1:
        return "Bryan found a draft had too many drawn animations: be sparing, and draw one only where it clearly helps."
    if lean >= 2:
        return ("Bryan has asked for more diagrams: wherever he explains something visual the footage doesn't show, "
                "draw it (within the limit).")
    if lean == 1:
        return "Bryan asked for more diagrams: lean towards drawing what he explains when the footage doesn't show it."
    return ""


def _insert_line(data: dict) -> str:
    pref = data.get("inserts") or {}
    lean = pref.get("lean", 0)
    if not lean:
        return ""
    ask = "fewer" if lean < 0 else "more"
    n = pref.get(ask, 0)
    return (f"- Drawn animations: Bryan asked for {ask} ({'once' if n == 1 else 'twice' if n == 2 else f'{n} times'}"
            + (f", last {_when(pref['last'])}" if pref.get("last") else "") + ")"
            + ("; Studio draws at most one per Reel." if lean <= -2 else "."))


# ── The playbook section ──────────────────────────────────────────────────────

def section(data: dict | None = None) -> str:
    data = data or _load()
    parts = [HEADING, "",
             "Studio keeps this section itself: problems it caught and fixed in its own drafts, and editing "
             "points from Bryan's notes. The editor and Studio's read follow them on every post. To drop one, "
             "use Forget in Review (Rules Studio taught itself). Everything else in this playbook is Bryan's, "
             "and Studio never changes it."]
    for scope, label in SCOPES.items():
        rules = sorted((r for r in data["rules"] if r["scope"] == scope and r["status"] == "active"),
                       key=lambda r: (-r["count"], r["id"]))
        extra = _insert_line(data) if scope == "reel" else ""
        if rules or extra:
            parts += ["", f"### {label}", *(_line(r) for r in rules), *([extra] if extra else [])]
    return "\n".join(parts)


def split(text: str) -> tuple[str, str, str]:
    """The playbook around Studio's section: (before, section, after); section is "" when absent."""
    if START in text and END in text and text.index(START) < text.index(END):
        a, b = text.index(START), text.index(END) + len(END)
        return text[:a], text[a:b], text[b:]
    return text, "", ""


def _place(text: str, block: str) -> str:
    before, current, after = split(text)
    if current:
        return before + block + after
    if "## Notes from reviews" in text:  # Bryan's notes stay last (playbook.remember appends there)
        i = text.index("## Notes from reviews")
        return text[:i] + block + "\n\n" + text[i:]
    return text.rstrip() + "\n\n" + block + "\n"


def sync_playbook(data: dict | None = None) -> None:
    """Write Studio's section into the playbook (only once there's a rule in it)."""
    import playbook
    data = data or _load()
    text = playbook.text()
    has_rules = any(r["status"] == "active" for r in data["rules"]) or bool(insert_lean(data))
    if not has_rules and not split(text)[1]:
        return
    new = _place(text, f"{START}\n{section(data)}\n{END}")
    if new != text:
        playbook.ensure().write_text(new, encoding="utf-8")


def bryan_text() -> str:
    """The playbook without Studio's section: the part Bryan edits."""
    import playbook
    before, current, after = split(playbook.text())
    if not current:
        return before
    return before.rstrip("\n") + ("\n\n" + after.lstrip("\n") if after.strip() else "\n")


def save_bryan_text(text: str) -> None:
    """Bryan's edit of his part of the playbook; Studio's section goes back where it was (before
    Notes from reviews)."""
    import playbook
    text = text.replace("\r\n", "\n")
    before, pasted, after = split(text)
    if pasted:  # a copy of Studio's section pasted back in: Studio writes its own
        text = before.rstrip("\n") + "\n\n" + after.lstrip("\n")
    for marker in (START, END):
        text = text.replace(marker, "")
    playbook.ensure().write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    sync_playbook()


# ── Bryan's notes ─────────────────────────────────────────────────────────────

def from_notes(conn, notes: list[dict], addressed: list[dict], scope: str) -> list[dict]:
    """After a send-back: Bryan's notes the editor marked as editing problems become rules
    (feedback.kind and feedback.rule say so, for Review's one line). Never raises."""
    out = []
    try:
        ids = {n["id"] for n in notes}
        for a in addressed or []:
            if a.get("note_id") not in ids or a.get("kind") not in ("editing", "taste"):
                continue
            rule = ""
            if a["kind"] == "editing" and (a.get("rule") or "").strip():
                learned = learn([{"rule": a["rule"], "same_rule": a.get("same_rule")}], scope, "bryan")
                rule = learned[0]["text"] if learned and learned[0]["status"] == "active" else ""
                out += learned
            conn.execute("UPDATE feedback SET kind = ?, rule = ? WHERE id = ?", (a["kind"], rule or None, a["note_id"]))
            # "Too many animations" / "more diagrams": how freely the editor draws from now on.
            lean = a.get("animations")
            if lean not in ("fewer", "more", "none"):
                lean = note_lean(next((n.get("body", "") for n in notes if n["id"] == a["note_id"]), ""))
            if lean in ("fewer", "more"):
                insert_feedback(lean, next((n.get("body", "") for n in notes if n["id"] == a["note_id"]), ""))
        conn.commit()
    except Exception as e:
        log.warning("Couldn't sort Bryan's notes into rules: %s: %s", type(e).__name__, e)
    return out


# ── The trend ─────────────────────────────────────────────────────────────────

_LOG = """
CREATE TABLE IF NOT EXISTS self_fix_log (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    production_id INTEGER NOT NULL,
    version       INTEGER NOT NULL,
    scope         TEXT NOT NULL,     -- reel | carousel
    caught        INTEGER NOT NULL,  -- editing problems in the first pass (Studio's checks + its read)
    fixed         INTEGER NOT NULL,  -- of those, fixed before Bryan saw the draft
    left_over     INTEGER NOT NULL,  -- fixes still on the read Bryan sees
    rounds        INTEGER NOT NULL,
    usd           REAL
);
CREATE INDEX IF NOT EXISTS self_fix_log_at ON self_fix_log (at);
"""


def log_draft(conn, production_id: int, version: int, scope: str, caught: int, fixed: int, left: int,
              rounds: int, usd: float) -> None:
    import catalog
    conn.executescript(_LOG)
    conn.execute("DELETE FROM self_fix_log WHERE production_id = ? AND version = ?", (production_id, version))
    conn.execute("""INSERT INTO self_fix_log (at, production_id, version, scope, caught, fixed, left_over, rounds, usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (catalog.now(), production_id, version, scope, caught, fixed, left, rounds, round(usd, 4)))
    conn.commit()


def trend(conn, scope: str | None = None, days: int = 30, now: datetime | None = None) -> dict:
    """Editing problems Studio caught itself per draft: the average over the last `days` and
    the `days` before, so Review can say whether it's getting better."""
    conn.executescript(_LOG)
    now = now or datetime.now(timezone.utc)
    edges = [(now - timedelta(days=days * k)).isoformat(timespec="seconds") for k in (0, 1, 2)]
    out = {}
    for name, (hi, lo) in (("recent", (edges[0], edges[1])), ("before", (edges[1], edges[2]))):
        row = conn.execute(f"""SELECT COUNT(*), AVG(caught) FROM self_fix_log WHERE at > ? AND at <= ?
                               {'AND scope = ?' if scope else ''}""", (lo, hi, *([scope] if scope else []))).fetchone()
        out[name] = {"drafts": row[0], "avg": round(row[1], 1) if row[1] is not None else None}
    return {"days": days, **out}
