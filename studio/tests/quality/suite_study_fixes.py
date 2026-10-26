"""The reference study's three defects from the server (2026-10-26, a reel Bryan wanted studied:
https://www.instagram.com/reel/De8SPjEAuV0/): Instagram refused yt-dlp ("HTTP Error 400"), Claude
refused the study's schema ("The compiled grammar is too large"), and `worker.py --style-study` wouldn't
run while the service ran; the failed study left an empty folder in Studio/Style.

- Downloads: a reel with no video fails with a plain reason and no Claude call (gallery-dl giving nothing,
  an empty file or a cut-short one); the cookies.txt sign-in reaches yt-dlp and gallery-dl when there is one
  (and not when there isn't); gallery-dl is tried after yt-dlp fails, a carousel's too after a 400 (not
  after a real "slow down"); a video gallery-dl does get is studied.
- Every structured-output schema Studio sends Claude (and the one Gemini gets) under a size budget, chosen
  from the measurements: the study's schema that failed is over it, every schema in use under it, and every
  module that asks for structured output is in the list.
- One-off modes run beside the service (its worker.lock held); two of the same kind can't run at once;
  the service's own work still needs it stopped.
- A failed study removes its own folder; on start, dated study folders with no file in them go, and a
  folder with any file stays.
yt-dlp and gallery-dl are fakes (no network); a real reel made at run time (ffmpeg); Whisper and Claude stubbed.
Run: python tests/quality/suite_study_fixes.py (from studio/)"""
import fcntl, json, os, re, shutil, subprocess, sys
from pathlib import Path
from types import SimpleNamespace as NS

from common import STUDIO, WORK as P, ok, finish, load_before

os.environ["ANTHROPIC_API_KEY"] = "test"
import settings, style_study, ref_youtube, transcribe
import yt_dlp

settings.ensure_folders()
URL = "https://www.instagram.com/reel/De8SPjEAuV0/"
YTDLP_400 = "ERROR: [Instagram] De8SPjEAuV0: Video info extraction failed: HTTP Error 400: Bad Request"
EVENTS = []  # ("yt-dlp", opts) / ("gallery-dl", argv), in the order they ran
MODE = {"ytdlp": YTDLP_400, "gdl": "refused"}


class FakeYDL:
    """yt-dlp's YoutubeDL: records its options, then refuses as Instagram did on the server."""

    def __init__(self, opts):
        self.opts = dict(opts)
        EVENTS.append(("yt-dlp", self.opts))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        raise Exception(MODE["ytdlp"])


yt_dlp.YoutubeDL = FakeYDL
_real_run = subprocess.run


def fake_run(cmd, *a, **kw):
    """gallery-dl answers as MODE["gdl"] says; every other command (ffmpeg, ffprobe) is real."""
    if not (isinstance(cmd, list) and "gallery_dl" in cmd):
        return _real_run(cmd, *a, **kw)
    EVENTS.append(("gallery-dl", list(cmd)))
    out = Path(cmd[cmd.index("-D") + 1]) if "-D" in cmd else None
    mode = MODE["gdl"]
    if mode == "refused" or out is None:
        return subprocess.CompletedProcess(cmd, 1, "", "[instagram][error] HttpError: '400 Bad Request' for "
                                                       "'https://www.instagram.com/graphql/query/'\n")
    out.mkdir(parents=True, exist_ok=True)
    if mode == "empty":
        (out / "instagram_De8SPjEAuV0.mp4").write_bytes(b"")
    elif mode == "partial":  # a download cut off half-way: a file, but not a video ffprobe can read
        (out / "instagram_De8SPjEAuV0.mp4").write_bytes(VIDEO.read_bytes()[:6000])
    else:
        shutil.copy(VIDEO, out / "instagram_De8SPjEAuV0.mp4")
        (out / "instagram_De8SPjEAuV0.mp4.json").write_text(json.dumps({"username": "someone", "likes": 120,
                                                                         "comments": 4}), encoding="utf-8")
    return subprocess.CompletedProcess(cmd, 0, "", "")


subprocess.run = fake_run


