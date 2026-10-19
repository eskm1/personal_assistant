// Shared bits: the Supabase client, labels, helpers, the modal and toast, stills
// (signed URLs from the private "thumbs" bucket) and background jobs.

export const cfg = window.STUDIO_CONFIG || {};

// Invite and reset links land here with the session in the URL. Read what kind of
// link it was before supabase-js consumes (and clears) it.
export const landing = (() => {
  const params = new URLSearchParams(location.hash.replace(/^#\/?/, "") + "&" + location.search.slice(1));
  return {
    type: params.get("type") || "",
    error: params.get("error_description") || params.get("error") || "",
    errorCode: params.get("error_code") || "",
    hasToken: params.has("access_token") || params.has("code"),
  };
})();

export const sb = window.supabase && cfg.supabaseUrl && cfg.supabaseAnonKey
  ? window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "implicit" },
  })
  : null;

// ── Labels ──────────────────────────────────────────────────────────────────

export const BRAND = { personal: "Personal", urban_makers: "Urban Makers" };
// The accounts the team plans for. Personal is on hold: everything is Urban Makers for
// now, so account pickers and labels are left out. Add "personal" back to bring it back
// (with STUDIO_BRANDS=urban_makers,personal on the Studio machine).
export const ACCOUNTS = ["urban_makers"];
export const oneBrand = ACCOUNTS.length < 2;
export const FORMAT = { talking_head: "Talking head", day_in_the_life: "Day in the life", compilation: "Compilation", carousel: "Carousel" };
// Reels the team films, and carousels (slides; no filming).
export const PLAN_FORMATS = ["talking_head", "day_in_the_life", "compilation", "carousel"];
export const REEL_FORMATS = ["talking_head", "day_in_the_life", "compilation"];
export const isCarousel = (i) => i?.format === "carousel";
// A carousel's slides (ideas.slides): their roles and where each slide's picture comes from.
export const SLIDE_ROLE = { cover: "Cover", point: "Point", ask: "Ask" };
export const PICTURE = { text: "Text only", clip: "Still from footage", photo: "Photo", concept: "AI concept image" };
export const FRAMING = { wide: "Wide", medium: "Medium", close: "Close-up", detail: "Detail", pov: "POV", overhead: "Overhead" };
export const KIND = { talking: "Talking to camera", broll: "B-roll", detail: "Detail", before_after: "Before / after", reaction: "Reaction", screen: "Screen" };
export const CAMERA = { iphone: "iPhone", zfc: "Z fc", gopro: "GoPro", any: "Any camera" };
// The cameras by their full names, in the order the shoot plan groups shots.
export const CAMERA_LONG = { iphone: "iPhone 16 Pro", zfc: "Nikon Z fc", gopro: "GoPro", any: "Any camera" };
export const CAMERA_ORDER = ["iphone", "zfc", "gopro", "any"];
export const SHOT_STATE = { needed: "Needed", filmed: "Filmed: waiting for upload", got: "Got it", retake: "Retake" };
export const DECISION = { go: "Go", parked: "Parked", proposed: "Not decided" };
// The five kinds of post (ideas.post_type): the job each post does (studio/content_plan.py).
export const POST_TYPES = ["knowhow", "ideas", "onsite", "finished", "offer"];
export const POST_TYPE = { knowhow: "Know-how", ideas: "Ideas", onsite: "On site", finished: "Finished homes", offer: "Work with us" };
export const KIND_INFO = {
  knowhow: { stage: "Get found", job: "Teach something practical people save and share", ask: "Save this / follow for more" },
  ideas: { stage: "Get found", job: "Design thinking: layouts, zoning, light, colour", ask: "Save this / which would you pick?" },
  onsite: { stage: "Build trust", job: "Show the craft and care while the work happens", ask: "Follow this project" },
  finished: { stage: "Build trust", job: "Proof: the portfolio, before and after, the owners' reaction", ask: "See the whole project / talk to us about yours" },
  offer: { stage: "Get enquiries", job: "Turn followers into enquiries", ask: "One of your offers" },
};
export const isKind = (k) => POST_TYPES.includes(k);
// "Save this / which would you pick?" stays as it is; anything else gets a full stop.
export const sentence = (s) => (/[.?!]$/.test(String(s)) ? String(s) : `${s}.`);
// A small coloured dot (and the name) for a post's kind.
export const kindDot = (k) => (isKind(k) ? `<span class="kdot ${k}" aria-hidden="true"></span>` : "");
export const kindChip = (k) => (isKind(k) ? `<span class="chip kind ${k}">${kindDot(k)}${esc(POST_TYPE[k])}</span>` : "");
// ideas.post_type and the content plan come with a database update; until then the planner
// asks for ideas without it (see withKinds).
// ideas.slides (carousels) comes with a later one, the same way.
export const schema = { kinds: true, slides: true };
export const withKinds = (cols) => `${cols}${schema.kinds ? ",post_type" : ""}${schema.slides ? ",slides" : ""}`;
// Run a query that names post_type (and slides); if a column isn't there yet, again without it.
export async function kindsQuery(run) {
  for (;;) {
    try { return await run(); } catch (err) {
      if (!missingTable(err)) throw err;
      if (schema.slides && /slides/.test(err?.message || "")) schema.slides = false;
      else if (schema.kinds) schema.kinds = false;
      else throw err;
    }
  }
}
// clips.archived_at (Review's clean-up) comes with a later database update: footage pickers ask for
// it and leave archived clips out; until it's there they ask without it.
schema.archived = true;
export async function liveClips(run) {
  for (;;) {
    try {
      const rows = await run(schema.archived ? ",archived_at" : "");
      return rows.filter((c) => !c.archived_at);
    } catch (err) {
      if (schema.archived && missingTable(err) && /archived_at/.test(err?.message || "")) { schema.archived = false; continue; }
      throw err;
    }
  }
}
export const VERDICT = { good: "Good", check: "Check", retake: "Retake" };
// The laptop's production status, written into ideas.production.
export const PRODUCTION = {
  planned: "Queued for the editor", producing: "Editing", revising: "Revising", ready: "Draft ready for review",
  approved: "Approved", posted: "Posted", rejected: "Rejected", failed: "Edit failed",
};

