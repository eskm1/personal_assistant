// The profile grid (#/grid; Bryan, via the content lead, 2026-10-27: "see and plan the whole @urbanmakers_sg
// profile before anything posts"). The account as a phone shows it: the bio header, then a 3-wide grid of 3:4
// tiles (the profile's crop of a 4:5 post; a Reel shows its cover), pins first, then newest at the top: what's
// planned (ideas with a posting date, drafts in Review, approved), scheduled, then what's posted on the account
// (post_results, phone posts too). A Reel kept out of the grid (Reels tab only) doesn't show.
//
// Drag a tile (hold a moment on a phone; Space and the arrow keys on a keyboard) to change the order: the dates
// stay where they are and the posts move through them (Shift), or two posts trade dates (Swap: the toggle, or
// Alt while dropping). Posted and scheduled posts don't move. Nothing is saved until Save this order (one
// request, set_post_dates); Undo goes back to the last saved order. Spread these gives many ideas dates at once.
// Tap a tile for its slides, caption, status and Studio's read; photos and the caption of an idea not made yet
// change there. Nothing posts from here: posting stays Approve, then Schedule.
//
// One request draws it (rpc profile_grid, migration 20261028000000); stills come signed from the thumbs bucket,
// lazily; the header comes from the Studio machine's heartbeat (laptop_status.info.instagram.profile).
import {
  $, $$, esc, sb, must, toast, isOwner, ownerName, state, underPlan, cfg, signPaths, stillUrl, plural, openModal,
  closeModal, formError, mark, offWhy, missingTable, POST_TYPE, kindDot, todayISO, dayLabel, parseDay, isoDay, PHONE,
  recall, store, ideaMarks, marksKey, keepDrafts, typingIn, ago,
} from "./core.js";
import { sortable } from "./sortable.js";
import { prepSlides, photosHTML, photosClick, photoSorter, saveSlideField, slidesLocked, slidePaths } from "./slides.js";
import { openSchedule, canSchedule } from "./posting.js";

const root = () => $("#grid");
export const grid = {
  data: null, missing: false, loaded: false, draft: new Map(), saved: null, swap: recall("studio.gridSwap") === "1",
  dates: recall("studio.gridDates") !== "0", showOut: false, panel: null, idea: null, preview: null, sig: "", seq: 0,
};
export const gridOpen = () => !root().hidden;
export const gridRoute = (hash) => (hash === "#/grid" ? { page: "grid" } : null);
const MAX_PINS = 3;
const SP = () => window.StudioPost;

// ── Data ────────────────────────────────────────────────────────────────────

const missingFn = (err) => /PGRST202|42883|Could not find the function|schema cache/i.test(`${err?.code || ""} ${err?.message || ""}`) || missingTable(err);

export async function loadGrid() {
  const seq = ++grid.seq;
  let data;
  try {
    const { data: d, error } = await sb.rpc("profile_grid");
    if (error) throw error;
    data = d;
  } catch (err) {
    if (!missingFn(err)) throw err;
    if (seq !== grid.seq) return;
    grid.missing = true;
    grid.loaded = true;
    return;
  }
  if (seq !== grid.seq) return;
  // Ideas Spread these brought in that the grid didn't have yet (no date until it's saved).
  const extra = (grid.data?.extra || []).filter((x) => !data.ideas.some((i) => i.id === x.id));
  grid.data = { ...data, extra };
  grid.missing = false;
  grid.loaded = true;
  const paths = [...data.media.map((m) => m.thumb_path), ...data.posts.map((p) => p.thumb_path), ...data.ideas.map((i) => i.cover),
    profile()?.avatar_path];
  await signPaths(paths.filter(Boolean)).catch(() => {});
}

const profile = () => state.laptop?.info?.instagram?.profile || null;
const ideasAll = () => [...(grid.data?.ideas || []), ...(grid.data?.extra || [])];
const dayOfTs = (iso) => SP()?.dayOf(iso) || String(iso || "").slice(0, 10);
const sgTs = (day) => Date.parse(`${day}T19:30:00+08:00`);   // a planned day, at Studio's usual posting time
const LIVE = ["scheduled", "publishing", "failed"];

// Every post the profile will show, with what the page needs: its mark, its date, whether it can move.
function compose() {
  const d = grid.data;
  if (!d) return { tiles: [], undated: [], out: [] };
  const pins = new Map(d.pins.map((p) => [p.idea_id ? `i${p.idea_id}` : `m${p.media_id}`, p]));
  const postOf = new Map(), loose = [];
  for (const p of d.posts) (p.idea_id ? postOf.set(p.idea_id, p) : loose.push(p));
  const used = new Set();
  const mediaFor = (ideaId, post) => d.media.find((m) => (ideaId && m.idea_id === ideaId) || (post && (m.post_id === post.id || (post.media_id && m.media_id === post.media_id))));
  const items = [];
  for (const i of ideasAll()) {
    const post = postOf.get(i.id) || null;
    const media = mediaFor(i.id, post);
    if (media) used.add(media.media_id);
    const posted = !!media || post?.state === "posted" || i.production?.status === "posted";
    const live = !posted && post && LIVE.includes(post.state);
    const date = grid.draft.has(i.id) ? grid.draft.get(i.id) : i.post_on;
    const reel = i.format !== "carousel";
    items.push({
      key: `i${i.id}`, idea: i, post, media, title: i.title, format: reel ? "reel" : "carousel", post_type: i.post_type,
      mark: window.StudioMarks?.of({ production: i.production, post: media ? { state: "posted", posted_at: media.posted_at } : post, format: i.format, slides_count: i.slides_count }),
      fact: window.StudioMarks?.needsFact(i), factText: i.needs_fact || "",
      posted, live, day: posted ? dayOfTs(media?.posted_at || post?.posted_at) : live ? dayOfTs(post.scheduled_at) : date || "",
      ts: posted ? Date.parse(media?.posted_at || post?.posted_at || "") || 0 : live ? Date.parse(post.scheduled_at) : date ? sgTs(date) : 0,
      dated: posted || !!live || !!date, changed: grid.draft.has(i.id) && grid.draft.get(i.id) !== i.post_on,
      inGrid: !reel || (post ? post.share_to_feed !== false : media ? media.in_grid !== false : i.in_grid !== false),
      thumb: media?.thumb_path || post?.thumb_path || i.cover || "", made: i.production?.id ?? null,
      version: i.production?.version ?? 0, coverText: i.cover_text || "", pin: pins.get(`i${i.id}`) || null,
      permalink: media?.permalink || post?.permalink || "",
    });
  }
  for (const p of loose) {
    const media = mediaFor(null, p);
    if (media) used.add(media.media_id);
    const posted = !!media || p.state === "posted";
    items.push({ key: `p${p.id}`, post: p, media, title: p.title, format: p.kind, post_type: p.post_type,
      mark: window.StudioMarks?.of({ post: media ? { state: "posted", posted_at: media.posted_at } : p }), posted, live: !posted && LIVE.includes(p.state),
      day: dayOfTs(posted ? media?.posted_at || p.posted_at : p.scheduled_at), ts: Date.parse(posted ? media?.posted_at || p.posted_at : p.scheduled_at) || 0,
      dated: true, inGrid: p.kind !== "reel" || p.share_to_feed !== false, thumb: media?.thumb_path || p.thumb_path || "",
      pin: null, permalink: media?.permalink || p.permalink || "" });
  }
  for (const m of d.media) {
    if (used.has(m.media_id)) continue;
    items.push({ key: `m${m.media_id}`, media: m, title: m.title || "A post on the account", format: m.format, post_type: m.post_type,
      mark: window.StudioMarks?.of({ post: { state: "posted", posted_at: m.posted_at } }), posted: true, day: dayOfTs(m.posted_at),
      ts: Date.parse(m.posted_at || "") || 0, dated: true, inGrid: m.format !== "reel" || m.in_grid !== false, thumb: m.thumb_path || "",
      pin: pins.get(`m${m.media_id}`) || null, permalink: m.permalink || "", fromPhone: !m.post_id });
  }
  for (const it of items) it.why = it.posted ? "It's on Instagram: posted posts stay where they are."
    : it.live ? (it.post.state === "failed" ? "It couldn't post: try again from the Posting calendar, with a new time." : `It's scheduled for ${SP()?.whenText(it.post.scheduled_at) || it.day}: change its time in the Schedule dialog.`)
      : it.pin ? "It's pinned, so it stays at the top. Unpin it to move it." : "";
  const out = items.filter((x) => !x.inGrid && x.dated);
  const shown = items.filter((x) => x.inGrid || grid.showOut);
  const pinned = shown.filter((x) => x.pin).sort((a, b) => a.pin.position - b.pin.position || a.pin.id - b.pin.id);
  const rest = shown.filter((x) => !x.pin && x.dated).sort((a, b) => b.ts - a.ts || String(b.key).localeCompare(String(a.key)));
  const undated = shown.filter((x) => !x.pin && !x.dated);
  return { tiles: [...pinned, ...rest], undated, out };
}

