# -*- coding: utf-8 -*-
"""决策文件：跨章、跨会话的“已经定了什么”（P1-4，2026-11-25）。

## 为什么不用摘要顶替

`data/summaries/rolling.md` 是**发生了什么**的压缩；模型每章都要重读一遍，
再自己推断“这个人到底怎么说话”“那只匣子到底开没开”。摘要会把语气与承诺磨掉，
而这两样恰恰是长篇最怕前后不一致的地方。

决策文件只记**作者已经拍板、后面不许改口**的事：人物语气、伏笔状态、禁用与设定。
它有三个硬约束：

1. **只追加，不重写**（`append` 永不改动已有行，也不重排）。
2. **每章最多 5 条**（超出的丢掉并出声）—— 记多了等于没记，模型会跳读。
3. **缺文件不算错**：没有决策文件就是“还没定过事”，任务书里那块渲染成空。

## 文件长这样

    # 决策文件（跨章/跨会话）
    <!-- 只追加，不重写。行的格式：- [标签] 内容 -->

    ## 第 3 章

    - [语气] 岑砚说话前会停半拍，不用成语
    - [伏笔] 老太太的石头还没还 [未回收]
    - [禁用] 不写内心独白式的解释段

标签目前是 `语气` / `伏笔` / `禁用` / `设定` 四种，写错也不拦（自由文本），
只是 `[伏笔]` + `[未回收]` 这一对会被 `open_foreshadows()` 收走，进接力包。

## 谁写它

阶段 4 每章结束时，从模型的产物里解析 `===DECISIONS===` 段（见
`stage4_writing.parse_decisions_block`），再调 `append()`。主控不替模型编决策。
"""
from __future__ import annotations

import re
from pathlib import Path

from utils.file_io import read_text, write_text

#: 决策文件位置（相对项目根；cwd 即项目根，与 data/ 其余路径同源）。
PATH = Path("data/canon/decisions.md")

#: 模型交决策的“收件箱”（P1-4 落地方式）。为什么不让模型把 `===DECISIONS===`
#: 写进正文文件：那样会**污染章节**（导出、字数、判据都会把它算进去），还得再
#: 反向剥一遍 —— 多一道剥就多一处会剥错。让它在旁边写一个小文件，主控只读不剥。
INBOX_DIR = Path("data/canon/_inbox")

HEADER = (
    "# 决策文件（跨章/跨会话）\n"
    "<!-- 只追加，不重写。行的格式：- [标签] 内容 -->\n"
)

TAG_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\s*(.+?)\s*$")
OPEN_MARK = "[未回收]"

MAX_PER_CHAPTER = 5


def _path(path=None):
    return Path(path) if path else PATH


def read(path=None):
    """读整份决策文件；不存在返回空串（不是异常）。"""
    p = _path(path)
    if not p.exists():
        return ""
    try:
        return read_text(p)
    except Exception:                                        # noqa: BLE001
        return ""


def _existing_lines(text):
    return {ln.strip() for ln in (text or "").splitlines() if ln.strip()}


def append(chapter_no, entries, path=None, max_per_chapter=MAX_PER_CHAPTER):
    """把本章的决策追加进去，返回真正写进去的条数。

    - `entries` 可以是多行文本（`===DECISIONS===` 段的原文），也可以是字符串列表；
      两种都按行拆，只认 `- [标签] 内容` 这一种形态（没有标签的行丢掉，
      避免把模型的解释性文字抄进决策文件）。
    - 同一条（逐字相同）已经在文件里就不重复追加 —— 重跑同一章很常见。
    - 超过 `max_per_chapter` 的丢掉并打印，不静默截断。
    """
    if isinstance(entries, str):
        raw_lines = entries.splitlines()
    else:
        raw_lines = []
        for e in (entries or []):
            raw_lines.extend(str(e).splitlines())

    picked, dropped = [], 0
    for ln in raw_lines:
        m = TAG_RE.match(ln)
        if not m:
            continue
        picked.append("- [%s] %s" % (m.group(1).strip(), m.group(2).strip()))
    if len(picked) > max_per_chapter:
        dropped = len(picked) - max_per_chapter
        picked = picked[:max_per_chapter]
    if not picked:
        return 0

    p = _path(path)
    text = read(p)
    seen = _existing_lines(text)
    fresh = [ln for ln in picked if ln not in seen]
    if not fresh:
        return 0

    if not text.strip():
        text = HEADER
    if not text.endswith("\n"):
        text += "\n"
    block = "\n## 第 %d 章\n\n" % int(chapter_no) + "\n".join(fresh) + "\n"
    write_text(p, text + block)
    if dropped:
        print("[canon] 第%d章决策 %d 条超出单章上限 %d，已丢弃 %d 条"
              % (int(chapter_no), len(picked) + dropped, max_per_chapter, dropped))
    return len(fresh)


