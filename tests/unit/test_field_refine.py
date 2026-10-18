# -*- coding: utf-8 -*-
"""定向改写候选生成（第 3 件 · C 兜底）的回归锁。

背景：Hermes 不在场时讨论入口不能断。C 兜底给一条**单轮**轻链路：用户点某个
章大纲字段 → 写一句意见 → 一次调用产出 2-3 个候选 → 挑一个 → 落提案进待审队列
→ 批准才写回。粒度是**一个字段**，不做整章重写（2026-10-18 用户裁定：「整章
重写意义不大」—— 改动面大、diff 难看、批准风险高）。

本文件锁六件事：

1. **候选解析**：模型按 JSON 返回时解析出 2-3 个候选，字段名与成本/模型/token
   数如实带回（成本必须算 —— 单轮直调不经过 run_task，漏记会让 GUI 成本页
   凭空少一笔，静默漏账比多花钱更难查）。
2. **截断防护**：finish_reason=length 时**拒绝**候选，不把半截内容给用户挑。
3. **非 JSON 拒绝**：模型不按格式返回时如实报错，不猜、不返回半截。
4. **提案不碰稿**：submit_chosen 落提案后章大纲**一个字节都不能变**（否则
   「候选对比」就成了「直接改稿」，待审队列形同虚设）。
5. **字段替换写回正确**：批准 blueprint_patch 提案后字段真变了、其他字段与
   优化点块不被污染、有写前备份。
6. **同值拒绝**：改写内容与原稿相同时拒绝写回（空提案不该骗过批准）。

用法：python tests/unit/test_field_refine.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import discuss, field_refine               # noqa: E402
from utils.chapter_blueprint import parse_chapter_md  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


CHAP = """## 第三章 仙台争花火

- 核心事件：
  1. 主角甲与主角乙争论出征规模
  2. 以旧创案例说服
- 涉及角色：主角甲；主角乙
- 功能：转折
- 衔接：承接第二章；钩子——远征队信号

## 【优化点1】关键段缺具体案例
- 理由：抽象口号撑不起逻辑

