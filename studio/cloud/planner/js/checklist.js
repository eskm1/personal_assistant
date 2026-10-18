// "Today at <project>", made for a phone: the site visit for today (or the next one), what
// to film on it grouped by camera, with Film / From library on each shot. "All shots" is the
// older on-site checklist: every shot still in play, grouped by arc and idea. One tap ticks a
// shot filmed (or unticks it). Routes: #/today/<project_id> (and the old #/checklist/<id>).
import {
  $, CAMERA, CAMERA_LONG, CAMERA_ORDER, FRAMING, KIND, esc, sb, must, toast, store, recall, pbarHTML, fbarHTML, prepShots,
  progress, takeRetake, takeFlags, nameOf, state, syncInert, dayLabel, plural, stillUrl, takePaths,
} from "./core.js";
import { loadShoot, visitFor } from "./shoot.js";
import { uploadButtonsHTML } from "./upload.js";

export const check = {
  pid: null, data: null, onlyNeeded: recall("studio.onlyNeeded") !== "0", camera: recall("studio.camera") || "",
  keep: new Set(), busy: 0, mode: null, visit: null,
};
const el = () => $("#checklist");
export const checklistOpen = () => !el().hidden;

export async function openChecklist(pid) {
  pid = Number(pid);
  if (!el().hidden && check.pid === pid) return;
  check.pid = pid;
  check.keep = new Set();
  check.data = null;
  check.mode = null;
  el().hidden = false;
  syncInert();
  document.body.classList.add("locked");
  el().innerHTML = `<p class="muted cl-loading"><span class="spin"></span> Loading the shot list…</p>`;
  try {
    await loadChecklist();
    if (!el().contains(document.activeElement)) el().querySelector(".cl-top .btn")?.focus({ preventScroll: true });
  } catch (err) {
    el().innerHTML = `<div class="cl-head"><a class="btn" href="#/project/${esc(pid)}">‹ Plan</a></div><p class="error-text cl-loading">Couldn't load it: ${esc(err.message)}</p>`;
  }
}

export function hideChecklist() {
  if (el().hidden) return;
  el().hidden = true;
  syncInert();
  el().innerHTML = "";
  if ($("#viewer").hidden && $("#modal").hidden) document.body.classList.remove("locked");
}

export async function loadChecklist() {
  const pid = check.pid;
  if (!pid || check.busy) return;
  const seq = (check.seq = (check.seq || 0) + 1);
  const [project, arcs, ideas, plan] = await Promise.all([
    sb.from("projects").select("id,name").eq("id", pid).maybeSingle().then(must),
    sb.from("arcs").select("id,title,position").eq("project_id", pid).order("position").order("id").then(must),
    sb.from("ideas").select("id,title,arc_id,decision,position,shots(*,takes(id,clip_id,clips(verdict,flags)))")
      .eq("project_id", pid).eq("status", "new").neq("decision", "parked").order("position").order("id").then(must),
    loadShoot(pid).catch(() => null),
  ]);
  if (pid !== check.pid || seq !== check.seq || check.busy || el().hidden) return;
  if (!project) throw new Error("this project is gone");
  const order = new Map(arcs.map((a, i) => [a.id, i]));
  const title = new Map(arcs.map((a) => [a.id, a.title]));
  const groups = ideas
    .map((i) => ({ ...i, shots: prepShots(i.shots) }))
    .filter((i) => i.shots.length)
    .sort((a, b) => ((order.get(a.arc_id) ?? 1e9) - (order.get(b.arc_id) ?? 1e9))
      || ((b.decision === "go") - (a.decision === "go")) || (a.position - b.position) || (a.id - b.id))
    .map((i) => ({ arc: title.get(i.arc_id) || "Unsorted", idea_id: i.id, title: i.title, decision: i.decision, progress: progress(i.shots), shots: i.shots }));
  check.data = { project, groups };
  check.visit = visitFor(plan);
  // The visit when there is one; the whole list when there's no shoot plan.
  if (!check.visit) check.mode = "all";
  else if (!check.mode) check.mode = recall("studio.todayMode") === "all" ? "all" : "day";
  if (check.mode === "day") renderToday(); else renderChecklist();
}