// ── Reordering: Shift (the dates stay, the posts move through them) or Swap (two posts trade dates) ──

const movable = (it) => it.idea && !it.posted && !it.live && !it.pin && it.dated;
// The dates every tile would have if `key` were dropped at place `to` of `order` (the tiles' keys as they were
// when it was picked up). Shift: the movable posts take the movable posts' dates in their new order, newest
// first, so the schedule's days stay and the posts move through them. Swap: it trades dates with the post at `to`.
function arrange(order, key, to, swap) {
  const byKey = new Map(compose().tiles.map((x) => [x.key, x]));
  const draft = new Map(grid.draft);
  const it = byKey.get(key);
  if (!it || !movable(it)) return draft;
  if (swap) {
    const partner = byKey.get(order[to]);
    if (!partner || partner.key === key || !movable(partner)) return draft;
    draft.set(it.idea.id, partner.day);
    draft.set(partner.idea.id, it.day);
    return draft;
  }
  const keys = order.filter((k) => k !== key);
  keys.splice(to, 0, key);
  const slots = order.map((k) => byKey.get(k)).filter(movable).map((x) => x.day).sort().reverse();
  keys.filter((k) => movable(byKey.get(k))).forEach((k, n) => draft.set(byKey.get(k).idea.id, slots[n]));
  return draft;
}

// ── Rendering ───────────────────────────────────────────────────────────────

export async function showGrid() {
  const fresh = root().hidden;
  root().hidden = false;
  if (fresh) {
    window.scrollTo(0, 0);
    grid.sig = "";
    render(true);   // the header and empty tiles now; the posts as soon as they're in
  }
  try { await loadGrid(); } catch (err) {
    root().innerHTML = `<div class="pg-wrap">${backHTML()}<p class="error-text">Couldn't load the grid: ${esc(err.message)}</p></div>`;
    return;
  }
  render(true);
  if (fresh) root().querySelector("h1")?.focus({ preventScroll: true });
}
export function hideGrid() {
  if (root().hidden) return;
  sorter?.stop();
  closePanel(false);
  root().hidden = true;
  root().innerHTML = "";
  grid.sig = "";
}
// A drag in hand (tiles or photos) or typing: a live change waits for it.
const busy = () => !!(sorter?.held() || photoSort?.held());
export async function loadGridPage() {
  if (busy() || typingIn(root())) { grid.pending = true; return; }
  await loadGrid();
  render();
  if (grid.panel && $("#pg-panel")) await openPanel(grid.panel, { keep: true });
}

const backHTML = () => `<div class="row"><a class="btn" href="#/" aria-label="Back to the plan">‹ Plan</a></div>`;

function headerHTML(tiles) {
  const p = profile() || {};
  const handle = p.username || "urbanmakers_sg";
  const avatar = stillUrl(p.avatar_path);
  const num = (n) => (n == null ? "–" : new Intl.NumberFormat("en", { notation: n >= 10000 ? "compact" : "standard" }).format(n));
  const counts = p.source === "account" && [p.media_count, p.followers, p.follows].some((n) => n != null);
  const upcoming = tiles.filter((x) => !x.posted).length;
  return `<header class="pg-profile" aria-label="The profile">
    <div class="pg-top">
      <span class="pg-avatar" aria-hidden="true">${avatar ? `<img src="${esc(avatar)}" alt="">` : `<span>UM</span>`}</span>
      ${counts ? `<dl class="pg-counts"><div><dt>posts</dt><dd>${esc(num(p.media_count))}</dd></div><div><dt>followers</dt><dd>${esc(num(p.followers))}</dd></div><div><dt>following</dt><dd>${esc(num(p.follows))}</dd></div></dl>`
        : `<p class="pg-counts-none muted small">${p.source === "account" ? "" : "Counts show once Instagram is connected."}</p>`}
    </div>
    <p class="pg-name">${esc(p.name || "Urban Makers")}</p>
    <p class="pg-handle">@${esc(handle)}</p>
    ${p.biography ? `<p class="pg-bio">${esc(p.biography)}</p>` : `<p class="pg-bio muted small">${p.source === "account" ? "No bio on the account." : "The bio shows once Instagram is connected."}</p>`}
    <p class="pg-plan small">${esc(upcoming ? `${plural(upcoming, "post")} planned or scheduled above the posted ones` : "Nothing planned yet: give ideas a posting date, or use Spread these")}</p>
  </header>`;
}

