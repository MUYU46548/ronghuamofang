# -*- coding: utf-8 -*-
"""单章重跑（第 N 章的 4→5→6）：准备 / 收尾（2026-11-04，task-20261104-002）。

## 它解决的是什么

用户点「重写」只有**阶段 4** 会重跑：`raw/NN.md` 换了，`checked/NN.md`、
`refined/NN.md`、`data/review/NN.json`、`checked_pending/NN.md`（stage5 改稿提案）
全是旧的。想把这一章的成品更新，就只能「从阶段 5 重跑」——而那是**整本**清空重算，
40 章的书等于为了 1 章重烧 39 章。

本模块给的是**纵向、单章**的重跑：只把第 N 章的 4/5/6 产物作废并重算，
其他章一个字节不碰。阶段级打回（reject.py）语义不动 —— 打回是"整本这个阶段我不满意"。

## 两条硬边界

1. **先快照，备份不了就不动**（沿用 import_guard 的丙3 契约）；
2. **只作废目标章**：raw 移到 `rewrite_backup/`（沿用既有唯一后悔药位置），
   checked / checked_pending / refined / 逐章评审移到 `rerun_backup/<ts>_chNN/`。
   不删任何文件 —— 出事能整目录捞回。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

# 层 → 目录（相对项目根）
LAYER_DIRS = {
    "raw": "data/chapters/raw",
    "checked": "data/chapters/checked",
    "checked_pending": "data/chapters/checked_pending",
    "refined": "data/chapters/refined",
}
REVIEW_DIR = "data/review"


def stage_layers(through=6):
    """range 到第几阶段 → 要作废哪些层（4=raw；5=checked/提案；6=refined）。"""
    layers = ["raw"]
    if int(through) >= 5:
        layers += ["checked", "checked_pending"]
    if int(through) >= 6:
        layers.append("refined")
    return layers


def inventory(root=".", n=1, through=6):
    """这次重跑会动哪些文件（文档 / GUI 预览 / 用例共用同一份口径）。

    返回 [{"layer", "path", "action", "exists"}]；`action` ∈ move/rewrite。
    """
    root = Path(root)
    n = int(n)
    name = "%02d.md" % n
    items = []
    for layer in stage_layers(through):
        p = root / LAYER_DIRS[layer] / name
        items.append({
            "layer": layer,
            "path": p.relative_to(root).as_posix(),
            "action": "rewrite" if layer == "raw" else "move",
            "exists": p.exists(),
        })
    if int(through) >= 5:
        p = root / REVIEW_DIR / ("%02d.json" % n)
        items.append({"layer": "review", "path": p.relative_to(root).as_posix(),
                      "action": "move", "exists": p.exists()})
    # 只读汇总：会被重算，但不是"按章作废"
    for rel in ("data/summaries/rolling.md", "data/state/appearances.json"):
        items.append({"layer": "summary", "path": rel,
                      "action": "recompute", "exists": (root / rel).exists()})
    return items


def _move(src: Path, dst_dir: Path):
    """移动文件到备份目录（保留原名）。返回 True = 真的移动了。"""
    if not src.exists():
        return False
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst_dir / src.name))
    return True


def prepare(root=".", n=1, through=6, snapshot=True):
    """重跑前：快照 → 目标章旧产物移到备份 → 清本章标记 → 阶段 4/5/6 置 pending。

    返回 {"backup_dir", "moved", "snapshot", "rollback"}。
    快照失败会抛 `ImportGuardError`（备份不了就不许动）。
    """
    root = Path(root)
    n = int(n)
    through = int(through)
    name = "%02d.md" % n
    ts = time.strftime("%Y%m%d_%H%M%S")

    info = {"n": n, "through": through, "moved": [], "snapshot": None,
            "backup_dir": None, "rollback": None}

    # 1) 整轮快照（丙3 契约：备份不了就不动）
    if snapshot:
        from utils.import_guard import preimport
        entry = preimport("rerun.ch%02d" % n, root=root)
        info["snapshot"] = entry.get("snapshot_id")
        info["rollback"] = entry.get("rollback")

    # 2) raw：移走（必须移 —— 留着的话 stage4 判"已完成"，根本不重写）
    raw = root / LAYER_DIRS["raw"] / name
    had_raw = raw.exists()
    if had_raw:
        bak_dir = root / "data" / "chapters" / "rewrite_backup"
        bak_dir.mkdir(parents=True, exist_ok=True)
        dst = bak_dir / ("%02d_%s.md" % (n, ts))
        shutil.move(str(raw), str(dst))
        info["moved"].append(dst.relative_to(root).as_posix())
        print("[rerun] 第%d章旧稿已移到 %s" % (n, dst.relative_to(root).as_posix()))
        # 旧稿存在 = 对已完成的一章发起新一轮 → 旧段群必须作废（否则复用旧文）
        parts = root / "data" / "chapters" / "_parts" / ("%02d" % n)
        if parts.exists():
            shutil.rmtree(parts, ignore_errors=True)
            print("[rerun] 第%d章旧段群缓存已作废" % n)

    # 3) 下游层：移到 rerun_backup（不删）
    backup_dir = root / "data" / "chapters" / "rerun_backup" / ("%s_ch%02d" % (ts, n))
    for layer in stage_layers(through):
        if layer == "raw":
            continue
        if _move(root / LAYER_DIRS[layer] / name, backup_dir / layer):
            info["moved"].append((backup_dir / layer / name).relative_to(root).as_posix())
    # 逐章评审账本（stage5 产物）：与 checked 同生共死
    if through >= 5:
        if _move(root / REVIEW_DIR / name.replace(".md", ".json"), backup_dir / "review"):
            info["moved"].append((backup_dir / "review" / name.replace(".md", ".json"))
                                 .relative_to(root).as_posix())
    if info["moved"]:
        info["backup_dir"] = backup_dir.relative_to(root).as_posix()
        print("[rerun] 第%d章旧产物已备份到 %s" % (n, info["backup_dir"]))

    # 4) 进度：只动本章标记 + 阶段 4/5/6 状态；**approved 一律不动**
    _mark_pending(root, n, through)
    return info


def _mark_pending(root: Path, n: int, through: int):
    from utils.progress_manager import ProgressManager
    pg = ProgressManager(root / "data" / "state" / "progress.json")
    st4 = pg.data.setdefault("stages", {}).setdefault("4", {})
    cc = list(st4.get("completed_chapters") or [])
    if n in cc:
        st4["completed_chapters"] = [x for x in cc if x != n]
    # 本章的失败/不合格留痕一并清掉：这轮重跑会给新结论
    st4["failed_chapters"] = [f for f in (st4.get("failed_chapters") or [])
                              if f.get("n") != n]
    inv = st4.get("invalid")
    if isinstance(inv, dict):
        inv.pop(str(n), None)
    if isinstance(st4.get("needs_rewrite"), list):
        st4["needs_rewrite"] = [x for x in st4["needs_rewrite"] if x != n]
    st4["rerun_chapter"] = n
    st4["rerun_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st4["rerun_through"] = through
    # 阶段 4/5/6 置 pending：否则 orchestrator 的断点逻辑会"已完成→跳过"
    for s in range(4, min(int(through), 6) + 1):
        st = pg.data.setdefault("stages", {}).setdefault(str(s), {})
        st["status"] = "pending"
        st.pop("finished_at", None)
        # approved 刻意保留：单章重跑不该让"我已经确认过这一阶段"作废
    pg.save()


def settle(root=".", n=1, through=6, refresh_judge=False):
    """收尾：确定性重算汇总（零 token）。返回说明列表。

    - 滚动摘要：stage4 写稿时已按章覆盖（幂等），这里不重复写；
    - 出场记录：全书确定性重算（内容只第 N 章变，`generated_at` 每次变）；
    - 判定（可选）：全书确定性回测（零 token），单章结论跟着新正文更新。
    """
    root = Path(root)
    n = int(n)
    msgs = []
    try:
        import sys
        if str(root / "scripts") not in sys.path:
            sys.path.insert(0, str(root / "scripts"))
        from appearances import refresh_appearances
        _p, stats, total, _new = refresh_appearances(
            chapter_count=_book_total(root))
        if _p:
            msgs.append("出场记录已重算（%d 个角色 / %d 章）" % (len(stats), total))
    except Exception as e:                                   # noqa: BLE001
        msgs.append("出场记录重算失败（不影响正文）：%s: %s"
                    % (type(e).__name__, str(e)[:120]))
    if refresh_judge:
        try:
            from utils.judge import backtest
            rep = backtest.run(root=str(root), book_name=_book_name(root), by="重跑单章")
            msgs.append("判定已重跑（%d 章）" % len(rep.get("chapters") or []))
        except Exception as e:                               # noqa: BLE001
            msgs.append("判定重跑失败（不影响正文）：%s: %s"
                        % (type(e).__name__, str(e)[:120]))
    return msgs


def _book_total(root: Path):
    try:
        cfg = (root / "config" / "project.yaml").read_text(encoding="utf-8-sig")
        import yaml
        return int((yaml.safe_load(cfg) or {}).get("book", {}).get("chapters") or 0) or None
    except Exception:                                        # noqa: BLE001
        return None


def _book_name(root: Path):
    try:
        import yaml
        cfg = (root / "config" / "project.yaml").read_text(encoding="utf-8-sig")
        return ((yaml.safe_load(cfg) or {}).get("book", {}) or {}).get("name", "")
    except Exception:                                        # noqa: BLE001
        return ""


def read_progress(root="."):
    """只读 progress（供端点做前置校验：章号是否超过总章数）。"""
    try:
        return json.loads((Path(root) / "data" / "state" / "progress.json")
                          .read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}
