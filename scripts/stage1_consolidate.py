# -*- coding: utf-8 -*-
"""阶段 1：素材梳理（确定性部分）。

职责（架构文档 v2 4.1）：
- 扫描 materials/raw/ 素材（.txt/.md/.docx），docx 提取文本、其余归一为 UTF-8 Markdown；
- 基础去重：MD5 精确 + difflib 相似度 >0.9（保留最长版本）；
- 输出素材清单 materials_manifest.json 供 LLM 归并设定。

原始碎片（可选，--scraps）：
- 扫描 materials/original_scraps/（用户自由命名、不进 Git 的私人碎片），
  经 scrap_cluster 做确定性聚类/时间轴/前瞻备忘 → data/setting/scraps_index.json；
- LLM 把碎片炼成结构化信息点 → data/setting/scraps_merge.json → 作为**额外输入**
  喂给既有归并任务（碎片本身不落进 materials/raw/，守"碎片与结构化卡片分离"的纪律）。

LLM 归并（提取角色/世界观/情节碎片 → setting.json）由 orchestrator 调子会话完成。
PDF 素材 P0 不支持，扫描时警告并跳过。
"""
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.scrap_cluster import collect as collect_scraps, write_index as write_scraps_index

SUPPORTED_EXTS = {".txt", ".md", ".markdown", ".docx"}
DEDUP_SIMILARITY = 0.9

SCRAPS_DIR_DEFAULT = "materials/original_scraps"
SCRAPS_INDEX = "data/setting/scraps_index.json"
SCRAPS_MERGE = "data/setting/scraps_merge.json"


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


