// Studio planner: sign-in, the header (laptop status, account, the shared top bar from
// ../shell.js), hash routes and live updates. Views live in board.js, idea.js,
// checklist.js and team.js.
import {
  $, esc, sb, must, landing, state, isOwner, toast, openModal, closeModal, modalOpen, formError,
  isTyping, laptopOn, laptopSince, underPlan, recall, store,
} from "./core.js";
import { showAuth, hideAuth } from "./auth.js";
import { plan, loadProjects, loadBoard, renderPlanner, selectProject, onBoardRefresh } from "./board.js";
import { view, openIdea, hideViewer, closeIdea, loadIdea, viewerOpen, step, updateArrows, restoreBoardScroll, forgetBoardScroll } from "./idea.js";
import { check, openChecklist, hideChecklist, loadChecklist, checklistOpen } from "./checklist.js";
import { showTeam, hideTeam } from "./team.js";
import { refsRoute, showRefs, hideRefs, resetRefs, refsOpen, loadRefs } from "./refs.js";
import { shoot, onShootRefresh } from "./shoot.js";
import { month, monthRoute, showMonth, hideMonth, monthOpen, loadMonthPage } from "./month.js";
import { onUploadRow, renderTray } from "./upload.js";

const SET_PASSWORD = "studio.setPassword";
const session = { started: false, channel: null };

// ── Sign-in ─────────────────────────────────────────────────────────────────

async function boot() {
  if (!sb) { showAuth("noconfig"); return; }
  if (landing.hasToken && (landing.type === "invite" || landing.type === "recovery")) {
    try { sessionStorage.setItem(SET_PASSWORD, landing.type); } catch { /* private mode */ }
  }
  showAuth("loading");
  let { data } = await sb.auth.getSession();
  // Served by the Studio dashboard at /plan/: the dashboard login is the planner's too.
  if (!data.session && underPlan && !landing.hasToken) {
    const got = await dashboardSession();
    if (got === "redirected") return;
    if (got?.blocked) { showAuth("blocked", { error: got.blocked }); return; }
    if (got) ({ data } = await sb.auth.getSession());
  }
  if (landing.hasToken || landing.error) history.replaceState(null, "", location.pathname);
  sb.auth.onAuthStateChange((event) => {
    if (event === "PASSWORD_RECOVERY") {
      try { sessionStorage.setItem(SET_PASSWORD, "recovery"); } catch { /* private mode */ }
    } else if (event === "SIGNED_OUT" && session.started) {
      stop();
      if (underPlan && !signingOut) { location.href = "/login?next=/plan/"; return; }
      showAuth("signin", { info: "You're signed out.", onSignedIn: enter });
    }
  });
  if (landing.error) {
    const expired = /expired|invalid/i.test(landing.error) || landing.errorCode === "otp_expired";
    showAuth("signin", {
      onSignedIn: enter,
      error: expired ? "That link has expired or was already used. Ask for a new invite, or use “Forgot your password?”." : landing.error,
    });
    return;
  }
  if (!data.session) { showAuth("signin", { onSignedIn: enter }); return; }
  await enter(data.session);
}

