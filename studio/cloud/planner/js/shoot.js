// The shoot plan: a project's renovation phases, the days someone is on site, and what to
// film on each visit (CONTRACT "Shoot plan"). A Go idea's shot is due on a site day when it's
// pinned to that day (shots.shoot_on), or isn't pinned and belongs to the day's phase.
// Also: the "Not on a site day yet" list (with Claude's place job), the "Needs a home" tray
// of the project's clips with no take, and the shot picker used to attach or move footage.
import {
  $, $$, CAMERA_LONG, CAMERA_ORDER, esc, sb, must, toast, store, recall, plural, stillUrl, signPaths, takePaths,
  takeVerdict, prepShots, isFilmed, todayISO, dayOf, dayLabel, dayRange, missingTable, mmss, nameOf, ownerName,
  openModal, closeModal, formError, jobView, jobActive, laptopWait, state, retakeBadge, takeRetake,
} from "./core.js";
import { uploadButtonsHTML, rememberShot, onClipDrop, clipDragType, uploadsFor } from "./upload.js";

export const shoot = {
  pid: null, data: null, missing: false, error: "", sig: "",
  open: new Set(JSON.parse(recall("studio.shootOpen") || '["needs"]')),
};

const SHOT_COLS = "id,idea_id,position,description,line,on_screen,camera,framing,kind,seconds,filmed_at,filmed_by,phase_id,shoot_on,"
  + "takes(id,clip_id,thumb_path,source,created_at,clips(title,verdict,flags,thumb_path,duration))";

// Everything the shoot plan (and the Today view) shows for one project.
export async function loadShoot(pid) {
  pid = Number(pid);
  let phases, days, ideas, clips, jobs;
  try {
    [phases, days, ideas, clips, jobs] = await Promise.all([
      sb.from("phases").select("*").eq("project_id", pid).order("position").order("id").then(must),
      sb.from("site_days").select("*").eq("project_id", pid).order("day").then(must),
      sb.from("ideas").select(`id,title,folder,decision,status,arc_id,position,shots(${SHOT_COLS})`)
        .eq("project_id", pid).eq("decision", "go").neq("status", "dismissed").order("position").order("id").then(must),
      sb.from("clips").select("id,title,duration,recorded_at,thumb_path,idea_id,verdict,takes(id)").eq("project_id", pid)
        .order("recorded_at", { ascending: false, nullsFirst: false }).order("id", { ascending: false }).limit(300).then(must),
      sb.from("jobs").select("id,kind,runner,project_id,state,result,error,created_at,started_at,updated_at")
        .eq("kind", "place").eq("project_id", pid).order("id", { ascending: false }).limit(1).then(must),
    ]);
  } catch (err) {
    if (missingTable(err)) {
      if (shoot.pid === pid || !shoot.pid) Object.assign(shoot, { pid, data: null, missing: true, error: "" });
      return null;
    }
    throw err;
  }
  const shotIds = ideas.flatMap((i) => (i.shots || []).map((s) => s.id));
  let uploads = [];
  if (shotIds.length) {
    uploads = await sb.from("shot_uploads").select("id,shot_id,file_name,state,error,created_at,updated_at")
      .in("shot_id", shotIds.slice(0, 300)).neq("state", "uploading").order("created_at", { ascending: false }).limit(200)
      .then(must).catch(() => []);
  }
  const d = build({ phases, days, ideas, clips, job: jobs[0] || null, uploads });
  await signPaths([...d.shots.flatMap((s) => s.takes.flatMap(takePaths)), ...d.homeless.map((c) => c.thumb_path)]).catch(() => {});
  Object.assign(shoot, { pid, data: d, missing: false, error: "" });
  return d;
}

