# -*- coding: utf-8 -*-
"""上下文记忆策略的自检（2026-10-20，台账三·「上下文 memory policy」）。

## 主判据

**L2 滚动层**
1. 200 章时只注入最近 `rolling_window` 章 + 全书摘要（不是 200 章全塞）
2. 被裁掉的章号**必须写出来**（"更早章节摘要未注入：第 1–191 章"）——
   否则模型会以为故事从第 192 章开始
3. 未来章节（章号 >= 本章）的摘要一律不注入（那是剧透，不是记忆）
4. 没有滚动摘要时给明确文案，不抛异常也不留空

**L3 召回层**
5. 伏笔：设定集 `plot_fragments` 里与本章大纲有词重合的碎片被召回，带 `[伏笔]`
6. 无关碎片**不**召回（"宁可少给，也不灌无关历史"）
7. 状态：本章之前的事件被召回（带章号），**未来事件不召回**
8. 关系：`relations` 的 dict / list 两种形态都读得出来
9. 角色：滚动摘要里提到本章角色的章被召回；最近 3 章排除（那段由上一章尾巴覆盖）
10. 每条召回都带「为什么在这里」（命中词 / 用词重合）—— 不许只有正文
11. 预算收紧后召回变少，且写明"因预算/top-K 未注入"
12. 一条都召不回时给明确文案（"不是漏了，是没有"）

用法：python tests/unit/test_memory_policy.py
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

from utils import memory_policy as MP                                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


def write_rolling(path, total_chapters, n_chapters=199):
    """造 1..n_chapters 章摘要（每章一句独一无二的话，便于断言"哪一章进来了"）。"""
    lines = ["# 滚动摘要", "## 全书摘要",
             "全书总述：露汐在港城追查一批失踪的旧档案。", "", "## 近期章节"]
    for i in range(1, n_chapters + 1):
        # 每 3 章让露汐露一次面：角色维召回需要"摘要里提到本章角色"才成立，
        # 全是"他"的摘要召回不出角色（这也是真实写作里会遇到的情况）。
        who = "露汐在档案室翻看一批旧卷宗" if i % 3 == 0 else "他在码头等一个人"
        lines += ["### 第%d章" % i,
                  "第%d章摘要：%s，雨下了一夜，那封信始终没有送到。" % (i, who)]
        lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def scene(tmp, **over):
    """造一套最小工作区：设定集 / 滚动摘要 / 一致性账本。"""
    s = {
        "characters": [
            {"id": "c001", "name": "露汐", "role": "档案室整理员",
             "traits": ["记事极细"], "relations": {"凤鸣": "她欠他一个人情"}},
            {"id": "c002", "name": "凤鸣", "role": "巡夜人",
             "traits": ["沉默"], "relations": [{"name": "露汐", "desc": "师徒"}]},
            {"id": "c003", "name": "无相关的路人", "role": "路人"},
        ],
        "items": [{"name": "怀表"}],
        "plot_fragments": [
            "怀表的来历始终没有交代，链子上刻着一个旧年号",
            "沙漠商队每年秋天经过北门，带着盐和铁器",
        ],
        "world": {"地理": "港城分七区，外围是滩涂。"},
        "timeline": ["某年冬，封锁开始"],
    }
    s.update(over.get("setting") or {})
    sp = Path(tmp) / "setting.json"
    sp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")

    st = {
        "version": 1,
        "events": [
            {"chapter": 3, "type": "death", "who": "露汐", "evidence": "露汐倒在雨里，没再起来"},
            {"chapter": 5, "type": "lose_item", "who": "凤鸣", "item": "怀表",
             "evidence": "他把怀表扔进河里"},
            {"chapter": 12, "type": "act", "who": "凤鸣", "evidence": "凤鸣推门进来"},
            {"chapter": 40, "type": "death", "who": "凤鸣", "evidence": "未来的事"},
        ],
        "conflicts": [],
    }
    stp = Path(tmp) / "state.json"
    stp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return str(sp), str(stp)


OUTLINE = (
    "核心事件：露汐在档案室翻到一只怀表，链子上刻着模糊的年号。\n"
    "涉及角色：露汐、凤鸣\n功能：揭开旧案的第一层。"
)


def run(n, tmp, outline=OUTLINE, cfg=None):
    sp, stp = scene(tmp)
    rp = Path(tmp) / "rolling.md"
    if not rp.exists():
        write_rolling(rp, n)
    return MP.build_memory(n, outline, sp, str(rp), stp, cfg)


# ====================================================================== P1
def case_rolling_layers():
    print("\n[P1] L2 滚动层：200 章只给最近 N 章 + 全书摘要，裁掉的章号必须写出来")
    tmp = Path(tempfile.mkdtemp())
    try:
        r = run(200, tmp)
        block = r["block"]
        st = r["stats"]["rolling"]
        check("P1 只取最近 rolling_window 章", len(st["chapters"]) <= 8, st["chapters"])
        check("P1 最近那章（199）在注入列表里", 199 in st["chapters"], st["chapters"])
        check("P1 第 1 章摘要没被整段注入", "第1章摘要：他在码头" not in block,
              block[:300])
        check("P1 明确写出省略范围（不静默）",
              "更早章节摘要未注入" in block and "第 1–" in block.replace("第 1-", "第 1–"),
              [l for l in block.splitlines() if "省略" in l or "未注入" in l])
        check("P1 但全书摘要照给", "全书总述" in block)
        check("P1 章号顺序从近到远（199 在 192 之前）",
              block.index("【第199章】") < block.index("【第192章】") if 192 in st["chapters"] else True,
              st["chapters"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_future():
    print("\n[P2] 未来章摘要/事件不注入（那是剧透，不是记忆）")
    tmp = Path(tempfile.mkdtemp())
    try:
        r = run(4, tmp)          # 第 4 章：rolling 里有 1..199，state 里有第 5/12/40 章事件
        check("P2 第 4 章不注入第 5 章及以后的摘要",
              all(c < 4 for c in r["stats"]["rolling"]["chapters"]),
              r["stats"]["rolling"]["chapters"])
        texts = " ".join(c["text"] for c in r["recall"])
        check("P2 第 4 章不召回第 5 章「扔怀表」事件", "扔进河里" not in texts, texts)
        check("P2 但会召回第 3 章的死亡事件", "第3章死亡" in texts, texts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================== P3
def case_recall_dims():
    print("\n[P3] L3 四维召回：伏笔 / 状态 / 关系 / 角色")
    tmp = Path(tempfile.mkdtemp())
    try:
        r = run(13, tmp)
        dims = {c["dim"] for c in r["recall"]}
        texts = "\n".join(c["text"] for c in r["recall"])
        check("P3 伏笔被召回（怀表碎片）", "foreshadow" in dims and "链子上刻着" in texts, texts)
        check("P3 无关碎片不召回（沙漠商队）", "沙漠商队" not in texts, texts)
        check("P3 状态被召回（扔怀表 / 第 3 章死亡）", "state" in dims, dims)
        check("P3 关系被召回（露汐/凤鸣）", "relation" in dims, dims)
        check("P3 角色被召回（滚动摘要里提到露汐的章）", "character" in dims, dims)
        check("P3 每条都带标签 [伏笔]/[状态]/[关系]",
              "[伏笔]" in r["block"] and "[状态]" in r["block"], r["block"][-400:])
        check("P3 每条都写了为什么（命中：）",
              all(c.get("why") for c in r["recall"]),
              [c.get("why") for c in r["recall"]])
        check("P3 角色召回排除了最近 3 章（那段由上一章尾巴覆盖）",
              all((c.get("chapter") or 0) <= 13 - 3 for c in r["recall"]
                  if c["dim"] == "character"),
              [c for c in r["recall"] if c["dim"] == "character"])
        check("P3 统计按维计数",
              sum(r["stats"]["recall"]["by_dim"].values()) == r["stats"]["recall"]["recalled"],
              r["stats"]["recall"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_budget_and_empty():
    print("\n[P4] 预算收紧 / 空数据 / 一条都召不回")
    tmp = Path(tempfile.mkdtemp())
    try:
        tight = {"context": {"memory": {"recall_chars": 120, "recall_top_k": 1}}}
        r = run(13, tmp, cfg=tight)
        check("P4 预算收紧后召回变少（<=1 条）", r["stats"]["recall"]["recalled"] <= 1,
              r["stats"]["recall"])
        check("P4 并写明因预算/top-K 未注入",
              "未注入" in r["block"] or r["stats"]["recall"]["dropped"] == 0,
              r["stats"]["recall"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp2 = Path(tempfile.mkdtemp())
    try:
        # 完全无数据：setting/rolling/state 都不存在
        r = MP.build_memory(2, "第一章的续写。", str(Path(tmp2) / "nope.json"),
                            str(Path(tmp2) / "nope.md"), str(Path(tmp2) / "nope2.json"))
        check("P4 无任何数据也不抛异常", isinstance(r["block"], str) and r["block"])
        check("P4 明确写出「没有滚动摘要」", "没有滚动摘要" in r["block"], r["block"])
        check("P4 明确写出「未召回到相关历史」", "未召回到相关历史" in r["block"], r["block"])

        # 有数据但全无关
        sp, stp = scene(tmp2)
        rp = Path(tmp2) / "rolling.md"
        write_rolling(rp, 5)
        r2 = MP.build_memory(9, "他在屋里擦桌子，一句话也没说。", sp, str(rp), stp)
        check("P4 全无关的大纲 → 召回 0 条且明说",
              r2["stats"]["recall"]["recalled"] == 0 and "未召回到相关历史" in r2["block"],
              r2["stats"]["recall"])
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


def case_config_guard():
    print("\n[P5] 配置写坏不静默变成 0（预算/窗口）")
    b = MP._budget({"context": {"memory": {"rolling_chars": "abc", "recall_top_k": -3}}})
    check("P5 非法 rolling_chars 回落默认", b["rolling_chars"] == MP.DEFAULT_BUDGET["rolling_chars"], b)
    check("P5 负数 top_k 被接受但不会崩", isinstance(b["recall_top_k"], int), b)
    b2 = MP._budget({})
    check("P5 无配置时取默认值", b2 == MP.DEFAULT_BUDGET, b2)
    check("P5 默认窗口 <= 8（200 章不能全塞）", MP.DEFAULT_BUDGET["rolling_window"] <= 8,
          MP.DEFAULT_BUDGET)


def main():
    print("=" * 62)
    print("  上下文记忆策略自检（docs/收口台账-20261019.md 三·memory policy）")
    print("=" * 62)
    case_rolling_layers()
    case_no_future()
    case_recall_dims()
    case_budget_and_empty()
    case_config_guard()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
