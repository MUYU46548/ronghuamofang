# -*- coding: utf-8 -*-
"""讨论落地协议（P0-1）的回归锁：提案 → 待审隔离 → 批准写回 → 编号顺延 → 驳回不碰稿。

背景：用户真实工作流是「与 agent 讨论十几次 → 结论以【优化点】烤进大纲」，
但产品里没有讨论入口，唯一修改通道是审批门的通过/打回二元选择。落地协议
（2026-10-18）补上这条通道：agent 提案进草稿区，用户在 GUI 批准才写回章大纲。

本文件锁四件事：

1. **草稿隔离**：提案落盘后、批准前，章大纲一个字节都不能变（否则「提案」
   就成了「直接改稿」，审批门形同虚设）。
2. **写回正确**：批准后优化点真在章大纲里，锚点 JSON 可反查，章骨架
   （events/roles/功能/衔接）不被污染，且有写前备份。
3. **编号顺延**：两个提案先后批准，章内优化点编号 1、2 不撞号，锚点的 n
   与章内编号一致（可互查）。
4. **schema 无损往返**：优化点块（含样例文与锚点注释）经 GUI 蓝图编辑器的
   parse → render 往返后一字不丢 —— 此前 renderContent 只渲染 6 个骨架字段，
   讨论成果会在用户下次保存时被静默删除。

用法：python tests/unit/test_discuss_protocol.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import discuss                       # noqa: E402
from utils.chapter_blueprint import (           # noqa: E402
    parse_chapter_md, render_chapter_md, next_opt_number, append_opt_point)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


BASE_CHAPTER = """## 第三章 仙台争花火

- 核心事件：
  1. 暮雨与风灵争论出征规模
- 涉及角色：暮雨；风灵
- 功能：转折
- 衔接：承接第二章；钩子——远征队信号
"""

PROPOSAL_BODY = ("- 候选：A 池鸿威旧创（选定）/ B 芦苇童年\n"
                 "- 理由：说服段缺可感知的代价\n"
                 "- 样例文（对话）：\n"
                 "  > 「我想起池鸿威终战那次……」\n"
                 "- 样例文（心理）：\n"
                 "  > 暮雨心中一震，沙都绝境的窒息感又漫上来。")


def _tmp_root():
    t = Path(tempfile.mkdtemp(prefix="discuss_test_"))
    (t / "data" / "outline" / "chapters").mkdir(parents=True)
    (t / "data" / "outline" / "chapters" / "03.md").write_text(
        BASE_CHAPTER, encoding="utf-8")
    return t


# ------------------------------------------------------- 1. 草稿隔离
def test_draft_isolation():
    print("\n[1] 草稿隔离：提案落盘不碰章大纲")
    t = _tmp_root()
    ok, msg, rel = discuss.submit(
        target_n=3, title="说服段补具体案例", body=PROPOSAL_BODY,
        rationale="说服段缺可感知的代价", candidates=["A 池鸿威旧创", "B 芦苇童年"],
        chosen="A 池鸿威旧创",
        anchor={"id": "ch3-opt1", "kind": "dialogue-sample",
                "tags": ["说服", "沙都线"], "weight": "high"},
        rounds=3, root=str(t))
    check("提案提交成功且落盘", ok and (t / rel).exists(), msg)
    check("提案文件带待审声明", "待审提案" in (t / rel).read_text(encoding="utf-8"))

    now = (t / "data" / "outline" / "chapters" / "03.md").read_text(encoding="utf-8")
    check("批准前章大纲一字未改", now == BASE_CHAPTER)

    q = discuss.queue(status="pending", root=str(t))
    check("进入待审队列", len(q) == 1 and q[0]["status"] == "pending", q)
    return t, rel


# ------------------------------------------------------- 2. 批准写回
def test_approve_writes_back(t, rel):
    print("\n[2] 批准写回：优化点真在章里，骨架不被污染")
    ok, msg = discuss.apply_proposal(rel, root=str(t), reviewer="用户审阅通过")
    check("批准成功", ok, msg)

    new = (t / "data" / "outline" / "chapters" / "03.md").read_text(encoding="utf-8")
    d = parse_chapter_md(new)
    check("章内出现 1 个优化点", len(d["opt_points"]) == 1, len(d["opt_points"]))
    check("优化点编号为 1", d["opt_points"][0]["n"] == 1)
    check("锚点 id 可反查", d["opt_points"][0]["anchor"].get("id") == "ch3-opt1",
          d["opt_points"][0]["anchor"])
    check("锚点 tags/weight 保留",
          d["opt_points"][0]["anchor"].get("tags") == ["说服", "沙都线"]
          and d["opt_points"][0]["anchor"].get("weight") == "high")
    check("骨架 roles 不被样例文污染", d["roles"] == ["暮雨", "风灵"], d["roles"])
    check("骨架 events 完好", d["events"] == ["暮雨与风灵争论出征规模"])

    baks = list((t / "data" / "outline" / "chapters").glob("03_v*.bak"))
    check("写前备份存在且是原始版",
          bool(baks) and "优化点" not in baks[0].read_text(encoding="utf-8"),
          baks[0].name if baks else "无备份")

    q = discuss.queue(status="approved", root=str(t))
    check("提案状态变已通过", len(q) == 1 and q[0]["status"] == "approved")
    return t


# ------------------------------------------------------- 3. 编号顺延
def test_numbering(t):
    print("\n[3] 编号顺延：二号提案接上号，不撞")
    ok2, _, rel2 = discuss.submit(target_n=3, title="第二条", body="第二条正文",
                                  anchor={"id": "ch3-opt2"}, root=str(t))
    ok3, msg3 = discuss.apply_proposal(rel2, root=str(t))
    check("二号提案批准成功", ok2 and ok3, msg3)

    d2 = parse_chapter_md((t / "data" / "outline" / "chapters" / "03.md")
                          .read_text(encoding="utf-8"))
    check("章内编号 [1,2] 不撞号", [o["n"] for o in d2["opt_points"]] == [1, 2],
          [o["n"] for o in d2["opt_points"]])
    check("锚点 n 与章内编号一致", d2["opt_points"][1]["anchor"].get("n") == 2)
    check("一号优化点未被二号破坏",
          d2["opt_points"][0]["anchor"].get("id") == "ch3-opt1")


# ------------------------------------------------------- 4. 驳回不碰稿
def test_reject_isolation(t):
    print("\n[4] 驳回：不碰章大纲")
    before = (t / "data" / "outline" / "chapters" / "03.md").read_text(encoding="utf-8")
    ok4, _, rel4 = discuss.submit(target_n=3, title="要驳回的", body="驳回内容",
                                  root=str(t))
    ok5, msg5 = discuss.reject(rel4, "方向不对", root=str(t))
    after = (t / "data" / "outline" / "chapters" / "03.md").read_text(encoding="utf-8")
    check("驳回成功且章大纲未变", ok5 and before == after, msg5)

    ok6, _ = discuss.reject(rel4, "", root=str(t))
    check("空原因驳回被拒", not ok6)


# ------------------------------------------------------- 5. 格式与安全
def test_format_and_safety(t):
    print("\n[5] 格式与安全")
    ok, err = discuss.draft_text("../../etc/passwd", root=str(t))
    check("路径穿越被拒", not ok, err)

    ok2, msg2, _ = discuss.submit(target_n=99, title="不存在的章", body="x",
                                  root=str(t))
    check("目标章不存在时拒绝", not ok2, msg2)

    ok3, msg3, _ = discuss.submit(target_n=3, title="空正文", body="   ",
                                  root=str(t))
    check("空 body 被拒", not ok3, msg3)

    # 缺 opt-body 标记的提案：apply 必须报错而不是把整份（含抬头）写进大纲
    bad = t / "data" / "outline" / "drafts"
    bad.mkdir(parents=True, exist_ok=True)
    badfile = bad / "bad.md"
    badfile.write_text("# 坏提案\n\n没有 body 标记\n", encoding="utf-8")
    ok4, msg4 = discuss.apply_proposal("data/outline/drafts/bad.md", root=str(t))
    check("缺 opt-body 标记的提案拒绝写回", not ok4 and "opt-body" in str(msg4), msg4)


# ------------------------------------------------------- 6. schema 无损往返
def test_blueprint_roundtrip():
    print("\n[6] schema 无损往返（GUI 蓝图编辑器保存不丢优化点）")
    text = BASE_CHAPTER + """

