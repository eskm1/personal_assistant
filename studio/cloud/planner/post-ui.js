// Posting to Instagram, the dialogs both halves of Studio share (Plan, this folder; Review, served as
// /static/post-ui.js): the Schedule dialog on an approved draft and the Instagram connection box.
// They talk to the Studio machine's posting API (studio/posting_routes.py) through the page's own
// fetch (the planner's Bearer token, or Review's cookie), set with init().
//
//   StudioPost.init({ api(method, path, body) -> json, toast(text), onChange(), media(path) -> url | "" })
//   StudioPost.openSchedule(productionId)   the Schedule / change / try again dialog
//   StudioPost.openConnect()                the Instagram box in a dialog (Review's account menu)
//   StudioPost.mountConnect(element)        the same box on a page (the planner's More > Instagram)
//   StudioPost.captionCheck(text)           the caption limits, as the server checks them
"use strict";
window.StudioPost = (() => {
  const opts = { api: null, toast: () => {}, onChange: () => {}, media: (p) => p, isOwner: () => true };
  const LIMITS = { chars: 2200, hashtags: 30, mentions: 20 };
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, w, many = `${w}s`) => `${n} ${n === 1 ? w : many}`;
  const mmss = (s) => { const t = Math.max(0, Math.round(s || 0)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };

  // ── Singapore time (UTC+8 all year), whatever the browser's own time zone ──
  const SG = 8 * 3600e3;
  const sgParts = (iso) => {
    const t = Date.parse(iso || "");
    if (!t) return null;
    const d = new Date(t + SG);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth(), d: d.getUTCDate(), wd: d.getUTCDay(), h: d.getUTCHours(), mi: d.getUTCMinutes() };
  };
  const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad = (n) => String(n).padStart(2, "0");
  function whenText(iso) {
    const p = sgParts(iso);
    if (!p) return "";
    const h = p.h % 12 || 12;
    return `${WD[p.wd]} ${p.d} ${MON[p.m]}, ${h}:${pad(p.mi)}${p.h < 12 ? "am" : "pm"}`;
  }
  // "7:30pm" (a calendar cell)
  const shortTime = (iso) => { const p = sgParts(iso); return p ? `${p.h % 12 || 12}:${pad(p.mi)}${p.h < 12 ? "am" : "pm"}` : ""; };
  const dayOf = (iso) => { const p = sgParts(iso); return p ? `${p.y}-${pad(p.m + 1)}-${pad(p.d)}` : ""; };
  const timeOf = (iso) => { const p = sgParts(iso); return p ? `${pad(p.h)}:${pad(p.mi)}` : ""; };
  const isoFrom = (day, time) => (day && time ? `${day}T${time}:00+08:00` : "");

  function captionCheck(text) {
    const t = String(text || "");
    const tags = (t.match(/(^|[^\w&])#[\p{L}\p{N}_]+/gu) || []).length;
    const mentions = (t.match(/(^|[^\w.])@[A-Za-z0-9._]{1,30}/g) || []).length;
    const problems = [];
    if (t.length > LIMITS.chars) problems.push(`${t.length - LIMITS.chars} characters too long (Instagram takes ${LIMITS.chars.toLocaleString("en")}).`);
    if (tags > LIMITS.hashtags) problems.push(`${tags} hashtags: Instagram takes ${LIMITS.hashtags} at most.`);
    if (mentions > LIMITS.mentions) problems.push(`${mentions} @mentions: Instagram takes ${LIMITS.mentions} at most.`);
    return { chars: t.length, hashtags: tags, mentions, problems };
  }

  // A button that can't act yet: aria-disabled (so a tap still arrives) and the reason (shell.js shows it).
  const offWhy = (why) => (why ? `aria-disabled="true" data-why="${esc(why)}"` : "");

  // ── The dialog frame (its own, so it works the same on both pages) ─────────
  let modal = null, onClick = null, returnTo = null;
  function frame() {
    if (modal) return modal;
    modal = document.createElement("div");
    modal.className = "modal sp-modal";
    modal.hidden = true;
    modal.innerHTML = `<div class="modal-card sp-card" role="dialog" aria-modal="true" aria-labelledby="sp-title">
      <button type="button" class="close" data-sp-close title="Close (Esc)" aria-label="Close">&times;</button><div class="sp-body"></div></div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", async (e) => {
      if (e.target === modal || e.target.closest("[data-sp-close]")) { close(); return; }
      if (onClick) {
        try { await onClick(e); } catch (err) { showError(err.message); }
      }
    });
    modal.addEventListener("submit", (e) => e.preventDefault());
    modal.addEventListener("input", (e) => modal.oninputHandler?.(e));
    modal.addEventListener("change", (e) => modal.oninputHandler?.(e));
    // Escape closes this dialog only (not the page under it).
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal && !modal.hidden) { e.stopImmediatePropagation(); e.preventDefault(); close(); }
    }, true);
    return modal;
  }
  const body = () => frame().querySelector(".sp-body");
  const $ = (sel) => frame().querySelector(sel);
  function open(html, click, input) {
    const m = frame();
    if (m.hidden) returnTo = document.activeElement;
    body().innerHTML = html;
    onClick = click || null;
    m.oninputHandler = input || null;
    m.hidden = false;
    document.body.classList.add("locked");
    (m.querySelector("[autofocus]") || m.querySelector("textarea, input:not([type=hidden]), button:not(.close)"))?.focus({ preventScroll: true });
  }
  function close() {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    body().innerHTML = "";
    onClick = null;
    modal.oninputHandler = null;
    if (!document.querySelector(".modal:not([hidden]):not(.sp-modal), .viewer:not([hidden])")) document.body.classList.remove("locked");
    returnTo?.focus?.({ preventScroll: true });
    returnTo = null;
  }
  function showError(text) {
    const el = $("#sp-error");
    if (el) { el.textContent = text || ""; el.hidden = !text; }
    else opts.toast(`Couldn't do that: ${text}`);
  }
  const busy = (btn, on, label) => {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.disabled = true; btn.innerHTML = `<span class="spin"></span> ${esc(label || btn.textContent)}`; }
    else { btn.disabled = false; btn.textContent = btn.dataset.label || btn.textContent; }
  };

  // ── Schedule ───────────────────────────────────────────────────────────────
  let plan = null;
  async function openSchedule(productionId) {
    open(`<h2 id="sp-title">Schedule on Instagram</h2><p class="muted"><span class="spin"></span> Loading…</p>`);
    try {
      plan = await opts.api("GET", `/api/instagram/plan/${encodeURIComponent(productionId)}`);
    } catch (err) {
      body().innerHTML = `<h2 id="sp-title">Schedule on Instagram</h2><p class="error-text">Couldn't load it: ${esc(err.message)}</p>
        <div class="row end"><button type="button" class="btn" data-sp-close>Close</button></div>`;
      return;
    }
    renderSchedule();
  }

  function draftLine(p) {
    const what = p.kind === "carousel" ? `Carousel · ${plural(p.slides || 0, "slide")}` : `Reel${p.duration ? ` · ${mmss(p.duration)}` : ""}`;
    const thumb = p.thumb ? opts.media(p.thumb) : "";
    return `<div class="sp-draft">${thumb ? `<img src="${esc(thumb)}" alt="" class="sp-thumb">` : `<span class="sp-thumb none" aria-hidden="true"></span>`}
      <div><strong>${esc(p.title)}</strong><span class="muted small">${esc(what)} · v${esc(p.version)}</span></div></div>`;
  }

  function takenLine(day) {
    const same = (plan.taken || []).filter((t) => dayOf(t) === day);
    if (same.length) return `<span class="warn-text">Another post goes out that day (${esc(same.map(timeOf).join(", "))}).</span>`;
    const near = (plan.taken || []).filter((t) => Math.abs(Date.parse(`${dayOf(t)}T00:00:00Z`) - Date.parse(`${day}T00:00:00Z`)) <= 864e5);
    return near.length ? `<span class="muted">A post goes out the day ${near.length > 1 ? "before and after" : Date.parse(`${dayOf(near[0])}T00:00:00Z`) < Date.parse(`${day}T00:00:00Z`) ? "before" : "after"}.</span>` : "";
  }

  function renderSchedule() {
    const p = plan.production, post = plan.post;
    const live = post && ["scheduled", "failed"].includes(post.state) ? post : null;
    const out = post && (post.state === "posted" || post.state === "publishing") ? post : null;
    const title = out ? "On Instagram" : live ? (post.state === "failed" ? "Couldn't post" : "Scheduled") : "Schedule on Instagram";
    if (out) {
      open(`<h2 id="sp-title">${title}</h2>${draftLine(p)}
        <p>${post.state === "posted" ? `Posted ${esc(whenText(post.posted_at))}.` : `<span class="spin"></span> Going out now.`}</p>
        ${post.permalink ? `<p><a class="btn" href="${esc(post.permalink)}" target="_blank" rel="noopener noreferrer">Open on Instagram ↗</a></p>` : ""}
        <div class="row end"><button type="button" class="btn" data-sp-close>Close</button></div>`);
      return;
    }
    const caption = live ? live.caption : p.caption;
    const at = live && post.state === "scheduled" ? live.scheduled_at : plan.suggested;
    const reel = p.kind === "reel";
    const cover = live ? live.cover_ms : null;
    const collab = (live?.collaborators || []).map((n) => `@${n}`).join(", ");
    const notConnected = !plan.connected;
    const today = dayOf(new Date().toISOString());
    const coverVideo = reel && p.video ? opts.media(p.video) : "";
    const html = `<h2 id="sp-title">${title}</h2>
      ${draftLine(p)}
      ${plan.why_not ? `<p class="sp-note bad">${esc(plan.why_not)}</p>` : ""}
      ${post?.state === "failed" ? `<p class="sp-note bad">${esc(post.error || "It didn't go out.")}</p>` : ""}
      ${live?.state === "scheduled" && live.error ? `<p class="sp-note">${esc(live.error)}</p>` : ""}
      ${notConnected ? `<p class="sp-note">Instagram isn't connected yet. You can schedule now; it goes out once it's connected. <button type="button" class="btn-link" data-sp-connect>Connect Instagram</button></p>` : ""}
      ${plan.posting_on === false ? `<p class="sp-note bad">Posting is switched off on the Studio machine (STUDIO_POSTING).</p>` : ""}
      <form class="form sp-form" id="sp-form">
        <fieldset class="sp-when"><legend class="um-label">When (Singapore time)</legend>
          <div class="sp-when-row">
            <label><span class="sr-only">Day</span><input type="date" id="sp-day" min="${esc(today)}" value="${esc(dayOf(at))}" required></label>
            <label><span class="sr-only">Time</span><input type="time" id="sp-time" step="300" value="${esc(timeOf(at))}" required></label>
            <button type="button" class="btn-link" data-sp-suggest title="The next free slot that keeps about ${esc(plan.per_week)} posts a week">Next free slot</button>
          </div>
          <p class="small sp-when-note" id="sp-when-note" aria-live="polite"></p>
        </fieldset>
        <label class="sp-field"><span class="um-label">Caption</span>
          <textarea id="sp-caption" rows="6" maxlength="5000">${esc(caption)}</textarea></label>
        <p class="small sp-count" id="sp-count" aria-live="polite"></p>
        ${reel ? `<fieldset class="sp-cover"><legend class="um-label">Cover</legend>
          <div class="seg" role="group" aria-label="Cover">
            <button type="button" data-sp-cover="first" class="${cover ? "" : "on"}" aria-pressed="${cover ? "false" : "true"}">First frame</button>
            <button type="button" data-sp-cover="pick" class="${cover ? "on" : ""}" aria-pressed="${cover ? "true" : "false"}">A moment</button></div>
          <div class="sp-cover-pick" id="sp-cover-pick" ${cover ? "" : "hidden"}>
            ${coverVideo ? `<video id="sp-cover-video" class="sp-cover-video" src="${esc(coverVideo)}#t=${(cover || 0) / 1000}" muted playsinline preload="metadata"></video>` : ""}
            <label class="sp-range"><span class="sr-only">Cover moment</span><input type="range" id="sp-cover-at" min="0" max="${esc(Math.max(1, Math.floor((p.duration || 1) * 10)))}" step="1" value="${esc(Math.round((cover || 0) / 100))}"></label>
            <span class="small mono" id="sp-cover-text">${esc(mmss((cover || 0) / 1000))}</span>
          </div></fieldset>
          <label class="sp-check"><input type="checkbox" id="sp-feed" ${live && live.share_to_feed === false ? "" : "checked"}> Also show it in the grid (feed)</label>` : ""}
        <label class="sp-field"><span class="um-label">Collaborators (optional)</span>
          <input type="text" id="sp-collab" value="${esc(collab)}" placeholder="@username, up to ${esc(plan.collaborators_max || 3)}" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
        <p class="error-text" id="sp-error" role="alert" hidden></p>
        <div class="sp-actions">
          ${live ? `<button type="button" class="btn ghost danger" data-sp-cancel>Cancel post</button>` : ""}
          <span class="sp-gap"></span>
          <button type="button" class="btn" data-sp-now ${offWhy(plan.why_not)}>Post now</button>
          <button type="submit" class="btn act" data-sp-save ${offWhy(plan.why_not)}>${live ? (post.state === "failed" ? "Try again at this time" : "Save") : "Schedule"}</button>
        </div>
      </form>`;
    open(html, scheduleClick, scheduleInput);
    updateCount();
    updateWhen();
  }

  function updateCount() {
    const c = captionCheck($("#sp-caption")?.value);
    const el = $("#sp-count");
    if (!el) return;
    el.innerHTML = c.problems.length ? `<span class="error-text">${esc(c.problems.join(" "))}</span>`
      : `${esc(c.chars.toLocaleString("en"))} / ${LIMITS.chars.toLocaleString("en")} characters · ${esc(plural(c.hashtags, "hashtag"))} (up to ${LIMITS.hashtags})`;
    // Can't post: the buttons say why when pressed (shell.js), not a silent grey.
    const why = plan.why_not || (c.problems.length ? `Fix the caption first: ${c.problems.join(" ")}` : "");
    for (const b of modal.querySelectorAll("[data-sp-save], [data-sp-now]")) {
      if (why) { b.setAttribute("aria-disabled", "true"); b.dataset.why = why; } else { b.removeAttribute("aria-disabled"); delete b.dataset.why; }
    }
  }
  function updateWhen() {
    const day = $("#sp-day")?.value, time = $("#sp-time")?.value, el = $("#sp-when-note");
    if (!el) return;
    const iso = isoFrom(day, time);
    if (!iso) { el.textContent = "Pick a day and a time."; return; }
    if (Date.parse(iso) < Date.now()) { el.innerHTML = `<span class="error-text">That time has passed.</span>`; return; }
    const suggested = plan.suggested && dayOf(plan.suggested) === day && timeOf(plan.suggested) === time;
    el.innerHTML = [suggested ? `The next free slot: about ${esc(plan.per_week)} posts a week, spread out.` : esc(whenText(iso)), takenLine(day)].filter(Boolean).join(" ");
  }
  function scheduleInput(e) {
    if (e.target.id === "sp-caption") updateCount();
    if (e.target.id === "sp-day" || e.target.id === "sp-time") updateWhen();
    if (e.target.id === "sp-cover-at") {
      const s = Number(e.target.value) / 10;
      $("#sp-cover-text").textContent = mmss(s);
      const v = $("#sp-cover-video");
      if (v) try { v.currentTime = s; } catch { /* not loaded */ }
    }
  }
  function fields() {
    const reel = plan.production.kind === "reel";
    const pick = modal.querySelector("[data-sp-cover=pick]")?.classList.contains("on");
    return {
      caption: $("#sp-caption").value,
      ...(reel ? { cover_ms: pick ? Math.round(Number($("#sp-cover-at").value) * 100) : 0, share_to_feed: $("#sp-feed").checked } : {}),
      collaborators: $("#sp-collab").value,
    };
  }
  async function send(when, btn, label) {
    showError("");
    busy(btn, true, label);
    const post = plan.post && ["scheduled", "failed"].includes(plan.post.state) ? plan.post : null;
    try {
      const body = { ...fields(), when };
      const r = post ? await opts.api("POST", `/api/instagram/posts/${post.id}`, body)
        : await opts.api("POST", "/api/instagram/posts", { ...body, production_id: plan.production.id });
      close();
      window.StudioUsage?.mark("post_scheduled", { now: when === "now" });
      opts.toast(when === "now" ? "Posting now. A Telegram says when it's out (a minute or two)." : `Scheduled for ${whenText(r.post.scheduled_at)}.`);
      opts.onChange(r.post);
    } catch (err) { busy(btn, false); showError(err.message); }
  }
  async function scheduleClick(e) {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.matches("[data-sp-connect]")) { await openConnect(); return; }
    if (t.matches("[data-sp-suggest]")) {
      $("#sp-day").value = dayOf(plan.suggested);
      $("#sp-time").value = timeOf(plan.suggested);
      updateWhen();
      return;
    }
    if (t.matches("[data-sp-cover]")) {
      const pick = t.dataset.spCover === "pick";
      for (const b of modal.querySelectorAll("[data-sp-cover]")) { b.classList.toggle("on", b === t); b.setAttribute("aria-pressed", String(b === t)); }
      $("#sp-cover-pick").hidden = !pick;
      return;
    }
    if (t.matches("[data-sp-save]")) {
      e.preventDefault();
      const iso = isoFrom($("#sp-day").value, $("#sp-time").value);
      if (!iso) return showError("Pick a day and a time.");
      if (Date.parse(iso) < Date.now()) return showError("That time has passed. Pick a later one, or Post now.");
      await send(iso, t, "Saving…");
      return;
    }
    if (t.matches("[data-sp-now]")) {
      if (t.dataset.sure !== "1") {
        t.dataset.sure = "1";
        t.textContent = "Post it now";
        t.classList.add("act");
        modal.querySelector("[data-sp-save]")?.classList.remove("act");
        showError("");
        const note = $("#sp-when-note");
        if (note) note.innerHTML = `<strong>It goes out on Instagram within a minute or two.</strong> Press Post it now to go ahead.`;
        return;
      }
      await send("now", t, "Posting…");
      return;
    }
    if (t.matches("[data-sp-cancel]")) {
      if (t.dataset.sure !== "1") { t.dataset.sure = "1"; t.textContent = "Yes, cancel it"; return; }
      busy(t, true, "Cancelling…");
      try {
        const r = await opts.api("POST", `/api/instagram/posts/${plan.post.id}/cancel`);
        close();
        opts.toast("Cancelled. It won't go out.");
        opts.onChange(r.post);
      } catch (err) { busy(t, false); showError(err.message); }
    }
  }

  // ── The Instagram connection ───────────────────────────────────────────────
  function connectHTML(st, form) {
    const owner = opts.isOwner();
    if (st && st.connected && !form) {
      const added = st.added_at ? new Date(st.added_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
      const lim = st.limit ? `<p class="small muted">Posts through Studio in the last ${esc(st.limit.hours)} hours: ${esc(st.limit.used)} of ${esc(st.limit.total)}.</p>` : "";
      const problem = st.expired ? `<p class="sp-note bad">The connection has run out. Paste a new token to post again.</p>`
        : st.problem ? `<p class="sp-note bad">${esc(st.problem)}</p>` : "";
      return `<div class="sp-connected">
        <p class="sp-status"><span class="sp-dot ${st.expired || st.problem ? "bad" : "ok"}" aria-hidden="true"></span>
          <span>Connected as <strong>@${esc(st.username)}</strong>${owner ? ` · token renews itself${added ? ` · added ${esc(added)}` : ""}` : ""}</span></p>
        ${owner ? `${problem}${lim}${st.limit_error ? `<p class="small muted">Couldn't read the posting limit: ${esc(st.limit_error)}</p>` : ""}
        <div class="row sp-connect-btns"><button type="button" class="btn" data-ig="replace">Replace</button>
          <button type="button" class="btn ghost danger" data-ig="disconnect">Disconnect</button></div>` : ""}
      </div>`;
    }
    if (!owner) return `<p class="muted">Instagram isn't connected yet. Bryan connects it.</p>`;
    return `<form class="form sp-connect-form" autocomplete="off">
      ${st?.connected ? "" : `<p>Studio posts only what you approve and schedule. Paste these from Meta's App Dashboard (your app › Instagram › API setup with Instagram login).</p>`}
      <label class="sp-field"><span class="um-label">Access token</span><input type="password" id="ig-token" autocomplete="off" spellcheck="false" placeholder="IGAA…" required></label>
      <label class="sp-field"><span class="um-label">Instagram app ID</span><input type="text" id="ig-app" inputmode="numeric" autocomplete="off" required></label>
      <label class="sp-field"><span class="um-label">Instagram app secret</span><input type="password" id="ig-secret" autocomplete="off" spellcheck="false" required></label>
      <p class="small muted">Kept only on the Studio machine. Never shown again.</p>
      <p class="error-text" id="ig-error" role="alert" hidden></p>
      <div class="row end">${st?.connected ? `<button type="button" class="btn" data-ig="keep">Keep the current one</button>` : ""}
        <button type="submit" class="btn act" data-ig="connect">Connect</button></div>
    </form>`;
  }

  function mountConnect(el) {
    let st = null;
    const draw = (form = false, html = null) => { el.innerHTML = html ?? connectHTML(st, form); };
    const err = (text) => { const e = el.querySelector("#ig-error"); if (e) { e.textContent = text; e.hidden = !text; } else opts.toast(text); };
    const load = async () => {
      el.innerHTML = `<p class="muted"><span class="spin"></span> Checking Instagram…</p>`;
      try { st = await opts.api("GET", "/api/instagram"); draw(); } catch (e) { draw(false, `<p class="error-text">Couldn't ask Studio: ${esc(e.message)}</p>`); }
    };
    if (!el.dataset.spBound) {
      el.dataset.spBound = "1";
      el.addEventListener("submit", (e) => e.preventDefault());
      el.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-ig]");
        if (!b) return;
        const what = b.dataset.ig;
        if (what === "replace") { draw(true); el.querySelector("#ig-token")?.focus(); return; }
        if (what === "keep") { draw(); return; }
        if (what === "disconnect") {
          if (b.dataset.sure !== "1") {
            b.dataset.sure = "1";
            b.textContent = "Yes, disconnect";
            b.insertAdjacentHTML("afterend", `<span class="small muted sp-sure">Scheduled posts won't go out until you connect again.</span>`);
            return;
          }
          busy(b, true, "Disconnecting…");
          try { st = await opts.api("POST", "/api/instagram/disconnect"); draw(); opts.toast("Instagram disconnected."); opts.onChange(); } catch (x) { busy(b, false); opts.toast(x.message); }
          return;
        }
        if (what === "connect") {
          e.preventDefault();
          const token = el.querySelector("#ig-token").value.trim(), app = el.querySelector("#ig-app").value.trim(), secret = el.querySelector("#ig-secret").value.trim();
          if (!token || !app || !secret) return err("Paste all three: the token, the app ID and the app secret.");
          busy(b, true, "Checking with Instagram…");
          try {
            st = await opts.api("POST", "/api/instagram/connect", { access_token: token, app_id: app, app_secret: secret });
            draw();
            opts.toast(`Connected as @${st.username}.`);
            opts.onChange();
          } catch (x) { busy(b, false); err(x.message); }
        }
      });
    }
    load();
    return { reload: load };
  }

  async function openConnect() {
    open(`<h2 id="sp-title">Instagram</h2><div class="sp-connect" id="sp-connect"></div>`);
    mountConnect($("#sp-connect"));
  }

  return { init: (o) => Object.assign(opts, o), openSchedule, openConnect, mountConnect, captionCheck, whenText, shortTime, dayOf, timeOf, close };
})();
