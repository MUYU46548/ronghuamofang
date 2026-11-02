# -*- coding: utf-8 -*-
"""段身份（2026-11-02，一期 F12 的地基）。

## 要治的病

一期评审点名的**单一失败原因**：没有段身份 → 没有段级重算 → “一处”永远不是单位。
系统只知道“第 3 章变了”，不知道“第 5 段变了”。

## 判据（每条都能变红）

- S1 **id 不随位置变**（前面插一段，后面所有 id 不变）—— 这就是 F12“位置稳定”
- S2 内容改了 id 就变
- S3 同一段出现两次 → 同一个 id，按**多重集**计数（不是去重）
- S4 diff 的 kept/added/removed/moved 各自对
- S5 没有快照 → 明确说“没有”，**不编 0**
- S6 快照落盘能读回、原子写
- S7 “一个字都没动”说得出来
- S8 只改空白不算改动（换行/空格不该让 id 变）
- S9 stage6 真的接了段级对照

用法：python tests/unit/test_segments.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import segments as seg                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("段身份")
    print("=" * 66)

    body = "## 第3章 试\n\n甲推开门。\n\n乙没有回头。\n\n甲关上门。"
    a1 = seg.anchors(body)
    check("S0 段落数与切分一致", len(a1) == 4, [s["preview"] for s in a1])
    check("S0 id 是 12 位", all(len(s["id"]) == 12 for s in a1), a1[0]["id"])

    # ---- S1 位置不变性（F12 的核心）----
    inserted = body.replace("甲推开门。", "门前有雪。\n\n甲推开门。")
    a2 = seg.anchors(inserted)
    old_ids = [s["id"] for s in a1]
    new_ids = [s["id"] for s in a2]
    check("S1 插一段后，原有段落 id 全在", all(i in new_ids for i in old_ids[1:]),
          (old_ids, new_ids))
    check("S1 新插的那段是新 id", len(a2) == 5 and a2[1]["id"] not in old_ids)
    check("S1 只是位置后移（index 变了）", a2[2]["id"] == a1[1]["id"])

    # ---- S2 内容改了 id 变 ----
    edited = body.replace("乙没有回头。", "乙回过头来。")
    check("S2 改内容 → id 变",
          seg.seg_id("乙没有回头。") != seg.seg_id("乙回过头来。"))

    # ---- S3 重复段落按多重集 ----
    dup = "甲。\n\n甲。\n\n乙。"
    ad = seg.anchors(dup)
    check("S3 重复段共用同一 id", ad[0]["id"] == ad[1]["id"], [s["id"] for s in ad])
    d_dup = seg.diff_ids(seg.anchors("甲。\n\n乙。"), ad)
    check("S3 多出一个重复段算 added=1", d_dup["added"] == 1, d_dup)
    check("S3 kept 按次数算（2）", d_dup["kept"] == 2, d_dup)

    # ---- S4 diff 各口径 ----
    d = seg.diff_ids(a1, a2)
    check("S4 插入：added=1 removed=0", (d["added"], d["removed"]) == (1, 0), d)
    check("S4 插入：moved 记下几段挪了位", d["moved"] >= 1, d)
    d2 = seg.diff_ids(a1, seg.anchors(edited))
    check("S4 改一段：added=1 removed=1", (d2["added"], d2["removed"]) == (1, 1), d2)

    # ---- S8 只改空白不算改动 ----
    spaced = body.replace("甲推开门。", "甲 推开门。")
    check("S8 空格不影响 id", seg.seg_id("甲推开门。") == seg.seg_id("甲 推开门。"))
    check("S8 换行不影响 id", seg.seg_id("甲推开门。") == seg.seg_id("甲推\n开门。"))
    check("S8 标点变了 id 就该变（标点是内容）",
          seg.seg_id("甲推开门。") != seg.seg_id("甲推开门"))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # ---- S5 没快照 ----
        r = seg.compare(9, body, root=root)
        check("S5 没快照 → has_snapshot=False", r.get("has_snapshot") is False, r)
        check("S5 没快照 → 不编数字", "kept" not in r, r)

        # ---- S6 快照落盘/读回 ----
        data = seg.snapshot(3, body, root=root, note="测试")
        p = seg.anchor_path(3, root)
        check("S6 快照文件写出", p.exists(), p)
        back = seg.load_snapshot(3, root=root)
        check("S6 读回来一致", back and back["count"] == 4 and back["segments"] == data["segments"],
              back and back["count"])
        check("S6 落在 _anchors 目录", p.parent.name == "_anchors", p.parent)

        # ---- S7 对照说话 ----
        same = seg.compare(3, body, root=root)
        check("S7 没改 → 一个字都没动", same.get("summary") == "一个字都没动", same.get("summary"))
        ins = seg.compare(3, inserted, root=root)
        check("S7 插一段 → 说“新增 1 段”",
              (ins.get("summary") or "").startswith("新增 1 段"), ins.get("summary"))
        chg = seg.compare(3, edited, root=root)
        check("S7 改一段 → 说“改写 1 段”（不是“改动 2 段”）",
              (chg.get("summary") or "").startswith("改写 1 段"), chg.get("summary"))
        check("S7 带上快照时间", bool(chg.get("snapshot_at")), chg.get("snapshot_at"))

    # ---- S9 接线 ----
    s6 = (ROOT / "scripts" / "stage6_polish.py").read_text(encoding="utf-8")
    check("S9 stage6 做了段级对照", "seg.diff_ids" in s6 and "seg.snapshot" in s6)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
