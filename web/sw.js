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
    // 不在安装完成时 skipWaiting：页面加载中途切换 controller 会让
    // 旧 HTML 配上新外壳资源；由更新横幅引导用户在合适时刷新
  })());
});

self.addEventListener('message', (event) => {
  // 页面在用户确认刷新后发送 SKIP_WAITING，让等待中的新 Worker 激活；
  // 安装期不主动跳过等待，避免加载中途切换外壳资源
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          // 只清理本应用的外壳缓存代，不能删掉同源其它缓存
          .filter((key) => (
            key.startsWith('medix-shell-') && key !== CACHE_NAME
          ))
          .map((key) => caches.delete(key))
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

    if (request.mode === 'navigate' && url.pathname === '/') {
      // 深链（/?action=...）不在预缓存清单里：返回当前代的 '/' 外壳，
      // 不能把服务器上的新 HTML 写进旧缓存代，否则新 HTML 会配旧 JS/CSS。
      // ?action= 由 app.js 从 location.search 读取，深链功能不受影响。
      const shell = await cache.match('/');
      if (shell) return shell;
      try {
        return await fetch(request);
      } catch {
        return offlineResponse(request);
      }
    }

    try {
      const response = await fetch(request);
      if (response && response.ok) {
        try {
          await cache.put(request, response.clone());
        } catch (error) {
          // 缓存失败不能影响已经成功的网络响应
          console.warn('[medix-sw] 写入外壳缓存失败', error);
        }
      }
      return response;
    } catch {
      return offlineResponse(request);
    }
  })());
});

function waitForReminderAck(clientId, timeoutMs) {
  // 页面收到消息后回 ack；旧缓存页面/未受控页面收不到消息时
  // 超时返回 false，由调用方回退为整页导航
  return new Promise((resolve) => {
    let timer = null;
    const finish = (acked) => {
      if (timer !== null) clearTimeout(timer);
      self.removeEventListener('message', onMessage);
      resolve(acked);
    };
    const onMessage = (event) => {
      const data = event.data || {};
      if (data.type !== 'medix-open-reminders-ack') return;
      if (clientId && event.source && event.source.id !== clientId) return;
      finish(true);
    };
    timer = setTimeout(() => finish(false), timeoutMs);
    self.addEventListener('message', onMessage);
  });
}

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
    // 整页导航会中断正在进行的流式会诊（服务端取消任务、已消耗
    // token 丢失）。点击系统通知时窗口通常处于未聚焦状态，因此
    // 任何已存在的窗口都先走消息让页面自己切换视图，收到 ack 才
    // 算完成；收不到（旧缓存页面/未受控）才回退整页导航
    const focused = windows.find(
      (client) => client.focused && 'postMessage' in client
    );
    const anyWindow = windows.find(
      (client) => 'focus' in client && 'postMessage' in client
    );
    const targetWindow = focused || anyWindow;
    if (targetWindow) {
      await targetWindow.focus();
      let posted = false;
      try {
        targetWindow.postMessage({ type: 'medix-open-reminders' });
        posted = true;
      } catch {
        posted = false;
      }
      if (posted && await waitForReminderAck(targetWindow.id, 800)) {
        return;
      }
      if ('navigate' in targetWindow) {
        try {
          await targetWindow.navigate(target);
        } catch {
          // 导航失败时至少已聚焦窗口
        }
      }
      return;
    }
    await self.clients.openWindow(target);
  })());
});
