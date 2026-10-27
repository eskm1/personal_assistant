// Ideas: one project's posts grouped by what to do next (Ready to make, Film these, Waiting for your
// call, Parked, and Made folded at the foot), not by series. Each card is the kind's dot, the title and
// one line of status. The project's name is the picker; its tools (shoot plan, Today on site, brief,
// series, find ideas, new project, archive) sit in one ⋯ menu beside it. One orange button: + New idea.
import {
  $, $$, BRAND, ACCOUNTS, oneBrand, esc, sb, must, invoke, toast, options, store, recall, plural,
  prepShots, progress, jobView, jobActive, kickDevelop, openModal, closeModal, formError, askFirst, nameOf, PRODUCTION,
  state, isFilmed, httpsUrl, laptopWait, ago, dayOf, todayISO, parseDay, dayLabel, shortDay, withKinds, kindsQuery, kindDot,
  schema, POST_TYPES, POST_TYPE, isCarousel, mark, offWhy, isOwner, gridCols, ideaMarks, marksKey, missingTable } from "./core.js";
import { shoot, loadShoot, renderShoot } from "./shoot.js";

export const plan = {
  projects: [], pid: null, board: null, sig: "", topSig: "", shellFor: null, order: [],
  // Shoot plan | Ideas, remembered on this device (the shoot plan is in the ⋯ menu).
  mode: ["shoot", "ideas"].includes(recall("studio.planMode")) ? recall("studio.planMode") : null,
  more: new Set(),   // groups showing every card ("Show N more")
};
export function planMode() { return plan.mode || "ideas"; }

const IDEA_COLS = "id,project_id,arc_id,title,brand,format,decision,status,production,position,created_by,created_at,updated_at,"
  + "shots(id,position,filmed_at,takes(id,clip_id,clips(verdict,recorded_at))),votes(user_id,vote),comments(count)";
const JOB_COLS = "id,kind,runner,project_id,idea_id,state,result,error,created_at,started_at,updated_at";
// Ideas Studio suggested (ideas.suggestion) come with a database update: asked for once it's there.
const ideaCols = () => withKinds(IDEA_COLS) + (schema.suggestion ? ",suggestion" : "") + gridCols();

