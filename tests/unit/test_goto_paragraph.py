# -*- coding: utf-8 -*-
"""指到即到（2026-11-02，一期 F07）。

## 要治的病

缺陷单只说"第 3 段命中 XX 规则"，正文在右边几百段里 —— 人得自己找。
"改一处"的链条在这里断了：看见了问题，但找不到那句话。

## 判据（前端无 DOM，取"结构 + 接线"口径；真行为要打包后跑客户端）

- J1 缺陷行能点到，且点击会调定位
- J2 正文段落带锚点（`data-para`），能滚过去
- J3 点到的那一段有高亮样式（滚过去了看不出来也不行）
- J4 能带着这一段跳到段落精修工作台（跨页签）
- J5 段落面板接得住（focus → 自动载入该章并选中那一段）
- J6 同一段再点一次也要生效（用时间戳，否则 watch 不触发）

用法：python tests/unit/test_goto_paragraph.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("指到即到")
    print("=" * 66)

    judge = (ROOT / "console" / "src" / "JudgePanel.vue").read_text(encoding="utf-8")

    check("J1 缺陷行点得到，且点击会定位",
          'focusPara(d.paragraph_index)' in judge,
          "点击处理器里没有 focusPara")
    check("J2 正文段落有锚点 data-para",
          ':data-para="p.paragraph_index"' in judge)
    check("J2 会滚到那一段", "scrollIntoView" in judge)
    check("J3 命中的段落有高亮样式",
          "judge-para-focus" in judge and "judge-para-body.judge-para-focus" in judge,
          "只有 class 没有样式，等于滚过去看不见")
    check("J4 能带着这一段去精修", "去精修这一段" in judge and 'emit("goto-paragraph"' in judge)

    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("J4 App 接了 goto-paragraph 事件",
          '@goto-paragraph="gotoParagraph"' in app)
    check("J4 切到精修页签", 'switchTab("refine")' in app or "switchTab('refine')" in app)
    check("J4 把位置传给段落面板", ':focus="refineFocus"' in app)
    check("J6 带时间戳（同一段再点也生效）", "at: Date.now()" in app)

    panel = (ROOT / "console" / "src" / "ParagraphRefinePanel.vue").read_text(encoding="utf-8")
    check("J5 面板声明了 focus", "focus: { type: Object" in panel)
    check("J5 watch 住 focus", "watch(() => props.focus" in panel)
    check("J5 自动载入该章", "await loadParagraphs(f.chapter)" in panel)
    check("J5 自动选中那一段", "selectPara(f.index)" in panel)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
