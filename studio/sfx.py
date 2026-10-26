"""
Soft sound effects on a Reel's graphics (Bryan, 2026-10-26, "yes ok": "a gentle whoosh as a label slides in, a tick
on the numbers panel, a soft chime on the end card. Calm, never jarring"). Before this a Reel had his voice and the
music only. His taste (music.TASTE): soft, warm, unhurried; nothing that competes with the voice.

  1. Where (cues, place): a few roles, each timed to its graphic's own animation (editor.py's Site Notes timings):
       in       a label, card or callout coming in: a soft whoosh as it slides in (from its start)
       count    a numbers panel: a soft tick as each bar grows (at most COUNT_MAX), instead of the whoosh
       picture  a drawn animation or a picture dissolving in: a very soft air whoosh
       end      the end card: one soft chime as the mark rises
     Sparse on purpose: never two within GAP seconds, none in the hook's first HOOK_CLEAR second, none within
     CUT_CLEAR of a cut in his speech, at most one per EVERY seconds of draft (and settings.SFX_MAX), the end chime
     first. Never on the loudest part of a spoken word: moved into the nearest gap (at most SHIFT); where no gap is
     near, it dips DIP_DB under those moments, or is left out when it would mostly sit on them. One that touches a
     word at all plays DUCK_DB quieter.
  2. What (effects, fetched once, kept in Studio/SoundEffects for every draft after): Epidemic Sound's own sound
     effects when its MCP server offers a tool for them (asked with tools/list each time a new effect is wanted,
     searched with calm, soft words per role; only for the accounts its licence covers, music.allowed_for), else
     Openverse's public API (no key), only effects marked CC0 or public domain, never anything else. Never
     generated. Each effect's source, id, title and licence go beside the draft (edl["sfx"]) and into its checks.
  3. How loud (editor.mix_final): each effect's loudest moment settings.SFX_LEVEL_DB (24) under the loud parts of
     his levelled voice, a short fade in and out, measured after and turned down if it came out louder; mixed in the
     draft's one encode with the voice and the music.
  4. Bryan's say: a note ("no sound effects", "the whoosh is too loud", "no chime") turns them off, down or one
     kind off for that post from then on (edl["sfx_pref"], pref_from_notes); Studio's read is told they're
     deliberate and may flag one as too loud or jarring (fix_actions' "sound_effects": drop one, or all quieter).

Free: Epidemic's are in Bryan's subscription, Openverse's CC0. Slideshow Reels (slideshow.py) get none.
"""
import json
import logging
import math
import re
import time
from pathlib import Path

import numpy as np
import requests

import settings

log = logging.getLogger("studio.sfx")

RATE = 48000
GAP = 1.5           # seconds between two effects at the least
HOOK_CLEAR = 1.0    # none in the hook's first second
CUT_CLEAR = 0.4     # none this close to a cut in his speech
EVERY = 6.0         # at most one effect per this many seconds of draft
SHIFT = 0.3         # how far an effect may move to clear a loud word
DUCK_DB = 6.0       # quieter when it touches a word at all
DIP_DB = 12.0       # and this much quieter still over a word's loudest part
MAX_ON_LOUD = 0.5   # left out when more of its loud part than this would sit on words' loudest parts
COUNT_MAX = 3       # ticks on a numbers panel
RETRY_HOURS = 6     # a search that found nothing (or failed) is tried again after this long

# Each role: what it's for, the words it's searched with (calm and soft first), the longest it plays, and when it
# starts against its graphic (seconds after the graphic's start).
ROLES = {
    "in": {"what": "a soft whoosh as it comes in", "queries": ["soft whoosh", "gentle swish", "soft swoosh"],
           "seconds": 1.0, "after": 0.0},
    "count": {"what": "a soft tick as each bar grows", "queries": ["soft tick", "soft click", "gentle tap"],
              "seconds": 0.3, "after": 0.3, "step": 0.14},  # the bars grow from 0.3 s, 0.14 s apart (editor._site_card)
    "picture": {"what": "a very soft air whoosh as it dissolves in",
                "queries": ["soft air whoosh", "gentle air swish", "soft breath whoosh"], "seconds": 1.4, "after": 0.0},
    "end": {"what": "a soft chime on the end card", "queries": ["soft chime", "gentle chime", "soft bell"],
            "seconds": 1.6, "after": 0.12},  # the mark rises at +0.12 s (editor._site_end_html)
}
ORDER = {"end": 0, "in": 1, "count": 1, "picture": 2}   # when there's room for only some: the end chime first
NAMES = {"in": "whoosh", "count": "tick", "picture": "air whoosh", "end": "chime"}
# Words in a title that say an effect isn't calm (Bryan: "never jarring").
HARSH = re.compile(r"\b(loud|impact|hit|boom|bang|explo\w*|glitch|scream\w*|alarm|horn|laser|punch|heavy|hard|"
                   r"distort\w*|crash|gun\w*|siren|buzz\w*|brutal|aggressive|riser|slam|smash|thud|metal\w*|static)\b", re.I)
