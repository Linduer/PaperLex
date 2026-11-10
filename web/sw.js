/* PaperLex Service Worker：外壳预缓存 + 音频按需缓存 + 词表网络优先 */
const CACHE = "paperlex-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest",
  "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // 发音 mp3：缓存优先（生成一次永久有效）
  if (url.pathname.endsWith(".mp3")) {
    e.respondWith(
      caches.open(CACHE).then(async c => {
        const hit = await c.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) c.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  // 词表 API：网络优先，离线回退到缓存
  if (url.pathname === "/api/terms") {
    e.respondWith(
      fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // 页面外壳：网络优先，失败回退缓存（支持离线打开）
  e.respondWith(
    fetch(req).catch(() => caches.match(req).then(r => r || caches.match("/")))
  );
});
