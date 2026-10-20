"""
igkit: the parts of Jake Schincariol's MIT-licensed instagram-agent-skill that Studio uses
(see README.md and LICENSE in this folder). Pure Python, standard library only, no network.

  humanize  the safe fixes (invisible characters, typography) and the slop lexicon
  detect    the five-check "does this read machine-written" panel
  hookscore a Reel hook's score on five properties (0-100)
  beats     a script timed into beats (the hook past 3 seconds is flagged)
  caption   what the feed shows before "... more", and the caption lint

Studio's own glue (prompts, the rewrite pass, the allowlist, the voice file) is in
studio/wording.py and studio/voice_file.py; these modules stay close to the original.
"""
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLOP = HERE / "slop.json"
HOOKS = HERE / "hooks.json"
VOICE_TEMPLATE = HERE / "voice_template.md"


@lru_cache(maxsize=1)
def lexicon() -> dict:
    """slop.json: invisible characters, typography, words, phrases and structural tells."""
    return json.loads(SLOP.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def hooks() -> dict:
    """hooks.json: the 26 hook formulas (name, template, example, on-screen version)."""
    return json.loads(HOOKS.read_text(encoding="utf-8"))