FREE_LICENCES = {"cc0": "CC0 (public domain dedication)", "pdm": "Public Domain Mark"}


def folder() -> Path:
    return settings.ROOT / "SoundEffects"


# ── The effects: fetched once, kept for every draft ───────────────────────────

def _index() -> dict:
    try:
        return json.loads((folder() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"effects": {}, "tried": {}}


def _save_index(index: dict) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    tmp = folder() / "index.json.tmp"
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder() / "index.json")


def _epidemic_ok(brand: str) -> bool:
    import music
    return bool(settings.EPIDEMIC_SOUND_API_KEY) and music.allowed_for(brand)


def effect_for(role: str, brand: str, index: dict | None = None) -> dict | None:
    """The kept effect for a role that this account may use (Epidemic's only where its licence covers the
    account), its prepared file still there; None when there's none yet."""
    index = index or _index()
    for e in index["effects"].get(role) or []:
        if e["source"] == "epidemic" and not _epidemic_ok(brand):
            continue
        if (folder() / e["file"]).exists():
            return e
    return None


def ensure(roles, brand: str) -> tuple[dict, list[str]]:
    """The effect for each role, fetched the first time only (Epidemic's sound effects when its server offers
    them for this account, else Openverse's CC0 / public domain ones). Returns ({role: effect}, notes)."""
    index, found, notes, changed = _index(), {}, [], False
    for role in roles:
        e = effect_for(role, brand, index)
        if e:
            found[role] = e
            continue
        sources = (["epidemic"] if _epidemic_ok(brand) else []) + ["openverse"]
        for source in sources:
            key = f"{source}:{role}"
            if time.time() - index["tried"].get(key, 0) < RETRY_HOURS * 3600:
                continue
            try:
                e = (_from_epidemic if source == "epidemic" else _from_openverse)(role)
            except Exception as ex:  # a draft without this effect beats no draft
                log.warning("Sound effect search (%s, %s) failed: %s: %s", source, role, type(ex).__name__, ex)
                e = None
            changed = True
            if not e:
                index["tried"][key] = time.time()
                continue
            index["tried"].pop(key, None)
            index["effects"].setdefault(role, []).append(e)
            found[role] = e
            break
        if role not in found:
            notes.append(f"no {NAMES[role]} sound this time (none found yet)")
    if changed:
        _save_index(index)
    return found, notes


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "effect"


def _calm(title: str) -> bool:
    return not HARSH.search(title or "")


