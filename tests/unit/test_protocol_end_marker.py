# -*- coding: utf-8 -*-
"""协议结束标记 + stage5 收尾自检的回归（2026-10-02 两个高危雷的钉子）。

## 雷是什么

**雷 ①（产物污染）**：`parse_ops` 旧实现要求「OP 块后面必须找得到 `END_RE`」，
找不到就**整块丢弃**。模型漏写结束标记时 ops 为空 → `_apply_ops` 的兜底把
**整段模型输出**（含 `===FILE:` 标记行与绝对路径）写进章节文件。
实证：`data/chapters/raw/01.md` 首行就是 `===FILE: E:\\...\\01.md===`。

**雷 ②（阶段空转仍报成功）**：`END_RE` 只认 `===END===`，而
`prompts/stage5_check.md` 教模型写 `===END FILE===` → stage5 每一批的块
**全部被丢弃** → 走「复制 raw → checked」兜底 → 打印「逻辑检查完成，报告:
data/outline/check_report.md」，**而该报告从未落盘**（实测不存在），
`checked/03.md` 与 `raw/03.md` 逐字节相同。

两个雷的共同教训：**判据写两遍（提示词一份、解析器一份）必然漂移**；
且「空结果」被当成「通过」。本文件把这两条都钉住。

用法：python tests/unit/test_protocol_end_marker.py（零 LLM、零网络）
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


# ====================================================================== P1

def case_end_re_variants():
    print("\n[P1] END_RE 必须认带 tag 的结束标记（stage5 就写的是 `===END FILE===`）")
    from utils.llm_client import END_RE
    # 允许的写法：END 后可跟一个或多个英文单词（`END FILE` / `END OF FILE`）
    yes = ["===END===", "===END FILE===", "===END FILE ===", "  ===END FILE===",
           "===  end  file  ===", "===END OF FILE==="]
    # 必须**不**认的：粘连词与非结束语义
    no = ["===ENDING===", "===END_FILE===", "===ENDS===", "END FILE"]
    bad = [t for t in yes if not END_RE.fullmatch(t)]
    over = [t for t in no if END_RE.fullmatch(t)]
    check("P1 带 tag 的结束标记被识别", not bad, f"→ 漏认 {bad}")
    check("P1 未被过度放宽（不把 ENDING / END_FILE / 裸文本当结束）", not over,
          f"→ 误认 {over}")
    check("P1 中文 tag 不识别属已知边界（此时靠 parse_ops 的块尾收边兜住，不丢块）",
          not END_RE.fullmatch("===END 结束==="))


# ====================================================================== P2

def case_parse_ops_mixed_markers():
    print("\n[P2] 两种结束标记混用时要全部解析出来")
    from utils.llm_client import parse_ops
    text = ("===FILE: data/outline/check_report.md===\n# 报告\n- 问题\n===END FILE===\n"
            "===FILE: data/chapters/checked/01.md===\n正文一\n===END===\n")
    ops = parse_ops(text)
    check("P2 解析出 2 个块（旧实现是 0）", len(ops) == 2, f"→ {len(ops)}")
    if len(ops) == 2:
        check("P2 块顺序与路径正确",
              ops[0][1].endswith("check_report.md") and ops[1][1].endswith("01.md"),
              f"→ {[o[1] for o in ops]}")
        check("P2 块内容不含结束标记残留",
              "===" not in ops[0][2] and "===" not in ops[1][2],
              f"→ {ops[0][2]!r} / {ops[1][2]!r}")


# ====================================================================== P3

def case_parse_ops_missing_end():
    print("\n[P3] 漏写结束标记时**不得整块丢弃**（雷 ① 的直接成因）")
    from utils.llm_client import parse_ops
    text = ("===FILE: data/chapters/raw/01.md===\n"
            "第1章正文\n第二段。\n")
    ops = parse_ops(text)
    check("P3 缺结束标记仍然解析出块（旧实现返回 []）", len(ops) == 1, f"→ {len(ops)}")
    if ops:
        content = ops[0][2]
        check("P3 **写出的内容不带头部 `===FILE:` 标记行**（旧实现整段照抄）",
              not content.lstrip().startswith("===FILE:"), f"→ {content[:60]!r}")
        check("P3 正文本身保留", "第1章正文" in content, f"→ {content[:60]!r}")

    # 两个块、只有前一个缺 END：第二个块不能被吞掉
    text2 = ("===FILE: a.md===\nA内容\n"
             "===FILE: b.md===\nB内容\n===END===\n")
    ops2 = parse_ops(text2)
    check("P3b 前块缺 END 不吞掉后块（仍 2 块）", len(ops2) == 2, f"→ {len(ops2)}")
    if len(ops2) == 2:
        check("P3b 前块内容不以 `===FILE:` 开头", not ops2[0][2].lstrip().startswith("===FILE:"),
              f"→ {ops2[0][2][:60]!r}")
        check("P3b 后块内容干净", ops2[1][2].strip() == "B内容", f"→ {ops2[1][2]!r}")


# ====================================================================== P4

def case_prompts_are_in_sync():
    print("\n[P4] 提示词里出现的结束标记必须全都能被解析器识别（防再次漂移）")
    from utils.llm_client import END_RE
    from utils.file_io import read_text
    pat = re.compile(r"^[^\r\n]*?(=== *END[^\r\n=]*===)", re.M | re.IGNORECASE)
    offenders = []
    for p in sorted((REPO / "prompts").glob("*.md")):
        for m in pat.finditer(read_text(p)):
            marker = m.group(1).strip()
            if not END_RE.fullmatch(marker):
                offenders.append(f"{p.name} → {marker}")
    check("P4 所有 prompts 的结束标记都能被 END_RE 识别", not offenders,
          "；".join(offenders[:5]))
    check("P4 stage5 已统一为 `===END===`",
          "===END FILE===" not in read_text(REPO / "prompts" / "stage5_check.md"))


# ====================================================================== P5

SANDBOX_FILES = ("stage5_check.py", "stage6_polish.py", "nf_api.py")


def build_sandbox(prefix="nf_proto_"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts" / "utils").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/chapters/raw", "data/chapters/checked", "data/outline",
                "data/state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for n in SANDBOX_FILES:
        s = REPO / "scripts" / n
        if s.exists():
            shutil.copy2(s, root / "scripts" / n)
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    shutil.copytree(REPO / "prompts", root / "prompts",
                    ignore=shutil.ignore_patterns("__pycache__", "history"),
                    dirs_exist_ok=True)
    for n in ("system.yaml", "project.yaml"):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    return root


BODY = "## 第1章 测试\n\n" + "\n\n".join(
    f"第{i}段。" + "他抬起头，风从窗口灌进来，吹动了桌上的纸。" * 3
    for i in range(1, 16)) + "\n"


def run_py(root, code, timeout=120):
    import os
    script = root / "_probe.py"
    script.write_text(code, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=str(root), timeout=timeout, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    out = p.stdout or ""
    if "__RESULT__" in out:
        try:
            return json.loads(out.split("__RESULT__", 1)[1].splitlines()[0]), out, p.stderr or ""
        except Exception:                                      # noqa: BLE001
            return None, out, p.stderr or ""
    return None, out, p.stderr or ""


STAGE5_PROBE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from pathlib import Path
import stage5_check
from utils.progress_manager import ProgressManager

MODE = os.environ["MODE"]

class Stub:
    provider = "stub"; model = "stub"
    def write_task(self, task_dir, name, content):
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p
    def run_task(self, task_file, workdir=None, model=None):
        if MODE in ("ok", "no_report"):
            src = Path("data/chapters/raw/01.md")
            dst = Path("data/chapters/checked/01.md")
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        if MODE == "ok":
            Path("data/outline").mkdir(parents=True, exist_ok=True)
            Path("data/outline/check_report.md").write_text(
                "# 逻辑检查报告\\n## 问题清单\\n- 无\\n", encoding="utf-8")
        return {"exit_code": 0, "stdout_tail": "", "tokens": 0, "tokens_out": 0,
                "cache_read": 0, "cost_yuan": 0.0}

progress = ProgressManager("data/state/progress.json")
ok, msg = stage5_check.run_stage({}, {}, progress, None, None,
                                 client=Stub(), task_dir="data/state/tasks")
st = (progress.data.get("stages") or {}).get("5") or {}
print("__RESULT__" + json.dumps(
    {"ok": bool(ok), "msg": msg, "warning": st.get("warning", ""),
     "status": st.get("status")}, ensure_ascii=False))
'''


def _stage5_scene(mode):
    root = build_sandbox()
    (root / "data" / "chapters" / "raw" / "01.md").write_text(BODY, encoding="utf-8")
    p, out, err = run_py(root, STAGE5_PROBE, )
    return p, out, err, root


def case_stage5_warns():
    print("\n[P5] stage5 兜底复制 / 报告缺失 → 必须显式告警（旧实现只打一行普通日志）")
    import os

    def run_mode(mode):
        root = build_sandbox()
        (root / "data" / "chapters" / "raw" / "01.md").write_text(BODY, encoding="utf-8")
        script = root / "_probe.py"
        script.write_text(STAGE5_PROBE, encoding="utf-8")
        e = dict(os.environ)
        e["MODE"] = mode
        try:
            pr = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                cwd=str(root), timeout=120, env=e)
        except subprocess.TimeoutExpired:
            return None, "", "TIMEOUT", root
        out = pr.stdout or ""
        if "__RESULT__" in out:
            try:
                return (json.loads(out.split("__RESULT__", 1)[1].splitlines()[0]),
                        out, pr.stderr or "", root)
            except Exception:                                  # noqa: BLE001
                return None, out, pr.stderr or "", root
        return None, out, pr.stderr or "", root

    # 场景 A：模型啥也没产出 → 兜底复制 + 报告缺失，两条警告都要有
    a, out_a, err_a, root_a = run_mode("silent")
    if a is None:
        check("P5 场景A 子进程产出结果", False, f"{out_a[-300:]} {err_a[-300:]}")
    else:
        check("P5 兜底复制会在返回消息里显式告警（不再只是行内日志）",
              "未经检查" in a["msg"], f"→ {a['msg'][:160]}")
        check("P5 报告缺失会被点出（旧实现照打「报告: check_report.md」）",
              "check_report.md" in a["msg"] and "缺失" in a["msg"], f"→ {a['msg'][:160]}")
        check("P5 警告写进 progress 状态（GUI/审计可见）",
              bool(a["warning"]), f"→ {a}")
        check("P5 仍然返回成功（章节产物可用，不误判整体失败）",
              a["ok"] is True and a["status"] == "done", f"→ {a}")
    shutil.rmtree(root_a, ignore_errors=True)
    print(f"  [print] 场景A stdout 摘要: …{out_a[-160:].strip()}")

    # 场景 B：章节有效但报告没写 → 只报报告缺失
    b, out_b, err_b, root_b = run_mode("no_report")
    if b is None:
        check("P5 场景B 子进程产出结果", False, f"{out_b[-300:]} {err_b[-300:]}")
    else:
        check("P5 章节有效时不再报「未经检查」", "未经检查" not in b["msg"],
              f"→ {b['msg'][:160]}")
        check("P5 但报告缺失仍然报出来", "check_report.md" in b["msg"],
              f"→ {b['msg'][:160]}")
    shutil.rmtree(root_b, ignore_errors=True)

    # 场景 C：章节与报告都齐 → 无警告，走正常 mark_stage_done
    c, out_c, err_c, root_c = run_mode("ok")
    if c is None:
        check("P5 场景C 子进程产出结果", False, f"{out_c[-300:]} {err_c[-300:]}")
    else:
        check("P5 全齐时没有警告（不制造噪音）",
              not c["warning"] and "⚠️" not in c["msg"], f"→ {c}")
    shutil.rmtree(root_c, ignore_errors=True)


# ====================================================================== P6

def case_strip_protocol_markers():
    print("\n[P6] 读取侧要能剥掉残留标记（防老产物把私路径带进成品）")
    from utils.llm_client import strip_protocol_markers
    dirty = ("===FILE: E:\\CODE\\X\\data\\chapters\\raw\\01.md===\n"
             "## 第1章 锈锁\n\n正文第一段。\n\n正文第二段。\n===END===\n")
    clean = strip_protocol_markers(dirty)
    check("P6 首行 `===FILE:` 标记被剥掉（私路径不再进成品）",
          "===FILE:" not in clean and "E:\\CODE" not in clean, f"→ {clean[:70]!r}")
    check("P6 结尾 `===END===` 被剥掉", "===" not in clean, f"→ {clean[-40:]!r}")
    check("P6 正文完整保留", "第1章 锈锁" in clean and "正文第二段。" in clean,
          f"→ {clean[:90]!r}")
    check("P6 没有标记的正常正文不受影响",
          strip_protocol_markers("## 第1章\n\n正文。\n") == "## 第1章\n\n正文。\n")
    # 正文里合法出现的 `==` 或比较符号不应被误伤
    keep = "他算了算：a == b，结果是 3。\n"
    check("P6 正文中的 `==` 不被误删", strip_protocol_markers(keep) == keep,
          f"→ {strip_protocol_markers(keep)!r}")


def main():
    print("=" * 72)
    print("协议结束标记 + stage5 收尾自检回归（两个高危雷）")
    print("=" * 72)
    case_end_re_variants()
    case_parse_ops_mixed_markers()
    case_parse_ops_missing_end()
    case_prompts_are_in_sync()
    case_stage5_warns()
    case_strip_protocol_markers()
    print("\n" + "=" * 72)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
