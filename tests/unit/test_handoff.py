# -*- coding: utf-8 -*-
"""handoff 交接稿的自检（2026-10-20，v0.10「可带走」）。

## 主判据

**内容完整性（会失败的反证）**
1. 下一章大纲的六个字段（核心事件/涉及角色/功能/小目标/悬念钩子/衔接）逐个进交接稿
2. 大纲里删掉一个字段 → 交接稿如实缺（不假装有）；字段清洗不吞内容
3. 下一章大纲缺失 → 交接稿写明「尚未生成 + 怎么补」，且 problems 非空
4. 未回收伏笔（open/advanced）进交接稿；已回收（closed）不占篇幅
5. 最近 3 章摘要进交接稿；滚动摘要缺失 → 明说，不编造
6. 上一章末尾原文段落进交接稿（语气衔接基准）
7. 写作要求（字数区间/style/style_notes）来自配置，逐条在稿

**可携带性**
8. 产物不含本机绝对路径（判据钉住 `E:\\` 与 `/Users/`）
9. 老产物首行残留 `===FILE: E:\\...===` 协议标记 → 被剥掉，不泄路径
10. 落盘路径 = `data/handoff/第N章后.md`；重复导出幂等覆盖

**开局交接稿（还没写过任何章）**
11. chapter=0：没有「最近摘要」段也不炸，身份/要求/设定照给

用法：python tests/unit/test_handoff.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("" if detail == "" else "  -> %r" % (detail,)))


OUTLINE_04 = """# 第四章 雨夜对峙

