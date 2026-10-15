// Studio dashboard: a Reels-style grid of every video by stage, and a review
// screen to leave timed notes and approve or send drafts back for changes.
"use strict";

const STAGES = [
  { key: "review", label: "To review", hint: "Drafts waiting for you. Leave notes on the moments that need work, then approve it or send it back. The next draft opens straight away." },
  { key: "ideas", label: "Ideas", hint: "Posts Claude planned from your library. Open one to play its segments, edit the caption, then choose Make this to send it to the editor." },
  { key: "producing", label: "Being edited", hint: "Edits in progress, including drafts you sent back. They return to To review when the new version renders." },
  { key: "library", label: "Library", hint: "Raw clips, tagged by Claude. Fix a wrong account or format, or leave a note on how a clip should be used." },
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
const POLL_MS = 10000;

const $ = (sel, root = document) => root.querySelector(sel);
const state = { items: [], stage: null, brand: "", list: [], current: null, detail: null, lastSync: 0, ideasJob: {} };

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

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
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
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function chips(item, short = false) {
  const out = [];
  if (item.brand) out.push(`<span class="chip ${esc(item.brand)}">${esc(BRAND[item.brand] || item.brand)}</span>`);
  if (item.kind === "production" && item.version) out.push(`<span class="chip">v${item.version}</span>`);
  if (item.open_notes) out.push(`<span class="chip notes">${item.open_notes} note${item.open_notes > 1 ? "s" : ""}</span>`);
  const status = item.kind === "clip" && item.status === "tagged" ? (item.reviewed ? "Checked" : null)
    : (short && STATUS_SHORT[item.status]) || STATUS[item.status];
  if (item.kind === "clip" && item.usable === 0) out.push(`<span class="chip failed">Unusable</span>`);
  if (status) out.push(`<span class="chip status ${esc(item.status)}">${esc(status)}</span>`);
  return out.join("");
}

// ── Board ───────────────────────────────────────────────────────────────────

function visible(stage) {
  return state.items.filter((i) => i.stage === stage && (!state.brand || i.brand === state.brand));
}

function renderTabs() {
  $("#tabs").innerHTML = STAGES.map((s) => {
    const n = visible(s.key).length;
    const hot = s.key === "review" && n ? " hot" : "";
    return `<button data-stage="${s.key}" class="${s.key === state.stage ? "on" : ""}">${s.label}<span class="count${hot}">${n}</span></button>`;
  }).join("");
  $("#stage-hint").textContent = STAGES.find((s) => s.key === state.stage).hint;
  renderToolbar();
}

function renderToolbar() {
  $("#toolbar").hidden = state.stage !== "ideas";
  const job = state.ideasJob || {};
  $("#suggest").disabled = !!job.running;
  $("#ideas-status").textContent = job.running
    ? "Claude is reading your library and planning posts. This takes a minute or two."
    : job.error ? `Last run failed: ${job.error}` : "";
}

function tileHTML(item, index) {
  const media = item.thumb
    ? `<img src="${esc(item.thumb)}" alt="" loading="lazy">`
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
  renderGrid();
}

async function sync() {
  try {
    const data = await api("/api/board");
    state.lastSync = Date.now();
    const signature = JSON.stringify(data.items);
    const wasRunning = state.ideasJob?.running;
    state.items = data.items;
    state.ideasJob = data.ideas_job || {};
    if (data.costs) {
      const c = data.costs;
      $("#costs").textContent = `API this month: ${money(c.usd)}`;
      $("#costs").title = Object.entries(c.by_kind).map(([k, v]) => `${k}: ${money(v.usd)} (${v.calls} calls)`).join("\n");
    }
    if (wasRunning && !state.ideasJob.running && state.ideasJob.count != null) {
      toast(state.ideasJob.count ? `${state.ideasJob.count} new idea${state.ideasJob.count > 1 ? "s" : ""}` : "No new ideas this time. Try a different focus or add more clips.");
    }
    renderToolbar();
    if (!state.stage) {
      // First load: open on whatever needs attention.
      const saved = recall("studio.stage");
      state.stage = visible("review").length ? "review" : (STAGES.some((s) => s.key === saved) ? saved : "library");
    }
    // Re-render only on change, so a hover preview isn't cut off every poll.
    if (signature !== state.signature) {
      state.signature = signature;
      render();
    }
    await refreshOpenItem();
  } catch (e) {
    $("#updated").textContent = `Can't reach the laptop (${e.message})`;
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
  if (!item || !item.video) return;
  let video = tile.querySelector("video");
  if (!video) {
    video = Object.assign(document.createElement("video"), { src: item.video, muted: true, loop: true, playsInline: true, preload: "metadata" });
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
  render();
});
$("#brand-filter").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-brand]");
  if (!btn) return;
  state.brand = btn.dataset.brand;
  document.querySelectorAll("#brand-filter button").forEach((b) => b.classList.toggle("on", b === btn));
  render();
});

// ── Viewer ──────────────────────────────────────────────────────────────────

const player = () => $("#player-frame video");

