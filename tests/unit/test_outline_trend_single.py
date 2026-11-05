# -*- coding: utf-8 -*-
"""趋势/建议：只有一个版本时也要说得出话（试跑第 18 条，2026-11-05）。

## 要治的病

`outline_review.convergence_verdict` 在版本 < 2 时返回 `insufficient`，界面把
“历史版本不足（至少需要 2 个版本才能判断趋势）”原样显示 —— 用户读到的就是
“这个功能没有”。但**单版体检本身就是结论**：多少条 OK / WARN / THIN、
平均分、结构问题、下一步能干什么，全都能确定性算出来，零 token。

## 修法

- 数据层：`/outline/trend` 在 `insufficient` 时附带 `summary`（单版体检）
  + `issues`（结构问题）+ `thin_items` + `next_step`；
- 界面：结论位置先摆“本次体检 PASS/WARN/FAIL”，趋势说明降级为附注，
  并给两个动作 —— “改这一版（提意见）”与“再跑体检”。
- `outline_advisor.advise()` 本来就返回 metrics + options，这里一并纳入判据
  （避免下一轮又从零实现一遍）。

## 判据

- C1 单版：convergence_verdict = insufficient，但 summary 有数
- C2 advisor 在单版时仍给 metrics 与至少一个 options
- C3 /outline/trend 在 insufficient 时带 summary / issues / next_step
- C4 界面不再把“样本不足”当主结论；有附注与两个动作按钮
反证：界面仍只显示一句“不足 2 版” = 未完成。

用法：python tests/unit/test_outline_trend_single.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []

OUTLINE = """# 示例书

## 关键节点

- 节点1：第1章 — 岑砚在盐埕码头发现一只没有署名的木箱，与矢车在雨里对峙，最后把箱子沉进河里。
- 节点2：第3章 — 祁雉在渡口客栈质问岑砚凭什么替人保管，冲突升级，两人约定三次会面。

## 章节规划

- 第1章：盐埕码头，岑砚接第一单私活，发现木箱里的语音石与矢车的失踪有关。
- 第2章：渡口客栈，祁雉登场，追问担保与契约，冲突落在“谁有资格替人保管”。
- 第3章：河岸夜谈，矢车说出失声前的最后一句话，悬念落在她为什么不肯写下。
"""

SETTING = {
    "characters": [{"name": "岑砚"}, {"name": "矢车"}, {"name": "祁雉"}],
    "world": {"locations": [{"name": "盐埕码头"}, {"name": "渡口客栈"}]},
}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    print("=" * 68)
    print("趋势/建议：单版本降级输出（试跑第 18 条）")
    print("=" * 68)

    import outline_advisor as oa
    import outline_review as ov

    tmp = Path(tempfile.mkdtemp(prefix="nf_trend_"))
    try:
        (tmp / "outline").mkdir()
        (tmp / "history").mkdir()
        g = tmp / "outline" / "global.md"
        g.write_text(OUTLINE, encoding="utf-8")
        s = tmp / "setting.json"
        s.write_text(json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")

        print("\n[C1] 单版：趋势不足，但体检有结论")
        series = ov.review_series(str(g), str(s), history_dir=str(tmp / "history"))
        check("只有一个版本", len(series) == 1, len(series))
        status, note = ov.convergence_verdict(series)
        check("判为 insufficient", status == "insufficient", status)
        check("原有说明仍保留（“至少需要 2 个版本”）", "2 个版本" in note)
        rev = ov.review(str(g), str(s))
        summ = rev["summary"]
        check("单版体检给出条目数", (summ.get("total") or 0) >= 2, summ)
        check("单版体检给出结论", summ.get("verdict") in ("PASS", "WARN", "FAIL"), summ)

        print("\n[C2] advisor 在单版时也给下一步")
        adv = oa.advise(global_path=str(g), setting_path=str(s),
                        history_dir=str(tmp / "history"))
        check("verdict 仍是 insufficient", adv.get("verdict") == "insufficient")
        m = adv.get("metrics") or {}
        check("metrics 有平均分", m.get("avg_score") is not None, m.get("avg_score"))
        check("metrics 有条目统计", (m.get("total") or 0) >= 2)
        check("至少一个可选方案", len(adv.get("options") or []) >= 1,
              [o.get("id") for o in adv.get("options") or []])

        print("\n[C3] /outline/trend 在 insufficient 时附带单版摘要")
        src = (ROOT / "scripts" / "nf_api_domains" / "outline.py").read_text(encoding="utf-8")
        check("附带 summary", 'out["summary"] = {' in src)
        check("附带 issues", 'out["issues"]' in src)
        check("附带 thin_items", 'out["thin_items"]' in src)
        check("附带 next_step", 'out["next_step"]' in src)
        check("失败不把主结论打成 500", 'out["summary_error"]' in src)

        print("\n[C4] 界面：结论在前、趋势降级为附注")
        vue = (ROOT / "console" / "src" / "OutlineView.vue").read_text(encoding="utf-8")
        css = (ROOT / "console" / "src" / "style.css").read_text(encoding="utf-8")
        check("识别单版状态", "singleOnly" in vue and "insufficient" in vue)
        check("主结论写“本次体检”", "本次体检" in vue)
        check("附注写“≥2 个版本”", "≥2 个版本" in vue)
        check("有“改这一版”动作", "改这一版（提意见）" in vue)
        check("有“再跑体检”动作", "再跑体检" in vue)
        check("附注有样式", ".ov-adv-foot" in css)
        app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        check("父组件接住 open-refine", '@open-refine="refineOpen = true"' in app)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 68)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
