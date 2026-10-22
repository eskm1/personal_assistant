"""Site Notes, the Reels' graphics look (Bryan, 2026-10-22: "go with A for now", the proposal at
https://claude.ai/artifact/TwCheZsi7ywNKNEAEJ3FcM): one font (Archivo, bundled), captions 74 px bold white with the key
word in brass, the hook and callouts on tight dark labels, cards as a dark panel over the footage (never full screen),
drawn inserts in Archivo on a dark panel, a picture's one "Concept · not a photo" label, the end card over the last
frame. The default (STUDIO_TITLE_STYLE=site); gallery stays one setting away and renders exactly as before.
The overlay pages are read as HyperFrames gets them (fonts, CSS, markup), real renders are read in pixels, and two
whole drafts of one plan (Site Notes and gallery) are compared. Claude, music, Whisper and the picture (Gemini) stubbed.
Run: python tests/quality/suite_look.py (from studio/). STUDIO_LOOK_STRIP=<folder> also saves a frame strip of each
look there (hook, caption with its key word, callout, numbers panel, picture, end card)."""
import hashlib, io, json, os, re, shutil, subprocess, time
from pathlib import Path
from types import SimpleNamespace as NS

from common import WORK as P, ok, finish, load_before
import media

os.environ.pop("STUDIO_TITLE_STYLE", None)  # Studio's default, whatever the shell says
os.environ.pop("STUDIO_CAPTION_FONT", None)
t_media = time.time()
M = media.ending_speech(P / "media")
DATA = json.loads((M / "words.json").read_text())
TALK = media.person_video(P / "media" / "person.mp4", (1080, 1920), DATA["total"], "540", 640, 3.0, audio=M / "talk.wav")
print(f"(media made in {time.time() - t_media:.0f}s)")
os.environ.update(ANTHROPIC_API_KEY="test", EPIDEMIC_SOUND_API_KEY="test", STUDIO_MUSIC_ACCOUNTS="urban_makers",
                  STUDIO_SELF_FIX_ROUNDS="0", STUDIO_EDIT_REFRAME="0", STUDIO_EDIT_PUNCH_INS="0")
import numpy as np
from PIL import Image
import settings, catalog, extract, editor, transcribe, music, anthropic, concepts, pictures, motion, final_check, render
import restyle

settings.ensure_folders()
conn = catalog.connect()
BRASS = (0xF2, 0xB6, 0x5A)


# ── What HyperFrames gets: every page kept (fonts, CSS, markup) ────────────────
PAGES = []
real_hf = render.hyperframes


def keeping(args, **kw):
    project = Path(args[1]) if len(args) > 1 else None
    if project and (project / "index.html").exists():
        keep = P / "pages" / f"{len(PAGES):03d}"
        shutil.copytree(project, keep)
        PAGES.append(keep)
    return real_hf(args, **kw)


render.hyperframes = keeping


def faces_of(page: Path) -> set:
    """The font families a page declares (@font-face), and the files they load."""
    text = (page / "index.html").read_text() + "".join(p.read_text() for p in page.glob("*.css"))
    fams = set(re.findall(r'@font-face\s*\{[^}]*font-family:\s*"([^"]+)"', text))
    return fams


def named_families(css: str) -> set:
    """The first family of every font-family rule (what draws the Latin words)."""
    return {re.split(r"[,;}]", v)[0].strip().strip('"\'') for v in re.findall(r"font-family:\s*([^;}]+)", css)}


def frame(video: Path, t: float, out: Path, alpha: bool = False) -> np.ndarray:
    args = ["ffmpeg", "-y", "-v", "error"] + (["-c:v", "libvpx-vp9"] if alpha else []) + [
        "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1"] + (["-pix_fmt", "rgba"] if alpha else []) + [str(out)]
    subprocess.run(args, check=True)
    return np.asarray(Image.open(out).convert("RGBA" if alpha else "RGB")).astype(float)


def brass_count(img: np.ndarray, box) -> int:
    x0, y0, x1, y1 = box
    a = img[y0:y1, x0:x1, :3]
    near = (np.abs(a[..., 0] - BRASS[0]) < 30) & (np.abs(a[..., 1] - BRASS[1]) < 30) & (np.abs(a[..., 2] - BRASS[2]) < 40)
    if img.shape[2] == 4:
        near &= img[y0:y1, x0:x1, 3] > 200
    return int(near.sum())


SHOTS = P / "frames"
SHOTS.mkdir(exist_ok=True)

# ── 1. The setting ────────────────────────────────────────────────────────────
print("\n== 1. The setting and the font")
ok(settings.TITLE_STYLE == "site" and editor.look_now() == {"titles": "site", "caption_font": "archivo"},
   f"Site Notes is the default: {editor.look_now()}")
ok(editor.title_style("gallery") == "gallery" and editor.title_style("classic") == "classic"
   and editor.title_style("nope") == "site", "gallery and classic still chosen by name; an unknown name is Site Notes")
