const CACHE = 'vyapara-track-v3';

const ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png'
];

// Install new service worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Delete old caches and activate immediately
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys =>
        Promise.all(
          keys
            .filter(key => key !== CACHE)
            .map(key => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

// Handle requests
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Never cache API / Supabase requests
  if (url.pathname.startsWith('/api/')) return;

  // Always get latest HTML
  if (
    url.pathname === '/' ||
    url.pathname === '/index.html'
  ) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const copy = response.clone();

          caches.open(CACHE).then(cache => {
            cache.put(event.request, copy);
          });

          return response;
        })
        .catch(() => caches.match(event.request))
    );

    return;
  }

  // Always get latest manifest
  if (url.pathname === '/manifest.json') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const copy = response.clone();

          caches.open(CACHE).then(cache => {
            cache.put(event.request, copy);
          });

          return response;
        })
        .catch(() => caches.match('/manifest.json'))
    );

    return;
  }

  // Always get latest app icons
  if (
    url.pathname === '/icon-192.png' ||
    url.pathname === '/icon-512.png'
  ) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const copy = response.clone();

          caches.open(CACHE).then(cache => {
            cache.put(event.request, copy);
          });

          return response;
        })
        .catch(() => caches.match(event.request))
    );

    return;
  }

  // Other files: cache first, then network
  event.respondWith(
    caches.match(event.request)
      .then(cachedResponse => {
        if (cachedResponse) {
          return cachedResponse;
        }

        return fetch(event.request)
          .then(response => {
            const copy = response.clone();

            caches.open(CACHE).then(cache => {
              cache.put(event.request, copy);
            });

            return response;
          })
          .catch(() => caches.match('/index.html'));
      })
  );
});
