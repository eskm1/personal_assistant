// Studio dashboard: a Reels-style grid of every video by stage, a review screen
// to leave timed notes and approve or send drafts back for changes, and a
// planning board where the team plans posts before filming them.
"use strict";

const STAGES = [
  { key: "plan", label: "Plan", hint: "Plan posts before filming. Throw ideas in, let Claude sort them into series with a shot list for each, vote, and tick shots off on site. Footage dropped in the project's inbox folder is matched to its shots automatically." },
  { key: "review", label: "To review", hint: "Drafts waiting for you. Leave notes on the moments that need work, then approve it or send it back. The next draft opens straight away." },
  { key: "ideas", label: "Ideas", hint: "Posts Claude planned from your library. Open one to play its segments, edit the caption, then choose Make this to send it to the editor, or add it to a plan." },
  { key: "producing", label: "Being edited", hint: "Edits in progress, including drafts you sent back. They return to To review when the new version renders." },
  { key: "library", label: "Library", hint: "Raw clips, tagged by Claude and quality-checked. Fix a wrong account or format, or leave a note on how a clip should be used." },
  { key: "ingesting", label: "Ingesting", hint: "Clips being transcribed and tagged right now, plus any that failed and will be retried." },
  { key: "cloud", label: "In the cloud", hint: "Uploads sitting in the OneDrive inbox. The laptop picks each one up once it finishes syncing." },
  { key: "done", label: "Done", hint: "Approved, posted and rejected edits." },
];
const BRAND = { personal: "Personal", urban_makers: "Urban Makers", unsure: "Brand unsure" };
const FORMAT = { talking_head: "Talking head", day_in_the_life: "Day in the life", compilation: "Compilation", other: "Other" };
const STATUS = {
  ready: "Ready for review", revising: "Revising", producing: "Editing", planned: "Planned",
  approved: "Approved", posted: "Posted", rejected: "Rejected", queued: "Queued", uploading: "Uploading",
  processing: "Ingesting", transcribed: "Needs tags", failed: "Failed",
};
// Tiles have little room; the review screen uses the full STATUS wording.
const STATUS_SHORT = { ready: "Ready", transcribed: "Untagged" };
const PLAN_FORMATS = ["talking_head", "day_in_the_life", "compilation"];
const FRAMING = { wide: "Wide", medium: "Medium", close: "Close-up", detail: "Detail", pov: "POV", overhead: "Overhead" };
const KIND = { talking: "Talking to camera", broll: "B-roll", detail: "Detail", before_after: "Before / after", reaction: "Reaction", screen: "Screen" };
const CAMERA = { iphone: "iPhone", zfc: "Z fc", gopro: "GoPro", any: "Any camera" };
const SHOT_STATE = { needed: "Needed", filmed: "Filmed: waiting for upload", got: "Got it", retake: "Retake" };
const DECISION = { go: "Go", parked: "Parked", proposed: "Not decided" };
const VERDICT = { good: "Good to use", check: "Usable, with issues", retake: "Retake" };
const POLL_MS = 10000;
const JOB_POLL_MS = 2500;

const $ = (sel, root = document) => root.querySelector(sel);
const state = { items: [], stage: null, brand: "", list: [], current: null, detail: null, lastSync: 0, ideasJob: {}, me: null };
// The planning board: projects, the open project's board, and UI bits that survive re-renders.
const plan = {
  projects: [], pid: null, board: null, sig: "", shellFor: null, order: [],
  hideParked: recall("studio.hideParked") === "1", editingArc: null, addingArc: false, dragging: false,
};
// The on-site checklist.
const check = { pid: null, data: null, onlyNeeded: recall("studio.onlyNeeded") !== "0", camera: recall("studio.camera") || "", keep: new Set(), busy: 0 };

// ── Helpers ─────────────────────────────────────────────────────────────────

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mmss(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } }
function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }
const isOwner = () => state.me?.role === "owner";

// Only our own paths (/media/...) or http(s) URLs ever become a src or href.
function safeSrc(url) {
  const u = String(url ?? "");
  return /^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u) ? u : "";
}
// Third-party links (Epidemic Sound previews and pages): https only.
function httpsUrl(url) {
  const u = String(url ?? "");
  return /^https:\/\//i.test(u) ? u : "";
}

