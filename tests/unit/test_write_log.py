# -*- coding: utf-8 -*-
"""写作账本（P2-2 的“一任务一记录”）判据 —— 零 token、临时工作区。

锁四件：
  A 追加 + 按章过滤 + 新→旧
  B 超上限只留最新 MAX_ENTRIES 条（它是线索不是归档）
  C 坏行/半行跳过，不连坐整份账本
  D 段落保存真的会记账（段落级唯一写盘点，一处覆盖手改/采用/回退）

用法：python tests/unit/test_write_log.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import write_log  # noqa: E402
import refine_paragraph as rp   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  → " + str(detail)) if detail and not cond else ""))


def main() -> int:
    print("== A. 追加 / 过滤 / 顺序 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_log.record(1, 0, "手改", 10, 12, root=root)
        write_log.record(2, 1, "AI 段落改写（采用）", 30, 40, root=root)
        write_log.record(1, 2, "回退", 50, 20, root=root)
        allrows = write_log.recent(root=root)
        check("A1 三条都在，且是新的在前", len(allrows) == 3
              and allrows[0]["paragraph"] == 2, allrows)
        ch1 = write_log.recent(chapter=1, root=root)
        check("A2 按章过滤只留那一章", [e["paragraph"] for e in ch1] == [2, 0],
              [e["paragraph"] for e in ch1])
        check("A3 长度前后都记了（不存正文）",
              ch1[0]["before_chars"] == 50 and ch1[0]["after_chars"] == 20
              and "text" not in ch1[0], ch1[0])

    print("\n== B. 上限：只留最新的 MAX_ENTRIES 条 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        n = write_log.MAX_ENTRIES + 5
        for i in range(n):
            write_log.record(1, i, "手改", i, i + 1, root=root)
        lines = (root / "data" / "state" / "write_log.jsonl").read_text(
            encoding="utf-8").splitlines()
        check("B1 文件不超过上限", len(lines) == write_log.MAX_ENTRIES, len(lines))
        last = json.loads(lines[-1])
        check("B2 留下的是最新的那些（最后一条是最后写的）",
              last["paragraph"] == n - 1, last)

    print("\n== C. 坏行不连坐 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "data" / "state" / "write_log.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text('{"chapter": 1, "paragraph": 0, "note": "手改", "before_chars": 1, '
                     '"after_chars": 2}\n{半行坏掉\n', encoding="utf-8")
        rows = write_log.recent(root=root)
        check("C1 坏行跳过，好行照读", len(rows) == 1 and rows[0]["note"] == "手改", rows)
        check("C2 没有账本 = 空列表（不抛）",
              write_log.recent(root=Path(td) / "nope") == [])

    print("\n== D. 段落保存真的记账 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.parent.mkdir(parents=True)
        chap.write_text("## 第1章\n\n他推门进来，屋里没人。\n", encoding="utf-8")
        ok, msg = rp.save_paragraph_text(1, 1, "他推开门，屋里空着。", note="手改", root=root)
        rows = write_log.recent(chapter=1, root=root)
        check("D1 保存成功", ok, msg)
        check("D2 账本里有一条，来源写对了",
              len(rows) == 1 and rows[0]["note"] == "手改"
              and rows[0]["paragraph"] == 1, rows)
        check("D3 记的是字数变化（不是正文）",
              rows[0]["after_chars"] == len("他推开门，屋里空着。") and "text" not in rows[0],
              rows[0])

        # 内容没变 → 不写盘，也不该多记一条（否则账本全是噪声）
        rp.save_paragraph_text(1, 1, "他推开门，屋里空着。", note="手改", root=root)
        check("D4 没写盘的那次不记账", len(write_log.recent(chapter=1, root=root)) == 1,
              write_log.recent(chapter=1, root=root))

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
