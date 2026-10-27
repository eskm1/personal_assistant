// Posting to Instagram in the planner (the Studio Director plan, phase 1): the posting calendar
// (#/calendar: this month and next, scheduled and posted per day, tap a post), Results (#/results:
// how posts did, by kind of post, format and month) and the Instagram connection (#/instagram).
// Posts and results are the Studio machine's (CONTRACT "Posting to Instagram"): read here from
// Supabase (posts, post_results, account_results); scheduling, rescheduling and cancelling go to
// the Studio machine through the shared dialogs in ../post-ui.js (window.StudioPost).
import {
  $, esc, sb, must, toast, isOwner, ownerName, missingTable, plural, mmss, state, underPlan, cfg,
  POST_TYPE, POST_TYPES, kindDot, signPaths, stillUrl, openModal, closeModal, schema, isoDay, ideaMarks, marksKey,
} from "./core.js";
import { studioApi, studioBase } from "./upload.js";

const root = () => $("#posting");
export const posts = { route: null, list: null, results: null, account: null, missing: false, sig: "", tab: 0, planned: [] };
export const postingOpen = () => !root().hidden;
const SP = () => window.StudioPost;
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STATE_LABEL = { scheduled: "Scheduled", publishing: "Going out now", posted: "Posted", failed: "Couldn't post", cancelled: "Cancelled" };
const FORMAT_LABEL = { reel: "Reels", carousel: "Carousels", image: "Photos" };
const POST_COLS = "id,production_id,idea_id,kind,title,caption,post_type,scheduled_at,state,stage,error,problem,next_try_at,permalink,posted_at,thumb_path,updated_at";

// The shared dialogs talk to the Studio machine with this page's planner session.
SP()?.init({
  api: (method, path, body) => studioApi(method, path, body),
  toast: (t) => toast(t, 5000),
  isOwner,
  // Review's stills and videos are same-origin only under /plan/.
  media: (p) => (underPlan ? p : ""),
  onChange: () => document.dispatchEvent(new CustomEvent("studio:refresh")),
});
export const canSchedule = () => isOwner() && studioBase() !== null && !!SP();
export function openSchedule(productionId) {
  if (!canSchedule()) { toast(`Scheduling needs the Studio address (it appears when Studio is on). Or schedule it in Review.`, 6000); return; }
  SP().openSchedule(productionId);
}

// #/calendar, #/results, #/instagram, or null.
export function postingRoute(hash) {
  if (hash === "#/calendar") return { page: "calendar" };
  if (hash === "#/results") return { page: "results" };
  if (hash === "#/instagram") return { page: "instagram" };
  return null;
}

// ── Data ────────────────────────────────────────────────────────────────────

const sgDay = (iso) => SP()?.dayOf(iso) || String(iso || "").slice(0, 10);
const outAt = (p) => p.posted_at || p.scheduled_at;

// Live posts from 70 days back to the end of next month (Plan's home and the calendar).
export async function loadPosts() {
  const since = new Date(Date.now() - 70 * 864e5).toISOString();
  try {
    const [rows, posted] = await Promise.all([
      sb.from("posts").select(POST_COLS).neq("state", "cancelled").gte("scheduled_at", since).order("scheduled_at").limit(500).then(must),
      sb.from("posts").select(POST_COLS).eq("state", "posted").gte("posted_at", since).lt("scheduled_at", since).limit(200).then(must),
    ]);
    posts.list = [...rows, ...posted];
    posts.missing = false;
  } catch (err) {
    if (!missingTable(err)) throw err;
    posts.list = [];
    posts.missing = true;
  }
  await signPaths(posts.list.map((p) => p.thumb_path)).catch(() => {});
  return posts.list;
}

async function loadResults() {
  const since = new Date(Date.now() - 90 * 864e5).toISOString();
  try {
    const [results, account] = await Promise.all([
      sb.from("post_results").select("*").gte("posted_at", since).order("posted_at", { ascending: false }).limit(400).then(must),
      sb.from("account_results").select("*").order("day", { ascending: false }).limit(60).then(must),
    ]);
    posts.results = results;
    posts.account = account;
  } catch (err) {
    if (!missingTable(err)) throw err;
    posts.missing = true;
    return true;
  }
  return false;
}

