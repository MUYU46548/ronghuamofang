# -*- coding: utf-8 -*-
"""成品保真与“残稿可接管”的实测探针（C5 / C6，2026-11-07）。

## 这个脚本量的是三件“口径不能靠猜”的事

| 编号 | 问题 | 为什么要量 |
|---|---|---|
| **N2** | 成品 docx 与源稿的差异到底有多少 | 若基线不为 0，导出侧“差异 = 0”这条判据要么永远红、要么被放宽成失效 |
| **N4** | 存量章里 raw / refined 双版留存率 | 决定版本回溯是“存量可回放”还是“只有新写的能回放” |
| **N5** | 自然段切分对空行 / 单换行 / 混合是否确定 | 三套写法若切得不一样，“三次切分一致”这句承诺就没有口径 |
| **N3** | 残稿（C2）还能不能形成可指名段、能不能就地接管 | 否则“残稿可进就地编辑”只是说法 |

## 用法

    python scripts/fidelity_probe.py --all                    # 全部（自动找试跑工作区）
    python scripts/fidelity_probe.py --retention <工作区>
    python scripts/fidelity_probe.py --paragraphs
    python scripts/fidelity_probe.py --docx <成品.docx> --chapters <工作区/data/chapters>
    python scripts/fidelity_probe.py --partial <工作区>        # 残稿接管（在临时副本上做，不动原件）

输出：`data/state/fidelity_probe.json` + `data/state/fidelity_probe.md`（人读）。
**只读原件**：残稿实验在临时目录里复制一份再做，正文一个字都不动。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.annotations import split_paragraphs as split_ann          # noqa: E402
from utils.verify_chapter import count_cn_words                      # noqa: E402

OUT_JSON = Path("data/state/fidelity_probe.json")
OUT_MD = Path("data/state/fidelity_probe.md")

# 自然段切分探针的三种输入（N5）：空行分隔 / 单换行 / 混合
SAMPLES = {
    "空行分隔": "第一段。\n\n第二段。\n\n第三段。",
    "单换行": "第一段。\n第二段。\n第三段。",
    "混合": "第一段。\n\n第二段。\n第三段。\n\n第四段。",
}


def _default_workspace() -> Path | None:
    """找试跑工作区（优先 %APPDATA%/绒花墨坊/workspace）。找不到就返回 None。"""
    appdata = os.environ.get("APPDATA", "")
    for name in ("绒花墨坊", "绒花摩坊"):
        p = Path(appdata) / name / "workspace"
        if p.is_dir():
            return p
    return None


# ---------------------------------------------------------------- N5
def probe_paragraphs() -> Dict[str, Any]:
    """自然段切分：三种输入 × 3 次，看是否确定、各切出几段。"""
    rows = {}
    for name, text in SAMPLES.items():
        runs = [split_ann(text) for _ in range(3)]
        same = runs[0] == runs[1] == runs[2]
        rows[name] = {"paragraphs": runs[0], "count": len(runs[0]), "stable": same}
    # 两套实现必须同源（annotations 只是转调 refine_paragraph）
    try:
        import refine_paragraph
        alt = {n: refine_paragraph.split_paragraphs(t) for n, t in SAMPLES.items()}
        same_impl = all(alt[n] == rows[n]["paragraphs"] for n in SAMPLES)
    except Exception as e:                                     # noqa: BLE001
        same_impl = False
        alt = {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return {"samples": rows, "two_impls_agree": same_impl,
            "alt": {k: (v if isinstance(v, str) else len(v)) for k, v in alt.items()},
            "verdict": ("确定：三次切分一致，且两处实现同源"
                        if all(r["stable"] for r in rows.values()) and same_impl
                        else "不确定：需要先定口径")}


# ---------------------------------------------------------------- N4
def probe_retention(ws: Path) -> Dict[str, Any]:
    """存量章各层留存：有多少章同时有 raw 与 refined（决定回溯能不能覆盖存量）。"""
    base = ws / "data" / "chapters"
    layers = {}
    for lay in ("raw", "checked", "refined"):
        d = base / lay
        layers[lay] = sorted(int(p.stem) for p in d.glob("*.md") if p.stem.isdigit())
    all_n = sorted({n for v in layers.values() for n in v})
    both = sorted(set(layers["raw"]) & set(layers["refined"]))
    only_refined = sorted(set(layers["refined"]) - set(layers["raw"]))
    return {
        "workspace": str(ws),
        "chapters": len(all_n),
        "per_layer": {k: len(v) for k, v in layers.items()},
        "raw_and_refined": len(both),
        "retention_rate": round(len(both) / len(all_n), 3) if all_n else 0.0,
        "only_refined": only_refined,
        "verdict": ("存量可回放：%d/%d 章有 raw+refined" % (len(both), len(all_n))
                    if all_n and len(both) == len(all_n)
                    else "存量不能全覆盖：%d/%d 章两版齐全" % (len(both), len(all_n))),
    }


# ---------------------------------------------------------------- N2
def _norm(s: str) -> str:
    """比较用归一化：去空白、去标点，只留汉字与字母数字。"""
    import re
    return re.sub(r"[\s\W_]+", "", s or "", flags=re.UNICODE)


def probe_docx(docx: Path, chapters_dir: Path) -> Dict[str, Any]:
    """成品 docx 与源稿（refined，逐章拼接）的差异：段落级对齐后统计。"""
    try:
        from docx import Document
    except ImportError:
        return {"error": "未安装 python-docx（pip install python-docx）"}
    if not docx.exists():
        return {"error": "成品不存在：%s" % docx}

    doc = Document(str(docx))
    doc_paras = [p.text for p in doc.paragraphs]
    doc_norm = [(_norm(t), t) for t in doc_paras if _norm(t)]

    src = []
    refined = chapters_dir / "refined"
    for f in sorted(p for p in refined.glob("*.md") if p.stem.isdigit()):
        text = f.read_text(encoding="utf-8")
        for para in split_ann(text):
            if _norm(para):
                src.append((_norm(para), f.stem, para))

    doc_set = {n for n, _ in doc_norm}
    src_set = {n for n, _, _ in src}
    missing = [(ch, t) for n, ch, t in src if n not in doc_set]      # 源稿有、成品没有
    extra_idx = [(i, t) for i, (n, t) in enumerate(doc_norm) if n not in src_set]

    # 分类（2026-11-07）：差异要分成“该有的差异”和“真丢了”
    #   · 源稿有而成品无：`<!-- quality: … -->` / `<!-- summary: … -->` 这类**内部注释**，
    #     本来就不该出现在给读者的成品里 —— 它们是流水线自己的元数据；
    #   · 成品有而源稿无：封面书名、目录提示（“按 F9 更新目录”）、分卷页 —— 成品附加页。
    # 只有“其它”那部分才是真保真缺陷。
    def _is_internal(t: str) -> bool:
        return "<!--" in t or "-->" in t or t.strip().startswith("<!--")

    miss_internal = [t for _, t in missing if _is_internal(t)]
    miss_other = [(c, t) for c, t in missing if not _is_internal(t)]
    # 成品附加页：出现在**第一章之前**的段落（封面书名 / 版权 / 目录提示），
    # 以及含目录类关键词的段落。用位置判据而不是关键词，才不会漏掉“书名”那一段。
    import re as _re
    first_ch = next((i for i, (n, t) in enumerate(doc_norm)
                     if _re.search(r"第\s*\d+\s*章", n)), 10 ** 9)
    extra_front = [t for i, t in extra_idx
                   if i < first_ch or any(k in t for k in ("目录", "F9", "书名"))]
    extra_other = [t for i, t in extra_idx
                   if i >= first_ch and not any(k in t for k in ("目录", "F9", "书名"))]
    extra = [t for _, t in extra_idx]

    words_internal = sum(count_cn_words(t) for t in miss_internal)
    total_words_src = sum(count_cn_words(t) for _, _, t in src)
    total_words_doc = sum(count_cn_words(t) for _, t in doc_norm)
    real_diff = len(miss_other) + len(extra_other)
    return {
        "docx": str(docx),
        "chapters_dir": str(chapters_dir),
        "docx_paragraphs": len(doc_paras),          # 含空段（Word 里的空行）
        "docx_nonempty": len(doc_norm),
        "source_paragraphs": len(src),
        "missing_in_docx": len(missing),
        "extra_in_docx": len(extra),
        "missing_internal_comments": len(miss_internal),
        "extra_front_matter": len(extra_front),
        "real_diff": real_diff,
        "diff_total": len(missing) + len(extra),
        "words_source": total_words_src,
        "words_docx": total_words_doc,
        "words_in_internal_comments": words_internal,
        "word_delta": total_words_doc - total_words_src,
        "missing_samples": [{"chapter": c, "text": t[:40]} for c, t in missing[:5]],
        "extra_samples": [t[:40] for t in extra[:5]],
        "real_diff_samples": ([{"chapter": c, "text": t[:60]} for c, t in miss_other[:5]]
                              + [t[:60] for t in extra_other[:5]]),
        "verdict": ("保真：%d 处差异全是内部注释与成品附加页，真正的丢失 0 处" % (len(missing) + len(extra))
                    if real_diff == 0
                    else "保真缺口：%d 处真差异（注释/附加页之外）" % real_diff),
    }


# ---------------------------------------------------------------- N3 / C5
def probe_partial(ws: Path) -> Dict[str, Any]:
    """残稿（C2）可接管性：在**临时副本**上把一章截断，看还能不能接管。

    验四件事：
    1. 截断稿仍能切出自然段（≥1 段才谈得上“就地编辑”）；
    2. 截断稿被判“不完整”（不许当成品放行）；
    3. 补回后半段后能重新判完整（就地编辑这条路真能救回来）；
    4. 接管过程中原件一个字节都没动（用 sha256 比对）。
    """
    import hashlib
    base = ws / "data" / "chapters" / "refined"
    files = sorted(p for p in base.glob("*.md") if p.stem.isdigit())
    if not files:
        return {"error": "没有 refined 章节可做残稿实验"}
    src = files[0]
    before_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    tmp = Path(tempfile.mkdtemp(prefix="nf_partial_"))
    dst = tmp / src.name
    shutil.copy2(src, dst)

    from utils.verify_chapter import is_chapter_complete
    full_text = dst.read_text(encoding="utf-8")
    cut = max(200, int(len(full_text) * 0.55))
    partial = full_text[:cut]
    if not partial.rstrip().endswith(("。", "！", "？", "…", "\"", "”")):
        partial = partial.rstrip() + "……"          # 模拟“写到一半断了”
    dst.write_text(partial, encoding="utf-8")

    paras = split_ann(partial)
    ok_full = is_chapter_complete(src, 4000, 7200)
    ok_partial = is_chapter_complete(dst, 4000, 7200)
    # 就地接管：把后半段补回去（模拟用户在编辑器里补完），再看是否判完整
    dst.write_text(full_text, encoding="utf-8")
    ok_repaired = is_chapter_complete(dst, 4000, 7200)
    after_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    shutil.rmtree(tmp, ignore_errors=True)

    return {
        "chapter": src.stem,
        "partial_chars": len(partial),
        "partial_paragraphs": len(paras),
        "complete_before": ok_full,
        "complete_partial": ok_partial,
        "complete_after_repair": ok_repaired,
        "original_untouched": before_sha == after_sha,
        "verdict": ("残稿可接管：能切段（%d 段）、不被误判成品、补齐后恢复完整判定"
                    % len(paras))
                   if paras and not ok_partial and ok_repaired else "残稿接管链路有缺口",
    }


def render_md(d: Dict[str, Any]) -> str:
    L = ["# 成品保真 / 残稿接管 实测（C5 · C6）", "",
         "> 由 `scripts/fidelity_probe.py` 生成；只读原件，残稿实验在临时副本上做。", ""]
    p = d.get("paragraphs") or {}
    if p:
        L += ["## N5 自然段切分确定性", "",
              "| 输入 | 切出段数 | 三次一致 |", "|---|---|---|"]
        for name, row in (p.get("samples") or {}).items():
            L.append("| %s | %d | %s |" % (name, row["count"], "是" if row["stable"] else "否"))
        L += ["", "- 两处实现同源：%s" % ("是" if p.get("two_impls_agree") else "**否**"),
              "- 结论：**%s**" % p.get("verdict"), ""]
    r = d.get("retention") or {}
    if r and "error" not in r:
        L += ["## N4 存量双版留存", "",
              "- 工作区：`%s`" % r["workspace"],
              "- 章数：%d；各层：%s" % (r["chapters"], r["per_layer"]),
              "- raw + refined 齐全：%d 章（留存率 %.1f%%）"
              % (r["raw_and_refined"], r["retention_rate"] * 100),
              "- 结论：**%s**" % r["verdict"], ""]
    x = d.get("docx") or {}
    if x and "error" not in x:
        L += ["## N2 成品与源稿差异", "",
              "- 成品：`%s`" % Path(x["docx"]).name,
              "- 段落：成品 %d（非空 %d） / 源稿 %d"
              % (x["docx_paragraphs"], x["docx_nonempty"], x["source_paragraphs"]),
              "- 差异 %d 处，其中：内部注释 **%d** 处、成品附加页 **%d** 处、**真丢失 %d 处**"
              % (x["diff_total"], x["missing_internal_comments"],
                 x["extra_front_matter"], x["real_diff"]),
              "- 汉字数：源稿 %s / 成品 %s（差 %+d；其中注释里 %s 字）"
              % (format(x["words_source"], ","), format(x["words_docx"], ","),
                 x["word_delta"], format(x["words_in_internal_comments"], ",")),
              "- 结论：**%s**" % x["verdict"]]
        if x.get("real_diff_samples"):
            L.append("- 真差异样本（**该修**）：%s" % "；".join(
                (s[:60] if isinstance(s, str) else "第%s章 %s" % (s.get("chapter"), s.get("text", "")))
                for s in x["real_diff_samples"]))
        L.append("")
    q = d.get("partial") or {}
    if q and "error" not in q:
        L += ["## N3 / C5 残稿可接管性", "",
              "- 实验章：第 %s 章（截到 %d 字）" % (q["chapter"], q["partial_chars"]),
              "- 截断稿仍能切出 **%d** 段（可指名）" % q["partial_paragraphs"],
              "- 完整性判定：原件 %s / 残稿 %s / 补齐后 %s"
              % (q["complete_before"], q["complete_partial"], q["complete_after_repair"]),
              "- 原件未被改动：%s" % ("是" if q["original_untouched"] else "**否**"),
              "- 结论：**%s**" % q["verdict"], ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="成品保真 / 残稿接管 实测")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--paragraphs", action="store_true")
    ap.add_argument("--retention", nargs="?", const="__auto__")
    ap.add_argument("--docx")
    ap.add_argument("--chapters")
    ap.add_argument("--partial", nargs="?", const="__auto__")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    out: Dict[str, Any] = {}
    if args.paragraphs or args.all:
        out["paragraphs"] = probe_paragraphs()

    ws = None
    for flag in (args.retention, args.partial):
        if flag and flag != "__auto__":
            ws = Path(flag)
    if ws is None and (args.all or args.retention == "__auto__" or args.partial == "__auto__"):
        ws = _default_workspace()
    if (args.retention or args.all) and ws:
        out["retention"] = probe_retention(ws)
    if (args.partial or args.all) and ws:
        out["partial"] = probe_partial(ws)

    if args.docx and args.chapters:
        out["docx"] = probe_docx(Path(args.docx), Path(args.chapters))
    elif args.all and ws:
        d = ws / "output"
        cands = sorted(d.glob("*.docx")) if d.is_dir() else []
        if cands:
            out["docx"] = probe_docx(cands[-1], ws / "data" / "chapters")

    base = Path(args.out_dir)
    (base / OUT_JSON).parent.mkdir(parents=True, exist_ok=True)
    (base / OUT_JSON).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_md(out)
    (base / OUT_MD).write_text(md, encoding="utf-8")
    print(md)
    print("\n原始读数 -> %s" % (base / OUT_JSON))
    return 0


if __name__ == "__main__":
    sys.exit(main())
