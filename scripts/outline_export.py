# -*- coding: utf-8 -*-
"""大纲迭代 → 沙盒审核包（2026-09-21）。

## 解决什么

此前沙盒回写**只有「完书后」通道**（`obsidian_postprocess.py` 生成作品介绍页 /
出场记录 / 新角色设定）。而用户真正需要的是**在大纲迭代过程中**把成果推出来审阅
—— 「我迭代了 12 轮，想看看到底改了什么、值不值得开工」。

本模块把一轮迭代的成果导出为**审核包**（三份文件）到沙盒，并自动登记为待审：

1. `大纲_对比_v{prev}_到_v{cur}.md` —— 体检指针 delta + 逐条目改善/退化/新增/删除
2. `大纲_v{cur}_待审.md`           —— 当前大纲全文（带体检结论头）
3. `大纲_迭代趋势.md`               —— 全版本指标表 + 收敛判定 + 建议

**只写沙盒，绝不碰 `data/outline/global.md`** —— 产物本身只读，审阅是旁路。
审核动作走 `python scripts/sandbox_review.py`（pending → approved / rejected）。

用法：
  python scripts/outline_export.py                # 导出最新一轮的审核包
  python scripts/outline_export.py --dry-run      # 只看会导出什么
  python scripts/outline_export.py --trend-window 5
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import outline_review as ov                          # noqa: E402
from obsidian_bridge import get_sandbox_dir          # noqa: E402
from utils.file_io import read_text                  # noqa: E402

GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"
SETTING = "data/setting/setting.json"

KIND = "outline_proposal"


def _header(title, cur_label, note_extra=""):
    """审核包的统一抬头 —— 让用户在 Obsidian 里一眼知道这是**待审提案**，
    而不是可以直接粘进 vault 的成品。"""
    return (f"# {title}\n\n"
            f"> 由 NovelForge 生成（{datetime.now().strftime('%Y-%m-%d %H:%M')}）·"
            f" 版本 {cur_label}\n"
            f"> **这是待审提案，不是成品。** 审阅后在项目里执行 "
            f"`python scripts/sandbox_review.py --approve <本文件名>` 通过，"
            f"或 `--reject <文件名> --note \"原因\"` 驳回。\n"
            f"> 审核状态只存在项目内 `data/state/sandbox_manifest.json`，"
            f"**不会随本文件粘进 vault**。\n"
            + (f"> {note_extra}\n" if note_extra else "")
            + "\n---\n\n")


def _cmp_markdown(cmp, cur_label):
    """对比稿：指针 delta + 逐条目差分。"""
    if not cmp:
        return _header("大纲迭代对比", cur_label) + \
            "尚无历史版本可对比（这是第一轮精修）。\n"
    p, c = cmp["prev"], cmp["cur"]
    d = cmp["deltas"]

    def sgn(v):
        return f"{v:+d}" if isinstance(v, int) else f"{v:+.2f}"

    lines = [_header("大纲迭代对比", cur_label,
                     f"对比基准：{p['label']}"),
             "## 体检指针变化\n",
             "| 指标 | 改前 | 改后 | 变化 |", "|---|---|---|---|",
             f"| 结构性问题 | {p['issues']} | {c['issues']} | {sgn(d['issues'])} |",
             f"| 空泛条目 | {p['thin']} | {c['thin']} | {sgn(d['thin'])} |",
             f"| 条目总数 | {p['total']} | {c['total']} | {sgn(d['total'])} |",
             f"| 平均分 | {p['avg_score']} | {c['avg_score']} | {sgn(d['avg_score'])} |",
             f"\n**本轮结论**：{cmp['advice']}\n"]

    ent = cmp["entries"]
    lines.append("## 逐条目变化\n")
    if not any(ent.values()):
        lines.append("条目集合与评分均无变化。\n")
    for key, label in (("improved", "改善"), ("regressed", "退化"),
                       ("added", "新增"), ("removed", "删除")):
        if ent[key]:
            lines.append(f"### {label}（{len(ent[key])} 条）\n")
            for t in ent[key]:
                lines.append(f"- {t}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _current_markdown(cur_label, review):
    """当前大纲全文 + 体检结论头。"""
    body = read_text(GLOBAL)
    s = review["summary"]
    lines = [_header("整体大纲（候选版）", cur_label),
             "## 体检结论\n",
             f"- 条目 {s['total']} 条（OK {s['ok']} / WARN {s['warn']} / THIN {s['thin']}）"
             f" → **{s['verdict']}**",
             f"- 预计章节数：{review.get('expected_chapters')}"]
    if review.get("issues"):
        lines.append("- 结构性问题：")
        for i in review["issues"]:
            lines.append(f"  - {i}")
    else:
        lines.append("- 结构性问题：无")
    lines += ["", "---", "", body]
    return "\n".join(lines) + "\n"


def _trend_markdown(series, window):
    """趋势表 + 收敛判定。"""
    status, note = ov.convergence_verdict(series, window=window)
    lines = [_header("大纲迭代趋势", f"共 {len(series)} 个版本"),
             "## 逐版本指标\n",
             "| 版本 | 条目 | OK | WARN | THIN | 结构性问题 | 平均分 | 单版体检 |",
             "|---|---|---|---|---|---|---|---|"]
    for e in series:
        lines.append(f"| {e['label']} | {e['total']} | {e['ok']} | {e['warn']} | "
                     f"{e['thin']} | {e['issues']} | {e['avg_score']} | {e['verdict']} |")
    tag = {"done": "✅ 已收敛", "stalled": "⚠ 停滞", "improving": "↘ 改善中",
           "mixed": "⚡ 波动", "insufficient": "· 样本不足"}.get(status, status)
    lines += ["", f"## 收敛判定：{tag}\n", note, "",
              "> 单版体检 = 该版本自身的 PASS/WARN/FAIL（有碎片即 FAIL）；",
              "> 收敛判定 = **跨版本**趋势（看最近几轮是否有实质进展）。两者不是一回事。"]
    return "\n".join(lines) + "\n"


def export(global_path=GLOBAL, setting_path=SETTING, history_dir=HISTORY_DIR,
           sandbox_dir=None, window=3, dry_run=False):
    """导出审核包。返回 (ok, msg, files)。"""
    if not Path(global_path).exists():
        return False, f"整体大纲不存在: {global_path}（先跑 stage2）", []

    sandbox = Path(sandbox_dir) if sandbox_dir else get_sandbox_dir()
    series = ov.review_series(global_path, setting_path, history_dir=history_dir)
    if not series:
        return False, "无版本可导出", []
    cur = series[-1]
    cur_label = cur["label"]
    review = ov.review(global_path, setting_path)
    cmp = ov.compare_with_previous(global_path, setting_path, history_dir=history_dir)

    # 文件名带版本号 → 同一版本重复导出时内容稳定，审核状态得以保留
    ver_tag = ""
    lb = ov.latest_backup(history_dir)
    if lb is not None:
        # 命名要点：lb[0] 是「最近一轮精修**之前**」的版本号，
        # 当前 global.md 是该轮的结果 —— 写 "v3_到_3" 语义不明，
        # 明确成 "v3到当前"。
        ver_tag = f"_v{lb[0]}到当前"
    plans = [
        (f"大纲_对比{ver_tag}.md", _cmp_markdown(cmp, cur_label), "本轮改了什么"),
        ("大纲_当前_待审.md", _current_markdown(cur_label, review), "候选版全文 + 体检"),
        ("大纲_迭代趋势.md", _trend_markdown(series, window), "要不要再迭代"),
    ]

    if dry_run:
        return True, "dry-run（未写入）", [p[0] for p in plans]

    from obsidian_bridge import write_sandbox            # noqa: PLC0415
    written = []
    for name, content, _desc in plans:
        ok, msg = write_sandbox(name, content, kind=KIND,
                                source=f"outline_export {cur_label}")
        if not ok:
            return False, f"写入失败 {name}: {msg}", written
        written.append(name)
    return True, f"已导出 {len(written)} 份待审产物到 {sandbox}", written


def main():
    ap = argparse.ArgumentParser(description="把大纲迭代成果导出为沙盒审核包")
    ap.add_argument("--global", dest="global_path", default=GLOBAL)
    ap.add_argument("--setting", dest="setting_path", default=SETTING)
    ap.add_argument("--history", dest="history_dir", default=HISTORY_DIR)
    ap.add_argument("--sandbox", dest="sandbox_dir", default=None,
                    help="沙盒目录（默认读 config/system.yaml 的 obsidian.sandbox_dir）")
    ap.add_argument("--trend-window", type=int, default=3,
                    help="收敛判定看最近几轮（默认 3）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会导出什么")
    args = ap.parse_args()

    ok, msg, files = export(args.global_path, args.setting_path, args.history_dir,
                            args.sandbox_dir, window=args.trend_window,
                            dry_run=args.dry_run)
    print(f"[outline_export] {msg}")
    for f in files:
        print(f"  - {f}")
    if ok and not args.dry_run:
        print("[outline_export] 审阅：python scripts/sandbox_review.py --queue")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