class Claude:
    """Counts every call; answers a reel read in the compact shape; the guide can be made to fail."""

    def __init__(self, guide_fails=False):
        self.calls, self.guide_fails = [], guide_fails

    @property
    def beta(self):
        return NS(messages=NS(create=self.create, stream=self.stream))

    def create(self, **kw):
        self.calls.append(kw)
        if "editor-in-chief" in kw.get("system", ""):
            if self.guide_fails:
                raise RuntimeError("the guide call failed")
            text = json.dumps({"content": "## House style (measured)", "report": "r", "changes": "c"})
        else:
            design = {"palette": [], "materials": [], "lighting": "", "camera": "", "framing": "", "shot_types": [],
                      "text_style": "", "closest_free_fonts": []}
            text = json.dumps({"layouts": [], "captions": {"present": False, "words_per_chunk": "", "case": "none",
                                                           "font": "", "accent_colours": [], "box": "", "position": ""},
                               "hook": "h", "first_frame": "f", "broll": [], "graphics": "none", "cta": "c",
                               "standout": "s", "design": design, "beats": ["a red wall | first beat"],
                               "arc": {"open": "result_first | 0.2 | seen | the room first",
                                       "hold": "fast_cuts | - | seen | quick", "turn": "none | - | inferred | no turn",
                                       "close": "just_stops | 5.9 | seen | stops"},
                               "gaps": [], "observations": ["0.2 | seen | the room first"]})
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=9000, output_tokens=1400, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def stream(self, **kw):
        msg = self.create(**kw)

        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()


