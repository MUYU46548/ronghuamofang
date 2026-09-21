# -*- coding: utf-8 -*-
"""GUI ↔ API 契约静态核对（按 **HTTP 方法** 核对，不只核路径）。

背景：本项目接连发现前端在调、后端对不上的端点，而且形态各异：
  - POST /models/switch        —— 文档里写了，do_POST 里根本没实现（404）
  - POST /export/markdown      —— 错写在 do_GET 里，函数体还引用了未定义的 body（500）
  - POST /models/add           —— 错写在 do_GET 里，GUI 走 POST 必 404
  - GET  /config/project       —— do_POST 里有个"读配置"分支，GUI 却用 GET（404）
  - POST /outline/chapters/save —— 丢了 `elif p == ...` 守卫，整段变成上一个分支的
                                    裸尾随代码（404 + 二次发送响应）
  - 暖橙主题 —— App.vue 有 theme-orange，style.css 里没有对应变量块（点了没反应）

只核路径不核方法会漏掉后两类，所以本脚本按 (method, path) 核对；任何"路径存在但方法不对"
会单独报出来，而不是被当成通过。

## 路由抽取为什么改成 AST（2026-09-19 第三轮）

原实现用正则从 `nf_api.py` 里**切源码文本**：

    m_get = re.search(r"def do_GET\\(self\\):(.*?)\\n    def do_POST", src, re.S)

这有两个问题：
  1. **脆弱**：缩进/空行一变就切歪，且一旦 `do_GET`/`do_POST` 被挪到别的文件，
     27 条断言会整体失绿 —— 等于用测试把 God Object 钉死在原地，与 P2 拆分目标冲突。
  2. **可被字符串骗过**：分支写进注释或字符串字面量里也会被 `findall` 抓到，
     于是"端点存在"可能是假绿。

现改为 `ast` 解析 `nf_api.py` 的 `Handler` 类，分别取：
  · `do_GET` / `do_POST` 两个**方法体内**（`ast.walk`）出现的 `p == "..."` /
    `p.startswith("...")` 比较 —— 只认真实代码；
  · 另有一条**结构性断言**：这两个方法必须仍定义在 `nf_api.py` 里（否则统一 API 层
    的测试锚点消失）；分支体被拆到 `nf_api_domains/*` 只改变实现位置，不影响契约。

用法：python tests/e2e/test_gui_api_contract.py
"""
import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
API_PY = ROOT / "scripts" / "nf_api.py"
VUE_DIR = ROOT / "console" / "src"
STYLE_CSS = ROOT / "console" / "src" / "style.css"
APP_VUE = ROOT / "console" / "src" / "App.vue"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:280]) if detail else ""))


# ---------------------------------------------------------------- 路由抽取（AST）

def _str_const(node):
    """取字符串字面量值；非字面量返回 None。"""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_p_name(node):
    """判断节点是否为 `p`。"""
    return isinstance(node, ast.Name) and node.id == "p"


