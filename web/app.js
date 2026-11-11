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
  ctxBloodType: document.getElementById('ctx-blood-type'),
  ctxEmergency1: document.getElementById('ctx-emergency-1'),
  ctxEmergency2: document.getElementById('ctx-emergency-2'),
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
  historyBackupNow: document.getElementById('history-backup-now'),
  historyRestore: document.getElementById('history-restore'),
  historyManageBackups: document.getElementById('history-manage-backups'),
  historyRange: document.getElementById('history-range'),
  historyTag: document.getElementById('history-tag'),
  historyRiskFilter: document.getElementById('history-risk-filter'),
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
  calcEgfrLab: document.getElementById('calc-egfr-lab'),
  calcFib4Lab: document.getElementById('calc-fib4-lab'),
  calcMeldLab: document.getElementById('calc-meld-lab'),
  calcApriAst: document.getElementById('calc-apri-ast'),
  calcApriPlatelets: document.getElementById('calc-apri-platelets'),
  calcApriUln: document.getElementById('calc-apri-uln'),
  calcApriLab: document.getElementById('calc-apri-lab'),
  calcEagHba1c: document.getElementById('calc-eag-hba1c'),
  calcEagLab: document.getElementById('calc-eag-lab'),
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
  calcCurbLab: document.getElementById('calc-curb-lab'),
  calcFormChildPugh: document.getElementById('calc-form-child-pugh'),
  calcChildBilirubin: document.getElementById('calc-child-bilirubin'),
  calcChildAlbumin: document.getElementById('calc-child-albumin'),
  calcChildInr: document.getElementById('calc-child-inr'),
  calcChildAscites: document.getElementById('calc-child-ascites'),
  calcChildEncephalopathy: document.getElementById('calc-child-encephalopathy'),
  calcChildPughLab: document.getElementById('calc-child-pugh-lab'),
  calcFormPhq9: document.getElementById('calc-form-phq9'),
  calcPhq9Questions: document.getElementById('calc-phq9-questions'),
  calcFormGad7: document.getElementById('calc-form-gad7'),
  calcFormAuditc: document.getElementById('calc-form-auditc'),
  calcFormMmrc: document.getElementById('calc-form-mmrc'),
  calcFormCat: document.getElementById('calc-form-cat'),
  calcFormEss: document.getElementById('calc-form-ess'),
  calcEssQuestions: document.getElementById('calc-ess-questions'),
  calcCatQuestions: document.getElementById('calc-cat-questions'),
  calcMmrcGrade: document.getElementById('calc-mmrc-grade'),
  calcAuditCQ1: document.getElementById('calc-auditc-q1'),
  calcAuditCQ2: document.getElementById('calc-auditc-q2'),
  calcAuditCQ3: document.getElementById('calc-auditc-q3'),
  calcAuditCSex: document.getElementById('calc-auditc-sex'),
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
  calcFormApri: document.getElementById('calc-form-apri'),
  calcFormEag: document.getElementById('calc-form-eag'),
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
  healthSearchToggle: document.getElementById('health-search-toggle'),
  healthSearchInput: document.getElementById('health-search-input'),
  healthSearchClear: document.getElementById('health-search-clear'),
  healthSearchStatus: document.getElementById('health-search-status'),
  healthSearchResults: document.getElementById('health-search-results'),
  insightSection: document.getElementById('insight-section'),
  insightSummary: document.getElementById('insight-summary'),
  insightList: document.getElementById('insight-list'),
  insightEmpty: document.getElementById('insight-empty'),
  qualitySummary: document.getElementById('quality-summary'),
  qualityList: document.getElementById('quality-list'),
  qualityEmpty: document.getElementById('quality-empty'),
  bpMonitoringSummary: document.getElementById('bp-monitoring-summary'),
  bpMonitoringStart: document.getElementById('bp-monitoring-start'),
  bpMonitoringDays: document.getElementById('bp-monitoring-days'),
  bpMonitoringCopy: document.getElementById('bp-monitoring-copy'),
  bpMonitoringBody: document.getElementById('bp-monitoring-body'),
  bpMonitoringEmpty: document.getElementById('bp-monitoring-empty'),
  bpMonitoringStatus: document.getElementById('bp-monitoring-status'),
  attachmentsSummary: document.getElementById('attachments-summary'),
  attachmentKind: document.getElementById('attachment-kind'),
  attachmentRefWrap: document.getElementById('attachment-ref-wrap'),
  attachmentRef: document.getElementById('attachment-ref'),
  attachmentFile: document.getElementById('attachment-file'),
  attachmentDropzone: document.getElementById('attachment-dropzone'),
  attachmentNote: document.getElementById('attachment-note'),
  attachmentUpload: document.getElementById('attachment-upload'),
  attachmentStatus: document.getElementById('attachment-status'),
  attachmentIntegrityCheck: document.getElementById('attachment-integrity-check'),
  attachmentExport: document.getElementById('attachment-export'),
  attachmentCleanup: document.getElementById('attachment-cleanup'),
  attachmentIntegrityStatus: document.getElementById('attachment-integrity-status'),
  attachmentFilterKind: document.getElementById('attachment-filter-kind'),
  attachmentFilterSort: document.getElementById('attachment-filter-sort'),
  attachmentFilterLinked: document.getElementById(
    'attachment-filter-linked'
  ),
  attachmentFilterQ: document.getElementById('attachment-filter-q'),
  attachmentFilterStart: document.getElementById(
    'attachment-filter-start'
  ),
  attachmentFilterEnd: document.getElementById('attachment-filter-end'),
  attachmentRefFilter: document.getElementById('attachment-ref-filter'),
  attachmentRefFilterLabel: document.getElementById(
    'attachment-ref-filter-label'
  ),
  attachmentRefFilterClear: document.getElementById(
    'attachment-ref-filter-clear'
  ),
  attachmentSelectAll: document.getElementById('attachment-select-all'),
  attachmentLinkSelected: document.getElementById(
    'attachment-link-selected'
  ),
  attachmentLinkBar: document.getElementById('attachment-link-bar'),
  attachmentLinkKind: document.getElementById('attachment-link-kind'),
  attachmentLinkRef: document.getElementById('attachment-link-ref'),
  attachmentLinkApply: document.getElementById('attachment-link-apply'),
  attachmentLinkUnlink: document.getElementById('attachment-link-unlink'),
  attachmentLinkCancel: document.getElementById('attachment-link-cancel'),
  attachmentExportSelected: document.getElementById(
    'attachment-export-selected'
  ),
  attachmentDeleteSelected: document.getElementById(
    'attachment-delete-selected'
  ),
  attachmentList: document.getElementById('attachment-list'),
  attachmentMore: document.getElementById('attachment-more'),
  attachmentEmpty: document.getElementById('attachment-empty'),
  attachmentPreview: document.getElementById('attachment-preview'),
  attachmentPreviewTitle: document.getElementById(
    'attachment-preview-title'
  ),
  attachmentPreviewBody: document.getElementById('attachment-preview-body'),
  attachmentPreviewClose: document.getElementById(
    'attachment-preview-close'
  ),
  attachmentPreviewDownload: document.getElementById(
    'attachment-preview-download'
  ),
  quickEntryInput: document.getElementById('quick-entry-input'),
  quickEntryParse: document.getElementById('quick-entry-parse'),
  quickEntrySave: document.getElementById('quick-entry-save'),
  quickEntryCancel: document.getElementById('quick-entry-cancel'),
  quickEntryUndo: document.getElementById('quick-entry-undo'),
  quickEntryPreview: document.getElementById('quick-entry-preview'),
  quickEntryStatus: document.getElementById('quick-entry-status'),
  healthDossierExport: document.getElementById('health-dossier-export'),
  emergencyCard: document.getElementById('emergency-card'),
  emergencyCardStatus: document.getElementById('emergency-card-status'),
  remindersOpen: document.getElementById('reminders-open'),
  remindersCount: document.getElementById('reminders-count'),
  visitSummary: document.getElementById('visit-summary'),
  remindersNotify: document.getElementById('reminders-notify'),
  remindersClearDone: document.getElementById('reminders-clear-done'),
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
  reminderDueQuick: document.getElementById('reminder-due-quick'),
  reminderUntilWrap: document.getElementById('reminder-until-wrap'),
  reminderAdd: document.getElementById('reminder-add'),
  remindersExport: document.getElementById('reminders-export'),
  remindersExportIcs: document.getElementById('reminders-export-ics'),
  remindersImport: document.getElementById('reminders-import'),
  remindersTemplate: document.getElementById('reminders-template'),
  remindersCompleteDue: document.getElementById('reminders-complete-due'),
  remindersImportFile: document.getElementById('reminders-import-file'),
  reminderPlanInterval: document.getElementById('reminder-plan-interval'),
  reminderPlanUnit: document.getElementById('reminder-plan-unit'),
  reminderPlanCount: document.getElementById('reminder-plan-count'),
  reminderPlanAdd: document.getElementById('reminder-plan-add'),
  reminderCancel: document.getElementById('reminder-cancel'),
  remindersStatus: document.getElementById('reminders-status'),
  remindersDueHint: document.getElementById('reminders-due-hint'),
  reminderStats: document.getElementById('reminder-stats'),
  reminderStatsBar: document.getElementById('reminder-stats-bar'),
  reminderStatsRange: document.getElementById('reminder-stats-range'),
  remindersProfile: document.getElementById('reminders-profile'),
  remindersShowDone: document.getElementById('reminders-show-done'),
  remindersOverdueOnly: document.getElementById('reminders-overdue-only'),
  remindersCalendarToday: document.getElementById('reminders-calendar-today'),
  remindersSearch: document.getElementById('reminders-search'),
  homeGlance: document.getElementById('home-glance'),
  homeGlanceReminders: document.getElementById('home-glance-reminders'),
  homeGlanceToday: document.getElementById('home-glance-today'),
  homeGlanceMetrics: document.getElementById('home-glance-metrics'),
  homeGlanceSymptoms: document.getElementById('home-glance-symptoms'),
  homeGlanceMedications: document.getElementById('home-glance-medications'),
  homeGlanceSummary: document.getElementById('home-glance-summary'),
  homeGlanceInsight: document.getElementById('home-glance-insight'),
  homeGlanceBackup: document.getElementById('home-glance-backup'),
  familyOverview: document.getElementById('family-overview'),
  familyOverviewList: document.getElementById('family-overview-list'),
  familyOverviewStatus: document.getElementById('family-overview-status'),
  familyOverviewExport: document.getElementById('family-overview-export'),
  familyOverviewCopy: document.getElementById('family-overview-copy'),
  todayMeds: document.getElementById('today-meds'),
  todayMedsSummary: document.getElementById('today-meds-summary'),
  todayMedsList: document.getElementById('today-meds-list'),
  todayMedsStatus: document.getElementById('today-meds-status'),
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
  metricTargetHint: document.getElementById('metric-target-hint'),
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
  metricContextWrap: document.getElementById('metric-context-wrap'),
  metricContext: document.getElementById('metric-context'),
  metricUnit: document.getElementById('metric-unit'),
  metricValue: document.getElementById('metric-value'),
  metricTime: document.getElementById('metric-time'),
  metricNote: document.getElementById('metric-note'),
  metricAdd: document.getElementById('metric-add'),
  metricCancel: document.getElementById('metric-cancel'),
  metricExport: document.getElementById('metric-export'),
  metricImport: document.getElementById('metric-import'),
  metricTemplate: document.getElementById('metric-template'),
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
  metricSearch: document.getElementById('metric-search'),
  symptomSection: document.getElementById('symptom-section'),
  symptomText: document.getElementById('symptom-text'),
  symptomSeverity: document.getElementById('symptom-severity'),
  symptomTime: document.getElementById('symptom-time'),
  symptomNote: document.getElementById('symptom-note'),
  symptomSave: document.getElementById('symptom-save'),
  symptomExport: document.getElementById('symptom-export'),
  symptomImport: document.getElementById('symptom-import'),
  symptomTemplate: document.getElementById('symptom-template'),
  symptomImportFile: document.getElementById('symptom-import-file'),
  symptomCancel: document.getElementById('symptom-cancel'),
  symptomStatus: document.getElementById('symptom-status'),
  symptomList: document.getElementById('symptom-list'),
  symptomEmpty: document.getElementById('symptom-empty'),
  symptomSearch: document.getElementById('symptom-search'),
  symptomQuick: document.getElementById('symptom-quick'),
  symptomSummary: document.getElementById('symptom-summary'),
  symptomTrend: document.getElementById('symptom-trend'),
  symptomTrendChart: document.getElementById('symptom-trend-chart'),
  vaccinationSection: document.getElementById('vaccination-section'),
  vaccinationName: document.getElementById('vaccination-name'),
  vaccinationDose: document.getElementById('vaccination-dose'),
  vaccinationDate: document.getElementById('vaccination-date'),
  vaccinationNextDue: document.getElementById('vaccination-next-due'),
  vaccinationNote: document.getElementById('vaccination-note'),
  vaccinationSave: document.getElementById('vaccination-save'),
  vaccinationExport: document.getElementById('vaccination-export'),
  vaccinationCancel: document.getElementById('vaccination-cancel'),
  vaccinationStatus: document.getElementById('vaccination-status'),
  vaccinationList: document.getElementById('vaccination-list'),
  vaccinationEmpty: document.getElementById('vaccination-empty'),
  vaccinationSearch: document.getElementById('vaccination-search'),
  vaccinationSummary: document.getElementById('vaccination-summary'),
  appointmentSection: document.getElementById('appointment-section'),
  appointmentTitle: document.getElementById('appointment-title'),
  appointmentDepartment: document.getElementById('appointment-department'),
  appointmentDoctor: document.getElementById('appointment-doctor'),
  appointmentLocation: document.getElementById('appointment-location'),
  appointmentTime: document.getElementById('appointment-time'),
  appointmentStatusSelect: document.getElementById('appointment-status'),
  appointmentNote: document.getElementById('appointment-note'),
  appointmentSave: document.getElementById('appointment-save'),
  appointmentExport: document.getElementById('appointment-export'),
  appointmentCancel: document.getElementById('appointment-cancel'),
  appointmentStatusText: document.getElementById('appointment-status-text'),
  appointmentList: document.getElementById('appointment-list'),
  appointmentEmpty: document.getElementById('appointment-empty'),
  appointmentSearch: document.getElementById('appointment-search'),
  appointmentSummary: document.getElementById('appointment-summary'),
  labSection: document.getElementById('lab-section'),
  labItem: document.getElementById('lab-item'),
  labPanel: document.getElementById('lab-panel'),
  labValue: document.getElementById('lab-value'),
  labUnit: document.getElementById('lab-unit'),
  labRefLow: document.getElementById('lab-ref-low'),
  labRefHigh: document.getElementById('lab-ref-high'),
  labTime: document.getElementById('lab-time'),
  labName: document.getElementById('lab-name'),
  labNote: document.getElementById('lab-note'),
  labSave: document.getElementById('lab-save'),
  labExport: document.getElementById('lab-export'),
  labImport: document.getElementById('lab-import'),
  labTemplate: document.getElementById('lab-template'),
  labImportFile: document.getElementById('lab-import-file'),
  labCancel: document.getElementById('lab-cancel'),
  labStatus: document.getElementById('lab-status'),
  labList: document.getElementById('lab-list'),
  labEmpty: document.getElementById('lab-empty'),
  labSearch: document.getElementById('lab-search'),
  labSummary: document.getElementById('lab-summary'),
  allergySection: document.getElementById('allergy-section'),
  allergySubstance: document.getElementById('allergy-substance'),
  allergyReaction: document.getElementById('allergy-reaction'),
  allergySeverity: document.getElementById('allergy-severity'),
  allergyKind: document.getElementById('allergy-kind'),
  allergyNotedAt: document.getElementById('allergy-noted-at'),
  allergyActive: document.getElementById('allergy-active'),
  allergyNote: document.getElementById('allergy-note'),
  allergySave: document.getElementById('allergy-save'),
  allergyExport: document.getElementById('allergy-export'),
  allergyImport: document.getElementById('allergy-import'),
  allergyTemplate: document.getElementById('allergy-template'),
  allergyImportFile: document.getElementById('allergy-import-file'),
  allergyCancel: document.getElementById('allergy-cancel'),
  allergyStatus: document.getElementById('allergy-status'),
  allergyList: document.getElementById('allergy-list'),
  allergyEmpty: document.getElementById('allergy-empty'),
  allergySearch: document.getElementById('allergy-search'),
  allergySummary: document.getElementById('allergy-summary'),
  careSection: document.getElementById('care-section'),
  careTitle: document.getElementById('care-title'),
  careInterval: document.getElementById('care-interval'),
  careNextDue: document.getElementById('care-next-due'),
  careLastDone: document.getElementById('care-last-done'),
  careActive: document.getElementById('care-active'),
  careNote: document.getElementById('care-note'),
  careSave: document.getElementById('care-save'),
  careExport: document.getElementById('care-export'),
  careCancel: document.getElementById('care-cancel'),
  careStatus: document.getElementById('care-status'),
  careList: document.getElementById('care-list'),
  careEmpty: document.getElementById('care-empty'),
  careSearch: document.getElementById('care-search'),
  careSummary: document.getElementById('care-summary'),
  visitSection: document.getElementById('visit-section'),
  visitAppointmentId: document.getElementById('visit-appointment-id'),
  visitDate: document.getElementById('visit-date'),
  visitDepartment: document.getElementById('visit-department'),
  visitDoctor: document.getElementById('visit-doctor'),
  visitFacility: document.getElementById('visit-facility'),
  visitDiagnosis: document.getElementById('visit-diagnosis'),
  visitAdvice: document.getElementById('visit-advice'),
  visitPrescription: document.getElementById('visit-prescription'),
  visitNextFollowup: document.getElementById('visit-next-followup'),
  visitNote: document.getElementById('visit-note'),
  visitSave: document.getElementById('visit-save'),
  visitExport: document.getElementById('visit-export'),
  visitCancel: document.getElementById('visit-cancel'),
  visitStatus: document.getElementById('visit-status'),
  visitList: document.getElementById('visit-list'),
  visitEmpty: document.getElementById('visit-empty'),
  visitSearch: document.getElementById('visit-search'),
  visitSummary: document.getElementById('visit-summary'),
  conditionSection: document.getElementById('condition-section'),
  conditionName: document.getElementById('condition-name'),
  conditionIcd10: document.getElementById('condition-icd10'),
  conditionDiagnosedAt: document.getElementById('condition-diagnosed-at'),
  conditionStatus: document.getElementById('condition-status'),
  conditionSeverity: document.getElementById('condition-severity'),
  conditionNote: document.getElementById('condition-note'),
  conditionSave: document.getElementById('condition-save'),
  conditionExport: document.getElementById('condition-export'),
  conditionCancel: document.getElementById('condition-cancel'),
  conditionStatusText: document.getElementById('condition-status-text'),
  conditionList: document.getElementById('condition-list'),
  conditionEmpty: document.getElementById('condition-empty'),
  conditionSearch: document.getElementById('condition-search'),
  conditionSummary: document.getElementById('condition-summary'),
  medicationSection: document.getElementById('medication-section'),
  medicationName: document.getElementById('medication-name'),
  medicationDose: document.getElementById('medication-dose'),
  medicationFrequency: document.getElementById('medication-frequency'),
  medicationStock: document.getElementById('medication-stock'),
  medicationStockUnit: document.getElementById('medication-stock-unit'),
  medicationLowStock: document.getElementById('medication-low-stock'),
  medicationExpiry: document.getElementById('medication-expiry'),
  medicationStartedAt: document.getElementById('medication-started-at'),
  medicationStoppedAt: document.getElementById('medication-stopped-at'),
  medicationStopReason: document.getElementById('medication-stop-reason'),
  medicationNote: document.getElementById('medication-note'),
  medicationActive: document.getElementById('medication-active'),
  medicationSave: document.getElementById('medication-save'),
  medicationCancel: document.getElementById('medication-cancel'),
  medicationStatus: document.getElementById('medication-status'),
  medicationList: document.getElementById('medication-list'),
  medicationLogHistory: document.getElementById('medication-log-history'),
  medicationLogList: document.getElementById('medication-log-list'),
  medicationLogEmpty: document.getElementById('medication-log-empty'),
  weeklySummarySection: document.getElementById('weekly-summary-section'),
  weeklySummaryRange: document.getElementById('weekly-summary-range'),
  weeklySummaryCopy: document.getElementById('weekly-summary-copy'),
  weeklySummaryExport: document.getElementById('weekly-summary-export'),
  weeklySummaryBody: document.getElementById('weekly-summary-body'),
  visitPrepDays: document.getElementById('visit-prep-days'),
  visitPrepGenerate: document.getElementById('visit-prep-generate'),
  visitPrepCopy: document.getElementById('visit-prep-copy'),
  visitPrepExport: document.getElementById('visit-prep-export'),
  visitPrepPrint: document.getElementById('visit-prep-print'),
  visitPrepStatus: document.getElementById('visit-prep-status'),
  visitPrepBody: document.getElementById('visit-prep-body'),
  visitPrepEmpty: document.getElementById('visit-prep-empty'),
  visitPrepSummary: document.getElementById('visit-prep-summary'),
  compareSection: document.getElementById('compare-section'),
  compareMetric: document.getElementById('compare-metric'),
  compareRange: document.getElementById('compare-range'),
  compareList: document.getElementById('compare-list'),
  compareExport: document.getElementById('compare-export'),
  compareStatus: document.getElementById('compare-status'),
  compareEmpty: document.getElementById('compare-empty'),
  calendarSection: document.getElementById('calendar-section'),
  calendarMonthLabel: document.getElementById('calendar-month-label'),
  calendarPrev: document.getElementById('calendar-prev'),
  calendarNext: document.getElementById('calendar-next'),
  calendarToday: document.getElementById('calendar-today'),
  calendarExport: document.getElementById('calendar-export'),
  calendarGrid: document.getElementById('calendar-grid'),
  calendarSummary: document.getElementById('calendar-summary'),
  timelineSection: document.getElementById('timeline-section'),
  timelineRange: document.getElementById('timeline-range'),
  timelineType: document.getElementById('timeline-type'),
  timelineSearch: document.getElementById('timeline-search'),
  timelineOnlyAlert: document.getElementById('timeline-only-alert'),
  timelineRiskOnly: document.getElementById('timeline-risk-only'),
  timelineExport: document.getElementById('timeline-export'),
  timelineMore: document.getElementById('timeline-more'),
  timelineDateChip: document.getElementById('timeline-date-chip'),
  timelineSummary: document.getElementById('timeline-summary'),
  timelineList: document.getElementById('timeline-list'),
  timelineEmpty: document.getElementById('timeline-empty'),
  medicationSearch: document.getElementById('medication-search'),
  medicationStatusFilter: document.getElementById('medication-status-filter'),
  medicationRange: document.getElementById('medication-range'),
  medicationEmpty: document.getElementById('medication-empty'),
  medicationExport: document.getElementById('medication-export'),
  medicationLogsExport: document.getElementById('medication-logs-export'),
  medicationReminders: document.getElementById('medication-reminders'),
  medicationImport: document.getElementById('medication-import'),
  medicationTemplate: document.getElementById('medication-template'),
  medicationLogAll: document.getElementById('medication-log-all'),
  medicationImportFile: document.getElementById('medication-import-file'),
  calcInteractionsLoad: document.getElementById('calc-interactions-load'),
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
let sessionId = initialSessionFromUrl || safeStorageGet('medix.session') || null;
// 当前会话已从服务端渲染的最大消息 ID；中止对账用它区分“旧问答”和
// “本轮新落库的问答”，仅靠问题文本会在重复提问时误判
let lastServerMessageId = 0;
if (initialSessionFromUrl) {
  safeStorageSet('medix.session', initialSessionFromUrl);
}
let busy = false;
let activeController = null;
let pricing = null;
let latestUsage = null;
let historyOpen = false;
let kbOpen = false;
let diagOpen = false;
let calcOpen = false;
let calcRequestSeq = 0;
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
let historyRisk = '';
let historyProfile = '';
let bulkMode = false;
let selectedSessions = new Set();
let historyItemMap = new Map();
let compareOpen = false;
let historyDays = 0;
let historyRequestSeq = 0;
let historyTagsRequestSeq = 0;
let auditRequestSeq = 0;
let remindersSearchTimer = null;
let symptomSearchTimer = null;
let medicationSearchTimer = null;
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
    els.authToken.setAttribute('aria-invalid', 'true');
  }
  if (authGateOpen) return;

  // 401 可能在抽屉打开时到达，先关闭其他模态，避免两个 aria-modal 并存
  closeHistory();
  closeKb();
  closeCalc();
  closeDiagnose();
  closeMetrics();
  closeReminders();
  closeCompare();
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
  els.authToken.setAttribute('aria-invalid', 'false');
  syncPageInert();
}

// 所有同源 /api 请求自动带上访问令牌（未开启访问保护时为空操作）
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  if (url.startsWith('/api/')) {
    const token = safeSessionGet('medix.token');
    if (token) {
      const headers = new Headers(init.headers || {});
      // Header 值只能是 ISO-8859-1；把 UTF-8 字节映射成 latin-1 字符，
      // 服务端 core/auth.py 会再按 UTF-8 解码
      const encodedToken = Array.from(
        new TextEncoder().encode(token),
        (byte) => String.fromCharCode(byte)
      ).join('');
      headers.set('X-Medix-Token', encodedToken);
      init = { ...init, headers };
    }
  }

  return nativeFetch(input, init).then((response) => {
    if (response.status === 401) {
      const hasToken = Boolean(safeSessionGet('medix.token'));
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
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) {
    themeColor.content = theme === 'dark' ? '#0d1519' : '#0b6e9e';
  }
  try {
    safeStorageSet(THEME_KEY, theme);
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

function isWindowNearBottom() {
  const doc = document.scrollingElement || document.documentElement;
  return window.innerHeight + window.scrollY >= doc.scrollHeight - 140;
}

/**
 * 只有用户停留在底部附近时才自动跟随新内容；
 * 用户向上翻看历史时，改为显示「回到最新」按钮，避免把阅读位置拽走。
 */
function scrollThreadToBottom(force = false) {
  if (isStacked()) {
    const doc = document.scrollingElement || document.documentElement;
    if (force || isWindowNearBottom()) {
      window.scrollTo({ top: doc.scrollHeight, behavior: 'auto' });
      els.jumpLatest.hidden = true;
    } else {
      els.jumpLatest.hidden = false;
    }
    return;
  }

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
  wrapper.tabIndex = 0;
  wrapper.setAttribute('aria-label', '表格，可横向滚动');
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
    { level: 'high', re: /高风险(?!人群|因素)|高度风险|高危(?!人群|因素)/ },
    { level: 'medium', re: /中风险(?!人群|因素)|中等风险|中危(?!人群|因素)/ },
    { level: 'low', re: /低风险(?!人群|因素)|风险较低|风险不高|低危(?!人群|因素)/ },
  ];
  const prefixAt = (value, index) => value.slice(
    Math.max(0, index - 12), index
  );
  const ruleOutAt = (value, index) => (
    /(?:不|不能|无法|难以|未能|未|没有?)\s*(?:完全|明确|彻底)?\s*排除\s*$/
      .test(prefixAt(value, index))
  );
  const negatedAt = (value, index) => (
    /(?:不属于|不是|并非|排除|未见|没有|无明显|无|非|不)(?:明显|任何|明确|相关|特殊)?\s*$/
      .test(prefixAt(value, index))
  );
  const hasPositiveMatch = (value, pattern) => {
    const global = new RegExp(
      pattern.source, pattern.flags.includes('g') ? pattern.flags : `${pattern.flags}g`
    );
    let match;
    while ((match = global.exec(value)) !== null) {
      // “不排除高危”是双重否定，风险仍然存在
      if (ruleOutAt(value, match.index)) return true;
      if (!negatedAt(value, match.index)) return true;
      if (match.index === global.lastIndex) global.lastIndex += 1;
    }
    return false;
  };
  for (const row of plain) {
    if (hasPositiveMatch(text, row.re)) {
      return { level: row.level, label: levelLabels[row.level] };
    }
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
    const previousFocus = document.activeElement;
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
    if (
      previousFocus
      && previousFocus !== document.body
      && document.contains(previousFocus)
      && typeof previousFocus.focus === 'function'
    ) {
      previousFocus.focus();
    }
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
    blood_type: '血型',
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

/** 下载健康数据导入模板，列名与服务端导入器保持一致。 */
async function downloadCsvTemplate(kind) {
  try {
    const res = await fetch(
      `/api/templates/${encodeURIComponent(kind)}`, { cache: 'no-store' }
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    // Response.text() 会丢掉服务端的 BOM，改用原始字节保证 Excel 正确识别中文
    const buffer = await res.arrayBuffer();
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-${kind}-template.csv`,
      'text/csv;charset=utf-8'
    );
  } catch (err) {
    window.alert(`下载模板失败：${err.message || err}`);
  }
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
  symptoms, medications, weekly, attachments = [], failures = [],
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
    const range = metric === 'bp'
      ? `收缩压 ${formatStatNumber(v1.min)}–${formatStatNumber(v1.max)}；`
        + `舒张压 ${formatStatNumber(v2.min)}–${formatStatNumber(v2.max)}`
      : `${formatStatNumber(v1.min)}–${formatStatNumber(v1.max)}`;
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

  const symptomItems = (symptoms || []).map((item) => (
    `<li>${escapeHtml(item.symptom)} · <strong>${Number(item.severity)}/10</strong>`
    + ` · ${escapeHtml(formatTime(item.recorded_at))}`
    + `${item.note ? `（${escapeHtml(item.note)}）` : ''}`
    + '</li>'
  )).join('\n');

  const medicationItems = (medications || []).map((item) => {
    const stats = [];
    const todayCount = Number(item.today_count || 0);
    const weekCount = Number(item.week_count || 0);
    stats.push(todayCount > 0 ? `今日已服 ${todayCount} 次` : '今日未打卡');
    if (weekCount > 0) stats.push(`近 7 天 ${weekCount} 次`);
    if (item.last_taken_at) {
      stats.push(`最近 ${escapeHtml(formatTime(item.last_taken_at))}`);
    }
    return `<li>${escapeHtml(item.name)}`
      + `${item.dose ? ` · ${escapeHtml(item.dose)}` : ''}`
      + `${item.frequency ? ` · ${escapeHtml(item.frequency)}` : ''}`
      + `（${stats.join(' · ')}）`
      + `${item.note ? ` · ${escapeHtml(item.note)}` : ''}</li>`;
  }).join('\n');

  const sessionItems = sessions.map((item) => (
    `<li>${escapeHtml(formatTime(item.updated_at || item.created_at))}`
    + ` · ${escapeHtml(item.title || '未命名会诊')}</li>`
  )).join('\n');

  const attachmentItems = (attachments || []).map((item) => {
    const date = String(item.created_at || '').slice(0, 10);
    const linked = item.ref_id ? '已关联' : '未关联';
    return `<li>${escapeHtml(date)} `
      + `${escapeHtml(item.original_name || '附件')}`
      + `（${escapeHtml(item.kind_label || '其他')}，${linked}）`
      + `${item.note ? ` · ${escapeHtml(item.note)}` : ''}</li>`;
  }).join('\n');

  const weeklyItems = weekly
    ? weeklySummaryLines(weekly)
      .map(([label, text]) => (
        `<li><strong>${escapeHtml(label)}</strong>：${escapeHtml(text)}</li>`
      ))
      .join('\n')
    : '';
  const weeklyRange = weekly
    ? `${escapeHtml(weekly.start)} 至 ${escapeHtml(weekly.end)}`
    : '';

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
  .warn {
    margin: 0 0 18px; padding: 10px 14px; border-radius: 8px;
    background: #fef3c7; border: 1px solid #f59e0b; color: #92400e;
    font-size: 13px;
  }
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

  ${failures.length
    ? `<p class="warn">部分数据读取失败（${failures.map(escapeHtml).join('、')}），`
      + '以下内容可能不完整，请核对后再使用。</p>'
    : ''}

  <section>
    <h2>基本信息</h2>
    <p class="muted">患者档案：${escapeHtml(profile || '未指定')}</p>
    ${contextRows
      ? `<table><tbody>${contextRows}</tbody></table>`
      : '<p class="muted">还没有填写年龄、既往史、用药或过敏史等补充信息。</p>'}
  </section>

  ${weeklyItems
    ? `<section>
        <h2>近 7 天小结（${weeklyRange}）</h2>
        <ul>${weeklyItems}</ul>
      </section>`
    : ''}

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
    <h2>用药清单</h2>
    ${medicationItems
      ? `<ul>${medicationItems}</ul>`
      : '<p class="muted">没有在用的药物记录。</p>'}
  </section>

  <section>
    <h2>近期症状</h2>
    ${symptomItems
      ? `<ul>${symptomItems}</ul>`
      : '<p class="muted">近 30 天没有症状记录。</p>'}
  </section>

  <section>
    <h2>随访提醒</h2>
    ${reminderItems
      ? `<ul>${reminderItems}</ul>`
      : '<p class="muted">没有待完成的随访提醒。</p>'}
  </section>

  <section>
    <h2>就医资料</h2>
    ${attachmentItems
      ? `<ul>${attachmentItems}</ul>`
      : '<p class="muted">还没有就医资料。</p>'}
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
    const metricParams = new URLSearchParams({ limit: '30', profile });
    const sessionParams = new URLSearchParams({ limit: '200', profile });
    const targetParams = new URLSearchParams({ profile });
    const reminderParams = new URLSearchParams({ profile });
    const symptomParams = new URLSearchParams({
      limit: '30', days: '30', profile,
    });
    const medicationParams = new URLSearchParams({
      active_only: 'true', limit: '100', profile,
    });
    const attachmentParams = new URLSearchParams({
      limit: '100', profile,
    });
    const weeklyParams = new URLSearchParams({ profile });
    const [
      metricsRes, remindersRes, sessionsRes, targetsRes, symptomsRes,
      medicationsRes, weeklyRes, attachmentsRes,
    ] = await Promise.all([
      fetch(`/api/metrics?${metricParams.toString()}`),
      fetch(`/api/reminders?${reminderParams.toString()}`),
      fetch(`/api/sessions?${sessionParams.toString()}`),
      fetch(`/api/metric-targets?${targetParams.toString()}`),
      fetch(`/api/symptoms?${symptomParams.toString()}`),
      fetch(`/api/medications?${medicationParams.toString()}`),
      fetch(`/api/reports/weekly?${weeklyParams.toString()}`),
      fetch(`/api/attachments?${attachmentParams.toString()}`),
    ]);
    const failures = [];
    const track = (res, label) => {
      if (!res.ok) failures.push(label);
      return res.ok;
    };
    const metrics = track(metricsRes, '健康指标')
      ? (await metricsRes.json()).metrics || []
      : [];
    const reminders = track(remindersRes, '随访提醒')
      ? ((await remindersRes.json()).reminders || []).filter(
        (item) => (item.profile || '') === profile
      )
      : [];
    const sessions = track(sessionsRes, '会诊记录')
      ? ((await sessionsRes.json()).sessions || [])
        .filter((item) => (item.profile || '') === profile)
        .slice(0, 5)
      : [];
    const targets = track(targetsRes, '指标目标')
      ? ((await targetsRes.json()).targets || []).filter(
        (item) => (item.profile || '') === profile
      )
      : [];
    const symptoms = track(symptomsRes, '症状日记')
      ? (await symptomsRes.json()).symptoms || []
      : [];
    const medications = track(medicationsRes, '用药清单')
      ? (await medicationsRes.json()).medications || []
      : [];
    const attachments = track(attachmentsRes, '就医资料')
      ? (await attachmentsRes.json()).attachments || []
      : [];
    const weekly = weeklyRes && weeklyRes.ok
      ? await weeklyRes.json().catch(() => null)
      : null;
    if (weeklyRes && !weeklyRes.ok) failures.push('近 7 天小结');
    const summaries = await fetchMetricSummaries(
      profile, metrics, 0, false
    );

    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      buildVisitSummaryHtml({
        profile, context, metrics, reminders, sessions, targets,
        summaries, symptoms, medications, weekly, attachments, failures,
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
    body.tabIndex = 0;
    body.setAttribute('aria-label', '科室意见全文，可滚动查看');
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
  const messageId = Number(payload.assistant_message_id) || 0;
  if (messageId > lastServerMessageId) lastServerMessageId = messageId;
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
  button.addEventListener('click', async () => {
    if (busy) return;
    button.disabled = true;
    // 传输中断不代表服务端没有保存：先核对本轮问答，已落库时恢复而不是重发
    if (sessionId) {
      const restored = await restoreCurrentSession(
        retry.question, Number(retry.baselineMessageId) || 0
      );
      if (restored) {
        retry.askTurn?.remove();
        wrap.remove();
        return;
      }
    }
    retry.askTurn?.remove();
    wrap.remove();
    send(retry.question);
  });
  wrap.appendChild(button);
}

function renderError(message, retry = null) {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  wrap.setAttribute('role', 'alert');
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
  wrap.setAttribute('role', 'alert');
  const strong = document.createElement('strong');
  strong.textContent = '会诊已停止';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode(
    '已停止接收本次会诊。若服务端已完成生成，结果会自动刷新；'
    + '否则可重新会诊。'
  ));
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
  safeStorageSet('medix.session', sessionId);
  setSessionUrl(sessionId);
  els.readout.session.textContent = shortSession(sessionId);
  return sessionId;
}

async function startNewSession() {
  if (busy) {
    window.alert('会诊进行中，请先停止当前会诊。');
    return;
  }
  // 作废仍在途的历史会话请求，避免旧响应覆盖刚创建的新会话
  openSessionSeq += 1;

  try {
    const res = await fetch('/api/sessions', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.session_id) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    sessionId = data.session_id;
    safeStorageSet('medix.session', sessionId);
    setSessionUrl(sessionId);
    lastServerMessageId = 0;

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
    } else if (historyRisk) {
      els.historyEmpty.textContent = '所选风险等级下没有会诊';
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
    const riskLabel = HEALTH_RISK_LABELS[item.risk];
    if (riskLabel) {
      const riskBadge = document.createElement('span');
      riskBadge.className = 'history-risk'
        + (
          item.risk === 'high' || item.risk === 'emergency'
            ? ' history-risk-alert'
            : ''
        );
      riskBadge.textContent = riskLabel;
      title.append(' ', riskBadge);
    }

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
  const riskLabel = historyRisk && els.historyRiskFilter
    ? String(
      els.historyRiskFilter.selectedOptions[0]?.textContent || ''
    ).trim()
    : '';
  if (riskLabel) title += `（${riskLabel}）`;
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

let feedbackRequestSeq = 0;

async function loadRecentFeedback() {
  if (!els.historyFeedback) return;
  const seq = ++feedbackRequestSeq;
  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(
      `/api/feedback/recent?limit=8&rating=-1${scope}`
    );
    if (!res.ok) return;
    const data = await res.json();
    if (seq !== feedbackRequestSeq) return;
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

let qualityRequestSeq = 0;

async function loadQualityInsights() {
  const seq = ++qualityRequestSeq;
  try {
    const scope = historyProfile
      ? `&profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(`/api/insights/quality?days=30${scope}`);
    if (!res.ok) return;
    const data = await res.json();
    if (seq !== qualityRequestSeq) return;
    renderQualityInsights(data);
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
  if (busy) {
    els.historyActionStatus.textContent = '会诊进行中，暂不能批量删除会话。';
    return;
  }
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
      clearCurrentSession();
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
      (historyRisk ? `&risk=${encodeURIComponent(historyRisk)}` : '') +
      (historyProfile ? `&profile=${encodeURIComponent(historyProfile)}` : '') +
      (historyDays > 0 ? `&days=${historyDays}` : '')
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== historyRequestSeq) return;

    const sessions = data.sessions || [];
    if (!append) {
      const visibleIds = new Set(sessions.map((item) => item.id));
      for (const id of Array.from(selectedSessions)) {
        if (!visibleIds.has(id)) selectedSessions.delete(id);
      }
    }
    const loadedTotal = historyOffset + sessions.length;
    const viewSummary = { ...(data.summary || {}), loaded: loadedTotal };

    renderHistoryItems(sessions, viewSummary, append);
    historyTotal = Number(viewSummary.sessions || 0);
    historyOffset = loadedTotal;
    historyLoading = false;
    updateHistoryMore(viewSummary);
    updateBulkBar();
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
    selectedSessions.clear();
    updateBulkBar();
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
    markBackupExported();
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
    if (historyRisk) params.set('risk', historyRisk);
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

async function refreshAfterBackupImport(result) {
  await loadHistory(els.historySearch.value.trim());
  await loadUsageTrend();
  await loadUsageBudget();
  if (result.profiles) await initProfiles();
  if (result.targets) loadMetricTargets();
  if (
    result.metrics || result.symptoms || result.reminders
    || result.medications || result.medication_logs
    || result.vaccinations || result.appointments
    || result.lab_results || result.allergies
    || result.care_items || result.visit_records
    || result.conditions
  ) {
    await refreshMetricProfileOptions();
    loadSymptoms();
    if (result.vaccinations) loadVaccinations();
    if (result.appointments) loadAppointments();
    if (result.lab_results) loadLabs();
    if (result.allergies) loadAllergies();
    if (result.care_items) loadCareItems();
    if (result.visit_records) loadVisits();
    if (result.conditions) loadConditions();
    if (result.medications || result.medication_logs) loadMedications();
    if (result.medication_logs) loadMedicationLogs();
    refreshReminderBadge();
    refreshOverview();
  }
  await loadQualityInsights();
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
    const reminderCount = Array.isArray(data.reminders)
      ? data.reminders.length
      : 0;
    const targetCount = Array.isArray(data.metric_targets)
      ? data.metric_targets.length
      : 0;
    const symptomCount = Array.isArray(data.symptoms)
      ? data.symptoms.length
      : 0;
    const medicationCount = Array.isArray(data.medications)
      ? data.medications.length
      : 0;
    const medicationLogCount = Array.isArray(data.medication_logs)
      ? data.medication_logs.length
      : 0;
    const auditCount = Array.isArray(data.audit_log)
      ? data.audit_log.length
      : 0;
    if (
      !count && !profileCount && !metricCount
      && !reminderCount && !targetCount && !symptomCount
      && !medicationCount && !medicationLogCount && !auditCount
    ) {
      window.alert('备份文件中没有可导入的数据。');
      return;
    }
    const summary = [
      count ? `${count} 个会话` : '',
      profileCount ? `${profileCount} 个患者档案` : '',
      metricCount ? `${metricCount} 条健康记录` : '',
      reminderCount ? `${reminderCount} 条随访提醒` : '',
      targetCount ? `${targetCount} 个指标目标` : '',
      symptomCount ? `${symptomCount} 条症状记录` : '',
      medicationCount ? `${medicationCount} 条用药记录` : '',
      medicationLogCount ? `${medicationLogCount} 次用药打卡` : '',
      auditCount ? `${auditCount} 条操作记录` : '',
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
          metric_targets: data.metric_targets,
          reminders: data.reminders,
          symptoms: data.symptoms,
          medications: data.medications,
          medication_logs: data.medication_logs,
          medication_history: data.medication_history,
          vaccinations: data.vaccinations,
          appointments: data.appointments,
          lab_results: data.lab_results,
          allergies: data.allergies,
          care_items: data.care_items,
          visit_records: data.visit_records,
          conditions: data.conditions,
          audit_log: data.audit_log,
          checksum: data.checksum,
        }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(result.detail || `服务返回 ${res.status}`);

      btn.textContent = `已导入 ${result.imported || 0} 次`;
      await refreshAfterBackupImport(result);
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

async function createLocalBackup() {
  const btn = els.historyBackupNow;
  if (!btn || btn.disabled) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '备份中…';

  try {
    const res = await fetch('/api/backups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    });
    const result = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(result.detail || `服务返回 ${res.status}`);

    btn.textContent = '已备份';
    markBackupExported();
    await loadHomeGlance();
  } catch (err) {
    btn.textContent = '备份失败';
    window.alert(`创建本地备份失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1600);
  }
}

async function restoreBackup() {
  const btn = els.historyRestore;
  if (!btn || btn.disabled) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '读取备份…';

  try {
    const listRes = await fetch('/api/backups');
    const list = await listRes.json().catch(() => ({}));
    if (!listRes.ok) {
      throw new Error(list.detail || `服务返回 ${listRes.status}`);
    }
    const candidates = (list.backups || []).slice(0, 10);
    let target = candidates[0];
    if (!target) {
      btn.textContent = '暂无备份';
      window.alert(
        '暂无本地备份。可点击“立即备份”，或在设置中开启自动备份。'
      );
      return;
    }

    if (candidates.length > 1) {
      const options = candidates.map((item, index) => {
        const when = item.modified_at
          ? formatTime(item.modified_at)
          : item.name;
        return `${index + 1}. ${item.name}（${when}）`;
      }).join('\n');
      const answer = window.prompt(
        `选择要恢复的备份序号：\n${options}`, '1'
      );
      if (answer === null) return;
      const index = Number(answer) - 1;
      if (
        !Number.isInteger(index)
        || index < 0
        || index >= candidates.length
      ) {
        window.alert('请输入列表中的有效序号。');
        return;
      }
      target = candidates[index];
    }

    const size = target.size
      ? `${Math.max(1, Math.round(target.size / 1024))} KB`
      : '';
    const when = target.modified_at
      ? formatTime(target.modified_at)
      : target.name;

    let inspectInfo;
    try {
      const inspectRes = await fetch(
        `/api/backups/${encodeURIComponent(target.name)}/inspect`,
        { cache: 'no-store' }
      );
      const info = await inspectRes.json().catch(() => ({}));
      if (!inspectRes.ok) {
        throw new Error(
          info.detail || `服务返回 ${inspectRes.status}`
        );
      }
      inspectInfo = info;
    } catch (err) {
      btn.textContent = '预检失败';
      window.alert(`备份预检失败：${err.message || err}`);
      return;
    }
    if (inspectInfo.checksum_ok === false) {
      btn.textContent = '校验失败';
      window.alert(
        '备份校验失败，文件可能已损坏或被修改，已停止恢复。'
        + '请选择其他备份或重新创建。'
      );
      return;
    }
    if (
      inspectInfo.checksum_ok !== true
      && inspectInfo.checksum_present !== false
    ) {
      btn.textContent = '预检异常';
      window.alert(
        '备份预检响应格式不正确，已停止恢复。请稍后重试。'
      );
      return;
    }
    if (inspectInfo.restore_ready === false) {
      const reasons = Array.isArray(inspectInfo.warnings)
        ? inspectInfo.warnings.join('；')
        : '备份内容不完整。';
      btn.textContent = '不可恢复';
      window.alert(`备份预检未通过，已停止恢复：${reasons}`);
      return;
    }
    const counts = inspectInfo.counts || {};
    const inspectSummary = [
      `共 ${Number(inspectInfo.total_records) || 0} 条记录`,
      `会话 ${counts.sessions || 0}（消息 ${counts.messages || 0}）`,
      `档案 ${counts.profiles || 0}`,
      `指标 ${counts.health_metrics || 0}`,
      `化验 ${counts.lab_results || 0}`,
      `用药 ${counts.medications || 0}`,
      `提醒 ${counts.reminders || 0}`,
    ].join('，');
    const inspectNotice = inspectInfo.checksum_present === false
      ? '该备份没有校验信息，请确认来源可靠；已有记录会跳过。'
      : '校验通过，已有记录会跳过，不会删除现有数据。';
    const warnings = Array.isArray(inspectInfo.warnings)
      ? inspectInfo.warnings : [];
    const warningText = warnings.length
      ? `\n注意：${warnings.join('；')}`
      : '';
    if (!window.confirm(
      `恢复本地备份「${target.name}」（${when}${size ? `，${size}` : ''}）？\n`
      + `预检：${inspectSummary}\n${inspectNotice}${warningText}`
    )) {
      return;
    }

    btn.textContent = '恢复中…';
    const res = await fetch(
      `/api/backups/${encodeURIComponent(target.name)}/restore`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      }
    );
    const result = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(result.detail || `服务返回 ${res.status}`);

    btn.textContent = `已恢复 ${result.imported || 0} 次`;
    await refreshAfterBackupImport(result);
  } catch (err) {
    btn.textContent = '恢复失败';
    window.alert(`恢复备份失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1800);
  }
}

async function manageLocalBackups() {
  const btn = els.historyManageBackups;
  if (!btn || btn.disabled) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '读取备份…';

  try {
    const res = await fetch('/api/backups', { cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);

    const candidates = (data.backups || []).slice(0, 10);
    if (!candidates.length) {
      window.alert('暂无本地备份。');
      return;
    }

    const options = candidates.map((item, index) => {
      const when = item.modified_at ? formatTime(item.modified_at) : item.name;
      const size = item.size
        ? `，${Math.max(1, Math.round(item.size / 1024))} KB`
        : '';
      return `${index + 1}. ${item.name}（${when}${size}）`;
    }).join('\n');
    const answer = window.prompt(
      `输入要删除的备份序号（留空或取消则不删除）：\n${options}`, ''
    );
    if (answer === null || !answer.trim()) return;

    const index = Number(answer) - 1;
    if (
      !Number.isInteger(index)
      || index < 0
      || index >= candidates.length
    ) {
      window.alert('请输入列表中的有效序号。');
      return;
    }

    const target = candidates[index];
    if (!window.confirm(
      `删除本地备份「${target.name}」？删除后无法恢复。`
    )) {
      return;
    }

    btn.textContent = '删除中…';
    const del = await fetch(
      `/api/backups/${encodeURIComponent(target.name)}`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      }
    );
    const result = await del.json().catch(() => ({}));
    if (!del.ok) throw new Error(result.detail || `服务返回 ${del.status}`);

    btn.textContent = '已删除';
    await loadHomeGlance();
  } catch (err) {
    btn.textContent = '删除失败';
    window.alert(`删除本地备份失败：${err.message || err}`);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1600);
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
  lastServerMessageId = messages.reduce(
    (max, message) => Math.max(max, Number(message.id) || 0),
    0
  );

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

let openSessionSeq = 0;

async function openSession(id) {
  if (busy) return;
  const seq = ++openSessionSeq;

  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages?limit=200`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    // 拉取期间用户可能已经发起了新会诊：此时不能再渲染历史，
    // 否则会把流式输出清空并把 sessionId 改回旧会话
    if (seq !== openSessionSeq || busy) return;

    sessionId = id;
    safeStorageSet('medix.session', id);
    setSessionUrl(id);
    closeHistory();
    resetRail();
    els.readout.session.textContent = shortSession(id);
    renderStoredMessages(data.messages || [], id, data.profile || '');
    setVitals('idle', '历史会诊');
    els.input.focus();
  } catch (err) {
    if (seq !== openSessionSeq || busy) return;
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
  const requestSeq = ++historyTagsRequestSeq;
  try {
    const scope = historyProfile
      ? `?profile=${encodeURIComponent(historyProfile)}`
      : '';
    const res = await fetch(`/api/tags${scope}`);
    if (!res.ok) return;
    const data = await res.json();
    // 档案快速切换时丢弃过期响应，避免旧标签覆盖新档案的下拉
    if (requestSeq !== historyTagsRequestSeq) return;
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
      // 当前标签已不存在：用「全部标签」重新拉取列表，避免筛选控件
      // 与列表内容不一致
      loadHistory(els.historySearch.value.trim());
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

function clearCurrentSession() {
  // 同时作废在途的历史读取，防止被删会话的旧响应重新渲染
  openSessionSeq += 1;
  sessionId = null;
  lastServerMessageId = 0;
  safeStorageRemove('medix.session');
  setSessionUrl(null);
  els.thread.replaceChildren(els.empty);
  els.empty.hidden = false;
  resetRail();
  els.readout.session.textContent = '—';
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
      clearCurrentSession();
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
let kbBusy = false;
let previewFilename = '';
let previewEditable = false;
let previewReturnFocus = null;
let previewEditing = false;
let previewEditingFilename = '';
let kbPreviewSeq = 0;

function setKbStatus(text, isError = false) {
  els.kbStatus.hidden = !text;
  els.kbStatus.textContent = text || '';
  els.kbStatus.classList.toggle('error', Boolean(isError));
}

function setKbBusy(busy) {
  kbBusy = busy;
  [els.kbFile, els.kbUpload, els.kbRebuild].forEach((el) => {
    if (el) el.disabled = busy;
  });
  if (els.kbList) {
    els.kbList.querySelectorAll('.kb-item-delete').forEach((el) => {
      el.disabled = busy || el.dataset.builtin === '1';
    });
  }
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
    del.dataset.builtin = doc.builtin ? '1' : '';
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
  previewEditingFilename = '';
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
  // 焦点回到打开预览的按钮，键盘用户不必从抽屉顶部重新 Tab
  if (previewReturnFocus && previewReturnFocus.isConnected) {
    previewReturnFocus.focus();
  }
  previewReturnFocus = null;
}

async function previewKbDocument(filename) {
  const seq = ++kbPreviewSeq;
  previewReturnFocus = document.activeElement;
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
    if (seq !== kbPreviewSeq) return;

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
    if (seq === kbPreviewSeq) {
      els.kbPreviewMeta.textContent = `读取失败：${err.message || err}`;
    }
  }
}

async function startKbPreviewEdit() {
  if (!previewEditable || previewEditing || !previewFilename) return;

  const filename = previewFilename;
  const seq = ++kbPreviewSeq;
  const button = els.kbPreviewEdit;
  button.disabled = true;
  button.textContent = '读取中…';

  try {
    const res = await fetch(
      `/api/knowledge/documents/${encodeURIComponent(filename)}/preview?full=1`
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    if (seq !== kbPreviewSeq || previewFilename !== filename) return;

    els.kbPreviewEditor.value = data.content || '';
    previewEditing = true;
    previewEditingFilename = filename;
    els.kbPreviewBody.hidden = true;
    els.kbPreviewEditor.hidden = false;
    els.kbPreviewEdit.hidden = true;
    els.kbPreviewSave.hidden = false;
    els.kbPreviewCancel.hidden = false;
    els.kbPreviewEditor.focus();
  } catch (err) {
    if (seq === kbPreviewSeq) {
      els.kbPreviewMeta.textContent = `读取全文失败：${err.message || err}`;
    }
  } finally {
    button.disabled = false;
    button.textContent = '编辑';
  }
}

async function saveKbPreviewEdit() {
  if (!previewEditing || !previewEditingFilename) return;

  const filename = previewEditingFilename;
  if (previewFilename !== filename) return;
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
  if (kbBusy) return;
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
  if (kbBusy) return;
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
  if (kbBusy) return;
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

    if (previewFilename === filename) closeKbPreview();
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
  const statValue = (value) => (
    value === null || value === undefined ? '—' : value
  );
  renderDiagSection(els.diagRuntime, '运行状态', [
    diagRow(
      '会话数据库',
      runtime.db_exists
        ? `${runtime.sessions ?? 0} 次会诊 / ${runtime.messages ?? 0} 条消息`
        : '未生成'
    ),
    diagRow('数据库大小', formatBytes(runtime.db_size || 0)),
    diagRow(
      '健康数据',
      `${statValue(runtime.metrics)} 条指标 / `
      + `${statValue(runtime.symptoms)} 条症状 / `
      + `${statValue(runtime.medications)} 种药物`
    ),
    diagRow(
      '用药打卡',
      `${statValue(runtime.medication_logs)} 次 / `
      + `${statValue(runtime.reminders)} 条提醒`
    ),
    diagRow(
      '患者档案',
      `${statValue(runtime.profiles)} 个 / `
      + `${statValue(runtime.targets)} 个指标目标`
    ),
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
  if (runtime.data_errors && runtime.data_errors.length) {
    els.diagRuntime.appendChild(
      diagRow('数据表读取', `部分表不可读：${runtime.data_errors.join('、')}`)
    );
  }
  const swStatus = describeServiceWorker();
  els.diagRuntime.appendChild(diagRow('离线外壳', swStatus.text, swStatus.ok));

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
  'metric.import': '导入健康记录',
  'metric_target.set': '设置指标目标',
  'metric_target.delete': '删除指标目标',
  'maintenance.purge': '维护清理',
  'symptom.create': '添加症状记录',
  'symptom.update': '修改症状记录',
  'symptom.delete': '删除症状记录',
  'symptom.import': '导入症状记录',
  'medication.create': '添加药物',
  'medication.update': '修改药物',
  'medication.delete': '删除药物',
  'medication.import': '导入用药清单',
  'medication.log': '用药打卡',
  'medication.log_delete': '撤销用药打卡',
  'reminder.create': '添加随访提醒',
  'reminder.complete': '完成随访提醒',
  'reminder.reopen': '恢复随访提醒',
  'reminder.update': '修改随访提醒',
  'reminder.snooze': '推迟随访提醒',
  'reminder.delete': '删除随访提醒',
  'reminder.import': '导入随访提醒',
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
  const requestSeq = ++auditRequestSeq;
  try {
    const res = await fetch(`/api/audit?${auditFilterParams().toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    // 快速切换筛选时丢弃过期响应
    if (requestSeq !== auditRequestSeq) return;
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
  if (busy) {
    setDiagStatus('会诊进行中，暂不能清理旧会诊。', true);
    return;
  }
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
    `将永久删除 ${days} 天以前的会诊及其消息（本地备份文件不会被自动删除，`
    + '如需清除请使用“管理备份”），确定继续？'
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

    // 当前打开的会诊可能已被清理；确认后再清空界面，避免继续展示已删除内容
    if (sessionId) {
      const current = sessionId;
      let purged = false;
      try {
        const check = await fetch(
          `/api/sessions/${encodeURIComponent(current)}/messages?limit=1`
        );
        if (check.ok) {
          const info = await check.json().catch(() => ({}));
          purged = Array.isArray(info.messages) && info.messages.length === 0;
        }
      } catch {
        purged = false;
      }
      if (purged && sessionId === current) clearCurrentSession();
    }
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
let metricSaving = false;
let metricsRequestSeq = 0;
let metricTrendRequestSeq = 0;
let metricOverviewRequestSeq = 0;
let metricSummaryRequestSeq = 0;
let metricWeeklyRequestSeq = 0;
let metricsOffset = 0;
let metricItemsCache = [];
let homeGlanceRequestSeq = 0;
const BACKUP_STORAGE_KEY = 'medix.lastBackupAt';
const HEALTH_FILTER_KEYS = {
  weeklyRange: 'medix.weeklyRange',
  timelineRange: 'medix.timelineRange',
  timelineType: 'medix.timelineType',
  metricRange: 'medix.metricRange',
  metricType: 'medix.metricType',
  compareMetric: 'medix.compareMetric',
  compareRange: 'medix.compareRange',
  medicationRange: 'medix.medicationRange',
  reminderStatsRange: 'medix.reminderStatsRange',
};

function safeSessionGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSessionSet(key, value) {
  try {
    sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeSessionRemove(key) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // 隐私模式下不可用时忽略
  }
}

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeStorageRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    // 隐私模式下不可用时忽略
  }
}

function readStoredFilter(key) {
  return safeStorageGet(key) || '';
}

function storeFilter(key, value) {
  safeStorageSet(key, value);
}

function restoreSelectValue(select, key) {
  if (!select) return;
  const stored = readStoredFilter(key);
  if (!stored) return;
  const exists = Array.from(select.options).some(
    (option) => option.value === stored
  );
  if (exists) select.value = stored;
}

function readLastBackupAt() {
  try {
    return safeStorageGet(BACKUP_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function markBackupExported() {
  const now = new Date();
  const local = new Date(
    now.getTime() - now.getTimezoneOffset() * 60000
  ).toISOString().slice(0, 19);
  try {
    safeStorageSet(BACKUP_STORAGE_KEY, local);
  } catch {
    // 隐私模式下不可用时忽略
  }
}

function backupDaysAgo(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
}
let weeklySummaryRequestSeq = 0;
let weeklySummaryCache = null;
let weeklySummaryCopyTimer = null;
let timelineRequestSeq = 0;
let timelineDataCache = null;
let timelineLimit = 200;
let timelineDate = '';
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
  cat: '分',
  ess: '分',
  auditc: '分',
  mmrc: '级',
};
const METRIC_CONTEXT_OPTIONS = {
  bp: [['morning', '晨起'], ['evening', '睡前']],
  glucose: [
    ['fasting', '空腹'],
    ['postprandial', '餐后'],
    ['bedtime', '睡前'],
    ['random', '随机'],
  ],
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
  cat: 'CAT 评分',
  ess: 'ESS 评分',
  auditc: 'AUDIT-C 评分',
  mmrc: 'mMRC 分级',
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
  cat: 'CAT 总分 0–40，分数越高提示慢阻肺对生活影响越大。',
  ess: 'ESS 总分 0–24，分数越高提示日间嗜睡越明显。',
  auditc: 'AUDIT-C 总分 0–12，分数越高提示风险饮酒可能性越大。',
  mmrc: 'mMRC 0–4 级，级别越高提示呼吸困难越明显。',
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

const METRIC_TARGET_DEFAULTS = {
  bp: { value1: 135, value2: 85 },
  spo2: { value1: 95 },
  heart_rate: { value1: 100 },
};

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
  if (els.metricTargetHint) els.metricTargetHint.hidden = true;
  renderMetricTargetList();
}

function startEditMetricTarget() {
  const metric = els.metricType.value;
  const target = findMetricTarget(els.metricProfile.value, metric);
  const isBp = metric === 'bp';
  let usedDefaults = false;
  if (target) {
    els.metricTargetValue1.value = String(target.value1);
    els.metricTargetValue2.value =
      target.value2 != null ? String(target.value2) : '';
  } else {
    const defaults = METRIC_TARGET_DEFAULTS[metric];
    els.metricTargetValue1.value =
      defaults ? String(defaults.value1) : '';
    els.metricTargetValue2.value =
      defaults && defaults.value2 != null ? String(defaults.value2) : '';
    usedDefaults = Boolean(defaults);
  }
  if (els.metricTargetHint) {
    els.metricTargetHint.hidden = !usedDefaults;
  }
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
  const isScore = [
    'phq9', 'gad7', 'cat', 'ess', 'auditc', 'mmrc',
  ].includes(value);
  els.metricValue.step = isScore ? '1' : '0.1';
  els.metricValue.min = isScore ? '0' : '';
  els.metricReference.textContent = METRIC_REFERENCES[value] || '';
  const contextOptions = METRIC_CONTEXT_OPTIONS[value] || [];
  const previous = els.metricContext.value;
  els.metricContext.replaceChildren();
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = '未指定';
  els.metricContext.appendChild(blank);
  for (const [key, label] of contextOptions) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = label;
    els.metricContext.appendChild(option);
  }
  els.metricContextWrap.hidden = !contextOptions.length;
  els.metricContext.value = contextOptions.some(
    ([key]) => key === previous
  ) ? previous : '';
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

  const nextProfile = names.has(current) ? current : '';
  // 选项重建后 select.value 会短暂变为空，需与重建前的值比较
  if (current !== nextProfile) {
    resetVisitPrep();
    loadAttachmentRefOptions();
  }
  els.metricProfile.value = nextProfile;
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
  const query = els.metricSearch
    ? String(els.metricSearch.value || '').trim()
    : '';
  els.metricsEmpty.textContent = query
    ? `没有匹配「${query}」的${metricName}记录。`
    : days > 0
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
    const criticalAlerts = Array.isArray(item.critical_alerts)
      ? item.critical_alerts
      : [];
    if (criticalAlerts.length) {
      const badge = document.createElement('span');
      badge.className = 'metric-critical-badge';
      badge.textContent = '危急值';
      badge.title = criticalAlerts.map((row) => row.message).join(' ');
      value.append(' ', badge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    const contextLabel = (METRIC_CONTEXT_OPTIONS[item.metric] || [])
      .find(([key]) => key === item.context)?.[1] || '';
    meta.textContent = [
      METRIC_NAMES[item.metric] || item.metric,
      item.profile || '未指定档案',
      contextLabel,
      formatTime(item.measured_at),
    ].filter(Boolean).join(' · ');

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
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== metricTrendRequestSeq) return;
    renderMetricTrend(data.metrics || [], metric);
  } catch {
    // 切换档案后请求失败时清空趋势，避免显示上一个患者的曲线
    if (requestSeq !== metricTrendRequestSeq) return;
    renderMetricTrend([], metric);
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
  if (isBp) {
    // 收缩压/舒张压的极值来自不同记录，不能拼成一次“180/110”的测量值
    addChip('收缩压最高', formatStatNumber(v1.max));
    addChip('收缩压最低', formatStatNumber(v1.min));
    addChip('舒张压最高', formatStatNumber(v2.max));
    addChip('舒张压最低', formatStatNumber(v2.min));
  } else {
    addChip('最高', formatStatNumber(v1.max) + unit);
    addChip('最低', formatStatNumber(v1.min) + unit);
  }

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
    'waist', 'phq9', 'gad7', 'cat', 'ess', 'auditc', 'mmrc',
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

    if (metric === 'bp' && summary.bp_grades) {
      const gradeLabels = {
        normal: '正常',
        high_normal: '正常高值',
        grade1: '1 级',
        grade2: '2 级',
        grade3: '3 级',
        low: '偏低',
      };
      const parts = Object.entries(gradeLabels)
        .filter(([key]) => Number(summary.bp_grades[key]) > 0)
        .map(([key, label]) => `${label} ${summary.bp_grades[key]}`);
      if (parts.length) {
        const grades = document.createElement('span');
        grades.className = 'metric-weekly-grades';
        grades.textContent = `分级 ${parts.join(' · ')}`;
        button.appendChild(grades);
      }
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
    const params = new URLSearchParams({ profile, days: '7' });
    const res = await fetch(
      `/api/metrics/latest?${params.toString()}`
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
    const params = new URLSearchParams({ profile });
    if (days > 0) params.set('days', String(days));
    const res = await fetch(
      `/api/metrics/latest?${params.toString()}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const items = (await res.json()).metrics || [];
    if (requestSeq !== metricOverviewRequestSeq) return;

    const latest = {};
    for (const item of items) {
      latest[item.metric] = item;
    }

    els.metricOverviewGrid.replaceChildren();
    let count = 0;
    for (const metric of [
      'bp', 'weight', 'glucose', 'temperature', 'heart_rate',
      'spo2', 'waist', 'phq9', 'gad7', 'cat', 'ess', 'auditc', 'mmrc',
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
    // 切换档案后请求失败时清空概览，避免把上一个患者的数值留在界面上
    if (requestSeq !== metricOverviewRequestSeq) return;
    els.metricOverviewGrid.replaceChildren();
    els.metricOverview.hidden = true;
  }
}

async function refreshMetrics() {
  loadHealthInsights();
  updateMetricTargetDisplay();
  await loadMetrics();
  await loadMetricTrend();
  await loadMetricSummary();
  await loadMetricWeekly();
  await loadMetricOverview();
  loadSymptoms();
  loadVaccinations();
  loadAppointments();
  loadLabs();
  loadAllergies();
  loadCareItems();
  loadVisits();
  loadAttachmentRefCounts();
  loadConditions();
  loadMedications();
  loadMedicationLogs();
  loadWeeklySummary();
  loadTimeline();
  loadCalendar();
  loadFamilyCompare();
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
  const query = els.metricSearch
    ? String(els.metricSearch.value || '').trim()
    : '';
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
      + (query ? `&q=${encodeURIComponent(query)}` : '')
    );
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `服务返回 ${res.status}`);
    }
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
  } catch (err) {
    if (requestSeq !== metricsRequestSeq) return;
    if (!append) {
      metricItemsCache = [];
      metricsOffset = 0;
      els.metricsList.replaceChildren();
      els.metricsMore.hidden = true;
      els.metricsEmpty.hidden = false;
      els.metricsEmpty.textContent =
        `加载失败：${err.message || err}`;
    }
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
  els.healthSearchToggle.setAttribute('aria-expanded', 'true');
  setMetricsStatus('');
  await refreshMetricProfileOptions();
  const activeProfile = els.profileSelect.value;
  if (activeProfile && profilesCache[activeProfile]) {
    setMetricProfileValue(activeProfile);
  }
  if (!els.metricTime.value) els.metricTime.value = metricNowLocal();
  if (els.symptomTime && !els.symptomTime.value) {
    els.symptomTime.value = metricNowLocal();
  }
  setMetricType(els.metricType.value || 'bp');
  resetMetricForm();
  quickEntryRequestSeq += 1;
  clearQuickEntryPreview();
  setQuickEntryStatus('');
  resetQuickEntryUndo();
  loadDataQuality();
  loadBpMonitoring();
  loadAttachments();
  loadAttachmentRefOptions();
  loadMetricTargets().then(() => refreshMetrics());
  if (!els.metricsDrawer.contains(document.activeElement)) {
    els.metricsClose.focus();
  }
}

function closeMetrics(restoreFocus = false) {
  metricsOpen = false;
  els.metricsDrawer.hidden = true;
  els.metricsBackdrop.hidden = true;
  syncPageInert();
  els.metricsOpen.setAttribute('aria-expanded', 'false');
  els.healthSearchToggle.setAttribute('aria-expanded', 'false');
  if (restoreFocus && metricsFocused instanceof HTMLElement) {
    metricsFocused.focus();
  }
}

/* ---------- 全局健康搜索 ---------- */

const HEALTH_SEARCH_LABELS = {
  metric: '指标',
  symptom: '症状',
  medication: '用药',
  lab: '化验',
  vaccination: '疫苗',
  appointment: '预约',
  care: '复查',
  visit: '就诊',
  allergy: '过敏',
  condition: '慢病',
  reminder: '提醒',
  attachment: '资料',
};

let healthSearchTimer = null;
let healthSearchSeq = 0;
let healthSearchQuery = '';

function setHealthSearchStatus(text, isError = false) {
  if (!els.healthSearchStatus) return;
  els.healthSearchStatus.textContent = text || '';
  els.healthSearchStatus.hidden = !text;
  els.healthSearchStatus.classList.toggle('error', Boolean(isError));
}

function hideHealthSearchResults() {
  if (els.healthSearchResults) {
    els.healthSearchResults.hidden = true;
    els.healthSearchResults.replaceChildren();
  }
  setHealthSearchStatus('');
}

async function openHealthSearch() {
  if (!metricsOpen) {
    const opening = openMetrics();
    if (els.healthSearchInput) {
      els.healthSearchInput.focus();
      els.healthSearchInput.select();
    }
    await opening;
    return;
  }
  if (els.healthSearchInput) {
    els.healthSearchInput.focus();
    els.healthSearchInput.select();
  }
}

async function runHealthSearch(rawQuery) {
  const query = String(rawQuery || '').trim();
  healthSearchQuery = query;
  if (els.healthSearchClear) {
    els.healthSearchClear.hidden = !query;
  }
  const seq = ++healthSearchSeq;
  if (!query) {
    hideHealthSearchResults();
    return;
  }
  const profile = els.metricProfile ? els.metricProfile.value : '';
  setHealthSearchStatus('正在搜索…');
  try {
    const res = await fetch(
      `/api/health/search?q=${encodeURIComponent(query)}`
      + `&limit=200&profile=${encodeURIComponent(profile)}`
    );
    const data = await res.json().catch(() => ({}));
    if (seq !== healthSearchSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    renderHealthSearchResults(data);
  } catch (err) {
    if (seq !== healthSearchSeq) return;
    if (els.healthSearchResults) {
      els.healthSearchResults.hidden = true;
      els.healthSearchResults.replaceChildren();
    }
    setHealthSearchStatus(`搜索失败：${err.message || err}`, true);
  }
}

function renderHealthSearchResults(data) {
  if (!els.healthSearchResults) return;
  const query = String(data.query || healthSearchQuery || '');
  const results = data.results || [];
  els.healthSearchResults.replaceChildren();
  els.healthSearchResults.hidden = false;
  if (!results.length) {
    setHealthSearchStatus(`没有匹配「${query}」的记录。`);
    return;
  }
  const counts = data.counts || {};
  const total = Number(data.total) || results.length;
  const summaryParts = Object.entries(counts)
    .filter(([, value]) => Number(value) > 0)
    .map(([key, value]) => (
      `${HEALTH_SEARCH_LABELS[key] || key} ${Number(value)}`
    ));
  setHealthSearchStatus(
    `共 ${total} 条`
    + (total > results.length ? `，显示 ${results.length} 条` : '')
    + (summaryParts.length ? `：${summaryParts.join(' · ')}` : '')
  );
  const grouped = new Map();
  for (const item of results) {
    const key = item.type || 'other';
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  }
  for (const [typeKey, items] of grouped) {
    const group = document.createElement('section');
    group.className = 'health-search-group';
    const title = document.createElement('h3');
    title.className = 'health-search-group-title';
    const typeTotal = Number(counts[typeKey]) || items.length;
    title.textContent = `${HEALTH_SEARCH_LABELS[typeKey] || typeKey}`
      + `（${items.length}`
      + (typeTotal > items.length ? ` / ${typeTotal}` : '')
      + '）';
    group.appendChild(title);
    const list = document.createElement('ul');
    list.className = 'metrics-list';
    for (const item of items) {
      list.appendChild(renderHealthSearchItem(item, query));
    }
    group.appendChild(list);
    els.healthSearchResults.appendChild(group);
  }
}

function renderHealthSearchItem(item, query) {
  const li = document.createElement('li');
  li.className = 'metric-item search-result';
  li.tabIndex = 0;
  li.setAttribute('role', 'button');
  li.setAttribute(
    'aria-label', `查看${item.label || ''}：${item.title || ''}`
  );
  const main = document.createElement('div');
  main.className = 'metric-main';
  const badge = document.createElement('span');
  badge.className =
    `timeline-badge timeline-badge-${item.type || 'metric'}`;
  badge.textContent = item.label || item.type || '';
  const title = document.createElement('strong');
  title.className = 'metric-value';
  setHighlightedText(title, item.title || '', query);
  const meta = document.createElement('span');
  meta.className = 'metric-meta';
  meta.textContent = [
    item.profile || '未指定档案',
    String(item.at || '').replace('T', ' ').slice(0, 16),
  ].filter(Boolean).join(' · ');
  main.append(badge, title, meta);
  if (item.detail) {
    const detail = document.createElement('span');
    detail.className = 'metric-note';
    setHighlightedText(detail, item.detail, query);
    main.appendChild(detail);
  }
  li.appendChild(main);
  const open = () => locateHealthRecord(item, query);
  li.addEventListener('click', open);
  li.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
    }
  });
  return li;
}

const HEALTH_SECTION_TARGETS = {
  symptom: {
    section: 'symptom-section',
    search: 'symptom-search',
    loader: loadSymptoms,
    render: () => renderSymptoms(filterSymptoms(symptomItemsCache)),
  },
  vaccination: {
    section: 'vaccination-section',
    search: 'vaccination-search',
    loader: loadVaccinations,
    render: () => renderVaccinations(
      filterVaccinations(vaccinationItemsCache)
    ),
  },
  appointment: {
    section: 'appointment-section',
    search: 'appointment-search',
    loader: loadAppointments,
    render: () => renderAppointments(
      filterAppointments(appointmentItemsCache)
    ),
  },
  lab: {
    section: 'lab-section',
    search: 'lab-search',
    loader: loadLabs,
    render: () => renderLabs(filterLabs(labItemsCache)),
  },
  allergy: {
    section: 'allergy-section',
    search: 'allergy-search',
    loader: loadAllergies,
    render: () => renderAllergies(filterAllergies(allergyItemsCache)),
  },
  care: {
    section: 'care-section',
    search: 'care-search',
    loader: loadCareItems,
    render: () => renderCareItems(filterCareItems(careItemsCache)),
  },
  visit: {
    section: 'visit-section',
    search: 'visit-search',
    loader: loadVisits,
    render: () => renderVisits(filterVisits(visitItemsCache)),
  },
  condition: {
    section: 'condition-section',
    search: 'condition-search',
    loader: loadConditions,
    render: () => renderConditions(
      filterConditions(conditionItemsCache)
    ),
  },
  medication: {
    section: 'medication-section',
    search: 'medication-search',
    loader: loadMedications,
    render: () => {
      const status = document.getElementById(
        'medication-status-filter'
      );
      if (status) status.value = '';
      renderMedications(filterMedications(medicationItemsCache));
    },
  },
};

async function locateHealthRecord(item, query = '') {
  if (!item) return;
  if (item.type === 'reminder') {
    closeMetrics();
    await openReminders();
    if (els.remindersProfile) {
      els.remindersProfile.value = item.profile || '';
    }
    if (reminderCalendarDay) {
      reminderCalendarDay = '';
      renderReminderCalendar(reminderItemsCache);
      if (els.remindersCalendarClear) {
        els.remindersCalendarClear.hidden = true;
      }
    }
    if (els.remindersOverdueOnly) {
      els.remindersOverdueOnly.checked = false;
    }
    if (els.remindersShowDone) {
      els.remindersShowDone.checked = Boolean(item.done);
    }
    await loadReminders();
    if (els.remindersSearch) {
      els.remindersSearch.value = query || '';
      refreshReminderViews();
      els.remindersSearch.focus();
    }
    const list = document.getElementById('reminders-list');
    if (list) list.scrollIntoView({ block: 'nearest' });
    return;
  }
  const profile = item.profile || '';
  if (els.metricProfile) els.metricProfile.value = profile;
  await openMetrics();
  if (els.metricProfile && els.metricProfile.value !== profile) {
    els.metricProfile.value = profile;
    els.metricProfile.dispatchEvent(
      new Event('change', { bubbles: true })
    );
  }
  hideHealthSearchResults();
  if (els.healthSearchInput) els.healthSearchInput.value = '';
  if (els.healthSearchClear) els.healthSearchClear.hidden = true;
  if (item.type === 'attachment') {
    setAttachmentRefFilter(null);
    if (els.attachmentFilterKind) els.attachmentFilterKind.value = '';
    if (els.attachmentFilterQ) {
      els.attachmentFilterQ.value = item.title || query || '';
      els.attachmentFilterQ.dispatchEvent(
        new Event('input', { bubbles: true })
      );
    }
    const section = document.getElementById('attachments-section');
    if (section) {
      section.open = true;
      section.scrollIntoView({ block: 'nearest' });
    }
    setAttachmentStatus(
      `已定位附件：${item.title || '附件'}`
    );
    return;
  }
  if (item.type === 'metric') {
    if (els.metricType && item.metric) {
      els.metricType.value = item.metric;
      setMetricType(item.metric);
      els.metricType.dispatchEvent(
        new Event('change', { bubbles: true })
      );
    }
    const atDate = String(item.at || '').slice(0, 10);
    let daysOld = 0;
    if (/^\d{4}-\d{2}-\d{2}$/.test(atDate)) {
      const measured = new Date(`${atDate}T00:00:00`);
      if (!Number.isNaN(measured.getTime())) {
        daysOld = Math.floor(
          (Date.now() - measured.getTime()) / 86400000
        );
      }
    }
    if (daysOld > 90) {
      // 超过指标列表可选范围时，直接定位到当天的时间线
      selectCalendarDay(atDate);
      return;
    }
    if (els.metricRange && els.metricRange.value !== '90') {
      els.metricRange.value = '90';
      els.metricRange.dispatchEvent(
        new Event('change', { bubbles: true })
      );
    }
    const list = document.getElementById('metrics-list');
    if (list) list.scrollIntoView({ block: 'start' });
    if (els.metricType) els.metricType.focus();
    return;
  }
  const target = HEALTH_SECTION_TARGETS[item.type];
  if (!target) return;
  const section = document.getElementById(target.section);
  if (section) section.open = true;
  try {
    await target.loader();
  } catch {
    // 各面板会自行展示加载错误，这里保持分区打开即可
  }
  const search = document.getElementById(target.search);
  if (search) {
    search.value = query || '';
    target.render();
    search.focus();
  } else {
    target.render();
  }
  if (section) section.scrollIntoView({ block: 'start' });
}

/* ---------- 随访提醒 ---------- */

let remindersFocused = null;
let editingReminderId = null;
let reminderMedicationId = 0;
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
  reminderMedicationId = 0;
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
  reminderMedicationId = Number(item.medication_id) || 0;
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
  if (!els.remindersDrawer.contains(document.activeElement)) {
    els.remindersClose.focus();
  }
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

function frequencyRepeatDays(frequency) {
  const text = String(frequency || '');
  if (/(每两月|每2月|每两个月)/.test(text)) return 60;
  if (/(每两周|每2周|每两星期|每两个星期|每隔一周|隔周)/.test(text)) {
    return 14;
  }
  // “每隔1天/隔日”表示每 2 天一次，必须先于通用数字天数匹配
  if (/(隔日|隔天|每隔一天|每隔1天|每两天|每二天)/.test(text)) return 2;
  const everyDays = text.match(/每\s*隔?\s*(\d+)\s*天/);
  if (everyDays) {
    return Math.max(1, Math.min(365, Number(everyDays[1]) || 1));
  }
  if (/(每周|每星期|每礼拜)/.test(text)) return 7;
  if (/(每月|每个月)/.test(text)) return 30;
  if (/(每日|每天|每晚|每早|一日|睡前|晨起)/.test(text)) return 1;
  return 0;
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
    cell.dataset.date = date;
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
      // 月历会整棵重建，焦点会掉回 body；尽量恢复到选中/今天
      const focusTarget = grid.querySelector(
        `[data-date="${reminderCalendarDay}"]`
      ) || grid.querySelector('.reminders-calendar-day.is-today')
        || grid.querySelector('.reminders-calendar-day');
      if (focusTarget) focusTarget.focus();
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
    if (String(item.title || '').startsWith('用药：')) {
      const medBadge = document.createElement('span');
      medBadge.className = 'reminder-badge reminder-badge-med';
      medBadge.textContent = '用药';
      meta.append(' ', medBadge);
    }
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

      const snooze = document.createElement('select');
      snooze.className = 'metric-edit reminder-snooze';
      snooze.title = '选择推迟天数';
      snooze.setAttribute('aria-label', `推迟提醒：${item.title}`);
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '推迟…';
      snooze.appendChild(placeholder);
      for (const days of [1, 3, 7, 14]) {
        const option = document.createElement('option');
        option.value = String(days);
        option.textContent = `${days} 天`;
        snooze.appendChild(option);
      }
      snooze.value = '';
      snooze.addEventListener('change', () => {
        const days = Number(snooze.value);
        snooze.value = '';
        if (days > 0) snoozeReminder(item, days);
      });

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
  // 用全量缓存判断条目是否真的被删除：搜索/逾期/日历筛选
  // 只是临时隐藏，不应清空正在编辑的表单
  if (
    editingReminderId
    && !reminderItemsCache.some((item) => item.id === editingReminderId)
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

async function maybeNotifyDueReminders(items) {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return;
  }
  const today = todayLocalDate();
  try {
    if (safeStorageGet(REMINDER_NOTIFIED_KEY) === today) return;
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
  const options = {
    body,
    tag: 'medix-reminders',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
  };
  try {
    // 移动端浏览器不允许 new Notification，必须走 Service Worker；
    // 桌面端没有可用 Worker 时再回退到构造函数
    let registration = null;
    if ('serviceWorker' in navigator) {
      registration = await Promise.race([
        navigator.serviceWorker.ready,
        new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
      ]);
    }
    if (registration && typeof registration.showNotification === 'function') {
      await registration.showNotification('MediX 随访提醒', options);
    } else {
      new Notification('MediX 随访提醒', options);
    }
    safeStorageSet(REMINDER_NOTIFIED_KEY, today);
  } catch {
    // 发送失败不记录“已提醒”，下次刷新重试
    setRemindersStatus('浏览器未能发送到期通知，请检查站点通知权限。', true);
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

function renderTodayMeds(medications) {
  if (!els.todayMeds) return;
  if (!medications) {
    els.todayMeds.hidden = true;
    els.todayMedsList.replaceChildren();
    return;
  }
  const active = medications.filter((item) => item.active);
  if (!active.length) {
    els.todayMeds.hidden = true;
    els.todayMedsList.replaceChildren();
    return;
  }
  const todayDoses = active.reduce(
    (total, item) => total + Number(item.today_count || 0), 0
  );
  const allDone = active.every((item) => Number(item.today_count) > 0);
  els.todayMedsSummary.textContent = allDone
    ? `今日均已打卡 · 共 ${todayDoses} 次`
    : (todayDoses > 0
      ? `今日已服 ${todayDoses} 次`
      : '今天还没有打卡');
  els.todayMedsSummary.classList.toggle('is-complete', allDone);
  els.todayMedsList.replaceChildren();
  for (const item of active) {
    const li = document.createElement('li');
    li.className = 'today-meds-item';

    const name = document.createElement('span');
    name.className = 'today-meds-name';
    const strong = document.createElement('strong');
    strong.textContent = item.name;
    const meta = document.createElement('span');
    meta.className = 'today-meds-meta';
    const stockAlert = item.out_of_stock
      ? '缺货'
      : item.low_stock
        ? `库存 ${Number(item.stock).toLocaleString('zh-CN', {
          maximumFractionDigits: 2,
        })}${item.stock_unit || ''}`
        : '';
    const expiryAlert = item.expired
      ? `已过期 ${item.expiry_date}`
      : item.expiring_soon
        ? `有效期 ${item.expiry_date}`
        : '';
    meta.textContent = [
      item.dose,
      item.profile,
      Number(item.today_count) > 0
        ? `今日 ${Number(item.today_count)} 次`
        : '今日未打卡',
      Number(item.skipped_today) > 0
        ? `今日未服 ${Number(item.skipped_today)} 次`
        : '',
      stockAlert,
      expiryAlert,
    ].filter(Boolean).join(' · ');
    name.append(strong, meta);

    const actions = document.createElement('span');
    actions.className = 'today-meds-actions';
    if (Number(item.today_count) > 0) {
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'btn-quiet today-meds-log';
      undo.textContent = '撤销';
      undo.setAttribute('aria-label', `撤销最近一次用药记录：${item.name}`);
      undo.addEventListener('click', () => undoMedicationLog(item));
      actions.appendChild(undo);
    }

    const log = document.createElement('button');
    log.type = 'button';
    log.className = 'btn-quiet today-meds-log';
    log.textContent = '打卡';
    log.setAttribute('aria-label', `记录一次用药：${item.name}`);
    log.addEventListener('click', () => logMedication(item));
    actions.appendChild(log);

    li.append(name, actions);
    els.todayMedsList.appendChild(li);
  }
  els.todayMeds.hidden = false;
}

function setTodayMedsStatus(text, isError = false) {
  if (!els.todayMedsStatus) return;
  els.todayMedsStatus.hidden = !text;
  els.todayMedsStatus.textContent = text || '';
  els.todayMedsStatus.classList.toggle('error', Boolean(isError));
}

function renderHomeGlance(
  reminders, metrics, symptoms, medications, weekly, insights, profile
) {
  if (!els.homeGlance) return;

  const pending = (reminders || []).filter((item) => !item.done);
  const overdue = pending.filter(
    (item) => Number(reminderDaysUntil(item.due_at)) < 0
  ).length;
  const dueToday = pending.filter(
    (item) => String(item.due_at || "").slice(0, 10) === todayLocalDate()
  ).length;
  const medsPending = (medications || []).filter(
    (item) => item.active && !Number(item.today_count)
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
        + (upcoming[0].profile ? `（${upcoming[0].profile}）` : '')
      : '点击打开随访提醒';
  } else if (upcoming.length) {
    reminderValue = `下一条：${formatReminderDue(upcoming[0].due_at)}`;
    reminderSub = (upcoming[0].title || '点击打开随访提醒')
      + (upcoming[0].profile ? ` · ${upcoming[0].profile}` : '');
  }

  const latest = {};
  for (const item of metrics || []) {
    if (!latest[item.metric]) latest[item.metric] = item;
  }
  const recent = Object.values(latest)
    .sort(
      (a, b) => String(b.measured_at).localeCompare(String(a.measured_at))
    )
    .slice(0, 3)
    .map(
      (item) => `${METRIC_NAMES[item.metric]} ${formatMetricValue(item)}`
        + (item.profile ? `（${item.profile}）` : '')
    );
  const metricValue = recent.length ? recent.join(' · ') : '暂无健康记录';
  const metricSub = recent.length ? '点击打开健康记录' : '点击录入第一条记录';

  const latestSymptom = (symptoms || [])[0];
  const symptomValue = latestSymptom
    ? `${latestSymptom.symptom} ${latestSymptom.severity}/10`
    : '暂无症状记录';
  const symptomSub = latestSymptom
    ? [formatTime(latestSymptom.recorded_at), latestSymptom.profile || '未指定档案']
      .join(' · ')
    : '点击记录第一条症状';

  els.homeGlanceReminders.replaceChildren(
    ...homeGlanceLine('随访提醒', reminderValue, reminderSub)
  );
  if (els.homeGlanceToday) {
    const todoParts = [];
    if (medsPending) todoParts.push(`${medsPending} 种药物待打卡`);
    const reminderDue = overdue + dueToday;
    if (reminderDue) todoParts.push(`${reminderDue} 条提醒待处理`);
    els.homeGlanceToday.replaceChildren(
      ...homeGlanceLine(
        '今日待办',
        todoParts.length ? todoParts.join(' · ') : '今日暂无待办',
        todoParts.length ? '点击打开随访提醒' : '保持记录好习惯'
      )
    );
  }
  els.homeGlanceMetrics.replaceChildren(
    ...homeGlanceLine('健康记录', metricValue, metricSub)
  );
  if (symptoms === null) {
    els.homeGlanceSymptoms.hidden = true;
  } else {
    els.homeGlanceSymptoms.hidden = false;
    els.homeGlanceSymptoms.replaceChildren(
      ...homeGlanceLine('症状日记', symptomValue, symptomSub)
    );
  }
  if (medications === null) {
    els.homeGlanceMedications.hidden = true;
  } else {
    const active = medications.filter((item) => item.active);
    const todayDoses = active.reduce(
      (total, item) => total + Number(item.today_count || 0), 0
    );
    const medValue = active.length
      ? `${active.length} 种在用药物`
      : '暂无在用药物';
    const medSub = active.length
      ? (todayDoses > 0 ? `今日已服 ${todayDoses} 次 · ` : '')
        + active
          .slice(0, 3)
          .map((item) => (
            item.name
            + (item.dose ? ` ${item.dose}` : '')
            + (item.profile ? `（${item.profile}）` : '')
          ))
          .join(' · ')
      : '点击添加用药清单';
    els.homeGlanceMedications.hidden = false;
    els.homeGlanceMedications.replaceChildren(
      ...homeGlanceLine('用药清单', medValue, medSub)
    );
  }
  if (weekly) {
    const days = Number(weekly.days) || 7;
    const metricCount = Number(weekly.metrics.count) || 0;
    const symptomCount = Number(weekly.symptoms.count) || 0;
    const doseCount = Number(weekly.medications.doses) || 0;
    const consultCount = Number(
      weekly.consultations ? weekly.consultations.count : 0
    ) || 0;
    const streakDays = Number(
      weekly.streak ? weekly.streak.current : 0
    ) || 0;
    els.homeGlanceSummary.hidden = false;
    els.homeGlanceSummary.replaceChildren(
      ...homeGlanceLine(
        '健康小结',
        `近 ${days} 天：指标 ${metricCount} 条`,
        `全部档案 · 症状 ${symptomCount} 条 · 打卡 ${doseCount} 次`
          + (consultCount ? ` · 会诊 ${consultCount} 次` : '')
          + (streakDays >= 2 ? ` · 连续记录 ${streakDays} 天` : '')
      )
    );
  } else {
    els.homeGlanceSummary.hidden = true;
  }
  if (els.homeGlanceInsight) {
    const top = insights && (insights.insights || [])[0];
    if (!top) {
      els.homeGlanceInsight.hidden = true;
      els.homeGlanceInsight.classList.remove('is-alert');
    } else {
      const levelLabel = INSIGHT_LEVEL_LABELS[top.level] || '提示';
      els.homeGlanceInsight.hidden = false;
      els.homeGlanceInsight.classList.toggle(
        'is-alert',
        top.level === 'danger' || top.level === 'warning'
      );
      els.homeGlanceInsight.replaceChildren(
        ...homeGlanceLine(
          profile ? `健康提示 · ${profile}` : '健康提示',
          top.title || '暂无需要关注',
          `${levelLabel}`
          + (top.detail ? ` · ${top.detail}` : '')
          + ' · 点击查看健康提示'
        )
      );
    }
  }
  if (els.homeGlanceBackup) {
    const lastBackup = readLastBackupAt();
    let backupValue = '还没有导出备份';
    let backupSub = '点击导出完整备份';
    if (lastBackup) {
      const days = backupDaysAgo(lastBackup);
      backupValue = `上次备份 ${formatTime(lastBackup)}`;
      if (days !== null && days >= 30) {
        backupSub = `已 ${days} 天未备份，建议现在导出`;
      }
    }
    els.homeGlanceBackup.replaceChildren(
      ...homeGlanceLine('数据备份', backupValue, backupSub)
    );
  }
  renderTodayMeds(medications);
  els.homeGlance.hidden = false;
}

async function loadHomeGlance() {
  if (!els.homeGlance) return;
  const requestSeq = ++homeGlanceRequestSeq;
  const profile = els.profileSelect ? els.profileSelect.value || '' : '';
  const insightsUrl = profile
    ? `/api/health/insights?days=30`
      + `&profile=${encodeURIComponent(profile)}`
    : '/api/health/insights?days=30';
  try {
    const [
      remindersRes, metricsRes, symptomsRes, medicationsRes, weeklyRes,
      insightsRes,
    ] = await Promise.all([
      fetch('/api/reminders'),
      fetch('/api/metrics/latest'),
      fetch('/api/symptoms?limit=1').catch(() => null),
      fetch('/api/medications?active_only=true&limit=50').catch(() => null),
      fetch('/api/reports/weekly').catch(() => null),
      fetch(insightsUrl).catch(() => null),
    ]);
    if (!remindersRes.ok || !metricsRes.ok) {
      if (requestSeq === homeGlanceRequestSeq) {
        els.homeGlance.hidden = true;
        if (els.todayMeds) els.todayMeds.hidden = true;
      }
      return;
    }
    const reminders = (await remindersRes.json()).reminders || [];
    const metrics = (await metricsRes.json()).metrics || [];
    const symptomsPayload = symptomsRes && symptomsRes.ok
      ? await symptomsRes.json().catch(() => null)
      : null;
    const symptoms = symptomsPayload
      ? symptomsPayload.symptoms || []
      : null;
    const medicationsPayload = medicationsRes && medicationsRes.ok
      ? await medicationsRes.json().catch(() => null)
      : null;
    const medications = medicationsPayload
      ? medicationsPayload.medications || []
      : null;
    const weeklyPayload = weeklyRes && weeklyRes.ok
      ? await weeklyRes.json().catch(() => null)
      : null;
    const insightsPayload = insightsRes && insightsRes.ok
      ? await insightsRes.json().catch(() => null)
      : null;
    if (requestSeq !== homeGlanceRequestSeq) return;
    renderHomeGlance(
      reminders, metrics, symptoms, medications, weeklyPayload,
      insightsPayload, profile
    );
  } catch {
    if (requestSeq === homeGlanceRequestSeq) {
      els.homeGlance.hidden = true;
    }
  }
}

let familyOverviewRequestSeq = 0;
let familyOverviewCache = null;

const HEALTH_RISK_LABELS = {
  emergency: '紧急',
  high: '高危',
  medium: '中危',
  low: '低危',
};

function familyContextText(context) {
  if (!context || typeof context !== 'object') return '';
  const parts = [];
  const age = Number.parseInt(context.age, 10);
  if (Number.isFinite(age) && age > 0) parts.push(`${age} 岁`);
  if (context.sex) parts.push(context.sex);
  return parts.join(' · ');
}

function familyChip(text, extraClass) {
  const chip = document.createElement('span');
  chip.className = 'family-chip' + (extraClass ? ` ${extraClass}` : '');
  chip.textContent = text;
  return chip;
}

function renderFamilyOverview(data) {
  if (!els.familyOverview || !els.familyOverviewList) return;
  const profiles = data.profiles || [];
  els.familyOverviewList.replaceChildren();
  if (!profiles.length) {
    els.familyOverview.hidden = true;
    return;
  }
  const days = Number(data.days) || 7;
  for (const profile of profiles) {
    const li = document.createElement('li');
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'family-card';
    card.dataset.profile = profile.name;
    const saved = Boolean(profilesCache[profile.name]);
    if (saved) {
      card.title = `切换到档案「${profile.name}」`;
    } else {
      card.disabled = true;
      card.title = '该档案已删除，仅保留历史记录';
    }

    const head = document.createElement('span');
    head.className = 'family-card-head';
    const name = document.createElement('strong');
    name.textContent = profile.name;
    head.appendChild(name);
    const attention = profile.attention || {};
    if (attention.level && attention.level !== 'ok') {
      const badge = document.createElement('span');
      badge.className = `family-attention-badge is-${attention.level}`;
      badge.textContent = attention.label || '';
      if (attention.detail) badge.title = attention.detail;
      head.appendChild(badge);
    }
    const contextText = familyContextText(profile.context);
    if (contextText) {
      const meta = document.createElement('span');
      meta.className = 'family-card-meta';
      meta.textContent = contextText;
      head.appendChild(meta);
    }
    if (!saved) {
      const gone = document.createElement('span');
      gone.className = 'family-card-gone';
      gone.textContent = '已删除';
      head.appendChild(gone);
    }
    card.appendChild(head);

    const chips = document.createElement('span');
    chips.className = 'family-card-chips';
    for (const item of (profile.metrics || []).slice(0, 3)) {
      const unit = item.unit ? ` ${item.unit}` : '';
      const critical = Array.isArray(item.critical_alerts)
        && item.critical_alerts.length > 0;
      chips.appendChild(familyChip(
        `${item.label} ${item.value}${unit}`
        + (critical ? ' 危急' : item.over_target ? ' 超标' : ''),
        critical || item.over_target ? 'family-chip-alert' : ''
      ));
    }
    const criticalInfo = profile.critical_alerts || {};
    if (criticalInfo.count) {
      const names = (criticalInfo.items || []).filter(Boolean);
      chips.appendChild(familyChip(
        `危急值 ${criticalInfo.count} 项`
        + (names.length ? ` · ${names.join('；')}` : ''),
        'family-chip-alert'
      ));
    }
    const care = profile.care_plan || {};
    const nextCare = care.next || {};
    if (care.overdue || care.due_soon || nextCare.title) {
      let careText = '复查';
      if (care.overdue) careText += ` 逾期 ${care.overdue} 项`;
      if (care.due_soon) careText += ` 近 7 天 ${care.due_soon} 项`;
      if (nextCare.title) {
        careText += ` · ${nextCare.title} ${nextCare.next_due_at || ''}`;
      }
      chips.appendChild(familyChip(
        careText,
        care.overdue ? 'family-chip-alert' : ''
      ));
    }
    const visits = profile.visits || {};
    const latestVisit = visits.latest || {};
    if (latestVisit.visit_date) {
      chips.appendChild(familyChip(
        `就诊 ${latestVisit.visit_date}`
        + (latestVisit.diagnosis ? ` · ${latestVisit.diagnosis}` : '')
      ));
    }
    const conditions = profile.conditions || {};
    if (conditions.count) {
      const names = (conditions.names || []).filter(Boolean);
      chips.appendChild(familyChip(
        `慢病 ${conditions.count} 项`
        + (names.length ? ` · ${names.join('、')}` : '')
      ));
    }
    const medications = profile.medications || {};
    if (medications.active) {
      const chip = familyChip(
        medications.taken_today
          ? `用药 今日 ${medications.taken_today} 次`
          : `用药 ${medications.active} 种 未打卡`
      );
      if (medications.adherence && medications.adherence.expected) {
        chip.textContent += ` · 依从率 ${medications.adherence.rate}%`;
      }
      if (Number(medications.skipped_today) > 0) {
        chip.textContent +=
          ` · 今日未服 ${Number(medications.skipped_today)} 次`;
      }
      const details = [];
      if (medications.names && medications.names.length) {
        details.push(medications.names.join('、'));
      }
      if (medications.last_taken_at) {
        details.push(`最近 ${formatTime(medications.last_taken_at)}`);
      }
      if (details.length) chip.title = details.join(' · ');
      chips.appendChild(chip);
    }
    const reminders = profile.reminders || {};
    if (reminders.overdue) {
      chips.appendChild(familyChip(
        `提醒 ${reminders.overdue} 条逾期`, 'family-chip-alert'
      ));
    } else if (reminders.due_today) {
      chips.appendChild(familyChip(`提醒 今天 ${reminders.due_today} 条`));
    } else if (reminders.pending) {
      chips.appendChild(familyChip(`提醒 ${reminders.pending} 条待办`));
    }
    const symptom = profile.symptoms || {};
    if (symptom.count) {
      const latest = symptom.latest || {};
      chips.appendChild(familyChip(
        `症状 近 ${days} 天 ${symptom.count} 条`
        + (latest.symptom ? ` · ${latest.symptom} ${latest.severity}/10` : '')
      ));
    }
    const appointment = profile.appointments || {};
    const nextVisit = appointment.next || {};
    if (nextVisit.title) {
      const when = String(nextVisit.scheduled_at || '').slice(0, 16)
        .replace('T', ' ');
      const daysUntil = nextVisit.days_until === null
        || nextVisit.days_until === undefined
        ? null
        : Number(nextVisit.days_until);
      chips.appendChild(familyChip(
        `就诊 ${nextVisit.title}`
        + (when ? ` · ${when}` : '')
        + (daysUntil === 0
          ? ' · 今天'
          : Number.isFinite(daysUntil) && daysUntil > 0
            ? ` · ${daysUntil} 天后`
            : '')
      ));
    }
    const labs = profile.labs || {};
    if (labs.critical) {
      const items = (labs.critical_items || []).filter(Boolean);
      const chip = familyChip(
        `化验危急值 ${labs.critical} 项`
        + (items.length ? ` · ${items.join('；')}` : '')
      );
      chip.className += ' family-chip-alert';
      chips.appendChild(chip);
    }
    if (labs.abnormal) {
      const items = (labs.items || []).filter(Boolean);
      chips.appendChild(familyChip(
        `化验异常 ${labs.abnormal} 项`
        + (items.length ? ` · ${items.join('；')}` : '')
      ));
    }
    const consultations = profile.consultations || {};
    if (consultations.count) {
      const chip = familyChip(
        `会诊 近 ${days} 天 ${consultations.count} 次`
      );
      const riskLabel = HEALTH_RISK_LABELS[consultations.risk];
      if (riskLabel) {
        chip.textContent += ` · ${riskLabel}`;
        if (
          consultations.risk === 'emergency'
          || consultations.risk === 'high'
        ) {
          chip.className += ' family-chip-alert';
        }
      }
      chips.appendChild(chip);
    }
    const streakDays = Number(profile.streak) || 0;
    if (streakDays >= 2) {
      chips.appendChild(familyChip(`连续 ${streakDays} 天`));
    }
    if (!chips.children.length) {
      chips.appendChild(familyChip(
        `近 ${days} 天暂无健康记录`, 'family-chip-muted'
      ));
    } else if (profile.recorded_today === false) {
      chips.appendChild(familyChip('今日未记录', 'family-chip-muted'));
    }
    card.appendChild(chips);

    if (profile.last_activity) {
      const foot = document.createElement('span');
      foot.className = 'family-card-foot';
      foot.textContent = `最近活动 ${formatTime(profile.last_activity)}`;
      card.appendChild(foot);
    }
    li.appendChild(card);
    els.familyOverviewList.appendChild(li);
  }
  els.familyOverview.hidden = false;
}

async function loadFamilyOverview() {
  if (!els.familyOverview) return;
  const requestSeq = ++familyOverviewRequestSeq;
  try {
    const res = await fetch('/api/family/overview?days=7');
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== familyOverviewRequestSeq) return;
    familyOverviewCache = data;
    renderFamilyOverview(data);
  } catch {
    if (requestSeq !== familyOverviewRequestSeq) return;
    els.familyOverview.hidden = true;
  }
}

function familyOverviewText(data) {
  const days = Number(data.days) || 7;
  const lines = [`家庭健康总览（近 ${days} 天）`];
  for (const profile of data.profiles || []) {
    const parts = [];
    const metrics = (profile.metrics || []).slice(0, 3);
    if (metrics.length) {
      parts.push(metrics.map((item) => (
        `${item.label} ${item.value}${item.unit ? ` ${item.unit}` : ''}`
        + (
          Array.isArray(item.critical_alerts)
          && item.critical_alerts.length
            ? '（危急）'
            : item.over_target ? '（超标）' : ''
        )
      )).join('、'));
    }
    const criticalInfo = profile.critical_alerts || {};
    if (criticalInfo.count) {
      const names = (criticalInfo.items || []).filter(Boolean);
      parts.push(
        `危急值 ${criticalInfo.count} 项`
        + (names.length ? `（${names.join('；')}）` : '')
      );
    }
    const medications = profile.medications || {};
    if (medications.active) {
      let medText = `用药 今日 ${medications.taken_today || 0} 次`;
      if (medications.adherence && medications.adherence.expected) {
        medText += `，依从率 ${medications.adherence.rate}%`;
      }
      if (Number(medications.skipped_today) > 0) {
        medText +=
          `，今日未服 ${Number(medications.skipped_today)} 次`;
      }
      parts.push(medText);
    }
    const reminders = profile.reminders || {};
    if (reminders.overdue) {
      parts.push(`提醒 ${reminders.overdue} 条逾期`);
    } else if (reminders.due_today) {
      parts.push(`提醒 今天 ${reminders.due_today} 条`);
    } else if (reminders.pending) {
      parts.push(`提醒 ${reminders.pending} 条待办`);
    }
    const symptom = profile.symptoms || {};
    if (symptom.count) parts.push(`症状 ${symptom.count} 条`);
    const appointment = profile.appointments || {};
    const nextVisit = appointment.next || {};
    if (nextVisit.title) {
      parts.push(`下次就诊 ${String(nextVisit.scheduled_at || '').slice(0, 10)}`);
    }
    const labs = profile.labs || {};
    if (labs.critical) parts.push(`化验危急值 ${labs.critical} 项`);
    if (labs.abnormal) parts.push(`化验异常 ${labs.abnormal} 项`);
    const care = profile.care_plan || {};
    if (care.overdue) {
      parts.push(`复查逾期 ${care.overdue} 项`);
    } else if (care.due_soon) {
      parts.push(`复查近 7 天 ${care.due_soon} 项`);
    }
    const visits = profile.visits || {};
    const latestVisit = visits.latest || {};
    if (latestVisit.visit_date) {
      parts.push(
        `最近就诊 ${latestVisit.visit_date}`
        + (latestVisit.diagnosis ? `（${latestVisit.diagnosis}）` : '')
      );
    }
    const conditions = profile.conditions || {};
    if (conditions.count) {
      const names = (conditions.names || []).filter(Boolean);
      parts.push(
        `慢病 ${conditions.count} 项`
        + (names.length ? `（${names.join('、')}）` : '')
      );
    }
    const consultations = profile.consultations || {};
    if (consultations.count) {
      const riskLabel = HEALTH_RISK_LABELS[consultations.risk];
      parts.push(
        `会诊 ${consultations.count} 次`
        + (riskLabel ? `（${riskLabel}）` : '')
      );
    }
    const context = familyContextText(profile.context);
    const name = profile.name || '未指定档案';
    lines.push(
      `${name}${context ? `（${context}）` : ''}：`
      + (parts.length ? parts.join('；') : '暂无健康记录')
    );
  }
  return lines.join('\n');
}

async function copyFamilyOverview() {
  if (!familyOverviewCache) {
    setFamilyOverviewStatus('暂无家庭健康数据可复制。');
    return;
  }
  const ok = await copyText(familyOverviewText(familyOverviewCache));
  setFamilyOverviewStatus(
    ok ? '已复制家庭健康总览。' : '复制失败，请手动选择文本。'
  );
}

function setFamilyOverviewStatus(text) {
  if (!els.familyOverviewStatus) return;
  els.familyOverviewStatus.textContent = text;
  els.familyOverviewStatus.hidden = !text;
}

async function exportFamilyOverviewCsv() {
  if (!els.familyOverviewExport) return;
  try {
    const res = await fetch('/api/family/overview/export.csv?days=7');
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-family-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setFamilyOverviewStatus('已导出家庭健康总览 CSV。');
  } catch (err) {
    setFamilyOverviewStatus(`导出失败：${err.message || err}`);
  }
}

function refreshOverview() {
  return Promise.all([
    loadHomeGlance(),
    loadWeeklySummary(),
    loadTimeline(),
    loadFamilyOverview(),
    loadHealthInsights(),
    loadDataQuality(),
  ]);
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
    updateClearDoneButton(reminderItemsCache);
    refreshReminderViews();
    refreshReminderBadge();
    refreshOverview();
    loadReminderStats();
  } catch (err) {
    if (requestSeq !== remindersRequestSeq) return;
    setRemindersStatus(`加载提醒失败：${err.message || err}`, true);
  }
}

let reminderStatsRequestSeq = 0;

async function loadReminderStats() {
  if (!els.reminderStats || !els.reminderStatsBar) return;
  const requestSeq = ++reminderStatsRequestSeq;
  const profile = els.remindersProfile ? els.remindersProfile.value : '';
  const days = Number(els.reminderStatsRange?.value) || 30;
  const params = new URLSearchParams({ days: String(days) });
  if (profile) params.set('profile', profile);
  try {
    const res = await fetch(`/api/reminders/stats?${params.toString()}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== reminderStatsRequestSeq) return;
    els.reminderStatsBar.hidden = false;
    if (!data.due_total) {
      els.reminderStats.textContent = `近 ${days} 天暂无到期提醒。`;
      return;
    }
    els.reminderStats.textContent =
      `近 ${days} 天到期 ${data.due_total} 条 · 已完成 ${data.done_total} 条`
      + (
        data.completion_rate !== null
          ? `（${data.completion_rate}%）`
          : ''
      )
      + ` · 待办 ${data.pending_total} 条`
      + (
        data.overdue_total
          ? `（逾期 ${data.overdue_total} 条）`
          : ''
      );
  } catch {
    if (requestSeq !== reminderStatsRequestSeq) return;
    els.reminderStatsBar.hidden = true;
  }
}

function updateClearDoneButton(reminders) {
  if (!els.remindersClearDone) return;
  const count = (reminders || []).filter((item) => item.done).length;
  els.remindersClearDone.hidden = count === 0;
  els.remindersClearDone.textContent = count
    ? `清理已完成（${count}）`
    : '清理已完成';
}

async function clearDoneReminders() {
  const done = reminderItemsCache.filter((item) => item.done);
  if (!done.length) {
    setRemindersStatus('没有已完成的提醒。');
    return;
  }
  if (!window.confirm(`删除 ${done.length} 条已完成的提醒？`)) return;
  let deleted = 0;
  for (const item of done) {
    try {
      const res = await fetch(`/api/reminders/${item.id}`, {
        method: 'DELETE',
      });
      if (res.ok) deleted += 1;
    } catch {
      // 单条删除失败时继续处理其余记录
    }
  }
  setRemindersStatus(`已清理 ${deleted} 条已完成提醒。`);
  await loadReminders();
  refreshReminderBadge();
  refreshOverview();
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
    medication_id: reminderMedicationId,
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
  const repeatDays = Math.trunc(Math.max(
    0,
    Math.min(365, Number(els.reminderRepeat.value) || 0)
  ));
  const payload = {
    title,
    due_at: dueAt,
    note: (els.reminderNote.value || '').trim(),
    profile: els.reminderProfile.value || '',
    advance_days: Math.trunc(Math.max(
      0,
      Math.min(30, Number(els.reminderAdvance.value) || 0)
    )),
    repeat_days: repeatDays,
    repeat_until: repeatDays > 0 ? (els.reminderUntil.value || '') : '',
  };
  if (!editingId) {
    payload.session_id = sessionId || '';
    payload.medication_id = reminderMedicationId;
  }

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

async function completeDueReminders() {
  const button = els.remindersCompleteDue;
  if (!button || button.disabled || reminderActionBusy) return;
  const today = todayLocalDate();
  const due = (reminderItemsCache || []).filter(
    (item) => !item.done && String(item.due_at || '').slice(0, 10) <= today
  );
  if (!due.length) {
    setRemindersStatus('今天没有到期或逾期的提醒。');
    return;
  }
  if (!window.confirm(
    `将 ${due.length} 条今天到期或已逾期的提醒标记为完成？`
  )) {
    return;
  }

  reminderActionBusy = true;
  button.disabled = true;
  button.textContent = '处理中…';
  let success = 0;
  let failed = 0;
  try {
    for (const item of due) {
      try {
        const res = await fetch(`/api/reminders/${item.id}/complete`, {
          method: 'POST',
        });
        if (!res.ok) throw new Error(`服务返回 ${res.status}`);
        success += 1;
      } catch {
        failed += 1;
      }
    }
  } finally {
    reminderActionBusy = false;
    button.disabled = false;
    button.textContent = '完成今日到期';
  }

  const message = `已完成 ${success} 条提醒`
    + (failed ? `，${failed} 条失败` : '')
    + '。';
  setRemindersStatus(message, failed > 0);
  await loadReminders();
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

async function snoozeReminder(item, days = 7) {
  if (reminderActionBusy) {
    setRemindersStatus('正在处理上一条提醒操作，请稍候。');
    return;
  }
  const snoozeDays = Math.max(1, Math.min(365, Number(days) || 7));
  reminderActionBusy = true;
  try {
    const res = await fetch(
      `/api/reminders/${item.id}/snooze?days=${snoozeDays}`,
      { method: 'POST' }
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json().catch(() => ({}));
    const due = data.reminder ? formatReminderDue(data.reminder.due_at) : '';
    setRemindersStatus(
      due ? `已推迟 ${snoozeDays} 天到 ${due}。` : `已推迟 ${snoozeDays} 天。`
    );
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
  // 导出跟随当前搜索、逾期与日历筛选，避免文件与界面不一致
  const query = els.remindersSearch
    ? els.remindersSearch.value.trim()
    : '';
  if (query) params.set('q', query);
  if (els.remindersOverdueOnly && els.remindersOverdueOnly.checked) {
    params.set('overdue', 'true');
  }
  if (reminderCalendarDay) params.set('date', reminderCalendarDay);

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
  els.metricContext.value = '';
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
  if (els.remindersOverdueOnly) {
    els.remindersOverdueOnly.checked = false;
  }
  reminderCalendarDay = '';
  if (els.remindersProfile) {
    els.remindersProfile.value = item.profile || '';
    loadReminders();
  }
  setRemindersStatus('已代入复查提醒，确认后点击「添加提醒」。');
  els.reminderTitle.focus();
}

async function restockMedication(item) {
  const unit = item.stock_unit || '单位';
  const current = item.stock === null || item.stock === undefined
    ? ''
    : String(item.stock);
  const input = window.prompt(
    `更新「${item.name}」的剩余库存（${unit}），留空表示不管理库存`,
    current
  );
  if (input === null) return;
  const text = input.trim();
  let stock = null;
  if (text) {
    const value = Number(text);
    if (!Number.isFinite(value) || value < 0) {
      setMedicationStatus('库存应为非负数字。', true);
      return;
    }
    stock = value;
  }
  try {
    const res = await fetch(`/api/medications/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setMedicationStatus(
      stock === null ? '已关闭库存管理。' : '已更新库存。'
    );
    await Promise.all([loadMedications(), loadMedicationLogs()]);
    refreshOverview();
  } catch (err) {
    setMedicationStatus(`更新库存失败：${err.message || err}`, true);
  }
}

async function createMedicationAlertReminder(item, kind) {
  const label = kind === 'stock' ? '补货提醒' : '到期提醒';
  try {
    const res = await fetch(
      `/api/medications/${item.id}/reminder?kind=${kind}`,
      { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setMedicationStatus(
      data.created
        ? `已创建${label}。`
        : `该药物已有未完成的${label}。`
    );
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setMedicationStatus(`创建${label}失败：${err.message || err}`, true);
  }
}

async function scheduleMedicationReminder(item) {
  await openReminders();
  reminderMedicationId = Number(item.id) || 0;
  if (els.reminderProfile) {
    els.reminderProfile.value = item.profile || '';
  }
  els.reminderTitle.value = `用药：${item.name}`;
  els.reminderDue.value = localDateAfter(0);
  els.reminderAdvance.value = '0';
  els.reminderRepeat.value = String(frequencyRepeatDays(item.frequency));
  syncReminderRepeatFields();
  const details = [item.dose, item.frequency].filter(Boolean).join(' · ');
  els.reminderNote.value = details ? `用法：${details}` : '';
  if (els.remindersSearch) els.remindersSearch.value = '';
  if (els.remindersOverdueOnly) {
    els.remindersOverdueOnly.checked = false;
  }
  reminderCalendarDay = '';
  if (els.remindersProfile) {
    els.remindersProfile.value = item.profile || '';
    loadReminders();
  }
  setRemindersStatus('已代入用药提醒，确认后点击「添加提醒」。');
  els.reminderTitle.focus();
}

async function scheduleAllMedicationReminders() {
  if (medicationReminderBusy) return;
  const active = medicationItemsCache.filter((item) => item.active);
  if (!active.length) {
    setMedicationStatus('用药清单中没有在用药物。', true);
    return;
  }
  medicationReminderBusy = true;
  if (els.medicationReminders) els.medicationReminders.disabled = true;
  try {
    let reminders = null;
    try {
      const res = await fetch('/api/reminders');
      if (!res.ok) throw new Error(`服务返回 ${res.status}`);
      reminders = (await res.json()).reminders || [];
    } catch (err) {
      setMedicationStatus(
        `读取现有提醒失败：${err.message || err}`, true
      );
      return;
    }
    if (!window.confirm(
      `为 ${active.length} 种在用药物各创建一条用药提醒？`
      + '已存在或频次无法识别的会跳过。'
    )) {
      return;
    }
    let created = 0;
    let skipped = 0;
    let invalid = 0;
    let failed = 0;
    for (const item of active) {
      const repeatDays = frequencyRepeatDays(item.frequency);
      if (!repeatDays) {
        invalid += 1;
        continue;
      }
      const title = `用药：${item.name}`;
      const exists = reminders.some((reminder) => (
        !reminder.done
        && (
          Number(reminder.medication_id) === item.id
          || (
            !Number(reminder.medication_id)
            && reminder.title === title
            && (reminder.profile || '') === (item.profile || '')
          )
        )
      ));
      if (exists) {
        skipped += 1;
        continue;
      }
      try {
        const res = await fetch('/api/reminders', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title,
            note: [item.dose, item.frequency].filter(Boolean).join(' · '),
            due_at: localDateAfter(0),
            profile: item.profile || '',
            advance_days: 0,
            repeat_days: repeatDays,
            medication_id: item.id,
          }),
        });
        if (!res.ok) throw new Error(`服务返回 ${res.status}`);
        reminders.push({
          title,
          profile: item.profile || '',
          medication_id: item.id,
          done: false,
        });
        created += 1;
      } catch {
        failed += 1;
      }
    }
    const parts = [`已创建 ${created} 条用药提醒`];
    if (skipped) parts.push(`跳过 ${skipped} 条已存在`);
    if (invalid) parts.push(`${invalid} 条频次无法识别`);
    if (failed) parts.push(`${failed} 条创建失败`);
    setMedicationStatus(
      parts.join('，') + '。', created === 0 && failed > 0
    );
    if (created) refreshOverview();
    refreshReminderBadge();
  } finally {
    medicationReminderBusy = false;
    if (els.medicationReminders) els.medicationReminders.disabled = false;
  }
}

let editingSymptomId = null;
let symptomSaving = false;
let symptomRequestSeq = 0;
let symptomItemsCache = [];
let editingSymptomProfile = '';

function setSymptomStatus(text, isError = false) {
  if (!els.symptomStatus) return;
  els.symptomStatus.textContent = text || '';
  els.symptomStatus.hidden = !text;
  els.symptomStatus.classList.toggle('error', Boolean(isError));
}

function resetSymptomForm() {
  editingSymptomId = null;
  editingSymptomProfile = '';
  els.symptomText.value = '';
  els.symptomSeverity.value = '0';
  els.symptomNote.value = '';
  els.symptomTime.value = metricNowLocal();
  els.symptomSave.textContent = '记录症状';
  els.symptomCancel.hidden = true;
}

function filterSymptoms(items) {
  const query = els.symptomSearch
    ? String(els.symptomSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [item.symptom, item.note, item.profile]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderSymptomTrend(items) {
  const chart = els.symptomTrendChart;
  if (!chart) return;
  chart.replaceChildren();
  if (!items || items.length < 2) {
    els.symptomTrend.hidden = true;
    return;
  }
  els.symptomTrend.hidden = false;

  const chronological = items.slice().reverse();
  const width = 320;
  const height = 80;
  const left = 24;
  const right = 8;
  const top = 8;
  const bottom = 14;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const x = (index) => left + (plotW * index) / (chronological.length - 1);
  const y = (value) => top + plotH * (1 - Number(value) / 10);

  for (const value of [0, 5, 10]) {
    chart.appendChild(svgEl('line', {
      x1: left,
      y1: y(value),
      x2: width - right,
      y2: y(value),
      class: 'symptom-trend-grid',
    }));
    const label = svgEl('text', {
      x: left - 4,
      y: y(value) + 3,
      class: 'symptom-trend-axis',
      'text-anchor': 'end',
    });
    label.textContent = String(value);
    chart.appendChild(label);
  }

  const points = chronological
    .map((item, index) => `${x(index).toFixed(1)},${y(item.severity).toFixed(1)}`)
    .join(' ');
  chart.appendChild(svgEl('polyline', {
    points,
    class: 'symptom-trend-line',
  }));

  chronological.forEach((item, index) => {
    const dot = svgEl('circle', {
      cx: x(index),
      cy: y(item.severity),
      r: 2.6,
      class: 'symptom-trend-dot',
    });
    const title = svgEl('title');
    title.textContent =
      `${item.symptom} ${item.severity}/10 · ${formatTime(item.recorded_at)}`;
    dot.appendChild(title);
    chart.appendChild(dot);
  });
}

function renderSymptoms(items) {
  if (!els.symptomList) return;
  els.symptomList.replaceChildren();
  els.symptomEmpty.hidden = items.length > 0;
  const query = els.symptomSearch
    ? String(els.symptomSearch.value || '').trim()
    : '';
  els.symptomEmpty.textContent = query
    ? `没有匹配「${query}」的症状记录。`
    : '还没有症状记录。';
  const days = metricRangeDays();
  if (items.length) {
    const average = items.reduce(
      (total, item) => total + Number(item.severity || 0), 0
    ) / items.length;
    els.symptomSummary.hidden = false;
    const scope = query
      ? '筛选'
      : (days > 0 ? `近 ${days} 天 ` : '全部 ');
    els.symptomSummary.textContent =
      `${scope}${items.length} 条`
      + ` · 平均 ${Math.round(average * 10) / 10}/10`;
  } else {
    els.symptomSummary.hidden = true;
  }
  renderSymptomTrend(items);
  if (
    editingSymptomId
    && !symptomItemsCache.some((item) => item.id === editingSymptomId)
  ) {
    resetSymptomForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.symptom;
    const severity = document.createElement('span');
    severity.className = 'symptom-severity '
      + (item.severity >= 7
        ? 'is-high'
        : item.severity >= 4 ? 'is-mid' : 'is-low');
    severity.textContent = `${item.severity} / 10`;
    title.append(' ', severity);

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      formatTime(item.recorded_at),
    ].join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑症状：${item.symptom}`);
    edit.addEventListener('click', () => editSymptom(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除症状：${item.symptom}`);
    del.addEventListener('click', () => deleteSymptom(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.symptomList.appendChild(li);
  }
}

async function loadSymptoms() {
  if (!els.symptomList) return;
  const requestSeq = ++symptomRequestSeq;
  const profile = els.metricProfile.value;
  const days = metricRangeDays();
  try {
    const res = await fetch(
      `/api/symptoms?limit=100000&profile=${encodeURIComponent(profile)}`
      + (days > 0 ? `&days=${days}` : '')
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== symptomRequestSeq) return;
    symptomItemsCache = data.symptoms || [];
    renderSymptoms(filterSymptoms(symptomItemsCache));
  } catch {
    // 加载失败必须清空旧档案的记录，避免把上一个患者的数据留在界面上
    if (requestSeq !== symptomRequestSeq) return;
    symptomItemsCache = [];
    renderSymptoms([]);
    els.symptomEmpty.textContent = '症状记录加载失败，请稍后重试。';
    els.symptomEmpty.hidden = false;
    setSymptomStatus('症状记录加载失败，请稍后重试。', true);
  }
}

async function importSymptomsCsv(file) {
  if (!file) return;
  setSymptomStatus('正在导入症状记录…');

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/symptoms/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复或时间无效`);
    if (data.invalid) parts.push(`格式错误 ${data.invalid} 行`);
    setSymptomStatus(parts.join('，') + '。', data.imported === 0);
    await refreshMetricProfileOptions();
    await loadSymptoms();
    refreshOverview();
  } catch (err) {
    setSymptomStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.symptomImportFile.value = '';
  }
}

async function exportSymptomsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile.value,
  });
  const days = metricRangeDays();
  if (days > 0) params.set('days', String(days));

  try {
    const res = await fetch(
      `/api/symptoms/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-symptoms-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setSymptomStatus('已导出症状 CSV。');
  } catch (err) {
    setSymptomStatus(`导出失败：${err.message || err}`, true);
  }
}

async function exportMedicationsCsv() {
  const params = new URLSearchParams({ profile: els.metricProfile.value });
  // 导出跟随用药列表的在用状态与搜索筛选
  const status = els.medicationStatusFilter
    ? els.medicationStatusFilter.value
    : '';
  if (status) params.set('status', status);
  const query = els.medicationSearch
    ? els.medicationSearch.value.trim()
    : '';
  if (query) params.set('q', query);
  try {
    const res = await fetch(
      `/api/medications/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-medications-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setMedicationStatus('已导出用药清单 CSV。');
  } catch (err) {
    setMedicationStatus(`导出失败：${err.message || err}`, true);
  }
}

async function exportMedicationLogsCsv() {
  const params = new URLSearchParams({ profile: els.metricProfile.value });
  try {
    const res = await fetch(
      `/api/medications/logs/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-medication-logs-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setMedicationStatus('已导出用药打卡 CSV。');
  } catch (err) {
    setMedicationStatus(`导出打卡失败：${err.message || err}`, true);
  }
}

async function importMedicationsCsv(file) {
  if (!file) return;
  setMedicationStatus('正在导入用药清单…');

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/medications/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }

    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复`);
    if (data.invalid) parts.push(`缺少药物名称 ${data.invalid} 行`);
    setMedicationStatus(parts.join('，') + '。', data.imported === 0);
    await refreshMetricProfileOptions();
    await loadMedications();
    refreshOverview();
  } catch (err) {
    setMedicationStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.medicationImportFile.value = '';
  }
}

function editSymptom(item) {
  editingSymptomId = item.id;
  editingSymptomProfile = item.profile || '';
  const hasProfile = Array.from(els.metricProfile.options)
    .some((option) => option.value === item.profile);
  if (hasProfile) els.metricProfile.value = item.profile;
  els.symptomText.value = item.symptom || '';
  els.symptomSeverity.value = String(item.severity || 0);
  els.symptomNote.value = item.note || '';
  els.symptomTime.value = String(item.recorded_at || '').slice(0, 16);
  els.symptomSave.textContent = '保存修改';
  els.symptomCancel.hidden = false;
  els.symptomText.focus();
}

async function saveSymptom() {
  if (symptomSaving) return;
  const symptom = (els.symptomText.value || '').trim();
  if (!symptom) {
    setSymptomStatus('请填写症状。', true);
    els.symptomText.focus();
    return;
  }
  const editingId = editingSymptomId;
  const payload = {
    profile: editingId
      ? editingSymptomProfile
      : (els.metricProfile.value || ''),
    symptom,
    severity: Number(els.symptomSeverity.value),
    recorded_at: els.symptomTime.value || metricNowLocal(),
    note: (els.symptomNote.value || '').trim(),
  };

  symptomSaving = true;
  els.symptomSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/symptoms/${editingId}` : '/api/symptoms',
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
    resetSymptomForm();
    setSymptomStatus(editingId ? '已保存修改。' : '已记录症状。');
    await loadSymptoms();
    refreshOverview();
  } catch (err) {
    setSymptomStatus(`保存失败：${err.message || err}`, true);
  } finally {
    symptomSaving = false;
    els.symptomSave.disabled = false;
  }
}

async function deleteSymptom(item) {
  if (!window.confirm(`删除症状「${item.symptom}」？`)) return;
  try {
    const res = await fetch(`/api/symptoms/${item.id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingSymptomId === item.id) resetSymptomForm();
    setSymptomStatus('已删除症状。');
    await loadSymptoms();
    refreshOverview();
  } catch (err) {
    setSymptomStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingVaccinationId = null;
let editingVaccinationProfile = '';
let vaccinationSaving = false;
let vaccinationRequestSeq = 0;
let vaccinationItemsCache = [];
let vaccinationSearchTimer = null;
let vaccinationReminderBusy = new Set();

function setVaccinationStatus(text, isError = false) {
  if (!els.vaccinationStatus) return;
  els.vaccinationStatus.textContent = text || '';
  els.vaccinationStatus.hidden = !text;
  els.vaccinationStatus.classList.toggle('error', Boolean(isError));
}

function resetVaccinationForm() {
  editingVaccinationId = null;
  editingVaccinationProfile = '';
  els.vaccinationName.value = '';
  els.vaccinationDose.value = '1';
  els.vaccinationDate.value = todayLocalDate();
  els.vaccinationNextDue.value = '';
  els.vaccinationNote.value = '';
  els.vaccinationSave.textContent = '保存接种记录';
  els.vaccinationCancel.hidden = true;
}

function vaccinationDueInfo(item) {
  const due = String(item.next_due || '').slice(0, 10);
  if (!due) return null;
  const today = todayLocalDate();
  if (due < today) {
    return { text: `下一剂 ${due} · 已逾期`, className: 'is-overdue' };
  }
  const diff = Math.round(
    (
      Date.parse(`${due}T00:00:00`)
      - Date.parse(`${today}T00:00:00`)
    ) / 86400000
  );
  if (diff === 0) {
    return { text: `下一剂 ${due} · 今天`, className: 'is-soon' };
  }
  if (diff <= 30) {
    return {
      text: `下一剂 ${due} · ${diff} 天后`,
      className: 'is-soon',
    };
  }
  return { text: `下一剂 ${due}`, className: '' };
}

function filterVaccinations(items) {
  const query = els.vaccinationSearch
    ? String(els.vaccinationSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [item.vaccine_name, item.note, item.profile]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderVaccinations(items) {
  if (!els.vaccinationList) return;
  els.vaccinationList.replaceChildren();
  els.vaccinationEmpty.hidden = items.length > 0;
  const query = els.vaccinationSearch
    ? String(els.vaccinationSearch.value || '').trim()
    : '';
  els.vaccinationEmpty.textContent = query
    ? `没有匹配「${query}」的疫苗接种记录。`
    : '还没有疫苗接种记录。';
  const today = todayLocalDate();
  const overdue = items.filter((item) => {
    const due = String(item.next_due || '').slice(0, 10);
    return due && due < today;
  }).length;
  if (items.length) {
    els.vaccinationSummary.hidden = false;
    const latest = items[0];
    els.vaccinationSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (latest && latest.vaccine_name
        ? ` · 最近 ${latest.vaccine_name}`
        : '')
      + (overdue ? ` · 逾期 ${overdue} 条` : '');
  } else {
    els.vaccinationSummary.hidden = true;
  }
  if (
    editingVaccinationId
    && !vaccinationItemsCache.some(
      (item) => item.id === editingVaccinationId
    )
  ) {
    resetVaccinationForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.vaccine_name || '疫苗';
    const dose = document.createElement('span');
    dose.className = 'vaccination-dose-badge';
    dose.textContent = `第 ${Number(item.dose_number) || 1} 剂`;
    title.append(' ', dose);

    const dueInfo = vaccinationDueInfo(item);
    if (dueInfo) {
      const dueBadge = document.createElement('span');
      dueBadge.className = 'vaccination-due-badge '
        + dueInfo.className;
      dueBadge.textContent = dueInfo.text;
      title.append(' ', dueBadge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      String(item.vaccinated_at || '').replace('T', ' ').slice(0, 16),
    ].join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (item.next_due) {
      const remind = document.createElement('button');
      remind.type = 'button';
      remind.className = 'metric-edit';
      remind.textContent = '提醒';
      remind.setAttribute(
        'aria-label',
        `为 ${item.vaccine_name} 下一剂设置提醒`
      );
      remind.addEventListener(
        'click', () => scheduleVaccinationReminder(item)
      );
      actions.appendChild(remind);
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute(
      'aria-label', `编辑疫苗记录：${item.vaccine_name}`
    );
    edit.addEventListener('click', () => editVaccination(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute(
      'aria-label', `删除疫苗记录：${item.vaccine_name}`
    );
    del.addEventListener('click', () => deleteVaccination(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.vaccinationList.appendChild(li);
  }
}

async function loadVaccinations() {
  if (!els.vaccinationList) return;
  const requestSeq = ++vaccinationRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/vaccinations?limit=100000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== vaccinationRequestSeq) return;
    vaccinationItemsCache = data.vaccinations || [];
    renderVaccinations(filterVaccinations(vaccinationItemsCache));
  } catch {
    if (requestSeq !== vaccinationRequestSeq) return;
    vaccinationItemsCache = [];
    renderVaccinations([]);
    els.vaccinationEmpty.textContent =
      '疫苗接种记录加载失败，请稍后重试。';
    els.vaccinationEmpty.hidden = false;
    setVaccinationStatus('疫苗接种记录加载失败，请稍后重试。', true);
  }
}

async function exportVaccinationsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/vaccinations/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-vaccinations-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setVaccinationStatus('已导出疫苗接种 CSV。');
  } catch (err) {
    setVaccinationStatus(`导出失败：${err.message || err}`, true);
  }
}

function editVaccination(item) {
  editingVaccinationId = item.id;
  editingVaccinationProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.vaccinationName.value = item.vaccine_name || '';
  els.vaccinationDose.value = String(item.dose_number || 1);
  els.vaccinationDate.value = String(item.vaccinated_at || '').slice(0, 10);
  els.vaccinationNextDue.value = String(item.next_due || '').slice(0, 10);
  els.vaccinationNote.value = item.note || '';
  els.vaccinationSave.textContent = '保存修改';
  els.vaccinationCancel.hidden = false;
  els.vaccinationName.focus();
}

async function saveVaccination() {
  if (vaccinationSaving) return;
  const name = (els.vaccinationName.value || '').trim();
  if (!name) {
    setVaccinationStatus('请填写疫苗名称。', true);
    els.vaccinationName.focus();
    return;
  }
  const vaccinatedAt = els.vaccinationDate.value || todayLocalDate();
  const nextDue = els.vaccinationNextDue.value || '';
  if (nextDue && nextDue < vaccinatedAt) {
    setVaccinationStatus('下一剂日期不能早于接种日期。', true);
    els.vaccinationNextDue.focus();
    return;
  }
  const editingId = editingVaccinationId;
  const payload = {
    profile: editingId
      ? editingVaccinationProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    vaccine_name: name,
    dose_number: Number(els.vaccinationDose.value) || 1,
    vaccinated_at: vaccinatedAt,
    next_due: nextDue,
    note: (els.vaccinationNote.value || '').trim(),
  };

  vaccinationSaving = true;
  els.vaccinationSave.disabled = true;
  try {
    const res = await fetch(
      editingId
        ? `/api/vaccinations/${editingId}`
        : '/api/vaccinations',
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
    resetVaccinationForm();
    setVaccinationStatus(
      editingId ? '已保存修改。' : '已保存接种记录。'
    );
    await loadVaccinations();
    refreshOverview();
  } catch (err) {
    setVaccinationStatus(`保存失败：${err.message || err}`, true);
  } finally {
    vaccinationSaving = false;
    els.vaccinationSave.disabled = false;
  }
}

async function deleteVaccination(item) {
  if (!window.confirm(`删除疫苗记录「${item.vaccine_name}」？`)) return;
  try {
    const res = await fetch(
      `/api/vaccinations/${item.id}`, { method: 'DELETE' }
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingVaccinationId === item.id) resetVaccinationForm();
    setVaccinationStatus('已删除疫苗记录。');
    await loadVaccinations();
    refreshOverview();
  } catch (err) {
    setVaccinationStatus(`删除失败：${err.message || err}`, true);
  }
}

async function scheduleVaccinationReminder(item) {
  if (!item.next_due) {
    setVaccinationStatus('请先填写下一剂日期。', true);
    return;
  }
  if (vaccinationReminderBusy.has(item.id)) return;
  vaccinationReminderBusy.add(item.id);
  setVaccinationStatus('正在创建接种提醒…');
  try {
    const res = await fetch(
      `/api/vaccinations/${item.id}/reminder`, { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setVaccinationStatus(
      data.created
        ? '已创建接种提醒（提前 7 天）。'
        : '该疫苗已有未完成的接种提醒。'
    );
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setVaccinationStatus(`创建提醒失败：${err.message || err}`, true);
  } finally {
    vaccinationReminderBusy.delete(item.id);
  }
}

let editingAppointmentId = null;
let editingAppointmentProfile = '';
let appointmentSaving = false;
let appointmentRequestSeq = 0;
let appointmentItemsCache = [];
let appointmentSearchTimer = null;
let appointmentReminderBusy = new Set();

const APPOINTMENT_STATUS_LABELS = {
  upcoming: '待就诊',
  completed: '已完成',
  cancelled: '已取消',
};

function setAppointmentStatusText(text, isError = false) {
  if (!els.appointmentStatusText) return;
  els.appointmentStatusText.textContent = text || '';
  els.appointmentStatusText.hidden = !text;
  els.appointmentStatusText.classList.toggle('error', Boolean(isError));
}

function resetAppointmentForm() {
  editingAppointmentId = null;
  editingAppointmentProfile = '';
  els.appointmentTitle.value = '';
  els.appointmentDepartment.value = '';
  els.appointmentDoctor.value = '';
  els.appointmentLocation.value = '';
  els.appointmentTime.value = metricNowLocal();
  els.appointmentStatusSelect.value = 'upcoming';
  els.appointmentNote.value = '';
  els.appointmentSave.textContent = '保存预约';
  els.appointmentCancel.hidden = true;
}

function appointmentCountdown(item) {
  const days = Number(item.days_until);
  if (!Number.isFinite(days) || item.status !== 'upcoming') return '';
  if (item.is_past) return '已过就诊时间';
  if (days === 0) return '今天';
  if (days === 1) return '明天';
  return `${days} 天后`;
}

function filterAppointments(items) {
  const query = els.appointmentSearch
    ? String(els.appointmentSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  const statusLabels = {
    upcoming: '待就诊',
    completed: '已完成',
    cancelled: '已取消',
  };
  return items.filter((item) => (
    [
      item.title, item.department, item.doctor,
      item.location, item.note, item.profile,
      statusLabels[item.status] || item.status,
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderAppointments(items) {
  if (!els.appointmentList) return;
  els.appointmentList.replaceChildren();
  els.appointmentEmpty.hidden = items.length > 0;
  const query = els.appointmentSearch
    ? String(els.appointmentSearch.value || '').trim()
    : '';
  els.appointmentEmpty.textContent = query
    ? `没有匹配「${query}」的就诊预约。`
    : '还没有就诊预约。';
  const upcoming = items.filter(
    (item) => item.status === 'upcoming' && !item.is_past
  ).length;
  if (items.length) {
    els.appointmentSummary.hidden = false;
    els.appointmentSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (upcoming ? ` · 待就诊 ${upcoming} 条` : '');
  } else {
    els.appointmentSummary.hidden = true;
  }
  if (
    editingAppointmentId
    && !appointmentItemsCache.some(
      (item) => item.id === editingAppointmentId
    )
  ) {
    resetAppointmentForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.title || '就诊预约';
    const statusBadge = document.createElement('span');
    statusBadge.className = 'appointment-status-badge '
      + `is-${item.status || 'upcoming'}`;
    statusBadge.textContent =
      APPOINTMENT_STATUS_LABELS[item.status] || '待就诊';
    title.append(' ', statusBadge);
    const countdown = appointmentCountdown(item);
    if (countdown) {
      const soonBadge = document.createElement('span');
      soonBadge.className = 'appointment-soon-badge';
      soonBadge.textContent = countdown;
      title.append(' ', soonBadge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      String(item.scheduled_at || '').replace('T', ' ').slice(0, 16),
      item.department,
      item.doctor,
      item.location,
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (item.status === 'upcoming') {
      const done = document.createElement('button');
      done.type = 'button';
      done.className = 'metric-edit';
      done.textContent = '完成';
      done.setAttribute('aria-label', `标记就诊已完成：${item.title}`);
      done.addEventListener(
        'click', () => updateAppointmentStatus(item, 'completed')
      );

      const cancelled = document.createElement('button');
      cancelled.type = 'button';
      cancelled.className = 'metric-edit';
      cancelled.textContent = '取消';
      cancelled.setAttribute('aria-label', `取消就诊预约：${item.title}`);
      cancelled.addEventListener(
        'click', () => updateAppointmentStatus(item, 'cancelled')
      );

      const remind = document.createElement('button');
      remind.type = 'button';
      remind.className = 'metric-edit';
      remind.textContent = '提醒';
      remind.setAttribute('aria-label', `为就诊创建提醒：${item.title}`);
      remind.addEventListener(
        'click', () => createAppointmentReminder(item)
      );
      actions.append(done, cancelled, remind);
    } else {
      const restore = document.createElement('button');
      restore.type = 'button';
      restore.className = 'metric-edit';
      restore.textContent = '恢复待就诊';
      restore.setAttribute('aria-label', `恢复为待就诊：${item.title}`);
      restore.addEventListener(
        'click', () => updateAppointmentStatus(item, 'upcoming')
      );
      actions.appendChild(restore);

      const record = document.createElement('button');
      record.type = 'button';
      record.className = 'metric-edit';
      record.textContent = '记录就诊';
      record.setAttribute('aria-label', `补记就诊记录：${item.title}`);
      record.addEventListener(
        'click', () => startVisitFromAppointment(item)
      );
      actions.appendChild(record);
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑就诊预约：${item.title}`);
    edit.addEventListener('click', () => editAppointment(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除就诊预约：${item.title}`);
    del.addEventListener('click', () => deleteAppointment(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.appointmentList.appendChild(li);
  }
}

async function loadAppointments() {
  if (!els.appointmentList) return;
  const requestSeq = ++appointmentRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/appointments?limit=100000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== appointmentRequestSeq) return;
    appointmentItemsCache = data.appointments || [];
    renderAppointments(filterAppointments(appointmentItemsCache));
  } catch {
    if (requestSeq !== appointmentRequestSeq) return;
    appointmentItemsCache = [];
    renderAppointments([]);
    els.appointmentEmpty.textContent =
      '就诊预约加载失败，请稍后重试。';
    els.appointmentEmpty.hidden = false;
    setAppointmentStatusText('就诊预约加载失败，请稍后重试。', true);
  }
}

async function exportAppointmentsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/appointments/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-appointments-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setAppointmentStatusText('已导出就诊预约 CSV。');
  } catch (err) {
    setAppointmentStatusText(`导出失败：${err.message || err}`, true);
  }
}

function editAppointment(item) {
  editingAppointmentId = item.id;
  editingAppointmentProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.appointmentTitle.value = item.title || '';
  els.appointmentDepartment.value = item.department || '';
  els.appointmentDoctor.value = item.doctor || '';
  els.appointmentLocation.value = item.location || '';
  els.appointmentTime.value =
    String(item.scheduled_at || '').slice(0, 16);
  els.appointmentStatusSelect.value = item.status || 'upcoming';
  els.appointmentNote.value = item.note || '';
  els.appointmentSave.textContent = '保存修改';
  els.appointmentCancel.hidden = false;
  els.appointmentTitle.focus();
}

async function saveAppointment() {
  if (appointmentSaving) return;
  const title = (els.appointmentTitle.value || '').trim();
  if (!title) {
    setAppointmentStatusText('请填写就诊主题。', true);
    els.appointmentTitle.focus();
    return;
  }
  const scheduledAt = els.appointmentTime.value
    || metricNowLocal();
  const editingId = editingAppointmentId;
  const payload = {
    profile: editingId
      ? editingAppointmentProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    title,
    department: (els.appointmentDepartment.value || '').trim(),
    doctor: (els.appointmentDoctor.value || '').trim(),
    location: (els.appointmentLocation.value || '').trim(),
    scheduled_at: scheduledAt,
    status: els.appointmentStatusSelect.value || 'upcoming',
    note: (els.appointmentNote.value || '').trim(),
  };

  appointmentSaving = true;
  els.appointmentSave.disabled = true;
  try {
    const res = await fetch(
      editingId
        ? `/api/appointments/${editingId}`
        : '/api/appointments',
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
    resetAppointmentForm();
    setAppointmentStatusText(
      editingId ? '已保存修改。' : '已保存就诊预约。'
    );
    await loadAppointments();
    refreshOverview();
  } catch (err) {
    setAppointmentStatusText(`保存失败：${err.message || err}`, true);
  } finally {
    appointmentSaving = false;
    els.appointmentSave.disabled = false;
  }
}

async function updateAppointmentStatus(item, status) {
  if (status === 'cancelled' && !window.confirm(
    `取消就诊预约「${item.title}」？`
  )) return;
  try {
    const res = await fetch(`/api/appointments/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setAppointmentStatusText(
      `已标记为${APPOINTMENT_STATUS_LABELS[status] || status}。`
    );
    await loadAppointments();
    refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setAppointmentStatusText(`更新状态失败：${err.message || err}`, true);
  }
}

async function deleteAppointment(item) {
  if (!window.confirm(`删除就诊预约「${item.title}」？`)) return;
  try {
    const res = await fetch(`/api/appointments/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingAppointmentId === item.id) resetAppointmentForm();
    setAppointmentStatusText('已删除就诊预约。');
    await loadAppointments();
    refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setAppointmentStatusText(`删除失败：${err.message || err}`, true);
  }
}

async function createAppointmentReminder(item) {
  if (appointmentReminderBusy.has(item.id)) return;
  appointmentReminderBusy.add(item.id);
  setAppointmentStatusText('正在创建就诊提醒…');
  try {
    const res = await fetch(
      `/api/appointments/${item.id}/reminder`, { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setAppointmentStatusText(
      data.created
        ? '已创建就诊提醒（提前 1 天）。'
        : '该预约已有未完成的就诊提醒。'
    );
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setAppointmentStatusText(`创建提醒失败：${err.message || err}`, true);
  } finally {
    appointmentReminderBusy.delete(item.id);
  }
}

let editingLabId = null;
let editingLabProfile = '';
let labSaving = false;
let labRequestSeq = 0;
let labItemsCache = [];
let labSearchTimer = null;

function setLabStatus(text, isError = false) {
  if (!els.labStatus) return;
  els.labStatus.textContent = text || '';
  els.labStatus.hidden = !text;
  els.labStatus.classList.toggle('error', Boolean(isError));
}

function resetLabForm() {
  editingLabId = null;
  editingLabProfile = '';
  els.labItem.value = '';
  els.labPanel.value = '';
  els.labValue.value = '';
  els.labUnit.value = '';
  els.labRefLow.value = '';
  els.labRefHigh.value = '';
  els.labTime.value = metricNowLocal();
  els.labName.value = '';
  els.labNote.value = '';
  els.labSave.textContent = '保存化验结果';
  els.labCancel.hidden = true;
}

function filterLabs(items) {
  const query = els.labSearch
    ? String(els.labSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [
      item.item, item.panel, item.unit, item.lab_name,
      item.note, item.profile, item.ref_text,
      item.abnormal
        ? (item.abnormal_high ? '偏高' : '偏低')
        : '',
      (item.critical_alerts || []).length ? '危急值' : '',
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function formatLabValue(item) {
  const value = Number(item.value);
  return Number.isFinite(value)
    ? value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
    : String(item.value || '');
}

function renderLabSparkline(series) {
  if (!Array.isArray(series) || series.length < 2) return null;
  const width = 132;
  const height = 30;
  const pad = 3;
  const values = series
    .map((row) => Number(row.value))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (index) => (
    pad + ((width - pad * 2) * index) / (values.length - 1)
  );
  const y = (value) => (
    height - pad - ((height - pad * 2) * (value - min)) / range
  );
  const svg = svgEl('svg', {
    viewBox: `0 0 ${width} ${height}`,
    class: 'lab-sparkline',
    'aria-hidden': 'true',
  });
  svg.appendChild(svgEl('polyline', {
    points: values.map((value, index) => (
      `${x(index).toFixed(1)},${y(value).toFixed(1)}`
    )).join(' '),
    class: 'lab-sparkline-line',
  }));
  const last = values.length - 1;
  svg.appendChild(svgEl('circle', {
    cx: x(last).toFixed(1),
    cy: y(values[last]).toFixed(1),
    r: 2.2,
    class: 'lab-sparkline-dot',
  }));
  return svg;
}

function renderLabs(items) {
  if (!els.labList) return;
  els.labList.replaceChildren();
  els.labEmpty.hidden = items.length > 0;
  const query = els.labSearch
    ? String(els.labSearch.value || '').trim()
    : '';
  els.labEmpty.textContent = query
    ? `没有匹配「${query}」的化验结果。`
    : '还没有化验结果。';
  const abnormal = items.filter((item) => item.abnormal).length;
  const critical = items.filter(
    (item) => (item.critical_alerts || []).length
  ).length;
  if (items.length) {
    els.labSummary.hidden = false;
    els.labSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (abnormal ? ` · 异常 ${abnormal} 项` : '')
      + (critical ? ` · 危急值 ${critical} 项` : '');
  } else {
    els.labSummary.hidden = true;
  }
  if (
    editingLabId
    && !labItemsCache.some((item) => item.id === editingLabId)
  ) {
    resetLabForm();
  }
  const sparklineSeen = new Set();

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.item || '化验';
    const valueBadge = document.createElement('span');
    valueBadge.className = 'lab-normal-badge';
    valueBadge.textContent =
      `${formatLabValue(item)}${item.unit ? ` ${item.unit}` : ''}`;
    title.append(' ', valueBadge);
    if (item.abnormal) {
      const flag = document.createElement('span');
      flag.className = 'lab-abnormal-badge';
      flag.textContent = item.abnormal_low ? '偏低' : '偏高';
      title.append(' ', flag);
    }
    if ((item.critical_alerts || []).length) {
      const flag = document.createElement('span');
      flag.className = 'lab-critical-badge';
      flag.textContent = '危急值';
      flag.title = item.critical_alerts[0].message || '';
      title.append(' ', flag);
    }
    const attachmentCount = Number(
      attachmentRefCounts.lab[item.id]
    ) || 0;
    if (attachmentCount) {
      const badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'care-due-badge is-soon attachment-count-badge';
      badge.textContent = `资料 ${attachmentCount}`;
      badge.setAttribute(
        'aria-label',
        `查看化验结果关联的 ${attachmentCount} 份资料`
      );
      badge.addEventListener(
        'click', () => openAttachmentListForRecord('lab', item)
      );
      title.append(' ', badge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      String(item.tested_at || '').replace('T', ' ').slice(0, 16),
      item.ref_text ? `参考 ${item.ref_text}` : '',
      item.panel,
      item.lab_name,
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    const series = (labItemsCache || [])
      .filter((row) => (
        row.item === item.item
        && (row.unit || '') === (item.unit || '')
      ))
      .sort((a, b) => (
        String(a.tested_at || '').localeCompare(String(b.tested_at || ''))
      ))
      .slice(-20);
    const sparkline = renderLabSparkline(series);
    const sparkKey = `${item.item}\u0000${item.unit || ''}`;
    if (sparkline && !sparklineSeen.has(sparkKey)) {
      sparklineSeen.add(sparkKey);
      main.appendChild(sparkline);
    }
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    const upload = document.createElement('button');
    upload.type = 'button';
    upload.className = 'metric-edit';
    upload.textContent = '资料';
    upload.setAttribute('aria-label', `上传化验单：${item.item || ''}`);
    upload.addEventListener(
      'click', () => openAttachmentUploadForRecord('lab', item)
    );

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑化验结果：${item.item}`);
    edit.addEventListener('click', () => editLab(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除化验结果：${item.item}`);
    del.addEventListener('click', () => deleteLab(item));

    actions.append(upload, edit, del);
    li.append(main, actions);
    els.labList.appendChild(li);
  }
}

async function loadLabs() {
  if (!els.labList) return;
  const requestSeq = ++labRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/lab-results?limit=100000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== labRequestSeq) return;
    labItemsCache = data.lab_results || [];
    renderLabs(filterLabs(labItemsCache));
  } catch {
    if (requestSeq !== labRequestSeq) return;
    labItemsCache = [];
    renderLabs([]);
    els.labEmpty.textContent = '化验结果加载失败，请稍后重试。';
    els.labEmpty.hidden = false;
    setLabStatus('化验结果加载失败，请稍后重试。', true);
  }
}

async function exportLabsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/lab-results/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-lab-results-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setLabStatus('已导出台账 CSV。');
  } catch (err) {
    setLabStatus(`导出失败：${err.message || err}`, true);
  }
}

async function importLabsCsv(file) {
  if (!file) return;
  setLabStatus('正在导入化验结果…');
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/lab-results/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复`);
    if (data.invalid) parts.push(`格式错误 ${data.invalid} 行`);
    setLabStatus(parts.join('，') + '。', data.imported === 0);
    await refreshMetricProfileOptions();
    await loadLabs();
    refreshAttachmentReferences();
    refreshOverview();
  } catch (err) {
    setLabStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.labImportFile.value = '';
  }
}

async function downloadLabTemplate() {
  try {
    const res = await fetch('/api/templates/lab-results');
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const buffer = await res.arrayBuffer();
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      'medix-lab-results-template.csv',
      'text/csv;charset=utf-8'
    );
    setLabStatus('已下载化验结果模板。');
  } catch (err) {
    setLabStatus(`下载模板失败：${err.message || err}`, true);
  }
}

function editLab(item) {
  editingLabId = item.id;
  editingLabProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.labItem.value = item.item || '';
  els.labPanel.value = item.panel || '';
  els.labValue.value = String(item.value ?? '');
  els.labUnit.value = item.unit || '';
  els.labRefLow.value =
    item.ref_low === null || item.ref_low === undefined
      ? ''
      : String(item.ref_low);
  els.labRefHigh.value =
    item.ref_high === null || item.ref_high === undefined
      ? ''
      : String(item.ref_high);
  els.labTime.value = String(item.tested_at || '').slice(0, 16);
  els.labName.value = item.lab_name || '';
  els.labNote.value = item.note || '';
  els.labSave.textContent = '保存修改';
  els.labCancel.hidden = false;
  els.labItem.focus();
}

function parseLabBound(value, label) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  const number = Number(text);
  if (!Number.isFinite(number)) {
    throw new Error(`${label}应为数字`);
  }
  return number;
}

async function saveLab() {
  if (labSaving) return;
  const item = (els.labItem.value || '').trim();
  if (!item) {
    setLabStatus('请填写化验项目。', true);
    els.labItem.focus();
    return;
  }
  const valueText = (els.labValue.value || '').trim();
  const value = Number(valueText);
  if (valueText === '' || !Number.isFinite(value)) {
    setLabStatus('化验数值应为数字。', true);
    els.labValue.focus();
    return;
  }
  let refLow = null;
  let refHigh = null;
  try {
    refLow = parseLabBound(els.labRefLow.value, '参考下限');
    refHigh = parseLabBound(els.labRefHigh.value, '参考上限');
  } catch (err) {
    setLabStatus(err.message || String(err), true);
    return;
  }
  if (refLow !== null && refHigh !== null && refLow > refHigh) {
    setLabStatus('参考下限不能高于参考上限。', true);
    els.labRefLow.focus();
    return;
  }
  const editingId = editingLabId;
  const payload = {
    profile: editingId
      ? editingLabProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    item,
    panel: (els.labPanel.value || '').trim(),
    value,
    unit: (els.labUnit.value || '').trim(),
    ref_low: refLow,
    ref_high: refHigh,
    tested_at: els.labTime.value || metricNowLocal(),
    lab_name: (els.labName.value || '').trim(),
    note: (els.labNote.value || '').trim(),
  };
  labSaving = true;
  els.labSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/lab-results/${editingId}` : '/api/lab-results',
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
    resetLabForm();
    const saved = data.lab_result || {};
    const criticalAlerts = saved.critical_alerts || [];
    if (criticalAlerts.length) {
      setLabStatus(
        `已保存。${criticalAlerts[0].message || '该结果达到危急值，请立即就医。'}`,
        true
      );
    } else {
      setLabStatus(editingId ? '已保存修改。' : '已保存化验结果。');
    }
    await loadLabs();
    refreshAttachmentReferences();
    refreshOverview();
  } catch (err) {
    setLabStatus(`保存失败：${err.message || err}`, true);
  } finally {
    labSaving = false;
    els.labSave.disabled = false;
  }
}

async function deleteLab(item) {
  if (!window.confirm(`删除化验结果「${item.item}」？`)) return;
  try {
    const res = await fetch(`/api/lab-results/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingLabId === item.id) resetLabForm();
    setLabStatus('已删除化验结果。');
    await loadLabs();
    refreshAttachmentReferences();
    refreshOverview();
  } catch (err) {
    setLabStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingAllergyId = null;
let editingAllergyProfile = '';
let allergySaving = false;
let allergyRequestSeq = 0;
let allergyItemsCache = [];
let allergySearchTimer = null;

const ALLERGY_SEVERITY_LABELS = {
  mild: '轻度',
  moderate: '中度',
  severe: '重度',
};

const ALLERGY_KIND_LABELS = {
  drug: '药物',
  food: '食物',
  environment: '环境',
  other: '其他',
};

function setAllergyStatus(text, isError = false) {
  if (!els.allergyStatus) return;
  els.allergyStatus.textContent = text || '';
  els.allergyStatus.hidden = !text;
  els.allergyStatus.classList.toggle('error', Boolean(isError));
}

function resetAllergyForm() {
  editingAllergyId = null;
  editingAllergyProfile = '';
  els.allergySubstance.value = '';
  els.allergyReaction.value = '';
  els.allergySeverity.value = 'moderate';
  els.allergyKind.value = 'drug';
  els.allergyNotedAt.value = '';
  els.allergyActive.checked = true;
  els.allergyNote.value = '';
  els.allergySave.textContent = '保存过敏史';
  els.allergyCancel.hidden = true;
}

function filterAllergies(items) {
  const query = els.allergySearch
    ? String(els.allergySearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [
      item.substance, item.reaction, item.note, item.profile,
      ALLERGY_SEVERITY_LABELS[item.severity],
      ALLERGY_KIND_LABELS[item.kind],
      item.active ? '' : '已停用',
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderAllergies(items) {
  if (!els.allergyList) return;
  els.allergyList.replaceChildren();
  els.allergyEmpty.hidden = items.length > 0;
  const query = els.allergySearch
    ? String(els.allergySearch.value || '').trim()
    : '';
  els.allergyEmpty.textContent = query
    ? `没有匹配「${query}」的过敏史。`
    : '还没有过敏史记录。';
  const activeCount = items.filter((item) => item.active).length;
  if (items.length) {
    els.allergySummary.hidden = false;
    els.allergySummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (activeCount ? ` · 在用 ${activeCount} 条` : '');
  } else {
    els.allergySummary.hidden = true;
  }
  if (
    editingAllergyId
    && !allergyItemsCache.some((item) => item.id === editingAllergyId)
  ) {
    resetAllergyForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.substance || '过敏原';
    const severity = document.createElement('span');
    severity.className = 'allergy-severity-badge '
      + `is-${item.severity || 'moderate'}`;
    severity.textContent =
      ALLERGY_SEVERITY_LABELS[item.severity] || '中度';
    title.append(' ', severity);
    const kind = document.createElement('span');
    kind.className = 'allergy-kind-badge';
    kind.textContent = ALLERGY_KIND_LABELS[item.kind] || '其他';
    title.append(' ', kind);
    if (!item.active) {
      const inactive = document.createElement('span');
      inactive.className = 'allergy-kind-badge';
      inactive.textContent = '已停用';
      title.append(' ', inactive);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      item.reaction,
      item.noted_at ? `发现于 ${item.noted_at}` : '',
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑过敏史：${item.substance}`);
    edit.addEventListener('click', () => editAllergy(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除过敏史：${item.substance}`);
    del.addEventListener('click', () => deleteAllergy(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.allergyList.appendChild(li);
  }
}

async function loadAllergies() {
  if (!els.allergyList) return;
  const requestSeq = ++allergyRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/allergies?limit=1000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== allergyRequestSeq) return;
    allergyItemsCache = data.allergies || [];
    renderAllergies(filterAllergies(allergyItemsCache));
  } catch {
    if (requestSeq !== allergyRequestSeq) return;
    allergyItemsCache = [];
    renderAllergies([]);
    els.allergyEmpty.textContent = '过敏史加载失败，请稍后重试。';
    els.allergyEmpty.hidden = false;
    setAllergyStatus('过敏史加载失败，请稍后重试。', true);
  }
}

async function exportAllergiesCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/allergies/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-allergies-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setAllergyStatus('已导出过敏史 CSV。');
  } catch (err) {
    setAllergyStatus(`导出失败：${err.message || err}`, true);
  }
}

async function importAllergiesCsv(file) {
  if (!file) return;
  setAllergyStatus('正在导入过敏史…');
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/allergies/import', {
      method: 'POST',
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const parts = [`已导入 ${data.imported || 0} 条`];
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条重复`);
    if (data.invalid) parts.push(`格式错误 ${data.invalid} 行`);
    setAllergyStatus(parts.join('，') + '。', data.imported === 0);
    await refreshMetricProfileOptions();
    await loadAllergies();
    refreshOverview();
  } catch (err) {
    setAllergyStatus(`导入失败：${err.message || err}`, true);
  } finally {
    els.allergyImportFile.value = '';
  }
}

async function downloadAllergyTemplate() {
  try {
    const res = await fetch('/api/templates/allergies');
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const buffer = await res.arrayBuffer();
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      'medix-allergies-template.csv',
      'text/csv;charset=utf-8'
    );
    setAllergyStatus('已下载过敏史模板。');
  } catch (err) {
    setAllergyStatus(`下载模板失败：${err.message || err}`, true);
  }
}

function editAllergy(item) {
  editingAllergyId = item.id;
  editingAllergyProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.allergySubstance.value = item.substance || '';
  els.allergyReaction.value = item.reaction || '';
  els.allergySeverity.value = item.severity || 'moderate';
  els.allergyKind.value = item.kind || 'drug';
  els.allergyNotedAt.value = String(item.noted_at || '').slice(0, 10);
  els.allergyActive.checked = Boolean(item.active);
  els.allergyNote.value = item.note || '';
  els.allergySave.textContent = '保存修改';
  els.allergyCancel.hidden = false;
  els.allergySubstance.focus();
}

async function saveAllergy() {
  if (allergySaving) return;
  const substance = (els.allergySubstance.value || '').trim();
  if (!substance) {
    setAllergyStatus('请填写过敏原。', true);
    els.allergySubstance.focus();
    return;
  }
  const editingId = editingAllergyId;
  const payload = {
    profile: editingId
      ? editingAllergyProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    substance,
    reaction: (els.allergyReaction.value || '').trim(),
    severity: els.allergySeverity.value || 'moderate',
    kind: els.allergyKind.value || 'drug',
    active: els.allergyActive.checked,
    noted_at: els.allergyNotedAt.value || '',
    note: (els.allergyNote.value || '').trim(),
  };
  allergySaving = true;
  els.allergySave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/allergies/${editingId}` : '/api/allergies',
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
    resetAllergyForm();
    setAllergyStatus(editingId ? '已保存修改。' : '已保存过敏史。');
    await loadAllergies();
    refreshOverview();
  } catch (err) {
    setAllergyStatus(`保存失败：${err.message || err}`, true);
  } finally {
    allergySaving = false;
    els.allergySave.disabled = false;
  }
}

async function deleteAllergy(item) {
  if (!window.confirm(`删除过敏史「${item.substance}」？`)) return;
  try {
    const res = await fetch(`/api/allergies/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingAllergyId === item.id) resetAllergyForm();
    setAllergyStatus('已删除过敏史。');
    await loadAllergies();
    refreshOverview();
  } catch (err) {
    setAllergyStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingCareId = null;
let editingCareProfile = '';
let careSaving = false;
let careRequestSeq = 0;
let careItemsCache = [];
let careSearchTimer = null;
let careReminderBusy = new Set();

function setCareStatus(text, isError = false) {
  if (!els.careStatus) return;
  els.careStatus.textContent = text || '';
  els.careStatus.hidden = !text;
  els.careStatus.classList.toggle('error', Boolean(isError));
}

function resetCareForm() {
  editingCareId = null;
  editingCareProfile = '';
  els.careTitle.value = '';
  els.careInterval.value = '90';
  els.careNextDue.value = '';
  els.careLastDone.value = '';
  els.careActive.checked = true;
  els.careNote.value = '';
  els.careSave.textContent = '保存复查计划';
  els.careCancel.hidden = true;
}

function careDueText(item) {
  const days = Number(item.days_until);
  if (!item.active) return '已停用';
  if (!Number.isFinite(days)) return '';
  if (item.overdue) return `已逾期 ${Math.abs(days)} 天`;
  if (days === 0) return '今天';
  if (days <= 7) return `${days} 天后`;
  return `${days} 天后`;
}

function filterCareItems(items) {
  const query = els.careSearch
    ? String(els.careSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [
      item.title, item.note, item.profile,
      item.active ? '' : '已停用',
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderCareItems(items) {
  if (!els.careList) return;
  els.careList.replaceChildren();
  els.careEmpty.hidden = items.length > 0;
  const query = els.careSearch
    ? String(els.careSearch.value || '').trim()
    : '';
  els.careEmpty.textContent = query
    ? `没有匹配「${query}」的复查计划。`
    : '还没有复查计划。';
  const overdue = items.filter((item) => item.overdue).length;
  const dueSoon = items.filter((item) => item.due_soon).length;
  if (items.length) {
    els.careSummary.hidden = false;
    els.careSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (overdue ? ` · 逾期 ${overdue} 条` : '')
      + (dueSoon ? ` · 近 7 天 ${dueSoon} 条` : '');
  } else {
    els.careSummary.hidden = true;
  }
  if (
    editingCareId
    && !careItemsCache.some((item) => item.id === editingCareId)
  ) {
    resetCareForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.title || '复查';
    const dueBadge = document.createElement('span');
    dueBadge.className = 'care-due-badge'
      + (
        !item.active
          ? ' is-inactive'
          : item.overdue ? ' is-overdue' : item.due_soon ? ' is-soon' : ''
      );
    dueBadge.textContent = careDueText(item);
    title.append(' ', dueBadge);

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      `每 ${Number(item.interval_days) || 0} 天`,
      `下次 ${item.next_due_at || '—'}`,
      item.last_done_at ? `上次 ${item.last_done_at}` : '',
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (item.active) {
      const done = document.createElement('button');
      done.type = 'button';
      done.className = 'metric-edit';
      done.textContent = '完成';
      done.setAttribute('aria-label', `标记复查已完成：${item.title}`);
      done.addEventListener('click', () => markCareDone(item));
      actions.appendChild(done);

      const remind = document.createElement('button');
      remind.type = 'button';
      remind.className = 'metric-edit';
      remind.textContent = '提醒';
      remind.setAttribute('aria-label', `为复查创建提醒：${item.title}`);
      remind.addEventListener('click', () => createCareReminder(item));
      actions.appendChild(remind);
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑复查计划：${item.title}`);
    edit.addEventListener('click', () => editCareItem(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除复查计划：${item.title}`);
    del.addEventListener('click', () => deleteCareItem(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.careList.appendChild(li);
  }
}

async function loadCareItems() {
  if (!els.careList) return;
  const requestSeq = ++careRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/care-items?limit=1000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== careRequestSeq) return;
    careItemsCache = data.care_items || [];
    renderCareItems(filterCareItems(careItemsCache));
  } catch {
    if (requestSeq !== careRequestSeq) return;
    careItemsCache = [];
    renderCareItems([]);
    els.careEmpty.textContent = '复查计划加载失败，请稍后重试。';
    els.careEmpty.hidden = false;
    setCareStatus('复查计划加载失败，请稍后重试。', true);
  }
}

async function exportCareItemsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/care-items/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-care-plan-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setCareStatus('已导出复查计划 CSV。');
  } catch (err) {
    setCareStatus(`导出失败：${err.message || err}`, true);
  }
}

function editCareItem(item) {
  editingCareId = item.id;
  editingCareProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.careTitle.value = item.title || '';
  els.careInterval.value = String(item.interval_days || 30);
  els.careNextDue.value = String(item.next_due_at || '').slice(0, 10);
  els.careLastDone.value = String(item.last_done_at || '').slice(0, 10);
  els.careActive.checked = Boolean(item.active);
  els.careNote.value = item.note || '';
  els.careSave.textContent = '保存修改';
  els.careCancel.hidden = false;
  els.careTitle.focus();
}

async function saveCareItem() {
  if (careSaving) return;
  const title = (els.careTitle.value || '').trim();
  if (!title) {
    setCareStatus('请填写复查项目。', true);
    els.careTitle.focus();
    return;
  }
  const interval = Number(els.careInterval.value);
  if (!Number.isFinite(interval) || interval < 1) {
    setCareStatus('间隔天数应为正整数。', true);
    els.careInterval.focus();
    return;
  }
  const editingId = editingCareId;
  const payload = {
    profile: editingId
      ? editingCareProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    title,
    interval_days: Math.round(interval),
    next_due_at: els.careNextDue.value || '',
    last_done_at: els.careLastDone.value || '',
    active: els.careActive.checked,
    note: (els.careNote.value || '').trim(),
  };
  careSaving = true;
  els.careSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/care-items/${editingId}` : '/api/care-items',
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
    resetCareForm();
    setCareStatus(editingId ? '已保存修改。' : '已保存复查计划。');
    await loadCareItems();
    refreshOverview();
  } catch (err) {
    setCareStatus(`保存失败：${err.message || err}`, true);
  } finally {
    careSaving = false;
    els.careSave.disabled = false;
  }
}

async function markCareDone(item) {
  try {
    const res = await fetch(`/api/care-items/${item.id}/done`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const nextDue = data.care_item ? data.care_item.next_due_at : '';
    const reminderDue = data.reminder
      ? String(data.reminder.due_at || '').slice(0, 10)
      : '';
    setCareStatus(
      `已记录复查完成${nextDue ? `，下次 ${nextDue}` : ''}`
      + (
        data.reminder_created
          ? `，已创建提前 7 天提醒${reminderDue ? `（${reminderDue}）` : ''}。`
          : '。'
      )
    );
    await loadCareItems();
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setCareStatus(`记录完成失败：${err.message || err}`, true);
  }
}

async function createCareReminder(item) {
  if (careReminderBusy.has(item.id)) return;
  careReminderBusy.add(item.id);
  setCareStatus('正在创建复查提醒…');
  try {
    const res = await fetch(
      `/api/care-items/${item.id}/reminder`, { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setCareStatus(
      data.created ? '已创建复查提醒（提前 7 天）。' : '该复查已有未完成的提醒。'
    );
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setCareStatus(`创建提醒失败：${err.message || err}`, true);
  } finally {
    careReminderBusy.delete(item.id);
  }
}

async function deleteCareItem(item) {
  if (!window.confirm(`删除复查计划「${item.title}」？`)) return;
  try {
    const res = await fetch(`/api/care-items/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingCareId === item.id) resetCareForm();
    setCareStatus('已删除复查计划。');
    await loadCareItems();
    refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setCareStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingVisitId = null;
let editingVisitProfile = '';
let visitSaving = false;
let visitRequestSeq = 0;
let visitItemsCache = [];
let visitSearchTimer = null;
let visitFollowupBusy = new Set();

function setVisitStatus(text, isError = false) {
  if (!els.visitStatus) return;
  els.visitStatus.textContent = text || '';
  els.visitStatus.hidden = !text;
  els.visitStatus.classList.toggle('error', Boolean(isError));
}

function resetVisitForm() {
  editingVisitId = null;
  editingVisitProfile = '';
  els.visitAppointmentId.value = '';
  els.visitDate.value = todayLocalDate();
  els.visitDepartment.value = '';
  els.visitDoctor.value = '';
  els.visitFacility.value = '';
  els.visitDiagnosis.value = '';
  els.visitAdvice.value = '';
  els.visitPrescription.value = '';
  els.visitNextFollowup.value = '';
  els.visitNote.value = '';
  els.visitSave.textContent = '保存就诊记录';
  els.visitCancel.hidden = true;
}

function filterVisits(items) {
  const query = els.visitSearch
    ? String(els.visitSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [
      item.diagnosis, item.advice, item.prescription,
      item.department, item.doctor, item.facility,
      item.note, item.profile,
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

async function openVisitPrepForVisit(item) {
  if (!item) return;
  const profile = item.profile || '';
  if (els.metricProfile) els.metricProfile.value = profile;
  await openMetrics();
  if (els.metricProfile && els.metricProfile.value !== profile) {
    els.metricProfile.value = profile;
    els.metricProfile.dispatchEvent(
      new Event('change', { bubbles: true })
    );
  }
  let days = 30;
  const date = String(item.visit_date || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    const visitTime = new Date(`${date}T00:00:00`).getTime();
    if (!Number.isNaN(visitTime)) {
      const elapsed = Math.max(
        1, Math.floor((Date.now() - visitTime) / 86400000) + 1
      );
      days = elapsed <= 30 ? 30 : elapsed <= 90 ? 90 : 180;
    }
  }
  if (els.visitPrepDays) els.visitPrepDays.value = String(days);
  resetVisitPrep();
  const section = document.getElementById('visit-prep-section');
  if (section) {
    section.open = true;
    section.scrollIntoView({ block: 'start' });
  }
  await loadVisitPrep();
}

async function openAttachmentUploadForRecord(kind, item) {
  if (!item || !els.attachmentKind) return;
  const profile = item.profile || '';
  if (els.metricProfile) els.metricProfile.value = profile;
  await openMetrics();
  if (els.metricProfile && els.metricProfile.value !== profile) {
    els.metricProfile.value = profile;
    els.metricProfile.dispatchEvent(
      new Event('change', { bubbles: true })
    );
  }
  if (!['visit', 'lab'].includes(kind)) return;
  els.attachmentKind.value = kind;
  els.attachmentKind.dispatchEvent(new Event('change', { bubbles: true }));
  const section = document.getElementById('attachments-section');
  if (section) {
    section.open = true;
    section.scrollIntoView({ block: 'start' });
  }
  await loadAttachmentRefOptions();
  if (els.attachmentRef) {
    els.attachmentRef.value = String(item.id);
  }
  if (els.attachmentFile) els.attachmentFile.focus();
}

function renderVisits(items) {
  if (!els.visitList) return;
  els.visitList.replaceChildren();
  els.visitEmpty.hidden = items.length > 0;
  const query = els.visitSearch
    ? String(els.visitSearch.value || '').trim()
    : '';
  els.visitEmpty.textContent = query
    ? `没有匹配「${query}」的就诊记录。`
    : '还没有就诊记录。';
  if (items.length) {
    els.visitSummary.hidden = false;
    els.visitSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`;
  } else {
    els.visitSummary.hidden = true;
  }
  if (
    editingVisitId
    && !visitItemsCache.some((item) => item.id === editingVisitId)
  ) {
    resetVisitForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.diagnosis || '就诊记录';
    if (item.next_followup_date) {
      const followup = document.createElement('span');
      followup.className = 'care-due-badge'
        + (item.followup_overdue ? ' is-overdue' : ' is-soon');
      followup.textContent = `复诊 ${item.next_followup_date}`;
      title.append(' ', followup);
    }
    if (item.followup_scheduled) {
      const scheduled = document.createElement('span');
      scheduled.className = 'care-due-badge is-soon';
      scheduled.textContent = '已安排复诊';
      title.append(' ', scheduled);
    }
    const attachmentCount = Number(
      attachmentRefCounts.visit[item.id]
    ) || 0;
    if (attachmentCount) {
      const badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'care-due-badge is-soon attachment-count-badge';
      badge.textContent = `资料 ${attachmentCount}`;
      badge.setAttribute(
        'aria-label',
        `查看就诊记录关联的 ${attachmentCount} 份资料`
      );
      badge.addEventListener(
        'click', () => openAttachmentListForRecord('visit', item)
      );
      title.append(' ', badge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      item.visit_date,
      item.department,
      item.doctor,
      item.facility,
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (item.advice) {
      const advice = document.createElement('span');
      advice.className = 'metric-note';
      advice.textContent = `医嘱：${item.advice}`;
      main.appendChild(advice);
    }
    if (item.prescription) {
      const prescription = document.createElement('span');
      prescription.className = 'metric-note';
      prescription.textContent = `处方：${item.prescription}`;
      main.appendChild(prescription);
    }
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (item.next_followup_date) {
      const followup = document.createElement('button');
      followup.type = 'button';
      followup.className = 'metric-edit';
      followup.textContent = item.followup_scheduled
        ? '复诊已安排'
        : '安排复诊';
      followup.setAttribute(
        'aria-label',
        `为就诊记录安排复诊：${item.diagnosis || ''}`
      );
      followup.addEventListener('click', () => scheduleVisitFollowup(item));
      actions.appendChild(followup);
    }

    const prep = document.createElement('button');
    prep.type = 'button';
    prep.className = 'metric-edit';
    prep.textContent = '准备复诊';
    prep.setAttribute(
      'aria-label',
      `为就诊记录生成复诊准备单：${item.diagnosis || ''}`
    );
    prep.addEventListener('click', () => openVisitPrepForVisit(item));

    const upload = document.createElement('button');
    upload.type = 'button';
    upload.className = 'metric-edit';
    upload.textContent = '资料';
    upload.setAttribute(
      'aria-label',
      `上传就诊资料：${item.diagnosis || ''}`
    );
    upload.addEventListener(
      'click', () => openAttachmentUploadForRecord('visit', item)
    );

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑就诊记录：${item.diagnosis || ''}`);
    edit.addEventListener('click', () => editVisit(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除就诊记录：${item.diagnosis || ''}`);
    del.addEventListener('click', () => deleteVisit(item));

    actions.append(prep, upload, edit, del);
    li.append(main, actions);
    els.visitList.appendChild(li);
  }
}

async function loadVisits() {
  if (!els.visitList) return;
  const requestSeq = ++visitRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/visits?limit=1000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== visitRequestSeq) return;
    visitItemsCache = data.visits || [];
    renderVisits(filterVisits(visitItemsCache));
  } catch {
    if (requestSeq !== visitRequestSeq) return;
    visitItemsCache = [];
    renderVisits([]);
    els.visitEmpty.textContent = '就诊记录加载失败，请稍后重试。';
    els.visitEmpty.hidden = false;
    setVisitStatus('就诊记录加载失败，请稍后重试。', true);
  }
}

async function exportVisitsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/visits/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-visits-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setVisitStatus('已导出就诊记录 CSV。');
  } catch (err) {
    setVisitStatus(`导出失败：${err.message || err}`, true);
  }
}

function startVisitFromAppointment(item) {
  resetVisitForm();
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.visitAppointmentId.value = String(item.id || '');
  els.visitDate.value = todayLocalDate();
  els.visitDepartment.value = item.department || '';
  els.visitDoctor.value = item.doctor || '';
  els.visitFacility.value = item.location || '';
  els.visitDiagnosis.value = item.title || '';
  const section = document.getElementById('visit-section');
  if (section) section.open = true;
  els.visitDiagnosis.focus();
  els.visitDiagnosis.scrollIntoView({ block: 'center' });
  setVisitStatus('已从预约带入，请补全诊断、医嘱与处方。');
}

function editVisit(item) {
  editingVisitId = item.id;
  editingVisitProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.visitAppointmentId.value = String(item.appointment_id || '');
  els.visitDate.value = String(item.visit_date || '').slice(0, 10);
  els.visitDepartment.value = item.department || '';
  els.visitDoctor.value = item.doctor || '';
  els.visitFacility.value = item.facility || '';
  els.visitDiagnosis.value = item.diagnosis || '';
  els.visitAdvice.value = item.advice || '';
  els.visitPrescription.value = item.prescription || '';
  els.visitNextFollowup.value =
    String(item.next_followup_date || '').slice(0, 10);
  els.visitNote.value = item.note || '';
  els.visitSave.textContent = '保存修改';
  els.visitCancel.hidden = false;
  els.visitDiagnosis.focus();
}

async function saveVisit() {
  if (visitSaving) return;
  const visitDate = els.visitDate.value || todayLocalDate();
  const editingId = editingVisitId;
  const payload = {
    profile: editingId
      ? editingVisitProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    appointment_id: Number(els.visitAppointmentId.value) || 0,
    visit_date: visitDate,
    department: (els.visitDepartment.value || '').trim(),
    doctor: (els.visitDoctor.value || '').trim(),
    facility: (els.visitFacility.value || '').trim(),
    diagnosis: (els.visitDiagnosis.value || '').trim(),
    advice: (els.visitAdvice.value || '').trim(),
    prescription: (els.visitPrescription.value || '').trim(),
    next_followup_date: els.visitNextFollowup.value || '',
    note: (els.visitNote.value || '').trim(),
  };
  visitSaving = true;
  els.visitSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/visits/${editingId}` : '/api/visits',
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
    resetVisitForm();
    setVisitStatus(editingId ? '已保存修改。' : '已保存就诊记录。');
    await loadVisits();
    refreshAttachmentReferences();
    refreshOverview();
  } catch (err) {
    setVisitStatus(`保存失败：${err.message || err}`, true);
  } finally {
    visitSaving = false;
    els.visitSave.disabled = false;
  }
}

async function scheduleVisitFollowup(item) {
  if (visitFollowupBusy.has(item.id)) return;
  visitFollowupBusy.add(item.id);
  setVisitStatus('正在安排复诊…');
  try {
    const res = await fetch(
      `/api/visits/${item.id}/schedule-followup`, { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const appointment = data.appointment || {};
    const when = String(appointment.scheduled_at || '')
      .replace('T', ' ')
      .slice(0, 16);
    if (data.appointment_created) {
      setVisitStatus(
        `已创建复诊预约${when ? `（${when}）` : ''}，并生成提前 1 天提醒。`
      );
    } else {
      setVisitStatus(
        `该复诊已安排过预约${when ? `（${when}）` : ''}，无需重复创建。`
      );
    }
    await loadVisits();
    loadAppointments();
    refreshReminderBadge();
    refreshOverview();
  } catch (err) {
    setVisitStatus(`安排复诊失败：${err.message || err}`, true);
  } finally {
    visitFollowupBusy.delete(item.id);
  }
}

async function deleteVisit(item) {
  if (!window.confirm(
    `删除就诊记录「${item.diagnosis || item.visit_date}」？`
  )) return;
  try {
    const res = await fetch(`/api/visits/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingVisitId === item.id) resetVisitForm();
    setVisitStatus('已删除就诊记录。');
    await loadVisits();
    refreshAttachmentReferences();
    refreshOverview();
  } catch (err) {
    setVisitStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingConditionId = null;
let editingConditionProfile = '';
let conditionSaving = false;
let conditionRequestSeq = 0;
let conditionItemsCache = [];
let conditionSearchTimer = null;
let conditionTemplateBusy = new Set();

const CONDITION_STATUS_LABELS = {
  active: '管理中',
  controlled: '已控制',
  resolved: '已痊愈',
};
const CONDITION_SEVERITY_LABELS = {
  mild: '轻度',
  moderate: '中度',
  severe: '重度',
};

function setConditionStatus(text, isError = false) {
  if (!els.conditionStatusText) return;
  els.conditionStatusText.textContent = text || '';
  els.conditionStatusText.hidden = !text;
  els.conditionStatusText.classList.toggle('error', Boolean(isError));
}

function resetConditionForm() {
  editingConditionId = null;
  editingConditionProfile = '';
  els.conditionName.value = '';
  els.conditionIcd10.value = '';
  els.conditionDiagnosedAt.value = '';
  els.conditionStatus.value = 'active';
  els.conditionSeverity.value = 'moderate';
  els.conditionNote.value = '';
  els.conditionSave.textContent = '保存问题';
  els.conditionCancel.hidden = true;
}

function filterConditions(items) {
  const query = els.conditionSearch
    ? String(els.conditionSearch.value || '').trim().toLowerCase()
    : '';
  if (!query) return items;
  return items.filter((item) => (
    [
      item.name, item.icd10, item.note, item.profile,
      CONDITION_STATUS_LABELS[item.status],
      CONDITION_SEVERITY_LABELS[item.severity],
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query)
  ));
}

function renderConditions(items) {
  if (!els.conditionList) return;
  els.conditionList.replaceChildren();
  els.conditionEmpty.hidden = items.length > 0;
  const query = els.conditionSearch
    ? String(els.conditionSearch.value || '').trim()
    : '';
  els.conditionEmpty.textContent = query
    ? `没有匹配「${query}」的慢病记录。`
    : '还没有慢病/健康问题记录。';
  const activeCount = items.filter(
    (item) => item.status !== 'resolved'
  ).length;
  if (items.length) {
    els.conditionSummary.hidden = false;
    els.conditionSummary.textContent =
      `${query ? '筛选' : '全部'} ${items.length} 条`
      + (activeCount ? ` · 管理中 ${activeCount} 条` : '');
  } else {
    els.conditionSummary.hidden = true;
  }
  if (
    editingConditionId
    && !conditionItemsCache.some(
      (item) => item.id === editingConditionId
    )
  ) {
    resetConditionForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.name || '健康问题';
    const statusBadge = document.createElement('span');
    statusBadge.className = 'condition-status-badge '
      + `is-${item.status || 'active'}`;
    statusBadge.textContent =
      CONDITION_STATUS_LABELS[item.status] || '管理中';
    title.append(' ', statusBadge);
    const severityBadge = document.createElement('span');
    severityBadge.className = 'allergy-kind-badge';
    severityBadge.textContent =
      CONDITION_SEVERITY_LABELS[item.severity] || '中度';
    title.append(' ', severityBadge);
    if (item.template_key) {
      const templateBadge = document.createElement('span');
      templateBadge.className = 'allergy-kind-badge';
      templateBadge.textContent = `模板：${item.template_name}`;
      title.append(' ', templateBadge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      item.profile || '未指定档案',
      item.icd10,
      item.diagnosed_at ? `确诊 ${item.diagnosed_at}` : '',
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    if (item.template_key) {
      const template = document.createElement('button');
      template.type = 'button';
      template.className = 'metric-edit';
      template.textContent = '套用模板';
      template.setAttribute(
        'aria-label', `按慢病模板生成复查计划：${item.name}`
      );
      template.addEventListener(
        'click', () => applyConditionTemplate(item)
      );
      actions.appendChild(template);
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑慢病记录：${item.name}`);
    edit.addEventListener('click', () => editCondition(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除慢病记录：${item.name}`);
    del.addEventListener('click', () => deleteCondition(item));

    actions.append(edit, del);
    li.append(main, actions);
    els.conditionList.appendChild(li);
  }
}

async function loadConditions() {
  if (!els.conditionList) return;
  const requestSeq = ++conditionRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/conditions?limit=1000&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== conditionRequestSeq) return;
    conditionItemsCache = data.conditions || [];
    renderConditions(filterConditions(conditionItemsCache));
  } catch {
    if (requestSeq !== conditionRequestSeq) return;
    conditionItemsCache = [];
    renderConditions([]);
    els.conditionEmpty.textContent = '慢病清单加载失败，请稍后重试。';
    els.conditionEmpty.hidden = false;
    setConditionStatus('慢病清单加载失败，请稍后重试。', true);
  }
}

async function applyConditionTemplate(item) {
  if (conditionTemplateBusy.has(item.id)) return;
  conditionTemplateBusy.add(item.id);
  setConditionStatus(`正在套用「${item.template_name || '慢病'}」模板…`);
  try {
    const res = await fetch(
      `/api/conditions/${item.id}/apply-template`, { method: 'POST' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const created = (data.care_items_created || []).length;
    const skipped = (data.care_items_skipped || []).length;
    const targets = (data.targets_created || []).length;
    setConditionStatus(
      `已按「${data.template}」模板生成复查 ${created} 项`
      + (skipped ? `、跳过已有 ${skipped} 项` : '')
      + (targets ? `，并设置指标目标 ${targets} 项。` : '。')
    );
    await loadCareItems();
    loadMetricTargets();
    refreshOverview();
  } catch (err) {
    setConditionStatus(`套用模板失败：${err.message || err}`, true);
  } finally {
    conditionTemplateBusy.delete(item.id);
  }
}

async function exportConditionsCsv() {
  const params = new URLSearchParams({
    profile: els.metricProfile ? els.metricProfile.value : '',
  });
  try {
    const res = await fetch(
      `/api/conditions/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-conditions-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setConditionStatus('已导出慢病清单 CSV。');
  } catch (err) {
    setConditionStatus(`导出失败：${err.message || err}`, true);
  }
}

function editCondition(item) {
  editingConditionId = item.id;
  editingConditionProfile = item.profile || '';
  const hasProfile = els.metricProfile
    ? Array.from(els.metricProfile.options)
      .some((option) => option.value === item.profile)
    : false;
  if (hasProfile) els.metricProfile.value = item.profile;
  els.conditionName.value = item.name || '';
  els.conditionIcd10.value = item.icd10 || '';
  els.conditionDiagnosedAt.value =
    String(item.diagnosed_at || '').slice(0, 10);
  els.conditionStatus.value = item.status || 'active';
  els.conditionSeverity.value = item.severity || 'moderate';
  els.conditionNote.value = item.note || '';
  els.conditionSave.textContent = '保存修改';
  els.conditionCancel.hidden = false;
  els.conditionName.focus();
}

async function saveCondition() {
  if (conditionSaving) return;
  const name = (els.conditionName.value || '').trim();
  if (!name) {
    setConditionStatus('请填写疾病/健康问题名称。', true);
    els.conditionName.focus();
    return;
  }
  const editingId = editingConditionId;
  const payload = {
    profile: editingId
      ? editingConditionProfile
      : (els.metricProfile ? els.metricProfile.value : ''),
    name,
    icd10: (els.conditionIcd10.value || '').trim(),
    diagnosed_at: els.conditionDiagnosedAt.value || '',
    status: els.conditionStatus.value || 'active',
    severity: els.conditionSeverity.value || 'moderate',
    note: (els.conditionNote.value || '').trim(),
  };
  conditionSaving = true;
  els.conditionSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/conditions/${editingId}` : '/api/conditions',
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
    resetConditionForm();
    setConditionStatus(editingId ? '已保存修改。' : '已保存慢病记录。');
    await loadConditions();
    refreshOverview();
  } catch (err) {
    setConditionStatus(`保存失败：${err.message || err}`, true);
  } finally {
    conditionSaving = false;
    els.conditionSave.disabled = false;
  }
}

async function deleteCondition(item) {
  if (!window.confirm(`删除慢病记录「${item.name}」？`)) return;
  try {
    const res = await fetch(`/api/conditions/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingConditionId === item.id) resetConditionForm();
    setConditionStatus('已删除慢病记录。');
    await loadConditions();
    refreshOverview();
  } catch (err) {
    setConditionStatus(`删除失败：${err.message || err}`, true);
  }
}

let editingMedicationId = null;
let editingMedicationProfile = '';
let medicationSaving = false;
let medicationRequestSeq = 0;
let medicationLogRequestSeq = 0;
let medicationItemsCache = [];
let medicationLogItemsCache = [];
let medicationLogBusy = new Set();
let medicationSkipBusy = new Set();
let medicationSkipDraft = null;
let medicationSkipFocusId = 0;
const MEDICATION_SKIP_REASON_OPTIONS = [
  { key: 'forget', label: '忘记' },
  { key: 'side_effect', label: '副作用' },
  { key: 'worry_side_effect', label: '担心副作用' },
  { key: 'cost', label: '费用' },
  { key: 'inconvenient', label: '不方便' },
  { key: 'other', label: '其他' },
];
let medicationReminderBusy = false;
let medicationHistoryCache = new Map();
let medicationHistoryOpen = new Set();
let medicationHistoryBusy = new Set();

function setMedicationStatus(text, isError = false) {
  if (!els.medicationStatus) return;
  els.medicationStatus.textContent = text || '';
  els.medicationStatus.hidden = !text;
  els.medicationStatus.classList.toggle('error', Boolean(isError));
}

function resetMedicationForm() {
  editingMedicationId = null;
  editingMedicationProfile = '';
  els.medicationName.value = '';
  els.medicationDose.value = '';
  els.medicationFrequency.value = '';
  els.medicationStock.value = '';
  els.medicationStockUnit.value = '';
  els.medicationLowStock.value = '0';
  els.medicationExpiry.value = '';
  els.medicationStartedAt.value = '';
  els.medicationStoppedAt.value = '';
  els.medicationStopReason.value = '';
  els.medicationNote.value = '';
  els.medicationActive.checked = true;
  els.medicationSave.textContent = '添加药物';
  els.medicationCancel.hidden = true;
}

function filterMedications(items) {
  const query = els.medicationSearch
    ? String(els.medicationSearch.value || '').trim().toLowerCase()
    : '';
  const status = els.medicationStatusFilter
    ? els.medicationStatusFilter.value
    : '';
  return (items || []).filter((item) => {
    if (status === 'active' && !item.active) return false;
    if (status === 'inactive' && item.active) return false;
    if (!query) return true;
    return [
      item.name, item.dose, item.frequency, item.note, item.profile,
      item.active ? '在用' : '停用',
    ]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query);
  });
}

function renderMedications(items) {
  if (!els.medicationList) return;
  els.medicationList.replaceChildren();
  els.medicationEmpty.hidden = items.length > 0;
  const query = els.medicationSearch
    ? String(els.medicationSearch.value || '').trim()
    : '';
  const status = els.medicationStatusFilter
    ? els.medicationStatusFilter.value
    : '';
  if (query) {
    els.medicationEmpty.textContent = `没有匹配「${query}」的药物。`;
  } else if (status === 'active') {
    els.medicationEmpty.textContent = '没有在用的药物。';
  } else if (status === 'inactive') {
    els.medicationEmpty.textContent = '没有停用的药物。';
  } else {
    els.medicationEmpty.textContent = '还没有用药记录。';
  }
  if (
    editingMedicationId
    && !medicationItemsCache.some(
      (item) => item.id === editingMedicationId
    )
  ) {
    resetMedicationForm();
  }

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.name;
    const badge = document.createElement('span');
    badge.className = item.active
      ? 'medication-active-badge'
      : 'medication-active-badge is-inactive';
    badge.textContent = item.active ? '在用' : '停用';
    title.append(' ', badge);

    if (item.stock !== null && item.stock !== undefined) {
      const stockBadge = document.createElement('span');
      stockBadge.className = 'medication-stock-badge'
        + (
          item.out_of_stock
            ? ' is-out'
            : item.low_stock ? ' is-low' : ''
        );
      stockBadge.textContent =
        `库存 ${Number(item.stock).toLocaleString('zh-CN', {
          maximumFractionDigits: 2,
        })}${item.stock_unit || ''}`;
      title.append(' ', stockBadge);
    }
    if (item.expiry_date) {
      const expiryBadge = document.createElement('span');
      expiryBadge.className = 'medication-expiry-badge'
        + (
          item.expired
            ? ' is-expired'
            : item.expiring_soon ? ' is-soon' : ''
        );
      if (item.expired) {
        expiryBadge.textContent = `已过期 ${item.expiry_date}`;
      } else if (item.expiring_soon) {
        expiryBadge.textContent =
          `有效期 ${item.expiry_date}（${Number(item.days_to_expiry)} 天）`;
      } else {
        expiryBadge.textContent = `有效期 ${item.expiry_date}`;
      }
      title.append(' ', expiryBadge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    const metaParts = [
      item.profile || '未指定档案',
      item.dose,
      item.frequency,
    ].filter(Boolean);
    if (Number(item.today_count) > 0) {
      metaParts.push(`今日已服 ${Number(item.today_count)} 次`);
    } else if (item.active) {
      metaParts.push('今日未打卡');
    }
    const rangeDays = Number(item.range_days) || 7;
    const expectedDoses = Number(
      item.expected_range_doses ?? item.expected_week_doses ?? 0
    );
    const rangeCount = Number(
      item.range_count ?? item.week_count ?? 0
    );
    if (expectedDoses > 0) {
      const rate = Math.min(
        100, Math.round((rangeCount / expectedDoses) * 100)
      );
      metaParts.push(
        `近 ${rangeDays} 天 ${rangeCount}/${expectedDoses} 次 · 依从率 ${rate}%`
      );
      meta.title = `按频次估算近 ${rangeDays} 天应服 ${expectedDoses} 次`;
    } else if (rangeCount > 0) {
      metaParts.push(`近 ${rangeDays} 天 ${rangeCount} 次`);
    }
    if (item.last_taken_at) {
      metaParts.push(`最近 ${formatTime(item.last_taken_at)}`);
    }
    if (Number(item.skipped_range) > 0) {
      const reason = item.last_skip_reason_label
        ? `（最近：${item.last_skip_reason_label}）`
        : '';
      metaParts.push(
        `近 ${rangeDays} 天未服 ${Number(item.skipped_range)} 次${reason}`
      );
    }
    if (item.started_at) {
      metaParts.push(`开始 ${item.started_at}`);
    }
    if (item.stopped_at) {
      metaParts.push(`停药 ${item.stopped_at}`);
    }
    meta.textContent = metaParts.join(' · ');

    main.append(title, meta);
    if (item.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = item.note;
      main.appendChild(note);
    }
    if (medicationHistoryOpen.has(item.id)) {
      const rows = medicationHistoryCache.get(item.id) || [];
      if (!rows.length) {
        const empty = document.createElement('span');
        empty.className = 'metric-note';
        empty.textContent = '暂无变更记录。';
        main.appendChild(empty);
      }
      for (const row of rows) {
        const entry = document.createElement('span');
        entry.className = 'metric-note medication-history-entry';
        const when = String(row.changed_at || '')
          .replace('T', ' ')
          .slice(0, 16);
        entry.textContent =
          `${when} · ${row.detail || row.action || '变更'}`;
        main.appendChild(entry);
      }
    }

    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'metric-edit';
    toggle.textContent = item.active ? '停用' : '启用';
    toggle.setAttribute('aria-label', `${item.active ? '停用' : '启用'}药物：${item.name}`);
    toggle.addEventListener('click', () => toggleMedication(item));

    const remind = document.createElement('button');
    remind.type = 'button';
    remind.className = 'metric-edit';
    remind.textContent = '提醒';
    remind.setAttribute('aria-label', `为药物设置提醒：${item.name}`);
    remind.addEventListener('click', () => scheduleMedicationReminder(item));

    const log = document.createElement('button');
    log.type = 'button';
    log.className = 'metric-edit';
    log.textContent = '打卡';
    log.setAttribute('aria-label', `记录一次用药：${item.name}`);
    log.addEventListener('click', () => logMedication(item));

    const skip = document.createElement('button');
    skip.type = 'button';
    skip.className = 'metric-edit';
    skip.textContent = '未服';
    skip.setAttribute('aria-label', `记录一次未服：${item.name}`);
    skip.setAttribute('aria-expanded', 'false');
    skip.addEventListener(
      'click', () => openMedicationSkipForm(item, li, skip)
    );

    const history = document.createElement('button');
    history.type = 'button';
    history.className = 'metric-edit';
    history.textContent = medicationHistoryOpen.has(item.id)
      ? '收起历史'
      : '历史';
    history.setAttribute('aria-label', `查看用药变更历史：${item.name}`);
    history.addEventListener('click', () => toggleMedicationHistory(item));

    let restock = null;
    if (item.stock !== null && item.stock !== undefined) {
      restock = document.createElement('button');
      restock.type = 'button';
      restock.className = 'metric-edit';
      restock.textContent = '库存';
      restock.setAttribute('aria-label', `更新库存：${item.name}`);
      restock.addEventListener('click', () => restockMedication(item));
    }

    let stockReminder = null;
    if (item.low_stock) {
      stockReminder = document.createElement('button');
      stockReminder.type = 'button';
      stockReminder.className = 'metric-edit';
      stockReminder.textContent = '补货提醒';
      stockReminder.setAttribute(
        'aria-label', `为低库存药物创建补货提醒：${item.name}`
      );
      stockReminder.addEventListener(
        'click', () => createMedicationAlertReminder(item, 'stock')
      );
    }

    let expiryReminder = null;
    if (item.expired || item.expiring_soon) {
      expiryReminder = document.createElement('button');
      expiryReminder.type = 'button';
      expiryReminder.className = 'metric-edit';
      expiryReminder.textContent = '到期提醒';
      expiryReminder.setAttribute(
        'aria-label', `为临期药物创建到期提醒：${item.name}`
      );
      expiryReminder.addEventListener(
        'click', () => createMedicationAlertReminder(item, 'expiry')
      );
    }

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'metric-edit';
    edit.textContent = '编辑';
    edit.setAttribute('aria-label', `编辑药物：${item.name}`);
    edit.addEventListener('click', () => editMedication(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除药物：${item.name}`);
    del.addEventListener('click', () => deleteMedication(item));

    actions.append(toggle, log, skip, history, remind);
    if (restock) actions.appendChild(restock);
    if (stockReminder) actions.appendChild(stockReminder);
    if (expiryReminder) actions.appendChild(expiryReminder);
    if (Number(item.today_count) > 0) {
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'metric-delete';
      undo.textContent = '撤销';
      undo.setAttribute('aria-label', `撤销最近一次用药记录：${item.name}`);
      undo.addEventListener('click', () => undoMedicationLog(item));
      actions.appendChild(undo);
    }
    actions.append(edit, del);
    li.append(main, actions);
    els.medicationList.appendChild(li);
    skip.setAttribute(
      'aria-controls', `medication-skip-form-${item.id}`
    );
    if (medicationSkipDraft && medicationSkipDraft.id === item.id) {
      attachMedicationSkipForm(item, li, skip, medicationSkipDraft);
    }
    if (medicationSkipFocusId === item.id) {
      medicationSkipFocusId = 0;
      skip.focus();
    }
  }
}

async function toggleMedicationHistory(item) {
  if (medicationHistoryOpen.has(item.id)) {
    medicationHistoryOpen.delete(item.id);
    renderMedications(filterMedications(medicationItemsCache));
    return;
  }
  medicationHistoryOpen.add(item.id);
  if (!medicationHistoryCache.has(item.id)) {
    if (medicationHistoryBusy.has(item.id)) return;
    medicationHistoryBusy.add(item.id);
    try {
      const res = await fetch(
        `/api/medications/${item.id}/history?limit=100`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `服务返回 ${res.status}`);
      }
      medicationHistoryCache.set(item.id, data.history || []);
    } catch (err) {
      medicationHistoryOpen.delete(item.id);
      setMedicationStatus(
        `加载用药历史失败：${err.message || err}`, true
      );
      medicationHistoryBusy.delete(item.id);
      renderMedications(filterMedications(medicationItemsCache));
      return;
    }
    medicationHistoryBusy.delete(item.id);
  }
  renderMedications(filterMedications(medicationItemsCache));
}

async function loadMedications() {
  if (!els.medicationList) return;
  const requestSeq = ++medicationRequestSeq;
  medicationHistoryCache.clear();
  medicationHistoryOpen.clear();
  const profile = els.metricProfile.value;
  const days = Number(els.medicationRange && els.medicationRange.value) || 7;
  try {
    const res = await fetch(
      `/api/medications?limit=1000&profile=${encodeURIComponent(profile)}`
      + `&days=${days}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== medicationRequestSeq) return;
    medicationItemsCache = data.medications || [];
    renderMedications(filterMedications(medicationItemsCache));
  } catch {
    // 同上：失败时清空旧档案的用药清单
    if (requestSeq !== medicationRequestSeq) return;
    medicationItemsCache = [];
    renderMedications([]);
    els.medicationEmpty.textContent = '用药清单加载失败，请稍后重试。';
    els.medicationEmpty.hidden = false;
    setMedicationStatus('用药清单加载失败，请稍后重试。', true);
  }
}

function renderMedicationLogs(items) {
  if (!els.medicationLogList) return;
  els.medicationLogList.replaceChildren();
  els.medicationLogEmpty.hidden = items.length > 0;
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.skipped
      ? `${item.medication_name || '已删除药物'}（未服）`
      : (item.medication_name || '已删除药物');

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      formatTime(item.taken_at),
      item.medication_dose,
      item.skipped
        ? `未服原因：${item.skip_reason_label || '其他'}`
        : '',
      item.profile || '未指定档案',
    ].filter(Boolean).join(' · ');

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
    del.setAttribute(
      'aria-label',
      `${item.skipped ? '删除未服记录' : '删除打卡记录'}：`
      + `${item.medication_name || ''} ${formatTime(item.taken_at)}`
    );
    del.addEventListener('click', () => deleteMedicationLogItem(item));
    actions.appendChild(del);

    li.append(main, actions);
    els.medicationLogList.appendChild(li);
  }
}

async function loadMedicationLogs() {
  if (!els.medicationLogList) return;
  const requestSeq = ++medicationLogRequestSeq;
  const profile = els.metricProfile.value;
  try {
    const res = await fetch(
      `/api/medication-logs?limit=20&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== medicationLogRequestSeq) return;
    medicationLogItemsCache = data.logs || [];
    renderMedicationLogs(medicationLogItemsCache);
  } catch {
    if (requestSeq !== medicationLogRequestSeq) return;
    medicationLogItemsCache = [];
    renderMedicationLogs([]);
    els.medicationLogEmpty.textContent = '打卡记录加载失败，请稍后重试。';
    els.medicationLogEmpty.hidden = false;
  }
}

async function deleteMedicationLogItem(item) {
  const label = `${item.medication_name || '药物'} ${formatTime(item.taken_at)}`;
  const kind = item.skipped ? '未服记录' : '打卡记录';
  if (!window.confirm(`删除${kind}「${label}」？`)) return;
  try {
    const res = await fetch(`/api/medication-logs/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const reopenNote = item.skipped
      ? ''
      : await reopenLinkedMedicationReminder(
        item.medication_name || '', item.profile || '', item.taken_at,
        item.medication_id
      );
    const message =
      `已删除${kind}。` + (reopenNote ? ` ${reopenNote}` : '');
    setMedicationStatus(message);
    setTodayMedsStatus(message);
    await Promise.all([loadMedications(), loadMedicationLogs()]);
    await refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setMedicationStatus(`删除${kind}失败：${err.message || err}`, true);
  }
}

function weeklyMetricText(row) {
  const name = METRIC_NAMES[row.metric] || row.metric;
  const unit = row.unit ? ` ${row.unit}` : '';
  const average = row.avg_value2 === null || row.avg_value2 === undefined
    ? `${row.avg_value1}${unit}`
    : `${row.avg_value1}/${row.avg_value2}${unit}`;
  let change = '';
  const previous = row.prev_avg_value1;
  if (previous !== null && previous !== undefined) {
    const diff = Number(row.avg_value1) - Number(previous);
    if (Number.isFinite(diff)) {
      if (Math.abs(diff) < 0.05) {
        change = '，较上期持平';
      } else {
        const arrow = diff > 0 ? '↑' : '↓';
        const magnitude = Math.round(Math.abs(diff) * 10) / 10;
        change = `，较上期 ${arrow}${magnitude}${unit}`;
      }
    }
  }
  const targetText = row.target && row.within_target !== null
    ? `，达标 ${row.within_target}/${row.count}`
    : '';
  return `${name} ${row.count} 次，平均 ${average}${change}${targetText}`;
}

function rangeDaysLabel(days) {
  return Number(days) === 1 ? '今天' : `近 ${Number(days)} 天`;
}

function weeklySummaryLines(data) {
  const days = Number(data.days) || 7;
  const emptyText = `${rangeDaysLabel(days)}没有记录`;
  const metricRows = data.metrics.by_metric || [];
  const lines = [[
    '健康指标',
    metricRows.length
      ? metricRows.map(weeklyMetricText).join('；')
      : emptyText,
  ]];

  const symptoms = data.symptoms;
  let symptomText = emptyText;
  if (symptoms.count) {
    symptomText =
      `${symptoms.count} 条，平均 ${symptoms.avg_severity}/10`
      + `，最高 ${symptoms.max_severity}/10`;
    if (symptoms.top && symptoms.top.length) {
      symptomText += `；${symptoms.top
        .map((item) => `${item.symptom} ${item.count} 次`)
        .join('、')}`;
    }
    if (symptoms.prev_count !== null && symptoms.prev_count !== undefined) {
      const diff = symptoms.count - symptoms.prev_count;
      symptomText += diff === 0
        ? '；较上期持平'
        : `；较上期${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)} 条`;
    }
  }
  lines.push(['症状日记', symptomText]);

  const meds = data.medications;
  const doses = Number(meds.doses) || 0;
  const skippedDoses = Number(meds.skips) || 0;
  let medicationText = '没有在用药物';
  if (meds.active) {
    medicationText = `在用 ${meds.active} 种，${rangeDaysLabel(days)}打卡 ${doses} 次`;
    if (meds.adherence && meds.adherence.expected) {
      medicationText += `，依从率 ${meds.adherence.rate}%`;
    }
  } else if (doses > 0 || skippedDoses > 0) {
    medicationText = `没有在用药物，${rangeDaysLabel(days)}打卡 ${doses} 次`;
  }
  if (skippedDoses > 0) {
    const reasons = (meds.skip_reasons || [])
      .map((item) => `${item.label} ${item.count} 次`)
      .join('、');
    medicationText += `，未服 ${skippedDoses} 次`
      + (reasons ? `（${reasons}）` : '');
  }
  if (meds.active || doses > 0 || skippedDoses > 0) {
    const details = [];
    if (meds.by_medication && meds.by_medication.length) {
      details.push(
        meds.by_medication
          .map(
            (item) => `${item.name} ${item.doses}`
              + (item.expected ? `/${item.expected}` : '')
              + ' 次'
          )
          .join('、')
      );
    }
    if (meds.prev_doses !== null && meds.prev_doses !== undefined) {
      const diff = doses - meds.prev_doses;
      details.push(diff === 0
        ? '较上期持平'
        : `较上期${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)} 次`);
    }
    if (details.length) {
      medicationText += `（${details.join('；')}）`;
    }
  }
  lines.push(['用药', medicationText]);

  const formatValue = (value) => {
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
      : String(value == null ? '' : value);
  };

  const labs = data.labs || {};
  let labText = `${rangeDaysLabel(days)}没有化验记录`;
  if (labs.count) {
    labText = `${labs.count} 条`;
    if (labs.critical) labText += `，危急值 ${labs.critical} 项`;
    if (labs.abnormal) labText += `，异常 ${labs.abnormal} 项`;
    if (labs.items && labs.items.length) {
      labText += `（${labs.items.map((item) => (
        `${item.item} ${formatValue(item.value)}`
        + `${item.unit ? ` ${item.unit}` : ''}`
        + `${item.abnormal_low ? ' 偏低' : ' 偏高'}`
      )).join('、')}）`;
    }
    if (labs.prev_count !== null && labs.prev_count !== undefined) {
      const diff = labs.count - Number(labs.prev_count);
      labText += diff === 0
        ? '；较上期持平'
        : `；较上期${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)} 条`;
      if (
        labs.prev_abnormal !== null
        && labs.prev_abnormal !== undefined
        && Number(labs.prev_abnormal) !== Number(labs.abnormal)
      ) {
        const abnormalDiff = Number(labs.abnormal) - Number(labs.prev_abnormal);
        labText += abnormalDiff > 0
          ? `，异常增加 ${abnormalDiff} 项`
          : `，异常减少 ${Math.abs(abnormalDiff)} 项`;
      }
    }
  }
  lines.push(['化验', labText]);

  const contexts = data.metric_contexts || {};
  const contextParts = [];
  for (const [metric, rows] of Object.entries(contexts)) {
    if (!Array.isArray(rows) || !rows.length) continue;
    const metricLabel = METRIC_NAMES[metric] || metric;
    const text = rows.map((row) => (
      `${row.label} ${formatValue(row.avg_value1)}`
      + (row.avg_value2 !== null && row.avg_value2 !== undefined
        ? `/${formatValue(row.avg_value2)}`
        : '')
      + `（${row.count} 次）`
    )).join('、');
    contextParts.push(`${metricLabel}：${text}`);
  }
  if (contextParts.length) {
    lines.push(['分情境均值', contextParts.join('；')]);
  }

  const critical = data.critical_alerts || {};
  if (Number(critical.count)) {
    let criticalText = `${critical.count} 项`;
    if (critical.items && critical.items.length) {
      criticalText += `（${critical.items.join('、')}）`;
    }
    lines.push(['危急值', criticalText]);
  }

  const vaccinations = data.vaccinations || {};
  const vaccineParts = [];
  if (Number(vaccinations.count)) {
    vaccineParts.push(`近 ${days} 天接种 ${vaccinations.count} 剂`);
  }
  if (Number(vaccinations.overdue)) {
    vaccineParts.push(`逾期 ${vaccinations.overdue} 剂`);
  }
  if (vaccinations.upcoming && vaccinations.upcoming.length) {
    vaccineParts.push(
      `未来 7 天 ${vaccinations.upcoming.map((item) => (
        `${item.vaccine_name || '疫苗'}`
        + ` 第${Number(item.dose_number) || 1}剂 ${item.next_due || ''}`
      )).join('、')}`
    );
  }
  if (vaccineParts.length) {
    lines.push(['疫苗', vaccineParts.join('，')]);
  }

  const care = data.care_plan || {};
  const careParts = [];
  if (Number(care.completed)) {
    careParts.push(`近 ${days} 天完成 ${care.completed} 项`);
  }
  if (Number(care.overdue)) {
    careParts.push(`逾期 ${care.overdue} 项`);
  }
  if (Number(care.due_in_7_days)) {
    careParts.push(`未来 7 天 ${care.due_in_7_days} 项`);
  }
  if (care.items && care.items.length) {
    careParts.push(
      care.items.map((item) => (
        `${item.title || '复查'} ${item.next_due_at || ''}`
      )).join('、')
    );
  }
  if (careParts.length) {
    lines.push(['复查计划', careParts.join('，')]);
  }

  const appointments = data.appointments || {};
  const visits = data.visits || {};
  const visitParts = [];
  if (appointments.upcoming && appointments.upcoming.length) {
    visitParts.push(
      `未来 7 天预约 ${appointments.upcoming.map((item) => (
        `${String(item.scheduled_at || '').slice(0, 10)}`
        + ` ${item.title || '就诊'}`
      )).join('、')}`
    );
  }
  if (Number(appointments.overdue)) {
    visitParts.push(`逾期未就诊 ${appointments.overdue} 个`);
  }
  if (Number(appointments.completed)) {
    visitParts.push(`近 ${days} 天完成 ${appointments.completed} 个预约`);
  }
  if (Number(visits.count)) {
    visitParts.push(`就诊 ${visits.count} 次`);
  }
  if (visits.latest && visits.latest.diagnosis) {
    visitParts.push(`最近诊断 ${visits.latest.diagnosis}`);
  }
  if (visits.upcoming_followups && visits.upcoming_followups.length) {
    visitParts.push(
      `未来复诊 ${visits.upcoming_followups.map((item) => (
        `${item.next_followup_date || ''}`
        + ` ${item.diagnosis || '复查'}`
      )).join('、')}`
    );
  }
  if (visitParts.length) {
    lines.push(['就诊与预约', visitParts.join('；')]);
  }

  const conditions = data.conditions || {};
  if (Number(conditions.active)) {
    let conditionText = `${conditions.active} 项`;
    if (conditions.names && conditions.names.length) {
      conditionText += `（${conditions.names.join('、')}）`;
    }
    lines.push(['慢病清单', conditionText]);
  }

  const consultations = data.consultations || {};
  const consultCount = Number(consultations.count) || 0;
  let consultationText = `${rangeDaysLabel(days)}没有会诊`;
  if (consultCount) {
    consultationText = `${consultCount} 次`;
    const previousConsult = consultations.prev_count;
    if (previousConsult !== null && previousConsult !== undefined) {
      const diff = consultCount - Number(previousConsult);
      consultationText += diff === 0
        ? '，较上期持平'
        : `，较上期${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)} 次`;
    }
    const riskLabel = HEALTH_RISK_LABELS[consultations.risk];
    if (riskLabel) consultationText += `，最近一次 ${riskLabel}`;
  }
  lines.push(['会诊', consultationText]);

  lines.push([
    '随访提醒',
    `待办 ${data.reminders.pending} 条，逾期 ${data.reminders.overdue} 条`
    + `，7 天内到期 ${data.reminders.due_in_7_days} 条`,
  ]);
  const streak = data.streak || {};
  if (Number(streak.current) > 0) {
    lines.push([
      '连续记录',
      `已连续 ${streak.current} 天，近 30 天记录 ${streak.days_last_30} 天`,
    ]);
  }
  return lines;
}

const INSIGHT_LEVEL_LABELS = {
  danger: '危急',
  warning: '需关注',
  info: '提示',
  ok: '正常',
};

let healthInsightRequestSeq = 0;

function setInsightSummary(data) {
  if (!els.insightSummary) return;
  const counts = data.counts || {};
  const parts = [];
  if (Number(counts.danger)) parts.push(`危急 ${counts.danger}`);
  if (Number(counts.warning)) parts.push(`需关注 ${counts.warning}`);
  if (Number(counts.info)) parts.push(`提示 ${counts.info}`);
  els.insightSummary.textContent = parts.length
    ? `· ${parts.join(' · ')}`
    : '· 暂无';
}

async function loadHealthInsights() {
  if (!els.insightList) return;
  const requestSeq = ++healthInsightRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/health/insights?days=30`
      + `&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) {
      throw new Error(`服务返回 ${res.status}`);
    }
    const data = await res.json();
    if (requestSeq !== healthInsightRequestSeq) return;
    renderHealthInsights(data);
  } catch {
    if (requestSeq !== healthInsightRequestSeq) return;
    if (els.insightList) els.insightList.replaceChildren();
    els.insightEmpty.textContent = '健康提示加载失败，请稍后重试。';
    els.insightEmpty.hidden = false;
  }
}

function renderHealthInsights(data) {
  if (!els.insightList) return;
  els.insightList.replaceChildren();
  const insights = data.insights || [];
  setInsightSummary(data);
  els.insightEmpty.hidden = insights.length > 0;
  els.insightEmpty.textContent = '暂未发现需要特别关注的问题。';
  for (const item of insights) {
    const li = document.createElement('li');
    li.className = 'metric-item insight-item';
    const main = document.createElement('div');
    main.className = 'metric-main';
    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.title || '';
    const badge = document.createElement('span');
    badge.className = `insight-level-badge is-${item.level || 'info'}`;
    badge.textContent = INSIGHT_LEVEL_LABELS[item.level] || '提示';
    title.append(' ', badge);
    main.appendChild(title);
    if (item.detail) {
      const detail = document.createElement('span');
      detail.className = 'metric-meta';
      detail.textContent = item.detail;
      main.appendChild(detail);
    }
    if (item.action) {
      const action = document.createElement('span');
      action.className = 'metric-note';
      action.textContent = item.action;
      main.appendChild(action);
    }
    li.appendChild(main);
    if (item.section) {
      const actions = document.createElement('div');
      actions.className = 'metric-item-actions';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'metric-edit';
      open.textContent = '查看';
      open.setAttribute('aria-label', `查看相关记录：${item.title}`);
      open.addEventListener(
        'click', () => openInsightSection(item)
      );
      actions.appendChild(open);
      li.appendChild(actions);
    }
    els.insightList.appendChild(li);
  }
}

function openInsightSection(insight) {
  const section = typeof insight === 'string'
    ? insight
    : insight && insight.section;
  if (!section) return;
  if (section === 'attachment') {
    const target = document.getElementById('attachments-section');
    if (target) {
      target.open = true;
      target.scrollIntoView({ block: 'nearest' });
    }
    const query = insight && insight.query
      ? String(insight.query) : '';
    if (query && els.attachmentFilterQ) {
      setAttachmentRefFilter(null);
      if (els.attachmentFilterKind) els.attachmentFilterKind.value = '';
      if (els.attachmentFilterLinked) {
        els.attachmentFilterLinked.value = '';
      }
      els.attachmentFilterQ.value = query;
      loadAttachments();
      return;
    }
    checkAttachmentIntegrity();
    return;
  }
  const profile = els.metricProfile ? els.metricProfile.value : '';
  if (section === 'metric') {
    const metric = insight && insight.metric ? insight.metric : '';
    locateHealthRecord({ type: 'metric', profile, metric }, '');
    return;
  }
  locateHealthRecord({ type: section, profile }, '');
}

let quickEntryPreview = null;
let quickEntryRequestSeq = 0;
let quickEntrySaving = false;
let quickEntryUndoing = false;
let quickEntryLastSavedId = 0;

function resetQuickEntryUndo() {
  quickEntryLastSavedId = 0;
  if (els.quickEntryUndo) els.quickEntryUndo.hidden = true;
}

function setQuickEntryStatus(text, isError = false) {
  if (!els.quickEntryStatus) return;
  els.quickEntryStatus.textContent = text || '';
  els.quickEntryStatus.hidden = !text;
  els.quickEntryStatus.classList.toggle('error', Boolean(isError));
}

function clearQuickEntryPreview() {
  quickEntryPreview = null;
  if (els.quickEntryPreview) {
    els.quickEntryPreview.replaceChildren();
    els.quickEntryPreview.hidden = true;
  }
  if (els.quickEntrySave) els.quickEntrySave.hidden = true;
  if (els.quickEntryCancel) els.quickEntryCancel.hidden = true;
}

function formatQuickEntryNumber(value) {
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
    : String(value == null ? '' : value);
}

function renderQuickEntryPreview(preview) {
  quickEntryPreview = preview;
  if (!els.quickEntryPreview) return;
  const first = formatQuickEntryNumber(preview.value1);
  const value = preview.value2 === null || preview.value2 === undefined
    ? first
    : `${first}/${formatQuickEntryNumber(preview.value2)}`;
  const parts = [
    `${preview.label || '指标'} ${value} ${preview.unit || ''}`.trim(),
  ];
  if (preview.context_label) parts.push(preview.context_label);
  else if (preview.supports_context) parts.push('未指定情境');
  els.quickEntryPreview.textContent = parts.join(' · ');
  els.quickEntryPreview.hidden = false;
  if (els.quickEntrySave) els.quickEntrySave.hidden = false;
  if (els.quickEntryCancel) els.quickEntryCancel.hidden = false;
}

async function parseQuickEntry() {
  if (!els.quickEntryInput) return;
  const text = els.quickEntryInput.value.trim();
  if (!text) {
    clearQuickEntryPreview();
    setQuickEntryStatus('请输入要记录的内容，例如：血压 150/95 早上。', true);
    return;
  }
  const requestSeq = ++quickEntryRequestSeq;
  clearQuickEntryPreview();
  setQuickEntryStatus('正在解析…');
  try {
    const res = await fetch('/api/metrics/quick-parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== quickEntryRequestSeq) return;
    if (!res.ok) {
      clearQuickEntryPreview();
      setQuickEntryStatus(
        data.detail || '无法解析这句话，请换一种写法。', true
      );
      return;
    }
    renderQuickEntryPreview(data.preview || {});
    setQuickEntryStatus('已解析，请确认后保存。');
  } catch (err) {
    if (requestSeq !== quickEntryRequestSeq) return;
    clearQuickEntryPreview();
    setQuickEntryStatus(`解析失败：${err.message || err}`, true);
  }
}

async function saveQuickEntry() {
  if (!quickEntryPreview || quickEntrySaving) return;
  const preview = quickEntryPreview;
  quickEntrySaving = true;
  if (els.quickEntrySave) els.quickEntrySave.disabled = true;
  try {
    const payload = {
      profile: els.metricProfile ? els.metricProfile.value : '',
      metric: preview.metric,
      value1: preview.value1,
      value2: preview.value2 === null || preview.value2 === undefined
        ? null
        : preview.value2,
      measured_at: metricNowLocal(),
      context: preview.context || '',
      note: String(preview.text || '').slice(0, 300),
    };
    const res = await fetch('/api/metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    quickEntryLastSavedId = data.metric && data.metric.id
      ? data.metric.id
      : 0;
    if (els.quickEntryUndo) {
      els.quickEntryUndo.hidden = !quickEntryLastSavedId;
    }
    const alerts = data.critical_alerts || [];
    clearQuickEntryPreview();
    if (els.quickEntryInput) els.quickEntryInput.value = '';
    if (alerts.length) {
      setQuickEntryStatus(
        `已保存。${alerts[0].message || '该数值达到危急值，请立即就医。'}`,
        true
      );
    } else {
      setQuickEntryStatus('已保存快速记录。');
    }
    await refreshMetrics();
    loadBpMonitoring();
    refreshOverview();
  } catch (err) {
    setQuickEntryStatus(`保存失败：${err.message || err}`, true);
  } finally {
    quickEntrySaving = false;
    if (els.quickEntrySave) els.quickEntrySave.disabled = false;
  }
}

async function undoQuickEntry() {
  if (!quickEntryLastSavedId || quickEntryUndoing) return;
  quickEntryUndoing = true;
  try {
    const res = await fetch(`/api/metrics/${quickEntryLastSavedId}`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) {
      throw new Error(`服务返回 ${res.status}`);
    }
    resetQuickEntryUndo();
    setQuickEntryStatus('已撤销刚才的记录。');
    await refreshMetrics();
    loadBpMonitoring();
    refreshOverview();
  } catch (err) {
    setQuickEntryStatus(`撤销失败：${err.message || err}`, true);
  } finally {
    quickEntryUndoing = false;
  }
}

let bpMonitoringRequestSeq = 0;
let bpMonitoringCache = null;
let bpMonitoringStartAuto = true;

let attachmentRequestSeq = 0;
let attachmentRefRequestSeq = 0;
let attachmentIntegrityRequestSeq = 0;
let attachmentOrphanCount = 0;
let attachmentCleanableCount = 0;
let attachmentCleaning = false;
let attachmentExporting = false;
let attachmentUploading = false;
let attachmentLoadingMore = false;
let attachmentHasMore = false;
let attachmentTotalCount = 0;
let attachmentTotalSize = 0;
let attachmentItemsCache = [];
let attachmentSelectedIds = new Set();
let attachmentDeleting = false;
let attachmentLinking = false;
let attachmentLinkRefRequestSeq = 0;
let attachmentPreviewUrl = null;
let attachmentPreviewItem = null;
let attachmentRefCounts = { visit: {}, lab: {} };
let attachmentRefFilter = null;
let attachmentFilterTimer = null;
const ATTACHMENT_PAGE_SIZE = 100;
const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
const ATTACHMENT_MAX_BATCH = 5;
const ATTACHMENT_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'pdf'];

function setAttachmentStatus(text, isError = false) {
  if (!els.attachmentStatus) return;
  els.attachmentStatus.textContent = text || '';
  els.attachmentStatus.hidden = !text;
  els.attachmentStatus.classList.toggle('error', Boolean(isError));
}

function setAttachmentIntegrityStatus(text, isError = false) {
  if (!els.attachmentIntegrityStatus) return;
  els.attachmentIntegrityStatus.textContent = text || '';
  els.attachmentIntegrityStatus.hidden = !text;
  els.attachmentIntegrityStatus.classList.toggle(
    'error', Boolean(isError)
  );
}

async function checkAttachmentIntegrity(prefix = '', force = false) {
  if (!els.attachmentIntegrityStatus) return;
  if (
    !force
    && (attachmentExporting || attachmentCleaning || attachmentUploading)
  ) {
    return;
  }
  const requestSeq = ++attachmentIntegrityRequestSeq;
  setAttachmentIntegrityStatus('正在检查附件文件…');
  try {
    const res = await fetch('/api/attachments/integrity');
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== attachmentIntegrityRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const orphanTotal = Number(data.orphan_count) || 0;
    const recent = Number(data.recent_orphan_count) || 0;
    attachmentOrphanCount = orphanTotal;
    attachmentCleanableCount = Math.max(0, orphanTotal - recent);
    const parts = [
      `登记 ${data.db_count || 0} 份，目录 ${data.file_count || 0} 个文件`,
    ];
    const missing = Number(data.missing_count) || 0;
    if (missing) {
      const names = (data.missing || [])
        .slice(0, 3)
        .map((item) => item.original_name || item.stored_name)
        .filter(Boolean)
        .join('、');
      parts.push(`缺失 ${missing} 份${names ? `（${names}）` : ''}`);
    }
    const mismatch = Number(data.size_mismatch_count) || 0;
    if (mismatch) {
      const names = (data.size_mismatch || [])
        .slice(0, 3)
        .map((item) => item.original_name || item.stored_name)
        .filter(Boolean)
        .join('、');
      parts.push(
        `大小不一致 ${mismatch} 份${names ? `（${names}）` : ''}`
      );
    }
    if (orphanTotal) {
      parts.push(
        `孤儿文件 ${orphanTotal} 个`
        + (recent ? `（${recent} 个刚写入，暂不清理）` : '')
      );
    }
    if (!missing && !orphanTotal && !mismatch) parts.push('未发现异常');
    const summary = parts.join('；');
    setAttachmentIntegrityStatus(
      prefix ? `${prefix}；${summary}` : summary,
      missing > 0 || mismatch > 0,
    );
    if (els.attachmentCleanup) {
      els.attachmentCleanup.hidden = attachmentCleanableCount === 0;
    }
  } catch (err) {
    if (requestSeq !== attachmentIntegrityRequestSeq) return;
    attachmentOrphanCount = 0;
    attachmentCleanableCount = 0;
    if (els.attachmentCleanup) els.attachmentCleanup.hidden = true;
    const message = `检查失败：${err.message || err}`;
    setAttachmentIntegrityStatus(
      prefix ? `${prefix}；${message}` : message, true
    );
  }
}

function refreshAttachmentIntegrityIfVisible() {
  if (
    els.attachmentIntegrityStatus
    && !els.attachmentIntegrityStatus.hidden
  ) {
    checkAttachmentIntegrity();
  }
}

async function cleanupAttachmentOrphans() {
  if (
    !attachmentCleanableCount
    || attachmentCleaning
    || attachmentExporting
    || attachmentUploading
  ) {
    return;
  }
  if (!window.confirm(
    `清理 ${attachmentCleanableCount} 个没有登记到数据库的附件文件？`
    + '该操作不可恢复；刚写入的文件会自动保留。'
  )) {
    return;
  }
  attachmentCleaning = true;
  if (els.attachmentCleanup) els.attachmentCleanup.disabled = true;
  setAttachmentIntegrityStatus('正在清理孤儿文件…');
  let conflict = false;
  try {
    const res = await fetch('/api/attachments/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirm: true,
        expected_orphans: attachmentOrphanCount,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      conflict = res.status === 409;
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const parts = [`已清理 ${data.removed || 0} 个孤儿文件`];
    if (data.failed) parts.push(`${data.failed} 个删除失败`);
    if (data.skipped) parts.push(`${data.skipped} 个新文件已保留`);
    const summary = parts.join('，');
    setAttachmentIntegrityStatus(summary);
    await checkAttachmentIntegrity(summary, true);
  } catch (err) {
    if (conflict) {
      await checkAttachmentIntegrity(
        '孤儿文件数量已变化，请重新确认', true
      );
    } else {
      setAttachmentIntegrityStatus(
        `清理失败：${err.message || err}`, true
      );
    }
  } finally {
    attachmentCleaning = false;
    if (els.attachmentCleanup) els.attachmentCleanup.disabled = false;
  }
}

async function exportAttachmentArchive(ids = null) {
  if (
    attachmentExporting
    || attachmentCleaning
    || attachmentUploading
  ) {
    return;
  }
  const selectedIds = Array.isArray(ids) ? ids : [];
  const params = new URLSearchParams();
  for (const id of selectedIds) params.append('ids', String(id));
  const url = selectedIds.length
    ? `/api/attachments/export.zip?${params.toString()}`
    : '/api/attachments/export.zip';
  attachmentExporting = true;
  attachmentIntegrityRequestSeq += 1;
  if (els.attachmentExport) els.attachmentExport.disabled = true;
  updateAttachmentSelection();
  setAttachmentIntegrityStatus(
    selectedIds.length
      ? `正在打包选中的 ${selectedIds.length} 份附件…`
      : '正在打包附件…'
  );
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);
  try {
    const res = await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const exported = Number(
      res.headers.get('X-Medix-Attachment-Count')
    ) || 0;
    const missing = Number(
      res.headers.get('X-Medix-Attachment-Missing')
    ) || 0;
    const blob = await res.blob();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      blob,
      selectedIds.length
        ? `medix-attachments-selected-${stamp}.zip`
        : `medix-attachments-${stamp}.zip`,
      'application/zip'
    );
    setAttachmentIntegrityStatus(
      (selectedIds.length
        ? `已导出选中的 ${exported} 份附件`
        : `已导出全部档案附件包（共 ${exported} 份）`)
      + (missing
        ? `，${missing} 份文件缺失或无法读取，仅写入清单`
        : '')
      + '。',
      missing > 0
    );
  } catch (err) {
    const message = err.name === 'AbortError'
      ? '导出超时，请稍后重试'
      : (err.message || err);
    setAttachmentIntegrityStatus(
      `导出附件包失败：${message}`, true
    );
  } finally {
    clearTimeout(timer);
    attachmentExporting = false;
    if (els.attachmentExport) els.attachmentExport.disabled = false;
    updateAttachmentSelection();
  }
}

function exportSelectedAttachments() {
  if (!attachmentSelectedIds.size) return;
  exportAttachmentArchive([...attachmentSelectedIds]);
}

function formatAttachmentSize(bytes) {
  const size = Number(bytes) || 0;
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) return `${Math.round(size / 1024)} KB`;
  return `${size} B`;
}

function renderAttachments(items, options = {}) {
  if (!els.attachmentList) return;
  const append = Boolean(options.append);
  if (!append) {
    attachmentItemsCache = [];
    attachmentSelectedIds.clear();
    els.attachmentList.replaceChildren();
  }
  attachmentItemsCache = attachmentItemsCache.concat(items);
  attachmentHasMore = Boolean(options.hasMore);
  els.attachmentsSummary.textContent =
    `· ${attachmentTotalCount} 份`
    + (attachmentTotalSize > 0
      ? ` · ${formatAttachmentSize(attachmentTotalSize)}`
      : '')
    + (options.filtered ? '（已筛选）' : '');
  els.attachmentEmpty.hidden = attachmentItemsCache.length > 0;
  els.attachmentEmpty.textContent =
    options.emptyText || '还没有就医资料。';
  els.attachmentMore.hidden = !attachmentHasMore;
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'metric-item';
    const select = document.createElement('input');
    select.type = 'checkbox';
    select.className = 'attachment-select';
    select.checked = attachmentSelectedIds.has(item.id);
    select.setAttribute(
      'aria-label', `选择附件：${item.original_name || '附件'}`
    );
    select.addEventListener('change', () => {
      if (select.checked) {
        attachmentSelectedIds.add(item.id);
      } else {
        attachmentSelectedIds.delete(item.id);
      }
      updateAttachmentSelection();
    });
    li.appendChild(select);
    const main = document.createElement('div');
    main.className = 'metric-main';
    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.original_name || '附件';
    const badge = document.createElement('span');
    badge.className = 'metric-meta';
    badge.textContent = item.kind_label || '其他';
    title.append(' ', badge);
    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    const linkedLabel = item.ref_id
      ? (item.kind === 'visit'
        ? '已关联就诊'
        : item.kind === 'lab' ? '已关联化验' : '已关联')
        + (item.ref_label ? ` · ${item.ref_label}` : '')
      : '';
    meta.textContent = [
      formatAttachmentSize(item.size),
      String(item.created_at || '').replace('T', ' ').slice(0, 16),
      linkedLabel,
      item.note || '',
    ].filter(Boolean).join(' · ');
    main.append(title, meta);
    if (item.file_missing) {
      const missing = document.createElement('span');
      missing.className = 'metric-note attachment-missing';
      missing.textContent = '文件缺失，请运行完整性检查';
      main.appendChild(missing);
    }
    li.appendChild(main);
    const actions = document.createElement('div');
    actions.className = 'metric-item-actions';
    const noteEdit = document.createElement('button');
    noteEdit.type = 'button';
    noteEdit.className = 'metric-edit';
    noteEdit.textContent = '备注';
    noteEdit.setAttribute(
      'aria-label', `编辑附件备注：${item.original_name}`
    );
    noteEdit.addEventListener('click', () => editAttachmentNote(item));
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'metric-edit';
    view.textContent = '查看';
    view.setAttribute('aria-label', `查看附件：${item.original_name}`);
    view.addEventListener('click', () => openAttachmentFile(item));
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'metric-edit';
    download.textContent = '下载';
    download.setAttribute('aria-label', `下载附件：${item.original_name}`);
    download.addEventListener(
      'click', () => openAttachmentFile(item, true)
    );
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'metric-delete';
    del.textContent = '删除';
    del.setAttribute('aria-label', `删除附件：${item.original_name}`);
    del.addEventListener('click', () => deleteAttachment(item));
    actions.append(noteEdit, view, download, del);
    li.appendChild(actions);
    els.attachmentList.appendChild(li);
  }
  updateAttachmentSelection();
}

async function loadAttachments(append = false) {
  if (!els.attachmentList || !metricsOpen) return;
  if (append && (!attachmentHasMore || attachmentLoadingMore)) return;
  if (!append) {
    attachmentLoadingMore = false;
    attachmentHasMore = false;
  }
  const requestSeq = ++attachmentRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const filterKind = els.attachmentFilterKind
    ? els.attachmentFilterKind.value : '';
  const filterSort = els.attachmentFilterSort
    ? els.attachmentFilterSort.value : '';
  const filterLinked = els.attachmentFilterLinked
    ? els.attachmentFilterLinked.value : '';
  const filterQuery = els.attachmentFilterQ
    ? els.attachmentFilterQ.value.trim() : '';
  const filterStart = els.attachmentFilterStart
    ? els.attachmentFilterStart.value : '';
  const filterEnd = els.attachmentFilterEnd
    ? els.attachmentFilterEnd.value : '';
  const hasDateFilter = Boolean(filterStart || filterEnd);
  const hasFilter = Boolean(
    filterKind || filterLinked || filterQuery || hasDateFilter
    || attachmentRefFilter
  );
  const offset = append ? attachmentItemsCache.length : 0;
  if (append) {
    attachmentLoadingMore = true;
    els.attachmentMore.disabled = true;
  }
  try {
    const params = new URLSearchParams({
      limit: String(ATTACHMENT_PAGE_SIZE),
      offset: String(offset),
      profile,
    });
    if (attachmentRefFilter) {
      params.set('kind', attachmentRefFilter.kind);
      params.set('ref_id', String(attachmentRefFilter.refId));
    } else if (filterKind) {
      params.set('kind', filterKind);
    }
    if (filterSort) params.set('sort', filterSort);
    if (filterLinked) params.set('linked', filterLinked);
    if (filterQuery) params.set('q', filterQuery);
    if (filterStart) params.set('start', filterStart);
    if (filterEnd) params.set('end', filterEnd);
    const res = await fetch(`/api/attachments?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== attachmentRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const items = data.attachments || [];
    attachmentTotalCount = Number(data.total_count) || 0;
    attachmentTotalSize = Number(data.total_size) || 0;
    renderAttachments(items, {
      append,
      hasMore: items.length >= ATTACHMENT_PAGE_SIZE,
      filtered: hasFilter,
      emptyText: hasFilter
        ? '没有符合条件的资料。'
        : '还没有就医资料。',
    });
  } catch (err) {
    if (requestSeq !== attachmentRequestSeq) return;
    if (append) {
      setAttachmentStatus(`加载更多失败：${err.message || err}`, true);
    } else {
      attachmentItemsCache = [];
      attachmentTotalCount = 0;
      attachmentTotalSize = 0;
      els.attachmentList.replaceChildren();
      els.attachmentsSummary.textContent = '';
      els.attachmentMore.hidden = true;
      els.attachmentEmpty.textContent =
        `加载失败：${err.message || err}`;
      els.attachmentEmpty.hidden = false;
    }
  } finally {
    if (requestSeq === attachmentRequestSeq) {
      attachmentLoadingMore = false;
      if (els.attachmentMore) els.attachmentMore.disabled = false;
    }
  }
}

async function loadAttachmentRefCounts() {
  if (!metricsOpen) return;
  try {
    const res = await fetch('/api/attachments/counts');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return;
    const counts = data.counts || {};
    attachmentRefCounts = {
      visit: counts.visit || {},
      lab: counts.lab || {},
    };
    if (els.visitList) renderVisits(filterVisits(visitItemsCache));
    if (els.labList) renderLabs(filterLabs(labItemsCache));
  } catch {
    // 数量统计失败不影响记录列表
  }
}

function setAttachmentRefFilter(filter) {
  attachmentRefFilter = filter;
  if (els.attachmentRefFilter) {
    els.attachmentRefFilter.hidden = !filter;
  }
  if (els.attachmentRefFilterLabel) {
    els.attachmentRefFilterLabel.textContent = filter
      ? `仅看：${filter.label}` : '';
  }
}

function clearAttachmentRefFilter() {
  setAttachmentRefFilter(null);
  loadAttachments();
}

async function openAttachmentListForRecord(kind, item) {
  if (!item || !['visit', 'lab'].includes(kind)) return;
  const profile = item.profile || '';
  if (els.metricProfile) els.metricProfile.value = profile;
  await openMetrics();
  if (els.metricProfile && els.metricProfile.value !== profile) {
    els.metricProfile.value = profile;
    els.metricProfile.dispatchEvent(
      new Event('change', { bubbles: true })
    );
  }
  setAttachmentRefFilter({
    kind,
    refId: item.id,
    label: attachmentRefLabel(kind, item).slice(0, 80)
      || (kind === 'visit' ? '就诊记录' : '化验记录'),
  });
  if (els.attachmentFilterKind) els.attachmentFilterKind.value = '';
  if (els.attachmentFilterLinked) els.attachmentFilterLinked.value = '';
  if (els.attachmentFilterQ) els.attachmentFilterQ.value = '';
  if (els.attachmentFilterStart) els.attachmentFilterStart.value = '';
  if (els.attachmentFilterEnd) els.attachmentFilterEnd.value = '';
  const section = document.getElementById('attachments-section');
  if (section) {
    section.open = true;
    section.scrollIntoView({ block: 'start' });
  }
  await loadAttachments();
}

function attachmentRefLabel(kind, item) {
  const date = String(
    item.visit_date || item.tested_at || ''
  ).slice(0, 10);
  if (kind === 'visit') {
    return [
      date,
      item.department,
      item.diagnosis,
    ].filter(Boolean).join(' · ') || `就诊记录 #${item.id}`;
  }
  const value = item.value === null || item.value === undefined
    ? '' : String(item.value);
  const result = `${item.item || '化验'} ${value}`.trim()
    + (item.unit ? ` ${item.unit}` : '');
  return [
    date,
    result,
    item.panel,
  ].filter(Boolean).join(' · ');
}

async function loadAttachmentRefOptions() {
  if (!els.attachmentRef || !els.attachmentRefWrap) return;
  if (!metricsOpen || attachmentUploading) return;
  const kind = els.attachmentKind.value;
  if (!['visit', 'lab'].includes(kind)) {
    attachmentRefRequestSeq += 1;
    els.attachmentRefWrap.hidden = true;
    els.attachmentRef.replaceChildren();
    els.attachmentRef.disabled = false;
    return;
  }
  els.attachmentRefWrap.hidden = false;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const requestSeq = ++attachmentRefRequestSeq;
  els.attachmentRef.disabled = true;
  const loading = document.createElement('option');
  loading.value = '0';
  loading.textContent = '正在加载关联记录…';
  els.attachmentRef.replaceChildren(loading);
  const params = new URLSearchParams({ limit: '1000' });
  // 空档案名表示“未指定”记录，后端按字符串相等校验，仍可关联
  params.set('profile', profile);
  const url = kind === 'visit'
    ? `/api/visits?${params.toString()}`
    : `/api/lab-results?${params.toString()}`;
  try {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== attachmentRefRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const items = kind === 'visit'
      ? (data.visits || [])
      : (data.lab_results || []);
    const placeholder = document.createElement('option');
    placeholder.value = '0';
    placeholder.textContent = items.length
      ? '不关联'
      : '暂无可关联记录';
    const options = [placeholder];
    for (const item of items) {
      const option = document.createElement('option');
      option.value = String(item.id);
      option.textContent = attachmentRefLabel(kind, item).slice(0, 60);
      options.push(option);
    }
    els.attachmentRef.replaceChildren(...options);
  } catch (err) {
    if (requestSeq !== attachmentRefRequestSeq) return;
    const option = document.createElement('option');
    option.value = '0';
    option.textContent = '关联记录加载失败，可不关联';
    els.attachmentRef.replaceChildren(option);
  } finally {
    if (requestSeq === attachmentRefRequestSeq && !attachmentUploading) {
      els.attachmentRef.disabled = false;
    }
  }
}

function refreshAttachmentReferences() {
  loadAttachmentRefOptions();
  loadAttachments();
  loadAttachmentRefCounts();
}

function scheduleAttachmentFilterSearch() {
  // 输入变化立即作废进行中的请求，避免旧结果覆盖新关键字
  attachmentRequestSeq += 1;
  attachmentLoadingMore = false;
  attachmentHasMore = false;
  attachmentSelectedIds.clear();
  updateAttachmentSelection();
  if (els.attachmentMore) {
    els.attachmentMore.hidden = true;
    els.attachmentMore.disabled = false;
  }
  clearTimeout(attachmentFilterTimer);
  attachmentFilterTimer = setTimeout(
    () => loadAttachments(), 250
  );
}

function selectedAttachmentItems() {
  return attachmentItemsCache.filter(
    (item) => attachmentSelectedIds.has(item.id)
  );
}

async function loadAttachmentLinkRefOptions() {
  if (!els.attachmentLinkRef || !els.attachmentLinkKind) return;
  const kind = els.attachmentLinkKind.value;
  if (!['visit', 'lab'].includes(kind)) return;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const requestSeq = ++attachmentLinkRefRequestSeq;
  els.attachmentLinkRef.disabled = true;
  const loading = document.createElement('option');
  loading.value = '0';
  loading.textContent = '正在加载关联记录…';
  els.attachmentLinkRef.replaceChildren(loading);
  const params = new URLSearchParams({ limit: '1000' });
  params.set('profile', profile);
  const url = kind === 'visit'
    ? `/api/visits?${params.toString()}`
    : `/api/lab-results?${params.toString()}`;
  try {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== attachmentLinkRefRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const items = kind === 'visit'
      ? (data.visits || [])
      : (data.lab_results || []);
    const placeholder = document.createElement('option');
    placeholder.value = '0';
    placeholder.textContent = items.length
      ? `请选择${kind === 'visit' ? '就诊' : '化验'}记录`
      : '暂无可关联记录';
    const options = [placeholder];
    for (const item of items) {
      const option = document.createElement('option');
      option.value = String(item.id);
      option.textContent = attachmentRefLabel(kind, item).slice(0, 60);
      options.push(option);
    }
    els.attachmentLinkRef.replaceChildren(...options);
  } catch (err) {
    if (requestSeq !== attachmentLinkRefRequestSeq) return;
    const option = document.createElement('option');
    option.value = '0';
    option.textContent = '关联记录加载失败';
    els.attachmentLinkRef.replaceChildren(option);
  } finally {
    if (requestSeq === attachmentLinkRefRequestSeq) {
      els.attachmentLinkRef.disabled = false;
    }
  }
}

async function openAttachmentLinkBar() {
  if (attachmentLinking || !els.attachmentLinkBar) return;
  const items = selectedAttachmentItems();
  const kind = items.length ? items[0].kind : '';
  if (
    !items.length
    || !['visit', 'lab'].includes(kind)
    || items.some((item) => item.kind !== kind)
  ) {
    setAttachmentStatus(
      '只有同类型的就诊资料或化验单可以批量关联。', true
    );
    return;
  }
  els.attachmentLinkBar.hidden = false;
  els.attachmentLinkKind.value = kind;
  await loadAttachmentLinkRefOptions();
  if (els.attachmentLinkRef) els.attachmentLinkRef.focus();
}

async function postAttachmentLink(ids, refId) {
  if (attachmentLinking || !ids.length) return false;
  attachmentLinking = true;
  updateAttachmentSelection();
  setAttachmentStatus(refId ? '正在关联附件…' : '正在取消关联…');
  try {
    const res = await fetch('/api/attachments/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, ref_id: refId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    attachmentSelectedIds.clear();
    if (els.attachmentLinkBar) els.attachmentLinkBar.hidden = true;
    await loadAttachments();
    loadAttachmentRefCounts();
    setAttachmentStatus(
      refId
        ? `已关联 ${data.updated || ids.length} 份附件。`
        : `已取消 ${data.updated || ids.length} 份附件的关联。`
    );
    return true;
  } catch (err) {
    setAttachmentStatus(`关联失败：${err.message || err}`, true);
    return false;
  } finally {
    attachmentLinking = false;
    updateAttachmentSelection();
  }
}

async function applyAttachmentLink() {
  if (attachmentLinking || !attachmentSelectedIds.size) return;
  const refId = els.attachmentLinkRef
    ? Number(els.attachmentLinkRef.value || 0) : 0;
  if (!refId) {
    setAttachmentStatus('请选择要关联的就诊或化验记录。', true);
    return;
  }
  await postAttachmentLink([...attachmentSelectedIds], refId);
}

async function unlinkSelectedAttachments() {
  if (attachmentLinking || !attachmentSelectedIds.size) return;
  const ids = [...attachmentSelectedIds];
  if (!window.confirm(`取消选中的 ${ids.length} 份附件的关联？`)) return;
  await postAttachmentLink(ids, 0);
}

function closeAttachmentLinkBar() {
  attachmentLinkRefRequestSeq += 1;
  if (els.attachmentLinkBar) els.attachmentLinkBar.hidden = true;
}

function onAttachmentDateChange() {
  const start = els.attachmentFilterStart
    ? els.attachmentFilterStart.value : '';
  const end = els.attachmentFilterEnd
    ? els.attachmentFilterEnd.value : '';
  if (start && end && start > end) {
    setAttachmentStatus('开始日期不能晚于结束日期。', true);
    return;
  }
  setAttachmentStatus('');
  loadAttachments();
}

function updateAttachmentSelection() {
  const total = attachmentItemsCache.length;
  const selectedItems = selectedAttachmentItems();
  const selectedVisible = selectedItems.length;
  const linkKind = selectedItems.length ? selectedItems[0].kind : '';
  const canLink = selectedItems.length > 0
    && ['visit', 'lab'].includes(linkKind)
    && selectedItems.every((item) => item.kind === linkKind);
  if (els.attachmentDeleteSelected) {
    els.attachmentDeleteSelected.hidden =
      attachmentSelectedIds.size === 0;
    els.attachmentDeleteSelected.textContent =
      `删除选中（${attachmentSelectedIds.size}）`;
    els.attachmentDeleteSelected.disabled =
      attachmentDeleting || attachmentLinking
      || attachmentSelectedIds.size === 0;
  }
  if (els.attachmentLinkSelected) {
    els.attachmentLinkSelected.hidden = !canLink;
    els.attachmentLinkSelected.textContent =
      `关联选中（${selectedItems.length}）`;
    els.attachmentLinkSelected.disabled =
      attachmentLinking || !canLink;
  }
  if (els.attachmentExportSelected) {
    els.attachmentExportSelected.hidden =
      attachmentSelectedIds.size === 0;
    els.attachmentExportSelected.textContent =
      `导出选中（${attachmentSelectedIds.size}）`;
    els.attachmentExportSelected.disabled =
      attachmentExporting || attachmentSelectedIds.size === 0;
  }
  if (!canLink && els.attachmentLinkBar) {
    els.attachmentLinkBar.hidden = true;
  }
  if (els.attachmentSelectAll) {
    els.attachmentSelectAll.checked =
      total > 0 && selectedVisible === total;
    els.attachmentSelectAll.disabled =
      attachmentDeleting || attachmentLinking || total === 0;
  }
}

async function deleteSelectedAttachments() {
  if (attachmentDeleting || !attachmentSelectedIds.size) return;
  const ids = [...attachmentSelectedIds];
  if (!window.confirm(
    `删除选中的 ${ids.length} 份附件？删除后无法恢复。`
  )) {
    return;
  }
  attachmentDeleting = true;
  updateAttachmentSelection();
  let removed = 0;
  let failed = 0;
  for (let index = 0; index < ids.length; index += 1) {
    setAttachmentStatus(
      `正在删除 ${index + 1}/${ids.length}…`
    );
    try {
      const res = await fetch(`/api/attachments/${ids[index]}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        removed += 1;
      } else {
        failed += 1;
      }
    } catch {
      failed += 1;
    }
  }
  attachmentSelectedIds.clear();
  attachmentDeleting = false;
  await loadAttachments();
    loadAttachmentRefCounts();
  refreshAttachmentIntegrityIfVisible();
  setAttachmentStatus(
    `已删除 ${removed} 份附件`
    + (failed ? `，${failed} 份失败` : '')
    + '。',
    failed > 0
  );
}

async function editAttachmentNote(item) {
  const current = item.note || '';
  const next = window.prompt('附件备注（最多 300 字）', current);
  if (next === null) return;
  const note = next.trim();
  if (note.length > 300) {
    setAttachmentStatus('附件备注不能超过 300 字。', true);
    return;
  }
  if (note === current.trim()) return;
  setAttachmentStatus('正在保存备注…');
  try {
    const res = await fetch(`/api/attachments/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setAttachmentStatus('附件备注已保存。');
    await loadAttachments();
    loadAttachmentRefCounts();
  } catch (err) {
    setAttachmentStatus(`备注保存失败：${err.message || err}`, true);
  }
}

function closeAttachmentPreview() {
  if (attachmentPreviewUrl) {
    URL.revokeObjectURL(attachmentPreviewUrl);
    attachmentPreviewUrl = null;
  }
  attachmentPreviewItem = null;
  if (els.attachmentPreviewBody) {
    els.attachmentPreviewBody.replaceChildren();
  }
  if (els.attachmentPreviewTitle) {
    els.attachmentPreviewTitle.textContent = '';
  }
  if (els.attachmentPreview) els.attachmentPreview.hidden = true;
}

function openAttachmentPreview(item, url) {
  if (!els.attachmentPreview || !els.attachmentPreviewBody) return false;
  closeAttachmentPreview();
  attachmentPreviewUrl = url;
  attachmentPreviewItem = item;
  if (els.attachmentPreviewTitle) {
    els.attachmentPreviewTitle.textContent = item.original_name || '附件';
  }
  const body = els.attachmentPreviewBody;
  const mime = String(item.mime || '');
  if (mime.startsWith('image/')) {
    const image = document.createElement('img');
    image.src = url;
    image.alt = item.original_name || '附件';
    body.appendChild(image);
  } else {
    const frame = document.createElement('iframe');
    frame.src = url;
    frame.title = item.original_name || '附件预览';
    body.appendChild(frame);
  }
  els.attachmentPreview.hidden = false;
  if (els.attachmentPreviewClose) els.attachmentPreviewClose.focus();
  return true;
}

function downloadPreviewAttachment() {
  if (!attachmentPreviewUrl || !attachmentPreviewItem) return;
  const anchor = document.createElement('a');
  anchor.href = attachmentPreviewUrl;
  anchor.download = attachmentPreviewItem.original_name || '附件';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function openAttachmentFile(item, download = false) {
  try {
    const res = await fetch(`/api/attachments/${item.id}/file`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (download) {
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = item.original_name || '附件';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } else if (!openAttachmentPreview(item, url)) {
      const opened = window.open(url, '_blank');
      if (!opened) {
        URL.revokeObjectURL(url);
        setAttachmentStatus(
          '浏览器拦截了新窗口，请允许弹窗后重试', true
        );
        return;
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
  } catch (err) {
    setAttachmentStatus(`打开附件失败：${err.message || err}`, true);
  }
}

function setAttachmentDropActive(active) {
  if (!els.attachmentDropzone) return;
  els.attachmentDropzone.classList.toggle('is-dragover', Boolean(active));
}

function handleAttachmentDrop(event) {
  event.preventDefault();
  setAttachmentDropActive(false);
  const files = Array.from(
    (event.dataTransfer && event.dataTransfer.files) || []
  );
  if (!files.length || !els.attachmentFile) return;
  try {
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(file);
    els.attachmentFile.files = transfer.files;
  } catch {
    setAttachmentStatus(
      '当前浏览器不支持拖拽上传，请使用文件选择', true
    );
    return;
  }
  uploadAttachment();
}

function handleAttachmentPaste(event) {
  if (!metricsOpen || attachmentUploading) return;
  const target = event.target;
  if (
    target && target.closest
    && target.closest('input, textarea, select, [contenteditable="true"]')
  ) {
    return;
  }
  const section = document.getElementById('attachments-section');
  if (!section || !section.open) return;
  const items = event.clipboardData && event.clipboardData.items;
  if (!items || !els.attachmentFile) return;
  const extensions = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
  };
  const stamp = Date.now();
  const files = [];
  for (const item of items) {
    if (item.kind !== 'file') continue;
    const extension = extensions[item.type];
    if (!extension) continue;
    const file = item.getAsFile();
    if (!file) continue;
    files.push(new File(
      [file],
      `粘贴图片-${stamp}-${files.length + 1}.${extension}`,
      { type: item.type }
    ));
  }
  if (!files.length) return;
  event.preventDefault();
  try {
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(file);
    els.attachmentFile.files = transfer.files;
  } catch {
    setAttachmentStatus(
      '当前浏览器不支持粘贴上传，请使用文件选择', true
    );
    return;
  }
  uploadAttachment();
}

async function uploadAttachment() {
  if (attachmentUploading) return;
  const files = Array.from(
    (els.attachmentFile && els.attachmentFile.files) || []
  );
  if (!files.length) {
    setAttachmentStatus('请选择要上传的文件', true);
    return;
  }
  if (files.length > ATTACHMENT_MAX_BATCH) {
    setAttachmentStatus(
      `一次最多上传 ${ATTACHMENT_MAX_BATCH} 个文件`, true
    );
    return;
  }
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    const extension = String(file.name || '')
      .split('.').pop().toLowerCase();
    if (!ATTACHMENT_EXTENSIONS.includes(extension)) {
      setAttachmentStatus(
        `第 ${index + 1} 个文件仅支持 JPG/PNG/WebP/PDF 附件`, true
      );
      return;
    }
    if (file.size > ATTACHMENT_MAX_BYTES) {
      setAttachmentStatus(
        `第 ${index + 1} 个文件超过 10 MB，请压缩后再上传`, true
      );
      return;
    }
  }
  const kind = els.attachmentKind.value || 'other';
  if (
    kind !== 'other'
    && els.attachmentRef
    && els.attachmentRefWrap
    && !els.attachmentRefWrap.hidden
    && els.attachmentRef.disabled
  ) {
    setAttachmentStatus('关联记录正在加载，请稍后再上传', true);
    return;
  }
  attachmentUploading = true;
  els.attachmentUpload.disabled = true;
  els.attachmentFile.disabled = true;
  els.attachmentNote.disabled = true;
  if (els.metricProfile) els.metricProfile.disabled = true;
  if (els.attachmentKind) els.attachmentKind.disabled = true;
  if (els.attachmentRef) els.attachmentRef.disabled = true;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const refId = els.attachmentRef && els.attachmentRefWrap
    && !els.attachmentRefWrap.hidden
    ? (els.attachmentRef.value || '0')
    : '0';
  const note = els.attachmentNote.value.trim();
  let uploaded = 0;
  const failures = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setAttachmentStatus(
        `正在上传 ${index + 1}/${files.length}…`
      );
      const form = new FormData();
      form.append('profile', profile);
      form.append('kind', kind);
      form.append('ref_id', refId);
      form.append('note', note);
      form.append('file', file);
      try {
        const res = await fetch('/api/attachments', {
          method: 'POST',
          body: form,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          let detail = typeof data.detail === 'string'
            ? data.detail
            : `服务返回 ${res.status}`;
          const retryAfter = Number(data.retry_after);
          if (res.status === 429 && retryAfter > 0) {
            detail += `（约 ${Math.ceil(retryAfter)} 秒后重试）`;
          }
          throw new Error(detail);
        }
        uploaded += 1;
      } catch (err) {
        failures.push(
          `${file.name || `第 ${index + 1} 个文件`}：`
          + `${err.message || err}`
        );
      }
    }
    if (uploaded) {
      els.attachmentFile.value = '';
      els.attachmentNote.value = '';
      await loadAttachments();
    loadAttachmentRefCounts();
      refreshAttachmentIntegrityIfVisible();
    }
    if (failures.length) {
      const names = failures.slice(0, 3).join('；')
        + (failures.length > 3
          ? `；另有 ${failures.length - 3} 个`
          : '');
      const currentProfile = els.metricProfile
        ? els.metricProfile.value : '';
      const targetText = currentProfile !== profile
        ? `；已上传至档案「${profile || '未指定'}」`
        : '';
      setAttachmentStatus(
        `已上传 ${uploaded} 个，${failures.length} 个失败：${names}。`
        + (uploaded ? '请重新选择失败文件重试' : '请重试')
        + targetText,
        true
      );
    } else {
      setAttachmentStatus(`已上传 ${uploaded} 个附件`);
    }
  } catch (err) {
    setAttachmentStatus(`上传失败：${err.message || err}`, true);
  } finally {
    attachmentUploading = false;
    els.attachmentUpload.disabled = false;
    els.attachmentFile.disabled = false;
    els.attachmentNote.disabled = false;
    if (els.metricProfile) els.metricProfile.disabled = false;
    if (els.attachmentKind) els.attachmentKind.disabled = false;
    if (els.attachmentRef) els.attachmentRef.disabled = false;
  }
}

async function deleteAttachment(item) {
  if (!window.confirm(`删除附件「${item.original_name || '附件'}」？`)) {
    return;
  }
  try {
    const res = await fetch(`/api/attachments/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    setAttachmentStatus('已删除附件');
    await loadAttachments();
    loadAttachmentRefCounts();
    refreshAttachmentIntegrityIfVisible();
  } catch (err) {
    setAttachmentStatus(`删除附件失败：${err.message || err}`, true);
  }
}

function bpMonitoringDefaultStart(days) {
  const date = new Date();
  date.setDate(date.getDate() - (Number(days || 7) - 1));
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function setBpMonitoringStatus(text, isError = false) {
  if (!els.bpMonitoringStatus) return;
  els.bpMonitoringStatus.textContent = text || '';
  els.bpMonitoringStatus.hidden = !text;
  els.bpMonitoringStatus.classList.toggle('error', Boolean(isError));
}

function formatBpMonitoringAvg(stats) {
  if (!stats || stats.avg_systolic == null) return '暂无';
  return `${stats.avg_systolic}/${stats.avg_diastolic} mmHg（${stats.count} 次）`;
}

function bpMonitoringText(data) {
  const range = data.is_future_plan
    ? `${data.start} 至 ${data.planned_end}（统计至 ${data.end}）`
    : `${data.start} 至 ${data.end}`;
  const lines = [
    `家庭血压监测（${range}`
      + `${data.profile ? ` · ${data.profile}` : ' · 未指定档案'}）`,
    `完成：${data.completed_slots}/${data.expected_slots} 时段`
      + `（${data.completion_rate}%），完整 ${data.days_completed} 天`,
    `平均：${formatBpMonitoringAvg(data.average)}`,
    `晨起：${formatBpMonitoringAvg(data.morning)}`,
    `睡前：${formatBpMonitoringAvg(data.evening)}`,
  ];
  if (data.morning_evening_systolic_diff != null) {
    const diff = Number(data.morning_evening_systolic_diff);
    lines.push(
      `晨晚收缩压差：${diff > 0 ? '+' : ''}${diff} mmHg`
    );
  }
  if (data.is_future_plan) {
    lines.push('计划仍在进行，未来日期的时段暂不计入完成度');
  }
  if (data.unlabeled) {
    lines.push(
      `未标注时段记录：${data.unlabeled} 条（未计入完成度）`
    );
  }
  const missing = data.missing || [];
  if (missing.length) {
    lines.push(`未测时段：${missing
      .slice(0, 20)
      .map((item) => `${item.date} ${item.labels.join('/')}`)
      .join('、')}${missing.length > 20 ? ` 等 ${missing.length} 个` : ''}`);
  }
  lines.push('仅统计家庭测量记录完成度，不判断血压是否达标。');
  return lines.join('\n');
}

function renderBpMonitoring(data) {
  if (!els.bpMonitoringBody) return;
  els.bpMonitoringBody.replaceChildren();
  els.bpMonitoringSummary.textContent =
    `· ${data.completion_rate}%（${data.completed_slots}/${data.expected_slots} 时段）`;
  const hasRecords = Number(data.readings) > 0;
  els.bpMonitoringEmpty.hidden = hasRecords;
  if (!hasRecords) {
    els.bpMonitoringEmpty.textContent = '所选区间还没有血压记录。';
  }
  const lines = [];
  if (hasRecords) {
    lines.push(`平均 ${formatBpMonitoringAvg(data.average)}`);
    if (data.morning && data.morning.avg_systolic != null) {
      lines.push(`晨起 ${formatBpMonitoringAvg(data.morning)}`);
    }
    if (data.evening && data.evening.avg_systolic != null) {
      lines.push(`睡前 ${formatBpMonitoringAvg(data.evening)}`);
    }
    if (data.morning_evening_systolic_diff != null) {
      const diff = Number(data.morning_evening_systolic_diff);
      lines.push(
        `晨晚收缩压差 ${diff > 0 ? '+' : ''}${diff} mmHg`
      );
    }
    if (data.unlabeled) {
      lines.push(
        `另有 ${data.unlabeled} 条未标注晨起/睡前的记录未计入完成度`
      );
    }
    if (data.is_future_plan) {
      lines.push('计划仍在进行，未来日期的时段暂不计入完成度');
    }
  }
  for (const text of lines) {
    const line = document.createElement('p');
    line.className = 'metric-reference';
    line.textContent = text;
    els.bpMonitoringBody.appendChild(line);
  }
  const missing = data.missing || [];
  if (hasRecords) {
    const note = document.createElement('p');
    note.className = 'metric-note';
    if (missing.length) {
      note.textContent = `未测时段：${missing
        .slice(0, 20)
        .map((item) => `${item.date.slice(5)} ${item.labels.join('/')}`)
        .join('、')}${
        missing.length > 20 ? ` 等 ${missing.length} 个时段` : ''
      }`;
    } else {
      note.textContent = '所有应测时段均已记录。';
    }
    els.bpMonitoringBody.appendChild(note);
  }
}

async function loadBpMonitoring() {
  if (!els.bpMonitoringBody || !metricsOpen) return;
  const days = Number(els.bpMonitoringDays.value) || 7;
  if (bpMonitoringStartAuto || !els.bpMonitoringStart.value) {
    els.bpMonitoringStart.value = bpMonitoringDefaultStart(days);
  }
  const requestSeq = ++bpMonitoringRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const start = els.bpMonitoringStart.value;
  const params = new URLSearchParams({ days: String(days) });
  params.set('profile', profile);
  if (start) params.set('start', start);
  try {
    const res = await fetch(
      `/api/health/bp-monitoring?${params.toString()}`
    );
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== bpMonitoringRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    renderBpMonitoring(data);
    bpMonitoringCache = { data, profile, start, days };
  } catch (err) {
    if (requestSeq !== bpMonitoringRequestSeq) return;
    bpMonitoringCache = null;
    els.bpMonitoringBody.replaceChildren();
    els.bpMonitoringSummary.textContent = '· 加载失败';
    els.bpMonitoringEmpty.textContent =
      `加载失败：${err.message || err}`;
    els.bpMonitoringEmpty.hidden = false;
  }
}

async function copyBpMonitoring() {
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const start = els.bpMonitoringStart.value;
  const days = Number(els.bpMonitoringDays.value) || 7;
  const matches = bpMonitoringCache
    && bpMonitoringCache.profile === profile
    && bpMonitoringCache.start === start
    && bpMonitoringCache.days === days;
  if (!matches) await loadBpMonitoring();
  const cache = bpMonitoringCache;
  if (
    !cache
    || cache.profile !== profile
    || cache.start !== start
    || cache.days !== days
  ) {
    setBpMonitoringStatus('摘要尚未加载', true);
    return;
  }
  const ok = await copyText(bpMonitoringText(cache.data));
  setBpMonitoringStatus(
    ok ? '已复制摘要' : '复制失败，请手动选择文本',
    !ok
  );
}

let dataQualityRequestSeq = 0;

async function loadDataQuality() {
  if (!els.qualityList) return;
  // 面板在健康记录抽屉内；抽屉未打开时等打开时再刷新，避免无关刷新触发全量扫描
  if (!metricsOpen) return;
  const requestSeq = ++dataQualityRequestSeq;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  try {
    const res = await fetch(
      `/api/health/data-quality?profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== dataQualityRequestSeq) return;
    renderDataQuality(data);
  } catch {
    if (requestSeq !== dataQualityRequestSeq) return;
    els.qualityList.replaceChildren();
    els.qualitySummary.textContent = '· 加载失败';
    els.qualityEmpty.textContent = '数据质量检查失败，请稍后重试。';
    els.qualityEmpty.hidden = false;
  }
}

function renderDataQuality(data) {
  if (!els.qualityList) return;
  const issues = data.issues || [];
  els.qualityList.replaceChildren();
  els.qualitySummary.textContent = issues.length
    ? `· ${issues.length} 项`
    : '· 良好';
  els.qualityEmpty.hidden = issues.length > 0;
  els.qualityEmpty.textContent = '暂无数据质量问题。';
  for (const item of issues) {
    const li = document.createElement('li');
    li.className = 'metric-item';
    const main = document.createElement('div');
    main.className = 'metric-main';
    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = item.title || '';
    const badge = document.createElement('span');
    badge.className = `insight-level-badge is-${item.level || 'info'}`;
    badge.textContent = INSIGHT_LEVEL_LABELS[item.level] || '提示';
    title.append(' ', badge);
    main.appendChild(title);
    if (item.detail) {
      const detail = document.createElement('span');
      detail.className = 'metric-meta';
      detail.textContent = item.detail;
      main.appendChild(detail);
    }
    if (item.hint) {
      const hint = document.createElement('span');
      hint.className = 'metric-note';
      hint.textContent = item.hint;
      main.appendChild(hint);
    }
    li.appendChild(main);
    if (item.section) {
      const actions = document.createElement('div');
      actions.className = 'metric-item-actions';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'metric-edit';
      open.textContent = '查看';
      open.setAttribute('aria-label', `查看相关记录：${item.title}`);
      open.addEventListener('click', () => openInsightSection(item));
      actions.appendChild(open);
      li.appendChild(actions);
    }
    els.qualityList.appendChild(li);
  }
}

function renderWeeklySummary(data) {
  if (!els.weeklySummaryBody) return;
  els.weeklySummaryBody.replaceChildren();
  const range = document.createElement('p');
  range.className = 'metric-reference';
  range.textContent =
    `${formatReminderDue(data.start)} – ${formatReminderDue(data.end)}`
    + ` · 档案：${data.profile || '未指定'}`;

  const list = document.createElement('ul');
  list.className = 'weekly-summary-list';
  for (const [label, text] of weeklySummaryLines(data)) {
    const li = document.createElement('li');
    const strong = document.createElement('strong');
    strong.textContent = label;
    li.append(strong, document.createTextNode(`：${text}`));
    list.appendChild(li);
  }

  els.weeklySummaryBody.append(range, list);
}

async function loadWeeklySummary() {
  if (!els.weeklySummaryBody) return;
  const requestSeq = ++weeklySummaryRequestSeq;
  const profile = els.metricProfile.value;
  const days = Number(els.weeklySummaryRange.value) || 7;
  try {
    const res = await fetch(
      `/api/reports/weekly?profile=${encodeURIComponent(profile)}`
      + `&days=${days}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== weeklySummaryRequestSeq) return;
    weeklySummaryCache = { data, profile, days };
    renderWeeklySummary(data);
  } catch {
    if (requestSeq !== weeklySummaryRequestSeq) return;
    weeklySummaryCache = null;
    els.weeklySummaryBody.replaceChildren();
    const note = document.createElement('p');
    note.className = 'metric-reference';
    note.textContent = '小结加载失败，请稍后重试。';
    els.weeklySummaryBody.appendChild(note);
  }
}

function weeklySummaryText(data) {
  const header =
    `健康小结（${formatReminderDue(data.start)} – `
    + `${formatReminderDue(data.end)}`
    + `${data.profile ? ` · ${data.profile}` : ''}）`;
  return [
    header,
    ...weeklySummaryLines(data).map(([label, text]) => `${label}：${text}`),
  ].join('\n');
}

async function copyWeeklySummary() {
  const profile = els.metricProfile.value;
  const days = Number(els.weeklySummaryRange.value) || 7;
  let cache = weeklySummaryCache;
  if (!cache || cache.profile !== profile || cache.days !== days) {
    await loadWeeklySummary();
    cache = weeklySummaryCache;
  }
  if (!cache || cache.profile !== profile || cache.days !== days) {
    setMetricsStatus('小结尚未加载，请稍后再试。', true);
    return;
  }
  const ok = await copyText(weeklySummaryText(cache.data));
  if (!ok) {
    setMetricsStatus('复制失败，请手动选择文本。', true);
    return;
  }
  setMetricsStatus('已复制健康小结。');
  if (els.weeklySummaryCopy) {
    els.weeklySummaryCopy.textContent = '已复制';
    clearTimeout(weeklySummaryCopyTimer);
    weeklySummaryCopyTimer = setTimeout(() => {
      els.weeklySummaryCopy.textContent = '复制小结';
    }, 1500);
  }
}

async function exportWeeklySummary() {
  const profile = els.metricProfile.value;
  const days = Number(els.weeklySummaryRange.value) || 7;
  let cache = weeklySummaryCache;
  if (!cache || cache.profile !== profile || cache.days !== days) {
    await loadWeeklySummary();
    cache = weeklySummaryCache;
  }
  if (!cache || cache.profile !== profile || cache.days !== days) {
    setMetricsStatus('小结尚未加载，请稍后再试。', true);
    return;
  }
  const data = cache.data;
  const lines = [
    `# 健康小结（${data.start} – ${data.end}）`,
    '',
    `- 档案：${profile || '未指定'}`,
    `- 范围：近 ${days} 天`,
    '',
    ...weeklySummaryLines(data).map(
      ([label, text]) => `- ${label}：${text}`
    ),
    '',
    '本小结由 MediX 生成，仅供健康记录参考，不构成医疗建议。',
  ];
  const stamp = todayLocalDate().replace(/-/g, '');
  downloadFile(
    new Blob([lines.join('\n') + '\n'], {
      type: 'text/markdown;charset=utf-8',
    }),
    `medix-weekly-${stamp}.md`,
    'text/markdown;charset=utf-8'
  );
  setMetricsStatus('已导出健康小结 Markdown。');
}

let visitPrepRequestSeq = 0;
let visitPrepCache = null;
let visitPrepLoading = false;

function visitPrepWindow() {
  const value = els.visitPrepDays ? els.visitPrepDays.value : '30';
  const sinceLast = value === 'last';
  const days = sinceLast ? 30 : (Number(value) || 30);
  return { key: value, sinceLast, days };
}

function visitPrepWindowQuery(window) {
  return `&days=${window.days}`
    + (window.sinceLast ? '&since_last_visit=true' : '');
}

function setVisitPrepStatus(text, isError = false) {
  if (!els.visitPrepStatus) return;
  els.visitPrepStatus.textContent = text || '';
  els.visitPrepStatus.hidden = !text;
  els.visitPrepStatus.classList.toggle('error', Boolean(isError));
}

function resetVisitPrep() {
  visitPrepRequestSeq += 1;
  visitPrepCache = null;
  visitPrepLoading = false;
  if (els.visitPrepBody) {
    els.visitPrepBody.replaceChildren();
    els.visitPrepBody.hidden = true;
  }
  if (els.visitPrepEmpty) {
    els.visitPrepEmpty.hidden = false;
    els.visitPrepEmpty.textContent = '选择患者档案后生成准备单。';
  }
  if (els.visitPrepSummary) els.visitPrepSummary.textContent = '';
  if (els.visitPrepCopy) els.visitPrepCopy.disabled = true;
  if (els.visitPrepExport) els.visitPrepExport.disabled = true;
  if (els.visitPrepGenerate) els.visitPrepGenerate.disabled = false;
  setVisitPrepStatus('');
}

function setMetricProfileValue(value) {
  if (!els.metricProfile || els.metricProfile.value === value) {
    return false;
  }
  els.metricProfile.value = value;
  // 程序化切换档案不会触发 change，必须在这里清空准备单
  resetVisitPrep();
  return true;
}

function renderVisitPrep(data) {
  if (!els.visitPrepBody) return;
  const body = els.visitPrepBody;
  body.replaceChildren();
  const addSection = (title, entries) => {
    if (!entries.length) return;
    const heading = document.createElement('p');
    heading.className = 'visit-prep-heading';
    heading.textContent = title;
    const list = document.createElement('ul');
    list.className = 'weekly-summary-list';
    for (const entry of entries) {
      const li = document.createElement('li');
      li.textContent = entry;
      list.appendChild(li);
    }
    body.append(heading, list);
  };

  if (data.last_visit) {
    const visit = data.last_visit;
    const entries = [];
    const where = [visit.visit_date, visit.department, visit.doctor]
      .filter(Boolean).join(' · ');
    if (where) entries.push(where);
    if (visit.diagnosis) entries.push(`诊断：${visit.diagnosis}`);
    if (visit.advice) entries.push(`医嘱：${visit.advice}`);
    if (visit.next_followup_date) {
      entries.push(`下次复诊：${visit.next_followup_date}`);
    }
    addSection('上次就诊', entries);
  }

  const abnormal = [];
  for (const item of data.abnormal_metrics || []) {
    const context = item.context ? `（${item.context}）` : '';
    let flag = '超标';
    if (item.critical) {
      flag = '危急值';
    } else if (item.target_direction === 'low') {
      flag = '低于目标';
    }
    abnormal.push(
      `${item.label} ${item.value} ${item.unit} · `
      + `${String(item.measured_at || '').slice(0, 10)}${context} · `
      + flag
    );
  }
  for (const item of data.abnormal_labs || []) {
    const ref = item.ref_text ? `（参考 ${item.ref_text}）` : '';
    const flag = item.critical
      ? '危急值' : (item.abnormal_high ? '偏高' : '偏低');
    abnormal.push(
      `化验 ${item.item} ${item.value} ${item.unit}${ref} · `
      + `${String(item.tested_at || '').slice(0, 10)} · ${flag}`
    );
  }
  addSection('需要告知医生的异常', abnormal);

  addSection(
    `近 ${data.days} 天症状`,
    (data.symptoms || []).map((item) => (
      `${item.symptom}：${item.count} 次，最高 ${item.max_severity}/10，`
      + `最近 ${String(item.last_at || '').slice(0, 10)}`
    ))
  );

  const medicationLines = (data.medications || []).map((item) => {
    const detail = [item.dose, item.frequency].filter(Boolean).join(' ');
    return `${item.name}${detail ? `（${detail}）` : ''}`;
  });
  const medicationSummary = data.medication_summary || {};
  if (data.medications && data.medications.length) {
    medicationLines.push(
      `近 ${data.days} 天已服 ${medicationSummary.taken_count || 0} 次，`
      + `未服 ${medicationSummary.skipped_count || 0} 次`
    );
    if ((medicationSummary.skip_reasons || []).length) {
      medicationLines.push(
        '未服原因：'
        + medicationSummary.skip_reasons
          .map((item) => `${item.reason} ${item.count} 次`)
          .join('、')
      );
    }
  }
  addSection(
    `目前用药（${medicationSummary.active_count || 0} 种）`,
    medicationLines
  );

  const allergyLines = (data.allergies || []).map((item) => (
    `过敏：${item.substance}（${item.kind}，${item.severity}`
    + `${item.reaction ? `，反应：${item.reaction}` : ''}）`
  ));
  for (const item of data.conditions || []) {
    allergyLines.push(`慢病：${item.name}（${item.status}）`);
  }
  addSection('过敏与慢病', allergyLines);

  const todoLines = [];
  const reminders = data.reminders || {};
  for (const item of reminders.items || []) {
    todoLines.push(
      `提醒：${item.title}（${String(item.due_at || '').slice(0, 10)}，`
      + `${item.overdue ? '已逾期' : '待完成'}）`
    );
  }
  for (const item of data.care_items || []) {
    todoLines.push(
      `复查：${item.title}（${item.next_due_at}，`
      + `${item.overdue ? '已逾期' : '临近'}）`
    );
  }
  addSection('待办与复查', todoLines);

  const attachmentLines = (data.attachments || []).map((item) => (
    `${item.name}（${item.kind}${item.linked ? '，已关联' : ''}）`
  ));
  const attachmentCount = Number(data.attachment_count) || 0;
  if (attachmentCount > attachmentLines.length) {
    attachmentLines.push(
      `另有 ${attachmentCount - attachmentLines.length} 份未列出`
    );
  }
  addSection(`携带资料（${attachmentCount} 份）`, attachmentLines);

  const questions = data.questions || [];
  if (questions.length) {
    const heading = document.createElement('p');
    heading.className = 'visit-prep-heading';
    heading.textContent = '建议询问医生';
    const list = document.createElement('ol');
    list.className = 'visit-prep-questions';
    for (const question of questions) {
      const li = document.createElement('li');
      li.textContent = question;
      list.appendChild(li);
    }
    body.append(heading, list);
  }

  body.hidden = false;
  if (els.visitPrepEmpty) els.visitPrepEmpty.hidden = true;
  if (els.visitPrepSummary) {
    els.visitPrepSummary.textContent = `· ${data.start} 至 ${data.end}`;
  }
}

async function loadVisitPrep() {
  if (visitPrepLoading) return;
  const profile = els.metricProfile ? els.metricProfile.value : '';
  if (!profile) {
    setVisitPrepStatus('请先选择患者档案', true);
    return;
  }
  const window = visitPrepWindow();
  const requestSeq = ++visitPrepRequestSeq;
  visitPrepLoading = true;
  els.visitPrepGenerate.disabled = true;
  setVisitPrepStatus('正在生成…');
  try {
    const res = await fetch(
      `/api/health/visit-prep?profile=${encodeURIComponent(profile)}`
      + visitPrepWindowQuery(window)
    );
    const data = await res.json().catch(() => ({}));
    if (requestSeq !== visitPrepRequestSeq) return;
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    visitPrepCache = { data, profile, window: window.key };
    renderVisitPrep(data);
    setVisitPrepStatus(
      `已生成：${data.start} 至 ${data.end}`
      + (data.since_last_visit ? ' · 上次就诊以来' : '')
    );
    els.visitPrepCopy.disabled = false;
    els.visitPrepExport.disabled = false;
  } catch (err) {
    if (requestSeq !== visitPrepRequestSeq) return;
    visitPrepCache = null;
    els.visitPrepBody.replaceChildren();
    els.visitPrepBody.hidden = true;
    els.visitPrepCopy.disabled = true;
    els.visitPrepExport.disabled = true;
    if (els.visitPrepSummary) els.visitPrepSummary.textContent = '';
    if (els.visitPrepEmpty) {
      els.visitPrepEmpty.hidden = false;
      els.visitPrepEmpty.textContent = '准备单生成失败，请稍后重试。';
    }
    setVisitPrepStatus(`生成失败：${err.message || err}`, true);
  } finally {
    if (requestSeq === visitPrepRequestSeq) {
      visitPrepLoading = false;
      els.visitPrepGenerate.disabled = false;
    }
  }
}

async function copyVisitPrep() {
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const window = visitPrepWindow();
  const cache = visitPrepCache;
  if (!cache || cache.profile !== profile || cache.window !== window.key) {
    setVisitPrepStatus('准备单尚未生成，请先点击生成。', true);
    return;
  }
  const ok = await copyText(cache.data.text || '');
  if (!ok) {
    setVisitPrepStatus('复制失败，请手动选择文本。', true);
    return;
  }
  setVisitPrepStatus('已复制复诊准备单。');
}

function exportVisitPrep() {
  const profile = els.metricProfile ? els.metricProfile.value : '';
  const window = visitPrepWindow();
  const cache = visitPrepCache;
  if (!cache || cache.profile !== profile || cache.window !== window.key) {
    setVisitPrepStatus('准备单尚未生成，请先点击生成。', true);
    return;
  }
  const stamp = todayLocalDate().replace(/-/g, '');
  downloadFile(
    new Blob([cache.data.text || ''], {
      type: 'text/markdown;charset=utf-8',
    }),
    `medix-visit-prep-${stamp}.md`,
    'text/markdown;charset=utf-8'
  );
  setVisitPrepStatus('已导出复诊准备单 Markdown。');
}

async function printVisitPrep() {
  const profile = els.metricProfile ? els.metricProfile.value : '';
  if (!profile) {
    setVisitPrepStatus('请先选择患者档案', true);
    return;
  }
  const window = visitPrepWindow();
  const button = els.visitPrepPrint;
  if (button) button.disabled = true;
  setVisitPrepStatus('正在准备打印版…');
  try {
    const res = await fetch(
      `/api/health/visit-prep/print?profile=${encodeURIComponent(profile)}`
      + visitPrepWindowQuery(window)
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const opened = window.open(url, '_blank');
    if (!opened) {
      URL.revokeObjectURL(url);
      setVisitPrepStatus(
        '浏览器拦截了新窗口，请允许弹窗后重试', true
      );
      return;
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    setVisitPrepStatus(
      '已打开打印版，可使用浏览器菜单打印或另存为 PDF。'
    );
  } catch (err) {
    setVisitPrepStatus(`打开打印版失败：${err.message || err}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

const TIMELINE_TYPE_LABELS = {
  metric: '指标',
  symptom: '症状',
  medication: '用药',
  vaccination: '疫苗',
  appointment: '就诊',
  lab: '化验',
  care: '复查',
  visit: '就诊记录',
  consultation: '会诊',
  attachment: '资料',
};

function renderTimeline(data) {
  if (!els.timelineList) return;
  const allEvents = data.events || [];
  const type = els.timelineType ? els.timelineType.value : '';
  const query = els.timelineSearch
    ? String(els.timelineSearch.value || '').trim().toLowerCase()
    : '';
  const onlyAlert = Boolean(
    els.timelineOnlyAlert && els.timelineOnlyAlert.checked
  );
  const riskOnly = Boolean(
    els.timelineRiskOnly && els.timelineRiskOnly.checked
  );
  const events = allEvents.filter((event) => {
    if (onlyAlert && !event.alert) return false;
    if (riskOnly && !(
      event.type === 'consultation'
      && (event.risk === 'high' || event.risk === 'emergency')
    )) return false;
    if (type && event.type !== type) return false;
    if (!query) return true;
    return [event.title, event.value, event.note, event.profile]
      .map((value) => String(value || ''))
      .join(' ')
      .toLowerCase()
      .includes(query);
  });
  const days = Number(data.days) || 30;
  const dateKey = String(data.date || '').trim();
  const rangeText = dateKey ? `${dateKey} ` : rangeDaysLabel(days);
  els.timelineList.replaceChildren();
  els.timelineEmpty.hidden = events.length > 0;
  if (query) {
    els.timelineEmpty.textContent = `没有匹配「${query}」的记录。`;
  } else if (onlyAlert) {
    els.timelineEmpty.textContent = `${rangeText}没有超标的记录。`;
  } else if (riskOnly) {
    els.timelineEmpty.textContent = `${rangeText}没有高危会诊记录。`;
  } else if (type) {
    els.timelineEmpty.textContent = `${rangeText}没有该类型的记录。`;
  } else {
    els.timelineEmpty.textContent = `${rangeText}没有健康记录。`;
  }
  if (els.timelineSummary) {
    // 筛选已由服务端执行，total 就是筛选后的总数；
    // 不能用当前已加载页数冒充总数
    const count = Number(data.total) || events.length;
    els.timelineSummary.hidden = !events.length;
    els.timelineSummary.textContent = events.length
      ? `${rangeText}共 ${count} 条记录`
      : '';
  }
  if (els.timelineDateChip) {
    els.timelineDateChip.hidden = !dateKey;
    els.timelineDateChip.textContent = dateKey ? `${dateKey} ×` : '';
    els.timelineDateChip.title = dateKey ? '清除日期筛选' : '';
  }
  for (const event of events) {
    const li = document.createElement('li');
    li.className = 'metric-item timeline-item';

    const main = document.createElement('div');
    main.className = 'metric-main';

    const title = document.createElement('strong');
    title.className = 'metric-value';
    title.textContent = event.title || '';
    const badge = document.createElement('span');
    badge.className =
      `timeline-badge timeline-badge-${event.type || 'metric'}`;
    badge.textContent = TIMELINE_TYPE_LABELS[event.type] || '记录';
    title.append(' ', badge);
    if (event.alert) {
      const alertBadge = document.createElement('span');
      alertBadge.className = 'timeline-alert';
      alertBadge.textContent = event.critical
        ? '危急'
        : event.type === 'vaccination'
          ? '到期'
          : event.type === 'appointment'
            ? '就诊'
            : event.type === 'lab' ? '异常' : '超标';
      title.appendChild(alertBadge);
    }

    const meta = document.createElement('span');
    meta.className = 'metric-meta';
    meta.textContent = [
      (
        event.type === 'vaccination' || event.type === 'appointment'
      )
        ? String(event.at || '').slice(0, 10)
        : formatTime(event.at),
      event.value,
      event.profile || '未指定档案',
    ].filter(Boolean).join(' · ');

    main.append(title, meta);
    if (event.note) {
      const note = document.createElement('span');
      note.className = 'metric-note';
      note.textContent = event.note;
      main.appendChild(note);
    }
    li.appendChild(main);
    if (event.type === 'consultation' && event.id) {
      li.classList.add('timeline-item-link');
      li.tabIndex = 0;
      li.setAttribute('role', 'button');
      li.title = '点击打开会诊记录';
      const openConsultation = () => {
        closeMetrics();
        openSession(event.id);
      };
      li.addEventListener('click', openConsultation);
      li.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openConsultation();
        }
      });
    }
    if (event.type === 'attachment' && event.id) {
      li.classList.add('timeline-item-link');
      li.tabIndex = 0;
      li.setAttribute('role', 'button');
      li.title = '点击定位就医资料';
      const openAttachment = () => {
        locateHealthRecord({
          type: 'attachment',
          profile: event.profile || '',
          title: event.title || '',
        }, event.title || '');
      };
      li.addEventListener('click', openAttachment);
      li.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openAttachment();
        }
      });
    }
    els.timelineList.appendChild(li);
  }
}

function updateTimelineMore(data) {
  if (!els.timelineMore) return;
  const loaded = (data.events || []).length;
  const total = Number(data.total) || loaded;
  els.timelineMore.hidden = !(total > loaded && timelineLimit < 2000);
}

let compareRequestSeq = 0;

function formatCompareValue(row, unit) {
  const suffix = unit ? ` ${unit}` : '';
  if (row.avg_value2 !== null && row.avg_value2 !== undefined) {
    return `${row.avg_value1}/${row.avg_value2}${suffix}`;
  }
  return `${row.avg_value1}${suffix}`;
}

async function loadFamilyCompare() {
  if (!els.compareList) return;
  const requestSeq = ++compareRequestSeq;
  const metric = els.compareMetric ? els.compareMetric.value : 'weight';
  const days = Number(els.compareRange && els.compareRange.value) || 30;
  const params = new URLSearchParams({
    metric, days: String(days),
  });
  try {
    const res = await fetch(`/api/family/compare?${params.toString()}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== compareRequestSeq) return;
    renderFamilyCompare(data);
  } catch {
    if (requestSeq !== compareRequestSeq) return;
    els.compareList.replaceChildren();
    if (els.compareEmpty) {
      els.compareEmpty.hidden = false;
      els.compareEmpty.textContent = '对比数据加载失败，请稍后重试。';
    }
  }
}

function setCompareStatus(text, isError = false) {
  if (!els.compareStatus) return;
  els.compareStatus.textContent = text || '';
  els.compareStatus.hidden = !text;
  els.compareStatus.classList.toggle('error', Boolean(isError));
}

async function exportFamilyCompare() {
  const metric = els.compareMetric ? els.compareMetric.value : 'weight';
  const days = Number(els.compareRange && els.compareRange.value) || 30;
  const params = new URLSearchParams({
    metric, days: String(days),
  });
  setCompareStatus('正在导出家庭指标对比…');
  try {
    const res = await fetch(
      `/api/family/compare/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const blob = await res.blob();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      blob,
      `medix-family-compare-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setCompareStatus('已导出家庭指标对比 CSV。');
  } catch (err) {
    setCompareStatus(
      `导出家庭指标对比失败：${err.message || err}`, true
    );
  }
}

function renderFamilyCompare(data) {
  if (!els.compareList) return;
  const rows = data.profiles || [];
  els.compareList.replaceChildren();
  if (els.compareEmpty) {
    els.compareEmpty.hidden = rows.length > 0;
    els.compareEmpty.textContent = `近 ${data.days} 天还没有可对比的${data.label}记录。`;
  }
  const withData = rows.filter(
    (row) => Number(row.count) > 0
      && row.avg_value1 !== null && row.avg_value1 !== undefined
  );
  const maxValue = withData.reduce(
    (max, row) => Math.max(max, Number(row.avg_value1) || 0), 0
  ) || 1;
  for (const row of rows) {
    const li = document.createElement('li');
    li.className = 'compare-item';
    const head = document.createElement('div');
    head.className = 'compare-head';
    const name = document.createElement('strong');
    name.textContent = row.name;
    const meta = document.createElement('span');
    meta.className = 'compare-meta';
    if (Number(row.count) > 0 && row.avg_value1 !== null) {
      const hasRange = row.avg_value2 === null && Number(row.count) > 1
        && row.min_value1 !== null && row.max_value1 !== null
        && Number(row.min_value1) !== Number(row.max_value1);
      const rangeText = hasRange
        ? `（${row.min_value1}–${row.max_value1}${data.unit ? ` ${data.unit}` : ''}）`
        : '';
      const targetText = row.target
        && Number.isFinite(Number(row.within_target))
        ? ` · 达标 ${row.within_target}/${row.count}`
        : '';
      meta.textContent = `平均 ${formatCompareValue(row, data.unit)}`
        + `${rangeText} · ${row.count} 次${targetText}`;
      if (row.target) {
        const targetValue2 = row.target.value2 !== null
          && row.target.value2 !== undefined
          ? `/${row.target.value2}`
          : '';
        const bound = row.target_lower_bound ? '≥' : '≤';
        meta.title = `目标 ${bound} ${row.target.value1}${targetValue2}`
          + `${data.unit ? ` ${data.unit}` : ''}`;
      }
    } else {
      meta.textContent = '所选范围内无记录';
    }
    head.append(name, meta);
    const barWrap = document.createElement('div');
    barWrap.className = 'compare-bar';
    const bar = document.createElement('span');
    bar.className = 'compare-bar-fill';
    if (Number(row.count) > 0 && row.avg_value1 !== null) {
      const ratio = Math.max(
        0, Math.min(1, Number(row.avg_value1) / maxValue)
      );
      bar.style.width = `${Math.round(ratio * 100)}%`;
    } else {
      bar.classList.add('compare-bar-empty');
    }
    barWrap.appendChild(bar);
    li.append(head, barWrap);
    if (row.latest && row.latest.value1 !== null
        && row.latest.value1 !== undefined) {
      const latestValue = row.latest.value2 !== null
        && row.latest.value2 !== undefined
        ? `${formatStatNumber(row.latest.value1)}/`
          + formatStatNumber(row.latest.value2)
        : formatStatNumber(row.latest.value1);
      const latest = document.createElement('span');
      latest.className = 'compare-latest';
      latest.textContent =
        `最近 ${latestValue}${data.unit ? ` ${data.unit}` : ''}`
        + `（${formatTime(row.latest.measured_at)}）`;
      li.appendChild(latest);
    }
    els.compareList.appendChild(li);
  }
}

let calendarMonth = '';
let calendarRequestSeq = 0;

function currentMonthKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function shiftMonthKey(monthKey, delta) {
  const [year, month] = monthKey.split('-').map((part) => Number(part));
  const date = new Date(year, month - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function calendarMonthText(monthKey) {
  const [year, month] = monthKey.split('-').map((part) => Number(part));
  return `${year} 年 ${month} 月`;
}

async function loadCalendar() {
  if (!els.calendarGrid) return;
  const requestSeq = ++calendarRequestSeq;
  const month = calendarMonth || currentMonthKey();
  const profile = els.metricProfile.value;
  try {
    const params = new URLSearchParams({ profile, month });
    const res = await fetch(`/api/health/calendar?${params.toString()}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== calendarRequestSeq) return;
    calendarMonth = data.month || month;
    renderCalendar(data);
  } catch {
    if (requestSeq !== calendarRequestSeq) return;
    els.calendarGrid.replaceChildren();
    if (els.calendarSummary) {
      els.calendarSummary.textContent = '月历加载失败，请稍后重试。';
    }
  }
}

function renderCalendar(data) {
  if (!els.calendarGrid) return;
  const monthKey = data.month || currentMonthKey();
  const [year, month] = monthKey.split('-').map((part) => Number(part));
  if (els.calendarMonthLabel) {
    els.calendarMonthLabel.textContent = calendarMonthText(monthKey);
  }
  const daysMap = data.days || {};
  const firstWeekday = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  const totalDays = new Date(year, month, 0).getDate();
  const todayKey = todayLocalDate();
  const grid = els.calendarGrid;
  grid.replaceChildren();
  for (let i = 0; i < firstWeekday; i += 1) {
    const blank = document.createElement('span');
    blank.className = 'calendar-cell is-empty';
    blank.setAttribute('aria-hidden', 'true');
    grid.appendChild(blank);
  }
  const badgeDefs = [
    ['metrics', '指', '指标'],
    ['symptoms', '症', '症状'],
    ['doses', '药', '用药打卡'],
    ['consultations', '诊', '会诊'],
    ['reminders_due', '提', '待办提醒'],
    ['vaccinations', '苗', '疫苗'],
    ['appointments', '约', '预约'],
    ['labs', '化', '化验'],
    ['care_due', '查', '复查'],
    ['visits', '记', '就诊记录'],
    ['alerts', '警', '异常'],
  ];
  for (let day = 1; day <= totalDays; day += 1) {
    const dayKey = `${monthKey}-${String(day).padStart(2, '0')}`;
    const entry = daysMap[dayKey];
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'calendar-cell';
    cell.dataset.date = dayKey;
    if (entry) cell.classList.add('has-data');
    if (dayKey === todayKey) cell.classList.add('is-today');
    const number = document.createElement('span');
    number.className = 'calendar-day-number';
    number.textContent = String(day);
    cell.appendChild(number);
    const badgeTexts = [];
    if (entry) {
      const badges = document.createElement('span');
      badges.className = 'calendar-badges';
      for (const [key, short, label] of badgeDefs) {
        const count = Number(entry[key]) || 0;
        if (!count) continue;
        const badge = document.createElement('span');
        badge.className = 'calendar-badge'
          + (
            key === 'reminders_due' || key === 'alerts'
              ? ' calendar-badge-alert'
              : ''
          );
        badge.textContent = `${short}${count}`;
        badges.appendChild(badge);
        badgeTexts.push(`${label} ${count}`);
      }
      if (entry.reminders_done) {
        const done = document.createElement('span');
        done.className = 'calendar-badge';
        done.textContent = `完${entry.reminders_done}`;
        badges.appendChild(done);
        badgeTexts.push(`已完成提醒 ${entry.reminders_done}`);
      }
      cell.appendChild(badges);
    }
    cell.setAttribute(
      'aria-label',
      `${year} 年 ${month} 月 ${day} 日`
      + (badgeTexts.length ? `，${badgeTexts.join('，')}` : '，无记录')
    );
    cell.addEventListener('click', () => selectCalendarDay(dayKey));
    grid.appendChild(cell);
  }
  if (els.calendarSummary) {
    const totals = data.totals || {};
    const parts = [];
    if (Number(totals.metrics)) parts.push(`指标 ${totals.metrics} 条`);
    if (Number(totals.symptoms)) parts.push(`症状 ${totals.symptoms} 条`);
    if (Number(totals.doses)) parts.push(`用药打卡 ${totals.doses} 次`);
    if (Number(totals.consultations)) {
      parts.push(`会诊 ${totals.consultations} 次`);
    }
    if (Number(totals.reminders_due)) {
      parts.push(`待办提醒 ${totals.reminders_due} 条`);
    }
    if (Number(totals.reminders_done)) {
      parts.push(`已完成提醒 ${totals.reminders_done} 条`);
    }
    if (Number(totals.vaccinations)) {
      parts.push(`疫苗 ${totals.vaccinations} 次`);
    }
    if (Number(totals.appointments)) {
      parts.push(`预约 ${totals.appointments} 次`);
    }
    if (Number(totals.labs)) {
      parts.push(`化验 ${totals.labs} 条`);
    }
    if (Number(totals.care_due)) {
      parts.push(`复查 ${totals.care_due} 项`);
    }
    if (Number(totals.visits)) {
      parts.push(`就诊 ${totals.visits} 次`);
    }
    if (Number(totals.alerts)) {
      parts.push(`异常 ${totals.alerts} 条`);
    }
    els.calendarSummary.textContent = parts.length
      ? `${data.profile ? `档案：${data.profile} · ` : ''}`
        + `本月 ${parts.join(' · ')}`
      : '本月暂无健康记录。';
  }
}

function selectCalendarDay(dayKey) {
  timelineDate = dayKey;
  if (els.timelineSection) els.timelineSection.open = true;
  loadTimeline();
  if (els.timelineSection) {
    els.timelineSection.scrollIntoView({ block: 'nearest' });
  }
}

async function exportCalendarCsv() {
  const profile = els.metricProfile.value;
  const month = calendarMonth || currentMonthKey();
  const params = new URLSearchParams({ profile, month });
  try {
    const res = await fetch(
      `/api/health/calendar/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-calendar-${month.replace('-', '')}.csv`,
      'text/csv;charset=utf-8'
    );
    setMetricsStatus('已导出健康月历 CSV。');
  } catch (err) {
    setMetricsStatus(`导出月历失败：${err.message || err}`, true);
  }
}

async function loadTimeline() {
  if (!els.timelineList) return;
  const requestSeq = ++timelineRequestSeq;
  const profile = els.metricProfile.value;
  const days = Number(els.timelineRange.value) || 30;
  const params = new URLSearchParams({
    profile,
    days: String(days),
    limit: String(timelineLimit),
  });
  if (timelineDate) params.set('date', timelineDate);
  // 筛选条件交给服务端，避免只过滤已加载的前几页
  if (els.timelineType && els.timelineType.value) {
    params.set('type', els.timelineType.value);
  }
  if (els.timelineOnlyAlert && els.timelineOnlyAlert.checked) {
    params.set('only_alert', 'true');
  }
  if (els.timelineRiskOnly && els.timelineRiskOnly.checked) {
    params.set('only_risk', 'true');
  }
  const timelineQuery = els.timelineSearch
    ? els.timelineSearch.value.trim()
    : '';
  if (timelineQuery) params.set('q', timelineQuery);
  try {
    const res = await fetch(`/api/timeline?${params.toString()}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    if (requestSeq !== timelineRequestSeq) return;
    timelineDataCache = data;
    renderTimeline(data);
    updateTimelineMore(data);
  } catch {
    if (requestSeq !== timelineRequestSeq) return;
    timelineDataCache = null;
    els.timelineList.replaceChildren();
    if (els.timelineMore) els.timelineMore.hidden = true;
    els.timelineEmpty.hidden = false;
    els.timelineEmpty.textContent = '时间线加载失败，请稍后重试。';
    if (els.timelineSummary) els.timelineSummary.hidden = true;
  }
}

async function exportTimelineCsv() {
  const profile = els.metricProfile.value;
  const days = Number(els.timelineRange.value) || 30;
  const params = new URLSearchParams({
    profile, days: String(days),
  });
  if (timelineDate) params.set('date', timelineDate);
  if (els.timelineType && els.timelineType.value) {
    params.set('type', els.timelineType.value);
  }
  if (els.timelineOnlyAlert && els.timelineOnlyAlert.checked) {
    params.set('only_alert', 'true');
  }
  if (els.timelineRiskOnly && els.timelineRiskOnly.checked) {
    params.set('only_risk', 'true');
  }
  const query = els.timelineSearch
    ? els.timelineSearch.value.trim()
    : '';
  if (query) params.set('q', query);
  try {
    const res = await fetch(
      `/api/timeline/export.csv?${params.toString()}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const buffer = await res.arrayBuffer();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([buffer], { type: 'text/csv;charset=utf-8' }),
      `medix-timeline-${stamp}.csv`,
      'text/csv;charset=utf-8'
    );
    setMetricsStatus('已导出健康时间线 CSV。');
  } catch (err) {
    setMetricsStatus(`导出时间线失败：${err.message || err}`, true);
  }
}

async function exportHealthDossier() {
  const profile = els.metricProfile.value;
  const isFamily = !profile;
  // 导出跟随健康记录抽屉的时间范围；「全部」用接口支持的最大天数
  const rangeDays = metricRangeDays();
  const days = String(rangeDays > 0 ? rangeDays : 365);
  try {
    const url = isFamily
      ? `/api/health/dossier-family.md?${new URLSearchParams({
        days,
      }).toString()}`
      : `/api/health/dossier.md?${new URLSearchParams({
        profile, days,
      }).toString()}`;
    const res = await fetch(url);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const text = await res.text();
    const stamp = todayLocalDate().replace(/-/g, '');
    downloadFile(
      new Blob([text], { type: 'text/markdown;charset=utf-8' }),
      isFamily
        ? `medix-family-dossier-${stamp}.md`
        : `medix-dossier-${stamp}.md`,
      'text/markdown;charset=utf-8'
    );
    setMetricsStatus(
      isFamily
        ? '已导出家庭健康档案 Markdown。'
        : '已导出健康档案 Markdown。'
    );
  } catch (err) {
    setMetricsStatus(`导出健康档案失败：${err.message || err}`, true);
  }
}

async function toggleMedication(item) {
  try {
    const res = await fetch(`/api/medications/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile: item.profile || '',
        name: item.name,
        dose: item.dose || '',
        frequency: item.frequency || '',
        note: item.note || '',
        active: !item.active,
      }),
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    setMedicationStatus(item.active ? '已停用药物。' : '已启用药物。');
    await loadMedications();
    refreshOverview();
  } catch (err) {
    setMedicationStatus(`更新状态失败：${err.message || err}`, true);
  }
}

async function completeDueMedicationReminder(item) {
  try {
    const res = await fetch('/api/reminders');
    if (!res.ok) return null;
    const reminders = (await res.json()).reminders || [];
    const title = `用药：${item.name}`;
    const candidates = reminders.filter((reminder) => {
      if (reminder.done) return false;
      const byId = Number(reminder.medication_id) === item.id;
      const legacy = !Number(reminder.medication_id)
        && reminder.title === title
        && (reminder.profile || '') === (item.profile || '');
      if (!byId && !legacy) return false;
      const days = reminderDaysUntil(reminder.due_at);
      return days !== null && days >= -1 && days <= 0;
    });
    const dose = String(item.dose || '').trim();
    const due = (
      dose
        ? candidates.find(
          (reminder) => String(reminder.note || '').includes(dose)
        )
        : null
    ) || candidates[0];
    if (!due) return null;
    const completeRes = await fetch(`/api/reminders/${due.id}/complete`, {
      method: 'POST',
    });
    if (!completeRes.ok) return null;
    const data = await completeRes.json().catch(() => ({}));
    return data.next && data.next.due_at
      ? `已完成今日用药提醒，下次：${formatReminderDue(data.next.due_at)}。`
      : '已完成今日用药提醒。';
  } catch {
    return null;
  }
}

async function reopenLinkedMedicationReminder(
  name, profile, takenAt, medicationId = 0
) {
  if (String(takenAt || '').slice(0, 10) !== todayLocalDate()) return null;
  try {
    const res = await fetch('/api/reminders?include_done=true');
    if (!res.ok) return null;
    const reminders = (await res.json()).reminders || [];
    const title = `用药：${name}`;
    const done = reminders.find((reminder) => {
      if (!reminder.done) return false;
      const byId = Number(medicationId) > 0
        && Number(reminder.medication_id) === Number(medicationId);
      const legacy = !Number(reminder.medication_id)
        && reminder.title === title
        && (reminder.profile || '') === (profile || '');
      return (byId || legacy)
        && String(reminder.due_at || '').slice(0, 10) === todayLocalDate();
    });
    if (!done) return null;
    const reopen = await fetch(`/api/reminders/${done.id}/reopen`, {
      method: 'POST',
    });
    return reopen.ok ? '已恢复当天的用药提醒。' : null;
  } catch {
    return null;
  }
}

function attachMedicationSkipForm(item, li, button, draft) {
  const main = li.querySelector('.metric-main');
  if (!main) return null;
  const form = document.createElement('div');
  form.className = 'medication-skip-form';
  form.id = `medication-skip-form-${item.id}`;
  const label = document.createElement('span');
  label.className = 'metric-note';
  label.textContent = '未服原因：';
  const select = document.createElement('select');
  select.setAttribute('aria-label', `未服原因：${item.name}`);
  for (const option of MEDICATION_SKIP_REASON_OPTIONS) {
    const node = document.createElement('option');
    node.value = option.key;
    node.textContent = option.label;
    select.appendChild(node);
  }
  select.value = (draft && draft.reason) || 'forget';
  const note = document.createElement('input');
  note.type = 'text';
  note.maxLength = 300;
  note.placeholder = '备注（可选）';
  note.setAttribute('aria-label', `未服备注：${item.name}`);
  note.value = (draft && draft.note) || '';
  const updateDraft = () => {
    if (medicationSkipDraft && medicationSkipDraft.id === item.id) {
      medicationSkipDraft.reason = select.value;
      medicationSkipDraft.note = note.value;
    }
  };
  select.addEventListener('change', updateDraft);
  note.addEventListener('input', updateDraft);
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'btn-quiet';
  save.textContent = '保存未服';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn-quiet';
  cancel.textContent = '取消';
  cancel.addEventListener('click', () => {
    medicationSkipDraft = null;
    form.remove();
    if (button) {
      button.setAttribute('aria-expanded', 'false');
      button.focus();
    }
  });
  save.addEventListener(
    'click',
    () => saveMedicationSkip(
      item, select.value, note.value, save, form, button
    )
  );
  form.append(label, select, note, save, cancel);
  main.appendChild(form);
  if (button) button.setAttribute('aria-expanded', 'true');
  return form;
}

function openMedicationSkipForm(item, li, button) {
  const main = li.querySelector('.metric-main');
  const existing = main && main.querySelector('.medication-skip-form');
  if (existing) {
    medicationSkipDraft = null;
    existing.remove();
    if (button) {
      button.setAttribute('aria-expanded', 'false');
      button.focus();
    }
    return;
  }
  medicationSkipDraft = { id: item.id, reason: 'forget', note: '' };
  const form = attachMedicationSkipForm(
    item, li, button, medicationSkipDraft
  );
  const select = form && form.querySelector('select');
  if (select) select.focus();
}

async function saveMedicationSkip(
  item, reason, note, button, form, skipButton
) {
  if (medicationSkipBusy.has(item.id)) return;
  medicationSkipBusy.add(item.id);
  if (button) button.disabled = true;
  try {
    const res = await fetch(`/api/medications/${item.id}/skip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason, note }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const reasonLabel =
      (data.log && data.log.skip_reason_label) || '其他';
    const message = `已记录未服：${item.name}（${reasonLabel}）。`;
    medicationSkipDraft = null;
    medicationSkipFocusId = item.id;
    if (form) form.remove();
    if (skipButton) skipButton.setAttribute('aria-expanded', 'false');
    setMedicationStatus(message);
    setTodayMedsStatus(message);
    await loadMedications();
    loadMedicationLogs();
    await refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setMedicationStatus(`记录未服失败：${err.message || err}`, true);
  } finally {
    medicationSkipBusy.delete(item.id);
    if (button) button.disabled = false;
  }
}

async function logMedication(item) {
  if (medicationLogBusy.has(item.id)) return;
  medicationLogBusy.add(item.id);
  const restoreFocus = Boolean(
    els.todayMedsList
    && document.activeElement
    && els.todayMedsList.contains(document.activeElement)
  );
  try {
    const res = await fetch(`/api/medications/${item.id}/logs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const takenAt = data.log ? formatTime(data.log.taken_at) : '';
    const reminderNote = await completeDueMedicationReminder(item);
    const message =
      (takenAt
        ? `已记录用药：${item.name}（${takenAt}）。`
        : `已记录用药：${item.name}。`)
      + (reminderNote ? ` ${reminderNote}` : '');
    setMedicationStatus(message);
    setTodayMedsStatus(message);
    await loadMedications();
    loadMedicationLogs();
    await refreshOverview();
    refreshReminderBadge();
    if (restoreFocus) {
      const button = [...els.todayMedsList.querySelectorAll('button')]
        .find(
          (btn) => btn.getAttribute('aria-label')
            === `记录一次用药：${item.name}`
        );
      if (button) button.focus();
    }
  } catch (err) {
    const message = `记录用药失败：${err.message || err}`;
    setMedicationStatus(message, true);
    setTodayMedsStatus(message, true);
  } finally {
    medicationLogBusy.delete(item.id);
  }
}

async function logAllMedications() {
  const button = els.medicationLogAll;
  if (!button || button.disabled) return;
  const pending = (medicationItemsCache || []).filter(
    (item) => item.active && !Number(item.today_count)
  );
  if (!pending.length) {
    setMedicationStatus('今天的在用药物都已打卡。');
    return;
  }
  if (!window.confirm(
    `为今天尚未打卡的 ${pending.length} 种药物各记录一次？`
  )) {
    return;
  }

  button.disabled = true;
  button.textContent = '打卡中…';
  let success = 0;
  let failed = 0;
  try {
    for (const item of pending) {
      if (medicationLogBusy.has(item.id)) {
        failed += 1;
        continue;
      }
      medicationLogBusy.add(item.id);
      try {
        const res = await fetch(`/api/medications/${item.id}/logs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!res.ok) throw new Error(`服务返回 ${res.status}`);
        await completeDueMedicationReminder(item);
        success += 1;
      } catch {
        failed += 1;
      } finally {
        medicationLogBusy.delete(item.id);
      }
    }
  } finally {
    button.disabled = false;
    button.textContent = '全部打卡';
  }

  const message = `已打卡 ${success} 种药物`
    + (failed ? `，${failed} 种失败` : '')
    + '。';
  setMedicationStatus(message, failed > 0);
  setTodayMedsStatus(message, failed > 0);
  await loadMedications();
  loadMedicationLogs();
  await refreshOverview();
  refreshReminderBadge();
}

async function undoMedicationLog(item) {
  if (medicationLogBusy.has(item.id)) return;
  if (!window.confirm(`撤销「${item.name}」最近一次用药记录？`)) return;
  medicationLogBusy.add(item.id);
  try {
    const listRes = await fetch(
      `/api/medications/${item.id}/logs?limit=20`
    );
    if (!listRes.ok) throw new Error(`服务返回 ${listRes.status}`);
    const logs = ((await listRes.json()).logs || [])
      .filter((log) => !log.skipped);
    if (!logs.length) {
      setMedicationStatus('没有可撤销的用药记录。');
      return;
    }
    const res = await fetch(`/api/medication-logs/${logs[0].id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const reopenNote = await reopenLinkedMedicationReminder(
      item.name, item.profile || '', logs[0].taken_at, item.id
    );
    const message =
      `已撤销 ${formatTime(logs[0].taken_at)} 的用药记录。`
      + (reopenNote ? ` ${reopenNote}` : '');
    setMedicationStatus(message);
    setTodayMedsStatus(message);
    await loadMedications();
    loadMedicationLogs();
    await refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setMedicationStatus(`撤销用药记录失败：${err.message || err}`, true);
  } finally {
    medicationLogBusy.delete(item.id);
  }
}

function editMedication(item) {
  editingMedicationId = item.id;
  editingMedicationProfile = item.profile || '';
  const hasProfile = Array.from(els.metricProfile.options)
    .some((option) => option.value === item.profile);
  if (hasProfile) els.metricProfile.value = item.profile;
  els.medicationName.value = item.name || '';
  els.medicationDose.value = item.dose || '';
  els.medicationFrequency.value = item.frequency || '';
  els.medicationStock.value = item.stock === null || item.stock === undefined
    ? ''
    : String(item.stock);
  els.medicationStockUnit.value = item.stock_unit || '';
  els.medicationLowStock.value = String(
    item.low_stock_threshold === null
      || item.low_stock_threshold === undefined
      ? 0
      : item.low_stock_threshold
  );
  els.medicationExpiry.value = String(item.expiry_date || '').slice(0, 10);
  els.medicationStartedAt.value =
    String(item.started_at || '').slice(0, 10);
  els.medicationStoppedAt.value =
    String(item.stopped_at || '').slice(0, 10);
  els.medicationStopReason.value = item.stop_reason || '';
  els.medicationNote.value = item.note || '';
  els.medicationActive.checked = Boolean(item.active);
  els.medicationSave.textContent = '保存修改';
  els.medicationCancel.hidden = false;
  els.medicationName.focus();
}

async function saveMedication() {
  if (medicationSaving) return;
  const name = (els.medicationName.value || '').trim();
  if (!name) {
    setMedicationStatus('请填写药物名称。', true);
    els.medicationName.focus();
    return;
  }
  const editingId = editingMedicationId;
  const stockText = (els.medicationStock.value || '').trim();
  const stock = stockText === '' ? null : Number(stockText);
  if (stock !== null && (!Number.isFinite(stock) || stock < 0)) {
    setMedicationStatus('库存应为非负数字。', true);
    els.medicationStock.focus();
    return;
  }
  const thresholdText = (els.medicationLowStock.value || '').trim();
  const threshold = thresholdText === '' ? 0 : Number(thresholdText);
  if (!Number.isFinite(threshold) || threshold < 0) {
    setMedicationStatus('低库存阈值应为非负数字。', true);
    els.medicationLowStock.focus();
    return;
  }
  const payload = {
    profile: editingId
      ? editingMedicationProfile
      : (els.metricProfile.value || ''),
    name,
    dose: (els.medicationDose.value || '').trim(),
    frequency: (els.medicationFrequency.value || '').trim(),
    note: (els.medicationNote.value || '').trim(),
    active: els.medicationActive.checked,
    stock,
    stock_unit: (els.medicationStockUnit.value || '').trim(),
    low_stock_threshold: threshold,
    expiry_date: els.medicationExpiry.value || '',
    started_at: els.medicationStartedAt.value || '',
    stopped_at: els.medicationStoppedAt.value || '',
    stop_reason: (els.medicationStopReason.value || '').trim(),
  };

  medicationSaving = true;
  els.medicationSave.disabled = true;
  try {
    const res = await fetch(
      editingId ? `/api/medications/${editingId}` : '/api/medications',
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
    resetMedicationForm();
    const safetyAlerts = data.safety && Array.isArray(data.safety.alerts)
      ? data.safety.alerts
      : [];
    if (safetyAlerts.length) {
      const first = safetyAlerts[0];
      const extra = safetyAlerts.length > 1
        ? `（另有 ${safetyAlerts.length - 1} 条提示）`
        : '';
      setMedicationStatus(
        '⚠ 用药安全提示：'
        + (first.title || '')
        + (first.detail ? `：${first.detail}` : '')
        + extra,
        false
      );
    } else {
      setMedicationStatus(editingId ? '已保存修改。' : '已添加药物。');
    }
    await loadMedications();
    refreshOverview();
  } catch (err) {
    setMedicationStatus(`保存失败：${err.message || err}`, true);
  } finally {
    medicationSaving = false;
    els.medicationSave.disabled = false;
  }
}

async function deleteMedication(item) {
  if (!window.confirm(`删除药物「${item.name}」？`)) return;
  try {
    const res = await fetch(`/api/medications/${item.id}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    if (editingMedicationId === item.id) resetMedicationForm();
    setMedicationStatus('已删除药物。');
    await loadMedications();
    loadMedicationLogs();
    refreshOverview();
    refreshReminderBadge();
  } catch (err) {
    setMedicationStatus(`删除失败：${err.message || err}`, true);
  }
}

async function loadMedicationsIntoInteractions() {
  // 只使用主界面的当前档案；健康记录抽屉的筛选值可能停留在其他患者
  const profile = els.profileSelect.value || '';
  if (els.calcInteractionsAllergies) {
    const allergies = String(
      readPatientContext().allergies || ''
    ).trim();
    if (allergies && !els.calcInteractionsAllergies.value.trim()) {
      els.calcInteractionsAllergies.value = allergies;
    }
    try {
      const allergyRes = await fetch(
        '/api/allergies?active_only=true&limit=200'
        + `&profile=${encodeURIComponent(profile)}`
      );
      if (allergyRes.ok) {
        const structured = (await allergyRes.json()).allergies || [];
        const text = structured
          .map((row) => String(row.substance || '').trim())
          .filter(Boolean)
          .join('、');
        if (
          text
          && !els.calcInteractionsAllergies.value.includes(text)
        ) {
          const current = els.calcInteractionsAllergies.value.trim();
          els.calcInteractionsAllergies.value = current
            ? `${current}、${text}`
            : text;
        }
      }
    } catch {
      // 结构化过敏史加载失败不影响用药清单带入
    }
  }
  try {
    const res = await fetch(
      `/api/medications?active_only=true&limit=100`
      + `&profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const items = (await res.json()).medications || [];
    if (!items.length) {
      els.calcInteractionsDrugs.value = '';
      els.calcResult.hidden = true;
      currentCalcAction = null;
      els.calcSave.hidden = true;
      setCalcStatus(
        `用药清单中还没有在用药物${profile ? `（档案：${profile}）` : ''}。`,
        true
      );
      return;
    }
    els.calcInteractionsDrugs.value = items
      .map((item) => (item.dose ? `${item.name} ${item.dose}` : item.name))
      .join('\n');
    setCalcStatus(
      `已带入 ${items.length} 种在用药物`
      + `${profile ? `（档案：${profile}）` : ''}，可直接检查相互作用。`
    );
  } catch (err) {
    setCalcStatus(`带入用药清单失败：${err.message || err}`, true);
  }
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
  els.metricContext.value = item.context || '';
  els.metricNote.value = item.note || '';
  els.metricAdd.textContent = '保存修改';
  els.metricCancel.hidden = false;
  setMetricsStatus(`正在编辑：${formatMetricValue(item)}`);

  const form = els.metricsDrawer.querySelector('.metrics-form');
  if (form) form.scrollIntoView({ block: 'start' });
  (item.metric === 'bp' ? els.metricSystolic : els.metricValue).focus();
}

async function addMetricRecord() {
  if (metricSaving) return;
  const type = els.metricType.value;
  const profile = els.metricProfile.value;
  const measuredAt = els.metricTime.value || metricNowLocal();
  const note = els.metricNote.value.trim();
  const editing = editingMetricId;
  let value1;
  let value2 = null;

  if (type === 'bp') {
    const systolicRaw = els.metricSystolic.value.trim();
    const diastolicRaw = els.metricDiastolic.value.trim();
    value1 = Number(systolicRaw);
    value2 = Number(diastolicRaw);
    if (
      !systolicRaw || !diastolicRaw
      || !Number.isFinite(value1) || !Number.isFinite(value2)
    ) {
      setMetricsStatus('请填写收缩压和舒张压。', true);
      return;
    }
  } else {
    const rawValue = els.metricValue.value.trim();
    value1 = Number(rawValue);
    if (!rawValue || !Number.isFinite(value1)) {
      setMetricsStatus('请填写数值。', true);
      return;
    }
  }

  metricSaving = true;
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
          context: els.metricContext.value || '',
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

    const alerts = Array.isArray(data.critical_alerts)
      ? data.critical_alerts
      : [];
    if (alerts.length) {
      setMetricsStatus(
        `⚠ ${alerts.map((row) => row.message).join(' ')}`,
        true
      );
    } else {
      setMetricsStatus(editing ? '已更新记录。' : '已保存记录。');
    }
    resetMetricForm();
    await refreshMetrics();
    loadBpMonitoring();
  } catch (err) {
    setMetricsStatus(`${editing ? '更新' : '保存'}失败：${err.message || err}`, true);
  } finally {
    metricSaving = false;
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
    loadBpMonitoring();
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
    loadBpMonitoring();
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
  const days = metricRangeDays();
  const query = els.metricSearch
    ? String(els.metricSearch.value || '').trim()
    : '';

  try {
    const res = await fetch(
      `/api/metrics/export.csv?profile=${encodeURIComponent(profile)}`
      + `&metric=${encodeURIComponent(metric)}`
      + (days > 0 ? `&days=${days}` : '')
      + (query ? `&q=${encodeURIComponent(query)}` : '')
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

const {
  findLabItem,
  labNumber,
  labCreatinineMgDl,
  labBilirubinMgDl,
  labBilirubinUmolL,
  labAlbuminGL,
  labUreaMmolL,
  childPughBilirubinScore,
  childPughAlbuminScore,
  childPughInrScore,
} = window.MedixLabPrefill || {};

async function loadLatestLabItems() {
  const profile = els.profileSelect ? els.profileSelect.value || '' : '';
  const res = await fetch(
    `/api/lab-results/latest?profile=${encodeURIComponent(profile)}`
  );
  if (!res.ok) throw new Error(`服务返回 ${res.status}`);
  return (await res.json()).lab_results || [];
}

function fillCalcAgeSex() {
  const context = readPatientContext();
  const age = Number.parseInt(context.age, 10);
  if (Number.isFinite(age) && !Number(els.calcEgfrAge.value)) {
    els.calcEgfrAge.value = String(age);
  }
  const fib4Age = document.getElementById('calc-fib4-age');
  if (fib4Age && Number.isFinite(age) && !Number(fib4Age.value)) {
    fib4Age.value = String(age);
  }
  const curbAge = document.getElementById('calc-curb-age');
  if (curbAge && Number.isFinite(age) && !Number(curbAge.value)) {
    curbAge.value = String(age);
  }
  if (
    (context.sex === '女' || context.sex === '男')
    && !els.calcEgfrSex.value
  ) {
    els.calcEgfrSex.value = context.sex === '女' ? 'female' : 'male';
  }
}

async function prefillEgfrFromLab() {
  try {
    fillCalcAgeSex();
    const items = await loadLatestLabItems();
    const creatinine = findLabItem(items, 'creatinine');
    const converted = labCreatinineMgDl(creatinine);
    if (!converted) {
      setCalcStatus('未找到最近的肌酐化验结果，请先在化验结果中登记。', true);
      return;
    }
    els.calcEgfrCreatinine.value = String(
      Math.round(converted.value * 100) / 100
    );
    els.calcEgfrUnit.value = 'mg/dL';
    setCalcStatus(
      `已带入肌酐 ${labNumber(creatinine)}`
      + `${creatinine.unit ? ` ${creatinine.unit}` : ''}`
      + `（${creatinine.tested_at || '最近一次'}）`
      + (converted.converted ? '，已换算为 mg/dL。' : '。')
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function prefillFib4FromLab() {
  try {
    fillCalcAgeSex();
    const items = await loadLatestLabItems();
    const ast = findLabItem(items, 'ast');
    const alt = findLabItem(items, 'alt');
    const platelets = findLabItem(items, 'platelets');
    const missing = [];
    if (labNumber(ast) === null) missing.push('AST');
    if (labNumber(alt) === null) missing.push('ALT');
    if (labNumber(platelets) === null) missing.push('血小板');
    if (missing.length) {
      setCalcStatus(
        `缺少化验项目：${missing.join('、')}，请先登记后再带入。`,
        true
      );
      return;
    }
    document.getElementById('calc-fib4-ast').value =
      String(Math.round(labNumber(ast)));
    document.getElementById('calc-fib4-alt').value =
      String(Math.round(labNumber(alt)));
    document.getElementById('calc-fib4-platelets').value =
      String(Math.round(labNumber(platelets)));
    setCalcStatus(
      `已带入 AST ${labNumber(ast)}、ALT ${labNumber(alt)}、`
      + `血小板 ${labNumber(platelets)}（最近一次化验）。`
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function prefillMeldFromLab() {
  try {
    const items = await loadLatestLabItems();
    const bilirubin = findLabItem(items, 'bilirubin');
    const inr = findLabItem(items, 'inr');
    const creatinine = findLabItem(items, 'creatinine');
    const bilirubinValue = labBilirubinMgDl(bilirubin);
    const creatinineValue = labCreatinineMgDl(creatinine);
    const missing = [];
    if (!bilirubinValue) missing.push('总胆红素');
    if (labNumber(inr) === null) missing.push('INR');
    if (!creatinineValue) missing.push('肌酐');
    if (missing.length) {
      setCalcStatus(
        `缺少化验项目：${missing.join('、')}，请先登记后再带入。`,
        true
      );
      return;
    }
    document.getElementById('calc-meld-bilirubin').value = String(
      Math.round(bilirubinValue.value * 10) / 10
    );
    document.getElementById('calc-meld-inr').value = String(
      Math.round(labNumber(inr) * 10) / 10
    );
    document.getElementById('calc-meld-creatinine').value = String(
      Math.round(creatinineValue.value * 10) / 10
    );
    setCalcStatus(
      `已带入总胆红素、INR 与肌酐（最近一次化验）`
      + (bilirubinValue.converted || creatinineValue.converted
        ? '，胆红素/肌酐已换算为 mg/dL。'
        : '。')
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function prefillApriFromLab() {
  try {
    const items = await loadLatestLabItems();
    const ast = findLabItem(items, 'ast');
    const platelets = findLabItem(items, 'platelets');
    const missing = [];
    if (labNumber(ast) === null) missing.push('AST');
    if (labNumber(platelets) === null) missing.push('血小板');
    if (missing.length) {
      setCalcStatus(
        `缺少化验项目：${missing.join('、')}，请先登记后再带入。`,
        true
      );
      return;
    }
    els.calcApriAst.value = String(Math.round(labNumber(ast)));
    els.calcApriPlatelets.value = String(
      Math.round(labNumber(platelets))
    );
    const refHigh = Number(ast && ast.ref_high);
    let ulnText = '';
    if (Number.isFinite(refHigh) && refHigh > 0) {
      const uln = Math.round(refHigh * 10) / 10;
      els.calcApriUln.value = String(uln);
      ulnText = `，AST 正常上限按化验单带入 ${uln} U/L`;
    } else {
      ulnText = '；未找到 AST 参考上限，正常上限仍为 40 U/L，'
        + '请按化验单调整';
    }
    setCalcStatus(
      `已带入 AST ${Math.round(labNumber(ast))} U/L、`
      + `血小板 ${Math.round(labNumber(platelets))} ×10⁹/L`
      + ulnText + '（最近一次化验）。'
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function prefillEagFromLab() {
  try {
    const items = await loadLatestLabItems();
    const hba1c = findLabItem(items, 'hba1c');
    let value = labNumber(hba1c);
    if (value === null) {
      setCalcStatus(
        '未找到最近的糖化血红蛋白化验结果，请先在化验结果中登记。',
        true
      );
      return;
    }
    const unit = String((hba1c && hba1c.unit) || '').toLowerCase();
    let converted = false;
    if (unit.includes('mmol/mol')) {
      value = 0.09148 * value + 2.152;
      converted = true;
    }
    if (value < 3 || value > 20) {
      setCalcStatus(
        '糖化血红蛋白数值或单位无法识别，请手动填写。',
        true
      );
      return;
    }
    value = Math.round(value * 10) / 10;
    els.calcEagHba1c.value = String(value);
    setCalcStatus(
      `已带入糖化血红蛋白 ${value}%`
      + (converted ? '（已由 mmol/mol 换算）' : '')
      + '（最近一次化验）。'
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function loadLatestBpMetric() {
  const profile = els.profileSelect ? els.profileSelect.value || '' : '';
  const res = await fetch(
    `/api/metrics?metric=bp&limit=1&profile=${encodeURIComponent(profile)}`
  );
  if (!res.ok) throw new Error(`服务返回 ${res.status}`);
  const data = await res.json();
  return (data.metrics || [])[0] || null;
}

async function prefillChildPughFromLab() {
  try {
    const items = await loadLatestLabItems();
    const bilirubin = findLabItem(items, 'bilirubin');
    const albumin = findLabItem(items, 'albumin');
    const inr = findLabItem(items, 'inr');
    const bilirubinValue = labBilirubinUmolL(bilirubin);
    const albuminValue = labAlbuminGL(albumin);
    const missing = [];
    if (!bilirubinValue) missing.push('总胆红素');
    if (!albuminValue) missing.push('白蛋白');
    if (labNumber(inr) === null) missing.push('INR');
    if (missing.length) {
      setCalcStatus(
        `缺少化验项目：${missing.join('、')}，请先登记后再带入。`,
        true
      );
      return;
    }
    const bilirubinScore = childPughBilirubinScore(bilirubinValue.value);
    const albuminScore = childPughAlbuminScore(albuminValue.value);
    const inrValue = labNumber(inr);
    const inrScore = childPughInrScore(inrValue);
    els.calcChildBilirubin.value = String(bilirubinScore);
    els.calcChildAlbumin.value = String(albuminScore);
    els.calcChildInr.value = String(inrScore);
    setCalcStatus(
      `已带入总胆红素 ${Math.round(bilirubinValue.value * 10) / 10} `
      + `μmol/L（${bilirubinScore} 分）、白蛋白 `
      + `${Math.round(albuminValue.value * 10) / 10} g/L`
      + `（${albuminScore} 分）、INR ${inrValue}（${inrScore} 分）`
      + (bilirubinValue.converted || albuminValue.converted
        ? '，已按单位换算。' : '。')
      + '腹水与肝性脑病请按临床情况选择。'
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

async function prefillCurb65FromLab() {
  try {
    fillCalcAgeSex();
    const items = await loadLatestLabItems();
    const urea = findLabItem(items, 'urea');
    const ureaValue = labUreaMmolL(urea);
    if (!ureaValue) {
      setCalcStatus(
        '未找到最近的尿素氮化验结果，请先在化验结果中登记。',
        true
      );
      return;
    }
    els.calcCurbUrea.value = String(
      Math.round(ureaValue.value * 10) / 10
    );
    let bp = null;
    let bpFailed = false;
    try {
      bp = await loadLatestBpMetric();
    } catch {
      bpFailed = true;
    }
    let bpText = '';
    if (bp && Number(bp.value1) > 0) {
      els.calcCurbSbp.value = String(Math.round(Number(bp.value1)));
      if (Number(bp.value2) > 0) {
        els.calcCurbDbp.value = String(Math.round(Number(bp.value2)));
      }
      bpText = `、血压 ${Math.round(Number(bp.value1))}`
        + (Number(bp.value2) > 0
          ? `/${Math.round(Number(bp.value2))}`
          : '')
        + ' mmHg';
    } else {
      bpText = bpFailed ? '（血压读取失败，可稍后重试）' : '（暂无血压记录）';
    }
    setCalcStatus(
      `已带入尿素氮 ${Math.round(ureaValue.value * 10) / 10} mmol/L`
      + (urea && ureaValue.converted ? '（已按单位换算）' : '')
      + `${bpText}（最近一次）；呼吸频率与意识状态请按实际情况填写。`
    );
  } catch (err) {
    setCalcStatus(`带入化验失败：${err.message || err}`, true);
  }
}

function setCalcStatus(text, isError = false) {
  els.calcStatus.textContent = text;
  els.calcStatus.hidden = !text;
  els.calcStatus.classList.toggle('error', isError);
}

function setCalcTool(tool) {
  calcRequestSeq += 1;
  els.calcTool.value = tool;
  try {
    safeStorageSet('medix.calcTool', tool);
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
    els.calcFormCat,
    els.calcFormEss,
    els.calcFormHasBled,
    els.calcFormGcs,
    els.calcFormWells,
    els.calcFormWellsPe,
    els.calcFormInteractions,
    els.calcFormQsofa,
    els.calcFormFib4,
    els.calcFormApri,
    els.calcFormEag,
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
  if (tool === 'cat') ensureCatForm();
  if (tool === 'ess') ensureEssForm();
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
  // 每次打开都按当前患者重置过敏史，避免沿用上一患者/上次带入的内容
  if (els.calcInteractionsAllergies) {
    els.calcInteractionsAllergies.value = String(
      context.allergies || ''
    ).trim();
  }
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
    storedTool = safeStorageGet('medix.calcTool') || '';
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
  calcRequestSeq += 1;
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
  const seq = ++calcRequestSeq;
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
    if (seq !== calcRequestSeq) return;

    const view = build(data);
    // 先让 live 区域可见，再写入结果，读屏才会播报
    els.calcResult.hidden = false;
    els.calcResultTitle.textContent = view.title;
    els.calcResultMain.textContent = view.main;
    els.calcResultDetail.textContent = view.detail || '';
    els.calcResultNote.textContent = view.note || data.note || '';
    els.calcResult.dataset.level = view.level || '';
    currentCalcAction = view.action || null;
    els.calcSave.hidden = !currentCalcAction;
    els.calcSave.textContent = currentCalcAction ? currentCalcAction.label : '保存到健康记录';
    els.calcResult.scrollIntoView({ block: 'nearest' });
    setCalcStatus('');
  } catch (err) {
    if (seq !== calcRequestSeq) return;
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
  const sex = els.calcEgfrSex.value;
  if (!sex) {
    window.alert('请选择性别后再计算 eGFR。');
    return;
  }
  runCalc(
    '/api/tools/egfr',
    {
      age: Number(els.calcEgfrAge.value),
      sex,
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
  const sex = els.calcChadsSex.value;
  if (!sex) {
    window.alert('请选择性别后再计算 CHA₂DS₂-VASc。');
    return;
  }
  runCalc(
    '/api/tools/chads-vasc',
    {
      age: Number(els.calcChadsAge.value),
      sex,
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

/** 量表下拉的“请选择”占位，避免未作答被当成 0 分。 */
function addScalePlaceholder(select) {
  const option = document.createElement('option');
  option.value = '';
  option.textContent = '请选择';
  option.disabled = true;
  option.selected = true;
  select.appendChild(option);
}

/** 收集量表答案；有未作答题时提示并聚焦第一题，返回 null。 */
function collectScaleAnswers(container, label) {
  const selects = Array.from(container.querySelectorAll('select'));
  const missing = selects.find((select) => !select.value);
  if (missing) {
    window.alert(`请回答全部题目后再计算 ${label}。`);
    missing.focus();
    return null;
  }
  return selects.map((select) => Number(select.value));
}

const PHQ9_QUESTIONS = [
  '做事时提不起劲或没有兴趣',  '感到心情低落、沮丧或绝望',
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
    addScalePlaceholder(select);
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
    addScalePlaceholder(select);
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
      profile: els.profileSelect.value || '',
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
  const answers = collectScaleAnswers(els.calcGad7Questions, 'GAD-7');
  if (!answers) return;

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

const CAT_QUESTIONS = [
  '我从不咳嗽 —— 我总在咳嗽',
  '我一点痰也没有 —— 我有很多很多痰',
  '我一点也没有胸闷的感觉 —— 我有很重的胸闷的感觉',
  '当我爬坡或爬一层楼时，我并不感到喘不过气来 —— 爬坡或爬一层楼时我非常喘不过气',
  '我在家里的任何劳动都不受慢阻肺影响 —— 家里的任何活动都很受慢阻肺影响',
  '每当我外出时就外出 —— 因为慢阻肺我很少外出',
  '我睡得很好 —— 因为慢阻肺我睡眠不好',
  '我精力旺盛 —— 我一点精力都没有',
];

function ensureCatForm() {
  if (els.calcCatQuestions.children.length) return;

  CAT_QUESTIONS.forEach((question, index) => {
    const label = document.createElement('label');
    label.className = 'calc-field phq9-question';

    const text = document.createElement('span');
    text.textContent = `${index + 1}. ${question}`;

    const select = document.createElement('select');
    select.className = 'phq9-select';
    select.dataset.index = String(index);
    addScalePlaceholder(select);
    for (let value = 0; value <= 5; value += 1) {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = String(value);
      select.appendChild(option);
    }

    label.append(text, select);
    els.calcCatQuestions.appendChild(label);
  });
}

const ESS_QUESTIONS = [
  '坐着阅读时',
  '看电视时',
  '在公共场所坐着不动时（如开会、剧院）',
  '作为乘客连续乘车 1 小时',
  '午后躺下休息时',
  '坐着与人交谈时',
  '午餐后安静坐着（未饮酒）',
  '开车时等红灯或堵车几分钟',
];

const ESS_OPTIONS = [
  ['0', '从不'],
  ['1', '很少'],
  ['2', '有时'],
  ['3', '经常'],
];

function ensureEssForm() {
  if (els.calcEssQuestions.children.length) return;

  ESS_QUESTIONS.forEach((question, index) => {
    const label = document.createElement('label');
    label.className = 'calc-field phq9-question';

    const text = document.createElement('span');
    text.textContent = `${index + 1}. ${question}`;

    const select = document.createElement('select');
    select.className = 'phq9-select';
    select.dataset.index = String(index);
    addScalePlaceholder(select);
    for (const [value, optionLabel] of ESS_OPTIONS) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${value} ${optionLabel}`;
      select.appendChild(option);
    }

    label.append(text, select);
    els.calcEssQuestions.appendChild(label);
  });
}

function submitEss(event) {
  event.preventDefault();
  ensureEssForm();
  const answers = collectScaleAnswers(els.calcEssQuestions, 'ESS');
  if (!answers) return;

  runCalc(
    '/api/tools/ess',
    { answers },
    (data) => ({
      title: 'ESS 结果',
      main: `评分 ${data.score} / 24`,
      detail: `嗜睡程度：${data.drowsiness}`,
      note: data.note,
      level: data.score > 15 ? 'warning' : '',
      action: {
        label: '保存 ESS 评分',
        run: () => saveScreenerScore('ess', data.score, 'ESS 嗜睡评估'),
      },
    })
  );
}

function submitCat(event) {
  event.preventDefault();
  ensureCatForm();
  const answers = collectScaleAnswers(els.calcCatQuestions, 'CAT');
  if (!answers) return;

  runCalc(
    '/api/tools/cat',
    { answers },
    (data) => ({
      title: 'CAT 结果',
      main: `评分 ${data.score} / 40`,
      detail: `影响程度：${data.impact}`,
      note: data.note,
      level: data.score > 30 ? 'warning' : '',
      action: {
        label: '保存 CAT 评分',
        run: () => saveScreenerScore('cat', data.score, 'CAT 评估'),
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
      action: {
        label: '保存 mMRC 分级',
        run: () => saveScreenerScore('mmrc', data.grade, 'mMRC 呼吸困难'),
      },
    })
  );
}

function submitAuditC(event) {
  event.preventDefault();
  const auditcSelects = [
    els.calcAuditCQ1, els.calcAuditCQ2, els.calcAuditCQ3,
  ];
  const missing = auditcSelects.find((select) => !select.value);
  if (missing) {
    window.alert('请回答全部题目后再计算 AUDIT-C。');
    missing.focus();
    return;
  }
  runCalc(
    '/api/tools/audit-c',
    {
      q1: Number(els.calcAuditCQ1.value),
      q2: Number(els.calcAuditCQ2.value),
      q3: Number(els.calcAuditCQ3.value),
      sex: els.calcAuditCSex.value,
    },
    (data) => ({
      title: 'AUDIT-C 结果',
      main: `评分 ${data.score} / 12`,
      detail: `风险分层：${data.risk}`,
      note: data.note,
      level: data.risk === '高风险' ? 'warning' : '',
      action: {
        label: '保存 AUDIT-C 评分',
        run: () => saveScreenerScore('auditc', data.score, 'AUDIT-C 筛查'),
      },
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
  const answers = collectScaleAnswers(els.calcPhq9Questions, 'PHQ-9');
  if (!answers) return;

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

function submitApri(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/apri',
    {
      ast: Number(els.calcApriAst.value),
      platelets: Number(els.calcApriPlatelets.value),
      ast_uln: Number(els.calcApriUln.value) || 40,
    },
    (data) => ({
      title: 'APRI 结果',
      main: `APRI ${data.apri}`,
      detail: `${data.risk}：${data.detail}`,
      note: data.note,
      level: data.risk === '提示纤维化' ? 'warning' : '',
    })
  );
}

function submitEag(event) {
  event.preventDefault();
  runCalc(
    '/api/tools/eag',
    { hba1c: Number(els.calcEagHba1c.value) },
    (data) => {
      const aboveTarget = Number(data.hba1c) >= 7;
      return {
        title: 'eAG 结果',
        main: `平均血糖约 ${data.eag_mmol_l} mmol/L`,
        detail: `HbA1c ${data.hba1c}% ≈ ${data.eag_mg_dl} mg/dL`
          + (aboveTarget ? '（高于常见控制目标 7%）' : ''),
        note: data.note,
        level: aboveTarget ? 'warning' : '',
      };
    }
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
  } else if (action === 'search') {
    await openMetrics();
    const searchQuery = (params.get('q') || '').trim();
    if (searchQuery) {
      if (els.healthSearchInput) {
        els.healthSearchInput.value = searchQuery;
      }
      await runHealthSearch(searchQuery);
    }
    if (els.healthSearchInput) els.healthSearchInput.focus();
  } else if (action === 'metrics') {
    openMetrics();
    if (params.get('symptoms') === '1' && els.symptomSection) {
      els.symptomSection.open = true;
    }
    if (params.get('medications') === '1' && els.medicationSection) {
      els.medicationSection.open = true;
    }
  }
}

async function restoreCurrentSession(expectedQuestion = '', baselineMessageId = 0) {
  if (!sessionId) return false;
  const expectedSession = sessionId;

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(expectedSession)}/messages?limit=200`
    );
    if (!res.ok) return false;

    const data = await res.json();
    if (sessionId !== expectedSession || busy) return false;

    const messages = data.messages || [];
    if (!messages.length) return false;

    const newestAssistantId = messages.reduce(
      (max, message) => message.role === 'assistant'
        ? Math.max(max, Number(message.id) || 0)
        : max,
      0
    );
    const hasIds = messages.some(
      (message) => (Number(message.id) || 0) > 0
    );
    if (expectedQuestion) {
      if (!hasIds) {
        // 纯短期记忆回退时没有消息 ID，退回按文本和顺序判断
        let questionIndex = -1;
        for (let i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i].role === 'user'
            && messages[i].content === expectedQuestion) {
            questionIndex = i;
            break;
          }
        }
        const persisted = questionIndex >= 0
          && messages.slice(questionIndex + 1).some(
            (message) => message.role === 'assistant'
          );
        if (!persisted) return false;
      } else {
        // 本轮提问和回答都必须比中止前的最大消息 ID 更新；否则只是同文本的
        // 旧问答，不能覆盖刚停止的提问与“已停止”卡片
        const persistedQuestion = messages.some(
          (message) => message.role === 'user'
            && message.content === expectedQuestion
            && (Number(message.id) || 0) > baselineMessageId
        );
        if (!persistedQuestion || newestAssistantId <= baselineMessageId) {
          return false;
        }
      }
    }

    els.readout.session.textContent = shortSession(expectedSession);
    renderStoredMessages(messages, expectedSession, data.profile || '');
    setVitals('idle', '已恢复上次会诊');
    return true;
  } catch {
    // 服务未启动或尚无记录时保持空状态
    return false;
  }
}

function scheduleAbortReconcile(session, question, baselineMessageId) {
  // 停止接收不等于服务端没有保存结果；稍后拉取一次会话，
  // 仅当本轮问答已落库时恢复，避免清掉停止提示并重复会诊
  [1200, 3500].forEach((delay) => {
    setTimeout(() => {
      if (!busy && sessionId === session) {
        restoreCurrentSession(question, baselineMessageId);
      }
    }, delay);
  });
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
  const baselineMessageId = lastServerMessageId;
  const askTurn = renderAsk(question);

  const startedAt = performance.now();
  const working = renderWorking();
  const workingText = working.querySelector('.working-text');
  const workingTime = working.querySelector('.working-time');
  const elapsedTimer = setInterval(() => {
    workingTime.textContent = `${Math.floor((performance.now() - startedAt) / 1000)}s`;
  }, 500);

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
        context: await buildConsultContext(),
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
            // 流式增量逐 token 更新，不能让读屏逐个朗读
            els.thread.setAttribute('aria-live', 'off');
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

    // 流式期间关闭了 live 播报，最终答复前恢复，让读屏朗读完整意见
    els.thread.setAttribute('aria-live', 'polite');
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
      // 流式期间关闭了 live 播报，终态卡片需要重新开启后才会被读出
      els.thread.setAttribute('aria-live', 'polite');
      renderStopped({ question, askTurn, baselineMessageId });
      scheduleAbortReconcile(sessionId, question, baselineMessageId);
      AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '已停止'));
      els.readout.mode.textContent = '已停止';
      logStep('用户停止了本次会诊', false);
      setVitals('idle', '已停止会诊');
    } else {
      // 渲染或解析异常时也要主动断开上游，避免服务端继续占用会诊锁
      try {
        controller.abort();
      } catch {
        // 已结束的请求忽略
      }
      els.thread.setAttribute('aria-live', 'polite');
      // 传输错误也可能发生在服务端已保存之后：稍后核对并恢复，避免重试重复落库
      renderError(
        err.message || String(err),
        { question, askTurn, baselineMessageId }
      );
      scheduleAbortReconcile(sessionId, question, baselineMessageId);
      AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '待命'));
      els.readout.mode.textContent = '中断';
      logStep(`中断：${err.message || err}`, false);
      setVitals('bad', '会诊中断');
    }
  } finally {
    clearInterval(elapsedTimer);
    els.thread.setAttribute('aria-live', 'polite');
    busy = false;
    activeController = null;
    els.stop.hidden = true;
    els.send.disabled = false;
    els.railScan.hidden = true;
    els.input.focus();
  }
}

/* ---------- 系统状态检查 ---------- */

let checkStatusPromise = null;

const OFFLINE_BANNER =
  '当前网络不可用：会诊与历史数据需要本地后端服务，恢复连接后会自动重试。';

let swRegistration = null;
let swHadController = false;
let swUpdateReady = false;
let loadedWebBuild = null;

function checkStatus() {
  if (!checkStatusPromise) {
    checkStatusPromise = checkStatusOnce().finally(() => {
      checkStatusPromise = null;
    });
  }
  return checkStatusPromise;
}

async function checkStatusOnce() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();

    // 资产更新不会改变 service worker（旧实现），轮询状态里的构建号
    // 能覆盖只改 HTML/JS/CSS 的发布
    if (data.web_build) {
      if (!loadedWebBuild) loadedWebBuild = data.web_build;
      else if (loadedWebBuild !== data.web_build) showUpdateBanner();
    }

    pricing = data.pricing || null;
    updateCostDisplay();

    if (data.auth_required) {
      if (authGateOpen) return;

      const token = safeSessionGet('medix.token');
      if (!token) {
        setVitals('warn', '需要访问令牌');
        showAuthGate();
        return;
      }

      const verify = await fetch('/api/auth/verify');
      if (!verify.ok) {
        safeSessionRemove('medix.token');
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
    if (navigator.onLine === false) {
      // 离线时保留专用提示，避免通用错误覆盖并丢失「离线」状态
      setVitals('warn', '离线');
      showBanner(OFFLINE_BANNER);
      return;
    }
    setVitals('bad', '无法连接服务');
    showBanner(`无法连接后端服务：${escapeHtml(err.message)}`);
  }
}

function showBanner(html) {
  els.banner.innerHTML = html;
  els.banner.hidden = false;
}

function hideBanner() {
  if (swUpdateReady) {
    // 新版本提示优先保留；按钮已存在时只恢复可见性，
    // 避免 20 秒一次的状态轮询重建 DOM 导致焦点丢失
    if (document.getElementById('sw-refresh')) {
      els.banner.hidden = false;
    } else {
      showUpdateBanner();
    }
    return;
  }
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
  if (els.ctxBloodType && els.ctxBloodType.value) {
    context.blood_type = els.ctxBloodType.value;
  }
  const emergencyContacts = [
    els.ctxEmergency1 ? els.ctxEmergency1.value.trim() : '',
    els.ctxEmergency2 ? els.ctxEmergency2.value.trim() : '',
  ].filter(Boolean);
  if (emergencyContacts.length) {
    context.emergency_contacts = emergencyContacts.join('；');
  }

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

async function buildConsultContext() {
  const context = readPatientContext();
  // 紧急联系人只用于本机急救卡，不随会诊发送给模型
  delete context.emergency_contacts;
  const profile = els.profileSelect.value || '';
  try {
    const [
      metricsRes, remindersRes, symptomsRes, medicationsRes,
    ] = await Promise.all([
      fetch(`/api/metrics/latest?profile=${encodeURIComponent(profile)}`),
      fetch('/api/reminders'),
      fetch(
        `/api/symptoms?limit=3&days=7&profile=${encodeURIComponent(profile)}`
      ),
      fetch(
        `/api/medications?active_only=true&limit=20`
        + `&profile=${encodeURIComponent(profile)}`
      ),
    ]);
    if (metricsRes.ok) {
      const metrics = (await metricsRes.json()).metrics || [];
      const recent = metrics
        .slice(0, 4)
        .map(
          (item) => `${METRIC_NAMES[item.metric]} ${formatMetricValue(item)}`
            + `（${formatTime(item.measured_at)}）`
        );
      if (recent.length) context.recent_metrics = recent.join('；');
    }
    if (remindersRes.ok) {
      const reminders = (await remindersRes.json()).reminders || [];
      const scoped = profile
        ? reminders.filter((item) => (item.profile || '') === profile)
        : reminders.filter((item) => !item.profile);
      const pending = scoped
        .slice(0, 4)
        .map(
          (item) => `${item.title}（${formatReminderDue(item.due_at)}）`
        );
      if (pending.length) context.pending_reminders = pending.join('；');
    }
    if (symptomsRes.ok) {
      const symptoms = (await symptomsRes.json()).symptoms || [];
      const recent = symptoms
        .slice(0, 3)
        .map(
          (item) => `${item.symptom} ${item.severity}/10`
            + `（${formatTime(item.recorded_at)}）`
        );
      if (recent.length) context.recent_symptoms = recent.join('；');
    }
    if (medicationsRes.ok) {
      const medications = (await medicationsRes.json()).medications || [];
      const activeMeds = medications.slice(0, 10).map((item) => (
        item.name
        + (item.dose ? ` ${item.dose}` : '')
        + (item.frequency ? ` ${item.frequency}` : '')
        + (Number(item.week_count) > 0
          ? `（近 7 天打卡 ${Number(item.week_count)} 次）`
          : '')
      ));
      if (activeMeds.length) {
        context.active_medications = activeMeds.join('；');
      }
    }
  } catch {
    // 辅助信息加载失败时不影响本次会诊
  }
  return context;
}

function updateContextSummary() {
  const context = readPatientContext();
  const parts = [];
  if (context.age) parts.push(`${context.age} 岁`);
  if (context.sex) parts.push(context.sex);
  if (context.blood_type) parts.push(`血型 ${context.blood_type}`);
  const contactCount = (
    typeof context.emergency_contacts === 'string'
      ? context.emergency_contacts.split(/[；;]/).filter(Boolean)
      : (Array.isArray(context.emergency_contacts)
        ? context.emergency_contacts.filter(Boolean)
        : [])
  ).length;
  if (contactCount) parts.push(`紧急联系人 ${contactCount} 位`);
  if (context.duration) parts.push(context.duration);
  if (context.history) parts.push('有既往病史');
  if (context.medications) parts.push('有用药');
  if (context.allergies) parts.push('有过敏史');

  els.contextSummary.textContent = parts.join(' · ');
  els.contextSummary.hidden = parts.length === 0;
  els.contextToggle.classList.toggle('has-context', parts.length > 0);

  try {
    if (parts.length) {
      safeSessionSet(CONTEXT_STORAGE_KEY, JSON.stringify(context));
    } else {
      safeSessionRemove(CONTEXT_STORAGE_KEY);
    }
  } catch {
    // 隐私模式下 sessionStorage 不可用时忽略
  }
}

function applyPatientContext(context) {
  const data = context && typeof context === 'object' ? context : {};
  els.ctxAge.value = data.age || '';
  els.ctxSex.value = data.sex || '';
  if (els.ctxBloodType) {
    const bloodType = String(data.blood_type || '');
    if (
      bloodType
      && ![...els.ctxBloodType.options]
        .some((option) => option.value === bloodType)
    ) {
      const option = document.createElement('option');
      option.value = bloodType;
      option.textContent = bloodType;
      els.ctxBloodType.appendChild(option);
    }
    els.ctxBloodType.value = bloodType;
  }
  const contacts = (
    typeof data.emergency_contacts === 'string'
      ? data.emergency_contacts.split(/[；;]/)
      : (Array.isArray(data.emergency_contacts)
        ? data.emergency_contacts
        : [])
  ).filter(Boolean);
  if (els.ctxEmergency1) els.ctxEmergency1.value = contacts[0] || '';
  if (els.ctxEmergency2) els.ctxEmergency2.value = contacts[1] || '';
  els.ctxDuration.value = data.duration || '';
  els.ctxHistory.value = data.history || '';
  els.ctxMedications.value = data.medications || '';
  els.ctxAllergies.value = data.allergies || '';
  if (els.profileSelect) els.profileSelect.value = '';
  updateContextSummary();
}

function loadSavedContext() {
  try {
    const raw = safeSessionGet(CONTEXT_STORAGE_KEY);
    if (raw) applyPatientContext(JSON.parse(raw));
    els.ctxSingle.checked = safeSessionGet(SINGLE_MODE_KEY) === '1';
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
    const raw = safeStorageGet(PROFILES_STORAGE_KEY);
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
    if (failed === 0) safeStorageRemove(PROFILES_STORAGE_KEY);
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
  loadFamilyOverview();
  loadHomeGlance();
}

function readStoredProfile() {
  try {
    return safeSessionGet(PROFILE_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function setActiveProfile(name) {
  activeProfileName = profilesCache[name] ? name : '';
  if (els.profileSelect) els.profileSelect.value = activeProfileName;
  try {
    if (activeProfileName) {
      safeSessionSet(PROFILE_STORAGE_KEY, activeProfileName);
    } else {
      safeSessionRemove(PROFILE_STORAGE_KEY);
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
    loadFamilyOverview();
    loadHomeGlance();
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
    // 家庭概览缓存仍保留旧档案名，重命名后立即刷新
    await loadFamilyOverview();
    loadHomeGlance();
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
    const wasSelected = els.profileSelect.value === name;
    delete profilesCache[name];
    refreshProfileOptions('');
    loadFamilyOverview();
    loadHomeGlance();
    if (wasSelected) applyPatientContext({});
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
    } else {
      applyPatientContext({});
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
  } else {
    applyPatientContext({});
  }
  loadHomeGlance();
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
        safeSessionSet(DRAFT_KEY, els.input.value);
      } else {
        safeSessionRemove(DRAFT_KEY);
      }
    } catch {
      // 隐私模式下 sessionStorage 不可用时忽略
    }
  }, 200);
}

function clearDraft() {
  clearTimeout(draftTimer);
  try {
    safeSessionRemove(DRAFT_KEY);
  } catch {
    // 忽略
  }
}

function loadDraft() {
  try {
    const draft = safeSessionGet(DRAFT_KEY);
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
els.calcEgfrLab.addEventListener('click', prefillEgfrFromLab);
els.calcFib4Lab.addEventListener('click', prefillFib4FromLab);
els.calcMeldLab.addEventListener('click', prefillMeldFromLab);
els.calcApriLab.addEventListener('click', prefillApriFromLab);
els.calcEagLab.addEventListener('click', prefillEagFromLab);
els.calcFormChads.addEventListener('submit', submitChads);
els.calcFormCurb65.addEventListener('submit', submitCurb65);
els.calcCurbLab.addEventListener('click', prefillCurb65FromLab);
els.calcFormChildPugh.addEventListener('submit', submitChildPugh);
els.calcChildPughLab.addEventListener('click', prefillChildPughFromLab);
els.calcFormPhq9.addEventListener('submit', submitPhq9);
els.calcFormAuditc.addEventListener('submit', submitAuditC);
els.calcFormMmrc.addEventListener('submit', submitMmrc);
els.calcFormCat.addEventListener('submit', submitCat);
els.calcFormEss.addEventListener('submit', submitEss);
els.calcFormGad7.addEventListener('submit', submitGad7);
els.calcFormHasBled.addEventListener('submit', submitHasBled);
els.calcFormGcs.addEventListener('submit', submitGcs);
els.calcFormWells.addEventListener('submit', submitWells);
els.calcFormWellsPe.addEventListener('submit', submitWellsPe);
els.calcFormInteractions.addEventListener('submit', submitInteractions);
els.calcFormQsofa.addEventListener('submit', submitQsofa);
els.calcFormFib4.addEventListener('submit', submitFib4);
els.calcFormMeld.addEventListener('submit', submitMeld);
els.calcFormApri.addEventListener('submit', submitApri);
els.calcFormEag.addEventListener('submit', submitEag);
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
els.healthSearchToggle.addEventListener('click', openHealthSearch);
els.healthSearchInput.addEventListener('input', (event) => {
  if (event.isComposing) return;
  if (healthSearchTimer) clearTimeout(healthSearchTimer);
  healthSearchTimer = setTimeout(() => {
    healthSearchTimer = null;
    runHealthSearch(els.healthSearchInput.value);
  }, 250);
});
els.healthSearchInput.addEventListener('keydown', (event) => {
  if (event.isComposing || event.key !== 'Enter') return;
  event.preventDefault();
  if (healthSearchTimer) {
    clearTimeout(healthSearchTimer);
    healthSearchTimer = null;
  }
  runHealthSearch(els.healthSearchInput.value);
});
els.healthSearchClear.addEventListener('click', () => {
  if (healthSearchTimer) {
    clearTimeout(healthSearchTimer);
    healthSearchTimer = null;
  }
  els.healthSearchInput.value = '';
  els.healthSearchClear.hidden = true;
  hideHealthSearchResults();
  els.healthSearchInput.focus();
});
function setEmergencyCardStatus(text, isError = false) {
  if (!els.emergencyCardStatus) return;
  els.emergencyCardStatus.textContent = text || '';
  els.emergencyCardStatus.hidden = !text;
  els.emergencyCardStatus.classList.toggle('error', Boolean(isError));
}

async function openEmergencyCard() {
  const profile = els.metricProfile ? els.metricProfile.value : '';
  if (!profile) {
    setEmergencyCardStatus('请先选择患者档案', true);
    return;
  }
  setEmergencyCardStatus('正在生成…');
  try {
    const res = await fetch(
      `/api/health/emergency-card?profile=${encodeURIComponent(profile)}`
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `服务返回 ${res.status}`);
    }
    const html = await res.text();
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const opened = window.open(url, '_blank');
    if (!opened) {
      URL.revokeObjectURL(url);
      setEmergencyCardStatus('浏览器拦截了新窗口，请允许弹窗后重试', true);
      return;
    }
    try {
      opened.opener = null;
    } catch {
      // 新窗口不可写时忽略
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    setEmergencyCardStatus('已生成急救卡');
  } catch (err) {
    setEmergencyCardStatus(`生成失败：${err.message || err}`, true);
  }
}

if (els.healthDossierExport) {
  els.healthDossierExport.addEventListener('click', exportHealthDossier);
}
if (els.emergencyCard) {
  els.emergencyCard.addEventListener('click', openEmergencyCard);
}
els.metricsBackdrop.addEventListener('click', () => closeMetrics(true));
els.remindersOpen.addEventListener('click', openReminders);
els.remindersClose.addEventListener('click', () => closeReminders(true));
els.remindersBackdrop.addEventListener('click', () => closeReminders(true));
els.remindersNotify.addEventListener('click', enableReminderNotifications);
els.remindersClearDone.addEventListener('click', clearDoneReminders);
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
els.remindersSearch.addEventListener('input', () => {
  clearTimeout(remindersSearchTimer);
  remindersSearchTimer = setTimeout(refreshReminderViews, 200);
});
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
els.reminderDueQuick.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-days]');
  if (!button) return;
  const days = Number(button.dataset.days) || 0;
  els.reminderDue.value = days === 30
    ? addMonthsToDate(todayLocalDate(), 1)
    : localDateAfter(days);
});
els.remindersShowDone.addEventListener('change', loadReminders);
els.remindersProfile.addEventListener('change', loadReminders);
if (els.reminderStatsRange) {
  els.reminderStatsRange.addEventListener('change', () => {
    storeFilter(
      HEALTH_FILTER_KEYS.reminderStatsRange, els.reminderStatsRange.value
    );
    loadReminderStats();
  });
}
els.reminderCancel.addEventListener('click', () => {
  resetReminderForm();
  setRemindersStatus('已取消编辑。');
});
els.reminderTitle.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    addReminder();
  }
});
els.reminderNote.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    addReminder();
  }
});
for (const input of [
  els.reminderDue,
  els.reminderAdvance,
  els.reminderRepeat,
  els.reminderUntil,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      addReminder();
    }
  });
}
els.metricType.addEventListener('change', () => {
  storeFilter(HEALTH_FILTER_KEYS.metricType, els.metricType.value);
  setMetricType(els.metricType.value);
  refreshMetrics();
});
els.metricProfile.addEventListener('change', () => {
  refreshMetrics();
  loadDataQuality();
  loadBpMonitoring();
  loadAttachments();
  loadAttachmentRefOptions();
  resetVisitPrep();
  quickEntryRequestSeq += 1;
  clearQuickEntryPreview();
  setQuickEntryStatus('');
  resetQuickEntryUndo();
});
els.metricProfile.addEventListener('change', () => {
  if (healthSearchQuery && els.healthSearchInput) {
    runHealthSearch(els.healthSearchInput.value);
  }
});
els.metricRange.addEventListener('change', () => {
  storeFilter(HEALTH_FILTER_KEYS.metricRange, els.metricRange.value);
  refreshMetrics();
});
if (els.quickEntryParse) {
  els.quickEntryParse.addEventListener('click', parseQuickEntry);
  els.quickEntryInput.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      parseQuickEntry();
    }
  });
  els.quickEntryInput.addEventListener('input', () => {
    quickEntryRequestSeq += 1;
    clearQuickEntryPreview();
    setQuickEntryStatus('');
  });
  els.quickEntrySave.addEventListener('click', saveQuickEntry);
  els.quickEntryCancel.addEventListener('click', () => {
    quickEntryRequestSeq += 1;
    clearQuickEntryPreview();
    setQuickEntryStatus('');
  });
  els.quickEntryUndo.addEventListener('click', undoQuickEntry);
}
if (els.bpMonitoringDays) {
  els.bpMonitoringDays.addEventListener('change', () => {
    bpMonitoringStartAuto = true;
    els.bpMonitoringStart.value = bpMonitoringDefaultStart(
      els.bpMonitoringDays.value
    );
    loadBpMonitoring();
  });
  els.bpMonitoringStart.addEventListener('change', () => {
    bpMonitoringStartAuto = false;
    loadBpMonitoring();
  });
  els.bpMonitoringCopy.addEventListener(
    'click', copyBpMonitoring
  );
}
if (els.attachmentUpload) {
  els.attachmentUpload.addEventListener('click', uploadAttachment);
  if (els.attachmentIntegrityCheck) {
    els.attachmentIntegrityCheck.addEventListener(
      'click', () => checkAttachmentIntegrity()
    );
    els.attachmentCleanup.addEventListener(
      'click', cleanupAttachmentOrphans
    );
    els.attachmentExport.addEventListener(
      'click', exportAttachmentArchive
    );
  }
  if (els.attachmentKind) {
    els.attachmentKind.addEventListener(
      'change', loadAttachmentRefOptions
    );
  }
  if (els.attachmentDropzone) {
    for (const type of ['dragenter', 'dragover']) {
      els.attachmentDropzone.addEventListener(type, (event) => {
        event.preventDefault();
        setAttachmentDropActive(true);
      });
    }
    for (const type of ['dragleave', 'dragend']) {
      els.attachmentDropzone.addEventListener(
        type, () => setAttachmentDropActive(false)
      );
    }
    els.attachmentDropzone.addEventListener(
      'drop', handleAttachmentDrop
    );
    els.attachmentDropzone.addEventListener('click', () => {
      if (els.attachmentFile) els.attachmentFile.click();
    });
    els.attachmentDropzone.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (els.attachmentFile) els.attachmentFile.click();
      }
    });
  }
  document.addEventListener('paste', handleAttachmentPaste);
  if (els.attachmentFilterKind) {
    els.attachmentFilterKind.addEventListener(
      'change', () => {
        if (attachmentRefFilter) setAttachmentRefFilter(null);
        loadAttachments();
      }
    );
  }
  if (els.attachmentRefFilterClear) {
    els.attachmentRefFilterClear.addEventListener(
      'click', clearAttachmentRefFilter
    );
  }
  if (els.attachmentFilterSort) {
    els.attachmentFilterSort.addEventListener(
      'change', () => loadAttachments()
    );
  }
  if (els.attachmentFilterLinked) {
    els.attachmentFilterLinked.addEventListener(
      'change', () => loadAttachments()
    );
  }
  if (els.attachmentFilterStart) {
    els.attachmentFilterStart.addEventListener(
      'change', onAttachmentDateChange
    );
  }
  if (els.attachmentFilterEnd) {
    els.attachmentFilterEnd.addEventListener(
      'change', onAttachmentDateChange
    );
  }
  if (els.attachmentFilterQ) {
    els.attachmentFilterQ.addEventListener('input', (event) => {
      if (event.isComposing) return;
      scheduleAttachmentFilterSearch();
    });
    els.attachmentFilterQ.addEventListener(
      'compositionend', scheduleAttachmentFilterSearch
    );
  }
  if (els.attachmentMore) {
    els.attachmentMore.addEventListener(
      'click', () => loadAttachments(true)
    );
  }
  if (els.attachmentSelectAll) {
    els.attachmentSelectAll.addEventListener('change', () => {
      const checked = els.attachmentSelectAll.checked;
      for (const item of attachmentItemsCache) {
        if (checked) {
          attachmentSelectedIds.add(item.id);
        } else {
          attachmentSelectedIds.delete(item.id);
        }
      }
      for (const input of els.attachmentList.querySelectorAll(
        '.attachment-select'
      )) {
        input.checked = checked;
      }
      updateAttachmentSelection();
    });
  }
  if (els.attachmentLinkSelected) {
    els.attachmentLinkSelected.addEventListener(
      'click', openAttachmentLinkBar
    );
  }
  if (els.attachmentLinkApply) {
    els.attachmentLinkApply.addEventListener(
      'click', applyAttachmentLink
    );
  }
  if (els.attachmentLinkUnlink) {
    els.attachmentLinkUnlink.addEventListener(
      'click', unlinkSelectedAttachments
    );
  }
  if (els.attachmentLinkCancel) {
    els.attachmentLinkCancel.addEventListener(
      'click', closeAttachmentLinkBar
    );
  }
  if (els.attachmentPreview) {
    els.attachmentPreview.addEventListener('click', (event) => {
      if (event.target === els.attachmentPreview) {
        closeAttachmentPreview();
      }
    });
    if (els.attachmentPreviewClose) {
      els.attachmentPreviewClose.addEventListener(
        'click', closeAttachmentPreview
      );
    }
    if (els.attachmentPreviewDownload) {
      els.attachmentPreviewDownload.addEventListener(
        'click', downloadPreviewAttachment
      );
    }
    document.addEventListener('keydown', (event) => {
      if (
        event.key === 'Escape'
        && els.attachmentPreview
        && !els.attachmentPreview.hidden
      ) {
        closeAttachmentPreview();
      }
    });
  }
  if (els.attachmentExportSelected) {
    els.attachmentExportSelected.addEventListener(
      'click', exportSelectedAttachments
    );
  }
  if (els.attachmentDeleteSelected) {
    els.attachmentDeleteSelected.addEventListener(
      'click', deleteSelectedAttachments
    );
  }
}
restoreSelectValue(
  els.weeklySummaryRange, HEALTH_FILTER_KEYS.weeklyRange
);
restoreSelectValue(els.metricRange, HEALTH_FILTER_KEYS.metricRange);
restoreSelectValue(els.metricType, HEALTH_FILTER_KEYS.metricType);
restoreSelectValue(els.compareMetric, HEALTH_FILTER_KEYS.compareMetric);
restoreSelectValue(els.compareRange, HEALTH_FILTER_KEYS.compareRange);
restoreSelectValue(
  els.medicationRange, HEALTH_FILTER_KEYS.medicationRange
);
restoreSelectValue(els.timelineRange, HEALTH_FILTER_KEYS.timelineRange);
restoreSelectValue(els.timelineType, HEALTH_FILTER_KEYS.timelineType);
restoreSelectValue(
  els.reminderStatsRange, HEALTH_FILTER_KEYS.reminderStatsRange
);

els.weeklySummaryRange.addEventListener('change', () => {
  storeFilter(HEALTH_FILTER_KEYS.weeklyRange, els.weeklySummaryRange.value);
  loadWeeklySummary();
});
els.weeklySummaryCopy.addEventListener('click', copyWeeklySummary);
if (els.weeklySummaryExport) {
  els.weeklySummaryExport.addEventListener('click', exportWeeklySummary);
}
if (els.visitPrepGenerate) {
  els.visitPrepGenerate.addEventListener('click', loadVisitPrep);
  els.visitPrepCopy.addEventListener('click', copyVisitPrep);
  els.visitPrepExport.addEventListener('click', exportVisitPrep);
  els.visitPrepPrint.addEventListener('click', printVisitPrep);
  els.visitPrepDays.addEventListener('change', resetVisitPrep);
}
els.timelineRange.addEventListener('change', () => {
  timelineDate = '';
  storeFilter(HEALTH_FILTER_KEYS.timelineRange, els.timelineRange.value);
  loadTimeline();
});
if (els.calendarPrev) {
  els.calendarPrev.addEventListener('click', () => {
    calendarMonth = shiftMonthKey(
      calendarMonth || currentMonthKey(), -1
    );
    loadCalendar();
  });
}
if (els.calendarNext) {
  els.calendarNext.addEventListener('click', () => {
    calendarMonth = shiftMonthKey(
      calendarMonth || currentMonthKey(), 1
    );
    loadCalendar();
  });
}
if (els.calendarToday) {
  els.calendarToday.addEventListener('click', () => {
    calendarMonth = '';
    loadCalendar();
  });
}
if (els.calendarExport) {
  els.calendarExport.addEventListener('click', exportCalendarCsv);
}
if (els.compareMetric) {
  els.compareMetric.addEventListener('change', () => {
    storeFilter(HEALTH_FILTER_KEYS.compareMetric, els.compareMetric.value);
    loadFamilyCompare();
  });
}
if (els.compareRange) {
  els.compareRange.addEventListener('change', () => {
    storeFilter(HEALTH_FILTER_KEYS.compareRange, els.compareRange.value);
    loadFamilyCompare();
  });
}
if (els.compareExport) {
  els.compareExport.addEventListener('click', exportFamilyCompare);
}
if (els.timelineDateChip) {
  els.timelineDateChip.addEventListener('click', () => {
    timelineDate = '';
    loadTimeline();
  });
}
els.timelineType.addEventListener('change', () => {
  storeFilter(HEALTH_FILTER_KEYS.timelineType, els.timelineType.value);
  loadTimeline();
});
if (els.timelineSearch) {
  let timelineSearchTimer = null;
  els.timelineSearch.addEventListener('input', () => {
    clearTimeout(timelineSearchTimer);
    timelineSearchTimer = setTimeout(loadTimeline, 250);
  });
}
if (els.timelineOnlyAlert) {
  els.timelineOnlyAlert.addEventListener('change', () => {
    loadTimeline();
  });
}
if (els.timelineRiskOnly) {
  els.timelineRiskOnly.addEventListener('change', () => {
    loadTimeline();
  });
}
if (els.timelineMore) {
  els.timelineMore.addEventListener('click', () => {
    if (timelineLimit >= 2000) return;
    timelineLimit = Math.min(timelineLimit + 300, 2000);
    loadTimeline();
  });
}
els.timelineExport.addEventListener('click', exportTimelineCsv);
els.metricTargetEdit.addEventListener('click', startEditMetricTarget);
els.metricTargetSave.addEventListener('click', saveMetricTarget);
els.metricTargetCancel.addEventListener('click', updateMetricTargetDisplay);
els.metricTargetClear.addEventListener('click', clearMetricTarget);
els.metricTargetValue1.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    saveMetricTarget();
  }
});
els.metricTargetValue2.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    saveMetricTarget();
  }
});
els.metricAdd.addEventListener('click', addMetricRecord);
els.symptomSave.addEventListener('click', saveSymptom);
if (els.symptomQuick) {
  els.symptomQuick.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-symptom]');
    if (!button) return;
    const symptom = button.dataset.symptom || '';
    if (!symptom) return;
    els.symptomText.value = symptom;
    els.symptomText.focus();
  });
}
els.medicationSave.addEventListener('click', saveMedication);
els.medicationExport.addEventListener('click', exportMedicationsCsv);
els.medicationLogsExport.addEventListener('click', exportMedicationLogsCsv);
els.medicationReminders.addEventListener(
  'click', scheduleAllMedicationReminders
);
els.medicationImport.addEventListener('click', () => {
  els.medicationImportFile.click();
});
els.medicationImportFile.addEventListener('change', () => {
  const file = els.medicationImportFile.files
    && els.medicationImportFile.files[0];
  if (file) importMedicationsCsv(file);
});
els.medicationCancel.addEventListener('click', () => {
  resetMedicationForm();
  setMedicationStatus('已取消编辑。');
});
els.calcInteractionsLoad.addEventListener(
  'click', loadMedicationsIntoInteractions
);
for (const input of [
  els.medicationName, els.medicationDose,
  els.medicationFrequency, els.medicationNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveMedication();
    }
  });
}
els.symptomExport.addEventListener('click', exportSymptomsCsv);
els.symptomImport.addEventListener('click', () => {
  els.symptomImportFile.click();
});
els.symptomImportFile.addEventListener('change', () => {
  const file = els.symptomImportFile.files
    && els.symptomImportFile.files[0];
  if (file) importSymptomsCsv(file);
});
els.symptomSearch.addEventListener('input', () => {
  clearTimeout(symptomSearchTimer);
  symptomSearchTimer = setTimeout(() => {
    renderSymptoms(filterSymptoms(symptomItemsCache));
  }, 200);
});
els.vaccinationSave.addEventListener('click', saveVaccination);
els.vaccinationExport.addEventListener('click', exportVaccinationsCsv);
els.vaccinationCancel.addEventListener('click', () => {
  resetVaccinationForm();
  setVaccinationStatus('已取消编辑。');
});
if (els.vaccinationSection) {
  els.vaccinationSection.addEventListener('toggle', () => {
    if (els.vaccinationSection.open && !els.vaccinationDate.value) {
      els.vaccinationDate.value = todayLocalDate();
    }
  });
}
for (const input of [
  els.vaccinationName, els.vaccinationDose,
  els.vaccinationDate, els.vaccinationNextDue, els.vaccinationNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveVaccination();
    }
  });
}
els.vaccinationSearch.addEventListener('input', () => {
  clearTimeout(vaccinationSearchTimer);
  vaccinationSearchTimer = setTimeout(() => {
    renderVaccinations(filterVaccinations(vaccinationItemsCache));
  }, 200);
});
els.appointmentSave.addEventListener('click', saveAppointment);
els.appointmentExport.addEventListener('click', exportAppointmentsCsv);
els.appointmentCancel.addEventListener('click', () => {
  resetAppointmentForm();
  setAppointmentStatusText('已取消编辑。');
});
if (els.appointmentSection) {
  els.appointmentSection.addEventListener('toggle', () => {
    if (els.appointmentSection.open && !els.appointmentTime.value) {
      els.appointmentTime.value = metricNowLocal();
    }
  });
}
for (const input of [
  els.appointmentTitle, els.appointmentDepartment,
  els.appointmentDoctor, els.appointmentLocation,
  els.appointmentTime, els.appointmentNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveAppointment();
    }
  });
}
els.appointmentSearch.addEventListener('input', () => {
  clearTimeout(appointmentSearchTimer);
  appointmentSearchTimer = setTimeout(() => {
    renderAppointments(filterAppointments(appointmentItemsCache));
  }, 200);
});
els.labSave.addEventListener('click', saveLab);
els.labExport.addEventListener('click', exportLabsCsv);
els.labImport.addEventListener('click', () => {
  els.labImportFile.click();
});
els.labImportFile.addEventListener('change', () => {
  const file = els.labImportFile.files && els.labImportFile.files[0];
  if (file) importLabsCsv(file);
});
els.labTemplate.addEventListener('click', downloadLabTemplate);
els.labCancel.addEventListener('click', () => {
  resetLabForm();
  setLabStatus('已取消编辑。');
});
if (els.labSection) {
  els.labSection.addEventListener('toggle', () => {
    if (els.labSection.open && !els.labTime.value) {
      els.labTime.value = metricNowLocal();
    }
  });
}
for (const input of [
  els.labItem, els.labPanel, els.labValue, els.labUnit,
  els.labRefLow, els.labRefHigh, els.labTime,
  els.labName, els.labNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveLab();
    }
  });
}
els.labSearch.addEventListener('input', () => {
  clearTimeout(labSearchTimer);
  labSearchTimer = setTimeout(() => {
    renderLabs(filterLabs(labItemsCache));
  }, 200);
});
els.allergySave.addEventListener('click', saveAllergy);
els.allergyExport.addEventListener('click', exportAllergiesCsv);
els.allergyImport.addEventListener('click', () => {
  els.allergyImportFile.click();
});
els.allergyImportFile.addEventListener('change', () => {
  const file = els.allergyImportFile.files
    && els.allergyImportFile.files[0];
  if (file) importAllergiesCsv(file);
});
els.allergyTemplate.addEventListener('click', downloadAllergyTemplate);
els.allergyCancel.addEventListener('click', () => {
  resetAllergyForm();
  setAllergyStatus('已取消编辑。');
});
for (const input of [
  els.allergySubstance, els.allergyReaction, els.allergyNotedAt,
  els.allergyNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveAllergy();
    }
  });
}
els.allergySearch.addEventListener('input', () => {
  clearTimeout(allergySearchTimer);
  allergySearchTimer = setTimeout(() => {
    renderAllergies(filterAllergies(allergyItemsCache));
  }, 200);
});
els.careSave.addEventListener('click', saveCareItem);
els.careExport.addEventListener('click', exportCareItemsCsv);
els.careCancel.addEventListener('click', () => {
  resetCareForm();
  setCareStatus('已取消编辑。');
});
for (const input of [
  els.careTitle, els.careInterval, els.careNextDue,
  els.careLastDone, els.careNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveCareItem();
    }
  });
}
els.careSearch.addEventListener('input', () => {
  clearTimeout(careSearchTimer);
  careSearchTimer = setTimeout(() => {
    renderCareItems(filterCareItems(careItemsCache));
  }, 200);
});
els.visitSave.addEventListener('click', saveVisit);
els.visitExport.addEventListener('click', exportVisitsCsv);
els.visitCancel.addEventListener('click', () => {
  resetVisitForm();
  setVisitStatus('已取消编辑。');
});
for (const input of [
  els.visitDate, els.visitDepartment, els.visitDoctor,
  els.visitFacility, els.visitDiagnosis, els.visitAdvice,
  els.visitPrescription, els.visitNextFollowup, els.visitNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveVisit();
    }
  });
}
els.visitSearch.addEventListener('input', () => {
  clearTimeout(visitSearchTimer);
  visitSearchTimer = setTimeout(() => {
    renderVisits(filterVisits(visitItemsCache));
  }, 200);
});
els.conditionSave.addEventListener('click', saveCondition);
els.conditionExport.addEventListener('click', exportConditionsCsv);
els.conditionCancel.addEventListener('click', () => {
  resetConditionForm();
  setConditionStatus('已取消编辑。');
});
for (const input of [
  els.conditionName, els.conditionIcd10, els.conditionDiagnosedAt,
  els.conditionNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveCondition();
    }
  });
}
els.conditionSearch.addEventListener('input', () => {
  clearTimeout(conditionSearchTimer);
  conditionSearchTimer = setTimeout(() => {
    renderConditions(filterConditions(conditionItemsCache));
  }, 200);
});
els.medicationSearch.addEventListener('input', () => {
  clearTimeout(medicationSearchTimer);
  medicationSearchTimer = setTimeout(() => {
    renderMedications(filterMedications(medicationItemsCache));
  }, 200);
});
els.medicationStatusFilter.addEventListener('change', () => {
  renderMedications(filterMedications(medicationItemsCache));
});
if (els.medicationRange) {
  els.medicationRange.addEventListener('change', () => {
    storeFilter(
      HEALTH_FILTER_KEYS.medicationRange, els.medicationRange.value
    );
    loadMedications();
  });
}
els.symptomCancel.addEventListener('click', () => {
  resetSymptomForm();
  setSymptomStatus('已取消编辑。');
});
for (const input of [els.symptomText, els.symptomNote]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      saveSymptom();
    }
  });
}
for (const input of [
  els.metricSystolic,
  els.metricDiastolic,
  els.metricValue,
  els.metricTime,
  els.metricNote,
]) {
  if (!input) continue;
  input.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      addMetricRecord();
    }
  });
}
els.homeGlanceReminders.addEventListener('click', () => {
  openReminders();
});
if (els.homeGlanceToday) {
  els.homeGlanceToday.addEventListener('click', () => {
    openReminders();
  });
}
els.homeGlanceMetrics.addEventListener('click', () => {
  openMetrics();
});
els.homeGlanceSymptoms.addEventListener('click', () => {
  openMetrics();
  if (els.symptomSection) els.symptomSection.open = true;
});
els.homeGlanceMedications.addEventListener('click', () => {
  openMetrics();
  if (els.medicationSection) els.medicationSection.open = true;
});
els.homeGlanceSummary.addEventListener('click', () => {
  openMetrics();
  if (els.weeklySummarySection) els.weeklySummarySection.open = true;
});
if (els.homeGlanceInsight) {
  els.homeGlanceInsight.addEventListener('click', async () => {
    const profile = els.profileSelect ? els.profileSelect.value : '';
    if (!profile) {
      const overview = document.getElementById('family-overview');
      const thread = document.getElementById('thread');
      if (overview && !overview.hidden && thread) {
        const top = overview.getBoundingClientRect().top
          - thread.getBoundingClientRect().top
          + thread.scrollTop - 60;
        thread.scrollTo({ top, behavior: 'smooth' });
        return;
      }
    }
    if (els.metricProfile && profile) {
      els.metricProfile.value = profile;
    }
    await openMetrics();
    if (els.insightSection) {
      els.insightSection.open = true;
      els.insightSection.scrollIntoView({ block: 'start' });
    }
  });
}
if (els.homeGlanceBackup) {
  els.homeGlanceBackup.addEventListener('click', async () => {
    await exportHistoryBackup();
    loadHomeGlance();
  });
}
els.familyOverviewList.addEventListener('click', async (event) => {
  const card = event.target.closest('.family-card');
  if (!card || card.disabled) return;
  const name = card.dataset.profile || '';
  if (!name || !profilesCache[name]) return;
  if (els.profileSelect.value === name) {
    setFamilyOverviewStatus(`当前档案：${name}`);
    return;
  }
  els.profileSelect.value = name;
  await applySelectedProfile();
  if (els.profileSelect.value !== name) {
    setFamilyOverviewStatus('');
    return;
  }
  setFamilyOverviewStatus(`已切换到档案「${name}」`);
  refreshOverview();
});
if (els.familyOverviewExport) {
  els.familyOverviewExport.addEventListener(
    'click', exportFamilyOverviewCsv
  );
}
if (els.familyOverviewCopy) {
  els.familyOverviewCopy.addEventListener('click', copyFamilyOverview);
}
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
if (els.metricSearch) {
  let metricSearchTimer = 0;
  els.metricSearch.addEventListener('input', () => {
    clearTimeout(metricSearchTimer);
    metricSearchTimer = setTimeout(() => {
      loadMetrics();
    }, 250);
  });
}

els.authForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = els.authToken.value.trim();
  if (!token) return;

  safeSessionSet('medix.token', token);
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

  safeSessionRemove('medix.token');
  showAuthGate('令牌不正确，请重试。');
  els.authToken.select();
});

els.authToken.addEventListener('input', () => {
  els.authError.hidden = true;
  els.authError.textContent = '';
  els.authToken.setAttribute('aria-invalid', 'false');
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
  // 同时清除 activeProfileName 与 sessionStorage，避免刷新后旧档案被重新选中
  setActiveProfile('');
  els.ctxAge.focus();
});

els.profileSelect.addEventListener('change', applySelectedProfile);
els.profileSave.addEventListener('click', savePatientProfile);
els.profileRename.addEventListener('click', renamePatientProfile);
els.profileDelete.addEventListener('click', deletePatientProfile);

els.ctxSingle.addEventListener('change', () => {
  try {
    if (els.ctxSingle.checked) {
      safeSessionSet(SINGLE_MODE_KEY, '1');
    } else {
      safeSessionRemove(SINGLE_MODE_KEY);
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
els.historyRestore?.addEventListener('click', restoreBackup);
els.historyBackupNow?.addEventListener('click', createLocalBackup);
els.historyManageBackups?.addEventListener('click', manageLocalBackups);

// 导入模板下载：列名与服务端导入器保持一致
els.metricTemplate?.addEventListener('click', () => {
  downloadCsvTemplate('metrics');
});
els.symptomTemplate?.addEventListener('click', () => {
  downloadCsvTemplate('symptoms');
});
els.medicationTemplate?.addEventListener('click', () => {
  downloadCsvTemplate('medications');
});
els.medicationLogAll?.addEventListener('click', logAllMedications);
els.remindersTemplate?.addEventListener('click', () => {
  downloadCsvTemplate('reminders');
});
els.remindersCompleteDue?.addEventListener('click', completeDueReminders);

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

if (els.historyRiskFilter) {
  els.historyRiskFilter.addEventListener('change', () => {
    historyRisk = els.historyRiskFilter.value;
    loadHistory(els.historySearch.value.trim());
  });
}

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
  if (
    (e.ctrlKey || e.metaKey)
    && e.shiftKey
    && e.key.toLowerCase() === 'k'
  ) {
    e.preventDefault();
    if (authGateOpen) {
      els.authToken.focus();
      return;
    }
    if (!metricsOpen) {
      openHealthSearch();
    } else if (els.healthSearchInput) {
      els.healthSearchInput.focus();
    }
    return;
  }

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
window.addEventListener('scroll', () => {
  if (isStacked() && isWindowNearBottom()) {
    els.jumpLatest.hidden = true;
    userPausedFollow = false;
  }
}, { passive: true });
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
      safeStorageGet(CUSTOM_STARTERS_KEY) || '[]'
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
    safeStorageSet(
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
let installPromptUsed = false;

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  installPromptUsed = false;
  els.pwaInstall.hidden = false;
});

window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  installPromptUsed = false;
  els.pwaInstall.hidden = true;
});

function updateConnectivity() {
  if (navigator.onLine === false) {
    setVitals('warn', '离线');
    showBanner(OFFLINE_BANNER);
  } else {
    hideBanner();
    checkStatus();
  }
}

window.addEventListener('offline', updateConnectivity);
window.addEventListener('online', updateConnectivity);
if (navigator.onLine === false) updateConnectivity();

els.pwaInstall.addEventListener('click', async () => {
  if (!deferredInstallPrompt) {
    showBanner('如需安装，请刷新页面后再次点击“安装应用”。');
    return;
  }
  if (installPromptUsed) {
    showBanner('安装提示已使用过；如需重试，请刷新页面后再点击“安装应用”。');
    return;
  }
  installPromptUsed = true;
  const promptEvent = deferredInstallPrompt;
  try {
    promptEvent.prompt();
    const choice = await promptEvent.userChoice;
    if (choice && choice.outcome === 'accepted') {
      deferredInstallPrompt = null;
      els.pwaInstall.hidden = true;
    } else {
      // 用户取消：保留安装入口并提示重试方式，而不是永久隐藏
      deferredInstallPrompt = null;
      showBanner('已取消安装。如需重试，请刷新页面后再次点击“安装应用”。');
    }
  } catch {
    deferredInstallPrompt = null;
    showBanner('当前浏览器无法弹出安装提示，请刷新页面后重试。');
  }
});

function describeServiceWorker() {
  if (!('serviceWorker' in navigator)) {
    return { text: '浏览器不支持', ok: false };
  }
  if (!window.isSecureContext) {
    return { text: '不可用（需要 HTTPS 或 localhost）', ok: false };
  }
  if (navigator.serviceWorker.controller) {
    return { text: '已就绪，可离线打开静态页面', ok: true };
  }
  if (swRegistration) {
    return { text: '已注册，等待激活', ok: true };
  }
  return { text: '未注册或注册失败', ok: false };
}

function showUpdateBanner() {
  swUpdateReady = true;
  if (document.getElementById('sw-refresh')) {
    els.banner.hidden = false;
    return;
  }
  showBanner(
    '新版本已就绪。为避免影响正在编辑的内容，请在合适时刷新页面。'
    + ' <button type="button" class="btn-quiet" id="sw-refresh">刷新页面</button>'
  );
  const button = document.getElementById('sw-refresh');
  if (button) {
    button.addEventListener('click', () => window.location.reload());
  }
}

if ('serviceWorker' in navigator) {
  swHadController = Boolean(navigator.serviceWorker.controller);

  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // 首次安装也会触发 controllerchange，只有替换旧外壳时才提示刷新
    if (swHadController) showUpdateBanner();
    swHadController = Boolean(navigator.serviceWorker.controller);
  });

  let swRegisterAttempts = 0;
  const registerServiceWorker = () => {
    if (!window.isSecureContext) return;
    navigator.serviceWorker.register('/sw.js')
      .then((registration) => {
        swRegistration = registration;
        swRegisterAttempts = 0;
      })
      .catch(() => {
        swRegistration = null;
        swRegisterAttempts += 1;
        if (swRegisterAttempts <= 3) {
          setTimeout(registerServiceWorker, 10000 * swRegisterAttempts);
        }
      });
  };

  window.addEventListener('load', registerServiceWorker);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      if (swRegistration) {
        swRegistration.update().catch(() => {});
      } else {
        registerServiceWorker();
      }
    }
  });
}

// 屏宽变化后重新排布波形（两份波形共用同一段路径，拼接处才能无缝衔接）
let ecgResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(ecgResizeTimer);
  ecgResizeTimer = setTimeout(renderEcg, 180);
});
