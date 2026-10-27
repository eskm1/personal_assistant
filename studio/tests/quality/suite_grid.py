"""The profile grid (Bryan, via the content lead, 2026-10-27: "see and plan the whole @urbanmakers_sg profile before
anything posts"), the Studio machine's side: photos-only carousels (each photo its slide, 4:5 never cropped or made
bigger, the cover line laid out by the real Chrome), a Reel's "in the profile grid" flag carried into scheduling
(Instagram's share_to_feed) and followed by a scheduled post, the planner's posting date as the Schedule dialog's
suggestion, the account's posts kept with a small still and their is_shared_to_feed, the profile header (the
account's, else settings), the planner's copy (stills uploaded once, new columns only once the migration is online),
the local mirror of the new idea columns, and Instagram refusing the new fields (asked again without them).
Instagram, Supabase and the CDN are local fakes; nothing leaves the machine.
Run: python tests/quality/suite_grid.py (from studio/)"""
import io, json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ROOT, ok, finish
os.environ.update(STUDIO_INSTAGRAM_HANDLE="urbanmakers_sg", STUDIO_INSTAGRAM_NAME="Urban Makers",
                  STUDIO_INSTAGRAM_BIO="Renovation, start to finish.\\nSingapore", STUDIO_POST_TIME="19:30")
from PIL import Image
import settings, catalog, carousel, posting, post_results, instagram, cloud_sync, slide_images
from instagram import IgError

settings.ensure_folders()
conn = catalog.connect()
SGT = timezone(timedelta(hours=8))
now = datetime.now(timezone.utc)