// What Plan's home shows: the next scheduled posts, and the ones that couldn't go out.
export function upcoming(list = posts.list || []) {
  const now = Date.now();
  return list.filter((p) => ["scheduled", "publishing"].includes(p.state) && Date.parse(p.scheduled_at) > now - 3600e3)
    .sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at));
}
export const failedPosts = (list = posts.list || []) => list.filter((p) => p.state === "failed");

// ── Pages ───────────────────────────────────────────────────────────────────

export async function showPosting(route) {
  const moved = posts.route?.page !== route.page;
  posts.route = route;
  root().hidden = false;
  if (moved) {
    posts.sig = "";
    window.scrollTo(0, 0);
    root().innerHTML = `<div class="pp-wrap"><p class="muted"><span class="spin"></span> Loading…</p></div>`;
  }
  try { await loadPostingPage(); } catch (err) {
    root().innerHTML = `<div class="pp-wrap">${backHTML()}<p class="error-text">Couldn't load this: ${esc(err.message)}</p></div>`;
  }
  if (moved) root().querySelector("h1")?.focus({ preventScroll: true });
}
export function hidePosting() {
  if (root().hidden) return;
  root().hidden = true;
  root().innerHTML = "";
  posts.route = null;
  posts.sig = "";
}
// Ideas with a posting date (the profile grid's, migration 20261028000000) in this month and next that aren't
// scheduled yet: the calendar shows them as planned. Nothing before that update.
async function loadPlanned() {
  if (!schema.grid) return [];
  const t = new Date();
  const from = isoDay(new Date(t.getFullYear(), t.getMonth(), 1)), to = isoDay(new Date(t.getFullYear(), t.getMonth() + 2, 0));
  try {
    return must(await sb.from("ideas").select("id,title,format,post_type,production,post_on,needs_fact,status,slides")
      .not("post_on", "is", null).gte("post_on", from).lte("post_on", to).neq("status", "dismissed").order("post_on").limit(200));
  } catch (err) {
    if (missingTable(err)) { schema.grid = false; return []; }
    throw err;
  }
}

export async function loadPostingPage() {
  const route = posts.route;
  if (!route) return;
  if (route.page === "calendar") {
    const [, planned] = await Promise.all([loadPosts(), loadPlanned().catch(() => [])]);
    const live = new Set((posts.list || []).filter((p) => p.idea_id).map((p) => p.idea_id));
    posts.planned = planned.filter((i) => !live.has(i.id) && !["posted", "rejected"].includes(i.production?.status));
  }
  if (route.page === "results") {
    // Both at once; the results' tables missing still counts once the posts are in.
    const [, missing] = await Promise.all([loadPosts(), loadResults()]);
    if (missing) posts.missing = true;
  }
  if (posts.route !== route) return;
  render();
}

const backHTML = () => `<div class="row"><a class="btn" href="#/" aria-label="Back to the plan">‹ Plan</a></div>`;
const updateNote = () => `<p><strong>Posting needs a one-time update of the online database.</strong></p>
  <p class="muted">${esc(ownerName())} runs <code>cloud_setup.py --update</code> on the laptop once; then this page works.</p>`;
const igInfo = () => state.laptop?.info?.instagram || null;

function connectionLine() {
  const ig = igInfo();
  if (!ig) return "";
  if (!ig.connected) {
    return `<p class="pp-banner">Instagram isn't connected yet, so nothing goes out. ${isOwner() ? `<a href="#/instagram">Connect it</a>` : `${esc(ownerName())} connects it.`}</p>`;
  }
  if (ig.expired || ig.problem) return `<p class="pp-banner bad">${esc(ig.expired ? "The Instagram connection has run out." : ig.problem)} ${isOwner() ? `<a href="#/instagram">Fix it</a>` : ""}</p>`;
  return "";
}

