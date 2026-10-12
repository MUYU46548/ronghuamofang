# -*- coding: utf-8 -*-
"""规划四态状态机（丙0）回归 —— 14 断言。

覆盖：
- 基础 propose → idea
- 合法转换（select / write / drop / revive）
- 非法转换拦截（idea → written 跳过 selected、selected → dropped 复活）
- written 引用硬校验（空路径拒、不存在拒、零字节拒、合法通过）
- AI 不自标 selected（无 select 调用路径，API 层语义隔离）
- 审计日志完整
- 状态库损坏/缺失 → 空 dict（不抛）

用法：python tests/unit/test_plan_state.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.plan_state import (  # noqa: E402
    propose, select, write, drop, revive, transition,
    list_items, get, stats,
    STATUS_IDEA, STATUS_SELECTED, STATUS_WRITTEN, STATUS_DROPPED,
    VALID_STATUSES, VALID_TRANSITIONS,
)

PASS, FAIL = [], []
TMP_ROOT = None


def setup():
    global TMP_ROOT
    TMP_ROOT = Path(tempfile.mkdtemp(prefix="plan_state_test_"))
    state_p = TMP_ROOT / "plan_state.json"
    audit_p = TMP_ROOT / "plan_audit.jsonl"
    return state_p, audit_p


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def test_propose_basic():
    print("\n[1] 基础 propose → idea 态")
    state_p, audit_p = setup()
    try:
        iid, entry = propose("测试提案", "摘要", source="test",
                             state_path=state_p, audit_path=audit_p)
        check("1.1 返回非空 id", bool(iid), iid)
        check("1.2 默认 status=idea", entry["status"] == STATUS_IDEA, entry["status"])
        check("1.3 source 记录正确", entry["source"] == "test", entry["source"])
        check("1.4 transition_history 首条是 idea",
              entry["transition_history"][0]["to"] == STATUS_IDEA,
              entry["transition_history"])
        # 审计日志
        audit_lines = [json.loads(l) for l in read_text(audit_p).strip().splitlines() if l.strip()]
        check("1.5 审计日志首条是 propose",
              audit_lines[0]["action"] == "propose" and audit_lines[0]["to"] == STATUS_IDEA,
              audit_lines[0] if audit_lines else None)
        # 重复 propose 不冲突
        iid2, _ = propose("测试提案", "摘要", source="test",
                          state_path=state_p, audit_path=audit_p)
        check("1.6 同名提案生成不同 id（基于时间戳+sha1）", iid != iid2, (iid, iid2))
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_valid_transitions():
    print("\n[2] 合法转换链 idea → selected → written")
    state_p, audit_p = setup()
    try:
        # 写一个真实非空引用文件
        ref_file = TMP_ROOT / "ref.md"
        ref_file.write_text("正文内容非空", encoding="utf-8")

        iid, _ = propose("链式转换", "摘要", state_path=state_p, audit_path=audit_p)
        ok, msg, e = select(iid, operator="user", state_path=state_p, audit_path=audit_p)
        check("2.1 select 成功", ok and e["status"] == STATUS_SELECTED, msg)
        ok, msg, e = write(iid, str(ref_file), operator="user",
                           state_path=state_p, audit_path=audit_p)
        check("2.2 write 成功（引用文件合法）", ok and e["status"] == STATUS_WRITTEN, msg)
        check("2.3 write 记录了 ref_path", e["ref_path"] == str(ref_file), e["ref_path"])
        # written 是终态 → 不能再 drop
        ok, msg, e = drop(iid, state_path=state_p, audit_path=audit_p)
        check("2.4 written 不能再 drop（终态）", not ok and "非法转换" in msg, msg)
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_illegal_transitions():
    print("\n[3] 非法转换拦截")
    state_p, audit_p = setup()
    try:
        iid, _ = propose("拦截测试", "", state_path=state_p, audit_path=audit_p)
        # idea → written（跳 selected）
        ok, msg, _ = write(iid, "/tmp/nonexistent.md", state_path=state_p, audit_path=audit_p)
        check("3.1 idea → written 被拦（需先 select）", not ok and "非法转换" in msg, msg)
        # selected → revive（无此合法转换）
        select(iid, state_path=state_p, audit_path=audit_p)
        ok, msg, _ = revive(iid, state_path=state_p, audit_path=audit_p)
        check("3.2 selected → revive 被拦（仅对 dropped 生效）",
              not ok, msg)
        # 直接 drop selected → ok
        ok, msg, e = drop(iid, state_path=state_p, audit_path=audit_p)
        check("3.3 selected → drop 合法", ok and e["status"] == STATUS_DROPPED, msg)
        # dropped → written（非法）
        ok, msg, _ = write(iid, "/tmp/x.md", state_path=state_p, audit_path=audit_p)
        check("3.4 dropped → written 被拦", not ok and "非法转换" in msg, msg)
        # dropped → idea（复活）
        ok, msg, e = revive(iid, state_path=state_p, audit_path=audit_p)
        check("3.5 dropped → idea（复活）合法", ok and e["status"] == STATUS_IDEA, msg)
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_reference_hard_validation():
    print("\n[4] written 引用硬校验")
    state_p, audit_p = setup()
    try:
        iid, _ = propose("引用校验", "", state_path=state_p, audit_path=audit_p)
        select(iid, state_path=state_p, audit_path=audit_p)

        ok, msg, _ = write(iid, "", state_path=state_p, audit_path=audit_p)
        check("4.1 空路径被拒", not ok and "空路径" in msg, msg)

        ok, msg, _ = write(iid, "/tmp/this_file_does_not_exist_xyz.md",
                           state_path=state_p, audit_path=audit_p)
        check("4.2 不存在路径被拒", not ok and "不存在" in msg, msg)

        empty_file = TMP_ROOT / "empty.md"
        empty_file.write_text("", encoding="utf-8")
        ok, msg, _ = write(iid, str(empty_file), state_path=state_p, audit_path=audit_p)
        check("4.3 零字节文件被拒", not ok and "零字节" in msg, msg)

        good_file = TMP_ROOT / "good.md"
        good_file.write_text("非空", encoding="utf-8")
        ok, msg, e = write(iid, str(good_file), state_path=state_p, audit_path=audit_p)
        check("4.4 合法引用通过", ok and e["status"] == STATUS_WRITTEN, msg)
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_persistence_and_list():
    print("\n[5] 持久化与查询")
    state_p, audit_p = setup()
    try:
        iid, _ = propose("持久化测试", "", state_path=state_p, audit_path=audit_p)
        # 重新加载 → 同状态
        entry = get(iid, state_path=state_p)
        check("5.1 get 读取状态", entry and entry["status"] == STATUS_IDEA, entry)
        items = list_items(state_path=state_p)
        check("5.2 list_items 返回", len(items) >= 1, len(items))
        items_idea = list_items(status=STATUS_IDEA, state_path=state_p)
        check("5.3 按状态筛选", all(e["status"] == STATUS_IDEA for e in items_idea), items_idea)
        s = stats(state_path=state_p)
        check("5.4 stats 各状态键齐", set(s.keys()) == set(VALID_STATUSES), s)
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_corrupt_state_recovery():
    print("\n[6] 损坏状态库恢复（不抛）")
    state_p, audit_p = setup()
    try:
        state_p.parent.mkdir(parents=True, exist_ok=True)
        state_p.write_text("{broken json!!!", encoding="utf-8")
        # load_state 返回空 dict
        from utils.plan_state import load_state, propose
        data = load_state(state_p)
        check("6.1 损坏 → 空 dict", data == {}, data)
        # 可以继续写入（覆盖损坏文件）
        iid, entry = propose("损坏恢复", "", state_path=state_p, audit_path=audit_p)
        check("6.2 损坏后可正常 propose", entry["status"] == STATUS_IDEA, entry)
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_ai_cannot_self_select():
    print("\n[7] AI 不自标 selected（语义隔离）")
    # 这是设计约束，测试用例验证：不存在 propose 直接产出 selected 的路径
    # 以及 transition 的 operator 字段由调用方传入（无"自动"捷径）
    state_p, audit_p = setup()
    try:
        # 没有 propose(..., status=selected) 参数
        iid, entry = propose("AI不自选", "", source="agent",
                             state_path=state_p, audit_path=audit_p)
        check("7.1 AI propose 恒为 idea", entry["status"] == STATUS_IDEA, entry["status"])
        # 即使 operator="agent" 也仍需通过 select 函数显式调用
        # （守卫在 API 层 / GUI 层，此处验证 select 函数本身不限制 operator ——
        #   API 层会拦截 non-GUI 来源，单元测试测状态机语义）
        ok, _, e = select(iid, operator="agent", state_path=state_p, audit_path=audit_p)
        check("7.2 select 函数层面允许任意 operator（守卫在 API 层）",
              ok and e["status"] == STATUS_SELECTED, "API层守卫，此处不限")
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)


def read_text(p):
    return Path(p).read_text(encoding="utf-8")


def main():
    test_propose_basic()
    test_valid_transitions()
    test_illegal_transitions()
    test_reference_hard_validation()
    test_persistence_and_list()
    test_corrupt_state_recovery()
    test_ai_cannot_self_select()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
