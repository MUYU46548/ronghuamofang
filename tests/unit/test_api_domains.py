# -*- coding: utf-8 -*-
"""P2 拆分护栏自检：域模块契约 + 薄转发形态 + 依赖方向。

## 为什么需要它

`nf_api.py` 从 2,872 行往下拆，最容易出的三类事故**不会**被 HTTP 契约测试抓到：

1. **域模块反向依赖被写成值拷贝** —— 域模块里写 `from nf_api import ROOT`，
   测试把 `nf_api.ROOT` 指到临时项目根后，域模块还盯着旧目录 → 静默读写真实仓库。
2. **分支体在内联业务逻辑** —— 拆了一半点，新代码又直接写在 elif 里，
   于是「拆完了」只是自我感觉良好。
3. **转发忘了展开返回值** —— `self._send(dom_misc.f(self))`（漏 `*_dom(...)`），
   会把元组当成 payload 塞进 JSON → 响应形状全错，但 `_send` 不报错。

本脚本用 AST + 源码扫描把这三条变成可执行的判据。

用法：python tests/unit/test_api_domains.py
"""
import ast
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
API_PY = SCRIPTS / "nf_api.py"
DOM_DIR = SCRIPTS / "nf_api_domains"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


# --------------------------------------------------------------- 1. 包结构

def test_package():
    print("\n[1] 域模块包结构")
    check("nf_api_domains/ 存在", DOM_DIR.is_dir())
    if not DOM_DIR.is_dir():
        return
    check("__init__.py 存在", (DOM_DIR / "__init__.py").is_file())
    check("contract.py（协议定义）存在", (DOM_DIR / "contract.py").is_file())
    check("misc.py（试点域）存在", (DOM_DIR / "misc.py").is_file())

    # 每个域模块都要在 nf_api.py 里被 import（域模块没被接线 = 端点实际没拆）
    src = API_PY.read_text(encoding="utf-8")
    dom_files = sorted(p.stem for p in DOM_DIR.glob("*.py")
                       if p.stem not in ("__init__", "contract"))
    missing = [d for d in dom_files if ("import %s as dom_" % d) not in src]
    check("所有域模块都在 nf_api.py 里接线（无孤立模块）",
          not missing, missing)
    check("域模块数量 >= 6（拆分已实质推进）", len(dom_files) >= 6, dom_files)

    init_src = (DOM_DIR / "__init__.py").read_text(encoding="utf-8")
    check("__init__ 说明了「不可 from nf_api import ROOT」的纪律",
          "from nf_api import ROOT" in init_src and "属性访问" in init_src)

    contract = (DOM_DIR / "contract.py").read_text(encoding="utf-8")
    check("contract 定义了 STREAM_RESPONSES 哨兵",
          "STREAM_RESPONSES" in contract)
    check("contract 声明返回协议 (status, payload)",
          "Result = Tuple[int, Any]" in contract)


# --------------------------------------------------------------- 2. 依赖方向

