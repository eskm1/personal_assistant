"""The brand kit (brand_kit.py, brand_kit_routes.py, Review > More > Brand kit; Bryan, 2026-10-28: "introduce fonts,
colour scheme as part of the brand design kit ... similar to how canva does it ... more consistent assets across and
also can tweak based on content type").

Part 0, units: the looks' own values, a kit per kind of post over the main kit, what's saved (only what differs),
contrast (WCAG 4.5:1) and a font without the bold a look needs falling back, both said in plain words.
Part 1, nothing changes until it's edited: with no kit, or an unedited one, the overlay page of every look and the
carousel's page are byte for byte the pages of editor.py and carousel.py at cdb3d39 (fixtures/before), and rendered
(HyperFrames, Chrome) the same pixels.
Part 2, an edited kit reaches every render: a Reel's overlay (colours, fonts in the page and in the measured labels,
the logo on the end card) in pixels, a carousel's slides, a slideshow Reel's slides, end card and backdrop.
Part 3, a real draft (real speech, real ffmpeg + HyperFrames; Claude stubbed): made in the kit of its kind of post
(On site's own labels), the kit changed, Edit words (a caption-only re-render) keeps the draft's kit, Edit it again
takes today's. A carousel keeps its version's kit through Studio's own fix round; its next version takes today's.
Part 4, uploads and the page's routes: fonts (.ttf, .otf, .woff2) checked from their own bytes, a logo PNG saved
again, everything else refused safely; Bryan saves, the team looks; the live preview; help, usage and README.
Run: python tests/quality/suite_brand_kit.py (from studio/)"""
import glob, io, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as HERE, ok, finish, load_before, STUDIO
import media
CUTS, MEDIA = HERE, HERE / "media"
media.cuts_speech(CUTS / "media"); media.music_bed(MEDIA / "music.mp3")
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  TELEGRAM_BOT_TOKEN="", STUDIO_SUPABASE_URL="", STUDIO_SUPABASE_SERVICE_KEY="",
                  STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0", STUDIO_EDIT_SCENES="0", STUDIO_SFX="0",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_SPELL_CHECK="0", STUDIO_SERVICE_WORKER="0")
import numpy as np
from PIL import Image
import settings, catalog, extract, editor, draft_review, llm, usage, music, worker, notify, restyle, render
import brand_kit, carousel, slideshow, final_check
import transcript_edit as te

settings.ensure_folders(); conn = catalog.connect()
(settings.ROOT / "style_release.txt").write_text(restyle.RELEASE + "\n")  # no release restart in this test
SHOTS = HERE / "frames"; SHOTS.mkdir(exist_ok=True)
RED, BLUE, GREEN = "#E4572E", "#1F3A5F", "#2EA043"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def near(img, colour, box, tol=26, alpha=False):
    """How many pixels in box are within tol of colour (opaque ones only, on an RGBA frame)."""
    x0, y0, x1, y1 = box
    a = img[y0:y1, x0:x1].astype(int)
    c = rgb(colour) if isinstance(colour, str) else colour
    m = (np.abs(a[..., 0] - c[0]) < tol) & (np.abs(a[..., 1] - c[1]) < tol) & (np.abs(a[..., 2] - c[2]) < tol)
    if alpha and a.shape[2] == 4:
        m &= a[..., 3] > 200
    return int(m.sum())


def frame(video, t, out, alpha=False):
    args = ["ffmpeg", "-y", "-v", "error"] + (["-c:v", "libvpx-vp9"] if alpha else []) + [
        "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1"] + (["-pix_fmt", "rgba"] if alpha else []) + [str(out)]
    subprocess.run(args, check=True)
    return np.asarray(Image.open(out).convert("RGBA" if alpha else "RGB")).astype(int)


def save_kit(body):
    brand_kit.save(body, "Bryan")


PAGES = []
real_hf = render.hyperframes


def keeping(args, **kw):
    project = Path(args[1]) if len(args) > 1 else None
    if project and (project / "index.html").exists():
        keep = HERE / "pages" / f"{len(PAGES):03d}"
        shutil.copytree(project, keep)
        PAGES.append(keep)
    return real_hf(args, **kw)


render.hyperframes = keeping


def page_of(fn):
    """The overlay page a call would render (not rendered)."""
    holder = {}

    def grab(args, **kw):
        holder["html"] = (Path(args[1]) / "index.html").read_text()
        holder["files"] = sorted(p.name for p in Path(args[1]).iterdir())
        return NS(returncode=1, stderr="", stdout="")
    real, render.hyperframes = render.hyperframes, grab
    try:
        fn()
    except RuntimeError:
        pass
    finally:
        render.hyperframes = real
    return holder.get("html", ""), holder.get("files", [])


# ═══ 0. Units ═══
print("\n== 0. The kit: the looks' own values, kinds of post, saving, warnings")
ok(not brand_kit.kit_file().exists() and brand_kit.load() == {}, "no kit saved: nothing on disk")
ok(editor.look_now() == {"titles": "site", "caption_font": "archivo"} and editor.look_now("onsite") == editor.look_now(),
   f"with no kit, a new draft's look is today's, no kit kept with it ({editor.look_now('onsite')})")
own = {p: brand_kit.preset_kit(p) for p in brand_kit.PRESETS}
ok(all(brand_kit.is_default(k) for k in own.values()) and all(not brand_kit.warnings(k) for k in own.values()),
   "each look's own kit is unedited and draws nothing hard to read")
ok(own["site"]["colours"]["key"] == editor.THEME["urban_makers"]["brass"]
   and brand_kit.rgb_text(own["site"]["colours"]["label"]) == editor.THEME["urban_makers"]["label-rgb"]
   and brand_kit.rgb_text(own["gallery"]["colours"]["label"]) == editor.THEME["urban_makers"]["shade-rgb"]
   and brand_kit.rgb_text(own["classic"]["colours"]["label"]) == editor.THEME["urban_makers"]["deep-rgb"]
   and own["classic"]["colours"]["key"].lower() == editor.THEME["urban_makers"]["accent"],
   "the looks' own colours are the editor's (brass and the labels' dark, the gallery shade, classic's green and amber)")
