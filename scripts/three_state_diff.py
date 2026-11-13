# -*- coding: utf-8 -*-
"""三态对照：raw / checked / refined 逐段对齐，说清"哪一段在哪一步被删掉"。

## 为什么要有它

盘上已经躺着三态（`data/chapters/{raw,checked,refined}/NN.md`）。同一套判据在两本书上
行为完全不同（一本检查阶段砍掉四分之一，一本几乎不动），**却没有一处能回答"砍的是哪些段"**。
这些被删掉的段落是最准的判据素材：它们就是"审稿认为该删的东西"的实证清单。

## 纪律（别把它做成第二个判决）

1. **只给证据，不给结论**：输出的是"这一段在 checked 里没有 + 它长什么样"，不是"这段是废话"。
2. **归类只覆盖能确定的部分**：对白 / 长旁白 / 与上一段高度重复 / 未分类。
   "离题""与设定冲突"这类判断需要读全书语境，本脚本**不猜**，一律落 `unclassified`。
3. **零模型调用**：确定性对齐（difflib）+ 确定性线索（长度 / 引号 / 相似度）。
4. **不改任何产物**：只写 `data/review/three_state.{json,md}`。

用法：
    python scripts/three_state_diff.py                # 当前目录当项目根
    python scripts/three_state_diff.py --root <工作区>
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

LAYERS = ("raw", "checked", "refined")
OUT_DIR = Path("data") / "review"

#: 一句话里出现这些引号就算对白（中文小说只用这几种）
QUOTE_RE = re.compile(r"[「」“”『』\"]")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")


def split_paras(text: str) -> list[str]:
    """按空行切段，去掉标题行与首尾空白。段落是一切的单位（与编辑器一致）。"""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        s = block.strip()
        if not s or HEADING_RE.match(s) or s in ("---", "***"):
            continue
        out.append(re.sub(r"\s+", " ", s))
    return out


def _chapters_dirs(root: Path) -> Path:
    """章节根：正常项目在 `data/chapters/`；归档书档（`books/_trash/<书名>/`）直接 `chapters/`。"""
    if (root / "data" / "chapters").is_dir():
        return root / "data" / "chapters"
    return root / "chapters"


def load_chapters(root: Path) -> dict[int, dict[str, list[str]]]:
    """{章号: {层: [段落]}}；缺的层不给键（"没跑过"和"跑空了"不是一回事）。"""
    books: dict[int, dict[str, list[str]]] = {}
    base = _chapters_dirs(root)
    for layer in LAYERS:
        d = base / layer
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            m = re.match(r"^(\d+)", f.stem)
            if not m:
                continue
            n = int(m.group(1))
            books.setdefault(n, {})[layer] = split_paras(
                f.read_text(encoding="utf-8", errors="replace"))
    return books


def _clues(para: str, prev_kept: str | None) -> list[str]:
    """确定性线索（**不是判定**）：给作者一眼看出这段是什么类型。"""
    out = []
    if QUOTE_RE.search(para):
        out.append("dialogue")
    if len(para) >= 80 and "dialogue" not in out:
        # 80 个汉字以上的无对白段落 = 一块连续旁白。阈值只是给作者一个分桶，不是判据。
        out.append("long_narration")
    if prev_kept:
        ratio = difflib.SequenceMatcher(None, prev_kept, para).ratio()
        if ratio >= 0.8:
            out.append("near_duplicate")
    return out or ["unclassified"]


def diff_layers(prev: list[str], nxt: list[str]) -> dict:
    """prev → nxt：哪些段落没了、哪些是新加的（顺序敏感，用段落序列做 LCS）。"""
    sm = difflib.SequenceMatcher(None, prev, nxt, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            for i in range(i1, i2):
                removed.append({"index": i, "text": prev[i],
                                "clues": _clues(prev[i], prev[i - 1] if i else None)})
        if tag in ("insert", "replace"):
            added.extend(nxt[j1:j2])
    return {"removed": removed, "added": added}


def run(root: Path) -> dict:
    chapters = load_chapters(root)
    pairs = [("raw", "checked"), ("checked", "refined")]
    report = {"root": str(root), "chapters": [], "summary": {
        "chapters": 0, "pairs": {}, "removed_chars": {}, "by_clue": {}, "layer_chars": {}}}
    for n in sorted(chapters):
        ch = chapters[n]
        for layer, paras in ch.items():
            st0 = report["summary"]["layer_chars"]
            st0[layer] = st0.get(layer, 0) + sum(len(p) for p in paras)
        entry = {"chapter": n, "layers": {k: len(v) for k, v in ch.items()}, "pairs": []}
        for a, b in pairs:
            if a not in ch or b not in ch:
                continue
            d = diff_layers(ch[a], ch[b])
            d.update({"from": a, "to": b})
            entry["pairs"].append(d)
            key = "%s→%s" % (a, b)
            st = report["summary"]
            st["removed_chars"][key] = st["removed_chars"].get(key, 0) + sum(
                len(x["text"]) for x in d["removed"])
            for x in d["removed"]:
                for c in x["clues"]:
                    st["by_clue"][c] = st["by_clue"].get(c, 0) + 1
            st["pairs"][key] = st["pairs"].get(key, 0) + len(d["removed"])
        report["chapters"].append(entry)
        report["summary"]["chapters"] += 1
    return report


def render_md(rep: dict) -> str:
    s = rep["summary"]
    lines = ["# 三态对照（raw → checked → refined）", "",
             "> 零模型调用。**这是证据清单，不是判决**：删掉的段落原样列出 + 确定性线索。",
             "> 线索只到「对白 / 长旁白 / 与上一段高度重复 / 未分类」；"
             "离题、与设定冲突这类需要读全书的判断**本脚本不做**。", "",
             "## 汇总", "",
             "> 口径：「被删段落」按段落序列对齐算 —— 一个段落被改写也会同时记成"
             "「删旧 + 增新」，所以**被删字数 ≥ 净减少字数**。净变化看最后一列。", "",
             "| 步骤 | 被删段落数 | 被删字数 | 净变化 |", "|---|---|---|---|"]
    lc = s.get("layer_chars", {})
    for k in sorted(s["pairs"]):
        a, b = k.split("→")
        net = lc.get(b, 0) - lc.get(a, 0)
        lines.append("| %s | %d | %d | %+d |" % (k, s["pairs"][k],
                                                 s["removed_chars"].get(k, 0), net))
    if s["by_clue"]:
        lines += ["", "线索分布：" + " · ".join(
            "%s %d" % (k, v) for k, v in sorted(s["by_clue"].items(), key=lambda x: -x[1]))]
    for ch in rep["chapters"]:
        lines += ["", "## 第 %d 章（raw %d 段 / checked %d 段 / refined %d 段）" % (
            ch["chapter"], ch["layers"].get("raw", 0), ch["layers"].get("checked", 0),
            ch["layers"].get("refined", 0))]
        for p in ch["pairs"]:
            if not p["removed"]:
                continue
            lines += ["", "**%s → %s**：删 %d 段" % (p["from"], p["to"], len(p["removed"]))]
            for x in p["removed"]:
                text = x["text"] if len(x["text"]) <= 180 else x["text"][:180] + "…"
                lines.append("- `%s`（%d 字）%s" % ("/".join(x["clues"]), len(x["text"]), text))
    return "\n".join(lines) + "\n"


def _print_summary(rep: dict) -> None:
    s = rep["summary"]
    for k in sorted(s["pairs"]):
        print("  %-16s 删 %d 段 / %d 字" % (k, s["pairs"][k], s["removed_chars"].get(k, 0)))
    if s["by_clue"]:
        print("  线索：" + " · ".join("%s %d" % (k, v)
                                     for k, v in sorted(s["by_clue"].items(), key=lambda x: -x[1])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三态对照：哪一段在哪一步被删（零 token）")
    ap.add_argument("--root", default=".", help="项目根（默认当前目录）")
    ap.add_argument("--out", default=None,
                    help="报告输出目录（默认：项目根有 data/ 时写 <root>/data/review；"
                         "书档目录没有 data/ 时必须显式给 —— 不往用户的书档里塞文件）")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    rep = run(root)
    if not rep["chapters"]:
        print("[three_state] 没找到三态章节（{data/,}chapters/{raw,checked,refined}/NN.md）")
        return 1
    if args.out:
        out = Path(args.out).resolve()
    elif (root / "data").is_dir():
        out = root / OUT_DIR
    else:
        print("[three_state] %d 章已对齐，但这是书档目录（没有 data/）——"
              "不往里写文件。加 --out <目录> 指定报告位置。" % rep["summary"]["chapters"])
        _print_summary(rep)
        return 0
    out.mkdir(parents=True, exist_ok=True)
    (out / "three_state.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "three_state.md").write_text(render_md(rep), encoding="utf-8")
    print("[three_state] %d 章 → %s" % (rep["summary"]["chapters"], out / "three_state.md"))
    _print_summary(rep)
    print("  说明：只报证据，不改任何产物。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
