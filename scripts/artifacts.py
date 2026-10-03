# -*- coding: utf-8 -*-
"""副产物管理台：**看清 / 导出 / 清理**（用户第 4 条，2026-10-03）。

## 为什么

流水线跑起来会在十来个目录里铺东西，但它们**性质完全不同**：

- **纯派生留痕**（截断的残缺稿、失败审稿原文、`--verbose` 的原始请求响应、任务文件、
  拆书切分正文）→ 随时可清，清掉不影响任何产物；
- **备份**（config / prompts / 大纲 / 章节 / 设定集的历史版本）→ 有用但要能按数量或
  天数收口，也能整体导出留档；
- **绝不该清**（项目快照 `history/`、归档书 `data/books/`、账本 `logs/runs.db`、
  体检/审稿报告、待审沙盒）→ 它们是内容与版本职责本身，删了就是删书。

此前这些目录**没有任何统一入口**：用户要么去翻文档目录表，要么根本不知道存在。
本脚本给出一屏清单（路径/数量/体积/最老最新/安全等级/建议），并提供导出与清理。

## 用法

    python scripts/artifacts.py                       # = --list，一屏看清
    python scripts/artifacts.py --json                # 机器可读（给外部 Agent / GUI）
    python scripts/artifacts.py --export truncated,tasks --to D:\\backup   # 导出（默认打包 zip）
    python scripts/artifacts.py --clean tasks --older-than 14             # 默认 dry-run 预览
    python scripts/artifacts.py --clean tasks --older-than 14 --yes       # 真删

纪律：**默认 dry-run**（与 `snapshot.py --restore` 同惯例）；`keep` 等级一律拒绝清理，
并把"该用哪个工具"指出来；`--yes` 才会落盘删除。
"""
import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from utils.console import ensure_utf8_stdout
    ensure_utf8_stdout()
except Exception:                                           # noqa: BLE001
    pass

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# ---- 副产物登记表（**唯一事实来源**；本表就是"专门的地方"的名录）----
# grade: safe = 纯派生可随时清 / backup = 备份，按量或按天收口 / keep = 不许清（内容本身）
ARTIFACT_KINDS = (
    {"id": "truncated", "grade": "safe", "path": "data/state/truncated",
     "what": "被 max_tokens 截断的残缺输出（隔离存放，防止半截章节被当成品）",
     "reuse": "排查截断原因（看它被砍在哪）；确认无用后可清"},
    {"id": "review_raw", "grade": "safe", "path": "data/state/review_raw",
     "what": "审稿 JSON 解析失败时隔离的模型原文",
     "reuse": "定位解析失败原因（括号/围栏/思考残片）；确认后可清"},
    {"id": "llm_raw", "grade": "safe", "path": "data/state/llm_raw",
     "what": "`--verbose` 落盘的 LLM 请求/响应原文",
     "reuse": "复盘提示词与模型行为；**含正文草稿，导出前想清楚**；确认后可清"},
    {"id": "tasks", "grade": "safe", "path": "data/state/tasks",
     "what": "各阶段生成的任务文件（子会话/prompt 的输入快照）",
     "reuse": "复现某一章的输入、手工重跑、对比提示词改动的影响"},
    {"id": "book_split", "grade": "safe", "path": "data/state/book_split",
     "what": "拆书切分出的正文（仅 `--emit` 时生成）",
     "reuse": "拿去做素材 / 对照节奏；确认后可清"},
    {"id": "config_history", "grade": "backup", "path": "config/history",
     "what": "配置定向改写前的备份（`utils/config_io` / project_config 写入时自动留）",
     "reuse": "改坏配置后回滚单份配置"},
    {"id": "prompts_history", "grade": "backup", "path": "prompts/history",
     "what": "提示词模板保存前的备份（GUI 保存时自动留，每模板最近 50 份）",
     "reuse": "回滚提示词；对比改前改后的产出差异"},
    {"id": "outline_history", "grade": "backup", "path": "data/outline/history",
     "what": "大纲每次精修的版本（global_vN.md）",
     "reuse": "回退到某一版大纲；`outline_review --trend` 的收敛判断就靠它"},
    {"id": "chapters_history", "grade": "backup", "path": "data/chapters/history",
     "what": "章节精修前的原稿（chNN_vM.md）",
     "reuse": "回退某一章；对比精修幅度"},
    {"id": "setting_history", "grade": "backup", "path": "data/setting/history",
     "what": "设定集补全/导入前的备份（setting_vN.json）",
     "reuse": "回滚设定集"},
    {"id": "snapshots", "grade": "keep", "path": "history",
     "what": "项目快照（每阶段成功后自动打，保留最近 10 份）",
     "reuse": "回滚整个工作区 —— 用 `snapshot.py --list` / `--restore <ID> --yes`"},
    {"id": "archived_books", "grade": "keep", "path": "data/books",
     "what": "归档的旧书（换书时整本搬进来）",
     "reuse": "回来看/取素材 —— 用 `switch_book.py --restore \"书名\" --yes`"},
    {"id": "reports", "grade": "keep", "path": "data/outline/*report*",
     "what": "体检 / 审稿 / 校对报告（review_report、proofread_report、polish_report…）",
     "reuse": "直接读；GUI 页签也读它 —— 要留存请用 `--export`，**不要删**"},
    {"id": "sandbox", "grade": "keep", "path": "data/state/obsidian_sandbox",
     "what": "待审产物（回写设定库的草稿）",
     "reuse": "走审核流程（`sandbox_review.py --queue` / GUI「审核」页签）"},
    {"id": "cost_db", "grade": "keep", "path": "logs/runs.db",
     "what": "运行记录与成本账本（token 熔断的计量来源）",
     "reuse": "`cost_report.py`、`nfctl status` 的 token 用量都读它 —— **删了就没账**"},
)
GRADES = ("safe", "backup", "keep")


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def kind_by_id(root, kid):
    for k in ARTIFACT_KINDS:
        if k["id"] == kid:
            kk = dict(k)
            kk["abs"] = (root / k["path"])
            return kk
    return None