VIDEO = P / "reel.mp4"
_real_run(["ffmpeg", "-y", "-v", "error", "-filter_complex",
           "color=c=red:s=540x960:d=2:r=25[a];color=c=blue:s=540x960:d=2:r=25[b];color=c=green:s=540x960:d=2:r=25[c];"
           "[a][b][c]concat=n=3:v=1:a=0[v];sine=f=220:d=6[s]", "-map", "[v]", "-map", "[s]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(VIDEO)], check=True)
transcribe.transcribe = lambda wav: {"language": "en", "text": "Three mistakes.", "words": [
    {"start": 0.3, "end": 0.5, "word": "Three"}, {"start": 0.6, "end": 0.9, "word": "mistakes."}],
    "segments": [{"start": 0.3, "end": 0.9, "text": "Three mistakes."}]}


def attempt(gdl_mode, cookies=True, url=URL, ytdlp=YTDLP_400):
    """analyse() on the link: (the result or the DownloadFailed, the downloaders in order, Claude's calls)."""
    EVENTS.clear()
    MODE.update(gdl=gdl_mode, ytdlp=ytdlp)
    if cookies:
        style_study.COOKIES_FILE.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tx\n")
    else:
        style_study.COOKIES_FILE.unlink(missing_ok=True)
    claude = Claude()
    work = P / f"dl-{gdl_mode}-{int(cookies)}"
    try:
        got = style_study.analyse(url, work / "stills", download_dir=work / "videos", client=claude, ref="fix:1")
    except style_study.DownloadFailed as e:
        got = e
    return got, list(EVENTS), claude.calls


# ── 1. Downloads: no video, no study; the sign-in to both; gallery-dl after yt-dlp ─────────────────────
cookie = str(style_study.COOKIES_FILE)
for mode, what in (("refused", "gallery-dl refused too"), ("empty", "gallery-dl gave an empty file"),
                   ("partial", "gallery-dl gave a cut-short file")):
    e, events, calls = attempt(mode)
    ok(isinstance(e, style_study.DownloadFailed) and str(e).startswith("Instagram refused the download: ")
       and "HTTP Error 400: Bad Request" in str(e) and "try again later or upload the file" in str(e),
       f"{what}: the reference fails with a plain reason ({str(e)[:150]})")
    ok(not calls, f"{what}: no Claude call is spent")
ok("[Instagram]" not in str(e) and "ERROR:" not in str(e), "the reason drops the downloader's prefixes")
e, events, _ = attempt("refused")
kinds = [k for k, _ in events]
ok(kinds == ["yt-dlp", "gallery-dl", "yt-dlp"], f"yt-dlp first, gallery-dl after it fails, then yt-dlp signed out ({kinds})")
ok(events[0][1].get("cookiefile") == cookie, "the cookies.txt sign-in goes to yt-dlp")
gdl = events[1][1]
ok("--cookies" in gdl and gdl[gdl.index("--cookies") + 1] == cookie, "and to gallery-dl")
ok("cookiefile" not in events[2][1], "the last try is yt-dlp without the sign-in (a public reel needs none)")
ok(e.problem == "rate", "Instagram's 400 is a wait-and-retry problem for the library (not failed for good)")
e, events, calls = attempt("refused", cookies=False)
gdl = [a for k, a in events if k == "gallery-dl"]
ok([k for k, _ in events] == ["yt-dlp", "gallery-dl"] and "cookiefile" not in events[0][1]
   and gdl and "--cookies" not in gdl[0], "with no cookies.txt: neither gets a sign-in, gallery-dl still tried")
ok(isinstance(e, style_study.DownloadFailed) and not calls, "and it fails plainly without a Claude call")
got, events, calls = attempt("video")
ok(isinstance(got, dict) and got["kind"] == "video" and got["measured"]["duration"] > 5 and len(calls) == 1,
   "a video gallery-dl gets is measured and read (one Claude call)")
ok(got["engagement"]["source"] == "gallery-dl" and got["engagement"]["likes"] == 120,
   "its metadata is gallery-dl's (likes, the account)")
ok(got["look"]["arc"]["open"]["pattern"] == "result_first" and got["look"]["beats"][0]["screen"] == "a red wall",
   "the compact answer reads into the kept shape")
ok(style_study.video_problem(VIDEO) is None and style_study.video_problem(P / "nothing.mp4") == "no file",
   "video_problem: a real reel is a video, a missing file isn't")
(P / "tiny.mp4").write_bytes(b"x" * 10)
ok(style_study.video_problem(P / "tiny.mp4") == "an empty file", "a few bytes are an empty file")

# A carousel link: gallery-dl after yt-dlp's 400, but not after a real "slow down" (429).
for err, tried, what in ((YTDLP_400, True, "after a 400"),
                         ("ERROR: [Instagram] X: HTTP Error 429: Too Many Requests", False, "not after a 429")):
    EVENTS.clear()
    MODE.update(gdl="refused", ytdlp=err)
    try:
        style_study.fetch_slides("https://www.instagram.com/p/De8SPjEAuV0/", P / "slides")
        failed = False
    except style_study.DownloadFailed:
        failed = True
    ok(failed and any(k == "gallery-dl" for k, _ in EVENTS) == tried, f"a carousel: gallery-dl {what}")


# ── 2. The schemas: every structured-output schema under a budget ───────────────────────────────────
# The budget comes from the measurements (2026-10-26, Studio's own measure below): the largest schemas
# Claude takes in daily use are Studio's read of a draft (draft_review.SCHEMA: 31 properties, 58 enum
# values, 6 unions, 2.2 KB) and the edit plan (editor, up to 41 properties and 2.3 KB). The study's schema
# Claude refused had 53 properties, 71 enum values, 10 objects and 3.8 KB. The weight sums what makes a
# grammar big: every property, every enum value, a union (anyOf, a type list) four times (each one doubles
# a part of the grammar) and an object twice. Every property is required (an optional one doubles too).
BUDGET = {"json": 2800, "props": 45, "enums": 64, "unions": 8, "optional": 0, "depth": 5, "weight": 130}


def measure(schema: dict) -> dict:
    m = {"props": 0, "enums": 0, "unions": 0, "optional": 0, "depth": 0, "objects": 0}

    def walk(s, depth):
        if not isinstance(s, dict):
            return
        m["depth"] = max(m["depth"], depth)
        m["enums"] += len(s.get("enum") or [])
        if "anyOf" in s or isinstance(s.get("type"), list):
            m["unions"] += 1
        for alt in s.get("anyOf") or []:
            walk(alt, depth)
        if s.get("type") == "object":
            props = s.get("properties") or {}
            m["objects"] += 1
            m["props"] += len(props)
            m["optional"] += len(set(props) - set(s.get("required") or []))
            for v in props.values():
                walk(v, depth + 1)
        if s.get("type") == "array":
            walk(s.get("items"), depth + 1)

    walk(schema, 0)
    m["json"] = len(json.dumps(schema, separators=(",", ":")))
    m["weight"] = m["props"] + m["enums"] + 4 * m["unions"] + 2 * m["objects"]
    return m


def over(m: dict) -> list[str]:
    return [f"{k} {m[k]} > {v}" for k, v in BUDGET.items() if m[k] > v]


import carousel, classify, concepts, content_plan, draft_review, editor, footage_ideas, help_desk, ideas, music
import plan, slideshow, spelling, taste, topics, voiceover, wording
SCHEMAS = {  # module: {label: schema} (the editor's plan in its largest form: pictures and a voice-over on)
    "style_study": {"LOOK_SCHEMA (a reel)": style_study.LOOK_SCHEMA, "SLIDES_SCHEMA (a carousel)": style_study.SLIDES_SCHEMA,
                    "GUIDE_SCHEMA (draft_guide)": style_study.GUIDE_SCHEMA},
    "ref_youtube": {"_schema (Gemini's responseSchema)": ref_youtube._schema()},
    "taste": {"MAP_SCHEMA": taste.MAP_SCHEMA, "REPORT_SCHEMA": taste.REPORT_SCHEMA},
    "draft_review": {"SCHEMA (a Reel)": draft_review.SCHEMA, "CAROUSEL_SCHEMA": draft_review.CAROUSEL_SCHEMA},
    "editor": {"the plan": editor._schema(False), "the plan + pictures + voice-over": voiceover.schema(editor._schema(True)),
               "_JOIN_SCHEMA": editor._JOIN_SCHEMA},
    "carousel": {"REVISE_SCHEMA": carousel.REVISE_SCHEMA},
    "topics": {"_schema": topics._schema()},
    "footage_ideas": {"_schema": footage_ideas._schema()},
    "classify": {"SCHEMA": classify.SCHEMA},
    "content_plan": {"_SCHEMA": content_plan._SCHEMA},
    "help_desk": {"SCHEMA": help_desk.SCHEMA},
    "music": {"TRACK_SCHEMA": music.TRACK_SCHEMA, "SHORTLIST_SCHEMA": music.SHORTLIST_SCHEMA},
    "spelling": {"_SCHEMA": spelling._SCHEMA},
    "plan": {"ORGANISE_SCHEMA": plan.ORGANISE_SCHEMA, "DEVELOP_SCHEMA": plan.DEVELOP_SCHEMA, "PLACE_SCHEMA": plan.PLACE_SCHEMA},
    "ideas": {"SCHEMA": ideas.SCHEMA},
    "wording": {"_REWRITE_SCHEMA": wording._REWRITE_SCHEMA},
    "slideshow": {"CHANGE_SCHEMA": slideshow.CHANGE_SCHEMA},
    "concepts": {"BRIEF_SCHEMA": concepts.BRIEF_SCHEMA, "CHECK_SCHEMA": concepts.CHECK_SCHEMA},
}
old = load_before("style_study_31ebb7f.py", "style_study_31ebb7f")
failing = measure(old.LOOK_SCHEMA)
ok(over(failing) and failing["weight"] > BUDGET["weight"],
   f"the study's schema Claude refused on 2026-10-26 is over the budget ({', '.join(over(failing))})")
print(f"     {'schema':52}" + "".join(f"{k:>8}" for k in BUDGET))
print(f"     {'style_study.LOOK_SCHEMA at 31ebb7f (refused)':52}" + "".join(f"{failing[k]:>8}" for k in BUDGET))
for module, schemas in SCHEMAS.items():
    for label, schema in schemas.items():
        m = measure(schema)
        print(f"     {module + '.' + label:52}" + "".join(f"{m[k]:>8}" for k in BUDGET))
        ok(not over(m), f"{module}.{label} is under the budget" + (f" ({', '.join(over(m))})" if over(m) else ""))
asks = sorted(p.stem for p in STUDIO.glob("*.py") if '"json_schema"' in p.read_text(encoding="utf-8", errors="replace"))
ok(set(asks) <= set(SCHEMAS), f"every module asking for structured output is measured (missing: {set(asks) - set(SCHEMAS)})")
look = measure(style_study.LOOK_SCHEMA)
ok(look["enums"] <= 12 and look["unions"] == 0 and look["depth"] <= 3,
   f"the study's schema: the structure's patterns are checked in code, not enums ({look})")
ok(style_study.read_parts({"arc": {"turn": {"pattern": "reveal", "at": 2.0, "basis": "seen", "what": "w"}}})["arc"]["turn"]
   ["pattern"] == "reveal", "a read in the old object shape still reads")


# ── 3. One-off modes beside the service; one of each kind at a time ────────────────────────────────
def hold(name):
    f = open(settings.ROOT / name, "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def worker(*argv):
    """worker.py in its own process (studio/.env never read), as the server's command line runs it."""
    code = ("import sys, runpy, dotenv; dotenv.load_dotenv = lambda *a, **k: False; "
            f"sys.argv = ['worker.py', *{list(argv)!r}]; runpy.run_path('worker.py', run_name='__main__')")
    r = _real_run([sys.executable, "-c", code], cwd=str(STUDIO), capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


links = P / "empty-links.txt"
links.write_text("Inspiration:\n", encoding="utf-8")
service = hold("worker.lock")  # the service is running
code, out = worker("--style-study", str(links))
ok("Another studio worker" not in out and "The study stopped: no links or videos found" in out and code == 1,
   f"--style-study runs while the service holds its lock ({out.strip()[-120:]})")
code, out = worker("--style-apply")
ok("No finished style study yet" in out and "Another studio worker" not in out, "--style-apply too")
mine = hold("style-study.lock")  # a study already running
code, out = worker("--style-study", str(links))
ok(code == 1 and "Another copy of a style study is running" in out and "no links" not in out,
   "a second study waits for the first (a lock of its own kind)")
code, out = worker("--style-apply")
ok("No finished style study yet" in out, "a one-off of another kind still runs beside it")
mine.close()
code, out = worker("--edit", "1")
ok(code == 1 and "Stop it first" in out, "the service's own work (--edit) still needs it stopped, and says so")
code, out = worker("--once")
ok(code == 0 and "Another studio worker is already running" in out, "a second loop still exits quietly")
service.close()
import worker as worker_mod


def mode(name, value):
    args = {k: None for k in (*worker_mod.SERVICE_WORK, *worker_mod.ALONGSIDE)}
    args[name] = value
    return worker_mod.mode_of(worker_mod.argparse.Namespace(**args))


ok([mode("style_study", Path("x")), mode("resolve", 3), mode("edit", 0), mode("once", True), mode("once", False)]
   == ["style_study", "resolve", "edit", "once", "loop"],
   "mode_of names each mode (an id of 0 too)")


# ── 4. Study folders: a failed study cleans up; empty dated folders go on start ────────────────────
def dated():
    return sorted(p.name for p in style_study.STUDIES.glob("*") if re.match(r"\d{4}-\d{2}-\d{2}_\d{4}", p.name))


MODE.update(gdl="refused", ytdlp=YTDLP_400)
reel_links = P / "reel-links.txt"
reel_links.write_text(f"Inspiration:\n{URL}\n", encoding="utf-8")
claude = Claude()
before = dated()
try:
    style_study.run(reel_links, client=claude, say=lambda *a: None)
    stopped = ""
except RuntimeError as e:
    stopped = str(e)
ok("none of the reels could be measured" in stopped and dated() == before and not claude.calls,
   "a study whose only reel Instagram refused stops, leaves no folder and spends no Claude call")
folder = P / "videos-in"
folder.mkdir()
shutil.copy(VIDEO, folder / "ours-kitchen.mp4")
try:
    style_study.run(folder, client=Claude(guide_fails=True), say=lambda *a: None)
except RuntimeError:
    pass
kept = [n for n in dated() if n not in before]
ok(len(kept) == 1 and (style_study.STUDIES / kept[0] / "reels.json").is_file(),
   "a study that measured a reel keeps its folder when the guide fails (what was paid for stays)")
S = style_study.STUDIES
(S / "2026-10-26_2238").mkdir()
(S / "2026-10-25_1000" / "videos" / "gdl-video").mkdir(parents=True)
(S / "2026-10-24_0900").mkdir()
(S / "2026-10-24_0900" / "reels.json").write_text("[]")
(S / "2026-10-24_0901" / "stills" / "01").mkdir(parents=True)
(S / "2026-10-24_0901" / "stills" / "01" / "0.15.jpg").write_bytes(b"jpg")
(S / "notes").mkdir()
gone = sorted(p.name for p in style_study.tidy_studies())
ok(gone == ["2026-10-25_1000", "2026-10-26_2238"], f"empty dated folders (and ones holding only empty folders) go ({gone})")
ok((S / "2026-10-24_0900" / "reels.json").is_file() and (S / "2026-10-24_0901" / "stills" / "01" / "0.15.jpg").is_file()
   and (S / kept[0]).is_dir(), "a folder with any file in it, at any depth, stays")
ok((S / "notes").is_dir(), "a folder not named like a study is never touched")
(S / "2026-10-26_2238").mkdir()
try:
    style_study.run(reel_links, client=Claude(), say=lambda *a: None)
except RuntimeError:
    pass
ok(not (S / "2026-10-26_2238").exists(), "a study's start removes the empty folder a failed one left")

finish()
