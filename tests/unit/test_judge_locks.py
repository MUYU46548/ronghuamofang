# -*- coding: utf-8 -*-
"""P0 拆锁与预算校验的判据（F15/F16/F17 + R01/D1）—— 零 token、纯静态 + 纯函数。

三条拆锁各自带**反证**（把锁加回去，判据必须变红）：
- F15 「擅长克制叙事」写死在 stage4 系统提示里
- F16 「不得新增关键情节」= 密度上限锁
- F17 无范文时仍注入「中立克制 / 内敛克制」风格指令

用法：python tests/unit/test_judge_locks.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import style_analyzer as SA                       # noqa: E402
from utils.judge import budget as BUD                        # noqa: E402
from utils.judge import caliber as C                         # noqa: E402

PASS, FAIL = [], []

LOCK_A = "擅长克制叙事"
LOCK_B = "不得新增关键情节"
LOCK_C = ("请保持情绪的中立克制", "请保持内敛克制的叙事")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def scan(text, needles):
    return [n for n in needles if n in text]


def main():
    stage4 = (ROOT / "prompts" / "stage4_writing.md").read_text(encoding="utf-8")

    print("=== A. F15/F16：两处提示词锁已拆 ===")
    check("stage4 不含「%s」" % LOCK_A, LOCK_A not in stage4,
          scan(stage4, [LOCK_A]))
    check("stage4 不含「%s」" % LOCK_B, LOCK_B not in stage4,
          scan(stage4, [LOCK_B]))
    # 反证：把锁写回一份临时文本，判据必须变红
    fake = stage4 + "\n你是" + LOCK_A + "。\n可补充细节，但" + LOCK_B + "。\n"
    check("反证：锁加回去后扫描必红", scan(fake, [LOCK_A, LOCK_B]) == [LOCK_A, LOCK_B],
          scan(fake, [LOCK_A, LOCK_B]))

    print("\n=== B. F17：无范文不注入风格 ===")
    src = (ROOT / "scripts" / "utils" / "style_analyzer.py").read_text(encoding="utf-8")
    check("源码里不再有那两句兜底指令", not scan(src, LOCK_C), scan(src, LOCK_C))
    check("无范文（空特征）→ 产出空串", SA.build_style_instruction({}) == "",
          repr(SA.build_style_instruction({}))[:40])
    neutral = SA.build_style_instruction({
        "sentiment": {"tone": "中性", "positive": 0, "negative": 0, "polarity": 0}})
    check("无明确情感倾向 → 不写「中立克制」", not scan(neutral, LOCK_C),
          neutral[:120])
    low_dlg = SA.build_style_instruction({"dialogue_ratio": 0.02})
    check("对白占比低 → 只报读数，不追加风格指令", "对白占比低" in low_dlg
          and not scan(low_dlg, LOCK_C), low_dlg[-80:])

    print("\n=== C. D1：stage5/stage6 的 max_tokens 已抬到能装下 ===")
    s5 = BUD.stage_max_tokens(5, str(ROOT))
    s6 = BUD.stage_max_tokens(6, str(ROOT))
    check("stage5 max_tokens ≥ 25000", s5 and s5 >= 25000, s5)
    check("stage6 max_tokens ≥ 15000", s6 and s6 >= 15000, s6)

    print("\n=== D. R01：预算—篇幅联动（装不下就不发请求） ===")
    proj = {"chapter": {"target_words": [5000, 9000]}}
    ok1, msg1, d1 = BUD.check_capacity(proj, 5, str(ROOT), max_tokens=8000, words=5967)
    check("8000 装 5967 字 → 拒绝", ok1 is False and "只够装" in msg1, msg1)
    check("拒绝理由给两组数字", "5967" in msg1 and "8000" in msg1, msg1)
    ok2, msg2, d2 = BUD.check_capacity(proj, 6, str(ROOT), max_tokens=4000, words=2186)
    check("4000 装 2186 字 → 拒绝（AC-01-4 的实测对）", ok2 is False, msg2)
    ok3, msg3, d3 = BUD.check_capacity(proj, 5, str(ROOT), max_tokens=25000, words=5967)
    check("25000 装 5967 字 → 放行", ok3 is True, msg3)
    ok4, msg4, _ = BUD.check_capacity({}, 5, str(ROOT), max_tokens=25000, words=0)
    check("目标字数未知 → 拒绝（不猜 2000）", ok4 is False and "未知" in msg4, msg4)
    check("需要量 = 字数 × 系数 × 安全系数", d3["needed"] == BUD.needed_tokens(5967), d3)

    print("\n=== E. 阶段入口：先查预算，再调模型 ===")
    s5src = (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8")
    s6src = (ROOT / "scripts" / "stage6_polish.py").read_text(encoding="utf-8")
    i5g = s5src.find("check_capacity(proj, 5")
    i5r = s5src.find("client.run_task(")
    check("stage5：预算校验在第一次模型调用之前", 0 < i5g < i5r, (i5g, i5r))
    i6g = s6src.find("check_capacity(proj, 6")
    i6r = s6src.find("ThreadPoolExecutor(max_workers")
    check("stage6：预算校验在发起润色之前", 0 < i6g < i6r, (i6g, i6r))

    print("\n=== F. 系数登记（R04 AC-04-5：不许散落配置文件无人读） ===")
    cal = C.load(str(ROOT))
    vals = C.values(cal)
    check("J13/J14（token 系数/安全系数）在登记表里", "J13" in vals and "J14" in vals,
          {k: vals.get(k) for k in ("J13", "J14")})
    by_id = {c["id"]: c for c in cal["criteria"]}
    check("两者消费方 = budget.guard",
          by_id["J13"]["consumer"] == "budget.guard"
          and by_id["J14"]["consumer"] == "budget.guard",
          (by_id["J13"]["consumer"], by_id["J14"]["consumer"]))

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
