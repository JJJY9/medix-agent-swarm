/* ============================================================
   MediX 前端逻辑
   - 通过 /api/chat/stream 读取 SSE，边跑边点亮会诊科室
   - 把 LLM 返回的 【小节】 文本渲染成分节的会诊意见书
   ============================================================ */

const els = {
  thread: document.getElementById('thread'),
  empty: document.getElementById('empty-state'),
  starters: document.getElementById('starters'),
  starterAdd: document.getElementById('starter-add'),
  composer: document.getElementById('composer'),
  input: document.getElementById('input'),
  skipLink: document.getElementById('skip-link'),
  contextToggle: document.getElementById('context-toggle'),
  contextPanel: document.getElementById('context-panel'),
  contextSummary: document.getElementById('context-summary'),
  contextClear: document.getElementById('context-clear'),
  ctxAge: document.getElementById('ctx-age'),
  ctxSex: document.getElementById('ctx-sex'),
  ctxDuration: document.getElementById('ctx-duration'),
  ctxHistory: document.getElementById('ctx-history'),
  ctxMedications: document.getElementById('ctx-medications'),
  ctxAllergies: document.getElementById('ctx-allergies'),
  ctxSingle: document.getElementById('ctx-single'),
  profileSelect: document.getElementById('profile-select'),
  profileSave: document.getElementById('profile-save'),
  profileRename: document.getElementById('profile-rename'),
  profileDelete: document.getElementById('profile-delete'),
  send: document.getElementById('send'),
  newSession: document.getElementById('new-session'),
  themeToggle: document.getElementById('theme-toggle'),
  historyToggle: document.getElementById('history-toggle'),
  historyDrawer: document.getElementById('history-drawer'),
  historyBackdrop: document.getElementById('history-backdrop'),
  historyClose: document.getElementById('history-close'),
  historySearch: document.getElementById('history-search'),
  historyList: document.getElementById('history-list'),
  historySummary: document.getElementById('history-summary'),
  historyActionStatus: document.getElementById('history-action-status'),
  historyFavoriteFilter: document.getElementById('history-favorite-filter'),
  historyArchiveFilter: document.getElementById('history-archive-filter'),
  historyBulkToggle: document.getElementById('history-bulk-toggle'),
  historyBulkBar: document.getElementById('history-bulk-bar'),
  historyBulkCount: document.getElementById('history-bulk-count'),
  historyBulkAll: document.getElementById('history-bulk-all'),
  historyBulkCompare: document.getElementById('history-bulk-compare'),
  historyBulkExport: document.getElementById('history-bulk-export'),
  historyBulkArchive: document.getElementById('history-bulk-archive'),
  historyBulkUnarchive: document.getElementById('history-bulk-unarchive'),
  historyBulkTags: document.getElementById('history-bulk-tags'),
  historyBulkDelete: document.getElementById('history-bulk-delete'),
  historyBulkCancel: document.getElementById('history-bulk-cancel'),
  historyExport: document.getElementById('history-export'),
  historyExportCsv: document.getElementById('history-export-csv'),
  historyImport: document.getElementById('history-import'),
  historyImportFile: document.getElementById('history-import-file'),
  historyRange: document.getElementById('history-range'),
  historyTag: document.getElementById('history-tag'),
  historyProfile: document.getElementById('history-profile'),
  historyFeedback: document.getElementById('history-feedback'),
  historyFeedbackCount: document.getElementById('history-feedback-count'),
  historyFeedbackList: document.getElementById('history-feedback-list'),
  historyFeedbackExport: document.getElementById('history-feedback-export'),
  historyMore: document.getElementById('history-more'),
  usageTrend: document.getElementById('usage-trend'),
  usageTrendTotal: document.getElementById('usage-trend-total'),
  usageExport: document.getElementById('usage-export'),
  usageChart: document.getElementById('usage-chart'),
  usageBars: document.getElementById('usage-bars'),
  usageAxis: document.getElementById('usage-axis'),
  usageTooltip: document.getElementById('usage-tooltip'),
  usageTable: document.getElementById('usage-table'),
  usageTableBody: document.getElementById('usage-table-body'),
  usageBudget: document.getElementById('usage-budget'),
  usageBudgetText: document.getElementById('usage-budget-text'),
  usageBudgetBar: document.getElementById('usage-budget-bar'),
  usageBudgetHint: document.getElementById('usage-budget-hint'),
  historyQuality: document.getElementById('history-quality'),
  historyQualityBody: document.getElementById('history-quality-body'),
  historyEmpty: document.getElementById('history-empty'),
  stop: document.getElementById('stop'),
  voiceToggle: document.getElementById('voice-toggle'),
  jumpLatest: document.getElementById('jump-latest'),
  kbManage: document.getElementById('kb-manage'),
  kbDrawer: document.getElementById('kb-drawer'),
  kbBackdrop: document.getElementById('kb-backdrop'),
  kbClose: document.getElementById('kb-close'),
  kbFile: document.getElementById('kb-file'),
  kbUpload: document.getElementById('kb-upload'),
  kbRebuild: document.getElementById('kb-rebuild'),
  kbStats: document.getElementById('kb-stats'),
  kbStatus: document.getElementById('kb-status'),
  kbList: document.getElementById('kb-list'),
  kbBody: document.getElementById('kb-body'),
  kbSearchInput: document.getElementById('kb-search-input'),
  kbSearchType: document.getElementById('kb-search-type'),
  kbSearchButton: document.getElementById('kb-search-button'),
  kbSearchResults: document.getElementById('kb-search-results'),
  kbPreview: document.getElementById('kb-preview'),
  kbPreviewTitle: document.getElementById('kb-preview-title'),
  kbPreviewMeta: document.getElementById('kb-preview-meta'),
  kbPreviewBody: document.getElementById('kb-preview-body'),
  kbPreviewClose: document.getElementById('kb-preview-close'),
  kbPreviewEdit: document.getElementById('kb-preview-edit'),
  kbPreviewSave: document.getElementById('kb-preview-save'),
  kbPreviewCancel: document.getElementById('kb-preview-cancel'),
  kbPreviewEditor: document.getElementById('kb-preview-editor'),
  diagnose: document.getElementById('diagnose'),
  diagnoseDrawer: document.getElementById('diagnose-drawer'),
  diagnoseBackdrop: document.getElementById('diagnose-backdrop'),
  diagnoseClose: document.getElementById('diagnose-close'),
  diagSummary: document.getElementById('diag-summary'),
  diagEnv: document.getElementById('diag-env'),
  diagRuntime: document.getElementById('diag-runtime'),
  diagLlm: document.getElementById('diag-llm'),
  diagKnowledge: document.getElementById('diag-knowledge'),
  diagPackages: document.getElementById('diag-packages'),
  diagAudit: document.getElementById('diag-audit'),
  diagAuditExport: document.getElementById('diag-audit-export'),
  diagPurge: document.getElementById('diag-purge'),
  auditAction: document.getElementById('audit-action'),
  auditKeyword: document.getElementById('audit-keyword'),
  auditStart: document.getElementById('audit-start'),
  auditEnd: document.getElementById('audit-end'),
  auditFilterApply: document.getElementById('audit-filter-apply'),
  diagRefresh: document.getElementById('diag-refresh'),
  diagLlmTest: document.getElementById('diag-llm-test'),
  diagCopy: document.getElementById('diag-copy'),
  diagDownload: document.getElementById('diag-download'),
  diagStatus: document.getElementById('diag-status'),
  calcOpen: document.getElementById('calc-open'),
  calcDrawer: document.getElementById('calc-drawer'),
  calcBackdrop: document.getElementById('calc-backdrop'),
  calcClose: document.getElementById('calc-close'),
  calcTool: document.getElementById('calc-tool'),
  calcFormBmi: document.getElementById('calc-form-bmi'),
  calcBmiHeight: document.getElementById('calc-bmi-height'),
  calcBmiWeight: document.getElementById('calc-bmi-weight'),
  calcFormEgfr: document.getElementById('calc-form-egfr'),
  calcEgfrAge: document.getElementById('calc-egfr-age'),
  calcEgfrSex: document.getElementById('calc-egfr-sex'),
  calcEgfrCreatinine: document.getElementById('calc-egfr-creatinine'),
  calcEgfrUnit: document.getElementById('calc-egfr-unit'),
  calcFormChads: document.getElementById('calc-form-chads'),
  calcChadsAge: document.getElementById('calc-chads-age'),
  calcChadsSex: document.getElementById('calc-chads-sex'),
  calcChadsChf: document.getElementById('calc-chads-chf'),
  calcChadsHypertension: document.getElementById('calc-chads-hypertension'),
  calcChadsDiabetes: document.getElementById('calc-chads-diabetes'),
  calcChadsStroke: document.getElementById('calc-chads-stroke'),
  calcChadsVascular: document.getElementById('calc-chads-vascular'),
  calcFormCurb65: document.getElementById('calc-form-curb65'),
  calcCurbAge: document.getElementById('calc-curb-age'),
  calcCurbConfusion: document.getElementById('calc-curb-confusion'),
  calcCurbUrea: document.getElementById('calc-curb-urea'),
  calcCurbRr: document.getElementById('calc-curb-rr'),
  calcCurbSbp: document.getElementById('calc-curb-sbp'),
  calcCurbDbp: document.getElementById('calc-curb-dbp'),
  calcFormChildPugh: document.getElementById('calc-form-child-pugh'),
  calcChildBilirubin: document.getElementById('calc-child-bilirubin'),
  calcChildAlbumin: document.getElementById('calc-child-albumin'),
  calcChildInr: document.getElementById('calc-child-inr'),
  calcChildAscites: document.getElementById('calc-child-ascites'),
  calcChildEncephalopathy: document.getElementById('calc-child-encephalopathy'),
  calcFormPhq9: document.getElementById('calc-form-phq9'),
  calcPhq9Questions: document.getElementById('calc-phq9-questions'),
  calcFormGad7: document.getElementById('calc-form-gad7'),
  calcFormAuditc: document.getElementById('calc-form-auditc'),
  calcFormMmrc: document.getElementById('calc-form-mmrc'),
  calcMmrcGrade: document.getElementById('calc-mmrc-grade'),
  calcAuditCQ1: document.getElementById('calc-auditc-q1'),
  calcAuditCQ2: document.getElementById('calc-auditc-q2'),
  calcAuditCQ3: document.getElementById('calc-auditc-q3'),
  calcGad7Questions: document.getElementById('calc-gad7-questions'),
  calcFormHasBled: document.getElementById('calc-form-hasbled'),
  calcHasBledAge: document.getElementById('calc-hasbled-age'),
  calcHasBledHypertension: document.getElementById('calc-hasbled-hypertension'),
  calcHasBledRenal: document.getElementById('calc-hasbled-renal'),
  calcHasBledLiver: document.getElementById('calc-hasbled-liver'),
  calcHasBledStroke: document.getElementById('calc-hasbled-stroke'),
  calcHasBledBleeding: document.getElementById('calc-hasbled-bleeding'),
  calcHasBledInr: document.getElementById('calc-hasbled-inr'),
  calcHasBledDrugs: document.getElementById('calc-hasbled-drugs'),
  calcHasBledAlcohol: document.getElementById('calc-hasbled-alcohol'),
  calcFormGcs: document.getElementById('calc-form-gcs'),
  calcGcsEye: document.getElementById('calc-gcs-eye'),
  calcGcsVerbal: document.getElementById('calc-gcs-verbal'),
  calcGcsMotor: document.getElementById('calc-gcs-motor'),
  calcFormWells: document.getElementById('calc-form-wells'),
  calcWellsCancer: document.getElementById('calc-wells-cancer'),
  calcWellsParalysis: document.getElementById('calc-wells-paralysis'),
  calcWellsBedridden: document.getElementById('calc-wells-bedridden'),
  calcWellsTenderness: document.getElementById('calc-wells-tenderness'),
  calcWellsEntire: document.getElementById('calc-wells-entire'),
  calcWellsCalf: document.getElementById('calc-wells-calf'),
  calcWellsEdema: document.getElementById('calc-wells-edema'),
  calcWellsCollateral: document.getElementById('calc-wells-collateral'),
  calcWellsPrevious: document.getElementById('calc-wells-previous'),
  calcWellsAlternative: document.getElementById('calc-wells-alternative'),
  calcFormWellsPe: document.getElementById('calc-form-wells-pe'),
  calcFormInteractions: document.getElementById('calc-form-interactions'),
  calcInteractionsDrugs: document.getElementById('calc-interactions-drugs'),
  calcInteractionsAllergies: document.getElementById('calc-interactions-allergies'),
  calcFormQsofa: document.getElementById('calc-form-qsofa'),
  calcQsofaRr: document.getElementById('calc-qsofa-rr'),
  calcQsofaSbp: document.getElementById('calc-qsofa-sbp'),
  calcQsofaMentation: document.getElementById('calc-qsofa-mentation'),
  calcFormFib4: document.getElementById('calc-form-fib4'),
  calcFib4Age: document.getElementById('calc-fib4-age'),
  calcFib4Ast: document.getElementById('calc-fib4-ast'),
  calcFib4Alt: document.getElementById('calc-fib4-alt'),
  calcFib4Platelets: document.getElementById('calc-fib4-platelets'),
  calcFormMeld: document.getElementById('calc-form-meld'),
  calcMeldBilirubin: document.getElementById('calc-meld-bilirubin'),
  calcMeldInr: document.getElementById('calc-meld-inr'),
  calcMeldCreatinine: document.getElementById('calc-meld-creatinine'),
  calcMeldDialysis: document.getElementById('calc-meld-dialysis'),
  calcFormStopBang: document.getElementById('calc-form-stopbang'),
  calcStopBangSnoring: document.getElementById('calc-stopbang-snoring'),
  calcStopBangTired: document.getElementById('calc-stopbang-tired'),
  calcStopBangApnea: document.getElementById('calc-stopbang-apnea'),
  calcStopBangHypertension: document.getElementById('calc-stopbang-hypertension'),
  calcStopBangBmi: document.getElementById('calc-stopbang-bmi'),
  calcStopBangAge: document.getElementById('calc-stopbang-age'),
  calcStopBangNeck: document.getElementById('calc-stopbang-neck'),
  calcStopBangMale: document.getElementById('calc-stopbang-male'),
  calcFormPadua: document.getElementById('calc-form-padua'),
  calcPaduaCancer: document.getElementById('calc-padua-cancer'),
  calcPaduaVte: document.getElementById('calc-padua-vte'),
  calcPaduaMobility: document.getElementById('calc-padua-mobility'),
  calcPaduaThrombophilia: document.getElementById('calc-padua-thrombophilia'),
  calcPaduaTrauma: document.getElementById('calc-padua-trauma'),
  calcPaduaAge: document.getElementById('calc-padua-age'),
  calcPaduaHeart: document.getElementById('calc-padua-heart'),
  calcPaduaMi: document.getElementById('calc-padua-mi'),
  calcPaduaInfection: document.getElementById('calc-padua-infection'),
  calcPaduaBmi: document.getElementById('calc-padua-bmi'),
  calcPaduaHormonal: document.getElementById('calc-padua-hormonal'),
  calcFormAbcd2: document.getElementById('calc-form-abcd2'),
  calcAbcd2Age: document.getElementById('calc-abcd2-age'),
  calcAbcd2Bp: document.getElementById('calc-abcd2-bp'),
  calcAbcd2Weakness: document.getElementById('calc-abcd2-weakness'),
  calcAbcd2Speech: document.getElementById('calc-abcd2-speech'),
  calcAbcd2Duration60: document.getElementById('calc-abcd2-duration60'),
  calcAbcd2Duration10: document.getElementById('calc-abcd2-duration10'),
  calcAbcd2Diabetes: document.getElementById('calc-abcd2-diabetes'),
  calcFormMorse: document.getElementById('calc-form-morse'),
  calcMorseHistory: document.getElementById('calc-morse-history'),
  calcMorseSecondary: document.getElementById('calc-morse-secondary'),
  calcMorseAid: document.getElementById('calc-morse-aid'),
  calcMorseIv: document.getElementById('calc-morse-iv'),
  calcMorseGait: document.getElementById('calc-morse-gait'),
  calcMorseMental: document.getElementById('calc-morse-mental'),
  calcWellsPeDvt: document.getElementById('calc-wellspe-dvt'),
  calcWellsPeLikely: document.getElementById('calc-wellspe-likely'),
  calcWellsPeHr: document.getElementById('calc-wellspe-hr'),
  calcWellsPeImmobilization: document.getElementById('calc-wellspe-immobilization'),
  calcWellsPePrevious: document.getElementById('calc-wellspe-previous'),
  calcWellsPeHemoptysis: document.getElementById('calc-wellspe-hemoptysis'),
  calcWellsPeMalignancy: document.getElementById('calc-wellspe-malignancy'),
  calcResult: document.getElementById('calc-result'),
  calcResultTitle: document.getElementById('calc-result-title'),
  calcResultMain: document.getElementById('calc-result-main'),
  calcResultDetail: document.getElementById('calc-result-detail'),
  calcResultNote: document.getElementById('calc-result-note'),
  calcSave: document.getElementById('calc-save'),
  calcStatus: document.getElementById('calc-status'),
  pwaInstall: document.getElementById('pwa-install'),
  metricsOpen: document.getElementById('metrics-open'),
  metricsDrawer: document.getElementById('metrics-drawer'),
  metricsBackdrop: document.getElementById('metrics-backdrop'),
  metricsClose: document.getElementById('metrics-close'),
  remindersOpen: document.getElementById('reminders-open'),
  remindersCount: document.getElementById('reminders-count'),
  visitSummary: document.getElementById('visit-summary'),
  remindersNotify: document.getElementById('reminders-notify'),
  remindersDrawer: document.getElementById('reminders-drawer'),
  remindersBackdrop: document.getElementById('reminders-backdrop'),
  remindersClose: document.getElementById('reminders-close'),
  reminderProfile: document.getElementById('reminder-profile'),
  reminderTitle: document.getElementById('reminder-title'),
  reminderDue: document.getElementById('reminder-due'),
  reminderNote: document.getElementById('reminder-note'),
  reminderAdvance: document.getElementById('reminder-advance'),
  reminderRepeat: document.getElementById('reminder-repeat'),
  reminderUntil: document.getElementById('reminder-until'),
  reminderUntilWrap: document.getElementById('reminder-until-wrap'),
  reminderAdd: document.getElementById('reminder-add'),
  remindersExport: document.getElementById('reminders-export'),
  remindersExportIcs: document.getElementById('reminders-export-ics'),
  remindersImport: document.getElementById('reminders-import'),
  remindersImportFile: document.getElementById('reminders-import-file'),
  reminderPlanInterval: document.getElementById('reminder-plan-interval'),
  reminderPlanUnit: document.getElementById('reminder-plan-unit'),
  reminderPlanCount: document.getElementById('reminder-plan-count'),
  reminderPlanAdd: document.getElementById('reminder-plan-add'),
  reminderCancel: document.getElementById('reminder-cancel'),
  remindersStatus: document.getElementById('reminders-status'),
  remindersDueHint: document.getElementById('reminders-due-hint'),
  remindersProfile: document.getElementById('reminders-profile'),
  remindersShowDone: document.getElementById('reminders-show-done'),
  remindersOverdueOnly: document.getElementById('reminders-overdue-only'),
  remindersCalendarToday: document.getElementById('reminders-calendar-today'),
  remindersSearch: document.getElementById('reminders-search'),
  homeGlance: document.getElementById('home-glance'),
  homeGlanceReminders: document.getElementById('home-glance-reminders'),
  homeGlanceMetrics: document.getElementById('home-glance-metrics'),
  remindersCalendar: document.getElementById('reminders-calendar'),
  remindersCalendarToggle: document.getElementById('reminders-calendar-toggle'),
  remindersCalendarTitle: document.getElementById('reminders-calendar-title'),
  remindersCalendarGrid: document.getElementById('reminders-calendar-grid'),
  remindersCalendarPrev: document.getElementById('reminders-calendar-prev'),
  remindersCalendarNext: document.getElementById('reminders-calendar-next'),
  remindersCalendarClear: document.getElementById('reminders-calendar-clear'),
  remindersList: document.getElementById('reminders-list'),
  remindersEmpty: document.getElementById('reminders-empty'),
  compareDrawer: document.getElementById('compare-drawer'),
  compareBackdrop: document.getElementById('compare-backdrop'),
  compareClose: document.getElementById('compare-close'),
  compareBody: document.getElementById('compare-body'),
  metricProfile: document.getElementById('metric-profile'),
  metricType: document.getElementById('metric-type'),
  metricRange: document.getElementById('metric-range'),
  metricReference: document.getElementById('metric-reference'),
  metricTargetText: document.getElementById('metric-target-text'),
  metricTargetEdit: document.getElementById('metric-target-edit'),
  metricTargetClear: document.getElementById('metric-target-clear'),
  metricTargetList: document.getElementById('metric-target-list'),
  metricTargetListItems: document.getElementById('metric-target-list-items'),
  metricTargetForm: document.getElementById('metric-target-form'),
  metricTargetValue1: document.getElementById('metric-target-value1'),
  metricTargetValue2: document.getElementById('metric-target-value2'),
  metricTargetSep: document.getElementById('metric-target-sep'),
  metricTargetSave: document.getElementById('metric-target-save'),
  metricTargetCancel: document.getElementById('metric-target-cancel'),
  metricBpValues: document.getElementById('metric-bp-values'),
  metricSystolic: document.getElementById('metric-systolic'),
  metricDiastolic: document.getElementById('metric-diastolic'),
  metricSingleWrap: document.getElementById('metric-single-wrap'),
  metricUnit: document.getElementById('metric-unit'),
  metricValue: document.getElementById('metric-value'),
  metricTime: document.getElementById('metric-time'),
  metricNote: document.getElementById('metric-note'),
  metricAdd: document.getElementById('metric-add'),
  metricCancel: document.getElementById('metric-cancel'),
  metricExport: document.getElementById('metric-export'),
  metricImport: document.getElementById('metric-import'),
  metricImportFile: document.getElementById('metric-import-file'),
  metricTrend: document.getElementById('metric-trend'),
  metricTrendTitle: document.getElementById('metric-trend-title'),
  metricTrendLatest: document.getElementById('metric-trend-latest'),
  metricTrendChart: document.getElementById('metric-trend-chart'),
  metricTrendHint: document.getElementById('metric-trend-hint'),
  metricStatGrid: document.getElementById('metric-stat-grid'),
  metricOverview: document.getElementById('metric-overview'),
  metricOverviewGrid: document.getElementById('metric-overview-grid'),
  metricWeekly: document.getElementById('metric-weekly'),
  metricWeeklyList: document.getElementById('metric-weekly-list'),
  metricsStatus: document.getElementById('metrics-status'),
  metricsList: document.getElementById('metrics-list'),
  metricsMore: document.getElementById('metric-more'),
  metricsEmpty: document.getElementById('metrics-empty'),
  authGate: document.getElementById('auth-gate'),
  authForm: document.getElementById('auth-form'),
  authToken: document.getElementById('auth-token'),
  authError: document.getElementById('auth-error'),
  vitals: document.getElementById('vitals'),
  vitalsText: document.getElementById('vitals-text'),
  banner: document.getElementById('config-banner'),
  railScan: document.getElementById('rail-scan'),
  railLog: document.getElementById('rail-log'),
  railLogList: document.getElementById('rail-log-list'),
  agents: Array.from(document.querySelectorAll('.agent')),
  readout: {
    mode: document.getElementById('r-mode'),
    agents: document.getElementById('r-agents'),
    tasks: document.getElementById('r-tasks'),
    time: document.getElementById('r-time'),
    usage: document.getElementById('r-usage'),
    costRow: document.getElementById('readout-cost-row'),
    cost: document.getElementById('r-cost'),
    kb: document.getElementById('r-kb'),
    session: document.getElementById('r-session'),
  },
};

const AGENT_IDS = ['consultation_agent', 'diagnostic_agent', 'research_agent'];

const AGENT_NAMES = {
  consultation_agent: '健康咨询',
  diagnostic_agent: '症状诊断',
  research_agent: '医学研究',
};

const initialSessionFromUrl = new URLSearchParams(window.location.search).get('session');
let sessionId = initialSessionFromUrl || localStorage.getItem('medix.session') || null;
if (initialSessionFromUrl) {
  localStorage.setItem('medix.session', initialSessionFromUrl);
}
let busy = false;
let activeController = null;
let pricing = null;
let latestUsage = null;
let historyOpen = false;
let kbOpen = false;
let diagOpen = false;
let calcOpen = false;
let metricsOpen = false;
let remindersOpen = false;
let authGateOpen = false;
let historyQuery = '';
let historyOffset = 0;
let historyTotal = 0;
let historyLoading = false;
let favoritesOnly = false;
let archivedOnly = false;
let historyTag = '';
let historyProfile = '';
let bulkMode = false;
let selectedSessions = new Set();
let historyItemMap = new Map();
let compareOpen = false;
let historyDays = 0;
let historyRequestSeq = 0;
let usageTrendRequestSeq = 0;
let usageBudgetRequestSeq = 0;
let kbSearching = false;

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function activeDrawer() {
  if (authGateOpen) return els.authGate;
  if (historyOpen) return els.historyDrawer;
  if (kbOpen) return els.kbDrawer;
  if (diagOpen) return els.diagnoseDrawer;
  if (calcOpen) return els.calcDrawer;
  if (metricsOpen) return els.metricsDrawer;
  if (remindersOpen) return els.remindersDrawer;
  if (compareOpen) return els.compareDrawer;
  return null;
}

function syncPageInert() {
  const shell = document.querySelector('.shell');
  if (!shell) return;
  if (historyOpen || kbOpen || diagOpen || calcOpen || metricsOpen || remindersOpen || compareOpen || authGateOpen) {
    shell.setAttribute('inert', '');
    shell.setAttribute('aria-hidden', 'true');
  } else {
    shell.removeAttribute('inert');
    shell.removeAttribute('aria-hidden');
  }
}

function showAuthGate(message = '') {
  if (message) {
    els.authError.textContent = message;
    els.authError.hidden = false;
  }
  if (authGateOpen) return;

  authGateOpen = true;
  els.authGate.hidden = false;
  syncPageInert();
  els.authToken.focus();
}

function hideAuthGate() {
  authGateOpen = false;
  els.authGate.hidden = true;
  els.authError.hidden = true;
  els.authError.textContent = '';
  syncPageInert();
}

// 所有同源 /api 请求自动带上访问令牌（未开启访问保护时为空操作）
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  if (url.startsWith('/api/')) {
    const token = sessionStorage.getItem('medix.token');
    if (token) {
      const headers = new Headers(init.headers || {});
      headers.set('X-Medix-Token', token);
      init = { ...init, headers };
    }
  }

  return nativeFetch(input, init).then((response) => {
    if (response.status === 401) {
      const hasToken = Boolean(sessionStorage.getItem('medix.token'));
      showAuthGate(
        hasToken ? '访问令牌无效或已失效，请重新输入。' : ''
      );
    }
    return response;
  });
};
let userPausedFollow = false;

const THEME_KEY = 'medix.theme';

function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function updateThemeToggle(dark) {
  els.themeToggle.setAttribute('aria-label', dark ? '切换到浅色模式' : '切换深色模式');
  els.themeToggle.title = dark ? '切换到浅色模式' : '切换深色模式';
  els.themeToggle.setAttribute('aria-pressed', String(dark));
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // 隐私模式下 localStorage 可能不可用，忽略即可
  }
  updateThemeToggle(theme === 'dark');
}

function initTheme() {
  // 主题已由 index.html 的内联脚本在首屏渲染前设置
  updateThemeToggle(currentTheme() === 'dark');
}

/* ---------- 工具 ---------- */

/**
 * 生成心电波形。
 * 一个心动周期 = P 波 + QRS 波群 + T 波（基线 y=30，R 峰 y=7，量程 44）。
 * 周期数按容器实际宽度算，viewBox 宽度随之调整，这样波形始终接近 1:1，
 * 不会在窄屏上被压成一团。走纸速度用 --ecg-dur 控制，保证不同屏宽观感一致。
 */
const ECG_BEAT_W = 75;
const ECG_BEAT = [
  [0, 30], [12, 30], [16, 27], [20, 30], [30, 30],
  [34, 33], [37, 7], [40, 36], [43, 30],
  [52, 30], [58, 23], [66, 30], [ECG_BEAT_W, 30],
];

function renderEcg() {
  const trace = document.querySelector('.vitals-trace');
  if (!trace) return;

  const width = trace.clientWidth || 600;
  const beats = Math.max(4, Math.ceil(width / ECG_BEAT_W));
  const totalW = beats * ECG_BEAT_W;

  let d = '';
  for (let b = 0; b < beats; b++) {
    const offset = b * ECG_BEAT_W;
    ECG_BEAT.forEach(([x, y], i) => {
      d += `${b === 0 && i === 0 ? 'M' : 'L'}${offset + x} ${y}`;
    });
  }

  document.querySelectorAll('.vitals-track svg').forEach((svg) => {
    svg.setAttribute('viewBox', `0 0 ${totalW} 44`);
    svg.querySelector('path').setAttribute('d', d);
  });

  // 走纸速度约 78 px/s，太快像故障，太慢看不出在跑
  const dur = Math.min(14, Math.max(3.2, totalW / 78));
  els.vitals.style.setProperty('--ecg-dur', `${dur.toFixed(2)}s`);
}

function setVitals(state, text) {
  els.vitals.dataset.state = state;
  els.vitalsText.textContent = text;
}

function agentEl(id) {
  return els.agents.find((el) => el.dataset.agent === id) || null;
}

function setAgentState(id, state, label) {
  const el = agentEl(id);
  if (!el) return;
  el.dataset.state = state;
  const stateEl = el.querySelector('.agent-state');
  if (stateEl && label) stateEl.textContent = label;
}

function shortSession(id) {
  return id ? '#' + id.slice(-8) : '—';
}

function resetRail() {
  AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '待命'));
  els.railLogList.replaceChildren();
  els.railLog.hidden = true;
  els.readout.mode.textContent = '—';
  els.readout.agents.textContent = '—';
  els.readout.tasks.textContent = '—';
  els.readout.time.textContent = '—';
  els.readout.usage.textContent = '—';
  els.readout.usage.title = '';
  latestUsage = null;
  updateCostDisplay();
  els.readout.session.textContent = shortSession(sessionId);
}

function logStep(text, live = true) {
  els.railLog.hidden = false;
  const li = document.createElement('li');
  li.textContent = text;
  if (live) li.dataset.live = '1';
  els.railLogList.appendChild(li);
  els.railLog.scrollIntoView({ block: 'nearest' });
}

function isNearBottom() {
  return els.thread.scrollHeight - els.thread.scrollTop - els.thread.clientHeight < 120;
}

/**
 * 只有用户停留在底部附近时才自动跟随新内容；
 * 用户向上翻看历史时，改为显示「回到最新」按钮，避免把阅读位置拽走。
 */
function scrollThreadToBottom(force = false) {
  if (force || isNearBottom()) {
    // smooth 滚动在后台标签页/部分环境下不生效，强制跟随内容时改用即时定位
    const previous = els.thread.style.scrollBehavior;
    els.thread.style.scrollBehavior = 'auto';
    els.thread.scrollTop = els.thread.scrollHeight;
    els.thread.style.scrollBehavior = previous;
    els.jumpLatest.hidden = true;
  } else {
    els.jumpLatest.hidden = false;
  }
}

/** 窄屏是整页滚动，且会诊面板排在问诊区上方 */
function isStacked() {
  return window.matchMedia('(max-width: 980px)').matches;
}

/* ---------- 渲染会诊意见 ---------- */

// 行内 Markdown：**粗体**、`代码`、[文本](https://...)，全部通过 DOM 创建，不使用 innerHTML
const INLINE_PATTERN = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;

function appendInline(parent, text) {
  const source = String(text);
  INLINE_PATTERN.lastIndex = 0;
  let cursor = 0;
  let match;

  while ((match = INLINE_PATTERN.exec(source)) !== null) {
    if (match.index > cursor) {
      parent.appendChild(document.createTextNode(source.slice(cursor, match.index)));
    }

    const token = match[0];
    if (token.startsWith('**')) {
      const strong = document.createElement('strong');
      strong.textContent = token.slice(2, -2);
      parent.appendChild(strong);
    } else if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      parent.appendChild(code);
    } else {
      const labelEnd = token.indexOf(']');
      const link = document.createElement('a');
      link.textContent = token.slice(1, labelEnd);
      link.href = match[2];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      parent.appendChild(link);
    }

    cursor = INLINE_PATTERN.lastIndex;
  }

  if (cursor < source.length) {
    parent.appendChild(document.createTextNode(source.slice(cursor)));
  }
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function isTableSeparator(line) {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{2,}:?$/.test(cell.replace(/\s+/g, '')));
}

function appendTable(container, rows) {
  const table = document.createElement('table');
  table.className = 'md-table';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const cell of rows[0]) {
    const th = document.createElement('th');
    appendInline(th, cell);
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  if (rows.length > 1) {
    const tbody = document.createElement('tbody');
    for (const row of rows.slice(1)) {
      const tr = document.createElement('tr');
      for (const cell of row) {
        const td = document.createElement('td');
        appendInline(td, cell);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
  }

  // 窄屏下表格允许横向滚动，避免撑破会诊意见卡片
  const wrapper = document.createElement('div');
  wrapper.className = 'md-table-wrap';
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

// 正文块级 Markdown：标题、无序/有序列表、引用、表格、分隔线，其余按段落处理
function appendBody(container, text) {
  const lines = String(text).split(/\r?\n/);

  let list = null;
  let listType = null;
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const p = document.createElement('p');
    appendInline(p, paragraph.join(' '));
    container.appendChild(p);
    paragraph = [];
  };

  const flushList = () => {
    list = null;
    listType = null;
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();

    if (!line) {
      flushParagraph();
      flushList();
      i += 1;
      continue;
    }

    // 表格：第二行是 |---|---| 分隔行
    if (line.startsWith('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushParagraph();
      flushList();
      const rows = [splitTableRow(line)];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      appendTable(container, rows);
      continue;
    }

    // 标题
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const h = document.createElement('h4');
      h.className = 'md-heading';
      appendInline(h, heading[2]);
      container.appendChild(h);
      i += 1;
      continue;
    }

    // 分隔线
    if (/^[-*_]{3,}$/.test(line)) {
      flushParagraph();
      flushList();
      container.appendChild(document.createElement('hr'));
      i += 1;
      continue;
    }

    // 引用
    if (line.startsWith('>')) {
      flushParagraph();
      flushList();
      const quote = document.createElement('blockquote');
      appendInline(quote, line.replace(/^>\s?/, ''));
      container.appendChild(quote);
      i += 1;
      continue;
    }

    // 无序列表
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (!list || listType !== 'ul') {
        list = document.createElement('ul');
        listType = 'ul';
        container.appendChild(list);
      }
      const li = document.createElement('li');
      appendInline(li, bullet[1]);
      list.appendChild(li);
      i += 1;
      continue;
    }

    // 有序列表
    const numbered = line.match(/^(\d+)\s*[.、)]\s*(.+)$/);
    if (numbered) {
      flushParagraph();
      if (!list || listType !== 'ol') {
        list = document.createElement('ol');
        listType = 'ol';
        container.appendChild(list);
      }
      const li = document.createElement('li');
      appendInline(li, numbered[2]);
      list.appendChild(li);
      i += 1;
      continue;
    }

    // 普通段落
    flushList();
    paragraph.push(line);
    i += 1;
  }

  flushParagraph();
}

