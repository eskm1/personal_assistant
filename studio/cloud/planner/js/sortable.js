// Drag to reorder, the same way everywhere it's offered (the profile grid's tiles, a carousel's photos): a mouse
// drags at once; a finger holds still for a moment first (so a swipe still scrolls the page), then drags; the
// keyboard lifts with Space, moves with the arrows (up and down a row in a grid), drops with Space or Enter and
// puts it back with Escape. Never on hover alone.
//
// The page draws the items; this only follows the pointer and says what's happening:
//   sortable(container, {
//     item: ".tile",                     the items (each with data-key), in their visual order
//     key: (el) => el.dataset.key,
//     can: (el) => "" | "why it can't move",   (said once, and nothing moves)
//     cols: () => 3,                     items per row, for Up / Down (1 = a list)
//     over: (fromKey, toIndex) => {},    while dragging: draw it as if dropped there (redraw freely)
//     drop: (fromKey, toIndex) => {},    let go: keep it
//     cancel: () => {},                  Escape, or a drag that went nowhere
//     say: (text) => {},                 for screen readers (a live region)
//   returns {dragging(), active(), held() (a press that may still become a drag), stop()}
//     why: (text) => {},                 a control that can't move says why
//   })
// The dragged item carries .is-dragging (the page keeps it on the redrawn one: see dragging()), the
// container .is-sorting, and a copy of the item follows the pointer.
export function sortable(box, o) {
  const st = { key: null, from: -1, to: -1, ghost: null, mode: "", timer: 0, start: null, pointer: null, moved: false, raf: 0 };
  const items = () => [...box.querySelectorAll(o.item)];
  const keyOf = (el) => o.key(el);
  const indexOf = (k) => items().findIndex((el) => keyOf(el) === k);
  const LONG = 360, SLOP = 8;

  function lift(el, mode, x, y) {
    const why = o.can?.(el) || "";
    if (why) { o.why?.(why); return false; }
    st.key = keyOf(el);
    st.from = st.to = indexOf(st.key);
    st.mode = mode;
    st.moved = false;
    box.classList.add("is-sorting");
    el.classList.add("is-dragging");
    if (mode !== "keys") {
      const r = el.getBoundingClientRect();
      const g = el.cloneNode(true);
      g.classList.add("sort-ghost");
      g.classList.remove("is-dragging");
      g.removeAttribute("id");
      g.setAttribute("aria-hidden", "true");
      Object.assign(g.style, { width: `${r.width}px`, height: `${r.height}px`, left: `${r.left}px`, top: `${r.top}px` });
      g.dataset.dx = String(x - r.left);
      g.dataset.dy = String(y - r.top);
      document.body.appendChild(g);
      st.ghost = g;
      if (mode === "touch") navigator.vibrate?.(12);
    }
    o.over?.(st.key, st.to);
    if (mode === "keys") refocus();
    o.say?.(`Picked up. ${mode === "keys" ? "Arrow keys move it, Space drops it, Escape puts it back." : "Move it, then let go."}`);
    return true;
  }
  function follow(x, y) {
    if (!st.ghost) return;
    st.ghost.style.left = `${x - Number(st.ghost.dataset.dx)}px`;
    st.ghost.style.top = `${y - Number(st.ghost.dataset.dy)}px`;
    // The nearest item's middle is where it would land.
    let best = -1, dist = Infinity;
    items().forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const d = Math.hypot(r.left + r.width / 2 - x, r.top + r.height / 2 - y);
      if (d < dist) { dist = d; best = i; }
    });
    if (best >= 0 && best !== st.to) { st.to = best; st.moved = true; o.over?.(st.key, st.to); }
    edgeScroll(y);
  }
  // Near the top or bottom of the screen while dragging, the page scrolls.
  function edgeScroll(y) {
    cancelAnimationFrame(st.raf);
    const edge = 56, h = window.innerHeight;
    const v = y < edge ? -(edge - y) / 3 : y > h - edge ? (y - (h - edge)) / 3 : 0;
    if (!v || !st.key) return;
    st.raf = requestAnimationFrame(() => { window.scrollBy(0, v); if (st.pointer) follow(st.pointer.x, st.pointer.y); });
  }
  function end(keep) {
    cancelAnimationFrame(st.raf);
    clearTimeout(st.timer);
    const k = st.key, to = st.to, moved = st.moved || st.to !== st.from;
    st.ghost?.remove();
    st.ghost = null;
    box.classList.remove("is-sorting");
    for (const el of box.querySelectorAll(".is-dragging")) el.classList.remove("is-dragging");
    st.key = null;
    st.mode = "";
    st.start = st.pointer = null;
    if (k == null) return;
    if (keep && moved) { o.drop?.(k, to); o.say?.(`Dropped at place ${to + 1}.`); }
    else { o.cancel?.(); o.say?.("Put back."); }
  }

  // ── Mouse and pen: pointer events ──
  box.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;   // a finger: the touch events below
    const el = e.target.closest(o.item);
    if (!el || !box.contains(el) || e.button > 0 || st.key || e.target.closest("[data-no-drag]")) return;
    st.start = { x: e.clientX, y: e.clientY, el, id: e.pointerId, type: e.pointerType };
    st.pointer = { x: e.clientX, y: e.clientY };
  });
  window.addEventListener("pointermove", (e) => {
    if (!st.start || st.start.type === "touch" || e.pointerId !== st.start.id) return;
    st.pointer = { x: e.clientX, y: e.clientY };
    if (!st.key) {
      if (Math.hypot(e.clientX - st.start.x, e.clientY - st.start.y) <= 5) return;
      if (!lift(st.start.el, "mouse", e.clientX, e.clientY)) { st.start = null; return; }
    }
    e.preventDefault();
    follow(e.clientX, e.clientY);
  }, { passive: false });
  const up = (e) => {
    if (!st.start || st.start.type === "touch" || e.pointerId !== st.start.id) return;
    if (st.key && st.mode === "mouse") { suppressClick(); end(e.type === "pointerup"); }
    st.start = null;
  };
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);

  // ── A finger: touch events (a browser that starts to scroll cancels pointer events, never these). Held still
  // for a moment it lifts; moved first, it's a scroll. Once lifted, the page doesn't scroll under it. ──
  box.addEventListener("touchstart", (e) => {
    const el = e.target.closest?.(o.item);
    if (!el || !box.contains(el) || st.key || e.touches.length > 1 || e.target.closest("[data-no-drag]")) return;
    const t = e.touches[0];
    st.start = { x: t.clientX, y: t.clientY, el, type: "touch" };
    st.pointer = { x: t.clientX, y: t.clientY };
    clearTimeout(st.timer);
    st.timer = setTimeout(() => { if (st.start?.type === "touch" && lift(st.start.el, "touch", st.pointer.x, st.pointer.y)) follow(st.pointer.x, st.pointer.y); }, LONG);
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (st.start?.type !== "touch") return;
    const t = e.touches[0];
    if (!t) return;
    st.pointer = { x: t.clientX, y: t.clientY };
    if (!st.key) {
      if (Math.hypot(t.clientX - st.start.x, t.clientY - st.start.y) > SLOP) { clearTimeout(st.timer); st.start = null; }  // a scroll
      return;
    }
    if (e.cancelable) e.preventDefault();
    follow(t.clientX, t.clientY);
  }, { passive: false });
  const touchEnd = (e) => {
    if (st.start?.type !== "touch") return;
    clearTimeout(st.timer);
    if (st.key && st.mode === "touch") { suppressClick(); end(e.type === "touchend"); }
    st.start = null;
  };
  document.addEventListener("touchend", touchEnd);
  document.addEventListener("touchcancel", touchEnd);
  // A long press opens no menu on the items (and a held image isn't offered for saving).
  box.addEventListener("contextmenu", (e) => { if (e.target.closest(o.item) && (st.key || st.start?.type === "touch")) e.preventDefault(); });
  // The click a drag ends with isn't a tap on the item.
  function suppressClick() {
    const stop = (e) => { e.stopPropagation(); e.preventDefault(); };
    window.addEventListener("click", stop, { capture: true, once: true });
    setTimeout(() => window.removeEventListener("click", stop, { capture: true }), 400);
  }
  box.addEventListener("dragstart", (e) => { if (e.target.closest?.(o.item)) e.preventDefault(); });

  // ── Keyboard ──
  box.addEventListener("keydown", (e) => {
    const el = e.target.closest?.(o.item);
    if (!el || e.altKey || e.ctrlKey || e.metaKey) return;
    if (!st.key) {
      if (e.key === " ") { e.preventDefault(); lift(el, "keys"); }
      return;
    }
    if (st.mode !== "keys") return;
    const n = items().length, cols = Math.max(1, o.cols?.() || 1);
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[e.key];
    if (step) {
      e.preventDefault();
      const to = Math.min(n - 1, Math.max(0, st.to + step));
      if (to !== st.to) { st.to = to; o.over?.(st.key, to); o.say?.(`Place ${to + 1} of ${n}.`); }
      refocus();
      return;
    }
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); const k = st.key; end(true); requestAnimationFrame(() => items().find((x) => keyOf(x) === k)?.focus()); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const k = st.key; end(false); requestAnimationFrame(() => items().find((x) => keyOf(x) === k)?.focus()); }
    if (e.key === "Tab") end(false);
  });

  // The page may draw the items again as they move: the moving one keeps the focus, so the next key moves it again.
  function refocus() {
    const el = items().find((x) => keyOf(x) === st.key);
    if (el && document.activeElement !== el) el.focus({ preventScroll: false });
  }

  // Space lifts and drops here (a button would also be pressed on its keyup): Enter is the item's own press.
  box.addEventListener("keyup", (e) => { if (e.key === " " && e.target.closest?.(o.item)) e.preventDefault(); });

  return {
    dragging: () => st.key,
    active: () => !!st.key,
    // A press on an item that may still become a drag (a finger holding still): the page shouldn't be redrawn under it.
    held: () => !!st.key || !!st.start,
    stop: () => end(false),
  };
}
