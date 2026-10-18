// The planning board: projects, the brief, the idea dump (plain or organised by
// Claude), arcs as columns and idea cards. Ported from the laptop's Plan tab.
import {
  $, $$, BRAND, ACCOUNTS, oneBrand, esc, sb, must, invoke, toast, options, store, recall, plural, typingIn, keepDrafts,
  pbarHTML, prepShots, progress, signPaths, stillUrl, takePaths, jobView, jobActive, kickDevelop,
  openModal, closeModal, formError, nameOf, PRODUCTION, state, fbarHTML, isFilmed, folderLineHTML, httpsUrl,
  laptopWait, ago, dayOf, todayISO, parseDay, dayLabel, withKinds, kindsQuery, kindChip, schema, POST_TYPES, POST_TYPE,
} from "./core.js";
import { shoot, loadShoot, renderShoot } from "./shoot.js";
import { loadMonth, renderStrip } from "./month.js";

export const plan = {
  projects: [], pid: null, board: null, sig: "", topSig: "", shellFor: null, order: [],
  hideParked: recall("studio.hideParked") === "1", editingArc: null, addingArc: false, dragging: false,
  // Shoot plan | Ideas, remembered on this device. Not chosen yet: the shoot plan once the
  // project has phases or site days, else Ideas (a new project starts with ideas).
  mode: ["shoot", "ideas"].includes(recall("studio.planMode")) ? recall("studio.planMode") : null,
};
export function planMode() {
  if (plan.mode) return plan.mode;
  const d = shoot.pid === plan.pid ? shoot.data : null;
  return d && (d.phases.length || d.days.length) ? "shoot" : "ideas";
}

const IDEA_COLS = "id,project_id,arc_id,title,brand,format,decision,status,production,position,created_by,created_at,updated_at,"
  + "shots(id,position,filmed_at,takes(id,thumb_path,clip_id,clips(verdict,thumb_path,recorded_at))),votes(user_id,vote),comments(count)";
const JOB_COLS = "id,kind,runner,project_id,idea_id,state,result,error,created_at,started_at,updated_at";

