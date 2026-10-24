# -*- coding: utf-8 -*-
"""产物概况统计自检（`utils.stage_artifacts.summarize_artifacts`，零 LLM、零网络）。

用户 2026-10-24 第 4 条：「产物概况依旧是空的，每个阶段跑完看不到产出。」
根因是收件箱的产物概况原先只在前端按 stage 1/2/6 硬编码三个**文件**路径去数行，
而 stage 3/4/7 的产物是**目录**（章大纲目录 / raw 章节目录 / output），前端的
readPreview 只读单文件、数不了目录 → 这些阶段的产物概况永远空白。

本用例守 `summarize_artifacts`（后端单源）的计数语义：
1. 缺失路径 → exists=False、计数全 0（不抛异常）；
2. 文件 → kind=file、lines/chars 正确（空文件 0 行）；
3. 目录 → kind=dir、files=条目数、chars=各文本文件字符数之和；
4. 目录里的 .docx（二进制）只计 files、不读字符（不崩）；
5. 子目录不计入 files（只数文件）；
6. 未登记的 stage → 空列表（不抛 KeyError）。

全部在临时根上跑，真实工作区零触碰。
用法：python tests/unit/test_stage_artifacts_summary.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.stage_artifacts import summarize_artifacts  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def by_path(items, rel):
    for it in items:
        if it["path"] == rel:
            return it
    return None


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # stage 4 产物目录：3 个 md + 1 个 docx + 1 个子目录（子目录不计 files）
        raw = root / "data" / "chapters" / "raw"
        (raw / "sub").mkdir(parents=True)
        (raw / "01.md").write_text("# 第一章\n" + "正文" * 50, encoding="utf-8")
        (raw / "02.md").write_text("# 第二章\n" + "正文" * 30, encoding="utf-8")
        (raw / "03.md").write_text("", encoding="utf-8")   # 空文件：计 files 不计 chars
        (raw / "合集.docx").write_bytes(b"PK\x03\x04binary")  # 二进制：不读字符

        # stage 1 产物文件
        setting = root / "data" / "setting"
        setting.mkdir(parents=True)
        (setting / "setting.json").write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

        # --- stage 4：目录计数 ---
        s4 = summarize_artifacts(4, root)
        check("stage4 返回 1 项", len(s4) == 1, s4)
        d = by_path(s4, "data/chapters/raw")
        check("stage4 exists=True", d and d["exists"])
        check("stage4 kind=dir", d and d["kind"] == "dir")
        # 3 md + 1 docx = 4 个文件；子目录 sub 不计
        check("stage4 files=4（子目录不计）", d and d["files"] == 4, d)
        # chars = 01 + 02 + 空文件 0（docx 不读）
        want = len("# 第一章\n" + "正文" * 50) + len("# 第二章\n" + "正文" * 30)
        check("stage4 chars 只算文本文件", d and d["chars"] == want,
              (d and d["chars"], want))

        # --- stage 1：文件计数 ---
        s1 = summarize_artifacts(1, root)
        f = by_path(s1, "data/setting/setting.json")
        check("stage1 kind=file", f and f["kind"] == "file")
        # 行数语义 = count("\\n") + 1（末尾换行也算一行，与 readPreview 的
        # split("\\n").length 一致）→ "…\\n…\\n" 是 3 段。
        check("stage1 lines=3", f and f["lines"] == 3, f)
        check("stage1 chars>0", f and f["chars"] > 0)

        # --- 缺失路径 ---
        s2 = summarize_artifacts(2, root)     # data/outline/global.md 不存在
        g = by_path(s2, "data/outline/global.md")
        check("缺失 exists=False", g and not g["exists"])
        check("缺失计数全 0", g and g["lines"] == 0 and g["chars"] == 0 and g["files"] == 0)

        # --- 空文件 ---
        empty = root / "empty.md"
        empty.write_text("", encoding="utf-8")
        from utils.stage_artifacts import STAGE_ARTIFACTS  # noqa: E402
        orig = dict(STAGE_ARTIFACTS)
        try:
            STAGE_ARTIFACTS[99] = ["empty.md"]
            e = by_path(summarize_artifacts(99, root), "empty.md")
            check("空文件 lines=0 且 exists=True", e and e["exists"] and e["lines"] == 0, e)
        finally:
            STAGE_ARTIFACTS.clear()
            STAGE_ARTIFACTS.update(orig)

        # --- 未登记 stage：空列表不抛 ---
        try:
            out = summarize_artifacts(42, root)
            check("未登记 stage → 空列表", out == [], out)
        except Exception as exc:  # noqa: BLE001
            check("未登记 stage → 空列表", False, type(exc).__name__ + ": " + str(exc))

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
