// The reference library and the taste report (CONTRACT "Reference library", "Taste report").
// Many Instagram posts imported at once (a pasted list or a CSV) wait, held, until the owner
// presses Analyse: the Studio machine then analyses them one a minute or so, so Instagram
// doesn't block it. The References page shows progress rather than hundreds of cards, a
// paged, filtered grid, the owner's Instagram sign-in for downloads, and the taste report
// (#/taste) Studio writes from all of them, which the owner approves.
import {
  $, $$, esc, sb, must, toast, ago, shortDate, plural, httpsUrl, isOwner, ownerName, state, stillUrl, signPaths,
  laptopOn, laptopWait, jobView, jobActive, openModal, closeModal, formError, store, recall, kindChip,
} from "./core.js";
import { studioApi, studioBase } from "./upload.js";
import { refs, markdown } from "./refs.js";
import { parseImport } from "./refimport.js";

const root = () => $("#refs");
export const isLib = (r) => r?.import_id != null;
export const lib = {
  type: recall("studio.libType") || "all", show: recall("studio.libShow") || "all", q: "", page: 0,
  signin: null, signinError: "", signinBusy: false, signinLoaded: false,
};
const PAGE = 60;
const TYPE = { reel: "Reel", carousel: "Carousel", image: "Photo", video: "Video", series: "Series" };
const TYPES = [["all", "All"], ["reel", "Reels"], ["carousel", "Carousels"], ["image", "Photos"], ["video", "Videos"]];
const SHOWS = [["all", "All"], ["done", "Analysed"], ["waiting", "Waiting"], ["failed", "Couldn't download"]];
const PROBLEM = { gone: "deleted or private", login: "wanted a login", rate: "refused for now", other: "other problems" };
const DEFAULT_EST = { per_post_usd: 0.05, seconds_per_post: 80, sample: 150, report: { base_usd: 0.6, per_chunk_usd: 0.3, chunk: 80 } };
const money = (v) => `US$${(Math.round((Number(v) || 0) * 100) / 100).toFixed(2)}`;
const info = () => state.laptop?.info?.references || null;
const est = () => ({ ...DEFAULT_EST, ...(info()?.estimates || {}) });
function hours(n) {
  const h = (n * est().seconds_per_post) / 3600;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} minutes` : h < 1.5 ? "about an hour" : h < 48 ? `about ${Math.round(h)} hours` : `about ${Math.round(h / 24)} days`;
}
const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v));
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const big = (n) => (n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${+(n / 1e3).toFixed(1)}k` : String(n));

// ── Counts and the progress card ────────────────────────────────────────────

export function libRows() { return (refs.rows || []).filter((r) => isLib(r) && r.status === "active"); }
function counts(rows) {
  const c = { total: rows.length, held: 0, queued: 0, running: 0, done: 0, failed: 0, problems: {}, retryable: 0, waitingNote: 0 };
  for (const r of rows) {
    const s = r.a?.state || "held";
    if (s === "held") c.held += 1;
    else if (s === "pending") { c.queued += 1; if (r.a?.error) c.waitingNote += 1; } else if (s === "running") c.running += 1;
    else if (s === "done") c.done += 1;
    else if (s === "failed") {
      c.failed += 1;
      const p = r.a?.problem || "other";
      c.problems[p] = (c.problems[p] || 0) + 1;
      if (p !== "gone") c.retryable += 1;
    }
  }
  return c;
}
const latestJob = (kind) => (refs.jobs || []).find((j) => j.kind === kind) || null;

function signinHTML() {
  if (!isOwner()) return "";
  const s = lib.signin || info()?.signin || null;
  const attention = s?.attention || info()?.signin?.attention || null;
  const loginTrouble = !!attention || info()?.paused?.kind === "login" || libRows().some((r) => r.a?.problem === "login" && r.a?.state !== "done");
  const where = studioBase();
  const status = s?.present
    ? `<p class="si-on"><span class="chip ok">Signed in for downloads</span> <span class="muted small">added ${esc(shortDate(s.added_at) || ago(s.added_at))}${s.expires ? ` · Instagram ends this sign-in around ${esc(shortDate(s.expires))}` : ""}</span></p>`
    : `<p class="muted">No sign-in yet. Instagram often refuses downloads to servers without one.</p>`;
  // The page's banner says it in full (refs.js attentionHTML); here, what that means for this box.
  const warn = attention ? `<p class="si-warn">Downloads with this sign-in keep failing: Instagram wants the account checked. Open Instagram with it on a phone, clear any check, then upload a fresh cookies.txt below.</p>`
    : loginTrouble ? `<p class="si-warn">${s?.present
    ? "Downloads are failing with “login required”: the sign-in has probably expired. Export a fresh cookies.txt and upload it here."
    : "Downloads are failing with “login required”: add a sign-in below."}</p>` : "";
  return `<details class="si-box" data-key="signin" ${loginTrouble || !s?.present ? "open" : ""}>
    <summary><strong>Instagram sign-in for downloads</strong> <span class="muted small">${s?.present ? "signed in" : "not set"}</span></summary>
    <div class="si-body">
      ${warn}${status}
      <ol class="si-steps">
        <li>Pick a <strong>spare Instagram account</strong> (not Urban Makers' own: if Instagram ever objects, only the spare is affected).</li>
        <li>On a laptop, sign in to it at instagram.com in Chrome or Edge.</li>
        <li>Install the browser extension <strong>“Get cookies.txt LOCALLY”</strong>, open it on instagram.com and export <code>cookies.txt</code>.</li>
        <li>Upload that file here. It goes straight to the Studio machine (never online), only for downloading references.</li>
      </ol>
      ${where === null ? `<p class="muted small">Uploading needs the Studio address: it appears when Studio is on.</p>`
        : `<div class="row"><label class="btn ${s?.present ? "" : "primary"}" for="si-file" tabindex="0">${s?.present ? "Replace…" : "Upload cookies.txt…"}</label>
          <input id="si-file" class="sr-only" type="file" accept=".txt,text/plain">
          ${s?.present ? `<button class="btn ghost danger" id="si-remove">Remove</button>` : ""}
          ${lib.signinBusy ? `<span class="spin"></span>` : ""}</div>`}
      ${lib.signinError ? `<p class="error-text small" role="alert">${esc(lib.signinError)}</p>` : ""}
    </div></details>`;
}

