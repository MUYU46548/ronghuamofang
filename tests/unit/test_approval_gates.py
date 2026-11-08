# -*- coding: utf-8 -*-
"""`/config/approval_gates`（审批门）的回归锁（2026-11-08）。

## 为什么要有这个端点

作者验收 v0.9.0 时问得很直接：“阶段 3 为什么无需审批？阶段 4 怎么也无需审批？”

事实是：门由 `gates.require_approval` 决定（出厂 `[2, 6]`），但此前**只有手改
config/system.yaml 一条路** —— 界面上既看不到“门在哪”，也改不了。于是“阶段 3/4
为什么不停”变成了一个只能靠读配置才能回答的问题。

## 判据（每条都能变红）

- A1 读口回当前值 + 出厂默认 `[2, 6]` + 六个阶段的名字（界面要按名字显示）
- A2 越界阶段号（0 / 7）/ 非整数 → 400，且**配置文件一个字不动**（fail-closed）
- A3 合法写入 → 落成**排序去重的整数列表**，回读一致；注释不丢（定向改写，不整份 dump）
- A4 写口是审批族：`agent_guard.FORBIDDEN_IN_AGENT_MODE` 含该路径 + 端点在 GUI 判据后面
- A5 路由真的接上：ROUTES 有 GET/POST，nf_api 有转发（否则界面上点了没反应）

用法：python tests/unit/test_approval_gates.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


from nf_api_domains import project as dom_proj          # noqa: E402
from utils import agent_guard                            # noqa: E402

SYS_YAML = """# ===== 头部注释（不许丢）=====
engine: self
gates:
  agent_mode: true
  # 门在后面几行；上面的注释必须还在
  require_approval:
  - 2
  - 6
  pause_on_failure: true
  material_autonomy: false
# ===== 尾部注释（不许丢）=====
"""


class _FakeH:
    headers = {}


def _in_tmp(fn):
    """把 nf_api.ROOT 与 cwd 都指到临时项目根（否则 handler 会写**真配置**）。"""
    import nf_api
    tmp = Path(tempfile.mkdtemp(prefix="nf_gate_"))
    (tmp / "config").mkdir(parents=True)
    (tmp / "config" / "system.yaml").write_text(SYS_YAML, encoding="utf-8")
    cwd = os.getcwd()
    old_root = nf_api.ROOT
    os.chdir(tmp)
    nf_api.ROOT = tmp
    try:
        return fn(tmp)
    finally:
        nf_api.ROOT = old_root
        os.chdir(cwd)


def _call_set(body):
    # 写口带 GUI 判据；这里只测校验与落盘，判据本身在 A4 单独锁（不起 HTTP 拿不到 token）
    orig = dom_proj._gui_only
    dom_proj._gui_only = lambda h, what: None
    try:
        return dom_proj.handle_config_approval_gates_set(_FakeH(), body)
    finally:
        dom_proj._gui_only = orig


def case_read():
    print("\n【A1】读口：当前值 + 出厂默认 + 阶段名")

    def run(tmp):
        return dom_proj.handle_config_approval_gates(_FakeH())

    code, data = _in_tmp(run)
    check("A1 GET 200", code == 200, code)
    check("A1 当前值 [2, 6]", data.get("stages") == [2, 6], data.get("stages"))
    check("A1 出厂默认也在", data.get("factory") == [2, 6], data.get("factory"))
    names = data.get("names") or {}
    # handler 侧键是 int（经 JSON 会变成字符串键，前端按 `names[n]` 取，两种都通）
    check("A1 六个阶段都有名字", all(i in names for i in range(1, 7)), names)
    check("A1 明说是哪个配置项", data.get("field") == "gates.require_approval",
          data.get("field"))


def case_reject_bad():
    print("\n【A2】越界 / 非整数 → 400，配置文件一个字不动")

    for bad in ({"stages": [2, 7]}, {"stages": [0]}, {"stages": ["三"]},
                {"stages": "2,6"}, {}):
        def run(tmp, bad=bad):
            before = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
            code, data = _call_set(bad)
            after = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
            return code, data, before == after

        code, data, unchanged = _in_tmp(run)
        check("A2 %r → 400" % (bad,), code == 400, code)
        check("A2 %r 配置文件没动" % (bad,), unchanged)
        check("A2 %r 有可行动的话" % (bad,), bool(str(data.get("error") or "").strip()),
              data.get("error"))


def case_write():
    print("\n【A3】合法写入：排序去重的整数列表，注释不丢")

    def run(tmp):
        code, data = _call_set({"stages": [4, 2, 2, 3]})
        after = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
        code2, data2 = dom_proj.handle_config_approval_gates(_FakeH())
        return code, data, after, code2, data2

    code, data, after, code2, data2 = _in_tmp(run)
    check("A3 写口 200", code == 200, code)
    check("A3 落成排序去重 [2, 3, 4]", data.get("stages") == [2, 3, 4], data.get("stages"))
    check("A3 回读一致", code2 == 200 and data2.get("stages") == [2, 3, 4],
          data2.get("stages"))
    check("A3 头部注释还在", "===== 头部注释（不许丢）=====" in after)
    check("A3 尾部注释还在", "===== 尾部注释（不许丢）=====" in after)
    check("A3 没写成 dict / 字符串", "require_approval:\n" in after or "require_approval:" in after)


def case_gui_only():
    print("\n【A4】写口是审批族：Agent 不得替作者减少确认点")

    check("A4 agent_guard 禁用名单含 /config/approval_gates",
          "/config/approval_gates" in agent_guard.FORBIDDEN_IN_AGENT_MODE)
    src = (ROOT / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
    i = src.find("def handle_config_approval_gates_set")
    check("A4 写口第一件事就是 GUI 判据",
          i > 0 and "_gui_only(h," in src[i:i + 900], src[i:i + 160])
    check("A4 文案点明“外部 Agent 不得减少确认点”", "审批" in src[i:i + 900])


def case_routes():
    print("\n【A5】路由与转发真的接上")

    check("A5 ROUTES 有 GET", any(r[0] == "GET" and r[1] == "/config/approval_gates"
                                  for r in dom_proj.ROUTES))
    check("A5 ROUTES 有 POST", any(r[0] == "POST" and r[1] == "/config/approval_gates"
                                   for r in dom_proj.ROUTES))
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("A5 nf_api 有 GET 转发",
          'p == "/config/approval_gates"' in api_src
          and "handle_config_approval_gates(self)" in api_src)
    check("A5 nf_api 有 POST 转发", "handle_config_approval_gates_set(self, body)" in api_src)


def main():
    print("=" * 66)
    print("审批门端点（/config/approval_gates）")
    case_read()
    case_reject_bad()
    case_write()
    case_gui_only()
    case_routes()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