function build({ phases, days, ideas, clips, job, uploads }) {
  const today = todayISO();
  const phaseById = new Map(phases.map((p) => [p.id, p]));
  const ideaById = new Map(ideas.map((i) => [i.id, i]));
  const shots = [];
  for (const i of ideas) {
    i.shots = prepShots(i.shots);
    i.shots.forEach((s, k) => {
      s.idea = i;
      s.n = k + 1;
      s.filmed = isFilmed(s);
      s.filmedDay = dayOf(s.filmed_at) || dayOf(s.takes[0]?.created_at);
      shots.push(s);
      rememberShot(s.id, `${s.description} (for: ${i.title})`);
    });
  }
  const dayList = days.map((d) => ({ ...d, phase: phaseById.get(d.phase_id) || null, past: d.day < today, today: d.day === today, shots: [] }));
  const byDate = new Map(dayList.map((d) => [d.day, d]));
  const phaseDays = new Map();
  for (const d of dayList) if (d.phase_id) (phaseDays.get(d.phase_id) || phaseDays.set(d.phase_id, []).get(d.phase_id)).push(d);
  const placed = new Set();
  for (const s of shots) {
    if (s.shoot_on) {
      const d = byDate.get(s.shoot_on);
      if (d) { d.shots.push(s); if (s.filmed || !d.past) placed.add(s.id); }
      continue;
    }
    const pd = phaseDays.get(s.phase_id) || [];
    if (!pd.length) continue;
    if (s.filmed) {
      // A filmed shot shows once: on the visit it was filmed (or the one before it).
      const fd = s.filmedDay;
      const d = (fd && (pd.find((x) => x.day === fd) || [...pd].reverse().find((x) => x.day <= fd))) || pd[0];
      d.shots.push(s);
      placed.add(s.id);
    } else {
      // Still to film: on every visit of its phase from today on.
      const ahead = pd.filter((x) => !x.past);
      for (const d of ahead) d.shots.push(s);
      if (ahead.length) placed.add(s.id);
    }
  }
  for (const d of dayList) {
    d.shots.sort((a, b) => (a.idea.position - b.idea.position) || (a.idea.id - b.idea.id) || (a.position - b.position));
    d.todo = d.shots.filter((s) => !s.filmed);
    d.filmedCount = d.shots.length - d.todo.length;
    d.posts = new Set(d.shots.map((s) => s.idea_id)).size;
    const pd = phaseDays.get(d.phase_id) || [];
    // The last visit of its phase while that phase still has shots to film.
    d.lastChance = !d.past && !!d.phase && pd[pd.length - 1] === d && d.todo.length > 0;
  }
  // Go shots still to film that aren't on any coming visit, with why.
  const why = (s) => {
    if (s.shoot_on) return byDate.has(s.shoot_on) ? `Its day (${dayLabel(s.shoot_on)}) is over` : `Pinned to ${dayLabel(s.shoot_on)}, which isn't a site day`;
    if (!s.phase_id || !phaseById.has(s.phase_id)) return "No phase yet";
    const p = phaseById.get(s.phase_id);
    return (phaseDays.get(s.phase_id) || []).length ? `${p.name}'s site days are over` : `No site day for ${p.name} yet`;
  };
  const unplaced = shots.filter((s) => !s.filmed && !placed.has(s.id)).map((s) => ({ shot: s, why: why(s) }));
  const unplacedIdeas = [];
  for (const u of unplaced) {
    let g = unplacedIdeas.find((x) => x.idea.id === u.shot.idea_id);
    if (!g) unplacedIdeas.push(g = { idea: u.shot.idea, items: [] });
    g.items.push(u);
  }
  const homeless = clips.filter((c) => !(c.takes || []).length).map((c) => ({ ...c, idea: ideaById.get(c.idea_id) || null }));
  const upByShot = new Map();
  for (const u of uploads) if (!upByShot.has(u.shot_id)) upByShot.set(u.shot_id, u);
  return {
    phases, phaseById, days: dayList, upcoming: dayList.filter((d) => !d.past), past: dayList.filter((d) => d.past).reverse(),
    ideas, shots, unplaced, unplacedIdeas, homeless, job, upByShot, today,
    current: phases.find((p) => p.starts_on && p.ends_on && p.starts_on <= today && today <= p.ends_on) || null,
  };
}

// The visit the Today view shows: today's, else the next one.
export function visitFor(d) {
  return d?.upcoming[0] || null;
}

// ── Rendering ───────────────────────────────────────────────────────────────

const el = () => $("#shoot-view");

