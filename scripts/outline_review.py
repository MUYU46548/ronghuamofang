# -*- coding: utf-8 -*-
"""大纲体检报告（P0.5-A，确定性脚本，不调用 LLM）。

读 data/outline/global.md，量化每条关键节点/章节规划的信息密度：
具体事件 / 冲突张力 / 角色 / 场景锚点 / 篇幅，标出空泛项，供用户在
阶段 2 审批前针对性审阅与精修。

用法：
  python scripts/outline_review.py
  python scripts/outline_review.py --outline data/outline/global.md --report data/outline/review_report.md

输出：
  - 终端摘要表
  - 详细报告 data/outline/review_report.md
"""
import argparse
import json
import re
from pathlib import Path

from utils.file_io import read_text, write_text

# 启发式词表（可热修；中文动词/事件词）
EVENT_WORDS = [
    "发现", "调查", "追查", "遭遇", "对决", "决战", "揭露", "决定", "离开",
    "返回", "袭击", "救", "偷", "进入", "打开", "写下", "收到", "反扑",
    "封", "和解", "想起", "质问", "对峙", "跟踪", "拦截", "截获", "出手",
    "冰封", "救治", "回应", "求助", "目击", "报告", "抵达", "出发", "潜入",
    "抢夺", "逃脱", "搜寻", "寻找", "落定", "促成", "加装", "改笔", "回应",
]
CONFLICT_WORDS = [
    "冲突", "矛盾", "危机", "对决", "反扑", "威胁", "争议", "拒绝", "对抗",
    "对峙", "挣扎", "质问", "决裂", "背叛", "阴谋", "暗线", "悬念", "转折",
    "危险", "纠纷", "争夺", "被捕", "封锁", "误会", "隐瞒", "秘密", "异动",
    "异常", "走私", "团伙", "暴露", "失控", "决裂", "分裂", "对抗",
]
SCENE_SUFFIX = [
    "医院", "宫", "府", "之间", "市", "镇", "区", "殿", "塔", "森林", "沙漠",
    "边境", "观测站", "回廊", "花园", "咖啡馆", "办公室", "战场", "废墟",
    "宿舍", "学校", "监狱", "车站", "码头", "府邸", "阁", "苑", "洲", "山脉",
    "河谷", "平原", "村落", "王城", "营地", "客栈", "面馆",
]

# 维度阈值
MIN_DENSE_CHARS = 60     # 去除空白后低于此字数视为篇幅不足
OK_SCORE = 4             # ≥4 视为充实
WARN_SCORE = 3           # =3 视为可能空泛


def _clean(text):
    return re.sub(r"\s+", "", text or "")


def _load_names(setting_path):
    """从 setting.json 提取角色名与地点名。缺失时返回空列表。"""
    chars, locs = [], []
    if setting_path and Path(setting_path).exists():
        try:
            data = json.loads(read_text(setting_path))
        except (json.JSONDecodeError, ValueError):
            return chars, locs
        for c in data.get("characters", []):
            if isinstance(c, dict) and c.get("name"):
                chars.append(str(c["name"]))
        world = data.get("world", {}) or {}
        for key in ("locations", "factions", "items"):
            for item in world.get(key, []) or []:
                if isinstance(item, str):
                    locs.append(item)
                elif isinstance(item, dict) and item.get("name"):
                    locs.append(str(item["name"]))
    return chars, locs


def _item_re(after):
    """构造「条目 + 终止前瞻」的正则片段。

    ⚠️ 2026-09-21 修 bug：终止前瞻原为 `(?=\\n\\s*[-*]|\\n##|\\Z)`，
    **恒定漏掉每节的最后一个条目** —— 因为最后一条后面是「空行 + 下一节标题」
    （`\\n\\n## `）或「换行 + 文件尾」，而 `\\n##` 要求紧邻换行后就是 `##`
    （`\\n\\n##` 不匹配）、`\\Z` 要求当前位置已在串尾（当前位置在 `\\n` 之前，也不匹配）。

    实测：写 5 个关键节点 / 5 条章节规划，解析结果**都是 4**。

    危害不小：`review()` 拿这个条数去做一致性检查
    （「章节规划 N 条 ≠ 预计章节数 M」），于是**每次体检都误报一项 issue**，
    用户被引导去"精修"一份其实完全正确的大纲。旧行为下这只是多打印一行；
    但一旦把 issue 当作失败信号（见 `refine_outline` 的假成功修复），
    它就会变成**假失败**。

    修法：把前瞻放宽为 `(?=\\n\\s*[-*]|\\n\\s*##|\\s*\\Z)` ——
    `\\s*` 吃掉空行/尾部换行（`\\s` 含换行），三个分支都能正确命中。
    """
    return rf"(?:^|\n)\s*[-*]?\s*({after}[^\n]*?)(?=\n\s*[-*]|\n\s*##|\s*\Z)"