export const currentProject = () => plan.projects.find((p) => p.id === plan.pid) || null;
const folderName = (s) => String(s || "").replace(/[<>:"/\\|?*]/g, "").trim().slice(0, 80);

export async function loadProjects() {
  plan.projects = must(await sb.from("projects").select("*").order("status").order("id", { ascending: false }));
  if (!plan.projects.some((p) => p.id === plan.pid)) {
    const saved = Number(recall("studio.project"));
    const active = plan.projects.filter((p) => p.status === "active");
    plan.pid = (plan.projects.find((p) => p.id === saved) || active[0] || plan.projects[0] || {}).id ?? null;
  }
}

export function selectProject(pid) {
  if (pid === plan.pid) return;
  plan.pid = pid;
  plan.board = null;
  plan.editingArc = null;
  plan.addingArc = false;
  plan.folders = new Map();
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
  // This month's mix (the strip above the plan) comes with a database update too.
  const monthLoad = loadMonth().catch(() => null);
  const [arcs, ideas, folders] = await Promise.all([
    sb.from("arcs").select("*").eq("project_id", pid).order("position").order("id").then(must),
    kindsQuery(() => sb.from("ideas").select(withKinds(IDEA_COLS)).eq("project_id", pid).neq("status", "dismissed")
      .order("position").order("id").then(must)),
    sb.from("ideas").select("id,folder").eq("project_id", pid).eq("decision", "go").neq("folder", "").then(must).catch(() => []),
  ]);
  await Promise.all([shootLoad, monthLoad]);
  const ids = ideas.map((i) => i.id);
  const jobs = must(await sb.from("jobs").select(JOB_COLS).in("kind", ["organise", "develop", "ideas"])
    .or(ids.length ? `project_id.eq.${pid},idea_id.in.(${ids.join(",")})` : `project_id.eq.${pid}`)
    .order("id", { ascending: false }).limit(400));
  if (stale()) return;
  const develop = new Map();
  for (const j of jobs) if (j.kind === "develop" && j.idea_id && !develop.has(j.idea_id)) develop.set(j.idea_id, j);
  const stills = [];
  for (const i of ideas) {
    i.shots = prepShots(i.shots);
    i.progress = progress(i.shots);
    i.first = i.shots.flatMap((s) => s.takes)[0] || null;
    if (i.first) stills.push(...takePaths(i.first));
    const up = [], down = [];
    let mine = 0;
    for (const v of i.votes || []) {
      (v.vote > 0 ? up : down).push(nameOf(v.user_id) || "Someone");
      if (v.user_id === state.me?.id) mine = v.vote;
    }
    i.votes = { up, down, mine };
    i.comments = i.comments?.[0]?.count ?? 0;
    i.filmedCount = i.shots.filter(isFilmed).length;
  }
  plan.folders = new Map(folders.map((f) => [f.id, f.folder]));
  await signPaths(stills).catch(() => {});
  if (stale()) return;
  plan.board = {
    project: plan.projects.find((p) => p.id === pid), arcs, ideas, jobs, develop,
    organise: jobs.find((j) => j.kind === "organise" && j.project_id === pid) || null,
    ideasJob: jobs.find((j) => j.kind === "ideas" && j.project_id === pid) || null,
  };
  kickDevelop(jobs);
}

// ── Rendering ───────────────────────────────────────────────────────────────

export function renderPlanner(force = false) {
  const root = $("#planner");
  if (!plan.projects.length) {
    plan.shellFor = null;
    root.innerHTML = `<div id="month-strip"></div><div class="plan-empty"><h2>Start a plan</h2>
      <p class="muted">A project is one job, like Kensington. Throw post ideas at it, let Claude sort them into series with a shot list for each, then tick shots off on site.</p>
      <button class="btn primary" id="plan-new">New project</button></div>`;
    return;
  }
  // The shell (project bar, brief, dump) is rebuilt only for another project.
  const rebuilt = plan.shellFor !== plan.pid || !$("#plan-board");
  if (rebuilt) {
    keepDrafts(root, () => { root.innerHTML = plannerShellHTML(); });
    plan.shellFor = plan.pid;
    plan.sig = "";
    plan.topSig = "";
  } else syncShell();
  const mode = planMode();
  for (const b of $$("[data-plan-mode]")) {
    b.classList.toggle("on", b.dataset.planMode === mode);
    b.setAttribute("aria-pressed", String(b.dataset.planMode === mode));
  }
  $("#shoot-view").hidden = mode !== "shoot";
  $("#ideas-view").hidden = mode !== "ideas";
  if (!plan.board || plan.board.project?.id !== plan.pid) {
    $("#plan-board").innerHTML = `<p class="muted"><span class="spin"></span> Loading the board…</p>`;
    if (mode === "shoot") $("#shoot-view").innerHTML = `<p class="muted"><span class="spin"></span> Loading the shoot plan…</p>`;
    return;
  }
  renderStrip(force || rebuilt);
  renderPlanTop();
  if (mode === "shoot") renderShoot(force || rebuilt);
  else renderPlanBoard(force || rebuilt, rebuilt);
}

function plannerShellHTML() {
  const p = currentProject() || {};
  const active = plan.projects.filter((x) => x.status === "active");
  const archived = plan.projects.filter((x) => x.status !== "active");
  const opt = (x) => `<option value="${esc(x.id)}" ${x.id === plan.pid ? "selected" : ""}>${esc(x.name)}</option>`;
  return `<div class="plan-bar">
      <h1 class="plan-title">Plan</h1>
      <select id="plan-project" aria-label="Project">${active.map(opt).join("")}
        ${archived.length ? `<optgroup label="Archived">${archived.map(opt).join("")}</optgroup>` : ""}</select>
      <span class="chip" id="plan-archived" ${p.status === "archived" ? "" : "hidden"}>Archived</span>
      <span class="plan-progress" id="plan-progress"></span>
      <div class="plan-bar-actions">
        <button class="btn primary" id="plan-add-one" title="Add one idea to this project: a line, and a link if you have one">New idea</button>
        <button class="btn" id="plan-checklist" title="What to film on today's (or the next) site visit, made for phones">Today on site</button>
        <button class="btn" id="plan-new">New project</button>
      </div>
      <div class="seg view-seg" role="group" aria-label="View" id="plan-modes">
        <button data-plan-mode="shoot" aria-pressed="false">Shoot plan</button>
        <button data-plan-mode="ideas" aria-pressed="false">Ideas</button>
      </div>
    </div>
    <div id="month-strip"></div>
    <details class="plan-details" id="plan-details">
      <summary><strong>Brief</strong> <span class="muted" id="brief-preview">${briefPreview(p)}</span></summary>
      <div class="plan-details-body">
        <div class="row">
          <label class="grow">Name<input id="proj-name" maxlength="120" value="${esc(p.name)}" data-orig="${esc(p.name)}"></label>
          ${oneBrand ? "" : `<label>Account<select id="proj-brand">${options(ACCOUNTS, BRAND, p.brand)}</select></label>`}
        </div>
        <label>Brief<textarea id="proj-brief" rows="4" maxlength="4000" data-orig="${esc(p.brief)}" placeholder="What's the job, who's the client, what should the posts show? Claude reads this when it organises ideas.">${esc(p.brief)}</textarea></label>
        <label>Inbox folder<input id="proj-folder" maxlength="80" value="${esc(p.folder)}" data-orig="${esc(p.folder)}" placeholder="${esc(folderName(p.name))}"></label>
        <p class="muted small" id="proj-folder-hint">${folderHint(p.folder || p.name)}</p>
        <div class="row">
          <button class="btn" id="proj-save">Save</button>
          <button class="btn ghost" id="proj-archive">${p.status === "archived" ? "Unarchive project" : "Archive project"}</button>
        </div>
      </div>
    </details>
    <div id="footage-news"></div>
    <section class="shoot" id="shoot-view" aria-label="Shoot plan" hidden></section>
    <div id="ideas-view">
    <div class="dump" id="plan-dump-box">
      <label class="sr-only" for="plan-dump">Ideas to add</label>
      <textarea id="plan-dump" data-draft rows="3" maxlength="20000" placeholder="Throw ideas in: one per line, or paste a messy brain-dump"></textarea>
      <div class="row">
        <button class="btn" id="plan-quick" title="Each line becomes an idea in Unsorted, as written. Free.">Add as ideas</button>
        <button class="btn primary" id="plan-organise" title="Claude sorts them into series (arcs) and gives each idea a hook and a storyboard. A minute or two; costs a few cents.">Organise with Claude</button>
        <span class="muted small" id="organise-status" aria-live="polite"></span>
      </div>
      <div id="organise-result" aria-live="polite"></div>
    </div>
    <div class="board-head">
      <label class="check"><input type="checkbox" id="plan-hide-parked" ${plan.hideParked ? "checked" : ""}> Hide parked</label>
      <span class="muted small hide-mobile">Drag a card to another column to move it, or open it to change its arc.</span>
      <div class="find-ideas">
        <button class="btn" id="plan-find-ideas" title="Studio reads this project's footage of the last two weeks: new posts go in “From your footage”, and footage that fits a planned post is added to it. Runs on Studio.">${FOOTAGE_ICON}Find ideas in new footage</button>
        <span class="muted small" id="find-ideas-status" aria-live="polite"></span>
      </div>
    </div>
    <div class="board" id="plan-board"></div>
    </div>`;
}

const folderHint = (folder) => `Inbox folder: footage dropped in OneDrive <code>Studio Inbox/${esc(folderName(folder) || "…")}</code> is filed to this project and matched to its shots.`;
function briefPreview(p) {
  const first = (p.brief || "").split("\n")[0].slice(0, 90);
  return first ? esc(first) + ((p.brief || "").length > 90 || (p.brief || "").includes("\n") ? "…" : "")
    : "Add what the job is and what the posts should show";
}

// Someone else changed the project: update fields nobody here is editing.
function syncShell() {
  const p = currentProject();
  if (!p) return;
  for (const [id, key] of [["proj-name", "name"], ["proj-brief", "brief"], ["proj-folder", "folder"]]) {
    const el = $(`#${id}`);
    if (!el || document.activeElement === el || el.value !== el.dataset.orig) continue;
    el.value = p[key] || "";
    el.dataset.orig = p[key] || "";
  }
  const brand = $("#proj-brand");
  if (brand && document.activeElement !== brand) brand.value = p.brand;
  $("#brief-preview").innerHTML = briefPreview(p);
  $("#plan-archived").hidden = p.status !== "archived";
  $("#proj-archive").textContent = p.status === "archived" ? "Unarchive project" : "Archive project";
  const sel = $("#plan-project");
  if (sel && document.activeElement !== sel) {
    const active = plan.projects.filter((x) => x.status === "active");
    const archived = plan.projects.filter((x) => x.status !== "active");
    const opt = (x) => `<option value="${esc(x.id)}" ${x.id === plan.pid ? "selected" : ""}>${esc(x.name)}</option>`;
    sel.innerHTML = active.map(opt).join("") + (archived.length ? `<optgroup label="Archived">${archived.map(opt).join("")}</optgroup>` : "");
  }
}

// Progress and Claude's organise job (and the storyboards it queued).
export function renderPlanTop() {
  const b = plan.board;
  if (!b || !$("#plan-progress")) return;
  const total = { needed: 0, filmed: 0, got: 0, retake: 0, total: 0 };
  for (const i of b.ideas) for (const k of Object.keys(total)) total[k] += i.progress[k] || 0;
  const job = jobView(b.organise);
  const seen = recall(`studio.organiseSeen.${b.project.id}`);
  const r = job?.result || {};
  const newIds = Array.isArray(r.idea_ids) ? r.idea_ids : [];
  const drafts = newIds.map((id) => ({ id, job: jobView(b.develop.get(id)), idea: b.ideas.find((i) => i.id === id) }));
  const news = footageNews(b);
  const ideasJob = jobView(b.ideasJob);
  const sig = JSON.stringify([total, job, seen, drafts.map((d) => [d.job?.state, d.job?.error, d.idea?.title]), news, ideasJob,
    plan.ideasRefused === b.project.id, state.laptop?.last_seen ? Math.floor(Date.parse(state.laptop.last_seen) / 60e3) : 0,
    state.laptop?.info?.footage_ideas?.waiting_clips || 0]);
  if (sig === plan.topSig) return;
  plan.topSig = sig;
  renderFootageNews(news);
  renderFindIdeas(ideasJob);
  $("#plan-progress").innerHTML = total.total
    ? `${pbarHTML(total)}<span><strong>${total.got}</strong> of ${total.total} shots filmed</span>`
    : `<span class="muted">No shots planned yet</span>`;
  const running = jobActive(job);
  $("#plan-organise").disabled = running;
  $("#plan-organise").textContent = running ? "Organising…" : "Organise with Claude";
  $("#organise-status").innerHTML = running
    ? `<span class="spin"></span> Claude is organising your ideas. About a minute; the board updates when it's done.` : "";
  let html = "";
  if (job && !running && String(job.id) !== seen) {
    const close = `<button class="icon-btn" data-dismiss-organise="${esc(job.id)}" aria-label="Dismiss" title="Dismiss">×</button>`;
    if (job.state === "done") {
      const nIdeas = Array.isArray(r.ideas) ? r.ideas.length : Number(r.ideas ?? newIds.length) || 0;
      const nArcs = Array.isArray(r.arcs) ? r.arcs.length : Number(r.arcs) || 0;
      const done = drafts.filter((d) => d.job?.state === "done").length;
      const failed = drafts.filter((d) => d.job?.state === "failed");
      const waiting = drafts.length - done - failed.length;
      html = `<div class="organise-result">${close}
        <strong>Claude added ${plural(nIdeas, "idea")}${nArcs ? ` and ${plural(nArcs, "new arc")}` : ""}.</strong>
        ${r.notes ? `<p>${esc(r.notes)}</p>` : ""}
        ${drafts.length ? `<p class="drafting">${waiting ? `<span class="spin"></span> ` : ""}Storyboards: ${done} of ${drafts.length} drafted${waiting ? `, ${waiting} still being drafted…` : "."}</p>` : ""}
        ${failed.length ? `<ul class="draft-fails">${failed.map((d) => `<li>Couldn't draft “${esc(d.idea?.title || `idea ${d.id}`)}”: ${esc(d.job.error || "unknown error")} <button class="btn small" data-open-idea="${esc(d.id)}">Open</button></li>`).join("")}</ul>` : ""}
      </div>`;
    } else if (job.state === "failed") {
      html = `<div class="organise-result bad">${close}
        <strong>Organising failed.</strong> <span>${esc(job.error || "")}</span> Try again, or add them as ideas.</div>`;
    }
  }
  $("#organise-result").innerHTML = html;
}

// ── Ideas from new footage ──────────────────────────────────────────────────
// After a shoot day the Studio machine reads the new clips and suggests posts into the
// project's "From your footage" arc (footage_ideas.py makes it). The board marks that arc,
// says when new ones arrived since you last looked, and can ask for a look right now.

const FOOTAGE_ARC = "from your footage";
export const fromFootage = (a) => !!a && String(a.title || "").trim().toLowerCase() === FOOTAGE_ARC;
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

// Ideas in the footage arc that arrived since this device last looked (the last week, the first time).
function footageNews(b) {
  const arc = b.arcs.find(fromFootage);
  if (!arc) return null;
  const seen = Date.parse(recall(seenKey(b.project.id)) || "") || Date.now() - 7 * 864e5;
  const fresh = b.ideas.filter((i) => i.arc_id === arc.id && Date.parse(i.created_at || "") > seen);
  if (!fresh.length) return null;
  const days = fresh.flatMap((i) => i.shots.flatMap((s) => s.takes.map((t) => dayOf(t.clips?.recorded_at)))).filter(Boolean).sort();
  return { ids: fresh.map((i) => i.id), latest: fresh.map((i) => i.created_at).sort().pop(), when: footageWhen(days.pop()) };
}

function renderFootageNews(news) {
  const box = $("#footage-news");
  if (!box) return;
  plan.footageNews = news;
  box.innerHTML = news ? `<div class="footage-news" role="status">
      ${FOOTAGE_ICON}<p><strong>${esc(plural(news.ids.length, "new idea"))}</strong> from ${esc(news.when)}.</p>
      <button class="btn small" data-footage-show>See them</button>
      <button class="icon-btn" data-footage-dismiss aria-label="Dismiss" title="Dismiss">×</button>
    </div>` : "";
}

// Seen: the banner goes; the new cards keep a "New" chip until the page is reloaded.
function footageSeen(news) {
  if (!news || !plan.board) return;
  store(seenKey(plan.board.project.id), news.latest || new Date().toISOString());
  plan.footageFresh = new Set([...(plan.footageFresh || []), ...news.ids]);
  plan.topSig = "";
  plan.sig = "";
}

function showFootageColumn() {
  const news = plan.footageNews;
  footageSeen(news);
  if (planMode() !== "ideas") { plan.mode = "ideas"; store("studio.planMode", "ideas"); }
  renderPlanner(true);
  const col = $(".col.from-footage");
  if (!col) return;
  col.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "start" });
  col.classList.remove("flash");
  void col.offsetWidth;
  col.classList.add("flash");
  $("#footage-col")?.focus({ preventScroll: true });
}

