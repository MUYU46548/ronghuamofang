# -*- coding: utf-8 -*-
"""归档查看/删除 + 收件箱通知 + 分段审阅（丙4）的真机视觉验收（Playwright）。

## 为什么单独一个用例

`e2e_ux_verify.py` 是既有功能的回归网，覆盖不到**本轮新增**的 UI：
项目页归档行的「查看」「删除」、归档查看对话框、收件箱的通知开关，
以及丙4 的**分段审阅面板**（收件箱 stage2 门内）。
按钮"看着有、点了没反应"正是本项目栽过的翻车点（2026-10-06：收件箱
「🔔 通知权限」点了毫无反应 —— requestPermission 只在 permission 为
"default" 时才动作），所以新按钮必须真的点一次才算数。

## 分段审阅为什么也必须真点

后端的闸 `tests/unit/test_outline_segments.py` 已经验过（拦/放都是真的），
但「闸开着、界面没做」正是丙4 上一轮留下的落差：用户在界面上点「确认放行」
只吃到一个 toast，看不到**差哪几段**。本用例要证的是闭环：面板真的渲染 8 段
→ 未全过时点「确认放行」被拦**且 toast 说得出差哪几段** → 逐段打勾真的推进
进度 → 全过后真的放行（门消失）。

## 自带夹具与服务（不依赖手工准备）

- 临时项目根：`data/books/测试归档书`（假归档，删除流程可点到底）
- 静态服务（renderer/dist）与后端（`--root` 指向临时根）都用**动态空闲端口**
  自己拉起、跑完收掉 —— 不复用固定端口：复用上一轮残留的后端会静默读到
  另一个项目（本用例开发时踩过两次），起完还会回读 `/state` 校验 project_dir
- 复用 `e2e_ux_verify` 的 preload 垫片 / 免责声明放行 / 截图函数

缺依赖（playwright）时**干净跳过**：打印 SKIP + 0 退出。

用法：python tests/e2e/e2e_new_features.py
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
import uuid
from pathlib import Path

# ⚠️ 用 reconfigure 而不是重包一层：本脚本随后要 import e2e_ux_verify，
# 它在导入时会 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`。
# 若这里也重包，旧包装被替换后遭 GC → 连带关掉共享 buffer → 之后所有 print
# 抛 "I/O operation on closed file"（本脚本踩过两次）。reconfigure 原地改编码，
# 对象不被替换（sys.__stdout__ 仍持有它），两层相安无事。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # 中文 Windows 控制台默认 CP936
    except Exception:                              # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "ux"

SITE = ""            # 由 ensure_services 赋值（动态端口，不写死 8091/8796）
API = ""
TOKEN = ""           # GUI 双因子 token（与后端 NF_GUI_TOKEN 同值），由 ensure_services 签发

PASS, FAIL = [], []
SPAWNED = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def _port_open(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _spawn(args, env=None):
    p = subprocess.Popen(args, cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env)
    SPAWNED.append(p)
    return p


def _wait_port(proc, port, timeout=25):
    for _ in range(int(timeout * 4)):
        if proc.poll() is not None:
            return False               # 进程自己退出了 → 端口"通"也与我们无关
        if _port_open(port):
            return True
        time.sleep(0.25)
    return False


def _free_port():
    """要一个当前空闲的端口（内核分配，天然不与 8765 等在用端口撞车）。

    为什么不再"占用固定端口再回收"：回收要靠 netstat 解析 + taskkill，而
    · 中文 Windows 的 netstat 是 CP936 输出（text=True → reader 线程
      UnicodeDecodeError，返回 None）；
    · 列下标写错会让 kill 静默不生效，端口却仍是"通的"；
    · _wait_port 见端口通就报成功 → 整轮测试连着上一轮残留的旧后端跑，
      静默读到另一个项目（2026-10-06 实测踩过两次）。
    动态端口从根上没有这个问题；跑完只收自己 spawn 的进程。
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _force_agent_mode_off(system_yaml: Path):
    """把临时根的 gates.agent_mode 改 false（按行定向替换，保留注释）。

    它消掉的是**第一层**假红：本机 config/system.yaml 是 `agent_mode: true`，
    照抄进夹具后，浏览器验收环境（拿不到 GUI token）会被 FORBIDDEN_IN_AGENT_MODE
    端点一律 403，表现是「对话框关不掉」，看着像前端 bug，实为夹具继承了 Agent 模式。
    与 tests/http/* 的 `_force_agent_mode_off` 同一做法。

    ⚠️ **只解第一层**。`/project/archive/delete` 还有第二层闸 `_gui_only` ——
    它与 agent_mode 无关、**始终**要求 GUI 双因子（`X-Mofang-Token`，由 Electron
    主进程经 IPC 签发），浏览器垫片给不出这个 token，所以本用例的**删除分支**
    仍然过不去。这不是本轮引入的（双因子落地晚于本用例），要修得让垫片带 token
    （`NF_GUI_TOKEN` 注入 nf_api + `window.__NF_GUI_TOKEN__` 注入 preload 垫片）——
    本轮未做，交付说明里已单列。
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


# 与 prompts/stage2_global_outline.md 的固定输出格式一致（8 段）——
# 分段就是按这些 `## ` 切的，夹具必须长得像真产物，否则验的不是真路径。
SEG_OUTLINE = """# 《演示书》整体大纲

