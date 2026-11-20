// Service Worker for Smart Booth — Lightweight App Shell PWA
const CACHE_NAME = 'smartbooth-pwa-v22';

const PRECACHE_ASSETS = [
  '/manifest.json',
  '/static/css/pwa_mobile.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-192-maskable.png',
  '/static/icons/icon-512-maskable.png',
  '/static/icons/apple-touch-icon.png',
  '/static/icons/favicon-32.png',
  '/static/icons/favicon-16.png',
  '/static/js/offline_db.js',
  '/static/js/app_state.js',
  '/static/js/omnisearch.js',
  '/static/js/voter_cards.js',
  '/static/js/slip_engine.js',
  '/static/js/history_intel.js',
  '/static/js/pwa_manager.js',
  '/static/js/caste_census.js',
  '/static/js/karyakartas.js',
  '/static/js/turnout_live.js',
  '/static/js/voter_management_tagger.js',
  '/static/js/voting_day.js',
  '/static/js/warroom.js',
  '/static/js/bulk_slip_dispatch.js'
];

// Message: Allow clients to prompt skipWaiting during update
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Install: precache the core app shell bypassing stale browser HTTP cache
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.all(
        PRECACHE_ASSETS.map((asset) => {
          return fetch(asset, { cache: 'reload' }).then((response) => {
            if (response && response.ok) {
              return cache.put(asset, response);
            }
          }).catch((err) => {
            console.warn('PWA Precache info for ' + asset + ':', err);
          });
        })
      );
    })
  );
});

// Activate: purge older caches and claim clients immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('PWA: Purging outdated cache:', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: Lean caching strategy
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET requests
  if (request.method !== 'GET') return;

  // 1. Navigation requests: Network-First with automatic shell caching for offline use
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            const u = new URL(request.url);
            const tenantParam = u.searchParams.get('tenant');
            const tenantCopy = tenantParam ? copy.clone() : null;

            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, copy);
              if (tenantParam && tenantCopy) {
                const baseTenantUrl = `${u.pathname}?tenant=${encodeURIComponent(tenantParam)}`;
                cache.put(baseTenantUrl, tenantCopy);
              }
            }).catch((err) => {
              console.warn('[SW] Navigation cache put error:', err);
            });
          }
          return response;
        })
        .catch(async () => {
          const cached = await caches.match(request);
          if (cached) return cached;
          const u = new URL(request.url);
          const pathCached = await caches.match(u.pathname + u.search);
          if (pathCached) return pathCached;
          if (u.searchParams.has('tenant')) {
            const baseTenantUrl = `${u.pathname}?tenant=${encodeURIComponent(u.searchParams.get('tenant'))}`;
            const tenantCached = await caches.match(baseTenantUrl);
            if (tenantCached) return tenantCached;
          }
          const rootCached = await caches.match('/');
          if (rootCached) return rootCached;
          const cache = await caches.open(CACHE_NAME);
          const keys = await cache.keys();
          for (const k of keys) {
            if (k.url.includes('tenant=') || k.url.endsWith('/') || k.mode === 'navigate') {
              const fallback = await cache.match(k);
              if (fallback) return fallback;
            }
          }
          return new Response('Offline. Please reconnect to access SmartBooth.', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: new Headers({ 'Content-Type': 'text/plain; charset=utf-8' })
          });
        })
    );
    return;
  }

  // 2. Static UI assets & scripts: Stale-While-Revalidate for instant loading
  if (
    url.pathname.startsWith('/static/') ||
    url.origin.includes('fonts.googleapis.com') ||
    url.origin.includes('fonts.gstatic.com') ||
    url.origin.includes('cdn.tailwindcss.com')
  ) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const networkFetch = fetch(request).then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        }).catch(() => cached);

        return cached || networkFetch;
      })
    );
    return;
  }

  // 3. Booth list: Cache with network fallback for offline dropdowns
  if (url.pathname === '/api/booths') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(async () => {
          return caches.match(request);
        })
    );
    return;
  }

  // 4. Other API Calls: Network-Only for fresh real-time search & slips
  // Do not cache /api/ responses to ensure voter queries are 100% live.
});
