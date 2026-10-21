# -*- coding: utf-8 -*-
"""卷弧滚动规划命令行 + ensure（供 stage3 / orchestrator 复用）。

用法：
    python scripts/volume_plan.py --show                # 看各卷状态
    python scripts/volume_plan.py --ensure              # 缺哪卷补哪卷（当前卷 + 下一卷）
    python scripts/volume_plan.py --volume 3            # 指定卷
    python scripts/volume_plan.py --volume 3 --force    # 已存在也重新规划（会覆盖契约，慎用）

## 生成纪律

- **缺才生成**：已存在且通过 `check_volume_outline`（逐章目标覆盖每章 + 无占位符）
  就直接复用，不重复烧 token。
- **存在但不合格 → 重生成**：这是"修复"，不是"静默换契约"——
  不合格的卷纲本来就不该被下游当既定事实。打印出具体不合格原因。
- **合格就绝不覆盖**（除非 `--force`）：卷纲是后面 20 章的契约，
  悄悄换掉会让已写的章对不上。
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path

from utils import volume_arc as va
from utils.config_io import load_config_yaml
from utils.file_io import read_text
from utils.template_loader import load_template


def _written_chapters(raw_dir="data/chapters/raw"):
    """已写到第几章（按 raw 目录里最大章号；没有则 0）。"""
    p = Path(raw_dir)
    if not p.exists():
        return 0
    nos = []
    for f in p.glob("*.md"):
        try:
            nos.append(int(f.stem))
        except (TypeError, ValueError):
            continue
    return max(nos) if nos else 0


def build_volume_task(cfg, proj, v, start, end, total, written, global_outline="data/outline/global.md",
                      setting_path="data/setting/setting.json"):
    if written > 0:
        written_note = ("已写到第 %d 章（这些章已经发生的事实不可推翻；本卷要接住它们）。"
                        % written)
    else:
        written_note = "还没有开始写作（本卷是开篇卷）。"
    # 叙事深化段数建议：按卷章数给模板里的区间（不足 6 章的卷只切 1 段，不硬凑）。
    # 值写进模板只是给模型的建议区间；**判据在 story_deepen.check_deepen**（确定性）。
    from utils import story_deepen as sd
    span = int(end) - int(start) + 1
    if span < sd.SMALL_VOLUME_CHAPTERS:
        min_seg, max_seg = 1, 1
    else:
        min_seg, max_seg = sd.MIN_SEGMENTS, sd.MAX_SEGMENTS
    mid = (int(start) + int(end)) // 2
    _, body = load_template("volume_plan.md", {
        "v": v,
        "start": start,
        "start_plus": start + 1,
        "end": end,
        "path_global": Path(global_outline).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_volume": va.volume_path(v).resolve(),
        "written_note": written_note,
        "min_seg": min_seg,
        "max_seg": max_seg,
        # 示例用的两段切分点（仅格式示例，模型自己决定真实切法）
        "seg1_end": mid,
        "seg2_start": mid + 1,
        "seg2_end": int(end),
    })
    return body


def ensure_volume(v, cfg=None, proj=None, client=None, task_dir="data/state/tasks",
                  total=None, written=None, force=False, log=print):
    """确保第 v 卷有**合格**卷纲。返回 (ok, msg, generated)。

    generated=True 表示这次真的调了模型（调用方可据此记成本）。
    """
    cfg = cfg or {}
    proj = proj or {}
    # 文体（2026-10-20）：散文 / 随笔集不做分卷 —— `writing_form` 里早写了 volume=False，
    # 但一直没人读：配了不生效比没配更糟（用户以为关了，实际还在烧 token 生成卷纲）。
    from utils import writing_form as _wf
    _app = _wf.applicability(proj)
    if not _app.get("volume", True):
        msg = "本文体（%s）不做分卷规划，跳过" % _app.get("label", "")
        log("[volume] " + msg)
        return True, msg, False
    total = int(total if total is not None else (proj.get("book") or {}).get("chapters", 0) or 0)
    size = va.volume_size(cfg, proj)
    start, end = va.volume_range(v, size, total or size * v)
    if written is None:
        written = _written_chapters()
    p = va.volume_path(v)

    if p.exists() and not force:
        ok, problems = va.check_volume_outline(p, start, end)
        if ok:
            log("[volume] 第 %d 卷（第 %d–%d 章）卷纲已存在且合格，跳过" % (v, start, end))
            return True, "已存在且合格", False
        log("[volume] 第 %d 卷卷纲**不合格**，将重新规划：%s" % (v, "；".join(problems)))

    if client is None:
        from utils.llm_client import make_client
        client = make_client(cfg, "outliner")
    task = client.write_task(task_dir, "volume_plan_v%02d.md" % v,
                             build_volume_task(cfg, proj, v, start, end, total, written))
    result = client.run_task(task)
    if result.get("exit_code") != 0:
        return False, "第 %d 卷规划失败（子会话退出码 %s）" % (v, result.get("exit_code")), True

    ok, problems = va.check_volume_outline(p, start, end)
    if not ok:
        # 不合格也把文件留在盘上（不删证据），但**不登记为已规划**
        return False, ("第 %d 卷卷纲不合格：%s（文件已留在 %s，可手工改）"
                       % (v, "；".join(problems), p.as_posix())), True

    # 叙事深化：**只有新生成**的卷纲走严格校验（存量卷纲没有深化节是正常态，
    # 不判废不重生成 —— 与章大纲六字段同纪律）。文体不做深化（essay/episodic）时早退。
    if _app.get("deepen", True):
        from utils import story_deepen as sd
        segs, present = sd.load_segments(p)
        if not present:
            log("[volume] WARN 第 %d 卷卷纲缺「叙事深化」节 —— 本卷将没有节奏形状注入"
                "（旧卷纲正常；要补切段就 --force 重规划）" % v)
        else:
            d_ok, d_problems = sd.check_deepen(segs, start, end)
            if not d_ok:
                return False, ("第 %d 卷叙事深化段不合格：%s（文件已留在 %s，可手工改）"
                               % (v, "、".join(d_problems), p.as_posix())), True

    plan = va.load_plan()
    parsed = va.parse_volume_outline(read_text(p))
    plan["volume_size"] = size
    va.mark_outlined(plan, v, start, end, based_on_chapter=written)
    plan["volumes"][str(v)]["goal"] = parsed.get("goal", "")
    plan["volumes"][str(v)]["title"] = parsed.get("title", "")
    va.save_plan(plan)
    log("[volume] 第 %d 卷（第 %d–%d 章）卷纲完成：%s" % (v, start, end, p.as_posix()))
    return True, "已生成", True


def ensure_window(cfg=None, proj=None, client=None, total=None, written=None,
                  force=False, log=print):
    """确保「当前卷 + 后续 rolling_volumes-1 卷」都有合格卷纲。返回 (ok, messages, generated_n)。"""
    cfg = cfg or {}
    proj = proj or {}
    # 文体（2026-10-20）：散文不做分卷 —— 入口早退，免得逐卷各打印一遍
    from utils import writing_form as _wf
    _app = _wf.applicability(proj)
    if not _app.get("volume", True):
        msg = "本文体（%s）不做分卷规划，跳过" % _app.get("label", "")
        log("[volume] " + msg)
        return True, [msg], 0
    total = int(total if total is not None else (proj.get("book") or {}).get("chapters", 0) or 0)
    size = va.volume_size(cfg, proj)
    roll = va.rolling_volumes(cfg)
    if written is None:
        written = _written_chapters()
    if roll <= 0:
        vols = list(range(1, va.volume_of(total, size) + 1))
    else:
        cur = va.volume_of(max(1, written + 1), size)
        last = va.volume_of(total, size)
        vols = list(range(cur, min(last, cur + roll - 1) + 1))
    msgs, gen = [], 0
    ok_all = True
    for v in vols:
        ok, msg, generated = ensure_volume(v, cfg, proj, client=client, total=total,
                                           written=written, force=force, log=log)
        msgs.append("第%d卷：%s" % (v, msg))
        ok_all = ok_all and ok
        gen += 1 if generated else 0
    return ok_all, msgs, gen


def show(cfg=None, proj=None):
    cfg = cfg or {}
    proj = proj or {}
    total = int((proj.get("book") or {}).get("chapters", 0) or 0)
    size = va.volume_size(cfg, proj)
    roll = va.rolling_volumes(cfg)
    plan = va.load_plan()
    written = _written_chapters()
    print("卷弧规划状态：%d 章 / 每卷 %d 章 / 滚动窗口 %s 卷 / 已写到第 %d 章"
          % (total, size, roll if roll else "关闭", written))
    if not total:
        print("（project.yaml 没写章数，无法分卷）")
        return 0
    for v in range(1, va.volume_of(total, size) + 1):
        start, end = va.volume_range(v, size, total)
        p = va.volume_path(v)
        info = (plan.get("volumes") or {}).get(str(v)) or {}
        if p.exists():
            ok, problems = va.check_volume_outline(p, start, end)
            state = "合格" if ok else "不合格（%s）" % "；".join(problems)
        else:
            state = "未规划"
        print("  第 %d 卷（第 %d–%d 章）：%s%s"
              % (v, start, end, state,
                 "（基于第%d章后）" % info["based_on_chapter"]
                 if info.get("based_on_chapter") else ""))
    return 0


def main():
    ap = argparse.ArgumentParser(description="卷弧滚动规划")
    ap.add_argument("--ensure", action="store_true", help="缺哪卷补哪卷（滚动窗口内）")
    ap.add_argument("--volume", type=int, default=0, help="指定卷号")
    ap.add_argument("--force", action="store_true", help="已存在也重新规划（会覆盖契约）")
    ap.add_argument("--show", action="store_true", help="只看状态")
    ap.add_argument("--task-dir", default="data/state/tasks")
    a = ap.parse_args()
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    if a.show and not a.ensure and not a.volume:
        return show(cfg, proj)
    if a.volume:
        ok, msg, _ = ensure_volume(a.volume, cfg, proj, task_dir=a.task_dir, force=a.force)
        print(msg)
        return 0 if ok else 1
    if a.ensure:
        ok, msgs, gen = ensure_window(cfg, proj, force=a.force)
        for m in msgs:
            print(m)
        print("本轮生成 %d 卷卷纲" % gen)
        return 0 if ok else 1
    return show(cfg, proj)


if __name__ == "__main__":
    raise SystemExit(main())
