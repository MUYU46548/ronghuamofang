# -*- coding: utf-8 -*-
"""活一 自由模式 + 活二 Word 导入的真机视觉验收（Playwright，自包含）。

## 为什么单独一个用例

`e2e_ux_verify.py` 是既有功能的回归网，覆盖不到本轮新增的三处 UI：
快速运行的**工序勾选条**（单选下拉 → 勾选集合）、**常用组合**存取、
章节页的 **Word 导入折叠块**（预检 → 确认两段式）。
按钮"看着有、点了没反应"正是本项目栽过的翻车点，所以新按钮必须真点一次。

覆盖（对应规格验收 1/2/4/6 的界面侧）：
- 勾选条真的能勾/取消、预估按所选数量求和、按钮禁用条件正确
- 常用组合：起名保存 → 下拉回填 → 勾选恢复
- 勾「缺上游」的组合跑 → 后端 409 → 界面把三要素报缺摊出来（不跑一半）
- 导入：空路径禁用、坏路径报人话、真 docx 预检**不落盘**、确认后真落盘 + 显示回退点

## 自带夹具与服务

复用 `e2e_new_features` 的做法：临时项目根 + 动态空闲端口 + 起完回读
`/state` 校验 `project_dir` 就是本夹具（端口通 ≠ 读的是你这个项目）。
用户在用的 8765 一律不碰。缺 playwright 时干净 SKIP（0 退出）。

用法：python tests/e2e/e2e_free_mode_import.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# 见 e2e_new_features 头注：这里只能 reconfigure，重包一层会在 import
# e2e_ux_verify 之后被 GC 连带关掉共享 buffer → 后续 print 全炸。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "free"

SITE = ""
API = ""
PASS, FAIL = [], []
SPAWNED = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  -> " + str(detail)[:220] if detail else ""))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn(args):
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SPAWNED.append(p)
    return p


def _wait_port(proc, port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return True
        except OSError:
            time.sleep(0.25)
    return False


def _force_agent_mode_off(system_yaml: Path):
    """把临时根的 gates.agent_mode 改 false（按行定向替换，保留注释）。

    **必须做**：本机 config/system.yaml 是 `agent_mode: true`，照抄会让新端点
    `/import/docx`（在 FORBIDDEN_IN_AGENT_MODE 里）对验收环境直接 403 ——
    而且报错文案里恰好含 "docx"，会撞出**假绿**断言（本用例第一版就这么绿过一次）。
    真机 Electron GUI 带 `X-Mofang-Token` 是放行的，关掉只影响这个无 token 的浏览器环境。
    与 tests/http/* 的 `_force_agent_mode_off` 同一做法。
    """
    p = Path(system_yaml)
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: false   # 验收环境强制标准模式")
            replaced = True
        else:
            out.append(ln)
    if replaced:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")


def make_fixture():
    """临时项目根：干净工作区（什么产物都没有）+ 一份可导入的 docx。"""
    from docx import Document
    root = Path(tempfile.mkdtemp(prefix="nf_free_fixture_"))
    (root / "config").mkdir(parents=True, exist_ok=True)
    sys_yaml = ROOT / "config" / "system.yaml"
    if sys_yaml.exists():
        shutil.copy(sys_yaml, root / "config" / "system.yaml")
        _force_agent_mode_off(root / "config" / "system.yaml")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 自由模式验收书\n  type: 科幻\n  chapters: 3\n", encoding="utf-8")

    docx = root / "验收稿.docx"
    doc = Document()
    for i in range(1, 4):
        doc.add_paragraph("第%d章 风起" % i)
        doc.add_paragraph("正文第%d段：夜色压城，码头的灯一盏盏熄了。" % i)
    doc.save(str(docx))

    # 坏路径用例：故意指向不存在的文件
    (root / "bad_path.txt").write_text("not a docx", encoding="utf-8")
    return root


def ensure_services(fixture):
    global SITE, API
    dist = ROOT / "console" / "renderer" / "dist"
    if not (dist / "index.html").exists():
        return "", "", "renderer/dist 未构建（先跑 npm run build）"

    site_port = _free_port()
    site_proc = _spawn([sys.executable, "-m", "http.server", str(site_port),
                        "--bind", "127.0.0.1", "--directory", str(dist)])
    if not _wait_port(site_proc, site_port):
        return "", "", "静态服务（%d）起不来" % site_port
    site = "http://127.0.0.1:%d" % site_port

    api_port = _free_port()
    api = "http://127.0.0.1:%d" % api_port
    proc = _spawn([sys.executable, str(ROOT / "scripts" / "nf_api.py"),
                   "--port", str(api_port), "--root", str(fixture)])
    if not _wait_port(proc, api_port):
        return "", "", "后端（%d）起不来" % api_port

    # 回读校验：必须 json 解析后按路径比（反斜杠转义会让子串比较恒假）
    last = ""
    for _ in range(40):
        if proc.poll() is not None:
            return "", "", "后端进程已退出（code=%s）" % proc.returncode
        try:
            raw = urllib.request.urlopen(api + "/state", timeout=3).read().decode("utf-8")
        except Exception:                                    # noqa: BLE001
            time.sleep(0.3)
            continue
        try:
            pd = str(json.loads(raw).get("project_dir") or "")
        except Exception:                                    # noqa: BLE001
            pd = ""
        last = pd or raw
        if pd and Path(pd).resolve() == Path(fixture).resolve():
            SITE, API = site, api
            return site, api, ""
        time.sleep(0.3)
    return "", "", "后端 project_dir 不是本夹具: %s" % last[:200]


def _toast(page):
    """读当前 toast 文案（say() 的落点）。"""
    try:
        t = page.locator(".toast, .tip, #toast").first
        if t.count():
            return t.inner_text()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        return page.evaluate(
            "() => { const el = document.querySelector('.toast');"
            " return el ? el.textContent : ''; }") or ""
    except Exception:                                        # noqa: BLE001
        return ""


def main():
    try:
        from playwright.sync_api import sync_playwright            # noqa: F401
    except ImportError:
        print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收，不是离线用例。")
        return 0

    sys.path.insert(0, str(ROOT / "tests" / "e2e"))
    fixture = make_fixture()
    site, api, err = ensure_services(fixture)
    if err:
        print("[SKIP] %s" % err)
        shutil.rmtree(str(fixture), ignore_errors=True)
        return 0

    import e2e_ux_verify as ux        # 导入时会重包 sys.stdout（别再叠一层）
    ux.SITE = site
    ws = Path(fixture)

    print("=" * 64)
    print("  自由模式 + Word 导入 真机验收")
    print("  site = %s   api = %s" % (site, api))
    print("  fixture =", ws)
    print("=" * 64)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx, page, errs, _bad = ux.new_page(browser, api, "free")
        try:
            # ================= A. 活一 自由模式 =================
            print("\n[A] 快速运行：单选下拉 → 工序勾选条")
            page.locator(".tabs button:has-text('流水线')").click()
            page.wait_for_timeout(900)
            q = page.locator(".qstage")
            n = q.count()
            check("勾选条渲染出全部工序", n >= 7, n)
            check("不再有旧的单选下拉",
                  page.locator(".flow-quick select.model-select:not(.combo-sel)")
                        .count() == 0)

            def clear_quick():
                """清空勾选。首屏会**自动预选**「下一个未完成阶段」（既有行为），
                不先清干净，后面的计数断言全是浮的 —— 这里把它当基线处理。"""
                for _ in range(12):
                    if page.locator(".qstage.on").count() == 0:
                        return
                    page.locator(".qstage.on").first.click()
                    page.wait_for_timeout(150)

            btn3 = q.nth(2)      # 3·逐章大纲（stageList 顺序 1..7）
            btn3.click()
            page.wait_for_timeout(300)
            check("点击即勾选（带 on 态）", "on" in (btn3.get_attribute("class") or ""))
            btn3.click()
            page.wait_for_timeout(300)
            check("再点取消勾选", "on" not in (btn3.get_attribute("class") or ""))

            clear_quick()
            check("清空后无勾选（含自动预选那一格）",
                  page.locator(".qstage.on").count() == 0,
                  page.locator(".qstage.on").count())

            q.nth(2).click()      # 3
            q.nth(4).click()      # 5
            page.wait_for_timeout(1400)
            cnt = page.locator(".qstage.on").count()
            check("勾了 2 件", cnt == 2, cnt)
            est = page.locator(".flow-est").inner_text()
            check("预估按所选数量求和（显示件数）", "%d 件" % cnt in est, est[:140])
            run_btn = page.locator("button:has-text('运行所选工序')")
            check("勾选后运行按钮可用", run_btn.count() == 1 and not run_btn.is_disabled())
            # 几何断言（截图之外的客观层）：行内不横向溢出、主按钮真渲染出尺寸
            geo = page.evaluate("""() => {
              const el = document.querySelector('.flow-quick');
              const btn = [...document.querySelectorAll('button')]
                .find(b => (b.textContent || '').includes('运行所选工序'));
              const r = btn ? btn.getBoundingClientRect() : null;
              return { overflow: el ? (el.scrollWidth - el.clientWidth) : -1,
                       w: r ? Math.round(r.width) : 0,
                       h: r ? Math.round(r.height) : 0 };
            }""")
            check("快速运行容器无横向溢出（换行而非挤出边界）",
                  geo["overflow"] <= 2, geo)
            check("主按钮「运行所选工序」渲染出真实尺寸",
                  geo["w"] > 40 and geo["h"] > 16, geo)
            run_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            ux.shot(page, "FREE1_quickselect", full=True)

            # ---- 常用组合：存 → 下拉回填 ----
            print("\n[B] 常用组合：起名保存 + 一键回填")
            page.locator(".combo-name").fill("只做后段")
            page.locator("button:has-text('存组合')").click()
            page.wait_for_timeout(500)
            combo_opts = page.locator(".combo-sel option")
            names = [combo_opts.nth(i).inner_text() for i in range(combo_opts.count())]
            saved = [x for x in names if "只做后段" in x]
            check("组合已存进下拉", bool(saved), names)
            check("存的是当时那两格（3,5）",
                  bool(saved) and "3,5" in saved[0], saved)
            clear_quick()
            check("清空后无勾选", page.locator(".qstage.on").count() == 0,
                  page.locator(".qstage.on").count())
            page.locator(".combo-sel").select_option(label=saved[0])
            page.wait_for_timeout(700)
            check("选组合即回填勾选（2 格）",
                  page.locator(".qstage.on").count() == 2,
                  page.locator(".qstage.on").count())
            ux.shot(page, "FREE2_combo", full=True)

            # ---- 报缺：勾缺上游的组合跑 → 后端 409 → 界面摊出三要素 ----
            print("\n[C] 缺上游产物：执行前拦住 + 三要素上屏（不跑一半）")
            # 夹具是空工作区：3（需 stage2 的 global.md）与 5（需 stage4 的 raw）都缺
            run_btn.click()
            page.wait_for_timeout(1200)
            dlg = page.locator(".dialog", has_text="运行前预估")
            check("多选走运行前预估确认框", dlg.count() == 1, dlg.count())
            ux.shot(page, "FREE3_estimate_dialog")
            if dlg.count():
                dlg.locator("button.primary").first.click()
                page.wait_for_timeout(1500)
            toast = _toast(page)
            check("界面报「缺上游产物」", "缺上游产物" in toast, toast[:220])
            check("报缺带三要素之「建议先跑」", "建议先跑" in toast, toast[:220])
            check("没被误报成运行中", not page.locator(".pill.st-running").count(),
                  page.locator(".pill.st-running").count())
            ux.shot(page, "FREE4_preflight_blocked")

            # ================= D. 活二 Word 导入 =================
            print("\n[D] Word 导入：禁用条件 → 坏路径 → 预检不落盘 → 确认落盘")
            page.locator(".tabs button:has-text('章节')").click()
            page.wait_for_timeout(900)
            box = page.locator(".import-box")
            check("导入折叠块在场", box.count() == 1, box.count())
            check("摘要文案可读", "导入 Word 稿" in box.inner_text(), box.inner_text()[:80])
            # 折叠块默认收起（:open 绑定在有路径/预检结果时才展开）—— 先点开再交互
            check("默认收起（不喧宾夺主）", not box.locator(".import-row").is_visible())
            box.locator("summary").click()
            page.wait_for_timeout(400)
            check("点摘要能展开", box.locator(".import-row").is_visible())
            pv = box.locator("button:has-text('预检')")
            check("空路径时预检禁用", pv.is_disabled())
            ux.shot(page, "FREE5_import_empty")

            # 坏路径（不是 .docx）
            box.locator(".import-path").fill(str(ws / "bad_path.txt"))
            page.wait_for_timeout(300)
            check("给了路径后预检可用", not pv.is_disabled())
            pv.click()
            page.wait_for_timeout(1500)
            toast = _toast(page)
            check("非 .docx → 人话报错上屏", "预检未过" in toast and "docx" in toast,
                  toast[:220])
            # 反证：不许再被 Agent 守卫 403 顶回来（那条文案里也含 "docx"，
            # 只匹配 "docx" 会假绿 —— 第一版就是这么误判的）
            check("不是被 Agent 守卫拦的（agent_mode 已按验收环境关掉）",
                  "只允许用户本人操作" not in toast and "agent_mode" not in toast,
                  toast[:220])
            check("坏路径不落盘", not (ws / "data" / "chapters" / "raw").exists())

            # 真 docx：预检（不落盘）
            box.locator(".import-path").fill(str(ws / "验收稿.docx"))
            page.wait_for_timeout(300)
            pv.click()
            page.wait_for_timeout(2000)
            prev = box.locator(".import-preview")
            check("预检出切分清单", prev.count() == 1, prev.count())
            if prev.count():
                txt = prev.inner_text()
                check("清单含切分模式与章数", "切分模式" in txt and "3 章" in txt, txt[:180])
                check("清单明示确认前不落盘", "确认前一个文件都不会写" in txt, txt[:180])
            check("预检阶段**没有**落盘（红线2）",
                  not (ws / "data" / "chapters" / "raw").exists())
            ux.shot(page, "FREE6_import_preview")

            # 确认导入
            box.locator("button:has-text('确认导入')").click()
            page.wait_for_timeout(2500)
            toast = _toast(page)
            check("确认后导入成功提示", "导入成功" in toast, toast[:220])
            raw = ws / "data" / "chapters" / "raw"
            check("raw/ 三章真落盘",
                  raw.is_dir() and sorted(f.name for f in raw.glob("*.md"))
                  == ["01.md", "02.md", "03.md"],
                  sorted(f.name for f in raw.glob("*.md")) if raw.is_dir() else "无目录")
            res = box.locator(".import-result")
            check("结果区显示回退点（丙3 快照）",
                  res.count() == 1 and "回退点" in res.inner_text(),
                  res.inner_text()[:160] if res.count() else "无结果区")
            check("输入 docx 仍在（只读红线）", (ws / "验收稿.docx").exists())
            ux.shot(page, "FREE7_import_done")

            # Chromium 会把**任何**非 2xx fetch 记成 console error。本轮刻意制造了
            # 两类：409（缺上游产物，执行前拦截）与 400（非 .docx / 路径为空）——
            # 它们是判据本身，不是故障。所以分两层判：JS 错误零容忍，网络错误只放行
            # 这两个**明确的状态码**（宽匹配会把真正的 500/0 也放过去）。
            real_errs = [e for e in errs
                         if e.startswith("error:") and "Failed to load resource" not in e]
            check("无 JS/脚本错误", not real_errs, real_errs[:3])
            expected = ("status of 409", "status of 400")
            net_errs = [e for e in errs if "Failed to load resource" in e]
            unexpected = [e for e in net_errs if not any(c in e for c in expected)]
            check("网络错误只含刻意制造的 409/400", not unexpected,
                  unexpected[:3] or ["全部符合预期：%d 条" % len(net_errs)])
        finally:
            ctx.close()
            browser.close()

    print("\n" + "=" * 64)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("  截图: %s" % OUT)
    print("=" * 64)
    shutil.rmtree(str(ws), ignore_errors=True)
    return 1 if FAIL else 0


def cleanup():
    """只收本脚本自己拉起的进程（动态端口，不碰用户在用的 8765）。"""
    for p in SPAWNED:
        try:
            p.wait(timeout=5)
        except Exception:                                    # noqa: BLE001
            pass
        try:
            p.terminate()
        except Exception:                                    # noqa: BLE001
            pass
    for p in SPAWNED:
        if p.poll() is None and os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"],
                           capture_output=True, timeout=10)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        cleanup()
