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
    # |-（strip）而不是 |：块字面量会把末尾换行也读进值里，
    # 导致「写入 A\nB → 回读 A\nB\n」不一致（2026-09-15 自检抓到）
    return ["|-"] + [(pad + ln if ln.strip() else "") for ln in lines]


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
    """写入 book.style_notes。

    2026-09-15 重写：原实现复用 _render_value 时对**多行**笔记逐行加缩进，会写出
    `  |`（丢掉 `style_notes:` 键名）+ 更深的续行 → project.yaml 直接解析失败
    （整条管线都会读不出配置）。现在统一走 set_book_fields 的块标量渲染与回读校验。
    """
    return set_book_fields({"style_notes": notes})


def get_project_config():
    return yaml.safe_load(read_text(PROJECT_YAML))


def get_style_notes():
    """读取 book.style_notes（缺失/为空/结构异常一律返回空串）。

    2026-09-15 修：nf_api 的 GET /config/style_notes 一直在 import 这个函数，
    但它从未存在过 → 控制台一打开「设置」页签就弹
    `ImportError: cannot import name 'get_style_notes' from 'utils.project_config'`。
    """
    try:
        cfg = get_project_config() or {}
    except Exception:
        return ""
    book = cfg.get("book")
    if not isinstance(book, dict):
        return ""
    notes = book.get("style_notes")
    if notes is None:
        return ""
    return notes if isinstance(notes, str) else str(notes)


# book: 块里可由 GUI 定向改写的标量字段（含注释保留）
BOOK_FIELDS = ("name", "genre", "chapters", "target_words", "style", "author",
               "language", "user_outline", "style_notes", "style_reference",
               "word_template")


def _book_key_re(key):
    return re.compile(r"^(\s*)" + re.escape(key) + r"\s*:(.*)$")


def _render_scalar(value, indent):
    """渲染单个 YAML 标量：整数不加引号（加了会被读回成字符串），字符串走 _render_value。"""
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, int):
        return [str(value)]
    return _render_value(str(value), indent)


def _eat_old_value(lines, i, end, indent, block_scalar):
    """跳过被替换键的旧值续行，返回新的下标。

    规则（保守，宁可少吃不误吃）：
      · 块标量（值以 | / > 开头）的续行可以是任意更深缩进的非空行
      · 普通值的续行只可能是更深缩进的**非注释**非空行（缩进注释属于文档，不能吃）
      · 空行只在块标量内部算续行，块外的空行是段落分隔，必须保留
    """
    while i < end:
        ln = lines[i]
        if not ln.strip():
            if not block_scalar:
                break
            i += 1
            continue
        if ln.lstrip().startswith("#"):
            break
        if _indent_of(ln) > indent:
            i += 1
            continue
        break
    return i


def set_book_fields(fields):
    """定向改写 config/project.yaml 的 book 块字段（保留注释与其它内容）。

    fields: {字段名: 值}，None 表示不修改；值里的换行按 YAML 块标量（|）渲染。
    返回 (ok: bool, msg: str)。写后回读校验（写坏就报错，不留半成品）。
    """
    unknown = [k for k in fields if k not in BOOK_FIELDS]
    if unknown:
        return False, "不支持的字段: " + "、".join(unknown)
    todo = {k: v for k, v in fields.items() if v is not None}
    if not todo:
        return False, "没有要修改的字段"
    if "name" in todo and not str(todo["name"]).strip():
        return False, "书名不能为空"
    if "chapters" in todo:
        try:
            n = int(todo["chapters"])
        except (TypeError, ValueError):
            return False, "章节数必须是整数"
        if not (1 <= n <= 999):
            return False, "章节数须在 1-999 之间"
        todo["chapters"] = n

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    src = read_text(PROJECT_YAML)
    shutil.copy2(PROJECT_YAML, BACKUP_DIR / f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml")
    lines = src.split("\n")
    start, end = _find_book_block(lines)

    applied, missed = [], []
    for key, value in todo.items():
        key_re = _book_key_re(key)
        if start >= 0:
            out, done = [], False
            i = start
            while i < end:
                m = key_re.match(lines[i])
                if m and not done:
                    indent = len(m.group(1))
                    old_val = m.group(2).strip()
                    block_scalar = old_val.startswith("|") or old_val.startswith(">")
                    rendered = _render_scalar(value, indent)
                    out.append(" " * indent + key + ": " + rendered[0])
                    out.extend(rendered[1:])
                    i = _eat_old_value(lines, i + 1, end, indent, block_scalar)
                    done = True
                    continue
                out.append(lines[i])
                i += 1
            if done:
                lines = lines[:start] + out + lines[end:]
                end = start + len(out)
                applied.append(key)
                continue
            # 块内没有这个键 → 插到块尾
            pad = " " * 4
            rendered = _render_scalar(value, 4)
            new_lines = [pad + key + ": " + rendered[0]] + rendered[1:]
            lines = lines[:end] + new_lines + lines[end:]
            end += len(new_lines)
            applied.append(key)
        else:
            # 连 book: 块都没有 → 在文件末尾补一个
            rendered = _render_scalar(value, 0)
            lines = lines + ["", "book:", "  " + key + ": " + rendered[0]] + rendered[1:]
            applied.append(key)
            missed.append("book 块缺失，已在文件末尾补建")

    text = "\n".join(lines)
    write_text(PROJECT_YAML, text)
    # 写后回读校验：YAML 必须还能解析，且字段真落地
    try:
        back = yaml.safe_load(read_text(PROJECT_YAML)) or {}
        book = back.get("book") or {}
    except Exception as e:
        return False, "写入后 YAML 解析失败（已备份，请检查 config/project.yaml）: " + str(e)[:120]
    bad = [k for k in applied if isinstance(todo[k], int) and book.get(k) != todo[k]]
    bad += [k for k in applied if isinstance(todo[k], str) and todo[k].strip()
            and str(book.get(k) or "").strip() != todo[k].strip()]
    if bad:
        return False, "写入后回读不一致: " + "、".join(bad)
    return True, "已更新 " + "、".join(applied) + ("；" + "；".join(missed) if missed else "")
