// Help, one press away on every screen (studio/CLAUDE.md, "The usability standard" 3 and 4). Loaded by shell.js the
// first time Help is pressed. One panel (a sheet on phones):
//   This page   a few lines on what the page is for and its main action, and Show me: a walk through its main
//               controls in place (one at a time with a line each; Next / Done; Escape stops; skips what isn't there)
//   Ask Studio  a how-to question answered by Claude from Studio's notes (POST /api/help/ask, help_desk.py)
//   Something not working?  tap the part (or skip), a few words, Send (POST /api/feedback)
// and, for the owner from More: Feedback (what people flagged, newest first, mark done) and Weekly report access.
// The page says where it is and how to reach Studio through StudioShell.setHost (shell.js).
"use strict";
window.StudioHelp = (() => {
  const shell = window.StudioShell;
  const host = () => shell?.host || {};
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const qa = (() => { try { return localStorage.getItem("studio.qa") === "1"; } catch { return false; } })();
  const PHONE = window.matchMedia("(max-width: 720px)");

  // ── What each page is for, and its walk-through ────────────────────────────
  // tour: [selector, line]. A step whose control isn't on screen is skipped.
  const PAGES = {
    home: { name: "Plan", lines: [
      "What needs you today, across every project.",
      "Bryan sees Needs your call: drafts to review and posts Studio suggests. Go, Not now or Dismiss.",
      "The team sees Your jobs: shots to film, ideas to Go or Park, drafts to leave notes on. Bryan's list is folded under it.",
      "The month: one slot per post you want. **+ Add** beside a gap starts an idea of that kind.",
      "Main action: **+ New idea**, the orange button.",
    ], tour: [
      ["#home-new-idea", "Start here. An idea into a project, with its kind."],
      ["#hm-needs", "What's waiting for you: drafts, shots to film and ideas to decide."],
      ["[data-sugg-act=go]", "Go keeps a suggestion. Not now parks it. Dismiss (Bryan) drops it."],
      [".hm-row .hm-act", "Opens the draft, the shot list or the ideas it's about."],
      [".hm-bryan", "What only Bryan can do, folded away."],
      ["[data-add-kind]", "+ Add starts an idea of the kind the month is short of."],
      [".hm-month", "This month's posts. Tap a kind to plan more of it."],
      [".hm-projects", "Your projects. Open one to see its ideas."],
      [".hm-coming", "Coming up: posts, site days, edits and filming."],
    ] },
    ideas: { name: "Ideas board", lines: [
      "One project's posts, grouped by what to do next.",
      "Waiting for your call (Waiting for Go or Park on the team's board): open one, then Go or Park.",
      "Film these: Go posts with shots still to film. Ready to make: every shot is in.",
      "Main action: **+ New idea**. **Project ⋯** has the shoot plan and Today on site.",
    ], tour: [
      ["#plan-project", "The project you're in. Tap to switch."],
      ["#plan-add-one", "+ New idea adds a post to this project."],
      [".grp-waiting", "Ideas waiting for a call: open one to decide Go or Park."],
      [".grp-film", "Film these: open one to film or upload its shots."],
      [".grp-ready", "Ready to make: every shot is in."],
      ["#plan-tools", "Project menu: shoot plan, Today on site, the brief and series."],
    ] },
    shoot: { name: "Shoot plan", lines: [
      "Which shots get filmed on which site day.",
      "Add the site days, then place the shots on them.",
      "On the day, Today on site lists what to film.",
    ], tour: [
      ["[data-day-add]", "Add a site day."],
      ["#sp-place", "Studio places the unplaced shots on your site days."],
      ["[data-plan-mode=ideas]", "Back to the ideas."],
    ] },
    idea: { name: "An idea", lines: [
      "One post: your call, the shots to film and the next step.",
      "Anyone on the team can Go or Park it. Dismiss and Make this are Bryan's.",
      "Tap the line under the title for its kind, format and caption.",
      "Main action: the big button at the bottom. It always shows the next step.",
    ], tour: [
      ["[data-plan-decision=go]", "Your call: Go to make it, Park for later. Tap it again to undo."],
      ["[data-details]", "Kind of post, format and caption. Tap to change."],
      [".shot-row", "A shot to film. Film or upload straight to it."],
      ["#idea-action", "The next step, always here: storyboard, film, Make this or Schedule."],
      ["#comment-body", "Comments for the team."],
    ] },
    today: { name: "Today on site", lines: [
      "The shots to film today, for the project you're on.",
      "Tap a shot to tick it off. Film or upload straight to it.",
      "Only what's left shows unless you switch it off.",
    ], tour: [
      [".cl-shot", "Tap to tick a shot off when it's filmed."],
      ["[data-up-film], [data-up-choose]", "Film or upload a take for that shot."],
      ["#cl-only", "Show only the shots still to film."],
    ] },
    calendar: { name: "Posting calendar", lines: [
      "What goes out on Instagram, day by day.",
      "Scheduled posts show at their time. Tap one to change or cancel it.",
      "Schedule from an approved draft: its page has **Schedule**.",
    ], tour: [
      [".pc-cell.today", "Today."],
      ["[data-pc-tab]", "Switch between the calendar and the list."],
      ["[data-pp-change]", "Change the time, or cancel a post."],
    ] },
    results: { name: "Results", lines: [
      "How the posts did on Instagram: views, reach, saves and shares.",
      "Numbers arrive about an hour after a post goes out, then every morning.",
    ], tour: [
      [".pp-card", "One post's numbers."],
      ["#pr-all", "Every post, not just the latest."],
    ] },
    instagram: { name: "Instagram", lines: [
      "The Instagram account Studio posts to.",
      "Bryan connects it once with a token from Meta. It stays on the Studio machine.",
    ], tour: [["#pp-connect", "The connection: who it posts as, or Connect."]] },
    refs: { name: "References", lines: [
      "Reels and posts you admire, for Studio to learn your taste from.",
      "Add one link, or import many at once.",
      "Analyse reads them; the taste report sums them up.",
    ], tour: [
      ["#ref-add, #ref-add-empty", "Add one reference by its link."],
      ["#ref-import, #ref-import-empty", "Import many: paste links or a CSV."],
      [".lib-tile", "A reference. Open it for what Studio saw."],
    ] },
    style: { name: "Style guide", lines: [
      "How Urban Makers posts look and sound. The editor follows it.",
      "Studio drafts updates from the references. Bryan approves them.",
    ], tour: [
      ["#sg-draft-h", "A new draft waiting for Bryan."],
      ["[data-sg-approve]", "Approve puts the draft in force."],
      ["#sg-active-h", "The guide in force now."],
    ] },
    content: { name: "Content plan", lines: [
      "The month's mix: how many posts of each kind.",
      "Targets and offers here steer what Studio suggests.",
      "Save when you change it.",
    ], tour: [
      ["#cp-mix", "Posts a month, and the mix of kinds."],
      ["#cp-offers", "The offers Studio may mention. Never invented ones."],
      ["#cp-save", "Save the plan."],
    ] },
    questions: { name: "Client questions", lines: [
      "Questions clients ask. Studio turns them into posts.",
      "Add one in the client's words. Keep names and addresses out.",
    ], tour: [
      ["#q-new, #q-add", "Add a question."],
      ["[data-q-edit]", "Change or remove a question."],
    ] },
    team: { name: "Team", lines: ["Who can sign in to Studio, and invites.", "Only Bryan invites and removes people."], tour: [] },
    review: { name: "To review", lines: [
      "Drafts waiting for Bryan. Open one to watch it.",
      "Leave notes at a moment, then approve it or send it back.",
      "The next draft opens straight after a decision.",
    ], tour: [
      ["#tabs", "Review's stages: To review, Done and the Library."],
      ["#tabs-more", "More: Being edited, Ingesting and In the cloud."],
      ["#grid .tile", "A draft. Open it to watch and decide."],
    ] },
    draft: { name: "A draft", lines: [
      "Watch it, note what to change, then decide.",
      "Pause where it needs a change and type in the box under the video: the note pins to that moment.",
      "Studio's read lists what to fix. They all go to the editor when you **Send back**.",
      "Main action: the bar at the bottom. **Send back** with your notes (anyone), or **Approve** (Bryan).",
    ], tour: [
      ["#player-frame", "The draft. Tap to play."],
      ["#quick-note, #note-jump, #note-body", "Type a note. It pins to the moment the video is at."],
      ["#note-remember", "Remember for future edits: the editor follows it on every edit."],
      [".read-card", "Studio's read: what works and what to fix."],
      ["#decision-bar", "Approve, or send it back with your notes."],
      ["#head-next", "The next draft."],
    ] },
    carousel: { name: "A carousel", lines: [
      "Swipe the slides, note what to change, then decide.",
      "A note can name a slide.",
      "Main action: the bar at the bottom. **Approve**, or **Send back** with your notes.",
    ], tour: [
      [".swiper, #player-frame", "The slides. Swipe or use the arrows."],
      ["#note-slide", "Which slide the note is about."],
      ["#note-body", "A note for Studio. Say which slide."],
      [".read-card", "Studio's read of the slides."],
      ["#decision-bar", "Approve, or send it back with your notes."],
    ] },
    producing: { name: "Being edited", lines: [
      "On a phone it's under **More** in Review's stages.",
      "Drafts the editor is working on, including ones sent back.",
      "Each returns to To review when the new version renders.",
    ], tour: [["#tabs", "Review's stages."], ["#grid .tile", "A draft being edited."]] },
    done: { name: "Done", lines: [
      "Approved, posted and rejected drafts.",
      "Open one to schedule it, or **Back to review** to decide again.",
      "Bryan can archive or remove old ones here.",
    ], tour: [["#tabs", "Review's stages."], ["#tidy-bar", "Switch views and tidy up."], ["#grid .tile", "A finished draft."]] },
    library: { name: "Library", lines: [
      "Every raw clip, tagged and quality-checked.",
      "Open a clip to see its verdict and best moments.",
      "**Select** picks several clips to archive or free up space.",
    ], tour: [
      ["#tabs", "Review's stages."],
      ["[data-tidy=select]", "Select several clips at once."],
      ["#grid .tile", "A clip. Open it for its verdict and moments."],
    ] },
    clip: { name: "A clip", lines: [
      "One raw clip: its quality verdict, best moments and tags.",
      "Fix a wrong tag under **Tags**.",
      "Leave a note on how it should be used.",
    ], tour: [
      ["#player-frame", "The clip. Tap to play."],
      ["#note-body", "A note about this clip."],
    ] },
    other: { name: "This page", lines: ["Use the bar to move between Plan, Ideas, Review and More.", "Ask Studio below if you're stuck."], tour: [] },
  };
  const pageNow = () => {
    let p = {};
    try { p = host().page?.() || {}; } catch { /* not ready */ }
    const key = PAGES[p.key] ? p.key : "other";
    return { key, ...PAGES[key], route: p.route || location.hash || "#/" };
  };
  const rich = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  async function api(method, path, body) {
    const a = host().api;
    if (!a) throw new Error("Studio isn't reachable from here");
    return a(method, path, body);
  }

  // ── The panel ──────────────────────────────────────────────────────────────
  let root = null, card = null, returnTo = null, view = "";
  function frame() {
    if (root) return root;
    root = document.createElement("div");
    root.className = "help-root";
    root.hidden = true;
    root.innerHTML = `<div class="hp-card" role="dialog" aria-modal="true" aria-labelledby="hp-title"></div>`;
    document.body.appendChild(root);
    card = root.firstElementChild;
    root.addEventListener("click", onClick);
    root.addEventListener("submit", onSubmit);
    // While help is open, Escape closes it and Tab stays in it, wherever the focus went (a button that was
    // busy a moment ago, say). Capture phase: the page under it never sees these keys.
    document.addEventListener("keydown", (e) => {
      if (root.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); close(); return; }
      if (e.key === "Tab") {
        if (!card.contains(document.activeElement)) { e.preventDefault(); card.querySelector("button, textarea")?.focus(); return; }
        trap(e, card);
      }
      if (e.key === "Enter" && e.target.id === "hp-q" && !e.shiftKey) { e.preventDefault(); ask(); }
    }, true);
    return root;
  }
  function trap(e, box) {
    const f = [...box.querySelectorAll("a[href], button:not([disabled]), textarea, input")].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function show(html, focusSel) {
    frame();
    card.innerHTML = html;
    if (root.hidden) {
      root.hidden = false;
      document.body.classList.add("help-open");
    }
    (card.querySelector(focusSel || "#hp-title") || card).focus({ preventScroll: true });
  }
  function close(refocus = true) {
    if (!root || root.hidden) return;
    root.hidden = true;
    document.body.classList.remove("help-open");
    if (refocus) returnTo?.focus?.({ preventScroll: true });
  }
  const head = (title, back = false) => `<div class="hp-head">
      ${back ? `<button type="button" class="hp-back" data-hp="home" aria-label="Back to help">‹</button>` : ""}
      <h2 id="hp-title" tabindex="-1">${esc(title)}</h2>
      <button type="button" class="hp-close" data-hp="close" aria-label="Close help">&times;</button></div>`;

  function open(which, from) {
    returnTo = from || document.activeElement;
    if (which === "feedback") return openFeedback();
    if (which === "report") return openReport();
    if (which === "flag") return startFlag();
    view = "home";
    const p = pageNow();
    const tour = steps(p).length;
    show(`${head("Help")}
      <div class="hp-body">
        <section class="hp-sec" aria-labelledby="hp-page-h">
          <h3 class="hp-label" id="hp-page-h">This page: ${esc(p.name)}</h3>
          <ul class="hp-lines">${p.lines.map((l) => `<li>${rich(l)}</li>`).join("")}</ul>
          ${tour ? `<button type="button" class="btn" data-hp="tour">Show me</button>` : ""}
        </section>
        <section class="hp-sec" aria-labelledby="hp-ask-h">
          <h3 class="hp-label" id="hp-ask-h">Ask Studio</h3>
          <form class="hp-ask" id="hp-ask">
            <label class="sr-only" for="hp-q">Your question</label>
            <textarea id="hp-q" rows="2" maxlength="500" placeholder="How do I…? e.g. How do I send a draft back?"></textarea>
            <button type="submit" class="btn primary" id="hp-ask-go">Ask</button>
          </form>
          <div class="hp-answer" id="hp-answer" aria-live="polite"></div>
        </section>
        <section class="hp-sec hp-flag-sec">
          <button type="button" class="btn hp-flag-btn" data-hp="flag">Something not working?</button>
          <p class="hp-small">Tap the part that's wrong and say what happened. It goes to Studio's weekly check.</p>
        </section>
      </div>`);
  }

  async function onClick(e) {
    if (e.target === root) { close(); return; }
    const b = e.target.closest("[data-hp]");
    if (!b) return;
    const what = b.dataset.hp;
    if (what === "close") close();
    else if (what === "home") open("", returnTo);
    else if (what === "tour") { close(false); tour(); }
    else if (what === "flag") { close(false); startFlag(); }
    else if (what === "helped") helped(b);
    else if (what === "flag-send") sendFlag(b);
    else if (what === "flag-again") { close(false); startFlag(true); }
    else if (what === "fb-done") markFlag(b);
    else if (what === "rp-show") { const t = card.querySelector("#rp-token"); t.textContent = t.dataset.token; b.hidden = true; }
    else if (what === "rp-copy") copyToken(b);
    else if (what === "rp-new") card.querySelector("#rp-confirm").hidden = false;
    else if (what === "rp-no") card.querySelector("#rp-confirm").hidden = true;
    else if (what === "rp-yes") rotate(b);
  }
  function onSubmit(e) {
    e.preventDefault();
    if (e.target.id === "hp-ask") ask();
    if (e.target.id === "hp-flag-form") sendFlag(card.querySelector("[data-hp=flag-send]"));
  }

  // ── Ask Studio ─────────────────────────────────────────────────────────────
  let asking = false;
  async function ask() {
    const box = card.querySelector("#hp-q"), out = card.querySelector("#hp-answer"), go = card.querySelector("#hp-ask-go");
    const q = box.value.trim();
    if (asking) return;
    if (q.length < 3) { out.innerHTML = `<p class="hp-small">Type a question first, like "How do I add a note?"</p>`; box.focus(); return; }
    asking = true;
    go.disabled = true;
    out.innerHTML = `<p class="hp-small"><span class="spin"></span> Asking Studio…</p>`;
    const p = pageNow();
    try {
      const r = await api("POST", "/api/help/ask", { question: q, page: p.key, page_name: p.name, page_help: p.lines.map((l) => l.replace(/\*\*/g, "")), qa });
      if (r.unavailable) {
        out.innerHTML = `<div class="hp-note"><p>${esc(r.error || "Ask Studio can't answer right now.")}</p>
          <p class="hp-small">This page's help is above. Show me walks you through it.</p></div>`;
      } else {
        out.innerHTML = `<div class="hp-reply">${format(r.answer)}</div>
          <div class="hp-helped" data-q="${esc(r.id)}"><span class="hp-small">Did that help?</span>
            <button type="button" class="btn small" data-hp="helped" data-v="1">Yes</button>
            <button type="button" class="btn small" data-hp="helped" data-v="0">No</button></div>
          ${r.left <= 5 ? `<p class="hp-small">${esc(r.left)} question${r.left === 1 ? "" : "s"} left today.</p>` : ""}`;
      }
    } catch (err) {
      out.innerHTML = `<div class="hp-note"><p>${esc(/today/i.test(err.message) ? err.message : "Ask Studio can't answer right now.")}</p>
        <p class="hp-small">This page's help is above. Show me walks you through it.</p></div>`;
    } finally {
      asking = false;
      go.disabled = false;
    }
  }
  // Plain text in, a little shape out: numbered lines as a list, **bold** for control names.
  function format(text) {
    const lines = String(text || "").split(/\n+/).map((l) => l.trim()).filter(Boolean);
    let html = "", list = [];
    const flushList = () => { if (list.length) html += `<ol>${list.map((l) => `<li>${rich(l)}</li>`).join("")}</ol>`; list = []; };
    for (const l of lines) {
      const m = l.match(/^(\d+)[.)]\s+(.*)$/) || l.match(/^[-•]\s+(.*)$/);
      if (m) list.push(m[2] ?? m[1]);
      else { flushList(); html += `<p>${rich(l)}</p>`; }
    }
    flushList();
    return html;
  }
  async function helped(b) {
    const box = b.closest(".hp-helped");
    box.innerHTML = `<span class="hp-small">${b.dataset.v === "1" ? "Thanks." : "Thanks. Try Something not working? if it's a fault."}</span>`;
    api("POST", `/api/help/ask/${encodeURIComponent(box.dataset.q)}/helpful`, { helpful: b.dataset.v === "1" }).catch(() => {});
  }

  // ── Show me ────────────────────────────────────────────────────────────────
  const visible = (el) => !!el && el.getClientRects().length > 0 && !el.closest("[hidden], [inert]") && getComputedStyle(el).visibility !== "hidden";
  function steps(p) {
    const out = [];
    for (const [sel, text] of p.tour || []) {
      let el = null;
      try { el = [...document.querySelectorAll(sel)].find(visible) || null; } catch { /* a bad selector */ }
      if (el && !out.some((s) => s.el === el)) out.push({ el, text });
    }
    return out;
  }
  let walk = null;
  function tour() {
    const list = steps(pageNow());
    if (!list.length) { open("", returnTo); return; }
    walk = { list, i: 0, spot: document.createElement("div"), bubble: document.createElement("div"), block: document.createElement("div") };
    walk.block.className = "hp-block";
    walk.spot.className = "hp-spot";
    walk.bubble.className = "hp-bubble";
    walk.bubble.setAttribute("role", "dialog");
    walk.bubble.setAttribute("aria-modal", "true");
    walk.bubble.setAttribute("aria-label", "Show me");
    document.body.append(walk.block, walk.spot, walk.bubble);
    walk.bubble.addEventListener("click", (e) => {
      const b = e.target.closest("[data-walk]");
      if (!b) return;
      if (b.dataset.walk === "next") stepTo(walk.i + 1);
      else if (b.dataset.walk === "back") stepTo(walk.i - 1);
      else endTour();
    });
    walk.block.addEventListener("click", (e) => e.stopPropagation());
    walk.keys = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); endTour(); }
      else if (e.key === "Tab") trap(e, walk.bubble);
      else if (e.key === "ArrowRight") stepTo(walk.i + 1);
      else if (e.key === "ArrowLeft") stepTo(walk.i - 1);
    };
    document.addEventListener("keydown", walk.keys, true);
    walk.move = () => place();
    addEventListener("resize", walk.move);
    addEventListener("scroll", walk.move, true);
    stepTo(0);
  }
  function stepTo(i) {
    if (!walk) return;
    if (i >= walk.list.length) { endTour(); return; }
    walk.i = Math.max(0, i);
    const s = walk.list[walk.i];
    const last = walk.i === walk.list.length - 1;
    s.el.scrollIntoView({ block: "center", inline: "nearest" });
    walk.bubble.innerHTML = `<p class="hp-step">${esc(walk.i + 1)} of ${esc(walk.list.length)}</p><p class="hp-walk-text">${rich(s.text)}</p>
      <div class="hp-walk-btns">${walk.i ? `<button type="button" class="btn small ghost" data-walk="back">Back</button>` : ""}
        <span class="hp-gap"></span>
        ${last ? "" : `<button type="button" class="btn small ghost" data-walk="end">Stop</button>`}
        <button type="button" class="btn small primary" data-walk="${last ? "end" : "next"}">${last ? "Done" : "Next"}</button></div>`;
    place();
    walk.bubble.querySelector(`[data-walk="${last ? "end" : "next"}"]`).focus({ preventScroll: true });
  }
  function place() {
    if (!walk) return;
    const r = walk.list[walk.i].el.getBoundingClientRect();
    const pad = 6;
    Object.assign(walk.spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const b = walk.bubble;
    const w = Math.min(320, innerWidth - 24);
    b.style.width = `${w}px`;
    const left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 12));
    const h = b.offsetHeight;
    let top = r.bottom + 14;
    if (top + h > innerHeight - 12) top = r.top - h - 14;
    if (top < 12) top = Math.max(12, Math.min(innerHeight - h - 12, r.bottom + 14));
    Object.assign(b.style, { left: `${left}px`, top: `${top}px` });
  }
  function endTour() {
    if (!walk) return;
    document.removeEventListener("keydown", walk.keys, true);
    removeEventListener("resize", walk.move);
    removeEventListener("scroll", walk.move, true);
    walk.block.remove(); walk.spot.remove(); walk.bubble.remove();
    walk = null;
    returnTo?.focus?.({ preventScroll: true });
  }

  // ── Something not working? ─────────────────────────────────────────────────
  let pick = null, picked = null;
  const describe = (el) => window.StudioUsage?.describe?.(el) || (el.id ? `#${el.id.replace(/\d+/g, "n")}` : el.tagName.toLowerCase());
  function startFlag(again = false) {
    if (!again) picked = null;
    const bar = document.createElement("div");
    bar.className = "hp-pickbar";
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "Pick the part that isn't working");
    bar.innerHTML = `<p><strong>Tap the part that isn't working.</strong></p>
      <div class="hp-pick-btns"><button type="button" class="btn small" data-pick="skip">Skip</button>
      <button type="button" class="btn small ghost" data-pick="cancel">Cancel</button></div>`;
    const box = document.createElement("div");
    box.className = "hp-pickbox";
    box.hidden = true;
    document.body.append(bar, box);
    pick = { bar, box };
    const stop = (e) => { e.preventDefault(); e.stopImmediatePropagation(); };
    pick.down = (e) => { if (!bar.contains(e.target)) stop(e); };
    pick.click = (e) => {
      if (bar.contains(e.target)) {
        const b = e.target.closest("[data-pick]");
        if (b) { stop(e); endPick(); if (b.dataset.pick === "skip") flagForm(); else returnTo?.focus?.(); }
        return;
      }
      stop(e);
      const el = document.elementFromPoint(e.clientX, e.clientY) || e.target;
      picked = { control: describe(el), rect: el.getBoundingClientRect() };
      endPick();
      flagForm();
    };
    pick.move = (e) => {
      if (bar.contains(e.target)) { box.hidden = true; return; }
      const el = document.elementFromPoint(e.clientX, e.clientY);
      if (!el) return;
      const r = (el.closest("button, a, input, textarea, select, [role=button], summary, label, .card, .tile") || el).getBoundingClientRect();
      Object.assign(box.style, { left: `${r.left - 3}px`, top: `${r.top - 3}px`, width: `${r.width + 6}px`, height: `${r.height + 6}px` });
      box.hidden = false;
    };
    pick.keys = (e) => { if (e.key === "Escape") { stop(e); endPick(); returnTo?.focus?.(); } };
    for (const ev of ["pointerdown", "mousedown"]) document.addEventListener(ev, pick.down, { capture: true, passive: false });
    document.addEventListener("click", pick.click, true);
    document.addEventListener("pointermove", pick.move, true);
    document.addEventListener("keydown", pick.keys, true);
    document.body.classList.add("hp-picking");
    bar.querySelector("[data-pick=skip]").focus({ preventScroll: true });
  }
  function endPick() {
    if (!pick) return;
    for (const ev of ["pointerdown", "mousedown"]) document.removeEventListener(ev, pick.down, { capture: true });
    document.removeEventListener("click", pick.click, true);
    document.removeEventListener("pointermove", pick.move, true);
    document.removeEventListener("keydown", pick.keys, true);
    pick.bar.remove(); pick.box.remove();
    pick = null;
    document.body.classList.remove("hp-picking");
  }
  function flagForm() {
    view = "flag";
    show(`${head("Something not working?", true)}
      <form class="hp-body" id="hp-flag-form">
        <p class="hp-picked">${picked ? "Part picked." : "No part picked."}
          <button type="button" class="btn-link" data-hp="flag-again">${picked ? "Pick again" : "Pick a part"}</button></p>
        <label class="hp-label" for="hp-words">What happened? (optional)</label>
        <textarea id="hp-words" rows="3" maxlength="1000" placeholder="e.g. Pressed Approve and nothing happened"></textarea>
        <p class="error-text" id="hp-flag-error" role="alert" hidden></p>
        <div class="hp-row"><button type="button" class="btn ghost" data-hp="close">Cancel</button>
          <button type="submit" class="btn primary" data-hp="flag-send">Send</button></div>
        <p class="hp-small">Sends the page, the part you tapped and your words. No screenshot.</p>
      </form>`, "#hp-words");
  }
  async function sendFlag(b) {
    const words = card.querySelector("#hp-words").value.trim();
    const err = card.querySelector("#hp-flag-error");
    if (!words && !picked) { err.textContent = "Pick a part or say what happened."; err.hidden = false; return; }
    b.disabled = true;
    const p = pageNow();
    try {
      await api("POST", "/api/feedback", { page: p.key, route: p.route, control: picked?.control || "", words,
        viewport: `${innerWidth}x${innerHeight}`, version: host().version?.() || "", qa });
      show(`${head("Thanks")}<div class="hp-body"><p>Sent. Studio's weekly check picks it up.</p>
        <div class="hp-row"><button type="button" class="btn primary" data-hp="close">Done</button></div></div>`, "[data-hp=close].btn");
      picked = null;
    } catch (e) {
      b.disabled = false;
      err.textContent = `Couldn't send it: ${e.message}`;
      err.hidden = false;
    }
  }

  // ── The owner's tools: Feedback and Weekly report access ───────────────────
  const when = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(+d) ? "" : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  };
  async function openFeedback() {
    view = "feedback";
    show(`${head("Feedback")}<div class="hp-body"><p class="hp-small"><span class="spin"></span> Loading…</p></div>`);
    let r;
    try { r = await api("GET", "/api/feedback"); } catch (e) {
      card.querySelector(".hp-body").innerHTML = `<p class="error-text">Couldn't load it: ${esc(e.message)}</p>`;
      return;
    }
    const rows = r.flags || [];
    card.querySelector(".hp-body").innerHTML = rows.length ? `<p class="hp-small">${esc(r.open)} open · newest first. What people flagged with Something not working?</p>
      <ul class="fb-list">${rows.map((f) => `<li class="fb-item${f.done_at ? " done" : ""}">
        <div class="fb-top"><strong>${esc(PAGES[f.page]?.name || f.page)}</strong><span class="hp-small">${esc(f.user_name)} · ${esc(when(f.at))}${f.qa ? " · test walk" : ""}</span></div>
        ${f.words ? `<p class="fb-words">${esc(f.words)}</p>` : `<p class="hp-small">No words, just the part.</p>`}
        <p class="hp-small mono">${esc(f.control || "No part picked")}${f.viewport ? ` · ${esc(f.viewport)}` : ""}${f.version ? ` · ${esc(f.version)}` : ""}</p>
        <button type="button" class="btn small${f.done_at ? " ghost" : ""}" data-hp="fb-done" data-id="${esc(f.id)}" data-done="${f.done_at ? "1" : ""}">${f.done_at ? "Done · undo" : "Mark done"}</button>
      </li>`).join("")}</ul>` : `<p>Nothing flagged yet. People flag problems from Help > Something not working?</p>`;
  }
  async function markFlag(b) {
    const done = !b.dataset.done;
    if (b.getAttribute("aria-busy") === "true") return;
    b.setAttribute("aria-busy", "true");
    try {
      await api("POST", `/api/feedback/${encodeURIComponent(b.dataset.id)}/done`, { done });
      b.dataset.done = done ? "1" : "";
      b.textContent = done ? "Done · undo" : "Mark done";
      b.classList.toggle("ghost", done);
      b.closest(".fb-item").classList.toggle("done", done);
    } catch { /* stays as it was */ }
    b.removeAttribute("aria-busy");
  }
  async function openReport() {
    view = "report";
    show(`${head("Weekly report access")}<div class="hp-body"><p class="hp-small"><span class="spin"></span> Loading…</p></div>`);
    let r;
    try { r = await api("GET", "/api/report/token"); } catch (e) {
      card.querySelector(".hp-body").innerHTML = `<p class="error-text">Couldn't load it: ${esc(e.message)}</p>`;
      return;
    }
    reportBody(r);
  }
  function reportBody(r) {
    card.querySelector(".hp-body").innerHTML = `<p>This key lets Studio's own weekly review read the usage report (no content, just what was used). Keep it private.</p>
      <p class="hp-label">Address</p><p class="mono hp-wrap">${esc(r.url || "")}</p>
      <p class="hp-label">Key</p><p class="mono hp-wrap" id="rp-token" data-token="${esc(r.token)}">${esc(String(r.token).slice(0, 4))}••••••••••••</p>
      <div class="hp-row"><button type="button" class="btn" data-hp="rp-show">Show</button>
        <button type="button" class="btn" data-hp="rp-copy">Copy</button>
        <button type="button" class="btn ghost danger" data-hp="rp-new">New key</button></div>
      <div class="hp-confirm" id="rp-confirm" hidden><p>The old key stops working at once. Make a new one?</p>
        <div class="hp-row"><button type="button" class="btn ghost" data-hp="rp-no">Cancel</button>
        <button type="button" class="btn danger" data-hp="rp-yes">Make a new key</button></div></div>`;
  }
  async function copyToken(b) {
    const t = card.querySelector("#rp-token").dataset.token;
    try { await navigator.clipboard.writeText(t); b.textContent = "Copied"; } catch {
      const tok = card.querySelector("#rp-token");
      tok.textContent = t;
      getSelection().selectAllChildren(tok);
      b.textContent = "Selected: copy it";
    }
    setTimeout(() => { b.textContent = "Copy"; }, 2500);
  }
  async function rotate(b) {
    b.disabled = true;
    try {
      const r = await api("POST", "/api/report/token/rotate", {});
      const url = card.querySelector(".hp-wrap")?.textContent || "";
      reportBody({ ...r, url });
      card.querySelector("#rp-token").textContent = r.token;
      card.querySelector("[data-hp=rp-show]").hidden = true;
    } catch (e) { b.disabled = false; }
  }

  // Phone or computer changed under an open walk: put the bubble back where it belongs.
  PHONE.addEventListener("change", () => place());
  return { open, close, tour, pages: PAGES, pageNow, isOpen: () => !!root && !root.hidden, walking: () => !!walk, picking: () => !!pick };
})();