ok(brand_kit.slide_colours(None) == carousel.COLOURS and own["site"]["fonts"]["slide_headings"] == "bricolage"
   and own["site"]["fonts"]["slide_text"] == "figtree", "the slides' own colours and fonts are the carousel's")
ok(own["site"]["fonts"]["titles"] == own["site"]["fonts"]["captions"] == "archivo"
   and own["gallery"]["fonts"]["titles"] == "instrument_serif" and own["gallery"]["fonts"]["captions"] == "inter",
   "Site Notes is Archivo; gallery its serif and Inter")
ok(brand_kit.resolve("knowhow", "personal") is None and editor.look_now(None, "personal") == {"titles": "site", "caption_font": "archivo"},
   "another account's posts don't use it (personal is on hold and keeps its colours)")

save_kit({"colours": {"key": RED, "text": "#FFFFFF"}, "fonts": {"titles": "dm_sans"},
          "kinds": {"onsite": {"colours": {"label": BLUE}}, "ideas": {"colours": {"key": RED}}}})
data = brand_kit.load()
ok(data["colours"] == {"key": RED} and data["fonts"] == {"titles": "dm_sans"} and list(data["kinds"]) == ["onsite"],
   f"only what differs is saved: the white words and Ideas' own key word equal what's under them ({data['colours']}, {list(data['kinds'])})")
on, kh = brand_kit.resolve("onsite"), brand_kit.resolve("knowhow")
ok(on["colours"]["label"] == BLUE and on["colours"]["key"] == RED and on["fonts"]["titles"] == "dm_sans" and on["kind"] == "onsite",
   "On site: its own labels, the rest from the main kit")
ok(kh["colours"]["label"] == own["site"]["colours"]["label"] and kh["colours"]["key"] == RED,
   "Know-how: the main kit (no labels of its own)")
ok(not brand_kit.is_default(kh) and editor.look_now("onsite")["kit"] == on and editor.look_now("onsite")["titles"] == "site",
   "an edited kit is kept with a new draft's look (a snapshot)")
for bad, why in (({"colours": {"key": "red"}}, "not a colour"), ({"colours": {"nope": "#000000"}}, "not a colour"),
                 ({"fonts": {"titles": "comic_sans"}}, "no such font"), ({"kinds": {"memes": {}}}, "no such kind"),
                 ({"logo": "logo-0000000000000000.png"}, "isn't there")):
    try:
        save_kit(bad)
        ok(False, f"refused: {bad}")
    except ValueError as e:
        ok(why in str(e), f"refused in plain words: {bad} -> {e}")
ok(brand_kit.load()["colours"] == {"key": RED}, "a refused save changes nothing")
ok(round(brand_kit.contrast("#000000", "#FFFFFF"), 1) == 21.0 and round(brand_kit.contrast("#777777", "#FFFFFF"), 2) == 4.48,
   "contrast as WCAG measures it (21:1, #777 on white 4.48:1)")
low = brand_kit.resolve(None, data={"colours": {"text": "#2A2A2A", "slide_body": "#2E3D38"}})
w = brand_kit.warnings(low)
ok({x["role"] for x in w if x["kind"] == "contrast"} >= {"text", "slide_body"} and all("hard to read" in x["text"] and "4.5" in x["text"] for x in w),
   f"text under 4.5:1 on what it sits on is said in plain words ({[x['text'] for x in w]})")
nb = brand_kit.resolve(None, data={"fonts": {"titles": "instrument_serif", "slide_headings": "instrument_serif"}})
w = [x for x in brand_kit.warnings(nb) if x["kind"] == "font"]
ok(nb["fonts"]["titles"] == "archivo" and nb["fonts"]["slide_headings"] == "bricolage" and nb["asked"]["titles"] == "instrument_serif"
   and len(w) == 2 and all("has no bold" in x["text"] for x in w) and "Archivo is used instead" in w[0]["text"],
   f"a font without the bold Site Notes' titles need falls back, and says so ({[x['text'] for x in w]})")
gal = brand_kit.resolve(None, data={"preset": "gallery", "fonts": {"titles": "instrument_serif", "captions": "nunito"}})
ok(gal["fonts"]["titles"] == "instrument_serif" and gal["fonts"]["captions"] == "nunito" and not gal["asked"],
   "gallery's light serif titles need no bold: no fall back")
cl = brand_kit.resolve(None, data={"preset": "classic", "fonts": {"captions": "outfit", "titles": "inter"}})
ok(cl["fonts"]["titles"] == cl["fonts"]["captions"] == "outfit", "classic sets its titles in the captions font")
# Measured in the kit's own titles font (the labels' boxes, as the overlay sets them and the watch-through measures).
title = "Waterproofing before the tiles"
box_a = editor.site_layout("hook", title)["box"]
with brand_kit.measuring(brand_kit.preset_kit("site")):
    box_d = editor.site_layout("hook", title)["box"]
with brand_kit.measuring(on):
    box_k = editor.site_layout("hook", title)["box"]
ok(box_a == box_d and abs(box_k[2] - box_a[2]) > 4, f"Site Notes' labels are measured in the kit's titles font (Archivo {box_a[2]:.0f}, "
   f"DM Sans {box_k[2]:.0f}; the unedited kit exactly Archivo's)")
ok(editor.draft_look({"look": {"titles": "site", "caption_font": "archivo", "kit": on}})["kit"] == on
   and "kit" not in editor.draft_look({"look": {"titles": "gallery", "caption_font": "inter", "kit": on}})
   and "kit" not in editor.draft_look({"look": {"titles": "site", "caption_font": "archivo"}}),
   "a draft keeps the kit it was made with (one made in another look, or before the kit: the look's own)")