async function openItem(item) {
  if (!item) return;
  state.current = { kind: item.kind, id: item.id };
  const route = `#/${item.kind}/${encodeURIComponent(item.id)}`;
  if (location.hash !== route) history.pushState(null, "", route);
  $("#viewer").hidden = false;
  document.body.style.overflow = "hidden";
  renderPlayer(item);
  await loadDetail();
  updateArrows();
}

function closeViewer() {
  player()?.pause();
  state.current = null;
  state.detail = null;
  $("#viewer").hidden = true;
  document.body.style.overflow = "";
  if (location.hash) history.pushState(null, "", location.pathname);
}

function renderPlayer(item) {
  const frame = $("#player-frame");
  if (item.video) {
    const original = item.original && recall("studio.original") === "1";
    frame.innerHTML = `<video controls autoplay playsinline src="${esc(original ? item.original : item.video)}" ${item.thumb ? `poster="${esc(item.thumb)}"` : ""}></video>
      ${item.original ? `<button class="quality${original ? " on" : ""}" id="quality" title="Switch between the 1080p preview and your untouched file">${original ? "Original" : "1080p preview"}</button>` : ""}`;
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
  video.src = original ? item.original : item.video;
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
  if (kind === "inbox") {
    const item = state.items.find((i) => i.kind === "inbox" && i.id === id);
    state.detail = item || null;
  } else {
    state.detail = await api(`/api/${kind}/${id}`);
  }
  renderPanel();
}

async function refreshOpenItem() {
  if (!state.current || state.current.kind === "inbox") return;
  const before = state.detail;
  const fresh = await api(`/api/${state.current.kind}/${state.current.id}`).catch(() => null);
  if (!fresh || !before) return;
  if (fresh.status !== before.status || fresh.version !== before.version) {
    state.detail = fresh;
    if (fresh.video !== before.video) renderPlayer(fresh);
    renderPanel();
    if (fresh.status === "ready") toast(`v${fresh.version} is ready to review`);
  }
}

function notesHTML(notes, currentVersion, isProduction) {
  if (!notes.length) return `<p class="muted">No notes yet.</p>`;
  return `<ul class="notes">${notes.map((n) => {
    const stamp = n.at_seconds == null ? "" : `<button class="stamp" data-seek="${n.at_seconds}">${mmss(n.at_seconds)}</button>`;
    const tags = [
      isProduction && n.version !== currentVersion ? `v${n.version}` : "",
      n.status === "addressed" ? "addressed" : "",
      n.remember ? "in playbook" : "",
    ].filter(Boolean).join(" · ");
    return `<li class="${n.status}">${stamp}${esc(n.body)}${tags ? `<span class="tag">${tags}</span>` : ""}</li>`;
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

function composerHTML(placeholder) {
  return `<div>
    <h3>Add a note</h3>
    <textarea id="note-body" placeholder="${esc(placeholder)}"></textarea>
    <div class="row" style="margin-top:8px">
      <label class="check"><input type="checkbox" id="note-pin" checked> <span id="pin-label">Pin to this moment</span></label>
      <label class="check" title="Adds it to the playbook the editor follows on every future edit"><input type="checkbox" id="note-remember"> Remember for future edits</label>
      <button class="btn" id="note-add" style="margin-left:auto">Add note</button>
    </div>
  </div>`;
}

function renderPanel() {
  const d = state.detail;
  const panel = $("#panel");
  if (!d) { panel.innerHTML = `<p class="muted">This item is gone.</p>`; return; }
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
      ${d.status === "new" ? `<div class="decision row">
        <button class="btn primary" id="idea-make">Make this</button>
        <button class="btn" id="idea-dismiss">Dismiss</button>
      </div>` : `<div class="box"><p>Already sent to the editor.</p></div>`}`;
    return;
  }

  if (d.kind === "production") {
    const canDecide = d.status === "ready";
    const openNow = d.notes.filter((n) => n.status === "open").length;
    panel.innerHTML = `${head}
      ${d.status === "failed" ? `<div class="box"><p><strong>The edit failed:</strong> ${esc(d.error)}</p>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="retry-edit">Retry edit</button></div></div>` : ""}
      ${d.edit_summary ? `<div><h3>What the editor did</h3><p>${esc(d.edit_summary)}</p></div>` : ""}
      ${musicHTML(d.music)}
      ${qaHTML(d.qa)}
      ${shotsHTML("The editor wished it had", d.missing_shots)}
      ${d.idea ? `<div><h3>Brief</h3><p>${esc(d.idea)}</p></div>` : ""}
      ${d.cost ? `<p class="muted">API cost so far: ${money(d.cost)}${d.graphics ? ` · ${d.graphics} graphic${d.graphics > 1 ? "s" : ""}` : ""}</p>` : ""}
      ${d.sources.length ? `<div><h3>Made from</h3><p class="muted">${d.sources.map((s) => esc(s.title)).join(", ")}</p></div>` : ""}
      ${canDecide || d.status === "failed" ? "" : `<div class="box"><p>${d.status === "revising" || d.status === "producing" || d.status === "planned"
        ? "The editor is working on this. It comes back to To review when the new version renders."
        : `${esc(STATUS[d.status] || d.status)}.`}</p></div>`}
      <div><h3>Notes</h3>${notesHTML(d.notes, d.version, true)}</div>
      ${composerHTML("What should change? e.g. \"Cut the first two seconds\", \"Music too loud here\"")}
      <div><h3>Post caption</h3><textarea id="caption" placeholder="The caption you'll post. You have the final say.">${esc(d.caption)}</textarea>
        ${canDecide ? "" : `<div class="row" style="margin-top:8px"><button class="btn" id="caption-save">Save caption</button></div>`}</div>
      ${canDecide ? `<div class="decision row">
        <button class="btn primary" id="decide-changes" ${openNow ? "" : "disabled title=\"Add a note first\""}>Request changes${openNow ? ` (${openNow})` : ""}</button>
        <button class="btn good" id="decide-approve">Approve</button>
        <button class="btn" id="decide-reject">Reject</button>
      </div>` : ""}`;
  } else {
    const option = (values, labels, current) => values.map((v) => `<option value="${v}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
    panel.innerHTML = `${head}
      ${d.status === "failed" ? `<div class="box"><p><strong>Failed:</strong> ${esc(d.error)}</p><p class="muted">The worker retries it next time it starts.</p></div>` : ""}
      ${d.summary ? `<div><h3>Summary</h3><p>${esc(d.summary)}</p></div>` : ""}
      ${d.brand_reason ? `<div><h3>Why this account</h3><p>${esc(d.brand_reason)}</p></div>` : ""}
      ${d.suggested_hook ? `<div><h3>Suggested hook</h3><p>"${esc(d.suggested_hook)}"</p></div>` : ""}
      ${d.moments.length ? `<div><h3>Best moments</h3><ul class="moments">${d.moments.map((m) =>
        `<li><button data-seek="${m.start}"><strong>${mmss(m.start)}–${mmss(m.end)}</strong> ${esc(m.description)}</button></li>`).join("")}</ul></div>` : ""}
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
      ${d.quality_notes ? `<p class="muted">Quality: ${esc(d.quality_notes)}</p>` : ""}
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
  if (i >= 0 && state.list[i + delta]) openItem(state.list[i + delta]);
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

// Panel actions (event delegation, so re-rendering the panel keeps them working).
$("#panel").addEventListener("click", async (e) => {
  const segment = e.target.closest("[data-segment]");
  if (segment && state.detail?.segments) {
    // Play just that segment: the #t=start,end media fragment stops at the end.
    const s = state.detail.segments[Number(segment.dataset.segment)];
    player().src = `${s.video}#t=${s.start},${s.end}`;
    player().play().catch(() => {});
    return;
  }
  const seek = e.target.closest("[data-seek]");
  if (seek && player()) {
    player().currentTime = Number(seek.dataset.seek);
    player().play().catch(() => {});
    return;
  }
  const id = e.target.id;
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
    }
    else if (id === "decide-changes") {
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
    } else if (id === "retry-edit") {
      await api(`/api/production/${d.id}/decision`, { decision: "retry", caption: $("#caption").value });
      toast("Queued for the editor again.");
      await loadDetail();
      sync();
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

async function addNote() {
  const body = $("#note-body").value.trim();
  if (!body) { $("#note-body").focus(); return; }
  const video = player();
  const at = $("#note-pin").checked && video ? video.currentTime : null;
  await api(`/api/${state.current.kind}/${state.current.id}/notes`, {
    body, at_seconds: at, remember: $("#note-remember").checked,
  });
  await loadDetail();
  sync();
  toast("Note added.");
  $("#note-body")?.focus();
}

$("#panel").addEventListener("keydown", (e) => {
  if (e.target.id === "note-body" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    addNote().catch((err) => toast(`Couldn't save: ${err.message}`));
  }
});

document.addEventListener("keydown", (e) => {
  if ($("#viewer").hidden) return;
  const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName);
  if (e.key === "Escape") { if (typing) e.target.blur(); else closeViewer(); return; }
  if (typing) return;
  if (e.key === "j" || e.key === "J") step(1);
  else if (e.key === "k" || e.key === "K") step(-1);
  else if (e.key === " " && player()) { e.preventDefault(); player().paused ? player().play() : player().pause(); }
});

$("#close").addEventListener("click", closeViewer);
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

// Deep links (#/production/12) and the browser's back button.
async function routeFromHash() {
  // #/tab/library etc.: links in Telegram digests open a tab directly.
  const tab = location.hash.match(/^#\/tab\/(\w+)$/);
  if (tab && STAGES.some((s) => s.key === tab[1])) {
    if (!$("#viewer").hidden) closeViewer();
    state.stage = tab[1];
    render();
    return;
  }
  const m = location.hash.match(/^#\/(clip|production|inbox|idea)\/(.+)$/);
  if (!m) { if (!$("#viewer").hidden) closeViewer(); return; }
  const id = decodeURIComponent(m[2]);
  const item = state.items.find((i) => i.kind === m[1] && String(i.id) === id);
  if (item) {
    state.stage = item.stage;
    render();
    openItem(item);
  }
}
window.addEventListener("popstate", routeFromHash);

sync().then(routeFromHash);
setInterval(sync, POLL_MS);