function parseSections(text) {
  const sections = [];
  const re = /【([^】\n]+)】/g;
  let cursor = 0;
  let currentTitle = null;
  let match;

  while ((match = re.exec(text)) !== null) {
    const body = text.slice(cursor, match.index).trim();
    if (currentTitle !== null || body) {
      sections.push({ title: currentTitle, body });
    }
    currentTitle = match[1];
    cursor = re.lastIndex;
  }

  const tail = text.slice(cursor).trim();
  if (currentTitle !== null) {
    sections.push({ title: currentTitle, body: tail });
  } else if (tail) {
    sections.push({ title: null, body: tail });
  }

  return sections.length ? sections : [{ title: null, body: text }];
}

/**
 * 识别风险等级。配色对齐急诊分诊的四级色标。
 *
 * 优先匹配「风险等级：高危」这类明确声明；模型写法不固定（中危/中风险/低危），
 * 都要覆盖。最后才退回按紧急征象判断 —— "请立即就医"这类词常出现在条件句里，
 * 不能直接当作紧急级别。
 */
function detectRisk(text) {
  const levelLabels = {
    emergency: '风险等级 · 紧急，建议立即就医',
    high: '风险等级 · 高，建议尽快就诊',
    medium: '风险等级 · 中，建议门诊评估',
    low: '风险等级 · 低，可先自我观察',
  };

  // 形如「综合风险：中危」「风险等级：高危」
  const tagged = text.match(/(?:综合)?风险(?:等级|程度|评级)?\s*[：:]\s*(紧急|高危|中危|低危)/);
  if (tagged) {
    const level = { 紧急: 'emergency', 高危: 'high', 中危: 'medium', 低危: 'low' }[tagged[1]];
    return { level, label: levelLabels[level] };
  }

  // 形如「**中风险**」「**高危**」
  // 注意「高危人群」「高风险人群」是在描述人群，不是风险等级
  const plain = [
    { level: 'emergency', re: /紧急状态/ },
    { level: 'high', re: /高风险(?!人群)|高度风险|高危(?!人群)/ },
    { level: 'medium', re: /中风险|中等风险|中危(?!人群)/ },
    { level: 'low', re: /低风险|风险较低|风险不高|低危(?!人群)/ },
  ];
  for (const row of plain) {
    if (row.re.test(text)) return { level: row.level, label: levelLabels[row.level] };
  }

  if (/立即就医|急救|拨打\s*120|危及生命/.test(text)) {
    return { level: 'high', label: '答复含紧急征象提示，请注意' };
  }
  return null;
}

/* ---------- 意见操作（复制 / 导出 / 再次会诊） ---------- */

function makeAction(label, onClick) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'note-action';
  btn.textContent = label;
  btn.addEventListener('click', onClick);
  return btn;
}

/**
 * 会诊意见反馈：同一按钮再次点击可取消。
 * 必须带上 message_id 与所属 session_id，避免切换会话后写错记录。
 */
function renderFeedback(payload) {
  const messageId = Number(payload.assistant_message_id);
  const targetSession = payload.session_id || sessionId;
  if (!messageId || !targetSession) return null;

  const box = document.createElement('div');
  box.className = 'note-feedback';

  const label = document.createElement('span');
  label.className = 'feedback-label';
  label.textContent = '这条意见有帮助吗？';
  box.appendChild(label);

  const status = document.createElement('span');
  status.className = 'feedback-status';
  status.setAttribute('role', 'status');

  let current = Number((payload.feedback && payload.feedback.rating) || 0);
  let savedComment = String((payload.feedback && payload.feedback.comment) || '');
  let form = null;
  const buttons = {};

  const paint = () => {
    for (const [rating, btn] of Object.entries(buttons)) {
      const active = Number(rating) === current;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  };

  const closeForm = () => {
    if (form) {
      form.remove();
      form = null;
    }
  };

  const submit = async (next, comment = '') => {
    closeForm();
    const previous = current;
    const previousComment = savedComment;
    current = next;
    savedComment = comment;
    paint();
    Object.values(buttons).forEach((btn) => { btn.disabled = true; });

    try {
      const res = await fetch(
        `/api/sessions/${encodeURIComponent(targetSession)}/feedback`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message_id: messageId,
            rating: next,
            comment,
          }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `服务返回 ${res.status}`);
      }
      const data = await res.json();
      current = Number(data.rating || 0);
      savedComment = String(data.comment || '');
      payload.feedback = current
        ? { rating: current, comment: savedComment }
        : null;
      paint();
      status.textContent = current === 1
        ? '已标记有用，感谢反馈'
        : current === -1
        ? savedComment
          ? `已记录：${savedComment}`
          : '已记录，我们会继续改进'
        : '已取消反馈';
      if (historyOpen) {
        loadRecentFeedback();
        loadQualityInsights();
      }
    } catch (err) {
      current = previous;
      savedComment = previousComment;
      paint();
      status.textContent = `反馈失败：${err.message || err}`;
    } finally {
      Object.values(buttons).forEach((btn) => { btn.disabled = false; });
    }
  };

  const showCommentForm = () => {
    if (form) {
      form.querySelector('input').focus();
      return;
    }

    form = document.createElement('div');
    form.className = 'feedback-comment-form';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'feedback-comment-input';
    input.maxLength = 200;
    input.placeholder = '可选：哪里需要改进？';
    input.setAttribute('aria-label', '反馈原因（可选）');

    const commit = () => submit(-1, input.value.trim().slice(0, 200));
    const sendBtn = makeAction('提交', commit);
    const skipBtn = makeAction('跳过', () => submit(-1, ''));
    sendBtn.setAttribute('aria-label', '提交待改进反馈');
    skipBtn.setAttribute('aria-label', '不填写原因，直接提交待改进');

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        closeForm();
      }
    });

    form.append(input, sendBtn, skipBtn);
    box.appendChild(form);
    input.focus();
  };

  for (const rating of [1, -1]) {
    const btn = makeAction(rating === 1 ? '有用' : '待改进', () => {
      if (rating === 1) {
        closeForm();
        submit(current === 1 ? 0 : 1, '');
        return;
      }
      if (current === -1) {
        closeForm();
        submit(0, '');
        return;
      }
      showCommentForm();
    });
    btn.classList.add('feedback-btn');
    btn.dataset.rating = String(rating);
    btn.setAttribute(
      'aria-label',
      rating === 1 ? '这条会诊意见有帮助' : '这条会诊意见需要改进'
    );
    buttons[rating] = btn;
    box.appendChild(btn);
  }

  paint();
  if (current === -1 && savedComment) {
    status.textContent = `已记录：${savedComment}`;
  }
  box.appendChild(status);
  return box;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 非安全上下文（例如局域网 http）下 clipboard API 不可用，退回 execCommand
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

function collectAnswerSections(text, pattern) {
  const sections = {};
  const matches = [...text.matchAll(pattern)];
  for (let index = 0; index < matches.length; index += 1) {
    const name = matches[index][1].trim();
    const start = matches[index].index + matches[index][0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
    sections[name] = text.slice(start, end).trim();
  }
  return sections;
}

function splitAnswerSections(text) {
  const bracketed = collectAnswerSections(text, /【([^】]{1,20})】/g);
  if (Object.keys(bracketed).length) return bracketed;
  return collectAnswerSections(text, /\*\*([^*\n]{1,20})\*\*/g);
}

function formatPatientContext(context) {
  const labels = {
    age: '年龄',
    sex: '性别',
    duration: '病程',
    history: '既往史',
    medications: '用药',
    allergies: '过敏史',
  };
  return Object.entries(labels)
    .filter(([key]) => context && context[key])
    .map(([key, label]) => `${label}：${context[key]}`)
    .join('；');
}

/** 把急症预检与用药安全提示写进 Markdown，保证导出/打印信息完整。 */
function buildSafetyMarkdown(payload, heading) {
  const lines = [];

  if (payload.triage && payload.triage.level === 'emergency') {
    const matched = (payload.triage.matches || []).join('、');
    lines.push(`${heading} 急症预检`, '');
    lines.push(
      `${matched ? `命中：${matched}。` : ''}`
      + (payload.triage.message || '请立即拨打 120 或前往急诊科。'),
      ''
    );
  }

  const safety = payload.medication_safety;
  if (safety && safety.alerts && safety.alerts.length) {
    lines.push(`${heading} 用药安全提示`, '');
    for (const alert of safety.alerts) {
      lines.push(`- **${alert.title}**：${alert.detail}`);
    }
    lines.push('', '> 以上为规则提示，不能替代医生或药师的判断。', '');
  }

  return lines;
}

function buildSOAP(payload, question) {
  const answer = (payload.answer || '').trim();
  const sections = splitAnswerSections(answer);
  const risk = sections['风险评估'] || '';
  const diagnosis = sections['诊断分析'] || '';
  const evidence = sections['医学证据'] || '';
  const core = sections['核心建议'] || '';

  const subjective = [];
  if (question) subjective.push(`主诉：${question.trim()}`);
  const contextText = formatPatientContext(payload.patient_context || {});
  if (contextText) subjective.push(`补充信息：${contextText}`);

  const objective = [];
  if (payload.triage && payload.triage.level && payload.triage.level !== 'none') {
    objective.push(`分诊提示：${payload.triage.message || payload.triage.level}`);
  }
  const safety = payload.medication_safety;
  if (safety && safety.alerts && safety.alerts.length) {
    objective.push(
      `用药安全提示：${safety.alerts.map((alert) => alert.title).join('；')}`
    );
  }
  if (evidence) objective.push(`医学证据：${evidence}`);
  const sources = (payload.sources || [])
    .map((source) => source.title || source.filename)
    .filter(Boolean);
  if (sources.length) objective.push(`参考来源：${sources.join('、')}`);

  let assessment = [risk, diagnosis].filter(Boolean).join('\n\n');
  if (!assessment) {
    const otherSections = Object.entries(sections)
      .filter(([name]) => !['核心建议', '免责声明'].includes(name))
      .map(([, text]) => text)
      .filter(Boolean);
    assessment = otherSections.join('\n\n')
      || (Object.keys(sections).length ? '详见会诊意见。' : answer);
  }
  const plan = [];
  if (core) {
    plan.push(core);
  } else if (payload.suggestions && payload.suggestions.length) {
    plan.push(
      payload.suggestions.map((item, index) => `${index + 1}. ${item}`).join('\n')
    );
  }

  return [
    'SOAP 会诊摘要',
    `S（主观资料）\n${subjective.join('\n') || '未提供'}`,
    `O（客观资料）\n${objective.join('\n') || '在线问诊，未提供检查结果。'}`,
    `A（评估）\n${assessment || '未提供'}`,
    `P（计划）\n${plan.join('\n\n') || '未提供'}`,
    payload.disclaimer ? `免责声明：${payload.disclaimer}` : '',
  ].filter(Boolean).join('\n\n');
}

function buildMarkdown(payload, question) {
  const lines = ['# MediX 会诊意见', ''];
  lines.push(`- 会话编号：${sessionId || '—'}`);
  lines.push(`- 导出时间：${new Date().toLocaleString()}`);
  const usageText = formatUsage(payload.usage, payload.llm_calls);
  if (usageText !== '—') lines.push(`- 模型用量：${usageText}`);
  lines.push('');

  if (question) {
    lines.push('## 主诉', '', question, '');
  }

  const contextText = formatPatientContext(payload.patient_context || {});
  if (contextText) {
    lines.push('## 补充信息', '', contextText, '');
  }

  lines.push(...buildSafetyMarkdown(payload, '##'));

  lines.push('## 会诊意见', '', (payload.answer || '').trim(), '');

  if (payload.suggestions && payload.suggestions.length) {
    lines.push('## 诊疗建议', '');
    payload.suggestions.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
    lines.push('');
  }

  if (payload.agent_contributions && payload.agent_contributions.length) {
    lines.push('## 各科室意见', '');
    payload.agent_contributions.forEach((contribution) => {
      lines.push(
        `### ${contribution.agent_name || contribution.agent_id}`,
        '',
        (contribution.content || '').trim(),
        ''
      );
    });
  }

  if (payload.sources && payload.sources.length) {
    lines.push('## 参考来源', '');
    payload.sources.forEach((source, index) => {
      const meta = [
        source.source,
        source.filename,
        typeof source.score === 'number'
          ? `${Math.round(source.score * 100)}%`
          : '',
      ].filter(Boolean).join(' · ');
      lines.push(
        `${index + 1}. ${source.title || source.filename || '医学知识库'}（${meta}）`
      );
      if (source.snippet) {
        lines.push(`   > ${String(source.snippet).replace(/\n/g, ' ')}`);
      }
    });
    lines.push('');
  }

  if (payload.disclaimer) {
    lines.push('## 免责声明', '', payload.disclaimer, '');
  }

  lines.push('---', '', '本文件由 MediX 生成，仅供参考，不能替代执业医师的诊断与治疗。', '');
  return lines.join('\n');
}

function printAnswer() {
  const details = [...document.querySelectorAll('.thread details')];
  const previous = details.map((item) => item.open);
  details.forEach((item) => { item.open = true; });

  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    details.forEach((item, index) => { item.open = previous[index]; });
    window.removeEventListener('afterprint', restore);
  };

  window.addEventListener('afterprint', restore);
  window.print();
  setTimeout(restore, 1500);
}

function downloadFile(content, filename, mimeType = 'text/markdown;charset=utf-8') {
  const blob = content instanceof Blob
    ? content
    : new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadMarkdown(payload, question) {
  const stamp = (sessionId || String(Date.now())).slice(-12);
  downloadFile(buildMarkdown(payload, question), `medix-consult-${stamp}.md`);
}

function buildSessionMarkdown(item, messages) {
  const lines = [`# ${item.title || 'MediX 会诊记录'}`, ''];
  lines.push(`- 会话编号：${item.id}`);
  lines.push(`- 更新时间：${formatTime(item.updated_at || item.created_at)}`);
  lines.push(`- 消息数：${messages.length}`);
  lines.push('');

  let turn = 0;
  let question = '';
  for (const message of messages) {
    if (message.role === 'user') {
      question = message.content || '';
    } else if (message.role === 'assistant' && message.content) {
      turn += 1;
      const metadata = message.metadata || {};
      lines.push(`## 第 ${turn} 轮`, '');
      if (question) {
        lines.push('### 主诉', '', question, '');
      }

      const contextText = formatPatientContext(metadata.patient_context || {});
      if (contextText) {
        lines.push('#### 补充信息', '', contextText, '');
      }

      lines.push(...buildSafetyMarkdown(metadata, '####'));

      lines.push('### 会诊意见', '', message.content.trim(), '');

      const turnUsage = formatUsage(metadata.usage, metadata.llm_calls);
      if (turnUsage !== '—') {
        lines.push(`> 模型用量：${turnUsage}`, '');
      }

      if (metadata.suggestions && metadata.suggestions.length) {
        lines.push('#### 诊疗建议', '');
        metadata.suggestions.forEach((suggestion, index) => {
          lines.push(`${index + 1}. ${suggestion}`);
        });
        lines.push('');
      }

      if (metadata.agent_contributions && metadata.agent_contributions.length) {
        lines.push('#### 各科室意见', '');
        metadata.agent_contributions.forEach((contribution) => {
          const name = contribution.agent_name
            || AGENT_NAMES[contribution.agent_id]
            || contribution.agent_id;
          const stats = contributionStats(contribution);
          const suffix = stats.length ? `（${stats.join(' · ')}）` : '';
          lines.push(
            `##### ${name}${suffix}`,
            '',
            (contribution.content || '').trim(),
            ''
          );
        });
      }

      if (metadata.sources && metadata.sources.length) {
        lines.push('#### 参考来源', '');
        metadata.sources.forEach((source, index) => {
          const meta = [
            source.source,
            source.filename,
            typeof source.score === 'number'
              ? `${Math.round(source.score * 100)}%`
              : '',
          ].filter(Boolean).join(' · ');
          lines.push(
            `${index + 1}. ${source.title || source.filename || '医学知识库'}（${meta}）`
          );
          if (source.snippet) {
            lines.push(`   > ${String(source.snippet).replace(/\n/g, ' ')}`);
          }
        });
        lines.push('');
      }

      if (metadata.disclaimer) {
        lines.push('#### 免责声明', '', metadata.disclaimer, '');
      }

      question = '';
    }
  }

  lines.push('---', '', '本文件由 MediX 生成，仅供参考，不能替代执业医师的诊断与治疗。', '');
  return lines.join('\n');
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 把会诊意见里的轻量 Markdown 转成 HTML，供独立报告使用（全程转义）。 */
function renderMarkdownLite(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let list = null;
  let paragraph = [];

  const inline = (value) => escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${paragraph.join('<br>')}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item) => `<li>${item}</li>`).join('');
    out.push(`<${list.type}>${items}</${list.type}>`);
    list = null;
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(5, Math.max(2, heading[1].length + 1));
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (!list || list.type !== 'ul') {
        flushList();
        list = { type: 'ul', items: [] };
      }
      list.items.push(inline(bullet[1]));
      continue;
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ordered) {
      flushParagraph();
      if (!list || list.type !== 'ol') {
        flushList();
        list = { type: 'ol', items: [] };
      }
      list.items.push(inline(ordered[1]));
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
      continue;
    }

    flushList();
    paragraph.push(inline(line));
  }

  flushParagraph();
  flushList();
  return out.join('\n');
}

