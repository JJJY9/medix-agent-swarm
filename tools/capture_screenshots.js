/**
 * 抓取运行截图，输出到 docs/screenshots/
 *
 * 前置：先在另一个终端把服务跑起来
 *     .venv/Scripts/python.exe api_server.py
 *
 * 运行（playwright 走 npx 缓存，不必装到项目里）：
 *     NODE_PATH=<npx缓存路径> node tools/capture_screenshots.js
 *
 * 见 README「截图」一节。
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE = process.env.MEDIX_URL || 'http://127.0.0.1:8000';
const OUT = path.resolve(__dirname, '..', 'docs', 'screenshots');

const QUESTION = '最近三天持续发热咳嗽伴咽痛，还有胸闷，需要去医院吗？';

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function shoot(page, name, opts = {}) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, ...opts });
  const kb = (fs.statSync(file).size / 1024).toFixed(0);
  console.log(`  ✓ ${name}  (${kb} KB)`);
}

async function waitForAnswer(page, timeoutMs = 240000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await page.evaluate(() => {
      if (document.querySelector('.failure')) return 'failure';
      const note = document.querySelector('.note');
      if (!note) return 'running';
      // 流式占位卡也带 .note：必须等 streaming 结束且发送按钮恢复
      if (note.classList.contains('streaming')) return 'running';
      const send = document.getElementById('send');
      if (send && send.disabled) return 'running';
      return 'answer';
    });
    if (state !== 'running') return state;
    await wait(1000);
  }
  return 'timeout';
}

/**
 * 启动浏览器。
 * 优先用 Playwright 自带的 Chromium；若版本对不上（缓存里是旧版），
 * 退回系统已装的 Edge / Chrome，避免为了截图再下 150MB。
 *
 * --disable-gpu 等参数是必须的：这台机器上 GPU 合成会把洋红色噪点烤进截图，
 * 关掉硬件加速后走软件光栅，出图才干净。
 */
const CHROMIUM_ARGS = [
  '--disable-gpu',
  '--disable-gpu-compositing',
  '--disable-accelerated-2d-canvas',
  '--force-color-profile=srgb',
  '--hide-scrollbars',
];

async function launchBrowser() {
  const attempts = [
    { label: 'Playwright Chromium', opts: { args: CHROMIUM_ARGS } },
    { label: '系统 Edge', opts: { channel: 'msedge', args: CHROMIUM_ARGS } },
    { label: '系统 Chrome', opts: { channel: 'chrome', args: CHROMIUM_ARGS } },
  ];
  const errors = [];
  for (const { label, opts } of attempts) {
    try {
      const browser = await chromium.launch(opts);
      console.log(`使用浏览器：${label}`);
      return browser;
    } catch (e) {
      errors.push(`${label}: ${String(e.message).split('\n')[0]}`);
    }
  }
  throw new Error('没有可用浏览器\n  ' + errors.join('\n  '));
}

/**
 * 临时解除内部滚动，让整段答复能一次性截全。
 * .thread 是 overflow:auto 的滚动容器，fullPage 不会把它展开，
 * 不处理的话截出来只有当前可视区那一段。
 */
async function expandForFullCapture(page, expand) {
  await page.evaluate((on) => {
    let s = document.getElementById('__capture_style');
    if (!s) {
      s = document.createElement('style');
      s.id = '__capture_style';
      document.head.appendChild(s);
    }
    s.textContent = on
      ? `html, body, .shell { height: auto !important; }
         .workspace { flex: none !important; min-height: auto !important;
                      grid-template-columns: minmax(0, 1fr) 328px !important; }
         .thread-col { overflow: visible !important; }
         .thread { overflow: visible !important; height: auto !important; }
         .rail { min-height: auto !important; }
         .rail-inner { overflow: visible !important; height: auto !important; }`
      : '';
    document.getElementById('thread').scrollTop = 0;
    window.scrollTo(0, 0);
  }, expand);
  await wait(350);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await launchBrowser();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    locale: 'zh-CN',
  });
  const page = await context.newPage();

  console.log(`打开 ${BASE} …`);
  await page.goto(BASE, { waitUntil: 'networkidle' });

  // 等知识库就绪，避免截到"加载中"
  await page.waitForFunction(
    () => document.getElementById('r-kb')?.textContent === '就绪',
    { timeout: 120000 }
  ).catch(() => console.log('  (知识库状态未变为就绪，继续)'));

  console.log('01 空状态');
  await shoot(page, '01-空状态-首页.png');

  console.log(`02 提交主诉：${QUESTION}`);
  await page.fill('#input', QUESTION);
  await page.click('#send');

  // 抓"会诊进行中"：心电加速、科室逐个点亮
  await wait(2500);
  await shoot(page, '02-会诊进行中.png');

  const state = await waitForAnswer(page);
  console.log(`   结果：${state}`);
  if (state !== 'answer') {
    if (state === 'failure') {
      const msg = await page.textContent('.failure');
      console.error('   会诊失败：', msg);
    } else {
      console.error('   等待会诊答复超时');
    }
    await browser.close();
    process.exitCode = 1;
    return;
  }

  console.log('03 完整会诊意见（展开内部滚动后整页截取）');
  await expandForFullCapture(page, true);
  await shoot(page, '03-完整会诊意见.png', { fullPage: true });
  await expandForFullCapture(page, false);

  console.log('04 诊疗建议与风险提示');
  await page.evaluate(() => {
    const t = document.getElementById('thread');
    t.scrollTop = t.scrollHeight;
  });
  await wait(500);
  await shoot(page, '04-诊疗建议.png');

  console.log('05 移动端：首页');
  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    locale: 'zh-CN',
  });
  const mp = await mobile.newPage();
  await mp.goto(BASE, { waitUntil: 'networkidle' });
  await wait(2000);
  // 输入框是 position:sticky，整页截图时会被定格在页面中间盖住正文
  await mp.evaluate(() => {
    const s = document.createElement('style');
    s.textContent = '.composer { position: static !important; }';
    document.head.insertBefore(s, document.head.firstChild);
  });
  await wait(300);
  await shoot(mp, '05-移动端-首页.png', { fullPage: true });

  console.log('06 移动端：会诊意见');
  await mp.fill('#input', QUESTION);
  await mp.click('#send');
  const mState = await waitForAnswer(mp);
  console.log(`   结果：${mState}`);
  if (mState !== 'answer') {
    console.error(
      mState === 'failure'
        ? '   移动端会诊失败'
        : '   移动端等待答复超时'
    );
    await mobile.close();
    await browser.close();
    process.exitCode = 1;
    return;
  }
  await mp.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await wait(600);
  await shoot(mp, '06-移动端-会诊意见.png');
  await mobile.close();

  await browser.close();
  console.log(`\n完成，输出目录：${OUT}`);
})().catch((err) => {
  console.error('截图失败：', err);
  process.exit(1);
});