def _download(url: str, path: Path, key: str = "") -> None:
    """A file to disk (at most 8 MB). key: Epidemic's, sent only to Epidemic and only when a link asks for it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, stream=True, timeout=60)
    if r.status_code in (401, 403) and key:
        r.close()
        r = requests.get(url, stream=True, timeout=60, headers={"Authorization": f"Bearer {key}"})
    with r:
        r.raise_for_status()
        size = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > 8 << 20:
                    raise RuntimeError("the sound effect file is too big")
                f.write(chunk)
    if path.stat().st_size < 1000:
        raise RuntimeError("the downloaded sound effect is too small to be audio")


def prepare(src: Path, role: str, out: Path) -> dict:
    """The effect made ready for a Reel: its silence at the start trimmed (so it starts on its moment), at most the
    role's length, softened a little (no rumble, no hiss), a short fade in and a long fade out. Returns
    {seconds, body}: its length and its loud part (from, to: within 6 dB of its peak)."""
    import extract
    raw = out.with_suffix(".f32")
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-af",
                  f"aresample={RATE},aformat=channel_layouts=stereo,highpass=f=120,lowpass=f=9000",
                  "-f", "f32le", str(raw)])
    x = np.fromfile(raw, dtype="<f4").reshape(-1, 2).astype(np.float64)
    raw.unlink(missing_ok=True)
    env = _envelope(x.mean(axis=1), 0.01)
    if not len(env) or env.max() < 1e-4:
        raise RuntimeError("the sound effect is silent")
    first = int(np.argmax(env > env.max() * 10 ** (-30 / 20)))   # where it starts (30 dB under its peak)
    x = x[max(0, first * RATE // 100 - RATE // 200):]
    x = x[:int(ROLES[role]["seconds"] * RATE)]
    n = len(x)
    fin, fout = min(int(0.008 * RATE), n // 4), max(int(n * 0.45), 1)
    ramp = np.ones(n)
    ramp[:fin] = np.linspace(0, 1, fin) if fin else ramp[:fin]
    ramp[n - fout:] = 0.5 + 0.5 * np.cos(np.linspace(0, math.pi, fout))
    x *= ramp[:, None]
    env = _envelope(x.mean(axis=1), 0.02)
    loud = np.nonzero(env >= env.max() * 10 ** (-6 / 20))[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.f32")
    x.astype("<f4").tofile(tmp)
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", str(tmp),
                  "-c:a", "pcm_f32le", str(out)])
    tmp.unlink(missing_ok=True)
    return {"seconds": round(n / RATE, 3), "body": [round(loud[0] * 0.02, 3), round((loud[-1] + 1) * 0.02, 3)]}


def _envelope(x, step: float) -> np.ndarray:
    """RMS per `step` seconds."""
    k = max(1, int(step * RATE))
    m = len(x) // k
    return np.sqrt(np.mean(np.square(x[:m * k]).reshape(m, k), axis=1)) if m else np.zeros(0)


def _keep(role: str, src: Path, meta: dict) -> dict:
    """A downloaded effect prepared and described for the index (the original is kept beside it)."""
    name = f"{role}-{meta['source']}-{_slug(meta['id'])}"
    ready = folder() / f"{name}.wav"
    meta.update(prepare(src, role, ready), file=ready.name, original=src.name, role=role,
                fetched=time.strftime("%Y-%m-%d"))
    return meta


# Openverse (api.openverse.org): public, no key. Only CC0 and public domain, sound effects, short.
def _from_openverse(role: str) -> dict | None:
    headers = {"User-Agent": "UrbanMakersStudio/1 (sound effects for Reels)"}
    for q in ROLES[role]["queries"]:
        r = requests.get(settings.OPENVERSE_URL, params={
            "q": q, "license": "cc0,pdm", "category": "sound_effect", "length": "shortest", "page_size": 20,
            "mature": "false"}, headers=headers, timeout=30)
        r.raise_for_status()
        for item in r.json().get("results") or []:
            if not free(item) or not item.get("url") or not _calm(item.get("title", "")):
                continue
            ms = item.get("duration")
            if isinstance(ms, (int, float)) and not (100 <= ms <= 8000):  # milliseconds
                continue
            ext = re.sub(r"[^a-z0-9]", "", str(item.get("filetype") or "mp3").lower())[:5] or "mp3"
            src = folder() / f"{role}-openverse-{_slug(item['id'])}.{ext}"
            try:
                _download(item["url"], src)
                return _keep(role, src, {
                    "source": "openverse", "id": str(item["id"]), "title": str(item.get("title") or "")[:120],
                    "creator": str(item.get("creator") or "")[:80], "provider": str(item.get("provider") or ""),
                    "licence": item["license"].lower(), "licence_name": FREE_LICENCES[item["license"].lower()],
                    "licence_url": str(item.get("license_url") or ""), "page": str(item.get("foreign_landing_url") or ""),
                    "query": q})
            except Exception as e:  # the next one
                log.info("Openverse effect %s didn't work: %s: %s", item.get("id"), type(e).__name__, e)
    return None


def free(item: dict) -> bool:
    """Is this Openverse result one Studio may use: CC0 or the public domain mark (and nothing that says
    otherwise), a sound effect?"""
    licence = str(item.get("license") or "").lower()
    url = str(item.get("license_url") or "")
    if licence not in FREE_LICENCES or (url and "publicdomain" not in url):
        return False
    return item.get("category") in (None, "", "sound_effect") and not item.get("mature")


# Epidemic Sound (music.py's MCP server). The code can't know ahead of time whether, or how, the server offers sound
# effects, so each time a new effect is wanted it asks tools/list: a tool for sound effects (by its name or
# description), else a search tool with a filter for them (an enum offering sound effects). None: Openverse instead.
_SFX = re.compile(r"sound[\s_-]?effects?|\bsfx\b|soundeffects?", re.I)
_SEARCHY = re.compile(r"search|find|browse|list|query", re.I)
_QUERY_KEYS = ("query", "q", "term", "search", "searchTerm", "search_term", "keyword", "keywords", "text", "prompt")
_ID_KEYS = re.compile(r"(^id$|_id$|Id$|^uuid$)")
_AUDIO = re.compile(r"\.(mp3|wav|ogg|m4a|aac|flac)(\?|$)|download|audio|preview|stream", re.I)


def _props(tool: dict) -> dict:
    return ((tool.get("inputSchema") or {}).get("properties") or {})


def _query_key(tool: dict) -> str | None:
    props = _props(tool)
    for k in _QUERY_KEYS:
        if k in props and (props[k].get("type") in (None, "string")):
            return k
    req = (tool.get("inputSchema") or {}).get("required") or []
    return next((k for k in req if props.get(k, {}).get("type") == "string"), None)


def _enum_values(p: dict) -> list:
    vals = list(p.get("enum") or [])
    for sub in (p.get("items") or {},):
        vals += list(sub.get("enum") or [])
    return vals


def sfx_tool(tools: list[dict]) -> dict | None:
    """How to search Epidemic's sound effects with these tools: {name, query (the argument for the words), fixed
    (other arguments, e.g. a type filter)}, or None when the server offers none."""
    for t in tools:
        text = f"{t.get('name', '')} {t.get('description', '')}"
        if _SFX.search(t.get("name", "")) or (_SFX.search(text) and _SEARCHY.search(text)):
            if _SEARCHY.search(t.get("name", "")) or not re.search(r"download", t.get("name", ""), re.I):
                q = _query_key(t)
                if q:
                    return {"name": t["name"], "query": q, "fixed": {}}
    for t in tools:
        q = _query_key(t)
        if not q or not _SEARCHY.search(t.get("name", "")):
            continue
        for k, p in _props(t).items():
            hit = next((v for v in _enum_values(p) if isinstance(v, str) and _SFX.search(v.replace("_", " "))), None)
            if hit:
                return {"name": t["name"], "query": q, "fixed": {k: [hit] if p.get("type") == "array" else hit}}
    return None


def _download_tool(tools: list[dict]) -> dict | None:
    """A tool that gives a download link for an effect by its id: {name, id (the argument)}."""
    best = None
    for t in tools:
        if not re.search(r"download|file|url|link", t.get("name", ""), re.I):
            continue
        props = _props(t)
        key = next((k for k in props if _ID_KEYS.search(k)), None)
        if not key:
            continue
        text = f"{t['name']} {t.get('description', '')}"
        if _SFX.search(text):
            return {"name": t["name"], "id": key}
        best = best or {"name": t["name"], "id": key}
    return best


def _payload(result: dict):
    """What a tool call returned, as data: its structured content, else the JSON in its text."""
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    out = []
    for c in result.get("content") or []:
        if c.get("type") == "text":
            try:
                out.append(json.loads(c["text"]))
            except (ValueError, TypeError):
                out.append(c["text"])
    return out[0] if len(out) == 1 else out


def _items(data) -> list[dict]:
    """Every dict in the data that looks like one effect: an id and a title or name."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if any(_ID_KEYS.search(k) for k in x) and any(k in x for k in ("title", "name")):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return found