// ── Helpers ─────────────────────────────────────────────────────────────────

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Who's signed in and the people they plan with.
export const state = { me: null, profiles: new Map(), laptop: null, view: "board" };
export const isOwner = () => state.me?.role === "owner";
export const nameOf = (id) => state.profiles.get(id)?.name || "";
export function ownerName() {
  for (const p of state.profiles.values()) if (p.role === "owner") return p.name || "The owner";
  return "The owner";
}

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Only http(s) URLs ever become a src or href.
export function httpUrl(url) {
  const u = String(url ?? "");
  return /^https?:\/\//i.test(u) ? u : "";
}
export function httpsUrl(url) {
  const u = String(url ?? "");
  return /^https:\/\//i.test(u) ? u : "";
}
export function mmss(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
export function ago(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
export function shortDate(iso) {
  const t = Date.parse(iso || "");
  return t ? new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
}
export function options(values, labels, current, blank) {
  return (blank != null ? `<option value="">${esc(blank)}</option>` : "")
    + values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
}
// Site days and phases are calendar dates ("2026-10-20"), read as local days.
const pad2 = (n) => String(n).padStart(2, "0");
export const isoDay = (d = new Date()) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
export const todayISO = () => isoDay(new Date());
export function parseDay(iso) {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}
// The local calendar day of a timestamp.
export const dayOf = (ts) => { const t = Date.parse(ts || ""); return t ? isoDay(new Date(t)) : ""; };
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "Mon 20 Oct"
export function dayLabel(iso, { weekday = true, year = false } = {}) {
  const d = parseDay(iso);
  if (!d) return "";
  return `${weekday ? `${WD[d.getDay()]} ` : ""}${d.getDate()} ${MON[d.getMonth()]}${year ? ` ${d.getFullYear()}` : ""}`;
}
// "Mon 20 – Tue 21 Oct", "From Mon 20 Oct", "No dates yet"
export function dayRange(a, b) {
  if (a && b && a === b) return dayLabel(a);
  if (a && b) {
    const da = parseDay(a), db = parseDay(b);
    const head = da.getMonth() === db.getMonth() && da.getFullYear() === db.getFullYear() ? `${WD[da.getDay()]} ${da.getDate()}` : dayLabel(a);
    return `${head} – ${dayLabel(b)}`;
  }
  if (a) return `From ${dayLabel(a)}`;
  if (b) return `Until ${dayLabel(b)}`;
  return "No dates yet";
}
// A table or column from a database update that hasn't been applied yet.
export function missingTable(err) {
  return /PGRST205|PGRST204|42P01|42703|Could not find the table|schema cache|does not exist/i.test(`${err?.code || ""} ${err?.message || ""}`);
}
// Filmed = ticked on site, or footage is in.
export const isFilmed = (s) => !!s.filmed_at || (s.takes || []).length > 0;
export function copyText(text) {
  const done = () => toast("Copied.");
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(done, () => toast(text, 8000));
  toast(text, 8000);
  return Promise.resolve();
}
// Served by the Studio dashboard at /plan/ (one app with the dashboard) rather than on its own.
export const underPlan = /^\/plan(\/|$)/.test(location.pathname);

export function store(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } }
export function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }
export function plural(n, word, many = `${word}s`) { return `${n} ${n === 1 ? word : many}`; }

