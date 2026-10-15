// Studio planner: sign-in, the header (laptop status, account), hash routes and
// live updates. Views live in board.js, idea.js, checklist.js and team.js.
import {
  $, esc, sb, must, landing, state, isOwner, toast, openModal, closeModal, modalOpen, formError,
  isTyping, laptopOn, laptopSince,
} from "./core.js";
import { showAuth, hideAuth } from "./auth.js";
import { plan, loadProjects, loadBoard, renderPlanner, selectProject, onBoardRefresh } from "./board.js";
import { view, openIdea, hideViewer, closeIdea, loadIdea, viewerOpen, step, updateArrows } from "./idea.js";
import { check, openChecklist, hideChecklist, loadChecklist, checklistOpen } from "./checklist.js";
import { showTeam, hideTeam } from "./team.js";

const SET_PASSWORD = "studio.setPassword";
const session = { started: false, channel: null };

// ── Sign-in ─────────────────────────────────────────────────────────────────

async function boot() {
  if (!sb) { showAuth("noconfig"); return; }
  if (landing.hasToken && (landing.type === "invite" || landing.type === "recovery")) {
    try { sessionStorage.setItem(SET_PASSWORD, landing.type); } catch { /* private mode */ }
  }
  showAuth("loading");
  const { data } = await sb.auth.getSession();
  if (landing.hasToken || landing.error) history.replaceState(null, "", location.pathname);
  sb.auth.onAuthStateChange((event) => {
    if (event === "PASSWORD_RECOVERY") {
      try { sessionStorage.setItem(SET_PASSWORD, "recovery"); } catch { /* private mode */ }
    } else if (event === "SIGNED_OUT" && session.started) {
      stop();
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
  }
  await route();
}

function stop() {
  if (session.channel) sb.removeChannel(session.channel);
  session.channel = null;
  session.started = false;
  hideViewer();
  hideChecklist();
  closeModal();
  $("#app").hidden = true;
  state.me = null;
  plan.projects = [];
  plan.board = null;
  plan.pid = null;
  plan.shellFor = null;
}

// ── Header: the laptop and your account ─────────────────────────────────────

function renderLaptop() {
  const on = laptopOn();
  const since = laptopSince();
  const pill = $("#laptop-btn");
  pill.classList.toggle("on", on);
  $("#laptop-text").textContent = on ? "Studio laptop on" : since ? `Laptop off since ${since}` : "Studio laptop off";
  pill.title = on ? "The Studio laptop is on and syncing." : "Footage matching, music shortlists and Make this wait for it.";
  $("#laptop-info").innerHTML = on
    ? `<p><strong>The Studio laptop is on.</strong></p><p class="muted small">It syncs about every 30 seconds: new footage is matched to shots, and music shortlists and Make this run there.</p>`
    : `<p><strong>The Studio laptop is off${since ? ` (last seen ${esc(since)})` : ""}.</strong></p><p class="muted small">Planning works without it. Footage matching, music shortlists and Make this wait for it and run as soon as it's back on.</p>`;
}
setInterval(() => { if (state.me) renderLaptop(); }, 30e3);
// In case a live update was missed (phone asleep), re-read the heartbeat now and then.
setInterval(async () => {
  if (!state.me || document.hidden) return;
  const { data } = await sb.from("laptop_status").select("*").eq("id", 1).maybeSingle();
  if (data) { state.laptop = data; renderLaptop(); }
}, 60e3);

function renderAccount() {
  const me = state.me;
  $("#account-btn").innerHTML = `<span class="avatar" aria-hidden="true">${esc((me.name || "?").slice(0, 1).toUpperCase())}</span><span class="account-name">${esc(me.name)}</span>`;
  $("#account-btn").setAttribute("aria-label", `Account: ${me.name}`);
  $("#account-menu").innerHTML = `<div class="menu-head"><strong>${esc(me.name)}</strong>
      <span class="muted small">${esc(me.email)} · ${isOwner() ? "Owner" : "Team"}</span></div>
    ${isOwner() ? `<button role="menuitem" data-account="team">Team…</button>` : ""}
    <button role="menuitem" data-account="password">Change password</button>
    <button role="menuitem" data-account="signout">Sign out</button>`;
}
function setMenu(which, open) {
  const [btn, menu] = which === "account" ? ["#account-btn", "#account-menu"] : ["#laptop-btn", "#laptop-info"];
  $(menu).hidden = !open;
  $(btn).setAttribute("aria-expanded", String(open));
  if (open) $(menu).querySelector("button")?.focus();
}
$("#account-btn").addEventListener("click", () => { setMenu("laptop", false); setMenu("account", $("#account-menu").hidden); });
$("#laptop-btn").addEventListener("click", () => { setMenu("account", false); setMenu("laptop", $("#laptop-info").hidden); });
document.addEventListener("click", (e) => {
  if (!e.target.closest("#account")) setMenu("account", false);
  if (!e.target.closest("#laptop")) setMenu("laptop", false);
});
$("#account-menu").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-account]");
  if (!btn) return;
  setMenu("account", false);
  const what = btn.dataset.account;
  if (what === "team") location.hash = "#/team";
  else if (what === "password") openPassword();
  else if (what === "signout") {
    await sb.auth.signOut().catch(() => {});
    stop();
    showAuth("signin", { info: "Signed out.", onSignedIn: enter });
  }
});

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

