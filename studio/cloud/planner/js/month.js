// The content plan: this month's numbers (Plan's home shows them as post slots, home.js), a page
// per kind of post (#/month/<kind>: the posts that count this month and the ones waiting for a
// decision), and the Content plan page (#/content-plan: the monthly targets per kind and the
// offers; the owner edits, the team reads). The counting is the database's content_month()
// (CONTRACT "Content plan"). Suggested topics and #/questions, the Client questions page, live in
// topics.js.
import {
  $, esc, sb, must, toast, plural, isOwner, ownerName, missingTable, shortDate, PRODUCTION,
  POST_TYPES, POST_TYPE, KIND_INFO, kindDot, DECISION, typingIn, sentence, keepDrafts,
} from "./core.js";
import { topics, loadSuggestions, loadQuestions, questionsPageHTML, questionsEvent } from "./topics.js";

const root = () => $("#month");
const MAX_OFFERS = 10;

export const month = {
  data: null, plan: null, missing: false, loaded: false, pageSig: "",
  route: null, ideas: null, projects: null, draft: null, dirty: false, error: "",
};
export const monthOpen = () => !root().hidden;

// #/month/<kind>, #/content-plan, #/questions, or null.
export function monthRoute(hash) {
  let m;
  if (hash === "#/content-plan") return { page: "plan" };
  if (hash === "#/questions") return { page: "questions" };
  if ((m = hash.match(/^#\/month\/(knowhow|ideas|onsite|finished|offer)$/))) return { page: "kind", kind: m[1] };
  return null;
}

// This month's counts and the plan. Missing until the database update: the strip stays away.
export async function loadMonth() {
  try {
    const [data, plan] = await Promise.all([
      sb.rpc("content_month").then(must),
      sb.from("content_plan").select("*").eq("id", 1).maybeSingle().then(must),
      loadSuggestions().catch(() => {}),   // at the same time (it copes with the tables missing itself)
    ]);
    Object.assign(month, { data, plan, missing: false, loaded: true });
  } catch (err) {
    if (!missingTable(err)) throw err;
    Object.assign(month, { data: null, plan: null, missing: true, loaded: true });
  }
}

const monthName = (m) => {
  const [y, mo] = String(m || "").split("-").map(Number);
  return y && mo ? new Date(y, mo - 1, 1).toLocaleDateString("en-GB", { month: "long" }) : "This month";
};
const kindsOf = (d) => (d && typeof d.kinds === "object" && d.kinds) || {};
const target = (c) => Number(c?.target) || 0;
const planned = (c) => Number(c?.planned) || 0;
const shortOf = (c) => Math.max(target(c) - planned(c), 0);
// "2 of 4 · 2 short"
function countHTML(c) {
  const short = shortOf(c);
  return `${esc(planned(c))} of ${esc(target(c))}${short ? ` · <span class="gap">${esc(short)} short</span>` : ""}`;
}
// A meter: the kind's colour for what's planned, grey for what's still missing (widths by script: no inline styles).
function meterBar(c, k) {
  const got = planned(c), want = target(c);
  if (!got && !want) return `<span class="pbar thin meter-bar none"></span>`;
  return `<span class="pbar thin meter-bar">${got ? `<i class="k ${k}" data-f="${Math.min(got, Math.max(want, got))}"></i>` : ""}${got < want ? `<i class="needed" data-f="${want - got}"></i>` : ""}</span>`;
}

// ── Pages ───────────────────────────────────────────────────────────────────

export async function showMonth(route) {
  const moved = !month.route || month.route.page !== route.page || month.route.kind !== route.kind;
  month.route = route;
  root().hidden = false;
  if (moved) {
    month.pageSig = "";
    month.ideas = null;
    if (!month.dirty) month.draft = null;
    window.scrollTo(0, 0);
    root().innerHTML = `<div class="month-wrap"><p class="muted"><span class="spin"></span> Loading…</p></div>`;
  }
  try {
    await loadMonthPage();
  } catch (err) {
    root().innerHTML = `<div class="month-wrap">${backHTML()}<p class="error-text">Couldn't load this: ${esc(err.message)}</p></div>`;
  }
  if (moved) root().querySelector("h1")?.focus({ preventScroll: true });
}

export function hideMonth() {
  if (root().hidden) return;
  root().hidden = true;
  root().innerHTML = "";
  month.route = null;
  month.pageSig = "";
  month.dirty = false;
  month.draft = null;
}

export async function loadMonthPage() {
  const route = month.route;
  if (!route) return;
  await loadMonth();
  if (route.page === "questions" && !month.missing) {
    try { await loadQuestions(); } catch (err) { if (!missingTable(err)) throw err; topics.questions = null; }
  }
  if (route.page === "kind" && !month.missing) {
    const [ideas, projects] = await Promise.all([
      sb.from("ideas").select("id,title,project_id,decision,status,production,post_type,updated_at")
        .eq("post_type", route.kind).neq("status", "dismissed").order("updated_at", { ascending: false }).limit(300).then(must),
      sb.from("projects").select("id,name,status").then(must),
    ]);
    if (month.route !== route) return;
    month.ideas = ideas;
    month.projects = new Map(projects.map((p) => [p.id, p]));
  }
  if (month.route !== route) return;
  renderPage();
}

const backHTML = () => `<div class="row"><a class="btn" href="#/" aria-label="Back to the plan">‹ Plan</a></div>`;
const updateNote = () => `<p><strong>The content plan needs a one-time update of the online database.</strong></p>
  <p class="muted">${esc(ownerName())} runs <code>cloud_setup.py --update</code> on the laptop once; then this page works.</p>`;

function renderPage() {
  const el = root();
  if (!month.route) return;
  // Someone typing, or the owner's unsaved targets and offers: don't redraw under them.
  if (month.dirty || typingIn(el)) { month.pending = true; return; }
  const sig = JSON.stringify([month.route, month.data, month.plan, month.ideas,
    month.route.page === "questions" ? [topics.questions, [...topics.ideas.values()], topics.editing] : 0]);
  if (sig === month.pageSig) return;
  month.pageSig = sig;
  month.pending = false;
  const title = month.route.page === "questions" ? "Client questions" : "Content plan";
  if (month.missing || (month.route.page === "questions" && !topics.questions)) {
    el.innerHTML = `<div class="month-wrap">${backHTML()}<h1 tabindex="-1">${title}</h1>${updateNote()}</div>`;
    return;
  }
  if (month.route.page === "questions") { keepDrafts(el, () => { el.innerHTML = questionsPageHTML(backHTML()); }); return; }
  el.innerHTML = month.route.page === "kind" ? kindPageHTML(month.route.kind) : planPageHTML();
  const tab = el.querySelector(".kind-tab.on");
  if (tab) tab.parentElement.scrollLeft = tab.offsetLeft - 16;
}

// #/month/<kind>: the posts of one kind that count this month, and the ones waiting for a decision.
function kindPageHTML(k) {
  const d = month.data || {};
  const c = kindsOf(d)[k] || {};
  const info = KIND_INFO[k];
  const counted = new Set(Array.isArray(c.ids) ? c.ids : []);
  const ideas = month.ideas || [];
  const here = ideas.filter((i) => counted.has(i.id));
  const ready = (i) => ["approved", "posted"].includes(i.production?.status);
  here.sort((a, b) => (ready(b) - ready(a)) || (Date.parse(b.updated_at) - Date.parse(a.updated_at)));
  const active = (i) => month.projects?.get(i.project_id)?.status !== "archived";
  const waiting = ideas.filter((i) => !counted.has(i.id) && i.status === "new" && i.decision !== "go" && !i.production && active(i));
  const tabs = POST_TYPES.map((x) => `<a class="kind-tab${x === k ? " on" : ""}" href="#/month/${x}" ${x === k ? `aria-current="page"` : ""}>${kindDot(x)}${esc(POST_TYPE[x])}</a>`).join("");
  const short = shortOf(c);
  return `<div class="month-wrap">
    ${backHTML()}
    <nav class="kind-tabs" aria-label="Kinds of post">${tabs}</nav>
    <h1 tabindex="-1" class="kind-title">${kindDot(k)}${esc(POST_TYPE[k])} <span class="muted">· ${esc(monthName(d.month))}</span></h1>
    <p class="kind-job"><span class="stage">${esc(info.stage)}</span> ${esc(info.job)}. <span class="muted">Ask: ${esc(sentence(info.ask))}</span></p>
    <div class="kind-count">${meterBar(c, k)}<p><strong>${esc(planned(c))} of ${esc(target(c))}</strong> this month${short ? ` · <span class="gap">${esc(short)} short</span>` : planned(c) > target(c) ? " · more than the target" : target(c) ? " · on target" : ""}</p></div>
    <section class="kind-sec" aria-labelledby="kind-now"><h2 id="kind-now">Counting this month <span class="count">${esc(here.length)}</span></h2>
      ${here.length ? `<ul class="kind-list">${here.map(rowHTML).join("")}</ul>`
        : `<p class="muted">Nothing yet. A post counts once it's Go (and until it's approved), or when it's approved this month.</p>`}</section>
    <section class="kind-sec" aria-labelledby="kind-wait"><h2 id="kind-wait">${isOwner() ? "Waiting for your call" : "Waiting for Go or Park"} <span class="count">${esc(waiting.length)}</span></h2>
      ${waiting.length ? `<p class="muted small">${short ? `Go on one of these to fill the gap.` : "Ideas of this kind that aren't Go yet."}</p><ul class="kind-list">${waiting.map(rowHTML).join("")}</ul>`
        : `<p class="muted">No other ${esc(POST_TYPE[k])} ideas on the board.</p>`}</section>
  </div>`;
}

function rowHTML(i) {
  const p = month.projects?.get(i.project_id);
  const prod = i.production && typeof i.production === "object" ? i.production : null;
  let state;
  if (prod && ["approved", "posted"].includes(prod.status)) state = `<span class="chip ok">Ready${prod.updated_at ? ` · approved ${esc(shortDate(prod.updated_at))}` : ""}</span>`;
  else if (prod?.status) state = `<span class="chip producing">${esc(PRODUCTION[prod.status] || prod.status)}</span>`;
  else if (i.status === "made") state = `<span class="chip producing">In the edit</span>`;
  else state = `<span class="chip ${esc(i.decision)}">${esc(DECISION[i.decision] || i.decision)}</span>`;
  return `<li><a class="kind-row" href="#/idea/${esc(i.id)}">
    <span class="kind-row-title">${esc(i.title)}</span>
    <span class="kind-row-meta">${state}<span class="muted small">${esc(p?.name || "")}</span></span></a></li>`;
}

// #/content-plan: the targets per kind and the offers.
const clampInt = (v, lo, hi) => Math.min(hi, Math.max(lo, Math.round(Number(v) || 0)));
function draftFromPlan() {
  const p = month.plan || {};
  const mix = {};
  for (const k of POST_TYPES) mix[k] = clampInt(p.mix?.[k], 0, 60);
  const offers = (Array.isArray(p.offers) ? p.offers : []).map((o) => ({ name: o?.name || "", details: o?.details || "", ask: o?.ask || "" }));
  return { mix, offers };
}
const totalOf = (mix) => POST_TYPES.reduce((n, k) => n + (Number(mix[k]) || 0), 0);
const perWeek = (total) => Math.round((total * 12 / 52) * 10) / 10;
function totalLine(mix) {
  const t = totalOf(mix);
  return `<strong>${esc(t)} a month</strong>, about ${esc(perWeek(t))} a week`;
}

function planPageHTML() {
  const owner = isOwner();
  if (!month.draft || !month.dirty) month.draft = draftFromPlan();
  const { mix, offers } = month.draft;
  const p = month.plan || {};
  const d = month.data || {};
  const kinds = kindsOf(d);
  const mixRows = POST_TYPES.map((k) => `<li class="mix-row">
      <span class="mix-name">${kindDot(k)}<span><strong>${esc(POST_TYPE[k])}</strong><span class="muted small"> ${esc(KIND_INFO[k].stage)}</span></span></span>
      ${owner ? `<label class="mix-num"><span class="sr-only">${esc(POST_TYPE[k])} a month</span><input type="number" inputmode="numeric" min="0" max="60" step="1" data-mix="${k}" value="${esc(mix[k])}"></label>`
        : `<span class="mix-num"><strong>${esc(mix[k])}</strong></span>`}
      <span class="mix-now muted small">${esc(monthName(d.month))}: ${countHTML(kinds[k])}</span>
    </li>`).join("");
  const offerRows = offers.map((o, n) => owner ? `<li class="offer-row" data-offer="${n}">
      <div class="offer-fields">
        <label>Offer<input data-offer-field="name" maxlength="80" value="${esc(o.name)}" placeholder="e.g. Free consultation"></label>
        <label><span>What's included <span class="muted">(optional)</span></span><textarea data-offer-field="details" rows="2" maxlength="500" placeholder="Only what's true and agreed: no prices unless you write them here">${esc(o.details)}</textarea></label>
        <label><span>The ask <span class="muted">(how a post words it)</span></span><input data-offer-field="ask" maxlength="200" value="${esc(o.ask)}" placeholder="e.g. Book a free consultation"></label>
      </div>
      <button type="button" class="icon-btn danger" data-offer-remove="${n}" aria-label="Remove the offer ${esc(o.name || n + 1)}" title="Remove">×</button>
    </li>` : `<li class="offer-row read"><strong>${esc(o.name)}</strong>${o.details ? `<p>${esc(o.details)}</p>` : ""}${o.ask ? `<p class="muted small">Ask: “${esc(o.ask)}”</p>` : ""}</li>`).join("");
  const kindsRef = POST_TYPES.map((k) => `<li>${kindDot(k)}<div><strong>${esc(POST_TYPE[k])}</strong> <span class="muted small">${esc(KIND_INFO[k].stage)}</span>
      <p>${esc(KIND_INFO[k].job)}. <span class="muted">Ask: ${esc(sentence(KIND_INFO[k].ask))}</span></p></div></li>`).join("");
  return `<div class="month-wrap cp">
    ${backHTML()}
    <h1 tabindex="-1">Content plan</h1>
    <p class="muted">Every post has a job, its kind. Studio keeps the month balanced against these targets: Claude leans toward the kinds that are short when it suggests or plans posts.${owner ? "" : ` ${esc(ownerName())} sets the targets and offers.`}</p>
    <section class="cp-sec" aria-labelledby="cp-mix"><h2 id="cp-mix">Posts a month</h2>
      <ul class="mix-list">${mixRows}</ul>
      <p class="mix-total" id="mix-total">${totalLine(mix)}</p>
    </section>
    <section class="cp-sec" aria-labelledby="cp-offers"><h2 id="cp-offers">Offers</h2>
      <p class="muted small">What people can take you up on. Work with us posts, and every call to action, use only these: Studio never makes up a price, discount or promotion.</p>
      ${offers.length ? `<ul class="offer-list">${offerRows}</ul>` : `<p class="muted">No offers yet: posts then ask people to follow, save or DM, and offer nothing.</p>`}
      ${owner && offers.length < MAX_OFFERS ? `<div class="row"><button type="button" class="btn" data-offer-add>+ Add an offer</button></div>` : ""}
    </section>
    ${owner ? `<div class="cp-save" id="cp-save">
      <p class="error-text" id="cp-error" role="alert" hidden></p>
      <span class="muted small" id="cp-state">${month.dirty ? "Unsaved changes" : p.updated_at ? `Saved ${esc(shortDate(p.updated_at))}` : ""}</span>
      <button type="button" class="btn" data-cp-cancel ${month.dirty ? "" : "disabled"}>Undo changes</button>
      <button type="button" class="btn primary" data-cp-save ${month.dirty ? "" : "disabled"}>Save</button>
    </div>` : ""}
    <section class="cp-sec" aria-labelledby="cp-topics"><h2 id="cp-topics">Suggested topics</h2>
      <p class="muted small">Once a week, and when a kind falls behind, Studio suggests posts for the gaps: from what you said on site, the questions clients ask, and what's coming up in the shoot calendar. They wait under This month for ${owner ? "your Go, Park or Dismiss" : `a Go or Park (yours or ${esc(ownerName())}'s)`}.</p>
      <div class="row"><a class="btn" href="#/questions">Client questions</a></div>
    </section>
    <section class="cp-sec" aria-labelledby="cp-kinds"><h2 id="cp-kinds">The five kinds</h2>
      <ul class="kinds-ref">${kindsRef}</ul>
      <p class="muted small">Get found reaches people who don't know you yet, Build trust shows you do it properly, Get enquiries makes the next step easy.</p>
    </section>
  </div>`;
}

function markDirty() {
  month.dirty = true;
  const s = $("#cp-state");
  if (s) s.textContent = "Unsaved changes";
  for (const b of root().querySelectorAll("[data-cp-save], [data-cp-cancel]")) b.disabled = false;
}
// Read what's in the boxes into the draft (before redrawing the list).
function readDraft() {
  const dr = month.draft;
  if (!dr) return;
  for (const el of root().querySelectorAll("[data-mix]")) dr.mix[el.dataset.mix] = clampInt(el.value, 0, 60);
  for (const row of root().querySelectorAll("[data-offer]")) {
    const o = dr.offers[Number(row.dataset.offer)];
    if (!o) continue;
    for (const f of row.querySelectorAll("[data-offer-field]")) o[f.dataset.offerField] = f.value;
  }
}
function redrawPlan() {
  month.pageSig = "";
  const keepDirty = month.dirty;
  root().innerHTML = planPageHTML();
  month.dirty = keepDirty;
  if (keepDirty) markDirty();
}

function problem(dr) {
  for (const o of dr.offers) {
    if (!o.name.trim()) return "Give every offer a name (or remove the empty one).";
    if (o.name.trim().length > 80) return "Keep offer names under 80 characters.";
  }
  return "";
}

async function save() {
  readDraft();
  const dr = month.draft;
  const err = problem(dr);
  const box = $("#cp-error");
  if (err) { box.textContent = err; box.hidden = false; return; }
  box.hidden = true;
  const offers = dr.offers.map((o) => ({ name: o.name.trim(), details: o.details.trim(), ask: o.ask.trim() }));
  const total = totalOf(dr.mix);
  const rows = must(await sb.from("content_plan").update({ mix: dr.mix, offers, posts_per_week: Math.round(total * 12 / 52) })
    .eq("id", 1).select("id"));
  if (!rows?.length) throw new Error(`only ${ownerName()} can change the content plan`);
  month.dirty = false;
  month.draft = null;
  toast("Content plan saved.");
  await loadMonthPage();
}

root().addEventListener("input", (e) => {
  if (e.target.matches("[data-mix]")) {
    readDraft();
    $("#mix-total").innerHTML = totalLine(month.draft.mix);
    markDirty();
  } else if (e.target.matches("[data-offer-field]")) markDirty();
});
// The Client questions page: add, edit, delete, suggest again.
async function reloadQuestions() {
  await loadQuestions();
  if (month.route?.page !== "questions") return;
  const el = root();
  keepDrafts(el, () => { el.innerHTML = questionsPageHTML(backHTML()); });
  month.pageSig = "";
}
for (const type of ["click", "submit"]) {
  root().addEventListener(type, async (e) => {
    if (month.route?.page !== "questions") return;
    try {
      if (await questionsEvent(e, root(), reloadQuestions)) e.stopImmediatePropagation();
    } catch (err) {
      toast(`Couldn't save: ${err.message}`);
    }
  });
}
root().addEventListener("click", async (e) => {
  if (month.route?.page === "questions") return;
  const t = e.target.closest("button");
  if (!t) return;
  try {
    if (t.matches("[data-offer-add]")) {
      readDraft();
      month.draft.offers.push({ name: "", details: "", ask: "" });
      month.dirty = true;
      redrawPlan();
      root().querySelector(`[data-offer="${month.draft.offers.length - 1}"] [data-offer-field="name"]`)?.focus();
    } else if (t.dataset.offerRemove != null) {
      readDraft();
      month.draft.offers.splice(Number(t.dataset.offerRemove), 1);
      month.dirty = true;
      redrawPlan();
    } else if (t.matches("[data-cp-cancel]")) {
      month.dirty = false;
      month.draft = null;
      redrawPlan();
    } else if (t.matches("[data-cp-save]")) {
      t.disabled = true;
      await save();
    }
  } catch (err) {
    t.disabled = false;
    toast(`Couldn't save: ${err.message}`);
  }
});
// Changes that arrived while someone was typing show once they stop.
root().addEventListener("focusout", () => setTimeout(() => { if (month.pending && !month.dirty) renderPage(); }, 0));
