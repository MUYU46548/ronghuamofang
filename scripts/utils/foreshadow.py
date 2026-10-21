# -*- coding: utf-8 -*-
"""伏笔生命周期台账（2026-10-20，v0.9.3 的前半）。

## 现状与动机

设定集里只有一堆 `plot_fragments`（碎片），`story_state` 只记死亡/物品/年份。
**一条伏笔埋了没回收，没人知道** —— 写到第 40 章，第 3 章埋的钩子早就忘了。

书童有完整的伏笔台账。我们抄它的**数据模型**，但换一个数据来源：

## 数据来源（关键取舍：不额外烧 token）

只用两个**确定性**入口，不调模型：

1. **章大纲的「悬念钩子」字段**（v0.7.2 已强制，缺字段 stage3 判不合格）→ 一条候选伏笔。
2. 后续章大纲的「核心事件」「小目标」文本 → 用 bigram 重合判断"这条线有没有被推进"。

**回收不自动判定**：文本相似不等于"解决了"。自动判回收会让台账变成谎话。
`closed` / `abandoned` 由作者在界面上标（人工标记**压过**机器判定，与素材裁决同口径）。

## 判据

- id 唯一（`ch03-1`），同 id 不得两条
- `advanced` 里的章号必须 > `planted`（否则是数据错，不是"推进"）
- `open` 且距最新章节超过 `gates.foreshadow_stale`（默认 30 章）→ 进 `stale` 告警
- 没有章大纲 → 返回空 + 明确说明"无从抽取"，**不返回假数据**
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

OUTLINE_DIR = Path("data/outline/chapters")
STATUS_PATH = Path("data/state/foreshadow_status.json")
DEFAULT_STALE_AFTER = 30
# 钩子与后续章文本的相关判据：**交集数 + 覆盖率双达标**（2026-10-20 实测调整）。
# 只用交集数：短钩子（10 字）撞 2 个就算，太松；长钩子（40 字）撞 3 个太难，太严。
# 只用覆盖率：短句随便撞 1 个就 20%，也松。两个一起才稳。
MIN_OVERLAP = 2
MIN_COVER = 0.15

_PUNCT = re.compile(r"[\s，。！？；：、“”‘’「」『』\"'（）()…—·,.!?;:]+")


def _grams(text):
    s = _PUNCT.sub("", text or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a, b):
    return len(_grams(a) & _grams(b))


def related(a, b):
    """钩子 a 与文本 b 是否"在说同一件事"（交集 ≥ MIN_OVERLAP 且覆盖率 ≥ MIN_COVER）。"""
    ga = _grams(a)
    if not ga:
        return False
    inter = len(ga & _grams(b))
    return inter >= MIN_OVERLAP and (inter / len(ga)) >= MIN_COVER


def _load_status():
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_status(data):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_chapters(outline_dir):
    """返回 [(章号, 钩子, 本章文本)]，按章号升序。解析失败的单章跳过（不整体失败）。"""
    d = Path(outline_dir)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            no = int(p.stem)
        except (TypeError, ValueError):
            continue
        try:
            from stage3_chapter_outline import parse_chapter_outline
            fields = parse_chapter_outline(p) or {}
        except Exception:                                     # noqa: BLE001
            try:
                text = read_text(p)
            except OSError:
                continue
            fields = {"悬念钩子": None, "_raw": text}
        hook = (fields.get("悬念钩子") or "").strip()
        body = " ".join(x for x in (
            fields.get("核心事件"), fields.get("小目标"), fields.get("功能"),
            fields.get("_raw")) if x)
        out.append((no, hook, body))
    return out


def collect(outline_dir=None, stale_after=None, latest_chapter=None):
    """生成台账。返回 dict（含 items / summary / notes）。

    `latest_chapter` 用于算"多久没推进"；不传则取章大纲的最大章号。
    """
    outline_dir = outline_dir or OUTLINE_DIR
    chapters = _read_chapters(outline_dir)
    if not chapters:
        return {
            "items": [], "notes": [
                "没有章大纲（%s 为空）—— 伏笔台账从章大纲的「悬念钩子」抽取，"
                "先把阶段 3 跑出来。" % str(outline_dir).replace("\\", "/")],
            "summary": {"total": 0, "open": 0, "advanced": 0,
                        "closed": 0, "abandoned": 0, "stale": 0},
            "stale_after": int(stale_after or DEFAULT_STALE_AFTER),
            "latest_chapter": latest_chapter,
        }

    latest = int(latest_chapter or chapters[-1][0])
    human = _load_status()
    items, seen = [], set()
    for no, hook, _body in chapters:
        if not hook:
            continue
        fid = "ch%02d-1" % no
        if fid in seen:                       # 同 id 不得两条
            continue
        seen.add(fid)
        advanced = [n2 for n2, _h2, body2 in chapters
                    if n2 > no and related(hook, body2)]
        # 数据自检：推进章必须晚于埋设章（理论上不会越界，留一条判据防将来改坏）
        advanced = [n for n in advanced if n > no]
        rec = {
            "id": fid,
            "text": hook,
            "planted": no,
            "advanced": advanced,
            "last": max(advanced) if advanced else no,
            "status": "advanced" if advanced else "open",
            "source": "auto",
        }
        h = human.get(fid) or {}
        if h.get("status") in ("closed", "abandoned", "open"):
            rec["status"] = h["status"]
            rec["source"] = "human"
            rec["reason"] = h.get("reason") or ""
            rec["at"] = h.get("at") or ""
        items.append(rec)

    stale_after = int(stale_after or DEFAULT_STALE_AFTER)
    for r in items:
        # 只有还开着的线索才会"变旧"；已回收/已废弃的不告警
        r["stale"] = bool(r["status"] in ("open", "advanced")
                          and (latest - r["last"]) > stale_after)
        r["idle"] = latest - r["last"]

    summary = {
        "total": len(items),
        "open": sum(1 for r in items if r["status"] == "open"),
        "advanced": sum(1 for r in items if r["status"] == "advanced"),
        "closed": sum(1 for r in items if r["status"] == "closed"),
        "abandoned": sum(1 for r in items if r["status"] == "abandoned"),
        "stale": sum(1 for r in items if r["stale"]),
    }
    notes = []
    if not items:
        notes.append("章大纲里一条「悬念钩子」都没有 —— 阶段 3 的大纲要么是旧的（缺该字段），"
                     "要么钩子写得太空（少于 6 字会被判不合格）")
    if summary["stale"]:
        notes.append("有 %d 条线索超过 %d 章没推进 —— 要么回收，要么明确标「已放弃」，"
                     "悬着最伤人" % (summary["stale"], stale_after))
    return {"items": items, "notes": notes, "summary": summary,
            "stale_after": stale_after, "latest_chapter": latest}


def get_item(fid, **kw):
    for r in collect(**kw)["items"]:
        if r["id"] == fid:
            return r
    return None


def set_status(fid, status, reason="", **kw):
    """人工拍板（closed / abandoned / open）。返回 (ok, msg)。

    `closed` 与 `abandoned` **必须写理由** —— "为什么这条不用管了"是唯一能防止
    台账被随手清空的东西。
    """
    if status not in ("closed", "abandoned", "open"):
        return False, "status 只能是 closed / abandoned / open"
    item = get_item(fid, **kw)
    if not item:
        return False, "没有这条伏笔: %s（先刷新台账，可能是章大纲改了）" % fid
    if status in ("closed", "abandoned") and not str(reason or "").strip():
        return False, "标记「%s」必须写理由（否则以后没人知道为什么）" % status
    data = _load_status()
    data[fid] = {
        "status": status,
        "reason": str(reason or "").strip(),
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_status(data)
    return True, "已标记 %s → %s" % (fid, status)


def render_markdown(data):
    """人读版（给导出/日志用）。"""
    s = data.get("summary") or {}
    lines = ["# 伏笔台账", "",
             "- 共 %d 条：未推进 %d · 已推进 %d · 已回收 %d · 已放弃 %d · 超期 %d"
             % (s.get("total", 0), s.get("open", 0), s.get("advanced", 0),
                s.get("closed", 0), s.get("abandoned", 0), s.get("stale", 0)),
             ""]
    for r in data.get("items") or []:
        flag = "⚠️ " if r.get("stale") else ""
        lines.append("- %s`%s` 第 %d 章：%s（%s，已闲置 %d 章）"
                     % (flag, r["id"], r["planted"], r["text"],
                        r["status"], r.get("idle", 0)))
    for n in data.get("notes") or []:
        lines.append("> " + n)
    return "\n".join(lines) + "\n"


def review_warnings(outline_dir=None, stale_after=None, latest_chapter=None):
    """审稿联动（2026-10-21）：给 stage5 的确定性告警列表。零 token。

    只报**悬空伏笔**：`status in (open, advanced)` 且在 `latest_chapter` 之前
    （含）埋设、却一直没推进/没回收的钩子。故事已经走到后面、这条线还晾着，
    是审稿该提示的**连续性风险**（读者会记得，作者忘了）。

    刻意**只报不拦**：悬空不等于写错（有些钩子本就是留白、或跨卷才回收）。
    stage5 把它并进"带警告完成"，与"兜底复制""七维不完整"同一档 —— 提示作者
    去台账看一眼，不替他决定要不要现在回收。

    返回 dict：`{"stale": [...], "summary": {...}}`，`stale` 每项含
    id/text/planted/status/idle。台账为空（无章大纲）→ 返回空列表，不倒假数据。
    """
    data = collect(outline_dir=outline_dir, stale_after=stale_after,
                   latest_chapter=latest_chapter)
    dangling = [r for r in data.get("items", [])
                if r.get("status") in ("open", "advanced")]
    return {
        "stale": dangling,
        "summary": data.get("summary", {}),
        "stale_after": data.get("stale_after", DEFAULT_STALE_AFTER),
        "latest_chapter": data.get("latest_chapter"),
    }
