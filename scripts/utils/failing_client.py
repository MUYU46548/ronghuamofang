# -*- coding: utf-8 -*-
"""FailingClient：可控失败的假客户端（F1~F8 失败路径测试专用）。

## 为什么需要它

`FakeClient.run_task()` **恒返回 `exit_code=0`** —— 全仓 22 个 `result["exit_code"] != 0`
分支（stage1~6 的子会话失败判定、batch_refine/refine_outline 等的失败回报）
在测试里**从未被走到过**。这是本项目最大的「假成功」温床：
兜底机制（重试/熔断/停止）自己崩了没人知道。

2026-09-19 审查报告 H5 项：「失败路径 100% 零覆盖」。
本模块是该结论的正面回应 —— 不动生产代码、不联网、可稳定复现失败。

## 用法

    from utils.failing_client import FailingClient

    # ① 全阶段恒失败
    client = FailingClient()                       # fail_on="*"

    # ② 只在指定阶段失败，其余正常（继承 FakeClient 的真实产物能力）
    client = FailingClient(fail_on=["stage4"])     # stage4 批失败、stage1~3 正常

    # ③ 前 N 次失败，之后成功（验证「重试后成功」）
    client = FailingClient(fail_times=1)

    # ④ 抛异常而非返回非零码（验证异常穿透路径）
    client = FailingClient(raise_exc=RuntimeError("模拟子会话崩溃"))

    # ⑤ 抛关键词参数：用于触发 orchestrator 的预算熔断（异常穿透 vs 正常收尾）
    见 `as_expensive()` 辅助构造。

## 设计边界（刻意为之）

- **绝不进真实流水线**：模块名以 `failing_` 开头，不在 `make_client()` 的 provider 分支里，
  只能被测试显式 import。
- **保持只读语义**：`exit_code != 0` 时**不写任何产物** —— 真实子会话失败时正是如此
  （子会话崩了不会产出章节文件）。若这里仍写文件，测出来的就不是失败路径了。
- `stop_flag` 被尊重：支持 F4（用户停止）用例。
"""
import re
from pathlib import Path

from utils.fake_client import FakeClient
from utils.file_io import read_text


def _stage_of(name):
    """从任务文件名推断阶段分组（与 FakeClient 的 dispatch 保持同口径）。

    返回值形如 "stage1" / "stage4" / "batch_refine"；
    无法识别时返回文件去扩展名后的第一段，便于 fail_on 精确匹配。
    """
    for pat, key in (
        (r"^stage1_scraps", "stage1_scraps"),
        (r"^stage1", "stage1"),
        (r"^stage2", "stage2"),
        (r"^stage3", "stage3"),
        (r"^stage4", "stage4"),
        (r"^stage5_proofread", "stage5_proofread"),
        (r"^stage5", "stage5"),
        (r"^stage6", "stage6"),
        (r"^stage7", "stage7"),
        (r"^chapter_review", "chapter_review"),
        (r"^batch_refine", "batch_refine"),
        (r"^auto_rewrite", "auto_rewrite"),
        (r"^proofread", "proofread"),
    ):
        if re.match(pat, name):
            return key
    return Path(name).stem