def photo(w, h, colour=(180, 120, 90)):
    """A photo kept the way uploads.py keeps one (Photos/<id>.jpg), with a stripe so a crop shows."""
    pid = os.urandom(16).hex()
    img = Image.new("RGB", (w, h), colour)
    for x in range(0, w, max(1, w // 8)):
        img.paste((40, 40, 40), (x, 0, x + 4, h))
    slide_images.PHOTOS.mkdir(parents=True, exist_ok=True)
    img.save(slide_images.PHOTOS / f"{pid}.jpg", "JPEG", quality=92)
    return pid


def slide(pid=None, heading="", role="point"):
    image = {"source": "photo", "photo_id": pid} if pid else {"source": "text"}
    return {"key": os.urandom(4).hex(), "role": role, "heading": heading, "body": "", "image": image}


# ── Photos only: each photo is its slide ──────────────────────────────────────

print("== photos-only carousels")
ok(slide_images.PHOTO_MAX >= 2700, f"a 2160x2700 photo is kept at its size when uploaded (PHOTO_MAX {slide_images.PHOTO_MAX})")
cleaned = carousel.clean_slides([slide(photo(400, 500)), {"role": "point", "heading": "", "body": "", "image": {"source": "text"}},
                                 slide(None, "Words only")])
ok(len(cleaned) == 2 and cleaned[0]["image"]["source"] == "photo" and cleaned[1]["heading"] == "Words only",
   "a slide with a photo and no words is kept; one with neither is dropped")

out = ROOT / "t_photos"
for name, (w, h), want, cropped, scaled in (("as_is", (2160, 2700), (2160, 2700), False, False),
                                            ("tall_919", (1080, 1920), (1080, 1350), True, False),
                                            ("wide", (1600, 1000), (800, 1000), True, False),
                                            ("huge", (3000, 3750), (2160, 2700), False, True),
                                            ("small", (800, 1000), (800, 1000), False, False)):
    src = slide_images.PHOTOS / f"{photo(w, h)}.jpg"
    info = carousel.photo_slide(src, out / f"{name}.jpg")
    with Image.open(out / f"{name}.jpg") as im:
        size, fmt_ = im.size, im.format
    ok(size == want and info["cropped"] == cropped and info["scaled"] == scaled and fmt_ == "JPEG",
       f"{w}x{h} -> {size[0]}x{size[1]} {fmt_} (cropped {info['cropped']}, smaller {info['scaled']})")

pids = [photo(2160, 2700, (200, 60 + 40 * k, 60)) for k in range(3)]
made = carousel.lay_out(conn, [slide(p, role="cover" if k == 0 else "point") for k, p in enumerate(pids)], ROOT / "t_lay1",
                        layout=carousel.PHOTOS)
sizes = [Image.open(f).size for f in made["files"]]
ok([Path(f).suffix for f in made["files"]] == [".jpg"] * 3 and sizes == [(2160, 2700)] * 3,
   f"no cover line: three JPEGs at the photos' own 2160x2700 ({sizes})")
ok([x["layout"] for x in made["layout"]] == ["photo-full"] * 3 and [x["source"] for x in made["pictures"]] == ["photo"] * 3,
   "each slide reported as a full photo")
first = Image.open(made["files"][0]).convert("RGB")
ok(first.getpixel((1100, 1350))[0] > 150, "the photo isn't covered by words or a shade")

made2 = carousel.lay_out(conn, [slide(pids[0], "Kitchen, start to finish", "cover"), slide(pids[1]), slide(None, "The ask, in words")],
                         ROOT / "t_lay2", layout=carousel.PHOTOS)
names = [Path(f).name for f in made2["files"]]
ok(names == ["01.png", "02.jpg", "03.png"], f"cover line on slide 1 (Chrome, PNG), slide 2 the photo, a slide without a picture in words ({names})")
ok(Image.open(made2["files"][0]).size == (1080, 1350) and not (ROOT / "t_lay2" / "01.jpg").exists(),
   "the cover is the slides' 1080x1350, and the plain 01.jpg is gone")
cov = made2["layout"][0]
ok(cov["layout"] == "photo-cover-line" and not cov["overflow"] and cov["boxes"]["heading"]
   and carousel.GRID_LEFT <= cov["boxes"]["heading"][0] and cov["boxes"]["heading"][2] <= carousel.GRID_RIGHT,
   f"the cover line fits, inside the profile grid's 3:4 crop ({cov['boxes']['heading']})")
px = Image.open(made2["files"][0]).convert("RGB")
ok(px.getpixel((560, 300))[0] > 150, "above the line the photo shows through")
ok(Image.open(made2["files"][2]).size == (1080, 1350), "the words slide is laid out as usual")

# make() carries the layout from the idea (the planner's slide_layout, mirrored) into the plan.
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, slide_layout, remote_id) VALUES ('Photos only', 'urban_makers', 'carousel', 'new', 'go', 'plan', 'plan', '',
                      '[]', '[]', '[]', ?, 'photos', 9001)""", (catalog.now(),))
idea_id = cur.lastrowid
conn.commit()
pid_prod = carousel.make(conn, idea_id, [slide(p) for p in pids])
row = conn.execute("SELECT * FROM productions WHERE id = ?", (pid_prod,)).fetchone()
ok(carousel.layout_of(row) == "photos" and json.loads(row["plan"])["layout"] == "photos", "Make this keeps 'photos' in the plan")
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at) VALUES ('Words', 'urban_makers', 'carousel', 'new', 'go', 'plan', 'plan', '', '[]', '[]', '[]', ?)""",
                   (catalog.now(),))
conn.commit()
row2 = conn.execute("SELECT * FROM productions WHERE id = ?", (carousel.make(conn, cur.lastrowid, [slide(None, "One", "cover"), slide(None, "Two")]),)).fetchone()
ok(carousel.layout_of(row2) is None, "a words carousel has no layout key (laid out as before)")

# ── The grid flag: share_to_feed ──────────────────────────────────────────────

print("== in the profile grid (share_to_feed)")
stamp = catalog.now()
reel_mp4 = ROOT / "reel.mp4"
reel_mp4.write_bytes(b"x")


