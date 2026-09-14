# -*- coding: utf-8 -*-
"""config/project.yaml 的定向读写（保留注释）。

需求背景：project.yaml 里有大量说明性注释，用 yaml.dump 回写会把注释全部冲掉。
本模块只做"按行替换某个键的值"这一件事，其余内容原样保留。

当前支持的字段：
  book.style_notes —— 用户手写的风格笔记（stage4/stage6 注入，见 style_analyzer）

另提供整份配置的只读读取：
  get_project_config() —— 返回 project.yaml 的 dict（GUI 初始化向导 / 项目信息用）
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from utils.file_io import read_text, write_text

ROOT = Path(__file__).resolve().parents[2]
PROJECT_YAML = ROOT / "config" / "project.yaml"
BACKUP_DIR = ROOT / "config" / "history"

_KEY_RE = re.compile(r"^(\s*)style_notes\s*:(.*)$")
_BOOK_RE = re.compile(r"^book\s*:")


def _indent_of(line):
    return len(line) - len(line.lstrip())


def _render_value(notes, indent):
    """渲染 style_notes 的 YAML 值，返回行列表。

    单行 → 双引号标量（转义 \\ 与 "）；多行 → 块标量 |；空 → ""。
    """
    pad = " " * (indent + 2)
    body = (notes or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        return ['""']
    lines = [ln.rstrip() for ln in body.split("\n")]
    # 去掉尾部空行
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) == 1:
        quoted = lines[0].replace("\\", "\\\\").replace('"', '\\"')
        return ['"' + quoted + '"']
    return ["|"] + [(pad + ln if ln.strip() else "") for ln in lines]


def _find_book_block(lines):
    """定位 book: 段的 (起始行, 结束行)。找不到返回 (-1, -1)。"""
    start = -1
    for i, ln in enumerate(lines):
        if _BOOK_RE.match(ln):
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if _indent_of(ln) == 0:
            end = j
            break
    return start, end


def _find_style_notes(lines, b_start, b_end):
    """在 book 段内找 style_notes 键所在行；找不到返回 -1。"""
    for i in range(b_start + 1, b_end):
        m = _KEY_RE.match(lines[i])
        if m and len(m.group(1)) == 2:
            return i
    return -1


def _block_end(lines, idx, indent):
    """计算 style_notes 键值块占用的结束行（不含归属下一键的行）。"""
    end = idx + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() == "":
            # 空行：若后面还有更深缩进的内容，则仍属于本块
            j = end + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and _indent_of(lines[j]) > indent:
                end = j
                continue
            break
        cur = _indent_of(ln)
        if cur > indent:
            end += 1
            continue
        break
    return end


def set_style_notes(notes, path=None, backup=True):
    """写入 book.style_notes，保留文件其余内容与注释。

    返回 (ok: bool, info: str)。backup=True 时先备份到 config/history/。
    """
    p = Path(path) if path else PROJECT_YAML
    try:
        text = read_text(p)
    except Exception as e:
        return False, "读取失败: %s" % e

    lines = text.split("\n")
    b_start, b_end = _find_book_block(lines)
    if b_start < 0:
        return False, "project.yaml 中未找到 book: 段"
    idx = _find_style_notes(lines, b_start, b_end)

    if idx < 0:
        # 字段缺失：插到 book 段末尾（top-level 下一个键之前）
        idx = end = b_end
        indent = 2
    else:
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        end = _block_end(lines, idx, indent)

    block = _render_value(notes, indent)
    pad = " " * indent
    new_lines = [pad + "style_notes: " + block[0]] + block[1:]

    out = lines[:idx] + new_lines + lines[end:]

    if backup:
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = BACKUP_DIR / ("project_%s.yaml" % stamp)
            n = 1
            while bak.exists():      # 同一秒内的多次保存不互相覆盖
                bak = BACKUP_DIR / ("project_%s_%d.yaml" % (stamp, n))
                n += 1
            shutil.copy2(p, bak)
        except Exception:
            bak = None
    else:
        bak = None

    try:
        write_text(p, "\n".join(out))
    except Exception as e:
        return False, "写入失败: %s" % e

    # 回读校验，确保写坏能立刻发现
    try:
        data = yaml.safe_load(read_text(p)) or {}
        got = (data.get("book") or {}).get("style_notes") or ""
    except Exception as e:
        return False, "写入后校验失败（YAML 解析错误）: %s" % e
    if got.strip() != (notes or "").strip():
        return False, "写入后校验不一致（期望 %r，实得 %r）" % (notes, got)

    return True, ("已写入 style_notes" + ("（备份: config/history/%s）" % bak.name if bak else ""))


def get_style_notes(path=None):
    """读取 book.style_notes，读不到返回 ""。"""
    p = Path(path) if path else PROJECT_YAML
    try:
        data = yaml.safe_load(read_text(p)) or {}
    except Exception:
        return ""
    return ((data.get("book") or {}).get("style_notes") or "").strip()


def get_project_config(path=None):
    """读取整份 config/project.yaml 为 dict（读失败返回 {}）。

    供 GUI 使用：初始化向导判断「书名是否还是占位符」、素材空目录提醒、
    以及显示当前项目信息。之前 nf_api 引用了不存在的同名函数，
    导致 GET /config/project 500、POST 同样 500（初始化向导从未生效）。
    """
    p = Path(path) if path else PROJECT_YAML
    try:
        data = yaml.safe_load(read_text(p))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
