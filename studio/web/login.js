// Keeps the #/... part of the address through signing in: browsers never send it to the
// server, so a link like /#/production/12 would otherwise open Plan instead of the draft.
"use strict";
(() => {
  const next = document.querySelector('input[name="next"]');
  const hash = location.hash;
  if (!next || !/^#\/./.test(hash) || next.value.includes("#")) return;
  // Review's routes open on the dashboard; anything else belongs to the page it came from.
  next.value = (/^#\/(tab|production|clip|inbox)\//.test(hash) ? "/" : next.value) + hash;
})();