def _score_entry(text, chars, locs):
    """对单条节点/规划打分。返回 (score, flags, reasons)。

    flags: dict 每个维度是否命中；reasons: 缺失维度说明（供报告展示）。
    """
    t = _clean(text)
    if not t:
        return 0, {}, ["条目为空"]
    flags = {
        "dense": len(t) >= MIN_DENSE_CHARS,
        "event": any(w in t for w in EVENT_WORDS),
        "conflict": any(w in t for w in CONFLICT_WORDS),
    }
    reasons = []
    # 角色维度：无 setting.json 时跳过（不惩罚），有则必须命中
    if chars:
        flags["role"] = any(c and c in t for c in chars)
        if not flags["role"]:
            reasons.append("未提及已知角色名")
    # 场景维度：地点名或场景后缀词
    flags["scene"] = any(s and s in t for s in locs) or any(
        w in t for w in SCENE_SUFFIX)
    if not flags["scene"]:
        reasons.append("无场景锚点")
    if not flags["dense"]:
        reasons.append(f"篇幅过短（{len(t)}字）")
    if not flags["event"]:
        reasons.append("无具体事件动词")
    if not flags["conflict"]:
        reasons.append("无冲突/张力词")
    score = sum(1 for v in flags.values() if v)
    return score, flags, reasons


def _level(score):
    if score >= OK_SCORE:
        return "OK"
    if score == WARN_SCORE:
        return "WARN"
    return "THIN"