/** 生成可独立打开、可打印为 PDF 的会诊报告（单文件、无外部依赖）。 */
function buildReportHtml(item, messages) {
  const title = item.title || 'MediX 会诊报告';
  const generatedAt = new Date().toLocaleString();
  const updatedAt = formatTime(item.updated_at || item.created_at);

  const turns = [];
  let question = '';
  for (const message of messages) {
    if (message.role === 'user') {
      question = message.content || '';
    } else if (message.role === 'assistant' && message.content) {
      turns.push({
        question,
        metadata: message.metadata || {},
        content: message.content,
      });
      question = '';
    }
  }

  const sections = [];
  turns.forEach((turn, index) => {
    const meta = turn.metadata;
    const parts = [`<section class="turn">`, `<h2>第 ${index + 1} 轮会诊</h2>`];

    if (turn.question) {
      parts.push('<h3>主诉</h3>', `<p>${escapeHtml(turn.question)}</p>`);
    }

    const contextText = formatPatientContext(meta.patient_context || {});
    if (contextText) {
      parts.push('<h3>补充信息</h3>', `<p>${escapeHtml(contextText)}</p>`);
    }

    if (meta.triage && meta.triage.level === 'emergency') {
      const matched = (meta.triage.matches || []).join('、');
      parts.push(
        '<div class="callout danger">',
        '<strong>急症预检</strong>',
        `<p>${escapeHtml((matched ? `命中：${matched}。` : '')
          + (meta.triage.message || '请立即拨打 120 或前往急诊科。'))}</p>`,
        '</div>'
      );
    }

    const safety = meta.medication_safety;
    if (safety && safety.alerts && safety.alerts.length) {
      const alerts = safety.alerts
        .map((alert) => `<li><strong>${escapeHtml(alert.title)}</strong>：${escapeHtml(alert.detail)}</li>`)
        .join('');
      parts.push(
        '<div class="callout warn">',
        '<strong>用药安全提示</strong>',
        `<ul>${alerts}</ul>`,
        '<p class="muted">以上为规则提示，不能替代医生或药师的判断。</p>',
        '</div>'
      );
    }

    parts.push('<h3>会诊意见</h3>', `<div class="answer">${renderMarkdownLite(turn.content)}</div>`);

    if (meta.suggestions && meta.suggestions.length) {
      const items = meta.suggestions
        .map((suggestion) => `<li>${escapeHtml(suggestion)}</li>`)
        .join('');
      parts.push('<h3>诊疗建议</h3>', `<ol>${items}</ol>`);
    }

    if (meta.agent_contributions && meta.agent_contributions.length) {
      parts.push('<h3>各科室意见</h3>');
      for (const contribution of meta.agent_contributions) {
        const name = contribution.agent_name
          || AGENT_NAMES[contribution.agent_id]
          || contribution.agent_id;
        const stats = contributionStats(contribution);
        parts.push(
          `<div class="contribution"><h4>${escapeHtml(name)}`
          + `${stats.length ? ` <span class="muted">（${escapeHtml(stats.join(' · '))}）</span>` : ''}</h4>`,
          `<div class="answer">${renderMarkdownLite(contribution.content || '')}</div></div>`
        );
      }
    }

    if (meta.sources && meta.sources.length) {
      parts.push('<h3>参考来源</h3>', '<ol class="sources">');
      meta.sources.forEach((source) => {
        const sourceMeta = [
          source.source,
          source.filename,
          typeof source.score === 'number' ? `${Math.round(source.score * 100)}%` : '',
        ].filter(Boolean).join(' · ');
        parts.push(
          `<li>${escapeHtml(source.title || source.filename || '医学知识库')}`
          + `${sourceMeta ? ` <span class="muted">（${escapeHtml(sourceMeta)}）</span>` : ''}`
          + `${source.snippet ? `<br><span class="muted">${escapeHtml(String(source.snippet).replace(/\n/g, ' '))}</span>` : ''}`
          + '</li>'
        );
      });
      parts.push('</ol>');
    }

    const usageText = formatUsage(meta.usage, meta.llm_calls);
    if (usageText !== '—') {
      parts.push(`<p class="muted">模型用量：${escapeHtml(usageText)}</p>`);
    }

    if (meta.disclaimer) {
      parts.push(`<p class="disclaimer">${escapeHtml(meta.disclaimer)}</p>`);
    }

    parts.push('</section>');
    sections.push(parts.join('\n'));
  });

  const empty = '<p class="muted">这次会诊还没有可导出的内容。</p>';

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)} · MediX 会诊报告</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 16px 64px;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
    color: #1f2933; background: #f5f7fa; line-height: 1.7;
  }
  .report {
    max-width: 820px; margin: 0 auto; background: #fff;
    border-radius: 12px; padding: 40px 44px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  }
  .brand { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; border-bottom: 3px solid #0f766e; padding-bottom: 16px; margin-bottom: 24px; }
  .brand h1 { margin: 0; font-size: 24px; letter-spacing: 0.5px; }
  .brand .stamp { color: #64748b; font-size: 12px; text-align: right; }
  .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px 24px; margin: 0 0 24px; padding: 14px 16px; background: #f0fdfa; border-radius: 8px; font-size: 13px; }
  .meta dt { color: #64748b; margin: 0; }
  .meta dd { margin: 0 0 6px; font-weight: 600; }
  .turn { border-top: 1px solid #e2e8f0; padding-top: 20px; margin-top: 20px; }
  .turn:first-of-type { border-top: 0; padding-top: 0; margin-top: 0; }
  h2 { font-size: 18px; margin: 0 0 12px; color: #0f766e; }
  h3 { font-size: 15px; margin: 20px 0 8px; color: #334155; }
  h4 { font-size: 14px; margin: 14px 0 6px; }
  p { margin: 8px 0; }
  ul, ol { margin: 8px 0; padding-left: 22px; }
  li { margin: 4px 0; }
  blockquote { margin: 10px 0; padding: 8px 14px; border-left: 3px solid #94a3b8; background: #f8fafc; color: #475569; }
  .callout { border-radius: 8px; padding: 12px 16px; margin: 14px 0; font-size: 14px; }
  .callout p, .callout ul { margin: 6px 0 0; }
  .callout.danger { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
  .callout.warn { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }
  .muted { color: #64748b; font-size: 12px; }
  .contribution { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 16px; margin: 10px 0; }
  .contribution h4 { margin: 4px 0 8px; }
  .disclaimer { margin-top: 16px; padding: 10px 14px; background: #f8fafc; border-radius: 8px; color: #64748b; font-size: 12px; }
  footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 12px; }
  .print-btn {
    position: fixed; right: 24px; bottom: 24px; padding: 12px 20px;
    border: 0; border-radius: 999px; background: #0f766e; color: #fff;
    font-size: 14px; cursor: pointer; box-shadow: 0 8px 20px rgba(15, 118, 110, 0.35);
  }
  .print-btn:hover { background: #115e59; }
  @media print {
    body { background: #fff; padding: 0; }
    .report { box-shadow: none; border-radius: 0; padding: 0; max-width: none; }
    .print-btn { display: none; }
    .turn { break-inside: avoid-page; }
  }
</style>
</head>
<body>
<main class="report">
  <header class="brand">
    <h1>MediX 多智能体会诊报告</h1>
    <span class="stamp">生成时间：${escapeHtml(generatedAt)}</span>
  </header>
  <dl class="meta">
    <div><dt>报告标题</dt><dd>${escapeHtml(title)}</dd></div>
    <div><dt>会话编号</dt><dd>${escapeHtml(item.id || '—')}</dd></div>
    <div><dt>最近更新</dt><dd>${escapeHtml(updatedAt)}</dd></div>
    <div><dt>会诊轮次</dt><dd>${turns.length}</dd></div>
  </dl>
  ${sections.length ? sections.join('\n') : empty}
  <footer>
    本报告由 MediX 自动生成，仅供参考，不能替代执业医师的诊断与治疗。
    如症状加重或出现新的不适，请及时就医。
  </footer>
</main>
<button type="button" class="print-btn" onclick="window.print()">打印 / 保存为 PDF</button>
</body>
</html>`;
}

/** 生成就诊摘要：患者档案 + 近期指标 + 随访提醒 + 近期会诊，打印友好。 */
function buildVisitSummaryHtml({
  profile, context, metrics, reminders, sessions, targets, summaries,
}) {
  const generatedAt = new Date().toLocaleString();
  const targetList = Array.isArray(targets) ? targets : [];

  const findTarget = (metric) => targetList.find(
    (target) => target.metric === metric
      && (target.profile || '') === (profile || '')
  ) || null;

  const isOverTarget = (item, target) => {
    if (!target) return false;
    if (targetIsLowerBound(item.metric)) {
      return Number(item.value1) < target.value1;
    }
    if (item.metric === 'bp') {
      return Number(item.value1) > target.value1
        || (target.value2 != null && Number(item.value2) > target.value2);
    }
    return Number(item.value1) > target.value1;
  };

  const contextLabels = {
    age: '年龄',
    sex: '性别',
    duration: '病程',
    history: '既往史',
    medications: '用药',
    allergies: '过敏史',
  };
  const contextRows = Object.entries(contextLabels)
    .filter(([key]) => context[key])
    .map(([key, label]) => (
      `<tr><th>${label}</th><td>${escapeHtml(context[key])}</td></tr>`
    ))
    .join('\n');

  const summaryRows = (summaries || []).map((summary) => {
    const metric = summary.metric;
    const v1 = summary.value1 || {};
    const v2 = summary.value2 || {};
    const pair = (first, second, fmt) => (metric === 'bp'
      ? `${fmt(first)}/${fmt(second)}`
      : fmt(first));
    const unit = METRIC_UNITS[metric] || '';
    const average = pair(v1.avg, v2.avg, formatStatNumber);
    const range = pair(v1.min, v2.min, formatStatNumber)
      + '–'
      + pair(v1.max, v2.max, formatStatNumber);
    const targetText = summary.target
      ? `${summary.within_target}/${summary.count}`
      : '—';
    return '<tr>'
      + `<td>${escapeHtml(METRIC_NAMES[metric] || metric)}</td>`
      + `<td>${Number(summary.count) || 0}</td>`
      + `<td>${escapeHtml(
        summary.latest ? formatMetricValue(summary.latest) : '—'
      )}</td>`
      + `<td>${escapeHtml(average)}${unit ? ' ' + escapeHtml(unit) : ''}</td>`
      + `<td>${escapeHtml(range)}</td>`
      + `<td>${escapeHtml(targetText)}</td>`
      + '</tr>';
  }).join('\n');

  const metricRows = metrics.map((item) => {
    const target = findTarget(item.metric);
    const over = isOverTarget(item, target);
    const grade = classifyMetric(item);
    return '<tr>'
      + `<td>${escapeHtml(METRIC_NAMES[item.metric] || item.metric)}</td>`
      + `<td>${escapeHtml(formatMetricValue(item))}`
      + `${grade ? ` <span class="muted">（${escapeHtml(grade.label)}）</span>` : ''}`
      + `${over
        ? ` <span class="overdue">（${targetIsLowerBound(item.metric) ? '低于目标' : '高于目标'}）</span>`
        : ''}</td>`
      + `<td>${escapeHtml(formatTime(item.measured_at))}</td>`
      + `<td>${escapeHtml(item.note || '')}</td>`
      + '</tr>';
  }).join('\n');

  const targetNotes = targetList.map((target) => {
    const direction = targetIsLowerBound(target.metric)
      ? '目标下限'
      : '目标上限';
    return `<li>${escapeHtml(METRIC_NAMES[target.metric] || target.metric)}`
      + ` ${direction} ${escapeHtml(formatMetricTarget(target))}</li>`;
  }).join('\n');

  const reminderItems = reminders.map((item) => {
    const repeat = Number(item.repeat_days) > 0
      ? ` · 每 ${Number(item.repeat_days)} 天重复`
        + (item.repeat_until
          ? `（截止 ${formatReminderDue(item.repeat_until)}）`
          : '')
      : '';
    return `<li>${escapeHtml(item.title)} · 到期 ${escapeHtml(formatReminderDue(item.due_at))}`
      + repeat
      + `${item.note ? `（${escapeHtml(item.note)}）` : ''}`
      + `${reminderIsDue(item.due_at) ? ' <strong class="overdue">已到期</strong>' : ''}`
      + '</li>';
  }).join('\n');

  const sessionItems = sessions.map((item) => (
    `<li>${escapeHtml(formatTime(item.updated_at || item.created_at))}`
    + ` · ${escapeHtml(item.title || '未命名会诊')}</li>`
  )).join('\n');

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>就诊摘要 · MediX</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 16px 64px;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
    color: #1f2933; background: #f5f7fa; line-height: 1.7;
  }
  .report {
    max-width: 820px; margin: 0 auto; background: #fff;
    border-radius: 12px; padding: 40px 44px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  }
  .brand { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; border-bottom: 3px solid #0f766e; padding-bottom: 16px; margin-bottom: 20px; }
  .brand h1 { margin: 0; font-size: 24px; letter-spacing: 0.5px; }
  .brand .stamp { color: #64748b; font-size: 12px; text-align: right; }
  section { margin-top: 22px; }
  h2 { font-size: 16px; margin: 0 0 8px; color: #0f766e; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
  thead th { color: #64748b; font-weight: 600; background: #f8fafc; }
  tbody th { width: 90px; color: #64748b; font-weight: 600; }
  ul { margin: 6px 0; padding-left: 22px; font-size: 13px; }
  li { margin: 3px 0; }
  .muted { color: #64748b; font-size: 12.5px; }
  .overdue { color: #d92b2b; }
  footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 12px; }
  .print-btn {
    position: fixed; right: 24px; bottom: 24px; padding: 12px 20px;
    border: 0; border-radius: 999px; background: #0f766e; color: #fff;
    font-size: 14px; cursor: pointer; box-shadow: 0 8px 20px rgba(15, 118, 110, 0.35);
  }
  .print-btn:hover { background: #115e59; }
  @media print {
    body { background: #fff; padding: 0; }
    .report { box-shadow: none; border-radius: 0; padding: 0; max-width: none; }
    .print-btn { display: none; }
    section { break-inside: avoid-page; }
  }
</style>
</head>
<body>
<main class="report">
  <header class="brand">
    <h1>就诊摘要</h1>
    <span class="stamp">生成时间：${escapeHtml(generatedAt)}</span>
  </header>

  <section>
    <h2>基本信息</h2>
    <p class="muted">患者档案：${escapeHtml(profile || '未指定')}</p>
    ${contextRows
      ? `<table><tbody>${contextRows}</tbody></table>`
      : '<p class="muted">还没有填写年龄、既往史、用药或过敏史等补充信息。</p>'}
  </section>

  <section>
    <h2>指标概览</h2>
    ${summaryRows
      ? `<table><thead><tr><th>指标</th><th>记录</th><th>最新</th><th>平均</th><th>最低–最高</th><th>达标</th></tr></thead><tbody>${summaryRows}</tbody></table>
        <p class="muted">达标列为未超过目标上限（血氧为不低于目标下限）的记录数；未设置目标的指标显示「—」。</p>`
      : '<p class="muted">还没有健康指标记录。</p>'}
  </section>

  <section>
    <h2>近期健康指标</h2>
    ${targetNotes ? `<ul class="target-notes">${targetNotes}</ul>` : ''}
    ${metricRows
      ? `<table>
          <thead><tr><th>指标</th><th>数值</th><th>测量时间</th><th>备注</th></tr></thead>
          <tbody>${metricRows}</tbody>
        </table>`
      : '<p class="muted">还没有健康指标记录。</p>'}
  </section>

  <section>
    <h2>随访提醒</h2>
    ${reminderItems
      ? `<ul>${reminderItems}</ul>`
      : '<p class="muted">没有待完成的随访提醒。</p>'}
  </section>

  <section>
    <h2>近期会诊</h2>
    ${sessionItems
      ? `<ul>${sessionItems}</ul>`
      : '<p class="muted">还没有会诊记录。</p>'}
  </section>

  <footer>
    本摘要由 MediX 根据本地记录自动生成，仅供就诊沟通参考，不能替代医生的诊断与治疗。
  </footer>
</main>
<button type="button" class="print-btn" onclick="window.print()">打印 / 保存为 PDF</button>
</body>
</html>`;
}

async function fetchMetricSummaries(
  profile, metrics, days = 0, omitEmptyProfile = false
) {
  const types = [...new Set((metrics || []).map((item) => item.metric))];
  const results = await Promise.all(types.map(async (metric) => {
    try {
      const params = new URLSearchParams({ metric });
      if (!(omitEmptyProfile && !profile)) {
        params.set('profile', profile || '');
      }
      if (days > 0) params.set('days', String(days));
      const res = await fetch(
        `/api/metrics/summary?${params.toString()}`
      );
      if (!res.ok) return null;
      const data = await res.json();
      return data && data.count > 0 ? data : null;
    } catch {
      return null;
    }
  }));
  return results.filter(Boolean);
}

async function exportVisitSummary() {
  const profile = els.profileSelect.value || '';
  const context = readPatientContext();
  const original = els.visitSummary.textContent;
  els.visitSummary.disabled = true;
  els.visitSummary.textContent = '生成中…';

  try {
    const metricParams = new URLSearchParams({ limit: '30' });
    if (profile) metricParams.set('profile', profile);
    const sessionParams = new URLSearchParams({ limit: '5' });
    if (profile) sessionParams.set('profile', profile);
    const targetParams = new URLSearchParams();
    if (profile) targetParams.set('profile', profile);
    const reminderParams = new URLSearchParams();
    if (profile) reminderParams.set('profile', profile);
    const [metricsRes, remindersRes, sessionsRes, targetsRes] = await Promise.all([
      fetch(`/api/metrics?${metricParams.toString()}`),
      fetch(`/api/reminders${reminderParams.toString() ? `?${reminderParams}` : ''}`),
      fetch(`/api/sessions?${sessionParams.toString()}`),
      fetch(`/api/metric-targets?${targetParams.toString()}`),
    ]);
    const metrics = metricsRes.ok
      ? (await metricsRes.json()).metrics || []
      : [];
    const reminders = remindersRes.ok
      ? (await remindersRes.json()).reminders || []
      : [];
    const sessions = sessionsRes.ok
      ? (await sessionsRes.json()).sessions || []
      : [];
    const targets = targetsRes.ok
      ? (await targetsRes.json()).targets || []
      : [];
    const summaries = await fetchMetricSummaries(
      profile, metrics, 0, true
    );

    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      buildVisitSummaryHtml({
        profile, context, metrics, reminders, sessions, targets, summaries,
      }),
      `medix-visit-summary-${stamp}.html`,
      'text/html;charset=utf-8'
    );
    setVitals('idle', '已生成就诊摘要');
  } catch (err) {
    window.alert(`生成就诊摘要失败：${err.message || err}`);
  } finally {
    els.visitSummary.textContent = original;
    els.visitSummary.disabled = false;
  }
}

function formatUsage(usage, llmCalls = 0) {
  const total = Number(usage && usage.total_tokens || 0);
  const calls = Number(llmCalls || 0);
  const parts = [];
  if (total > 0) parts.push(`${total.toLocaleString()} tokens`);
  if (calls > 0) parts.push(`${calls} 次调用`);
  return parts.length ? parts.join(' · ') : '—';
}

function usageDetail(usage, llmCalls = 0) {
  const total = Number(usage && usage.total_tokens || 0);
  const calls = Number(llmCalls || 0);
  if (!total && !calls) return '';
  const prompt = Number(usage && usage.prompt_tokens || 0);
  const completion = Number(usage && usage.completion_tokens || 0);
  const parts = [];
  if (total > 0) {
    parts.push(
      `输入 ${prompt.toLocaleString()} · 输出 ${completion.toLocaleString()} · 合计 ${total.toLocaleString()} tokens`
    );
  }
  if (calls > 0) parts.push(`${calls} 次模型调用`);
  const cost = estimateCost(usage);
  if (cost != null && total > 0) parts.push(`约 ${formatCost(cost)}`);
  return parts.join(' · ');
}

function estimateCost(usage) {
  if (!pricing || !usage) return null;
  const prompt = Number(usage.prompt_tokens || 0);
  const completion = Number(usage.completion_tokens || 0);
  const value =
    (prompt / 1_000_000) * Number(pricing.input_per_million || 0) +
    (completion / 1_000_000) * Number(pricing.output_per_million || 0);
  return Number.isFinite(value) ? value : null;
}

function formatCost(value) {
  if (value == null) return '';
  const currency = (pricing && pricing.currency) || '¥';
  if (value === 0) return `${currency}0`;
  if (value < 0.01) return `${currency}${value.toFixed(4)}`;
  if (value < 1) return `${currency}${value.toFixed(3)}`;
  return `${currency}${value.toFixed(2)}`;
}

function updateCostDisplay() {
  const visible = Boolean(pricing);
  els.readout.costRow.hidden = !visible;

  if (!visible) {
    els.readout.cost.textContent = '—';
    els.readout.cost.title = '';
    return;
  }

  const total = Number(latestUsage && latestUsage.total_tokens || 0);
  const cost = estimateCost(latestUsage);
  els.readout.cost.textContent =
    total > 0 && cost != null ? formatCost(cost) : '—';

  if (total > 0) {
    els.readout.cost.title =
      `按输入 ${pricing.currency}${pricing.input_per_million}/百万 tokens、` +
      `输出 ${pricing.currency}${pricing.output_per_million}/百万 tokens 估算，` +
      '实际以服务商账单为准';
  } else {
    els.readout.cost.title = '';
  }
}

function contributionStats(contribution) {
  const stats = [];
  if (contribution.duration != null) stats.push(`耗时 ${contribution.duration}s`);
  if (contribution.tool_calls != null) stats.push(`工具 ${contribution.tool_calls} 次`);
  if (contribution.iterations != null) stats.push(`${contribution.iterations} 轮`);
  if (contribution.usage && Number(contribution.usage.total_tokens || 0) > 0) {
    stats.push(`${Number(contribution.usage.total_tokens).toLocaleString()} tokens`);
  }
  return stats;
}

function contributeStats(contribution) {
  const stats = contributionStats(contribution);
  return stats.length ? stats.join(' · ') : (contribution.task_type || '');
}

function renderContributions(contributions) {
  const details = document.createElement('details');
  details.className = 'contributions';

  const summary = document.createElement('summary');
  summary.textContent = `各科室意见 · ${contributions.length}`;
  details.appendChild(summary);

  for (const contribution of contributions) {
    const block = document.createElement('div');
    block.className = 'contribution';

    const head = document.createElement('div');
    head.className = 'contribution-head';

    const name = document.createElement('strong');
    name.textContent = contribution.agent_name
      || AGENT_NAMES[contribution.agent_id]
      || contribution.agent_id;

    const meta = document.createElement('span');
    meta.className = 'contribution-type';
    meta.textContent = contributeStats(contribution);

    head.append(name, meta);

    const body = document.createElement('div');
    body.className = 'contribution-body';
    appendBody(body, contribution.content || '');

    block.append(head, body);
    details.appendChild(block);
  }

  return details;
}

function renderSources(sources) {
  const details = document.createElement('details');
  details.className = 'sources';

  const summary = document.createElement('summary');
  summary.textContent = `参考来源 · ${sources.length}`;
  details.appendChild(summary);

  for (const source of sources) {
    const item = document.createElement('div');
    item.className = 'source-item';

    const head = document.createElement('div');
    head.className = 'source-head';

    const title = document.createElement('strong');
    title.textContent = source.title || source.filename || '医学知识库';

    const meta = document.createElement('span');
    meta.className = 'source-meta';
    const score = typeof source.score === 'number'
      ? `${Math.round(source.score * 100)}%`
      : '';
    meta.textContent = [source.source, source.filename, score]
      .filter(Boolean)
      .join(' · ');

    head.append(title, meta);

    const snippet = document.createElement('p');
    snippet.className = 'source-snippet';
    snippet.textContent = source.snippet || '';

    item.append(head, snippet);
    details.appendChild(item);
  }

  return details;
}

function renderAnswer(payload, elapsedSeconds, question = '') {
  const turn = document.createElement('div');  turn.className = 'turn';

  const note = document.createElement('div');
  note.className = 'note';
  note.dataset.mode = payload.historical ? 'single' : payload.mode;

  const head = document.createElement('div');
  head.className = 'note-head';

  const kind = document.createElement('span');
  kind.className = 'note-kind';
  kind.textContent = payload.historical
    ? '历史会诊意见'
    : payload.mode === 'swarm'
    ? '多学科会诊意见'
    : '单科诊疗意见';
  head.appendChild(kind);

  const time = document.createElement('span');
  time.className = 'note-time';
  if (payload.historical || elapsedSeconds == null) {
    time.hidden = true;
  } else {
    time.textContent = `${elapsedSeconds.toFixed(1)}s`;
  }
  head.appendChild(time);

  note.appendChild(head);

  const body = document.createElement('div');
  body.className = 'note-body';

  for (const section of parseSections(payload.answer || '')) {
    const wrap = document.createElement('div');
    wrap.className = 'section';

    if (section.title) {
      const h3 = document.createElement('h3');
      h3.className = 'section-title';
      h3.textContent = section.title;
      wrap.appendChild(h3);
    }
    if (section.body) appendBody(wrap, section.body);

    body.appendChild(wrap);
  }

  note.appendChild(body);

  const risk = detectRisk(payload.answer || '');
  if (risk) {
    const chip = document.createElement('div');
    chip.className = 'risk';
    chip.dataset.level = risk.level;
    chip.textContent = risk.label;
    note.appendChild(chip);
  }

  if (payload.suggestions && payload.suggestions.length) {
    const advice = document.createElement('div');
    advice.className = 'advice';

    const h = document.createElement('h3');
    h.textContent = `诊疗建议 · ${payload.suggestions.length} 条`;
    advice.appendChild(h);

    const ol = document.createElement('ol');
    for (const s of payload.suggestions) {
      const li = document.createElement('li');
      appendInline(li, s);
      ol.appendChild(li);
    }
    advice.appendChild(ol);
    note.appendChild(advice);
  }

  if (payload.agent_contributions && payload.agent_contributions.length) {
    note.appendChild(renderContributions(payload.agent_contributions));
  }

  if (payload.sources && payload.sources.length) {
    note.appendChild(renderSources(payload.sources));
  }

  if (payload.disclaimer) {
    const d = document.createElement('div');
    d.className = 'disclaimer';
    d.textContent = payload.disclaimer;
    note.appendChild(d);
  }

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  actions.appendChild(makeAction('复制意见', async (e) => {
    const btn = e.currentTarget;
    const ok = await copyText(payload.answer || '');
    btn.textContent = ok ? '已复制' : '复制失败';
    setTimeout(() => {
      btn.textContent = '复制意见';
    }, 1600);
  }));

  actions.appendChild(makeAction('复制 SOAP', async (e) => {
    const btn = e.currentTarget;
    const ok = await copyText(buildSOAP(payload, question));
    btn.textContent = ok ? 'SOAP 已复制' : '复制失败';
    setTimeout(() => {
      btn.textContent = '复制 SOAP';
    }, 1600);
  }));

  actions.appendChild(makeAction('导出 Markdown', () => {
    downloadMarkdown(payload, question);
  }));

  actions.appendChild(makeAction('导出 HTML 报告', () => {
    const stamp = (sessionId || String(Date.now())).slice(-12);
    const reportTitle = (question || 'MediX 会诊报告').trim().slice(0, 40);
    downloadFile(
      buildReportHtml(
        { id: sessionId, title: reportTitle, updated_at: new Date().toISOString() },
        [
          { role: 'user', content: question },
          { role: 'assistant', content: payload.answer || '', metadata: payload },
        ]
      ),
      `medix-report-${stamp}.html`,
      'text/html;charset=utf-8'
    );
  }));

  actions.appendChild(makeAction('设随访提醒', () => {
    openReminders();
    const title = (question || '复诊随访')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 40);
    els.reminderTitle.value = title;
    els.reminderNote.value = '来自会诊建议';
    els.reminderDue.value = defaultReminderDue();
    els.reminderTitle.focus();
  }));

  actions.appendChild(makeAction('打印 / PDF', () => {
    printAnswer();
  }));

  if (question) {
    actions.appendChild(makeAction('再次会诊', () => {
      if (busy) return;
      send(question);
    }));
  }

  note.appendChild(actions);

  const feedback = renderFeedback(payload);
  if (feedback) note.appendChild(feedback);

  turn.appendChild(note);
  els.thread.appendChild(turn);
  scrollThreadToBottom();
}

function renderWorking() {
  const wrap = document.createElement('div');
  wrap.className = 'working';
  wrap.id = 'working';
  wrap.innerHTML =
    '<span class="working-bars"><i></i><i></i><i></i></span>' +
    '<span class="working-text">正在分诊并调度科室…</span>' +
    '<span class="working-time" id="working-time">0s</span>';
  els.thread.appendChild(wrap);
  scrollThreadToBottom(true);
  return wrap;
}

/**
 * 汇总阶段的临时意见卡片：先以纯文本渐进显示 Token，
 * 收到 result 后再替换成带小节、风险提示和操作栏的正式意见书。
 */
function renderStreamingNote() {
  const turn = document.createElement('div');
  turn.className = 'turn';

  const note = document.createElement('div');
  note.className = 'note streaming';
  note.dataset.mode = 'single';

  const head = document.createElement('div');
  head.className = 'note-head';
  const kind = document.createElement('span');
  kind.className = 'note-kind';
  kind.textContent = '正在生成会诊意见';
  head.appendChild(kind);
  note.appendChild(head);

  const body = document.createElement('div');
  body.className = 'note-body streaming-body';
  note.appendChild(body);

  turn.appendChild(note);
  els.thread.appendChild(turn);
  scrollThreadToBottom(true);

  return { turn, body };
}

function makeFailureRetry(wrap, retry, label = '重试本次会诊') {
  if (!retry || !retry.question) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'failure-action';
  button.textContent = label;
  button.addEventListener('click', () => {
    if (busy) return;
    retry.askTurn?.remove();
    wrap.remove();
    send(retry.question);
  });
  wrap.appendChild(button);
}

function renderError(message, retry = null) {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  const strong = document.createElement('strong');
  strong.textContent = '会诊未能完成';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode(message));

  makeFailureRetry(wrap, retry);

  const action = document.createElement('button');
  action.type = 'button';
  action.className = 'failure-action';
  action.textContent = '打开系统自检';
  action.addEventListener('click', openDiagnose);
  wrap.appendChild(action);

  els.thread.appendChild(wrap);
  scrollThreadToBottom(true);
}

function renderStopped(retry = null) {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  const strong = document.createElement('strong');
  strong.textContent = '会诊已停止';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode('本次会诊在生成过程中被手动停止，未产生结果。'));
  makeFailureRetry(wrap, retry, '继续本次会诊');
  els.thread.appendChild(wrap);
  scrollThreadToBottom(true);
}

function renderAsk(text) {
  const turn = document.createElement('div');
  turn.className = 'turn';

  const label = document.createElement('p');
  label.className = 'ask-label';
  label.textContent = '主诉';
  turn.appendChild(label);

  const ask = document.createElement('div');
  ask.className = 'ask';
  ask.textContent = text;
  turn.appendChild(ask);

  els.thread.appendChild(turn);
  scrollThreadToBottom(true);
  return turn;
}

function renderTriage(container, triage) {
  if (!container || !triage || triage.level !== 'emergency') return;
  if (container.querySelector('.triage-alert')) return;

  const alert = document.createElement('div');
  alert.className = 'triage-alert';
  alert.setAttribute('role', 'alert');

  const title = document.createElement('strong');
  title.textContent = '急症警示';

  const message = document.createElement('p');
  const matched = (triage.matches || []).join('、');
  message.textContent =
    (matched ? `描述中出现：${matched}。` : '') +
    (triage.message || '请立即拨打 120 或前往急诊科。');

  alert.append(title, message);
  container.appendChild(alert);
  scrollThreadToBottom(true);
}

function renderMedicationSafety(container, safety) {
  if (!container || !safety || !safety.alerts || !safety.alerts.length) return;
  if (container.querySelector('.medication-alert')) return;

  const box = document.createElement('div');
  box.className = 'medication-alert';
  box.dataset.level = safety.level === 'warning' ? 'warning' : 'info';
  box.setAttribute('role', 'alert');

  const title = document.createElement('strong');
  title.textContent = safety.level === 'warning' ? '用药安全提示' : '用药提醒';
  box.appendChild(title);

  for (const alert of safety.alerts) {
    const item = document.createElement('div');
    item.className = 'medication-item';

    const itemTitle = document.createElement('b');
    itemTitle.textContent = alert.title;
    const detail = document.createElement('p');
    detail.textContent = alert.detail;

    item.append(itemTitle, detail);
    box.appendChild(item);
  }

  const note = document.createElement('p');
  note.className = 'medication-note';
  note.textContent = '以上为规则提示，不能替代医生或药师的判断。';
  box.appendChild(note);

  container.appendChild(box);
  scrollThreadToBottom(true);
}

/* ---------- 会话 ---------- */

function setSessionUrl(id) {
  if (!window.history || typeof window.history.replaceState !== 'function') return;
  const url = new URL(window.location.href);
  if (id) {
    url.searchParams.set('session', id);
  } else {
    url.searchParams.delete('session');
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

async function ensureSession() {
  if (sessionId) return sessionId;
  const res = await fetch('/api/sessions', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.session_id) {
    throw new Error(data.detail || `新建会话失败（${res.status}）`);
  }
  sessionId = data.session_id;
  localStorage.setItem('medix.session', sessionId);
  setSessionUrl(sessionId);
  els.readout.session.textContent = shortSession(sessionId);
  return sessionId;
}

async function startNewSession() {
  if (busy) {
    window.alert('会诊进行中，请先停止当前会诊。');
    return;
  }

  try {
    const res = await fetch('/api/sessions', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.session_id) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    sessionId = data.session_id;
    localStorage.setItem('medix.session', sessionId);
    setSessionUrl(sessionId);

    els.thread.replaceChildren(els.empty);
    els.empty.hidden = false;
    resetRail();
    closeHistory();
    closeKb();
    closeDiagnose();
    closeReminders();
    closeCompare();
    els.input.focus();
  } catch (err) {
    setVitals('warn', '新建会诊失败');
    window.alert(`新建会诊失败：${err.message || err}`);
  }
}

/* ---------- 历史会诊 ---------- */

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const day = `${sameYear ? '' : `${date.getFullYear()}-`}${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setHighlightedText(element, text, query) {
  element.replaceChildren();
  const source = String(text || '');
  const needle = String(query || '').trim();
  if (!needle) {
    element.textContent = source;
    return;
  }

  const lowerSource = source.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  let cursor = 0;
  let index = lowerSource.indexOf(lowerNeedle, cursor);

  while (index !== -1) {
    if (index > cursor) {
      element.appendChild(document.createTextNode(source.slice(cursor, index)));
    }
    const mark = document.createElement('mark');
    mark.textContent = source.slice(index, index + needle.length);
    element.appendChild(mark);
    cursor = index + needle.length;
    index = lowerSource.indexOf(lowerNeedle, cursor);
  }

  if (cursor < source.length) {
    element.appendChild(document.createTextNode(source.slice(cursor)));
  }
}

function renderHistoryItems(sessions, summary, append = false) {
  if (!append) {
    els.historyList.replaceChildren();
    historyItemMap.clear();
  }
  renderHistorySummary(summary);

  if (!sessions.length && !els.historyList.children.length) {
    els.historyEmpty.hidden = false;
    if (archivedOnly) {
      els.historyEmpty.textContent = '还没有归档的会诊';
    } else if (historyTag) {
      els.historyEmpty.textContent = `标签「${historyTag}」下没有会诊`;
    } else if (historyProfile) {
      els.historyEmpty.textContent = `档案「${historyProfile}」下没有会诊`;
    } else if (favoritesOnly) {
      els.historyEmpty.textContent = '还没有收藏的会诊';
    } else if (historyDays > 0) {
      els.historyEmpty.textContent = '所选时间范围内没有会诊记录';
    } else {
      els.historyEmpty.textContent = els.historySearch.value.trim()
        ? '没有匹配的会诊记录'
        : '还没有历史会诊，提交一次主诉后会自动保存。';
    }
    return;
  }
  els.historyEmpty.hidden = true;

  for (const item of sessions) {
    historyItemMap.set(item.id, item);
    const li = document.createElement('li');
    li.className = 'history-item';
    li.dataset.sessionId = item.id;
    if (item.id === sessionId) li.dataset.current = '1';
    if (bulkMode) li.classList.add('selecting');

    let checkbox = null;
    if (bulkMode) {
      checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'history-select';
      checkbox.checked = selectedSessions.has(item.id);
      checkbox.setAttribute(
        'aria-label',
        `选择：${item.title || '未命名会诊'}`
      );
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          selectedSessions.add(item.id);
        } else {
          selectedSessions.delete(item.id);
        }
        updateBulkBar();
      });
    }

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'history-open';

    const title = document.createElement('strong');
    setHighlightedText(title, item.title || '未命名会诊', historyQuery);

    const meta = document.createElement('span');
    meta.className = 'history-meta';
    const metaParts = [
      item.profile ? `档案：${item.profile}` : '',
      formatTime(item.updated_at),
      `${item.message_count || 0} 条`,
    ];
    const sessionTokens = Number(item.usage && item.usage.total_tokens || 0);
    if (sessionTokens > 0) {
      metaParts.push(`${sessionTokens.toLocaleString()} tokens`);
    }
    meta.textContent = metaParts.filter(Boolean).join(' · ');

    const preview = document.createElement('span');
    preview.className = 'history-preview';
    setHighlightedText(
      preview,
      item.match_snippet || item.preview || '（暂无答复摘要）',
      historyQuery
    );

    const tags = Array.isArray(item.tags) ? item.tags : [];
    if (tags.length) {
      const tagRow = document.createElement('span');
      tagRow.className = 'history-tags';
      for (const tag of tags) {
        const chip = document.createElement('span');
        chip.className = 'history-tag';
        chip.textContent = tag;
        tagRow.appendChild(chip);
      }
      open.append(title, meta, tagRow, preview);
    } else {
      open.append(title, meta, preview);
    }
    open.addEventListener('click', () => openSession(item.id));

    const actions = document.createElement('div');
    actions.className = 'history-actions';

    const favorite = document.createElement('button');
    favorite.type = 'button';
    favorite.className = `history-action history-star${item.favorite ? ' active' : ''}`;
    favorite.textContent = item.favorite ? '★' : '☆';
    favorite.title = item.favorite ? '取消收藏' : '收藏这次会诊';
    favorite.setAttribute(
      'aria-label',
      `${item.favorite ? '取消收藏' : '收藏'}：${item.title || '未命名会诊'}`
    );
    favorite.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleFavorite(item);
    });

    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'history-action';
    rename.textContent = '改名';
    rename.title = '重命名这次会诊';
    rename.setAttribute('aria-label', `重命名：${item.title || '未命名会诊'}`);
    rename.addEventListener('click', () => renameHistory(item));

    const exportBtn = document.createElement('button');
    exportBtn.type = 'button';
    exportBtn.className = 'history-action';
    exportBtn.textContent = '导出';
    exportBtn.title = '导出整段会诊记录为 Markdown';
    exportBtn.setAttribute('aria-label', `导出：${item.title || '未命名会诊'}`);
    exportBtn.addEventListener('click', () => exportHistory(item));

    const reportBtn = document.createElement('button');
    reportBtn.type = 'button';
    reportBtn.className = 'history-action';
    reportBtn.textContent = 'HTML';
    reportBtn.title = '导出为可打印的 HTML 报告';
    reportBtn.setAttribute(
      'aria-label',
      `导出 HTML 报告：${item.title || '未命名会诊'}`
    );
    reportBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      exportHistoryHtml(item);
    });

    const copyLink = document.createElement('button');
    copyLink.type = 'button';
    copyLink.className = 'history-action';
    copyLink.textContent = '链接';
    copyLink.title = '复制这次会诊的直达链接';
    copyLink.setAttribute(
      'aria-label',
      `复制链接：${item.title || '未命名会诊'}`
    );
    copyLink.addEventListener('click', async (event) => {
      event.stopPropagation();
      const url = new URL('/', window.location.origin);
      url.searchParams.set('session', item.id);
      const ok = await copyText(url.href);
      els.historyActionStatus.textContent = ok
        ? '已复制会诊链接'
        : '复制链接失败';
    });

    const tagsBtn = document.createElement('button');
    tagsBtn.type = 'button';
    tagsBtn.className = 'history-action';
    tagsBtn.textContent = '标签';
    tagsBtn.title = '编辑这次会诊的标签';
    tagsBtn.setAttribute(
      'aria-label',
      `编辑标签：${item.title || '未命名会诊'}`
    );
    tagsBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      editHistoryTags(item);
    });

    const archive = document.createElement('button');
    archive.type = 'button';
    archive.className = 'history-action';
    archive.textContent = item.archived ? '恢复' : '归档';
    archive.title = item.archived ? '移出归档' : '把这次会诊移入归档';
    archive.setAttribute(
      'aria-label',
      `${item.archived ? '移出归档' : '归档'}：${item.title || '未命名会诊'}`
    );
    archive.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleArchive(item);
    });

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'history-action danger';
    del.textContent = '删除';
    del.title = '删除这次会诊记录';
    del.setAttribute('aria-label', `删除：${item.title || '未命名会诊'}`);
    del.addEventListener('click', () => deleteHistory(item));

    actions.append(favorite, rename, exportBtn, reportBtn, copyLink, tagsBtn, archive, del);
    li.append(...(checkbox ? [checkbox] : []), open, actions);
    els.historyList.appendChild(li);
  }

  updateBulkBar();
}

function renderHistorySummary(summary) {
  if (!summary || !summary.sessions) {
    els.historySummary.hidden = true;
    els.historySummary.textContent = '';
    return;
  }

  const totalTokens = Number(summary.usage && summary.usage.total_tokens || 0);
  const llmCalls = Number(summary.llm_calls || 0);
  const scope = archivedOnly ? '已归档' : favoritesOnly ? '收藏' : '';
  let title;
  if (historyProfile) {
    title =
      `档案「${historyProfile}」`
      + (historyTag ? `·标签「${historyTag}」` : '')
      + `共 ${summary.sessions} 次${scope}会诊`;
  } else if (historyTag) {
    title = `标签「${historyTag}」共 ${summary.sessions} 次会诊`;
  } else {
    title = `共 ${summary.sessions} 次${scope}会诊`;
  }
  const parts = [title];
  if (summary.loaded && summary.loaded < summary.sessions) {
    parts.push(`已加载 ${summary.loaded} 次`);
  }
  if (summary.turns) parts.push(`${summary.turns} 轮`);
  if (totalTokens > 0) parts.push(`${totalTokens.toLocaleString()} tokens`);
  if (llmCalls > 0) parts.push(`${llmCalls} 次调用`);
  const summaryCost = estimateCost(summary.usage);
  if (summaryCost != null && totalTokens > 0) {
    parts.push(formatCost(summaryCost));
  }

  const feedback = summary.feedback;
  if (feedback && feedback.total > 0) {
    const rate = feedback.rate == null ? '—' : `${feedback.rate}%`;
    parts.push(`反馈 ${feedback.total} 条 · 好评率 ${rate}`);
  }

  els.historySummary.textContent = parts.join(' · ');
  els.historySummary.hidden = false;
}

function renderRecentFeedback(items) {
  if (!els.historyFeedback) return;
  els.historyFeedbackList.replaceChildren();

  if (!items.length) {
    els.historyFeedback.hidden = true;
    els.historyFeedbackCount.textContent = '';
    return;
  }

  els.historyFeedback.hidden = false;
  els.historyFeedbackCount.textContent = `（${items.length}）`;

  for (const item of items) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'history-feedback-item';
    btn.addEventListener('click', () => openSession(item.session_id));

    const title = document.createElement('strong');
    title.textContent = item.session_title || item.session_id;

    const comment = document.createElement('span');
    comment.className = 'history-feedback-comment';
    comment.textContent = item.comment || '未填写原因';

    const meta = document.createElement('span');
    meta.className = 'history-feedback-meta';
    meta.textContent = `待改进 · ${formatTime(item.updated_at)}`;

    btn.append(title, comment, meta);
    li.appendChild(btn);
    els.historyFeedbackList.appendChild(li);
  }
}

async function loadRecentFeedback() {
  if (!els.historyFeedback) return;
  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(
      `/api/feedback/recent?limit=8&rating=-1${scope}`
    );
    if (!res.ok) return;
    const data = await res.json();
    renderRecentFeedback(data.feedback || []);
  } catch {
    // 反馈区加载失败不影响历史列表
  }
}

async function exportFeedbackCsv() {
  const button = els.historyFeedbackExport;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '…';

  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(`/api/feedback/export.csv?rating=-1${scope}`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const buffer = await res.arrayBuffer();
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-feedback-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    button.textContent = '已导出';
  } catch (err) {
    button.textContent = '失败';
    window.alert(`导出反馈失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1600);
  }
}

function renderQualityInsights(data) {
  if (!data || !data.turns) {
    els.historyQuality.hidden = true;
    els.historyQualityBody.replaceChildren();
    return;
  }

  els.historyQuality.hidden = false;
  els.historyQualityBody.replaceChildren();

  const rows = [
    ['会诊 / 轮次', `${data.sessions} 次 / ${data.turns} 轮`],
    ['急症警示', `${data.emergency} 次`],
    [
      '用药提示',
      `${data.medication_sessions} 次会诊 · ${data.medication_alerts} 条`,
    ],
    [
      '反馈',
      data.feedback_total
        ? `${data.feedback_total} 条 · 好评率 ${data.rate}%`
        : '暂无',
    ],
    ['平均用时', data.avg_time == null ? '—' : `${data.avg_time} s`],
    ['平均 Token', `${Number(data.avg_tokens || 0).toLocaleString()} / 轮`],
  ];

  for (const [label, value] of rows) {
    const row = document.createElement('div');
    row.className = 'quality-row';
    const key = document.createElement('span');
    key.textContent = label;
    const val = document.createElement('span');
    val.textContent = value;
    row.append(key, val);
    els.historyQualityBody.appendChild(row);
  }

  if (data.top_risks && data.top_risks.length) {
    const block = document.createElement('div');
    block.className = 'quality-block';
    const title = document.createElement('h4');
    title.textContent = '常见急症标签';
    const chips = document.createElement('div');
    chips.className = 'quality-chips';
    for (const item of data.top_risks) {
      const chip = document.createElement('span');
      chip.className = 'quality-chip';
      chip.textContent = `${item.label} ×${item.count}`;
      chips.appendChild(chip);
    }
    block.append(title, chips);
    els.historyQualityBody.appendChild(block);
  }

  if (data.top_medication_alerts && data.top_medication_alerts.length) {
    const block = document.createElement('div');
    block.className = 'quality-block';
    const title = document.createElement('h4');
    title.textContent = '常见用药提示';
    const list = document.createElement('ul');
    list.className = 'quality-list';
    for (const item of data.top_medication_alerts) {
      const li = document.createElement('li');
      li.textContent = `${item.title} ×${item.count}`;
      list.appendChild(li);
    }
    block.append(title, list);
    els.historyQualityBody.appendChild(block);
  }
}

async function loadQualityInsights() {
  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(`/api/insights/quality?days=30${scope}`);
    if (!res.ok) return;
    renderQualityInsights(await res.json());
  } catch {
    // 质量概览加载失败不影响历史列表
  }
}

function updateHistoryMore(summary) {
  const total = Number(summary && summary.sessions || 0);
  const loaded = Number(summary && summary.loaded || 0);

  if (loaded < total) {
    els.historyMore.hidden = false;
    els.historyMore.disabled = historyLoading;
    els.historyMore.textContent = historyLoading
      ? '加载中…'
      : `加载更多（已加载 ${loaded} / 共 ${total}）`;
  } else {
    els.historyMore.hidden = true;
    els.historyMore.disabled = false;
  }
}

function updateBulkBar() {
  const count = selectedSessions.size;
  els.historyBulkCount.textContent = `已选 ${count} 项`;

  const visibleIds = Array.from(
    document.querySelectorAll('#history-list .history-item')
  ).map((item) => item.dataset.sessionId);
  const allSelected = visibleIds.length > 0
    && visibleIds.every((id) => selectedSessions.has(id));
  els.historyBulkAll.textContent = allSelected ? '取消全选' : '全选';

  [els.historyBulkArchive, els.historyBulkUnarchive,
   els.historyBulkTags, els.historyBulkDelete]
    .forEach((button) => {
      button.disabled = count === 0;
    });
  els.historyBulkCompare.disabled = count !== 2;
  els.historyBulkExport.disabled = count === 0;
}

function setBulkMode(enabled) {
  bulkMode = enabled;
  selectedSessions.clear();
  els.historyBulkBar.hidden = !bulkMode;
  els.historyBulkToggle.setAttribute(
    'aria-pressed', String(bulkMode)
  );
  els.historyBulkToggle.textContent = bulkMode ? '退出批量' : '批量';
  updateBulkBar();
  loadHistory(els.historySearch.value.trim());
}

function toggleBulkSelectAll() {
  const items = Array.from(
    document.querySelectorAll('#history-list .history-item')
  );
  const visibleIds = items.map((item) => item.dataset.sessionId);
  const allSelected = visibleIds.length > 0
    && visibleIds.every((id) => selectedSessions.has(id));

  if (allSelected) {
    selectedSessions.clear();
  } else {
    visibleIds.forEach((id) => selectedSessions.add(id));
  }

  items.forEach((item) => {
    const box = item.querySelector('.history-select');
    if (box) box.checked = selectedSessions.has(item.dataset.sessionId);
  });
  updateBulkBar();
}

async function bulkArchive(archived) {
  const ids = Array.from(selectedSessions);
  if (!ids.length) return;

  try {
    const res = await fetch('/api/bulk/sessions/archive', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, archived }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    const missing = (data.missing || []).length;
    selectedSessions.clear();
    await loadHistory(els.historySearch.value.trim());
    await loadHistoryTags();
    els.historyActionStatus.textContent =
      `${archived ? '已归档' : '已移出归档'} ${data.updated || 0} 次会诊`
      + (missing ? `（${missing} 个已不存在）` : '');
  } catch (err) {
    window.alert(`批量归档失败：${err.message || err}`);
  }
}

async function bulkTagSessions() {
  const ids = Array.from(selectedSessions);
  if (!ids.length) return;

  const input = window.prompt(
    `为选中的 ${ids.length} 次会诊设置标签（逗号/空格分隔，留空清除）`,
    ''
  );
  if (input === null) return;
  const tags = input.split(/[,，、\s]+/).filter(Boolean);

  try {
    const res = await fetch('/api/bulk/sessions/tags', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, tags }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    selectedSessions.clear();
    await loadHistory(els.historySearch.value.trim());
    await loadHistoryTags();
    els.historyActionStatus.textContent = data.tags.length
      ? `已为 ${data.updated || 0} 次会诊设置标签：${data.tags.join('、')}`
      : `已清空 ${data.updated || 0} 次会诊的标签`;
  } catch (err) {
    window.alert(`批量设置标签失败：${err.message || err}`);
  }
}

async function bulkDelete() {
  const ids = Array.from(selectedSessions);
  if (!ids.length) return;
  if (!window.confirm(`删除选中的 ${ids.length} 次会诊？删除后不可恢复。`)) {
    return;
  }

  try {
    const res = await fetch('/api/bulk/sessions/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    if (ids.includes(sessionId)) {
      sessionId = null;
      localStorage.removeItem('medix.session');
      setSessionUrl(null);
      els.thread.replaceChildren(els.empty);
      els.empty.hidden = false;
      resetRail();
      els.readout.session.textContent = '—';
    }

    const missing = (data.missing || []).length;
    selectedSessions.clear();
    await loadHistory(els.historySearch.value.trim());
    await loadRecentFeedback();
    await loadQualityInsights();
    await loadHistoryTags();
    await loadUsageTrend();
    await loadUsageBudget();
    els.historyActionStatus.textContent =
      `已删除 ${data.deleted || 0} 次会诊`
      + (missing ? `（${missing} 个已不存在）` : '');
  } catch (err) {
    window.alert(`批量删除失败：${err.message || err}`);
  }
}

/* ---------- 会诊对比 ---------- */

async function bulkExportMarkdown() {
  const ids = Array.from(selectedSessions);
  if (!ids.length) return;

  els.historyActionStatus.textContent = '正在导出选中会诊…';
  try {
    const documents = await Promise.all(ids.map(async (id) => {
      const item = historyItemMap.get(id) || { id, title: '未命名会诊' };
      const messages = await fetchSessionMessages({ id });
      return buildSessionMarkdown(item, messages);
    }));

    const header = [
      '# MediX 会诊记录导出',
      '',
      `导出时间：${new Date().toLocaleString()}`,
      `会话数：${ids.length}`,
      '',
    ].join('\n');
    const content = `${header}\n${documents.join('\n\n---\n\n')}\n`;
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(content, `medix-sessions-${stamp}.md`);
    els.historyActionStatus.textContent = `已导出 ${ids.length} 次会诊`;
  } catch (err) {
    els.historyActionStatus.textContent = '批量导出失败';
    window.alert(`批量导出失败：${err.message || err}`);
  }
}


function compareTurns(messages) {
  const turns = [];
  let question = '';
  for (const message of messages) {
    if (message.role === 'user') {
      question = message.content || '';
    } else if (message.role === 'assistant' && message.content) {
      turns.push({
        question,
        content: message.content,
        metadata: message.metadata || {},
      });
      question = '';
    }
  }
  return turns;
}

function compareStats(turns) {
  let emergency = 0;
  let medication = 0;
  let sources = 0;
  let contributions = 0;
  for (const turn of turns) {
    const meta = turn.metadata;
    if (meta.triage && meta.triage.level === 'emergency') emergency += 1;
    if (meta.medication_safety && Array.isArray(meta.medication_safety.alerts)) {
      medication += meta.medication_safety.alerts.length;
    }
    if (Array.isArray(meta.sources)) sources += meta.sources.length;
    if (Array.isArray(meta.agent_contributions)) {
      contributions += meta.agent_contributions.length;
    }
  }
  return [
    `急症预检命中 ${emergency} 次`,
    `用药安全提示 ${medication} 条`,
    `参考来源 ${sources} 条`,
    `各科室意见 ${contributions} 条`,
  ];
}

function renderCompareColumn(item, messages) {
  const turns = compareTurns(messages);
  const column = document.createElement('section');
  column.className = 'compare-column';

  const title = document.createElement('h3');
  title.textContent = item.title || '未命名会诊';
  column.appendChild(title);

  const totalTokens = Number(item.usage && item.usage.total_tokens || 0);
  const meta = document.createElement('p');
  meta.className = 'compare-meta';
  meta.textContent = [
    formatTime(item.updated_at || item.created_at),
    `${messages.length} 条消息`,
    totalTokens > 0 ? `${totalTokens.toLocaleString()} tokens` : '',
  ].filter(Boolean).join(' · ');
  column.appendChild(meta);

  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (tags.length) {
    const tagRow = document.createElement('div');
    tagRow.className = 'compare-tags';
    for (const tag of tags) {
      const chip = document.createElement('span');
      chip.className = 'history-tag';
      chip.textContent = tag;
      tagRow.appendChild(chip);
    }
    column.appendChild(tagRow);
  }

  const stats = document.createElement('ul');
  stats.className = 'compare-stats';
  for (const line of compareStats(turns)) {
    const li = document.createElement('li');
    li.textContent = line;
    stats.appendChild(li);
  }
  column.appendChild(stats);

  if (!turns.length) {
    const empty = document.createElement('p');
    empty.className = 'compare-meta';
    empty.textContent = '这次会诊还没有会诊意见。';
    column.appendChild(empty);
    return column;
  }

  turns.forEach((turn, index) => {
    const article = document.createElement('article');
    article.className = 'compare-turn';

    const head = document.createElement('h4');
    head.textContent = `第 ${index + 1} 轮`;
    article.appendChild(head);

    if (turn.question) {
      const question = document.createElement('p');
      question.className = 'compare-question';
      question.textContent = turn.question;
      article.appendChild(question);
    }

    const answer = document.createElement('div');
    answer.className = 'compare-answer';
    appendBody(answer, turn.content);
    article.appendChild(answer);

    column.appendChild(article);
  });

  return column;
}

function renderCompare(left, right) {
  els.compareBody.replaceChildren();
  els.compareBody.appendChild(renderCompareColumn(left.item, left.messages));
  els.compareBody.appendChild(renderCompareColumn(right.item, right.messages));
}

async function openCompare(ids) {
  if (ids.length !== 2) return;
  closeHistory();
  closeKb();
  closeCalc();
  closeDiagnose();
  closeMetrics();
  closeReminders();
  compareOpen = true;
  els.compareDrawer.hidden = false;
  els.compareBackdrop.hidden = false;
  syncPageInert();
  const status = document.createElement('p');
  status.className = 'compare-status';
  status.textContent = '正在加载两份会诊…';
  els.compareBody.replaceChildren(status);
  els.compareClose.focus();

  try {
    const results = await Promise.all(ids.map(async (id) => {
      const item = historyItemMap.get(id) || { id, title: '未命名会诊' };
      const messages = await fetchSessionMessages({ id });
      return { item, messages };
    }));
    if (!compareOpen) return;
    renderCompare(results[0], results[1]);
  } catch (err) {
    if (!compareOpen) return;
    status.textContent = `加载会诊失败：${err.message || err}`;
    status.classList.add('error');
  }
}

function closeCompare(restoreFocus = false) {
  compareOpen = false;
  els.compareDrawer.hidden = true;
  els.compareBackdrop.hidden = true;
  syncPageInert();
  if (restoreFocus) els.historyToggle.focus();
}

async function loadHistory(query = '', { append = false } = {}) {
  if (!append) {
    historyQuery = query;
    historyOffset = 0;
    historyTotal = 0;
  }

  const requestSeq = ++historyRequestSeq;
  historyLoading = true;
  updateHistoryMore({ sessions: historyTotal, loaded: historyOffset });

  try {
    const res = await fetch(
      `/api/sessions?limit=50&offset=${historyOffset}&q=${encodeURIComponent(historyQuery)}` +
      (favoritesOnly ? '&favorite=1' : '') +
      (archivedOnly ? '&archived=true' : '') +
      (historyTag ? `&tag=${encodeURIComponent(historyTag)}` : '') +
      (historyProfile ? `&profile=${encodeURIComponent(historyProfile)}` : '') +
      (historyDays > 0 ? `&days=${historyDays}` : '')
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== historyRequestSeq) return;

    const sessions = data.sessions || [];
    const loadedTotal = historyOffset + sessions.length;
    const viewSummary = { ...(data.summary || {}), loaded: loadedTotal };

    renderHistoryItems(sessions, viewSummary, append);
    historyTotal = Number(viewSummary.sessions || 0);
    historyOffset = loadedTotal;
    historyLoading = false;
    updateHistoryMore(viewSummary);
  } catch (err) {
    if (requestSeq !== historyRequestSeq) return;
    historyLoading = false;
    if (append) {
      els.historyMore.hidden = false;
      els.historyMore.disabled = false;
      els.historyMore.textContent = '加载更多失败，点击重试';
      return;
    }
    els.historyList.replaceChildren();
    els.historySummary.hidden = true;
    els.historyMore.hidden = true;
    els.historyEmpty.hidden = false;
    els.historyEmpty.textContent = `会诊记录加载失败：${err.message || err}`;
  }
}

function hideUsageTooltip() {
  els.usageTooltip.hidden = true;
}

function showUsageTooltip(day, slot) {
  const chartRect = els.usageChart.getBoundingClientRect();
  const slotRect = slot.getBoundingClientRect();
  const tokens = Number(day.usage && day.usage.total_tokens || 0);

  els.usageTooltip.textContent =
    `${day.date} · ${tokens.toLocaleString()} tokens · ` +
    `${Number(day.llm_calls || 0)} 次调用`;
  els.usageTooltip.hidden = false;

  const half = els.usageTooltip.offsetWidth / 2;
  let left = slotRect.left - chartRect.left + slotRect.width / 2;
  left = Math.max(half + 4, Math.min(left, chartRect.width - half - 4));
  els.usageTooltip.style.left = `${left}px`;
  els.usageTooltip.style.top = `${slotRect.top - chartRect.top + 2}px`;
}

function renderUsageTrend(data) {
  const days = Array.isArray(data.days) ? data.days : [];
  const totals = data.totals || {};
  const totalTokens = Number(totals.usage && totals.usage.total_tokens || 0);
  const totalCalls = Number(totals.llm_calls || 0);

  els.usageTrend.hidden = false;
  const trendCost = estimateCost(totals.usage);
  els.usageTrendTotal.textContent = totalTokens
    ? `${totalTokens.toLocaleString()} tokens · ${totalCalls} 次调用` +
      (trendCost != null ? ` · ${formatCost(trendCost)}` : '')
    : '暂无用量';

  els.usageBars.replaceChildren();
  els.usageAxis.replaceChildren();
  els.usageTableBody.replaceChildren();
  hideUsageTooltip();

  if (!days.length || !totalTokens) {
    els.usageChart.hidden = true;
    els.usageTable.hidden = true;
    return;
  }

  els.usageChart.hidden = false;
  els.usageTable.hidden = false;

  const max = Math.max(
    ...days.map((day) => Number(day.usage && day.usage.total_tokens || 0)),
    1
  );

  for (const day of days) {
    const tokens = Number(day.usage && day.usage.total_tokens || 0);
    const calls = Number(day.llm_calls || 0);

    const slot = document.createElement('button');
    slot.type = 'button';
    slot.className = 'usage-bar-slot';
    slot.setAttribute(
      'aria-label',
      `${day.date}：${tokens.toLocaleString()} tokens，${calls} 次调用`
    );
    slot.addEventListener('pointerenter', () => showUsageTooltip(day, slot));
    slot.addEventListener('pointerleave', hideUsageTooltip);
    slot.addEventListener('focus', () => showUsageTooltip(day, slot));
    slot.addEventListener('blur', hideUsageTooltip);

    const bar = document.createElement('span');
    bar.className = 'usage-bar';
    bar.style.height = tokens > 0
      ? `${Math.max(2, Math.round((tokens / max) * 100))}%`
      : '0';
    slot.appendChild(bar);

    const label = document.createElement('span');
    label.textContent = String(day.date || '').slice(5);

    els.usageBars.appendChild(slot);
    els.usageAxis.appendChild(label);

    const row = document.createElement('tr');
    for (const value of [day.date, tokens.toLocaleString(), String(calls)]) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.appendChild(cell);
    }
    els.usageTableBody.appendChild(row);
  }
}

async function loadUsageTrend() {
  const requestSeq = ++usageTrendRequestSeq;
  const scope = historyProfile
    ? `&profile=${encodeURIComponent(historyProfile)}`
    : '';
  try {
    const res = await fetch(
      `/api/usage/daily?days=7${favoritesOnly ? '&favorite=1' : ''}${scope}`
    );
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== usageTrendRequestSeq) return;
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    renderUsageTrend(data);
  } catch {
    if (requestSeq === usageTrendRequestSeq) {
      els.usageTrend.hidden = true;
    }
  }
}

function renderUsageBudget(data) {
  const budget = Number(data.budget || 0);
  if (!budget) {
    els.usageBudget.hidden = true;
    return;
  }

  const usage = data.usage || {};
  const total = Number(usage.total_tokens || 0);
  const percent = Number(
    data.percent != null ? data.percent : (total / budget) * 100
  );
  const width = Math.max(0, Math.min(100, percent));

  els.usageBudget.hidden = false;
  els.usageBudgetText.textContent =
    `${total.toLocaleString()} / ${budget.toLocaleString()} tokens` +
    `（${percent.toFixed(1)}%）`;
  els.usageBudgetBar.style.width = `${width}%`;

  els.usageBudget.classList.toggle(
    'warn', percent >= 80 && percent < 100
  );
  els.usageBudget.classList.toggle('critical', percent >= 100);

  if (percent >= 100) {
    els.usageBudgetHint.textContent = '已超出本月预算，请留意模型费用。';
  } else if (percent >= 80) {
    els.usageBudgetHint.textContent = '已使用 80% 以上，接近本月预算。';
  } else {
    els.usageBudgetHint.textContent =
      `剩余 ${Math.max(0, budget - total).toLocaleString()} tokens`;
  }
}

async function loadUsageBudget() {
  const requestSeq = ++usageBudgetRequestSeq;
  try {
    const res = await fetch('/api/usage/month');
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== usageBudgetRequestSeq) return;
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    renderUsageBudget(data);
  } catch {
    if (requestSeq === usageBudgetRequestSeq) {
      els.usageBudget.hidden = true;
    }
  }
}

async function exportUsageCsv() {
  const button = els.usageExport;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '…';

  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(
      `/api/usage/export.csv?days=7${favoritesOnly ? '&favorite=1' : ''}${scope}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const buffer = await res.arrayBuffer();
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-usage-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    button.textContent = '已导出';
  } catch (err) {
    button.textContent = '失败';
    window.alert(`导出用量失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1600);
  }
}

