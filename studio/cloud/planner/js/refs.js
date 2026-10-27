// References and the style guide. The team keeps reels that show the style it wants
// (its own best ones and other accounts'), each with a note on why it works. The Studio
// laptop analyses each one and drafts a style guide from the active set; the owner
// approves it. Routes: #/references, #/references/<id>, #/style, #/style/<brand>, #/taste.
// The imported library (hundreds of posts) and the taste report live in library.js.
import {
  $, $$, BRAND, ACCOUNTS, oneBrand, esc, sb, must, toast, ago, shortDate, plural, httpsUrl, isOwner, ownerName, nameOf, state,
  typingIn, keepDrafts, signPaths, stillUrl, jobView, jobActive, laptopWait, laptopOn, openModal, closeModal,
  modalOpen, formError, store, recall, isTyping,
} from "./core.js";
import {
  isLib, lib, libraryHTML, libVisiblePaths, openImport, loadSignin, libDetailHTML, slidesLookHTML, designHTML, tasteHTML,
  tastePaths, tasteLineHTML,
} from "./library.js";
import { addRefFile, canUpload, studioApi, nameOrder } from "./upload.js";

const root = () => $("#refs");
const BRANDS = ACCOUNTS;
const SIDE = { ours: "Ours", inspiration: "Inspiration" };
const SIDE_LONG = { ours: "Ours: our own reel", inspiration: "Inspiration: another account" };
const JOB_COLS = "id,kind,runner,state,payload,result,error,requested_by,created_at,started_at,updated_at";
// The list's columns: the light parts of each analysis (a few numbers, not transcripts), so
// a library of hundreds of posts stays quick. The reference page loads its full analysis.
const LIGHT = "id,brand,side,url,file_name,title,why,status,added_by,created_at,updated_at,retired_at,account,post_type,purpose,import_id,shortcode,"
  + "reference_analysis(state,error,problem,kind,stills,analysed_at,updated_at,analyser,engagement,"
  + "duration:measured->duration,cuts:measured->cuts,first_cut:measured->first_cut,shot_median:measured->shot_median,"
  + "words_per_sec:measured->words_per_sec,music_under_voice_db:measured->music_under_voice_db,slides:measured->slides,"
  + "captions:look->captions,layouts:look->layouts,standout:look->standout,cover_hook:look->cover_hook)";
const MEASURED_KEYS = ["duration", "cuts", "first_cut", "shot_median", "words_per_sec", "music_under_voice_db", "slides"];
const LOOK_KEYS = ["captions", "layouts", "standout", "cover_hook"];
// The library columns come with a database update (20261023000000_reference_library).
const schemaLib = { on: true };
const WHY_HINT = "e.g. most saves we've had; the reveal at 3 s; fast captions, two or three words at a time";

export const refs = {
  route: null, rows: null, guides: [], jobs: [], taste: [], sig: "", pending: false, editing: null, error: "",
  brand: oneBrand ? "all" : recall("studio.refBrand") || "all", status: recall("studio.refStatus") || "active",
  guideBrand: BRANDS.includes(recall("studio.guideBrand")) ? recall("studio.guideBrand") : "urban_makers",
  flashed: new Set(), own: {}, sending: {},
};
export const refsOpen = () => !root().hidden;

