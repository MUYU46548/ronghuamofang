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
    parser.add_argument("--quiet", action="store_true", help="仅打印结论，不写报告")
    args = parser.parse_args()

    if not Path(args.outline).exists():
        print(f"[outline_review] 大纲不存在: {args.outline}（先跑 stage2）")
        return 1
    result = review(args.outline, args.setting)
    print_summary(result)
    if not args.quiet:
        write_text(args.report, render_markdown(result))
        print(f"[outline_review] 详细报告: {args.report}")
    return 0 if result["summary"]["verdict"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