def _audio_url(x) -> str:
    if isinstance(x, str):
        m = re.search(r"https?://\S+", x)
        return m.group(0).rstrip(").,\"'") if m and _AUDIO.search(m.group(0)) else ""
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, str) and v.startswith("http") and (_AUDIO.search(k) or _AUDIO.search(v)):
                return v
        for v in x.values():
            u = _audio_url(v) if isinstance(v, (dict, list)) else ""
            if u:
                return u
    if isinstance(x, list):
        for v in x:
            u = _audio_url(v)
            if u:
                return u
    return ""


def _seconds(item: dict) -> float | None:
    for k, v in item.items():
        if re.search(r"duration|length|seconds", k, re.I) and isinstance(v, (int, float)) and not isinstance(v, bool):
            return v / 1000 if (re.search(r"ms$|millis", k, re.I) or v > 600) else float(v)
    return None


def _from_epidemic(role: str, es=None) -> dict | None:
    import music
    es = es or music.EpidemicSound()
    es.connect()
    tools = es._post("tools/list")["tools"]
    tool = sfx_tool(tools)
    if not tool:
        log.info("Epidemic Sound's server offers no sound effects tool (%d tools); using Openverse", len(tools))
        return None
    down = _download_tool(tools)
    for q in ROLES[role]["queries"]:
        data = _payload(es.call(tool["name"], {tool["query"]: q, **tool["fixed"]}))
        for item in _items(data):
            ident = str(next(v for k, v in item.items() if _ID_KEYS.search(k)))
            title = str(item.get("title") or item.get("name") or "")
            secs = _seconds(item)
            if not _calm(title) or (secs is not None and not 0.1 <= secs <= 8.0):
                continue
            url = _audio_url(item)
            if not url and down:
                url = _audio_url(_payload(es.call(down["name"], {down["id"]: ident})))
            if not url:
                continue
            src = folder() / f"{role}-epidemic-{_slug(ident)}{Path(url.split('?')[0]).suffix[:5] or '.mp3'}"
            try:
                _download(url, src, settings.EPIDEMIC_SOUND_API_KEY)
                page = next((v for v in item.values() if isinstance(v, str) and "epidemicsound.com" in v
                             and not _AUDIO.search(v)), "")
                return _keep(role, src, {"source": "epidemic", "id": ident, "title": title[:120], "creator": "",
                                         "provider": "Epidemic Sound", "licence": "epidemic",
                                         "licence_name": "Epidemic Sound subscription (Urban Makers)", "licence_url": "",
                                         "page": page, "query": q})
            except Exception as e:
                log.info("Epidemic effect %s didn't work: %s: %s", ident, type(e).__name__, e)
    return None


