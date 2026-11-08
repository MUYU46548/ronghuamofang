# -*- coding: utf-8 -*-
"""self 模式下“大纲精修”出任务书（2026-11-08，0.9.1 验收第 9 条）。

## 要治的病

作者的用法是“绒花墨坊出任务书 → 外部 agent 改 → 回到墨坊复核”，引擎也设成了
`engine: self`。可 `/refine/outline` 在 self 下被前置拒绝，点“AI 精修大纲”只得到
一句“用不了”—— 于是他认定“墨坊和外部 agent 根本是断开的”。

## 判据（每条都能变红）

- R1 self 模式：返回 202 + `mode=self` + 任务书真落盘（零 token，不起 job）
- R2 改之前先备份：`data/outline/history/global_vN.md` 存着**旧内容**
- R3 任务书自包含：含你的意见、当前大纲全文、写回路径、硬约束（章节数不变等）
- R4 非 self 引擎：**不吃这条分支**（照旧走模型精修，返回 job_id）
- R5 接线：`/refine/outline` 不在“要模型”名单里（否则这条分支永远走不到）

用法：python tests/unit/test_self_refine_outline.py
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


from nf_api_domains import refine as dom_refine                  # noqa: E402
import nf_api                                                    # noqa: E402

OUTLINE = """# 全书大纲

## 第一卷 起

### 第1章 入境
- 核心事件：柯砚在码头丢了半张船票，被扣下问话。
- 关键节点：他第一次听见"底噪"这个词。

### 第2章 夜航
- 核心事件：他替老太太压一句话进石头，收七文钱。
- 关键节点：钱到手，规矩也记住了。
"""


class _FakeH:
    headers = {}


_CWD = [Path(".")]


def _setup(tmp, engine):
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "system.yaml").write_text("engine: %s\n" % engine, encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(
        "book:\n  name: 测试书\n  chapters: 10\n  target_words: 90000\n", encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(OUTLINE, encoding="utf-8")


def _in_tmp(engine, fn):
    tmp = Path(tempfile.mkdtemp(prefix="nf_ro_"))
    _setup(tmp, engine)
    cwd = os.getcwd()
    old_root = nf_api.ROOT
    os.chdir(tmp)
    _CWD[0] = tmp
    nf_api.ROOT = tmp
    try:
        return fn(tmp)
    finally:
        nf_api.ROOT = old_root
        os.chdir(cwd)


def case_self_task():
    print("\n【R1】self → 202 + 任务书落盘（零 token）")
    fb = "第一卷太慢，把第 2 章的冲突提前到第 1 章；删掉空泛的过渡句。"

    def run(tmp):
        return dom_refine.handle_refine_outline(_FakeH(), {"feedback": fb})

    code, data = _in_tmp("self", run)
    check("R1 返回 202", code == 202, code)
    check("R1 mode=self", data.get("mode") == "self", data)
    rel = str(data.get("task") or "")
    check("R1 任务书路径给出来了（字符串，不是 dict）", rel and "{" not in rel, rel)
    found = None
    if rel:
        cand = Path(rel)
        if not cand.is_absolute():
            cand = _CWD[0] / cand
        if cand.exists():
            found = cand
    check("R1 任务书真的落盘", found is not None, rel)
    if found:
        body = found.read_text(encoding="utf-8")
        print("\n【R3】任务书自包含")
        check("R3 含你的意见", fb in body)
        check("R3 含写回路径 data/outline/global.md", "data/outline/global.md" in body)
        check("R3 含当前大纲全文（第1章 入境 在）", "第1章 入境" in body)
        check("R3 说清章节数不变", "章节数不变" in body)
        check("R3 说清改完怎么复核（点运行 / 阶段 2）", "阶段 2" in body)
        check("R3 说清旧版在哪回退", "history" in body)
    check("R1 没起 job（不调模型）", "job_id" not in data, data)


def case_backup():
    print("\n【R2】改之前先备份旧版")
    fb = "把第一卷压到两章。"

    def run(tmp):
        code, data = dom_refine.handle_refine_outline(_FakeH(), {"feedback": fb})
        hist = tmp / "data" / "outline" / "history"
        files = sorted(p.name for p in hist.glob("global_v*.md")) if hist.is_dir() else []
        old = ""
        if files:
            old = (hist / files[0]).read_text(encoding="utf-8")
        now = (tmp / "data" / "outline" / "global.md").read_text(encoding="utf-8")
        return code, data, files, old, now

    code, data, files, old, now = _in_tmp("self", run)
    check("R2 备份文件出现", bool(files), files)
    check("R2 备份是**旧内容**（第1章 入境 还在）", "第1章 入境" in old, old[:60])
    check("R2 备份之后没有偷改正文（外部还没写回，global.md 原样）", now == OUTLINE)


def case_non_self():
    print("\n【R4】非 self 引擎：不吃这条分支（照旧走模型）")
    calls = {}

    def run(tmp):
        old_c, old_j = dom_refine.api._client_for_env, dom_refine.api.start_job
        dom_refine.api._client_for_env = lambda cfg, role: calls.setdefault("client", object())
        dom_refine.api.start_job = lambda name, fn: (calls.setdefault("job", "jid9"), None)
        try:
            return dom_refine.handle_refine_outline(_FakeH(), {"feedback": "随便改改"})
        finally:
            dom_refine.api._client_for_env, dom_refine.api.start_job = old_c, old_j

    code, data = _in_tmp("direct", run)
    check("R4 返回 202 + job_id（走模型那条路）", code == 202 and data.get("job_id"),
          (code, data))
    check("R4 不返回 mode=self", data.get("mode") != "self", data)
    check("R4 真的构造了客户端", "client" in calls, calls)
    check("R4 没有写任务书", "task" not in data, data)


def case_wiring():
    print("\n【R5】接线：名单里必须**没有** /refine/outline")
    check("R5 NEEDS_MODEL_ENDPOINTS 不含 /refine/outline",
          "/refine/outline" not in nf_api.NEEDS_MODEL_ENDPOINTS,
          sorted(nf_api.NEEDS_MODEL_ENDPOINTS))
    check("R5 逐节点精修仍在名单（它没有任务书路径）",
          "/refine/outline/node" in nf_api.NEEDS_MODEL_ENDPOINTS)
    src = (ROOT / "scripts" / "nf_api_domains" / "refine.py").read_text(encoding="utf-8")
    check("R5 有 build_self_refine_task", "def build_self_refine_task(" in src)
    check("R5 handler 按引擎分流",
          'if resolve_engine(cfg) == "self":' in src)


def main():
    print("=" * 66)
    print("self 模式：大纲精修出任务书")
    case_self_task()
    case_backup()
    case_non_self()
    case_wiring()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
