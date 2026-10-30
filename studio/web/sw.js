// Studio's service worker: opening the home-screen app shows the last copy of the page at once,
// instead of first waiting for the server in Europe (from Singapore, connecting and asking costs
// most of a second). Only the two pages themselves are kept, "/" (Review) and "/plan/" (Plan):
// never the data (Supabase, /api/...), sign-in, uploads, pictures or video. Their scripts and
// styles already stay in the browser by their ?v= addresses (webassets.py).
//
// Each open also asks the server for the page in the background and keeps the new copy. When the
// page has changed (Studio was updated) and nobody has touched it yet, it reloads itself.
// Switched off with STUDIO_SERVICE_WORKER=off (this file then removes itself), and skipped for one
// visit with ?sw=off on the address.
// studio-pages-2 (2026-10-30): the first version never replaced a kept page (it read the old copy after
// the page had taken it, which fails), so a browser kept opening the page from the day it first saved
// it (Bryan: no Grid in More, an empty Posting calendar, until Ctrl+Shift+R). The new name drops those.
const CACHE = "studio-pages-2";
const PAGES = ["/", "/plan/"];

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const name of await caches.keys()) if (name !== CACHE) await caches.delete(name);
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.mode !== "navigate" || req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.search || !PAGES.includes(url.pathname)) return;
  e.respondWith(page(e, url.pathname));
});

async function page(e, path) {
  const cache = await caches.open(CACHE);
  const kept = await cache.match(path);
  // Read now: once the page has taken the kept copy, it can't be read again.
  const was = kept ? kept.clone().text() : Promise.resolve(null);
  const fresh = fetch(e.request).then(async (res) => {
    if (res.type === "basic" && res.status === 200 && (res.headers.get("Content-Type") || "").startsWith("text/html")) {
      const text = await res.clone().text();
      const before = await was;
      await cache.put(path, res.clone());
      if (before !== null && before !== text) await updated(e.resultingClientId || e.clientId, path);
    } else {
      // Signed out (sent to the sign-in page) or the page is gone: open from the server next time.
      await cache.delete(path);
    }
    return res;
  });
  if (kept) {
    e.waitUntil(fresh.catch(() => {}));
    return kept;
  }
  return fresh;
}

// Tell the page that opened from the old copy (it reloads if nobody has touched it yet).
async function updated(id, path) {
  for (let i = 0; i < 20; i++) {
    const list = await self.clients.matchAll({ type: "window" });
    const c = list.find((w) => w.id === id) || list.find((w) => new URL(w.url).pathname === path);
    if (c) { c.postMessage({ studio: "updated" }); return; }
    await new Promise((r) => setTimeout(r, 250));
  }
}