function render() {
  const el = root();
  const r = posts.route;
  if (!r) return;
  const sig = JSON.stringify([r, posts.list, posts.results, posts.account, posts.missing, posts.tab, igInfo(), posts.planned]);
  if (sig === posts.sig && el.firstChild) return;
  posts.sig = sig;
  if (r.page === "instagram") {
    el.innerHTML = `<div class="pp-wrap pp-narrow">${backHTML()}<h1 tabindex="-1">Instagram</h1>
      <section class="pp-card" aria-label="The connection"><div id="pp-connect"></div></section>
      <section class="pp-card pp-how" aria-labelledby="pp-how"><h2 id="pp-how">How posting works</h2>
        <ul class="pp-bullets"><li>Studio posts only drafts you approved and scheduled.</li>
          <li>Schedule from an approved draft in Review, or from its idea here. You can change the time or cancel until it's out.</li>
          <li>Telegram tells you when a post is out, or why it couldn't go.</li>
          <li>Results are read every morning: <a href="#/results">Results</a>.</li></ul></section>
    </div>`;
    if (SP() && (studioBase() !== null)) SP().mountConnect($("#pp-connect"));
    else $("#pp-connect").innerHTML = `<p class="muted">${esc(igInfo()?.connected ? `Connected as @${igInfo().username}.` : "Not connected.")} Open Studio (Review) to change it.</p>`;
    return;
  }
  if (posts.missing) { el.innerHTML = `<div class="pp-wrap">${backHTML()}<h1 tabindex="-1">${r.page === "results" ? "Results" : "Posting calendar"}</h1>${updateNote()}</div>`; return; }
  el.innerHTML = r.page === "calendar" ? calendarHTML() : resultsHTML();
}

// ── Calendar ────────────────────────────────────────────────────────────────

const postMark = (p) => window.StudioMarks?.html(window.StudioMarks.of({ post: p }), { small: true, title: false }) || "";
function plannedHTML(i) {
  return `<a class="pc-post planned ${esc(i.post_type || "none")}" href="#/idea/${esc(i.id)}" title="${esc(`Planned: ${i.title}`)}" aria-label="${esc(`Planned for this day, not scheduled yet: ${i.title}`)}">
    <span class="pc-time">${ideaMarks(i, null, { small: true, title: false })}</span><span class="pc-title">${esc(i.title)}</span></a>`;
}
function monthGrid(y, m, byDay, planned = new Map()) {
  const first = new Date(Date.UTC(y, m, 1));
  const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7;   // Monday first
  const today = sgDay(new Date().toISOString());
  let cells = "";
  for (let i = 0; i < lead; i++) cells += `<div class="pc-cell blank" aria-hidden="true"></div>`;
  for (let d = 1; d <= days; d++) {
    const key = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const list = byDay.get(key) || [];
    const plan = planned.get(key) || [];
    const label = `${d} ${MONTHS[m]}${list.length ? `: ${plural(list.length, "post")}` : ""}${plan.length ? `${list.length ? "," : ":"} ${plan.length} planned` : ""}`;
    cells += `<div class="pc-cell${key === today ? " today" : ""}${key < today ? " past" : ""}" aria-label="${esc(label)}">
      <span class="pc-day">${d}</span>
      ${list.map((p) => `<button type="button" class="pc-post ${esc(p.post_type || "none")} ${esc(p.state)}" data-post="${esc(p.id)}"
        title="${esc(`${STATE_LABEL[p.state]}: ${p.title}`)}" aria-label="${esc(`${STATE_LABEL[p.state]} ${SP()?.whenText(outAt(p)) || ""}: ${p.title}`)}">
        <span class="pc-time">${postMark(p)}${esc(SP()?.shortTime(outAt(p)) || "")}</span><span class="pc-title">${esc(p.title)}</span></button>`).join("")}
      ${plan.map(plannedHTML).join("")}
    </div>`;
  }
  return `<section class="pc-month" aria-label="${esc(`${MONTHS[m]} ${y}`)}">
    <h2 class="pc-month-name">${esc(`${MONTHS[m]} ${y}`)}</h2>
    <div class="pc-grid"><div class="pc-head" aria-hidden="true">${WD.map((w) => `<span>${w}</span>`).join("")}</div>
      <div class="pc-cells">${cells}</div></div>
  </section>`;
}

