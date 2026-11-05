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


def test_app_js_home_glance_labels_profiles(client):
    app_js = client.get("/static/app.js").text

    assert "（${item.profile}）" in app_js
    assert "· ${upcoming[0].profile}" in app_js


def test_app_js_saves_screener_scores(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "saveScreenerScore('cat'" in app_js
    assert "saveScreenerScore('ess'" in app_js
    assert "saveScreenerScore('auditc'" in app_js
    assert "saveScreenerScore('mmrc'" in app_js
    assert 'value="cat"' in html


def test_app_js_screener_saves_active_profile(client):
    app_js = client.get("/static/app.js").text
    match = re.search(
        r"async function saveScreenerScore.*?\n}", app_js, re.S
    )

    assert match is not None
    assert "profile: els.profileSelect.value" in match.group(0)


def test_metric_select_is_grouped(client):
    html = client.get("/").text

    assert '<optgroup label="生命体征">' in html
    assert '<optgroup label="量表评分">' in html


def test_app_js_metric_save_guards_and_latest(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "let metricSaving = false;" in app_js
    assert "if (metricSaving) return;" in app_js
    assert "event.isComposing" in app_js
    assert "/api/metrics/latest" in app_js
    assert 'id="calc-auditc-sex"' in html


def test_context_panel_mentions_health_context(client):
    html = client.get("/").text

    assert "会诊时会自动参考当前档案的最近健康指标、症状、用药清单与待办随访" in html


def test_app_js_ships_symptom_diary(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function saveSymptom" in app_js
    assert "async function loadSymptoms" in app_js
    assert 'id="symptom-text"' in html
    assert 'id="symptom-list"' in html


def test_app_js_supports_symptom_deeplink(client):
    app_js = client.get("/static/app.js").text

    assert "params.get('symptoms') === '1'" in app_js
    assert "symptomSection.open" in app_js


def test_app_js_report_includes_symptoms(client):
    app_js = client.get("/static/app.js").text

    assert "const symptomItems = (symptoms || [])" in app_js
    assert "<h2>近期症状</h2>" in app_js


def test_app_js_ships_symptom_csv(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function exportSymptomsCsv" in app_js
    assert 'id="symptom-export"' in html


def test_app_js_ships_home_symptoms(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "homeGlanceSymptoms" in app_js
    assert 'id="home-glance-symptoms"' in html
    assert "症状日记'" in app_js


def test_app_js_ships_home_medications(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "homeGlanceMedications" in app_js
    assert 'id="home-glance-medications"' in html
    assert "/api/medications?active_only=true&limit=50" in app_js
    assert "种在用药物" in app_js


def test_app_js_ships_home_today_meds(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert "function renderTodayMeds" in app_js
    assert 'id="today-meds"' in html
    assert 'id="today-meds-list"' in html
    assert "today-meds-item" in app_js
    assert "today-meds-actions" in app_js
    assert "撤销最近一次用药记录" in app_js
    assert "今日未打卡" in app_js
    assert "今天还没有打卡" in app_js
    assert "今日均已打卡" in app_js
    assert "today-meds-status" in app_js
    assert "is-complete" in app_js
    assert ".today-meds" in css


def test_app_js_ships_medication_reminder_shortcut(client):
    app_js = client.get("/static/app.js").text

    assert "function scheduleMedicationReminder" in app_js
    assert "function frequencyRepeatDays" in app_js
    assert "为药物设置提醒" in app_js
    assert "已代入用药提醒" in app_js


def test_app_js_ships_medication_filters(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert 'id="medication-search"' in html
    assert 'id="medication-status-filter"' in html
    assert "function filterMedications" in app_js
    assert ".medication-filters" in css
    assert "els.medicationSearch.addEventListener('input'" in app_js
    assert "els.medicationStatusFilter.addEventListener('change'" in app_js


def test_app_js_ships_reminder_snooze_days(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "function snoozeReminder(item, days = 7)" in app_js
    assert "reminder-snooze" in app_js
    assert "推迟…" in app_js
    assert ".reminder-snooze" in css


def test_app_js_ships_clear_done_reminders(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert 'id="reminders-clear-done"' in html
    assert "async function clearDoneReminders" in app_js
    assert "清理已完成（" in app_js


def test_app_js_ships_medication_intake_log(client):
    app_js = client.get("/static/app.js").text

    assert "async function logMedication" in app_js
    assert "async function undoMedicationLog" in app_js
    assert "/api/medications/${item.id}/logs" in app_js
    assert "/api/medication-logs/" in app_js
    assert "今日已服" in app_js
    assert "近 7 天" in app_js
    assert "今日未打卡" in app_js
    assert "已记录用药" in app_js
    assert "medicationLogBusy.has(item.id)" in app_js
    assert "medicationLogBusy.delete(item.id)" in app_js


def test_app_js_ships_medication_intake_log_history(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function loadMedicationLogs" in app_js
    assert "function renderMedicationLogs" in app_js
    assert "async function deleteMedicationLogItem" in app_js
    assert "/api/medication-logs?limit=20" in app_js
    assert 'id="medication-log-list"' in html
    assert 'id="medication-log-empty"' in html


def test_app_js_ships_batch_medication_reminders(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function scheduleAllMedicationReminders" in app_js
    assert 'id="medication-reminders"' in html
    assert "已创建 ${created} 条用药提醒" in app_js
    assert "repeat_days: repeatDays" in app_js
    assert "条频次无法识别" in app_js
    assert "medicationReminderBusy" in app_js
    assert "reminders.push({" in app_js


def test_app_js_ships_health_timeline(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert "async function loadTimeline" in app_js
    assert "function renderTimeline" in app_js
    assert "/api/timeline" in app_js
    assert 'id="timeline-section"' in html
    assert 'id="timeline-range"' in html
    assert 'id="timeline-type"' in html
    assert 'id="timeline-search"' in html
    assert 'id="timeline-only-alert"' in html
    assert '<option value="1">今天</option>' in html
    assert "rangeDaysLabel" in app_js
    assert 'id="timeline-export"' in html
    assert "async function exportTimelineCsv" in app_js
    assert "/api/timeline/export.csv" in app_js
    assert "timeline-badge" in app_js
    assert "timelineSearch" in app_js
    assert "timelineOnlyAlert" in app_js
    assert "timeline-alert" in app_js
    assert ".timeline-badge" in css


def test_app_js_ships_family_overview(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert "async function loadFamilyOverview" in app_js
    assert "function renderFamilyOverview" in app_js
    assert "/api/family/overview" in app_js
    assert "/api/family/overview/export.csv" in app_js
    assert "async function exportFamilyOverviewCsv" in app_js
    assert "async function copyFamilyOverview" in app_js
    assert 'id="family-overview"' in html
    assert 'id="family-overview-copy"' in html
    assert 'id="family-overview-export"' in html
    assert ".family-card" in css
    assert ".family-chip" in css


def test_app_js_ships_home_backup_card(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert 'id="home-glance-backup"' in html
    assert "BACKUP_STORAGE_KEY" in app_js
    assert "readLastBackupAt" in app_js
    assert "markBackupExported" in app_js
    assert "homeGlanceBackup" in app_js


def test_app_js_ships_weekly_summary(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert "async function loadWeeklySummary" in app_js
    assert "function renderWeeklySummary" in app_js
    assert "function weeklySummaryLines" in app_js
    assert "/api/reports/weekly" in app_js
    assert 'id="weekly-summary-section"' in html
    assert 'id="weekly-summary-range"' in html
    assert 'id="weekly-summary-copy"' in html
    assert ".weekly-summary-list" in css
    assert "async function copyWeeklySummary" in app_js
    assert "function weeklySummaryText" in app_js
    assert "const ok = await copyText(weeklySummaryText(cache.data))" in app_js
    assert "已复制健康小结" in app_js
    assert "${rangeDaysLabel(days)}打卡" in app_js
    assert "较上期" in app_js
    assert "较上期${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)} 条" in app_js
    assert "近 7 天小结" in app_js
    assert "weeklySummaryRequestSeq" in app_js
    assert "medicationLogRequestSeq" in app_js
    assert "function refreshOverview" in app_js
    assert 'id="home-glance-summary"' in html
    assert "homeGlanceSummary" in app_js
    assert "近 ${days} 天：指标" in app_js


def test_app_js_links_medication_log_and_reminder(client):
    app_js = client.get("/static/app.js").text

    assert "async function completeDueMedicationReminder" in app_js
    assert "async function reopenLinkedMedicationReminder" in app_js
    assert "已完成今日用药提醒" in app_js
    assert "days >= -1 && days <= 0" in app_js
    assert "candidates.find(" in app_js
    assert "readPatientContext().allergies" in app_js


def test_app_js_profile_scoping_and_refresh(client):
    app_js = client.get("/static/app.js").text.replace("\r\n", "\n")

    assert "new URLSearchParams({ limit: '30', profile })" in app_js
    assert "(item.profile || '') === profile" in app_js
    assert "applyPatientContext({});" in app_js
    assert "全部档案 · 症状" in app_js
    assert "没有在用药物，${rangeDaysLabel(days)}打卡" in app_js
    assert "refreshReminderBadge();\n        refreshOverview();" in app_js
    assert (
        "await refreshMetricProfileOptions();\n"
        "    await loadMedications();"
    ) in app_js
    assert "payload.medication_id = reminderMedicationId" in app_js
    assert "medication_id: reminderMedicationId" in app_js
    assert "medication_id: item.id" in app_js
    assert "Number(reminder.medication_id) === item.id" in app_js
    assert "limit: '200', profile" in app_js
    assert (
        "fetchMetricSummaries(\n      profile, metrics, 0, false"
    ) in app_js


def test_app_js_medication_review_hardening(client):
    app_js = client.get("/static/app.js").text.replace("\r\n", "\n")
    css = client.get("/static/styles.css").text

    # 编辑中的药物被筛选隐藏时不应重置表单
    assert "!medicationItemsCache.some(" in app_js
    # 增删改/导入后刷新首页速览与本周小结
    assert app_js.count(
        "await loadMedications();\n    refreshOverview();"
    ) >= 3
    assert app_js.count(
        "loadMedicationLogs();\n    await refreshOverview();"
    ) >= 2
    assert app_js.count(
        "loadMedicationLogs();\n    refreshOverview();"
    ) >= 1
    # 首页用药卡片标注档案，避免跨档案聚合歧义
    assert "（${item.profile}）" in app_js
    # 备份导入携带打卡记录
    assert "medication_logs: data.medication_logs" in app_js
    # 推迟下拉失败后可重试
    assert "snooze.value = '';" in app_js
    # 频次解析覆盖每两月 / 每 N 天
    assert "每两月" in app_js
    assert "everyDays" in app_js
    # 审计标签补全
    assert "'metric_target.set': '设置指标目标'" in app_js
    assert "'maintenance.purge': '维护清理'" in app_js
    # 窄屏操作区可换行
    assert "flex-wrap: wrap" in css
    assert "max-width: 55%" in css


def test_app_js_ships_medication_logs_export(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function exportMedicationLogsCsv" in app_js
    assert "/api/medications/logs/export.csv" in app_js
    assert 'id="medication-logs-export"' in html


def test_app_js_ships_audit_labels_for_records(client):
    app_js = client.get("/static/app.js").text

    assert "'medication.log': '用药打卡'" in app_js
    assert "'symptom.create': '添加症状记录'" in app_js
    assert "'reminder.import': '导入随访提醒'" in app_js


def test_app_js_ships_diag_data_stats(client):
    app_js = client.get("/static/app.js").text

    assert "健康数据" in app_js
    assert "用药打卡" in app_js
    assert "个指标目标" in app_js


def test_app_js_marks_medication_hints(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "reminder-badge-med" in app_js
    assert "startsWith('用药：')" in app_js
    assert ".reminder-badge-med" in css
    assert "今日未打卡" in app_js


def test_app_js_ships_symptom_severity_styles(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "is-high" in app_js
    assert ".symptom-severity.is-high" in css


def test_app_js_ships_symptom_search(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function filterSymptoms" in app_js
    assert 'id="symptom-search"' in html


def test_app_js_ships_symptom_quick_picks(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text
    css = client.get("/static/styles.css").text

    assert 'id="symptom-quick"' in html
    assert 'data-symptom="头晕"' in html
    assert "els.symptomQuick.addEventListener" in app_js
    assert ".symptom-quick" in css


def test_app_js_ships_symptom_summary(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert 'id="symptom-summary"' in html
    assert "平均 ${Math.round(average * 10) / 10}/10" in app_js


def test_app_js_backup_import_includes_all_data(client):
    app_js = client.get("/static/app.js").text

    assert "symptoms: data.symptoms" in app_js
    assert "reminders: data.reminders" in app_js
    assert "metric_targets: data.metric_targets" in app_js
    assert "editingSymptomProfile" in app_js


def test_app_js_ships_symptom_import(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function importSymptomsCsv" in app_js
    assert "/api/symptoms/import" in app_js
    assert 'id="symptom-import"' in html


def test_app_js_ships_symptom_trend(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "function renderSymptomTrend" in app_js
    assert 'id="symptom-trend-chart"' in html


def test_app_js_symptom_cards_can_hide(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "limit=100000" in app_js
    assert ".home-glance-card[hidden]" in css


def test_app_js_ships_dark_symptom_severity(client):
    css = client.get("/static/styles.css").text

    assert 'html[data-theme="dark"] .symptom-severity.is-low' in css
    assert 'html[data-theme="dark"] .symptom-severity.is-high' in css


def test_app_js_ships_medications(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function saveMedication" in app_js
    assert "async function loadMedicationsIntoInteractions" in app_js
    assert 'id="medication-name"' in html
    assert 'id="calc-interactions-load"' in html


def test_app_js_ships_medication_context_and_report(client):
    app_js = client.get("/static/app.js").text

    assert "active_medications" in app_js
    assert "近 7 天打卡 ${Number(item.week_count)} 次" in app_js
    assert "const medicationItems = (medications || [])" in app_js
    assert "medications: data.medications" in app_js


def test_app_js_supports_medication_deeplink(client):
    app_js = client.get("/static/app.js").text

    assert "params.get('medications') === '1'" in app_js
    assert "medicationSection.open" in app_js


def test_app_js_medication_interaction_clears_empty(client):
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "els.calcInteractionsDrugs.value = ''" in app_js
    assert "els.calcResult.hidden = true" in app_js
    assert 'html[data-theme="dark"] .medication-active-badge.is-inactive' in css


def test_app_js_ships_medication_csv_and_toggle(client):
    app_js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "async function exportMedicationsCsv" in app_js
    assert "async function importMedicationsCsv" in app_js
    assert "async function toggleMedication" in app_js
    assert 'id="medication-export"' in html
    assert 'id="medication-import"' in html
