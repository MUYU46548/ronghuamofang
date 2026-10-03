# -*- coding: utf-8 -*-
"""B2 会话续接回归（默认关，不改既有行为）。

| 编号 | 钉什么 |
|---|---|
| K1 | `--resume` 只在**开了开关且传了 session_id** 时才拼进命令 |
| K2 | **默认关**：没开开关时，即使传了 session_id 也不拼（= 行为与改动前一致） |
| K3 | `_parse_stream_json` 能提出 session_id；提不到 → None；非 JSON 行不炸 |
| K4 | 续接失败**自动降级**为全新会话重试一次（续接不该成为新的失败源） |
| K5 | 两个 client 的 `run_task` **签名同构**（direct 收下 session_id 并忽略） |
| K6 | `make_client` 从 `cfg["hermes"]["session_continuation"]` 读，缺省 = False |
| K7 | stage4 只在**本章成功**后才把 session 传下一章（失败章节不带噪音过去） |

用法：python tests/unit/test_session_continuation.py（零 LLM、零网络、不起子进程）
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.llm_client import (HermesClient, OpenAICompatClient,   # noqa: E402
                              make_client)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:200]) if extra else ""))


# --------------------------------------------------------------------- K1/K2

def case_resume_flag():
    print("\n[K1/K2] --resume 只在开了开关且传了 session_id 时才拼")
    on = HermesClient(session_continuation=True)
    off = HermesClient()                      # 默认关
    cmd_on = on._build_cmd("t.md", session_id="sess-123")
    check("K1 开关开 + 有 session_id → 拼 --resume 与 --create-if-missing",
          "--resume" in cmd_on and "sess-123" in cmd_on
          and "--create-if-missing" in cmd_on, cmd_on)
    cmd_none = on._build_cmd("t.md", session_id=None)
    check("K1 开关开但无 session_id → 不拼", "--resume" not in cmd_none, cmd_none)
    cmd_off = off._build_cmd("t.md", session_id="sess-123")
    check("K2 **默认关**：即使传了 session_id 也不拼（行为不变）",
          "--resume" not in cmd_off, cmd_off)
    check("K2 默认关时开关属性为 False", off.session_continuation is False,
          off.session_continuation)


# --------------------------------------------------------------------- K3

def case_parse_session():
    print("\n[K3] _parse_stream_json 提取 session_id")
    raw = "\n".join([
        '{"type":"system","subtype":"init","session_id":"abc-1","model":"m1"}',
        '{"type":"text","text":"hi"}',
        '不是 JSON 的一行（收尾 session_id 行）',
        '{"type":"result","exit_code":0,"text":"done"}',
    ])
    result, init, texts, sid = HermesClient._parse_stream_json(raw)
    check("K3 从 init 事件提出 session_id", sid == "abc-1", sid)
    check("K3 result/text 解析不受影响",
          result is not None and texts == ["hi"], (result, texts))
    _r2, _i2, _t2, sid2 = HermesClient._parse_stream_json('{"type":"text","text":"x"}')
    check("K3 事件里没有 session_id → None（不报错）", sid2 is None, sid2)
    _r3, _i3, _t3, sid3 = HermesClient._parse_stream_json("完全不是 JSON\n")
    check("K3 全脏输入不炸且返回 None", sid3 is None, sid3)


# --------------------------------------------------------------------- K4

def case_fallback():
    print("\n[K4] 续接失败自动降级为全新会话")
    c = HermesClient(session_continuation=True)
    calls = []

    def fake_run_once(task_file, workdir=None, model=None, session_id=None):
        calls.append(session_id)
        if session_id:
            return {"exit_code": 1, "error": "session not found"}
        return {"exit_code": 0, "session_id": "brand-new", "stdout_tail": ""}

    c._run_once = fake_run_once          # 不打真子进程
    res = c.run_task("x.md", session_id="old-sess")
    check("K4 先试续接、失败后降级再试一次（调用两次）",
          calls == ["old-sess", None], calls)
    check("K4 降级后成功且带 resumed_fallback 标记",
          res["exit_code"] == 0 and res.get("resumed_fallback") is True, res)

    # 未开开关时不该有任何续接尝试
    c2 = HermesClient(session_continuation=False)
    calls2 = []

    def fake2(task_file, workdir=None, model=None, session_id=None):
        calls2.append(session_id)
        return {"exit_code": 0}

    c2._run_once = fake2
    r2 = c2.run_task("x.md", session_id="old")
    check("K4 开关关着 → 原样透传，无降级逻辑",
          calls2 == ["old"] and not r2.get("resumed_fallback"), (calls2, r2))


# --------------------------------------------------------------------- K5

def case_signature():
    print("\n[K5] 两个 client 的 run_task 签名同构（含 session_id）")
    import inspect
    for cls in (HermesClient, OpenAICompatClient):
        params = list(inspect.signature(cls.run_task).parameters)
        check("K5 %s.run_task 收 session_id" % cls.__name__,
              "session_id" in params, params)


# --------------------------------------------------------------------- K6

def case_make_client_cfg():
    print("\n[K6] make_client 从 cfg['hermes'] 读开关，缺省 False")
    c_on = make_client({"engine": "hermes",
                        "hermes": {"session_continuation": True}}, verbose=False)
    c_def = make_client({"engine": "hermes"}, verbose=False)
    check("K6 配了 true → 开关开",
          isinstance(c_on, HermesClient) and c_on.session_continuation is True,
          getattr(c_on, "session_continuation", None))
    check("K6 没有该键 → 默认关（**不改既有行为**）",
          isinstance(c_def, HermesClient) and c_def.session_continuation is False,
          getattr(c_def, "session_continuation", None))


# --------------------------------------------------------------------- K7

def case_stage4_wiring():
    print("\n[K7] stage4 只在成功后传递 session（AST 结构断言）")
    src = (REPO / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("K7 stage4 调 run_task 时传了 session_id",
          "client.run_task(task, session_id=chapter_session)" in src, "")
    check("K7 有 chapter_session 初始化", "chapter_session = None" in src, "")
    # 更新语句必须落在「本章成功」分支内（consec_fail = 0 之后），而不是失败分支
    tree = ast.parse(src)
    ok_order = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body_src = ast.get_source_segment(src, node) or ""
        i_reset = body_src.find("consec_fail = 0")
        i_set = body_src.find('chapter_session = result["session_id"]')
        # 只看**同时含两者**的那个函数（外层/嵌套函数里没有这两句）
        if i_reset != -1 and i_set != -1:
            ok_order = i_set > i_reset
            break
    check("K7 session 更新在「成功」之后（不在失败分支）", ok_order, ok_order)
    check("K7 更新前有 session_continuation 守卫",
          'getattr(client, "session_continuation", False)' in src, "")


def case_all_implementations():
    print("\n[K8] 所有 run_task 实现都必须收 session_id（防漏改）")
    missing = []
    for root in (REPO / "scripts", REPO / "tests"):     # tests/ 也扫：夹具桩同样是实现
        for p in root.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except Exception:                                 # noqa: BLE001
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "run_task":
                    args = [a.arg for a in node.args.args]
                    has_var = bool(node.args.vararg or node.args.kwarg)
                    if "session_id" not in args and not has_var:
                        missing.append("%s:%d" % (p.relative_to(REPO).as_posix(),
                                                  node.lineno))
    # 实测踩到：stage4 改调 `run_task(task, session_id=...)` 后，FakeClient 没跟着改
    # → 4 个端到端用例整片 TypeError。这条护栏就是防「加了参数却漏改某个实现」。
    check("K8 无遗漏实现（否则调用方传参会 TypeError）", not missing, missing)


def main():
    print("=" * 70)
    print("B2 会话续接回归（默认关）")
    print("=" * 70)
    case_resume_flag()
    case_parse_session()
    case_fallback()
    case_signature()
    case_make_client_cfg()
    case_stage4_wiring()
    case_all_implementations()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
