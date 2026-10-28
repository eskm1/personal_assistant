"""Photo carousels from a OneDrive folder (the content lead's showcase brief, 2026-10-28): the photos named in a
carousel job become the slides in that order (case and spaces ignored, any picture type, a GIF's first frame), the
cover line on slide 1 only, in the serif, small, at the bottom; a missing or doubled photo stops the job before the
idea changes; the folder name is never kept on the idea or the draft; and showcase_batch's plan (re-date, re-title,
caption, grid comment, which ideas are sent) refuses anything ambiguous. OneDrive and Supabase are local fakes.
Run: python tests/quality/suite_showcase.py (from studio/)"""
import json, os, shutil
from pathlib import Path

from common import ROOT, ok, finish
from PIL import Image
import settings, catalog, carousel, cloud_sync, onedrive, showcase, showcase_batch, slide_images

settings.ensure_folders()
conn = catalog.connect()

# ── A fake OneDrive folder ────────────────────────────────────────────────────
DRIVE = ROOT / "t_drive"
FOLDER = "Work/Showcases/Home A"   # a full path from OneDrive's top, as on the server
shutil.rmtree(DRIVE, ignore_errors=True)
(DRIVE / FOLDER).mkdir(parents=True)


def pic(name, colour, size=(1600, 2000), fmt=None):
    img = Image.new("RGB", size, colour)
    (DRIVE / FOLDER / name).parent.mkdir(parents=True, exist_ok=True)
    img.save(DRIVE / FOLDER / name, fmt)


# Like the long condo's folder: pictures at the top, and subfolders "Other photos/" and "GIF/".
pic("Other photos/12.jpg", (200, 40, 40))
pic("11.JPG", (40, 200, 40))
pic("Other photos/1.png", (40, 40, 200))
pic("GIF/1.gif", (220, 220, 40), fmt="GIF")
pic("12.jpg", (90, 90, 90))                      # a 12 at the top too: "12" and "Other 12" stay apart
pic("16.heic.txt", (0, 0, 0), fmt="PNG")       # not a picture name: ignored
pic("Dup.jpg", (10, 10, 10))
pic("Dup.png", (10, 10, 10))
pic("Other photos/5.jpg", (10, 10, 10))
pic("Other pics/5.jpg", (10, 10, 10))            # "Other 5" fits two pictures
copied = []


listed = []


def fake_ls(*parts, recursive=False, dirs=False, timeout=300):
    folder = DRIVE / "/".join(parts)
    listed.append(("/".join(parts), recursive))
    if not folder.is_dir():
        raise onedrive.OneDriveError("OneDrive problem: directory not found")
    found = sorted(folder.rglob("*") if recursive else folder.iterdir())
    return [{"Path": p.relative_to(folder).as_posix(), "Name": p.name, "IsDir": p.is_dir(), "Size": p.stat().st_size}
            for p in found if not (dirs is False and p.is_dir())]


def fake_run(args, timeout):
    assert args[0] == "copyto"
    remote, local = args[1], args[2]
    rel = remote.split(":", 1)[1]
    copied.append(rel)
    shutil.copyfile(DRIVE / rel, local)
    return ""


onedrive._lsjson, onedrive._run, onedrive.rclone_path = fake_ls, fake_run, lambda: "/usr/bin/rclone"


class FakeApi:
    def __init__(self, jobs=(), ideas=()):
        self.jobs, self.ideas = list(jobs), {i["id"]: dict(i) for i in ideas}
        self.updates, self.uploads, self.inserts = [], [], []

    def request(self, method, path, **kw):
        return type("R", (), {"json": lambda s: [], "content": b""})()

    def select(self, table, params=None):
        if table == "jobs":
            return [j for j in self.jobs if j["state"] == "queued"]
        if table == "ideas":
            iid = int(params["id"].split(".")[1])
            return [self.ideas[iid]] if iid in self.ideas else []
        return []

    def update(self, table, filters, values):
        self.updates.append((table, filters, values))
        if table == "jobs":
            jid = int(filters["id"].split(".")[1]) if "id" in filters else None
            for j in self.jobs:
                if jid is None or j["id"] != jid:
                    continue
                if "state" in filters and j["state"] != filters["state"].split(".")[1]:
                    return []
                j.update(values)
                return [j]
            return []
        if table == "ideas":
            iid = int(filters["id"].split(".")[1])
            self.ideas[iid].update(values)
        return [values]

    def insert(self, table, rows, **kw):
        self.inserts.append((table, rows))
        return []

    def upload(self, path, content, content_type="image/jpeg"):
        self.uploads.append(path)


# ── The spec and the match ────────────────────────────────────────────────────
print("== which photos")
for bad, why in (({"folder": "", "files": ["a", "b"]}, "no folder"), ({"folder": "../etc", "files": ["a", "b"]}, "a folder going up"),
                 ({"folder": "Showcases/<the folder>", "files": ["a", "b"]}, "the spec's placeholder folder"),
                 ({"folder": FOLDER, "files": ["a"]}, "one photo"), ({"folder": FOLDER, "files": ["a", "A "]}, "the same photo twice"),
                 ({"folder": FOLDER, "files": [str(n) for n in range(30)]}, "more than Instagram takes"),
                 ({"folder": FOLDER, "files": ["a", "b"], "cover": "x" * 200}, "a long cover line")):
    try:
        showcase.check_spec(bad)
        ok(False, f"refused: {why}")
    except showcase.ShowcaseError as e:
        ok(True, f"refused: {why} ({e})")
entries = showcase.listing(FOLDER)
ok(listed[-1] == (FOLDER, True), "the folder is listed with its subfolders")
got = showcase.match(entries, ["other 12", "11", "Other  1", "gif 1", "12", "Other photos/12"])
ok([e["Path"] for e in got] == ["Other photos/12.jpg", "11.JPG", "Other photos/1.png", "GIF/1.gif", "12.jpg", "Other photos/12.jpg"],
   f"\"Other 12\" is 12 in the Other subfolder, \"11\" at the top, the path works too; case and spaces ignored ({[e['Path'] for e in got]})")
