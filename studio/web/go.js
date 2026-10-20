// Studio opens on Plan when the planner is served here (data-plan-here on <html>): leave for it
// before Review's styles and script load. The home-screen app starts at "/", so this runs on every
// open; app.js has the same rule (and stops there) in case this file didn't.
(function () {
  var plan = document.documentElement.getAttribute("data-plan-here");
  var h = location.hash;
  if (plan && (!h || h === "#" || h === "#/" || /^#\/(tab\/plan|plan\/\d+|plan-idea\/\d+|checklist\/\d+)$/.test(h))) location.replace(plan);
})();
