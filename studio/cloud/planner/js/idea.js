// One planned post: the storyboard (left) and its plan (right). Ported from the
// laptop dashboard's planned-idea viewer. Stills come from the private bucket.
import {
  $, cfg, BRAND, ACCOUNTS, oneBrand, FORMAT, PLAN_FORMATS, FRAMING, KIND, CAMERA, SHOT_STATE, DECISION, PRODUCTION,
  esc, sb, must, invoke, toast, options, ago, mmss, shortDate, plural, typingIn, keepDrafts, isOwner, ownerName,
  nameOf, state, pbarHTML, prepShots, progress, bestTake, takeRetake, takeFlags, retakeBadge, signPaths, stillUrl,
  takePaths, jobView, jobActive, kickDevelop, laptopWait, httpUrl, httpsUrl, openModal, closeModal, syncInert,
  missingTable, dayLabel, folderLineHTML, pageMode, PHONE, POST_TYPES, POST_TYPE, KIND_INFO, kindDot, sentence,
  isCarousel, REEL_FORMATS, schema,
} from "./core.js";
import { plan } from "./board.js";
import { uploadButtonsHTML, rememberShot } from "./upload.js";
import {
  prepSlides, slidePaths, slidesStoryHTML, slidesFieldHTML, writeSlidesHTML, saveSlideField, slidesClick, slidesLocked,
  conceptsMakeLine,
} from "./slides.js";

// clips(*): its "flagged" column (needs a retake) comes with a database update.
const IDEA_SEL = "*,shots(*,takes(*,clips(*))),"
  + "votes(user_id,vote),comments(id,author,author_name,body,created_at)";
const JOB_COLS = "id,kind,runner,project_id,idea_id,state,result,error,payload,created_at,started_at,updated_at";

export const view = { id: null, d: null, storySig: "", panelSig: "", pending: false };
const panel = () => $("#panel");
export const viewerOpen = () => !$("#viewer").hidden;

// On a computer the post opens over the board (the page underneath stays still); on a
// phone it's a page of its own under the top bar (core.js syncInert), and the board comes
// back where it was.
function syncLock() {
  const lock = (viewerOpen() && !pageMode()) || !$("#checklist").hidden || !$("#modal").hidden;
  document.body.classList.toggle("locked", lock);
}

// cameFrom: the address it was opened from (the board, the shoot plan, Today), so Back
// can be the browser's own back (and the back gesture does the same).
export async function openIdea(id, cameFrom = null) {
  id = Number(id);
  view.lastId = id;
  const fresh = view.id !== id;
  view.id = id;
  if (fresh) {
    view.d = null;
    view.storySig = "";
    view.panelSig = "";
    $("#player-frame").innerHTML = `<div class="placeholder"><span class="spin"></span> Loading the storyboard…</div>`;
    panel().innerHTML = "";
  }
  const opening = $("#viewer").hidden;
  if (opening) {
    view.returnFocus = document.activeElement;
    view.cameFrom = cameFrom;
    view.scroll = { y: window.scrollY, x: $("#plan-board")?.scrollLeft || 0, pid: plan.pid };
    $("#viewer").hidden = false;
    syncInert();
    syncLock();
  }
  if (pageMode() && fresh) window.scrollTo(0, 0);
  renderHead();
  try {
    await loadIdea(true);
  } catch (err) {
    panel().innerHTML = `<p class="muted">Couldn't open this idea (${esc(err.message)}).</p>`;
  }
  updateArrows();
  if (fresh) (pageMode() ? $("#viewer-head [data-back]") : $("#close"))?.focus({ preventScroll: true });
}

export function hideViewer() {
  if ($("#viewer").hidden) return;
  $("#viewer").hidden = true;
  syncInert();
  syncLock();
  $("#player-frame").innerHTML = "";
  panel().innerHTML = "";
  $("#viewer-head").innerHTML = "";
  view.id = null;
  view.d = null;
  const back = view.returnFocus;
  view.returnFocus = null;
  view.lastClosed = view.lastId;
  if (back && document.contains(back)) back.focus({ preventScroll: true });
}

// Back to the board where it was: the browser's back when the post was opened from it.
export function closeIdea() {
  const pid = view.d?.project_id || plan.pid;
  if (view.cameFrom && history.length > 1) { history.back(); return; }
  location.hash = pid ? `#/project/${pid}` : "#/";
}
// After the board is drawn again: its scroll, as it was when the post opened.
export function restoreBoardScroll() {
  const s = view.scroll;
  if (!s || s.pid !== plan.pid) return;
  window.scrollTo(0, s.y);
  const board = $("#plan-board");
  if (board) board.scrollLeft = s.x;
}
export function forgetBoardScroll() { view.scroll = null; }

// Phones: Back, where this post is on the board, and the next one, above the post.
function renderHead() {
  const head = $("#viewer-head");
  if (!viewerOpen() || !pageMode()) { head.innerHTML = ""; return; }
  const i = plan.order.indexOf(view.id);
  const last = i < 0 || i >= plan.order.length - 1;
  head.innerHTML = `<button type="button" class="head-link" data-back aria-label="Back to the plan">‹&nbsp;Back</button>
    ${i >= 0 ? `<span class="head-pos">${i + 1} of ${plan.order.length}</span>` : ""}
    <button type="button" class="head-link head-next" data-head-next ${last ? "disabled" : ""} aria-label="Next idea">›</button>`;
}
$("#viewer-head").addEventListener("click", (e) => {
  if (e.target.closest("[data-back]")) closeIdea();
  else if (e.target.closest("[data-head-next]")) step(1);
});
PHONE.addEventListener("change", () => {
  if (!viewerOpen()) return;
  syncInert();
  syncLock();
  renderHead();
});

