# -*- coding: utf-8 -*-
"""一致性账本（P3）：把"LLM 通读后写一段报告"换成"代码比对结构化事实"。

## 为什么

`quality_checklist.check_consistency()` 此前只判一件事：本章**有没有提到**任一已知角色名。
函数在、判据空 —— 与"笔刷名存实亡"是同一种病。stage5 靠 LLM 通读找矛盾，
产出的是自由文本，不可回归、不能跨章累计，而且模型看自己的东西只会说"结构完整"。

## 做什么（只做确定性可判的三类 + 两类结构性提示）

| code | 判据 | 级别 |
|---|---|---|
| `dead_again` | 某人物在第 N 章有明确死亡句，第 M>N 章又作为行动主体出现 | conflict |
| `death_duplicate` | 同一人物在两个不同章节各被写死一次，中间没有复活情节 | conflict |
| `item_resurrect` | 某物品在第 N 章被丢弃/交出/毁掉，第 M>N 章又被掏出/握着 | conflict |
| `time_regression` | 绝对年份（1998 年 / 一九九八年）与章序反向，且该章无回忆标记 | conflict |
| `unknown_name` | 正文出现与已知角色同长度、同首字、编辑距离 1 的名字，且带"说/道/问" | warning |

**暂不做**：关系突变（结盟→反目）与地点瞬移。这两类没有可靠的确定性判据，
做出来只会制造误报 —— 记在这里是提醒后来者：不是漏了，是刻意不做。

**宁可少报，不许把正常叙事判成矛盾**：
- 死亡句排除否定与"差点/险些/没死"；
- 死后再出现时，含"墓/坟/遗/照片/回忆/想起/梦/生前"的句子不算复活；
- 年份倒退排除"回忆/那年/多年前/三年前/记得"；
- 未知名只在与已知名共享首字且同长度时提示（`周砚清` ↔ `周砚青`），不做无差别人名识别。

## 纪律

- **零 token**：纯规则 + 复用的分句函数（`style_analyzer`），不调模型。
- **只报不拦**：写报告与账本，不改正文、不影响 exit code。
- **可重算**：`events` 是唯一事实，冲突由 `check()` 从 events 推导 ——
  改判据不用重扫正文，重扫也不会漂移。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.file_io import read_text, write_text                      # noqa: E402
from utils.style_analyzer import _split_sentences                     # noqa: E402

STATE_REL = "data/state/story_state.json"
CHAPTERS_REL = "data/chapters/raw"
SETTING_REL = "data/setting/setting.json"

# ---- 词表 ----------------------------------------------------------------

DEATH_WORDS = ("死了", "断气", "咽气", "去世", "身亡", "下葬", "入土", "没了呼吸", "没有呼吸")
DEATH_NEG = ("没", "不", "未", "差点", "险些", "几乎", "假", "装", "仿佛", "好像", "如果")
HOLD_WORDS = ("拿出", "掏出", "握着", "揣着", "收进", "放进", "捡起", "接过", "拿着", "摸出", "取出")
# 「真正失去」与「转手」必须分开：递出去下一章再接回来是合法写法，
# 混在一起会把"转手"误报成"丢失后复现"（首版就是这么错的）。
LOSE_WORDS = ("扔", "毁", "烧", "丢掉", "丢了", "失手", "还回去", "埋了", "沉进")
TRANSFER_WORDS = ("递给", "给了", "交出", "交给", "送到")
RECALL_MARKS = ("回忆", "想起", "梦见", "梦到", "那年", "多年前", "三年前", "十年前",
                "记得", "生前", "墓", "坟", "遗物", "照片")
ACTION_HINTS = ("说", "道", "问", "答", "喊", "叫", "走", "站", "坐", "看", "抬",
                "转", "拿", "推", "拉", "笑", "点头", "摇头")

YEAR_RE = re.compile(r"(?:(?:19|20)\d{2}|[一二三四五六七八九][〇零一二三四五六七八九]{3})\s*年")
CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_year(s):
    """把'一九九八年'/'1998年' 统一成 int。"""
    digits = re.findall(r"\d", s)
    if len(digits) == 4:
        try:
            return int("".join(digits))
        except ValueError:
            return None
    chars = [c for c in s if c in CN_DIGITS]
    if len(chars) == 4:
        return int("".join(str(CN_DIGITS[c]) for c in chars))
    return None


def _has_neg(sentence, word):
    """死亡词前面 8 字内出现否定词 → 不算死亡。"""
    i = sentence.find(word)
    head = sentence[max(0, i - 8):i]
    return any(n in head for n in DEATH_NEG)


def _is_actor(sentence, name):
    """名字是否作为行动主体（句首 10 字内，或后面紧跟动作提示词）。"""
    i = sentence.find(name)
    if i < 0:
        return False
    if i <= 10:
        return True
    tail = sentence[i + len(name): i + len(name) + 4]
    return any(h in tail for h in ACTION_HINTS)


def _snippet(sentence, limit=70):
    s = sentence.strip()
    return s[:limit] + ("…" if len(s) > limit else "")


# ---- 扫描一章 ------------------------------------------------------------

def scan_chapter(chapter_no, text, characters, items):
    """把一章扫成事件列表（纯确定性）。

    events 条目统一带 `chapter` / `type` / `evidence`，冲突判定只看 events。
    每人每章只记一条 `act`（首次作为行动主体）—— 逐句记会让账本膨胀，
    而判"死后再现"只需要知道"这一章他动过"。
    """
    chars = [c for c in (characters or []) if c]
    its = [i for i in (items or []) if i]
    ev = []
    acted = set()

    for s in _split_sentences(text):
        if not s.strip():
            continue
        for c in chars:
            if c not in s:
                continue
            # 死亡（排除否定与"差点/假装"；回忆句里的死亡不记账 ——
            # "他想起那年父亲死了"是合法叙事，记了会让下一章的正常出场变成"复活"）
            for w in DEATH_WORDS:
                if (w in s and not _has_neg(s, w)
                        and not any(r in s for r in RECALL_MARKS)):
                    ev.append({"chapter": chapter_no, "type": "death", "who": c,
                               "evidence": _snippet(s)})
                    break
            # 持有 / 失去（物品）
            for it in its:
                if it not in s:
                    continue
                holding = any(h in s for h in HOLD_WORDS)
                losing = any(w in s for w in LOSE_WORDS)
                if losing and not holding:
                    ev.append({"chapter": chapter_no, "type": "lose_item",
                               "who": c, "item": it, "evidence": _snippet(s)})
                elif holding:
                    ev.append({"chapter": chapter_no, "type": "hold_item",
                               "who": c, "item": it, "evidence": _snippet(s)})
            # 出场（用于"死后再现"判定；每人每章一条）
            if c not in acted and _is_actor(s, c):
                acted.add(c)
                ev.append({"chapter": chapter_no, "type": "act", "who": c,
                           "evidence": _snippet(s)})

    # 时间锚点：每章取**第一个**非回忆的绝对年份。
    # 同一章里出现多个年份（"1998 年……2018 年"）是正常写法，逐条记会误报倒退。
    year_seen = False
    for s in _split_sentences(text):
        if year_seen:
            break
        for m in YEAR_RE.finditer(s):
            y = _cn_year(m.group(0))
            if y is None:
                continue
            recall = any(w in s for w in RECALL_MARKS)
            if recall:
                continue
            ev.append({"chapter": chapter_no, "type": "time", "year": y,
                       "evidence": _snippet(s)})
            year_seen = True
            break

    # 疑似错名：与已知名同长度、同首字、差 1 字，且紧跟"说/道/问/答"
    ev.extend(_unknown_names(chapter_no, text, chars))
    return ev


def _unknown_names(chapter_no, text, chars):
    known = set(chars)
    by_head = {}
    for c in known:
        by_head.setdefault((c[0], len(c)), []).append(c)
    out, seen = [], set()
    for s in _split_sentences(text):
        m = re.search(r"([\u4e00-\u9fa5]{2,3})(?=\s*(?:说|道|问|答|喊))", s)
        if not m:
            continue
        cand = m.group(1)
        if cand in known or cand in seen:
            continue
        for k in by_head.get((cand[0], len(cand)), []):
            if _edit1(cand, k):
                seen.add(cand)
                out.append({"chapter": chapter_no, "type": "unknown_name",
                            "name": cand, "like": k, "evidence": _snippet(s)})
                break
    return out


def _edit1(a, b):
    """编辑距离恰好 1（同长度，只差一个字）。"""
    if len(a) != len(b) or a == b:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


# ---- 冲突判定 ------------------------------------------------------------

def check(events):
    """从事件推导冲突。events 是唯一事实，本函数可随时重跑。"""
    out = []
    ev = sorted(events, key=lambda e: e.get("chapter", 0))

    # 1 死而复生
    deaths = {}
    for e in ev:
        if e["type"] == "death":
            deaths.setdefault(e["who"], e)
    for who, d in deaths.items():
        for e in ev:
            if (e["type"] == "act" and e["who"] == who
                    and e["chapter"] > d["chapter"]
                    and not any(w in e["evidence"] for w in RECALL_MARKS)):
                out.append({
                    "code": "dead_again", "level": "conflict",
                    "who": who, "chapter": e["chapter"],
                    "detail": "%s 在第%d章已死亡，第%d章又作为行动主体出现"
                              % (who, d["chapter"], e["chapter"]),
                    "evidence": [d["evidence"], e["evidence"]],
                })
                break

    # 2 同一人物被写死两次（中间没有复活情节）
    deaths_by_who = {}
    for e in ev:
        if e["type"] == "death":
            deaths_by_who.setdefault(e["who"], []).append(e)
    for who, ds in deaths_by_who.items():
        if len(ds) > 1:
            out.append({
                "code": "death_duplicate", "level": "conflict",
                "who": who, "chapter": ds[1]["chapter"],
                "detail": "%s 被写死了两次（第%d章、第%d章），中间没有复活情节"
                          % (who, ds[0]["chapter"], ds[1]["chapter"]),
                "evidence": [ds[0]["evidence"], ds[1]["evidence"]],
            })

    # 3 物品丢失后复现
    lost = {}
    for e in ev:
        if e["type"] == "lose_item":
            lost.setdefault(e["item"], e)
    for item, l in lost.items():
        for e in ev:
            if (e["type"] == "hold_item" and e["item"] == item
                    and e["chapter"] > l["chapter"]):
                out.append({
                    "code": "item_resurrect", "level": "conflict",
                    "item": item, "chapter": e["chapter"],
                    "detail": "「%s」在第%d章已失去，第%d章又被持有"
                              % (item, l["chapter"], e["chapter"]),
                    "evidence": [l["evidence"], e["evidence"]],
                })
                break

    # 4 时间倒退（只看非回忆的绝对年份）
    years = [(e["chapter"], e["year"]) for e in ev
             if e["type"] == "time" and not e.get("recall")]
    for i in range(1, len(years)):
        (c0, y0), (c1, y1) = years[i - 1], years[i]
        if c1 > c0 and y1 < y0:
            out.append({
                "code": "time_regression", "level": "conflict",
                "chapter": c1,
                "detail": "第%d章是 %d 年，第%d章却回到 %d 年（且无回忆标记）"
                          % (c0, y0, c1, y1),
                "evidence": ["第%d章 %d 年" % (c0, y0), "第%d章 %d 年" % (c1, y1)],
            })

    # 5 疑似错名（warning）
    for e in ev:
        if e["type"] == "unknown_name":
            out.append({
                "code": "unknown_name", "level": "warning",
                "chapter": e["chapter"],
                "detail": "第%d章出现「%s」，疑似「%s」的错名" % (e["chapter"], e["name"], e["like"]),
                "evidence": [e["evidence"]],
            })

    return out


# ---- 账本读写 ------------------------------------------------------------

def state_path(root):
    return Path(root) / STATE_REL


def load_state(root):
    p = state_path(root)
    if not p.exists():
        return {"version": 1, "updated_at": None, "events": [], "conflicts": []}
    try:
        return json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return {"version": 1, "updated_at": None, "events": [], "conflicts": []}


def save_state(root, state):
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    state["conflicts"] = check(state.get("events") or [])
    write_text(p, json.dumps(state, ensure_ascii=False, indent=2))
    return state


def load_entities(setting_path):
    """从设定集取角色名与物品名。"""
    p = Path(setting_path) if setting_path else None
    chars, items = [], []
    if not p or not p.exists():
        return chars, items
    try:
        data = json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return chars, items
    for c in data.get("characters") or []:
        n = (c or {}).get("name")
        if n and n not in chars:
            chars.append(n)
    for it in data.get("items") or []:
        n = (it or {}).get("name")
        if n and n not in items:
            items.append(n)
    return chars, items


def update_chapter(root, chapter_no, text, setting_path=None):
    """增量：替换该章旧事件 → 重算冲突 → 落盘。返回 (state, 本章相关冲突)。"""
    chars, items = load_entities(setting_path or Path(root) / SETTING_REL)
    state = load_state(root)
    keep = [e for e in state.get("events") or [] if e.get("chapter") != chapter_no]
    keep.extend(scan_chapter(chapter_no, text, chars, items))
    state["events"] = keep
    state = save_state(root, state)
    mine = [c for c in state["conflicts"] if c.get("chapter") == chapter_no]
    return state, mine


def rebuild(root, setting_path=None, chapters_dir=None):
    """全量重扫（章节被重写、或判据升级后用）。"""
    root = Path(root)
    chapters = Path(chapters_dir) if chapters_dir else root / CHAPTERS_REL
    chars, items = load_entities(setting_path or root / SETTING_REL)
    events = []
    for p in sorted(chapters.glob("*.md")):
        m = re.search(r"(\d+)", p.stem)
        if not m:
            continue
        events.extend(scan_chapter(int(m.group(1)), read_text(p), chars, items))
    state = {"version": 1, "events": events, "conflicts": []}
    return save_state(root, state)


def summarize(state):
    conf = [c for c in state.get("conflicts") or [] if c.get("level") == "conflict"]
    warn = [c for c in state.get("conflicts") or [] if c.get("level") == "warning"]
    return "事件 %d 条，冲突 %d 条，提示 %d 条" % (
        len(state.get("events") or []), len(conf), len(warn))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="一致性账本（确定性、零 token）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--rebuild", action="store_true", help="重扫全部章节")
    ap.add_argument("--check", action="store_true", help="只打印现有账本的冲突")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if args.rebuild:
        state = rebuild(root)
    else:
        state = load_state(root)

    if args.json:
        print(json.dumps(state if args.rebuild else
                         {"conflicts": state.get("conflicts") or []},
                         ensure_ascii=False, indent=2))
        return 0
    print("[story_state] %s" % summarize(state))
    for c in state.get("conflicts") or []:
        print("  [%s] 第%s章 %s" % (c.get("level"), c.get("chapter"), c.get("detail")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
