/* MediX 离线外壳：只缓存静态资源，不缓存任何 /api/ 数据。 */
const CACHE_NAME = 'medix-shell-v2';
const SHELL = [
  '/',
  '/static/styles.css',
  '/static/app.js',
  '/static/theme-init.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

//: 有缓存时最多等网络这么久；超时先用缓存，避免网络挂起导致白屏
const NETWORK_TIMEOUT_MS = 3500;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(async (cache) => {
        const results = await Promise.allSettled(
          SHELL.map((url) => cache.add(url))
        );
        const failed = [];
        results.forEach((result, index) => {
          if (result.status === 'rejected') failed.push(SHELL[index]);
        });
        if (!failed.length) return;

        // 升级期间个别资源可能暂时不可用，先从旧版本缓存补齐再删旧缓存
        const oldKeys = (await caches.keys())
          .filter((key) => key !== CACHE_NAME);
        for (const key of oldKeys) {
          const oldCache = await caches.open(key);
          for (const url of failed) {
            if (await cache.match(url)) continue;
            const response = await oldCache.match(url);
            if (response) await cache.put(url, response);
          }
        }

        const stillMissing = [];
        for (const url of SHELL) {
          if (!(await cache.match(url))) stillMissing.push(url);
        }
        if (stillMissing.length) {
          console.warn(
            `[medix-sw] ${stillMissing.length} 个外壳资源未能缓存，离线功能可能不完整`
          );
        }
      })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

//: 网络响应成功时顺手写入缓存；失败时返回 null，由调用方回退缓存
function fetchAndCache(request) {
  return fetch(request).then(
    (response) => {
      if (response && response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME)
          .then((cache) => cache.put(request, copy))
          .catch(() => {});
      }
      return response;
    },
    () => null
  );
}

function offlineResponse(request) {
  return caches.match('/').then((shell) => {
    if (request.mode === 'navigate' && shell) return shell;
    return new Response('离线状态下无法加载该资源。', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  });
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith((async () => {
    const cached = await caches.match(request);
    const network = fetchAndCache(request);

    if (!cached) {
      // 没有缓存时等待网络（即使较慢），失败才显示离线提示
      const response = await network;
      if (response) return response;
      return offlineResponse(request);
    }

    const timeout = new Promise((resolve) => {
      setTimeout(() => resolve(null), NETWORK_TIMEOUT_MS);
    });
    const response = await Promise.race([network, timeout]);
    // 超时先用缓存；网络响应稍后返回时仍会写入缓存
    return response || cached;
  })());
});