def _files_of(root, kind):
    """登记项的现有文件列表。`path` 支持 glob（如 `data/outline/*report*`）。"""
    rel = kind["path"]
    if "*" in rel or "?" in rel:
        return sorted(f for f in root.glob(rel) if f.is_file())
    p = root / rel
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(f for f in p.rglob("*") if f.is_file())
    return []


def stat_kind(root, kind):
    files = _files_of(root, kind)
    size = sum(f.stat().st_size for f in files)
    mtimes = [f.stat().st_mtime for f in files]
    return {
        "id": kind["id"], "grade": kind["grade"], "path": kind["path"],
        "exists": bool(files), "files": len(files), "bytes": size,
        "newest": (datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M")
                   if mtimes else None),
        "oldest": (datetime.fromtimestamp(min(mtimes)).strftime("%Y-%m-%d %H:%M")
                   if mtimes else None),
    }


def do_list(root, as_json=False):
    rows = [stat_kind(root, k) for k in ARTIFACT_KINDS]
    if as_json:
        print(json.dumps({"root": str(root), "kinds": rows,
                          "registry": [{k: v for k, v in kk.items() if k != "abs"}
                                       for kk in [dict(x) for x in ARTIFACT_KINDS]]},
                         ensure_ascii=False, indent=1))
        return 0
    print("副产物管理台 · %s" % root)
    print("=" * 84)
    total_safe = total_backup = 0
    for k, row in zip(ARTIFACT_KINDS, rows):
        mark = {"safe": "[可清]", "backup": "[备份]", "keep": "[勿删]"}[k["grade"]]
        if row["exists"]:
            print("%s %-16s %-30s %5d 个文件  %10s  最新 %s"
                  % (mark, k["id"], k["path"], row["files"], _human(row["bytes"]),
                     row["newest"] or "-"))
        else:
            print("%s %-16s %-30s %s" % (mark, k["id"], k["path"], "（暂无）"))
        if k["grade"] == "safe":
            total_safe += row["bytes"]
        elif k["grade"] == "backup":
            total_backup += row["bytes"]
    print("-" * 84)
    print("可清（safe）合计 %s · 备份（backup）合计 %s" % (_human(total_safe), _human(total_backup)))
    print("\n每条是什么 / 怎么复用：")
    for k in ARTIFACT_KINDS:
        print("  · %-16s %s" % (k["id"], k["what"]))
        print("      %s复用：%s" % (" " * 12, k["reuse"]))
    print("\n清理：`--clean <id,...> [--older-than 天] [--keep-last N] --yes`（默认 dry-run 预览）")
    print("导出：`--export <id,...|all> --to <目录>`（默认打包成一个 zip）")
    return 0


def do_export(root, ids, to_dir, zip_it=True):
    kinds = [kind_by_id(root, i) for i in ids]
    missing = [i for i, k in zip(ids, kinds) if k is None]
    if missing:
        print("ERROR: 未知副产物 id: %s" % "、".join(missing))
        print("       可用：%s" % "、".join(k["id"] for k in ARTIFACT_KINDS))
        return 1
    out = Path(to_dir)
    if not out.is_absolute():
        out = Path.cwd() / out
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if zip_it:
        zpath = out / ("novelforge_artifacts_%s.zip" % stamp)
        n = 0
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for k in kinds:
                for f in _files_of(root, k):
                    z.write(f, arcname=str(f.relative_to(root)).replace("\\", "/"))
                    n += 1
        print("已导出 %d 个文件 → %s" % (n, zpath))
        return 0
    n = 0
    for k in kinds:
        src = root / k["path"]
        dst = out / k["id"]
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            n += sum(1 for f in dst.rglob("*") if f.is_file())
        elif src.is_file():
            shutil.copy2(src, dst)
            n += 1
        else:
            for f in _files_of(root, k):
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst / f.name)
                n += 1
    print("已导出 %d 个文件 → %s" % (n, out))
    return 0