# ── Where: the cues from the graphics, then the rules ─────────────────────────

def cues(graphics: list[dict], end: dict | None, style: str = "site") -> list[dict]:
    """The effects the draft's graphics would get: [{role, at, ticks (offsets from at), for (what it's for, a key
    that holds through re-renders), title}]. Site Notes' numbers panel gets ticks instead of a whoosh."""
    import editor
    out = []
    for g in graphics:
        if "start" not in g:
            continue
        kind = g.get("style") or "card"
        if kind in ("scene", "picture"):
            role, ticks = "picture", [0.0]
        elif kind == "card" and style == "site" and \
                editor.site_layout("card", g.get("title", ""), g.get("italic", ""), g.get("lines") or [])["form"] == "panel-numbers":
            rows = len(editor._site_numbers([editor.unmark(x) for x in g.get("lines") or [] if editor.unmark(x)]) or [])
            role, ticks = "count", [round(k * ROLES["count"]["step"], 3) for k in range(min(max(rows, 1), COUNT_MAX))]
        else:
            role, ticks = "in", [0.0]
        out.append({"role": role, "at": round(g["start"] + ROLES[role]["after"], 3), "ticks": ticks,
                    "for": f"{kind}:{g.get('title', '')}", "title": g.get("title", "")})
    if end:
        out.append({"role": "end", "at": round(end["start"] + ROLES["end"]["after"], 3), "ticks": [0.0],
                    "for": "end", "title": "the end card"})
    return sorted(out, key=lambda c: c["at"])


def joins(cuts: list[dict]) -> list[float]:
    """Where his speech is cut, on the draft's clock (the joins between pieces)."""
    out, t = [], 0.0
    for c in cuts[:-1]:
        t += c["end"] - c["start"]
        out.append(round(t, 3))
    return out


def loud_parts(words: list[dict], voice=None, rate: int = 1000) -> list[tuple[float, float]]:
    """Each word's loudest part on the draft's clock: where his voice is within 6 dB of that word's peak (voice:
    its level every 1/rate s, from voice_level), else the middle 60% of the word."""
    out = []
    for w in words:
        s, e = w["start"], w["end"]
        if e <= s:
            continue
        if voice is not None and len(voice):
            seg = voice[int(s * rate):max(int(e * rate), int(s * rate) + 1)]
            if len(seg) and seg.max() > 0:
                hot = np.nonzero(seg >= seg.max() * 10 ** (-6 / 20))[0]
                out.append((round(s + hot[0] / rate, 3), round(s + (hot[-1] + 1) / rate, 3)))
                continue
        out.append((round(s + 0.2 * (e - s), 3), round(e - 0.2 * (e - s), 3)))
    return out


def voice_level(path: Path, rate: int = 1000):
    """His voice's level (RMS) every 1/rate s of a draft's cut, from its sound."""
    import extract
    raw = path.with_name(path.stem + "_sfxvoice.f32")
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(path), "-vn", "-af", f"aresample={RATE}",
                  "-ac", "1", "-f", "f32le", str(raw)])
    x = np.fromfile(raw, dtype="<f4").astype(np.float64)
    raw.unlink(missing_ok=True)
    k = RATE // rate
    m = len(x) // k
    return np.sqrt(np.mean(np.square(x[:m * k]).reshape(m, k), axis=1)) if m else np.zeros(0)


