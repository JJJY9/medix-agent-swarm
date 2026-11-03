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
  send: document.getElementById('send'),
  newSession: document.getElementById('new-session'),
  themeToggle: document.getElementById('theme-toggle'),
  historyToggle: document.getElementById('history-toggle'),
  historyDrawer: document.getElementById('history-drawer'),
  historyBackdrop: document.getElementById('history-backdrop'),
  historyClose: document.getElementById('history-close'),
  historySearch: document.getElementById('history-search'),
  historyList: document.getElementById('history-list'),
  historyEmpty: document.getElementById('history-empty'),
  stop: document.getElementById('stop'),
  jumpLatest: document.getElementById('jump-latest'),
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
    kb: document.getElementById('r-kb'),
    session: document.getElementById('r-session'),
  },
};

const AGENT_IDS = ['consultation_agent', 'diagnostic_agent', 'research_agent'];

let sessionId = localStorage.getItem('medix.session') || null;
let busy = false;
let activeController = null;
let historyOpen = false;
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

  if (payload.disclaimer) {
    lines.push('## 免责声明', '', payload.disclaimer, '');
  }

  lines.push('---', '', '本文件由 MediX 生成，仅供参考，不能替代执业医师的诊断与治疗。', '');
  return lines.join('\n');
}

function downloadFile(content, filename) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
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
      question = '';
    }
  }

  lines.push('---', '', '本文件由 MediX 生成，仅供参考，不能替代执业医师的诊断与治疗。', '');
  return lines.join('\n');
}

function renderAnswer(payload, elapsedSeconds, question = '') {
  const turn = document.createElement('div');
  turn.className = 'turn';

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

function renderError(message) {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  const strong = document.createElement('strong');
  strong.textContent = '会诊未能完成';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode(message));
  els.thread.appendChild(wrap);
  scrollThreadToBottom(true);
}

function renderStopped() {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  const strong = document.createElement('strong');
  strong.textContent = '会诊已停止';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode('本次会诊在生成过程中被手动停止，未产生结果。'));
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

function renderHistoryItems(sessions) {
  els.historyList.replaceChildren();
  els.historyEmpty.hidden = sessions.length > 0;

  if (!sessions.length) {
    els.historyEmpty.textContent = els.historySearch.value.trim()
      ? '没有匹配的会诊记录'
      : '还没有历史会诊，提交一次主诉后会自动保存。';
    return;
  }

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
    meta.textContent = `${formatTime(item.updated_at)} · ${item.message_count || 0} 条`;

    const preview = document.createElement('span');
    preview.className = 'history-preview';
    preview.textContent = item.preview || '（暂无答复摘要）';

    open.append(title, meta, preview);
    open.addEventListener('click', () => openSession(item.id));

    const actions = document.createElement('div');
    actions.className = 'history-actions';

    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'history-action';
    rename.textContent = '改名';
    rename.title = '重命名这次会诊';
    rename.addEventListener('click', () => renameHistory(item));

    const exportBtn = document.createElement('button');
    exportBtn.type = 'button';
    exportBtn.className = 'history-action';
    exportBtn.textContent = '导出';
    exportBtn.title = '导出整段会诊记录为 Markdown';
    exportBtn.addEventListener('click', () => exportHistory(item));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'history-action danger';
    del.textContent = '删除';
    del.title = '删除这次会诊记录';
    del.addEventListener('click', () => deleteHistory(item));

    actions.append(rename, exportBtn, del);
    li.append(open, actions);
    els.historyList.appendChild(li);
  }
}

async function loadHistory(query = '') {
  try {
    const res = await fetch(`/api/sessions?limit=50&q=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error(`服务返回 ${res.status}`);
    const data = await res.json();
    renderHistoryItems(data.sessions || []);
  } catch (err) {
    els.historyList.replaceChildren();
    els.historyEmpty.hidden = false;
    els.historyEmpty.textContent = `会诊记录加载失败：${err.message || err}`;
  }
}

let lastFocused = null;

function openHistory() {
  lastFocused = document.activeElement;
  historyOpen = true;
  els.historyDrawer.hidden = false;
  els.historyBackdrop.hidden = false;
  els.historyToggle.setAttribute('aria-expanded', 'true');
  els.historySearch.value = '';
  els.historyList.replaceChildren();
  els.historyEmpty.hidden = false;
  els.historyEmpty.textContent = '正在加载会诊记录…';
  loadHistory();
  els.historySearch.focus();
}

function closeHistory(restoreFocus = false) {
  historyOpen = false;
  els.historyDrawer.hidden = true;
  els.historyBackdrop.hidden = true;
  els.historyToggle.setAttribute('aria-expanded', 'false');
  if (restoreFocus && lastFocused instanceof HTMLElement) {
    lastFocused.focus();
  }
}

function renderStoredMessages(messages) {
  els.thread.replaceChildren(els.empty);
  els.empty.hidden = messages.length > 0;

  let question = '';
  for (const message of messages) {
    if (message.role === 'user') {
      question = message.content || '';
      renderAsk(question);
    } else if (message.role === 'assistant' && message.content) {
      renderAnswer(
        { answer: message.content, mode: 'single', historical: true },
        null,
        question
      );
      question = '';
    }
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
  } catch (err) {
    closeHistory();
    renderError(`读取会诊记录失败：${err.message || err}`);
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
      body: JSON.stringify({ question, session_id: sid, enable_swarm: true }),
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
      renderStopped();
      AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '已停止'));
      els.readout.mode.textContent = '已停止';
      logStep('用户停止了本次会诊', false);
      setVitals('idle', '已停止会诊');
    } else {
      renderError(err.message || String(err));
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

let historySearchTimer = null;
els.historySearch.addEventListener('input', () => {
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(() => {
    loadHistory(els.historySearch.value.trim());
  }, 220);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && historyOpen) closeHistory(true);
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
