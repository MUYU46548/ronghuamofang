# -*- coding: utf-8 -*-
"""产物闸的判据：未批准的产物，程序不承认。

## 为什么这条判据必须存在

`engine: self` 下执行方（agent）按任务书写产物。改前是直接写正式路径，
程序只看"文件在不在"就判完成 —— 那等于没有任何人能作主（用户 2026-11-01
与 2026-11-11 两次同一句判词）。

## 判据（每条都会失败：删掉对应实现即红）

A. 没有候选 → 拒绝合成，且**正式路径一个字节都不创建**
B. 有候选没人批 → 拒绝合成
C. 批了且内容没变 → 合成成功，内容逐字节一致
D. **批完又被改过 → stale，拒绝合成**（拿旧批准套新稿是这里最容易犯的错）
E. 合成前旧正式文件被备份（合错能退）
F. 没登记的阶段不受影响（渐进接入不会拦死别的阶段）
G. 候选路径保留层级（两个阶段同名产物不互相覆盖）

零 LLM、零费用、不碰工作区数据（全部在临时目录里造）。
用法：python tests/unit/test_product_gate.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    from utils import product_gate as pg

    rel = "data/setting/setting.json"
    print("【A】没有候选 → 拒绝，且不创建正式文件")
    root = Path(tempfile.mkdtemp(prefix="nf_gate_"))
    st = pg.status(root, 1, rel)
    check("状态 no_pending", st["state"] == "no_pending", st)
    ok, msg, _info = pg.promote(root, 1, rel)
    check("promote 被拒", ok is False, msg)
    check("正式路径没被创建", not (root / rel).exists())

    print("\n【B】有候选、没人批 → 拒绝")
    pend = pg.pending_path(root, 1, rel)
    pend.parent.mkdir(parents=True, exist_ok=True)
    pend.write_text('{"characters": [{"name": "甲"}]}', encoding="utf-8")
    st = pg.status(root, 1, rel)
    check("状态 pending", st["state"] == "pending", st)
    ok, msg, _info = pg.promote(root, 1, rel)
    check("promote 仍被拒", ok is False, msg)
    check("正式路径仍未创建", not (root / rel).exists())

    print("\n【C】批准后合成，内容逐字节一致")
    ok, info = pg.record_approval(root, 1, rel, by="unit-test")
    check("批准成功", ok is True, info)
    st = pg.status(root, 1, rel)
    check("状态 approved", st["state"] == "approved", st)
    ok, msg, info = pg.promote(root, 1, rel)
    check("promote 成功", ok is True, msg)
    check("正式文件内容 = 候选",
          (root / rel).read_bytes() == pend.read_bytes())

    print("\n【D】批完又改候选 → stale，拒绝（防偷渡）")
    pend.write_text('{"characters": [{"name": "乙"}]}', encoding="utf-8")
    st = pg.status(root, 1, rel)
    check("状态 stale", st["state"] == "stale", st)
    ok, msg, _info = pg.promote(root, 1, rel)
    check("promote 被拒", ok is False, msg)
    check("正式文件没有被偷偷改成乙", "乙" not in (root / rel).read_text(encoding="utf-8"))

    print("\n【E】重新批准 → 合成前备份旧正式文件")
    ok, _info = pg.record_approval(root, 1, rel, by="unit-test")
    check("重批成功", ok is True)
    ok, msg, info = pg.promote(root, 1, rel)
    check("promote 成功", ok is True, msg)
    check("返回了备份路径", bool(info.get("backup")), info)
    check("备份文件存在且是上一版（甲）",
          bool(info.get("backup")) and "甲" in (root / info["backup"]).read_text(encoding="utf-8"),
          info.get("backup"))

    print("\n【F】登记表覆盖 1~6（7/8 是本地导出，故意不在内）")
    for _n in range(1, 7):
        check("stage %d 已登记" % _n, pg.is_registered(_n))
    check("stage 7 / 8 不登记（本地导出，不经外部执行方）",
          not pg.is_registered(7) and not pg.is_registered(8))

    print("\n【G】候选路径保留层级，不同阶段同名不覆盖")
    a = pg.pending_rel(1, rel)
    b = pg.pending_rel(2, rel)
    check("两级路径不同", a != b, (a, b))
    check("保留原层级", a.endswith(rel) and b.endswith(rel), (a, b))

    print("\n【H】阶段 4：目录型产物（逐章正文）")
    rel4 = "data/chapters/raw/01.md"
    check("阶段 4 已登记", pg.is_registered(4))
    p4 = pg.pending_path(root, 4, rel4)
    p4.parent.mkdir(parents=True, exist_ok=True)
    p4.write_text("## 第1章 测试\n\n正文。\n", encoding="utf-8")
    pg.pending_path(root, 4, "data/chapters/raw/02.md").write_text(
        "## 第2章 测试\n\n正文。\n", encoding="utf-8")
    rels4 = pg.product_rels(root, 4)
    check("从候选目录展开出两章", len(rels4) == 2, rels4)
    check("清单里是正式相对路径（不是候选路径）",
          all(not r.startswith("data/_pending") for r in rels4), rels4)
    check("未批 → 不合成", pg.status(root, 4, rel4)["state"] == "pending")
    ok, _msg, _info = pg.promote(root, 4, rel4)
    check("未批 promote 被拒", ok is False)
    check("正式路径没被创建", not (root / rel4).exists())
    pg.record_approval(root, 4, rel4, by="unit-test")
    ok, msg, _info = pg.promote(root, 4, rel4)
    check("批准后合成成功", ok is True, msg)
    check("第 1 章进了正式路径", (root / rel4).exists())
    check("第 2 章仍在候选（没被顺带合进去）",
          not (root / "data/chapters/raw/02.md").exists())

    print("\n【I】阶段 2/3/5/6：候选 → 批准 → 合成（每阶段一条，防只接了一半）")
    root2 = Path(tempfile.mkdtemp(prefix="nf_gate_all_"))
    cases = [
        (2, "data/outline/global.md"),
        (3, "data/outline/chapters/01.md"),
        (5, "data/chapters/checked/01.md"),
        (6, "data/chapters/refined/01.md"),
    ]
    for stage, r in cases:
        p = pg.pending_path(root2, stage, r)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# 阶段%d 候选\n\n正文。\n" % stage, encoding="utf-8")
        check("阶段%d 候选出现在清单里" % stage, r in pg.product_rels(root2, stage))
        check("阶段%d 未批 → 不合成" % stage,
              pg.status(root2, stage, r)["state"] == "pending"
              and pg.promote(root2, stage, r)[0] is False
              and not (root2 / r).exists())
        pg.record_approval(root2, stage, r, by="unit-test")
        ok, msg, _info = pg.promote(root2, stage, r)
        check("阶段%d 批准后合成，内容逐字节一致" % stage,
              ok and (root2 / r).read_bytes() == p.read_bytes(), msg)
        check("阶段%d 合成后 approved_formal 为真" % stage,
              pg.approved_formal(root2, stage, r))

    print("\n【J】promote_approved 只合成批过的那几份")
    r5a, r5b = "data/chapters/checked/01.md", "data/chapters/checked/02.md"
    p5b = pg.pending_path(root2, 5, r5b)
    p5b.write_text("# 第2章检查稿\n\n正文。\n", encoding="utf-8")
    pg.record_approval(root2, 5, r5a, by="unit-test")     # 01 重批（未变更，仍 approved）
    done = pg.promote_approved(root2, 5)
    check("只合成 01（02 没批，留在候选区）",
          r5a in done and r5b not in done, done)
    check("02 的正式路径没被创建", not (root2 / r5b).exists())
    check("02 仍在清单里等批准",
          pg.status(root2, 5, r5b)["state"] == "pending")

    print("\n【K】直接放在正式路径、没有批准记录 → approved_formal 为假（复用捷径的后门判据）")
    root3 = Path(tempfile.mkdtemp(prefix="nf_gate_bypass_"))
    (root3 / rel).parent.mkdir(parents=True, exist_ok=True)
    (root3 / rel).write_text('{"characters": [{"name": "丙"}]}', encoding="utf-8")
    check("文件在但没批过 → 不算已批准", pg.approved_formal(root3, 1, rel) is False)
    # 批准必须有候选（不许批空气），所以把同一份内容放进候选区再批
    p3 = pg.pending_path(root3, 1, rel)
    p3.parent.mkdir(parents=True, exist_ok=True)
    p3.write_text('{"characters": [{"name": "丙"}]}', encoding="utf-8")
    pg.record_approval(root3, 1, rel, by="unit-test")
    check("批过之后才算", pg.approved_formal(root3, 1, rel) is True)
    (root3 / rel).write_text('{"characters": [{"name": "丁"}]}', encoding="utf-8")
    check("批过之后文件又被改 → 又变回不算", pg.approved_formal(root3, 1, rel) is False)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
