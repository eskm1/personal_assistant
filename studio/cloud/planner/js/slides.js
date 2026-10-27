// A carousel's slides on the idea page (CONTRACT "Carousels"): the slides as the main column, each
// with a small preview of how it reads on its left (edit the words, reorder, add, delete, and pick
// each slide's picture); "Write slides" (Claude, plan-develop) and Make this sit with the post. Pictures come from
// Urban Makers' own footage (a still Studio takes from a clip), photos uploaded from the phone, or
// an AI concept picture Studio draws when Bryan presses Make this (only when the Studio machine has
// the image key, laptop_status.info.concepts; never in a Finished homes post; marked "Concept").
import {
  $, esc, sb, must, toast, plural, mmss, shortDate, signPaths, stillUrl, jobView, jobActive, retakeBadge,
  openModal, closeModal, laptopWait, state, SLIDE_ROLE, PICTURE, liveClips, isOwner, ownerName, offWhy, mark, ago,
  schema, PHONE,
} from "./core.js";
import { addPhoto, addPhotos, canUpload, studioApi, isPhoto, nameOrder, onPhotoDrop } from "./upload.js";
import { sortable } from "./sortable.js";

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
const isConcept = (s) => s.image?.source === "concept";
// Concept pictures: the Studio machine says whether it has the image key (never the key itself).
export const conceptsOn = () => state.laptop?.info?.concepts?.on === true;
const FINISHED = "finished";
// About what one concept slide costs (two options).
function conceptCost() {
  const c = state.laptop?.info?.concepts;
  const usd = Number(c?.per_image_usd) * (Number(c?.options) || 2);
  return usd > 0 ? `about US$${usd.toFixed(2)}` : "";
}
// Made (or being made): the slides change in Review now, with notes.
export const slidesLocked = (d) => d.status === "made" || !!d.production;

// ── A slide's preview ────────────────────────────────────────────────────────

function miniHTML(s, i, n) {
  const thumb = stillUrl(s.image?.thumb_path);
  return `<button type="button" class="cs-mini ${esc(s.role)}${thumb ? " has-pic" : ""}" data-goto-slide="${esc(s.key)}" aria-label="Slide ${i + 1}: ${esc(s.heading || "no heading")}. Edit it">
    ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : ""}
    <span class="cs-num">${i + 1}/${n}</span>
    ${isConcept(s) ? `<span class="cs-concept">Concept</span>` : ""}
    ${pictured(s) && !thumb && !isConcept(s) ? `<span class="cs-pic-wait">${esc(PICTURE[s.image.source] || "Picture")}</span>` : ""}
    <span class="cs-text"><span class="cs-h">${esc(s.heading)}</span>${s.body ? `<span class="cs-b">${esc(s.body)}</span>` : ""}</span>
  </button>`;
}

// ── The editor ───────────────────────────────────────────────────────────────

function pictureLine(s, d) {
  const img = s.image || {};
  if (img.source === "clip") {
    const title = d.clipTitles?.get(img.clip_id);
    return `${PICTURE.clip}: ${title ? `${title}, ` : `clip ${img.clip_id}, `}${mmss(img.at || 0)}`;
  }
  return PICTURE[img.source] || PICTURE.text;
}

// The picture buttons, and for a concept slide what it should show.
function conceptHTML(s, i, d, locked) {
  const id = `sl-${esc(s.key)}-concept`;
  if (d.post_type === FINISHED) {
    return isConcept(s) ? `<p class="error-text small">Finished homes posts show only real work: this slide is laid out as text unless you give it a still or a photo.</p>` : "";
  }
  if (!isConcept(s)) return "";
  const cost = conceptCost();
  return `<div class="slide-concept">
    <label class="small" for="${id}">What should the concept show?</label>
    <input id="${id}" data-slide-field="concept_note" maxlength="300" value="${esc(s.image.note || "")}" data-orig="${esc(s.image.note || "")}" placeholder="e.g. a reading corner by the window, light oak and linen" ${locked ? "disabled" : ""}>
    <span class="muted small">${conceptsOn()
      ? `Studio draws two options when ${locked ? "the carousel was made" : "Bryan presses Make this"}${cost ? ` (${esc(cost)})` : ""}, checks them, and Bryan picks one in Review. Marked “Concept” on the slide. A real still or photo is better where you have one.`
      : "Studio needs the image key before it can draw concepts; until then this slide is laid out as text."}</span>
  </div>`;
}

