/* MediX 离线外壳：只缓存静态资源，不缓存任何 /api/ 数据。 */
// __MEDIX_BUILD__ 由服务端替换为前端资源构建号：任一页面资源变化都会
// 改变 sw.js 字节，触发浏览器安装新 Worker 与新缓存代。
const CACHE_NAME = 'medix-shell-__MEDIX_BUILD__';
const SHELL = [
  '/',
  '/static/styles.css',
  '/static/app.js',
  '/static/lab-prefill-core.js',
  '/static/theme-init.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      // 整代原子安装；任一资源失败就放弃新缓存，继续使用完整旧版本，
      // 不能把不同版本的 index/app.js 混在同一个缓存里
      await cache.addAll(SHELL);
    } catch (error) {
      console.warn('[medix-sw] 外壳安装失败，保留旧版本缓存', error);
      await caches.delete(CACHE_NAME);
      // 必须让 install 失败：否则新 Worker 会激活并删除旧缓存，
      // 而新缓存又不完整，离线外壳会彻底丢失
      throw error;
    }
    await self.skipWaiting();
  })());
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
    // 外壳优先用当前缓存代，保证 HTML/JS/CSS 来自同一版本；
    // 新版本通过新的 sw.js 构建号安装后整体切换
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) return cached;

    try {
      const response = await fetch(request);
      if (response && response.ok) {
        await cache.put(request, response.clone());
      }
      return response;
    } catch {
      return offlineResponse(request);
    }
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    // 提醒通知点击后聚焦已打开的窗口并跳到提醒面板；
    // 没有窗口时新开一个，由前端 handleLaunchAction 处理 ?action=reminders
    const target = '/?action=reminders';
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of windows) {
      if ('focus' in client) {
        await client.focus();
        if ('navigate' in client) {
          try {
            await client.navigate(target);
          } catch {
            // 导航失败时至少已聚焦窗口
          }
        }
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