// #/references, #/references/<id>, #/style, #/style/<brand>, or null.
export function refsRoute(hash) {
  let m;
  if (hash === "#/references") return { page: "list" };
  if ((m = hash.match(/^#\/references\/(\d+)$/))) return { page: "detail", id: Number(m[1]) };
  if ((m = hash.match(/^#\/style(?:\/(urban_makers|personal))?$/))) return { page: "style", brand: m[1] || null };
  if (hash === "#/taste") return { page: "taste" };
  return null;
}

export async function showRefs(route) {
  const prev = refs.route;
  const moved = !prev || prev.page !== route.page || prev.id !== route.id;
  if (route.page === "style" && route.brand && BRANDS.includes(route.brand)) {
    refs.guideBrand = route.brand;
    store("studio.guideBrand", route.brand);
  }
  refs.route = route;
  root().hidden = false;
  if (moved) {
    refs.sig = "";
    refs.editing = null;
    window.scrollTo(0, 0);
  }
  if (!refs.rows) root().innerHTML = `<div class="refs-wrap"><p class="muted"><span class="spin"></span> Loading references…</p></div>`;
  else {
    // Show what we have straight away, with all of this reference's stills.
    if (route.page === "detail") await signPaths(byId(route.id)?.stills || []).catch(() => {});
    if (refs.route === route) render(true);
  }
  try {
    await loadRefs(true);
  } catch (err) {
    // The References tables come with an update Bryan applies once (cloud_setup.py --update).
    const missing = /PGRST205|Could not find the table|schema cache/i.test(`${err.code || ""} ${err.message}`);
    if (!refs.rows && missing) root().innerHTML = `<div class="refs-wrap"><p><strong>References and the style guide need a one-time update of the online database.</strong></p>
      <p class="muted">${esc(ownerName())} runs <code>cloud_setup.py --update</code> in the studio folder once; then this page works.</p>
      <div class="row"><button class="btn" id="refs-retry">Try again</button></div></div>`;
    else if (!refs.rows) root().innerHTML = `<div class="refs-wrap"><p class="error-text">Couldn't load the references: ${esc(err.message)}</p>
      <div class="row"><button class="btn" id="refs-retry">Try again</button></div></div>`;
    else toast(`Couldn't refresh: ${err.message}`);
  }
  if (moved) root().querySelector("h1")?.focus({ preventScroll: true });
}

export function hideRefs() {
  if (root().hidden) return;
  root().hidden = true;
  root().innerHTML = "";
  refs.route = null;
  refs.editing = null;
  refs.sig = "";
}

export function resetRefs() {
  hideRefs();
  refs.rows = null;
  refs.guides = [];
  refs.jobs = [];
}

export async function loadRefs(force = false) {
  const route = refs.route;
  if (!route) return;
  const seq = (refs.seq = (refs.seq || 0) + 1);
  // Only a newer load that already landed (or another page) makes this one stale: with a busy
  // database, loads overlap all the time, and dropping each for the next would never show anything.
  const stale = () => refs.route !== route || seq < (refs.doneSeq || 0);
  const refsQuery = async () => {
    if (schemaLib.on) {
      const { data, error } = await sb.from("reference_videos").select(LIGHT).order("created_at", { ascending: false }).order("id", { ascending: false });
      if (!error) return data;
      if (!/42703|PGRST200|column|account|import_id|relationship/i.test(`${error.code || ""} ${error.message}`)) throw error;
      schemaLib.on = false;
    }
    return must(await sb.from("reference_videos").select("*,reference_analysis(*)").order("created_at", { ascending: false }).order("id", { ascending: false }));
  };
  const tasteQuery = async () => {
    if (!schemaLib.on) return [];
    const cols = route.page === "taste" ? "*" : "id,brand,version,status,created_at,approved_at,guide_id";
    const { data, error } = await sb.from("taste_reports").select(cols).order("version", { ascending: false }).limit(route.page === "taste" ? 5 : 3);
    return error ? [] : data;
  };
  const fullQuery = async () => {
    if (route.page !== "detail") return null;
    const { data } = await sb.from("reference_analysis").select("*").eq("reference_id", route.id).maybeSingle();
    return data || null;
  };
  const ownQuery = () => (route.page === "detail" && canUpload()
    ? studioApi("GET", `/api/uploads/references/${route.id}`).then((o) => { refs.own[route.id] = o || {}; }).catch(() => {}) : null);
  const [rows, guides, jobs, taste, full] = await Promise.all([
    refsQuery(),
    sb.from("style_guides").select("*").order("version", { ascending: false }).then(must),
    sb.from("jobs").select(JOB_COLS).in("kind", ["style", "refs", "taste"]).order("id", { ascending: false }).limit(40).then(must),
    tasteQuery(), fullQuery(), route.page === "list" ? loadSignin() : null, ownQuery(),
  ]);
  if (stale()) { if (force) refs.forceNext = true; return; }
  for (const r of rows) {
    let a = Array.isArray(r.reference_analysis) ? r.reference_analysis[0] : r.reference_analysis;
    delete r.reference_analysis;
    if (a && schemaLib.on) {
      a = { ...a, measured: Object.fromEntries(MEASURED_KEYS.filter((k) => a[k] != null).map((k) => [k, a[k]])),
        look: Object.fromEntries(LOOK_KEYS.filter((k) => a[k] != null).map((k) => [k, a[k]])) };
    }
    if (full && r.id === route.id) a = full;
    r.a = a || null;
    r.stills = Array.isArray(a?.stills) ? a.stills.filter((p) => typeof p === "string" && p) : [];
  }
  refs.doneSeq = seq;
  refs.rows = rows;
  refs.taste = taste;
  const paths = route.page === "detail" ? (rows.find((r) => r.id === route.id)?.stills || [])
    : route.page === "taste" ? tastePaths()
    : [...rows.filter((r) => !isLib(r)).map((r) => r.stills[0]), ...libVisiblePaths()];
  await signPaths(paths).catch(() => {});
  if (stale()) { if (force) refs.forceNext = true; return; }
  if (refs.forceNext) { force = true; refs.forceNext = false; }
  refs.guides = guides;
  refs.jobs = jobs;
  render(force);
}
refs.reload = () => loadRefs(true).catch(() => {});
refs.rerender = () => render(true);

// ── Helpers ─────────────────────────────────────────────────────────────────

const byId = (id) => refs.rows?.find((r) => r.id === Number(id)) || null;
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
function fmt(v, digits = 1) {
  const n = num(v);
  return n == null ? "" : String(+n.toFixed(digits));
}
const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v));

function platformOf(url) {
  try {
    const host = new URL(url).hostname.replace(/^(www|m|vm|vt)\./, "");
    if (/(^|\.)instagram\.com$/.test(host)) return "Instagram";
    if (/(^|\.)tiktok\.com$/.test(host)) return "TikTok";
    if (/(^|\.)(youtube\.com|youtu\.be)$/.test(host)) return "YouTube";
  } catch { /* not a URL */ }
  return "";
}
function shortUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/+$/, "");
    const s = u.hostname.replace(/^www\./, "") + path;
    return s.length > 48 ? `${s.slice(0, 47)}…` : s;
  } catch { return String(url || "").slice(0, 48); }
}
// The name a reference goes by: its title, else where it's from.
function refLabel(r) {
  if (!r) return "A deleted reference";
  if (str(r.title)) return str(r.title);
  if (r.url) return shortUrl(r.url);
  return r.file_name || `Reference ${r.id}`;
}
const fromLaptop = (r) => !!r.file_name && !r.url;
function addedBy(r) {
  if (fromLaptop(r)) return `Added from the OneDrive References folder · ${ago(r.created_at)}`;
  return `Added by ${nameOf(r.added_by) || "someone"} · ${ago(r.created_at)}`;
}

function analysisChip(r, long = false) {
  const a = r.a;
  const s = a?.state || "pending";
  if (s === "done") return `<span class="chip ok">Analysed</span>`;
  if (s === "running") return `<span class="chip drafting"><span class="spin"></span>Analysing…</span>`;
  if (s === "held") return `<span class="chip">Waiting for Analyse</span>`;
  if (s === "failed") {
    const full = str(a.error) || "unknown error";
    // Instagram turning the server away says so plainly; the whole reason is on the page.
    const why = long ? full : /needs attention/i.test(full) ? "The sign-in account needs attention"
      : /refusing the server/i.test(full) ? "Instagram is refusing the server right now" : full;
    return `<span class="chip failed" title="${esc(full)}">${long ? "Failed: " : ""}${esc(long || why.length <= 60 ? why : `${why.slice(0, 59)}…`)}</span>`;
  }
  return `<span class="chip wait">Waiting for Studio</span>`;
}
const brandChip = (b) => oneBrand ? "" : `<span class="chip ${esc(b)}">${esc(BRAND[b] || b)}</span>`;

// Numbers worth a glance on a card.
const KEY_NUMBERS = [
  ["shot_median", (v) => `${fmt(v)} s`, "shots"],
  ["first_cut", (v) => `${fmt(v)} s`, "first cut"],
  ["words_per_sec", (v) => fmt(v), "words/s"],
  ["music_under_voice_db", (v) => `${fmt(v, 0)} dB`, "music under voice"],
  ["duration", (v) => `${fmt(v, 0)} s`, "long"],
  ["cuts", (v) => fmt(v, 0), "cuts"],
  ["slides", (v) => fmt(v, 0), "slides"],
];
function statsHTML(m) {
  if (!m || typeof m !== "object") return "";
  const items = KEY_NUMBERS.filter(([k]) => num(m[k]) != null)
    .map(([k, f, label]) => `<span class="stat"><b>${esc(f(m[k]))}</b> ${esc(label)}</span>`);
  return items.length ? `<span class="stats">${items.join("")}</span>` : "";
}

// Every number the laptop measured, labelled.
const NUMBERS = [
  ["duration", "Length", "s"], ["cuts", "Cuts", ""], ["first_cut", "First cut at", "s"],
  ["shot_median", "Typical shot", "s"], ["shot_mean", "Average shot", "s"], ["shot_p90", "Long shots (90th pct)", "s"],
  ["shot_max", "Longest shot", "s"], ["words_per_sec", "Words per second", ""], ["words", "Words", ""],
  ["speech_start", "Talking starts at", "s"], ["tail_after_last_word", "After the last word", "s"],
  ["gap_median", "Typical pause", "s"], ["gap_p90", "Long pauses (90th pct)", "s"], ["long_pauses", "Pauses over 0.4 s", ""],
  ["music_under_voice_db", "Music under voice", "dB"], ["voice_db", "Voice level", "dB"],
  ["loudness_lufs", "Loudness", "LUFS"], ["loudness_true_peak", "True peak", "dBTP"], ["fps", "Frame rate", "fps"],
  ["slides", "Slides", ""], ["caption_words", "Caption words", ""], ["hashtags", "Hashtags", ""],
];
function numbersHTML(m) {
  if (!m || typeof m !== "object") return "";
  const cells = NUMBERS.filter(([k]) => num(m[k]) != null)
    .map(([k, label, unit]) => `<div><dt>${esc(label)}</dt><dd>${esc(fmt(m[k], ["cuts", "words", "long_pauses", "slides", "caption_words", "hashtags"].includes(k) ? 0 : 2))}${unit ? ` <span class="unit">${esc(unit)}</span>` : ""}</dd></div>`);
  if (num(m.width) && num(m.height)) cells.push(`<div><dt>Size</dt><dd>${esc(fmt(m.width, 0))}×${esc(fmt(m.height, 0))}</dd></div>`);
  return cells.length ? `<dl class="ref-nums">${cells.join("")}</dl>` : "";
}

const LAYOUT = {
  talking_full: "talking head, full frame", punch_in: "punch-in", split_broll_top: "split: b-roll on top",
  broll_full: "b-roll, full frame", screen: "screen", text_card: "text card", before_after: "before / after", other: "other",
};
const CASE = { upper: "ALL CAPS", mixed: "Mixed case", lower: "lower case", none: "no case" };
function layoutSummary(layouts) {
  if (!Array.isArray(layouts) || !layouts.length) return "";
  const counts = new Map();
  for (const l of layouts) counts.set(String(l), (counts.get(String(l)) || 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([l, n]) => `${LAYOUT[l] || l} ${Math.round((100 * n) / layouts.length)}%`).join(", ");
}
function captionSummary(c, long = false) {
  if (!c || typeof c !== "object") return "";
  if (c.present === false) return "No captions";
  const parts = long
    ? [str(c.words_per_chunk) && `${str(c.words_per_chunk)} words at a time`, CASE[c.case] || str(c.case), str(c.font),
      Array.isArray(c.accent_colours) && c.accent_colours.length ? `accents: ${c.accent_colours.map(str).join(", ")}` : "",
      str(c.box), str(c.position)]
    : [CASE[c.case] || str(c.case), str(c.font)];
  return parts.filter(Boolean).join("; ");
}
function lookSummaryHTML(look) {
  if (!look || typeof look !== "object") return "";
  const lines = [];
  const cap = captionSummary(look.captions);
  if (cap) lines.push(`<span><b>Captions:</b> ${esc(cap)}</span>`);
  const lay = layoutSummary(look.layouts);
  if (lay) lines.push(`<span><b>Layouts:</b> ${esc(lay)}</span>`);
  if (str(look.standout)) lines.push(`<span class="standout"><b>Stands out:</b> ${esc(str(look.standout))}</span>`);
  return lines.length ? `<span class="ref-look">${lines.join("")}</span>` : "";
}
function lookHTML(look) {
  if (!look || typeof look !== "object") return "";
  const list = (v) => (Array.isArray(v) ? v.map(str).filter(Boolean).join(", ") : str(v));
  const rows = [
    ["Hook (first 3 s)", str(look.hook)], ["First frame", str(look.first_frame)],
    ["Captions", captionSummary(look.captions, true)], ["Layouts", layoutSummary(look.layouts)],
    ["B-roll", list(look.broll)], ["Graphics", str(look.graphics)], ["Call to action", str(look.cta)],
    ["Stands out", str(look.standout)],
  ].filter(([, v]) => v);
  return rows.length ? `<dl class="ref-lookfull">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
}

// A tiny Markdown renderer: headings, bullets, numbered lists, bold, italic, code and
// paragraphs. Everything is escaped first; only these tags are ever added.
function mdInline(s) {
  return String(s).split(/(`[^`\n]+`)/).map((part, k) => {
    if (k % 2) return `<code>${esc(part.slice(1, -1))}</code>`;
    return esc(part)
      .replace(/\*\*(?=\S)(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__(?=\S)(.+?)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*(?=\S)([^*\n]+?)\*(?![*\w])/g, "$1<em>$2</em>")
      .replace(/(^|[^_\w])_(?=\S)([^_\n]+?)_(?![_\w])/g, "$1<em>$2</em>");
  }).join("");
}
export function markdown(text) {
  const out = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) out.push(`<p>${mdInline(para.join(" "))}</p>`); para = []; };
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li${i.sub ? ` class="sub"` : ""}>${mdInline(i.text)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };
  for (const raw of String(text || "").replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?$/))) {
      flushPara(); flushList();
      const level = Math.min(6, m[1].length + 2);
      out.push(`<h${level}>${mdInline(m[2])}</h${level}>`);
      continue;
    }
    if (/^\s{0,3}([-*_])(\s*\1){2,}$/.test(line)) { flushPara(); flushList(); out.push("<hr>"); continue; }
    if ((m = line.match(/^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/))) {
      flushPara();
      const tag = /\d/.test(m[2]) ? "ol" : "ul";
      const sub = m[1].length >= 2;
      if (!list || (list.tag !== tag && !sub)) { flushList(); list = { tag, items: [] }; }
      list.items.push({ text: m[3], sub });
      continue;
    }
    if (list && /^\s/.test(raw)) { list.items[list.items.length - 1].text += ` ${line.trim()}`; continue; }
    flushList();
    para.push(line.replace(/^\s*>\s?/, "").trim());
  }
  flushPara();
  flushList();
  return out.join("") || `<p class="muted">Empty.</p>`;
}

