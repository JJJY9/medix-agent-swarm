"""会诊 HTML 报告导出的静态资源离线测试。"""
import re

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_app_js_ships_standalone_html_report(client):
    app_js = client.get("/static/app.js").text

    assert "function buildReportHtml" in app_js
    assert "function renderMarkdownLite" in app_js
    assert "function escapeHtml" in app_js
    assert "导出 HTML 报告" in app_js
    assert "medix-report-" in app_js
    assert "window.print()" in app_js


def test_app_js_ships_voice_input(client):
    app_js = client.get("/static/app.js").text

    assert "function setupVoiceInput" in app_js
    assert "function toggleVoice" in app_js
    assert "webkitSpeechRecognition" in app_js
    assert "voice-toggle" in app_js


def test_app_js_ships_reminder_badge_and_notifications(client):
    app_js = client.get("/static/app.js").text

    assert "function updateReminderBadge" in app_js
    assert "function refreshReminderBadge" in app_js
    assert "function enableReminderNotifications" in app_js
    assert "Notification.requestPermission" in app_js
    assert "reminders-count" in app_js


def test_app_js_ships_session_compare(client):
    app_js = client.get("/static/app.js").text

    assert "function openCompare" in app_js
    assert "function renderCompareColumn" in app_js
    assert "function compareStats" in app_js
    assert "history-bulk-compare" in app_js
    assert "compare-drawer" in app_js


def test_calc_forms_have_submit_buttons(client):
    html = client.get("/").text
    forms = re.findall(
        r'<form[^>]*class="calc-form"[^>]*>.*?</form>', html, re.S
    )
    assert forms
    for block in forms:
        form_id = re.search(r'id="([^"]+)"', block).group(1)
        assert 'type="submit"' in block, f"{form_id} 缺少提交按钮"


def test_app_js_ships_visit_summary(client):
    app_js = client.get("/static/app.js").text

    assert "function buildVisitSummaryHtml" in app_js
    assert "function exportVisitSummary" in app_js
    assert "medix-visit-summary-" in app_js
    assert "visit-summary" in app_js


def test_styles_keep_hidden_overrides(client):
    css = client.get("/static/styles.css").text

    assert ".reminder-count[hidden]" in css
    assert ".metric-target-form[hidden]" in css


def test_app_js_ships_bulk_export(client):
    app_js = client.get("/static/app.js").text

    assert "function bulkExportMarkdown" in app_js
    assert "history-bulk-export" in app_js
    assert "medix-sessions-" in app_js


def test_app_js_escapes_banner_error_text(client):
    app_js = client.get("/static/app.js").text

    assert "escapeHtml(data.startup_error" in app_js
    assert "escapeHtml(data.knowledge_base.error)" in app_js
    assert "escapeHtml(err.message)" in app_js


def test_app_js_ships_sessions_csv_export(client):
    app_js = client.get("/static/app.js").text

    assert "function exportHistoryCsv" in app_js
    assert "history-export-csv" in app_js
    assert "/api/sessions/export.csv" in app_js


def test_app_js_ships_blood_pressure_grading(client):
    app_js = client.get("/static/app.js").text

    assert "function classifyBloodPressure" in app_js
    assert "正常高值" in app_js
    assert "bp-grade-" in app_js


