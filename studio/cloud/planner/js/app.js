// Studio planner: sign-in, the header (laptop status, account, the shared top bar from
// ../shell.js), hash routes and live updates. Views live in board.js, idea.js,
// checklist.js and team.js.
import {
  $, esc, sb, must, landing, state, isOwner, toast, openModal, closeModal, modalOpen, formError,
  isTyping, laptopOn, laptopSince, underPlan, recall, store,
} from "./core.js";
import { showAuth, hideAuth } from "./auth.js";
import { plan, loadProjects, prefetchProjects, loadBoard, renderPlanner, selectProject, onBoardRefresh, planMode, showFootageColumn } from "./board.js";
import { home, homeOpen, loadHome, renderHome, onHomeRefresh } from "./home.js";
import { view, openIdea, hideViewer, closeIdea, loadIdea, viewerOpen, step, updateArrows, restoreBoardScroll, forgetBoardScroll } from "./idea.js";
import { check, openChecklist, hideChecklist, loadChecklist, checklistOpen } from "./checklist.js";
import { showTeam, hideTeam } from "./team.js";
import { shoot, onShootRefresh } from "./shoot.js";
import { month, monthRoute, showMonth, hideMonth, monthOpen, loadMonthPage } from "./month.js";
import { onUploadRow, renderTray, studioBase, studioApi } from "./upload.js";
import { postingRoute, showPosting, hidePosting, postingOpen, loadPostingPage } from "./posting.js";
import { gridRoute, showGrid, hideGrid, gridOpen, loadGridPage } from "./grid.js";

