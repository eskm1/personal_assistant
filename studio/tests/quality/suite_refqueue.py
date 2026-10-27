"""How Studio downloads Instagram references without Instagram turning the server away (2026-10-27, from the
live server: Bryan's three newest hand-added references failed, "HttpError 400" on /api/v1/media/<id>/info/ and
yt-dlp's "Video info extraction failed: HTTP Error 400", while the library ran hundreds of downloads from one
server with one spare sign-in).

- The queue: hand-added references go first and are tried once when added, even while the library is paused,
  then wait for the backoff and the pause like the rest (no tight loop); the library at most
  STUDIO_REFERENCE_LIBRARY_PER_HOUR an hour, jittered; nothing of the library touches Instagram during a pause.
- The sign-in: a rolling record of Instagram's answers (ig_health); every signed-in call failing for 2+ hours,
  or a checkpoint / challenge / login page, marks it "needs attention" (the page says so, the library pauses
  until a fresh cookies.txt); a signed-in success clears it, a signed-out one doesn't; gone / no internet don't count.
- A 400 on the post's info (gallery-dl, with or without "Bad Request") and yt-dlp's "Video info extraction failed:
  HTTP Error 400" are "rate" in both the reel and the carousel path, with the plain "Instagram is refusing the
  server right now" words (the sign-in's words once it needs attention).
- Upload the file instead: the planner's upload pieces with reference_id, a set made the reference's own files,
  analysed through the same measure / look path (a video, or slides in order), its link kept; a mixed set refused.
- OneDrive: a file or folder named with a link reference's post code attaches to it (821 /p/DecIHe2ADYk,
  822 /reels/DeKIm8tsyWj, 824 /p/Dey86aEI9tj's ten slides); a folder of pictures is one carousel, taken only
  once it is quiet; an earlier duplicate is merged and retired, once; loose pictures stay where they are.
- No cookie value in a log, the record or a reason.
yt-dlp, gallery-dl, Supabase and Claude are fakes; a real reel and real pictures made at run time.
Run: python tests/quality/suite_refqueue.py (from studio/)"""
import io, json, logging, os, random, shutil, subprocess, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish

os.environ["ANTHROPIC_API_KEY"] = "test"
from PIL import Image
import settings, catalog, style_study, ref_library, ref_files, ig_health, cloud_sync, transcribe, onedrive
import yt_dlp

settings.ensure_folders()
settings.STABLE_SECONDS = 0
conn = catalog.connect()
SECRET = "SESSIONVALUE9f3k2"  # a cookie value that must never show up anywhere
LOG = io.StringIO()
_h = logging.StreamHandler(LOG)
_h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_h)
logging.getLogger().setLevel(logging.DEBUG)


def cookies(at: float | None = None):
    style_study.COOKIES_FILE.write_text("# Netscape HTTP Cookie File\n"
                                        f".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t{SECRET}\n")
    if at:
        os.utime(style_study.COOKIES_FILE, (at, at))


def reset_pacing():
    cloud_sync._save_state({ref_library.STATE_KEY: {}})
    ig_health._file().unlink(missing_ok=True)


# ── Fakes: Supabase, yt-dlp, gallery-dl, Claude ───────────────────────────────

class FakeAPI:
    """Enough of cloud_sync.Client for the references: eq / in / is.null filters, insert, update, upload."""

    def __init__(self):
        self.t = {"reference_videos": [], "reference_analysis": []}
        self.updates, self.inserts, self.uploads = [], [], {}

    @staticmethod
    def _match(row, params):
        for k, v in (params or {}).items():
            if k in ("select", "order", "limit"):
                continue
            op, _, val = str(v).partition(".")
            cur = row.get(k)
            if op == "eq" and str(cur) != val:
                return False
            if op == "in" and str(cur) not in val.strip("()").split(","):
                return False
            if op == "is" and val == "null" and cur is not None:
                return False
        return True

    def select(self, table, params=None):
        return [dict(r) for r in self.t.setdefault(table, []) if self._match(r, params)]

    def update(self, table, params, values):
        out = []
        for r in self.t.setdefault(table, []):
            if self._match(r, params):
                r.update(values)
                out.append(dict(r))
        self.updates.append((table, dict(params), dict(values)))
        return out

    def insert(self, table, row, **kw):
        rows = self.t.setdefault(table, [])
        new = {"id": max([r.get("id", 0) for r in rows] + [900]) + 1, "status": "active", "why": "", "url": None,
               "import_id": None, "brand": "urban_makers", **row}
        rows.append(new)
        self.inserts.append((table, dict(row)))
        if table == "reference_videos":
            self.t["reference_analysis"].append({"reference_id": new["id"], "state": "pending", "updated_at": None})
        return [dict(new)]

    def upload(self, path, data):
        self.uploads[path] = len(data)

    def request(self, *a, **k):
        return []

    def ref(self, rid):
        return next(r for r in self.t["reference_videos"] if r["id"] == rid)

    def analysis(self, rid):
        return next(a for a in self.t["reference_analysis"] if a["reference_id"] == rid)

    def add(self, rid, url=None, state="pending", library=False, why="", **extra):
        self.t["reference_videos"].append({"id": rid, "url": url, "status": "active", "brand": "urban_makers",
                                           "side": "inspiration", "why": why, "title": "", "import_id": 1 if library else None,
                                           "file_name": extra.pop("file_name", None), **extra})
        self.t["reference_analysis"].append({"reference_id": rid, "state": state, "updated_at": None, "error": None,
                                             "problem": extra.get("problem")})

    def maps(self):
        return self.select("reference_videos"), {a["reference_id"]: a for a in self.select("reference_analysis")}


