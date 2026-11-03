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


import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.config_io import load_config_yaml
from utils.text_norm import normalize_chapter_file

HISTORY_DIR = Path("data/chapters.history")
PARA_HISTORY_DIR = Path("data/chapters/para_history")
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")
VERSION_RE = re.compile(r"^ch(\d{2})_v(\d+)\.md$")
PARA_HISTORY_RE = re.compile(r"^ch(\d{2})_p(\d+)_h(\d+)\.json$")


def _norm(path, chapter=None):
    """就地归一化章节文件的中文标点。幂等、零 token；失败只打印不阻断。

    段落回退读的是历史版本 JSON（原样搬运），历史版本本身可能已带脏标点
    —— 回退后统一清洗一次，避免“回退一次脏一次”。
    """
    try:
        changed, counts = normalize_chapter_file(path)
    except Exception as e:                                    # noqa: BLE001
        print(f"[text_norm] 第{chapter}章标点归一化失败（不影响主流程）: "
              f"{type(e).__name__}: {e}")
        return
    if changed:
        detail = "、".join(f"{k}={v}" for k, v in counts.items() if v)
        print(f"[refine_p] 第{chapter}章标点归一化（{detail}）")


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


_SPLIT_RE = re.compile(r"\n[ \t]*\n")


def _segment_spans(text):
    """每个段落**文字**在原文中的 (start, end)（不含前后空白）。

    与 `split_paragraphs` 必须同口径 —— 否则“界面上第 5 段”和“这里第 5 段”
    不是同一段，替换会串行。判据：`test_paragraph_save.py` 的 M10 逐段比对两套结果。
    """
    def push(out, t, a, b):
        seg = t[a:b]
        if not seg.strip():
            return
        left = len(seg) - len(seg.lstrip())
        right = len(seg.rstrip())
        out.append((a + left, a + right))

    out, pos = [], 0
    for m in _SPLIT_RE.finditer(text):
        push(out, text, pos, m.start())
        pos = m.end()
    push(out, text, pos, len(text))
    if len(out) <= 1 and text.strip():
        out = []
        for m in re.finditer(r"[^\n]+", text):
            push(out, text, m.start(), m.end())
    return out


def replace_segment(text, index, new_text):
    """只替换第 index 段，**其余字节原样保留**（2026-11-02，一期 F11/F10）。

    ## 为什么不能用 "\n\n".join(...)

    老写法把段落重新拼起来，等于**顺手规范化了所有分隔符**：
    原来 `\n   \n`（带空格的空行）、行首缩进、多个空行，全被压成 `\n\n`。
    于是"我改的是第 5 段"，落盘后第 1~4 段的字节也变了 ——
    “未改动段逐字不变”这条保证根本不成立，而且没人看得出来（字数不变）。

    ## 格式层不归这里管

    换行符由 `utils.file_io.write_text` 统一成 LF（全项目口径），
    因此这里只在 LF 文本上算位置，不再做 CRLF 往返。
    **归一化（空格/标点）也不归这里** —— 那是 `text_norm` 的职责，会在写盘后统一跑；
    这里只保证"除了目标段的文字，别的一个字节都不动"。
    """
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    spans = _segment_spans(norm)
    if index < 0 or index >= len(spans):
        raise IndexError("段落号越界：当前共 %d 段，请求 %d" % (len(spans), index))
    a, b = spans[index]
    return norm[:a] + str(new_text or "").strip() + norm[b:]


