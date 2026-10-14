# -*- coding: utf-8 -*-
"""丙4 大纲分段审批回归（切分 / 状态 / 判据 / CLI / 接线 / GUI 侧成对）。

守四条契约（utils/outline_segments 模块 docstring）：
1. 旧审核不为新内容背书 —— global.md 一改，全部分段打回未通过
2. 切不出 2 段时闸**不拦** —— 老流程/测试替身零破坏
3. 判据只有一份 —— CLI 与 HTTP 同调 gate_status（本文件验 CLI 端到端 + 静态接线）
4. `gates.segment_approval` 键缺失 = 保持改动前行为

分段粒度按幕（用户拍板）→ 实现按 global.md 的 `## ` 切；stage2 模板固定产出
起/承/转/合/… 即天然退化成按大节，本用例用真实模板结构当夹具。

全程离线、零 token、只动临时目录。
用法：python tests/unit/test_outline_segments.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
sys.path.insert(0, str(REPO / "scripts"))

from utils.outline_segments import (gate_status, split_segments,  # noqa: E402
                                    set_segment, summary, sync, STATE_REL)

PASS, FAIL = [], []
TMP = None
ORIG_CWD = None

# 真实模板结构（prompts/stage2_global_outline.md 的输出格式）
TEMPLATE_OUTLINE = """# 《测试之书》整体大纲

## 起
开端：主角登场，冲突埋下。

## 承
承接：冲突升级，势力登场。

## 转
转折：真相揭露，关系翻转。

## 合
收束：主线闭合，余韵留白。

## 关键节点
- 第 5 章 码头对峙
- 第 12 章 身份揭晓

## 预计章节数
24 章

## 章节规划
分三卷，每卷 8 章。

## 要求
保持冷峻文风，避免说明文。
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  → " + str(detail)[:200] if detail else ""))


def setup(with_outline=True, system_yaml=None):
    global TMP, ORIG_CWD
    ORIG_CWD = os.getcwd()
    TMP = Path(tempfile.mkdtemp(prefix="nf_seg_"))
    os.chdir(TMP)
    (TMP / "data" / "outline").mkdir(parents=True)
    (TMP / "data" / "state").mkdir(parents=True)
    (TMP / "config").mkdir(parents=True)
    if with_outline:
        (TMP / "data" / "outline" / "global.md").write_text(
            TEMPLATE_OUTLINE, encoding="utf-8")
    # progress：阶段2 已完成（审批门的前置）
    (TMP / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": "测试之书",
                    "stages": {"2": {"status": "done", "approved": False}}}),
        encoding="utf-8")
    (TMP / "config" / "system.yaml").write_text(
        system_yaml if system_yaml is not None else
        "engine: direct\ngates:\n  agent_mode: false\n  segment_approval: true\n",
        encoding="utf-8")
    return TMP


def teardown():
    os.chdir(ORIG_CWD)
    shutil.rmtree(TMP, ignore_errors=True)


