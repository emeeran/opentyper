/* OpenTyper service worker — strictly opt-in (Settings → General).
   Cache name is keyed to the backend REV so upgrades never serve stale assets.
   Network-first for /api (never cache replies), cache-first for static assets. */

const REV = new URLSearchParams(self.location.search).get("rev") || "1";
const CACHE = `opentyper-static-${REV}`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll([
    "/", "/style.css", "/manifest.webmanifest", "/icon.svg",
    "/vendor/marked.min.js", "/vendor/highlight.min.js", "/vendor/dompurify.min.js",
  ])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("opentyper-static-") && k !== CACHE)
          .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api")) return; // network only, no caching
  event.respondWith(
    caches.match(event.request).then((hit) => {
      const fetched = fetch(event.request).then((res) => {
        if (res.ok && url.origin === self.location.origin) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, clone));
        }
        return res;
      }).catch(() => hit);
      return hit || fetched;
    }));
});
