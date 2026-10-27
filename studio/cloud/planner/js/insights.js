// Insights (#/insights): what kinds of posts work on the account (insights.py on the Studio machine, rung 1 of the
// content analytics). The forecast grade first (does Studio's judgement beat "every post is typical" yet?), then the
// Monday memo (Do more / Do less / Try next, what we can't tell yet, earlier memos), the features that stand out
// (each value against the rest, small groups pulled toward typical, n and "proven" / "early signal"), every post's
// score and the followers. Read from the Studio machine (GET /api/instagram/insights) with the planner's Bearer, like
// the posting API; for everyone on the team; nothing here changes a post. Loaded the first time the page opens.
import { $, esc, mark, plural, underPlan } from "./core.js";
import { studioApi, studioBase } from "./upload.js";

const ins = { data: null, memo: null, all: false, allPosts: false, sig: "" };
const POSTS_SHOWN = 15;   // the newest; the rest one press away
const root = () => {
  let el = $("#insights");
  if (!el) {   // its own section, next to the other pages (index.html stays as it is)
    el = document.createElement("section");
    Object.assign(el, { id: "insights", className: "ins-page", hidden: true });
    el.setAttribute("aria-label", "Insights");
    $("#main").append(el);
    el.addEventListener("click", onClick);
  }
  return el;
};
export const insightsOpen = () => !!$("#insights") && !$("#insights").hidden;

// Its look, once (insights.css; the planner's policy takes no inline styles).
function styles() {
  if (document.querySelector("link[data-ins-css]")) return;
  const l = document.createElement("link");
  Object.assign(l, { rel: "stylesheet", href: underPlan ? "/plan/insights.css" : "insights.css" });
  l.dataset.insCss = "";
  document.head.append(l);
}

