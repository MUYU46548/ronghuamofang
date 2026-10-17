# -*- coding: utf-8 -*-
"""Agent 模式运行锁的真机视觉验收（Playwright）——2026-10-17 用户拍板项。

用户口径：「Agent 模式下常规运行的按钮应该锁死防止误操作」。锁的是**运行类**入口
（下一步 / 全自动 / 流式全自动 / Word 成品 / 运行所选工序 / 阶段行运行 / 失败重试 /
Space 快捷键 / 命令面板），审批 / 打回 / 停止不受影响（那几件事只归桌面端）。

本用例证闭环（按钮"看着禁用"不够，函数层也必须锁）：
1. agent_mode=true 时上述按钮逐个真的 `disabled`，agent 栏写明「已锁定（防双跑）」；
2. **按钮之外的路径也锁得住**：Space 快捷键直接调函数 → 吃到锁定 toast（双保险）；
3. 锁不影响审批族：存在待审批门时「执行下一步」（approve 类）保持可用；
4. 「复制任务」简报通道改道：HTTP 提为首选、CLI 行带 runtime_python、无裸 python、
   无成对花括号（外部 Agent 的停下判据）；
5. 产品自带开关真能解锁：点 agent 栏「关闭」→ 按钮恢复可用（锁不是永久的）；
6. `/state` 形态自报：form ∈ {source, installed}、runtime_python 指向真实解释器。

## 自带夹具与服务
临时项目根 + 动态空闲端口自起静态服务与 nf_api（--root 指向夹具），跑完自清；
不碰用户在用的 8765。缺 playwright / renderer 未构建时干净跳过（SKIP + 0 退出）。
用法：python tests/e2e/e2e_agent_lock.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

# 见 e2e_new_features 同款注释：用 reconfigure 而不是重包一层 stdout（GC 会关共享 buffer）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "ux"

SITE = ""
API = ""
TOKEN = ""

PASS, FAIL = [], []
SPAWNED = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def _port_open(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _spawn(args, env=None):
    p = subprocess.Popen(args, cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env)
    SPAWNED.append(p)
    return p


def _wait_port(proc, port, timeout=25):
    for _ in range(int(timeout * 4)):
        if proc.poll() is not None:
            return False
        if _port_open(port):
            return True
        time.sleep(0.25)
    return False


def _free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _set_agent_mode(system_yaml: Path, on: bool):
    """按行定向改写 gates.agent_mode（保留注释）。验收环境没有 GUI 确认流，
    直接写配置是夹具的标准做法（tests/http 的 _force_agent_mode_off 同源）。"""
    p = Path(system_yaml)
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines:
        if ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: %s   # 验收环境定向设置"
                       % ("true" if on else "false"))
        else:
            out.append(ln)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def make_fixture():
    """全 pending（无审批门）→ 下一步 = 运行阶段 1（run 类，应被锁）。"""
    root = Path(tempfile.mkdtemp(prefix="nf_lock_fixture_"))
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    (root / "materials" / "raw" / "甲_概念卡.md").write_text(
        "---\ntype: concept\nname: 甲\n---\n\n验收用占位素材卡。\n", encoding="utf-8")
    # has_progress=true（文件存在）→ 不出冷启动引导卡，聚焦运行锁本身
    (root / "data" / "state" / "progress.json").write_text(
        json.dumps({"stages": {}}, ensure_ascii=False), encoding="utf-8")
    sys_yaml = ROOT / "config" / "system.yaml"
    if sys_yaml.exists():
        shutil.copy(sys_yaml, root / "config" / "system.yaml")
        _set_agent_mode(root / "config" / "system.yaml", True)   # 本用例就是要开锁测
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 锁验书\n  type: 奇幻\n  chapters: 3\n", encoding="utf-8")
    return root


def ensure_services(fixture):
    global SITE, API, TOKEN
    dist = ROOT / "console" / "renderer" / "dist"
    if not (dist / "index.html").exists():
        return "", "", "renderer/dist 未构建（先跑 npm run build）"

    site_port = _free_port()
    site_proc = _spawn([sys.executable, "-m", "http.server", str(site_port),
                        "--bind", "127.0.0.1", "--directory", str(dist)])
    if not _wait_port(site_proc, site_port):
        return "", "", "静态服务（%d）起不来" % site_port
    site = "http://127.0.0.1:%d" % site_port

    api_port = _free_port()
    api = "http://127.0.0.1:%d" % api_port
    TOKEN = uuid.uuid4().hex
    env = dict(os.environ)
    env["NF_GUI_TOKEN"] = TOKEN
    proc = _spawn([sys.executable, str(ROOT / "scripts" / "nf_api.py"),
                   "--port", str(api_port), "--root", str(fixture)], env=env)
    if not _wait_port(proc, api_port):
        return "", "", "后端（%d）起不来" % api_port

    last = ""
    for _ in range(40):
        if proc.poll() is not None:
            return "", "", "后端进程已退出（code=%s）" % proc.returncode
        try:
            raw = urllib.request.urlopen(api + "/state", timeout=3).read().decode("utf-8")
        except Exception:                                  # noqa: BLE001
            time.sleep(0.3)
            continue
        try:
            pd = str(json.loads(raw).get("project_dir") or "")
        except Exception:                                  # noqa: BLE001
            pd = ""
        last = pd or raw
        if pd and Path(pd).resolve() == Path(fixture).resolve():
            SITE, API = site, api
            return site, api, ""
        time.sleep(0.3)
    return "", "", "后端 project_dir 不是本夹具: %s" % last[:200]


def _get_state(api):
    raw = urllib.request.urlopen(api + "/state", timeout=5).read().decode("utf-8")
    return json.loads(raw)


def main():
    try:
        from playwright.sync_api import sync_playwright            # noqa: F401
    except ImportError:
        print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收，不是离线用例。")
        return 0

    sys.path.insert(0, str(ROOT / "tests" / "e2e"))
    fixture = make_fixture()
    site, api, err = ensure_services(fixture)
    if err:
        print("[SKIP] %s" % err)
        shutil.rmtree(str(fixture), ignore_errors=True)
        return 0

    import e2e_ux_verify as ux        # 导入时会重包 sys.stdout（别再叠一层）
    ux.SITE = site
    ux.GUI_TOKEN = TOKEN

    ws = Path(fixture)
    st0 = _get_state(api)
    print("=" * 62)
    print("  Agent 模式运行锁验收")
    print("  site = %s   api = %s" % (site, api))
    print("  fixture =", ws)
    print("=" * 62)

    # ---------- /state 形态自报（F3，先验后进页面） ----------
    check("/state 自报 form 合法", st0.get("form") in ("source", "installed"), st0.get("form"))
    rp = st0.get("runtime_python") or ""
    check("/state 自报 runtime_python 且指向真实解释器",
          bool(rp) and __import__("pathlib").Path(rp).exists(), rp)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx, page, errs, _bad = ux.new_page(browser, api, "lock")
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=site)
            # toggle 的二次确认走 window.confirm（浏览器上下文里真弹）→ 全部接受
            page.on("dialog", lambda d: d.accept())

            # ---------- agent 栏状态 ----------
            page.wait_for_timeout(800)
            check("⚡ Agent 徽标在场", page.locator(".agent-mode-badge").count() == 1)
            bar = page.locator(".agent-bar")
            check("agent 栏写明运行已锁定（不是哑锁）",
                  bar.count() == 1 and "常规运行按钮已锁定" in bar.inner_text(),
                  bar.inner_text()[:120] if bar.count() else "无 agent 栏")

            # ---------- 流水线条：运行类按钮逐个锁死 ----------
            page.locator(".tabs button:has-text('流水线')").click()
            page.wait_for_timeout(700)
            btn_next = page.locator(".flow-next button:text-is('⏎ 执行下一步')")
            btn_all = page.locator(".flow-next button:text-is('全自动')")
            btn_stream = page.locator(".flow-next button:text-is('流式全自动')")
            btn_word = page.locator(".flow-next button:text-is('📄 Word 成品')")
            btn_quick = page.locator(".flow-quick button:has-text('运行所选工序')")
            check("「执行下一步」禁用（下一步=运行类）", btn_next.is_disabled())
            check("「全自动」禁用", btn_all.is_disabled())
            check("「流式全自动」禁用", btn_stream.is_disabled())
            check("「Word 成品」禁用", btn_word.is_disabled())
            check("「运行所选工序」禁用", btn_quick.is_disabled())
            check("锁定按钮带解释性 tooltip（不是无名禁用）",
                  "Agent 模式" in (btn_all.get_attribute("title") or ""))
            ux.shot(page, "LOCK1_flowbar_locked")

            # 阶段行「运行」也锁
            row_run = page.locator(".stage-actions button:text-is('运行')")
            check("阶段行「运行」按钮在场", row_run.count() >= 1, row_run.count())
            check("阶段行「运行」全部禁用",
                  all(row_run.nth(i).is_disabled() for i in range(row_run.count())))

            # ---------- 按钮之外的路径：Space 快捷键（函数级双保险） ----------
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_timeout(500)
            toast = page.locator(".toast")
            check("Space 快捷键也被锁（函数级守卫生效）",
                  toast.count() >= 1 and "Agent 模式" in toast.first.inner_text(),
                  toast.first.inner_text()[:120] if toast.count() else "无 toast")

            # ---------- 复制任务简报：通道改道（F2） ----------
            copy_btn = page.locator(".stage-actions button:has-text('复制任务')")
            check("「复制任务」按钮在场（agent 模式专属）", copy_btn.count() >= 1, copy_btn.count())
            copy_btn.first.click()
            page.wait_for_timeout(600)
            brief = page.evaluate("navigator.clipboard.readText()")
            check("简报 HTTP 提为首选通道", "HTTP（推荐" in brief, brief[:150])
            check("简报 CLI 行带 runtime_python（不再裸 python）",
                  ("CLI：" + rp) in brief, brief[brief.find("CLI："):brief.find("CLI：") + 120]
                  if "CLI：" in brief else "无 CLI 行")
            check("简报声明执行形态", "执行形态" in brief)
            check("简报无成对花括号（外部 Agent 的停下判据）",
                  "{{" not in brief and "}}" not in brief)
            ux.shot(page, "LOCK2_task_brief")

            # ---------- 审批族不受影响：先造一个审批门 ----------
            (ws / "data" / "state" / "progress.json").write_text(
                json.dumps({"stages": {"1": {"status": "done", "approved": False}}},
                           ensure_ascii=False), encoding="utf-8")
            page.locator("button[title='全局刷新（所有页签数据）']").click()
            page.wait_for_timeout(900)
            btn_next2 = page.locator(".flow-next button:text-is('⏎ 执行下一步')")
            check("审批门出现后「执行下一步」恢复可用（approve 类不锁）",
                  not btn_next2.is_disabled())
            # 运行类仍然锁（对照：锁的是行为，不是全局禁用）
            check("同时运行类按钮仍锁", page.locator(
                ".flow-next button:text-is('全自动')").is_disabled())
            ux.shot(page, "LOCK3_approve_not_locked")

            # ---------- 产品自带开关真能解锁（F1 收尾） ----------
            page.locator(".agent-bar button:has-text('关闭')").click()
            page.wait_for_timeout(1200)
            check("关掉 Agent 模式后 ⚡ 徽标消失", page.locator(".agent-mode-badge").count() == 0)
            check("解锁后「全自动」恢复可用",
                  not page.locator(".flow-next button:text-is('全自动')").is_disabled())
            check("解锁后「执行下一步」恢复可用",
                  not page.locator(".flow-next button:text-is('⏎ 执行下一步')").is_disabled())
            ux.shot(page, "LOCK4_unlocked")

            real_errs = [e for e in errs if e.startswith("error:")]
            check("无 console error", not real_errs, real_errs[:3])
        finally:
            ctx.close()
            browser.close()

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("  截图: %s" % OUT)
    print("=" * 62)
    shutil.rmtree(str(ws), ignore_errors=True)
    return 1 if FAIL else 0


def cleanup():
    """只收本脚本自己拉起的进程（动态端口，不碰用户在用的 8765）。"""
    for p in SPAWNED:
        try:
            p.wait(timeout=5)
        except Exception:                                    # noqa: BLE001
            pass
        try:
            p.terminate()
        except Exception:                                    # noqa: BLE001
            pass
    for p in SPAWNED:
        if p.poll() is None and os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"],
                           capture_output=True, timeout=10)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        cleanup()
