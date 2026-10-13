# -*- coding: utf-8 -*-
"""外部 Word 稿导入器（活二 · 自由模式 + Word 导入，2026-10-13 规格 v1.1）。

## 干什么

把**已经在墨坊之外写好**的 .docx 稿件导进项目，当成"已写好的章节"，
之后用自由模式（`orchestrator --stages`）挑着跑后面的工序。
解决的实际场景：稿子在外面生成好了，只想拿墨坊的提示词润色 / 校对 / 出 Word。

流程五段：**读取 → 切分 → 预检确认 → 落位 → 对账**。

    python scripts/import_docx.py 稿件.docx                    # 预检清单，确认后落盘
    python scripts/import_docx.py 稿件.docx --preview          # 只看不写
    python scripts/import_docx.py 稿件.docx --yes               # 跳过交互确认
    python scripts/import_docx.py 稿件.docx --as checked        # 落到「校对过」层
    python scripts/import_docx.py 稿件.docx --single            # 兜底1：整本当一章
    python scripts/import_docx.py 稿件.docx --split-by "^第.{1,6}幕"  # 兜底2：自定义分隔
    python scripts/import_docx.py 稿件.docx --json              # 机器可读输出（给接口用）

## 四条红线（问题卡「不可越」）

1. **输入 docx 一个字节都不动**：只读；落盘后复核 sha256，不一致直接判失败。
2. **预检未过不落盘**：没确认（`--yes` / 交互回答 y）之前，一个文件都不写。
3. **切分复用现成件**：走 `book_split.split_chapters`（中文回目 / Chapter /
   编号行 / Markdown 标题四种模式取命中最多），不另起炉灶；docx 的标题样式
   在读取阶段预处理成 `#` 标题，让现成的 Markdown 模式直接认。
4. **落位前打 before-import 快照**（丙3 契约）：`utils.import_guard.preimport`，
   快照失败即中止导入。

## 落位三档 = 起点三档

`data/chapters/{raw,checked,refined}/`。出稿时 stage7 逐章按 refined > checked >
raw 回退，所以导入到哪一层，就等于从哪一道工序开始用：
raw = 检查/润色/出稿都能跑；checked = 跳过校对直接润色；refined = 直接出稿。

## 兜底（不许静默当一章）

整篇识别不出任何章节结构 → 报「未识别出章节结构」+ 两条出路（`--single` /
`--split-by`），**不落任何文件**。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_split  # noqa: E402  （scripts/ 顶层模块，自举已在上一行）
from utils.file_io import write_text  # noqa: E402
from utils.project_config import load_project_yaml  # noqa: E402

TIERS = ("raw", "checked", "refined")
TIER_CN = {"raw": "毛坯（raw，从检查起可用）",
           "checked": "校对过（checked，从润色起可用）",
           "refined": "润过（refined，直接出稿）"}
# doctor 里与「导入」直接相关的检查项；其余（全局大纲等）是整书体检，
# 导入时上游工序本来就没跑，**不算导入失败**（否则新书首次导入必报红）。
CHAPTER_CHECKS = ("raw 章节文件", "checked 章节文件", "refined 章节文件")
MAX_INPUT_CHARS = 8_000_000

_HEADING_STYLE = re.compile(r"^(?:Heading|标题)\s*(\d)", re.IGNORECASE)
_HEADING_BARE = re.compile(r"^(?:Heading|标题)\b", re.IGNORECASE)


# ---------------------------------------------------------------- 1. 读取
def _heading_level(paragraph):
    """段落标题级别；非标题返回 0。只认 Heading N / 标题 N 样式。"""
    try:
        name = (paragraph.style.name or "").strip()
    except Exception:                                       # noqa: BLE001
        return 0
    m = _HEADING_STYLE.match(name)
    if m:
        return max(1, min(3, int(m.group(1))))              # book_split 只认 #{1,3}
    if _HEADING_BARE.match(name):
        return 1
    return 0


def extract_text(path):
    """docx → 纯文本（Markdown 形态）：只取文字与标题级别，**不搬格式**。

    标题段转成 `# …` 行，让 `book_split` 现成的「Markdown 标题」模式直接认；
    普通段落各成一段（空行分隔），下游 stage7 合并时才不会把整篇挤成一坨。

    **输入文件只读**（红线1）。
    """
    from docx import Document                                # 延迟 import，缺依赖给可行动报错
    doc = Document(str(path))
    blocks = []
    total = 0
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        total += len(text)
        if total > MAX_INPUT_CHARS:
            raise ValueError(f"文档超过单次导入上限 {MAX_INPUT_CHARS} 字符，请先分卷")
        lvl = _heading_level(p)
        blocks.append(("#" * lvl + " " + text) if lvl else text)
    return "\n\n".join(blocks)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- 2. 切分
def split_text(text, single=False, split_by=None):
    """切章。返回 `(mode, chapters)`；`mode is None` = 未识别（调用方给兜底出路）。

    三种来源：`--single` 整本一章 / `--split-by` 自定义分隔行 / 默认复用
    `book_split.split_chapters`（红线3，不另起炉灶）。
    """
    if single:
        return "整本单章（--single）", [
            {"title": "（整本单章）", "body": text, "start": 0}]
    if split_by:
        try:
            rx = re.compile(split_by, re.MULTILINE)
        except re.error as e:
            return f"自定义分隔规则语法错：{e}", None
        ms = list(rx.finditer(text))
        if not ms:
            return f"自定义分隔规则没有匹配到任何行：{split_by}", None
        chapters = []
        head = text[:ms[0].start()].strip()
        if head:
            chapters.append({"title": "（分隔符前的引子）", "body": head, "start": 0})
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
            chapters.append({"title": m.group(0).strip()[:60],
                             "body": text[m.start():end], "start": m.start()})
        return f"自定义分隔（{split_by}）", chapters
    name, chapters = book_split.split_chapters(text)
    if name is None:                                         # detect_pattern 全不适用
        return None, None
    return name, chapters


def _chars(body):
    return len(re.sub(r"\s+", "", body or ""))


def preview_lines(chapters):
    """预检清单：序号 / 标题 / 字数（规格第 3 条：确认才落盘）。"""
    return ["%02d  %-46s %s 字" % (i, (c.get("title") or "").strip()[:46],
                                   _chars(c.get("body")))
            for i, c in enumerate(chapters, 1)]


# ---------------------------------------------------------------- 4/5. 落位 + 对账
def _doctor(root):
    """跑墨坊自己的数据体检（复用 `nfctl.collect_doctor`），并把**与导入无关**的
    红项单独摘出来 —— 新书首次导入时全局大纲当然还不存在，那不是导入的锅。"""
    try:
        from nfctl import collect_doctor
        doc = collect_doctor(Path(root))
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    items = doc.get("items", [])
    chapter_blocking = [it.get("name") for it in items
                        if it.get("name") in CHAPTER_CHECKS and not it.get("ok")]
    other = [b for b in doc.get("blocking", []) if b not in CHAPTER_CHECKS]
    doc["chapter_blocking"] = chapter_blocking
    doc["other_blocking"] = other
    note = "章节相关检查全绿" if not chapter_blocking else \
        "章节相关检查有红项：" + "、".join(chapter_blocking)
    if other:
        note += "；另有与导入无关的红项（上游工序尚未跑，属正常）：" + "、".join(other)
    doc["note"] = note
    return doc


def import_docx(src, tier="raw", root=".", yes=False, single=False,
                split_by=None, preview_only=False, overwrite=False,
                interactive=None):
    """导入主流程。返回 dict（`ok` / `status` / `error` / `warnings` / `doctor`…）。

    `interactive=None` 时按 `sys.stdin.isatty()` 判；测试与 HTTP 调用传 False，
    此时未给 `--yes` 就**只出预检清单、不落盘**（红线2，不卡在 stdin 上）。
    """
    root = Path(root)
    src = Path(src)
    res = {"ok": False, "wrote": 0, "tier": tier, "warnings": [],
           "input_file": str(src)}

    if not src.exists():
        res["error"] = f"找不到输入文件：{src}"
        return res
    if src.suffix.lower() != ".docx":
        res["error"] = (f"只支持 .docx（收到 {src.suffix or '无扩展名'}）。"
                        f"请先用 Word 另存为 .docx 再导入")
        return res
    if tier not in TIERS:
        res["error"] = f"落位档只能是 {'/'.join(TIERS)}（收到 {tier}）"
        return res
    if os.path.getsize(src) == 0:
        res["error"] = "输入文件是 0 字节"
        return res

    try:
        before = _sha256(src)
    except OSError as e:
        res["error"] = f"读不到输入文件：{e}"
        return res

    # ---- 1. 读取（只读）----
    try:
        text = extract_text(src)
    except Exception as e:                                   # noqa: BLE001
        res["error"] = f"读取 docx 失败：{type(e).__name__}: {e}"
        return res
    if not text.strip():
        res["error"] = "文档里没有可导入的文字（空文档？）"
        return res

    # ---- 2. 切分 ----
    mode, chapters = split_text(text, single=single, split_by=split_by)
    if chapters is None or mode is None:
        why = mode if isinstance(mode, str) else "未识别出章节结构"
        res.update(
            error=why,
            suggestions=[
                "出路一：整本当一章 —— 加 --single（章节序号会只有 01）",
                "出路二：自己给分隔规则 —— 加 --split-by \"<正则>\"，"
                "每个匹配行开一章，例如 --split-by \"^第.{1,6}幕\"",
                "先只看不写确认效果：加 --preview",
            ])
        return res

    res.update(pattern=mode, chapters=chapters,
               preview=preview_lines(chapters),
               chapter_count=len(chapters),
               total_chars=sum(_chars(c.get("body")) for c in chapters))

    # ---- 3. 预检（未确认不落盘，红线2/4）----
    if preview_only:
        res.update(ok=True, status="preview")
        return res

    out_dir = root / "data" / "chapters" / tier
    targets = [out_dir / ("%02d.md" % i) for i in range(1, len(chapters) + 1)]
    existing = [t.name for t in targets if t.exists()]
    if existing and not overwrite:
        res.update(status="conflict",
                   error=(f"目标层 {tier}/ 已有 {len(existing)} 个同名章节文件"
                          f"（{'、'.join(existing[:5])}{'…' if len(existing) > 5 else ''}），"
                          f"不静默覆盖"),
                   suggestion="确认要覆盖请加 --overwrite（落盘前会先打 before-import 快照）")
        return res

    if not yes:
        if interactive is None:
            interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
        if not interactive:
            res.update(ok=True, status="needs_confirm",
                       note="已生成预检清单但**未落盘**：确认无误后加 --yes 再跑一次")
            return res
        print("预检清单（切分结果，确认才落盘）：")
        for line in res["preview"]:
            print("  " + line)
        ans = input("确认落盘到 data/chapters/%s/ ? [y/N] " % tier).strip().lower()
        if ans not in ("y", "yes"):
            res.update(status="aborted", error="已取消，未落盘")
            return res

    # ---- 4. 落位（丙3：动手前先快照）----
    from utils.import_guard import ImportGuardError, preimport
    try:
        entry = preimport("import_docx", root=root)
    except ImportGuardError as e:
        res["error"] = str(e)
        return res
    res["snapshot_id"] = entry.get("snapshot_id")

    out_dir.mkdir(parents=True, exist_ok=True)
    for i, ch in enumerate(chapters, 1):
        body = (ch.get("body") or "").strip()
        write_text(out_dir / ("%02d.md" % i), body + "\n")
        res["wrote"] += 1

    # ---- 5. 对账 ----
    res["doctor"] = _doctor(root)
    chap_block = res["doctor"].get("chapter_blocking") or []
    if chap_block:
        res["ok"] = False
        res["status"] = "doctor_failed"
        res["error"] = "导入后数据体检未过（章节相关红项）：" + "、".join(chap_block)
        return res

    target = 0
    try:
        proj = load_project_yaml(str(root / "config" / "project.yaml")) or {}
        target = int((proj.get("book") or {}).get("chapters") or 0)
    except Exception:                                         # noqa: BLE001
        target = 0
    if target and target != res["wrote"]:
        res["warnings"].append(
            f"章节数 {res['wrote']} 与项目目标 {target} 章不一致（黄提示，不拦截）")

    try:
        after = _sha256(src)
    except OSError as e:
        res["ok"] = False
        res["error"] = f"导入后读不到输入文件了：{e}"
        return res
    if after != before:                                       # 红线1
        res["ok"] = False
        res["status"] = "input_modified"
        res["error"] = "输入文件的 sha256 变了 —— 导入器写了你的 Word 原件（红线违规）"
        return res
    res["input_unchanged"] = True

    res.update(ok=True, status="written")
    return res


# ---------------------------------------------------------------- CLI
def _print_result(res):
    if res.get("preview"):
        print("预检清单（切分结果）：")
        for line in res["preview"]:
            print("  " + line)
        print(f"  共 {res['chapter_count']} 章 / {res['total_chars']} 字"
              f"，切分模式：{res['pattern']}")
    if not res.get("ok"):
        print("导入失败：" + str(res.get("error")))
        for s in res.get("suggestions", []):
            print("  · " + s)
        if res.get("suggestion"):
            print("  · " + res["suggestion"])
        return
    if res.get("status") == "preview":
        print("（--preview：只看不写，未落盘）")
        return
    if res.get("status") == "needs_confirm":
        print(res.get("note"))
        return
    print(f"导入成功：{res['wrote']} 章 → data/chapters/{res['tier']}/"
          f"（落位：{TIER_CN[res['tier']]}）")
    if res.get("snapshot_id"):
        print(f"回退点：{res['snapshot_id']}（python scripts/snapshot.py "
              f"--restore {res['snapshot_id']} --yes）")
    doc = res.get("doctor") or {}
    if doc.get("note"):
        print("体检：" + doc["note"])
    for w in res.get("warnings", []):
        print("提示：" + w)
    print("下一步（自由模式，只跑你勾的）："
          f"python scripts/orchestrator.py --stages {'5,6,7' if res['tier'] == 'raw' else '6,7'}")


def main():
    parser = argparse.ArgumentParser(
        description="外部 Word 稿导入器（读取→切分→预检→落位→对账）")
    parser.add_argument("docx", help="要导入的 .docx（只读，一个字节都不会动）")
    parser.add_argument("--as", dest="tier", default="raw", choices=TIERS,
                        help="落位档：raw=毛坯（默认）/ checked=校对过 / refined=润过")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认直接落盘")
    parser.add_argument("--preview", action="store_true", help="只出预检清单，不落盘")
    parser.add_argument("--single", action="store_true",
                        help="兜底1：识别不出章节结构时，整本当一章")
    parser.add_argument("--split-by", default=None, metavar="REGEX",
                        help="兜底2：自定义分隔行正则（每个匹配行开一章）")
    parser.add_argument("--overwrite", action="store_true",
                        help="允许覆盖目标层已有的同名章节文件（落盘前先打快照）")
    parser.add_argument("--root", default=".", help="项目根（默认当前目录）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="机器可读输出（给接口/脚本用）")
    args = parser.parse_args()

    res = import_docx(args.docx, tier=args.tier, root=args.root, yes=args.yes,
                      single=args.single, split_by=args.split_by,
                      preview_only=args.preview, overwrite=args.overwrite,
                      interactive=False if (args.yes or args.preview) else None)
    if args.as_json:
        out = {k: v for k, v in res.items() if k != "chapters"}
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        _print_result(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