# ═══ 1. Nothing changes until it's edited ═══
print("\n== 1. Today's look, byte for byte and pixel for pixel")
old = load_before("editor_cdb3d39.py", "editor_before_kit")
old_c = load_before("carousel_cdb3d39.py", "carousel_before_kit")
CH = [{"start": 0.3, "end": 1.4, "text": "Which ones are"}, {"start": 1.4, "end": 2.6, "text": "beams, really?", "key": 0, "lit": True},
      {"start": 3.0, "end": 4.6, "text": "inside the DB box", "key": 3}]
GR = [{"style": "callout", "start": 3.0, "duration": 1.6, "title": "Only 16-way", "italic": "16-way", "lines": ["This board"], "point": [212, 860]},
      {"style": "card", "start": 4.8, "duration": 1.6, "title": "Power", "italic": "", "lines": ["Older HDB 30A", "HDB 40A"]}]
END = {"start": 6.6, "duration": 1.6, "ask": "Save this for your *reno*", "handle": "@urbanmakers_sg"}
HOOK = "Tap test: beam or false *ceiling?*"


def overlay_call(mod, style, kit=..., out=None, **kw):
    args = dict(style=style, end=dict(END), pip={"side": "left", "spans": [[0.2, 1.0]]}, paper=[(4.0, 4.5)])
    if kit is not ...:
        args["kit"] = kit
    args.update(kw)
    return lambda: mod.render_overlay([dict(c) for c in CH], HOOK, 8.2, out or (HERE / "x.webm"), [dict(g) for g in GR], **args)


same = {}
for style in brand_kit.PRESETS:
    before = page_of(overlay_call(old, style))
    same[style] = (before == page_of(overlay_call(editor, style)), before == page_of(overlay_call(editor, style, own[style])))
ok(all(a and b for a, b in same.values()), f"every look's overlay page (and its files) is the page before the kit, with no kit or "
   f"an unedited one {same}")
slides = carousel.clean_slides([{"role": "cover", "heading": "Three things to check before you tile", "body": "Swipe for the list.", "image": {"source": "text"}},
                                {"role": "point", "heading": "The membrane goes up the wall", "body": "At least 150 mm in the shower.", "image": {"source": "text"}},
                                {"role": "ask", "heading": "Save this for your reno", "body": "", "image": {"source": "text"}}])
pics = [(None, "")] * 3
ok(old_c.page_html(slides, pics) == carousel.page_html(slides, pics) == carousel.page_html(slides, pics, kit=own["site"])
   and old_c.page_html(slides, pics, reel=True) == carousel.page_html(slides, pics, reel=True, kit=own["site"]),
   "the carousel's page (and a slideshow Reel's) is the page before the kit")
# The cover line's font changed on purpose after cdb3d39 (the serif, the content lead's showcase brief, 2026-10-28):
# with no kit or an unedited one its colours are the ones before the kit.
serif = carousel.SERIF.resolve().as_uri()
old_cover = carousel._COVER_LINE_CSS % {"serif": serif, **old_c.COLOURS}
new_cover = carousel._COVER_LINE_CSS % {"serif": serif, **brand_kit.slide_colours(None)}
own_cover = carousel._COVER_LINE_CSS % {"serif": serif, **brand_kit.slide_colours(own["site"])}
ok(old_cover == new_cover == own_cover and "UM Serif" in new_cover,
   "a photos-only carousel's cover line: the serif, in the colours before the kit")
# Rendered.
t0 = time.time()
ov_old, ov_new = HERE / "ov_old.webm", HERE / "ov_new.webm"
old.render_overlay([dict(c) for c in CH], HOOK, 8.2, ov_old, [dict(g) for g in GR], style="site", end=dict(END))
editor.render_overlay([dict(c) for c in CH], HOOK, 8.2, ov_new, [dict(g) for g in GR], style="site", end=dict(END), kit=own["site"])
diffs = []
for t in (1.6, 3.9, 5.6, 8.0):
    a, b = frame(ov_old, t, SHOTS / f"old_{t}.png", True), frame(ov_new, t, SHOTS / f"new_{t}.png", True)
    diffs.append(int(np.abs(a - b).max()))
print(f"  (two overlays rendered in {time.time() - t0:.0f}s)")
ok(max(diffs) <= 2, f"rendered, the unedited kit's Reel overlay is today's pixel for pixel (hook, callout, card, end card: "
   f"largest difference {diffs} of 255)")
lo_old = old_c.lay_out(conn, slides, HERE / "car_old")
lo_new = carousel.lay_out(conn, slides, HERE / "car_new", kit=own["site"])
cdiff = [int(np.abs(np.asarray(Image.open(a)).astype(int) - np.asarray(Image.open(b)).astype(int)).max())
         for a, b in zip(lo_old["files"], lo_new["files"])]
ok(len(cdiff) == 3 and max(cdiff) <= 2, f"rendered, the slides are today's pixel for pixel (largest difference {cdiff} of 255)")

# ═══ 2. An edited kit reaches every render ═══
print("\n== 2. The kit in a Reel's overlay, a carousel and a slideshow Reel")
LOGO = HERE / "logo.png"
im = Image.new("RGBA", (600, 220), (0, 0, 0, 0))
from PIL import ImageDraw
d = ImageDraw.Draw(im); d.rectangle((40, 40, 560, 180), fill=(46, 160, 67, 255))
im.save(LOGO)
logo = brand_kit.add_logo(LOGO.read_bytes(), "Bryan")
save_kit({"colours": {"key": RED, "end": "#3A1F5F", "slide_bg": "#F3EDE2", "slide_heading": "#1E2A32", "slide_body": "#3B4A52",
                      "slide_accent": "#B3432B", "slide_end": "#1E2A32"},
          "fonts": {"titles": "dm_sans", "slide_headings": "archivo"},
          "kinds": {"onsite": {"colours": {"label": BLUE}}}, "logo": logo})
