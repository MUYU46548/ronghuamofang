# -*- coding: utf-8 -*-
"""L2「一句话改一段」的判据 —— 源码契约 + 真跑 charDiff（零 token）。

## 这条判据守的是什么

竞品对表（`docs/功能建议-20261113.md` 第 1 条）把 L2 拆成五件，墨坊缺三件：
预设档只填不发、只出一条候选、差异只有段落级。这一轮补的是「点了就发」与「字符级差异」。

判据分两段：
  A 源码契约 —— 档位点下去真的提交；误点护栏（首次提示 / 只填入开关 / self 引擎不提交）都在；
     候选两栏用字符级 diff 渲染（不是并排全文）。
  B 真跑 charDiff —— 用 node 直接跑 `console/src/diff.js`，证明它真的标出了增删的那几个字
     （源码里写了 import 不等于能用；这里要的是输出）。
"""
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "console" / "src"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


def read(name):
    return (SRC / name).read_text(encoding="utf-8")


def func_body(src, name):
    m = re.search(r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    i = src.find("{", m.end())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    return src[i:]


def case_contract():
    print("\n[A] 档位点了就发 + 误点护栏")
    ce = read("ChapterEditor.vue")

    m = re.search(r"const QUICK_EDITS = \[(.*?)\n\];", ce, re.S)
    labels = re.findall(r'label: "([^"]+)"', m.group(1)) if m else []
    check("A1 档位够用（≥5 个，含更短/更多描写/去掉总结句）",
          len(labels) >= 5 and {"更短", "更多描写", "去掉总结句"} <= set(labels), labels)

    qe = func_body(ce, "quickEdit")
    check("A2 档位真的提交（不是只填进输入框）", "submitRefine()" in qe, qe[:200])
    check("A3 没选段落时明说怎么做（不静默无反应）", "先" in qe and "selPara.value === null" in qe)
    check("A4 首次点击有一次确认（误点=一次调用）",
          "window.confirm" in qe and "mofang_quick_warned" in ce)
    check("A5 “只填入”开关存在（想改字的人有退路）",
          "quickFillOnly" in qe and "setQuickFillOnly" in ce and "mofang_quick_fill_only" in ce)
    check("A6 自任务引擎下不提交（那个端点必然拒绝，不许假装能用）",
          "props.selfEngine" in qe and "SELF_HINT" in ce)

    tmpl = ce[ce.find("<template>"):]
    check("A7 模板绑的是 quickEdit（不是 feedback = q.text）",
          "@click=\"quickEdit(q)\"" in tmpl and "@click=\"feedback = q.text\"" not in tmpl)
    check("A8 候选两栏用字符级 diff 渲染",
          "candDiff.before" in tmpl and "candDiff.after" in tmpl
          and "ce-del" in tmpl and "ce-ins" in tmpl)
    check("A9 字符级 diff 复用 diff.js 的 charDiff（不另写一份）",
          'import { charDiff } from "./diff.js"' in ce and "charDiff(b, a)" in ce)
    check("A10 差异配色走主题变量（暗色主题不许看不清）",
          ".ce-del" in ce and "var(--bad)" in ce and "var(--ok)" in ce)


def case_char_diff_runs():
    print("\n[B] 真跑 charDiff（node）")
    node = shutil.which("node")
    if not node:
        check("B0 node 可用（跑不了就不假装跑过）", False, "PATH 里没有 node")
        return
    script = (
        'import(%s).then((m) => {\n'
        "  const ops = m.charDiff('他推开门，屋里的灯还亮着。', '他推开门，屋里的灯还亮着，灯泡嗡嗡响。');\n"
        "  const add = ops.filter(o => o.op === 'add').map(o => o.text).join('');\n"
        "  const del = ops.filter(o => o.op === 'del').map(o => o.text).join('');\n"
        "  const same = ops.filter(o => o.op === '=').map(o => o.text).join('');\n"
        "  console.log(JSON.stringify({ add, del, same }));\n"
        "}).catch((e) => { console.log('ERR ' + e.message); });\n"
        % json.dumps((SRC / "diff.js").as_uri())
    )
    p = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    out = (p.stdout or "").strip()
    ok = out.startswith("{")
    check("B1 node 能加载 diff.js 并返回", ok, out[:200] or (p.stderr or "")[:200])
    if not ok:
        return
    d = json.loads(out)
    check("B2 新加的字被标出来（，灯泡嗡嗡响）", "灯泡嗡嗡响" in d["add"], d)
    check("B3 没改的部分原样保留（不被当成全删全加）",
          d["same"].startswith("他推开门") and len(d["same"]) >= 10, d["same"])
    check("B4 删除集合为空（这一段只增不删）", d["del"] == "", d["del"])

    # 反向：改写一句，应当同时出现 del 与 add
    script2 = (
        'import(%s).then((m) => {\n'
        "  const ops = m.charDiff('她把三句标准念了一遍。', '她把那三行标准念了两遍。');\n"
        "  const add = ops.filter(o => o.op === 'add').map(o => o.text).join('');\n"
        "  const del = ops.filter(o => o.op === 'del').map(o => o.text).join('');\n"
        "  console.log(JSON.stringify({ add, del }));\n"
        "});\n" % json.dumps((SRC / "diff.js").as_uri())
    )
    p2 = subprocess.run([node, "--input-type=module", "-e", script2],
                        capture_output=True, text=True, encoding="utf-8", timeout=120)
    out2 = (p2.stdout or "").strip()
    d2 = json.loads(out2) if out2.startswith("{") else {}
    check("B5 改写句同时有增有删（不是只标一边）",
          bool(d2.get("add")) and bool(d2.get("del")), out2[:200])


for fn in (case_contract, case_char_diff_runs):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