settings.TITLE_STYLE = "gallery"
gal_now = editor.look_now()
settings.TITLE_STYLE = "site"
ok(gal_now == {"titles": "gallery", "caption_font": "inter"}, f"STUDIO_TITLE_STYLE=gallery goes back in one setting: {gal_now}")
ok(editor.draft_look({"look": {"titles": "gallery", "caption_font": "inter"}}) == {"titles": "gallery", "caption_font": "inter"}
   and editor.draft_look({"look": {"titles": "site", "caption_font": "inter"}})["caption_font"] == "archivo"
   and editor.draft_look({})["titles"] == "classic", "a draft keeps its look (a gallery draft stays gallery); Site Notes is always Archivo")
files = [editor.FONTS / f"Archivo-latin-{w}.woff2" for w in ("700", "750", "800")]
lic = editor.FONTS / "OFL-Archivo.txt"
ok(all(f.exists() for f in files) and sum(f.stat().st_size for f in files) < 60_000
   and "Open Font License" in lic.read_text() and "Archivo Project Authors" in lic.read_text(),
   f"Archivo bundled, 700/750/800 at 88% width, {sum(f.stat().st_size for f in files) // 1024} KB, with its OFL licence")
ok(editor.THEME["urban_makers"]["brass"] == "#F2B65A" and "--brass:#F2B65A" in editor._theme_css("#f5a524", "site")
   and "--brass" not in editor._theme_css("#f5a524", "gallery"), "brass is in the theme (on a Site Notes page only)")

# ── 2. The overlay page HyperFrames gets ──────────────────────────────────────
print("\n== 2. The overlay page")
CHUNKS = [{"start": 0.3, "end": 1.2, "text": "Which ones are"}, {"start": 1.2, "end": 2.4, "text": "beams, really?", "key": 0},
          {"start": 3.0, "end": 4.6, "text": "inside the DB box", "key": 3},
          {"start": 5.0, "end": 6.8, "text": "for HDBs, 40 amperes"}, {"start": 7.0, "end": 8.8, "text": "dark, even in the daytime"},
          {"start": 9.0, "end": 10.8, "text": "Strip, pour, then tile"}]
GR = [{"style": "callout", "start": 3.0, "duration": 1.9, "title": "Only *16-way*", "lines": ["This board, today"],
       "point": [212, 860]},
      {"style": "card", "start": 5.0, "duration": 1.9, "title": "Power supply by home",
       "lines": ["Older HDB 30A", "HDB 40A", "Condo, landed 63A"]},
      {"style": "card", "start": 7.0, "duration": 1.9, "title": "", "lines": ["Now: dark, even by day", "The plan: lit with our own lights"]},
      {"style": "card", "start": 9.0, "duration": 1.9, "title": "The *screed* goes first",
       "lines": ["Hack the old tiles", "Pour a level screed", "Lay the new tiles"]}]
for g in GR:
    g["title"], g["italic"] = editor.title_parts(g["title"])
END = {"start": 11.0, "duration": 1.8, "ask": "Save this for your next viewing", "handle": "@urbanmakers_sg"}
HOOK = "Tap test: beam or false *ceiling?*"
t0 = time.time()
n0 = len(PAGES)
ov = P / "ov_site.webm"
editor.render_overlay(CHUNKS, HOOK, 12.8, ov, GR, style="site", end=END)
print(f"  overlay rendered in {time.time() - t0:.0f}s")
page = PAGES[n0]
html = (page / "index.html").read_text()
css = html[html.index("<style>"):html.index("</style>")]
ok(faces_of(page) == {"Archivo"} and sorted(p.name for p in page.glob("*.woff2")) ==
   ["archivo-700.woff2", "archivo-750.woff2", "archivo-800.woff2"],
   f"only Archivo is loaded (its three faces): {sorted(faces_of(page))}, {sorted(p.name for p in page.glob('*.woff2'))}")
ok(named_families(css) == {"Archivo"} and not re.search(r"Studio Serif|Instrument|Inter|Studio Caption|Barlow|Plex|Nunito", css),
   f"every rule names Archivo (CJK only as a fallback): {named_families(css)}")
cap = re.search(r"\.cap\{([^}]*)\}", css).group(1)
em = re.search(r"\.cap em\{([^}]*)\}", css).group(1)
ok("font-size:74px" in cap and "font-weight:750" in cap and "color:#fff" in cap
   and "0 2px 4px rgba(0,0,0,.5),0 0 30px rgba(0,0,0,.45)" in cap and "top:1240px" in cap,
   "captions: 74 px, weight 750, white, the soft shadow, where captions always sit")
ok("font-style:normal" in em and "color:var(--brass)" in em and "--brass:#F2B65A" in css
   and '<div id="c1" class="cap clip"' in html and "<em>beams</em>," in html,
   "the key word is brass, not italic (and only the word, not its comma)")
hook = re.search(r'<div id="hook"[^>]*>(.*?)</div></div>', html).group(1)
labels = re.findall(r'<span class="lb"[^>]*>(.*?)</span>', hook)
lb = re.search(r"\.lb\{([^}]*)\}", css).group(1)
ok(len(labels) >= 2 and "font-size:104px" in lb and "font-weight:800" in lb and "rgba(var(--label-rgb),.9)" in lb
   and "--label-rgb:18,17,14" in css and "<em>ceiling</em>?" in hook and "left:64px" in css,
   f"the hook: {len(labels)} stacked dark labels at 104 px 800 from x 64, its key word in brass: {labels}")
