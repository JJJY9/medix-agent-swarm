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

function scrollThreadToBottom() {
  els.thread.scrollTop = els.thread.scrollHeight;
}

/** 窄屏是整页滚动，且会诊面板排在问诊区上方 */
function isStacked() {
  return window.matchMedia('(max-width: 980px)').matches;
}

/* ---------- 渲染会诊意见 ---------- */

// 把 "**粗体**" 拆成文本节点和 <strong>，不使用 innerHTML
function appendInline(parent, text) {
  const parts = String(text).split(/\*\*([^*]+)\*\*/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (i % 2 === 1) {
      const strong = document.createElement('strong');
      strong.textContent = part;
      parent.appendChild(strong);
    } else {
      parent.appendChild(document.createTextNode(part));
    }
  });
}

// 正文里的 "1. xxx" 归组成 <ol>，其余按段落处理
function appendBody(container, text) {
  const lines = String(text)
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);

  let list = null;

  for (const line of lines) {
    const numbered = line.match(/^(\d+)\s*[.、)]\s*(.+)$/);
    if (numbered) {
      if (!list) {
        list = document.createElement('ol');
        container.appendChild(list);
      }
      const li = document.createElement('li');
      appendInline(li, numbered[2]);
      list.appendChild(li);
    } else {
      list = null;
      const p = document.createElement('p');
      appendInline(p, line);
      container.appendChild(p);
    }
  }
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

function renderAnswer(payload, elapsedSeconds) {
  const turn = document.createElement('div');
  turn.className = 'turn';

  const note = document.createElement('div');
  note.className = 'note';
  note.dataset.mode = payload.mode;

  const head = document.createElement('div');
  head.className = 'note-head';

  const kind = document.createElement('span');
  kind.className = 'note-kind';
  kind.textContent = payload.mode === 'swarm' ? '多学科会诊意见' : '单科诊疗意见';
  head.appendChild(kind);

  const time = document.createElement('span');
  time.className = 'note-time';
  time.textContent = `${elapsedSeconds.toFixed(1)}s`;
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
    '<span class="working-text">正在分诊并调度科室…</span>';
  els.thread.appendChild(wrap);
  scrollThreadToBottom();
  return wrap;
}

function renderError(message) {
  const wrap = document.createElement('div');
  wrap.className = 'failure';
  const strong = document.createElement('strong');
  strong.textContent = '会诊未能完成';
  wrap.appendChild(strong);
  wrap.appendChild(document.createTextNode(message));
  els.thread.appendChild(wrap);
  scrollThreadToBottom();
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
  scrollThreadToBottom();
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
  els.input.focus();
}

/* ---------- 提交主诉 ---------- */

async function send(question) {
  if (busy || !question.trim()) return;

  busy = true;
  els.send.disabled = true;
  els.empty.hidden = true;

  resetRail();
  renderAsk(question);

  const working = renderWorking();
  const workingText = working.querySelector('.working-text');
  els.railScan.hidden = false;
  setVitals('active', '会诊进行中');

  // 窄屏整页滚动，会诊面板排在问诊区上方，提交后把视线带回问诊区
  if (isStacked()) {
    els.thread.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  const startedAt = performance.now();
  let firstAgentSeen = false;

  AGENT_IDS.forEach((id) => setAgentState(id, 'working', '等待调度'));
  els.readout.mode.textContent = '分诊中';
  logStep('已提交主诉，正在分诊并路由');

  try {
    const sid = await ensureSession();

    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, session_id: sid, enable_swarm: true }),
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

        if (event.type === 'stage') {
          workingText.textContent = '正在调度专科科室…';
          els.readout.mode.textContent = '会诊分诊中';
        } else if (event.type === 'agent_done') {
          setAgentState(event.agent_id, 'done', '已出具意见');
          firstAgentSeen = true;
          logStep(`${event.name} 已出具会诊意见`);
        } else if (event.type === 'result') {
          finalPayload = event;
        } else if (event.type === 'error') {
          throw new Error(event.message);
        }
      }
    }

    working.remove();

    if (!finalPayload) {
      throw new Error('服务未返回结果');
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
    els.readout.tasks.textContent = String(finalPayload.subtasks_completed ?? 0);
    els.readout.time.textContent = `${(finalPayload.total_time || elapsed).toFixed(1)}s`;
    els.readout.session.textContent = shortSession(finalPayload.session_id || sid);

    logStep(finalPayload.mode === 'swarm' ? '协调科室已汇总各方意见' : '单科直接出具意见', false);
    setVitals('idle', '系统就绪');

    renderAnswer(finalPayload, finalPayload.total_time || elapsed);
  } catch (err) {
    working.remove();
    renderError(err.message || String(err));
    AGENT_IDS.forEach((id) => setAgentState(id, 'idle', '待命'));
    els.readout.mode.textContent = '中断';
    logStep(`中断：${err.message || err}`, false);
    setVitals('bad', '会诊中断');
  } finally {
    busy = false;
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

els.starters.addEventListener('click', (e) => {
  const btn = e.target.closest('.starter');
  if (!btn) return;
  els.empty.hidden = true;
  send(btn.dataset.q);
});

resetRail();
renderEcg();
checkStatus();
setInterval(checkStatus, 20000);
els.input.focus();

// 屏宽变化后重新排布波形（两份波形共用同一段路径，拼接处才能无缝衔接）
let ecgResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(ecgResizeTimer);
  ecgResizeTimer = setTimeout(renderEcg, 180);
});