- 核心事件：沈砚在雨夜撞见内鬼销毁账册
- 涉及角色：沈砚、内鬼（账房先生）
- 小目标：拿到能指认幕后人的账册残页
- 悬念钩子：账册残页的笔迹与老师的手稿极像
- 功能：把内线冲突推向明处
- 衔接：承接第三章的库房搜查，引出第五章的朝堂对质
"""

PARA = ("他推开门的时候，屋檐上的水线正连成了串。院子里没有人，只有一条老狗趴在墙根。"
        "灯芯爆了一下，屋里暗了半格。他把湿透的袖口往腕上又卷了一道。")
RAW_03 = "## 第三章 库房\n\n" + "\n\n".join([PARA] * 4)


def main():
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp(prefix="nf_handoff_"))
    try:
        # ---- 铺夹具工作区
        (tmp / "data/outline/chapters").mkdir(parents=True)
        (tmp / "data/chapters/raw").mkdir(parents=True)
        (tmp / "data/summaries").mkdir(parents=True)
        (tmp / "data/setting").mkdir(parents=True)
        (tmp / "config").mkdir(parents=True)
        (tmp / "data/outline/chapters/04.md").write_text(OUTLINE_04, encoding="utf-8")
        (tmp / "data/chapters/raw/03.md").write_text(RAW_03, encoding="utf-8")
        (tmp / "data/chapters/raw/01.md").write_text(RAW_03, encoding="utf-8")
        (tmp / "data/chapters/raw/02.md").write_text(RAW_03, encoding="utf-8")
        (tmp / "data/summaries/rolling.md").write_text(
            "# 滚动摘要\n## 全书摘要\n沈砚追查雾港核心的旧案。\n\n## 近期章节\n"
            "### 第1章\n沈砚接到程砧的委托。\n\n### 第2章\n两人进入雾港遗址。\n\n"
            "### 第3章\n库房里发现封印遗迹的线索。\n", encoding="utf-8")
        (tmp / "data/setting/setting.json").write_text(
            '{"characters": [{"name": "沈砚", "role": "机关术师"}], "world": {},'
            ' "plot_fragments": [], "timeline": []}', encoding="utf-8")
        (tmp / "config/project.yaml").write_text(
            "book:\n  name: \"测试书\"\n  genre: \"悬疑\"\n  chapters: 6\n"
            "  style: \"冷峻克制\"\n  style_notes: \"\\u591a\\u7528\\u77ed\\u53e5\"\n"
            "  form: \"narrative\"\n", encoding="utf-8")
        (tmp / "config/system.yaml").write_text(
            "chapter:\n  target_words: [3000, 5000]\n", encoding="utf-8")

        from utils import handoff as HO
        from utils import foreshadow as FS

        print("\n[P1] 内容完整性")
        data = HO.build(root=tmp)
        text = data["text"]
        check("P1 默认取已写最大章号（第3章后）", data["chapter"] == 3, data["chapter"])
        check("P1 下一章指向第 4 章", data["next_chapter"] == 4, data["next_chapter"])
        for f in ("核心事件", "涉及角色", "功能", "小目标", "悬念钩子", "衔接"):
            check("P1 大纲字段「%s」进交接稿" % f, f in text)
        check("P1 钩子的值逐字在稿（不是只出现字段名）",
              "笔迹与老师的手稿极像" in text)
        check("P1 problems 为空（数据齐全）", data["problems"] == [], data["problems"])

        print("\n[P2] 写作要求来自配置")
        check("P2 字数区间来自 system.yaml", "3000 ~ 5000" in text)
        check("P2 文风来自 project.yaml", "冷峻克制" in text)
        check("P2 风格笔记进稿", "多用短句" in text)

        print("\n[P3] 设定摘要 + 伏笔 + 最近章节")
        check("P3 设定集角色进稿", "沈砚" in text and "机关术师" in text)
        check("P3 最近 3 章摘要进稿", "库房里发现封印遗迹" in text)
        check("P3 上一章末尾原文进稿", "屋檐上的水线正连成了串" in text)

        # 造一条伏笔：04 章的钩子是「悬念钩子」字段 → planted=4？不 —— collect 从
        # **所有**章大纲抽钩子；夹具只有 04 有钩子 → 1 条 open。
        fs = FS.collect(outline_dir=str(tmp / "data/outline/chapters"))
        check("P3 夹具抽出 1 条伏笔", len(fs["items"]) == 1, fs["items"])
        check("P3 未回收伏笔进交接稿", "账册残页的笔迹" in text)

        print("\n[P4] 缺料明说，不编造")
        (tmp / "data/outline/chapters/04.md").unlink()
        data2 = HO.build(root=tmp)
        check("P4 大纲缺失 → problems 点名第 4 章",
              any("第 4 章" in p and "大纲" in p for p in data2["problems"]), data2["problems"])
        check("P4 正文写明尚未生成 + 怎么补",
              "还没生成" in data2["text"] and "阶段 3" in data2["text"])
        (tmp / "data/outline/chapters/04.md").write_text(OUTLINE_04, encoding="utf-8")

        print("\n[P5] 可携带性：绝对路径与协议标记")
        check("P5 正常稿无本机绝对路径", HO._ABS_RE.search(data["text"]) is None,
              HO._ABS_RE.search(data["text"]))
        # 老产物污染：raw/03 首行残留协议标记（含私路径）→ 剥掉
        (tmp / "data/chapters/raw/03.md").write_text(
            "===FILE: D:\\\\Books\\\\SomeAuthor\\\\data\\\\chapters\\\\raw\\\\03.md===\n"
            + RAW_03, encoding="utf-8")
        data3 = HO.build(root=tmp)
        check("P5 协议标记被剥掉", "===FILE" not in data3["text"])
        check("P5 私路径没进稿", "D:\\Books" not in data3["text"])
        check("P5 但正文还在", "屋檐上的水线" in data3["text"])

        print("\n[P6] 落盘与幂等")
        d = HO.export(root=tmp)
        p = Path(d["path"])
        check("P6 落盘路径 = data/handoff/第3章后.md",
              p.as_posix().endswith("data/handoff/第3章后.md"), p.as_posix())
        check("P6 文件真实存在且非空", p.exists() and p.stat().st_size > 500)
        first = p.read_text(encoding="utf-8")
        HO.export(root=tmp)
        check("P6 重复导出幂等（内容一致）",
              p.read_text(encoding="utf-8") == first)

        print("\n[P7] 开局交接稿（还没写过任何章）")
        for n in (1, 2, 3):
            (tmp / ("data/chapters/raw/%02d.md" % n)).unlink()
        data0 = HO.build(chapter=0, root=tmp)
        check("P7 chapter=0 不炸", data0["chapter"] == 0 and data0["next_chapter"] == 1)
        check("P7 身份照给", "《测试书》" in data0["text"])
        check("P7 没有前文可述时明说（不编造）", "开局交接稿" in data0["text"])

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
