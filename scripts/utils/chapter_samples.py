# -*- coding: utf-8 -*-
"""分支样例锚点（第 4 件 · 选项可视化）—— 把用户选中的写作样例喂给 stage4。

## 解决什么

用户反馈：「竞品有选项可视化，分支样例方向直观可选，显然根本没做进绒花墨坊里，
这条信息又在某次会话中丢失了。如果我这里不提就永远没了。」

此前 stage4 写作只吃 5 个骨架字段（标题/核心事件/涉及角色/功能/衔接），样例文
**没有位置安放**。现在讨论协议的锚点机制（`<!--opt-anchor -->`）已经就位，
本模块把它接到 stage4：用户在章大纲里选定「样例文本」→ stage4 生成任务时读出来
→ 注入成 few-shot 锚点。

## 为什么读 opt_points 而不是新增字段

锚点块本身就是「讨论沉淀」的载体，样例文属于同一类东西（都是用户磨出来的、
写给 AI 看的参照）。复用它就不用改 `chapter_blueprint` 的 schema，也不用改
GUI 蓝图编辑器的往返逻辑 —— 那两处刚为无损往返修过，再动有回归风险。

## 注入原则（2026-10-18 用户口径）

用户明确说过：「整套写作用不了多少 token，之前试跑的几千万大部分都是 agent
行动产生的」，且「预算给高，单章给 5 万 token 甚至更高都可以」。
**所以全量注入，不做 top-K 截断** —— 样例文是用户精心磨出来的，截断掉任何一段
都可能丢掉关键的语气/节奏信息。唯一的上界是单块 4000 字（防某块异常膨胀挤掉
设定切片），超出才截并标注原字数。
"""
import re
from pathlib import Path

from utils.file_io import read_text

CHAPTERS_DIR = "data/outline/chapters"
SAMPLE_BLOCK_CAP = 4000

# 优化点块的标题行：## 【优化点N】标题
_OPT_HEAD_RE = re.compile(r"^##\s*【优化点(\d+)】\s*(.*?)\s*$", re.M)
# 锚点 JSON：<!--opt-anchor {...} -->
_ANCHOR_RE = re.compile(r"<!--\s*opt-anchor\s*(\{[\s\S]*?\})\s*-->")


def _clip(text, tag):
    """超长块截断 + 明确标注原字数（不静默截断 —— 静默会让模型以为读全了）。"""
    text = text or ""
    if len(text) <= SAMPLE_BLOCK_CAP:
        return text
    return (text[:SAMPLE_BLOCK_CAP]
            + f"\n\n（{tag}超长已截断：原 {len(text)} 字，完整内容见章大纲文件）")


def _is_sample(anchor):
    """判断一个锚点是不是「样例文本」类。

    只认显式的 kind，避免把讨论意见、字段改写这类锚点误当样例喂给写作模型
    （那些是「为什么要改」，不是「照这个写」—— 混进去会污染 few-shot）。
    """
    if not isinstance(anchor, dict):
        return False
    kind = str(anchor.get("kind") or "").strip().lower()
    return kind in ("sample", "sample_text", "样例文", "样例")


def chapter_samples(chapter_no, root="."):
    """读某章的样例锚点，拼成 stage4 注入块。返回 str（无样例时为空串）。

    只从章大纲（data/outline/chapters/NN.md）的优化点块里挑 `kind` 为样例的，
    其余锚点（讨论意见、字段改写）一概不注入 —— 它们是给**人**看的决策记录，
    不是给写作模型的参照。
    """
    p = Path(root) / CHAPTERS_DIR / f"{int(chapter_no):02d}.md"
    if not p.exists():
        return ""
    text = read_text(p)

    blocks = []
    for raw, anchor in _iter_opt_blocks(text):
        if not _is_sample(anchor):
            continue
        title, body = _read_block(raw, anchor)
        if body:
            blocks.append((title, body))

    if not blocks:
        return ""

    parts = ["### 本章样例文本（作者选定的写法参照，语气/节奏/密度照此把握）", ""]
    for title, body in blocks:
        parts.append(f"**{title}**")
        parts.append("")
        parts.append(_clip(body, title))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# 优化点块的起点：## 【优化点N】（不锚定行尾 —— 标题行可能嵌着整段正文）
