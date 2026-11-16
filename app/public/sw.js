// The minimum a browser wants before it will offer to install the page, and
// nothing beyond it.
//
// Deliberately no cache: this page is a window onto a session that is running
// somewhere else, and a stale shell serving a stale transcript would be worse
// than no page at all. The fetch handler exists because installability wants
// one; it does nothing but let the request through.

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', () => {})
