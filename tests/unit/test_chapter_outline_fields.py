# -*- coding: utf-8 -*-
"""章大纲六字段的自检（2026-10-20，v0.7.2「章大纲四字段」）。

## 为什么加这两个字段

书童（Pagekin）的章节蓝图是「主角小目标 / 冲突与转折 / 悬念钩子 / 出场人物」。
我们的章大纲一直只有「核心事件 / 涉及角色 / 功能 / 衔接」——
**缺的正好是写作时最用得上的两个抓手**：

- 没有「小目标」，一章就是"发生了几件事"（模型会平铺直叙）；
- 没有「悬念钩子」，章末没有理由让人翻页；它还是 v0.9.3 伏笔台账的**唯一确定性数据源**
  （不给这个字段，台账就得靠模型自由发挥，等于没有判据）。

## 主判据

1. 六字段齐全 → strict 通过
2. 缺「小目标」/「悬念钩子」→ strict 失败，但**宽松模式通过**（存量宽容：
   升级一次不能把用户已经写完的大纲全判废）
3. 字段行存在但**值为空** → 连宽松也失败（旧实现只判 `f in text`，空值照样绿）
4. 值太短（"小目标：调查"）→ strict 失败，错误信息要说清几个字、下限几个字
5. 正文里出现"钩子/小目标"字样但**没有字段行** → 不算有该字段（旧实现的典型假绿）
6. 加粗写法 `**小目标**：` 也认
7. `parse_chapter_outline` 能一次取出六个字段（供伏笔台账/GUI 复用，不许各写一份正则）
8. 提示词里六字段都在，且带反例/正例
9. 假客户端产出的章大纲**必须能过 strict**（夹具走生产契约，否则 stage3 假红或假绿）

用法：python tests/unit/test_chapter_outline_fields.py
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

import stage3_chapter_outline as S3                                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (str(detail)[:300],)))


FULL = """## 第1章 锈锁

