// A carousel's slides on the idea page (CONTRACT "Carousels"): a preview of every slide on the
// left, and on the right the slides line by line (edit the words, reorder, add, delete, and pick
// each slide's picture), "Write slides" (Claude, plan-develop) and Make this. Pictures come only
// from Urban Makers' own footage (a still Studio takes from a clip) or photos uploaded from the
// phone; AI concept images come in a later update.
import {
  $, esc, sb, must, toast, plural, mmss, shortDate, signPaths, stillUrl, jobView, jobActive, retakeBadge,
  openModal, closeModal, laptopWait, SLIDE_ROLE, PICTURE,
} from "./core.js";
import { addPhoto, canUpload, studioApi } from "./upload.js";

export const MAX_SLIDES = 12;
const ROLES = ["cover", "point", "ask"];
const newKey = () => Math.random().toString(36).slice(2, 12);

// The stored slides, made safe to show and edit.
export function prepSlides(raw) {
  return (Array.isArray(raw) ? raw : []).slice(0, MAX_SLIDES).map((s) => ({
    key: String(s?.key || newKey()).slice(0, 40),
    role: ROLES.includes(s?.role) ? s.role : "point",
    heading: String(s?.heading || ""),
    body: String(s?.body || ""),
    image: s?.image && typeof s.image === "object" ? { ...s.image } : { source: "text" },
  }));
}
export const slidePaths = (slides) => slides.map((s) => s.image?.thumb_path).filter(Boolean);
const pictured = (s) => s.image?.source && s.image.source !== "text";
// Made (or being made): the slides change in Review now, with notes.
export const slidesLocked = (d) => d.status === "made" || !!d.production;

// ── The preview (left) ───────────────────────────────────────────────────────

function miniHTML(s, i, n) {
  const thumb = stillUrl(s.image?.thumb_path);
  return `<button class="cs-mini ${esc(s.role)}${thumb ? " has-pic" : ""}" data-goto-slide="${esc(s.key)}" aria-label="Slide ${i + 1}: ${esc(s.heading || "no heading")}. Edit it">
    ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : ""}
    <span class="cs-num">${i + 1}/${n}</span>
    ${pictured(s) && !thumb ? `<span class="cs-pic-wait">${esc(PICTURE[s.image.source] || "Picture")}</span>` : ""}
    <span class="cs-text"><span class="cs-h">${esc(s.heading)}</span>${s.body ? `<span class="cs-b">${esc(s.body)}</span>` : ""}</span>
  </button>`;
}

export function slidesStoryHTML(d) {
  const job = jobView(d.jobs.develop);
  const writing = jobActive(job);
  const n = d.slides.length;
  const pics = d.slides.filter(pictured).length;
  return `<div class="storyboard carousel-board">
    <div class="sb-head"><h2 id="sb-title">Slides</h2>
      <span class="muted">${n ? `${plural(n, "slide")}${pics ? ` · ${plural(pics, "picture")}` : ""}` : "Carousel"}</span>
      ${writing && n ? `<span class="sb-drafting"><span class="spin"></span> Claude is rewriting them…</span>` : ""}</div>
    ${n ? `<div class="cs-strip">${d.slides.map((s, i) => miniHTML(s, i, n)).join("")}</div>`
      : writing ? `<div class="sb-empty"><p><span class="spin"></span> The slides are being written…</p><p class="muted">Claude is writing the cover, the points and the ask. About a minute; they appear here by themselves.</p></div>`
        : `<div class="sb-empty"><p>No slides yet.</p><p class="muted">Use Write slides on the right, or add them yourself.</p></div>`}
  </div>`;
}

// ── The editor (right) ───────────────────────────────────────────────────────

function pictureLine(s, d) {
  const img = s.image || {};
  if (img.source === "clip") {
    const title = d.clipTitles?.get(img.clip_id);
    return `${PICTURE.clip}: ${title ? `${title}, ` : `clip ${img.clip_id}, `}${mmss(img.at || 0)}`;
  }
  return PICTURE[img.source] || PICTURE.text;
}