async function exportHistoryBackup() {
  const btn = els.historyExport;
  if (btn.disabled) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '导出中…';

  try {
    const res = await fetch('/api/sessions/export');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    const stamp = String(data.exported_at || new Date().toISOString())
      .replace(/[:T]/g, '-')
      .slice(0, 19);
    downloadFile(
      JSON.stringify(data, null, 2),
      `medix-sessions-${stamp}.json`,
      'application/json;charset=utf-8'
    );
    btn.textContent = `已导出 ${data.session_count || 0} 次`;
  } catch (err) {
    btn.textContent = '导出失败';
    window.alert(`导出备份失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1800);
  }
}

async function exportHistoryCsv() {
  const btn = els.historyExportCsv;
  if (btn.disabled) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '导出中…';

  try {
    const params = new URLSearchParams();
    const query = els.historySearch.value.trim();
    if (query) params.set('q', query);
    if (favoritesOnly) params.set('favorite', '1');
    if (archivedOnly) params.set('archived', 'true');
    if (historyTag) params.set('tag', historyTag);
    if (historyProfile) params.set('profile', historyProfile);
    if (historyDays > 0) params.set('days', String(historyDays));

    const res = await fetch(
      `/api/sessions/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-sessions-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    btn.textContent = '已导出';
  } catch (err) {
    btn.textContent = '失败';
    window.alert(`导出列表失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1600);
  }
}

async function importHistoryBackup() {
  const file = els.historyImportFile.files && els.historyImportFile.files[0];
  if (!file) return;

  try {
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      window.alert('备份文件不是有效的 JSON。');
      return;
    }

    const count = Array.isArray(data.sessions) ? data.sessions.length : 0;
    const profileCount = Array.isArray(data.profiles) ? data.profiles.length : 0;
    const metricCount = Array.isArray(data.health_metrics)
      ? data.health_metrics.length
      : 0;
    if (!count && !profileCount && !metricCount) {
      window.alert('备份文件中没有会话、患者档案或健康记录。');
      return;
    }
    const summary = [
      count ? `${count} 个会话` : '',
      profileCount ? `${profileCount} 个患者档案` : '',
      metricCount ? `${metricCount} 条健康记录` : '',
    ].filter(Boolean).join('、');
    if (!window.confirm(`导入 ${summary}？已存在的记录会跳过。`)) {
      return;
    }

    const btn = els.historyImport;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '导入中…';

    try {
      const res = await fetch('/api/sessions/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: data.version,
          sessions: data.sessions,
          profiles: data.profiles,
          health_metrics: data.health_metrics,
        }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(result.detail || `服务返回 ${res.status}`);

      btn.textContent = `已导入 ${result.imported || 0} 次`;
      await loadHistory(els.historySearch.value.trim());
      await loadUsageTrend();
      await loadUsageBudget();
      if (result.profiles) await initProfiles();
      await loadQualityInsights();
    } catch (err) {
      btn.textContent = '导入失败';
      window.alert(`导入备份失败：${err.message || err}`);
    } finally {
      setTimeout(() => {
        btn.textContent = original;
        btn.disabled = false;
      }, 1800);
    }
  } finally {
    els.historyImportFile.value = '';
  }
}

let lastFocused = null;

function openHistory() {
  closeKb();
  closeDiagnose();
  closeCalc();
  closeMetrics();
  closeReminders();
  closeCompare();
  lastFocused = document.activeElement;
  historyOpen = true;
  els.historyDrawer.hidden = false;
  els.historyBackdrop.hidden = false;
  syncPageInert();
  els.historyToggle.setAttribute('aria-expanded', 'true');
  els.historySearch.value = '';
  els.historyList.replaceChildren();
  els.historySummary.hidden = true;
  els.historySummary.textContent = '';
  historyQuery = '';
  historyOffset = 0;
  historyTotal = 0;
  historyLoading = false;
  els.historyMore.hidden = true;
  els.historyMore.disabled = false;
  els.historyEmpty.hidden = false;
  els.historyEmpty.textContent = '正在加载会诊记录…';
  loadHistory();
  loadRecentFeedback();
  loadQualityInsights();
  loadHistoryTags();
  loadUsageTrend();
  loadUsageBudget();
  refreshHistoryProfileOptions();
  els.historySearch.focus();
}

function closeHistory(restoreFocus = false) {
  historyOpen = false;
  els.historyDrawer.hidden = true;
  els.historyBackdrop.hidden = true;
  hideUsageTooltip();
  syncPageInert();
  els.historyToggle.setAttribute('aria-expanded', 'false');
  if (restoreFocus && lastFocused instanceof HTMLElement) {
    lastFocused.focus();
  }
}

function renderStoredMessages(messages, activeSessionId = sessionId, profile = '') {
  els.thread.replaceChildren(els.empty);
  els.empty.hidden = messages.length > 0;

  let question = '';
  let askTurn = null;
  let lastMetadata = null;
  let lastUsage = null;
  let lastLlmCalls = 0;
  for (const message of messages) {
    if (message.role === 'user') {
      question = message.content || '';
      askTurn = renderAsk(question);
    } else if (message.role === 'assistant' && message.content) {
      const metadata = message.metadata || {};
      lastMetadata = metadata;
      if (metadata.usage) {
        lastUsage = metadata.usage;
        lastLlmCalls = metadata.llm_calls || 0;
      }
      if (metadata.triage) {
        renderTriage(askTurn, metadata.triage);
      }
      if (metadata.medication_safety) {
        renderMedicationSafety(askTurn, metadata.medication_safety);
      }
      renderAnswer(
        {
          answer: message.content,
          mode: metadata.swarm_enabled ? 'swarm' : 'single',
          suggestions: metadata.suggestions || [],
          disclaimer: metadata.disclaimer || '',
          sources: metadata.sources || [],
          agent_contributions: metadata.agent_contributions || [],
          patient_context: metadata.patient_context || {},
          triage: metadata.triage || {},
          assistant_message_id: message.id,
          feedback: message.feedback || metadata.feedback || null,
          session_id: activeSessionId,
          historical: true,
        },
        null,
        question
      );
      question = '';
      askTurn = null;
    }
  }

  if (lastMetadata) {
    if (typeof lastMetadata.swarm_enabled === 'boolean') {
      els.readout.mode.textContent = lastMetadata.swarm_enabled ? '多学科会诊' : '单科接诊';
    }
    const involved = (lastMetadata.agents_involved || []).map(
      (agentId) => AGENT_NAMES[agentId] || agentId
    );
    if (involved.length) {
      els.readout.agents.textContent = involved.join('、');
    }
    if (lastMetadata.subtasks_completed != null) {
      const completed = Number(lastMetadata.subtasks_completed || 0);
      const failed = Number(lastMetadata.subtasks_failed || 0);
      els.readout.tasks.textContent = failed
        ? `${completed}（${failed} 失败）`
        : String(completed);
    }
    if (lastMetadata.total_time != null) {
      els.readout.time.textContent = `${Number(lastMetadata.total_time).toFixed(1)}s`;
    }
  }

  if (lastUsage) {
    els.readout.usage.textContent = formatUsage(lastUsage, lastLlmCalls);
    els.readout.usage.title = usageDetail(lastUsage, lastLlmCalls);
    latestUsage = lastUsage;
    updateCostDisplay();
  }

  applyPatientContext(
    (lastMetadata && lastMetadata.patient_context) || {}
  );
  setActiveProfile(profile || '');

  scrollThreadToBottom(true);
}

async function openSession(id) {
  if (busy) return;

  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages?limit=200`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();

    sessionId = id;
    localStorage.setItem('medix.session', id);
    setSessionUrl(id);
    closeHistory();
    resetRail();
    els.readout.session.textContent = shortSession(id);
    renderStoredMessages(data.messages || [], id, data.profile || '');
    setVitals('idle', '历史会诊');
    els.input.focus();
  } catch (err) {
    closeHistory();
    renderError(`读取会诊记录失败：${err.message || err}`);
    els.input.focus();
  }
}

async function toggleFavorite(item) {
  const favorite = !item.favorite;

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(item.id)}/favorite`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ favorite }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    await loadHistory(els.historySearch.value.trim());
    await loadUsageTrend();
    els.historyActionStatus.textContent = favorite
      ? '已收藏会诊'
      : '已取消收藏';
    if (historyOpen) els.historySearch.focus();
  } catch (err) {
    window.alert(`更新收藏失败：${err.message || err}`);
  }
}

async function loadHistoryTags() {
  try {
    const scope = historyProfile
      ? `?profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(`/api/tags${scope}`);
    if (!res.ok) return;
    const data = await res.json();
    const items = data.tags || [];
    const current = historyTag;

    els.historyTag.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '全部标签';
    els.historyTag.appendChild(placeholder);

    for (const item of items) {
      const option = document.createElement('option');
      option.value = item.name;
      option.textContent = `${item.name}（${item.count}）`;
      els.historyTag.appendChild(option);
    }

    if (current && items.some((item) => item.name === current)) {
      els.historyTag.value = current;
    } else if (current) {
      historyTag = '';
      els.historyTag.value = '';
    }
  } catch {
    // 标签加载失败不影响历史列表
  }
}

async function editHistoryTags(item) {
  const current = (item.tags || []).join('、');
  const input = window.prompt(
    '标签（用逗号或空格分隔，最多 5 个，每个最长 20 字）',
    current
  );
  if (input === null) return;

  const tags = input.split(/[,，、\s]+/).filter(Boolean);

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(item.id)}/tags`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    await loadHistoryTags();
    await loadHistory(els.historySearch.value.trim());
    els.historyActionStatus.textContent = data.tags.length
      ? `已更新标签：${data.tags.join('、')}`
      : '已清空标签';
    if (historyOpen) els.historySearch.focus();
  } catch (err) {
    window.alert(`更新标签失败：${err.message || err}`);
  }
}

async function toggleArchive(item) {
  const archived = !item.archived;

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(item.id)}/archive`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archived }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    await loadHistory(els.historySearch.value.trim());
    await loadHistoryTags();
    els.historyActionStatus.textContent = archived
      ? '已归档会诊'
      : '已移出归档';
    if (historyOpen) els.historySearch.focus();
  } catch (err) {
    window.alert(`更新归档失败：${err.message || err}`);
  }
}

async function deleteHistory(item) {
  const title = item.title || '这次会诊';
  if (busy && item.id === sessionId) {
    window.alert('会诊进行中，请先停止当前会诊。');
    return;
  }
  if (!window.confirm(`删除「${title}」的会诊记录？删除后不可恢复。`)) return;

  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(item.id)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`删除失败（${res.status}）`);

    if (item.id === sessionId) {
      sessionId = null;
      localStorage.removeItem('medix.session');
      setSessionUrl(null);
      els.thread.replaceChildren(els.empty);
      els.empty.hidden = false;
      resetRail();
      els.readout.session.textContent = '—';
    }
    await loadHistory(els.historySearch.value.trim());
    await loadRecentFeedback();
    await loadQualityInsights();
    await loadHistoryTags();
    await loadUsageTrend();
    await loadUsageBudget();
    els.historyActionStatus.textContent = '已删除会诊';
    if (historyOpen) els.historySearch.focus();
  } catch (err) {
    window.alert(err.message || String(err));
  }
}

async function renameHistory(item) {
  const current = item.title || '未命名会诊';
  const next = window.prompt('重命名这次会诊', current);
  if (next === null) return;

  const title = next.trim();
  if (!title || title === current) return;

  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(item.id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error(`重命名失败（${res.status}）`);
    await loadHistory(els.historySearch.value.trim());
    els.historyActionStatus.textContent = '已重命名会诊';
    if (historyOpen) els.historySearch.focus();
  } catch (err) {
    window.alert(err.message || String(err));
  }
}

async function fetchSessionMessages(item) {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(item.id)}/messages?limit=0`
  );
  if (!res.ok) throw new Error(`读取会诊记录失败（${res.status}）`);
  const data = await res.json();
  return data.messages || [];
}

async function exportHistory(item) {
  try {
    const messages = await fetchSessionMessages(item);
    if (!messages.length) {
      window.alert('这次会诊还没有可导出的内容。');
      return;
    }
    downloadFile(
      buildSessionMarkdown(item, messages),
      `medix-session-${item.id}.md`
    );
  } catch (err) {
    window.alert(err.message || String(err));
  }
}

async function exportHistoryHtml(item) {
  try {
    const messages = await fetchSessionMessages(item);
    if (!messages.length) {
      window.alert('这次会诊还没有可导出的内容。');
      return;
    }
    downloadFile(
      buildReportHtml(item, messages),
      `medix-report-${item.id}.html`,
      'text/html;charset=utf-8'
    );
    els.historyActionStatus.textContent = '已导出 HTML 报告';
  } catch (err) {
    window.alert(err.message || String(err));
  }
}

/* ---------- 知识库管理 ---------- */

let kbFocused = null;
let previewFilename = '';
let previewEditable = false;
let previewEditing = false;

function setKbStatus(text, isError = false) {
  els.kbStatus.hidden = !text;
  els.kbStatus.textContent = text || '';
  els.kbStatus.classList.toggle('error', Boolean(isError));
}

function setKbBusy(busy) {
  [els.kbFile, els.kbUpload, els.kbRebuild].forEach((el) => {
    if (el) el.disabled = busy;
  });
}

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null) return '—';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function renderKbDocuments(documents) {
  els.kbList.replaceChildren();
  if (!documents.length) {
    const empty = document.createElement('li');
    empty.className = 'kb-empty';
    empty.textContent = '还没有可导入的文档。';
    els.kbList.appendChild(empty);
    return;
  }

  for (const doc of documents) {
    const item = document.createElement('li');
    item.className = 'kb-item';

    const body = document.createElement('div');
    body.className = 'kb-item-body';

    const name = document.createElement('span');
    name.className = 'kb-item-name';
    name.textContent = doc.filename;

    const meta = document.createElement('span');
    meta.className = 'kb-item-meta';
    meta.textContent = `${doc.builtin ? '内置' : '上传'} · ${formatBytes(doc.size)}`;

    body.append(name, meta);

    const preview = document.createElement('button');
    preview.type = 'button';
    preview.className = 'kb-item-preview';
    preview.textContent = '预览';
    preview.addEventListener('click', () => previewKbDocument(doc.filename));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'kb-item-delete';
    del.textContent = '删除';
    if (doc.builtin) {
      del.disabled = true;
      del.title = '内置文档请通过仓库更新';
    } else {
      del.addEventListener('click', () => deleteKbDocument(doc.filename));
    }

    item.append(body, preview, del);
    els.kbList.appendChild(item);
  }
}

function exitKbPreviewEdit() {
  previewEditing = false;
  els.kbPreviewEditor.hidden = true;
  els.kbPreviewBody.hidden = false;
  els.kbPreviewSave.hidden = true;
  els.kbPreviewCancel.hidden = true;
  els.kbPreviewEdit.hidden = !previewEditable;
  els.kbPreviewEdit.disabled = false;
  els.kbPreviewEdit.textContent = '编辑';
  els.kbPreviewSave.disabled = false;
  els.kbPreviewSave.textContent = '保存';
}

function closeKbPreview() {
  exitKbPreviewEdit();
  previewFilename = '';
  previewEditable = false;
  els.kbPreview.hidden = true;
  els.kbPreviewTitle.textContent = '文档预览';
  els.kbPreviewMeta.textContent = '';
  els.kbPreviewBody.textContent = '';
  els.kbPreviewEditor.value = '';
  els.kbPreviewEdit.hidden = true;
}

async function previewKbDocument(filename) {
  exitKbPreviewEdit();
  previewFilename = filename;
  previewEditable = false;
  els.kbPreviewEdit.hidden = true;
  els.kbPreview.hidden = false;
  els.kbPreviewTitle.textContent = filename;
  els.kbPreviewMeta.textContent = '正在读取…';
  els.kbPreviewBody.textContent = '';
  els.kbPreview.scrollIntoView({ block: 'nearest' });
  els.kbPreviewClose.focus();

  try {
    const res = await fetch(
      `/api/knowledge/documents/${encodeURIComponent(filename)}/preview`
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    const meta = [
      data.builtin ? '内置' : '上传',
      data.type,
      data.source,
      `${Number(data.total_length || 0).toLocaleString()} 字`,
      data.truncated ? `仅显示前 ${Number(data.content.length).toLocaleString()} 字` : '',
    ].filter(Boolean);
    els.kbPreviewTitle.textContent = data.filename || filename;
    els.kbPreviewMeta.textContent = meta.join(' · ');
    els.kbPreviewBody.textContent = data.content || '（文档内容为空）';
    previewFilename = data.filename || filename;
    previewEditable = !data.builtin
      && ['.txt', '.md', '.markdown'].includes(
        String(data.suffix || '').toLowerCase()
      );
    els.kbPreviewEdit.hidden = !previewEditable;
  } catch (err) {
    els.kbPreviewMeta.textContent = `读取失败：${err.message || err}`;
  }
}

async function startKbPreviewEdit() {
  if (!previewEditable || previewEditing || !previewFilename) return;

  const button = els.kbPreviewEdit;
  button.disabled = true;
  button.textContent = '读取中…';

  try {
    const res = await fetch(
      `/api/knowledge/documents/${encodeURIComponent(previewFilename)}/preview?full=1`
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    els.kbPreviewEditor.value = data.content || '';
    previewEditing = true;
    els.kbPreviewBody.hidden = true;
    els.kbPreviewEditor.hidden = false;
    els.kbPreviewEdit.hidden = true;
    els.kbPreviewSave.hidden = false;
    els.kbPreviewCancel.hidden = false;
    els.kbPreviewEditor.focus();
  } catch (err) {
    els.kbPreviewMeta.textContent = `读取全文失败：${err.message || err}`;
  } finally {
    button.disabled = false;
    button.textContent = '编辑';
  }
}

async function saveKbPreviewEdit() {
  if (!previewEditing || !previewFilename) return;

  const filename = previewFilename;
  const content = els.kbPreviewEditor.value;
  if (!content.trim()) {
    setKbStatus('文档内容不能为空。', true);
    return;
  }
  if (new Blob([content]).size > 5 * 1024 * 1024) {
    setKbStatus('文件超过 5 MB 限制。', true);
    return;
  }

  const button = els.kbPreviewSave;
  button.disabled = true;
  button.textContent = '保存中…';
  setKbStatus('正在保存并重建索引…');

  try {
    const res = await fetch(
      `/api/knowledge/documents/${encodeURIComponent(filename)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `保存失败（${res.status}）`);
    if (!kbOpen || previewFilename !== filename) return;

    const rebuild = data.rebuild || {};
    setKbStatus(
      `已保存 ${data.document?.filename || filename}。` +
      `重建完成：${rebuild.documents || 0} 个文档、${rebuild.chunks || 0} 个向量块。`
    );
    exitKbPreviewEdit();
    await loadKnowledge();
    await previewKbDocument(filename);
  } catch (err) {
    setKbStatus(`保存失败：${err.message || err}`, true);
  } finally {
    button.disabled = false;
    button.textContent = '保存';
  }
}

function renderKbSearchResults(data) {
  const results = data.results || [];
  els.kbSearchResults.replaceChildren();
  els.kbSearchResults.hidden = false;

  if (!results.length) {
    const empty = document.createElement('p');
    empty.className = 'kb-search-empty';
    empty.textContent = '没有匹配的文档。';
    els.kbSearchResults.appendChild(empty);
    return;
  }

  for (const item of results) {
    const row = document.createElement('div');
    row.className = 'kb-search-item';

    const head = document.createElement('div');
    head.className = 'kb-search-head';
    const title = document.createElement('strong');
    title.textContent = item.title || item.filename || '医学知识库';
    const score = document.createElement('span');
    score.className = 'kb-search-score';
    score.textContent = `${Math.round((Number(item.score) || 0) * 100)}%`;
    head.append(title, score);

    const meta = document.createElement('p');
    meta.className = 'kb-search-meta';
    meta.textContent = [item.source, item.filename, item.type]
      .filter(Boolean)
      .join(' · ');

    const snippet = document.createElement('p');
    snippet.className = 'kb-search-snippet';
    snippet.textContent = item.snippet || '（无片段）';

    row.append(head, meta, snippet);
    if (item.filename) {
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.title = `预览 ${item.filename}`;
      const open = () => previewKbDocument(item.filename);
      row.addEventListener('click', open);
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          open();
        }
      });
    }
    els.kbSearchResults.appendChild(row);
  }
}

async function searchKnowledgeBase() {
  if (kbSearching) return;

  const query = els.kbSearchInput.value.trim();
  if (!query) {
    els.kbSearchResults.hidden = true;
    setKbStatus('请输入检索关键词。', true);
    return;
  }

  kbSearching = true;
  const button = els.kbSearchButton;
  button.disabled = true;
  button.textContent = '检索中…';
  els.kbSearchResults.hidden = false;
  els.kbSearchResults.replaceChildren();
  const loading = document.createElement('p');
  loading.className = 'kb-search-empty';
  loading.textContent = '正在检索知识库…';
  els.kbSearchResults.appendChild(loading);

  try {
    const filterType = els.kbSearchType.value;
    const res = await fetch(
      `/api/knowledge/search?q=${encodeURIComponent(query)}&top_k=5`
      + (filterType ? `&filter_type=${encodeURIComponent(filterType)}` : '')
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `检索失败（${res.status}）`);
    renderKbSearchResults(data);
  } catch (err) {
    els.kbSearchResults.replaceChildren();
    const error = document.createElement('p');
    error.className = 'kb-search-empty';
    error.textContent = `检索失败：${err.message || err}`;
    els.kbSearchResults.appendChild(error);
  } finally {
    kbSearching = false;
    button.textContent = '检索';
    button.disabled = false;
  }
}

async function loadKnowledge() {
  els.kbStats.textContent = '正在加载…';
  try {
    const res = await fetch('/api/knowledge');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    const chunks = data.chunk_count == null ? '未知' : data.chunk_count;
    els.kbStats.textContent = `文档 ${data.document_count || 0} 个 · 向量块 ${chunks} 个`;
    renderKbDocuments(data.documents || []);
  } catch (err) {
    els.kbStats.textContent = '知识库加载失败';
    setKbStatus(err.message || String(err), true);
  }
}

function openKb() {
  closeHistory();
  closeDiagnose();
  closeCalc();
  closeMetrics();
  closeReminders();
  closeCompare();
  kbFocused = document.activeElement;
  kbOpen = true;
  els.kbDrawer.hidden = false;
  els.kbBackdrop.hidden = false;
  syncPageInert();
  els.kbManage.setAttribute('aria-expanded', 'true');
  setKbStatus('');
  closeKbPreview();
  loadKnowledge();
  els.kbClose.focus();
}

function closeKb(restoreFocus = false) {
  kbOpen = false;
  els.kbDrawer.hidden = true;
  els.kbBackdrop.hidden = true;
  syncPageInert();
  els.kbManage.setAttribute('aria-expanded', 'false');
  if (restoreFocus && kbFocused instanceof HTMLElement) {
    kbFocused.focus();
  }
}