// A text box someone is typing in (checkboxes and buttons don't count).
export function isTyping(el) {
  if (!el) return false;
  if (el.tagName === "TEXTAREA") return true;
  return el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(el.type);
}
// Typing with something unsaved: a field changed since it was rendered, or a draft box with text.
export function typingIn(root) {
  const a = document.activeElement;
  if (!root || !isTyping(a) || !root.contains(a)) return false;
  return a.dataset.orig != null ? a.value !== a.dataset.orig : a.value.trim() !== "";
}
// Re-render a region without losing what someone has typed but not sent yet,
// and put the cursor back where it was.
export function keepDrafts(root, fn) {
  const key = (el) => el.id ? `#${el.id}` : el.dataset.shotField ? `s${el.closest("[data-shot]")?.dataset.shot}:${el.dataset.shotField}`
    : el.dataset.ideaField ? `i:${el.dataset.ideaField}` : "";
  const find = (k) => {
    if (k.startsWith("#")) return document.getElementById(k.slice(1));
    if (k.startsWith("i:")) return root.querySelector(`[data-idea-field="${CSS.escape(k.slice(2))}"]`);
    const [sid, field] = k.slice(1).split(":");
    return root.querySelector(`[data-shot="${CSS.escape(sid)}"] [data-shot-field="${CSS.escape(field)}"]`);
  };
  const drafts = $$("[data-draft]", root).map((el) => [key(el), el.value]);
  const a = document.activeElement;
  const focus = a && root.contains(a) && a !== document.body
    ? { k: key(a), typing: isTyping(a), dirty: a.dataset.orig == null || a.value !== a.dataset.orig, v: a.value, s: a.selectionStart, e: a.selectionEnd }
    : null;
  fn();
  for (const [k, v] of drafts) {
    const el = k && find(k);
    if (el && v && !el.disabled) el.value = v;
  }
  if (focus?.k) {
    const el = find(focus.k);
    if (el) {
      el.focus({ preventScroll: true });
      if (focus.typing && isTyping(el)) {
        // Keep unsaved typing; a clean field shows the fresh value.
        if (focus.dirty) el.value = focus.v;
        try { el.setSelectionRange(focus.s, focus.e); } catch { /* number inputs */ }
      }
    }
  }
}

// Supabase answers {data, error}; turn errors into exceptions with a readable message.
export function must({ data, error }) {
  if (error) throw new Error(error.message || String(error));
  return data;
}
async function fnError(error) {
  const res = error?.context;
  if (res && typeof res.clone === "function") {
    try {
      const j = await res.clone().json();
      if (j?.error || j?.message) return String(j.error || j.message);
    } catch { /* not JSON */ }
    try {
      const t = (await res.clone().text()).trim();
      if (t) return t.slice(0, 200);
    } catch { /* no body */ }
    if (res.status === 404) return "the function isn't deployed yet";
    if (res.status) return `HTTP ${res.status}`;
  }
  if (/Failed to send a request/i.test(error?.message || "")) return "couldn't reach the server";
  return error?.message || String(error);
}
// Call an Edge Function (the user's session token goes along automatically).
export async function invoke(name, opts = {}) {
  const { data, error } = await sb.functions.invoke(name, opts);
  if (error) throw new Error(await fnError(error));
  if (data && typeof data === "object" && data.error) throw new Error(String(data.error));
  return data;
}