// GET /api/planner-session on the dashboard: the signed-in dashboard user's Supabase
// session. 401: not signed in to the dashboard (go there); 403: not a planner member.
async function dashboardSession() {
  let res;
  try {
    res = await fetch("/api/planner-session", { credentials: "same-origin", headers: { Accept: "application/json" } });
  } catch { return null; }
  if (res.status === 401) { location.href = "/login?next=/plan/"; return "redirected"; }
  const j = await res.json().catch(() => ({}));
  if (res.status === 403) return { blocked: j.error || "Your Studio account isn't on the planning team. Ask the owner to add you." };
  if (!res.ok || !j.access_token || !j.refresh_token) return null;
  const { error } = await sb.auth.setSession({ access_token: j.access_token, refresh_token: j.refresh_token });
  return error ? null : true;
}
let signingOut = false;
// Under /plan/ signing out of the planner signs out of the dashboard too.
async function signOutEverywhere() {
  signingOut = true;
  await sb.auth.signOut().catch(() => {});
  stop();
  if (underPlan) {
    await fetch("/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    location.href = "/login?next=/plan/";
    return;
  }
  signingOut = false;
  showAuth("signin", { info: "Signed out.", onSignedIn: enter });
}

async function enter(sess, opts = {}) {
  if (!sess) { showAuth("signin", { onSignedIn: enter }); return; }
  const user = sess.user;
  let me;
  try {
    me = must(await sb.from("profiles").select("id,name,email,role").eq("id", user.id).maybeSingle());
  } catch (err) {
    showAuth("signin", { onSignedIn: enter, error: `Couldn't load your account: ${err.message}` });
    return;
  }
  if (!me) { showAuth("gone", { email: user.email }); return; }
  state.me = me;
  let pending = null;
  try { pending = sessionStorage.getItem(SET_PASSWORD); } catch { /* private mode */ }
  if (pending && !opts.passwordSet) {
    showAuth("set", { invite: pending === "invite", email: user.email, name: me.name, onSignedIn: enter });
    return;
  }
  hideAuth();
  $("#app").hidden = false;
  if (opts.passwordSet) toast("Password set. Welcome in.");
  await start();
}

async function start() {
  const [profiles, laptop] = await Promise.all([
    sb.from("profiles").select("id,name,email,role").then(must).catch(() => []),
    sb.from("laptop_status").select("*").eq("id", 1).maybeSingle().then(must).catch(() => null),
  ]);
  state.profiles = new Map(profiles.map((p) => [p.id, p]));
  state.laptop = laptop;
  renderAccount();
  renderLaptop();
  if (!session.started) {
    session.started = true;
    startRealtime();
    loadMachine();
    pollNav();
  }
  await route();
}

function stop() {
  if (session.channel) sb.removeChannel(session.channel);
  if (session.refChannel) sb.removeChannel(session.refChannel);
  if (session.shootChannel) sb.removeChannel(session.shootChannel);
  if (session.planChannel) sb.removeChannel(session.planChannel);
  if (session.topicChannel) sb.removeChannel(session.topicChannel);
  if (session.libChannel) sb.removeChannel(session.libChannel);
  session.channel = session.refChannel = session.shootChannel = session.planChannel = session.topicChannel = session.libChannel = null;
  session.started = false;
  hideViewer();
  hideChecklist();
  closeModal();
  resetRefs();
  hideMonth();
  Object.assign(month, { data: null, plan: null, loaded: false });
  $("#app").hidden = true;
  state.me = null;
  plan.projects = [];
  plan.board = null;
  plan.pid = null;
  plan.shellFor = null;
  Object.assign(shoot, { pid: null, data: null, missing: false, sig: "" });
  renderTray();
}

// ── Header: the laptop and your account ─────────────────────────────────────

function renderLaptop() {
  const on = laptopOn();
  const since = laptopSince();
  const pill = $("#laptop-btn");
  pill.classList.toggle("on", on);
  $("#laptop-text").textContent = on ? "Studio on" : since ? `Studio offline since ${since}` : "Studio offline";
  pill.title = on ? "Studio is on and syncing." : "Footage matching, music shortlists and Make this wait for it.";
  $("#laptop-info").innerHTML = on
    ? `<p><strong>Studio is on.</strong></p><p class="muted small">It syncs about every 30 seconds: new footage is matched to shots, and music shortlists, Make this, reference analysis and style guide drafts run there.</p>`
    : `<p><strong>Studio is offline${since ? ` (last seen ${esc(since)})` : ""}.</strong></p><p class="muted small">Planning works without it. Footage matching, music shortlists, Make this, reference analysis and style guide drafts wait for it and run as soon as it's back.</p>`;
}
setInterval(() => { if (state.me) renderLaptop(); }, 30e3);
// In case a live update was missed (phone asleep), re-read the heartbeat now and then.
setInterval(async () => {
  if (!state.me || document.hidden) return;
  const { data } = await sb.from("laptop_status").select("*").eq("id", 1).maybeSingle();
  if (data) { state.laptop = data; renderLaptop(); }
}, 60e3);

// The account menu: the same items as Review's. Served at /plan/, the owner also gets
// the Studio machine's version, Update now and Restart Studio (the dashboard's /api/update).
const machine = { me: null };
function renderAccount() {
  const me = state.me;
  $("#account-btn").innerHTML = `<span class="avatar" aria-hidden="true">${esc((me.name || "?").slice(0, 1).toUpperCase())}</span><span class="account-name">${esc(me.name)}</span>`;
  $("#account-btn").setAttribute("aria-label", `Account: ${me.name}`);
  $("#account-menu").innerHTML = `<div class="menu-head"><strong>${esc(me.name)}</strong>
      <span class="muted small">${esc(me.email)} · ${isOwner() ? "Owner" : "Team"}</span></div>
    ${isOwner() ? `<button role="menuitem" data-account="team">Team…</button>` : ""}
    <button role="menuitem" data-account="password">Change password</button>
    <button role="menuitem" data-account="signout">Sign out</button>
    ${isOwner() && machine.me?.role === "owner" ? versionHTML(machine.me.version) : ""}`;
}
function versionHTML(v) {
  if (!v) return "";
  const when = v.date ? new Date(v.date).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
  const status = v.error ? `<span class="small bad-text">Last update failed: ${esc(v.error)}</span>`
    : v.requested ? `<span class="muted small">Update requested: Studio restarts by itself when it's idle.</span>`
    : v.auto_minutes ? `<span class="muted small">Updates itself every ${esc(v.auto_minutes)} min when idle.</span>` : "";
  return `<div class="menu-head menu-version">
      <span class="muted small" title="${esc(v.subject)}">Version ${esc(v.commit)}${when ? ` · ${esc(when)}` : ""}</span>${status}</div>
    <button role="menuitem" data-account="update">Update now</button>
    <button role="menuitem" data-account="restart">Restart Studio</button>`;
}
async function loadMachine() {
  if (!underPlan) return;
  try {
    const res = await fetch("/api/me", { credentials: "same-origin", headers: { Accept: "application/json" } });
    machine.me = res.ok ? await res.json() : null;
  } catch { machine.me = null; }
  if (state.me) renderAccount();
}
$("#account-menu").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-account]");
  if (!btn) return;
  const what = btn.dataset.account;
  if (what === "team") location.hash = "#/team";
  else if (what === "password") openPassword();
  else if (what === "signout") await signOutEverywhere();
  else if (what === "update" || what === "restart") {
    const restart = what === "restart";
    try {
      const res = await fetch("/api/update", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(restart ? { restart: true } : {}),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
      toast(restart ? "Studio restarts as soon as it's idle (usually within a minute)."
        : "Checking for updates. If there's one, Studio restarts by itself in about a minute.");
      await loadMachine();
    } catch (err) { toast(`Couldn't start the update: ${err.message}`); }
  }
});

