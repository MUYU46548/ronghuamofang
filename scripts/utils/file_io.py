# -*- coding: utf-8 -*-
"""NovelForge 文件读写抽象层。

提供编码回退链读取（兼容 UTF-8 BOM / CRLF / GBK 旧文件，参考
obsidian-automation 踩坑经验）与统一换行写入。
"""
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16")


def read_text(path, fallback_encodings=ENCODINGS):
    """按编码回退链读取文本文件，全部失败则抛出最后的 UnicodeError。"""
    p = Path(path)
    last_err = None
    for enc in fallback_encodings:
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
    raise last_err


def write_text(path, content, newline="\n", atomic=True):
    """UTF-8 写入；统一 LF 换行。atomic=True 时先写 .tmp 再 rename，防断电半写。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8", newline=newline)
        tmp.replace(p)
    else:
        p.write_text(content, encoding="utf-8", newline=newline)


def append_text(path, content, newline="\n"):
    """UTF-8 追加写入（统一 LF 换行）；文件不存在时创建，父目录自动建立。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline=newline) as f:
        f.write(content)


def ensure_dir(path):
    """确保目录存在并返回 Path 对象。"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
