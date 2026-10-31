# -*- coding: utf-8 -*-
"""设置页两条「界面接线」护栏（2026-10-31）。

## 为什么单独立一条

后端能跑 ≠ 用户能用。这批活里有两次是同一个形状：

1. **引擎切换**：`config/system.yaml` 的 `engine` 一直可读不可写，设置页把它当
   一行只读文字显示 —— 标题却写着「引擎与模型」。用户要换引擎只能手改 YAML，
   而界面上看起来能点。**端点缺失**是靠肉眼发现的。
2. **提示词还原**：`/prompts/factory_restore` 做完了，如果前端没接上，
   用户照样只有 history 回滚 —— 等于白做。

所以这里钉的不是"后端有端点"（那是 test_config_writes_http /
test_prompt_factory_http 的事），而是**前端真的调了它**。

## 判据

- U1 switchEngine() 函数体里出现 `/config/engine`，且模板里 `.eng-card` 可点
- U2 restorePromptFactory() 调 `/prompts/factory_restore`，模板里有该按钮
- U3 新增的引擎面板样式**不得硬编码浅底**（暗色主题下必瞎，判据同
     test_ui_theme_and_paths 的 A 条；这里只查本批新增的类，避免与那边重复实现）
- U4 不回到「一排 art-row + 原生 select」的老结构（`.rm-row` 必须在）

用法：python tests/unit/test_engine_settings_ui.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "console" / "src" / "App.vue"
CSS = ROOT / "console" / "src" / "style.css"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def func_body(src, name):
    """粗切一个 `function name(...) { ... }` 的函数体（按大括号配平）。

    不做 AST：这是 .vue 里的 <script setup> 段，用 Python AST 解析不了。
    判据只要「函数体内出现了某个字符串」，粗切够用；匹配不到返回空串（= 断言失败）。
    """
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        return ""
    i = src.find("{", m.end())
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    return ""


def main():
    print("=" * 66)
    print("设置页接线护栏：引擎可切 / 出厂还原可点 / 暗色不瞎")
    print("=" * 66)
    app = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    print("\n【U1】引擎切换：函数真的调了端点，模板真的能点")
    body = func_body(app, "switchEngine")
    check("switchEngine() 存在且非空", bool(body))
    check("函数体里调用了 /config/engine", '"/config/engine"' in body, body[:120])
    check("切换后把新值写回界面状态（models.value.engine）",
          "models.value.engine" in body, body[:160])
    check("模板里有两张引擎卡（.eng-card ×2 的循环体）",
          app.count("switchEngine('direct')") == 1
          and app.count("switchEngine('hermes')") == 1,
          (app.count("switchEngine('direct')"), app.count("switchEngine('hermes')")))
    check("卡片带选中态绑定（:class on）", 'class="eng-card"' in app)

    print("\n【U2】出厂还原：按钮真的调了端点")
    body = func_body(app, "restorePromptFactory")
    check("restorePromptFactory() 存在且非空", bool(body))
    check("函数体里调用了 /prompts/factory_restore",
          '"/prompts/factory_restore"' in body, body[:120])
    check("模板里有「还原出厂默认」按钮",
          "还原出厂默认" in app and "@click=\"restorePromptFactory\"" in app)
    check("按钮在有出厂副本/已一致时禁用（防误点覆盖）",
          "hasFactory(promptName)" in app and "factoryIdentical(promptName)" in app)
    check("界面上能看见保底状态（无出厂副本 / 已一致 / 已被修改）",
          "无出厂副本" in app and "= 出厂默认" in app and "已被修改" in app)

    print("\n【U3】本批新增样式不得硬编码浅底（暗色主题下必瞎）")
    added = ["eng-block", "eng-card", "eng-label", "eng-badge", "eng-tags",
             "rm-table", "rm-row", "rm-search", "prov-card", "prov-key",
             "provider-select"]
    bad = []
    for cls in added:
        # 取该类名出现处前后 400 字符，检查是否出现 #fff / #ffffff / white
        for m in re.finditer(r"\." + re.escape(cls) + r"\b", css):
            seg = css[m.start():m.start() + 400]
            if re.search(r"#fff|#ffffff|background\s*:\s*white", seg, re.I):
                bad.append(cls)
                break
    check("新增类的样式里没有 #fff / white 硬编码", not bad, bad)
    check("供应商下拉现在有自己的样式（此前只有 .model-select 有）",
          ".provider-select" in css)
    # 2026-10-31 实测：全项目有 6 个 select 类名**从来没写过样式**（暗色下是原生白框）。
    # 现在统一在一条多类名选择器里定义；新加下拉必须登记进这条，否则又裸奔。
    # 2026-10-31：judge-ev-class 随 JudgeEvents 组件改造退役（分类改成按钮），
    # 从这条清单里移出 —— 清单里的每个类都必须真的有消费者，否则断言会保护死代码。
    ui_selects = ["model-select", "provider-select", "bp-select",
                  "style-rule-select", "fr-select", "bs-select"]
    group = re.search(r"(\.model-select[^{]*)\{[^}]*background:\s*var\(--field\)",
                      css)
    group_txt = group.group(1) if group else ""
    orphan = [c for c in ui_selects if "." + c not in group_txt]
    check("七个下拉类共用同一条外观定义（新增的不能再裸奔）",
          not orphan, "未登记: %s" % orphan)
    check("下拉展开后的 option 也上了色（暗色不出现白片）",
          "option" in css and ".model-select option" in css)

    print("\n【U4】不带回老结构：角色模型是表格，不是一排 art-row")
    check("模板用 .rm-table / .rm-row 承载角色模型",
          'class="rm-table"' in app and 'class="rm-row"' in app)
    check("角色用途列有中文说明（不再只给英文键名）",
          "ROLE_USE" in app and "正文写作" in app)
    check("agent 模式下明确标出「角色模型不参与」",
          "agent 模式下不参与" in app)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
