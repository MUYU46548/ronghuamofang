# -*- coding: utf-8 -*-
"""人工改动率：AI 交稿之后，人到底改了多少（零 token、确定性）。

## 为什么要有它

作者问的是“写作质量什么时候能提升到把人工重写率从 80% 降到 20%”。
在那之前必须先能**量**这个 80% —— 否则任何“提升了”都是感觉。

## 口径（唯一实现，别处不要另写一份）

数据来源只有一处：`data/chapters/history/chNN_vM.md`。这是
`/chapters/save`（人手动保存）与 `refine_chapter` 共用的备份链，
**每保存一版递增一个 v**。所以：

    v1            = 人第一次看到的那份（流水线产出 / 首次导入）
    vN（最大）    = 人最后一次保存的版本
    v1 → vN       = 人的净改动

三个数各自的意思：

1. **char_displaced**（改动率，主指标）
   = `1 - 共同字数 / 基线字数`。共同部分用 `difflib.SequenceMatcher` 的
   matching blocks 算，**只数汉字**（标点/空白不计 —— 否则一次标点归一化
   就会被算成“改了 30%”，与作者体感不符）。
2. **sentences_rewritten**（句子改写率）
   按句子切，基线里没有原样保留的句子占比。比字数严格：整句被改写
   即使字数一样也会被算出来。
3. **chapters_full_rewrite**（整章重写率）
   改动率 ≥ 0.9 判为“整章重写”。这是作者最痛的那种，单独一档。

## 纪律

- **只报数，不评好坏**。改动多不一定是 AI 差（也可能作者在精修），
  改动少也不一定是 AI 好（也可能作者没审）。这里只给证据。
- **样本不足要明说**：一章都没手改过 → `no_data`，不返回 0%
  （0% 会被读成“AI 写得完美”，那是彻头彻尾的误导）。
- **零 token、断网可跑、不改任何产物**：只读 history 与当前稿。

用法：
    python scripts/human_edit_rate.py                  # 当前目录当项目根
    python scripts/human_edit_rate.py --root <工作区>
    python scripts/human_edit_rate.py --json           # 只要机器可读结果
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

#: 汉字（CJK 统一表意文字 + 扩展 A）。**只数汉字**：标点、数字、拉丁字母都不算。
#: 为什么：`text_norm` 会把人没动过的半角标点改成全角，按“字符”算会把这类
#: 自动归一化算成人的改动，改动率凭空涨一截。
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: 句子切分：中日韩句末标点 + **半角**句末标点 + 换行。
#: 半角 `.` `!` `?` 必须一起认：`text_norm` 归一化之前，稿子里用的是半角标点；
#: 只认全角时，一整段半角稿会被切成"一句话"，句子改写率于是报 100%
#: （2026-11-28 实测踩过：同一段文字只把逗号句号换成半角，改写率从 0 跳到 1.0）。
#: 小数点 `3.5` 会因此被误切，但这里只用来比"句子是否原样保留"，
#: 误切在两侧对称发生，不影响结论；比起把整段算成一句，这个代价可以接受。
SENT_RE = re.compile(r"[^。！？…!?.\n]+[。！？…!?.]*")

#: 改动率超过这个数就当“整章重写”（≈ 九成以上字都换了）
FULL_REWRITE_AT = 0.9

HISTORY_DIR = Path("data") / "chapters" / "history"
CHAP_RE = re.compile(r"^ch(\d+)_v(\d+)\.md$")


def han_only(text: str) -> str:
    """只留汉字，其余全部丢掉。"""
    return "".join(HAN_RE.findall(text or ""))


def split_sentences(text: str) -> list[str]:
    """切句（只保留含汉字的句子；空句丢掉）。

    ⚠️ 顺序不能反：**先按原文切句，再对每一句取汉字**。
    2026-11-28 实测踩过：先 `han_only()` 再切句的话，标点已经被删光，
    整段会被当成一句话 —— 半角稿与全角稿于是永远"完全不同句"，
    句子改写率恒报 100%（看起来像"人把每句都重写了"，其实是切句没生效）。
    """
    out = []
    for m in SENT_RE.finditer(text or ""):
        s = han_only(m.group(0))
        if s:
            out.append(s)
    return out


def compare(base: str, final: str) -> dict:
    """比一份基线稿与最终稿。返回三个指标 + 证据。"""
    b_han, f_han = han_only(base), han_only(final)
    n_base, n_final = len(b_han), len(f_han)

    # ---- 1) 改动率：共同字数 / 基线字数 ----
    # autojunk=False：中文语料里高频字（的/了/是）占比很高，默认的 junk 启发式
    # 会把它们当噪声剔掉，导致共同部分被低估、改动率被高估。
    sm = difflib.SequenceMatcher(None, b_han, f_han, autojunk=False)
    kept = sum(blk.size for blk in sm.get_matching_blocks())

    # 分母取**两者较大值**，不是只取基线字数。
    # 2026-11-28 实测踩过：人只往里加了一段（v1 的每个字都还在），
    # `kept / n_base` = 1 → 改动率 0% —— 可作者明明添了半页字。
    # 用 max() 后，"只增不改"会如实体现为改动率 > 0（新增占多少）。
    denom = max(n_base, n_final)
    char_displaced = (1 - kept / denom) if denom else None

    # 新增/删减分开报：两个方向都是人的工作，但性质不同，
    # 合成一个数会让人以为"改动率 40%"全是删改。
    added = max(0, n_final - kept)
    removed = max(0, n_base - kept)

    # ---- 2) 句子改写率：基线句子里有几成被原样保留 ----
    b_sents = split_sentences(base)
    kept_sents = 0
    if b_sents:
        final_set = set(split_sentences(final))
        kept_sents = sum(1 for s in b_sents if s in final_set)
        sent_rewritten = 1 - kept_sents / len(b_sents)
    else:
        sent_rewritten = None

    return {
        "chars_base": n_base,
        "chars_final": n_final,
        "chars_kept": kept,
        "chars_added": added,
        "chars_removed": removed,
        "char_displaced": (round(char_displaced, 4)
                           if char_displaced is not None else None),
        "sentences_base": len(b_sents),
        "sentences_kept": kept_sents,
        "sentences_rewritten": (round(sent_rewritten, 4)
                                if sent_rewritten is not None else None),
        "is_full_rewrite": (char_displaced is not None
                            and char_displaced >= FULL_REWRITE_AT),
    }


def collect(root: Path) -> dict:
    """扫 history 目录，逐章算出人改了多少。"""
    hist = root / HISTORY_DIR
    if not hist.is_dir():
        return {
            "ok": True, "status": "no_data",
            "chapters": [], "summary": {},
            "note": ("还没有历史版本（%s 不存在）。这个目录在人手动保存正文时才会出现 —— "
                     "也就是说，还没人改过任何一章，无从计算改动率。"
                     % HISTORY_DIR.as_posix()),
        }

    # 按章聚合：{章号: [(版号, 路径)]}
    per_ch: dict[int, list[tuple[int, Path]]] = {}
    for p in hist.iterdir():
        m = CHAP_RE.match(p.name)
        if not m:
            continue
        per_ch.setdefault(int(m.group(1)), []).append((int(m.group(2)), p))

    chapters, skipped = [], []
    for ch in sorted(per_ch):
        versions = sorted(per_ch[ch])
        if len(versions) < 2:
            # 只有一版 = 人存过一次但从没改过，或者那是 AI 的备份。
            # **不当作 0% 改动** —— 一版看不出"改了什么"。
            skipped.append({"chapter": ch, "why": "只有一个历史版本，无法比较",
                            "versions": len(versions)})
            continue
        v_first, p_first = versions[0]
        v_last, p_last = versions[-1]
        try:
            base = p_first.read_text(encoding="utf-8")
            final = p_last.read_text(encoding="utf-8")
        except OSError as e:
            skipped.append({"chapter": ch, "why": "读取失败：%s" % e})
            continue
        row = compare(base, final)
        row.update({"chapter": ch, "base_version": v_first,
                    "final_version": v_last, "versions": len(versions)})
        chapters.append(row)

    if not chapters:
        return {
            "ok": True, "status": "no_data", "chapters": [], "summary": {},
            "skipped": skipped,
            "note": ("有 %d 章的历史目录，但没有一章存过两个以上版本 —— "
                     "看不出人改了什么。至少改过一次并保存，才有数。"
                     % len(skipped)),
        }

    rates = [c["char_displaced"] for c in chapters if c["char_displaced"] is not None]
    sents = [c["sentences_rewritten"] for c in chapters
             if c["sentences_rewritten"] is not None]
    full = sum(1 for c in chapters if c["is_full_rewrite"])
    summary = {
        "chapters": len(chapters),
        "char_displaced_avg": round(sum(rates) / len(rates), 4) if rates else None,
        "char_displaced_median": (sorted(rates)[len(rates) // 2] if rates else None),
        "sentences_rewritten_avg": (round(sum(sents) / len(sents), 4) if sents else None),
        "chapters_full_rewrite": full,
        "chapters_full_rewrite_pct": (round(full / len(chapters), 4) if chapters else None),
    }
    return {"ok": True, "status": "ok", "chapters": chapters,
            "summary": summary, "skipped": skipped, "note": ""}


def render_text(rep: dict) -> str:
    if rep["status"] != "ok":
        return "人工改动率：没有可用的数据。\n" + rep["note"]

    s = rep["summary"]
    lines = [
        "人工改动率（AI 交稿 → 人最后保存，只数汉字）",
        "=" * 46,
        "可比章数: %d" % s["chapters"],
        "平均改动率: %.1f%%   中位数: %.1f%%"
        % (s["char_displaced_avg"] * 100, s["char_displaced_median"] * 100),
        "句子改写率: %.1f%%（基线句子里被整句换掉的占比）"
        % (s["sentences_rewritten_avg"] * 100
           if s["sentences_rewritten_avg"] is not None else 0),
        "整章重写: %d 章（%.0f%%）"
        % (s["chapters_full_rewrite"], s["chapters_full_rewrite_pct"] * 100),
        "",
        "逐章（改动率 / 句子改写率 / 版本数）:",
    ]
    for c in rep["chapters"]:
        lines.append("  第 %2d 章  %5.1f%%  %5.1f%%  v%d→v%d（共 %d 版）%s"
                     % (c["chapter"],
                        (c["char_displaced"] or 0) * 100,
                        (c["sentences_rewritten"] or 0) * 100,
                        c["base_version"], c["final_version"], c["versions"],
                        "  ← 整章重写" if c["is_full_rewrite"] else ""))
    if rep["skipped"]:
        lines.append("")
        lines.append("跳过 %d 章：" % len(rep["skipped"]))
        for k in rep["skipped"][:8]:
            lines.append("  第 %s 章 —— %s" % (k.get("chapter", "?"), k["why"]))
    lines += [
        "",
        "口径：只数汉字（标点/数字不计）；改动率 = 1 - 共同字数 / max(基线, 最终)；",
        "      （分母取较大值 —— 只往里加字也算改动，不会报 0%）",
        "      句子改写 = 基线句子未被原样保留的占比；改动率 ≥90% 记整章重写。",
        "      只报数，不评好坏 —— 改动多可能是 AI 差，也可能是你在精修。",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="人工改动率（零 token）")
    ap.add_argument("--root", default=".", help="项目根（默认当前目录）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    rep = collect(root)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
