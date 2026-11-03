# -*- coding: utf-8 -*-
"""失败契约：`stderr_tail` 不许在 stage 层丢掉，非可重试错误不许原样再赌（2026-11-03）。

## 要治的两件事

1. **错误契约断链**：`llm_client._finish` 已经把子会话的 `stderr_tail`（上游 HTTP
   报文尾部）抓下来了，但 stage 的失败分支统一只写一句"子会话退出码非零"
   → `progress.error` 与界面上一个字都没有，排障只能猜（上次 20 分钟）。
2. **盲目重试**：`should_auto_retry` 只判截断。400/401/403/404、模型不可用这些
   **同一输入重跑必然同样失败**的错误照样烧掉整轮重试（实测白跑 2/3 的时间）。

## 判据（每条都能变红）

- E1 `describe()` 从 stderr 报文里挑出带 HTTP 码的那一句（不是 "Traceback"）
- E2 `describe()` 无报文时退回"退出码非零 + 没有报文"（不静默丢信息）
- E3 `classify()`：400/422 → request，401/403 → auth，404/模型不可用 → not_found，
  503/超时 → transport，finish_reason=length → truncated
- E4 `retryable()`：四类非可重试错误全判 False 并给出可执行处置
- E5 `retryable()`：transport / unknown 保持可重试（不擅自收紧既有行为）
- E6 `should_auto_retry(..., error_text=400 报文)` → 不重试；不传 error_text 时旧行为不变
- E7 `stage_error_text()` 同时取阶段级 error 与逐章 failed_chapters[].error
- E8 源码接线：stage1/2/4/5 的失败分支不再写死"子会话退出码非零"

用法：python tests/unit/test_error_contract.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import orchestrator                                              # noqa: E402
from utils import error_contract as ec                           # noqa: E402
from utils.progress_manager import ProgressManager               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


# 真实上游报文样式（tokenrhythm / 通用 OpenAI 兼容网关都会这么回）
STDERR_400 = """Traceback (most recent call last):
  File "hermes_cli/main.py", line 1, in <module>
openai.BadRequestError: Error code: 400 - {'code': 'UPSTREAM_ERROR', 'message': \
'Failed to deserialize the JSON body into the target type: duplicate field \
`reasoning_content` at line 1 column 203054'}
"""


def main():
    print("=" * 66)
    print("失败契约（error_contract）")

    print("\n【1】describe()：把报文里最该看的那一行带出来")
    got = ec.describe({"exit_code": 1, "stderr_tail": STDERR_400})
    check("E1 带出 HTTP 400 与上游 message", "400" in got and "duplicate" in got, got)
    check("E1b 不以 Traceback 开头（挑的是重点行）", not got.startswith("Traceback"), got)
    check("E1c 长度有上限（进 GUI 提示条不刷屏）", len(got) <= ec.MAX_REASON, len(got))

    got = ec.describe({"exit_code": 2})
    check("E2 无报文时不静默：说清 exit 且说明没有报文",
          "exit=2" in got and "没有" in got, got)

    check("E2b 报文在 stdout_tail 时同样能取到",
          "400" in ec.describe({"exit_code": 1, "stdout_tail": STDERR_400}))

    print("\n【2】classify()：错误类型判据")
    cases = [
        ("Error code: 400 - bad request", "request"),
        ("HTTP 422 Unprocessable", "request"),
        ('duplicate field `reasoning_content`', "request"),
        ("Error code: 401 - invalid api key", "auth"),
        ("Error code: 403 Forbidden", "auth"),
        ("Error code: 404 - no such model", "not_found"),
        ("{'code': 'MODEL_NOT_AVAILABLE', 'message': '模型不可用：mimo-v2-omni'}", "not_found"),
        ("Error code: 503 upstream unavailable", "transport"),
        ("ReadTimeout: 连接超时", "transport"),
        ("finish_reason=length", "truncated"),
        ("something odd happened", "unknown"),
    ]
    for text, want in cases:
        got = ec.classify(text)
        check("E3 %-22s → %s" % (want, text[:22]), got == want, got)

    print("\n【3】retryable()：非可重试的四类，一律别赌")
    for text, kw in [("Error code: 400 - duplicate field", "请求被上游拒绝"),
                     ("Error code: 401 - invalid api key", "鉴权失败"),
                     ("Error code: 404 - no such model", "模型不存在"),
                     ("finish_reason=length", "截断")]:
        ok, why = ec.retryable(text)
        check("E4 不重试：" + text[:26], ok is False and kw in why, (ok, why))
        check("E4b 说清怎么处置：" + text[:26],
              any(k in why for k in ("处理", "调大", "检查")), why)

    print("\n【4】retryable()：偶发面保持可重试（不擅自收紧）")
    for text in ("Error code: 503 upstream unavailable", "ReadTimeout: 连接超时",
                 "something odd happened", ""):
        ok, why = ec.retryable(text)
        check("E5 仍可重试：" + (text[:24] or "(空)"), ok is True and why == "", (ok, why))

    print("\n【5】should_auto_retry 接入 error_text")
    ok, why = orchestrator.should_auto_retry({"auto_retry": True}, 0, False,
                                            "Error code: 400 - duplicate field")
    check("E6 400 报文 → 不重试", ok is False and "请求" in why, (ok, why))
    ok, why = orchestrator.should_auto_retry({"auto_retry": True}, 0, False)
    check("E6b 不传 error_text → 旧行为（重试）", ok is True and why == "", (ok, why))
    ok, why = orchestrator.should_auto_retry({"auto_retry": True}, 0, False,
                                            "Error code: 503 upstream unavailable")
    check("E6c 503 报文 → 仍重试", ok is True, (ok, why))

    print("\n【6】stage_error_text / first_failure：阶段级 + 逐章级都读")
    tmp = tempfile.mkdtemp()
    pm = ProgressManager(str(Path(tmp) / "progress.json"))
    pm.set_stage(4, "failed", failed_count=2)
    pm.mark_chapter_failed(1, "子会话失败（HTTP 400）：duplicate field", 4)
    pm.mark_chapter_failed(2, "子会话失败（HTTP 400）：duplicate field", 4)
    text = ec.stage_error_text(pm, 4)
    check("E7 取到逐章失败原因", "HTTP 400" in text, text)
    pm2 = ProgressManager(str(Path(tmp) / "progress2.json"))
    pm2.set_stage(2, "failed", error="结构校验失败: 第二节缺失")
    check("E7b 取到阶段级 error", "结构校验失败" in ec.stage_error_text(pm2, 2))
    check("E7c first_failure 给一句人话", "HTTP 400" in ec.first_failure(pm, 4))

    print("\n【7】接线：stage 失败分支不再写死“子会话退出码非零”")
    for fname in ("stage1_consolidate.py", "stage2_outline.py",
                  "stage4_writing.py", "stage5_check.py",
                  "utils/enhanced_chapter.py"):
        src = (ROOT / "scripts" / fname).read_text(encoding="utf-8")
        check("E8 %s 用了 error_contract" % fname, "error_contract" in src)
    src4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("E8b stage4 的失败原因来自 describe(result)", "describe(result)" in src4)
    check("E8c stage4 阶段级 failed 带 error（不是只有 failed_count）",
          'error=_first_why or None' in src4)
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("E8d orchestrator 把失败原因喂给重试判据",
          "error_contract.stage_error_text(progress, n)" in orch)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
