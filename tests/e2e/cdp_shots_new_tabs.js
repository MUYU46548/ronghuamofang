// 用 CDP 驱动 headless Chrome 逐页签截图 + 抓控制台报错。
// 不依赖 agent-browser / playwright / ws —— Node 22 自带全局 WebSocket。
//
// 用法：node tests/e2e/cdp_shots_new_tabs.js <baseUrl> <outDir>
// 例：  node tests/e2e/cdp_shots_new_tabs.js http://127.0.0.1:8090 Temp/gui_verify
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const BASE = process.argv[2] || 'http://127.0.0.1:8090';
const OUT = process.argv[3] || 'Temp/gui_verify';
const PORT = 9333;
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
// 新面板 + 联调页签；顺序即截图顺序
const TABS = ['流水线', '审稿', '校对', '文风', '文风>章节节奏', '成本', '分章', '项目', '导出'];
const SKIP_ESTIMATE_PROBE = false;
// 运行前预估确认框（点「运行」阶段1 会先弹预估，这里只截不确认）
const PROBE_ESTIMATE = true;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let b = '';
      res.on('data', (d) => (b += d));
      res.on('end', () => {
        try { resolve(JSON.parse(b)); } catch (e) { reject(new Error('bad json: ' + b.slice(0, 120))); }
      });
    }).on('error', reject);
  });
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', rej, { once: true });
    });
    return new CDP(ws);
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('timeout: ' + method)); }
      }, 30000);
    });
  }
  async evalJS(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  }
  async shot(file) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
  }
  errors() {
    const out = [];
    for (const e of this.events) {
      if (e.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(e.params.type)) {
        out.push('[console.' + e.params.type + '] ' + e.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '));
      }
      if (e.method === 'Runtime.exceptionThrown') {
        out.push('[exception] ' + (e.params.exceptionDetails.exception?.description
          || e.params.exceptionDetails.text || '').split('\n')[0]);
      }
      if (e.method === 'Log.entryAdded' && ['error'].includes(e.params.entry.level)) {
        const en = e.params.entry;
        out.push('[log.error] ' + en.text + (en.url ? '  ← ' + en.url : ''));
      }
      // 非 2xx 的 HTTP 响应（带 URL，便于定位是哪个端点/资源）
      if (e.method === 'Network.responseReceived') {
        const st = e.params.response.status;
        if (st >= 400) {
          out.push('[http ' + st + '] ' + e.params.response.url);
        }
      }
    }
    return out;
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const profile = fs.mkdtempSync(path.join(require('os').tmpdir(), 'cdp_prof_'));
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--no-proxy-server', '--window-size=1440,1000',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + profile, BASE,
  ], { stdio: 'ignore' });

  let pageWs = null;
  try {
    for (let i = 0; i < 60 && !pageWs; i++) {
      await sleep(400);
      try {
        const list = await getJson('http://127.0.0.1:' + PORT + '/json/list');
        const page = list.find((t) => t.type === 'page' && String(t.url).includes('127.0.0.1'));
        if (page?.webSocketDebuggerUrl) pageWs = page.webSocketDebuggerUrl;
      } catch (e) { /* chrome 还没起来 */ }
    }
    if (!pageWs) { console.log('FAIL: 无法连接到 Chrome 调试端口'); process.exit(1); }

    const cdp = await CDP.connect(pageWs);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');
    await cdp.send('Network.enable');
    await sleep(2500);   // 等 Vue 挂载 + 首屏 fetch 完成

    const online = await cdp.evalJS("document.querySelector('.conn')?.textContent || '?'");
    console.log('连接状态:', online.trim());
    if (!/已连接/.test(online)) {
      console.log('WARN: 控制台显示「' + online.trim() + '」——检查 nf_api 是否在 8765');
    }

    let idx = 0;
    for (const spec of TABS) {
      idx += 1;
      // 支持 "文风>章节节奏" 形式：先点主页签，再点页内子页签
      const [main, sub] = spec.split(">");
      const clickByText = (txt) => cdp.evalJS(`(() => {
        const b = [...document.querySelectorAll('nav.tabs button, .tabs-sub button')]
          .find(x => x.textContent.trim() === ${JSON.stringify(txt)});
        if (!b) return false;
        b.click();
        return true;
      })()`);
      if (!(await clickByText(main))) { console.log('SKIP 无此页签:', main); continue; }
      await sleep(1800);
      if (sub && !(await clickByText(sub))) { console.log('SKIP 无此子页签:', sub); continue; }
      await sleep(2200);
      const f = path.join(OUT, String(idx).padStart(2, '0') + '_'
        + spec.replace('>', '-') + '.png');
      await cdp.shot(f);
      const text = await cdp.evalJS("document.querySelector('#app')?.innerText || ''");
      const head = text.split('\n').filter((l) => l.trim()).slice(0, 6).join(' | ');
      console.log('SHOT ' + path.basename(f) + '  ← ' + head.slice(0, 200));
    }

    // 运行前 token/费用预估确认框：点「运行」只弹框、不发 POST；截完点「取消」
    await cdp.evalJS(`(() => {
      const b = [...document.querySelectorAll('nav.tabs button')]
        .find(x => x.textContent.trim() === '流水线');
      if (b) b.click();
    })()`);
    await sleep(1500);
    const opened = await cdp.evalJS(`(() => {
      const row = document.querySelector('.stage-row');
      const btn = row && [...row.querySelectorAll('button')].find(x => x.textContent.trim() === '运行');
      if (!btn) return false;
      btn.click();
      return true;
    })()`);
    if (opened) {
      await sleep(2500);
      const dialogText = await cdp.evalJS(
        "document.querySelector('.drawer-mask .dialog')?.innerText || ''");
      if (dialogText) {
        await cdp.shot(path.join(OUT, '90_运行前预估确认.png'));
        console.log('SHOT 90_运行前预估确认.png  ← '
          + dialogText.split('\n').filter((l) => l.trim()).slice(0, 8).join(' | ').slice(0, 240));
        const cancelled = await cdp.evalJS(`(() => {
          const btns = [...document.querySelectorAll('.dialog-actions button')];
          const c = btns.find(x => x.textContent.trim() === '取消');
          if (!c) return false;
          c.click();
          return true;
        })()`);
        console.log(cancelled ? '已点击「取消」（未提交任何运行）' : 'WARN 未找到取消按钮');
      } else {
        console.log('WARN 点「运行」后没有出现预估确认框');
      }
    } else {
      console.log('SKIP 找不到「运行」按钮');
    }

    const errs = cdp.errors();
    console.log('\n=== 控制台 error/warning（' + errs.length + ' 条）===');
    const seen = new Set();
    for (const e of errs) {
      if (seen.has(e)) continue;
      seen.add(e);
      console.log('  ' + e.slice(0, 300));
    }
    console.log(errs.length ? 'RESULT: 有报错' : 'RESULT: 控制台干净');
    cdp.ws.close();
  } finally {
    chrome.kill();
  }
})();
