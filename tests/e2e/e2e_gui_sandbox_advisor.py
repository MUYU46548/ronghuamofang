# -*- coding: utf-8 -*-
"""GUI 新增界面真机验收（CDP，非 mock）。

验证三件事，每件都要有**可复验的证据**（截图 + 提取到的真实文本）：
  1. 「审核」页签存在，且徽标数 = 后端待审数
  2. 审核页签能把队列渲染出来，且「通过」按钮真的改了后端状态
  3. 大纲页签「趋势 / 建议」抽屉能打开，且展示 trend 表与 advise 方案

做法：临时项目根 → 起真实 nf_api（--allow-fake）→ 预置沙盒产物与大纲历史 →
vite preview 起前端（注入 __NF_API_BASE__）→ CDP 驱动 headless Chrome。

用法：python tests/e2e/e2e_gui_sandbox_advisor.py
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = ROOT / ".venv" / "Scripts" / "node.exe"
if not NODE.exists():
    NODE = Path(shutil.which("node") or "node")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(base, method, path, body=None, timeout=30):
    data = json.dumps(body or {}, ensure_ascii=False).encode() if method == "POST" else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, str(e)


OUTLINE_BAD = """# 《测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
（无）

## 预计章节数
1

## 章节规划
（无）
"""

OUTLINE_GOOD = """# 《测试书》整体大纲

## 起
露汐在沙都医院发现匿名信，决定独自追查寄信人的身份，与守卫发生冲突。

## 承
线索指向旧档案室，露汐与阿砚在雨夜潜入，撞见管理员正在焚毁卷宗。

## 转
真相揭开：匿名信出自她已故的母亲，信中藏着沙都供水改道的秘密。

## 合
露汐在议会上公开证据，代价是失去唯一的栖身之所，但她把秘密交给了值得的人。

## 关键节点
- 露汐在沙都医院发现匿名信，决定追查寄信人。
- 露汐与阿砚潜入旧档案室，撞见管理员焚毁卷宗。
- 母亲遗信揭开沙都供水改道的秘密。
- 露汐在议会上公开证据，失去栖身之所。

## 预计章节数
12

