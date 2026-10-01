# -*- coding: utf-8 -*-
"""GUI 暗色主题可读性 + 「打开文件」路径护栏（2026-10-01 起）。

## 两条都是用户实测反馈，且都是「改了但没测」的典型

1. **暗色主题下文本读不出来**：`console/src/style.css` 的输入框/卡片写死
   `background:#fff`，而文字色是 `var(--ink)` —— 暗色主题下 `--ink` 是浅色，
   于是「浅底浅字」。更糟的是暗色覆盖只写了 `.theme-dark` 一套，
   三套 `night-*`（暗夜蓝/绿/暖）完全裸奔，连顶栏都是白色半透明。
   → 判据 A/B/C/D：禁止硬编码浅底、暗色主题必须齐备控件变量、
   `.is-dark` 公共覆盖必须在、**并且用 WCAG 对比度做数值断言**。

2. **设置页「在文件夹中显示」点了没反应**：`.env`、`data/`、`output/` 都在 workspace
   （打包态 = `%APPDATA%\\绒花墨坊\\workspace`），而 Electron 主进程用
   `path.resolve(ROOT, ...)`（打包态 = 安装目录里的 payload）解析 → 必然
   「文件不存在」；`pathAllowed` 又按 ROOT 前缀 slice 算相对路径，
   workspace 路径会被切出一段垃圾 → 全部判「不在白名单」。
   前端还**不检查返回值**、照样提示成功 → 用户只看到「点了没反应」。
   → 判据 E：文件类 IPC 必须经 `resolveExisting`（workspace 优先）、
   前端必须看返回值、`updater:check` 不得把不可序列化的对象过 IPC。

全程静态 + 纯计算，离线、零网络、零费用。
用法：python tests/unit/test_ui_theme_and_paths.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


CSS_PATH = ROOT / "console" / "src" / "style.css"
APP_PATH = ROOT / "console" / "src" / "App.vue"
MAIN_PATH = ROOT / "console" / "main" / "index.js"

CSS = CSS_PATH.read_text(encoding="utf-8")
APP = APP_PATH.read_text(encoding="utf-8")
MAIN = MAIN_PATH.read_text(encoding="utf-8")

# 输入/卡片类选择器：这些一旦写死浅色底，暗色主题下必然「浅底浅字」
INPUT_SELECTOR_RE = re.compile(
    r"^\.(text-input|model-select|prompt-text|tree-edit|sb-item|ov-opt|ov-diff-seg|"
    r"stream-body|prompt-list|ov-multi-col|ov-adv-verdict|sb-preview|setting-row|"
    r"mini|view-toggle|export-panel)")


def _strip_comments(css):
    """去掉 /* ... */ 注释后再做源码断言 —— 注释里出现 `background:#fff`
    只是说明文字，不该判定为违规（本项目注释会主动引用反例）。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# ---------------------------------------------------------------- A
def test_A_no_hardcoded_light_background():
    print("\n[A] 不得再出现硬编码浅色底（暗色主题下=浅底浅字）")
    clean = _strip_comments(CSS)
    hits = []
    for i, line in enumerate(clean.splitlines(), 1):
        if re.search(r"background(-color)?:\s*#(fff|ffffff)\b", line, re.I):
            hits.append("style.css:%d %s" % (i, line.strip()[:70]))
        elif re.search(r"background(-color)?:\s*white\b", line, re.I):
            hits.append("style.css:%d %s" % (i, line.strip()[:70]))
    check("A1 没有 background:#fff / #ffffff / white", not hits, "；".join(hits[:5]))

    near = []
    for i, line in enumerate(clean.splitlines(), 1):
        if INPUT_SELECTOR_RE.match(line) and re.search(r"background:\s*#[ef]", line, re.I):
            near.append("style.css:%d %s" % (i, line.strip()[:70]))
    check("A2 输入/卡片类选择器不再用 #e*/#f* 近白底", not near, "；".join(near[:5]))

    check("A3 控件底色变量 --field 已定义",
          "--field:" in clean and "--field-inset:" in clean)


# ---------------------------------------------------------------- B
def _dark_theme_ids():
    m = re.search(r"const DARK_THEMES\s*=\s*\[(.*?)\];", APP, re.S)
    return re.findall(r'"([a-z-]+)"', m.group(1)) if m else []


def _theme_ids():
    m = re.search(r"const THEMES\s*=\s*\[(.*?)\];", APP, re.S)
    return re.findall(r'\{\s*id:\s*"([a-z-]+)",\s*name:', m.group(1)) if m else []


def _theme_block(css, tid, clean=True):
    src = _strip_comments(css) if clean else css
    m = re.search(r":root\.theme-%s\s*\{(.*?)\}" % re.escape(tid), src, re.S)
    return m.group(1) if m else ""