export async function loadIdea(force = false) {
  const id = view.id;
  if (!id) return;
  // Loads overlap (saves, live updates); only the newest one may render.
  const seq = (view.seq = (view.seq || 0) + 1);
  const stale = () => view.id !== id || view.seq !== seq;
  const [idea, jobs] = await Promise.all([
    sb.from("ideas").select(IDEA_SEL).eq("id", id).maybeSingle().then(must),
    sb.from("jobs").select(JOB_COLS).eq("idea_id", id).order("id", { ascending: false }).limit(60).then(must),
  ]);
  if (stale()) {
    if (force && view.id === id) view.forceNext = true;
    return;
  }
  if (!idea) {
    view.d = null;
    $("#player-frame").innerHTML = `<div class="placeholder">This idea is gone.</div>`;
    panel().innerHTML = `<p class="muted">Someone deleted it, or it never existed.</p><p><a class="btn" href="#/">Back to the board</a></p>`;
    return;
  }
  // Phases and site days (for "Phase" / "Day" on each shot) come with a database update.
  const optional = (q) => q.then(must).catch((err) => (missingTable(err) ? null : []));
  const [arcs, phases, days] = await Promise.all([
    sb.from("arcs").select("id,title,position").eq("project_id", idea.project_id).order("position").order("id").then(must),
    optional(sb.from("phases").select("id,name,position").eq("project_id", idea.project_id).order("position").order("id")),
    optional(sb.from("site_days").select("id,day,phase_id").eq("project_id", idea.project_id).order("day")),
  ]);
  if (stale()) {
    if (force && view.id === id) view.forceNext = true;
    return;
  }
  const d = idea;
  d.shots = prepShots(d.shots);
  d.progress = progress(d.shots);
  d.arcs = arcs;
  d.phases = phases;
  d.days = days;
  for (const s of d.shots) rememberShot(s.id, `${s.description} (for: ${d.title})`);
  const up = [], down = [];
  let mine = 0;
  for (const v of d.votes || []) {
    (v.vote > 0 ? up : down).push(nameOf(v.user_id) || "Someone");
    if (v.user_id === state.me?.id) mine = v.vote;
  }
  d.votes = { up, down, mine };
  d.notes = [...(d.comments || [])].sort((a, b) => a.id - b.id);
  d.music = d.music && typeof d.music === "object" ? d.music : {};
  d.jobs = {};
  for (const j of jobs) if (!d.jobs[j.kind]) d.jobs[j.kind] = j;
  d.canMake = d.shots.some((s) => s.takes.length);
  // A carousel: its slides (and the titles of the clips its stills come from).
  d.slidesOnline = "slides" in d; // the carousels database update is in
  d.slides = prepSlides(d.slides);
  if (isCarousel(d)) {
    d.canMake = d.slides.length >= 2;
    const ids = [...new Set(d.slides.map((x) => x.image?.clip_id).filter((x) => Number.isInteger(x)))];
    const clips = ids.length ? await sb.from("clips").select("id,title").in("id", ids).then(must).catch(() => []) : [];
    d.clipTitles = new Map(clips.map((c) => [c.id, c.title]));
  }
  await signPaths([...d.shots.flatMap((s) => s.takes.flatMap(takePaths)), ...slidePaths(d.slides)]).catch(() => {});
  if (stale()) {
    if (force && view.id === id) view.forceNext = true;
    return;
  }
  if (view.forceNext) { force = true; view.forceNext = false; }
  view.d = d;
  kickDevelop(jobs);
  render(force);
}

// Re-render what changed; never under someone's unsaved typing.
function render(force = false) {
  const d = view.d;
  if (!d) return;
  const jobs = Object.fromEntries(Object.entries(d.jobs).map(([k, j]) => [k, jobView(j)]));
  const shotsSig = JSON.stringify([d.shots, d.title, d.format, d.slides, jobs.develop?.state, d.shots.map((s) => s.takes.map((t) => stillUrl(...takePaths(t)))),
    d.slides.map((x) => stillUrl(x.image?.thumb_path))]);
  if (force || shotsSig !== view.storySig) {
    if (isCarousel(d)) {
      const scroll = $("#player-frame").scrollTop;
      $("#player-frame").innerHTML = slidesStoryHTML(d);
      $("#player-frame").scrollTop = scroll;
    } else renderStoryboard();
    view.storySig = shotsSig;
  }
  const sig = JSON.stringify([d, jobs, state.laptop?.last_seen ? Math.floor(Date.parse(state.laptop.last_seen) / 60e3) : 0,
    state.laptop?.info?.concepts?.on === true]);
  if (!force && sig === view.panelSig) return;
  if (!force && typingIn(panel())) { view.pending = true; return; }
  renderPanel();
  view.panelSig = sig;
  view.pending = false;
}

// ── Storyboard ──────────────────────────────────────────────────────────────

function frameHTML(s, i) {
  const take = bestTake(s);
  const thumb = take ? stillUrl(...takePaths(take)) : "";
  const tags = [CAMERA[s.camera] && s.camera !== "any" ? CAMERA[s.camera] : "", FRAMING[s.framing] || ""].filter(Boolean).join(" · ");
  const media = thumb
    ? `<button class="sb-media has-take" data-goto-shot="${esc(s.id)}" aria-label="Shot ${i + 1}: ${esc(s.description)}. Go to its details">
        <img src="${esc(thumb)}" alt="" loading="lazy"></button>`
    : `<button class="sb-media sb-placeholder" data-goto-shot="${esc(s.id)}" aria-label="Shot ${i + 1}: ${esc(s.description)}. Edit it">
        <span class="sb-camera">${esc(CAMERA[s.camera] || CAMERA.any)}</span>
        <span class="sb-framing">${esc((FRAMING[s.framing] || "Shot").toUpperCase())}</span>
        ${s.kind ? `<span class="sb-kind">${esc(KIND[s.kind] || s.kind)}</span>` : ""}
        <span class="sb-desc">${esc(s.description)}</span>
        ${s.on_screen ? `<span class="sb-caption">${esc(s.on_screen)}</span>` : ""}
        ${s.line ? `<span class="sb-line">“${esc(s.line)}”</span>` : ""}
      </button>`;
  const flags = s.state === "retake" ? takeFlags(take) : [];
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
  const d = view.d;
  const p = d.progress;
  const secs = d.shots.reduce((sum, s) => sum + (Number(s.seconds) || 0), 0);
  const dev = jobView(d.jobs.develop);
  const drafting = jobActive(dev);
  const scroll = $("#player-frame").scrollTop;
  $("#player-frame").innerHTML = `<div class="storyboard">
    <div class="sb-head"><h2 id="sb-title">Storyboard</h2>
      <span class="muted">${plural(d.shots.length, "shot")}${secs ? ` · about ${Math.round(secs)}s` : ""}</span>
      ${p.total ? `<span class="sb-progress">${pbarHTML(p)} ${esc(p.got)}/${esc(p.total)} got</span>` : ""}
      ${drafting && d.shots.length ? `<span class="sb-drafting"><span class="spin"></span> Claude is reworking it…</span>` : ""}</div>
    ${d.shots.length ? `<div class="sb-strip">${d.shots.map(frameHTML).join("")}</div>`
      : drafting ? `<div class="sb-empty"><p><span class="spin"></span> The storyboard is being drafted…</p><p class="muted">Claude is writing the shots. About a minute; it appears here by itself.</p></div>`
        : `<div class="sb-empty"><p>No shots planned yet.</p><p class="muted">Add shots on the right, or use Refine with Claude to storyboard it.</p></div>`}
  </div>`;
  $("#player-frame").scrollTop = scroll;
}