function ago(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
function shortDate(iso) {
  const t = Date.parse(iso || "");
  return t ? new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
}

function options(values, labels, current, blank) {
  return (blank != null ? `<option value="">${esc(blank)}</option>` : "")
    + values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
}

// A text box someone is typing in (checkboxes and buttons don't count).
function isTyping(el) {
  if (!el) return false;
  if (el.tagName === "TEXTAREA") return true;
  return el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(el.type);
}
function typingIn(root) {
  const a = document.activeElement;
  return !!root && isTyping(a) && root.contains(a);
}
// Re-render without losing what someone has typed but not sent yet.
function keepDrafts(ids, fn) {
  const saved = ids.map((id) => [id, document.getElementById(id)?.value]);
  fn();
  for (const [id, value] of saved) {
    const el = document.getElementById(id);
    if (el && value && !el.disabled) el.value = value;
  }
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("signed out");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

// A missing thumbnail hides itself instead of showing a broken image.
document.addEventListener("error", (e) => {
  if (e.target.tagName === "IMG") e.target.classList.add("broken");
}, true);

function verdictChip(verdict) {
  if (!verdict) return `<span class="chip">Not checked</span>`;
  return `<span class="chip verdict ${esc(verdict)}">${esc({ good: "Good", check: "Check", retake: "Retake" }[verdict] || verdict)}</span>`;
}

function chips(item, short = false) {
  const out = [];
  if (item.brand) out.push(`<span class="chip ${esc(item.brand)}">${esc(BRAND[item.brand] || item.brand)}</span>`);
  if (item.kind === "production" && item.version) out.push(`<span class="chip">v${esc(item.version)}</span>`);
  if (item.open_notes) out.push(`<span class="chip notes">${esc(item.open_notes)} note${item.open_notes > 1 ? "s" : ""}</span>`);
  const status = item.kind === "clip" && item.status === "tagged" ? (item.reviewed ? "Checked" : null)
    : (short && STATUS_SHORT[item.status]) || STATUS[item.status];
  if (item.kind === "clip" && item.usable === 0) out.push(`<span class="chip failed">Unusable</span>`);
  if (item.kind === "clip" && (item.verdict === "retake" || item.verdict === "check")) out.push(verdictChip(item.verdict));
  if (status) out.push(`<span class="chip status ${esc(item.status)}">${esc(status)}</span>`);
  return out.join("");
}

// got / filmed / retake / needed as one segmented bar.
function pbarHTML(p) {
  if (!p || !p.total) return `<span class="pbar none"></span>`;
  const label = `${p.got} got, ${p.filmed} filmed, ${p.retake} to retake, ${p.needed} needed`;
  return `<span class="pbar" role="img" aria-label="${esc(label)}" title="${esc(label)}">${["got", "filmed", "retake", "needed"]
    .map((k) => (p[k] ? `<i class="${k}" style="flex:${Number(p[k]) || 0}"></i>` : "")).join("")}</span>`;
}

// ── Account ─────────────────────────────────────────────────────────────────

function renderAccount() {
  const me = state.me;
  if (!me) return;
  $("#account-btn").innerHTML = `<span class="avatar">${esc((me.name || "?").slice(0, 1).toUpperCase())}</span><span class="account-name">${esc(me.name)}</span>`;
  $("#account-menu").innerHTML = `<div class="menu-head"><strong>${esc(me.name)}</strong>
      <span class="muted small">${esc(me.email)} · ${isOwner() ? "Owner" : "Team"}</span></div>
    ${isOwner() ? `<button role="menuitem" data-account="team">Team…</button>` : ""}
    <button role="menuitem" data-account="password">Change password</button>
    <button role="menuitem" data-account="signout">Sign out</button>
    ${versionHTML(me.version)}`;
}
function versionHTML(v) {
  if (!v) return "";
  const when = v.date ? new Date(v.date).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
  const status = v.error ? `<span class="small" style="color:var(--bad)">Last update failed: ${esc(v.error)}</span>`
    : v.requested ? `<span class="muted small">Update requested: Studio restarts by itself when it's idle.</span>`
    : v.auto_minutes ? `<span class="muted small">Updates itself every ${esc(v.auto_minutes)} min when idle.</span>` : "";
  return `<div class="menu-head" style="border-top:1px solid var(--line)">
      <span class="muted small" title="${esc(v.subject)}">Version ${esc(v.commit)}${when ? ` · ${esc(when)}` : ""}</span>${status}</div>
    ${isOwner() ? `<button role="menuitem" data-account="update">Update now</button>
      <button role="menuitem" data-account="restart">Restart Studio</button>` : ""}`;
}
function setAccountMenu(open) {
  $("#account-menu").hidden = !open;
  $("#account-btn").setAttribute("aria-expanded", String(open));
}
$("#account-btn").addEventListener("click", () => setAccountMenu($("#account-menu").hidden));
document.addEventListener("click", (e) => {
  if (!e.target.closest("#account")) setAccountMenu(false);
  // Close arc menus when clicking anywhere else.
  document.querySelectorAll("details.col-menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
});
$("#account-menu").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-account]");
  if (!btn) return;
  setAccountMenu(false);
  if (btn.dataset.account === "team") openTeam();
  else if (btn.dataset.account === "password") openPassword();
  else if (btn.dataset.account === "update" || btn.dataset.account === "restart") {
    const restart = btn.dataset.account === "restart";
    try {
      await api("/api/update", restart ? { restart: true } : {});
      toast(restart ? "Studio restarts as soon as it's idle (usually within a minute). This page reconnects by itself."
        : "Checking for updates. If there's one, Studio restarts by itself in about a minute.");
      state.me = await api("/api/me");
      renderAccount();
    } catch (err) { toast(`Couldn't start the update: ${err.message}`); }
  }
  else if (btn.dataset.account === "signout") {
    await fetch("/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  }
});

// ── Modal ───────────────────────────────────────────────────────────────────

let modalClick = null;
function openModal(html, onClick, wide = false) {
  $("#modal-body").innerHTML = html;
  $("#modal .modal-card").classList.toggle("wide", wide);
  modalClick = onClick || null;
  $("#modal").hidden = false;
  document.body.style.overflow = "hidden";
  $("#modal-body").querySelector("input:not([type=hidden]), textarea, select")?.focus();
}
function closeModal() {
  $("#modal").hidden = true;
  $("#modal-body").innerHTML = "";
  modalClick = null;
  if ($("#viewer").hidden && $("#checklist").hidden) document.body.style.overflow = "";
}
$("#modal").addEventListener("click", async (e) => {
  if (e.target.id === "modal" || e.target.closest("[data-modal-close]")) { closeModal(); return; }
  if (modalClick) {
    try { await modalClick(e); } catch (err) { toast(`Couldn't do that: ${err.message}`); }
  }
});
$("#modal").addEventListener("submit", (e) => e.preventDefault());

function formError(text) {
  const el = $("#modal-error");
  if (el) { el.textContent = text; el.hidden = !text; }
}

// Change password
function openPassword() {
  openModal(`<h2 id="modal-title">Change password</h2>
    <form class="form" id="pw-form">
      <label>Current password<input type="password" id="pw-current" autocomplete="current-password" required></label>
      <label>New password<input type="password" id="pw-new" autocomplete="new-password" minlength="10" required></label>
      <label>New password again<input type="password" id="pw-again" autocomplete="new-password" minlength="10" required></label>
      <p class="muted small">At least 10 characters. You stay signed in here; other devices are signed out.</p>
      <p class="error-text" id="modal-error" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="pw-save">Change password</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "pw-save") return;
    const current = $("#pw-current").value, next = $("#pw-new").value;
    if (next.length < 10) return formError("Use at least 10 characters.");
    if (next !== $("#pw-again").value) return formError("The two new passwords don't match.");
    try {
      await api("/api/me/password", { current, new: next });
    } catch (err) { return formError(err.message); }
    closeModal();
    toast("Password changed.");
  });
}

// Team (owner)
async function openTeam(invite) {
  openModal(`<h2 id="modal-title">Team</h2><p class="muted">Loading…</p>`, teamClick);
  await renderTeam(invite);
}
async function renderTeam(invite) {
  const t = await api("/api/team");
  const note = $("#invite-note")?.value || "";
  $("#modal-body").innerHTML = `<h2 id="modal-title">Team</h2>
    <p class="muted small">Team members can plan, vote, comment, tick off shots, save captions and fix clip tags. Spending money and the final calls stay with you.</p>
    <h3>People</h3>
    <ul class="people">${t.users.map((u) => `<li>
      <div><strong>${esc(u.name)}</strong>${u.id === state.me.id ? ` <span class="muted">(you)</span>` : ""}
        <span class="chip">${u.role === "owner" ? "Owner" : "Team"}</span>
        <div class="muted small">${esc(u.email)} · ${u.last_login_at ? `last signed in ${esc(ago(u.last_login_at))}` : "never signed in"}</div></div>
      ${u.id !== state.me.id ? `<button class="btn small" data-remove-user="${esc(u.id)}" data-name="${esc(u.name)}">Remove</button>` : ""}
    </li>`).join("")}</ul>
    <h3>Open invites</h3>
    ${t.invites.length ? `<ul class="people">${t.invites.map((i) => `<li>
      <div><strong>${esc(i.note || "Invite")}</strong><div class="muted small">Expires ${esc(shortDate(i.expires_at))}</div></div>
      <button class="btn small" data-cancel-invite="${esc(i.id)}">Cancel</button></li>`).join("")}</ul>`
      : `<p class="muted small">None.</p>`}
    <h3>Invite someone</h3>
    <div class="row"><input id="invite-note" class="grow" maxlength="200" placeholder="Who it's for (optional), e.g. Wei, site supervisor" value="${esc(note)}">
      <button class="btn primary" id="invite-go">Create invite link</button></div>
    ${invite ? `<div class="box invite-link">
      <div class="row"><input id="invite-link" class="grow" readonly value="${esc(invite.link)}" aria-label="Invite link">
        <button class="btn" id="invite-copy">Copy</button></div>
      <p class="muted small">Valid for ${esc(invite.days)} days and works once. Send it to them directly; whoever opens it can make an account.</p>
    </div>` : ""}`;
}
async function teamClick(e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "invite-go") {
    const res = await api("/api/team/invite", { note: $("#invite-note").value.trim() });
    $("#invite-note").value = "";
    await renderTeam(res);
    $("#invite-link")?.select();
  } else if (btn.id === "invite-copy") {
    const link = $("#invite-link").value;
    try { await navigator.clipboard.writeText(link); toast("Invite link copied."); } catch {
      $("#invite-link").select();
      toast("Press Ctrl+C to copy the link.");
    }
  } else if (btn.dataset.cancelInvite) {
    await api(`/api/team/invite/${Number(btn.dataset.cancelInvite)}/cancel`, {});
    toast("Invite cancelled.");
    await renderTeam();
  } else if (btn.dataset.removeUser) {
    if (!confirm(`Remove ${btn.dataset.name}? They're signed out straight away and can't sign in again.`)) return;
    await api(`/api/team/${Number(btn.dataset.removeUser)}/remove`, {});
    toast(`${btn.dataset.name} removed.`);
    await renderTeam();
  }
}

// ── Board ───────────────────────────────────────────────────────────────────

function visible(stage) {
  return state.items.filter((i) => i.stage === stage && (!state.brand || i.brand === state.brand));
}

function renderTabs() {
  $("#tabs").innerHTML = STAGES.map((s) => {
    if (s.key === "plan") return `<button data-stage="plan" class="${state.stage === "plan" ? "on" : ""}">${s.label}</button>`;
    const n = visible(s.key).length;
    const hot = s.key === "review" && n ? " hot" : "";
    return `<button data-stage="${s.key}" class="${s.key === state.stage ? "on" : ""}">${s.label}<span class="count${hot}">${n}</span></button>`;
  }).join("");
  $("#stage-hint").textContent = state.stage === "plan" && state.cloud
    ? "The team plans on the online planner. Footage dropped in a project's inbox folder is still matched to its shots here, and shows up there."
    : STAGES.find((s) => s.key === state.stage).hint;
  renderToolbar();
}

function renderToolbar() {
  $("#toolbar").hidden = state.stage !== "ideas";
  const owner = isOwner();
  $("#idea-focus").hidden = !owner;
  $("#suggest").hidden = !owner;
  const job = state.ideasJob || {};
  $("#suggest").disabled = !!job.running;
  $("#ideas-status").textContent = !owner
    ? "Bryan runs Suggest posts. Open an idea to read it, or add it to a plan."
    : job.running ? "Claude is reading your library and planning posts. This takes a minute or two."
      : job.error ? `Last run failed: ${job.error}` : "";
}

function tileHTML(item, index) {
  const thumb = safeSrc(item.thumb);
  const media = thumb
    ? `<img src="${esc(thumb)}" alt="" loading="lazy">`
    : `<div class="placeholder">${item.kind === "inbox" ? `${esc(item.size_mb)} MB<br>in OneDrive` : "No preview yet"}</div>`;
  const meta = [item.format && FORMAT[item.format], item.duration && `${item.kind === "idea" ? "~" : ""}${mmss(item.duration)}`].filter(Boolean);
  return `<button class="tile" data-index="${index}" aria-label="${esc(item.title)}">
    ${media}
    <div class="chips">${chips(item, true)}</div>
    <div class="shade"><div class="title">${esc(item.title)}</div>
      <div class="meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div></div>
  </button>`;
}

function renderGrid() {
  state.list = visible(state.stage);
  const grid = $("#grid");
  grid.innerHTML = state.list.length
    ? state.list.map(tileHTML).join("")
    : `<div class="empty">Nothing here right now.</div>`;
}

function render() {
  renderTabs();
  const planning = state.stage === "plan";
  $("#grid").hidden = planning;
  $("#planner").hidden = !planning;
  if (planning) (state.cloud ? renderCloudPlan() : renderPlanner());
  else renderGrid();
}

// Planning online (Supabase): the Plan tab links to the planner and shows how the
// laptop's sync is going, instead of the local board.
function renderCloudPlan() {
  const c = state.cloud;
  const root = $("#planner");
  const sig = JSON.stringify(c);
  if (plan.cloudSig === sig && $("#cloud-plan")) return;
  plan.cloudSig = sig;
  plan.shellFor = null;
  const url = safeSrc(c.planner_url);
  const o = c.online || {};
  const online = o.projects != null
    ? ` · online: ${o.projects} project${o.projects === 1 ? "" : "s"}, ${o.ideas} ideas, ${o.shots} shots, ${o.takes} takes, ${o.clips} clip${o.clips === 1 ? "" : "s"}`
    : "";
  root.innerHTML = `<div class="plan-empty" id="cloud-plan">
    <h2>Planning has moved online</h2>
    <p class="muted">Projects, ideas and shot lists live on the online planner now, so the team can plan while this laptop is off. Footage that lands here is still checked, matched to the planned shots and shown there.</p>
    ${url ? `<a class="btn primary" href="${esc(url)}" target="_blank" rel="noopener">Open the planner</a>`
          : `<p class="muted small">Set STUDIO_PLANNER_URL in studio\\.env to link the planner here.</p>`}
    <p class="muted small">${c.last_ok ? `Laptop last synced ${esc(ago(c.last_ok))}` : "Waiting for the laptop's first sync…"}${esc(online)}</p>
    ${c.last_error ? `<p class="small">Last sync failed ${esc(ago(c.last_error_at))}: ${esc(c.last_error)}. It tries again every 30 seconds.</p>` : ""}
    ${(c.jobs || []).length ? `<p class="muted small">Recent laptop jobs: ${c.jobs.map(esc).join("; ")}</p>` : ""}
  </div>`;
}

async function sync() {
  try {
    const data = await api("/api/board");
    if (data.version && state.version && data.version !== state.version) {
      // A new version is running: reload to get its screens too, but never
      // under someone's typing (fields save when you click away).
      const subject = data.version.split("|").slice(1).join("|");
      toast(`Studio updated${subject ? `: ${subject}` : ""}. The page refreshes when you're not typing.`);
      state.reloadPending = true;
    }
    if (state.reloadPending && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      setTimeout(() => location.reload(), 2500);
      state.reloadPending = false;
    }
    state.version = data.version || state.version;
    state.lastSync = Date.now();
    const signature = JSON.stringify(data.items);
    const wasRunning = state.ideasJob?.running;
    state.items = data.items;
    state.ideasJob = data.ideas_job || {};
    const cloudBefore = JSON.stringify(state.cloud ?? null);
    state.cloud = data.cloud || null;
    const cloudChanged = cloudBefore !== JSON.stringify(state.cloud);
    const costs = $("#costs");
    costs.hidden = !data.costs;
    if (data.costs) {
      const c = data.costs;
      costs.textContent = `API this month: ${money(c.usd)}`;
      costs.title = Object.entries(c.by_kind).map(([k, v]) => `${k}: ${money(v.usd)} (${v.calls} calls)`).join("\n");
    }
    if (wasRunning && !state.ideasJob.running && state.ideasJob.count != null) {
      toast(state.ideasJob.count ? `${state.ideasJob.count} new idea${state.ideasJob.count > 1 ? "s" : ""}` : "No new ideas this time. Try a different focus or add more clips.");
    }
    renderToolbar();
    if (!state.stage) {
      // First load: open on whatever needs attention. The team mostly plans.
      const saved = recall("studio.stage");
      const known = STAGES.some((s) => s.key === saved);
      state.stage = isOwner()
        ? (visible("review").length ? "review" : (known ? saved : "library"))
        : (known ? saved : "plan");
    }
    // Re-render only on change, so a hover preview isn't cut off every poll.
    if (signature !== state.signature || (cloudChanged && state.stage === "plan")) {
      state.signature = signature;
      render();
    }
    await refreshOpenItem();
    if (state.stage === "plan" && !state.cloud && $("#viewer").hidden && $("#checklist").hidden) await loadPlan();
    if (!$("#checklist").hidden && !check.busy) await loadChecklist();
  } catch (e) {
    if (e.message !== "signed out") $("#updated").textContent = `Can't reach the laptop (${e.message})`;
  }
}

setInterval(() => {
  if (state.lastSync) {
    const s = Math.round((Date.now() - state.lastSync) / 1000);
    $("#updated").textContent = s < 5 ? "Up to date" : `Updated ${s}s ago`;
  }
}, 1000);

// Hover previews: play a muted loop of the video under the pointer.
$("#grid").addEventListener("mouseover", (e) => {
  const tile = e.target.closest(".tile");
  if (!tile || tile.classList.contains("playing")) return;
  const item = state.list[Number(tile.dataset.index)];
  const src = safeSrc(item?.video);
  if (!src) return;
  let video = tile.querySelector("video");
  if (!video) {
    video = Object.assign(document.createElement("video"), { src, muted: true, loop: true, playsInline: true, preload: "metadata" });
    tile.insertBefore(video, tile.querySelector(".chips"));
  }
  tile.classList.add("playing");
  video.play().catch(() => {});
});
$("#grid").addEventListener("mouseout", (e) => {
  const tile = e.target.closest(".tile");
  if (!tile || tile.contains(e.relatedTarget)) return;
  tile.classList.remove("playing");
  tile.querySelector("video")?.pause();
});
$("#grid").addEventListener("click", (e) => {
  const tile = e.target.closest(".tile");
  if (tile) openItem(state.list[Number(tile.dataset.index)]);
});
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-stage]");
  if (!btn) return;
  state.stage = btn.dataset.stage;
  store("studio.stage", state.stage);
  if (state.stage === "plan") history.replaceState(null, "", plan.pid ? `#/plan/${plan.pid}` : "#/tab/plan");
  else if (location.hash) history.replaceState(null, "", location.pathname);
  render();
});
$("#brand-filter").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-brand]");
  if (!btn) return;
  state.brand = btn.dataset.brand;
  document.querySelectorAll("#brand-filter button").forEach((b) => b.classList.toggle("on", b === btn));
  render();
});

// ── Plan: projects and the board ────────────────────────────────────────────

const currentProject = () => plan.projects.find((p) => p.id === plan.pid) || null;

async function loadProjects() {
  const data = await api("/api/projects");
  plan.projects = data.projects;
  if (!plan.projects.some((p) => p.id === plan.pid)) {
    const saved = Number(recall("studio.project"));
    const active = plan.projects.filter((p) => p.status === "active");
    plan.pid = (plan.projects.find((p) => p.id === saved) || active[0] || plan.projects[0] || {}).id ?? null;
  }
}

function selectProject(pid) {
  plan.pid = pid;
  plan.board = null;
  plan.editingArc = null;
  plan.addingArc = false;
  store("studio.project", String(pid));
}

async function loadPlan(force = false) {
  if (!plan.pid) { plan.board = null; return; }
  const pid = plan.pid;
  const data = await api(`/api/plan/${pid}`);
  if (pid !== plan.pid) return;
  const before = plan.board?.project?.id === pid ? plan.board.jobs?.organise : null;
  plan.board = data;
  const job = data.jobs.organise;
  if (before?.state === "running" && job && job.state !== "running") {
    toast(job.state === "done" ? `Claude added ${job.result?.ideas ?? 0} idea${job.result?.ideas === 1 ? "" : "s"} to the board.` : "Organising failed. See the message on the board.");
  }
  if (state.stage === "plan" && !$("#planner").hidden) {
    renderPlanTop();
    renderPlanBoard(force);
  }
}

function renderPlanner(force = false) {
  const root = $("#planner");
  if (!plan.projects.length) {
    plan.shellFor = null;
    root.innerHTML = `<div class="plan-empty"><h2>Start a plan</h2>
      <p class="muted">A project is one job, like Kensington. Throw post ideas at it, let Claude sort them into series with a shot list for each, then tick shots off on site.</p>
      <button class="btn primary" id="plan-new">New project</button></div>`;
    return;
  }
  const rebuilt = force || plan.shellFor !== plan.pid || !$("#plan-board");
  if (rebuilt) {
    keepDrafts(["plan-dump"], () => { root.innerHTML = plannerShellHTML(); });
    plan.shellFor = plan.pid;
    plan.sig = "";
  }
  if (!plan.board || plan.board.project.id !== plan.pid) {
    $("#plan-board").innerHTML = `<p class="muted">Loading the board…</p>`;
    loadPlan(true).catch((err) => toast(`Couldn't load the plan: ${err.message}`));
    return;
  }
  renderPlanTop();
  renderPlanBoard(rebuilt);
}

function plannerShellHTML() {
  const p = currentProject() || {};
  const active = plan.projects.filter((x) => x.status === "active");
  const archived = plan.projects.filter((x) => x.status !== "active");
  const opt = (x) => `<option value="${esc(x.id)}" ${x.id === plan.pid ? "selected" : ""}>${esc(x.name)}</option>`;
  const briefPreview = (p.brief || "").split("\n")[0].slice(0, 90);
  return `<div class="plan-bar">
      <select id="plan-project" aria-label="Project">${active.map(opt).join("")}
        ${archived.length ? `<optgroup label="Archived">${archived.map(opt).join("")}</optgroup>` : ""}</select>
      ${p.status === "archived" ? `<span class="chip">Archived</span>` : ""}
      <span class="plan-progress" id="plan-progress"></span>
      <div class="plan-bar-actions">
        <button class="btn" id="plan-checklist" title="The shot list for filming on site, made for phones">On-site checklist</button>
        <button class="btn" id="plan-new">New project</button>
      </div>
    </div>
    <details class="plan-details" id="plan-details">
      <summary><strong>Brief</strong> <span class="muted">${briefPreview ? esc(briefPreview) + (p.brief.length > 90 || p.brief.includes("\n") ? "…" : "") : "Add what the job is and what the posts should show"}</span></summary>
      <div class="plan-details-body">
        <div class="row">
          <label class="grow">Name<input id="proj-name" maxlength="120" value="${esc(p.name)}"></label>
          <label>Account<select id="proj-brand">${options(["urban_makers", "personal"], BRAND, p.brand)}</select></label>
        </div>
        <label>Brief<textarea id="proj-brief" rows="4" maxlength="4000" placeholder="What's the job, who's the client, what should the posts show? Claude reads this when it organises ideas.">${esc(p.brief)}</textarea></label>
        <p class="muted small">Inbox folder: uploads dropped in <code>Studio Inbox\\${esc(p.folder || p.name)}</code> on OneDrive are filed to this project and matched to its shots.</p>
        <div class="row">
          <button class="btn" id="proj-save">Save</button>
          <button class="btn ghost" id="proj-archive">${p.status === "archived" ? "Unarchive project" : "Archive project"}</button>
        </div>
      </div>
    </details>
    <div class="dump">
      <textarea id="plan-dump" rows="3" maxlength="20000" placeholder="Throw ideas in: one per line, or paste a messy brain-dump"></textarea>
      <div class="row">
        <button class="btn" id="plan-quick" title="Each line becomes an idea in Unsorted, as written. Free.">Add as ideas</button>
        <button class="btn primary" id="plan-organise" title="Claude sorts them into series (arcs) and gives each idea a hook, a storyboard and a music direction. About a minute; costs a few cents.">Organise with Claude</button>
        <span class="muted small" id="organise-status"></span>
      </div>
      <div id="organise-result"></div>
    </div>
    <div class="board-head">
      <label class="check"><input type="checkbox" id="plan-hide-parked" ${plan.hideParked ? "checked" : ""}> Hide parked</label>
      <span class="muted small hide-mobile">Drag a card to another column to move it, or open it to change its arc.</span>
    </div>
    <div class="board" id="plan-board"></div>`;
}

