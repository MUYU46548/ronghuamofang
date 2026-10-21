# -*- coding: utf-8 -*-
"""handoff 交接稿（2026-10-20，v0.10「可带走」）。

## 为什么要有它

换模型 / 换会话 / 换机器，上下文全靠重跑 —— 作品没有一种「可携带形态」。
交接稿 = 把「这本书现在写到哪了、接下来该写什么」压成**一份人可读、
可直接粘给任何模型**的 markdown：新会话只靠它就能写出接得上的下一章。

## 内容（全部确定性拼装，零 token）

`data/handoff/第N章后.md`：

1. **作品身份** —— 书名 / 文体 / 总章数 / 已写到第几章
2. **写作要求** —— style / style_notes / 单章字数区间（从 project.yaml + system.yaml 抄）
3. **设定摘要** —— 复用 `setting_brief.build()`（有预算上限，不贴全量 setting.json）
4. **未回收伏笔** —— 复用 `foreshadow.collect()`，只列 open / advanced（已回收的不占篇幅）
5. **最近章节摘要** —— 滚动摘要里的最近 3 章 + 上一章末尾原文段落（语气衔接用）
6. **下一章大纲** —— `data/outline/chapters/{N+1}.md` **全文内联**（一个字段都不能少）

## 判据（确定性）

- **下一章大纲的每个非空字段都必须出现在交接稿里**（假客户端可验证）——
  交接稿缺了「悬念钩子」，新会话写出的下一章就会丢钩子，这正是它要防的事故
- **不含本机绝对路径**：所有输入先过 `strip_protocol_markers`（老产物首行可能残留
  `===FILE: E:\\...===`），输出再过绝对路径扫描 —— 交接稿要去别人家，不能带门牌号
- **缺料明说，不编造**：下一章大纲没生成 → 写明「尚未生成 + 怎么补」；
  没有伏笔 / 没有摘要同理。空手交出去也要让接手方知道哪里是空的

## 边界

- 只读拼装器：不写 data/ 之外的任何东西，不调模型，不改书稿与进度
- 交接稿是**快照**：盘上状态变了它不会自动更新，重新导出即可（幂等覆盖）
"""
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text
from utils.config_io import load_config_yaml

HANDOFF_DIR = Path("data/handoff")
ROLLING_PATH = Path("data/summaries/rolling.md")
RAW_DIR = Path("data/chapters/raw")
OUTLINE_DIR = Path("data/outline/chapters")
SETTING_PATH = Path("data/setting/setting.json")
SYSTEM_PATH = Path("config/system.yaml")
PROJECT_PATH = Path("config/project.yaml")

# 绝对路径扫描：Windows 盘符 / UNC / POSIX 家目录。交接稿要去别人家，不能带门牌号。
_ABS_RE = re.compile(r"([A-Za-z]:\\|\\\\[A-Za-z0-9]|[A-Za-z]://(?!/)|/(?:Users|home|var|etc|private)/)")


def handoff_path(chapter, root="."):
    return Path(root) / HANDOFF_DIR / ("第%d章后.md" % int(chapter))