def test_dependency_direction():
    """域模块**禁止**值拷贝 nf_api 的模块级名字。"""
    print("\n[2] 依赖方向：域模块必须经属性访问，不得值拷贝")
    bad = []
    for f in sorted(DOM_DIR.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "nf_api":
                # `from nf_api import X` 一律违规 —— ROOT 会被拷成死值，
                # 其它名字（函数）虽然当前无害，但同一模式早晚踩同一个坑。
                bad.append("%s:%d from nf_api import %s" % (
                    f.name, node.lineno,
                    ", ".join(a.name for a in node.names)))
    check("域模块无 `from nf_api import ...`（会值拷贝 ROOT，测试/安装态失效）",
          not bad, bad)

    # 至少要有一处 `import nf_api as api` 证明走的是属性访问
    ok_attr = False
    for f in sorted(DOM_DIR.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        if "import nf_api as api" in src and "api.ROOT" in src:
            ok_attr = True
    check("至少一处域模块用 `import nf_api as api` + `api.ROOT` 的属性访问",
          ok_attr)


# --------------------------------------------------------------- 3. handler 只 return

def test_handlers_only_return():
    """handler 不得调用 h._send（谁发响应只能有一个答案）。"""
    print("\n[3] handler 只能 return，不得自行 _send")
    offenders = []
    for f in sorted(DOM_DIR.glob("*.py")):
        if f.name == "contract.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("handle_")]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                # h._send(...) / self._send(...)
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr in ("_send", "_stream_sse"):
                    offenders.append("%s:%d %s() 内调 %s" % (
                        f.name, call.lineno, fn.name, func.attr))
    check("handle_* 内无 _send / _stream_sse 调用（响应统一由 nf_api 发出）",
          not offenders, offenders)


def test_handlers_no_double_body_read():
    """handler 不得再调 h._body()（请求体是一次性流，二次读会**挂死**）。

    2026-09-21 实测事故：`/sandbox/review` 的 handler 里写了 `body = h._body()`，
    而 `do_POST` 开头已经读过一次。HTTP 表现是「请求永远不返回、服务端日志
    一片空白」—— 不是 500，不是 400，就是静默挂起，极难定位。
    正确写法是签名收参：`handle_xxx(h, body)`，由 do_POST 传入已读的 dict。
    """
    print("\n[3b] handler 不得二次读请求体（会挂死在 rfile.read）")
    offenders = []
    for f in sorted(DOM_DIR.glob("*.py")):
        if f.name == "contract.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("handle_")]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr == "_body":
                    offenders.append("%s:%d %s() 内调 %s" % (
                        f.name, call.lineno, fn.name, func.attr))
    check("handle_* 内无 h._body() 调用（请求体由 do_POST 传入）",
          not offenders, offenders)
    # 反向：确认 do_POST 里确实是「先读一次、再把 body 传下去」
    src = API_PY.read_text(encoding="utf-8")
    check("do_POST 开头只读一次请求体", src.count("body = self._body()") == 1,
          src.count("body = self._body()"))
    check("新增的域模块已把 body 作为参数传入",
          "handle_sandbox_review(self, body)" in src)


# --------------------------------------------------------------- 4. 薄转发形态

def test_thin_forwarding():
    """被迁走的分支体应当是纯转发，不再内联业务实现。"""
    print("\n[4] 薄转发形态（do_GET 分支体只做转发）")
    src = API_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    get_node = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            for s in n.body:
                if isinstance(s, ast.FunctionDef) and s.name == "do_GET":
                    get_node = s
    check("do_GET 仍定义在 nf_api.Handler（契约测试锚点）", get_node is not None)
    if get_node is None:
        return

    # 收集 `self._send(*_dom(dom_xxx.handle_yyy(self)))` 形式的转发端点。
    # 注意：必须只遍历**顶层 elif 链**，不能用 ast.walk(get_node) —— 后者会递归
    # 进分支体内部的 if/except，把内层条件也当成「分支」，度量出来的行数毫无意义。
    def _top_chain(method_node):
        chain = []
        for stmt in method_node.body:
            if isinstance(stmt, ast.If):
                chain.append(stmt)
                cur = stmt
                while cur.orelse and isinstance(cur.orelse[0], ast.If):
                    cur = cur.orelse[0]
                    chain.append(cur)
        return chain

    chain = _top_chain(get_node)

    def _dom_in_body(node):
        """该分支**自身 body** 内是否出现 `_dom(...)`。

        必须只扫 `node.body` —— `ast.walk(node)` 会钻进 `orelse`，
        沿 elif 链把后续分支（乃至链尾）的 `_dom` 全算成本分支的，
        于是每一个分支都被判成「已转发且超长」。
        """
        for stmt in node.body:
            for c in ast.walk(stmt):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) \
                        and c.func.id == "_dom":
                    return True
        return False

    forwarded = [n.lineno for n in chain if _dom_in_body(n)]
    check("do_GET 内存在薄转发分支（`self._send(*_dom(...))`）",
          len(forwarded) >= 8, "找到 %d 处 / 链长 %d" % (len(forwarded), len(chain)))

    # 转发分支体必须极短 —— 判据只对**已经转发**的分支生效：
    # 拆分是分批进行的，尚未迁走的旧分支仍可能很长（那是待办，不是缺陷）；
    # 但「已经声明转发的分支里又长出实现」必须立刻报警，否则拆分会被写回去。
    # 注意：不能直接 `len(ast.unparse(node).splitlines())` —— unparse 一个 If
    # 会把 orelse（即后续整条 elif 链）一并打印，行数必然爆表。
    # 正确做法：**只量 body**（该分支自己的实现），用「行号跨度」估实际长度。
    def _body_lines(node):
        if not node.body:
            return 0
        last = max(getattr(s, "end_lineno", getattr(s, "lineno", 0)) for s in node.body)
        return max(1, last - node.body[0].lineno + 1)

    fat_forwards = []
    for n in chain:
        if not n.body:
            continue
        if _dom_in_body(n) and _body_lines(n) > 6:
            fat_forwards.append((n.lineno, _body_lines(n), ast.unparse(n.test)[:40]))
    check("已转发的分支体保持极短（<= 6 行；实现不得再长回分支里）",
          not fat_forwards, fat_forwards[:5])

    # 转发分支必须带 _dom 展开 —— 漏掉 * _dom 会把元组当 payload 塞进 JSON
    bad_send = []
    for node in chain:
        for stmt in node.body:
            for c in ast.walk(stmt):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) \
                        and c.func.attr == "_send" and c.args:
                    a0 = c.args[0]
                    if isinstance(a0, ast.Attribute) and a0.attr.startswith("handle_"):
                        bad_send.append(c.lineno)
    check("无 `self._send(dom.xxx(...))` 漏展开 `*_dom` 的写法",
          not bad_send, bad_send)


