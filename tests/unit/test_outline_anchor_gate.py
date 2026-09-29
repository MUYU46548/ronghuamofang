# -*- coding: utf-8 -*-
"""大纲产出侧锚点闸门自检（件3，2026-09-29）。

验收（执行单 v2.2 件3）：「构造 THIN 样本回放：被校验打回；提示词含锚点数下限」。

覆盖：
A. `stage2_outline.check_outline_anchors` —— THIN 超上限判不合格、在上限内放行；
   判据确实**复用** outline_review（不另写一套打分）。
B. `prompts/stage2_global_outline.md` 里确实写入了锚点数下限硬约束。
C. 结构校验 `check_global_outline` **未被牵连**（GUI 保存/精修共用它，不应因空泛被拦）。

全程离线、零网络、零费用。
用法：python tests/unit/test_outline_anchor_gate.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from stage2_outline import check_outline_anchors, check_global_outline   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


THIN_NODE = "- 节点{n}：推进主线，铺垫后续转折。"
GOOD_NODE = ("- 节点{n}：第{n}-{m}章 落魄机关术师林深在边城营地被守卫队长苏晚找上门，"
             "两人一同出发前往归墟遗址，在遗址深处发现异常封印遗迹，"
             "与朝廷暗卫爆发正面冲突，苏晚出手相救，林深决定追查三年前那场失控的真相。")
HEAD = ("# 《归墟核心》整体大纲\n## 起\n起\n## 承\n承\n## 转\n转\n## 合\n合\n"
        "## 关键节点\n")


def write(tmp, name, body):
    p = Path(tmp) / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_gate():
    print("\n[A] THIN 超限被打回 / 上限内放行")
    with tempfile.TemporaryDirectory() as tmp:
        thin2 = write(tmp, "thin2.md", HEAD + THIN_NODE.format(n=1) + "\n" + THIN_NODE.format(n=2) + "\n")
        ok, errs = check_outline_anchors(thin2, setting_path=str(Path(tmp) / "no_setting.json"))
        check("2 条 THIN → 判不合格", ok is False, errs)
        check("错误信息带 THIN 计数与上限", bool(errs) and "THIN" in errs[0] and "上限" in errs[0], errs)

        thin1 = write(tmp, "thin1.md", HEAD + THIN_NODE.format(n=1) + "\n" + GOOD_NODE.format(n=2, m=4) + "\n")
        ok1, errs1 = check_outline_anchors(thin1, setting_path=str(Path(tmp) / "no_setting.json"))
        check("1 条 THIN（= 容差上限）→ 放行", ok1 is True, errs1)

        good = write(tmp, "good.md", HEAD + GOOD_NODE.format(n=1, m=2) + "\n" + GOOD_NODE.format(n=2, m=4) + "\n")
        ok2, errs2 = check_outline_anchors(good, setting_path=str(Path(tmp) / "no_setting.json"))
        check("全锚点 → 放行", ok2 is True, errs2)


def test_not_touching_structural():
    print("\n[B] 结构校验未被牵连（GUI 保存/精修共用它）")
    with tempfile.TemporaryDirectory() as tmp:
        thin = write(tmp, "thin.md", HEAD + THIN_NODE.format(n=1) + "\n## 预计章节数\n3\n")
        ok, errs = check_global_outline(thin)
        check("结构完整但空泛 → 结构校验仍通过（不越权拦人）", ok is True, errs)


def test_prompt_has_floor():
    print("\n[C] 提示词含锚点数下限硬约束")
    text = (ROOT / "prompts" / "stage2_global_outline.md").read_text(encoding="utf-8")
    check("含「硬约束（锚点数下限）」", "锚点数下限" in text)
    check("含场景地点要求", "具体场景地点" in text)
    check("含 THIN 打回说明", "THIN" in text and "打回" in text)
    check("保留 {{target_words}} 占位符（渲染不被破坏）", "{{target_words}}" in text)


def main():
    print("===== 大纲锚点闸门自检（件3）=====")
    test_gate()
    test_not_touching_structural()
    test_prompt_has_floor()
    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
