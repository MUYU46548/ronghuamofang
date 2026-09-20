// 角色关系图端到端验证：headless Chrome + CDP（用系统 Chrome，不下载 Chromium）。
//
// 自给自足：内置静态服务器（serving console/renderer/dist）+ fetch 桩
// （把 /setting/current、/setting/appearances 指向本地 fixture），
// 不需要启动 nf_api，也不碰真实 data/ 之外的任何东西。
//
// 断言：节点/边数量与数据层一致、月神边数=可匹配关系数、absent 虚线、
//       幽灵节点存在、力导向真的跑开了、拖拽固定、缩放生效、其他页签未受影响、无 JS 错误。
//
// 运行：cd console && node ../tests/e2e/verify_role_graph.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { createRequire } = require('module');
const { pathToFileURL } = require('url');
const os = require('os');

const req = createRequire(path.join(__dirname, '..', 'console', 'package.json'));
const esbuild = req('esbuild');
// CDP 的 WebSocket 用 Node 22 内置的全局 WebSocket —— 免得为测试脚本引入 ws 依赖

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'console', 'renderer', 'dist');
const PORT = 5199;
const APP = `http://127.0.0.1:${PORT}/`;
const OUT = path.resolve(__dirname) + path.sep;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let b = ''; res.on('data', (d) => (b += d)); res.on('end', () => resolve({ status: res.statusCode, body: b })); })
    .on('error', reject);
});

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log('[%s] %s%s', cond ? 'PASS' : 'FAIL', name,
    cond ? '' : '  → ' + JSON.stringify(detail));
}

// ---------------- 静态服务器 ----------------
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.ico': 'image/x-icon', '.json': 'application/json; charset=utf-8' };

function serve() {
  return new Promise((resolve, reject) => {
    const srv = http.createServer((rq, rs) => {
      let p = decodeURIComponent(rq.url.split('?')[0]);
      if (p === '/') p = '/index.html';
      const f = path.join(DIST, p);
      if (!f.startsWith(DIST) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) {
        rs.writeHead(404); rs.end('not found'); return;
      }
      rs.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' });
      fs.createReadStream(f).pipe(rs);
    });
    srv.on('error', reject);
    srv.listen(PORT, '127.0.0.1', () => resolve(srv));
  });
}

// ---------------- CDP ----------------
class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.onopen = () => res();
      ws.onerror = () => rej(new Error('CDP WebSocket 连接失败: ' + wsUrl));
    });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (msg.id && c.pending.has(msg.id)) {
        const { resolve, reject } = c.pending.get(msg.id);
        c.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
}

async function evaluate(cdp, expr) {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 500));
  return r.result.value;
}

async function shot(cdp, name) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(OUT + name, Buffer.from(r.data, 'base64'));
  console.log('  saved', name, fs.statSync(OUT + name).size, 'bytes');
}

// ---------------- 数据层期望值（与组件共用同一份实现） ----------------
async function expectations() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rgv-'));
  const outfile = path.join(tmp, 'bundle.mjs');
  esbuild.buildSync({
    entryPoints: [path.join(ROOT, 'console', 'src', 'roleGraphData.js')],
    bundle: true, format: 'esm', outfile, logLevel: 'silent',
  });
  const { buildGraph, neighborsOf } = await import(pathToFileURL(outfile).href);
  const setting = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'setting', 'setting.json'), 'utf8'));
  const appearances = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'state', 'appearances.json'), 'utf8'));
  const g = buildGraph(setting, appearances, {});
  const yueshen = g.nodes.find((n) => n.name === '月神');
  const nb = neighborsOf(yueshen, g.links);
  const exp = {
    nodes: g.stats.nodeCount, links: g.stats.linkCount,
    ghosts: g.stats.ghostCount, real: g.stats.realCount,
    yueshenDegree: yueshen.degree, yueshenNeighborCount: nb.size - 1,
    fx: outfile, setting, appearances,
  };
  fs.rmSync(tmp, { recursive: true, force: true });
  return exp;
}