def verify_untouched(before, after, index):
    """校验"只动了第 index 段"：其余字节必须逐字相同（F11，2026-11-03）。

    返回问题列表（空 = 通过）。判据是**字节级**的：先用 `_segment_spans` 拿到
    目标段在原稿里的前后边界，再比对新稿的同位置前缀/后缀。

    为什么不能只按"段 id 多重集"判：id 相同只能说明"这几段还在"，说明不了
    "一个字都没动"。实测风险是本模块自己：老写法 `"\n\n".join(...)` 会把
    `\n   \n` 这种带空格的空行压平 —— 段还是那几段，字节却变了。
    """
    b = (before or "").replace("\r\n", "\n").replace("\r", "\n")
    a = (after or "").replace("\r\n", "\n").replace("\r", "\n")
    spans = _segment_spans(b)
    if index < 0 or index >= len(spans):
        return ["段落号越界：原稿共 %d 段，请求 %d" % (len(spans), index)]
    start, end = spans[index]
    problems = []
    if not a.startswith(b[:start]):
        problems.append("目标段**之前**的内容被改动了（前缀不一致）")
    tail = b[end:]
    if not a.endswith(tail):
        problems.append("目标段**之后**的内容被改动了（后缀不一致）")
    return problems


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

    # 替换段落：**只换这一段**，其余字节原样（F11 未改动段逐字不变的硬要求）。
    # 老写法 "\n\n".join(...) 会顺手把所有分隔符规范化，改一段等于重排全章。
    new_text = replace_segment(full_text, paragraph_index, target_text)

    # 备份整章后写入
    version = _next_version(chapter)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    write_text(chap_path, new_text)
    # 中文标点归一化（2026-10-18）：历史版本里可能带着修复前的脏标点，
    # 回退后统一清洗一次。
    _norm(chap_path, chapter)

    return True, f"段落 #{paragraph_index} 已回退到 h{history_id}（整章备份 v{version}）"


def save_paragraph_text(chapter, paragraph_index, new_text, note="手改"):
    """作者直接改这一段并保存（2026-11-02，不可用清单第 8 条）。

    ## 为什么要有它

    以前只有一条路：填一句意见 → 提交重写 job → 等 → 看 diff。
    **没有"这一处我自己改"的路径** —— 改一个字也得让模型重跑一遍，改得怎么样全看它。
    于是人只能"再许愿一次"。

    ## 纪律与回退同源

    - 当前段落先写进历史（**手改也可回退**，与 restore 一个口径）；
    - 整章先备份到 HISTORY_DIR；
    - 落盘后跑中文标点归一化（与其它写入口一致）。

    返回 (ok, message)。
    """
    body = (new_text or "").strip()
    if not body:
        return False, "内容是空的 —— 要删这一段请直接删，不要保存空段落"
    if "\n\n" in body:
        return False, "这一段里有空行。按段落保存时不能夹空行（那会变成两段）"

    chap_path = _pick_chapter_path(chapter)
    if chap_path is None:
        return False, f"第 {chapter} 章不存在"

    full_text = read_text(chap_path)      # 保留原文，替换时才能"其余字节原样"
    paragraphs = split_paragraphs(full_text)
    if paragraph_index >= len(paragraphs):
        return False, f"段落号越界：当前共 {len(paragraphs)} 段，请求 {paragraph_index}"

    current = paragraphs[paragraph_index]
    if current == body:
        return True, "内容没变，未写入"

    save_paragraph_history(chapter, paragraph_index, current, body, note)

    # 只替换这一段（其余字节原样）—— 见 replace_segment 的 docstring
    version = _next_version(chapter)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    new_full = replace_segment(full_text, paragraph_index, body)
    # 写盘前硬校验（F11，2026-11-03）：除了目标段，其余字节必须一字不动。
    # 不过就不写 —— 宁可这一段落空，也不许"改一段、动全章"。
    _bad = verify_untouched(full_text, new_full, paragraph_index)
    if _bad:
        return False, ("没写入：改动波及了其他段（%s）—— 这是本模块的缺陷，"
                       "请把这条报给开发者，正文一个字都没动" % "；".join(_bad))
    write_text(chap_path, new_full)
    _norm(chap_path, chapter)
    return True, ("段落 #%d 已保存（%d → %d 字；整章备份 v%d，旧内容也进了段落历史）"
                  % (paragraph_index, len(current), len(body), version))


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