function renderFindIdeas(job) {
  const btn = $("#plan-find-ideas"), status = $("#find-ideas-status");
  if (!btn || !status) return;
  const busy = jobActive(job);
  // Studio does this by itself once a shoot day's uploads go quiet (laptop_status.info).
  const waiting = Number(state.laptop?.info?.footage_ideas?.waiting_clips) || 0;
  const soon = !busy && waiting ? ` <span class="find-soon">Studio will suggest ideas from ${esc(plural(waiting, "new clip"))} once uploads stop.</span>` : "";
  btn.disabled = busy;
  btn.lastChild.textContent = busy ? "Finding ideas…" : "Find ideas in new footage";
  if (plan.ideasRefused === plan.pid && !job) {
    status.innerHTML = `<span class="error-text">Needs a one-time update: run cloud_setup.py --update</span>`;
    return;
  }
  if (!job) { status.innerHTML = soon.trim(); return; }
  if (job.state === "queued") { status.innerHTML = `<span class="spin"></span> Asked Studio. ${esc(laptopWait())}`; return; }
  if (job.state === "running") { status.innerHTML = `<span class="spin"></span> Studio is reading the new footage. A minute or two.`; return; }
  if (job.state === "failed") { status.innerHTML = `<span class="error-text">Couldn't look: ${esc(job.error || "unknown error")}</span>${soon}`; return; }
  const r = job.result || {};
  const count = (v) => (Array.isArray(v) ? v.length : Number(v) || 0);
  const n = count(r.new_ideas ?? r.ideas), clips = count(r.attached);
  const when = job.updated_at ? ` (${esc(ago(job.updated_at))})` : "";
  status.innerHTML = r.clips === 0 ? `Last look${when}: no new footage from the last two weeks.`
    : n || clips ? `Last look${when}: ${esc(plural(n, "new idea"))}${clips ? `, and ${esc(plural(clips, "clip"))} added to planned posts` : ""}.`
      : `Last look${when}: no new ideas in the footage this time.`;
  status.innerHTML += soon;
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

function boardBusy() {
  const board = $("#plan-board");
  return plan.dragging || plan.editingArc != null || plan.addingArc || typingIn(board)
    || !!board?.querySelector("details.col-menu[open]");
}

// force: re-render even if nothing changed. mine: the person here changed the
// board's own state (arc form, filter), so render even while it's "busy".
export function renderPlanBoard(force = false, mine = false) {
  const b = plan.board;
  const el = $("#plan-board");
  if (!b || !el) return;
  const drafting = b.ideas.map((i) => jobView(b.develop.get(i.id))?.state || "");
  const sig = JSON.stringify([b.arcs, b.ideas, drafting, plan.hideParked, plan.editingArc, plan.addingArc, [...(plan.folders || [])], currentProject()?.folder]);
  if (!force && sig === plan.sig) return;
  if (!mine && boardBusy()) { plan.pendingBoard = true; return; }
  plan.pendingBoard = false;
  plan.sig = sig;
  const arcIds = new Set(b.arcs.map((a) => a.id));
  const arcOf = (i) => (arcIds.has(i.arc_id) ? i.arc_id : null);
  const shown = b.ideas.filter((i) => !plan.hideParked || i.decision !== "parked");
  const cols = [];
  if (b.ideas.some((i) => arcOf(i) === null) || !b.arcs.length) cols.push({ arc: null, ideas: shown.filter((i) => arcOf(i) === null) });
  b.arcs.forEach((a, k) => cols.push({ arc: a, index: k, ideas: shown.filter((i) => i.arc_id === a.id) }));
  plan.order = cols.flatMap((c) => c.ideas.map((i) => i.id));
  const hidden = b.ideas.length - shown.length;
  el.innerHTML = cols.map((c) => columnHTML(c, b.arcs.length)).join("") + newArcHTML()
    + (hidden ? `<p class="muted small board-note">${plural(hidden, "parked idea")} hidden.</p>` : "");
  if (plan.editingArc != null) $("#arc-title")?.focus();
  if (plan.addingArc) $("#arc-new-title")?.focus();
}

function columnHTML(c, arcCount) {
  const a = c.arc;
  const key = a ? String(a.id) : "";
  const footage = fromFootage(a);
  let head;
  if (a && plan.editingArc === a.id) {
    head = `<div class="arc-form">
      <input id="arc-title" maxlength="200" value="${esc(a.title)}" aria-label="Arc name">
      <textarea id="arc-purpose" rows="2" maxlength="1000" placeholder="What this series is for" aria-label="What this series is for">${esc(a.purpose)}</textarea>
      <div class="row"><button class="btn small primary" data-arc-save="${esc(a.id)}">Save</button><button class="btn small" data-arc-cancel>Cancel</button></div></div>`;
  } else {
    head = `<div class="col-title"><h3${footage ? ` tabindex="-1" id="footage-col"` : ""}>${a ? esc(a.title) : "Unsorted"}</h3><span class="count">${c.ideas.length}</span>
      ${a ? `<details class="col-menu"><summary aria-label="Options for ${esc(a.title)}" title="Arc options">⋯</summary><div class="menu">
        <button data-arc-action="edit" data-arc-id="${esc(a.id)}">Rename or edit purpose</button>
        <button data-arc-action="left" data-arc-id="${esc(a.id)}" ${c.index === 0 ? "disabled" : ""}>Move left</button>
        <button data-arc-action="right" data-arc-id="${esc(a.id)}" ${c.index === arcCount - 1 ? "disabled" : ""}>Move right</button>
        <button data-arc-action="delete" data-arc-id="${esc(a.id)}" class="danger">Delete arc…</button></div></details>` : ""}</div>
      ${footage ? `<p class="col-source">${FOOTAGE_ICON}Found in your footage</p>` : ""}
      ${a ? (a.purpose ? `<p class="col-purpose">${esc(a.purpose)}</p>` : "") : `<p class="col-purpose">Not in a series yet. Drag into an arc, or let Claude organise them.</p>`}`;
  }
  return `<section class="col${footage ? " from-footage" : ""}" data-arc="${esc(key)}" aria-label="${a ? esc(a.title) : "Unsorted"}">
    <header class="col-head">${head}</header>
    <div class="col-cards">${c.ideas.map((i) => cardHTML(i, footage)).join("") || `<p class="muted small col-empty">${footage ? "Studio adds posts here when it finds them in new footage" : "Drop ideas here"}</p>`}</div>
  </section>`;
}

function cardHTML(i, footage = false) {
  const p = i.progress;
  const thumb = i.first ? stillUrl(...takePaths(i.first)) : "";
  const ups = i.votes.up.length, downs = i.votes.down.length;
  const dev = jobView(plan.board.develop.get(i.id));
  const prod = i.production?.status;
  const filmed = i.filmedCount || 0;
  const meta = [
    ups ? `<span title="Keen: ${esc(i.votes.up.join(", "))}">👍 ${ups}</span>` : "",
    downs ? `<span title="Not keen: ${esc(i.votes.down.join(", "))}">👎 ${downs}</span>` : "",
    i.comments ? `<span title="${esc(plural(i.comments, "comment"))}">💬 ${esc(i.comments)}</span>` : "",
  ].filter(Boolean).join("");
  const decision = { go: "Go", parked: "Parked", proposed: "Idea" }[i.decision] || "Idea";
  const fresh = footage && plan.footageFresh?.has(i.id);
  const label = [i.title, decision, POST_TYPE[i.post_type] || "", footage ? "found in your footage" : "", fresh ? "new" : "", p.total ? `${filmed} of ${p.total} shots filmed` : "No shots yet", ups ? `${ups} keen` : ""].filter(Boolean).join(", ");
  const folder = plan.folders?.get(i.id);
  const card = `<button class="card ${esc(i.decision)}" data-plan-idea="${esc(i.id)}" draggable="true" aria-label="${esc(label)}">
    ${thumb ? `<img class="card-thumb" src="${esc(thumb)}" alt="" loading="lazy">` : ""}
    <span class="card-body">
      <span class="card-title">${esc(i.title)}</span>
      <span class="card-chips">
        <span class="chip ${esc(i.decision)}">${decision}</span>
        ${kindChip(i.post_type)}
        ${footage ? `<span class="chip footage">${fresh ? "New · " : ""}From footage</span>` : ""}
        ${oneBrand ? "" : `<span class="chip ${esc(i.brand)}">${esc(BRAND[i.brand] || i.brand)}</span>`}
        ${i.status === "made" || prod ? `<span class="chip producing">${esc(prod ? PRODUCTION[prod] || prod : "In edit")}</span>` : ""}
        ${jobActive(dev) ? `<span class="chip drafting"><span class="spin"></span>Drafting storyboard…</span>` : ""}
        ${dev?.state === "failed" && !p.total ? `<span class="chip failed">Storyboard failed</span>` : ""}
      </span>
      ${p.total ? `<span class="card-progress"><span>${esc(filmed)} of ${esc(p.total)} shots</span>${fbarHTML(filmed, p.total)}</span>` : `<span class="card-progress muted">No shots yet</span>`}
      ${meta ? `<span class="card-meta">${meta}</span>` : ""}
    </span>
  </button>`;
  const line = folder ? folderLineHTML(currentProject(), { ...i, folder }) : "";
  return line ? `<div class="card-wrap">${card}${line}</div>` : card;
}

function newArcHTML() {
  if (plan.addingArc) {
    return `<section class="col col-new"><div class="arc-form">
      <input id="arc-new-title" maxlength="200" placeholder="Arc name, e.g. Waterproofing" aria-label="Arc name">
      <textarea id="arc-new-purpose" rows="2" maxlength="1000" placeholder="What this series is for (optional)" aria-label="What this series is for"></textarea>
      <div class="row"><button class="btn small primary" data-arc-save="new">Add arc</button><button class="btn small" data-arc-cancel>Cancel</button></div>
    </div></section>`;
  }
  return `<section class="col col-new"><button class="new-arc" data-new-arc>+ New arc</button></section>`;
}

// ── Actions ─────────────────────────────────────────────────────────────────

let refresh = async () => {};
export function onBoardRefresh(fn) { refresh = fn; }

async function saveArc(which) {
  if (which === "new") {
    const title = $("#arc-new-title").value.trim();
    if (!title) { $("#arc-new-title").focus(); return; }
    const pos = Math.max(0, ...plan.board.arcs.map((a) => a.position || 0)) + 1;
    must(await sb.from("arcs").insert({ project_id: plan.pid, title, purpose: $("#arc-new-purpose").value.trim(), position: pos }));
    plan.addingArc = false;
    renderPlanBoard(true, true);
    toast("Arc added.");
  } else {
    const title = $("#arc-title").value.trim();
    if (!title) { $("#arc-title").focus(); return; }
    must(await sb.from("arcs").update({ title, purpose: $("#arc-purpose").value.trim() }).eq("id", Number(which)));
    const a = plan.board.arcs.find((x) => x.id === Number(which));
    if (a) Object.assign(a, { title, purpose: $("#arc-purpose").value.trim() });
    plan.editingArc = null;
    renderPlanBoard(true, true);
  }
  await refresh(true);
}

async function arcAction(action, id) {
  const arcs = plan.board.arcs;
  if (action === "edit") {
    plan.editingArc = id;
    renderPlanBoard(true, true);
    return;
  }
  if (action === "delete") {
    const arc = arcs.find((a) => a.id === id);
    if (!confirm(`Delete the arc "${arc?.title}"? Its ideas move to Unsorted; nothing else is lost.`)) return;
    must(await sb.from("arcs").delete().eq("id", id));
    toast("Arc deleted. Its ideas are in Unsorted.");
  } else {
    // Swap with the neighbour, numbering every arc 1..n so equal positions can't stick.
    const order = arcs.map((a) => a.id);
    const k = order.indexOf(id), j = k + (action === "left" ? -1 : 1);
    if (k < 0 || j < 0 || j >= order.length) return;
    [order[k], order[j]] = [order[j], order[k]];
    await Promise.all(order.map((aid, n) => {
      const a = arcs.find((x) => x.id === aid);
      return a.position === n + 1 ? null : sb.from("arcs").update({ position: n + 1 }).eq("id", aid).then(must);
    }));
  }
  await refresh(true);
}

function dumpLines(text) {
  return text.split("\n")
    .map((l) => l.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, "").trim())
    .filter(Boolean).map((l) => l.slice(0, 300));
}