export const currentProject = () => plan.projects.find((p) => p.id === plan.pid) || null;
const folderName = (s) => String(s || "").replace(/[<>:"/\\|?*]/g, "").trim().slice(0, 80);

// A computer: the groups side by side, Parked as a dimmed column. Narrower: one under another.
const DESK = window.matchMedia("(min-width: 900px)");
DESK.addEventListener("change", () => { if ($("#plan-board") && plan.board) renderPlanBoard(true, true); });

// Asked for at sign-in, with your profile (app.js), so the first page doesn't wait for it after:
// the first loadProjects() takes that one, every later one asks again.
let early = null;
export function prefetchProjects() {
  early = loadProjectsNow();
  early.catch(() => {});
}
export function loadProjects() {
  const p = early || loadProjectsNow();
  early = null;
  return p;
}
async function loadProjectsNow() {
  const saved = Number(recall("studio.project"));
  // A first visit (nothing remembered on this device) opens on the project with work: asked alongside.
  const first = !saved && !plan.pid ? workFirst() : null;
  plan.projects = must(await sb.from("projects").select("*").order("status").order("id", { ascending: false }));
  if (!plan.projects.some((p) => p.id === plan.pid)) {
    const active = plan.projects.filter((p) => p.status === "active");
    const pick = plan.projects.find((p) => p.id === saved) || (first && pickWorkProject(active, await first))
      || active.find((p) => !p.standing) || active[0] || plan.projects[0] || {};
    plan.pid = pick.id ?? null;
    // Kept for this device, so the next visits don't ask again (and a project picked later replaces it).
    if (first && plan.pid) store("studio.project", String(plan.pid));
  }
}
// The next site day, else the latest change to an idea: which client project has work now.
async function workFirst() {
  const [days, ideas] = await Promise.all([
    sb.from("site_days").select("project_id,day").gte("day", todayISO()).order("day").limit(20).then(must).catch(() => []),
    sb.from("ideas").select("project_id,updated_at").neq("status", "dismissed").order("updated_at", { ascending: false }).limit(30).then(must).catch(() => []),
  ]);
  return { days, ideas };
}
function pickWorkProject(active, work) {
  const client = new Map(active.filter((p) => !p.standing).map((p) => [p.id, p]));
  for (const d of work?.days || []) if (client.has(d.project_id)) return client.get(d.project_id);
  for (const i of work?.ideas || []) if (client.has(i.project_id)) return client.get(i.project_id);
  return null;
}
// The picker: client projects first (newest first), the standing ones (Everyday posts, Topics) last.
const standingLast = (a, b) => (a.standing ? 1 : 0) - (b.standing ? 1 : 0);

export function selectProject(pid) {
  if (pid === plan.pid) return;
  plan.pid = pid;
  plan.board = null;
  plan.more = new Set();
  if (pid) store("studio.project", String(pid));
}

// Everything the board shows for one project.
export async function loadBoard() {
  const pid = plan.pid;
  if (!pid) { plan.board = null; return; }
  const seq = (plan.seq = (plan.seq || 0) + 1);
  const stale = () => pid !== plan.pid || seq !== plan.seq;
  // The shoot plan's tables come with a database update: if they're missing, the board still works.
  const shootLoad = loadShoot(pid).catch((err) => { shoot.error = err.message; return null; });
  // The jobs come at the same time: the project's own, and its ideas' (one may have moved here
  // from another project with its jobs).
  const kinds = ["organise", "develop", "ideas"];
  const [arcs, ideas, ownJobs, ideaJobs, postRows] = await Promise.all([
    sb.from("arcs").select("*").eq("project_id", pid).order("position").order("id").then(must),
    kindsQuery(() => sb.from("ideas").select(ideaCols()).eq("project_id", pid).neq("status", "dismissed")
      .order("position").order("id").then(must)),
    sb.from("jobs").select(JOB_COLS).in("kind", kinds).eq("project_id", pid).order("id", { ascending: false }).limit(400).then(must),
    sb.from("jobs").select(`${JOB_COLS},ideas!inner(project_id)`).in("kind", kinds).eq("ideas.project_id", pid)
      .order("id", { ascending: false }).limit(400).then(must),
    // Each idea's Instagram post, for its card's mark (Scheduled, Posted): nothing before the posting update.
    sb.from("posts").select("id,idea_id,state,scheduled_at,posted_at,ideas!inner(project_id)").eq("ideas.project_id", pid)
      .neq("state", "cancelled").order("id").limit(400).then(must).catch((err) => (missingTable(err) ? [] : [])),
  ]);
  await shootLoad;
  const live = new Set(ideas.map((i) => i.id));
  const byId = new Map(ownJobs.map((j) => [j.id, j]));
  for (const { ideas: _, ...j } of ideaJobs) if (live.has(j.idea_id)) byId.set(j.id, j);
  const jobs = [...byId.values()].sort((a, b) => b.id - a.id).slice(0, 400);
  if (stale()) return;
  const develop = new Map();
  for (const j of jobs) if (j.kind === "develop" && j.idea_id && !develop.has(j.idea_id)) develop.set(j.idea_id, j);
  for (const i of ideas) {
    i.shots = prepShots(i.shots);
    i.progress = progress(i.shots);
    const up = [], down = [];
    let mine = 0;
    for (const v of i.votes || []) {
      (v.vote > 0 ? up : down).push(nameOf(v.user_id) || "Someone");
      if (v.user_id === state.me?.id) mine = v.vote;
    }
    i.votes = { up, down, mine };
    i.comments = i.comments?.[0]?.count ?? 0;
    i.filmedCount = i.shots.filter(isFilmed).length;
    // A carousel: only its slides count is needed here.
    i.slideCount = Array.isArray(i.slides) ? i.slides.length : 0;
    delete i.slides;
  }
  const postOf = new Map();
  for (const { ideas: _, ...p } of postRows) if (!postOf.has(p.idea_id) || p.state === "posted") postOf.set(p.idea_id, p);
  for (const i of ideas) i.post = postOf.get(i.id) || null;
  plan.board = {
    project: plan.projects.find((p) => p.id === pid), arcs, ideas, jobs, develop,
    organise: jobs.find((j) => j.kind === "organise" && j.project_id === pid) || null,
    ideasJob: jobs.find((j) => j.kind === "ideas" && j.project_id === pid) || null,
  };
  // The order the board shows them in: the idea page's "2 of 8" and previous / next.
  plan.order = GROUPS.flatMap((g) => ideasIn(g.key).map((i) => i.id));
  kickDevelop(jobs);
}

// ── What to do next ─────────────────────────────────────────────────────────

const isMade = (i) => i.status === "made" || !!i.production;
// Ready to make: every shot filmed (a carousel: a cover and at least one more slide).
const readyToMake = (i) => (isCarousel(i) ? i.slideCount >= 2 : i.shots.length > 0 && i.filmedCount === i.shots.length);
export function groupOf(i) {
  if (isMade(i)) return "made";
  if (i.decision === "parked") return "parked";
  if (i.decision === "go") return readyToMake(i) ? "ready" : "film";
  return "waiting";
}
const GROUPS = [
  { key: "ready", title: "Ready to make", empty: "A Go post lands here once every shot is filmed." },
  { key: "film", title: "Film these", empty: "No Go posts with shots still to film." },
  { key: "waiting", title: "Waiting for your call", empty: "Nothing waiting for a decision." },
  { key: "parked", title: "Parked", empty: "Nothing parked." },
  { key: "made", title: "Made" },
];
const ideasIn = (key) => (plan.board?.ideas || []).filter((i) => groupOf(i) === key);

// The next site day each Go idea has a shot to film on (the shoot plan, loaded with the board).
function nextDays() {
  const d = shoot.pid === plan.pid ? shoot.data : null;
  const next = new Map();
  for (const day of d?.upcoming || []) for (const s of day.todo || []) if (!next.has(s.idea_id)) next.set(s.idea_id, day.day);
  return next;
}

// ── Rendering ───────────────────────────────────────────────────────────────

const CHEVRON = `<svg class="chev" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6"/></svg>`;
const DOTS = `<svg class="dots" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`;

export function renderPlanner(force = false) {
  const root = $("#planner");
  if (!plan.projects.length) {
    plan.shellFor = null;
    root.innerHTML = `<div class="plan-empty"><h2>Start a plan</h2>
      <p class="muted">A project is one job, like Kensington. Throw post ideas at it, let Claude sort them into series with a shot list for each, then tick shots off on site.</p>
      <button class="btn act lg" id="plan-new">New project</button></div>`;
    return;
  }
  // The shell (title, tools, banner) is rebuilt only for another project.
  const rebuilt = plan.shellFor !== plan.pid || !$("#plan-board");
  if (rebuilt) {
    closeMenus(false);
    root.innerHTML = plannerShellHTML();
    plan.shellFor = plan.pid;
    plan.sig = "";
    plan.topSig = "";
  } else syncShell();
  const mode = planMode();
  root.classList.toggle("shoot-mode", mode === "shoot");
  $("#shoot-head").hidden = $("#shoot-view").hidden = mode !== "shoot";
  $("#ideas-view").hidden = mode !== "ideas";
  $("#plan-add-one").hidden = mode !== "ideas";
  if (!plan.board || plan.board.project?.id !== plan.pid) {
    // Loading: the line sits where the banner goes and the board waits hidden, so the banner and the
    // cards arrive together (a banner landing above drawn cards pushed them down).
    $("#plan-board").innerHTML = "";
    if (mode === "ideas") {
      $("#ideas-view").hidden = true;
      $("#plan-banner").innerHTML = `<p class="muted plan-loading"><span class="spin"></span> Loading the ideas…</p>`;
      plan.topSig = "";
    }
    if (mode === "shoot") $("#shoot-view").innerHTML = `<p class="muted"><span class="spin"></span> Loading the shoot plan…</p>`;
    return;
  }
  renderPlanTop();
  if (mode === "shoot") renderShoot(force || rebuilt);
  else renderPlanBoard(force || rebuilt, rebuilt);
}

function plannerShellHTML() {
  const p = currentProject() || {};
  return `<div class="ideas-head">
      <div class="proj-pick">
        <button type="button" class="proj-btn" id="plan-project" aria-haspopup="menu" aria-expanded="false" aria-controls="proj-menu">
          <span class="proj-name" id="proj-title" data-name="${esc(p.name)}">${titleHTML(p.name)}</span>
          <span class="proj-status" id="plan-progress" aria-live="polite"></span>
          <span class="sr-only">Change project</span>
        </button>
        <div class="menu proj-menu" id="proj-menu" role="menu" aria-label="Projects" hidden></div>
      </div>
      <div class="tools">
        <button type="button" class="btn tools-btn" id="plan-tools" aria-haspopup="menu" aria-expanded="false" aria-controls="tools-menu"
          aria-label="Project menu: shoot plan, Today on site, brief, series, find ideas, new project, archive"><span class="tools-word">Project</span>${DOTS}</button>
        <div class="menu tools-menu" id="tools-menu" role="menu" aria-label="Project" hidden></div>
      </div>
      <button type="button" class="btn act lg plan-add" id="plan-add-one">+ New idea</button>
    </div>
    <div id="plan-banner" aria-live="polite"></div>
    <div class="mode-head" id="shoot-head" hidden>
      <button type="button" class="btn-link" data-plan-mode="ideas">‹ Ideas</button><h2>Shoot plan</h2>
    </div>
    <section class="shoot" id="shoot-view" aria-label="Shoot plan" hidden></section>
    <div id="ideas-view"><div class="board-groups" id="plan-board"></div></div>`;
}

// The name with the picker's chevron kept on the line of its last word.
function titleHTML(name) {
  const words = String(name || "").trim().split(/\s+/);
  const last = words.pop() || "";
  return `${esc(words.join(" "))}${words.length ? " " : ""}<span class="nowrap">${esc(last)}${CHEVRON}</span>`;
}

// Someone else changed the project: its name.
function syncShell() {
  const p = currentProject();
  if (!p) return;
  const t = $("#proj-title");
  if (t && t.dataset.name !== p.name) { t.dataset.name = p.name; t.innerHTML = titleHTML(p.name); }
  if (!$("#proj-menu").hidden) renderProjectMenu();
}

// The line under the title, the one banner, and the ⋯ menu's live parts.
export function renderPlanTop() {
  const b = plan.board;
  if (!b || !$("#plan-progress")) return;
  const job = jobView(b.organise);
  const seen = recall(`studio.organiseSeen.${b.project.id}`);
  const r = job?.result || {};
  const newIds = Array.isArray(r.idea_ids) ? r.idea_ids : [];
  const drafts = newIds.map((id) => ({ id, job: jobView(b.develop.get(id)), idea: b.ideas.find((i) => i.id === id) }));
  const news = footageNews(b);
  const ideasJob = jobView(b.ideasJob);
  const status = statusLine(b);
  const sig = JSON.stringify([status, job, seen, drafts.map((d) => [d.job?.state, d.job?.error, d.idea?.title]), news, ideasJob,
    plan.ideasRefused === b.project.id, state.laptop?.last_seen ? Math.floor(Date.parse(state.laptop.last_seen) / 60e3) : 0,
    state.laptop?.info?.footage_ideas?.waiting_clips || 0, b.arcs.length, b.project.brief, b.project.status]);
  if (sig === plan.topSig) return;
  plan.topSig = sig;
  plan.footageNews = news;
  $("#plan-progress").textContent = status;
  $("#plan-banner").innerHTML = bannerHTML(b, job, seen, r, newIds, drafts, news);
  if (!$("#tools-menu").hidden) renderToolsMenu();
}

// "5 of 13 shots filmed · next site day Thu 23": the Go posts' shots, and the shoot plan's next visit.
function statusLine(b) {
  const p = b.project || {};
  const shots = b.ideas.filter((i) => i.decision === "go" && !isMade(i) && !isCarousel(i)).flatMap((i) => i.shots);
  const filmed = shots.filter(isFilmed).length;
  const d = shoot.pid === plan.pid ? shoot.data : null;
  const next = d?.upcoming?.[0]?.day;
  return [p.status === "archived" ? "Archived" : "",
    shots.length ? `${filmed} of ${plural(shots.length, "shot")} filmed` : b.ideas.length ? "No shots to film yet" : "No ideas yet",
    next ? (next === todayISO() ? "site day today" : `next site day ${shortDay(next)}`) : ""].filter(Boolean).join(" · ");
}

// One banner at most: Claude organising (or what it did), else ideas new from the footage.
function bannerHTML(b, job, seen, r, newIds, drafts, news) {
  if (jobActive(job)) {
    return `<p class="plan-banner organise-status"><span class="spin"></span> Claude is organising your ideas. About a minute; the board updates when it's done.</p>`;
  }
  if (job && String(job.id) !== seen && (job.state === "done" || job.state === "failed")) {
    const close = `<button class="icon-btn" data-dismiss-organise="${esc(job.id)}" aria-label="Dismiss" title="Dismiss">×</button>`;
    if (job.state === "failed") {
      return `<div class="organise-result bad">${close}<strong>Organising failed.</strong> <span>${esc(job.error || "")}</span> Try again, or add them as ideas.</div>`;
    }
    const nIdeas = Array.isArray(r.ideas) ? r.ideas.length : Number(r.ideas ?? newIds.length) || 0;
    const nArcs = Array.isArray(r.arcs) ? r.arcs.length : Number(r.arcs) || 0;
    const done = drafts.filter((d) => d.job?.state === "done").length;
    const failed = drafts.filter((d) => d.job?.state === "failed");
    const waiting = drafts.length - done - failed.length;
    return `<div class="organise-result">${close}
      <strong>Claude added ${plural(nIdeas, "idea")}${nArcs ? ` and ${plural(nArcs, "new series", "new series")}` : ""}.</strong>
      ${r.notes ? `<p>${esc(r.notes)}</p>` : ""}
      ${drafts.length ? `<p class="drafting">${waiting ? `<span class="spin"></span> ` : ""}Storyboards: ${done} of ${drafts.length} drafted${waiting ? `, ${waiting} still being drafted…` : "."}</p>` : ""}
      ${failed.length ? `<ul class="draft-fails">${failed.map((d) => `<li>Couldn't draft “${esc(d.idea?.title || `idea ${d.id}`)}”: ${esc(d.job.error || "unknown error")} <button class="btn small" data-open-idea="${esc(d.id)}">Open</button></li>`).join("")}</ul>` : ""}
    </div>`;
  }
  if (news) {
    return `<div class="footage-news" role="status">
      ${FOOTAGE_ICON}<p><strong>${esc(plural(news.ids.length, "new idea"))}</strong> from ${esc(news.when)}.</p>
      <button class="btn small" data-footage-show>See them</button>
      <button class="icon-btn" data-footage-dismiss aria-label="Dismiss" title="Dismiss">×</button>
    </div>`;
  }
  return "";
}

// ── Ideas from new footage ──────────────────────────────────────────────────
// After a shoot day the Studio machine reads the new clips and suggests posts into the
// project's "From your footage" series (footage_ideas.py makes it). They wait for your call;
// the banner says when new ones arrived since you last looked, and ⋯ can ask for a look now.

const FOOTAGE_ARC = "from your footage";
export const fromFootage = (a) => !!a && String(a.title || "").trim().toLowerCase() === FOOTAGE_ARC;
const SUGGEST_ARC = "studio suggests";
const FOOTAGE_ICON = `<svg class="footage-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></svg>`;
const seenKey = (pid) => `studio.footageSeen.${pid}`;

// "Saturday's footage", from the day the new ideas' clips were filmed.
function footageWhen(day) {
  const d = parseDay(day), t = parseDay(todayISO());
  if (!d) return "your footage";
  const days = Math.round((t - d) / 864e5);
  if (days === 0) return "today's footage";
  if (days === 1) return "yesterday's footage";
  if (days > 1 && days < 7) return `${d.toLocaleDateString("en-GB", { weekday: "long" })}'s footage`;
  return `the footage of ${dayLabel(day, { weekday: false })}`;
}
const footageDay = (i) => i.shots.flatMap((s) => s.takes.map((t) => dayOf(t.clips?.recorded_at))).filter(Boolean).sort().pop();

// Ideas in the footage series that arrived since this device last looked (the last week, the first time).
function footageNews(b) {
  const arc = b.arcs.find(fromFootage);
  if (!arc) return null;
  const seen = Date.parse(recall(seenKey(b.project.id)) || "") || Date.now() - 7 * 864e5;
  const fresh = b.ideas.filter((i) => i.arc_id === arc.id && Date.parse(i.created_at || "") > seen);
  if (!fresh.length) return null;
  const days = fresh.map(footageDay).filter(Boolean).sort();
  return { ids: fresh.map((i) => i.id), latest: fresh.map((i) => i.created_at).sort().pop(), when: footageWhen(days.pop()) };
}

// Seen: the banner goes; the new cards keep "New" until the page is reloaded.
function footageSeen(news) {
  if (!news || !plan.board) return;
  store(seenKey(plan.board.project.id), news.latest || new Date().toISOString());
  plan.footageFresh = new Set([...(plan.footageFresh || []), ...news.ids]);
  plan.topSig = "";
  plan.sig = "";
}

// "See them" (and #/project/<id>/footage): the group they wait in, opened out, with the new ones marked.
export function showFootageColumn() {
  const news = plan.footageNews;
  footageSeen(news);
  if (planMode() !== "ideas") { plan.mode = "ideas"; store("studio.planMode", "ideas"); }
  const fresh = plan.footageFresh || new Set();
  for (const g of GROUPS) if (ideasIn(g.key).some((i) => fresh.has(i.id))) plan.more.add(g.key);
  renderPlanner(true);
  const first = [...fresh].map((id) => $(`.card[data-plan-idea="${id}"]`)).find(Boolean);
  const grp = first?.closest(".grp") || $(".grp-waiting");
  if (!grp) return;
  if (grp.tagName === "DETAILS") grp.open = true;
  grp.scrollIntoView({ behavior: "smooth", block: "start" });
  grp.classList.remove("flash");
  void grp.offsetWidth;
  grp.classList.add("flash");
  (first || grp.querySelector(".grp-title"))?.focus({ preventScroll: true });
}

function findIdeasStatus(job) {
  const busy = jobActive(job);
  // Studio does this by itself once a shoot day's uploads go quiet (laptop_status.info).
  const waiting = Number(state.laptop?.info?.footage_ideas?.waiting_clips) || 0;
  const soon = !busy && waiting ? ` Studio will suggest ideas from ${esc(plural(waiting, "new clip"))} once uploads stop.` : "";
  if (plan.ideasRefused === plan.pid && !job) return `<span class="error-text">Needs a one-time update: run cloud_setup.py --update</span>`;
  if (!job) return soon.trim() || "Studio reads the last two weeks of footage.";
  if (job.state === "queued") return `<span class="spin"></span> Asked Studio. ${esc(laptopWait())}`;
  if (job.state === "running") return `<span class="spin"></span> Studio is reading the new footage. A minute or two.`;
  if (job.state === "failed") return `<span class="error-text">Couldn't look: ${esc(job.error || "unknown error")}</span>${soon}`;
  const r = job.result || {};
  const count = (v) => (Array.isArray(v) ? v.length : Number(v) || 0);
  const n = count(r.new_ideas ?? r.ideas), clips = count(r.attached);
  const when = job.updated_at ? ` (${esc(ago(job.updated_at))})` : "";
  return (r.clips === 0 ? `Last look${when}: no new footage from the last two weeks.`
    : n || clips ? `Last look${when}: ${esc(plural(n, "new idea"))}${clips ? `, and ${esc(plural(clips, "clip"))} added to planned posts` : ""}.`
      : `Last look${when}: no new ideas in the footage this time.`) + soon;
}

async function findIdeas() {
  const pid = plan.pid;
  const { error } = await sb.from("jobs").insert({ kind: "ideas", runner: "laptop", project_id: pid, state: "queued", payload: {} });
  if (error) {
    // The kind (or who may ask for it) arrives with a database update.
    if (/23514|42501|check constraint|row-level security|violates/i.test(`${error.code || ""} ${error.message || ""}`)) {
      plan.ideasRefused = pid;
      plan.topSig = "";
      renderPlanTop();
      toast("Needs a one-time update: run cloud_setup.py --update", 6000);
      return;
    }
    throw new Error(error.message);
  }
  plan.ideasRefused = null;
  if (plan.board?.project.id === pid) {
    plan.board.ideasJob = { kind: "ideas", runner: "laptop", project_id: pid, state: "queued", created_at: new Date().toISOString() };
    renderPlanTop();
  }
  toast("Asked Studio to look for ideas in the new footage.");
  await refresh();
}

// ── The groups ──────────────────────────────────────────────────────────────

// force: re-render even if nothing changed. mine: the person here changed the board's own state.
export function renderPlanBoard(force = false) {
  const b = plan.board;
  const el = $("#plan-board");
  if (!b || !el) return;
  const desk = DESK.matches;
  const next = nextDays();
  const drafting = b.ideas.map((i) => jobView(b.develop.get(i.id))?.state || "");
  const sig = JSON.stringify([b.arcs.map((a) => [a.id, a.title]), b.ideas, drafting, [...next], desk, [...plan.more],
    [...(plan.footageFresh || [])], plan.footageNews?.ids || []]);
  if (!force && sig === plan.sig) return;
  plan.sig = sig;
  const focused = document.activeElement?.closest?.("#plan-board [data-plan-idea], #plan-board [data-more], #plan-board [data-less], #plan-board summary");
  const refocus = focused && (focused.dataset.planIdea ? `[data-plan-idea="${focused.dataset.planIdea}"]`
    : focused.dataset.more ? `[data-more="${focused.dataset.more}"], [data-less="${focused.dataset.more}"]`
      : focused.dataset.less ? `[data-less="${focused.dataset.less}"], [data-more="${focused.dataset.less}"]`
        : focused.closest("[data-fold]") ? `[data-fold="${focused.closest("[data-fold]").dataset.fold}"] > summary` : "");
  const lists = Object.fromEntries(GROUPS.map((g) => [g.key, ideasIn(g.key)]));
  plan.order = GROUPS.flatMap((g) => lists[g.key].map((i) => i.id));
  const ctx = { b, next, fresh: new Set([...(plan.footageFresh || []), ...(plan.footageNews?.ids || [])]), desk };
  const html = GROUPS.map((g) => groupHTML(g, lists[g.key], ctx)).join("");
  el.classList.toggle("desk", desk);
  el.innerHTML = b.ideas.length ? html + `<div class="board-key">${marksKey()}</div>` : `<div class="board-empty"><p><strong>No ideas in ${esc(b.project?.name || "this project")} yet.</strong></p>
    <p class="muted">Press + New idea: one idea, or a list for Claude to sort into series with a storyboard each.</p></div>`;
  if (refocus) el.querySelector(refocus)?.focus({ preventScroll: true });
}

const foldKey = (k) => `studio.fold.${k}`;
function groupHTML(g, list, ctx) {
  // A computer shows the first four groups as columns (an empty one says so); Made folds under them.
  // Narrower, an empty group isn't shown, and Parked and Made fold.
  const folded = g.key === "made" || (g.key === "parked" && !ctx.desk);
  if (!list.length && (folded || !ctx.desk)) return "";
  const limit = ctx.desk ? 6 : 3;
  const all = plan.more.has(g.key);
  const shown = all ? list : list.slice(0, limit);
  const more = list.length - shown.length;
  const cards = shown.map((i) => cardHTML(i, ctx)).join("") || `<p class="muted small grp-empty">${esc(g.empty)}</p>`;
  const toggle = more > 0 ? `<button type="button" class="btn-link grp-more" data-more="${g.key}">Show ${more} more</button>`
    : all && list.length > limit ? `<button type="button" class="btn-link grp-more" data-less="${g.key}">Show fewer</button>` : "";
  // The team decides Go / Park too (Bryan, 2026-10-20): the group says what to do, not whose call it is.
  const title = `${esc(g.key === "waiting" && !isOwner() ? "Waiting for Go or Park" : g.title)} · ${list.length}`;
  if (folded) {
    const open = recall(foldKey(g.key)) === "1";
    return `<details class="grp grp-${g.key}" data-fold="${g.key}" ${open ? "open" : ""}>
      <summary class="grp-title">${title}</summary><div class="grp-cards">${cards}</div>${toggle}</details>`;
  }
  return `<section class="grp grp-${g.key}" aria-labelledby="grp-${g.key}">
    <h2 class="grp-title" id="grp-${g.key}" tabindex="-1">${title}</h2><div class="grp-cards">${cards}</div>${toggle}</section>`;
}

// One line under the title: the kind, then what's next (or what's going on).
function cardLine(i, ctx) {
  const carousel = isCarousel(i);
  const g = groupOf(i);
  const n = i.shots.length, filmed = i.filmedCount || 0;
  const dev = jobView(ctx.b.develop.get(i.id));
  const slides = i.slideCount ? plural(i.slideCount, "slide") : "No slides yet";
  let what = "", busy = false, bad = false;
  if (jobActive(dev)) { what = carousel ? "Writing slides…" : "Drafting storyboard…"; busy = true; }
  else if (dev?.state === "failed" && !(carousel ? i.slideCount : n)) { what = carousel ? "Slides failed" : "Storyboard failed"; bad = true; }
  else if (g === "made") {
    const prod = i.production?.status;
    what = prod ? PRODUCTION[prod] || prod : carousel ? "Studio is laying it out" : "In the edit";
  } else if (g === "ready") what = carousel ? `${slides}, ready to make` : n === 1 ? "Its shot is filmed" : `All ${n} shots filmed`;
  else if (g === "film") {
    const day = ctx.next.get(i.id);
    what = carousel ? slides : n ? `${filmed} of ${plural(n, "shot")}${day ? ` · ${shortDay(day)}` : ""}` : "No shots yet";
  } else {
    const arc = ctx.b.arcs.find((a) => a.id === i.arc_id);
    const fresh = ctx.fresh.has(i.id);
    if (g === "waiting" && fromFootage(arc)) what = `${fresh ? "New · " : ""}From ${footageWhen(footageDay(i))}`;
    else if (g === "waiting" && (i.suggestion || String(arc?.title || "").trim().toLowerCase() === SUGGEST_ARC)) what = "Studio suggested";
    else what = carousel ? slides : n ? plural(n, "shot") : g === "waiting" ? "No shots yet" : "";
  }
  const kind = POST_TYPE[i.post_type] || "";
  const text = [kind, carousel ? "Carousel" : "", what].filter(Boolean).join(" · ");
  return { text, busy, bad };
}

function cardHTML(i, ctx) {
  const line = cardLine(i, ctx);
  const g = groupOf(i);
  const ups = i.votes.up.length;
  const label = [i.title, line.text, ups ? `${ups} keen` : "", i.comments ? plural(i.comments, "comment") : ""].filter(Boolean).join(". ");
  const marks = ideaMarks(i, i.post, { small: true, title: false });
  const status = window.StudioMarks?.of({ production: i.production, post: i.post, format: i.format, slides_count: i.slideCount }).text || "";
  return `<button type="button" class="card idea-card ${g}" data-plan-idea="${esc(i.id)}" aria-label="${esc(`${label}. ${status}`)}">
    <span class="card-marks">${marks}</span>
    <span class="card-title">${esc(i.title)}</span>
    <span class="card-line${line.bad ? " bad-text" : ""}">${line.busy ? `<span class="spin"></span>` : kindDot(i.post_type)}<span>${esc(line.text || "Not sorted yet")}</span></span>
  </button>`;
}

// ── Menus: the project picker and ⋯ ─────────────────────────────────────────

const MENUS = { project: ["#plan-project", "#proj-menu"], tools: ["#plan-tools", "#tools-menu"] };
function setMenu(which, open) {
  const [btn, menu] = MENUS[which];
  if (!$(menu)) return;
  if (open) { if (which === "project") renderProjectMenu(); else renderToolsMenu(); }
  $(menu).hidden = !open;
  $(btn).setAttribute("aria-expanded", String(open));
  if (open) $(menu).querySelector("[role^=menuitem]:not(:disabled)")?.focus();
}
function toggleMenu(which) {
  const open = $(MENUS[which][1])?.hidden;
  for (const w of Object.keys(MENUS)) if (w !== which) setMenu(w, false);
  setMenu(which, !!open);
}
function closeMenus(refocus = true) {
  for (const [w, [btn, menu]] of Object.entries(MENUS)) {
    if (!$(menu) || $(menu).hidden) continue;
    const had = $(menu).contains(document.activeElement);
    setMenu(w, false);
    if (refocus && had) $(btn).focus();
  }
}

function renderProjectMenu() {
  const active = plan.projects.filter((x) => x.status === "active").sort(standingLast);
  const archived = plan.projects.filter((x) => x.status !== "active");
  const item = (x) => `<a role="menuitemradio" aria-checked="${x.id === plan.pid}" href="#/project/${esc(x.id)}" data-pick-project="${esc(x.id)}">
    <span class="pm-name">${esc(x.name)}</span>${x.id === plan.pid ? `<span class="pm-tick" aria-hidden="true">✓</span>` : ""}</a>`;
  $("#proj-menu").innerHTML = active.map(item).join("")
    + (archived.length ? `<div class="menu-label" role="presentation">Archived</div>${archived.map(item).join("")}` : "");
}

function renderToolsMenu() {
  const p = currentProject() || {};
  const b = plan.board;
  const shootMode = planMode() === "shoot";
  const job = jobView(b?.ideasJob);
  const brief = (p.brief || "").split("\n")[0].slice(0, 60);
  const sub = (t) => `<span class="menu-sub">${t}</span>`;
  $("#tools-menu").innerHTML = `
    <button role="menuitem" data-tool="mode">${shootMode ? "Ideas" : "Shoot plan"}${sub(shootMode ? "Back to the ideas" : "Phases, site days and what to film on each")}</button>
    <button role="menuitem" data-tool="today">Today on site${sub("The shot list for today's visit, for a phone")}</button>
    <button role="menuitem" data-tool="brief">Brief and inbox folder${sub(brief ? esc(brief) + ((p.brief || "").length > 60 ? "…" : "") : "Add what the job is")}</button>
    <button role="menuitem" data-tool="series">Series${sub(b?.arcs.length ? esc(plural(b.arcs.length, "series", "series")) : "Group posts into a run")}</button>
    <button role="menuitem" data-tool="find" ${offWhy(jobActive(job) ? `Studio is already looking. New ideas land in ${isOwner() ? "Waiting for your call" : "Waiting for Go or Park"}.` : "")}>${jobActive(job) ? "Finding ideas…" : "Find ideas in new footage"}${sub(findIdeasStatus(job))}</button>
    <div class="menu-rule" role="separator"></div>
    <button role="menuitem" data-tool="new">New project</button>
    <button role="menuitem" data-tool="archive"${p.status === "archived" ? "" : ` class="danger"`}>${p.status === "archived" ? "Unarchive project" : "Archive project…"}</button>`;
}

// ── Brief and series ────────────────────────────────────────────────────────

const folderHint = (folder) => `Footage dropped in OneDrive <code>Studio Inbox/${esc(folderName(folder) || "…")}</code> is filed to this project and matched to its shots.`;

function openBrief() {
  const p = currentProject();
  if (!p) return;
  openModal(`<h2 id="modal-title">Brief and inbox folder</h2>
    <form class="form">
      <label>Name<input id="proj-name" maxlength="120" value="${esc(p.name)}" required></label>
      ${oneBrand ? "" : `<label>Account<select id="proj-brand">${options(ACCOUNTS, BRAND, p.brand)}</select></label>`}
      <label>Brief<textarea id="proj-brief" rows="5" maxlength="4000" placeholder="What's the job, who's the client, what should the posts show? Claude reads this when it organises ideas.">${esc(p.brief)}</textarea></label>
      <label>Inbox folder<input id="proj-folder" maxlength="80" value="${esc(p.folder)}" placeholder="${esc(folderName(p.name))}"></label>
      <p class="muted small" id="proj-folder-hint">${folderHint(p.folder || p.name)}</p>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="proj-save">Save</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "proj-save") return;
    const name = $("#proj-name").value.trim();
    if (!name) return formError("The project needs a name.");
    e.target.disabled = true;
    try {
      must(await sb.from("projects").update({ name, brand: $("#proj-brand")?.value || p.brand || ACCOUNTS[0], brief: $("#proj-brief").value.trim(), folder: folderName($("#proj-folder").value) }).eq("id", p.id));
    } catch (err) { e.target.disabled = false; return formError(err.message); }
    await closeModal();
    await saveProject({}, "Project saved.", false);
  });
  const hint = () => { $("#proj-folder-hint").innerHTML = folderHint($("#proj-folder").value.trim() || $("#proj-name").value.trim()); };
  $("#proj-name").addEventListener("input", hint);
  $("#proj-folder").addEventListener("input", hint);
}

// The project's series (arcs): rename, say what each is for, reorder, delete, add. An idea's own
// series is picked in its Details.
const series = { editing: null, deleting: null };
function openSeries() {
  const b = plan.board;
  if (!b) return;
  const counts = new Map();
  for (const i of b.ideas) counts.set(i.arc_id, (counts.get(i.arc_id) || 0) + 1);
  const row = (a, k) => {
    if (series.editing === a.id) {
      return `<li class="series-row"><form class="arc-form">
        <label>Name<input id="arc-title" maxlength="200" value="${esc(a.title)}"></label>
        <label>What it's for<textarea id="arc-purpose" rows="2" maxlength="1000" placeholder="What this series is for">${esc(a.purpose)}</textarea></label>
        <div class="row end"><button type="button" class="btn small" data-arc-cancel>Cancel</button><button type="submit" class="btn small primary" data-arc-save="${esc(a.id)}">Save</button></div></form></li>`;
    }
    const n = counts.get(a.id) || 0;
    if (series.deleting === a.id) {
      return `<li class="series-row"><p><strong>Delete “${esc(a.title)}”?</strong> ${n ? `Its ${esc(plural(n, "idea"))} go to Unsorted; nothing else is lost.` : "It has no ideas."}</p>
        <div class="row end"><button type="button" class="btn small" data-arc-cancel>Cancel</button><button type="button" class="btn small danger" data-arc-del="${esc(a.id)}">Delete</button></div></li>`;
    }
    return `<li class="series-row">
      <div class="series-text"><span class="series-name">${esc(a.title)}</span> <span class="muted small">${esc(plural(n, "idea"))}</span>
        ${a.purpose ? `<span class="muted small series-purpose">${esc(a.purpose)}</span>` : ""}</div>
      <span class="series-btns">
        <button type="button" class="icon-btn" data-arc-action="left" data-arc-id="${esc(a.id)}" ${k === 0 ? "disabled" : ""} aria-label="Move ${esc(a.title)} up" title="Move up">↑</button>
        <button type="button" class="icon-btn" data-arc-action="right" data-arc-id="${esc(a.id)}" ${k === b.arcs.length - 1 ? "disabled" : ""} aria-label="Move ${esc(a.title)} down" title="Move down">↓</button>
        <button type="button" class="btn small" data-arc-action="edit" data-arc-id="${esc(a.id)}">Edit</button>
        <button type="button" class="btn small ghost danger" data-arc-action="delete" data-arc-id="${esc(a.id)}" aria-label="Delete ${esc(a.title)}">Delete</button>
      </span></li>`;
  };
  const unsorted = counts.get(null) || 0;
  openModal(`<h2 id="modal-title">Series</h2>
    <p class="muted">A series is a run of posts on one theme, like Waterproofing. Pick an idea's series in its Details.</p>
    ${b.arcs.length ? `<ul class="series-list">${b.arcs.map(row).join("")}</ul>` : `<p class="muted">No series yet.</p>`}
    ${unsorted ? `<p class="muted small">${esc(plural(unsorted, "idea"))} not in a series.</p>` : ""}
    <h3>New series</h3>
    <form class="arc-form">
      <input id="arc-new-title" maxlength="200" placeholder="Name, e.g. Waterproofing" aria-label="New series name">
      <textarea id="arc-new-purpose" rows="2" maxlength="1000" placeholder="What it's for (optional)" aria-label="What the new series is for"></textarea>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close data-autofocus>Done</button><button type="submit" class="btn primary" data-arc-save="new">Add series</button></div>
    </form>`, async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const ds = t.dataset;
    if (ds.arcSave) await saveArc(ds.arcSave);
    else if (ds.arcCancel != null) { series.editing = series.deleting = null; openSeries(); }
    else if (ds.arcDel) {
      must(await sb.from("arcs").delete().eq("id", Number(ds.arcDel)));
      series.deleting = null;
      toast("Series deleted. Its ideas are in Unsorted.");
      await refresh(true);
      reopenSeries();
    } else if (ds.arcAction) await arcAction(ds.arcAction, Number(ds.arcId));
  }, false, "bottom");
  if (series.editing != null) $("#arc-title")?.focus();
}

// After a change: the dialog again, unless it was closed meanwhile.
function reopenSeries() { if (!$("#modal").hidden && $("#arc-new-title")) openSeries(); }

async function saveArc(which) {
  if (which === "new") {
    const title = $("#arc-new-title").value.trim();
    if (!title) { $("#arc-new-title").focus(); return formError("Give the series a name."); }
    const pos = Math.max(0, ...plan.board.arcs.map((a) => a.position || 0)) + 1;
    must(await sb.from("arcs").insert({ project_id: plan.pid, title, purpose: $("#arc-new-purpose").value.trim(), position: pos }));
    toast("Series added.");
  } else {
    const title = $("#arc-title").value.trim();
    if (!title) { $("#arc-title").focus(); return formError("Give the series a name."); }
    must(await sb.from("arcs").update({ title, purpose: $("#arc-purpose").value.trim() }).eq("id", Number(which)));
    series.editing = null;
  }
  await refresh(true);
  reopenSeries();
}

async function arcAction(action, id) {
  const arcs = plan.board.arcs;
  if (action === "edit") { series.editing = id; series.deleting = null; openSeries(); return; }
  if (action === "delete") { series.deleting = id; series.editing = null; openSeries(); return; }
  // Swap with the neighbour, numbering every series 1..n so equal positions can't stick.
  const order = arcs.map((a) => a.id);
  const k = order.indexOf(id), j = k + (action === "left" ? -1 : 1);
  if (k < 0 || j < 0 || j >= order.length) return;
  [order[k], order[j]] = [order[j], order[k]];
  await Promise.all(order.map((aid, n) => {
    const a = arcs.find((x) => x.id === aid);
    return a.position === n + 1 ? null : sb.from("arcs").update({ position: n + 1 }).eq("id", aid).then(must);
  }));
  await refresh(true);
  reopenSeries();
}

// ── Actions ─────────────────────────────────────────────────────────────────

let refresh = async () => {};
export function onBoardRefresh(fn) { refresh = fn; }

function dumpLines(text) {
  return text.split("\n")
    .map((l) => l.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, "").trim())
    .filter(Boolean).map((l) => l.slice(0, 300));
}

// Several ideas at once, into a project: each line as an idea in Unsorted (free), or the whole
// text to Claude, which sorts it into series with a storyboard each.
async function addIdeas(pid, text, organise) {
  const p = plan.projects.find((x) => x.id === pid);
  if (organise) {
    const res = await invoke("plan-organise", { body: { project_id: pid, text } });
    if (plan.board?.project?.id === pid && res?.job_id) {
      plan.board.organise = { id: res.job_id, kind: "organise", runner: "cloud", state: "queued", created_at: new Date().toISOString() };
      renderPlanTop();
    }
    return "Claude is organising them. About a minute.";
  }
  const lines = dumpLines(text);
  const known = plan.board?.project?.id === pid ? plan.board.ideas : must(await sb.from("ideas").select("position").eq("project_id", pid));
  const pos = Math.max(0, ...known.map((i) => i.position || 0)) + 1;
  must(await sb.from("ideas").insert(lines.map((title, k) => ({ project_id: pid, title, brand: p?.brand || "urban_makers", position: pos + k }))));
  mark("idea_created", { many: lines.length });
  return `Added ${plural(lines.length, "idea")} to Unsorted${p && pid !== plan.pid ? ` in ${p.name}` : ""}.`;
}

async function saveProject(fields, message, write = true) {
  if (write) must(await sb.from("projects").update(fields).eq("id", plan.pid));
  await loadProjects();
  plan.topSig = "";
  renderPlanner(true);
  if (message) toast(message);
}


// New idea: a title, or a whole structure or script. The first line (up to TITLE_MAX) is the title; anything more
// is kept, as written, in a comment Claude reads (comments hold up to 4,000 characters).
const TITLE_MAX = 300;
const FULL_LABEL = "The idea in full:";
const IDEA_MAX = 4000 - FULL_LABEL.length - 1;
function ideaText(value) {
  const text = value.replace(/\r/g, "").trim();
  const first = (text.split("\n").find((l) => l.trim()) || "").replace(/\s+/g, " ").trim();
  let title = first;
  if (title.length > TITLE_MAX) {
    const cut = title.slice(0, TITLE_MAX - 1);
    title = `${cut.slice(0, cut.lastIndexOf(" ") > TITLE_MAX / 2 ? cut.lastIndexOf(" ") : cut.length).trim()}…`;
  }
  return { title, full: text.replace(/\s+/g, " ") === title ? "" : text.slice(0, IDEA_MAX) };
}

function archiveProject() {
  const p = currentProject();
  if (!p) return;
  if (p.status === "archived") { saveProject({ status: "active" }, `${p.name} is active again.`).catch((err) => toast(`Couldn't do that: ${err.message}`)); return; }
  askFirst({ title: `Archive ${p.name}?`, text: "It stays readable; its shots stop being matched to new footage.", ok: "Archive project", danger: true }, async () => {
    must(await sb.from("projects").update({ status: "archived" }).eq("id", p.id));
    await closeModal();
    await saveProject({}, `${p.name} archived.`, false);
  });
}

export function openNewProject() {
  openModal(`<h2 id="modal-title">New project</h2>
    <form class="form">
      <label>Name<input id="np-name" maxlength="120" placeholder="e.g. Kensington" required></label>
      ${oneBrand ? "" : `<label>Account<select id="np-brand">${options(ACCOUNTS, BRAND, "urban_makers")}</select></label>`}
      <label><span>Inbox folder <span class="muted">(optional)</span></span><input id="np-folder" maxlength="80" placeholder="Same as the name"></label>
      <p class="muted small" id="np-hint">${folderHint("")}</p>
      <label><span>Brief <span class="muted">(optional)</span></span><textarea id="np-brief" rows="4" maxlength="4000" placeholder="What's the job, who's the client, what should the posts show?"></textarea></label>
      <p class="error-text" id="modal-error" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="np-save">Create project</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "np-save") return;
    const name = $("#np-name").value.trim();
    if (!name) return formError("Give the project a name.");
    let row;
    try {
      row = must(await sb.from("projects").insert({
        name, brand: $("#np-brand")?.value || ACCOUNTS[0], folder: folderName($("#np-folder").value) || folderName(name), brief: $("#np-brief").value.trim(),
      }).select("id").single());
    } catch (err) { return formError(err.message); }
    await closeModal();
    await loadProjects();
    location.hash = `#/project/${row.id}`;
    toast(`${name} created.`);
  });
  const hint = () => { $("#np-hint").innerHTML = folderHint($("#np-folder").value.trim() || $("#np-name").value.trim()); };
  $("#np-name").addEventListener("input", hint);
  $("#np-folder").addEventListener("input", hint);
}

// New idea's kind by default: the kind the month is shortest of (home.js sets it; "" = let Studio decide).
let kindHint = () => "";
export function setKindHint(fn) { kindHint = fn; }

// New idea, phone-friendly (a full-screen sheet on narrow screens): the essentials first (the project,
// the idea, its kind; Bryan approved trimming it, 2026-10-20), and under More the rest: Reel or carousel,
// an optional link kept as its first comment, its series, and several at once (a list or a messy
// brain-dump, added as written or organised by Claude). From Plan's home it asks which project; on
// Ideas it's the open project. opts.kind: the kind chosen (+ Add on a month's gap).
export function openQuickAdd(opts = {}) {
  const active = plan.projects.filter((p) => p.status === "active");
  const cur = plan.projects.find((p) => p.id === plan.pid);
  const pid = cur && (cur.status === "active" || opts.from !== "home") ? cur.id : active[0]?.id;
  if (!pid) { openNewProject(); return; }
  const choices = cur && cur.status !== "active" && opts.from !== "home" ? [cur, ...active] : active;
  const projectField = choices.length > 1 || opts.from === "home"
    ? `<label>Project<select id="qa-project">${choices.map((p) => `<option value="${esc(p.id)}" ${p.id === pid ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>` : "";
  const preset = schema.kinds ? (opts.kind ?? kindHint() ?? "") : "";
  openModal(`<h2 id="modal-title">New idea</h2>
    <form class="form" id="qa-form">
      ${projectField}
      <div class="form" id="qa-one">
        <label>Idea<textarea id="qa-text" rows="3" maxlength="${IDEA_MAX}" placeholder="e.g. Why we flood-test every bathroom for 24 hours"></textarea></label>
        <p class="muted small">Write as much as you like. The first line is the title. The rest is saved on the idea for Claude.</p>
        ${schema.kinds ? `<label>Kind of post<select id="qa-kind">${options(POST_TYPES, POST_TYPE, preset, "Let Studio decide")}</select></label>
          ${preset && !opts.kind ? `<p class="muted small qa-kind-why">The month is shortest of ${esc(POST_TYPE[preset])} posts.</p>` : ""}` : ""}
        <details class="qa-more" id="qa-more">
          <summary>More: ${schema.slides ? "Reel or carousel, " : ""}link, series, several at once</summary>
          <div class="form">
            ${schema.slides ? `<fieldset class="qa-format"><legend>Make it as</legend>
              <label class="radio"><input type="radio" name="qa-format" value="reel" checked> A Reel <span class="muted small">(filmed)</span></label>
              <label class="radio"><input type="radio" name="qa-format" value="carousel"> A carousel <span class="muted small">(slides; no filming)</span></label></fieldset>` : ""}
            <label><span>Link <span class="muted">(optional, e.g. a reel that inspired it)</span></span><input id="qa-link" type="url" inputmode="url" maxlength="500" placeholder="https://"></label>
            <label>Series<select id="qa-arc"><option value="">Unsorted</option></select></label>
            <button type="button" class="btn-link qa-switch" data-qa-mode="many">Add several ideas at once instead</button>
          </div>
        </details>
      </div>
      <div class="form" id="qa-many" hidden>
        <label>Ideas<textarea id="plan-dump" rows="7" maxlength="20000" placeholder="One per line, or paste a messy brain-dump"></textarea></label>
        <p class="muted small">Add as ideas puts each line in Unsorted as written. Organise with Claude sorts them into series and drafts a storyboard for each (a minute or two; a few cents).</p>
        <button type="button" class="btn-link qa-switch" data-qa-mode="one">Just one idea instead</button>
      </div>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end" id="qa-one-btns"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="qa-add">Add</button></div>
      <div class="row end" id="qa-many-btns" hidden><button type="button" class="btn" data-modal-close>Cancel</button>
        <button type="button" class="btn" id="plan-quick">Add as ideas</button>
        <button type="submit" class="btn primary" id="plan-organise">Organise with Claude</button></div>
    </form>`, async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.qaMode) { setMode(t.dataset.qaMode); return; }
    const target = Number($("#qa-project")?.value || pid);
    if (t.id === "plan-quick" || t.id === "plan-organise") {
      const text = $("#plan-dump").value.trim();
      if (!text) { $("#plan-dump").focus(); return formError("Write some ideas first."); }
      t.disabled = true;
      let message;
      try { message = await addIdeas(target, text, t.id === "plan-organise"); } catch (err) { t.disabled = false; return formError(`Couldn't start: ${err.message}`); }
      await closeModal();
      toast(message, 4000);
      // Claude's progress shows on that project's board.
      if (t.id === "plan-organise" && location.hash !== `#/project/${target}`) location.hash = `#/project/${target}`;
      else await refresh(true);
      return;
    }
    if (t.id !== "qa-add") return;
    const { title, full } = ideaText($("#qa-text").value);
    const raw = $("#qa-link").value.trim();
    if (!title) { $("#qa-text").focus(); return formError("Write the idea first."); }
    const link = raw ? httpsUrl(/^[a-z]+:/i.test(raw) ? raw : `https://${raw}`) : "";
    if (raw && !link) return formError("The link should start with https://");
    t.disabled = true;
    const p = plan.projects.find((x) => x.id === target);
    const known = plan.board?.project?.id === target ? plan.board.ideas : [];
    const pos = Math.max(0, ...known.map((i) => i.position || 0)) + 1;
    let row;
    try {
      const kind = $("#qa-kind")?.value || null;
      const carousel = document.querySelector("input[name=qa-format]:checked")?.value === "carousel";
      row = must(await sb.from("ideas").insert({ project_id: target, title, brand: p?.brand || "urban_makers", arc_id: $("#qa-arc").value ? Number($("#qa-arc").value) : null, position: pos, ...(kind ? { post_type: kind } : {}), ...(carousel ? { format: "carousel" } : {}) }).select("id").single());
      // The whole text, as written, is the idea's first comment: Claude reads comments when it plans or writes slides.
      if (full) {
        const res = await sb.from("comments").insert({ idea_id: row.id, body: `${FULL_LABEL}\n${full}` });
        if (res.error) { await sb.from("ideas").delete().eq("id", row.id); throw res.error; }
      }
      if (link) must(await sb.from("comments").insert({ idea_id: row.id, body: `Link: ${link}` }));
      mark("idea_created", { kind: !!kind, carousel, preset: !!kind && kind === preset, more: !!$("#qa-more")?.open, long: !!full });
      if (kind) mark("idea_kind", { at: "new" });
    } catch (err) { t.disabled = false; return formError(err.message); }
    closeModal();
    // Open: the new idea is one tap away (from Plan it isn't on the screen).
    toast(opts.from === "home" && target !== plan.pid ? `Idea added to ${p?.name || "the project"}.` : "Idea added.", 3200,
      { label: "Open", href: `#/idea/${row.id}`, mark: "toast_open" });
    await refresh(true);
  }, false, "sheet");
  function setMode(mode) {
    $("#qa-one").hidden = $("#qa-one-btns").hidden = mode !== "one";
    $("#qa-many").hidden = $("#qa-many-btns").hidden = mode !== "many";
    formError("");
    (mode === "one" ? $("#qa-text") : $("#plan-dump")).focus();
  }
  // The series list follows the project.
  async function fillArcs() {
    const target = Number($("#qa-project")?.value || pid);
    const arcs = plan.board?.project?.id === target ? plan.board.arcs
      : await sb.from("arcs").select("id,title,position").eq("project_id", target).order("position").order("id").then(must).catch(() => []);
    const sel = $("#qa-arc");
    if (sel) sel.innerHTML = `<option value="">Unsorted</option>${arcs.map((a) => `<option value="${esc(a.id)}">${esc(a.title)}</option>`).join("")}`;
  }
  $("#qa-project")?.addEventListener("change", fillArcs);
  fillArcs();
  // The box grows with a long script (up to most of the screen, then it scrolls).
  $("#qa-text").addEventListener("input", (e) => {
    const t = e.target;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight + 2, Math.round(innerHeight * 0.6))}px`;
  });
  if (opts.mode === "many") setMode("many");
}

function setMode(mode) {
  plan.mode = mode;
  store("studio.planMode", mode);
  renderPlanner(true);
  if (mode === "shoot") $("#shoot-head h2")?.scrollIntoView({ block: "nearest" });
}

$("#planner").addEventListener("click", async (e) => {
  const t = e.target.closest("button, a[data-pick-project], [data-plan-idea]");
  if (!t) return;
  const ds = t.dataset;
  try {
    if (t.id === "plan-project") toggleMenu("project");
    else if (t.id === "plan-tools") toggleMenu("tools");
    else if (ds.pickProject) closeMenus(false);   // the link goes to the project
    else if (ds.tool) {
      closeMenus(ds.tool === "find" || ds.tool === "mode");
      const tool = ds.tool;
      if (tool === "mode") setMode(planMode() === "shoot" ? "ideas" : "shoot");
      else if (tool === "today") location.hash = `#/today/${plan.pid}`;
      else if (tool === "brief") openBrief();
      else if (tool === "series") { series.editing = series.deleting = null; openSeries(); }
      else if (tool === "find") await findIdeas();
      else if (tool === "new") openNewProject();
      else if (tool === "archive") archiveProject();
    } else if (t.id === "plan-new") openNewProject();
    else if (ds.planMode) setMode(ds.planMode);
    else if (t.id === "plan-add-one") openQuickAdd();
    else if (ds.footageShow != null) showFootageColumn();
    else if (ds.footageDismiss != null) {
      footageSeen(plan.footageNews);
      renderPlanTop();
      renderPlanBoard(true, true);
    } else if (ds.dismissOrganise) {
      store(`studio.organiseSeen.${plan.pid}`, ds.dismissOrganise);
      renderPlanTop();
    } else if (ds.openIdea) location.hash = `#/idea/${Number(ds.openIdea)}`;
    else if (ds.planIdea) location.hash = `#/idea/${Number(ds.planIdea)}`;
    else if (ds.more) { plan.more.add(ds.more); renderPlanBoard(true, true); }
    else if (ds.less) { plan.more.delete(ds.less); renderPlanBoard(true, true); }
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
  }
});

// Parked and Made: open or folded, remembered on this device.
$("#planner").addEventListener("toggle", (e) => {
  const d = e.target;
  if (d.dataset?.fold) store(foldKey(d.dataset.fold), d.open ? "1" : "0");
}, true);

// The menus close on a click anywhere else, and on Escape (focus back on their button).
document.addEventListener("click", (e) => {
  if (!e.target.closest?.(".proj-pick")) setMenu("project", false);
  if (!e.target.closest?.(".tools")) setMenu("tools", false);
});
$("#planner").addEventListener("keydown", (e) => {
  const menu = e.target.closest?.(".menu");
  if (e.key === "Escape" && (menu || !$("#proj-menu")?.hidden || !$("#tools-menu")?.hidden)) {
    e.stopPropagation();
    closeMenus(false);
    $(menu?.id === "tools-menu" ? "#plan-tools" : "#plan-project")?.focus();
  } else if (menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    const items = $$("[role^=menuitem]:not(:disabled)", menu);
    const k = items.indexOf(document.activeElement);
    items[(k + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    e.preventDefault();
  }
});
