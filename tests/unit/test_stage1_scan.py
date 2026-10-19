# -*- coding: utf-8 -*-
"""stage1 素材扫描的目录边界回归（2026-10-19）。

## 钉什么

`scan_materials` 用 `rglob` 扫 `materials/raw/`，而本仓在该目录下还有
`_backup/`（同名覆盖前的旧版本，存量 4 个文件）。旧实现会钻进去：

- 同一张卡的新旧两版一起喂给归并（白烧 token，且让模型在「潮神」的两个版本间二选一）
- 素材指纹随备份目录变化而抖动 → 反复触发「素材有变化，重新归并」

判据：**以 `_` 或 `.` 开头的子目录一律跳过；普通子目录照旧递归**（不误伤用户的分类目录）。

| 编号 | 钉什么 |
|---|---|
| S1 | 顶层卡被扫到 |
| S2 | 普通子目录（用户自己的分类）仍然递归 |
| S3 | `_backup/` 里的卡**不被扫到**（本次修复的核心） |
| S4 | `.hidden/` 同样跳过（约定一致） |
| S5 | 非支持扩展名不进食谱 |

用法：python tests/unit/test_stage1_scan.py（零 LLM、零网络）
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from stage1_consolidate import scan_materials                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(extra)[:200]) if extra else ""))


def main():
    print("=" * 62)
    print("  stage1 素材扫描：目录边界")
    print("=" * 62)

    root = Path(tempfile.mkdtemp(prefix="nf_scan_"))
    mats = root / "materials"
    nd = root / "normalized"
    (mats / "地点").mkdir(parents=True)
    (mats / "_backup").mkdir(parents=True)
    (mats / ".hidden").mkdir(parents=True)

    (mats / "沈砚_角色卡.md").write_text("# 角色：沈砚\n正文\n", encoding="utf-8")
    (mats / "地点" / "雾港遗址_场景卡.md").write_text("# 场景：雾港遗址\n正文\n", encoding="utf-8")
    (mats / "_backup" / "沈砚_角色卡_旧.md").write_text("# 角色：沈砚\n旧版\n", encoding="utf-8")
    (mats / "_backup" / "潮神_角色卡.md").write_text("# 角色：潮神\n备份\n", encoding="utf-8")
    (mats / ".hidden" / "隐藏.md").write_text("# 隐藏\n", encoding="utf-8")
    (mats / "说明.txt").write_text("txt 也支持\n", encoding="utf-8")
    (mats / "图片.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    manifest = scan_materials(str(mats), str(nd))
    names = sorted(Path(it["source"]).name for it in manifest)

    check("S1 顶层卡被扫到", "沈砚_角色卡.md" in names, names)
    check("S2 普通子目录仍然递归", "雾港遗址_场景卡.md" in names, names)
    check("S3 `_backup/` 里的卡不被扫到（新旧两版不重复归并）",
          not any(n.startswith(("沈砚_角色卡_旧", "潮神_角色卡")) for n in names), names)
    check("S4 `.hidden/` 同样跳过", "隐藏.md" not in names, names)
    check("S5 txt 受支持、png 不进食谱",
          "说明.txt" in names and "图片.png" not in names, names)
    check("S5 扫描条数 = 3（沈砚 / 雾港遗址 / 说明.txt）", len(manifest) == 3,
          len(manifest))

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
