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
"use strict";
window.StudioShell = (() => {
  const root = document.getElementById("shell");
  if (!root) return null;
  const $ = (sel) => root.querySelector(sel);
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
  };
  const icon = (name) => `<svg class="shell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
  const badge = `<span class="shell-badge" data-badge hidden></span>`;

  // The sections behind More: on a computer a menu under More, on a phone the More sheet.
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
          </div>
        </div>
      </nav>
      <div class="shell-right">
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
        </nav>
        <div class="shell-sheet-account" id="shell-sheet-account"></div>
      </div>
    </div>`;

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
  $("#shell-more-menu").addEventListener("click", (e) => { if (e.target.closest("a")) setPop("more", false); });
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
  return { half, links, setLinks, setCurrent, setBadge, closeSheet, current: () => current };
})();