function slideRowHTML(s, i, d, locked) {
  const n = d.slides.length;
  const thumb = stillUrl(s.image?.thumb_path);
  const id = (f) => `sl-${esc(s.key)}-${f}`;
  const dis = locked ? "disabled" : "";
  return `<li class="slide-row ${esc(s.role)}" id="slide-${esc(s.key)}" data-slide-key="${esc(s.key)}" data-saved-scope>
    <div class="shot-top">
      <span class="shot-num" aria-hidden="true">${i + 1}</span>
      <label class="sr-only" for="${id("role")}">Slide ${i + 1} is the</label>
      <select id="${id("role")}" data-slide-field="role" ${dis}>${ROLES.map((r) => `<option value="${r}" ${r === s.role ? "selected" : ""}>${SLIDE_ROLE[r]}</option>`).join("")}</select>
      <span class="saved" aria-live="polite"></span>
      ${locked ? "" : `<span class="shot-tools">
        <button class="icon-btn" data-slide-move="-1" ${i === 0 ? "disabled" : ""} title="Move up" aria-label="Move slide ${i + 1} up">↑</button>
        <button class="icon-btn" data-slide-move="1" ${i === n - 1 ? "disabled" : ""} title="Move down" aria-label="Move slide ${i + 1} down">↓</button>
        <button class="icon-btn danger" data-slide-delete title="Delete slide" aria-label="Delete slide ${i + 1}">×</button></span>`}
    </div>
    <label class="sr-only" for="${id("heading")}">Slide ${i + 1} heading</label>
    <input class="slide-heading" id="${id("heading")}" data-slide-field="heading" maxlength="200" value="${esc(s.heading)}" data-orig="${esc(s.heading)}" placeholder="${s.role === "cover" ? "The hook: what makes someone swipe" : s.role === "ask" ? "The ask, e.g. Planning a reno?" : "One idea, in a few words"}" ${dis}>
    <label class="sr-only" for="${id("body")}">Slide ${i + 1} words</label>
    <textarea id="${id("body")}" data-slide-field="body" rows="${Math.min(6, Math.max(2, Math.ceil(s.body.length / 38)))}" maxlength="800" data-orig="${esc(s.body)}" placeholder="The words under it (optional)" ${dis}>${esc(s.body)}</textarea>
    <div class="slide-pic">
      <span class="slide-thumb">${thumb ? `<img src="${esc(thumb)}" alt="">` : `<span class="slide-none">${pictured(s) ? "Preview soon" : "Text"}</span>`}</span>
      <div class="slide-pic-info">
        <span class="small">${esc(pictureLine(s, d))}</span>
        ${s.image?.note ? `<span class="muted small">Picture idea: ${esc(s.image.note)}</span>` : ""}
        ${locked ? "" : `<span class="row tight slide-pic-btns">
          <button type="button" class="btn small" data-slide-pic="clip" aria-label="Slide ${i + 1}: a still from your footage">From footage</button>
          <button type="button" class="btn small" data-slide-pic="photo" aria-label="Slide ${i + 1}: upload a photo">Photo</button>
          ${pictured(s) ? `<button type="button" class="btn small ghost" data-slide-pic="text" aria-label="Slide ${i + 1}: text only">Text only</button>` : ""}
          <button type="button" class="btn small ghost" disabled title="AI concept images come in the next update">AI concept · soon</button>
        </span>`}
      </div>
    </div>
  </li>`;
}

export function slidesFieldHTML(d) {
  const locked = slidesLocked(d);
  return `<div class="field"><h3>Slides · ${esc(d.slides.length)}</h3>
    ${locked ? `<p class="muted small">These slides are in Review now. To change them, send the draft back with notes there.</p>` : `<p class="muted small">A cover, 4 to 8 points and the ask. Changes save when you click away.</p>`}
    ${d.slides.length ? `<ol class="shot-list slide-list">${d.slides.map((s, i) => slideRowHTML(s, i, d, locked)).join("")}</ol>` : `<p class="muted">No slides yet.</p>`}
    ${locked || d.slides.length >= MAX_SLIDES ? "" : `<div class="row add-shot"><button class="btn" id="slide-add">+ Add slide</button></div>`}
  </div>`;
}

