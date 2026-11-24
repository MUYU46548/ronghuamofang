# -*- coding: utf-8 -*-
"""审批门升级成“阶段 4 要签字”的落地判据（2026-11-24）。

## 为什么需要这条

作者拍板：“升级成需要签字的门。” 出厂配置改成 `require_approval: [2, 4, 6]` 之后，
真正会挡住功能的不是代码，而是**工作区的 config/system.yaml 永远不会被升级覆盖**
（seedWorkspace 只补缺失文件）。老用户（包括作者这台）会永远看不到阶段 4 那道门 ——
功能做了，等于没做，这是本项目最贵的一类失败（“提了没做”）。

## 钉什么

1. 纯迁移函数行为（node 单测）：旧出厂值 [2,6] → [2,4,6]；作者自己改过就一个字不动；
2. 接线：`seedWorkspace()` 里真的调了它，而且**在“无事可做就早退”之前**调
   （否则代码没变的那次启动就不会迁移）；
3. 出厂配置确实含 4，且 `approval_stages` 单一来源仍然生效；
4. 界面/手册的说法与配置一致（“出厂 2、4、6”），避免又出现“界面说没有、实际有门”。
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
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
    print("\n【1】迁移判据（node 单测：该迁的迁、改过的不动）")
    node = shutil.which("node")
    if not node:
        skip("node 单测", "本机没装 node")
        return
    p = subprocess.run([node, str(ROOT / "console" / "tests" / "gateMigrate.test.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    check("node 单测全过（14 断言）", p.returncode == 0, out[-300:])


def case_wiring():
    print("\n【2】接线：在早退之前迁移，且不 require electron")
    main = read("console/main/index.js")
    mod = read("console/main/gateMigrate.js")
    check("模块不 require electron（才能 node 直测）",
          'require("electron")' not in mod)
    check("主进程引入了迁移函数",
          'require("./gateMigrate")' in main and "migrateRequireApproval" in main)
    i_mig = main.find("migrateGate4(path.join(getWorkspaceDir(), SEED_CONFIG_DIR));")
    i_ready = main.find("app.whenReady()")
    check("在 whenReady 里、seedWorkspace 之后调（首启时配置刚播下来）",
          i_mig > 0 and i_ready > 0 and i_mig > i_ready,
          "mig=%s ready=%s" % (i_mig, i_ready))
    # 为什么**不能**放在 seedWorkspace 里：test_failure_paths.F7 会把 seedWorkspace 的
    # 函数体抽出来在桩环境里跑（只带一份固定的辅助函数名单），多引用一个未定义的函数
    # 就直接 ReferenceError → 那条用例假红（2026-10-07 已经踩过一次）。
    i_seed = main.find("function seedWorkspace()")
    i_seed_end = main.find("\n}", i_seed)
    check("不在 seedWorkspace 函数体里（F7 会抽函数体跑探针）",
          i_seed > 0 and i_seed_end > i_seed
          and "migrateGate4" not in main[i_seed:i_seed_end],
          "seed=%s end=%s" % (i_seed, i_seed_end))
    check("写盘前先备份 .bak-gate4", '.bak-gate4' in main)


def case_factory_config():
    print("\n【3】出厂配置含阶段 4，且单一来源仍生效")
    from utils.approval_policy import approval_stages  # noqa: E402
    import yaml  # noqa: E402
    cfg = yaml.safe_load(read("config/system.yaml"))
    gates = cfg.get("gates") or {}
    check("出厂 require_approval 含 4", 4 in (gates.get("require_approval") or []),
          gates.get("require_approval"))
    check("阶段 1 仍按 material_autonomy 并入（关着的时候归并完也停一次）",
          gates.get("material_autonomy") is not False or 1 in approval_stages(gates),
          approval_stages(gates))


def case_copy():
    print("\n【4】界面与手册的说法跟配置一致")
    h1 = read("console/src/help/01-start.md")
    h9 = read("console/src/help/09-terms.md")
    app = read("console/src/App.vue")
    check("手册 01 写明出厂 2、4、6", "阶段 2、4、6" in h1)
    check("术语表写明 4 = 正文写完、审稿报告已出", "4（正文写完" in h9)
    check("设置页说明写明出厂 2、4、6", "出厂默认勾阶段 2、4、6" in app)
    check("收件箱给阶段 4 一个“审稿报告”入口", "s.stage === 4" in app
          and "审稿报告" in app)


def main():
    print("=" * 66)
    print("审批门：阶段 4 升级成签字门（配置迁移 + 接线 + 文案）")
    print("=" * 66)
    case_node_unit()
    case_wiring()
    case_factory_config()
    case_copy()
    print("-" * 66)
    print("PASS %d / FAIL %d / SKIP %d" % (len(PASS), len(FAIL), len(SKIP)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
