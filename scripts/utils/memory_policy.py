# -*- coding: utf-8 -*-
"""上下文记忆策略（memory policy）—— 把「把能塞的都塞进去」换成**分层 + 按需**。

## 为什么（台账三·「上下文固定拼接」）

旧 stage4 的上下文是「设定集路径 + 滚动摘要路径 + 上一章尾 + 范文」一锅端：
滚动摘要给的是**路径**而不是内容（模型读多少不受控），设定的相关条目靠
`_extract_world_context` 按大纲筛，但**没有任何预算**。40 章还能过，
200 章时要么摘要无限膨胀、要么模型把预算全吃在历史上，正文写不出来。

## 三层结构（每层职责不同，不许互相顶替）

| 层 | 内容 | 预算纪律 |
|---|---|---|
| **L1 固定层** | 本章大纲 / 本章角色卡 / 上一章尾 / 硬性要求 | **不裁** —— 裁了就没法写。仍由 stage4 既有块提供 |
| **L2 滚动层** | 全书摘要 + 最近 N 章摘要 | 按 `rolling_chars` 裁，**裁掉的章号必须写出来** |
| **L3 召回层** | 四维召回：伏笔 / 角色 / 状态 / 关系 | 按 `recall_chars` 裁 + top-K，**每条都带「为什么被召回」** |

## L3 四维（对标 ainovel-cli 的四维推荐）

- `foreshadow` 伏笔：设定集 `plot_fragments` 里与本章有词重合的碎片；
- `character` 角色：滚动摘要里提到**本章涉及角色**的章（说明他上次在干什么）；
- `state` 状态：一致性账本（`story_state.json`）里**本章之前**发生的死亡/物品去向/时间锚；
- `relation` 关系：设定集角色 `relations` 里涉及本章角色的条目。

## 判据（确定性、零 token）

相关度 = 中文二元组重合数 + 3 × 命中的设定专名数。低于阈值（2 且无专名）的候选
**不进**召回块 —— 宁可少给，也不把无关历史灌进提示词稀释注意力。

**不许静默**：裁掉的章、丢掉的候选、召回到 0 条，三种情况都必须在块里/统计里写明。
"看起来有上下文，其实是空的"是本项目反复踩过的坑。
"""
import json
import re
from pathlib import Path

from utils.file_io import read_text
from utils.summary_chain import load_rolling

DEFAULT_BUDGET = {
    "rolling_chars": 2600,     # L2 预算（字符；中文 1 字 ≈ 1 token 量级）
    "recall_chars": 2400,      # L3 预算
    "rolling_window": 8,       # L2 最多取最近几章（预算内还要再裁一次）
    "recall_top_k": 6,         # L3 最多几条
    # 相关度门槛（2026-10-20 实测调过）：只看 bigram 重合数会把"没有""一个"这类
    # 虚词组合算成相关 —— 中文里任意两句话重合 2-3 个 bigram 是常态，实测
    # 「他在屋里擦桌子」也能召回到「怀表的来历」碎片。所以要求**两条同时满足**：
    # 重合数 >= 4 且重合率 >= 20%；专名命中则直接放行（人名/物品名才是真信号）。
    "recall_min_terms": 4,
}
DEFAULT_MIN_RATIO = 0.2
DIM_LABEL = {
    "foreshadow": "伏笔",
    "character": "角色",
    "state": "状态",
    "relation": "关系",
}
_NAMES_CAP = 80          # 专名数量上限（防御性：设定集异常大时别做 O(n*m)）


def _budget(cfg):
    b = dict(DEFAULT_BUDGET)
    mem = ((cfg or {}).get("context") or {}).get("memory") or {}
    for k in DEFAULT_BUDGET:
        if k in mem:
            try:
                b[k] = int(mem[k])
            except (TypeError, ValueError):
                pass                      # 配置写坏就回落默认，不静默改成 0
    return b


def _grams(text):
    """中文二元组 + 英文/数字整词，用于确定性相关度。"""
    out = set()
    for seg in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", str(text or "")):
        if re.match(r"[A-Za-z0-9_]", seg):
            if len(seg) >= 2:
                out.add(seg.lower())
        else:
            for i in range(len(seg) - 1):
                out.add(seg[i:i + 2])
    return out


