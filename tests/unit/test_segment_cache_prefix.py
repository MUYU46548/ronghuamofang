# -*- coding: utf-8 -*-
"""多文件任务的**统一请求头** / 前缀缓存自检（P1 缓存，2026-10-03）。零 LLM。

## 为什么需要

多文件输出任务会被 `segment_requests` 拆成 N 个请求。它们的正文（任务说明 +
内联输入）逐字节相同，**只有尾部那句「本次只处理第 i/N 个」不同**。
模型的**前缀缓存**按「请求开头逐字相同」命中 —— 写作阶段的输入量远大于输出量，
所以前缀里**一个字符都不能变**，一变就是 N 份独立前缀（省钱大头就在这里）。

本项目已经踩过两次：
  ① 2026-09-29：指令**头插**在 user 消息最前 → 分叉点在第 15 个字符，
     后面 55 行要求全部作废，命中率上限 1.2%；
  ② 2026-10-03：指令虽然移尾了，但指令**自己**还是「第 1/3 个…」打头 →
     分叉点落在指令第 3 个字符，指令后半段那句「严禁输出其他文件的内容块」
     每个子请求各成一份独立前缀（本用例就是为了钉死这一条）。

判据是**结构性的**：量公共前缀长度、看变量出现在哪里。谁再把变量挪到前部，
本用例立刻红。

用法：python tests/unit/test_segment_cache_prefix.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.llm_client import (SEGMENT_HEAD, common_prefix_len,  # noqa: E402
                              segment_requests)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


# 拟真 body：任务说明 + 内联输入（越长越接近真实；分叉代价按绝对字符数算）
BODY = ("# 阶段 5 任务：全书逻辑检查（分批 1/3）\n\n"
        "你是专业的文学作者兼严谨审校。\n\n"
        "## 输入文件（用 read_file 读取）\n- 设定集: /x/data/setting/setting.json\n\n"
        "## 核心任务：系统化检查逻辑漏洞\n" + ("检查项说明。" * 400) + "\n\n"
        "## 内联输入\n" + ("===== 文件: /x/data/chapters/raw/01.md =====\n" + "正文。" * 900))

WRITES = ["/x/data/chapters/checked/01.md", "/x/data/chapters/checked/02.md",
          "/x/data/chapters/checked/03.md"]


def case_prefix_shared():
    print("\n【1】N 个子请求共享**整段正文**前缀（分叉只在尾部指令里）")
    reqs = segment_requests(BODY, WRITES, [])
    prompts = [r[0] for r in reqs]
    check("拆成 3 个请求", len(reqs) == 3, len(reqs))
    check("每个请求只带自己的 1 个输出", all(len(r[1]) == 1 for r in reqs))
    cpl = common_prefix_len(prompts)
    check("公共前缀 ≥ 正文长度（正文逐字节共享）", cpl >= len(BODY.rstrip()) - 2,
          "共用 %d 字符 / 正文 %d" % (cpl, len(BODY.rstrip())))
    ratio = cpl / min(len(p) for p in prompts)
    check("公共前缀占比 ≥ 99%（分叉代价可忽略）", ratio >= 0.99,
          "%.4f" % ratio)
    # 反证：若把变量挪回前部（旧写法），占比会崩 —— 判据必须真的能分辨
    old = [("第 %d/%d 个【本次调用仅处理第 %d 个输出文件：%s】" % (i, 3, i, w)) + BODY
           for i, w in enumerate(WRITES, 1)]
    check("反证：变量前置时占比暴跌（说明本判据有效）",
          common_prefix_len(old) / min(len(p) for p in old) < 0.05,
          "%.4f" % (common_prefix_len(old) / min(len(p) for p in old)))


def case_head_is_suffix():
    print("\n【2】统一请求头必须**整段**是后缀（前面一律不许有差异）")
    for i, (prompt, _w, _a) in enumerate(segment_requests(BODY, WRITES, []), 1):
        check("第 %d 个：指令在最末（其后无内容）" % i,
              prompt.rstrip().endswith("】") and prompt.count("本次输出文件") == 1,
              prompt[-60:])
        check("第 %d 个：变量（文件路径）出现在指令最后" % i,
              prompt.rstrip().endswith(WRITES[i - 1] + "】"), prompt[-60:])
    prompts = [r[0] for r in segment_requests(BODY, WRITES, [])]
    cpl = common_prefix_len(prompts)
    # 模板渲染后的**常量段**（{index} 之前的一切）；cpl 必须覆盖到它末尾
    const = SEGMENT_HEAD.split("{index}")[0].format(total=len(WRITES))
    check("指令里的常量说明**全部**落在公共前缀内（这是本轮的修复点）",
          prompts[0][:cpl].endswith(const), "cpl=%d 前缀尾=%r 常量尾=%r"
          % (cpl, prompts[0][max(0, cpl - 24):cpl], const[-24:]))
    check("「严禁输出其他文件的内容块」落在公共前缀内",
          "严禁输出其他文件的内容块" in prompts[0][:cpl])
    check("分叉点之后只剩「第 i/N 个 + 路径」",
          prompts[0][cpl:].startswith("1/3") or "1/3" in prompts[0][cpl:cpl + 12],
          repr(prompts[0][cpl:cpl + 40]))
    check("分叉点距请求末尾 ≤ 120 字符（尾部开销可控）",
          min(len(p) for p in prompts) - cpl <= 120,
          min(len(p) for p in prompts) - cpl)


def case_single_and_zero():
    print("\n【3】单文件 / 无输出：不得无谓拆分（拆了反而多烧钱）")
    r1 = segment_requests(BODY, ["/x/a.md"], [])
    check("单输出 → 1 个请求", len(r1) == 1, len(r1))
    check("单输出 → 正文**原样**（不加任何指令尾巴）", r1[0][0] == BODY, r1[0][0][-40:])
    r0 = segment_requests(BODY, [], ["/x/report.md"])
    check("无 writes（只有 append）→ 1 个请求，append 目标照旧带上",
          len(r0) == 1 and r0[0][2] == ["/x/report.md"], r0[0][2])
    r2 = segment_requests(BODY, WRITES, ["/x/report.md"])
    check("append 目标对每个子请求都保留（报告要累积）",
          all(r[2] == ["/x/report.md"] for r in r2), [r[2] for r in r2])


def case_deterministic():
    print("\n【4】顺序确定性（同一输入两次拆分必须逐字节相同）")
    a = [r[0] for r in segment_requests(BODY, WRITES, [])]
    b = [r[0] for r in segment_requests(BODY, WRITES, [])]
    check("两次拆分完全相同（缓存命中要求稳定）", a == b)
    check("第 i 个请求指向第 i 个文件（不串位）",
          all(a[i].rstrip().endswith(WRITES[i] + "】") for i in range(3)),
          [x[-30:] for x in a])


def main():
    print("=" * 62)
    print("  多文件任务统一请求头 / 前缀缓存自检")
    print("=" * 62)
    case_prefix_shared()
    case_head_is_suffix()
    case_single_and_zero()
    case_deterministic()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