async function addIdeas(organise) {
  const box = $("#plan-dump");
  const text = box.value.trim();
  if (!text) { box.focus(); toast("Write some ideas first."); return; }
  const p = currentProject();
  if (organise) {
    let res;
    try {
      res = await invoke("plan-organise", { body: { project_id: plan.pid, text } });
    } catch (err) { toast(`Couldn't start: ${err.message}`, 6000); return; }
    box.value = "";
    if (plan.board && res?.job_id) {
      plan.board.organise = { id: res.job_id, kind: "organise", runner: "cloud", state: "queued", created_at: new Date().toISOString() };
      renderPlanTop();
    }
    toast("Claude is organising them. About a minute.");
    await refresh();
  } else {
    const lines = dumpLines(text);
    const pos = Math.max(0, ...(plan.board?.ideas || []).map((i) => i.position || 0)) + 1;
    must(await sb.from("ideas").insert(lines.map((title, k) => ({ project_id: plan.pid, title, brand: p?.brand || "urban_makers", position: pos + k }))));
    box.value = "";
    toast(`Added ${plural(lines.length, "idea")} to Unsorted.`);
    await refresh(true);
  }
}

async function saveProject(fields, message) {
  must(await sb.from("projects").update(fields).eq("id", plan.pid));
  await loadProjects();
  renderPlanner(true);
  if (message) toast(message);
}

