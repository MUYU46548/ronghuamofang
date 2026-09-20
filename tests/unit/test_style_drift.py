# -*- coding: utf-8 -*-
"""风格偏差分析自测：覆盖正常漂移 / 匹配 / 空范文 / 短文本四种情形。

运行：python tests/test_style_drift.py（workdir=项目根）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from utils.style_analyzer import (  # noqa: E402
    extract_style_features, compute_style_drift, format_drift_report, MIN_TEXT_LEN,
)

# 范文：短句 + 高对话 + 比喻密集
REF = """
他推开门。屋里的灯没亮。
“回来了？”她问，声音很轻。
“嗯。”他放下包，像一只疲惫的鸟收起翅膀。
“吃饭了吗？”
“没有。”
她笑了笑，笑容像水面的光，一晃就散了。
他忽然觉得心里空了一块，冷风从那里灌进来，怎么也堵不上。
“我先去躺一会儿。”
“好。”她说，“汤在锅里。”
他没说话，只是点了点头。夜色像一只手，慢慢合拢。
""" * 3

# 润色稿：长句 + 几乎无对话，明显漂移
OUT = """
他推开那扇沉重的木门，屋内的灯因为没有打开而显得格外昏暗，空气里浮动着一种说不清的、陈旧的寂静气息。
她转过身来，目光在他脸上停留了片刻，随后用一种很轻的声音问了一句是否已经回来了。
他把肩上的包缓缓放下，动作迟缓得仿佛一只飞了很久的鸟终于收拢翅膀。
""" * 3


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return cond


def _integration():
    """验证 stage6 侧：范文特征加载 + 偏差小节追加进已有 polish_report。"""
    import os
    import tempfile
    import stage6_polish as s6

    old_cwd = os.getcwd()
    tmp = Path(tempfile.mkdtemp())
    try:
        os.chdir(tmp)
        ref_path = tmp / "ref.md"
        ref_path.write_text(REF, encoding="utf-8")
        feats = s6._load_reference_features(ref_path)
        if not feats:
            print("FAIL 范文特征加载")
            return False
        # 缓存命中：第二次同一路径应取到同一对象
        if s6._load_reference_features(ref_path) is not feats:
            print("FAIL 范文特征缓存")
            return False
        if s6._load_reference_features("") != {}:
            print("FAIL 空 style_reference 应返回 {}")
            return False

        report = s6._report_path(0)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# 润色报告\n\n第1章 已润色。\n", encoding="utf-8")
        sec = s6._drift_section(3, feats, OUT)
        n = s6._append_drift_report(report, [sec, ""])
        body = report.read_text(encoding="utf-8")
        ok = (n == 1 and body.startswith("# 润色报告") and "## 风格偏差报告（第3章）" in body
              and list(report.parent.glob("polish_report*")) == [report])
        print(("PASS " if ok else "FAIL ") + "报告追加（不新建文件）")
        if not ok:
            print(body)
        return ok
    finally:
        os.chdir(old_cwd)


def main():
    ok = True
    ref_f = extract_style_features(REF)
    out_f = extract_style_features(OUT)
    res = compute_style_drift(ref_f, out_f)
    print("\n--- 漂移报告 ---")
    print(format_drift_report(res, title="第3章"))
    ok &= check("有漂移项", res["drift_count"] > 0)
    ok &= check("summary 含'漂移'", "漂移" in res["summary"])
    ok &= check("drift_pct 保留2位小数",
                all(round(d["drift_pct"], 2) == d["drift_pct"] for d in res["drifts"]))

    # 自比（相同文本）→ 应匹配良好
    same = compute_style_drift(ref_f, ref_f)
    print("--- 自比报告 ---")
    print(format_drift_report(same, title="第4章"))
    ok &= check("自比无漂移", same["drift_count"] == 0 and "风格匹配良好" in same["summary"])

    # 空范文 → 跳过，不报错
    empty = compute_style_drift({}, out_f)
    ok &= check("空范文跳过", empty["drifts"] == [] and empty.get("skipped") and
                format_drift_report(empty, title="第5章") == "")

    # 集成：stage6 追加写入（临时 cwd，验证"追加到现有 report、不新建文件"）
    ok &= _integration()
    # 短文本 → extract_style_features 返回 {}，上层按跳过处理
    ok &= check("短文本返回空特征", extract_style_features("太短了") == {})
    ok &= check("MIN_TEXT_LEN=100", MIN_TEXT_LEN == 100)

    print("\nALL_OK" if ok else "\nHAS_FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