const NUM = new Intl.NumberFormat("en");
const n0 = (v) => (v == null ? "–" : NUM.format(Math.round(v)));
const n1 = (v) => (v == null ? "–" : v >= 10 ? NUM.format(Math.round(v)) : v.toFixed(1));
const pct = (v) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${Math.round(Math.abs(v) * 100)}%`);
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso) => { const d = new Date(String(iso || "").slice(0, 10) + "T00:00:00"); return Number.isNaN(+d) ? "" : `${d.getDate()} ${MON[d.getMonth()]}`; };
const FORMAT = { reel: "Reel", carousel: "Carousel", slideshow: "Slideshow Reel", image: "Photo" };
const STRENGTH = { proven: ["ok", "Proven"], early: ["drafting", "Early signal"], idea: ["parked", "To test"], none: ["parked", "No clear difference"], few: ["parked", "Too few"] };
const chip = (k) => { const [cls, text] = STRENGTH[k] || STRENGTH.none; return `<span class="chip ${cls} ins-chip">${esc(text)}</span>`; };

export async function showInsights() {
  const el = root();
  styles();
  const first = el.hidden;
  el.hidden = false;
  if (first) {
    window.scrollTo(0, 0);
    if (!ins.data) el.innerHTML = `<div class="pp-wrap"><p class="muted"><span class="spin"></span> Loading…</p></div>`;
  }
  await loadInsights();
  if (first) el.querySelector("h1")?.focus({ preventScroll: true });
}
export function hideInsights() {
  const el = $("#insights");
  if (!el || el.hidden) return;
  el.hidden = true;
  ins.sig = "";
}
export async function loadInsights() {
  if (studioBase() === null) { render(); return; }
  try {
    const q = ins.memo ? `?memo=${encodeURIComponent(ins.memo)}` : "";
    ins.data = await studioApi("GET", `/api/instagram/insights${q}`);
    ins.error = null;
  } catch (err) {
    ins.error = err.message || String(err);
  }
  render();
}

const back = `<div class="row"><a class="btn" href="#/results" aria-label="Back to Results">‹ Results</a></div>`;

function render() {
  const el = $("#insights");
  if (!el || el.hidden) return;
  const sig = JSON.stringify([ins.data, ins.error, ins.all, ins.allPosts, studioBase() === null]);
  if (sig === ins.sig && el.firstChild) return;
  ins.sig = sig;
  const head = `<div class="pp-head"><h1 tabindex="-1">Insights</h1><span class="muted">What kinds of posts work · a memo every Monday</span></div>`;
  if (studioBase() === null) {
    el.innerHTML = `<div class="pp-wrap">${back}${head}<p class="pp-banner">Insights come from the Studio machine, and it can't be reached from here yet. Open Studio (Review), then Plan › More › Insights.</p></div>`;
    return;
  }
  if (!ins.data) {
    el.innerHTML = `<div class="pp-wrap">${back}${head}<p class="pp-banner bad">Couldn't load the insights: ${esc(ins.error || "no answer")}. <button type="button" class="btn small" data-ins-retry>Try again</button></p></div>`;
    return;
  }
  const d = ins.data;
  const scored = (d.posts || []).filter((p) => p.index != null).length;
  el.innerHTML = `<div class="pp-wrap ins">
    ${back}${head}
    ${ins.error ? `<p class="pp-banner bad">Couldn't refresh: ${esc(ins.error)}</p>` : ""}
    <p class="ins-grade ${d.grade?.ok ? "ok" : "hunch"}" id="ins-grade"><strong>Forecast grade</strong><span>${esc(d.grade?.text || "")}</span></p>
    ${memoHTML(d)}
    ${(d.posts || []).length ? `${findingsHTML(d)}${postsHTML(d, scored)}` : `<section class="pp-card"><p class="pp-empty">No numbers yet. Once Instagram is connected and a post has been out a week, its score shows here.</p></section>`}
    ${followersHTML(d.followers || [])}
    ${howHTML(d)}
  </div>`;
  for (const i of el.querySelectorAll("[data-w]")) i.style.width = `${Math.max(0, Math.min(100, Number(i.dataset.w) || 0))}%`;
  for (const i of el.querySelectorAll("[data-h]")) i.style.height = `${Math.max(2, Math.min(100, Number(i.dataset.h) || 0))}%`;
}

function memoHTML(d) {
  const rec = d.memo;
  const dates = d.memos || [];
  const picker = dates.length > 1 ? `<div class="ins-memos" role="group" aria-label="Earlier memos">${dates.slice(0, 8).map((x) =>
    `<button type="button" class="btn small${(rec?.date || dates[0]) === x ? " on" : ""}" data-ins-memo="${esc(x)}" aria-pressed="${(rec?.date || dates[0]) === x}">${esc(day(x))}</button>`).join("")}</div>` : "";
  if (!rec || !rec.memo) {
    const next = d.next_memo ? ` The first one comes ${esc(day(d.next_memo))}.` : "";
    return `<section class="pp-card" aria-labelledby="ins-memo-h"><h2 id="ins-memo-h">The Monday memo</h2>
      <p class="pp-empty">No memo yet.${next} It needs at least 5 posts with a week of numbers.</p></section>`;
  }
  const m = rec.memo;
  const list = (key, title) => (m[key] || []).length ? `<div class="ins-list"><h3>${title}</h3><ul>${m[key].map((b) =>
    `<li>${chip(b.strength)}<span>${esc(b.text)}</span></li>`).join("")}</ul></div>` : "";
  return `<section class="pp-card" aria-labelledby="ins-memo-h"><h2 id="ins-memo-h">The Monday memo <span class="count">${esc(day(rec.date))}</span></h2>
    <div class="ins-memo">
      ${m.headline ? `<p class="ins-headline">${esc(m.headline)}</p>` : ""}
      ${list("do_more", "Do more")}${list("do_less", "Do less")}${list("try_next", "Try next")}
      ${(m.cant_tell || []).length ? `<div class="ins-list"><h3>What we can't tell yet</h3><ul class="plain">${m.cant_tell.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>` : ""}
      <p class="muted small">${rec.by === "claude" ? "Written by Claude from the numbers below." : "Written by Studio from the numbers below."} Suggestions only: your playbook and your calls stand.</p>
      ${picker}
    </div></section>`;
}

function findingsHTML(d) {
  const L = d.labels || {};
  const rows = (d.findings || []).filter((r) => r.n >= 1);
  const strong = rows.filter((r) => r.label === "proven" || r.label === "early");
  const shown = ins.all ? rows : strong;
  const row = (r) => {
    const name = `${L.features?.[r.feature] || r.feature}: ${r.value}`;
    const metric = L.metrics?.[r.metric] || r.metric;
    if (r.lift == null) {
      return `<div class="ins-f few"><span class="ins-f-name">${esc(name)}<span class="pp-sub">${esc(plural(r.n, "post"))}: too few to say</span></span>
        <span class="ins-bar none" aria-hidden="true"></span><span class="mono ins-pct">–</span>${chip("few")}</div>`;
    }
    const w = Math.min(Math.abs(r.lift), 1) * 50;
    const label = `${name}. ${metric} ${pct(r.lift)} against the rest, ${r.n} posts against ${r.n_rest}. ${STRENGTH[r.label]?.[1] || ""}.`;
    return `<div class="ins-f ${esc(r.label)}" role="listitem" aria-label="${esc(label)}">
      <span class="ins-f-name">${esc(name)}<span class="pp-sub">${esc(metric)} · n=${esc(r.n)} vs ${esc(r.n_rest)}</span></span>
      <span class="ins-bar" aria-hidden="true"><i class="${r.lift >= 0 ? "pos" : "neg"}" data-w="${w.toFixed(1)}"></i></span>
      <span class="mono ins-pct">${esc(pct(r.lift))}</span>${chip(r.label)}</div>`;
  };
  return `<section class="pp-card" aria-labelledby="ins-find-h"><h2 id="ins-find-h">What stands out <span class="count">${esc(strong.length)}</span></h2>
    <p class="ins-note">Each line: the posts with it against the rest, after small groups are pulled toward typical. n is how many posts.</p>
    ${shown.length ? `<div class="ins-fs" role="list">${shown.map(row).join("")}</div>` : `<p class="pp-empty">Nothing stands out yet. More posts of each kind will tell.</p>`}
    ${rows.length > strong.length ? `<div class="ins-more"><button type="button" class="btn small" data-ins-all aria-expanded="${ins.all}">${ins.all ? "Show only what stands out" : `Show every feature (${rows.length})`}</button></div>` : ""}
  </section>`;
}

function postsHTML(d, scored) {
  const posts = d.posts || [];
  const max = Math.max(150, ...posts.map((p) => p.index || 0));
  const row = (p) => {
    const sub = [day(p.posted_at), FORMAT[p.format] || p.format, p.by_studio ? "" : "from the phone",
      p.status === "too_new" ? "too new: scored at day 7" : p.status === "late" ? "first read after day 10" : ""].filter(Boolean).join(" · ");
    const title = p.permalink ? `<a href="${esc(p.permalink)}" target="_blank" rel="noopener noreferrer">${esc(p.title || "Untitled")}</a>` : esc(p.title || "Untitled");
    const r = p.rates || {};
    return `<div class="pr-tr ins-post" role="row">
      <span role="cell" class="pr-post-title">${title}<span class="pp-sub">${esc(sub)}</span></span>
      <span role="cell" class="pr-bar-cell">${p.index != null ? `<span class="pbar thin pr-bar ins-ix" aria-hidden="true"><i class="k bar ${p.index >= 100 ? "up" : ""}" data-w="${(100 * p.index / max).toFixed(1)}"></i></span>` : ""}<span class="mono">${esc(n0(p.index))}</span></span>
      <span role="cell" class="mono">${esc(n1(r.saves))}</span><span role="cell" class="mono pr-opt">${esc(n1(r.shares))}</span>
      <span role="cell" class="mono pr-opt">${esc(n1(r.follows))}</span>
      <span role="cell" class="mono pr-opt">${r.watch != null ? `${Math.round(r.watch * 100)}%` : "–"}</span>
      <span role="cell" class="mono pr-opt">${esc(n0(p.likes))}</span></div>`;
  };
  return `<section class="pp-card" aria-labelledby="ins-posts-h"><h2 id="ins-posts-h">Every post <span class="count">${esc(scored)} scored</span></h2>
    <p class="ins-note">Index: 100 is a typical post of its format, judged at day 7 on saves, shares, follows, profile visits and comments per 1,000 reached (and for Reels how much people watch). Likes are shown, not judged.</p>
    <div class="pr-table ins-posts" role="table" aria-label="Every post">
      <div class="pr-tr pr-th" role="row"><span role="columnheader">Post</span><span role="columnheader">Index</span><span role="columnheader">Saves /1k</span>
        <span role="columnheader" class="pr-opt">Shares /1k</span><span role="columnheader" class="pr-opt">Follows /1k</span>
        <span role="columnheader" class="pr-opt">Watched</span><span role="columnheader" class="pr-opt">Likes</span></div>
      ${(ins.allPosts ? posts : posts.slice(0, POSTS_SHOWN)).map(row).join("")}</div>
    ${posts.length > POSTS_SHOWN ? `<div class="ins-more"><button type="button" class="btn small" data-ins-posts aria-expanded="${ins.allPosts}">${ins.allPosts ? "Show the newest only" : `Show all ${posts.length} posts`}</button></div>` : ""}
  </section>`;
}

function followersHTML(list) {
  if (list.length < 2) return "";
  const last = list[list.length - 1];
  const ago = (days) => {
    const cut = new Date(Date.parse(last.day) - days * 864e5).toISOString().slice(0, 10);
    const old = list.filter((x) => x.day <= cut).pop();
    return old ? last.followers - old.followers : null;
  };
  const lo = Math.min(...list.map((x) => x.followers)), hi = Math.max(...list.map((x) => x.followers));
  const span = Math.max(1, hi - lo);
  const change = (v, label) => (v == null ? "" : `<span class="pp-sub">${v >= 0 ? "+" : "−"}${n0(Math.abs(v))} in ${label}</span>`);
  return `<section class="pp-card" aria-labelledby="ins-fol-h"><h2 id="ins-fol-h">Followers</h2>
    <div class="ins-fol"><div class="ins-fol-num"><strong class="pr-big">${esc(n0(last.followers))}</strong>${change(ago(7), "7 days")}${change(ago(30), "30 days")}</div>
      <div class="ins-spark" role="img" aria-label="${esc(`Followers from ${n0(list[0].followers)} on ${day(list[0].day)} to ${n0(last.followers)} on ${day(last.day)}`)}">
        ${list.map((x) => `<i data-h="${(10 + 90 * (x.followers - lo) / span).toFixed(1)}" title="${esc(`${day(x.day)}: ${n0(x.followers)}`)}"></i>`).join("")}</div>
      <p class="pp-sub ins-fol-axis"><span>${esc(day(list[0].day))}</span><span>${esc(day(last.day))}</span></p></div></section>`;
}

function howHTML(d) {
  const r = d.rules || {};
  return `<details class="pp-card ins-how"><summary>How Studio scores and judges</summary><ul class="pp-bullets">
    <li>Each post is judged ${esc(r.age || 7)} days after it goes out, against the typical post of its format (the middle of the ${esc(r.base_n || 20)} before it).</li>
    <li>A feature needs ${esc(r.show_min || 3)} posts to show. One post is never a pattern.</li>
    <li>Proven: ${esc(r.proven_n || 8)} posts or more on each side, very unlikely to be luck, and at least ${Math.round((r.proven_lift || .15) * 100)}% apart. Early signal: likely, worth testing.</li>
    <li>Every Monday Studio forecasts the week's posts, then checks them against the real numbers. The grade says if its judgement beats "every post is typical" (target: ${Math.round((r.target || .25) * 100)}% better).</li>
  </ul></details>`;
}

async function onClick(e) {
  const m = e.target.closest("[data-ins-memo]");
  if (m) {
    const latest = (ins.data?.memos || [])[0];
    ins.memo = m.dataset.insMemo === latest ? null : m.dataset.insMemo;
    mark("insights_memo");
    await loadInsights();
    return;
  }
  if (e.target.closest("[data-ins-all]")) {
    ins.all = !ins.all;
    mark("insights_all", { on: ins.all });
    render();
    return;
  }
  if (e.target.closest("[data-ins-posts]")) {
    ins.allPosts = !ins.allPosts;
    render();
    return;
  }
  if (e.target.closest("[data-ins-retry]")) loadInsights();
}