function paceHTML(c) {
  const i = info();
  const bits = [];
  if (c.queued + c.running === 0) return "";
  if (!laptopOn()) return `<p class="lib-pace">Studio is offline: analysing carries on when it's back. ${esc(laptopWait())}</p>`;
  if (i?.paused) return `<p class="lib-pace bad"><strong>Paused.</strong> ${esc(i.paused.reason)}</p>`;
  const now = libRows().find((r) => r.a?.state === "running");
  if (now) bits.push(`Analysing ${esc(now.account ? `@${now.account}` : `#${now.id}`)} now.`);
  if (i?.backing_off && i?.next_at) bits.push(`Instagram asked us to slow down: next try ${esc(new Date(i.next_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}.`);
  else bits.push(`A few an hour (at most ${esc(String(est().per_hour || 4))}), so Instagram keeps trusting the server: ${esc(hours(c.queued + c.running))} left. References you add by hand go first.`);
  return `<p class="lib-pace"><span class="spin"></span> ${bits.join(" ")}</p>`;
}

function jobLine() {
  const j = jobView(latestJob("refs"));
  if (!j) return "";
  if (j.state === "queued") return `<p class="job-line"><span class="spin"></span> Asked ${esc(ago(j.created_at))}. ${esc(laptopWait())}</p>`;
  if (j.state === "running") return `<p class="job-line"><span class="spin"></span> Studio is on it.</p>`;
  if (j.state === "failed" && Date.now() - Date.parse(j.updated_at || j.created_at) < 6 * 3600e3) return `<p class="error-text small">Couldn't do that: ${esc(j.error || "")}</p>`;
  if (j.state === "done" && j.result?.note && Date.now() - Date.parse(j.updated_at || j.created_at) < 3600e3) return `<p class="muted small">${esc(j.result.note)}.</p>`;
  return "";
}

// The progress bar: analysed, couldn't be downloaded, queued (SVG: the page's CSP allows no inline styles).
function barHTML(c) {
  const w = (n) => (c.total ? (1000 * n) / c.total : 0);
  let x = 0;
  const seg = (n, cls) => { const r = `<rect class="${cls}" x="${x.toFixed(1)}" y="0" width="${w(n).toFixed(1)}" height="8"></rect>`; x += w(n); return r; };
  return `<svg class="lib-bar" viewBox="0 0 1000 8" preserveAspectRatio="none" role="img" aria-label="${c.done} of ${c.total} analysed">
    <rect class="bg" x="0" y="0" width="1000" height="8"></rect>${seg(c.done, "done")}${seg(c.failed, "failed")}${seg(c.queued + c.running, "queued")}</svg>`;
}

function progressHTML(c) {
  const e = est(), b = info()?.budget;
  const failedBits = Object.entries(c.problems).sort((a, b2) => b2[1] - a[1]).map(([p, n]) => `${n} ${PROBLEM[p] || p}`).join(", ");
  const line = [`<strong>${c.done} of ${c.total} analysed</strong>`];
  if (c.failed) line.push(`${c.failed} couldn't be downloaded${failedBits ? ` (${esc(failedBits)})` : ""}`);
  if (c.queued + c.running) line.push(`${c.queued + c.running} queued`);
  if (c.held) line.push(`${c.held} waiting for Analyse`);
  const owner = isOwner();
  const busy = jobActive(jobView(latestJob("refs")));
  const paused = !!info()?.paused;
  const sample = Math.min(e.sample || 150, c.held);
  const btns = [];
  if (owner && c.held) {
    btns.push(`<button class="btn primary" data-lib-analyse="all" data-n="${c.held}" ${busy ? "disabled" : ""}>Analyse all ${c.held}</button>`);
    if (c.held > sample) btns.push(`<button class="btn" data-lib-analyse="sample" data-n="${sample}" ${busy ? "disabled" : ""}>Analyse a sample of ${sample} first</button>`);
  }
  if (owner && c.queued + c.running) {
    btns.push(paused ? `<button class="btn" data-lib-act="resume" ${busy ? "disabled" : ""}>Resume</button>` : `<button class="btn" data-lib-act="pause" ${busy ? "disabled" : ""}>Pause</button>`);
    btns.push(`<button class="btn ghost" data-lib-act="stop" ${busy ? "disabled" : ""}>Stop</button>`);
  }
  if (owner && c.retryable && !(c.queued + c.running)) btns.push(`<button class="btn ghost" data-lib-act="retry" ${busy ? "disabled" : ""}>Try the ${c.retryable} again</button>`);
  return `<section class="lib-progress" aria-labelledby="lib-h">
    <div class="lib-top"><h2 id="lib-h">Library <span class="count">${c.total}</span></h2>
      <a class="btn small" href="#/taste">Taste report ›</a></div>
    <p class="lib-line">${line.join(" · ")}</p>
    ${barHTML(c)}
    ${paceHTML(c)}
    ${c.held && owner ? `<p class="muted small">Nothing is downloaded or spent until you press Analyse. All ${c.held}: about ${money(c.held * e.per_post_usd)}, ${esc(hours(c.held))}${c.held > sample ? `; a sample of ${sample} (spread across kinds of post and accounts, at most 2 per account): about ${money(sample * e.per_post_usd)}, ${esc(hours(sample))}` : ""}.</p>` : ""}
    ${c.held && !owner ? `<p class="muted small">${c.held} waiting for ${esc(ownerName())} to press Analyse (it costs about ${money(e.per_post_usd)} a post).</p>` : ""}
    ${btns.length ? `<div class="row lib-btns">${btns.join("")}</div>` : ""}
    ${jobLine()}
    ${b && owner ? `<p class="muted small">This month: ${money(b.spent_usd)} of the ${money(b.cap_usd)} reference budget used.</p>` : ""}
  </section>`;
}

// ── The grid ────────────────────────────────────────────────────────────────

function filtered() {
  const q = lib.q.trim().toLowerCase().replace(/^@/, "");
  return libRows().filter((r) => {
    const s = r.a?.state || "held";
    if (lib.type !== "all" && (r.post_type || "other") !== lib.type) return false;
    if (lib.show === "done" && s !== "done") return false;
    if (lib.show === "waiting" && !["held", "pending", "running"].includes(s)) return false;
    if (lib.show === "failed" && s !== "failed") return false;
    if (q && !(`${r.account} ${r.title} ${r.why}`.toLowerCase().includes(q))) return false;
    return true;
  });
}
function pageRows() {
  const rows = filtered();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  if (lib.page >= pages) lib.page = pages - 1;
  return { rows, slice: rows.slice(lib.page * PAGE, lib.page * PAGE + PAGE), pages };
}
export const libVisiblePaths = () => pageRows().slice.map((r) => r.stills[0]);

function tileHTML(r) {
  const s = r.a?.state || "held";
  const thumb = stillUrl(r.stills[0]);
  const eng = r.a?.engagement || {};
  const chip = s === "done" ? "" : s === "failed" ? `<span class="chip failed" title="${esc(str(r.a?.error))}">${esc(PROBLEM[r.a?.problem] ? `Couldn't: ${PROBLEM[r.a.problem]}` : "Couldn't download")}</span>`
    : s === "running" ? `<span class="chip drafting"><span class="spin"></span>Now</span>` : s === "pending" ? `<span class="chip wait">Queued</span>` : `<span class="chip">Waiting</span>`;
  return `<a class="lib-tile${s === "failed" ? " failed" : ""}" href="#/references/${esc(r.id)}">
    <span class="lib-thumb">${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="ref-none">${esc(TYPE[r.post_type] || "Post")}</span>`}
      ${r.post_type ? `<span class="lib-type">${esc(TYPE[r.post_type] || r.post_type)}</span>` : ""}</span>
    <span class="lib-meta"><span class="lib-acct">${esc(r.account ? `@${r.account}` : `#${r.id}`)}</span>
      ${num(eng.likes) != null ? `<span class="muted small">♥ ${esc(big(eng.likes))}${num(eng.views) ? ` · ▶ ${esc(big(eng.views))}` : ""}</span>` : ""}
      ${chip}</span>
  </a>`;
}
export function gridHTML() {
  const { rows, slice, pages } = pageRows();
  const from = rows.length ? lib.page * PAGE + 1 : 0;
  const pager = pages > 1 ? `<div class="lib-pager row"><button class="btn small" data-lib-page="-1" ${lib.page ? "" : "disabled"} aria-label="Previous page">‹ Previous</button>
    <span class="muted small">${from}–${from + slice.length - 1} of ${rows.length}</span>
    <button class="btn small" data-lib-page="1" ${lib.page < pages - 1 ? "" : "disabled"} aria-label="Next page">Next ›</button></div>` : "";
  return slice.length ? `<div class="lib-grid">${slice.map(tileHTML).join("")}</div>${pager}` : `<p class="muted small ref-empty">Nothing matches.</p>`;
}
function seg(attr, current, items, label) {
  return `<div class="seg" role="group" aria-label="${esc(label)}">${items.map(([v, t]) =>
    `<button type="button" data-${attr}="${esc(v)}" aria-pressed="${v === current}" class="${v === current ? "on" : ""}">${esc(t)}</button>`).join("")}</div>`;
}
export function libraryHTML() {
  const rows = libRows();
  if (!rows.length) return isOwner() ? signinHTML() : "";
  const c = counts(rows);
  return `${progressHTML(c)}${signinHTML()}
    <section class="lib-list" aria-label="Library posts">
      <div class="refs-filters">${seg("lib-type", lib.type, TYPES, "Kind of post")}${seg("lib-show", lib.show, SHOWS, "Show")}
        <input id="lib-q" class="lib-q" type="search" placeholder="Account…" value="${esc(lib.q)}" aria-label="Find an account" autocomplete="off"></div>
      <div id="lib-grid">${gridHTML()}</div>
    </section>`;
}
async function redrawGrid() {
  await signPaths(libVisiblePaths()).catch(() => {});
  const g = $("#lib-grid");
  if (g) g.innerHTML = gridHTML();
}

// ── Importing ───────────────────────────────────────────────────────────────

let parsed = null;
export function openImport() {
  parsed = null;
  openModal(`<h2 id="modal-title">Import many references</h2>
    <form class="form" id="imp-form" novalidate>
      <p class="muted small">Paste links (one per line) or choose a CSV file with a <code>url</code> column (an <code>account</code> and <code>type</code> column are kept too). The same post twice, or one already in references, is skipped. Nothing is downloaded or spent until ${isOwner() ? "you press" : `${esc(ownerName())} presses`} Analyse.</p>
      <label>Whose posts<select id="imp-side"><option value="inspiration">Inspiration: other accounts</option><option value="ours">Ours: our own posts</option></select></label>
      <label>Links<textarea id="imp-text" rows="6" spellcheck="false" autocomplete="off" placeholder="https://www.instagram.com/reel/…&#10;https://www.instagram.com/p/…"></textarea></label>
      <div class="row"><label class="btn" for="imp-file" tabindex="0">Choose a CSV or text file…</label><input id="imp-file" class="sr-only" type="file" accept=".csv,.txt,text/csv,text/plain"><span class="muted small" id="imp-fname"></span></div>
      <div id="imp-preview" class="imp-preview" aria-live="polite"></div>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="imp-go" disabled>Import</button></div>
    </form>`, async (e) => {
    if (e.target.closest("#imp-go")) await doImport(e.target.closest("#imp-go"));
  }, true);
  const update = (text, name) => {
    parsed = parseImport(text);
    parsed.name = name || "Pasted list";
    const existing = new Set((refs.rows || []).map((r) => r.shortcode ? `ig:${r.shortcode}` : String(r.url || "").replace(/\/+$/, "").toLowerCase()));
    parsed.fresh = parsed.rows.filter((r) => !existing.has(r.shortcode ? `ig:${r.shortcode}` : r.url.replace(/\/+$/, "").toLowerCase()));
    const types = {};
    for (const r of parsed.fresh) types[r.post_type || "unknown"] = (types[r.post_type || "unknown"] || 0) + 1;
    const accounts = new Set(parsed.fresh.map((r) => r.account.toLowerCase()).filter(Boolean)).size;
    const already = parsed.rows.length - parsed.fresh.length;
    const tagged = parsed.fresh.filter((r) => r.purpose).length;
    $("#imp-preview").innerHTML = parsed.total ? `<p><strong>${plural(parsed.fresh.length, "new post")}</strong>${accounts ? ` from ${plural(accounts, "account")}` : ""}:
      ${Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, n]) => `${n} ${esc(t === "unknown" ? "of unknown kind" : (TYPE[t] || t).toLowerCase() + (n > 1 ? "s" : ""))}`).join(", ")}.</p>
      <p class="muted small">${[already ? `${already} already in references` : "", parsed.repeated ? `${parsed.repeated} twice in the list` : "", parsed.invalid.length ? `${parsed.invalid.length} lines that aren't post links` : "", tagged ? `${tagged} with a design / content / engagement tag` : ""].filter(Boolean).join(" · ") || "No duplicates."}</p>
      ${parsed.invalid.length ? `<details><summary class="small">Lines skipped</summary><ul class="small">${parsed.invalid.slice(0, 8).map((l) => `<li><code>${esc(l)}</code></li>`).join("")}</ul></details>` : ""}` : "";
    $("#imp-go").disabled = !parsed.fresh.length;
    $("#imp-go").textContent = parsed.fresh.length ? `Import ${parsed.fresh.length}` : "Import";
    formError("");
  };
  $("#imp-text").addEventListener("input", () => { $("#imp-fname").textContent = ""; update($("#imp-text").value); });
  $("#imp-file").addEventListener("change", async () => {
    const f = $("#imp-file").files[0];
    if (!f) return;
    if (f.size > 5 * 1024 ** 2) { formError("That file is too big (5 MB at most)."); return; }
    const text = await f.text();
    $("#imp-fname").textContent = f.name;
    $("#imp-text").value = text.length > 20000 ? `${text.slice(0, 20000)}\n…` : text;
    update(text, f.name.slice(0, 200));
  });
}
async function doImport(btn) {
  if (!parsed?.fresh?.length) return formError("Paste links or choose a file first.");
  btn.disabled = true;
  btn.textContent = "Importing…";
  const side = $("#imp-side").value;
  const rows = parsed.fresh.map(({ url, account, post_type, purpose, why }) => ({ url, account, post_type, purpose, why }));
  const { data, error } = await sb.rpc("import_references", { p_side: side, p_brand: "urban_makers", p_name: parsed.name, p_rows: rows, p_invalid: parsed.invalid.length });
  if (error) {
    btn.disabled = false;
    btn.textContent = `Import ${parsed.fresh.length}`;
    const missing = /PGRST202|Could not find the function|schema cache/i.test(`${error.code || ""} ${error.message}`);
    return formError(missing ? `Importing needs a one-time update of the online database: ${ownerName()} runs cloud_setup.py --update.` : `Couldn't import: ${error.message}`);
  }
  closeModal();
  toast(`Imported ${plural(data.added, "post")}${data.duplicates ? ` (${data.duplicates} already there)` : ""}. ${isOwner() ? "Press Analyse when you're ready: nothing is spent until then." : `${ownerName()} presses Analyse when ready.`}`, 7000);
  await refs.reload?.();
}

