# -*- coding: utf-8 -*-
"""伏笔生命周期台账（2026-10-20，v0.9.3 的前半）。

## 现状与动机

设定集里只有一堆 `plot_fragments`（碎片），`story_state` 只记死亡/物品/年份。
**一条伏笔埋了没回收，没人知道** —— 写到第 40 章，第 3 章埋的钩子早就忘了。

书童有完整的伏笔台账。我们抄它的**数据模型**，但换一个数据来源：

## 数据来源（关键取舍：不额外烧 token）

只用两个**确定性**入口，不调模型：

1. **章大纲的“悬念钩子”字段**（v0.7.2 已强制，缺字段 stage3 判不合格）→ 一条候选伏笔。
2. 后续章大纲的“核心事件”“小目标”文本 → 用 bigram 重合判断"这条线有没有被推进"。

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
GLOBAL_OUTLINE = Path("data/outline/global.md")
STATUS_PATH = Path("data/state/foreshadow_status.json")
DEFAULT_STALE_AFTER = 30
# 钩子与后续章文本的相关判据：**交集数 + 覆盖率双达标**（2026-10-20 实测调整）。
# 只用交集数：短钩子（10 字）撞 2 个就算，太松；长钩子（40 字）撞 3 个太难，太严。
# 只用覆盖率：短句随便撞 1 个就 20%，也松。两个一起才稳。
MIN_OVERLAP = 2
MIN_COVER = 0.15

_PUNCT = re.compile(r"[\s，。！？；：、“”‘’\u300c\u300d\u300e\u300f\"'（）()…—·,.!?;:]+")


def _grams(text):
    s = _PUNCT.sub("", text or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a, b):
    return len(_grams(a) & _grams(b))


# ---------------------------------------------------------------- 整体大纲的伏笔清单
#: 一行一条的形态：`- f001 | 起 | 埋：… | 收：… | 状态：未回收`
#: 编号允许 f001 / F001 / f1 三种写法（模型偶尔会用短号）；后面字段用 | 分。
_F_ROW = re.compile(
    r"^\s*[-*]\s*[fF](\d{1,3})\s*\|\s*([^\|\n]*)\|\s*埋[：:]\s*([^\|\n]*)"
    r"\|\s*收[：:]\s*([^\|\n]*)\|\s*状态[：:]\s*([^\|\n]*)\s*$",
    re.MULTILINE)
_VALID_STATUS = ("未回收", "已回收", "放弃")


def parse_global_outline(text):
    """从整体大纲解析伏笔清单（2026-11-28）。

    ## 为什么要有这个
    真机《测试用书-1125》：整体大纲里作者（模型）自己编了 **f001~f026** 并逐条
    写了埋设点与回收计划，但台账只认**章大纲的“悬念钩子”** —— 两套体系互不相认，
    而且提示词从来没要求过 f 编号。结果是那 26 条在系统里彻底失联：
    界面上的伏笔台账只显示 6 条（来自章钩子），另外 26 条一个字都看不到。

    作者 2026-11-28 裁决：**提示词正式要求 f 编号 + 台账改从整体大纲收集**。
    两边口径写在这里：`prompts/stage2_global_outline.md` 的“伏笔清单”段。

    ## 返回
    `(items, problems)`：
      - items: `[{id, arc, planted, payoff, status, text}]`
      - problems: 人能读的毛病清单（跳号 / 编号重复 / 状态非法 / 引用不存在的号）
    解析**宽松**（编号写法、字段顺序容错），但**报问题严格** —— 悄悄丢掉一条比
    报错更糟，作者会以为那条伏笔没问题。
    """
    items, problems = [], []
    seen = {}
    for m in _F_ROW.finditer(text or ""):
        num = int(m.group(1))
        fid = "f%03d" % num
        arc = (m.group(2) or "").strip()
        planted = (m.group(3) or "").strip()
        payoff = (m.group(4) or "").strip()
        status = (m.group(5) or "").strip()
        if not planted or not payoff:
            problems.append("%s 的“埋”或“收”是空的 —— 这条算不上伏笔" % fid)
            continue
        if status not in _VALID_STATUS:
            problems.append("%s 的状态“%s”不是 未回收/已回收/放弃 之一，按“未回收”处理"
                            % (fid, status))
            status = "未回收"
        if fid in seen:
            problems.append("%s 编号重复（第二条被丢弃）" % fid)
            continue
        seen[fid] = True
        items.append({"id": fid, "arc": arc, "planted": planted,
                      "payoff": payoff, "status": status,
                      "text": planted})

    # 编号连续性：只在拿到东西时查（空清单由调用方说明“没有清单”）
    if items:
        nums = sorted(int(i["id"][1:]) for i in items)
        missing = [n for n in range(1, nums[-1] + 1) if n not in nums]
        if missing:
            problems.append("编号不连续，缺 %s（连续编号是台账对齐的前提）"
                            % "、".join("f%03d" % n for n in missing[:8]))

    # “本段兑现”里引用了表里没有的号 —— 这就是真机那种"模型自己记着、系统不知道"
    quoted = set(re.findall(r"\bf(\d{3})\b", text or ""))
    declared = {str(int(i["id"][1:])).zfill(3) for i in items}
    ghost = sorted(quoted - declared)
    if ghost:
        problems.append("正文引用了清单里没有的编号：%s（引用不存在的号 = 台账断链）"
                        % "、".join("f" + g for g in ghost[:8]))
    return items, problems


def related(a, b):
    """钩子 a 与文本 b 是否"在说同一件事"（交集 ≥ MIN_OVERLAP 且覆盖率 ≥ MIN_COVER）。"""
    ga = _grams(a)
    if not ga:
        return False
    inter = len(ga & _grams(b))
    return inter >= MIN_OVERLAP and (inter / len(ga)) >= MIN_COVER


def _load_status(path=None):
    """读作者的人工标记（closed/abandoned）。

    `path` 显式给出时按它读 —— `GET /chapters/context` 这类**带 --root 的只读视图**
    必须读目标项目的状态文件，不能用进程 CWD 的相对路径（否则会给另一本书的伏笔
    判“已回收”，而这里返回的是自认为准确的数字）。
    """
    try:
        p = Path(path) if path else STATUS_PATH
        data = json.loads(p.read_text(encoding="utf-8"))
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


def collect(outline_dir=None, stale_after=None, latest_chapter=None,
            status_path=None, global_outline=None):
    """生成台账。返回 dict（含 items / summary / notes）。

    数据来源两条，**整体大纲优先**（2026-11-28 作者裁决）：
    1. 整体大纲的“伏笔清单”段（`f001 | 起 | 埋：… | 收：… | 状态：…`）——
       这是作者/模型显式声明的伏笔，带编号、带回收计划，台账以它为准；
    2. 章大纲的“悬念钩子”—— 没有整体大纲清单时的兜底，也是它的补充。

    两条都收（id 前缀不同：`f001` vs `ch03-1`），互不覆盖；
    章钩子里与整体清单说的是同一件事时，**仍单列**（宁可多一条，不许静默合并）。

    `latest_chapter` 用于算"多久没推进"；不传则取章大纲的最大章号。
    `status_path` 显式指定人工标记文件（只读视图 / `--root` 场景用）。
    `global_outline` 显式指定整体大纲路径（`--root` 场景用）。
    """
    outline_dir = outline_dir or OUTLINE_DIR
    gpath = Path(global_outline) if global_outline else GLOBAL_OUTLINE
    chapters = _read_chapters(outline_dir)
    human = _load_status(status_path)
    notes = []

    # ---- 来源 1：整体大纲的伏笔清单 ----
    declared, problems = [], []
    if gpath.exists():
        try:
            declared, problems = parse_global_outline(
                gpath.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:                                # noqa: BLE001
            notes.append("整体大纲读不出来（%s: %s）—— 只用章钩子"
                         % (type(e).__name__, str(e)[:120]))
    elif gpath.name:
        notes.append("没有整体大纲（%s）—— 只用章钩子的悬念钩子"
                     % str(gpath).replace("\\", "/"))
    notes.extend(problems)

    if not chapters and not declared:
        return {
            "items": [], "notes": notes + [
                "没有章大纲（%s 为空）且整体大纲里也没有伏笔清单 —— "
                "无从抽取，不是“没有伏笔”" % str(outline_dir).replace("\\", "/")],
            "summary": {"total": 0, "open": 0, "advanced": 0,
                        "closed": 0, "abandoned": 0, "stale": 0},
            "stale_after": int(stale_after or DEFAULT_STALE_AFTER),
            "latest_chapter": latest_chapter,
        }

    latest = int(latest_chapter or (chapters[-1][0] if chapters else 0) or 0)
    items, seen = [], set()

    # 整体清单先进（它的状态是作者手写的，权威）
    for d in declared:
        fid = d["id"]
        if fid in seen:
            continue
        seen.add(fid)
        rec = {
            "id": fid,
            "text": d["text"],
            "arc": d.get("arc") or "",
            "payoff": d.get("payoff") or "",
            "planted": None,                 # 整体清单不给章号，只有段落（起/承/转/合）
            "advanced": [],
            "last": None,
            "status": ("closed" if d["status"] == "已回收"
                       else "abandoned" if d["status"] == "放弃" else "open"),
            "source": "outline",
        }
        h = human.get(fid) or {}
        if h.get("status") in ("closed", "abandoned", "open"):
            rec["status"] = h["status"]
            rec["source"] = "human"
            rec["reason"] = h.get("reason") or ""
            rec["at"] = h.get("at") or ""
        items.append(rec)

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
        # 整体大纲来的条目没有章号（`planted=None`）—— 它们只按声明状态算，
        # 不参与"闲置 N 章"（没有埋设章就算不出闲置，瞎算会给出假告警）。
        _last = r.get("last")
        if _last is None:
            r["stale"] = False
            r["idle"] = None
            continue
        # 只有还开着的线索才会"变旧"；已回收/已废弃的不告警
        r["stale"] = bool(r["status"] in ("open", "advanced")
                          and (latest - _last) > stale_after)
        r["idle"] = latest - _last

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
        notes.append("章大纲里一条“悬念钩子”都没有 —— 阶段 3 的大纲要么是旧的（缺该字段），"
                     "要么钩子写得太空（少于 6 字会被判不合格）")
    if summary["stale"]:
        notes.append("有 %d 条线索超过 %d 章没推进 —— 要么回收，要么明确标“已放弃”，"
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
        return False, "标记“%s”必须写理由（否则以后没人知道为什么）" % status
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
        if r.get("planted") is None:
            # 整体大纲来的条目：没有章号，按段落 + 回收计划显示
            lines.append("- %s`%s`（%s段）：%s → 计划：%s（%s）"
                         % (flag, r["id"], r.get("arc") or "?",
                            r["text"], r.get("payoff") or "（未写）",
                            r["status"]))
        else:
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