ok("scrim" not in html and "capscrim" not in html, "no big shade over the top of the frame (or under the captions)")
g0 = re.search(r'<div id="g0"[^>]*>(.*?)</div></div>', html).group(1)
ok('class="lb br"' in g0 and "This board, today" in g0 and '<path d="M120 ' in g0 and 'class="dot" style="left:212px;top:860px"' in g0,
   "a callout: a brass label and its line on a dark one; it points at the thing (a brass line and dot) when the plan says where")
g1 = html[html.index('id="g1"'):html.index('id="g2"')]
ok('class="panel"' in g1 and g1.count('class="row') == 3 and 'class="row hi"' in g1 and ">63A<" in g1
   and 'width:100%' in g1 and 'width:48%' in g1 and "font-variant-numeric:tabular-nums" in css,
   "numbers: a dark panel with big tabular digits and bars (the largest in brass), not a full-screen card")
g2 = html[html.index('id="g2"'):html.index('id="g3"')]
ok(re.findall(r'class="lb k[^"]*"[^>]*>([^<]*)<', g2) == ["Now", "The plan"] and 'class="lb k br"' in g2,
   "\"Now\" / \"The plan\" as label pairs, the plan's key on brass")
g3 = html[html.index('id="g3"'):html.index('id="endcard"')]
ok('class="panel"' in g3 and g3.count('class="pt"') == 3 and "<em>screed</em>" in g3, "a headline and its points: a panel")
ok(not re.search(r'class="card', html) and ".card" not in css, "no full-screen card in a Site Notes page")
end_css = css[css.index(".endcard{"):]
ok(".endcard .shade{position:absolute;inset:0;background:rgba(var(--label-rgb),.74)}" in end_css
   and "font-weight:800" in end_css and 'style="font-size:118px"' in html and "<em>viewing</em>" in html
   and re.search(r'<span class="hd">@urbanmakers_sg</span>', html) and "background:var(--brass)" in end_css,
   "the end card: a dark shade over the last frame, the ask at 118 px 800 with one brass word, the handle on a brass label")
pip_page_n = len(PAGES)
try:
    editor.render_overlay(CHUNKS[:2], "", 3.0, P / "pip.webm", style="site", pip={"side": "left", "spans": [[0.2, 2.6]]})
except RuntimeError:
    pass
pip_html = (PAGES[pip_page_n] / "index.html").read_text()
import voiceover
room = editor.W - sum(voiceover.caption_room("left"))
widest = max(editor._site_width(w, editor.SITE_PIP_CAPTION_PX, "750", -0.01) for w in
             ("waterproofing", "comfortable", "cornerstone", "amperes,"))
ok(".cap.pipl{left:448px;right:150px;max-width:482px;font-size:60px}" in pip_html and 'class="cap lit pipl clip"' not in pip_html
   and 'class="cap pipl clip"' in pip_html and widest < room,
   f"beside a voice-over's window the captions move beside it, 60 px so a long word fits ({widest:.0f} of {room} px)")

# The gallery and classic looks: exactly the page they were before Site Notes (editor.py at c0aa84f)
old = load_before("editor_c0aa84f.py", "editor_before_site")


def html_of(mod, style):
    holder = {}
    real = render.hyperframes

    def grab(args, **kw):
        holder["html"] = (Path(args[1]) / "index.html").read_text()
        return NS(returncode=1, stderr="", stdout="")
    render.hyperframes = grab
    try:
        mod.render_overlay([{**c, "lit": True} if i == 1 else c for i, c in enumerate(CHUNKS)], HOOK, 12.8, P / "x.webm",
                           [dict(g) for g in GR], style=style, end=END, pip={"side": "left", "spans": [[0.2, 1.0]]},
                           paper=[(9.0, 10.0)])
    except RuntimeError:
        pass
    finally:
        render.hyperframes = real
    return holder["html"]


same = {s: html_of(old, s) == html_of(editor, s) for s in ("gallery", "classic")}
ok(all(same.values()), f"gallery and classic: the overlay page is the same, byte for byte, as before Site Notes {same}")
gal_html = html_of(editor, "gallery")
ok('"Studio Serif"' in gal_html and "Archivo" not in gal_html and "--brass" not in gal_html,
   "a gallery page still loads its serif and Inter, nothing of Site Notes")

# ── 3. The rendered overlay, in pixels ────────────────────────────────────────
print("\n== 3. The rendered overlay")
f_hook = frame(ov, 1.6, SHOTS / "ov_hook.png", alpha=True)
f_call = frame(ov, 4.0, SHOTS / "ov_callout.png", alpha=True)
f_card = frame(ov, 6.0, SHOTS / "ov_card.png", alpha=True)
f_end = frame(ov, 12.6, SHOTS / "ov_end.png", alpha=True)
box = editor.site_layout("hook", editor.title_parts(HOOK)[0])["box"]
inside = f_hook[int(box[1]) + 10:int(box[3]) - 10, 70:200]
ok(inside[..., 3].mean() > 200 and inside[..., :3].mean() < 120 and box[0] == 64 and abs(box[1] - 300) < 1,
   f"the hook's labels are dark and solid from x 64, y 300 (alpha {inside[..., 3].mean():.0f}, level {inside[..., :3].mean():.0f})")