kit = brand_kit.resolve("onsite")
ok(kit["logo"] == logo and brand_kit.logo_path(kit).is_file(), "the logo is the kit's once saved with it")
html, files = page_of(overlay_call(editor, "site", kit))
css = html[html.index("<style>"):html.index("</style>")]
ok(f"--brass:{RED}" in css and "--label-rgb:31,58,95" in css and "--end-rgb:58,31,95" in css and "rgba(var(--end-rgb),.74)" in css,
   "the overlay page: the key word, the labels and the end card's shade in the kit's colours")
fams = set(re.findall(r'@font-face\{font-family:"([^"]+)"', css))
ok(fams == {"DM Sans", "Archivo"} and 'font-family:"DM Sans"' in css and '.cap{font-family:"Archivo"' in css
   and any(f.startswith("dm_sans-") for f in files),
   f"its titles in the kit's font, its captions in theirs, the files next to the page ({sorted(fams)})")
ok('<img class="logo" src="logo.png"' in html and "logo.png" in files and ".endcard .mk img" in css,
   "the logo in place of the mark on the end card")
ov_kit = HERE / "ov_kit.webm"
editor.render_overlay([dict(c) for c in CH], HOOK, 8.2, ov_kit, [dict(g) for g in GR], style="site", end=dict(END), kit=kit)
f_hook = frame(ov_kit, 1.6, SHOTS / "kit_hook.png", True)
f_end = frame(ov_kit, 8.0, SHOTS / "kit_end.png", True)
f_old = frame(ov_old, 1.6, SHOTS / "old_hook.png", True)
with brand_kit.measuring(kit):
    hb = [int(v) for v in editor.site_layout("hook", editor.title_parts(HOOK)[0])["box"]]
ok(near(f_hook, (31, 58, 95), hb, 14, True) > 20000 and near(f_old, (31, 58, 95), hb, 14, True) == 0,
   f"rendered: the hook's labels are On site's blue ({near(f_hook, (31, 58, 95), hb, 14, True)} px)")
ok(near(f_hook, RED, (hb[0], hb[1], hb[2], hb[3]), 30, True) > 400 and near(f_hook, RED, (90, 1240, 930, 1460), 30, True) > 200
   and near(f_hook, "#F2B65A", (90, 1240, 930, 1460), 30, True) < 20,
   "rendered: the hook's and the caption's key word in the kit's red, no brass")
ok(near(f_end, (46, 160, 67), (64, 500, 700, 760), 20, True) > 5000, "rendered: the logo on the end card")
# The carousel.
lo_kit = carousel.lay_out(conn, slides, HERE / "car_kit", kit=kit)
s1 = np.asarray(Image.open(lo_kit["files"][0]).convert("RGB")).astype(int)
s3 = np.asarray(Image.open(lo_kit["files"][2]).convert("RGB")).astype(int)
ok(near(s1, "#F3EDE2", (0, 600, 1080, 1100), 4) > 300000 and near(s3, "#1E2A32", (0, 400, 1080, 900), 4) > 200000,
   "rendered: the slides' background and the last slide's in the kit's colours")
ok(near(s1, "#1E2A32", (84, 236, 1000, 1110), 30) > 3000 and near(s1, "#B3432B", (84, 236, 400, 1110), 30) > 600,
   "the heading in the kit's colour, the accent bar in its accent")
page = carousel.page_html(slides, pics, kit=kit)
ok("Archivo-latin-800.woff2" in page and ".mark img.logo" in page and 'class="logo"' in page,
   "the slides' headings in the kit's font, the logo in the mark")
ok(near(s1, (46, 160, 67), (60, 50, 400, 120), 20) > 300, "rendered: the logo at the top of a slide")
shutil.copy(lo_kit["files"][0], SHOTS / "slide_kit.png")
# A slideshow Reel: its slides laid out in the kit, its end card and backdrop.
rl, fell = slideshow.reel_layout(conn, HERE / "ss", [{"n": n, "role": s["role"], "heading": s["heading"], "body": s["body"],
                                                      "image": s["image"], "carousel_file": "", "file": ""}
                                                     for n, s in enumerate(slides, 1)], kit)
r1 = np.asarray(Image.open(rl[0]["file"]).convert("RGB")).astype(int)
ok(not fell and near(r1, "#F3EDE2", (0, 600, 1080, 1100), 4) > 300000, "a slideshow Reel's slides are laid out in the kit")
shown = [{"file": x["file"], "start": i * 2.0, "seconds": 2.0} for i, x in enumerate(rl)]
html, files = page_of(lambda: slideshow.compose(shown, 8.0, {"start": 6.0, "duration": 2.0, "ask": "Save this", "handle": "@urbanmakers_sg"},
                                                 HERE / "ss.mp4", editor.ACCENT["urban_makers"], push=False, style="site", kit=kit))
ok("--card-hi:#F3EDE2" in html and "--card-lo:#1E2A32" in html and '<img class="logo" src="logo.png"' in html
   and f"--brass:{RED}" in html and 'font-family:"DM Sans"' in html,
   "its backdrop in the slides' colours, its end card in the kit (logo, key colour, titles font)")
html_d, _ = page_of(lambda: slideshow.compose(shown, 8.0, {"start": 6.0, "duration": 2.0, "ask": "Save this", "handle": "h"},
                                               HERE / "ss.mp4", editor.ACCENT["urban_makers"], push=False, style="site"))
ok("--card-hi:" in html_d and "--card-hi:#F3EDE2" not in html_d and "logo.png" not in html_d, "without a kit, as before")