<!--opt-anchor {"id":"ch3-opt1","kind":"dialogue-sample","n":1} -->
"""

GOOD_JSON = json.dumps({"candidates": [
    {"text": "主角乙拿出星图指出航道风险", "why": "用具体物件代替抽象争论"},
    {"text": "主角甲列举三场败绩说明必要", "why": "用往事支撑论点"},
    {"text": "两人以兵棋推演验证规模差异", "why": "把争论落到可演算的行动"},
]}, ensure_ascii=False)


class FakeClient:
    """假客户端：单轮返回固定 JSON，不烧真 token。"""

    def __init__(self, text=GOOD_JSON, finish="stop",
                 usage=None, model="fake-model"):
        self._t, self._f = text, finish
        self._u = usage or {"prompt_tokens": 1200, "completion_tokens": 300}
        self._m = model

    def _post_chat(self, messages, temperature=None, max_tokens=None):
        return self._t, self._u, self._m, self._f


def _tmp_root():
    t = Path(tempfile.mkdtemp(prefix="field_refine_"))
    d = t / "data" / "outline" / "chapters"
    d.mkdir(parents=True)
    (d / "03.md").write_text(CHAP, encoding="utf-8")
    return t


# ------------------------------------------------------- 1. 候选解析
def test_candidates_parse():
    print("\n[1] 候选解析 + 计量如实带回")
    t = _tmp_root()
    ok, err, payload = field_refine.generate_candidates(
        3, "events", note="关键段太生硬", client=FakeClient(), root=str(t))
    check("生成成功", ok, err)
    if not ok:
        return t
    check("候选数 3", len(payload["candidates"]) == 3, len(payload["candidates"]))
    check("field 回带", payload["field"] == "events")
    check("文本非空", all(c["text"].strip() for c in payload["candidates"]))
    check("why 非空", all(c["why"].strip() for c in payload["candidates"]))
    check("成本已算（单轮直调不走 run_task，漏记会瞒过成本页）",
          payload["cost_yuan"] >= 0, payload["cost_yuan"])
    check("模型名回带", payload["model"] == "fake-model")
    check("token 数回带",
          payload["tokens_in"] == 1200 and payload["tokens_out"] == 300)
    return t


# ------------------------------------------------------- 2. 截断防护
def test_truncation_guard(t):
    print("\n[2] 截断防护")
    ok, err, _ = field_refine.generate_candidates(
        3, "events", client=FakeClient(finish="length"), root=str(t))
    check("finish=length 拒绝候选", not ok)
    check("报错点名 max_tokens", "max_tokens" in (err or ""), err)


# ------------------------------------------------------- 3. 非 JSON 拒绝
def test_bad_json_guard(t):
    print("\n[3] 非 JSON 拒绝（不猜、不返回半截）")
    ok, err, _ = field_refine.generate_candidates(
        3, "events", client=FakeClient(text="我觉得可以从三个角度改……"), root=str(t))
    check("非 JSON 拒绝", not ok)
    check("报错说明格式", "JSON" in (err or ""), err)


# ------------------------------------------------------- 4. 提案不碰稿
def test_submit_isolation(t):
    print("\n[4] 提交候选 → 落提案，章大纲不动")
    chap = t / "data" / "outline" / "chapters" / "03.md"
    before = chap.read_text(encoding="utf-8")
    ok, err, rel = field_refine.submit_chosen(
        3, "events", "主角乙拿出星图指出航道风险", why="用具体物件代替抽象争论",
        note="关键段太生硬", root=str(t))
    check("提交成功", ok, err)
    after = chap.read_text(encoding="utf-8")
    check("章大纲一字节未变（提案 ≠ 直接改稿）", before == after)
    check("提案落盘", rel is not None and (t / rel).exists(), rel)
    draft = (t / rel).read_text(encoding="utf-8")
    check("提案带 blueprint_patch 锚点", "blueprint_patch" in draft)
    check("提案带字段名", '"field": "events"' in draft)
    check("提案是待审态（不是成品）", "待审" in draft or "pending" in draft)
    return rel


# ------------------------------------------------------- 5. 字段替换写回
def test_patch_apply(t, rel):
    print("\n[5] 批准 blueprint_patch → 字段真改、别处不脏、有备份")
    chap = t / "data" / "outline" / "chapters" / "03.md"
    ok, msg = discuss.apply_proposal(rel, root=str(t))
    check("批准成功", ok, msg)
    d = parse_chapter_md(chap.read_text(encoding="utf-8"))
    check("目标字段已改", d["events"] == ["主角乙拿出星图指出航道风险"], d["events"])
    check("其他字段未被污染",
          d["roles"] == ["主角甲", "主角乙"] and d["function"] == "转折")
    check("优化点块保住（parse→render 往返不丢）", len(d["opt_points"]) == 1)
    baks = list((t / "data" / "outline" / "chapters").glob("03_v*.bak"))
    check("写前备份存在", bool(baks))
    if baks:
        check("备份是原稿（含旧事件）",
              "争论出征规模" in baks[0].read_text(encoding="utf-8"))


# ------------------------------------------------------- 6. 同值拒绝
def test_same_value_rejected(t):
    print("\n[6] 同值提案拒绝（空改不该骗过批准）")
    ok, _, rel = field_refine.submit_chosen(
        3, "function", "转折", root=str(t))
    check("同值提案可提交（提交侧不判空改）", ok)
    ok2, msg = discuss.apply_proposal(rel, root=str(t))
    check("批准时拒绝同值", not ok2, msg)
    check("报错点名字段", "完全相同" in (msg or ""), msg)


# ------------------------------------------------------- 7. 输入校验
def test_input_validation(t):
    print("\n[7] 输入校验")
    ok, err, _ = field_refine.current_field(3, "bogus", root=str(t))
    check("未知字段拒绝", not ok, err)
    ok2, err2, _ = field_refine.current_field(99, "events", root=str(t))
    check("不存在章拒绝", not ok2, err2)
    ok3, err3, _ = field_refine.generate_candidates(
        3, "events", client=FakeClient(
            text=json.dumps({"candidates": [{"text": "", "why": ""}]},
                            ensure_ascii=False)), root=str(t))
    check("全空候选拒绝", not ok3, err3)


def main():
    t = test_candidates_parse()
    test_truncation_guard(t)
    test_bad_json_guard(t)
    rel = test_submit_isolation(t)
    if rel:
        test_patch_apply(t, rel)
        # 同值测试要排在 patch 之后：它复用已被改过的 function 字段
        test_same_value_rejected(t)
    test_input_validation(t)

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