ok(f_hook[150:260, :, 3].max() < 10 and f_hook[int(box[3]) + 30:1200, 600:1000, 3].max() < 10,
   "nothing over the rest of the frame while the hook is up (no shade)")
ok(brass_count(f_hook, (64, 300, 1000, int(box[3]))) > 400 and brass_count(f_hook, (90, 1240, 930, 1460)) > 300,
   f"brass on the hook's key word and the caption's ({brass_count(f_hook, (64, 300, 1000, int(box[3])))}, "
   f"{brass_count(f_hook, (90, 1240, 930, 1460))} px)")
ok(brass_count(f_call, (64, 300, 700, 420)) > 5000 and brass_count(f_call, (100, 500, 260, 900)) > 300,
   "the callout's brass label, and its leader and dot")
pbox = editor.site_layout("card", "Power supply by home", "", GR[1]["lines"])["box"]
ok(f_card[int(pbox[1]) + 20:int(pbox[3]) - 20, 100:980, 3].mean() > 200 and f_card[int(pbox[3]) + 40:1220, :, 3].max() < 10
   and f_card[1600:1900, :, 3].max() < 10 and brass_count(f_card, (64, int(pbox[1]), 1016, int(pbox[3]))) > 2000,
   f"the numbers panel is over the top of the footage (to y {pbox[3]:.0f}); below it the footage shows, not a card")
ok(f_end[..., 3].min() > 150 and f_end[1500:1900, :, :3].mean() < 40 and brass_count(f_end, (64, 560, 1000, 1300)) > 2000,
   "the end card covers the frame with the dark shade (the last frame shows through), brass word and handle")

# ── 4. Drawn inserts and pictures ─────────────────────────────────────────────
print("\n== 4. Drawn inserts and the picture label")
STEPS = {"type": "steps", "title": "Levelling the floor", "items": ["Strip the floor", "Pour a level screed", "Lay the tiles"]}
clean = motion.validate({"scenes": [{**STEPS, "duration": 4.0}]}, insert=True)
reports = {}
for mode in ("cutaway", "panel"):
    proj = motion.build_project(clean, P / f"ins_{mode}", mode, theme="site")
    (P / f"ins_{mode}_shots").mkdir(exist_ok=True)
    reports[mode] = motion.check(proj, clean, P / f"ins_{mode}_shots", theme="site")
    reports[mode]["html"] = (proj / "index.html").read_text()
fams = {m: sorted({f["family"] for f in r["fonts"]}) for m, r in reports.items()}
ok(all(f == ["Archivo"] for f in fams.values()) and all(not r["problems"] for r in reports.values()),
   f"a Site Notes insert loads Archivo only and passes the library's check (safe area, fit): {fams}")
cut_html = reports["cutaway"]["html"]
ok('class="mode-cutaway theme-site"' in cut_html and 'class="shade"' in cut_html and 'class="cut"' not in cut_html
   and 'class="pcard"' not in reports["panel"]["html"] and "Barlow" not in cut_html,
   "on a dark panel over the footage: a cutaway dims the footage under it instead of covering it with paper")
shot = np.asarray(Image.open(sorted((P / "ins_cutaway_shots").glob("*.png"))[0]).convert("RGBA")).astype(float)
ok(30 < shot[1700, 540, 3] < 200 and shot[400, 540, 3] > 200,
   f"the cutaway's footage stays visible (shade alpha {shot[1700, 540, 3]:.0f}), the panel is solid ({shot[400, 540, 3]:.0f})")
mcss = (Path(motion.HERE) / "motion.css").read_text()
site_css = mcss[mcss.index(".theme-site {"):]
ok("--orange: #F2B65A" in site_css and "--sage-deep: #F2B65A" in site_css and '--display: "Archivo"' in site_css
   and '--mono: "Archivo"' in site_css, "the drawings' accents are brass (one accent), every family Archivo")
# The paper theme (gallery and classic Reels, the explainers) draws as it did: the old library's page, the same pixels
oldm = load_before("motion_c0aa84f.py", "motion_before_site")
oldm.THEME_FONTS = motion.THEME_FONTS  # its fonts: where they still are
paper_old = oldm.build_project(clean, P / "paper_old", "cutaway")
(paper_old / "motion.css").write_bytes(__import__("lzma").decompress((Path(__file__).parent / "fixtures" / "before" /
                                                                    "motion_c0aa84f.css.xz").read_bytes()))
paper_new = motion.build_project(clean, P / "paper_new", "cutaway")
(P / "paper_old_shots").mkdir(exist_ok=True)
(P / "paper_new_shots").mkdir(exist_ok=True)
r_old = motion.check(paper_old, clean, P / "paper_old_shots")
r_new = motion.check(paper_new, clean, P / "paper_new_shots")
a = [np.asarray(Image.open(p)).astype(int) for p in sorted((P / "paper_old_shots").glob("*.png"))]
b = [np.asarray(Image.open(p)).astype(int) for p in sorted((P / "paper_new_shots").glob("*.png"))]
diff = max(float(np.abs(x - y).max()) for x, y in zip(a, b)) if a and len(a) == len(b) else 999
ok(diff <= 2 and not r_new["problems"] and sorted({f["family"] for f in r_new["fonts"]}) == ["Barlow", "IBM Plex Mono", "IBM Plex Sans"],
   f"a gallery Reel's insert is drawn exactly as before (largest pixel difference {diff:.0f})")