let toastTimer;
export function toast(text, ms = 3200) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// A missing still hides itself instead of showing a broken image.
document.addEventListener("error", (e) => {
  if (e.target.tagName === "IMG") e.target.classList.add("broken");
}, true);

// ── Shots ───────────────────────────────────────────────────────────────────

export const takeVerdict = (t) => t?.clips?.verdict ?? null;
export const takeRetake = (t) => needsRetake(t?.clips);
export const takeFlags = (t) => (Array.isArray(t?.clips?.flags) ? t.clips.flags : [])
  .map((f) => (typeof f === "string" ? f : f?.text)).filter(Boolean);

// needed | filmed (ticked, footage not in yet) | got (a usable take) | retake.
export function shotState(shot) {
  const takes = shot.takes || [];
  if (takes.some((t) => !takeRetake(t))) return "got";
  if (takes.length) return "retake";
  return shot.filmed_at ? "filmed" : "needed";
}
export function progress(shots) {
  const p = { needed: 0, filmed: 0, got: 0, retake: 0, total: shots.length };
  for (const s of shots) p[s.state] += 1;
  return p;
}
// Shots in order with their takes and state.
export function prepShots(shots) {
  const list = [...(shots || [])].sort((a, b) => (a.position - b.position) || (a.id - b.id));
  for (const s of list) {
    s.takes = [...(s.takes || [])].sort((a, b) => a.id - b.id);
    s.state = shotState(s);
  }
  return list;
}
export const bestTake = (s) => s.takes.find((t) => !takeRetake(t)) || s.takes[0] || null;

// "As long as videos are usable, don't flag": cards and thumbnails only warn about a clip
// that needs a retake (clips.flagged, after the database update, says the same).
export const needsRetake = (c) => !!c && (c.verdict === "retake" || c.flagged === true);
export const retakeBadge = (c) => (needsRetake(c) ? `<span class="chip verdict retake">Retake</span>` : "");