// ── Routes: #/project/<id>, #/idea/<id>, #/checklist/<project_id>, #/team ───

async function refreshBoard(force = false) {
  await loadProjects();
  await loadBoard();
  if (!$("#planner").hidden && !viewerOpen() && !checklistOpen()) renderPlanner(force);
}
onBoardRefresh(async (force) => { await refreshBoard(force); });

let routing = Promise.resolve();
function route() {
  routing = routing.then(doRoute, doRoute).catch((err) => toast(`Couldn't load that: ${err.message}`));
  return routing;
}
async function doRoute() {
  if (!state.me) return;
  const h = location.hash;
  let m;
  if ((m = h.match(/^#\/idea\/(\d+)$/))) {
    hideChecklist();
    hideTeam();
    $("#planner").hidden = false;
    await openIdea(Number(m[1]));
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
  if ((m = h.match(/^#\/checklist\/(\d+)$/))) {
    hideViewer();
    hideTeam();
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
  renderPlanner(true);
  await loadBoard();
  renderPlanner(true);
  // Back from an idea: keyboard focus returns to its card.
  if (view.lastClosed) {
    const card = document.querySelector(`.card[data-plan-idea="${Number(view.lastClosed)}"]`);
    if (card && (document.activeElement === document.body || !document.contains(document.activeElement))) card.focus({ preventScroll: true });
    view.lastClosed = null;
  }
}
window.addEventListener("hashchange", () => route());

// ── Live updates ────────────────────────────────────────────────────────────

const TABLES = ["projects", "arcs", "ideas", "shots", "clips", "takes", "votes", "comments", "jobs", "laptop_status"];
let refreshTimer = null;
let refreshing = false;
let again = false;

function startRealtime() {
  const channel = sb.channel("studio-planner");
  for (const table of TABLES) {
    channel.on("postgres_changes", { event: "*", schema: "public", table }, (payload) => onChange(table, payload));
  }
  channel.subscribe((status) => {
    state.live = status;
    if (status === "SUBSCRIBED") scheduleRefresh();
  });
  session.channel = channel;
}

function onChange(table, payload) {
  if (table === "laptop_status") {
    if (payload.new) state.laptop = payload.new;
    renderLaptop();
  }
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
    await Promise.all(tasks.map((t) => t.catch(() => {})));
  } finally {
    refreshing = false;
    if (again) { again = false; scheduleRefresh(); }
  }
}
document.addEventListener("studio:refresh", () => scheduleRefresh(50));
document.addEventListener("visibilitychange", () => { if (!document.hidden) scheduleRefresh(50); });
window.addEventListener("online", () => scheduleRefresh(50));
// A slow safety net if live updates drop; also expires stale jobs on screen.
setInterval(() => { if (!document.hidden) scheduleRefresh(0); }, 60e3);

// ── Keys ────────────────────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  const typing = isTyping(e.target) || e.target.tagName === "SELECT";
  if (modalOpen()) { if (e.key === "Escape") closeModal(); return; }
  if (e.key === "Escape" && (!$("#account-menu").hidden || !$("#laptop-info").hidden)) {
    const which = $("#account-menu").hidden ? "laptop" : "account";
    setMenu(which, false);
    $(which === "account" ? "#account-btn" : "#laptop-btn").focus();
    return;
  }
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
