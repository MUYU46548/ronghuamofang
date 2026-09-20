// UI 修复复核：headless Chrome + CDP（用系统 Chrome，不下载 Chromium）
// 1) 流水线页紧凑度指标  2) 大纲树是否还有"一字一排"的竖排故障（量化）
// 3) 树宽拖拽  4) 收件箱跳转后能否一步返回
// 运行：cd console && node ../tests/e2e/verify_ui_fix.js
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const APP = 'http://127.0.0.1:5199/';
const OUT = path.resolve(__dirname) + path.sep;

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let b = '';
      res.on('data', (d) => (b += d));
      res.on('end', () => resolve({ status: res.statusCode, body: b }));
    }).on('error', reject);
  });
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(wsUrl) {
    const WebSocket = require('ws');
    const ws = new WebSocket(wsUrl, { perMessageDeflate: false, maxPayload: 256 * 1024 * 1024 });
    await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
    return new CDP(ws);
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  onMessage() {
    this.ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    });
  }
}

async function evaluate(cdp, expr) {
  const r = await cdp.send('Runtime.evaluate', {
    expression: expr, awaitPromise: true, returnByValue: true,
  });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
}

async function shot(cdp, name) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(OUT + name, Buffer.from(r.data, 'base64'));
  console.log('  saved', name, fs.statSync(OUT + name).size, 'bytes');
}

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log('[%s] %s%s', cond ? 'PASS' : 'FAIL', name, detail ? '  → ' + JSON.stringify(detail) : '');
}

const METRICS = `(() => {
  const out = {};
  const px = (el, p) => el ? getComputedStyle(el)[p] : null;
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };

  // 流水线紧凑度
  const mini = document.querySelector('.stage-actions .mini');
  const row = document.querySelector('.stage-row');
  const bar = document.querySelector('.run-all-bar');
  out.pipeline = {
    stageRowH: box(row) && box(row).h,
    miniH: box(mini) && box(mini).h,
    miniPad: mini ? px(mini, 'padding') : null,
    runAllDisplay: bar ? px(bar, 'display') : null,
    runAllGap: bar ? px(bar, 'gap') : null,
    stageRows: document.querySelectorAll('.stage-row').length,
    actionsPerRow: document.querySelectorAll('.stage-row')[0]
      ? document.querySelectorAll('.stage-row')[0].querySelectorAll('.stage-actions .mini').length : 0,
  };

  // 大纲树：逐行量标签宽度与行数（竖排故障 = 标签宽度极窄 + 行数远超 1）
  const rows = [...document.querySelectorAll('.tree-row')];
  const labelStats = rows.map((r) => {
    const lab = r.querySelector('.tree-label');
    if (!lab) return null;
    const b = lab.getBoundingClientRect();
    const lh = parseFloat(getComputedStyle(lab).lineHeight) || 20;
    return { w: Math.round(b.width), h: Math.round(b.height), lines: Math.max(1, Math.round(b.height / lh)) };
  }).filter(Boolean);
  out.tree = {
    rowCount: rows.length,
    labelCount: labelStats.length,
    minLabelWidth: labelStats.length ? Math.min(...labelStats.map((x) => x.w)) : null,
    maxLines: labelStats.length ? Math.max(...labelStats.map((x) => x.lines)) : null,
    cards: box(document.querySelector('.ov-tree-card')),
    splitter: box(document.querySelector('.ov-splitter')),
    maxRowTooltipLen: rows.reduce((m, r) => Math.max(m, (r.getAttribute('title') || '').length), 0),
    firstRows: rows.slice(0, 5).map((r) => (r.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 46)),
  };

  // 返回收件箱
  const backbar = document.querySelector('.backbar');
  out.backbar = { present: !!backbar, text: backbar ? backbar.textContent.trim().replace(/\\s+/g, ' ') : '' };
  out.activeTab = (() => {
    const b = [...document.querySelectorAll('.tabs button')].find((x) => x.classList.contains('active'));
    return b ? b.textContent.trim() : null;
  })();
  return out;
})()`;

