# -*- coding: utf-8 -*-
"""残稿可见可采纳 + 伏笔台账（2026-10-20）。零 token，全部确定性判据。

跑法：python tests/unit/test_truncated_foreshadow.py

## 为什么这些断言值得写

- 残稿：采纳是**唯一能把隔离文本送回正式产物路径**的口子。判据一旦松，
  「采纳残稿」就变成绕过审稿的后门（半章正文冒充完成章）。
- 伏笔台账：自动化最容易做假的地方 —— 用文本相似去"自动判定已回收"，
  台账会很好看，但全是谎话。所以这里钉住「回收只能人工标」「标了必须给理由」。
"""
import io
import json
import os
import shutil
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


# ⚠️ 必须分段：单段长文会被 verify_chapter 的退化判据（段数 <6 / 单段 >50%）打掉，
# 那是判据正确，不是夹具该绕过的。残稿采纳测试要验的是"标记"，不是段落判据。
_PARA = ("他推开门的时候，屋檐上的水线正连成了串。院子里没有人，只有一条老狗趴在墙根。"
         "灯芯爆了一下，屋里暗了半格。他把湿透的袖口往腕上又卷了一道。")
LONG = "\n\n".join([_PARA, _PARA, _PARA, _PARA, _PARA, _PARA, _PARA])