export function openNewProject() {
  openModal(`<h2 id="modal-title">New project</h2>
    <form class="form">
      <label>Name<input id="np-name" maxlength="120" placeholder="e.g. Kensington" required></label>
      ${oneBrand ? "" : `<label>Account<select id="np-brand">${options(ACCOUNTS, BRAND, "urban_makers")}</select></label>`}
      <label>Inbox folder <span class="muted">(optional)</span><input id="np-folder" maxlength="80" placeholder="Same as the name"></label>
      <p class="muted small" id="np-hint">${folderHint("")}</p>
      <label>Brief <span class="muted">(optional)</span><textarea id="np-brief" rows="4" maxlength="4000" placeholder="What's the job, who's the client, what should the posts show?"></textarea></label>
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

// One idea, phone-friendly: a full-screen sheet on narrow screens. Text, an optional
// link (kept as the idea's first comment), and the series it belongs to.
export function openQuickAdd() {
  const arcs = plan.board?.arcs || [];
  openModal(`<h2 id="modal-title">Add an idea</h2>
    <form class="form">
      <label>Idea<textarea id="qa-text" rows="4" maxlength="300" placeholder="e.g. Why we flood-test every bathroom for 24 hours" required></textarea></label>
      <label>Link <span class="muted">(optional, e.g. a reel that inspired it)</span><input id="qa-link" type="url" inputmode="url" maxlength="500" placeholder="https://"></label>
      <label>Series<select id="qa-arc"><option value="">Unsorted</option>${arcs.map((a) => `<option value="${esc(a.id)}">${esc(a.title)}</option>`).join("")}</select></label>
      ${schema.kinds ? `<label>Kind of post<select id="qa-kind">${options(POST_TYPES, POST_TYPE, "", "Let Studio decide")}</select></label>` : ""}
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="qa-add">Add</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "qa-add") return;
    const title = $("#qa-text").value.replace(/\s+/g, " ").trim().slice(0, 300);
    const raw = $("#qa-link").value.trim();
    if (!title) return formError("Write the idea first.");
    const link = raw ? httpsUrl(/^[a-z]+:/i.test(raw) ? raw : `https://${raw}`) : "";
    if (raw && !link) return formError("The link should start with https://");
    e.target.disabled = true;
    const p = currentProject();
    const pos = Math.max(0, ...(plan.board?.ideas || []).map((i) => i.position || 0)) + 1;
    let row;
    try {
      const kind = $("#qa-kind")?.value || null;
      row = must(await sb.from("ideas").insert({ project_id: plan.pid, title, brand: p?.brand || "urban_makers", arc_id: $("#qa-arc").value ? Number($("#qa-arc").value) : null, position: pos, ...(kind ? { post_type: kind } : {}) }).select("id").single());
      if (link) must(await sb.from("comments").insert({ idea_id: row.id, body: `Link: ${link}` }));
    } catch (err) { e.target.disabled = false; return formError(err.message); }
    closeModal();
    toast("Idea added.");
    await refresh(true);
  }, false, "sheet");
}