// A tile in three parts: its look (class, words for screen readers), what it shows (the picture, marks), and the
// date chip. The middle never changes while dragging, so a redraw keeps the tile itself (patchTiles).
function tileParts(it, dragging) {
  // A draft's own laid-out cover (served here at /plan/), else the still the planner has.
  const still = stillUrl(it.thumb);
  const src = it.made != null && underPlan && !it.posted && !it.live && it.format === "carousel"
    ? `/media/production/${encodeURIComponent(it.made)}/thumb?v=${encodeURIComponent(it.version)}` : still;
  const words = !src && it.format === "carousel" && it.coverText;
  const date = it.day ? dayLabel(it.day) : "";
  const pin = it.pin ? (it.posted ? (it.pin.done_at ? "Pinned" : "Pin it in the app") : `Pin after ${dayLabel(it.day) || "it posts"}`) : "";
  const showDate = (grid.dates || dragging) && date && !it.posted;
  const label = [it.title, it.mark?.text, !it.posted && date ? `Goes out ${date}` : "", pin, it.fact ? `Needs a fact from ${ownerName()}` : "",
    it.inGrid ? "" : "Not in the grid: Reels tab only", movable(it) ? "Open it, or press Space to move it" : "Open it"].filter(Boolean).join(". ");
  const cls = `pg-tile${it.posted ? " posted" : ""}${movable(it) ? " can-move" : ""}${it.changed ? " changed" : ""}${it.inGrid ? "" : " out"}${sorter?.dragging() === it.key ? " is-dragging" : ""}${grid.panel === it.key ? " on" : ""}`;
  const body = `${src ? `<img src="${esc(src)}" alt="" loading="lazy" draggable="false"${still && still !== src ? ` data-fallback="${esc(still)}"` : ""}>` : words ? `<span class="pg-words"><span>${esc(it.coverText)}</span></span>` : `<span class="pg-none">${esc(it.title)}</span>`}
    <span class="pg-corner">${it.mark ? window.StudioMarks.html(it.mark, { small: true, title: false }) : ""}${it.fact ? window.StudioMarks.fact("", { small: true, title: false }) : ""}</span>
    <span class="pg-format" aria-hidden="true">${it.pin ? PIN : it.format === "reel" ? REEL : it.format === "carousel" ? STACK : ""}</span>`;
  const chip = showDate || pin ? `<span class="pg-date${it.changed ? " moved" : ""}">${esc(pin && !it.posted ? pin : pin || date)}</span>` : "";
  return { cls, label, body, chip };
}
function tileHTML(it, i, dragging) {
  const t = tileParts(it, dragging);
  return `<button type="button" class="${t.cls}" data-key="${esc(it.key)}" aria-label="${esc(t.label)}">${t.body}${t.chip}</button>`;
}
// The tiles in a new order, keeping each tile that's still there (its picture isn't fetched again, and a finger
// dragging it keeps hold: touch events go to the element the finger first touched).
const bodies = new WeakMap();
function patchTiles(box, view, dragging) {
  const have = new Map([...box.querySelectorAll(".pg-tile")].map((el) => [el.dataset.key, el]));
  const nodes = [];
  view.forEach((it, i) => {
    const t = tileParts(it, dragging);
    let el = have.get(it.key);
    if (!el || bodies.get(el) !== t.body) {
      const tpl = document.createElement("template");
      tpl.innerHTML = tileHTML(it, i, dragging).trim();
      const fresh = tpl.content.firstElementChild;
      bodies.set(fresh, t.body);
      if (el && document.activeElement === el) requestAnimationFrame(() => fresh.focus({ preventScroll: true }));
      el = fresh;
    } else {
      if (el.className !== t.cls) el.className = t.cls;
      if (el.getAttribute("aria-label") !== t.label) el.setAttribute("aria-label", t.label);
      const old = el.querySelector(".pg-date");
      if ((old?.outerHTML || "") !== t.chip) {
        old?.remove();
        if (t.chip) el.insertAdjacentHTML("beforeend", t.chip);
      }
    }
    nodes.push(el);
    if (i % 3 === 2 && i < view.length - 1) { const line = document.createElement("span"); line.className = "pg-rowline"; line.setAttribute("aria-hidden", "true"); nodes.push(line); }
  });
  if (!view.length) { box.innerHTML = tilesHTML(view, dragging); return; }
  // Moved into place one by one (insertBefore), never all taken out at once: each stays in the page.
  for (const x of [...box.children]) if (!nodes.includes(x)) x.remove();
  nodes.forEach((n, k) => { if (box.children[k] !== n) box.insertBefore(n, box.children[k] || null); });
}
const PIN = `<svg viewBox="0 0 24 24"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/></svg>`;
const REEL = `<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M10.5 9.2l4.3 2.8-4.3 2.8z"/></svg>`;
const STACK = `<svg viewBox="0 0 24 24"><rect x="7" y="3.5" width="13.5" height="13.5" rx="2"/><path d="M4 7v11.5A2.5 2.5 0 0 0 6.5 21H17"/></svg>`;

// The tiles in rows of three, a fine line under each row (what Instagram's grid shows as one row).
function tilesHTML(tiles, dragging) {
  let html = "";
  tiles.forEach((it, i) => {
    html += tileHTML(it, i, dragging);
    if (i % 3 === 2 && i < tiles.length - 1) html += `<span class="pg-rowline" aria-hidden="true"></span>`;
  });
  return html || `<p class="pg-empty muted">No posts yet: the account's posts appear once Studio reads them, and planned ones once they have a posting date.</p>`;
}

function render(force = false) {
  const el = root();
  if (!el || el.hidden) return;
  if (!grid.loaded && !grid.data) {
    el.innerHTML = `<div class="pg-wrap"><div class="pg-phone">${headerHTML([])}<div class="pg-tiles skeleton">${Array.from({ length: 9 }, () => `<span class="pg-tile ghost"></span>`).join("")}</div></div></div>`;
    return;
  }
  if (grid.missing) {
    el.innerHTML = `<div class="pg-wrap pg-narrow">${backHTML()}<h1 tabindex="-1">Grid</h1>
      <p><strong>The grid needs a one-time update of the online database.</strong></p>
      <p class="muted">${isOwner() ? "On the laptop, in personal_assistant\\studio: <code>git pull</code>, then <code>.\\venv\\Scripts\\python cloud_setup.py --update</code>. Then this page works." : `${esc(ownerName())} runs a one-time Studio update; then this page works.`}</p></div>`;
    return;
  }
  const { tiles, undated, out } = compose();
  const dragging = !!sorter?.active();
  const view = grid.preview ? applyPreview(tiles) : tiles;
  const sig = JSON.stringify([view.map((t) => [t.key, t.day, t.thumb, t.mark?.text, t.fact, t.pin?.id, t.pin?.done_at, t.changed, stillUrl(t.thumb)]), undated.map((t) => t.key),
    out.length, grid.dates, grid.swap, grid.showOut, dragging, [...grid.draft], profile()]);
  if (!force && sig === grid.sig && el.querySelector(".pg-tiles")) return;
  grid.sig = sig;
  const dirty = [...grid.draft].some(([id, day]) => ideasAll().find((i) => i.id === id)?.post_on !== day);
  const upcoming = tiles.filter((x) => !x.posted).length;
  const rows = upcoming % 3;
  if (el.querySelector(".pg-tiles") && !force) {
    // Only the parts that change while you work: the tiles, the save bar, the note.
    patchTiles($("#pg-tiles"), view, dragging);
    $("#pg-tiles").classList.toggle("dates", grid.dates || dragging);
    $("#pg-savebar").outerHTML = saveBarHTML(dirty);
    $("#pg-note").innerHTML = noteHTML(upcoming, rows, out);
    $("#pg-undated").outerHTML = undatedHTML(undated);
    return;
  }
  keepDrafts(el, () => {
    el.innerHTML = `<div class="pg-wrap">
    <div class="pg-head">${backHTML()}<h1 tabindex="-1">Grid</h1>
      <span class="muted pg-sub">The profile as a phone shows it: newest at the top.</span></div>
    <div class="pg-toolbar" role="toolbar" aria-label="The grid">
      <div class="seg pg-mode" role="group" aria-label="When you drop a post">
        <button type="button" data-pg-mode="shift" class="${grid.swap ? "" : "on"}" aria-pressed="${!grid.swap}" title="The dates stay where they are; the posts move through them">Shift the rest</button>
        <button type="button" data-pg-mode="swap" class="${grid.swap ? "on" : ""}" aria-pressed="${grid.swap}" title="The two posts trade dates; nothing else moves (or hold Alt while dropping)">Swap two</button></div>
      <button type="button" class="btn" data-pg-spread>Spread these…</button>
      <button type="button" class="btn ghost" data-pg-dates>${grid.dates ? "Hide dates" : "Show dates"}</button>
      <a class="btn ghost" href="#/calendar">Posting calendar</a>
      ${marksKey({ cls: "pg-key" })}
    </div>
    <div class="pg-cols">
      <div class="pg-phone">
        ${headerHTML(tiles)}
        <p class="pg-note small" id="pg-note" aria-live="polite">${noteHTML(upcoming, rows, out)}</p>
        <div class="pg-tiles${grid.dates || dragging ? " dates" : ""}" id="pg-tiles" role="list" aria-label="Posts, newest first, three to a row"></div>
        ${undatedHTML(undated)}
        <p class="pg-how muted small">Drag a post to move it (on a phone, hold it a moment first). On a keyboard: Space picks it up, the arrows move it, Space drops it. Posted and scheduled posts stay where they are.</p>
      </div>
      <aside class="pg-panel" id="pg-panel" aria-label="The post" ${grid.panel ? "" : "data-empty"}>${grid.panel ? `<p class="muted"><span class="spin"></span> Loading…</p>` : panelEmptyHTML()}</aside>
    </div>
    ${saveBarHTML(dirty)}
    <p class="sr-only" id="pg-live" aria-live="assertive"></p>
  </div>`;
  });
  patchTiles($("#pg-tiles"), view, dragging);
  if (grid.panel) openPanel(grid.panel, { keep: true });
}

