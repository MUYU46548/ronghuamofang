# -*- coding: utf-8 -*-
"""章节校验：标题格式 / 字数 / 段落空行 / 占位符 / 复杂元素 / 质量自评分。

对应架构文档 v2 4.1 阶段4校验点与 4.7 自评分解析。
"""
import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from utils.file_io import read_text

TITLE_RE = re.compile(r"^##\s*第\s*(\d+)\s*章.*$", re.MULTILINE)
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d{1,2})\s*/\s*10\s*-->", re.IGNORECASE)
# 复杂元素（v2 4.6 降级策略要求生成阶段禁止出现）
COMPLEX_PATTERNS = [
    (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), "Markdown 表格"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "Markdown 图片"),
    (re.compile(r"^```"), "代码块"),
]
PLACEHOLDERS = ("XXX", "TODO", "待补充", "此处插入", "{{", "}}")


@dataclass
class ChapterCheck:
    path: str
    ok: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    word_count: int = 0
    chapter_no: int = None
    title: str = None
    quality: int = None

    def summary(self):
        return (f"[{'PASS' if self.ok else 'FAIL'}] 第{self.chapter_no or '?'}章 "
                f"{self.title or ''} 字数={self.word_count} quality={self.quality or '-'}"
                + (f" 错误: {len(self.errors)}" if self.errors else ""))


def count_cn_words(text):
    """粗略字数统计：去除空白与 Markdown 符号后的字符数（含中英文）。"""
    stripped = re.sub(r"[#*`>_|\[\]()!\-]", "", text)
    stripped = re.sub(r"\s+", "", stripped)
    return len(stripped)


def is_chapter_complete(path, target_min=1200, target_max=3500):
    """检查章节是否真正完成（非空、无截断、字数达标、无占位符、标题格式正确）。

    与 check_chapter() 互补：check_chapter 返回详细报告，
    is_chapter_complete 只做布尔判定，供断点续跑筛选使用。
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        text = read_text(path).strip()
    except Exception:
        return False
    if not text:
        return False
    # 检查截断标记
    if text.endswith("...") or text.endswith("……"):
        return False
    # 检查字数
    words = count_cn_words(text)
    if words < target_min or words > target_max:
        return False
    # 检查占位符
    for ph in PLACEHOLDERS:
        if ph in text:
            return False
    # 检查标题格式
    if not TITLE_RE.search(text):
        return False
    return True


def check_chapter(path, min_words=2000, max_words=3000):
    """校验单章文件，返回 ChapterCheck。"""
    text = read_text(path)
    result = ChapterCheck(path=str(path))

    title_m = TITLE_RE.search(text)
    if not title_m:
        result.errors.append("缺少标题：需为 `## 第X章 章节名`")
    else:
        result.chapter_no = int(title_m.group(1))
        result.title = title_m.group(0).lstrip("# ")

    result.word_count = count_cn_words(text)
    if not (min_words <= result.word_count <= max_words):
        result.errors.append(f"字数 {result.word_count} 不在 [{min_words}, {max_words}] 区间")

    # 段落空行检查：正文连续两段之间需空行
    body = QUALITY_RE.sub("", text)
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for i in range(1, len(lines)):
        if lines[i].strip() and lines[i - 1].strip():
            result.warnings.append(f"第 {i + 1} 行与上一段之间缺空行")
            break  # 只报一次

    for pat, name in COMPLEX_PATTERNS:
        if pat.search(text):
            result.errors.append(f"含复杂元素：{name}（应降级为文本描述，见 v2 4.6）")

    for ph in PLACEHOLDERS:
        if ph in text:
            result.errors.append(f"含占位符：{ph}")

    qm = QUALITY_RE.search(text)
    if qm:
        result.quality = int(qm.group(1))
    else:
        result.warnings.append("缺少质量自评分注释 <!-- quality: X/10 -->")

    result.ok = not result.errors
    return result


def main():
    parser = argparse.ArgumentParser(description="NovelForge 章节校验")
    parser.add_argument("target", help="单章文件或章节目录")
    parser.add_argument("--min", type=int, default=2000)
    parser.add_argument("--max", type=int, default=3000)
    args = parser.parse_args()

    target = Path(args.target)
    files = [target] if target.is_file() else sorted(target.glob("*.md"))
    results = [check_chapter(f, args.min, args.max) for f in files]
    for r in results:
        print(r.summary())
    print(f"\n合计: {sum(r.ok for r in results)}/{len(results)} 通过")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
