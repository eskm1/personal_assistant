// The status marks, the same everywhere in Studio (Bryan, via the content lead, 2026-10-27): Posted, Scheduled
// (with its date), Approved, Draft in Review, Idea, and Needs a fact from Bryan. One small round icon in a tile's
// corner or beside a title, its words on hover, focus and for screen readers, and a key (legendHTML) wherever
// marks show. Plan (the planner's Grid, Plan's home, Ideas, the Posting calendar) and Review (served as
// /static/marks.js) both load this file, so a mark looks and reads the same in both halves.
//
//   StudioMarks.of({production, post, format, slides_count, post_on})   -> {key, label, text, bad?}
//       production: ideas.production ({status, ...}) or a draft's status as a string; post: {state,
//       scheduled_at, posted_at} (a Studio post or a post on the account); slides_count for a carousel idea.
//   StudioMarks.html(mark, {small, title})   the round icon (role img, its words as the label)
//   StudioMarks.fact(text) / StudioMarks.needsFact(idea)   the Needs-a-fact mark, and whether an idea needs one
//   StudioMarks.legendHTML()                  the key: every mark and what it means
"use strict";
window.StudioMarks = (() => {
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Stroke icons (24 box, drawn at 12-14 px inside the round mark).
  const ICONS = {
    posted: '<path d="M5 12.5l4.2 4.2L19 7"/>',
    scheduled: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    approved: '<path d="M12 3l2.4 1.8 3-.1.9 2.9 2.4 1.8-1 2.8 1 2.8-2.4 1.8-.9 2.9-3-.1L12 21l-2.4-1.8-3 .1-.9-2.9L3.3 14.6l1-2.8-1-2.8 2.4-1.8.9-2.9 3 .1z"/><path d="M8.6 12.2l2.3 2.3 4.6-4.6"/>',
    review: '<path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    idea: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.8.6 1.1 1.4 1.1 2.2h5c0-.8.3-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
    fact: '<path d="M9.2 9.3a2.9 2.9 0 1 1 4.1 2.6c-.8.4-1.3 1-1.3 1.9v.6M12 17.3v.2"/>',
  };
  let owner = "Bryan";
  const LABEL = {
    posted: "Posted", scheduled: "Scheduled", approved: "Approved", review: "Draft in Review", idea: "Idea",
    fact: () => `Needs a fact from ${owner}`,
  };
  const label = (k) => (typeof LABEL[k] === "function" ? LABEL[k]() : LABEL[k]);
  // What each means, for the key.
  const MEANS = {
    posted: "On the account now.",
    scheduled: "Approved and set to go out on Instagram at its time.",
    approved: "Approved, not scheduled yet.",
    review: "A draft is made, or being made: it waits in Review.",
    idea: "Planned, not made yet (no slides yet, or footage still to film).",
    fact: () => `Something to check or fill in before it goes out: ${owner} knows it.`,
  };

  // Singapore days and times (Studio's posting runs on Singapore time; no summer time).
  const SG = 8 * 3600e3;
  const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function day(iso) {
    // A date ("2026-11-02") is that day; a timestamp is its day in Singapore.
    const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const d = m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : new Date(Date.parse(iso || "") + SG);
    return Number.isNaN(d.getTime()) ? "" : `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
  }
  function time(iso) {
    const t = Date.parse(iso || "");
    if (!t) return "";
    const d = new Date(t + SG);
    const h = d.getUTCHours(), mi = d.getUTCMinutes();
    return `${h % 12 || 12}${mi ? `:${String(mi).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}`;
  }

  const IN_EDIT = new Set(["planned", "producing", "revising"]);
  function of(x = {}) {
    const prod = typeof x.production === "string" ? x.production : x.production?.status || "";
    const post = x.post || null;
    if (post?.state === "posted" || prod === "posted") {
      const at = post?.posted_at;
      return { key: "posted", label: label("posted"), text: at ? `Posted ${day(at)}` : "Posted" };
    }
    if (post && ["scheduled", "publishing"].includes(post.state)) {
      const at = post.scheduled_at;
      return { key: "scheduled", label: label("scheduled"),
        text: post.state === "publishing" ? "Going out on Instagram now" : `Scheduled ${day(at)}${time(at) ? `, ${time(at)}` : ""}` };
    }
    if (post?.state === "failed") return { key: "scheduled", label: label("scheduled"), bad: true, text: "Scheduled, but it couldn't post" };
    if (prod === "approved") return { key: "approved", label: label("approved"), text: "Approved, not scheduled yet" };
    if (prod === "ready") return { key: "review", label: label("review"), text: "Draft in Review" };
    if (IN_EDIT.has(prod)) return { key: "review", label: label("review"), text: prod === "revising" ? "Draft: Studio is revising it" : "Draft: Studio is making it" };
    if (prod === "failed") return { key: "review", label: label("review"), bad: true, text: "Draft: the edit failed" };
    const carousel = x.format === "carousel";
    const n = Number(x.slides_count) || 0;
    return { key: "idea", label: label("idea"),
      text: carousel ? (n ? `Idea: ${n} slide${n === 1 ? "" : "s"}, not made yet` : "Idea (no slides yet)") : "Idea (not made yet)" };
  }

  function svg(key) {
    return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[key] || ""}</svg>`;
  }
  function html(m, opts = {}) {
    if (!m) return "";
    const words = m.text || m.label;
    return `<span class="sm-mark sm-${esc(m.key)}${m.bad ? " sm-bad" : ""}${opts.small ? " sm-small" : ""}" role="img" aria-label="${esc(words)}"${opts.title === false ? "" : ` title="${esc(words)}"`}>${svg(m.key)}</span>`;
  }
  // Needs a fact from Bryan: the idea's own flag (ideas.needs_fact, with what's missing), or [check] in its words.
  function needsFact(i = {}) {
    if (i.needs_fact != null) return true;
    if (i.check === true) return true;
    const words = [i.title, i.hook, i.caption, i.cta, ...(Array.isArray(i.slides) ? i.slides.flatMap((s) => [s?.heading, s?.body]) : [])];
    return words.some((w) => /\[check\]/i.test(String(w || "")));
  }
  function fact(text = "", opts = {}) {
    const words = text ? `${label("fact")}: ${text}` : label("fact");
    return html({ key: "fact", label: label("fact"), text: words }, opts);
  }
  function legendHTML(opts = {}) {
    const keys = ["posted", "scheduled", "approved", "review", "idea", "fact"];
    return `<details class="sm-legend${opts.cls ? ` ${esc(opts.cls)}` : ""}"${opts.open ? " open" : ""}>
      <summary><span class="sm-legend-dots" aria-hidden="true">${keys.slice(0, 3).map((k) => `<span class="sm-mark sm-${k} sm-small">${svg(k)}</span>`).join("")}</span>Key</summary>
      <ul class="sm-legend-list">${keys.map((k) => `<li><span class="sm-mark sm-${k}" aria-hidden="true">${svg(k)}</span>
        <span><strong>${esc(label(k))}</strong> <span class="sm-legend-means">${esc(typeof MEANS[k] === "function" ? MEANS[k]() : MEANS[k])}</span></span></li>`).join("")}</ul>
    </details>`;
  }
  return { of, html, fact, needsFact, legendHTML, day, time, label, setOwner: (name) => { if (name) owner = String(name).split(" ")[0]; } };
})();