export function writeSlidesHTML(d) {
  const job = jobView(d.jobs.develop);
  const active = jobActive(job);
  const changes = job?.result?.changes;
  if (slidesLocked(d)) return "";
  return `<div class="box refine"><h3>Write slides with Claude</h3>
    <label class="sr-only" for="refine-request">What should Claude change</label>
    <textarea id="refine-request" data-draft rows="2" maxlength="2000" ${active ? "disabled" : ""} placeholder="${d.slides.length ? "What should change? e.g. “make slide 3 simpler”, “end with a site visit”." : "Anything Claude should know? Leave empty to write it from the idea."}"></textarea>
    <div class="row mt8"><button class="btn primary" id="refine-go" ${active ? "disabled" : ""} title="About a minute; costs a few cents">${active ? "Writing…" : d.slides.length ? "Rewrite slides" : "Write slides"}</button>
      ${active ? `<span class="muted small"><span class="spin"></span> ${job.state === "queued" ? "Starting…" : "Claude is writing the slides."}</span>` : ""}</div>
    <p class="muted small">A cover, 4 to 8 points and the ask, plus the caption, in your voice. The ask only uses your offers. Pictures you picked stay.</p>
    ${job?.state === "done" && changes ? `<p class="refine-result"><strong>Last time:</strong> ${esc(changes)}</p>` : ""}
    ${job?.state === "failed" ? `<p class="error-text">Writing failed: ${esc(job.error || "unknown error")}</p>` : ""}
  </div>`;
}

// ── Saving ───────────────────────────────────────────────────────────────────

async function save(d, slides) {
  must(await sb.from("ideas").update({ slides }).eq("id", d.id));
  d.slides = slides;
}
const copy = (d) => d.slides.map((s) => ({ ...s, image: { ...s.image } }));

// A text field or the role select of one slide.
export async function saveSlideField(el, d) {
  const key = el.closest("[data-slide-key]")?.dataset.slideKey;
  const field = el.dataset.slideField;
  const slides = copy(d);
  const s = slides.find((x) => x.key === key);
  if (!s || !field) return false;
  s[field] = field === "role" ? (ROLES.includes(el.value) ? el.value : "point") : el.value.trim();
  await save(d, slides);
  if (el.dataset.orig != null) el.dataset.orig = el.value;
  return true;
}

// Set one slide's picture (by its key), on the slides as they are now in the planner.
async function setPicture(ideaId, key, image) {
  const row = must(await sb.from("ideas").select("id,slides").eq("id", ideaId).maybeSingle());
  const slides = prepSlides(row?.slides);
  const s = slides.find((x) => x.key === key);
  if (!s) throw new Error("that slide is gone");
  s.image = { ...image, ...(s.image?.note ? { note: s.image.note } : {}) };
  must(await sb.from("ideas").update({ slides }).eq("id", ideaId));
  document.dispatchEvent(new CustomEvent("studio:refresh"));
}

// Buttons in the slides editor. Returns true when it was one of them.
export async function slidesClick(t, d) {
  const ds = t.dataset;
  const key = t.closest("[data-slide-key]")?.dataset.slideKey;
  if (t.id === "slide-add") {
    const slides = copy(d);
    const s = { key: newKey(), role: slides.length ? "point" : "cover", heading: "", body: "", image: { source: "text" } };
    const ask = slides.length && slides[slides.length - 1].role === "ask";
    slides.splice(ask ? slides.length - 1 : slides.length, 0, s);
    await save(d, slides);
    setTimeout(() => document.getElementById(`sl-${s.key}-heading`)?.focus(), 50);
    return true;
  }
  if (ds.slideMove && key) {
    const slides = copy(d);
    const k = slides.findIndex((x) => x.key === key), j = k + Number(ds.slideMove);
    if (k < 0 || j < 0 || j >= slides.length) return true;
    [slides[k], slides[j]] = [slides[j], slides[k]];
    await save(d, slides);
    return true;
  }
  if (ds.slideDelete != null && key) {
    const s = d.slides.find((x) => x.key === key);
    if ((s.heading || s.body || pictured(s)) && !confirm(`Delete slide ${d.slides.indexOf(s) + 1}?`)) return true;
    await save(d, d.slides.filter((x) => x.key !== key));
    toast("Slide deleted.");
    return true;
  }
  if (ds.slidePic && key) {
    const n = d.slides.findIndex((x) => x.key === key) + 1;
    if (ds.slidePic === "text") {
      const slides = copy(d);
      const s = slides.find((x) => x.key === key);
      s.image = { source: "text", ...(s.image?.note ? { note: s.image.note } : {}) };
      await save(d, slides);
    } else if (ds.slidePic === "photo") pickPhoto(d, key, n);
    else if (ds.slidePic === "clip") openClipPicker(d, key, n);
    return true;
  }
  return false;
}