def test_B_dark_themes_have_control_vars():
    print("\n[B] 每套暗色主题都必须自带控件变量（否则回落到浅色默认值）")
    dark = _dark_theme_ids()
    check("B1 解析到 DARK_THEMES 清单", len(dark) >= 4, dark)
    for tid in dark:
        block = _theme_block(CSS, tid)
        check("B2 :root.theme-%s 存在" % tid, bool(block))
        for var in ("--field:", "--field-inset:", "--field-on:", "--topbar:",
                    "--ink-ok:", "--ink-bad:", "--ink-warn:"):
            check("B3 theme-%s 定义 %s" % (tid, var.rstrip(":")), var in block)


# ---------------------------------------------------------------- C
def test_C_is_dark_override():
    print("\n[C] 暗色公共覆盖 .is-dark 必须存在且被 App.vue 挂上")
    check("C1 style.css 有 .is-dark 规则", ".is-dark" in _strip_comments(CSS))
    check("C2 App.vue 的 setTheme 写入 is-dark 类",
          re.search(r'is-dark', APP) and "DARK_THEMES.includes" in APP)
    # 老写法（只覆盖 theme-dark）会让 night-* 裸奔，禁止回退
    legacy = re.findall(r"\.theme-dark \.(?:card|topbar|ps-progress-text|mini)", CSS)
    check("C3 没有退回「只覆盖 .theme-dark」的旧写法", not legacy, legacy)


# ---------------------------------------------------------------- D
def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = (la, lb) if la > lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def _var(block, name):
    m = re.search(r"%s:\s*(#[0-9a-fA-F]{3,6})" % re.escape(name), block)
    return m.group(1) if m else ""


def test_D_contrast_wcag():
    """数值断言：控件底 vs 正文色，对比度必须过 WCAG AA(4.5)。

    用真实公式算，不做形式断言 —— 将来谁把 --field 调成接近 --ink，
    这条会立刻红，而不是等用户来报告「字看不见」。
    """
    print("\n[D] WCAG 对比度：每套主题 --field × --ink ≥ 4.5（AA 正文）")
    # 浅色主题共用同一块控件变量（选择器是 :root, :root.theme-purple, ...），
    # 所以取全局第一个 --field 定义即可（暗色主题会在各自块里覆盖它）。
    m = re.search(r"--field:\s*(#[0-9a-fA-F]{3,6})", _strip_comments(CSS))
    default_field = m.group(1) if m else ""
    check("D1 浅色默认 --field 解析到", bool(default_field), default_field)

    ids = _theme_ids()
    check("D2 主题清单解析完整（含 night-*）", len(ids) >= 10, ids)

    for tid in ids:
        block = _theme_block(CSS, tid)
        field = _var(block, "--field") or default_field     # 浅色走共享默认块
        ink = _var(block, "--ink")
        if not field or not ink:
            check("D3 theme-%s --field/--ink 可解析" % tid, False,
                  "field=%r ink=%r" % (field, ink))
            continue
        ratio = _contrast(field, ink)
        check("D3 theme-%s 对比度 %.2f ≥ 4.5" % (tid, ratio), ratio >= 4.5,
              "field=%s ink=%s" % (field, ink))


# ---------------------------------------------------------------- E
def test_E_path_resolution_and_ipc():
    print("\n[E] 文件类 IPC 必须走 resolveExisting（workspace 优先）")
    check("E1 main 定义 resolveExisting", "function resolveExisting(" in MAIN)
    check("E2 resolveExisting 优先 workspace",
          re.search(r"function resolveExisting[\s\S]{0,300}?getWorkspaceDir\(\)", MAIN) is not None)

    # resolveExisting 内部那一处 path.resolve(ROOT, ...) 是「兜底」语义，属合法；
    # 检查的是**函数之外**是否还有裸用（那才是绕过 workspace 的旧写法）。
    body = re.search(r"function resolveExisting\([\s\S]*?\n\}", MAIN)
    rest = MAIN.replace(body.group(0), "") if body else MAIN
    bad = re.findall(r"path\.resolve\(ROOT,\s*relPath\)", rest)
    check("E3 resolveExisting 之外不再裸用 path.resolve(ROOT, relPath)", not bad, bad)

    check("E4 pathAllowed 同时识别两个根",
          "new Set([" in MAIN and "getWorkspaceDir()" in MAIN)

    # updater:check 回传不可序列化对象 → IPC reject → 前端按钮卡在「检查中…」
    check("E5 updater:check 不把 result 原对象过 IPC",
          "return { ok: true, result }" not in MAIN)
    check("E6 updater:check 只回传可序列化字段",
          "currentVersion:" in MAIN and "version: info.version" in MAIN)

    print("\n[F] 前端必须检查 IPC/reveal 的返回值（不再假成功）")
    check("F1 revealEnvInFolder 检查返回结果",
          bool(re.search(r"revealEnvInFolder[\s\S]{0,700}?res\??\.\s*ok", APP)))
    check("F2 checkForUpdates 有 try/finally（按钮不会被卡死）",
          bool(re.search(r"async function checkForUpdates[\s\S]{0,1200}?finally", APP)))
    check("F3 设置页保留了「检查更新」按钮", "checkForUpdates" in APP)
    check("F4 关于弹窗也提供「检查更新」入口",
          "检查更新" in (ROOT / "console" / "src" / "AboutDialog.vue").read_text(encoding="utf-8"))


