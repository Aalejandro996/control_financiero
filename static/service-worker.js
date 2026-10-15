const CACHE_NAME = 'control-financiero-shell-v6';
const SHELL = ['/', '/static/manifest.webmanifest', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Authentication and financial API requests are never cached.
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).then((response) => {
    if (response.ok && (url.pathname === '/' || url.pathname.startsWith('/static/'))) {
      const clone = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
    }
    return response;
  }).catch(async () => (await caches.match(event.request)) || (await caches.match('/'))));
});
