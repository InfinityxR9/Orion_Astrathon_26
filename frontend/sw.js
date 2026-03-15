/**
 * Aurora Forecast Platform - Service Worker
 * Strategy:
 *   Static assets & navigation  -> cache-first, network fallback
 *   API data routes              -> network-first, serve cached copy on failure
 *   Map tiles (CartoDB)          -> cache-first, network fallback
 *
 * Correctness rules:
 *   1. WebSocket upgrades (mode="websocket") MUST be ignored.
 *      Their 101 response cannot be cloned and throws "body already used".
 *   2. Response.clone() MUST be called synchronously before any async
 *      caches.open() call, otherwise the body is consumed before we can store it.
 */

const CACHE_NAME = "aurora-forecast-v3";

const STATIC_PRECACHE = [
    "/",
    "/static/style.css",
    "/static/app.js",
];

const API_PATHS = [
    "/solar-wind",
    "/aurora-grid",
    "/alerts",
    "/terminator",
    "/kp-timeline",
    "/visibility-score",
    "/visibility",
    "/photo-settings",
    "/sightings",
    "/better-viewing-spot",
    "/bz-history",
];

// Install: pre-cache the app shell
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(STATIC_PRECACHE))
            .then(() => self.skipWaiting())
    );
});

// Activate: evict old caches
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) =>
                Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
            )
            .then(() => self.clients.claim())
    );
});

// Fetch
self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") return;

    // WebSocket upgrades cannot be intercepted - bail immediately
    if (event.request.mode === "websocket") return;

    const url = new URL(event.request.url);
    if (url.protocol === "ws:" || url.protocol === "wss:") return;

    // Map tiles: cache-first, network fallback
    if (url.hostname.endsWith("cartocdn.com") || url.hostname.endsWith("openstreetmap.org")) {
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(event.request).then((hit) => {
                    if (hit) return hit;
                    return fetch(event.request).then((resp) => {
                        if (resp.ok) {
                            const toCache = resp.clone(); // clone before any async op
                            cache.put(event.request, toCache);
                        }
                        return resp;
                    });
                })
            )
        );
        return;
    }

    // API routes: network-first, cached fallback when offline
    const isApi = API_PATHS.some(
        (p) => url.pathname === p || url.pathname.startsWith(p + "?") || url.pathname.startsWith(p + "/")
    );
    if (url.hostname === self.location.hostname && isApi) {
        event.respondWith(
            fetch(event.request)
                .then((resp) => {
                    if (resp.ok) {
                        const toCache = resp.clone(); // clone synchronously before caches.open()
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, toCache));
                    }
                    return resp;
                })
                .catch(() =>
                    caches.match(event.request).then((cached) => {
                        if (cached) return cached;
                        return new Response(
                            JSON.stringify({ error: "offline", cached: false }),
                            { status: 503, headers: { "Content-Type": "application/json" } }
                        );
                    })
                )
        );
        return;
    }

    // Static assets & navigation: cache-first, network fallback
    if (url.hostname === self.location.hostname) {
        event.respondWith(
            caches.match(event.request).then((hit) => {
                if (hit) return hit;
                return fetch(event.request).then((resp) => {
                    if (resp.ok) {
                        const toCache = resp.clone(); // clone synchronously
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, toCache));
                    }
                    return resp;
                }).catch(async () => {
                    // For navigations, serve cached shell so app can still boot offline.
                    if (event.request.mode === "navigate") {
                        const shell = await caches.match("/");
                        if (shell) return shell;
                    }
                    const cached = await caches.match(event.request);
                    if (cached) return cached;
                    return new Response("offline", { status: 503, statusText: "Offline" });
                });
            })
        );
    }
});