async function uploadKbDocument(fileOverride = null) {
  const files = Array.isArray(fileOverride)
    ? fileOverride
    : fileOverride
    ? [fileOverride]
    : Array.from(els.kbFile.files || []);

  if (!files.length) {
    setKbStatus('请先选择要上传的文件。', true);
    return;
  }

  for (const file of files) {
    if (!/\.(txt|md|markdown|pdf)$/i.test(file.name)) {
      setKbStatus(`「${file.name}」不是支持的格式，仅支持 TXT / Markdown / PDF。`, true);
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setKbStatus(`「${file.name}」超过 5 MB 限制。`, true);
      return;
    }
  }

  const form = new FormData();
  for (const file of files) form.append('files', file);

  setKbBusy(true);
  setKbStatus(
    files.length > 1
      ? `正在上传 ${files.length} 个文件并重建索引，首次运行可能需要加载模型…`
      : '正在上传并重建索引，首次运行可能需要加载模型…'
  );

  try {
    const res = await fetch('/api/knowledge/upload-batch', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `上传失败（${res.status}）`);

    const saved = data.documents || [];
    const failed = data.failed || [];
    const rebuild = data.rebuild || {};
    const rebuildText =
      `重建完成：${rebuild.documents || 0} 个文档、${rebuild.chunks || 0} 个向量块。`;

    if (failed.length) {
      setKbStatus(
        `已上传 ${saved.length} 个文件，${failed.length} 个失败：`
        + `${failed.map((item) => item.filename).join('、')}。${rebuildText}`,
        true
      );
    } else {
      setKbStatus(`已上传 ${saved.length} 个文件。${rebuildText}`);
    }

    els.kbFile.value = '';
    await loadKnowledge();
    checkStatus();
  } catch (err) {
    setKbStatus(err.message || String(err), true);
  } finally {
    setKbBusy(false);
  }
}

async function rebuildKb() {
  setKbBusy(true);
  setKbStatus('正在重建索引，请稍候…');

  try {
    const res = await fetch('/api/knowledge/rebuild', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `重建失败（${res.status}）`);

    setKbStatus(`重建完成：${data.documents} 个文档、${data.chunks} 个向量块。`);
    await loadKnowledge();
    checkStatus();
  } catch (err) {
    setKbStatus(err.message || String(err), true);
  } finally {
    setKbBusy(false);
  }
}

async function deleteKbDocument(filename) {
  if (!window.confirm(`删除「${filename}」并重建索引？`)) return;

  setKbBusy(true);
  setKbStatus(`正在删除 ${filename} 并重建索引…`);

  try {
    const res = await fetch(
      `/api/knowledge/documents/${encodeURIComponent(filename)}?rebuild=true`,
      { method: 'DELETE' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `删除失败（${res.status}）`);

    setKbStatus(`已删除 ${filename}。`);
    await loadKnowledge();
    checkStatus();
  } catch (err) {
    setKbStatus(err.message || String(err), true);
  } finally {
    setKbBusy(false);
  }
}

/* ---------- 系统自检 ---------- */

let diagFocused = null;
let lastDiagnostics = null;

function setDiagStatus(text, isError = false) {
  els.diagStatus.hidden = !text;
  els.diagStatus.textContent = text || '';
  els.diagStatus.classList.toggle('error', Boolean(isError));
}

function diagRow(label, value, ok = null) {
  const row = document.createElement('div');
  row.className = 'diag-row' + (ok === false ? ' bad' : '');

  const key = document.createElement('span');
  key.className = 'k';
  key.textContent = label;

  const val = document.createElement('span');
  val.className = 'v';
  val.textContent = String(value ?? '—');

  row.append(key, val);
  return row;
}

function renderDiagSection(container, title, rows) {
  container.replaceChildren();
  const heading = document.createElement('h3');
  heading.textContent = title;
  container.appendChild(heading);
  rows.forEach((row) => container.appendChild(row));
}

function renderDiagnostics(report) {
  lastDiagnostics = report;

  const missing = report.missing_required || [];
  const healthy = report.python_ok
    && missing.length === 0
    && report.llm_config
    && report.llm_config.api_key_set;

  els.diagSummary.className = `diag-summary ${healthy ? 'ok' : 'warn'}`;
  els.diagSummary.textContent = healthy
    ? '环境正常，可以运行'
    : '发现问题，请检查下面标红的项目';

  renderDiagSection(els.diagEnv, '运行环境', [
    diagRow('操作系统', report.os || '—'),
    diagRow('Python', report.python || '—', report.python_ok),
    diagRow('项目目录', report.project_root || '—'),
  ]);

  const runtime = report.runtime || {};
  renderDiagSection(els.diagRuntime, '运行状态', [
    diagRow(
      '会话数据库',
      runtime.db_exists
        ? `${runtime.sessions ?? 0} 次会诊 / ${runtime.messages ?? 0} 条消息`
        : '未生成'
    ),
    diagRow('数据库大小', formatBytes(runtime.db_size || 0)),
    diagRow('访问令牌', runtime.auth_enabled ? '已开启' : '未开启'),
    diagRow(
      '会诊限流',
      Number(runtime.rate_limit || 0) > 0
        ? `${runtime.rate_limit} 次/分钟`
        : '已关闭'
    ),
    diagRow('费用估算', runtime.pricing_configured ? '已配置' : '未配置'),
    diagRow(
      '月度预算',
      Number(runtime.monthly_token_budget || 0) > 0
        ? `${Number(runtime.monthly_token_budget).toLocaleString()} tokens`
        : '未配置'
    ),
    diagRow(
      '自动归档',
      Number(runtime.auto_archive_days || 0) > 0
        ? `${Number(runtime.auto_archive_days)} 天未更新`
        : '未启用'
    ),
  ]);
  if (runtime.db_error) {
    els.diagRuntime.appendChild(
      diagRow('数据库读取', runtime.db_error)
    );
  }

  const llm = report.llm_config || {};
  renderDiagSection(els.diagLlm, 'LLM 配置', [
    diagRow('API Key', llm.api_key_set ? '已配置' : '未配置', Boolean(llm.api_key_set)),
    diagRow('模型', llm.model_name || '—'),
    diagRow('接口地址', llm.base_url || '—'),
  ]);

  const kb = report.knowledge || {};
  renderDiagSection(els.diagKnowledge, '知识库', [
    diagRow('内置文档', `${kb.documents ?? 0} 个`),
    diagRow('上传文档', `${kb.uploads ?? 0} 个`),
    diagRow('向量库文件', (kb.vector_store_files || []).join('、') || '未生成'),
  ]);

  els.diagPackages.replaceChildren();
  const heading = document.createElement('h3');
  heading.textContent = '依赖';
  els.diagPackages.appendChild(heading);

  for (const pkg of report.packages || []) {
    const row = document.createElement('div');
    row.className = 'diag-row' + (pkg.ok ? '' : ' bad');

    const key = document.createElement('span');
    key.className = 'k';
    key.textContent = `${pkg.ok ? '✅' : '❌'} ${pkg.package}`;

    const val = document.createElement('span');
    val.className = 'v';
    val.textContent = pkg.ok
      ? (pkg.version || '已安装')
      : (pkg.required ? '未安装' : '未安装（可选）');

    row.append(key, val);
    els.diagPackages.appendChild(row);
  }

  setDiagStatus('');
}

const AUDIT_LABELS = {
  'session.delete': '删除会诊',
  'session.archive': '归档会诊',
  'session.unarchive': '恢复会诊',
  'session.export': '导出备份',
  'session.import': '导入备份',
  'session.tags': '更新标签',
  'session.bulk_archive': '批量归档',
  'session.bulk_delete': '批量删除',
  'session.bulk_tags': '批量设置标签',
  'profile.delete': '删除患者档案',
  'profile.rename': '重命名患者档案',
  'metric.delete': '删除健康记录',
  'reminder.create': '添加随访提醒',
  'reminder.complete': '完成随访提醒',
  'reminder.update': '修改随访提醒',
  'reminder.snooze': '推迟随访提醒',
  'reminder.delete': '删除随访提醒',
};

function auditFilterParams(withLimit = true) {
  const params = new URLSearchParams();
  const action = (els.auditAction.value || '').trim();
  const keyword = (els.auditKeyword.value || '').trim();
  const start = (els.auditStart.value || '').trim();
  const end = (els.auditEnd.value || '').trim();
  if (action) params.set('action', action);
  if (keyword) params.set('q', keyword);
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  if (withLimit) params.set('limit', '20');
  return params;
}

function auditFilterActive() {
  return Boolean(
    (els.auditAction.value || '').trim()
    || (els.auditKeyword.value || '').trim()
    || (els.auditStart.value || '').trim()
    || (els.auditEnd.value || '').trim()
  );
}

function populateAuditActions(actions) {
  if (!els.auditAction) return;
  const current = els.auditAction.value || '';
  els.auditAction.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = '全部动作';
  els.auditAction.appendChild(all);

  for (const action of actions || []) {
    const option = document.createElement('option');
    option.value = action;
    option.textContent = AUDIT_LABELS[action] || action;
    els.auditAction.appendChild(option);
  }

  els.auditAction.value = (actions || []).includes(current) ? current : '';
}

async function loadAuditLog() {
  try {
    const res = await fetch(`/api/audit?${auditFilterParams().toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    const actions = data.actions || [];
    populateAuditActions(data.available_actions || []);

    const rows = actions.map((item) => {
      const label = AUDIT_LABELS[item.action] || item.action;
      const metaParts = [item.target, item.detail, formatTime(item.created_at)]
        .filter(Boolean);
      return diagRow(label, metaParts.join(' · ') || '—');
    });
    if (!rows.length) {
      rows.push(diagRow(
        auditFilterActive() ? '没有符合条件的记录' : '暂无操作记录',
        '—'
      ));
    }
    renderDiagSection(
      els.diagAudit,
      auditFilterActive() ? '筛选结果' : '最近操作',
      rows
    );
  } catch {
    // 审计加载失败不影响系统自检
  }
}

async function exportAuditCsv() {
  const button = els.diagAuditExport;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '…';

  try {
    const params = auditFilterParams(false).toString();
    const res = await fetch(`/api/audit/export.csv${params ? `?${params}` : ''}`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-audit-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    button.textContent = auditFilterActive() ? '已导出筛选' : '已导出';
  } catch (err) {
    button.textContent = '失败';
    window.alert(`导出审计失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1600);
  }
}

async function purgeOldSessions() {
  const input = window.prompt(
    '删除多少天以前的会诊？删除后不可恢复。',
    '90'
  );
  if (input === null) return;

  const days = Number(input);
  if (!Number.isFinite(days) || days < 1 || days > 3650) {
    window.alert('请输入 1–3650 之间的天数。');
    return;
  }
  if (!window.confirm(
    `将永久删除 ${days} 天以前的会诊及其消息，确定继续？`
  )) {
    return;
  }

  const button = els.diagPurge;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '清理中…';
  try {
    const res = await fetch('/api/maintenance/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setDiagStatus(
      `已清理 ${data.sessions || 0} 次会诊`
      + `（${data.messages || 0} 条消息）`
    );
    await loadHistory(els.historySearch.value.trim());
    loadAuditLog();
  } catch (err) {
    setDiagStatus(`清理失败：${err.message || err}`, true);
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
}

async function loadDiagnostics() {
  els.diagSummary.className = 'diag-summary';
  els.diagSummary.textContent = '正在检查…';

  try {
    const res = await fetch('/api/diagnose');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    renderDiagnostics(data);
  } catch (err) {
    els.diagSummary.className = 'diag-summary warn';
    els.diagSummary.textContent = '系统自检失败';
    setDiagStatus(err.message || String(err), true);
  }
}

function openDiagnose() {
  closeHistory();
  closeKb();
  closeCalc();
  closeMetrics();
  closeReminders();
  closeCompare();
  diagFocused = document.activeElement;
  diagOpen = true;
  els.diagnoseDrawer.hidden = false;
  els.diagnoseBackdrop.hidden = false;
  syncPageInert();
  els.diagnose.setAttribute('aria-expanded', 'true');
  setDiagStatus('');
  loadDiagnostics();
  loadAuditLog();
  els.diagnoseClose.focus();
}

function closeDiagnose(restoreFocus = false) {
  diagOpen = false;
  els.diagnoseDrawer.hidden = true;
  els.diagnoseBackdrop.hidden = true;
  syncPageInert();
  els.diagnose.setAttribute('aria-expanded', 'false');
  if (restoreFocus && diagFocused instanceof HTMLElement) {
    diagFocused.focus();
  }
}

/* ---------- 健康指标记录 ---------- */

let metricsFocused = null;
let editingMetricId = null;
let metricsRequestSeq = 0;
let metricTrendRequestSeq = 0;
let metricOverviewRequestSeq = 0;
let metricSummaryRequestSeq = 0;
let metricWeeklyRequestSeq = 0;
let metricsOffset = 0;
let metricItemsCache = [];
const METRICS_PAGE_SIZE = 50;

const METRIC_UNITS = {
  bp: 'mmHg',
  weight: 'kg',
  glucose: 'mmol/L',
  temperature: '°C',
  heart_rate: '次/分',
  spo2: '%',
  waist: 'cm',
  phq9: '分',
  gad7: '分',
};
const METRIC_NAMES = {
  bp: '血压',
  weight: '体重',
  glucose: '血糖',
  temperature: '体温',
  heart_rate: '心率',
  spo2: '血氧饱和度',
  waist: '腰围',
  phq9: 'PHQ-9 评分',
  gad7: 'GAD-7 评分',
};
const METRIC_REFERENCES = {
  bp: '家庭血压参考：<135/85 mmHg；≥135/85 建议复测并咨询医生。',
  weight: '体重需结合身高计算 BMI，单一体重没有统一正常范围。',
  glucose: '血糖参考值需结合空腹/餐后与化验单，本工具不做诊断。',
  temperature: '腋温参考：36.0–37.3 °C。',
  heart_rate: '静息心率参考：60–100 次/分；运动后或发热时会升高。',
  spo2: '静息血氧参考：≥95%；持续低于 90% 请尽快就医。',
  waist: '腰围参考：男性 <90 cm、女性 <85 cm（中国成人中心性肥胖标准）。',
  phq9: '评分越高提示抑郁症状越明显，需结合临床访谈判断。',
  gad7: '评分越高提示焦虑症状越明显，需结合临床访谈判断。',
};

/**
 * 诊室血压分级（中国高血压防治指南 2018 成人标准）。
 * 收缩压与舒张压分属不同级别时取较高者；均未达正常高值时再判断偏低。
 */
function classifyBloodPressure(systolic, diastolic) {
  const sys = Number(systolic);
  const dia = Number(diastolic);
  if (!Number.isFinite(sys) || !Number.isFinite(dia)) return null;

  const grade = (s, d) => {
    if (s >= 180 || d >= 110) return { label: '3 级高血压', level: 'danger' };
    if (s >= 160 || d >= 100) return { label: '2 级高血压', level: 'danger' };
    if (s >= 140 || d >= 90) return { label: '1 级高血压', level: 'warn' };
    if (s >= 120 || d >= 80) return { label: '正常高值', level: 'warn' };
    return { label: '正常', level: 'ok' };
  };

  const result = grade(sys, dia);
  if (result.label !== '正常') return result;
  if (sys < 90 || dia < 60) return { label: '偏低', level: 'low' };
  return result;
}



/**
 * 静息心率分级（仅作提示；运动后、发热或情绪激动时升高属正常）。
 */
function classifyHeartRate(value) {
  const rate = Number(value);
  if (!Number.isFinite(rate)) return null;
  if (rate < 50) return { label: '明显心动过缓', level: 'danger' };
  if (rate < 60) return { label: '心动过缓', level: 'warn' };
  if (rate <= 100) return { label: '正常', level: 'ok' };
  if (rate <= 120) return { label: '心动过速', level: 'warn' };
  return { label: '明显心动过速', level: 'danger' };
}

/**
 * 静息血氧饱和度分级（海平面、静息状态）。
 */
function classifySpo2(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return null;
  if (percent >= 95) return { label: '正常', level: 'ok' };
  if (percent >= 90) return { label: '偏低', level: 'warn' };
  return { label: '低氧', level: 'danger' };
}

function classifyMetric(item) {
  if (!item) return null;
  if (item.metric === 'bp') {
    return classifyBloodPressure(item.value1, item.value2);
  }
  if (item.metric === 'heart_rate') return classifyHeartRate(item.value1);
  if (item.metric === 'spo2') return classifySpo2(item.value1);
  return null;
}

function setMetricsStatus(text, isError = false) {
  els.metricsStatus.textContent = text;
  els.metricsStatus.hidden = !text;
  els.metricsStatus.classList.toggle('error', isError);
}

/* ---------- 健康指标自定义目标 ---------- */

let metricTargets = [];

function findMetricTarget(profile, metric) {
  return metricTargets.find(
    (item) => item.metric === metric
      && (item.profile || '') === (profile || '')
  ) || null;
}

function targetIsLowerBound(metric) {
  return metric === 'spo2';
}

function formatMetricTarget(target) {
  if (!target) return '';
  const unit = target.unit || '';
  if (target.metric === 'bp' && target.value2 != null) {
    return `${target.value1}/${target.value2} ${unit}`.trim();
  }
  return `${target.value1} ${unit}`.trim();
}

async function loadMetricTargets() {
  try {
    const res = await fetch('/api/metric-targets');
    if (!res.ok) return;
    const data = await res.json();
    metricTargets = data.targets || [];
  } catch {
    // 目标加载失败不影响指标展示
  }
}

function updateMetricTargetDisplay() {
  const target = findMetricTarget(els.metricProfile.value, els.metricType.value);
  els.metricTargetText.textContent = target
    ? `目标（${targetIsLowerBound(els.metricType.value) ? '下限' : '上限'}）：${formatMetricTarget(target)}`
    : '尚未设置目标';
  els.metricTargetClear.hidden = !target;
  els.metricTargetEdit.textContent = target ? '修改目标' : '设置目标';
  els.metricTargetEdit.hidden = false;
  els.metricTargetForm.hidden = true;
  renderMetricTargetList();
}

function startEditMetricTarget() {
  const metric = els.metricType.value;
  const target = findMetricTarget(els.metricProfile.value, metric);
  const isBp = metric === 'bp';
  els.metricTargetValue1.value = target ? String(target.value1) : '';
  els.metricTargetValue2.value =
    target && target.value2 != null ? String(target.value2) : '';
  els.metricTargetValue2.hidden = !isBp;
  els.metricTargetSep.hidden = !isBp;
  els.metricTargetValue1.setAttribute(
    'aria-label',
    targetIsLowerBound(metric) ? '目标下限' : '目标上限'
  );
  els.metricTargetForm.hidden = false;
  els.metricTargetEdit.hidden = true;
  els.metricTargetValue1.focus();
}

async function saveMetricTarget() {
  const metric = els.metricType.value;
  const profile = els.metricProfile.value;
  const value1 = Number(els.metricTargetValue1.value);
  const value2 = metric === 'bp' ? Number(els.metricTargetValue2.value) : null;
  if (!Number.isFinite(value1) || value1 <= 0) {
    setMetricsStatus('请填写有效的目标数值。', true);
    return;
  }
  if (metric === 'bp' && (!Number.isFinite(value2) || value2 <= 0)) {
    setMetricsStatus('请填写有效的舒张压目标。', true);
    return;
  }

  try {
    const res = await fetch('/api/metric-targets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile, metric, value1, value2 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    metricTargets = metricTargets.filter(
      (item) => !(item.metric === metric
        && (item.profile || '') === (profile || ''))
    );
    metricTargets.push(data.target);
    setMetricsStatus('已保存目标。');
    updateMetricTargetDisplay();
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`保存目标失败：${err.message || err}`, true);
  }
}

async function clearMetricTarget() {
  const metric = els.metricType.value;
  const profile = els.metricProfile.value;
  if (!findMetricTarget(profile, metric)) return;

  try {
    const params = new URLSearchParams();
    if (profile) params.set('profile', profile);
    const query = params.toString();
    const res = await fetch(
      `/api/metric-targets/${encodeURIComponent(metric)}${query ? `?${query}` : ''}`,
      { method: 'DELETE' }
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    metricTargets = metricTargets.filter(
      (item) => !(item.metric === metric
        && (item.profile || '') === (profile || ''))
    );
    setMetricsStatus('已清除目标。');
    updateMetricTargetDisplay();
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`清除目标失败：${err.message || err}`, true);
  }
}

function renderMetricTargetList() {
  const box = els.metricTargetList;
  const list = els.metricTargetListItems;
  if (!box || !list) return;

  const profile = els.metricProfile.value || '';
  const order = Object.keys(METRIC_UNITS);
  const items = (Array.isArray(metricTargets) ? metricTargets : [])
    .filter((target) => (target.profile || '') === profile)
    .sort((a, b) => order.indexOf(a.metric) - order.indexOf(b.metric));

  list.replaceChildren();
  if (!items.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  for (const target of items) {
    const label = METRIC_NAMES[target.metric] || target.metric;
    const li = document.createElement('li');
    li.className = 'metric-target-list-item';

    const text = document.createElement('span');
    text.textContent =
      `${label} 目标${targetIsLowerBound(target.metric) ? '下限' : '上限'} `
      + formatMetricTarget(target);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'metric-edit metric-target-remove';
    remove.textContent = '删除';
    remove.setAttribute('aria-label', `删除${label}目标`);
    remove.addEventListener('click', () => deleteMetricTargetFor(target));

    li.append(text, remove);
    list.appendChild(li);
  }
}

async function deleteMetricTargetFor(target) {
  try {
    const params = new URLSearchParams();
    if (target.profile) params.set('profile', target.profile);
    const query = params.toString();
    const res = await fetch(
      `/api/metric-targets/${encodeURIComponent(target.metric)}`
      + (query ? `?${query}` : ''),
      { method: 'DELETE' }
    );
    if (!res.ok && res.status !== 404) {
      throw new Error(`服务返回 ${res.status}`);
    }
    metricTargets = metricTargets.filter(
      (item) => !(item.metric === target.metric
        && (item.profile || '') === (target.profile || ''))
    );
    setMetricsStatus('已删除目标。');
    updateMetricTargetDisplay();
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`删除目标失败：${err.message || err}`, true);
  }
}

function metricNowLocal() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

function setMetricType(type) {
  const value = METRIC_UNITS[type] ? type : 'bp';
  els.metricType.value = value;
  const isBp = value === 'bp';
  els.metricBpValues.hidden = !isBp;
  els.metricSingleWrap.hidden = isBp;
  els.metricUnit.textContent = METRIC_UNITS[value];
  const isScore = value === 'phq9' || value === 'gad7';
  els.metricValue.step = isScore ? '1' : '0.1';
  els.metricValue.min = isScore ? '0' : '';
  els.metricReference.textContent = METRIC_REFERENCES[value] || '';
}

async function refreshMetricProfileOptions() {
  const usedNames = await fetchUsedProfileNames();
  const current = els.metricProfile.value;
  const names = new Set(Object.keys(profilesCache));
  for (const name of usedNames) names.add(name);

  els.metricProfile.replaceChildren();

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '未指定';
  els.metricProfile.appendChild(placeholder);

  for (const name of [...names].sort()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = profilesCache[name]
      ? name
      : `${name}（已删除）`;
    els.metricProfile.appendChild(option);
  }

  els.metricProfile.value = names.has(current) ? current : '';
}

function formatMetricValue(item) {
  if (item.metric === 'bp') {
    return `${item.value1}/${item.value2} ${item.unit}`;
  }
  return `${item.value1} ${item.unit}`;
}

function renderMetrics(items, days = 0, metric = els.metricType.value) {
  els.metricsList.replaceChildren();
  els.metricsEmpty.hidden = items.length > 0;
  const metricName = METRIC_NAMES[metric] || '该类';
  els.metricsEmpty.textContent = days > 0
    ? `所选时间范围内还没有${metricName}记录。`
    : `还没有${metricName}记录。`;

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const value = document.createElement('strong');
    value.className = 'metric-value';
    value.textContent = formatMetricValue(item);

    const target = findMetricTarget(item.profile || '', item.metric);
    let overTarget = false;
    if (target) {
      overTarget = targetIsLowerBound(item.metric)
        ? Number(item.value1) < target.value1
        : item.metric === 'bp'
          ? Number(item.value1) > target.value1
            || (target.value2 != null && Number(item.value2) > target.value2)
          : Number(item.value1) > target.value1;
      if (overTarget) {
        const badge = document.createElement('span');
        badge.className = 'metric-over-target';
        badge.textContent = targetIsLowerBound(item.metric)
          ? '低于目标'
          : '高于目标';
        badge.title =
          `目标${targetIsLowerBound(item.metric) ? '下限' : '上限'}：`
          + formatMetricTarget(target);
        value.append(' ', badge);
      }
    }

    const metricGrade = classifyMetric(item);
    if (metricGrade) {
      const badge = document.createElement('span');
      badge.className = `bp-grade bp-grade-${metricGrade.level}`;
      badge.textContent = metricGrade.label;
      value.append(' ', badge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      METRIC_NAMES[item.metric] || item.metric,
      item.profile || '未指定档案',
      formatTime(item.measured_at),
    ].join(' · ');

    main.append(value, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (overTarget || (metricGrade && metricGrade.level !== 'ok')) {
      const follow = document.createElement('button');
      follow.type = 'button';
      follow.className = 'metric-edit';
      follow.textContent = '安排复查';
      follow.setAttribute(
        'aria-label', `安排复查：${formatMetricValue(item)}`
      );
      follow.addEventListener('click', () => scheduleMetricFollowUp(item));
      actions.appendChild(follow);
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑记录：${formatMetricValue(item)}`);
    edit.addEventListener('click', () => editMetricRecord(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除记录：${formatMetricValue(item)}`);
    del.addEventListener('click', () => deleteMetricRecord(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.metricsList.appendChild(li);
  }
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, String(value));
  }
  return el;
}

function renderMetricTrend(items, metric) {
  const chart = els.metricTrendChart;
  chart.replaceChildren();

  if (!items.length) {
    els.metricTrend.hidden = true;
    return;
  }

  els.metricTrend.hidden = false;
  const chronological = items.slice().reverse();
  const latest = chronological[chronological.length - 1];
  els.metricTrendTitle.textContent =
    `${METRIC_NAMES[metric] || metric}趋势（最近 ${items.length} 条）`;
  els.metricTrendLatest.textContent = `最新：${formatMetricValue(latest)}`;

  const series = metric === 'bp'
    ? [
        { key: 'value1', cls: 'systolic', label: '收缩压' },
        { key: 'value2', cls: 'diastolic', label: '舒张压' },
      ]
    : [{ key: 'value1', cls: 'single', label: METRIC_NAMES[metric] || metric }];

  const values = [];
  for (const item of chronological) {
    for (const s of series) {
      const value = Number(item[s.key]);
      if (Number.isFinite(value)) values.push(value);
    }
  }
  if (!values.length) {
    els.metricTrend.hidden = true;
    return;
  }

  const target = findMetricTarget(els.metricProfile.value, metric);
  const targetValues = [];
  if (target) {
    targetValues.push(Number(target.value1));
    if (metric === 'bp' && target.value2 != null) {
      targetValues.push(Number(target.value2));
    }
  }
  for (const value of targetValues) {
    if (Number.isFinite(value)) values.push(value);
  }

  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const padding = Math.max(1, (max - min) * 0.15);
  min = Math.floor(min - padding);
  max = Math.ceil(max + padding);

  const width = 320;
  const height = 120;
  const left = 30;
  const right = 10;
  const top = 12;
  const bottom = 20;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const x = (index) => chronological.length === 1
    ? left + plotW / 2
    : left + (plotW * index) / (chronological.length - 1);
  const y = (value) => top + plotH * (1 - (value - min) / (max - min));

  for (const value of [min, (min + max) / 2, max]) {
    chart.appendChild(svgEl('line', {
      x1: left,
      y1: y(value),
      x2: width - right,
      y2: y(value),
      class: 'trend-grid',
    }));
    const label = svgEl('text', {
      x: left - 4,
      y: y(value) + 3,
      class: 'trend-axis',
      'text-anchor': 'end',
    });
    label.textContent = Number.isInteger(value)
      ? String(value)
      : value.toFixed(1);
    chart.appendChild(label);
  }

  for (const value of targetValues) {
    if (!Number.isFinite(value)) continue;
    chart.appendChild(svgEl('line', {
      x1: left,
      y1: y(value),
      x2: width - right,
      y2: y(value),
      class: 'trend-target',
    }));
    const label = svgEl('text', {
      x: width - right,
      y: y(value) - 3,
      class: 'trend-target-label',
      'text-anchor': 'end',
    });
    label.textContent = `目标 ${value}`;
    chart.appendChild(label);
  }

  for (const s of series) {
    const points = chronological
      .map((item, index) => {
        const value = Number(item[s.key]);
        return Number.isFinite(value)
          ? `${x(index).toFixed(1)},${y(value).toFixed(1)}`
          : null;
      })
      .filter(Boolean)
      .join(' ');

    if (points) {
      chart.appendChild(svgEl('polyline', {
        points,
        class: `trend-line ${s.cls}`,
      }));
    }

    chronological.forEach((item, index) => {
      const value = Number(item[s.key]);
      if (!Number.isFinite(value)) return;
      const dot = svgEl('circle', {
        cx: x(index),
        cy: y(value),
        r: 2.6,
        class: `trend-dot ${s.cls}`,
      });
      const title = svgEl('title');
      title.textContent =
        `${s.label} ${formatMetricValue(item)} · ${formatTime(item.measured_at)}`;
      dot.appendChild(title);
      chart.appendChild(dot);
    });
  }

  const firstAxis = svgEl('text', {
    x: left,
    y: height - 4,
    class: 'trend-axis',
  });
  firstAxis.textContent = formatTime(chronological[0].measured_at);
  chart.appendChild(firstAxis);

  if (chronological.length > 1) {
    const lastAxis = svgEl('text', {
      x: width - right,
      y: height - 4,
      class: 'trend-axis',
      'text-anchor': 'end',
    });
    lastAxis.textContent = formatTime(latest.measured_at);
    chart.appendChild(lastAxis);
  }

  els.metricTrendHint.textContent = chronological.length < 2
    ? '再记录一次即可看到变化。'
    : metric === 'bp'
    ? '蓝色为收缩压，绿色为舒张压；按测量时间从早到晚。'
    : '按测量时间从早到晚。';
  chart.setAttribute(
    'aria-label',
    `${METRIC_NAMES[metric] || metric}趋势，最新 ${formatMetricValue(latest)}`
  );
}

async function loadMetricTrend() {
  const requestSeq = ++metricTrendRequestSeq;
  const metric = els.metricType.value;
  const profile = els.metricProfile.value;
  const days = metricRangeDays();
  try {
    const res = await fetch(
      `/api/metrics?limit=30&metric=${encodeURIComponent(metric)}`
      + `&profile=${encodeURIComponent(profile)}`
      + (days > 0 ? `&days=${days}` : '')
    );
    if (!res.ok) return;
    const data = await res.json();
    if (requestSeq !== metricTrendRequestSeq) return;
    renderMetricTrend(data.metrics || [], metric);
  } catch {
    // 趋势加载失败不影响记录列表
  }
}

function formatStatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return String(Math.round(number * 10) / 10);
}

function formatStatDelta(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const rounded = Math.round(number * 10) / 10;
  if (rounded === 0) return '0';
  return rounded > 0 ? `+${rounded}` : `−${Math.abs(rounded)}`;
}

function renderMetricSummary(data) {
  const grid = els.metricStatGrid;
  if (!grid) return;
  grid.replaceChildren();
  const count = Number(data && data.count) || 0;
  if (!count) {
    grid.hidden = true;
    return;
  }
  grid.hidden = false;

  const metric = data.metric;
  const isBp = metric === 'bp';
  const unit = METRIC_UNITS[metric] ? ` ${METRIC_UNITS[metric]}` : '';
  const v1 = data.value1 || {};
  const v2 = data.value2 || {};
  const pair = (first, second, mapper) => (isBp
    ? `${mapper(first)}/${mapper(second)}`
    : mapper(first));

  const addChip = (label, value) => {
    const chip = document.createElement('div');
    chip.className = 'metric-stat';
    const name = document.createElement('span');
    name.className = 'metric-stat-label';
    name.textContent = label;
    const strong = document.createElement('strong');
    strong.className = 'metric-stat-value';
    strong.textContent = value;
    chip.append(name, strong);
    grid.appendChild(chip);
  };

  addChip('记录', `${count} 条`);
  addChip('平均', pair(v1.avg, v2.avg, formatStatNumber) + unit);
  addChip('最高', pair(v1.max, v2.max, formatStatNumber));
  addChip('最低', pair(v1.min, v2.min, formatStatNumber));

  const hasPrev1 = v1.prev_avg !== null && v1.prev_avg !== undefined;
  const hasPrev2 = v2.prev_avg !== null && v2.prev_avg !== undefined;
  if (
    Number(data.days) > 0
    && Number.isFinite(Number(v1.avg))
    && hasPrev1
  ) {
    const delta1 = Number(v1.avg) - Number(v1.prev_avg);
    const deltaText = isBp && hasPrev2
      ? `${formatStatDelta(delta1)}/${formatStatDelta(
        Number(v2.avg) - Number(v2.prev_avg)
      )}`
      : formatStatDelta(delta1);
    addChip('较上期', deltaText + unit);
  }

  if (data.target && Number.isFinite(Number(data.within_target))) {
    addChip('达标', `${data.within_target}/${count} 条`);
  }
}

async function loadMetricSummary() {
  const requestSeq = ++metricSummaryRequestSeq;
  const metric = els.metricType.value;
  const profile = els.metricProfile.value;
  const days = metricRangeDays();
  try {
    const res = await fetch(
      `/api/metrics/summary?metric=${encodeURIComponent(metric)}`
      + `&profile=${encodeURIComponent(profile)}`
      + (days > 0 ? `&days=${days}` : '')
    );
    if (!res.ok) return;
    const data = await res.json();
    if (requestSeq !== metricSummaryRequestSeq) return;
    renderMetricSummary(data);
  } catch {
    if (requestSeq === metricSummaryRequestSeq) {
      els.metricStatGrid.hidden = true;
    }
  }
}

function renderMetricWeekly(summaries) {
  const list = els.metricWeeklyList;
  if (!list) return;
  list.replaceChildren();
  const order = [
    'bp', 'weight', 'glucose', 'temperature', 'heart_rate', 'spo2',
    'waist', 'phq9', 'gad7',
  ];
  const items = (summaries || [])
    .filter((item) => item.count > 0)
    .sort((a, b) => order.indexOf(a.metric) - order.indexOf(b.metric));
  if (!items.length) {
    els.metricWeekly.hidden = true;
    return;
  }
  els.metricWeekly.hidden = false;

  for (const summary of items) {
    const metric = summary.metric;
    const v1 = summary.value1 || {};
    const v2 = summary.value2 || {};
    const isBp = metric === 'bp';
    const unit = METRIC_UNITS[metric] ? ` ${METRIC_UNITS[metric]}` : '';
    const average = isBp
      ? `${formatStatNumber(v1.avg)}/${formatStatNumber(v2.avg)}`
      : formatStatNumber(v1.avg);
    const hasPrev1 = v1.prev_avg !== null && v1.prev_avg !== undefined;
    const hasPrev2 = v2.prev_avg !== null && v2.prev_avg !== undefined;

    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'metric-weekly-item';
    button.title = `查看${METRIC_NAMES[metric] || metric}趋势`;

    const name = document.createElement('span');
    name.className = 'metric-weekly-name';
    name.textContent = `${METRIC_NAMES[metric] || metric} · ${summary.count} 条`;

    const value = document.createElement('strong');
    value.className = 'metric-weekly-value';
    value.textContent = `${average}${unit}`;

    button.append(name, value);

    if (hasPrev1) {
      const delta1 = Number(v1.avg) - Number(v1.prev_avg);
      const deltaText = isBp && hasPrev2
        ? `${formatStatDelta(delta1)}/${formatStatDelta(
          Number(v2.avg) - Number(v2.prev_avg)
        )}`
        : formatStatDelta(delta1);
      const delta = document.createElement('span');
      delta.className = 'metric-weekly-delta';
      delta.textContent = `较前 7 天 ${deltaText}${unit}`;
      button.appendChild(delta);
    }

    if (summary.target && Number.isFinite(Number(summary.within_target))) {
      const target = document.createElement('span');
      target.className = 'metric-weekly-target';
      target.textContent = `达标 ${summary.within_target}/${summary.count}`;
      button.appendChild(target);
    }

    button.addEventListener('click', () => {
      setMetricType(metric);
      refreshMetrics();
    });
    li.appendChild(button);
    list.appendChild(li);
  }
}

async function loadMetricWeekly() {
  const requestSeq = ++metricWeeklyRequestSeq;
  const profile = els.metricProfile.value;
  try {
    const res = await fetch(
      `/api/metrics?limit=500&profile=${encodeURIComponent(profile)}&days=7`
    );
    if (!res.ok) return;
    const items = (await res.json()).metrics || [];
    if (requestSeq !== metricWeeklyRequestSeq) return;
    const summaries = await fetchMetricSummaries(profile, items, 7);
    if (requestSeq !== metricWeeklyRequestSeq) return;
    renderMetricWeekly(summaries);
  } catch {
    if (requestSeq === metricWeeklyRequestSeq) {
      els.metricWeekly.hidden = true;
    }
  }
}

async function loadMetricOverview() {
  const requestSeq = ++metricOverviewRequestSeq;
  const profile = els.metricProfile.value;
  const days = metricRangeDays();
  try {
    const res = await fetch(
      `/api/metrics?limit=500&profile=${encodeURIComponent(profile)}`
      + (days > 0 ? `&days=${days}` : '')
    );
    if (!res.ok) return;
    const items = (await res.json()).metrics || [];
    if (requestSeq !== metricOverviewRequestSeq) return;

    const latest = {};
    for (const item of items) {
      if (!latest[item.metric]) latest[item.metric] = item;
    }

    els.metricOverviewGrid.replaceChildren();
    let count = 0;
    for (const metric of [
      'bp', 'weight', 'glucose', 'temperature', 'heart_rate',
      'spo2', 'waist', 'phq9', 'gad7',
    ]) {
      const item = latest[metric];
      if (!item) continue;
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'metric-overview-chip';
      const grade = classifyMetric(item);
      chip.textContent =
        `${METRIC_NAMES[metric]} ${formatMetricValue(item)}`
        + (grade ? ` · ${grade.label}` : '');
      chip.title = `${formatTime(item.measured_at)}${item.note ? ` · ${item.note}` : ''}`;
      chip.addEventListener('click', () => {
        setMetricType(metric);
        refreshMetrics();
      });
      els.metricOverviewGrid.appendChild(chip);
      count += 1;
    }

    els.metricOverview.hidden = count === 0;
  } catch {
    // 概览加载失败不影响表单与列表
  }
}

async function refreshMetrics() {
  updateMetricTargetDisplay();
  await loadMetrics();
  await loadMetricTrend();
  await loadMetricSummary();
  await loadMetricWeekly();
  await loadMetricOverview();
  loadHomeGlance();
}

function metricRangeDays() {
  return Number(els.metricRange && els.metricRange.value) || 0;
}

async function loadMetrics(append = false) {
  const requestSeq = ++metricsRequestSeq;
  const metric = els.metricType.value;
  const profile = els.metricProfile.value;
  const days = metricRangeDays();
  if (!append) {
    metricsOffset = 0;
    metricItemsCache = [];
  }
  try {
    const res = await fetch(
      `/api/metrics?limit=${METRICS_PAGE_SIZE}`
      + `&offset=${metricsOffset}`
      + `&metric=${encodeURIComponent(metric)}`
      + `&profile=${encodeURIComponent(profile)}`
      + (days > 0 ? `&days=${days}` : '')
    );
    if (!res.ok) return;
    const data = await res.json();
    if (requestSeq !== metricsRequestSeq) return;
    const items = data.metrics || [];
    metricItemsCache = append
      ? metricItemsCache.concat(items)
      : items;
    metricsOffset = metricItemsCache.length;
    if (els.metricsMore) {
      els.metricsMore.hidden = items.length < METRICS_PAGE_SIZE;
    }
    renderMetrics(metricItemsCache, days, metric);
  } catch {
    // 读取失败时保留当前列表
  }
}

async function openMetrics() {
  closeHistory();
  closeKb();
  closeCalc();
  closeDiagnose();
  closeReminders();
  closeCompare();
  metricsFocused = document.activeElement;
  metricsOpen = true;
  els.metricsDrawer.hidden = false;
  els.metricsBackdrop.hidden = false;
  syncPageInert();
  els.metricsOpen.setAttribute('aria-expanded', 'true');
  setMetricsStatus('');
  await refreshMetricProfileOptions();
  const activeProfile = els.profileSelect.value;
  if (activeProfile && profilesCache[activeProfile]) {
    els.metricProfile.value = activeProfile;
  }
  if (!els.metricTime.value) els.metricTime.value = metricNowLocal();
  setMetricType(els.metricType.value || 'bp');
  resetMetricForm();
  loadMetricTargets().then(() => refreshMetrics());
  els.metricsClose.focus();
}

function closeMetrics(restoreFocus = false) {
  metricsOpen = false;
  els.metricsDrawer.hidden = true;
  els.metricsBackdrop.hidden = true;
  syncPageInert();
  els.metricsOpen.setAttribute('aria-expanded', 'false');
  if (restoreFocus && metricsFocused instanceof HTMLElement) {
    metricsFocused.focus();
  }
}

/* ---------- 随访提醒 ---------- */

let remindersFocused = null;
let editingReminderId = null;
let remindersRequestSeq = 0;
let reminderSaving = false;
let reminderActionBusy = false;
let reminderPlanBusy = false;
let reminderProfileTouched = false;
let reminderCalendarOpen = false;
let reminderCalendarMonth = new Date();
let reminderCalendarDay = '';
let reminderItemsCache = [];

const REMINDER_WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

function todayLocalDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function defaultReminderDue() {
  return localDateAfter(7);
}

function resetReminderForm() {
  editingReminderId = null;
  els.reminderTitle.value = '';
  els.reminderNote.value = '';
  els.reminderDue.value = defaultReminderDue();
  els.reminderAdvance.value = '0';
  els.reminderRepeat.value = '0';
  els.reminderUntil.value = '';
  els.reminderUntilWrap.hidden = true;
  els.reminderAdd.textContent = '添加提醒';
  els.reminderCancel.hidden = true;
}

function editReminder(item) {
  editingReminderId = item.id;
  els.reminderTitle.value = item.title || '';
  if (els.reminderProfile) {
    els.reminderProfile.value = item.profile || '';
  }
  els.reminderDue.value = String(item.due_at || '').slice(0, 10);
  els.reminderAdvance.value = String(item.advance_days || 0);
  els.reminderRepeat.value = String(item.repeat_days || 0);
  els.reminderUntil.value = String(item.repeat_until || '').slice(0, 10);
  syncReminderRepeatFields();
  els.reminderNote.value = item.note || '';
  els.reminderAdd.textContent = '保存修改';
  els.reminderCancel.hidden = false;
  els.reminderTitle.focus();
}

function setRemindersStatus(text, isError = false) {
  els.remindersStatus.hidden = !text;
  els.remindersStatus.textContent = text || '';
  els.remindersStatus.classList.toggle('error', Boolean(isError));
}

async function openReminders() {
  closeHistory();
  closeKb();
  closeCalc();
  closeDiagnose();
  closeMetrics();
  closeCompare();
  remindersFocused = document.activeElement;
  remindersOpen = true;
  els.remindersDrawer.hidden = false;
  els.remindersBackdrop.hidden = false;
  syncPageInert();
  els.remindersOpen.setAttribute('aria-expanded', 'true');
  setRemindersStatus('');
  syncNotifyButton();
  resetReminderForm();
  reminderProfileTouched = false;
  await refreshReminderProfileOptions();
  if (els.reminderProfile && !reminderProfileTouched) {
    els.reminderProfile.value = els.profileSelect.value || '';
  }
  loadReminders();
  els.remindersClose.focus();
}

function closeReminders(restoreFocus = false) {
  remindersOpen = false;
  els.remindersDrawer.hidden = true;
  els.remindersBackdrop.hidden = true;
  syncPageInert();
  els.remindersOpen.setAttribute('aria-expanded', 'false');
  if (restoreFocus && remindersFocused instanceof HTMLElement) {
    remindersFocused.focus();
  }
}

function formatReminderDue(dueAt) {
  const [date, time] = String(dueAt || '').split('T');
  const parts = (date || '').split('-');
  if (parts.length !== 3) return dueAt || '—';
  const display = `${Number(parts[1])}月${Number(parts[2])}日`;
  return time ? `${display} ${time.slice(0, 5)}` : display;
}

function reminderIsDue(dueAt) {
  return String(dueAt || '').slice(0, 10) <= todayLocalDate();
}

function reminderDaysUntil(dueAt) {
  const due = String(dueAt || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(due)) return null;
  const today = todayLocalDate();
  const diff = Date.parse(`${due}T00:00:00`)
    - Date.parse(`${today}T00:00:00`);
  return Number.isFinite(diff) ? Math.round(diff / 86400000) : null;
}

function reminderIsAdvanceDue(item) {
  if (!item || item.done) return false;
  const advance = Number(item.advance_days) || 0;
  if (advance <= 0) return false;
  const days = reminderDaysUntil(item.due_at);
  return days !== null && days > 0 && days <= advance;
}

function shiftLocalDate(dateStr, days) {
  const [year, month, day] = String(dateStr || '').split('-').map(Number);
  if (!year || !month || !day) return dateStr;
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + Number(days || 0));
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function addMonthsToDate(dateStr, months) {
  const [year, month, day] = String(dateStr || '').split('-').map(Number);
  if (!year || !month || !day) return dateStr;
  const target = new Date(year, month - 1 + Number(months || 0), 1);
  const lastDay = new Date(
    target.getFullYear(), target.getMonth() + 1, 0
  ).getDate();
  target.setDate(Math.min(day, lastDay));
  const offset = target.getTimezoneOffset() * 60000;
  return new Date(target.getTime() - offset).toISOString().slice(0, 10);
}

function buildReminderSeries(startDate, interval, unit, count) {
  const dates = [];
  const step = Math.max(1, Number(interval) || 1);
  const total = Math.max(1, Math.min(Number(count) || 1, 24));
  for (let index = 0; index < total; index += 1) {
    if (unit === 'month') {
      dates.push(addMonthsToDate(startDate, step * index));
    } else {
      const days = unit === 'week' ? step * 7 : step;
      dates.push(shiftLocalDate(startDate, days * index));
    }
  }
  return dates;
}

function syncReminderRepeatFields() {
  const repeat = Math.trunc(Math.max(
    0, Math.min(365, Number(els.reminderRepeat.value) || 0)
  ));
  els.reminderUntilWrap.hidden = repeat <= 0;
  if (repeat <= 0) els.reminderUntil.value = '';
}

function reminderDueCountdown(item) {
  if (!item || item.done) return '';
  const days = reminderDaysUntil(item.due_at);
  if (days === null) return '';
  if (days < 0) return `（已逾期 ${Math.abs(days)} 天）`;
  if (days === 0) return '（今天）';
  return `（还有 ${days} 天）`;
}

function localDateAfter(days) {
  const now = new Date();
  now.setDate(now.getDate() + Number(days || 0));
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function reminderGroupOf(item) {
  if (item.done) return 'done';
  const due = String(item.due_at || '').slice(0, 10);
  const today = todayLocalDate();
  if (due < today) return 'overdue';
  if (due === today) return 'today';
  return due <= localDateAfter(7) ? 'soon' : 'later';
}

const REMINDER_GROUP_LABELS = {
  overdue: '已逾期',
  today: '今天到期',
  soon: '未来 7 天',
  later: '更晚',
  done: '已完成',
};

function renderReminderCalendar(items) {
  if (!els.remindersCalendar) return;
  els.remindersCalendar.hidden = !reminderCalendarOpen;
  els.remindersCalendarToggle.setAttribute(
    'aria-expanded', String(reminderCalendarOpen)
  );
  els.remindersCalendarToggle.textContent = reminderCalendarOpen
    ? '收起月历'
    : '月历';
  if (!reminderCalendarOpen) return;

  const counts = {};
  for (const item of items || []) {
    const due = String(item.due_at || '').slice(0, 10);
    if (!due) continue;
    counts[due] = (counts[due] || 0) + 1;
  }

  const year = reminderCalendarMonth.getFullYear();
  const month = reminderCalendarMonth.getMonth();
  els.remindersCalendarTitle.textContent = `${year} 年 ${month + 1} 月`;

  const grid = els.remindersCalendarGrid;
  grid.replaceChildren();
  for (const label of REMINDER_WEEKDAYS) {
    const head = document.createElement('span');
    head.className = 'reminders-calendar-weekday';
    head.textContent = label;
    grid.appendChild(head);
  }

  const firstDay = new Date(year, month, 1);
  const offset = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = todayLocalDate();
  for (let index = 0; index < offset; index += 1) {
    const blank = document.createElement('span');
    blank.className = 'reminders-calendar-blank';
    grid.appendChild(blank);
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = `${year}-${String(month + 1).padStart(2, '0')}`
      + `-${String(day).padStart(2, '0')}`;
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'reminders-calendar-day';
    if (date === today) cell.classList.add('is-today');
    if (date === reminderCalendarDay) cell.classList.add('is-selected');
    cell.setAttribute(
      'aria-pressed', String(date === reminderCalendarDay)
    );

    const number = document.createElement('span');
    number.textContent = String(day);
    cell.appendChild(number);

    const count = counts[date] || 0;
    if (count > 0) {
      const dot = document.createElement('span');
      dot.className = 'reminders-calendar-dot';
      dot.textContent = count > 9 ? '9+' : String(count);
      cell.appendChild(dot);
      cell.title = `${date}：${count} 条提醒`;
      cell.setAttribute('aria-label', `${date}，${count} 条提醒`);
    } else {
      cell.title = date;
      cell.setAttribute('aria-label', date);
    }

    cell.addEventListener('click', () => {
      reminderCalendarDay = reminderCalendarDay === date ? '' : date;
      refreshReminderViews();
    });
    grid.appendChild(cell);
  }

  els.remindersCalendarClear.hidden = !reminderCalendarDay;
}

function searchFilteredReminders() {
  const query = els.remindersSearch
    ? String(els.remindersSearch.value || '').trim().toLowerCase()
    : '';
  let items = reminderItemsCache;
  if (query) {
    items = items.filter((item) => (
      [item.title, item.note, item.profile]
        .map((value) => String(value || ''))
        .join(' ')
        .toLowerCase()
        .includes(query)
    ));
  }
  const overdueOnly = Boolean(
    els.remindersOverdueOnly && els.remindersOverdueOnly.checked
  );
  if (overdueOnly) {
    items = items.filter((item) => {
      if (item.done) return false;
      const days = reminderDaysUntil(item.due_at);
      return days !== null && days < 0;
    });
  }
  return items;
}

function refreshReminderViews() {
  const items = searchFilteredReminders();
  renderReminderCalendar(items);
  renderFilteredReminders();
}

function renderFilteredReminders() {
  const query = els.remindersSearch
    ? String(els.remindersSearch.value || '').trim()
    : '';
  const searched = searchFilteredReminders();
  const items = reminderCalendarDay
    ? searched.filter(
        (item) => String(item.due_at || '').slice(0, 10) === reminderCalendarDay
      )
    : searched;
  if (els.remindersEmpty) {
    if (query && !searched.length) {
      els.remindersEmpty.textContent = `没有匹配「${query}」的提醒。`;
    } else if (
      els.remindersOverdueOnly
      && els.remindersOverdueOnly.checked
      && !searched.length
    ) {
      els.remindersEmpty.textContent = '没有逾期的提醒。';
    } else if (reminderCalendarDay) {
      els.remindersEmpty.textContent =
        `所选日期（${formatReminderDue(reminderCalendarDay)}）没有提醒。`;
    } else {
      els.remindersEmpty.textContent = '还没有随访提醒。';
    }
  }
  renderReminders(items);
}

function shiftReminderCalendarMonth(delta) {
  reminderCalendarMonth = new Date(
    reminderCalendarMonth.getFullYear(),
    reminderCalendarMonth.getMonth() + delta,
    1
  );
  reminderCalendarDay = '';
  refreshReminderViews();
}

function renderReminders(items) {
  els.remindersList.replaceChildren();
  els.remindersEmpty.hidden = items.length > 0;

  let dueCount = 0;
  const groupCounts = {};
  for (const item of items) {
    const key = reminderGroupOf(item);
    groupCounts[key] = (groupCounts[key] || 0) + 1;
  }

  let lastGroup = null;
  for (const item of items) {
    const group = reminderGroupOf(item);
    if (group !== lastGroup) {
      lastGroup = group;
      const head = document.createElement('li');
      head.className = 'reminder-group-head';
      head.setAttribute('role', 'presentation');
      const label = document.createElement('span');
      label.textContent = REMINDER_GROUP_LABELS[group] || '';
      const count = document.createElement('span');
      count.className = 'reminder-group-count';
      count.textContent = `${groupCounts[group]} 条`;
      head.append(label, count);
      els.remindersList.appendChild(head);
    }
    const due = !item.done && reminderIsDue(item.due_at);
    if (due) dueCount += 1;

    const li = document.createElement('li');
    li.className = 'metric-item reminder-item';
    if (item.done) li.classList.add('reminder-item-done');

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'reminder-title';
    title.textContent = item.title;

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = `到期：${formatReminderDue(item.due_at)}${reminderDueCountdown(item)}`
      + (Number(item.advance_days) > 0
        ? ` · 提前 ${Number(item.advance_days)} 天`
        : '')
      + (Number(item.repeat_days) > 0
        ? ` · 每 ${Number(item.repeat_days)} 天重复`
          + (item.repeat_until
            ? `（截止 ${formatReminderDue(item.repeat_until)}）`
            : '')
        : '')
      + (item.profile ? ` · 档案：${item.profile}` : '');
    if (item.done) {
      const badge = document.createElement('span');
      badge.className = 'reminder-badge reminder-badge-done';
      badge.textContent = '已完成';
      meta.append(' ', badge);
    } else if (due) {
      const badge = document.createElement('span');
      badge.className = 'reminder-badge';
      badge.textContent = '已到期';
      meta.append(' ', badge);
    } else if (reminderIsAdvanceDue(item)) {
      const badge = document.createElement('span');
      badge.className = 'reminder-badge reminder-badge-advance';
      badge.textContent = '提前提醒';
      meta.append(' ', badge);
    }

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除提醒：${item.title}`);
    del.addEventListener('click', () => deleteReminder(item));

    if (item.done) {
      const reopen = document.createElement('button');
      reopen.type = 'button';
      reopen.className = 'metric-edit';
      reopen.textContent = '恢复';
      reopen.setAttribute('aria-label', `恢复提醒：${item.title}`);
      reopen.addEventListener('click', () => reopenReminder(item));
      actions.append(reopen, del);
    } else {
      const done = document.createElement('button');
      done.type = 'button';
      done.className = 'metric-edit';
      done.textContent = '完成';
      done.setAttribute('aria-label', `完成提醒：${item.title}`);
      done.addEventListener('click', () => completeReminder(item));

      const snooze = document.createElement('button');
      snooze.type = 'button';
      snooze.className = 'metric-edit';
      snooze.textContent = '推迟';
      snooze.title = '推迟 7 天';
      snooze.setAttribute('aria-label', `推迟提醒：${item.title}`);
      snooze.addEventListener('click', () => snoozeReminder(item));

      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'metric-edit';
      edit.textContent = '编辑';
      edit.setAttribute('aria-label', `编辑提醒：${item.title}`);
      edit.addEventListener('click', () => editReminder(item));

      actions.append(done, snooze, edit, del);
    }

    li.append(main, actions);
    els.remindersList.appendChild(li);
  }

  els.remindersDueHint.hidden = dueCount === 0;
  els.remindersDueHint.textContent = dueCount
    ? `有 ${dueCount} 条提醒已到期`
    : '';
  if (
    editingReminderId
    && !items.some((item) => item.id === editingReminderId)
  ) {
    resetReminderForm();
  }
}

function updateReminderBadge(count) {
  if (!els.remindersCount) return;
  const value = Number(count) || 0;
  els.remindersCount.hidden = value === 0;
  els.remindersCount.textContent = value > 99 ? '99+' : String(value);
  els.remindersOpen.setAttribute(
    'aria-label',
    value ? `随访提醒，${value} 条需要关注` : '随访提醒'
  );
}

async function refreshReminderBadge() {
  try {
    const res = await fetch('/api/reminders');
    if (!res.ok) return;
    const data = await res.json();
    const reminders = data.reminders || [];
    const attentionItems = reminders.filter(
      (item) => !item.done
        && (reminderIsDue(item.due_at) || reminderIsAdvanceDue(item))
    );
    updateReminderBadge(attentionItems.length);
    maybeNotifyDueReminders(reminders);
  } catch {
    // 徽标加载失败不影响主流程
  }
}

const REMINDER_NOTIFIED_KEY = 'medix.reminderNotified';

function syncNotifyButton() {
  if (!els.remindersNotify) return;
  if (!('Notification' in window)) {
    els.remindersNotify.hidden = true;
    return;
  }
  els.remindersNotify.hidden = false;
  const state = Notification.permission;
  if (state === 'granted') {
    els.remindersNotify.textContent = '已开启到期通知';
    els.remindersNotify.disabled = true;
  } else if (state === 'denied') {
    els.remindersNotify.textContent = '通知已被浏览器拒绝';
    els.remindersNotify.disabled = true;
  } else {
    els.remindersNotify.textContent = '开启到期通知';
    els.remindersNotify.disabled = false;
  }
}

async function enableReminderNotifications() {
  if (!('Notification' in window)) return;
  try {
    const permission = await Notification.requestPermission();
    syncNotifyButton();
    if (permission === 'granted') {
      setRemindersStatus('已开启到期通知。');
      await loadReminders();
    } else if (permission === 'denied') {
      setRemindersStatus(
        '浏览器已拒绝通知权限，可在站点设置中重新开启。',
        true
      );
    }
  } catch {
    // 部分浏览器需要用户手势，失败时静默
  }
}

function maybeNotifyDueReminders(items) {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return;
  }
  const today = todayLocalDate();
  try {
    if (localStorage.getItem(REMINDER_NOTIFIED_KEY) === today) return;
  } catch {
    return;
  }
  const dueItems = (items || []).filter(
    (item) => !item.done && reminderIsDue(item.due_at)
  );
  const advanceItems = (items || []).filter(reminderIsAdvanceDue);
  if (!dueItems.length && !advanceItems.length) return;

  const parts = [];
  if (dueItems.length) parts.push(`${dueItems.length} 条提醒已到期`);
  if (advanceItems.length) parts.push(`${advanceItems.length} 条提醒即将到期`);
  const titles = dueItems.concat(advanceItems)
    .slice(0, 3)
    .map((item) => item.title)
    .join('、');
  const total = dueItems.length + advanceItems.length;
  const body = titles
    ? `有${parts.join('，')}：${titles}${total > 3 ? ' 等' : ''}`
    : `有${parts.join('，')}。`;
  try {
    new Notification('MediX 随访提醒', { body, tag: 'medix-reminders' });
    localStorage.setItem(REMINDER_NOTIFIED_KEY, today);
  } catch {
    // 通知构造失败时忽略
  }
}

function homeGlanceLine(label, value, sub) {
  const parts = [];
  const head = document.createElement('span');
  head.className = 'home-glance-label';
  head.textContent = label;
  const main = document.createElement('strong');
  main.className = 'home-glance-value';
  main.textContent = value;
  const extra = document.createElement('span');
  extra.className = 'home-glance-sub';
  extra.textContent = sub;
  parts.push(head, main, extra);
  return parts;
}

function renderHomeGlance(reminders, metrics) {
  if (!els.homeGlance) return;

  const pending = (reminders || []).filter((item) => !item.done);
  const overdue = pending.filter(
    (item) => Number(reminderDaysUntil(item.due_at)) < 0
  ).length;
  const upcoming = pending
    .filter((item) => {
      const days = reminderDaysUntil(item.due_at);
      return days !== null && days >= 0;
    })
    .sort((a, b) => String(a.due_at).localeCompare(String(b.due_at)));

  let reminderValue = '暂无待办提醒';
  let reminderSub = '点击打开随访提醒';
  if (overdue > 0) {
    reminderValue = `${overdue} 条已逾期`;
    reminderSub = upcoming.length
      ? `下一条：${formatReminderDue(upcoming[0].due_at)}`
      : '点击打开随访提醒';
  } else if (upcoming.length) {
    reminderValue = `下一条：${formatReminderDue(upcoming[0].due_at)}`;
    reminderSub = upcoming[0].title || '点击打开随访提醒';
  }

  const latest = {};
  for (const item of metrics || []) {
    if (!latest[item.metric]) latest[item.metric] = item;
  }
  const metricTypes = [
    'bp', 'weight', 'glucose', 'temperature', 'heart_rate', 'spo2',
    'waist', 'phq9', 'gad7',
  ];
  const recent = metricTypes
    .filter((metric) => latest[metric])
    .slice(0, 3)
    .map((metric) => `${METRIC_NAMES[metric]} ${formatMetricValue(latest[metric])}`);
  const metricValue = recent.length ? recent.join(' · ') : '暂无健康记录';
  const metricSub = recent.length ? '点击打开健康记录' : '点击录入第一条记录';

  els.homeGlanceReminders.replaceChildren(
    ...homeGlanceLine('随访提醒', reminderValue, reminderSub)
  );
  els.homeGlanceMetrics.replaceChildren(
    ...homeGlanceLine('健康记录', metricValue, metricSub)
  );
  els.homeGlance.hidden = false;
}

async function loadHomeGlance() {
  if (!els.homeGlance) return;
  try {
    const [remindersRes, metricsRes] = await Promise.all([
      fetch('/api/reminders'),
      fetch('/api/metrics?limit=20'),
    ]);
    const reminders = remindersRes.ok
      ? (await remindersRes.json()).reminders || []
      : [];
    const metrics = metricsRes.ok
      ? (await metricsRes.json()).metrics || []
      : [];
    renderHomeGlance(reminders, metrics);
  } catch {
    els.homeGlance.hidden = true;
  }
}

async function loadReminders() {
  const requestSeq = ++remindersRequestSeq;
  try {
    const includeDone = Boolean(
      els.remindersShowDone && els.remindersShowDone.checked
    );
    const params = new URLSearchParams();
    const profile = els.remindersProfile
      ? els.remindersProfile.value
      : '';
    if (profile) params.set('profile', profile);
    if (includeDone) params.set('include_done', 'true');
    const query = params.toString();
    const res = await fetch(`/api/reminders${query ? `?${query}` : ''}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== remindersRequestSeq) return;
    reminderItemsCache = data.reminders || [];
    renderFilteredReminders();
    refreshReminderBadge();
    loadHomeGlance();
  } catch (err) {
    if (requestSeq !== remindersRequestSeq) return;
    setRemindersStatus(`加载提醒失败：${err.message || err}`, true);
  }
}

async function refreshReminderProfileOptions() {
  if (!els.remindersProfile) return;

  const names = await fetchUsedProfileNames();
  for (const name of Object.keys(readProfiles())) names.add(name);

  const current = els.remindersProfile.value || '';
  els.remindersProfile.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = '全部档案';
  els.remindersProfile.appendChild(all);
  for (const name of [...names].sort()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = readProfiles()[name]
      ? name
      : `${name}（已删除）`;
    els.remindersProfile.appendChild(option);
  }
  els.remindersProfile.value = names.has(current) ? current : '';

  if (els.reminderProfile) {
    const formCurrent = els.reminderProfile.value || '';
    els.reminderProfile.replaceChildren();
    const unassigned = document.createElement('option');
    unassigned.value = '';
    unassigned.textContent = '未指定';
    els.reminderProfile.appendChild(unassigned);
    for (const name of [...names].sort()) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      els.reminderProfile.appendChild(option);
    }
    els.reminderProfile.value = names.has(formCurrent) ? formCurrent : '';
  }
}