def _has_p_compare(node):
    """判断表达式里是否出现 `p == "..."` / `p.startswith("...")` 这类路由判定。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Compare) and any(isinstance(op, ast.Eq) for op in n.ops) \
                and _is_p_name(n.left):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "startswith" and _is_p_name(n.func.value):
            return True
    return False


def _extract_from_body(body):
    """从一串语句里提取 `p == "x"` / `p.startswith("x")` / 模板端点。

    只在**真实表达式**里找，注释与字符串字面量天然不可能命中。
    """
    exact, prefixes, templated = set(), set(), set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        # p == "x"
        if isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], ast.Eq) and _is_p_name(node.left) \
                and len(node.comparators) == 1:
            v = _str_const(node.comparators[0])
            if v:
                exact.add(v)
        # p.startswith("x")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "startswith" and _is_p_name(node.func.value) \
                and node.args:
            v = _str_const(node.args[0])
            if v:
                prefixes.add(v)
        # p.startswith("/stage/") and p.endswith("/run")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            seg = [n for n in ast.walk(node)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            pre = [n for n in seg if n.func.attr == "startswith" and _is_p_name(n.func.value)]
            suf = [n for n in seg if n.func.attr == "endswith" and _is_p_name(n.func.value)]
            if pre and suf and pre[0].args and suf[0].args:
                a, b = _str_const(pre[0].args[0]), _str_const(suf[0].args[0])
                if a and b:
                    templated.add(a + "{n}" + b)
    return exact, prefixes, templated


def _handler_methods(tree):
    """找出 nf_api.py 里 Handler 类的 do_GET / do_POST 方法节点。"""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Handler":
            for sub in node.body if hasattr(node, "body") else []:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name in ("do_GET", "do_POST"):
                    found[sub.name] = sub
    return found


def server_routes():
    """按方法拆分路由表。"""
    src = API_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    methods = _handler_methods(tree)
    blocks = {}
    for name, m in (("GET", methods.get("do_GET")), ("POST", methods.get("do_POST"))):
        blocks[name] = _extract_from_body(m.body) if m else (set(), set(), set())
    return blocks, src, methods


def _branch_elifs(method_node):
    """该方法的**顶层分支链**里的 elif/if 语句（用于「丢失 elif 守卫」类回归）。

    只取 `if p ...: / elif p ...:` 这条链，不递归进嵌套块。
    """
    out = []
    if not method_node:
        return out
    for stmt in method_node.body:
        if isinstance(stmt, ast.If):
            out.append(stmt)
            cur = stmt
            while cur.orelse:
                nxt = cur.orelse[0]
                if isinstance(nxt, ast.If):
                    out.append(nxt)
                    cur = nxt
                else:
                    break
    return out


GUI_CALL_RE = re.compile(
    r"""\bapi\(\s*(?:["'`])([^"'`]+)(?:["'`])"""
    r"""(?:\s*,\s*(?:["'`])(GET|POST)(?:["'`]))?""")


def gui_calls():
    """返回 {(method, path): [文件:行, ...]}。未写方法参数的一律按 GET 计（fetch 默认）。"""
    calls = {}
    pat2 = re.compile(r"""API\s*\+\s*(?:["'`])([^"'`]+)(?:["'`])""")
    for vue in sorted(VUE_DIR.glob("*.vue")):
        for i, line in enumerate(vue.read_text(encoding="utf-8").splitlines(), 1):
            for path, method in GUI_CALL_RE.findall(line):
                if path.startswith("/"):
                    calls.setdefault(((method or "GET"), path), []).append(
                        "%s:%d" % (vue.name, i))
            for path in pat2.findall(line):
                if path.startswith("/"):
                    calls.setdefault(("GET", path), []).append("%s:%d" % (vue.name, i))
    return calls


def normalize(p):
    p = p.split("?")[0]
    p = re.sub(r"\$\{[^}]*\}", "{v}", p)
    # 字符串拼接调用（api("/stage/" + n + "/run")）只能截到 '/stage/'，
    # 这种残段不参与方法核对，只要任一方法能匹配到前缀即可。
    return p, p.endswith("/")


def matches(path, routes):
    exact, prefixes, templated = routes
    if path in exact:
        return True
    if any(path.startswith(pre) for pre in prefixes):
        return True
    for t in templated:
        rx = "^" + re.escape(t).replace(re.escape("{n}"), r"[0-9]+") + "$"
        if re.match(rx, path):
            return True
    return False


def main():
    routes, src, methods = server_routes()

    print("=== 0. 路由抽取锚点（AST）===")
    check("nf_api.py 仍定义 Handler.do_GET（统一 API 层的测试锚点）",
          "do_GET" in methods)
    check("nf_api.py 仍定义 Handler.do_POST（统一 API 层的测试锚点）",
          "do_POST" in methods)
    if "do_GET" not in methods or "do_POST" not in methods:
        print("\n锚点缺失，后续核对失去意义，提前退出。")
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        return 1

    for m in ("GET", "POST"):
        e, p, t = routes[m]
        print("do_%s：精确 %d / 前缀 %d / 模板 %d" % (m, len(e), len(p), len(t)))
    calls = gui_calls()
    print("前端调用点：%d 个 (method, path) 组合\n" % len(calls))

    print("=== 1. 每个前端调用都能在服务端找到【同方法】分支 ===")
    missing, wrong_method = {}, {}
    for (method, raw), where in sorted(calls.items()):
        path, ambiguous = normalize(raw)
        if matches(path, routes[method]):
            continue
        other = "POST" if method == "GET" else "GET"
        if matches(path, routes[other]):
            if ambiguous:
                continue          # 拼接残段，方法无从判断，不算错
            wrong_method["%s %s" % (method, raw)] = where
        else:
            missing["%s %s" % (method, raw)] = where
    check("无「前端在调、后端没有」的端点", not missing, missing)
    check("无「路径存在但 HTTP 方法不对」的端点", not wrong_method, wrong_method)

    print("\n=== 2. 历史断点回归项 ===")
    for method, ep in (("POST", "/models/switch"), ("POST", "/export/markdown"),
                       ("POST", "/models/add"), ("GET", "/config/project"),
                       ("POST", "/outline/chapters/save")):
        ok = ep in routes[method][0]
        check("%s %s 已注册" % (method, ep), ok,
              sorted(x for x in routes[method][0] if ep.split("/")[-1] in x))

    print("\n=== 3. 本轮新增端点都在 ===")
    for method, ep in (("GET", "/estimate"), ("GET", "/proofread/report"),
                       ("POST", "/proofread/run"), ("POST", "/style/analyze"),
                       ("GET", "/book/pacing"), ("POST", "/book/split"),
                       ("POST", "/stage/skip"), ("GET", "/logs/tail"),
                       ("GET", "/costs/streaming"), ("GET", "/about"),
                       # 2026-09-21：大纲迭代闭环 + 沙盒审核队列（GUI 页签依赖）
                       ("GET", "/outline/trend"), ("GET", "/outline/advise"),
                       ("GET", "/sandbox/queue"), ("GET", "/sandbox/file"),
                       ("POST", "/sandbox/review")):
        check("%s %s" % (method, ep), ep in routes[method][0])

    print("\n=== 4. do_GET / do_POST 内的裸 import 是否遮蔽模块级名 ===")
    tree = ast.parse(src)
    mod_names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                mod_names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                mod_names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            mod_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    mod_names.add(t.id)
    shadow, inline_count = [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in ("do_GET", "do_POST")):
            continue
        local = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                for a in n.names:
                    local.add(a.asname or a.name.split(".")[0])
                    inline_count += 1
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    local.add(a.asname or a.name)
                    inline_count += 1
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                            local.add(sub.id)
        hit = sorted(local & mod_names)
        if hit:
            shadow.append((node.name, hit))
    check("do_GET/do_POST 内无本地名遮蔽模块级名（真正的 UnboundLocalError 风险）",
          not shadow, shadow)
    check("本轮新增模块（proofread/estimate_tokens/book_split）已提到模块级",
          not any(x in src for x in ("import proofread\n", )),
          "疑似在函数内 import proofread")
    print("       （信息）do_GET/do_POST 内共 %d 处历史内联 import，"
          "均不遮蔽模块级名，可后续统一提取" % inline_count)

    print("\n=== 5. 死分支 / 裸尾随代码不再回来 ===")
    get_node = methods["do_GET"]
    get_body_src = ast.unparse(get_node)
    check("do_GET 内不再出现裸 read_text(/write_text(（未导入的名字）",
          not re.search(r"(?<![.\w])(read_text|write_text)\(", get_body_src),
          re.findall(r"(?<![.\w])(?:read_text|write_text)\(", get_body_src)[:5])
    check("do_GET 内不再出现 body.get（do_GET 没有 body）",
          "body.get" not in get_body_src,
          [l.strip() for l in get_body_src.splitlines() if "body.get" in l][:3])
    check("GET /review/run 不再出现在 do_GET（该分支会 500）",
          "/review/run" not in routes["GET"][0])

    # 「丢失 elif 守卫」类缺陷的判据（2026-09-19 修正）：
    # 曾经的写法是 `"elif" in ast.unparse(do_POST)` —— 那是**空断言**。
    # `ast.unparse` 把 `elif X:` 还原为「`else:` 里嵌 `if X:`」，函数体里必然有
    # 一大堆 `if`，所以怎么都能匹配到。真正的判据是**结构性的**：
    #   分支链从第一个 p 判定出发，沿 orelse 能一直走到最后一个分支；
    # 一旦某个 `elif p == "..."` 被误写成独立 `if`（历史上那次事故的形态），
    # 它就是 Try 体的**第二条顶层语句**，链在此断裂，链长骤降 —— 必须报警。
    post_stmts = []
    for stmt in methods["do_POST"].body:
        post_stmts.extend(stmt.body if isinstance(stmt, ast.Try) else [stmt])
    chains = sum(1 for s in post_stmts if isinstance(s, ast.If))
    check("do_POST 的 p 分支恰好构成一条 if/elif 链（未被裸 if 打断）",
          chains == 1, "Try 体内顶层 if 语句数 = %d（应为 1）" % chains)

    post_chain = []
    for stmt in post_stmts:
        if not isinstance(stmt, ast.If):
            continue
        post_chain.append(stmt)
        cur = stmt
        while cur.orelse and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
            post_chain.append(cur)
    check("do_POST 分支链长度 >= 40（61 个端点未断裂丢失）",
          len(post_chain) >= 40, len(post_chain))
    check("POST /outline/chapters/save 在分支链内（历史断点回归）",
          any("/outline/chapters/save" in ast.unparse(n.test) for n in post_chain),
          "该端点必须由 do_POST 顶层 elif 链分发，不能是裸尾随代码")

    print("\n=== 6. 主题：App.vue 提供的每个 theme 都要有 CSS 变量块 ===")
    app = APP_VUE.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    # 只从 THEMES 常量里取，避免误匹配 providerOptions 里 { id: "tokenhub", name: ... }
    block = re.search(r"const THEMES\s*=\s*\[(.*?)\];", app, re.S)
    theme_ids = re.findall(r'\{\s*id:\s*"([a-z]+)",\s*name:', block.group(1)) if block else []
    check("解析到 THEMES 主题列表", len(theme_ids) >= 5, theme_ids)
    for tid in sorted(set(theme_ids)):
        check("style.css 定义了 :root.theme-%s" % tid,
              (":root.theme-%s" % tid) in css)

    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
