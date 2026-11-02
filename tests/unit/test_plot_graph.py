# -*- coding: utf-8 -*-
"""情节图数据（2026-11-02，视频 v0.5 对标②）。

## 判据（每条都能变红）

- G1 没有章大纲 → 空图 + 一句"先跑阶段 3"（不造节点）
- G2 N 章 → N 个节点，N-1 条顺序连线
- G3 共享角色且**相邻 ≤3 章**才连（远了不连，否则 200 章变上万条）
- G4 卷分组：读不到 volumes/ 就按 per_volume 均匀切
- G5 节点带 label/function/roles/events/hook（图上点得到字）
- G6 端点接线

用法：python tests/unit/test_plot_graph.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import plot_graph                                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _chap(dirp, n, title, roles, func="推进", hook="有人敲门"):
    p = dirp / ("%02d.md" % n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# 第%d章 %s\n\n- 核心事件：\n  1. 第一件事\n  2. 第二件事\n"
        "- 涉及角色：%s\n- 功能：%s\n- 衔接：承接上一章；钩子——%s\n"
        % (n, title, roles, func, hook), encoding="utf-8")
    return p


def main():
    print("=" * 66)
    print("情节图数据")
    print("=" * 66)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # ---- G1 空 ----
        g = plot_graph.build_graph(root=root)
        check("G1 没有章大纲 → 空图", g["nodes"] == [] and g["links"] == [])
        check("G1 给一句下一步", "阶段 3" in (g.get("note") or ""), g.get("note"))

        d = root / "data" / "outline" / "chapters"
        _chap(d, 1, "开局", "甲、乙")
        _chap(d, 2, "遇险", "甲")
        _chap(d, 3, "歇脚", "甲、丙")   # 与第 1 章共享"甲"，用来验窗口内连线
        _chap(d, 8, "远处", "甲")

        g = plot_graph.build_graph(root=root, per_volume=2)
        check("G2 节点数 = 章数", len(g["nodes"]) == 4, [n["id"] for n in g["nodes"]])
        seq = [l for l in g["links"] if l["kind"] == "seq"]
        check("G2 顺序连线 = 章数-1", len(seq) == 3, seq)
        check("G2 顺序连线首尾对", (seq[0]["source"], seq[0]["target"]) == ("01", "02"), seq[0])

        # ---- G3 共享角色只连窗口内 ----
        role = [l for l in g["links"] if l["kind"] == "role"]
        pairs = {(l["source"], l["target"]) for l in role}
        check("G3 相邻两章共享角色 → 连", ("01", "02") in pairs, pairs)
        check("G3 隔 1 章共享角色 → 连", ("01", "03") in pairs, pairs)   # 1 与 3 差 2，窗口内
        check("G3 隔太远（1↔8）→ 不连", ("01", "08") not in pairs, pairs)
        check("G3 连线带上是哪个角色",
              any(l.get("roles") == ["甲"] for l in role), [l.get("roles") for l in role])

        # ---- G4 卷 ----
        vols = {v["vol"]: v["chapters"] for v in g["volumes"]}
        check("G4 按 per_volume=2 切卷", vols.get(1) == [1, 2] and vols.get(2) == [3], vols)
        check("G4 第 8 章落在第 4 卷", vols.get(4) == [8], vols)

        # ---- G5 节点字段 ----
        n1 = [n for n in g["nodes"] if n["id"] == "01"][0]
        check("G5 有标题", n1["title"] == "开局", n1)
        check("G5 label 带章号", n1["label"].startswith("第1章"), n1["label"])
        check("G5 有功能", n1["function"] == "推进", n1["function"])
        check("G5 有角色", n1["roles"] == ["甲", "乙"], n1["roles"])
        check("G5 有事件（最多 2 条）", len(n1["events"]) == 2, n1["events"])
        check("G5 有钩子", n1["hook"] == "有人敲门", n1["hook"])

        # ---- G6 端点 ----
        api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("G6 端点存在", '"/outline/graph"' in api)
        app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        check("G6 面板挂到大纲页", "<PlotGraph" in app)
        panel = ROOT / "console" / "src" / "PlotGraph.vue"
        check("G6 面板有导图按钮", panel.exists() and "导出" in panel.read_text(encoding="utf-8"))

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