export function renderShoot(force = false) {
  const root = el();
  if (!root) return;
  if (shoot.missing) {
    shoot.sig = "missing";
    root.innerHTML = `<div class="shoot-missing box"><p><strong>The shoot plan needs a one-time update of the online database.</strong></p>
      <p class="muted">${esc(ownerName())} runs <code>cloud_setup.py --update</code> in the studio folder once; then this page works. The Ideas view works in the meantime.</p></div>`;
    return;
  }
  const d = shoot.data;
  if (!d) { root.innerHTML = `<p class="muted"><span class="spin"></span> Loading the shoot plan…</p>`; shoot.sig = ""; return; }
  const sig = JSON.stringify([d.phases, d.days.map((x) => [x.id, x.day, x.phase_id, x.who, x.note, x.shots.map((s) => s.id)]),
    d.shots.map((s) => [s.id, s.description, s.camera, s.filmed_at, s.filmed_by, s.phase_id, s.shoot_on, s.state, s.idea.title,
      s.takes.map((t) => [t.id, stillUrl(...takePaths(t))])]),
    d.homeless.map((c) => [c.id, c.title, stillUrl(c.thumb_path)]), jobView(d.job), [...d.upByShot.values()],
    [...shoot.open], state.laptop?.last_seen ? Math.floor(Date.parse(state.laptop.last_seen) / 60e3) : 0]);
  if (!force && sig === shoot.sig) return;
  if (!force && root.querySelector("details.sp-menu[open]")) { shoot.pending = true; return; }
  shoot.sig = sig;
  shoot.pending = false;
  const fk = document.activeElement?.closest?.("#shoot-view") ? document.activeElement.dataset.fk : null;
  root.innerHTML = `<div class="sp-grid">
      <div class="sp-main">
        ${phasesHTML(d)}
        <div class="sp-days-head"><h2>Site days</h2><button class="btn" data-day-add>+ Add site day</button></div>
        ${d.upcoming.length ? d.upcoming.map((x) => dayHTML(x, d)).join("")
          : `<div class="sp-empty"><p><strong>No site days coming up.</strong></p><p class="muted">Add the days someone is on site, with the phase the work is in. Each day then lists what to film.</p></div>`}
        ${d.past.length ? `<details class="sp-past" data-open-key="past" ${shoot.open.has("past") ? "open" : ""}>
          <summary>Past site days (${d.past.length})</summary>${d.past.map((x) => dayHTML(x, d)).join("")}</details>` : ""}
      </div>
      <aside class="sp-side">
        ${unplacedHTML(d)}
        ${homelessHTML(d)}
      </aside>
    </div>`;
  if (fk) root.querySelector(`[data-fk="${CSS.escape(fk)}"]`)?.focus({ preventScroll: true });
}

function phasesHTML(d) {
  const today = d.today;
  const items = d.phases.map((p) => {
    const done = p.ends_on && p.ends_on < today;
    const now = d.current?.id === p.id;
    const when = done ? "Done" : dayRange(p.starts_on, p.ends_on);
    return `<li><button class="phase${now ? " now" : ""}${done ? " done" : ""}" data-phase-edit="${esc(p.id)}" data-fk="ph:${esc(p.id)}"
      ${now ? 'aria-current="step"' : ""} aria-label="${esc(`${p.name}, ${when}${now ? ", now" : ""}. Edit`)}">
      <span class="phase-name">${esc(p.name)}</span><span class="phase-when">${esc(when)}${done ? ` <span class="sr-only">(${esc(dayRange(p.starts_on, p.ends_on))})</span>` : ""}</span></button></li>`;
  }).join("");
  return `<section class="sp-phases" aria-labelledby="sp-phases-h"><div class="sp-section-head"><h2 id="sp-phases-h">Renovation phases</h2>
      <span class="muted small hide-mobile">Tap a phase to change its name or dates.</span></div>
    <ol class="phase-strip">${items}<li><button class="phase add" data-phase-add data-fk="ph:add">+ Add phase</button></li></ol>
    ${d.phases.length ? "" : `<p class="muted small">Start with the phases of the job (Demolition, Waterproofing, Tiling, … Handover) and their dates. Shots are filmed in the phase whose work they show.</p>`}
  </section>`;
}