function calendarHTML() {
  const list = posts.list || [];
  const byDay = new Map();
  for (const p of list) {
    const k = sgDay(outAt(p));
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(p);
  }
  for (const v of byDay.values()) v.sort((a, b) => Date.parse(outAt(a)) - Date.parse(outAt(b)));
  const t = new Date(Date.now() + 8 * 3600e3);
  const y = t.getUTCFullYear(), m = t.getUTCMonth();
  const months = [[y, m], [m === 11 ? y + 1 : y, (m + 1) % 12]];
  const next = upcoming(list);
  const done = list.filter((p) => p.state === "posted").sort((a, b) => Date.parse(outAt(b)) - Date.parse(outAt(a))).slice(0, 8);
  const failed = failedPosts(list);
  const tab = posts.tab || 0;
  const planned = new Map();
  for (const i of posts.planned || []) { if (!planned.has(i.post_on)) planned.set(i.post_on, []); planned.get(i.post_on).push(i); }
  const legend = `<div class="pc-legend" aria-label="Key">${POST_TYPES.map((k) => `<span>${kindDot(k)}${esc(POST_TYPE[k])}</span>`).join("")}
    <span><i class="pc-key posted" aria-hidden="true"></i>Posted</span><span><i class="pc-key scheduled" aria-hidden="true"></i>Scheduled</span>
    ${schema.grid ? `<span><i class="pc-key planned" aria-hidden="true"></i>Planned (a posting date, not scheduled yet)</span>` : ""}</div>
    ${marksKey({ cls: "pc-marks" })}`;
  return `<div class="pp-wrap">
    ${backHTML()}
    <div class="pp-head"><h1 tabindex="-1">Posting calendar</h1>
      <span class="muted">${esc(plural(next.length, "post"))} scheduled${posts.planned?.length ? `, ${esc(posts.planned.length)} more planned` : ""}</span>
      <a class="btn pp-grid-link" href="#/grid">See it as the grid</a></div>
    ${connectionLine()}
    ${failed.length ? `<section class="pp-card pp-failed" aria-labelledby="pp-failed"><h2 id="pp-failed">Couldn't post</h2>${failed.map(agendaRow).join("")}</section>` : ""}
    <div class="seg pc-tabs" role="group" aria-label="Which month">${months.map(([yy, mm], i) => `<button type="button" data-pc-tab="${i}" class="${i === tab ? "on" : ""}" aria-pressed="${i === tab}">${esc(MONTHS[mm])}</button>`).join("")}</div>
    <div class="pc-months" data-tab="${tab}">${months.map(([yy, mm]) => monthGrid(yy, mm, byDay, planned)).join("")}</div>
    ${legend}
    <div class="pp-cols">
      <section class="pp-card" aria-labelledby="pp-next"><h2 id="pp-next">Coming up</h2>
        ${next.length ? next.map(agendaRow).join("") : `<p class="pp-empty">Nothing scheduled. Schedule an approved draft in Review${isOwner() ? "" : ` (${esc(ownerName())})`}.</p>`}</section>
      <section class="pp-card" aria-labelledby="pp-done"><h2 id="pp-done">Posted</h2>
        ${done.length ? done.map(agendaRow).join("") : `<p class="pp-empty">Nothing posted through Studio yet.</p>`}</section>
    </div>
  </div>`;
}

function agendaRow(p) {
  const still = stillUrl(p.thumb_path);
  const sub = [POST_TYPE[p.post_type], p.kind === "carousel" ? "Carousel" : "Reel",
    p.state === "failed" ? (p.error || "") : p.state === "scheduled" && p.error ? p.error : ""].filter(Boolean).join(" · ");
  return `<button type="button" class="pp-row" data-post="${esc(p.id)}">
    ${still ? `<img class="pp-thumb" src="${esc(still)}" alt="" loading="lazy">` : `<span class="pp-thumb none" aria-hidden="true"></span>`}
    <span class="pp-row-text"><span class="pp-when ${esc(p.state)}">${postMark(p)}${kindDot(p.post_type)}${esc(SP()?.whenText(outAt(p)) || "")} · ${esc(STATE_LABEL[p.state])}</span>
      <span class="pp-row-title">${esc(p.title)}</span>${sub ? `<span class="pp-sub">${esc(sub)}</span>` : ""}</span></button>`;
}

// A post, tapped: what it is, when, and (owner) change it.
function openPost(id) {
  const p = (posts.list || []).find((x) => String(x.id) === String(id));
  if (!p) return;
  const r = (posts.results || []).find((x) => String(x.post_id) === String(p.id));
  const still = stillUrl(p.thumb_path);
  const review = underPlan ? `/#/production/${p.production_id}` : /^https?:\/\//i.test(String(cfg.studioUrl || "")) ? `${String(cfg.studioUrl).replace(/\/+$/, "")}/#/production/${p.production_id}` : "";
  const changeable = ["scheduled", "failed"].includes(p.state);
  openModal(`<h2 id="modal-title">${esc(p.title)}</h2>
    <div class="pp-sheet">
      ${still ? `<img class="pp-sheet-thumb" src="${esc(still)}" alt="">` : ""}
      <div class="pp-sheet-text">
        <p class="pp-when ${esc(p.state)}">${kindDot(p.post_type)}<strong>${esc(STATE_LABEL[p.state])}</strong> · ${esc(SP()?.whenText(outAt(p)) || "")}</p>
        <p class="muted small">${esc([POST_TYPE[p.post_type], p.kind === "carousel" ? "Carousel" : "Reel"].filter(Boolean).join(" · "))}</p>
        ${p.error ? `<p class="${p.state === "failed" ? "error-text" : "muted"} small">${esc(p.error)}</p>` : ""}
        ${r ? `<p class="small">${esc(numbersLine(r))}</p>` : ""}
      </div>
    </div>
    ${p.caption ? `<h3>Caption</h3><p class="pp-caption">${esc(p.caption)}</p>` : ""}
    <div class="row end pp-sheet-btns">
      ${p.permalink ? `<a class="btn" href="${esc(p.permalink)}" target="_blank" rel="noopener noreferrer">Open on Instagram ↗</a>` : ""}
      ${review ? `<a class="btn" href="${esc(review)}">Open the draft</a>` : ""}
      ${changeable && canSchedule() ? `<button type="button" class="btn act" data-pp-change="${esc(p.production_id)}">${p.state === "failed" ? "Try again" : "Change"}</button>` : ""}
    </div>
    ${changeable && !isOwner() ? `<p class="muted small">${esc(ownerName())} can change or cancel it until it's out.</p>` : ""}`, async (e) => {
    const b = e.target.closest("[data-pp-change]");
    if (!b) return;
    await closeModal();
    openSchedule(Number(b.dataset.ppChange));
  });
}