$("#planner").addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-plan-idea]");
  if (!t) return;
  const ds = t.dataset;
  try {
    if (t.id === "plan-new") openNewProject();
    else if (t.id === "plan-checklist") location.hash = `#/today/${plan.pid}`;
    else if (ds.planMode) {
      plan.mode = ds.planMode;
      store("studio.planMode", plan.mode);
      renderPlanner(true);
    } else if (t.id === "plan-add-one") openQuickAdd();
    else if (t.id === "plan-quick") await addIdeas(false);
    else if (t.id === "plan-organise") await addIdeas(true);
    else if (t.id === "proj-save") {
      const name = $("#proj-name").value.trim();
      await saveProject({ ...(name ? { name } : {}), brand: $("#proj-brand")?.value || ACCOUNTS[0], brief: $("#proj-brief").value.trim(), folder: folderName($("#proj-folder").value) }, "Project saved.");
    } else if (t.id === "proj-archive") {
      const p = currentProject();
      if (p.status === "archived") await saveProject({ status: "active" }, `${p.name} is active again.`);
      else if (confirm(`Archive ${p.name}? It stays readable; its shots stop being matched to new footage.`)) {
        await saveProject({ status: "archived" }, `${p.name} archived.`);
      }
    } else if (t.id === "plan-find-ideas") await findIdeas();
    else if (ds.footageShow != null) showFootageColumn();
    else if (ds.footageDismiss != null) {
      footageSeen(plan.footageNews);
      renderPlanTop();
      $("#plan-board") && renderPlanBoard(true, true);
    } else if (ds.dismissOrganise) {
      store(`studio.organiseSeen.${plan.pid}`, ds.dismissOrganise);
      renderPlanTop();
    } else if (ds.openIdea) location.hash = `#/idea/${Number(ds.openIdea)}`;
    else if (ds.planIdea) location.hash = `#/idea/${Number(ds.planIdea)}`;
    else if (ds.arcAction) {
      t.closest("details")?.removeAttribute("open");
      await arcAction(ds.arcAction, Number(ds.arcId));
    } else if (ds.arcSave) await saveArc(ds.arcSave);
    else if (ds.arcCancel != null) {
      plan.editingArc = null;
      plan.addingArc = false;
      renderPlanBoard(true, true);
    } else if (ds.newArc != null) {
      plan.addingArc = true;
      renderPlanBoard(true, true);
    }
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
  }
});

