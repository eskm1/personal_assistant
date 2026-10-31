"""Colour grades for a carousel's photos (photo_grade.py; the team, 2026-10-31: the long condo's photos warmer, like the
interior photographers they follow, picked per photo side by side): each look warms as measured (cream whites, warmer
mid-tones; the dramatic ones darker with the windows kept bright, less warmth on a photo already warm), the original
untouched; a pick queues the next version (carousel.regrade) and the run grades each photo from the photo it was
first given (never a grade on a grade), keeps the cover line, caption and order, leaves the open notes open and asks
Claude nothing; a scheduled draft, a draft being made, a slide that isn't a photo or a look Studio doesn't have is
refused; the team may pick too (POST /api/production/<id>/grade).
Run: python tests/quality/suite_grade.py (from studio/)"""
import json
from pathlib import Path

from common import ROOT, ok, finish
import numpy as np
from PIL import Image
import settings, catalog, carousel, concepts, photo_grade, self_fix, slide_images

settings.ensure_folders()
conn = catalog.connect()
lab = photo_grade.rgb_to_lab


def flat(rgb, size=(400, 500)):
    return np.full((size[1], size[0], 3), np.array(rgb, np.float32) / 255, np.float32)


def mean_lab(img):
    return lab(img).reshape(-1, 3).mean(0)


# ── The looks ─────────────────────────────────────────────────────────────────
print("== the looks")
grey, white = flat((150, 150, 150)), flat((255, 255, 255))
ok(np.array_equal(photo_grade.grade(grey, "original"), grey), "original leaves the photo as it is")
for look in ("soft", "golden", "marcus", "dramatic", "deeper"):
    g = mean_lab(photo_grade.grade(grey, look))
    ok(g[2] > 5 and g[1] >= 0, f"{look}: a neutral grey comes out warm (b* {g[2]:.1f}, a* {g[1]:.1f})")
w = {look: mean_lab(photo_grade.grade(white, look)) for look in ("soft", "marcus", "deeper")}
ok(90 < w["marcus"][0] < 95 and 3 < w["marcus"][2] < 7, f"marcus: pure white becomes cream, as in his photos (L {w['marcus'][0]:.1f}, b* {w['marcus'][2]:.1f})")
ok(w["soft"][0] > w["marcus"][0] > w["deeper"][0], "soft keeps the most light, marcus less, deeper least")
mid = {look: mean_lab(photo_grade.grade(grey, look))[0] for look in ("marcus", "dramatic", "deeper")}
ok(mid["marcus"] > mid["dramatic"] > mid["deeper"], f"the dramatic looks darken the room more ({mid})")

# A room with a bright window: the window stays bright under the dramatic look while the room goes deeper.
room = flat((160, 150, 140), (600, 750))
room[200:500, 200:400] = 1.0
d = photo_grade.grade(room, "dramatic")
win, wall = lab(d[300:400, 250:350]).reshape(-1, 3).mean(0)[0], lab(d[600:700, 50:150]).reshape(-1, 3).mean(0)[0]
ok(win - wall > lab(room[300:400, 250:350]).reshape(-1, 3).mean(0)[0] - lab(room[600:700, 50:150]).reshape(-1, 3).mean(0)[0],
   f"dramatic: more light between the window and the room than before ({win:.0f} vs {wall:.0f})")
corner, centre = lab(photo_grade.grade(flat((150, 150, 150), (600, 750)), "dramatic"))[[5, 370], [5, 300], 0]
ok(corner < centre - 2, f"the vignette darkens the corners ({corner:.1f} vs {centre:.1f} at the centre)")

# A photo already warm from its lights gets less warmth under the dramatic looks (it would turn orange).
warm = flat((215, 170, 120))
added_warm = mean_lab(photo_grade.grade(warm, "dramatic"))[2] - mean_lab(warm)[2]
added_grey = mean_lab(photo_grade.grade(grey, "dramatic"))[2] - mean_lab(grey)[2]
ok(added_warm < added_grey, f"a warm-lit photo gets less extra warmth ({added_warm:.1f} vs {added_grey:.1f})")

# ── grade_file: a new photo, the one it came from untouched ───────────────────
print("== a graded photo is a new photo")
pids = []
for k, colour in enumerate([(150, 150, 150), (120, 130, 140), (200, 190, 180)]):
    p = ROOT / f"in{k}.jpg"
    Image.new("RGB", (800, 1000), colour).save(p)
    pids.append(slide_images.save_photo(p)["id"])