def run_script(rel, *args):
    p = subprocess.run([PY, str(REPO / "scripts" / rel)] + list(args),
                       cwd=str(TMP), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- 1. 切分
def test_split():
    print("\n[1] 分段切分（粒度=按幕，实为按 ## 大节）")
    segs = split_segments(TEMPLATE_OUTLINE)
    check("1.1 模板结构切成 8 段", len(segs) == 8, [s["id"] for s in segs])
    check("1.2 首段从 0 起（含 H1 大标题，卷首不落空）", segs[0]["start"] == 0,
          segs[0])
    check("1.3 标题取自 ## 行", segs[0]["title"] == "起" and segs[3]["title"] == "合",
          [s["title"] for s in segs])
    check("1.4 每段有 id/chars", all(s["id"].startswith("s") and s["chars"] > 0
                                     for s in segs))
    only_h1 = "# 甲\n\n内容\n\n# 乙\n\n内容\n"
    check("1.5 无 ## 时退到 # 切", len(split_segments(only_h1)) == 2,
          split_segments(only_h1))
    one = "只有一段，没有任何标题层级的散文。"
    got = split_segments(one)
    check("1.6 无标题 → 单段「（全文）」",
          len(got) == 1 and got[0]["title"] == "（全文）", got)
    check("1.7 空文本 → 空列表", split_segments("") == [] and split_segments("  ") == [])


# ---------------------------------------------------------------- 2/3 状态
def test_state_flow():
    print("\n[2] 首次建账 + 逐段通过")
    setup()
    try:
        st, reason = sync(TMP)
        check("2.1 首次建账 reason=first", reason == "first", reason)
        check("2.2 状态文件落在 data/state/", (TMP / STATE_REL).exists(), STATE_REL)
        sm = summary(TMP)
        check("2.3 共 8 段、已过 0", sm["total"] == 8 and sm["approved"] == 0, sm)
        check("2.4 未全过", sm["all_approved"] is False)

        ok, _ = set_segment(TMP, "s01", True)
        check("2.5 通过 s01", ok)
        sm = summary(TMP)
        check("2.6 进度 1/8", sm["approved"] == 1 and sm["pending"] == 7, sm["pending"])
        ok, _ = set_segment(TMP, "不存在的段", True)
        check("2.7 乱写段号 → 报错不静默", not ok)
        for i in range(2, 9):
            set_segment(TMP, "s%02d" % i, True)
        sm = summary(TMP)
        check("2.8 全部通过 → all_approved", sm["all_approved"] is True, sm)
        ok, _ = set_segment(TMP, "s01", False)
        sm = summary(TMP)
        check("2.9 撤销后回到未全过", ok and sm["all_approved"] is False
              and sm["approved"] == 7, sm["approved"])
        set_segment(TMP, "s01", True)
    finally:
        teardown()


# ---------------------------------------------------------------- 4. 内容变了
def test_invalidation():
    print("\n[3] 旧审核不为新内容背书")
    setup()
    try:
        sync(TMP)
        for i in range(1, 9):
            set_segment(TMP, "s%02d" % i, True)
        check("3.1 改之前已全过", summary(TMP)["all_approved"] is True)
        # 大纲改一个字
        p = TMP / "data" / "outline" / "global.md"
        p.write_text(p.read_text(encoding="utf-8").replace("码头对峙", "码头对峙改"),
                     encoding="utf-8")
        st, reason = sync(TMP)
        sm = summary(TMP)
        check("3.2 内容变了 → reason=doc_changed", reason == "doc_changed", reason)
        check("3.3 全部打回未通过", sm["approved"] == 0 and not sm["all_approved"],
              sm["approved"])
        check("3.4 作废次数记账 +1", sm["invalidations"] == 1, sm["invalidations"])
        # 没改时不得误伤
        for i in range(1, 9):
            set_segment(TMP, "s%02d" % i, True)
        st, reason = sync(TMP)
        check("3.5 未改动时保留已通过（不许每读一次就作废）",
              reason == "unchanged" and summary(TMP)["all_approved"], reason)
    finally:
        teardown()


# ---------------------------------------------------------------- 5. 放行判据
def test_gate():
    print("\n[4] 放行判据 gate_status（单源）")
    setup()
    try:
        sync(TMP)
        ok, why = gate_status(TMP, cfg={"gates": {"segment_approval": True}})
        check("4.1 未全过 → 拦下", ok is False)
        check("4.2 报错给三件事：差几段 / 用哪个 CLI / 下一条命令",
              "还有 8/8 段未通过" in why and "segment_cli" in why
              and "approve.py --stage 2" in why, why[:220])
        for i in range(1, 9):
            set_segment(TMP, "s%02d" % i, True)
        ok, _ = gate_status(TMP, cfg={"gates": {"segment_approval": True}})
        check("4.3 全过 → 放行", ok is True)
    finally:
        teardown()

    # 契约 2 / 4：切不出两段、键缺失、显式关掉 → 一律不拦
    # （每段各自 setup/teardown：嵌套 setup 会把 ORIG_CWD 覆盖成上一个临时根）
    setup(with_outline=False)
    try:
        (TMP / "data" / "outline" / "global.md").write_text(
            "只有一整篇，没有任何标题层级。\n", encoding="utf-8")
        ok, _ = gate_status(TMP, cfg={"gates": {"segment_approval": True}})
        check("4.4 单段大纲 → 闸不拦（等同整本审批）", ok is True)

        os.remove(str(TMP / "data" / "outline" / "global.md"))
        ok, _ = gate_status(TMP, cfg={"gates": {"segment_approval": True}})
        check("4.5 无大纲 → 不拦（不是分段审批能管的事）", ok is True)
    finally:
        teardown()

    setup()          # 有 8 段、全 pending —— 若忽略开关就该拦，这样 4.6/4.7 才有意义
    try:
        sync(TMP)
        ok, _ = gate_status(TMP, cfg={"gates": {}})
        check("4.6 键缺失 = 保持改动前行为（不拦）", ok is True)
        ok, _ = gate_status(TMP, cfg={"gates": {"segment_approval": False}})
        check("4.7 显式 false = 退回整本一次审（不拦）", ok is True)
        ok, _ = gate_status(TMP, cfg={"gates": {"segment_approval": True}}, stage=1)
        check("4.8 非阶段2 不管", ok is True)
    finally:
        teardown()


# ---------------------------------------------------------------- 6. CLI
def test_cli():
    print("\n[5] CLI 端到端（segment_cli / approve.py）")
    setup()
    try:
        rc, out = run_script("segment_cli.py", "list")
        check("5.1 list rc=0 且报 0/8", rc == 0 and "已通过 0/8" in out, out[:200])
        rc, out = run_script("segment_cli.py", "approve", "s01")
        check("5.2 approve 生效并报进度 1/8",
              rc == 0 and "进度：1/8" in out, out[:200])
        rc, out = run_script("segment_cli.py", "status")
        check("5.3 未全过时 status rc=1 并说清还不行",
              rc == 1 and "还不行" in out, (rc, out[:160]))

        # —— 闸：先判后写 ——
        rc, out = run_script("approve.py", "--stage", "2")
        check("5.4 分段未全过 → approve.py 拒绝放行（rc=1）",
              rc == 1 and "拒绝放行阶段2" in out, (rc, out[:240]))
        prog = json.loads((TMP / "data" / "state" / "progress.json")
                          .read_text(encoding="utf-8"))
        check("5.5 先判后写：approved 一个字节都没动",
              prog["stages"]["2"].get("approved") is False, prog["stages"]["2"])

        for i in range(2, 9):
            run_script("segment_cli.py", "approve", "s%02d" % i)
        rc, out = run_script("approve.py", "--stage", "2")
        check("5.6 全部分段通过后放行（rc=0）", rc == 0, (rc, out[:240]))
        prog = json.loads((TMP / "data" / "state" / "progress.json")
                          .read_text(encoding="utf-8"))
        check("5.7 放行后 approved=true", prog["stages"]["2"].get("approved") is True,
              prog["stages"]["2"])
        check("5.8 放行前打了审批快照（既有护栏未被绕过）",
              any((TMP / "history").glob("*approve_stage2*")),
              list((TMP / "history").glob("*approve_stage2*")))
    finally:
        teardown()


def test_switch_off():
    print("\n[6] 开关：键为 false 时退回整本一次审")
    setup(system_yaml="engine: direct\ngates:\n  agent_mode: false\n"
                      "  segment_approval: false\n")
    try:
        sync(TMP)
        rc, out = run_script("approve.py", "--stage", "2")
        check("6.1 segment_approval=false → 未全过也放行（rc=0）", rc == 0,
              (rc, out[:200]))
    finally:
        teardown()


def test_wiring():
    print("\n[7] 接线：判据单源（CLI 与 HTTP 同调一份）")
    api = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("7.1 nf_api.act_approve 调 gate_status（HTTP 侧同一份判据）",
          "gate_status" in api and "分段审批未全过" in api)
    appr = (REPO / "scripts" / "approve.py").read_text(encoding="utf-8")
    check("7.2 approve.py 调 gate_status 且在快照之前",
          appr.find("gate_status") != -1
          and appr.find("gate_status") < appr.find("make_snapshot"))
    check("7.3 两处都不各自实现判据（只 import 不重写）",
          appr.count("def gate_status") == 0 and api.count("def gate_status") == 0)
    cfg = (REPO / "config" / "system.yaml").read_text(encoding="utf-8")
    check("7.4 本仓已显式开启 segment_approval", "segment_approval: true" in cfg)


def test_gui_wiring():
    """[8] GUI 侧接线 —— 闸与界面必须成对（丙4 上一轮的落差就出在这）。

    上一轮交付时闸已开着、分段审阅界面还没做：用户在界面上点「确认放行」
    只会吃到一个 409，看不到差哪几段。这些断言钉住「界面在场 + 写侧有闸」，
    防止又一次只交付一半。
    """
    print("\n[8] GUI 侧接线（分段审阅界面 + 写侧闸必须成对）")
    from utils import agent_guard
    seg = (REPO / "scripts" / "nf_api_domains" / "segments.py").read_text(encoding="utf-8")
    api = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("8.1 读写两侧住在域模块（不内联回 do_GET/do_POST）",
          seg.count("def handle_segments_") == 2, seg.count("def handle_segments_"))
    check("8.2 do_GET 有 /outline/segments 转发", 'p == "/outline/segments"' in api)
    check("8.3 do_POST 有 /outline/segments/set 转发",
          'p == "/outline/segments/set"' in api)
    check("8.4 写侧列入 FORBIDDEN_IN_AGENT_MODE（可读不可勾）",
          agent_guard.is_forbidden_in_agent_mode("/outline/segments/set") is True)
    check("8.5 读侧不拦（否则 Agent 看不到差哪几段）",
          agent_guard.is_forbidden_in_agent_mode("/outline/segments") is False)
    check("8.6 放行判据不复述（调 gate_status，不 def gate_status）",
          "gate_status" in seg and "def gate_status" not in seg)
    check("8.7 双因子判据不重写（复用 project._gui_only，不自调 is_trusted_gui）",
          "from nf_api_domains.project import _gui_only" in seg
          and "is_trusted_gui(" not in seg)
    app = (REPO / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("8.8 前端有分段审阅面板", 'class="seg-review"' in app)
    check("8.9 前端真的会提交勾选（走写侧端点）", '"/outline/segments/set"' in app)
    check("8.10 面板跟着 stage2 门自动加载（不是藏在某个按钮后面）",
          "loadSegReview()" in app and "pendingGates.value.some" in app)
    css = (REPO / "console" / "src" / "style.css").read_text(encoding="utf-8")
    blk = css[css.find(".seg-review"):] if ".seg-review" in css else ""
    check("8.11 样式在场且走主题变量（硬编码色值在暗色主题下看不清）",
          ".seg-review" in blk and "var(--" in blk and "#9e4f4f" not in blk)


def main():
    test_split()
    test_state_flow()
    test_invalidation()
    test_gate()
    test_cli()
    test_switch_off()
    test_wiring()
    test_gui_wiring()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
