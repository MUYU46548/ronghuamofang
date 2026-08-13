# -*- coding: utf-8 -*-
"""阶段 1：素材梳理（确定性部分）。

职责（架构文档 v2 4.1）：
- 扫描 materials/raw/ 素材（.txt/.md/.docx），docx 提取文本、其余归一为 UTF-8 Markdown；
- 基础去重：MD5 精确 + difflib 相似度 >0.9（保留最长版本）；
- 输出素材清单 materials_manifest.json 供 LLM 归并设定。

LLM 归并（提取角色/世界观/情节碎片 → setting.json）由 orchestrator 调子会话完成。
PDF 素材 P0 不支持，扫描时警告并跳过。
"""
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

SUPPORTED_EXTS = {".txt", ".md", ".markdown", ".docx"}
DEDUP_SIMILARITY = 0.9


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def normalize_docx(path, out_dir):
    """提取 docx 文本为 Markdown 文件。"""
    from docx import Document
    doc = Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    out = out_dir / (path.stem + ".md")
    write_text(out, "\n\n".join(paras) + "\n")
    return out


def normalize_plain(path, out_dir):
    """txt/md 原文归一为 UTF-8 Markdown（编码回退链）。"""
    content = read_text(path)
    out = out_dir / (path.stem + ".md")
    write_text(out, content)
    return out


def scan_materials(materials_dir, normalized_dir):
    """扫描并归一化素材，返回清单 list[dict]。PDF 警告跳过。"""
    materials_dir = Path(materials_dir)
    normalized_dir = Path(normalized_dir)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for path in sorted(materials_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
            if path.is_file() and path.suffix.lower() == ".pdf":
                print(f"[WARN] PDF 素材暂不支持自动归一化，请手动转文本: {path.name}")
            continue
        try:
            if path.suffix.lower() == ".docx":
                out = normalize_docx(path, normalized_dir)
            else:
                out = normalize_plain(path, normalized_dir)
            manifest.append({
                "source": str(path),
                "normalized": str(out),
                "name": path.stem,
                "words": len(re.sub(r"\s+", "", read_text(out))),
            })
        except Exception as e:
            print(f"[WARN] 归一化失败 {path.name}: {e}")
    return manifest


def dedup(manifest, threshold=DEDUP_SIMILARITY):
    """基础去重：MD5 精确 + difflib 相似。返回 (保留清单, 移除清单)。"""
    by_hash = {}
    for item in manifest:
        by_hash.setdefault(file_md5(item["normalized"]), []).append(item)
    kept, removed = [], []
    for group in by_hash.values():
        if len(group) > 1:
            best = max(group, key=lambda x: x["words"])
            kept.append(best)
            removed.extend(x for x in group if x is not best)
        else:
            kept.append(group[0])
    # 相似度去重（不同 MD5 但内容高度相似）
    kept.sort(key=lambda x: x["words"], reverse=True)
    unique = []
    for item in kept:
        if any(difflib.SequenceMatcher(None,
                read_text(item["normalized"]), read_text(k["normalized"])).ratio() > threshold
               for k in unique):
            removed.append(item)
        else:
            unique.append(item)
    return unique, removed


def build_setting_task(proj, manifest_path, normalized_dir):
    _, body = load_template("stage1_materials.md", {
        "path_manifest": Path(manifest_path).resolve(),
        "path_normalized": Path(normalized_dir).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
    })
    return body


def validate_setting(path):
    """校验 setting.json 四顶层键。返回 (ok, errors)。"""
    try:
        data = json.loads(read_text(path))
    except Exception as e:
        return False, [f"JSON 解析失败: {e}"]
    missing = [k for k in ("characters", "world", "plot_fragments", "timeline") if k not in data]
    if missing:
        return False, [f"缺少顶层键: {missing}"]
    if not isinstance(data.get("characters"), list) or len(data["characters"]) == 0:
        return False, ["characters 为空"]
    return True, []


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or HermesClient()
    task_dir = task_dir or "data/state/tasks"
    materials_dir = proj.get("materials", {}).get("dir", "materials/raw")
    manifest_path = Path("data/setting/materials_manifest.json")
    normalized_dir = "data/setting/normalized"
    setting_path = Path("data/setting/setting.json")

    manifest = scan_materials(materials_dir, normalized_dir)
    if not manifest:
        # 允许无新素材但已有设定集的场景（重跑设定阶段）
        if setting_path.exists():
            ok, errors = validate_setting(setting_path)
            if ok:
                progress.mark_stage_done(1)
                print("[stage1] 无新素材，复用已有设定集")
                return True, "stage1 跳过（无素材，复用设定集）"
        progress.set_stage(1, "failed", error="materials/raw 无素材且无既有设定集")
        return False, "stage1 失败：materials/raw 为空"
    manifest, removed = dedup(manifest)
    write_text(manifest_path, json.dumps(
        {"count": len(manifest), "removed_count": len(removed),
         "materials": manifest, "removed": removed},
        ensure_ascii=False, indent=2))

    if setting_path.exists():
        ok, errors = validate_setting(setting_path)
        if ok:
            progress.mark_stage_done(1)
            print(f"[stage1] 素材归一化 {len(manifest)} 条，设定集已存在，跳过归并")
            return True, "stage1 完成（复用设定集）"
        print(f"[stage1] 既有设定集无效（{errors}），重新归并")

    task = client.write_task(task_dir, "stage1_setting.md",
                             build_setting_task(proj, manifest_path, normalized_dir))
    result = client.run_task(task)
    if result["exit_code"] != 0:
        progress.set_stage(1, "failed", error="子会话退出码非零")
        return False, "stage1 归并子会话失败"
    ok, errors = validate_setting(setting_path)
    if not ok:
        progress.set_stage(1, "failed", error="; ".join(errors))
        return False, "stage1 设定集校验失败: " + "; ".join(errors)

    progress.mark_stage_done(1)
    print(f"[stage1] 素材归一化 {len(manifest)} 条（去重 {len(removed)} 条），设定集生成完成")
    return True, "stage1 完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段1：素材梳理（确定性部分）")
    parser.add_argument("--materials", default="materials/raw")
    parser.add_argument("--normalized", default="data/setting/normalized")
    parser.add_argument("--out", default="data/setting/materials_manifest.json")
    parser.add_argument("--no-dedup", action="store_true", help="跳过相似去重")
    args = parser.parse_args()

    manifest = scan_materials(args.materials, args.normalized)
    removed = []
    if not args.no_dedup:
        manifest, removed = dedup(manifest)
    write_text(args.out, json.dumps(
        {"count": len(manifest), "removed_count": len(removed),
         "materials": manifest, "removed": removed},
        ensure_ascii=False, indent=2))
    print(f"素材归一化: {len(manifest)} 条保留, {len(removed)} 条去重移除")
    print(f"清单输出: {args.out}")
    return 0 if manifest else 1


if __name__ == "__main__":
    raise SystemExit(main())
