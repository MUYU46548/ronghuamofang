# -*- coding: utf-8 -*-
"""抢端口前"先问一句"的回归（2026-11-20）。

## 被修的是什么

桌面端启动时无条件杀掉占 8765 的实例。而流水线就在 nf_api **自己的进程里**跑
（`nf_api.py` 顶层 `from orchestrator import run`，所以 `data/state/run.lock`
里的 pid 就是 nf_api 的 pid）→ 双击图标 = 把正在写的那一章连进程一起杀掉，
界面一个错都不报，作者只会发现稿子少了一段。

## 本文件钉什么

1. 纯判据（node 单测）：占端口的进程是不是持锁者 —— 是则问、否则杀；
2. 接线：主进程两条杀进程的路（端口探测 EADDRINUSE / listening 后就清场）
   必须走**同一个**判断，否则"先问一句"只在一半路径生效；
3. 等的时候不许吃掉端口探测的 3 次配额（否则等两轮就"放弃清理"了）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  [SKIP] %s  -> %s" % (name, why))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def case_node_unit():
    print("\n【1】纯判据（node 单测：该杀的杀、该拦的拦）")
    node = shutil.which("node")
    if not node:
        skip("node 单测", "本机没装 node（发版前请在装了 node 的环境跑一次）")
        return
    p = subprocess.run([node, str(ROOT / "console" / "tests" / "portTakeover.test.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    check("node 单测全过（24 断言）", p.returncode == 0, out[-300:])


def case_module_pure():
    print("\n【2】判据住在独立纯模块（不 require electron，故可 node 直测）")
    mod = read("console/main/portTakeover.js")
    check("模块存在且导出三个判据",
          all(k in mod for k in ("module.exports",
                                 "decidePortTakeover", "takeoverDetail",
                                 "shouldKeepWaiting")))
    check("**不 require electron**（能 node 直测的前提）",
          'require("electron")' not in mod)
    check("判据的核心是 pid 身份比对，不是 owner 语义",
          "pids.includes(pid)" in mod and "owner" not in mod.split("function decidePortTakeover")[1]
          .split("function ")[0])
    check("锁文件内容不可信：说明串要清洗 + 截断",
          "replace(/\\s+/g" in mod and "slice(0, n)" in mod
          and 'clean(l.cmd, 120)' in mod)


def case_wiring():
    print("\n【3】接线：两条清场路径共用同一判断")
    main = read("console/main/index.js")
    check("主进程引入判据", 'require("./portTakeover")' in main
          and "decidePortTakeover(" in main)
    check("killPortOccupants 会先判再杀", "decidePortTakeover({ lock, portPids: pids })" in main)
    check("读运行锁（与 Python 侧同一份格式）",
          '"data", "state", "run.lock"' in main)
    # 顺序：判据在前，杀在后（写反了就是"杀完再问"，等于没问）
    body = main.split("function killPortOccupants(")[1].split("function schedulePipelineWaitRetry")[0]
    check("**先问后杀**（判据出现位置早于杀进程）",
          body.find("decidePortTakeover(") < body.find("if (!killTree(parseInt(pid)))"))
    check("选了等 → 一个都不杀，返回 waiting",
          "waiting: true" in body)
    check("对话框默认按钮是等（默认值不能是丢稿）",
          'defaultId: 0' in body and "buttons: [\"等它写完（推荐）\"" in body)
    check("对话框弹不出来时按旧行为清场（打不开软件更硬）",
          "询问对话框弹不出来" in body and "choice = 1" in body)
    # 只有一处 netstat 杀进程（旧实现是两处各杀各的）
    check("**全文件只剩一处 netstat 清场**（两条路不再各写一份）",
          main.count("netstat -ano | findstr") == 1,
          main.count("netstat -ano | findstr"))
    check("EADDRINUSE 路径也走 killPortOccupants",
          "const take = killPortOccupants(API_PORT);" in
          main.split('if (e.code === "EADDRINUSE")')[1].split("tester.once(\"listening\"")[0])
    check("listening 路径处理 waiting（不 spawn、去重试）",
          "if (take.waiting) {" in main.split('tester.once("listening"')[1][:600])
    check("等的时候不吃端口探测配额（不调 shouldRetryPortProbe）",
          "shouldRetryPortProbe" not in
          main.split("function schedulePipelineWaitRetry")[1].split("\nfunction ")[0])


def case_boundary():
    print("\n【4】边界：不许把应用永久卡在离线态")
    main = read("console/main/index.js")
    mod = read("console/main/portTakeover.js")
    check("等有上限（到点重新问人）", "PIPELINE_WAIT_MAX_TICKS" in main
          and "shouldKeepWaiting(" in main)
    check("上限判据在纯模块里（NaN/缺值都有兜底）",
          "Number.isFinite(n)" in mod)
    check("已经选过等 → 不重复弹框（只弹一次）",
          "pipelineWait) {\n      return { pids, waiting: true };" in main
          or "// 已在等 → 继续等，不重复弹框" in main)
    check("等的时候给一条系统通知（否则用户只看到界面离线）",
          "正在等上一次任务写完" in main)


for fn in (case_node_unit, case_module_pure, case_wiring, case_boundary):
    fn()

print()
print("test_port_takeover: %d passed, %d failed, %d skipped"
      % (len(PASS), len(FAIL), len(SKIP)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
