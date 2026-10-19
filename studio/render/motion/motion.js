// Studio's animated explainers (motion.py writes the page; this file moves it).
//
// The page is static markup: every scene is a HyperFrames clip (data-start, data-duration), and every
// element that moves carries data-a (the move), data-at (seconds after its scene starts) and optionally
// data-d (how long) and data-origin. Python decides the choreography; this file holds the small
// vocabulary of moves, all calm (eases out, 0.4-0.8 s, nothing that bounces), fits text that is too big
// for its box, then registers one paused GSAP timeline for HyperFrames to seek frame by frame.
(function () {
  const ID = document.querySelector("[data-composition-id]").dataset.compositionId;
  const MOVES = {
    rise: [{ y: 40, opacity: 0 }, { y: 0, opacity: 1 }, 0.6, "power3.out"],
    "rise-word": [{ yPercent: 112 }, { yPercent: 0 }, 0.7, "power3.out"],
    fade: [{ opacity: 0 }, { opacity: 1 }, 0.5, "power1.out"],
    draw: [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }, 0.8, "power2.inOut"],
    "grow-x": [{ scaleX: 0 }, { scaleX: 1 }, 0.6, "power3.inOut"],
    "grow-y": [{ scaleY: 0 }, { scaleY: 1 }, 0.6, "power3.inOut"],
    pop: [{ scale: 0.88, opacity: 0 }, { scale: 1, opacity: 1 }, 0.5, "power3.out"],
    "slide-l": [{ x: -48, opacity: 0 }, { x: 0, opacity: 1 }, 0.6, "power3.out"],
    "slide-r": [{ x: 48, opacity: 0 }, { x: 0, opacity: 1 }, 0.6, "power3.out"],
    wipe: [{ clipPath: "inset(0% 100% 0% 0%)" }, { clipPath: "inset(0% 0% 0% 0%)" }, 0.8, "power3.inOut"],
    "wipe-up": [{ clipPath: "inset(100% 0% 0% 0%)" }, { clipPath: "inset(0% 0% 0% 0%)" }, 0.8, "power3.inOut"],
    dim: [{ opacity: 1 }, { opacity: 0.55 }, 0.5, "power1.inOut"],
  };

  // Text that doesn't fit its stage gets smaller, a step at a time (never below 62%).
  function fit() {
    document.querySelectorAll(".stage").forEach((st) => {
      const fits = [...st.querySelectorAll(".fit")];
      fits.forEach((f) => { f.dataset.px = parseFloat(getComputedStyle(f).fontSize); });
      const over = () => st.scrollHeight > st.clientHeight + 1 || fits.some((f) => f.scrollWidth > f.clientWidth + 1);
      let k = 1;
      while (over() && k > 0.62) {
        k = Math.round((k - 0.03) * 100) / 100;
        fits.forEach((f) => { f.style.fontSize = (Number(f.dataset.px) * k).toFixed(1) + "px"; });
      }
      st.dataset.fit = k.toFixed(2);
      st.dataset.overflow = over() ? "1" : "0";
    });
  }

  const seen = new WeakSet();
  function add(tl, el, at) {
    const move = el.dataset.a;
    const first = !seen.has(el);
    seen.add(el);
    if (el.dataset.origin) gsap.set(el, { transformOrigin: el.dataset.origin });
    if (move === "count") {  // a number counting up to its value
      const to = Number(el.dataset.to), dec = Number(el.dataset.dec || 0), o = { v: 0 };
      const show = () => { el.textContent = o.v.toFixed(dec); };
      tl.fromTo(o, { v: 0 }, { v: to, duration: Number(el.dataset.d || 1.1), ease: "power2.out", onUpdate: show,
        immediateRender: first }, at);
      show();
      return;
    }
    if (move === "breathe") {  // one slow swell to draw the eye, then back
      tl.fromTo(el, { scale: 1 }, { scale: 1.035, duration: Number(el.dataset.d || 0.9), ease: "sine.inOut", yoyo: true,
        repeat: 1, immediateRender: false }, at);
      return;
    }
    const m = MOVES[move];
    if (!m) return;
    // autoRound off: GSAP rounds px values, which would snap a stroke drawn on 1 -> 0 (pathLength 1) in one frame.
    tl.fromTo(el, { ...m[0] }, { ...m[1], duration: Number(el.dataset.d || m[2]), ease: el.dataset.ease || m[3],
      immediateRender: first, autoRound: false }, at);
  }

  async function build() {
    await document.fonts.ready;
    fit();
    const tl = gsap.timeline({ paused: true });
    const total = Number(document.querySelector("[data-composition-id]").dataset.duration);
    const grid = document.querySelector(".bg .grid");
    if (grid) tl.fromTo(grid, { y: 0 }, { y: -96, duration: total, ease: "none" }, 0);
    document.querySelectorAll(".scene").forEach((sc) => {
      const s = Number(sc.dataset.start), d = Number(sc.dataset.duration);
      sc.querySelectorAll("[data-a]").forEach((el) => add(tl, el, s + Number(el.dataset.at || 0)));
      const seg = document.querySelector(`.seg[data-i="${sc.dataset.i}"] b`);
      if (seg) tl.fromTo(seg, { scaleX: 0 }, { scaleX: 1, duration: d, ease: "none" }, s);
      if (sc.dataset.exit === "1") {
        tl.fromTo(sc.querySelector(".inner"), { opacity: 1, y: 0 }, { opacity: 0, y: -28, duration: 0.42, ease: "power2.in",
          immediateRender: false }, s + d - 0.45);
      }
    });
    const hdr = document.querySelector(".hdr");
    if (hdr) {
      const s = Number(hdr.dataset.start), d = Number(hdr.dataset.duration);
      tl.fromTo(hdr, { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power1.out" }, s);
      tl.fromTo(hdr, { opacity: 1 }, { opacity: 0, duration: 0.42, ease: "power2.in", immediateRender: false }, s + d - 0.45);
    }
    window.__timelines = window.__timelines || {};
    window.__timelines[ID] = tl;
    window.__motionReady = true;
  }
  build();
})();
