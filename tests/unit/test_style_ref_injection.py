# -*- coding: utf-8 -*-
"""范文注入上限的回归锁（2026-10-19）。

## 为什么需要它

`stage4` 原本是 `read_text(style_ref).strip()[:6000]` —— **静默截断**：

- 作者给了一整章 8000 字，实际只喂进去 6000，**没有任何提示**，
  于是"范文没起作用"会被误判成"模型学不像"；
- 配了路径但文件不存在时同样是静默占位（`（未提供…）`），
  和"本来没配"看起来一模一样。

项目纪律是「静默降级比没有更糟」，所以把上限改成可配
（`book.style_reference_max_chars`，1000~40000），且**截断/缺文件都必须出声**。

| 编号 | 钉什么 |
|---|---|
| R1 | 未配置 → 占位文本，meta.configured=False，不出 WARN |
| R2 | 配了但文件不存在 → 占位 + **WARN**（不许静默） |
| R3 | 短文（未超限）→ 全文注入，truncated=0，无 WARN |
| R4 | 超长 → 注入到上限为止、truncated 报出真实差值、**WARN 写明丢了多少字** |
| R5 | 自定义上限生效 |
| R6 | 越界值夹到区间（0 → 1000，999999 → 40000） |
| R7 | 坏值（非数字）回落默认 6000 |
| R8 | 注入的是**前 N 字**（不是随机/尾部） |

用法：python tests/unit/test_style_ref_injection.py（零 LLM、零网络）
"""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from stage4_writing import (                                  # noqa: E402
    STYLE_REF_PLACEHOLDER, build_style_reference_block)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(extra)[:180]) if extra else ""))


def run(book):
    """跑一次并抓 stdout（WARN 是否打印）。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        text, meta = build_style_reference_block(book)
    return text, meta, buf.getvalue()


def main():
    print("=" * 64)
    print("  范文注入：上限 / 截断可见性")
    print("=" * 64)

    root = Path(tempfile.mkdtemp(prefix="nf_style_"))
    long_text = "".join("第%d段：他推开门，风从走廊尽头灌进来。" % i for i in range(1, 401))
    long_path = root / "long.md"
    long_path.write_text(long_text, encoding="utf-8")
    short_path = root / "short.md"
    short_path.write_text("他把刀放下，说了句「走吧」。" * 20, encoding="utf-8")

    # ------------------------------------------------------------------ R1
    print("\n[R1] 未配置")
    text, meta, out = run({"style_reference": ""})
    check("R1 返回占位文本", text == STYLE_REF_PLACEHOLDER, text[:40])
    check("R1 configured=False 且不出 WARN", meta["configured"] is False and "WARN" not in out)

    # ------------------------------------------------------------------ R2
    print("\n[R2] 配了但文件不存在")
    text, meta, out = run({"style_reference": str(root / "不存在.md")})
    check("R2 仍是占位文本", text == STYLE_REF_PLACEHOLDER)
    check("R2 **必须 WARN**（与「本来没配」区分开）",
          "WARN" in out and "不存在" in out, out.strip()[:80])
    check("R2 meta 标出 exists=False", meta["exists"] is False)

    # ------------------------------------------------------------------ R3
    print("\n[R3] 短文不超过上限")
    text, meta, out = run({"style_reference": str(short_path)})
    check("R3 全文注入且与原文一致", text == short_path.read_text(encoding="utf-8").strip())
    check("R3 truncated=0 且无 WARN",
          meta["truncated"] == 0 and "WARN" not in out, out.strip()[:80])

    # ------------------------------------------------------------------ R4
    print("\n[R4] 超长（%d 字 > 6000）" % len(long_text))
    text, meta, out = run({"style_reference": str(long_path)})
    check("R4 注入 6000 字", meta["injected"] == 6000 and len(text) == 6000,
          meta["injected"])
    check("R4 truncated 报出真实差值", meta["truncated"] == len(long_text) - 6000,
          meta["truncated"])
    check("R4 **WARN 写明丢了多少字**（不许静默截断）",
          "WARN" in out and str(meta["truncated"]) in out, out.strip()[:120])

    # ------------------------------------------------------------------ R5
    print("\n[R5] 自定义上限")
    text, meta, out = run({"style_reference": str(long_path),
                           "style_reference_max_chars": 2000})
    check("R5 按自定义上限截断", meta["injected"] == 2000 and meta["limit"] == 2000,
          meta)

    # ------------------------------------------------------------------ R6
    print("\n[R6] 越界值夹到区间")
    _, m_lo, _ = run({"style_reference": str(long_path), "style_reference_max_chars": 0})
    _, m_hi, _ = run({"style_reference": str(long_path),
                      "style_reference_max_chars": 999999})
    check("R6 0 → 下限 1000", m_lo["limit"] == 1000, m_lo["limit"])
    check("R6 999999 → 上限 40000", m_hi["limit"] == 40000, m_hi["limit"])

    # ------------------------------------------------------------------ R7
    print("\n[R7] 坏值回落默认")
    _, m_bad, _ = run({"style_reference": str(long_path),
                       "style_reference_max_chars": "六千"})
    check("R7 非数字回落 6000", m_bad["limit"] == 6000, m_bad["limit"])

    # ------------------------------------------------------------------ R8
    print("\n[R8] 注入的是前 N 字")
    text8, _, _ = run({"style_reference": str(long_path),
                       "style_reference_max_chars": 1000})
    check("R8 与原文前缀逐字一致", text8 == long_text.strip()[:1000])

    print("\n" + "=" * 64)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
