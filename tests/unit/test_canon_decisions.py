# -*- coding: utf-8 -*-
"""决策文件（P1-4，2026-11-25）：只追加、每章 ≤5 条、缺文件不算错。

判据（施工任务书 §5）：
  第 6 章输入 ≤ 第 1 章 1.5 倍（要真跑）
  decisions.md 每章新增 ≤ 5 行

本用例判能离线判的那一半：格式、幂等、上限、未回收伏笔的提取、预算截断。

用法：python tests/unit/test_canon_decisions.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import canon                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="canon_"))
    p = tmp / "data" / "canon" / "decisions.md"
    try:
        print("\n【1】没有文件 = 还没定过事（不是错误）")
        check("read 返回空串", canon.read(p) == "")
        check("render_for_prompt 返回空串（任务书那一段直接空着）",
              canon.render_for_prompt(p) == "")
        check("open_foreshadows 返回空列表", canon.open_foreshadows(path=p) == [])

        print("\n【2】追加：只认“- [标签] 内容”")
        n = canon.append(1, """===DECISIONS===
- [语气] 岑砚说话前会停半拍，不用成语
这句没有标签，应该被丢掉
- [伏笔] 老太太的石头还没还 [未回收]
- [禁用] 不写内心独白式的解释段
""", path=p)
        check("写进去 3 条（无标签行丢弃）", n == 3, n)
        body = canon.read(p)
        check("文件里有章标题", "## 第 1 章" in body, body[:120])
        check("三条都在",
              all(x in body for x in ("[语气]", "[伏笔]", "[禁用]")))
        check("无标签的那句没被抄进去", "这句没有标签" not in body)

        print("\n【3】幂等：重跑同一章不重复追加")
        before = canon.read(p)
        n2 = canon.append(1, "- [语气] 岑砚说话前会停半拍，不用成语\n", path=p)
        check("重复条目返回 0", n2 == 0, n2)
        check("文件一个字节没变", canon.read(p) == before)

        print("\n【4】每章上限 5 条（超出的丢掉并出声）")
        many = "\n".join("- [设定] 第%d条设定" % i for i in range(1, 9))
        n3 = canon.append(2, many, path=p)
        check("只写 5 条", n3 == 5, n3)
        ch2 = canon.chapter_lines(2, path=p)
        check("第 2 章恰好 5 行", len(ch2) == 5, ch2)
        check("取到的是前 5 条", "第5条设定" in " ".join(ch2) and "第6条" not in " ".join(ch2))

        print("\n【5】未回收伏笔")
        opens = canon.open_foreshadows(path=p)
        check("只取带 [未回收] 的那条", opens == ["老太太的石头还没还"], opens)
        canon.append(3, "- [伏笔] 老太太的石头已经还了（回收）", path=p)
        check("回收后不再算未回收（新行没带标记）",
              canon.open_foreshadows(path=p) == ["老太太的石头还没还"])

        print("\n【6】接力块：决策 + 未回收伏笔")
        block = canon.render_for_prompt(path=p)
        check("块里有标题", "已定的决策" in block, block[:80])
        check("块里有未回收伏笔段", "还没回收的伏笔" in block and "老太太的石头还没还" in block)
        check("块里有第 1 章的条目", "停半拍" in block)

        print("\n【7】预算截断：写明省了多少，不静默少喂")
        long_block = canon.render_for_prompt(path=p, budget=80)
        check("超预算时带“省略”说明", "省略" in long_block, long_block[:160])
        check("截断后仍保留未回收伏笔", "老太太的石头还没还" in long_block)

        print("\n【8】chapter_lines 只取那一章")
        ch1 = canon.chapter_lines(1, path=p)
        check("第 1 章 3 行", len(ch1) == 3, ch1)
        check("不串到第 2 章", all("第2条设定" not in x for x in ch1))

        print("\n【9】收件箱并库（模型交在 data/canon/_inbox/NN.md）")
        inbox = tmp / "data" / "canon" / "_inbox"
        p2 = tmp / "data" / "canon" / "decision2.md"
        check("收件箱没有文件 → 并入 0 条（不是错）",
              canon.harvest(7, path=p2, inbox_dir=inbox) == 0)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "07.md").write_text(
            "- [语气] 矢车不说“我觉得”\n- [伏笔] 那块砖还没还 [未回收]\n",
            encoding="utf-8")
        check("并入 2 条", canon.harvest(7, path=p2, inbox_dir=inbox) == 2)
        check("第二次并库是幂等的（重跑同一章不会写两遍）",
              canon.harvest(7, path=p2, inbox_dir=inbox) == 0)
        body2 = canon.read(p2)
        check("决策文件里确实有那两条",
              "矢车不说" in body2 and "那块砖还没还" in body2, body2[:160])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