async function addReminderSeries() {
  if (reminderPlanBusy) return;
  const title = (els.reminderTitle.value || '').trim();
  const startDate = (els.reminderDue.value || '').trim();
  if (!title) {
    setRemindersStatus('提醒内容不能为空。', true);
    els.reminderTitle.focus();
    return;
  }
  if (!startDate) {
    setRemindersStatus('请选择首次提醒日期。', true);
    els.reminderDue.focus();
    return;
  }

  const interval = Math.trunc(Math.max(
    1, Math.min(Number(els.reminderPlanInterval.value) || 1, 12)
  ));
  const unit = els.reminderPlanUnit.value;
  const count = Math.trunc(Math.max(
    2, Math.min(Number(els.reminderPlanCount.value) || 2, 24)
  ));
  const dates = buildReminderSeries(startDate, interval, unit, count);
  const base = {
    title,
    note: (els.reminderNote.value || '').trim(),
    profile: els.reminderProfile.value || '',
    advance_days: Math.trunc(Math.max(
      0,
      Math.min(30, Number(els.reminderAdvance.value) || 0)
    )),
  };

  reminderPlanBusy = true;
  els.reminderPlanAdd.disabled = true;
  setRemindersStatus(`正在创建 ${dates.length} 条提醒…`);
  let created = 0;
  try {
    for (const dueAt of dates) {
      const res = await fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...base, due_at: dueAt }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `服务返回 ${res.status}`);
      }
      created += 1;
    }
    setRemindersStatus(`已创建 ${created} 条随访提醒。`);
    resetReminderForm();
    await loadReminders();
  } catch (err) {
    setRemindersStatus(
      `已创建 ${created} 条，后续失败：${err.message || err}`,
      true
    );
    await loadReminders();
  } finally {
    reminderPlanBusy = false;
    els.reminderPlanAdd.disabled = false;
  }
}

