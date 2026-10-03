# -*- coding: utf-8 -*-
"""章节质量验收 checklist（防止明鉴悲剧：五十万字债）。

用法：
    python scripts/quality_checklist.py              # 检查当前书档
    python scripts/quality_checklist.py --chapter 3  # 检查指定章节

检查维度：
1. 结构完整性：章节文件存在、非空、有段落
2. 逻辑一致性：无明显矛盾（时间线、角色行为）
3. 角色一致性：角色名与设定集匹配
4. 字数达标：章节字数在目标范围内
5. 大纲对齐：章节内容与逐章大纲匹配
6. 退化检测：无复读填充、思考残片、元话语
"""
import argparse
import json
import re
import sys
from pathlib import Path
from utils.config_io import load_config_yaml

ROOT = Path(__file__).resolve().parents[1]
SETTING = ROOT / "data" / "setting" / "setting.json"
CHAPTERS = ROOT / "data" / "chapters" / "refined"
CHAPTERS_RAW = ROOT / "data" / "chapters" / "raw"
OUTLINE = ROOT / "data" / "outline" / "chapters"
PROJECT = ROOT / "config" / "project.yaml"


def count_cn_words(text):
    """统计中文字符数。"""
    return len(re.findall(r'[\u4e00-\u9fff]', text))


def check_structure(chapter_file, chapter_no):
    """检查1：结构完整性。"""
    issues = []
    if not chapter_file.exists():
        return [f"第{chapter_no}章文件不存在"]
    text = chapter_file.read_text(encoding="utf-8")
    if len(text.strip()) < 100:
        issues.append(f"第{chapter_no}章过短（{len(text)}字符）")
    if "\n\n" not in text:
        issues.append(f"第{chapter_no}章无段落分隔")
    return issues


def check_degeneration(text, chapter_no):
    """检查2：退化检测。"""
    issues = []
    # 复读填充：同一段落重复3次以上
    lines = text.split("\n")
    for i in range(len(lines) - 2):
        if lines[i].strip() and lines[i] == lines[i + 1] == lines[i + 2]:
            issues.append(f"第{chapter_no}章第{i}行有复读填充")
            break
    # 思考残片
    if "reasoning_content" in text or "thinking" in text.lower():
        issues.append(f"第{chapter_no}章含思考残片")
    # 元话语拒答
    if "原稿内容缺失" in text or "基于已有信息无法处理" in text:
        issues.append(f"第{chapter_no}章含元话语拒答")
    # 无段落换行
    if text.count("\n") < 3:
        issues.append(f"第{chapter_no}章无段落换行")
    return issues


def check_consistency(text, chapter_no, setting):
    """检查3：角色一致性。"""
    issues = []
    if not setting.exists():
        return []
    data = json.loads(setting.read_text(encoding="utf-8"))
    characters = data.get("characters", [])
    # 检查是否提及了不存在的角色
    known_names = {c.get("name", "") for c in characters if c.get("name")}
    # 简单检查：如果设定集有角色，但章节一个都没提到
    if known_names and len(known_names) > 2:
        mentioned = sum(1 for name in known_names if name in text)
        if mentioned == 0 and len(text) > 500:
            issues.append(f"第{chapter_no}章未提及任何已知角色")
    return issues


def check_word_count(text, chapter_no, project):
    """检查4：字数达标。"""
    issues = []
    if not project.exists():
        return []
    proj = load_config_yaml(project)
    target = proj.get("book", {}).get("target_words", 9000)
    chapters = proj.get("book", {}).get("chapters", 10)
    per_chapter = target // max(chapters, 1)
    actual = count_cn_words(text)
    if actual < per_chapter * 0.5:
        issues.append(f"第{chapter_no}章字数不足（{actual}/{per_chapter}）")
    return issues


def check_outline_alignment(text, chapter_no):
    """检查5：大纲对齐。"""
    issues = []
    outline_file = OUTLINE / f"{chapter_no:02d}.md"
    if not outline_file.exists():
        return []
    outline = outline_file.read_text(encoding="utf-8")
    # 简单检查：大纲中的关键词是否出现在章节中
    keywords = re.findall(r'\*\*([^*]+)\*\*', outline)
    if keywords:
        matched = sum(1 for kw in keywords if kw in text)
        if matched == 0:
            issues.append(f"第{chapter_no}章与大纲关键词不匹配")
    return issues


