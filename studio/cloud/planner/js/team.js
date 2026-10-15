// The Team page (owner only): who's on the team, invite by email, remove.
import { $, esc, ago, invoke, isOwner, state, toast } from "./core.js";

const el = () => $("#team");
let members = null;
let listNote = ""; // loading or error text while there's no list

export async function showTeam() {
  el().hidden = false;
  if (!isOwner()) {
    el().innerHTML = `<div class="team-wrap"><a class="btn" href="#/">‹ Plan</a><h1>Team</h1><p class="muted">Only the owner manages the team.</p></div>`;
    return;
  }
  members = null;
  listNote = `<p class="muted"><span class="spin"></span> Loading the team…</p>`;
  render();
  await load();
}

export function hideTeam() { el().hidden = true; }

async function load() {
  try {
    const data = await invoke("team", { method: "GET" });
    members = Array.isArray(data?.members) ? data.members : [];
    render();
  } catch (err) {
    members = null;
    listNote = `<p class="error-text">Couldn't load the team: ${esc(err.message)}</p><button class="btn" id="team-retry">Try again</button>`;
    render();
  }
}

function render(error = "") {
  const email = $("#invite-email")?.value || "", name = $("#invite-name")?.value || "";
  const list = !members ? listNote : (members.length ? `<ul class="people">${members.map((u) => `<li>
      <div class="person"><strong>${esc(u.name || u.email)}</strong>${u.id === state.me.id ? ` <span class="muted">(you)</span>` : ""}
        <span class="chip">${u.role === "owner" ? "Owner" : "Team"}</span>
        <div class="muted small">${esc(u.email)} · ${u.last_sign_in_at ? `last signed in ${esc(ago(u.last_sign_in_at))}` : u.invited_at ? `invited ${esc(ago(u.invited_at))}, hasn't signed in yet` : "hasn't signed in yet"}</div></div>
      ${u.id !== state.me.id && u.role !== "owner" ? `<button class="btn small" data-remove-user="${esc(u.id)}" data-name="${esc(u.name || u.email)}">Remove</button>` : ""}
    </li>`).join("")}</ul>` : `<p class="muted">Just you so far.</p>`);
  el().innerHTML = `<div class="team-wrap">
    <div class="row"><a class="btn" href="#/" aria-label="Back to the plan">‹ Plan</a></div>
    <h1>Team</h1>
    <p class="muted">Team members can plan, vote, comment, tick off shots and attach footage. Go / Park, Dismiss and Make this stay with you.</p>
    <h2>People</h2>
    ${list}
    <h2>Invite someone</h2>
    <form class="form invite-form" id="invite-form" novalidate>
      <div class="row">
        <label class="grow">Name<input id="invite-name" maxlength="80" autocomplete="off" placeholder="e.g. Wei" value="${esc(name)}"></label>
        <label class="grow">Email<input id="invite-email" type="email" maxlength="200" autocomplete="off" placeholder="name@example.com" value="${esc(email)}"></label>
      </div>
      <p class="error-text" id="invite-error" role="alert" ${error ? "" : "hidden"}>${esc(error)}</p>
      <div class="row"><button type="submit" class="btn primary" id="invite-go">Send invite</button>
        <span class="muted small">They get an email with a link to set their password and sign in.</span></div>
    </form>
  </div>`;
}

el().addEventListener("submit", async (e) => {
  e.preventDefault();
  if (e.target.id !== "invite-form") return;
  const email = $("#invite-email").value.trim(), name = $("#invite-name").value.trim();
  if (!/^\S+@\S+\.\S+$/.test(email)) { render("Enter their email address."); $("#invite-email").focus(); return; }
  if (!name) { render("Add their name, so the team knows who's who."); $("#invite-name").focus(); return; }
  $("#invite-go").disabled = true;
  try {
    await invoke("team", { body: { action: "invite", email, name, redirect_to: location.origin } });
  } catch (err) {
    render(`Couldn't invite them: ${err.message}`);
    return;
  }
  $("#invite-email").value = "";
  $("#invite-name").value = "";
  toast(`Invite sent to ${email}.`);
  await load();
});

el().addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "team-retry") { await load(); return; }
  if (!btn.dataset.removeUser) return;
  const who = btn.dataset.name;
  if (!confirm(`Remove ${who}? They're signed out and can't sign in again. What they planned stays.`)) return;
  btn.disabled = true;
  try {
    await invoke("team", { body: { action: "remove", user_id: btn.dataset.removeUser } });
    toast(`${who} removed.`);
  } catch (err) {
    toast(`Couldn't remove ${who}: ${err.message}`);
  }
  await load();
});