// The Review badge: drafts waiting, from the dashboard this page is served by. On the
// planner's own address there's no dashboard login, so no badge.
async function pollNav() {
  if (!underPlan || !state.me || !window.StudioShell) return;
  let res;
  try {
    res = await fetch("/api/nav", { credentials: "same-origin", headers: { Accept: "application/json" } });
  } catch { return; }
  if (res.status === 401) {
    // Signed out of Studio (elsewhere, or the login ran out): sign out here too.
    signingOut = true;
    await sb.auth.signOut({ scope: "local" }).catch(() => {});
    location.href = "/login?next=/plan/";
    return;
  }
  const j = res.ok ? await res.json().catch(() => null) : null;
  if (j) window.StudioShell.setBadge(j.review);
}
setInterval(() => { if (!document.hidden) pollNav(); }, 60e3);
window.addEventListener("focus", () => pollNav());

function openPassword() {
  openModal(`<h2 id="modal-title">Change password</h2>
    <form class="form" id="pw-form">
      <input type="email" autocomplete="username" value="${esc(state.me.email)}" hidden readonly>
      <label>Current password<input type="password" id="pw-current" autocomplete="current-password" required></label>
      <label>New password<input type="password" id="pw-new" autocomplete="new-password" minlength="10" required></label>
      <label>New password again<input type="password" id="pw-again" autocomplete="new-password" minlength="10" required></label>
      <p class="muted small">At least 10 characters.</p>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="pw-save">Change password</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "pw-save") return;
    const current = $("#pw-current").value, next = $("#pw-new").value;
    if (!current) return formError("Enter your current password.");
    if (next.length < 10) return formError("Use at least 10 characters.");
    if (next !== $("#pw-again").value) return formError("The two new passwords don't match.");
    e.target.disabled = true;
    const check = await sb.auth.signInWithPassword({ email: state.me.email, password: current });
    if (check.error) { e.target.disabled = false; return formError("Your current password isn't right."); }
    const { error } = await sb.auth.updateUser({ password: next });
    if (error) { e.target.disabled = false; return formError(error.message); }
    closeModal();
    toast("Password changed.");
  });
}

// ── Routes: #/project/<id>, #/idea/<id>, #/checklist/<project_id>, #/team,
//    #/references[/<id>], #/style[/<brand>] ─────────────────────────────────

async function refreshBoard(force = false) {
  await loadProjects();
  await loadBoard();
  if (!$("#planner").hidden && !viewerOpen() && !checklistOpen()) renderPlanner(force);
}
onBoardRefresh(async (force) => { await refreshBoard(force); });
onShootRefresh(async () => { if (!$("#planner").hidden && !viewerOpen() && !checklistOpen()) await refreshBoard(true); });

let routing = Promise.resolve();
// The address before this one: a post opened from a page of the app goes back to it.
const nav = { last: null };
function route() {
  routing = routing.then(doRoute, doRoute).catch((err) => toast(`Couldn't load that: ${err.message}`));
  return routing;
}
// The top bar: where we are, and Today for the current (or last) project.
function setSection(name) {
  const pid = plan.pid || Number(recall("studio.project")) || 0;
  window.StudioShell?.setLinks({ today: pid ? `#/today/${pid}` : "#/today" });
  window.StudioShell?.setCurrent(name);
}

async function doRoute() {
  if (!state.me) return;
  let h = location.hash;
  const prev = nav.last;
  nav.last = h;
  let m;
  const refRoute = refsRoute(h);
  const planRoute = monthRoute(h);
  if (planRoute) {
    hideViewer();
    hideChecklist();
    hideTeam();
    hideRefs();
    $("#planner").hidden = true;
    setSection({ plan: "content", questions: "questions" }[planRoute.page] || "plan");
    await showMonth(planRoute);
    return;
  }
  hideMonth();
  if (refRoute) {
    hideViewer();
    hideChecklist();
    hideTeam();
    $("#planner").hidden = true;
    setSection(refRoute.page === "style" || refRoute.page === "taste" ? "style" : "refs");
    await showRefs(refRoute);
    return;
  }
  hideRefs();
  // #/today on its own (the Today tab before a project is picked): the current project's.
  if (h === "#/today") {
    await loadProjects();
    h = plan.pid ? `#/today/${plan.pid}` : "#/";
    history.replaceState(null, "", h);
  }
  setSection(h === "#/team" ? "" : /^#\/(?:today|checklist)\//.test(h) ? "today" : "plan");
  if ((m = h.match(/^#\/idea\/(\d+)$/))) {
    hideChecklist();
    hideTeam();
    $("#planner").hidden = false;
    const from = !viewerOpen() && prev != null && !/^#\/idea\//.test(prev) ? prev : viewerOpen() ? view.cameFrom : null;
    await openIdea(Number(m[1]), from);
    const pid = view.d?.project_id;
    if (pid && (pid !== plan.pid || !plan.board)) {
      if (!plan.projects.length) await loadProjects();
      selectProject(pid);
      await loadBoard().catch(() => {});
      renderPlanner(true);
      updateArrows();
    }
    return;
  }
  // Today on site; #/checklist/<id> is its old name.
  if ((m = h.match(/^#\/(?:today|checklist)\/(\d+)$/))) {
    hideViewer();
    hideTeam();
    if (Number(m[1]) !== plan.pid) store("studio.project", m[1]);
    setSection("today");
    await openChecklist(Number(m[1]));
    return;
  }
  if (h === "#/team") {
    hideViewer();
    hideChecklist();
    $("#planner").hidden = true;
    await showTeam();
    return;
  }
  // The board.
  hideViewer();
  hideChecklist();
  hideTeam();
  $("#planner").hidden = false;
  m = h.match(/^#\/project\/(\d+)$/);
  await loadProjects();
  if (m && plan.projects.some((p) => p.id === Number(m[1]))) selectProject(Number(m[1]));
  if (plan.pid && location.hash !== `#/project/${plan.pid}`) history.replaceState(null, "", `#/project/${plan.pid}`);
  setSection("plan");
  renderPlanner(true);
  restoreBoardScroll();
  await loadBoard();
  renderPlanner(true);
  restoreBoardScroll();
  forgetBoardScroll();
  // Back from an idea: keyboard focus returns to its card.
  if (view.lastClosed) {
    const card = document.querySelector(`.card[data-plan-idea="${Number(view.lastClosed)}"]`);
    if (card && (document.activeElement === document.body || !document.contains(document.activeElement))) card.focus({ preventScroll: true });
    view.lastClosed = null;
  }
}
window.addEventListener("hashchange", () => route());

// ── Live updates ────────────────────────────────────────────────────────────

const TABLES = ["projects", "arcs", "ideas", "shots", "clips", "takes", "votes", "comments", "jobs", "laptop_status",
  "reference_videos", "reference_analysis", "style_guides"];
const REF_TABLES = new Set(["reference_videos", "reference_analysis", "style_guides", "taste_reports", "reference_imports"]);
const LIBRARY_TABLES = ["taste_reports", "reference_imports"];
const SHOOT_TABLES = ["phases", "site_days", "shot_uploads"];
const PLAN_TABLES = ["content_plan"];
const TOPIC_TABLES = ["client_questions"];
let refreshTimer = null;
let refreshing = false;
let again = false;

function startRealtime() {
  const channel = sb.channel("studio-planner");
  for (const table of TABLES.filter((t) => !REF_TABLES.has(t))) {
    channel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  channel.subscribe((status) => {
    state.live = status;
    if (status === "SUBSCRIBED") scheduleRefresh();
  });
  session.channel = channel;
  // Its own channel: until the database update that adds these tables, subscribing
  // to them fails, and that mustn't take the planning board's live updates down too.
  const refChannel = sb.channel("studio-references");
  for (const table of [...REF_TABLES].filter((t) => !LIBRARY_TABLES.includes(t))) {
    refChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  refChannel.subscribe();
  session.refChannel = refChannel;
  // The reference library's taste reports and imports, on their own channel for the same reason.
  const libChannel = sb.channel("studio-library");
  for (const table of LIBRARY_TABLES) {
    libChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  libChannel.subscribe();
  session.libChannel = libChannel;
  // The shoot plan's tables, on their own channel for the same reason.
  const shootChannel = sb.channel("studio-shoot");
  for (const table of SHOOT_TABLES) {
    shootChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  shootChannel.subscribe();
  session.shootChannel = shootChannel;
  // The content plan (targets and offers), on its own channel for the same reason.
  const planChannel = sb.channel("studio-content-plan");
  for (const table of PLAN_TABLES) {
    planChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  planChannel.subscribe();
  session.planChannel = planChannel;
  // Client questions (suggested topics), on their own channel for the same reason.
  const topicChannel = sb.channel("studio-topics");
  for (const table of TOPIC_TABLES) {
    topicChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  topicChannel.subscribe();
  session.topicChannel = topicChannel;
}

function onChange(table, payload) {
  if (table === "laptop_status") {
    if (payload.new) state.laptop = payload.new;
    renderLaptop();
  }
  if (table === "shot_uploads" && payload.new) onUploadRow(payload.new);
  // References and style guides only matter while their pages are open.
  if (REF_TABLES.has(table) && !refsOpen()) return;
  // A clip the laptop pushed only matters to an open footage picker or board stills.
  scheduleRefresh();
}

// Debounced: a burst of changes (Claude writing a storyboard) becomes one refresh.
export function scheduleRefresh(ms = 350) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshNow, ms);
}
async function refreshNow() {
  if (!state.me) return;
  if (refreshing) { again = true; return; }
  refreshing = true;
  try {
    const tasks = [];
    if (viewerOpen()) tasks.push(loadIdea());
    if (checklistOpen() && !check.busy) tasks.push(loadChecklist());
    if (!$("#planner").hidden || viewerOpen()) tasks.push(refreshBoard());
    if (refsOpen()) tasks.push(loadRefs());
    if (monthOpen()) tasks.push(loadMonthPage());
    await Promise.all(tasks.map((t) => t.catch(() => {})));
  } finally {
    refreshing = false;
    if (again) { again = false; scheduleRefresh(); }
  }
}
document.addEventListener("studio:refresh", () => scheduleRefresh(50));
document.addEventListener("visibilitychange", () => { if (!document.hidden) { scheduleRefresh(50); pollNav(); } });
window.addEventListener("online", () => scheduleRefresh(50));
// A slow safety net if live updates drop; also expires stale jobs on screen.
setInterval(() => { if (!document.hidden) scheduleRefresh(0); }, 60e3);

// ── Keys ────────────────────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  const typing = isTyping(e.target) || e.target.tagName === "SELECT";
  if (modalOpen()) { if (e.key === "Escape") closeModal(); return; }
  if (checklistOpen()) {
    if (e.key === "Escape" && !typing) location.hash = `#/project/${check.pid}`;
    return;
  }
  if (!viewerOpen()) return;
  if (e.key === "Escape") { if (typing) e.target.blur(); else closeIdea(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "j" || e.key === "J") step(1);
  else if (e.key === "k" || e.key === "K") step(-1);
});

boot().catch((err) => showAuth("signin", { error: `Something went wrong: ${err.message}`, onSignedIn: enter }));
