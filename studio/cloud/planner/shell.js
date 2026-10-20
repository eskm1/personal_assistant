// Studio's app shell: the top bar, the phone tab bar and its More sheet. The same in both
// halves of Studio, Plan (the planner, this folder) and Review (the dashboard, studio/web,
// which serves this file as /static/shell.js), so moving between them is changing tabs.
//
// <div id="shell" class="shell" data-half="plan|review"> is filled in when this script
// runs, before the page's own script. The page then fills the status pill (#laptop-*)
// and the account menu (#account-menu), and tells the shell where it is:
//   StudioShell.setCurrent("plan" | "ideas" | "today" | "review" | "refs" | "style" | "content" | "questions" | "library"
//                          | "calendar" | "results" | "instagram" | "")
//   StudioShell.setLinks({ today: "#/today/3", ... })   ("" hides that item)
//   StudioShell.setBadge(2)                              (drafts waiting for review)
//   StudioShell.setHost({ page, api, role, version, usage })  (help, feedback and usage capture: help.js, capture.js)
//
// Help is one press away on every screen: the "?" on the bar (and [data-help-open] anywhere else, such as a
// desktop viewer's head) loads help.js and help.css the first time it's pressed. A control that can't act says
// why: give it aria-disabled="true" (not disabled, which swallows the tap) and data-why="what to do instead".
"use strict";
window.StudioShell = (() => {
  const root = document.getElementById("shell");
  if (!root) return null;
  const $ = (sel) => root.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $$ = (sel) => [...root.querySelectorAll(sel)];
  const half = root.dataset.half === "review" ? "review" : "plan";

  // Where each section lives, from this half. The planner on its own address (Netlify)
  // reaches Review at the Studio address in its config, and shows no badge there.
  const underPlan = /^\/plan(\/|$)/.test(location.pathname);
  const studioUrl = (() => {
    const u = String(window.STUDIO_CONFIG?.studioUrl ?? "");
    return /^https?:\/\//i.test(u) ? u.replace(/\/+$/, "") : "";
  })();
  const dash = half === "review" || underPlan ? "/" : studioUrl ? `${studioUrl}/` : "";
  const planner = half === "plan" ? "" : "/plan/";
  const links = {
    plan: `${planner}#/`,
    ideas: `${planner}#/ideas`,
    today: `${planner}#/today`,
    review: dash && `${dash}#/tab/review`,
    refs: `${planner}#/references`,
    style: `${planner}#/style`,
    content: `${planner}#/content-plan`,
    questions: `${planner}#/questions`,
    calendar: `${planner}#/calendar`,
    results: `${planner}#/results`,
    instagram: `${planner}#/instagram`,
    library: dash && `${dash}#/tab/library`,
  };
  if (half === "review") links.plan = "/plan/";

  // Stroke icons for the phone tab bar and the More sheet.
  const ICONS = {
    plan: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v3H9zM9 11h6M9 15h4"/>',
    ideas: '<rect x="3.5" y="4" width="7" height="16" rx="1.5"/><rect x="13.5" y="4" width="7" height="10" rx="1.5"/>',
    today: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><circle cx="12" cy="15" r="1.6"/>',
    review: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M10 9l5 3-5 3z"/>',
    more: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
    refs: '<path d="M7 3.5h10a1 1 0 0 1 1 1V21l-6-4-6 4V4.5a1 1 0 0 1 1-1z"/>',
    style: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    content: '<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M8 16v-3M12 16V9M16 16v-5"/>',
    questions: '<path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5v-3.5h-1a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/><path d="M10.2 9.4a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.3M12 14.2v.1"/>',
    library: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M6 4h12M10 11l4 2.5-4 2.5z"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4M8 14h2M12 14h2M16 14h.5M8 17h2M12 17h2"/>',
    results: '<path d="M4 20h16M7 17v-5M12 17V7M17 17v-8"/>',
    instagram: '<rect x="4" y="4" width="16" height="16" rx="4.5"/><circle cx="12" cy="12" r="3.6"/><circle cx="16.6" cy="7.4" r=".9"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4M12 16.6v.2"/>',
    feedback: '<path d="M5 21V4.5M5 4.5h11l-2 4 2 4H5"/>',
    report: '<path d="M14.5 4.5a4 4 0 1 1-3 6.6L4 18.6V21h2.5v-2h2v-2h2l1.2-1.2"/><circle cx="16.5" cy="7.5" r="1"/>',
  };
  const icon = (name) => `<svg class="shell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
  const badge = `<span class="shell-badge" data-badge hidden></span>`;

  // The sections behind More: on a computer a menu under More, on a phone the More sheet.
  // The owner's own tools at the end of More: what people flagged, and the weekly report's key (help.js).
  const OWNER_TOOLS = [["feedback", "Feedback"], ["report", "Weekly report access"]];
  const MORE = [["calendar", "Posting calendar"], ["results", "Results"], ["refs", "References"], ["style", "Style guide"],
    ["content", "Content plan"], ["questions", "Client questions"], ["today", "Today on site"], ["library", "Library"],
    ["instagram", "Instagram"]];
  root.innerHTML = `<header class="shell-top" id="shell-top">
      <a class="shell-mark" data-sec="plan" href="#" aria-label="Studio: Plan"><span class="wordmark">URBAN MAKERS</span><span class="studio-tag">Studio</span></a>
      <nav class="shell-nav" aria-label="Studio">
        <a data-sec="plan" href="#">Plan</a>
        <a data-sec="ideas" href="#">Ideas</a>
        <a data-sec="review" href="#">Review${badge}</a>
        <div class="shell-more">
          <button type="button" class="shell-more-btn" id="shell-more-btn" aria-haspopup="true" aria-expanded="false" aria-controls="shell-more-menu">More</button>
          <div class="menu shell-more-menu" id="shell-more-menu" hidden>
            ${MORE.map(([sec, label]) => `<a data-sec="${sec}" href="#">${label}</a>`).join("")}
            ${OWNER_TOOLS.map(([tool, label]) => `<button type="button" class="shell-owner" data-shell-tool="${tool}" hidden>${label}</button>`).join("")}
          </div>
        </div>
      </nav>
      <div class="shell-right">
        <button type="button" class="shell-help" id="shell-help" data-help-open aria-haspopup="dialog" aria-label="Help: how this page works, Ask Studio, report a problem">${icon("help")}<span class="shell-help-word">Help</span></button>
        <div class="laptop" id="laptop">
          <button type="button" class="laptop-pill" id="laptop-btn" aria-haspopup="true" aria-expanded="false" aria-controls="laptop-info"><span class="dot"></span><span id="laptop-text">Studio…</span></button>
          <div class="menu laptop-info" id="laptop-info" hidden></div>
        </div>
        <div class="account" id="account">
          <button type="button" class="account-btn" id="account-btn" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu"></button>
          <div class="menu" id="account-menu" role="menu" hidden></div>
        </div>
      </div>
    </header>
    <nav class="shell-tabbar" id="shell-tabbar" aria-label="Studio sections">
      <a class="shell-tab" data-sec="plan" href="#">${icon("plan")}<span>Plan</span></a>
      <a class="shell-tab" data-sec="ideas" href="#">${icon("ideas")}<span>Ideas</span></a>
      <a class="shell-tab" data-sec="review" href="#">${icon("review")}<span>Review</span>${badge}</a>
      <button type="button" class="shell-tab" id="shell-more" aria-haspopup="dialog" aria-expanded="false" aria-controls="shell-sheet">${icon("more")}<span>More</span></button>
    </nav>
    <div class="shell-sheet" id="shell-sheet" hidden>
      <div class="shell-sheet-card" role="dialog" aria-modal="true" aria-labelledby="shell-sheet-title">
        <div class="shell-sheet-head"><h2 id="shell-sheet-title">More</h2>
          <button type="button" class="shell-sheet-close" data-sheet-close aria-label="Close">&times;</button></div>
        <nav class="shell-sheet-links" aria-label="More sections">
          ${MORE.map(([sec, label]) => `<a data-sec="${sec}" href="#">${icon(sec)}<span>${label}</span></a>`).join("")}
          ${OWNER_TOOLS.map(([tool, label]) => `<button type="button" class="shell-owner" data-shell-tool="${tool}" hidden>${icon(tool)}<span>${label}</span></button>`).join("")}
        </nav>
        <div class="shell-sheet-account" id="shell-sheet-account"></div>
      </div>
    </div>`;
  // Who was signed in here last time, drawn at once (the page fills it in again when it knows): the bar
  // used to grow by a name's width after load, and move the page with it.
  try {
    const name = localStorage.getItem("studio.meName");
    if (name) $("#account-btn").innerHTML = `<span class="avatar" aria-hidden="true">${esc(name.slice(0, 1).toUpperCase())}</span><span class="account-name">${esc(name)}</span>`;
  } catch { /* private mode */ }

  let current = "";
  function paintLinks() {
    for (const a of $$("[data-sec]")) {
      if (a.tagName !== "A") continue;
      const href = links[a.dataset.sec];
      a.hidden = !href && !a.classList.contains("shell-mark");
      a.setAttribute("href", href || links.plan);
    }
  }
  function setLinks(next) {
    Object.assign(links, next || {});
    paintLinks();
  }
  // The highlighted section: Plan, Ideas, Review, or More for the sections behind it (on the top
  // bar Library counts as Review, one of its stages).
  const IN_MORE = MORE.map(([sec]) => sec);
  function setCurrent(sec) {
    current = sec || "";
    const top = current === "library" ? "review" : current;
    for (const a of $$(".shell-nav > a")) mark(a, a.dataset.sec === top);
    for (const a of $$(".shell-more-menu a")) mark(a, a.dataset.sec === current);
    $("#shell-more-btn").classList.toggle("on", IN_MORE.includes(top));
    for (const a of $$(".shell-tabbar a")) mark(a, a.dataset.sec === current);
    for (const a of $$(".shell-sheet-links a")) mark(a, a.dataset.sec === current);
    $("#shell-more").classList.toggle("on", IN_MORE.includes(current));
  }
  function mark(a, on) {
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  function setBadge(n) {
    const count = Math.max(0, Number(n) || 0);
    for (const b of $$("[data-badge]")) {
      b.hidden = !count;
      b.innerHTML = count ? `${count > 99 ? "99+" : count}<span class="sr-only"> waiting</span>` : "";
    }
  }

  // ── Pop-overs: the status pill and the account menu ────────────────────────
  const POPS = { laptop: ["#laptop-btn", "#laptop-info"], account: ["#account-btn", "#account-menu"], more: ["#shell-more-btn", "#shell-more-menu"] };
  function setPop(which, open) {
    const [btn, menu] = POPS[which];
    if ($(menu).hidden === !open) return;   // no change: no write (every click closes them all)
    $(menu).hidden = !open;
    $(btn).setAttribute("aria-expanded", String(open));
    if (open) $(menu).querySelector("button, a")?.focus();
  }
  const popOpen = (which) => !$(POPS[which][1]).hidden;
  const toggle = (which) => { for (const w of Object.keys(POPS)) if (w !== which) setPop(w, false); setPop(which, !popOpen(which)); };
  $("#laptop-btn").addEventListener("click", () => toggle("laptop"));
  $("#account-btn").addEventListener("click", () => toggle("account"));
  $("#shell-more-btn").addEventListener("click", () => toggle("more"));
  // A section chosen from More closes it.
  $("#shell-more-menu").addEventListener("click", (e) => { if (e.target.closest("a, [data-shell-tool]")) setPop("more", false); });
  // A choice in the account menu closes it (the page acts on it).
  $("#account-menu").addEventListener("click", (e) => { if (e.target.closest("[data-account]")) setPop("account", false); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#laptop")) setPop("laptop", false);
    if (!e.target.closest("#account")) setPop("account", false);
    if (!e.target.closest(".shell-more")) setPop("more", false);
  });

  // ── More (phones) ──────────────────────────────────────────────────────────
  // Posting calendar, Results, References, Style guide, Content plan, Client questions, Today on site, Library, Instagram
  // and the account menu's items, as a sheet.
  const sheet = $("#shell-sheet");
  let sheetReturn = null;
  function openSheet() {
    const items = $("#account-menu").cloneNode(true);
    for (const el of items.querySelectorAll("[role], [id]")) { el.removeAttribute("role"); el.removeAttribute("id"); }
    $("#shell-sheet-account").innerHTML = items.innerHTML;
    sheetReturn = document.activeElement;
    sheet.hidden = false;
    $("#shell-more").setAttribute("aria-expanded", "true");
    document.body.classList.add("shell-sheet-open");
    sheet.querySelector(".shell-sheet-links a:not([hidden])")?.focus();
  }
  function closeSheet(refocus = true) {
    if (sheet.hidden) return;
    sheet.hidden = true;
    $("#shell-more").setAttribute("aria-expanded", "false");
    document.body.classList.remove("shell-sheet-open");
    if (refocus) sheetReturn?.focus?.();
    sheetReturn = null;
  }
  $("#shell-more").addEventListener("click", () => (sheet.hidden ? openSheet() : closeSheet()));
  sheet.addEventListener("click", (e) => {
    if (e.target === sheet || e.target.closest("[data-sheet-close]")) { closeSheet(); return; }
    if (e.target.closest("a[href]")) { closeSheet(false); return; }
    if (e.target.closest("[data-shell-tool]")) { closeSheet(false); return; }
    const item = e.target.closest("[data-account]");
    if (item) {
      closeSheet(false);
      // The page's own account menu handles it, as if chosen there.
      $(`#account-menu [data-account="${CSS.escape(item.dataset.account)}"]`)?.click();
    }
  });
  // Keyboard: Escape closes whatever the shell has open; Tab stays inside the sheet.
  document.addEventListener("keydown", (e) => {
    if (!sheet.hidden) {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); closeSheet(); return; }
      if (e.key === "Tab") {
        const f = [...sheet.querySelectorAll("a[href]:not([hidden]), button:not([disabled])")];
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
      return;
    }
    if (e.key === "Escape") {
      for (const which of Object.keys(POPS)) {
        if (popOpen(which)) {
          e.stopPropagation();
          setPop(which, false);
          $(POPS[which][0]).focus();
          return;
        }
      }
    }
  }, true);
  // Going somewhere closes the sheet (a link to this same page doesn't reload it).
  window.addEventListener("hashchange", () => closeSheet(false));
  // Wider than a phone: no sheet.
  window.matchMedia("(min-width: 721px)").addEventListener("change", (m) => { if (m.matches) closeSheet(false); });

  paintLinks();

  // ── Help, feedback, usage: the page's side of it ──────────────────────────
  // host: { page() -> {key, route, flags}, api(method, path, body) -> JSON, role() -> "owner" | "team",
  //         version() -> "abc1234", usage: {url, token() -> Bearer or null} | null (no capture) }
  const api = { half, links, setLinks, setCurrent, setBadge, closeSheet, current: () => current, host: null, setHost, icon };
  function setHost(host) {
    api.host = Object.assign(api.host || {}, host || {});
    const owner = api.host.role?.() === "owner";
    for (const b of $$("[data-shell-tool]")) b.hidden = !owner;
    document.dispatchEvent(new CustomEvent("studio:host"));
  }

  // help.js and help.css, fetched the first time help is pressed (the page names them in <template id="studio-lazy">,
  // so they carry the same ?v= as everything else).
  let helpLoading = null;
  function loadHelp() {
    if (window.StudioHelp) return Promise.resolve(window.StudioHelp);
    if (helpLoading) return helpLoading;
    if (document.readyState === "loading") {   // the page's <template> is further down: wait for it
      return new Promise((resolve, reject) => document.addEventListener("DOMContentLoaded", () => loadHelp().then(resolve, reject), { once: true }));
    }
    const tpl = document.getElementById("studio-lazy");
    const dir = half === "review" ? "/static/" : "";
    const src = tpl?.content.querySelector("script[src]")?.getAttribute("src") || `${dir}help.js`;
    const css = tpl?.content.querySelector("link[href]")?.getAttribute("href") || `${dir}help.css`;
    helpLoading = new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = css;
      document.head.appendChild(link);
      const sc = document.createElement("script");
      sc.src = src;
      sc.onload = () => (window.StudioHelp ? resolve(window.StudioHelp) : reject(new Error("help didn't load")));
      sc.onerror = () => { helpLoading = null; reject(new Error("help didn't load")); };
      document.head.appendChild(sc);
    });
    return helpLoading;
  }
  api.loadHelp = loadHelp;
  function openHelp(view, from) {
    for (const w of Object.keys(POPS)) setPop(w, false);
    loadHelp().then((h) => h.open(view, from)).catch(() => toastHere("Help couldn't load. Check the connection and try again."));
  }
  api.openHelp = openHelp;
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-help-open], [data-shell-tool]");
    if (!b || b.closest("[aria-disabled=true]")) return;
    e.preventDefault();
    openHelp(b.dataset.shellTool || b.dataset.helpOpen || "", b);
  });
  // A little earlier on a computer: hovering the button fetches help, so the press opens at once.
  $("#shell-help").addEventListener("pointerenter", () => { loadHelp().catch(() => {}); }, { once: true });

  function toastHere(text) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastHere.timer);
    toastHere.timer = setTimeout(() => { t.hidden = true; }, 4000);
  }

  // ── A control that can't act says why ─────────────────────────────────────
  // [aria-disabled=true][data-why]: a tap (or click) shows the reason instead of acting; hover and keyboard focus
  // show it too. One small note under the control, closed by the next tap, Escape or after a while.
  let why = null, whyFor = null, whyTimer = 0, whyWatch = 0;
  function showWhy(el, sticky) {
    const text = el.dataset.why;
    if (!text) return;
    if (!why) {
      why = document.createElement("div");
      why.className = "why-tip";
      why.id = "why-tip";
      why.setAttribute("role", "status");
      document.body.appendChild(why);
    }
    why.textContent = text;
    why.hidden = false;
    whyFor = el;
    el.setAttribute("aria-describedby", "why-tip");
    const r = el.getBoundingClientRect();
    const w = Math.min(300, window.innerWidth - 24);
    why.style.maxWidth = `${w}px`;
    const tw = Math.min(w, why.offsetWidth || w);
    const left = Math.max(12, Math.min(r.left + r.width / 2 - tw / 2, window.innerWidth - tw - 12));
    const below = r.bottom + 8 + (why.offsetHeight || 40) < window.innerHeight - 8;
    why.style.left = `${left}px`;
    why.style.top = below ? `${r.bottom + 8}px` : "";
    why.style.bottom = below ? "" : `${window.innerHeight - r.top + 8}px`;
    clearTimeout(whyTimer);
    whyTimer = setTimeout(hideWhy, sticky ? 6000 : 4000);
    // Its control redrawn or gone (a busy button that has finished): the note goes with it.
    clearInterval(whyWatch);
    whyWatch = setInterval(() => {
      if (!whyFor || why.hidden) clearInterval(whyWatch);
      else if (!whyFor.isConnected || whyFor.getAttribute("aria-disabled") !== "true") { clearInterval(whyWatch); hideWhy(); }
    }, 200);
  }
  function hideWhy() {
    if (!why || why.hidden) return;
    why.hidden = true;
    whyFor?.removeAttribute("aria-describedby");
    whyFor = null;
  }
  api.showWhy = showWhy;
  const offWithWhy = (t) => t?.closest?.("[aria-disabled=true][data-why]");
  // Capture phase: the page's own handlers never see a press on a control that can't act.
  document.addEventListener("click", (e) => {
    const el = offWithWhy(e.target);
    if (!el) { if (why && !why.hidden && !e.target.closest?.(".why-tip")) hideWhy(); return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    showWhy(el, true);
    window.StudioUsage?.why?.(el);   // usage capture: a press on a control that couldn't act
  }, true);
  document.addEventListener("pointerover", (e) => {
    if (e.pointerType !== "mouse") return;
    const el = offWithWhy(e.target);
    if (el && el !== whyFor) showWhy(el, false);
  });
  document.addEventListener("pointerout", (e) => {
    if (e.pointerType === "mouse" && whyFor && offWithWhy(e.target) === whyFor && !whyFor.contains(e.relatedTarget)) hideWhy();
  });
  document.addEventListener("focusin", (e) => { const el = offWithWhy(e.target); if (el) showWhy(el, false); else if (whyFor) hideWhy(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && why && !why.hidden) { e.preventDefault(); e.stopImmediatePropagation(); hideWhy(); return; }
    // Enter or Space on a control that can't act: the reason, not the action.
    if ((e.key === "Enter" || e.key === " ") && offWithWhy(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); showWhy(offWithWhy(e.target), true); }
  }, true);
  // Scrolling: the note follows its control while it's on screen.
  window.addEventListener("scroll", () => {
    if (!why || why.hidden || !whyFor) return;
    const r = whyFor.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight || !whyFor.isConnected) { hideWhy(); return; }
    const below = r.bottom + 8 + why.offsetHeight < window.innerHeight - 8;
    why.style.top = below ? `${r.bottom + 8}px` : "";
    why.style.bottom = below ? "" : `${window.innerHeight - r.top + 8}px`;
  }, { passive: true, capture: true });

  return api;
})();

