# -*- coding: utf-8 -*-
"""口径表：术语 → 取值 → 首见章 + 历法（R4/R8，2026-11-16）。

## 为什么需要一份"权威口径"

OB37 施工回执 §4.2 的第 5 条：三个子任务分别把名册写成 32／44／47，
**各自都对，合起来就错** —— 因为它们只读自己那一章，跨章一致性不在它们的视野里。
回执 §三 列的硬错里还有：安森死亡年份错一年、豆霖“只有十二个”、
同一段连出两遍。这类错不是文笔问题，是**没有一份权威口径**。

本模块给两份东西：

1. `data/setting/glossary.yaml`
   - `terms[]`：术语 → 取值 / 允许数字 / 首见章 / 状态（locked|draft）/ 备注
   - `calendar`：纪元、锚点事件、写法约定、禁用写法
2. `check()` / `scan_book()`：把正文里**与口径冲突的数字**与**历法写法混用**找出来，
   带章号、段号、原句 —— 不是"41 处可疑"，是"第 8 章写 47，第 10 章写 32"。

## 边界（写死）

- **只报不写正文**：改哪个数是作者的决定（回执 §八 第 4 条：32 与 47 都能自圆其说）。
- **没有口径表时零 noise**：文件不存在 / terms 为空 → 不报任何 finding（不是失败）。
- 数字抽取只认 `term` 附近 16 个字符内的阿拉伯数字与中文数字，
  不猜代指、不做 NLP —— 宁可漏报，不可误报（误报会让作者关掉这个功能）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

REL_PATH = Path("data") / "setting" / "glossary.yaml"

# 中文数字（够用到亿；小说里出现更大的数字基本是年份，不用担心）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

RELATIVE_WORDS = ("去年", "前年", "今年", "明年", "大前年", "后年",
                  "上个月", "下个月", "上一年", "下一年")
DAY_COUNT_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百千]+\s*天")
MONTH_DAY_RE = re.compile(r"[0-9一二三四五六七八九十]{1,2}\s*月\s*[0-9一二三四五六七八九十]{1,3}\s*日")


# ---------------------------------------------------------------- 路径/读写
def path(root="."):
    return Path(root) / REL_PATH


def load(root="."):
    """读口径表。文件不存在 → 空骨架（**不是错误**）。"""
    p = path(root)
    if not p.exists():
        return {"terms": [], "calendar": {}}
    try:
        # 走项目统一的配置读取（相对路径解析 + 重复键/语法错误带行号），
        # 不要在数据文件上自己裸 safe_load —— 结构性护栏会拦（A2）。
        from utils.config_io import load_config_yaml
        data = load_config_yaml(p, default={}) or {}
    except Exception as e:                                     # noqa: BLE001
        raise ValueError("glossary.yaml 解析失败：%s" % e)
    if not isinstance(data, dict):
        raise ValueError("glossary.yaml 顶层不是映射")
    data.setdefault("terms", [])
    data.setdefault("calendar", {})
    return data


def save(data, root="."):
    """写回口径表（整份 YAML；这是用户资产，写前备份到 data/setting/history/）。"""
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        hist = p.parent / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        (hist / ("glossary_%s.yaml" % stamp)).write_bytes(p.read_bytes())
    import yaml
    from utils.file_io import write_text
    write_text(p, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return p


# ---------------------------------------------------------------- 数字解析
def cn_to_int(text):
    """中文数字 → 整数（只处理到万位；解析不了返回 None）。"""
    s = str(text or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                total += (section + num) * unit
                section, num = 0, 0
            else:
                section += (num or 1) * unit
                num = 0
        else:
            return None
    return total + section + num


def numbers_in(text):
    """抽出一段文字里的所有数字（阿拉伯 + 中文）。"""
    out = []
    for m in re.finditer(r"\d+", str(text or "")):
        out.append(int(m.group(0)))
    for m in re.finditer(r"[零一二两三四五六七八九十百千万]{2,}", str(text or "")):
        v = cn_to_int(m.group(0))
        if v is not None:
            out.append(v)
    return out


def term_numbers(term_entry):
    """一条术语允许出现的取值。

    优先用显式 `numbers`；没有就从 `value` 里抽（`"12→32→44→48→47（存44亡3）"`
    会抽出 12/32/44/48/47 与两个 44）。
    """
    nums = term_entry.get("numbers")
    if isinstance(nums, list) and nums:
        out = []
        for x in nums:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    return numbers_in(term_entry.get("value") or "")


# ---------------------------------------------------------------- 检查
def _paragraphs(text):
    return [p.strip() for p in str(text or "").split("\n\n") if p.strip()]


def check(text, chapter=None, glossary=None, window=16):
    """量一章：返回 `{findings: [...], checked_terms: N, calendar: {...}}`。

    两类 finding：
    - `term_conflict`：术语附近出现了一个不在允许取值里的数字；
    - `calendar_mix`：历法写法混用（相对时间 / 天数写法与约定的月日写法冲突）。

    每条都带 `chapter` / `para_index` / `quote`，可直接定位。
    """
    data = glossary if isinstance(glossary, dict) else load()
    terms = [t for t in (data.get("terms") or []) if isinstance(t, dict) and t.get("term")]
    findings = []
    paras = _paragraphs(text)

    for t in terms:
        name = str(t.get("term") or "").strip()
        allowed = set(term_numbers(t))
        if not name:
            continue
        status = str(t.get("status") or "draft").lower()
        for i, p in enumerate(paras):
            for m in re.finditer(re.escape(name), p):
                tail = p[m.end():m.end() + window]
                # 只认紧跟在术语后面的数字（"名册四十七人" / "名册 47 人"）
                hit = numbers_in(tail[:window])
                if not hit:
                    continue
                value = hit[0]
                if allowed and value not in allowed:
                    findings.append({
                        "kind": "term_conflict",
                        "chapter": chapter,
                        "para_index": i,
                        "term": name,
                        "found": value,
                        "allowed": sorted(allowed),
                        "status": status,
                        "quote": p[max(0, m.start() - 8):m.start() + 40],
                        "why": "“%s”这里写 %s，口径表允许的是 %s"
                               % (name, value, "／".join(str(x) for x in sorted(allowed))),
                    })
    cal = data.get("calendar") or {}
    if cal:
        forbid_relative = bool(cal.get("relative_forbidden"))
        style = str(cal.get("style") or "")
        prefer_absolute = bool(cal.get("prefer_absolute")) or "月" in style and "日" in style
        for i, p in enumerate(paras):
            if forbid_relative:
                for w in RELATIVE_WORDS:
                    if w in p:
                        findings.append({
                            "kind": "calendar_mix", "chapter": chapter, "para_index": i,
                            "word": w, "quote": p[max(0, p.find(w) - 10):p.find(w) + 30],
                            "why": "口径表规定正文只写具体日期，这里用了相对时间“%s”" % w,
                        })
            if prefer_absolute:
                if DAY_COUNT_RE.search(p) and MONTH_DAY_RE.search(p):
                    findings.append({
                        "kind": "calendar_mix", "chapter": chapter, "para_index": i,
                        "quote": p[:60],
                        "why": "同一段里同时出现“第 N 天”与“X 月 X 日”—— 两种写法混用",
                    })
                elif DAY_COUNT_RE.search(p) and "月" in style:
                    findings.append({
                        "kind": "calendar_mix", "chapter": chapter, "para_index": i,
                        "quote": p[:60],
                        "why": "口径表约定用“X 月 X 日”，这里写“第 N 天”",
                    })
    return {"chapter": chapter, "checked_terms": len(terms),
            "findings": findings, "count": len(findings),
            "calendar": {k: cal.get(k) for k in ("era", "anchor", "style") if cal.get(k)}}


def scan_book(chapters=None, root=".", glossary=None):
    """全书扫一遍，按章给 findings，并按术语汇总**跨章不一致**。

    返回 `{chapters: {n: report}, by_term: {term: {values: {value: [章...]}}, conflicts: [...]}}`
    —— 这正是回执要的那句话："第 8 章写 47，第 10 章写 32"。
    """
    data = glossary if isinstance(glossary, dict) else load(root)
    base = Path(root) / "data" / "chapters"
    if chapters is None:
        ns = set()
        for layer in ("refined", "checked", "raw"):
            d = base / layer
            if d.is_dir():
                for f in d.glob("*.md"):
                    if f.stem.isdigit():
                        ns.add(int(f.stem))
        chapters = sorted(ns)
    out = {"chapters": {}, "by_term": {}, "total": 0}
    for n in chapters:
        text = ""
        for layer in ("refined", "checked", "raw"):
            p = base / layer / ("%02d.md" % int(n))
            if p.exists():
                text = p.read_text(encoding="utf-8")
                break
        if not text:
            continue
        rep = check(text, n, data)
        out["chapters"][n] = rep
        out["total"] += rep["count"]
    # 跨章汇总：同一个术语在各章被写成了哪些数
    for n, rep in out["chapters"].items():
        for f in rep["findings"]:
            if f.get("kind") != "term_conflict":
                continue
            slot = out["by_term"].setdefault(f["term"], {"values": {}, "allowed": f.get("allowed")})
            slot["values"].setdefault(str(f["found"]), []).append(n)
    for term, slot in out["by_term"].items():
        slot["values"] = {k: sorted(set(v)) for k, v in slot["values"].items()}
        vals = list(slot["values"].keys())
        slot["conflicts"] = len(vals) > 1
        if slot["conflicts"]:
            slot["summary"] = "“%s”在全书出现了 %s 三种以上写法：%s" % (
                term, "/".join(vals),
                "；".join("第 %s 章写 %s" % ("、".join(str(c) for c in chs), v)
                         for v, chs in slot["values"].items()))
    return out


# ---------------------------------------------------------------- 写入（作者拍板）
def upsert_term(term, value="", numbers=None, anchor_chapter=None,
                status="locked", note="", root=".", glossary=None):
    """新增/更新一条术语口径（**作者拍板**的落点：改一个数只改这里一处）。"""
    name = str(term or "").strip()
    if not name:
        raise ValueError("term 不能为空")
    data = dict(glossary or load(root))
    terms = [dict(t) for t in (data.get("terms") or []) if isinstance(t, dict)]
    hit = None
    for t in terms:
        if str(t.get("term") or "") == name:
            hit = t
            break
    payload = {"term": name,
               "value": value if value is not None else "",
               "status": status or "locked"}
    if numbers:
        payload["numbers"] = [int(x) for x in numbers]
    if anchor_chapter:
        payload["anchor_chapter"] = int(anchor_chapter)
    if note:
        payload["note"] = note
    if hit is None:
        terms.append(payload)
    else:
        hit.update(payload)
    data["terms"] = terms
    save(data, root)
    return payload


def set_calendar(**fields):
    """更新历法约定（era / anchor / style / relative_forbidden / prefer_absolute）。"""
    allowed = ("era", "anchor", "style", "relative_forbidden", "prefer_absolute")
    data = load()
    cal = dict(data.get("calendar") or {})
    for k, v in fields.items():
        if k in allowed and v is not None:
            cal[k] = v
    data["calendar"] = cal
    save(data)
    return cal


def summary(glossary=None, root="."):
    """给界面/CLI 的一句话：几条锁定、几条待定、历法有没有设。"""
    data = glossary if isinstance(glossary, dict) else load(root)
    terms = [t for t in (data.get("terms") or []) if isinstance(t, dict)]
    locked = sum(1 for t in terms if str(t.get("status") or "").lower() == "locked")
    cal = data.get("calendar") or {}
    return {"terms": len(terms), "locked": locked, "draft": len(terms) - locked,
            "calendar_set": bool(cal.get("era") or cal.get("style")),
            "path": path(root).as_posix()}
