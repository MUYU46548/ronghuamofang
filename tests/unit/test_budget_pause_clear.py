# -*- coding: utf-8 -*-
"""“恢复运行”的收尾判据：清 budget.paused（2026-11-26）。

为什么要单独立一条：作者 2026-11-25 点了两次“恢复运行”，界面没有任何变化 ——
一部分原因是前端静默（已在前端修），另一部分是**后端根本没清暂停旗标**：
横幅一直挂着，`/state.budget.paused` 仍是 true，界面就看不出“恢复成功”。

本用例守四件事（零 LLM、零网络）：
1. paused=true → 清掉、返回 True、其余预算键一个不动；
2. 已经是 false → 返回 False（幂等，不重复写盘）；
3. progress.json 不存在 → 不抛（恢复动作不能因为盘上还没文件就崩）；
4. 改坏盘上文件（非 JSON）→ 也不抛，且**不把文件清空**（宁可不清，不许毁数据）。

用法：python tests/unit/test_budget_pause_clear.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _tmp_progress(payload=None, raw=None):
    tmp = Path(tempfile.mkdtemp(prefix="pause_"))
    (tmp / "data" / "state").mkdir(parents=True)
    pf = tmp / "data" / "state" / "progress.json"
    if raw is not None:
        pf.write_text(raw, encoding="utf-8")
    elif payload is not None:
        pf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp, pf


def main():
    print("=" * 62)
    print("  恢复运行 · 清 budget.paused")
    print("=" * 62)
    old_root = nf_api.ROOT

    print("\n【1】paused=true → 清掉并返回 True")
    tmp, pf = _tmp_progress({"project": "t", "budget": {"paused": True, "limit_yuan": 300,
                                                        "spent_yuan": 1.5}})
    try:
        nf_api.ROOT = tmp
        check("返回 True（这次真的清掉了）", nf_api.clear_budget_pause() is True)
        d = json.loads(pf.read_text(encoding="utf-8"))
        check("盘上 budget.paused = false", d.get("budget", {}).get("paused") is False, d)
        check("同段其它键不动（limit_yuan / spent_yuan 原样）",
              d["budget"].get("limit_yuan") == 300 and d["budget"].get("spent_yuan") == 1.5,
              d["budget"])
        check("project 等顶层键不动", d.get("project") == "t", d.get("project"))

        print("\n【2】已经是 false → 幂等（返回 False，不报错）")
        check("第二次返回 False", nf_api.clear_budget_pause() is False)
    finally:
        nf_api.ROOT = old_root
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n【3】progress.json 不存在 → 不抛")
    tmp, pf = _tmp_progress(None)
    try:
        nf_api.ROOT = tmp
        ok = True
        try:
            got = nf_api.clear_budget_pause()
        except Exception as e:                                  # noqa: BLE001
            ok, got = False, e
        check("不抛异常（恢复动作不能因为没文件就崩）", ok, got)
        check("返回 False（没有可清的东西）", got is False, got)
    finally:
        nf_api.ROOT = old_root
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n【4】盘上文件坏了（非 JSON）→ 不抛，也不把原文件清空")
    bad = "{ 这不是 JSON"
    tmp, pf = _tmp_progress(None, raw=bad)
    try:
        nf_api.ROOT = tmp
        ok = True
        try:
            got = nf_api.clear_budget_pause()
        except Exception as e:                                  # noqa: BLE001
            ok, got = False, e
        check("不抛异常", ok, got)
        # ProgressManager 会把坏文件隔离成 .corrupt-* 再降级；关键在于**原文没丢**
        quarantined = list((tmp / "data" / "state").glob("progress.json.corrupt-*"))
        keep = pf.read_text(encoding="utf-8") if pf.exists() else ""
        check("原始内容仍可取回（隔离副本或原文件其一）",
              bool(quarantined) or keep.startswith("{ 这不是"), (quarantined, keep[:40]))
    finally:
        nf_api.ROOT = old_root
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
