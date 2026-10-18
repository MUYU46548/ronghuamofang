# -*- coding: utf-8 -*-
"""讨论简报打包（P0-1 简报投递的后端半边）。

## 为什么需要它

用户的实际场景是**绒花墨坊桌面客户端 + 一个常开的 Agent 桌面端（Hermes）并存**。
讨论回路发生在 Agent 那边（它有多轮记忆、能真读文件、能跑体检），墨坊这边只负责
「把讨论对象打包好」与「接住讨论结论」。中间那一步此前是空白 —— 用户得自己去
翻文件路径、复制章大纲、说明白要讨论什么。

本模块把「发起讨论」收敛成一次打包：读章大纲 + 相关设定 + 上一章末尾 + 待审优化点
+ 摩擦提醒，产出一份**自包含**的任务书。用户把它贴给 Agent，Agent 就有足够上下文
开聊，不必回头追问「文件在哪」。

## 与 kickoff 的区别（别搞混）

`prompts/agent_kickoff.md` 是**开工提示词**：告诉一个新接手的 Agent「这是什么项目、
怎么自检、红线在哪」。本模块产出的是**单次讨论任务书**：告诉一个已经在跑的 Agent
「现在就这一章、这一段，讨论这个意见」。前者是长期契约，后者是一次性工单。

## ⚠️ 简报里不得出现成对花括号

这是外部 Agent 的「这是模板不是任务」停下判据。模板占位符一旦原样落进简报，
接手方第一步就停工报告（2026-10-11 实测踩过）。所以本模块**不用任何模板渲染**，
所有文本都是确定性拼接 + 真实文件内容。
"""
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text

CHAPTERS_DIR = "data/outline/chapters"
CHAPTER_RAW_DIR = "data/chapters"
SETTING_PATH = "data/setting/setting.json"
ROLLING_PATH = "data/summaries/rolling.md"
DISCUSS_MANIFEST = "data/state/discuss_manifest.json"

# 简报长度保护：整份任务书超过这个字数就截断长块（Agent 侧读原文更准）。
# 用户口径：写作子动作单章 5 万 token 甚至更高都可以 —— 这里的截断只防「把整本书
# 塞进剪贴板」这种极端，正常一章的上下文远够不着。
BRIEF_CHAR_CAP = 60000
LONG_BLOCK_CAP = 8000


def _clip_block(text, cap=LONG_BLOCK_CAP, tag=""):
    text = text or ""
    if len(text) <= cap:
        return text
    return (text[:cap]
            + f"\n\n（{tag or '本段'}超长已截断：原 {len(text)} 字，"
              f"完整内容请自行读取源文件）")


def _chapter_no(name):
    """从文件名（03.md / 3.md）取章号；取不到返回 0。"""
    m = re.match(r"^0*(\d+)\.md$", str(name))
    return int(m.group(1)) if m else 0


def _pending_opts(root):
    """待审讨论提案 → {章号: [提案路径]}（Agent 需要知道哪些还悬着）。"""
    from utils import discuss
    try:
        items = discuss.queue(status="pending", root=str(root))
    except Exception:                                  # noqa: BLE001
        return {}
    out = {}
    for it in items:
        n = 0
        m = re.search(r"ch(\d+)_", str(it.get("path") or ""))
        if m:
            n = int(m.group(1))
        if n:
            out.setdefault(n, []).append(str(it.get("path")))
    return out