cloud_sync._library_online = True
MODE = {"ytdlp": "ok", "gdl": "ok"}
EVENTS = []
YTDLP_400 = "ERROR: [Instagram] DeKIm8tsyWj: Video info extraction failed: HTTP Error 400: Bad Request"
GDL_400 = "[instagram][error] HttpError: '400 ' for 'https://www.instagram.com/api/v1/media/3721094418/info/'"


class FakeYDL:
    def __init__(self, opts):
        self.opts = dict(opts)
        EVENTS.append(("yt-dlp", "cookiefile" in self.opts))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        mode = MODE["ytdlp"]
        if mode != "ok":
            raise Exception(mode)
        return {"like_count": 321, "comment_count": 12, "view_count": 9000, "channel": "markynv",
                "description": "Three details #reno", "id": "x"}


yt_dlp.YoutubeDL = FakeYDL
_real_run = subprocess.run


def fake_run(cmd, *a, **kw):
    if not (isinstance(cmd, list) and "gallery_dl" in cmd):
        return _real_run(cmd, *a, **kw)
    EVENTS.append(("gallery-dl", "--cookies" in cmd))
    mode = MODE["gdl"]
    return subprocess.CompletedProcess(cmd, 1, "", (mode if mode != "ok" else GDL_400) + "\n")


subprocess.run = fake_run


class Claude:
    def __init__(self):
        self.calls = []

    @property
    def beta(self):
        return NS(messages=NS(create=self.create, stream=self.stream))

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("system") == style_study._SLIDES_SYSTEM:
            text = json.dumps({"layout": "photo with a line", "cover_hook": "3 details", "slides": []})
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
transcribe.transcribe = lambda wav: {"language": "en", "text": "Three details.", "words": [
    {"start": 0.3, "end": 0.5, "word": "Three"}, {"start": 0.6, "end": 0.9, "word": "details."}],
    "segments": [{"start": 0.3, "end": 0.9, "text": "Three details."}]}


def pictures(folder: Path, names: list[str], size=(1320, 1649)) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for k, n in enumerate(names):
        Image.new("RGB", size, (30 + 20 * k % 200, 90, 140)).save(folder / n, "JPEG", quality=80)
        out.append(folder / n)
    return out


# ── 1. The queue: hand-added first, the library slow and steady ──────────────────────────────────────
reset_pacing()
cookies()
api = FakeAPI()
api.add(1, "https://www.instagram.com/reel/LIBREEL0001/", library=True)
api.add(2, "https://www.instagram.com/reel/LIBREEL0002/", library=True)
api.add(10, "https://www.instagram.com/reel/HANDADDED01/")
api.add(11, "https://www.instagram.com/p/HANDADDED02/")
api.add(12, "https://www.youtube.com/shorts/abcdefghijk")
refs, analyses = api.maps()
q = [r["id"] for r in ref_library.hand_queue(refs, analyses)]
ok(q and q[0] == 10 and 1 not in q and 2 not in q, f"hand-added references come first, the library isn't in their queue ({q})")
ok(10 in q and 11 not in q and 12 in q, "one Instagram download a tick; a YouTube one (no download) beside it")
ok(ref_library.next_up(refs, analyses)["id"] == 1, "the library's own queue still holds its posts")

# A whole sync tick (cloud_sync.sync_references): the hand-added one before the library's, one each.
order = []
_one = cloud_sync.analyse_one


