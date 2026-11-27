# -*- coding: utf-8 -*-
"""UX 增强的真机视觉验收（Playwright + headless Chromium）。

为什么要有它：按钮"看着有、点了没反应"是这类 GUI 最常见的翻车点，只跑 HTTP 冒烟测不出来。
本脚本把构建好的 renderer/dist 用静态服务打开，用真实浏览器逐项点击/按键，截图存
Temp/gui_verify/ux/，同时收集 console error 与非 2xx 请求。

两个后端：
  - 假后端（tests/e2e/mock_nf_api_state.py，8798）：确定性喂\u300c阶段3 失败 + 阶段4 已跳过 + 待重写章节\u300d，
    验证错误恢复一级入口、跳过徽标、待重写徽标、跳过确认框。
  - 真后端（scripts/nf_api.py --port 8799）：真数据（1-4 已完成），验证工作流条、命令面板、
    Space→预估确认框、数字键切页签、日志面板。

用法：python tests/e2e_ux_verify.py

⚠️ 真机验收，**不是**离线自动化用例：需先 `pip install playwright` 并
`playwright install chromium`，还要起静态服务（8091）+ mock/真后端。
缺依赖时**干净跳过**（打印 SKIP + 0 退出），不抛 ModuleNotFoundError ——
按项目纪律，**缺依赖/缺外部服务 = 未执行 ≠ 失败**（见 MEMORY.md）。
"""
import io
import json
import os
import sys

import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收，不是离线用例。")
    print("       启用：python -m pip install playwright && playwright install chromium")
    print("       另需起静态服务 127.0.0.1:8091 与后端 8798/8799（详见本文件 docstring）。")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "ux"
OUT.mkdir(parents=True, exist_ok=True)
# 地址默认值与历史一致；`run_all.py` 会用环境变量换成它挑好的空闲端口
# （固定端口撞上本机常驻服务时，不许抢端口、也不许杀别人的进程）。
SITE = os.environ.get("NF_E2E_SITE") or "http://127.0.0.1:8091"
MOCK_API = os.environ.get("NF_E2E_MOCK") or "http://127.0.0.1:8798"
COLD_API = os.environ.get("NF_E2E_COLD") or "http://127.0.0.1:8797"   # 同一个 mock 的 --cold 实例（全新工作区）
REAL_API = os.environ.get("NF_E2E_REAL") or "http://127.0.0.1:8799"
# GUI 双因子的第二因子（可选）。设了它，new_page 就会把同值注入前端垫片，
# 与后端 spawn 时的 NF_GUI_TOKEN 配对 —— 归档删除 / 止烧阈值这类 `_gui_only`
# 端点才在验收环境里走得通。不设 = 与旧行为一致（这些端点仍然 403）。
GUI_TOKEN = ""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))

# ---- preload 垫片（纯浏览器跑 Electron 前端所必需）----
# 前端在 Electron 里通过 preload 拿到 window.mofangAPI（console/preload/index.js，
# contextBridge 暴露）。本验收用 headless Chromium，没有 preload →
# window.mofangAPI 为 undefined，App.vue 的 4 处
# `window.mofangAPI.readPreview(...)` 直接抛
# "Cannot read properties of undefined (reading 'readPreview')"，
# 页面只剩骨架（2026-09-29 实测：整个验收卡死在第二条断言）。
#
# 垫片按 preload 的**同名同形**契约补齐，让前端走它的正常错误分支。
#
# ⚠️ 已知覆盖缺口（有意为之，不是遗漏）：
#   readPreview 走的是 Electron IPC（主进程读工作区文件，带白名单），
#   浏览器侧**没有等价通道**。所以这里返回 {ok:false} —— 好处是零侵入、零 console 噪音；
#   代价是“文件预览”这条路径在浏览器验收里**不被覆盖**（它只在真机 Electron 下生效）。
#   若要真正覆盖，需要给浏览器加一层文件桥（例如带 CORS 头的只读静态服务），
#   那属于验收基建的独立议题，别顺手塞进本脚本。
PRELOAD_SHIM_JS = """
const readPreviewFallback = async (p) => (p === "prompts/agent_kickoff.md"
    ? { ok: true, size: 64, content: "# kickoff\\n\\n项目根：{{PROJECT_ROOT}}\\n\\n禁止从头重建本项目。\\n" }
    : { ok: false, error: "e2e harness: 无文件桥（readPreview 是 Electron 独有 IPC）" })
window.mofangAPI = window.mofangAPI || {
  // GUI 双因子的第二因子（2026-10-06 加固）：真机由 Electron 主进程经 IPC 签发。
  // 验收环境没有主进程 → 由 new_page 注入 window.__NF_GUI_TOKEN__（与后端
  // NF_GUI_TOKEN 同值）。不给这个值的话，_gui_only 类端点（归档删除、止烧阈值）
  // 在浏览器垫片下**永远 403**，看着像前端 bug（e2e_new_features 踩过）。
  getGuiToken: async () => window.__NF_GUI_TOKEN__ || "",
  // 2026-11-06（B7）：给章节文件一条假文件桥 —— 没有它，审阅台左栏永远空着，
  // 中栏编辑器根本不渲染，**审阅台的列宽/行宽无从验收**（真缺陷会一直躲在这里）。
  _nfChapterText: (n) => "## 第" + n + "章 验收章" + String.fromCharCode(10, 10)
    + Array.from({ length: 40 }, () => "他推门进来的时候，屋檐上的水线正连成了串，"
      + "地上积了一小滩暗色的水。她停下脚步，侧过脸朝窗外的天色看了一眼，随即收回视线。")
      .join(String.fromCharCode(10, 10)),
  readPreview: async (p) => {
    const _m = p.match(/^data\/chapters\/(raw|checked|refined)\/(\d+)\.md$/);
    if (_m) {
      return { ok: true, size: 2048,
               content: window.mofangAPI._nfChapterText(Number(_m[2])) };
    }
    return readPreviewFallback(p);
  },
  openArtifact: async () => ({ ok: true }),
  openFile: async () => ({ ok: true }),
  revealInFolder: async () => ({ ok: true }),
  openFileDialog: async () => ({ ok: false, canceled: true }),
  promptsList: async () => ({ ok: true, items: [] }),
  promptsGet: async () => ({ ok: true, content: "" }),
  promptsSave: async () => ({ ok: true }),
  styleNotesGet: async () => ({ ok: true, content: "" }),
  styleNotesSave: async () => ({ ok: true }),
  openExternal: async () => ({ ok: true }),
  appAbout: async () => ({ ok: true }),
  updaterCheck: async () => ({ ok: true }),
  updaterStatus: async () => ({ ok: true }),
  updaterQuitAndInstall: async () => ({ ok: true }),
  onUpdater: () => () => {},
  // ---- 接入其他 Agent（agent-connect:* IPC 的等价假实现）----
  agentConnectInfo: async () => ({
    ok: true, packaged: false, version: "0.0.0-e2e",
    pythonCmd: "C:\\\\fake\\\\repo\\\\.venv\\\\Scripts\\\\python.exe",
    bridgePath: "C:\\\\fake\\\\repo\\\\scripts\\\\nf_mcp_stdio_bridge.py",
    hermesConfig: "C:\\\\fake\\\\AppData\\\\Local\\\\hermes\\\\config.yaml",
    claudeConfig: "C:\\\\fake\\\\.claude.json",
    clineConfig: "C:\\\\fake\\\\cline_mcp_settings.json",
    yamlSnippet: "mcp_servers:\\n  novelforge:\\n    command: C:\\\\fake\\\\repo\\\\.venv\\\\Scripts\\\\python.exe\\n    args:\\n      - C:\\\\fake\\\\repo\\\\scripts\\\\nf_mcp_stdio_bridge.py\\n    enabled: true",
    jsonSnippet: "{\\n  \\"mcpServers\\": {\\n    \\"novelforge\\": {\\n      \\"command\\": \\"C:\\\\\\\\fake\\\\\\\\repo\\\\\\\\.venv\\\\\\\\Scripts\\\\\\\\python.exe\\",\\n      \\"args\\": [\\n        \\"C:\\\\\\\\fake\\\\\\\\repo\\\\\\\\scripts\\\\\\\\nf_mcp_stdio_bridge.py\\"\\n      ]\\n    }\\n  }\\n}",
    mcpListening: false,
  }),
  agentOpenConfig: async () => ({ ok: true, path: "C:\\\\fake\\\\hermes\\\\config.yaml", existed: true }),
  // ---- 诊断与调试（debug:* IPC 的等价假实现）----
  debugInfo: async () => ({
    ok: true, version: "0.0.0-e2e", packaged: false, platform: "win32",
    electron: "28.3.3", node: "18",
    logPath: "C:\\\\fake\\\\repo\\\\Temp\\\\nf_api_child.log", logExists: true,
    codeRoot: "C:\\\\fake\\\\repo", projectRoot: "C:\\\\fake\\\\repo",
    projectRootSource: "default", apiBase: "http://127.0.0.1:8798",
    apiChildPid: 4242, apiHealthy: true, devtoolsOpen: false,
  }),
  debugOpenLog: async () => ({ ok: true, path: "C:\\\\fake\\\\repo\\\\Temp\\\\nf_api_child.log" }),
  debugToggleDevtools: async () => ({ ok: true, open: true }),
  debugRestartApi: async () => ({ ok: true, healthy: true }),
};
"""


