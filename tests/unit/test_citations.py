# -*- coding: utf-8 -*-
"""大纲引用解析自检（`utils/citations.py`，零 LLM、零网络）。

用户 2026-10-24 的取材方式：大纲里直接点名素材文件（「（碎片2026-4-8）」），
写作时必须机械兑现。本用例守五件事：

1. 抽取：全角/半角括号都认；「（参见相关碎片）」这种没有文件名的不算引用；
2. 命中：`小说碎片2026-4-8.md` 能被键 `碎片2026-4-8` 命中（词干包含判据）；
3. 缺失：找不到的键**如实报缺**，并在块里写明「不得编造」——绝不假装命中；
4. 截断：单卡超 PER_CITE_CHARS 标注原文长度 + 指路原文件；
5. 确定性：同一输入两次解析结果逐字一致（上下文不能一本书两次跑出两种样子）。

专名纪律：夹具全部自造（`碎片2099-1-1` 一类），不碰任何真实素材的专名。
用法：python tests/unit/test_citations.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.citations import (  # noqa: E402
    PER_CITE_CHARS, build_citation_block, extract_citations, resolve_citations,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    print("== 1. 抽取（纯函数） ==")
    # 键保留**用户原文全称**（含"小说"前缀）——键代表"大纲怎么写的"，
    # 命中靠词干包含判据跨前缀匹配（真实素材正是这种形态：大纲写
    # 「（碎片2026-4-8）」，文件叫「小说碎片2026-4-8.md」）。
    txt = ("甲向乙申请算力（碎片2099-1-1），推演周期。次日收到报告"
           "（小说碎片2099-2-2）。他参见相关碎片，又翻了（碎片2099-1-1）。")
    keys = extract_citations(txt)
    check("抽出两枚键（保留原文全称）",
          keys == ["碎片2099-1-1", "小说碎片2099-2-2"], keys)
    check("重复引用去重保序", len(keys) == 2)
    check("「参见相关碎片」不算引用（无数字键被丢弃）",
          "相关碎片" not in "".join(keys))
    check("半角括号同样认", extract_citations("用(碎片2099-3-3)补写") == ["碎片2099-3-3"])
    check("无引用文本 → 空列表", extract_citations("本章没有任何引用。") == [])
    check("空输入不抛", extract_citations("") == [] and extract_citations(None) == [])

    print("== 2/3/4. 命中 / 缺失 / 截断（真实文件系统，临时目录） ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "小说碎片2099-1-1.md").write_text(
            "超算申请单：算力配额七成，推演窗口三日。", encoding="utf-8")
        long_body = "长卡正文。" * (PER_CITE_CHARS // 5 + 50)
        (root / "守灯会设定碎片2099-2-2.md").write_text(long_body, encoding="utf-8")

        cites = resolve_citations("开篇用（碎片2099-1-1）与（碎片2099-2-2）"
                                  "以及（碎片2099-9-9）。", [str(root)])
        by_key = {c["key"]: c for c in cites}
        check("键数 = 3", len(cites) == 3, [c["key"] for c in cites])
        check("词干包含判据命中（小说碎片2099-1-1.md）",
              by_key["碎片2099-1-1"]["found"]
              and by_key["碎片2099-1-1"]["path"].endswith("小说碎片2099-1-1.md"))
        check("长词干也能命中（守灯会设定碎片2099-2-2.md）",
              by_key["碎片2099-2-2"]["found"])
        check("缺失键如实报缺（碎片2099-9-9）",
              by_key["碎片2099-9-9"]["found"] is False)
        check("单卡超限 → truncated 且正文截到上限",
              by_key["碎片2099-2-2"]["truncated"]
              and len(by_key["碎片2099-2-2"]["_text"]) == PER_CITE_CHARS)

        block = build_citation_block(cites)
        check("块含取材指令抬头", "取材指令" in block)
        check("命中卡正文进块", "超算申请单" in block)
        check("截断卡给出原文件指路", "完整原文见" in block
              and "守灯会设定碎片2099-2-2.md" in block)
        check("缺失卡显式告警 + 禁止编造",
              "未找到" in block and "不得编造" in block and "碎片2099-9-9" in block)

        # 5. 确定性
        again = build_citation_block(
            resolve_citations("开篇用（碎片2099-1-1）与（碎片2099-2-2）"
                              "以及（碎片2099-9-9）。", [str(root)]))
        check("两次解析逐字一致（确定性）", again == block)

    print("== 边界 ==")
    check("无引用 → 块为空串（模板渲染成空行）",
          build_citation_block([]) == "")
    check("不存在的素材目录不抛（返回缺省）",
          resolve_citations("（碎片2099-1-1）", ["/no/such/dir"])[0]["found"] is False)

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
