# -*- coding: utf-8 -*-
"""llm_client 解析链 mock 集成测试（T1-T8）。运行：runpy，cwd=项目根。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)

from utils import llm_client as lc  # noqa: E402
from utils.file_io import read_text, write_text  # noqa: E402

ok_count = 0
fails = []


def check(name, cond, info=""):
    global ok_count
    if cond:
        ok_count += 1
        print("PASS", name)
    else:
        fails.append(name)
        print("FAIL", name, "--", str(info)[:200])


BS = chr(92)
NEWLINE = chr(10)

# ---- T1 extract_input_paths（stage4 形态）----
body = ("---" + NEWLINE + "stage: 4" + NEWLINE + "---" + NEWLINE +
        "# 任务" + NEWLINE +
        "## 输入文件（用 read_file 读取）" + NEWLINE +
        "- 本章大纲: E:" + BS + "NF" + BS + "data" + BS + "outline" + BS +
        "chapters" + BS + "01.md" + NEWLINE +
        "- 设定集: E:" + BS + "NF" + BS + "data" + BS + "setting" + BS +
        "setting.json（仅参考涉及本章条目）" + NEWLINE +
        "- 项目配置: E:" + BS + "NF" + BS + "config" + BS + "project.yaml" + NEWLINE +
        "## 写作要求" + NEWLINE +
        "将正文写入文件: E:" + BS + "NF" + BS + "data" + BS + "chapters" + BS +
        "raw" + BS + "01.md" + NEWLINE)
refs = lc.extract_input_paths(body)
check("T1 输入路径数=3", len(refs) == 3, str(refs))
check("T1 大纲路径含多段", any(("outline" + BS + "chapters" + BS + "01.md") in p for p, _ in refs), str(refs))
check("T1 setting含括号说明可解析", any(p.endswith("setting.json") for p, _ in refs), str(refs))

# ---- T2 inline_inputs 真实文件 ----
NEWLINE = chr(10)
tmp = Path("data/test_llm_t2")
if tmp.exists():
    import shutil
    shutil.rmtree(tmp)
tmp.mkdir(parents=True, exist_ok=True)
write_text(tmp / "o.md", "第一章大纲内容XYZ")
write_text(tmp / "s.json", '{"k": 1}')
body2 = ("## 输入文件（用 read_file 读取）" + NEWLINE +
         "- 本章大纲: " + str(tmp / "o.md") + NEWLINE +
         "- 设定集: " + str(tmp / "s.json") + "（说明）" + NEWLINE +
         "## 输出" + NEWLINE +
         "写入: " + str(tmp / "out.md") + NEWLINE)
nb, missing = lc.inline_inputs(body2)
check("T2 无缺失", missing == [], str(missing))
check("T2 内联区存在", "## 内联输入" in nb)
check("T2 内容进入", "第一章大纲内容XYZ" in nb and '"k": 1' in nb)
check("T2 read_file提示替换", ("（用 read_file 读取）" not in nb) and ("直连引擎" in nb))
check("T2 输出路径保留", ("写入: " + str(tmp / "out.md")) in nb)

# ---- T3 parse_ops ----
model_out = ("好的，以下是文件。" + NEWLINE +
             "===FILE: E:" + BS + "NF" + BS + "data" + BS + "chapters" + BS +
             "raw" + BS + "01.md===" + NEWLINE +
             "## 第1章 测试" + NEWLINE + NEWLINE + "正文段落。" + NEWLINE + NEWLINE +
             "<!-- quality: 8/10 -->" + NEWLINE + "===END===" + NEWLINE)
ops = lc.parse_ops(model_out)
check("T3 ops数=1", len(ops) == 1, str(ops))
check("T3 路径尾", ops and ops[0][1].endswith("01.md"), str(ops))
check("T3 内容完整", ops and ("## 第1章 测试" in ops[0][2]) and ("quality: 8/10" in ops[0][2]))

model_out2 = ("===FILE: E:" + BS + "NF" + BS + "data" + BS + "setting" + BS +
              "x.json===" + NEWLINE + "```json" + NEWLINE + '{"a": 2}' + NEWLINE +
              "```" + NEWLINE + "===END===" + NEWLINE)
ops2 = lc.parse_ops(model_out2)
c2 = ops2[0][2] if ops2 else ""
check("T3b 围栏剥离", c2.strip().startswith("{") and "```" not in c2, repr(c2))

model_out3 = ("===FILE: E:" + BS + BS + "NF" + BS + BS + "data" + BS + BS +
              "a.md===" + NEWLINE + "内容A" + NEWLINE + "===END===" + NEWLINE +
              "===APPEND: E:" + BS + "NF" + BS + "data" + BS + "report.md===" +
              NEWLINE + "- 修改1" + NEWLINE + "===END===" + NEWLINE)
ops3 = lc.parse_ops(model_out3)
check("T3c 双反斜杠归一", ops3 and ops3[0][1] == ("E:" + BS + "NF" + BS + "data" + BS + "a.md"), str(ops3))
check("T3c APPEND解析", len(ops3) == 2 and ops3[1][0] == "APPEND", str(ops3))

# ---- T4 expected_outputs 排除输入段 ----
writes, appends = lc.OpenAICompatClient._expected_outputs(None, nb)
check("T4 只剩输出路径", writes == [str(tmp / "out.md")], str(writes))
check("T4 appends空", appends == [], str(appends))

# ---- T5 segment_requests ----
b5 = "## 输出" + NEWLINE + "写入: A.md" + NEWLINE + "写入: B.md" + NEWLINE + "写入: C.md" + NEWLINE
reqs = lc.segment_requests(b5, ["A.md", "B.md", "C.md"], [])
check("T5 拆3请求", len(reqs) == 3, str(len(reqs)))
check("T5 各含1写", all(len(r[1]) == 1 for r in reqs))
check("T5 头部指令", "仅处理第 2/3" in reqs[1][0], reqs[1][0][:80])
reqs1 = lc.segment_requests(b5, ["A.md"], [])
check("T5 单文件不拆", len(reqs1) == 1)

# ---- T6 白名单 ----
check("T6 data允许", lc.allowed_paths(["data" + BS + "x.md"]) == [])
check("T6 越权拒绝", lc.allowed_paths(["prompts" + BS + "x.md"]) != [])

# ---- T7 snap ----
check("T7 按名贴齐", lc.snap_to_expected("E:" + BS + "任意" + BS + "01.md",
                                          "data" + BS + "chapters" + BS + "01.md") ==
      ("data" + BS + "chapters" + BS + "01.md"))
check("T7 不同名不贴", lc.snap_to_expected("02.md", "01.md") is None)

# ---- T8 mock run_task 全链（多文件拆分 + append + usage 记账）----
import shutil
import json

# 安全护栏：测试前快照（测试会清理 data/test_llm_t2 和 data/test_llm_t8）
try:
    from snapshot import snapshot as make_snapshot
    snap = make_snapshot("llm_client_selftest_wipe")
    print(f"[llm_client_selftest] 快照已保存: {snap}")
except Exception as e:
    print(f"[llm_client_selftest] 快照失败（继续执行）: {e}")

for d in ("data/test_llm_t2", "data/test_llm_t8"):
    if Path(d).exists():
        shutil.rmtree(d)
tmp = Path("data/test_llm_t8")
tmp.mkdir(parents=True, exist_ok=True)
fake_responses = iter([
    "说明。" + "===FILE: " + str(tmp / "f1.md") + "===" + NEWLINE + "F1内容" +
    NEWLINE + "===END===",
    "===FILE: " + str(tmp / "f2.md") + "===" + NEWLINE + "F2内容" + NEWLINE +
    "===END===" + NEWLINE + "===APPEND: " + str(tmp / "rep.md") + "===" + NEWLINE +
    "- 追加行" + NEWLINE + "===END===",
])


class MockLC(lc.OpenAICompatClient):
    def _post_chat(self, messages, temperature=None, max_tokens=None):
        # 返回 4 元组（2026-09-23 起第 4 位是 finish_reason）
        return (next(fake_responses), {"prompt_tokens": 100, "completion_tokens": 50},
                "mock-model", "stop")


task = Path("data/test_llm_t8/mock_task.md")
write_text(task, "## 输出" + NEWLINE +
           "写入: " + str(tmp / "f1.md") + NEWLINE +
           "写入: " + str(tmp / "f2.md") + NEWLINE +
           "追加到: " + str(tmp / "rep.md") + NEWLINE)
os.environ["NOVELFORGE_DEBUG"] = ""
c = MockLC(model="mock-model", base_url="http://localhost:1", api_key="x")
res = c.run_task(task)
check("T8 返回结构", res["exit_code"] == 0 and res["tokens"] == 200 and
      res["tokens_out"] == 100 and res["model"] == "mock-model" and
      res["requests"] == 2, str(res))
check("T8 f1落盘", (tmp / "f1.md").exists() and "F1内容" in read_text(tmp / "f1.md"))
check("T8 f2落盘", (tmp / "f2.md").exists() and "F2内容" in read_text(tmp / "f2.md"))
check("T8 append落盘", "追加行" in read_text(tmp / "rep.md"))
check("T8 真实usage标记", res["estimated"] is False)

print("====", ok_count, "PASS,", len(fails), "FAIL", fails)
sys.exit(1 if fails else 0)