function noteHTML(upcoming, rows, out) {
  const parts = [];
  if (upcoming && rows) parts.push(`${esc(plural(upcoming, "post"))} coming up. Rows line up when posts come in threes: ${rows === 1 ? "2 more" : "1 more"} ${rows === 1 ? "make" : "makes"} the top row whole.`);
  else if (upcoming) parts.push(`${esc(plural(upcoming, "post"))} coming up, ${esc(plural(upcoming / 3, "whole row"))}.`);
  if (out.length) parts.push(`${esc(plural(out.length, "Reel"))} ${out.length === 1 ? "is" : "are"} kept out of the grid (Reels tab only). <button type="button" class="btn-link" data-pg-out>${grid.showOut ? "Hide" : "Show"} ${out.length === 1 ? "it" : "them"}</button>`);
  return parts.join(" ");
}

function undatedHTML(undated) {
  if (!undated.length) return `<div id="pg-undated" hidden></div>`;
  return `<section class="pg-undated" id="pg-undated" aria-labelledby="pg-undated-h">
    <h2 class="um-label" id="pg-undated-h">No posting date yet · ${esc(undated.length)}</h2>
    <div class="pg-strip">${undated.map((it) => `<button type="button" class="pg-chip" data-key="${esc(it.key)}" aria-label="${esc(`${it.title}. ${it.mark?.text || ""}. No posting date yet: open it to give it one`)}">
      ${it.mark ? window.StudioMarks.html(it.mark, { small: true, title: false }) : ""}<span>${esc(it.title)}</span></button>`).join("")}</div>
    <p class="muted small">Open one to give it a date, or use Spread these.</p>
  </section>`;
}

function saveBarHTML(dirty) {
  const n = [...grid.draft].filter(([id, day]) => ideasAll().find((i) => i.id === id)?.post_on !== day).length;
  return `<div class="pg-savebar${dirty ? " on" : ""}" id="pg-savebar" ${dirty ? "" : "hidden"} role="region" aria-label="Unsaved order">
    <span>${esc(dirty ? `${plural(n, "date")} changed, not saved yet` : "")}</span>
    <button type="button" class="btn" data-pg-undo>Undo</button>
    <button type="button" class="btn act" data-pg-save>Save this order</button></div>`;
}

const panelEmptyHTML = () => `<div class="pg-panel-empty"><p><strong>Tap a post</strong> to see its slides, caption and where it is.</p>
  <p class="muted small">Change the photos and caption of a post that isn't made yet. Posting stays Approve, then Schedule.</p></div>`;

// While dragging: the tiles in the order they'd have if dropped now (grid.preview = the draft that would be).
function applyPreview(tiles) {
  const save = grid.draft;
  grid.draft = grid.preview;
  const { tiles: t } = compose();
  grid.draft = save;
  return t;
}

// ── Drag ────────────────────────────────────────────────────────────────────

