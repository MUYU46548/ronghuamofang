# -*- coding: utf-8 -*-
"""stage1 元文件分拣自检（`_is_meta_file` + `scan_materials` 跳过行为，零 LLM）。

用户 2026-10-24 的素材里混着「碎片填充计划」这类登记表（正文：已使用的碎片
在此登记）。喂进归并等于把索引当成设定，污染设定集。本用例守三件事：

1. 元文件被识别（登记表 / 说明 / readme / 变更日志）；
2. **真素材不被误跳**（角色卡、碎片正文哪怕提到"计划"两字也不算元文件）；
3. scan_materials 真的跳过它们并在清单里消失，_ 目录也照样跳。

专名纪律：夹具全部自造，不碰真实素材的专名。
用法：python tests/unit/test_stage1_meta_skip.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from stage1_consolidate import _is_meta_file, scan_materials  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "raw").mkdir()
        (root / "raw" / "_backup").mkdir()

        def w(rel, text):
            p = root / rel
            p.write_text(text, encoding="utf-8")
            return p

        # --- 元文件（真实 OB34 形态：标题是「说明」，正文是登记表） ---
        meta_a = w("碎片填充计划.md", "# 说明\n\n已使用的碎片在此登记，避免重复使用。\n\n# 登记列表\n2099-1-1\n2099-2-2\n")
        meta_b = w("变更日志.md", "# 变更日志\n\n2099-01-01 新增角色卡。\n")
        meta_c = w("readme.md", "# Readme\n\n这是素材目录说明。\n")
        # --- 真素材（必须不被跳过） ---
        real_a = w("角色卡甲.md", "# 角色卡\n\n- 名称：甲\n- 身份：巡道工\n\n他有个计划：每天数枕木。\n")
        real_b = w("小说碎片2099-1-1.md", "超算申请单：算力配额七成，推演窗口三日。\n")
        real_c = w("raw/卡.md", "# 卡\n\n正文。\n")
        real_d = w("raw/_backup/旧卡.md", "# 旧卡\n\n覆盖前的旧版本。\n")
        # 两个真坑（2026-10-24 反向验证在 设定库 真设定上抓出，必须锁死）：
        # ① YAML frontmatter 里的 tag `- 索引` 曾被当成标题 → 真设定被判元；
        # ② 真设定正文首句「本索引遵循…」曾命中我加的 probe 词「本索引」。
        real_e = w("地理系统索引.md",
                   "---\npublish: true\ntags:\n- 索引\n- 地理系统\n"
                   "上级页面: '[[总索引]]'\n---\n\n本索引遵循的层级划分规则为：\n\n"
                   "- 世界\n\t- 星域\n")
        real_f = w("种族索引.md",
                   "---\ntags:\n- 索引\n- 种族\n---\n\n"
                   "本索引不作为分类参考标准，仅链接到已建立的页面。\n")
        # 自动生成的聚类清单（OB34 实测形态）：该跳
        meta_d = w("00_大纲收束索引.md",
                   "# 遗声 · 大纲收束索引 v2（自动生成，未改动任何源文件）\n\n"
                   "> 沙盒文件总数：**84** 篇。按 9 维聚类，反推世界支柱与大纲骨架占位。\n")

        print("== 1. 元文件识别 ==")
        check("登记表被判为元文件", _is_meta_file(meta_a))
        check("变更日志被判为元文件", _is_meta_file(meta_b))
        check("readme 被判为元文件", _is_meta_file(meta_c))
        check("自动生成的收束索引被判为元文件（OB34 实测形态）",
              _is_meta_file(meta_d))

        print("== 2. 真素材不被误跳（保守判据） ==")
        check("角色卡不判元（正文含「计划」也不算）", not _is_meta_file(real_a))
        check("碎片正文不判元", not _is_meta_file(real_b))
        check("raw 里的卡不判元", not _is_meta_file(real_c))
        check("不存在的文件不抛（OSError → False）",
              not _is_meta_file(root / "nope.md"))
        # 反向验证：设定库 `01 索引/` 下 13 个真设定文件名带「索引」、
        # frontmatter tag 是 `- 索引`、正文首句是「本索引…」——三重诱因，
        # 我前两版判据都在这里误伤过，锁死。
        check("带 YAML frontmatter 的真设定不判元（tag「索引」不是标题）",
              not _is_meta_file(real_e))
        check("正文首句「本索引不作为…」不判元（probe 词已收紧）",
              not _is_meta_file(real_f))

        print("== 3. scan_materials 真的跳过 ==")
        norm = root / "normalized"
        manifest = scan_materials(str(root), str(norm))
        names = {Path(m["name"]).name for m in manifest}
        check("清单含全部真素材", {"角色卡甲", "小说碎片2099-1-1", "卡"} <= names,
              sorted(names))
        check("清单不含元文件（碎片填充计划）", "碎片填充计划" not in names)
        check("清单不含元文件（变更日志）", "变更日志" not in names)
        check("清单不含 readme", "readme" not in names)
        check("清单不含 _backup 里的旧卡", "旧卡" not in names)

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