// got / filmed / retake / needed as one segmented bar. Widths are set by script
// (the CSP allows no inline styles), see the observer below.
export function pbarHTML(p) {
  if (!p || !p.total) return `<span class="pbar none"></span>`;
  const label = `${p.got} got, ${p.filmed} filmed, ${p.retake} to retake, ${p.needed} needed`;
  return `<span class="pbar" role="img" aria-label="${esc(label)}" title="${esc(label)}">${["got", "filmed", "retake", "needed"]
    .map((k) => (p[k] ? `<i class="${k}" data-f="${Number(p[k]) || 0}"></i>` : "")).join("")}</span>`;
}
// A thin "done of total" bar.
export function fbarHTML(done, total, label = "") {
  if (!total) return `<span class="pbar thin none"></span>`;
  const l = label || `${done} of ${total} filmed`;
  return `<span class="pbar thin" role="img" aria-label="${esc(l)}" title="${esc(l)}">${done ? `<i class="got" data-f="${Number(done) || 0}"></i>` : ""}${total - done ? `<i class="needed" data-f="${Number(total - done) || 0}"></i>` : ""}</span>`;
}
const cleanFolder = (s) => String(s || "").replace(/[<>:"/\\|?*]/g, "").trim().slice(0, 80);
// Where camera clips for a Go idea go: its post folder in the Studio Inbox.
export function folderLineHTML(project, idea) {
  if (!idea?.folder || idea.decision !== "go" || idea.status === "dismissed") return "";
  const pf = cleanFolder(project?.folder) || cleanFolder(project?.name);
  const parts = ["OneDrive", "Studio Inbox", pf, idea.folder].filter(Boolean);
  const path = ["Studio Inbox", pf, idea.folder].filter(Boolean).join("/");
  return `<div class="folder-line"><span class="folder-text">Camera clips: ${parts.map(esc).join(" › ")}</span>
    <button type="button" class="btn small ghost" data-copy="${esc(path)}" aria-label="Copy the folder path ${esc(path)}">Copy</button></div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest?.("[data-copy]");
  if (b) copyText(b.dataset.copy);
});
function paintBars(root) {
  if (root.nodeType !== 1) return;
  const bars = root.matches?.(".pbar i[data-f]") ? [root] : root.querySelectorAll(".pbar i[data-f]");
  for (const i of bars) i.style.flex = `${Number(i.dataset.f) || 0} 1 0`;
}
new MutationObserver((records) => {
  for (const r of records) for (const n of r.addedNodes) paintBars(n);
}).observe(document.documentElement, { childList: true, subtree: true });

// ── Stills: signed URLs, cached so re-renders don't reload images ──────────

const signed = new Map(); // path -> {url, until}
export async function signPaths(paths) {
  const now = Date.now();
  const need = [...new Set(paths.filter(Boolean))].filter((p) => {
    const s = signed.get(p);
    return !s || s.until - now < 10 * 60e3;
  });
  for (let i = 0; i < need.length; i += 100) {
    const chunk = need.slice(i, i + 100);
    const { data, error } = await sb.storage.from("thumbs").createSignedUrls(chunk, 3600);
    if (error) { for (const p of chunk) signed.set(p, { url: "", until: now + 60e3 }); continue; }
    for (const d of data || []) {
      const url = !d.error && httpUrl(d.signedUrl);
      // Not uploaded yet (the laptop sends stills after the row): try again in a minute.
      signed.set(d.path, url ? { url, until: now + 3600e3 } : { url: "", until: now + 60e3 });
    }
  }
}
// The first of these paths that has a still.
export function stillUrl(...paths) {
  for (const p of paths) {
    const s = p && signed.get(p);
    if (s?.url) return s.url;
  }
  return "";
}
export const takePaths = (t) => [t?.thumb_path, t?.clips?.thumb_path];

// ── Jobs ────────────────────────────────────────────────────────────────────

const STALE_MS = 4 * 60e3;
// A cloud job still running 4 minutes after it started died with its function.
export function jobView(job) {
  if (!job) return null;
  if (job.runner === "cloud" && job.state === "running"
      && Date.now() - Date.parse(job.started_at || job.updated_at || job.created_at) > STALE_MS) {
    return { ...job, state: "failed", error: "Took too long, try again." };
  }
  const kickError = kickErrors.get(job.id);
  if (kickError && job.state === "queued") return { ...job, state: "failed", error: `Couldn't start: ${kickError}` };
  return job;
}
export const jobActive = (job) => !!job && (job.state === "queued" || job.state === "running");

// Organise only queues one develop job per new idea; the planner starts them
// (at most 4 at a time) and remembers which it already started this session.
const kicked = new Set();
const kickQueue = [];
export const kickErrors = new Map();
let inFlight = 0;
export function kickDevelop(jobs) {
  for (const j of jobs || []) {
    if (j.kind === "develop" && j.state === "queued" && !kicked.has(j.id)) {
      kicked.add(j.id);
      kickQueue.push(j.id);
    }
  }
  pumpKicks();
}
function pumpKicks() {
  while (inFlight < 4 && kickQueue.length) {
    const id = kickQueue.shift();
    inFlight += 1;
    invoke("plan-develop", { body: { job_id: id } })
      .catch((err) => {
        kickErrors.set(id, err.message);
        document.dispatchEvent(new CustomEvent("studio:refresh"));
      })
      .finally(() => { inFlight -= 1; pumpKicks(); });
  }
}

// ── The Studio laptop ───────────────────────────────────────────────────────

export function laptopOn() {
  const t = Date.parse(state.laptop?.last_seen || "");
  return !!t && Date.now() - t < 2 * 60e3;
}
export function laptopSince() {
  const t = state.laptop?.last_seen;
  return t ? ago(t) : "";
}
// "Waiting for Studio" wording for jobs that need it.
export function laptopWait() {
  if (laptopOn()) return "Studio picks it up within a minute.";
  const since = laptopSince();
  return `Waiting for Studio (offline${since ? ` since ${since}` : ""}). It runs as soon as Studio is back.`;
}

// Phones (720 px and less, where the tab bar is): a planned post opens as a page of its
// own under the top bar, not over everything.
export const PHONE = window.matchMedia("(max-width: 720px)");
export const pageMode = () => PHONE.matches;
// Content under an overlay (viewer, checklist, modal) can't be reached by keyboard or screen
// readers. On a phone the post is a page: the top bar and tab bar stay usable.
export function syncInert() {
  const viewer = !$("#viewer").hidden;
  const page = viewer && pageMode();
  const overlay = (viewer && !page) || !$("#checklist").hidden;
  document.body.classList.toggle("idea-page", page);
  $("#viewer").toggleAttribute("aria-modal", viewer && !page);
  $("#viewer").setAttribute("role", page ? "region" : "dialog");
  // Today on site sits under the top bar (and above the phone tab bar), so both stay usable over it.
  $("#shell-top").inert = viewer && !page;
  $("#shell-tabbar").inert = viewer && !page;
  $("#main").inert = overlay;
  $("#app").inert = !$("#modal").hidden;
}

// ── Modal ───────────────────────────────────────────────────────────────────

let modalClick = null;
let modalReturn = null;
// On a phone a dialog is a step in the history, so the back gesture closes it.
const modalStep = { on: false, skip: false, done: null };
// kind "sheet": full screen on phones (quick add, the footage and shot pickers).
export function openModal(html, onClick, wide = false, kind = "") {
  if ($("#modal").hidden) {
    modalReturn = document.activeElement;
    if (pageMode()) {
      history.pushState({ ...(history.state || {}), studioModal: true }, "");
      modalStep.on = true;
    }
  }
  $("#modal-body").innerHTML = html;
  $("#modal .modal-card").classList.toggle("wide", wide);
  $("#modal").classList.toggle("sheet", kind === "sheet");
  modalClick = onClick || null;
  $("#modal").hidden = false;
  syncInert();
  document.body.classList.add("locked");
  if (kind === "sheet") $("#modal .modal-card").scrollTop = 0;
  $("#modal-body").querySelector("input:not([type=hidden]), textarea, select")?.focus();
}
// Resolves once the history is back where it was (await it before going somewhere else).
export function closeModal() {
  if ($("#modal").hidden) return Promise.resolve();
  $("#modal").hidden = true;
  $("#modal-body").innerHTML = "";
  modalClick = null;
  syncInert();
  if (lockedBehind()) document.body.classList.add("locked"); else document.body.classList.remove("locked");
  modalReturn?.focus?.();
  modalReturn = null;
  if (!modalStep.on) return Promise.resolve();
  modalStep.on = false;
  if (!history.state?.studioModal) return Promise.resolve();
  modalStep.skip = true;
  const back = new Promise((resolve) => { modalStep.done = resolve; setTimeout(resolve, 1000); });
  history.back();
  return back;
}
window.addEventListener("popstate", () => {
  if (modalStep.skip) {
    modalStep.skip = false;
    modalStep.done?.();
    modalStep.done = null;
    return;
  }
  // The back gesture with a dialog open: it closes.
  if (modalStep.on && !$("#modal").hidden && !history.state?.studioModal) {
    modalStep.on = false;
    closeModal();
  }
});
// Something under the dialog still holds the page still (a desktop overlay, Today on site).
function lockedBehind() {
  return (!$("#viewer").hidden && !pageMode()) || !$("#checklist").hidden;
}
export const modalOpen = () => !$("#modal").hidden;
$("#modal").addEventListener("click", async (e) => {
  if (e.target.id === "modal" || e.target.closest("[data-modal-close]")) { closeModal(); return; }
  if (modalClick) {
    try { await modalClick(e); } catch (err) { toast(`Couldn't do that: ${err.message}`); }
  }
});
// Enter in a modal form "clicks" its submit button, which the click handler above runs.
$("#modal").addEventListener("submit", (e) => e.preventDefault());
export function formError(text) {
  const el = $("#modal-error");
  if (el) { el.textContent = text; el.hidden = !text; }
}
