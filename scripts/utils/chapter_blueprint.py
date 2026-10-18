# -*- coding: utf-8 -*-
"""单章大纲结构化解析/渲染（供蓝图编辑器读写）。

标准格式（与 stage3_chapter_outline.md 一致）：
## 第N章 章节名
- 核心事件：
  1. ...
  2. ...
- 涉及角色：...
- 功能：铺垫/推进/转折/高潮/收束/过渡
- 衔接：承接...；钩子...

解析输出 dict：
{
  "title": "章节名",
  "events": ["事件1", "事件2", ...],
  "roles": ["角色1", "角色2", ...],
  "function": "推进",
  "carryover": "承接上一章的...",
  "hook": "为下一章埋下...",
  "opt_points": [          # 讨论沉淀的【优化点】块（2026-10-18 落地协议）
    {"n": 1, "title": "说服力不足", "raw": "整块原文（含锚点注释）",
     "anchor": {...opt-anchor JSON...}}
  ]
}

渲染输入同上，输出标准 markdown 字符串。

## ⚠️ 优化点块必须无损往返（2026-10-18）

优化点是**讨论的沉淀**，只追加不覆盖。此前蓝图编辑器的保存路径只渲染 6 个骨架
字段，任何追加在章末的优化点块会在下次保存时被**静默删除** —— 讨论成果当场蒸发。
现在 parse 先把优化点切出来、render 再原样拼回去，`raw` 保留逐字节原文（含
`<!--opt-anchor -->` 注释），保证「agent 写入 → 用户在 GUI 改个错别字 → 保存」
这条路上优化点一字不丢。
"""
import json
import re
from pathlib import Path

FUNC_CHOICES = ["铺垫", "推进", "转折", "高潮", "收束", "过渡"]

TITLE_RE = re.compile(r"^#{1,2}\s*第?\s*\d*\s*章?\s*(.+?)\s*$", re.M)
EVENTS_RE = re.compile(r"-\s*核心事件[：:]\s*\n((?:\s*\d+[.、]\s*.+\n?)*)")
ROLES_RE = re.compile(r"-\s*涉及角色[：:]\s*(.+)")
FUNC_RE = re.compile(r"-\s*功能[：:]\s*(.+)")
CARRY_RE = re.compile(r"-\s*衔接[：:]\s*([\s\S]*?)(?=\n## |\n- |\Z)")

EVENT_ITEM_RE = re.compile(r"^\s*\d+[.、]\s*(.+?)\s*$", re.M)

# 优化点块：从 `## 【优化点N】` 行到下一个 `## ` 或文件尾。
# 为什么用【】而不是普通标题：① 与章标题 `## 第N章` 明确区分，解析不歧义；
# ② 沿用用户真实大纲的既有形态（他的 27 章大纲就是这么记讨论成果的）。
OPT_HEAD_RE = re.compile(r"^##\s*【优化点(\d+)】\s*(.*?)\s*$", re.M)
ANCHOR_RE = re.compile(r"<!--\s*opt-anchor\s*(\{[\s\S]*?\})\s*-->")


def split_opt_sections(text):
    """把章节大纲切成（骨架 markdown, 优化点块列表）。

    **必须先切再解析骨架**：优化点正文里若出现「- 涉及角色：…」这类字样
    （样例文里写角色对话是常态），不切开会污染骨架字段。
    """
    heads = list(OPT_HEAD_RE.finditer(text or ""))
    if not heads:
        return (text or ""), []
    skeleton = text[:heads[0].start()].rstrip() + "\n"
    blocks = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        raw = text[m.start():end].strip("\n")
        anchor = {}
        am = ANCHOR_RE.search(raw)
        if am:
            try:
                anchor = json.loads(am.group(1))
            except (ValueError, TypeError):
                anchor = {}
        blocks.append({"n": int(m.group(1)), "title": m.group(2).strip(),
                       "raw": raw, "anchor": anchor})
    return skeleton, blocks


