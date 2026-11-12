# -*- coding: utf-8 -*-
"""0.9.1“让界面不撒谎”：候选卡就地回退 + self 模式下不再有“点了才报错”的按钮。

## 为什么需要它

两件事在盘上都成立过：
1. `ChapterEditor.vue` 的候选卡文案写着“旧版进段落历史，可回退”，而回退按钮住在
   “精修”页签 —— **承诺与入口分居两屏**。竞品调研（docs/竞品调研与开发计划-20261110.md
   六·1）把它列为第一条该修的：文案已经承诺、动作没有。
2. `engine: self` 下 `/refine/paragraph` 必然 400，但只有章节编辑器的按钮被置灰了，
   “精修”页签与审阅台的批量精修按钮照旧可点 —— 作者点下去只得到一句“用不了”
   （0.9.1 验收第 16 条那一族）。

## 判据（都是"会失败"的判据：删掉对应实现即红）

A. 段落候选区三个动作齐全，回退复用已有端点，不自造新端点
B. **采用之后历史仍可展开**（`cand || histOpen`）—— 后悔最常发生在点完“采用”之后
C. 回退是两步确认，且明说“当前这段会先存成一条新历史”（回退本身也能退）
D. self 模式：精修页签三动作 + 审阅台批量精修都被禁用，并写明原因
E. `App.vue` 真把 `selfEngine` 传下去了 —— 只加 prop 不传参等于永不生效
F. 它调的两个端点仍在 ROUTES 里（只断言前端"调了"，端点被删也该红）

全程零 LLM、零费用、不碰工作区数据。
用法：python tests/unit/test_revert_and_self_disabled.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SRC = ROOT / "console" / "src"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def read(name):
    return (SRC / name).read_text(encoding="utf-8")


def main():
    editor = read("ChapterEditor.vue")

    print("【A】候选卡三个动作（采用 / 放弃 / 回到上一版）")
    check("候选卡有“回到上一版”入口", "回到上一版" in editor)
    check("回退复用段落历史端点", "/chapters/paragraph/history" in editor)
    check("回退复用段落恢复端点", "/chapters/paragraph/restore" in editor)
    check("历史读取与回退各有实现",
          "async function toggleParaHistory" in editor and "async function restorePara" in editor)
    check("不引新端点（history/restore 之外没有第三个 history 路由）",
          editor.count("/chapters/paragraph/history") >= 1
          and "/chapters/paragraph/history/save" not in editor)

    print("\n【B】采用之后仍能退（后悔最常发生在点完采用之后）")
    check("候选块的条件是 cand || histOpen（不是只看 cand）",
          'v-if="cand || histOpen"' in editor)
    check("候选为空时仍显示历史标题",
          "这一段的历史" in editor)

    print("\n【C】两步确认 + 回退可回退")
    check("第一次点只问、第二次才动手",
          "histConfirm.value !== h.id" in editor)
    check("明说当前这段会先存成一条新历史",
          "先存成一条新历史" in editor or "先存为一条新历史" in editor)
    check("换段落会收起上一段的历史（免得退错段）",
          "histOpen.value = false" in editor)

    print("\n【D】self 模式：精修页签与审阅台不再“点了才报错”")
    para = read("ParagraphRefinePanel.vue")
    check("精修页签接收 selfEngine", "selfEngine: { type: Boolean" in para)
    check("执行重写 / dry-run / 批量重写 三处都按 selfEngine 禁用",
          para.count("|| selfEngine") >= 3, para.count("|| selfEngine"))
    check("禁用带原因（不是灰掉不说为什么）", "SELF_HINT" in para and "engine: self" in para)
    check("页头有常驻说明（不用点了才知道）", "self-hint" in para and "自任务模式" in para)

    review = read("ReviewConsole.vue")
    check("审阅台批量精修按钮按 selfEngine 禁用",
          "!hasDecision || props.selfEngine" in review)

    print("\n【E】参数真的传下去了（只加 prop 不传 = 永不生效）")
    app = read("App.vue")
    line = [ln for ln in app.splitlines() if "<ParagraphRefinePanel" in ln]
    check("App.vue 给精修页签传了 self-engine",
          bool(line) and "self-engine" in line[0], line[:1])
    check("selfEngine 仍由后端 engine 字段推导",
          'engine === "self"' in app)

    print("\n【F】前端调的两个端点仍在后端注册")
    from nf_api_domains import refine as dom_refine
    routes = {(m, p) for m, p, _h in dom_refine.ROUTES}
    check("GET /chapters/paragraph/history 在 ROUTES",
          ("GET", "/chapters/paragraph/history") in routes)
    check("POST /chapters/paragraph/restore 在 ROUTES",
          ("POST", "/chapters/paragraph/restore") in routes)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