def test_G_functional_path_resolution():
    """功能反证：把**真实函数体**抽到 node 里跑。

    静态判据只能证明「写法改了」，证明不了「真的能找到 .env」。
    这里用假目录复刻打包态的两个根：payload（代码根，里面没有 .env）
    + workspace（用户数据，有 .env 与 data/），并同时验证旧写法为什么失败。
    """
    print("\n[G] 功能反证：resolveExisting / pathAllowed 在「payload + workspace」双根下")
    node = shutil.which("node")
    if not node:
        check(True, "G 跳过 node 行为验证（环境无 node）")
        return

    def grab(name):
        m = re.search(r"function %s\([\s\S]*?\n\}" % name, MAIN)
        return m.group(0) if m else ""

    fn_resolve, fn_allowed = grab("resolveExisting"), grab("pathAllowed")
    check("G1 能抽取 resolveExisting / pathAllowed 函数体",
          bool(fn_resolve) and bool(fn_allowed))
    if not (fn_resolve and fn_allowed):
        return

    probe = Path(tempfile.mkdtemp(prefix="nf_ui_"))
    try:
        code_root = probe / "payload"
        ws_root = probe / "workspace"
        code_root.mkdir(parents=True)
        (ws_root / "data" / "outline").mkdir(parents=True)
        (ws_root / ".env").write_text("X_KEY=1\n", encoding="utf-8")
        (ws_root / "data" / "outline" / "global.md").write_text("# 大纲\n", encoding="utf-8")

        js = (
            'const path=require("path"), fs=require("fs");\n'
            'const ROOT=process.env.NF_CODE_ROOT, WS=process.env.NF_WS_ROOT;\n'
            'const getWorkspaceDir=()=>WS;\n'
            + fn_resolve + "\n" + fn_allowed + "\n" +
            'const env=resolveExisting(".env");\n'
            'const legacy=path.resolve(ROOT,".env");\n'
            'console.log(JSON.stringify({\n'
            '  envIsWs: env===path.join(WS,".env"),\n'
            '  envExists: fs.existsSync(env),\n'
            '  legacyExists: fs.existsSync(legacy),\n'
            '  dataAllowed: pathAllowed(path.join(WS,"data","outline","global.md")),\n'
            '  envDenied: pathAllowed(path.join(WS,".env")),\n'
            '  outsideDenied: pathAllowed(path.join(path.dirname(WS),"evil.md")),\n'
            '}));\n'
        )
        probe_js = probe / "probe.js"
        probe_js.write_text(js, encoding="utf-8")
        env = dict(os.environ, NF_CODE_ROOT=str(code_root), NF_WS_ROOT=str(ws_root))
        proc = subprocess.run([node, str(probe_js)], capture_output=True,
                              text=True, env=env, timeout=60)
        if proc.returncode != 0:
            check("G2 node 探针可执行", False, (proc.stderr or "")[:220])
            return
        r = json.loads(proc.stdout.strip().splitlines()[-1])
        check("G2 .env 解析到 workspace（而不是代码根）", r["envIsWs"], r)
        check("G3 旧写法 path.resolve(ROOT,'.env') 在打包态不存在（根因反证）",
              (not r["legacyExists"]) and r["envExists"], r)
        check("G4 workspace 下的产物路径在白名单内（旧实现会判「越界」）",
              r["dataAllowed"], r)
        check("G5 .env 本身仍不可被外部打开", not r["envDenied"], r)
        check("G6 两个根之外的路径仍被拒", not r["outsideDenied"], r)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def main():
    print("=" * 62)
    print("  GUI 暗色主题可读性 + 文件打开路径护栏（离线）")
    print("=" * 62)
    for fn in (test_A_no_hardcoded_light_background,
               test_B_dark_themes_have_control_vars,
               test_C_is_dark_override,
               test_D_contrast_wcag,
               test_E_path_resolution_and_ipc,
               test_G_functional_path_resolution):
        fn()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
