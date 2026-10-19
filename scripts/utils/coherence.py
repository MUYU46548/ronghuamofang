# -*- coding: utf-8 -*-
"""设定集 ↔ 大纲的重合度（方案 C，2026-10-19）。

## 它拦的是什么

2026-10-18 的现场：`examples/sample-book/` 的 `user_outline` 讲沈砚/程砧/机关术，
`materials/` 的 19 张卡却是潮神/鲸民那一套。stage1 全量归并后，设定集 16 个角色
只有 2 个属于大纲、18 个地点 **0 个**属于大纲 —— 模型拿别人的设定写这本书，
成品崩坏，而 `nfctl check` 与全流程门禁**全程绿灯**。

根因不是模型，是**没有任何判据检查「设定集与这本书的正文计划是否配套」**。
本模块就是那条判据：纯确定性、零 token、可复算。

## 判据（为什么是这两个）

不做中文 NER —— 那需要词典且会误判。改用**反查**：设定集里的每个专名，
由用户自己写的大纲来判定「这本书要不要用它」。

- **角色命中率**：设定集角色名中，在 `user_outline` 里出现过的比例
- **地点命中率**：同上
- **总命中率**：全部条目（角色/地点/势力/物品/概念/体系）的命中比例

判定分三档（`mode` 由 `gates.setting_coherence` 控制）：

| 情形 | 结论 |
|---|---|
| 大纲为空 | `skipped` —— 没有正文计划可比，系统自拟大纲时本就无从判断 |
| **角色 0 命中** 或 **地点 0 命中**（且该类别有条目） | `warn` / `block` —— 一本要写的书，主角和场地一个都对不上，必然是拿错了素材 |
| 总命中率 < `min_ratio`（默认 0.15，且条目数 ≥ 6） | `warn` / `block` |
| 其余 | `ok` |

**为什么默认 `warn` 而不是 `block`**：阈值是启发式，用户的大纲可能写得很抽象
（「一个落魄的匠人」不点名），硬拦会把正常流程卡死。`warn` 会打印横幅、
写 `data/setting/coherence.json`、并把结论挂到 `progress` 的 warning 上 ——
**可见**，但不改变既有流程。确认口径后可把配置改成 `block` 硬拦。

## 与 `_extract_world_context` 的分工

那个函数管「本章要用哪些条目」（按章大纲筛，写进 prompt）；本模块管
「整本设定集是不是这本书的」（整本大纲反查，决定要不要拦住流程）。
"""
import json
import re
from pathlib import Path

REPORT_PATH = "data/setting/coherence.json"
DEFAULT_MIN_RATIO = 0.15
MIN_TOTAL_FOR_RATIO = 6      # 条目太少时比例没有统计意义（2 个里对 1 个 = 50%）
FATAL_KINDS = ("characters", "locations")

KIND_LABELS = {
    "characters": "角色",
    "locations": "地点",
    "factions": "势力",
    "items": "物品",
    "concepts": "概念",
    "magic_system": "体系",
    "timeline": "时间线",
}


def _base(name):
    """去括号/点号后缀取根名（与 setting_schema.base_name 同口径，避免重依赖）。"""
    s = str(name or "").strip()
    s = re.sub(r"[（(\[【].*?[)）\]】]", "", s).strip()
    s = re.sub(r"[\s·・]+.*$", "", s).strip()
    return s


