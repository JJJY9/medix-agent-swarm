"""家庭健康总览关注提示的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
CSS = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_family_card_renders_attention_badge():
    assert "profile.attention" in SCRIPT
    assert "family-attention-badge" in SCRIPT
    assert "is-${attention.level}" in SCRIPT
    assert "attention.label" in SCRIPT
    assert "attention.detail" in SCRIPT


def test_family_attention_styles():
    assert ".family-attention-badge" in CSS
    assert ".family-attention-badge.is-danger" in CSS
    assert ".family-attention-badge.is-warning" in CSS
    assert ".family-attention-badge.is-info" in CSS
    assert (
        'html[data-theme="dark"] .family-attention-badge.is-warning'
        in CSS
    )
