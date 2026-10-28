"""
The playbook: plain-text rules the idea generator and the editor follow, kept at
Studio/playbook.md so Bryan can edit it. Starts as a copy of playbook_seed.md;
notes he marks "remember" on the dashboard are appended under "Notes from reviews".
"""
import logging
from datetime import date
from pathlib import Path

from settings import PLAYBOOK

log = logging.getLogger("studio.playbook")

SEED = Path(__file__).resolve().parent / "playbook_seed.md"

# Seed lines changed after a playbook was first copied from the seed: (the old line exactly as the
# seed had it, its replacement). migrate() swaps a line only when it is still exactly the old one, so
# anything Bryan wrote or reworded himself stays as he left it. Bryan, 2026-10-21, on Reels "cut in
# quite a weird way" and ending "abruptly ... without a proper transition out or close": "look at
# whether the style guide is causing this issue".
SEED_CHANGES = [
    ("- End right after the last word or the payoff shot. No intro cards, no long outros.",
     "- End on a complete sentence, then a short beat on the last shot and the Urban Makers end card; never stop "
     "mid-sentence or mid-animation."),
    ("- Talking head: tight cuts, captions on every phrase, B-roll over the words it illustrates.",
     "- Talking head: cut between sentences and ideas, keep the natural pauses inside a sentence; trim only long "
     "silences. Captions on every phrase, B-roll over the words it illustrates."),
]
# Seed lines added after a playbook was first copied: each goes in under its heading once, if the playbook doesn't
# have it word for word (Bryan may have moved or reworded it; a reworded one gets the new line too, so he sees both).
SEED_ADDS = [
    ("## Story", "- Finished homes posts carry no offer, ever: not on a slide, not in the caption, not even a free consultation. The caption ends on a one-word question to the reader. A missing offer is never a fault there (Bryan, 2026-10-28)."),
]
_migrated = False


def migrate(path: Path | None = None) -> list[str]:
    """Bring a playbook copied from an older seed up to date (SEED_CHANGES): each old line still there
    word for word is replaced, nothing else is touched. Safe to run any number of times. Returns the
    new lines it wrote."""
    path = path or PLAYBOOK
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines(keepends=True)
    done = []
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        for old, new in SEED_CHANGES:
            if body.rstrip() == old:
                lines[i] = new + line[len(body):]
                done.append(new)
    have = {ln.rstrip("\r\n").rstrip() for ln in lines}
    for heading, new in SEED_ADDS:
        if new in have or new in done:
            continue
        at = next((i for i, ln in enumerate(lines) if ln.strip() == heading), None)
        if at is None:
            lines += ["\n" if lines and not lines[-1].endswith("\n") else "", f"{new}\n"]
        else:
            end = at + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            while end > at + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, f"{new}\n")
        done.append(new)
    if done:
        path.write_text("".join(lines), encoding="utf-8")
        for new in done:
            log.info("Playbook: replaced an old starting rule with \"%s\"", new)
    return done


def ensure() -> Path:
    global _migrated
    if not PLAYBOOK.exists():
        PLAYBOOK.parent.mkdir(parents=True, exist_ok=True)
        PLAYBOOK.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
    if not _migrated:  # once per start: the starting rules that changed since this copy was made
        _migrated = True
        try:
            migrate(PLAYBOOK)
        except Exception as e:  # the playbook as it is beats no playbook
            log.warning("Couldn't update the playbook's starting rules: %s", e)
    return PLAYBOOK


def text() -> str:
    return ensure().read_text(encoding="utf-8")


def remember(note: str, context: str) -> None:
    with ensure().open("a", encoding="utf-8") as f:
        f.write(f"- {note.strip()} ({context}, {date.today().isoformat()})\n")


def forget(note: str) -> None:
    """Drop a remembered note's line again (its note was taken back)."""
    path = ensure()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = f"- {note.strip()} ("
    hit = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].startswith(start)), None)
    if hit is not None:
        del lines[hit]
        path.write_text("".join(lines), encoding="utf-8")
