# -*- coding: utf-8 -*-
"""素材批量移动 / 删除 / 撤回（试跑第 6 条，2026-11-05）。

## 要治的病

作者原话：“误操作全部扔进了“结构化素材”，狂按撤回键无效。也没有任何批量操作入口。”

## 判据（卡里那条用例 + 三条安全线）

- M1 造 12 个文件 → 选 8 个批量移动 → 撤回 → 8 个都回到原目录（逐个比对路径）
- M2 撤回后 **mtime 不被改写**（搬家用 rename 语义，不是重写）
- M3 删除进回收站（不是真删）→ 撤回能搬回
- M4 路径穿越（`../x`、`a/b`、绝对路径）一律拒收
- M5 目标已有同名文件 → 拒收那一条并给原因，不覆盖
- M6 一步撤回不了（文件已被手工搬走）时**整步不落账**，并如实报错

用法：python tests/unit/test_materials_batch_undo.py
"""
import io
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def main():
    print("=" * 70)
    print("素材批量搬运 / 撤回（试跑第 6 条）")
    print("=" * 70)

    from utils import materials_ops as ops

    tmp = Path(tempfile.mkdtemp(prefix="nf_mat_"))
    try:
        raw = tmp / "materials" / "raw"
        scraps = tmp / "materials" / "original_scraps"
        raw.mkdir(parents=True)
        names = ["素材%02d.md" % i for i in range(1, 13)]
        for i, n in enumerate(names):
            p = raw / n
            p.write_text("# %s\n正文" % n, encoding="utf-8")
            # 让 mtime 各不相同，撤回后好比对
            t = time.time() - 3600 + i * 60
            import os
            os.utime(p, (t, t))
        before_mtime = {n: (raw / n).stat().st_mtime for n in names}

        print("\n[M1] 8 个批量移动 → 撤回 → 全部回原目录")
        picked = names[:8]
        ok, res = ops.move_many(tmp, picked, "scraps")
        check("移动成功", ok and len(res["moved"]) == 8, res)
        check("源目录少了 8 个", len(list(raw.glob("*.md"))) == 4,
              len(list(raw.glob("*.md"))))
        check("目标目录有 8 个", len(list(scraps.glob("*.md"))) == 8,
              len(list(scraps.glob("*.md"))))
        check("日志记了一步", len(ops.journal(tmp)) == 1, ops.journal(tmp))

        ok2, res2 = ops.undo_last(tmp)
        check("撤回成功", ok2 and len(res2["undone"]) == 8, res2)
        back = sorted(p.name for p in raw.glob("*.md"))
        check("8 个都回到原目录", back == sorted(names), back)
        check("目标目录清空", len(list(scraps.glob("*.md"))) == 0)

        print("\n[M2] 撤回不改 mtime")
        after = {n: (raw / n).stat().st_mtime for n in names}
        check("逐个 mtime 一致", all(abs(before_mtime[n] - after[n]) < 1e-6 for n in names),
              [(n, before_mtime[n], after[n]) for n in names[:2]])

        print("\n[M3] 删除进回收站，可撤回")
        ok3, res3 = ops.delete_many(tmp, names[8:10])
        check("删除成功", ok3 and len(res3["deleted"]) == 2, res3)
        check("文件已不在 raw", not (raw / names[8]).exists())
        trash = list((tmp / "data" / "state" / "materials_trash").rglob("*.md"))
        check("回收站里有 2 个", len(trash) == 2, len(trash))
        ok4, res4 = ops.undo_last(tmp)
        check("撤回删除成功", ok4 and len(res4["undone"]) == 2, res4)
        check("两个都回来了", (raw / names[8]).exists() and (raw / names[9]).exists())

        print("\n[M4] 路径穿越被拒")
        ok5, res5 = ops.move_many(tmp, ["../evil.md", "a/b.md", "C:\\x.md", ".."], "scraps")
        check("四个都被拒", len(res5["skipped"]) == 4, res5["skipped"])
        check("没有任何文件被搬走", len(list(scraps.glob("*.md"))) == 0)
        check("拒收理由说人话",
              any("路径分隔符" in s["reason"] or "非法" in s["reason"] or "绝对路径" in s["reason"]
                  for s in res5["skipped"]), res5["skipped"])

        print("\n[M5] 目标同名不覆盖")
        (scraps / names[0]).write_text("已经在这儿了", encoding="utf-8")
        ok6, res6 = ops.move_many(tmp, [names[0]], "scraps")
        check("这一条被跳过", len(res6["skipped"]) == 1 and not res6["moved"], res6)
        check("理由写明不覆盖", "不覆盖" in res6["skipped"][0]["reason"],
              res6["skipped"][0]["reason"])
        check("原文件还在 raw", (raw / names[0]).exists())
        check("目标内容没被改写",
              (scraps / names[0]).read_text(encoding="utf-8") == "已经在这儿了")

        print("\n[M6] 撤回不了时不落账")
        ok7, res7 = ops.move_many(tmp, [names[1]], "scraps")
        (scraps / names[1]).unlink()          # 模拟被手工搬走
        ok8, res8 = ops.undo_last(tmp)
        check("撤回如实失败", (not ok8) and res8.get("error"), res8)
        check("失败时日志保留（还能再试/排查）", len(ops.journal(tmp)) == 1, ops.journal(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[接线] 端点与界面")
    misc = (ROOT / "scripts" / "nf_api_domains" / "materials.py").read_text(encoding="utf-8")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("域模块有移动/删除/撤回", all(k in misc for k in
          ("handle_materials_move", "handle_materials_delete", "handle_materials_undo")))
    check("ROUTES 登记四条",
          all(('"/materials/%s"' % p) in misc
              for p in ("move", "delete", "undo")))
    check("分发器接 POST 移动", 'elif p == "/materials/move"' in api)
    check("分发器接 POST 撤回", 'elif p == "/materials/undo"' in api)
    check("界面有批量条", "mat-batch" in app and "matMove('scraps')" in app)
    check("界面有撤回按钮", "撤回上一步" in app and "matUndoLast" in app)
    check("删除走二次确认（应用内，不用 window.confirm）",
          "确认删除（可撤回）" in app and "window.confirm(\\n      \\\"确定删除" not in app)

    print("\n" + "=" * 70)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