try:
    showcase.match(entries, ["Other 12", "19", "Dup", "16", "Other 5", "GIF 2"])
    ok(False, "a missing or doubled photo stops it")
except showcase.ShowcaseError as e:
    ok(all(n in str(e) for n in ("19", "16", "Dup", "Other 5", "GIF 2")) and "Nothing was changed" in str(e),
       f"a missing or doubled photo stops it, each named ({e})")
try:
    showcase.listing("Work/Showcases/Nowhere")
    ok(False, "a folder that isn't there says so")
except showcase.ShowcaseError as e:
    ok("full path" in str(e), f"a folder that isn't there says so ({e})")

# ── The job: photos become the slides, then Make this ─────────────────────────
print("== the carousel job")
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, post_type, remote_id) VALUES ('Mon 2 Nov · One long room, one long table', 'urban_makers',
                      'carousel', 'new', 'go', 'plan', 'plan', '', '[]', '[]', '[]', ?, 'finished', 7001)""", (catalog.now(),))
local = cur.lastrowid
conn.commit()
CAPTION = "A long, narrow condo.\nStone top [check], steel base.\nLong table or round?\n#condorenovation"
spec = {"folder": FOLDER, "files": ["Other 12", "11", "Other 1", "GIF 1"], "cover": "One long room, one long table."}
spec_photos = dict(spec)
api = FakeApi(jobs=[{"id": 1, "kind": "carousel", "state": "queued", "idea_id": 7001, "created_at": catalog.now(),
                     "payload": {"caption": CAPTION, "photos": spec}}],
              ideas=[{"id": 7001, "format": "carousel", "slides": [{"role": "cover", "heading": "Old words", "body": "x",
                                                                    "image": {"source": "text"}}], "caption": "old",
                      "slide_layout": None}])
cloud_sync._grid_online = True
done = cloud_sync.run_jobs(api, conn, {7001: local})
job = api.jobs[0]
ok(job["state"] == "done" and job.get("error") is None, f"the job is done ({job.get('state')}, {job.get('error')})")
slides = api.ideas[7001]["slides"]
ok(len(slides) == 4 and all(s["image"]["source"] == "photo" for s in slides) and api.ideas[7001]["slide_layout"] == "photos",
   "the idea's slides are the four photos, laid out photos only")
ok(slides[0]["heading"] == "One long room, one long table." and all(not s["heading"] and not s["body"] for s in slides[1:]),
   "the cover line on slide 1 only; no words on the others")
ok(copied == [f"{FOLDER}/Other photos/12.jpg", f"{FOLDER}/11.JPG", f"{FOLDER}/Other photos/1.png", f"{FOLDER}/GIF/1.gif"],
   "fetched from OneDrive in the order given, from their subfolders")
ok(len(api.uploads) == 4 and all(u.startswith("photos/") for u in api.uploads), "each photo's preview uploaded for the planner")
cols = [Image.open(slide_images.photo_path(s["image"]["photo_id"])).convert("RGB").getpixel((5, 5)) for s in slides]
ok(cols[0][0] > 150 and cols[1][1] > 150 and cols[2][2] > 150 and cols[3][0] > 150 and cols[3][1] > 150,
   f"the right photo on each slide, the GIF's first frame included ({cols})")
row = conn.execute("SELECT p.* FROM productions p JOIN ideas i ON i.production_id = p.id WHERE i.id = ?", (local,)).fetchone()
plan = json.loads(row["plan"])
ok(row is not None and row["format"] == "carousel" and plan.get("layout") == "photos" and len(plan["slides"]) == 4,
   "a carousel production, photos only, four slides")
ok(row["caption"] == CAPTION, "the caption kept exactly, [check] and hashtags included")
ok(FOLDER not in json.dumps(dict(row)) and "Home A" not in json.dumps(api.ideas[7001]),
   "the folder name isn't kept on the draft or the idea")

# A missing photo: the job fails, the idea and its slides untouched, no production.
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, remote_id) VALUES ('Two', 'urban_makers', 'carousel', 'new', 'go', 'plan', 'plan', '', '[]',
                      '[]', '[]', ?, 7002)""", (catalog.now(),))
local2 = cur.lastrowid
conn.commit()
before = [{"role": "cover", "heading": "Keep me", "body": "", "image": {"source": "text"}}]
api2 = FakeApi(jobs=[{"id": 2, "kind": "carousel", "state": "queued", "idea_id": 7002, "created_at": catalog.now(),
                      "payload": {"caption": "c", "photos": {"folder": FOLDER, "files": ["Other 12", "19"]}}}],
               ideas=[{"id": 7002, "format": "carousel", "slides": before, "caption": "c", "slide_layout": None}])
cloud_sync.run_jobs(api2, conn, {7002: local2})
ok(api2.jobs[0]["state"] == "failed" and "19" in (api2.jobs[0].get("error") or ""), f"a missing photo: the job fails, saying which ({api2.jobs[0].get('error')})")
ok(api2.ideas[7002]["slides"] == before and not [u for u in api2.updates if u[0] == "ideas"], "and the idea isn't changed")
ok(conn.execute("SELECT production_id FROM ideas WHERE id = ?", (local2,)).fetchone()[0] is None, "and nothing is made")