ok(editor.scene_key({"scene": STEPS, "look": "cutaway", "duration": 4.0}) !=
   editor.scene_key({"scene": STEPS, "look": "cutaway", "duration": 4.0, "titles": "site"})
   and pictures.file_key({"picture": "k", "look": "cutaway", "duration": 3.0}) !=
   pictures.file_key({"picture": "k", "look": "cutaway", "duration": 3.0, "titles": "site"}),
   "a Site Notes insert and picture are kept apart from a gallery one in the cache")
lab = pictures.label_image(titles="site")
la = np.asarray(lab).astype(float)
ok(pictures.SITE_LABEL == "Concept · not a photo" and 40 <= lab.height <= 60 and la[..., :3].mean() < 90
   and la[..., 3].min() > 200 and pictures.label_at("site") == (64, 240),
   f"the picture's label: \"CONCEPT · NOT A PHOTO\" in the caps label style, {lab.width}x{lab.height}, top left at x 64")
PIC = P / "pic.png"
Image.fromarray(np.dstack([np.full((1376, 768), v, np.uint8) for v in (200, 160, 110)])).save(PIC)
pictures.render_one(PIC, "cutaway", 1.2, P / "pic_site.webm", "site")
pictures.render_one(PIC, "cutaway", 1.2, P / "pic_gal.webm")
ps = frame(P / "pic_site.webm", 0.6, SHOTS / "pic_site.png", alpha=True)
pg = frame(P / "pic_gal.webm", 0.6, SHOTS / "pic_gal.png", alpha=True)
ok(ps[..., 3].min() > 250 and abs(ps[120, 900, 0] - ps[900, 900, 0]) < 6 and ps[250:280, 80:500, :3].mean() < 90
   and pg[120, 900, 0] < pg[900, 900, 0] - 15,
   "a picture stays full bleed with no shade over its top (gallery's shade stays), its label at top left")

# ── 5. Whole drafts: Site Notes, then the same plan in gallery ─────────────────
print("\n== 5. Whole drafts, one plan in each look")


def fake_png(seed: str) -> bytes:
    h = int(seed[:6], 16)
    base = np.array([190 + h % 50, 150 + (h >> 8) % 40, 100 + (h >> 16) % 30], dtype=np.float32)
    x = np.arange(768)[None, :, None]
    y = np.linspace(0, 1, 1376)[:, None, None]
    img = base * (0.82 + 0.12 * np.sin(x / 10.0)) * (1.05 - 0.2 * y)
    out = io.BytesIO()
    Image.fromarray(img.clip(0, 255).astype(np.uint8)).save(out, "PNG")
    return out.getvalue()


def fake_reel_picture(brief, subject, ref, client=None, may_draw=True):
    key = hashlib.sha256(brief.encode()).hexdigest()[:20]
    path = P / "concepts" / f"{key}.png"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_bytes(fake_png(key))
    return {"key": key, "path": path, "drawn": 0, "usd": 0.0, "cached": True, "why": ""}


concepts.reel_picture = fake_reel_picture
pictures.available = lambda post_type=None: None