class FailingClient(FakeClient):
    """与 FakeClient 同签名，但可按策略返回失败。

    参数
    ----
    fail_on : str | list[str] | None
        命中即失败。`"*"` / None 之外的默认是 `"*"`（全失败）。
        单个字符串按「阶段分组」精确匹配（见 `_stage_of`），
        如 `"stage4"` 只让 stage4_* 任务失败。
    fail_times : int | None
        只让前 N 次调用失败，之后恢复正常（验证「重试后成功」链路）。
        `None` = 一直失败。
    exit_code : int
        失败时返回的退出码（默认 1，模拟子会话非零退出）。
    raise_exc : BaseException | None
        不为 None 时改为**抛异常**而非返回非零码 —— 覆盖「客户端本身崩了」
        这条比「客户端返回失败」更深的路径。
    empty_stdout : bool
        失败时是否清空 stdout_tail（模拟「无任何输出就挂了」）。
    """

    provider = "failing"
    model = "failing"

    def __init__(self, fail_on="*", fail_times=None, exit_code=1,
                 raise_exc=None, empty_stdout=True):
        if isinstance(fail_on, str):
            fail_on = [fail_on]
        self.fail_on = list(fail_on or ["*"])
        self.fail_times = fail_times
        self.exit_code = exit_code
        self.raise_exc = raise_exc
        self.empty_stdout = empty_stdout
        # 调用计数（按「总次数」与「按阶段」分别记，便于断言）
        self.calls = 0
        self.calls_by_stage = {}

    # ---------------------------------------------------------------- 内部

    def _should_fail(self, stage):
        """判定本次调用是否应失败（并推进计数器）。"""
        self.calls += 1
        self.calls_by_stage[stage] = self.calls_by_stage.get(stage, 0) + 1
        if self.fail_times is not None and self.calls > self.fail_times:
            return False
        if "*" in self.fail_on:
            return True
        return stage in self.fail_on

    def _fail_result(self, stage):
        err = self.raise_exc
        if err is not None:
            raise err
        return {
            "exit_code": self.exit_code,
            "stdout_tail": "" if self.empty_stdout
                           else "（failing）模拟子会话失败: " + stage,
            "tokens": 0,
            "tokens_out": 0,
            "cost_yuan": 0.0,
            "estimated": True,
            "provider": self.provider,
            "model": self.model,
            "requests": 1,
            "error": "模拟失败",
        }

    # ---------------------------------------------------------------- 接口

    def run_task(self, task_file, workdir=None, model=None):
        name = Path(task_file).name
        stage = _stage_of(name)
        if self._should_fail(stage):
            print("[failing_client] 模拟失败: " + name + "（分组 " + stage + "）")
            return self._fail_result(stage)
        # 未命中失败策略 → 复用 FakeClient 的真实产物能力，
        # 这样「只在 stage4 失败」的用例里 stage1~3 依然能产出可校验的产物。
        return super().run_task(task_file, workdir=workdir, model=model)

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None):
        name = Path(task_file).name
        stage = _stage_of(name)
        # 停止优先：真实场景里用户点停止后，子会话即便"会失败"也应表现为 stopped。
        if stop_flag and stop_flag():
            return {
                "exit_code": self.exit_code,
                "stdout_tail": "",
                "tokens": 0, "tokens_out": 0, "cost_yuan": 0.0,
                "estimated": True, "provider": self.provider, "model": self.model,
                "requests": 1, "stopped": True,
            }
        if self._should_fail(stage):
            print("[failing_client] 模拟流式失败: " + name)
            res = self._fail_result(stage)
            res["stopped"] = False
            return res
        return super().run_task_stream(task_file, on_piece, stop_flag,
                                       workdir=workdir, model=model)

    def write_task(self, task_dir, name, content):
        """写任务文件本身**不算失败点** —— 真实崩溃发生在执行阶段。

        否则 stage4 会在 `client.write_task()` 就炸，测不到
        「子会话退出码非零 → mark_chapter_failed」这条真正的分支。
        """
        return super().write_task(task_dir, name, content)


class ExpensiveClient(FakeClient):
    """正常产出，但每次调用都记一笔**高额**费用 —— 触发预算熔断（F3）。

    与 FailingClient 的区别：FailingClient 造「失败」，这里造「成功但烧钱」，
    用来验证 `cost.status() == "pause"` 这条**与失败无关**的熔断路径。
    """

    provider = "expensive"
    model = "expensive"

    def __init__(self, cost_per_call=200.0):
        self.cost_per_call = cost_per_call

    def run_task(self, task_file, workdir=None, model=None):
        res = super().run_task(task_file, workdir=workdir, model=model)
        res["cost_yuan"] = self.cost_per_call
        res["estimated"] = False   # 走真实计价，避免被 estimated 逻辑改写
        return res


def stage_failing_client(fail_on="*", **kw):
    """便捷构造：FailingClient 且失败时抛异常（用于异常穿透类用例）。"""
    kw.setdefault("raise_exc", RuntimeError("模拟子会话进程崩溃"))
    return FailingClient(fail_on=fail_on, **kw)


def read_task_name(task_file):
    """测试辅助：安全读回任务文件名（供断言用）。"""
    return read_text(Path(task_file))[:200]
