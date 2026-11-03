# -*- coding: utf-8 -*-
"""章范围：先写三章看看（2026-11-02，不可用清单第 1 条）。

## 要治的病

阶段 4 一旦开始就把**所有**未完成章一口气写完：只有阶段粒度，没有章粒度。
于是"先写三章看看"做不到 —— 而后面所有"停下来看看"都以它为前提。

## 判据（每条都能变红）

- P1 `1-3` → {1,2,3}
- P2 `1,3,5` → {1,3,5}
- P3 `1-3,7` → {1,2,3,7}
- P4 空 / None → None（不限制）
- P5 非法写法必须**抛错并说清**（`abc` / `3-1` / `0`）——静默忽略等于
     让用户以为限了范围、实际整本跑完，这正是要治的病
- P6 中文逗号也认（手打的是中文标点）
- P7 三个入口（stage4 / orchestrator / nf_api 动作）签名里真的带 chapters

用法：python tests/unit/test_chapter_scope.py
"""
import io
import inspect
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from stage4_writing import parse_chapter_scope, _apply_scope        # noqa: E402
import stage4_writing                                             # noqa: E402
import orchestrator                                               # noqa: E402
import nf_api                                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("章范围")
    print("=" * 66)

    check("P1 1-3 → 1,2,3", parse_chapter_scope("1-3") == {1, 2, 3},
          parse_chapter_scope("1-3"))
    check("P2 1,3,5 → 1,3,5", parse_chapter_scope("1,3,5") == {1, 3, 5},
          parse_chapter_scope("1,3,5"))
    check("P3 1-3,7 混写", parse_chapter_scope("1-3,7") == {1, 2, 3, 7},
          parse_chapter_scope("1-3,7"))
    check("P4 空 → None", parse_chapter_scope("") is None)
    check("P4 None → None", parse_chapter_scope(None) is None)
    check("P6 中文逗号也认", parse_chapter_scope("1，3") == {1, 3},
          parse_chapter_scope("1，3"))

    for bad, why in (("abc", "非数字"), ("3-1", "起点大于终点"), ("0", "从 1 开始"),
                     ("1-", "缺终点")):
        try:
            parse_chapter_scope(bad)
            check("P5 %r 必须报错（%s）" % (bad, why), False, "被静默接受了")
        except ValueError as e:
            check("P5 %r 报错且说清楚（%s）" % (bad, why), bool(str(e)), str(e))
        except Exception as e:                             # noqa: BLE001
            check("P5 %r 报 ValueError（%s）" % (bad, why), False, repr(e))

    # ---- P7 三个入口的接线 ----
    check("P7 stage4.run_stage 带 chapters",
          "chapters" in inspect.signature(stage4_writing.run_stage).parameters)
    check("P7 orchestrator.run 带 chapters",
          "chapters" in inspect.signature(orchestrator.run).parameters)
    check("P7 nf_api 非流式动作带 chapters",
          "chapters" in inspect.signature(nf_api.act_run_stage).parameters)
    check("P7 nf_api 流式动作带 chapters",
          "chapters" in inspect.signature(nf_api.act_run_stage_streamed).parameters)

    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("P7 API 端点把 chapters 传下去", "chapters=_scope" in src)
    check("P7 非法章范围回 400 而不是忽略", '"章范围写错了' in src)

    # ---- P9 自动重试也必须守住章范围（2026-11-03 真机踩坑）----
    # 现象：`只写 1-3` 的 run 写完 1-3 之后，一进自动重试就退化成整本跑，
    # 又去写第 4、5 章（因为重试那行调 run_stage 时没带 chapters）。
    orch_src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    _stage4_calls = [ln for ln in orch_src.splitlines()
                     if "STAGES[n].run_stage(" in ln or "_extra)" in ln]
    check("P9 自动重试与首次调用都带章范围（两个调用点都过 _extra）",
          orch_src.count("**_extra)") >= 2, orch_src.count("**_extra)"))
    check("P9 重试那行不再裸调 run_stage",
          "run_stage(cfg, proj, progress, db, cost, client=client, run_id=run_id)" not in orch_src)

    # ---- P10 按范围完成 = 部分完成（2026-11-03 作者拍板）----
    from stage4_writing import _scope_completion
    check("P10 范围 1-3 全写完、全书 5 章只差 4/5 → 部分完成（外面缺 4,5）",
          _scope_completion({1, 2, 3}, {1, 2, 3}, 5) == ([4, 5], []),
          _scope_completion({1, 2, 3}, {1, 2, 3}, 5))
    check("P10 整本都写完 → 无缺（可以标 done）",
          _scope_completion({1, 2, 3}, {1, 2, 3, 4, 5}, 5) == ([], []))
    check("P10 范围内还有没写完的 → left_in 非空（走失败/中断语义，不判部分完成）",
          _scope_completion({1, 2, 3}, {1}, 5) == ([4, 5], [2, 3]),
          _scope_completion({1, 2, 3}, {1}, 5))
    check("P10 不限范围（None）→ 两项都空，语义与从前一致",
          _scope_completion(None, {1}, 5) == ([], []) and
          _scope_completion(set(), {1}, 5) == ([], []))
    check("P10 范围超出本书章数不影响判定",
          _scope_completion({1, 2, 9}, {1, 2}, 3) == ([3], []))

    # 接线：不许把"按范围完成"写成 done，也不许被当成失败去重试
    s4_src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("P11 stage4 把按范围完成落成 partial（不是 done）",
          'progress.set_stage(4, "partial"' in s4_src)
    check("P11 stage4 用的是盘上事实判完成（is_chapter_complete），不是只看标记",
          "is_chapter_complete(_p, *_tw_scope" in s4_src)
    check("P11 orchestrator 见到 partial 就不重试、不进下游",
          'progress.stage_status(n) == "partial"' in orch_src
          and '_finalize("waiting_review", 3)' in orch_src)
    check("P11 /state 把 chapters_left 交给界面",
          'out["chapters_left"] = st["chapters_left"]' in
          (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8"))
    check("P11 界面把 partial 显示成【按范围完成】",
          'partial: "按范围完成"' in
          (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8"))

    # ---- P8~P10 行为：真的缩了范围（不是只解析了字符串）----
    want, out = _apply_scope([1, 2, 3, 4, 5, 6], {1, 2, 3})
    check("P8 只写 1-3 时 todo 缩到 3 章", want == [1, 2, 3], want)
    check("P8 没有多余的跳过项", out == [], out)
    want, out = _apply_scope([2, 4], {1, 2})
    check("P8 已完成/越界的章被报出来", (want, out) == ([2], [1]), (want, out))
    want, out = _apply_scope([1, 2], None)
    check("P8 不填范围 → 原样全写", (want, out) == ([1, 2], []), (want, out))
    want, out = _apply_scope([1, 2], {9})
    check("P8 范围内一章都不用写 → 空列表（调用方据此直接返回）", want == [], want)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
