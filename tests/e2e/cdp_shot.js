// 用 CDP 驱动 headless Chrome 截图「大纲」页签（不依赖 agent-browser 的 Chromium 下载）
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const APP = 'http://127.0.0.1:5199/';
const OUT = 'E:\\CODE\\CangKu\\NovelForge\\Temp\\';

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
  const r = await cdp.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}

async function shot(cdp, name) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(OUT + name, Buffer.from(r.data, 'base64'));
  console.log('saved', name, fs.statSync(OUT + name).size, 'bytes');
}

(async () => {
  const port = 9222;
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    `--remote-debugging-port=${port}`, '--window-size=1500,1040',
    '--user-data-dir=' + OUT + 'cdp_profile', 'about:blank',
  ], { stdio: 'ignore', detached: false });

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

  await cdp.send('Page.navigate', { url: APP });
  await sleep(5000);   // 等 API 轮询与 Vue 挂载

  // 1) 首页
  await shot(cdp, 'ui_1_pipeline.png');

  // 2) 切到「大纲」页签
  const clicked = await evaluate(cdp, `(() => {
    const btns = [...document.querySelectorAll('.tabs button')];
    const b = btns.find(x => x.textContent.trim() === '大纲');
    if (b) { b.click(); return 'clicked'; }
    return 'notfound:' + btns.map(x=>x.textContent.trim()).join('|');
  })()`);
  console.log('tab click:', clicked);
  await sleep(2500);
  await shot(cdp, 'ui_2_outline.png');

  // 3) 诊断数据
  const diag = await evaluate(cdp, `(() => {
    const out = {};
    out.online = !!document.querySelector('.conn.on');
    out.treeRows = document.querySelectorAll('.tree-row').length;
    out.propCard = !!document.querySelector('.ov-prop-card');
    out.emptyProp = (document.querySelector('.ov-prop-card .empty')||{}).textContent || '';
    out.toolbar = (document.querySelector('.ov-toolbar .meta')||{}).textContent || '';
    out.issues = [...document.querySelectorAll('.ov-issue')].map(x=>x.textContent.trim());
    out.verdictPill = (document.querySelector('.ov-toolbar .pill')||{}).textContent || '';
    out.treeText = [...document.querySelectorAll('.tree-row')].slice(0,12).map(x=>x.textContent.trim().replace(/\\s+/g,' '));
    out.propFlags = [...document.querySelectorAll('.ov-flag')].map(x=>x.textContent.trim());
    out.propChars = [...document.querySelectorAll('.ov-chars .pill')].map(x=>x.textContent.trim());
    out.aiBtns = [...document.querySelectorAll('.ov-ai button')].map(x=>x.textContent.trim());
    return out;
  })()`);
  console.log('DIAG=' + JSON.stringify(diag, null, 2));

  // 4) 选中 node_1 看属性面板
  await evaluate(cdp, `(() => {
    const rows = [...document.querySelectorAll('.tree-row')];
    const t = rows.find(r => (r.textContent||'').includes('节点1'));
    if (t) t.click();
  })()`);
  await sleep(1200);
  await shot(cdp, 'ui_3_selected.png');

  const diag2 = await evaluate(cdp, `(() => {
    const ta = document.querySelector('.ov-prop-text');
    return {
      propText: ta ? ta.value : null,
      flags: [...document.querySelectorAll('.ov-flag')].map(x=>x.textContent.trim()),
      chars: [...document.querySelectorAll('.ov-chars')].map(x=>x.textContent.trim()),
      selRow: !!document.querySelector('.tree-row.sel'),
    };
  })()`);
  console.log('DIAG2=' + JSON.stringify(diag2, null, 2));

  // 5) 打开版本对比
  await evaluate(cdp, `(() => {
    const b = [...document.querySelectorAll('.ov-toolbar button')].find(x=>x.textContent.trim()==='版本对比');
    if (b) b.click();
  })()`);
  await sleep(2000);
  await shot(cdp, 'ui_4_versiondiff.png');
  const diag3 = await evaluate(cdp, `(() => {
    const d = document.querySelector('.drawer');
    return { open: !!document.querySelector('.drawer-mask'), segs: document.querySelectorAll('.ov-diff-seg').length,
             info: (document.querySelector('.ov-verinfo')||{}).textContent||'' };
  })()`);
  console.log('DIAG3=' + JSON.stringify(diag3, null, 2));

  // 6) 关闭，打开多方案
  await evaluate(cdp, `(() => { const b=[...document.querySelectorAll('.drawer-head button')].find(x=>x.textContent.trim()==='关闭'); if(b) b.click(); })()`);
  await sleep(800);
  await evaluate(cdp, `(() => { const b=[...document.querySelectorAll('.ov-toolbar button')].find(x=>x.textContent.trim()==='多方案'); if(b) b.click(); })()`);
  await sleep(1500);
  await shot(cdp, 'ui_5_multi.png');
  const diag4 = await evaluate(cdp, `(() => ({ open: !!document.querySelector('.drawer-mask'), empty:(document.querySelector('.ov-multi')||{}).textContent || (document.querySelector('.drawer .empty')||{}).textContent||'', btns:[...document.querySelectorAll('.drawer-head button')].map(x=>x.textContent.trim()) }))()`);
  console.log('DIAG4=' + JSON.stringify(diag4, null, 2));

  // 控制台错误
  const errs = await evaluate(cdp, `window.__errs || []`).catch(() => []);
  proc.kill();
  await sleep(500);
  console.log('DONE');
  process.exit(0);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
