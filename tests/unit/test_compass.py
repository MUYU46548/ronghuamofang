# -*- coding: utf-8 -*-
"""指南针（compass）判据自检 —— roadmap 7.3。

为什么值得写：指南针是全书终局的唯一锚点，一旦写歪，后面每章都以它为基准对齐。
判据钉死的是"拒绝写歪"和"变更留痕"两件事，外加注入侧不编造。

用法：python tests/unit/test_compass.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → %r" % (detail,)) if detail else ""))


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from utils import compass as C

    tmp = Path(tempfile.mkdtemp(prefix="nf_compass_"))
    try:
        print("\n[P1] 拒绝写歪（校验不过不落盘）")
        ok, probs, warns = C.check({"ending_direction": "太短"}, total_chapters=None)
        check("P1a 方向过短 → 不通过", not ok and any("过短" in p for p in probs), probs)
        ok, probs, warns = C.check({"ending_direction": "主角最终继承老师遗志重新封印核心并公开真相"},
                                   total_chapters=None)
        check("P1b 合法方向 → 通过", ok, probs)

        six = [{"text": "线%d" % i} for i in range(6)]
        ok, probs, _ = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                "active_threads": six}, total_chapters=None)
        check("P1c 长线 6 条（>5）→ 拒绝并点名", not ok and any("超过上限" in p for p in probs), probs)
        five = [{"text": "线%d" % i} for i in range(5)]
        ok, probs, _ = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                "active_threads": five}, total_chapters=None)
        check("P1d 长线 5 条 → 通过", ok, probs)
        ok, probs, _ = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                "active_threads": [{"text": ""}]}, total_chapters=None)
        check("P1e 空长线 → 拒绝", not ok and any("缺 text" in p for p in probs), probs)

        ok, probs, _ = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                "scale_estimate": "abc"}, total_chapters=None)
        check("P1f 规模非整数 → 拒绝", not ok and any("整数" in p for p in probs), probs)
        ok, probs, warns = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                    "scale_estimate": 300}, total_chapters=6)
        check("P1g 规模偏离>30% → 告警不阻断", ok and any("偏离" in w for w in warns), (ok, warns))
        ok, probs, warns = C.check({"ending_direction": "主角最终继承老师遗志封印核心公开真相",
                                    "scale_estimate": 6}, total_chapters=6)
        check("P1h 规模吻合 → 无告警", ok and not warns, warns)

        print("\n[P2] 变更留痕（终局方向改动自动存 history）")
        # 用独立干净数据集 —— P1 构造过的脏数据（6条线/空text）若被复用会污染落盘文件，
        # 让 P3 的注入断言因残留字段而假红。
        good = {"ending_direction": "主角最终继承老师遗志封印核心并公开真相",
                "active_threads": [{"text": "沈砚与程砧的信任裂痕"}],
                "scale_estimate": 10}
        ok, msg, warns = C.save(good, root=tmp)
        check("P2a 首次保存成功", ok, msg)
        check("P2b compass.json 落盘", (tmp / "data/outline/compass.json").exists())
        c2 = dict(good)
        c2["ending_direction"] = "主角放弃封印，选择让核心的力量流散到世间各处去"
        ok, msg, warns = C.save(c2, root=tmp)
        hist = list((tmp / "data/outline/history").glob("compass_v*.json"))
        check("P2c 改方向 → 存 history", ok and len(hist) == 1, [h.name for h in hist])
        old = json.loads(hist[0].read_text(encoding="utf-8"))
        check("P2d history 存的是旧方向", "继承老师遗志" in old["ending_direction"], old)
        check("P2e 告警提示可回溯", any("history" in w for w in warns), warns)

        print("\n[P3] 注入（有据注入、没据明说、不编造）")
        b = C.render_context(n=1, total=10, cfg={}, proj={}, root=tmp)
        check("P3a 注入块含终局方向", "终局方向" in b and "流散到世间" in b, b[:120])
        check("P3b 注入块含活跃长线", "活跃长线" in b and "信任裂痕" in b, b[:200])
        check("P3c 注入块含规模", "规模估计" in b, b[:240])

        # 跨卷未更新 → 提示
        c3 = json.loads((tmp / "data/outline/compass.json").read_text(encoding="utf-8"))
        c3["volume_at_update"] = 1
        (tmp / "data/outline/compass.json").write_text(
            json.dumps(c3, ensure_ascii=False), encoding="utf-8")
        b2 = C.render_context(n=45, total=100, cfg={"chapter": {"volume_size": 20}},
                              proj={"book": {"chapters": 100}}, root=tmp)
        check("P3d 跨卷未更新 → 提示更新", "指南针未更新" in b2, b2[-160:])

        # 没设 → 空块 + missing_note 明说
        tmp2 = Path(tempfile.mkdtemp(prefix="nf_compass_empty_"))
        b3 = C.render_context(n=1, total=10, cfg={}, proj={}, root=tmp2)
        check("P3e 没设 → 注入块为空（不编造）", b3 == "", repr(b3))
        note = C.missing_note(root=tmp2)
        check("P3f missing_note 明说未设", "未设" in note and "compass.json" in note, note[:80])
        check("P3g 设了指南针 → missing_note 为空",
              C.missing_note(root=tmp) == "")
        shutil.rmtree(tmp2, ignore_errors=True)

        print("\n[P4] 接线（volume_arc 注入链含指南针）")
        from utils import volume_arc as VA
        ctx = VA.render_context(n=1, total=10, cfg={}, proj={"book": {"chapters": 10}}, root=tmp)
        check("P4a render_context 含指南针块", "指南针" in ctx and "流散到世间" in ctx, ctx[-160:])

    finally:
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