def do_clean(root, ids, older_than=None, keep_last=None, yes=False):
    kinds = [kind_by_id(root, i) for i in ids]
    missing = [i for i, k in zip(ids, kinds) if k is None]
    if missing:
        print("ERROR: 未知副产物 id: %s" % "、".join(missing))
        return 1
    refuse = [k for k in kinds if k["grade"] == "keep"]
    if refuse:
        print("拒绝清理（这些是内容本身，不是留痕）：")
        for k in refuse:
            print("  · %s（%s）→ %s" % (k["id"], k["path"], k["reuse"]))
        print("要留存/搬走请用 `--export`。")
        return 1

    plan = []
    for k in kinds:
        for f in _files_of(root, k):
            plan.append((k, f))
    # 默认按时间倒序，便于 keep-last 分目录取"最近 N 个"
    plan.sort(key=lambda kf: (kf[0]["id"], -kf[1].stat().st_mtime))
    victims = plan
    if older_than is not None:
        cut = datetime.now().timestamp() - float(older_than) * 86400
        victims = [(k, f) for k, f in victims if f.stat().st_mtime < cut]
    if keep_last:
        kept = set()
        seen = {}
        for k, f in plan:
            i = seen.get(k["id"], 0)
            if i < int(keep_last):
                kept.add(f)
            seen[k["id"]] = i + 1
        victims = [(k, f) for k, f in victims if f not in kept]

    freed = sum(f.stat().st_size for _k, f in victims if f.exists())
    if not victims:
        print("没有匹配的副产物需要清理（条件：older-than=%s keep-last=%s）"
              % (older_than, keep_last))
        return 0
    print("%s清理计划：%d 个文件，约 %s"
          % ("**执行**" if yes else "预览（dry-run，未删任何东西）", len(victims), _human(freed)))
    for k, f in victims[:40]:
        print("  - %s" % f.relative_to(root))
    if len(victims) > 40:
        print("  … 其余 %d 个" % (len(victims) - 40))
    if not yes:
        print("\n加 `--yes` 才会真删。")
        return 0
    for _k, f in victims:
        try:
            f.unlink()
        except OSError as e:
            print("  删除失败 %s：%s" % (f, e))
    # 顺手清掉空目录（只清登记表里的那几个，不递归扫别的）
    for k in kinds:
        p = root / k["path"]
        if p.is_dir():
            for d in sorted((x for x in p.rglob("*") if x.is_dir()),
                            key=lambda x: len(x.parts), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass
    print("已清理 %d 个文件，释放约 %s" % (len(victims), _human(freed)))
    return 0


def parse_ids(raw):
    if not raw or raw.strip().lower() == "all":
        return [k["id"] for k in ARTIFACT_KINDS]
    return [x.strip() for x in raw.split(",") if x.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="副产物管理台（看清 / 导出 / 清理）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="项目根（默认=脚本所在仓库；测试与 --root 场景可覆盖）")
    ap.add_argument("--json", action="store_true", help="机器可读清单")
    ap.add_argument("--export", metavar="IDS", help="导出这些 id（逗号分隔，或 all）")
    ap.add_argument("--to", metavar="DIR", help="导出目标目录")
    ap.add_argument("--no-zip", action="store_true", help="导出为目录树而非 zip")
    ap.add_argument("--clean", metavar="IDS", help="清理这些 id（逗号分隔；只允许 safe/backup）")
    ap.add_argument("--older-than", type=float, default=None, help="只清理 N 天前的文件")
    ap.add_argument("--keep-last", type=int, default=None, help="每个目录保留最近 N 个文件")
    ap.add_argument("--yes", action="store_true", help="真正执行删除（默认 dry-run）")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.export:
        if not a.to:
            print("ERROR: --export 需要 --to <目录>")
            return 2
        return do_export(root, parse_ids(a.export), a.to, zip_it=not a.no_zip)
    if a.clean:
        return do_clean(root, parse_ids(a.clean), older_than=a.older_than,
                        keep_last=a.keep_last, yes=a.yes)
    return do_list(root, as_json=a.json)


if __name__ == "__main__":
    sys.exit(main())