class Fake:
    """Claude: the edit plan given, a clean read, no joins."""

    def __init__(self, plan):
        self.calls, self.plan = [], plan

    @property
    def beta(self):
        return NS(messages=NS(stream=self.stream, create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        sysp = kw.get("system", "")
        if "Place names stay internal" in sysp:
            text = json.dumps({"verdict": "Ready to post", "scores": {k: "good" for k in ("hook", "pace", "sound", "captions", "picture")},
                               "fixes": [], "works": ["ok"]})
        elif "broken sentences" in sysp:
            text = json.dumps({"joins": []})
        else:
            text = json.dumps(self.plan)
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def stream(self, **kw):
        msg = self.create(**kw)

        class Ctx:
            def __enter__(s): return NS(get_final_message=lambda: msg)
            def __exit__(s, *a): return False
        return Ctx()


def fake_track(brief, seconds, out_dir, ref=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    return media.music_bed(out_dir / "bed.mp3", 60), {"title": "Test Bed", "artist": "ffmpeg", "recording_id": "t1"}


CURRENT = {}
transcribe.transcribe = lambda wav: {"words": editor.timeline(CURRENT["cuts"], CL)}
music.allowed_for = lambda b: True
orig_render = editor.render_cut


def counting_render(cuts, clips, out):
    CURRENT["cuts"] = cuts
    return orig_render(cuts, clips, out)


editor.render_cut = counting_render

WORDS = [{k: w[k] for k in ("word", "start", "end")} for w in DATA["words"]]
d = settings.LIBRARY / "2026-10" / "talk"
d.mkdir(parents=True)
v = d / "talk.mp4"
shutil.copy(TALK, v)
meta = extract.probe(v)
extract.extract_frames(v, d / "frames", meta["duration"])
(d / "transcript.json").write_text(json.dumps({"language": "en", "text": " ".join(w["word"] for w in WORDS),
                                               "words": WORDS, "segments": []}), encoding="utf-8")
CID = catalog.start(conn, "fp-talk", v.name, d, v)
catalog.save_tags(conn, CID, meta, {"language": "en", "text": ""},
                  {"brand": "urban_makers", "brand_reason": "", "format": "talking_head", "title": "talk", "summary": "",
                   "topics": [], "setting": "", "usable": True, "quality_notes": "fine", "suggested_hook": "", "moments": []})
CL = {r["id"]: dict(r) for r in conn.execute("select * from clips")}
I = {}
for i, w in enumerate(WORDS):
    I.setdefault(w["word"], i)


def at(word):
    return WORDS[I[word]]["start"]


GRAPHICS = [
    {"clip_id": CID, "at": at("Then"), "duration": 2.4, "style": "callout", "title": "A level *screed*",
     "lines": ["Over the whole room"], "image": "", "scene": "", "look": "cutaway", "role": "explain", "why": ""},
    {"clip_id": CID, "at": at("It"), "duration": 2.5, "style": "picture", "title": "Screed drying", "lines": [],
     "image": "A freshly poured cement screed floor drying in an empty room, soft daylight.",
     "scene": json.dumps({"type": "headline", "text": "Screed drying"}), "look": "cutaway", "role": "explain", "why": "shows it"},
    {"clip_id": CID, "at": at("For"), "duration": 3.2, "style": "card", "title": "Drying time",
     "lines": ["Screed 2 days", "Tile adhesive 1 day"], "image": "", "scene": "", "look": "cutaway", "role": "explain", "why": ""},
    {"clip_id": CID, "at": at("So"), "duration": 2.6, "style": "scene", "title": "Levelling the floor", "lines": [],
     "image": "", "scene": json.dumps(STEPS), "look": "cutaway", "role": "explain", "why": "shows the steps"},
]
PLAN = {"cuts": [{"clip_id": CID, "start": WORDS[0]["start"], "end": WORDS[-1]["end"], "why": "all of it"}],
        "hook_text": "Floors *level* again", "key_words": ["room", "porcelain"],
        "music": {"mood": "warm", "search": "acoustic", "tempo_bpm": 100, "volume_db": -16},
        "summary": "A short talk.", "notes_addressed": [], "missing_shots": [], "graphics": GRAPHICS}


def draft(look: str):
    settings.TITLE_STYLE = look
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": f"Floors {look}", "hook": "h",
                                    "angle": "x", "beats": [],
                                    "segments": [{"clip_id": CID, "start": 0, "end": DATA["total"], "purpose": "a"}],
                                    "caption": "cap", "est_seconds": 25, "cta": "Save this for your next reno."}])
    idea = conn.execute("select max(id) from ideas").fetchone()[0]
    pid = catalog.make_production(conn, idea)
    plan = json.loads(conn.execute("select plan from productions where id=?", (pid,)).fetchone()[0])
    plan["cta"] = "Save this for your next reno."
    conn.execute("update productions set plan=? where id=?", (json.dumps(plan), pid))
    conn.commit()
    fake = Fake(json.loads(json.dumps(PLAN)))
    anthropic.Anthropic = lambda **kw: fake
    n = len(PAGES)
    t0 = time.time()
    final = editor.edit(conn, pid, client=fake, find_track=fake_track)
    print(f"  {look} draft took {time.time() - t0:.1f}s")
    settings.TITLE_STYLE = "site"
    row = dict(conn.execute("select * from productions where id=?", (pid,)).fetchone())
    return pid, final, json.loads(row["edl"]), PAGES[n:]


site_id, site_mp4, site_edl, site_pages = draft("site")
gal_id, gal_mp4, gal_edl, gal_pages = draft("gallery")


def kinds(edl):
    return {g["style"]: g for g in edl["graphics"]}


sg, gg = kinds(site_edl), kinds(gal_edl)
print("  site graphics:", [(g["style"], g["start"], g["duration"], g.get("look"), g.get("titles")) for g in site_edl["graphics"]])
print("  gallery graphics:", [(g["style"], g["start"], g["duration"], g.get("look"), g.get("titles")) for g in gal_edl["graphics"]])
ok(site_edl["look"] == {"titles": "site", "caption_font": "archivo"} and gal_edl["look"]["titles"] == "gallery",
   f"each draft keeps the look it was made in: {site_edl['look']}, {gal_edl['look']}")
ok(set(sg) == set(gg) == {"callout", "picture", "card", "scene"}
   and all(abs(sg[k]["start"] - gg[k]["start"]) < 0.01 for k in sg),
   "the same plan places the same graphics at the same moments in either look (timing untouched)")
ok(sg["scene"].get("titles") == "site" and sg["picture"].get("titles") == "site"
   and "titles" not in gg["scene"] and "titles" not in gg["picture"], "the inserts are drawn in the draft's look")
overlay_pages = [p for p in site_pages if 'data-composition-id="overlay"' in (p / "index.html").read_text()]
insert_pages = [p for p in site_pages if 'data-composition-id="main"' in (p / "index.html").read_text()]
fam_all = set().union(*[faces_of(p) for p in site_pages]) if site_pages else set()
ok(overlay_pages and insert_pages and fam_all == {"Archivo"},
   f"the whole Site Notes draft loads one family: {sorted(fam_all)} ({len(overlay_pages)} overlay, {len(insert_pages)} insert pages)")