// Progress and Claude's organise job: refreshed without touching the dump box.
function renderPlanTop() {
  const b = plan.board;
  if (!b || !$("#plan-progress")) return;
  const total = { needed: 0, filmed: 0, got: 0, retake: 0, total: 0 };
  for (const i of b.ideas) for (const k of Object.keys(total)) total[k] += i.progress[k] || 0;
  $("#plan-progress").innerHTML = total.total
    ? `${pbarHTML(total)}<span><strong>${total.got}</strong>/${total.total} shots got</span>`
    : `<span class="muted">No shots planned yet</span>`;
  const job = b.jobs.organise;
  const running = job?.state === "running";
  $("#plan-organise").disabled = running;
  $("#plan-organise").textContent = running ? "Organising…" : "Organise with Claude";
  $("#organise-status").innerHTML = running ? `<span class="spin"></span> Claude is organising your ideas. About a minute; the board updates when it's done.` : "";
  const seen = recall(`studio.organiseSeen.${b.project.id}`);
  let result = "";
  if (job && job.state !== "running" && job.finished && seen !== job.finished) {
    const r = job.result || {};
    result = job.state === "done"
      ? `<div class="organise-result"><button class="icon-btn" data-dismiss-organise="${esc(job.finished)}" aria-label="Dismiss">×</button>
          <strong>Claude added ${esc(r.ideas ?? 0)} idea${r.ideas === 1 ? "" : "s"}${r.arcs ? ` and ${esc(r.arcs)} new arc${r.arcs === 1 ? "" : "s"}` : ""}.</strong>
          ${r.notes ? `<p>${esc(r.notes)}</p>` : ""}</div>`
      : `<div class="organise-result bad"><button class="icon-btn" data-dismiss-organise="${esc(job.finished)}" aria-label="Dismiss">×</button>
          <strong>Organising failed.</strong> <span>${esc(job.error)}</span> Your text wasn't lost from the board; try again or add them as ideas.</div>`;
  }
  $("#organise-result").innerHTML = result;
}

function boardBusy() {
  const board = $("#plan-board");
  return plan.dragging || plan.editingArc != null || plan.addingArc || typingIn(board)
    || !!board?.querySelector("details.col-menu[open]");
}

function renderPlanBoard(force = false) {
  const b = plan.board;
  const el = $("#plan-board");
  if (!b || !el) return;
  const sig = JSON.stringify([b.arcs, b.ideas, plan.hideParked, state.brand, plan.editingArc, plan.addingArc]);
  if (!force && (sig === plan.sig || boardBusy())) return;
  plan.sig = sig;
  const arcIds = new Set(b.arcs.map((a) => a.id));
  const arcOf = (i) => (arcIds.has(i.arc_id) ? i.arc_id : null);
  const shown = b.ideas.filter((i) => (!plan.hideParked || i.decision !== "parked") && (!state.brand || i.brand === state.brand));
  const cols = [];
  if (b.ideas.some((i) => arcOf(i) === null)) cols.push({ arc: null, ideas: shown.filter((i) => arcOf(i) === null) });
  b.arcs.forEach((a, k) => cols.push({ arc: a, index: k, ideas: shown.filter((i) => i.arc_id === a.id) }));
  plan.order = cols.flatMap((c) => c.ideas.map((i) => ({ kind: "plan", id: i.id })));
  if (state.current?.kind === "plan") { state.list = plan.order; updateArrows(); }
  const hidden = b.ideas.length - shown.length;
  el.innerHTML = cols.map((c) => columnHTML(c, b.arcs.length)).join("") + newArcHTML()
    + (hidden ? `<p class="muted small board-note">${hidden} idea${hidden > 1 ? "s" : ""} hidden by your filters.</p>` : "");
  if (plan.editingArc != null) $("#arc-title")?.focus();
  if (plan.addingArc) $("#arc-new-title")?.focus();
}

function columnHTML(c, arcCount) {
  const a = c.arc;
  const key = a ? String(a.id) : "";
  let head;
  if (a && plan.editingArc === a.id) {
    head = `<div class="arc-form">
      <input id="arc-title" maxlength="200" value="${esc(a.title)}" aria-label="Arc name">
      <textarea id="arc-purpose" rows="2" maxlength="1000" placeholder="What this series is for">${esc(a.purpose)}</textarea>
      <div class="row"><button class="btn small primary" data-arc-save="${esc(a.id)}">Save</button><button class="btn small" data-arc-cancel>Cancel</button></div></div>`;
  } else {
    head = `<div class="col-title"><h3>${a ? esc(a.title) : "Unsorted"}</h3><span class="count">${c.ideas.length}</span>
      ${a ? `<details class="col-menu"><summary aria-label="Arc options" title="Arc options">⋯</summary><div class="menu">
        <button data-arc-action="edit" data-arc-id="${esc(a.id)}">Rename or edit purpose</button>
        <button data-arc-action="left" data-arc-id="${esc(a.id)}" ${c.index === 0 ? "disabled" : ""}>Move left</button>
        <button data-arc-action="right" data-arc-id="${esc(a.id)}" ${c.index === arcCount - 1 ? "disabled" : ""}>Move right</button>
        <button data-arc-action="delete" data-arc-id="${esc(a.id)}" class="danger">Delete arc…</button></div></details>` : ""}</div>
      ${a ? (a.purpose ? `<p class="col-purpose">${esc(a.purpose)}</p>` : "") : `<p class="col-purpose">Not in a series yet. Drag into an arc, or let Claude organise them.</p>`}`;
  }
  return `<section class="col" data-arc="${esc(key)}">
    <header class="col-head">${head}</header>
    <div class="col-cards">${c.ideas.map(cardHTML).join("") || `<p class="muted small col-empty">Drop ideas here</p>`}</div>
  </section>`;
}

function cardHTML(i) {
  const p = i.progress;
  const thumb = safeSrc(i.thumb);
  const ups = i.votes.up.length, downs = i.votes.down.length;
  const meta = [
    p.total ? `<span>${esc(p.got)}/${esc(p.total)} shots</span>` : `<span>No shots yet</span>`,
    ups ? `<span title="${esc(i.votes.up.join(", "))}">👍 ${ups}</span>` : "",
    downs ? `<span title="${esc(i.votes.down.join(", "))}">👎 ${downs}</span>` : "",
    i.comments ? `<span title="Comments">💬 ${esc(i.comments)}</span>` : "",
  ].filter(Boolean).join("");
  return `<button class="card ${esc(i.decision)}" data-plan-idea="${esc(i.id)}" draggable="true">
    ${thumb ? `<img class="card-thumb" src="${esc(thumb)}" alt="" loading="lazy">` : ""}
    <span class="card-body">
      <span class="card-title">${esc(i.title)}</span>
      <span class="card-chips">
        <span class="chip ${esc(i.brand)}">${esc(BRAND[i.brand] || i.brand)}</span>
        ${i.decision === "go" ? `<span class="chip go">Go</span>` : i.decision === "parked" ? `<span class="chip parked">Parked</span>` : ""}
        ${i.status === "made" ? `<span class="chip producing">In edit</span>` : ""}
      </span>
      ${pbarHTML(p)}
      <span class="card-meta">${meta}</span>
    </span>
  </button>`;
}

function newArcHTML() {
  if (plan.addingArc) {
    return `<section class="col col-new"><div class="arc-form">
      <input id="arc-new-title" maxlength="200" placeholder="Arc name, e.g. Waterproofing" aria-label="Arc name">
      <textarea id="arc-new-purpose" rows="2" maxlength="1000" placeholder="What this series is for (optional)"></textarea>
      <div class="row"><button class="btn small primary" data-arc-save="new">Add arc</button><button class="btn small" data-arc-cancel>Cancel</button></div>
    </div></section>`;
  }
  return `<section class="col col-new"><button class="new-arc" data-new-arc>+ New arc</button></section>`;
}

async function saveArc(which) {
  if (which === "new") {
    const title = $("#arc-new-title").value.trim();
    if (!title) { $("#arc-new-title").focus(); return; }
    await api(`/api/project/${plan.pid}/arcs`, { title, purpose: $("#arc-new-purpose").value.trim() });
    plan.addingArc = false;
    toast("Arc added.");
  } else {
    const title = $("#arc-title").value.trim();
    if (!title) { $("#arc-title").focus(); return; }
    await api(`/api/arc/${Number(which)}`, { title, purpose: $("#arc-purpose").value.trim() });
    plan.editingArc = null;
  }
  await loadPlan(true);
}

async function arcAction(action, id) {
  if (action === "edit") {
    plan.editingArc = id;
    renderPlanBoard(true);
    return;
  }
  if (action === "delete") {
    const arc = plan.board.arcs.find((a) => a.id === id);
    if (!confirm(`Delete the arc "${arc?.title}"? Its ideas move to Unsorted; nothing else is lost.`)) return;
    await api(`/api/arc/${id}`, { delete: true });
    toast("Arc deleted. Its ideas are in Unsorted.");
  } else {
    await api(`/api/arc/${id}`, { move: action === "left" ? -1 : 1 });
  }
  await loadPlan(true);
}

async function addIdeas(organise) {
  const box = $("#plan-dump");
  const text = box.value.trim();
  if (!text) { box.focus(); toast("Write some ideas first."); return; }
  if (organise) {
    await api(`/api/project/${plan.pid}/organise`, { text });
    box.value = "";
    if (plan.board) plan.board.jobs.organise = { state: "running" };
    renderPlanTop();
    toast("Claude is organising them. About a minute.");
  } else {
    const res = await api(`/api/project/${plan.pid}/ideas`, { text });
    box.value = "";
    toast(`Added ${res.added} idea${res.added > 1 ? "s" : ""} to Unsorted.`);
    await loadPlan(true);
  }
}

async function saveProject(fields, message) {
  await api(`/api/project/${plan.pid}`, fields);
  await loadProjects();
  renderPlanner(true);
  if (message) toast(message);
}

function openNewProject() {
  openModal(`<h2 id="modal-title">New project</h2>
    <form class="form">
      <label>Name<input id="np-name" maxlength="120" placeholder="e.g. Kensington" required></label>
      <label>Account<select id="np-brand">${options(["urban_makers", "personal"], BRAND, "urban_makers")}</select></label>
      <label>Inbox folder <span class="muted">(optional)</span><input id="np-folder" maxlength="80" placeholder="Same as the name"></label>
      <p class="muted small" id="np-hint">Studio makes the OneDrive folder <code>Studio Inbox\\…</code>; uploads dropped there are filed to this project and matched to its shots.</p>
      <label>Brief <span class="muted">(optional)</span><textarea id="np-brief" rows="4" maxlength="4000" placeholder="What's the job, who's the client, what should the posts show?"></textarea></label>
      <p class="error-text" id="modal-error" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="np-save">Create project</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "np-save") return;
    const name = $("#np-name").value.trim();
    if (!name) return formError("Give the project a name.");
    let res;
    try {
      res = await api("/api/projects", { name, brand: $("#np-brand").value, folder: $("#np-folder").value.trim(), brief: $("#np-brief").value.trim() });
    } catch (err) { return formError(err.message); }
    closeModal();
    selectProject(res.id);
    await loadProjects();
    state.stage = "plan";
    history.pushState(null, "", `#/plan/${res.id}`);
    render();
    renderPlanner(true);
    toast(`${name} created.`);
  });
  const hint = () => {
    const folder = $("#np-folder").value.trim() || $("#np-name").value.trim() || "…";
    $("#np-hint").innerHTML = `Studio makes the OneDrive folder <code>Studio Inbox\\${esc(folder)}</code>; uploads dropped there are filed to this project and matched to its shots.`;
  };
  $("#np-name").addEventListener("input", hint);
  $("#np-folder").addEventListener("input", hint);
}

