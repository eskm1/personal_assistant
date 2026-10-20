// Usage capture for Review and the planner (activity.py, studio/CLAUDE.md "The usability standard" 4): page views,
// presses on controls, dead clicks (a press that changed nothing within 700 ms), rage clicks (3+ quick presses on
// one spot), dialogs closed without their action, errors and failed API calls, and the golden paths' named steps
// (StudioUsage.mark). Never content: a control is its id, a data-* action name, a label from the app's own chrome
// or its tag and classes; never the words on a card, title, caption or note. Batched: one request every ~15 s at
// most, sendBeacon when the page is left; never in the way of a press (passive listeners, no waiting).
// The page tells it where it is and where to send through StudioShell.setHost (shell.js). localStorage
// studio.qa = "1" marks a test walk: every event carries qa, and the weekly report leaves them out.
"use strict";
(() => {
  if (window.StudioUsage) return;
  const store = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const qa = store("studio.qa") === "1";
  const rand = () => Array.from(crypto.getRandomValues(new Uint8Array(9)), (b) => "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"[b % 62]).join("");
  const session = (() => {
    try {
      let s = sessionStorage.getItem("studio.tab");
      if (!s) { s = rand(); sessionStorage.setItem("studio.tab", s); }
      return s;
    } catch { return rand(); }
  })();
  const Q = [];
  // The first batch goes ~15 s after the page opens (or when it's left), like every one after it.
  let off = false, timer = 0, lastSend = Date.now(), sending = false, inflight = null;
  const host = () => window.StudioShell?.host || null;

  function where() {
    let p = {};
    try { p = host()?.page?.() || {}; } catch { /* the page isn't ready */ }
    return { key: p.key || "other", route: p.route || `${location.pathname.startsWith("/plan") ? "/plan/" : location.pathname}${location.hash || "#/"}`,
      flags: p.flags || null, pending: !!p.pending };
  }
  function push(k, g, d) {
    if (off) return;
    const w = where();
    Q.push({ t: Date.now(), k, p: w.key, r: w.route, ...(g ? { g: String(g).slice(0, 120) } : {}), ...(d ? { d } : {}) });
    if (Q.length > 400) Q.splice(0, Q.length - 400);
    schedule();
  }
  function schedule() {
    if (Q.length >= 40) { flush(); return; }
    if (!timer) timer = setTimeout(flush, Math.max(15000 - (Date.now() - lastSend), 2000));
  }

  async function flush(leaving = false) {
    clearTimeout(timer);
    timer = 0;
    const h = host();
    if (off || !h || (!Q.length && !(leaving && inflight))) return;
    if (h.usage === null) { off = true; Q.length = 0; return; }   // capture is off on this Studio
    const url = h.usage?.url;
    if (!url || (sending && !leaving)) { if (!leaving) schedule(); return; }   // leaving: send now, even mid-send
    const same = url.startsWith("/");
    // A request still on its way as the page goes: its batch goes again by beacon (Studio drops the copy by its id).
    if (leaving && inflight && same && navigator.sendBeacon) navigator.sendBeacon(url, new Blob([inflight], { type: "text/plain" }));
    if (!Q.length) return;
    const batch = Q.splice(0, 200);
    const body = JSON.stringify({ session, batch: rand(), qa, events: batch });
    lastSend = Date.now();
    if (leaving && same && navigator.sendBeacon) {
      if (navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }))) return;
    }
    sending = true;
    inflight = body;
    try {
      const tok = same ? null : await h.usage.token?.();
      if (!same && !tok) throw new Error("no token");
      const res = await realFetch(url, {
        method: "POST", body, keepalive: body.length < 60000, credentials: same ? "same-origin" : "omit",
        headers: { "Content-Type": "text/plain", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      });
      const j = await res.json().catch(() => ({}));
      if (j.on === false) { off = true; Q.length = 0; }
      else if (!res.ok && res.status !== 400 && res.status !== 413) Q.unshift(...batch.slice(0, 200));
    } catch {
      if (!leaving) Q.unshift(...batch);
    } finally {
      sending = false;
      inflight = null;
      if (Q.length && !leaving) schedule();
    }
  }
  addEventListener("pagehide", () => flush(true));
  // A link to the other half of Studio (Review <-> Plan) leaves this page: send now, before it goes.
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href]");
    if (!a || a.target === "_blank" || e.defaultPrevented) return;
    try {
      const u = new URL(a.href, location.href);
      if (u.origin === location.origin && u.pathname !== location.pathname) setTimeout(() => flush(true), 0);
    } catch { /* not a link */ }
  });
  // Hidden (another app, the phone locked): send then. A moment later, so a page being left sends from pagehide
  // instead (a beacon from the hidden event of a page being left can be dropped).
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") setTimeout(() => flush(true), 50); });

  // ── What a control is, without its words ───────────────────────────────────
  const CONTROL = "button, a[href], [role=button], [role=menuitem], [role=tab], [role=link], summary, label, input, select, textarea, [data-help-open], [tabindex]:not([tabindex='-1'])";
  const strip = (s) => String(s).replace(/\d+/g, "n");
  const token = /^[a-z][a-z0-9_.-]{0,30}$/;
  function describe(el) {
    if (!el || el.nodeType !== 1) return "";
    const c = el.closest(CONTROL) || el;
    let d = "";
    if (c.id) d = `#${strip(c.id)}`;
    if (!d) {
      for (const a of c.attributes) {
        if (!a.name.startsWith("data-") || /^data-(why|orig|name|copy|swipe)$/.test(a.name)) continue;
        const v = a.value;
        d = `[${a.name}${v && token.test(v) && !/^\d/.test(v) ? `=${v}` : ""}]`;
        break;
      }
    }
    if (!d) {
      // A label only from the app's own chrome (the bar, More, help): elsewhere a label can carry a title.
      const label = c.closest("#shell, .help-root, .shell-sheet") ? (c.getAttribute("aria-label") || "").split(":")[0].trim() : "";
      if (label && label.length <= 30 && !/\d/.test(label)) d = `aria:${label}`;
    }
    if (!d) {
      const cls = [...c.classList].filter((k) => /^[a-z][a-z0-9-]*$/.test(k)).slice(0, 3);
      const role = c.getAttribute("role");
      d = `${c.tagName.toLowerCase()}${role ? `[role=${role}]` : ""}${cls.length ? `.${cls.join(".")}` : ""}`;
    }
    // Where it is: the nearest box with an id (#decision-bar, #home, #modal ...).
    const box = (c.parentElement || c).closest("[id]");
    return box && box !== c && box.id && !d.startsWith(`#${strip(box.id)}`) ? `${d} in #${strip(box.id)}` : d;
  }
  const isField = (el) => !!el.closest?.("input, textarea, select, [contenteditable=''], [contenteditable=true], video, audio, canvas");

  // ── Presses: clicks, dead clicks, rage clicks ──────────────────────────────
  let presses = [];
  let requests = 0;
  document.addEventListener("click", (e) => {
    const el = e.target;
    if (!(el instanceof Element) || off || el.closest(".help-root, .hp-spot, .why-tip")) return;
    const target = describe(el);
    const control = el.closest(CONTROL);
    if (control) push("click", target);
    // Rage: three presses within a second, close together.
    const now = Date.now();
    presses = presses.filter((p) => now - p.t < 1000 && Math.hypot(p.x - e.clientX, p.y - e.clientY) < 40);
    presses.push({ t: now, x: e.clientX, y: e.clientY });
    if (presses.length === 3) push("rage", target, { n: 3 });
    if (isField(el)) return;
    // Dead: nothing changed within 700 ms (no DOM change, no navigation, no request, no focus to a field).
    if (!control) {
      const r = el.getBoundingClientRect();
      if (r.width * r.height > 0.4 * innerWidth * innerHeight) return;   // the page's background, not a thing
    }
    watchDead(target, location.href, requests);
  }, { capture: true, passive: true });

  // Requests the page made (pictures, scripts, Supabase, Studio), not counting these reports.
  let fetched = 0;
  try {
    new PerformanceObserver((list) => { for (const e of list.getEntries()) if (!e.name.includes("/api/usage")) fetched += 1; })
      .observe({ type: "resource" });
  } catch { /* an old browser: the DOM and the address still tell */ }
  let deadWatch = null;
  function watchDead(target, href, reqs) {
    deadWatch?.stop(false);
    let changed = false;
    const mo = new MutationObserver(() => { changed = true; mo.disconnect(); });
    mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
    const resources = fetched;
    const t = setTimeout(() => w.stop(true), 700);
    const w = {
      stop(judge) {
        clearTimeout(t);
        mo.disconnect();
        if (deadWatch === w) deadWatch = null;
        if (!judge || changed || location.href !== href || requests !== reqs) return;
        if (fetched !== resources) return;
        if (document.activeElement && isField(document.activeElement)) return;
        if (String(getSelection?.() || "").trim()) return;   // selecting text
        push("dead", target);
      },
    };
    deadWatch = w;
  }

  // ── Page views ─────────────────────────────────────────────────────────────
  let lastView = "", viewTimer = 0;
  function view(tries = 0) {
    clearTimeout(viewTimer);
    viewTimer = setTimeout(() => {
      if (!host()) { if (tries < 20) view(tries + 1); return; }
      const w = where();
      if (w.pending && tries < 8) { view(tries + 1); return; }
      const sig = `${w.key}|${w.route}`;
      if (sig === lastView) return;
      lastView = sig;
      push("view", "", w.flags || undefined);
    }, tries ? 500 : 900);
  }
  addEventListener("hashchange", () => view());
  addEventListener("popstate", () => view());
  // The apps also move with pushState / replaceState (the next draft after a decision): a view too.
  for (const fn of ["pushState", "replaceState"]) {
    const real = history[fn].bind(history);
    history[fn] = function (...args) { const before = location.href; const r = real(...args); if (location.href !== before) view(); return r; };
  }
  document.addEventListener("studio:host", () => view(), { once: true });
  view();

  // ── Dialogs closed without their action ────────────────────────────────────
  // #modal (both apps) and post-ui's .sp-modal. "done" if a button inside other than close/cancel was pressed.
  const open = new Map();
  function dialogKey(m) {
    const body = m.querySelector("#modal-body, .sp-body") || m;
    const named = [...body.querySelectorAll("[id]")].find((x) => !/^(modal|sp)-(title|error)$/.test(x.id));
    if (named) return `#${strip(named.id)}`;
    const first = body.firstElementChild;
    return first ? describe(first).split(" in ")[0] : "dialog";
  }
  const watchModals = () => {
    for (const m of document.querySelectorAll(".modal")) {
      const shown = !m.hidden;
      if (shown && !open.has(m)) open.set(m, { key: dialogKey(m), t: Date.now(), acted: false, action: hasAction(m) });
      if (!shown && open.has(m)) {
        const o = open.get(m);
        open.delete(m);
        push("dialog", o.key, { result: o.acted ? "done" : o.action ? "cancelled" : "closed", ms: Date.now() - o.t });
      }
    }
  };
  const ACTION = ".btn.primary, .btn.act, .btn.danger, .btn.good, [type=submit]";
  const hasAction = (m) => !!m.querySelector(ACTION);
  // Only "hidden" flips (a dialog shown or closed), not every render: cheap on busy pages.
  new MutationObserver((list) => { if (list.some((m) => m.target.classList?.contains("modal"))) watchModals(); })
    .observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
  document.addEventListener("click", (e) => {
    const m = e.target.closest?.(".modal");
    const o = m && open.get(m);
    if (o) { o.key = dialogKey(m); o.action = o.action || hasAction(m); }   // a dialog that was still loading when it opened
    const b = e.target.closest?.("button, [role=button], a[href]");
    // Its action: the dialog's main button (Save, Add, Schedule, Delete...), not a toggle inside it or Cancel.
    if (o && b && b.matches(ACTION) && !b.matches("[data-modal-close], [data-sp-close], .close, [aria-disabled=true]")) o.acted = true;
  }, true);

  // ── Errors ─────────────────────────────────────────────────────────────────
  const file = (src) => String(src || "").split("?")[0].split("/").pop();
  let errors = 0;
  addEventListener("error", (e) => {
    if (!(e instanceof ErrorEvent) || errors++ > 20) return;
    push("error", "", { msg: String(e.error?.message || e.message || "error").replace(/^Uncaught (\w*Error: )?/, "").slice(0, 200), src: `${file(e.filename) || "inline"}:${e.lineno || 0}` });
  });
  addEventListener("unhandledrejection", (e) => {
    if (errors++ > 20) return;
    const r = e.reason;
    const stack = String(r?.stack || "").match(/([\w.-]+\.js)(?:\?[^:]*)?:(\d+)/);
    push("error", "", { msg: String(r?.message || r || "rejected").slice(0, 200), src: stack ? `${stack[1]}:${stack[2]}` : "promise" });
  });
  // Failed API calls (Studio's and Supabase's through fetch): method, path with ids replaced, status.
  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    requests += 1;
    const p = realFetch(input, init);
    p.then((res) => { if (!res.ok && res.status !== 304) apiError(input, init, res.status); }, () => apiError(input, init, 0));
    return p;
  };
  function apiError(input, init, status) {
    try {
      const u = new URL(typeof input === "string" ? input : input.url, location.href);
      if (u.pathname.endsWith("/api/usage")) return;
      const method = (init?.method || input?.method || "GET").toUpperCase();
      push("api_error", "", { method, path: u.pathname.replace(/\/[0-9a-f-]{6,}|\/\d+/gi, "/:id"), status });
    } catch { /* not a URL */ }
  }

  // ── For the pages ──────────────────────────────────────────────────────────
  window.StudioUsage = {
    // A golden path's step: note_added, approved, sent_back, idea_decided, make_this, upload_done, idea_created, ...
    mark(name, detail) { if (/^[a-z_]{2,30}$/.test(name)) push("action", name, detail && typeof detail === "object" ? detail : undefined); },
    // A press on a control that couldn't act (shell.js showed why).
    why(el) { push("click", describe(el), { off: true }); },
    describe, flush: () => flush(), qa, session, waiting: () => Q.length, off: () => off,
  };
  for (const [name, detail] of window.__su || []) window.StudioUsage.mark(name, detail);
  window.__su = [];
})();
