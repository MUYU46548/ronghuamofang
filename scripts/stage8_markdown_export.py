# -*- coding: utf-8 -*-
"""阶段 8：Markdown 分卷导出（P3 多平台发布）。

事务性导出：先写临时目录 → 校验 → 原子提交，失败不留半成品。
"""
import argparse
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from utils.file_io import read_text, write_text

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def datnow():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def get_available_chapters():
    for d in ["data/chapters/refined", "data/chapters/checked", "data/chapters/raw"]:
        files = sorted(Path(d).glob("*.md"))
        if files:
            return files
    return []


def split_into_volumes(total, per_vol):
    volumes = []
    for i in range(0, total, per_vol):
        end = min(i + per_vol, total)
        volumes.append((i, end))
    return volumes


def build_volume_markdown(chapters, vol_idx, book_name):
    start_n = chapters[0].stem
    end_n = chapters[-1].stem
    lines = [
        f"# {book_name}（第{vol_idx + 1}卷 · 第{start_n}-{end_n}章）",
        "",
        f"> 导出时间：{datnow()}",
        f"> 章节范围：第 {start_n} 章 — 第 {end_n} 章（共 {len(chapters)} 章）",
        "",
        "---",
        "",
        "## 本卷目录",
        "",
    ]
    for f in chapters:
        text = read_text(f)
        first_line = text.split("\n")[0] if text else f.stem
        title = first_line.lstrip("#").strip() if first_line.startswith("#") else f.stem
        lines.append(f"- 第 {f.stem} 章 {title}")
    lines.extend(["", "---", ""])
    for f in chapters:
        text = read_text(f)
        text = COMMENT_RE.sub("", text).strip()
        if text:
            lines.append(text)
            lines.append("")
            lines.append("---")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_full_markdown(chapters, book_name):
    lines = [
        f"# {book_name}",
        "",
        f"> 导出时间：{datnow()}",
        f"> 章节数：{len(chapters)} 章",
        "",
        "---",
        "",
    ]
    for f in chapters:
        text = read_text(f)
        text = COMMENT_RE.sub("", text).strip()
        if text:
            lines.append(text)
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def validate_export(tmp_dir, chapters, volumes):
    """校验临时目录中的导出产物完整性。返回 (ok, errors)"""
    errors = []
    tmp = Path(tmp_dir)
    
    # 检查全书文件
    full_md = tmp / "全书.md"
    if not full_md.exists():
        errors.append("全书.md 未生成")
    elif full_md.stat().st_size == 0:
        errors.append("全书.md 为空")
    
    # 检查各卷
    for vol_idx, (start, end) in enumerate(volumes):
        vol_chapters = chapters[start:end]
        vol_dir = tmp / f"卷{vol_idx + 1:02d}_章节{vol_chapters[0].stem}-{vol_chapters[-1].stem}"
        if not vol_dir.exists():
            errors.append(f"卷{vol_idx + 1} 目录缺失: {vol_dir.name}")
            continue
        vol_md = vol_dir / f"卷{vol_idx + 1:02d}.md"
        if not vol_md.exists():
            errors.append(f"卷{vol_idx + 1} 文件缺失: {vol_md.name}")
        elif vol_md.stat().st_size == 0:
            errors.append(f"卷{vol_idx + 1} 文件为空: {vol_md.name}")
        readme = vol_dir / "README.md"
        if not readme.exists():
            errors.append(f"卷{vol_idx + 1} README 缺失")
    
    return len(errors) == 0, errors


def export_markdown(book_name="未命名", chapters_per_vol=5, out_dir=None):
    """
    事务性导出：
    1. 写临时目录
    2. 校验完整性
    3. 原子提交（shutil.move）
    失败时清理临时目录，不留半成品。
    """
    chapters = get_available_chapters()
    if not chapters:
        return False, "无可用章节", None

    out = Path(out_dir) if out_dir else Path("output") / f"{book_name}_markdown"
    
    # 创建临时目录
    tmp_dir = Path(tempfile.mkdtemp(prefix="nf_export_"))
    try:
        # 阶段 1: 写临时目录
        full_md = build_full_markdown(chapters, book_name)
        write_text(tmp_dir / "全书.md", full_md)

        volumes = split_into_volumes(len(chapters), chapters_per_vol)
        for vol_idx, (start, end) in enumerate(volumes):
            vol_chapters = chapters[start:end]
            vol_dir = tmp_dir / f"卷{vol_idx + 1:02d}_章节{vol_chapters[0].stem}-{vol_chapters[-1].stem}"
            vol_dir.mkdir(parents=True, exist_ok=True)

            vol_md = build_volume_markdown(vol_chapters, vol_idx, book_name)
            write_text(vol_dir / f"卷{vol_idx + 1:02d}.md", vol_md)

            readme = f"# 第{vol_idx + 1}卷\n\n"
            readme += f"- 所属书目：{book_name}\n"
            readme += f"- 章节数量：{len(vol_chapters)} 章\n"
            readme += f"- 导出时间：{datnow()}\n"
            write_text(vol_dir / "README.md", readme)

        # 阶段 2: 校验
        ok, errors = validate_export(tmp_dir, chapters, volumes)
        if not ok:
            return False, f"校验失败: {'; '.join(errors)}", None

        # 阶段 3: 原子提交
        # 如果目标目录已存在，先备份
        backup_dir = None
        if out.exists():
            backup_dir = out.with_suffix(".bak")
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.move(str(out), str(backup_dir))
        
        # 创建父目录并 move
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_dir), str(out))
        
        # 成功后清理备份
        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir)
        
        return True, f"已导出 {len(chapters)} 章（{len(volumes)} 卷）→ {out}", str(out)
    
    except Exception as e:
        # 失败时清理临时目录
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        return False, f"导出失败: {str(e)}", None


def run_stage(cfg, proj, progress, db=None, cost=None, client=None, task_dir=None, run_id=None):
    """兼容 orchestrator 接口的入口。"""
    book_name = (proj or {}).get("book", {}).get("name", "未命名")
    chapters_per_vol = int((cfg or {}).get("chapter", {}).get("batch_outline", 5))
    ok, msg, path = export_markdown(book_name, chapters_per_vol)
    return ok, msg


def main():
    parser = argparse.ArgumentParser(description="Markdown 分卷导出")
    parser.add_argument("--per-vol", type=int, default=5, help="每卷章节数")
    args = parser.parse_args()

    import yaml
    proj = yaml.safe_load(read_text("config/project.yaml"))
    book_name = proj.get("book", {}).get("name", "未命名")

    ok, msg, path = export_markdown(book_name, args.per_vol)
    print(msg)
    print(f"输出: {path}" if path else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
