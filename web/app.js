/* ============================================================
   MediX 前端逻辑
   - 通过 /api/chat/stream 读取 SSE，边跑边点亮会诊科室
   - 把 LLM 返回的 【小节】 文本渲染成分节的会诊意见书
   ============================================================ */

const els = {
  thread: document.getElementById('thread'),
  empty: document.getElementById('empty-state'),
  starters: document.getElementById('starters'),
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
  historyFavoriteFilter: document.getElementById('history-favorite-filter'),
  historyExport: document.getElementById('history-export'),
  historyImport: document.getElementById('history-import'),
  historyImportFile: document.getElementById('history-import-file'),
  historyMore: document.getElementById('history-more'),
  usageTrend: document.getElementById('usage-trend'),
  usageTrendTotal: document.getElementById('usage-trend-total'),
  usageChart: document.getElementById('usage-chart'),
  usageBars: document.getElementById('usage-bars'),
  usageAxis: document.getElementById('usage-axis'),
  usageTooltip: document.getElementById('usage-tooltip'),
  usageTable: document.getElementById('usage-table'),
  usageTableBody: document.getElementById('usage-table-body'),
  historyEmpty: document.getElementById('history-empty'),
  stop: document.getElementById('stop'),
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
  kbPreview: document.getElementById('kb-preview'),
  kbPreviewTitle: document.getElementById('kb-preview-title'),
  kbPreviewMeta: document.getElementById('kb-preview-meta'),
  kbPreviewBody: document.getElementById('kb-preview-body'),
  kbPreviewClose: document.getElementById('kb-preview-close'),
  diagnose: document.getElementById('diagnose'),
  diagnoseDrawer: document.getElementById('diagnose-drawer'),
  diagnoseBackdrop: document.getElementById('diagnose-backdrop'),
  diagnoseClose: document.getElementById('diagnose-close'),
  diagSummary: document.getElementById('diag-summary'),
  diagEnv: document.getElementById('diag-env'),
  diagLlm: document.getElementById('diag-llm'),
  diagKnowledge: document.getElementById('diag-knowledge'),
  diagPackages: document.getElementById('diag-packages'),
  diagRefresh: document.getElementById('diag-refresh'),
  diagCopy: document.getElementById('diag-copy'),
  diagDownload: document.getElementById('diag-download'),
  diagStatus: document.getElementById('diag-status'),
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

let sessionId = localStorage.getItem('medix.session') || null;
let busy = false;
let activeController = null;
let historyOpen = false;
let kbOpen = false;
let diagOpen = false;
let authGateOpen = false;
let historyQuery = '';
let historyOffset = 0;
let historyTotal = 0;
let historyLoading = false;
let favoritesOnly = false;

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function activeDrawer() {
  if (authGateOpen) return els.authGate;
  if (historyOpen) return els.historyDrawer;
  if (kbOpen) return els.kbDrawer;
  if (diagOpen) return els.diagnoseDrawer;
  return null;
}

function syncPageInert() {
  const shell = document.querySelector('.shell');
  if (!shell) return;
  if (historyOpen || kbOpen || diagOpen || authGateOpen) {
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
      showAuthGate('访问令牌无效或已失效，请重新输入。');
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

function downloadFile(content, filename, mimeType = 'text/markdown;charset=utf-8') {
  const blob = new Blob([content], { type: mimeType });
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
      lines.push(`## 第 ${turn} 轮`, '');
      if (question) {
        lines.push('### 主诉', '', question, '');
      }
      lines.push('### 会诊意见', '', message.content.trim(), '');

      const metadata = message.metadata || {};
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
  return parts.join(' · ');
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

  actions.appendChild(makeAction('导出 Markdown', () => {
    downloadMarkdown(payload, question);
  }));

  actions.appendChild(makeAction('打印 / PDF', () => {
    window.print();
  }));

  if (question) {
    actions.appendChild(makeAction('再次会诊', () => {
      if (busy) return;
      send(question);
    }));
  }

  note.appendChild(actions);

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

/* ---------- 会话 ---------- */

async function ensureSession() {
  if (sessionId) return sessionId;
  const res = await fetch('/api/sessions', { method: 'POST' });
  const data = await res.json();
  sessionId = data.session_id;
  localStorage.setItem('medix.session', sessionId);
  els.readout.session.textContent = shortSession(sessionId);
  return sessionId;
}

async function startNewSession() {
  const res = await fetch('/api/sessions', { method: 'POST' });
  const data = await res.json();
  sessionId = data.session_id;
  localStorage.setItem('medix.session', sessionId);

  els.thread.replaceChildren(els.empty);
  els.empty.hidden = false;
  resetRail();
  closeHistory();
  closeKb();
  closeDiagnose();
  els.input.focus();
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

function renderHistoryItems(sessions, summary, append = false) {
  if (!append) els.historyList.replaceChildren();
  renderHistorySummary(summary);

  if (!sessions.length && !els.historyList.children.length) {
    els.historyEmpty.hidden = false;
    if (favoritesOnly) {
      els.historyEmpty.textContent = '还没有收藏的会诊';
    } else {
      els.historyEmpty.textContent = els.historySearch.value.trim()
        ? '没有匹配的会诊记录'
        : '还没有历史会诊，提交一次主诉后会自动保存。';
    }
    return;
  }
  els.historyEmpty.hidden = true;

  for (const item of sessions) {
    const li = document.createElement('li');
    li.className = 'history-item';
    if (item.id === sessionId) li.dataset.current = '1';

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'history-open';

    const title = document.createElement('strong');
    title.textContent = item.title || '未命名会诊';

    const meta = document.createElement('span');
    meta.className = 'history-meta';
    const metaParts = [
      formatTime(item.updated_at),
      `${item.message_count || 0} 条`,
    ];
    const sessionTokens = Number(item.usage && item.usage.total_tokens || 0);
    if (sessionTokens > 0) {
      metaParts.push(`${sessionTokens.toLocaleString()} tokens`);
    }
    meta.textContent = metaParts.join(' · ');

    const preview = document.createElement('span');
    preview.className = 'history-preview';
    preview.textContent = item.preview || '（暂无答复摘要）';

    open.append(title, meta, preview);
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

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'history-action danger';
    del.textContent = '删除';
    del.title = '删除这次会诊记录';
    del.setAttribute('aria-label', `删除：${item.title || '未命名会诊'}`);
    del.addEventListener('click', () => deleteHistory(item));

    actions.append(favorite, rename, exportBtn, del);
    li.append(open, actions);
    els.historyList.appendChild(li);
  }
}

function renderHistorySummary(summary) {
  if (!summary || !summary.sessions) {
    els.historySummary.hidden = true;
    els.historySummary.textContent = '';
    return;
  }

  const totalTokens = Number(summary.usage && summary.usage.total_tokens || 0);
  const llmCalls = Number(summary.llm_calls || 0);
  const parts = [`共 ${summary.sessions} 次${favoritesOnly ? '收藏' : ''}会诊`];
  if (summary.loaded && summary.loaded < summary.sessions) {
    parts.push(`已加载 ${summary.loaded} 次`);
  }
  if (summary.turns) parts.push(`${summary.turns} 轮`);
  if (totalTokens > 0) parts.push(`${totalTokens.toLocaleString()} tokens`);
  if (llmCalls > 0) parts.push(`${llmCalls} 次调用`);

  els.historySummary.textContent = parts.join(' · ');
  els.historySummary.hidden = false;
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

async function loadHistory(query = '', { append = false } = {}) {  if (!append) {
    historyQuery = query;
    historyOffset = 0;
    historyTotal = 0;
  }

  historyLoading = true;
  updateHistoryMore({ sessions: historyTotal, loaded: historyOffset });

  try {
    const res = await fetch(
      `/api/sessions?limit=50&offset=${historyOffset}&q=${encodeURIComponent(historyQuery)}` +
      (favoritesOnly ? '&favorite=1' : '')
    );
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    const sessions = data.sessions || [];
    const loadedTotal = historyOffset + sessions.length;
    const viewSummary = { ...(data.summary || {}), loaded: loadedTotal };

    renderHistoryItems(sessions, viewSummary, append);
    historyTotal = Number(viewSummary.sessions || 0);
    historyOffset = loadedTotal;
    historyLoading = false;
    updateHistoryMore(viewSummary);
  } catch (err) {
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
  els.usageTrendTotal.textContent = totalTokens
    ? `${totalTokens.toLocaleString()} tokens · ${totalCalls} 次调用`
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
  try {
    const res = await fetch(
      `/api/usage/daily?days=7${favoritesOnly ? '&favorite=1' : ''}`
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
    renderUsageTrend(data);
  } catch {
    els.usageTrend.hidden = true;
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
    if (!count) {
      window.alert('备份文件中没有会话。');
      return;
    }
    if (!window.confirm(`导入 ${count} 个会话？已存在的会话编号会跳过。`)) {
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
        body: JSON.stringify({ version: data.version, sessions: data.sessions }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(result.detail || `服务返回 ${res.status}`);

      btn.textContent = `已导入 ${result.imported || 0} 次`;
      await loadHistory(els.historySearch.value.trim());
      await loadUsageTrend();
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
  loadUsageTrend();
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

function renderStoredMessages(messages) {
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
      renderAnswer(
        {
          answer: message.content,
          mode: metadata.swarm_enabled ? 'swarm' : 'single',
          suggestions: metadata.suggestions || [],
          disclaimer: metadata.disclaimer || '',
          sources: metadata.sources || [],
          agent_contributions: metadata.agent_contributions || [],
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
  }

  if (lastMetadata && lastMetadata.patient_context) {
    applyPatientContext(lastMetadata.patient_context);
  }

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
    closeHistory();
    resetRail();
    els.readout.session.textContent = shortSession(id);
    renderStoredMessages(data.messages || []);
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
  } catch (err) {
    window.alert(`更新收藏失败：${err.message || err}`);
  }
}

async function deleteHistory(item) {
  const title = item.title || '这次会诊';
  if (!window.confirm(`删除「${title}」的会诊记录？删除后不可恢复。`)) return;

  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(item.id)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`删除失败（${res.status}）`);

    if (item.id === sessionId) {
      sessionId = null;
      localStorage.removeItem('medix.session');
      els.thread.replaceChildren(els.empty);
      els.empty.hidden = false;
      resetRail();
      els.readout.session.textContent = '—';
    }
    await loadHistory(els.historySearch.value.trim());
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
  } catch (err) {
    window.alert(err.message || String(err));
  }
}

async function exportHistory(item) {
  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(item.id)}/messages?limit=500`
    );
    if (!res.ok) throw new Error(`读取会诊记录失败（${res.status}）`);
    const data = await res.json();
    const messages = data.messages || [];
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

/* ---------- 知识库管理 ---------- */

let kbFocused = null;

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

function closeKbPreview() {
  els.kbPreview.hidden = true;
  els.kbPreviewTitle.textContent = '文档预览';
  els.kbPreviewMeta.textContent = '';
  els.kbPreviewBody.textContent = '';
}

async function previewKbDocument(filename) {
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
  } catch (err) {
    els.kbPreviewMeta.textContent = `读取失败：${err.message || err}`;
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

async function uploadKbDocument() {
  const file = els.kbFile.files && els.kbFile.files[0];
  if (!file) {
    setKbStatus('请先选择要上传的文件。', true);
    return;
  }

  const form = new FormData();
  form.append('file', file);

  setKbBusy(true);
  setKbStatus('正在上传并重建索引，首次运行可能需要加载模型…');

  try {
    const res = await fetch('/api/knowledge/upload', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `上传失败（${res.status}）`);

    const rebuild = data.rebuild || {};
    setKbStatus(
      `已上传 ${data.document?.filename || file.name}。` +
      `重建完成：${rebuild.documents || 0} 个文档、${rebuild.chunks || 0} 个向量块。`
    );
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
  diagFocused = document.activeElement;
  diagOpen = true;
  els.diagnoseDrawer.hidden = false;
  els.diagnoseBackdrop.hidden = false;
  syncPageInert();
  els.diagnose.setAttribute('aria-expanded', 'true');
  setDiagStatus('');
  loadDiagnostics();
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

async function restoreCurrentSession() {
  if (!sessionId) return;

  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=50`
    );
    if (!res.ok) return;

    const data = await res.json();
    const messages = data.messages || [];
    if (!messages.length) return;

    els.readout.session.textContent = shortSession(sessionId);
    renderStoredMessages(messages);
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
        enable_swarm: true,
        context: readPatientContext(),
      }),
      signal: controller.signal,
    });

    if (!res.ok || !res.body) {
      throw new Error(`服务返回 ${res.status}`);
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
    els.readout.session.textContent = shortSession(finalPayload.session_id || sid);

    logStep(finalPayload.mode === 'swarm' ? '协调科室已汇总各方意见' : '单科直接出具意见', false);
    if (failedCount) {
      logStep(`有 ${failedCount} 个会诊任务未完成`, false);
    }
    setVitals('idle', '系统就绪');

    renderAnswer(finalPayload, finalPayload.total_time || elapsed, question);

    if (historyOpen) {
      loadHistory(els.historySearch.value.trim());
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
      showBanner(`服务初始化失败：${data.startup_error || '未知错误'}`);
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
      showBanner(`知识库加载失败：${data.knowledge_base.error}。检索类问题会返回空结果。`);
    } else if (busy) {
      // 会诊进行中，不要覆盖波形状态
    } else if (data.knowledge_base.status === 'ready') {
      setVitals('idle', '系统就绪');
    } else {
      setVitals('pending', '知识库加载中');
    }

    els.readout.kb.textContent =
      data.knowledge_base.status === 'ready'
        ? '就绪'
        : data.knowledge_base.status === 'loading'
        ? '加载中'
        : '失败';

    if (!els.readout.session.textContent || els.readout.session.textContent === '—') {
      els.readout.session.textContent = shortSession(sessionId);
    }
  } catch (err) {
    setVitals('bad', '无法连接服务');
    showBanner(`无法连接后端服务：${err.message}`);
  }
}

function showBanner(html) {
  els.banner.innerHTML = html;
  els.banner.hidden = false;
}

/* ---------- 结构化补充信息 ---------- */

const CONTEXT_STORAGE_KEY = 'medix.context';

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
  updateContextSummary();
}

function loadSavedContext() {
  try {
    const raw = sessionStorage.getItem(CONTEXT_STORAGE_KEY);
    if (raw) applyPatientContext(JSON.parse(raw));
  } catch {
    // 忽略损坏的缓存
  }
}

/* ---------- 事件绑定 ---------- */

function autoGrow() {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, 180) + 'px';
}

els.input.addEventListener('input', autoGrow);

els.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
});

