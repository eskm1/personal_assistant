// Carousel slides to PNGs (carousel.py runs this): opens the slides page Studio wrote in the
// headless Chrome HyperFrames already uses, waits for the bundled fonts and the pictures, lets
// the page fit each slide's text (window.studioFit), then saves every <section class="slide">
// as its own 1080x1350 PNG. Nothing is fetched from the network: every request that isn't a
// local file is refused, so a slide can never pull in someone else's image or a web font.
//
//   node shoot.mjs <job.json>
//   job: {chrome, html, out_dir, width, height}
//   prints {slides: [{n, file, fit, overflow, boxes}], fonts: [{family, status}], refused: [...]}
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
  await page.goto(pathToFileURL(job.html).href, { waitUntil: "load", timeout: 120000 });
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((i) => (i.complete ? null : new Promise((ok) => { i.onload = i.onerror = ok; }))));
    return [...document.fonts].map((f) => ({ family: f.family.replace(/"/g, ""), status: f.status }));
  });
  const report = await page.evaluate(() => window.studioFit());
  const slides = [];
  for (const s of report) {
    const el = await page.$(`#slide-${s.n}`);
    const file = join(job.out_dir, `${String(s.n).padStart(2, "0")}.png`);
    await el.screenshot({ path: file, type: "png" });
    slides.push({ ...s, file });
  }
  process.stdout.write(JSON.stringify({ slides, fonts, refused }));
} finally {
  await browser.close();
}
