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
_, problems = showcase_batch.plan({"project": "x", "folder": FOLDER, "ideas": [{"title": "room", "date": "2026-11-02"}]}, ideas, {})
ok(problems and "3 ideas match" in problems[0], f"a title matching several ideas stops it ({problems})")
_, problems = showcase_batch.plan({"project": "x", "ideas": [{"title": "Kitchen", "date": "2026-11-02"},
                                                             {"title": "Kitchen and bathroom", "date": "2026-11-03"}]}, ideas, {})
ok(any("both pick" in p for p in problems), "two entries picking one idea stops it")
_, problems = showcase_batch.plan({"project": "x", "ideas": [{"title": "Kitchen", "date": "2 Nov"}]}, ideas, {})
ok(any("YYYY-MM-DD" in p for p in problems), "a date that isn't a date stops it")

finish()