function dayHTML(x, d) {
  const groups = CAMERA_ORDER.map((cam) => ({ cam, list: x.shots.filter((s) => (s.camera || "any") === cam) })).filter((g) => g.list.length);
  const label = dayLabel(x.day);
  const ends = x.phase?.ends_on ? ` (${x.phase.name} ends ${dayLabel(x.phase.ends_on)})` : "";
  return `<article class="sd-card${x.today ? " today" : ""}${x.past ? " past" : ""}" aria-label="${esc(label)}">
    <header class="sd-head">
      <h3>${esc(label)}${x.today ? ` <span class="chip today-chip">Today</span>` : ""}</h3>
      ${x.phase ? `<span class="chip phase-chip">${esc(x.phase.name)}</span>` : `<span class="chip">No phase</span>`}
      <button class="btn small ghost push-right" data-day-edit="${esc(x.id)}" data-fk="de:${esc(x.id)}" aria-label="Edit ${esc(label)}">Edit</button>
    </header>
    <p class="sd-sub">${x.shots.length ? `${plural(x.shots.length, "shot")} for ${plural(x.posts, "post")}${x.filmedCount ? ` · ${x.filmedCount} filmed` : ""}` : "Nothing to film yet"}
      ${x.who ? ` · <span>On site: ${esc(x.who)}</span>` : ""}</p>
    ${x.note ? `<p class="sd-note">${esc(x.note)}</p>` : ""}
    ${x.lastChance ? `<div class="sd-warn" role="note"><strong>Last chance:</strong> ${plural(x.todo.length, `${x.phase.name} shot`)} can't be filmed after this visit${esc(ends)}.</div>` : ""}
    ${groups.map((g) => `<div class="sd-cam"><h4>${esc(CAMERA_LONG[g.cam])} <span class="count">${g.list.length}</span></h4>
      <ul class="sp-shots">${g.list.map((s) => shotRowHTML(s, x, d)).join("")}</ul></div>`).join("")}
    ${!x.shots.length && !x.past ? `<p class="muted small">${x.phase ? `No Go shots in ${esc(x.phase.name)} yet. Move shots here, or ask Claude to place them.` : "Give this day a phase to see what to film."}</p>` : ""}
  </article>`;
}

function uploadChip(s, d) {
  const u = d.upByShot.get(s.id);
  const mine = uploadsFor(s.id).some((x) => ["queued", "starting", "uploading", "paused"].includes(x.state));
  if (mine) return `<span class="chip drafting">Uploading…</span>`;
  if (!u) return "";
  if (u.state === "received") return `<span class="chip wait">Upload in: Studio is checking it</span>`;
  if (u.state === "failed") return `<span class="chip failed" title="${esc(u.error || "")}">Upload failed</span>`;
  return "";
}

export function takeThumbsHTML(s, key) {
  return s.takes.map((t) => {
    const url = stillUrl(...takePaths(t));
    const title = t.clips?.title || `Clip ${t.clip_id}`;
    const retake = takeRetake(t);
    return `<button type="button" class="sp-thumb${retake ? " retake" : ""}" data-take="${esc(t.id)}" data-take-shot="${esc(s.id)}" data-fk="t:${key}:${esc(t.id)}" aria-label="${esc(`Footage: ${title}${retake ? " (needs a retake)" : ""}. Move or remove`)}">
      ${url ? `<img src="${esc(url)}" alt="" loading="lazy">` : `<span class="take-none" aria-hidden="true">No still</span>`}${retake ? `<span class="thumb-badge" aria-hidden="true">Retake</span>` : ""}</button>`;
  }).join("");
}

function shotRowHTML(s, x, d) {
  const key = `${x.id}:${s.id}`;
  const got = s.takes.length > 0;
  return `<li class="sp-shot st-${esc(s.state)}" data-drop-shot="${esc(s.id)}">
    <label class="sp-tick"><input type="checkbox" data-sp-filmed="${esc(s.id)}" data-fk="f:${key}" ${s.filmed ? "checked" : ""} ${got ? "disabled" : ""}
        aria-label="${esc(`Filmed: ${s.description}`)}">
      <span class="sp-desc">${esc(s.description)}</span></label>
    <div class="sp-meta">
      <span class="sp-for">for: <a href="#/idea/${esc(s.idea_id)}">${esc(s.idea.title)}</a></span>
      ${s.shoot_on ? `<span class="chip">Pinned to this day</span>` : ""}
      ${s.state === "got" ? `<span class="chip ok">Footage in</span>` : s.state === "retake" ? `<span class="chip failed">Retake</span>` : s.filmed_at ? `<span class="chip">Filmed${s.filmed_by && nameOf(s.filmed_by) ? ` by ${esc(nameOf(s.filmed_by))}` : ""}</span>` : ""}
      ${uploadChip(s, d)}
    </div>
    ${s.takes.length ? `<div class="sp-thumbs">${takeThumbsHTML(s, key)}</div>` : ""}
    <div class="sp-actions">${x.past ? "" : uploadButtonsHTML(s.id, s.description)}
      <button type="button" class="btn small ghost" data-shot-place="${esc(s.id)}" data-fk="m:${key}" aria-label="${esc(`Move to another phase or day: ${s.description}`)}">Move…</button></div>
  </li>`;
}

function placeJobHTML(d) {
  const job = jobView(d.job);
  if (!job) return "";
  if (job.state === "queued") return `<p class="muted small job-wait"><span class="spin"></span> Asked Claude. ${esc(laptopWait())}</p>`;
  if (job.state === "running") return `<p class="muted small"><span class="spin"></span> Claude is placing the shots in phases. About a minute.</p>`;
  if (job.state === "failed") return `<p class="error-text small">Placing failed: ${esc(job.error || "unknown error")}</p>`;
  const r = job.result || {};
  const placed = Number(r.placed) || 0, left = Number(r.unplaced) || 0;
  return `<p class="muted small">Last time (${esc(dayLabel(dayOf(job.updated_at), { weekday: false }))}): Claude placed ${plural(placed, "shot")}${left ? `; ${left} didn't fit a phase` : ""}.</p>`;
}

function unplacedHTML(d) {
  const job = jobView(d.job);
  const busy = jobActive(job);
  const n = d.unplaced.length;
  return `<section class="sp-side-box" aria-labelledby="sp-unplaced-h">
    <h2 id="sp-unplaced-h">Not on a site day yet <span class="count">${n}</span></h2>
    ${n ? `<ul class="sp-unplaced">${d.unplacedIdeas.map((g) => `<li><details data-open-key="u:${esc(g.idea.id)}" ${shoot.open.has(`u:${g.idea.id}`) ? "open" : ""}>
        <summary><span class="sp-u-title">${esc(g.idea.title)}</span> <span class="count">${g.items.length}</span></summary>
        <ul class="sp-u-shots">${g.items.map(({ shot: s, why }) => `<li data-drop-shot="${esc(s.id)}"><span class="sp-u-desc">${esc(s.description)}</span>
          <span class="muted small">${esc(why)}</span>
          <button type="button" class="btn small ghost" data-shot-place="${esc(s.id)}" data-fk="mu:${esc(s.id)}" aria-label="${esc(`Put on a phase or day: ${s.description}`)}">Move…</button></li>`).join("")}</ul>
        <a class="small" href="#/idea/${esc(g.idea.id)}">Open the post</a></details></li>`).join("")}</ul>`
      : `<p class="muted small">${d.shots.length ? "Every Go shot still to film is on a coming site day." : "No Go ideas with shots yet. Mark ideas Go in the Ideas view."}</p>`}
    <div class="row"><button class="btn primary" id="sp-place" ${busy || !d.phases.length || !n ? "disabled" : ""}
      title="Claude reads each shot and picks the phase whose work it shows. Runs on Studio.">${busy ? "Placing…" : "Place by phase with Claude"}</button></div>
    ${!d.phases.length && n ? `<p class="muted small">Add phases first, then Claude can place the shots in them.</p>` : ""}
    ${placeJobHTML(d)}
  </section>`;
}

function homelessHTML(d) {
  const list = d.homeless;
  return `<details class="sp-side-box sp-needs" data-open-key="needs" ${shoot.open.has("needs") ? "open" : ""}>
    <summary><h2>Needs a home <span class="count">${list.length}</span></h2></summary>
    <p class="muted small">Footage from this project that isn't on a shot yet.<span class="hide-mobile"> Drag one onto a shot, or</span> use “Put on a shot…”.</p>
    ${list.length ? `<ul class="nh-list">${list.map((c) => {
      const url = stillUrl(c.thumb_path);
      return `<li class="nh-clip" draggable="true" data-clip="${esc(c.id)}">
        <span class="nh-thumb">${url ? `<img src="${esc(url)}" alt="" loading="lazy" draggable="false">` : `<span class="take-none" aria-hidden="true">No still</span>`}</span>
        <span class="nh-text"><span class="nh-title">${esc(c.title || `Clip ${c.id}`)}</span>${retakeBadge(c)}
          <span class="muted small">${c.duration ? esc(mmss(c.duration)) : ""}${c.idea ? ` · from ${esc(c.idea.title)}` : ""}</span>
          <button type="button" class="btn small" data-clip-put="${esc(c.id)}" data-fk="c:${esc(c.id)}">Put on a shot…</button></span>
      </li>`;
    }).join("")}</ul>` : `<p class="muted small">Nothing waiting. New footage that Studio can't match to a shot shows up here.</p>`}
  </details>`;
}

// ── Actions ─────────────────────────────────────────────────────────────────

let reload = async () => {};
export function onShootRefresh(fn) { reload = fn; }
async function refresh() {
  await reload();
  document.dispatchEvent(new CustomEvent("studio:refresh"));
}

const dateInput = (id, value, label) => `<label>${label}<input type="date" id="${id}" value="${esc(value || "")}"></label>`;

function openPhase(id) {
  const d = shoot.data;
  const p = id ? d.phaseById.get(id) : null;
  const k = p ? d.phases.indexOf(p) : -1;
  openModal(`<h2 id="modal-title">${p ? "Edit phase" : "Add a phase"}</h2>
    <form class="form">
      <label>Name<input id="ph-name" maxlength="80" value="${esc(p?.name || "")}" placeholder="e.g. Waterproofing" required></label>
      <div class="row two">${dateInput("ph-start", p?.starts_on, "Starts")}${dateInput("ph-end", p?.ends_on, "Ends")}</div>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      ${p ? `<div class="row"><button type="button" class="btn small" data-ph-move="-1" ${k === 0 ? "disabled" : ""}>Move earlier</button>
        <button type="button" class="btn small" data-ph-move="1" ${k === d.phases.length - 1 ? "disabled" : ""}>Move later</button>
        <button type="button" class="btn small ghost danger push-right" data-ph-delete>Delete phase…</button></div>` : ""}
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="ph-save">${p ? "Save" : "Add phase"}</button></div>
    </form>`, async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.id === "ph-save") {
      const name = $("#ph-name").value.trim();
      const starts_on = $("#ph-start").value || null, ends_on = $("#ph-end").value || null;
      if (!name) return formError("Give the phase a name.");
      if (starts_on && ends_on && ends_on < starts_on) return formError("It can't end before it starts.");
      if (p) must(await sb.from("phases").update({ name, starts_on, ends_on }).eq("id", p.id));
      else {
        const pos = Math.max(0, ...d.phases.map((x) => x.position || 0)) + 1;
        must(await sb.from("phases").insert({ project_id: shoot.pid, name, starts_on, ends_on, position: pos }));
      }
      closeModal();
      toast(p ? "Phase saved." : `${name} added.`);
      await refresh();
    } else if (t.dataset.phMove) {
      const order = d.phases.map((x) => x.id);
      const j = k + Number(t.dataset.phMove);
      if (j < 0 || j >= order.length) return;
      [order[k], order[j]] = [order[j], order[k]];
      await Promise.all(order.map((pid, n) => (d.phaseById.get(pid).position === n + 1 ? null : sb.from("phases").update({ position: n + 1 }).eq("id", pid).then(must))));
      closeModal();
      await refresh();
    } else if (t.dataset.phDelete != null) {
      const n = d.shots.filter((s) => s.phase_id === p.id).length;
      if (!confirm(`Delete the phase “${p.name}”?${n ? ` Its ${plural(n, "shot")} go back to “Not on a site day yet”.` : ""} Site days in it keep their date.`)) return;
      must(await sb.from("phases").delete().eq("id", p.id));
      closeModal();
      toast("Phase deleted.");
      await refresh();
    }
  });
}

// The phase whose dates hold this day, if any.
const phaseOn = (d, day) => d.phases.find((p) => p.starts_on && p.ends_on && p.starts_on <= day && day <= p.ends_on)?.id || "";

function openDay(id) {
  const d = shoot.data;
  const x = id ? d.days.find((y) => y.id === id) : null;
  const day = x?.day || "";
  const phaseOpts = (sel) => `<option value="">No phase</option>${d.phases.map((p) => `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.name)} (${esc(dayRange(p.starts_on, p.ends_on))})</option>`).join("")}`;
  openModal(`<h2 id="modal-title">${x ? `Edit ${esc(dayLabel(x.day))}` : "Add a site day"}</h2>
    <form class="form">
      ${dateInput("sd-day", day, "Date")}
      <label>Phase<select id="sd-phase">${phaseOpts(x?.phase_id ?? "")}</select></label>
      <label>Who's on site <span class="muted">(optional)</span><input id="sd-who" maxlength="120" value="${esc(x?.who || "")}" placeholder="e.g. Wei"></label>
      <label>Note <span class="muted">(optional)</span><input id="sd-note" maxlength="300" value="${esc(x?.note || "")}" placeholder="e.g. Tiler in from 9 am"></label>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end">${x ? `<button type="button" class="btn ghost danger" data-sd-delete>Delete day…</button>` : ""}
        <button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="sd-save">${x ? "Save" : "Add site day"}</button></div>
    </form>`, async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.id === "sd-save") {
      const dayVal = $("#sd-day").value;
      if (!dayVal) return formError("Pick the date.");
      if (d.days.some((y) => y.day === dayVal && y.id !== x?.id)) return formError("There's already a site day on that date.");
      const row = { day: dayVal, phase_id: $("#sd-phase").value ? Number($("#sd-phase").value) : null, who: $("#sd-who").value.trim(), note: $("#sd-note").value.trim() };
      const { error } = x ? await sb.from("site_days").update(row).eq("id", x.id) : await sb.from("site_days").insert({ project_id: shoot.pid, ...row });
      if (error) return formError(error.code === "23505" ? "There's already a site day on that date." : error.message);
      closeModal();
      toast(x ? "Site day saved." : `${dayLabel(dayVal)} added.`);
      await refresh();
    } else if (t.dataset.sdDelete != null) {
      const pinned = d.shots.filter((s) => s.shoot_on === x.day).length;
      if (!confirm(`Delete the site day ${dayLabel(x.day)}?${pinned ? ` ${plural(pinned, "shot")} pinned to it will need another day.` : ""}`)) return;
      must(await sb.from("site_days").delete().eq("id", x.id));
      closeModal();
      toast("Site day deleted.");
      await refresh();
    }
  });
  // A new date picks the phase it falls in (unless someone chose one already).
  let touched = !!x?.phase_id;
  $("#sd-phase").addEventListener("change", () => { touched = true; });
  $("#sd-day").addEventListener("change", () => { if (!touched) $("#sd-phase").value = String(phaseOn(d, $("#sd-day").value)); });
}

// Move a shot to a phase, or pin it to one site day. Works from anywhere the shot shows.
export async function openPlace(shotId) {
  let d = shoot.data;
  let s = d?.shots.find((x) => x.id === shotId);
  if (!s) {
    s = must(await sb.from("shots").select("id,idea_id,description,phase_id,shoot_on,ideas(project_id)").eq("id", shotId).maybeSingle());
    if (!s) { toast("That shot is gone."); return; }
    if (!d || shoot.pid !== s.ideas?.project_id) d = await loadShoot(s.ideas.project_id);
    if (!d) { toast("The shoot plan needs a one-time database update first."); return; }
  }
  openModal(`<h2 id="modal-title">Move this shot</h2>
    <p class="muted">${esc(s.description)}</p>
    <form class="form">
      <label>Phase (the work it shows)<select id="pl-phase"><option value="">No phase</option>${d.phases.map((p) => `<option value="${esc(p.id)}" ${p.id === s.phase_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label>Day<select id="pl-day"><option value="">Any site day in its phase</option>${d.days.map((x) => `<option value="${esc(x.day)}" ${x.day === s.shoot_on ? "selected" : ""}>${esc(dayLabel(x.day))}${x.phase ? ` · ${esc(x.phase.name)}` : ""}${x.past ? " (past)" : ""}</option>`).join("")}</select></label>
      <p class="muted small">Pin it to a day when it has to be filmed on that visit; otherwise it shows on every site day of its phase until it's filmed.</p>
      <p class="error-text" id="modal-error" role="alert" hidden></p>
      <div class="row end"><button type="button" class="btn" data-modal-close>Cancel</button><button type="submit" class="btn primary" id="pl-save">Save</button></div>
    </form>`, async (e) => {
    if (e.target.id !== "pl-save") return;
    const phase_id = $("#pl-phase").value ? Number($("#pl-phase").value) : null;
    const shoot_on = $("#pl-day").value || null;
    must(await sb.from("shots").update({ phase_id, shoot_on }).eq("id", shotId));
    closeModal();
    toast(shoot_on ? `Pinned to ${dayLabel(shoot_on)}.` : phase_id ? `Moved to ${d.phaseById.get(phase_id)?.name}.` : "Taken off the shoot plan.");
    await refresh();
  });
}

// Pick one of the project's Go shots (grouped by post). For attaching or moving footage.
export async function pickShot(pid, { title, intro = "", exclude = null, onPick }) {
  openModal(`<h2 id="modal-title">${esc(title)}</h2>${intro ? `<p class="muted">${intro}</p>` : ""}
    <div id="ps-list" aria-live="polite"><p class="muted"><span class="spin"></span> Loading shots…</p></div>`, async (e) => {
    const b = e.target.closest("[data-pick-shot]");
    if (!b || b.disabled) return;
    b.disabled = true;
    try { await onPick(Number(b.dataset.pickShot)); } catch (err) { b.disabled = false; toast(`Couldn't do that: ${err.message}`); return; }
    closeModal();
  }, true, "sheet");
  const ideas = must(await sb.from("ideas").select("id,title,position,shots(id,position,description,camera,filmed_at,takes(id))")
    .eq("project_id", pid).eq("decision", "go").neq("status", "dismissed").order("position").order("id"));
  const box = $("#ps-list");
  if (!box) return;
  const groups = ideas.map((i) => ({ ...i, shots: prepShots(i.shots) })).filter((i) => i.shots.length);
  box.innerHTML = groups.length ? groups.map((i) => `<h3>${esc(i.title)}</h3><ul class="ps-shots">${i.shots.map((s, k) => `<li>
      <button type="button" class="ps-shot" data-pick-shot="${esc(s.id)}" ${s.id === exclude ? "disabled" : ""}>
        <span class="shot-num" aria-hidden="true">${k + 1}</span><span class="ps-desc">${esc(s.description)}</span>
        <span class="muted small">${esc(CAMERA_LONG[s.camera || "any"])}${s.takes.length ? ` · ${plural(s.takes.length, "take")}` : s.filmed_at ? " · filmed" : ""}${s.id === exclude ? " · it's here now" : ""}</span>
      </button></li>`).join("")}</ul>`).join("")
    : `<p class="muted">No Go posts with shots in this project yet.</p>`;
}

export async function attachClip(clipId, shotId) {
  const { error } = await sb.from("takes").insert({ shot_id: shotId, clip_id: clipId, source: "manual" });
  if (error) throw new Error(error.code === "23505" ? "that clip is already on this shot" : error.message);
  toast("Footage put on the shot.");
  await refresh();
}

// Footage on a shot: move it to another shot (delete + insert) or take it off.
export async function openTake(takeId, shotId) {
  const t = must(await sb.from("takes").select("id,clip_id,shot_id,thumb_path,source,clips(title,duration,verdict,thumb_path),shots(ideas(project_id))").eq("id", takeId).maybeSingle());
  if (!t) { toast("That footage is already gone."); return; }
  const pid = t.shots?.ideas?.project_id;
  await signPaths(takePaths(t)).catch(() => {});
  const url = stillUrl(...takePaths(t));
  const c = t.clips || {};
  openModal(`<h2 id="modal-title">Footage on this shot</h2>
    <div class="take-detail">${url ? `<img class="take-big" src="${esc(url)}" alt="">` : ""}
      <div><p><strong>${esc(c.title || `Clip ${t.clip_id}`)}</strong></p><p class="muted small">${c.duration ? esc(mmss(c.duration)) : ""} ${retakeBadge(c)}
        ${t.note ? esc(t.note) : t.source === "auto" ? "Matched by Studio" : "Added by hand"}</p></div></div>
    <div class="row end"><button type="button" class="btn ghost danger" id="tk-remove">Remove from this shot</button>
      <button type="button" class="btn primary" id="tk-move">Move to another shot…</button></div>`, async (e) => {
    if (e.target.id === "tk-remove") {
      must(await sb.from("takes").delete().eq("id", takeId));
      closeModal();
      toast("Removed. The clip is in “Needs a home” if it isn't on another shot.");
      await refresh();
    } else if (e.target.id === "tk-move") {
      await pickShot(pid, {
        title: "Move footage to…", intro: esc(c.title || ""), exclude: shotId,
        onPick: async (to) => {
          const { error } = await sb.from("takes").insert({ shot_id: to, clip_id: t.clip_id, source: "manual" });
          if (error && error.code !== "23505") throw new Error(error.message);
          must(await sb.from("takes").delete().eq("id", takeId));
          toast("Footage moved.");
          await refresh();
        },
      });
    }
  });
}

async function tick(id, on) {
  const s = shoot.data?.shots.find((x) => x.id === id);
  if (s) { s.filmed_at = on ? new Date().toISOString() : null; s.filmed_by = on ? state.me?.id : null; }
  must(await sb.from("shots").update({ filmed_at: on ? new Date().toISOString() : null }).eq("id", id));
  toast(on ? "Ticked as filmed." : "Unticked.");
  await refresh();
}

async function queuePlace() {
  must(await sb.from("jobs").insert({ kind: "place", runner: "laptop", project_id: shoot.pid, state: "queued", payload: {} }));
  toast("Asked Claude to place the shots by phase.");
  await refresh();
}

// Clicks anywhere a shot shows (shoot plan, Today view, idea panel) for the shared modals.
document.addEventListener("click", async (e) => {
  const t = e.target.closest?.("button");
  if (!t || t.closest("#modal")) return;
  const ds = t.dataset;
  try {
    if (ds.shotPlace) await openPlace(Number(ds.shotPlace));
    else if (ds.take && ds.takeShot) await openTake(Number(ds.take), Number(ds.takeShot));
    else if (!t.closest("#shoot-view")) return;
    else if (ds.phaseEdit) openPhase(Number(ds.phaseEdit));
    else if (ds.phaseAdd != null) openPhase(null);
    else if (ds.dayAdd != null) openDay(null);
    else if (ds.dayEdit) openDay(Number(ds.dayEdit));
    else if (t.id === "sp-place") await queuePlace();
    else if (ds.clipPut) {
      const clip = shoot.data.homeless.find((c) => c.id === Number(ds.clipPut));
      await pickShot(shoot.pid, { title: "Put on a shot", intro: esc(clip?.title || ""), onPick: (sid) => attachClip(Number(ds.clipPut), sid) });
    }
  } catch (err) { toast(`Couldn't do that: ${err.message}`); }
});
document.addEventListener("change", (e) => {
  const c = e.target;
  if (!c.matches?.("#shoot-view [data-sp-filmed]")) return;
  tick(Number(c.dataset.spFilmed), c.checked).catch((err) => { toast(`Couldn't save: ${err.message}`); c.checked = !c.checked; });
});
// Remember which sections are open.
document.addEventListener("toggle", (e) => {
  const k = e.target.dataset?.openKey;
  if (!k || !e.target.closest("#shoot-view")) return;
  if (e.target.open) shoot.open.add(k); else shoot.open.delete(k);
  store("studio.shootOpen", JSON.stringify([...shoot.open].filter((x) => !x.startsWith("u:"))));
}, true);
// Drag a clip from "Needs a home" onto a shot (desktop).
document.addEventListener("dragstart", (e) => {
  const li = e.target.closest?.(".nh-clip[data-clip]");
  if (!li) return;
  e.dataTransfer.setData(clipDragType, li.dataset.clip);
  e.dataTransfer.setData("text/plain", li.querySelector(".nh-title")?.textContent || "");
  e.dataTransfer.effectAllowed = "copy";
  li.classList.add("dragging");
});
document.addEventListener("dragend", (e) => e.target.closest?.(".nh-clip")?.classList.remove("dragging"));
onClipDrop((clip, sid) => attachClip(clip, sid).catch((err) => toast(`Couldn't attach it: ${err.message}`)));
