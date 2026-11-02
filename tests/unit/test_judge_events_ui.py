# -*- coding: utf-8 -*-
"""I3 落点与接线：事件勾选必须在**分章页**，且两处共用一份实现（2026-10-31）。

## 用户提的问题

方寸列的未完成项 I3：\u300c事件勾选放在单章详情里，没有单独落在\u300e分章\u300f页签
（04 交互定义的原位置）\u300d。

这条不是审美问题 —— 用户跑回测的第一步就是\u300c逐章勾这章发生了几件事\u300d，
而他现在得先进\u300c章节 → 判定 → 单章详情\u300d才能勾，跟大纲对照着看很别扭。

## 判据

- U1 分章页（ChapterBlueprint）真的挂了 JudgeEvents，且在章纲卡片下方
- U2 判定详情（JudgePanel）用的是**同一个组件**（不许两份实现）
- U3 组件本身守住三条硬约束：0 事件不可勾、四类必填、两个计数分开显示
- U4 分章页的提示通道是通的（`@toast` 才是 App 在听的那个；`window._bpToast`
     从来没被赋值 —— 这个 bug 让\u300c已保存/保存失败\u300d静默了不知多久）

用法：python tests/unit/test_judge_events_ui.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "console" / "src"
PASS, FAIL = [], []


def code_only(text):
    """去掉注释后再做字符串断言。

    教训：注释里写\u300c不用 /judge/chapter\u300d本身是好事，但如果断言直接扫全文，
    这句解释会把用例判红 —— 于是最后大家把注释删掉，注释就白写了。
    """
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("I3：事件勾选落在分章页 + 两处共用一份实现")
    print("=" * 66)
    bp = (SRC / "ChapterBlueprint.vue").read_text(encoding="utf-8")
    jp = (SRC / "JudgePanel.vue").read_text(encoding="utf-8")
    comp_path = SRC / "JudgeEvents.vue"
    check("组件 JudgeEvents.vue 存在", comp_path.is_file())
    comp = comp_path.read_text(encoding="utf-8") if comp_path.is_file() else ""

    print("\n【U1】分章页：章纲卡片下方就是勾选区")
    check("ChapterBlueprint 引入了 JudgeEvents",
          'import JudgeEvents from "./JudgeEvents.vue"' in bp)
    check("模板里挂了 <JudgeEvents :chapter=\"currentN\" ...>",
          "<JudgeEvents" in bp and ':chapter="currentN"' in bp)
    check("切章就拉事件（loadCurrent 里调 loadJudgeEvents）",
          "await loadJudgeEvents(n)" in bp)
    check("章纲保存后重拉事件（事件列表可能增删）",
          bp.count("loadJudgeEvents(currentN.value)") >= 1
          or bp.count("loadJudgeEvents(currentN)") >= 1)
    bp_code = code_only(bp)
    check("走的是轻读端点（outline=1），不在分章页跑完整分析",
          "&outline=1" in bp_code and "/judge/chapter" not in bp_code,
          "分章页若调 /judge/chapter 会白算一遍三稿对照")

    print("\n【U2】判定详情复用同一组件（不许两份实现）")
    check("JudgePanel 也 import 了 JudgeEvents",
          'import JudgeEvents from "./JudgeEvents.vue"' in jp)
    check("JudgePanel 里 <JudgeEvents 带 @saved（保存后刷新档位）",
          "<JudgeEvents" in jp and '@saved="onEventsSaved"' in jp)
    check("JudgePanel 不再自己维护勾选状态（evChecked/evClass 已删）",
          "evChecked" not in jp and "evClass" not in jp)
    check("整块旧实现已移除（judge-events 类不再由 JudgePanel 渲染）",
          'class="judge-events"' not in jp)

    print("\n【U3】组件守住三条硬约束")
    check("0 事件 → 明说为什么勾不了（2026-10-31 改文案：指出该去改章纲，而不是干说“不可勾选”）",
          "还没写" in comp and "核心事件" in comp and "不可判定" in comp)
    check("四类必填：勾了没分类会拦下（保存前检查 unclassified）",
          "unclassified" in comp and "四类必选一类" in comp)
    check("两个计数分开算（checkedCount / countedCount）",
          "checkedCount" in comp and "countedCount" in comp)
    check("界面上把差别摊开（勾了 N 件 · 计入下限 M / min）",
          "勾了" in comp and "计入下限" in comp and "{{ eventMin }}" in comp)
    check("没填证据句时明确说\u300c不算进 N 件\u300d（不是悄悄少算）",
          "不算进" in comp)
    check("分类按钮用的是四类固定值（与后端 EVENT_CLASSES 同口径）",
          all(c in comp for c in ("人物处境", "信息占有", "关系", "目标")))
    check("提示语只做展示，不参与结论（hint/densityNote 只读）",
          "系统不替你合并" in comp or "只提示、不改档" in comp)

    print("\n【U4】分章页提示通道是通的")
    check("ChapterBlueprint 声明了 emits",
          'defineEmits(["toast"])' in bp)
    check("say() 走 emit，不再写那个从未赋值的 window._bpToast",
          'emit("toast", msg)' in bp_code and "_bpToast" not in bp_code)
    check("App.vue 监听的事件名与之一致（@toast）",
          '@toast="say"' in (SRC / "App.vue").read_text(encoding="utf-8"))

    print("\n【U5】没有引入新页签（04 定义：本次不新增任何顶层页签）")
    app = (SRC / "App.vue").read_text(encoding="utf-8")
    tabs = re.findall(r"\{ t: \"[a-z_]+\", name: \"([^\"]+)\"", app)
    check("页签数量没有因为加这块而增加（分章页仍在）",
          "分章" in tabs, tabs)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