# A carousel job without photos: as before (the idea's own slides).
pid = [os.urandom(16).hex() for _ in range(2)]
for p in pid:
    Image.new("RGB", (800, 1000), (90, 90, 90)).save(slide_images.PHOTOS / f"{p}.jpg")
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, remote_id) VALUES ('Three', 'urban_makers', 'carousel', 'new', 'go', 'plan', 'plan', '', '[]',
                      '[]', '[]', ?, 7003)""", (catalog.now(),))
local3 = cur.lastrowid
conn.commit()
own = [{"role": "cover", "heading": "", "body": "", "image": {"source": "photo", "photo_id": p}} for p in pid]
api3 = FakeApi(jobs=[{"id": 3, "kind": "carousel", "state": "queued", "idea_id": 7003, "created_at": catalog.now(), "payload": {}}],
               ideas=[{"id": 7003, "format": "carousel", "slides": own, "caption": "c", "slide_layout": "photos"}])
cloud_sync.run_jobs(api3, conn, {7003: local3})
ok(api3.jobs[0]["state"] == "done" and not [u for u in api3.updates if u[0] == "ideas" and "slides" in u[2]],
   "a carousel job without photos makes the idea's own slides, as before")

# ── The cover line: the serif, small, at the bottom ───────────────────────────
print("== the cover line")
css = carousel._COVER_LINE_CSS % {"serif": carousel.SERIF.resolve().as_uri(), **carousel.COLOURS}
ok(carousel.SERIF.is_file() and "UM Serif" in css and "font-weight:400" in css and "50px" in css,
   "the cover line is set in the serif (Instrument Serif), regular, 50 px at most")
made = carousel.lay_out(conn, slides[:2], ROOT / "t_cover", layout=carousel.PHOTOS)
cov = made["layout"][0]
box = cov["boxes"]["heading"]
ok(cov["layout"] == "photo-cover-line" and not cov["overflow"] and box and box[3] > 1350 - 260 and box[1] > 1000,
   f"one short line, at the bottom ({box}, {cov['heading_px']} px)")
ok(box[2] <= carousel.GRID_RIGHT and box[0] >= carousel.GRID_LEFT, "inside the profile grid's crop")
shutil.copy(made["files"][0], ROOT / "cover_line.png")

# ── showcase_batch: the plan ──────────────────────────────────────────────────
print("== the batch")
ideas = [{"id": 24, "title": "Mon 16 Nov · One long room, one long table", "format": "carousel", "status": "new", "production": None},
         {"id": 25, "title": "Wed 18 Nov · The living room", "format": "carousel", "status": "new", "production": None},
         {"id": 26, "title": "Fri 20 Nov · The aquarium bar", "format": "carousel", "status": "made", "production": {"id": 9}},
         {"id": 27, "title": "Mon 23 Nov · Kitchen and bathroom", "format": "carousel", "status": "new", "production": None}]
comments = {24: [{"id": 501, "idea_id": 24, "body": "Grid: row 4"}, {"id": 502, "idea_id": 24, "body": "Nice"}]}
spec = {"project": "Finished homes", "folder": FOLDER, "ideas": [
    {"title": "One long room, one long table", "date": "2026-11-02", "grid": "Week 1 is the top row.", "caption": "C1",
     "photos": ["Other 12", "11"], "cover": "One long room."},
    {"title": "The living room", "date": "2026-11-04", "grid": "Week 1 is the top row."},
    {"title": "The aquarium bar", "date": "2026-12-02", "photos": ["Other 12", "11"]}]}
rows, problems = showcase_batch.plan(spec, ideas, comments)
ok(not problems and [r["sets"]["title"] for r in rows] == ["Mon 2 Nov · One long room, one long table", "Wed 4 Nov · The living room",
                                                           "Wed 2 Dec · The aquarium bar"], f"titles carry the new day ({problems})")
ok(rows[0]["sets"]["post_on"] == "2026-11-02" and rows[0]["sets"]["caption"] == "C1" and "caption" not in rows[1]["sets"],
   "the posting date, and the caption only where one is given")
ok(rows[0]["grid_old"] and rows[0]["grid_old"][0]["id"] == 501 and not rows[1]["grid_old"], "the grid comment found to change, or added")
ok(rows[0]["make"] and rows[0]["make"]["folder"] == FOLDER and rows[1]["make"] is None and rows[2]["make"] is None,
   "only an unmade idea with photos is sent; one already made is left as it is")
ok(not showcase_batch.check_photos(rows), "the dry run finds every carousel's photos in OneDrive")
real_ls = onedrive._lsjson
def not_here(*a, **k):
    raise onedrive.OneDriveError(onedrive.NOT_CONNECTED)
onedrive._lsjson = not_here
here = showcase_batch.check_photos(rows)
onedrive._lsjson = real_ls
ok(len(here) == 1 and "Studio server" in here[0] and "server_move" not in here[0],
   f"on a computer without Studio's OneDrive connection it says to run it on the server ({here})")
spec["ideas"][0]["photos"] = ["Other 12", "Other 99"]
rows2, _ = showcase_batch.plan(spec, ideas, comments)
bad = showcase_batch.check_photos(rows2)
ok(len(bad) == 1 and "Other 99" in bad[0] and "#24" in bad[0], f"a missing photo shows in the dry run, before anything changes ({bad})")
# A rerun finds its own grid notes (the CTO, 2026-10-28: the notes start "Week 1 ...", not "Grid", so a second run
# added a copy to every idea): the oldest is kept and changed in place, the script's later copies are removed.
dup = {25: [{"id": 601, "idea_id": 25, "body": "Week 1 is the top row.", "author_name": "Content plan"},
            {"id": 602, "idea_id": 25, "body": "Week 1 is the top row.", "author_name": "Content plan"},
            {"id": 603, "idea_id": 25, "body": "Love this one", "author_name": "Michelle"}]}
rows_d, _ = showcase_batch.plan(spec, ideas, dup)
ok(rows_d[1]["grid_old"][0]["id"] == 601 and [c["id"] for c in rows_d[1]["extra"]] == [602],
   "a rerun finds its own grid note (oldest kept), and the copy a rerun made is the one to remove")


class BatchApi:
    def __init__(self):
        self.calls = []
        self.comments = [dict(c) for c in dup[25]]

    def select(self, table, params=None):
        if table == "projects":
            return [{"id": 5, "name": "Finished homes"}]
        if table == "ideas":
            return [dict(i) for i in ideas]
        if table == "comments":
            return [dict(c) for c in self.comments]
        return []

    def update(self, table, filters, values):
        self.calls.append(("update", table, filters, values))
        return [values]

    def insert(self, table, rows, **kw):
        self.calls.append(("insert", table, rows))
        return []

    def request(self, method, path, **kw):
        self.calls.append((method, path, kw.get("params")))
        if method == "DELETE":
            gone = {int(x) for x in kw["params"]["id"][4:-1].split(",")}
            self.comments = [c for c in self.comments if c["id"] not in gone or c.get("author_name") != "Content plan"]
        return None


import sys as _sys
bapi = BatchApi()
real_client = showcase_batch.cloud_sync.Client
showcase_batch.cloud_sync.Client = lambda: bapi
spec_file = ROOT / "grid_spec.json"
spec_file.write_text(json.dumps({"project": "Finished homes", "ideas": [
    {"title": "The living room", "date": "2026-11-04", "grid": "Week 1 is the top row."}]}))
argv = _sys.argv
_sys.argv = ["showcase_batch.py", str(spec_file), "--apply", "--no-photo-check"]
code = showcase_batch.main()
first = list(bapi.calls)
bapi.calls.clear()
code2 = showcase_batch.main()
second = list(bapi.calls)
_sys.argv, showcase_batch.cloud_sync.Client = argv, real_client
ok(code == 0 and [c for c in first if c[0] == "DELETE"] and not [c for c in first if c[0] == "insert"]
   and [c["id"] for c in bapi.comments] == [601, 603],
   f"run with --apply: no new copy, the duplicate removed, the oldest and Michelle's comment kept ({[c['id'] for c in bapi.comments]})")
bapi.comments.append({"id": 604, "idea_id": 25, "body": "Week 1 is the top row.", "author_name": "Content plan"})
bapi.calls.clear()
_sys.argv, showcase_batch.cloud_sync.Client = ["showcase_batch.py", str(spec_file), "--apply", "--grid-only", "--no-photo-check"], (lambda: bapi)
code3 = showcase_batch.main()
_sys.argv, showcase_batch.cloud_sync.Client = argv, real_client
ok(code3 == 0 and [c["id"] for c in bapi.comments] == [601, 603] and not [c for c in bapi.calls if c[0] == "update" and c[1] == "ideas"]
   and not [c for c in bapi.calls if c[0] == "insert"], "--grid-only: duplicates removed, the ideas' dates, titles and captions untouched")
ok(code2 == 0 and not [c for c in second if c[0] in ("insert", "DELETE") or (c[0] == "update" and c[1] == "comments")],
   "run again: the grid notes are left as they are")
_, problems = showcase_batch.plan({"project": "x", "folder": FOLDER, "ideas": [{"title": "room", "date": "2026-11-02"}]}, ideas, {})
ok(problems and "3 ideas match" in problems[0], f"a title matching several ideas stops it ({problems})")
_, problems = showcase_batch.plan({"project": "x", "ideas": [{"title": "Kitchen", "date": "2026-11-02"},
                                                             {"title": "Kitchen and bathroom", "date": "2026-11-03"}]}, ideas, {})
ok(any("both pick" in p for p in problems), "two entries picking one idea stops it")
_, problems = showcase_batch.plan({"project": "x", "ideas": [{"title": "Kitchen", "date": "2 Nov"}]}, ideas, {})
ok(any("YYYY-MM-DD" in p for p in problems), "a date that isn't a date stops it")

# ── As written: Studio never rewrites a photos-only carousel ──────────────────
print("== as written")
import self_fix, draft_review
row = dict(conn.execute("SELECT * FROM productions WHERE id = (SELECT production_id FROM ideas WHERE id = ?)", (local,)).fetchone())
pid0 = row["id"]
before_slides = json.loads(conn.execute("SELECT slides FROM productions WHERE id = ?", (pid0,)).fetchone()[0] or "null")
ok(carousel.layout_of(row) == carousel.PHOTOS, "the showcase draft is photos only")
ctx = draft_review.carousel_context(row, {"slides": plan["slides"], "pictures": []})
ok("Photos only, by design" in ctx and "Never ask for words on a slide" in ctx and "editor_can_fix false" in ctx,
   "Studio's read is told it's photos only by design, the ask in the caption")
ok("[check]" in draft_review._CAROUSEL_SYSTEM and "never filled in or removed" in draft_review._CAROUSEL_SYSTEM
   and "Never suggest adding an offer" in draft_review._CAROUSEL_SYSTEM, "and that a [check] and a new offer are never fixes")
READ = {"kind": "carousel", "verdict": "Nearly ready: 2 things to fix", "scores": {"ask": "fix"}, "works": ["Calm photos"],
        "fixes": [{"what": "Add an ask slide: Book a free consultation", "why": "No call to action.", "slide": None, "area": "ask",
                   "when": "this_draft", "editor_can_fix": True,
                   "action": {"type": "slide_text", "item": None, "field": "caption", "to": "Book a free consultation today!"}},
                  {"what": "Fill in the stone type", "why": "A [check] is left.", "slide": None, "area": "claims",
                   "when": "this_draft", "editor_can_fix": True,
                   "action": {"type": "slide_text", "item": 1, "field": "heading", "to": "Granite"}}]}
reads, patched = [], []
real_read, real_patch, real_srev = draft_review.safe_run, carousel.patch, carousel.self_revise
draft_review.safe_run = lambda c, p, cl=None, ref=None: (reads.append(p), json.loads(json.dumps(READ)))[1]
carousel.patch = lambda *a, **k: (patched.append("patch"), (None, []))[1]
carousel.self_revise = lambda *a, **k: patched.append("revise")
conn.execute("UPDATE productions SET status = 'producing', review_version = version WHERE id = ?", (pid0,))
conn.commit()
out = self_fix.deliver(conn, pid0)
draft_review.safe_run, carousel.patch, carousel.self_revise = real_read, real_patch, real_srev
after = conn.execute("SELECT * FROM productions WHERE id = ?", (pid0,)).fetchone()
hist = json.loads(after["self_fix"] or "{}")
ok(reads == [pid0] and not patched and hist.get("rounds") == [] and hist.get("stopped") == "photos",
   f"photos only: Studio reads it once and makes no fix round ({patched}, {hist.get('stopped')})")
ok(after["caption"] == CAPTION and json.loads(after["slides"] or "null") == before_slides,
   "its slides and caption stay exactly as written, [check] included")
whys = [f.get("left_because", {}).get("text", "") for f in (json.loads(after["review"] or "{}").get("fixes") or [])]
ok(whys and all("photos-only carousel" in w for w in whys), f"each fix the read found is left for Bryan, saying why ({whys[:1]})")
real_current = carousel.current
r, res = carousel.patch(conn, pid0, [{"n": 1, "action": {"type": "slide_text", "item": 1, "field": "heading", "to": "New words"}}], 1)
ok(r is None and not res[0]["ok"] and "as written" in res[0]["why"], "a patch asked of it anyway changes nothing")
try:
    carousel.self_revise(conn, pid0, [], 1)
    ok(False, "a rewrite asked of it anyway is refused")
except RuntimeError as e:
    ok("as written" in str(e), "a rewrite asked of it anyway is refused")

# ── [check] and offers, in any carousel ───────────────────────────────────────
print("== [check] and offers")
g = carousel.guard_words
old = "Stone top [check], steel base.\nLong table or round?"
ok(g(old, "Stone top, steel base.\nLong table or round?")[0] == old, "a [check] removed: kept as it was")
ok(g(old, "Stone top granite, steel base.\nLong table or round?")[0] == old, "a [check] filled in: kept as it was")
ok(g(old, old.replace("[check]", "[CHECK]"))[0] == old.replace("[check]", "[CHECK]"), "the same [check] in other case is fine")
ok(g(old, old + "\nBook a free consultation today!")[0] == old, "an offer the draft didn't have: kept as it was")
ok(g(old, old.replace("round?", "round? Book a free consultation."), "Please add: book a free consultation")[0] != old,
   "unless Bryan's own note asked for it")
ok(g("Book a free consultation.", "Book a free consultation now.")[0] == "Book a free consultation now.",
   "an offer the draft already had may be reworded")
ok(g(old, "Stone top [check], steel base.\nRound or long?")[0] != old, "other words change as before")
w = conn.execute("""INSERT INTO productions (title, brand, format, status, version, caption, plan, created_at, updated_at)
                    VALUES ('Words', 'urban_makers', 'carousel', 'ready', 1, ?, ?, ?, ?)""",
                 (old, json.dumps({"slides": []}), catalog.now(), catalog.now())).lastrowid
conn.execute("UPDATE productions SET slides = ? WHERE id = ?", (json.dumps({"version": 1, "slides": [
    {"key": "a", "role": "cover", "heading": "Stone [check: which stone]", "body": "", "image": {"source": "text"}},
    {"key": "b", "role": "point", "heading": "Point", "body": "", "image": {"source": "text"}}], "files": []}), w))
conn.commit()
calls = []
real_lay = carousel.lay_out
carousel.lay_out = lambda c, sl, out_dir, **k: (calls.append(1), out_dir.mkdir(parents=True, exist_ok=True),
                                                {"files": [str(ROOT / "cover_line.png")], "layout": [], "pictures": []})[2]
_, res = carousel.patch(conn, w, [{"n": 1, "action": {"type": "slide_text", "item": None, "field": "caption", "to": "Stone top, steel base."}},
                                  {"n": 2, "action": {"type": "slide_text", "item": 1, "field": "heading", "to": "Stone: granite"}},
                                  {"n": 3, "action": {"type": "slide_text", "item": None, "field": "caption", "to": old + " Book a free consultation."}},
                                  {"n": 4, "action": {"type": "slide_text", "item": 2, "field": "heading", "to": "A better point"}}], 1)
carousel.lay_out = real_lay
cap_now = conn.execute("SELECT caption FROM productions WHERE id = ?", (w,)).fetchone()[0]
ok([x["ok"] for x in res] == [False, False, False, True] and "[check]" in res[0]["why"] and "[check]" in res[1]["why"]
   and "offer" in res[2]["why"] and cap_now == old,
   f"a words carousel's patches: a [check] removed or filled and a new offer refused, saying why; the rest made ({[x.get('why') for x in res]})")
# Finished homes: no offer, ever (Bryan, 2026-10-28, via the content lead).
ok(g("Long table or round?", "Long table or round? Free consultation this month.", "add a free consultation", no_offers=True)[0]
   == "Long table or round?" and g("Book a free consultation.", "Book now.", no_offers=True)[0] == "Book a free consultation.",
   "a Finished homes post: no offer at all, not even one Bryan's note asked for or the draft had")
ok(g("Long table or round?", "Long table, or round?", no_offers=True)[0] == "Long table, or round?", "other words change as before")
fin = dict(row, plan=json.dumps({**json.loads(row["plan"]), "post_type": "finished"}))
ctx = draft_review.carousel_context(fin, {"slides": plan["slides"], "pictures": []})
ok("no offer, ever" in ctx and "A missing offer is never a fault" in ctx, "Studio's read: a missing offer is never a fault on a Finished homes post")
import content_plan, playbook
ok("no offer" in content_plan.KINDS["finished"]["ask"] and "talk to us" not in content_plan.KINDS["finished"]["ask"],
   "the Finished homes kind's ask is a one-word question, no offer")
ts = (Path(settings.__file__).parent / "cloud/supabase/functions/_shared/prompts.ts").read_text()
ok('ask: "a one-word question to the reader (no offer, ever: not even a free consultation)"' in ts, "and the planner's the same")
old_pb = ROOT / "old_playbook.md"
old_pb.write_text("# Studio playbook\n\n## Story\n\n- One idea per post.\n\n## Pacing and cutting\n\n- Cut between phrases.\n")
added = playbook.migrate(old_pb)
text_pb = old_pb.read_text()
ok(len(added) == 1 and "Finished homes posts carry no offer, ever" in text_pb
   and text_pb.index("carry no offer") < text_pb.index("## Pacing"), "an existing playbook gets the rule once, under Story")
ok(playbook.migrate(old_pb) == [] and old_pb.read_text() == text_pb, "and never twice")
ok("Finished homes posts carry no offer, ever" in playbook.SEED.read_text(), "a new playbook starts with it")
fw = conn.execute("""INSERT INTO productions (title, brand, format, status, version, caption, plan, created_at, updated_at)
                     VALUES ('Fin', 'urban_makers', 'carousel', 'ready', 1, 'Long table or round?', ?, ?, ?)""",
                  (json.dumps({"post_type": "finished"}), catalog.now(), catalog.now())).lastrowid
conn.execute("UPDATE productions SET slides = ? WHERE id = ?", (json.dumps({"version": 1, "slides": [
    {"key": "a", "role": "cover", "heading": "Cover", "body": "", "image": {"source": "text"}},
    {"key": "b", "role": "point", "heading": "Point", "body": "", "image": {"source": "text"}}], "files": []}), fw))
conn.commit()
_, res = carousel.patch(conn, fw, [{"n": 1, "action": {"type": "slide_text", "item": None, "field": "caption",
                                                       "to": "Long table or round? Free consultation for new homeowners."}}], 1)
ok(not res[0]["ok"] and "Finished homes" in res[0]["why"], f"Studio's fix round can't add one to a Finished homes post ({res[0].get('why')})")
_, probs = showcase_batch.plan({"project": "x", "ideas": [{"title": "Kitchen and bathroom", "date": "2026-11-02",
                                                           "caption": "Nice. Book a free consultation!"}]}, ideas, {})
ok(any("offer" in p for p in probs), "the batch refuses a Finished homes caption with an offer")
kept = carousel.photos_only([{"key": "a", "heading": "Cover [check]", "body": "", "image": {}}, {"key": "b", "heading": "", "body": "", "image": {}}],
                            [{"key": "z", "heading": "Cover, better", "body": "More words", "image": {}}, {"key": "y", "heading": "Ask", "body": "Book now", "image": {}},
                             {"key": "x", "heading": "Extra", "body": "", "image": {}}], cover_from_notes=True)
ok(len(kept) == 2 and [k["key"] for k in kept] == ["a", "b"] and kept[0]["heading"] == "Cover [check]" and not kept[1]["heading"]
   and not any(k["body"] for k in kept), "a revision of a photos-only carousel keeps its photos and order, no words added, the cover's [check] kept")

# ── Made again from the drafts as written (--remake) ──────────────────────────
print("== made again")
api4 = FakeApi(jobs=[{"id": 9, "kind": "carousel", "state": "queued", "idea_id": 7001, "created_at": catalog.now(),
                      "payload": {"caption": CAPTION, "photos": spec_photos, "remake": True}}],
               ideas=[{"id": 7001, "format": "carousel", "slides": [], "caption": "x", "slide_layout": "photos"}])
conn.execute("UPDATE productions SET status = 'ready', caption = 'Rewritten. Book a free consultation.' WHERE id = ?", (pid0,))
conn.commit()
cloud_sync.run_jobs(api4, conn, {7001: local})
new_pid = conn.execute("SELECT production_id FROM ideas WHERE id = ?", (local,)).fetchone()[0]
old_row = conn.execute("SELECT * FROM productions WHERE id = ?", (pid0,)).fetchone()
new_row = conn.execute("SELECT * FROM productions WHERE id = ?", (new_pid,)).fetchone()
ok(api4.jobs[0]["state"] == "done" and new_pid and new_pid != pid0, f"the carousel is made again ({api4.jobs[0].get('error')})")
ok(old_row["status"] == "rejected" and old_row["archived_at"] and conn.execute(
    "SELECT 1 FROM feedback WHERE target = 'production' AND target_id = ? AND body = ?", (pid0, showcase.REMAKE_NOTE)).fetchone(),
   "the rewritten draft is archived, with a note saying why (its files kept)")
ok(new_row["caption"] == CAPTION and len(json.loads(new_row["plan"])["slides"]) == 4 and carousel.layout_of(new_row) == carousel.PHOTOS,
   "the new one is the drafts as written: the caption, the photos in order, photos only")
conn.execute("UPDATE productions SET status = 'approved' WHERE id = ?", (new_pid,))
conn.commit()
api5 = FakeApi(jobs=[{"id": 10, "kind": "carousel", "state": "queued", "idea_id": 7001, "created_at": catalog.now(),
                      "payload": {"caption": CAPTION, "photos": spec_photos, "remake": True}}],
               ideas=[{"id": 7001, "format": "carousel", "slides": [], "caption": "x", "slide_layout": "photos"}])
cloud_sync.run_jobs(api5, conn, {7001: local})
ok(api5.jobs[0]["state"] == "failed" and "approved" in (api5.jobs[0].get("error") or "")
   and conn.execute("SELECT status FROM productions WHERE id = ?", (new_pid,)).fetchone()[0] == "approved",
   f"an approved draft is never made again ({api5.jobs[0].get('error')})")
rows3, _ = showcase_batch.plan({**spec, "remake": True}, ideas, comments)
ok(rows3[2]["make"] is not None and rows3[2]["make"]["files"] == ["Other 12", "11"], "--remake sends an idea already made too")


# ── Bryan's notes on a photos-only carousel (2026-10-28: "Remove this photo", "use the version with the day curtains
# drawn"): a photo removed or moved, one named in a note swapped in from the same folder, one he describes left open ──
print("== notes on a photos-only carousel")
import llm, types
pids = [os.urandom(16).hex() for _ in range(5)]
for k, p in enumerate(pids):
    Image.new("RGB", (800, 1000), (40 * k, 90, 90)).save(slide_images.PHOTOS / f"{p}.jpg")
ph = [{"key": f"k{k}", "role": "cover" if k == 0 else "point", "heading": "One long room. [check]" if k == 0 else "",
       "body": "", "image": {"source": "photo", "photo_id": p}} for k, p in enumerate(pids)]
prod = {"id": 9900, "title": "Mon 2 Nov · One long room", "version": 1, "caption": CAPTION,
        "plan": json.dumps({"idea_id": local, "layout": "photos", "post_type": "finished"})}
NOTES = [{"id": 1, "slide": 3, "body": "Remove this photo, it's too messy with all the lighting"},
         {"id": 2, "slide": 4, "body": "Use the version of the photo that has the day curtains drawn"},
         {"id": 3, "slide": 2, "body": "Use Other 1 instead"}]
ANSWER = {"slides": [1, 2, 4, 5], "cover": "One long room. [check]", "caption_changed": True,
          "caption": CAPTION.replace("Long table or round?", "Long table or round? Book a free consultation."),
          "needs_photo": [{"note_id": 2, "slide": 4, "want": "the day curtains drawn", "file": ""},
                          {"note_id": 3, "slide": 2, "want": "Other 1", "file": "Other 1"}],
          "summary": "x", "notes_addressed": [{"note_id": 1, "how": "removed slide 3"}, {"note_id": 3, "how": "swapped"}]}
asked_claude = []


def fake_final(client, **req):
    asked_claude.append(req)
    return types.SimpleNamespace(stop_reason="end_turn", model="claude", usage=types.SimpleNamespace(input_tokens=10, output_tokens=10),
                                 content=[types.SimpleNamespace(type="text", text=json.dumps(ANSWER))])


real_final, real_client2, real_folder = llm.final_message, cloud_sync.Client, showcase.folder_for
llm.final_message = fake_final
cloud_sync.Client = lambda: FakeApi()
showcase.folder_for = lambda api, c, idea: FOLDER
rev = carousel.revise(conn, prod, ph, NOTES, client=object())
llm.final_message, cloud_sync.Client, showcase.folder_for = real_final, real_client2, real_folder
ok(len(asked_claude) == 1 and "photos-only" in asked_claude[0]["system"] and "slide 3" in asked_claude[0]["messages"][0]["content"],
   "a photos-only carousel's notes go to its own small revision, not the slide writer")
ok(len(rev["slides"]) == 4 and [s["key"] for s in rev["slides"]] == ["k0", "k1", "k3", "k4"], "the photo he removed is gone, the rest in order")
ok(rev["slides"][1]["image"]["photo_id"] != pids[1] and rev["slides"][1]["image"]["source"] == "photo"
   and Image.open(slide_images.photo_path(rev["slides"][1]["image"]["photo_id"])).convert("RGB").getpixel((5, 5))[2] > 150,
   "the photo his note named (Other 1) is swapped in from the same folder")
ok(rev["slides"][2]["image"]["photo_id"] == pids[3] and rev["keep_open"] == [2] and "day curtains" in rev["summary"]
   and "naming" in rev["summary"], f"the one he described stays open, saying how to name the photo ({rev['summary']})")
ok(rev["slides"][0]["heading"] == "One long room. [check]" and all(not s["heading"] and not s["body"] for s in rev["slides"][1:])
   and rev["caption"] == "", "the cover line and its [check] kept, no words added, the caption's new offer refused (Finished homes)")
ok({a["note_id"] for a in rev["notes_addressed"]} == {1, 3}, "the notes it acted on are addressed; the open one isn't")

# ── Fewer photos, the live words kept (the CTO, 2026-10-29: 22, 24 and 26 rebuilt; Bryan had edited and approved
# captions in Review, so a rebuild must carry them, character for character) ──
print("== made again with the words as they are")
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, post_type, remote_id) VALUES ('Fri 6 Nov · There''s a door in this wall', 'urban_makers',
                      'carousel', 'new', 'go', 'plan', 'plan', '', '[]', '[]', '[]', ?, 'finished', 7010)""", (catalog.now(),))
