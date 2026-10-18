# -*- coding: utf-8 -*-
"""「选写作方向」面板的专项验收（第 4 件 · 选项可视化的 GUI 层）。

用户反馈的原话是「竞品有选项可视化，分支样例方向直观可选」——「直观可选」的
核心是**三个方向并排可比**，所以下面这些断言是本脚本的重点：

- 分章页签工具栏出现「选写作方向」按钮（这是唯一的 GUI 入口）；
- 点开后面板渲染，且顶部说明含「不直接写稿」（红线要看得见）；
- 选章后面板显示章号（面板绑当前章，不串章）；
- 「生成方向样例」→ 走 job 轮询 → **3 张方向卡片并排出现**；
- 卡片有 label + why（方向名 + 适配说明，缺了就没法「直观」比）；
- 点卡片 → 编辑框预填该方向样例（可就地改）；
- 「提交为写作参照」→ 提交的是编辑框文本 → 提示走「审核」页签批准；
- 「这一章已选定的方向」区显示读回结果；
- 切章后面板自动收起（防看着 A 章的面板改 B 章）；
- 全程无 console error。

前置：静态服务 8091（serve console/renderer/dist）+ mock 8798 已起。
用法：python tests/e2e/e2e_branch_sample.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "branch_sample"
OUT.mkdir(parents=True, exist_ok=True)
SITE = "http://127.0.0.1:8091"
API = "http://127.0.0.1:8798"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context()
        ctx.add_init_script(f"window.__NF_API_BASE__ = '{API}';")
        ctx.add_init_script("""
        window.mofangAPI = {
          readPreview: async () => '',
          notifyGate: () => ({ ok: true }),
          updaterStatus: async () => ({ initialized: false, dev: true }),
        };
        window.confirm = () => true;
        """)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept())
        bad_urls = []
        page.on("response", lambda r: bad_urls.append(f"{r.status} {r.url}")
                if r.status >= 400 and "favicon" not in r.url else None)

        page.goto(SITE, wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 首启免责门：真实放行流（不注入 localStorage 绕过）
        if page.locator(".disclaimer-mask").count():
            page.locator(".disclaimer-body").evaluate(
                "el => { el.scrollTop = el.scrollHeight; }")
            page.wait_for_timeout(350)
            cb = page.locator(".disclaimer-check input")
            agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
            if cb.count() and agree.count():
                cb.first.check()
                page.wait_for_timeout(150)
                agree.first.click()
                page.wait_for_timeout(450)
        check("免责声明已放行", page.locator(".disclaimer-mask").count() == 0)

        # 切到分章页签
        page.locator("nav.tabs button", has_text="分章").first.click()
        page.wait_for_timeout(1500)

        # 选章前：编辑器不渲染（工具栏在 v-else 分支里，跟着 current 走）
        check("选章前编辑器不渲染（空态提示）",
              page.locator(".bp-empty-large").count() > 0)
        check("选章前工具栏入口不存在",
              page.locator(".bp-toolbar button", has_text="选写作方向").count() == 0)

        # 选一章（面板绑当前章号）
        items = page.locator(".bp-chapter-item")
        check("章节列表有条目（mock 喂了章大纲）", items.count() > 0, items.count())
        if items.count():
            items.first.click()
            page.wait_for_timeout(900)

        # 工具栏入口：选章后才出现
        open_btn = page.locator(".bp-toolbar button", has_text="选写作方向")
        check("工具栏有「选写作方向」入口", open_btn.count() > 0, open_btn.count())
        check("默认面板收起", page.locator(".bs-wrap").count() == 0)

        open_btn.first.click()
        page.wait_for_timeout(800)
        panel = page.locator(".bs-wrap")
        check("点开后面板渲染", panel.count() > 0)
        txt = panel.first.inner_text() if panel.count() else ""
        check("面板说明含「不直接写稿」", "不直接写稿" in txt)
        check("面板标题带当前章号", "选写作方向" in txt and "章" in txt, txt[:60])
        page.screenshot(path=str(OUT / "S1_panel.png"), full_page=True)

        # 已选定方向（读回）
        existing = panel.locator(".bs-existing .bs-ex-item")
        check("「已选定的方向」区有读回结果", existing.count() > 0, existing.count())

        # 生成方向样例
        panel.locator("textarea").first.fill("这章气氛要压下来，别写得太热闹")
        panel.locator("button", has_text="生成方向样例").first.click()
        # job 轮询 2s 间隔，mock 立即返回 ok —— 给足时间
        page.wait_for_timeout(6000)
        cards = panel.locator(".bs-card")
        check("生成 3 张方向卡片", cards.count() == 3, cards.count())
        page.screenshot(path=str(OUT / "S2_cards.png"), full_page=True)

        if cards.count() == 3:
            # 「直观可选」的前提：三张卡片各自有方向名 + 适配说明
            nos = [cards.nth(i).locator(".bs-card-no").first.inner_text()
                   for i in range(3)]
            whys = [cards.nth(i).locator(".bs-card-why").first.inner_text()
                    for i in range(3)]
            check("三张卡片各有方向名", all(n.strip() for n in nos), nos)
            check("方向名互不相同（是分叉，不是同义反复）",
                  len(set(nos)) == 3, nos)
            check("三张卡片各有适配说明", all(w.strip() for w in whys), whys)

            # 并排布局：三张卡片的 top 应基本对齐（横向排 = 同一行）
            boxes = [cards.nth(i).bounding_box() for i in range(3)]
            if all(boxes):
                tops = [b["y"] for b in boxes]
                check("三张卡片并排（同一批，非纵向堆叠）",
                      max(tops) - min(tops) < 8, tops)
                check("卡片宽度合理（不是被挤成一条）",
                      min(b["width"] for b in boxes) > 200,
                      [round(b["width"]) for b in boxes])

            # 点第一张 → 编辑框预填
            first_text = cards.first.locator(".bs-card-text").first.inner_text()
            cards.first.click()
            page.wait_for_timeout(400)
            edit = panel.locator(".bs-edit textarea").first
            check("点卡片后编辑框预填该方向样例",
                  edit.input_value() == first_text, (edit.input_value() or "")[:40])
            check("选中卡片高亮", "on" in (cards.first.get_attribute("class") or ""))

            # 就地改 → 提交的应是改后的文本
            edited = first_text + "（我改过的版本）"
            edit.fill(edited)
            page.wait_for_timeout(200)
            submit_btn = panel.locator("button", has_text="提交为写作参照")
            check("编辑后提交按钮可用", not submit_btn.first.is_disabled())
            submit_btn.first.click()
            page.wait_for_timeout(1500)

            toast = panel.locator(".bs-toast")
            t = toast.first.inner_text() if toast.count() else ""
            check("提交后提示走「审核」页签批准", "审核" in t and "批准" in t, t[:80])
            check("提交后清空方向卡（等下一轮）", cards.count() == 0)

        # 空文本护栏：再生成 → 清空编辑框 → 提交禁用
        panel.locator("button", has_text="生成方向样例").first.click()
        page.wait_for_timeout(6000)
        if panel.locator(".bs-card").count():
            panel.locator(".bs-edit textarea").first.fill("")
            page.wait_for_timeout(200)
            check("编辑框清空后提交按钮禁用",
                  panel.locator("button", has_text="提交为写作参照").first.is_disabled())

        # 切章自动收起
        if page.locator(".bp-chapter-item").count() > 1:
            page.locator(".bp-chapter-item").nth(1).click()
            page.wait_for_timeout(900)
            check("切章后面板自动收起", page.locator(".bs-wrap").count() == 0)

        # 全程无 console error
        fatal = [e for e in errors if "favicon" not in e and "Download the Vue" not in e]
        check("全程无 console error", not fatal, (fatal[:2], bad_urls[:4]))

        page.screenshot(path=str(OUT / "S3_after.png"), full_page=True)
        browser.close()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
