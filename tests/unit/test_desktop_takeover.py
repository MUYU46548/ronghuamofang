# -*- coding: utf-8 -*-
"""v0.6.6 修复批次的**接线级**回归（2026-10-07）。

行为层另有 node 单测（quitGuard.test.js：回执/超时/netstat 解析）与 e2e
（引导五步/自装技能源）覆盖；本文件钉的是「线有没有接上」—— 静态事实，
改坏了任何一条都是接线断裂：
  1. 8765 双绑接管：spawn 前清场 + startApi 重入守卫 + 解析纯函数引入；
  2. 退出确认回执链：preload 回执 → 主进程撤表 → 判据支持 acked；
  3. 技能随版本分发 + 自装技能源 + 版本/种子号推进。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fails = []
passed = []


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print("  [PASS] " + name)
    else:
        fails.append(name)
        print("  [FAIL] " + name + ("  -> " + str(extra) if extra else ""))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


main = read("console/main/index.js")
qg = read("console/main/quitGuard.js")
preload = read("console/preload/index.js")
appvue = read("console/src/App.vue")
pkg = read("console/package.json")
bridge = read("scripts/nf_mcp_stdio_bridge.py")

print("=== 1. 8765 双绑接管 ===")
check("killPortOccupants 清场函数存在", "function killPortOccupants(" in main)
check("listening 分支 spawn 前调用清场", "killPortOccupants(API_PORT);" in main)
check("startApi 有 apiProc 重入守卫",
      "function startApi(attempt) {" in main and "if (apiProc) return;" in main)
check("netstat 解析纯函数被 index.js 引入", "parseNetstatListeningPids" in main)
check("quitGuard 导出该纯函数", "parseNetstatListeningPids" in qg)

print("=== 2. 退出确认回执链 ===")
check("preload 收到 close-requested 即回执", "app:close-answered" in preload)
check("主进程处理 close-answered", 'ipcMain.on("app:close-answered"' in main)
check("回执处理器撤看门狗定时器",
      "clearTimeout(closeAskTimer)" in main.split('ipcMain.on("app:close-answered"')[1][:400])
check("看门狗判据支持 acked（框在场不超时）", "s.acked" in qg)
check("确认框文案分忙闲两态（不再误导）", "quitDlg.busy" in appvue)

print("=== 3. 技能分发 / 自装 / 版本 ===")
check("extraResources 带 payload/skills", '"to": "payload/skills"' in pkg)
check("extraResources 带 payload/examples（样例随包，出厂内容检测的比对真源）",
      '"to": "payload/examples"' in pkg)
check("版本已 bump 0.6.7", '"version": "0.6.7"' in pkg)
seed_lines = [l for l in main.splitlines() if "const SEED_VERSION" in l]
seed_ok = False
if seed_lines:
    try:
        seed_ok = int(seed_lines[0].split("=")[1].strip().rstrip(";")) >= 21
    except ValueError:
        seed_ok = False
check("SEED_VERSION >= 21（payload 脚本有实质变更）", seed_ok, seed_lines[:1])
check("syncSkills 随版本分发（函数 + 启动调用）",
      "function syncSkills(" in main and "syncSkills();" in main)
check("agent-connect 返回 skillPath", "skillPath: p.skillPath" in main)
check("自装指令用主进程 skillPath", "c.skillPath" in appvue)
check("引导含 Agent 接入第 5 步", "接外部 Agent（可选）" in appvue)
check("引导不与免责声明叠层", "showGuide && !disclaimerOpen" in appvue)
check("免责声明 ack 后跑延后引导",
      "pendingAfterDisclaimer" in appvue and "onDisclaimerAck" in appvue)

print("=== 4. 桥接根解析 ===")
check("注册表 NF_ROOT 兜底（对齐新起进程）",
      "winreg" in bridge and "NF_ROOT（注册表用户环境）" in bridge)

print()
print("test_desktop_takeover: %d passed, %d failed" % (len(passed), len(fails)))
raise SystemExit(1 if fails else 0)
