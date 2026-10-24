# -*- coding: utf-8 -*-
"""设定集编辑器 + Agent 权限面板的真机视觉自查（2026-10-24）。

用户点名「该截图自查的自己查，别连界面长什么都不知道，哪里丑都看不出来」。
本脚本只干四件事，全部断言到 DOM 文本，不靠肉眼猜：

  1. Story Bible 世界观 tab 不再恒空 —— 要渲染出 mock 喂的「青石站」+「停了的怀表」
     （旧前端读 world.all，而真书的 world 下没有 all 键 → 恒显示"暂无条目"）；
  2. 角色编辑器要能读到真实字段 traits/relations/experience（旧编辑器只认
     snippet/description，用户在 Story Bible 里改不了任何实质内容）；
  3. 世界观条目编辑器要读得到 detail/locked_facts；
  4. Agent 权限面板要渲染两列 + 运行锁说明，且**没有**触发"界面在说假话"的红条
     （后端真源与界面承诺一致时不该出现）。

依赖 e2e_ux_verify.py 的同款垫片（preload shim + 免责声明放行）。
用法：先起 8091 静态站 + 8798 mock，再 python tests/e2e/e2e_setting_agent_shot.py
"""
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_ux_verify import (  # noqa: E402
    PRELOAD_SHIM_JS, SITE, MOCK_API, settle_disclaimer, shot, OUT,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def field_text(page):
    """抽屉内所有输入控件（textarea/input）的**真实值**拼接。

    ⚠️ 不能用 inner_text：它不含 <textarea>/<input> 的 value —— 第一版断言
    正是栽在这里，3 项误报 FAIL（探针证实值其实读到了）。占位符也算进去，
    这样"字段在不在界面上"和"有没有填上真值"都能被区分。"""
    # ⚠️ 别在 JS 里写 "\n"：Python 三引号会把它变成真实换行 → JS 语法错
    # （Page.evaluate: SyntaxError）。用 fromCharCode(10) 从根上避开转义。
    return page.evaluate("""() => [...document.querySelectorAll('.drawer textarea, .drawer input[type=text]')]
        .map(el => (el.value || '') + String.fromCharCode(10) + (el.placeholder || ''))
        .join(String.fromCharCode(10))""")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 940})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page)

        # --- 1. Story Bible 世界观 tab ---
        page.get_by_role("button", name="设定").first.click()
        page.wait_for_timeout(1200)
        # 子 tab：世界观
        page.get_by_role("button", name="世界观").first.click()
        page.wait_for_timeout(900)
        body = page.inner_text("body")
        check("世界观 tab 渲染出地点「青石站」", "青石站" in body,
              "" if "青石站" in body else "页面未见「青石站」")
        check("世界观 tab 渲染出物品「停了的怀表」", "停了的怀表" in body,
              "" if "停了的怀表" in body else "页面未见「停了的怀表」")
        check("不再显示「暂无条目」", "暂无条目" not in body,
              "" if "暂无条目" not in body else "世界观 tab 仍为空")
        p1 = shot(page, "setting_world_tab")

        # --- 2. 世界观条目编辑器：detail / locked_facts ---
        # 条目行是 .setting-row（带 @click）；名字与分组标签同行，用 get_by_text(exact)
        # 会因可访问名被合并而匹配不到（2026-10-24 实测超时），改用 CSS 类 + :has 文本。
        page.locator(".setting-row", has_text="青石站").first.click()
        page.wait_for_timeout(900)
        fields = field_text(page)
        check("世界观编辑器读到 detail（详述含「两站台」）",
              "两站台" in fields, "" if "两站台" in fields else "编辑器未读到 detail")
        check("世界观编辑器读到 locked_facts（含「二站台已封闭」）",
              "二站台已封闭" in fields,
              "" if "二站台已封闭" in fields else "编辑器未读到 locked_facts")
        p2 = shot(page, "setting_world_editor")
        # 关抽屉
        # 抽屉内定位关闭按钮：页面别处也可能有「关闭」，限定在 .drawer 里避免误点
        page.locator(".drawer").get_by_role("button", name="关闭").click()
        page.wait_for_timeout(600)

        # --- 3. 角色编辑器：traits / relations / experience ---
        page.get_by_role("button", name="角色").first.click()
        page.wait_for_timeout(900)
        page.locator(".setting-row", has_text="周砚清").first.click()
        page.wait_for_timeout(900)
        fields = field_text(page)
        check("角色编辑器读到 traits（含「认刻度不认人」）",
              "认刻度不认人" in fields,
              "" if "认刻度不认人" in fields else "编辑器未读到 traits")
        check("角色编辑器读到 relations（含「韩拙」）",
              "韩拙" in fields, "" if "韩拙" in fields else "编辑器未读到 relations")
        check("角色编辑器读到 experience（含「轨距偏差」）",
              "轨距偏差" in fields,
              "" if "轨距偏差" in fields else "编辑器未读到 experience")
        # records（2026-10-24 OB34 素材）：证词体多行原文必须显示，且空行分条
        # 后**内部换行不被切碎**（一条证词含多行，切碎 = 语气丢失）
        ta = page.locator(".drawer textarea").last.input_value()
        check("角色编辑器读到 records（含「那个小姑娘看上去很乖」）",
              "那个小姑娘看上去很乖" in ta,
              "" if "那个小姑娘看上去很乖" in ta else "编辑器未读到 records")
        check("records 内部换行保留（一条证词两行未被切碎）",
              "一首诗怎么就能变成安防指令。\n但自从他接手" in ta,
              repr(ta[:120]))
        check("records 两条用空行分隔（不是每行一条）",
              "。\n\n那个小姑娘" in ta or "。\n\n——" in ta or "\n\n" in ta,
              repr(ta[-120:]))
        p3 = shot(page, "setting_char_editor")
        page.locator(".drawer").get_by_role("button", name="关闭").click()
        page.wait_for_timeout(600)

        # --- 4. Agent 权限面板 ---
        page.get_by_role("button", name="流水线").first.click()
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="查看权限").first.click()
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        check("权限面板出现标题", "Agent 模式权限边界" in body)
        check("渲染出 forbidden 列标题", "Agent 不可代劳" in body)
        check("渲染出 internal 列标题", "Agent 可驱动" in body)
        check("forbidden 列含 /approve", "/approve" in body)
        check("internal 列含跑阶段", "/stage/{n}/run" in body)
        check("运行锁说明含「双跑」", "双跑" in body)
        # 后端真源与界面承诺一致 → 不该出现"说假话"红条
        check("未出现「界面在说假话」红条", "说假话" not in body,
              "" if "说假话" not in body else "后端名单与界面承诺不一致！")
        p4 = shot(page, "agent_permissions_panel")

        browser.close()

    print("\n截图：%s" % ", ".join(str(p) for p in (p1, p2, p3, p4)))
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
