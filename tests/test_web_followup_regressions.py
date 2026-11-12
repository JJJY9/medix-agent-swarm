"""今日修复的回归护栏：知识库草稿、指标同类型、化验渲染上限等。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_kb_preview_close_buttons_do_not_pass_event_as_force():
    # 事件对象不能作为 force 参数绕过未保存草稿确认
    assert (
        "els.kbPreviewClose.addEventListener('click', () => closeKbPreview());"
        in SCRIPT
    )
    assert (
        "els.kbPreviewCancel.addEventListener("
        "'click', () => exitKbPreviewEdit());"
        in SCRIPT
    )
    assert "force !== true" in SCRIPT


def test_kb_save_syncs_original_before_exit():
    start = SCRIPT.index("async function saveKbPreviewEdit")
    body = SCRIPT[start:start + 2400]
    sync = body.index("previewEditOriginal = els.kbPreviewEditor.value;")
    exit_pos = body.index("exitKbPreviewEdit();")
    # 保存成功后先把新内容记为基线，退出编辑时不再弹放弃确认
    assert sync < exit_pos


def test_metric_same_type_card_keeps_edit():
    assert "editingMetricId && els.metricType.value !== type" in SCRIPT


def test_reminder_focus_returns_to_same_position():
    assert "function captureReminderFocusState()" in SCRIPT
    assert "function restoreReminderFocusState(state)" in SCRIPT
    assert "Number(state.index) || 0" in SCRIPT


def test_lab_render_limit_not_reset_on_refresh():
    start = SCRIPT.index("async function loadLabs()")
    body = SCRIPT[start:start + 900]
    # 保存/删除后的刷新不应把“显示更多”展开的列表收回 300 行
    assert "labRenderLimit = 300;" not in body


def test_metrics_more_label_recovers_after_success_or_supersede():
    start = SCRIPT.index("els.metricsMore.addEventListener('click'")
    body = SCRIPT[start:start + 700]
    assert "if (loaded !== false) {" in body
    assert "els.metricsMore.textContent = '显示更早记录';" in body