$("#planner").addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-plan-idea]");
  if (!t) return;
  const ds = t.dataset;
  try {
    if (t.id === "plan-new") openNewProject();
    else if (t.id === "plan-checklist") openChecklist(plan.pid);
    else if (t.id === "plan-quick") await addIdeas(false);
    else if (t.id === "plan-organise") await addIdeas(true);
    else if (t.id === "proj-save") {
      const name = $("#proj-name").value.trim();
      await saveProject({ ...(name ? { name } : {}), brand: $("#proj-brand").value, brief: $("#proj-brief").value }, "Project saved.");
    } else if (t.id === "proj-archive") {
      const p = currentProject();
      if (p.status === "archived") await saveProject({ status: "active" }, `${p.name} is active again.`);
      else if (confirm(`Archive ${p.name}? It stays readable; its shots stop being matched to new footage.`)) {
        await saveProject({ status: "archived" }, `${p.name} archived.`);
      }
    } else if (ds.dismissOrganise) {
      store(`studio.organiseSeen.${plan.pid}`, ds.dismissOrganise);
      renderPlanTop();
    } else if (ds.planIdea) openPlanIdea(Number(ds.planIdea));
    else if (ds.arcAction) {
      t.closest("details")?.removeAttribute("open");
      await arcAction(ds.arcAction, Number(ds.arcId));
    } else if (ds.arcSave) await saveArc(ds.arcSave);
    else if (ds.arcCancel != null) {
      plan.editingArc = null;
      plan.addingArc = false;
      renderPlanBoard(true);
    } else if (ds.newArc != null) {
      plan.addingArc = true;
      renderPlanBoard(true);
    }
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
  }
});
$("#planner").addEventListener("change", async (e) => {
  if (e.target.id === "plan-project") {
    selectProject(Number(e.target.value));
    history.replaceState(null, "", `#/plan/${plan.pid}`);
    renderPlanner(true);
  } else if (e.target.id === "plan-hide-parked") {
    plan.hideParked = e.target.checked;
    store("studio.hideParked", plan.hideParked ? "1" : "0");
    renderPlanBoard(true);
  }
});
$("#planner").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.target.id === "arc-title" || e.target.id === "arc-new-title")) {
    e.preventDefault();
    saveArc(e.target.id === "arc-title" ? plan.editingArc : "new").catch((err) => toast(`Couldn't save: ${err.message}`));
  } else if (e.key === "Escape" && e.target.closest(".arc-form")) {
    plan.editingArc = null;
    plan.addingArc = false;
    renderPlanBoard(true);
  } else if (e.target.id === "plan-dump" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    addIdeas(false).catch((err) => toast(`Couldn't add: ${err.message}`));
  }
});

// Drag cards between columns (desktop).
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
  document.querySelectorAll(".col.drop").forEach((c) => c.classList.remove("drop"));
});
$("#planner").addEventListener("dragover", (e) => {
  const col = e.target.closest(".col:not(.col-new)");
  if (!col || !plan.dragging) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".col.drop").forEach((c) => { if (c !== col) c.classList.remove("drop"); });
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
    await api(`/api/plan-idea/${id}`, { arc_id: arcId });
    await loadPlan(true);
  } catch (err) {
    toast(`Couldn't move it: ${err.message}`);
  }
});

// ── Viewer ──────────────────────────────────────────────────────────────────

const player = () => $("#player-frame video");

function openAny(item) {
  if (item?.kind === "plan") openPlanIdea(item.id);
  else openItem(item);
}

async function openItem(item) {
  if (!item) return;
  $("#player-frame").querySelectorAll("video, audio").forEach((m) => m.pause());
  state.current = { kind: item.kind, id: item.id };
  const route = `#/${item.kind}/${encodeURIComponent(item.id)}`;
  if (location.hash !== route) history.pushState(null, "", route);
  $("#viewer").hidden = false;
  $("#viewer").classList.remove("plan-mode");
  document.body.style.overflow = "hidden";
  renderPlayer(item);
  await loadDetail();
  updateArrows();
}

function closeViewer(silent = false) {
  const wasPlan = state.current?.kind === "plan";
  const pid = wasPlan ? state.detail?.project_id : null;
  $("#viewer").querySelectorAll("video, audio").forEach((m) => m.pause());
  state.current = null;
  state.detail = null;
  $("#viewer").hidden = true;
  if (wasPlan) $("#player-frame").innerHTML = "";
  $("#viewer").classList.remove("plan-mode");
  document.body.style.overflow = "";
  if (silent) return;
  if (wasPlan) {
    if (pid && pid !== plan.pid) selectProject(pid);
    state.stage = "plan";
    history.pushState(null, "", plan.pid ? `#/plan/${plan.pid}` : "#/tab/plan");
    render();
    loadPlan(true).catch(() => {});
  } else if (location.hash) history.pushState(null, "", location.pathname);
}

function renderPlayer(item) {
  const frame = $("#player-frame");
  const video = safeSrc(item.video), original = safeSrc(item.original), thumb = safeSrc(item.thumb);
  if (video) {
    const useOriginal = original && recall("studio.original") === "1";
    frame.innerHTML = `<video controls autoplay playsinline src="${esc(useOriginal ? original : video)}" ${thumb ? `poster="${esc(thumb)}"` : ""}></video>
      ${original ? `<button class="quality${useOriginal ? " on" : ""}" id="quality" title="Switch between the 1080p preview and your untouched file">${useOriginal ? "Original" : "1080p preview"}</button>` : ""}`;
    player().addEventListener("timeupdate", updatePinLabel);
    player().addEventListener("error", () => {
      // Originals from an iPhone are usually HEVC, which some browsers can't play.
      if (player().src.endsWith("/original")) {
        toast("This browser can't play the original (iPhone HEVC). Showing the 1080p preview.");
        setQuality(item, false);
      }
    });
    $("#quality")?.addEventListener("click", () => setQuality(item, !$("#quality").classList.contains("on")));
  } else {
    const why = item.kind === "inbox" ? "Still in OneDrive. It plays here once it's ingested." : "Being processed. It plays here once a preview is ready.";
    frame.innerHTML = `<div class="placeholder">${why}</div>`;
  }
}

function setQuality(item, original) {
  const video = player();
  const at = video.currentTime, wasPlaying = !video.paused;
  store("studio.original", original ? "1" : "0");
  video.src = safeSrc(original ? item.original : item.video);
  video.addEventListener("loadedmetadata", () => {
    video.currentTime = at;
    if (wasPlaying) video.play().catch(() => {});
  }, { once: true });
  const btn = $("#quality");
  btn.classList.toggle("on", original);
  btn.textContent = original ? "Original" : "1080p preview";
}

async function loadDetail() {
  const { kind, id } = state.current;
  if (kind === "plan") return loadPlanIdea(true);
  if (kind === "inbox") {
    const item = state.items.find((i) => i.kind === "inbox" && i.id === id);
    state.detail = item || null;
  } else {
    state.detail = await api(`/api/${kind}/${id}`);
  }
  if (kind === "idea" && !plan.projects.length) await loadProjects().catch(() => {});
  renderPanel();
}

async function refreshOpenItem() {
  if (!state.current || state.current.kind === "inbox") return;
  if (state.current.kind === "plan") return loadPlanIdea();
  const before = state.detail;
  const fresh = await api(`/api/${state.current.kind}/${state.current.id}`).catch(() => null);
  if (!fresh || !before) return;
  const resolveChanged = (fresh.resolve || {}).state !== (before.resolve || {}).state;
  if (resolveChanged && fresh.status === before.status && fresh.version === before.version) {
    state.detail = fresh;
    renderPanel();
    if (fresh.resolve.state === "ready") toast("The DaVinci Resolve project is ready");
    return;
  }
  if (fresh.status !== before.status || fresh.version !== before.version) {
    state.detail = fresh;
    if (fresh.video !== before.video) renderPlayer(fresh);
    renderPanel();
    if (fresh.status === "ready") toast(`v${fresh.version} is ready to review`);
  }
}

function resolveHTML(d) {
  const r = d.resolve;
  if (!r || !isOwner()) return "";
  const body = {
    none: `<p class="muted">Finish this by hand in the free DaVinci Resolve: the cut from your original clips, with captions, graphics and music on their own layers.</p>
      <div class="row" style="margin-top:10px"><button class="btn" id="resolve-build">Make Resolve project</button></div>`,
    building: `<p class="muted">Building the project (captions and graphics render as their own layers; a minute or two)…</p>`,
    ready: `<p class="muted">In <code>${esc(r.folder)}</code>. In Resolve: File &gt; Import &gt; Timeline &gt; <strong>timeline.fcpxml</strong>.</p>
      <div class="row" style="margin-top:10px"><button class="btn" id="resolve-open">Show folder</button>
      <button class="btn" id="resolve-build">Rebuild</button></div>`,
    failed: `<p><strong>Couldn't build it:</strong> ${esc(r.error || "")}</p>
      <div class="row" style="margin-top:10px"><button class="btn" id="resolve-build">Try again</button></div>`,
  }[r.state];
  return `<div class="box"><h3>Open in DaVinci Resolve</h3>${body}</div>`;
}

function notesHTML(notes, currentVersion, isProduction) {
  if (!notes.length) return `<p class="muted">No notes yet.</p>`;
  return `<ul class="notes">${notes.map((n) => {
    const stamp = n.at_seconds == null ? "" : `<button class="stamp" data-seek="${Number(n.at_seconds) || 0}">${mmss(n.at_seconds)}</button>`;
    const tags = [
      isProduction && n.version !== currentVersion ? `v${n.version}` : "",
      n.status === "addressed" ? "addressed" : "",
      n.remember ? "in playbook" : "",
    ].filter(Boolean).join(" · ");
    return `<li class="${esc(n.status)}">${n.author ? `<span class="author">${esc(n.author)}</span>` : ""}${stamp}<span class="note-body">${esc(n.body)}</span>${tags ? `<span class="tag">${esc(tags)}</span>` : ""}</li>`;
  }).join("")}</ul>`;
}

function money(usd) {
  return usd < 0.01 ? "under 1¢" : `$${usd.toFixed(2)}`;
}

function shotsHTML(heading, shots) {
  if (!shots || !shots.length) return "";
  return `<div><h3>${esc(heading)}</h3><ul class="shots">${shots.map((s) =>
    `<li><strong>${esc(s.shot)}</strong><span>${esc(s.why)}</span></li>`).join("")}</ul></div>`;
}

function musicHTML(m) {
  if (!m) return "";
  let text;
  if (m.source === "epidemic") text = `${esc(m.title)} by ${esc(m.artist)} (Epidemic Sound)`;
  else if (m.source === "instagram") text = esc(m.note);
  else text = `None this time${m.error ? `: ${esc(m.error)}` : ""}. Mood asked for: ${esc(m.mood)}.`;
  return `<div><h3>Music</h3><p>${text}</p></div>`;
}

function qaHTML(qa) {
  if (!qa) return "";
  const parts = [];
  if (qa.match != null) parts.push(`${Math.round(qa.match * 100)}% of words intact`);
  if (qa.lufs != null) parts.push(`${qa.lufs.toFixed(1)} LUFS`);
  if (qa.true_peak != null) parts.push(`peak ${qa.true_peak.toFixed(1)} dB`);
  const warn = qa.match != null && qa.match < 0.95 && qa.missing?.length
    ? `<p class="muted">Possibly clipped or misheard: ${qa.missing.map(esc).join(", ")}</p>` : "";
  return parts.length ? `<div><h3>Checks</h3><p>${parts.join(" · ")}</p>${warn}</div>` : "";
}

// A clip's quality check: the verdict, what's wrong and what to do next time.
function qualityHTML(q) {
  if (!q) return `<div class="qcheck"><h3>Quality</h3><p class="muted">Not checked: this clip came in before quality checks.</p></div>`;
  const num = (v, digits = 0) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : null);
  const pct = (v, digits = 0) => (typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(digits)}%` : null);
  const a = q.audio || {}, p = q.picture || {};
  const rows = [
    ["Voice level", num(a.voice_db, 1) && `${num(a.voice_db, 1)} dB`],
    ["Background noise", num(a.background_db, 1) && `${num(a.background_db, 1)} dB`],
    ["Voice above background", num(a.snr_db, 1) && `${num(a.snr_db, 1)} dB (18+ is clean)`],
    ["Distorted samples", pct(a.clipped_fraction, 2)],
    ["Speech clarity", num(a.speech_confidence, 2) && `${num(a.speech_confidence, 2)} (closer to 0 is clearer)`],
    ["Brightness", pct(p.brightness)],
    ["Blown highlights", pct(p.blown_fraction) && `${pct(p.blown_fraction)} of the frame`],
    ["Contrast", pct(p.contrast)],
    ["Frames checked", num(p.frames_checked)],
  ].filter(([, v]) => v);
  const flags = q.flags || [];
  return `<div class="qcheck v-${esc(q.verdict)}">
    <h3>Quality</h3>
    <p class="verdict-line"><span class="dot"></span>${esc(VERDICT[q.verdict] || q.verdict)}</p>
    ${flags.length ? `<ul class="flags">${flags.map((f) => `<li class="${esc(f.level)}">
      <strong>${esc(f.text)}</strong>${f.tip ? `<span>Next time: ${esc(f.tip)}</span>` : ""}</li>`).join("")}</ul>` : ""}
    <details><summary>Measurements</summary>
      ${rows.length ? `<dl class="measures">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : `<p>No measurements.</p>`}
      <p class="muted small">${q.judged ? "Focus, stability, framing and lighting were also checked by Claude." : "Focus, stability and framing weren't checked (tagged without Claude)."}</p>
    </details>
  </div>`;
}

function usedInPlanHTML(shots) {
  if (!shots?.length) return "";
  return `<div><h3>Used in the plan</h3><ul class="moments">${shots.map((s) => `<li><button data-open-plan-idea="${esc(s.idea_id)}">
    <strong>${esc(s.idea_title)}</strong> ${esc(s.description)}
    <span class="muted small">${mmss(s.start)}–${s.end != null ? mmss(s.end) : "end"} · ${s.source === "auto" ? "matched automatically" : "added by hand"}</span></button></li>`).join("")}</ul></div>`;
}

function composerHTML(placeholder) {
  return `<div>
    <h3>Add a note</h3>
    <textarea id="note-body" placeholder="${esc(placeholder)}"></textarea>
    <div class="row" style="margin-top:8px">
      <label class="check"><input type="checkbox" id="note-pin" checked> <span id="pin-label">Pin to this moment</span></label>
      ${isOwner() ? `<label class="check" title="Adds it to the playbook the editor follows on every future edit"><input type="checkbox" id="note-remember"> Remember for future edits</label>` : ""}
      <button class="btn" id="note-add" style="margin-left:auto">Add note</button>
    </div>
  </div>`;
}

const bryanDecides = (text) => `<div class="box bryan"><p><strong>Bryan decides.</strong> ${esc(text)}</p></div>`;

