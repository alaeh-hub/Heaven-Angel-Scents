/* Service worker for the installed app.
 *
 * This is a live operations system behind a sign-in, so pages and data are
 * NEVER cached: every navigation and API call goes to the network. The only
 * thing kept is static files (CSS, JS, fonts, icons), served stale and
 * refreshed in the background so the app opens fast. If the network is down
 * when a page is opened, a short offline notice is shown instead of the
 * browser's error page. */
const CACHE_PREFIX = 'ha-static-';
const CACHE = CACHE_PREFIX + 'v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function offlinePage() {
  const html = '<!doctype html><html lang="en"><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width, initial-scale=1">'
    + '<title>Offline</title>'
    + '<style>:root{color-scheme:dark light}body{margin:0;min-height:100dvh;display:grid;place-items:center;'
    + 'font:16px/1.5 system-ui,sans-serif;background:Canvas;color:CanvasText;text-align:center;padding:1.5rem}'
    + 'h1{font-size:1.25rem;margin:0 0 .5rem}p{margin:0 0 1rem;opacity:.8}'
    + 'button{font:inherit;padding:.5rem 1rem;border-radius:.5rem;border:1px solid currentColor;'
    + 'background:transparent;color:inherit;cursor:pointer}</style>'
    + '<main><h1>You are offline</h1><p>Heaven &amp; Angel Scents needs a connection to load your records.</p>'
    + '<button type="button" onclick="location.reload()">Try again</button></main></html>';
  return new Response(html, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(offlinePage));
    return;
  }

  if (url.pathname.startsWith('/static/') && !url.pathname.startsWith('/static/uploads/')) {
    const cachePromise = caches.open(CACHE);
    const networkPromise = fetch(req);
    const refresh = cachePromise.then((cache) =>
      networkPromise.then((res) => {
        if (res && res.ok) {
          return cache.put(req, res.clone()).then(() => res);
        }
        return res;
      })
    );
    event.waitUntil(refresh.catch((err) => {
      console.warn('[pwa] Static asset cache refresh failed:', err);
    }));
    event.respondWith(
      cachePromise.then((cache) => cache.match(req))
        .then((hit) => hit || networkPromise)
    );
  }
});