// One drag helper for the page's tiles (bound to the page itself, which stays while its parts are redrawn).
let sorter = null;
let pickedOrder = null;
function bindSorter() {
  if (sorter) return;
  const box = root();
  const keys = () => [...box.querySelectorAll(".pg-tile")].map((x) => x.dataset.key);
  sorter = sortable(box, {
    item: ".pg-tile",
    key: (el) => el.dataset.key,
    can: (el) => compose().tiles.find((x) => x.key === el.dataset.key)?.why || "",
    cols: () => 3,
    over: (key, to) => {
      if (!pickedOrder) pickedOrder = keys();
      // `to` is a place on the grid: Shift puts it there, Swap trades with whatever was there at pick-up.
      grid.preview = arrange(pickedOrder, key, to, grid.swap || altHeld);
      render();
    },
    drop: (key) => {
      if (grid.preview) {
        grid.draft = grid.preview;
        mark("grid_drag", { swap: grid.swap || altHeld, keys: keysDrag });
      }
      grid.preview = null;
      pickedOrder = null;
      render();
      say("Moved. Save this order to keep it.");
      catchUp();
      box.querySelector(`.pg-tile[data-key="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
    },
    cancel: () => { grid.preview = null; pickedOrder = null; render(); catchUp(); },
    say: (text) => say(text),
    why: (text) => toast(text, 5000),
  });
}
let altHeld = false, keysDrag = false;
window.addEventListener("keydown", (e) => { if (e.key === "Alt") altHeld = true; if (e.key === " " && e.target.closest?.(".pg-tile")) keysDrag = true; });
window.addEventListener("keyup", (e) => { if (e.key === "Alt") altHeld = false; });
window.addEventListener("pointerdown", (e) => { altHeld = e.altKey; keysDrag = false; }, true);
window.addEventListener("pointermove", (e) => { if (sorter?.active()) altHeld = e.altKey; });
// Live changes held back while dragging: now.
// A press let go without a drag: what waited for it, now.
for (const ev of ["pointerup", "touchend", "touchcancel"]) window.addEventListener(ev, () => setTimeout(() => { if (!busy()) catchUp(); }, 60));
function catchUp() { if (grid.pending && gridOpen()) { grid.pending = false; setTimeout(() => loadGridPage().catch(() => {}), 0); } }
function say(text) { const l = $("#pg-live"); if (l) l.textContent = text; }

// ── Saving the order ────────────────────────────────────────────────────────

async function saveOrder() {
  const changes = [...grid.draft].map(([id, day]) => ({ id, post_on: day || null, was: ideasAll().find((i) => i.id === id)?.post_on ?? null }))
    .filter((c) => c.post_on !== c.was);
  if (!changes.length) { grid.draft.clear(); render(); return; }
  const btn = $("[data-pg-save]");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  try {
    const n = must(await sb.rpc("set_post_dates", { p_dates: changes.map(({ id, post_on }) => ({ id, post_on })) }));
    grid.saved = changes;
    grid.draft.clear();
    grid.data.extra = [];
    mark("grid_saved", { n: changes.length });
    await loadGrid();
    render(true);
    toast(`Saved ${plural(Number(n) || changes.length, "posting date")}.`, 7000, { label: "Undo", run: undoSaved, mark: "grid_undo_saved" });
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = "Save this order"; }
    toast(`Couldn't save the order: ${err.message}`, 7000);
  }
}
async function undoSaved() {
  const back = grid.saved;
  if (!back?.length) return;
  try {
    must(await sb.rpc("set_post_dates", { p_dates: back.map((c) => ({ id: c.id, post_on: c.was })) }));
    grid.saved = null;
    await loadGrid();
    render(true);
    toast("Back to the order before.");
  } catch (err) { toast(`Couldn't undo: ${err.message}`, 7000); }
}
window.addEventListener("beforeunload", (e) => {
  if (!gridOpen() || ![...grid.draft].some(([id, day]) => ideasAll().find((i) => i.id === id)?.post_on !== day)) return;
  e.preventDefault();
  e.returnValue = "";
});

// ── The panel: one post ─────────────────────────────────────────────────────

const PANEL_COLS = "id,title,format,post_type,decision,status,production,caption,hook,cta,slides,post_on,in_grid,needs_fact,slide_layout,photos_confirmed_at,project_id";
const panelStep = { on: false };
async function openPanel(key, opts = {}) {
  if (opts.keep && photoSort?.held()) { grid.pending = true; return; }
  const it = [...compose().tiles, ...compose().undated, ...compose().out].find((x) => x.key === key);
  const box = $("#pg-panel");
  if (!it || !box) { closePanel(); return; }
  if (grid.panel !== key || !opts.keep) mark("grid_panel", { posted: it.posted });
  grid.panel = key;
  box.removeAttribute("data-empty");
  document.body.classList.toggle("pg-sheet-open", PHONE.matches);
  if (PHONE.matches && !panelStep.on) { history.pushState({ ...(history.state || {}), gridPanel: true }, ""); panelStep.on = true; }
  for (const t of $$("#pg-tiles .pg-tile.on")) t.classList.remove("on");
  $(`#pg-tiles [data-key="${CSS.escape(key)}"]`)?.classList.add("on");
  let idea = null;
  if (it.idea) {
    try {
      idea = must(await sb.from("ideas").select(PANEL_COLS).eq("id", it.idea.id).maybeSingle());
    } catch (err) { box.innerHTML = `<p class="error-text">Couldn't load it: ${esc(err.message)}</p>`; return; }
    if (grid.panel !== key) return;
    if (idea) {
      idea.slides = prepSlides(idea.slides);
      idea.post = it.post;
      await signPaths(slidePaths(idea.slides)).catch(() => {});
    }
  }
  grid.idea = idea;
  const review = idea?.production?.id != null && underPlan ? await readSummary(idea.production.id) : null;
  if (grid.panel !== key) return;
  keepDrafts(box, () => { box.innerHTML = panelHTML(it, idea, review); });
  if (!opts.keep) box.querySelector(".pg-panel-close")?.focus({ preventScroll: true });
  if (!PHONE.matches && !opts.keep) box.scrollIntoView({ block: "nearest" });
}
export function closePanel(back = true) {
  grid.panel = null;
  grid.idea = null;
  document.body.classList.remove("pg-sheet-open");
  for (const t of $$("#pg-tiles .pg-tile.on")) t.classList.remove("on");
  const box = $("#pg-panel");
  if (box) { box.setAttribute("data-empty", ""); box.innerHTML = panelEmptyHTML(); }
  if (panelStep.on) { panelStep.on = false; if (back && history.state?.gridPanel) history.back(); }
}
window.addEventListener("popstate", () => { if (panelStep.on && !history.state?.gridPanel) { panelStep.on = false; closePanel(false); } });

const readCache = new Map();
async function readSummary(productionId) {
  if (readCache.has(productionId)) return readCache.get(productionId);
  try {
    const res = await fetch(`/api/production/${encodeURIComponent(productionId)}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const j = res.ok ? await res.json() : null;
    const r = j?.review ? { verdict: j.review.verdict || "", fixes: (j.review.fixes || []).slice(0, 3).map((f) => f.what).filter(Boolean),
      slides: j.slides_count || (Array.isArray(j.slides?.files) ? j.slides.files.length : 0) } : { slides: j?.slides_count || 0 };
    readCache.set(productionId, r);
    return r;
  } catch { return null; }
}

function slidesViewHTML(it, idea, review) {
  // A draft's laid-out slides (served here at /plan/), else the slides as planned, else the post's own still.
  let pics = [];
  if (idea?.production?.id != null && underPlan && idea.format === "carousel") {
    const n = review?.slides || idea.slides.length;
    pics = Array.from({ length: n }, (_, k) => ({ src: `/media/production/${idea.production.id}/slide/${k + 1}?v=${idea.production.version || 0}` }));
  } else if (idea?.format === "carousel" && idea.slides.length) {
    pics = idea.slides.map((x) => ({ src: stillUrl(x.image?.thumb_path), words: x.heading }));
  } else {
    const src = idea?.production?.id != null && underPlan ? `/media/production/${idea.production.id}/thumb?v=${idea.production.version || 0}` : stillUrl(it.thumb);
    pics = [{ src, words: src ? "" : it.title, reel: it.format === "reel" }];
  }
  return `<div class="pg-slides" aria-label="${esc(plural(pics.length, "slide"))}, swipe to see them">
    <div class="pg-track" id="pg-track" tabindex="0">${pics.map((p, k) => `<figure class="pg-slide">${p.src ? `<img src="${esc(p.src)}" alt="${esc(`Slide ${k + 1}`)}" loading="lazy">` : `<span class="pg-words big"><span>${esc(p.words || "No picture yet")}</span></span>`}
      ${p.reel ? `<span class="pg-reel-tag">Reel</span>` : ""}<figcaption>${k + 1} / ${pics.length}</figcaption></figure>`).join("")}</div>
    ${pics.length > 1 ? `<div class="pg-slide-nav"><button type="button" class="icon-btn" data-pg-slide="-1" aria-label="Previous slide">‹</button><span class="muted small">Swipe</span><button type="button" class="icon-btn" data-pg-slide="1" aria-label="Next slide">›</button></div>` : ""}
  </div>`;
}

function panelHTML(it, idea, review) {
  const owner = isOwner();
  const made = idea?.production?.id != null;
  const prod = idea?.production || null;
  const reviewHref = prod ? (underPlan ? `/#/production/${prod.id}` : /^https?:\/\//i.test(String(cfg.studioUrl || "")) ? `${String(cfg.studioUrl).replace(/\/+$/, "")}/#/production/${prod.id}` : "") : "";
  const pinned = !!it.pin;
  const pins = grid.data?.pins?.length || 0;
  const date = it.day ? dayLabel(it.day, { year: false }) : "";
  const where = it.posted ? `Posted ${date}` : it.live ? (it.post.state === "failed" ? "Couldn't post" : `Scheduled ${SP()?.whenText(it.post.scheduled_at) || date}`) : date ? `Planned for ${date}${it.changed ? " (not saved yet)" : ""}` : "No posting date yet";
  const caption = idea ? (made ? `<div class="pg-cap"><h3 class="um-label">Caption</h3><p class="pp-caption">${esc(idea.caption || "No caption yet.")}</p>
        <p class="muted small">${prod?.status === "approved" ? "Change it when you schedule: the Schedule dialog has the caption." : "The draft's caption: send it back in Review to change it."}</p></div>`
      : `<div class="field pg-cap" data-saved-scope><h3><label class="um-label" for="pg-caption">Caption</label> <span class="saved" aria-live="polite"></span></h3>
        <textarea id="pg-caption" data-pg-field="caption" rows="5" maxlength="5000" data-orig="${esc(idea.caption || "")}" placeholder="The Instagram caption, with hashtags">${esc(idea.caption || "")}</textarea></div>`)
    : it.media?.caption || it.post?.caption ? `<div class="pg-cap"><h3 class="um-label">Caption</h3><p class="pp-caption">${esc(it.media?.caption || it.post?.caption)}</p></div>` : "";
  const reel = it.format === "reel";
  return `<div class="pg-panel-head">
      <div class="pg-panel-title"><h2>${esc(it.title)}</h2>
        <p class="pg-status">${it.mark ? window.StudioMarks.html(it.mark, { small: true }) : ""}<span>${esc(it.mark?.text || "")}</span></p>
        <p class="muted small">${esc([where, POST_TYPE[it.post_type], reel ? "Reel" : it.format === "carousel" ? "Carousel" : "Photo", it.fromPhone ? "Posted from the phone" : ""].filter(Boolean).join(" · "))}</p></div>
      <button type="button" class="icon-btn pg-panel-close" data-pg-close aria-label="Close">×</button></div>
    ${slidesViewHTML(it, idea, review)}
    ${it.fact ? `<div class="box fact-box">${window.StudioMarks.fact("", { title: false })}<p><strong>Needs a fact from ${esc(ownerName())}.</strong> ${esc(it.factText ? (/[.?!]$/.test(it.factText) ? it.factText : `${it.factText}.`) : "")}${it.idea?.check ? " The words have [check] where a figure goes." : ""}</p></div>` : ""}
    ${review?.verdict ? `<div class="pg-read"><h3 class="um-label">Studio's read</h3><p>${esc(review.verdict)}</p>${review.fixes.length ? `<ul>${review.fixes.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}</div>`
      : made && !underPlan ? `<p class="muted small">Studio's read of the draft is in Review.</p>` : ""}
    ${caption}
    ${idea && idea.format === "carousel" ? photosHTML(idea, { cls: "pg-photos" }) : ""}
    ${idea && !it.posted && !it.live ? `<div class="pg-fields" data-saved-scope>
        <label class="field"><span class="um-label">Posting date</span><input type="date" data-pg-field="post_on" value="${esc(it.day || "")}" aria-label="Posting date"></label>
        ${reel ? `<label class="check-field"><input type="checkbox" data-pg-field="in_grid" ${it.inGrid ? "checked" : ""}> Show it in the profile grid</label>` : ""}
        <span class="saved" aria-live="polite"></span>
        <p class="muted small">A new date moves it in the grid; Save this order keeps it.</p></div>` : ""}
    ${idea && reel && it.live ? `<label class="check-field pg-fields"><input type="checkbox" data-pg-field="in_grid" ${it.inGrid ? "checked" : ""}> Show it in the profile grid</label>` : ""}
    <div class="pg-panel-btns">
      ${it.idea || it.media ? (pinned ? `<button type="button" class="btn" data-pg-unpin>Unpin</button>
          ${it.posted && !it.pin.done_at ? `<button type="button" class="btn good" data-pg-pinned>It's pinned in the app</button>` : ""}`
        : `<button type="button" class="btn" data-pg-pin ${offWhy(pins >= MAX_PINS ? `Instagram pins at most ${MAX_PINS} posts. Unpin one first.` : "")}>Pin to the top</button>`) : ""}
      ${idea && !made ? `<button type="button" class="btn" data-pg-fact>${it.fact && idea.needs_fact != null ? "The fact is in" : `Needs a fact from ${esc(ownerName())}`}</button>` : ""}
      ${it.idea ? `<a class="btn" href="#/idea/${esc(it.idea.id)}">Open the idea</a>` : ""}
      ${reviewHref && !it.posted ? `<a class="btn" href="${esc(reviewHref)}">Open the draft in Review</a>` : ""}
      ${prod?.status === "approved" && !it.live && !it.posted ? (canSchedule() ? `<button type="button" class="btn act" data-pg-schedule="${esc(prod.id)}">Schedule…</button>`
        : `<button type="button" class="btn act" ${offWhy(owner ? "Scheduling needs the Studio address. It appears when Studio is on, or schedule it in Review." : `${ownerName()} schedules approved posts.`)}>Schedule…</button>`) : ""}
      ${it.permalink ? `<a class="btn" href="${esc(it.permalink)}" target="_blank" rel="noopener noreferrer">Open on Instagram ↗</a>` : ""}
    </div>
    ${pinned ? `<p class="muted small">${it.posted ? (it.pin.done_at ? "Pinned on Instagram too." : "Instagram's API can't pin: press and hold the post in the Instagram app, then Pin to your profile.") : `Pin after ${esc(date || "it posts")}: Studio can't pin for you, so it reminds you on Plan once it's out.`}</p>` : ""}
    <p class="muted small pg-nopost">Nothing posts from here. ${prod?.status === "approved" ? "Schedule sets its time." : made ? "Bryan approves it in Review, then schedules it." : "Make it, approve it in Review, then schedule it."}</p>`;
}

async function savePanelField(el) {
  const f = el.dataset.pgField;
  const it = compose().tiles.concat(compose().undated, compose().out).find((x) => x.key === grid.panel);
  const idea = grid.idea;
  if (!it || !idea) return;
  if (f === "post_on") {
    grid.draft.set(idea.id, el.value || null);
    if (!el.value) grid.draft.set(idea.id, null);
    mark("grid_date", {});
    render();
    openPanel(grid.panel, { keep: true });
    say("The date changed. Save this order to keep it.");
    return;
  }
  if (f === "in_grid") {
    must(await sb.from("ideas").update({ in_grid: el.checked }).eq("id", idea.id));
    mark("grid_flag", { in: el.checked });
    toast(el.checked ? "It shows in the profile grid." : "Reels tab only: it won't show in the grid.");
    await loadGrid();
    render();
    return;
  }
  if (f === "caption") {
    must(await sb.from("ideas").update({ caption: el.value }).eq("id", idea.id));
    idea.caption = el.value;
    el.dataset.orig = el.value;
    const s = el.closest("[data-saved-scope]")?.querySelector(".saved");
    if (s) { s.textContent = "Saved"; s.classList.add("show"); setTimeout(() => s.classList.remove("show"), 1600); }
    mark("grid_caption");
  }
}

// ── Spread these: many ideas, one date each, in order ───────────────────────

const WEEK = [["1", "Mon"], ["2", "Tue"], ["3", "Wed"], ["4", "Thu"], ["5", "Fri"], ["6", "Sat"], ["0", "Sun"]];
const spread = { pid: null, ideas: [], picked: [], start: "", days: new Set(["1", "3", "5"]), skip: false };
function nextMonday() {
  const d = new Date();
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  return isoDay(d);
}
export function spreadDates(start, days, n, taken = new Set()) {
  const out = [];
  const d = parseDay(start);
  if (!d || !days.size || n <= 0) return out;
  for (let guard = 0; out.length < n && guard < 800; guard++, d.setDate(d.getDate() + 1)) {
    const iso = isoDay(d);
    if (days.has(String(d.getDay())) && !taken.has(iso)) out.push(iso);
  }
  return out;
}

async function openSpread() {
  const { plan, loadProjects } = await import("./board.js");
  if (!plan.projects.length) await loadProjects();
  const active = plan.projects.filter((p) => p.status === "active");
  spread.pid = spread.pid && active.some((p) => p.id === spread.pid) ? spread.pid : plan.pid && active.some((p) => p.id === plan.pid) ? plan.pid : active[0]?.id;
  spread.start = spread.start || nextMonday();
  spread.picked = [];
  openModal(`<h2 id="modal-title">Spread these</h2>
    <p class="muted">Pick the posts in the order they should go out, a start day and the weekdays. Each gets the next day in turn.</p>
    <div class="sp-row"><label class="field"><span class="um-label">Project</span><select id="sp-project">${active.map((p) => `<option value="${esc(p.id)}" ${p.id === spread.pid ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label class="field"><span class="um-label">Pick by number</span><span class="row tight"><input id="sp-range" placeholder="e.g. 24-44" inputmode="numeric" autocomplete="off"><button type="button" class="btn" data-sp-range>Pick</button></span></label></div>
    <div id="sp-list" class="sp-list" aria-live="polite"><p class="muted"><span class="spin"></span> Loading…</p></div>
    <h3 class="um-label">In this order</h3>
    <ol id="sp-order" class="sp-order"></ol>
    <div class="sp-row"><label class="field"><span class="um-label">Starting</span><input type="date" id="sp-start" value="${esc(spread.start)}"></label>
      <fieldset class="field sp-days"><legend class="um-label">On</legend><div class="seg" role="group" aria-label="Weekdays">${WEEK.map(([v, l]) => `<button type="button" data-sp-day="${v}" class="${spread.days.has(v) ? "on" : ""}" aria-pressed="${spread.days.has(v)}">${l}</button>`).join("")}</div></fieldset></div>
    <label class="check-field"><input type="checkbox" id="sp-skip" ${spread.skip ? "checked" : ""}> Skip days that already have a post</label>
    <p class="small" id="sp-preview" aria-live="polite"></p>
    <p class="error-text" id="modal-error" role="alert" hidden></p>
    <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="button" class="btn act" id="sp-go">Spread them</button></div>`, spreadClick, true, "sheet");
  $("#sp-project").addEventListener("change", (e) => { spread.pid = Number(e.target.value); spread.picked = []; loadSpread(); });
  $("#sp-start").addEventListener("change", (e) => { spread.start = e.target.value; spreadPreview(); });
  $("#sp-skip").addEventListener("change", (e) => { spread.skip = e.target.checked; spreadPreview(); });
  $("#sp-range").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); pickRange(); } });
  orderSorter();
  loadSpread();
}
async function loadSpread() {
  const box = $("#sp-list");
  if (!box) return;
  try {
    spread.ideas = must(await sb.from("ideas").select("id,title,format,post_type,decision,status,production,post_on,needs_fact,in_grid,slide_layout,slides,caption,hook,cta,project_id")
      .eq("project_id", spread.pid).neq("status", "dismissed").order("position").order("id"));
  } catch (err) { box.innerHTML = `<p class="error-text">${esc(err.message)}</p>`; return; }
  const live = new Set((grid.data?.posts || []).filter((p) => p.idea_id && p.state !== "cancelled").map((p) => p.idea_id));
  spread.ideas = spread.ideas.filter((i) => !live.has(i.id) && i.production?.status !== "posted" && i.production?.status !== "rejected");
  renderSpread();
}
function renderSpread() {
  const box = $("#sp-list");
  if (!box) return;
  const picked = new Set(spread.picked);
  box.innerHTML = spread.ideas.length ? `<ul class="sp-ideas">${spread.ideas.map((i) => `<li><label class="check-field"><input type="checkbox" data-sp-pick="${esc(i.id)}" ${picked.has(i.id) ? "checked" : ""}>
      <span class="mono sp-num">#${esc(i.id)}</span> ${kindDot(i.post_type)}<span class="sp-title">${esc(i.title)}</span>${i.post_on ? `<span class="muted small">${esc(dayLabel(i.post_on))}</span>` : ""}</label></li>`).join("")}</ul>`
    : `<p class="muted">No ideas waiting to go out in this project.</p>`;
  const order = $("#sp-order");
  order.innerHTML = spread.picked.map((id, k) => {
    const i = spread.ideas.find((x) => x.id === id);
    return `<li class="sp-item" data-key="${esc(id)}"><span class="mono">${k + 1}</span><span class="sp-title">${esc(i?.title || `#${id}`)}</span>
      <span class="sp-moves"><button type="button" class="icon-btn" data-sp-move="-1" ${offWhy(k ? "" : "It's first.")} aria-label="Earlier">↑</button><button type="button" class="icon-btn" data-sp-move="1" ${offWhy(k < spread.picked.length - 1 ? "" : "It's last.")} aria-label="Later">↓</button></span></li>`;
  }).join("") || `<li class="muted small">Tick the posts above, or pick them by number. Drag (or ↑ ↓) to change the order.</li>`;
  spreadPreview();
}
function takenDays() {
  const out = new Set();
  for (const t of compose().tiles) if (t.day && !spread.picked.includes(t.idea?.id)) out.add(t.day);
  return out;
}
function spreadPreview() {
  const el = $("#sp-preview");
  if (!el) return;
  const n = spread.picked.length;
  const dates = spreadDates(spread.start, spread.days, n, spread.skip ? takenDays() : new Set());
  const clash = spread.skip ? 0 : dates.filter((d) => takenDays().has(d)).length;
  el.innerHTML = !n ? "Nothing picked yet." : !spread.days.size ? `<span class="error-text">Pick at least one weekday.</span>`
    : `${esc(plural(n, "post"))}: ${esc(dayLabel(dates[0], { year: true }))} to ${esc(dayLabel(dates[dates.length - 1], { year: true }))}.${clash ? ` <span class="warn-text">${esc(plural(clash, "day"))} already ${clash === 1 ? "has" : "have"} a post.</span>` : ""} Nothing is saved until Save this order.`;
}
function pickRange() {
  const text = $("#sp-range")?.value || "";
  const ids = new Set(spread.ideas.map((i) => i.id));
  const want = [];
  for (const part of text.split(/[,\s]+/).filter(Boolean)) {
    const m = part.match(/^#?(\d+)(?:\s*[-–]\s*#?(\d+))?$/);
    if (!m) continue;
    const a = Number(m[1]), b = Number(m[2] || m[1]);
    for (let x = Math.min(a, b); x <= Math.max(a, b) && want.length < 200; x++) if (ids.has(x)) want.push(x);
  }
  if (!want.length) { formError(`None of those numbers are ideas in this project that can still get a date.`); return; }
  formError("");
  spread.picked = [...new Set([...spread.picked, ...want])];
  renderSpread();
}
let spreadSort = null;
function orderSorter() {
  const box = $("#sp-order");
  if (!box) return;
  spreadSort = sortable(box, {
    item: ".sp-item", key: (el) => el.dataset.key, cols: () => 1,
    over: (key, to) => {
      const el = box.querySelector(`[data-key="${CSS.escape(key)}"]`);
      const rest = [...box.children].filter((x) => x !== el);
      box.insertBefore(el, rest[to] || null);
    },
    drop: () => { spread.picked = [...box.children].map((x) => Number(x.dataset.key)).filter(Boolean); renderSpread(); },
    cancel: () => renderSpread(),
  });
  void spreadSort;
}
async function spreadClick(e) {
  const t = e.target.closest("button, input[data-sp-pick]");
  if (!t) return;
  if (t.dataset.spPick) {
    const id = Number(t.dataset.spPick);
    spread.picked = t.checked ? [...spread.picked, id] : spread.picked.filter((x) => x !== id);
    renderSpread();
    return;
  }
  if (t.dataset.spRange != null) { pickRange(); return; }
  if (t.dataset.spDay) {
    const v = t.dataset.spDay;
    if (spread.days.has(v)) spread.days.delete(v); else spread.days.add(v);
    t.classList.toggle("on", spread.days.has(v));
    t.setAttribute("aria-pressed", String(spread.days.has(v)));
    spreadPreview();
    return;
  }
  if (t.dataset.spMove) {
    const id = Number(t.closest("[data-key]").dataset.key);
    const k = spread.picked.indexOf(id), j = k + Number(t.dataset.spMove);
    if (j < 0 || j >= spread.picked.length) return;
    [spread.picked[k], spread.picked[j]] = [spread.picked[j], spread.picked[k]];
    renderSpread();
    return;
  }
  if (t.id === "sp-go") {
    const n = spread.picked.length;
    if (!n) { formError("Pick the posts first."); return; }
    if (!spread.days.size) { formError("Pick at least one weekday."); return; }
    const dates = spreadDates(spread.start, spread.days, n, spread.skip ? takenDays() : new Set());
    // The ideas the grid doesn't have yet join it (until saved, from here).
    const have = new Set(ideasAll().map((i) => i.id));
    for (const i of spread.ideas.filter((x) => spread.picked.includes(x.id) && !have.has(x.id))) {
      grid.data.extra.push({ ...i, slides_count: Array.isArray(i.slides) ? i.slides.length : 0, cover: i.slides?.[0]?.image?.thumb_path || null,
        cover_text: i.slides?.[0]?.heading || "", check: false });
    }
    spread.picked.forEach((id, k) => grid.draft.set(id, dates[k]));
    mark("grid_spread", { n, days: [...spread.days].join("") });
    store("studio.gridSpread", JSON.stringify([...spread.days]));
    await closeModal();
    await signPaths(grid.data.extra.map((x) => x.cover).filter(Boolean)).catch(() => {});
    render(true);
    toast(`${plural(n, "date")} set, ${dayLabel(dates[0])} to ${dayLabel(dates[n - 1])}. Save this order to keep them.`, 7000);
  }
}
try { const d = JSON.parse(recall("studio.gridSpread") || "null"); if (Array.isArray(d) && d.length) spread.days = new Set(d.map(String)); } catch { /* none kept */ }

// ── Presses ─────────────────────────────────────────────────────────────────

root().addEventListener("click", async (e) => {
  const t = e.target.closest("button, a");
  if (!t) return;
  const ds = t.dataset;
  try {
    if (t.matches(".pg-tile, .pg-chip")) { await openPanel(ds.key); return; }
    if (ds.pgMode) { grid.swap = ds.pgMode === "swap"; store("studio.gridSwap", grid.swap ? "1" : "0"); mark("grid_mode", { swap: grid.swap }); render(true); return; }
    if (ds.pgDates != null) { grid.dates = !grid.dates; store("studio.gridDates", grid.dates ? "1" : "0"); render(true); return; }
    if (ds.pgOut != null) { grid.showOut = !grid.showOut; render(); return; }
    if (ds.pgSpread != null) { await openSpread(); return; }
    if (ds.pgSave != null) { await saveOrder(); return; }
    if (ds.pgUndo != null) { grid.draft.clear(); grid.data.extra = []; mark("grid_undo"); render(true); toast("Back to the saved order."); return; }
    if (ds.pgClose != null) { closePanel(); return; }
    if (ds.pgSlide) { const tr = $("#pg-track"); tr?.scrollBy({ left: Number(ds.pgSlide) * tr.clientWidth, behavior: "smooth" }); return; }
    if (ds.pgSchedule) { if (t.getAttribute("aria-disabled") !== "true") openSchedule(Number(ds.pgSchedule)); return; }
    const it = compose().tiles.concat(compose().undated, compose().out).find((x) => x.key === grid.panel);
    if (!it) return;
    if (ds.pgPin != null) {
      if (t.getAttribute("aria-disabled") === "true") return;
      const used = new Set((grid.data.pins || []).map((p) => p.position));
      const position = [1, 2, 3].find((n) => !used.has(n)) || 3;
      const row = it.idea ? { idea_id: it.idea.id, position } : { media_id: it.media.media_id, position };
      const { error } = await sb.from("grid_pins").insert(row);
      if (error) throw new Error(/at most 3/.test(error.message) ? `Instagram pins at most ${MAX_PINS} posts. Unpin one first.` : error.message);
      mark("grid_pin");
      toast(it.posted ? "Pinned here. Pin it in the Instagram app too: Studio can't." : `Pinned. Plan reminds you to pin it in the app once it's out.`, 6000);
    } else if (ds.pgUnpin != null) {
      must(await sb.from("grid_pins").delete().eq("id", it.pin.id));
      mark("grid_unpin");
      toast("Unpinned here. Unpin it in the Instagram app too if it's pinned there.");
    } else if (ds.pgPinned != null) {
      must(await sb.from("grid_pins").update({ done_at: new Date().toISOString() }).eq("id", it.pin.id));
      toast("Noted: pinned on Instagram.");
    } else if (ds.pgFact != null && grid.idea) {
      const on = grid.idea.needs_fact == null;
      must(await sb.from("ideas").update({ needs_fact: on ? "" : null }).eq("id", grid.idea.id));
      if (on) mark("fact_flagged", { at: "grid" });
      toast(on ? `Flagged: it needs a fact from ${ownerName()}. Say what in the idea's Details.` : "Marked as filled in.");
    } else {
      const ph = grid.idea ? await photosClick(t, grid.idea) : false;
      if (ph === "picker") return;
      if (ph === "render") { openPanel(grid.panel, { keep: true }); return; }
      if (!ph) return;
    }
    await loadGrid();
    render();
    await openPanel(grid.panel, { keep: true });
  } catch (err) { toast(`Couldn't do that: ${err.message}`, 6000); }
});
root().addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.pgField === "post_on" || el.dataset.pgField === "in_grid") savePanelField(el).catch((err) => toast(`Couldn't save: ${err.message}`));
});
root().addEventListener("focusout", async (e) => {
  const el = e.target;
  try {
    if (el.dataset.pgField === "caption" && el.value !== el.dataset.orig) await savePanelField(el);
    else if (el.dataset.slideField && grid.idea && el.value !== el.dataset.orig) {
      await saveSlideField(el, grid.idea);
      el.dataset.orig = el.value;
      toast("Cover line saved.");
    }
  } catch (err) { toast(`Couldn't save: ${err.message}`); }
});
root().addEventListener("keydown", (e) => {
  if (e.key === "Escape" && grid.panel && !sorter?.active() && !document.querySelector("#modal:not([hidden])")) { e.preventDefault(); closePanel(); }
});
PHONE.addEventListener("change", () => { if (gridOpen()) render(true); });
// A draft's cover that Studio can't serve (the draft was cleared away): the planner's own still instead.
root().addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName === "IMG" && img.dataset.fallback) { img.classList.remove("broken"); img.src = img.dataset.fallback; delete img.dataset.fallback; }
}, true);
// The panel's photos: drag to reorder (slides.js), then the panel and grid again.
let photoSort = null;
function initGrid() {
  if (photoSort) return;
  photoSort = photoSorter(root(), () => grid.idea, async () => { await loadGrid(); render(); if (grid.panel) await openPanel(grid.panel, { keep: true }); });
  bindSorter();
}
initGrid();
// After a photo or a live change: the grid and its panel again (not while dragging or typing).
document.addEventListener("studio:refresh", () => { if (gridOpen() && grid.panel) readCache.clear(); });
void slidesLocked; void ago; void closeModal; void formError;