door = cur.lastrowid
conn.commit()
first_job = {"id": 20, "kind": "carousel", "state": "queued", "idea_id": 7010, "created_at": catalog.now(),
             "payload": {"caption": "Draft caption.\nDid you spot it?", "photos": {"folder": FOLDER, "files": ["GIF 1", "11", "Other 1"],
                                                                                   "cover": "There's a door in this wall."}}}
cloud_sync.run_jobs(FakeApi(jobs=[first_job], ideas=[{"id": 7010, "format": "carousel", "slides": [], "caption": "",
                                                      "slide_layout": "photos"}]), conn, {7010: door})
old_id = conn.execute("SELECT production_id FROM ideas WHERE id = ?", (door,)).fetchone()[0]
EDITED = "Swipe once: the door is part of the fluted wall.\nBehind it: the study [check].\nDid you spot it before you swiped?\n#hiddendoor"
data_ = carousel.current(conn.execute("SELECT * FROM productions WHERE id = ?", (old_id,)).fetchone())
data_["slides"][0]["heading"] = "There's a door in this wall, edited."
conn.execute("UPDATE productions SET status = 'approved', caption = ?, slides = ? WHERE id = ?", (EDITED, json.dumps(data_), old_id))
conn.execute("""INSERT INTO feedback (target, target_id, version, body, status, created_at)
                VALUES ('production', ?, 1, 'Remove photo 8', 'open', ?)""", (old_id, catalog.now()))
