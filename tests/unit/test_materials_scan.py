# -*- coding: utf-8 -*-
"""素材扫描清单自检（GET /materials/scan 的判据层，零 token）。

判据只有一份：`utils.meta_detect.iter_material_files`（stage1 归并、
索引对账、界面清单三处共用）。本用例锁三件事：

1. 真素材进 `read`；
2. 元文件（README / 碎片填充计划）进 `skipped`，且**不**进 `read`；
3. `_` / `.` 子目录与 PDF 的行为与 stage1 一致。

用法：python tests/unit/test_materials_scan.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.meta_detect import scan_summary                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if cond:
        print("  [PASS] " + name)
    else:
        print("  [FAIL] " + name + "  → " + str(extra))


def main():
    print("=" * 66)
    print("素材扫描清单（阶段 1 会读哪些 / 跳过哪些）")
    print("=" * 66)
    tmp = Path(tempfile.mkdtemp(prefix="nf_mat_scan_"))
    try:
        (tmp / "raw").mkdir()
        raw = tmp / "raw"
        (raw / "角色卡.md").write_text("# 角色：阿岚\n\n随记。\n", encoding="utf-8")
        (raw / "场景.txt").write_text("渡口，夜里。\n", encoding="utf-8")
        (raw / "README.md").write_text("# materials/raw\n\n目录说明。\n", encoding="utf-8")
        (raw / "碎片填充计划.md").write_text("# 碎片填充计划\n\n登记表。\n",
                                            encoding="utf-8")
        (raw / "参考.pdf").write_bytes(b"%PDF-1.4 fake")
        (raw / "_backup").mkdir()
        (raw / "_backup" / "旧版.md").write_text("旧版。\n", encoding="utf-8")
        (raw / "子目录").mkdir()
        (raw / "子目录" / "补充.md").write_text("补充设定。\n", encoding="utf-8")

        out = scan_summary(raw)
        read = set(out["read"])
        skipped = {x["name"] for x in out["skipped"]}

        check("真素材进 read（.md/.txt）", {"角色卡.md", "场景.txt"} <= read, read)
        check("子目录里的素材也会读（与 stage1 递归一致）", "子目录/补充.md" in read, read)
        check("元文件进 skipped，且不混进 read",
              {"README.md", "碎片填充计划.md"} <= skipped
              and not ({"README.md", "碎片填充计划.md"} & read), (skipped, read))
        check("`_backup/` 不扫（旧版本不进归并）",
              not any(n.startswith("_backup") for n in read | skipped), read | skipped)
        check("PDF 不读，但给一句可行动的警告",
              "参考.pdf" not in read
              and any("PDF" in w and "参考.pdf" in w for w in out["warnings"]),
              out["warnings"])
        check("相对路径统一用 /（界面直接展示）",
              all("\\" not in n for n in read | skipped), read | skipped)
        check("计数与清单长度一致",
              out["read_count"] == len(out["read"])
              and out["skip_count"] == len(out["skipped"]), out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