# ═══ 3. A real draft keeps its kit; a new edit takes today's ═══
print("\n== 3. A real draft: made in its kind's kit, Edit words keeps it, Edit it again takes today's")
data = json.loads((CUTS / "media" / "words.json").read_text())
words = [{k: x[k] for k in ("word", "start", "end")} for x in data["words"]]
words[11]["word"] = "SIDs"
dd = settings.LIBRARY / "2026-10" / "talk"; dd.mkdir(parents=True)
v = dd / "talk.mp4"; shutil.copy(CUTS / "media" / "talk.mp4", v)
(dd / "transcript.json").write_text(json.dumps({"language": "en", "text": "", "words": words, "segments": []}), encoding="utf-8")
cid = catalog.start(conn, "fp-talk", v.name, dd, v)
catalog.save_tags(conn, cid, extract.probe(v), {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "Joints",
                   "summary": "Bryan on joints", "topics": [], "setting": "", "usable": True, "quality_notes": "fine",
                   "suggested_hook": "", "moments": []})
def W(word, n=0): return [x for x in words if x["word"] == word][n]
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": "Seamless joints",
    "hook": "Joints you can't see", "angle": "x", "beats": [], "segments": [{"clip_id": cid, "start": 0, "end": data["total"],
    "purpose": "all"}], "caption": "cap", "est_seconds": 15}])
idea = conn.execute("select max(id) from ideas").fetchone()[0]
conn.execute("UPDATE ideas SET post_type = 'onsite' WHERE id = ?", (idea,)); conn.commit()
pid = catalog.make_production(conn, idea)
def row(): return dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())
music.find_track = lambda brief, seconds, out_dir, ref="": (MEDIA / "music.mp3", {"title": "Bed", "artist": "ffmpeg", "recording_id": "t1"})
music.allowed_for = lambda b: True
editor.check_words = lambda final, expected, work: {"words_expected": len(expected), "match": 1.0, "missing": []}
notify.digest.add = lambda kind, n=1: None
import previews
previews.for_draft = lambda path: None
EDL = {"cuts": [{"clip_id": cid, "start": W("This")["start"] - 0.2, "end": W("seamless.")["end"] + 0.25, "why": "kept"}],
       "hook_text": "Joints you *can't* see", "key_words": ["seamless"],
       "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
       "summary": "Kept the explanation.", "missing_shots": [], "notes_addressed": [], "graphics": []}
CALLS, QUEUE = [], {"edit": [json.loads(json.dumps(EDL)), json.loads(json.dumps(EDL))]}
READ = {"verdict": "Ready to post", "scores": {s: "good" for s in ("hook", "pace", "sound", "captions", "picture")}, "fixes": [], "works": ["Clear"]}
def fake_final(client, **req):
    sysp = req.get("system", "")
    k = ("edit" if "You are the editor for Bryan's Instagram Reels" in sysp else "review" if "You review draft Instagram Reels" in sysp
         else "join" if "You check a Reel's cut for broken sentences" in sysp else "?")
    CALLS.append(k)
    content = req["messages"][0]["content"]
    text = "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text") if isinstance(content, list) else content
    answer = (QUEUE["edit"].pop(0) if k == "edit" else READ if k == "review"
              else {"joins": [{"id": i + 1, "reads_complete": True, "restore": []} for i in range(text.count("join "))]} if k == "join"
              else None)
    if answer is None:
        raise AssertionError("unexpected call: " + sysp[:60])
    return NS(content=[NS(type="text", text=json.dumps(answer))], stop_reason="end_turn", model=req.get("model") or "claude-opus-5-5",
              usage=NS(input_tokens=2000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0))
llm.final_message = fake_final


def overlay_page_of_last():
    pages = [p for p in PAGES if 'data-composition-id="overlay"' in (p / "index.html").read_text()]
    return (pages[-1] / "index.html").read_text() if pages else ""


t0 = time.time()
worker.run_editor(stop_after_one=True)
r1 = row()
print(f"  (v1 in {time.time() - t0:.0f}s, calls {CALLS})")
e1 = json.loads(r1["edl"])
k1 = (e1.get("look") or {}).get("kit") or {}
ok(r1["status"] == "ready" and r1["version"] == 1 and k1.get("kind") == "onsite" and k1["colours"]["label"] == BLUE
   and k1["colours"]["key"] == RED and k1["fonts"]["titles"] == "dm_sans",
   f"v1 is made in On site's kit, kept with the draft ({r1['status']}, {k1.get('kind')}, {k1.get('colours', {}).get('label')})")
p1 = overlay_page_of_last()
ok(f"--brass:{RED}" in p1 and "--label-rgb:31,58,95" in p1 and 'font-family:"DM Sans"' in p1, "its overlay page in that kit")
f1 = frame(Path(r1["video_path"]), 1.0, SHOTS / "v1_hook.png")
with brand_kit.measuring(k1):
    hb1 = [int(x) for x in editor.site_layout("hook", editor.title_parts(e1["hook_text"])[0])["box"]]
ok(near(f1, (31, 58, 95), hb1, 28) > 15000 and near(f1, RED, hb1, 34) > 300,
   f"in the finished Reel the hook sits on On site's blue, its key word red ({near(f1, (31, 58, 95), hb1, 28)}, {near(f1, RED, hb1, 34)} px)")
ok([int(x) for x in final_check.shown(e1, r1["duration"])[0]["box"]] == hb1,
   "the watch-through measures the hook in the draft's own font")
