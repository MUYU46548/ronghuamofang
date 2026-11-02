# -*- coding: utf-8 -*-
"""设定版本：列出 / 对照 / 恢复（2026-11-02，视频③对标）。

## 要治的病

`data/setting/history/setting_vN.json` **一直在写**（覆盖设定前自动备份），
但人看不见也拿不回来 —— 改错了只能去目录里翻 json 手工比对。

## 判据（每条都能变红）

- V1 列出版本，新的在前，带条目数
- V2 两版对照：新增 / 删除 / 字段级改动
- V3 与"当前"对照（不给 to）
- V4 恢复：内容回到那一版，且**当前版本先被存成新的一版**（恢复也可回退）
- V5 恢复不存在的版本 → 拒
- V6 历史文件坏了 → 列出来标"读不出来"，恢复拒（不拿坏数据覆盖好数据）
- V7 端点/权限接线

用法：python tests/unit/test_setting_versions.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import setting_versions as sv                           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    print("=" * 66)
    print("设定版本")
    print("=" * 66)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hist = root / "data" / "setting" / "history"

        v1 = {"characters": [{"name": "甲", "role": "铁匠", "traits": ["寡言"]},
                             {"name": "乙", "role": "掌柜", "traits": ["贪"]}],
              "world": [{"name": "旧城", "snippet": "砖墙"}],
              "plot_fragments": ["a", "b"]}
        v2 = {"characters": [{"name": "甲", "role": "铁匠", "traits": ["寡言", "手稳"]},
                             {"name": "丙", "role": "船工", "traits": []}],
              "world": [{"name": "旧城", "snippet": "砖墙与铁门"}],
              "plot_fragments": ["a"]}
        live = {"characters": [{"name": "甲", "role": "铸剑师", "traits": ["寡言", "手稳"]}],
                "world": [], "plot_fragments": []}

        _write(hist / "setting_v1.json", v1)
        _write(hist / "setting_v2.json", v2)
        _write(root / "data" / "setting" / "setting.json", live)
        (hist / "setting_v3.json").write_text("{坏掉的", encoding="utf-8")

        # ---- V1 ----
        items = sv.list_versions(root=root)
        check("V1 列出 3 个文件", len(items) == 3, [i["v"] for i in items])
        check("V1 新的在前", [i["v"] for i in items][:2] == [3, 2], [i["v"] for i in items])
        check("V1 带条目数", items[1]["counts"].get("characters") == 2, items[1]["counts"])
        check("V6 坏文件被标出来", [i["readable"] for i in items if i["v"] == 3] == [False])

        # ---- V2 v1 → v2 ----
        d = sv.diff(1, 2, root=root)
        check("V2 新增丙", d["added"] == ["丙"], d["added"])
        check("V2 删除乙", d["removed"] == ["乙"], d["removed"])
        names = [c["name"] for c in d["changed"]]
        check("V2 甲被标为改动", names == ["甲"], names)
        fields = [f["field"] for f in d["changed"][0]["fields"]]
        check("V2 改的是 traits", fields == ["traits"], fields)
        check("V2 世界条目也对照了",
              any(c["name"] == "旧城" for c in d["world"]["changed"]), d["world"])

        # ---- V3 v1 → 当前 ----
        d2 = sv.diff(1, None, root=root)
        check("V3 与当前比：甲的角色变了",
              any(f["field"] == "role" for c in d2["changed"] if c["name"] == "甲"
                  for f in c["fields"]), d2["changed"])
        check("V3 与当前比：乙没了", d2["removed"] == ["乙"], d2["removed"])
        check("V3 to 标签是当前", d2["to"] == "当前", d2["to"])

        # ---- V4 恢复 ----
        ok, msg = sv.restore(1, root=root)
        check("V4 恢复成功", ok, msg)
        now = json.loads((root / "data" / "setting" / "setting.json").read_text(encoding="utf-8"))
        check("V4 内容回到 v1", now == v1, now)
        after = sv.list_versions(root=root)
        check("V4 当前版本先被存成新一版", max(i["v"] for i in after if i["readable"]) >= 4,
              [i["v"] for i in after])
        check("V4 提示里说了备份", "已存为" in msg, msg)

        # ---- V5 不存在的版本 ----
        ok5, m5 = sv.restore(99, root=root)
        check("V5 不存在的版本拒恢复", ok5 is False, m5)

        # ---- V6 坏文件不覆盖 ----
        ok6, m6 = sv.restore(3, root=root)
        check("V6 坏文件拒恢复（不拿坏数据覆盖好数据）", ok6 is False, m6)

    # ---- V7 接线 ----
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("V7 版本列表端点", '"/setting/versions"' in api)
    check("V7 对照端点", '"/setting/diff"' in api)
    check("V7 恢复端点", '"/setting/restore"' in api)
    guard = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
    check("V7 外部 Agent 不得恢复设定", '"/setting/restore"' in guard)
    panel = (ROOT / "console" / "src" / "SettingVersionsPanel.vue")
    check("V7 面板存在", panel.exists())
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("V7 面板挂到设定页", "<SettingVersionsPanel" in app)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