// ── A photo from the phone ───────────────────────────────────────────────────

const photoInput = document.createElement("input");
photoInput.type = "file";
photoInput.accept = "image/jpeg,image/png,image/heic,image/heif,.heic,.heif,.jpg,.jpeg,.png";
photoInput.className = "up-picker";
photoInput.tabIndex = -1;
photoInput.setAttribute("aria-hidden", "true");
document.body.appendChild(photoInput);
let photoFor = null;
photoInput.addEventListener("change", () => {
  const file = photoInput.files[0];
  photoInput.value = "";
  const f = photoFor;
  photoFor = null;
  if (!file || !f) return;
  addPhoto(f.ideaId, file, `Photo for slide ${f.n} of “${f.title}”`, async (photo) => {
    await setPicture(f.ideaId, f.key, { source: "photo", photo_id: photo.id, ...(photo.thumb_path ? { thumb_path: photo.thumb_path } : {}) });
    toast(`Photo is on slide ${f.n}.`);
  });
});
function pickPhoto(d, key, n) {
  if (!canUpload()) { toast(`Uploading needs Studio to be reachable. ${laptopWait()}`, 7000); return; }
  photoFor = { ideaId: d.id, key, n, title: d.title };
  photoInput.click();
}

// ── A still from the footage: pick a clip, then its moment ──────────────────