# Bryan changes the kit: green key word, On site's labels back to the main kit's.
save_kit({"colours": {"key": GREEN}, "fonts": {}, "kinds": {}, "logo": None})
ok(brand_kit.resolve("onsite")["colours"]["key"] == GREEN and not brand_kit.logo_path(brand_kit.resolve("onsite")), "the kit changed")
# Edit words: the captions only, the same cut, the draft's own kit.
tl1 = draft_review.draft_words(conn, r1)
box = te.editable(tl1)
te.submit(conn, pid, box.replace("SIDs", "as IDs"), 1, who="Michelle")
CALLS.clear()
worker.run_editor(stop_after_one=True)
r2 = row()
e2 = json.loads(r2["edl"])
ok(r2["version"] == 2 and CALLS == [] and e2["look"].get("kit") == k1, f"Edit words: v2 keeps v1's kit exactly, no Claude call ({CALLS})")
p2 = overlay_page_of_last()
ok(f"--brass:{RED}" in p2 and GREEN not in p2 and "--label-rgb:31,58,95" in p2, "its overlay page: v1's red and blue, not today's green")
f2 = frame(Path(r2["video_path"]), 1.0, SHOTS / "v2_hook.png")
ok(near(f2, (31, 58, 95), hb1, 28) > 15000 and near(f2, GREEN, hb1, 34) < 50, "and in its pixels")
# Edit it again: a fresh edit in today's kit.
restyle.mark(conn, [pid])
CALLS.clear()
worker.run_editor(stop_after_one=True)
r3 = row()
e3 = json.loads(r3["edl"])
k3 = e3["look"].get("kit") or {}
ok(r3["version"] == 3 and "edit" in CALLS and k3.get("colours", {}).get("key") == GREEN and k3["colours"]["label"] == own["site"]["colours"]["label"],
   f"Edit it again: v3 is made in today's kit ({k3.get('colours', {}).get('key')})")
p3 = overlay_page_of_last()
ok(f"--brass:{GREEN}" in p3 and "--label-rgb" in p3 and "31,58,95" not in p3, "its overlay page in today's colours")
# The kit back to its look's own: a new edit keeps no kit, and draws today's look.
save_kit({"colours": {}, "fonts": {}, "kinds": {}, "logo": None})
ok(editor.look_now("onsite") == {"titles": "site", "caption_font": "archivo"}, "an unedited kit again: a new draft keeps no kit")
# A carousel: its version's kit through Studio's own fix round; the next version in today's.
save_kit({"colours": {"slide_bg": "#F3EDE2", "slide_heading": "#1E2A32", "slide_body": "#3B4A52"}, "fonts": {},
          "kinds": {"knowhow": {"colours": {"slide_bg": "#E9F0F7"}}}})
catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "carousel", "title": "Tiles", "hook": "Tiles", "angle": "x",
                                "beats": [], "segments": [], "caption": "cap", "est_seconds": 30, "cta": "Save this"}])
cidea = conn.execute("select max(id) from ideas").fetchone()[0]
conn.execute("UPDATE ideas SET post_type = 'knowhow' WHERE id = ?", (cidea,))
cplan = {"idea_id": cidea, "title": "Tiles", "brand": "urban_makers", "format": "carousel", "post_type": "knowhow",
         "slides": [{**s, "image": {"source": "text"}} for s in slides]}
stamp = catalog.now()
cpid = conn.execute("""INSERT INTO productions (title, brand, format, status, version, idea, source_clips, caption, plan, created_at, updated_at)
                       VALUES ('Tiles', 'urban_makers', 'carousel', 'planned', 0, 'x', '[]', 'cap', ?, ?, ?)""",
                    (json.dumps(cplan), stamp, stamp)).lastrowid
conn.commit()
import self_fix
real_deliver = self_fix.deliver
self_fix.deliver = lambda conn_, production_id, client=None, *a, **k: dict(conn_.execute(
    "SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
carousel.run(conn, cpid)
crow = dict(conn.execute("select * from productions where id=?", (cpid,)).fetchone())
cdata = carousel.current(crow)
bg1 = np.asarray(Image.open(cdata["files"][0]).convert("RGB")).astype(int)
ok(cdata.get("kit", {}).get("kind") == "knowhow" and cdata["kit"]["colours"]["slide_bg"] == "#E9F0F7"
   and near(bg1, "#E9F0F7", (0, 600, 1080, 1100), 4) > 300000, "a carousel's v1 in Know-how's kit, kept with the version")
save_kit({"colours": {"slide_bg": "#2B1F3A"}, "fonts": {}, "kinds": {}})
out, res = carousel.patch(conn, cpid, [{"n": 1, "action": {"type": "slide_text", "item": 2, "field": "body", "to": "At least 150 mm up."}}], 1)
pdata = carousel.current(dict(conn.execute("select * from productions where id=?", (cpid,)).fetchone()))
bgp = np.asarray(Image.open(pdata["files"][0]).convert("RGB")).astype(int)
ok(out and res[0]["ok"] and pdata["kit"] == cdata["kit"] and near(bgp, "#E9F0F7", (0, 600, 1080, 1100), 4) > 300000,
   "Studio's own fix round lays it out in the version's kit, not today's")
conn.execute("UPDATE productions SET status = 'planned' WHERE id = ?", (cpid,)); conn.commit()
carousel.run(conn, cpid)
ndata = carousel.current(dict(conn.execute("select * from productions where id=?", (cpid,)).fetchone()))
bgn = np.asarray(Image.open(ndata["files"][0]).convert("RGB")).astype(int)
ok(ndata["version"] == 2 and ndata["kit"]["colours"]["slide_bg"] == "#2B1F3A" and near(bgn, "#2B1F3A", (0, 600, 1080, 1100), 4) > 300000,
   "the next version takes today's kit")
self_fix.deliver = real_deliver

# ═══ 4. Uploads and the page ═══
print("\n== 4. Uploads checked, the routes, the page")
FONTS = Path(render.RENDER_DIR) / "captions" / "fonts"
woff2 = (FONTS / "Inter-latin-600.woff2").read_bytes()
info = brand_kit.check_font(woff2, "anything.woff2")
ok(info["ext"] == ".woff2" and info["family"] == "Inter" and info["weight"] == "600" and info["style"] == "normal",
   f"a .woff2 read from its own bytes: family, weight ({info})")
var = brand_kit.check_font((FONTS / "DMSans-latin.woff2").read_bytes(), "x.woff2")
ok(len(var["weight"].split()) == 2 and int(var["weight"].split()[0]) < 400 < int(var["weight"].split()[1]),
   f"a variable font's weights from its axis ({var['weight']})")
system = sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)) + sorted(glob.glob("/usr/share/fonts/**/*.otf", recursive=True))
ttf = next((Path(p) for p in system if p.endswith(".ttf") and "Bold" in p), None)
otf = next((Path(p) for p in system if p.endswith(".otf")), None)
ok(ttf and brand_kit.check_font(ttf.read_bytes(), ttf.name)["ext"] == ".ttf" and otf
   and brand_kit.check_font(otf.read_bytes(), otf.name)["ext"] == ".otf", f"a .ttf and an .otf accepted ({ttf and ttf.name}, {otf and otf.name})")