async function addReminder() {
  if (reminderSaving) return;
  const title = (els.reminderTitle.value || '').trim();
  const dueAt = (els.reminderDue.value || '').trim();
  if (!title) {
    setRemindersStatus('提醒内容不能为空。', true);
    els.reminderTitle.focus();
    return;
  }
  if (!dueAt) {
    setRemindersStatus('请选择提醒日期。', true);
    els.reminderDue.focus();
    return;
  }

  const editingId = editingReminderId;
  const payload = {
    title,
    due_at: dueAt,
    note: (els.reminderNote.value || '').trim(),
    profile: els.reminderProfile.value || '',
    advance_days: Math.trunc(Math.max(
      0,
      Math.min(30, Number(els.reminderAdvance.value) || 0)
    )),
    repeat_days: Math.trunc(Math.max(
      0,
      Math.min(365, Number(els.reminderRepeat.value) || 0)
    )),
    repeat_until: els.reminderUntil.value || '',
  };
  if (!editingId) payload.session_id = sessionId || '';

  els.reminderAdd.disabled = true;
  reminderSaving = true;
  try {
    const res = await fetch(
      editingId ? `/api/reminders/${editingId}` : '/api/reminders',
      {
        method: editingId ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    resetReminderForm();
    setRemindersStatus(editingId ? '已保存修改。' : '已添加提醒。');
    await loadReminders();
  } catch (err) {
    setRemindersStatus(
      `${editingId ? '保存修改' : '添加提醒'}失败：${err.message || err}`,
      true
    );
  } finally {
    els.reminderAdd.disabled = false;
    reminderSaving = false;
  }
}

async function completeReminder(item) {
  if (reminderActionBusy) return;
  reminderActionBusy = true;
  try {
    const res = await fetch(`/api/reminders/${item.id}/complete`, {
      method: 'POST',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingReminderId === item.id) resetReminderForm();
    setRemindersStatus(
      data.next && data.next.due_at
        ? `已完成提醒，下次：${formatReminderDue(data.next.due_at)}。`
        : '已完成提醒。'
    );
    await loadReminders();
  } catch (err) {
    window.alert(`完成提醒失败：${err.message || err}`);
  } finally {
    reminderActionBusy = false;
  }
}

async function reopenReminder(item) {
  if (reminderActionBusy) return;
  reminderActionBusy = true;
  try {
    const res = await fetch(`/api/reminders/${item.id}/reopen`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    setRemindersStatus('已恢复为待完成。');
    await loadReminders();
  } catch (err) {
    window.alert(`恢复提醒失败：${err.message || err}`);
  } finally {
    reminderActionBusy = false;
  }
}

async function snoozeReminder(item) {
  if (reminderActionBusy) return;
  reminderActionBusy = true;
  try {
    const res = await fetch(`/api/reminders/${item.id}/snooze?days=7`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json().catch(() => ({}));
    const due = data.reminder ? formatReminderDue(data.reminder.due_at) : '';
    setRemindersStatus(due ? `已推迟到 ${due}。` : '已推迟 7 天。');
    await loadReminders();
  } catch (err) {
    window.alert(`推迟提醒失败：${err.message || err}`);
  } finally {
    reminderActionBusy = false;
  }
}

async function deleteReminder(item) {
  if (reminderActionBusy) return;
  if (!window.confirm(`删除提醒「${item.title}」？`)) return;
  reminderActionBusy = true;
  try {
    const res = await fetch(`/api/reminders/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingReminderId === item.id) resetReminderForm();
    setRemindersStatus('已删除提醒。');
    await loadReminders();
  } catch (err) {
    window.alert(`删除提醒失败：${err.message || err}`);
  } finally {
    reminderActionBusy = false;
  }
}

async function exportRemindersCsv() {
  const params = new URLSearchParams();
  const profile = els.remindersProfile ? els.remindersProfile.value : '';
  if (profile) params.set('profile', profile);
  params.set(
    'include_done',
    els.remindersShowDone && els.remindersShowDone.checked
      ? 'true'
      : 'false'
  );

  try {
    const res = await fetch(
      `/api/reminders/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-reminders-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setRemindersStatus('已导出提醒 CSV。');
  } catch (err) {
    window.alert(`导出提醒失败：${err.message || err}`);
  }
}

async function importRemindersCsv(file) {
  if (!file) return;
  setRemindersStatus('正在导入随访提醒…');

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/reminders/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复或日期无效`);
    if (data.invalid) parts.push(`缺少内容或日期 ${data.invalid} 行`);
    setRemindersStatus(parts.join('，') + '。', data.imported === 0);
    await refreshReminderProfileOptions();
    await loadReminders();
  } catch (err) {
    setRemindersStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.remindersImportFile.value = '';
  }
}

async function exportRemindersIcs() {
  const params = new URLSearchParams();
  const profile = els.remindersProfile ? els.remindersProfile.value : '';
  if (profile) params.set('profile', profile);
  if (els.remindersShowDone && els.remindersShowDone.checked) {
    params.set('include_done', 'true');
  }

  try {
    const res = await fetch(
      `/api/reminders/export.ics?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/calendar;charset=utf-8' }),
      `medix-reminders-${stamp}.ics`,
      'text/calendar;charset=utf-8'
    );
    setRemindersStatus('已导出日历文件，可导入手机或桌面日历。');
  } catch (err) {
    window.alert(`导出日历时失败：${err.message || err}`);
  }
}

function resetMetricForm() {
  editingMetricId = null;
  els.metricSystolic.value = '';
  els.metricDiastolic.value = '';
  els.metricValue.value = '';
  els.metricNote.value = '';
  els.metricTime.value = metricNowLocal();
  els.metricAdd.textContent = '保存记录';
  els.metricCancel.hidden = true;
}

function cancelMetricEdit() {
  resetMetricForm();
  setMetricsStatus('已取消编辑。');
}

async function scheduleMetricFollowUp(item) {
  await openReminders();
  if (els.reminderProfile) {
    els.reminderProfile.value = item.profile || '';
  }
  els.reminderTitle.value = `复查${METRIC_NAMES[item.metric] || '指标'}`;
  els.reminderDue.value = localDateAfter(7);
  els.reminderAdvance.value = '1';
  els.reminderNote.value =
    `上次记录：${formatMetricValue(item)}（${formatTime(item.measured_at)}）`;
  if (els.remindersSearch) els.remindersSearch.value = '';
  if (els.remindersProfile) {
    els.remindersProfile.value = item.profile || '';
    loadReminders();
  }
  setRemindersStatus('已代入复查提醒，确认后点击「添加提醒」。');
  els.reminderTitle.focus();
}

function editMetricRecord(item) {
  editingMetricId = item.id;
  setMetricType(item.metric);
  const hasProfile = Array.from(els.metricProfile.options)
    .some((option) => option.value === item.profile);
  els.metricProfile.value = hasProfile ? item.profile : '';

  if (item.metric === 'bp') {
    els.metricSystolic.value = String(item.value1);
    els.metricDiastolic.value = String(item.value2);
    els.metricValue.value = '';
  } else {
    els.metricSystolic.value = '';
    els.metricDiastolic.value = '';
    els.metricValue.value = String(item.value1);
  }

  els.metricTime.value = String(item.measured_at || '').slice(0, 16);
  els.metricNote.value = item.note || '';
  els.metricAdd.textContent = '保存修改';
  els.metricCancel.hidden = false;
  setMetricsStatus(`正在编辑：${formatMetricValue(item)}`);

  const form = els.metricsDrawer.querySelector('.metrics-form');
  if (form) form.scrollIntoView({ block: 'start' });
  (item.metric === 'bp' ? els.metricSystolic : els.metricValue).focus();
}

async function addMetricRecord() {
  const type = els.metricType.value;
  const profile = els.metricProfile.value;
  const measuredAt = els.metricTime.value || metricNowLocal();
  const note = els.metricNote.value.trim();
  const editing = editingMetricId;
  let value1;
  let value2 = null;

  if (type === 'bp') {
    value1 = Number(els.metricSystolic.value);
    value2 = Number(els.metricDiastolic.value);
    if (!Number.isFinite(value1) || !Number.isFinite(value2)) {
      setMetricsStatus('请填写收缩压和舒张压。', true);
      return;
    }
  } else {
    value1 = Number(els.metricValue.value);
    if (!Number.isFinite(value1)) {
      setMetricsStatus('请填写数值。', true);
      return;
    }
  }

  els.metricAdd.disabled = true;
  els.metricAdd.textContent = editing ? '更新中…' : '保存中…';

  try {
    const res = await fetch(
      editing ? `/api/metrics/${editing}` : '/api/metrics',
      {
        method: editing ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile,
          metric: type,
          value1,
          value2,
          measured_at: measuredAt,
          note,
        }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof data.detail === 'string'
        ? data.detail
        : `请检查输入（${res.status}）`;
      throw new Error(detail);
    }

    setMetricsStatus(editing ? '已更新记录。' : '已保存记录。');
    resetMetricForm();
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`${editing ? '更新' : '保存'}失败：${err.message || err}`, true);
  } finally {
    els.metricAdd.disabled = false;
    els.metricAdd.textContent = editingMetricId ? '保存修改' : '保存记录';
  }
}

async function deleteMetricRecord(item) {
  if (!window.confirm(`删除这条${METRIC_NAMES[item.metric] || '健康'}记录？`)) {
    return;
  }

  try {
    const res = await fetch(`/api/metrics/${item.id}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok && res.status !== 404) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    if (editingMetricId === item.id) resetMetricForm();
    setMetricsStatus('已删除记录。');
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`删除失败：${err.message || err}`, true);
  }
}

async function importMetricsCsv(file) {
  if (!file) return;
  setMetricsStatus('正在导入健康记录…');

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/metrics/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复/无效`);
    if (data.invalid) parts.push(`格式错误 ${data.invalid} 行`);
    setMetricsStatus(parts.join('，') + '。', data.imported === 0);
    await refreshMetricProfileOptions();
    await refreshMetrics();
  } catch (err) {
    setMetricsStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.metricImportFile.value = '';
  }
}

async function exportMetricsCsv() {
  const button = els.metricExport;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '…';

  const profile = els.metricProfile.value;
  const metric = els.metricType.value;

  try {
    const res = await fetch(
      `/api/metrics/export.csv?profile=${encodeURIComponent(profile)}`
      + `&metric=${encodeURIComponent(metric)}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const buffer = await res.arrayBuffer();
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-metrics-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    button.textContent = '已导出';
  } catch (err) {
    button.textContent = '失败';
    window.alert(`导出健康记录失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1600);
  }
}

/* ---------- 临床计算工具 ---------- */

let calcFocused = null;
let currentCalcAction = null;

function setCalcStatus(text, isError = false) {
  els.calcStatus.textContent = text;
  els.calcStatus.hidden = !text;
  els.calcStatus.classList.toggle('error', isError);
}

function setCalcTool(tool) {
  els.calcTool.value = tool;
  try {
    localStorage.setItem('medix.calcTool', tool);
  } catch {
    // 隐私模式下不可用时忽略
  }
  currentCalcAction = null;
  els.calcSave.hidden = true;
  for (const form of [
    els.calcFormBmi,
    els.calcFormEgfr,
    els.calcFormChads,
    els.calcFormCurb65,
    els.calcFormChildPugh,
    els.calcFormPhq9,
    els.calcFormGad7,
    els.calcFormAuditc,
    els.calcFormMmrc,
    els.calcFormHasBled,
    els.calcFormGcs,
    els.calcFormWells,
    els.calcFormWellsPe,
    els.calcFormInteractions,
    els.calcFormQsofa,
    els.calcFormFib4,
    els.calcFormMeld,
    els.calcFormStopBang,
    els.calcFormPadua,
    els.calcFormAbcd2,
    els.calcFormMorse,
  ]) {
    form.hidden = form.dataset.tool !== tool;
  }
  if (tool === 'phq9') ensurePhq9Form();
  if (tool === 'gad7') ensureGad7Form();
  els.calcResult.hidden = true;
  setCalcStatus('');
}

function openCalc() {
  closeHistory();
  closeKb();
  closeDiagnose();
  closeMetrics();
  closeReminders();
  closeCompare();
  calcFocused = document.activeElement;
  calcOpen = true;
  els.calcDrawer.hidden = false;
  els.calcBackdrop.hidden = false;
  syncPageInert();
  els.calcOpen.setAttribute('aria-expanded', 'true');

  const context = readPatientContext();
  const age = Number.parseInt(context.age, 10);
  if (Number.isFinite(age)) {
    els.calcEgfrAge.value = String(age);
    els.calcChadsAge.value = String(age);
    els.calcCurbAge.value = String(age);
    els.calcHasBledAge.value = String(age);
  }
  if (context.sex === '女' || context.sex === '男') {
    const sex = context.sex === '女' ? 'female' : 'male';
    els.calcEgfrSex.value = sex;
    els.calcChadsSex.value = sex;
  }

  let storedTool = '';
  try {
    storedTool = localStorage.getItem('medix.calcTool') || '';
  } catch {
    storedTool = '';
  }
  const hasStoredTool = [...els.calcTool.options].some(
    (option) => option.value === storedTool
  );
  setCalcTool(
    hasStoredTool ? storedTool : (els.calcTool.value || 'bmi')
  );
  els.calcClose.focus();
}

function closeCalc(restoreFocus = false) {
  calcOpen = false;
  els.calcDrawer.hidden = true;
  els.calcBackdrop.hidden = true;
  syncPageInert();
  els.calcOpen.setAttribute('aria-expanded', 'false');
  if (restoreFocus && calcFocused instanceof HTMLElement) {
    calcFocused.focus();
  }
}

async function runCalc(url, body, build) {
  setCalcStatus('计算中…');
  els.calcResult.hidden = true;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof data.detail === 'string'
        ? data.detail
        : `请检查输入（${res.status}）`;
      throw new Error(detail);
    }

    const view = build(data);
    els.calcResultTitle.textContent = view.title;
    els.calcResultMain.textContent = view.main;
    els.calcResultDetail.textContent = view.detail || '';
    els.calcResultNote.textContent = view.note || data.note || '';
    els.calcResult.dataset.level = view.level || '';
    currentCalcAction = view.action || null;
    els.calcSave.hidden = !currentCalcAction;
    els.calcSave.textContent = currentCalcAction ? currentCalcAction.label : '保存到健康记录';
    els.calcResult.hidden = false;
    els.calcResult.scrollIntoView({ block: 'nearest' });
    setCalcStatus('');
  } catch (err) {
    currentCalcAction = null;
    els.calcSave.hidden = true;
    setCalcStatus(`计算失败：${err.message || err}`, true);
  }
}

function submitBmi(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/bmi',
    {
      height_cm: Number(els.calcBmiHeight.value),
      weight_kg: Number(els.calcBmiWeight.value),
    },
    (data) => ({
      title: 'BMI 结果',
      main: `BMI ${data.bmi}`,
      detail:
        `分类：${data.category} · 健康体重范围 `
        + `${data.healthy_weight_min}–${data.healthy_weight_max} kg`,
      note: data.note,
    })
  );
}

function submitEgfr(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/egfr',
    {
      age: Number(els.calcEgfrAge.value),
      sex: els.calcEgfrSex.value,
      creatinine: Number(els.calcEgfrCreatinine.value),
      unit: els.calcEgfrUnit.value,
    },
    (data) => ({
      title: 'eGFR 结果',
      main: `eGFR ${data.egfr} mL/min/1.73 m²`,
      detail:
        `分期 ${data.stage}（${data.description}）· `
        + `血肌酐 ${data.creatinine_mg_dl} mg/dL`,
      note: data.note,
    })
  );
}

function submitChads(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/chads-vasc',
    {
      age: Number(els.calcChadsAge.value),
      sex: els.calcChadsSex.value,
      chf: els.calcChadsChf.checked,
      hypertension: els.calcChadsHypertension.checked,
      diabetes: els.calcChadsDiabetes.checked,
      stroke_tia: els.calcChadsStroke.checked,
      vascular_disease: els.calcChadsVascular.checked,
    },
    (data) => ({
      title: 'CHA₂DS₂-VASc 结果',
      main: `评分 ${data.score} / 9`,
      detail: `风险分层：${data.risk}`,
      note: data.note,
    })
  );
}

const PHQ9_QUESTIONS = [
  '做事时提不起劲或没有兴趣',
  '感到心情低落、沮丧或绝望',
  '入睡困难、睡不安稳或睡得过多',
  '感觉疲倦或没有活力',
  '食欲不振或吃太多',
  '觉得自己很糟，或觉得自己很失败，或让自己/家人失望',
  '对事物专注有困难，例如看报纸或看电视时',
  '动作或说话速度缓慢到别人已经察觉，或正好相反——烦躁或坐立不安、动来动去',
  '有不如死掉或用某种方式伤害自己的念头',
];

const PHQ9_OPTIONS = [
  ['0', '完全不会'],
  ['1', '几天'],
  ['2', '一半以上天数'],
  ['3', '几乎每天'],
];

function ensurePhq9Form() {
  if (els.calcPhq9Questions.children.length) return;

  PHQ9_QUESTIONS.forEach((question, index) => {
    const label = document.createElement('label');
    label.className = 'calc-field phq9-question';

    const text = document.createElement('span');
    text.textContent = `${index + 1}. ${question}`;

    const select = document.createElement('select');
    select.className = 'phq9-select';
    select.dataset.index = String(index);
    for (const [value, optionLabel] of PHQ9_OPTIONS) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = optionLabel;
      select.appendChild(option);
    }

    label.append(text, select);
    els.calcPhq9Questions.appendChild(label);
  });
}

const GAD7_QUESTIONS = [
  '感到紧张、焦虑或急切',
  '不能停止或控制担忧',
  '对各种各样的事情担忧过多',
  '很难放松下来',
  '由于不安而无法静坐',
  '变得容易烦恼或急躁',
  '感到似乎将有可怕的事情发生而害怕',
];

function ensureQuestionOptions(select) {
  for (const [value, optionLabel] of PHQ9_OPTIONS) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = optionLabel;
    select.appendChild(option);
  }
}

function ensureGad7Form() {
  if (els.calcGad7Questions.children.length) return;

  GAD7_QUESTIONS.forEach((question, index) => {
    const label = document.createElement('label');
    label.className = 'calc-field phq9-question';

    const text = document.createElement('span');
    text.textContent = `${index + 1}. ${question}`;

    const select = document.createElement('select');
    select.className = 'gad7-select';
    select.dataset.index = String(index);
    ensureQuestionOptions(select);

    label.append(text, select);
    els.calcGad7Questions.appendChild(label);
  });
}

async function saveScreenerScore(metric, score, note) {
  const res = await fetch('/api/metrics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      metric,
      value1: score,
      measured_at: metricNowLocal(),
      note,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string'
      ? data.detail
      : `服务返回 ${res.status}`;
    setCalcStatus(`保存失败：${detail}`, true);
    throw new Error(detail);
  }
  setCalcStatus('已保存到健康记录，可在「健康记录」中查看趋势。');
}

function submitGad7(event) {
  event.preventDefault();
  ensureGad7Form();
  const answers = Array.from(
    els.calcGad7Questions.querySelectorAll('select')
  ).map((select) => Number(select.value));

  runCalc(
    '/api/tools/gad7',
    { answers },
    (data) => ({
      title: 'GAD-7 结果',
      main: `评分 ${data.score} / 21`,
      detail: `严重程度：${data.severity}`,
      note: data.note,
      action: {
        label: '保存 GAD-7 评分',
        run: () => saveScreenerScore('gad7', data.score, 'GAD-7 筛查'),
      },
    })
  );
}

function submitMmrc(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/mmrc',
    { grade: Number(els.calcMmrcGrade.value) },
    (data) => ({
      title: 'mMRC 结果',
      main: `${data.grade} 级 · ${data.severity}`,
      detail: data.label,
      note: data.note,
      level: data.grade >= 3 ? 'warning' : '',
    })
  );
}

function submitAuditC(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/audit-c',
    {
      q1: Number(els.calcAuditCQ1.value),
      q2: Number(els.calcAuditCQ2.value),
      q3: Number(els.calcAuditCQ3.value),
    },
    (data) => ({
      title: 'AUDIT-C 结果',
      main: `评分 ${data.score} / 12`,
      detail: `风险分层：${data.risk}`,
      note: data.note,
      level: data.risk === '高风险' ? 'warning' : '',
    })
  );
}

function submitHasBled(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/has-bled',
    {
      age: Number(els.calcHasBledAge.value),
      hypertension: els.calcHasBledHypertension.checked,
      abnormal_renal: els.calcHasBledRenal.checked,
      abnormal_liver: els.calcHasBledLiver.checked,
      stroke_history: els.calcHasBledStroke.checked,
      bleeding_history: els.calcHasBledBleeding.checked,
      labile_inr: els.calcHasBledInr.checked,
      drugs: els.calcHasBledDrugs.checked,
      alcohol: els.calcHasBledAlcohol.checked,
    },
    (data) => ({
      title: 'HAS-BLED 结果',
      main: `评分 ${data.score} / 9`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
    })
  );
}

function submitPhq9(event) {
  event.preventDefault();
  ensurePhq9Form();
  const answers = Array.from(
    els.calcPhq9Questions.querySelectorAll('select')
  ).map((select) => Number(select.value));

  runCalc(
    '/api/tools/phq9',
    { answers },
    (data) => ({
      title: 'PHQ-9 结果',
      main: `评分 ${data.score} / 27`,
      detail:
        `严重程度：${data.severity}`
        + (data.self_harm ? ' · 第 9 题提示需要立即获得专业帮助' : ''),
      note: data.note,
      level: data.urgent ? 'warning' : '',
      action: {
        label: '保存 PHQ-9 评分',
        run: () => saveScreenerScore('phq9', data.score, 'PHQ-9 筛查'),
      },
    })
  );
}

function submitGcs(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/gcs',
    {
      eye: Number(els.calcGcsEye.value),
      verbal: Number(els.calcGcsVerbal.value),
      motor: Number(els.calcGcsMotor.value),
    },
    (data) => ({
      title: 'GCS 结果',
      main: `评分 ${data.score} / 15`,
      detail:
        `严重程度：${data.severity} · `
        + `睁眼 ${data.eye} / 语言 ${data.verbal} / 运动 ${data.motor}`,
      note: data.note,
      level: data.score <= 8 ? 'warning' : '',
    })
  );
}

function submitWells(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/wells-dvt',
    {
      active_cancer: els.calcWellsCancer.checked,
      paralysis_or_immobilization: els.calcWellsParalysis.checked,
      bedridden_or_surgery: els.calcWellsBedridden.checked,
      deep_vein_tenderness: els.calcWellsTenderness.checked,
      entire_leg_swelling: els.calcWellsEntire.checked,
      calf_swelling: els.calcWellsCalf.checked,
      pitting_edema: els.calcWellsEdema.checked,
      collateral_veins: els.calcWellsCollateral.checked,
      previous_dvt: els.calcWellsPrevious.checked,
      alternative_diagnosis: els.calcWellsAlternative.checked,
    },
    (data) => ({
      title: 'Wells DVT 结果',
      main: `评分 ${data.score} 分`,
      detail:
        `概率分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '高概率' ? 'warning' : '',
    })
  );
}

function submitChildPugh(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/child-pugh',
    {
      bilirubin: Number(els.calcChildBilirubin.value),
      albumin: Number(els.calcChildAlbumin.value),
      inr: Number(els.calcChildInr.value),
      ascites: Number(els.calcChildAscites.value),
      encephalopathy: Number(els.calcChildEncephalopathy.value),
    },
    (data) => ({
      title: 'Child-Pugh 结果',
      main: `评分 ${data.score} / 15`,
      detail: `分级：Child-Pugh ${data.grade}`,
      note: data.note,
      level: data.grade === 'C' ? 'warning' : '',
    })
  );
}

function submitWellsPe(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/wells-pe',
    {
      clinical_signs_dvt: els.calcWellsPeDvt.checked,
      pe_more_likely: els.calcWellsPeLikely.checked,
      heart_rate_gt_100: els.calcWellsPeHr.checked,
      immobilization_or_surgery: els.calcWellsPeImmobilization.checked,
      previous_dvt_pe: els.calcWellsPePrevious.checked,
      hemoptysis: els.calcWellsPeHemoptysis.checked,
      malignancy: els.calcWellsPeMalignancy.checked,
    },
    (data) => ({
      title: 'Wells PE 结果',
      main: `评分 ${data.score} 分`,
      detail:
        `概率分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '高概率' ? 'warning' : '',
    })
  );
}

function submitInteractions(event) {
  event.preventDefault();
  const drugs = (els.calcInteractionsDrugs.value || '').trim();
  if (!drugs) {
    setCalcStatus('请至少填写一种药物。', true);
    return;
  }
  runCalc(
    '/api/tools/interactions',
    {
      drugs,
      allergies: (els.calcInteractionsAllergies.value || '').trim(),
    },
    (data) => {
      const alerts = data.alerts || [];
      if (!alerts.length) {
        return {
          title: '药物相互作用检查',
          main: '未发现规则覆盖的明确相互作用',
          detail: data.drugs ? `已检查：${data.drugs}` : '',
          note: '规则只覆盖常见明确风险，未命中不代表绝对安全；用药调整请咨询医生或药师。',
          level: 'ok',
        };
      }
      return {
        title: '药物相互作用检查',
        main: `发现 ${alerts.length} 条提示`,
        detail: alerts
          .map((alert) => `${alert.title}：${alert.detail}`)
          .join('；'),
        note: '以上为规则提示，不能替代医生或药师的判断。',
        level: data.level || 'warning',
      };
    }
  );
}

function submitStopBang(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/stop-bang',
    {
      snoring: els.calcStopBangSnoring.checked,
      tired: els.calcStopBangTired.checked,
      observed_apnea: els.calcStopBangApnea.checked,
      hypertension: els.calcStopBangHypertension.checked,
      bmi_gt_35: els.calcStopBangBmi.checked,
      age_gt_50: els.calcStopBangAge.checked,
      neck_gt_40cm: els.calcStopBangNeck.checked,
      male: els.calcStopBangMale.checked,
    },
    (data) => ({
      title: 'STOP-BANG 结果',
      main: `评分 ${data.score} / 8`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '低风险' ? '' : 'warning',
    })
  );
}

function submitPadua(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/padua',
    {
      active_cancer: els.calcPaduaCancer.checked,
      previous_vte: els.calcPaduaVte.checked,
      reduced_mobility: els.calcPaduaMobility.checked,
      thrombophilia: els.calcPaduaThrombophilia.checked,
      recent_trauma_surgery: els.calcPaduaTrauma.checked,
      age_ge_70: els.calcPaduaAge.checked,
      heart_respiratory_failure: els.calcPaduaHeart.checked,
      acute_mi_stroke: els.calcPaduaMi.checked,
      acute_infection_rheumatologic: els.calcPaduaInfection.checked,
      bmi_ge_30: els.calcPaduaBmi.checked,
      hormonal_treatment: els.calcPaduaHormonal.checked,
    },
    (data) => ({
      title: 'Padua 结果',
      main: `评分 ${data.score} 分`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 得分项：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '高风险' ? 'warning' : '',
    })
  );
}

function submitAbcd2(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/abcd2',
    {
      age_ge_60: els.calcAbcd2Age.checked,
      bp_ge_140_90: els.calcAbcd2Bp.checked,
      unilateral_weakness: els.calcAbcd2Weakness.checked,
      speech_disturbance: els.calcAbcd2Speech.checked,
      duration_ge_60: els.calcAbcd2Duration60.checked,
      duration_10_59: els.calcAbcd2Duration10.checked,
      diabetes: els.calcAbcd2Diabetes.checked,
    },
    (data) => ({
      title: 'ABCD2 结果',
      main: `评分 ${data.score} / 7`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 得分项：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '低风险' ? '' : 'warning',
    })
  );
}

function submitMorse(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/morse-fall',
    {
      history_of_falling: Number(els.calcMorseHistory.value),
      secondary_diagnosis: Number(els.calcMorseSecondary.value),
      ambulatory_aid: Number(els.calcMorseAid.value),
      intravenous: Number(els.calcMorseIv.value),
      gait: Number(els.calcMorseGait.value),
      mental_status: Number(els.calcMorseMental.value),
    },
    (data) => ({
      title: 'Morse 跌倒风险结果',
      main: `评分 ${data.score} 分`,
      detail:
        `风险分层：${data.risk} · ${(data.criteria || []).join('；')}`,
      note: data.note,
      level: data.risk === '低风险' ? '' : 'warning',
    })
  );
}

function submitQsofa(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/qsofa',
    {
      respiratory_rate: Number(els.calcQsofaRr.value),
      systolic_bp: Number(els.calcQsofaSbp.value),
      altered_mentation: els.calcQsofaMentation.checked,
    },
    (data) => ({
      title: 'qSOFA 结果',
      main: `评分 ${data.score} / 3`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
      level: data.risk === '高风险' ? 'warning' : '',
    })
  );
}

function submitFib4(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/fib-4',
    {
      age: Number(els.calcFib4Age.value),
      ast: Number(els.calcFib4Ast.value),
      alt: Number(els.calcFib4Alt.value),
      platelets: Number(els.calcFib4Platelets.value),
    },
    (data) => ({
      title: 'FIB-4 结果',
      main: `FIB-4 ${data.fib4}`,
      detail: `${data.risk}：${data.detail}`,
      note: data.note,
      level: data.risk === '高风险' ? 'warning' : '',
    })
  );
}

function submitMeld(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/meld',
    {
      bilirubin: Number(els.calcMeldBilirubin.value),
      inr: Number(els.calcMeldInr.value),
      creatinine: Number(els.calcMeldCreatinine.value),
      dialysis: els.calcMeldDialysis.checked,
    },
    (data) => ({
      title: 'MELD 结果',
      main: `MELD ${data.meld} 分`,
      detail: `风险分层：${data.risk} · ${(data.criteria || []).join('；')}`,
      note: data.note,
      level: ['高风险', '极高风险'].includes(data.risk) ? 'warning' : '',
    })
  );
}

function submitCurb65(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/curb65',
    {
      age: Number(els.calcCurbAge.value),
      confusion: els.calcCurbConfusion.checked,
      urea: Number(els.calcCurbUrea.value),
      respiratory_rate: Number(els.calcCurbRr.value),
      systolic_bp: Number(els.calcCurbSbp.value),
      diastolic_bp: Number(els.calcCurbDbp.value),
    },
    (data) => ({
      title: 'CURB-65 结果',
      main: `评分 ${data.score} / 5`,
      detail:
        `风险分层：${data.risk}`
        + (data.criteria && data.criteria.length
          ? ` · 符合：${data.criteria.join('、')}`
          : ' · 未命中评分项'),
      note: data.note,
    })
  );
}

