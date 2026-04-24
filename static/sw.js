// SkyWatch Service Worker — caches map tiles
const CACHE_NAME = 'skywatch-tiles-v1';
const TILE_PATTERNS = [
    /basemaps\.cartocdn\.com/,
    /api\.cesium\.com/,
    /assets\.cesium\.com/,
    /api\.mapbox\.com/,
    /\.tile\./,
    /tiles\./,
    /s3-eu-west-1\.amazonaws\.com\/jamcams/,  // TfL camera images
];

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = event.request.url;

    // Only cache GET requests matching tile patterns
    if (event.request.method !== 'GET') return;

    const isTile = TILE_PATTERNS.some(p => p.test(url));
    if (!isTile) return;

    event.respondWith(
        caches.open(CACHE_NAME).then(cache =>
            cache.match(event.request).then(cached => {
                if (cached) return cached;
                return fetch(event.request).then(response => {
                    if (response.ok) {
                        cache.put(event.request, response.clone());
                    }
                    return response;
                }).catch(() => cached);
            })
        )
    );
});
