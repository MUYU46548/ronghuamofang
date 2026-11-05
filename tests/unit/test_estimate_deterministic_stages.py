# -*- coding: utf-8 -*-
"""阶段 7 / 8 的 token 预估（2026-11-05）。

## 要治的病

客户端进“流水线”页签会调 `/estimate?stage=8`（Markdown 导出）做选阶段预估。
`estimate_tokens._heuristic_stage` 只认 1~6，`else: raise ValueError("未知阶段")`
→ 端点 500，控制台每次留一条红字。而阶段 7/8 是**确定性转换，根本不调模型**，
正确答案是 0，不是“未知”。

## 判据

- C1 阶段 7 / 8 返回 calls=0、tokens 0、basis 说清“不调用模型”
- C2 真的未知阶段（9）仍然报错 —— 不要把“把异常吞掉”当成修法
- C3 阶段 4 的预估不受影响（仍是正数）

用法：python tests/unit/test_estimate_deterministic_stages.py
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
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("阶段 7 / 8 的 token 预估")
    print("=" * 66)

    import estimate_tokens as et

    cfg = {"engine": "direct",
           "model": {"default": {"provider": "tokenhub", "id": "glm-5.3"}},
           "chapter": {"target_words": [5000, 9000]}}
    proj = {"book": {"name": "验收用书", "chapters": 12}}

    print("\n[C1] 确定性阶段 = 0")
    for st in (7, 8):
        row = et._heuristic_stage(st, cfg, proj, calls=1, cpt=1.5, role="default")
        check("阶段 %d calls=0" % st, row["calls"] == 0, row["calls"])
        check("阶段 %d tokens 全 0" % st,
              row["tokens_in"] == 0 and row["tokens_out"] == 0,
              (row["tokens_in"], row["tokens_out"]))
        check("阶段 %d 说明为什么不花钱" % st,
              any("不调用模型" in b for b in row["basis"]), row["basis"][:2])

    print("\n[C2] 未知阶段仍报错（不许吞异常）")
    try:
        et._heuristic_stage(9, cfg, proj, calls=1, cpt=1.5, role="default")
        check("阶段 9 抛 ValueError", False, "没有抛")
    except ValueError as e:
        check("阶段 9 抛 ValueError", "未知阶段" in str(e), str(e))

    print("\n[C3] 阶段 4 不受影响")
    row4 = et._heuristic_stage(4, cfg, proj, calls=12, cpt=1.5, role="default")
    check("阶段 4 预估为正 token", row4["tokens_in"] > 0 and row4["tokens_out"] > 0,
          (row4["tokens_in"], row4["tokens_out"]))

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
