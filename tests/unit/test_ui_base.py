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

    # U4：替换 `.mini` 时必须用 md 档，不许用 sm。
    # 为什么：`.mini` 是 30px，而 sm 档是 28px —— 换完之后同一行里按钮比输入框矮 2px。
    # 2026-11-27 实测踩过：写作约束 / 指南针 / 交接稿 / 质量趋势 四个面板全中，
    # e2e 当时"全绿"，因为没人量过高度。
    # 例外只有“纯按钮行”（对话框 footer 那种，不与输入控件同行）：列在白名单里并写明原因，
    # 新增例外要在这里补一行 —— 白名单是给“确实不需要 30px”的场合用的，不是给自己开绿灯。
    ALLOW_SM = {
        "App.vue": "UiDialog footer（取消 / 恢复并继续）—— 纯按钮行，不与输入控件同行",
    }
    sm_use = []
    for f in SRC.glob("*.vue"):
        if f.name in ALLOW_SM:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for _m in re.finditer(r'<UiButton[^>]*\bsize="sm"[^>]*>', text):
            sm_use.append(f.name)
    check("U4 除对话框 footer 外没有组件用 size=\"sm\"（会矮 2px）",
          not sm_use, sorted(set(sm_use)))

    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