def _normalize(text):
    """Markdown 语法归一化：去掉 **加粗**、*斜体*、#标题、-列表、•列表、__下划线__"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^[-*•]\s*", "", text)
    return text


def review(global_path, setting_path=None):
    """体检 global.md。返回结构化 dict，供 CLI 打印与报告落盘复用。

    解析器兼容三种输出格式：
    - 旧 fake 模式：`- 节点N：...`（纯文本）
    - LLM 加粗格式：`- **节点N：第X章** — ...`（glm-5 等）
    - LLM 区间格式：`- **第1-2章（功能）**：...`（章节规划）
    """
    raw_text = read_text(global_path)
    text = _normalize(raw_text)
    chars, locs = _load_names(setting_path)
    result = {
        "path": str(Path(global_path).resolve()),
        "setting_loaded": bool(chars or locs),
        "nodes": [],          # 关键节点评分
        "plan": [],           # 章节规划评分
        "issues": [],         # 整体一致性检查
        "summary": {},
    }

    # 提取关键节点条目
    node_sec = re.search(r"^##\s*关键节点.*?(?=^##\s|\Z)", text, re.M | re.S)
    node_items = []
    if node_sec:
        # 兼容三种格式：`- 节点N：...` / `- 节点N — ...` / `- 节点N ...`
        # （前瞻已修「漏掉末条」的 bug，见 `_item_re` 的 docstring）
        node_items = re.findall(_item_re(r"节点\s*\d+"),
                                node_sec.group(0), re.S)
    for item in node_items:
        item = item.strip()
        title = re.match(r"(节点\s*\d+)", item)
        if not title:
            continue
        score, flags, reasons = _score_entry(item, chars, locs)
        result["nodes"].append({
            "title": title.group(1).strip(),
            "score": score, "level": _level(score),
            "flags": flags, "reasons": reasons,
            "chars": len(_clean(item)),
        })

    # 提取章节规划条目
    plan_sec = re.search(r"^##\s*章节规划.*?(?=^##\s|\Z)", text, re.M | re.S)
    plan_items = []
    if plan_sec:
        # 兼容：`- 第N章（功能）` / `- 第N-M章（功能）` / `- 第N章：...`
        plan_items = re.findall(
            _item_re(r"第\s*[\d\-~\s一二三四五六七八九十百]+\s*章"),
            plan_sec.group(0), re.S)
    for item in plan_items:
        item = item.strip()
        title = re.match(r"(第\s*[\d\-~\s一二三四五六七八九十百]+\s*章)", item)
        if not title:
            continue
        score, flags, reasons = _score_entry(item, chars, locs)
        result["plan"].append({
            "title": title.group(1).strip(),
            "score": score, "level": _level(score),
            "flags": flags, "reasons": reasons,
            "chars": len(_clean(item)),
        })

    # 预计章节数
    m = re.search(r"^##\s*预计章节数\s*\n\s*(\d+)", text, re.M)
    expected = int(m.group(1)) if m else None
    result["expected_chapters"] = expected
    if expected is None:
        result["issues"].append("缺少有效的 ## 预计章节数")
    else:
        if len(node_items) < expected:
            result["issues"].append(
                f"关键节点 {len(node_items)} 条 < 预计章节数 {expected}："
                "部分章节可能缺少驱动事件")
        if len(plan_items) != expected:
            result["issues"].append(
                f"章节规划 {len(plan_items)} 条 ≠ 预计章节数 {expected}："
                "规划未覆盖全部章节")

    # 汇总
    all_entries = result["nodes"] + result["plan"]
    thins = [e for e in all_entries if e["level"] == "THIN"]
    warns = [e for e in all_entries if e["level"] == "WARN"]
    result["summary"] = {
        "total": len(all_entries),
        "ok": len(all_entries) - len(thins) - len(warns),
        "warn": len(warns),
        "thin": len(thins),
        "verdict": ("FAIL" if thins or result["issues"]
                    else "WARN" if warns else "PASS"),
    }
    return result


# ---------------------------------------------------------------- 迭代收敛视图
#
# 2026-09-21 新增：回答「还要不要再迭代一轮」。
#
# 背景：`refine_outline` 每轮会把**修改前**的状态备份到
# `history/global_v{N}.md`（N 从 1 递增），所以：
#   - `history/global_v{N}.md` = 第 N 轮精修**之前**的大纲
#   - `global.md`             = 最后一轮精修**之后**的大纲
# 于是「与上一版对比」= 当前 `global.md` vs 编号最大的 `global_v{N}.md`。
#
# 全部确定性、零 token —— 只是把已有的 `review()` 跑在不同文件上。

def _fingerprint(result, label="", path=""):
    """从一个 `review()` 结果抽出可比较的指标。

    比较的**主键**是 `(issues, thin)` —— 二者都是「越小越好」：
    `issues` 是硬性结构问题（已会让精修回报失败），`thin` 是信息密度不足的条目数。
    次键是 `avg_score`（条目平均分，越高越好），只在主键持平时用于细分方向。
    """
    s = result["summary"]
    scores = [e["score"] for e in result["nodes"] + result["plan"]]
    issues = len(result["issues"])
    thin = s["thin"]
    return {
        "label": label,
        "path": path,
        "total": s["total"], "ok": s["ok"], "warn": s["warn"], "thin": thin,
        "issues": issues,
        "key": issues + thin,          # 主键：越小越好
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "nodes": len(result["nodes"]), "plan": len(result["plan"]),
        "expected": result.get("expected_chapters"),
        "verdict": s["verdict"],
    }


def latest_backup(history_dir="data/outline/history"):
    """返回编号最大的 `global_v{N}.md`（= 最近一轮精修**之前**的状态）。

    返回 `(version, Path)`；目录不存在或无备份时返回 `None`。
    """
    hist = Path(history_dir)
    best = None
    if hist.is_dir():
        for p in hist.glob("global_v*.md"):
            m = re.match(r"global_v(\d+)\.md$", p.name)
            if m:
                n = int(m.group(1))
                if best is None or n > best[0]:
                    best = (n, p)
    return best


def _entry_map(result):
    """{title: score} —— 用于逐条目对比。"""
    out = {}
    for e in result["nodes"] + result["plan"]:
        out[e["title"]] = e["score"]
    return out


def compare_with_previous(global_path, setting_path=None,
                          history_dir="data/outline/history"):
    """当前大纲 vs 最近一次备份。返回对比 dict；无备份时返回 None。

    返回 {
        from_version, prev, cur,        # prev/cur 是 _fingerprint
        deltas: {...},                  # cur - prev（对 key/issues/thin/avg_score）
        entries: {improved, regressed, added, removed},   # 逐条目差分
        verdict: "improved"|"stalled"|"regressed",
        advice: str,
    }
    """
    lb = latest_backup(history_dir)
    if lb is None:
        return None
    ver, prev_path = lb
    if not prev_path.exists() or not Path(global_path).exists():
        return None

    # 各体检一次（review 会读盘 + 正则扫描，重复调用是浪费）
    prev_rev = review(prev_path, setting_path)
    cur_rev = review(global_path, setting_path)
    prev = _fingerprint(prev_rev, label=f"v{ver}（本轮改前）", path=str(prev_path))
    cur = _fingerprint(cur_rev, label="当前", path=str(global_path))

    pm, cm = _entry_map(prev_rev), _entry_map(cur_rev)
    improved = sorted(t for t, v in cm.items() if t in pm and v > pm[t])
    regressed = sorted(t for t, v in cm.items() if t in pm and v < pm[t])
    added = sorted(t for t in cm if t not in pm)
    removed = sorted(t for t in pm if t not in cm)

    deltas = {k: cur[k] - prev[k] for k in ("key", "issues", "thin", "total", "avg_score")}

    if cur["key"] < prev["key"]:
        verdict, advice = "improved", "本轮有实质改善（问题/碎片条目减少），可继续按同一方向迭代。"
    elif cur["key"] == prev["key"]:
        if cur["avg_score"] > prev["avg_score"]:
            verdict = "stalled"
            advice = ("本轮问题数未变、仅平均分略升 —— 收益已明显递减。"
                      "建议换角度提意见（如调整母题/角色动机）、补素材，或直接开工。")
        else:
            verdict = "stalled"
            advice = ("本轮**无实质改善**。同方向继续迭代大概率只是重写措辞，"
                      "建议换角度提意见、补素材，或直接开工。")
    else:
        verdict = "regressed"
        advice = (f"本轮**反而变差**（问题数上升）。"
                  f"建议回退：GUI「大纲」页签的「版本恢复」→ v{ver}，"
                  f"或 `python scripts/outline_panel.py --restore {ver}`。")

    return {"from_version": ver, "prev": prev, "cur": cur, "deltas": deltas,
            "entries": {"improved": improved, "regressed": regressed,
                        "added": added, "removed": removed},
            "verdict": verdict, "advice": advice}


def print_compare(cmp):
    """打印版本对比摘要。"""
    if not cmp:
        return
    d = cmp["deltas"]

    def _sgn(v):
        return f"{v:+d}" if isinstance(v, int) else f"{v:+.2f}"

    print(f"[outline_review] 与上一版对比（v{cmp['from_version']} → 当前）："
          f"问题 {cmp['prev']['issues']}→{cmp['cur']['issues']}（{_sgn(d['issues'])}）"
          f"，碎片 {cmp['prev']['thin']}→{cmp['cur']['thin']}（{_sgn(d['thin'])}）"
          f"，平均分 {cmp['prev']['avg_score']}→{cmp['cur']['avg_score']}"
          f"（{_sgn(d['avg_score'])}）")
    ent = cmp["entries"]
    if ent["improved"]:
        print(f"  改善 {len(ent['improved'])} 条：{'、'.join(ent['improved'][:6])}"
              f"{'…' if len(ent['improved']) > 6 else ''}")
    if ent["regressed"]:
        print(f"  退化 {len(ent['regressed'])} 条：{'、'.join(ent['regressed'][:6])}"
              f"{'…' if len(ent['regressed']) > 6 else ''}")
    if ent["added"]:
        print(f"  新增 {len(ent['added'])} 条：{'、'.join(ent['added'][:6])}"
              f"{'…' if len(ent['added']) > 6 else ''}")
    if ent["removed"]:
        print(f"  删除 {len(ent['removed'])} 条：{'、'.join(ent['removed'][:6])}"
              f"{'…' if len(ent['removed']) > 6 else ''}")
    print(f"  → {cmp['advice']}")


def review_series(global_path, setting_path=None,
                  history_dir="data/outline/history"):
    """按版本顺序体检全部历史（v1..vN）+ 当前，返回指标列表。

    这是「迭代 50 轮」视角：一眼看出每轮是否有进展。
    """
    series = []
    hist = Path(history_dir)
    if hist.is_dir():
        vers = []
        for p in hist.glob("global_v*.md"):
            m = re.match(r"global_v(\d+)\.md$", p.name)
            if m:
                vers.append((int(m.group(1)), p))
        for n, p in sorted(vers):
            series.append(_fingerprint(review(p, setting_path),
                                       label=f"v{n}（第{n}轮前）", path=str(p)))
    if global_path and Path(global_path).exists():
        series.append(_fingerprint(review(global_path, setting_path),
                                   label="当前", path=str(global_path)))
    return series


def convergence_verdict(series, window=3):
    """从指标序列判断是否已收敛。返回 (状态, 说明)。

    判据（确定性，看**最近 window 轮**）：
      - 最新一轮 `key == 0`                → `done`    已达标，可开工
      - 最近 window 轮 `key` 全部相同       → `stalled` 停滞，收益递减
      - 最近 window 轮 `key` 严格递减       → `improving` 仍在改善
      - 其余（有升有降）                    → `mixed`   波动，需人工判断
      - 序列不足 2 条                       → `insufficient` 样本不足
    """
    if len(series) < 2:
        return "insufficient", "历史版本不足（至少需要 2 个版本才能判断趋势）。"
    tail = series[-window:] if len(series) >= window else series
    keys = [e["key"] for e in tail]
    last = series[-1]

    if last["key"] == 0:
        return "done", (f"最新版无结构性问题、无碎片条目（{last['total']} 条全部达标）"
                        f"—— 可以开工。")
    if len(set(keys)) == 1:
        return "stalled", (f"最近 {len(tail)} 轮的问题数**完全没变**（均为 {keys[-1]}）"
                           f"—— 收益已递减。建议换角度提意见、补素材，或直接开工。")
    if all(keys[i] > keys[i + 1] for i in range(len(keys) - 1)):
        return "improving", (f"最近 {len(tail)} 轮问题数持续下降（{'→'.join(map(str, keys))}）"
                             f"—— 仍在改善，可继续迭代。")
    return "mixed", (f"最近 {len(tail)} 轮问题数有升有降（{'→'.join(map(str, keys))}）"
                     f"—— 建议回看具体是哪几条在反复，换策略而非继续微调。")


def print_trend(series):
    """打印迭代趋势表。"""
    if not series:
        print("[outline_review] 无版本记录（先跑 stage2 或 refine_outline）")
        return
    print("[outline_review] 迭代趋势（每行 = 一个版本；key = 问题数 + 碎片数，越小越好）")
    print(f"  {'版本':<14}{'条目':>5}{'OK':>4}{'WARN':>5}{'THIN':>5}"
          f"{'问题':>5}{'平均分':>7}{'判定':>7}")
    for e in series:
        print(f"  {e['label']:<14}{e['total']:>5}{e['ok']:>4}{e['warn']:>5}"
              f"{e['thin']:>5}{e['issues']:>5}{e['avg_score']:>7.2f}{e['verdict']:>7}")
    status, note = convergence_verdict(series)
    tag = {"done": "✅ 已收敛", "stalled": "⚠ 停滞", "improving": "↘ 改善中",
           "mixed": "⚡ 波动", "insufficient": "· 样本不足"}.get(status, status)
    print(f"  → {tag}：{note}")


def render_markdown(result):
    """渲染详细报告（写盘用）。"""
    lines = [
        "# 大纲体检报告",
        "",
        f"- 大纲文件: `{result['path']}`",
        f"- 设定集加载: {'是' if result['setting_loaded'] else '否（角色/场景维度按通过计）'}",
        f"- 预计章节数: {result['expected_chapters']}",
        "",
        "## 关键节点",
        "",
        "| 节点 | 评分 | 结论 | 篇幅 | 事件 | 冲突 | 角色 | 场景 | 说明 |",
        "|------|------|------|------|------|------|------|------|------|",
    ]
    for e in result["nodes"]:
        lines.append(_row(e))
    lines += ["", "## 章节规划", "",
              "| 规划 | 评分 | 结论 | 篇幅 | 事件 | 冲突 | 角色 | 场景 | 说明 |",
              "|------|------|------|------|------|------|------|------|------|"]
    for e in result["plan"]:
        lines.append(_row(e))
    lines += ["", "## 整体检查", ""]
    if result["issues"]:
        lines += [f"- ⚠ {i}" for i in result["issues"]]
    else:
        lines.append("- 无一致性问题")
    lines += ["", "## 结论",
              f"- 条目: {result['summary']['total']} 条 "
              f"（OK {result['summary']['ok']} / WARN {result['summary']['warn']} / "
              f"THIN {result['summary']['thin']}）",
              f"- 判定: **{result['summary']['verdict']}**",
              "",
              "- OK: 信息充实；WARN: 可能空泛，建议审阅；THIN: 明显空泛，建议精修",
              "- 体检为启发式提示，非质量判决；重点看 THIN 项是否确实缺驱动事件/角色/场景",
    ]
    return "\n".join(lines) + "\n"


def _row(e):
    f = e["flags"]
    mark = lambda v: "✓" if v else "✗"
    return (f"| {e['title']} | {e['score']} | {e['level']} | {e['chars']} "
            f"| {mark(f.get('event', False))} | {mark(f.get('conflict', False))} "
            f"| {mark(f.get('role', True))} | {mark(f.get('scene', False))} "
            f"| {'；'.join(e['reasons'][:3])} |")


def print_summary(result):
    s = result["summary"]
    print(f"[outline_review] 大纲体检: {s['total']} 条目 "
          f"(OK {s['ok']} / WARN {s['warn']} / THIN {s['thin']}) → {s['verdict']}")
    for e in result["nodes"] + result["plan"]:
        if e["level"] != "OK":
            print(f"  [{e['level']}] {e['title']}: {e['chars']}字 "
                  f"{'；'.join(e['reasons'][:2])}")
    for i in result["issues"]:
        print(f"  [!] {i}")


def main():
    parser = argparse.ArgumentParser(description="NovelForge 大纲体检报告（确定性）")
    parser.add_argument("--outline", default="data/outline/global.md")
    parser.add_argument("--setting", default="data/setting/setting.json")
    parser.add_argument("--report", default="data/outline/review_report.md")
    parser.add_argument("--history", default="data/outline/history",
                        help="版本备份目录（用于对比与趋势）")
    parser.add_argument("--quiet", action="store_true", help="仅打印结论，不写报告")
    parser.add_argument("--trend", action="store_true",
                        help="打印全部版本的迭代趋势 + 收敛判断（回答「还要不要再来一轮」）")
    parser.add_argument("--no-compare", action="store_true",
                        help="跳过与上一版的对比")
    args = parser.parse_args()

    if not Path(args.outline).exists():
        print(f"[outline_review] 大纲不存在: {args.outline}（先跑 stage2）")
        return 1
    result = review(args.outline, args.setting)
    print_summary(result)

    # 迭代收敛视图（确定性，零 token）
    if args.trend:
        print()
        print_trend(review_series(args.outline, args.setting,
                                 history_dir=args.history))
    elif not args.no_compare:
        cmp = compare_with_previous(args.outline, args.setting,
                                    history_dir=args.history)
        if cmp:
            print()
            print_compare(cmp)

    if not args.quiet:
        write_text(args.report, render_markdown(result))
        print(f"[outline_review] 详细报告: {args.report}")
    return 0 if result["summary"]["verdict"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
