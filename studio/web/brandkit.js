// The Brand kit page (Review > More > Brand kit, #/brand-kit; brand_kit.py, brand_kit_routes.py): the fonts,
// colours and logo every Reel, carousel and slideshow Reel is drawn in, and what a kind of post does differently.
// The preview beside it is the real page each render uses, in the kit being edited. Nothing changes until Save.
// Fetched by app.js the first time the page opens (<template id="brandkit-lazy">); uses app.js's helpers
// (apiCall, toast, esc, openModal, closeModal, mark, phone).
"use strict";
window.StudioBrandKit = (() => {
  const root = () => document.getElementById("brandkit");
  const bk = { view: null, draft: null, saved: "", kind: "", moment: "title", slide: 1, previewTimer: 0, previewSeq: 0,
    preview: null, busy: false, error: "" };
  const KIND_HINT = "Only what you change here differs from the main kit. The rest follows it.";
  const q = (sel) => root().querySelector(sel);

  // ── The draft: what the page shows, saved as it is ───────────────────────
  function fromView(v) {
    const own = v.presets[v.preset];
    const kinds = {};
    for (const k of Object.keys(v.kinds)) kinds[k] = { colours: { ...v.kinds[k].colours }, fonts: { ...v.kinds[k].fonts } };
    return { preset: v.preset, colours: { ...own.colours, ...v.colours }, fonts: { ...own.fonts, ...v.fonts }, kinds,
      logo: v.logo || null };
  }
  const snapshot = (d) => JSON.stringify(d);
  const dirty = () => bk.draft && snapshot(bk.draft) !== bk.saved;
  const editable = () => !!bk.view?.editable;
  function valueOf(kind, what, role) {
    const over = kind && bk.draft.kinds[kind]?.[what]?.[role];
    return over || bk.draft[what][role];
  }
  function setValue(what, role, value) {
    if (bk.kind) {
      const box = (bk.draft.kinds[bk.kind] = bk.draft.kinds[bk.kind] || { colours: {}, fonts: {} });
      box[what] = box[what] || {};
      if (value === bk.draft[what][role]) delete box[what][role]; else box[what][role] = value;
    } else bk.draft[what][role] = value;
  }
  const overridden = (what, role) => !!(bk.kind && bk.draft.kinds[bk.kind]?.[what]?.[role]);

  // ── Drawing ───────────────────────────────────────────────────────────────
  const why = () => (editable() ? "" : ` aria-disabled="true" data-why="Only Bryan can change the brand kit."`);
  const off = () => (editable() ? "" : " disabled");
  const MOMENTS = [["title", "Title"], ["callout", "Callout"], ["card", "Card"], ["end", "End"]];
  const SLIDES = [[1, "Cover"], [2, "Point"], [3, "Last"]];

  function render() {
    const focused = root().contains(document.activeElement) ? document.activeElement.id : "";
    draw();
    if (focused) document.getElementById(focused)?.focus({ preventScroll: true });
  }

  function draw() {
    const v = bk.view;
    const kindName = (k) => v.kind_names.find((x) => x.key === k)?.name || k;
    const fontName = (key) => v.fonts_list.find((f) => f.key === key)?.name || key;
    const saved = v.updated ? `Saved ${new Date(v.updated).toLocaleDateString(undefined, { day: "numeric", month: "short" })}${v.by ? ` by ${esc(v.by)}` : ""}.` : "Not changed yet: today's look.";
    root().innerHTML = `
      <div class="bk-head">
        <button type="button" class="btn" data-bk-back>‹ Review</button>
        <div class="bk-title"><h1 tabindex="-1" id="bk-h">Brand kit</h1>
          <p class="muted small">Fonts, colours and logo for every Reel and carousel. Drafts already made keep their look. ${saved}</p></div>
      </div>
      <div class="bk-body">
        <section class="bk-preview" aria-label="Preview">
          <div class="bk-frames">
            <figure class="bk-fig"><div class="bk-frame reel" id="bk-reel"></div>
              <div class="seg bk-seg bk-moments" role="group" aria-label="Reel moment">${MOMENTS.map(([k, l]) =>
                `<button type="button" data-bk-moment="${k}" class="${bk.moment === k ? "on" : ""}" aria-pressed="${bk.moment === k}">${l}</button>`).join("")}</div>
              <button type="button" class="btn bk-cycle" id="bk-cycle" aria-label="Reel moment: ${esc(momentName())}. Tap for the next.">${esc(momentName())} ›</button>
              <figcaption class="muted small">A Reel</figcaption></figure>
            <figure class="bk-fig"><div class="bk-frame slide" id="bk-slide"></div>
              <div class="seg bk-seg" role="group" aria-label="Slide">${SLIDES.map(([n, l]) =>
                `<button type="button" data-bk-slide="${n}" class="${bk.slide === n ? "on" : ""}" aria-pressed="${bk.slide === n}">${l}</button>`).join("")}</div>
              <figcaption class="muted small">A carousel slide</figcaption></figure>
          </div>
        </section>
        <section class="bk-controls">
          ${editable() ? "" : `<p class="bk-team">You can look. Only Bryan can change the brand kit.</p>`}
          <div class="bk-for">
            <label class="um-label" for="bk-kind">Kit for</label>
            <select id="bk-kind">
              <option value="">Every post (main kit)</option>
              ${v.kind_names.map((k) => `<option value="${esc(k.key)}" ${bk.kind === k.key ? "selected" : ""}>${esc(k.name)}${countOver(k.key) ? ` · ${countOver(k.key)} changed` : ""}</option>`).join("")}
            </select>
            <p class="muted small" id="bk-kind-hint">${bk.kind ? `${esc(kindName(bk.kind))}: ${KIND_HINT}` : "What every post uses. Pick a kind of post to change only its own."}</p>
          </div>
          <div class="bk-warn" id="bk-warn" role="status" aria-live="polite"></div>
          ${bk.kind ? "" : `<div class="bk-group">
            <h2>Start from</h2>
            <div class="seg bk-seg" role="group" aria-label="Look">${Object.entries(v.presets).map(([k, p]) =>
              `<button type="button" data-bk-preset="${k}" class="${bk.draft.preset === k ? "on" : ""}" aria-pressed="${bk.draft.preset === k}"${why()}>${esc(p.name)}</button>`).join("")}</div>
            <p class="muted small">${bk.draft.preset === "site" ? "Site Notes: one bold font, words on dark labels. Bryan's pick." : bk.draft.preset === "gallery" ? "Gallery: a serif title, captions that glow." : "Classic: boxed titles and captions."}</p>
          </div>`}
          <div class="bk-group"><h2>Colours</h2>
            ${groupHTML("reel", "Reels")}${groupHTML("slide", "Carousels")}</div>
          <div class="bk-group"><h2>Fonts</h2>
            ${v.font_roles.map((r) => fontRowHTML(r, fontName)).join("")}
            ${bk.kind ? "" : uploadsHTML()}</div>
          ${bk.kind ? "" : logoHTML()}
        </section>
      </div>
      <div class="bk-savebar" role="region" aria-label="Save the brand kit">
        <span class="muted small" id="bk-state">${dirty() ? "Not saved yet." : "No changes."}</span>
        <button type="button" class="btn" id="bk-undo" ${dirty() ? "" : "hidden"}>Undo changes</button>
        <button type="button" class="btn act" id="bk-save"${editable() ? (dirty() ? "" : ` aria-disabled="true" data-why="Change a colour, font or logo first."`) : why()}>Save</button>
      </div>`;
    drawPreview();
    paintWarnings();
  }

  const momentName = () => (MOMENTS.find(([k]) => k === bk.moment) || MOMENTS[0])[1];
  function setMoment(m) {
    bk.moment = m;
    for (const b of root().querySelectorAll("[data-bk-moment]")) {
      b.classList.toggle("on", b.dataset.bkMoment === m);
      b.setAttribute("aria-pressed", String(b.dataset.bkMoment === m));
    }
    const c = q("#bk-cycle");
    if (c) { c.textContent = `${momentName()} ›`; c.setAttribute("aria-label", `Reel moment: ${momentName()}. Tap for the next.`); }
    const f = q("#bk-reel iframe:not(.next)");
    if (f) showIn(f, "reel");
  }

  function countOver(kind) {
    const k = bk.draft.kinds[kind];
    return k ? Object.keys(k.colours || {}).length + Object.keys(k.fonts || {}).length : 0;
  }

  function groupHTML(group, title) {
    const rows = bk.view.roles.filter((r) => r.group === group);
    return `<h3 class="um-label">${title}</h3><ul class="bk-rows">${rows.map((r) => {
      const v = valueOf(bk.kind, "colours", r.key);
      const what = r.notes?.[bk.draft.preset] || r.for;
      const over = overridden("colours", r.key);
      return `<li class="bk-row${over ? " over" : ""}" data-role="${esc(r.key)}">
        <label class="bk-sw" for="bk-c-${esc(r.key)}"><input type="color" id="bk-c-${esc(r.key)}" data-bk-colour="${esc(r.key)}" value="${esc(v.toLowerCase())}"${off()} aria-label="${esc(r.name)} colour"></label>
        <div class="bk-what"><label for="bk-h-${esc(r.key)}"><strong>${esc(r.name)}</strong></label><span class="muted small">${esc(what)}</span>
          <span class="bk-same">${sameHTML(r.key)}</span></div>
        <input type="text" class="bk-hex" id="bk-h-${esc(r.key)}" data-bk-hex="${esc(r.key)}" value="${esc(v)}" maxlength="7" spellcheck="false" autocomplete="off" inputmode="text"${off()}>
      </li>`;
    }).join("")}</ul>`;
  }

  // A kind's colour row: whether it follows the main kit, or the way back to it.
  function sameHTML(role) {
    if (!bk.kind) return "";
    return overridden("colours", role)
      ? `<button type="button" class="btn-link" data-bk-reset="colours:${esc(role)}"${why()}>Use the main kit's</button>`
      : `<span class="muted small">Same as the main kit</span>`;
  }
  // After typing or picking a colour: the row says what it is now, without drawing the page again (a redraw under
  // a press would swallow the press).
  function syncRow(role) {
    const row = q(`.bk-row[data-role="${role}"]`);
    if (!row) return;
    row.classList.toggle("over", overridden("colours", role));
    const same = row.querySelector(".bk-same");
    if (same) same.innerHTML = sameHTML(role);
    const opt = q(`#bk-kind option[value="${bk.kind}"]`);
    if (opt && bk.kind) opt.textContent = `${bk.view.kind_names.find((k) => k.key === bk.kind)?.name || bk.kind}${countOver(bk.kind) ? ` · ${countOver(bk.kind)} changed` : ""}`;
  }

  function fontRowHTML(r, fontName) {
    const v = valueOf(bk.kind, "fonts", r.key);
    const over = overridden("fonts", r.key);
    const classicTitles = bk.draft.preset === "classic" && r.key === "titles";
    const opts = (src) => bk.view.fonts_list.filter((f) => f.source === src).map((f) =>
      `<option value="${esc(f.key)}" ${f.key === v ? "selected" : ""}>${esc(f.name)}${f.bold ? "" : " (no bold)"}</option>`).join("");
    const mine = opts("upload");
    return `${r.key === "titles" ? `<h3 class="um-label">Reels</h3>` : r.key === "slide_headings" ? `<h3 class="um-label">Carousels</h3>` : ""}
      <div class="bk-font${over ? " over" : ""}" data-role="${esc(r.key)}">
        <label for="bk-f-${esc(r.key)}"><strong>${esc(r.name)}</strong> <span class="muted small">${esc(classicTitles ? "Classic sets titles in the captions font" : r.for)}</span></label>
        <select id="bk-f-${esc(r.key)}" data-bk-font="${esc(r.key)}"${off()}${classicTitles ? " disabled" : ""}>
          <optgroup label="Studio's fonts">${opts("bundled")}</optgroup>${mine ? `<optgroup label="Your fonts">${mine}</optgroup>` : ""}
        </select>
        ${bk.kind && over ? `<button type="button" class="btn-link" data-bk-reset="fonts:${esc(r.key)}"${why()}>Use the main kit's (${esc(fontName(bk.draft.fonts[r.key]))})</button>` : ""}
      </div>`;
  }

  function uploadsHTML() {
    const mine = bk.view.fonts_list.filter((f) => f.source === "upload");
    return `<h3 class="um-label">Your fonts</h3>
      ${mine.length ? `<ul class="bk-mine">${mine.map((f) => f.faces.map((x) => `<li><span><strong>${esc(f.name)}</strong> <span class="muted small">${esc(x.style_name || "")} · ${esc(String(x.weight))} · ${Math.max(1, Math.round((x.bytes || 0) / 1024))} KB</span></span>
        <button type="button" class="btn small ghost danger" data-bk-unfont="${esc(x.id)}" data-name="${esc(`${f.name} ${x.style_name || ""}`.trim())}"${why()}>Remove</button></li>`).join("")).join("")}</ul>` : ""}
      <label class="bk-drop" id="bk-font-drop" for="bk-font-file"${why()}>
        <span><strong>Add a font</strong>: drop a .ttf, .otf or .woff2 here, or tap to choose.</span>
        <span class="muted small">Add the bold file too: titles need one. Up to ${bk.view.limits.font_mb} MB.</span>
        <input type="file" id="bk-font-file" class="sr-only" accept=".ttf,.otf,.woff2,font/ttf,font/otf,font/woff2"${off()}>
      </label>
      <p class="error-text small" id="bk-font-err" role="alert" hidden></p>`;
  }

  function logoHTML() {
    const logo = bk.draft.logo;
    return `<div class="bk-group"><h2>Logo</h2>
      <div class="bk-logo">
        <div class="bk-logo-show">${logo ? `<img src="/media/brand-kit/logo/${esc(logo)}" alt="Your logo">` : `<img src="/media/brand-kit/mark.svg" alt="The Urban Makers mark">`}</div>
        <div class="bk-logo-act">
          <label class="bk-drop" id="bk-logo-drop" for="bk-logo-file"${why()}>
            <span><strong>${logo ? "Change the logo" : "Use your own logo"}</strong>: drop a PNG here, or tap to choose.</span>
            <span class="muted small">On the end card and the slides. A see-through background works best.</span>
            <input type="file" id="bk-logo-file" class="sr-only" accept="image/png"${off()}>
          </label>
          ${logo ? `<button type="button" class="btn small ghost" id="bk-logo-off"${why()}>Use the Urban Makers mark</button>` : ""}
        </div>
      </div>
      <p class="error-text small" id="bk-logo-err" role="alert" hidden></p></div>`;
  }

  function paintWarnings() {
    const box = q("#bk-warn");
    if (!box) return;
    const list = bk.preview?.warnings || [];
    box.innerHTML = list.length ? `<strong>Check these</strong><ul>${list.map((w) => `<li>${esc(w.text)}</li>`).join("")}</ul>` : "";
    box.hidden = !list.length;
    for (const el of root().querySelectorAll(".bk-row, .bk-font")) {
      el.classList.toggle("warn", list.some((w) => w.role === el.dataset.role));
    }
  }

  function paintSaveBar() {
    const save = q("#bk-save");
    if (!save) return;
    q("#bk-state").textContent = bk.busy ? "Saving…" : dirty() ? "Not saved yet." : "No changes.";
    q("#bk-undo").hidden = !dirty();
    if (editable()) {
      if (dirty() && !bk.busy) { save.removeAttribute("aria-disabled"); delete save.dataset.why; }
      else { save.setAttribute("aria-disabled", "true"); save.dataset.why = bk.busy ? "Saving now." : "Change a colour, font or logo first."; }
    }
  }

  // ── The preview: the real pages, in frames ────────────────────────────────
  const SIZE = { reel: [1080, 1920], slide: [1080, 1350] };
  function fit(frame) {
    const [w] = SIZE[frame.classList.contains("reel") ? "reel" : "slide"];
    frame.style.setProperty("--s", String(frame.clientWidth / w));
  }
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver((es) => es.forEach((e) => fit(e.target))) : null;

  function drawPreview() {
    for (const [id, part] of [["#bk-reel", "reel"], ["#bk-slide", "slide"]]) {
      const frame = q(id);
      if (!frame) continue;
      ro?.observe(frame);
      fit(frame);
      if (bk.preview?.[part]) swapIn(frame, bk.preview[part], part, true);
      else frame.innerHTML = `<span class="bk-wait muted small">Loading the preview…</span>`;
    }
  }
  function at(part) {
    return part === "reel" ? bk.preview.moments[bk.moment] : bk.slide;
  }
  // The new page loads under the old one and takes its place once its fonts are in: no flash between them.
  function swapIn(frame, url, part, now) {
    const old = frame.querySelector("iframe:not(.next)");
    if (old && old.dataset.url === url) { showIn(old, part); return; }
    const f = document.createElement("iframe");
    f.className = old && !now ? "next" : "";
    f.title = part === "reel" ? "Reel preview" : "Slide preview";
    f.tabIndex = -1;
    f.setAttribute("aria-hidden", "true");
    f.dataset.url = url;
    f.src = `${url}#${at(part)}`;
    f.addEventListener("load", () => {
      const ready = () => {
        try {
          if (f.contentDocument?.documentElement?.dataset.ready !== "1") return false;
        } catch { /* not ours */ }
        showIn(f, part);
        f.classList.remove("next");
        for (const x of frame.querySelectorAll("iframe")) if (x !== f) x.remove();
        frame.querySelector(".bk-wait")?.remove();
        return true;
      };
      if (!ready()) { let n = 0; const t = setInterval(() => { if (ready() || ++n > 60) { clearInterval(t); ready(); } }, 50); }
    });
    frame.appendChild(f);
  }
  function showIn(f, part) {
    try { f.contentWindow?.show?.(at(part)); } catch { /* still loading */ }
  }
  function schedulePreview(ms = 220) {
    clearTimeout(bk.previewTimer);
    bk.previewTimer = setTimeout(loadPreview, ms);
  }
  async function loadPreview() {
    const seq = ++bk.previewSeq;
    try {
      const p = await apiCall("POST", "/api/brand-kit/preview", { kit: bk.draft, kind: bk.kind || null });
      if (seq !== bk.previewSeq) return;
      bk.preview = p;
      const reel = q("#bk-reel"), slide = q("#bk-slide");
      if (reel) swapIn(reel, p.reel, "reel");
      if (slide) swapIn(slide, p.slide, "slide");
      paintWarnings();
    } catch (err) {
      if (seq === bk.previewSeq) toast(`The preview couldn't be made: ${err.message}`);
    }
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  function changed(rerender = false) {
    if (rerender) render(); else paintSaveBar();
    schedulePreview();
  }

  async function save() {
    if (!dirty() || bk.busy || !editable()) return;
    bk.busy = true;
    paintSaveBar();
    try {
      const v = await apiCall("POST", "/api/brand-kit", bk.draft);
      bk.view = { ...v, editable: true };
      bk.draft = fromView(bk.view);
      bk.saved = snapshot(bk.draft);
      const kinds = Object.keys(bk.view.kinds).filter((k) => countOver(k)).length;
      mark("brand_kit_saved", { kinds, logo: !!bk.draft.logo });
      toast("Saved. New drafts use it.");
      bk.busy = false;
      render();
    } catch (err) {
      bk.busy = false;
      paintSaveBar();
      toast(`Couldn't save: ${err.message}`);
    }
  }

  async function upload(kind, file) {
    const err = q(kind === "font" ? "#bk-font-err" : "#bk-logo-err");
    const show = (text) => { if (err) { err.textContent = text; err.hidden = !text; } };
    show("");
    if (!file) return;
    const okType = kind === "font" ? /\.(ttf|otf|woff2)$/i.test(file.name) : /\.png$/i.test(file.name) || file.type === "image/png";
    if (!okType) { show(kind === "font" ? "Only .ttf, .otf or .woff2 fonts." : "The logo must be a PNG."); return; }
    const limit = (kind === "font" ? bk.view.limits.font_mb : bk.view.limits.logo_mb) * 1024 * 1024;
    if (file.size > limit) { show(`That file is over ${limit / 1024 / 1024} MB.`); return; }
    const drop = q(kind === "font" ? "#bk-font-drop" : "#bk-logo-drop");
    drop?.classList.add("busy");
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`/api/brand-kit/${kind}`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      if (kind === "font") {
        bk.view.fonts_list = data.fonts_list;
        mark("brand_kit_font_added", { weight: String(data.font?.weight || "") });
        toast(`${data.font.family} ${data.font.style_name} added. Pick it under Fonts.`);
        render();
      } else {
        bk.draft.logo = data.logo;
        mark("brand_kit_logo");
        changed(true);
      }
    } catch (e) {
      show(e.message);
    } finally {
      drop?.classList.remove("busy");
    }
  }

  function onClick(e) {
    const t = e.target.closest("button, [data-bk-back]");
    if (!t) return;
    if (t.matches("[data-bk-back]")) { leave(); return; }
    if (t.dataset.bkMoment) { setMoment(t.dataset.bkMoment); return; }
    if (t.id === "bk-cycle") {
      const i = MOMENTS.findIndex(([k]) => k === bk.moment);
      setMoment(MOMENTS[(i + 1) % MOMENTS.length][0]);
      return;
    }
    if (t.dataset.bkSlide) {
      bk.slide = Number(t.dataset.bkSlide);
      for (const b of root().querySelectorAll("[data-bk-slide]")) { b.classList.toggle("on", b === t); b.setAttribute("aria-pressed", String(b === t)); }
      const f = q("#bk-slide iframe:not(.next)");
      if (f) showIn(f, "slide");
      return;
    }
    if (!editable()) return;
    if (t.dataset.bkPreset) {
      const p = t.dataset.bkPreset;
      if (p === bk.draft.preset) return;
      const name = bk.view.presets[p].name;
      openModal(`<h2 id="modal-title">Start from ${esc(name)}?</h2>
        <p>The colours and fonts go back to ${esc(name)}'s own. What you set for a kind of post stays.</p>
        <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button>
          <button type="button" class="btn primary" id="bk-preset-yes">Start from ${esc(name)}</button></div>`, (ev) => {
        if (ev.target.id !== "bk-preset-yes") return;
        const own = bk.view.presets[p];
        bk.draft.preset = p;
        bk.draft.colours = { ...own.colours };
        bk.draft.fonts = { ...own.fonts };
        closeModal();
        changed(true);
      });
      return;
    }
    if (t.dataset.bkReset) {
      const [what, role] = t.dataset.bkReset.split(":");
      delete bk.draft.kinds[bk.kind]?.[what]?.[role];
      changed(true);
      return;
    }
    if (t.dataset.bkUnfont) {
      const id = t.dataset.bkUnfont;
      openModal(`<h2 id="modal-title">Remove ${esc(t.dataset.name)}?</h2>
        <p>It goes off the list, and posts that used it go back to the look's own font. Drafts already made keep it.</p>
        <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button>
          <button type="button" class="btn danger" id="bk-unfont-yes">Remove it</button></div>`, async (ev) => {
        if (ev.target.id !== "bk-unfont-yes") return;
        const v = await apiCall("POST", `/api/brand-kit/font/${encodeURIComponent(id)}/remove`, {});
        closeModal();
        const keep = bk.draft;
        bk.view = v;
        // Fonts no longer there fall back in the draft too.
        const known = new Set(v.fonts_list.map((f) => f.key));
        for (const box of [keep, ...Object.values(keep.kinds)]) {
          for (const [role, key] of Object.entries(box.fonts || {})) if (!known.has(key)) {
            if (box === keep) box.fonts[role] = v.presets[keep.preset].fonts[role]; else delete box.fonts[role];
          }
        }
        bk.saved = snapshot(fromView(v));
        changed(true);
      });
      return;
    }
    if (t.id === "bk-logo-off") { bk.draft.logo = null; changed(true); return; }
    if (t.id === "bk-undo") { bk.draft = JSON.parse(bk.saved); changed(true); return; }
    if (t.id === "bk-save") save();
  }

  function onInput(e) {
    const t = e.target;
    if (t.dataset.bkColour) {
      const v = t.value.toUpperCase();
      setValue("colours", t.dataset.bkColour, v);
      const hex = q(`[data-bk-hex="${t.dataset.bkColour}"]`);
      if (hex) hex.value = v;
      changed();
    } else if (t.dataset.bkHex) {
      const m = /^#?([0-9a-f]{6})$/i.exec(t.value.trim());
      t.classList.toggle("err", !m && t.value.trim().length >= 6);
      if (!m) return;
      const v = `#${m[1].toUpperCase()}`;
      setValue("colours", t.dataset.bkHex, v);
      const sw = q(`[data-bk-colour="${t.dataset.bkHex}"]`);
      if (sw) sw.value = v.toLowerCase();
      changed();
    }
  }
  function onChange(e) {
    const t = e.target;
    if (t.id === "bk-kind") {
      bk.kind = t.value;
      if (bk.kind) mark("brand_kit_kind", { kind: bk.kind });
      changed(true);
      q("#bk-kind")?.focus();
      return;
    }
    if (t.dataset.bkFont) { setValue("fonts", t.dataset.bkFont, t.value); changed(true); return; }
    if (t.dataset.bkColour) { onInput(e); syncRow(t.dataset.bkColour); return; }
    if (t.dataset.bkHex) {
      if (!/^#?[0-9a-f]{6}$/i.test(t.value.trim())) { t.value = valueOf(bk.kind, "colours", t.dataset.bkHex); t.classList.remove("err"); }
      syncRow(t.dataset.bkHex);
      return;
    }
    if (t.id === "bk-font-file") { upload("font", t.files?.[0]); t.value = ""; return; }
    if (t.id === "bk-logo-file") { upload("logo", t.files?.[0]); t.value = ""; }
  }
  function onDrag(e) {
    const drop = e.target.closest?.(".bk-drop");
    if (!drop || !editable()) return;
    e.preventDefault();
    if (e.type === "dragover") drop.classList.add("over");
    else if (e.type === "dragleave") drop.classList.remove("over");
    else if (e.type === "drop") {
      drop.classList.remove("over");
      upload(drop.id === "bk-font-drop" ? "font" : "logo", e.dataTransfer?.files?.[0]);
    }
  }

  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    const r = root();
    r.addEventListener("click", (e) => { onClick(e); });
    r.addEventListener("input", onInput);
    r.addEventListener("change", onChange);
    for (const ev of ["dragover", "dragleave", "drop"]) r.addEventListener(ev, onDrag);
    // Typing a colour on a phone: the preview lets go of the top, so the box and the keyboard have the room.
    r.addEventListener("focusin", (e) => r.classList.toggle("typing", !!e.target.matches?.(".bk-hex")));
    r.addEventListener("focusout", () => r.classList.remove("typing"));
    window.addEventListener("beforeunload", (e) => { if (isOpen() && dirty()) { e.preventDefault(); e.returnValue = ""; } });
  }

  // ── Open and leave ────────────────────────────────────────────────────────
  const isOpen = () => !!root() && !root().hidden;
  async function open() {
    wire();
    const r = root();
    r.hidden = false;
    if (!bk.view) r.innerHTML = `<p class="muted bk-loading">Loading the brand kit…</p>`;
    try {
      const v = await apiCall("GET", "/api/brand-kit");
      const keep = bk.draft && dirty() ? bk.draft : null;
      bk.view = v;
      bk.draft = keep || fromView(v);
      bk.saved = snapshot(fromView(v));
      render();
      q("#bk-h")?.focus();
      schedulePreview(0);
    } catch (err) {
      r.innerHTML = `<div class="bk-head"><button type="button" class="btn" data-bk-back>‹ Review</button></div>
        <p class="error-text bk-loading" role="alert">Couldn't load the brand kit: ${esc(err.message)}</p>`;
    }
  }
  function close() {
    const r = root();
    if (!r) return;
    r.hidden = true;
  }
  function leave() {
    const go = () => { bk.draft = null; close(); location.hash = "#/tab/review"; };
    if (!dirty()) { go(); return; }
    openModal(`<h2 id="modal-title">Leave without saving?</h2><p>Your changes to the brand kit aren't saved.</p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Stay</button>
        <button type="button" class="btn danger" id="bk-leave-yes">Leave</button></div>`, (ev) => {
      if (ev.target.id === "bk-leave-yes") { closeModal(); go(); }
    });
  }
  return { open, close, isOpen, dirty, state: bk };
})();
