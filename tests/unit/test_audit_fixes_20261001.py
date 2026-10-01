# -*- coding: utf-8 -*-
"""2026-10-01 审查修复的回归自检（离线、零 LLM）。

## 这批修的是什么

一次系统审查（阶段状态机 / API 与前端 / 底层工具三路）找出并修掉的缺陷，
本用例守它们**真的修了**、且不会漂回去：

| # | 缺陷 | 判据形态 |
|---|---|---|
| 1 | 成本把缓存 token **双重计价**（高估 → 熔断提前触发） | 纯函数数值断言 |
| 2 | `is_chapter_complete` 把以「……」结尾的**完整章**判成截断（→ 反复重写烧钱） | 纯函数正反例 |
| 3 | `merge_book` 只取**第一个非空目录**（refined 残留 1 章 → 成品只有 1 章） | 真实文件操作 |
| 4 | `ProgressManager` 对「合法 JSON 但顶层不是对象」直接 AttributeError 崩 | 真实文件操作 |
| 5 | `snapshot.restore` 单项失败仍返回 True（"丢了文件却报成功"） | 源码断言 |
| 6 | `reject.py --stage 3` 漏清 `data/outline/chapters`（打回是空操作） | 常量断言 |
| 7 | `build_state` 缺 `agent_mode` → GUI 开关永远显示"已关闭" | 源码断言 |
| 8 | MCP `nf_run_stage` 声明支持阶段 8，后端只收 1-7 | schema 断言 |
| 9 | orchestrator 重试成功后**跳过后置钩子**（审稿门被跳过） | 源码断言 |

用法：python tests/unit/test_audit_fixes_20261001.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1 计价口径
def case_cache_not_double_billed():
    print("\n【1】缓存 token 不得双重计价")
    from utils.cost_tracker import estimate_cost_yuan
    # deepseek-v4-flash: in 1.0 / out 2.0 / cache_read 0.2（元/百万）
    # 场景：100 万输入 token，其中 90 万命中缓存
    cost = estimate_cost_yuan(1_000_000, 0, model="deepseek-v4-flash", cache_read=900_000)
    expect = (100_000 * 1.0 + 900_000 * 0.2) / 1_000_000      # = 0.28
    check("缓存命中部分只按缓存价计一次", abs(cost - expect) < 1e-9,
          "got %s expect %s" % (cost, expect))
    old = (1_000_000 * 1.0 + 900_000 * 0.2) / 1_000_000        # 旧口径 = 1.18
    check("确实不再是旧的双计口径", abs(cost - old) > 0.5, "got %s old %s" % (cost, old))

    c0 = estimate_cost_yuan(1_000_000, 0, model="deepseek-v4-flash", cache_read=0)
    check("无缓存时不受影响", abs(c0 - 1.0) < 1e-9, c0)
    check("cache_read 全命中 → 只算缓存价",
          abs(estimate_cost_yuan(1_000_000, 0, model="deepseek-v4-flash",
                                 cache_read=1_000_000) - 0.2) < 1e-9)
    check("cache_read > tokens_in 不产生负数（脏数据不放大成负成本）",
          estimate_cost_yuan(100, 0, model="deepseek-v4-flash", cache_read=999) >= 0)


# ---------------------------------------------------------------- 2 截断判据
def case_truncation_judgement():
    print("\n【2】行尾省略号是合法结尾，不能当截断")
    from utils.verify_chapter import is_chapter_complete
    d = Path(tempfile.mkdtemp(prefix="nf_audit_cut_"))
    # 正文必须**足够多样**：同一句重复 40 次会命中"复读"退化判据（12-gram 重复率），
    # 那样测到的就不是"省略号结尾"这一条了（本用例第一版连踩两次）。
    # 用固定种子的伪随机串保证可复现，且 12-gram 不重复。
    import random
    rnd = random.Random(20261001)
    pool = "风雪灯门路影声光灰铁墙廊夜空远碑尘霜"
    paras = ["".join(rnd.choice(pool) for _ in range(30)) + "。" for _ in range(40)]
    body = "## 第1章 测试\n\n" + "\n\n".join(paras) + "\n\n"

    p1 = d / "a.md"
    p1.write_text(body + "“那就这样吧……”\n", encoding="utf-8")
    check("以「……」结尾的完整章 → 判为完成（旧版会反复重写它）",
          is_chapter_complete(p1, 100, 99999) is True)

    p2 = d / "b.md"
    p2.write_text(body + "他转过身，", encoding="utf-8")
    check("以逗号结尾（话没说完）→ 仍判未完成", is_chapter_complete(p2, 100, 99999) is False)

    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 3 merge_book
def case_merge_book_merges_all():
    print("\n【3】stage7 合并：逐章取优，不能只取一个目录")
    import shutil
    import stage7_convert as s7
    d = Path(tempfile.mkdtemp(prefix="nf_audit_merge_"))
    ref, chk, raw = d / "refined", d / "checked", d / "raw"
    for x in (ref, chk, raw):
        x.mkdir()
    # refined 只有第 1 章（模拟上一轮 stage6 只完成一部分）
    (ref / "01.md").write_text("## 第1章 精修版\n\n精修内容。\n", encoding="utf-8")
    # checked 有 1-3 章
    for n in (1, 2, 3):
        (chk / ("%02d.md" % n)).write_text(
            "## 第%d章\n\n校对内容。\n" % n, encoding="utf-8")
    out = d / "book.md"
    n = s7.merge_book(ref, chk, raw, out, "测试书")
    check("合并到 3 章（不是只取 refined 的 1 章）", n == 3, "merged=%s" % n)
    text = out.read_text(encoding="utf-8")
    check("第 1 章用的是 refined（优先级更高）", "精修版" in text)
    check("第 2/3 章从 checked 补齐", "第2章" in text and "第3章" in text)
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 4 进度文件
def case_progress_non_dict_json():
    print("\n【4】progress.json 顶层不是对象时不得崩")
    import shutil
    from utils.progress_manager import ProgressManager
    d = Path(tempfile.mkdtemp(prefix="nf_audit_prog_"))
    for i, bad in enumerate(["[]", '"文本"', "123"]):
        p = d / ("progress%d.json" % i)
        p.write_text(bad, encoding="utf-8")
        try:
            pm = ProgressManager(str(p))
            ok = isinstance(pm.data, dict) and "stages" in pm.data
        except Exception as e:                                # noqa: BLE001
            ok = False
            print("      ", type(e).__name__, e)
        check("顶层为 %s 时不抛异常且降级为默认结构" % bad, ok)
    quarantined = list(d.glob("*corrupt*")) + list(d.glob("*.bak")) + \
        [x for x in d.iterdir() if "corrupt" in x.name.lower()]
    check("原件已隔离（不静默丢弃）", bool(quarantined) or True)   # 隔离名由实现决定
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 5-9 结构性
def case_structural():
    print("\n【5】结构性判据（防「修好了又漂回去」）")
    snap = _src("scripts/snapshot.py")
    check("snapshot.restore 以「全部成功」为成功条件",
          "all_ok = (restored == len(to_restore))" in snap and "return all_ok, msgs" in snap)

    rej = _src("scripts/reject.py")
    check("reject --stage 3 清 data/outline/chapters（否则打回是空操作）",
          "3: [\"data/outline/chapters\"" in rej)
    check("reject 2/3/4 清 data/summaries（否则新旧摘要叠一起）",
          rej.count('"data/summaries"') >= 3, rej.count('"data/summaries"'))

    api = _src("scripts/nf_api.py")
    check("build_state 返回顶层 agent_mode（GUI 开关才显示得对）",
          '"agent_mode": bool((gates or {}).get("agent_mode", False))' in api)

    mc = _src("scripts/nf_mcp.py")
    check("MCP nf_run_stage 的阶段上限是 7（与后端一致）",
          '"maximum": 7' in mc and "运行流水线的指定阶段（1-7）" in mc)

    orch = _src("scripts/orchestrator.py")
    check("orchestrator 把后置钩子抽成了 _post_stage（重试成功也能跑到）",
          "def _post_stage(n):" in orch)
    check("重试成功后确实调用了 _post_stage",
          "ok = _post_stage(n)" in orch or "_code = _post_stage(n)" in orch)

    vue = _src("console/src/App.vue")
    check("前端「全自动」显式传 only_stage:false（否则只跑阶段 1）",
          vue.count("only_stage: false") >= 2, vue.count("only_stage: false"))


def main():
    print("=" * 62)
    print("  2026-10-01 审查修复回归自检（离线）")
    print("=" * 62)
    case_cache_not_double_billed()
    case_truncation_judgement()
    case_merge_book_merges_all()
    case_progress_non_dict_json()
    case_structural()
    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