$("#player-frame").addEventListener("click", (e) => {
  const go = e.target.closest("[data-goto-shot], [data-goto-slide]");
  if (!go) return;
  const row = go.dataset.gotoSlide ? document.getElementById(`slide-${go.dataset.gotoSlide}`)
    : document.getElementById(`shot-${Number(go.dataset.gotoShot)}`);
  if (!row) return;
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.remove("flash");
  void row.offsetWidth;
  row.classList.add("flash");
  row.querySelector("textarea")?.focus({ preventScroll: true });
});

// ── Panel ───────────────────────────────────────────────────────────────────

function takeHTML(t) {
  const thumb = stillUrl(...takePaths(t));
  const flags = takeRetake(t) ? takeFlags(t) : [];
  const c = t.clips || {};
  return `<div class="take">
    <span class="take-thumb">${thumb ? `<img src="${esc(thumb)}" alt="">` : `<span class="take-none" aria-hidden="true">No still</span>`}</span>
    <div class="take-info">
      <div class="row tight">${retakeBadge(t.clips)}<span class="muted small">${t.note ? esc(t.note) : t.source === "auto" ? "Matched automatically" : `Added by hand${t.created_by && nameOf(t.created_by) ? ` by ${esc(nameOf(t.created_by))}` : ""}`}</span></div>
      <div class="small">${esc(c.title || `Clip ${t.clip_id}`)} · ${t.start != null ? mmss(t.start) : "0:00"}–${t.end != null ? mmss(t.end) : "end"}</div>
      ${flags.length ? `<ul class="take-flags">${flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    </div>
    <button class="btn small ghost" data-take="${esc(t.id)}" data-take-shot="${esc(t.shot_id)}" aria-label="Move take ${esc(c.title || "")} to another shot">Move</button>
    <button class="icon-btn danger" data-take-remove="${esc(t.id)}" title="Remove this take" aria-label="Remove take ${esc(c.title || "")}">×</button>
  </div>`;
}

function shotRowHTML(s, i, n) {
  const field = (key, label, ph, max) => `<label class="inline-field"><span>${label}</span><input data-shot-field="${key}" maxlength="${max}" value="${esc(s[key])}" data-orig="${esc(s[key])}" placeholder="${esc(ph)}"></label>`;
  const d = view.d;
  const place = d.phases ? `<div class="shot-place">
      <label class="inline-field"><span>Phase</span><select data-shot-field="phase_id" aria-label="Phase of shot ${i + 1}"><option value="">No phase</option>${d.phases.map((p) => `<option value="${esc(p.id)}" ${p.id === s.phase_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label class="inline-field"><span>Day</span><select data-shot-field="shoot_on" aria-label="Site day for shot ${i + 1}"><option value="">Any day in its phase</option>${(d.days || []).map((x) => `<option value="${esc(x.day)}" ${x.day === s.shoot_on ? "selected" : ""}>${esc(dayLabel(x.day))}</option>`).join("")}
        ${s.shoot_on && !(d.days || []).some((x) => x.day === s.shoot_on) ? `<option value="${esc(s.shoot_on)}" selected>${esc(dayLabel(s.shoot_on))} (not a site day)</option>` : ""}</select></label>
    </div>` : "";
  return `<li class="shot-row st-${esc(s.state)}" id="shot-${esc(s.id)}" data-shot="${esc(s.id)}" data-drop-shot="${esc(s.id)}" data-saved-scope>
    <div class="shot-top">
      <span class="shot-num" aria-hidden="true">${i + 1}</span>
      <label class="filmed-toggle" title="Tick when it's been filmed on site"><input type="checkbox" data-shot-filmed ${s.filmed_at ? "checked" : ""} aria-label="Shot ${i + 1} filmed"><span>Filmed</span></label>
      <span class="badge st-${esc(s.state)}">${esc(SHOT_STATE[s.state] || s.state)}</span>
      <span class="saved" aria-live="polite"></span>
      <span class="shot-tools">
        <button class="icon-btn" data-shot-move="-1" ${i === 0 ? "disabled" : ""} title="Move up" aria-label="Move shot ${i + 1} up">↑</button>
        <button class="icon-btn" data-shot-move="1" ${i === n - 1 ? "disabled" : ""} title="Move down" aria-label="Move shot ${i + 1} down">↓</button>
        <button class="icon-btn danger" data-shot-delete title="Delete shot" aria-label="Delete shot ${i + 1}">×</button>
      </span>
    </div>
    ${s.filmed_at ? `<p class="muted small">Ticked${s.filmed_by && nameOf(s.filmed_by) ? ` by ${esc(nameOf(s.filmed_by))}` : ""} ${esc(ago(s.filmed_at))}</p>` : ""}
    <textarea data-shot-field="description" rows="2" maxlength="1000" data-orig="${esc(s.description)}" aria-label="Shot ${i + 1}: what the camera sees">${esc(s.description)}</textarea>
    <div class="shot-selects">
      <select data-shot-field="camera" aria-label="Camera">${options(Object.keys(CAMERA), CAMERA, s.camera, "Camera…")}</select>
      <select data-shot-field="framing" aria-label="Framing">${options(Object.keys(FRAMING), FRAMING, s.framing, "Framing…")}</select>
      <select data-shot-field="kind" aria-label="Kind of shot">${options(Object.keys(KIND), KIND, s.kind, "Kind…")}</select>
      <span class="num-field"><input type="number" min="0.5" max="120" step="0.5" data-shot-field="seconds" value="${esc(s.seconds ?? "")}" data-orig="${esc(s.seconds ?? "")}" aria-label="Seconds in the cut"> s</span>
    </div>
    ${field("line", "Says", "Spoken line (optional)", 1000)}
    ${field("on_screen", "On screen", "Text on screen (optional)", 300)}
    ${field("why", "Why", "What this shot does for the story", 500)}
    ${place}
    <div class="takes">${s.takes.map(takeHTML).join("")}
      <div class="row tight"><button class="btn small" data-attach>${s.takes.length ? "Attach more footage" : "Attach footage"}</button>
        ${uploadButtonsHTML(s.id, `shot ${i + 1}`)}</div></div>
  </li>`;
}

function musicHTML(d) {
  const m = d.music || {};
  const job = jobView(d.jobs.music);
  const active = jobActive(job);
  const desc = [m.mood, m.tempo_bpm ? `about ${Math.round(m.tempo_bpm)} BPM` : "", m.search ? `search “${m.search}”` : ""].filter(Boolean).map(esc).join(" · ");
  const chosenId = m.chosen?.recording_id;
  let body = "";
  if (m.chosen) body += `<div class="chosen">♪ Picked: <strong>${esc(m.chosen.title)}</strong>${m.chosen.artist ? ` by ${esc(m.chosen.artist)}` : ""}</div>`;
  const tracks = Array.isArray(m.shortlist) ? m.shortlist : [];
  if (tracks.length) {
    body += `<ul class="tracks">${tracks.map((t, i) => {
      const on = chosenId != null && t.recording_id === chosenId;
      const preview = httpsUrl(t.preview_url), page = httpsUrl(t.page_url);
      return `<li class="track${on ? " on" : ""}">
        <div class="track-head"><div><strong>${esc(t.title)}</strong> <span class="muted">${esc(t.artist)}${t.bpm ? ` · ${esc(Math.round(t.bpm))} BPM` : ""}</span></div>
          <button class="btn small${on ? " good" : ""}" data-music-pick="${i}" aria-pressed="${on}">${on ? "Unpick" : "Pick"}</button></div>
        ${t.why ? `<p class="muted small">${esc(t.why)}</p>` : ""}
        ${preview ? `<audio controls preload="none" src="${esc(preview)}"></audio>` : ""}
        ${page ? `<a class="small" href="${esc(page)}" target="_blank" rel="noopener noreferrer">Open on Epidemic Sound ↗</a>` : ""}
      </li>`;
    }).join("")}</ul>`;
  }
  if (m.note && (d.brand === "personal" || m.source === "instagram" || !tracks.length)) body += `<p class="muted music-note">${esc(m.note)}</p>`;
  const status = job?.state === "queued" ? `<p class="muted small job-wait"><span class="spin"></span> Queued. ${esc(laptopWait())}</p>`
    : job?.state === "running" ? `<p class="muted small"><span class="spin"></span> Studio is looking for tracks. About a minute.</p>`
      : job?.state === "failed" ? `<p class="error-text small">Couldn't shortlist: ${esc(job.error || "unknown error")}</p>` : "";
  const again = tracks.length || m.note;
  return `<div class="field"><h3>Music</h3>
    <p>${desc || `<span class="muted">No direction yet. Refine with Claude to get one.</span>`}</p>${body}
    <div class="row mt8"><button class="btn" id="music-shortlist" ${active ? "disabled" : ""}
      title="Runs on Studio. Urban Makers: a few Epidemic Sound tracks to try. Personal: a note on what to pick in Instagram.">${active ? "Shortlisting…" : again ? "Shortlist again" : "Shortlist music"}</button></div>
    ${status}
  </div>`;
}

function refineHTML(d) {
  const job = jobView(d.jobs.develop);
  const active = jobActive(job);
  const changes = job?.result?.changes;
  return `<div class="box refine"><h3>Refine with Claude</h3>
    <label class="sr-only" for="refine-request">What should change</label>
    <textarea id="refine-request" data-draft rows="2" maxlength="4000" ${active ? "disabled" : ""} placeholder="What should change? e.g. “make the hook punchier”, “add a before/after”. Leave empty to just complete the plan."></textarea>
    <div class="row mt8"><button class="btn primary" id="refine-go" ${active ? "disabled" : ""} title="About a minute; costs a few cents">${active ? "Refining…" : "Refine with Claude"}</button>
      ${active ? `<span class="muted small"><span class="spin"></span> ${job.state === "queued" ? "Starting…" : "Claude is reworking the plan."}</span>` : ""}</div>
    <p class="muted small">Claude rewrites the hook, text and storyboard and reads the comments. Shots already filmed or with footage are always kept; unfilmed ones may be reworded or replaced.</p>
    ${job?.state === "done" && changes ? `<p class="refine-result"><strong>Last refine (${esc(ago(job.updated_at))}):</strong> ${esc(Array.isArray(changes) ? changes.join(" ") : changes)}</p>` : ""}
    ${job?.state === "failed" ? `<p class="error-text">Refine failed: ${esc(job.error || "unknown error")}</p>` : ""}
  </div>`;
}

function commentsHTML(notes) {
  const me = state.me?.id;
  return `<div class="field"><h3>Comments${notes.length ? ` (${notes.length})` : ""}</h3>
    ${notes.length ? `<ul class="notes">${notes.map((n) => `<li><div class="note-by"><strong>${esc(n.author_name || nameOf(n.author) || "Someone")}</strong> <span class="muted small">${esc(ago(n.created_at))}</span>
        ${n.author === me || isOwner() ? `<button class="icon-btn small-x" data-comment-delete="${esc(n.id)}" title="Delete comment" aria-label="Delete comment">×</button>` : ""}</div>
      <span class="note-body">${esc(n.body)}</span></li>`).join("")}</ul>` : `<p class="muted">No comments yet.</p>`}
    <label class="sr-only" for="comment-body">Add a comment</label>
    <textarea id="comment-body" data-draft rows="2" maxlength="4000" placeholder="Add a comment for the team (Ctrl+Enter to post)" class="mt8"></textarea>
    <div class="row mt8"><button class="btn push-right" id="comment-add">Comment</button></div>
  </div>`;
}

function makeHTML(d) {
  const owner = isOwner();
  const job = jobView(d.jobs.make);
  const prod = d.production && typeof d.production === "object" ? d.production : null;
  // "" = the planner is served by the dashboard itself (/plan/): same origin.
  const studio = cfg.studioUrl === "" ? "" : httpUrl(cfg.studioUrl);
  const carousel = isCarousel(d);
  if (d.status === "made" || prod) {
    const label = prod?.status ? PRODUCTION[prod.status] || prod.status : carousel ? "Studio is laying it out" : "The editor is on it";
    const link = (studio || cfg.studioUrl === "") && prod?.id != null
      ? `<a class="btn" href="${esc(`${studio}/#/production/${encodeURIComponent(prod.id)}`)}" target="_blank" rel="noopener noreferrer" title="Opens the draft on the Studio dashboard">Open the draft ↗</a>` : "";
    return `<div class="decision row"><span><strong>In the edit.</strong> ${esc(label)}${prod?.version ? ` · v${esc(prod.version)}` : ""}${prod?.updated_at ? ` <span class="muted small">(${esc(ago(prod.updated_at))})</span>` : ""}</span>${link}</div>`;
  }
  if (carousel) return makeCarouselHTML(d, owner);
  if (jobActive(job)) {
    return `<div class="decision row"><span class="small"><span class="spin"></span> <strong>Sent to the editor.</strong> ${job.state === "queued" ? esc(laptopWait()) : "Studio is starting the edit."}</span></div>`;
  }
  const failed = job?.state === "failed" ? `<span class="error-text small">Last try failed: ${esc(job.error || "unknown error")}</span>` : "";
  if (owner) {
    return `<div class="decision row"><button class="btn primary" id="plan-make" ${d.canMake ? "" : "disabled"}>Make this</button>
      <span class="muted small">${d.canMake ? "Sends it to Studio's editor, cut from the best take of each shot." : "Needs footage for at least one shot."}</span>${failed}</div>`;
  }
  return `<div class="decision row"><span class="muted small">${esc(ownerName())} sends it to the editor once the footage is in.</span>${failed}</div>`;
}

// Make this, for a carousel: Studio lays the slides out (job kind 'carousel', the owner only).
function makeCarouselHTML(d, owner) {
  const job = jobView(d.jobs.carousel);
  if (jobActive(job)) {
    return `<div class="decision row"><span class="small"><span class="spin"></span> <strong>Sent to Studio.</strong> ${job.state === "queued" ? esc(laptopWait()) : "Studio is laying out the slides."}</span></div>`;
  }
  const failed = job?.state === "failed" ? `<span class="error-text small">Last try failed: ${esc(job.error || "unknown error")}</span>` : "";
  if (owner) {
    const concepts = d.canMake ? conceptsMakeLine(d) : "";
    return `<div class="decision row"><button class="btn primary" id="plan-make" ${d.canMake ? "" : "disabled"}>Make this</button>
      <span class="muted small">${d.canMake ? "Studio lays out the slides in the Urban Makers look; the draft arrives in Review." : "Needs slides first (a cover and at least one more)."}${concepts ? ` ${esc(concepts)}` : ""}</span>${failed}</div>`;
  }
  return `<div class="decision row"><span class="muted small">${esc(ownerName())} sends it to Studio once the slides are ready.</span>${failed}</div>`;
}

// Studio suggested this post (topics.py): why now, and where it came from.
const SUGGEST_FROM = {
  transcript: "From what you said on site: the clip is in the storyboard.",
  question: "Answers a question a client asked.",
  calendar: "Timed for what's coming up in the shoot calendar.",
  reference: "Shaped by your saved references.",
  basics: "Singapore renovation basics: check the facts before it goes out.",
};
function suggestedHTML(d) {
  const s = d.suggestion && typeof d.suggestion === "object" ? d.suggestion : null;
  if (!s?.why) return "";
  const from = s.source === "transcript" && isCarousel(d) ? "From what you said on site." : SUGGEST_FROM[s.source] || "";
  return `<div class="box sugg-box"><p><strong>Studio suggested this.</strong> ${esc(s.why)}</p>
    ${from ? `<p class="muted small">${esc(from)}${s.source === "question" ? ` <a href="#/questions">Client questions</a>` : ""}</p>` : ""}</div>`;
}

function renderPanel() {
  const d = view.d;
  const owner = isOwner();
  const v = d.votes;
  const voters = [v.up.length ? `Keen: ${v.up.join(", ")}` : "", v.down.length ? `Not keen: ${v.down.join(", ")}` : ""].filter(Boolean).join(" · ") || "No votes yet";
  const field = (key, label, ph, rows = 2, max = 1000) => `<div class="field" data-saved-scope>
    <h3><label for="f-${key}">${esc(label)}</label> <span class="saved" aria-live="polite"></span></h3>
    <textarea id="f-${key}" data-idea-field="${key}" rows="${rows}" maxlength="${max}" data-orig="${esc(d[key])}" placeholder="${esc(ph)}">${esc(d[key])}</textarea></div>`;
  const dismissed = d.status === "dismissed";
  const carousel = isCarousel(d);
  // A Reel with shots stays a Reel (and a carousel with slides a carousel) unless it's emptied first.
  // (Carousels need the database update: until then "slides" isn't there to write.)
  const formats = carousel ? (d.slides.length ? ["carousel"] : PLAN_FORMATS)
    : d.shots.length || !schema.slides || !d.slidesOnline ? REEL_FORMATS : PLAN_FORMATS;
  const decision = owner
    ? `<div class="row">
        <div class="seg decision-seg" role="group" aria-label="Decision">${["go", "parked", "proposed"].map((k) =>
          `<button data-plan-decision="${k}" class="${d.decision === k ? `on ${k}` : ""}" aria-pressed="${d.decision === k}">${{ go: "Go", parked: "Park", proposed: "Undecided" }[k]}</button>`).join("")}</div>
        ${dismissed ? `<button class="btn small" data-plan-decision="restore">Put back on the board</button>`
          : `<button class="btn small ghost danger" data-plan-decision="dismissed" title="Take it off the board">Dismiss…</button>`}</div>`
    : `<p><span class="chip ${esc(d.decision)}">${esc(DECISION[d.decision] || d.decision)}</span> <span class="muted small">${esc(ownerName())} decides Go or Park. Vote to tell him what you think.</span></p>`;
  const by = nameOf(d.created_by);
  const scroll = panel().scrollTop;
  keepDrafts(panel(), () => {
    panel().innerHTML = `
      ${dismissed ? `<div class="box bad-box"><p><strong>Dismissed.</strong> It's off the board${owner ? "" : `; ${esc(ownerName())} can put it back`}.</p></div>` : ""}
      ${suggestedHTML(d)}
      <div class="field" data-saved-scope>
        <h3><label for="f-title">Planned post</label> <span class="saved" aria-live="polite"></span></h3>
        <input class="title-input" id="f-title" data-idea-field="title" maxlength="300" value="${esc(d.title)}" data-orig="${esc(d.title)}">
        <p class="muted small mt6">${by ? `Added by ${esc(by)} · ` : ""}updated ${esc(ago(d.updated_at))} · Changes save when you click away.</p>
        ${folderLineHTML(plan.projects.find((p) => p.id === d.project_id), d)}
      </div>
      <div class="meta-grid" data-saved-scope>
        <label>Arc<select data-idea-field="arc_id">${`<option value="">Unsorted</option>` + d.arcs.map((a) => `<option value="${esc(a.id)}" ${a.id === d.arc_id ? "selected" : ""}>${esc(a.title)}</option>`).join("")}</select></label>
        ${oneBrand ? "" : `<label>Account<select data-idea-field="brand">${options(ACCOUNTS, BRAND, d.brand)}</select></label>`}
        ${"post_type" in d ? `<label class="kind-pick">Kind of post<span class="kind-select">${kindDot(d.post_type)}<select data-idea-field="post_type" aria-describedby="kind-hint">${options(POST_TYPES, POST_TYPE, d.post_type || "", "Not set: Studio picks one")}</select></span></label>` : ""}
        <label>Format<select data-idea-field="format" ${carousel && slidesLocked(d) ? "disabled" : ""}>${options(formats, FORMAT, d.format)}</select></label>
        ${carousel ? `<label>Slides<span class="num-field">${esc(d.slides.length)} slides</span></label>` : `<label>Length<span class="num-field"><input type="number" min="3" max="180" step="1" data-idea-field="est_seconds" value="${esc(Math.round(d.est_seconds || 30))}" data-orig="${esc(Math.round(d.est_seconds || 30))}"> s</span></label>`}
        ${"post_type" in d ? `<p class="kind-hint muted small" id="kind-hint">${kindHint(d.post_type)}</p>` : ""}
        <span class="saved" aria-live="polite"></span>
      </div>
      <div class="box"><h3>${owner ? "Your call" : `${esc(ownerName())}'s call`}</h3>${decision}
        <div class="votes">
          <button class="vote${v.mine === 1 ? " on" : ""}" data-vote="1" aria-pressed="${v.mine === 1}" title="Worth making" aria-label="Worth making, ${v.up.length} so far">👍 ${v.up.length}</button>
          <button class="vote${v.mine === -1 ? " on down" : ""}" data-vote="-1" aria-pressed="${v.mine === -1}" title="Not worth it" aria-label="Not worth it, ${v.down.length} so far">👎 ${v.down.length}</button>
          <span class="muted small">${esc(voters)}</span>
        </div></div>
      ${carousel ? `${field("purpose", "Who it's for and why", "Who should swipe, and what they should do after")}
      ${slidesFieldHTML(d)}
      ${writeSlidesHTML(d)}` : `${field("hook", "Hook (first 3 seconds)", "The line or moment that stops the scroll")}
      ${field("purpose", "Who it's for and why", "Who should watch, and what they should think, feel or do after")}
      ${field("cta", "Call to action", "e.g. Save this for your reno", 1, 500)}
      <div class="field"><h3>Shots · ${esc(d.progress.got)}/${esc(d.progress.total)} got</h3>
        ${d.shots.length ? `<ol class="shot-list">${d.shots.map((s, i) => shotRowHTML(s, i, d.shots.length)).join("")}</ol>` : `<p class="muted">No shots yet.</p>`}
        <div class="row add-shot"><label class="sr-only" for="shot-new">New shot</label><input id="shot-new" data-draft class="grow" maxlength="1000" placeholder="Describe a new shot, e.g. Close-up: grout going into the corner">
          <button class="btn" id="shot-add">+ Add shot</button></div>
      </div>
      ${refineHTML(d)}
      ${musicHTML(d)}`}
      ${field("caption", "Caption", "The Instagram caption, with hashtags", 4, 5000)}
      ${commentsHTML(d.notes)}
      ${makeHTML(d)}`;
  });
  panel().scrollTop = scroll;
}

// What a kind of post is for, under the picker.
function kindHint(k) {
  const info = KIND_INFO[k];
  if (!info) return "Every post has a job. Pick its kind, or leave it and Studio picks one from the plan.";
  return `<strong>${esc(POST_TYPE[k])}</strong> (${esc(info.stage.toLowerCase())}): ${esc(info.job)}. Ask: ${esc(sentence(info.ask))}`;
}

function flashSaved(el) {
  const s = el.closest("[data-saved-scope]")?.querySelector(".saved");
  if (!s) return;
  s.textContent = "Saved";
  s.classList.add("show");
  clearTimeout(s.timer);
  s.timer = setTimeout(() => s.classList.remove("show"), 1600);
}

// ── Saving ──────────────────────────────────────────────────────────────────

async function saveIdeaField(el) {
  const d = view.d;
  const key = el.dataset.ideaField;
  let value = el.value;
  if (key === "arc_id") value = value ? Number(value) : null;
  else if (key === "post_type") value = value || null;
  else if (key === "est_seconds") {
    value = Number(value);
    if (!(value >= 3 && value <= 180)) { toast("Length must be between 3 and 180 seconds."); el.value = el.dataset.orig; return; }
  } else if (key === "title") {
    value = value.trim();
    if (!value) { el.value = d.title; toast("The title can't be empty."); return; }
  }
  must(await sb.from("ideas").update({ [key]: value }).eq("id", d.id));
  d[key] = value;
  if (el.dataset.orig != null) el.dataset.orig = el.value;
  flashSaved(el);
}

async function saveShotField(el) {
  const sid = Number(el.closest("[data-shot]").dataset.shot);
  let body;
  if (el.dataset.shotFilmed != null) body = { filmed_at: el.checked ? new Date().toISOString() : null };
  else {
    const key = el.dataset.shotField;
    let value = el.value;
    if (["camera", "framing", "kind", "shoot_on"].includes(key)) value = value || null;
    else if (key === "phase_id") value = value ? Number(value) : null;
    else if (key === "seconds") {
      value = value === "" ? null : Number(value);
      if (value != null && !(value > 0 && value <= 120)) { toast("Seconds must be between 0 and 120."); el.value = el.dataset.orig; return; }
    } else if (key === "description") {
      value = value.trim();
      if (!value) { toast("Describe the shot (or delete it)."); el.value = el.dataset.orig; return; }
    }
    body = { [key]: value };
  }
  must(await sb.from("shots").update(body).eq("id", sid));
  if (el.dataset.orig != null) el.dataset.orig = el.value;
  flashSaved(el);
}

// Text saves when you click away; selects and ticks save straight away.
panel().addEventListener("focusout", async (e) => {
  const el = e.target;
  if (isTextField(el) && el.dataset.slideField && el.value !== el.dataset.orig && view.d) {
    try {
      if (await saveSlideField(el, view.d)) flashSaved(el);
      await loadIdea();
    } catch (err) { toast(`Couldn't save: ${err.message}`); }
  } else if (isTextField(el) && (el.dataset.ideaField || el.dataset.shotField) && el.value !== el.dataset.orig) {
    try {
      if (el.dataset.ideaField) await saveIdeaField(el); else await saveShotField(el);
      await loadIdea();
    } catch (err) { toast(`Couldn't save: ${err.message}`); }
  }
  // Catch up on changes held back while typing.
  setTimeout(() => { if (view.pending && !typingIn(panel())) render(); }, 0);
});
const isTextField = (el) => el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && el.type !== "checkbox");
panel().addEventListener("change", async (e) => {
  const el = e.target;
  if (isTextField(el)) return;
  try {
    if (el.dataset.ideaField) await saveIdeaField(el);
    else if (el.dataset.shotField != null || el.dataset.shotFilmed != null) await saveShotField(el);
    else if (el.dataset.slideField && view.d) { if (await saveSlideField(el, view.d)) flashSaved(el); }
    else return;
    await loadIdea();
  } catch (err) { toast(`Couldn't save: ${err.message}`); }
});

async function addShot() {
  const box = $("#shot-new");
  const description = box.value.trim();
  if (!description) { box.focus(); return; }
  const d = view.d;
  const pos = Math.max(0, ...d.shots.map((s) => s.position || 0)) + 1;
  must(await sb.from("shots").insert({ idea_id: d.id, description, position: pos }));
  box.value = "";
  await loadIdea(true);
  $("#shot-new")?.focus();
  toast("Shot added.");
}

async function addComment() {
  const box = $("#comment-body");
  const body = box.value.trim();
  if (!body) { box.focus(); return; }
  must(await sb.from("comments").insert({ idea_id: view.d.id, body }));
  box.value = "";
  await loadIdea(true);
  toast("Comment posted.");
}

async function startRefine() {
  const d = view.d;
  const box = $("#refine-request");
  const request = box.value.trim();
  let res;
  try {
    res = await invoke("plan-develop", { body: { idea_id: d.id, ...(request ? { request } : {}) } });
  } catch (err) { toast(`Couldn't start: ${err.message}`, 6000); return; }
  box.value = "";
  d.jobs.develop = { id: res?.job_id, kind: "develop", runner: "cloud", state: "running", started_at: new Date().toISOString() };
  render(true);
  toast(isCarousel(d) ? "Claude is writing the slides. About a minute." : "Claude is refining it. About a minute.");
}

async function moveShot(shotId, delta) {
  const order = view.d.shots.map((s) => s.id);
  const k = order.indexOf(shotId), j = k + delta;
  if (k < 0 || j < 0 || j >= order.length) return;
  [order[k], order[j]] = [order[j], order[k]];
  await Promise.all(order.map((sid, n) => {
    const s = view.d.shots.find((x) => x.id === sid);
    return s.position === n + 1 ? null : sb.from("shots").update({ position: n + 1 }).eq("id", sid).then(must);
  }));
}

panel().addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  const d = view.d;
  if (!d) return;
  const ds = t.dataset;
  const shotId = Number(t.closest("[data-shot]")?.dataset.shot);
  try {
    if (await slidesClick(t, d)) { await loadIdea(true); return; }
    if (ds.attach != null) { openPicker(shotId); return; }
    if (t.id === "shot-add") { await addShot(); return; }
    if (t.id === "comment-add") { await addComment(); return; }
    if (t.id === "refine-go") { await startRefine(); return; }
    if (ds.planDecision) {
      const k = ds.planDecision;
      if (k === "dismissed") {
        if (!confirm(`Dismiss "${d.title}"? It comes off the board.`)) return;
        must(await sb.from("ideas").update({ status: "dismissed" }).eq("id", d.id));
        toast("Dismissed.");
        closeIdea();
        return;
      }
      if (k === "restore") {
        must(await sb.from("ideas").update({ status: "new" }).eq("id", d.id));
        toast("Back on the board.");
      } else {
        must(await sb.from("ideas").update({ decision: k }).eq("id", d.id));
        toast({ go: "Marked Go.", parked: "Parked.", proposed: "Back to undecided." }[k]);
      }
    } else if (ds.vote) {
      const vote = Number(ds.vote);
      if (d.votes.mine === vote) must(await sb.from("votes").delete().eq("idea_id", d.id).eq("user_id", state.me.id));
      else must(await sb.from("votes").upsert({ idea_id: d.id, user_id: state.me.id, vote }, { onConflict: "idea_id,user_id" }));
    } else if (t.id === "music-shortlist") {
      must(await sb.from("jobs").insert({ kind: "music", runner: "laptop", idea_id: d.id, project_id: d.project_id, state: "queued", payload: {} }));
      toast("Asked Studio for a music shortlist.");
    } else if (ds.musicPick != null) {
      const track = d.music.shortlist[Number(ds.musicPick)];
      const on = d.music.chosen?.recording_id === track.recording_id;
      must(await sb.from("ideas").update({ music: { ...d.music, chosen: on ? null : track } }).eq("id", d.id));
      toast(on ? "Unpicked." : `Picked ${track.title}.`);
    } else if (ds.shotMove) {
      await moveShot(shotId, Number(ds.shotMove));
    } else if (ds.shotDelete != null) {
      const s = d.shots.find((x) => x.id === shotId);
      const n = d.shots.indexOf(s) + 1;
      if (s.takes.length) {
        if (!confirm(`Shot ${n} has ${plural(s.takes.length, "take")} attached. Delete the shot anyway? The clips stay in the library.`)) return;
      } else if (s.description && !confirm(`Delete shot ${n}?`)) return;
      must(await sb.from("shots").delete().eq("id", shotId));
      toast("Shot deleted.");
    } else if (ds.takeRemove) {
      must(await sb.from("takes").delete().eq("id", Number(ds.takeRemove)));
      toast("Take removed. The clip stays in the library.");
    } else if (ds.commentDelete) {
      if (!confirm("Delete this comment?")) return;
      must(await sb.from("comments").delete().eq("id", Number(ds.commentDelete)));
    } else if (t.id === "plan-make") {
      const caption = $("#f-caption")?.value ?? d.caption;
      if (caption !== d.caption) must(await sb.from("ideas").update({ caption }).eq("id", d.id));
      const kind = isCarousel(d) ? "carousel" : "make";
      must(await sb.from("jobs").insert({ kind, runner: "laptop", idea_id: d.id, project_id: d.project_id, state: "queued", payload: { caption } }));
      toast(kind === "carousel" ? "Sent to Studio to lay out the slides." : "Sent to Studio's editor.");
    } else return;
    await loadIdea(true);
  } catch (err) {
    toast(`Couldn't do that: ${err.message}`);
  }
});

