# -*- coding: utf-8 -*-
"""config/project.yaml 的定向读写（保留注释）。

支持的字段：
  book.style_notes —— 用户手写的风格笔记
  book.word_template —— Word 模板路径
  materials.scraps_dir —— 碎片目录
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
    pad = " " * (indent + 2)
    body = (notes or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        return ['""']
    lines = [ln.rstrip() for ln in body.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) == 1:
        quoted = lines[0].replace("\\", "\\\\").replace('"', '\\"')
        return ['"' + quoted + '"']
    return ["|"] + [(pad + ln if ln.strip() else "") for ln in lines]


def _find_book_block(lines):
    start = -1
    for i, ln in enumerate(lines):
        if _BOOK_RE.match(ln):
            start = i
            break
    if start < 0:
        return -1, -1
    end = start + 1
    indent = _indent_of(lines[start])
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.startswith(" " * (indent + 1)) and not line.startswith("\t"):
            break
        end += 1
    return start, end


def set_style_notes(notes):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    src = read_text(PROJECT_YAML)
    shutil.copy2(PROJECT_YAML, BACKUP_DIR / f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml")
    lines = src.split("\n")
    out = []
    done = False
    for line in lines:
        if not done and _KEY_RE.match(line):
            m = _KEY_RE.match(line)
            indent = len(m.group(1))
            for r in _render_value(notes, indent):
                out.append(" " * indent + r if r else "")
            done = True
        else:
            out.append(line)
    if not done:
        start, end = _find_book_block(lines)
        if start >= 0:
            pad = " " * 4
            out = lines[:end] + [pad + "style_notes: " + _render_value(notes, 4)[0]] + lines[end:]
        else:
            out.append("style_notes: " + _render_value(notes, 0)[0])
    write_text(PROJECT_YAML, "\n".join(out))
    return True, "已更新 style_notes"


def get_project_config():
    return yaml.safe_load(read_text(PROJECT_YAML))