def draft(title, fmt, remote, in_grid=None, post_on=None):
    c = conn.execute("""INSERT INTO productions (title, brand, format, status, version, video_path, duration, caption, created_at, updated_at)
                        VALUES (?, 'urban_makers', ?, 'approved', 1, ?, 20, 'A caption', ?, ?)""",
                     (title, fmt, str(reel_mp4), stamp, stamp))
    conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                    created_at, production_id, remote_id, in_grid, post_on) VALUES (?, 'urban_makers', ?, 'made', 'go', 'plan', 'plan', '',
                    '[]', '[]', '[]', ?, ?, ?, ?, ?)""", (title, fmt, stamp, c.lastrowid, remote, in_grid, post_on))
    conn.commit()
    return c.lastrowid


out_reel = draft("Reels tab only", "talking_head", 101, in_grid=0)
in_reel = draft("In the grid", "talking_head", 102, in_grid=1)
unknown = draft("Before the update", "talking_head", 103, in_grid=None)
ok(posting.grid_default(conn, out_reel) is False and posting.grid_default(conn, in_reel) is True and posting.grid_default(conn, unknown) is True,
   "the idea's flag is the default; unknown (before the update) is in the grid")
when = (now + timedelta(days=3)).isoformat()
p_out = posting.schedule(conn, out_reel, when, by="test")
p_in = posting.schedule(conn, in_reel, when, by="test")
ok(p_out["share_to_feed"] is False and p_in["share_to_feed"] is True, "Schedule without a choice follows the idea's flag")
p_un = posting.schedule(conn, unknown, when, share_to_feed=False, by="test")
ok(p_un["share_to_feed"] is False, "a choice in the Schedule dialog wins")

# The planner's flag changes while it's scheduled: the post follows (only while scheduled, only Reels).
conn.execute("UPDATE ideas SET in_grid = 1 WHERE remote_id = 101")
conn.execute("UPDATE ideas SET in_grid = 0 WHERE remote_id = 102")
conn.commit()
n = posting.follow_grid(conn)
rows = {r["id"]: r["share_to_feed"] for r in conn.execute("SELECT id, share_to_feed FROM posts")}
ok(n == 2 and rows[p_out["id"]] == 1 and rows[p_in["id"]] == 0, f"a scheduled Reel follows its idea's flag ({n} changed)")
conn.execute("UPDATE posts SET state = 'publishing' WHERE id = ?", (p_in["id"],))
conn.execute("UPDATE ideas SET in_grid = 1 WHERE remote_id = 102")
conn.commit()
posting.follow_grid(conn)
ok(conn.execute("SELECT share_to_feed FROM posts WHERE id = ?", (p_in["id"],)).fetchone()[0] == 0,
   "once it's going out, it isn't changed")
posting.set_grid_flag(conn, 103, True)
ok(conn.execute("SELECT in_grid FROM ideas WHERE remote_id = 103").fetchone()[0] == 1,
   "the Schedule dialog's choice is written to the idea too (here; the planner when it's online)")

# The posting date as the Schedule dialog's suggestion (a scheduled post's own time wins once it's scheduled).
day = (now.astimezone(SGT) + timedelta(days=9)).date().isoformat()
planned = draft("Planned for a day", "talking_head", 104, post_on=day)
plan = posting.plan_for(conn, planned)
at = datetime.fromisoformat(plan["suggested"]).astimezone(SGT)
ok(plan["planned_on"] == day and at.date().isoformat() == day and (at.hour, at.minute) == (19, 30),
   f"the planner's posting date is the suggested slot, at the usual time ({plan['suggested_text']})")
past = draft("A date gone by", "talking_head", 105, post_on=(now.astimezone(SGT) - timedelta(days=2)).date().isoformat())
plan2 = posting.plan_for(conn, past)
ok(datetime.fromisoformat(plan2["suggested"]) > now, "a date that has passed isn't suggested")
ok(plan["in_grid"] is True and posting.plan_for(conn, out_reel)["in_grid"] is True, "the dialog gets the idea's flag")

# ── The account's posts: a small still, in the grid or not ────────────────────

print("== the account's posts and profile")
PICS = {}


def jpeg(w, h, colour):
    b = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(b, "JPEG")
    return b.getvalue()


PICS["https://cdn.example/reel_cover.jpg"] = jpeg(1080, 1920, (10, 200, 10))
PICS["https://cdn.example/reel_video.mp4"] = b"not a picture"
PICS["https://cdn.example/album.jpg"] = jpeg(1440, 1800, (200, 10, 10))
PICS["https://cdn.example/avatar.jpg"] = jpeg(320, 320, (20, 20, 200))
fetched = []


def fake_download(url):
    fetched.append(url)
    return PICS.get(url)


post_results._download = fake_download
MEDIA = [
    {"id": "17900000000000001", "media_type": "VIDEO", "media_product_type": "REELS", "caption": "A Reel off the grid #reno",
     "permalink": "https://www.instagram.com/reel/AAA/", "timestamp": (now - timedelta(days=2)).isoformat(),
     "media_url": "https://cdn.example/reel_video.mp4", "thumbnail_url": "https://cdn.example/reel_cover.jpg", "is_shared_to_feed": False},
    {"id": "17900000000000002", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED", "caption": "C" * 900,
     "permalink": "https://www.instagram.com/p/BBB/", "timestamp": (now - timedelta(days=5)).isoformat(),
     "media_url": "https://cdn.example/album.jpg"},
    {"id": "../etc/passwd", "media_type": "IMAGE", "media_url": "https://cdn.example/album.jpg", "timestamp": now.isoformat()},
]
for m in MEDIA:
    post_results._save_media(conn, m)
conn.commit()
r1 = conn.execute("SELECT * FROM ig_media WHERE media_id = '17900000000000001'").fetchone()
r2 = conn.execute("SELECT * FROM ig_media WHERE media_id = '17900000000000002'").fetchone()
t1 = settings.IG_THUMBS / "17900000000000001.jpg"
ok(r1["thumb"] == t1.name and Image.open(t1).size == (540, 960) and Image.open(t1).getpixel((10, 10))[1] > 150,
   "a Reel's still is its cover (thumbnail_url, not the video), 540 wide")
ok(r1["in_grid"] == 0 and r2["in_grid"] is None, "is_shared_to_feed kept for a Reel (false = Reels tab only); none for a carousel")
ok(len(r2["full_caption"]) == 900 and len(r2["caption"]) == 500, "the whole caption is kept for the grid's panel")
ok(not list(settings.IG_THUMBS.glob("*passwd*")) and not (ROOT / "etc").exists(), "a strange media id is never a file name")
before = len(fetched)
post_results._save_media(conn, MEDIA[0])
ok(len(fetched) == before, "a still is fetched once")
small = Image.open(io.BytesIO(post_results.small_jpeg(jpeg(300, 400, (1, 2, 3)))))
ok(small.size == (300, 400), "a small picture is never made bigger")

# Instagram refusing the grid's fields: the plain fields, and no stills (the read still works).
calls = []


def fake_call(method, path, params=None, **kw):
    calls.append(params.get("fields", ""))
    if "is_shared_to_feed" in params.get("fields", ""):
        raise IgError("(#100) Tried accessing nonexisting field (is_shared_to_feed)", "other", code=100)
    return {"data": [{"id": "1", "timestamp": now.isoformat()}], "paging": {}}


real_call, real_id = instagram.call, instagram._ig_id
instagram.call, instagram._ig_id = fake_call, lambda data=None: "17841400000000001"
got = instagram.recent_media(limit=5)
ok(len(got) == 1 and "is_shared_to_feed" in calls[0] and "media_url" not in calls[-1] and not instagram._grid_fields["on"],
   "a refused field: asked again with the plain fields, and not asked for again")
instagram._grid_fields["on"] = True


def profile_call(method, path, params=None, **kw):
    calls.append(params["fields"])
    if "biography" in params["fields"]:
        raise IgError("(#100) Tried accessing nonexisting field (biography)", "other", code=100)
    return {"user_id": "1", "username": "urbanmakers_sg", "name": "Urban Makers", "followers_count": 1234,
            "follows_count": 56, "media_count": 78, "profile_picture_url": "https://cdn.example/avatar.jpg"}


instagram.call = profile_call
prof = post_results.read_profile(now)
ok(prof and prof["followers"] == 1234 and prof["biography"] == "" and prof.get("avatar"), "the profile without the refused bio, its picture kept")
ok("access_token" not in json.dumps(prof) and (settings.IG_THUMBS / "avatar.jpg").is_file(), "nothing secret in the saved profile")
instagram.call, instagram._ig_id = real_call, real_id

# The header: the account's when it's connected, else settings.
real_connected = instagram.connected
instagram.connected = lambda: False
info = post_results.profile_info()
ok(info["source"] == "settings" and info["username"] == "urbanmakers_sg" and info["biography"] == "Renovation, start to finish.\nSingapore",
   "not connected: the header from settings (handle, name, bio)")
instagram.connected = lambda: True
info = post_results.profile_info()
ok(info["source"] == "account" and info["followers"] == 1234 and "avatar_path" not in info,
   "connected: the account's own, the picture only once it's in the bucket")

# ── The planner's copy ────────────────────────────────────────────────────────


class FakeApi:
    def __init__(self):
        self.rows, self.uploads = {}, []

    def insert(self, table, values, **kw):
        self.rows.setdefault(table, []).append(dict(values))

    def upload(self, path, content, content_type="image/jpeg"):
        self.uploads.append(path)


for m in MEDIA[:2]:
    conn.execute("INSERT INTO post_results (media_id, day, views, pulled_at) VALUES (?, ?, 10, ?)", (m["id"], "2026-10-27", catalog.now()))
conn.commit()
api = FakeApi()
post_results.mirror(api, conn, grid=False)
sent = {r["media_id"]: r for r in api.rows.get("post_results", [])}
ok(sent and all("thumb_path" not in r and "in_grid" not in r and "caption" not in r for r in sent.values()) and not api.uploads,
   "before the grid's update: the same columns as before, nothing uploaded")
api = FakeApi()
post_results.mirror(api, conn, grid=True)
sent = {r["media_id"]: r for r in api.rows.get("post_results", [])}
ok(sent["17900000000000001"]["thumb_path"] == "results/17900000000000001.jpg" and sent["17900000000000001"]["in_grid"] is False
   and sent["17900000000000002"]["in_grid"] is None and len(sent["17900000000000002"]["caption"]) == 900,
   "after it: each post's still path, in the grid or not, the whole caption")
ok(sorted(api.uploads) == sorted(["results/17900000000000001.jpg", "results/17900000000000002.jpg", f"profile/{prof['avatar']}.jpg"]),
   f"the stills and the profile picture uploaded ({len(api.uploads)})")
api2 = FakeApi()
post_results.mirror(api2, conn, grid=True)
ok(not api2.uploads and not api2.rows.get("post_results"), "nothing again when nothing changed")
info = post_results.profile_info()
ok(info.get("avatar_path") == f"profile/{prof['avatar']}.jpg", "the header's picture once it's uploaded")
# An account that hasn't posted for 90 days still has a grid: its newest posts, however old, read once as soon as
# Instagram is connected (not at tomorrow's daily read); a post Instagram gives no numbers for still shows, bare.
OLD = [{"id": f"1790000000000010{i}", "media_type": "IMAGE", "media_product_type": "FEED", "caption": f"Old post {i}",
        "permalink": f"https://www.instagram.com/p/OLD{i}/", "timestamp": (now - timedelta(days=200 + i)).isoformat(),
        "media_url": "https://cdn.example/album.jpg"} for i in range(3)]
asked = {"media": [], "insights": [], "profile": 0}


def fake_recent(since=None, limit=200):
    asked["media"].append((since, limit))
    return [m for m in OLD if since is None or datetime.fromisoformat(m["timestamp"]) >= since][:limit]


def fake_insights(media_id, metrics):
    asked["insights"].append(media_id)
    if media_id.endswith("102"):
        raise IgError("(#10) Not enough viewers for the media to show insights", "other", code=10)
    return {"views": 50, "reach": 40, "likes": 5}


def fake_profile():
    asked["profile"] += 1
    return {"username": "urbanmakers_sg", "name": "Urban Makers", "biography": "Renovation", "followers_count": 1137,
            "follows_count": 56, "media_count": 78}


real = (instagram.recent_media, instagram.media_insights, instagram.profile)
instagram.recent_media, instagram.media_insights, instagram.profile = fake_recent, fake_insights, fake_profile
post_results._profile_file().unlink(missing_ok=True)
ok(post_results.profile_info()["source"] == "reading", "connected, the account not read yet: the header says reading, not connect")
post_results._load_state()
post_results._state.update(daily=post_results._now().astimezone(post_results.SGT).date().isoformat(), grid_read=None, grid_tried=None)
post_results._save_state()   # tick reads the state from its file
post_results.tick(conn)
ok(asked["profile"] == 1 and asked["media"] == [(None, post_results.GRID_POSTS)], "the first grid read happens at once, not tomorrow")
ok(sorted(asked["insights"]) == sorted(m["id"] for m in OLD), "each old post's numbers read once")
ok(post_results.profile_info()["source"] == "account", "then the header is the account's")
post_results.tick(conn)
ok(asked["profile"] == 1 and len(asked["insights"]) == 3, "and not again on the next tick")
api3 = FakeApi()
post_results.mirror(api3, conn, grid=True)
sent = {r["media_id"]: r for r in api3.rows.get("post_results", [])}
ok(sent.get("17900000000000100", {}).get("views") == 50 and sent.get("17900000000000102", {}).get("views") is None
   and sent.get("17900000000000102", {}).get("thumb_path") and sent["17900000000000102"]["pulled_at"],
   "old posts reach the grid, one with no numbers among them")
api5 = FakeApi()
post_results.mirror(api5, conn, grid=True)
ok(not api5.rows.get("post_results"), "nothing again when nothing changed, bare posts included")
api4 = FakeApi()
post_results.mirror(api4, conn, grid=False)
ok("17900000000000102" not in {r["media_id"] for r in api4.rows.get("post_results", [])}, "before the grid's update, a post with no numbers isn't sent")
counts = post_results.read_grid(conn)
ok(counts["older"] == 0 and asked["insights"].count("17900000000000102") == 2 and asked["insights"].count("17900000000000101") == 1,
   "the next read asks again only for the one with no numbers")
instagram.recent_media, instagram.media_insights, instagram.profile = real
pi = posting.info()
ok(pi["carousel_max"] == settings.INSTAGRAM_CAROUSEL_MAX and pi["profile"]["username"] == "urbanmakers_sg" and "access_token" not in json.dumps(pi),
   "the heartbeat carries the carousel limit and the profile, never a token")
instagram.connected = real_connected

# ── The local mirror of the ideas' new columns ────────────────────────────────

print("== the local mirror")


class PullApi:
    def __init__(self, ideas):
        self.data = {"projects": [{"id": 1, "name": "Demo", "brand": "urban_makers", "folder": "Demo", "brief": "", "status": "active"}],
                     "arcs": [], "ideas": ideas, "shots": [], "takes": [], "clips": []}

    def select(self, table, params=None):
        return self.data[table]


def remote_idea(i, **extra):
    base = {"id": i, "project_id": 1, "arc_id": None, "title": f"Idea {i}", "brand": "urban_makers", "format": "carousel", "hook": "",
            "purpose": "", "cta": "", "caption": "", "est_seconds": 30, "music": {}, "decision": "go", "status": "new", "position": i,
            "updated_at": catalog.now(), "folder": "", "post_type": "finished", "slides": None}
    return {**base, **extra}


cloud_sync.pull(PullApi([remote_idea(501, post_on="2026-11-02", in_grid=False, slide_layout="photos"), remote_idea(502)]), conn)
a = conn.execute("SELECT post_on, in_grid, slide_layout FROM ideas WHERE remote_id = 501").fetchone()
b = conn.execute("SELECT post_on, in_grid, slide_layout FROM ideas WHERE remote_id = 502").fetchone()
ok(tuple(a) == ("2026-11-02", 0, "photos"), f"posting date, grid flag and layout mirrored ({tuple(a)})")
ok(tuple(b) == (None, None, None), "before the update (no such columns online): nothing, and nothing breaks")

finish()