panel().addEventListener("keydown", (e) => {
  const ctrlEnter = e.key === "Enter" && (e.ctrlKey || e.metaKey);
  if (e.target.id === "comment-body" && ctrlEnter) {
    e.preventDefault();
    addComment().catch((err) => toast(`Couldn't post: ${err.message}`));
  } else if (e.target.id === "shot-new" && e.key === "Enter") {
    e.preventDefault();
    addShot().catch((err) => toast(`Couldn't add: ${err.message}`));
  } else if (e.target.id === "refine-request" && ctrlEnter) {
    e.preventDefault();
    startRefine();
  } else if (e.key === "Enter" && e.target.matches("input[data-idea-field], input[data-shot-field], input[data-slide-field]")) {
    e.target.blur();
  }
});

// ── Attach footage: pick a clip for a shot ──────────────────────────────────

let pickerTimer;
function openPicker(shotId) {
  const d = view.d;
  const s = d.shots.find((x) => x.id === shotId);
  const n = d.shots.indexOf(s) + 1;
  openModal(`<h2 id="modal-title">Attach footage</h2>
    <p class="muted">To shot ${n}: ${esc(s.description)}</p>
    <input type="search" id="picker-q" class="search" placeholder="Search clips by title" aria-label="Search clips">
    <div id="picker-results" aria-live="polite"><p class="muted"><span class="spin"></span> Loading clips…</p></div>`, async (e) => {
    const btn = e.target.closest("[data-pick-clip]");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    const { error } = await sb.from("takes").insert({ shot_id: shotId, clip_id: Number(btn.dataset.pickClip), source: "manual" });
    if (error) {
      btn.disabled = false;
      toast(error.code === "23505" ? "That clip is already attached to this shot." : `Couldn't attach it: ${error.message}`);
      return;
    }
    closeModal();
    toast("Footage attached.");
    await loadIdea(true);
  }, true, "sheet");
  const search = () => loadPicker(d.project_id, s).catch((err) => { if ($("#picker-results")) $("#picker-results").innerHTML = `<p class="error-text">${esc(err.message)}</p>`; });
  $("#picker-q").addEventListener("input", () => { clearTimeout(pickerTimer); pickerTimer = setTimeout(search, 250); });
  search();
}

