"""Run Studio's edit-quality suites: cuts, endings, listen-back and watch-through, fix rounds, slideshow
Reels, voice-overs, picture inserts, the graphics look (Site Notes), Whisper's prompt, the wiki as a source of topics,
the reference study (beat timeline, structure, YouTube through Gemini; refused downloads, schema sizes, one-offs beside
the service, study folders), sound effects on the graphics, the profile grid (photos-only carousels, the grid flag, the
account's stills and profile), reference downloads (hand-added first, the library's hourly cap, the sign-in that needs
attention, Instagram's 400 as a refusal, uploaded files and OneDrive drops attached to their reference). One line per suite with its pass count; exits 1 if any check fails.

    python tests/quality/run.py                # from studio/, all suites, 4 at a time
    python tests/quality/run.py cuts ending    # just these
    python tests/quality/run.py --jobs 2 --keep

Each suite runs with the interpreter running this, in a folder of its own (removed after unless --keep
or a suite fails), with no real keys: GEMINI_API_KEY unset, ANTHROPIC_API_KEY empty, topics off,
STUDIO_ROOT a temporary folder, and studio/.env never read (common.py)."""
import argparse, os, re, shutil, signal, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[1]

# name: (script, extra args, the checks it makes). Longest first, so the slow ones start at once.
SUITES = {
    "finalcheck": ("suite_finalcheck.py", [], 59),
    "pip": ("suite_pip.py", [], 80),
    "selffix": ("suite_selffix.py", [], 67),
    "ending": ("suite_ending.py", [], 71),
    "patches": ("suite_patches.py", [], 59),
    "slideshow": ("suite_slideshow.py", [], 97),
    "pictures": ("suite_pictures.py", [], 67),
    "sfx": ("suite_sfx.py", [], 67),
    "look": ("suite_look.py", [], 55),
    "cuts": ("suite_cuts.py", [], 41),
    "edit_units": ("suite_edit_units.py", [], 83),
    "whisper_prompt": ("suite_whisper_prompt.py", [], 10),
    "refstudy": ("suite_refstudy.py", [], 70),
    "study_fixes": ("suite_study_fixes.py", [], 66),
    "wiki": ("suite_wiki.py", [], 136),
    "insights": ("suite_insights.py", [], 66),
    "grid": ("suite_grid.py", [], 54),
    "refqueue": ("suite_refqueue.py", [], 88),
}
TIMEOUT = 90 * 60  # a hang, not a slow box: finalcheck alone takes 15-45 min on 4 vCPU
RUNNING = []  # each suite runs in a process group of its own (with its ffmpeg and Chrome)


def env_for(work: Path) -> dict:
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    env.update(ANTHROPIC_API_KEY="", STUDIO_TOPICS_DAY="off", STUDIO_TOPICS_BEHIND="0", PYTHONUNBUFFERED="1",
               STUDIO_ROOT=str(work / "root"), STUDIO_INBOX=str(work / "root" / "Inbox"),
               STUDIO_QUALITY_WORK=str(work), TMPDIR=str(work / "tmp"))
    env["PYTHONPATH"] = os.pathsep.join([str(STUDIO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_one(name: str, base: Path) -> dict:
    script, args, want = SUITES[name]
    work = base / name
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    log = base / f"{name}.log"
    t0 = time.time()
    with open(log, "w") as out:
        p = subprocess.Popen([sys.executable, "-u", str(HERE / script), *args], cwd=str(work), env=env_for(work),
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        RUNNING.append(p)
        timer = threading.Timer(TIMEOUT, stop, (p,)); timer.start()
        _, status, ru = os.wait4(p.pid, 0)  # its CPU time too (with ffmpeg's, Chrome's): the box's load apart
        p.returncode = code = os.waitstatus_to_exitcode(status)
        if not timer.is_alive():
            code = "timeout"
        timer.cancel()
        stop(p)  # anything it left running
    cpu = ru.ru_utime + ru.ru_stime
    text = log.read_text(errors="replace")
    m = re.findall(r"^(\d+)/(\d+) passed$", text, re.M)
    passed, total = (int(m[-1][0]), int(m[-1][1])) if m else (0, 0)
    fails = [ln for ln in text.splitlines() if ln.startswith("FAIL ")]
    good = code == 0 and m and passed == total and total >= want
    return {"name": name, "ok": bool(good), "passed": passed, "total": total, "want": want, "code": code,
            "fails": fails, "secs": time.time() - t0, "cpu": cpu, "log": log, "tail": text.strip().splitlines()[-6:]}


def main():
    global TIMEOUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="suite names (default: all): " + ", ".join(SUITES))
    ap.add_argument("--jobs", type=int, default=4, help="suites at a time (default 4)")
    ap.add_argument("--timeout", type=int, default=TIMEOUT // 60, help=f"minutes a suite may take (default {TIMEOUT // 60})")
    ap.add_argument("--keep", action="store_true", help="keep each suite's folder and log")
    a = ap.parse_args()
    names = a.only or list(SUITES)
    TIMEOUT = a.timeout * 60
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        ap.error("no such suite: " + ", ".join(unknown))
    missing = [t for t in ("ffmpeg", "ffprobe", "node") if not shutil.which(t)]
    if missing:
        sys.exit("needs " + ", ".join(missing) + " on PATH")
    base = Path(tempfile.mkdtemp(prefix="studio-quality-"))
    print(f"Studio quality suites: {len(names)}, {a.jobs} at a time, in {base}", flush=True)
    t0 = time.time()
    res = []
    pool = ThreadPoolExecutor(max_workers=max(1, a.jobs))
    try:
        for f in as_completed([pool.submit(run_one, n, base) for n in names]):
            r = f.result(); res.append(r)
            mark = "ok  " if r["ok"] else "FAIL"
            why = "" if r["ok"] else (f"  exit {r['code']}" + (f", {r['total']} checks, expected {r['want']}" if r["total"] < r["want"] else ""))
            print(f"{mark} {r['name']:<15} {r['passed']}/{r['total']} passed  {r['secs']:5.0f}s (cpu {r['cpu']:.0f}s){why}",
                  flush=True)
            for ln in r["fails"][:10]:
                print("       " + ln[:200])
            if not r["ok"] and not r["fails"]:
                for ln in r["tail"]:
                    print("       | " + ln[:200])
    except KeyboardInterrupt:
        for p in RUNNING:
            stop(p)
        pool.shutdown(cancel_futures=True)
        sys.exit(f"stopped; logs in {base}")
    pool.shutdown()
    bad = [r for r in res if not r["ok"]]
    total = sum(r["passed"] for r in res), sum(r["total"] for r in res)
    print(f"{'all green' if not bad else str(len(bad)) + ' suite(s) failed'}: {total[0]}/{total[1]} checks, "
          f"{time.time() - t0:.0f}s (cpu {sum(r['cpu'] for r in res):.0f}s)", flush=True)
    if bad or a.keep:
        print(f"logs and folders kept in {base}")
    else:
        shutil.rmtree(base, ignore_errors=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