def test_app_js_draws_metric_target_line(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "trend-target" in app_js
    assert "目标 " in app_js
    assert ".trend-target" in css


def test_app_js_ships_custom_starters(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "function addCustomStarter" in app_js
    assert "medix.customStarters" in app_js
    assert "starter-add" in app_js
    assert ".starter-wrap" in css


def test_app_js_ships_reminder_from_note(client):
    app_js = client.get("/static/app.js").text

    assert "设随访提醒" in app_js
    assert "来自会诊建议" in app_js


def test_app_js_scopes_profile_state(client):
    app_js = client.get("/static/app.js").text

    assert "/api/profile-names" in app_js
    assert "medix.activeProfile" in app_js
    assert "function setActiveProfile" in app_js
    assert "切换患者档案会开始新的会诊记录" in app_js


def test_app_js_filters_reminders_by_profile(client):
    app_js = client.get("/static/app.js").text

    assert "function refreshReminderProfileOptions" in app_js
    assert "reminders-profile" in app_js


def test_app_js_exports_reminders_csv(client):
    app_js = client.get("/static/app.js").text

    assert "function exportRemindersCsv" in app_js
    assert "/api/reminders/export.csv" in app_js


def test_app_js_exports_reminders_ics(client):
    app_js = client.get("/static/app.js").text

    assert "function exportRemindersIcs" in app_js
    assert "/api/reminders/export.ics" in app_js


def test_app_js_groups_and_remembers_calc_tool(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "medix.calcTool" in app_js
    assert '<optgroup label="心血管与血栓">' in html
    assert '<optgroup label="肝病">' in html


def test_app_js_ships_metric_range_filter(client):
    app_js = client.get("/static/app.js").text

    assert "function metricRangeDays" in app_js
    assert "metric-range" in app_js


def test_app_js_handles_pwa_launch_actions(client):
    app_js = client.get("/static/app.js").text

    assert "function handleLaunchAction" in app_js
    assert "action === 'reminders'" in app_js



def test_app_js_ships_metric_summary(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function renderMetricSummary" in app_js
    assert "async function loadMetricSummary" in app_js
    assert "/api/metrics/summary" in app_js
    assert 'id="metric-stat-grid"' in html


def test_app_js_ships_reminder_groups(client):
    app_js = client.get("/static/app.js").text

    assert "function reminderGroupOf" in app_js
    assert "reminder-group-head" in app_js
    assert "已逾期" in app_js
    assert "未来 7 天" in app_js


def test_app_js_ships_reminder_csv_import(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function importRemindersCsv" in app_js
    assert "/api/reminders/import" in app_js
    assert 'id="reminders-import"' in html


def test_app_js_ships_reminder_advance_notice(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function reminderIsAdvanceDue" in app_js
    assert "reminder-badge-advance" in app_js
    assert "advance_days" in app_js
    assert 'id="reminder-advance"' in html


def test_app_js_ships_visit_summary_metric_overview(client):
    app_js = client.get("/static/app.js").text

    assert "async function fetchMetricSummaries" in app_js
    assert "指标概览" in app_js
    assert "summaryRows" in app_js


def test_app_js_ships_reminder_series(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function addReminderSeries" in app_js
    assert "function buildReminderSeries" in app_js
    assert "function addMonthsToDate" in app_js
    assert "按周期安排多次随访" in html
    assert 'id="reminder-plan-add"' in html


def test_app_js_ships_metric_weekly_overview(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function loadMetricWeekly" in app_js
    assert "function renderMetricWeekly" in app_js
    assert "近 7 天变化" in html
    assert 'id="metric-weekly"' in html


def test_app_js_truncates_advance_days(client):
    app_js = client.get("/static/app.js").text

    assert "omitEmptyProfile" in app_js
    assert "Math.trunc(Math.max(" in app_js


def test_app_js_ships_reminder_profile_select(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "reminderProfile: document.getElementById('reminder-profile')" in app_js
    assert "els.reminderProfile.value = item.profile" in app_js
    assert 'id="reminder-profile"' in html


def test_app_js_ships_extended_metric_types(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert 'value="heart_rate"' in html
    assert 'value="spo2"' in html
    assert 'value="waist"' in html
    assert "血氧饱和度" in app_js
    assert "腰围参考" in app_js


def test_app_js_ships_reminder_calendar(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function renderReminderCalendar" in app_js
    assert "function shiftReminderCalendarMonth" in app_js
    assert "REMMINDER_WEEKDAYS" not in app_js
    assert "REMINDER_WEEKDAYS" in app_js
    assert 'id="reminders-calendar-grid"' in html
    assert 'id="reminders-calendar-toggle"' in html


def test_app_js_ships_metric_target_list(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function renderMetricTargetList" in app_js
    assert "async function deleteMetricTargetFor" in app_js
    assert 'id="metric-target-list"' in html


def test_app_js_ships_metric_followup(client):
    app_js = client.get("/static/app.js").text

    assert "async function scheduleMetricFollowUp" in app_js
    assert "安排复查" in app_js
    assert "上次记录：" in app_js


def test_app_js_ships_reminder_search(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function searchFilteredReminders" in app_js
    assert "function refreshReminderViews" in app_js
    assert 'id="reminders-search"' in html


def test_app_js_ships_metric_grading(client):
    app_js = client.get("/static/app.js").text

    assert "function classifyHeartRate" in app_js
    assert "function classifySpo2" in app_js
    assert "function classifyMetric" in app_js
    assert "明显心动过缓" in app_js
    assert "低氧" in app_js


def test_app_js_ships_metrics_paging(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "METRICS_PAGE_SIZE" in app_js
    assert "metricsOffset" in app_js
    assert 'id="metric-more"' in html


def test_app_js_ships_target_direction_and_guards(client):
    app_js = client.get("/static/app.js").text

    assert "function targetIsLowerBound" in app_js
    assert "reminderProfileTouched" in app_js
    assert "低于目标" in app_js


def test_app_js_ships_weekly_adherence_and_a11y(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "metric-weekly-target" in app_js
    assert "aria-pressed" in app_js
    assert 'aria-live="polite"' in html
    assert 'id="reminders-empty" role="status"' in html


def test_app_js_ships_repeating_reminders(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function syncReminderRepeatFields" in app_js
    assert "repeat_days" in app_js
    assert "每次都" not in app_js
    assert 'id="reminder-repeat"' in html
    assert 'id="reminder-until"' in html


def test_app_js_ships_reminder_countdown(client):
    app_js = client.get("/static/app.js").text

    assert "function reminderDueCountdown" in app_js
    assert "已逾期 ${" in app_js
    assert "（还有 ${days} 天）" in app_js


def test_app_js_ships_overdue_filter_and_today(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "remindersOverdueOnly" in app_js
    assert "没有逾期的提醒" in app_js
    assert 'id="reminders-overdue-only"' in html
    assert 'id="reminders-calendar-today"' in html


def test_app_js_ships_home_glance(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function loadHomeGlance" in app_js
    assert "function renderHomeGlance" in app_js
    assert 'id="home-glance-reminders"' in html
    assert 'id="home-glance-metrics"' in html


def test_app_js_ships_audit_c(client):
    html = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'value="auditc"' in html
    assert 'id="calc-form-auditc"' in html
    assert "function submitAuditC" in app_js
    assert "/api/tools/audit-c" in app_js


def test_app_js_ships_mmrc(client):
    html = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'value="mmrc"' in html
    assert 'id="calc-form-mmrc"' in html
    assert "function submitMmrc" in app_js
    assert "/api/tools/mmrc" in app_js


def test_app_js_report_target_direction_and_repeat(client):
    app_js = client.get("/static/app.js").text

    assert "const direction = targetIsLowerBound(target.metric)" in app_js
    assert "天重复`" in app_js


def test_app_js_home_glance_guards(client):
    app_js = client.get("/static/app.js").text

    assert "homeGlanceRequestSeq" in app_js
    assert "Object.values(latest)" in app_js
    assert "remindersResult.hidden" not in app_js


def test_app_js_ships_cat(client):
    html = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'value="cat"' in html
    assert 'id="calc-form-cat"' in html
    assert "function ensureCatForm" in app_js
    assert "function submitCat" in app_js
    assert "/api/tools/cat" in app_js


def test_app_js_ships_ess(client):
    html = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'value="ess"' in html
    assert 'id="calc-form-ess"' in html
    assert "function ensureEssForm" in app_js
    assert "function submitEss" in app_js
    assert "/api/tools/ess" in app_js


def test_app_js_enter_submits_forms(client):
    app_js = client.get("/static/app.js").text

    assert "els.reminderDue," in app_js
    assert "els.metricSystolic," in app_js
    assert "addMetricRecord();" in app_js
    assert "addReminder();" in app_js


def test_app_js_ships_reminder_due_quick(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "reminderDueQuick" in app_js
    assert 'id="reminder-due-quick"' in html
    assert 'data-days="30"' in html


def test_app_js_ships_consult_health_context(client):
    app_js = client.get("/static/app.js").text

    assert "async function buildConsultContext" in app_js
    assert "context: await buildConsultContext()" in app_js
    assert "pending_reminders" in app_js


def test_app_js_ships_metric_target_defaults(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "METRIC_TARGET_DEFAULTS" in app_js
    assert "已填入常用参考值" in html
    assert 'id="metric-target-hint"' in html
