// Plan's home ("What needs you", option A, approved 2026-10-19): what's waiting for a decision
// across every project, what's coming up, and on the side the month as post slots, the projects
// and Client questions. Everything here already exists elsewhere (ideas with their decision,
// status, production and suggestion; content_month(); site days, phases and shots; client
// questions); this page only gathers it. Served at /plan/, it also asks the Studio machine for
// the drafts waiting in Review (GET /api/home: stills and what Studio fixed itself); the planner
// on its own address does without, from ideas.production.
import {
  $, esc, sb, must, toast, cfg, state, isOwner, ownerName, plural, mmss, todayISO, parseDay, dayLabel, dayOf,
  POST_TYPES, POST_TYPE, kindDot, missingTable, underPlan, isFilmed, openModal, closeModal, formError, offWhy, mark,
  ideaMarks, marksKey,
} from "./core.js";
import { plan, loadProjects, fromFootage, openQuickAdd, openNewProject, setKindHint } from "./board.js";
import { month, loadMonth } from "./month.js";
import { topics, suggestClick, jobLine, whenLine } from "./topics.js";
import { loadPosts, upcoming, failedPosts, openSchedule, canSchedule } from "./posting.js";

export const home = { data: null, sig: "", seq: 0 };
const root = () => $("#home");
export const homeOpen = () => !root().hidden;

const IN_EDIT = new Set(["planned", "producing", "revising"]);
const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const FOOTAGE_DAYS = 14;   // footage ideas newer than this count as waiting for a look

// ── Loading ─────────────────────────────────────────────────────────────────

// Ideas across the active projects, with what the page needs from their shots. Columns from
// later database updates are dropped one by one when they aren't there yet.
async function ideasFor(ids) {
  if (!ids.length) return [];
  const optional = ["post_type", "suggestion", "needs_fact"];
  let shots = "shots(id,phase_id,shoot_on,filmed_at,takes(id,clips(recorded_at)))";
  for (;;) {
    const cols = ["id,title,project_id,arc_id,format,decision,status,production,created_at,updated_at", ...optional, shots].join(",");
    try {
      return must(await sb.from("ideas").select(cols).in("project_id", ids).neq("status", "dismissed").order("id", { ascending: false }).limit(1500));
    } catch (err) {
      if (!missingTable(err)) throw err;
      const gone = optional.find((c) => err.message.includes(c));
      if (gone) optional.splice(optional.indexOf(gone), 1);
      else if (shots.includes("phase_id")) shots = "shots(id,filmed_at,takes(id,clips(recorded_at)))";
      else throw err;
    }
  }
}
// A table from a later database update: nothing until it's there.
const maybe = (q) => q.then(must).catch((err) => { if (missingTable(err)) return []; throw err; });

// What only the Studio machine knows (drafts' stills, what Studio fixed): /plan/ only.
async function reviewQueue() {
  if (!underPlan) return null;
  try {
    const res = await fetch("/api/home", { credentials: "same-origin", headers: { Accept: "application/json" } });
    return res.ok ? await res.json() : null;
  } catch { return null; }
}

export async function loadHome() {
  const seq = ++home.seq;
  const queue = reviewQueue();   // the Studio machine is far away: ask it first, alongside everything else
  await loadProjects();
  const active = plan.projects.filter((p) => p.status === "active");
  const ids = active.map((p) => p.id);
  const [ideas, arcs, days, phases, guides, reports, dismissed, api, , posts, pins] = await Promise.all([
    ideasFor(ids),
    ids.length ? sb.from("arcs").select("id,project_id,title").in("project_id", ids).then(must) : [],
    ids.length ? maybe(sb.from("site_days").select("id,project_id,day,phase_id").in("project_id", ids).gte("day", todayISO()).order("day").limit(40)) : [],
    ids.length ? maybe(sb.from("phases").select("id,project_id,name").in("project_id", ids)) : [],
    maybe(sb.from("style_guides").select("id,brand,version,status").eq("status", "draft").order("id", { ascending: false }).limit(1)),
    maybe(sb.from("taste_reports").select("id,version,status").eq("status", "draft").order("id", { ascending: false }).limit(1)),
    isOwner() ? sb.from("ideas").select("id", { count: "exact", head: true }).eq("status", "dismissed").not("suggestion", "is", null)
      .is("production", null).then((r) => (r.error ? 0 : r.count || 0)) : 0,
    queue,
    loadMonth().catch(() => null),
    loadPosts().catch(() => []),
    // Pins from the profile grid still to do in the Instagram app (Studio can't pin): after the grid's update only.
    isOwner() ? maybe(sb.from("grid_pins").select("id,idea_id,media_id").is("done_at", null)) : [],
  ]);
  if (seq !== home.seq) return;
  home.data = { active, ideas, arcs, days, phases, guides, reports, dismissed, api, posts, pins };
}

