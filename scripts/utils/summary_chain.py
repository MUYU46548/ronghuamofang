# -*- coding: utf-8 -*-
"""滚动摘要维护（架构文档 v2 4.4）。

data/summaries/rolling.md 结构：
  # 滚动摘要
  ## 全书摘要        —— 约 500 字，持续压缩更新
  ## 近期章节        —— 最近 N 章详细摘要（每章约 300 字），N = summary_every

本模块负责 append / 压缩信号 / 上一章末尾衔接段提取；
真正的 LLM 摘要生成由 orchestrator 调子会话完成，脚本只做文件结构维护。
"""
import re
from pathlib import Path

from utils.file_io import read_text, write_text

GLOBAL_HEADER = "## 全书摘要"
RECENT_HEADER = "## 近期章节"
CHAPTER_PAT = re.compile(r"^###\s*第\s*(\d+)\s*章\s*$", re.MULTILINE)


def load_rolling(path):
    """解析 rolling.md 为 {global_summary: str, chapters: {no: str}}。"""
    data = {"global_summary": "", "chapters": {}}
    if not Path(path).exists():
        return data
    text = read_text(path)
    m = re.search(GLOBAL_HEADER + r"\s*(.*?)(?=" + RECENT_HEADER + r"|\Z)", text, re.S)
    if m:
        data["global_summary"] = m.group(1).strip()
    sec = re.search(RECENT_HEADER + r"\s*(.*?)\Z", text, re.S)
    if sec:
        blocks = re.split(r"(?=^###\s*第)", sec.group(1), flags=re.M)
        for b in blocks:
            cm = CHAPTER_PAT.match(b.strip().splitlines()[0]) if b.strip() else None
            if cm:
                no = int(cm.group(1))
                body = "\n".join(b.strip().splitlines()[1:]).strip()
                data["chapters"][no] = body
    return data


def write_rolling(path, data):
    parts = ["# 滚动摘要", GLOBAL_HEADER, data.get("global_summary", "") or "（待首次压缩生成）", "", RECENT_HEADER]
    for no in sorted(data.get("chapters", {})):
        parts.append(f"### 第{no}章")
        parts.append(data["chapters"][no])
        parts.append("")
    write_text(path, "\n".join(parts).rstrip() + "\n")


def append_chapter_summary(path, chapter_no, summary, max_recent=15):
    """追加一章摘要；超出窗口时自动压缩最早章为一句概要并入全书摘要。"""
    data = load_rolling(path)
    data["chapters"][chapter_no] = summary.strip()
    need_compress = len(data["chapters"]) > max_recent
    compressed = None
    if need_compress:
        oldest = min(data["chapters"])
        old_summary = data["chapters"].pop(oldest)
        one_liner = f"第{oldest}章: {old_summary[:60]}…" if len(old_summary) > 60 else f"第{oldest}章: {old_summary}"
        data["global_summary"] = f"{data.get('global_summary', '')} {one_liner}".strip()[:500]
        compressed = oldest
    write_rolling(path, data)
    return {"need_compress": need_compress, "oldest_chapter": compressed}


def compress_recent(data, chapter_no, chapter_summary, global_summary):
    """将指定章节从近期区移入全书摘要（压缩结果由 orchestrator 子会话生成后调用）。"""
    data["chapters"].pop(chapter_no, None)
    data["global_summary"] = global_summary.strip()
    return data


def extract_prev_tail(chapter_path, n_paras=4):
    """提取上一章末尾 n 段（按空行分隔的段落），用于章节衔接语气注入。"""
    text = read_text(chapter_path)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # 去质量注释
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras[-n_paras:]