before = slide_images.photo_path(pids[0]).read_bytes()
made = photo_grade.grade_file(slide_images.photo_path(pids[0]), "marcus")
ok(made["id"] != pids[0] and made["path"].exists() and slide_images.photo_path(pids[0]).read_bytes() == before,
   "grade_file keeps the graded photo as a new Studio photo; the original stays byte for byte")
gp = np.asarray(Image.open(made["path"]).convert("RGB"), np.float32).mean((0, 1))
ok(gp[0] > gp[2] + 8 and (made["width"], made["height"]) == (800, 1000), f"warmer and the same size ({gp.round(1)})")
try:
    photo_grade.grade_file(slide_images.photo_path(pids[0]), "teal")
    ok(False, "an unknown look is refused")
except ValueError:
    ok(True, "an unknown look is refused")

# ── A carousel: pick, queue, run ──────────────────────────────────────────────
print("== picking grades on a carousel")
cur = conn.execute("""INSERT INTO ideas (title, brand, format, status, decision, origin, batch, angle, beats, segments, wishlist,
                      created_at, post_type) VALUES ('Wed 4 Nov · The living room', 'urban_makers', 'carousel', 'made',
                      'go', 'plan', 'plan', '', '[]', '[]', '[]', ?, 'finished')""", (catalog.now(),))
idea = cur.lastrowid
slides = [{"key": f"k{k}", "role": "cover" if k == 0 else "point", "heading": "Soft colours, warm light, nothing loud." if k == 0 else "",
           "body": "", "image": {"source": "photo", "photo_id": p, "thumb_path": f"photos/{p}.jpg"}} for k, p in enumerate(pids)]
slides.append({"key": "k9", "role": "point", "heading": "A words slide", "body": "", "image": {"source": "text"}})
CAPTION = "Soft colours, warm light.\nWhich would you keep?"
cur = conn.execute("""INSERT INTO productions (title, format, status, version, caption, plan, slides, brand,
                      created_at, updated_at) VALUES ('Wed 4 Nov · The living room', 'carousel', 'approved', 1, ?, ?, ?,
                      'urban_makers', ?, ?)""",
                   (CAPTION, json.dumps({"idea_id": idea, "layout": "photos", "post_type": "finished", "slides": slides}),
                    json.dumps({"version": 1, "slides": slides, "files": []}), catalog.now(), catalog.now()))
pid = cur.lastrowid
conn.execute("UPDATE ideas SET production_id = ? WHERE id = ?", (pid, idea))
conn.execute("""INSERT INTO feedback (target, target_id, version, body, status, created_at)
                VALUES ('production', ?, 1, 'Use the photo with the curtains drawn', 'open', ?)""", (pid, catalog.now()))
conn.commit()

changed = carousel.regrade(conn, pid, {"1": "marcus", "2": "original", 3: "dramatic"})
row = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
ok(changed == [1, 3] and row["status"] == "planned", f"slides 1 and 3 change (2 is already the original); queued as the next version ({changed}, {row['status']})")
ok(carousel.current(row)["regrade"] == {"1": "marcus", "3": "dramatic"}, "the picks wait on the draft for the run")
for bad, why in [({"4": "marcus"}, "isn't a photo"), ({"9": "marcus"}, "isn't a photo"), ({"1": "teal"}, "no look"),
                 ({"x": "marcus"}, "number")]:
    conn.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (pid,))
    try:
        carousel.regrade(conn, pid, bad)
        ok(False, f"{bad} is refused")
    except ValueError as e:
        ok(why in str(e), f"{bad} is refused ({e})")
conn.execute("UPDATE productions SET status = 'producing' WHERE id = ?", (pid,))
try:
    carousel.regrade(conn, pid, {"1": "golden"})
    ok(False, "a draft being made is refused")
except ValueError as e:
    ok("producing" in str(e), f"a draft being made is refused ({e})")
conn.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (pid,))
conn.execute("INSERT INTO posts (production_id, kind, scheduled_at, state, created_at, updated_at) VALUES (?, 'carousel', ?, 'scheduled', ?, ?)",
             (pid, catalog.now(), catalog.now(), catalog.now()))
conn.commit()
try:
    carousel.regrade(conn, pid, {"1": "golden"})
    ok(False, "a scheduled one is refused")
except ValueError as e:
    ok("scheduled" in str(e), f"a scheduled one is refused: its photos stay ({e})")
