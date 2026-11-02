# -*- coding: utf-8 -*-
"""成品过期标记（2026-11-02，不可用清单第 9 条）。

## 要治的病

「改完成品悄悄过期」：用户改了正文，`output/*.docx` 不会告诉你它已经旧了。
全书只有判定报告有 `stale` 标记，成品侧一声不响 —— 于是「我改完了」和
「导出的是三天前的」可以同时成立，界面上没有任何东西反对。

## 判据（每条都能变红）

- S1 源稿比成品新       → `stale=True`，并点名哪几章
- S2 源稿比成品旧       → `stale=False`（不许狼来了）
- S3 **优先级**：raw/03 比成品新、但成品实际用的是更旧的 refined/03 →
       **不得**报过期（同 merge_book 的 refined > checked > raw 逐章取）
- S4 Markdown 目录按**卷文件** mtime 判，不按目录 mtime
- S5 `data/merged/book.md` 更新但没重新导出 → 也算过期
- S6 没有 output/ → 空列表，不抛异常

用法：python tests/unit/test_export_stale.py
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import nf_api as api                                             # noqa: E402
from nf_api_domains import post_misc as pm                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _touch(p, mtime):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def _mkroot(tmp):
    root = Path(tmp)
    (root / "output").mkdir(parents=True, exist_ok=True)
    return root


def main():
    print("=" * 66)
    print("成品过期标记")
    print("=" * 66)
    old_root = api.ROOT
    now = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = _mkroot(tmp)
            api.ROOT = root

            docx = root / "output" / "某书_完整版.docx"
            _touch(docx, now - 1000)                       # 成品：1000 秒前
            # 采用稿：02 比成品新，01 比成品旧
            _touch(root / "data/chapters/refined/01.md", now - 5000)
            _touch(root / "data/chapters/refined/02.md", now - 100)

            items = pm._export_history_items()
            word = [i for i in items if i["kind"] == "word"]
            check("S1 Word 成品出现在列表里", len(word) == 1, items)
            it = word[0] if word else {}
            check("S1 源稿更新 → 标过期", it.get("stale") is True, it)
            check("S1 点名改过的章", it.get("stale_chapters") == ["02.md"],
                  it.get("stale_chapters"))
            check("S1 有一句人话", "最新稿" in (it.get("stale_note") or ""),
                  it.get("stale_note"))

            # ---- S3 优先级：raw/01 新，但成品用的是 refined/01（旧） ----
            _touch(root / "data/chapters/raw/01.md", now - 50)
            items = pm._export_history_items()
            it = [i for i in items if i["kind"] == "word"][0]
            check("S3 低优先级稿更新不算过期（refined 赢）",
                  it.get("stale_chapters") == ["02.md"], it.get("stale_chapters"))

            # ---- S2 全部源稿都比成品旧 ----
            _touch(docx, now)                              # 成品变成最新
            items = pm._export_history_items()
            it = [i for i in items if i["kind"] == "word"][0]
            check("S2 源稿都更旧 → 不报过期", it.get("stale") is False, it)

            # ---- S5 合并稿更新但没重新导出 ----
            _touch(root / "data/merged/book.md", now + 10)
            items = pm._export_history_items()
            it = [i for i in items if i["kind"] == "word"][0]
            check("S5 merged/book.md 更新 → 报过期", it.get("stale") is True, it)
            check("S5 说明是合并稿变了",
                  "合并稿" in (it.get("stale_note") or ""), it.get("stale_note"))

            # ---- S4 Markdown 分卷按文件 mtime ----
            (root / "data/merged/book.md").unlink()
            md_dir = root / "output" / "某书_markdown"
            md_dir.mkdir()
            os.utime(md_dir, (now - 3000, now - 3000))     # 目录很旧
            _touch(md_dir / "第一卷.md", now - 2000)        # 卷文件也旧
            items = pm._export_history_items()
            md = [i for i in items if i["kind"] == "markdown"]
            check("S4 Markdown 目录被识别", len(md) == 1, items)
            check("S4 按卷文件时间（不是目录时间）", md and md[0]["mtime"] == now - 2000,
                  md[0]["mtime"] if md else None)
            check("S4 卷文件比源稿旧 → 报过期", md and md[0].get("stale") is True, md)

            # ---- S6 没有 output/ ----
            api.ROOT = Path(tmp) / "empty"
            check("S6 没有 output/ → 空列表", pm._export_history_items() == [])
    finally:
        api.ROOT = old_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