function conceptButtonHTML(s, i, d) {
  if (d.post_type === FINISHED) return "";
  if (!conceptsOn()) {
    return `<button type="button" class="btn small ghost" disabled title="Bryan puts the Gemini image key on the Studio machine first (README, Carousels)">AI concept · needs the image key</button>`;
  }
  return isConcept(s) ? "" : `<button type="button" class="btn small" data-slide-pic="concept" aria-label="Slide ${i + 1}: an AI concept picture">AI concept</button>`;
}

function slideRowHTML(s, i, d, locked) {
  const n = d.slides.length;
  const id = (f) => `sl-${esc(s.key)}-${f}`;
  const dis = locked ? "disabled" : "";
  return `<li class="shot-row slide-row ${esc(s.role)}" id="slide-${esc(s.key)}" data-slide-key="${esc(s.key)}" data-saved-scope>
    <div class="shot-media">${miniHTML(s, i, n)}</div>
    <div class="shot-body">
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
      <textarea id="${id("body")}" data-slide-field="body" rows="${Math.min(6, Math.max(2, Math.ceil(s.body.length / 70)))}" maxlength="800" data-orig="${esc(s.body)}" placeholder="The words under it (optional)" ${dis}>${esc(s.body)}</textarea>
      <div class="slide-pic">
        <div class="slide-pic-info">
          <span class="small"><span class="muted">Picture:</span> ${esc(pictureLine(s, d))}</span>
          ${s.image?.note && !isConcept(s) ? `<span class="muted small">Picture idea: ${esc(s.image.note)}</span>` : ""}
          ${locked ? "" : `<span class="row tight slide-pic-btns">
            <button type="button" class="btn small" data-slide-pic="clip" aria-label="Slide ${i + 1}: a still from your footage">From footage</button>
            <button type="button" class="btn small" data-slide-pic="photo" aria-label="Slide ${i + 1}: upload a photo">Photo</button>
            ${conceptButtonHTML(s, i, d)}
            ${pictured(s) ? `<button type="button" class="btn small ghost" data-slide-pic="text" aria-label="Slide ${i + 1}: text only">Text only</button>` : ""}
          </span>`}
        </div>
      </div>
      ${conceptHTML(s, i, d, locked)}
    </div>
  </li>`;
}

// The slides: the main column on a computer, the middle of the plan on a phone.
export function slidesMainHTML(d) {
  const locked = slidesLocked(d);
  const writing = jobActive(jobView(d.jobs.develop));
  const n = d.slides.length;
  const pics = d.slides.filter(pictured).length;
  return `<section class="idea-main" aria-labelledby="sb-title">
    <div class="main-head"><h2 id="sb-title">Slides</h2>
      <span class="main-sum">${n ? `${esc(plural(n, "slide"))}${pics ? ` · ${esc(plural(pics, "picture"))}` : ""}` : "Carousel"}</span>
      ${writing && n ? `<span class="sb-drafting"><span class="spin"></span> Claude is rewriting them…</span>` : ""}</div>
    <p class="muted small main-note">${locked ? "These slides are in Review now. To change them, send the draft back with notes there." : "A cover, 4 to 8 points and the ask. Changes save when you click away."}</p>
    ${n ? `<ol class="shot-list slide-list">${d.slides.map((s, i) => slideRowHTML(s, i, d, locked)).join("")}</ol>`
      : writing ? `<div class="sb-empty"><p><span class="spin"></span> The slides are being written…</p><p class="muted">Claude is writing the cover, the points and the ask. About a minute; they appear here by themselves.</p></div>`
        : `<div class="sb-empty"><p>No slides yet.</p><p class="muted">Use Write slides with Claude, or add them yourself.</p></div>`}
    ${locked || n >= MAX_SLIDES ? "" : `<div class="row add-shot"><button class="btn" id="slide-add">+ Add slide</button></div>`}
  </section>`;
}