// ── Owner actions ───────────────────────────────────────────────────────────

async function queue(kind, payload, message) {
  const job = must(await sb.from("jobs").insert({ kind, runner: "laptop", state: "queued", payload }).select("*").single());
  refs.jobs = [job, ...(refs.jobs || [])];
  toast(laptopOn() ? message : `${message} Studio starts when it's back.`, 6000);
  await refs.reload?.();
}

root().addEventListener("click", async (e) => {
  const jump = e.target.closest("a[data-tr-jump]");
  if (jump) {  // the report's contents: scroll, don't change the page's address
    e.preventDefault();
    $(jump.getAttribute("href"))?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const t = e.target.closest("button");
  if (!t || !root().contains(t)) return;
  const ds = t.dataset;
  try {
    if (ds.libType || ds.libShow) {
      if (ds.libType) { lib.type = ds.libType; store("studio.libType", lib.type); } else { lib.show = ds.libShow; store("studio.libShow", lib.show); }
      lib.page = 0;
      for (const b of $$(ds.libType ? "[data-lib-type]" : "[data-lib-show]", root())) {
        const on = b === t;
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", String(on));
      }
      await redrawGrid();
    } else if (ds.libPage) {
      lib.page = Math.max(0, lib.page + Number(ds.libPage));
      await redrawGrid();
      $(".lib-list")?.scrollIntoView({ block: "start" });
    } else if (ds.libAnalyse) {
      const n = Number(ds.n);
      const each = est().per_post_usd;
      const left = info()?.budget?.left_usd;
      const sample = ds.libAnalyse === "sample";
      if (!confirm(`${sample ? `Analyse a sample of ${n}` : `Analyse all ${n}`}? About ${money(n * each)} and ${hours(n)}.${left != null && n * each > left ? ` Only ${money(left)} is left of this month's budget: Studio stops when it's used.` : ""} Studio downloads them one a minute or so, and you can pause or stop any time.`)) return;
      t.disabled = true;
      await queue("refs", { action: "analyse", mode: sample ? "sample" : "all", n, estimate_usd: Math.round(n * each * 100) / 100, brand: "urban_makers" },
        sample ? `Asked Studio to analyse a sample of ${n}.` : `Asked Studio to analyse all ${n}.`);
    } else if (ds.libAct) {
      const act = ds.libAct;
      if (act === "stop" && !confirm("Stop analysing? The queued posts go back to waiting for Analyse; nothing more is spent.")) return;
      t.disabled = true;
      await queue("refs", { action: act }, { pause: "Pausing.", resume: "Resuming.", stop: "Stopping.", retry: "Trying them again." }[act]);
    } else if (ds.libOne) {
      t.disabled = true;
      await queue("refs", { action: "analyse", mode: "all", ids: [Number(ds.libOne)], n: 1 }, "Asked Studio to analyse this one.");
    } else if (t.id === "si-remove") {
      if (!confirm("Remove Studio's Instagram sign-in? Downloads may then be refused.")) return;
      lib.signinBusy = true;
      lib.signin = await studioApi("DELETE", "/api/uploads/instagram-signin");
      lib.signinBusy = false;
      lib.signinError = "";
      toast("Sign-in removed from the Studio machine.");
      refs.rerender?.();
    } else if (ds.tasteWrite != null) {
      const n = Number(ds.n);
      const r = est().report;
      const cost = r.base_usd + Math.ceil(n / r.chunk) * r.per_chunk_usd;
      if (!confirm(`Write the taste report from ${n} analysed references? About ${money(cost)} and a few minutes.`)) return;
      t.disabled = true;
      await queue("taste", { brand: "urban_makers" }, "Asked Studio for the taste report.");
    } else if (ds.tasteApprove || ds.tasteReject) {
      const id = Number(ds.tasteApprove || ds.tasteReject);
      const rep = refs.taste.find((x) => x.id === id);
      const approve = !!ds.tasteApprove;
      if (!confirm(approve
        ? `Approve taste report v${rep.version}? Its proposed style guide becomes the one in force, and its taste section goes into the playbook the idea generator and editor follow.`
        : `Reject taste report v${rep.version}? Nothing changes: the style guide in use stays as it is.`)) return;
      t.disabled = true;
      const rows = must(await sb.from("taste_reports").update({ status: approve ? "approved" : "rejected" }).eq("id", id).select("id"));
      if (!rows?.length) throw new Error("only the owner can decide on the taste report");
      toast(approve ? `Taste report v${rep.version} approved. Studio puts it in the playbook on its next sync.` : `Taste report v${rep.version} rejected.`, 6000);
      await refs.reload?.();
    }
  } catch (err) {
    t.disabled = false;
    lib.signinBusy = false;
    toast(`Couldn't do that: ${err.message}`, 6000);
  }
});
root().addEventListener("input", (e) => {
  if (e.target.id !== "lib-q") return;
  lib.q = e.target.value;
  lib.page = 0;
  clearTimeout(lib.qTimer);
  lib.qTimer = setTimeout(redrawGrid, 150);
});
root().addEventListener("change", async (e) => {
  if (e.target.id !== "si-file") return;
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  if (f.size > 256 * 1024) { lib.signinError = "That file is too big for a cookies.txt."; refs.rerender?.(); return; }
  lib.signinBusy = true;
  lib.signinError = "";
  refs.rerender?.();
  try {
    lib.signin = await studioApi("PUT", "/api/uploads/instagram-signin", await f.text());
    toast("Signed in for downloads. Studio uses it from the next download.", 5000);
  } catch (err) {
    lib.signinError = err.message || "Couldn't upload it";
  }
  lib.signinBusy = false;
  refs.rerender?.();
});

// The sign-in's status, from the Studio machine (owner only; once per visit to the page).
export async function loadSignin() {
  if (!isOwner() || lib.signinLoaded || studioBase() === null) return;
  lib.signinLoaded = true;
  try { lib.signin = await studioApi("GET", "/api/uploads/instagram-signin"); } catch { lib.signin = null; }
}

// ── One library post (extra lines on the reference page) ───────────────────

export function libDetailHTML(r) {
  if (!r) return "";
  const a = r.a || {};
  const eng = a.engagement || {};
  const bits = [["Likes", eng.likes], ["Comments", eng.comments], ["Views", eng.views], ["Followers", eng.followers]]
    .filter(([, v]) => num(v) != null).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(big(v))}</dd></div>`);
  if (eng.uploaded) bits.push(`<div><dt>Posted</dt><dd>${esc(shortDate(eng.uploaded) || eng.uploaded)}</dd></div>`);
  const head = `${r.account ? `<span class="chip">@${esc(r.account)}</span>` : ""}${r.post_type ? `<span class="chip">${esc(TYPE[r.post_type] || r.post_type)}</span>` : ""}${r.purpose ? `<span class="chip">For ${esc(r.purpose)}</span>` : ""}`;
  const held = (a.state || "held") === "held";
  return `<section class="ref-sec"><h2>In the library</h2>
    <div class="card-chips">${head}</div>
    ${bits.length ? `<dl class="ref-nums">${bits.join("")}</dl>` : a.state === "done" ? `<p class="muted small">Instagram didn't show its likes or views.</p>` : ""}
    ${held ? `<p class="muted small">Imported with the library: waiting for Analyse.</p>${isOwner() ? `<div class="row"><button class="btn" data-lib-one="${esc(r.id)}">Analyse just this one (about ${money(est().per_post_usd)})</button></div>` : ""}` : ""}
    ${a.state === "pending" && a.error ? `<p class="muted small">Queued again: ${esc(a.error)}</p>` : a.state === "pending" ? `<p class="muted small">Queued: Studio analyses the library a few posts an hour.</p>` : ""}
  </section>`;
}
// Claude's read of a carousel or photo post.
export function slidesLookHTML(look) {
  if (!look || typeof look !== "object" || !("cover_hook" in look || "structure" in look)) return "";
  const t = look.typography || {};
  const d = look.design || {};
  const list = (v) => (Array.isArray(v) ? v.map(str).filter(Boolean).join(", ") : str(v));
  const rows = [["Cover hook", str(look.cover_hook)], ["Slides", list(look.structure)], ["Text", str(look.text_density)],
    ["Type", [str(t.headline), str(t.body)].filter(Boolean).join("; ")], ["Closest free fonts", list(t.closest_free_fonts)],
    ["Layout", str(look.layout)], ["Palette", list(d.palette)], ["Materials", list(d.materials)], ["Light", str(d.lighting)],
    ["Call to action", str(look.cta)], ["Stands out", str(look.standout)]].filter(([, v]) => v);
  return rows.length ? `<dl class="ref-lookfull">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
}
export function designHTML(d) {
  if (!d || typeof d !== "object") return "";
  const list = (v) => (Array.isArray(v) ? v.map(str).filter(Boolean).join(", ") : str(v));
  const rows = [["Palette", list(d.palette)], ["Materials", list(d.materials)], ["Light", str(d.lighting)], ["Camera", str(d.camera)],
    ["Framing", str(d.framing)], ["Shots", list(d.shot_types)], ["On-screen type", str(d.text_style)], ["Closest free fonts", list(d.closest_free_fonts)]].filter(([, v]) => v);
  return rows.length ? `<dl class="ref-lookfull">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
}

// ── The taste report (#/taste) ──────────────────────────────────────────────

const WHY = { design: "The look", content: "The idea, hook or structure", engagement: "It clearly did better" };
const FORMAT = { reels: "Reels", carousels: "Carousels", both: "Reels and carousels" };

function exampleIds(sec) {
  const ids = [];
  for (const k of ["design", "content", "engagement"]) for (const e of sec?.[k]?.examples || []) ids.push(e.id);
  for (const r of sec?.recommendations || []) ids.push(...(r.examples || []));
  for (const k of ["design", "content", "engagement"]) for (const e of sec?.notable?.[k] || []) ids.push(e.id);
  return [...new Set(ids.map(Number).filter(Boolean))];
}
export function tastePaths() {
  const rep = (refs.taste || [])[0];
  const by = new Map((refs.rows || []).map((r) => [r.id, r]));
  return exampleIds(rep?.sections).map((id) => by.get(id)?.stills?.[0]).filter(Boolean);
}
function thumb(id, note = "") {
  const r = (refs.rows || []).find((x) => x.id === Number(id));
  if (!r) return "";
  const img = stillUrl(r.stills[0]);
  const href = httpsUrl(r.url);
  const label = r.account ? `@${r.account}` : `#${r.id}`;
  return `<a class="tr-ex" ${href ? `href="${esc(href)}" target="_blank" rel="noopener noreferrer"` : `href="#/references/${esc(r.id)}"`} title="${esc(note || label)}">
    <span class="tr-ex-img">${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : `<span class="ref-none">${esc(TYPE[r.post_type] || "Post")}</span>`}</span>
    <span class="tr-ex-acct">${esc(label)} ↗</span>${note ? `<span class="tr-ex-note">${esc(note)}</span>` : ""}</a>`;
}
const strip = (items) => (items?.length ? `<div class="tr-strip">${items.map((e) => (typeof e === "object" ? thumb(e.id, e.note) : thumb(e))).join("")}</div>` : "");

