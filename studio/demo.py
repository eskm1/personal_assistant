"""
Try the dashboard without real footage: builds a throwaway studio folder with
generated sample videos in every stage (inbox, ingesting, library, being edited,
ready for review, done), then serves the dashboard on it.

  python demo.py            build once (reused on later runs) and serve
  python demo.py --reset    rebuild from scratch

Your real studio folder and catalogue are never touched: the demo lives in
%TEMP%\\studio-demo (or $TMPDIR/studio-demo).
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

DEMO_ROOT = Path(tempfile.gettempdir()) / "studio-demo"
# Must be set before settings is imported anywhere.
os.environ["STUDIO_ROOT"] = str(DEMO_ROOT)
os.environ["STUDIO_INBOX"] = str(DEMO_ROOT / "Inbox")
os.environ.setdefault("STUDIO_DASHBOARD_HOST", "127.0.0.1")
os.environ["STUDIO_DASHBOARD_PASSWORD"] = ""

import catalog  # noqa: E402
import extract  # noqa: E402
import settings  # noqa: E402

# (source, hue) pairs so each sample looks different. All are ffmpeg built-ins.
PATTERNS = [("testsrc2", 0), ("mandelbrot", 0), ("gradients=speed=0.02", 0), ("cellauto", 120),
            ("life=mold=10:life_color=#e0a030:death_color=#202020", 0), ("smptehdbars", 0),
            ("testsrc2", 200), ("gradients=speed=0.03:c0=#1d3557:c1=#e63946", 0)]

CLIPS = [
    ("Kitchen backsplash tiling, day 2", "urban_makers", "talking_head",
     "Bryan explains why the wall is waterproofed before the first tile goes on.",
     "Walking the client through the kitchen tiling sequence on site.",
     "Watch what we do before a single tile goes up."),
    ("Bathroom waterproofing membrane", "urban_makers", "compilation",
     "Close-ups of the membrane being rolled on and the corners sealed.",
     "Pure process footage from a renovation site; company work.", ""),
    ("Morning site walk in Tampines", "urban_makers", "day_in_the_life",
     "A walk through three units, checking carpentry and electrical progress.",
     "Shows the company's projects and team at work.", "Three sites before 9 a.m."),
    ("Why I left my corporate job", "personal", "talking_head",
     "Bryan on leaving a stable job to build a renovation company.",
     "About his own decisions and life, not the company's work.",
     "I quit the safest job I ever had."),
    ("Saturday run and coffee", "personal", "day_in_the_life",
     "A weekend morning: run, coffee, planning the week.", "Personal routine, no work content.", ""),
    ("Carpentry workshop close-ups", "urban_makers", "compilation",
     "Saw cuts, edge banding and cabinet assembly in the workshop.", "Company craft footage.", ""),
]

PRODUCTIONS = [
    # title, brand, format, status, version, idea, notes [(version, at, body, status)]
    ("3 mistakes homeowners make with waterproofing", "urban_makers", "talking_head", "ready", 1,
     "Talking head from the bathroom site, cut to the membrane close-ups for each mistake.", []),
    ("Kitchen reveal: before and after", "urban_makers", "compilation", "ready", 2,
     "Before/after montage of the kitchen project, music-led.",
     [(1, 2.5, "Hold the 'before' shot longer so the reveal lands.", "addressed"),
      (1, 6.0, "Music is too loud under the last line.", "addressed")]),
    ("A day running a renovation company", "personal", "day_in_the_life", "ready", 1,
     "Site walk + workshop + evening reflection, told in first person.", []),
    ("Tiling in 30 seconds", "urban_makers", "compilation", "revising", 1,
     "Fast-cut tiling process with captions for each step.",
     [(1, 1.2, "Opening is too slow. Start on the first tile going down.", "open"),
      (1, 4.0, "Caption covers the trowel. Move it up.", "open")]),
    ("Why I started Urban Makers", "personal", "talking_head", "producing", 0,
     "From the corporate-job clip, tightened to 45 seconds with a stronger hook.", []),
    ("Carpentry close-ups montage", "urban_makers", "compilation", "approved", 2,
     "Workshop B-roll cut to an upbeat track.", [(1, None, "Swap the track for something calmer.", "addressed")]),
]


def _video(out: Path, pattern: str, hue: int, seconds: int = 8) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"hue=h={hue}" if hue else "null"
    extract._run([settings.FFMPEG, "-y", "-v", "error",
                  "-f", "lavfi", "-i", f"{pattern}{':' if '=' in pattern else '='}size=540x960:rate=30",
                  "-f", "lavfi", "-i", f"sine=frequency={220 + hue}:duration={seconds}",
                  "-t", str(seconds), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(out)])
    return out


def build() -> None:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    settings.ensure_folders()
    conn = catalog.connect()
    print(f"Building demo studio in {DEMO_ROOT} ...")

    clip_ids = []
    for n, (title, brand, fmt, summary, reason, hook) in enumerate(CLIPS):
        clip_dir = settings.LIBRARY / "2026-10" / f"2026-10-{10 + n:02d}_0900_IMG_{1200 + n}"
        video = _video(clip_dir / f"IMG_{1200 + n}.mp4", *PATTERNS[n % len(PATTERNS)])
        meta = extract.probe(video)
        extract.extract_frames(video, clip_dir / "frames", meta["duration"])
        clip_id = catalog.start(conn, f"demo-{n}", video.name, clip_dir, video)
        analysis = {"brand": brand, "brand_reason": reason, "format": fmt, "title": title, "summary": summary,
                    "topics": ["renovation", "singapore"] if brand == "urban_makers" else ["life"],
                    "setting": "", "usable": True, "quality_notes": "fine", "suggested_hook": hook,
                    "moments": [{"start": 1.0, "end": 3.5, "kind": "hook", "description": "Strongest opening line"},
                                {"start": 4.0, "end": 7.0, "kind": "visual", "description": "Clean process shot"}]}
        transcript = {"language": "en", "text": f"(Sample transcript for '{title}'.)"}
        catalog.save_tags(conn, clip_id, meta, transcript, analysis)
        clip_ids.append(clip_id)

    # One clip mid-ingest and one that failed, so those stages have something in them.
    for n, (status, error) in enumerate([("processing", None),
                                         ("failed", "APIConnectionError: Connection error.")]):
        clip_dir = settings.LIBRARY / "2026-10" / f"2026-10-15_1{n}00_IMG_23{n}0"
        video = _video(clip_dir / f"IMG_23{n}0.mp4", *PATTERNS[(n + 3) % len(PATTERNS)], seconds=4)
        extract.extract_frames(video, clip_dir / "frames", 4)
        clip_id = catalog.start(conn, f"demo-busy-{n}", video.name, clip_dir, video)
        if status == "failed":
            catalog.mark_failed(conn, clip_id, error)

    renders = settings.ROOT / "Renders"
    for n, (title, brand, fmt, status, version, idea, notes) in enumerate(PRODUCTIONS):
        video = thumb = None
        if version:
            video = _video(renders / f"production_{n + 1}_v{version}.mp4", *PATTERNS[(n + 2) % len(PATTERNS)])
            thumb = renders / f"production_{n + 1}_v{version}.jpg"
            extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", "2", "-i", str(video),
                          "-frames:v", "1", "-q:v", "4", str(thumb)])
        now = catalog.now()
        cur = conn.execute(
            """INSERT INTO productions (title, brand, format, status, version, idea, source_clips,
                   video_path, thumb_path, duration, caption, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, brand, fmt, status, version, idea, f"[{clip_ids[n % len(clip_ids)]}]",
             str(video) if video else None, str(thumb) if thumb else None, 8 if video else None,
             "" if status != "approved" else "Every cut measured twice. #carpentry #urbanmakers", now, now))
        for v, at, body, note_status in notes:
            conn.execute(
                """INSERT INTO feedback (target, target_id, version, at_seconds, body, status, created_at)
                   VALUES ('production', ?, ?, ?, ?, ?, ?)""", (cur.lastrowid, v, at, body, note_status, now))
    conn.commit()

    # Two ideas waiting on the Ideas tab, built from the sample clips.
    catalog.save_ideas(conn, "demo", [
        {"brand": "urban_makers", "format": "talking_head", "title": "Why we waterproof before we tile",
         "hook": "Watch what we do before a single tile goes up.",
         "angle": "Homeowners rarely see this step, and skipping it is the most expensive mistake in a bathroom.",
         "beats": ["Hook on the bare wall", "What waterproofing is", "Membrane close-ups", "Tiles going on",
                   "Payoff: a dry wall for years"],
         "segments": [{"clip_id": clip_ids[0], "start": 1.0, "end": 4.0, "purpose": "Hook line to camera"},
                      {"clip_id": clip_ids[1], "start": 0.5, "end": 3.5, "purpose": "Membrane being rolled on"},
                      {"clip_id": clip_ids[0], "start": 4.5, "end": 7.5, "purpose": "Explains why it matters"}],
         "caption": "The step nobody sees but everybody pays for if it's skipped. #waterproofing #renovation #urbanmakers",
         "est_seconds": 24},
        {"brand": "personal", "format": "day_in_the_life", "title": "Three sites before 9 a.m.",
         "hook": "Three sites before 9 a.m.",
         "angle": "A fast look at what running a renovation company actually feels like.",
         "beats": ["Early start", "Site walk", "Workshop", "Coffee and planning"],
         "segments": [{"clip_id": clip_ids[2], "start": 0.5, "end": 3.0, "purpose": "Opening walk"},
                      {"clip_id": clip_ids[5], "start": 1.0, "end": 3.0, "purpose": "Workshop close-up"},
                      {"clip_id": clip_ids[4], "start": 2.0, "end": 5.0, "purpose": "Coffee, planning the week"}],
         "caption": "Most mornings look like this. #dayinthelife #founder", "est_seconds": 18},
    ])

    # Inbox: one synced upload waiting, one still "uploading" (modified just now).
    _video(settings.INBOX / "IMG_2401.MOV", "testsrc2", 300, seconds=3)
    os.utime(settings.INBOX / "IMG_2401.MOV", (0, 0))
    _video(settings.INBOX / "IMG_2402.MOV", "mandelbrot", 60, seconds=3)
    print("Demo ready.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the dashboard on generated sample data.")
    parser.add_argument("--reset", action="store_true", help="rebuild the demo data")
    args = parser.parse_args()
    if args.reset or not (DEMO_ROOT / "catalog.db").exists():
        build()
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"Open http://127.0.0.1:{settings.DASHBOARD_PORT}  (Ctrl+C to stop)")
    import dashboard
    dashboard.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
