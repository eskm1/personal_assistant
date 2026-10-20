// Sign in, forgotten password, and "Set your password" after an invite or reset link.
// Sign-ups are off: people are invited by the owner from the Team page.
import { $, esc, sb, underPlan } from "./core.js";

const card = () => $("#auth-card");
let onSignedIn = null;

export function showAuth(view, opts = {}) {
  $("#app").hidden = true;
  $("#auth").hidden = false;
  if (opts.onSignedIn) onSignedIn = opts.onSignedIn;
  const error = opts.error ? `<div class="auth-error" role="alert">${esc(opts.error)}</div>` : "";
  const info = opts.info ? `<div class="auth-info" role="status">${esc(opts.info)}</div>` : "";
  const html = {
    signin: `<h1>Studio</h1><p>Sign in to plan posts with the team.</p>${error}${info}
      <form id="auth-form" data-auth="signin" novalidate>
        <label for="a-email">Email</label>
        <input id="a-email" type="email" autocomplete="email" required value="${esc(opts.email || "")}">
        <label for="a-password">Password</label>
        <input id="a-password" type="password" autocomplete="current-password" required>
        <button type="submit" class="auth-primary">Sign in</button>
      </form>
      <button class="auth-link" data-auth-go="forgot">Forgot your password?</button>`,
    forgot: `<h1>Reset your password</h1><p>We'll email you a link. It signs you in here so you can pick a new password.</p>${error}
      <form id="auth-form" data-auth="forgot" novalidate>
        <label for="a-email">Email</label>
        <input id="a-email" type="email" autocomplete="email" required value="${esc(opts.email || "")}">
        <button type="submit" class="auth-primary">Send reset link</button>
      </form>
      <button class="auth-link" data-auth-go="signin">Back to sign in</button>`,
    sent: `<h1>Check your email</h1><p>If ${esc(opts.email || "that address")} has a Studio account, a reset link is on its way. Open it on this device.</p>
      <button class="auth-link" data-auth-go="signin">Back to sign in</button>`,
    set: `<h1>${opts.invite ? "Welcome to Studio" : "Set a new password"}</h1>
      <p>${opts.invite ? `Hi ${esc(opts.name || "there")}. Pick a password: you'll use it to sign in here and on your phone.` : `Signed in as ${esc(opts.email || "you")}. Pick a new password.`}</p>${error}
      <form id="auth-form" data-auth="set" novalidate>
        <input type="email" autocomplete="username" value="${esc(opts.email || "")}" hidden readonly>
        <label for="a-new">New password (10+ characters)</label>
        <input id="a-new" type="password" autocomplete="new-password" minlength="10" required>
        <label for="a-again">New password again</label>
        <input id="a-again" type="password" autocomplete="new-password" minlength="10" required>
        <button type="submit" class="auth-primary">Set password and continue</button>
      </form>
      <button class="auth-link" data-auth-go="signout">Sign out</button>`,
    gone: `<h1>No access</h1><p>${esc(opts.email || "This account")} isn't on the Studio team any more. Ask the owner if that's a mistake.</p>
      <button class="auth-link" data-auth-go="signout">Sign in as someone else</button>`,
    blocked: `<h1>No access to planning</h1>${error}<a class="auth-link" href="${underPlan ? "/#/tab/review" : "#/"}">Back to Studio</a>
      <button class="auth-link" data-auth-go="signout">Sign in as someone else</button>`,
    noconfig: `<h1>Not set up yet</h1><p>This site has no Supabase settings. Set SUPABASE_URL and SUPABASE_ANON_KEY and build again (node build-config.mjs).</p>`,
    loading: `<p class="muted"><span class="spin"></span> Loading…</p>`,
  }[view];
  card().innerHTML = html;
  card().dataset.view = view;
  card().dataset.invite = opts.invite ? "1" : "";
  card().dataset.email = opts.email || "";
  card().dataset.name = opts.name || "";
  card().querySelector("input:not([hidden]):not([value='']), input:not([hidden])")?.focus();
  if (view === "signin" && opts.email) $("#a-password")?.focus();
}

export function hideAuth() {
  $("#auth").hidden = true;
  card().innerHTML = "";
}

// The press answers at once: "Signing in…" (or "Sending…", "Saving…") until the answer comes.
function busy(on, label = "") {
  const btn = card().querySelector(".auth-primary");
  if (!btn) return;
  btn.disabled = on;
  btn.classList.toggle("busy", on);
  if (on && label) { btn.dataset.label = btn.textContent; btn.textContent = label; } else if (!on && btn.dataset.label) btn.textContent = btn.dataset.label;
}

function friendly(message) {
  if (/invalid login credentials/i.test(message)) return "That email and password don't match.";
  if (/email not confirmed/i.test(message)) return "Open the invite link in your email first.";
  if (/rate limit|too many/i.test(message)) return "Too many tries. Wait a minute and try again.";
  if (/same.*password|different from the old/i.test(message)) return "Pick a password you haven't used here before.";
  if (/failed to fetch|network/i.test(message)) return "Can't reach Studio. Check your connection.";
  return message;
}

$("#auth").addEventListener("submit", async (e) => {
  e.preventDefault();
  const kind = e.target.dataset.auth;
  const v = card().dataset;
  if (kind === "signin") {
    const email = $("#a-email").value.trim(), password = $("#a-password").value;
    if (!email || !password) return showAuth("signin", { email, error: "Enter your email and password." });
    busy(true, "Signing in…");
    const { data, error } = await sb.auth.signInWithPassword({ email, password });
    if (error) return showAuth("signin", { email, error: friendly(error.message) });
    onSignedIn?.(data.session);
  } else if (kind === "forgot") {
    const email = $("#a-email").value.trim();
    if (!/^\S+@\S+\.\S+$/.test(email)) return showAuth("forgot", { email, error: "Enter the email you sign in with." });
    busy(true);
    const { error } = await sb.auth.resetPasswordForEmail(email, { redirectTo: location.origin + location.pathname });
    if (error) return showAuth("forgot", { email, error: friendly(error.message) });
    showAuth("sent", { email });
  } else if (kind === "set") {
    const next = $("#a-new").value, again = $("#a-again").value;
    const opts = { invite: !!v.invite, email: v.email, name: v.name };
    if (next.length < 10) return showAuth("set", { ...opts, error: "Use at least 10 characters." });
    if (next !== again) return showAuth("set", { ...opts, error: "The two passwords don't match." });
    busy(true);
    const { error } = await sb.auth.updateUser({ password: next });
    if (error) return showAuth("set", { ...opts, error: friendly(error.message) });
    try { sessionStorage.removeItem("studio.setPassword"); } catch { /* private mode */ }
    const { data } = await sb.auth.getSession();
    onSignedIn?.(data.session, { passwordSet: true });
  }
});

$("#auth").addEventListener("click", async (e) => {
  const go = e.target.closest("[data-auth-go]")?.dataset.authGo;
  if (!go) return;
  const email = $("#a-email")?.value.trim() || card().dataset.email || "";
  if (go === "signout") {
    try { sessionStorage.removeItem("studio.setPassword"); } catch { /* private mode */ }
    await sb.auth.signOut().catch(() => {});
    // Under the dashboard (/plan/) the dashboard login is the planner's: sign out of both.
    if (underPlan) {
      await fetch("/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
      location.href = "/login?next=/plan/";
      return;
    }
    showAuth("signin", { email });
  } else showAuth(go, { email });
});