def refused(raw, name):
    try:
        brand_kit.check_font(raw, name)
        return ""
    except ValueError as e:
        return str(e)
ok("isn't a .ttf" in refused(b"<html><script>alert(1)</script>" * 100, "font.woff2"), "a web page called .woff2: refused")
ok("Only .ttf" in refused(woff2, "font.exe") and "Only .ttf" in refused(woff2, "font.svg"), "a name that isn't a font's: refused")
ok("too big" in refused(b"wOF2" + b"\0" * brand_kit.FONT_MAX_BYTES, "big.woff2"), "over 3 MB: refused")
ok("couldn't" in refused(woff2[:2048], "cut.woff2"), f"a cut-short font: refused, in plain words ({refused(woff2[:2048], 'cut.woff2')})")
ok("isn't a .ttf" in refused(b"wOFF" + woff2[4:], "old.woff2"), "a WOFF 1 (or anything else) passed off as .woff2: refused")
ok(not brand_kit._FAMILY.match('Evil";}body{x') and not brand_kit._FAMILY.match("") and brand_kit._FAMILY.match("Söhne Breit"),
   "a family name with quotes or braces is refused; a sane one passes")
e = brand_kit.add_font(woff2, "../../../etc/evil.ttf", "Bryan")
stored = brand_kit.font_dir() / e["file"]
ok(stored.is_file() and stored.parent == brand_kit.font_dir() and re.fullmatch(r"[0-9a-f]{16}\.woff2", e["file"])
   and not (settings.ROOT / "etc").exists(), f"the file name is never used: kept as {e['file']} in BrandKit/fonts")
ok(brand_kit.add_font(woff2, "again.woff2", "Bryan")["id"] == e["id"] and len(brand_kit.uploads()) == 1, "the same file twice is one font")
fam = brand_kit.upload_key("Inter")
ok(any(f["key"] == fam and f["source"] == "upload" for f in brand_kit.fonts_list()), "it joins the list under its family")
save_kit({"fonts": {"captions": fam, "titles": fam}})
k = brand_kit.resolve()
ok(k["fonts"]["titles"] == fam and k["faces"]["titles"][0]["upload"] == e["id"] and brand_kit.faces(k, "titles")[0]["path"] == stored,
   "picked, the kit loads its file (Inter 600 is bold enough)")
html, files = page_of(overlay_call(editor, "site", k))
ok('font-family:"Kit inter"' in html and "kit-inter-1.woff2" in files, "an uploaded font's page name is Studio's own, never the file's words")
brand_kit.remove_font(e["id"])
ok(not any(f["source"] == "upload" for f in brand_kit.fonts_list()) and "titles" not in brand_kit.load()["fonts"] and stored.is_file(),
   "removed: off the list and out of the kit; its file stays for drafts made with it")
ok(brand_kit.faces(k, "titles")[0]["path"] == stored, "a draft made with it still loads it")
stored.unlink()
ok(brand_kit.faces(k, "titles")[0]["path"].name.startswith("Archivo"), "its file gone by hand: the look's own font, never a failed render")
png = brand_kit.check_logo(LOGO.read_bytes())
ok(png.startswith(b"\x89PNG") and Image.open(io.BytesIO(png)).size == (521, 141), "a logo PNG saved again by Studio, its empty edges trimmed")
def logo_refused(raw):
    try:
        brand_kit.check_logo(raw)
        return ""
    except ValueError as e:
        return str(e)
jpg = io.BytesIO(); Image.new("RGB", (100, 100)).save(jpg, "JPEG")
wide = io.BytesIO(); Image.new("RGBA", (5000, 20)).save(wide, "PNG")
ok("must be a PNG" in logo_refused(jpg.getvalue()) and "must be a PNG" in logo_refused(b"<svg onload='x'/>")
   and "16 to 4000" in logo_refused(wide.getvalue()) and "couldn't be opened" in logo_refused(b"\x89PNG\r\n\x1a\n" + b"x" * 300),
   "a JPEG, an SVG, a 5000 px banner and a broken PNG: refused")
# The routes.
import auth, dashboard
uid = auth.create_user(conn, "bryan@x.com", "Bryan", "correct horse battery", auth.OWNER)
tid = auth.create_user(conn, "michelle@theurbanmakers.com", "Michelle", "correct horse battery", auth.TEAM)
def client(u):
    c = dashboard.app.test_client()
    if u:
        with c.session_transaction() as s:
            s["uid"] = u; s["sv"] = 1
    return c
owner, team, nobody = client(uid), client(tid), client(None)
ok(nobody.get("/api/brand-kit").status_code == 401 and nobody.post("/api/brand-kit/preview", json={}).status_code == 401,
   "signed out: nothing")
