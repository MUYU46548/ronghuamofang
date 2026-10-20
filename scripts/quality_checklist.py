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

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def check_consistency(text, chapter_no, setting, require_character=True):
    """检查3：角色一致性。

    2026-10-19（P3）：优先读**一致性账本** `data/state/story_state.json`。
    账本里有跨章累计的事件（死亡 / 物品 / 时间锚），能判"第 3 章又出现了第 2 章
    已经死的人"这类硬伤 —— 旧实现只判"本章有没有提到任一已知角色"，
    等于函数在、判据空。

    账本不存在（没跑过带账本的 stage4）时**退回旧判据**：
    不能让一堆老工作区因为没账本而突然全红。
    """
    issues = []
    ledger = ROOT / "data" / "state" / "story_state.json"
    if ledger.exists():
        try:
            data = json.loads(ledger.read_text(encoding="utf-8"))
            # 账本存在但**一条事件都没有** = 还没扫过章节（空壳），
            # 这时不能接管 —— 否则旧判据被一个空账本静默架空。
            if data.get("events"):
                for c in data.get("conflicts") or []:
                    if c.get("chapter") == chapter_no and c.get("level") == "conflict":
                        issues.append("第%d章一致性冲突（%s）：%s"
                                      % (chapter_no, c.get("code"), c.get("detail")))
                return issues
        except Exception:                                      # noqa: BLE001
            pass                                               # 账本坏了退回旧判据

    if not setting.exists():
        return []
    data = json.loads(setting.read_text(encoding="utf-8"))
    characters = data.get("characters", [])
    # 检查是否提及了不存在的角色
    known_names = {c.get("name", "") for c in characters if c.get("name")}
    # 简单检查：如果设定集有角色，但章节一个都没提到
    # 散文/散文集没有角色 —— 这条判据对它是误报（文体见 utils/writing_form.py）
    if require_character and known_names and len(known_names) > 2:
        mentioned = sum(1 for name in known_names if name in text)
        if mentioned == 0 and len(text) > 500:
            issues.append(f"第{chapter_no}章未提及任何已知角色")
    return issues


def check_word_count(text, chapter_no, project):
    """检查4：字数达标。

    ## 两条判据（2026-10-18 补第二条）

    1. **全书均摊的半数**（旧判据，保留）：按 book.target_words / chapters 算
       每章均摊，低于它的一半判不足。
    2. **配置的单章下限**（新增）：`system.yaml` 的 `chapter.target_words[0]`。
       旧实现只看均摊值 —— 实测《雾港核心》book.target_words=9000 / chapters=3
       → 均摊 3000、门槛 1500，于是 2033 字的章**直接过检**，用户判定
       「一章两三千字根本不合格」。收尾闸门必须与写作阶段用的是**同一把尺**。
    """
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

    # 判据 2：单章下限（读不到 system.yaml 时静默跳过 —— 收尾闸门不该因
    # 「没有 system.yaml」而误报，但也**不因此放宽**：有值就必须过）。
    sys_yaml = ROOT / "config" / "system.yaml"
    if sys_yaml.exists():
        try:
            sys_cfg = load_config_yaml(sys_yaml) or {}
            tw = ((sys_cfg.get("chapter") or {}).get("target_words")) or []
            # 容错：老 bug 曾把 target_words 写成 dict（{low, high}）而非列表。
            # 这里按形态分别取下限，坏形态一律跳过判据（不误报，也不假装有闸门）。
            if isinstance(tw, dict):
                low = int(tw.get("low") or 0)
            elif isinstance(tw, (list, tuple)) and len(tw) >= 1:
                low = int(tw[0])
            else:
                low = 0
        except Exception:                                    # noqa: BLE001
            low = 0
        if low and actual < low:
            issues.append(f"第{chapter_no}章字数低于配置下限（{actual}/{low}，"
                          f"见 config/system.yaml 的 chapter.target_words）")
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

    # 文体（2026-10-20）：决定"未提角色"这条判据要不要跑
    from utils import writing_form as _wf
    _form_app = _wf.applicability(proj)
    _require_character = _form_app.get("must_mention_character", True)
    if _form_app.get("warning"):
        print("[quality] " + _form_app["warning"])
    if not _require_character:
        print("[quality] 文体：%s —— 跳过「未提及角色」判据" % _form_app["label"])

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
        issues.extend(check_consistency(text, ch, SETTING,
                                        require_character=_require_character))
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
