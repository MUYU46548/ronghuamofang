# -*- coding: utf-8 -*-
"""提示词升级判据：出厂副本当参照，用户手改的一个字不动（2026-11-28）。

## 为什么需要这条

0.9.15 安装态实测：工作区里 `stage4_expand` / `stage4_group` / `stage4_refine` /
`stage6_polish` 四份提示词停在 11-24 的旧版。把它们逐字节丢进 git 历史比对，
都能匹配到旧提交 —— **不是用户改的，是升级没刷**。

根因不在"要不要覆盖"这条产品决策（`SEED_KEEP_USER_EDITS = ["prompts"]` 是对的），
而在**判据恒为假**：`factory_manifest.json` 只记 `prompts/factory/*`（18 条），
顶层 `prompts/*.md` 一条都没记（播种时 factory/ 走 force 刷新分支，不进 seededRels），
于是 `recorded` 恒 undefined → 所有顶层提示词永远被判成"用户改过"。

## 钉什么

1. 纯判据（node 单测）：三条路径都对，边界都不误伤；
2. 接线：`copyMissingOnly` 真的用了它，且真去读出厂副本；
3. **反证**：把第二路参照摘掉 → 用例必须变红（防“判据写了但没人跑”）。
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
                           ("  -> " + str(detail)[:240]) if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  [SKIP] %s  -> %s" % (name, why))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def case_node_unit():
    print("\n【1】判据本身（node 单测：该刷的刷、改过的不动）")
    node = shutil.which("node")
    if not node:
        skip("node 单测", "本机没装 node")
        return
    p = subprocess.run([node, str(ROOT / "console" / "tests" / "promptRefresh.test.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    check("node 单测全过（15 断言）", p.returncode == 0, out[-400:])


def case_wiring():
    print("\n【2】接线：真的用判据模块，真去读出厂副本")
    main = read("console/main/index.js")
    mod = read("console/main/promptRefresh.js")
    check("判据模块不 require electron（才能 node 直测）",
          'require("electron")' not in mod)
    check("主进程引入了判据模块",
          'require("./promptRefresh")' in main and "promptRefreshMod" in main)
    check("copyMissingOnly 调了 decideRefresh",
          "decideRefresh(" in main and "verdict.follow" in main)
    check("判据模块声明了 factory-copy-match 这条理由",
          "factory-copy-match" in mod)


def case_order_matters():
    """时序判据：出厂副本的 sha 必须在 force 刷新**之前**抓（2026-11-28 实测的坑）。

    为什么这条必须专门钉：`prompts/factory/` 在播种过程中会被 force 刷成新版。
    如果参照是从盘上现读的，读到的是**新版** → 顶层提示词又会被判成"用户改过"，
    这个 bug 就换个形式复活了（实测 5 份全部误判）。
    靠注释提醒不够 —— 位置错一行就复活，必须让机器看着。
    """
    print("\n【3】时序：出厂副本 sha 在 force 刷新之前抓")
    main = read("console/main/index.js")
    i_snap = main.find("const factoryShas = snapshotFactoryShas(ws);")
    i_loop = main.find("for (const d of SEED_CODE_DIRS) {")
    check("调了 snapshotFactoryShas(ws)", i_snap > 0, i_snap)
    check("抓取位置在 SEED_CODE_DIRS 循环之前（即任何 force 刷新之前）",
          i_snap > 0 and i_loop > 0 and i_snap < i_loop,
          "snap=%s loop=%s" % (i_snap, i_loop))
    check("copyMissingOnly 收到并转发了 factoryShas",
          "copyMissingOnly(ws, src, dst, seededRels, true, prevFiles, stats, factoryShas)" in main)
    check("copyMissingOnly 内部不再现读 prompts/factory 当参照（用快照）",
          'path.join(base, "prompts", "factory"' not in main)


def case_counter_evidence():
    """反证：把第二路参照摘掉，判据必须变红 —— 否则这条用例是装饰。"""
    print("\n【3】反证：摘掉出厂副本那一路 → 用例必须红")
    node = shutil.which("node")
    if not node:
        skip("反证", "本机没装 node")
        return
    src = read("console/main/promptRefresh.js")
    broken = src.replace('factorySha && factorySha === curSha', 'false')
    if broken == src:
        check("反证前提：能在源码里找到那段判断", False, "没找到可替换的字符串")
        return
    tmp = ROOT / "Temp" / "_probe_prompt_refresh"
    tmp.mkdir(parents=True, exist_ok=True)
    probe = tmp / "promptRefresh.js"
    probe.write_text(broken, encoding="utf-8")
    # 造一个只 import 这份坏模块的用例副本
    tsrc = read("console/tests/promptRefresh.test.js").replace(
        'require("../main/promptRefresh.js")', 'require("%s")'
        % str(probe).replace("\\", "/"))
    tprobe = tmp / "promptRefresh.test.js"
    tprobe.write_text(tsrc, encoding="utf-8")
    p = subprocess.run([node, str(tprobe)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=120)
    check("摘掉出厂副本参照后单测确实变红（判据真的在拦）",
          p.returncode != 0, (p.stdout or "")[-200:])
    shutil.rmtree(tmp, ignore_errors=True)


def case_real_workspace_state():
    """记实现场：工作区那 4 份到底是不是用户改的（git 历史可比对）。

    这条不是硬判据（工作区可能不在本机的新机器上），只在能查时报出来 —— 
    它把"升级没刷"与"用户改过"两类事实分开，免得下一轮又靠猜。
    """
    print("\n【4】现场核对（只报告，不判定）")
    ws = Path.home() / "AppData" / "Roaming" / "绒花墨坊" / "workspace" / "prompts"
    if not ws.is_dir():
        skip("工作区核对", "本机没有该工作区")
        return
    names = ("stage4_expand", "stage4_group", "stage4_refine", "stage6_polish",
             "stage4_writing")
    repo_fac = ROOT / "prompts" / "factory"
    stale = []
    for n in names:
        f = ws / (n + ".md")
        if not f.exists():
            continue
        cur = f.read_bytes().replace(b"\r\n", b"\n")
        ref = (repo_fac / (n + ".md")).read_bytes().replace(b"\r\n", b"\n")
        if cur != ref:
            stale.append(n)
    print("    与当前出厂副本不同的：%s" % (", ".join(stale) if stale else "无"))
    check("现场核对完成（结论见上，不作为硬判据）", True, stale)


def main():
    print("=" * 66)
    print("提示词升级：出厂副本当参照（该刷的刷、改过的不动）")
    print("=" * 66)
    case_node_unit()
    case_wiring()
    case_order_matters()
    case_counter_evidence()
    case_real_workspace_state()
    print("-" * 66)
    print("PASS %d / FAIL %d / SKIP %d" % (len(PASS), len(FAIL), len(SKIP)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