def main():
    # 控制台安全网：本脚本不 import utils（纯 stdlib），故显式调一次 ——
    # stdout 被管道捕获（GUI/Electron spawn、后台任务）时是 cp936，而通过行含
    # ✓/✗ 不在 GBK 里 → UnicodeEncodeError → **门禁明明通过却退 1**（假红），
    # 而 orchestrator 的 gates.quality_gate 正是靠退出码阻断"全部完成"。
    from utils.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="章节质量验收 checklist")
    parser.add_argument("--chapter", type=int, help="只检查指定章节")
    parser.add_argument("--allow-missing", action="store_true",
                        help="把「产物缺失」降级为**明确标注的跳过**（探索期/只写了前几章时用）。"
                             "默认缺失即失败：跑完却缺文件绝不算通过。")
    args = parser.parse_args()

    if not PROJECT.exists():
        print("ERROR: config/project.yaml 不存在")
        sys.exit(1)
    proj = load_config_yaml(PROJECT)
    total_chapters = proj.get("book", {}).get("chapters", 10)

    if args.chapter:
        chapters = [args.chapter]
    else:
        chapters = list(range(1, total_chapters + 1))
    if not chapters:
        # 空集合 = 什么都没验 → 不许"全过"（vacuous truth 是门禁最阴的假绿）
        print("ERROR: 没有可验收的章节（config/project.yaml 的 book.chapters 为 0？）")
        sys.exit(1)

    print(f"章节质量验收 checklist（共 {len(chapters)} 章）")
    print("=" * 60)

    all_pass = True
    missing = []
    for ch in chapters:
        chapter_file = CHAPTERS / f"{ch:02d}.md"
        if not chapter_file.exists():
            chapter_file = CHAPTERS_RAW / f"{ch:02d}.md"
        if not chapter_file.exists():
            # ⚠️ 2026-10-03（用户拍板）：**产物缺失一律失败**。
            # 旧实现这里只打一行 `[SKIP] 文件不存在` 就 continue，`all_pass` 保持 True
            # → 末尾照打「全部章节通过验收 ✓」并退 0。而本脚本是 orchestrator 的收尾闸门
            # （gates.quality_gate），于是"跑了但没落盘"这种最该拦的情形被盖了绿灯。
            # 产物存在 ≠ 合格（那要靠下面的逐项检查）；但**产物缺失一定不合格**。
            missing.append(ch)
            if args.allow_missing:
                print(f"\n第{ch}章: [SKIP] 产物缺失（--allow-missing 放行，**未经验收**）")
                continue
            all_pass = False
            print(f"\n第{ch}章: [FAIL] 产物缺失 —— 该章没有任何落盘文件")
            print(f"  - 既无 {CHAPTERS.relative_to(ROOT).as_posix()}/{ch:02d}.md，"
                  f"也无 {CHAPTERS_RAW.relative_to(ROOT).as_posix()}/{ch:02d}.md")
            print("  - 「跑过但没落盘」= 失败（截断丢弃 / 子会话异常 / 被删）；"
                  "确属还没写到这一章，请显式加 --allow-missing（届时会标注为未验收）")
            continue

        text = chapter_file.read_text(encoding="utf-8")
        issues = []
        issues.extend(check_structure(chapter_file, ch))
        issues.extend(check_degeneration(text, ch))
        issues.extend(check_consistency(text, ch, SETTING))
        issues.extend(check_word_count(text, ch, PROJECT))
        issues.extend(check_outline_alignment(text, ch))

        if issues:
            all_pass = False
            print(f"\n第{ch}章: [FAIL]（{len(issues)} 个问题）")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"\n第{ch}章: [PASS]")

    print("\n" + "=" * 60)
    if all_pass:
        if missing:
            # 不许把"跳过"说成"通过"（旧实现的病根就是这两句话糊在一起）
            print(f"全部章节处理完毕，但其中 {len(missing)} 章产物缺失、**未经验收**："
                  + "、".join(str(c) for c in missing))
            print("（--allow-missing 已放行；这批章节不能算验收通过）")
        else:
            print("全部章节通过验收 ✓")
        sys.exit(0)
    else:
        if missing:
            print(f"有 {len(missing)} 章**产物缺失**（跑完却没落盘），另有章节未通过检查")
        print("部分章节未通过，请修复后再继续")
        sys.exit(1)


if __name__ == "__main__":
    main()
