"""
The trade glossary: words Whisper should hear right and captions should spell right
(Studio/glossary.txt, Bryan can add to it).

One term per line ("MCST", "waterproofing", "Lamitak"); a line "wrong -> right" is a mis-hearing
Studio always corrects in captions ("M C S T -> MCST"); "#" starts a comment. The file is made
from SEED the first time it's read; Studio never rewrites it after that, it only adds at the end the
terms someone corrected in Review with "Remember this word" (add).

Used three ways: Whisper is given the terms as hotwords when it transcribes (transcribe.py), the
caption spell-check before each render is given them (spelling.py), and the "wrong -> right"
lines are applied to captions by code, free.
"""
import logging
import re
import threading

import settings

log = logging.getLogger("studio.glossary")

PATH = settings.ROOT / "glossary.txt"

SEED = """\
# Studio's trade glossary. One word or name per line, spelt the way captions should show it.
# Whisper is nudged to hear these, and Studio's caption spell-check trusts them.
# A line "wrong -> right" is a mis-hearing Studio always corrects in captions, e.g.:
#   M C S T -> MCST
# Lines starting with # are notes. Add your own at the end.

# Singapore housing and authorities
HDB
BTO
EC
MCST
condo
URA
BCA
PUB
SCDF
NEA
LTA
SP Group
CPF
HIP
ID firm
resale flat
executive condo
landed
strata

# The work
renovation
reno
hacking
screed
skim coat
plastering
false ceiling
cove lighting
partition
feature wall
carpentry
laminate
veneer
plywood
MDF
HDF
solid surface
quartz
sintered stone
countertop
backsplash
wardrobe
cabinet
shoe cabinet
TV console
platform bed
tiling
tiles
homogeneous tiles
porcelain
ceramic
vinyl
SPC flooring
overlay
grout
epoxy grout
tile adhesive
skirting
waterproofing
membrane
ponding test
water heater
plumbing
sanitary
concealed piping
electrical
DB box
circuit breaker
ELCB
RCCB
power point
lighting point
rewiring
licensed electrician
aircon
trunking
dado

# Brand names (add the ones you use)
Lamitak
Formica
Hafele
Blum
Mapei
Sika
Laticrete
Weber
Bostik
Nippon Paint
Dulux
Jotun
Schneider
Legrand
Grohe
Hansgrohe
TOTO
Rinnai
Urban Makers
"""

_lock = threading.Lock()
_cache: dict = {"mtime": None, "terms": [], "fixes": {}}


def ensure() -> None:
    """Make the file from SEED the first time (never over Bryan's)."""
    if PATH.exists():
        return
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(SEED, encoding="utf-8")
        log.info("Made the trade glossary at %s", PATH)
    except OSError as e:
        log.warning("Couldn't make the glossary at %s: %s", PATH, e)


def _parse(text: str) -> tuple[list[str], dict[str, str]]:
    terms, fixes = [], {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "->" in line:
            wrong, right = (x.strip() for x in line.split("->", 1))
            if wrong and right:
                fixes[key(wrong)] = right
                if right.lower() not in (t.lower() for t in terms):
                    terms.append(right)
            continue
        if line.lower() not in (t.lower() for t in terms):
            terms.append(line)
    return terms, fixes


def _load() -> dict:
    ensure()
    with _lock:
        try:
            mtime = PATH.stat().st_mtime
        except OSError:
            terms, fixes = _parse(SEED)
            return {"mtime": None, "terms": terms, "fixes": fixes}
        if _cache["mtime"] != mtime:
            try:
                terms, fixes = _parse(PATH.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as e:
                log.warning("Couldn't read the glossary (%s); using the built-in one", e)
                terms, fixes = _parse(SEED)
            _cache.update(mtime=mtime, terms=terms, fixes=fixes)
        return dict(_cache)


def key(text: str) -> str:
    """Words compared without case, spaces or punctuation ("M.C.S.T." = "mcst")."""
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def terms() -> list[str]:
    """Every term, as it should be spelt."""
    return list(_load()["terms"])


def fixes() -> dict[str, str]:
    """{key(wrong): right}: Bryan's own "wrong -> right" lines."""
    return dict(_load()["fixes"])


def known(text: str) -> bool:
    """Is this a glossary term (whatever its case and punctuation)?"""
    k = key(text)
    return bool(k) and any(key(t) == k for t in terms())


ADDED = "# Added from Review (Edit words, Remember this word)"


def add(new: list[str], who: str = "") -> list[str]:
    """Terms Bryan corrected in a transcript with "Remember this word" (transcript_edit.py), added at the
    end of the file under ADDED, once each: never one the glossary already has, never a line that would
    read as something else (a comment, a "wrong -> right"). Returns the ones added."""
    ensure()
    added = []
    with _lock:
        try:
            have = PATH.read_text(encoding="utf-8") if PATH.exists() else SEED
        except (OSError, UnicodeDecodeError) as e:
            log.warning("Couldn't read the glossary to add to it: %s", e)
            return []
        known_keys = {key(t) for t in _parse(have)[0]}
        for t in new:
            t = re.sub(r"\s+", " ", str(t or "")).strip()
            if (not t or len(t) > 40 or len(t.split()) > 4 or "#" in t or "->" in t or key(t) in known_keys
                    or len(key(t)) < 2):
                continue
            known_keys.add(key(t))
            added.append(t)
        if not added:
            return []
        text = have if have.endswith("\n") else have + "\n"
        if ADDED not in text:
            text += f"\n{ADDED}\n"
        try:
            PATH.write_text(text + "".join(f"{t}\n" for t in added), encoding="utf-8")
        except OSError as e:
            log.warning("Couldn't add %s to the glossary: %s", added, e)
            return []
    log.info("Glossary: added %s (by %s)", ", ".join(added), who or "Review")
    return added


# Whisper keeps about 223 tokens of hotwords; the short terms go first (acronyms and names are what
# it mis-hears most), and the list stops well inside that.
HOTWORDS_CHARS = 600


def hotwords() -> str | None:
    """The terms for Whisper (faster-whisper's hotwords), or None when there are none."""
    picked, size = [], 0
    for t in sorted(terms(), key=lambda t: (len(t.split()) > 1, len(t))):
        if size + len(t) + 2 > HOTWORDS_CHARS:
            break
        picked.append(t)
        size += len(t) + 2
    return ", ".join(picked) or None