## 章节规划
- 第1章 匿名信：露汐在医院值夜时拾到一封没有署名的信。
- 第2章 夜探档案室：与阿砚潜入，撞破焚毁卷宗的场景。
- 第3章 母亲的笔迹：辨认出笔迹，动摇对父亲之死的认知。
- 第4章 供水改道：查到工程账目与议会的关联。
- 第5章 议会交锋：公开证据，付出代价。
"""


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_gui_sb_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "data" / "outline" / "history").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "露汐", "aliases": [], "description": "女主"},
                       {"name": "阿砚", "aliases": [], "description": "同伴"}],
        "world": [], "plot_fragments": [], "timeline": [],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "history" / "global_v1.md").write_text(
        OUTLINE_BAD, encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        OUTLINE_GOOD, encoding="utf-8")
    # 预置两份沙盒产物：一份待审（会被点「通过」）、一份待审（答辩留证）
    subprocess.run([str(PY), "-c",
                    "import sys; sys.path.insert(0,'scripts');"
                    "from obsidian_bridge import write_sandbox as w;"
                    "print(w('词条_露汐.md', '# 露汐\\n\\n沙都医院的夜班护士。\\n', kind='entry'));"
                    "print(w('词条_阿砚.md', '# 阿砚\\n\\n档案室的旧识。\\n', kind='entry'))"],
                   cwd=str(tmp), capture_output=True, text=True, encoding="utf-8")
    return tmp


def main():
    if not Path(CHROME).exists():
        print("SKIP: 未找到 Chrome，无法做真机验收（%s）" % CHROME)
        return 0

    tmp = build_project()
    api_port = free_port()
    web_port = free_port()
    dist = ROOT / "console" / "renderer" / "dist"
    if not (dist / "index.html").exists():
        print("FAIL: 未找到前端构建产物 %s（先 npm run build）" % dist)
        return 1

    logf = open(str(tmp / "api.log"), "wb")
    api = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                            "--port", str(api_port), "--allow-fake"],
                           cwd=str(tmp), stdout=logf, stderr=subprocess.STDOUT)
    web = None
    chrome = None
    try:
        base = "http://127.0.0.1:%d" % api_port
        for _ in range(80):
            if req(base, "GET", "/health")[0] == 200:
                break
            time.sleep(0.25)
        else:
            print("FAIL: nf_api 未起来")
            return 1

        # 造一个 index.html 包装：注入 __NF_API_BASE__ 指向临时后端
        served = tmp / "web"
        shutil.copytree(dist, served)
        (served / "index.html").write_text(
            (dist / "index.html").read_text(encoding="utf-8").replace(
                "<head>", '<head>\n  <script>window.__NF_API_BASE__="%s";</script>' % base, 1),
            encoding="utf-8")

        web = subprocess.Popen(
            [str(NODE), "-e",
             "const http=require('http'),fs=require('fs'),p=require('path');"
             "const R=%r;" % str(served) +
             "const M={'.html':'text/html','.js':'text/javascript','.css':'text/css'};"
             "http.createServer((q,s)=>{let f=p.join(R,q.url==='/'?'index.html':q.url.split('?')[0]);"
             "if(!fs.existsSync(f)||fs.statSync(f).isDirectory()){s.writeHead(404);return s.end('nf');}"
             "s.writeHead(200,{'Content-Type':M[p.extname(f)]||'application/octet-stream'});"
             "fs.createReadStream(f).pipe(s);}).listen(%d,'127.0.0.1');" % web_port],
            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)

        url = "http://127.0.0.1:%d/" % web_port
        shot_dir = ROOT / "Temp" / "gui_verify_advisor"
        shot_dir.mkdir(parents=True, exist_ok=True)

        # CDP 驱动：复用仓库既有脚本（含逐页签点击 + 控制台报错收集）
        cdp_port = free_port()
        chrome = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
             "--remote-debugging-port=%d" % cdp_port,
             "--window-size=1600,1000", "--user-data-dir=" + str(tmp / "cdp"),
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)

        # 预置后的**基准状态**必须在 probe 动手之前取，否则断言的是被改过的状态
        code, q0 = req(base, "GET", "/sandbox/queue")
        check("后端待审数初始为 2（probe 动手前）",
              (q0.get("stats") or {}).get("pending") == 2, q0.get("stats"))

        probe = tmp / "probe.js"
        probe.write_text(PROBE_JS, encoding="utf-8")
        r = subprocess.run([str(NODE), str(probe), url, str(cdp_port),
                            str(shot_dir), base],
                           capture_output=True, text=True, encoding="utf-8", timeout=180)
        print(r.stdout or "")
        if r.stderr:
            print("[probe stderr]", r.stderr[-1200:])
        rep_file = shot_dir / "report.json"
        rep = json.loads(rep_file.read_text(encoding="utf-8")) if rep_file.exists() else {}

        print("\n=== 断言（基于页面真实渲染结果 + 后端状态回读）===")
        check("「审核」徽标数 = 初始待审数（2，页面加载时抓的）",
              str(rep.get("badge")) == "2", rep.get("badge"))
        check("页面顶部能看到「审核」页签", rep.get("tabExists") is True,
              rep.get("tabText"))
        check("审核页签渲染出队列（含两份产物路径）",
              "词条_露汐.md" in (rep.get("sandboxText") or "")
              and "词条_阿砚.md" in (rep.get("sandboxText") or ""),
              (rep.get("sandboxText") or "")[:220])
        check("页面明示「不会写入 Obsidian」的边界",
              "不会写入" in (rep.get("sandboxText") or "")
              or "绝不" in (rep.get("sandboxText") or "")
              or "手工完成" in (rep.get("sandboxText") or ""),
              (rep.get("sandboxText") or "")[:220])
        # 注意：断言必须打「点击成功（rc=0）」与「后端确实变了」两件事，
        # 只判 rc 会漏掉「点了但后端没动」，只判后端会漏掉「自己发生了什么都不知道」。
        code, q1 = req(base, "GET", "/sandbox/queue?all=1")
        st1 = q1.get("stats") or {}
        check("点「通过」后后端状态真的变了（approved=1, pending=1）",
              rep.get("approvedCount") == 0
              and st1.get("approved") == 1 and st1.get("pending") == 1,
              (rep.get("approvedCount"), st1, q1.get("items")))
        check("顶部徽标随动作降到 1", str(rep.get("badgeAfter")) == "1",
              rep.get("badgeAfter"))

        code, t = req(base, "GET", "/outline/trend")
        code, a = req(base, "GET", "/outline/advise")
        check("趋势抽屉能打开并显示收敛判定",
              rep.get("advOpen") is True
              and (rep.get("advText") or "").strip() != "",
              (rep.get("advText") or "")[:200])
        check("抽屉里出现迭代趋势表（含「单版体检」列）",
              "单版体检" in (rep.get("advText") or "")
              or "迭代趋势" in (rep.get("advText") or ""),
              (rep.get("advText") or "")[:250])
        check("抽屉里出现「接下来可以做什么」与候选方案",
              "接下来可以做什么" in (rep.get("advText") or ""),
              (rep.get("advText") or "")[:250])
        check("推荐方案与后端一致（%s）" % a.get("recommended"),
              rep.get("advRecommended") == a.get("recommended"),
              (rep.get("advRecommended"), a.get("recommended")))
        check("无 JS 控制台报错", not rep.get("errors"), rep.get("errors"))

        shots = sorted(shot_dir.glob("*.png"))
        check("产出验收截图（可人工复核）", len(shots) >= 3,
              [p.name for p in shots])
        print("\n截图目录: %s" % shot_dir)

        print("=" * 62)
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: " + "、".join(FAIL))
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        for p in (chrome, web, api):
            if p is None:
                continue
            try:
                p.terminate()
                p.wait(timeout=6)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        logf.close()
        shutil.rmtree(tmp, ignore_errors=True)


PROBE_JS = r"""
// CDP 探针：打开页面 → 点「审核」→ 点「通过」→ 点大纲「趋势 / 建议」→ 抓文本与截图
const http = require('http');
const fs = require('fs');
const path = require('path');

