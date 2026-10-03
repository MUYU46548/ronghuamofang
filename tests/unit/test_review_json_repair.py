# -*- coding: utf-8 -*-
"""审稿 JSON 断裂修复路径自检（P0，2026-10-03）。零 LLM、零网络。

## 为什么需要

审稿报告是 JSON，而 JSON 是**一个字符错了就整份作废**的格式。旧实现有两处病：

1. `chapter_review._extract_json_from_output` 是**第二套**手写解析，**没有断裂兜底**
   —— 被 max_tokens 截断、只差一个收尾 `}`/`]` 的报告一律解析失败；
2. 解析失败**一次就 return False**：整轮审稿（把每章正文+大纲+滚动摘要塞进 prompt，
   输入动辄几十万 token）全白烧，返回的还是半截原文，人看不出该改提示词还是该调上限。

现在：断裂兜底（validator.parse_llm_json 的括号配对补全）→ 仍败按
`gates.review_retries`（默认 1）重试 → 仍败则**原文隔离存放 + 可行动报错 + 非零返回**，
由 orchestrator 的审稿门 fail-closed 停住（不放行 stage5/6）。

判据都是**行为级**的：看「LLM 被调了几次」「报告落没落盘」「返回消息能不能照着做」，
而不是看日志里有没有打印。

用法：python tests/unit/test_review_json_repair.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import chapter_review as cr                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


# 断裂样本：只差收尾括号（截断的真实形态）
TRUNCATED = ('{"chapters":[{"n":1,"findings":[{"id":"f001","type":"other",'
             '"severity":"warn","detail":"断在这里')
GOOD = json.dumps({"chapters": [{"n": 1, "findings": []}]}, ensure_ascii=False)
NOT_JSON = "好的，我已经审查完了，但忘了输出 JSON。"


class ScriptedClient:
    """按脚本回放 LLM 输出；记录被调用次数（重试判据的唯一证据）。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_task(self, task_path, **kwargs):
        self.calls += 1
        item = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        if isinstance(item, dict):
            return dict(item)
        return {"exit_code": 0, "stdout_tail": item, "tokens": 10, "tokens_out": 5,
                "model": "scripted"}


def build_workspace(tag, review_retries=None):
    """临时项目根：一章正文 + 模板 + 配置。"""
    tmp = Path(tempfile.mkdtemp(prefix="review_json_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference", "__pycache__"))
    for d in ("data/chapters/raw", "data/outline/chapters", "data/state", "config"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data/chapters/raw/01.md").write_text(
        "# 第1章 测试\n\n" + "正文。" * 200 + "\n<!-- quality: 8/10 -->\n", encoding="utf-8")
    (tmp / "data/outline/chapters/01.md").write_text(
        "# 第1章 大纲\n- 核心事件：某事件\n", encoding="utf-8")
    gates = ""
    if review_retries is not None:
        gates = "  review_retries: %d\n" % review_retries
    (tmp / "config/system.yaml").write_text(
        "engine: direct\nbudget:\n  limit_yuan: 300\ngates:\n" + gates, encoding="utf-8")
    (tmp / "config/project.yaml").write_text(
        'book:\n  name: "审稿 JSON 测试书"\n', encoding="utf-8")
    return tmp


def run_case(tag, outputs, report="data/outline/review_report.json", retries=None):
    """在临时工作区跑一次 run_review。返回 (ok, msg, client, tmp)。"""
    tmp = build_workspace(tag, review_retries=retries)
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        client = ScriptedClient(outputs)
        ok, msg = cr.run_review("raw", report, dry_run=False, client=client)
        return ok, msg, client, tmp
    finally:
        os.chdir(origin)


# ============================================================ 1. 断裂兜底
def case_repair_saves_truncated():
    print("\n【1】断裂 JSON 兜底：只差收尾括号的报告要救回来（这是省钱的那一半）")
    c = cr._extract_json_from_output(TRUNCATED)
    check("截断的审查 JSON 被补全括号后解析成功", cr._is_review_report(c), c)
    check("补回来的内容没串味（章号/字段都在）",
          c and c["chapters"][0]["n"] == 1
          and c["chapters"][0]["findings"][0]["id"] == "f001", c)
    check("FILE 协议文本里的 JSON 也能提出来",
          cr._is_review_report(cr._extract_json_from_output(
              "===FILE: data/outline/review_report.json\n" + GOOD + "\n===END===")))
    check("裸 JSON 照旧", cr._is_review_report(cr._extract_json_from_output(GOOD)))
    check("纯散文 → None（不许把散文当报告）",
          cr._extract_json_from_output(NOT_JSON) is None)


