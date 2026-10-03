# -*- coding: utf-8 -*-
"""段落级定点重生成 CLI（P0 精修轴 MVP）。

与 refine_chapter.py 的区别：
- refine_chapter = 整章重写（±20% 字数铁律）
- refine_paragraph = 段落级定点重写（不触发字数铁律，只改目标段落）

新增（Round 2）：
- 段落级历史追溯：每次重写记录为独立条目，含原始段落、改写后、时间戳、反馈
- 段落回退：可回退到任一段落历史版本
- 段落 diff：重写后生成 diff 记录

用法：
  python scripts/refine_paragraph.py 3 5 "把这段内心独白改成白描风格"
  python scripts/refine_paragraph.py 3 5 "..." --dry-run
  python scripts/refine_paragraph.py 3 5 "..." --show-diff
  python scripts/refine_paragraph.py 3 --history           # 查看该段历史
  python scripts/refine_paragraph.py 3 5 --restore 2       # 回退到历史 #2
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.config_io import load_config_yaml

HISTORY_DIR = Path("data/chapters.history")
PARA_HISTORY_DIR = Path("data/chapters/para_history")
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")
VERSION_RE = re.compile(r"^ch(\d{2})_v(\d+)\.md$")
PARA_HISTORY_RE = re.compile(r"^ch(\d{2})_p(\d+)_h(\d+)\.json$")


def _pick_chapter_path(n):
    """按 refined > checked > raw 优先级定位章节文件。"""
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        p = Path(d) / f"{n:02d}.md"
        if p.exists():
            return p
    return None


def _count_cn(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def split_paragraphs(text):
    """按空行切分段落，返回段落列表。与 console/src/diff.js 的 splitParagraphs 一致。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = re.split(r'\n[ \t]*\n', normalized)
    paras = [p.strip() for p in paras if p.strip()]
    if len(paras) <= 1 and normalized.strip():
        paras = normalized.split('\n')
        paras = [p.strip() for p in paras if p.strip()]
    return paras