async function testModelConnection() {
  const button = els.diagLlmTest;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '测试中…';
  setDiagStatus('正在调用模型…');

  try {
    const res = await fetch('/api/diagnose/llm', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    if (data.ok) {
      const tokens = Number(data.usage && data.usage.total_tokens || 0);
      setDiagStatus(
        `模型连通正常，返回：${data.reply || 'OK'}` +
        (tokens ? `（${tokens} tokens）` : '')
      );
    } else {
      setDiagStatus(`模型调用失败：${data.error || '未知错误'}`, true);
    }
  } catch (err) {
    setDiagStatus(`测试失败：${err.message || err}`, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function copyDiagnostics() {
  if (!lastDiagnostics) {
    setDiagStatus('还没有可复制的诊断结果，请先重新检查。', true);
    return;
  }
  const ok = await copyText(JSON.stringify(lastDiagnostics, null, 2));
  setDiagStatus(ok ? '诊断信息已复制到剪贴板。' : '复制失败。', !ok);
}

function downloadDiagnostics() {
  if (!lastDiagnostics) {
    setDiagStatus('还没有可下载的诊断结果，请先重新检查。', true);
    return;
  }
  downloadFile(
    JSON.stringify(lastDiagnostics, null, 2),
    'medix-diagnostics.json',
    'application/json;charset=utf-8'
  );
  setDiagStatus('诊断信息已开始下载。');
}

async function handleLaunchAction() {
  const params = new URLSearchParams(window.location.search);
  const action = params.get('action');
  if (!action) return;

  const url = new URL(window.location.href);
  url.searchParams.delete('action');
  window.history.replaceState(
    {},
    '',
    `${url.pathname}${url.search}${url.hash}`
  );

  if (action === 'new') {
    await startNewSession();
  } else if (action === 'reminders') {
    openReminders();
    const reminderQuery = params.get('q');
    if (reminderQuery && els.remindersSearch) {
      els.remindersSearch.value = reminderQuery;
    }
    if (params.get('calendar') === '1') {
      reminderCalendarOpen = true;
      renderReminderCalendar(reminderItemsCache);
    }
  } else if (action === 'metrics') {
    openMetrics();
  }
}

async function restoreCurrentSession() {
  if (!sessionId) return;
  const expectedSession = sessionId;

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(expectedSession)}/messages?limit=200`
    );
    if (!res.ok) return;

    const data = await res.json();
    if (sessionId !== expectedSession || busy) return;

    const messages = data.messages || [];
    if (!messages.length) return;

    els.readout.session.textContent = shortSession(expectedSession);
    renderStoredMessages(messages, expectedSession, data.profile || '');
    setVitals('idle', '已恢复上次会诊');
  } catch {
    // 服务未启动或尚无记录时保持空状态
  }
}

/* ---------- 提交主诉 ---------- */

async function send(question) {
  if (busy || !question.trim()) return;

  busy = true;
  els.send.disabled = true;
  els.stop.hidden = false;
  els.empty.hidden = true;
  userPausedFollow = false;

  const controller = new AbortController();
  activeController = controller;

  resetRail();
  const askTurn = renderAsk(question);

  const startedAt = performance.now();
  const working = renderWorking();
  const workingText = working.querySelector('.working-text');
  const workingTime = working.querySelector('.working-time');
  const elapsedTimer = setInterval(() => {
    workingTime.textContent = `${Math.floor((performance.now() - startedAt) / 1000)}s`;
  }, 500);

  els.thread.setAttribute('aria-busy', 'true');
  els.railScan.hidden = false;
  setVitals('active', '会诊进行中');

  // 窄屏是整页滚动，提交后把视线带回问诊区
  if (isStacked()) {
    els.thread.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  let firstAgentSeen = false;
  let agentDoneCount = 0;
  let streamTurn = null;
  let streamBody = null;
  let streamBuffer = '';

  AGENT_IDS.forEach((id) => setAgentState(id, 'working', '等待调度'));
  els.readout.mode.textContent = '分诊中';
  logStep('已提交主诉，正在分诊并路由');

  try {
    const sid = await ensureSession();

    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        session_id: sid,
        enable_swarm: !els.ctxSingle.checked,
        context: readPatientContext(),
        patient_profile: els.profileSelect.value || '',
      }),
      signal: controller.signal,
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    if (!res.body) {
      throw new Error('服务未返回流式内容');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop();

      for (const chunk of chunks) {
        const line = chunk.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;

        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        if (event.type === 'triage') {
          renderTriage(askTurn, event);
        } else if (event.type === 'medication_safety') {
          renderMedicationSafety(askTurn, event);
        } else if (event.type === 'stage') {
          workingText.textContent = '正在调度专科科室…';
          els.readout.mode.textContent = '会诊分诊中';
        } else if (event.type === 'agent_started') {
          setAgentState(event.agent_id, 'working', '接诊中');
          logStep(`${event.name} 开始接诊`);
        } else if (event.type === 'agent_done') {
          setAgentState(event.agent_id, 'done', '已出具意见');
          firstAgentSeen = true;
          agentDoneCount += 1;
          workingText.textContent =
            agentDoneCount >= AGENT_IDS.length
              ? '各科室意见已齐，正在汇总会诊意见…'
              : `${event.name} 已出具意见 · 已完成 ${agentDoneCount} 个科室`;
          logStep(`${event.name} 已出具会诊意见`);
        } else if (event.type === 'token') {
          if (!streamTurn) {
            working.remove();
            clearInterval(elapsedTimer);
            const streamed = renderStreamingNote();
            streamTurn = streamed.turn;
            streamBody = streamed.body;
          }
          streamBuffer += event.delta || '';
          streamBody.textContent = streamBuffer;
          scrollThreadToBottom(!userPausedFollow);
        } else if (event.type === 'result') {
          finalPayload = event;
        } else if (event.type === 'error') {
          throw new Error(event.message);
        }
      }
    }

    if (streamTurn) {
      streamTurn.remove();
      streamTurn = null;
      streamBody = null;
    }
    working.remove();

    if (!finalPayload) {
      throw new Error('服务未返回结果');
    }

    // 兼容只拿到最终结果的客户端：结果里也带有 triage 字段
    if (finalPayload.triage) {
      renderTriage(askTurn, finalPayload.triage);
    }
    if (finalPayload.medication_safety) {
      renderMedicationSafety(askTurn, finalPayload.medication_safety);
    }

    const elapsed = (performance.now() - startedAt) / 1000;

    // 单科模式下收不到 agent_done，按返回结果补点亮
    if (!firstAgentSeen) {
      for (const a of finalPayload.agents_involved || []) {
        setAgentState(a.id, 'done', '已出具意见');
      }
    }
    AGENT_IDS.forEach((id) => {
      const el = agentEl(id);
      if (el && el.dataset.state === 'working') {
        setAgentState(id, 'idle', '未参与');
      }
    });

    els.readout.mode.textContent = finalPayload.mode === 'swarm' ? '多学科会诊' : '单科接诊';
    els.readout.agents.textContent = (finalPayload.agents_involved || []).length
      ? finalPayload.agents_involved.map((a) => a.name).join('、')
      : '—';
    const failedCount = finalPayload.subtasks_failed || 0;
    els.readout.tasks.textContent = failedCount
      ? `${finalPayload.subtasks_completed ?? 0}（${failedCount} 失败）`
      : String(finalPayload.subtasks_completed ?? 0);
    els.readout.time.textContent = `${(finalPayload.total_time || elapsed).toFixed(1)}s`;
    els.readout.usage.textContent = formatUsage(
      finalPayload.usage,
      finalPayload.llm_calls
    );
    els.readout.usage.title = usageDetail(
      finalPayload.usage,
      finalPayload.llm_calls
    );
    latestUsage = finalPayload.usage || null;
    updateCostDisplay();
    els.readout.session.textContent = shortSession(finalPayload.session_id || sid);

    logStep(finalPayload.mode === 'swarm' ? '协调科室已汇总各方意见' : '单科直接出具意见', false);
    if (failedCount) {
      logStep(`有 ${failedCount} 个会诊任务未完成`, false);
    }
    setVitals('idle', '系统就绪');

    renderAnswer(finalPayload, finalPayload.total_time || elapsed, question);

    if (historyOpen) {
      loadHistory(els.historySearch.value.trim());
      loadUsageTrend();
      loadUsageBudget();
      loadQualityInsights();
    }
  } catch (err) {
    if (streamTurn) {
      streamTurn.remove();
      streamTurn = null;
      streamBody = null;
    }
    working.remove();

    if (err.name === 'AbortError') {
      renderStopped({ question, askTurn });
      AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '已停止'));
      els.readout.mode.textContent = '已停止';
      logStep('用户停止了本次会诊', false);
      setVitals('idle', '已停止会诊');
    } else {
      renderError(err.message || String(err), { question, askTurn });
      AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '待命'));
      els.readout.mode.textContent = '中断';
      logStep(`中断：${err.message || err}`, false);
      setVitals('bad', '会诊中断');
    }
  } finally {
    clearInterval(elapsedTimer);
    els.thread.removeAttribute('aria-busy');
    busy = false;
    activeController = null;
    els.stop.hidden = true;
    els.send.disabled = false;
    els.railScan.hidden = true;
    els.input.focus();
  }
}

/* ---------- 系统状态检查 ---------- */

async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();

    pricing = data.pricing || null;
    updateCostDisplay();

    if (data.auth_required) {
      if (authGateOpen) return;

      const token = sessionStorage.getItem('medix.token');
      if (!token) {
        setVitals('warn', '需要访问令牌');
        showAuthGate();
        return;
      }

      const verify = await fetch('/api/auth/verify');
      if (!verify.ok) {
        sessionStorage.removeItem('medix.token');
        setVitals('warn', '需要访问令牌');
        showAuthGate('访问令牌无效或已失效，请重新输入。');
        return;
      }
    } else if (authGateOpen) {
      hideAuthGate();
    }

    if (!data.ok) {
      setVitals('bad', '服务异常');
      showBanner(`服务初始化失败：${escapeHtml(data.startup_error || '未知错误')}`);
      return;
    }

    if (!data.llm_configured) {
      setVitals('warn', '未配置 API Key');
      showBanner(
        '尚未配置 LLM API Key，提交主诉会失败。请编辑项目根目录的 <code>.env</code>，' +
          '填写 <code>MEDIX_API_KEY</code>（火山方舟 / OpenAI 兼容接口），然后重启服务。'
      );
    } else if (data.knowledge_base.status === 'failed') {
      setVitals('warn', '知识库异常');
      showBanner(`知识库加载失败：${escapeHtml(data.knowledge_base.error)}。检索类问题会返回空结果。`);
    } else {
      hideBanner();
      if (busy) {
        // 会诊进行中，不要覆盖波形状态
      } else if (data.knowledge_base.status === 'ready') {
        setVitals('idle', '系统就绪');
      } else {
        setVitals('pending', '知识库加载中');
      }
    }

    els.readout.kb.textContent =
      data.knowledge_base.status === 'ready'
        ? '就绪'
        : data.knowledge_base.status === 'failed'
        ? '失败'
        : '加载中';

    if (!els.readout.session.textContent || els.readout.session.textContent === '—') {
      els.readout.session.textContent = shortSession(sessionId);
    }
  } catch (err) {
    setVitals('bad', '无法连接服务');
    showBanner(`无法连接后端服务：${escapeHtml(err.message)}`);
  }
}

function showBanner(html) {
  els.banner.innerHTML = html;
  els.banner.hidden = false;
}

function hideBanner() {
  els.banner.hidden = true;
  els.banner.textContent = '';
}

/* ---------- 结构化补充信息 ---------- */

const CONTEXT_STORAGE_KEY = 'medix.context';
const SINGLE_MODE_KEY = 'medix.singleMode';
const PROFILES_STORAGE_KEY = 'medix.profiles';
const PROFILE_STORAGE_KEY = 'medix.activeProfile';
let profilesCache = {};
let activeProfileName = '';

function readPatientContext() {
  const context = {};
  const age = els.ctxAge.value.trim();
  if (age) context.age = age;
  if (els.ctxSex.value) context.sex = els.ctxSex.value;

  const duration = els.ctxDuration.value.trim();
  if (duration) context.duration = duration;
  const history = els.ctxHistory.value.trim();
  if (history) context.history = history;
  const medications = els.ctxMedications.value.trim();
  if (medications) context.medications = medications;
  const allergies = els.ctxAllergies.value.trim();
  if (allergies) context.allergies = allergies;

  return context;
}

function updateContextSummary() {
  const context = readPatientContext();
  const parts = [];
  if (context.age) parts.push(`${context.age} 岁`);
  if (context.sex) parts.push(context.sex);
  if (context.duration) parts.push(context.duration);
  if (context.history) parts.push('有既往病史');
  if (context.medications) parts.push('有用药');
  if (context.allergies) parts.push('有过敏史');

  els.contextSummary.textContent = parts.join(' · ');
  els.contextSummary.hidden = parts.length === 0;
  els.contextToggle.classList.toggle('has-context', parts.length > 0);

  try {
    if (parts.length) {
      sessionStorage.setItem(CONTEXT_STORAGE_KEY, JSON.stringify(context));
    } else {
      sessionStorage.removeItem(CONTEXT_STORAGE_KEY);
    }
  } catch {
    // 隐私模式下 sessionStorage 不可用时忽略
  }
}

function applyPatientContext(context) {
  const data = context && typeof context === 'object' ? context : {};
  els.ctxAge.value = data.age || '';
  els.ctxSex.value = data.sex || '';
  els.ctxDuration.value = data.duration || '';
  els.ctxHistory.value = data.history || '';
  els.ctxMedications.value = data.medications || '';
  els.ctxAllergies.value = data.allergies || '';
  if (els.profileSelect) els.profileSelect.value = '';
  updateContextSummary();
}

function loadSavedContext() {
  try {
    const raw = sessionStorage.getItem(CONTEXT_STORAGE_KEY);
    if (raw) applyPatientContext(JSON.parse(raw));
    els.ctxSingle.checked = sessionStorage.getItem(SINGLE_MODE_KEY) === '1';
  } catch {
    // 忽略损坏的缓存
  }
}

function readProfiles() {
  return profilesCache;
}

function setProfiles(profiles) {
  profilesCache = {};
  for (const profile of profiles || []) {
    if (profile && profile.name) profilesCache[profile.name] = profile;
  }
}

async function fetchProfiles() {
  const res = await fetch('/api/profiles');
  if (!res.ok) throw new Error(`服务返回 ${res.status}`);
  const data = await res.json();
  setProfiles(data.profiles || []);
}

/**
 * 把旧版本存在 localStorage 的档案迁移到服务端。
 * 同名档案已在服务端时跳过；全部成功后删除本地副本，失败则保留以便下次重试。
 */
async function migrateLegacyProfiles() {
  let legacy;
  try {
    const raw = localStorage.getItem(PROFILES_STORAGE_KEY);
    legacy = raw ? JSON.parse(raw) : null;
  } catch {
    legacy = null;
  }
  if (!legacy || typeof legacy !== 'object' || Array.isArray(legacy)) return;

  let migrated = 0;
  let failed = 0;
  for (const [name, entry] of Object.entries(legacy)) {
    if (!name || profilesCache[name]) continue;
    const context = entry && typeof entry === 'object' && entry.context
      ? entry.context
      : entry;
    try {
      const res = await fetch('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, context }),
      });
      if (res.ok) {
        migrated += 1;
      } else {
        failed += 1;
      }
    } catch {
      failed += 1;
    }
  }

  try {
    if (failed === 0) localStorage.removeItem(PROFILES_STORAGE_KEY);
  } catch {
    // 隐私模式下 localStorage 不可用时忽略
  }
  if (migrated) await fetchProfiles();
}

async function initProfiles() {
  try {
    await fetchProfiles();
    await migrateLegacyProfiles();
    refreshProfileOptions();
  } catch {
    // 服务暂不可用时不阻塞主界面；恢复后刷新页面即可重新加载
  }
}

function readStoredProfile() {
  try {
    return sessionStorage.getItem(PROFILE_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function setActiveProfile(name) {
  activeProfileName = profilesCache[name] ? name : '';
  if (els.profileSelect) els.profileSelect.value = activeProfileName;
  try {
    if (activeProfileName) {
      sessionStorage.setItem(PROFILE_STORAGE_KEY, activeProfileName);
    } else {
      sessionStorage.removeItem(PROFILE_STORAGE_KEY);
    }
  } catch {
    // 隐私模式下不可用时忽略
  }
}

async function fetchUsedProfileNames() {
  const names = new Set();
  try {
    const [namesRes, profilesRes] = await Promise.all([
      fetch('/api/profile-names'),
      fetch('/api/profiles'),
    ]);
    if (profilesRes.ok) {
      const data = await profilesRes.json();
      setProfiles(data.profiles || []);
    }
    if (namesRes.ok) {
      const data = await namesRes.json();
      for (const name of data.profiles || []) {
        if (typeof name === 'string' && name) names.add(name);
      }
    }
  } catch {
    // 档案名加载失败时退回档案列表
  }
  return names;
}

async function refreshHistoryProfileOptions() {
  if (!els.historyProfile) return;

  const names = await fetchUsedProfileNames();
  for (const name of Object.keys(readProfiles())) names.add(name);

  const current = historyProfile || els.historyProfile.value || '';
  els.historyProfile.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = '全部档案';
  els.historyProfile.appendChild(all);
  for (const name of [...names].sort()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = readProfiles()[name]
      ? name
      : `${name}（已删除）`;
    els.historyProfile.appendChild(option);
  }
  els.historyProfile.value = names.has(current) ? current : '';
  historyProfile = els.historyProfile.value;
}

function refreshProfileOptions(selected = undefined) {
  const profiles = readProfiles();
  const current = selected === undefined
    ? (els.profileSelect.value || activeProfileName || readStoredProfile())
    : selected;

  els.profileSelect.replaceChildren();
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '常用档案…';
  els.profileSelect.appendChild(placeholder);

  for (const name of Object.keys(profiles).sort()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    els.profileSelect.appendChild(option);
  }

  setActiveProfile(profiles[current] ? current : '');
  refreshHistoryProfileOptions();
}

async function savePatientProfile() {
  const context = readPatientContext();
  if (!Object.keys(context).length) {
    window.alert('请先填写补充信息，再保存为常用档案。');
    return;
  }

  const name = (window.prompt('档案名称（如：妈妈、孩子）') || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 20);
  if (!name) return;

  if (readProfiles()[name] && !window.confirm(`档案「${name}」已存在，是否覆盖？`)) {
    return;
  }

  try {
    const res = await fetch('/api/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, context }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    if (data.profile) profilesCache[data.profile.name] = data.profile;
    refreshProfileOptions(name);
  } catch (err) {
    window.alert(`保存档案失败：${err.message || err}`);
  }
}

async function renamePatientProfile() {
  const name = els.profileSelect.value;
  if (!name) {
    window.alert('请先选择一个常用档案。');
    return;
  }

  const nextName = (window.prompt('新的档案名称', name) || '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!nextName || nextName === name) return;

  try {
    const res = await fetch(
      `/api/profiles/${encodeURIComponent(name)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: nextName }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    await initProfiles();
    setActiveProfile(data.name);
    setVitals('idle', '档案已重命名');
  } catch (err) {
    window.alert(`重命名档案失败：${err.message || err}`);
  }
}

async function deletePatientProfile() {
  const name = els.profileSelect.value;
  if (!name) {
    window.alert('请先选择一个常用档案。');
    return;
  }
  if (!window.confirm(`删除常用档案「${name}」？`)) return;

  try {
    const res = await fetch(`/api/profiles/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    delete profilesCache[name];
    refreshProfileOptions('');
  } catch (err) {
    window.alert(`删除档案失败：${err.message || err}`);
  }
}

async function applySelectedProfile() {
  const name = els.profileSelect.value;
  const previous = activeProfileName;

  if (name === previous) {
    if (name && readProfiles()[name]) {
      applyPatientContext(readProfiles()[name].context || {});
      els.profileSelect.value = name;
    }
    return;
  }

  const hasTurns = els.thread.querySelectorAll('.turn').length > 0;
  if (sessionId && hasTurns) {
    const ok = window.confirm(
      '切换患者档案会开始新的会诊记录，是否继续？'
    );
    if (!ok) {
      els.profileSelect.value = previous;
      return;
    }
    const before = sessionId;
    await startNewSession();
    if (sessionId === before) {
      els.profileSelect.value = previous;
      return;
    }
  }

  setActiveProfile(name);
  if (name && readProfiles()[name]) {
    applyPatientContext(readProfiles()[name].context || {});
    els.profileSelect.value = name;
  }
}

/* ---------- 事件绑定 ---------- */

function autoGrow() {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, 180) + 'px';
}

const DRAFT_KEY = 'medix.draft';
let draftTimer = null;

function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      if (els.input.value.trim()) {
        sessionStorage.setItem(DRAFT_KEY, els.input.value);
      } else {
        sessionStorage.removeItem(DRAFT_KEY);
      }
    } catch {
      // 隐私模式下 sessionStorage 不可用时忽略
    }
  }, 200);
}

function clearDraft() {
  clearTimeout(draftTimer);
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // 忽略
  }
}

function loadDraft() {
  try {
    const draft = sessionStorage.getItem(DRAFT_KEY);
    if (draft) {
      els.input.value = draft;
      autoGrow();
    }
  } catch {
    // 忽略
  }
}

/* ---------- 语音输入（Web Speech API，不支持时隐藏按钮） ---------- */

let voiceRecognition = null;
let voiceListening = false;
let voicePrefix = '';
let voiceFinal = '';

function setVoiceListening(state) {
  voiceListening = state;
  if (!els.voiceToggle) return;
  els.voiceToggle.classList.toggle('listening', state);
  els.voiceToggle.setAttribute('aria-pressed', state ? 'true' : 'false');
  els.voiceToggle.title = state
    ? '停止语音输入'
    : '语音输入（多数浏览器需联网识别）';
}

function stopVoice() {
  if (voiceRecognition && voiceListening) {
    try {
      voiceRecognition.stop();
    } catch {
      // 已停止时忽略
    }
  }
  setVoiceListening(false);
}

function setupVoiceInput() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition || !els.voiceToggle) return;

  voiceRecognition = new SpeechRecognition();
  voiceRecognition.lang = 'zh-CN';
  voiceRecognition.continuous = false;
  voiceRecognition.interimResults = true;
  voiceRecognition.maxAlternatives = 1;

  voiceRecognition.addEventListener('start', () => {
    voicePrefix = els.input.value.trim();
    voiceFinal = '';
    setVoiceListening(true);
    setVitals('active', '正在听写…');
  });

  voiceRecognition.addEventListener('result', (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (result.isFinal) {
        voiceFinal += result[0].transcript;
      } else {
        interim += result[0].transcript;
      }
    }
    const spoken = `${voiceFinal}${interim}`.trim();
    if (!spoken) return;
    els.input.value =
      `${voicePrefix}${voicePrefix ? ' ' : ''}${spoken}`.slice(0, 2000);
    els.input.dispatchEvent(new Event('input', { bubbles: true }));
  });

  voiceRecognition.addEventListener('end', () => {
    setVoiceListening(false);
    if (voiceFinal.trim()) setVitals('idle', '语音已转成文字');
  });

  voiceRecognition.addEventListener('error', (event) => {
    setVoiceListening(false);
    const code = event.error || '';
    if (code === 'not-allowed' || code === 'service-not-allowed') {
      setVitals('warn', '麦克风不可用');
    } else if (code === 'no-speech') {
      setVitals('idle', '没有听到语音');
    } else if (code !== 'aborted') {
      setVitals('warn', '语音识别失败');
    }
  });

  els.voiceToggle.hidden = false;
}

function toggleVoice() {
  if (!voiceRecognition) return;
  if (voiceListening) {
    stopVoice();
    return;
  }
  try {
    voiceRecognition.start();
  } catch {
    // 重复 start 等异常时忽略
  }
}

els.input.addEventListener('input', () => {
  autoGrow();
  saveDraft();
});

els.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
});

els.composer.addEventListener('submit', (e) => {
  e.preventDefault();
  stopVoice();
  if (busy) {
    setVitals('active', '会诊进行中');
    return;
  }
  const q = els.input.value.trim();
  if (!q) return;
  els.input.value = '';
  autoGrow();
  clearDraft();
  send(q);
});

if (els.voiceToggle) {
  els.voiceToggle.addEventListener('click', toggleVoice);
}
setupVoiceInput();
refreshReminderBadge();
loadHomeGlance();
syncNotifyButton();

els.newSession.addEventListener('click', startNewSession);

els.themeToggle.addEventListener('click', () => {
  applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
});

els.stop.addEventListener('click', () => {
  if (activeController) activeController.abort();
});

els.historyToggle.addEventListener('click', () => {
  if (historyOpen) {
    closeHistory(true);
  } else {
    openHistory();
  }
});

els.historyClose.addEventListener('click', () => closeHistory(true));
els.historyBackdrop.addEventListener('click', () => closeHistory(true));

els.kbManage.addEventListener('click', openKb);
els.kbClose.addEventListener('click', () => closeKb(true));
els.kbBackdrop.addEventListener('click', () => closeKb(true));
els.kbUpload.addEventListener('click', () => uploadKbDocument());
els.kbRebuild.addEventListener('click', rebuildKb);
els.kbPreviewClose.addEventListener('click', closeKbPreview);
els.kbPreviewEdit.addEventListener('click', startKbPreviewEdit);
els.kbPreviewSave.addEventListener('click', saveKbPreviewEdit);
els.kbPreviewCancel.addEventListener('click', exitKbPreviewEdit);
els.kbSearchButton.addEventListener('click', searchKnowledgeBase);
els.kbSearchInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.isComposing) {
    event.preventDefault();
    searchKnowledgeBase();
  }
});

function isFileDrag(event) {
  const types = event.dataTransfer && event.dataTransfer.types;
  return types ? Array.from(types).includes('Files') : false;
}

['dragenter', 'dragover'].forEach((type) => {
  els.kbBody.addEventListener(type, (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    els.kbBody.classList.add('dragging');
  });
});

els.kbBody.addEventListener('dragleave', (event) => {
  if (!els.kbBody.contains(event.relatedTarget)) {
    els.kbBody.classList.remove('dragging');
  }
});

els.kbBody.addEventListener('drop', (event) => {
  event.preventDefault();
  els.kbBody.classList.remove('dragging');
  const files =
    event.dataTransfer &&
    event.dataTransfer.files
      ? Array.from(event.dataTransfer.files)
      : [];
  if (files.length) uploadKbDocument(files);
});

document.addEventListener('drop', (event) => {
  if (!els.kbBody.contains(event.target) && isFileDrag(event)) {
    event.preventDefault();
  }
});

els.diagnose.addEventListener('click', openDiagnose);
els.diagnoseClose.addEventListener('click', () => closeDiagnose(true));
els.diagnoseBackdrop.addEventListener('click', () => closeDiagnose(true));
els.diagRefresh.addEventListener('click', loadDiagnostics);
els.diagLlmTest.addEventListener('click', testModelConnection);
els.diagCopy.addEventListener('click', copyDiagnostics);
els.diagDownload.addEventListener('click', downloadDiagnostics);
els.diagAuditExport.addEventListener('click', exportAuditCsv);
els.diagPurge.addEventListener('click', purgeOldSessions);
els.auditAction.addEventListener('change', loadAuditLog);
els.auditFilterApply.addEventListener('click', loadAuditLog);
els.auditKeyword.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    loadAuditLog();
  }
});
els.auditStart.addEventListener('change', loadAuditLog);
els.auditEnd.addEventListener('change', loadAuditLog);

els.calcOpen.addEventListener('click', openCalc);
els.calcClose.addEventListener('click', () => closeCalc(true));
els.calcBackdrop.addEventListener('click', () => closeCalc(true));
els.calcTool.addEventListener('change', () => setCalcTool(els.calcTool.value));
els.calcFormBmi.addEventListener('submit', submitBmi);
els.calcFormEgfr.addEventListener('submit', submitEgfr);
els.calcFormChads.addEventListener('submit', submitChads);
els.calcFormCurb65.addEventListener('submit', submitCurb65);
els.calcFormChildPugh.addEventListener('submit', submitChildPugh);
els.calcFormPhq9.addEventListener('submit', submitPhq9);
els.calcFormAuditc.addEventListener('submit', submitAuditC);
els.calcFormMmrc.addEventListener('submit', submitMmrc);
els.calcFormGad7.addEventListener('submit', submitGad7);
els.calcFormHasBled.addEventListener('submit', submitHasBled);
els.calcFormGcs.addEventListener('submit', submitGcs);
els.calcFormWells.addEventListener('submit', submitWells);
els.calcFormWellsPe.addEventListener('submit', submitWellsPe);
els.calcFormInteractions.addEventListener('submit', submitInteractions);
els.calcFormQsofa.addEventListener('submit', submitQsofa);
els.calcFormFib4.addEventListener('submit', submitFib4);
els.calcFormMeld.addEventListener('submit', submitMeld);
els.calcFormStopBang.addEventListener('submit', submitStopBang);
els.calcFormPadua.addEventListener('submit', submitPadua);
els.calcFormAbcd2.addEventListener('submit', submitAbcd2);
els.calcFormMorse.addEventListener('submit', submitMorse);
els.calcSave.addEventListener('click', async () => {
  if (!currentCalcAction) return;
  const original = els.calcSave.textContent;
  els.calcSave.disabled = true;
  els.calcSave.textContent = '保存中…';
  try {
    await currentCalcAction.run();
    currentCalcAction = null;
    els.calcSave.hidden = true;
  } catch {
    els.calcSave.textContent = original;
  } finally {
    els.calcSave.disabled = false;
  }
});

els.metricsOpen.addEventListener('click', openMetrics);
els.metricsClose.addEventListener('click', () => closeMetrics(true));
els.metricsBackdrop.addEventListener('click', () => closeMetrics(true));
els.remindersOpen.addEventListener('click', openReminders);
els.remindersClose.addEventListener('click', () => closeReminders(true));
els.remindersBackdrop.addEventListener('click', () => closeReminders(true));
els.remindersNotify.addEventListener('click', enableReminderNotifications);
els.visitSummary.addEventListener('click', exportVisitSummary);
els.reminderAdd.addEventListener('click', addReminder);
els.remindersExport.addEventListener('click', exportRemindersCsv);
els.remindersExportIcs.addEventListener('click', exportRemindersIcs);
els.remindersImport.addEventListener('click', () => {
  els.remindersImportFile.click();
});
els.remindersImportFile.addEventListener('change', () => {
  const file = els.remindersImportFile.files
    && els.remindersImportFile.files[0];
  if (file) importRemindersCsv(file);
});
els.reminderPlanAdd.addEventListener('click', addReminderSeries);
els.remindersCalendarToggle.addEventListener('click', () => {
  reminderCalendarOpen = !reminderCalendarOpen;
  if (!reminderCalendarOpen) reminderCalendarDay = '';
  refreshReminderViews();
});
els.remindersCalendarPrev.addEventListener('click', () => {
  shiftReminderCalendarMonth(-1);
});
els.remindersCalendarNext.addEventListener('click', () => {
  shiftReminderCalendarMonth(1);
});
els.remindersCalendarClear.addEventListener('click', () => {
  reminderCalendarDay = '';
  refreshReminderViews();
});
els.remindersSearch.addEventListener('input', refreshReminderViews);
els.remindersOverdueOnly.addEventListener(
  'change', refreshReminderViews
);
els.remindersCalendarToday.addEventListener('click', () => {
  const now = new Date();
  reminderCalendarMonth = new Date(
    now.getFullYear(), now.getMonth(), 1
  );
  reminderCalendarDay = '';
  refreshReminderViews();
});
els.reminderProfile.addEventListener('change', () => {
  reminderProfileTouched = true;
});
els.reminderRepeat.addEventListener('input', syncReminderRepeatFields);
els.remindersShowDone.addEventListener('change', loadReminders);
els.remindersProfile.addEventListener('change', loadReminders);
els.reminderCancel.addEventListener('click', () => {
  resetReminderForm();
  setRemindersStatus('已取消编辑。');
});
els.reminderTitle.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addReminder();
  }
});
els.reminderNote.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addReminder();
  }
});
els.metricType.addEventListener('change', () => {
  setMetricType(els.metricType.value);
  refreshMetrics();
});
els.metricProfile.addEventListener('change', refreshMetrics);
els.metricRange.addEventListener('change', refreshMetrics);
els.metricTargetEdit.addEventListener('click', startEditMetricTarget);
els.metricTargetSave.addEventListener('click', saveMetricTarget);
els.metricTargetCancel.addEventListener('click', updateMetricTargetDisplay);
els.metricTargetClear.addEventListener('click', clearMetricTarget);
els.metricTargetValue1.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    saveMetricTarget();
  }
});
els.metricTargetValue2.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    saveMetricTarget();
  }
});
els.metricAdd.addEventListener('click', addMetricRecord);
els.homeGlanceReminders.addEventListener('click', () => {
  openReminders();
});
els.homeGlanceMetrics.addEventListener('click', () => {
  openMetrics();
});
els.metricsMore.addEventListener('click', async () => {
  const original = els.metricsMore.textContent;
  els.metricsMore.disabled = true;
  els.metricsMore.textContent = '加载中…';
  try {
    await loadMetrics(true);
  } finally {
    els.metricsMore.disabled = false;
    els.metricsMore.textContent = original;
  }
});
els.metricCancel.addEventListener('click', cancelMetricEdit);
els.metricExport.addEventListener('click', exportMetricsCsv);
els.metricImport.addEventListener('click', () => els.metricImportFile.click());
els.metricImportFile.addEventListener('change', () => {
  const file = els.metricImportFile.files && els.metricImportFile.files[0];
  if (file) importMetricsCsv(file);
});

els.authForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = els.authToken.value.trim();
  if (!token) return;

  sessionStorage.setItem('medix.token', token);
  try {
    const verify = await fetch('/api/auth/verify');
    if (verify.ok) {
      hideAuthGate();
      window.location.reload();
      return;
    }
  } catch {
    // 网络异常按令牌无效处理，让用户重试
  }

  sessionStorage.removeItem('medix.token');
  showAuthGate('令牌不正确，请重试。');
  els.authToken.select();
});

els.authToken.addEventListener('input', () => {
  els.authError.hidden = true;
});

els.contextToggle.addEventListener('click', () => {
  const opening = els.contextPanel.hidden;
  els.contextPanel.hidden = !opening;
  els.contextToggle.setAttribute('aria-expanded', String(opening));
  if (opening) els.ctxAge.focus();
});

els.contextPanel.addEventListener('input', updateContextSummary);
els.contextPanel.addEventListener('change', updateContextSummary);

els.contextClear.addEventListener('click', () => {
  applyPatientContext({});
  els.profileSelect.value = '';
  els.ctxAge.focus();
});

els.profileSelect.addEventListener('change', applySelectedProfile);
els.profileSave.addEventListener('click', savePatientProfile);
els.profileRename.addEventListener('click', renamePatientProfile);
els.profileDelete.addEventListener('click', deletePatientProfile);

els.ctxSingle.addEventListener('change', () => {
  try {
    if (els.ctxSingle.checked) {
      sessionStorage.setItem(SINGLE_MODE_KEY, '1');
    } else {
      sessionStorage.removeItem(SINGLE_MODE_KEY);
    }
  } catch {
    // 忽略
  }
});

let historySearchTimer = null;
els.historySearch.addEventListener('input', () => {
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(() => {
    loadHistory(els.historySearch.value.trim());
  }, 220);
});

els.historyMore.addEventListener('click', () => {
  if (historyLoading) return;
  loadHistory(historyQuery, { append: true });
});

els.historyExport.addEventListener('click', exportHistoryBackup);
els.historyExportCsv.addEventListener('click', exportHistoryCsv);
els.historyFeedbackExport.addEventListener('click', exportFeedbackCsv);
els.usageExport.addEventListener('click', exportUsageCsv);
els.historyImport.addEventListener('click', () => els.historyImportFile.click());
els.historyImportFile.addEventListener('change', importHistoryBackup);

els.historyFavoriteFilter.addEventListener('click', () => {
  favoritesOnly = !favoritesOnly;
  els.historyFavoriteFilter.setAttribute('aria-pressed', String(favoritesOnly));
  els.historyFavoriteFilter.textContent = favoritesOnly ? '★' : '☆';
  loadHistory(els.historySearch.value.trim());
  loadUsageTrend();
});

els.historyArchiveFilter.addEventListener('click', () => {
  archivedOnly = !archivedOnly;
  els.historyArchiveFilter.setAttribute('aria-pressed', String(archivedOnly));
  loadHistory(els.historySearch.value.trim());
});

els.historyRange.addEventListener('change', () => {
  historyDays = Number(els.historyRange.value) || 0;
  loadHistory(els.historySearch.value.trim());
});

els.historyTag.addEventListener('change', () => {
  historyTag = els.historyTag.value;
  loadHistory(els.historySearch.value.trim());
});

if (els.historyProfile) {
  els.historyProfile.addEventListener('change', () => {
    historyProfile = els.historyProfile.value;
    loadHistory(els.historySearch.value.trim());
    loadUsageTrend();
    loadQualityInsights();
    loadRecentFeedback();
    loadHistoryTags();
  });
}

els.historyBulkToggle.addEventListener('click', () => {
  setBulkMode(!bulkMode);
});
els.historyBulkAll.addEventListener('click', toggleBulkSelectAll);
els.historyBulkArchive.addEventListener('click', () => bulkArchive(true));
els.historyBulkUnarchive.addEventListener('click', () => bulkArchive(false));
els.historyBulkTags.addEventListener('click', bulkTagSessions);
els.historyBulkDelete.addEventListener('click', bulkDelete);
els.historyBulkCancel.addEventListener('click', () => setBulkMode(false));
els.historyBulkCompare.addEventListener('click', () => {
  openCompare(Array.from(selectedSessions));
});
els.historyBulkExport.addEventListener('click', bulkExportMarkdown);
els.compareClose.addEventListener('click', () => closeCompare(true));
els.compareBackdrop.addEventListener('click', () => closeCompare(true));

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (authGateOpen) {
      els.authToken.focus();
      return;
    }
    if (!historyOpen) {
      openHistory();
    } else {
      els.historySearch.focus();
    }
    return;
  }

  if (e.key === 'Escape') {
    if (historyOpen) closeHistory(true);
    if (kbOpen) closeKb(true);
    if (diagOpen) closeDiagnose(true);
    if (calcOpen) closeCalc(true);
    if (metricsOpen) closeMetrics(true);
    if (remindersOpen) closeReminders(true);
    if (compareOpen) closeCompare(true);
    return;
  }

  if (e.key !== 'Tab') return;
  const drawer = activeDrawer();
  if (!drawer) return;

  const focusable = Array.from(drawer.querySelectorAll(FOCUSABLE_SELECTOR))
    .filter((el) => el.getClientRects().length > 0);
  if (!focusable.length) return;

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const current = document.activeElement;

  if (e.shiftKey) {
    if (current === first || !drawer.contains(current)) {
      e.preventDefault();
      last.focus();
    }
  } else if (current === last || !drawer.contains(current)) {
    e.preventDefault();
    first.focus();
  }
});

els.skipLink.addEventListener('click', (event) => {
  event.preventDefault();
  els.input.focus();
});

els.jumpLatest.addEventListener('click', () => {
  userPausedFollow = false;
  scrollThreadToBottom(true);
});
els.thread.addEventListener('scroll', () => {
  if (isNearBottom()) {
    els.jumpLatest.hidden = true;
    userPausedFollow = false;
  }
});
els.thread.addEventListener('wheel', () => {
  userPausedFollow = true;
}, { passive: true });
els.thread.addEventListener('touchmove', () => {
  userPausedFollow = true;
}, { passive: true });

/* ---------- 自定义常用问题（保存在浏览器本地） ---------- */

const CUSTOM_STARTERS_KEY = 'medix.customStarters';
const MAX_CUSTOM_STARTERS = 8;
let customStarters = [];

function loadCustomStartersFromStorage() {
  try {
    const raw = JSON.parse(
      localStorage.getItem(CUSTOM_STARTERS_KEY) || '[]'
    );
    customStarters = Array.isArray(raw)
      ? raw
        .filter((item) => typeof item === 'string' && item.trim())
        .map((item) => item.trim().slice(0, 60))
        .slice(0, MAX_CUSTOM_STARTERS)
      : [];
  } catch {
    customStarters = [];
  }
}

function persistCustomStarters() {
  try {
    localStorage.setItem(
      CUSTOM_STARTERS_KEY, JSON.stringify(customStarters)
    );
  } catch {
    // 隐私模式下不可用时忽略
  }
}

function renderCustomStarters() {
  if (!els.starters) return;
  els.starters.querySelectorAll('.starter-wrap').forEach((el) => el.remove());
  customStarters.forEach((text, index) => {
    const wrap = document.createElement('span');
    wrap.className = 'starter-wrap';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'starter';
    btn.textContent = text;
    btn.dataset.q = text;

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'starter-remove';
    del.textContent = '×';
    del.title = '删除这条常用问题';
    del.setAttribute('aria-label', `删除常用问题：${text}`);
    del.addEventListener('click', () => {
      customStarters.splice(index, 1);
      persistCustomStarters();
      renderCustomStarters();
    });

    wrap.append(btn, del);
    els.starters.appendChild(wrap);
  });
}

function addCustomStarter() {
  let text = (els.input.value || '').trim();
  if (!text) {
    text = (window.prompt('输入要保存的常用问题') || '').trim();
  }
  if (!text) return;
  text = text.slice(0, 60);

  if (customStarters.includes(text)) {
    setVitals('idle', '这条问题已在常用中');
    return;
  }
  if (customStarters.length >= MAX_CUSTOM_STARTERS) {
    setVitals('warn', `最多保存 ${MAX_CUSTOM_STARTERS} 条常用问题`);
    return;
  }
  customStarters.push(text);
  persistCustomStarters();
  renderCustomStarters();
  setVitals('idle', '已添加常用问题');
}

els.starters.addEventListener('click', (e) => {
  const btn = e.target.closest('.starter');
  if (!btn) return;
  els.empty.hidden = true;
  send(btn.dataset.q);
});
els.starterAdd.addEventListener('click', addCustomStarter);
loadCustomStartersFromStorage();
renderCustomStarters();

resetRail();
initTheme();
renderEcg();
loadSavedContext();
loadDraft();
initProfiles().finally(() => {
  restoreCurrentSession();
  handleLaunchAction();
});
checkStatus();
setInterval(checkStatus, 20000);
els.input.focus();

// 可安装 PWA：只缓存静态外壳，API 数据不进入 Service Worker 缓存
let deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  els.pwaInstall.hidden = false;
});

window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  els.pwaInstall.hidden = true;
});

function updateConnectivity() {
  if (navigator.onLine === false) {
    setVitals('warn', '离线');
    showBanner(
      '当前网络不可用：会诊与历史数据需要本地后端服务，恢复连接后会自动重试。'
    );
  } else {
    hideBanner();
    checkStatus();
  }
}

window.addEventListener('offline', updateConnectivity);
window.addEventListener('online', updateConnectivity);
if (navigator.onLine === false) updateConnectivity();

els.pwaInstall.addEventListener('click', async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  try {
    await deferredInstallPrompt.userChoice;
  } catch {
    // 用户取消或浏览器不支持时忽略
  }
  deferredInstallPrompt = null;
  els.pwaInstall.hidden = true;
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 非 HTTPS / 浏览器不支持时静默降级
    });
  });
}

// 屏宽变化后重新排布波形（两份波形共用同一段路径，拼接处才能无缝衔接）
let ecgResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(ecgResizeTimer);
  ecgResizeTimer = setTimeout(renderEcg, 180);
});
