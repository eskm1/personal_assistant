"""
The playbook: plain-text rules the idea generator and the editor follow, kept at
Studio/playbook.md so Bryan can edit it. Starts as a copy of playbook_seed.md;
notes he marks "remember" on the dashboard are appended under "Notes from reviews".
"""
from datetime import date
from pathlib import Path

from settings import PLAYBOOK

SEED = Path(__file__).resolve().parent / "playbook_seed.md"


def ensure() -> Path:
    if not PLAYBOOK.exists():
        PLAYBOOK.parent.mkdir(parents=True, exist_ok=True)
        PLAYBOOK.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
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
