# -*- coding: utf-8 -*-
"""自由模式（活一）回归 —— 24 断言。

守三件事（问题卡活一）：
1. `--stages 3,5,7` 解析：去重升序、人话报错、与 `--from` / `--stage` **互斥不猜意图**
2. 执行前**一次报全**的缺料拦截，每条带三要素（缺什么 / 哪件产的 / 建议先跑哪件）
3. 「本次会产出」的上游不算缺 —— 勾「检查+润色+出稿」不许被自己拦死

判据全在 `utils.stage_select` / `utils.stage_artifacts`（纯函数，临时目录），
不拉 orchestrator 的整条 stage 链（那是它 import 就要付的价）。
全程离线、零 token。
用法：python tests/unit/test_stage_select.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.stage_artifacts import missing_inputs  # noqa: E402
from utils.stage_select import (mutual_exclusion_error, parse_stages,  # noqa: E402
                                preflight_lines, stage_order)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  → " + str(detail)[:200] if detail else ""))


def expect_value_error(raw):
    try:
        parse_stages(raw)
    except ValueError as e:
        return str(e)
    return None


# ---------------------------------------------------------------- 1. 解析
def test_parse():
    print("\n[1] 多选解析")
    check("1.1 基本形态 3,5,7", parse_stages("3,5,7") == [3, 5, 7])
    check("1.2 去重 + 升序", parse_stages("7,3,5,3,7") == [3, 5, 7])
    check("1.3 容忍中文/空白分隔", parse_stages("3， 5、7 ") == [3, 5, 7])
    check("1.4 None → None（没给这个参数）", parse_stages(None) is None)
    check("1.5 空串报错并给写法", "阶段号 1-8" in (expect_value_error("  ") or ""))
    e = expect_value_error("3,a")
    check("1.6 非整数 → 人话报错（含正确写法）",
          e is not None and "不是阶段号" in e and "3,5,7" in e, e)
    e = expect_value_error("9")
    check("1.7 越界 → 报出可用范围 1-8",
          e is not None and "超出范围" in e and "1-8" in e, e)
    e = expect_value_error("0")
    check("1.8 阶段 0 同样拒", e is not None and "超出范围" in e, e)


# ---------------------------------------------------------------- 2. 互斥
def test_mutex():
    print("\n[2] 与旧参数互斥（不猜意图）")
    m = mutual_exclusion_error([3, 5, 7], only_stage=4)
    check("2.1 --stages + --stage → 报错", m is not None and "二选一" in m, m)
    m = mutual_exclusion_error([3, 5, 7], from_given=True)
    check("2.2 --stages + --from → 报错", m is not None and "--from" in m, m)
    check("2.3 只给 --stages → 放行",
          mutual_exclusion_error([3, 5, 7], from_given=False, only_stage=None) is None)
    check("2.4 完全没给 --stages → 与谁都不冲突（旧用法零影响）",
          mutual_exclusion_error(None, from_given=True, only_stage=4) is None)
    check("2.5 报错文案含两个选项的正确写法",
          mutual_exclusion_error([1], from_given=True) is not None
          and "--stages 3,5,7" in mutual_exclusion_error([1], from_given=True))


# ---------------------------------------------------------------- 3. 排序
def test_order():
    print("\n[3] 执行顺序三选一")
    check("3.1 多选 → 升序集合", stage_order(stages=[7, 3, 5]) == [3, 5, 7])
    check("3.2 单跑 → 只有一件", stage_order(only_stage=6) == [6])
    check("3.3 从 N 跑到底（默认 1）",
          stage_order(from_stage=1) == [1, 2, 3, 4, 5, 6, 7, 8])
    check("3.4 --from 5 → 5..8", stage_order(from_stage=5) == [5, 6, 7, 8])
    check("3.5 多选优先于 from（两者同时给时由 CLI 先拦，这里只保证不串味）",
          stage_order(stages=[6, 7], from_stage=2) == [6, 7])


# ---------------------------------------------------------------- 4. 报缺
def _mkroot():
    return Path(tempfile.mkdtemp(prefix="nf_ss_"))


def _put(root, rel, content="x"):
    p = root / rel
    if rel.endswith("/"):
        p.mkdir(parents=True, exist_ok=True)
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_preflight():
    print("\n[4] 执行前报缺（三要素 + 一次报全）")
    root = _mkroot()
    try:
        # 全空：勾 6（润色）—— 它要 checked，由阶段5 产
        lines = preflight_lines([6], root, will_produce=[6])
        check("4.1 缺料被报出", len(lines) == 1, lines)
        check("4.2 三要素：缺什么", "data/chapters/checked" in (lines[0] if lines else ""))
        check("4.3 三要素：哪件工序产的",
              "阶段5" in lines[0] and "检查" in lines[0], lines[:1])
        check("4.4 三要素：建议先跑哪件", "建议先跑阶段5" in lines[0], lines[:1])

        # 上游补齐 → 放行
        _put(root, "data/chapters/checked/01.md", "校对稿")
        lines = preflight_lines([6], root, will_produce=[6])
        check("4.5 上游在场 → 不报", lines == [], lines)

        # 一次报全：勾 6 和 3，两个都缺
        root2 = _mkroot()
        try:
            lines = preflight_lines([3, 6], root2, will_produce=[3, 6])
            check("4.6 一次报全（不是跑一半才报）", len(lines) >= 2, lines)
            check("4.7 两条分别指向不同上游",
                  any("data/outline/global.md" in x for x in lines)
                  and any("data/chapters/checked" in x for x in lines), lines)
        finally:
            shutil.rmtree(root2, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_will_produce():
    print("\n[5] 「本次会产出」的上游不算缺（不许把正常组合拦死）")
    root = _mkroot()
    try:
        # 勾 5,6,7：checked 是这次跑阶段5 才有的，现在当然没有
        _put(root, "data/chapters/raw/01.md", "毛坯")
        lines = preflight_lines([5, 6, 7], root, will_produce=[5, 6, 7])
        check("5.1 勾「检查+润色+出稿」不被自己拦死", lines == [], lines)
        # 只勾 6（不勾 5）→ checked 既没有、这次也不产 → 必须拦
        lines = preflight_lines([6], root, will_produce=[6])
        check("5.2 只勾润色而无校对产物 → 拦", len(lines) == 1, lines)
        # 阶段1 无上游要求（素材由用户放），不该报
        check("5.3 阶段1 不设上游门（素材非流水线产物）",
              missing_inputs(1, root) == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_any_group():
    print("\n[6] 出稿/导出的「三层任一可用」回退链")
    root = _mkroot()
    try:
        # 三层全空 → 整组报（让人一次看清三层都没有）
        probs = missing_inputs(7, root)
        check("6.1 三层全无 → 报出", len(producers_of(probs)) >= 1 and probs, probs)
        check("6.2 整组一起报（raw/checked/refined 三条）",
              len(probs) == 3 and all(p["kind"] == "any" for p in probs), len(probs))
        # 只有 raw（导入毛坯的典型态）→ 出稿放行
        _put(root, "data/chapters/raw/01.md", "毛坯")
        check("6.3 只有 raw 也能出稿（回退链，不硬要三层）",
              missing_inputs(7, root) == [])
        check("6.4 阶段8 同一条回退链", missing_inputs(8, root) == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def producers_of(probs):
    return sorted({p["produced_by"] for p in probs})


def test_advice_mentions_import():
    print("\n[7] 报缺文案给得出「导入」这条出路")
    root = _mkroot()
    try:
        lines = preflight_lines([7], root, will_produce=[7])
        check("7.1 阶段7 缺料时提示可用导入器落位",
              any("import_docx" in x for x in lines), lines)
        check("7.2 同时给出建议先跑的工序",
              any("建议先跑阶段4" in x for x in lines), lines)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_parse()
    test_mutex()
    test_order()
    test_preflight()
    test_will_produce()
    test_any_group()
    test_advice_mentions_import()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
