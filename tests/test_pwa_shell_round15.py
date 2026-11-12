"""PWA 外壳缓存代与更新提示的回归测试（静态检查）。"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
SW = (WEB / "sw.js").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_service_worker_does_not_skip_waiting_mid_load():
    """新 Worker 不能在页面加载中途接管，否则旧 HTML 会配新资源。"""
    install = SW[SW.index("addEventListener('install'"):SW.index(
        "addEventListener('message'"
    )]
    assert "self.skipWaiting()" not in install


def test_navigation_serves_current_shell_instead_of_caching_new_html():
    fetch = SW[SW.index("addEventListener('fetch'"):]
    assert "request.mode === 'navigate'" in fetch
    assert "await cache.match('/')" in fetch
    # 导航分支必须在 fetch 之前返回，不能把新 HTML 写入旧缓存代
    navigate_index = fetch.index("request.mode === 'navigate'")
    put_index = fetch.index("cache.put(request")
    assert navigate_index < put_index


def test_activate_only_deletes_app_shell_caches():
    activate = SW[SW.index("addEventListener('activate'"):SW.index(
        "function offlineResponse"
    )]
    assert "key.startsWith('medix-shell-')" in activate
    assert "key !== CACHE_NAME" in activate


def test_cache_put_failure_keeps_network_response():
    fetch = SW[SW.index("addEventListener('fetch'"):]
    assert "写入外壳缓存失败" in fetch
    # cache.put 在独立 try 内，不能落入返回离线响应的 catch
    put_index = fetch.index("cache.put(request")
    offline_index = fetch.index("return offlineResponse(request)", put_index)
    assert offline_index > put_index


def test_skip_waiting_only_after_user_refresh():
    # install 不主动跳过等待，但页面可在用户点刷新后发消息激活
    assert "SKIP_WAITING" in SW
    assert "self.skipWaiting()" in SW
    assert "waiting.postMessage" in APP
    assert "activateServiceWorkerUpdate" in APP


def test_navigation_shell_only_for_root_path():
    # 深链只限 '/'；/docs、/redoc 等页面不能被替换成应用外壳
    assert "url.pathname === '/'" in SW


def test_update_banner_survives_status_warnings():
    banner = APP[APP.index("function showBanner(html)"):APP.index(
        "function hideBanner()"
    )]
    assert "banner-update" in banner
    assert "swUpdateReady" in banner
    assert "sw-refresh" in banner

    hide = APP[APP.index("function hideBanner()"):APP.index(
        "function hideBanner()"
    ) + 800]
    # 警告恢复后只保留更新提示，不能把旧警告留在横幅里
    assert "banner-update" in hide
    assert "appendChild(updateBlock)" in hide