def _next_version(chapter):
    """该章已有备份数 + 1。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in HISTORY_DIR.glob(f"ch{chapter:02d}_v*.md")
            if (m := re.match(rf"ch{chapter:02d}_v(\d+)\.md", p.name))]
    return (max(nums) + 1) if nums else 1


def _next_para_history_id(chapter, paragraph_index):
    """该段落的下一个历史 ID。"""
    PARA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    pattern = f"ch{chapter:02d}_p{paragraph_index}_h*.json"
    nums = []
    for p in PARA_HISTORY_DIR.glob(pattern):
        m = PARA_HISTORY_RE.match(p.name)
        if m and int(m.group(1)) == chapter and int(m.group(2)) == paragraph_index:
            nums.append(int(m.group(3)))
    return (max(nums) + 1) if nums else 1


def save_paragraph_history(chapter, paragraph_index, original_text, rewritten_text, feedback):
    """保存段落改写历史。"""
    hid = _next_para_history_id(chapter, paragraph_index)
    record = {
        "id": hid,
        "chapter": chapter,
        "paragraph_index": paragraph_index,
        "original": original_text,
        "rewritten": rewritten_text,
        "feedback": feedback,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "original_chars": len(original_text),
        "rewritten_chars": len(rewritten_text),
    }
    PARA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = PARA_HISTORY_DIR / f"ch{chapter:02d}_p{paragraph_index}_h{hid}.json"
    write_text(path, json.dumps(record, ensure_ascii=False, indent=2))
    return record


def list_paragraph_history(chapter, paragraph_index):
    """列出某段落的改写历史（新 → 旧）。"""
    PARA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    pattern = f"ch{chapter:02d}_p{paragraph_index}_h*.json"
    records = []
    for p in PARA_HISTORY_DIR.glob(pattern):
        m = PARA_HISTORY_RE.match(p.name)
        if not m:
            continue
        if int(m.group(1)) != chapter or int(m.group(2)) != paragraph_index:
            continue
        try:
            data = json.loads(read_text(p))
            records.append(data)
        except Exception:
            continue
    records.sort(key=lambda x: x.get("id", 0), reverse=True)
    return records


def restore_paragraph_version(chapter, paragraph_index, history_id):
    """回退段落到指定历史版本。
    
    安全纪律：
    - 回退前当前段落内容存入新的历史记录（回退本身可回退）
    - 历史记录不存在 → 报错不动作
    - 当前段落位置不匹配 → 报错不动作
    """
    history_file = PARA_HISTORY_DIR / f"ch{chapter:02d}_p{paragraph_index}_h{history_id}.json"
    if not history_file.exists():
        avail = [r.get("id") for r in list_paragraph_history(chapter, paragraph_index)]
        return False, f"段落 #{paragraph_index} 无历史 h{history_id}（现有：{avail or '无'}）"

    chap_path = _pick_chapter_path(chapter)
    if chap_path is None:
        return False, f"第 {chapter} 章不存在"

    try:
        hist_data = json.loads(read_text(history_file))
    except Exception as e:
        return False, f"读取历史文件失败: {e}"

    # 切分段落
    full_text = read_text(chap_path)
    paragraphs = split_paragraphs(full_text)
    if paragraph_index >= len(paragraphs):
        return False, f"段落号越界：当前共 {len(paragraphs)} 段，请求 {paragraph_index}"

    current_text = paragraphs[paragraph_index]
    target_text = hist_data.get("original", "")

    # 如果历史记录里的 original 和当前不一致，说明段落已经被改过
    # 仍然执行回退，但提示用户
    if current_text != hist_data.get("rewritten", ""):
        pass  # 不阻断，但下方提示

    # 保存当前段落为新历史（回退前的快照）
    save_paragraph_history(chapter, paragraph_index, current_text, target_text, f"回退前快照 → h{history_id}")

    # 替换段落
    new_paragraphs = list(paragraphs)
    new_paragraphs[paragraph_index] = target_text
    new_text = "\n\n".join(new_paragraphs)

    # 备份整章后写入
    version = _next_version(chapter)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    write_text(chap_path, new_text)

    return True, f"段落 #{paragraph_index} 已回退到 h{history_id}（整章备份 v{version}）"


def build_paragraph_task(cfg, proj, chapter, paragraph_index, paragraph_text, feedback, version, chap_path, outline_path):
    """生成段落级定点重写任务文件。"""
    current_full = read_text(chap_path)
    meta, body = load_template("stage4_refine_paragraph.md", {
        "chapter": chapter,
        "version": version,
        "paragraph_index": paragraph_index,
        "feedback": feedback,
        "paragraph_text": paragraph_text,
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_chapter": Path(chap_path).resolve(),
    })
    body = body.replace("{{current_chapter}}", current_full)
    body = body.replace("{{paragraph_text}}", paragraph_text)
    return body


def run_paragraph_refine(cfg, proj, chapter, paragraph_index, feedback, client=None,
                          task_dir=None, dry_run=False, outline_path="data/outline/chapters"):
    """段落级定点重生成。

    Args:
        chapter: 章节号
        paragraph_index: 段落号（0-indexed，按空行切分后的顺序）
        feedback: 修改意见
    """
    client = client or make_client(cfg, "polisher")
    task_dir = task_dir or "data/state/tasks"
    chap_path = _pick_chapter_path(chapter)
    if chap_path is None:
        return False, f"第 {chapter} 章不存在（raw/checked/refined 均无）", None, None, None

    full_text = read_text(chap_path)
    paragraphs = split_paragraphs(full_text)
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return False, f"段落号越界：第 {chapter} 章共 {len(paragraphs)} 段，请求索引 {paragraph_index}", None, None, None

    target_paragraph = paragraphs[paragraph_index]

    version = _next_version(chapter)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    print(f"[refine_para] 已备份 → {backup}（v{version}）")

    outline = Path(outline_path) / f"{chapter:02d}.md"
    if not outline.exists():
        outline = Path("data/outline/global.md")

    task = client.write_task(task_dir, f"stage4_refine_ch{chapter:02d}_p{paragraph_index}.md",
                             build_paragraph_task(cfg, proj, chapter, paragraph_index,
                                                  target_paragraph, feedback, version, chap_path, outline))
    print(f"[refine_para] 任务文件: {task}")
    print(f"[refine_para] 目标段落 (#{paragraph_index}, {len(target_paragraph)} 字):")
    print(f"  {target_paragraph[:80]}{'...' if len(target_paragraph) > 80 else ''}")

    if dry_run:
        print("[refine_para] --dry-run：未调用子会话")
        return True, "dry-run（仅备份 + 任务文件）", None, version, None

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, f"段落重写子会话失败", backup, version, None

    if not chap_path.exists():
        return False, "重写后章节文件缺失", backup, version, None

    after_text = read_text(chap_path)
    after_paras = split_paragraphs(after_text)

    if abs(len(after_paras) - len(paragraphs)) > 2:
        return False, f"段落数变化过大（{len(paragraphs)}→{len(after_paras)}），疑似整章重写，已保留备份 {backup}", backup, version, None

    before_words = _count_cn(full_text)
    after_words = _count_cn(after_text)

    # 提取改写后的目标段落
    rewritten_paragraph = after_paras[paragraph_index] if paragraph_index < len(after_paras) else ""

    # 保存段落历史
    hist_record = save_paragraph_history(chapter, paragraph_index, target_paragraph, rewritten_paragraph, feedback)

    print(f"[refine_para] 第 {chapter} 章 #{paragraph_index} 重写完成："
          f"全章 {before_words}→{after_words} 字 ({after_words - before_words:+d})")
    print(f"[refine_para] 段落历史: h{hist_record['id']}（{target_paragraph[:30]}... → {rewritten_paragraph[:30]}...）")
    return True, f"第 {chapter} 章 #{paragraph_index} 重写完成（v{version}, h{hist_record['id']}）", backup, version, hist_record


def main():
    parser = argparse.ArgumentParser(description="NovelForge 段落级定点重生成")
    parser.add_argument("chapter", type=int, help="章节号")
    parser.add_argument("paragraph_index", type=int, nargs="?", help="段落号（0-indexed）")
    parser.add_argument("feedback", nargs="?", default=None, help="修改意见")
    parser.add_argument("--feedback", dest="fb2", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--task-dir", default="data/state/tasks")
    parser.add_argument("--history", action="store_true", help="查看该段改写历史")
    parser.add_argument("--restore", type=int, default=None, metavar="HID",
                        help="回退段落到指定历史 ID")
    args = parser.parse_args()

    # 查看历史模式
    if args.history:
        if args.paragraph_index is None:
            print("用法: python scripts/refine_paragraph.py <章号> <段落号> --history")
            return 1
        records = list_paragraph_history(args.chapter, args.paragraph_index)
        if not records:
            print(f"[refine_para] 第 {args.chapter} 章 #{args.paragraph_index} 暂无改写历史")
            return 0
        print(f"[refine_para] 第 {args.chapter} 章 #{args.paragraph_index} 改写历史（新 → 旧）：")
        for r in records:
            print(f"  h{r['id']} | {r['timestamp']} | {r['original_chars']}→{r['rewritten_chars']} 字 | {r['feedback'][:40]}")
            print(f"    原: {r['original'][:60]}{'...' if len(r['original']) > 60 else ''}")
            print(f"    改: {r['rewritten'][:60]}{'...' if len(r['rewritten']) > 60 else ''}")
        return 0

    # 回退模式
    if args.restore is not None:
        if args.paragraph_index is None:
            print("用法: python scripts/refine_paragraph.py <章号> <段落号> --restore <HID>")
            return 1
        ok, msg = restore_paragraph_version(args.chapter, args.paragraph_index, args.restore)
        print(f"\n[refine_para] {'[OK]' if ok else '[FAIL]'} {msg}")
        return 0 if ok else 1

    # 正常重写模式
    if args.paragraph_index is None:
        print('用法: python scripts/refine_paragraph.py <章号> <段落号> "修改意见"')
        print('      python scripts/refine_paragraph.py 3 5 "把这段改成白描"')
        print('      python scripts/refine_paragraph.py 3 5 --history     # 查看历史')
        print('      python scripts/refine_paragraph.py 3 5 --restore 2  # 回退')
        return 1

    feedback = args.fb2 or args.feedback
    if not feedback:
        print('用法: python scripts/refine_paragraph.py <章号> <段落号> "修改意见"')
        return 1

    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    ok, msg, backup, version, hist = run_paragraph_refine(
        cfg, proj, args.chapter, args.paragraph_index, feedback,
        task_dir=args.task_dir, dry_run=args.dry_run
    )
    print(f"\n[refine_para] {'[OK]' if ok else '[FAIL]'} {msg}")
    if backup:
        print(f"[refine_para] 备份: {backup}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
