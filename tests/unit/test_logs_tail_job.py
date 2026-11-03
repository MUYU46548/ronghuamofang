# -*- coding: utf-8 -*-
"""/logs/tail 按 job 切片：别把上一次 run 的历史当成本次失败日志（P3，2026-11-03）。

## 要治的病

日志是一条**共享追加流**（Electron 把 nf_api / orchestrator 的 stdout 都追加进
同一个文件）。用户点“查看日志”时看到的是混杂输出：跑过三本书、二十个阶段之后，
"这次为什么失败"要靠人眼从几百行里挑。P3 清单里那条就是它。

## 做法

nf_api 在 job 起止各写一条边界行（`[job] ===== <id> start/end =====`），
`/logs/tail?job=<id>` 只返回这一段。切片是纯函数，可单测。

## 判据（每条都能变红）

- J1 有边界 → 只切出这一段（含边界行本身）
- J2 找不到 → 返回空列表（调用方据此说"没有这次的日志"，绝不回别人的）
- J3 同一个 id 出现两次（重跑）→ 取**最后一段**
- J4 没有 end 行（还在跑）→ 切到文件末尾
- J5 接线：nf_api 起止都写边界；/logs/tail 认 ?job=

用法：python tests/unit/test_logs_tail_job.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.run_log import extract_job_block, job_marker      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("/logs/tail 按 job 切片")

    a, b = "20261103_150040_da634c", "20261103_155512_aa11bb"
    lines = (
        ["旧 run 的输出 1", "旧 run 的输出 2"]
        + [job_marker(a, "start"), "A 的阶段输出", "A 的第二行", job_marker(a, "end", "failed")]
        + ["中间噪声"]
        + [job_marker(b, "start"), "B 的输出"]
    )

    print("\n【1】切出目标 job")
    got = extract_job_block(lines, a)
    check("J1 以 start 边界开头", got and got[0] == job_marker(a, "start"), got[:1])
    check("J1 以 end 边界结尾", got and got[-1] == job_marker(a, "end", "failed"), got[-1:])
    check("J1 不含别的 job 的行", all(not l.startswith("[job]") or "da634c" in l for l in got), got)
    check("J1 正文行在内", "A 的阶段输出" in got, got)

    print("\n【2】找不到就是找不到")
    check("J2 未知 id → 空列表", extract_job_block(lines, "20260101_000000_zzzzzz") == [])

    print("\n【3】重跑同一个 id 取最后一段")
    twice = [job_marker(a, "start"), "第一次", job_marker(a, "end", "failed"),
             "后来的东西", job_marker(a, "start"), "第二次", job_marker(a, "end", "ok")]
    got = extract_job_block(twice, a)
    check("J3 取最后一段（第二次）", "第二次" in got and "第一次" not in got, got)

    print("\n【4】还在跑（没有 end）")
    got = extract_job_block([job_marker(b, "start"), "进行中 1", "进行中 2"], b)
    check("J4 没有 end 行 → 切到末尾", got[-1] == "进行中 2" and len(got) == 3, got)

    print("\n【5】接线")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("J5 job 起止都写边界", api.count("job_marker(job_id") >= 2, api.count("job_marker(job_id"))
    misc = (ROOT / "scripts" / "nf_api_domains" / "misc.py").read_text(encoding="utf-8")
    check("J5 /logs/tail 认 ?job=", 'h._query().get("job")' in misc)
    check("J5 无边界时明确说没找到（不给别人的日志）",
          "日志里没有这次 job" in misc)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