conn.commit()
keep_job = lambda jid, approved=True: {"id": jid, "kind": "carousel", "state": "queued", "idea_id": 7010, "created_at": catalog.now(),
                                       "payload": {"caption": "the batch file's old caption", "remake": True, "keep": True,
                                                   **({"include_approved": True} if approved else {}),
                                                   "photos": {"folder": FOLDER, "files": ["GIF 1", "Other 1"], "cover": "batch cover"}}}
a1 = FakeApi(jobs=[keep_job(21, approved=False)], ideas=[{"id": 7010, "format": "carousel", "slides": [], "caption": "", "slide_layout": "photos"}])
cloud_sync.run_jobs(a1, conn, {7010: door})
ok(a1.jobs[0]["state"] == "failed" and "approved" in a1.jobs[0]["error"]
   and conn.execute("SELECT status FROM productions WHERE id = ?", (old_id,)).fetchone()[0] == "approved",
   "an approved draft isn't made again unless asked (--include-approved)")
conn.execute("INSERT INTO posts (production_id, kind, scheduled_at, state, created_at, updated_at) VALUES (?, 'carousel', ?, 'scheduled', ?, ?)", (old_id, catalog.now(), catalog.now(), catalog.now()))
conn.commit()
a2 = FakeApi(jobs=[keep_job(22)], ideas=[{"id": 7010, "format": "carousel", "slides": [], "caption": "", "slide_layout": "photos"}])
cloud_sync.run_jobs(a2, conn, {7010: door})
ok(a2.jobs[0]["state"] == "failed" and "scheduled" in a2.jobs[0]["error"], "a scheduled one never is")
conn.execute("DELETE FROM posts WHERE production_id = ?", (old_id,))
conn.commit()
a3 = FakeApi(jobs=[keep_job(23)], ideas=[{"id": 7010, "format": "carousel", "slides": [], "caption": "", "slide_layout": "photos"}])
cloud_sync.run_jobs(a3, conn, {7010: door})
new_door = conn.execute("SELECT production_id FROM ideas WHERE id = ?", (door,)).fetchone()[0]
nd = conn.execute("SELECT * FROM productions WHERE id = ?", (new_door,)).fetchone()
nplan = json.loads(nd["plan"])
ok(a3.jobs[0]["state"] == "done" and new_door != old_id and nd["status"] in ("planned", "producing", "ready"),
   f"with it, the approved draft is made again; the new one goes to To review, not approved ({nd['status']})")
