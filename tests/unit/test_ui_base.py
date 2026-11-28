# -*- coding: utf-8 -*-
"""shadcn-vue 基座判据（2026-11-27，迁移第 1–3 步）。

只锁“接线 + 主题桥 + 试点组件”这一层，不要求 50 个组件已经迁完。
判据与方寸卡 `shadcn-vue-基座迁移q2b-已-be321d74` 对齐：
- `console/tailwind.config.js` 必须存在；
- preflight 必须关掉（旧 3k 行 style.css 视觉不变）；
- 主题 token 必须桥进 Tailwind；
- 试点组件必须存在且不新增 `<style scoped>`；
- 已迁组件必须真的被模板引用（不是建完就放着）。

用法：python tests/unit/test_ui_base.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "console"
SRC = CONSOLE / "src"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if detail else ""))


def main():
    print("=" * 66)
    print("shadcn-vue 基座（第 1–3 步）")
    print("=" * 66)

    cfg = CONSOLE / "tailwind.config.js"
    check("tailwind.config.js 存在", cfg.exists())
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    check("preflight 已关（旧 style.css 不被 reset）",
          re.search(r"preflight\s*:\s*false", text) is not None)
    check("content 覆盖 src/**/*.vue", "src/**/*.{vue,js}" in text)
    for token in ("--ink", "--muted", "--accent", "--card", "--border", "--ok", "--bad", "--warn"):
        check("主题 token 桥接 " + token, token in text)

    postcss = CONSOLE / "postcss.config.js"
    check("postcss.config.js 存在", postcss.exists())
    ptext = postcss.read_text(encoding="utf-8") if postcss.exists() else ""
    check("postcss 接了 tailwindcss + autoprefixer",
          "tailwindcss" in ptext and "autoprefixer" in ptext)

    style = (SRC / "style.css").read_text(encoding="utf-8")
    check("style.css 有 @tailwind utilities（工具类能生效）",
          "@tailwind utilities" in style)
    check("style.css 仍有 --content-max 唯一真源",
          style.count("--content-max:") == 1)

    cn = SRC / "lib" / "cn.js"
    check("cn.js（clsx + tailwind-merge）存在", cn.exists())

    for name in ("UiButton.vue", "UiDialog.vue", "UiEmptyState.vue"):
        p = SRC / "components" / "ui" / name
        check("试点组件 " + name + " 存在", p.exists())
        if p.exists():
            t = p.read_text(encoding="utf-8")
            check("试点组件 " + name + " 无 <style scoped>（Tailwind 承担样式）",
                  "<style" not in t)
            check("试点组件 " + name + " 用主题变量（不是裸色值）",
                  "var(--" in t)

    app = (SRC / "App.vue").read_text(encoding="utf-8")
    check("App.vue 真的引用了 UiButton", "UiButton" in app and "<UiButton" in app)
    check("App.vue 真的引用了 UiDialog", "<UiDialog" in app)
    ov = (SRC / "OutlineView.vue").read_text(encoding="utf-8")
    check("OutlineView.vue 真的引用了 UiEmptyState", "<UiEmptyState" in ov)

    # scoped 计数只认**真正的样式块**：正则必须落在 `</template>` 之后。
    # 老写法 `re.search(r"<style[^>]*scoped", 全文)` 会把**注释里提到这个标签**的文件也算进去 ——
    # 2026-11-27 实测：三个组件已把样式块整个摘掉，只因为注释写了“样式从 `<style scoped>` 迁到”
    # 就继续被计为 38，看上去一个组件都没迁成。判据统计的必须是样式块，不是这个字符串。
    SCOPED_RE = re.compile(r"</template>[\s\S]*?<style[^>]*\bscoped\b")
    scoped = 0
    for f in SRC.glob("*.vue"):
        if SCOPED_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
            scoped += 1
    check("根组件里 <style scoped> 数不超过迁移前实测基线 35（只许降）",
          scoped <= 35, scoped)

    # ---- UiButton 的四档必须与 style.css 的 `.btn-*` 层同源 ----
    # 为什么单独钉：试点组件第一版把 primary 写成 `bg-[var(--accent)] text-white`，
    # 而作者 2026-11-25 定案是 `--accent-strong` 底 —— 十套主题实测 accent 配白字
    # 只有 2.23–3.47:1（能看见但看不清），strong 才够 4.60–4.71:1。
    # 退化的表现不是报错，是“主按钮看着像个灰按钮”，只有真读起来才觉得累。
    btn = (SRC / "components" / "ui" / "UiButton.vue").read_text(encoding="utf-8")
    check("U1 UiButton primary 用 --accent-strong（不是 --accent）",
          "bg-[var(--accent-strong)]" in btn and "bg-[var(--accent)] " not in btn)
    check("U1b UiButton primary 的字色走 --btn-primary-ink",
          "var(--btn-primary-ink)" in btn)
    check("U2 UiButton ghost 字色用 --muted（与 .btn-ghost 同源）",
          "text-[color:var(--muted)]" in btn)

    # md 高度必须等于 .mini / .btn 的 30px：同一行里输入框(30px)与按钮一高一矮，
    # 是第一眼就能看出来的“不齐”，但几何自检抓不到（它只看有没有出屏）。
    geo = {}
    for m in re.finditer(r"(?m)^\.(mini|btn)\s*\{([^}]*)\}", style):
        h = re.search(r"height:\s*([^;]+);", m.group(2))
        geo[m.group(1)] = h.group(1).strip() if h else None
    md = re.search(r'md:\s*"([^"]+)"', btn)
    md_cls = md.group(1) if md else ""
    check("U3 UiButton md 高度 = .mini 的 height（%s）" % geo.get("mini"),
          geo.get("mini") and ("h-[30px]" in md_cls or
                               ("h-%d" % (int(geo["mini"].replace("px", "")) // 4)) in md_cls),
          md_cls)

    # U4：替换 `.mini` 时必须用 md 档，也不许用 sm —— **全仓零例外**。
    # 为什么：`.mini` 是 30px，而 sm 档是 28px —— 换完之后同一行里按钮比输入框矮 2px。
    # 2026-11-27 实测踩过：写作约束 / 指南针 / 交接稿 / 质量趋势 四个面板全中，
    # e2e 当时"全绿"，因为没人量过高度。
    # 2026-11-28 作者拍板：**统一到 30px**，删掉对话框 footer 的白名单。
    # 理由（写在这里免得以后有人再开）：全仓 600+ 处 .mini 长期与新组件并存，
    # "只允许一个高度"是最省心的规矩；28 vs 30 的差别肉眼分辨不出来，
    # 少一档尺寸 = 少一条要记住的规矩。
    sm_use = []
    for f in SRC.glob("*.vue"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for _m in re.finditer(r'<UiButton[^>]*\bsize="sm"[^>]*>', text):
            sm_use.append(f.name)
    check("U4 全仓没有组件用 size=\"sm\"（唯一高度 30px，无例外）",
          not sm_use, sorted(set(sm_use)))

    # ---- 第二批基座（2026-11-28）：UiInput / UiTable / UiField / UiBadge ----
    # 为什么必须先把这四个补上再迁 B/C 档：现状是“每个面板各写一套输入框 / 表格 / 标签”，
    # 值已经分成 padding 5px 与 6px、圆角 7px 与 8px 两档。不先给基座，迁一个面板就
    # 自创一套，等于把刚清理掉的问题重新种回去。
    for name in ("UiInput.vue", "UiTable.vue", "UiField.vue", "UiBadge.vue"):
        p = SRC / "components" / "ui" / name
        check("基座 " + name + " 存在", p.exists())
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8")
        # 只认真正的样式块：以**最后一个** `</template>` 为界，之后不许出现 <style。
        # 不能用第一个 —— 文件头的用法示例注释里就写着 `<template #cell-x>`，
        # 正则从那之后一直扫到注释里的 `<style scoped>` 就误判（本轮实测踩过）。
        check("基座 " + name + " 无 <style scoped>",
              "<style" not in t.rsplit("</template>", 1)[-1])
        check("基座 " + name + " 用主题变量（不是裸色值）", "var(--" in t)

    # U5：UiBadge 的几何必须与 .pill / .badge 一致。
    # 三个名字（.pill / .badge / UiBadge）是同一件东西 —— 12px 字号、同档圆角。
    # test_ui_tokens【10】已钉住前两者，这里把第三个也钉上，避免新徽标自成一套。
    badge = (SRC / "components" / "ui" / "UiBadge.vue").read_text(encoding="utf-8")
    pill_geo = {}
    for m in re.finditer(r"(?m)^\.(pill|badge|st-\w+)\s*\{([^}]*)\}", style):
        sel, body = m.group(1), m.group(2)
        r = re.search(r"border-radius:[ ]*([^;]+);", body)
        f = re.search(r"font-size:[ ]*([^;]+);", body)
        if r and sel in ("pill", "badge"):
            pill_geo[sel] = (r.group(1).strip(), f.group(1).strip() if f else None)
    check("U5a UiBadge 圆角 7px = .pill/.badge 的实测值",
          "rounded-[7px]" in badge and pill_geo.get("pill", ("",))[0] == "7px",
          pill_geo)
    check("U5b UiBadge 字号 12px = .pill/.badge 的实测值",
          "text-[12px]" in badge and pill_geo.get("badge", ("", ""))[1] == "12px",
          pill_geo)
    # 底色必须由主题色调出来，不许写死 rgba —— 换主题时底色要跟着走
    check("U5c UiBadge 底色走 color-mix(主题色, transparent)",
          "color-mix(in_srgb,var(--" in badge and not re.search(r"rgba?\(\s*\d", badge))

    # U6：UiTable 的单元格几何只有一处真源，且不许在组件里另写 padding 档。
    table = (SRC / "components" / "ui" / "UiTable.vue").read_text(encoding="utf-8")
    check("U6a UiTable 的单元格 padding 由 cellPad 单点决定",
          table.count("py-[5px]") == 1 and "cellPad" in table)
    check("U6b UiTable 不写 height/行高魔法数（对齐基础层）",
          not re.search(r"\bh-\[\d+px\]", table))

    # U7：UiInput 不许重新定义几何 —— 那会让 style.css 的基础层失效，
    # 同一屏里出现两档输入框高度（正是 test_ui_tokens【8】在治的病）。
    ui_input = (SRC / "components" / "ui" / "UiInput.vue").read_text(encoding="utf-8")
    for prop in ("padding", "border-radius", "height"):
        check("U7 UiInput 不重定义 " + prop + "（留给 :where() 基础层）",
              (" " + prop + ":") not in ui_input and (prop + "-") not in ui_input)

    # U8：输入类封装必须自己实现 v-model（2026-11-28 首版踩过，静默的）。
    # 首版把希望寄托在 `v-bind="attrs"` 上，结果是属性名变成 modelvalue=""，
    # 输入的值永远不回传 —— 界面不报错、保存时拿到空串，只有真点一遍才发现。
    # 判据两条：声明了 modelValue、且真的 emit 了 update:modelValue。
    check("U8a UiInput 声明 modelValue prop", "modelValue:" in ui_input)
    check("U8b UiInput emit update:modelValue",
          'emit("update:modelValue"' in ui_input)
    # U8c：textarea 里不许放子节点 —— Vue 编译器会把 slot 内容当**纯文本**渲染，
    # 界面上会直接出现字面的 <slot />（同一轮截图目检抓到）。
    # 只查 <template> 段：文件头注释里的反例（`<textarea><slot /></textarea>`）不算。
    tpl_part = ui_input.rsplit("</script>", 1)[-1]
    tm = re.search(r"<textarea[^>]*?(/>|>(.*?)</textarea>)", tpl_part, re.S)
    check("U8c UiInput 的 textarea 无子节点（放 slot 会变成字面文本）",
          tm is not None and (tm.group(1) == "/>" or not (tm.group(2) or "").strip()),
          tm.group(0)[:80] if tm else "没找到 textarea")

    # U9：基座组件必须真的有调用点 —— 建完就放着等于白建，而且下一次有人会再造一个。
    # 这条也会逼着“先补基座”这个决定落地：补了就得用，用了才算补对了。
    importers = {}
    for comp in ("UiInput", "UiTable", "UiField", "UiBadge"):
        hits = []
        for f in SRC.glob("*.vue"):
            if f.name.startswith("Ui"):
                continue
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if ("<%s" % comp) in txt:
                hits.append(f.name)
        importers[comp] = sorted(hits)
    unused = [k for k, v in importers.items() if not v]
    check("U9 四个新基座都有真实调用点（不是建完就放着）", not unused,
          {k: (v or "未被引用") for k, v in importers.items()})

    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