// ── Rendering ───────────────────────────────────────────────────────────────

function render(force = false) {
  const route = refs.route;
  if (!route || !refs.rows) return;
  const lap = laptopOn() ? "on" : Math.floor(Date.parse(state.laptop?.last_seen || 0) / 60e3) || 0;
  const stills = refs.rows.map((r) => (route.page === "detail" && r.id === route.id ? r.stills : r.stills.slice(0, 1)).map((p) => !!stillUrl(p)));
  const sig = JSON.stringify([route, refs.rows, refs.guides, refs.jobs, refs.taste, refs.brand, refs.status, refs.guideBrand, refs.editing, lap, stills,
    state.laptop?.info?.references || null, lib.signin, lib.signinError, lib.signinBusy, lib.type, lib.show, lib.page, refs.own, refs.sending]);
  if (!force && sig === refs.sig) return;
  if (!force && (typingIn(root()) || root().querySelector("select:focus"))) { refs.pending = true; return; }
  refs.sig = sig;
  refs.pending = false;
  const html = route.page === "detail" ? detailHTML(route.id) : route.page === "style" ? styleHTML() : route.page === "taste" ? tasteHTML() : listHTML();
  // Keep open <details> open across re-renders.
  const open = new Set($$("details[data-key][open]", root()).map((d) => d.dataset.key));
  keepDrafts(root(), () => { root().innerHTML = html; });
  for (const d of $$("details[data-key]", root())) if (open.has(d.dataset.key)) d.open = true;
  flashNewDraft();
}

function seg(attr, current, items, label) {
  return `<div class="seg" role="group" aria-label="${esc(label)}">${items.map(([v, text]) =>
    `<button type="button" data-${attr}="${esc(v)}" aria-pressed="${v === current}" class="${v === current ? "on" : ""}">${esc(text)}</button>`).join("")}</div>`;
}

// ── The list ────────────────────────────────────────────────────────────────

function listHTML() {
  const all = refs.rows.filter((r) => !isLib(r));
  const shown = all.filter((r) => (refs.brand === "all" || r.brand === refs.brand)
    && (refs.status === "all" || r.status === refs.status));
  const group = (side, blurb) => {
    const rows = shown.filter((r) => r.side === side);
    return `<section class="ref-group" aria-labelledby="ref-h-${side}">
      <h2 id="ref-h-${side}">${esc(SIDE[side])} <span class="count">${rows.length}</span></h2>
      <p class="muted small">${esc(blurb)}</p>
      ${rows.length ? `<div class="ref-grid">${rows.map(cardHTML).join("")}</div>` : `<p class="muted small ref-empty">None ${refs.status === "retired" ? "retired" : "here"} yet.</p>`}
    </section>`;
  };
  const retired = all.filter((r) => r.status === "retired" && (refs.brand === "all" || r.brand === refs.brand)).length;
  return `<div class="refs-wrap">
    <div class="refs-head">
      <div class="refs-intro">
        <h1 tabindex="-1">References</h1>
        <p class="muted">Reels that show the style we want: our own best ones and other accounts'. Say why each one works. Studio measures them (cuts, pace, captions, music) and drafts the style guide from them.</p>
      </div>
      <div class="row refs-actions"><button class="btn primary" id="ref-add">Add references</button>${schemaLib.on ? `<button class="btn" id="ref-import">Import many…</button>` : ""}<a class="btn" href="#/style">Style guide</a></div>
    </div>
    ${attentionHTML()}
    ${stuckHTML(all)}
    ${guideStatusHTML()}
    ${schemaLib.on ? libraryHTML() : ""}
    ${all.length ? `<div class="refs-filters">
      ${oneBrand ? "" : seg("ref-brand", refs.brand, [["all", "Both accounts"], ...BRANDS.map((b) => [b, BRAND[b]])], "Account")}
      ${seg("ref-status", refs.status, [["active", "Active"], ["retired", `Retired${retired ? ` (${retired})` : ""}`], ["all", "All"]], "Show")}
    </div>
    ${group("ours", "Our own reels that did well.")}
    ${group("inspiration", "Other accounts' reels we'd like ours to feel like.")}`
    : refs.rows.length ? "" : `<div class="plan-empty"><h2>No references yet</h2>
      <p class="muted">Add a reel that shows the style you want, ours or someone else's, and say why it works: “most saves we've had; the reveal at 3 s; fast captions”.</p>
      <button class="btn primary" id="ref-add-empty">Add a reference</button>${schemaLib.on ? ` <button class="btn" id="ref-import-empty">Import a list or CSV…</button>` : ""}</div>`}
  </div>`;
}

function cardHTML(r) {
  const thumb = stillUrl(r.stills[0]);
  const label = refLabel(r);
  const where = r.url ? platformOf(r.url) : "From a file";
  // A link Studio couldn't download: the card offers the way round it (a button can't sit inside the link).
  const fix = r.url && r.a?.state === "failed" && r.status === "active";
  const inner = `<span class="ref-thumb">${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="ref-none">${esc(where || "No still yet")}</span>`}</span>
    <span class="ref-body">
      <span class="ref-title">${esc(label)}</span>
      <span class="card-chips">${brandChip(r.brand)}${analysisChip(r)}</span>
      ${str(r.why) ? `<span class="ref-why">${esc(r.why)}</span>` : `<span class="ref-why none">No note yet on why it works.</span>`}
      <span class="ref-by muted small">${esc(addedBy(r))}</span>
    </span>`;
  if (fix) {
    return `<div class="ref-card ref-card-fix" data-ref-card="${esc(r.id)}">
      <a class="ref-card-link" href="#/references/${esc(r.id)}">${inner}</a>
      <div class="ref-fix">${uploadButtonHTML(r)}
        <span class="muted small">Or drop it in OneDrive: Studio Inbox &gt; References.</span></div>
    </div>`;
  }
  return `<a class="ref-card${r.status === "retired" ? " retired" : ""}" href="#/references/${esc(r.id)}" data-ref-card="${esc(r.id)}">
    <span class="ref-thumb">${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="ref-none">${esc(where || "No still yet")}</span>`}</span>
    <span class="ref-body">
      <span class="ref-title">${esc(label)}</span>
      <span class="card-chips">${brandChip(r.brand)}${r.status === "retired" ? `<span class="chip parked">Retired</span>` : ""}${analysisChip(r)}</span>
      ${str(r.why) ? `<span class="ref-why">${esc(r.why)}</span>` : `<span class="ref-why none">No note yet on why it works.</span>`}
      ${r.a?.state === "done" ? statsHTML(r.a.measured) + lookSummaryHTML(r.a.look) : ""}
      <span class="ref-by muted small">${esc(addedBy(r))}</span>
    </span>
  </a>`;
}