v = team.get("/api/brand-kit").json
ok(v["editable"] is False and owner.get("/api/brand-kit").json["editable"] is True and v["preset"] == "site"
   and len(v["kind_names"]) == 5 and v["kind_names"][0]["name"] == "Know-how", "the team sees it, Bryan changes it; the five kinds of post")
before = brand_kit.kit_file().read_text()
r1_ = team.post("/api/brand-kit", json={"colours": {"key": RED}})
r2_ = team.post("/api/brand-kit/font", data={"file": (io.BytesIO(woff2), "x.woff2")}, content_type="multipart/form-data")
r3_ = team.post("/api/brand-kit/logo", data={"file": (io.BytesIO(LOGO.read_bytes()), "l.png")}, content_type="multipart/form-data")
ok(r1_.status_code == r2_.status_code == r3_.status_code == 403 and "Only Bryan" in r1_.json["error"]
   and brand_kit.kit_file().read_text() == before, "the team's save, font and logo: refused, saying why")
r = owner.post("/api/brand-kit", json={"colours": {"key": RED}, "kinds": {"finished": {"colours": {"label": BLUE}}}})
ok(r.status_code == 200 and r.json["colours"]["key"] == RED and r.json["resolved"]["finished"]["colours"]["label"] == BLUE,
   "Bryan saves; the page gets each kind's kit back")
ok(owner.post("/api/brand-kit", json={"colours": {"key": "javascript:"}}).status_code == 400, "a bad value: 400")
r = owner.post("/api/brand-kit/font", data={"file": (io.BytesIO(b"<html>" * 400), "x.woff2")}, content_type="multipart/form-data")
ok(r.status_code == 400 and "isn't a .ttf" in r.json["error"], f"a bad font upload: 400 in plain words ({r.json})")
r = owner.post("/api/brand-kit/font", data={"file": (io.BytesIO(woff2), "x.woff2")}, content_type="multipart/form-data")
ok(r.status_code == 200 and r.json["font"]["family"] == "Inter" and any(f["source"] == "upload" for f in r.json["fonts_list"]),
   "a font upload: the list comes back with it")
r = owner.post("/api/brand-kit/logo", data={"file": (io.BytesIO(LOGO.read_bytes()), "l.png")}, content_type="multipart/form-data")
ok(r.status_code == 200 and owner.get(r.json["url"]).status_code == 200 and "logo" not in brand_kit.load(),
   "a logo upload is kept, and only becomes the kit's with Save")
r = owner.post("/api/brand-kit/preview", json={"kit": {"colours": {"key": GREEN}, "kinds": {"finished": {"colours": {"label": BLUE}}}},
                                               "kind": "finished"})
ok(r.status_code == 200 and r.json["resolved"]["colours"]["key"] == GREEN and r.json["resolved"]["colours"]["label"] == BLUE
   and brand_kit.load()["colours"]["key"] == RED, "the preview: the kit as edited, for a kind, nothing saved")
reel = owner.get(r.json["reel"])
slide = owner.get(r.json["slide"])
rp = reel.data.decode()
ok(reel.status_code == 200 and f"--brass:{GREEN}" in rp and "window.show" in rp and "gsap.min.js" in rp
   and reel.headers.get("X-Frame-Options") == "SAMEORIGIN" and slide.status_code == 200 and "studioFit" in slide.data.decode(),
   "the preview's pages: the real overlay and slides, framed by Review only")
ok("file://" not in slide.data.decode() and all(team.get(r.json["slide"].rsplit("/", 1)[0] + "/" + n).status_code == 200
                                                  for n in re.findall(r'url\("(f\d+\.woff2)"\)', slide.data.decode())),
   "the slides' fonts served next to their page (no file:// address)")
ok(owner.get(r.json["reel"].replace("index.html", "..%2f..%2f..%2fbrand_kit.json")).status_code == 404
   and owner.get("/media/brand-kit/preview/zzzz/reel/index.html").status_code == 404, "nothing outside a preview's own folder")
ok(owner.get("/media/brand-kit/mark.svg").status_code == 200, "the Urban Makers mark for the page")
# The page and its words.
web = (STUDIO / "web").resolve()
js = (web / "brandkit.js").read_text(); app = (web / "app.js").read_text(); idx = (web / "index.html").read_text()
shell = (STUDIO / "cloud" / "planner" / "shell.js").read_text(); helpjs = (STUDIO / "cloud" / "planner" / "help.js").read_text()
ok('["brand", "Brand kit"]' in shell and "#/brand-kit" in shell and 'id="brandkit"' in idx and "brandkit-lazy" in idx
   and '"#/brand-kit"' in app and 'key: "brand_kit"' in app, "More > Brand kit: in the shell, Review's page, fetched when opened")
ok(all(m in js for m in ('mark("brand_kit_saved"', 'mark("brand_kit_font_added"', 'mark("brand_kit_logo")', 'mark("brand_kit_kind"'))
   and "brand_kit_" in Path(STUDIO / "usage_report.py").read_text() and "brand_kit: { name: \"Brand kit\"" in helpjs,
   "usage marks, the weekly report's FEATURES row and the help lines")
ok('data-why="Only Bryan can change the brand kit."' in js and 'accept=".ttf,.otf,.woff2' in js and 'accept="image/png"' in js
   and "bk-drop" in js and "dragover" in js, "the team's Save says why; the drop zones take only fonts and PNGs")
readme = (STUDIO / "README.md").read_text(); claude = (STUDIO / "CLAUDE.md").read_text()
ok("Brand kit" in readme and "brand_kit.py" in claude and "not yet proven on the server" in claude[claude.index("Brand kit"):][:1200],
   "README and the Open items say what it is (not yet proven on the server)")
import updater
ok("brand_kit" in updater._MODULES and "brand_kit_routes" in updater._MODULES, "the updater checks the new modules import")

finish()