def new_page(browser, api_base, tag):
    ctx = browser.new_context(viewport={"width": 1440, "height": 940})
    ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(api_base))
    if GUI_TOKEN:
        ctx.add_init_script("window.__NF_GUI_TOKEN__ = %s;" % json.dumps(GUI_TOKEN))
    ctx.add_init_script(PRELOAD_SHIM_JS)
    page = ctx.new_page()
    errs, bad = [], []
    page.on("console", lambda m: errs.append(m.type + ": " + m.text[:200])
            if m.type in ("error", "warning") else None)
    # 失败请求**带上 URL**一起收：否则 console 里只剩
    # "Failed to load resource: net::ERR_CONNECTION_REFUSED"，看不出是哪个端口被拒
    # （2026-09-29 排查这条时正是卡在这里，只能另写探针脚本）。
    page.on("requestfailed", lambda r: errs.append(
        "requestfailed: %s | %s" % (r.url, r.failure)))
    page.on("response", lambda r: bad.append("%s %s" % (r.status, r.url))
            if r.status >= 400 and "/favicon" not in r.url else None)
    page.goto(SITE, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    settle_disclaimer(page)   # 首启强制免责声明：全新 profile 必弹，先验后放行
    return ctx, page, errs, bad


def goto_chapter_list(page):
    """点“章节”页签，并确保落在**列表视图**（2026-11-26）。

    章节页默认进“审阅台”（App.vue 的 chapterView 默认 desk），而候选面板 /
    本章体检 / 成书提炼 / 讨论箱都挂在列表视图的 v-else 分支里。不切回来，
    这些选择器恒为 0 个 → 后续 click 等满 30 秒超时，看着像按钮坏了。
    """
    page.locator("nav.tabs button", has_text="章节").first.click()
    page.wait_for_timeout(900)
    back = page.locator("button:has-text('回到列表')")
    if back.count():
        back.first.click()
        page.wait_for_timeout(700)


def _ensure_desk_edit(page):
    """审阅台默认是**阅读**模式，编辑器 `.ce-textarea` 只在“编辑”下渲染（2026-11-26）。

    不切过去，旧用例的 `wait_for_selector(".ce-textarea")` 会等满超时 ——
    看着像审阅台坏了，其实只是模式不对。
    """
    if page.locator(".ce-textarea").count():
        return
    # 按钮名要精确等于“编辑”：窄窗口下还有“编辑本章大纲”之类含同样两字的按钮，
    # 子串匹配会点错一个，编辑器永远不出现（2026-11-26 在 980 宽复现）。
    for nm in ("编辑", "编辑正文"):
        btn = page.get_by_role("button", name=nm, exact=True)
        if btn.count():
            try:
                btn.first.click()
                page.wait_for_timeout(500)
            except Exception:                                   # noqa: BLE001
                pass
            return


# 截图自检台账：每条 = {name, ok, notes}。e2e 结束时统一打印，
# 有问题**不阻断**（截图是辅助证据），但必须出声 —— 带偏差的截图比没有更糟。
SHOT_CHECKS = []
# 2026-11-20：被挤出视口右缘的元素（只收集、先不判定 —— 见 _overflow_probe）。
OVERFLOW_PROBE = []


def _geom(page):
    """读关键固定元素的几何（顶栏是最好认的破绽）。"""
    try:
        return page.evaluate("""() => {
          const tb = document.querySelector('.topbar');
          const doc = document.documentElement;
          return {
            scrollY: Math.round(window.scrollY || 0),
            vw: Math.round(window.innerWidth),
            vh: Math.round(window.innerHeight),
            scrollH: Math.round(doc.scrollHeight),
            topbar: tb ? (() => {
              const r = tb.getBoundingClientRect();
              const cs = getComputedStyle(tb);
              return {y: Math.round(r.y), h: Math.round(r.height),
                      w: Math.round(r.width), position: cs.position};
            })() : null,
          };
        }""")
    except Exception:                                        # noqa: BLE001
        return None


_OVERFLOW_PROBE_JS = r"""
() => {
  // 视口右边界外的可见元素 = 用户点不到 / 看不全的东西（2026-11-20）。
  // 起因：1366 宽下章节编辑器的保存按钮被挤出窗口只露半个紫块，而 346 条 e2e 全绿
  // —— 因为没有任何一条判据量过"元素整体在不在屏幕里"。
  // 误报控制：
  //   · 跳过 position:fixed（toast / 浮层本来可能出屏再动画进来）
  //   · 跳过"祖先可横向滚动"的（那是设计好的横向滚动区，伸出去正常）
  //   · 跳过自己就是滚动容器的（它的子元素超出由滚动承接）
  const W = window.innerWidth;
  const out = [];
  const scrollable = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (/(auto|scroll)/.test(s.overflowX) && a.scrollWidth > a.clientWidth + 1) return true;
    }
    return false;
  };
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const s = getComputedStyle(el);
    if (s.position === 'fixed' || s.visibility === 'hidden' || s.display === 'none') continue;
    // 背景柔光斑（.blob）故意 `right:-60px` 出界，是装饰，不是可点/可读的东西 ——
    // 不排除它会让判据永远红，等于没有判据。
    if (String(el.className || '').indexOf('blob') >= 0) continue;
    if (r.right <= W + 2) continue;
    if (scrollable(el)) continue;
    const label = (el.textContent || '').trim().slice(0, 16)
                  || (el.className && String(el.className).slice(0, 24)) || el.tagName;
    out.push({ el: label, tag: el.tagName, over: Math.round(r.right - W) });
  }
  // 同一段文字会被父子重复报（外层容器也超），按 label 去重、只留溢出最多的
  const seen = new Map();
  for (const o of out) {
    const k = o.el;
    if (!seen.has(k) || seen.get(k).over < o.over) seen.set(k, o);
  }
  return [...seen.values()].sort((a, b) => b.over - a.over).slice(0, 8);
}
"""


def _overflow_probe(page, name, notes=None):
    """量"有没有元素被挤出视口右缘"。**是判据，不是诊断**（2026-11-20）。

    为什么必须有它：1366 宽下章节编辑器的保存按钮被挤出窗口右缘只露半个紫块，
    而当时 346 条 e2e 全绿 —— M 段只量了"正文几个字一行"，没有任何一条量过
    "这个东西整体在不在屏幕里"。用户点不到的按钮，比少一个功能更伤人。
    排除项见 JS 里的三行注释（fixed 浮层 / 可横向滚动的容器 / 背景光斑）。
    """
    try:
        bad = page.evaluate(_OVERFLOW_PROBE_JS) or []
    except Exception:                                        # noqa: BLE001
        return []
    if bad:
        OVERFLOW_PROBE.append({"name": name, "bad": bad})
        print("      ⚠️ 出屏 %d 处 → %s" % (
            len(bad), "；".join("%s(+%dpx)" % (b["el"], b["over"]) for b in bad[:3])))
        if notes is not None:
            notes.append("元素出屏: " + "；".join(
                "%s(+%dpx)" % (b["el"], b["over"]) for b in bad[:3]))
    return bad


def shot(page, name, full=False, keep_scroll=False):
    """截图到 Temp/gui_verify/ux/，**并自检这张图能不能作为证据用**。

    ## 为什么截图需要自检（2026-10-31 用户指出）

    我给用户看的全页截图里，"绒花墨坊"顶栏被画在了内容中间 —— 用户以为界面坏了。
    根因：`.topbar` 是 `position: sticky`，而 `screenshot(full_page=True)` 会把视口
    临时拉高到整页高度渲染，sticky 元素却停在"当前滚动位置"的吸附位。
    **界面没问题，是交付证据带系统偏差。**凭据不可信比没有更糟，所以加这三道：

    | 判据 | 抓什么 |
    |---|---|
    | A scrollY 必须为 0（全页截图前强制滚回顶部） | sticky 伪影的**唯一成因** |
    | B `.topbar` 顶部 y ∈ [-2, 2] 且高度 > 40 | 顶栏真的在顶端（布局没坏） |
    | C 全页图高 ≈ scrollHeight（±40px） | 渲染没铺开 / 抓在半路 |

    任一不过 → 记进 SHOT_CHECKS 并**降级为视口截图**（宁可截少，不给错图）。
    """
    p = OUT / ("%s.png" % name)
    notes = []
    y0 = 0
    # 2026-11-03：只重置 window 滚动**不够**。实测 A2/A3/A4/A5 顶栏在 y=-47：
    # 那些面板是在**内部滚动容器**里滚的（点完东西后停在半路），而 `.topbar` 是
    # sticky —— 它跟着内层容器走，window.scrollTo(0,0) 一点用没有，截出来的图
    # 顶栏被切掉半行（用户会以为界面坏了）。
    # ⚠️ 但"故意滚到可见再拍"的调用方要传 keep_scroll=True（D9/D10 那两处），
    # 否则会把刚滚好的块又滚回去，后续几何断言就判成"不在视口内"。
    if not keep_scroll:
        try:
            page.evaluate(
                "() => {"
                "  window.scrollTo(0, 0);"
                "  document.querySelectorAll('*').forEach(el => {"
                "    try { if (el.scrollTop) el.scrollTop = 0;"
                "          if (el.scrollLeft) el.scrollLeft = 0; } catch (e) {}"
                "  });"
                "}"
            )
            page.wait_for_timeout(240)
        except Exception:                                    # noqa: BLE001
            notes.append("重置滚动容器失败")
        g = _geom(page)
        y0 = (g or {}).get("scrollY") or 0
        if y0:
            # A：全页截图前必须回到顶部，否则 sticky 顶栏会被画在内容中间
            try:
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(280)
            except Exception:                                # noqa: BLE001
                notes.append("scrollTo(0,0) 执行失败")
            g2 = _geom(page)
            if (g2 or {}).get("scrollY", 1) not in (0, None):
                notes.append("滚回顶部没成功（scrollY=%s）" % (g2 or {}).get("scrollY"))
    g = _geom(page)
    _overflow_probe(page, name, notes)
    if g and g.get("topbar"):
        tb = g["topbar"]
        # B：顶栏必须在视口顶端，且不是被挤成一条细线
        if not (-2 <= tb["y"] <= 2):
            notes.append("顶栏不在顶端（y=%s）" % tb["y"])
        if tb["h"] <= 40:
            notes.append("顶栏高度异常（h=%s）" % tb["h"])
    downgrade = any(("滚回顶部没成功" in n) or ("顶栏" in n) for n in notes)
    use_full = full and not downgrade
    if downgrade and full:
        notes.append("已降级为视口截图")

    try:
        page.screenshot(path=str(p), full_page=use_full)
    except Exception as e:                                   # noqa: BLE001
        notes.append("截图失败: %s" % (str(e)[:80],))
        use_full = False

    # C：全页图的尺寸应接近整页高度（用 PIL 不可用，读 PNG 头拿宽高，零依赖）
    if use_full and p.exists():
        try:
            with open(p, "rb") as f:
                head = f.read(33)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                h = int.from_bytes(head[20:24], "big")
                if g and g.get("scrollH") and abs(h - g["scrollH"]) > 60:
                    notes.append("图高 %d 与整页高 %d 差太多" % (h, g["scrollH"]))
        except Exception:                                    # noqa: BLE001
            pass

    if y0 and (use_full or downgrade):
        try:
            page.evaluate("window.scrollTo(0, %d)" % y0)
            page.wait_for_timeout(140)
        except Exception:                                    # noqa: BLE001
            pass

    SHOT_CHECKS.append({"name": name, "ok": not notes, "notes": notes})
    if notes:
        print("      shot → %s  ⚠️ 自检: %s" % (p.name, "；".join(notes)))
    else:
        print("      shot → %s" % p.name)
    return p


# 免责声明首启门只在第一个上下文跑行为断言（后续上下文走同一放行流即可）
_DISCLAIMER_VERIFIED = [False]


def settle_disclaimer(page, require_first_run=True):
    """首启免责声明强制弹窗（2026-10-05 · DisclaimerDialog A/B 双版）的处理。

    全新浏览器 profile 没有 `mofang_disclaimer_ack_v*` → App.vue onMounted 弹出
    first-run 真锁模态（z-60 压全抽屉、焦点困于弹窗内），不处理会把后续所有点击拦死
    （2026-10-05 实测：卡在\u300c跳过确认框\u300d，报 disclaimer-mask intercepts pointer events）。
    这里**先跑该门的行为断言（仅首次），再走完真实勾选流放行**——不是绕过，是把这道闸
    纳入真机验收；勾选流本身也验证了\u300c滚到底才解锁 → 同意 → POST /license/ack 落地
    → 本地留一份显示用时间戳\u300d。

    2026-11-18：弹不弹由**服务端** `GET /license` 说了算（之前只看 localStorage）。
    因此同一后端进程里签过一次之后，后续上下文不再弹 —— 见下方 absence 分支。

    require_first_run（2026-11-27）：**同一轮 run_all 里跑多个套件时必须传 False**。
    签字是服务端进程内状态，前一个套件签过之后，后一个套件的第一个上下文也不会弹 ——
    那不是“首启门失效”，是同一份 mock 进程的既定语义。踩过的表现：先跑 panels 再跑
    writing-rules，后者日志里冒出一条 [FAIL] 首启强制免责声明弹出，而它记进的是
    e2e_ux_verify 自己的 PASS/FAIL 列表（本套件仍显示 10/0 全绿），极易被误读成产品回归。
    只验一次首启门的套件（如 ux）保持默认 True。
    """
    if page.locator(".disclaimer-mask.forced").count() == 0:
        # 2026-11-18：判据已搬到服务端（`GET /license`）。同一后端进程内签过一次之后，
        # 后续浏览器上下文**哪怕 localStorage 全空**也不再弹 —— 这不是“首启门失效”，
        # 恰恰是新语义：签字是服务端的事，不跟着浏览器 profile 走。
        # 所以只有**第一个**上下文必须弹；之后不弹才算对。
        if _DISCLAIMER_VERIFIED[0] or not require_first_run:
            return True
        check("首启强制免责声明弹出（服务端未签时必弹）",
              False, "首个上下文也没弹（首启门失效，或服务端已残留 ack）")
        return False
    if not _DISCLAIMER_VERIFIED[0]:
        _DISCLAIMER_VERIFIED[0] = True
        check("首启强制免责声明弹出（服务端未签时必弹）", True)
        check("弹窗语义=对话框（role=dialog + aria-modal）",
              page.locator(".disclaimer-dlg[role=dialog][aria-modal=true]").count() == 1)
        agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
        check("\u300c我已阅读并同意\u300d按钮存在且未滚到底前禁用",
              agree.count() == 1 and agree.first.is_disabled(),
              "count=%d disabled=%s" % (agree.count(),
                                        agree.first.is_disabled() if agree.count() else "-"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        check("ESC 无旁路（强制模式弹窗不关）",
              page.locator(".disclaimer-mask").count() == 1)
        shot(page, "E1_disclaimer_first_run")
    # 滚到底 → 解锁勾选 → 同意（与用户真实路径一致）
    page.locator(".disclaimer-body").evaluate("el => { el.scrollTop = el.scrollHeight; }")
    page.wait_for_timeout(350)
    cb = page.locator(".disclaimer-check input")
    agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
    if cb.count() and cb.first.is_enabled() and agree.count():
        cb.first.check()
        page.wait_for_timeout(150)
        agree.first.click()
        page.wait_for_timeout(450)
        # ack 键随 DISCLAIMER_VERSION 走（v1 → v1.1 …），只断言前缀，不断言具体版本号
        check("滚到底后勾选解锁 → 同意生效 → 弹窗关闭且 ack 落 localStorage",
              page.locator(".disclaimer-mask").count() == 0
              and page.evaluate(
                  "() => Object.keys(localStorage).some("
                  "k => k.startsWith('mofang_disclaimer_ack_v'))"))
    else:
        check("滚到底后勾选解锁", False,
              "勾选框=%s 同意按钮=%s" % (cb.count(), agree.count()))
    return True


def test_mock(browser):
    print("=== A. 假后端（8798）：错误恢复 / 跳过 / 待重写 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "mock")
    try:
        check("工作流条渲染（一键工作流 + 7 个步骤节点）",
              page.locator(".flow-strip").count() == 1 and page.locator(".flow-step").count() == 7,
              (page.locator(".flow-strip").count(), page.locator(".flow-step").count()))
        nxt = page.locator(".flow-next b").first.inner_text()
        check("下一步指向审批门（阶段6 已完成未审批）", "确认阶段 6" in nxt, nxt)
        check("步骤条标出了失败阶段（bad 类）", page.locator(".flow-step.bad").count() >= 1,
              page.locator(".flow-step.bad").count())
        check("步骤条标出了跳过阶段（skip 类）", page.locator(".flow-step.skip").count() >= 1,
              page.locator(".flow-step.skip").count())
        check("失败阶段卡片上一级入口可见（重试/跳过/回退/日志四件套）",
              page.locator(".stage-recovery").count() >= 1 and
              page.locator(".stage-recovery button:has-text('重试')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('跳过此阶段')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('回退')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('查看日志')").count() >= 1)
        check("跳过徽标渲染", page.locator(".pill.st-skip").count() >= 1)
        check("待重写章节徽标渲染", page.locator(".pill:has-text('待重写')").count() >= 1)
        ws = page.locator(".ps-eta", has_text="本卷会话")
        ws_text = ws.inner_text() if ws.count() else ""
        check("会话账本显示本卷会话 / 水位 / 上次写到第几章",
              ws.count() == 1 and "122,000" in ws_text and "48.8%" in ws_text
              and "第 3 章" in ws_text and "接力包 1 份" in ws_text, ws_text)
        shot(page, "A1_pipeline_recovery")

        print("  --- 跳过确认框（勾选护栏，不用 window.confirm）---")
        page.locator(".stage-recovery button:has-text('跳过此阶段')").first.click()
        page.wait_for_timeout(400)
        check("跳过确认框弹出", page.locator(".dialog:has-text('跳过阶段')").count() == 1)
        btn = page.locator(".dialog button:has-text('确认跳过')")
        check("未勾选确认 → 按钮禁用（防误点）", btn.is_disabled())
        page.locator(".dialog input[type=checkbox]").check()
        page.wait_for_timeout(200)
        check("勾选后按钮可用", not btn.is_disabled())
        shot(page, "A2_skip_confirm")
        btn.click()
        page.wait_for_timeout(800)
        check("提交后弹框关闭 + 顶部 toast 反馈",
              page.locator(".dialog:has-text('跳过阶段')").count() == 0 and page.locator(".toast").count() == 1,
              page.locator(".toast").inner_text() if page.locator(".toast").count() else "")
        shot(page, "A3_after_skip")

        print("  --- 运行日志面板 ---")
        page.locator(".stage-recovery button:has-text('查看日志')").first.click()
        page.wait_for_timeout(900)
        check("日志面板打开且显示内容",
              page.locator(".log-body").count() == 1 and
              "阶段3 结果" in page.locator(".log-body").inner_text(),
              page.locator(".log-body").inner_text()[:80] if page.locator(".log-body").count() else "")
        shot(page, "A4_run_log")
        page.locator(".dialog button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)

        print("  --- 命令面板（Ctrl+K）---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        check("Ctrl+K 打开命令面板", page.locator(".cmd-palette").count() == 1)
        total = page.locator(".cmd-item").count()
        check("命令数量足够多（含工作流/阶段/页签/恢复/外观）", total > 25, total)
        shot(page, "A5_palette")
        page.fill(".cmd-input", "跳过")
        page.wait_for_timeout(300)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("中文关键词能过滤到\u300c跳过阶段\u300d", any("跳过阶段" in t for t in labels), labels[:4])
        shot(page, "A6_palette_filter")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        check("命令面板 Enter 能执行（打开跳过确认框）",
              page.locator(".cmd-palette").count() == 0 and page.locator(".dialog:has-text('跳过阶段')").count() == 1)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        # Esc 只关弹层，dialog 由自身按钮关：直接点取消
        if page.locator(".dialog:has-text('跳过阶段')").count():
            page.locator(".dialog button:has-text('取消')").first.click()
            page.wait_for_timeout(300)

        print("  --- 快捷键 ---")
        page.keyboard.press("Shift+Slash")
        page.wait_for_timeout(400)
        check("? 打开快捷键说明", page.locator(".dialog:has-text('键盘快捷键')").count() == 1)
        shot(page, "A7_shortcuts")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Esc 关闭快捷键弹层", page.locator(".dialog:has-text('键盘快捷键')").count() == 0)
        page.keyboard.press("9")
        page.wait_for_timeout(600)
        active = page.locator(".tabs button.active").first.inner_text()
        check("数字键 9 → 文风页签", "文风" in active, active)
        page.keyboard.press("1")
        page.wait_for_timeout(600)
        check("数字键 1 → 流水线页签", "流水线" in page.locator(".tabs button.active").first.inner_text())
        shot(page, "A8_back_to_pipeline")

        print("  --- 顶栏\u300c📋 日志\u300d（对齐方寸 顶栏诊断入口）---")
        page.locator("header button:has-text('📋 日志')").click()
        page.wait_for_timeout(900)
        check("顶栏 📋 按钮打开运行日志面板（排障第一入口常驻可见）",
              page.locator(".log-body").count() == 1,
              page.locator(".log-body").count())
        shot(page, "A12_header_log_button")
        page.locator(".dialog button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)

        print("  --- 定价批量导入（预览 → 确认 两段式）---")
        page.locator(".tabs button:has-text('成本')").click()
        page.wait_for_timeout(600)
        page.locator("button:has-text('定价')").first.click()
        page.wait_for_timeout(500)
        check("定价面板展开前没有导入区", page.locator(".rate-import").count() == 0)
        page.locator("button:has-text('批量导入')").first.click()
        page.wait_for_timeout(300)
        check("点\u300c批量导入\u300d展开粘贴框", page.locator(".rate-import-box").count() == 1)
        shot(page, "A9_rate_import_open")

        page.fill(".rate-import-box", "kimi-k2.6  1.0  4.2  0.2\nglm-5.3 8 28 2\n")
        page.locator("button:has-text('解析预览')").click()
        page.wait_for_timeout(600)
        rows = page.locator(".rate-import .cost-table tbody tr")
        check("预览表列出解析出的条目", rows.count() == 2, rows.count())
        preview = page.locator(".rate-import .cost-table").inner_text()
        check("预览表含模型名与新值",
              "kimi-k2.6" in preview and "4.2" in preview, preview.replace("\n", " | ")[:80])
        check("动作列标出\u300c新增\u300d", "新增" in preview, preview.replace("\n", " | ")[:80])
        check("预览不是写入（未出现\u300c已导入\u300d）",
              page.locator(".rate-import:has-text('✅ 已导入')").count() == 0)
        check("解析无错误时\u300c确认导入\u300d可用",
              not page.locator(".rate-import button:has-text('确认导入')").is_disabled())

        page.fill(".rate-import-box", "kimi-k2.6 1.0\n")
        page.locator("button:has-text('解析预览')").click()
        page.wait_for_timeout(600)
        check("坏输入 → 明显报错块（不是静默通过）",
              page.locator(".rate-import-err").count() == 1
              and "只解析出 1 个数值" in page.locator(".rate-import-err").inner_text(),
              page.locator(".rate-import-err").inner_text()[:70])
        check("有解析错误时\u300c确认导入\u300d被禁用",
              page.locator(".rate-import button:has-text('确认导入')").is_disabled())
        shot(page, "A10_rate_import_error")

        page.fill(".rate-import-box", "kimi-k2.6  1.0  4.2  0.2\n")
        page.locator("button:has-text('解析预览')").click()
        page.wait_for_timeout(600)
        page.locator(".rate-import button:has-text('确认导入')").click()
        page.wait_for_timeout(800)
        body_txt = page.locator(".rate-import").inner_text()
        check("确认后报告\u300c已导入 N 条\u300d", "已导入 1 条" in body_txt, body_txt.replace("\n", " | ")[:90])
        shot(page, "A11_rate_import_done")
        page.locator(".tabs button:has-text('流水线')").click()
        page.wait_for_timeout(500)

        errs_real = [e for e in errs if "404" not in e]
        print("  console error/warning: %s" % (errs_real or "无"))
        check("无前端 console 报错（假后端下）", not errs_real, errs_real[:3])
    finally:
        ctx.close()


def real_state():
    """取真后端 /state（供 test_real 推导期望值）。取不到返回 None。"""
    try:
        import urllib.request
        with urllib.request.urlopen(REAL_API + "/state", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def real_next():
    """从 /state 推导 (下一可运行阶段号, 待审批门阶段号)。

    ⚠️ 为什么不写死\u300c阶段 5\u300d（2026-10-03 事故）：
    本脚本原先假定真后端工作区是\u300c1-4 已完成\u300d，断言写死 `下一步 = 运行阶段 5`。
    换成别的工作区状态（例如刚跑完 stage1、停在审批门）之后：
      ① 4 条断言集体**假红**（其实什么都没验）；
      ② 更糟：下面那段 `Space`（= 执行下一步）会**真的把审批按掉** ——
         本轮实测把用户工作区的 `stage1.approved` 改成了 true（已手工还原成"从未审批"）。
    现在：阶段号从 /state 推导；**下一步是审批门时整段跳过 Space**。
    """
    st = real_state() or {}
    gate, run = None, None
    for s in st.get("stages") or []:
        n = int(s.get("stage") or 0)
        if s.get("status") == "done" and not s.get("approved"):
            gate = gate if gate is not None else n
        elif s.get("status") != "done" and run is None:
            run = n
    return run, gate


def test_real(browser):
    print("\n=== B. 真后端（8799）：真数据上的工作流 / 预估 / 日志 ===")
    # 前提一：8799 得在跑。没起时 `real_next()` 拿不到 /state，后面 `.flow-next`
    # 会以 30s 超时的形式炸掉整轮 —— 那是**没执行**，不是产品坏了。
    try:
        with urllib.request.urlopen(REAL_API + "/health", timeout=3) as r:
            alive = r.status == 200
    except Exception:                                        # noqa: BLE001
        alive = False
    if not alive:
        print("  [SKIP] B 段前提不成立：%s 没有在跑（真后端未启动）。" % REAL_API)
        print("         启动：python scripts/nf_api.py --port 8799")
        return
    run_stage, gate_stage = real_next()
    is_gate = bool(gate_stage) and (run_stage is None or gate_stage < run_stage)
    print("  真后端推导：下一可运行阶段=%s · 待审批门=%s" % (run_stage, gate_stage))
    ctx, page, errs, bad = new_page(browser, REAL_API, "real")
    try:
        # 环境前提（2026-11-06 补）：真后端必须指向**已初始化**的书籍工作区。
        # 未初始化（`config/project.yaml` 还是示例书名）时 App 会自动弹
        # “项目初始化向导”—— 它是 `.drawer-mask` 全屏遮罩，后面所有点击都被它吃掉，
        # 表现为 `.flow-quick .qstage` 点击超时（看着像 UI 坏了，其实是前提不成立）。
        # 按项目纪律“未执行 ≠ 失败”：显式跳过并说清怎么补齐前提，不要假装验过。
        _wizard = page.locator(".drawer-mask:has-text('项目初始化向导')")
        if _wizard.count() and _wizard.first.is_visible():
            print("  [SKIP] B 段前提不成立：真后端工作区未初始化"
                  "（首屏弹出“项目初始化向导”遮挡工作流条）。")
            print("         补齐：先在桌面端走完初始化 / 让 config/project.yaml 填真实书名与章节数，"
                  "再跑本脚本。")
            return
        nxt = page.locator(".flow-next b").first.inner_text()
        if is_gate:
            check("真数据：下一步 = 确认阶段 %s（审批门优先）" % gate_stage,
                  ("阶段 %s" % gate_stage) in nxt and "确认" in nxt, nxt)
            sel = page.locator(".flow-quick select").input_value()
            # 审批门状态下 quickStage 为 null → select 显示占位项文本（"选择阶段…"）。
            # 断言放宽到“占位项或某个阶段号”：前者是当前设计，后者是"将来预选下一可运行阶段"
            # 的实现 —— 两种都算健康，写死任一种都会在对方实现下假红。
            check("审批门状态下快速运行选择器停在占位项（不预设阶段）",
                  page.locator(".flow-quick select").count() == 1
                  and ("选择阶段" in str(sel) or str(sel).strip().isdigit()),
                  repr(sel))
        else:
            check("真数据：下一步 = 运行阶段 %s" % run_stage,
                  ("阶段 %s" % run_stage) in nxt, nxt)
            # 活一契约（b676efe + App.vue watch(state)）：快速运行从单选下拉改成
            # **勾选条**（勾几件跑几件），但“自动对齐到下一个未完成阶段”保留 ——
            # 只是形态从 select.input_value() 变成该阶段按钮的 .on 勾选态。
            # 旧断言读 .flow-quick select（现已只剩“组合…”下拉）必假红，按新契约改写。
            strip = page.locator(".flow-quick .qstage")
            n_stages = len((real_state() or {}).get("stages") or [])
            check("快速运行勾选条渲染全部 %s 个阶段按钮（新契约）" % n_stages,
                  strip.count() == n_stages, strip.count())
            on_txt = page.locator(".flow-quick .qstage.on").all_inner_texts()
            check("勾选条自动对齐到阶段 %s（旧\u300cselect 自动对齐\u300d契约的新形态）" % run_stage,
                  len(on_txt) == 1 and on_txt[0].startswith(str(run_stage)), on_txt)
            strip.nth(run_stage - 1).click()
            page.wait_for_timeout(250)
            check("点击取消勾选（勾选条真交互：可关）",
                  page.locator(".flow-quick .qstage.on").count() == 0,
                  page.locator(".flow-quick .qstage.on").count())
            strip.nth(run_stage - 1).click()
            page.wait_for_timeout(250)
            on_txt2 = page.locator(".flow-quick .qstage.on").all_inner_texts()
            check("再点恢复勾选（可开可关，回到对齐态）",
                  len(on_txt2) == 1 and on_txt2[0].startswith(str(run_stage)), on_txt2)
        page.wait_for_timeout(2500)
        est = (page.locator(".flow-est").first.inner_text()
               if page.locator(".flow-est").count() else "")
        if "预计" in est:
            check("预估成本已拉到（/estimate 真实口径）", "¥" in est, est)
        else:
            print("  [SKIP] 预估成本：当前工作流条不展示预估（下一步=%s）"
                  % ("审批门" if is_gate else "运行"))
        shot(page, "B1_pipeline_real")

        if is_gate:
            # ⚠️ 绝不在这里按 Space：Space = 执行下一步 = 会真的把审批按掉（改用户数据）。
            print("  [SKIP] Space = 执行下一步：下一步是**审批门**，真机按下去会改用户工作区；"
                  "该段只在\u300c下一步 = 运行\u300d时有意义（那时会先弹费用预估确认框）。")
        elif (real_state() or {}).get("agent_mode"):
            # Agent 模式运行锁（2026-10-17 新契约）：Space = 执行下一步 = 运行类
            # → 被 agentRunGuard 拦下吃锁定 toast，**不弹**预估框。此前这条断言
            # 只认“弹预估框”，真后端开 agent_mode 时必假红 —— 按新语义分流。
            print("  --- Space = 执行下一步（Agent 模式：应吃锁定提示，不弹预估）---")
            # 2026-11-08：不要用“点 body 某个坐标”来清焦点 —— 内容区改成单档 1600px 后，
            # (700,700) 正好落到“重估”按钮上，焦点被按钮抢走，Space 触发的是按钮而不是
            # 全局快捷键（B2 断言因此假红，截图里“重估”带焦点环）。
            # 这里显式清掉 activeElement，才是“焦点不在任何控件上”的真实前提。
            page.evaluate("() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }")
            page.keyboard.press(" ")
            page.wait_for_timeout(1200)
            check("Agent 模式下 Space 吃锁定提示（运行锁生效）",
                  page.locator(".toast:has-text('Agent 模式已开启')").count() >= 1,
                  page.locator(".toast").all_inner_texts() if page.locator(".toast").count() else "<无 toast>")
            check("锁定时不开预估确认框",
                  page.locator(".dialog:has-text('运行前预估')").count() == 0)
            shot(page, "B2_space_locked_by_agent_mode")
        else:
            print("  --- Space = 执行下一步（走费用预估确认，不真花钱）---")
            # 2026-11-08：不要用“点 body 某个坐标”来清焦点 —— 内容区改成单档 1600px 后，
            # (700,700) 正好落到“重估”按钮上，焦点被按钮抢走，Space 触发的是按钮而不是
            # 全局快捷键（B2 断言因此假红，截图里“重估”带焦点环）。
            # 这里显式清掉 activeElement，才是“焦点不在任何控件上”的真实前提。
            page.evaluate("() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }")
            page.keyboard.press(" ")
            page.wait_for_timeout(1800)
            check("Space 打开运行前预估确认框",
                  page.locator(".dialog:has-text('运行前预估')").count() == 1)
            shot(page, "B2_estimate_by_space")
            if page.locator(".dialog button:has-text('取消')").count():
                page.locator(".dialog button:has-text('取消')").first.click()
                page.wait_for_timeout(400)
            check("取消后回到流水线且未启动任务",
                  page.locator(".dialog:has-text('运行前预估')").count() == 0,
                  page.locator(".runbar").count())

        print("  --- 命令面板搜索 + 执行只读命令 ---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "日")
        page.wait_for_timeout(300)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("搜索\u300c日\u300d命中运行日志", any("日志" in t for t in labels), labels[:5])
        page.fill(".cmd-input", "运行日志")
        page.wait_for_timeout(250)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        check("真后端日志面板打开（/logs/tail 真实读取）",
              page.locator(".log-body").count() == 1 and
              len(page.locator(".log-body").inner_text()) > 50,
              (page.locator(".log-body").inner_text()[:60] if page.locator(".log-body").count() else ""))
        shot(page, "B3_real_run_log")
        page.locator(".dialog button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)

        page.keyboard.press("r")
        page.wait_for_timeout(800)
        check("R 键刷新有 toast 反馈", page.locator(".toast").count() == 1,
              page.locator(".toast").inner_text() if page.locator(".toast").count() else "")

        print("  --- 真后端\u300c关于\u300d：版本/路径必须来自真实数据 ---")
        page.locator(".brand-btn").click()
        page.wait_for_timeout(1200)
        about = page.locator(".about-dlg")
        check("真后端关于弹窗打开", about.count() == 1)
        atxt = about.inner_text() if about.count() else ""
        # 版本号必须**取自 console/package.json**（真实口径），且不得是 dev/mock 占位。
        # 原实现硬编码 "v0.1.0" —— 判据本身是错的：① 版本一升就必然红（2026-09-29
        # 已是 0.3.2）；② "v0.1.0" 还能匹到 mock 的 "0.1.0-mock"，等于没测出“非 dev”。
        _pkg_ver = json.loads((ROOT / "console" / "package.json").read_text(encoding="utf-8"))["version"]
        check("版本号取自 console/package.json（非 dev）",
              ("v" + _pkg_ver) in atxt and "读取版本信息失败" not in atxt
              and "mock" not in atxt, atxt[:70].replace("\n", " | "))
        # 路径断言**从后端自报的 project_root 推导**，不写死本机路径、也不写死"仓库根"
        # （2026-11-03 修）：打包态下 ROOT 是工作区，源码态才是仓库；按仓库根去断，
        # 打包/多书场景必然假红。真正的保证是"界面显示的 = 后端真实在用的项目根"。
        try:
            with urllib.request.urlopen(REAL_API + "/about", timeout=5) as _r:
                _real_root = (json.loads(_r.read().decode("utf-8"))
                              .get("project_root") or "")
        except Exception:                                    # noqa: BLE001
            _real_root = ""
        # 2026-11-20（问题表 P0-8）：数据位置不再正面印三条绝对路径，改成
        # “你的项目 / 写作内容 / 成品输出 + 一句话说明”，路径收进 title（悬停可见）。
        # 保证不变：界面给出的位置 = 后端真实在用的项目根 —— 只是从正文搬到 title。
        _root_row = about.locator(".ap-row").first
        _root_title = (_root_row.get_attribute("title") or "") if _root_row.count() else ""
        check("显示真实项目根路径（悬停标题里就是后端自报的路径）",
              bool(_real_root) and _real_root in _root_title
              and "你的项目" in atxt,
              "期望 title 含 project_root=%r；实际 title=%r"
              % (_real_root, _root_title))
        check("含快速上手 / 数据位置 / 许可区块",
              about.locator(".about-steps li").count() == 4 and about.locator(".ap-row").count() == 3
              and "MIT" in atxt)
        shot(page, "B4_about_real")
        about.locator("button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)
        errs_real = [e for e in errs if "404" not in e]
        print("  console error/warning: %s" % (errs_real or "无"))
        check("无前端 console 报错（真后端下）", not errs_real, errs_real[:3])
        print("  非 2xx 响应: %s" % (list(dict.fromkeys(bad))[:5] or "无"))
    finally:
        ctx.close()


def test_dark(browser):
    """暗色主题：新组件（工作流条 / 命令面板 / 恢复条 / 浮层）不能只在浅色下能看。"""
    print("\n=== C. 暗夜紫主题下的观感 ===")
    ctx = browser.new_context(viewport={"width": 1440, "height": 940})
    ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
    ctx.add_init_script("localStorage.setItem('mofang_theme','dark');")
    ctx.add_init_script(PRELOAD_SHIM_JS)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.type + ": " + m.text[:200])
            if m.type in ("error", "warning") else None)
    try:
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        check("暗色主题已生效（html.theme-dark）",
              page.evaluate("document.documentElement.className"), page.evaluate("document.documentElement.className"))
        check("工作流条/恢复条在暗色下仍渲染", page.locator(".flow-strip").count() == 1
              and page.locator(".stage-recovery").count() >= 1)
        # 对比度粗检：正文与卡片背景的亮度差不能过小
        ratio = page.evaluate("""() => {
          const el = document.querySelector('.flow-next b');
          const s = getComputedStyle(el);
          const card = getComputedStyle(document.querySelector('.flow-strip'));
          const lum = (c) => { const m = c.match(/\\d+/g).map(Number);
            const a = m.slice(0,3).map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); });
            return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2]; };
          const l1 = lum(s.color), l2 = lum(card.backgroundColor);
          const hi = Math.max(l1,l2), lo = Math.min(l1,l2);
          return {color: s.color, bg: card.backgroundColor, contrast: +(((hi+0.05)/(lo+0.05)).toFixed(2))};
        }""")
        check("暗色下正文/卡片对比度 ≥ 3", ratio["contrast"] >= 3, ratio)
        shot(page, "C1_dark_pipeline")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        shot(page, "C2_dark_palette")
        check("暗色主题无 console 报错", not [e for e in errs if "404" not in e], errs[:3])
    finally:
        ctx.close()


def test_about_project(browser, mock_proc=None):
    """关于 / 项目页签 / 新建项目向导 / 冷启动引导（发布级入口）。"""
    print("\n=== D. 关于 · 项目管理 · 新建项目向导 · 冷启动引导 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "about")
    try:
        print("  --- 左上角 LOGO → 关于 ---")
        page.locator(".brand-btn").click()
        page.wait_for_timeout(900)
        check("点 LOGO 打开\u300c关于\u300d", page.locator(".about-dlg").count() == 1)
        txt = page.locator(".about-dlg").inner_text()
        check("关于含版本号", "v" in txt and "绒花墨坊" in txt, txt[:60].replace("\n", " | "))
        check("关于含\u300c快速上手\u300d四步", page.locator(".about-steps li").count() == 4,
              page.locator(".about-steps li").count())
        check("关于含运行环境表（Electron/Chromium/平台）",
              "Electron" in txt and "Chromium" in txt, None)
        check("关于含数据位置三行 + 打开按钮",
              page.locator(".ap-row").count() == 3
              and page.locator(".ap-row button:has-text('打开')").count() == 3)
        check("关于含许可与外链按钮",
              page.locator(".about-links button:has-text('GitHub 仓库')").count() == 1
              and page.locator(".about-links button:has-text('开源许可')").count() == 1)
        check("关于含第三方组件声明（MIT/BSD）", "MIT" in txt and "BSD" in txt, None)
        shot(page, "D1_about")
        page.locator(".about-dlg button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        check("关闭后关于消失", page.locator(".about-dlg").count() == 0)

        print("  --- 书名 → 项目页签 ---")
        page.locator(".brand-sub-btn").click()
        page.wait_for_timeout(700)
        active = page.locator(".tabs button.active").first.inner_text()
        check("点书名跳到\u300c项目\u300d页签", "项目" in active, active)
        check("项目页签有\u300c＋ 新建项目\u300d入口",
              page.locator("button:has-text('＋ 新建项目')").count() >= 1)
        check("项目页签显示工作区状态徽标",
              page.locator("text=工作区有数据").count() >= 1 or page.locator("text=工作区为空").count() >= 1)
        # 出厂样例徽标（2026-10-11 用户需求“当前项目没有任何显示标识”）：
        # mock 的书名与样例书名相同 → 提示“同名”而非“出厂项目”（2026-10-23 改：
        # 名字比对冒充出厂身份，在真书上误导过用户），说明文要说清"只是撞名"。
        badge = page.locator(".pill:has-text('与出厂样例同名')")
        check("项目页显示\u300c与出厂样例同名\u300d徽标（书名 == 样例书名但无铺设标记）",
              badge.count() >= 1, badge.count())
        tip = badge.first.get_attribute("title") if badge.count() else ""
        check("徽标悬浮说明含\u300c自检没过\u300d与\u300c一键还原\u300d边界",
              bool(tip) and "自检没过" in tip and "一键还原" in tip, (tip or "")[:160])
        if badge.count():
            badge.first.click()
            page.wait_for_timeout(300)
            t = page.locator(".toast")
            toast = t.inner_text() if t.count() else ""
            check("点徽标也能弹出说明（不只依赖悬浮）", "自检没过" in toast, toast[:160])
        shot(page, "D2_project_tab")

        print("  --- 新建项目向导四步（v0.20 起；2026-11-21 起显示为 1~4：1 选预设 → 2 基本信息 → 3 当前工作区 → 4 确认创建）---")
        page.locator("button:has-text('＋ 新建项目')").first.click()
        page.wait_for_timeout(500)
        # 旧断言按三步布局写（首屏即基本信息），v0.20 插入预设步后整段长期失真 ——
        # 2026-10-24 改按真实四步走；活跃步用 .wiz-step.on 判（全量文本恒含所有步名，
        # 拿 inner_text 判断等于不断言）。
        check("向导打开且停在第 0 步（选预设）", page.locator(".wiz-steps").count() == 1
              and "1 选预设" in page.locator(".wiz-step.on").inner_text())
        nxt = page.locator(".dialog button:has-text('下一步')")
        check("未选预设时\u300c下一步\u300d可用（手工流程不被堵在预设屏）", not nxt.is_disabled())
        shot(page, "D3_wizard_step0")
        nxt.click()
        page.wait_for_timeout(300)
        check("进入第 1 步（基本信息）", "2 基本信息" in page.locator(".wiz-step.on").inner_text())
        check("空书名时\u300c下一步\u300d禁用（前置校验）", nxt.is_disabled())
        page.locator(".dialog input.text-input").first.fill("验收新书")
        page.wait_for_timeout(200)
        check("填了书名后\u300c下一步\u300d可用", not nxt.is_disabled())
        nxt.click()
        page.wait_for_timeout(300)
        check("进入第 2 步（当前工作区）", "3 当前工作区" in page.locator(".wiz-step.on").inner_text())
        check("有数据时显示归档勾选框", page.locator(".wiz-check input[type=checkbox]").count() == 1)
        page.locator(".wiz-check input[type=checkbox]").uncheck()
        page.wait_for_timeout(250)
        check("取消勾选后给出明确风险提示",
              page.locator(".wiz-warn").count() == 1
              and "拒绝" in page.locator(".wiz-warn").inner_text(),
              page.locator(".wiz-warn").inner_text()[:60])
        check("未勾选归档时\u300c下一步\u300d直接置灰（不许走到确认创建再被后端弹回）",
              nxt.is_disabled())
        page.locator(".wiz-check input[type=checkbox]").check()
        page.wait_for_timeout(200)
        check("勾选归档后\u300c下一步\u300d恢复可用", not nxt.is_disabled())
        page.locator(".dialog button:has-text('下一步')").click()
        page.wait_for_timeout(300)
        check("进入第 3 步（确认摘要）", "4 确认创建" in page.locator(".wiz-step.on").inner_text())
        summary = page.locator(".dialog .cost-table").inner_text()
        check("摘要含书名/类型/章数/归档说明",
              "验收新书" in summary and "归档为" in summary, summary.replace("\n", " | ")[:90])
        shot(page, "D4_wizard_confirm")
        page.locator(".dialog button:has-text('确认创建')").click()
        page.wait_for_timeout(1500)
        toast_txt = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("提交后向导关闭 + toast 明确报告创建结果",
              page.locator(".wiz-steps").count() == 0 and "新项目已创建" in toast_txt,
              toast_txt)
        check("创建后回到流水线页签",
              "流水线" in page.locator(".tabs button.active").first.inner_text())
        shot(page, "D5_after_create")

        print("  --- 预设流程：铺设 → 第 2 步不再要求勾归档（创建带 preset_applied）---")
        page.locator(".tabs button:has-text('项目')").first.click()
        page.wait_for_timeout(300)
        page.locator("button:has-text('＋ 新建项目')").first.click()
        page.wait_for_timeout(600)
        cards = page.locator(".dialog .wiz-preset")
        check("第 0 步列出出厂预设卡片", cards.count() >= 1, cards.count())
        cards.first.click()
        page.wait_for_timeout(200)
        page.locator(".dialog .wiz-preset-act button").click()   # “铺设“…””
        page.wait_for_timeout(600)
        check("铺设成功 → 显示\u300c已铺设\u300d",
              page.locator(".dialog .pill.st-done").filter(has_text="已铺设").count() == 1,
              page.locator(".dialog").inner_text()[:120])
        nxt2 = page.locator(".dialog button:has-text('下一步')")
        check("铺设后第 0 步\u300c下一步\u300d可用", not nxt2.is_disabled())
        nxt2.click()
        page.wait_for_timeout(300)
        check("书名从预设带出（第 1 步可直接过）", not nxt2.is_disabled())
        nxt2.click()
        page.wait_for_timeout(300)
        check("预设流程第 2 步：不显示归档勾选框（工作区就是新项目）",
              page.locator(".dialog .wiz-check").count() == 0)
        check("第 2 步说明素材保留 / 原项目已在铺设时归档",
              "已铺设" in page.locator(".dialog").inner_text()
              and "不会动素材" in page.locator(".dialog").inner_text(),
              page.locator(".dialog").inner_text()[:200])
        check("第 2 步\u300c下一步\u300d可用（无需勾归档）", not nxt2.is_disabled())
        shot(page, "D6_wizard_preset_step2")
        nxt2.click()
        page.wait_for_timeout(300)
        check("确认摘要标明\u300c素材保留\u300d",
              "素材保留" in page.locator(".dialog .cost-table").inner_text(),
              page.locator(".dialog .cost-table").inner_text().replace("\n", " | ")[:120])
        page.locator(".dialog button:has-text('确认创建')").click()
        page.wait_for_timeout(1500)
        toast2 = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("预设流程创建成功，toast 说明素材保留（= 前端带上了 preset_applied）",
              "新项目已创建" in toast2 and "素材保留" in toast2, toast2)
        shot(page, "D7_after_preset_create")

        print("  --- 命令面板里的新入口 ---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "关于")
        page.wait_for_timeout(250)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("命令面板能搜到\u300c关于绒花墨坊\u300d", any("关于" in t for t in labels), labels[:3])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "新建项目")
        page.wait_for_timeout(250)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("命令面板能搜到\u300c新建项目\u300d", any("新建项目" in t for t in labels), labels[:3])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "免责")
        page.wait_for_timeout(250)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("命令面板能搜到\u300c查看免责声明\u300d（常驻入口之一）",
              any("免责声明" in t for t in labels), labels[:3])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

        print("  --- 流水线条上的 token 闸门（hermes 下唯一有效的止烧观测面）---")
        page.locator(".tabs button:has-text('流水线')").click()
        page.wait_for_timeout(700)
        strip = page.locator(".ps-item").all_inner_texts()
        joined = " | ".join(strip)
        check("流水线条显示\u300ctoken 用量上限\u300d（不能只显示 ¥0.0）",
              any(("token 用量上限" in t) or ("token 闸门" in t) for t in strip),
              joined[:160])
        check("显示 token 已用 / 上限（数字真的来自 /state）",
              "116,599" in joined and "10,000,000" in joined, joined[:200])
        shot(page, "D8_token_gate_strip")

        print("  --- 止烧阈值面板（设置页：读值 → 恢复默认预设 → 明确反馈）---")
        page.locator(".tabs button:has-text('设置')").click()
        page.wait_for_timeout(700)
        check("设置页有免责声明入口（pill + 查看完整声明按钮）",
              page.locator(".pill:has-text('免责声明')").count() >= 1
              and page.locator("button:has-text('查看完整声明')").count() >= 1
              and page.locator("button:has-text('查看完整声明')").is_visible(),
              (page.locator(".pill:has-text('免责声明')").count(),
               page.locator("button:has-text('查看完整声明')").count()))
        navs = page.locator(".set-nav-btn")
        # 2026-11-15：第 10 项在新增时没跟着改这条断言（旧写法 9 项）。
        # 2026-11-20：“诊断与调试”改名“运行状态”（作者向重写，见问题表 P0-1）。
        # 数量断言必须与 SETTINGS_TABS 同源 —— 加了页签先改这里，否则红在断言上、不红在功能上。
        check("设置页侧边栏分类渲染（10 项，取代一条长名单；含\u300cWord 模板\u300d\u300c运行状态\u300d\u300c使用手册\u300d）",
              navs.count() == 10, navs.all_inner_texts())
        check("侧栏最后一项是\u300c使用手册\u300d（手册放最后，不挡默认分类）",
              "使用手册" in (navs.last.inner_text() if navs.count() else ""),
              navs.all_inner_texts())
        check("默认停在\u300c引擎与模型\u300d分类（免责声明在其下可见）",
              "on" in (navs.nth(0).get_attribute("class") or ""),
              navs.nth(0).get_attribute("class"))
        page.locator(".set-nav-btn", has_text="预算与止烧").click()
        page.wait_for_timeout(250)
        # 讨论设置也复用 tl-grid 排版（带 `discuss` 类）：这里只认预算那一块，
        # 否则同一个类会匹配到两处（2026-11-16 实测 strict 报错）。
        check("止烧面板渲染（网格 + 五个字段）",
              page.locator(".tl-grid:not(.discuss)").count() == 1
              and page.locator(".tl-field").count() >= 5,
              page.locator(".tl-field").count())
        # 值来自后端（mock 回的就是出厂预设）
        vals = [page.locator(".tl-field input[type=number]").nth(i).input_value()
                for i in range(page.locator(".tl-field input[type=number]").count())]
        check("单请求上限显示 200000（与出厂预设一致）", "200000" in vals, vals)
        check("累计上限显示 10000000（1000 万/轮）", "10000000" in vals, vals)
        _grid = page.locator(".tl-grid:not(.discuss)")
        check("区间提示可见（不是空标签）",
              "1000" in _grid.inner_text() and "100000" in _grid.inner_text(),
              _grid.inner_text()[:80].replace("\n", " | "))
        shot(page, "D7_token_limit_panel")
        page.locator("button:has-text('恢复默认预设')").click()
        page.wait_for_timeout(900)
        msg = (page.locator(".tl-msg").first.inner_text()
               if page.locator(".tl-msg").count() else "")
        check("点\u300c恢复默认预设\u300d后有明确反馈（成功或失败都要说清）",
              ("已恢复默认预设" in msg) or ("恢复失败" in msg), msg or "（无反馈元素）")

        print("  --- Word 模板面板（件五 GUI 入口：下拉 / 打开模板目录 / 恢复默认）---")
        page.locator(".set-nav-btn", has_text="Word 模板").click()
        page.wait_for_timeout(450)
        wpane = page.locator(".set-pane", has_text="Word 成品模板（templates/")
        check("\u300cWord 模板\u300d面板渲染（h4 + 说明）",
              wpane.count() == 1 and wpane.locator("h4").count() == 1, wpane.count())
        wsel = wpane.locator("select.model-select")
        check("下拉列出 mock 清单的 2 个模板",
              wsel.locator("option").count() == 2, wsel.locator("option").count())
        check("当前值 = 出厂模板（GET 回读真的接上了）",
              wsel.input_value() == "templates/出厂预设模板.docx", wsel.input_value())
        check("四个按钮齐全（重新读取 / 打开模板目录 / 恢复默认 / 应用）",
              all(wpane.locator("button:has-text('%s')" % b).count() >= 1
                  for b in ("重新读取", "打开模板目录", "恢复默认", "应用")),
              wpane.locator("button").all_inner_texts())
        check("未改动时\u300c应用\u300d禁用（防误写配置）",
              wpane.locator("button:has-text('应用')").is_disabled())
        check("字段口径写在面板上（book.word_template）",
              "book.word_template" in wpane.inner_text(),
              wpane.inner_text()[:120].replace("\n", " | "))
        shot(page, "D11_word_template")

        wsel.select_option("templates/备用模板.docx")
        page.wait_for_timeout(250)
        check("改选后出现\u300c已修改\u300d徽标 + 应用解锁",
              wpane.locator(".pill.st-gate").count() >= 1
              and not wpane.locator("button:has-text('应用')").is_disabled())
        wpane.locator("button:has-text('打开模板目录')").click()
        page.wait_for_timeout(350)
        wt_toast = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("\u300c打开模板目录\u300d有明确反馈（成功或失败都要说清）",
              "模板目录" in wt_toast, wt_toast)
        wpane.locator("button:has-text('应用')").click()
        page.wait_for_timeout(700)
        check("应用后下拉回读到新值（写→读真链路，不是静态面板）",
              wsel.input_value() == "templates/备用模板.docx", wsel.input_value())
        wt_txt = wpane.inner_text()
        check("应用后给出落点反馈且\u300c已修改\u300d徽标消失",
              "word_template" in wt_txt and "已修改" not in wt_txt,
              wt_txt[:200].replace("\n", " | "))
        wpane.locator("button:has-text('恢复默认')").click()
        page.wait_for_timeout(600)
        check("恢复默认回到出厂模板",
              wsel.input_value() == "templates/出厂预设模板.docx", wsel.input_value())
        shot(page, "D12_word_template_applied")

        print("  --- 词表面板（B1：保存 → 生效档位真的变）---")
        page.locator(".set-nav-btn", has_text="风格与提示词").click()
        page.wait_for_timeout(450)
        wlp = page.locator(".wl-panel")
        check("词表面板渲染（病灶 / 合规两块输入框 + 说明）",
              wlp.count() == 1 and wlp.locator("textarea.wl-text").count() == 2,
              (wlp.count(), wlp.locator("textarea.wl-text").count()))
        check("初始档位 = 内置 20 条（未配置自定义表时明说）",
              "内置 20 条" in wlp.inner_text(), wlp.inner_text()[:120].replace("\n", " | "))
        # 2026-11-08：先显式重读一次并等它落地，再输入。
        # 面板挂载时的首次 GET 可能还在飞；此时 fill 会被随后到达的响应覆盖，
        # “未保存”徽标一闪即消（本轮 e2e 就是在这里假红，单独跑同样步骤却正常）。
        wlp.locator("button:has-text('重新读取')").click()
        page.wait_for_timeout(700)
        wlp.locator("textarea.wl-text").nth(0).fill("微微一笑\n眸光\n眼里闪过一丝\n")
        page.wait_for_timeout(300)
        check("改动后出现\u300c未保存\u300d提示（不保存就不生效，得先看见）",
              wlp.locator(".pill.st-gate").count() >= 1)
        save_btn = wlp.locator("button:has-text('保存')")
        check("有改动时“保存”可点（没有改动才该禁用）", save_btn.count() == 1 and not save_btn.is_disabled())
        if save_btn.is_disabled():
            return   # 干净失败：不再用 30s 超时把整轮拖死
        save_btn.click()
        page.wait_for_timeout(800)
        wl_txt = wlp.inner_text()
        check("保存后档位变成\u300c界面词表\u300d（写→读是真链路，不是静态面板）",
              "界面词表" in wl_txt, wl_txt[:180].replace("\n", " | "))
        check("保存后回显生效 3 条", "实际生效 3 条" in wl_txt,
              wl_txt[:220].replace("\n", " | "))
        shot(page, "D13_wordlists")

        print("  --- 主题颜色：亮色/暗色分排 + 文字标识（2026-10-06 用户反馈）---")
        page.locator(".set-nav-btn", has_text="外观与更新").click()
        page.wait_for_timeout(250)
        groups = page.locator(".theme-group")
        titles = page.locator(".theme-group-title")
        check("主题颜色分两排（.theme-group × 2）", groups.count() == 2, groups.count())
        check("两排各有文字标识（亮色主题 / 暗色主题）",
              titles.count() == 2
              and titles.nth(0).inner_text().strip() == "亮色主题"
              and titles.nth(1).inner_text().strip() == "暗色主题",
              titles.all_inner_texts())
        light_btns = groups.nth(0).locator(".theme-swatch")
        dark_btns = groups.nth(1).locator(".theme-swatch")
        check("亮色排 6 个 / 暗色排 4 个（与 THEMES / DARK_THEMES 一致）",
              light_btns.count() == 6 and dark_btns.count() == 4,
              (light_btns.count(), dark_btns.count()))
        dark_titles = [dark_btns.nth(i).get_attribute("title")
                       for i in range(dark_btns.count())]
        check("暗色排全是\u300c暗夜\u300d系主题（分组没串排）",
              bool(dark_titles) and all(t and t.startswith("暗夜") for t in dark_titles),
              dark_titles)
        shot(page, "D11_theme_rows")

        print("  --- 接入其他 Agent（配置段生成 + kickoff 一键复制）---")
        # 2026-11-26：设置页导航项名字已从“Agent 接入”改为“外部协作”
        # （SETTINGS_TABS 里 id=agent）。旧选择器等不到元素 → 30 秒超时假红。
        page.locator(".set-nav-btn", has_text="外部协作").click()
        page.wait_for_timeout(250)
        check("\u300c接入其他 Agent\u300d配置块渲染", page.locator(".ac-block").count() == 1,
              page.locator(".ac-block").count())
        acode = page.locator(".ac-code")
        check("默认展示 Hermes YAML 配置段（含 mcp_servers 与垫片路径）",
              "mcp_servers" in acode.inner_text()
              and "nf_mcp_stdio_bridge" in acode.inner_text(),
              acode.inner_text()[:100].replace("\n", " | "))
        page.locator(".ac-tabs button", has_text="Claude").click()
        page.wait_for_timeout(250)
        check("切到 JSON 目标后展示 mcpServers JSON",
              "mcpServers" in acode.inner_text() and "nf_mcp_stdio_bridge" in acode.inner_text(),
              acode.inner_text()[:80].replace("\n", " | "))
        kick = page.locator(".ac-kickoff textarea")
        kick_val = kick.input_value() if kick.count() else ""
        check("kickoff 提示词已加载且占位符被替换（不是空壳）",
              kick.count() == 1 and "PROJECT_ROOT}}" not in kick_val
              and len(kick_val) > 25,
              "len=%d" % len(kick_val))
        check("\u300c复制启动提示词\u300d按钮可用",
              not page.locator(".ac-kickoff button:has-text('复制启动提示词')").is_disabled())
        page.locator(".ac-kickoff button:has-text('复制启动提示词')").click()
        page.wait_for_timeout(400)
        kick_toast = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("复制动作有明确反馈（已复制 / 失败都要说）",
              ("已复制" in kick_toast) or ("失败" in kick_toast), kick_toast)

        print("  --- 让它自装（对齐方寸\u300c🤖 让它自装\u300d第三渠道）---")
        si = page.locator(".ac-selfinstall")
        check("\u300c让它自装\u300d块渲染", si.count() == 1, si.count())
        si_ta = page.locator(".ac-selfinstall textarea")
        si_val = si_ta.input_value() if si_ta.count() else ""
        check("自装指令含目标配置路径 + YAML 配置段（不是空壳）",
              "config.yaml" in si_val and "mcp_servers" in si_val
              and "nf_mcp_stdio_bridge" in si_val,
              "len=%d" % len(si_val))
        check("自装指令不再指向已删除的技能卡（2026-11-25 删卡）",
              "skills/worldbuilding/ronghuamofang/SKILL.md" not in si_val)
        check("自装指令带自检步骤与红线（25 个 nf_* / 禁手写产物）",
              "nf_get_state" in si_val and "冒充执行" in si_val,
              si_val[-160:].replace("\n", " | "))
        page.locator(".ac-selfinstall button", has_text="Claude Code").click()
        page.wait_for_timeout(250)
        si_val2 = si_ta.input_value()
        check("切换目标后指令跟随（JSON mcpServers + 该家配置路径）",
              "mcpServers" in si_val2 and ".claude.json" in si_val2,
              si_val2[:120].replace("\n", " | "))
        page.locator(".ac-selfinstall button:has-text('复制自装指令')").click()
        page.wait_for_timeout(400)
        si_toast = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("复制自装指令有明确反馈",
              ("已复制" in si_toast) or ("失败" in si_toast), si_toast)
        blk = page.locator(".ac-block")
        blk.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        shot(page, "D9_agent_connect", keep_scroll=True)   # 先滚到可见再拍：拍的必须是新块本身
        bb = blk.bounding_box()
        check("配置块滚到可见后在视口内且不越界（几何断言）",
              bool(bb) and bb["x"] >= 0 and bb["x"] + bb["width"] <= 1440 + 2
              and bb["y"] >= -2 and bb["y"] + bb["height"] <= 940 + 2,
              bb)
        check("页面无水平溢出（scrollWidth ≤ clientWidth+2）",
              page.evaluate("() => document.documentElement.scrollWidth"
                            " <= document.documentElement.clientWidth + 2"))

        print("  --- 运行状态与排查（2026-11-20 由“诊断与调试”重写：作者向）---")
        page.locator(".set-nav-btn", has_text="运行状态").click()
        page.wait_for_timeout(250)
        check("\u300c运行状态与排查\u300d块渲染",
              page.locator("h4:has-text('运行状态与排查')").count() == 1,
              page.locator("h4:has-text('运行状态与排查')").count())
        dbg_meta = page.locator("h4:has-text('运行状态与排查') + .meta")
        dbg_txt = dbg_meta.inner_text() if dbg_meta.count() else ""
        check("显示程序形态/版本/后台服务状态（来自 debug:info）",
              "源码版" in dbg_txt and "0.0.0-e2e" in dbg_txt and "正常" in dbg_txt,
              dbg_txt[:120].replace("\n", " | "))
        # 2026-11-20 的核心断言：默认页面上只有“数据在哪 / 日志在哪”两行，
        # 程序文件路径与 PID 收进开发者选项 —— 它们是问题表 P0-1 的原话。
        check("默认只显示数据/日志两行，不印程序路径与 PID",
              "fake" in dbg_txt and "nf_api_child.log" in dbg_txt
              and "程序文件在" not in dbg_txt and "PID" not in dbg_txt,
              dbg_txt[-160:].replace("\n", " | "))
        for label in ("查看运行日志", "打开日志文件", "复制排查信息", "重启后台服务"):
            check("状态块含\u300c%s\u300d按钮" % label,
                  page.locator("button:has-text('%s')" % label).count() >= 1, label)
        check("默认不出现开发者工具入口（开发者选项关着）",
              page.locator("button:has-text('开发者工具')").count() == 0, None)
        page.locator("label:has-text('显示开发者选项') input[type=checkbox]").check()
        page.wait_for_timeout(200)
        dbg_txt2 = dbg_meta.inner_text() if dbg_meta.count() else ""
        check("打开开发者选项后才出现程序文件路径",
              "程序文件在" in dbg_txt2, dbg_txt2[-120:].replace("\n", " | "))
        page.locator("button:has-text('开发者工具')").click()
        page.wait_for_timeout(300)
        dt_toast = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("开发者工具有反馈（IPC 等价假实现回 ok）",
              "DevTools" in dt_toast, dt_toast)
        # 看完就关掉：devMode 会写 localStorage，留着会让后面的截图多出两行
        # 程序路径（截图要反映作者默认看到的样子），也免得污染后续用例。
        page.locator("label:has-text('显示开发者选项') input[type=checkbox]").uncheck()
        page.wait_for_timeout(200)
        check("关掉开发者选项后程序路径又消失",
              "程序文件在" not in (dbg_meta.inner_text() if dbg_meta.count() else ""), None)
        # 出厂内容（0.6.7 出厂/用户数据分治）：诊断节的新块，数据来自 /factory/list
        page.wait_for_timeout(500)          # 等 mock 的 /factory/list 回来
        fac_meta = page.locator("h4:has-text('出厂内容') + .meta")
        fac_txt = fac_meta.inner_text() if fac_meta.count() else ""
        check("\u300c出厂内容\u300d块渲染（清单文件数 + 样例匹配数）",
              page.locator("h4:has-text('出厂内容')").count() == 1
              and "214" in fac_txt and "2 个" in fac_txt,
              fac_txt[:160].replace("\n", " | "))
        fac_btn = page.locator("button:has-text('清理出厂样例')")
        check("有命中时\u300c清理出厂样例\u300d按钮存在且可用",
              fac_btn.count() >= 1 and not fac_btn.first.is_disabled(),
              fac_btn.count())
        # 状态块几何：先滚到可见再拍/再断言（bounding_box 是视口相对坐标）
        h4d = page.locator("h4:has-text('运行状态与排查')")
        h4d.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        shot(page, "D10_diagnostics", keep_scroll=True)
        dbb = h4d.bounding_box()
        check("诊断块标题滚到可见后在视口内（几何断言）",
              bool(dbb) and dbb["x"] >= 0 and dbb["x"] + dbb["width"] <= 1440 + 2
              and dbb["y"] >= -2 and dbb["y"] <= 940 + 2, dbb)

        page.locator(".tabs button:has-text('流水线')").click()
        page.wait_for_timeout(400)
    finally:
        ctx.close()

    print("  --- 新手序列引导（I-1，2026-11-03）---")
    # 前提：8797 是同一个 mock 的 --cold 实例。没起时整段只能空跑，
    # 会连报“引导卡不存在”这种假红 —— 按项目纪律：未执行 ≠ 失败。
    try:
        with urllib.request.urlopen(COLD_API + "/health", timeout=3) as r:
            _cold_alive = r.status == 200
    except Exception:                                        # noqa: BLE001
        _cold_alive = False
    if not _cold_alive:
        print("  [SKIP] 序列引导段前提不成立：%s 没有在跑。" % COLD_API)
        print("         启动：python tests/e2e/mock_nf_api_state.py --port 8797 --cold")
    if _cold_alive:
        ctx2, page2, errs2, bad2 = new_page(browser, COLD_API, "cold")
        try:
            check("全新工作区显示序列引导卡", page2.locator(".ob-card").count() == 1,
                  page2.locator(".ob-card").count())
            check("给出唯一的“当前步”",
                  "下一步：建项目" in (page2.locator(".ob-next-title").inner_text()
                                    if page2.locator(".ob-next-title").count() else ""),
                  page2.locator(".ob-next-title").inner_text()
                  if page2.locator(".ob-next-title").count() else "(缺)")
            check("收起态不铺步骤（先一句“下一步”，不糊一脸）",
                  page2.locator(".ob-step").count() == 0)
            page2.locator(".ob-card button:has-text('全部步骤')").click()
            page2.wait_for_timeout(350)
            check("展开后 8 步齐全", page2.locator(".ob-step").count() == 8,
                  page2.locator(".ob-step").count())
            check("只有一个“当前步”（序列严格）",
                  page2.locator(".ob-step.is-current").count() == 1,
                  page2.locator(".ob-step.is-current").count())
            check("当前步写明点了哪里",
                  page2.locator(".ob-step.is-current .ob-step-how").count() == 1)
            shot(page2, "D6_onboarding_cold")
            page2.locator(".ob-step.is-current button:has-text('带我去')").click()
            page2.wait_for_timeout(500)
            check("“带我去”真的打开了新建项目向导",
                  page2.locator(".drawer-mask .dialog").count() >= 1,
                  page2.locator(".drawer-mask .dialog").count())
            errs2_real = [e for e in errs2 if "404" not in e]
            check("引导无 console 报错", not errs2_real, errs2_real[:3])
        finally:
            ctx2.close()

    print("  --- 审批门抢“当前步”+ 带我去高亮 ---")
    ctx3, page3, errs3, bad3 = new_page(browser, MOCK_API, "gate-guide")
    try:
        check("有未审批阶段时，下一步 = 审批放行",
              "审批放行" in (page3.locator(".ob-next-title").inner_text()
                          if page3.locator(".ob-next-title").count() else ""),
              page3.locator(".ob-next-title").inner_text()
              if page3.locator(".ob-next-title").count() else "(缺)")
        page3.locator(".ob-card button:has-text('带我去')").first.click()
        page3.wait_for_timeout(450)
        check("切到收件箱页签",
              page3.locator(".tabs button.active:has-text('收件箱')").count() == 1,
              page3.locator(".tabs button.active").all_inner_texts())
        check("目标区域被高亮（ob-spot 落到了 gate-list）",
              page3.locator('[data-ob="gate-list"].ob-spot').count() == 1)
        shot(page3, "D7_onboarding_gate")
        errs3_real = [e for e in errs3 if "404" not in e]
        check("引导跳转无 console 报错", not errs3_real, errs3_real[:3])
    finally:
        ctx3.close()


    print("  --- 多方案：逐版进度 + 并排对比（I-2）---")
    ctx4, page4, errs4, bad4 = new_page(browser, MOCK_API, "multi")
    try:
        page4.locator(".tabs button:has-text('大纲')").click()
        page4.wait_for_timeout(900)
        page4.locator("button:has-text('多写几版大纲')").first.click()   # 2026-11-21 由“多方案”改名
        page4.wait_for_timeout(1200)
        check("多方案抽屉打开", page4.locator(".ov-multi").count() == 1,
              page4.locator(".ov-multi").count())
        check("三份方案并排", page4.locator(".ov-multi-col").count() == 3,
              page4.locator(".ov-multi-col").count())
        check("推荐列只有一列且带徽标",
              page4.locator(".ov-multi-col.is-rec").count() == 1
              and page4.locator(".ov-multi-col.is-rec:has-text('推荐')").count() == 1)
        meta_txt = page4.locator(".ov-multi-meta").first.inner_text()
        check("元信息显示字数 / 节点 / 章规划",
              "5,820" in meta_txt and "节点" in meta_txt and "章规划" in meta_txt, meta_txt)
        check("推荐说明不越权（明说是排序建议）",
              page4.locator(".ov-multi-hint:has-text('最终由你定')").count() == 1)
        check("默认勾选与推荐一致（不出现“推荐2、默认1”）",
              page4.locator(".ov-multi-col.is-rec input[type=checkbox]").first.is_checked()
              and page4.locator(".ov-multi-col.is-rec input[type=radio]").first.is_checked())
        shot(page4, "D8_multi_compare")
        # 逐版进度：点“生成 3 版”，按钮文案必须动起来
        page4.locator(".drawer-head button:has-text('生成 3 版')").click()
        page4.wait_for_timeout(2600)
        btn = page4.locator(".drawer-head button:has-text('版')").first.inner_text()
        check("生成中显示第几版（不是不动的“生成中…”）",
              "/3 版" in btn or "3 版完成" in btn, btn)
        shot(page4, "D9_multi_progress")
        errs4_real = [e for e in errs4 if "404" not in e]
        check("多方案无 console 报错", not errs4_real, errs4_real[:3])
    finally:
        ctx4.close()


    print("  --- 设定条目来源链路（F21）---")
    ctx5, page5, errs5, bad5 = new_page(browser, MOCK_API, "prov")
    try:
        page5.locator(".tabs button:has-text('设定')").click()
        page5.wait_for_timeout(1200)
        # 只认“设定条目来源”那个输入框：InteractionPanel 也用了同名 class
        # （角色 A / 角色 B），裸 .prov-input 会命中 3 个 → strict mode 直接炸（2026-11-05 实测）。
        page5.locator('input[list="prov-names"]').scroll_into_view_if_needed()
        check("“设定条目来源”块渲染", page5.locator(".prov-block").count() == 1)
        page5.locator('input[list="prov-names"]').fill("主角甲")
        page5.locator(".prov-row button:has-text('追溯')").click()
        page5.wait_for_timeout(700)
        res = page5.locator(".prov-result")
        check("追溯结果渲染", res.count() == 1, res.count())
        rt = res.inner_text() if res.count() else ""
        check("说明最早出现在哪一版", "最早出现在 v1" in rt, rt[:80].replace("\n", " | "))
        check("字段级“来源”可读", "从这个版本起" in rt and "性格" in rt and "v2" in rt)
        check("导入来源显示文件路径", "materials/raw/主角甲_角色卡.md" in rt)
        check("改动记录列出字段", "改动记录" in rt and "简介" in rt)
        page5.locator(".prov-block").scroll_into_view_if_needed()
        page5.wait_for_timeout(200)
        shot(page5, "D13_setting_provenance", keep_scroll=True)
        errs5_real = [e for e in errs5 if "404" not in e]
        check("条目来源无 console 报错", not errs5_real, errs5_real[:3])
    finally:
        ctx5.close()


    print("  --- 两人同场互动约束（C 组）---")
    ctx6, page6, errs6, bad6 = new_page(browser, MOCK_API, "interaction")
    try:
        page6.locator(".tabs button:has-text('设定')").click()
        page6.wait_for_timeout(1200)
        card = page6.locator(".ix-card")
        card.scroll_into_view_if_needed()
        check("“两人同场互动”卡渲染", card.count() == 1, card.count())
        ins = card.locator(".ix-row input")
        check("两个角色输入框", ins.count() == 2, ins.count())
        ins.nth(0).fill("主角甲")
        ins.nth(1).fill("主角乙")
        card.locator("button:has-text('生成约束')").click()
        page6.wait_for_timeout(700)
        res = card.locator(".ix-result")
        check("约束卡生成", res.count() == 1, res.count())
        rt = res.inner_text() if res.count() else ""
        check("给出关系基调", "亲近" in rt, rt[:60].replace("\n", " | "))
        check("列出设定依据（可复核）", "依据" in rt and "师兄" in rt)
        check("称呼 / 禁忌 / 写法三块齐全",
              "称呼" in rt and "别这样写" in rt and "写法" in rt)
        card.scroll_into_view_if_needed()
        page6.wait_for_timeout(200)
        shot(page6, "D14_interaction", keep_scroll=True)
        errs6_real = [e for e in errs6 if "404" not in e]
        check("同场约束无 console 报错", not errs6_real, errs6_real[:3])
    finally:
        ctx6.close()

    print("  --- 诊断抽屉（运行态 + 体检明细）---")
    ctx7, page7, errs7, bad7 = new_page(browser, MOCK_API, "diagnose")
    try:
        page7.locator("button:has-text('补救')").first.click()
        page7.wait_for_timeout(1500)
        dlg = page7.locator(".diag-dlg")
        check("诊断抽屉打开（不是只有建议列表）", dlg.count() == 1, dlg.count())
        dt = dlg.inner_text() if dlg.count() else ""
        check("有“诊断”块", "运行态" in dt, dt[:60].replace("\n", " | "))
        check("有“数据检查”全量计数",
              ("数据检查" in dt or "数据体检" in dt) and "通过" in dt)
        check("有“建议”块", "补救建议" in dt)
        shot(page7, "D15_diagnose")
        errs7_real = [e for e in errs7 if "404" not in e]
        check("诊断无 console 报错", not errs7_real, errs7_real[:3])
    finally:
        ctx7.close()


    print("  --- 成本口径说明（F-4）---")
    ctx8, page8, errs8, bad8 = new_page(browser, MOCK_API, "cost")
    try:
        page8.locator(".tabs button:has-text('成本')").click()
        page8.wait_for_timeout(1200)
        note = page8.locator(".billing-note")
        check("成本页有计价口径说明", note.count() == 1, note.count())
        nt = note.inner_text() if note.count() else ""
        # 2026-11-20（问题表 P0-5）：说明里不再印 `engine=direct` / `provider 刊例价`
        # 这类配置项名与英文，改成“按量计价 / 走订阅流量”。断言跟着改口径，不是改松：
        # 判的仍是“读者能不能分清这两种计费”，只是换成作者能读的词。
        check("说明里写清按量/订阅", "按量" in nt or "订阅" in nt, nt[:60])
        check("入/出比值有解释（不是产出效率）", "不是产出效率" in nt, nt[:80])
        page8.locator(".cost-cards").scroll_into_view_if_needed()
        page8.wait_for_timeout(200)
        shot(page8, "D16_billing_note", keep_scroll=True)
        errs8_real = [e for e in errs8 if "404" not in e]
        check("成本页无 console 报错", not errs8_real, errs8_real[:3])
    finally:
        ctx8.close()


_DESK_MEASURE_JS = r"""
() => {
  // 一行放得下几个汉字：clientWidth 减左右 padding，再除以一个汉字的实际步进宽度。
  // 为什么不数截图：标点/数字宽度不同，目测一遍一个数，两个人数两个数。
  const per = (el) => {
    const c = getComputedStyle(el);
    const cw = el.clientWidth - (parseFloat(c.paddingLeft) || 0) - (parseFloat(c.paddingRight) || 0);
    const cv = document.createElement('canvas').getContext('2d');
    cv.font = c.fontWeight + ' ' + c.fontSize + ' ' + c.fontFamily;
    const g = cv.measureText('\u6c49').width;
    return g > 0 ? Math.floor(cw / g) : 0;
  };
  const out = {};
  const ta = document.querySelector('.ce-textarea');
  if (ta) out.edit = per(ta);
  // 正文段落 = .ce-read-p 里**不是标题**的那些（2026-11-15 修）：
  // 章节标题也带 ce-read-p 类（多一个 ce-read-h，字号 17px），
  // 原来拿 querySelector('.ce-read-p') 就取到标题 → 量出 40 字/行，
  // 而正文一直是 15px / 46 字/行。判据量错元素 = 假红，比没判据更糟。
  const paras = [...document.querySelectorAll('.ce-read-p')]
    .filter((e) => !e.classList.contains('ce-read-h'));
  if (paras.length) out.read = per(paras[0]);
  out.readParas = paras.length;
  return out;
}
"""


_DESK_HEAD_OVERFLOW_JS = r"""
() => {
  // 标题行（.drawer-head）里的元素必须**完整落在容器内**，且不许被压成竖排。
  // 为什么加这条（2026-11-20 目检）：1366 视口下 `保存` 被挤出窗口右缘只露半个紫块、
  // `侧栏` 被切、`第 1 章` 竖成三行、`2165 字（下限 4000）` 压成一竖条 ——
  // 而当时 346 条 e2e 全绿：M 段只量了"正文几个字一行"，没有一条量过头。
  const head = document.querySelector('.drawer-head');
  if (!head) return { missing: true };
  const hb = head.getBoundingClientRect();
  const right = hb.right + 0.5;
  const bad = [];
  for (const el of head.querySelectorAll('b, .mini, .pill, .ce-wordbadge, .ce-layer-pills')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;          // 隐藏（v-if 没渲染）
    const label = (el.textContent || '').trim().slice(0, 14) || el.className;
    // ① 溢出容器右缘 = 点不到
    if (r.right > right) bad.push({ why: '溢出右缘', el: label,
                                    over: Math.round(r.right - right) });
    // ② 一行 30px 上下的按钮被压到 55px 以上 = 中文逐字竖排
    if (r.height > 55 && !el.classList.contains('ce-layer-pills'))
      bad.push({ why: '被压成竖排', el: label, h: Math.round(r.height) });
    // ③ 内容比盒子宽 = 文字被截断
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0)
      bad.push({ why: '文字被截', el: label,
                 need: el.scrollWidth, have: el.clientWidth });
  }
  return { bad, headRight: Math.round(right), win: window.innerWidth,
           // 回一句标题行开头，用来证明这条断言量的**是编辑器那一条**，
           // 不是页面上别的 drawer-head（审阅台里还有别的抽屉）。
           headText: (head.textContent || '').trim().slice(0, 18),
           items: head.querySelectorAll('b, .mini, .pill, .ce-wordbadge, .ce-layer-pills').length };
}
"""


def test_desk_width(browser):
    """B7（2026-11-06）：审阅台正文列宽 ≥ 作者定的口径（当前 40 汉字/行）。

    用户原话“正文不是主视觉”。此前 e2e 进不去审阅台（mock 不返章节正文），
    所以这条缺陷在自动化里**结构上不可见** —— 补完 mock 的 /chapters/layers|content
    与章节文件假桥之后，这里把它钉成判据：三档视口 × 编辑/阅读两态。

    ⚠️ 阈值是 40，不是最早的 45（2026-11-26 对齐 L1-a2）：
    `ChapterEditor.vue` 的 `--read-measure: 40em` × `--fs-read: 17px` = 680px，
    作者 2026-11-25 亲自把字号调大、行宽定成“40 个汉字”（他说“实际改稿时
    文字差不多也这么大”）。45 是旧口径（15px × 730px ≈ 46 字）的遗留阈值，
    继续拿它当判据只会对着一个已经被推翻的设计报红。
    """
    print("\n=== F. 审阅台正文列宽（B7）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "desk")
    try:
        first = True
        for w in (1366, 1680, 1920):
            page.set_viewport_size({"width": w, "height": 940})
            page.wait_for_timeout(400)
            if first:
                page.locator("nav.tabs button", has_text="章节").first.click()
                page.wait_for_timeout(700)
                first = False
            # 2026-11-26：章节页默认就在审阅台（chapterView 默认 desk）→ 这时按钮文案是
            # “回到列表”，没有“审阅台”可点。旧代码硬点必等满 30 秒。
            if page.locator("button:has-text('审阅台')").count():
                page.locator("button:has-text('审阅台')").first.click()
            try:
                _ensure_desk_edit(page)
                page.wait_for_selector(".ce-textarea", timeout=6000)
            except Exception:                                 # noqa: BLE001
                check("审阅台在 %d 宽下可进入（中栏编辑器渲染）" % w, False,
                      "中栏没渲染（章节假桥或 /chapters/content 没接上）")
                continue
            page.wait_for_timeout(500)
            e = page.evaluate(_DESK_MEASURE_JS)
            page.screenshot(path=str(OUT / ("desk_width_%d_edit.png" % w)))
            check("%d 宽 · 编辑态正文 ≥40 汉字/行" % w, (e.get("edit") or 0) >= 40,
                  "实测 %s 字/行" % e.get("edit"))
            hh = page.evaluate(_DESK_HEAD_OVERFLOW_JS)
            _bad = hh.get("bad") or []
            check("%d 宽 · 标题行没有元素溢出/竖排/截字" % w,
                  not hh.get("missing") and not _bad,
                  ("；".join("%s：%s（%s）" % (b["why"], b["el"],
                                              {k: v for k, v in b.items()
                                               if k not in ("why", "el")})
                            for b in _bad)[:220] if _bad else
                   "没有标题行" if hh.get("missing") else
                   "量的是 %s 那一行 · %s 个元素都在框内"
                   % (hh.get("headText"), hh.get("items"))))
            page.locator("button:has-text('阅读')").first.click()
            page.wait_for_timeout(800)
            r = page.evaluate(_DESK_MEASURE_JS)
            page.screenshot(path=str(OUT / ("desk_width_%d_read.png" % w)))
            check("%d 宽 · 阅读态正文 ≥40 汉字/行" % w,
                  bool(r.get("readParas")) and (r.get("read") or 0) >= 40,
                  "实测 %s 字/行（正文段 %s 个）" % (r.get("read"), r.get("readParas")))
            page.locator("button:has-text('编辑')").first.click()
            page.wait_for_timeout(300)
        # C3（2026-11-07）：跑完流水线/重写后批注会失效 —— 必须在标题行就看得见，
        # 不能“点进段落批注才知道”。mock 里固定给 1 条已失效 + 1 条位置变了。
        pill = page.locator(".ce-ann-alert")
        check("批注失效在标题行直接点名（不用点进批注模式才发现）",
              pill.count() >= 1 and "批注" in pill.first.inner_text(),
              pill.first.inner_text() if pill.count() else "（没有提示元素）")
        # 只钉“章节链路”的 4xx：mock 里 /review/ledger、/setting/appearances 之类还没造，
        # 那些 404 是本段之外的覆盖缺口（右栏显示“读取失败”），不该让这条判据变成噪声。
        chapter_bad = [x for x in bad if "/chapters/" in x]
        check("审阅台章节链路无 4xx（layers / content / paragraphs）", not chapter_bad,
              chapter_bad[:3])
    finally:
        ctx.close()


def test_candidates(browser):
    """B2（2026-11-06）：候选稿三栏并排 —— 分数、扣分理由、采用链路。

    为什么要单独一段：这一整块是“多写几版再挑”的**唯一人眼入口**，
    它能坏的地方（后端没返分数、理由空、采用按钮点了没反应）都不会让后端用例变红。
    """
    print("\n=== G. 候选稿对比（B2）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "cand")
    try:
        goto_chapter_list(page)
        panel = page.locator(".cand-panel")
        check("章节页有候选面板（多写几版再挑）", panel.count() == 1, panel.count())
        check("面板里能直接开关“重写时多写 N 版”（用户不必改配置文件）",
              panel.locator("select").count() >= 2, panel.locator("select").count())
        panel.locator("button:has-text('查看候选')").first.click()
        page.wait_for_timeout(900)
        cols = page.locator(".cand-col")
        check("三栏并排（3 个候选）", cols.count() == 3, cols.count())
        scores = [cols.nth(i).locator(".cand-score").inner_text() for i in range(cols.count())]
        check("每栏都有分数", all(s.strip() not in ("", "—") for s in scores), scores)
        check("分数从高到低排（最该看的在最左）",
              [float(s) for s in scores] == sorted([float(s) for s in scores], reverse=True), scores)
        check("每栏都有逐条扣分理由（不是只给个数）",
              all(cols.nth(i).locator(".cand-reasons li").count() >= 2 for i in range(cols.count())),
              [cols.nth(i).locator(".cand-reasons li").count() for i in range(cols.count())])
        # 结构证据（2026-11-17）：分数之外还要有“对白死不死板 / 有没有说教味”这类事实，
        # 且三种状态各自说清（越线 / 都在标准内 / 没算出来）。
        check("越线那版点名“需人工抽样”并写出具体项",
              "需人工抽样" in cols.nth(1).inner_text()
              and "格言式单句密度" in cols.nth(1).inner_text(),
              cols.nth(1).inner_text()[:120])
        check("六项都在标准内的那版明说“都在标准内”",
              "都在标准内" in cols.nth(0).inner_text(), cols.nth(0).inner_text()[:80])
        check("没算出结构的那版显式说明（不许静默空白）",
              "结构指标没算出来" in cols.nth(2).inner_text(), cols.nth(2).inner_text()[:80])
        # 判定依据预览（2026-11-18）：这版一进阶段 5 会被哪几条确定规则拦下。
        # 三种状态同样各说清（有依据 / 一条都没触发 / 没算出来），不给档位。
        check("判定依据点名规则、处数与改法",
              "长句上限" in cols.nth(1).inner_text() and "拆句" in cols.nth(1).inner_text(),
              cols.nth(1).inner_text()[:160])
        check("一条规则都没触发的那版明说（不当成空白）",
              "一条都没触发" in cols.nth(0).inner_text(), cols.nth(0).inner_text()[:120])
        check("没有判定依据的那版显式说明",
              "判定依据" in cols.nth(2).inner_text()
              and ("没算出来" in cols.nth(2).inner_text()
                   or "没有这项预览" in cols.nth(2).inner_text()),
              cols.nth(2).inner_text()[:120])
        check("缺口径/缺章纲在面板层面点名（不混进单版依据里）",
              "出厂默认标准" in page.inner_text("body")
              and "章纲类两条" in page.inner_text("body"))
        check("自动选中的那版有标记", cols.locator(".pill:has-text('自动选中')").count() == 1)
        shot(page, "G1_candidates")
        btn = cols.nth(1).locator("button.cand-apply")
        btn.click()
        page.wait_for_timeout(300)
        check("采用是两步确认（先问一句再动手，不用原生弹窗）",
              "确认" in btn.inner_text(), btn.inner_text())
        btn.click()
        page.wait_for_timeout(800)
        check("采用后按钮变成“已采用”（写→读回环）",
              "已采用" in cols.nth(1).locator("button.cand-apply").inner_text(),
              cols.nth(1).locator("button.cand-apply").inner_text())
        check("候选链路无 4xx", not [x for x in bad if "candidates" in x or "best_of_n" in x],
              [x for x in bad if "candidates" in x or "best_of_n" in x][:3])
    finally:
        ctx.close()


def test_sandbox_review(browser):
    """审核页（沙盒产物队列，2026-11-18 补真机验收）。

    为什么补：设定冲突预检（`bc90387`）当初只有源码级断言 —— 审核页不在 ux 套件覆盖
    范围内，假后端还恒回空队列，于是这一整页**从没进过真机**。它能坏的地方恰好都是
    源码断言看不出来的：三条预检状态挤成一行、驳回对话框开不出来、通过之后统计不动。
    """
    print("\n=== K. 审核队列（沙盒产物 + 设定冲突预检）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "sandbox")
    try:
        page.locator("nav.tabs button", has_text="审核").first.click()
        page.wait_for_timeout(1000)
        items = page.locator(".sb-item")
        check("审核页列出 3 条待审（不是空队列）", items.count() == 3, items.count())
        warn = items.nth(0).inner_text()
        check("有冲突那条点名“设定冲突预检”+具体哪一条（同名，年龄 30 vs 32）",
              "设定冲突预检" in warn and "年龄 30" in warn, warn[:160])
        check("近名也算一条（不当成同名处理）", "近名" in warn, warn[:160])
        ok_txt = items.nth(1).inner_text()
        check("没发现冲突那条明说“没发现同名/近名”",
              "没发现同名" in ok_txt, ok_txt[:120])
        na_txt = items.nth(2).inner_text()
        check("预检不了那条说清为什么（缺设定库），不留空白",
              "冲突预检不了" in na_txt and "设定" in na_txt, na_txt[:120])
        check("孤儿文件单独列出来（审核盲区不许静默吞掉）",
              page.locator(".sb-orphans").count() == 1
              and "未登记" in page.locator(".sb-orphans").inner_text())
        shot(page, "K1_sandbox_queue")

        # 通过：只改状态 —— 条目从“待审”视图消失，统计跟着变
        before = page.locator(".sb-stats").inner_text()
        items.nth(0).locator("button.mini.primary").click()
        page.wait_for_timeout(800)
        after_stats = page.locator(".sb-stats").inner_text()
        check("点“通过”后统计真的变了（不是只弹个提示）", before != after_stats,
              (before, after_stats))
        check("通过后从默认视图移走（默认只看待审）",
              page.locator(".sb-item").count() == 2, page.locator(".sb-item").count())
        check("提示写明“只改审核状态、文件没动”",
              "只改审核状态" in page.locator(".toast").first.inner_text(),
              page.locator(".toast").first.inner_text()[:100])

        # 驳回：必须填原因才给确认（原因进状态库）
        page.locator(".sb-item").nth(0).locator("button.mini.danger").click()
        page.wait_for_timeout(500)
        dlg = page.locator(".drawer-mask .dialog")
        check("驳回先开对话框要原因（不是一点就驳回）", dlg.count() == 1)
        confirm = dlg.locator("button.mini.danger")
        check("没写原因时“确认驳回”是禁用的", confirm.is_disabled())
        dlg.locator("textarea").fill("第二节与设定冲突")
        page.wait_for_timeout(200)
        check("写了原因才解锁", not confirm.is_disabled())
        shot(page, "K2_sandbox_reject_dialog")
        confirm.click()
        page.wait_for_timeout(700)
        check("驳回后对话框关掉", page.locator(".drawer-mask .dialog").count() == 0)
        # 只钉审核链路的 4xx：mock 里 /activity、/exports/history 还没造，
        # 那些 404 是本段之外的覆盖缺口，不该让这条判据变成噪声。
        page_bad = [x for x in bad if "sandbox" in x]
        check("没有前端异常（审核链路无 4xx）", not page_bad, page_bad[:2])
    finally:
        ctx.close()


def test_merge_conflicts(browser):
    """成书提炼并库前的冲突清单（路线四 P1，2026-11-18）。

    这一块的坏法源码断言看不出来：复选框勾了没带上、锁定那条点得动、
    勾完提示还写“一个字没动”（说了假话）。假后端为此给了三条冲突
    （可勾 / 锁定 / world 行），并**有状态**回“覆盖了哪几条”。
    """
    print("\n=== L. 并库前冲突清单（勾了才覆盖）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "merge")
    try:
        goto_chapter_list(page)
        box = page.locator("details.import-box", has_text="成书提炼").first
        box.locator("summary").click()
        page.wait_for_timeout(300)
        box.locator("input.import-path").fill("E:/mock/old.txt")
        box.locator("button:has-text('预演（零 token）')").click()
        page.wait_for_timeout(700)
        box.locator("button:has-text('开始提炼')").click()
        page.wait_for_timeout(3600)          # 等一次轮询（2.5s）拿到“已完成”
        box.locator("button:has-text('合并预览')").click()
        page.wait_for_timeout(800)

        rows = box.locator("input[type=checkbox]")
        # 只数冲突清单里的：details 里可能还有别的复选框，按 label 文本定位
        conflict_rows = box.locator("input[type=checkbox][value^='character:'], "
                                    "input[type=checkbox][value^='world:']")
        check("同名冲突逐条列出来（角色 2 条 + 设定 1 条）", conflict_rows.count() == 3,
              conflict_rows.count())
        check("差异摊到字段级（旧 → 新都写出来）",
              "药铺学徒" in box.inner_text() and "医女" in box.inner_text(),
              box.inner_text()[:200])
        locked = box.locator("input[type=checkbox][value='character:老周']")
        check("锁定那条勾不动（锁定设定不能被一次并库顺手解开）",
              locked.count() == 1 and locked.is_disabled())
        check("不勾时写明“不勾 = 保留原来那份”",
              "保留设定集里原来那份" in box.inner_text(), box.inner_text()[:200])
        # 证据要拍得到冲突清单本身：这一段在页面靠下，先滚到位再拍（keep_scroll=True）
        box.locator("input[type=checkbox][value='character:老周']").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        shot(page, "L1_merge_conflicts", keep_scroll=True)

        box.locator("input[type=checkbox][value='character:苏芷']").check()
        box.locator("button:has-text('确认并进设定集')").click()
        page.wait_for_timeout(900)
        toast = page.locator(".toast").first.inner_text()
        check("勾了才覆盖，且提示说清覆盖了几条",
              "覆盖了 1 条" in toast, toast[:120])
        check("提示同时说明其余同名条目没动",
              "一个字没动" in toast, toast[:120])
        check("写入后明说已写（不是还挂着“还没写”）",
              "已写入" in box.inner_text(), box.inner_text()[:160])
        page_bad = [x for x in bad if "book_extract" in x]
        check("没有前端异常（成书提炼链路无 4xx）", not page_bad, page_bad[:2])
    finally:
        ctx.close()


def test_stage5_step(browser):
    """P1-1（2026-11-14）：stage5 改稿裁定改成逐处步进。

    为什么单独一段：这块的坏法全是“不会让后端用例变红”的那种 ——
    一次又把 40 项全展开、计数不对、定完不跳、上一处没禁用、
    宫格颜色不跟着裁定走。假后端为此加了**有状态**的 stage5
    （tests/e2e/mock_nf_api_state.py: stage5_items / stage5_decide）。
    """
    print("\n=== J. stage5 改稿裁定逐处步进（P1-1）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "stage5")
    try:
        page.locator("nav.tabs button", has_text="审稿").first.click()
        page.wait_for_timeout(1200)
        panel = page.locator(".card", has=page.locator("h3:text-is('阶段 5 改稿裁定')"))
        panel.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        check("审稿页有“阶段 5 改稿裁定”面板", panel.count() == 1, panel.count())
        check("步进条在场（上一处 / 计数 / 下一处）",
              panel.locator(".step-bar button:has-text('上一处')").count() == 1
              and panel.locator(".step-bar button:has-text('下一处')").count() == 1
              and panel.locator(".counter").count() == 1)
        counter = panel.locator(".counter").inner_text()
        check("计数从第 1 处起（默认停在第一处没定的）", "第 1 / 8 处" in counter, counter)
        check("一次只展开一处（不再把全章 40 项摊开）",
              panel.locator(".item").count() == 1, panel.locator(".item").count())
        check("宫格给了全章 8 处，位置一眼可数",
              panel.locator(".chip").count() == 8, panel.locator(".chip").count())
        check("只改标点的那 1 处不占格（宫格不是 9 格），并有一句话说明它并到哪儿",
              panel.locator(".folded-note").count() == 1
              and "1 处" in panel.locator(".folded-note").inner_text(),
              panel.locator(".folded-note").inner_text() if panel.locator(".folded-note").count() else "无")
        check("第一处时“上一处”是禁用的（到头就停）",
              panel.locator(".step-bar button:has-text('上一处')").first.is_disabled())
        shot(page, "J1_stage5_step", keep_scroll=True)

        panel.locator(".step-bar button:has-text('下一处')").first.click()
        page.wait_for_timeout(500)
        check("点“下一处”→ 计数到第 2 处",
              "第 2 / 8 处" in panel.locator(".counter").inner_text(),
              panel.locator(".counter").inner_text())
        check("翻页后仍只显示一处", panel.locator(".item").count() == 1)
        check("挂靠的那一处能展开看到顺带改了哪几处标点",
              panel.locator("details.folds").count() == 1
              and "顺带改了 1 处" in panel.locator("details.folds summary").inner_text(),
              panel.locator("details.folds summary").inner_text() if panel.locator("details.folds").count() else "无")
        panel.locator("details.folds summary").first.click()
        page.wait_for_timeout(200)
        check("展开后看得到那一处的原文与改后（不藏起来）",
              "她停下脚步" in panel.locator("details.folds").inner_text(),
              panel.locator("details.folds").inner_text()[:80])

        # 定一处：采用 → 自动跳到下一处没定的
        panel.locator(".item button:has-text('采用模型改法')").first.click()
        page.wait_for_timeout(900)
        counter = panel.locator(".counter").inner_text()
        check("定完自动跳到下一处（第 2 处 → 第 3 处）", "第 3 / 8 处" in counter, counter)
        check("已定计数跟着走（1 / 8）",
              "已定 1 / 8" in panel.locator(".step-bar").inner_text(),
              panel.locator(".step-bar").inner_text())
        check("宫格第 2 格变成“采用”色（ok 类）",
              "ok" in (panel.locator(".chip").nth(1).get_attribute("class") or ""),
              panel.locator(".chip").nth(1).get_attribute("class"))

        # 驳回一处：保留原文
        panel.locator(".item button:has-text('保留原文')").first.click()
        page.wait_for_timeout(900)
        check("保留原文也算定过（已定 2 / 8）",
              "已定 2 / 8" in panel.locator(".step-bar").inner_text(),
              panel.locator(".step-bar").inner_text())
        check("宫格第 3 格变成“保留”色（bad 类）",
              "bad" in (panel.locator(".chip").nth(2).get_attribute("class") or ""),
              panel.locator(".chip").nth(2).get_attribute("class"))

        # 回看已定的一处：能改判（作用域只到这一处）
        panel.locator(".chip").nth(0).click()
        page.wait_for_timeout(400)
        check("点宫格能回到已定的那一处", "第 1 / 8 处" in panel.locator(".counter").inner_text(),
              panel.locator(".counter").inner_text())
        panel.locator(".item button:has-text('保留原文')").first.click()
        page.wait_for_timeout(900)
        check("已定的一处可以改判（采用 → 保留）",
              "bad" in (panel.locator(".chip").nth(0).get_attribute("class") or ""),
              panel.locator(".chip").nth(0).get_attribute("class"))

        # 全部定完 → 出现“可以合成”，合成本章按钮解锁
        for _ in range(12):
            if panel.locator(".step-bar .pill:has-text('全部定完')").count():
                break
            panel.locator(".step-bar button:has-text('跳到没定的')").first.click()
            page.wait_for_timeout(300)
            panel.locator(".item button:has-text('采用模型改法')").first.click()
            page.wait_for_timeout(800)
        check("全定完给出明确出口（“全部定完，可以合成”）",
              panel.locator(".step-bar .pill:has-text('全部定完')").count() == 1)
        apply_btn = panel.locator(".detail-head button:has-text('合成本章')")
        check("合成本章按钮解锁", not apply_btn.first.is_disabled())
        shot(page, "J2_stage5_done", keep_scroll=True)
        apply_btn.first.click()
        page.wait_for_timeout(900)
        check("合成后不再显示待裁定的那一章（走完闭环）",
              panel.locator(".chip").count() == 0 or "已合成" in panel.inner_text(),
              panel.inner_text()[:120])

        check("step5 裁定链路没有 4xx", not [x for x in bad if "stage5" in x],
              [x for x in bad if "stage5" in x][:3])
        check("step5 面板没有 console 报错",
              not [e for e in errs if "stage5" in e.lower()], errs[:3])
    finally:
        ctx.close()


def mock_json(path):
    """读假后端的一个只读端点（e2e 回查后端状态用，不碰真仓库）。"""
    with urllib.request.urlopen(MOCK_API + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_drafts(browser):
    """P1-3（2026-11-15）：评论/发言**先存草稿、显式发送才进对话**。

    为什么单独一段：这一类的坏法会让后端用例全绿 —— 输入框关一下就清空、
    切到另一个主题把草稿串过去、写着写着刷新一下没了、看着像发过了其实没发。
    所以两头都要断：界面上草稿在不在，以及**后端到底收到没收到**。
    假后端为此补了 /discuss/*、/review/report、/review/comment 与两个回查端点。
    """
    print("\n=== K. 草稿：先存草稿、显式发送才进对话（P1-3）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "drafts")
    try:
        # ---------- 1. 章节讨论 ----------
        goto_chapter_list(page)
        box = page.locator("details.discuss-box").first
        box.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        lis = box.locator(".discuss-list li")
        check("讨论面板列出两条讨论", lis.count() == 2, lis.count())

        lis.nth(0).click()                       # 结局要不要开信
        page.wait_for_timeout(700)
        area = box.locator(".discuss-say textarea")
        area.fill("草稿甲：结局她拆信但不读完")
        page.wait_for_timeout(300)
        check("写进框里就标出“草稿，还没说出去”",
              box.locator(".discuss-actions .pill:has-text('草稿')").count() == 1,
              box.locator(".discuss-actions").inner_text()[:80])

        lis.nth(1).click()                       # 切到另一条讨论
        page.wait_for_timeout(700)
        check("切到另一条讨论，输入框是空的（草稿不串味）",
              area.input_value() == "", area.input_value())
        check("另一条讨论上没有“草稿”提示",
              box.locator(".discuss-actions .pill:has-text('草稿')").count() == 0)

        lis.nth(0).click()                       # 切回来
        page.wait_for_timeout(700)
        check("切回来草稿还在（各自一份）",
              "草稿甲" in area.input_value(), area.input_value())

        says = mock_json("/_mock/discuss_says").get("says") or []
        check("没点发送 → 后端一条都没收到（写草稿不等于说出去）", says == [], says)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2200)
        # 注意：刷新后不再调 settle_disclaimer —— 它自己带“首启必弹”的断言，
        # 而这里 ack 已在 localStorage 里，弹窗**不该**再出现（调用即假红）。
        goto_chapter_list(page)
        box = page.locator("details.discuss-box").first
        box.scroll_into_view_if_needed()
        lis = box.locator(".discuss-list li")
        lis.nth(0).click()
        page.wait_for_timeout(800)
        area = box.locator(".discuss-say textarea")
        check("刷新页面后草稿还在（存在本机，不靠内存）",
              "草稿甲" in area.input_value(), area.input_value())
        shot(page, "K1_draft_discuss", keep_scroll=True)

        box.locator(".discuss-actions button:has-text('以作者身份说')").first.click()
        page.wait_for_timeout(900)
        says = mock_json("/_mock/discuss_says").get("says") or []
        check("点“以作者身份说”→ 后端真的收到这一轮",
              len(says) == 1 and "草稿甲" in says[0].get("text", ""), says)
        check("发送成功后输入框清空、草稿提示消失",
              area.input_value() == ""
              and box.locator(".discuss-actions .pill:has-text('草稿')").count() == 0,
              area.input_value())

        # ---------- 2. 审稿页的评论 ----------
        page.locator("nav.tabs button", has_text="审稿").first.click()
        page.wait_for_timeout(1600)
        panel = page.locator(".card", has=page.locator("h4:text-is('确定性检查汇总')"))
        check("审稿页读到审查报告（1 章 2 条）", panel.count() == 1, panel.count())
        page.locator(".chapter-head").first.click()      # 展开第 1 章
        page.wait_for_timeout(600)
        rows = page.locator(".finding-row")
        check("展开后有 2 条审查意见", rows.count() == 2, rows.count())

        rows.nth(0).locator("button:has-text('💬')").click()
        page.wait_for_timeout(400)
        cbox = rows.nth(0).locator(".comment-input")
        cbox.fill("草稿乙：这条因果要补一句过渡")
        page.wait_for_timeout(300)
        rows.nth(0).locator("button:has-text('收起')").click()   # 收起（留着草稿）
        page.wait_for_timeout(400)
        hint = rows.nth(0).locator(".comment-draft-hint")
        check("收起输入框后明说“有草稿，还没发送”（不假装已经说过了）",
              hint.count() == 1 and "还没发送" in hint.inner_text(),
              hint.inner_text() if hint.count() else "无")
        shot(page, "K2_draft_comment_closed", keep_scroll=True)

        cmts = mock_json("/_mock/review_comments").get("comments") or []
        check("收起 ≠ 发送 → 后端一条评论都没收到", cmts == [], cmts)

        rows.nth(0).locator("button:has-text('继续写')").click()
        page.wait_for_timeout(400)
        check("“继续写”把刚才那份草稿接回来",
              "草稿乙" in rows.nth(0).locator(".comment-input").input_value(),
              rows.nth(0).locator(".comment-input").input_value())
        shot(page, "K2_draft_comment", keep_scroll=True)

        rows.nth(0).locator(".comment-input-row button:has-text('发送')").click()
        page.wait_for_timeout(900)
        cmts = mock_json("/_mock/review_comments").get("comments") or []
        check("点“发送”→ 后端收到这条评论",
              len(cmts) == 1 and "草稿乙" in cmts[0].get("text", ""), cmts)
        check("发送成功后草稿提示消失",
              rows.nth(0).locator(".comment-draft-hint").count() == 0)

        # 只断**被测端点**（技能里那条）：审稿页别处的 /review/ledger、/review/structure
        # 假后端本来就没实现，把它们算进来是整页假红。
        mine = [x for x in bad if "/discuss/" in x or "/review/comment" in x or "/review/report" in x]
        check("草稿链路没有 4xx", not mine, mine[:3])
    finally:
        ctx.close()


def test_page_view(browser):
    """P2-1（2026-11-15）：改动的**阅读态呈现** + 列表↔版面双向定位。

    为什么单独一段：这一类的坏法都“看着正常、其实错位”——
    高亮错行、点一段跳到另一处、新增段在原文栏里凭空出现、
    在段落上点“采用”改的却是别的段。后端用例碰不到这些，
    只能靠真机 + 有状态假后端（stage5 夹具里带 layout）。
    """
    print("\n=== L. 阅读态版面：两稿按成书版面排 + 双向定位（P2-1）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "pageview")
    try:
        page.locator("nav.tabs button", has_text="审稿").first.click()
        page.wait_for_timeout(1400)
        panel = page.locator(".card", has=page.locator("h3:text-is('阶段 5 改稿裁定')"))
        panel.scroll_into_view_if_needed()
        page.wait_for_timeout(400)

        # 第 2 章是这一段的干净状态（第 1 章被 J 段一路定完并合成过了）
        panel.locator(".list .row", has_text="第 2 章").first.click()
        page.wait_for_timeout(900)
        check("默认是“逐处看”，版面没有展开（不让两套视图同时糊上去）",
              panel.locator(".view-toggle button:has-text('逐处看')").count() == 1
              and panel.locator(".page-view").count() == 0)

        panel.locator(".view-toggle button:has-text('整页看')").first.click()
        # 等元素真出现，不等固定时长（2026-11-24）：原来 `wait_for_timeout(600)`，
        # 慢一轮时两栏还没渲染完就去数 `.s5p.chg`，报的是 "Locator.get_attribute
        # Timeout"——看着像产品坏了，其实只是测试抢跑。实测同一提交下 一次过 / 一次红。
        page.locator(".page-cols .page-col").first.wait_for(state="visible", timeout=15000)
        page.wait_for_timeout(200)
        cols = panel.locator(".page-cols .page-col")
        check("版面给了两栏（原稿 / 改后）", cols.count() == 2, cols.count())
        raw_p = cols.nth(0).locator(".s5p")
        prop_p = cols.nth(1).locator(".s5p")
        check("原稿栏排出了全章 8 段（含没改的）", raw_p.count() == 8, raw_p.count())
        check("改后栏也排出 8 段", prop_p.count() == 8, prop_p.count())
        check("原稿栏标出 7 处改动（新增那段原文里没有）",
              cols.nth(0).locator(".s5p.chg").count() == 7,
              cols.nth(0).locator(".s5p.chg").count())
        check("改后栏标出 7 处改动（删掉那段改后没有）",
              cols.nth(1).locator(".s5p.chg").count() == 7,
              cols.nth(1).locator(".s5p.chg").count())
        check("没改的那一段不带编号（高亮只落在改动上）",
              raw_p.nth(0).locator(".s5p-tag").count() == 0
              and raw_p.nth(0).locator(".s5p-text").inner_text().startswith("这一段模型没动"),
              raw_p.nth(0).inner_text()[:60])
        shot(page, "L1_stage5_page", keep_scroll=True)

        # 正文 → 列表：点改后栏第 2 个改动段，逐处看应当停在第 2 处
        cols.nth(1).locator(".s5p.chg").nth(1).click()
        page.wait_for_timeout(500)
        check("点版面上的一段 → 当前这一处跟着变（正文→列表）",
              panel.locator(".s5p.is-cur").count() >= 1,
              panel.locator(".s5p.is-cur").count())
        panel.locator(".view-toggle button:has-text('逐处看')").first.click()
        page.wait_for_timeout(400)
        counter = panel.locator(".counter").inner_text()
        check("切回逐处看，停的正是刚才点的那一处（第 2 / 8 处）",
              "第 2 / 8 处" in counter, counter)

        # 列表 → 正文：那一处的按钮能把版面切过去并高亮它
        panel.locator(".item button:has-text('在版面里看这一处')").first.click()
        page.wait_for_timeout(600)
        check("“在版面里看这一处”切到版面并高亮同一段",
              panel.locator(".page-view").count() == 1
              and panel.locator(".s5p.is-cur").count() >= 1,
              panel.locator(".s5p.is-cur").count())

        # 直接在段落上定：改后栏第 2 个改动段（第 2 处）点“采用”
        target = cols.nth(1).locator(".s5p.chg").nth(1)
        target.hover()
        page.wait_for_timeout(200)
        target.locator("button:has-text('采用')").first.click()
        page.wait_for_timeout(900)
        check("在版面段落上点“采用”→ 那一段变成采用色（绿）",
              "ok" in (target.get_attribute("class") or ""), target.get_attribute("class"))
        check("已定的段落有字的结论（不只靠底色）",
              "已采用" in target.inner_text(), target.inner_text()[:40])
        shot(page, "L2_stage5_page_decided", keep_scroll=True)
        panel.locator(".view-toggle button:has-text('逐处看')").first.click()
        page.wait_for_timeout(400)
        check("逐处视图的已定计数跟着走（不是只改了版面）",
              "已定 1 / 8" in panel.locator(".step-bar").inner_text(),
              panel.locator(".step-bar").inner_text())

        check("版面链路没有 4xx", not [x for x in bad if "stage5" in x],
              [x for x in bad if "stage5" in x][:3])
    finally:
        ctx.close()


def test_annotation_to_rewrite(browser):
    """P0-2（2026-11-14 落地，2026-11-15 补视觉验收）：批注 → 只改它指的那一段。

    为什么必须真机验：这条链的入口在界面上（点批注上的“改这一处”），
    后端单测再全绿也拦不到前端那一层的错 —— 本轮就是这么发现的：
    `submitRefine` 里读了没声明的 `override`，点下去当场 ReferenceError，
    一个请求都发不出去（批注改稿从上线起就是坏的，台账却记着“已落地”）。
    所以这里两头都断：界面上候选真的出来了、后端收到的确实是这条批注。
    """
    print("\n=== M. 批注 → 改这一处 → 采用（P0-2）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "annflow")
    try:
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(1500)
        # 2026-11-26：章节页默认就在审阅台（chapterView 默认 desk）→ 这时按钮文案是
        # “回到列表”，没有“审阅台”可点。旧代码硬点必等满 30 秒。
        if page.locator("button:has-text('审阅台')").count():
            page.locator("button:has-text('审阅台')").first.click()
        _ensure_desk_edit(page)
        page.wait_for_selector(".ce-textarea", timeout=8000)
        page.wait_for_timeout(500)
        page.locator("button:has-text('段落批注')").first.click()
        page.wait_for_timeout(800)

        anns = page.locator(".ce-ann")
        check("批注列表给出 3 条（正常 / 位置变了 / 已失效）", anns.count() == 3, anns.count())
        check("失效的那条写明“引用的原句已改/已删”",
              "已改/已删" in anns.nth(2).inner_text(), anns.nth(2).inner_text()[:60])

        # 分组 + 跳段（2026-11-17，P1-9）：只有 open/missing 时“已收口”永远是空的，
        # 所以 mock 特意给了一条 resolved；分组标题必须把两边的条数都说出来。
        heads = page.locator(".ce-ann-group-t")
        check("批注分两组（未收口在前 / 已收口在后）",
              heads.count() == 2 and "未收口" in heads.nth(0).inner_text()
              and "已收口" in heads.nth(1).inner_text(),
              [heads.nth(i).inner_text() for i in range(heads.count())])
        check("未收口 3 条（处理过的没有被算进去）", "3" in heads.nth(0).inner_text(),
              heads.nth(0).inner_text())
        check("已收口默认折叠（只剩未收口那 3 条占屏幕）", anns.count() == 3, anns.count())

        anns.first.locator("button.ce-ann-jump").click()
        page.wait_for_timeout(900)
        on = page.locator(".ce-para.on")
        check("点批注上的“第 1 段”→ 正文里真的选中那一段（不用自己滚回去数）",
              on.count() == 1 and "1" in on.first.locator(".ce-para-no").inner_text(),
              on.count())

        heads.nth(1).locator("button").click()
        page.wait_for_timeout(500)
        check("点“展开”→ 已收口那条回到列表里（共 4 条）", anns.count() == 4, anns.count())
        check("已收口那条保留“已处理”痕迹（不是消失）",
              "已处理" in anns.nth(3).inner_text(), anns.nth(3).inner_text()[-40:])
        # 分组标题在侧栏下方，整页截图按“滚动归零”纪律拍不到 —— 把这一块滚到眼前再拍一张。
        page.locator(".ce-ann-group-t").first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "M0_ann_groups_el.png"))
        shot(page, "M0_ann_groups")

        anns.first.locator("button:has-text('改这一处')").click()
        page.wait_for_timeout(3500)
        check("点“改这一处”→ 出候选（改前/改后并排），正文一个字没动",
              page.locator(".ce-cand").count() == 1
              and page.locator(".ce-cand .ce-diff-col").count() == 2,
              page.locator(".ce-cand").count())
        intent_txt = (page.locator(".ce-cand-intent").inner_text()
                      if page.locator(".ce-cand-intent").count() else "")
        check("候选卡写出“模型的理解”（模型先复述要改什么，不是只给 diff）",
              "我理解为要改" in intent_txt, intent_txt[:60])
        check("批注当场变成显眼的状态标签“已出候选，待采用”（open → addressed，不自动算完）",
              anns.first.locator(".pill.st-gate", has_text="待采用").count() == 1,
              anns.first.inner_text()[-40:])
        check("已经改过一次的批注，按钮改口为“再改一版”（不让人以为还得点一次“改这一处”）",
              "再改一版" in anns.first.inner_text(), anns.first.inner_text()[-40:])

        flow = mock_json("/_mock/ann_flow")
        pv = flow.get("previews") or []
        check("后端收到的确实是这条批注（annotation_id + 原文）",
              bool(pv) and pv[-1].get("annotation_id") == "a1"
              and pv[-1].get("feedback") == "开头要再慢一点", pv)
        check("改的是它指的那一段（第 1 段 / index 0）",
              bool(pv) and str(pv[-1].get("paragraph_index")) == "0", pv)
        applied_base = len(flow.get("applied") or [])
        check("还没点采用 → 后端 applied 没增加（候选不等于成品）",
              applied_base == len(flow.get("applied") or []), applied_base)
        # 批注卡与候选区在侧栏里不是同一屏：各拍一张，目检才看得到两个状态
        anns.first.screenshot(path=str(OUT / "M1_ann_state.png"))
        shot(page, "M1_ann_to_cand", keep_scroll=True)

        page.locator(".ce-cand-head button:has-text('采用')").first.click()
        page.wait_for_timeout(1500)
        flow = mock_json("/_mock/ann_flow")
        ap = flow.get("applied") or []
        check("点“采用”→ 后端记下这一段被换掉",
              len(ap) == applied_base + 1 and "阶前积出一小滩暗色的水" in ap[-1].get("after", ""), ap)
        check("采用后候选卡收起来（不留一张“还能点”的假卡）",
              page.locator(".ce-cand").count() == 0, page.locator(".ce-cand").count())
        shot(page, "M2_ann_applied", keep_scroll=True)

        mine = [x for x in bad if "/chapters/paragraph" in x or "/chapters/annotations" in x]
        check("批注改稿链路没有 4xx", not mine, mine[:3])
        ce_errs = [e for e in errs if "override" in e or "ReferenceError" in e]
        check("界面上没有 ReferenceError 一类脚本错（本轮修的就是这个）",
              not ce_errs, ce_errs[:2])
    finally:
        ctx.close()


def mock_post(path, body=None):
    """给假后端发一条写请求（e2e 里布置“下一次会撞冲突”这类场景用）。"""
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(MOCK_API + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_conflict_no_overwrite(browser):
    """Papilot ⑧（2026-11-15）：候选出来之后这一段被改过 —— 冲突摊开、不覆盖。

    为什么要真机：这条的坏法正好是最危险的那种 —— 后端拒了，界面却当成功、
    或者自动重试一次把作者刚写的正文换掉。两端都要断：界面必须把两份正文摊出来
    让人选，且**只有作者点“仍然采用”才覆盖**。
    """
    print("\n=== N. 冲突摊开、不自动覆盖（Papilot ⑧）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "conflict")
    try:
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(1500)
        # 2026-11-26：章节页默认就在审阅台（chapterView 默认 desk）→ 这时按钮文案是
        # “回到列表”，没有“审阅台”可点。旧代码硬点必等满 30 秒。
        if page.locator("button:has-text('审阅台')").count():
            page.locator("button:has-text('审阅台')").first.click()
        _ensure_desk_edit(page)
        page.wait_for_selector(".ce-textarea", timeout=8000)
        page.wait_for_timeout(500)
        page.locator("button:has-text('段落批注')").first.click()
        page.wait_for_timeout(800)

        applied_base = len((mock_json("/_mock/ann_flow").get("applied") or []))
        # 先让后端布置“这一段已经变过”（下一条批注 → 候选可用，但采用会撞冲突）
        page.locator(".ce-ann").nth(1).locator("button.primary").first.click()
        page.wait_for_timeout(3000)
        mock_post("/_mock/conflict_next", {})
        check("候选照常出得来（冲突发生在采用那一刻，不是在生成时）",
              page.locator(".ce-cand").count() == 1, page.locator(".ce-cand").count())

        page.locator(".ce-cand-head button:has-text('采用')").first.click()
        page.wait_for_timeout(1200)
        block = page.locator(".ce-conflict")
        check("采用撞上冲突 → 界面摊开冲突块（不静默覆盖）", block.count() == 1, block.count())
        check("撞了冲突就不给原来的“采用”主按钮了（危险动作不在原位）",
              page.locator(".ce-cand-head button:has-text('采用')").count() == 0,
              page.locator(".ce-cand-head").inner_text()[:60])
        if block.count():
            txt = block.inner_text()
            check("两份正文并排摆出来（现在的 / 候选基于的）",
                  "现在的正文" in txt and "候选基于的正文" in txt, txt[:120])
            check("三条出路齐（重新出候选是主操作 / 覆盖是危险动作 / 先不决定）",
                  block.locator("button.primary:has-text('重新出候选')").count() == 1
                  and block.locator("button.danger:has-text('仍然采用')").count() == 1
                  and block.locator("button:has-text('先不决定')").count() == 1)
            block.scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            block.screenshot(path=str(OUT / "N1_conflict_block.png"))
        shot(page, "N1_conflict", keep_scroll=True)

        flow = mock_json("/_mock/ann_flow")
        check("冲突时后端 applied 没增加（一个字都没写进去）",
              len(flow.get("applied") or []) == applied_base, flow.get("applied"))
        check("后端记下了这条冲突，且当时没被强制覆盖",
              bool(flow.get("conflicts"))
              and (flow.get("conflicts") or [{}])[-1].get("forced") is False,
              flow.get("conflicts"))

        page.locator(".ce-conflict button:has-text('仍然采用')").first.click()
        page.wait_for_timeout(1500)
        flow = mock_json("/_mock/ann_flow")
        check("作者明确点“仍然采用”才真的覆盖（覆盖是他的决定）",
              len(flow.get("applied") or []) == applied_base + 1, flow.get("applied"))
        check("覆盖后冲突块收起、候选卡也收起",
              page.locator(".ce-conflict").count() == 0
              and page.locator(".ce-cand").count() == 0,
              (page.locator(".ce-conflict").count(), page.locator(".ce-cand").count()))

        mine = [x for x in bad if "/chapters/paragraph" in x]
        check("冲突链路的 4xx 只来自被测端点的那一次 409",
              all("409" in x or "/chapters/paragraph/apply" in x for x in mine), mine[:3])
    finally:
        ctx.close()


def test_write_log(browser):
    """P2-2 的“一任务一记录”（2026-11-15）：这一章的改动流水，看得见、记得准。

    为什么值得单独一段：审稿时最常问“这一段怎么变成这样了”，而答案原来散在三处
    （段落历史记内容、候选区记待采用、快照记整章），没有按时间的流水。
    这里断两头：界面读得到历史记录，且**采用一条候选之后**列表真的多出一条
    （不是一份静态示例）。
    """
    print("\n=== O. 这一章的改动流水（P2-2 一任务一记录）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "writelog")
    try:
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(1500)
        # 2026-11-26：章节页默认就在审阅台（chapterView 默认 desk）→ 这时按钮文案是
        # “回到列表”，没有“审阅台”可点。旧代码硬点必等满 30 秒。
        if page.locator("button:has-text('审阅台')").count():
            page.locator("button:has-text('审阅台')").first.click()
        _ensure_desk_edit(page)
        page.wait_for_selector(".ce-textarea", timeout=8000)
        page.wait_for_timeout(700)
        # 侧栏（含“这一章的改动”）在审阅台里默认收起 —— 先切到“段落批注”，
        # 那一态强制展开侧栏（否则这一块的用例测的是“没渲染”，不是“没数据”）。
        page.locator("button:has-text('段落批注')").first.click()
        page.wait_for_timeout(800)

        block = page.locator(".ce-side-block", has=page.locator(".ce-side-title:text-is('这一章的改动')"))
        check("侧栏有“这一章的改动”一块", block.count() == 1, block.count())
        rows = block.locator(".ce-wlog")
        base_rows = rows.count()
        # 相对基线：前面几段（M/N）也会真的写正文、也就真的记账 —— 写死 2 条会互相吃环境。
        check("读得到这一章的历史改动（至少 mock 预置的 2 条）", base_rows >= 2, base_rows)
        first = rows.first.inner_text() if rows.count() else ""
        check("每条写清时间 / 来源 / 段号 / 字数变化",
              "AI 段落改写" in first and "段" in first and "→" in first, first[:80])
        shot(page, "O1_write_log", keep_scroll=True)

        # 采用一条候选 → 流水里应当多一条
        page.locator(".ce-ann").first.locator("button.primary").first.click()
        page.wait_for_timeout(3000)
        check("先出候选", page.locator(".ce-cand").count() == 1, page.locator(".ce-cand").count())
        page.locator(".ce-cand-head button:has-text('采用')").first.click()
        page.wait_for_timeout(1500)

        rows = block.locator(".ce-wlog")
        check("采用之后流水多出一条", rows.count() == base_rows + 1, rows.count())
        check("最新那条就是刚才那次采用",
              "AI 段落改写" in rows.first.inner_text(), rows.first.inner_text()[:80])
        block.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        block.screenshot(path=str(OUT / "O2_write_log_block.png"))
        shot(page, "O2_write_log_after", keep_scroll=True)

        mine = [x for x in bad if "/chapters/write_log" in x or "/chapters/paragraph" in x]
        check("流水链路没有 4xx", not mine, mine[:3])
    finally:
        ctx.close()



def test_multi_candidate_switch(browser):
    """P0-1 + Papilot 3.3：一次出 1 主 + 2 备选，切换候选零调用（2026-11-15）。

    为什么单独锁：这两条最容易被“看起来实现了”骗过去 —— 候选标签画出来很简单，
    但“切一条不等于再花一次钱”只能靠数请求证明：切换期间不能出现
    /chapters/paragraph/preview 或 /jobs/* 的新请求。
    文本也必须真的不同，否则切了跟没切一样（假绿）。
    """
    print("\n=== P. 多候选：一次出三条，切换不花钱（P0-1 / 3.3）===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "multicand")
    reqs = []
    page.on("request", lambda r: reqs.append(r.url))
    try:
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(1500)
        # 2026-11-26：章节页默认就在审阅台（chapterView 默认 desk）→ 这时按钮文案是
        # “回到列表”，没有“审阅台”可点。旧代码硬点必等满 30 秒。
        if page.locator("button:has-text('审阅台')").count():
            page.locator("button:has-text('审阅台')").first.click()
        _ensure_desk_edit(page)
        page.wait_for_selector(".ce-textarea", timeout=8000)
        page.wait_for_timeout(500)
        page.locator("button:has-text('段落批注')").first.click()
        page.wait_for_timeout(800)

        page.locator(".ce-ann").nth(1).locator("button.primary").first.click()
        page.wait_for_timeout(3000)
        check("候选卡出来了", page.locator(".ce-cand").count() == 1, page.locator(".ce-cand").count())

        tabs = page.locator(".ce-cand-tab")
        check("一次给了 3 条候选（1 主 + 2 备选）", tabs.count() == 3, tabs.count())
        check("界面上写明“这次一次出了 3 条”",
              "一次出了 3 条" in page.locator(".ce-cand-pick").inner_text(),
              page.locator(".ce-cand-pick").inner_text()[:80])
        shot(page, "P1_multi_candidate", keep_scroll=True)

        after = page.locator(".ce-cand .ce-diff-col").nth(1)
        # 从这一刻起数请求：切换候选本身不许再发任何“生成/轮询”请求
        reqs.clear()
        tabs.nth(0).click()
        page.wait_for_timeout(400)
        txt1 = after.inner_text()
        tabs.nth(1).click()
        page.wait_for_timeout(300)
        txt2 = after.inner_text()
        tabs.nth(2).click()
        page.wait_for_timeout(300)
        txt3 = after.inner_text()

        check("切到第 1 条 → 改后换成候选一的文本", "候选一" in txt1, txt1[:60])
        check("切到第 2 条 → 又换成候选二（不是同一段文字）",
              "候选二" in txt2 and txt2 != txt1, txt2[:60])
        check("切回最新那条 → 是模型给的主方案",
              "阶前积出一小滩暗色的水" in txt3, txt3[:60])

        spent = [u for u in reqs if "paragraph/preview" in u or "/jobs/" in u]
        check("切换候选期间没有一次“生成/轮询”请求（审阅零调用）", spent == [], spent[:3])

        page.locator(".ce-cand-head button:has-text('采用')").first.click()
        page.wait_for_timeout(1200)
        flow = mock_json("/_mock/ann_flow")
        ap = flow.get("applied") or []
        check("采用的就是当前选中那条（不是永远用最新）",
              bool(ap) and "阶前积出一小滩暗色的水" in ap[-1].get("after", ""), ap[-1:])

        mine = [x for x in bad if "/chapters/paragraph" in x]
        check("多候选链路没有 4xx", not mine, mine[:3])
    finally:
        ctx.close()


def test_narrow_window(browser):
    """980 宽（主进程 `minWidth`）下界面还能不能用（2026-11-20）。

    为什么要单开一段：`console/main/index.js` 是 `width:1280 / minWidth:980` ——
    作者可以把窗口拖到 980，而**此前最窄只测到 1366**。1366 下已经出过一次真事故
    （保存按钮被挤出窗口只露半个紫块，346 条 e2e 全绿），980 只会更挤。

    这里**不要求 45 汉字/行**：那是宽窗口的目标，980 下压到 30 出头是合理的。
    只要求三件事：标题行不被挤爆、没有东西被挤出屏幕、编辑/阅读两态切得动。
    """
    print("\n=== F2. 980 宽（minWidth）下还能用 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "narrow")
    try:
        page.set_viewport_size({"width": 980, "height": 760})
        page.wait_for_timeout(400)
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(700)
        if page.locator("button:has-text('审阅台')").count():
            page.locator("button:has-text('审阅台')").first.click()
        try:
            _ensure_desk_edit(page)
            page.wait_for_selector(".ce-textarea", timeout=6000)
        except Exception:                                   # noqa: BLE001
            check("980 宽下审阅台能进（中栏编辑器渲染）", False, "中栏没渲染")
            return
        page.wait_for_timeout(500)
        hh = page.evaluate(_DESK_HEAD_OVERFLOW_JS)
        _bad = hh.get("bad") or []
        check("980 宽 · 标题行没有元素溢出/竖排/截字", not hh.get("missing") and not _bad,
              ("；".join("%s：%s（%s）" % (b["why"], b["el"],
                                          {k: v for k, v in b.items()
                                           if k not in ("why", "el")})
                        for b in _bad)[:200] if _bad else
               "没有标题行" if hh.get("missing") else
               "量的是 %s 那一行 · %s 个元素都在框内"
               % (hh.get("headText"), hh.get("items"))))
        ov = _overflow_probe(page, "narrow_980")
        check("980 宽 · 没有元素被挤出屏幕", not ov,
              "；".join("%s(+%dpx)" % (b["el"], b["over"]) for b in ov[:4]))
        page.screenshot(path=str(OUT / "narrow_980_read.png"))
        # 两态切得动 = 窄窗口下按钮还在可点位置
        page.locator("button:has-text('阅读')").first.click()
        page.wait_for_timeout(700)
        check("980 宽 · 点得动 阅读（正文换到只读态）",
              page.locator(".ce-read").count() >= 1)
        page.locator("button:has-text('编辑')").first.click()
        page.wait_for_timeout(400)
        check("980 宽 · 点得回 编辑", page.locator(".ce-textarea").count() >= 1)
        mine = [x for x in bad if "/chapters/" in x]
        check("980 宽 · 章节链路没有 4xx", not mine, mine[:3])
    finally:
        ctx.close()


def test_materials_tab(browser):
    """素材页（2026-11-27）：不再用“卡片/碎片”考用户，直接给阶段 1 读入清单。"""
    print("\n=== G. 素材页：说人话 + 阶段 1 读入预览 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "materials")
    try:
        page.locator("nav.tabs button", has_text="素材").first.click()
        page.wait_for_timeout(1000)
        check("素材页出现“阶段 1 会读/跳过”摘要",
              page.locator(".material-scan").count() >= 1)
        page.locator("button:has-text('看清单')").first.click()
        page.wait_for_timeout(300)
        body = page.locator(".material-scan").inner_text()
        check("说明写成“不要求模板格式”", "不要求模板格式" in body, body[:240])
        check("计数与清单一致（会读 2 / 跳过 1）",
              "会读 2 篇" in body and "跳过 1 篇" in body, body[:240])
        check("清单列出文件名与跳过原因",
              "角色卡_阿岚.md" in body and "README.md" in body
              and "元文件" in body, body[:300])
        check("素材页没有把“结构化卡片”这种内部词摆在按钮上",
              page.locator("nav.tabs button", has_text="结构化卡片").count() == 0)
        shot(page, "materials_scan")
        mine = [x for x in bad if "/materials/scan" in x]
        check("素材页 /materials/scan 没有 4xx/5xx", not mine, mine[:3])
    finally:
        ctx.close()


def _alive(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:                                       # noqa: BLE001
        return False


def preflight():
    """三个必需服务缺一个就整轮不跑（2026-11-17）。

    为什么必须前置拦：缺服务不会报错，只会在第一个 `.flow-strip` 上等 30 秒超时，
    于是长出一大片假红 —— 看着像产品坏了，其实是**没执行**。这正是"失败分不清
    真红还是环境红"的来源。真后端（REAL_API）不在此列：test_real 自己会 SKIP。
    返回空串=可跑。
    """
    need = [("静态站点", SITE + "/index.html"), ("假后端", MOCK_API + "/state"),
            ("冷启动假后端", COLD_API + "/state")]
    down = ["%s（%s）" % (n, u) for n, u in need if not _alive(u)]
    if not down:
        return ""
    return ("缺服务：%s。\n"
            "一条命令起全套：python tests/e2e/run_all.py --only ux\n"
            "手工起的话需要四个：\n"
            "  python -m http.server 8091 --directory console/renderer/dist\n"
            "  python tests/e2e/mock_nf_api_state.py --port 8798\n"
            "  python tests/e2e/mock_nf_api_state.py --port 8797 --cold\n"
            "  python scripts/nf_api.py --port 8799 --allow-fake --root tests/e2e/fixture_project"
            % "；".join(down))


def main():
    block = preflight()
    if block:
        print("[SKIP] 本脚本是**真机**验收，需要外部服务；按纪律“缺服务 = 未执行 ≠ 失败”。")
        print("       " + block.replace("\n", "\n       "))
        return 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        try:
            test_mock(browser)
            test_real(browser)
            test_dark(browser)
            test_about_project(browser)
            test_desk_width(browser)
            test_narrow_window(browser)
            test_candidates(browser)
            test_sandbox_review(browser)
            test_merge_conflicts(browser)
            test_stage5_step(browser)
            test_drafts(browser)
            test_page_view(browser)
            test_annotation_to_rewrite(browser)
            test_conflict_no_overwrite(browser)
            test_write_log(browser)
            test_multi_candidate_switch(browser)
            test_materials_tab(browser)
        finally:
            browser.close()

    # 截图自检（2026-10-31）：错位/半路的图**不许进交付**。
    # 这是唯一会因"图能不能当证据"而变红的判据 —— 截图是我们给人看的凭据。
    print("\n=== I. 截图自检（能不能作为证据）===")
    bad = [c for c in SHOT_CHECKS if not c["ok"]]
    check("全部 %d 张截图通过几何自检（顶栏在顶端 / 滚动归零 / 图高合理 / 元素不出屏）"
          % len(SHOT_CHECKS), not bad,
          [{"name": b["name"], "notes": b["notes"]} for b in bad][:4])
    if SHOT_CHECKS:
        print("      已核对 %d 张；有问题 %d 张" % (len(SHOT_CHECKS), len(bad)))
    if OVERFLOW_PROBE:
        print("\n  ⚠️ 出屏元素清单（%d 张图里有）：" % len(OVERFLOW_PROBE))
        for it in OVERFLOW_PROBE:
            print("     %-28s %s" % (it["name"],
                                     "；".join("%s +%dpx" % (b["el"], b["over"])
                                               for b in it["bad"][:5])))

    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("截图目录: %s" % OUT)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