function compareHTML(stats) {
  const eng = stats?.engagement || {};
  const blocks = [];
  for (const [k, label] of [["video", "Reels and videos"], ["images", "Carousels and photos"]]) {
    const b = eng[k];
    if (!b) continue;
    if (!b.compare) { blocks.push(`<p class="muted small">${esc(label)}: ${esc(b.note || "not enough numbers to compare")} (${b.scored || 0} with ${b.metric === "per_view" ? "views" : "follower counts"}).</p>`); continue; }
    const per = b.metric === "per_view" ? "likes + comments per view" : "likes + comments per follower";
    const val = (v, share) => (typeof v !== "number" ? "–" : share ? `${Math.round(v * 100)}%` : String(v));
    blocks.push(`<div class="tr-cmp"><h4>${esc(label)}: the top quarter vs the rest</h4>
      <p class="muted small">${b.scored} posts with the numbers, ranked by ${esc(per)} (top quarter median ${esc(String(b.top_median_rate))}, the rest ${esc(String(b.rest_median_rate))}).</p>
      <div class="tr-rows">${Object.entries(b.compare).map(([f, v]) => `<div><span>${esc(f)}</span><b>${esc(val(v.top, v.share))}</b><span class="muted">vs ${esc(val(v.rest, v.share))}</span></div>`).join("")}</div></div>`);
  }
  const cov = eng.coverage;
  if (cov) blocks.push(`<p class="muted small">Numbers Instagram gave: likes for ${cov.likes}, comments ${cov.comments}, views ${cov.views}, followers ${cov.followers} of ${stats.posts} posts.</p>`);
  return blocks.join("");
}