def main():
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp(prefix="nf_tf_"))
    cwd = os.getcwd()
    try:
        from utils import truncated_store as TS
        from utils import foreshadow as FS
        from utils import verify_chapter as V

        # 让模块指向临时工作区（正常由端点用 api.ROOT 覆盖）
        TS.TRUNC_DIR = tmp / "data" / "state" / "truncated"
        TS.RAW_DIR = tmp / "data" / "chapters" / "raw"
        TS.TRUNC_DIR.mkdir(parents=True)
        (tmp / "data" / "chapters" / "raw").mkdir(parents=True)

        print("\n[P1] 残稿：清单与可采纳判定")
        (TS.TRUNC_DIR / "stage4_ch03_20261020_120000.txt").write_text(LONG, encoding="utf-8")
        (TS.TRUNC_DIR / "stage4_ch04_20261020_130000.txt").write_text("刚刚开始就断了", encoding="utf-8")
        items = TS.list_items()
        check("P1 列出两份残稿", len(items) == 2, [i["name"] for i in items])
        by_ch = {i["chapter_hint"]: i for i in items}
        check("P1 从任务名认出章号", 3 in by_ch and 4 in by_ch, list(by_ch))
        check("P1 长残稿可采纳", by_ch[3]["adoptable"] is True, by_ch[3]["chars"])
        check("P1 短残稿不可采纳", by_ch[4]["adoptable"] is False, by_ch[4]["chars"])
        check("P1 带预览与字数", by_ch[3]["preview"] and by_ch[3]["chars"] > 300,
              (by_ch[3]["preview"][:20], by_ch[3]["chars"]))
        check("P1 未采纳时 adopted_as 为空", by_ch[3]["adopted_as"] is None)

        print("\n[P2] 残稿：采纳的拒绝条件都明说原因")
        ok, msg = TS.adopt("stage4_ch04_20261020_130000.txt", 4)
        check("P2 太短 → 拒绝并说明字数", not ok and "太短" in msg, msg)
        ok, msg = TS.adopt("不存在的.txt", 3)
        check("P2 不存在 → 拒绝", not ok and "不存在" in msg, msg)
        ok, msg = TS.adopt("../escape.txt", 3)
        check("P2 越界名 → 拒绝（不允许 ../）", not ok, msg)
        ok, msg = TS.adopt("stage4_ch03_20261020_120000.txt", 0)
        check("P2 章号越界 → 拒绝", not ok and "1-999" in msg, msg)

        print("\n[P3] 残稿：采纳 = 写进 raw + 打标记，但**仍判未完成**")
        ok, msg = TS.adopt("stage4_ch03_20261020_120000.txt", 3)
        check("P3 采纳成功", ok, msg)
        raw = TS.RAW_DIR / "03.md"
        check("P3 内容已写入 raw/03.md", raw.exists() and len(raw.read_text(encoding="utf-8")) > 300)
        check("P3 标记已落盘", TS.is_adopted(3) is True)
        check("P3 清单回显「已采纳为第 N 章」",
              any(i["adopted_as"] == 3 for i in TS.list_items()))
        check("P3 ⚠️ 采纳后仍判**未完成**（不能变成绕过审稿的后门）",
              V.is_chapter_complete(raw, target_min=100, target_max=99999) is False)

        print("\n[P4] 残稿：覆盖保护与「已补齐」")
        ok, msg = TS.adopt("stage4_ch03_20261020_120000.txt", 3)
        check("P4 已有内容 → 拒绝覆盖并说明", not ok and "覆盖" in msg, msg)
        ok, msg = TS.adopt("stage4_ch03_20261020_120000.txt", 3, replace=True)
        check("P4 显式 replace → 允许", ok, msg)
        check("P4 clear 之前标记仍在", TS.is_adopted(3) is True)
        check("P4 clear 成功", TS.clear_mark(3) is True)
        check("P4 clear 后标记消失", TS.is_adopted(3) is False)
        check("P4 补齐后恢复正常判定（此文本字数/结构达标）",
              V.is_chapter_complete(raw, target_min=100, target_max=99999) is True)
        check("P4 重复 clear 返回 False（不假装成功）", TS.clear_mark(3) is False)
        check("P4 标记文件损坏不抛异常",
              (TS.TRUNC_DIR / "adopted.json").write_text("{坏的", encoding="utf-8")
              or TS.adopted_marks() == {})

        print("\n[P5] 伏笔台账：没有大纲就明说，不倒假数据")
        FS.OUTLINE_DIR = tmp / "data" / "outline" / "chapters"
        FS.STATUS_PATH = tmp / "data" / "state" / "foreshadow_status.json"
        data = FS.collect()
        check("P5 无大纲 → 空列表", data["items"] == [])
        check("P5 并且说明原因", any("没有章大纲" in n for n in data["notes"]), data["notes"])
        check("P5 计数全 0", data["summary"]["total"] == 0, data["summary"])

        print("\n[P6] 伏笔台账：从「悬念钩子」抽取 + 推进判定")
        FS.OUTLINE_DIR.mkdir(parents=True)
        def w(no, hook, event):
            (FS.OUTLINE_DIR / ("%02d.md" % no)).write_text(
                "## 第%d章 标题\n- 核心事件：%s\n- 涉及角色：苏芷\n"
                "- 小目标：拿到那份旧记录\n- 悬念钩子：%s\n- 功能：推进\n"
                % (no, event, hook), encoding="utf-8")

        w(1, "借阅卡背面的签名是她父亲的名字", "她在档案室翻到一张借阅卡")
        w(2, "旧码头那盏灯是谁点的", "她去旧码头查看灯的位置")
        w(3, "借阅卡背面的签名是她父亲的名字", "她把借阅卡上的签名拿去比对旧档案里的笔迹")
        w(4, "旧码头那盏灯是谁点的", "她在雨里站了一夜，什么也没等到")
        data = FS.collect(latest_chapter=4)
        check("P6 四章各一条钩子 → 4 条", data["summary"]["total"] == 4, data["summary"])
        check("P6 id 形如 ch01-1", data["items"][0]["id"] == "ch01-1", data["items"][0]["id"])
        ids = [i["id"] for i in data["items"]]
        check("P6 id 不重复", len(ids) == len(set(ids)), ids)
        ch1 = [i for i in data["items"] if i["id"] == "ch01-1"][0]
        check("P6 第 3 章提到同一件事 → 记为推进", 3 in ch1["advanced"], ch1["advanced"])
        check("P6 推进后的 last = 最后推进章", ch1["last"] == 3, ch1["last"])
        check("P6 状态由推进推导为 advanced", ch1["status"] == "advanced", ch1["status"])
        ch2 = [i for i in data["items"] if i["id"] == "ch02-1"][0]
        check("P6 完全无关的章不算推进", ch2["advanced"] == [], ch2["advanced"])

        print("\n[P7] 伏笔台账：超期告警")
        d2 = FS.collect(latest_chapter=40, stale_after=30)
        check("P7 闲置 39 章 → 标 stale", all(r["stale"] for r in d2["items"]), d2["items"])
        check("P7 stale 计数进汇总", d2["summary"]["stale"] == 4, d2["summary"])
        check("P7 并给出一条提示", any("超期" in n or "没推进" in n for n in d2["notes"]))
        d3 = FS.collect(latest_chapter=5, stale_after=30)
        check("P7 闲置未超阈值 → 不告警", d3["summary"]["stale"] == 0, d3["summary"])

        print("\n[P8] 伏笔台账：回收只能人工标，且必须给理由")
        ok, msg = FS.set_status("ch01-1", "closed")
        check("P8 不给理由 → 拒绝", not ok and "理由" in msg, msg)
        ok, msg = FS.set_status("ch01-1", "closed", "第 3 章已确认签名是父亲的")
        check("P8 给了理由 → 成功", ok, msg)
        d4 = FS.collect(latest_chapter=40, stale_after=30)
        c1 = [i for i in d4["items"] if i["id"] == "ch01-1"][0]
        check("P8 状态变 closed", c1["status"] == "closed", c1["status"])
        check("P8 source=human（压过自动判定）", c1["source"] == "human", c1["source"])
        check("P8 已回收的不再算超期", c1["stale"] is False, c1)
        check("P8 回收计数进汇总", d4["summary"]["closed"] == 1, d4["summary"])
        ok, msg = FS.set_status("ch99-9", "closed", "x")
        check("P8 不存在的 id → 拒绝", not ok and "没有这条" in msg, msg)
        ok, msg = FS.set_status("ch01-1", "done", "x")
        check("P8 非法状态 → 拒绝", not ok and "closed" in msg, msg)
        ok, _ = FS.set_status("ch01-1", "open")
        check("P8 可以打回 open", ok)

        print("\n[P9] 人读版")
        md = FS.render_markdown(FS.collect(latest_chapter=40, stale_after=30))
        check("P9 有标题与计数", "# 伏笔台账" in md and "共 4 条" in md, md[:80])
        check("P9 逐条列出 id 与章号", "`ch01-1`" in md and "第 1 章" in md, md[:200])
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("   x " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