(async () => {
  const exp = await expectations();
  console.log('数据层期望：节点 %d（已入册 %d + 幽灵 %d）· 边 %d · 月神边数 %d',
    exp.nodes, exp.real, exp.ghosts, exp.links, exp.yueshenDegree);

  const srv = await serve();
  const profile = OUT + 'cdp_profile_rg';
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    // 环境里设了 HTTP_PROXY/HTTPS_PROXY，localhost 会被代理吃掉返回 502，必须绕过
    '--no-proxy-server', '--proxy-bypass-list=<-loopback>',
    '--remote-debugging-port=9224', '--window-size=1500,1100',
    '--user-data-dir=' + profile, 'about:blank',
  ], { stdio: 'ignore' });
  global.__chrome = proc;

  const stop = async () => {
    try { proc.kill(); } catch (_) { /* noop */ }
    try { srv.close(); } catch (_) { /* noop */ }
    await sleep(300);
  };

  let ver = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    try { ver = JSON.parse((await get('http://127.0.0.1:9224/json/version')).body); break; } catch (_) { /* wait */ }
  }
  if (!ver) { console.error('CDP 未就绪'); await stop(); process.exit(1); }
  console.log('chrome', ver.Browser);

  const list = JSON.parse((await get('http://127.0.0.1:9224/json/list')).body);
  const page = list.find((t) => t.type === 'page');
  const cdp = await CDP.connect(page.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  // 注入：错误收集 + fetch 桩（在应用脚本之前执行）
  const stub = `
    window.__errs = [];
    window.addEventListener('error', (e) => window.__errs.push(String(e.message)));
    const FIX = { setting: ${JSON.stringify(exp.setting)}, appearances: ${JSON.stringify(exp.appearances)} };
    window.__FIX = FIX;
    window.fetch = async (url, opt) => {
      const u = String(url);
      let body, status = 200;
      if (u.includes('/setting/appearances')) body = Object.assign({ ok: true }, FIX.appearances);
      else if (u.includes('/setting/current')) body = { ok: true, setting: FIX.setting };
      else if (u.includes('/state')) body = { book: '测试', stages: [], gates: {}, cost: {}, latest_job: {}, current_job: null };
      else if (u.includes('/health')) body = { ok: true };
      else { body = {}; status = 404; }
      return new Response(JSON.stringify(body), { status: status, headers: { 'Content-Type': 'application/json' } });
    };
  `;
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: stub });
  await cdp.send('Page.navigate', { url: APP });
  await sleep(4000);

  // 进入 设定 → 关系图
  console.log('\n=== 1. 打开「设定 → 关系图」 ===');
  await evaluate(cdp, `(() => {
    const b = [...document.querySelectorAll('.tabs button')].find(x => x.textContent.trim().startsWith('设定'));
    if (b) b.click();
  })()`);
  await sleep(2500);
  const tabClick = await evaluate(cdp, `(() => {
    const b = [...document.querySelectorAll('.tabs-sub button')].find(x => x.textContent.trim() === '关系图');
    if (!b) return 'no-tab';
    b.click(); return 'clicked';
  })()`);
  console.log('  关系图页签:', tabClick);
  await sleep(3500);
  check('存在「关系图」子页签', tabClick === 'clicked', tabClick);
  await shot(cdp, 'rg_1_graph.png');

  console.log('\n=== 2. 节点与边数量与数据层一致 ===');
  const dom = await evaluate(cdp, `(() => {
    const nodes = [...document.querySelectorAll('.rg-node')];
    const links = [...document.querySelectorAll('.rg-link')];
    const xy = nodes.map((g) => { const t = g.getAttribute('transform') || ''; const m = /translate\\(([-\\d.]+),([-\\d.]+)\\)/.exec(t); return m ? [parseFloat(m[1]), parseFloat(m[2])] : null; }).filter(Boolean);
    const r = (el) => { const c = el.querySelector('circle'); return c ? parseFloat(c.getAttribute('r')) : null; };
    return {
      nodeCount: nodes.length,
      linkCount: links.length,
      ghostNodes: nodes.filter((n) => n.classList.contains('ghost')).length,
      lockedLabels: nodes.filter((n) => n.querySelector('title') && /locked/.test(n.querySelector('title').textContent)).length,
      absentDashed: nodes.filter((n) => { const c = n.querySelector('circle'); return c && (c.getAttribute('stroke-dasharray') || '') !== '' && !n.classList.contains('ghost'); }).length,
      ghostDashed: nodes.filter((n) => { const c = n.querySelector('circle'); return n.classList.contains('ghost') && (c.getAttribute('stroke-dasharray') || '') !== ''; }).length,
      labels: nodes.map((n) => (n.querySelector('.rg-label') || {}).textContent || '').filter(Boolean),
      radii: nodes.map(r).filter((x) => x != null),
      spread: xy.length ? [Math.min(...xy.map(p => p[0])), Math.max(...xy.map(p => p[0])), Math.min(...xy.map(p => p[1])), Math.max(...xy.map(p => p[1]))] : null,
      legendItems: document.querySelectorAll('.rg-legend-item').length,
      unmatchedText: (document.querySelector('.rg-head') || {}).textContent || '',
    };
  })()`);
  console.log('  ' + JSON.stringify({ nodeCount: dom.nodeCount, linkCount: dom.linkCount,
    ghostNodes: dom.ghostNodes, absentDashed: dom.absentDashed, spread: dom.spread }));
  check('节点数与数据层一致', dom.nodeCount === exp.nodes, [dom.nodeCount, exp.nodes]);
  check('边数与数据层一致', dom.linkCount === exp.links, [dom.linkCount, exp.links]);
  check('幽灵节点数与数据层一致', dom.ghostNodes === exp.ghosts, [dom.ghostNodes, exp.ghosts]);
  check('月神 成为节点', dom.labels.includes('月神'), dom.labels.slice(0, 6));
  check('absent 角色描边为虚线', dom.absentDashed > 0, dom.absentDashed);
  check('幽灵节点描边为虚线', dom.ghostDashed === exp.ghosts, dom.ghostDashed);
  check('locked 角色带锁形标（title 含 locked）', dom.lockedLabels > 0, dom.lockedLabels);
  check('节点半径按出场次数分档（不全相等）',
    new Set(dom.radii.map((r) => Math.round(r))).size > 1, [...new Set(dom.radii.map((r) => Math.round(r)))].slice(0, 8));
  check('图例渲染（关系类型 + 描边分级）', dom.legendItems >= 10, dom.legendItems);
  check('头部显示未匹配提示按钮', /未匹配清单/.test(dom.unmatchedText), dom.unmatchedText.slice(0, 80));

  console.log('\n=== 3. 力导向真的跑开了（不是全叠在原点） ===');
  const [minx, maxx, miny, maxy] = dom.spread || [0, 0, 0, 0];
  check('节点在横向上铺开（x 跨度 > 300）', maxx - minx > 300, dom.spread);
  check('节点在纵向上铺开（y 跨度 > 150）', maxy - miny > 150, dom.spread);

  console.log('\n=== 4. 点节点高亮一阶邻居 + 右栏详情 ===');
  const hl = await evaluate(cdp, `(async () => {
    const g = [...document.querySelectorAll('.rg-node')].find(n => (n.querySelector('.rg-label')||{}).textContent === '月神');
    if (!g) return { ok: false };
    g.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    const nodes = [...document.querySelectorAll('.rg-node')];
    return {
      ok: true,
      hl: nodes.filter(n => n.classList.contains('hl')).length,
      dim: nodes.filter(n => n.classList.contains('dim')).length,
      sel: nodes.filter(n => n.classList.contains('sel')).length,
      hlLinks: document.querySelectorAll('.rg-link.hl').length,
      panel: (document.querySelector('.rg-side') || {}).textContent || '',
    };
  })()`);
  console.log('  ' + JSON.stringify({ hl: hl.hl, dim: hl.dim, sel: hl.sel, hlLinks: hl.hlLinks }));
  check('点击后该节点标记为 sel', hl.ok && hl.sel === 1, hl);
  check('一阶邻居数 = 数据层可匹配关系数', hl.hl === exp.yueshenNeighborCount, [hl.hl, exp.yueshenNeighborCount]);
  check('其余节点被淡化', hl.dim > 0, hl.dim);
  check('高亮的边数 = 月神边数', hl.hlLinks === exp.yueshenDegree, [hl.hlLinks, exp.yueshenDegree]);
  check('右栏显示该角色详情', /月神/.test(hl.panel) && /出场/.test(hl.panel), hl.panel.slice(0, 60));
  await shot(cdp, 'rg_2_selected.png');

  console.log('\n=== 5. 拖拽固定 + 缩放 ===');
  const dnd = await evaluate(cdp, `(async () => {
    const g = [...document.querySelectorAll('.rg-node')].find(n => (n.querySelector('.rg-label')||{}).textContent === '露汐');
    const before = g.getAttribute('transform');
    const r = g.querySelector('circle').getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const down = (t, x, y) => g.dispatchEvent(new MouseEvent(t, { bubbles: true, clientX: x, clientY: y, view: window }));
    down('mousedown', cx, cy);
    for (let i = 1; i <= 6; i++) { down('mousemove', cx + i * 16, cy + i * 8); await new Promise(r2 => setTimeout(r2, 25)); }
    down('mouseup', cx + 96, cy + 48);
    await new Promise(r2 => setTimeout(r2, 250));
    const after = g.getAttribute('transform');
    return { before, after, moved: before !== after };
  })()`);
  console.log('  ' + JSON.stringify({ moved: dnd.moved, before: dnd.before, after: dnd.after }));
  check('拖拽后节点位置改变（拖拽固定生效）', dnd.moved, dnd);

  const zoomRes = await evaluate(cdp, `(async () => {
    const svg = document.querySelector('.rg-svg');
    const root = svg.querySelector('g');
    const before = root.getAttribute('transform') || '';
    svg.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: -240, clientX: 300, clientY: 200 }));
    await new Promise(r => setTimeout(r, 350));
    return { before, after: root.getAttribute('transform') || '' };
  })()`);
  check('滚轮缩放改变视图变换', zoomRes.after !== zoomRes.before && /scale/.test(zoomRes.after), zoomRes);
  await shot(cdp, 'rg_3_zoom.png');

  console.log('\n=== 6. 「重新布局」释放固定位置 ===');
  const re = await evaluate(cdp, `(async () => {
    const btn = [...document.querySelectorAll('.rg-mini, .mini')].find(b => b.textContent.trim() === '重新布局');
    if (!btn) return { ok: false };
    btn.click();
    await new Promise(r => setTimeout(r, 900));
    const g = [...document.querySelectorAll('.rg-node')].find(n => (n.querySelector('.rg-label')||{}).textContent === '露汐');
    return { ok: true, transform: g.getAttribute('transform') };
  })()`);
  check('重新布局按钮存在且可点', re.ok, re);
  await sleep(600);

  console.log('\n=== 7. 未匹配清单可展开 ===');
  const up = await evaluate(cdp, `(async () => {
    const btn = [...document.querySelectorAll('.mini')].find(b => /未匹配清单/.test(b.textContent));
    if (!btn) return { ok: false };
    btn.click();
    await new Promise(r => setTimeout(r, 300));
    const rows = [...document.querySelectorAll('.rg-unmatched-row')];
    const hz = rows.find(r => (r.querySelector('b') || {}).textContent === '休止符');
    return { ok: true, rows: rows.length,
             hzRows: rows.filter(r => (r.querySelector('b') || {}).textContent === '休止符').length,
             hzFrom: hz ? hz.textContent.split('、').length : 0,
             text: (document.querySelector('.rg-unmatched') || {}).textContent || '' };
  })()`);
  console.log('  ' + JSON.stringify({ rows: up.rows }));
  check('展开后按名称逐条列出未匹配名（一行一名）', up.ok && up.rows === exp.ghosts,
    [up.rows, exp.ghosts]);
  check('被多处引用的名字只出现一行、且列出全部来源角色',
    up.hzRows === 1 && up.hzFrom >= 2, { rows: up.hzRows, from: up.hzFrom });
  check('清单里提到 alias.json 的解决方式', /alias\.json/.test(up.text), up.text.slice(0, 60));
  await shot(cdp, 'rg_4_unmatched.png');

  console.log('\n=== 8. 不影响其他页签 ===');
  const back = await evaluate(cdp, `(async () => {
    const b = [...document.querySelectorAll('.tabs-sub button')].find(x => x.textContent.trim().startsWith('角色'));
    if (!b) return { ok: false };
    b.click();
    await new Promise(r => setTimeout(r, 600));
    return { ok: true, rows: document.querySelectorAll('.setting-row').length,
             graphGone: document.querySelectorAll('.rg-node').length };
  })()`);
  check('切回「角色」页签仍正常渲染列表', back.ok && back.rows > 0, back);
  check('关系图已卸载（不残留 SVG 节点）', back.graphGone === 0, back.graphGone);

  const errs = await evaluate(cdp, `window.__errs || []`).catch(() => []);
  console.log('\n页面 JS 错误: ' + JSON.stringify(errs));
  check('无未捕获 JS 错误', !errs || errs.length === 0, errs);

  const fails = results.filter((r) => !r.ok);
  console.log('\n' + '='.repeat(64));
  console.log('合计: %d 通过 / %d 失败', results.length - fails.length, fails.length);
  if (fails.length) console.log('失败项: ' + fails.map((f) => f.name).join('、'));
  console.log('='.repeat(64));

  await stop();
  fs.rmSync(profile, { recursive: true, force: true });
  process.exit(fails.length ? 1 : 0);
})().catch(async (e) => {
  console.error('FAIL', e);
  if (global.__chrome) { try { global.__chrome.kill(); } catch (_) { /* noop */ } }
  process.exit(1);
});