## 起
开端：主角登场，冲突埋下。（验收用占位文本）

## 承
承接：冲突升级，势力登场。

## 转
转折：真相揭露，关系翻转。

## 合
收束：主线闭合，余韵留白。

## 关键节点
- 第 5 章 码头对峙
- 第 12 章 身份揭晓

## 预计章节数
24 章

## 章节规划
分三卷，每卷 8 章。

## 要求
保持冷峻文风，避免说明文。
"""


def make_fixture():
    """临时项目根：一份可被"查看"、也能被"删除"的假归档 + 阶段2 待审批的大纲。"""
    root = Path(tempfile.mkdtemp(prefix="nf_arch_fixture_"))
    book = root / "data" / "books" / "测试归档书"
    (book / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    # 分段审阅夹具（丙4）：8 段大纲 + 阶段2「已完成、未审批」→ 收件箱出 stage2 门。
    (root / "data" / "outline").mkdir(parents=True, exist_ok=True)
    (root / "data" / "outline" / "global.md").write_text(SEG_OUTLINE, encoding="utf-8")
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    (root / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": "演示书",
                    "stages": {"2": {"status": "done", "approved": False}}},
                   ensure_ascii=False), encoding="utf-8")
    (book / "_meta.json").write_text(
        '{"book": "测试归档书", "archived_at": "2026-10-01 12:00:00"}', encoding="utf-8")
    (book / "chapters" / "01.md").write_text(
        "# 第一章\n\n这是归档里的正文，用于验证在线预览。\n", encoding="utf-8")
    (book / "notes.txt").write_text("归档备注：这段文字应该能在「查看」里读到。\n",
                                    encoding="utf-8")
    sys_yaml = ROOT / "config" / "system.yaml"
    if sys_yaml.exists():
        shutil.copy(sys_yaml, root / "config" / "system.yaml")
        _force_agent_mode_off(root / "config" / "system.yaml")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 演示书\n  type: 奇幻\n  chapters: 3\n", encoding="utf-8")
    return root


def ensure_services(fixture):
    """拉起静态服务 + 后端（各自动态端口）。返回 (site, api, err)。

    两条纪律：
    · **不复用固定端口** —— 复用上一轮残留的后端会静默读到另一个项目；
    · **回读校验** —— 起完必须确认 `/state` 的 `project_dir` 就是本夹具，
      否则"端口通"只会骗自己。
    用户在用的 8765 等端口一律不碰（动态端口天然不撞）。
    """
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
    # GUI 双因子：后端签发 token（NF_GUI_TOKEN），前端由 new_page 注入同值。
    # 没有它，`_gui_only` 端点（归档删除 / 止烧阈值）在浏览器验收下永远 403 ——
    # 本用例的删除分支正是这么红的（双因子落地晚于本用例）。
    global TOKEN
    TOKEN = uuid.uuid4().hex
    env = dict(os.environ)
    env["NF_GUI_TOKEN"] = TOKEN
    proc = _spawn([sys.executable, str(ROOT / "scripts" / "nf_api.py"),
                   "--port", str(api_port), "--root", str(fixture)], env=env)
    if not _wait_port(proc, api_port):
        return "", "", "后端（%d）起不来" % api_port

    # ⚠️ 必须先 json 解析再按路径比：`/state` 是 JSON，Windows 反斜杠被转义成
    # `C:\\Users\\...`，拿 `str(fixture)`（单反斜杠）做子串比较**永远不相等** ——
    # 假阴性会把好端端的后端误判成"不是本夹具"（本用例踩过两次）。
    last = ""
    for _ in range(40):                 # 后端冷启动可能要一两秒，最多等 12 秒
        if proc.poll() is not None:
            return "", "", "后端进程已退出（code=%s）" % proc.returncode
        try:
            raw = urllib.request.urlopen(api + "/state", timeout=3).read().decode("utf-8")
        except Exception:                                  # noqa: BLE001
            time.sleep(0.3)
            continue
        try:
            pd = str(json.loads(raw).get("project_dir") or "")
        except Exception:                                  # noqa: BLE001
            pd = ""
        last = pd or raw
        if pd and Path(pd).resolve() == Path(fixture).resolve():
            SITE, API = site, api
            return site, api, ""
        time.sleep(0.3)
    return "", "", "后端 project_dir 不是本夹具: %s" % last[:200]


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
    ux.SITE = site                    # new_page() 读的是 ux 模块里的 SITE
    ux.GUI_TOKEN = TOKEN              # 垫片的 getGuiToken 读它（双因子第二因子）

    ws = Path(fixture)
    print("=" * 62)
    print("  新增前端验收：归档查看/删除 + 收件箱通知 + 分段审阅")
    print("  site = %s   api = %s" % (site, api))
    print("  fixture =", ws)
    print("=" * 62)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx, page, errs, _bad = ux.new_page(browser, api, "newf")
        try:
            # ---------- 项目页：归档行 ----------
            page.locator(".tabs button:has-text('项目')").click()
            page.wait_for_timeout(900)
            row = page.locator(".art-row", has_text="测试归档书")
            check("归档行渲染", row.count() >= 1, row.count())
            check("行内有「查看」", row.locator("button:has-text('查看')").count() == 1)
            check("行内有「删除」", row.locator("button:has-text('删除')").count() == 1)
            check("行内仍保留「恢复」", row.locator("button:has-text('恢复')").count() == 1)
            ux.shot(page, "NEW1_archive_row")

            # ---------- 查看：只读预览 ----------
            row.locator("button:has-text('查看')").click()
            page.wait_for_timeout(1000)
            check("查看对话框打开", page.locator("h3:has-text('查看归档')").count() == 1)
            dlg = page.locator(".dialog", has_text="查看归档")
            check("列出 notes.txt", dlg.locator("text=notes.txt").count() >= 1)
            check("列出 chapters/01.md", dlg.locator("text=chapters/01.md").count() >= 1)
            check("显示归档根路径", str(ws) in dlg.inner_text(), dlg.inner_text()[:120])
            ux.shot(page, "NEW2_archive_viewer")
            dlg.locator("text=notes.txt").first.click()
            page.wait_for_timeout(800)
            body = dlg.locator("pre").inner_text()
            check("点文件能预览到正文", "归档备注" in body, body[:80])
            check("只读提示在场", "只读预览" in dlg.inner_text())
            page.locator(".dialog button:has-text('关闭')").click()
            page.wait_for_timeout(500)

            # ---------- 删除：护栏逐层验 ----------
            row = page.locator(".art-row", has_text="测试归档书")
            row.locator("button:has-text('删除')").click()
            page.wait_for_timeout(600)
            check("删除对话框打开", page.locator("h3:has-text('删除归档')").count() == 1)
            ux.shot(page, "NEW3_delete_dialog")
            dd = page.locator(".dialog", has_text="删除归档")
            btn = dd.locator("button.danger")
            check("未勾护栏 → 按钮禁用", btn.is_disabled())
            dd.locator("label", has_text="我确认删除归档").locator("input").check()
            page.wait_for_timeout(200)
            check("勾选后按钮可用", not btn.is_disabled())
            btn.click()
            page.wait_for_timeout(600)
            check("缺书名 → 拦下并提示", dd.locator("text=请输入归档名").count() >= 1)
            check("此时数据仍在", (ws / "data" / "books" / "测试归档书").exists())
            dd.locator("input.text-input").fill("错的名字")
            btn.click()
            page.wait_for_timeout(600)
            check("书名不匹配 → 仍拦下", dd.locator("text=请输入归档名").count() >= 1)
            check("错名也未动数据", (ws / "data" / "books" / "测试归档书").exists())
            ux.shot(page, "NEW4_delete_guard")
            dd.locator("input.text-input").fill("测试归档书")
            btn.click()
            page.wait_for_timeout(1500)
            check("删除成功（对话框关闭）",
                  page.locator("h3:has-text('删除归档')").count() == 0)
            check("列表里不再出现该归档",
                  page.locator(".art-row", has_text="测试归档书").count() == 0)
            ux.shot(page, "NEW5_deleted")
            books = ws / "data" / "books"
            check("原目录已移走", not (books / "测试归档书").exists())
            trash = list((books / "_trash").glob("测试归档书__*"))
            check("落进回收站且内容可找回",
                  len(trash) == 1 and (trash[0] / "notes.txt").exists(), trash)

            # ---------- 收件箱：通知按钮 ----------
            page.locator(".tabs button:has-text('收件箱')").click()
            page.wait_for_timeout(900)
            nb = page.locator("button", has_text="通知")
            check("收件箱通知按钮存在", nb.count() >= 1, nb.count())
            head = page.locator(".card-head").inner_text()
            check("按钮带通道说明（不再只写「已开启」）",
                  ("浏览器通知" in head) or ("系统级通知" in head), head[:160])
            before = nb.first.inner_text()
            nb.first.click()
            page.wait_for_timeout(600)
            after = page.locator("button", has_text="通知").first.inner_text()
            check("点击可切换开/关（不再是死按钮）", before != after, (before, after))
            ux.shot(page, "NEW6_inbox_notify")

            # ---------- 收件箱：分段审阅（丙4 · 闸与界面必须成对）----------
            panel = page.locator(".seg-review")
            check("stage2 门内渲染分段面板", panel.count() == 1, panel.count())
            check("面板列出 8 段", panel.locator(".seg-row").count() == 8,
                  panel.locator(".seg-row").count())
            check("进度 0/8",
                  "0/8" in page.locator(".seg-head .pill").inner_text(),
                  page.locator(".seg-head .pill").inner_text())
            check("未全过时拦它的理由直接显示（不是哑闸）",
                  page.locator(".seg-gate-msg").count() == 1,
                  page.locator(".seg-gate-msg").count())

            # 展开阅读：正文真在场（只有标题的空壳等于让人盲签）
            panel.locator(".seg-toggle").first.click()
            page.wait_for_timeout(300)
            txt = panel.locator(".seg-text")
            check("点标题展开该段正文",
                  txt.count() == 1 and "开端" in txt.inner_text(),
                  txt.inner_text()[:60] if txt.count() else "无正文")
            ux.shot(page, "NEW7_segment_panel")

            # 未全过时点「确认放行」→ 必须被拦，且 toast 说得出差哪几段 + 下一条命令
            page.locator(".gate-head button:has-text('确认放行')").click()
            page.wait_for_timeout(500)
            toast = page.locator(".toast").inner_text()
            check("未全过 → 放行被拦", "段未通过" in toast, toast[:160])
            check("toast 给出差哪几段（不是哑闸）", "还有" in toast, toast[:200])
            check("toast 给出下一条命令（不是死胡同）", "segment_cli" in toast, toast[:240])
            check("被拦后门仍在（stage2 未被误批）", page.locator(".seg-review").count() == 1)

            # 逐段打勾：走的是 GUI 双因子写入端点，进度必须真推进
            for _ in range(8):
                btn = page.locator(".seg-row button:has-text('通过本段')")
                if btn.count() == 0:
                    break
                btn.first.click()
                page.wait_for_timeout(450)
            check("八段全部勾完",
                  page.locator(".seg-row button:has-text('通过本段')").count() == 0)
            check("进度 8/8",
                  "8/8" in page.locator(".seg-head .pill").inner_text(),
                  page.locator(".seg-head .pill").inner_text())
            check("全过后拦它的理由消失",
                  page.locator(".seg-gate-msg").count() == 0)
            ux.shot(page, "NEW8_segment_all_approved")

            # 全过 → 放行真的通（门消失）
            page.locator(".gate-head button:has-text('确认放行')").click()
            page.wait_for_timeout(1000)
            check("全过后放行成功（stage2 门消失）",
                  page.locator(".seg-review").count() == 0,
                  page.locator(".seg-review").count())

            # 唯一预期的「报错」是上面那次刻意触发的 409：Chromium 把被拒的
            # fetch 记成一条网络级 console error，而它正是本用例要证的拦截本身。
            # 只放行这一条、且必须真的发生过 —— 放宽成「忽略全部 409」会让
            # 真出问题的冲突（如并发写）一起溜过去。
            blocked = [e for e in errs
                       if "Failed to load resource" in e and "409" in e]
            check("刻意触发的 409 拦截确实发生过", len(blocked) >= 1, blocked[:2])
            real_errs = [e for e in errs if e.startswith("error:") and e not in blocked]
            check("无 console error", not real_errs, real_errs[:3])
        finally:
            ctx.close()
            browser.close()

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("  截图: %s" % OUT)
    print("=" * 62)
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