# --------------------------------------------------------------- 5. 部署面

def test_seed_covers_domain_dir():
    """打包/播种必须带上新目录 —— 否则用户升级后 nf_api 找不到域模块。"""
    print("\n[5] 播种 / 打包覆盖新目录")
    idx = ROOT / "console" / "main" / "index.js"
    check("console/main/index.js 存在", idx.is_file())
    if not idx.is_file():
        return
    js = idx.read_text(encoding="utf-8")
    # SEED_CODE_DIRS 决定升级时刷哪些目录
    m = None
    for line in js.splitlines():
        if "SEED_CODE_DIRS" in line and "=" in line:
            m = line
            break
    check("解析到 SEED_CODE_DIRS", m is not None, m)
    if m:
        check("SEED_CODE_DIRS 覆盖 scripts（nf_api_domains 随 scripts 走）",
              "scripts" in m, m.strip())
    check("index.js 未把 nf_api_domains 单独漏掉（若列举了 scripts 即自动包含）",
          "scripts" in js)


def test_main_alias_guard():
    """`python scripts/nf_api.py` 启动时，域模块看到的必须是**同一个** nf_api。

    这是本套件里**最重要**的一条。原因见下：

    以脚本方式启动时，本文件的模块名是 `__main__`；域模块里写
    `import nf_api as api` 会**再加载一份**，进程里就有两个 nf_api ——
    `__main__`（真正在处理请求的）和 `nf_api`（域模块看到的），
    两边 `JOBS` / `CURRENT` / `ROOT` 是彼此独立的对象。

    这个缺陷是**静默**的：从磁盘读文件的端点（/state、/costs/…）全正常，
    只有依赖进程内状态的端点会坏 —— `/jobs/{id}` 永远 404，前端轮询
    后台任务全部超时。更阴的是：直接 `import nf_api` 的单测**全绿**
    （只有一份模块），所以它只在真机 HTTP 下暴露。

    判据分两层：
    - 静态：源码里必须有 `sys.modules.setdefault("nf_api", sys.modules["__main__"])`
    - 动态：真的把脚本作为 `__main__` 执行两次导入，断言拿到同一个对象
    """
    print("\n[6] __main__ 别名守卫（防双份 nf_api 实例）")
    src = API_PY.read_text(encoding="utf-8")
    check("nf_api.py 里做了 `__main__` → `nf_api` 的模块别名",
          'sys.modules.setdefault("nf_api"' in src
          and 'sys.modules["__main__"]' in src,
          "缺这段时 /jobs/{id} 会永远 404（域模块看到的是另一份空 JOBS）")

    # 动态验证：在子进程里模拟 `python scripts/nf_api.py` 的导入语义。
    #
    # 注意用的是轻量替身而不是真加载 nf_api.py：真文件会连带 import
    # orchestrator / llm_client 等重模块（十几秒起，CI 上不可接受），
    # 而本判据要验的是**别名机制**是否生效，与文件内容无关。
    # 只把源码里那段守卫**原样执行一遍**，再加一句 `import nf_api` 看是否命中。
    import re
    import subprocess

    # 从真源码里抠出守卫那段（保证测的是实际写下的代码，不是本测试的复刻）
    guard_lines = [ln for ln in src.splitlines()
                   if "sys.modules.setdefault" in ln
                   or '__main__"' in ln and "sys.modules" in ln]
    check("守卫语句能从源码里抽出（供动态验证复用）",
          any("setdefault" in ln for ln in guard_lines), guard_lines[:2])

    probe = (
        "import sys, types\n"
        "m = types.ModuleType('__main__')\n"
        "m.JOBS = {'id1': 1}\n"
        "sys.modules['__main__'] = m\n"
        "sys.modules.pop('nf_api', None)\n"
        "__name__ = '__main__'\n"
        "%s\n"
        "import nf_api\n"
        "print('SAME' if nf_api is m else 'DIFF')\n"
        "print('JOBS_SAME' if nf_api.JOBS is m.JOBS else 'JOBS_DIFF')\n"
    ) % "\n".join(ln.strip() for ln in guard_lines)
    try:
        proc = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                              text=True, timeout=60)
        out = (proc.stdout or "").strip().splitlines()
        check("按源码里的守卫执行后 `import nf_api` 命中 __main__（同一模块对象）",
              "SAME" in out, out[:3] or (proc.stderr or "")[-300:])
        check("两份模块共享同一个 JOBS 字典（/jobs/{id} 才查得到）",
              "JOBS_SAME" in out, out[:3])
    except subprocess.TimeoutExpired:
        check("按源码里的守卫执行后 `import nf_api` 命中 __main__", False, "子进程超时")