(async () => {
  const port = 9223;
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    // 环境里设了 HTTP_PROXY/HTTPS_PROXY，localhost 会被代理吃掉返回 502，必须绕过
    '--no-proxy-server', '--proxy-bypass-list=<-loopback>',
    `--remote-debugging-port=${port}`, '--window-size=1500,1040',
    '--user-data-dir=' + OUT + 'cdp_profile_verify', 'about:blank',
  ], { stdio: 'ignore' });
  global.__chromeProc = proc;   // 失败路径也要能收尸，避免遗留 headless Chrome

  let ver = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    try { ver = JSON.parse((await get(`http://127.0.0.1:${port}/json/version`)).body); break; } catch (_) { /* wait */ }
  }
  if (!ver) { console.error('CDP 未就绪'); proc.kill(); process.exit(1); }
  console.log('chrome', ver.Browser);

  const list = JSON.parse((await get(`http://127.0.0.1:${port}/json/list`)).body);
  const page = list.find((t) => t.type === 'page');
  const cdp = await CDP.connect(page.webSocketDebuggerUrl);
  cdp.onMessage();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: 'window.__errs=[];window.addEventListener("error",e=>window.__errs.push(String(e.message)));',
  });

  await cdp.send('Page.navigate', { url: APP });
  await sleep(6000);

  console.log('\n=== 1. 流水线页（紧凑度）===');
  await shot(cdp, 'fix_1_pipeline.png');
  let m = await evaluate(cdp, METRICS);
  console.log('  ' + JSON.stringify(m.pipeline));
  check('流水线阶段行渲染正常', m.pipeline.stageRows >= 7, m.pipeline.stageRows);
  check('按钮点击高度 >= 26px（不再那么挤）', m.pipeline.miniH >= 26, m.pipeline.miniH);
  check('全自动运行条应用了横向布局样式', m.pipeline.runAllDisplay === 'flex', m.pipeline.runAllDisplay);

  console.log('\n=== 2. 大纲树（竖排故障复核）===');
  const clicked = await evaluate(cdp, `(() => {
    const b = [...document.querySelectorAll('.tabs button')].find(x => x.textContent.trim() === '大纲');
    if (b) { b.click(); return 'clicked'; }
    return 'notfound';
  })()`);
  console.log('  tab click:', clicked);
  await sleep(3000);
  await shot(cdp, 'fix_2_outline.png');
  m = await evaluate(cdp, METRICS);
  console.log('  tree: ' + JSON.stringify({ rowCount: m.tree.rowCount, minLabelWidth: m.tree.minLabelWidth, maxLines: m.tree.maxLines, cards: m.tree.cards, splitter: m.tree.splitter }));
  console.log('  首几行文本: ' + JSON.stringify(m.tree.firstRows, null, 0));
  check('大纲树有节点', m.tree.rowCount > 0, m.tree.rowCount);
  check('标签最窄宽度 >= 120px（不再被压成竖排）', m.tree.minLabelWidth >= 120, m.tree.minLabelWidth);
  check('没有任何节点被折成 3 行以上', m.tree.maxLines <= 3, m.tree.maxLines);
  check('截断仅影响显示：悬浮提示里保留完整原文（>= 60 字）',
    m.tree.maxRowTooltipLen >= 60, m.tree.maxRowTooltipLen);
  check('存在分隔条（可拖拽调宽）', !!(m.tree.splitter && m.tree.splitter.w > 0), m.tree.splitter);

  console.log('\n=== 3. 拖动分隔条改树宽 ===');
  const dragRes = await evaluate(cdp, `(async () => {
    const sp = document.querySelector('.ov-splitter');
    const card = document.querySelector('.ov-tree-card');
    if (!sp || !card) return { ok: false, reason: 'no splitter' };
    const before = Math.round(card.getBoundingClientRect().width);
    const r = sp.getBoundingClientRect();
    const cy = r.top + r.height / 2;
    sp.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: r.left + 4, clientY: cy, pointerId: 1 }));
    for (let i = 1; i <= 8; i++) {
      window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: r.left + 4 + i * 14, clientY: cy, pointerId: 1 }));
      await new Promise((res) => setTimeout(res, 20));
    }
    window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: r.left + 116, clientY: cy, pointerId: 1 }));
    await new Promise((res) => setTimeout(res, 120));
    const after = Math.round(card.getBoundingClientRect().width);
    return { ok: true, before, after, saved: localStorage.getItem('mofang.outline.treeW') };
  })()`);
  console.log('  ' + JSON.stringify(dragRes));
  check('拖拽后树变宽', dragRes.ok && dragRes.after > dragRes.before, dragRes);
  check('树宽写入 localStorage（下次打开保持）',
    dragRes.ok && dragRes.saved && Number(dragRes.saved) === dragRes.after, dragRes.saved);
  await sleep(400);
  await shot(cdp, 'fix_3_widened.png');

  console.log('\n=== 4. 收件箱 → 大纲 → 一步返回 ===');
  await evaluate(cdp, `(() => { const b=[...document.querySelectorAll('.tabs button')].find(x=>x.textContent.trim().startsWith('收件箱')); if(b) b.click(); })()`);
  await sleep(1500);
  const inboxJump = await evaluate(cdp, `(() => {
    const b = [...document.querySelectorAll('.gate-card button')].find(x => x.textContent.trim() === '查看结构化大纲');
    if (!b) return 'no-button';
    b.click(); return 'clicked';
  })()`);
  console.log('  收件箱内跳转按钮:', inboxJump);
  await sleep(2000);
  await shot(cdp, 'fix_4_from_inbox.png');
  m = await evaluate(cdp, METRICS);
  console.log('  ' + JSON.stringify(m.backbar) + ' activeTab=' + m.activeTab);
  check('从收件箱跳转后出现返回按钮', m.backbar.present, m.backbar);
  check('当前确实在大纲页', m.activeTab === '大纲', m.activeTab);

  const backClick = await evaluate(cdp, `(() => {
    const b = document.querySelector('.backbar button');
    if (!b) return 'no-button';
    b.click(); return 'clicked';
  })()`);
  await sleep(1200);
  m = await evaluate(cdp, METRICS);
  check('点返回后回到收件箱', m.activeTab && m.activeTab.startsWith('收件箱'), m.activeTab);
  check('回到收件箱后返回按钮自动消失', !m.backbar.present, m.backbar);

  const errs = await evaluate(cdp, `window.__errs || []`).catch(() => []);
  console.log('\n页面 JS 错误: ' + JSON.stringify(errs));
  check('无未捕获 JS 错误', !errs || errs.length === 0, errs);

  const fails = results.filter((r) => !r.ok);
  console.log('\n' + '='.repeat(60));
  console.log('合计: %d 通过 / %d 失败', results.length - fails.length, fails.length);
  if (fails.length) console.log('失败项: ' + fails.map((f) => f.name).join('、'));
  console.log('='.repeat(60));

  proc.kill();
  await sleep(500);
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('FAIL', e);
  if (global.__chromeProc) { try { global.__chromeProc.kill(); } catch (_) { /* noop */ } }
  process.exit(1);
});
