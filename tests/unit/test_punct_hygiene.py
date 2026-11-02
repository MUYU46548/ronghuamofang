# -*- coding: utf-8 -*-
"""标点卫生：直角引号不该出现在任何地方，也不该进小说产物（2026-11-02 作者口径）。

## 口径

- **代码 / 文档 / 提示词 / 配置**：文件里不许出现字面直角引号（U+300C~U+300F）。
- **小说产物**：所有写正文的入口都要过归一化；最后导出合并再兜底一次；
  章节校验把残留直角引号判成 **error**（不是 warning），否则脏稿会一路进成品。

## 为什么测试自己也不写字面

写本文件的字面量就会让 H1 恒红。所以本文件用 `chr(0x300c)` 构造字符 ——
**判据本身必须满足判据**，否则规则第一天就失效。

## 判据

- H1 全仓（git 索引）零字面直角引号
- H2 归一化把四个字符都换掉，且换对（“”‘’）
- H3 七个写正文的入口都调了归一化
- H4 章节校验把直角引号判 error

用法：python tests/unit/test_punct_hygiene.py
"""
import io
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.text_norm import normalize_chinese_punctuation_with_counts   # noqa: E402
from utils import punct_check                                            # noqa: E402

#: 被测字符（不用字面，避免本文件自己违规）
L, R, SL, SR = (chr(0x300C), chr(0x300D), chr(0x300E), chr(0x300F))
BAD = L + R + SL + SR

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("标点卫生")
    print("=" * 66)

    # ---- H1 全仓零命中 ----
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z"],
                             cwd=str(ROOT), capture_output=True, timeout=30)
        files = [x for x in out.stdout.decode("utf-8", "replace").split("\0") if x] \
            if out.returncode == 0 else []
    except Exception:                                        # noqa: BLE001
        files = []
    if not files:
        check("H1 全仓扫描（无 git 索引 → 跳过）", True, "安装态不适用")
    else:
        bad = []
        for rel in files:
            p = ROOT / rel
            if not p.is_file() or p.suffix.lower() in (
                    ".png", ".jpg", ".ico", ".docx", ".dotx", ".pdf", ".lock"):
                continue
            try:
                t = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(c in t for c in BAD):
                bad.append(rel)
        check("H1 全仓零字面直角引号", not bad, bad[:6])

    # ---- H2 归一化换对 ----
    src = "%s双%s %s单%s" % (L, R, SL, SR)
    got, _counts = normalize_chinese_punctuation_with_counts(src)
    check("H2 双直角引号 → “”", got.startswith(chr(0x201C)) and chr(0x201D) in got, got)
    check("H2 单直角引号 → ‘’", chr(0x2018) in got and chr(0x2019) in got, got)
    check("H2 一个都不剩", not any(c in got for c in BAD), got)

    # ---- H3 七个写入口 ----
    entry = {
        "stage4_writing": "写作",
        "stage5_check": "检查稿",
        "stage6_polish": "润色",
        "stage7_convert": "导出合并",
        "refine_chapter": "整章改写",
        "refine_paragraph": "段落重写/手改",
        "import_docx": "Word 导入",
    }
    for mod, label in entry.items():
        p = ROOT / "scripts" / ("%s.py" % mod)
        t = p.read_text(encoding="utf-8")
        check("H3 %s（%s）调了归一化" % (mod, label),
              "normalize" in t, "没有 normalize* 调用")

    # ---- H4 校验层拦截 ----
    errs, warns, _m = punct_check.check_punctuation("他说%s走吧%s。" % (L, R))
    joined = " ".join(errs)
    check("H4 直角引号被判 error（不是 warning）", bool(errs), (errs, warns))
    check("H4 error 里说清是什么", "直角引号" in joined, joined)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