def ok_one(api_, ref, analysis, client=None, library=False):
    order.append(ref["id"])
    api_.update("reference_analysis", {"reference_id": f"eq.{ref['id']}"}, {"state": "done", "analyser": style_study.ANALYSER})
    return f"reference #{ref['id']}: analysed", "ok"


cloud_sync.analyse_one = ok_one
ref_library.followers_for = lambda api_, ref: None
reset_pacing()
api = FakeAPI()
api.add(1, "https://www.instagram.com/reel/LIBREEL0001/", library=True)
api.add(2, "https://www.instagram.com/reel/LIBREEL0002/", library=True)
api.add(10, "https://www.instagram.com/reel/HANDADDED01/")
cloud_sync.sync_references(api, time.time(), conn=conn)
ok(order == [10], f"a sync tick: the hand-added reference first; the library waits a minute or so after it ({order})")
st = ref_library._state()
st["next_at"] = time.time() - 1  # the minute or so has passed
ref_library._save(st)
cloud_sync.sync_references(api, time.time(), conn=conn)
cloud_sync.sync_references(api, time.time(), conn=conn)
ok(order == [10, 1], f"then one library post, and the next tick none (its slot is ~15 min away) ({order})")
cloud_sync.analyse_one = _one

# The library's cap: a simulated six hours of 30 s ticks with every download working.
reset_pacing()
t0 = time.time()
starts = []
for k in range(6 * 120):
    now = t0 + 30 * k
    go, _ = ref_library.ready(conn, now=now)
    if go:
        starts.append(now)
        ref_library.record("ok", now=now, library=True)
