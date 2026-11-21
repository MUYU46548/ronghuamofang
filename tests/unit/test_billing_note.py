# -*- coding: utf-8 -*-
"""成本口径说明（F-4，2026-11-03）。

## 要治的病

`engine: hermes` 下 `cost_yuan` 恒 0、输入输出比 43:1 —— 界面上只有一个
“¥0.00”，用户要么以为记账坏了，要么以为成本可忽略。两件事都必须**当众说清**：

1. 恒 0 = 订阅流量没有按量单价，**不是免费**；止烧只看 token 闸门；
2. in/out 比值大是正常现象（每次请求重发设定/大纲/前文），**不是产出效率**。

## 判据

- C1 hermes → money_metered=false，note 里出现“订阅流量 / 不是免费”
- C2 direct → money_metered=true，note 说“按量计价/刊例价”
- C3 io_ratio 有值且 = in/out；out=0 时不给比例（不除零）
- C4 io_note 解释这不是产出效率
- C5 端点接线（/costs/summary 里算 billing）
- C6 界面在 hermes 下不再只显示 ¥0，而是“订阅流量”并给出说明

用法：python tests/unit/test_billing_note.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    print("=" * 66)
    print("成本口径说明（F-4）")
    print("=" * 66)

    misc = (ROOT / "scripts" / "nf_api_domains" / "misc.py").read_text(encoding="utf-8")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    mock = (ROOT / "tests" / "e2e" / "mock_nf_api_state.py").read_text(encoding="utf-8")

    print("\n[C1/C2] 两种引擎的说明文本")
    # 2026-11-17：订阅口径从 hermes 一个引擎扩成 hermes + dsh，所以这里不再钉死
    # `engine=hermes：` 这一串字面量（否则加引擎时只能改测试把断言改松）。
    # 2026-11-20：说明文字整段重写 —— 界面上不再出现 `engine=xxx` / `provider`
    # 这类参数名，所以断言改成钉“同一句人话”，并加一条反向闸：不许把参数名写回去。
    check("订阅分支写明“走订阅 / 不是免费”",
          "这笔用量走订阅" in misc and "不是免费" in misc, None)
    check("订阅分支同时覆盖 hermes 与 dsh（不是只想着 hermes）",
          'engine in ("hermes", "dsh")' in misc, None)
    check("按量分支写明“官方价目表”",
          "费用按官方价目表估算" in misc)
    check("说明不以 pill 标签开头（否则界面重复成“按量计价 按量计价：”）",
          '"订阅流量：这笔用量' not in misc and '"按量计价：费用' not in misc, None)
    check("说明文字里不再出现 engine=direct / engine=%s / provider 刊例价",
          "engine=direct" not in misc and "engine=%s" not in misc
          and "provider 刊例价" not in misc, None)
    check("money_metered 由引擎决定（订阅口径即不计金额）",
          '"money_metered": engine not in ("hermes", "dsh")' in misc)

    print("\n[C3] in/out 比例")
    check("有 out 才给比例（不除零）",
          'round(t_in / t_out, 1) if t_out else None' in misc)
    check("比例字段名为 io_ratio", '"io_ratio"' in misc)

    print("\n[C4] 比例的解释")
    check("说明重发上下文导致的输入膨胀",
          "每次请求都要重发设定" in misc, None)
    check("明说不是产出效率", "不是产出效率" in misc)

    print("\n[C5] 端点接线")
    check("/costs/summary 里组装 billing", 'totals["billing"] = {' in misc)

    print("\n[C6] 界面不再只显示 ¥0")
    check("hermes 下金额位显示“订阅流量”", '"订阅流量" : fmtYuan' in app)
    check("有计价口径说明块", "billing-note" in app and "billing.note" in app)
    check("显示入/出比值", "io_ratio" in app and "入/出" in app)
    check("mock 也带 billing（视觉验收能验到）", '"billing"' in mock)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