def _score(text, outline_text, outline_grams, names):
    """返回 (相关度, 命中的专名, 重合 bigram 数, 重合率)。

    专名权重 3 —— 命中一个人名比撞十个 bigram 更有意义。

    ⚠️ 专名必须**同时出现在候选和本章大纲里**才算命中（2026-10-20 实测踩到）：
    只判"专名在候选里"会让设定集里**每一个带人名的碎片**无差别召回 ——
    "怀表的来历"里含"怀表"就命中，哪怕本章大纲在擦桌子。那样召回层等于全量灌入。
    重合率同理用于过滤"两句中文都含'没有'"这类假相关。
    """
    grams = _grams(text)
    hit = [x for x in names if x and x in text and x in outline_text]
    inter = len(grams & outline_grams)
    ratio = (inter / len(grams)) if grams else 0.0
    return inter + 3 * len(hit), hit, inter, ratio


def _load_json(path):
    p = Path(path) if path else None
    if not p or not p.exists():
        return {}
    try:
        return json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return {}


def _char_names(setting):
    names = []
    for c in setting.get("characters") or []:
        if isinstance(c, dict):
            n = str(c.get("name") or "").strip()
        else:
            n = str(c or "").strip()
        if n and n not in names:
            names.append(n)
    return names


def _item_names(setting):
    names = []
    for it in setting.get("items") or []:
        n = str((it or {}).get("name") if isinstance(it, dict) else it).strip()
        if n and n not in names:
            names.append(n)
    return names


def _frag_text(frag):
    if isinstance(frag, str):
        return frag.strip()
    if isinstance(frag, dict):
        for k in ("text", "detail", "desc", "summary", "title", "name"):
            v = frag.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _relation_lines(setting, involved):
    """设定集里涉及本章角色的关系条目 → [(角色名, 关系文本)]。"""
    out = []
    for c in setting.get("characters") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if name not in involved:
            continue
        rel = c.get("relations")
        if isinstance(rel, dict):
            for other, desc in rel.items():
                if isinstance(desc, dict):
                    desc = desc.get("desc") or desc.get("type") or ""
                if str(desc).strip():
                    out.append((name, "%s → %s：%s" % (name, other, str(desc).strip())))
        elif isinstance(rel, list):
            for r in rel:
                if isinstance(r, dict):
                    other = r.get("name") or r.get("to") or r.get("target") or ""
                    desc = r.get("desc") or r.get("type") or r.get("relation") or ""
                    if other:
                        out.append((name, "%s → %s：%s" % (name, other, str(desc).strip())))
                elif isinstance(r, str) and r.strip():
                    out.append((name, "%s：%s" % (name, r.strip())))
    return out


def _state_lines(state, n, involved, items):
    """一致性账本里**本章之前**的事件（未来事件不召回 —— 那是剧透，不是记忆）。"""
    out = []
    for e in state.get("events") or []:
        if not isinstance(e, dict):
            continue
        try:
            ch = int(e.get("chapter"))
        except (TypeError, ValueError):
            continue
        if ch >= n:
            continue
        who = str(e.get("who") or "")
        item = str(e.get("item") or "")
        if who and who not in involved and item and item not in items:
            continue
        typ = str(e.get("type") or "")
        ev = str(e.get("evidence") or "").strip()
        if typ == "death":
            out.append((ch, "%s 在第%d章死亡（%s）" % (who, ch, ev[:40])))
        elif typ == "lose_item":
            out.append((ch, "%s 在第%d章失去「%s」（%s）" % (who, ch, item, ev[:40])))
        elif typ == "hold_item":
            out.append((ch, "%s 在第%d章持有「%s」" % (who, ch, item)))
        elif typ == "time":
            out.append((ch, "第%d章时间锚：%s 年" % (ch, e.get("year"))))
        elif typ == "unknown_name":
            continue                      # 错名提示是给审校看的，不该进写作上下文
    return out