## 【优化点1】说服力不足，缺具体案例
- 候选：A 池鸿威旧创（选定）/ B 芦苇童年
- 理由：说服段缺可感知的代价
- 样例文（对话）：
  > 「我想起池鸿威终战那次……」
- 样例文（心理）：
  > 暮雨心中一震。注意：样例文里出现「涉及角色：」字样也不该污染骨架

<!--opt-anchor {"id":"ch3-opt1","kind":"dialogue-sample","tags":["说服","沙都线"],"rounds":3,"chosen":"A","rationale":"说服段缺可感知的代价","weight":"high"} -->
"""
    d = parse_chapter_md(text)
    check("解析出 1 个优化点", len(d["opt_points"]) == 1)
    check("骨架不被优化点样例文污染", d["roles"] == ["暮雨", "风灵"], d["roles"])

    rt = render_chapter_md(d)
    d2 = parse_chapter_md(rt)
    check("往返后优化点仍在", len(d2["opt_points"]) == 1)
    check("往返后原文逐字一致", d2["opt_points"][0]["raw"] == d["opt_points"][0]["raw"])
    check("往返后锚点逐字段一致",
          d2["opt_points"][0]["anchor"] == d["opt_points"][0]["anchor"])

    # 老文件（无优化点）往返不变形
    d3 = parse_chapter_md(BASE_CHAPTER)
    check("无优化点老章 opt_points 为空", d3["opt_points"] == [])
    check("无优化点老章渲染不变形", "核心事件" in render_chapter_md(d3))

    check("next_opt_number 空文件返回 1", next_opt_number(BASE_CHAPTER) == 1)
    check("next_opt_number 有一条返回 2",
          next_opt_number(append_opt_point(BASE_CHAPTER, "x")) == 2)


def main():
    t, rel = test_draft_isolation()
    t = test_approve_writes_back(t, rel)
    test_numbering(t)
    test_reject_isolation(t)
    test_format_and_safety(t)
    test_blueprint_roundtrip()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