ok(nd["caption"] == EDITED, "the caption Bryan edited is carried character for character, not the batch file's")
ok(nplan["slides"][0]["heading"] == "There's a door in this wall, edited." and len(nplan["slides"]) == 2
   and all(not x["heading"] for x in nplan["slides"][1:]), "the cover line as it was; only the photos change (two now)")
ok(conn.execute("SELECT COUNT(*) FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open'",
                (old_id,)).fetchone()[0] == 0, "the old draft's open notes are closed when it's archived")

# The batch: --only and --keep-current queue just those, change nothing else.
bapi2 = BatchApi()
bapi2.comments = []
spec2 = ROOT / "keep_spec.json"
spec2.write_text(json.dumps({"project": "Finished homes", "folder": FOLDER, "ideas": [
    {"title": "One long room, one long table", "date": "2026-11-02", "caption": "C1", "photos": ["Other 12", "11"], "cover": "x"},
    {"title": "The living room", "date": "2026-11-04", "photos": ["Other 12", "11"], "cover": "y"},
    {"title": "The aquarium bar", "date": "2026-12-02", "photos": ["Other 12", "11"]}]}))
showcase_batch.cloud_sync.Client = lambda: bapi2
_sys.argv = ["showcase_batch.py", str(spec2), "--remake", "--keep-current", "--include-approved", "--only", "one long room",
             "--only", "living room", "--apply", "--no-photo-check"]
