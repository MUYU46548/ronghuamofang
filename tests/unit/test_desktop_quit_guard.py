# -*- coding: utf-8 -*-
"""桌面端「关不掉」回归自检（P0，2026-10-03）。零 LLM、零网络。

## 事故与根因（实测取证）

用户报告「桌面端应用又关不掉了，原因不明」。进程树取证发现：
`绒花墨坊.exe`(9012) + 3 个 Electron 子进程 + 1 个它 spawn 的 `nf_api.py`(13616)，
而 nf_api 自己**还孵着一个孙进程**（PID 2732）。根因是两处叠加：

1. **渲染进程的 `window.beforeunload` 原生确认框**（App.vue `setupExitGuard`），
   触发条件包含「有 stage 是 done 但未审批」—— 那是审批门的**长期正常状态**
   （可持续数天）→ 每次关窗都弹 Chromium 原生框；该框在 Windows 上会跑到窗口后面、
   或被 Esc/取消吃掉 → 表现就是「点了 X 没反应、关不掉」，并连带卡住「重启安装」。
   主进程侧**完全没有** `mainWindow.on("close")`，既没兜底也没看门狗。
2. `stopApi()` 只 `apiProc.kill()`：**杀子不杀孙** → 残留进程继续占 8765 →
   下次启动走「端口被占 → 杀进程 → 重试」那条路，而那条路是**无限递归**，
   端口清不掉时应用看起来就是「起不来/半死不活」。

这与项目自己的 GUI 纪律冲突（「破坏性操作走应用内确认框，不用 window.confirm
（Electron 原生对话框不可靠）」）—— `beforeunload` 正是同类且更糟。

## 本用例守什么

判据分两层：**纯逻辑行为**（node 单测，见 console/tests/quitGuard.test.js）+
**结构形态**（源码断言，防回流）。结构断言是刻意的：
「关不掉」没有一个能在 CI 里手动关一百次窗口的验法，只能钉住形态。

用法：python tests/unit/test_desktop_quit_guard.py
"""
import io
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  [SKIP] %s  → %s" % (name, why))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ============================================================ 1. 纯逻辑（node）
def case_node_unit():
    print("\n【1】退出守卫纯逻辑（node 单测：判据真的按预期放行/拦截）")
    node = shutil.which("node")
    if not node:
        skip("node 单测", "本机没装 node（发版前请在装了 node 的环境跑一次）")
        return
    p = subprocess.run([node, str(ROOT / "console" / "tests" / "quitGuard.test.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    check("node 单测全过（17 断言）", p.returncode == 0, out[-300:])


# ============================================================ 2. 渲染进程
def case_renderer():
    print("\n【2】渲染进程：不许再用 beforeunload 原生框，改为上报 busy + 应用内确认")
    vue = read("console/src/App.vue")
    check("**已无** window.beforeunload 退出守卫（本次事故的直接原因）",
          'addEventListener("beforeunload"' not in vue
          and "addEventListener('beforeunload'" not in vue)
    check("不再设置 returnValue（原生框的触发点）", "returnValue" not in vue)
    check("上报 busy 给主进程", "setQuitBusy" in vue)
    check("监听主进程的关窗询问", "onCloseRequested" in vue)
    check("有应用内确认对话框状态", "quitDlg" in vue and "退出绒花墨坊？" in vue)
    check("确认路径调 confirmQuit", "mofangAPI" in vue and "confirmQuit" in vue)
    check("取消路径调 cancelQuit（让下次关窗还能再问）", "cancelQuit" in vue)
    check("对话框带勾选护栏（与「跳过阶段」同一套交互）",
          'v-model="quitDlg.agreed"' in vue and ':disabled="!quitDlg.agreed"' in vue)
    check("busy 判据仍然包含「done 但未审批」（审批门是长期状态，才必须问一次）",
          's.status === "done" && !s.approved' in vue)


# ============================================================ 3. 主进程
def case_main():
    print("\n【3】主进程：关窗由主进程判 + 看门狗 + 全树杀子 + 端口探测限次")
    main = read("console/main/index.js")
    check("注册了窗口 close 处理器（旧实现完全没有）",
          'mainWindow.on("close"' in main)
    check("close 里用纯判据 shouldGuardClose（可单测）",
          "shouldGuardClose({" in main)
    check("拦下后去问渲染进程", "askRendererToClose" in main)
    check("**有看门狗**：渲染进程不应答也要能退",
          "closeAskTimedOut(" in main and "forceQuit(" in main)
    check("强制退出走 app.exit（不依赖任何清理成功）",
          "app.exit(0)" in main and "process.exit(0)" in main)
    check("busy 由渲染进程上报", 'ipcMain.on("app:busy"' in main)
    check("确认通道 isQuitting=true 后放行（否则递归弹框）",
          'ipcMain.handle("app:quit-confirmed"' in main and "isQuitting = true" in main)
    check("取消通道存在", 'ipcMain.handle("app:quit-canceled"' in main)
    check("**杀整棵树**（旧实现 apiProc.kill() 杀子不杀孙）",
          "killTree(pid)" in main and "taskkill" not in main.split("function killTree")[0])
    check("startApi 的端口占用路径也用整树杀", "killTree(parseInt(pid))" in main)
    check("端口探测**限次**（旧实现是无限递归）",
          "shouldRetryPortProbe(" in main and "MAX_PORT_PROBE_RETRIES" in main)
    check("before-quit 有硬退出看门狗", "before-quit-timeout" in main)
    check("装更新时不抢退出（否则「重启安装」会失效）",
          "updateInstalling" in main and "if (updateInstalling) return;" in main)
    check("纯判据住在独立模块（不 require electron，故可 node 直测）",
          (ROOT / "console/main/quitGuard.js").exists()
          and "require(\"electron\")" not in read("console/main/quitGuard.js"))


# ============================================================ 4. IPC 交叉核对
def case_ipc_wiring():
    print("\n【4】preload ↔ main 的通道必须成对（少一个就是静默失效）")
    pre = read("console/preload/index.js")
    main = read("console/main/index.js")
    pairs = [
        ("setQuitBusy", 'ipcRenderer.send("app:busy"', 'ipcMain.on("app:busy"'),
        ("onCloseRequested", 'ipcRenderer.on("app:close-requested"',
         'webContents.send("app:close-requested")'),
        ("confirmQuit", 'ipcRenderer.invoke("app:quit-confirmed"',
         'ipcMain.handle("app:quit-confirmed"'),
        ("cancelQuit", 'ipcRenderer.invoke("app:quit-canceled"',
         'ipcMain.handle("app:quit-canceled"'),
    ]
    for name, pre_needle, main_needle in pairs:
        check("%s：preload 有 %s 且 main 有对应端" % (name, name.split(":")[0]),
              pre_needle in pre and main_needle in main,
              "preload=%s main=%s" % (pre_needle in pre, main_needle in main))
    check("onCloseRequested 返回退订函数（组件卸载不泄漏监听）",
          "removeListener(\"app:close-requested\"" in pre)


# ============================================================ 5. 反证
def case_reflection():
    print("\n【5】反证：把守卫改回旧形态，本用例必须变红")
    vue = read("console/src/App.vue")
    # 模拟旧实现（只做字符串层面的反证，不改真文件）
    old_vue = vue.replace("const quitDlg = ref({ open: false, agreed: false });",
                          'window.addEventListener("beforeunload", (e) => {\n'
                          '  e.returnValue = "确认退出？";\n'
                          '});\nconst quitDlg = ref({ open: false, agreed: false });')
    check("旧形态（beforeunload + returnValue）会被【2】的断言抓住",
          'addEventListener("beforeunload"' in old_vue and "returnValue" in old_vue)
    main = read("console/main/index.js")
    old_main = main.replace("killTree(pid)", "apiProc.kill()")
    check("退回「只杀子进程」会被【3】的断言抓住",
          "killTree(pid)" not in old_main)


def main():
    print("=" * 62)
    print("  桌面端「关不掉」回归自检（P0）")
    print("=" * 62)
    case_node_unit()
    case_renderer()
    case_main()
    case_ipc_wiring()
    case_reflection()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d / 跳过 %d" % (len(PASS), len(FAIL), len(SKIP)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
