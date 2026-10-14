"""
Send-only Telegram messages to Bryan through Ava's bot, so a tagged clip shows up
in the same chat he already uses. Failures are logged, never raised: a missed
ping must not fail a clip that was processed fine.
"""
import logging

import requests

from settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("studio.notify")

BRAND_LABELS = {"personal": "Personal", "urban_makers": "Urban Makers", "unsure": "Brand unsure"}
FORMAT_LABELS = {"talking_head": "talking head", "day_in_the_life": "day in the life",
                 "compilation": "compilation footage", "other": "other"}


def configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send(text: str) -> bool:
    if not configured():
        log.info("Telegram not configured; would have sent: %s", text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("Telegram send failed: %s", e)
        return False


def _mmss(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def clip_tagged(meta: dict, analysis: dict | None, source_name: str) -> str:
    if analysis is None:
        return (f"Studio: saved {source_name} ({_mmss(meta['duration'])}, {meta['orientation']}). "
                "Not tagged — Claude is switched off.")
    a = analysis
    lines = [
        f"Studio: {a['title']}",
        f"{BRAND_LABELS.get(a['brand'], a['brand'])} · {FORMAT_LABELS.get(a['format'], a['format'])} · "
        f"{_mmss(meta['duration'])} {meta['orientation']}",
        a["summary"],
    ]
    if a["brand"] == "unsure":
        lines.append(f"Why unsure: {a['brand_reason']}")
    if a["moments"]:
        first = a["moments"][0]
        lines.append(f"{len(a['moments'])} usable moment(s), first at {_mmss(first['start'])}: "
                     f"{first['description']}")
    if not a["usable"]:
        lines.append(f"Flagged unusable: {a['quality_notes']}")
    return "\n".join(lines)