ov_html = (overlay_pages[-1] / "index.html").read_text()
ok('class="site clip"' in ov_html and 'class="panel"' in ov_html and 'class="lb br"' in ov_html
   and "<em>level</em>" in ov_html and 'class="endcard clip"' in ov_html and 'class="card' not in ov_html,
   "its overlay: the hook on labels, the callout's brass label, the card as a panel, the end card")
gal_fams = set().union(*[faces_of(p) for p in gal_pages])
ok({"Studio Serif", "Studio Caption"} <= gal_fams and "Archivo" not in gal_fams
   and all("theme-site" not in (p / "index.html").read_text() for p in gal_pages),
   f"the gallery draft: its serif and caption font, paper inserts, nothing of Site Notes ({sorted(gal_fams)})")
# Frames: the card over the footage (site) or filling the screen (gallery)
cs, cg = sg["card"], gg["card"]
fs_card = frame(site_mp4, cs["start"] + 1.4, SHOTS / "site_card.png")
fs_before = frame(site_mp4, cs["start"] - 0.25, SHOTS / "site_before_card.png")
fg_card = frame(gal_mp4, cg["start"] + 1.4, SHOTS / "gal_card.png")
fg_before = frame(gal_mp4, cg["start"] - 0.25, SHOTS / "gal_before_card.png")
low_s = float(np.abs(fs_card[1600:1880] - fs_before[1600:1880]).mean())
low_g = float(np.abs(fg_card[1600:1880] - fg_before[1600:1880]).mean())
ok(low_s < 8 and low_g > 20 and brass_count(fs_card, (64, 250, 1016, 1150)) > 500,
   f"a card is a panel over the footage (below it the footage carries on: {low_s:.1f} levels; gallery's card fills "
   f"the screen: {low_g:.1f})")
# The end card: over the last frame (site) or the account's green (gallery)
close_s, close_g = site_edl["close"]["at"], gal_edl["close"]["at"]
dur_s = float(extract.probe(site_mp4)["duration"])
fe_s, fl_s = frame(site_mp4, dur_s - 0.15, SHOTS / "site_end.png"), frame(site_mp4, close_s - 0.1, SHOTS / "site_last.png")
dur_g = float(extract.probe(gal_mp4)["duration"])
fe_g, fl_g = frame(gal_mp4, dur_g - 0.15, SHOTS / "gal_end.png"), frame(gal_mp4, close_g - 0.1, SHOTS / "gal_last.png")


def corr(a, b):
    a, b = a.mean(axis=2).ravel(), b.mean(axis=2).ravel()
    return float(np.corrcoef(a, b)[0, 1])


# Under the ask and the handle, where the person is (fixtures/person.jpg on a plain wall)
cs_s = corr(fe_s[1150:1330:2, 300:780:2], fl_s[1150:1330:2, 300:780:2])
cs_g = corr(fe_g[1150:1330:2, 300:780:2], fl_g[1150:1330:2, 300:780:2])
ratio = fe_s[1150:1330, 300:780].mean() / max(fl_s[1150:1330, 300:780].mean(), 1)
ok(cs_s > 0.6 and 0.15 < ratio < 0.45 and cs_g < 0.4 and brass_count(fe_s, (64, 560, 1016, 1400)) > 2000,
   f"the end card is the last frame under a dark shade (its picture still there: {cs_s:.2f}, {ratio:.0%} as bright), "
   f"with brass; gallery's is its own card ({cs_g:.2f})")
ok(abs(close_s - close_g) < 0.01 and abs(dur_s - dur_g) < 0.05 and site_edl["close"]["card"]["seconds"] == editor.END_CARD_SECONDS,
   f"the close is timed as before: the card at {close_s:.2f}s for {editor.END_CARD_SECONDS}s, {dur_s:.2f}s in all")
# The picture: full bleed with its label in either look
pk = sg["picture"]
fp = frame(site_mp4, pk["start"] + pk["duration"] / 2, SHOTS / "site_picture.png")
x0, y0 = pictures.label_at("site")
lab_px = fp[y0 + 8:y0 + 46, x0 + 6:x0 + 480]
ok(lab_px.mean() < 110 and fp[700:1100, 300:780, 0].mean() - fp[700:1100, 300:780, 2].mean() > 40,
   "the picture fills the frame with its Concept label at top left")
# The watch-through knows where Site Notes puts things
items = final_check.shown(site_edl, dur_s)
card_box = next(x["box"] for x in items if x["kind"] == "card")
hook_box = next(x["box"] for x in items if x["kind"] == "hook")
call_box = next(x["box"] for x in items if x["kind"] == "callout")
ok(card_box is not None and card_box[1] == editor.SITE_PANEL_TOP and card_box[3] <= editor.CARD_BOTTOM
   and next(x["box"] for x in final_check.shown(gal_edl, dur_g) if x["kind"] == "card") is None
   and hook_box[0] == 64 and hook_box[1] == 300 and call_box[0] == 64,
   f"the watch-through measures Site Notes' boxes (hook {[round(v) for v in hook_box]}, card {[round(v) for v in card_box]}); "
   "gallery's card is still full screen")
