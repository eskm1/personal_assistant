// Keeps the #/... part of the address through signing in: browsers never send it to the
// server, so a link like /#/production/12 would otherwise open Plan instead of the draft.
// Says what's behind the sign-in (a draft from a Telegram link), and answers the press at once.
"use strict";
(() => {
  const next = document.querySelector('input[name="next"]');
  const hash = location.hash;
  if (next && /^#\/./.test(hash) && !next.value.includes("#")) {
    // Review's routes open on the dashboard; anything else belongs to the page it came from.
    next.value = (/^#\/(tab|production|clip|inbox)\//.test(hash) ? "/" : next.value) + hash;
  }
  const why = document.getElementById("login-why");
  if (why && next && /#\/production\/\d+/.test(next.value)) why.textContent = "Sign in to see the draft waiting for you. Use your work email and your Studio password.";
  // "Signing in…" the moment it's pressed (the answer takes a second or two from far away).
  const form = document.querySelector("form");
  const go = document.getElementById("login-go");
  if (form && go) {
    form.addEventListener("submit", (e) => {
      if (go.getAttribute("aria-busy") === "true") { e.preventDefault(); return; }
      go.setAttribute("aria-busy", "true");
      go.textContent = next ? "Signing in…" : "Creating your account…";
    });
    // Back from the next page: the button is ready again.
    window.addEventListener("pageshow", () => { go.removeAttribute("aria-busy"); go.textContent = next ? "Sign in" : "Create account"; });
  }
})();