def inbox_path(chapter_no, inbox_dir=None):
    """模型该把本章决策写到哪（任务书里照抄这个路径）。"""
    d = Path(inbox_dir) if inbox_dir else INBOX_DIR
    return d / ("%02d.md" % int(chapter_no))


def harvest(chapter_no, path=None, inbox_dir=None):
    """把收件箱里那一章的决策并进决策文件，返回并入条数。

    收件箱文件不存在 = 模型这一章没交决策（不算错，返回 0）。
    文件**不删**：`append` 自带去重，重跑同一章不会写两遍；
    留着还能回答“这一章的决策是哪来的”。
    """
    src = inbox_path(chapter_no, inbox_dir)
    if not src.exists():
        return 0
    try:
        body = read_text(src)
    except Exception as e:                                   # noqa: BLE001
        print("[canon] 第%d章决策收件箱读不出来（本章不并决策）: %s: %s"
              % (int(chapter_no), type(e).__name__, e))
        return 0
    return append(chapter_no, body, path=path)


def open_foreshadows(text=None, path=None):
    """取所有还挂着的伏笔（行里带 `[未回收]`），按出现顺序返回内容（不含标签）。"""
    body = text if text is not None else read(path)
    out = []
    for ln in (body or "").splitlines():
        if OPEN_MARK not in ln:
            continue
        m = TAG_RE.match(ln)
        if not m:
            continue
        out.append(m.group(2).replace(OPEN_MARK, "").strip())
    return out


def chapter_lines(chapter_no, text=None, path=None):
    """取某一章记下的决策原文行（`- [标签] 内容`），接力包用得到。"""
    body = text if text is not None else read(path)
    want = "## 第 %d 章" % int(chapter_no)
    out, on = [], False
    for ln in (body or "").splitlines():
        if ln.strip().startswith("## "):
            on = ln.strip() == want
            continue
        if on and TAG_RE.match(ln):
            out.append(ln.strip())
    return out


def render_for_prompt(chapter_no=None, text=None, path=None, budget=4000):
    """给任务书用的块：最近几章的决策 + 还没回收的伏笔。

    `chapter_no` 为空 = 整份照抄（首次建任务书时用）。超预算时从最早的章开始截，
    并写明截了多少 —— 不静默少喂（同 stage4 其余上下文块的纪律）。
    """
    body = text if text is not None else read(path)
    if not (body or "").strip():
        return ""
    opens = open_foreshadows(body)
    head = "### 已定的决策（只追加，不重写；与本节冲突的写法一律按本节）\n\n"
    detail = body.strip()
    if len(detail) > budget:
        cut = len(detail) - budget
        detail = detail[-budget:]
        # 从第一个完整章节标题开始，免得把半行抄进去
        idx = detail.find("\n## ")
        if idx > 0:
            detail = detail[idx + 1:]
        detail += "\n\n（更早的决策省略约 %d 字）" % cut
    tail = ""
    if opens:
        tail = "\n\n### 还没回收的伏笔（写这一章时至少碰一下，别让它烂掉）\n" + \
               "\n".join("- " + x for x in opens)
    return head + detail + tail