// One line per account: where its style guide stands.
function guideStatusHTML() {
  const lines = BRANDS.map((b) => {
    const { active, draft } = guidesOf(b);
    if (!active && !draft) return "";
    const bits = [];
    if (active) bits.push(`v${esc(active.version)} in use`);
    if (draft) bits.push(`<strong>draft v${esc(draft.version)} ${isOwner() ? "needs your approval" : `waiting for ${esc(ownerName())}'s approval`}</strong>`);
    return `<a class="guide-line" href="#/style/${esc(b)}"><span>${esc(BRAND[b])} style guide: ${bits.join(" · ")}</span><span aria-hidden="true">›</span></a>`;
  }).filter(Boolean);
  return lines.length ? `<div class="guide-lines">${lines.join("")}</div>` : "";
}

// ── One reference ───────────────────────────────────────────────────────────

function detailHTML(id) {
  const r = byId(id);
  const back = `<div class="row"><a class="btn" href="#/references" aria-label="Back to references">‹ References</a></div>`;
  if (!r) {
    return `<div class="refs-wrap">${back}<h1 tabindex="-1">Reference not found</h1><p class="muted">Someone deleted it, or it never existed.</p></div>`;
  }
  const a = r.a || {};
  const href = httpsUrl(r.url);
  const platform = platformOf(r.url);
  const mine = r.added_by === state.me?.id;
  const canDelete = isOwner() || mine;
  const inGuides = refs.guides.filter((g) => (g.reference_ids || []).map(Number).includes(r.id) && (g.status === "active" || g.status === "draft"));
  const stills = r.stills.map((p, k) => [p, stillUrl(p), k]).filter(([, u]) => u);
  let analysis;
  if (a.state === "done") {
    analysis = `${stills.length ? `<div class="ref-strip" role="list" aria-label="Stills">${stills.map(([, u, k]) =>
      `<figure role="listitem"><img src="${esc(u)}" alt="Still ${k + 1}" loading="lazy"><figcaption>${k + 1}</figcaption></figure>`).join("")}</div>`
      : `<p class="muted small">No stills.</p>`}
      ${numbersHTML(a.measured)}
      ${a.kind === "images" || a.measured?.kind === "images" ? slidesLookHTML(a.look) : lookHTML(a.look)}
      ${a.look?.design ? `<details data-key="design"><summary>The look: palette, materials, light, camera, type</summary>${designHTML(a.look.design)}</details>` : ""}
      ${str(a.measured?.caption) ? `<details data-key="caption"><summary>Instagram caption</summary><p class="note-body">${esc(a.measured.caption)}</p></details>` : ""}
      ${str(a.measured?.transcript) ? `<details data-key="transcript"><summary>Transcript</summary><p class="note-body">${esc(a.measured.transcript)}</p></details>` : ""}
      <p class="muted small">Analysed ${esc(ago(a.analysed_at || a.updated_at))}.</p>`;
  } else if (a.state === "failed") {
    const refused = r.url && (a.problem === "rate" || a.problem === "login" || /refusing the server|needs attention/i.test(str(a.error)));
    analysis = `<p class="error-text">Studio couldn't analyse it: ${esc(str(a.error) || "unknown error")}</p>
      <p class="muted small">${refused ? "Studio tries again by itself once Instagram lets it. Or upload the file instead (below)."
        : `${!r.url ? "Check the file in the References folder plays. "
        : /login|private|sign in|cookies/i.test(str(a.error)) ? "Check the link opens without signing in (private accounts can't be downloaded). " : ""}Studio tries again within a day; changing the link analyses it again now.`}</p>`;
  } else if (a.state === "running") {
    analysis = `<p><span class="spin"></span> Studio is analysing it: cuts, pace, music, and a look at a dozen stills.</p>`;
  } else if (a.state === "held") {
    analysis = `<p class="muted">Not analysed yet: it waits for ${esc(isOwner() ? "you" : ownerName())} to press Analyse on the References page.</p>`;
  } else {
    analysis = `<p class="muted"><span class="spin"></span> Waiting for Studio. ${esc(laptopOn() ? "It's on and picks this up within a minute or two." : laptopWait())}</p>`;
  }
  const sideOpts = Object.entries(SIDE_LONG).map(([v, t]) => `<option value="${v}" ${v === r.side ? "selected" : ""}>${esc(t)}</option>`).join("");
  const brandOpts = BRANDS.map((v) => `<option value="${v}" ${v === r.brand ? "selected" : ""}>${esc(BRAND[v])}</option>`).join("");
  return `<div class="refs-wrap ref-detail" data-ref="${esc(r.id)}">
    ${back}
    <div class="ref-detail-head">
      <h1 tabindex="-1" class="sr-only">${esc(refLabel(r))}</h1>
      <div class="field" data-saved-scope>
        <div class="row tight"><label class="muted small" for="ref-title">Name</label><span class="saved" aria-live="polite"></span></div>
        <input id="ref-title" class="title-input" maxlength="200" value="${esc(r.title)}" data-orig="${esc(r.title)}" placeholder="${esc(refLabel(r))}" autocomplete="off">
      </div>
      <div class="card-chips">${brandChip(r.brand)}<span class="chip">${esc(SIDE[r.side] || r.side)}</span>${r.status === "retired" ? `<span class="chip parked">Retired</span>` : ""}${analysisChip(r, true)}</div>
      <p class="ref-source">${href
        ? `<a class="btn" href="${esc(href)}" target="_blank" rel="noopener noreferrer">Open${platform ? ` on ${esc(platform)}` : " the link"} ↗</a> <span class="muted small ref-url">${esc(r.url)}</span>`
        : fromLaptop(r) ? `<span class="muted">Added from the OneDrive References folder: <code>${esc(r.file_name)}</code></span>` : ""}</p>
    </div>
    <div class="ref-detail-grid">
      <div class="ref-col">
        ${isLib(r) ? libDetailHTML(r) : ""}
        <section class="ref-sec" data-saved-scope>
          <h2><label for="ref-why">Why it works</label> <span class="saved" aria-live="polite"></span></h2>
          <textarea id="ref-why" rows="5" maxlength="4000" data-orig="${esc(r.why)}" placeholder="${esc(WHY_HINT)}">${esc(r.why)}</textarea>
          <p class="muted small">Saved when you click away. Be specific: this note is what the style guide learns from.</p>
        </section>
        <section class="ref-sec" data-saved-scope>
          <h2>Details <span class="saved" aria-live="polite"></span></h2>
          <div class="meta-grid">
            <label>Whose reel<select id="ref-side">${sideOpts}</select></label>
            ${oneBrand ? "" : `<label>Account<select id="ref-brand">${brandOpts}</select></label>`}
          </div>
          <p class="muted small">${esc(addedBy(r))}${inGuides.length ? ` · In ${inGuides.map((g) => `${BRAND[g.brand]} style guide v${g.version}${g.status === "draft" ? " (draft)" : ""}`).join(", ")}` : ""}</p>
        </section>
        <section class="ref-sec">
          <h2>${r.status === "retired" ? "Retired" : "Retire"}</h2>
          ${r.status === "retired"
            ? `<p>Retired ${esc(shortDate(r.retired_at) || "")}. It doesn't count toward the style guide any more. Restore it to count it again.</p>
               <div class="row"><button class="btn" id="ref-restore">Restore</button></div>`
            : `<p class="muted">Retire it when it no longer shows the style you want. Retired references stop counting toward the style guide: the next draft leaves it out and says so. You can restore it any time.</p>
               <div class="row"><button class="btn" id="ref-retire">Retire</button></div>`}
          ${canDelete ? `<div class="row"><button class="btn ghost danger" id="ref-delete">Delete…</button>
            <span class="muted small">Gone for good, with its analysis. Retiring is usually better.</span></div>` : ""}
        </section>
      </div>
      <section class="ref-sec ref-analysis">
        <h2>What Studio measured</h2>
        ${analysis}
        ${a.state === "done" && a.engagement?.source === "upload" ? `<p class="muted small">Analysed from the uploaded file; the link stays for its likes and views.</p>` : ""}
      </section>
      ${r.url ? ownFileHTML(r) : ""}
    </div>
  </div>`;
}

// ── The style guide ─────────────────────────────────────────────────────────

function guidesOf(brand) {
  const list = refs.guides.filter((g) => g.brand === brand).sort((a, b) => b.version - a.version);
  const active = list.find((g) => g.status === "active") || null;
  const draft = list.find((g) => g.status === "draft" && g.version > (active?.version || 0)) || null;
  return { list, active, draft };
}
const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));