def _recall(n, outline_text, setting, state, rolling, budget,
            min_ratio=DEFAULT_MIN_RATIO):
    """四维召回 → (kept, stats)。kept 每条带 dim/score/why/chapter。"""
    names = _char_names(setting) + _item_names(setting)
    involved = [x for x in _char_names(setting) if x and x in outline_text]
    items = [x for x in _item_names(setting) if x and x in outline_text]
    outline_grams = _grams(outline_text)
    cands = []

    # 1) 伏笔（设定集碎片）
    for frag in setting.get("plot_fragments") or []:
        t = _frag_text(frag)
        if t:
            cands.append({"dim": "foreshadow", "text": t, "chapter": None})

    # 2) 角色（滚动摘要里提到本章角色的章；最近 3 章排除 —— 那段由上一章尾巴覆盖）
    recent_cut = max(0, n - 3)
    for ch, summ in (rolling.get("chapters") or {}).items():
        if not isinstance(summ, str) or not summ.strip():
            continue
        if int(ch) >= n or int(ch) > recent_cut:
            continue
        hit = [x for x in involved if x in summ]
        if hit:
            cands.append({"dim": "character", "chapter": int(ch),
                          "text": "第%d章：%s" % (ch, summ.strip()[:140]),
                          "_hit": hit})

    # 3) 状态（一致性账本）
    for ch, t in _state_lines(state, n, involved, items):
        cands.append({"dim": "state", "chapter": ch, "text": t})

    # 4) 关系
    for name, t in _relation_lines(setting, involved):
        cands.append({"dim": "relation", "text": t})

    kept, dropped, seen = [], 0, set()
    for c in cands:
        key = re.sub(r"\s+", "", c["text"])[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        score, hit, inter, ratio = _score(c["text"], outline_text, outline_grams, names)
        hit = list(dict.fromkeys((c.get("_hit") or []) + hit))
        # 相关性：专名命中直接放行；否则要求重合数 + 重合率**双达标**
        if not hit and not (inter >= budget["recall_min_terms"] and ratio >= min_ratio):
            dropped += 1
            continue
        # 时间近度：状态/角色类越近越相关（每 10 章衰减 1 分，最多 +3）
        if c.get("chapter"):
            score += max(0, 3 - max(0, n - int(c["chapter"])) // 10)
        c2 = {k: v for k, v in c.items() if not k.startswith("_")}
        c2["score"] = round(score, 2)
        c2["why"] = "命中：" + ("、".join(hit[:4]) if hit else "用词重合")
        kept.append(c2)

    # 排序：分数优先；同分取章号大的（更近的记忆先给）
    kept.sort(key=lambda x: (x["score"], x.get("chapter") or 0), reverse=True)
    head, size = [], 0
    for c in kept:
        line_len = len(c["text"]) + 40
        if len(head) >= budget["recall_top_k"]:
            dropped += 1
            continue
        if size + line_len > budget["recall_chars"]:
            dropped += 1
            continue
        head.append(c)
        size += line_len

    by_dim = {}
    for c in head:
        by_dim[c["dim"]] = by_dim.get(c["dim"], 0) + 1
    stats = {
        "candidates": len(cands),
        "recalled": len(head),
        "dropped": dropped,
        "by_dim": by_dim,
        "chars": size,
    }
    return head, stats


def _render_rolling(rolling, n, budget):
    """L2：全书摘要 + 最近 N 章。裁掉的章号**必须**写出来。"""
    g = (rolling.get("global_summary") or "").strip()
    chapters = {int(k): v for k, v in (rolling.get("chapters") or {}).items()
                if str(k).isdigit() and v}
    parts, size, kept_no = [], 0, []
    if g:
        g_txt = g if len(g) <= 600 else g[:600].rstrip() + "…"
        parts.append("【全书】" + g_txt)
        size += len(g_txt) + 6
    for ch in sorted(chapters, reverse=True):
        if ch >= n:
            continue                      # 未来的章摘要是剧透，不注入
        if len(kept_no) >= budget["rolling_window"]:
            break
        txt = chapters[ch].strip()
        if len(txt) > 300:
            txt = txt[:300].rstrip() + "…"
        line = "【第%d章】%s" % (ch, txt)
        if size + len(line) > budget["rolling_chars"]:
            break
        parts.append(line)
        kept_no.append(ch)
        size += len(line) + 1
    all_past = [c for c in sorted(chapters, reverse=True) if c < n]
    omitted = [c for c in all_past if c not in kept_no]
    if omitted:
        # 省略说明单独成行：让模型知道"更早的章节存在但没给你"，而不是以为故事从第 N-k 章开始
        parts.append("（更早章节摘要未注入：第 %d–%d 章，共 %d 章；"
                     "属记忆预算裁剪，不是全书只有这些章）"
                     % (min(omitted), max(omitted), len(omitted)))
    stats = {"chapters": kept_no, "omitted": omitted, "chars": size}
    return "\n\n".join(parts), stats


def build_memory(n, outline_text, setting_path=None, rolling_path=None,
                 state_path=None, cfg=None):
    """组装 L2 + L3，返回 {block, rolling, recall, stats}。

    任何一层缺失都不抛异常：写作不该因为"没有历史"而停。但**缺什么要说**。
    """
    budget = _budget(cfg)
    setting = _load_json(setting_path)
    state = _load_json(state_path)
    rolling = {"global_summary": "", "chapters": {}}
    if rolling_path and Path(rolling_path).exists():
        try:
            rolling = load_rolling(rolling_path)
        except Exception:                                      # noqa: BLE001
            rolling = {"global_summary": "", "chapters": {}}

    l2, l2_stats = _render_rolling(rolling, n, budget)
    min_ratio = DEFAULT_MIN_RATIO
    mem_cfg = ((cfg or {}).get("context") or {}).get("memory") or {}
    if mem_cfg.get("recall_min_ratio") is not None:
        try:
            min_ratio = float(mem_cfg["recall_min_ratio"])
        except (TypeError, ValueError):
            min_ratio = DEFAULT_MIN_RATIO      # 写坏就回落，不静默变成 0（等于不过滤）
    recalled, l3_stats = _recall(n, outline_text or "", setting, state, rolling,
                                 budget, min_ratio)

    lines = ["## 记忆上下文（L2 滚动 + L3 召回）", ""]
    lines.append("### L2 滚动记忆（全书摘要 + 最近章节）")
    lines.append(l2 if l2 else "（没有滚动摘要：这是前几章，或摘要尚未生成）")
    lines.append("")
    lines.append("### L3 相关历史（四维召回：伏笔 / 角色 / 状态 / 关系）")
    if recalled:
        lines.append("按「与本章大纲的相关度」选出，共 %d 条；括号里是它为什么在这里。"
                     % len(recalled))
        for c in recalled:
            lines.append("- [%s] %s（%s）" % (DIM_LABEL.get(c["dim"], c["dim"]),
                                             c["text"], c["why"]))
        if l3_stats["dropped"]:
            lines.append("（另有 %d 条相关历史因预算/top-K 未注入）" % l3_stats["dropped"])
    else:
        lines.append("（本章未召回到相关历史条目 —— 不是漏了，是没有与本章大纲相关且够近的旧事）")

    stats = {
        "n": n,
        "rolling": l2_stats,
        "recall": l3_stats,
        "budget": budget,
        "chars": len("\n".join(lines)),
    }
    return {"block": "\n".join(lines), "rolling": l2, "recall": recalled, "stats": stats}


def main(argv=None):
    """命令行自检：python scripts/utils/memory_policy.py <章号> [大纲文件]"""
    import argparse
    ap = argparse.ArgumentParser(description="查看某一章会注入什么记忆上下文")
    ap.add_argument("n", type=int)
    ap.add_argument("--outline", default="")
    ap.add_argument("--setting", default="data/setting/setting.json")
    ap.add_argument("--rolling", default="data/summaries/rolling.md")
    ap.add_argument("--state", default="data/state/story_state.json")
    a = ap.parse_args(argv)
    outline = read_text(a.outline) if a.outline and Path(a.outline).exists() else ""
    r = build_memory(a.n, outline, a.setting, a.rolling, a.state)
    print(r["block"])
    print("\n--- stats ---")
    print(json.dumps(r["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
