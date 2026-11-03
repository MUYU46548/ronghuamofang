# -*- coding: utf-8 -*-
"""设定目录映射通用化（2026-11-03，用户点名"找这个做包死的"）。

## 为什么不能写死

墨坊要发布。每个人的库布局不同（`设定/人物`、`03 设定/01 人物`、`World/Characters`…），
把任何一种当通例写进代码/文档，换个人就废 —— 这正是"知识库扫描通用化"那条纪律。

## 做法

- `GET /config/vault_dirs`：把**用户库里真实存在的目录**列出来（带 md 篇数，两层上限），
  外加当前映射与五个键；代码里一个具体目录名都不出现。
- `POST /config/vault_dirs`：保存用户点选结果到 `config/system.local.yaml`（不进 Git），
  校验：相对路径 / 不许 `..` / 不许绝对路径 / 目录必须存在；空串 = 不用这一类。

## 判据

- Q1 端点对"没配库"给出明确提示（不是空喜欢）
- Q2 写盘只落 local（不碰 system.yaml，不进 Git）
- Q3 保存校验：绝对路径 / `..` / 不存在的目录一律 400 且说清哪个键
- Q4 界面：候选来自接口、五个下拉、有保存按钮
用法：python tests/unit/test_vault_dirs.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name); print("  [PASS] " + name)
    else:
        FAIL.append(name); print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    src = (ROOT / "scripts" / "nf_api_domains" / "vault_dirs.py").read_text(encoding="utf-8")
    check("Q1 候选清单来自扫描用户库（不写死目录名）",
          "_os.walk(vault)" in src and '"characters": "设定/' not in src)
    check("Q1 没配库时给可行动提示", "还没配置知识库路径" in src or "先在“知识库”里填库路径" in src)
    # 写只落 local；读 system.yaml 只用于"当前值"回显（允许）
    check("Q2 写盘只落 system.local.yaml",
          'path=api.ROOT / "config" / "system.local.yaml"' in src)
    check("Q3 校验相对路径与存在性",
          '".." in v.split("/")' in src and "is_dir()" in src)
    check("Q3 拒绝绝对路径/盘符", 'startswith("/")' in src and '":" in v' in src)
    check("Q4 五键齐全",
          all(k in src for k in ("characters", "locations", "factions", "concepts", "timeline")))

    vue = ROOT / "console" / "src" / "VaultDirsPanel.vue"
    check("Q4 界面组件存在", vue.exists())
    vt = vue.read_text(encoding="utf-8") if vue.exists() else ""
    check("Q4 下拉项来自接口候选", "candidates" in vt and "/config/vault_dirs" in vt)
    check("Q4 有保存动作", "save()" in vt and '"POST"' in vt)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("Q4 挂进设置页", "<VaultDirsPanel" in app)

    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("Q5 两条路由都接了", '"/config/vault_dirs"' in api)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