els.composer.addEventListener('submit', (e) => {
  e.preventDefault();
  const q = els.input.value.trim();
  if (!q) return;
  els.input.value = '';
  autoGrow();
  send(q);
});

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
els.kbUpload.addEventListener('click', uploadKbDocument);
els.kbRebuild.addEventListener('click', rebuildKb);
els.kbPreviewClose.addEventListener('click', closeKbPreview);

els.diagnose.addEventListener('click', openDiagnose);
els.diagnoseClose.addEventListener('click', () => closeDiagnose(true));
els.diagnoseBackdrop.addEventListener('click', () => closeDiagnose(true));
els.diagRefresh.addEventListener('click', loadDiagnostics);
els.diagCopy.addEventListener('click', copyDiagnostics);
els.diagDownload.addEventListener('click', downloadDiagnostics);

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
  els.ctxAge.focus();
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
els.historyImport.addEventListener('click', () => els.historyImportFile.click());
els.historyImportFile.addEventListener('change', importHistoryBackup);

els.historyFavoriteFilter.addEventListener('click', () => {
  favoritesOnly = !favoritesOnly;
  els.historyFavoriteFilter.setAttribute('aria-pressed', String(favoritesOnly));
  els.historyFavoriteFilter.textContent = favoritesOnly ? '★' : '☆';
  loadHistory(els.historySearch.value.trim());
  loadUsageTrend();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (historyOpen) closeHistory(true);
    if (kbOpen) closeKb(true);
    if (diagOpen) closeDiagnose(true);
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

els.starters.addEventListener('click', (e) => {
  const btn = e.target.closest('.starter');
  if (!btn) return;
  els.empty.hidden = true;
  send(btn.dataset.q);
});

resetRail();
initTheme();
renderEcg();
loadSavedContext();
restoreCurrentSession();
checkStatus();
setInterval(checkStatus, 20000);
els.input.focus();

// 屏宽变化后重新排布波形（两份波形共用同一段路径，拼接处才能无缝衔接）
let ecgResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(ecgResizeTimer);
  ecgResizeTimer = setTimeout(renderEcg, 180);
});