const BASE_URL = process.argv[2];
const CDP_PORT = process.argv[3];
const OUT = process.argv[4];
const API = process.argv[5];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b=''; r.on('data',d=>b+=d); r.on('end',()=>{
      try { res(JSON.parse(b)); } catch(e){ rej(new Error(b.slice(0,120))); } }); })
      .on('error', rej);
  });
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.errors = [];
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      } else if (m.method === 'Runtime.exceptionThrown') {
        this.errors.push(String(m.params?.exceptionDetails?.text || '').slice(0, 160));
      } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        this.errors.push((m.params.args || []).map(a => a.value).join(' ').slice(0, 160));
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async evalJS(expr) {
    const r = await this.send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  }
  async shot(file) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
  }
}

(async () => {
  const rep = { errors: [] };
  let targets = [];
  for (let i = 0; i < 60; i++) {
    try { targets = (await getJson(`http://127.0.0.1:${CDP_PORT}/json/list`)).filter(t => t.type === 'page'); }
    catch (e) { targets = []; }
    if (targets.length) break;
    await sleep(300);
  }
  if (!targets.length) { console.log('CDP: 无可用 page target'); process.exit(2); }

  const WebSocket = globalThis.WebSocket;
  const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
  await new Promise((res) => ws.addEventListener('open', res));
  const cdp = new CDP(ws);
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');

  await cdp.send('Page.navigate', { url: BASE_URL });
  await sleep(4000);

  const txt = (sel) => cdp.evalJS(`document.querySelector(${JSON.stringify(sel)})?.innerText || ''`);

  // ---- 1. 审核页签存在 + 徽标数 ----
  rep.tabText = await cdp.evalJS(
    "[...document.querySelectorAll('nav.tabs button')].map(b=>b.textContent.trim()).join(' | ')");
  rep.tabExists = /审核/.test(rep.tabText);
  rep.badge = await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('nav.tabs button')].find(x => x.textContent.includes('审核'));
    if (!b) return null;
    const s = b.querySelector('.badge');
    return s ? s.textContent.trim() : null;
  })()`);

  await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('nav.tabs button')].find(x => x.textContent.includes('审核'));
    if (b) b.click();
  })()`);
  await sleep(2500);
  await cdp.shot(path.join(OUT, '01_审核队列.png'));
  rep.sandboxText = await txt('#app');

  // ---- 2. 点第一份产物的「通过」 ----
  rep.approvedCount = await cdp.evalJS(`(() => {
    const items = [...document.querySelectorAll('.sb-item')];
    const first = items.find(x => x.classList.contains('sb-pending'));
    if (!first) return -1;
    const btn = [...first.querySelectorAll('button')].find(b => b.textContent.trim() === '通过');
    if (!btn) return -2;
    btn.click();
    return 0;
  })()`);
  await sleep(2500);
  await cdp.shot(path.join(OUT, '02_审核_通过后.png'));
  rep.badgeAfter = await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('nav.tabs button')].find(x => x.textContent.includes('审核'));
    const s = b && b.querySelector('.badge');
    return s ? s.textContent.trim() : '0';
  })()`);
  rep.sandboxTextAfter = await txt('#app');

  // ---- 3. 大纲页签「趋势 / 建议」 ----
  await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('nav.tabs button')].find(x => x.textContent.trim() === '大纲');
    if (b) b.click();
  })()`);
  await sleep(2500);
  rep.advOpen = await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('button')].find(x => /趋势\\s*\\/\\s*建议/.test(x.textContent));
    if (!b) return false;
    b.click();
    return true;
  })()`);
  await sleep(3500);
  await cdp.shot(path.join(OUT, '03_大纲_趋势建议.png'));
  rep.advText = await cdp.evalJS(
    "(document.querySelector('.drawer-mask .drawer')||{}).innerText || ''");
  // 取 data-opt-id（机器可比的稳定契约）而不是标题文案 —— 文案会变，id 不会
  rep.advRecommended = await cdp.evalJS(
    "document.querySelector('.ov-opt.rec')?.dataset.optId || null");
  await cdp.evalJS(`(() => {
    const b = [...document.querySelectorAll('.drawer-head button')].find(x => x.textContent.trim() === '关闭');
    if (b) b.click();
  })()`);
  await sleep(600);
  rep.errors = cdp.errors.filter(e => e && !/favicon/i.test(e));

  fs.writeFileSync(path.join(OUT, 'report.json'),
                   JSON.stringify(rep, null, 1), 'utf-8');
  console.log('probe OK: tab=' + rep.tabExists + ' badge=' + rep.badge
              + ' approveRc=' + rep.approvedCount + ' badgeAfter=' + rep.badgeAfter
              + ' advOpen=' + rep.advOpen + ' rec=' + rep.advRecommended);
  process.exit(0);
})().catch((e) => { console.log('probe FAILED: ' + (e && e.message)); process.exit(3); });
"""


if __name__ == "__main__":
    sys.exit(main())