_OPT_BLOCK_START_RE = re.compile(r"^##\s*【优化点(\d+)】", re.M)


def _iter_opt_blocks(text):
    """把章大纲切成（原文块, 锚点 dict）序列。

    以「下一个 ## 标题」切块，而不是假设标题是单行 —— append_opt_point
    写回时会把整段样例文塞进 ## 【优化点N】 之后，多行样例在单行正则下整个
    块被漏掉（实测：GUI 批准方向样例后 stage4 读不到，few-shot 静默失效）。
    """
    heads = list(_OPT_BLOCK_START_RE.finditer(text or ""))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        raw = text[m.start():end].strip("\n")
        anchor = {}
        am = _ANCHOR_RE.search(raw)
        if am:
            import json
            try:
                anchor = json.loads(am.group(1))
            except (ValueError, TypeError):
                anchor = {}
        yield raw, anchor


def _looks_like_title(text):
    """判断单行正则取出的「标题」是不是真的标题。

    append_opt_point 把整段样例文塞在 ## 【优化点N】 之后，单行正则会把正文
    当标题返回 —— 标题一旦取成正文，正文就被当成标题显示，样例文位置颠倒。

    判据只能用长度：正文必然比标题长得多，而**不能**用「带句末标点」—— 正常
    标题「说服段该用具体案例。」就带句号，用它判会把好标题误降级成编号。
    """
    t = (text or "").strip()
    return bool(t) and len(t) <= 60


def _block_title(raw, anchor):
    """块标题的两个稳定来源：锚点 label → 优化点编号。

    **不从标题行取**——`append_opt_point` 没有独立标题行，标题行里躺着的是正文
    首行，拿它当标题会把正文位置颠倒（见 `_read_block`）。
    """
    if anchor.get("label"):
        return str(anchor["label"]).strip()
    n = anchor.get("n") or "?"
    return f"优化点{n}"


def _read_block(raw, anchor):
    """把一个优化点块读成（标题, 正文）。三种写回格式都要不丢首行、不重复。

    `append_opt_point`（discuss.apply 的唯一写入口）产出 `## 【优化点N】{正文}` ——
    **没有独立标题行**，正文首行就落在 `## ` 之后。原读法整行剥掉标题行，于是：

    - 正文只有 1 行时 → body 为空 → `if body:` 判空 → **整块被丢弃**
      （GUI 批准方向样例后 stage4 静默读不到，few-shot 失效，无任何报错）；
    - 正文多行时 → 首行静默丢失。

    分支（顺序即优先级）：

    1. 锚点带显式 text/sample → 直接用它，标题行不参与；
    2. 锚点带 label（方向样例提交时写入）→ label 作标题，首行**归正文**；
    3. 标题行短、且后面还有正文 → 手写格式，标题行是标题，正文 = 其余部分；
    4. 其余（正文全落在标题行里）→ 编号作标题，首行整段归正文。
    """
    explicit = (anchor.get("text") or anchor.get("sample") or "").strip()
    if explicit:
        return _block_title(raw, anchor), explicit

    head = _head_rest(raw)
    rest = _ANCHOR_RE.sub("", _OPT_HEAD_RE.sub("", raw, count=1)).strip("\n").strip()
    both = (head + "\n\n" + rest).strip() if (head and rest) else (head or rest)

    label = str(anchor.get("label") or "").strip()
    if label and both:
        # 情形 2：标题来自 label，首行不能被当成标题吞掉
        return label, both
    if head and rest and _looks_like_title(head):
        # 情形 3：`## 【优化点N】标题` + 正文 —— 手写格式的真标题
        return head, rest
    # 情形 4（含只有一行正文）：编号作标题，首行归正文
    return _block_title(raw, anchor), both


def _head_rest(raw):
    """标题行里去掉 `## 【优化点N】` 前缀后剩下的部分（可能是样例文首行）。"""
    m = _OPT_BLOCK_START_RE.match(raw or "")
    if not m:
        return ""
    return raw[m.end():].split("\n", 1)[0].strip()
