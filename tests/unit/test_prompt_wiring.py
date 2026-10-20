# -*- coding: utf-8 -*-
"""提示词接线自检（2026-10-20）：新注入块**真的进了任务文本**。

## 为什么单独测这个

每个块的生成函数都有自己的单元测试（memory_policy / setting_brief / volume_arc），
但"生成对了"不等于"接进 prompt 了"。中间隔着 `load_template(name, {...})` 的一次
变量传递 —— 漏一个键，模板里就留一个 `{{xxx}}` 字面量，模型看到的是占位符，
而**所有函数级测试照样全绿**。

本项目踩过同类事故（stage5 空稿：批次列表写成裸文件名 → 章节从未进 prompt，
上层的"检查完成"照打）。所以这里断言三件事：

1. 四个阶段的任务文本里都出现了新块的关键句；
2. **任务文本里不允许残留 `{{占位符}}`**（漏传变量的唯一可靠信号）；
3. 关键块不是空的（不是"变量传了但值是空串"）。

用法：python tests/unit/test_prompt_wiring.py
"""
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []
PLACEHOLDER_RE = re.compile(r"\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (str(detail)[:300],)))


def build_ws():
    tmp = Path(tempfile.mkdtemp(prefix="nf_wiring_"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        "book:\n  name: \"接线测试\"\n  chapters: 40\n  target_words: 200000\n",
        encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [
            {"id": "c001", "name": "苏芷", "role": "档案室整理员",
             "traits": ["记事极细"], "relations": {"周砚清": "她欠他一个人情"}},
            {"id": "c002", "name": "周砚清", "role": "巡夜人", "traits": ["沉默"]},
        ],
        "world": {"locations": [{"name": "河口三区", "snippet": "旧库房所在"}]},
        "plot_fragments": ["怀表的来历始终没有交代"],
        "timeline": ["某年冬，封锁开始"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 接线测试 大纲\n\n## 起\n开篇\n## 承\n## 转\n## 合\n## 关键节点\n"
        "- 节点一\n## 预计章节数\n40\n", encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
        "# 第1章 锈锁\n\n- 核心事件：苏芷在档案室翻到一只怀表\n"
        "- 涉及角色：苏芷、周砚清\n- 功能：推进\n", encoding="utf-8")
    (tmp / "data" / "summaries").mkdir(parents=True)
    (tmp / "data" / "summaries" / "rolling.md").write_text(
        "# 滚动摘要\n## 全书摘要\n苏芷在河口追查一桩旧案。\n\n## 近期章节\n"
        "### 第1章\n苏芷翻到怀表，链子上刻着年号。\n", encoding="utf-8")
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "materials").mkdir()
    return tmp


def main():
    print("=" * 62)
    print("  提示词接线自检（新增块是否真的进了任务文本）")
    print("=" * 62)
    tmp = build_ws()
    cwd = Path.cwd()
    try:
        import os
        os.chdir(tmp)
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))

        print("\n[P1] stage2：设定集摘要内联")
        import stage2_outline as S2
        b2 = S2.build_task(cfg, proj)
        check("P1 stage2 任务含设定集摘要块", "设定集摘要" in b2, b2[:200])
        check("P1 摘要有角色（不是空块）", "苏芷" in b2 and "记事极细" in b2)
        check("P1 摘要有伏笔段（旧实现从没进过大纲）", "伏笔" in b2 and "怀表的来历" in b2)
        check("P1 摘要有时间线", "封锁开始" in b2)

        print("\n[P2] stage3：卷弧上下文")
        import stage3_chapter_outline as S3
        b3 = S3.build_batch_task(cfg, proj, [1], "data/outline/global.md",
                                 "data/setting/setting.json")
        check("P2 stage3 任务含卷弧块", "卷弧" in b3, b3[:200])
        check("P2 无卷纲时明确说「尚未生成」", "尚未生成" in b3, b3[-300:])

        print("\n[P3] stage4：记忆上下文")
        import stage4_writing as S4
        b4 = S4.build_chapter_task(cfg, proj, 1, "data/outline/chapters/01.md",
                                   "data/setting/setting.json",
                                   "data/summaries/rolling.md", ["上一章末尾这一段。"])
        check("P3 stage4 任务含记忆块", "记忆上下文" in b4, b4[:200])
        check("P3 有 L2 滚动层", "L2 滚动记忆" in b4 and "全书摘要" in b4)
        check("P3 有 L3 召回层", "L3 相关历史" in b4)
        # L3 至少要有**具体条目**或**明确的空态**。断言不能只盯某一维：
        # 本夹具的任务是开篇，滚动摘要里唯一一章是"未来"（不召回），伏笔那条
        # 又只与大纲重合 2-3 个 bigram（低于门槛）—— 真正该命中的是关系维
        # （苏芷/周砚清都出现在本章大纲里）。判据写成"任意一维有条目或空态"。
        dims = [d for d in ("[伏笔]", "[角色]", "[状态]", "[关系]") if d in b4]
        check("P3 L3 有具体召回条目或明确空态",
              bool(dims) or "未召回到相关历史" in b4, (dims, b4[-400:]))
        check("P3 关系维召回成功（两个角色都在本章大纲里）", "[关系]" in b4, b4[-400:])

        print("\n[P4] stage5：七维评审契约")
        from utils.template_loader import load_template
        from utils.file_io import read_text
        # 用 stage5 的真实构造入口（它会填 path_review 等变量）
        import stage5_check as S5
        from utils.file_io import write_text
        (Path("data/chapters/raw")).mkdir(parents=True, exist_ok=True)
        write_text("data/chapters/raw/01.md", "他推开门的时候，屋檐上的水线正连成了串。")
        f = Path("data/chapters/raw/01.md").resolve()
        b5 = S5.build_check_task(proj, "data/chapters/raw", "data/setting/setting.json",
                                 "data/chapters/checked", "- %s" % f, 1, 2)
        check("P4 stage5 任务含七维评审节", "七维评审账本" in b5, b5[:200])
        check("P4 要求引用原文（不是自由文本）", "逐字存在" in b5)
        check("P4 写明了账本输出路径且是绝对路径",
              "batch1.json" in b5 and str(Path("data/review/batch1.json").resolve()) in b5,
              [l for l in b5.splitlines() if "batch1.json" in l])

        print("\n[P5] 全局：任务文本里不许残留 {{占位符}}")
        for name, body in (("stage2", b2), ("stage3", b3), ("stage4", b4), ("stage5", b5)):
            left = PLACEHOLDER_RE.findall(body)
            check("P5 %s 无未替换占位符" % name, not left, left[:5])
    finally:
        import os
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
