# -*- coding: utf-8 -*-
"""设定集摘要（大纲阶段的内联 canon）—— 解决"大纲阶段只看得到一个文件路径"。

## 为什么（台账三·「设定库闭环：大纲注入」）

stage2 的输入一直是 `- 设定集: <绝对路径>`，让模型自己 read_file。模型读不读、
读多少、读哪几条，全不受控。实测设定集 69 KB（含 hundreds 条 vault 词条）时，
模型通常只翻到前十几个角色就开始写大纲 —— 结果是**伏笔和时间线从没进过大纲**，
到了 stage4 才第一次被看见，那时结构已经定了。

本模块把设定集压成一份**有预算、有结构**的摘要，直接内联进 stage2 任务：

| 段 | 内容 | 为什么必须在 |
|---|---|---|
| 角色 | 名字 / 身份 / 性格（≤3）/ 关系（≤3） | 大纲的"谁推动什么"靠它 |
| 世界 | 地点 / 势力（名 + 一句描述） | 场景调度与冲突来源 |
| 伏笔 | plot_fragments 全量（按预算） | 大纲阶段就要决定哪条在哪一卷兑现 |
| 时间线 | timeline 全量（按预算） | 起承转合的"时间跨度" |
| vault 补充 | 设定集里**还没有**的 vault 词条（名 + 摘要） | 词条没归并进来时，大纲也不该视而不见 |

## 判据

- **预算硬裁**，且裁掉多少条**必须写出来**（"另有 N 条未内联"）—— 静默截断
  会让人以为设定集只有这么多。
- 相同名字只出现一次（角色 / 地点 / vault 补充之间去重）。
- 读不到 / 格式坏 → 返回空块 + 原因，**不抛异常**（大纲不该因设定集小问题而停），
  但调用方要打印出来。
"""
import json
from pathlib import Path

from utils.file_io import read_text
from utils.setting_schema import base_name

DEFAULT_BUDGET = 4200


def _one_line(v, limit=60):
    s = str(v or "").replace("\n", " ").strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def _traits(c, n=3):
    t = c.get("traits")
    if isinstance(t, list):
        vals = [str(x).strip() for x in t if str(x).strip()]
    else:
        vals = [str(t).strip()] if str(t or "").strip() else []
    return vals[:n]


def _relations(c, n=3):
    rel = c.get("relations")
    out = []
    if isinstance(rel, dict):
        for k, v in rel.items():
            desc = v.get("desc") if isinstance(v, dict) else v
            out.append("%s（%s）" % (k, _one_line(desc, 24)) if desc else str(k))
    elif isinstance(rel, list):
        for r in rel:
            if isinstance(r, dict):
                who = r.get("name") or r.get("to") or r.get("target")
                desc = r.get("desc") or r.get("relation") or r.get("type")
                if who:
                    out.append("%s（%s）" % (who, _one_line(desc, 24)) if desc else str(who))
            elif str(r).strip():
                out.append(str(r).strip())
    return out[:n]


def _world_items(setting):
    """地点 / 势力：兼容 {locations:[...]} 与 {地理: "..."} 两种历史形态。"""
    w = setting.get("world")
    out = []
    if isinstance(w, dict):
        for key in ("locations", "factions"):
            for it in w.get(key) or []:
                if isinstance(it, dict) and it.get("name"):
                    out.append((str(it["name"]), _one_line(it.get("snippet") or it.get("desc")
                                                           or it.get("description"), 50)))
        for k, v in w.items():
            if k in ("locations", "factions", "concepts", "all"):
                continue
            if isinstance(v, str) and v.strip():
                out.append((str(k), _one_line(v, 60)))
    return out


def _vault_extra(vault_path, known, budget_lines=12):
    """vault 里**尚未进入设定集**的词条（名 + 摘要），只列前若干条。"""
    if not vault_path:
        return [], 0
    try:
        from obsidian_bridge import scan_vault
        data = scan_vault(vault_path)
    except Exception:                                          # noqa: BLE001
        return [], 0
    extra = []
    for e in (data.get("characters") or []) + (data.get("world") or []):
        name = str((e or {}).get("name") or "").strip()
        if not name or base_name(name) in known:
            continue
        known.add(base_name(name))
        extra.append("%s：%s" % (name, _one_line(e.get("snippet"), 50)))
    return extra[:budget_lines], max(0, len(extra) - budget_lines)