function openClipPicker(d, key, n) {
  openModal(`<h2 id="modal-title">A still for slide ${esc(n)}</h2>
    <p class="muted">From Urban Makers' own footage. Pick a clip, then the moment; Studio takes the sharpest frame there.</p>
    <input type="search" id="still-q" class="search" placeholder="Search clips by title" aria-label="Search clips">
    <div id="still-results" aria-live="polite"><p class="muted"><span class="spin"></span> Loading clips…</p></div>`, async (e) => {
    const clipBtn = e.target.closest("[data-still-clip]");
    const momentBtn = e.target.closest("[data-still-at]");
    if (e.target.closest("[data-still-back]")) { loadClips(d); return; }
    if (clipBtn) { await showMoments(Number(clipBtn.dataset.stillClip)); return; }
    if (momentBtn && !momentBtn.disabled) {
      for (const b of document.querySelectorAll("[data-still-at]")) b.disabled = true;
      const status = $("#still-status");
      if (status) status.innerHTML = `<span class="spin"></span> Studio is taking the still…`;
      try {
        const r = await studioApi("POST", "/api/uploads/stills", { clip_id: Number(momentBtn.dataset.stillClipId), at: Number(momentBtn.dataset.stillAt) });
        await setPicture(d.id, key, { source: "clip", clip_id: r.clip_id, at: r.at, thumb_path: r.thumb_path });
        await signPaths([r.thumb_path]).catch(() => {});
        closeModal();
        toast(`Still is on slide ${n}.`);
      } catch (err) {
        for (const b of document.querySelectorAll("[data-still-at]")) b.disabled = false;
        if (status) status.textContent = `Couldn't take the still: ${err.message}`;
      }
    }
  }, true, "sheet");
  let timer;
  $("#still-q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => loadClips(d), 250); });
  loadClips(d);
}

async function loadClips(d) {
  const box = $("#still-results");
  if (!box) return;
  const q = $("#still-q")?.value.trim() || "";
  if ($("#still-q")) $("#still-q").hidden = false;
  const base = () => {
    let r = sb.from("clips").select("id,title,recorded_at,duration,project_id,verdict,thumb_path")
      .order("recorded_at", { ascending: false, nullsFirst: false }).order("id", { ascending: false }).limit(60);
    if (q) r = r.ilike("title", `%${q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`);
    return r;
  };
  try {
    const [mine, rest] = await Promise.all([
      base().eq("project_id", d.project_id).then(must),
      base().or(`project_id.is.null,project_id.neq.${Number(d.project_id)}`).then(must),
    ]);
    await signPaths([...mine, ...rest].map((c) => c.thumb_path)).catch(() => {});
    if (!$("#still-results") || ($("#still-q")?.value.trim() || "") !== q) return;
    const tile = (c) => {
      const thumb = stillUrl(c.thumb_path);
      return `<button class="pick" data-still-clip="${esc(c.id)}" aria-label="${esc(c.title || `Clip ${c.id}`)}">
        ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="pick-none">No still yet</span>`}
        <span class="pick-chips">${retakeBadge(c)}</span>
        <span class="pick-text"><span class="pick-title">${esc(c.title || `Clip ${c.id}`)}</span>
          <span class="pick-meta">${c.duration ? esc(mmss(c.duration)) : ""}${c.recorded_at ? ` · ${esc(shortDate(c.recorded_at))}` : ""}</span></span></button>`;
    };
    box.innerHTML = !mine.length && !rest.length
      ? `<p class="muted">${q ? "No clips match." : "No clips yet. Studio adds them here as it tags new footage."}</p>`
      : (mine.length ? `<h3>In this project</h3><div class="picker-grid">${mine.map(tile).join("")}</div>` : "")
        + (rest.length ? `<h3>${mine.length ? "Other clips" : "Recent clips"}</h3><div class="picker-grid">${rest.map(tile).join("")}</div>` : "");
  } catch (err) {
    box.innerHTML = `<p class="error-text">${esc(err.message)}</p>`;
  }
}

async function showMoments(clipId) {
  const box = $("#still-results");
  if (!box) return;
  if ($("#still-q")) $("#still-q").hidden = true;
  if (!canUpload()) {
    box.innerHTML = `<p class="muted">Studio takes the still, so it needs to be reachable. ${esc(laptopWait())}</p><p><button class="btn" data-still-back>Back to the clips</button></p>`;
    return;
  }
  box.innerHTML = `<p class="muted"><span class="spin"></span> Loading the clip's moments…</p>`;
  let m;
  try { m = await studioApi("GET", `/api/uploads/clips/${encodeURIComponent(clipId)}/moments`); } catch (err) {
    box.innerHTML = `<p class="error-text">Couldn't load the clip: ${esc(err.message)}</p><p><button class="btn" data-still-back>Back to the clips</button></p>`;
    return;
  }
  if (!$("#still-results")) return;
  const at = (t) => `data-still-at="${esc(Number(t) || 0)}" data-still-clip-id="${esc(clipId)}"`;
  box.innerHTML = `<div class="row tight"><button class="btn small ghost" data-still-back>‹ Clips</button><strong class="grow">${esc(m.title || `Clip ${clipId}`)}</strong><span class="muted small">${esc(mmss(m.duration || 0))}</span></div>
    ${m.moments?.length ? `<h3>Best moments</h3><ul class="moment-list">${m.moments.map((x) => `<li><button class="btn small" ${at(((Number(x.start) || 0) + (Number(x.end) || Number(x.start) || 0)) / 2)}>${esc(mmss(x.start))}</button> <span>${esc(x.description || "")}</span></li>`).join("")}</ul>` : ""}
    <h3>Or tap a frame</h3>
    ${m.frames?.length ? `<div class="picker-grid frames-grid">${m.frames.map((f) => `<button class="pick frame-pick" ${at(f.at)} aria-label="The moment at ${esc(mmss(f.at))}"><img src="${esc(/^data:image\/jpeg;base64,/.test(f.src) ? f.src : "")}" alt=""><span class="pick-text"><span class="pick-meta">${esc(mmss(f.at))}</span></span></button>`).join("")}</div>` : `<p class="muted">No frames of this clip on Studio.</p>`}
    <p class="muted small" id="still-status" aria-live="polite"></p>`;
}
