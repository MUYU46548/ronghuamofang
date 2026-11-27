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

    scoped = 0
    for f in SRC.glob("*.vue"):
        if re.search(r"<style[^>]*scoped", f.read_text(encoding="utf-8", errors="ignore")):
            scoped += 1
    check("根组件里 <style scoped> 数不超过迁移前实测基线 38（只许降）",
          scoped <= 38, scoped)

    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