def case_contract_guard():
    print("\n【2】契约判据：能解析成 JSON ≠ 是审查报告")
    check("缺 chapters → 不算报告（否则会渲染出一份假「零问题」报告）",
          cr._is_review_report({"error": "我不确定"}) is False)
    check("chapters 不是数组 → 不算报告", cr._is_review_report({"chapters": {}}) is False)
    check("数组顶层 → 不算报告（审查报告是对象）", cr._is_review_report([]) is False)
    check("None → 不算报告", cr._is_review_report(None) is False)
    check("正常报告 → 通过", cr._is_review_report({"chapters": []}) is True)


# ============================================================ 3. 重试 → 停
def case_parse_fail_retries_then_stops():
    print("\n【3】解析失败 → 重试 → 仍败则明确报错停（不再一次报废整轮）")
    ok, msg, client, tmp = run_case("retry", [NOT_JSON, NOT_JSON])
    try:
        check("两次都解析不出 → 阶段判失败", ok is False, msg)
        check("**真的重试了**（默认 1 轮 → 共 2 次调用）", client.calls == 2,
              "实际调用 %d 次" % client.calls)
        check("报错点明「审稿门 fail-closed，不放行下游」",
              "fail-closed" in msg, msg)
        check("报错是可行动的（给出该改哪个键/哪个模板/怎么重跑）",
              "budget.token_limit.per_request_max_tokens" in msg
              and "prompts/chapter_review.md" in msg and "--from 4" in msg, msg)
        check("带上了 validator 的失败偏移诊断", "偏移" in msg, msg)
        raw = tmp / "data" / "state" / "review_raw"
        files = sorted(raw.glob("*.txt")) if raw.exists() else []
        check("失败原文被隔离存放（能看出断在哪，不是只回一句失败）",
              len(files) == 1 and NOT_JSON[:10] in files[0].read_text(encoding="utf-8"),
              [str(f) for f in files])
        check("**没有落盘报告**（不许把解析失败伪装成零问题报告）",
              not (tmp / "data" / "outline" / "review_report.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_repair_no_retry():
    print("\n【4】反证：能兜底救回的就**不要**重试（省一次几十万 token 的调用）")
    ok, msg, client, tmp = run_case("ok1", [TRUNCATED])
    try:
        check("第一次就成功（断裂被兜底）", ok is True, msg)
        check("**只调了 1 次**，没有多余重试", client.calls == 1,
              "实际调用 %d 次" % client.calls)
        report = tmp / "data" / "outline" / "review_report.json"
        check("报告真的落盘了", report.exists())
        data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        check("落盘内容来自修复后的解析结果", data.get("chapters"),
              str(data)[:200])
        check("报告带 scope/确定性检查元数据",
              data.get("scope") == "raw" and "deterministic" in data, str(data)[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_second_attempt_succeeds():
    print("\n【5】第一次散文、第二次真 JSON → 重试救回（重试是有意义的那一次）")
    ok, msg, client, tmp = run_case("ok2", [NOT_JSON, GOOD])
    try:
        check("重试后成功", ok is True, msg)
        check("调用 2 次", client.calls == 2, client.calls)
        check("报告落盘",
              (tmp / "data" / "outline" / "review_report.json").exists())
        check("成功路径**不**隔离原文（只有失败才留垃圾）",
              not (tmp / "data" / "state" / "review_raw").exists()
              or not list((tmp / "data" / "state" / "review_raw").glob("*.txt")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_retries_configurable():
    print("\n【6】gates.review_retries 可配（0 = 不重试，省到极致）")
    ok, msg, client, tmp = run_case("no0", [NOT_JSON, GOOD], retries=0)
    try:
        check("retries=0 → 只调 1 次", client.calls == 1, client.calls)
        check("仍明确报错停（不是静默）", ok is False and "fail-closed" in msg, msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok, msg, client, tmp = run_case("r3", [NOT_JSON] * 4, retries=3)
    try:
        check("retries=3 → 共 4 次调用（上限被尊重）", client.calls == 4, client.calls)
        check("仍失败则明确报错停", ok is False, msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_exit_code_failure_no_retry():
    print("\n【7】调用本身失败（退出码非零）→ **不重试**（同一个上限只会再截断一次）")
    ok, msg, client, tmp = run_case("exit2", [
        {"exit_code": 2, "error": "输出被 max_tokens 截断", "stdout_tail": "半截正文"}])
    try:
        check("只调 1 次（不做无意义的重复烧钱）", client.calls == 1, client.calls)
        check("失败原因原样带出（含退出码）", ok is False and "退出码 2" in msg, msg)
        check("原文照样隔离存放", list((tmp / "data" / "state" / "review_raw").glob("*.txt")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  审稿 JSON 断裂修复路径自检（P0）")
    print("=" * 62)
    case_repair_saves_truncated()
    case_contract_guard()
    case_parse_fail_retries_then_stops()
    case_repair_no_retry()
    case_second_attempt_succeeds()
    case_retries_configurable()
    case_exit_code_failure_no_retry()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