async function loadPicker(projectId, shot) {
  const q = $("#picker-q")?.value.trim() || "";
  const base = () => {
    let r = sb.from("clips").select("id,title,recorded_at,duration,project_id,verdict,thumb_path")
      .order("recorded_at", { ascending: false, nullsFirst: false }).order("id", { ascending: false }).limit(60);
    if (q) r = r.ilike("title", `%${q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`);
    return r;
  };
  const [mine, rest] = await Promise.all([
    base().eq("project_id", projectId).then(must),
    base().or(`project_id.is.null,project_id.neq.${Number(projectId)}`).then(must),
  ]);
  await signPaths([...mine, ...rest].map((c) => c.thumb_path)).catch(() => {});
  if (!$("#picker-results") || ($("#picker-q")?.value.trim() || "") !== q) return;
  const attached = new Set(shot.takes.map((t) => t.clip_id));
  const tile = (c) => {
    const thumb = stillUrl(c.thumb_path);
    return `<button class="pick" data-pick-clip="${esc(c.id)}" ${attached.has(c.id) ? "disabled" : ""} aria-label="${esc(c.title || `Clip ${c.id}`)}${attached.has(c.id) ? " (attached)" : ""}">
    ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="pick-none">No still yet</span>`}
    <span class="pick-chips">${retakeBadge(c)}${attached.has(c.id) ? `<span class="chip">Attached</span>` : ""}</span>
    <span class="pick-text"><span class="pick-title">${esc(c.title || `Clip ${c.id}`)}</span>
      <span class="pick-meta">${c.duration ? esc(mmss(c.duration)) : ""}${c.recorded_at ? ` · ${esc(shortDate(c.recorded_at))}` : ""}</span></span>
  </button>`;
  };
  $("#picker-results").innerHTML = !mine.length && !rest.length
    ? `<p class="muted">${q ? "No clips match." : "No clips yet. Studio adds them here as it tags new footage."}</p>`
    : (mine.length ? `<h3>In this project</h3><div class="picker-grid">${mine.map(tile).join("")}</div>` : "")
      + (rest.length ? `<h3>${mine.length ? "Other recent clips" : "Recent clips"}</h3><div class="picker-grid">${rest.map(tile).join("")}</div>` : "");
}

// ── Previous / next idea on the board ───────────────────────────────────────

function updateArrows() {
  const i = plan.order.indexOf(view.id);
  $("#prev").disabled = i <= 0;
  $("#next").disabled = i < 0 || i >= plan.order.length - 1;
  renderHead();
}
// The next post replaces this one in the history: Back still goes to the board.
export function step(delta) {
  // The address is ahead of the page while the next post loads (J pressed twice quickly).
  const now = Number(location.hash.match(/^#\/idea\/(\d+)$/)?.[1]) || view.id;
  const i = plan.order.indexOf(now);
  const next = i >= 0 ? plan.order[i + delta] : null;
  if (next) location.replace(`#/idea/${next}`);
}
$("#prev").addEventListener("click", () => step(-1));
$("#next").addEventListener("click", () => step(1));
$("#close").addEventListener("click", () => closeIdea());
export { updateArrows };
