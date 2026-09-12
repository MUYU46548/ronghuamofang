# -*- coding: utf-8 -*-
"""global.md 的结构化解析 / 生成 / 局部替换（GUI 大纲视图的数据底座）。

职责（只做结构，不做评分）：
- parse_global(text)       把 global.md 解析为 {acts, nodes, plan, expected_chapters}
- render_global(struct)    把结构化数据渲染回 global.md 文本（固定格式）
- replace_node(text, id, new_text)   只替换某一条关键节点
- replace_plan(text, id, new_text)   只替换某一条章节规划
- build_char_index(setting_path)     角色名索引（节点属性面板关联用）
- match_chars(text, names)           从文本里匹配已知角色名

评分逻辑不在此模块：节点评分仍由 outline_review.review() 提供（红线：只调用不改写）。
"""
import re
from pathlib import Path

from utils.file_io import read_text

# 固定分节（格式规范，不得增删）
ACT_NAMES = ["起", "承", "转", "合"]
NODE_SEC = "关键节点"
COUNT_SEC = "预计章节数"
PLAN_SEC = "章节规划"

# 关键节点：- 节点N：... / - 节点N ... / - 节点N: ...
NODE_LINE_RE = re.compile(r"^[-*]\s*(节点\s*\d+)\s*[：:，,]?\s*(.*)$")
# 章节规划：- 第N章：... / - 第 3 章：... / 支持中文数字
PLAN_LINE_RE = re.compile(r"^[-*]\s*(第\s*[\d一二三四五六七八九十百零两]+\s*章)\s*[：:，,]?\s*(.*)$")

# 把所有「## 起/承/转/合/关键节点/预计章节数/章节规划」视为分节边界
_SECTION_RE = re.compile(
    r"^##\s*(起|承|转|合|关键节点|预计章节数|章节规划)\s*$", re.M)


def _node_id(i):
    return "node_%d" % i


def _ch_id(i):
    return "ch_%d" % i


def _parse_act_blocks(text):
    """返回 {act_name: 正文文本}，缺失的 act 值为 None。"""
    acts = {a: None for a in ACT_NAMES}
    heads = list(_SECTION_RE.finditer(text))
    for idx, h in enumerate(heads):
        name = h.group(1)
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        block = text[h.end():end]
        if name in acts:
            # 去掉块首尾空行，保留内部换行
            acts[name] = block.strip("\n").strip()
    return acts


def _parse_expected(text):
    m = re.search(r"^##\s*预计章节数\s*\n\s*(\d+)", text, re.M)
    if not m:
        # 容错：分节存在但数字在同一/后续行
        m2 = re.search(r"^##\s*预计章节数[^\d]*(\d+)", text, re.M)
        return int(m2.group(1)) if m2 else None
    return int(m.group(1))


def parse_global(text):
    """把 global.md 文本解析为结构化 dict。

    返回：
      {
        "title": "……",                # 一级标题（书名行，原样）
        "acts": {"起": "...", ...},     # 四节正文
        "nodes": [{"id","title","text","tail"}],
        "plan":  [{"id","title","text","tail"}],
        "expected_chapters": int|None,
        "raw": 原文,
      }
    text/tail 说明：行形如 `- 节点1：事件概述`，title="节点1"，
    text="节点1：事件概述"（整条原文，回写用），tail="事件概述"（正文，评分用）。
    """
    title = ""
    m = re.match(r"^#\s+(.*)$", text, re.M)
    if m:
        title = m.group(1).strip()

    nodes, plan = [], []
    for line in text.splitlines():
        s = line.strip()
        nm = NODE_LINE_RE.match(s)
        if nm:
            nodes.append({
                "id": _node_id(len(nodes) + 1),
                "title": re.sub(r"\s+", "", nm.group(1)),
                "text": s[2:].strip() if s[:1] in "-*" else s,
                "tail": nm.group(2).strip(),
            })
            continue
        pm = PLAN_LINE_RE.match(s)
        if pm:
            plan.append({
                "id": _ch_id(len(plan) + 1),
                "title": re.sub(r"\s+", " ", pm.group(1)).strip(),
                "text": s[2:].strip() if s[:1] in "-*" else s,
                "tail": pm.group(2).strip(),
            })

    return {
        "title": title,
        "acts": _parse_act_blocks(text),
        "nodes": nodes,
        "plan": plan,
        "expected_chapters": _parse_expected(text),
        "raw": text,
    }


