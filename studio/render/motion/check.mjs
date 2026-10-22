// The render check for an explainer (motion.py runs it before HyperFrames renders): opens the page in
// the same headless Chrome with every request that isn't a local file refused, waits for the bundled
// fonts and the timeline, then seeks to moments where each scene is fully on screen and measures
// what's there: text and drawings inside the safe area (clear of Instagram's own top bar, caption and
// buttons), no two pieces of text on top of each other, nothing cut off.
//
//   node check.mjs <job.json>
//   job: {chrome, html, width, height, safe: {left, top, right, bottom}, buttons: {x, y}, times: [{scene, t}],
//         shots: optional folder for a PNG of each moment measured}
//   prints {fonts, refused, problems, fit, frames}
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import puppeteer from "puppeteer-core";

const job = JSON.parse(await readFile(process.argv[2], "utf8"));
const browser = await puppeteer.launch({
  executablePath: job.chrome,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=none", "--hide-scrollbars", "--mute-audio"],
});
const refused = [];
try {
  const page = await browser.newPage();
  await page.setViewport({ width: job.width, height: job.height, deviceScaleFactor: 1 });
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (/^(file|data):/i.test(r.url())) r.continue();
    else { refused.push(r.url().slice(0, 200)); r.abort(); }
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.goto(pathToFileURL(job.html).href, { waitUntil: "load", timeout: 120000 });
  await page.waitForFunction(() => window.__motionReady === true, { timeout: 60000 });
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    // A face the page doesn't use yet stays "unloaded" (Chrome loads fonts lazily), which made an insert that
    // only shows a headline fail as "didn't load": load every bundled face, so the check is about the files
    // (a missing or broken one still ends up "error").
    await Promise.all([...document.fonts].map((f) => f.load().catch(() => null)));
    return [...document.fonts].map((f) => ({ family: f.family.replace(/"/g, ""), weight: f.weight, status: f.status }));
  });
  const fit = await page.evaluate(() => [...document.querySelectorAll(".scene")].map((s) => {
    const st = s.querySelector(".stage");
    return { scene: Number(s.dataset.i), fit: Number(st?.dataset.fit || 1), overflow: st?.dataset.overflow === "1" };
  }));
  const frames = [];
  for (const { scene, t } of job.times) {
    const frame = await page.evaluate(({ scene, t, safe, buttons }) => {
      // Show only the clips HyperFrames would show at t, then seek the timeline there.
      document.querySelectorAll("[data-start][data-duration]").forEach((c) => {
        if (c.hasAttribute("data-composition-id")) return;
        const s = Number(c.dataset.start), d = Number(c.dataset.duration);
        c.style.visibility = t >= s && t < s + d ? "visible" : "hidden";
      });
      const id = document.querySelector("[data-composition-id]").dataset.compositionId;
      window.__timelines[id].seek(t, false);
      const shown = (el) => {
        let o = 1;
        for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
          const cs = getComputedStyle(e);
          if (cs.display === "none" || cs.visibility === "hidden") return 0;
          o *= Number(cs.opacity);
        }
        return o;
      };
      const inkBox = (el) => {
        if (el instanceof SVGElement) return el.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(el);
        const rs = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
        if (!rs.length) return null;
        return { left: Math.min(...rs.map((r) => r.left)), top: Math.min(...rs.map((r) => r.top)),
                 right: Math.max(...rs.map((r) => r.right)), bottom: Math.max(...rs.map((r) => r.bottom)) };
      };
      const root = document.querySelector(`.scene[data-i="${scene}"]`);
      const items = [];
      const add = (el, kind) => {
        if (shown(el) < 0.5) return;
        const r = kind === "text" ? inkBox(el) : el.getBoundingClientRect();
        if (!r || r.right - r.left < 1) return;
        items.push({ kind, text: (el.textContent || el.getAttribute("class") || "").trim().slice(0, 40),
                     box: [r.left, r.top, r.right, r.bottom].map(Math.round), el });
      };
      [...root.querySelectorAll(".t")].forEach((el) => add(el, "text"));
      [...root.querySelectorAll("[data-box]")].forEach((el) => add(el, "figure"));
      const hdr = document.querySelector(".hdr");
      if (hdr && shown(hdr) >= 0.5) [...hdr.querySelectorAll(".t")].forEach((el) => add(el, "text"));
      const problems = [];
      for (const it of items) {
        const [l, tp, r, b] = it.box;
        if (l < safe.left || tp < safe.top || r > safe.right || b > safe.bottom)
          problems.push(`${it.kind} "${it.text}" at ${it.box} is outside the safe area`);
        else if (it.kind === "text" && r > buttons.x && b > buttons.y)
          problems.push(`${it.kind} "${it.text}" at ${it.box} is under Instagram's buttons`);
      }
      const texts = items.filter((i) => i.kind === "text");
      for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
        const a = texts[i], c = texts[j];
        if (a.el.contains(c.el) || c.el.contains(a.el)) continue;
        const w = Math.min(a.box[2], c.box[2]) - Math.max(a.box[0], c.box[0]);
        const h = Math.min(a.box[3], c.box[3]) - Math.max(a.box[1], c.box[1]);
        if (w > 2 && h > 2) problems.push(`text "${a.text}" overlaps "${c.text}"`);
      }
      root.querySelectorAll(".fit").forEach((f) => {
        if (shown(f) >= 0.5 && f.scrollWidth > f.clientWidth + 1) problems.push(`"${f.textContent.trim().slice(0, 40)}" is cut off`);
      });
      return { scene, t, items: items.map(({ el, ...rest }) => rest), problems };
    }, { scene, t, safe: job.safe, buttons: job.buttons });
    if (job.shots) {  // a still of the moment measured (fast previews, without a full render)
      frame.shot = join(job.shots, `scene${String(scene + 1).padStart(2, "0")}-${t.toFixed(2)}.png`);
      await page.screenshot({ path: frame.shot, type: "png", omitBackground: true });
    }
    frames.push(frame);
  }
  const problems = frames.flatMap((f) => f.problems.map((p) => `scene ${f.scene + 1} at ${f.t.toFixed(2)}s: ${p}`));
  fit.filter((f) => f.overflow).forEach((f) => problems.push(`scene ${f.scene + 1}: too much to fit even at the smallest size`));
  errors.forEach((e) => problems.push(`page error: ${e}`));
  process.stdout.write(JSON.stringify({ fonts, refused, problems, fit, frames }));
} finally {
  await browser.close();
}
