// Suggested topics (CONTRACT "Suggested topics"): the open suggestions for the month's gaps (Plan's
// home lists them with Go / Not now / Dismiss, home.js), "Suggest topics", and the Client questions
// page (#/questions: a fast add box and the list, with the posts they became). The Studio machine
// writes the suggestions (topics.py); the team keeps the questions.
import {
  esc, sb, must, toast, plural, isOwner, ownerName, nameOf, missingTable, shortDate, ago, state, laptopWait, DECISION,
} from "./core.js";

export const topics = { list: [], job: null, missing: false, questions: null, ideas: new Map(), editing: null, busy: new Set() };

const SUGG_COLS = "id,title,post_type,format,decision,status,suggestion,project_id,created_at";
const DAY_NAME = { monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday", friday: "Friday", saturday: "Saturday", sunday: "Sunday" };

// Open suggestions (waiting for Bryan) and the latest "Suggest topics" job. Missing until the
// database update: the row stays away.
export async function loadSuggestions() {
  try {
    const [list, jobs, open] = await Promise.all([
      sb.from("ideas").select(SUGG_COLS).not("suggestion", "is", null).eq("status", "new").eq("decision", "proposed")
        .order("id", { ascending: false }).limit(12).then(must),
      sb.from("jobs").select("id,kind,runner,state,result,error,created_at,started_at,updated_at").eq("kind", "topics")
        .order("id", { ascending: false }).limit(1).then(must),
      sb.from("client_questions").select("id", { count: "exact", head: true }).eq("status", "open"),
    ]);
    if (open.error) throw open.error;
    Object.assign(topics, { list: list.filter((i) => !topics.busy.has(i.id)), job: jobs[0] || null, openQuestions: open.count || 0, missing: false });
  } catch (err) {
    if (!missingTable(err)) throw err;
    Object.assign(topics, { list: [], job: null, missing: true });
  }
}

export const suggestSig = () => JSON.stringify([topics.list, topics.job, topics.missing, topics.openQuestions, info()]);
const info = () => state.laptop?.info?.topics || null;

export function whenLine() {
  const t = info();
  const last = t?.last_suggested || topics.list.map((i) => i.suggestion?.suggested_at).filter(Boolean).sort().pop();
  const weekly = t && DAY_NAME[t.day] ? `Studio suggests by itself every ${DAY_NAME[t.day]} morning${t.behind ? " and when the month falls behind" : ""}.` : "";
  return [last ? `Last suggested ${shortDate(last)}.` : "", weekly].filter(Boolean).join(" ");
}

export function jobLine() {
  const j = topics.job;
  if (!j) return "";
  if (j.state === "queued") return `<span class="spin"></span> Asked Studio. ${esc(laptopWait())}`;
  if (j.state === "running") return `<span class="spin"></span> Studio is thinking up topics for the gaps. About a minute.`;
  if (j.state === "failed" && Date.now() - Date.parse(j.updated_at || j.created_at) < 864e5) {
    return `<span class="error-text">Couldn't suggest topics: ${esc(j.error || "unknown error")}</span>`;
  }
  if (j.state === "done" && Date.now() - Date.parse(j.updated_at || j.created_at) < 6 * 3600e3) {
    const n = Number(j.result?.new_ideas) || 0;
    return n ? `Studio suggested ${esc(plural(n, "topic"))} (${esc(ago(j.updated_at))}).`
      : `No new topics this time${j.result?.notes ? `: ${esc(j.result.notes)}` : " (nothing strong enough that isn't planned already)."}`;
  }
  return "";
}

// Go / Park / Dismiss on a card (the owner), and "Suggest topics" (anyone).
export async function suggestClick(t, rerender) {
  if (t.matches("[data-suggest-topics]")) {
    t.disabled = true;
    const { error } = await sb.from("jobs").insert({ kind: "topics", runner: "laptop", state: "queued", payload: {} });
    if (error) {
      if (/23514|check constraint/i.test(`${error.code || ""} ${error.message || ""}`)) throw new Error(`needs a one-time update: ${ownerName()} runs cloud_setup.py --update`);
      if (/42501|row-level security/i.test(`${error.code || ""} ${error.message || ""}`)) { toast("Studio is already on it."); return true; }
      throw new Error(error.message);
    }
    topics.job = { kind: "topics", runner: "laptop", state: "queued", created_at: new Date().toISOString() };
    toast("Asked Studio to suggest topics for this month's gaps.");
    rerender();
    return true;
  }
  const act = t.dataset.suggAct;
  if (!act) return false;
  const id = Number(t.closest("[data-sugg]")?.dataset.sugg);
  const i = topics.list.find((x) => x.id === id);
  if (!i) return true;
  topics.busy.add(id);
  rerender();
  try {
    const values = act === "dismissed" ? { status: "dismissed" } : { decision: act };
    const rows = must(await sb.from("ideas").update(values).eq("id", id).select("id"));
    if (!rows?.length) throw new Error(`only ${ownerName()} can decide`);
    topics.list = topics.list.filter((x) => x.id !== id);
    toast(act === "go" ? (i.format === "carousel" ? "Marked Go. Open it to write the slides." : "Marked Go.")
      : act === "parked" ? "Parked." : "Dismissed. Studio won't suggest it again.");
  } finally {
    topics.busy.delete(id);
    rerender();
  }
  return true;
}

// ── #/questions: the Client questions page ──────────────────────────────────

const Q_STATUS = { open: "Open", suggested: "Suggested as a post", used: "Became a post", passed: `Passed` };

export async function loadQuestions() {
  const rows = must(await sb.from("client_questions").select("*").order("id", { ascending: false }).limit(500));
  const ids = [...new Set(rows.map((q) => q.idea_id).filter(Boolean))];
  const ideas = ids.length ? must(await sb.from("ideas").select("id,title,decision,status,production").in("id", ids)) : [];
  topics.questions = rows;
  topics.ideas = new Map(ideas.map((i) => [i.id, i]));
}

function ideaLink(q) {
  const i = topics.ideas.get(q.idea_id);
  if (!i) return "";
  const s = i.status === "dismissed" ? `<span class="chip rejected">Dismissed</span>`
    : i.status === "made" || i.production ? `<span class="chip producing">Made</span>`
      : `<span class="chip ${esc(i.decision)}">${esc(DECISION[i.decision] || i.decision)}</span>`;
  return `<a class="q-idea" href="#/idea/${esc(i.id)}"><span>→ ${esc(i.title)}</span>${s}</a>`;
}

function qItemHTML(q) {
  const me = state.me?.id;
  const mayDelete = isOwner() || q.created_by === me;
  if (topics.editing === q.id) {
    return `<li class="q-item editing" data-q="${esc(q.id)}"><form class="q-form" data-q-edit="${esc(q.id)}">
      <label>Question<textarea data-q-field="question" rows="2" maxlength="500" required>${esc(q.question)}</textarea></label>
      <label><span>Who asked <span class="muted">(optional)</span></span><input data-q-field="asked_by" maxlength="120" value="${esc(q.asked_by)}" placeholder="e.g. a couple at a site visit"></label>
      <label><span>Note <span class="muted">(optional)</span></span><textarea data-q-field="note" rows="2" maxlength="2000" placeholder="What you answered, or what's behind the question">${esc(q.note)}</textarea></label>
      <div class="row end"><button type="button" class="btn" data-q-cancel>Cancel</button><button type="submit" class="btn primary">Save</button></div>
    </form></li>`;
  }
  const by = nameOf(q.created_by);
  const meta = [q.asked_by ? `Asked by ${esc(q.asked_by)}` : "", by ? `added by ${esc(by)}` : "", esc(shortDate(q.created_at))].filter(Boolean).join(" · ");
  return `<li class="q-item" data-q="${esc(q.id)}">
    <p class="q-text">${esc(q.question)}</p>
    ${q.note ? `<p class="q-note muted small">${esc(q.note)}</p>` : ""}
    <p class="q-meta muted small">${meta}</p>
    ${ideaLink(q)}
    <div class="q-actions">
      ${q.status === "passed" ? `<button type="button" class="btn small" data-q-again title="Studio may suggest a post from it again">Suggest again</button>` : ""}
      <button type="button" class="btn small ghost" data-q-edit-open>Edit</button>
      ${mayDelete ? `<button type="button" class="btn small ghost danger" data-q-delete aria-label="Delete the question: ${esc(q.question.slice(0, 60))}">Delete</button>` : ""}
    </div>
  </li>`;
}

export function questionsPageHTML(backHTML) {
  const rows = topics.questions || [];
  const groups = ["open", "suggested", "used", "passed"].map((s) => [s, rows.filter((q) => q.status === s)]);
  const sec = ([s, list]) => list.length || s === "open" ? `<section class="q-sec" aria-labelledby="q-${s}"><h2 id="q-${s}">${esc(Q_STATUS[s])} <span class="count">${esc(list.length)}</span></h2>
      ${list.length ? `<ul class="q-list">${list.map(qItemHTML).join("")}</ul>` : `<p class="muted">None waiting. Add the next one you hear.</p>`}
      ${s === "passed" && list.length ? `<p class="muted small">${esc(ownerName())} dismissed the post Studio suggested from these. Suggest again puts one back.</p>` : ""}</section>` : "";
  return `<div class="month-wrap qs">
    ${backHTML}
    <h1 tabindex="-1">Client questions</h1>
    <p class="muted">What clients ask you. Real questions make the best Know-how posts: when Studio suggests topics it answers open ones first, and the post it suggests shows up here.</p>
    <form class="q-add" id="q-add">
      <label class="sr-only" for="q-new">A question a client asked</label>
      <textarea id="q-new" data-draft rows="2" maxlength="500" required placeholder="What did they ask? e.g. Can I hack this wall in a resale flat?"></textarea>
      <details class="q-more"><summary>Who asked, a note <span class="muted">(optional)</span></summary>
        <label>Who asked<input id="q-asked" maxlength="120" placeholder="e.g. a couple at a site visit"></label>
        <label>Note<textarea id="q-note" rows="2" maxlength="2000" placeholder="What you answered, or what's behind the question"></textarea></label>
      </details>
      <p class="error-text" id="q-error" role="alert" hidden></p>
      <div class="row end"><button type="submit" class="btn primary" id="q-save">Add question</button></div>
    </form>
    ${groups.map(sec).join("")}
    <p class="muted small">Don't write clients' names or addresses here: questions only.</p>
  </div>`;
}

// Clicks and submits on the Client questions page. Returns true when handled.
export async function questionsEvent(e, root, reload) {
  const t = e.target;
  if (e.type === "submit" && t.id === "q-add") {
    e.preventDefault();
    const question = root.querySelector("#q-new").value.trim();
    const err = root.querySelector("#q-error");
    if (question.length < 3) { err.textContent = "Write the question first."; err.hidden = false; return true; }
    err.hidden = true;
    root.querySelector("#q-save").disabled = true;
    try {
      must(await sb.from("client_questions").insert({ question, asked_by: root.querySelector("#q-asked").value.trim(), note: root.querySelector("#q-note").value.trim() }));
      root.querySelector("#q-new").value = "";
      root.querySelector("#q-asked").value = "";
      root.querySelector("#q-note").value = "";
      toast("Question added. Studio can turn it into a post.");
      await reload();
      root.querySelector("#q-new")?.focus();
    } finally {
      const b = root.querySelector("#q-save");
      if (b) b.disabled = false;
    }
    return true;
  }
  if (e.type === "submit" && t.dataset.qEdit) {
    e.preventDefault();
    const values = {};
    for (const f of t.querySelectorAll("[data-q-field]")) values[f.dataset.qField] = f.value.trim();
    if ((values.question || "").length < 3) { toast("The question can't be empty."); return true; }
    must(await sb.from("client_questions").update(values).eq("id", Number(t.dataset.qEdit)));
    topics.editing = null;
    toast("Saved.");
    await reload();
    return true;
  }
  if (e.type !== "click") return false;
  const b = t.closest("button");
  const id = Number(b?.closest("[data-q]")?.dataset.q);
  if (!b || !id) return false;
  if (b.matches("[data-q-edit-open]")) { topics.editing = id; await reload(); root.querySelector(`[data-q="${id}"] textarea`)?.focus(); return true; }
  if (b.matches("[data-q-cancel]")) { topics.editing = null; await reload(); return true; }
  if (b.matches("[data-q-again]")) {
    must(await sb.from("client_questions").update({ status: "open", idea_id: null }).eq("id", id));
    toast("Back in the open questions.");
    await reload();
    return true;
  }
  if (b.matches("[data-q-delete]")) {
    if (!confirm("Delete this question?")) return true;
    const rows = must(await sb.from("client_questions").delete().eq("id", id).select("id"));
    if (!rows?.length) throw new Error(`only ${ownerName()} or whoever added it can delete it`);
    toast("Deleted.");
    await reload();
    return true;
  }
  return false;
}