// The brief saves when you click away, too.
$("#planner").addEventListener("focusout", async (e) => {
  const el = e.target;
  const key = { "proj-brief": "brief", "proj-name": "name", "proj-folder": "folder" }[el.id];
  if (!key || el.value === el.dataset.orig) return;
  let value = key === "folder" ? folderName(el.value) : el.value.trim();
  if (key === "name" && !value) { el.value = el.dataset.orig; toast("The project needs a name."); return; }
  try {
    must(await sb.from("projects").update({ [key]: value }).eq("id", plan.pid));
    el.dataset.orig = el.value;
    const p = currentProject();
    if (p) p[key] = value;
    if (key === "folder") $("#proj-folder-hint").innerHTML = folderHint(value || p?.name);
    if (key === "brief") $("#brief-preview").innerHTML = briefPreview(p || {});
    toast(key === "brief" ? "Brief saved." : "Saved.");
  } catch (err) { toast(`Couldn't save: ${err.message}`); }
});
$("#planner").addEventListener("input", (e) => {
  if (e.target.id === "proj-folder" || e.target.id === "proj-name") {
    $("#proj-folder-hint").innerHTML = folderHint($("#proj-folder").value.trim() || $("#proj-name").value.trim());
  }
});

$("#planner").addEventListener("change", async (e) => {
  if (e.target.id === "plan-project") {
    location.hash = `#/project/${Number(e.target.value)}`;
  } else if (e.target.id === "plan-hide-parked") {
    plan.hideParked = e.target.checked;
    store("studio.hideParked", plan.hideParked ? "1" : "0");
    renderPlanBoard(true, true);
  } else if (e.target.id === "proj-brand") {
    try {
      must(await sb.from("projects").update({ brand: e.target.value }).eq("id", plan.pid));
      const p = currentProject();
      if (p) p.brand = e.target.value;
      toast("Account saved.");
    } catch (err) { toast(`Couldn't save: ${err.message}`); }
  }
});
$("#planner").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.target.id === "arc-title" || e.target.id === "arc-new-title")) {
    e.preventDefault();
    saveArc(e.target.id === "arc-title" ? plan.editingArc : "new").catch((err) => toast(`Couldn't save: ${err.message}`));
  } else if (e.key === "Escape" && e.target.closest(".arc-form")) {
    e.stopPropagation();
    plan.editingArc = null;
    plan.addingArc = false;
    renderPlanBoard(true, true);
  } else if (e.key === "Escape" && e.target.closest("details.col-menu[open]")) {
    const d = e.target.closest("details.col-menu");
    d.open = false;
    d.querySelector("summary").focus();
  } else if (e.target.id === "plan-dump" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    addIdeas(false).catch((err) => toast(`Couldn't add: ${err.message}`));
  }
});

