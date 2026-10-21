# -*- coding: utf-8 -*-
"""卷弧双层滚动规划 —— 让"第 190 章"不再在第 1 天就被写死。

## 为什么（台账三·「卷弧双层滚动规划」）

旧流程是**一次性规划全书**：stage2 出一份 global.md，stage3 按它把 1..N 章的
章大纲全部生成。`outline_review` 已经抓到症状 —— 越靠后的节点越空泛
（"主角继续调查，遇到新的阻力"），因为模型在第 1 天并不知道第 190 章该发生什么，
它只能编一句放之四海而皆准的话。

## 双层怎么分

| 层 | 覆盖范围 | 粒度 | 何时确定 |
|---|---|---|---|
| **近层（near）** | 当前卷 + 后续 `rolling_volumes-1` 卷 | 卷纲 + **逐章目标** | 进入该区间时 |
| **远层（far）** | 更远的卷 | **一句话方向** | 只写方向，细节等写到再定 |

远层**故意不给细节**：给了也是编的，"编出来的细节"比"明确的空白"更糟 ——
后者至少让模型知道自己在即兴，前者会让它以为那是既定事实并强行贴合。

## 判据（确定性）

`check_volume_outline()`：卷纲必须覆盖该卷**每一章**的逐章目标（缺章即不合格），
且不得含占位符（"待补充"/"XXX"/"TODO"）。空泛无逐章目标的卷纲一律判不合格 ——
否则"滚动规划"只是把一次性规划拆成文件，质量没有变化。

## 不做自动重排

卷纲一旦生成即视为既定（`status=outlined`）；重生成必须显式 `--force`。
理由：卷纲是后面 20 章的契约，静默换掉会让已写的章对不上。
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

DEFAULT_VOLUME_SIZE = 20
DEFAULT_ROLLING_VOLUMES = 2
PLAN_REL = "data/outline/volumes/plan.json"
VOLUME_DIR_REL = "data/outline/volumes"
PLACEHOLDERS = ("待补充", "TODO", "XXX", "（略）", "(略)", "待定")


def volume_size(cfg=None, proj=None):
    """一卷多少章：project.yaml `book.volume_size` 优先，其次 system.yaml `chapter.volume_size`。"""
    for src, key in (((proj or {}).get("book") or {}, "volume_size"),
                     ((cfg or {}).get("chapter") or {}, "volume_size")):
        if key in src:
            try:
                v = int(src[key])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return DEFAULT_VOLUME_SIZE


def rolling_volumes(cfg=None):
    """只详细规划最近几卷（0 = 关闭滚动，回到"一次全书"）。"""
    try:
        v = int(((cfg or {}).get("chapter") or {}).get("rolling_volumes",
                                                       DEFAULT_ROLLING_VOLUMES))
    except (TypeError, ValueError):
        return DEFAULT_ROLLING_VOLUMES
    return max(0, v)


def volume_of(n, size):
    """第 n 章属于第几卷（1 起）。"""
    n = max(1, int(n))
    return (n - 1) // size + 1


def volume_range(v, size, total):
    """第 v 卷覆盖的章范围 (start, end)，end 不超过总章数。"""
    start = (v - 1) * size + 1
    end = min(v * size, max(start, int(total)))
    return start, end


def horizon(total, written, size, roll):
    """本轮到第几章为止：当前卷 + 后续 roll-1 卷；roll=0 则全书。"""
    total = int(total)
    if roll <= 0:
        return total
    v = volume_of(max(1, int(written) + 1), size)
    return min(total, (v + roll - 1) * size)


def plan_path(root="."):
    return Path(root) / PLAN_REL


def volume_path(v, root="."):
    return Path(root) / VOLUME_DIR_REL / ("v%02d.md" % int(v))


def load_plan(root="."):
    p = plan_path(root)
    if not p.exists():
        return {"version": 1, "volumes": {}}
    try:
        d = json.loads(read_text(p))
        if not isinstance(d.get("volumes"), dict):
            d["volumes"] = {}
        return d
    except Exception:                                          # noqa: BLE001
        return {"version": 1, "volumes": {}}


def save_plan(plan, root="."):
    p = plan_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_text(p, json.dumps(plan, ensure_ascii=False, indent=2))
    return plan


def mark_outlined(plan, v, start, end, based_on_chapter=0):
    plan.setdefault("volumes", {})[str(v)] = {
        "start": start,
        "end": end,
        "status": "outlined",
        "outlined_at": datetime.now().isoformat(timespec="seconds"),
        "based_on_chapter": based_on_chapter,
    }
    return plan


def parse_volume_outline(text):
    """把卷纲 md 拆成 {title, goal, turns, chapters:{n: line}, handoff}。"""
    t = text or ""
    out = {"title": "", "goal": "", "turns": [], "chapters": {}, "handoff": ""}
    m = re.search(r"^#\s+(.+)$", t, re.M)
    if m:
        out["title"] = m.group(1).strip()
    for key, pat in (("goal", r"^##\s*本卷目标\s*$"), ("handoff", r"^##\s*卷末交接\s*$")):
        mm = re.search(pat + r"\s*(.*?)(?=^##\s|\Z)", t, re.M | re.S)
        if mm:
            out[key] = mm.group(1).strip()
    sec = re.search(r"^##\s*关键转折\s*(.*?)(?=^##\s|\Z)", t, re.M | re.S)
    if sec:
        out["turns"] = [ln.strip("-*· \t") for ln in sec.group(1).splitlines()
                        if ln.strip().startswith(("-", "*", "·"))]
    sec = re.search(r"^##\s*逐章目标\s*(.*?)(?=^##\s|\Z)", t, re.M | re.S)
    if sec:
        for ln in sec.group(1).splitlines():
            cm = re.search(r"第\s*(\d+)\s*章\s*[：:、.\-]?\s*(.*)", ln)
            if cm:
                out["chapters"][int(cm.group(1))] = cm.group(2).strip()
    return out


def check_volume_outline(path, start, end):
    """卷纲合格判据：逐章目标覆盖每一章 + 无占位符。返回 (ok, problems)。"""
    p = Path(path)
    if not p.exists():
        return False, ["卷纲文件不存在：%s" % p.as_posix()]
    try:
        text = read_text(p)
    except Exception as e:                                     # noqa: BLE001
        return False, ["卷纲读取失败：%s" % e]
    if len(text.strip()) < 40:
        return False, ["卷纲内容过短（< 40 字）"]
    problems = []
    bad = [w for w in PLACEHOLDERS if w in text]
    if bad:
        problems.append("含占位符：%s" % "、".join(bad))
    parsed = parse_volume_outline(text)
    miss = [n for n in range(int(start), int(end) + 1) if n not in parsed["chapters"]]
    if miss:
        problems.append("逐章目标缺第 %s 章" % ("、".join(str(n) for n in miss[:12]))
                        + ("…" if len(miss) > 12 else ""))
    empty = [n for n, v in parsed["chapters"].items() if len(v.strip()) < 4]
    if empty:
        problems.append("逐章目标为空：第 %s 章" % "、".join(str(n) for n in sorted(empty)[:8]))
    return (not problems), problems


def nearest_far_line(plan, v, total, size):
    """下一卷的一句话方向（远层）。没有就明说是空白，不要编。"""
    nv = int(v) + 1
    start, end = volume_range(nv, size, total)
    if start > total:
        return ""
    info = (plan.get("volumes") or {}).get(str(nv)) or {}
    goal = str(info.get("goal") or "").strip()
    if goal:
        return "第 %d 卷（第 %d–%d 章）：%s" % (nv, start, end, goal)
    return ("第 %d 卷（第 %d–%d 章）：**尚未规划**（滚动规划：写到该卷前再定，"
            "现在只有方向、没有细节）" % (nv, start, end))


def render_context(n, total, cfg=None, proj=None, root="."):
    """给 stage3/stage4 的注入块：本卷详细卷纲 + 下一卷一句话。"""
    size = volume_size(cfg, proj)
    v = volume_of(n, size)
    start, end = volume_range(v, size, total)
    plan = load_plan(root)
    p = volume_path(v, root)
    lines = ["## 卷弧（本章属于第 %d 卷：第 %d–%d 章）" % (v, start, end)]
    if p.exists():
        body = read_text(p)
        parsed = parse_volume_outline(body)
        if parsed["goal"]:
            lines.append("**本卷目标**：" + parsed["goal"])
        if parsed["turns"]:
            lines.append("**本卷关键转折**：")
            lines += ["- " + t for t in parsed["turns"][:6]]
        if n in parsed["chapters"]:
            lines.append("**本章在本卷里的位置（卷纲原话）**：" + parsed["chapters"][n])
        lines.append("")
        lines.append("（卷纲全文见 %s。本章的情节推进以它为上限："
                     "可以补细节，不得越过它提前引爆本卷转折。）" % p.as_posix())
    else:
        lines.append("（**本卷卷纲尚未生成** —— 本章按全书大纲推进；"
                     "建议先跑 `python scripts/volume_plan.py --ensure`，"
                     "否则近层规划退化成一次性全书大纲。）")
    far = nearest_far_line(plan, v, total, size)
    if far:
        lines.append("")
        lines.append("**下一卷（远层，只有方向）**：" + far)
    # 叙事深化（2026-10-20，v0.7.2）：卷内的节奏形状。stage3 生成整批章大纲时
    # 要看到全卷段表（否则各章平推、高潮均匀撒）。存量卷纲没有深化节 → 空白，
    # 明确不注入（不给"暂无数据"占位）；essay/episodic 文体在 context_for 里早退。
    try:
        from utils import story_deepen as sd
        _full, _ = sd.context_for(n, total, cfg, proj, root)
        if _full:
            lines.append("")
            lines.append(_full)
    except Exception as e:                                    # noqa: BLE001
        print("[volume] 叙事深化块组装失败（本章将无深化上下文）: %s: %s"
              % (type(e).__name__, e))
    return "\n".join(lines)