def render_global(struct):
    """按固定格式把结构化数据渲染回 global.md 文本。

    缺章处理：起/承/转/合四节始终输出（缺失写占位），保证格式规范。
    """
    title = (struct.get("title") or "").strip() or "整体大纲"
    title = title.lstrip("#").strip()                  # 容忍已带 # 的输入
    if not title:
        title = "整体大纲"
    if not title.endswith("整体大纲"):
        title = title + " 整体大纲"

    lines = ["# " + title, ""]

    acts = struct.get("acts") or {}
    for name in ACT_NAMES:
        body = (acts.get(name) or "").strip()
        lines.append("## " + name)
        lines.append(body if body else "（待补充）")
        lines.append("")

    lines.append("## " + NODE_SEC)
    nodes = struct.get("nodes") or []
    if nodes:
        for nd in nodes:
            lines.append("- " + _norm_entry(nd))
    else:
        lines.append("- （待补充关键节点）")
    lines.append("")

    lines.append("## " + COUNT_SEC)
    exp = struct.get("expected_chapters")
    lines.append(str(exp) if exp else "0")
    lines.append("")

    lines.append("## " + PLAN_SEC)
    plan = struct.get("plan") or []
    if plan:
        for pl in plan:
            lines.append("- " + _norm_entry(pl))
    else:
        lines.append("- （待补充章节规划）")
    lines.append("")

    return "\n".join(lines)


def _norm_entry(entry):
    """把一条节点/规划渲染为 `标题：正文` 形式（回写用）。"""
    title = (entry.get("title") or "").strip()
    tail = (entry.get("tail") or "").strip()
    if not title:
        # 无结构化标题时退回整条原文（去掉前缀横杠）
        return (entry.get("text") or "").lstrip("-* ").strip()
    if not tail:
        return title
    if tail.startswith(title):
        return tail          # 正文已含标题前缀（如「节点1：…」），原样输出
    return title + "：" + tail


def replace_entry(text, entry_id, new_text):
    """在 global.md 文本中替换一条节点/规划，其余内容原样保留。

    返回 (new_full_text, old_entry_text)；未找到返回 (None, None)。
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith(("-", "*")):
            continue
        nm = NODE_LINE_RE.match(s)
        pm = PLAN_LINE_RE.match(s)
        if not nm and not pm:
            continue
        if nm:
            idx = int(re.sub(r"\D", "", nm.group(1)) or 0)
            cur_id = _node_id(idx) if idx else None
        else:
            cur_id = None
            # 规划 id 按出现顺序计算
            seen = 0
            for j in range(i + 1):
                if PLAN_LINE_RE.match(lines[j].strip()):
                    seen += 1
            cur_id = _ch_id(seen)
        if cur_id != entry_id:
            continue
        old = s[2:].strip() if s[:1] in "-*" else s
        body = (new_text or "").strip()
        if not body:
            return None, None
        indent = line[:len(line) - len(line.lstrip())]
        prefix = "- " if s[:1] == "-" else "* "
        # 新文本若已带 `- ` 前缀则去掉，避免双前缀
        if body.startswith(("- ", "* ")):
            body = body[2:].strip()
        lines[i] = indent + prefix + body
        return "\n".join(lines), old
    return None, None


def build_char_index(setting_path=None):
    """从 setting.json 收集角色名列表（保持文件顺序）。"""
    names = []
    if setting_path and Path(setting_path).exists():
        try:
            import json
            data = json.loads(read_text(setting_path))
        except (ValueError, OSError):
            return names
        for c in data.get("characters", []) or []:
            if isinstance(c, dict) and c.get("name"):
                names.append(str(c["name"]).strip())
            elif isinstance(c, str):
                names.append(c.strip())
    return [n for n in names if n]


def match_chars(text, names):
    """返回文本中命中的角色名列表（按名字长度倒序，避免短名误吞）。"""
    if not text:
        return []
    hits = []
    for n in sorted(set(names), key=len, reverse=True):
        if n and n in text and n not in hits:
            hits.append(n)
    return hits