// The home-screen app's service worker (web/sw.js; pages served by Studio carry <html data-sw>,
// Netlify's don't): the app opens from its last copy of the page instead of waiting for the server.
// ?sw=off on the address: this visit without it, and its kept pages are thrown away.
(() => {
  if (!("serviceWorker" in navigator)) return;
  if (/[?&]sw=off\b/.test(location.search)) {
    navigator.serviceWorker.getRegistrations().then((rs) => Promise.all(rs.map((r) => r.unregister())))
      .then(() => caches.keys()).then((ks) => Promise.all(ks.map((k) => caches.delete(k)))).catch(() => {});
    return;
  }
  const sw = document.documentElement.dataset.sw;
  if (!sw) return;
  const opened = Date.now();
  let touched = false;
  for (const ev of ["pointerdown", "keydown"]) addEventListener(ev, () => { touched = true; }, { once: true, capture: true });
  // Studio was updated since that copy: open the new one, unless someone is already using the page
  // (then the next open has it).
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.studio === "updated" && !touched && Date.now() - opened < 20e3) location.reload();
  });
  navigator.serviceWorker.startMessages();
  const register = () => navigator.serviceWorker.register(sw, { scope: "/" }).catch(() => {});
  if (document.readyState === "complete") register(); else addEventListener("load", register, { once: true });
})();
