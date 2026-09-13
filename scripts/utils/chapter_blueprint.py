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
  "hook": "为下一章埋下..."
}

渲染输入同上，输出标准 markdown 字符串。
"""
import re
from pathlib import Path

FUNC_CHOICES = ["铺垫", "推进", "转折", "高潮", "收束", "过渡"]

TITLE_RE = re.compile(r"^#{1,2}\s*第?\s*\d*\s*章?\s*(.+?)\s*$", re.M)
EVENTS_RE = re.compile(r"-\s*核心事件[：:]\s*\n((?:\s*\d+[.、]\s*.+\n?)*)")
ROLES_RE = re.compile(r"-\s*涉及角色[：:]\s*(.+)")
FUNC_RE = re.compile(r"-\s*功能[：:]\s*(.+)")
CARRY_RE = re.compile(r"-\s*衔接[：:]\s*([\s\S]*?)(?=\n## |\n- |\Z)")

EVENT_ITEM_RE = re.compile(r"^\s*\d+[.、]\s*(.+?)\s*$", re.M)


def parse_chapter_md(text: str) -> dict:
    """解析标准格式的单章大纲 markdown 为结构化 dict。"""
    d = {
        "title": "",
        "events": [],
        "roles": [],
        "function": "",
        "carryover": "",
        "hook": "",
    }
    m = TITLE_RE.search(text)
    if m:
        d["title"] = m.group(1).strip()

    m = EVENTS_RE.search(text)
    if m:
        d["events"] = [x.strip() for x in EVENT_ITEM_RE.findall(m.group(1)) if x.strip()]

    m = ROLES_RE.search(text)
    if m:
        # 角色可能以 ；; 、, ， 分隔
        raw = m.group(1).strip()
        roles = re.split(r"[；;、,，]\s*", raw)
        d["roles"] = [r.strip() for r in roles if r.strip()]

    m = FUNC_RE.search(text)
    if m:
        d["function"] = m.group(1).strip()

    m = CARRY_RE.search(text)
    if m:
        raw = m.group(1).strip()
        # 衔接可能包含"承接..."和"钩子..."两段
        carry, hook = "", ""
        if "钩子" in raw:
            parts = re.split(r"钩子[：:]?\s*", raw, maxsplit=1)
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

    return "\n".join(lines)


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