def most(total: float) -> int:
    """How many effects a draft of this length may have (each numbers panel's ticks count as one)."""
    return max(0, min(settings.SFX_MAX, max(1, int(total // EVERY))))


def _overlap(a: float, b: float, parts) -> float:
    """How long a to b sits on these parts (seconds, to the millisecond)."""
    return round(sum(max(0.0, min(b, y) - max(a, x)) for x, y in parts), 3)


def place(wanted: list[dict], effects: dict, words: list[dict], cut_at: list[float], total: float,
          voice=None) -> tuple[list[dict], list[str]]:
    """The effects that make it, by the rules (the module's docstring): [{role, at, ticks, for, title, moved,
    duck_db, dips}], and in plain words what was left out and why (for the log; nothing depends on it). An effect
    whose loud part would land on a word's loudest part moves (at most SHIFT) to where it's clear, else to where it
    touches least; it plays DIP_DB quieter over those moments (dips), and is left out when they'd be more than
    MAX_ON_LOUD of its loud part. One that touches a word at all plays DUCK_DB quieter."""
    loud = loud_parts(words, voice)
    spans = [(w["start"], w["end"]) for w in words]
    cap, left = most(total), []
    placed = []
    shifts = sorted({round(k * 0.02, 2) for k in range(-int(SHIFT / 0.02), int(SHIFT / 0.02) + 1)},
                    key=lambda d: (abs(d), -d))
    for c in sorted(wanted, key=lambda c: (ORDER[c["role"]], c["at"])):
        e = effects.get(c["role"])
        if not e:
            continue
        b0, b1 = e["body"]
        body = max(b1 - b0, 0.01)
        length = e["seconds"]
        best = None  # (ticks kept, time on loud parts, |shift|), t, the ticks kept
        for d in shifts:
            t = round(c["at"] + d, 3)
            kept, on_loud = [], 0.0
            for off in c["ticks"]:
                a = t + off
                if a < HOOK_CLEAR or a + min(length, 0.3) > total + 0.001:
                    continue
                if any(abs(a - j) < CUT_CLEAR or a < j < a + b1 for j in cut_at):
                    continue
                hit = _overlap(a + b0, a + b1, loud)
                if hit > MAX_ON_LOUD * body:
                    continue
                kept.append(off)
                on_loud += hit
            if not kept:
                continue
            first, last = t + kept[0], t + kept[-1]
            if any(first < o["at"] + o["ticks"][-1] + GAP and o["at"] - GAP < last for o in placed):
                continue
            score = (-len(kept), round(on_loud, 3), abs(d))
            if best is None or score < best[0]:
                best = (score, t, kept)
            if len(kept) == len(c["ticks"]) and on_loud == 0:
                break
        if best is None:
            left.append(f"{c['role']} at {c['at']:.1f}s ({c['title']}): no clear moment")
            continue
        if c["role"] != "end" and len(placed) >= cap:  # the end chime went first; the rest as there's room
            left.append(f"{c['role']} at {c['at']:.1f}s ({c['title']}): enough for a {total:.0f}s draft")
            continue
        _, t, kept = best
        over = any(t + off + b0 < y and x < t + off + b1 for off in kept for x, y in spans)
        dips = sorted({(round(float(max(x, t + off)), 3), round(float(min(y, t + off + length)), 3)) for off in kept
                       for x, y in loud if _overlap(t + off, t + off + length, [(x, y)]) > 0})
        placed.append({"role": c["role"], "at": round(t + kept[0], 3), "ticks": [round(off - kept[0], 3) for off in kept],
                       "for": c["for"], "title": c["title"], "moved": round(t - c["at"], 2),
                       "duck_db": DUCK_DB if over else 0.0, "dips": [list(x) for x in dips]})
    return sorted(placed, key=lambda p: p["at"]), left


# ── Bryan's say: notes and the read's fix ─────────────────────────────────────
_WHAT = r"\b(sound[\s-]?effects?|sfx|whoosh\w*|swoosh\w*|swish\w*|ticks?|ticking|chimes?|dings?|effects? sounds?)\b"
_OFF = re.compile(r"\b(no|without|remove|drop|lose|cut|kill|mute|skip|stop|don'?t (?:want|need|use|add)|turn off|"
                  r"take (?:out|off)|get rid of)\b[^.!?,;]{0,25}?\b" + _WHAT + r"|" + _WHAT +
                  r"[^.!?,;]{0,20}?\b(off|out|away|gone|not needed|unnecessary|jarring|annoying|distracting|cheesy|too much)\b", re.I)
_QUIETER = re.compile(_WHAT + r"[^.!?,;]{0,25}?\b(too loud|loud|quieter|softer|lower|down|subtler)\b|"
                      r"\b(quieter|softer|lower|turn down|subtler)\b[^.!?,;]{0,25}?\b" + _WHAT, re.I)
_LOUDER = re.compile(_WHAT + r"[^.!?,;]{0,25}?\b(louder|too quiet|too soft|can'?t hear|barely hear)\b|"
                     r"\b(louder|turn up)\b[^.!?,;]{0,25}?\b" + _WHAT, re.I)
_ON = re.compile(r"\b(add|bring back|put back|want|keep|more)\b[^.!?,;]{0,20}?\b" + _WHAT, re.I)


def _roles_of(word: str) -> list[str]:
    w = word.lower()
    if re.match(r"(whoosh|swoosh|swish)", w):
        return ["in", "picture"]
    if re.match(r"tick", w):
        return ["count"]
    if re.match(r"(chime|ding)", w):
        return ["end"]
    return []  # all of them


def note_ask(text: str) -> dict | None:
    """What a note asks of the sound effects: {kind: off | quieter | louder | on, roles ([] = all)}, else None."""
    text = text or ""
    for kind, rx in (("off", _OFF), ("quieter", _QUIETER), ("louder", _LOUDER), ("on", _ON)):
        m = rx.search(text)
        if m:
            what = next((g for g in m.groups() if g and re.fullmatch(_WHAT, g, re.I)), "")
            return {"kind": kind, "roles": _roles_of(what)}
    return None


def pref_from_notes(notes: list[dict], previous: dict | None) -> tuple[dict, list[str]]:
    """The draft's sound effects as Bryan asked (carried from the draft before, then his notes on it): {off, level
    (dB, added to the usual), roles_off, drop}; and what changed, in plain words."""
    pref = {"off": False, "level": 0.0, "roles_off": [], "drop": [], **(previous or {})}
    did = []
    for n in notes:
        ask = note_ask(n.get("body", ""))
        if not ask:
            continue
        roles = ask["roles"]
        if ask["kind"] == "off":
            if roles:
                pref["roles_off"] = sorted(set(pref["roles_off"]) | set(roles))
                did.append(f"took the {NAMES[roles[0]]} sound off, as your note asked")
            else:
                pref["off"] = True
                did.append("took the sound effects off, as your note asked")
        elif ask["kind"] == "on":
            if pref["off"] or set(roles or ROLES) & set(pref["roles_off"]):
                if pref["off"] and roles:  # just these back
                    pref["roles_off"] = sorted(set(pref["roles_off"]) | (set(ROLES) - set(roles)))
                else:
                    pref["roles_off"] = sorted(set(pref["roles_off"]) - set(roles or ROLES))
                pref["off"] = False
                did.append("put the sound effects back, as your note asked")
        else:
            step = -6.0 if ask["kind"] == "quieter" else 4.0
            pref["level"] = level(pref["level"] + step)
            did.append(f"made the sound effects {'quieter' if step < 0 else 'a little louder'}, as your note asked")
    return pref, did


def level(v: float) -> float:
    """A level change kept in its range: never closer to his voice than 12 dB, at most 18 dB quieter."""
    return round(min(max(float(v), -18.0), settings.SFX_LEVEL_DB - 12.0, 6.0), 1)


def patch(edl: dict, a: dict) -> str:
    """The read's fix "sound_effects": item (E2 = 2) with field "remove" takes that one off; field "remove" with no
    item takes them all off; amount (dB, negative = quieter) turns them all down or up. Raises ValueError with
    why when it can't."""
    placed = (edl.get("sfx") or {}).get("placed") or []
    pref = edl["sfx_pref"] = {"off": False, "level": 0.0, "roles_off": [], "drop": [], **(edl.get("sfx_pref") or {})}
    item, amount = a.get("item"), a.get("amount")
    if a.get("field") == "remove":
        if item is None:
            if pref["off"]:
                raise ValueError("the sound effects are already off")
            pref["off"] = True
            return "took the sound effects off"
        if not 1 <= int(item) <= len(placed):
            raise ValueError(f"there's no sound effect E{item}")
        p = placed[int(item) - 1]
        pref["drop"] = sorted(set(pref["drop"]) | {p["for"]})
        return f"took off the {NAMES[p['role']]} at {p['at']:.1f}s"
    if amount is None or abs(float(amount)) < 0.5:
        raise ValueError("the read didn't say what to change in the sound effects")
    new = level(pref["level"] + float(amount))
    if abs(new - pref["level"]) < 0.5:
        raise ValueError("the sound effects are already as far as they go")
    old, pref["level"] = pref["level"], new
    return f"turned the sound effects {'down' if new < old else 'up'} {abs(new - old):.0f} dB"


# ── The draft's effects (editor._render) ──────────────────────────────────────

def plan(edl: dict, graphics: list[dict], end: dict | None, cuts: list[dict], words: list[dict], total: float,
         brand: str, style: str, picture: Path | None = None) -> tuple[list[dict], list[str]]:
    """The draft's sound effects, ready for the mix: [{file, at, seconds, duck_db, dips}] (each tick its own), and the
    lines for its checks. Kept beside the draft in edl["sfx"]: each one's role, moment, what it's for, its source,
    id, title and licence. Nothing (and no edl["sfx"]) when settings.SFX is off."""
    if not settings.SFX:
        edl.pop("sfx", None)
        return [], []
    pref = edl.get("sfx_pref") or {}
    if pref.get("off"):
        edl["sfx"] = {"on": False, "why": "Bryan asked for none on this post"}
        return [], []
    wanted = [c for c in cues(graphics, end, style) if c["role"] not in (pref.get("roles_off") or [])
              and c["for"] not in (pref.get("drop") or [])]
    if not wanted or most(total) <= 0:
        edl["sfx"] = {"on": True, "placed": [], "level_db": settings.SFX_LEVEL_DB - float(pref.get("level") or 0)}
        return [], []
    effects, notes = ensure(sorted({c["role"] for c in wanted}), brand)
    voice = None
    if picture is not None and words:
        try:
            voice = voice_level(picture)
        except Exception as e:  # the words' middles stand in for his voice's loud parts
            log.info("Couldn't read the voice's level for the sound effects: %s", e)
    placed, left = place(wanted, effects, words, joins(cuts), total, voice)
    if left:
        log.info("Sound effects left out: %s", "; ".join(left))
    below = settings.SFX_LEVEL_DB - float(pref.get("level") or 0)
    layer, kept = [], []
    for n, p in enumerate(placed, 1):
        e = effects[p["role"]]
        for off in p["ticks"]:
            at = p["at"] + off
            sec = round(min(e["seconds"], total - at - 0.02), 3)
            layer.append({"file": folder() / e["file"], "at": round(at, 3), "seconds": sec, "duck_db": p["duck_db"],
                          "dips": [d for d in p["dips"] if d[0] < at + sec and at < d[1]]})
        kept.append({"n": n, **p, **{k: e.get(k, "") for k in ("source", "id", "licence", "licence_name", "licence_url",
                                                                "creator", "page")}, "effect": e.get("title", "")})
    edl["sfx"] = {"on": True, "placed": kept, "level_db": below}
    lines = []
    if kept:
        lines.append(f"added {len(kept)} soft sound effect{'s' if len(kept) != 1 else ''} as the graphics come in, "
                     f"well under your voice: " + "; ".join(describe(p) for p in kept))
    return layer, lines + notes


def describe(p: dict) -> str:
    """One effect in a few words, with where it came from ("whoosh at 4.2s as "Two days to dry" comes in
    (Openverse, CC0: "Soft Whoosh" by X)")."""
    what = {"in": "a whoosh", "count": f"{len(p.get('ticks') or [0])} tick{'s' if len(p.get('ticks') or [0]) != 1 else ''}",
            "picture": "an air whoosh", "end": "a chime"}[p["role"]]
    where = "on the end card" if p["role"] == "end" else f"as \"{p['title']}\" comes in"
    src = ("Epidemic Sound" if p.get("source") == "epidemic" else "Openverse") + (
        f", {p['licence'].upper()}" if p.get("source") == "openverse" else "")
    who = f" by {p['creator']}" if p.get("creator") else ""
    return f"{what} at {p['at']:.1f}s {where} ({src}: \"{p.get('effect', '')}\"{who})"


def listed(edl: dict) -> str:
    """The draft's effects for Studio's read (numbered E1...), or "" when it has none."""
    s = edl.get("sfx") or {}
    if not s.get("on") or not s.get("placed"):
        return ""
    return ("Sound effects (Studio adds a few on purpose, soft and well under his voice, timed to the graphics; numbered: "
            "E1 is item 1): " + "; ".join(f"E{p['n']} " + describe(p) for p in s["placed"]))