// Close arc menus when clicking anywhere else.
document.addEventListener("click", (e) => {
  $$("details.col-menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  catchUp();
});
// Changes that arrived while the board was busy show once it isn't.
function catchUp() {
  setTimeout(() => { if (plan.pendingBoard && !boardBusy()) renderPlanBoard(true); }, 0);
}
$("#planner").addEventListener("toggle", catchUp, true);
$("#planner").addEventListener("focusout", catchUp);

// Drag cards between columns (desktop; on phones, change the arc in the idea).
$("#planner").addEventListener("dragstart", (e) => {
  const card = e.target.closest?.(".card[data-plan-idea]");
  if (!card) return;
  e.dataTransfer.setData("text/plain", card.dataset.planIdea);
  e.dataTransfer.effectAllowed = "move";
  plan.dragging = true;
  card.classList.add("dragging");
});
$("#planner").addEventListener("dragend", (e) => {
  plan.dragging = false;
  e.target.closest?.(".card")?.classList.remove("dragging");
  $$(".col.drop").forEach((c) => c.classList.remove("drop"));
  catchUp();
});
$("#planner").addEventListener("dragover", (e) => {
  const col = e.target.closest(".col:not(.col-new)");
  if (!col || !plan.dragging) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  $$(".col.drop").forEach((c) => { if (c !== col) c.classList.remove("drop"); });
  col.classList.add("drop");
});
$("#planner").addEventListener("drop", async (e) => {
  const col = e.target.closest(".col:not(.col-new)");
  if (!col) return;
  e.preventDefault();
  plan.dragging = false;
  col.classList.remove("drop");
  const id = Number(e.dataTransfer.getData("text/plain"));
  const arcId = col.dataset.arc ? Number(col.dataset.arc) : null;
  const idea = plan.board?.ideas.find((i) => i.id === id);
  if (!idea || idea.arc_id === arcId) return;
  try {
    must(await sb.from("ideas").update({ arc_id: arcId }).eq("id", id));
    idea.arc_id = arcId;
    renderPlanBoard(true, true);
    await refresh(true);
  } catch (err) {
    toast(`Couldn't move it: ${err.message}`);
  }
});
