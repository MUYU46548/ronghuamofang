# -*- coding: utf-8 -*-
"""轮次语义：一轮 = 一张判据表（R5，2026-11-16）。

## 为什么要"轮次"这个东西

OB37 走了四轮，每轮的目标、判据、停止条件都不一样：

| 轮 | 目标 | 判据 |
|---|---|---|
| 骨架 | 事件全落地 | 每章节都被覆盖 |
| 语体 | 成句中文 | 碎段比例、问答阶梯 |
| 扩写 | 节均达标 | 单节字数、章字数 |
| 润色 | 去 AI 腔 | 连排对白、短对白比、段末短句比、说/问密度 |

而墨坊只有"重写 / 润色"两个动作，**没有"这一轮在验什么"的数据**。
结果是每一轮都要靠作者一句话推翻上一轮（回执 §二 的关键教训）。

本模块把轮次做成数据：`data/state/rounds.json` 里一条轮次 = 名字 + 目标 + 判据表；
`evaluate()` 拿判据表去量一章，回答"这一轮达标了吗、差在哪几节"。

## 判据键（可用的量）

| 键 | 含义 | 来源 |
|---|---|---|
| `sections_covered` | 声明的节里写到的比例（0–1） | `utils/sections` |
| `section_min` | 最短一节的汉字数 | 同上 |
| `chapter_min` | 本章汉字数 | `utils/word_count` |
| `dialogue_runs_per_chapter` | 连排 ≥3 段对白 处数 | `utils/prose_metrics` |
| `short_dialogue_ratio` | ≤12 字对白占比 | 同上 |
| `end_short_ratio` | 段末 ≤12 字占比 | 同上 |
| `say_ask_per_10k` | “说/问”每万字 | 同上 |
| `short_para_ratio` | 碎段（≤20 汉字）占比 | 同上 |

表达式写法：`"<0.12"`、`"<=2"`、`">=15000"`、`"2000"`（无运算符 = `>=`）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REL_PATH = Path("data") / "state" / "rounds.json"

#: 出厂轮次（照抄 OB37 的四轮；数值是那本书的实测水位，作者可改）。
DEFAULT_ROUNDS = [
    {"name": "骨架", "goal": "事件全落地",
     "checks": {"sections_covered": "1.0", "chapter_min": "3000"}},
    {"name": "语体", "goal": "成句中文",
     "checks": {"short_para_ratio": "<0.12", "dialogue_runs_per_chapter": "<=2"}},
    {"name": "扩写", "goal": "节均达标",
     "checks": {"section_min": "2000", "chapter_min": "15000"}},
    {"name": "润色", "goal": "去 AI 腔",
     "checks": {"dialogue_runs_per_chapter": "<=8", "short_dialogue_ratio": "<0.5",
                "end_short_ratio": "<0.25", "say_ask_per_10k": "<180"}},
]

KNOWN_CHECKS = ("sections_covered", "section_min", "chapter_min",
                "dialogue_runs_per_chapter", "short_dialogue_ratio",
                "end_short_ratio", "say_ask_per_10k", "short_para_ratio")

_OPS = (("<=", lambda a, b: a <= b), (">=", lambda a, b: a >= b),
        ("<", lambda a, b: a < b), (">", lambda a, b: a > b))


# ---------------------------------------------------------------- 读写
def path(root="."):
    return Path(root) / REL_PATH


def load(root="."):
    """读轮次表。文件不存在 → 出厂四轮（**只读回退，不落盘**）。"""
    p = path(root)
    if not p.exists():
        return {"rounds": [dict(r) for r in DEFAULT_ROUNDS], "current": DEFAULT_ROUNDS[0]["name"],
                "seeded": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rounds": [dict(r) for r in DEFAULT_ROUNDS], "current": DEFAULT_ROUNDS[0]["name"],
                "seeded": False, "error": "rounds.json 读不出（用出厂四轮）"}
    rounds = data.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        data["rounds"] = [dict(r) for r in DEFAULT_ROUNDS]
    data.setdefault("current", data["rounds"][0].get("name"))
    data["seeded"] = True
    return data


def save(rounds, current=None, root="."):
    """写回轮次表（用户资产，覆盖前备份到 data/state/history/）。"""
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("rounds 必须是非空列表")
    for r in rounds:
        _validate_round(r)
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        import time
        hist = p.parent / "history"
        hist.mkdir(parents=True, exist_ok=True)
        (hist / ("rounds_%s.json" % time.strftime("%Y%m%d_%H%M%S"))).write_bytes(p.read_bytes())
    data = {"rounds": rounds, "current": current or rounds[0].get("name")}
    from utils.file_io import write_text
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    return data


def _validate_round(r):
    if not isinstance(r, dict) or not str(r.get("name") or "").strip():
        raise ValueError("每一轮都必须有 name")
    checks = r.get("checks") or {}
    if not isinstance(checks, dict):
        raise ValueError("轮次 %s 的 checks 必须是对象" % r.get("name"))
    unknown = [k for k in checks if k not in KNOWN_CHECKS]
    if unknown:
        raise ValueError("轮次 %s 里有不认识的判据键：%s（可用：%s）"
                         % (r.get("name"), "、".join(unknown), "、".join(KNOWN_CHECKS)))
    for k, expr in checks.items():
        if not isinstance(expr, str) or not _parse_expr(expr):
            raise ValueError("轮次 %s 的判据 %s 表达式看不懂：%r（例：\"<=8\"、\"2000\"）"
                             % (r.get("name"), k, expr))


def current(root="."):
    data = load(root)
    name = data.get("current")
    for r in data["rounds"]:
        if r.get("name") == name:
            return r
    return data["rounds"][0]


def set_current(name, root="."):
    data = load(root)
    names = [r.get("name") for r in data["rounds"]]
    if name not in names:
        raise ValueError("没有这一轮：%s（现有：%s）" % (name, "、".join(names)))
    save(data["rounds"], name, root)
    return name


# ---------------------------------------------------------------- 求值
def _parse_expr(expr):
    s = str(expr or "").strip()
    for op, fn in _OPS:
        if s.startswith(op):
            try:
                return fn, float(s[len(op):].strip())
            except ValueError:
                return None
    try:
        return (lambda a, b: a >= b), float(s)
    except ValueError:
        return None


def _measure(chapter, root="."):
    """取这一章的量（优先读已落盘的 metrics/sections，没有就现算）。"""
    from pathlib import Path as _P
    from utils.word_count import count_cn_words
    from utils import prose_metrics, sections as sec

    body = ""
    for layer in ("refined", "checked", "raw"):
        p = _P(root) / "data" / "chapters" / layer / ("%02d.md" % int(chapter))
        if p.exists():
            try:
                body = p.read_text(encoding="utf-8")
            except OSError:
                body = ""
            break

    rep = prose_metrics.measure_and_save(chapter, body, None, root) if body else None
    style = (rep or {}).get("style") or {}
    outline = ""
    op = _P(root) / "data" / "outline" / "chapters" / ("%02d.md" % int(chapter))
    if op.exists():
        try:
            outline = op.read_text(encoding="utf-8")
        except OSError:
            outline = ""
    sec_rep = sec.measure(chapter, body, outline, None)
    rows = [r for r in (sec_rep.get("sections") or []) if not r.get("undeclared")]
    covered = 1.0
    if sec_rep.get("declared") and rows:
        covered = round(sum(1 for r in rows if (r.get("hanzi") or 0) > 0) / len(rows), 3)
    # ⚠️ 字数口径必须与 metrics 面板一致：`count_cn_words(整份文件)` 会把章标题与
    # `<!-- quality/summary -->` 注释一起算进去（实测同一章 240 vs 228），
    # 界面上两个数字打架 = 作者不知道该信哪个（2026-11-16 真机目检抓到）。
    hanzi = style.get("hanzi") if rep else count_cn_words(body)
    return {
        "chapter_min": hanzi,
        "sections_covered": covered,
        "section_min": min([r.get("hanzi") or 0 for r in rows], default=0),
        "dialogue_runs_per_chapter": style.get("dialogue_runs", 0),
        "short_dialogue_ratio": style.get("short_dialogue_ratio", 0),
        "end_short_ratio": style.get("end_short_ratio", 0),
        "say_ask_per_10k": style.get("say_ask_per_10k", 0),
        "short_para_ratio": style.get("short_paragraph_ratio", 0),
        "_sections": sec_rep,
    }


def evaluate(chapter, round_name=None, root="."):
    """量一章，按（当前或指定的）轮次判据逐条求值。

    返回 `{chapter, round, goal, checks: [{key, expr, actual, ok, gap}], ok, gaps}`
    —— "这一轮达标了吗、差在哪" 的直接答案。
    """
    data = load(root)
    if round_name:
        rnd = next((r for r in data["rounds"] if r.get("name") == round_name), None)
        if rnd is None:
            raise ValueError("没有这一轮：%s（现有：%s）"
                             % (round_name, "、".join(r.get("name") for r in data["rounds"])))
    else:
        rnd = current(root)
    actual = _measure(chapter, root)
    checks = []
    for key, expr in (rnd.get("checks") or {}).items():
        parsed = _parse_expr(expr)
        value = actual.get(key, 0)
        ok = bool(parsed and parsed[0](float(value or 0), parsed[1]))
        item = {"key": key, "expr": expr, "actual": value, "ok": ok}
        if not ok and parsed:
            item["gap"] = round(float(parsed[1]) - float(value or 0), 3)
        checks.append(item)
    return {"chapter": int(chapter), "round": rnd.get("name"), "goal": rnd.get("goal"),
            "checks": checks, "ok": all(c["ok"] for c in checks),
            "gaps": [c["key"] for c in checks if not c["ok"]],
            "sections_verdict": (actual.get("_sections") or {}).get("verdict")}


def evaluate_book(round_name=None, chapters=None, root="."):
    """整本：每章是否达标 + 未达标的章号（界面横幅用一行话）。"""
    from pathlib import Path as _P
    if chapters is None:
        ns = set()
        for layer in ("refined", "checked", "raw"):
            d = _P(root) / "data" / "chapters" / layer
            if d.is_dir():
                for f in d.glob("*.md"):
                    if f.stem.isdigit():
                        ns.add(int(f.stem))
        chapters = sorted(ns)
    rows = []
    for n in chapters:
        try:
            rows.append(evaluate(n, round_name, root))
        except Exception as e:                                 # noqa: BLE001
            rows.append({"chapter": n, "error": "%s: %s" % (type(e).__name__, e)})
    bad = [r["chapter"] for r in rows if not r.get("ok")]
    return {"round": round_name or current(root).get("name"), "chapters": rows,
            "passed": len([r for r in rows if r.get("ok")]), "failed": bad,
            "ok": not bad}