// The kind of post the month is shortest of (most slots still to plan): New idea's kind by default.
export function shortestKind() {
  const kinds = month.data?.kinds || {};
  let best = "", gap = 0;
  for (const k of POST_TYPES) {
    const g = (Number(kinds[k]?.target) || 0) - (Number(kinds[k]?.planned) || 0);
    if (g > gap) { gap = g; best = k; }
  }
  return best;
}
setKindHint(shortestKind);

// ── What needs a decision, what's coming up ─────────────────────────────────

const projectName = (pid) => plan.projects.find((p) => p.id === pid)?.name || "";
const isReel = (i) => i.format !== "carousel";
const prodStatus = (i) => i.production?.status || "";

// Where a draft opens in Review: here at /plan/, at the Studio address elsewhere; without one, the idea.
function reviewHref(productionId, ideaId) {
  if (underPlan) return `/#/production/${productionId}`;
  const studio = String(cfg.studioUrl || "").replace(/\/+$/, "");
  if (/^https?:\/\//i.test(studio)) return `${studio}/#/production/${productionId}`;
  return ideaId ? `#/idea/${ideaId}` : "";
}

// "Friday's filming", from the newest footage day of the ideas.
function filmedWhen(ideas) {
  const days = ideas.flatMap((i) => (i.shots || []).flatMap((s) => (s.takes || []).map((t) => dayOf(t.clips?.recorded_at)))).filter(Boolean).sort();
  const d = parseDay(days.pop()), t = parseDay(todayISO());
  if (!d) return "new footage";
  const n = Math.round((t - d) / 864e5);
  if (n === 0) return "today's filming";
  if (n === 1) return "yesterday's filming";
  if (n > 1 && n < 7) return `${WEEKDAY[d.getDay()]}'s filming`;
  return `the filming of ${dayLabel(d.toISOString().slice(0, 10), { weekday: false })}`;
}

function needs(d) {
  const out = [];
  const byProduction = new Map(d.ideas.filter((i) => i.production?.id).map((i) => [Number(i.production.id), i]));
  // Drafts waiting in Review: the machine's list when it answered, else the ideas' production status.
  const drafts = d.api?.review
    ? d.api.review.map((r) => ({ ...r, idea: byProduction.get(Number(r.id)) }))
    : d.ideas.filter((i) => prodStatus(i) === "ready").map((i) => ({ id: i.production.id, title: i.title, format: i.format, idea: i }));
  for (const r of drafts) {
    const i = r.idea;
    const meta = [i && POST_TYPE[i.post_type], (r.format || i?.format) === "carousel" ? "Carousel" : "Reel",
      r.duration ? mmss(r.duration) : r.slides_count ? plural(r.slides_count, "slide") : "",
      r.fixed ? `Studio fixed ${plural(r.fixed, "thing")} before showing you` : ""].filter(Boolean);
    out.push({ type: "draft", key: `d${r.id}`, id: r.id, title: r.title || i?.title || "Draft", thumb: r.thumb || "", meta, href: reviewHref(r.id, i?.id) });
  }
  const live = new Set(d.active.map((p) => p.id));
  for (const s of topics.list.filter((x) => live.has(x.project_id))) out.push({ type: "suggest", key: `s${s.id}`, idea: s });
  // New post ideas from footage, one line per project.
  const footageArcs = new Set(d.arcs.filter(fromFootage).map((a) => a.id));
  const since = Date.now() - FOOTAGE_DAYS * 864e5;
  const fresh = d.ideas.filter((i) => footageArcs.has(i.arc_id) && i.decision === "proposed" && i.status === "new" && !i.suggestion
    && Date.parse(i.created_at || "") > since);
  for (const pid of [...new Set(fresh.map((i) => i.project_id))]) {
    const list = fresh.filter((i) => i.project_id === pid);
    out.push({ type: "footage", key: `f${pid}`, pid, n: list.length, when: filmedWhen(list) });
  }
  // Edits that failed: Retry edit is in Review.
  const failed = d.api?.failed
    ? d.api.failed.map((f) => ({ ...f, idea: byProduction.get(Number(f.id)) }))
    : d.ideas.filter((i) => prodStatus(i) === "failed").map((i) => ({ id: i.production.id, title: i.title, error: "", idea: i }));
  for (const f of failed) out.push({ type: "failed", key: `x${f.id}`, title: f.title, error: f.error, href: reviewHref(f.id, f.idea?.id) });
  // Posts that couldn't go out to Instagram: Try again (Bryan).
  for (const p of failedPosts(d.posts || [])) out.push({ type: "postfail", key: `pf${p.id}`, post: p });
  // A post pinned in the grid that's out now: pin it in the Instagram app.
  for (const pin of d.pins || []) {
    const post = pin.idea_id ? (d.posts || []).find((p) => p.idea_id === pin.idea_id && p.state === "posted") : null;
    if (pin.idea_id && !post) continue;
    out.push({ type: "pin", key: `pin${pin.id}`, title: post?.title || d.ideas.find((i) => i.id === pin.idea_id)?.title || "A post you pinned in the grid" });
  }
  for (const g of d.guides) out.push({ type: "guide", key: `g${g.id}`, version: g.version });
  for (const r of d.reports) out.push({ type: "taste", key: `t${r.id}`, version: r.version });
  return out;
}

// The team's own jobs (Bryan, 2026-10-20: Michelle's Plan leads with what she can do): shots to film,
// Studio's suggestions and new ideas to Go or Park, drafts to watch and leave notes on. What only Bryan
// can do (failed edits and posts, the style guide, the taste report) folds under "Waiting for Bryan".
const BRYAN_ONLY = new Set(["failed", "postfail", "guide", "taste", "pin"]);
function teamJobs(d, list) {
  const out = [];
  // Shots to film: the next site days with shots still to film, then Go posts part-filmed elsewhere.
  const days = comingUp(d).filter((x) => x.type === "day").slice(0, 2);
  for (const x of days) out.push({ ...x, type: "film", key: `film${x.key}` });
  const withDay = new Set(days.map((x) => x.pid));
  const toFilm = new Map();
  for (const i of d.ideas.filter((x) => x.decision === "go" && isReel(x) && !x.production && x.status !== "made" && !withDay.has(x.project_id))) {
    const left = (i.shots || []).filter((s) => !isFilmed(s)).length;
    if (left) toFilm.set(i.project_id, (toFilm.get(i.project_id) || 0) + left);
  }
  for (const [pid, n] of [...toFilm].slice(0, 2)) out.push({ type: "film", key: `filmp${pid}`, pid, shots: n });
  // Ideas to Go or Park: Studio's suggestions and footage ideas (as for Bryan), then others waiting, per project.
  out.push(...list.filter((x) => x.type === "suggest" || x.type === "footage"));
  const footagePids = new Set(list.filter((x) => x.type === "footage").map((x) => x.pid));
  const footageArcs = new Set(d.arcs.filter(fromFootage).map((a) => a.id));
  const waiting = new Map();
  for (const i of d.ideas.filter((x) => x.decision === "proposed" && x.status === "new" && !x.production && !x.suggestion
    && !(footageArcs.has(x.arc_id) && footagePids.has(x.project_id)))) waiting.set(i.project_id, (waiting.get(i.project_id) || 0) + 1);
  for (const [pid, n] of waiting) out.push({ type: "waiting", key: `w${pid}`, pid, n });
  // Drafts: watch, leave notes, send back (Bryan approves).
  out.push(...list.filter((x) => x.type === "draft").map((x) => ({ ...x, team: true })));
  return out;
}

// Site days with shots still to film, posts in the edit, posts being filmed.
function comingUp(d) {
  const today = todayISO();
  const go = d.ideas.filter((i) => i.decision === "go" && i.status !== "dismissed");
  const phaseName = new Map(d.phases.map((p) => [p.id, p.name]));
  const days = [];
  for (const day of d.days) {
    let shots = 0;
    const posts = new Set();
    for (const i of go.filter((x) => x.project_id === day.project_id)) {
      for (const s of i.shots || []) {
        const due = s.shoot_on ? s.shoot_on === day.day : s.phase_id != null && s.phase_id === day.phase_id;
        if (due && !isFilmed(s)) { shots += 1; posts.add(i.id); }
      }
    }
    if (shots) days.push({ type: "day", key: `day${day.id}`, day: day.day, pid: day.project_id, phase: phaseName.get(day.phase_id) || "", shots, posts: posts.size, soon: day.day === today || !days.length });
  }
  const edits = d.ideas.filter((i) => IN_EDIT.has(prodStatus(i)))
    .map((i) => ({ type: "edit", key: `e${i.id}`, idea: i, revising: prodStatus(i) === "revising" }));
  const filming = go.filter((i) => isReel(i) && !i.production && i.status !== "made")
    .map((i) => ({ i, total: (i.shots || []).length, filmed: (i.shots || []).filter(isFilmed).length }))
    .filter((x) => x.total && x.filmed && x.filmed < x.total)
    .sort((a, b) => b.filmed / b.total - a.filmed / a.total)
    .map((x) => ({ type: "filming", key: `m${x.i.id}`, idea: x.i, filmed: x.filmed, total: x.total }));
  // The next posts going out on Instagram.
  const out = upcoming(d.posts || []).slice(0, 3).map((p) => ({ type: "post", key: `p${p.id}`, post: p }));
  return [...out, ...days.slice(0, 3), ...edits.slice(0, 3), ...filming.slice(0, 3)];
}

// ── Rendering ───────────────────────────────────────────────────────────────

export function renderHome(force = false) {
  const el = root();
  const d = home.data;
  if (!d) { el.innerHTML = `<div class="hm-wrap"><p class="muted"><span class="spin"></span> Loading…</p></div>`; return; }
  const owner = isOwner();
  const all = needs(d);
  // Bryan: everything that needs him. The team: their own jobs first; Bryan's list folded under them.
  const list = owner ? all : teamJobs(d, all);
  const his = owner ? [] : all.filter((x) => BRYAN_ONLY.has(x.type));
  const next = comingUp(d).filter((x) => owner || !list.some((j) => j.key === `film${x.key}`));
  const sig = JSON.stringify([list, his, next, month.data, d.posts, topics.job, topics.openQuestions, d.active.map((p) => [p.id, p.name]),
    d.ideas.length, d.dismissed, [...topics.busy], state.laptop?.info?.topics?.last_suggested]);
  if (!force && sig === home.sig && el.firstChild) return;
  home.sig = sig;
  const now = new Date();
  const n = list.length;
  const headline = !n ? "Nothing needs you right now"
    : owner ? `${n} ${n === 1 ? "thing needs" : "things need"} you` : `${n} ${n === 1 ? "thing" : "things"} you can do`;
  el.innerHTML = `<div class="hm-wrap">
    <header class="hm-head">
      <div class="hm-title">
        <span class="hm-date">${esc(`${WEEKDAY[now.getDay()]} ${now.getDate()} ${MONTHS[now.getMonth()]}`)}</span>
        <h1 class="page-title" id="hm-title" tabindex="-1">${esc(headline)}</h1>
        ${monthLineHTML()}
      </div>
      <button type="button" class="btn act lg" id="home-new-idea">+ New idea</button>
    </header>
    <div class="hm-cols">
      <div class="hm-main">
        ${owner ? needsHTML(list, d) : jobsHTML(list, his)}
        ${nextHTML(next)}
      </div>
      <aside class="hm-side" aria-label="This month and projects">
        ${monthHTML()}
        ${projectsHTML(d)}
        ${questionsHTML()}
      </aside>
    </div>
  </div>`;
}

function needsHTML(list, d) {
  const clear = isOwner() && d.dismissed
    ? `<div class="hm-foot"><button type="button" class="btn-link" data-clear-dismissed>Clear ${esc(plural(d.dismissed, "dismissed suggestion"))}</button></div>` : "";
  return `<section class="hm-card hm-needs" aria-labelledby="hm-needs">
    <h2 class="hm-card-head" id="hm-needs">Needs your call</h2>
    ${list.length ? list.map(needHTML).join("") : `<p class="hm-empty">Nothing needs you right now.</p>`}
    ${clear}
  </section>`;
}

function jobsHTML(list, his) {
  const fold = his.length ? `<details class="hm-bryan">
      <summary><span>${esc(`${plural(his.length, "thing")} waiting for ${ownerName()}`)}</span></summary>
      ${his.map(needHTML).join("")}</details>` : "";
  return `<section class="hm-card hm-needs" aria-labelledby="hm-needs">
    <h2 class="hm-card-head" id="hm-needs">Your jobs</h2>
    ${list.length ? list.map(needHTML).join("") : `<p class="hm-empty">Nothing for you right now. + New idea starts the next post.</p>`}
    ${fold}
  </section>`;
}

function needHTML(x) {
  if (x.type === "film") {
    const today = x.day === todayISO();
    const when = !x.day ? "" : today ? "Today" : dayLabel(x.day, { weekday: true }).split(" ").slice(0, 2).join(" ");
    const href = x.day ? `#/today/${x.pid}` : `#/project/${x.pid}`;
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label${today ? " warn-text" : ""}">${esc(when ? `${when} · site day` : "Shots to film")}</span>
        <span class="hm-name">${esc(`${projectName(x.pid)}: ${plural(x.shots, "shot")} to film`)}</span>
        ${x.posts ? `<span class="hm-sub">${esc(`For ${plural(x.posts, "post")}${x.phase ? ` · ${x.phase}` : ""}`)}</span>` : ""}</div>
      <a class="btn hm-act" href="${esc(href)}" data-mark="home_film">${x.day ? "Shot list" : "Film"}</a>
    </article>`;
  }
  if (x.type === "waiting") {
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label">Ideas to decide</span>
        <span class="hm-name">${esc(`${plural(x.n, "idea")} waiting for Go or Park`)}</span>
        <span class="hm-sub">${esc(projectName(x.pid))} · open one, then Go or Park</span></div>
      <a class="btn hm-act" href="#/project/${esc(x.pid)}">Decide</a>
    </article>`;
  }
  if (x.type === "draft") {
    return `<article class="hm-row" data-need="${esc(x.key)}">
      ${x.thumb ? `<img class="hm-thumb" src="${esc(x.thumb)}" alt="" loading="lazy">` : `<span class="hm-thumb none" aria-hidden="true"></span>`}
      <div class="hm-text"><span class="hm-label good-text">${window.StudioMarks?.html(window.StudioMarks.of({ production: "ready" }), { small: true }) || ""}${x.team ? `Draft for ${esc(ownerName())} to approve` : "Draft ready to review"}</span>
        <span class="hm-name">${esc(x.title)}</span>
        ${x.meta.length ? `<span class="hm-sub">${esc((x.team ? ["Watch it, add notes or send it back", ...x.meta] : x.meta).join(" · "))}</span>` : x.team ? `<span class="hm-sub">Watch it, add notes or send it back</span>` : ""}</div>
      ${x.href ? `<a class="btn hm-act" href="${esc(x.href)}">${x.team ? "Watch" : "Review"}</a>` : ""}
    </article>`;
  }
  if (x.type === "suggest") {
    const i = x.idea;
    const busy = offWhy(topics.busy.has(i.id) ? "Saving your call. A moment." : "");
    const kind = i.post_type ? `${kindDot(i.post_type)}` : "";
    const what = ["Studio suggests", POST_TYPE[i.post_type], i.format === "carousel" ? "Carousel" : i.suggestion?.clip_id ? "Reel from your footage" : "Reel"].filter(Boolean).join(" · ");
    return `<article class="hm-row" data-sugg="${esc(i.id)}">
      <div class="hm-text"><span class="hm-label">${ideaMarks(i, null, { small: true })}${kind}${esc(what)}</span>
        <a class="hm-name" href="#/idea/${esc(i.id)}">${esc(i.title)}</a>
        ${i.suggestion?.why ? `<span class="hm-sub">${esc(i.suggestion.why)}</span>` : ""}</div>
      <div class="hm-btns">
        <button type="button" class="btn good" data-sugg-act="go" ${busy} aria-label="Go: ${esc(i.title)}">Go</button>
        <button type="button" class="btn" data-sugg-act="parked" ${busy} aria-label="Not now: ${esc(i.title)}" title="Park it: it stays on the board for later">Not now</button>
        ${isOwner() ? `<button type="button" class="btn ghost" data-sugg-act="dismissed" ${busy} aria-label="Dismiss: ${esc(i.title)}" title="Studio won't suggest it again">Dismiss</button>` : ""}
      </div>
    </article>`;
  }
  if (x.type === "footage") {
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label">From your footage</span>
        <span class="hm-name">${esc(plural(x.n, "post idea"))} from ${esc(x.when)}</span>
        <span class="hm-sub">${esc(projectName(x.pid))} · Go on the ones you like</span></div>
      <a class="btn hm-act" href="#/project/${esc(x.pid)}/footage">Look through</a>
    </article>`;
  }
  if (x.type === "postfail") {
    const p = x.post;
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label bad-text">${window.StudioMarks?.html(window.StudioMarks.of({ post: p }), { small: true }) || ""}${kindDot(p.post_type)}Couldn't post to Instagram</span>
        <span class="hm-name">${esc(p.title)}</span>
        ${p.error ? `<span class="hm-sub">${esc(p.error)}</span>` : ""}</div>
      ${canSchedule() ? `<button type="button" class="btn hm-act" data-post-retry="${esc(p.production_id)}">Try again</button>`
        : `<a class="btn hm-act" href="#/calendar">Open</a>`}
    </article>`;
  }
  if (x.type === "failed") {
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label bad-text">Edit failed</span>
        <span class="hm-name">${esc(x.title)}</span>
        ${x.error ? `<span class="hm-sub">${esc(x.error)}</span>` : ""}</div>
      ${x.href ? `<a class="btn hm-act" href="${esc(x.href)}">Open</a>` : ""}
    </article>`;
  }
  if (x.type === "pin") {
    return `<article class="hm-row" data-need="${esc(x.key)}">
      <div class="hm-text"><span class="hm-label">Pin it on Instagram</span>
        <span class="hm-name">${esc(x.title)}</span>
        <span class="hm-sub">You pinned it in the grid and it's out now. Studio can't pin: hold the post in the Instagram app, then Pin to your profile.</span></div>
      <a class="btn hm-act" href="#/grid">Open the grid</a>
    </article>`;
  }
  const guide = x.type === "guide";
  return `<article class="hm-row" data-need="${esc(x.key)}">
    <div class="hm-text"><span class="hm-label">${guide ? "Style guide" : "Taste report"}</span>
      <span class="hm-name">${guide ? `A new style guide draft (v${esc(x.version)}) to approve` : `The taste report (v${esc(x.version)}) to approve`}</span></div>
    <a class="btn hm-act" href="${guide ? "#/style" : "#/taste"}">Read it</a>
  </article>`;
}

function nextHTML(next) {
  const rows = next.map((x) => {
    if (x.type === "post") {
      const p = x.post, sp = window.StudioPost;
      const day = sp?.dayOf(p.scheduled_at) || "";
      const when = day === todayISO() ? "Today" : dayLabel(day, { weekday: true }).split(" ").slice(0, 2).join(" ");
      return `<div class="hm-next" data-next="${esc(x.key)}">
        <span class="hm-when${day === todayISO() ? " soon" : ""}">${esc(when)}<br>${esc(sp?.timeOf(p.scheduled_at) || "")}</span>
        <a class="hm-next-text" href="#/calendar"><span class="hm-next-name">${esc(p.title)}</span>
          <span class="hm-sub">${window.StudioMarks?.html(window.StudioMarks.of({ post: p }), { small: true }) || ""}${kindDot(p.post_type)} ${esc([p.state === "publishing" ? "Going out now" : "On Instagram", POST_TYPE[p.post_type], p.kind === "carousel" ? "Carousel" : "Reel"].filter(Boolean).join(" · "))}</span></a>
      </div>`;
    }
    if (x.type === "day") {
      const when = x.day === todayISO() ? "Today" : dayLabel(x.day, { weekday: true }).split(" ").slice(0, 2).join(" ");
      const what = ["Site day", x.phase || projectName(x.pid)].join(" · ");
      return `<div class="hm-next" data-next="${esc(x.key)}">
        <span class="hm-when${x.soon ? " soon" : ""}">${esc(when)}</span>
        <a class="hm-next-text" href="#/project/${esc(x.pid)}/shoot"><span class="hm-next-name">${esc(what)}</span>
          <span class="hm-sub">${esc(`${x.phase ? `${projectName(x.pid)} · ` : ""}${plural(x.shots, "shot")} to film for ${plural(x.posts, "post")}`)}</span></a>
        <a class="hm-go" href="#/today/${esc(x.pid)}">Shot list →</a>
      </div>`;
    }
    const i = x.idea;
    const label = x.type === "edit" ? "In edit" : "Filming";
    const sub = x.type === "edit"
      ? [POST_TYPE[i.post_type], x.revising ? "revising from your notes" : "Studio is editing it"].filter(Boolean).join(" · ")
      : [POST_TYPE[i.post_type], `${x.filmed} of ${x.total} shots filmed`].filter(Boolean).join(" · ");
    return `<div class="hm-next" data-next="${esc(x.key)}">
      <span class="hm-when">${label}</span>
      <a class="hm-next-text" href="#/idea/${esc(i.id)}"><span class="hm-next-name">${esc(i.title)}</span><span class="hm-sub">${ideaMarks(i, null, { small: true })}${esc(sub)}</span></a>
    </div>`;
  }).join("");
  return `<section class="hm-card hm-coming" aria-labelledby="hm-next">
    <h2 class="hm-card-head" id="hm-next">Coming up</h2>
    ${rows || `<p class="hm-empty">No posts, site days, edits or filming coming up.</p>`}
    <div class="hm-foot"><a class="btn-link" href="#/calendar">Posting calendar</a><a class="btn-link" href="#/grid">Grid</a>${marksKey({ cls: "hm-key" })}</div>
  </section>`;
}

// The month as post slots: one per post the targets ask for, filled once it's planned.
function monthHTML() {
  const d = month.data;
  if (!d || month.missing) return "";
  const kinds = d.kinds || {};
  const [y, mo] = String(d.month || "").split("-").map(Number);
  const name = y && mo ? MONTHS[mo - 1] : "This month";
  let slots = "", total = 0, filled = 0;
  const left = [];
  for (const k of POST_TYPES) {
    const t = Number(kinds[k]?.target) || 0, p = Number(kinds[k]?.planned) || 0, out = Number(kinds[k]?.posted) || 0;
    // Posted first (solid with a tick), then planned (solid), then still to plan (a tint).
    for (let i = 0; i < t; i++) slots += `<span class="hm-slot ${k}${i < out ? " on posted" : i < p ? " on" : ""}" data-kind="${k}" title="${esc(`${POST_TYPE[k]}${i < out ? ": posted" : i < p ? ": planned" : ""}`)}"></span>`;
    total += t;
    filled += Math.min(p, t);
    if (t > p) left.push(`<li><a href="#/month/${k}">${kindDot(k)}${esc(`${t - p} ${POST_TYPE[k]}`)}</a>
      <button type="button" class="btn small" data-add-kind="${k}" aria-label="${esc(`Add a ${POST_TYPE[k]} idea`)}">+ Add</button></li>`);
  }
  const untyped = Number(d.untyped) || 0;
  const busy = topics.job && (topics.job.state === "queued" || topics.job.state === "running");
  const foot = topics.missing ? "" : jobLine() || esc(whenLine());
  return `<section class="hm-card hm-month" aria-labelledby="hm-month">
    <div class="hm-month-head"><h2 id="hm-month">${esc(name)}</h2>
      <span class="hm-sub">${esc(`${d.planned ?? 0} of ${d.target ?? total} posts planned`)}${d.posted ? ` · ${esc(d.posted)} posted` : ""}${d.scheduled ? ` · ${esc(d.scheduled)} scheduled` : ""}${!d.posted && !d.scheduled && d.ready ? ` · ${esc(d.ready)} ready` : ""}</span></div>
    ${total ? `<div class="hm-slots" role="img" aria-label="${esc(`${total} post slots, ${filled} planned`)}">${slots}</div>` : ""}
    ${total && left.length ? `<p class="hm-still">Still to plan:</p><ul class="hm-gaps">${left.join("")}</ul>`
      : `<p class="hm-still">${total ? "Every slot this month is planned." : "No monthly targets yet."}</p>`}
    ${untyped ? `<p class="hm-sub">${esc(plural(untyped, "planned post"))} ${untyped === 1 ? "has" : "have"} no kind yet: Studio sorts ${untyped === 1 ? "it" : "them"} soon.</p>` : ""}
    <div class="hm-month-foot">
      <a href="#/content-plan">Targets and offers</a>
      ${topics.missing ? "" : `<button type="button" class="btn-link" data-suggest-topics ${offWhy(busy ? "Studio is already suggesting topics. They appear under Needs your call." : "")} title="Studio reads what Bryan said on site, the client questions and the shoot calendar, and suggests posts for this month's gaps. A minute; a few cents.">${busy ? "Suggesting…" : "Suggest topics"}</button>`}
    </div>
    ${foot ? `<p class="hm-sub" aria-live="polite">${foot}</p>` : ""}
  </section>`;
}

// "11 of 13 planned · still to plan: 1 On site, 2 Work with us" under the title (on a phone the month is
// below the jobs); a tap goes to the month card.
function monthLineHTML() {
  const d = month.data;
  if (!d || month.missing) return "";
  const kinds = d.kinds || {};
  const left = POST_TYPES.map((k) => [k, (Number(kinds[k]?.target) || 0) - (Number(kinds[k]?.planned) || 0)]).filter(([, n]) => n > 0);
  const total = Number(d.target) || POST_TYPES.reduce((n, k) => n + (Number(kinds[k]?.target) || 0), 0);
  if (!total) return "";
  const text = `${d.planned ?? 0} of ${total} planned${left.length ? ` · still to plan: ${left.map(([k, n]) => `${n} ${POST_TYPE[k]}`).join(", ")}` : " · every slot planned"}`;
  return `<a class="hm-month-line" href="#hm-month" data-month-line>${esc(text)}</a>`;
}

function projectsHTML(d) {
  const standing = (p) => (p.standing ? 1 : 0);
  const projects = [...d.active].sort((a, b) => standing(a) - standing(b) || b.id - a.id);
  const rows = projects.map((p) => {
    const ideas = d.ideas.filter((i) => i.project_id === p.id);
    const shots = ideas.filter(isReel).flatMap((i) => i.shots || []);
    const carousels = ideas.filter((i) => !isReel(i)).length;
    const stat = !ideas.length ? "No ideas yet"
      : shots.length ? `${plural(ideas.length, "idea")} · ${shots.filter(isFilmed).length}/${shots.length} shots`
        : carousels === ideas.length ? plural(carousels, "carousel") : plural(ideas.length, "idea");
    return `<a class="hm-proj" href="#/project/${esc(p.id)}"><span class="hm-proj-name">${esc(p.name)}</span><span class="hm-sub">${esc(stat)}</span></a>`;
  }).join("");
  return `<section class="hm-card hm-projects" aria-labelledby="hm-proj">
    <h2 class="hm-card-head" id="hm-proj">Projects</h2>
    ${rows || `<p class="hm-empty">No projects yet.</p>`}
    <div class="hm-foot"><button type="button" class="btn-link" id="home-new-project">+ New project</button></div>
  </section>`;
}

function questionsHTML() {
  if (topics.missing) return "";
  const n = topics.openQuestions || 0;
  return `<a class="hm-card hm-questions" href="#/questions">
    <span class="hm-q-text"><span class="hm-next-name">Client questions</span>
      <span class="hm-sub">${n ? `${esc(plural(n, "question"))} waiting to become posts` : "Add the questions clients ask"}</span></span>
    <span class="hm-go">Add →</span></a>`;
}

// ── Actions ─────────────────────────────────────────────────────────────────

let refreshHome = async () => {};
export function onHomeRefresh(fn) { refreshHome = fn; }

async function clearDismissed() {
  const n = home.data?.dismissed || 0;
  if (!n) return;
  openModal(`<h2 id="modal-title">Clear ${esc(plural(n, "dismissed suggestion"))}?</h2>
    <p>They're taken off for good. Studio still remembers them, so it won't suggest them again.</p>
    <p class="error-text" id="modal-error" role="alert" hidden></p>
    <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button>
      <button type="button" class="btn danger" id="hm-clear-yes">Clear ${esc(n)}</button></div>`, async (e) => {
    if (e.target.id !== "hm-clear-yes") return;
    e.target.disabled = true;
    try {
      const rows = must(await sb.from("ideas").delete().eq("status", "dismissed").not("suggestion", "is", null).is("production", null).select("id"));
      closeModal();
      toast(`Cleared ${plural(rows.length, "dismissed suggestion")}.`);
      await refreshHome(true);
    } catch (err) { e.target.disabled = false; formError(err.message); }
  });
}

root().addEventListener("click", async (e) => {
  const line = e.target.closest("[data-month-line]");
  if (line) { e.preventDefault(); $("#hm-month")?.closest(".hm-card")?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  const go = e.target.closest("a[data-mark]");
  if (go) mark(go.dataset.mark);
  const t = e.target.closest("button, .hm-slot");
  if (!t) return;
  try {
    if (t.id === "home-new-idea") openQuickAdd({ from: "home" });   // its kind: the month's shortest (setKindHint)
    else if (t.dataset.addKind) { mark("month_gap_add", { kind: t.dataset.addKind }); openQuickAdd({ from: "home", kind: t.dataset.addKind }); }
    else if (t.id === "home-new-project") openNewProject();
    else if (t.matches("[data-clear-dismissed]")) await clearDismissed();
    else if (t.matches("[data-post-retry]")) openSchedule(Number(t.dataset.postRetry));
    else if (t.matches(".hm-slot")) location.hash = `#/month/${t.dataset.kind}`;
    else if (t.matches("[data-sugg-act], [data-suggest-topics]")) {
      await suggestClick(t, () => renderHome(true));
      await refreshHome(true);
    }
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
    renderHome(true);
  }
});