function refLink(id) {
  const r = byId(id);
  return r ? `<a href="#/references/${esc(r.id)}">${esc(refLabel(r))}</a>` : `<span class="muted">a deleted reference</span>`;
}
function whySnippet(r) {
  const w = str(r?.why).replace(/\s+/g, " ");
  return w ? `<span class="muted">: ${esc(w.length > 110 ? `${w.slice(0, 109)}…` : w)}</span>` : "";
}

// What changed since the active guide was built: references added, retired, deleted.
function sinceGuide(brand, active) {
  const brandRefs = refs.rows.filter((r) => r.brand === brand && r.status === "active" && !isLib(r));
  const current = brandRefs.filter((r) => r.a?.state === "done");
  const curIds = new Set(current.map((r) => r.id));
  const pending = brandRefs.filter((r) => !r.a || r.a.state === "pending" || r.a.state === "running");
  const failed = brandRefs.filter((r) => r.a?.state === "failed");
  const gIds = new Set((active?.reference_ids || []).map(Number));
  const added = current.filter((r) => !gIds.has(r.id));
  const removed = [...gIds].filter((id) => !curIds.has(id)).map((id) => {
    const r = byId(id);
    const why = !r ? "deleted" : r.brand !== brand ? `moved to ${BRAND[r.brand]}` : r.status === "retired" ? "retired" : "being analysed again";
    return { id, r, why };
  });
  return { current, curIds, pending, failed, added, removed };
}

function bannerHTML(brand, active, draft) {
  const s = sinceGuide(brand, active);
  const parts = [];
  const lap = laptopOn() ? "Studio is on it" : "waiting for Studio";
  const pendingLine = s.pending.length ? `<p>${plural(s.pending.length, "reference")} still being analysed (${esc(lap)}).</p>` : "";
  const failedLine = s.failed.length ? `<p class="bad-text">${plural(s.failed.length, "reference")} couldn't be analysed and won't count: ${s.failed.map((r) => refLink(r.id)).join(", ")}.</p>` : "";
  if (!active) {
    if (draft) return pendingLine || failedLine ? `<div class="sg-banner">${pendingLine}${failedLine}</div>` : "";
    return `<div class="sg-banner"><p><strong>No approved ${esc(BRAND[brand])} style guide yet.</strong>
      ${s.current.length ? `${plural(s.current.length, "analysed reference")} ready. Ask for a guide with “Update style guide”, or let Studio draft one by itself.`
        : `Add references and say why they work; once Studio has analysed them it drafts the first guide.`}</p>${pendingLine}${failedLine}</div>`;
  }
  if (s.added.length || s.removed.length) {
    const counts = new Map();
    for (const x of s.removed) counts.set(x.why, (counts.get(x.why) || 0) + 1);
    const summary = [s.added.length ? `${s.added.length} added` : "", ...[...counts].map(([why, n]) => `${n} ${why}`)].filter(Boolean).join(", ");
    const covered = draft && sameSet(new Set((draft.reference_ids || []).map(Number)), s.curIds);
    parts.push(`<p><strong>Since this guide: ${esc(summary)}.</strong></p>
      <ul class="sg-diff">${s.added.map((r) => `<li class="plus"><span aria-hidden="true">+</span> ${refLink(r.id)}${whySnippet(r)}</li>`).join("")}
      ${s.removed.map((x) => `<li class="minus"><span aria-hidden="true">−</span> ${refLink(x.id)} <span class="muted">(${esc(x.why)})</span></li>`).join("")}</ul>
      <p class="muted small">${covered ? `Draft v${esc(draft.version)} below takes these into account.`
        : s.pending.length ? "Studio drafts a new version once they're all analysed."
        : "Studio drafts a new version by itself; “Update style guide” asks it now."}</p>`);
  }
  if (!parts.length && !pendingLine && !failedLine) return "";
  return `<div class="sg-banner">${parts.join("")}${pendingLine}${failedLine}</div>`;
}

function latestJob(brand) {
  return refs.jobs.find((j) => j.kind === "style" && (j.payload?.brand || "urban_makers") === brand) || null;
}
function jobHTML(brand) {
  const job = jobView(latestJob(brand));
  if (!job) return "";
  const seen = recall(`studio.styleJobSeen.${brand}`);
  if (job.state === "queued") return `<p class="job-line"><span class="spin"></span> Asked ${esc(ago(job.created_at))}. ${esc(laptopWait())}</p>`;
  if (job.state === "running") return `<p class="job-line"><span class="spin"></span> Studio is drafting a new style guide. A minute or two.</p>`;
  if (String(job.id) === seen || Date.now() - Date.parse(job.updated_at || job.created_at) > 6 * 3600e3) return "";
  const close = `<button class="icon-btn" data-dismiss-sgjob="${esc(job.id)}" aria-label="Dismiss" title="Dismiss">×</button>`;
  if (job.state === "failed") {
    return `<div class="organise-result bad">${close}<strong>Couldn't update the style guide.</strong> <span>${esc(job.error || "")}</span></div>`;
  }
  const r = job.result || {};
  const g = r.guide_id ? refs.guides.find((x) => x.id === Number(r.guide_id)) : null;
  const after = { active: "is approved and in use", archived: "has since been replaced", rejected: "was rejected" };
  const text = g ? (g.status === "draft" ? `Draft v${esc(g.version)} is ready below.` : `Draft v${esc(g.version)} ${after[g.status] || "is ready"}.`)
    : r.version ? `Draft v${esc(r.version)} is ready.` : esc(str(r.note) || "Nothing new to draft: the guide already matches the references.");
  return `<div class="organise-result">${close}<strong>${text}</strong></div>`;
}

function guideRefsHTML(g, key) {
  const ids = (g.reference_ids || []).map(Number);
  if (!ids.length) return "";
  return `<details data-key="${esc(key)}"><summary>Built from ${plural(ids.length, "reference")}</summary>
    <ul class="sg-refs">${ids.map((id) => `<li>${refLink(id)}${byId(id) ? ` <span class="muted small">(${esc(SIDE[byId(id).side] || "")})</span>` : ""}</li>`).join("")}</ul></details>`;
}