root().addEventListener("click", (e) => {
  const tab = e.target.closest("[data-pc-tab]");
  if (tab) { posts.tab = Number(tab.dataset.pcTab); render(); return; }
  const p = e.target.closest("[data-post]");
  if (p) openPost(p.dataset.post);
});

// ── Results ─────────────────────────────────────────────────────────────────

const NUM = new Intl.NumberFormat("en");
const n = (v) => (v == null ? "–" : NUM.format(Math.round(v)));
const secs = (ms) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)} s`);
function numbersLine(r) {
  return [`${n(r.views)} views`, `${n(r.reach)} reached`, `${n(r.likes)} likes`, `${n(r.saves)} saves`, `${n(r.shares)} shares`,
    r.avg_watch_ms != null ? `watched ${secs(r.avg_watch_ms)} on average` : ""].filter(Boolean).join(" · ");
}
const METRICS = ["views", "reach", "likes", "comments", "saves", "shares", "interactions", "avg_watch_ms", "follows", "profile_visits"];
function group(rows, key) {
  const g = new Map();
  for (const r of rows) {
    const k = r[key] || "";
    if (!g.has(k)) g.set(k, { key: k, posts: 0, sum: {}, cnt: {} });
    const x = g.get(k);
    x.posts += 1;
    for (const m of METRICS) if (r[m] != null) { x.sum[m] = (x.sum[m] || 0) + Number(r[m]); x.cnt[m] = (x.cnt[m] || 0) + 1; }
  }
  return [...g.values()].map((x) => ({ ...x, avg: Object.fromEntries(METRICS.map((m) => [m, x.cnt[m] ? x.sum[m] / x.cnt[m] : null])) }));
}
const monthKey = (iso) => sgDay(iso).slice(0, 7);
const monthLabel = (k) => { const [y, m] = k.split("-").map(Number); return y ? `${MONTHS[m - 1]} ${y}` : "–"; };

function groupTable(title, id, groups, label, dot) {
  const max = Math.max(1, ...groups.map((g) => g.avg.views || 0));
  return `<section class="pp-card" aria-labelledby="${id}"><h2 id="${id}">${esc(title)}</h2>
    <div class="pr-table" role="table" aria-label="${esc(title)}">
      <div class="pr-tr pr-th" role="row"><span role="columnheader"></span><span role="columnheader">Posts</span><span role="columnheader">Views a post</span>
        <span role="columnheader" class="pr-opt">Reach</span><span role="columnheader" class="pr-opt">Saves</span><span role="columnheader" class="pr-opt">Shares</span><span role="columnheader" class="pr-opt">Watched</span></div>
      ${groups.map((g) => `<div class="pr-tr" role="row">
        <span role="cell" class="pr-name">${dot ? kindDot(g.key) : ""}${esc(label(g.key))}</span>
        <span role="cell" class="mono">${esc(g.posts)}</span>
        <span role="cell" class="pr-bar-cell"><span class="pbar thin pr-bar" aria-hidden="true"><i class="k ${dot && g.key ? esc(g.key) : "bar"}" data-f="${Math.round(g.avg.views || 0)}"></i><i class="needed" data-f="${Math.round(max - (g.avg.views || 0))}"></i></span><span class="mono">${esc(n(g.avg.views))}</span></span>
        <span role="cell" class="mono pr-opt">${esc(n(g.avg.reach))}</span><span role="cell" class="mono pr-opt">${esc(n(g.avg.saves))}</span>
        <span role="cell" class="mono pr-opt">${esc(n(g.avg.shares))}</span><span role="cell" class="mono pr-opt">${esc(secs(g.avg.avg_watch_ms))}</span>
      </div>`).join("")}
    </div></section>`;
}

function resultsHTML() {
  const rows = posts.results || [];
  const acct = posts.account || [];
  const latest = acct.find((a) => a.followers != null);
  const month = acct.filter((a) => Date.parse(a.day) > Date.now() - 31 * 864e5 && a.followers != null);
  const gained = latest && month.length > 1 ? latest.followers - month[month.length - 1].followers : null;
  const sum = (k) => rows.reduce((s, r) => s + (Number(r[k]) || 0), 0);
  const studio = rows.filter((r) => r.post_id != null).length;
  const tiles = [
    ["Followers", latest ? n(latest.followers) : "–", gained != null ? `${gained >= 0 ? "+" : ""}${n(gained)} in 30 days` : ""],
    ["Posts", n(rows.length), `${studio} by Studio · last 90 days`],
    ["Views", n(sum("views")), rows.length ? `${n(sum("views") / rows.length)} a post` : ""],
    ["Reach a post", rows.length ? n(sum("reach") / rows.length) : "–", "accounts that saw it"],
    ["Saves", n(sum("saves")), "people keeping it"],
    ["Shares", n(sum("shares")), "sent to someone"],
  ].map(([k, v, s]) => `<div class="pr-tile"><span class="um-label">${esc(k)}</span><strong class="pr-big">${esc(v)}</strong>${s ? `<span class="pp-sub">${esc(s)}</span>` : ""}</div>`).join("");
  const byKind = group(rows, "post_type").sort((a, b) => (b.avg.views || 0) - (a.avg.views || 0));
  const byFormat = group(rows, "format").sort((a, b) => (b.avg.views || 0) - (a.avg.views || 0));
  const byMonth = group(rows.map((r) => ({ ...r, month: monthKey(r.posted_at) })), "month").sort((a, b) => a.key.localeCompare(b.key));
  const read = rows.reduce((t, r) => (r.pulled_at > t ? r.pulled_at : t), "");
  const list = rows.map((r) => `<div class="pr-tr pr-post" role="row">
      <span role="cell" class="pr-name">${kindDot(r.post_type)}<span class="pr-post-title">${r.permalink ? `<a href="${esc(r.permalink)}" target="_blank" rel="noopener noreferrer">${esc(r.title || "Untitled")}</a>` : esc(r.title || "Untitled")}
        <span class="pp-sub">${esc([SP()?.whenText(r.posted_at)?.split(",")[0], FORMAT_LABEL[r.format]?.replace(/s$/, ""), POST_TYPE[r.post_type] || (r.post_id ? "" : "Not from Studio")].filter(Boolean).join(" · "))}</span></span></span>
      <span role="cell" class="mono">${esc(n(r.views))}</span><span role="cell" class="mono">${esc(n(r.reach))}</span>
      <span role="cell" class="mono pr-opt">${esc(n(r.likes))}</span><span role="cell" class="mono pr-opt">${esc(n(r.comments))}</span>
      <span role="cell" class="mono pr-opt">${esc(n(r.saves))}</span><span role="cell" class="mono pr-opt">${esc(n(r.shares))}</span>
      <span role="cell" class="mono pr-opt">${esc(secs(r.avg_watch_ms))}</span></div>`).join("");
  return `<div class="pp-wrap">
    ${backHTML()}
    <div class="pp-head"><h1 tabindex="-1">Results</h1><span class="muted">${read ? `Read ${esc(SP()?.whenText(read) || "")}` : "Read every morning"}</span>
      <a class="btn small" href="#/insights" id="pr-insights">What works: Insights ›</a></div>
    ${connectionLine()}
    ${rows.length ? `<div class="pr-tiles">${tiles}</div>
      ${groupTable("By kind of post", "pr-kind", byKind, (k) => POST_TYPE[k] || "Not sorted", true)}
      <div class="pp-cols">${groupTable("By format", "pr-format", byFormat, (k) => FORMAT_LABEL[k] || "Other", false)}
        ${groupTable("By month", "pr-month", byMonth, monthLabel, false)}</div>
      <section class="pp-card" aria-labelledby="pr-all"><h2 id="pr-all">Every post <span class="count">${esc(rows.length)}</span></h2>
        <div class="pr-table pr-posts" role="table" aria-label="Every post">
          <div class="pr-tr pr-th" role="row"><span role="columnheader">Post</span><span role="columnheader">Views</span><span role="columnheader">Reach</span>
            <span role="columnheader" class="pr-opt">Likes</span><span role="columnheader" class="pr-opt">Comments</span><span role="columnheader" class="pr-opt">Saves</span>
            <span role="columnheader" class="pr-opt">Shares</span><span role="columnheader" class="pr-opt">Watched</span></div>
          ${list}</div></section>`
      : `<section class="pp-card"><p class="pp-empty">No results yet. Once Instagram is connected, Studio reads every post of the last 90 days each morning, and a new post about an hour after it goes out.</p></section>`}
  </div>`;
}