conn.execute("DELETE FROM posts WHERE production_id = ?", (pid,))
conn.execute("UPDATE productions SET status = 'planned' WHERE id = ?", (pid,))
conn.commit()

print("== the run")
laid, asked = [], []
real = carousel.lay_out, self_fix.deliver, concepts.prepare, carousel.revise


def fake_lay_out(conn_, slides_, out_dir, reel=False, layout=None, kit=None):
    laid.append(json.loads(json.dumps(slides_)))
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for n, s in enumerate(slides_, 1):
        f = out_dir / f"{n:02d}.png"
        Image.new("RGB", (108, 135), (90, 90, 90)).save(f)
        files.append(str(f))
    return {"files": files, "pictures": [{"n": n, "source": s["image"]["source"]} for n, s in enumerate(slides_, 1)]}


def fake_deliver(conn_, production_id, client=None):
    conn_.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (production_id,))
    conn_.commit()
    return conn_.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()


carousel.lay_out, self_fix.deliver = fake_lay_out, fake_deliver
concepts.prepare = lambda *a, **k: {"drawn": 0, "usd": 0}
carousel.revise = lambda *a, **k: asked.append(a) or {"slides": a[2]}
carousel.run(conn, pid)
row = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
data = carousel.current(row)
s1, s2, s3 = (data["slides"][i]["image"] for i in range(3))
ok(row["version"] == 2 and row["status"] == "ready" and not asked, "the next version, back in To review, with nothing asked of Claude")
ok(s1["grade"] == "marcus" and s1["graded_from"] == pids[0] and s1["photo_id"] != pids[0]
   and s3["grade"] == "dramatic" and s3["graded_from"] == pids[2] and s2 == slides[1]["image"],
   "slides 1 and 3 graded from their own photos; slide 2 as it was")
ok([s["heading"] for s in data["slides"]] == [s["heading"] for s in slides] and row["caption"] == CAPTION
   and data["slides"][3]["image"] == {"source": "text"}, "the cover line, the words slide, the order and the caption as they were")
ok("regrade" not in data, "the picks are used up once made")
ok(conn.execute("SELECT COUNT(*) FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open'",
                (pid,)).fetchone()[0] == 1, "the open note stays open (a grade isn't an answer to it)")

# Changed again: from the first photo, never a grade on a grade; back to the original undoes it.
carousel.regrade(conn, pid, {"1": "deeper", "3": "original"})
carousel.run(conn, pid)
data = carousel.current(conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone())
s1, s3 = data["slides"][0]["image"], data["slides"][2]["image"]
ok(s1["grade"] == "deeper" and s1["graded_from"] == pids[0], "a second pick grades the first photo again, not the graded one")
ok(s3.get("photo_id") == pids[2] and "grade" not in s3 and "graded_from" not in s3, "Original puts the photo back as it came")
ok(carousel.regrade(conn, pid, {"1": "deeper"}) == [], "picking the look it already has changes nothing")
carousel.lay_out, self_fix.deliver, concepts.prepare, carousel.revise = real

# ── The endpoint ──────────────────────────────────────────────────────────────
print("== the endpoint")
import auth, dashboard
team_id = auth.create_user(conn, "michelle@theurbanmakers.com", "Michelle", "correct horse battery", auth.TEAM)
web = dashboard.app.test_client()
with web.session_transaction() as ss:
    ss["uid"] = team_id; ss["sv"] = 1
conn.execute("UPDATE productions SET status = 'ready' WHERE id = ?", (pid,))
conn.commit()
r = web.post(f"/api/production/{pid}/grade", json={"looks": {"2": "golden"}})
ok(r.status_code == 200 and r.json["changed"] == [2], f"the team may pick a grade ({r.status_code} {r.json})")
ok(conn.execute("SELECT status FROM productions WHERE id = ?", (pid,)).fetchone()[0] == "planned", "and it's queued")
r = web.post(f"/api/production/{pid}/grade", json={"looks": {}})
ok(r.status_code == 400, "no looks: refused")
r = web.post("/api/production/987654/grade", json={"looks": {"1": "marcus"}})
ok(r.status_code == 404, "no such carousel: 404")
web2 = dashboard.app.test_client()
r = web2.post(f"/api/production/{pid}/grade", json={"looks": {"1": "marcus"}})
ok(r.status_code in (401, 302, 403), f"signed out: refused ({r.status_code})")

finish()
