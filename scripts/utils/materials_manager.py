# -*- coding: utf-8 -*-
"""素材管理：扫描 materials/raw/ 下的素材文件。

仅做文件系统级扫描 + 元数据查询，不做 LLM 调用。
"""
import hashlib
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Optional

# 支持的素材扩展名（按 kind 分组）
TEXT_EXTS = {".txt", ".md", ".markdown", ".docx", ".doc"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ALL_EXTS = TEXT_EXTS | IMAGE_EXTS


def _detect_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def _md5_of(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


class MaterialsManager:
    """管理 materials/raw/ 下的素材文件。"""

    def __init__(self, raw_dir: Path):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def list_materials(self) -> dict:
        """列出所有素材文件（按文件名排序）。"""
        items = []
        for p in sorted(self.raw_dir.iterdir()):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in ALL_EXTS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            kind = _detect_kind(p)
            items.append({
                "name": p.name,
                "size": st.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "kind": kind,
                "ext": ext,
                "md5": _md5_of(p),
            })
        return {"dir": str(self.raw_dir), "items": items, "count": len(items)}

    def add_from_path(self, src_path: str, overwrite: bool = False) -> dict:
        """从外部路径复制一份素材到 raw/。"""
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError("源文件不存在: " + str(src))
        if src.suffix.lower() not in ALL_EXTS:
            raise ValueError("不支持的文件类型: " + src.suffix)
        dst = self.raw_dir / src.name
        if dst.exists() and not overwrite:
            raise FileExistsError("素材已存在: " + src.name)
        shutil.copy2(src, dst)
        return {"name": src.name, "size": dst.stat().st_size, "path": str(dst)}

    def create_new(self, name: str, content: str = "") -> dict:
        """新建素材文件（文本类）。"""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("非法文件名: " + name)
        ext = Path(name).suffix.lower()
        if ext not in TEXT_EXTS:
            raise ValueError("新建素材仅支持文本类型: " + str(TEXT_EXTS))
        dst = self.raw_dir / name
        dst.write_text(content, encoding="utf-8")
        return {"name": name, "size": dst.stat().st_size, "path": str(dst)}

    def delete(self, name: str) -> dict:
        """删除素材文件（仅 raw/ 副本）。"""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("非法文件名: " + name)
        target = (self.raw_dir / name).resolve()
        if target.parent != self.raw_dir.resolve():
            raise ValueError("路径越界")
        if not target.is_file():
            raise FileNotFoundError("素材不存在: " + name)
        size = target.stat().st_size
        target.unlink()
        return {"name": name, "size": size}

    def read_content(self, name: str, max_bytes: int = 2 * 1024 * 1024) -> str:
        """读取素材文本内容（用于预览/编辑）。"""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("非法文件名: " + name)
        target = (self.raw_dir / name).resolve()
        if target.parent != self.raw_dir.resolve():
            raise ValueError("路径越界")
        if not target.is_file():
            raise FileNotFoundError("素材不存在: " + name)
        ext = target.suffix.lower()
        if ext not in TEXT_EXTS:
            raise ValueError("该文件类型不可读（非文本）: " + ext)
        if target.stat().st_size > max_bytes:
            raise ValueError("文件过大（>%dMB）" % (max_bytes // 1024 // 1024))
        return target.read_text(encoding="utf-8", errors="replace")

    def save_content(self, name: str, content: str) -> dict:
        """保存素材文本内容。"""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("非法文件名: " + name)
        target = (self.raw_dir / name).resolve()
        if target.parent != self.raw_dir.resolve():
            raise ValueError("路径越界")
        if not target.is_file():
            raise FileNotFoundError("素材不存在: " + name)
        ext = target.suffix.lower()
        if ext not in TEXT_EXTS:
            raise ValueError("该文件类型不可写（非文本）: " + ext)
        target.write_text(content, encoding="utf-8")
        return {"name": name, "size": target.stat().st_size}