ov_hook = re.search(r'<div id="hook"[^>]*>.*?<div class="lbs" style="top:(\d+)px">(.*?)</div></div>', ov_html)
n_lab = len(re.findall(r'class="lb"', ov_hook.group(2)))
est = editor.site_layout("hook", "Floors level again")
ok(n_lab == len(est["items"]) and int(ov_hook.group(1)) == round(est["box"][1]),
   f"the box is the overlay's own layout ({n_lab} label(s) from y {ov_hook.group(1)})")
flags, _ = final_check.watch(final_check._job(conn, dict(conn.execute("select * from productions where id=?",
                                                                       (site_id,)).fetchone())))
print("  watch-through flags:", [(f.get("check"), f["what"]) for f in flags])
ok(not [f for f in flags if f.get("check") in ("too_fast", "late")],
   f"the watch-through finds nothing up too briefly or late on the Site Notes draft: {[f['what'] for f in flags]}")

# ── 6. The release ────────────────────────────────────────────────────────────
print("\n== 6. The release: waiting drafts made again in the new look")
ok(restyle.RELEASE == "2026-10-22 Site Notes graphics" and restyle.RELEASE_FRESH is False and restyle.RELEASE_REELS_ONLY,
   "the release is bumped as a revision (made again, not started over), Reels only")
made = {}
for fmt in ("talking_head", "slideshow", "carousel", "talking_head"):
    catalog.save_ideas(conn, "t", [{"brand": "urban_makers", "format": "talking_head", "title": f"w {fmt}", "hook": "h",
                                    "angle": "x", "beats": [], "segments": [], "caption": "", "est_seconds": 20}])
    pid_x = catalog.make_production(conn, conn.execute("select max(id) from ideas").fetchone()[0])
    conn.execute("update productions set status='ready', format=? where id=?", (fmt, pid_x))
    made[pid_x] = fmt
conn.execute("update productions set status='approved' where id in (?, ?)", (site_id, gal_id))
conn.commit()
(settings.ROOT / "style_release.txt").write_text("2026-10-21 gentle cuts, a proper ending, listen-back and watch-through, started over\n")
sent = []
import notify
notify.send = lambda text, *a, **k: sent.append(text)
n = restyle.run_once(conn)
rows = {i: dict(conn.execute("select status, restyle from productions where id=?", (i,)).fetchone()) for i in made}
reels = [i for i, f in made.items() if f != "carousel"]
carousel_id = next(i for i, f in made.items() if f == "carousel")
ok(n == 3 and all(rows[i]["status"] == "revising" and rows[i]["restyle"] == restyle.AGAIN for i in reels),
   f"the Reels waiting (a slideshow among them) are made again in today's look: {n}")
ok(rows[carousel_id]["status"] == "ready" and not rows[carousel_id]["restyle"]
   and conn.execute("select count(*) from productions where status='rejected'").fetchone()[0] == 0,
   "a carousel (its slides didn't change) is left as it is; nothing rejected")
ok(sent and "Studio's new graphics look (Site Notes) is in" in sent[0] and "making the 3 drafts waiting for review again in it"
   in sent[0], f"Telegram: {sent[0][:160] if sent else None!r}")
ok(restyle.run_once(conn) == 0 and len(sent) == 1, "once per release")
ok(editor.look_now()["titles"] == "site", "a draft made again takes today's look")

# ── 7. The docs ───────────────────────────────────────────────────────────────
print("\n== 7. Docs")
readme = Path(editor.__file__).with_name("README.md").read_text()
envx = Path(editor.__file__).with_name(".env.example").read_text()
qreadme = Path(__file__).with_name("README.md").read_text()
ok("Site Notes" in readme and "STUDIO_TITLE_STYLE=gallery" in readme and "brass" in readme
   and "STUDIO_TITLE_STYLE=" in envx and "site (default" in envx and "suite_look.py" in qreadme,
   "the README, .env.example and the suites' README explain the look and the way back")

strip = os.environ.get("STUDIO_LOOK_STRIP")
if strip:  # each look's frames: hook, a caption with its key word, the callout, the numbers panel, the picture, the end card
    out = Path(strip)
    out.mkdir(parents=True, exist_ok=True)
    for name, mp4, edl in (("site", site_mp4, site_edl), ("gallery", gal_mp4, gal_edl)):
        k = kinds(edl)
        words = editor.timeline(edl["cuts"], CL)
        key_at = next((w["start"] + 0.2 for w in words if editor._core(w["word"]).lower() == "room"), 1.0)
        dur = float(extract.probe(mp4)["duration"])
        times = [1.4, key_at, k["callout"]["start"] + 1.2, k["card"]["start"] + 1.4,
                 k["picture"]["start"] + k["picture"]["duration"] / 2, dur - 0.15]
        for i, t in enumerate(times):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1",
                            "-vf", "scale=360:-2", str(out / f"{name}{i}.png")], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", *sum([["-i", str(out / f"{name}{i}.png")] for i in range(len(times))], []),
                        "-filter_complex", f"hstack=inputs={len(times)}", str(out / ("strip.png" if name == "site" else
                                                                                    "strip_gallery.png"))], check=True)
        shutil.copy(mp4, out / f"{name}.mp4")
        print(f"  {name} strip at {[round(t, 2) for t in times]}")

finish()