def test_no_relative_path_io():
    """API 层不得用相对路径做 IO —— 必须经 `api.ROOT` 解析。

    这是 2026-09-20 修的**潜伏缺陷**：`/review/report` 等端点曾用
    `Path("data/outline/review_report.json")`（相对进程 CWD），
    而同族其它端点用 `api.ROOT / ...`。两种语义并存时：

        cwd = 书本A（有报告）   --root 书本B（无报告）
        → GET /review/report 返回 200，内容是**书本A**的数据

    生产（Electron）恰好没暴露它，因为主进程 `spawn(..., {cwd: ws})`
    且同时传 `--root ws`，CWD 与 ROOT 相等。但 `--root` 参数的存在
    本身就说明设计允许两者不同 —— 潜伏缺陷迟早会被踩到。

    判据：AST 找 `Path("data/...")` / `api.Path("logs/...")` 这类
    **字面量相对路径**。只查字面量，不误伤 `api.ROOT / "data" / x`。
    """
    print("\n[7] 无相对路径 IO（必须先经 ROOT 解析）")
    REL_HEADS = ("data/", "logs/", "output/", "config/", "prompts/",
                 "materials/", "templates/")
    offenders = []

    def _is_path_call(node):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "Path":
            return True
        return isinstance(f, ast.Attribute) and f.attr == "Path"

    targets = [API_PY] + sorted(DOM_DIR.glob("*.py"))
    for f in targets:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args or not _is_path_call(node):
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                v = a0.value.replace("\\", "/")
                if v.startswith(REL_HEADS):
                    offenders.append("%s:%d Path(%r)" % (f.name, node.lineno, a0.value))

    check("API 层无字面量相对路径 IO（防 --root 场景静默读错项目）",
          not offenders, offenders[:6])


def main():
    test_package()
    test_dependency_direction()
    test_handlers_only_return()
    test_handlers_no_double_body_read()
    test_thin_forwarding()
    test_seed_covers_domain_dir()
    test_main_alias_guard()
    test_no_relative_path_io()

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