function draftHTML(g) {
  const owner = isOwner();
  const editing = owner && refs.editing === g.id;
  const added = (g.added_ids || []).map(Number), removed = (g.removed_ids || []).map(Number);
  const job = latestJob(g.brand);
  const fresh = job?.state === "done" && Number(job.result?.guide_id) === g.id && !refs.flashed.has(job.id);
  return `<section class="sg-card draft${fresh ? " fresh" : ""}" id="sg-draft" data-guide="${esc(g.id)}" aria-labelledby="sg-draft-h">
    <div class="sg-card-head">
      <h2 id="sg-draft-h">New draft: v${esc(g.version)}</h2>
      <span class="chip wait">Draft</span>
      <span class="muted small">Drafted ${esc(ago(g.created_at))} from ${plural((g.reference_ids || []).length, "reference")}</span>
    </div>
    <div class="sg-changes"><h3>What changed</h3>${str(g.changes) ? `<div class="md">${markdown(g.changes)}</div>` : `<p class="muted">Not said.</p>`}
      ${added.length || removed.length ? `<ul class="sg-diff">${added.map((id) => `<li class="plus"><span aria-hidden="true">+</span> ${refLink(id)}${whySnippet(byId(id))}</li>`).join("")}
        ${removed.map((id) => `<li class="minus"><span aria-hidden="true">−</span> ${refLink(id)}</li>`).join("")}</ul>` : ""}
    </div>
    ${editing
      ? `<div class="sg-edit"><h3><label for="sg-edit-text">The guide (Markdown)</label></h3>
          <textarea id="sg-edit-text" rows="18" maxlength="40000" data-orig="${esc(g.content)}">${esc(g.content)}</textarea>
          <div class="row"><button class="btn good" data-sg-save-approve="${esc(g.id)}">Save and approve</button>
            <button class="btn" data-sg-save="${esc(g.id)}">Save draft</button>
            <button class="btn ghost" data-sg-cancel>Cancel</button></div></div>`
      : `<div><h3>The guide</h3><div class="md sg-content">${markdown(g.content)}</div></div>`}
    ${str(g.report) ? `<details data-key="report-${esc(g.id)}"><summary>The report: what the references show</summary><div class="md">${markdown(g.report)}</div></details>` : ""}
    ${guideRefsHTML(g, `refs-${g.id}`)}
    ${editing ? "" : owner
      ? `<div class="row sg-decide"><button class="btn good" data-sg-approve="${esc(g.id)}">Approve</button>
          <button class="btn" data-sg-edit="${esc(g.id)}">Edit</button>
          <button class="btn ghost danger" data-sg-reject="${esc(g.id)}">Reject</button>
          <span class="muted small">Approving makes it the guide the idea generator and the editor follow.</span></div>`
      : `<p class="sg-wait"><strong>Waiting for ${esc(ownerName())}'s approval.</strong></p>`}
  </section>`;
}

function activeHTML(g) {
  return `<section class="sg-card active" aria-labelledby="sg-active-h">
    <div class="sg-card-head">
      <h2 id="sg-active-h">In use: v${esc(g.version)}</h2>
      <span class="chip ok">Active</span>
      <span class="muted small">Approved${g.approved_by ? ` by ${esc(nameOf(g.approved_by) || "someone")}` : ""}${g.approved_at ? ` on ${esc(shortDate(g.approved_at))}` : ""}</span>
    </div>
    <div class="md sg-content">${markdown(g.content)}</div>
    ${str(g.report) ? `<details data-key="report-${esc(g.id)}"><summary>The report: what the references show</summary><div class="md">${markdown(g.report)}</div></details>` : ""}
    ${str(g.changes) ? `<details data-key="changes-${esc(g.id)}"><summary>What changed in this version</summary><div class="md">${markdown(g.changes)}</div></details>` : ""}
    ${guideRefsHTML(g, `refs-${g.id}`)}
  </section>`;
}

const STATUS = { archived: "Replaced", rejected: "Rejected", draft: "Older draft", active: "Active" };
function historyHTML(list) {
  if (!list.length) return "";
  return `<section class="sg-history" aria-labelledby="sg-hist-h"><h2 id="sg-hist-h">Earlier versions</h2>
    ${list.map((g) => {
      const when = g.approved_at ? `approved ${shortDate(g.approved_at)}` : `drafted ${shortDate(g.created_at)}`;
      return `<details class="sg-hist" data-key="hist-${esc(g.id)}"><summary><strong>v${esc(g.version)}</strong>
        <span class="chip ${g.status === "rejected" ? "failed" : "parked"}">${esc(STATUS[g.status] || g.status)}</span>
        <span class="muted small">${esc(when)} · ${plural((g.reference_ids || []).length, "reference")}</span></summary>
        <div class="sg-hist-body">
          <h3>What changed</h3>${str(g.changes) ? `<div class="md">${markdown(g.changes)}</div>` : `<p class="muted">Not said.</p>`}
          <details data-key="hist-content-${esc(g.id)}"><summary>The guide as it was</summary><div class="md">${markdown(g.content)}</div></details>
        </div></details>`;
    }).join("")}</section>`;
}

function styleHTML() {
  const brand = refs.guideBrand;
  const { list, active, draft } = guidesOf(brand);
  const job = jobView(latestJob(brand));
  const busy = jobActive(job);
  const history = list.filter((g) => g !== active && g !== draft);
  const nActive = refs.rows.filter((r) => r.brand === brand && r.status === "active" && !isLib(r)).length;
  return `<div class="refs-wrap sg">
    <div class="refs-head">
      <div class="refs-intro">
        <h1 tabindex="-1">Style guide</h1>
        <p class="muted">How our reels are edited, drafted by Studio from the references and approved by ${esc(ownerName())}. The idea generator and the editor follow the guide in use.</p>
      </div>
      ${oneBrand ? "" : seg("guide-brand", brand, BRANDS.map((b) => [b, BRAND[b]]), "Account")}
    </div>
    <div class="sg-update">
      <div class="row">
        <button class="btn primary" id="sg-update" ${busy || !nActive ? "disabled" : ""}>${busy ? "Updating…" : "Update style guide"}</button>
        <a class="btn" href="#/references">References</a>
      </div>
      <p class="muted small">${nActive ? "Studio also drafts a new version by itself whenever references are added or retired." : `Add ${esc(BRAND[brand])} references first.`}</p>
      ${jobHTML(brand)}
    </div>
    ${tasteLineHTML()}
    ${bannerHTML(brand, active, draft)}
    ${draft ? draftHTML(draft) : ""}
    ${active ? activeHTML(active) : draft ? "" : `<p class="muted">No ${esc(BRAND[brand])} style guide yet.</p>`}
    ${historyHTML(history)}
  </div>`;
}

// A draft that just arrived from an "Update style guide" job: show it once.
function flashNewDraft() {
  if (refs.route?.page !== "style") return;
  const job = latestJob(refs.guideBrand);
  if (!job || job.state !== "done" || refs.flashed.has(job.id)) return;
  const card = $("#sg-draft.fresh");
  if (!card) return;
  refs.flashed.add(job.id);
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── Adding references ───────────────────────────────────────────────────────

const TRACKING = /^(utm_|igsh|igshid$|si$|is_from_webapp$|sender_device$|_r$|_t$|feature$|share_)/i;
export function parseLink(raw) {
  let s = String(raw || "").trim().replace(/^<|>$/g, "");
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(s)) s = `https://${s}`;
  let u;
  try { u = new URL(s); } catch { return { raw, error: "That isn't a link." }; }
  if (u.protocol === "http:") u.protocol = "https:";
  if (u.protocol !== "https:" || !u.hostname.includes(".") || u.username || u.password) return { raw, error: "Only https:// links to a reel." };
  for (const k of [...u.searchParams.keys()]) if (TRACKING.test(k)) u.searchParams.delete(k);
  u.hash = "";
  const url = u.toString();
  const platform = platformOf(url);
  let warn = "";
  if (!platform) warn = "Not Instagram, TikTok or YouTube: Studio may not be able to download it.";
  else if (platform === "Instagram" && !/\/(reels?|p|tv)\/[^/]+/.test(u.pathname)) warn = "That looks like a profile, not a reel.";
  else if (platform === "YouTube" && !/youtu\.be$/.test(u.hostname) && !/^\/shorts\//.test(u.pathname)) warn = "Not a YouTube Short: it works, but Shorts suit best.";
  return { raw, url, platform, warn, key: url.replace(/\/+$/, "").toLowerCase() };
}
const linkKey = (url) => String(url || "").replace(/\/+$/, "").toLowerCase();

function addRowHTML(p, k, keep) {
  if (p.error) {
    return `<div class="ar-row bad" data-ar-bad><div class="ar-head"><span class="ar-n">${k + 1}</span><span class="ar-link">${esc(p.raw.slice(0, 120))}</span></div>
      <p class="error-text small">${esc(p.error)}</p></div>`;
  }
  const dup = refs.rows?.find((r) => r.url && linkKey(r.url) === p.key);
  const side = keep?.side || recall("studio.refSide") || "inspiration";
  return `<div class="ar-row${dup ? " dup" : ""}" data-ar-url="${esc(p.url)}" data-ar-key="${esc(p.key)}">
    <div class="ar-head"><span class="ar-n">${k + 1}</span><span class="ar-link">${esc(shortUrl(p.url))}</span>
      ${p.platform ? `<span class="chip">${esc(p.platform)}</span>` : ""}</div>
    ${dup ? `<p class="muted small">Already in references: <a href="#/references/${esc(dup.id)}" data-modal-close>${esc(refLabel(dup))}</a>. Skipped.</p>`
      : `${p.warn ? `<p class="ar-warn">${esc(p.warn)}</p>` : ""}
      <label>Whose reel<select data-ar-side>${Object.entries(SIDE_LONG).map(([v, t]) => `<option value="${v}" ${v === side ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      <label>Why it works<textarea data-ar-why rows="3" maxlength="4000" placeholder="${esc(WHY_HINT)}">${esc(keep?.why || "")}</textarea></label>`}
  </div>`;
}

function openAdd() {
  const brand = oneBrand ? BRANDS[0] : refs.brand !== "all" ? refs.brand : (recall("studio.refAddBrand") || "urban_makers");
  openModal(`<h2 id="modal-title">Add references</h2>
    <form class="form" id="ar-form" novalidate>
      <label>Link<textarea id="ar-links" rows="2" maxlength="20000" spellcheck="false" autocomplete="off"
        placeholder="https://www.instagram.com/reel/…  (several? one per line)"></textarea></label>
      <p class="muted small">Instagram reels, TikToks or YouTube Shorts, public. Studio downloads each one, measures its cuts, pace and captions, and keeps a few stills.</p>
      ${oneBrand ? "" : `<label>Account<select id="ar-brand">${BRANDS.map((b) => `<option value="${b}" ${b === brand ? "selected" : ""}>${esc(BRAND[b])}</option>`).join("")}</select></label>`}
      <div class="ar-rows" id="ar-rows"></div>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="ar-save">Add</button></div>
    </form>`, async (e) => {
    if (e.target.closest("#ar-save")) await saveAdd(e.target.closest("#ar-save"));
  }, true);
  const rebuild = () => {
    const keep = new Map($$(".ar-row[data-ar-key]").map((row) => [row.dataset.arKey, {
      side: row.querySelector("[data-ar-side]")?.value, why: row.querySelector("[data-ar-why]")?.value,
    }]));
    const seen = new Set();
    // One link per line; a line may carry other words ("great hook https://…").
    const parsed = $("#ar-links").value.split("\n").map((l) => l.trim()).filter(Boolean)
      .map((l) => parseLink((l.match(/https?:\/\/\S+/i) || [l])[0]))
      .filter((p) => (p.key ? !seen.has(p.key) && seen.add(p.key) : true)).slice(0, 30);
    $("#ar-rows").innerHTML = parsed.map((p, k) => addRowHTML(p, k, p.key && keep.get(p.key))).join("");
    $("#ar-save").textContent = parsed.filter((p) => p.url).length > 1 ? `Add ${parsed.filter((p) => p.url).length}` : "Add";
    $("#ar-save").dataset.confirmed = "";
    formError("");
  };
  $("#ar-links").addEventListener("input", rebuild);
  $("#ar-rows").addEventListener("input", () => { $("#ar-save").dataset.confirmed = ""; });
}

async function saveAdd(btn) {
  const rows = $$(".ar-row[data-ar-url]:not(.dup)").map((row) => ({
    row, url: row.dataset.arUrl, side: row.querySelector("[data-ar-side]").value, why: row.querySelector("[data-ar-why]").value.trim(),
  }));
  if (!$$(".ar-row").length) { $("#ar-links").focus(); return formError("Paste a link to a reel first."); }
  if ($$(".ar-row[data-ar-bad]").length) return formError("Fix or remove the lines that aren't links.");
  if (!rows.length) return formError("Those are all in references already.");
  const missing = rows.filter((r) => !r.why);
  for (const r of rows) r.row.classList.toggle("missing", !r.why);
  if (missing.length && !btn.dataset.confirmed) {
    btn.dataset.confirmed = "1";
    missing[0].row.querySelector("[data-ar-why]").focus();
    return formError(`${missing.length === rows.length && rows.length === 1 ? "It has" : `${plural(missing.length, "link")} ${missing.length === 1 ? "has" : "have"}`} no note on why it works. That note is what the style guide learns from: add one, or press Add again to add without.`);
  }
  btn.disabled = true;
  const brand = $("#ar-brand")?.value || BRANDS[0];
  store("studio.refAddBrand", brand);
  store("studio.refSide", rows[rows.length - 1].side);
  const failed = [];
  let added = 0;
  for (const r of rows) {
    const { error } = await sb.from("reference_videos").insert({ url: r.url, side: r.side, brand, why: r.why });
    if (!error) { added += 1; r.row.remove(); continue; }
    failed.push(error.code === "23505" ? `${shortUrl(r.url)} is already in references.` : `${shortUrl(r.url)}: ${error.message}`);
  }
  btn.disabled = false;
  if (failed.length) {
    formError(`${added ? `Added ${added}. ` : ""}${failed.join(" ")}`);
    if (added) await loadRefs(true).catch(() => {});
    return;
  }
  closeModal();
  toast(`Added ${plural(added, "reference")}. ${laptopOn() ? "Studio analyses them in a minute or two." : "Studio analyses them when it's back."}`, 5000);
  if (refs.status === "retired") { refs.status = "active"; store("studio.refStatus", "active"); }
  await loadRefs(true).catch(() => {});
}

// ── A reference's own file (when Studio can't download the post) ────────────

function uploadButtonHTML(r) {
  const busy = refs.sending[r.id];
  return busy ? `<span class="muted small"><span class="spin"></span> Uploading ${busy.done} of ${plural(busy.n, "file")}…</span>`
    : `<button type="button" class="btn small" data-ref-upload="${esc(r.id)}">${refs.own[r.id]?.kind ? "Upload a different file" : "Upload the file instead"}</button>`;
}

function ownFileHTML(r) {
  const own = refs.own[r.id] || {};
  const what = own.kind === "images" ? plural(own.files, "picture") : "the video";
  return `<section class="ref-sec" aria-labelledby="ref-own-h">
    <h2 id="ref-own-h">Upload the file instead</h2>
    ${own.kind ? `<p>Studio has ${esc(what)}${own.at ? ` (${esc(ago(own.at))})` : ""} and analyses that, not the download.</p>`
      : `<p class="muted">If Studio can't download it, upload the post's video, or its pictures in order. Studio analyses them the same way and keeps the link for likes and views.</p>`}
    <div class="row">${uploadButtonHTML(r)}</div>
    <p class="muted small">Or drop it in OneDrive: Studio Inbox &gt; References &gt; ${esc(SIDE[r.side] || "Inspiration")}, with the post's code${codeOf(r.url) ? ` (${esc(codeOf(r.url))})` : ""} in the file or folder name.</p>
  </section>`;
}
const codeOf = (url) => (String(url || "").match(/instagram\.com\/(?:[A-Za-z0-9_.]+\/)?(?:p|reels?|tv)\/([A-Za-z0-9_-]+)/i) || [])[1] || "";

// Links Studio couldn't download sit below the library's grid: one line at the top goes to them.
function stuckHTML(all) {
  const n = all.filter((r) => r.url && r.status === "active" && r.a?.state === "failed").length;
  return n ? `<button type="button" class="guide-line ref-jump" data-ref-jump><span>${esc(plural(n, "reference"))} couldn't be downloaded: upload the file instead</span><span aria-hidden="true">›</span></button>` : "";
}

// The sign-in looks dead, not just limited (laptop_status.info.references.signin.attention).
function attentionHTML() {
  const at = state.laptop?.info?.references?.signin?.attention;
  if (!at) return "";
  return `<p class="si-warn" role="status">${esc(isOwner() ? at.text : `Instagram downloads wait for ${ownerName()}: the sign-in account needs attention.`)}</p>`;
}

const REF_VIDEO = /\.(mp4|mov|m4v|mkv|avi|webm)$/i;
const REF_PICTURE = /\.(jpe?g|png|webp|heic|heif)$/i;
const refPicker = document.createElement("input");
refPicker.type = "file";
refPicker.accept = "video/*,image/*,.heic,.heif";
refPicker.multiple = true;
refPicker.className = "up-picker";
refPicker.tabIndex = -1;
refPicker.setAttribute("aria-hidden", "true");
document.body.appendChild(refPicker);
let pickRef = null;
refPicker.addEventListener("change", () => {
  const files = [...refPicker.files];
  refPicker.value = "";
  if (pickRef && files.length) sendRefFiles(pickRef, files);
  pickRef = null;
});

// One video, or the post's pictures (in name order): the same upload pieces as a shot's video, then Studio
// is told the set is complete and analyses it.
function sendRefFiles(id, files) {
  const videos = files.filter((f) => /^video\//.test(f.type || "") || REF_VIDEO.test(f.name || ""));
  const pictures = files.filter((f) => !videos.includes(f) && (/^image\//.test(f.type || "") || REF_PICTURE.test(f.name || "")));
  if (videos.length + pictures.length !== files.length || (videos.length && pictures.length) || videos.length > 1) {
    toast("Pick the post's one video, or its pictures (JPEG, PNG, WebP or HEIC).", 6000);
    return;
  }
  if (pictures.length > 20) { toast("A post has at most 20 pictures.", 6000); return; }
  const list = videos.length ? videos : nameOrder(pictures);
  const set = Math.random().toString(36).slice(2, 14).padEnd(12, "0");
  const label = `Reference: ${refLabel(byId(id))}`;
  const sending = (refs.sending[id] = { n: list.length, done: 0 });
  render(true);
  list.forEach((f, k) => addRefFile(id, set, k + 1, f, label, () => {
    sending.done += 1;
    if (sending.done === sending.n) readyRef(id, set, list.length, videos.length > 0);
    else render(true);
  }));
}

async function readyRef(id, set, n, video) {
  try {
    await studioApi("POST", `/api/uploads/references/${id}/ready`, { set });
    const them = video ? "it" : "them";
    toast(`Studio has ${video ? "the video" : plural(n, "picture")}. ${laptopOn() ? `It analyses ${them} in a minute or two.` : `It analyses ${them} when it's back.`}`, 6000);
  } catch (err) {
    toast(`Couldn't use those files: ${err.message}`, 7000);
  }
  delete refs.sending[id];
  delete refs.own[id];
  await loadRefs(true).catch(() => {});
}

// ── Actions ─────────────────────────────────────────────────────────────────

// An update that RLS quietly refused comes back with no rows.
async function updateOne(table, id, fields, what = "save that") {
  const rows = must(await sb.from(table).update(fields).eq("id", id).select("id"));
  if (!rows?.length) throw new Error(`you can't ${what}`);
}

function flashSaved(el) {
  const s = el.closest("[data-saved-scope]")?.querySelector(".saved");
  if (!s) return;
  s.textContent = "Saved";
  s.classList.add("show");
  clearTimeout(s.timer);
  s.timer = setTimeout(() => s.classList.remove("show"), 1600);
}

async function decide(id, fields, message) {
  await updateOne("style_guides", id, fields, "decide on style guides (only the owner can)");
  refs.editing = null;
  toast(message, 5000);
  await loadRefs(true);
}

root().addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  const ds = t.dataset;
  const id = Number(refs.route?.id);
  try {
    if (ds.refJump != null) {
      const card = $(".ref-card-fix");
      card?.scrollIntoView({ behavior: "smooth", block: "center" });
      card?.querySelector("[data-ref-upload]")?.focus({ preventScroll: true });
    } else if (ds.refUpload) {
      if (!canUpload()) { toast(`Uploading needs the Studio address: it appears when Studio is on.`, 6000); return; }
      pickRef = Number(ds.refUpload);
      refPicker.click();
    } else if (t.id === "ref-add" || t.id === "ref-add-empty") openAdd();
    else if (t.id === "ref-import" || t.id === "ref-import-empty") openImport();
    else if (t.id === "refs-retry") await showRefs(refs.route);
    else if (ds.refBrand) { refs.brand = ds.refBrand; store("studio.refBrand", refs.brand); render(true); }
    else if (ds.refStatus) { refs.status = ds.refStatus; store("studio.refStatus", refs.status); render(true); }
    else if (ds.guideBrand) {
      refs.guideBrand = ds.guideBrand;
      store("studio.guideBrand", ds.guideBrand);
      refs.editing = null;
      history.replaceState(null, "", `#/style/${ds.guideBrand}`);
      refs.route = { page: "style", brand: ds.guideBrand };
      render(true);
    } else if (t.id === "ref-retire" || t.id === "ref-restore") {
      const retire = t.id === "ref-retire";
      t.disabled = true;
      await updateOne("reference_videos", id, { status: retire ? "retired" : "active" }, retire ? "retire it" : "restore it");
      toast(retire ? "Retired. It stops counting toward the style guide." : "Restored. It counts toward the style guide again.", 4500);
      await loadRefs(true);
    } else if (t.id === "ref-delete") {
      const r = byId(id);
      if (!confirm(`Delete “${refLabel(r)}” for good? Its analysis goes too. To stop it counting toward the style guide, Retire keeps it instead.`)) return;
      const rows = must(await sb.from("reference_videos").delete().eq("id", id).select("id"));
      if (!rows?.length) throw new Error("only the owner or whoever added it can delete it");
      toast("Reference deleted.");
      location.hash = "#/references";
    } else if (t.id === "sg-update") {
      const brand = refs.guideBrand;
      t.disabled = true;
      const job = must(await sb.from("jobs").insert({ kind: "style", runner: "laptop", state: "queued", payload: { brand } }).select(JOB_COLS).single());
      refs.jobs = [job, ...refs.jobs];
      render(true);
      toast(laptopOn() ? "Asked Studio for a new draft." : "Asked. Studio drafts it when it's back.", 4500);
      await loadRefs(true);
    } else if (ds.dismissSgjob) {
      store(`studio.styleJobSeen.${refs.guideBrand}`, ds.dismissSgjob);
      render(true);
    } else if (ds.sgApprove) {
      const g = refs.guides.find((x) => x.id === Number(ds.sgApprove));
      t.disabled = true;
      await decide(g.id, { status: "active" }, `v${g.version} is the style guide now. Studio writes it into the playbook.`);
    } else if (ds.sgReject) {
      const g = refs.guides.find((x) => x.id === Number(ds.sgReject));
      if (!confirm(`Reject draft v${g.version}? The guide in use stays as it is. Studio drafts again when references change.`)) return;
      await decide(g.id, { status: "rejected" }, `Draft v${g.version} rejected.`);
    } else if (ds.sgEdit) {
      refs.editing = Number(ds.sgEdit);
      render(true);
      const box = $("#sg-edit-text");
      box?.focus();
      box?.setSelectionRange(0, 0);
    } else if (ds.sgCancel != null) {
      const box = $("#sg-edit-text");
      if (box && box.value !== box.dataset.orig && !confirm("Discard your changes to the draft?")) return;
      refs.editing = null;
      render(true);
    } else if (ds.sgSave || ds.sgSaveApprove) {
      const gid = Number(ds.sgSave || ds.sgSaveApprove);
      const g = refs.guides.find((x) => x.id === gid);
      const content = $("#sg-edit-text").value.trim();
      if (!content) { toast("The guide can't be empty."); return; }
      t.disabled = true;
      if (ds.sgSaveApprove) {
        await decide(gid, { content, status: "active" }, `Your edited v${g.version} is the style guide now. Studio writes it into the playbook.`);
      } else {
        await updateOne("style_guides", gid, { content }, "edit style guides (only the owner can)");
        refs.editing = null;
        toast("Draft saved. Approve it when it's right.");
        await loadRefs(true);
      }
    }
  } catch (err) {
    t.disabled = false;
    toast(`Couldn't do that: ${err.message}`, 5000);
  }
});

// Text saves when you click away; the selects save straight away.
root().addEventListener("focusout", async (e) => {
  const el = e.target;
  const key = { "ref-why": "why", "ref-title": "title" }[el.id];
  if (key && el.value !== el.dataset.orig && refs.route?.page === "detail") {
    const id = refs.route.id;
    const value = key === "title" ? el.value.trim() : el.value.replace(/\s+$/, "");
    try {
      await updateOne("reference_videos", id, { [key]: value });
      el.dataset.orig = el.value;
      const r = byId(id);
      if (r) r[key] = value;
      flashSaved(el);
      await loadRefs();
    } catch (err) { toast(`Couldn't save: ${err.message}`); }
  }
  // Catch up on changes held back while typing.
  setTimeout(() => { if (refs.pending && !typingIn(root())) render(); }, 0);
});
root().addEventListener("change", async (e) => {
  const el = e.target;
  const key = { "ref-side": "side", "ref-brand": "brand" }[el.id];
  if (!key || refs.route?.page !== "detail") return;
  try {
    await updateOne("reference_videos", refs.route.id, { [key]: el.value });
    flashSaved(el);
    await loadRefs(true);
  } catch (err) { toast(`Couldn't save: ${err.message}`); }
});

// Escape on a reference goes back to the list.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !refsOpen() || modalOpen() || refs.route?.page !== "detail") return;
  if (isTyping(e.target) || e.target.tagName === "SELECT") { e.target.blur(); return; }
  location.hash = "#/references";
});