per_hour = settings.REFERENCE_LIBRARY_PER_HOUR
worst = max(sum(1 for s in starts if a <= s < a + 3600) for a in starts)
gaps = [b - a for a, b in zip(starts, starts[1:])]
ok(per_hour == 4, "STUDIO_REFERENCE_LIBRARY_PER_HOUR defaults to 4")
ok(worst <= per_hour, f"never more than {per_hour:g} library downloads in any hour ({worst} at most)")
ok(18 <= len(starts) <= 30, f"a steady few an hour over six hours ({len(starts)})")
mid = sorted(gaps)[len(gaps) // 2]
ok(len({round(g / 30) for g in gaps}) >= 4 and min(gaps) >= 0.6 * 900 - 30 and 0.75 * 900 <= mid <= 1.3 * 900,
   f"spaced with jitter around 15 minutes ({min(gaps) / 60:.0f}-{max(gaps) / 60:.0f} min, typically {mid / 60:.0f})")
ok(ref_library.estimates(conn)["seconds_per_post"] == 900 and ref_library.estimates(conn)["per_hour"] == 4,
   "the planner's estimate uses the cap (900 s a post)")

# Nothing of the library touches Instagram during a pause.
reset_pacing()
st = ref_library._state()
ref_library._pause(st, "rate", "Instagram is limiting downloads", until=time.time() + 3600)
ref_library._save(st)
tried = []
_real_one = cloud_sync.analyse_one


def counting_one(api_, ref, analysis, client=None, library=False):
    tried.append((ref["id"], library))
    api_.update("reference_analysis", {"reference_id": f"eq.{ref['id']}"},
                {"state": "failed", "problem": "rate", "error": style_study.refused_text()})
    return f"reference #{ref['id']}: failed", "rate"


cloud_sync.analyse_one = counting_one
followers = []
ref_library.followers_for = lambda api_, ref: followers.append(ref["id"])
api = FakeAPI()
api.add(1, "https://www.instagram.com/reel/LIBREEL0001/", library=True)
api.add(10, "https://www.instagram.com/reel/HANDADDED01/")
for _ in range(5):
    refs, analyses = api.maps()
    ref_library.tick(api, refs, analyses, conn)
ok(not tried and not followers, "while paused, five library ticks ask Instagram nothing")
for _ in range(5):  # five sync ticks: the hand-added one, once
    refs, analyses = api.maps()
    for r in ref_library.hand_queue(refs, analyses):
        ref_library.hand_one(api, r, analyses[r["id"]])
ok(tried == [(10, False)], f"a hand-added reference is tried once when added, pause or not ({tried})")
ok(api.analysis(10)["state"] == "failed" and api.analysis(10)["problem"] == "rate",
   "refused: it shows as failed (so its card offers the upload)")
st = ref_library._state()
st["paused"]["until"] = time.time() - 1
ref_library._save(st)
refs, analyses = api.maps()
ok([r["id"] for r in ref_library.hand_queue(refs, analyses)] == [10],
   "once the pause is over it is tried again, before the library")
ok(ref_library._state()["paused"] is None, "and the pause is cleared")
reset_pacing()
ref_library.record("rate", library=False)
st = ref_library._state()
ok(st["backoff_at"] - time.time() >= 0.85 * ref_library.BACKOFF_FIRST and not ref_library.instagram_free(),
   "after a refusal nothing is tried for 10 minutes or more (no tight loop)")
refs, analyses = api.maps()
ok(10 not in [r["id"] for r in ref_library.hand_queue(refs, analyses)], "so the refused one waits")
for _ in range(3):
    ref_library.record("rate", library=False)
st = ref_library._state()
ok(st["paused"] and st["paused"]["kind"] == "rate", "four refusals in a row (hand-added count too) pause it all")
cloud_sync.analyse_one = _real_one

# ── 2. The sign-in: a temporary limit or a dead sign-in ────────────────────────────────────────────
reset_pacing()
T = time.time() - 10 * 3600
cookies(at=T)
for k in range(8):  # eight signed-in 400s in 70 minutes: a limit, not yet the sign-in
    ig_health.record("gallery-dl", True, "rate", GDL_400, now=T + 600 * k)
ok(ig_health.attention() is None, "eight failures in 70 minutes: not the sign-in yet")
ig_health.record("yt-dlp", False, "ok", now=T + 6000)
for k in range(8):
    ig_health.record("gallery-dl", True, "rate", GDL_400, now=T + 7200 + 1200 * k)
a = ig_health.attention()
ok(a and a["why"] == "failing", "every signed-in call failing for 2+ hours: the sign-in needs attention")
ig_health.record("yt-dlp", False, "ok", now=T + 7200 + 1200 * 9)
ok(ig_health.attention() is not None, "a signed-out success clears nothing about the sign-in")
ig_health.record("yt-dlp", True, "ok", now=T + 7200 + 1200 * 10)
ok(ig_health.attention() is None, "a signed-in success clears it")
lines = json.loads(ig_health._file().read_text())["outcomes"]
ok(lines[-12]["endpoint"] == "media_info" and lines[-12]["status"] == 400 and lines[-12]["signed_in"] is True
   and set(lines[-1]) >= {"at", "tool", "signed_in", "ok", "status", "endpoint"},
   "each line keeps the time, tool, endpoint (the post's media info), status and sign-in")
ok(len(lines) <= ig_health.OUTCOMES_KEPT, f"a small rolling record ({len(lines)} kept)")
for msg in ("[instagram][error] HTTP redirect to challenge page (https://www.instagram.com/challenge/)",
            "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)",
            '{"message":"checkpoint_required","status":"fail"}', '{"message":"login_required"}'):
    reset_pacing()
    cookies(at=time.time() - 60)
    ig_health.record("gallery-dl", True, style_study.problem_of(msg), msg)
    ok(ig_health.attention() and ig_health.attention()["why"] == "challenge", f"one signed-in answer says it: {msg[:48]}")
reset_pacing()
cookies(at=time.time() - 60)
ig_health.record("gallery-dl", False, "login", "[instagram][error] HTTP redirect to login page (/accounts/login/)")
ok(ig_health.attention() is None, "a signed-out call sent to the login page says nothing about the sign-in")
for k in range(10):
    ig_health.record("yt-dlp", True, "gone" if k % 2 else "net", "HTTP Error 404: Not Found", now=time.time() - 9000 + 900 * k)
ok(ig_health.attention() is None, "deleted posts and no internet don't count against the sign-in")
reset_pacing()
cookies(at=time.time() - 3600)
ig_health.record("gallery-dl", True, "login", "HTTP redirect to challenge page (/challenge/)")
ok(ig_health.attention(), "flagged")
st = ref_library.record("rate")
ok(st["paused"] and st["paused"]["kind"] == "login" and "needs attention" in st["paused"]["reason"],
   "the library pauses with the sign-in's words, not 'try again later'")
info = ref_library.info(conn)
ok(info["signin"]["attention"]["text"] == ("The sign-in account needs attention: open Instagram with it on a phone, "
                                           "clear any check, then upload a fresh cookies.txt here."),
   "the References page gets the plain sentence (laptop_status.info.references.signin.attention)")
ok(style_study.refused_text().startswith("The sign-in account needs attention"), "a refused download says it too")
cookies(at=time.time() + 1)  # a fresh cookies.txt (a moment after the pause: the clock's grain)
ok(ig_health.attention() is None and ref_library.ready(conn)[0], "a fresh cookies.txt clears it and resumes the library")

# ── 3. The 400: rate in both paths, in plain words ────────────────────────────────────────────────
reset_pacing()
cookies()
for msg, what in ((GDL_400, "gallery-dl's 400 on /api/v1/media/<id>/info/ (no reason text)"),
                  ("[instagram][error] HttpError: '400 Bad Request' for 'https://www.instagram.com/api/v1/media/1/info/'",
                   "gallery-dl's 400 Bad Request"), (YTDLP_400, "yt-dlp's video info 400"),
                  ("ERROR: [Instagram] X: Video info extraction failed: HTTP Error 400", "yt-dlp's 400 without its reason"),
                  ("HTTP Error 429: Too Many Requests", "a 429")):
    ok(style_study.problem_of(msg) == "rate", f"{what}: rate")
    ok(style_study.download_problem("https://www.instagram.com/p/x/", msg, "cookies.txt").startswith(
        "Instagram is refusing the server right now"), f"{what}: the plain words, not 'Download failed'")
ok(style_study.problem_of("HTTP Error 404: Not Found") == "gone" and style_study.problem_of(
    "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)") == "login",
   "a 404 is still gone, a login page still login")
MODE.update(ytdlp=YTDLP_400, gdl=GDL_400)
for url, what in (("https://www.instagram.com/p/DecIHe2ADYk/", "a /p/ post (the carousel path)"),
                  ("https://www.instagram.com/reels/DeKIm8tsyWj/", "a /reels/ link (the reel path)")):
    EVENTS.clear()
    try:
        style_study.analyse(url, P / "st" / "stills", download_dir=P / "st" / "dl", client=Claude(), ref="t:1")
        e = None
    except style_study.DownloadFailed as err:
        e = err
    ok(e is not None and e.problem == "rate" and str(e).startswith("Instagram is refusing the server right now")
       and "Download failed" not in str(e) and "HttpError" not in str(e), f"{what}: rate, plainly ({str(e)[:70]})")
    ok(any(k == "gallery-dl" for k, _ in EVENTS), f"{what}: gallery-dl still tried after yt-dlp's 400")
api = FakeAPI()
api.add(821, "https://www.instagram.com/p/DecIHe2ADYk/", why="the cover's type")
refs, analyses = api.maps()
line, outcome = cloud_sync.analyse_one(api, api.ref(821), analyses[821], Claude())
a = api.analysis(821)
ok(outcome == "rate" and a["state"] == "failed" and a["problem"] == "rate"
   and a["error"].startswith("Instagram is refusing the server right now"), "the card's reason is the plain one")

# ── 4. Upload the file instead ──────────────────────────────────────────────────────────────────────
reset_pacing()
st = ref_library._state()
ref_library._pause(st, "rate", "paused", until=time.time() + 3600)
ref_library._save(st)
EVENTS.clear()
work = P / "up"
shutil.copy(VIDEO, P / "mine.mp4")
ref_files.attach(821, [P / "mine.mp4"], "upload", by="bryan@x.com")
ref_files.pending(api, 821)
ok(api.analysis(821)["state"] == "pending" and api.analysis(821)["problem"] is None and api.analysis(821)["error"] is None,
   "the failed state clears once the file is there")
refs, analyses = api.maps()
claude = Claude()
ok([r["id"] for r in ref_library.hand_queue(refs, analyses)] == [821], "it is analysed next, pause or not (no download)")
line, outcome = cloud_sync.analyse_one(api, api.ref(821), analyses[821], claude)
a = api.analysis(821)
ok(outcome == "ok" and a["state"] == "done" and a["kind"] == "video" and a["measured"]["duration"] > 5
   and a["measured"]["cuts"] >= 2 and len(claude.calls) == 1, "the uploaded video is measured and read like a download")
ok(not EVENTS, "nothing asked of Instagram while it's paused (no numbers)")
ok(api.ref(821)["url"] == "https://www.instagram.com/p/DecIHe2ADYk/" and a["engagement"]["source"] == "upload",
   "the link stays on the reference, the analysis says it came from the upload")
ok((P / "mine.mp4").exists() is False and ref_files.files_for(821)["files"][0].exists(), "the file is kept for later")
reset_pacing()
MODE.update(ytdlp="ok")
EVENTS.clear()
ref_files.pending(api, 821)
refs, analyses = api.maps()
cloud_sync.analyse_one(api, api.ref(821), analyses[821], Claude())
a = api.analysis(821)
ok(a["engagement"]["likes"] == 321 and a["engagement"]["views"] == 9000 and a["measured"]["caption_words"] == 2
   and EVENTS == [("yt-dlp", True)], "Instagram free: one look at the link (no download) for its likes and caption")
slides = pictures(P / "slides-up", ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg"], (1080, 1350))
ref_files.attach(822, sorted(slides, key=lambda p: ref_files.natural(p.name)), "upload")
api.add(822, "https://www.instagram.com/reels/DeKIm8tsyWj/", state="failed", problem="rate")
reset_pacing()
st = ref_library._state()
ref_library._pause(st, "rate", "paused", until=time.time() + 3600)
ref_library._save(st)
ref_files.pending(api, 822)
refs, analyses = api.maps()
claude = Claude()
cloud_sync.analyse_one(api, api.ref(822), analyses[822], claude)
a = api.analysis(822)
own = ref_files.files_for(822)
ok(a["state"] == "done" and a["kind"] == "images" and a["measured"]["slides_seen"] == 3
   and claude.calls and claude.calls[0]["system"] == style_study._SLIDES_SYSTEM, "uploaded slides go through the carousel read")
ok([p.name for p in own["files"]] == ["01.jpg", "02.jpg", "03.jpg"] and own["kind"] == "images",
   "kept in order (IMG_1, IMG_2, IMG_10)")

# The upload routes: the same pieces as a shot's video (uploads.py), a set, then "ready".
from flask import Flask
import uploads, cloud_auth, auth
uploads.MIN_FREE = 0  # the sandbox's disk, not what's under test
app = Flask("t")
app.before_request(uploads.guard)
app.register_blueprint(uploads.bp)
app.register_blueprint(ref_files.bp)
cloud_auth.verify = lambda tok: {"id": "u1", "email": "bryan@theurbanmakers.com", "role": "owner", "name": "Bryan"}
auth.email_allowed = lambda *a: True
web_api = FakeAPI()
web_api.add(824, "https://www.instagram.com/p/Dey86aEI9tj/", state="failed", problem="rate")
cloud_sync.Client = lambda *a, **k: web_api
c = app.test_client()
H = {"Authorization": "Bearer t"}


def send(ref_id, upload_set, k, path):
    data = path.read_bytes()
    r = c.post("/api/uploads", json={"reference_id": ref_id, "set": upload_set, "index": k, "name": path.name,
                                     "size": len(data), "type": "image/jpeg"}, headers=H)
    if r.status_code != 201:
        return r
    return c.put(f"/api/uploads/{r.get_json()['id']}?offset=0", data=data, headers={**H, "Content-Type": "application/octet-stream"})


ten = pictures(P / "ten", [f"{n}.jpg" for n in range(1, 11)])
upload_set = ref_files.new_set()
puts = [send(824, upload_set, k, f) for k, f in enumerate(ten, 1)]
ok(all(r.status_code == 200 and r.get_json()["state"] == "received" for r in puts), "ten slides up, piece by piece")
ok(web_api.analysis(824)["state"] == "failed", "nothing changes until the set is complete")
r = c.post("/api/uploads/references/824/ready", json={"set": upload_set}, headers=H)
ok(r.status_code == 200 and r.get_json() == {"kind": "images", "files": 10, "at": r.get_json()["at"]}
   and web_api.analysis(824)["state"] == "pending", "ready: ten pictures are the reference's, queued to analyse")
ok(c.get("/api/uploads/references/824", headers=H).get_json()["files"] == 10, "and the page can ask what Studio has")
mixed = ref_files.new_set()
send(824, mixed, 1, ten[0])
shutil.copy(VIDEO, P / "v.mp4")
data = (P / "v.mp4").read_bytes()
r1 = c.post("/api/uploads", json={"reference_id": 824, "set": mixed, "index": 2, "name": "v.mp4", "size": len(data),
                                  "type": "video/mp4"}, headers=H)
c.put(f"/api/uploads/{r1.get_json()['id']}?offset=0", data=data, headers={**H, "Content-Type": "application/octet-stream"})
r = c.post("/api/uploads/references/824/ready", json={"set": mixed}, headers=H)
ok(r.status_code == 415 and "not both" in r.get_json()["error"] and ref_files.files_for(824)["kind"] == "images",
   "a video and pictures together are refused; the slides already there stay")
r = c.post("/api/uploads", json={"reference_id": 824, "set": mixed, "index": 1, "name": "notes.pdf", "size": 10,
                                 "type": "application/pdf"}, headers=H)
ok(r.status_code == 415, "a file that's neither is refused at the start")
r = c.post("/api/uploads", json={"reference_id": 9999, "set": mixed, "index": 1, "name": "a.jpg", "size": 10,
                                 "type": "image/jpeg"}, headers=H)
ok(r.status_code == 404, "an upload for a reference that's gone is refused")

# ── 5. OneDrive: a known post's file or folder attaches to it; a folder of pictures is one carousel ──────────
api = FakeAPI()
api.add(821, "https://www.instagram.com/p/DecIHe2ADYk/", state="failed", problem="rate", why="Bryan: the cover's type")
api.add(822, "https://www.instagram.com/reels/DeKIm8tsyWj/", state="failed", problem="rate", why="Bryan: the details")
api.add(824, "https://www.instagram.com/p/Dey86aEI9tj/", state="failed", problem="rate", why="Bryan: ten slides")
for rid in (821, 822, 824):
    shutil.rmtree(ref_files._dir(rid), ignore_errors=True)
side = settings.REFERENCE_INBOX / "Inspiration"
side.mkdir(parents=True, exist_ok=True)
for name in ("markynv-editing-details-DeKIm8tsyWj.mp4", "iamkevinwalton-good-quality-videos-DecIHe2ADYk.mp4", "loose.jpg"):
    shutil.copy(VIDEO if name.endswith(".mp4") else ten[0], side / name)
past = time.time() - 600
for f in side.iterdir():
    os.utime(f, (past, past))
refs, _ = api.maps()
before = len(api.t["reference_videos"])
added = cloud_sync.reference_intake(api, refs)
ok(len(api.t["reference_videos"]) == before and not api.inserts, "the two videos make no new references")
ok(ref_files.files_for(822) and ref_files.files_for(822)["kind"] == "video" and ref_files.files_for(821)["kind"] == "video",
   "markynv-...-DeKIm8tsyWj.mp4 is 822's file, iamkevinwalton-...-DecIHe2ADYk.mp4 821's")
ok(api.analysis(822)["state"] == "pending" and api.analysis(822)["problem"] is None
   and api.ref(822)["why"] == "Bryan: the details", "the failed state clears, Bryan's note stays")
ok((side / "loose.jpg").exists() and not any(p.suffix == ".mp4" for p in side.iterdir()),
   "a loose picture stays where it is (as before); the videos left the inbox")
folder = side / "Dey86aEI9tj slides"
pictures(folder, [f"{n}.jpg" for n in (10, 1, 2, 3, 4, 5, 6, 7, 8, 9)])
cloud_sync.reference_intake(api, refs)
ok(folder.exists() and not ref_files.files_for(824), "a folder that has just changed waits (half-copied?)")
ref_files.intake(api, refs, cloud_sync.REF_SIDES, now=time.time() + ref_files.FOLDER_QUIET + 5)
own = ref_files.files_for(824)
ok(own and own["kind"] == "images" and len(own["files"]) == 10 and not folder.exists() and not api.inserts,
   "once quiet, the ten slides attach to 824 (no new reference) and leave the inbox")
with Image.open(own["files"][0]) as im0, Image.open(own["files"][9]) as im9:
    ok(im0.size == (1320, 1649) and im0.getpixel((5, 5)) != im9.getpixel((5, 5)), "kept at 1320x1649, in order")
ok(ref_files.match("x", ref_files.codes(refs)) is None and [p.name for p in sorted(
    [Path(f"{n}.jpg") for n in (10, 1, 2)], key=lambda p: ref_files.natural(p.name))] == ["1.jpg", "2.jpg", "10.jpg"],
   "names in natural order (2 before 10)")
refs, analyses = api.maps()
claude = Claude()
cloud_sync.analyse_one(api, api.ref(824), analyses[824], claude)
a = api.analysis(824)
ok(a["state"] == "done" and a["measured"]["slides_seen"] == 10 and a["measured"]["width"] == 1320
   and claude.calls[0]["system"] == style_study._SLIDES_SYSTEM, "analysed through the carousel path (ten slides)")
plain = side / "Kitchen ideas"
pictures(plain, ["b.jpg", "a.jpg"])
ref_files.intake(api, refs, cloud_sync.REF_SIDES, now=time.time() + ref_files.FOLDER_QUIET + 5)
new = [r for r in api.t["reference_videos"] if r.get("file_name") == "Kitchen ideas"]
ok(len(new) == 1 and new[0]["post_type"] == "carousel" and ref_files.files_for(new[0]["id"])["kind"] == "images",
   "a folder with no post code becomes one carousel reference")
# A duplicate made before this fix: merged into the link reference once, then retired.
api = FakeAPI()
api.add(822, "https://www.instagram.com/reels/DeKIm8tsyWj/", state="failed", problem="rate", why="Bryan: the details")
api.add(830, None, state="done", why="Content lead: the hands in frame", file_name="markynv-editing-details-DeKIm8tsyWj.mp4",
        title="markynv editing details DeKIm8tsyWj")
api.analysis(830).update(measured={"duration": 6.0}, look={"hook": "h"}, stills=["refs/830/1.jpg"], analyser=2,
                         kind="video", engagement={"source": "file"})
shutil.rmtree(ref_files._dir(822), ignore_errors=True)
shutil.copy(VIDEO, settings.REFERENCES / "markynv-editing-details-DeKIm8tsyWj.mp4")
refs, _ = api.maps()
lines = ref_files.merge_duplicates(api, refs)
ok(api.ref(830)["status"] == "retired" and api.ref(822)["status"] == "active", "the duplicate is retired")
ok(api.ref(822)["why"] == "Bryan: the details\n\nContent lead: the hands in frame", "both notes on the link reference")
a = api.analysis(822)
ok(a["state"] == "done" and a["measured"] == {"duration": 6.0} and a["stills"] == ["refs/830/1.jpg"]
   and a["problem"] is None and a["engagement"]["source"] == "upload", "its analysis is the link reference's now")
ok(ref_files.files_for(822) and ref_files.files_for(822)["merged_from"] == 830, "and its file")
n = len(api.updates)
refs, _ = api.maps()
ok(not ref_files.merge_duplicates(api, refs) and len(api.updates) == n, "a second run changes nothing")
# OneDrive itself: pictures in a References folder are pulled with their folder; loose ones aren't.
old = datetime.now(timezone.utc) - timedelta(minutes=10)
mod = old.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
entries = [{"Path": "References/Inspiration/Dey86aEI9tj slides/01.jpg", "Size": 5, "ModTime": mod},
           {"Path": "References/Inspiration/Dey86aEI9tj slides/02.jpg", "Size": 5,
            "ModTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")},
           {"Path": "References/Inspiration/loose.jpg", "Size": 5, "ModTime": mod},
           {"Path": "References/Inspiration/a-DeKIm8tsyWj.mp4", "Size": 5, "ModTime": mod}]
ready, settling, odd = onedrive._waiting(entries)
ok([str(i["local"]) for i in ready] == ["References/Inspiration/Dey86aEI9tj slides/01.jpg",
                                         "References/Inspiration/a-DeKIm8tsyWj.mp4"] and len(settling) == 1,
   "OneDrive pulls a folder's pictures into the same folder (a loose picture isn't pulled)")
onedrive._ref_busy.clear()
onedrive._ref_busy.update(i["local"].parent.as_posix() for i in ready + settling if i["local"].parts[0] == "References")
ok(onedrive.ref_folder_busy(settings.INBOX / "References/Inspiration/Dey86aEI9tj slides")
   and not onedrive.ref_folder_busy(settings.INBOX / "References/Inspiration/Kitchen ideas"),
   "a folder with a picture still uploading counts as busy")
onedrive._ref_busy.clear()

# ── 6. No cookie value anywhere ───────────────────────────────────────────────────────────────────────
cookies()
reset_pacing()
MODE.update(ytdlp=f"ERROR: [Instagram] X: HTTP Error 400 (cookie: sessionid={SECRET}; csrftoken=abc)",
            gdl=f"[instagram][error] HttpError: '400 ' for 'https://www.instagram.com/api/v1/media/1/info/' sessionid={SECRET}")
last = None
for url in ("https://www.instagram.com/p/DecIHe2ADYk/", "https://www.instagram.com/reels/DeKIm8tsyWj/"):
    try:
        style_study.analyse(url, P / "c" / "stills", download_dir=P / "c" / "dl", client=Claude(), ref="t:2")
    except style_study.DownloadFailed as e:
        last = e
ok(last is not None and SECRET not in str(last), "the reason carries no cookie value")
ok(SECRET not in ig_health._file().read_text(), "the record carries no cookie value")
ok(SECRET not in LOG.getvalue() and "sessionid" not in json.dumps(ref_library.info(conn)), "nor any log line, nor the heartbeat")
ok(ig_health.scrub(f"a sessionid={SECRET}; csrftoken=zz b") == "a sessionid=…; csrftoken=… b", "scrub blanks cookie values")
finish()