def build_chapter_brief(n, note="", root=".", project_root_label=""):
    """打包「讨论第 n 章」的自包含任务书。返回 (ok, msg, brief_text)。

    内容顺序刻意按「先结论后材料」：Agent 一上来就知道要讨论什么、意见是什么，
    然后才是支撑材料（章大纲 / 待审优化点 / 相关角色 / 上一章末尾）。
    """
    root = Path(root)
    n = int(n)
    if not (1 <= n <= 999):
        return False, "章号须为 1-999", ""

    chap_path = root / CHAPTERS_DIR / f"{n:02d}.md"
    if not chap_path.exists():
        return False, f"第 {n} 章大纲不存在（{CHAPTERS_DIR}/{n:02d}.md），先跑 stage3", ""

    label = project_root_label or str(root)
    chap_md = read_text(chap_path)

    parts = [
        f"# 讨论任务：第 {n} 章",
        "",
        f"- 项目根：{label}",
        f"- 讨论对象：{CHAPTERS_DIR}/{n:02d}.md（单章大纲）",
        f"- 发起时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 用户意见",
        "",
        (note or "（用户未填具体意见 —— 请就本章大纲的结构、节奏、角色站位、"
                 "伏笔铺设提出可讨论的问题，并给出 2-3 个候选改进方向）"),
        "",
        "## 要做的事",
        "",
        "1. 读上面那份章大纲（相对项目根）。",
        "2. 结合下方材料，就用户意见展开**多轮讨论**——先确认你读懂了问题，"
        "再给候选方向，等用户挑/改，不要一轮就下结论。",
        "3. 讨论收敛后，把结论整理成一条**优化点提案**提交（见下方提交方式）。"
        "提案进待审区，用户批准才写回大纲 —— **不要直接改章大纲文件**。",
        "",
        "## 提交方式",
        "",
        "讨论收敛后执行（一条命令，同步返回）：",
        "",
        "```",
        "POST http://127.0.0.1:8765/outline/discussion/apply",
        "Content-Type: application/json",
        "",
        f'{{"target_n": {n}, "title": "一句话标题", "body": "要写回章大纲的优化点正文",'
        ' "rationale": "为什么改", "candidates": ["方向A", "方向B"],'
        ' "chosen": "方向A", "rounds": 3}',
        "```",
        "",
        "字段说明：`body` 是优化点正文（**不要**包含 `## 【优化点N】` 标题行 ——"
        "编号由系统按章内现状顺延，写了会撞号）；`candidates`/`chosen` 记录分支"
        "选择（有分支可视化时尤其要填）；`rounds` 是讨论轮次。",
        "",
        "## 红线",
        "",
        "- **不要直接写** `data/outline/chapters/*.md` —— 提案走上面的端点，"
        "写回由用户在 GUI 审核页签批准后自动完成。",
        "- **不要代替用户批准**（`/outline/discussion/review` 在 Agent 禁用名单里）。",
        "- 管线状态文件（progress.json / runs.db）只有 orchestrator 能写。",
        "- 本任务只讨论这一章，不要顺手重跑整条流水线。",
        "",
        "## 材料",
        "",
        f"### 第 {n} 章大纲（{CHAPTERS_DIR}/{n:02d}.md）",
        "",
        _clip_block(chap_md, tag="章大纲"),
        "",
    ]

    # 待审提案（这章还有没有悬着的讨论）
    pending = _pending_opts(root).get(n)
    if pending:
        parts += [
            "### 本章待审提案（尚未批准，讨论时可推翻或补充）",
            "",
        ]
        for p in pending:
            parts.append(f"- {p}")
        parts.append("")

    # 相关角色：从章大纲的「涉及角色」行取，再回设定集捞卡
    roles = _roles_from_chapter(chap_md)
    if roles:
        parts += [f"### 相关角色设定（{len(roles)} 位）", ""]
        setting = root / SETTING_PATH
        if setting.exists():
            cards = _role_cards(setting, roles)
            if cards:
                parts += [_clip_block(cards, tag="角色设定"), ""]
            else:
                parts.append("（设定集里没找到这些角色的条目）")
                parts.append("")
        else:
            parts.append("（设定集不存在，跳过）")
            parts.append("")

    # 上一章末尾（衔接上下文）
    prev = root / CHAPTERS_DIR / f"{n - 1:02d}.md"
    if n > 1 and prev.exists():
        prev_md = read_text(prev)
        tail = prev_md[-1200:] if len(prev_md) > 1200 else prev_md
        parts += [
            f"### 上一章大纲末尾（{CHAPTERS_DIR}/{n - 1:02d}.md，仅供衔接参考）",
            "",
            tail,
            "",
        ]

    # 本章已写初稿（若 stage4 已跑过）
    for sub in ("refined", "checked", "raw"):
        cpath = root / CHAPTER_RAW_DIR / sub / f"{n:02d}.md"
        if cpath.exists():
            draft = read_text(cpath)
            parts += [
                f"### 本章现有初稿（{CHAPTER_RAW_DIR}/{sub}/{n:02d}.md，"
                f"{len(draft)} 字）",
                "",
                _clip_block(draft, cap=12000, tag="初稿"),
                "",
            ]
            break

    # 滚动摘要（全书走向，Agent 判断「这章该往哪走」要用）
    rolling = root / ROLLING_PATH
    if rolling.exists():
        rmd = read_text(rolling)
        parts += [
            f"### 滚动摘要（{ROLLING_PATH}，全书走向）",
            "",
            _clip_block(rmd[:3000], tag="滚动摘要"),
            "",
        ]

    brief = "\n".join(parts).rstrip() + "\n"

    # 整份保护：超上限就砍材料段（保留任务段与红线 —— 那是 Agent 必读）
    if len(brief) > BRIEF_CHAR_CAP:
        brief = _cap_brief(brief, BRIEF_CHAR_CAP)

    # 终检：不得出现成对花括号（外部 Agent 的停下判据）
    if brief.count("{") >= 2 and brief.count("}") >= 2:
        # 提交示例里的 JSON 花括号是**成对示例**，属白名单形态；这里只拦「模板感」的
        # 单个占位符形态 {{xxx}}
        if re.search(r"\{\{[^}]{1,40}\}\}", brief):
            return False, "简报里出现模板占位符（成对花括号），已拒绝产出", ""

    return True, f"已打包第 {n} 章讨论任务书（{len(brief)} 字）", brief


def _cap_brief(brief, cap):
    """超长时保留头部（任务/红线）+ 砍材料尾部。"""
    lines = brief.split("\n")
    head, tail = [], []
    budget = cap
    for ln in lines:
        if budget - len(ln) > 0:
            head.append(ln)
            budget -= len(ln)
        else:
            tail.append(ln)
    note = (f"\n\n（材料段超长已截断：原 {len(brief)} 字，"
            f"保留 {len(head)} 字。完整材料请自行读取上面列出的源文件路径）\n")
    return "\n".join(head) + note


def _roles_from_chapter(chap_md):
    """从章大纲的「涉及角色」行取角色名（分隔符兼容后端解析）。"""
    m = re.search(r"^-\s*涉及角色[：:]\s*(.+?)\s*$", chap_md, re.M)
    if not m:
        return []
    raw = m.group(1).strip()
    names = re.split(r"[；;、,，]\s*", raw)
    return [x.strip() for x in names if x.strip()][:12]


def _role_cards(setting_path, roles, cap_per_role=600):
    """从 setting.json 捞角色卡（只取 name/description/traits，控制体积）。"""
    import json
    try:
        data = json.loads(read_text(setting_path))
    except Exception:                                  # noqa: BLE001
        return ""
    chars = data.get("characters") or []
    want = list(roles)
    blocks = []
    for r in want:
        for c in chars:
            if str(c.get("name") or "").strip() == r:
                desc = str(c.get("description") or "").strip()[:cap_per_role]
                traits = c.get("traits") or []
                tr = "、".join(str(t) for t in traits[:6]) if traits else ""
                blocks.append(f"- **{r}**：{desc}" + (f"（特点：{tr}）" if tr else ""))
                break
    return "\n".join(blocks)
