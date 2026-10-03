# -*- coding: utf-8 -*-
"""canon 新鲜度 + 陪跑/自主开关（B4）回归。

| 编号 | 钉什么 |
|---|---|
| J1 | 审批门**三态**：键缺失 = **不改行为** / false = 陪跑（并入阶段 1）/ true = 自主 |
| J2 | `canon_stale`：**未冻结不算过期**；素材 mtime 晚于冻结时间才算 |
| J3 | `recanon` 重冻并给出**条目级变更**（带变更字段名），冻结时间更新 |
| J4 | 范围隔离：`plot_fragments` 不进 diff（它的 status 是另一套语义） |
| J5 | 变更清单**只报「哪些键变了」，不报「谁改的」**（单人本地无从得知，硬造即假信息） |

用法：python tests/unit/test_canon_refresh.py（零 LLM、零网络）
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import material_state as ms                          # noqa: E402
from utils.approval_policy import approval_stages as _approval_stages   # noqa: E402
from orchestrator import _approval_stages as _orch_approval     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:200]) if extra else ""))


# --------------------------------------------------------------------- J1

def case_approval_three_states():
    print("\n[J1] 审批门三态（键缺失必须不改行为）")
    # 注意层级：策略收的是 **gates 字典**（不是整个 cfg）；
    # orchestrator 的 `_approval_stages(cfg)` 才是剥 gates 的那层薄包装。
    g = {"require_approval": [2, 6]}
    check("J1 键缺失 → 只按 require_approval（老配置/测试替身不受影响）",
          _approval_stages(g) == [2, 6], _approval_stages(g))
    g2 = {"require_approval": [2, 6], "material_autonomy": False}
    check("J1 false（陪跑）→ 阶段 1 并入审批门",
          sorted(_approval_stages(g2)) == [1, 2, 6], _approval_stages(g2))
    g3 = {"require_approval": [2, 6], "material_autonomy": True}
    check("J1 true（自主）→ 不额外停",
          _approval_stages(g3) == [2, 6], _approval_stages(g3))
    g4 = {"require_approval": [], "material_autonomy": False}
    check("J1 陪跑 + 空基集 → 仍只有阶段 1 门",
          _approval_stages(g4) == [1], _approval_stages(g4))
    g5 = {"require_approval": []}
    check("J1 键缺失 + 空基集 → 空（**不得**被默认值强加门）",
          _approval_stages(g5) == [], _approval_stages(g5))
    g6 = {}
    check("J1 gates 缺 require_approval → 默认 [2]",
          _approval_stages(g6) == [2], _approval_stages(g6))
    # 单一来源：orchestrator 的包装必须委托同一份策略（否则会出现
    # 「orchestrator 停了、nfctl 的待审批里却看不到 stage1」的不一致）
    edges = {"require_approval": [2, 6], "material_autonomy": False}
    check("J1 orchestrator 的 _approval_stages 与策略同源（薄包装剥 gates）",
          _orch_approval({"gates": edges}) == _approval_stages(edges),
          _orch_approval({"gates": edges}))


# --------------------------------------------------------------------- J2

def case_stale():
    print("\n[J2] canon_stale：未冻结不算过期；素材晚于冻结才算")
    td = Path(tempfile.mkdtemp())
    try:
        raw = td / "materials" / "raw"
        raw.mkdir(parents=True)
        (raw / "甲_角色卡.md").write_text("# 角色：甲\n", encoding="utf-8")
        cp = td / "canon.json"
        stale, why, _ = ms.canon_stale(str(cp), str(raw))
        check("J2 无 canon → 不算过期（「没冻结」≠「过期」）",
              stale is False and "尚未冻结" in why, (stale, why))

        # canon 冻结时间设为**未来** → 素材不可能晚于它 → 不过期
        cp.write_text(json.dumps({
            "_meta": {"canon_frozen_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 600))},
        }, ensure_ascii=False), encoding="utf-8")
        stale2, _, _ = ms.canon_stale(str(cp), str(raw))
        check("J2 冻结时间晚于全部素材 → 不过期", stale2 is False, stale2)

        # 把素材 mtime 推到冻结时间之后 → 过期
        future = time.time() + 1200
        os.utime(raw / "甲_角色卡.md", (future, future))
        stale3, why3, det3 = ms.canon_stale(str(cp), str(raw))
        check("J2 素材晚于冻结 → 过期", stale3 is True, stale3)
        check("J2 原因里点名是哪个素材", "甲_角色卡" in why3, why3)
        check("J2 detail 带最新素材时间", bool(det3.get("newest_mtime")), det3)

        # 损坏 canon → 不炸、不误报过期
        cp.write_text("{ 坏 JSON", encoding="utf-8")
        stale4, why4, _ = ms.canon_stale(str(cp), str(raw))
        check("J2 canon 损坏 → 不报过期且说明原因（不静默）",
              stale4 is False and "解析失败" in why4, (stale4, why4))
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- J3

def case_recanon():
    print("\n[J3] recanon：重冻 + 条目级变更 + 冻结时间更新")
    td = Path(tempfile.mkdtemp())
    try:
        sp = td / "setting.json"
        cp = td / "canon.json"
        sp.write_text(json.dumps({
            "characters": [{"name": "甲", "role": "法师"}, {"name": "丙", "role": "新来的"}],
            "world": {"locations": [{"name": "某地", "description": "旧描述"}]},
            "_meta": {},
        }, ensure_ascii=False), encoding="utf-8")
        cp.write_text(json.dumps({
            "characters": [{"name": "甲", "role": "战士"}, {"name": "乙", "role": "走了的"}],
            "world": {"locations": [{"name": "某地", "description": "旧描述"}]},
            "_meta": {"canon_frozen_at": "2026-01-01T00:00:00"},
        }, ensure_ascii=False), encoding="utf-8")
        ok, msg, ch = ms.recanon(str(sp), str(cp))
        check("J3 重冻成功", ok is True, msg)
        check("J3 added 含新条目 丙", ch["added"] == ["characters:丙"], ch["added"])
        check("J3 removed 含消失条目 乙", ch["removed"] == ["characters:乙"], ch["removed"])
        check("J3 changed 精确到字段（甲 的 role）",
              ch["changed"] == [{"key": "characters:甲", "fields": ["role"]}], ch["changed"])
        meta = json.loads(cp.read_text(encoding="utf-8"))["_meta"]
        check("J3 冻结时间已更新（不再是 2026-01-01）",
              meta.get("canon_frozen_at") != "2026-01-01T00:00:00",
              meta.get("canon_frozen_at"))
        # 重冻后立即再比 → 应无变更（幂等）
        ok2, _m2, ch2 = ms.recanon(str(sp), str(cp))
        n2 = len(ch2["added"]) + len(ch2["removed"]) + len(ch2["changed"])
        check("J3 幂等：紧接着重冻一次无变更", ok2 and n2 == 0, ch2)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- J4

def case_scope():
    print("\n[J4] plot_fragments 不进 diff（status 语义不同）")
    a = {"characters": [{"name": "甲"}],
         "plot_fragments": [{"id": "f1", "source": "x", "status": "unused"}]}
    b = {"characters": [{"name": "甲"}],
         "plot_fragments": [{"id": "f1", "source": "x", "status": "used"},
                            {"id": "f2", "source": "y", "status": "unused"}]}
    ch = ms.diff_canon_entries(a, b)
    check("J4 plot_fragments 的 status 变化**不产生** changed",
          ch["changed"] == [], ch["changed"])
    check("J4 plot_fragments 新增条目**不产生** added",
          ch["added"] == [], ch["added"])


# --------------------------------------------------------------------- J5

def case_no_author():
    print("\n[J5] 变更清单只报「哪些键变了」，不报「谁改的」")
    ch = ms.diff_canon_entries(
        {"characters": [{"name": "甲", "role": "A"}]},
        {"characters": [{"name": "甲", "role": "B"}]})
    item = ch["changed"][0] if ch["changed"] else {}
    check("J5 changed 项只含 key/fields 两个键",
          set(item) == {"key", "fields"}, item)
    blob = json.dumps(ch, ensure_ascii=False)
    check("J5 不出现 author/editor/修改人 之类的伪字段",
          not any(w in blob for w in ("author", "editor", "修改人", "谁改")), blob)


def main():
    print("=" * 70)
    print("canon 新鲜度 + 陪跑/自主开关（B4）回归")
    print("=" * 70)
    case_approval_three_states()
    case_stale()
    case_recanon()
    case_scope()
    case_no_author()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
