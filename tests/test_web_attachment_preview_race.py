"""附件查看请求竞态的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_attachment_preview_ignores_stale_responses():
    assert "let attachmentPreviewRequestSeq = 0;" in SCRIPT
    assert (
        "const requestSeq = download ? 0 : ++attachmentPreviewRequestSeq;"
        in SCRIPT
    )
    # 旧响应或抽屉已关闭时丢弃，不再打开错误患者的附件
    assert (
        "requestSeq !== attachmentPreviewRequestSeq || !metricsOpen"
        in SCRIPT
    )
    # 关闭预览会作废仍在途的请求
    assert "attachmentPreviewRequestSeq += 1;" in SCRIPT


def test_attachment_download_is_not_blocked_by_drawer_state():
    # 下载路径不使用预览序号，不受抽屉状态影响
    assert "download ? 0 : ++attachmentPreviewRequestSeq" in SCRIPT