def manifest_fingerprint(manifest, extra_fingerprints=()):
    """素材清单内容指纹：归一化文件内容的 sha256（与 mtime 无关）。

    用于判断"素材是否有实质变化"——只有内容变化才触发设定集重新归并，
    修复此前"设定集已存在即复用、新素材永远进不了 setting.json"的问题。

    extra_fingerprints 用于把**原始碎片**一并折进指纹：碎片不进 materials/raw/，
    若不计入指纹，用户改了碎片重跑会得到"素材无变化，复用设定集"——功能等于没接线。
    注意必须传碎片**内容**指纹（scrap_cluster 的 content_fingerprint），
    不能传 scraps_index.json 的文件字节：后者含 generated_at 等易变字段，
    会让每次运行都误判为"素材有变化"，把设定集反复重归并（烧钱）。
    """
    h = hashlib.sha256()
    for item in sorted(manifest, key=lambda x: x["source"]):
        try:
            with open(item["normalized"], "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    for fp in extra_fingerprints:
        h.update(b"\x01")
        h.update((fp or "").encode("utf-8"))
    return h.hexdigest()


def _write_manifest(manifest, removed, path, fingerprint=None):
    """写素材清单（带内容指纹）。"""
    write_text(path, json.dumps(
        {"count": len(manifest), "removed_count": len(removed),
         "materials": manifest, "removed": removed,
         "_fingerprint": fingerprint},
        ensure_ascii=False, indent=2))


def _load_prev_fingerprint(manifest_path):
    try:
        prev = json.loads(read_text(manifest_path))
        return prev.get("_fingerprint")
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        return None


def build_setting_task(proj, manifest_path, normalized_dir, extra_inputs=()):
    """构建设定集归并任务。extra_inputs 为额外输入文件（如碎片提炼结果）。"""
    extra = "\n".join("- 碎片提炼结果: %s" % Path(p).resolve() for p in extra_inputs if p)
    _, body = load_template("stage1_materials.md", {
        "path_manifest": Path(manifest_path).resolve(),
        "path_normalized": Path(normalized_dir).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "extra_inputs": extra,
    })
    return body


# --------------------------------------------------------------------------
# 原始碎片（--scraps）：确定性聚类 + LLM 提炼
# --------------------------------------------------------------------------

def prepare_scraps(scraps_dir=SCRAPS_DIR_DEFAULT, index_path=SCRAPS_INDEX):
    """扫描碎片并写索引。返回 (index, batches, warnings)。

    空目录/不存在都返回 (None, [], warnings)：调用方据此完全跳过碎片分支，
    保证 --with-scraps 对"没有碎片"的用户零影响。
    """
    index, warnings = collect_scraps(scraps_dir)
    if not index["stats"]["scrap_count"]:
        return None, [], warnings
    write_scraps_index(index, index_path)
    return index, index["inline_batches"], warnings


def build_scraps_task(index_path, scraps_dir, merge_path, batch=None, batches=None):
    """构建碎片提炼任务。

    单批：用「目录 + 下的 *.md」让 llm_client 整目录内联；
    多批：改用逐文件行（`- 碎片: <绝对路径>`），每批只内联本批文件——
    否则 llm_client 的 220000 字符上限会**静默丢文件**（只打 WARN，不报错）。
    """
    if batch:
        lines = ["- 碎片: %s" % Path(f).resolve() for f in batch["files"]]
        scrap_inputs = "\n".join(lines)
    else:
        scrap_inputs = "- 原始碎片目录: %s/ 下的 *.md" % Path(scraps_dir).resolve()
    _, body = load_template("stage1_scraps.md", {
        "path_index": Path(index_path).resolve(),
        "path_merge": Path(merge_path).resolve(),
        "scrap_inputs": scrap_inputs,
    })
    return body


def validate_scraps_merge(path):
    """校验碎片提炼结果。返回 (ok, errors)。"""
    try:
        data = json.loads(read_text(path))
    except Exception as e:
        return False, ["JSON 解析失败: %s" % e]
    if not isinstance(data, dict):
        return False, ["顶层不是 JSON 对象"]
    if not isinstance(data.get("points"), list):
        return False, ["缺少 points 数组"]
    for key in ("conflicts", "open_questions"):
        if key in data and not isinstance(data[key], list):
            return False, ["%s 必须是数组" % key]
    return True, []


def merge_scraps_batches(paths, out_path=SCRAPS_MERGE):
    """把多批提炼结果合并成一份（Python 侧确定性合并，不再调模型）。

    合并时重新编号 id，避免各批次模型都从 p001 开始导致重号。
    """
    merged = {"points": [], "conflicts": [], "open_questions": [], "batches": []}
    n = 0
    for p in paths:
        try:
            data = json.loads(read_text(p))
        except Exception as e:
            print("[WARN] 批次结果解析失败 %s: %s" % (p, e))
            continue
        merged["batches"].append({"file": str(p),
                                 "points": len(data.get("points") or [])})
        for pt in data.get("points") or []:
            if not isinstance(pt, dict):
                continue
            n += 1
            pt = dict(pt)
            pt["id"] = "p%03d" % n
            merged["points"].append(pt)
        merged["conflicts"].extend(x for x in (data.get("conflicts") or []) if x)
        merged["open_questions"].extend(x for x in (data.get("open_questions") or []) if x)
    write_text(out_path, json.dumps(merged, ensure_ascii=False, indent=2))
    return merged, out_path


def run_scraps_merge(client, task_dir, index_path, scraps_dir, batches,
                     merge_path=SCRAPS_MERGE, cost=None, run_id=None):
    """跑碎片提炼（可能多批）。返回 (ok, msg, merge_path or None)。"""
    multi = len(batches) > 1
    batch_paths = []
    for i, batch in enumerate(batches, 1):
        name = ("stage1_scraps_b%02d.md" % i) if multi else "stage1_scraps.md"
        out = ("data/setting/scraps_merge_b%02d.json" % i) if multi else merge_path
        task = client.write_task(task_dir, name,
                                 build_scraps_task(index_path, scraps_dir, out,
                                                   batch=batch if multi else None))
        print("[stage1] 碎片提炼%s → %s"
              % (("（第 %d/%d 批，%d 个碎片）" % (i, len(batches), len(batch["files"])))
                 if multi else "", task))
        result = client.run_task(task)
        if cost and run_id:
            cost.charge_cost(run_id, 1, 0, result)
        if result["exit_code"] != 0:
            return False, "碎片提炼子会话失败（第 %d 批）" % i, None
        if not Path(out).exists():
            return False, "碎片提炼未产出结果文件：%s" % out, None
        ok, errors = validate_scraps_merge(out)
        if not ok:
            return False, "碎片提炼结果校验失败（第 %d 批）: %s" % (i, "; ".join(errors)), None
        batch_paths.append(out)

    if multi:
        merged, merged_path = merge_scraps_batches(batch_paths, merge_path)
        print("[stage1] 碎片提炼合并 %d 批 → %d 个信息点 → %s"
              % (len(batch_paths), len(merged["points"]), merged_path))
    else:
        merged_path = batch_paths[0]
    return True, "碎片提炼完成（%d 批）" % len(batch_paths), merged_path


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


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None,
              with_scraps=None):
    """阶段1 主流程。

    with_scraps: None=按配置（proj.materials.use_scraps，默认 True）；True/False 强制。
    强制 True 但目录为空时自动退化为无碎片流程，不报错。
    """
    client = client or make_client(cfg, "architect")
    task_dir = task_dir or "data/state/tasks"
    mats_cfg = proj.get("materials", {}) or {}
    materials_dir = mats_cfg.get("dir", "materials/raw")
    scraps_dir = mats_cfg.get("scraps_dir", SCRAPS_DIR_DEFAULT)
    if with_scraps is None:
        with_scraps = bool(mats_cfg.get("use_scraps", True))
    manifest_path = Path("data/setting/materials_manifest.json")
    normalized_dir = "data/setting/normalized"
    setting_path = Path("data/setting/setting.json")

    # ---- 原始碎片（确定性部分，先跑；不参与 materials/raw 归一化）----
    scraps_index, scraps_batches, scraps_merge_path = None, [], None
    if with_scraps:
        scraps_index, scraps_batches, warnings = prepare_scraps(scraps_dir, SCRAPS_INDEX)
        for w in warnings:
            print("[stage1] [碎片] %s" % w)
        if scraps_index:
            st = scraps_index["stats"]
            print("[stage1] 碎片 %d 个 → %d 簇（合并簇 %d）/%d 前瞻备忘 → %s"
                  % (st["scrap_count"], st["cluster_count"], st["merged_cluster_count"],
                     st["lookahead_count"], SCRAPS_INDEX))
        else:
            print("[stage1] 未发现原始碎片（%s），按无碎片流程继续" % scraps_dir)

    manifest = scan_materials(materials_dir, normalized_dir)
    if not manifest and not scraps_index:
        # 允许无新素材但已有设定集的场景（重跑设定阶段）
        if setting_path.exists():
            ok, errors = validate_setting(setting_path)
            if ok:
                progress.mark_stage_done(1)
                print("[stage1] 无新素材，复用已有设定集")
                return True, "stage1 跳过（无素材，复用设定集）"
        progress.set_stage(1, "failed", error="materials/raw 无素材且无既有设定集")
        return False, "stage1 失败：materials/raw 为空"
    if not manifest and scraps_index:
        print("[stage1] materials/raw 无素材卡，仅用原始碎片归并设定集"
              "（建议之后把碎片提炼进 materials/raw/ 再重跑）")

    manifest, removed = dedup(manifest)
    # 碎片内容指纹一并计入：改碎片 → 指纹变 → 触发重归并
    extra_fp = [scraps_index["content_fingerprint"]] if scraps_index else []
    sig = manifest_fingerprint(manifest, extra_fingerprints=extra_fp)
    prev_sig = _load_prev_fingerprint(manifest_path)
    _write_manifest(manifest, removed, manifest_path, sig)
    setting_valid = setting_path.exists() and validate_setting(setting_path)[0]

    if setting_valid and sig == prev_sig:
        # 素材无实质变化 → 复用设定集（此前会无条件复用，新素材进不了 setting.json）
        progress.mark_stage_done(1)
        print(f"[stage1] 素材归一化 {len(manifest)} 条（去重 {len(removed)} 条），素材无变化，复用设定集")
        return True, "stage1 完成（素材无变化，复用设定集）"
    if setting_valid:
        print(f"[stage1] 素材有变化（指纹不一致），重新归并设定集")

    # ---- 碎片提炼（LLM）：结果作为额外输入喂给归并任务 ----
    extra_inputs = []
    if scraps_index and scraps_batches:
        ok_s, msg_s, scraps_merge_path = run_scraps_merge(
            client, task_dir, SCRAPS_INDEX, scraps_dir, scraps_batches,
            merge_path=SCRAPS_MERGE, cost=cost, run_id=run_id)
        print("[stage1] %s" % msg_s)
        if not ok_s:
            progress.set_stage(1, "failed", error=msg_s)
            return False, "stage1 碎片提炼失败: " + msg_s
        if scraps_merge_path and Path(scraps_merge_path).exists():
            extra_inputs.append(scraps_merge_path)
            try:
                merged = json.loads(read_text(scraps_merge_path))
                oq = merged.get("open_questions") or []
                if oq:
                    print("[stage1] 碎片遗留 %d 条待定事项（不当作既成事实）" % len(oq))
            except Exception:
                pass

    task = client.write_task(task_dir, "stage1_setting.md",
                             build_setting_task(proj, manifest_path, normalized_dir,
                                                extra_inputs=extra_inputs))
    result = client.run_task(task)
    if cost and run_id:
        cost.charge_cost(run_id, 1, 0, result)
    if result["exit_code"] != 0:
        progress.set_stage(1, "failed", error="子会话退出码非零")
        return False, "stage1 归并子会话失败"
    ok, errors = validate_setting(setting_path)
    if not ok:
        progress.set_stage(1, "failed", error="; ".join(errors))
        return False, "stage1 设定集校验失败: " + "; ".join(errors)

    # 设定集就绪 → 生成设定库引用索引（materials/vault_links.md）
    vault_links = proj.get("vault", {}).get("vault_links", "materials/vault_links.md")
    try:
        from build_vault_links import build_vault_links
        total, srcs = build_vault_links(setting_path, vault_links)
        print(f"[stage1] 设定库引用索引生成: {total} 条目 / {srcs} 素材 → {vault_links}")
    except Exception as e:
        print(f"[WARN] vault_links 生成失败（不影响归并结果）: {e}")

    progress.mark_stage_done(1)
    print(f"[stage1] 素材归一化 {len(manifest)} 条（去重 {len(removed)} 条），设定集生成完成")
    return True, "stage1 完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段1：素材梳理（确定性部分）")
    parser.add_argument("--materials", default="materials/raw")
    parser.add_argument("--normalized", default="data/setting/normalized")
    parser.add_argument("--out", default="data/setting/materials_manifest.json")
    parser.add_argument("--no-dedup", action="store_true", help="跳过相似去重")
    parser.add_argument("--scraps", default=SCRAPS_DIR_DEFAULT,
                        help="原始碎片目录（自由命名、不进 Git）")
    parser.add_argument("--with-scraps", action="store_true",
                        help="本次一并扫描原始碎片并计入素材指纹")
    parser.add_argument("--no-scraps", action="store_true", help="本次完全忽略原始碎片")
    parser.add_argument("--scraps-index", default=SCRAPS_INDEX, help="碎片索引输出路径")
    args = parser.parse_args()

    with_scraps = not args.no_scraps
    scraps_index = None
    if with_scraps:
        scraps_index, _batches, warnings = prepare_scraps(args.scraps, args.scraps_index)
        for w in warnings:
            print("[碎片] %s" % w)
        if scraps_index:
            st = scraps_index["stats"]
            print("原始碎片: %d 个 → %d 簇（%d 合并簇）/ %d 前瞻备忘"
                  % (st["scrap_count"], st["cluster_count"],
                     st["merged_cluster_count"], st["lookahead_count"]))
            print("碎片索引: %s" % args.scraps_index)
        else:
            print("原始碎片: 无（%s）" % args.scraps)

    manifest = scan_materials(args.materials, args.normalized)
    removed = []
    if not args.no_dedup:
        manifest, removed = dedup(manifest)
    extra_fp = [scraps_index["content_fingerprint"]] if scraps_index else []
    _write_manifest(manifest, removed, args.out,
                    manifest_fingerprint(manifest, extra_fingerprints=extra_fp))
    print(f"素材归一化: {len(manifest)} 条保留, {len(removed)} 条去重移除")
    print(f"清单输出: {args.out}")
    return 0 if (manifest or scraps_index) else 1


if __name__ == "__main__":
    raise SystemExit(main())