// References, the style guide, the library and the taste report (refs.js, library.js, refimport.js: a
// third of the planner's code) load the first time one of their pages opens, not with Plan.
let refsMod = null;
const refsLoad = () => (refsMod ||= import("./refs.js"));
// Insights (#/insights, what kinds of posts work) loads the first time it opens too.
let insMod = null;
const insLoad = () => (insMod ||= import("./insights.js"));
const refsOpen = () => !$("#refs").hidden;
// #/references, #/references/<id>, #/style, #/style/<brand>, #/taste (as refs.js reads them), or null.
function refsRoute(hash) {
  let m;
  if (hash === "#/references") return { page: "list" };
  if ((m = hash.match(/^#\/references\/(\d+)$/))) return { page: "detail", id: Number(m[1]) };
  if ((m = hash.match(/^#\/style(?:\/(urban_makers|personal))?$/))) return { page: "style", brand: m[1] || null };
  if (hash === "#/taste") return { page: "taste" };
  return null;
}
const hideRefs = () => refsMod?.then((m) => m.hideRefs());
const resetRefs = () => refsMod?.then((m) => m.resetRefs());
const showRefs = async (route) => {
  const m = await refsLoad();
  if (refsRoute(location.hash)) await m.showRefs(route);   // not if the page was left while it loaded
};
const loadRefs = async () => (refsMod ? (await refsMod).loadRefs() : undefined);

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
  // config.js (made by Studio) asked already, as the page started: take that answer once.
  const early = window.STUDIO_EARLY_SESSION;
  window.STUDIO_EARLY_SESSION = null;
  try {
    res = (early && await early) || await fetch("/api/planner-session", { credentials: "same-origin", headers: { Accept: "application/json" } });
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
  store("studio.meName", "");
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
  // Everyone's profiles and the Studio machine's heartbeat come with yours (start() uses them):
  // one round trip instead of two.
  const early = Promise.all([
    sb.from("profiles").select("id,name,email,role").then(must).catch(() => []),
    sb.from("laptop_status").select("*").eq("id", 1).maybeSingle().then(must).catch(() => null),
  ]);
  prefetchProjects();   // and the projects, which every page starts from
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
  await start(early);
}

async function start(early) {
  const [profiles, laptop] = await early;
  state.profiles = new Map(profiles.map((p) => [p.id, p]));
  state.laptop = laptop;
  renderAccount();
  renderLaptop();
  setHelpHost();
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
  if (session.postChannel) sb.removeChannel(session.postChannel);
  if (session.gridChannel) sb.removeChannel(session.gridChannel);
  session.channel = session.refChannel = session.shootChannel = session.planChannel = session.topicChannel = session.libChannel = session.postChannel = session.gridChannel = null;
  session.started = false;
  hideViewer();
  hideChecklist();
  closeModal();
  resetRefs();
  hideMonth();
  hidePosting();
  hideGrid();
  Object.assign(month, { data: null, plan: null, loaded: false });
  $("#app").hidden = true;
  state.me = null;
  plan.projects = [];
  plan.board = null;
  plan.pid = null;
  plan.shellFor = null;
  Object.assign(home, { data: null, sig: "" });
  $("#home").hidden = true;
  Object.assign(shoot, { pid: null, data: null, missing: false, sig: "" });
  renderTray();
}

// ── Help, feedback and usage (shell.js, help.js, capture.js) ────────────────

// Where we are, for help and usage: the page's key (help.js PAGES), the address with ids out, and flags the weekly
// report's golden paths start from (ideas waiting for a call, shots to film, an approved draft).
function pageNow() {
  const h = location.hash || "#/";
  const route = `/plan/${h}`;
  const q = (sel) => document.querySelector(sel);
  if (viewerOpen()) {
    const d = view.d;
    if (!d || String(d.id) !== h.split("/").pop()) return { key: "idea", route, pending: true };
    const prod = d.production && typeof d.production === "object" ? d.production : null;
    return { key: "idea", route, flags: {
      calls: d.decision === "proposed" && d.status !== "dismissed" && !prod,
      film: d.decision === "go" && (d.shots || []).some((x) => x.state === "needed" || x.state === "retake"),
      approved: prod?.status === "approved" && !(d.post && d.post.state !== "cancelled"),
    } };
  }
  if (h === "#" || h === "#/") return { key: "home", route, pending: !home.data, flags: { calls: !!q("#home [data-sugg-act]") } };
  if (/^#\/(today|checklist)\//.test(h)) return { key: "today", route, flags: { film: !!q("#checklist .cl-shot:not([aria-pressed=true])") } };
  if (h === "#/team") return { key: "team", route };
  if (gridRoute(h)) return { key: "grid", route };
  if (h === "#/insights") return { key: "insights", route };
  const post = postingRoute(h);
  if (post) return { key: post.page, route };
  const month = monthRoute(h);
  if (month) return { key: month.page === "questions" ? "questions" : "content", route };
  const ref = refsRoute(h);
  if (ref) return { key: ref.page === "style" || ref.page === "taste" ? "style" : "refs", route };
  if (/^#\/(project|ideas)/.test(h)) {
    return { key: planMode() === "shoot" ? "shoot" : "ideas", route, pending: !plan.board, flags: { calls: !!q(".grp-waiting .card") } };
  }
  return { key: "other", route };
}
// Help and feedback reach the Studio machine: served by it (/plan/), with the dashboard's cookie, so a person is
// the same person in Review and Plan; on Netlify, the planner's Bearer token at the Studio address.
async function helpApi(method, path, body) {
  if (underPlan) {
    const res = await fetch(path, { method, credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
    return j;
  }
  if (studioBase() === null) throw new Error("the Studio address isn't known yet");
  return studioApi(method, path, method === "GET" ? undefined : body || {});
}
function setHelpHost() {
  const base = underPlan ? "" : studioBase();
  window.StudioShell?.setHost({
    page: pageNow, api: helpApi, role: () => state.me?.role || "team",
    version: () => machine.me?.version?.commit || state.laptop?.info?.version || "",
    usage: underPlan ? (machine.me?.usage === false ? null : { url: "/api/usage" })
      // Only an https Studio address: the planner's own policy (netlify.toml) lets it reach no other.
      : /^https:\/\//.test(base || "") ? { url: `${base}/api/usage`, token: async () => (await sb.auth.getSession()).data.session?.access_token || null } : {},
  });
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
  store("studio.meName", me.name || "");   // the bar draws it at once next time (shell.js)
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
  if (state.me) { renderAccount(); setHelpHost(); }
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

// ── Routes: #/ (Plan's home), #/ideas and #/project/<id>[/footage|/shoot] (the board), #/idea/<id>,
//    #/today/<project_id>, #/team, #/references[/<id>], #/style[/<brand>], #/content-plan, … ──

async function refreshBoard(force = false) {
  await loadProjects();
  await loadBoard();
  if (!$("#planner").hidden && !viewerOpen() && !checklistOpen()) renderPlanner(force);
}
onBoardRefresh(async (force) => { await refreshBoard(force); });
onShootRefresh(async () => { if (!$("#planner").hidden && !viewerOpen() && !checklistOpen()) await refreshBoard(true); });
onHomeRefresh(async (force) => { if (homeOpen()) { await loadHome(); renderHome(force); } });

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

async function showHome() {
  $("#home").hidden = false;
  if (!home.data) renderHome(true);
  await loadHome();
  renderHome(true);
}
function hideHome() {
  $("#home").hidden = true;
}

async function doRoute() {
  if (!state.me) return;
  let h = location.hash;
  const prev = nav.last;
  nav.last = h;
  let m;
  const refRoute = refsRoute(h);
  const planRoute = monthRoute(h);
  const postRoute = postingRoute(h);
  if (h === "#/insights") {
    hideViewer();
    hideChecklist();
    hideTeam();
    hideRefs();
    hideHome();
    hideMonth();
    hidePosting();
    hideGrid();
    $("#planner").hidden = true;
    setSection("insights");
    const mod = await insLoad();
    if (location.hash === "#/insights") await mod.showInsights();   // not if the page was left while it loaded
    return;
  }
  insMod?.then((mod) => mod.hideInsights());
  if (gridRoute(h)) {
    hideViewer();
    hideChecklist();
    hideTeam();
    hideRefs();
    hideHome();
    hideMonth();
    hidePosting();
    $("#planner").hidden = true;
    setSection("grid");
    await showGrid();
    return;
  }
  hideGrid();
  if (postRoute) {
    hideViewer();
    hideChecklist();
    hideTeam();
    hideRefs();
    hideHome();
    hideMonth();
    $("#planner").hidden = true;
    setSection(postRoute.page);
    await showPosting(postRoute);
    return;
  }
  hidePosting();
  if (planRoute) {
    hideViewer();
    hideChecklist();
    hideTeam();
    hideRefs();
    hideHome();
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
    hideHome();
    $("#planner").hidden = true;
    setSection(refRoute.page === "style" || refRoute.page === "taste" ? "style" : "refs");
    await showRefs(refRoute);
    return;
  }
  hideRefs();
  // Plan's home: what needs a decision across every project.
  if (!h || h === "#" || h === "#/") {
    hideViewer();
    hideChecklist();
    hideTeam();
    $("#planner").hidden = true;
    setSection("plan");
    await showHome();
    if (prev == null || /^#\/(idea|project|today)\//.test(prev || "")) $("#hm-title")?.focus({ preventScroll: true });
    return;
  }
  // #/today on its own (Today on site before a project is picked): the current project's.
  if (h === "#/today") {
    await loadProjects();
    h = plan.pid ? `#/today/${plan.pid}` : "#/";
    history.replaceState(null, "", h);
    if (h === "#/") { await doRoute(); return; }
  }
  setSection(h === "#/team" ? "" : /^#\/(?:today|checklist)\//.test(h) ? "today" : "ideas");
  if ((m = h.match(/^#\/idea\/(\d+)$/))) {
    hideChecklist();
    hideTeam();
    // From Plan's home the post opens over it; from anywhere else over its project's board.
    const overHome = homeOpen();
    if (overHome) setSection("plan");
    if (!overHome) $("#planner").hidden = false;
    const from = !viewerOpen() && prev != null && !/^#\/idea\//.test(prev) ? prev : viewerOpen() ? view.cameFrom : null;
    await openIdea(Number(m[1]), from);
    const pid = view.d?.project_id;
    if (!overHome && pid && (pid !== plan.pid || !plan.board)) {
      if (!plan.projects.length) await loadProjects();
      selectProject(pid);
      await loadBoard().catch(() => {});
      renderPlanner(true);
      updateArrows();
    }
    return;
  }
  hideHome();
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
  // The board (Ideas): #/ideas is the current project's; #/project/<id>/footage opens on the ideas
  // found in new footage, #/project/<id>/shoot on its shoot plan.
  hideViewer();
  hideChecklist();
  hideTeam();
  $("#planner").hidden = false;
  m = h.match(/^#\/project\/(\d+)(?:\/(footage|shoot))?$/);
  const open = m?.[2] || "";
  await loadProjects();
  if (m && plan.projects.some((p) => p.id === Number(m[1]))) selectProject(Number(m[1]));
  if (open === "shoot" || open === "footage") {
    plan.mode = open === "shoot" ? "shoot" : "ideas";
    store("studio.planMode", plan.mode);
  }
  if (plan.pid && location.hash !== `#/project/${plan.pid}`) history.replaceState(null, "", `#/project/${plan.pid}`);
  nav.last = location.hash;
  setSection("ideas");
  renderPlanner(true);
  restoreBoardScroll();
  await loadBoard();
  renderPlanner(true);
  restoreBoardScroll();
  forgetBoardScroll();
  if (open === "footage" && planMode() === "ideas") showFootageColumn();
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
const POST_TABLES = ["posts", "post_results", "account_results"];
const GRID_TABLES = ["grid_pins"];
let refreshTimer = null;
let refreshing = false;
let again = false;

function startRealtime() {
  const channel = sb.channel("studio-planner");
  for (const table of TABLES.filter((t) => !REF_TABLES.has(t))) {
    channel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  // Subscribed (again, after a drop: a phone asleep, the network gone): read what changed meanwhile.
  // Not when it's the first time and quick: the page is loading everything at that moment anyway.
  const since = Date.now();
  let joined = false;
  channel.subscribe((status) => {
    state.live = status;
    if (status === "SUBSCRIBED") {
      if (joined || Date.now() - since > 5000) scheduleRefresh();
      joined = true;
    }
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
  // Instagram posts and results (the Studio machine's copy), on their own channel for the same reason.
  const postChannel = sb.channel("studio-posts");
  for (const table of POST_TABLES) {
    postChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  postChannel.subscribe();
  session.postChannel = postChannel;
  // The profile grid's pins, on their own channel for the same reason.
  const gridChannel = sb.channel("studio-grid");
  for (const table of GRID_TABLES) {
    gridChannel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  gridChannel.subscribe();
  session.gridChannel = gridChannel;
}

// The Studio machine's heartbeat, without what changes on every one (when, and the sync's own counts).
const beat = (l) => JSON.stringify([l?.version, { ...(l?.info || {}), sync: null }]);

function onChange(table, payload) {
  if (table === "laptop_status") {
    const before = state.laptop, wasOn = laptopOn();
    if (payload.new) state.laptop = payload.new;
    renderLaptop();
    // Every 30 s: the status pill is enough, unless Studio came back or told us something new.
    if (before && payload.new && beat(before) === beat(payload.new) && wasOn === laptopOn()) return;
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
    if (!$("#planner").hidden || (viewerOpen() && !homeOpen())) tasks.push(refreshBoard());
    if (homeOpen()) tasks.push(loadHome().then(() => renderHome()));
    if (refsOpen()) tasks.push(loadRefs());
    if (monthOpen()) tasks.push(loadMonthPage());
    if (postingOpen()) tasks.push(loadPostingPage());
    if (gridOpen()) tasks.push(loadGridPage());
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