def _names_of(entry):
    """一条设定条目 → 可用于反查的名字（正名 + 别名 + 根名）。"""
    if isinstance(entry, str):
        return [entry] if entry.strip() else []
    if not isinstance(entry, dict):
        return []
    out = []
    for key in ("name", "title", "正式名称"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    aliases = entry.get("aliases") or entry.get("别名") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    for a in aliases:
        if isinstance(a, str) and a.strip() and a.strip() not in ("无", "-"):
            out.append(a.strip())
    extra = []
    for n in out:
        b = _base(n)
        if b and b != n:
            extra.append(b)
    for n in extra:
        if n not in out:
            out.append(n)
    # 去重保序
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def collect_names(setting):
    """设定集 → {类别: [名字...]}。

    `world.all` 一律跳过：它是各分类的并集（历史上同一批数据被展开两遍，
    计入会让命中率虚高/分母翻倍）。非列表值（如 `world.地理: "一段描述"`）也跳过。
    """
    setting = setting if isinstance(setting, dict) else {}
    kinds = {}

    chars = setting.get("characters") or []
    if isinstance(chars, list):
        names = []
        for c in chars:
            names.extend(_names_of(c))
        if names:
            kinds["characters"] = names

    world = setting.get("world") or {}
    if isinstance(world, dict):
        for key, val in world.items():
            if key in ("all", "_meta") or not isinstance(val, list) or not val:
                continue
            names = []
            for entry in val:
                names.extend(_names_of(entry))
            if names:
                kinds[key] = names

    # 时间线也算一条（条目通常是 {"time": ..., "event": ...}，没有名字 —— 只在有 name 时计）
    timeline = setting.get("timeline") or []
    if isinstance(timeline, list):
        names = []
        for t in timeline:
            names.extend(_names_of(t))
        if names:
            kinds["timeline"] = names

    return kinds


def _hit(name, outline):
    """名字是否出现在大纲里。单字名不算（「甲」「乙」这类会假命中）。"""
    if not name or len(name) < 2:
        return False
    return name in outline


def evaluate(setting, outline, mode="warn", min_ratio=DEFAULT_MIN_RATIO):
    """算重合度并给结论。mode: off/warn/block（仅影响 verdict，不影响计算）。"""
    mode = str(mode or "warn").lower()
    outline = (outline or "").strip()
    kinds = collect_names(setting)

    report = {
        "mode": mode,
        "min_ratio": float(min_ratio),
        "outline_chars": len(outline),
        "checked": False,
        "verdict": "skipped",
        "kinds": {},
        "total": 0,
        "hits": 0,
        "ratio": 0.0,
        "fatal": [],
        "reasons": [],
    }
    if mode == "off":
        report["reasons"].append("gates.setting_coherence=off，未检查")
        return report
    if not outline:
        report["reasons"].append(
            "user_outline 为空 —— 系统自拟大纲时无从反查，跳过（这不代表设定集没问题）")
        return report
    if not kinds:
        report["reasons"].append("设定集里没有可反查的条目（characters/world 全空）")
        return report

    total = hits = 0
    for kind, names in kinds.items():
        label = KIND_LABELS.get(kind, kind)
        uniq = list(dict.fromkeys(names))
        k_hits = [n for n in uniq if _hit(n, outline)]
        k_total = len(uniq)
        total += k_total
        hits += len(k_hits)
        missed = [n for n in uniq if n not in k_hits]
        report["kinds"][kind] = {
            "label": label,
            "total": k_total,
            "hits": len(k_hits),
            "ratio": round(len(k_hits) / k_total, 3) if k_total else 0.0,
            "hit_samples": k_hits[:8],
            "missed_samples": missed[:8],
        }
        if kind in FATAL_KINDS and k_total and not k_hits:
            report["fatal"].append(
                "设定集的 %d 个%s，没有一个出现在大纲里" % (k_total, label))

    report["checked"] = True
    report["total"] = total
    report["hits"] = hits
    report["ratio"] = round(hits / total, 3) if total else 0.0

    bad = bool(report["fatal"])
    if not bad and total >= MIN_TOTAL_FOR_RATIO and report["ratio"] < float(min_ratio):
        bad = True
        report["reasons"].append(
            "总命中率 %.0f%% 低于下限 %.0f%%（%d/%d 个条目的名字出现在大纲里）"
            % (report["ratio"] * 100, float(min_ratio) * 100, hits, total))
    for f in report["fatal"]:
        report["reasons"].append(f)

    if not bad:
        report["verdict"] = "ok"
    else:
        report["verdict"] = "block" if mode == "block" else "warn"
        report["reasons"].append(
            "多半是拿错了素材：素材卡与这本书的世界观不配套 → stage1 会归并出一份"
            "不属于这本书的设定集，而后续阶段不会报错。请核对 materials/raw 与 "
            "config/project.yaml 的 user_outline 是否同一本书")
    return report


def summary_line(report):
    """一行摘要（控制台/`progress.warning` 用）。"""
    if not report.get("checked"):
        return "设定集重合度：跳过（%s）" % "；".join(report.get("reasons") or ["未检查"])
    parts = []
    for kind, d in report.get("kinds", {}).items():
        parts.append("%s %d/%d" % (d["label"], d["hits"], d["total"]))
    return ("设定集重合度：总 %d/%d（%.0f%%）· %s"
            % (report["hits"], report["total"], report["ratio"] * 100,
               "，".join(parts)))


def write_report(report, path=REPORT_PATH):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return str(p)


def load_report(path=REPORT_PATH):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None