def build(setting_path="data/setting/setting.json", vault_path=None,
          budget=DEFAULT_BUDGET):
    """返回 {"block", "stats", "error"}。任何异常都变成 error 字段，不抛。"""
    p = Path(setting_path)
    stats = {"characters": 0, "world": 0, "fragments": 0, "timeline": 0,
             "vault_extra": 0, "trimmed": 0, "chars": 0}
    if not p.exists():
        return {"block": "", "stats": stats,
                "error": "设定集不存在：%s（大纲阶段将只依据素材清单）" % p.as_posix()}
    try:
        setting = json.loads(read_text(p))
    except Exception as e:                                     # noqa: BLE001
        return {"block": "", "stats": stats, "error": "设定集解析失败：%r" % (e,)}
    if not isinstance(setting, dict):
        return {"block": "", "stats": stats, "error": "设定集顶层不是对象"}

    lines = ["## 设定集摘要（已内联；**不要**再逐条 read_file 去翻 setting.json）"]
    known = set()

    chars = [c for c in (setting.get("characters") or []) if isinstance(c, dict) and c.get("name")]
    if chars:
        lines.append("")
        lines.append("### 角色（%d）" % len(chars))
        for c in chars:
            known.add(base_name(str(c["name"])))
            bits = [_one_line(c.get("role") or c.get("description"), 40)]
            tr = _traits(c)
            if tr:
                bits.append("性格：" + "、".join(tr))
            rel = _relations(c)
            if rel:
                bits.append("关系：" + "；".join(rel))
            lock = "【locked】" if str(c.get("locked")).lower() in ("true", "1", "yes") else ""
            lines.append("- %s%s：%s" % (lock, c["name"], "；".join(b for b in bits if b)))
            stats["characters"] += 1

    world = _world_items(setting)
    if world:
        lines.append("")
        lines.append("### 地点 / 势力（%d）" % len(world))
        for name, desc in world:
            known.add(base_name(name))
            lines.append("- %s：%s" % (name, desc) if desc else "- %s" % name)
            stats["world"] += 1

    frags = [str(f).strip() for f in (setting.get("plot_fragments") or []) if str(f).strip()]
    if frags:
        lines.append("")
        lines.append("### 伏笔 / 情节碎片（%d）——大纲必须决定哪一条在哪一卷兑现" % len(frags))
        lines += ["- " + _one_line(f, 120) for f in frags]
        stats["fragments"] = len(frags)

    tl = [str(t).strip() for t in (setting.get("timeline") or []) if str(t).strip()]
    if tl:
        lines.append("")
        lines.append("### 时间线（%d）" % len(tl))
        lines += ["- " + _one_line(t, 120) for t in tl]
        stats["timeline"] = len(tl)

    extra, more = _vault_extra(vault_path, known)
    if extra:
        lines.append("")
        lines.append("### vault 里尚未归并的词条（%d，仅供大纲参考）" % len(extra))
        lines += ["- " + e for e in extra]
        stats["vault_extra"] = len(extra)
    if more:
        stats["trimmed"] += more

    # 预算硬裁：从尾部砍（尾部是 vault 补充 + 时间线，角色在最前，最不该丢）
    body = "\n".join(lines)
    if len(body) > budget:
        keep, size = [], 0
        dropped = 0
        for ln in lines:
            if size + len(ln) + 1 > budget:
                dropped += 1
                continue
            keep.append(ln)
            size += len(ln) + 1
        keep.append("（另有 %d 行未内联：超出摘要预算 %d 字；"
                    "完整设定集见 %s）" % (dropped, budget, p.as_posix()))
        stats["trimmed"] += dropped
        body = "\n".join(keep)
    stats["chars"] = len(body)
    return {"block": body, "stats": stats, "error": ""}