- 核心事件：苏芷想取回借阅卡，被档案室的门禁挡住，最后从侧窗翻进去
- 涉及角色：苏芷、周砚清
- 小目标：拿到 1978 年那张借阅卡
- 悬念钩子：卡片背面的签名是她父亲的名字
- 功能：推进
- 衔接：承接雨夜拜访
"""


def write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    return p


def main():
    print("=" * 62)
    print("  章大纲六字段自检（v0.7.2）")
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp())
    try:
        print("\n[P1] 六字段齐全")
        p = write(tmp, "full.md", FULL)
        ok, errs = S3.check_chapter_outline(p, strict=True)
        check("P1 六字段齐全 → strict 通过", ok, errs)
        ok2, errs2 = S3.check_chapter_outline(p)
        check("P1 宽松模式也通过", ok2, errs2)

        print("\n[P2] 缺字段：strict 失败、宽松通过（存量宽容）")
        no_goal = write(tmp, "no_goal.md", FULL.replace("- 小目标：拿到 1978 年那张借阅卡\n", ""))
        ok3, errs3 = S3.check_chapter_outline(no_goal, strict=True)
        check("P2 缺「小目标」→ strict 失败", not ok3, errs3)
        check("P2 错误点名缺的是小目标", any("小目标" in e for e in errs3), errs3)
        ok4, _ = S3.check_chapter_outline(no_goal)
        check("P2 但宽松模式通过（老项目不会因升级判废）", ok4)

        no_hook = write(tmp, "no_hook.md", FULL.replace("- 悬念钩子：卡片背面的签名是她父亲的名字\n", ""))
        ok5, errs5 = S3.check_chapter_outline(no_hook, strict=True)
        check("P2 缺「悬念钩子」→ strict 失败", not ok5, errs5)

        print("\n[P3] 字段存在但值为空 / 太短")
        empty = write(tmp, "empty.md", FULL.replace("- 小目标：拿到 1978 年那张借阅卡", "- 小目标："))
        ok6, errs6 = S3.check_chapter_outline(empty, strict=True)
        check("P3 新字段有行但值为空 → strict 失败", not ok6, errs6)
        check("P3 错误说「缺少字段或值为空」", any("值为空" in e for e in errs6), errs6)

        # 硬字段空值：**宽松模式**也必须失败（旧实现 `f in text` 会让它绿）
        empty_hard = write(tmp, "empty_hard.md",
                           "\n".join(("- 核心事件：" if ln.startswith("- 核心事件：") else ln)
                                     for ln in FULL.splitlines()))
        ok6b, errs6b = S3.check_chapter_outline(empty_hard)
        check("P3 硬字段值为空 → 宽松模式也失败（旧实现会绿）", not ok6b, errs6b)
        check("P3 并点名是核心事件", any("核心事件" in e for e in errs6b), errs6b)

        short = write(tmp, "short.md", FULL.replace("- 小目标：拿到 1978 年那张借阅卡", "- 小目标：调查"))
        ok7, errs7 = S3.check_chapter_outline(short, strict=True)
        check("P3 值太短（2 字）→ strict 失败", not ok7, errs7)
        check("P3 错误里带实际字数与下限",
              any("只有" in e and str(S3.MIN_FIELD_CHARS) in e for e in errs7), errs7)

        print("\n[P4] 正文提到字段名不算有该字段")
        fake = write(tmp, "fake.md",
                     "## 第1章 锈锁\n\n- 核心事件：苏芷去了档案室，被门挡住\n- 涉及角色：苏芷\n"
                     "- 功能：推进\n\n（这一章的小目标和悬念钩子还没想好）\n")
        ok8, errs8 = S3.check_chapter_outline(fake, strict=True)
        check("P4 正文提到「小目标」不冒充字段", not ok8, errs8)
        check("P4 仍报缺两个新字段",
              sum(1 for e in errs8 if "小目标" in e) == 1
              and sum(1 for e in errs8 if "悬念钩子" in e) == 1, errs8)

        print("\n[P5] 写法兼容")
        bold = write(tmp, "bold.md", FULL.replace("- 小目标：", "- **小目标**：")
                     .replace("- 悬念钩子：", "- **悬念钩子**: "))
        ok9, errs9 = S3.check_chapter_outline(bold, strict=True)
        check("P5 加粗 + 半角冒号也认", ok9, errs9)
        plain = write(tmp, "plain.md", FULL.replace("- ", ""))
        ok10, errs10 = S3.check_chapter_outline(plain, strict=True)
        check("P5 无列表符号也认", ok10, errs10)

        print("\n[P6] 解析器（供伏笔台账/GUI 复用）")
        parsed = S3.parse_chapter_outline(p)
        check("P6 六个字段都解析出来", set(parsed) == set(S3.REQUIRED_FIELDS + S3.NEW_FIELDS),
              list(parsed))
        check("P6 小目标值正确", parsed["小目标"] == "拿到 1978 年那张借阅卡", parsed["小目标"])
        check("P6 钩子值正确", parsed["悬念钩子"] == "卡片背面的签名是她父亲的名字",
              parsed["悬念钩子"])
        check("P6 缺字段返回 None（不是空串，便于区分）",
              S3.parse_chapter_outline(no_goal)["小目标"] is None)

        print("\n[P7] 提示词与夹具")
        prompt = (ROOT / "prompts" / "stage3_chapter_outline.md").read_text(encoding="utf-8")
        check("P7 提示词要求六字段",
              all(f in prompt for f in S3.REQUIRED_FIELDS + S3.NEW_FIELDS),
              [f for f in S3.REQUIRED_FIELDS + S3.NEW_FIELDS if f not in prompt])
        check("P7 提示词给了反例（不许写「继续调查」）", "继续调查" in prompt)
        check("P7 提示词给了正例", "借阅卡" in prompt)

        from utils import fake_client as fc
        (Path(tmp) / "data" / "outline" / "chapters").mkdir(parents=True)
        cwd = Path.cwd()
        import os
        os.chdir(tmp)
        try:
            fc._write_chapter_outlines("- 第1章 → data/outline/chapters/01.md\n- 第2章 → data/outline/chapters/02.md")
            ok11, errs11 = S3.check_chapter_outline("data/outline/chapters/01.md", strict=True)
            check("P7 假客户端产物能过 strict（夹具走生产契约）", ok11, errs11)
            parsed2 = S3.parse_chapter_outline("data/outline/chapters/02.md")
            check("P7 假客户端每章的小目标/钩子各不相同（不是同一句糊过去）",
                  parsed2["小目标"] and "2" in parsed2["小目标"], parsed2["小目标"])
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