def parse_chapter_md(text: str) -> dict:
    """解析标准格式的单章大纲 markdown 为结构化 dict。"""
    d = {
        "title": "",
        "events": [],
        "roles": [],
        "function": "",
        "carryover": "",
        "hook": "",
        "opt_points": [],
    }
    # 先把优化点切出去，再解析骨架 —— 样例文里出现「- 涉及角色：」等字样
    # 不会污染骨架字段（见 split_opt_sections docstring）。
    skeleton, opts = split_opt_sections(text)
    d["opt_points"] = opts

    m = TITLE_RE.search(skeleton)
    if m:
        d["title"] = m.group(1).strip()

    m = EVENTS_RE.search(skeleton)
    if m:
        d["events"] = [x.strip() for x in EVENT_ITEM_RE.findall(m.group(1)) if x.strip()]

    m = ROLES_RE.search(skeleton)
    if m:
        # 角色可能以 ；; 、, ， 分隔
        raw = m.group(1).strip()
        roles = re.split(r"[；;、,，]\s*", raw)
        d["roles"] = [r.strip() for r in roles if r.strip()]

    m = FUNC_RE.search(skeleton)
    if m:
        d["function"] = m.group(1).strip()

    m = CARRY_RE.search(skeleton)
    if m:
        raw = m.group(1).strip()
        # 衔接可能包含"承接..."和"钩子..."两段
        carry, hook = "", ""
        if "钩子" in raw:
            # 分隔符可能是「钩子：」「钩子:」「钩子——」——统一切掉，否则破折号
            # 留进 hook 值里，而渲染时又拼一个「钩子——」→ 每轮 parse/render
            # 累积两个破折号（定向改写每批准一次就多一对，实测 3 轮后变 8 个）。
            parts = re.split(r"钩子\s*[：:—\-]*\s*", raw, maxsplit=1)
            carry = parts[0].strip().rstrip("；;。")
            hook = parts[1].strip() if len(parts) > 1 else ""
        else:
            carry = raw.rstrip("；;。")
        d["carryover"] = carry
        d["hook"] = hook

    return d


def render_chapter_md(d: dict) -> str:
    """将结构化 dict 渲染为标准 markdown 格式。"""
    title = d.get("title", "").strip() or "未命名章节"
    lines = [f"## {title}", ""]

    # 核心事件
    events = d.get("events", [])
    if events:
        lines.append("- 核心事件：")
        for i, ev in enumerate(events, 1):
            lines.append(f"  {i}. {ev}")
        lines.append("")

    # 涉及角色
    roles = d.get("roles", [])
    if roles:
        lines.append(f"- 涉及角色：{'；'.join(roles)}")
        lines.append("")

    # 功能
    func = d.get("function", "").strip()
    if func:
        lines.append(f"- 功能：{func}")
        lines.append("")

    # 衔接
    carry = d.get("carryover", "").strip().rstrip("；;。")
    hook = d.get("hook", "").strip()
    if carry or hook:
        parts = []
        if carry:
            parts.append(carry)
        if hook:
            parts.append(f"钩子——{hook}")
        lines.append(f"- 衔接：{'; '.join(parts)}")
        lines.append("")

    # 优化点：原样拼回（讨论沉淀，只追加不覆盖）
    opts = d.get("opt_points") or []
    if opts:
        lines.append("")
        for o in opts:
            raw = (o.get("raw") or "").strip("\n")
            if raw:
                lines.append(raw)
                lines.append("")

    return "\n".join(lines)


def next_opt_number(text):
    """当前章节大纲里下一个可用的优化点编号（已有 1,2 → 返回 3）。"""
    _, opts = split_opt_sections(text or "")
    return max((o["n"] for o in opts), default=0) + 1


def append_opt_point(chapter_text, body_md, anchor=None):
    """把一个优化点块追加到章节大纲末尾。返回新文本。

    编号顺延（提案里写「优化点1」是提案内部的序号，落到章里要接上已有的号）。
    锚点的 `n` 用顺延后的编号，保证「章内编号 == 锚点编号」可反查。
    """
    n = next_opt_number(chapter_text)
    anchor = dict(anchor or {})
    anchor["n"] = n
    block = f"## 【优化点{n}】{(body_md or '').strip()}\n\n" \
            f"<!--opt-anchor {json.dumps(anchor, ensure_ascii=False)} -->"
    base = (chapter_text or "").rstrip("\n")
    return f"{base}\n\n{block}\n"


def save_chapter_blueprint(n: int, data: dict, backup=True) -> tuple:
    """
    保存单章大纲（结构化 dict → 标准 markdown → 文件）。
    返回 (ok, msg)。
    """
    from utils.file_io import read_text, write_text
    from scripts.utils.outline_struct import replace_entry

    chapters_dir = Path("data/outline/chapters")
    chapters_dir.mkdir(parents=True, exist_ok=True)
    path = chapters_dir / f"{n:02d}.md"

    # 写前备份
    if backup and path.exists():
        import shutil
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_name(f"{n:02d}_v{ts}.bak")
        shutil.copy2(path, bak)

    content = render_chapter_md(data)

    # 校验：标题不能为空
    if not data.get("title", "").strip():
        return False, "章节名不能为空"

    # 校验：事件至少一条
    if not data.get("events"):
        return False, "至少需要一条核心事件"

    # 校验：涉及角色至少一个
    if not data.get("roles"):
        return False, "至少需要一个涉及角色"

    write_text(path, content)
    return True, f"已保存 {path.name}"
