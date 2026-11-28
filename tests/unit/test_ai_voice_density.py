# -*- coding: utf-8 -*-
"""篇章密度判据（2026-11-28）。

## 为什么要有它

`ai_voice` 原有五类特征全在**词句层**（万能修饰词、对称排比、模板套话、段末金句）。
用作者的真实改稿样本（视频月报 v1→v2）实测，最强的两个差异信号**一个都没被覆盖**：

    冒号（每千字）  AI 稿 9.5  →  作者稿 0.0   （正文 11 个 → 0 个，全删）
    阿拉伯数字     AI 稿 41 处 →  作者稿 11 处 （删 73%）

这两条不是"某句话像 AI"，而是"整篇的说话习惯"：冒号密度高 = 通篇在
"先抛结论再解释"，是汇报腔；数字密度高 = 用数据撑场面（提交数、代码行数）。

## 判据两条（都必须过）

1. **能分开**：同一份真实样本，AI 稿命中、作者稿不命中。
   只验"AI 稿命中"是不够的 —— 一个恒返回 hit 的实现也能过，
   所以必须同时验作者稿干净。
2. **不误报附录**：附录表格里的冒号是格式需要，传正文才判。
   （本轮实测踩过：算全文时作者稿也有 13 个冒号，一半来自附件表格。）

用法：python tests/unit/test_ai_voice_density.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import ai_voice as av  # noqa: E402

PASS, FAIL = [], []

SAMPLE = Path("E:/视频制作/项目/十一月月报_20261128")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def body_only(t: str) -> str:
    i = t.find("# 附一")
    return t[:i] if i > 0 else t


def main():
    print("=" * 66)
    print("篇章密度判据（冒号 / 数字）")
    print("=" * 66)

    # ---------------------------------------------------------------- 合成样本
    print("\n【1】合成样本：能分开“汇报腔”与“叙述”")

    # 汇报腔：冒号密、数字多（模拟 AI 稿的说话习惯）
    report = (
        "这个月主要干了三件事：删代码，换渲染，认错。\n"
        "删了多少：方寸 1774 行，墨坊 1325 行，合计 3099 行。\n"
        "提交次数：绒花 333 次，方寸 334 次，司天 238 次，配套 278 次。\n"
        "结论很明确：以前每个产品都要自己养一套模型调用：自己的 key，自己的客户端。\n"
        "还有一个细节值得说：管线拆成了 8 段：每段都有账。\n"
        "最后补充一点：这个月总共 1067 次提交，比上个月增长 12%。\n"
    ) * 3   # 重复到 >300 汉字
    r_rep = av.scan_density(report)
    check("汇报腔样本适用（够长）", r_rep["applicable"] is True, r_rep.get("chars"))
    codes = [f["code"] for f in r_rep["flags"]]
    check("汇报腔样本命中“冒号密度”", "high_colon" in codes, codes)
    check("汇报腔样本命中“数字密度”", "high_digits" in codes, codes)

    # 叙述：冒号少、数字少（模拟作者的写法）
    narr = (
        "他推开门，雨还在下。\n"
        "院子里没有灯，檐下挂着水帘，风把灯火吹得直晃。\n"
        "他站了很久没有动，忽然想起父亲说过的一句话。\n"
        "那句话他记得不清楚了，只记得父亲说的时候也在下雨。\n"
        "后来他走进院子，把门从外面带上，没有再回头看一眼。\n"
    ) * 8
    r_nar = av.scan_density(narr)
    check("叙述样本适用（够长）", r_nar["applicable"] is True, r_nar.get("chars"))
    check("叙述样本**不**命中冒号", "high_colon" not in [f["code"] for f in r_nar["flags"]],
          {"flags": r_nar["flags"], "per_k": r_nar["colon_per_kchar"]})
    check("叙述样本**不**命中数字", "high_digits" not in [f["code"] for f in r_nar["flags"]],
          {"digits_per_300": r_nar["digits_per_300"]})

    # ---------------------------------------------------------------- 短样本
    print("\n【2】短样本不判（样本不足时不给结论）")
    r_short = av.scan_density("他推开门：雨还在下。")
    check("短样本 applicable=False", r_short["applicable"] is False, r_short)
    check("短样本给出原因而不是静默", "note" in r_short, r_short.get("note"))

    # ---------------------------------------------------------------- 真实样本
    print("\n【3】真实样本：AI 稿命中、作者稿干净")
    v1p, v2p = SAMPLE / "文案草稿_v1.md", SAMPLE / "文案草稿_v2.md"
    if not v1p.exists() or not v2p.exists():
        check("真实样本存在（缺则跳过）", False, str(SAMPLE))
    else:
        v1 = body_only(io.open(v1p, encoding="utf-8").read())
        v2 = body_only(io.open(v2p, encoding="utf-8").read())
        a = av.scan_density(v1)
        b = av.scan_density(v2)

        print("      AI 稿(v1 正文)：冒号 %.2f/千字，数字 %.1f/300字"
              % (a["colon_per_kchar"], a["digits_per_300"]))
        print("      作者稿(v2 正文)：冒号 %.2f/千字，数字 %.1f/300字"
              % (b["colon_per_kchar"], b["digits_per_300"]))

        check("AI 稿命中冒号密度（9.5 > 4.0）",
              "high_colon" in [f["code"] for f in a["flags"]],
              a["colon_per_kchar"])
        check("**作者稿不命中冒号**（0 个，这是最关键的一条）",
              "high_colon" not in [f["code"] for f in b["flags"]],
              b["colon_per_kchar"])
        # 数字：AI 10.6 / 作者 3.5，阈 7.0 —— 这两条必须分别成立，
        # 否则一个"永远命中"或"永远不命中"的实现都能通过。
        check("AI 稿命中数字密度（10.6 > 7.0）",
              "high_digits" in [f["code"] for f in a["flags"]],
              a["digits_per_300"])
        check("**作者稿不命中数字**（3.5 < 7.0）",
              "high_digits" not in [f["code"] for f in b["flags"]],
              b["digits_per_300"])

        # 附录污染：整篇算会把作者稿也算出冒号
        full2 = io.open(v2p, encoding="utf-8").read()
        check("证明“必须传正文”：整篇算会混进附录冒号",
              full2.count("：") > v2.count("："),
              {"全文": full2.count("："), "正文": v2.count("：")})

    print("\n" + "-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
