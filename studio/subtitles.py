"""
Subtitles are English or Simplified Chinese only. This fixes clips transcribed before
that rule: clips heard as another language (Singlish heard as Malay) are transcribed
again, Chinese ones are turned into Simplified characters, and drafts using them are
rendered again from their edit plan (new version, back in To review; no Claude cost).

Runs once by itself when Studio starts (worker.py); by hand: python subtitles.py
"""
import json
import logging
import sys
import tempfile
from pathlib import Path

import catalog
import extract
import settings
import transcribe

log = logging.getLogger("studio.subtitles")

DONE = settings.ROOT / "subtitles_fixed_v1"


def _rewrite(conn, clip: dict, transcript: dict) -> None:
    (Path(clip["clip_dir"]) / "transcript.json").write_text(
        json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")
    conn.execute("UPDATE clips SET language = ?, transcript = ? WHERE id = ?",
                 (transcript.get("language"), transcript.get("text"), clip["id"]))
    conn.commit()


def fix_clips(conn) -> list[int]:
    """Clips whose captions changed. Transcribing again takes a while per clip (CPU)."""
    allowed = settings.WHISPER_LANGUAGES
    changed = []
    for row in conn.execute("SELECT * FROM clips WHERE clip_dir IS NOT NULL").fetchall():
        clip = dict(row)
        path = Path(clip["clip_dir"]) / "transcript.json"
        if not path.exists():
            continue
        t = json.loads(path.read_text(encoding="utf-8"))
        if not t.get("words"):
            continue
        lang = t.get("language")
        if allowed and lang not in allowed and Path(clip["video_path"]).exists():
            log.info("Clip %s was transcribed as %r; transcribing again in %s", clip["id"], lang, "/".join(allowed))
            with tempfile.TemporaryDirectory(prefix="studio-subs-") as tmp:
                wav = extract.extract_audio(Path(clip["video_path"]), Path(tmp) / "a.wav")
                _rewrite(conn, clip, transcribe.transcribe(wav))
            changed.append(clip["id"])
        elif lang == "zh":
            fixed = json.loads(transcribe.simplified(json.dumps(t, ensure_ascii=False)))
            if fixed != t:
                log.info("Clip %s: Chinese captions now in Simplified characters", clip["id"])
                _rewrite(conn, clip, fixed)
                changed.append(clip["id"])
    return changed


def fix(conn) -> dict:
    """Fix the clips, then render again every finished draft that uses one."""
    import editor
    changed = set(fix_clips(conn))
    redone, failed = [], []
    for row in conn.execute("SELECT id, edl FROM productions WHERE edl IS NOT NULL AND status IN "
                            "('ready', 'approved')").fetchall():
        uses = {c["clip_id"] for c in json.loads(row["edl"]).get("cuts", [])}
        if not uses & changed:
            continue
        try:
            editor.rerender(conn, row["id"])
            redone.append(row["id"])
        except Exception as e:  # one draft's failure mustn't stop the others
            log.warning("Couldn't render production %s again: %s", row["id"], e)
            catalog.set_production_status(conn, row["id"], "ready")
            failed.append(row["id"])
    return {"clips": sorted(changed), "rerendered": redone, "failed": failed}


def fix_once(conn) -> None:
    """At startup: once per Studio (DONE marks it). Never raises."""
    if DONE.exists():
        return
    try:
        result = fix(conn)
        DONE.write_text(json.dumps(result), encoding="utf-8")
        if result["clips"]:
            log.info("Subtitles fixed: clips %s; drafts rendered again: %s", result["clips"], result["rerendered"])
    except Exception:
        log.exception("Fixing subtitle languages failed; tries again at the next start")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(fix(catalog.connect()), indent=2))
    sys.exit(0)