code4 = showcase_batch.main()
_sys.argv, showcase_batch.cloud_sync.Client = argv, real_client
jobs = [c[2] for c in bapi2.calls if c[0] == "insert" and c[1] == "jobs"]
ok(code4 == 0 and len(jobs) == 2 and {j["idea_id"] for j in jobs} == {24, 25}
   and all(j["payload"].get("keep") and j["payload"].get("include_approved") and j["payload"].get("remake") for j in jobs)
   and not [c for c in bapi2.calls if c[0] == "update" and c[1] == "ideas"] and not [c for c in bapi2.calls if c[1] == "comments"],
   "--only and --keep-current: just those two sent, keeping their words; no idea, date, title, caption or grid note touched")

# ── No slideshow Reel from a photos-only carousel (the CTO: its photos landed inside a green frame) ──
print("== no slideshow from photos")
import auth, dashboard
owner_id = auth.create_user(conn, "bryan@x.com", "Bryan", "correct horse battery", auth.OWNER)
web = dashboard.app.test_client()
with web.session_transaction() as ss:
    ss["uid"] = owner_id; ss["sv"] = 1
conn.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (new_door,))
conn.commit()
det = web.get(f"/api/production/{new_door}").json
ok(det is not None and det.get("slideshow") is None, "Review doesn't offer Make a slideshow Reel on a photos-only carousel")
r = web.post(f"/api/production/{new_door}/slideshow", json={})
ok(r.status_code == 400 and "photos-only" in r.json["error"], f"and refuses it if asked ({r.json})")

finish()