function modeSwitch() {
  if (!check.visit) return "";
  return `<div class="seg cl-modes" role="group" aria-label="Show">${[["day", check.visit.today ? "Today's shots" : "Next visit"], ["all", "All shots"]].map(([k, l]) =>
    `<button data-cl-mode="${k}" class="${check.mode === k ? "on" : ""}" aria-pressed="${check.mode === k}">${l}</button>`).join("")}</div>`;
}

// The site visit: due shots grouped by camera, big tap targets.
function renderToday() {
  const v = check.visit;
  const data = check.data;
  const root = el();
  const scroll = root.scrollTop;
  const focusId = document.activeElement?.dataset?.clShot || "";
  const done = v.shots.filter((s) => s.filmed).length;
  const groups = CAMERA_ORDER.map((cam) => ({ cam, list: v.shots.filter((s) => (s.camera || "any") === cam) })).filter((g) => g.list.length);
  const when = v.today ? "Today" : `Next visit: ${dayLabel(v.day)}`;
  const ends = v.phase?.ends_on ? ` (${v.phase.name} ends ${dayLabel(v.phase.ends_on)})` : "";
  root.innerHTML = `<div class="cl-head">
      <div class="cl-top"><a class="btn" href="#/project/${esc(data.project.id)}" aria-label="Back to the plan">‹ Plan</a>
        <div class="cl-title"><h1>${v.today ? `Today at ${esc(data.project.name)}` : esc(data.project.name)}</h1>
          <span class="muted small">${esc(when)}${v.phase ? ` · ${esc(v.phase.name)}` : ""}${v.who ? ` · On site: ${esc(v.who)}` : ""}</span></div></div>
      <div class="cl-progress">${fbarHTML(done, v.shots.length)}<span><strong>${done}</strong> of ${plural(v.shots.length, "shot")} filmed${v.posts ? ` · for ${plural(v.posts, "post")}` : ""}</span></div>
      ${modeSwitch()}
    </div>
    <div class="cl-body">
      ${v.note ? `<p class="cl-note">${esc(v.note)}</p>` : ""}
      ${v.lastChance ? `<div class="sd-warn cl-warn" role="note"><strong>Last chance:</strong> ${plural(v.todo.length, `${v.phase.name} shot`)} can't be filmed after this visit${esc(ends)}.</div>` : ""}
      ${groups.map((g) => `<h2 class="cl-arc">${esc(CAMERA_LONG[g.cam])} · ${g.list.length}</h2>
        <ul class="cl-shots">${g.list.map(todayShotHTML).join("")}</ul>`).join("")
        || `<div class="cl-done"><p><strong>Nothing planned for this visit.</strong></p><p class="muted">Give the day a phase in the shoot plan, or move shots onto it. “All shots” lists everything still needed.</p></div>`}
      <p class="muted small cl-foot">Tap a shot when it's filmed. Film or pick a video to send it straight to that shot; it turns into “Got it” once Studio has checked it.</p>
    </div>`;
  root.scrollTop = scroll;
  if (focusId) root.querySelector(`[data-cl-shot="${CSS.escape(focusId)}"]`)?.focus({ preventScroll: true });
}

function todayShotHTML(s) {
  const take = s.takes[0];
  const thumb = take ? stillUrl(...takePaths(take)) : "";
  const label = [FRAMING[s.framing] ? FRAMING[s.framing].toUpperCase() : "", KIND[s.kind] || "", s.seconds ? `${Math.round(s.seconds * 10) / 10}s` : ""].filter(Boolean).join(" · ");
  const by = nameOf(s.filmed_by);
  const st = s.state === "needed" && s.filmed ? "filmed" : s.state;
  return `<li class="cl-item" data-drop-shot="${esc(s.id)}"><button class="cl-shot st-${esc(st)}" data-cl-shot="${esc(s.id)}" aria-pressed="${s.filmed}" ${s.takes.length ? 'aria-disabled="true"' : ""}>
      <span class="cl-icon" aria-hidden="true">${CL_ICON[st] || ""}</span>
      <span class="cl-text">
        ${label ? `<span class="cl-label">${esc(label)}</span>` : ""}
        <span class="cl-desc">${esc(s.description)}</span>
        ${s.line ? `<span class="cl-line">Say: “${esc(s.line)}”</span>` : ""}
        ${s.on_screen ? `<span class="cl-onscreen">On screen: ${esc(s.on_screen)}</span>` : ""}
        <span class="cl-for">for: ${esc(s.idea.title)}</span>
      </span>
      <span class="cl-state">${esc({ needed: "Tap when filmed", filmed: by ? `Filmed by ${by}` : "Filmed", got: "Got it", retake: "Retake" }[st])}</span>
      ${thumb ? `<img class="cl-thumb" src="${esc(thumb)}" alt="">` : ""}
    </button>
    <div class="cl-up">${uploadButtonsHTML(s.id, s.description)}</div></li>`;
}

const CL_ICON = { needed: "", filmed: "✓", got: "✓", retake: "↻" };

function renderChecklist() {
  const data = check.data;
  const root = el();
  const scroll = root.scrollTop;
  const total = { needed: 0, filmed: 0, got: 0, retake: 0, total: 0 };
  for (const g of data.groups) for (const s of g.shots) { total[s.state] += 1; total.total += 1; }
  const camOk = (s) => !check.camera || !s.camera || s.camera === "any" || s.camera === check.camera;
  const wanted = (s) => camOk(s) && (!check.onlyNeeded || s.state === "needed" || s.state === "retake" || check.keep.has(s.id));
  const groups = data.groups.map((g) => ({ ...g, list: g.shots.filter(wanted) })).filter((g) => g.list.length);
  let lastArc = null;
  const body = groups.map((g) => {
    const arc = g.arc !== lastArc ? `<h2 class="cl-arc">${esc(g.arc)}</h2>` : "";
    lastArc = g.arc;
    return `${arc}<section class="cl-group">
      <div class="cl-idea"><a class="cl-idea-title" href="#/idea/${esc(g.idea_id)}">${esc(g.title)}</a>
        ${g.decision === "go" ? `<span class="chip go">Go</span>` : ""}
        <span class="muted small">${esc(g.progress.got)}/${esc(g.progress.total)} got</span></div>
      <ul class="cl-shots">${g.list.map((s) => {
        const take = s.takes.find(takeRetake) || null;
        const flags = takeFlags(take);
        const label = [s.camera ? CAMERA[s.camera] : "", FRAMING[s.framing] ? FRAMING[s.framing].toUpperCase() : "", KIND[s.kind] || "", s.seconds ? `${Math.round(s.seconds * 10) / 10}s` : ""].filter(Boolean).join(" · ");
        const by = nameOf(s.filmed_by);
        return `<li class="cl-item" data-drop-shot="${esc(s.id)}"><button class="cl-shot st-${esc(s.state)}" data-cl-shot="${esc(s.id)}" aria-pressed="${!!s.filmed_at}">
          <span class="cl-icon" aria-hidden="true">${CL_ICON[s.state] || ""}</span>
          <span class="cl-text">
            ${label ? `<span class="cl-label">${esc(label)}</span>` : ""}
            <span class="cl-desc">${esc(s.description)}</span>
            ${s.line ? `<span class="cl-line">“${esc(s.line)}”</span>` : ""}
            ${s.on_screen ? `<span class="cl-onscreen">On screen: ${esc(s.on_screen)}</span>` : ""}
            ${s.state === "retake" && flags.length ? `<span class="cl-flags">Retake: ${esc(flags.join("; "))}</span>` : ""}
          </span>
          <span class="cl-state">${esc({ needed: "Tap when filmed", filmed: by ? `Filmed by ${by}` : "Filmed", got: "Got it", retake: s.filmed_at ? "Retake (ticked)" : "Retake" }[s.state])}</span>
        </button>${s.state === "got" ? "" : `<div class="cl-up">${uploadButtonsHTML(s.id, s.description)}</div>`}</li>`;
      }).join("")}</ul></section>`;
  }).join("");
  root.innerHTML = `<div class="cl-head">
      <div class="cl-top"><a class="btn" href="#/project/${esc(data.project.id)}" aria-label="Back to the plan">‹ Plan</a>
        <div class="cl-title"><h1>${check.visit ? "All shots" : "Shot list"}</h1><span class="muted small">${esc(data.project.name)}</span></div></div>
      ${modeSwitch()}
      <div class="cl-progress">${pbarHTML(total)}<span><strong>${total.got}</strong> of ${total.total} got · ${total.filmed} waiting for upload${total.retake ? ` · <span class="bad-text">${total.retake} to retake</span>` : ""}</span></div>
      <div class="cl-filters">
        <div class="seg cl-cams" role="group" aria-label="Camera">${[["", "All"], ["iphone", "iPhone"], ["zfc", "Z fc"], ["gopro", "GoPro"]].map(([k, l]) =>
          `<button data-cl-camera="${k}" class="${check.camera === k ? "on" : ""}" aria-pressed="${check.camera === k}">${l}</button>`).join("")}</div>
        <label class="check cl-only"><input type="checkbox" id="cl-only" ${check.onlyNeeded ? "checked" : ""}> Still needed only</label>
      </div>
    </div>
    <div class="cl-body">${body || `<div class="cl-done"><p><strong>${total.total ? "Nothing left to film here." : "No shots planned yet."}</strong></p>
      <p class="muted">${total.total ? (check.onlyNeeded || check.camera ? "Untick “Still needed only” or pick All to see everything." : "") : "Ideas need a storyboard before they show up here. Parked ideas and ideas already in the edit are left out."}</p></div>`}
      <p class="muted small cl-foot">Tap a shot when it's filmed. It turns into “Got it” once the footage is uploaded and matched by Studio. Parked ideas and ideas already in the edit aren't listed.</p></div>`;
  root.scrollTop = scroll;
}

async function toggleShot(id) {
  const all = [...check.data.groups.flatMap((g) => g.shots), ...(check.visit?.shots || [])].filter((x) => x.id === id);
  const s = all[0];
  if (!s) return;
  if (s.state === "got") { toast("We already have footage for this shot."); return; }
  const filmed = !s.filmed_at;
  for (const x of all) {
    x.filmed_at = filmed ? new Date().toISOString() : null;
    x.filmed_by = filmed ? state.me?.id : null;
    x.filmed = filmed || x.takes.length > 0;
    if (x.state === "needed" || x.state === "filmed") x.state = filmed ? "filmed" : "needed";
  }
  check.keep.add(id);
  if (check.mode === "day") renderToday(); else renderChecklist();
  $(`[data-cl-shot="${id}"]`)?.focus({ preventScroll: true });
  check.busy += 1;
  try {
    must(await sb.from("shots").update({ filmed_at: s.filmed_at }).eq("id", id));
  } catch (err) {
    toast(`Couldn't save: ${err.message}`);
  } finally {
    check.busy -= 1;
  }
  if (!check.busy) await loadChecklist().catch(() => {});
}

el().addEventListener("click", (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.clShot) toggleShot(Number(t.dataset.clShot));
  else if (t.dataset.clMode) {
    check.mode = t.dataset.clMode;
    store("studio.todayMode", check.mode);
    el().scrollTop = 0;
    if (check.mode === "day") renderToday(); else renderChecklist();
    el().querySelector(`[data-cl-mode="${check.mode}"]`)?.focus();
  }
  else if (t.dataset.clCamera != null) {
    check.camera = t.dataset.clCamera;
    store("studio.camera", check.camera);
    renderChecklist();
  }
});
el().addEventListener("change", (e) => {
  if (e.target.id !== "cl-only") return;
  check.onlyNeeded = e.target.checked;
  check.keep = new Set();
  store("studio.onlyNeeded", check.onlyNeeded ? "1" : "0");
  renderChecklist();
});