function reportHTML(rep) {
  const s = rep.sections || {};
  const owner = isOwner();
  const status = { draft: `<span class="chip wait">Waiting for ${owner ? "your" : `${esc(ownerName())}'s`} approval</span>`, approved: `<span class="chip ok">Approved</span>`,
    archived: `<span class="chip parked">Replaced</span>`, rejected: `<span class="chip failed">Rejected</span>` }[rep.status] || "";
  const sec = (key, n, title) => `<section class="tr-sec" id="tr-${key}"><h2>${n}. ${esc(title)}</h2>
    <div class="md">${markdown(s[key]?.text || "")}</div>${key === "engagement" ? compareHTML(s.stats) : ""}${strip(s[key]?.examples)}</section>`;
  const recs = (s.recommendations || []).map((r, k) => `<li class="tr-rec"><div class="tr-rec-head"><span class="tr-rank">${k + 1}</span><strong>${esc(r.title)}</strong></div>
      <div class="card-chips"><span class="chip">${esc(FORMAT[r.format] || r.format)}</span>${(r.post_kinds || []).map(kindChip).join("")}</div>
      <p>${esc(r.detail)}</p><p class="muted small">Evidence: ${esc(r.evidence)}</p>${strip(r.examples)}</li>`).join("");
  const fonts = (s.fonts || []).map((f) => `<div class="tr-font"><b>${esc(f.closest_free_font)}</b> <span class="muted small">${esc(f.licence)}</span>
      <span>${esc(f.seen)}</span><span class="muted small">For ${esc(f.use_for)}</span></div>`).join("");
  const settings = (s.settings || []).map((x) => `<div class="tr-set"><code>${esc(x.setting)}</code><span><span class="muted">now</span> ${esc(x.now)} → <b>${esc(x.proposed)}</b></span><span class="muted small">${esc(x.why)}</span></div>`).join("");
  const notable = ["design", "content", "engagement"].filter((k) => s.notable?.[k]?.length).map((k) =>
    `<div class="tr-why"><h4>${esc(WHY[k])} <span class="count">${s.notable[k].length}</span></h4>${strip(s.notable[k].slice(0, 12))}</div>`).join("");
  return `<article class="tr" aria-labelledby="tr-h">
    <div class="sg-card-head"><h2 id="tr-h">Taste report v${esc(rep.version)}</h2>${status}
      <span class="muted small">From ${plural((rep.reference_ids || []).length, "reference")} · ${esc(ago(rep.created_at))}${rep.usage?.cost_usd != null ? ` · cost ${money(rep.usage.cost_usd)}` : ""}</span></div>
    <nav class="tr-toc" aria-label="Sections"><a href="#tr-design" data-tr-jump>Design</a><a href="#tr-content" data-tr-jump>Content</a><a href="#tr-engagement" data-tr-jump>Engagement</a><a href="#tr-recs" data-tr-jump>Recommendations</a><a href="#tr-guide" data-tr-jump>Proposed guide</a></nav>
    ${rep.status === "draft" ? (owner ? `<div class="tr-decide"><p><strong>Nothing changes until you approve.</strong> Approving puts the proposed style guide in force and adds the taste section to the playbook.</p>
      <div class="row"><button class="btn good" data-taste-approve="${esc(rep.id)}">Approve</button><button class="btn ghost danger" data-taste-reject="${esc(rep.id)}">Reject</button></div></div>`
      : `<p class="sg-wait"><strong>Waiting for ${esc(ownerName())}'s approval.</strong></p>`) : ""}
    <section class="tr-sec"><h2>Summary</h2><div class="md">${markdown(s.summary || "")}</div></section>
    ${sec("design", 1, "Design")}
    ${fonts ? `<section class="tr-sec"><h2>Fonts we could bundle</h2><div class="tr-fonts">${fonts}</div></section>` : ""}
    ${sec("content", 2, "Content")}
    ${sec("engagement", 3, "Engagement")}
    <section class="tr-sec" id="tr-recs"><h2>Recommendations for Urban Makers</h2><ol class="tr-recs">${recs}</ol></section>
    ${settings ? `<section class="tr-sec"><h2>Editor and carousel settings to change</h2><div class="tr-sets">${settings}</div><p class="muted small">Settings change only when ${esc(ownerName())} asks for it: approving doesn't change them.</p></section>` : ""}
    ${notable ? `<section class="tr-sec"><h2>Why these stand out</h2><p class="muted small">Inferred by Studio from each post: the team didn't tag them.</p>${notable}</section>` : ""}
    <section class="tr-sec" id="tr-guide"><h2>Proposed style guide</h2><div class="md sg-content">${markdown(s.guide_update || "")}</div>
      <details data-key="taste-section"><summary>Proposed taste section for the playbook</summary><div class="md">${markdown(rep.taste_section || s.taste_section || "")}</div></details></section>
  </article>`;
}

export function tasteHTML() {
  const reps = refs.taste || [];
  const latest = reps[0];
  const done = (refs.rows || []).filter((r) => r.status === "active" && r.a?.state === "done").length;
  const job = jobView(latestJob("taste"));
  const busy = jobActive(job);
  const r = est().report;
  const cost = r.base_usd + Math.ceil(Math.max(done, 1) / r.chunk) * r.per_chunk_usd;
  const jobText = !job ? "" : job.state === "queued" ? `<p class="job-line"><span class="spin"></span> Asked ${esc(ago(job.created_at))}. ${esc(laptopWait())}</p>`
    : job.state === "running" ? `<p class="job-line"><span class="spin"></span> Studio is reading the library and writing the report: a few minutes.</p>`
    : job.state === "failed" && Date.now() - Date.parse(job.updated_at || job.created_at) < 6 * 3600e3 ? `<p class="error-text small">Couldn't write it: ${esc(job.error || "")}</p>` : "";
  return `<div class="refs-wrap sg tr-page">
    <div class="row"><a class="btn" href="#/style">‹ Style guide</a><a class="btn" href="#/references">References</a></div>
    <div class="refs-head"><div class="refs-intro"><h1 tabindex="-1">Taste report</h1>
      <p class="muted">What the posts the team likes have in common (design, content, engagement), and what Urban Makers should change because of them. Written by Studio from every analysed reference; ${esc(ownerName())} approves it.</p></div></div>
    ${isOwner() ? `<div class="sg-update"><div class="row"><button class="btn primary" data-taste-write data-n="${done}" ${busy || done < 10 ? "disabled" : ""}>${busy ? "Writing…" : latest ? "Write a new taste report" : "Write the taste report"}</button></div>
      <p class="muted small">${done < 10 ? `It needs at least 10 analysed references (${done} so far).` : `From ${plural(done, "analysed reference")}: about ${money(cost)}.`}</p>${jobText}</div>` : jobText}
    ${latest ? reportHTML(latest) : `<p class="muted">No taste report yet.${done >= 10 ? "" : " Analyse the library on the References page first."}</p>`}
    ${reps.length > 1 ? `<section class="sg-history"><h2>Earlier reports</h2>${reps.slice(1).map((x) => `<p class="muted small">v${esc(x.version)} · ${esc(x.status)} · ${esc(shortDate(x.created_at))}</p>`).join("")}</section>` : ""}
  </div>`;
}

// The line on the Style guide page.
export function tasteLineHTML() {
  const latest = (refs.taste || [])[0];
  if (!latest && !libRows().length) return "";
  const text = !latest ? "Taste report: none yet" : latest.status === "draft"
    ? `<strong>Taste report v${esc(latest.version)} ${isOwner() ? "needs your approval" : `waiting for ${esc(ownerName())}'s approval`}</strong>`
    : `Taste report v${esc(latest.version)}: ${esc({ approved: "approved", rejected: "rejected", archived: "replaced" }[latest.status] || latest.status)}`;
  return `<a class="guide-line" href="#/taste"><span>${text}</span><span aria-hidden="true">›</span></a>`;
}