function adoptHTML() {
  const active = plan.projects.filter((p) => p.status === "active");
  if (!active.length) return `<div class="box"><h3>Add to a plan</h3><p class="muted">Make a project on the Plan tab first.</p></div>`;
  const pid = active.some((p) => p.id === plan.pid) ? plan.pid : active[0].id;
  return `<div class="box"><h3>Add to a plan</h3>
    <p class="muted small">Moves it onto a project's board: its footage becomes shots that already have takes, and its wish list becomes shots to film.</p>
    <div class="row" style="margin-top:10px">
      <select id="adopt-project" aria-label="Project">${active.map((p) => `<option value="${esc(p.id)}" ${p.id === pid ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <select id="adopt-arc" aria-label="Arc"><option value="">Unsorted</option></select>
      <button class="btn" id="adopt-go">Add to plan</button>
    </div></div>`;
}
async function fillAdoptArcs() {
  const sel = $("#adopt-project");
  if (!sel) return;
  const pid = Number(sel.value);
  const arcs = plan.board?.project.id === pid ? plan.board.arcs : (await api(`/api/plan/${pid}`)).arcs;
  if (Number($("#adopt-project")?.value) !== pid) return;
  $("#adopt-arc").innerHTML = `<option value="">Unsorted</option>${arcs.map((a) => `<option value="${esc(a.id)}">${esc(a.title)}</option>`).join("")}`;
}

function renderPanel() {
  const d = state.detail;
  const panel = $("#panel");
  if (!d) { panel.innerHTML = `<p class="muted">This item is gone.</p>`; return; }
  const owner = isOwner();
  const head = `<div><h2>${esc(d.title)}</h2></div><div class="chips">${chips(d)}${d.format ? `<span class="chip">${esc(FORMAT[d.format] || d.format)}</span>` : ""}</div>`;

  if (d.kind === "inbox") {
    panel.innerHTML = `${head}<div class="box"><p>${esc(d.size_mb)} MB in the OneDrive inbox.
      ${d.status === "uploading" ? "Still syncing to the laptop." : "Synced; the worker picks it up on its next pass (within a minute)."}</p></div>`;
    return;
  }

  if (d.kind === "idea") {
    panel.innerHTML = `${head}
      <div><h3>Hook</h3><p>${esc(d.hook)}</p></div>
      <div><h3>Why it works</h3><p>${esc(d.angle)}</p></div>
      ${d.beats.length ? `<div><h3>Structure</h3><ol class="beats">${d.beats.map((b) => `<li>${esc(b)}</li>`).join("")}</ol></div>` : ""}
      <div><h3>Footage (${d.segments.length} segment${d.segments.length > 1 ? "s" : ""})</h3><ul class="moments">${d.segments.map((s, i) =>
        `<li><button data-segment="${i}"><strong>${esc(s.clip_title)} · ${mmss(s.start)}–${mmss(s.end)}</strong> ${esc(s.purpose)}</button></li>`).join("")}</ul></div>
      ${shotsHTML("Film these to make it stronger", d.wishlist)}
      <div><h3>Caption</h3><textarea id="caption">${esc(d.caption)}</textarea></div>
      ${d.status === "new" ? adoptHTML() : ""}
      ${d.status !== "new" ? `<div class="box"><p>Already sent to the editor.</p></div>`
        : owner ? `<div class="decision row">
        <button class="btn primary" id="idea-make">Make this</button>
        <button class="btn" id="idea-dismiss">Dismiss</button>
      </div>` : bryanDecides("He makes or dismisses library ideas. You can add this one to a plan.")}`;
    fillAdoptArcs().catch(() => {});
    return;
  }

  if (d.kind === "production") {
    const canDecide = d.status === "ready";
    const openNow = d.notes.filter((n) => n.status === "open").length;
    const settled = d.status === "approved" || d.status === "rejected";
    const statusBox = canDecide || d.status === "failed" ? "" : `<div class="box"><p>${d.status === "revising" || d.status === "producing" || d.status === "planned"
      ? "The editor is working on this. It comes back to To review when the new version renders."
      : `${esc(STATUS[d.status] || d.status)}.`}</p>
      ${owner && settled ? `<div class="row" style="margin-top:10px"><button class="btn" id="reopen" title="Move it back to To review so you can decide again">Back to review</button></div>` : ""}</div>`;
    panel.innerHTML = `${head}
      ${d.status === "failed" ? `<div class="box"><p><strong>The edit failed:</strong> ${esc(d.error)}</p>
        ${owner ? `<div class="row" style="margin-top:10px"><button class="btn primary" id="retry-edit">Retry edit</button></div>` : `<p class="muted">Bryan can retry it.</p>`}</div>` : ""}
      ${d.edit_summary ? `<div><h3>What the editor did</h3><p>${esc(d.edit_summary)}</p></div>` : ""}
      ${musicHTML(d.music)}
      ${qaHTML(d.qa)}
      ${shotsHTML("The editor wished it had", d.missing_shots)}
      ${d.idea ? `<div><h3>Brief</h3><p>${esc(d.idea)}</p></div>` : ""}
      ${d.cost ? `<p class="muted">API cost so far: ${money(d.cost)}${d.graphics ? ` · ${d.graphics} graphic${d.graphics > 1 ? "s" : ""}` : ""}</p>` : ""}
      ${resolveHTML(d)}
      ${d.sources.length ? `<div><h3>Made from</h3><p class="muted">${d.sources.map((s) => esc(s.title)).join(", ")}</p></div>` : ""}
      ${statusBox}
      <div><h3>Notes</h3>${notesHTML(d.notes, d.version, true)}</div>
      ${composerHTML("What should change? e.g. \"Cut the first two seconds\", \"Music too loud here\"")}
      <div><h3>Post caption</h3><textarea id="caption" placeholder="The caption you'll post. You have the final say.">${esc(d.caption)}</textarea>
        ${canDecide && owner ? "" : `<div class="row" style="margin-top:8px"><button class="btn" id="caption-save">Save caption</button></div>`}</div>
      ${canDecide ? (owner ? `<div class="decision row">
        <button class="btn primary" id="decide-changes" ${openNow ? "" : "disabled title=\"Add a note first\""}>Request changes${openNow ? ` (${openNow})` : ""}</button>
        <button class="btn good" id="decide-approve">Approve</button>
        <button class="btn" id="decide-reject">Reject</button>
      </div>` : bryanDecides("He approves, rejects or sends it back. Your notes go to him and the editor.")) : ""}`;
  } else {
    const option = (values, labels, current) => values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
    panel.innerHTML = `${head}
      ${d.status === "failed" ? `<div class="box"><p><strong>Failed:</strong> ${esc(d.error)}</p><p class="muted">The worker retries it next time it starts.</p></div>` : ""}
      ${d.status === "tagged" || d.status === "transcribed" ? qualityHTML(d.quality) : ""}
      ${usedInPlanHTML(d.shots)}
      ${d.summary ? `<div><h3>Summary</h3><p>${esc(d.summary)}</p></div>` : ""}
      ${d.brand_reason ? `<div><h3>Why this account</h3><p>${esc(d.brand_reason)}</p></div>` : ""}
      ${d.suggested_hook ? `<div><h3>Suggested hook</h3><p>"${esc(d.suggested_hook)}"</p></div>` : ""}
      ${d.moments.length ? `<div><h3>Best moments</h3><ul class="moments">${d.moments.map((m) =>
        `<li><button data-seek="${Number(m.start) || 0}"><strong>${mmss(m.start)}–${mmss(m.end)}</strong> ${esc(m.description)}</button></li>`).join("")}</ul></div>` : ""}
      ${d.status === "tagged" || d.status === "transcribed" ? `<div class="box"><h3>Check the tags</h3>
        <div class="row">
          <select id="tag-brand">${option(d.brands, BRAND, d.brand)}</select>
          <select id="tag-format">${option(d.formats, FORMAT, d.format)}</select>
          <label class="check"><input type="checkbox" id="tag-usable" ${d.usable === 0 ? "" : "checked"}> Usable</label>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="btn" id="tags-save">Save</button>
          <button class="btn primary" id="tags-next">Save &amp; next</button>
        </div></div>` : ""}
      <div><h3>Notes</h3>${notesHTML(d.notes, 0, false)}</div>
      ${composerHTML("How should this clip be used? e.g. \"Great hook for the kitchen reveal\"")}
      ${d.topics.length ? `<p class="muted">${d.topics.map((t) => `#${esc(t)}`).join(" ")}</p>` : ""}
      ${d.quality_notes ? `<p class="muted">Claude's quality notes: ${esc(d.quality_notes)}</p>` : ""}
      ${d.cost ? `<p class="muted">Tagging cost: ${money(d.cost)}</p>` : ""}
      ${d.transcript ? `<details><summary>Transcript</summary><p>${esc(d.transcript)}</p></details>` : ""}`;
  }
  updatePinLabel();
}

function updatePinLabel() {
  const label = $("#pin-label");
  const video = player();
  if (label) label.textContent = video && video.currentTime > 0 ? `Pin to ${mmss(video.currentTime)}` : "Pin to this moment";
}

function updateArrows() {
  const i = indexOfCurrent();
  $("#prev").disabled = i <= 0;
  $("#next").disabled = i < 0 || i >= state.list.length - 1;
}
function indexOfCurrent() {
  if (!state.current) return -1;
  return state.list.findIndex((it) => it.kind === state.current.kind && String(it.id) === String(state.current.id));
}
function step(delta) {
  const i = indexOfCurrent();
  if (i >= 0 && state.list[i + delta]) openAny(state.list[i + delta]);
}

async function nextIn(stage) {
  const done = state.current;
  await sync();
  const next = state.items.find((it) => it.stage === stage && (!state.brand || it.brand === state.brand)
    && !(it.kind === done.kind && String(it.id) === String(done.id)));
  if (next) {
    state.stage = stage;
    render();
    openItem(next);
  } else {
    closeViewer();
    render();
    toast(stage === "ideas" ? "That's every idea. Suggest more any time." : "All caught up. Nothing else to review.");
  }
}
const nextForReview = () => nextIn("review");

function goToTab(stage) {
  closeViewer(true);
  state.stage = stage;
  history.pushState(null, "", `#/tab/${stage}`);
  render();
}

function openProduction(id) {
  const item = state.items.find((i) => i.kind === "production" && i.id === id);
  if (item) { state.stage = item.stage; render(); openItem(item); } else goToTab("producing");
}

// Panel actions (event delegation, so re-rendering the panel keeps them working).
$("#panel").addEventListener("click", async (e) => {
  if (state.current?.kind === "plan") { await planPanelClick(e); return; }
  const segment = e.target.closest("[data-segment]");
  if (segment && state.detail?.segments) {
    // Play just that segment: the #t=start,end media fragment stops at the end.
    const s = state.detail.segments[Number(segment.dataset.segment)];
    player().src = `${safeSrc(s.video)}#t=${Number(s.start) || 0},${Number(s.end) || 0}`;
    player().play().catch(() => {});
    return;
  }
  const seek = e.target.closest("[data-seek]");
  if (seek && player()) {
    player().currentTime = Number(seek.dataset.seek);
    player().play().catch(() => {});
    return;
  }
  const planLink = e.target.closest("[data-open-plan-idea]");
  if (planLink) { openPlanIdea(Number(planLink.dataset.openPlanIdea)); return; }
  const id = e.target.closest("button")?.id || e.target.id;
  const d = state.detail;
  try {
    if (id === "note-add") await addNote();
    else if (id === "idea-make") {
      await api(`/api/idea/${d.id}/decision`, { decision: "make", caption: $("#caption").value });
      toast("Sent to the editor. It's in Being edited.");
      await nextIn("ideas");
    } else if (id === "idea-dismiss") {
      await api(`/api/idea/${d.id}/decision`, { decision: "dismiss" });
      toast("Dismissed.");
      await nextIn("ideas");
    } else if (id === "adopt-go") {
      const pid = Number($("#adopt-project").value);
      const arc = $("#adopt-arc").value;
      await api(`/api/idea/${d.id}/adopt`, { project_id: pid, arc_id: arc ? Number(arc) : null });
      selectProject(pid);
      toast(`Added to the ${currentProject()?.name || "project"} plan.`);
      sync();
      await openPlanIdea(d.id);
    } else if (id === "decide-changes") {
      await api(`/api/production/${d.id}/decision`, { decision: "changes", caption: $("#caption").value });
      toast("Sent back for changes. Opening the next one.");
      await nextForReview();
    } else if (id === "decide-approve") {
      await api(`/api/production/${d.id}/decision`, { decision: "approve", caption: $("#caption").value });
      toast("Approved.");
      await nextForReview();
    } else if (id === "decide-reject") {
      if (!confirm("Reject this edit? It moves to Done and won't be revised.")) return;
      await api(`/api/production/${d.id}/decision`, { decision: "reject", caption: $("#caption").value });
      toast("Rejected.");
      await nextForReview();
    } else if (id === "reopen") {
      if (!confirm(`Move "${d.title}" back to To review? You can then approve, reject or request changes again.`)) return;
      await api(`/api/production/${d.id}/decision`, { decision: "reopen" });
      toast("Back in To review.");
      await loadDetail();
      sync();
    } else if (id === "retry-edit") {
      await api(`/api/production/${d.id}/decision`, { decision: "retry", caption: $("#caption").value });
      toast("Queued for the editor again.");
      await loadDetail();
      sync();
    } else if (id === "resolve-build") {
      await api(`/api/production/${d.id}/resolve`, {});
      await loadDetail();
    } else if (id === "resolve-open") {
      const res = await api(`/api/production/${d.id}/resolve/open`, {});
      if (!res.opened) {
        navigator.clipboard?.writeText(res.folder).catch(() => {});
        toast("Folder path copied. It's on the Studio laptop.");
      }
    } else if (id === "caption-save") {
      await api(`/api/production/${d.id}/decision`, { decision: "caption", caption: $("#caption").value });
      toast("Caption saved.");
    } else if (id === "tags-save" || id === "tags-next") {
      await api(`/api/clip/${d.id}/review`, {
        brand: $("#tag-brand").value, format: $("#tag-format").value, usable: $("#tag-usable").checked,
      });
      toast("Tags saved.");
      await sync();
      if (id === "tags-next") step(1); else await loadDetail();
    }
  } catch (err) {
    toast(`Couldn't save: ${err.message}`);
  }
});

$("#panel").addEventListener("change", async (e) => {
  if (state.current?.kind === "plan") { await planPanelChange(e); return; }
  if (e.target.id === "adopt-project") fillAdoptArcs().catch(() => {});
});

async function addNote() {
  const body = $("#note-body").value.trim();
  if (!body) { $("#note-body").focus(); return; }
  const video = player();
  const at = $("#note-pin").checked && video ? video.currentTime : null;
  await api(`/api/${state.current.kind}/${state.current.id}/notes`, {
    body, at_seconds: at, remember: !!$("#note-remember")?.checked,
  });
  await loadDetail();
  sync();
  toast("Note added.");
  $("#note-body")?.focus();
}

$("#panel").addEventListener("keydown", (e) => {
  const ctrlEnter = e.key === "Enter" && (e.ctrlKey || e.metaKey);
  if (e.target.id === "note-body" && ctrlEnter) {
    e.preventDefault();
    addNote().catch((err) => toast(`Couldn't save: ${err.message}`));
  } else if (e.target.id === "comment-body" && ctrlEnter) {
    e.preventDefault();
    addComment().catch((err) => toast(`Couldn't post: ${err.message}`));
  } else if (e.target.id === "shot-new" && e.key === "Enter") {
    e.preventDefault();
    addShot().catch((err) => toast(`Couldn't add: ${err.message}`));
  } else if (e.target.id === "refine-request" && ctrlEnter) {
    e.preventDefault();
    startRefine().catch((err) => toast(`Couldn't start: ${err.message}`));
  }
});

// ── Planned idea: storyboard (left) and plan (right) ────────────────────────

async function openPlanIdea(id) {
  id = Number(id);
  $("#viewer").querySelectorAll("video, audio").forEach((m) => m.pause());
  state.current = { kind: "plan", id };
  state.detail = null;
  state.planSig = "";
  state.storySig = "";
  state.list = plan.order;
  const route = `#/plan-idea/${id}`;
  if (location.hash !== route) history.pushState(null, "", route);
  $("#viewer").hidden = false;
  $("#viewer").classList.add("plan-mode");
  document.body.style.overflow = "hidden";
  $("#player-frame").innerHTML = `<div class="placeholder">Loading the storyboard…</div>`;
  $("#panel").innerHTML = "";
  try {
    await loadPlanIdea(true);
  } catch (err) {
    $("#panel").innerHTML = `<p class="muted">Couldn't open this idea (${esc(err.message)}).</p>`;
  }
  updateArrows();
}

const JOB_DONE = { develop: "Claude refined the plan.", music: "Music shortlist ready." };

async function loadPlanIdea(force = false) {
  const id = state.current?.id;
  const d = await api(`/api/plan-idea/${id}`);
  if (state.current?.kind !== "plan" || state.current.id !== id) return;
  const before = state.detail;
  for (const k of ["develop", "music"]) {
    if (before?.jobs?.[k]?.state === "running" && d.jobs[k] && d.jobs[k].state !== "running") {
      toast(d.jobs[k].state === "done" ? JOB_DONE[k] : `${k === "develop" ? "Refine" : "Music shortlist"} failed: ${d.jobs[k].error}`);
    }
  }
  state.detail = d;
  if (d.project_id && d.project_id !== plan.pid && !plan.board) selectProject(d.project_id);
  const sig = JSON.stringify(d);
  if (!force && sig === state.planSig) return;
  const shotsSig = JSON.stringify([d.shots, d.title]);
  if (force || shotsSig !== state.storySig) {
    if (!mediaPlaying($("#player-frame"))) {
      renderStoryboard();
      state.storySig = shotsSig;
    }
  }
  if (force || !(typingIn($("#panel")) || mediaPlaying($("#panel")))) {
    renderPlanPanel();
    state.planSig = sig;
  }
}

function mediaPlaying(root) {
  return [...(root?.querySelectorAll("video, audio") || [])].some((m) => !m.paused && !m.ended);
}

function bestTake(s) {
  return s.takes.find((t) => t.verdict !== "retake") || s.takes[0] || null;
}

function frameHTML(s, i) {
  const take = bestTake(s);
  const thumb = take ? safeSrc(take.thumb) : "";
  const tags = [CAMERA[s.camera] && s.camera !== "any" ? CAMERA[s.camera] : "", FRAMING[s.framing] || ""].filter(Boolean).join(" · ");
  const media = thumb
    ? `<button class="sb-media has-take" data-play-take="${esc(s.id)}:${esc(take.id)}" aria-label="Play shot ${i + 1}">
        <img src="${esc(thumb)}" alt="" loading="lazy"><span class="play" aria-hidden="true">▶</span></button>`
    : `<button class="sb-media sb-placeholder" data-goto-shot="${esc(s.id)}" aria-label="Edit shot ${i + 1}">
        ${s.camera && s.camera !== "any" ? `<span class="sb-camera">${esc(CAMERA[s.camera])}</span>` : ""}
        <span class="sb-framing">${esc((FRAMING[s.framing] || "Shot").toUpperCase())}</span>
        ${s.kind ? `<span class="sb-kind">${esc(KIND[s.kind] || s.kind)}</span>` : ""}
        <span class="sb-desc">${esc(s.description)}</span>
        ${s.on_screen ? `<span class="sb-caption">${esc(s.on_screen)}</span>` : ""}
        ${s.line ? `<span class="sb-line">“${esc(s.line)}”</span>` : ""}
      </button>`;
  const flags = s.state === "retake" || take?.verdict === "check" ? (take?.flags || []) : [];
  return `<article class="sb-frame st-${esc(s.state)}" id="frame-${esc(s.id)}">
    <div class="sb-card">${media}
      <span class="sb-num">${i + 1}</span>
      ${s.seconds ? `<span class="sb-secs">${esc(Math.round(s.seconds * 10) / 10)}s</span>` : ""}
      <span class="sb-badge st-${esc(s.state)}">${esc(SHOT_STATE[s.state] || s.state)}</span>
    </div>
    ${thumb ? `<p class="sb-under">${tags ? `<strong>${esc(tags)}</strong> ` : ""}${esc(s.description)}</p>` : ""}
    ${flags.length ? `<ul class="sb-flags">${flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
  </article>`;
}

function renderStoryboard() {
  const d = state.detail;
  const p = d.progress;
  const secs = d.shots.reduce((sum, s) => sum + (Number(s.seconds) || 0), 0);
  $("#player-frame").innerHTML = `<div class="storyboard">
    <div class="sb-head"><h2>Storyboard</h2>
      <span class="muted">${d.shots.length} shot${d.shots.length === 1 ? "" : "s"}${secs ? ` · about ${Math.round(secs)}s` : ""}</span>
      ${p.total ? `<span class="sb-progress">${pbarHTML(p)} ${esc(p.got)}/${esc(p.total)} got</span>` : ""}</div>
    ${d.shots.length ? `<div class="sb-strip">${d.shots.map(frameHTML).join("")}</div>`
      : `<div class="sb-empty"><p>No shots planned yet.</p><p class="muted">Add shots on the right, or use Refine with Claude to storyboard it.</p></div>`}
  </div>`;
}

function playTake(shotId, takeId) {
  const s = state.detail?.shots.find((x) => x.id === shotId);
  const t = s?.takes.find((x) => x.id === takeId);
  const src = safeSrc(t?.video);
  const media = $(`#frame-${shotId} .sb-media`);
  if (!src || !media) return;
  $("#player-frame").querySelectorAll("video").forEach((v) => v.pause());
  const start = Number(t.start) || 0, end = Number(t.end) || 0;
  const wrap = document.createElement("div");
  wrap.className = "sb-media playing";
  wrap.innerHTML = `<video src="${esc(src)}#t=${start}${end > start ? `,${end}` : ""}" controls autoplay playsinline></video>`;
  media.replaceWith(wrap);
  wrap.querySelector("video").play().catch(() => {});
  wrap.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

$("#player-frame").addEventListener("click", (e) => {
  if (state.current?.kind !== "plan") return;
  const play = e.target.closest("[data-play-take]");
  if (play) {
    const [sid, tid] = play.dataset.playTake.split(":").map(Number);
    playTake(sid, tid);
    return;
  }
  const go = e.target.closest("[data-goto-shot]");
  if (go) {
    const row = document.getElementById(`shot-${Number(go.dataset.gotoShot)}`);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.remove("flash");
    void row.offsetWidth;
    row.classList.add("flash");
  }
});

function takeHTML(s, t) {
  return `<div class="take">
    <button class="take-thumb" data-play-take="${esc(s.id)}:${esc(t.id)}" title="Play this take in the storyboard" aria-label="Play take">
      <img src="${esc(safeSrc(t.thumb))}" alt="" loading="lazy"><span class="play" aria-hidden="true">▶</span></button>
    <div class="take-info">
      <div class="row tight">${verdictChip(t.verdict)}<span class="muted small">${t.source === "auto" ? "Matched automatically" : "Added by hand"}</span></div>
      <div class="small">${esc(t.title)} · ${mmss(t.start)}–${t.end != null ? mmss(t.end) : "end"}</div>
      ${t.flags?.length ? `<ul class="take-flags">${t.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    </div>
    <button class="icon-btn danger" data-take-remove="${esc(t.id)}" title="Remove this take" aria-label="Remove take">×</button>
  </div>`;
}

function shotRowHTML(s, i, n) {
  return `<li class="shot-row st-${esc(s.state)}" id="shot-${esc(s.id)}" data-shot="${esc(s.id)}" data-saved-scope>
    <div class="shot-top">
      <span class="shot-num">${i + 1}</span>
      <label class="filmed-toggle" title="Tick when it's been filmed on site"><input type="checkbox" data-shot-filmed ${s.filmed_at ? "checked" : ""}><span>Filmed</span></label>
      <span class="badge st-${esc(s.state)}">${esc(SHOT_STATE[s.state] || s.state)}</span>
      <span class="saved" aria-live="polite"></span>
      <span class="shot-tools">
        <button class="icon-btn" data-shot-move="-1" ${i === 0 ? "disabled" : ""} title="Move up" aria-label="Move shot up">↑</button>
        <button class="icon-btn" data-shot-move="1" ${i === n - 1 ? "disabled" : ""} title="Move down" aria-label="Move shot down">↓</button>
        <button class="icon-btn danger" data-shot-delete title="Delete shot" aria-label="Delete shot">×</button>
      </span>
    </div>
    ${s.filmed_at ? `<p class="muted small">Ticked${s.filmed_by ? ` by ${esc(s.filmed_by)}` : ""} ${esc(ago(s.filmed_at))}</p>` : ""}
    <textarea data-shot-field="description" rows="2" maxlength="1000" aria-label="What the camera sees">${esc(s.description)}</textarea>
    <div class="shot-selects">
      <select data-shot-field="camera" aria-label="Camera">${options(Object.keys(CAMERA), CAMERA, s.camera, "Camera…")}</select>
      <select data-shot-field="framing" aria-label="Framing">${options(Object.keys(FRAMING), FRAMING, s.framing, "Framing…")}</select>
      <select data-shot-field="kind" aria-label="Kind of shot">${options(Object.keys(KIND), KIND, s.kind, "Kind…")}</select>
      <span class="num-field"><input type="number" min="0.5" max="120" step="0.5" data-shot-field="seconds" value="${esc(s.seconds ?? "")}" aria-label="Seconds in the cut"> s</span>
    </div>
    <label class="inline-field"><span>Says</span><input data-shot-field="line" maxlength="1000" value="${esc(s.line)}" placeholder="Spoken line (optional)"></label>
    <label class="inline-field"><span>On screen</span><input data-shot-field="on_screen" maxlength="300" value="${esc(s.on_screen)}" placeholder="Text on screen (optional)"></label>
    <label class="inline-field"><span>Why</span><input data-shot-field="why" maxlength="500" value="${esc(s.why)}" placeholder="What this shot does for the story"></label>
    <div class="takes">${s.takes.map((t) => takeHTML(s, t)).join("")}
      <button class="btn small" data-attach>${s.takes.length ? "Attach more footage" : "Attach footage"}</button></div>
  </li>`;
}

function planMusicHTML(d) {
  const m = d.music || {};
  const job = d.jobs?.music;
  const running = job?.state === "running";
  const desc = [m.mood, m.tempo_bpm ? `about ${Math.round(m.tempo_bpm)} BPM` : "", m.search ? `search “${m.search}”` : ""].filter(Boolean).map(esc).join(" · ");
  const chosenId = m.chosen?.recording_id;
  let body = "";
  if (m.chosen) body += `<div class="chosen">♪ Picked: <strong>${esc(m.chosen.title)}</strong> by ${esc(m.chosen.artist)}</div>`;
  if (m.source === "instagram" && m.note) body += `<p class="muted">${esc(m.note)}</p>`;
  if (m.source === "epidemic") {
    if (m.shortlist?.length) {
      body += `<ul class="tracks">${m.shortlist.map((t, i) => {
        const on = chosenId != null && t.recording_id === chosenId;
        const preview = httpsUrl(t.preview_url), page = httpsUrl(t.page_url);
        return `<li class="track${on ? " on" : ""}">
          <div class="track-head"><div><strong>${esc(t.title)}</strong> <span class="muted">${esc(t.artist)}${t.bpm ? ` · ${esc(Math.round(t.bpm))} BPM` : ""}</span></div>
            <button class="btn small${on ? " good" : ""}" data-music-pick="${i}">${on ? "Unpick" : "Pick"}</button></div>
          ${t.why ? `<p class="muted small">${esc(t.why)}</p>` : ""}
          ${preview ? `<audio controls preload="none" src="${esc(preview)}"></audio>` : ""}
          ${page ? `<a class="small" href="${esc(page)}" target="_blank" rel="noopener noreferrer">Open on Epidemic Sound ↗</a>` : ""}
        </li>`;
      }).join("")}</ul>`;
    } else if (m.note) body += `<p class="muted">${esc(m.note)}</p>`;
  }
  const status = running ? `<span class="muted small"><span class="spin"></span> Looking for tracks. About a minute.</span>`
    : job?.state === "failed" ? `<span class="error-text small">Couldn't shortlist: ${esc(job.error)}</span>` : "";
  const again = m.shortlist?.length || m.note;
  return `<div class="field"><h3>Music</h3>
    <p>${desc || `<span class="muted">No direction yet. Refine with Claude to get one.</span>`}</p>${body}
    <div class="row" style="margin-top:8px"><button class="btn" id="music-shortlist" ${running ? "disabled" : ""}
      title="Urban Makers: Claude searches Epidemic Sound for a few tracks to try. Personal: a note on what to pick in Instagram. Costs a few cents.">${running ? "Shortlisting…" : again ? "Shortlist again" : "Shortlist music"}</button>${status}</div>
  </div>`;
}

function refineHTML(d) {
  const job = d.jobs?.develop;
  const running = job?.state === "running";
  return `<div class="box refine"><h3>Refine with Claude</h3>
    <textarea id="refine-request" rows="2" maxlength="4000" ${running ? "disabled" : ""} placeholder="What should change? e.g. “make the hook punchier”, “add a before/after”. Leave empty to just complete the plan."></textarea>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="refine-go" ${running ? "disabled" : ""} title="About a minute; costs a few cents">${running ? "Refining…" : "Refine with Claude"}</button>
      ${running ? `<span class="muted small"><span class="spin"></span> Claude is reworking the plan.</span>` : ""}</div>
    <p class="muted small">Claude rewrites the hook, text and storyboard and reads the comments. Shots already filmed or with footage are always kept; unfilmed ones may be reworded or replaced.</p>
    ${job?.state === "done" && job.result ? `<p class="refine-result"><strong>Last refine${job.finished ? ` (${esc(ago(job.finished))})` : ""}:</strong> ${esc(job.result)}</p>` : ""}
    ${job?.state === "failed" ? `<p class="error-text">Refine failed: ${esc(job.error)}</p>` : ""}
  </div>`;
}

function commentsHTML(notes) {
  return `<div class="field"><h3>Comments${notes.length ? ` (${notes.length})` : ""}</h3>
    ${notes.length ? `<ul class="notes">${notes.map((n) => `<li><div class="note-by"><strong>${esc(n.author || "Someone")}</strong> <span class="muted small">${esc(ago(n.created_at))}</span></div>
      <span class="note-body">${esc(n.body)}</span></li>`).join("")}</ul>` : `<p class="muted">No comments yet.</p>`}
    <textarea id="comment-body" rows="2" maxlength="4000" placeholder="Add a comment for the team (Ctrl+Enter to post)" style="margin-top:8px"></textarea>
    <div class="row" style="margin-top:8px"><button class="btn" id="comment-add" style="margin-left:auto">Comment</button></div>
  </div>`;
}

function renderPlanPanel() {
  const d = state.detail;
  const panel = $("#panel");
  const owner = isOwner();
  const v = d.votes || { up: [], down: [], mine: 0 };
  const voters = [v.up.length ? `Keen: ${v.up.join(", ")}` : "", v.down.length ? `Not keen: ${v.down.join(", ")}` : ""].filter(Boolean).join(" · ") || "No votes yet";
  const field = (key, label, ph, rows = 2, max = 1000) => `<div class="field" data-saved-scope>
    <h3><label for="f-${key}">${esc(label)}</label> <span class="saved" aria-live="polite"></span></h3>
    <textarea id="f-${key}" data-idea-field="${key}" rows="${rows}" maxlength="${max}" placeholder="${esc(ph)}">${esc(d[key])}</textarea></div>`;
  const decision = owner
    ? `<div class="row">
        <div class="seg decision-seg" role="group" aria-label="Decision">${["go", "parked", "proposed"].map((k) =>
          `<button data-plan-decision="${k}" class="${d.decision === k ? `on ${k}` : ""}" aria-pressed="${d.decision === k}">${{ go: "Go", parked: "Park", proposed: "Undecided" }[k]}</button>`).join("")}</div>
        <button class="btn small ghost danger" data-plan-decision="dismissed" title="Take it off the board">Dismiss…</button></div>`
    : `<p><span class="chip ${esc(d.decision)}">${esc(DECISION[d.decision] || d.decision)}</span> <span class="muted small">Bryan decides Go or Park. Vote to tell him what you think.</span></p>`;
  const made = d.status === "made";
  const makeBar = made
    ? `<div class="decision row"><span><strong>In the edit.</strong></span>
        ${d.production_id ? `<button class="btn" data-open-production="${esc(d.production_id)}">Open the edit</button>` : ""}
        <button class="btn" data-goto-tab="producing">Open Being edited</button></div>`
    : owner ? `<div class="decision row"><button class="btn primary" id="plan-make" ${d.can_make ? "" : "disabled"}>Make this</button>
        <span class="muted small">${d.can_make ? "Sends it to the editor, cut from the best take of each shot." : "Needs footage for at least one shot."}</span></div>`
      : `<div class="decision row"><span class="muted small">Bryan sends it to the editor once the footage is in.</span></div>`;
  const scroll = panel.scrollTop;
  keepDrafts(["comment-body", "refine-request", "shot-new"], () => {
    panel.innerHTML = `
      <div class="field" data-saved-scope>
        <h3>Planned post <span class="saved" aria-live="polite"></span></h3>
        <input class="title-input" data-idea-field="title" maxlength="300" value="${esc(d.title)}" aria-label="Title">
        <p class="muted small" style="margin-top:6px">${d.by ? `Added by ${esc(d.by)} · ` : ""}updated ${esc(ago(d.updated))} · Changes save when you click away.</p>
      </div>
      <div class="meta-grid" data-saved-scope>
        <label>Arc<select data-idea-field="arc_id">${`<option value="">Unsorted</option>` + d.arcs.map((a) => `<option value="${esc(a.id)}" ${a.id === d.arc_id ? "selected" : ""}>${esc(a.title)}</option>`).join("")}</select></label>
        <label>Account<select data-idea-field="brand">${options(["urban_makers", "personal"], BRAND, d.brand)}</select></label>
        <label>Format<select data-idea-field="format">${options(PLAN_FORMATS, FORMAT, d.format)}</select></label>
        <label>Length<span class="num-field"><input type="number" min="3" max="180" step="1" data-idea-field="est_seconds" value="${esc(Math.round(d.est_seconds || 30))}"> s</span></label>
        <span class="saved" aria-live="polite"></span>
      </div>
      <div class="box"><h3>${owner ? "Your call" : "Bryan's call"}</h3>${decision}
        <div class="votes">
          <button class="vote${v.mine === 1 ? " on" : ""}" data-vote="1" aria-pressed="${v.mine === 1}" title="Worth making">👍 ${v.up.length}</button>
          <button class="vote${v.mine === -1 ? " on down" : ""}" data-vote="-1" aria-pressed="${v.mine === -1}" title="Not worth it">👎 ${v.down.length}</button>
          <span class="muted small">${esc(voters)}</span>
        </div></div>
      ${field("hook", "Hook (first 3 seconds)", "The line or moment that stops the scroll")}
      ${field("purpose", "Who it's for and why", "Who should watch, and what they should think, feel or do after")}
      ${field("cta", "Call to action", "e.g. Save this for your reno", 1, 500)}
      <div class="field"><h3>Shots · ${esc(d.progress.got)}/${esc(d.progress.total)} got</h3>
        ${d.shots.length ? `<ol class="shot-list">${d.shots.map((s, i) => shotRowHTML(s, i, d.shots.length)).join("")}</ol>` : `<p class="muted">No shots yet.</p>`}
        <div class="row add-shot"><input id="shot-new" class="grow" maxlength="1000" placeholder="Describe a new shot, e.g. Close-up: grout going into the corner">
          <button class="btn" id="shot-add">+ Add shot</button></div>
      </div>
      ${refineHTML(d)}
      ${planMusicHTML(d)}
      ${field("caption", "Caption", "The Instagram caption, with hashtags", 4, 5000)}
      ${commentsHTML(d.notes || [])}
      ${makeBar}`;
  });
  panel.scrollTop = scroll;
}

function flashSaved(el) {
  const s = el.closest("[data-saved-scope]")?.querySelector(".saved");
  if (!s) return;
  s.textContent = "Saved";
  s.classList.add("show");
  clearTimeout(s.timer);
  s.timer = setTimeout(() => s.classList.remove("show"), 1600);
}

async function planPanelChange(e) {
  const el = e.target;
  const d = state.detail;
  try {
    if (el.dataset.ideaField) {
      const key = el.dataset.ideaField;
      let value = el.value;
      if (key === "arc_id") value = value ? Number(value) : null;
      else if (key === "est_seconds") {
        value = Number(value);
        if (!(value >= 3 && value <= 180)) { toast("Length must be between 3 and 180 seconds."); return; }
      } else if (key === "title" && !value.trim()) { el.value = d.title; toast("The title can't be empty."); return; }
      await api(`/api/plan-idea/${d.id}`, { [key]: value });
      flashSaved(el);
    } else if (el.dataset.shotField != null || el.dataset.shotFilmed != null) {
      const sid = Number(el.closest("[data-shot]").dataset.shot);
      let body;
      if (el.dataset.shotFilmed != null) body = { filmed: el.checked };
      else {
        const key = el.dataset.shotField;
        let value = el.value;
        if (["camera", "framing", "kind"].includes(key)) value = value || null;
        else if (key === "seconds") {
          value = Number(value);
          if (!(value > 0 && value <= 120)) { toast("Seconds must be between 0 and 120."); return; }
        } else if (key === "description" && !value.trim()) { toast("Describe the shot (or delete it)."); return; }
        body = { [key]: value };
      }
      await api(`/api/shot/${sid}`, body);
      flashSaved(el);
    } else return;
    await loadPlanIdea();
  } catch (err) {
    toast(`Couldn't save: ${err.message}`);
  }
}

async function addShot() {
  const box = $("#shot-new");
  const description = box.value.trim();
  if (!description) { box.focus(); return; }
  await api(`/api/plan-idea/${state.detail.id}/shots`, { description });
  box.value = "";
  await loadPlanIdea(true);
  $("#shot-new")?.focus();
  toast("Shot added.");
}

async function addComment() {
  const box = $("#comment-body");
  const body = box.value.trim();
  if (!body) { box.focus(); return; }
  await api(`/api/idea/${state.detail.id}/notes`, { body });
  box.value = "";
  await loadPlanIdea(true);
  toast("Comment posted.");
}

async function startRefine() {
  const d = state.detail;
  await api(`/api/plan-idea/${d.id}/develop`, { request: $("#refine-request").value.trim() });
  $("#refine-request").value = "";
  d.jobs.develop = { state: "running" };
  renderPlanPanel();
  toast("Claude is refining it. About a minute.");
}

async function planPanelClick(e) {
  const t = e.target.closest("button");
  if (!t) return;
  const d = state.detail;
  const ds = t.dataset;
  const shotId = Number(t.closest("[data-shot]")?.dataset.shot);
  try {
    if (ds.playTake) {
      const [sid, tid] = ds.playTake.split(":").map(Number);
      playTake(sid, tid);
      return;
    }
    if (ds.attach != null) { openPicker(shotId); return; }
    if (ds.gotoTab) { goToTab(ds.gotoTab); return; }
    if (ds.openProduction) { openProduction(Number(ds.openProduction)); return; }
    if (t.id === "shot-add") { await addShot(); return; }
    if (t.id === "comment-add") { await addComment(); return; }
    if (t.id === "refine-go") { await startRefine(); return; }
    if (ds.planDecision) {
      const k = ds.planDecision;
      if (k === "dismissed" && !confirm(`Dismiss "${d.title}"? It comes off the board.`)) return;
      await api(`/api/plan-idea/${d.id}/decision`, { decision: k });
      if (k === "dismissed") { toast("Dismissed."); closeViewer(); return; }
      toast({ go: "Marked Go.", parked: "Parked.", proposed: "Back to undecided." }[k]);
    } else if (ds.vote) {
      const vote = Number(ds.vote);
      await api(`/api/plan-idea/${d.id}/vote`, { vote: d.votes.mine === vote ? 0 : vote });
    } else if (t.id === "music-shortlist") {
      await api(`/api/plan-idea/${d.id}/music`, {});
      d.jobs.music = { state: "running" };
      renderPlanPanel();
      return;
    } else if (ds.musicPick != null) {
      const track = d.music.shortlist[Number(ds.musicPick)];
      const on = d.music.chosen?.recording_id === track.recording_id;
      await api(`/api/plan-idea/${d.id}/music/choose`, { recording_id: on ? null : track.recording_id });
      toast(on ? "Unpicked." : `Picked ${track.title}.`);
    } else if (ds.shotMove) {
      await api(`/api/shot/${shotId}`, { move: Number(ds.shotMove) });
    } else if (ds.shotDelete != null) {
      const s = d.shots.find((x) => x.id === shotId);
      if (s.takes.length && !confirm(`This shot has ${s.takes.length} take${s.takes.length > 1 ? "s" : ""} attached. Delete the shot anyway? The clips stay in the library.`)) return;
      await api(`/api/shot/${shotId}`, { delete: true });
      toast("Shot deleted.");
    } else if (ds.takeRemove) {
      await api(`/api/take/${Number(ds.takeRemove)}/remove`, {});
      toast("Take removed. The clip stays in the library.");
    } else if (t.id === "plan-make") {
      const res = await api(`/api/idea/${d.id}/decision`, { decision: "make", caption: $("#f-caption")?.value ?? d.caption });
      toast("Sent to the editor. It'll show up in Being edited.");
      sync();
      if (res.production_id) d.production_id = res.production_id;
    } else return;
    await loadPlanIdea(true);
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
  }
}

// Attach footage: pick a clip for a shot.
let pickerTimer;
function openPicker(shotId) {
  const d = state.detail;
  const s = d.shots.find((x) => x.id === shotId);
  const n = d.shots.indexOf(s) + 1;
  openModal(`<h2 id="modal-title">Attach footage</h2>
    <p class="muted">To shot ${n}: ${esc(s.description)}</p>
    <input type="search" id="picker-q" class="search" placeholder="Search clips by title or summary" aria-label="Search clips">
    <div id="picker-results"><p class="muted">Loading clips…</p></div>`, async (e) => {
    const btn = e.target.closest("[data-pick-clip]");
    if (!btn || btn.disabled) return;
    await api(`/api/shot/${shotId}/takes`, { clip_id: Number(btn.dataset.pickClip) });
    closeModal();
    toast("Footage attached.");
    await loadPlanIdea(true);
  }, true);
  const search = () => loadPicker(d.project_id, s).catch((err) => { $("#picker-results").innerHTML = `<p class="error-text">${esc(err.message)}</p>`; });
  $("#picker-q").addEventListener("input", () => { clearTimeout(pickerTimer); pickerTimer = setTimeout(search, 250); });
  search();
}
async function loadPicker(projectId, shot) {
  const q = $("#picker-q")?.value.trim() || "";
  const data = await api(`/api/project/${projectId}/clips?q=${encodeURIComponent(q)}`);
  if (!$("#picker-results") || ($("#picker-q")?.value.trim() || "") !== q) return;
  const attached = new Set(shot.takes.map((t) => t.clip_id));
  const tile = (c) => `<button class="pick" data-pick-clip="${esc(c.id)}" ${attached.has(c.id) ? "disabled" : ""}>
    <img src="${esc(safeSrc(c.thumb))}" alt="" loading="lazy">
    <span class="pick-chips">${c.verdict ? verdictChip(c.verdict) : ""}${attached.has(c.id) ? `<span class="chip">Attached</span>` : ""}</span>
    <span class="pick-text"><span class="pick-title">${esc(c.title)}</span>
      <span class="pick-meta">${c.duration ? esc(mmss(c.duration)) : ""}${c.recorded_at ? ` · ${esc(shortDate(c.recorded_at))}` : ""}</span></span>
  </button>`;
  const mine = data.clips.filter((c) => c.in_project), rest = data.clips.filter((c) => !c.in_project);
  $("#picker-results").innerHTML = !data.clips.length ? `<p class="muted">No clips match.</p>`
    : (mine.length ? `<h3>In this project</h3><div class="picker-grid">${mine.map(tile).join("")}</div>` : "")
      + (rest.length ? `<h3>${mine.length ? "Other recent clips" : "Recent clips"}</h3><div class="picker-grid">${rest.map(tile).join("")}</div>` : "");
}

// ── On-site checklist ───────────────────────────────────────────────────────

async function openChecklist(pid) {
  if (!pid) return;
  check.pid = Number(pid);
  check.keep = new Set();
  check.data = null;
  const route = `#/checklist/${check.pid}`;
  if (location.hash !== route) history.pushState(null, "", route);
  if (!$("#viewer").hidden) closeViewer(true);
  $("#checklist").hidden = false;
  document.body.style.overflow = "hidden";
  $("#checklist").innerHTML = `<p class="muted cl-loading">Loading the shot list…</p>`;
  try {
    await loadChecklist();
  } catch (err) {
    $("#checklist").innerHTML = `<div class="cl-head"><button class="btn" data-cl-back>‹ Plan</button></div><p class="error-text cl-loading">Couldn't load it: ${esc(err.message)}</p>`;
  }
}
function closeChecklist(push = true) {
  $("#checklist").hidden = true;
  $("#checklist").innerHTML = "";
  if ($("#viewer").hidden && $("#modal").hidden) document.body.style.overflow = "";
  if (!push) return;
  if (check.pid && check.pid !== plan.pid) selectProject(check.pid);
  state.stage = "plan";
  history.pushState(null, "", `#/plan/${plan.pid}`);
  render();
  loadPlan(true).catch(() => {});
}
async function loadChecklist() {
  const pid = check.pid;
  const data = await api(`/api/project/${pid}/checklist`);
  if (pid !== check.pid || check.busy || $("#checklist").hidden) return;
  check.data = data;
  renderChecklist();
}

const CL_ICON = { needed: "", filmed: "✓", got: "✓", retake: "↻" };

function renderChecklist() {
  const data = check.data;
  const el = $("#checklist");
  const scroll = el.scrollTop;
  const total = { needed: 0, filmed: 0, got: 0, retake: 0, total: 0 };
  for (const g of data.groups) for (const s of g.shots) { total[s.state] += 1; total.total += 1; }
  const camOk = (s) => !check.camera || !s.camera || s.camera === "any" || s.camera === check.camera;
  const wanted = (s) => camOk(s) && (!check.onlyNeeded || s.state === "needed" || s.state === "retake" || check.keep.has(s.id));
  const groups = data.groups.map((g) => ({ ...g, list: g.shots.filter(wanted) })).filter((g) => g.list.length);
  let lastArc = null;
  const body = groups.map((g) => {
    const arc = g.arc !== lastArc ? `<div class="cl-arc">${esc(g.arc)}</div>` : "";
    lastArc = g.arc;
    return `${arc}<section class="cl-group">
      <div class="cl-idea"><button class="cl-idea-title" data-cl-open="${esc(g.idea_id)}">${esc(g.title)}</button>
        ${g.decision === "go" ? `<span class="chip go">Go</span>` : ""}
        <span class="muted small">${esc(g.progress.got)}/${esc(g.progress.total)} got</span></div>
      <ul class="cl-shots">${g.list.map((s) => {
        const take = s.takes.find((t) => t.verdict === "retake") || null;
        const label = [s.camera ? CAMERA[s.camera] : "", FRAMING[s.framing] ? FRAMING[s.framing].toUpperCase() : "", KIND[s.kind] || "", s.seconds ? `${Math.round(s.seconds * 10) / 10}s` : ""].filter(Boolean).join(" · ");
        return `<li><button class="cl-shot st-${esc(s.state)}" data-cl-shot="${esc(s.id)}" aria-pressed="${!!s.filmed_at}">
          <span class="cl-icon" aria-hidden="true">${CL_ICON[s.state] || ""}</span>
          <span class="cl-text">
            ${label ? `<span class="cl-label">${esc(label)}</span>` : ""}
            <span class="cl-desc">${esc(s.description)}</span>
            ${s.line ? `<span class="cl-line">“${esc(s.line)}”</span>` : ""}
            ${s.on_screen ? `<span class="cl-onscreen">On screen: ${esc(s.on_screen)}</span>` : ""}
            ${s.state === "retake" && take?.flags?.length ? `<span class="cl-flags">Retake: ${esc(take.flags.join("; "))}</span>` : ""}
          </span>
          <span class="cl-state">${esc({ needed: "Tap when filmed", filmed: s.filmed_by ? `Filmed by ${s.filmed_by}` : "Filmed", got: "Got it", retake: s.filmed_at ? "Retake (ticked)" : "Retake" }[s.state])}</span>
        </button></li>`;
      }).join("")}</ul></section>`;
  }).join("");
  el.innerHTML = `<div class="cl-head">
      <div class="cl-top"><button class="btn" data-cl-back aria-label="Back to the plan">‹ Plan</button>
        <div class="cl-title"><strong>Shot list</strong><span class="muted small">${esc(data.project.name)}</span></div></div>
      <div class="cl-progress">${pbarHTML(total)}<span><strong>${total.got}</strong> of ${total.total} got · ${total.filmed} waiting for upload${total.retake ? ` · <span class="bad-text">${total.retake} to retake</span>` : ""}</span></div>
      <div class="cl-filters">
        <div class="seg cl-cams" role="group" aria-label="Camera">${[["", "All"], ["iphone", "iPhone"], ["zfc", "Z fc"], ["gopro", "GoPro"]].map(([k, l]) =>
          `<button data-cl-camera="${k}" class="${check.camera === k ? "on" : ""}" aria-pressed="${check.camera === k}">${l}</button>`).join("")}</div>
        <label class="check cl-only"><input type="checkbox" id="cl-only" ${check.onlyNeeded ? "checked" : ""}> Still needed only</label>
      </div>
    </div>
    <div class="cl-body">${body || `<div class="cl-done"><p><strong>${total.total ? "Nothing left to film here." : "No shots planned yet."}</strong></p>
      <p class="muted">${total.total ? (check.onlyNeeded || check.camera ? "Untick “Still needed only” or pick All to see everything." : "") : "Ideas need a storyboard before they show up here. Parked ideas and ideas already in the edit are left out."}</p></div>`}
      <p class="muted small cl-foot">Tap a shot when it's filmed. It turns into “Got it” once the footage is uploaded and matched. Parked ideas and ideas already in the edit aren't listed.</p></div>`;
  el.scrollTop = scroll;
}

async function toggleShot(id) {
  const s = check.data.groups.flatMap((g) => g.shots).find((x) => x.id === id);
  if (!s) return;
  if (s.state === "got") { toast("We already have footage for this shot."); return; }
  const filmed = !s.filmed_at;
  s.filmed_at = filmed ? new Date().toISOString() : null;
  s.filmed_by = filmed ? state.me?.name : null;
  if (s.state === "needed" || s.state === "filmed") s.state = filmed ? "filmed" : "needed";
  check.keep.add(id);
  renderChecklist();
  check.busy += 1;
  try {
    await api(`/api/shot/${id}`, { filmed });
  } catch (err) {
    toast(`Couldn't save: ${err.message}`);
  } finally {
    check.busy -= 1;
  }
  if (!check.busy) await loadChecklist().catch(() => {});
}

$("#checklist").addEventListener("click", (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.clBack != null) closeChecklist();
  else if (t.dataset.clShot) toggleShot(Number(t.dataset.clShot));
  else if (t.dataset.clCamera != null) {
    check.camera = t.dataset.clCamera;
    store("studio.camera", check.camera);
    renderChecklist();
  } else if (t.dataset.clOpen) {
    closeChecklist(false);
    openPlanIdea(Number(t.dataset.clOpen));
  }
});
$("#checklist").addEventListener("change", (e) => {
  if (e.target.id !== "cl-only") return;
  check.onlyNeeded = e.target.checked;
  check.keep = new Set();
  store("studio.onlyNeeded", check.onlyNeeded ? "1" : "0");
  renderChecklist();
});

// ── Keys, buttons, routes ───────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName);
  if (!$("#modal").hidden) { if (e.key === "Escape") closeModal(); return; }
  if (e.key === "Escape" && !$("#account-menu").hidden) { setAccountMenu(false); $("#account-btn").focus(); return; }
  if (!$("#checklist").hidden) { if (e.key === "Escape" && !typing) closeChecklist(); return; }
  if ($("#viewer").hidden) return;
  if (e.key === "Escape") { if (typing) e.target.blur(); else closeViewer(); return; }
  if (typing) return;
  if (e.key === "j" || e.key === "J") step(1);
  else if (e.key === "k" || e.key === "K") step(-1);
  else if (e.key === " " && player()) { e.preventDefault(); player().paused ? player().play() : player().pause(); }
});

$("#close").addEventListener("click", () => closeViewer());
$("#suggest").addEventListener("click", async () => {
  try {
    await api("/api/ideas/generate", { focus: $("#idea-focus").value });
    state.ideasJob = { running: true };
    renderToolbar();
  } catch (err) {
    toast(`Couldn't start: ${err.message}`);
  }
});
$("#prev").addEventListener("click", () => step(-1));
$("#next").addEventListener("click", () => step(1));

// Deep links (#/production/12, #/plan-idea/4, #/checklist/1) and the back button.
async function routeFromHash() {
  const hash = location.hash;
  const closeOverlays = () => {
    if (!$("#viewer").hidden) closeViewer(true);
    if (!$("#checklist").hidden) closeChecklist(false);
  };
  // #/tab/library etc.: links in Telegram digests open a tab directly.
  const tab = hash.match(/^#\/tab\/(\w+)$/);
  if (tab && STAGES.some((s) => s.key === tab[1])) {
    closeOverlays();
    state.stage = tab[1];
    render();
    return;
  }
  const project = hash.match(/^#\/plan\/(\d+)$/);
  if (project) {
    closeOverlays();
    if (Number(project[1]) !== plan.pid) selectProject(Number(project[1]));
    state.stage = "plan";
    render();
    return;
  }
  const planIdea = hash.match(/^#\/plan-idea\/(\d+)$/);
  if (planIdea && state.cloud) {  // planned ideas open on the online planner now
    closeOverlays();
    state.stage = "plan";
    render();
    return;
  }
  if (planIdea) {
    if (!$("#checklist").hidden) closeChecklist(false);
    if (state.current?.kind === "plan" && state.current.id === Number(planIdea[1])) return;
    if (state.stage !== "plan") { state.stage = "plan"; render(); }
    openPlanIdea(Number(planIdea[1]));
    return;
  }
  const list = hash.match(/^#\/checklist\/(\d+)$/);
  if (list) {
    if (!$("#checklist").hidden && check.pid === Number(list[1])) return;
    openChecklist(Number(list[1]));
    return;
  }
  const m = hash.match(/^#\/(clip|production|inbox|idea)\/(.+)$/);
  if (!m) { closeOverlays(); if (state.stage === "plan") render(); return; }
  if (!$("#checklist").hidden) closeChecklist(false);
  const id = decodeURIComponent(m[2]);
  const item = state.items.find((i) => i.kind === m[1] && String(i.id) === id);
  if (item) {
    state.stage = item.stage;
    render();
    openItem(item);
  }
}
window.addEventListener("popstate", routeFromHash);

// While Claude works on something, check every few seconds instead of every ten.
setInterval(async () => {
  try {
    const d = state.detail;
    if (state.current?.kind === "plan" && d?.jobs && ["develop", "music"].some((k) => d.jobs[k]?.state === "running")) await loadPlanIdea();
    if (state.stage === "plan" && plan.board?.jobs?.organise?.state === "running" && $("#viewer").hidden) await loadPlan();
  } catch { /* the next tick tries again */ }
}, JOB_POLL_MS);

async function init() {
  try {
    state.me = await api("/api/me");
  } catch (err) {
    if (err.message !== "signed out") $("#updated").textContent = `Can't reach the laptop (${err.message})`;
    return;
  }
  renderAccount();
  await loadProjects().catch(() => {});
  await sync();
  await routeFromHash();
  setInterval(sync, POLL_MS);
}
init();