def _clean(text):
    """剥协议标记 + 质量注释（只留正文）。"""
    from utils.llm_client import strip_protocol_markers
    text = strip_protocol_markers(text or "")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def _written_chapters(root="."):
    d = Path(root) / RAW_DIR
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.md"):
        try:
            out.append(int(f.stem))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def build(chapter=None, root="."):
    """拼装交接稿。返回 {"text", "path", "chapter", "next_chapter", "problems"}。

    `chapter` = 「第几章之后」的那章；不传则取已写章节的最大章号。
    没写过任何章时 chapter=0（开局交接稿：只有设定与要求，没有「最近摘要」）。
    """
    root = Path(root)
    written = _written_chapters(root)
    if chapter is None:
        chapter = written[-1] if written else 0
    chapter = int(chapter)
    next_ch = chapter + 1
    problems = []

    from utils.llm_client import strip_protocol_markers

    # ---------------- 作品身份与写作要求
    try:
        proj = load_config_yaml(str(root / PROJECT_PATH)) or {}
    except Exception as e:                                   # noqa: BLE001
        proj = {}
        problems.append("config/project.yaml 读取失败：%r" % (e,))
    book = proj.get("book") or {}
    try:
        syscfg = load_config_yaml(str(root / SYSTEM_PATH)) or {}
    except Exception:                                        # noqa: BLE001
        syscfg = {}
    tw = ((syscfg.get("chapter") or {}).get("target_words")) or [5000, 9000]

    from utils import writing_form as _wf
    _app = _wf.applicability(proj)

    lines = ["# 交接稿：《%s》第 %d 章后" % (book.get("name") or "未命名", chapter), ""]
    lines += ["> 本文件由 `nfctl handoff` 生成，是**导出时刻的快照**。把全文粘给新会话/新模型，",
              "> 它就能写出接得上的下一章。盘上状态变了请重新导出。", ""]
    lines += ["## 一、作品身份", "",
              "- 书名：《%s》" % (book.get("name") or "未命名"),
              "- 文体：%s" % _app.get("label", "叙事小说"),
              "- 总章数：%s" % (book.get("chapters") or "未设定"),
              "- 已写到：第 %d 章（共写过 %d 章）" % (chapter, len(written)),
              "- 接下来要写：**第 %d 章**" % next_ch,
              ""]

    lines += ["## 二、写作要求", "",
              "- 单章字数区间：%d ~ %d 字" % (int(tw[0]), int(tw[1]))]
    if book.get("style"):
        lines.append("- 文风要求：%s" % book.get("style"))
    if str(book.get("style_notes") or "").strip():
        lines += ["- 用户风格笔记：", ""]
        lines += ["  " + ln for ln in str(book["style_notes"]).strip().splitlines()]
    lines.append("")

    # ---------------- 设定摘要
    lines += ["## 三、设定摘要", ""]
    from utils import setting_brief as _sb
    sb = _sb.build(setting_path=str(root / SETTING_PATH))
    if sb.get("block"):
        lines.append(_clean(sb["block"]))
        if sb.get("stats", {}).get("trimmed"):
            lines.append("")
            lines.append("> 注：设定集超过摘要预算，另有 %d 行未内联；需要细节时读 %s。"
                         % (sb["stats"]["trimmed"], SETTING_PATH.as_posix()))
    else:
        lines.append("> （%s）" % (sb.get("error") or "还没有设定集"))
        problems.append("没有设定集：%s" % SETTING_PATH.as_posix())
    lines.append("")

    # ---------------- 未回收伏笔
    lines += ["## 四、未回收伏笔", ""]
    from utils import foreshadow as _fs
    # ⚠️ foreshadow.STATUS_PATH 是模块级相对路径（依赖 CWD）。API 用 --root 指向
    # 别的书档时会读错书的人工标注 —— 这里显式重定向到 root（用完还原，不污染全局）。
    _saved_status = _fs.STATUS_PATH
    _fs.STATUS_PATH = root / "data" / "state" / "foreshadow_status.json"
    try:
        fs = _fs.collect(outline_dir=str(root / OUTLINE_DIR))
    finally:
        _fs.STATUS_PATH = _saved_status
    open_items = [r for r in fs.get("items") or [] if r.get("status") in ("open", "advanced")]
    if open_items:
        for r in open_items:
            lines.append("- `%s`（第 %d 埋设，%s，已闲置 %d 章）：%s"
                         % (r["id"], r["planted"],
                            "推进至第 %d 章" % r["last"] if r["status"] == "advanced" else "未推进",
                            r.get("idle", 0), r["text"]))
        stale = [r for r in open_items if r.get("stale")]
        if stale:
            lines.append("")
            lines.append("> 其中 %d 条超过 %d 章没推进 —— 下一章要么推进它，要么明确放弃。"
                         % (len(stale), fs.get("stale_after", 30)))
    else:
        lines.append("> （台账为空。没有章大纲就没有「悬念钩子」可抽；"
                     "跑过阶段 3 后自动生成。）")
    lines.append("")

    # ---------------- 最近章节摘要 + 上一章原文尾
    lines += ["## 五、最近章节（语气与事实的衔接基准）", ""]
    from utils import summary_chain as _sc
    rolling = _sc.load_rolling(root / ROLLING_PATH)
    recent = sorted(rolling.get("chapters") or {})[-3:] if rolling.get("chapters") else []
    if chapter > 0 and recent:
        for no in recent:
            lines.append("### 第 %d 章摘要" % no)
            lines.append("")
            lines.append(_clean(rolling["chapters"][no]))
            lines.append("")
    elif chapter > 0:
        lines.append("> （滚动摘要为空 —— 写下一章前先补摘要，否则新会话不知道前文发生了什么。）")
        problems.append("滚动摘要缺失：%s" % ROLLING_PATH.as_posix())
        lines.append("")
    else:
        lines.append("> （开局交接稿：还没有已写章节，没有前文可述。）")
        lines.append("")
    if chapter > 0:
        raw_prev = root / RAW_DIR / ("%02d.md" % chapter)
        if not raw_prev.exists():
            raw_prev = root / RAW_DIR / ("%d.md" % chapter)
        if raw_prev.exists():
            tails = _sc.extract_prev_tail(raw_prev, n_paras=3)
            if tails:
                lines.append("### 上一章（第 %d 章）末尾原文" % chapter)
                lines.append("")
                lines += [_clean(p) for p in tails]
                lines.append("")

    # ---------------- 下一章大纲（全文内联）
    lines += ["## 六、下一章大纲（第 %d 章）" % next_ch, ""]
    nxt = root / OUTLINE_DIR / ("%02d.md" % next_ch)
    if not nxt.exists():
        nxt = root / OUTLINE_DIR / ("%d.md" % next_ch)
    if nxt.exists():
        body = _clean(read_text(nxt))
        lines.append(body)
        # 判据：大纲里的每个非空字段都必须出现在交接稿里（防截断/防清洗吃字段）
        from stage3_chapter_outline import parse_chapter_outline
        fields = parse_chapter_outline(nxt) or {}
        for name, val in fields.items():
            if name.startswith("_"):
                continue
            if val and val.strip() and val.strip() not in "\n".join(lines):
                problems.append("下一章大纲字段「%s」没进交接稿" % name)
    else:
        lines.append("> **第 %d 章的大纲还没生成**（%s 不存在）。先跑阶段 3，再重新导出交接稿。"
                     % (next_ch, (OUTLINE_DIR / ("%02d.md" % next_ch)).as_posix()))
        problems.append("下一章大纲缺失：第 %d 章" % next_ch)
    lines.append("")

    text = "\n".join(lines).rstrip() + "\n"

    # 终判：绝对路径不许出稿
    hit = _ABS_RE.search(text)
    if hit:
        problems.append("交接稿里出现本机绝对路径：%s…" % hit.group(1))
    return {
        "text": text,
        "path": handoff_path(chapter, root).as_posix(),
        "chapter": chapter,
        "next_chapter": next_ch,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "problems": problems,
    }


def export(chapter=None, root="."):
    """拼装并落盘。返回 build() 的 dict（多一个 "written": True/False）。"""
    data = build(chapter, root=root)
    p = Path(root) / HANDOFF_DIR / ("第%d章后.md" % data["chapter"])
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, data["text"])
    data["written"] = True
    data["path"] = p.as_posix()
    return data