// For Make this: what the concept slides will cost, or why they'll be text.
export function conceptsMakeLine(d) {
  const n = d.slides.filter(isConcept).length;
  if (!n) return "";
  if (d.post_type === FINISHED) return `${plural(n, "concept slide")} will be text: Finished homes posts show only real work.`;
  if (!conceptsOn()) return `${plural(n, "concept slide")} will be text until Studio has the image key.`;
  const c = state.laptop?.info?.concepts;
  const usd = Number(c?.per_image_usd) * (Number(c?.options) || 2) * n;
  return `Studio draws ${n === 1 ? "the concept picture" : `${n} concept pictures`} (two options each${usd > 0 ? `, about US$${usd.toFixed(2)}` : ""}); you pick in Review.`;
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
  if (field === "concept_note") {
    // What the concept should show: a new brief for Studio (nothing is drawn until Make this).
    const note = el.value.trim().slice(0, 300);
    s.image = { source: "concept", ...(note ? { note } : {}) };
  } else s[field] = field === "role" ? (ROLES.includes(el.value) ? el.value : "point") : el.value.trim();
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
    } else if (ds.slidePic === "concept") {
      if (!conceptsOn() || d.post_type === FINISHED) return true;
      const slides = copy(d);
      const s = slides.find((x) => x.key === key);
      s.image = { source: "concept", ...(s.image?.note ? { note: s.image.note } : {}) };
      await save(d, slides);
      setTimeout(() => document.getElementById(`sl-${key}-concept`)?.focus(), 50);
      toast(`Slide ${n} will get an AI concept picture when it's made. Say what it should show.`);
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
  const base = (extra) => {
    let r = sb.from("clips").select(`id,title,recorded_at,duration,project_id,verdict,thumb_path${extra}`)
      .order("recorded_at", { ascending: false, nullsFirst: false }).order("id", { ascending: false }).limit(60);
    if (q) r = r.ilike("title", `%${q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`);
    return r;
  };
  try {
    const [mine, rest] = await Promise.all([
      liveClips((x) => base(x).eq("project_id", d.project_id).then(must)),
      liveClips((x) => base(x).or(`project_id.is.null,project_id.neq.${Number(d.project_id)}`).then(must)),
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


// ── Photos: many at once, in order (the profile grid's panel and the idea page) ──
// A carousel's photos as a strip: add a folder (or many from the phone's Photos) in one go, in file-name order;
// drag to reorder (hold a moment on a phone), or tap one for Move earlier / Move later / Make cover / Remove;
// slide 1 is the cover. Instagram takes at most 10 in a carousel (laptop_status.info.instagram.carousel_max).
// "Photos fill the slide" (ideas.slide_layout 'photos') makes each photo its whole slide, with slide 1's
// heading as an optional small cover line; Bryan's Confirm photos says the set is the one he wants.

export const isPhotoLayout = (d) => d?.slide_layout === "photos";
export const carouselMax = () => Math.max(2, Number(state.laptop?.info?.instagram?.carousel_max) || 10);
const phSel = new Map();   // idea id -> the photo key picked for its actions
const shown = new Map();   // the ideas whose photo strips are on screen (by id), so a drop finds its idea
const phNotes = new Map(); // idea id -> what the last Add photos said (kept when the strip is drawn again)
const hasPhoto = (s) => s.image?.source === "photo" || s.image?.source === "clip" || s.image?.source === "concept";

export function photosHTML(d, opts = {}) {
  shown.set(d.id, d);
  const locked = slidesLocked(d);
  const max = carouselMax();
  const n = d.slides.length;
  const sel = phSel.get(d.id);
  const pick = d.slides.find((x) => x.key === sel);
  const owner = isOwner();
  const grid = schema.grid && "slide_layout" in d;
  const photos = isPhotoLayout(d);
  const tiles = d.slides.map((x, i) => {
    const thumb = stillUrl(x.image?.thumb_path);
    const on = x.key === sel;
    return `<li class="ph-item${on ? " on" : ""}" data-key="${esc(x.key)}">
      <button type="button" class="ph-tile" data-ph-pick="${esc(x.key)}" aria-pressed="${on}"
        aria-label="${esc(`Slide ${i + 1}${i === 0 ? ", the cover" : ""}${x.heading ? `: ${x.heading}` : ""}${thumb ? "" : ", no picture yet"}. ${locked ? "" : "Tap for its options, or hold and drag to move it"}`)}">
        ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy" draggable="false">` : `<span class="ph-none">${esc(x.heading || (hasPhoto(x) ? "Picture" : "Words"))}</span>`}
        <span class="ph-num">${i + 1}</span>${i === 0 ? `<span class="ph-cover">Cover</span>` : ""}
      </button></li>`;
  }).join("");
  const k = pick ? d.slides.indexOf(pick) : -1;
  const actions = pick && !locked ? `<div class="ph-actions" role="group" aria-label="${esc(`Slide ${k + 1}`)}">
      <button type="button" class="btn small" data-ph-move="-1" ${offWhy(k === 0 ? "It's already first." : "")}>Move earlier</button>
      <button type="button" class="btn small" data-ph-move="1" ${offWhy(k === n - 1 ? "It's already last." : "")}>Move later</button>
      <button type="button" class="btn small" data-ph-cover ${offWhy(k === 0 ? "It's the cover already." : "")}>Make cover</button>
      <button type="button" class="btn small ghost danger" data-ph-remove>Remove</button></div>` : "";
  const confirmed = d.photos_confirmed_at;
  const confirm = !grid || locked || !n ? ""
    : confirmed ? `<p class="ph-confirmed good-text small">Photos confirmed ${esc(ago(confirmed))}. Changing them asks again.</p>`
      : `<button type="button" class="btn ${owner ? "good" : ""}" data-ph-confirm ${offWhy(owner ? "" : `${ownerName()} confirms the photos.`)}>Confirm photos</button>`;
  const room = Math.max(0, max - n);
  const cover = photos && n && !locked ? `<div class="ph-line" data-slide-key="${esc(d.slides[0].key)}" data-saved-scope>
      <label class="um-label" for="ph-line-${esc(d.id)}">Cover line (optional, slide 1 only)</label> <span class="saved" aria-live="polite"></span>
      <input id="ph-line-${esc(d.id)}" data-slide-field="heading" maxlength="120" value="${esc(d.slides[0].heading)}" data-orig="${esc(d.slides[0].heading)}" placeholder="A few words over the first photo"></div>` : "";
  return `<section class="ph-box${opts.cls ? ` ${esc(opts.cls)}` : ""}" ${locked ? "" : `data-drop-photos="${esc(d.id)}"`} aria-label="Photos">
    <div class="ph-head"><h3 class="um-label">Photos · ${esc(`${n} of ${max}`)}</h3>
      ${grid && !locked ? `<div class="seg ph-layout" role="group" aria-label="Layout">
        <button type="button" data-ph-layout="" class="${photos ? "" : "on"}" aria-pressed="${!photos}">Words on slides</button>
        <button type="button" data-ph-layout="photos" class="${photos ? "on" : ""}" aria-pressed="${photos}">Photos fill the slide</button></div>` : ""}</div>
    ${n ? `<ol class="ph-strip">${tiles}</ol>` : `<p class="muted small ph-empty">No photos yet.</p>`}
    ${actions}
    ${locked ? `<p class="muted small">These slides are in Review now. To change the photos, send the draft back with notes there.</p>`
      : `<div class="ph-add">
        <button type="button" class="btn${n ? "" : " primary"}" data-ph-add ${offWhy(!canUpload() ? `Adding photos needs Studio to be reachable. ${laptopWait()}` : room ? "" : `Instagram takes ${max} photos in a carousel. Remove one to add another.`)}>${n ? "Add more photos" : "Add photos"}</button>
        <span class="muted small">${PHONE.matches ? "Pick several from Photos at once." : "Or drop a folder or several photos here."} Up to ${esc(max)}, in file-name order; then drag to change it.</span>
      </div>`}
    <p class="small ph-note" id="ph-note-${esc(d.id)}" aria-live="polite">${esc(phNotes.get(d.id) || "")}</p>
    ${cover}
    ${confirm ? `<div class="ph-foot">${confirm}</div>` : ""}
  </section>`;
}

// One more photo at the end (a words carousel: before its ask), on the slides as they are now in the planner.
async function appendPhoto(ideaId, photo) {
  const row = must(await sb.from("ideas").select("id,slides").eq("id", ideaId).maybeSingle());
  const slides = prepSlides(row?.slides);
  if (slides.length >= Math.min(MAX_SLIDES, carouselMax())) {
    toast(`A photo was left out: Instagram takes ${carouselMax()} in a carousel.`, 6000);
    return;
  }
  const s = { key: newKey(), role: slides.length ? "point" : "cover", heading: "", body: "",
    image: { source: "photo", photo_id: photo.id, ...(photo.thumb_path ? { thumb_path: photo.thumb_path } : {}) } };
  const ask = slides.length > 1 && slides[slides.length - 1].role === "ask";
  slides.splice(ask ? slides.length - 1 : slides.length, 0, s);
  must(await sb.from("ideas").update({ slides }).eq("id", ideaId));
  if (photo.thumb_path) await signPaths([photo.thumb_path]).catch(() => {});
  document.dispatchEvent(new CustomEvent("studio:refresh"));
}

// Photos picked or dropped for an idea: the first that fit (Instagram's 10), in file-name order; the rest said.
export function addPhotosTo(d, files) {
  const note = document.getElementById(`ph-note-${d.id}`);
  // Said under the photos when they're on screen, else in a toast.
  const say = (text) => {
    phNotes.set(d.id, text);
    if (note?.closest(".ph-box")?.offsetParent) note.textContent = text; else toast(text, 7000);
  };
  if (!canUpload()) { say(`Adding photos needs Studio to be reachable. ${laptopWait()}`); return; }
  const photos = nameOrder([...files].filter(isPhoto));
  const other = files.length - photos.length;
  const room = Math.max(0, carouselMax() - d.slides.length);
  if (!photos.length) { say(other ? "Those aren't photos Studio can use (JPEG, PNG or HEIC)." : "No photos in that."); return; }
  if (!room) { say(`This carousel has ${carouselMax()} photos, Instagram's most. Remove one first.`); return; }
  const take = photos.slice(0, room), left = photos.slice(room);
  const queued = addPhotos(d.id, take, `Photos for “${d.title}”`, (photo) => appendPhoto(d.id, photo));
  mark("photos_added", { n: queued, left: left.length });
  say([`Adding ${plural(queued, "photo")} in file-name order.`,
    left.length ? `Instagram takes ${carouselMax()} in a carousel, so ${plural(left.length, "photo")} ${left.length === 1 ? "was" : "were"} left out: ${left.slice(0, 3).map((f) => f.name).join(", ")}${left.length > 3 ? "…" : ""}.` : "",
    other ? `${plural(other, "file")} ${other === 1 ? "isn't a photo" : "aren't photos"}, skipped.` : ""].filter(Boolean).join(" "));
}

const manyInput = document.createElement("input");
manyInput.type = "file";
manyInput.multiple = true;
manyInput.accept = "image/jpeg,image/png,image/heic,image/heif,.heic,.heif,.jpg,.jpeg,.png";
manyInput.className = "up-picker";
manyInput.tabIndex = -1;
manyInput.setAttribute("aria-hidden", "true");
document.body.appendChild(manyInput);
let manyFor = null;
manyInput.addEventListener("change", () => {
  const files = [...manyInput.files];
  manyInput.value = "";
  const d = manyFor;
  manyFor = null;
  if (d && files.length) addPhotosTo(d, files);
});

onPhotoDrop((zone, files) => { const d = shown.get(Number(zone.dataset.dropPhotos)); if (d) addPhotosTo(d, files); });

// Buttons in a photo strip. Returns true when it was one of them and changed something (the caller reloads the
// idea), "render" for a change on screen only, "picker" when the photo picker opened (nothing to reload).
export async function photosClick(t, d) {
  const ds = t.dataset;
  shown.set(d.id, d);
  if (ds.phPick) { phSel.set(d.id, phSel.get(d.id) === ds.phPick ? null : ds.phPick); return "render"; }
  if (ds.phAdd != null) {
    if (t.getAttribute("aria-disabled") !== "true") { manyFor = d; manyInput.click(); }
    return "picker";   // nothing changed yet: the page isn't drawn again under the open picker
  }
  if (ds.phLayout != null) {
    const layout = ds.phLayout || null;
    if ((d.slide_layout || null) === layout) return true;
    must(await sb.from("ideas").update({ slide_layout: layout }).eq("id", d.id));
    d.slide_layout = layout;
    mark("photo_layout", { photos: !!layout });
    toast(layout ? "Each photo fills its slide. Slide 1 can carry a short cover line." : "Back to words on the slides.");
    return true;
  }
  if (ds.phConfirm != null) {
    if (t.getAttribute("aria-disabled") === "true") return true;
    const at = new Date().toISOString();
    must(await sb.from("ideas").update({ photos_confirmed_at: at, photos_confirmed_by: state.me?.id || null }).eq("id", d.id));
    d.photos_confirmed_at = at;
    mark("photos_confirmed", { n: d.slides.length });
    toast("Photos confirmed.");
    return true;
  }
  const key = phSel.get(d.id);
  if (!key) return false;
  const before = copy(d);
  const slides = copy(d);
  const k = slides.findIndex((x) => x.key === key);
  if (k < 0) return false;
  if (ds.phMove) {
    const j = k + Number(ds.phMove);
    if (j < 0 || j >= slides.length || t.getAttribute("aria-disabled") === "true") return true;
    [slides[k], slides[j]] = [slides[j], slides[k]];
  } else if (ds.phCover != null) {
    if (!k) return true;
    slides.unshift(...slides.splice(k, 1));
  } else if (ds.phRemove != null) {
    slides.splice(k, 1);
    phSel.delete(d.id);
  } else return false;
  await saveOrder(d, slides);
  if (ds.phRemove != null) {
    toast("Photo removed from the carousel.", 6000, { label: "Undo", mark: "photo_remove_undo", run: async () => {
      try { await save(d, before); document.dispatchEvent(new CustomEvent("studio:refresh")); toast("Photo back."); } catch (err) { toast(`Couldn't put it back: ${err.message}`); }
    } });
  }
  return true;
}

// The slides in a new order: the cover is whatever is first now, the ask stays last on a words carousel.
async function saveOrder(d, slides) {
  slides.forEach((x, i) => { if (i === 0) x.role = "cover"; else if (x.role === "cover") x.role = "point"; });
  if (!isPhotoLayout(d) && slides.length > 1) {
    const ask = slides.findIndex((x) => x.role === "ask");
    if (ask >= 0 && ask !== slides.length - 1) slides.push(...slides.splice(ask, 1));
  }
  await save(d, slides);
  d.photos_confirmed_at = null;
}

// Drag to reorder a strip (sortable.js), once per page region that shows strips: getD() is the idea on screen.
export function photoSorter(region, getD, after) {
  return sortable(region, {
    item: ".ph-item",
    key: (el) => el.dataset.key,
    can: () => { const d = getD(); return !d ? "Loading." : slidesLocked(d) ? "These slides are in Review now: send it back there to change them." : ""; },
    cols: () => {
      const strip = region.querySelector(".ph-strip");
      const first = strip?.children[0], all = strip ? [...strip.children] : [];
      return Math.max(1, all.filter((x) => x.offsetTop === first?.offsetTop).length);
    },
    over: (key, to) => {
      const strip = region.querySelector(".ph-strip");
      const el = strip && [...strip.children].find((x) => x.dataset.key === key);
      if (!el) return;
      const rest = [...strip.children].filter((x) => x !== el);
      strip.insertBefore(el, rest[to] || null);
      [...strip.children].forEach((x, i) => { const num = x.querySelector(".ph-num"); if (num) num.textContent = String(i + 1); });
    },
    drop: async (key) => {
      const d = getD();
      const strip = region.querySelector(".ph-strip");
      if (!d || !strip) return;
      const order = [...strip.children].map((x) => x.dataset.key);
      const slides = order.map((k) => copy(d).find((x) => x.key === k)).filter(Boolean);
      if (slides.length !== d.slides.length) return;
      try { await saveOrder(d, slides); mark("photos_reordered"); } catch (err) { toast(`Couldn't save the order: ${err.message}`); }
      after?.();
    },
    cancel: () => after?.(),
    why: (text) => toast(text),
  });
}
