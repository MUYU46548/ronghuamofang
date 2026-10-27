# -*- coding: utf-8 -*-
"""病灶词扫描（吃书⑦）在本仓的入口判据 —— 零 token、确定性。

## 为什么单独测一层

实现本体是从 SP-1024 原样搬来的（`utils/eatbook/cliche_scanner`），但**在本仓怎么取词表、
怎么找正文**是本层新写的，那才是会悄悄错的地方：
- 词表坏了要不要炸掉整次验收？（答：回落默认词表，扫描本身不该因配置崩）
- `refined/checked/raw` 三层都在时扫哪一层？（答：与编辑器同序，取最高一层）

## 判据

A. 默认词表：计数、密度（每千字）、排行、位置示例带【】
B. 自定义词表：字面词 / `regex:` / `#` 注释 / 空行 / 同名去重
C. 词表不可用 → 回落默认（不抛）
D. 章节定位：refined > checked > raw；都没有才报错
E. 接线：`quality_checklist` 只报告不阻断；`nfctl cliche` 子命令存在
用法：python tests/unit/test_cliche_scan.py
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import cliche_scan                                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


TEXT = ("他眼底闪过一丝慌乱，随即微微一笑。\n"
        "她也微微一笑，仿佛雨停了一般。\n"
        "这一句是干净的。\n")


def main():
    print("\n【A】默认词表（与 SP-1024 同一口径：字面子串、不去重长词）")
    rep = cliche_scan.scan_text(TEXT)
    hits = {h["词"]: h for h in rep["命中"]}
    check("命中『微微一笑』2 次", hits.get("微微一笑", {}).get("次数") == 2, hits.get("微微一笑"))
    check("命中『眼底闪过一丝』1 次", hits.get("眼底闪过一丝", {}).get("次数") == 1)
    check("『仿佛…一般』按正则命中", hits.get("仿佛…一般", {}).get("次数") == 1,
          hits.get("仿佛…一般"))
    check("排行按次数从高到低", rep["排行"][0][0] == "微微一笑", rep["排行"])
    check("位置示例带【】标出命中处",
          "【微微一笑】" in hits["微微一笑"]["位置示例"], hits["微微一笑"]["位置示例"])
    total_chars = len("".join(TEXT.split()))
    check("密度 = 次数/千字（保留两位）",
          hits["微微一笑"]["密度每千字"] == round(2 * 1000.0 / total_chars, 2),
          hits["微微一笑"]["密度每千字"])
    check("总字数口径 = 去空白字符数", rep["总字数"] == total_chars, rep["总字数"])

    print("\n【B】自定义词表（字面 / regex: / 注释 / 空行 / 去重）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_cliche_"))
    try:
        wl = tmp / "wl.txt"
        wl.write_text("# 这是注释\n\n微微一笑\n微微一笑\nregex:仿佛[^，。]{0,6}一般\n",
                      encoding="utf-8")
        rep2 = cliche_scan.scan_text(TEXT, wordlist_path=str(wl))
        terms = [h["词"] for h in rep2["命中"]]
        check("词表只认 2 条（同名去重）", rep2["词表条目数"] == 2, rep2["词表条目数"])
        check("regex: 条目保留原始正则作为显示名",
              any("仿佛" in t for t in terms), terms)

        empty = tmp / "empty.txt"
        empty.write_text("# 只有注释\n\n", encoding="utf-8")
        rep3 = cliche_scan.scan_text(TEXT, wordlist_path=str(empty))
        check("空词表 → 回落默认（不抛）", rep3["词表条目数"] >= 20, rep3["词表条目数"])

        missing = cliche_scan.scan_text(TEXT, wordlist_path=str(tmp / "nope.txt"))
        check("词表文件不存在 → 回落默认", missing["词表条目数"] >= 20)

        print("\n【C】章节定位 refined > checked > raw")
        ws = tmp / "ws"
        for layer, body in (("raw", "## 第1章\n\nraw 里的正文，微微一笑。"),
                            ("checked", "## 第1章\n\nchecked 里的正文。"),
                            ("refined", "## 第1章\n\nrefined 里的正文，微微一笑。")):
            d = ws / "data" / "chapters" / layer
            d.mkdir(parents=True, exist_ok=True)
            (d / "01.md").write_text(body, encoding="utf-8")
        p = cliche_scan.chapter_path(1, root=ws)
        check("三层都在 → 取 refined", p is not None and p.parent.name == "refined", p)
        (ws / "data" / "chapters" / "refined" / "01.md").unlink()
        p = cliche_scan.chapter_path(1, root=ws)
        check("refined 没了 → 退 checked", p is not None and p.parent.name == "checked", p)
        (ws / "data" / "chapters" / "checked" / "01.md").unlink()
        p = cliche_scan.chapter_path(1, root=ws)
        check("再退 raw", p is not None and p.parent.name == "raw", p)
        raised = False
        try:
            cliche_scan.scan_chapter(99, root=ws)
        except FileNotFoundError:
            raised = True
        check("三层都没有 → FileNotFoundError（不返回空报告冒充正常）", raised)

        rep_ch = cliche_scan.scan_chapter(1, root=ws)
        check("scan_chapter 带上来源文件", rep_ch.get("file", "").endswith("01.md"),
              rep_ch.get("file"))
        check("top_hits 截断", len(cliche_scan.top_hits(rep_ch, 1)) <= 1)

        print("\n【D】接线：只报告不阻断 + 命令存在")
        qc = (ROOT / "scripts" / "quality_checklist.py").read_text(encoding="utf-8")
        check("quality_checklist 调用了病灶词扫描", "cliche_scan" in qc)
        check("病灶词段落明确「只报告，不阻断验收」", "只报告，不阻断验收" in qc)
        _start = qc.find("病灶词扫描（只报告")
        _end = qc.find('print("\\n" + "=" * 60)', _start)
        block = qc[_start:_end] if _start >= 0 and _end > _start else ""
        check("病灶词段落里没有 all_pass（不参与门禁）",
              bool(block) and "all_pass" not in block, block[:80])
        nfctl = (ROOT / "scripts" / "nfctl.py").read_text(encoding="utf-8")
        check("nfctl 有 cliche 子命令", '"cliche"' in nfctl and "collect_cliche" in nfctl)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
