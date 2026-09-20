// Intentionally does no caching. This app is fully dynamic (live sign-in,
// live transcription), so caching responses would risk showing stale data.
// A registered service worker with a fetch handler is still required by
// Chrome/Android for the "Add to Home Screen" install prompt to appear.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
