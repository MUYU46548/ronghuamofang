# -*- coding: utf-8 -*-
"""C3 书稿数据跨版本迁移机制回归（utils/workspace_migrate + CLI + 启动钩子）。

守六条契约（模块 docstring）：
1. 判据只有一份（DATA_SCHEMA + MIGRATIONS，CLI / nf_api / orchestrator 同调 ensure）
2. 数据比代码新 = 拒绝运行（fail-closed）
3. 先快照后动手（复用丙3 import_guard），快照失败一个字节都不改
4. 只自动跑 safe 步骤，改写用户文件结构的必须 CLI 显式 --yes
5. 无戳的既有工作区记基线 v1，**不改动任何数据**
6. 幂等；中途失败不推进版本戳（下次重试，不留半迁移状态）

全程离线、零 token、只动临时目录。
用法：python tests/unit/test_workspace_migrate.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
sys.path.insert(0, str(REPO / "scripts"))

from utils import workspace_migrate as wm          # noqa: E402
from utils import import_guard                     # noqa: E402

PASS, FAIL = [], []
TMP = None
BOOK = "data/outline/global.md"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  → " + str(detail)[:200] if detail else ""))


def setup(book="# 旧书\n\n内容\n"):
    global TMP
    TMP = Path(tempfile.mkdtemp(prefix="nf_wmig_"))
    (TMP / "data" / "outline").mkdir(parents=True)
    (TMP / "data" / "state").mkdir(parents=True)
    if book is not None:
        (TMP / BOOK).write_text(book, encoding="utf-8")
    return TMP


def teardown():
    shutil.rmtree(TMP, ignore_errors=True)


def sha(p):
    return (Path(p).read_bytes())


def step_fn(tag="x"):
    """造一步会改 global.md 的迁移（真写文件，不是空壳）。"""
    def fn(r):
        p = Path(r) / BOOK
        p.write_text(p.read_text(encoding="utf-8") + "\n<!-- %s -->\n" % tag,
                     encoding="utf-8")
        return "已追加 %s" % tag
    return fn


def audit_rows(root):
    p = Path(root) / "history" / "import_guard.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------- [1] 基线
def test_baseline():
    print("\n[1] 既有书稿无戳 → 记基线，且不动数据（契约 5）")
    setup()
    try:
        st = wm.status(TMP)
        check("1.1 状态 = baseline", st["action"] == "baseline", st["action"])
        before = sha(TMP / BOOK)
        ok, rep = wm.migrate(TMP)
        check("1.2 迁移成功（只写版本戳）", ok, rep["lines"])
        check("1.3 数据文件逐字节未变", sha(TMP / BOOK) == before)
        stamp = wm.read_stamp(TMP)
        check("1.4 戳记为 v%d" % wm.DATA_SCHEMA, stamp and stamp["schema"] == wm.DATA_SCHEMA,
              stamp)
        check("1.5 戳落在 data/state/（归档/书档搬不走的运行态）",
              wm.stamp_path(TMP).as_posix().endswith(wm.STAMP_REL))
        ok, rep = wm.migrate(TMP)
        check("1.6 再跑 = no-op（幂等）", ok and rep["action"] == "none", rep["action"])
    finally:
        teardown()


def test_fresh_workspace():
    print("\n[2] 空工作区（连 data/ 都没有）也记基线")
    TMP2 = Path(tempfile.mkdtemp(prefix="nf_wmig_empty_"))
    try:
        st = wm.status(TMP2)
        check("2.1 空工作区 = baseline", st["action"] == "baseline", st["action"])
        check("2.2 文案说清是空工作区", "空工作区" in st["message"], st["message"])
        ok, rep = wm.migrate(TMP2)
        check("2.3 记基线成功且不报错", ok, rep["lines"])
        check("2.4 没有被误判成有书稿要迁", rep["action"] == "baseline")
    finally:
        shutil.rmtree(TMP2, ignore_errors=True)


# ---------------------------------------------------------------- [3] 数据比代码新
def test_ahead():
    print("\n[3] 数据比代码新 → 拒绝运行（契约 2，fail-closed）")
    setup()
    try:
        wm._write_stamp(TMP, wm.DATA_SCHEMA + 5, "假装是未来版本写的")
        st = wm.status(TMP)
        check("3.1 状态 = ahead", st["action"] == "ahead", st["action"])
        check("3.2 文案给两条出路（升级程序 / 回退快照）",
              "升级" in st["message"] and "snapshot.py --restore" in st["message"],
              st["message"][:200])
        ok, rep = wm.migrate(TMP)
        check("3.3 migrate 拒绝执行", ok is False)
        check("3.4 戳原样保留（不许被旧程序改回去）",
              wm.read_stamp(TMP)["schema"] == wm.DATA_SCHEMA + 5)
        ok, rep = wm.ensure(TMP)
        check("3.5 启动钩子 ensure 也拒绝（nf_api 据此退出）", ok is False)
    finally:
        teardown()


# ---------------------------------------------------------------- [4] 真迁移
def test_migrate_chain():
    print("\n[4] 链式迁移：v1→v2→v3，真改文件 + 真打快照")
    setup()
    steps = (wm.Step(1, 2, step_fn("v2"), "补一个字段"),
             wm.Step(2, 3, step_fn("v3"), "重建索引"))
    try:
        wm._write_stamp(TMP, 1, "基线")
        before = sha(TMP / BOOK)
        ok, rep = wm.migrate(TMP, current=3, steps=steps)
        check("4.1 两步都跑完", ok and len(rep["applied"]) == 2, rep["lines"])
        check("4.2 文件被两步依次改过",
              sha(TMP / BOOK).count(b"<!-- v2 -->") == 1
              and sha(TMP / BOOK).count(b"<!-- v3 -->") == 1
              and sha(TMP / BOOK) != before)
        check("4.3 戳推进到 v3", wm.read_stamp(TMP)["schema"] == 3)
        check("4.4 先打了快照（丙3 契约）", rep["snapshot_id"] is not None, rep)
        check("4.5 报告里带可直接抄的回退命令",
              rep["rollback"] and "--restore" in rep["rollback"], rep["rollback"])
        rows = [r for r in audit_rows(TMP) if r.get("op") == "workspace_migrate"]
        check("4.6 审计落 history/import_guard.jsonl 且回退可查",
              len(rows) == 1 and rows[0].get("ok") is True, rows)

        ok, rep2 = wm.migrate(TMP, current=3, steps=steps)
        check("4.7 再跑幂等：无步骤、无新快照",
              ok and rep2["action"] == "none" and len(rep2["applied"]) == 0
              and len([r for r in audit_rows(TMP)
                       if r.get("op") == "workspace_migrate"]) == 1, rep2["action"])
    finally:
        teardown()


# ---------------------------------------------------------------- [5] dry-run
def test_dry_run():
    print("\n[5] 预演：不打快照、不写戳、不动文件")
    setup()
    steps = (wm.Step(1, 2, step_fn("v2"), "补一个字段"),)
    try:
        wm._write_stamp(TMP, 1, "基线")
        before = sha(TMP / BOOK)
        ok, rep = wm.migrate(TMP, current=2, steps=steps, dry_run=True)
        check("5.1 预演返回成功", ok, rep["lines"])
        check("5.2 打印了将跑哪一步", any("v1→v2" in l for l in rep["lines"]), rep["lines"])
        check("5.3 没打快照", rep["snapshot_id"] is None)
        check("5.4 没写戳", wm.read_stamp(TMP)["schema"] == 1)
        check("5.5 文件原样", sha(TMP / BOOK) == before)
    finally:
        teardown()


# ---------------------------------------------------------------- [6] 非 safe 步骤
def test_unsafe_gate():
    print("\n[6] 改写用户文件结构的步骤：启动钩子不许自动跑（契约 4）")
    setup()
    steps = (wm.Step(1, 2, step_fn("v2"), "补一个字段", safe=True),
             wm.Step(2, 3, step_fn("v3"), "重排章节文件（会动你的稿子）", safe=False))
    try:
        wm._write_stamp(TMP, 1, "基线")
        ok, rep = wm.ensure(TMP, current=3, steps=steps)
        check("6.1 启动钩子拒绝（停在非 safe 步骤前）", ok is False)
        check("6.2 说清是哪一步、要什么命令",
              any("重排章节文件" in l and "--run --yes" in l for l in rep["lines"]),
              rep["lines"])
        check("6.3 连前面的 safe 步骤也未执行（不落中间状态）",
              wm.read_stamp(TMP)["schema"] == 1
              and sha(TMP / BOOK).count(b"<!-- v2 -->") == 0)
        ok, rep = wm.migrate(TMP, current=3, steps=steps, allow_unsafe=True)
        check("6.4 显式 --yes 后两步都跑完", ok and len(rep["applied"]) == 2, rep["lines"])
        check("6.5 戳到 v3", wm.read_stamp(TMP)["schema"] == 3)
    finally:
        teardown()


# ---------------------------------------------------------------- [7] 失败面
def test_failure_paths():
    print("\n[7] 失败：步骤抛异常 / 快照失败 / 版本链有缺口")
    setup()
    steps = (wm.Step(1, 2, step_fn("v2"), "补一个字段"),)
    try:
        wm._write_stamp(TMP, 1, "基线")

        def boom(r):
            raise RuntimeError("磁盘炸了")
        bad = (wm.Step(1, 2, boom, "会炸的那一步"),)
        ok, rep = wm.migrate(TMP, current=2, steps=bad)
        check("7.1 步骤抛异常 → 失败返回", ok is False)
        check("7.2 报错点名是哪一步", any("会炸的那一步" in l for l in rep["lines"]),
              rep["lines"])
        check("7.3 **版本戳未推进**（不留半迁移状态）", wm.read_stamp(TMP)["schema"] == 1)
        check("7.4 给出回退命令", any("--restore" in l for l in rep["lines"]), rep["lines"])

        # 快照失败 = fail-closed：一个字节都不许改
        before = sha(TMP / BOOK)
        real = import_guard.preimport

        def boom_snap(op, root=None, required=True):
            raise import_guard.ImportGuardError("[import-guard] 快照失败，已中止")
        wm.import_guard.preimport = boom_snap
        try:
            ok, rep = wm.migrate(TMP, current=2, steps=steps)
        finally:
            wm.import_guard.preimport = real
        check("7.5 快照失败 → 迁移中止（丙3 契约）", ok is False)
        check("7.6 文件一个字节没动", sha(TMP / BOOK) == before)
        check("7.7 戳也没写", wm.read_stamp(TMP)["schema"] == 1)

        # 版本链缺口：只有 1→2 的步骤，却要走到 v3
        ok, rep = wm.migrate(TMP, current=3, steps=steps)
        check("7.8 缺口 → 拒绝执行（不静默跳过）", ok is False)
        check("7.9 说清断在哪个版本", any("断了" in l for l in rep["lines"]), rep["lines"])

        dup = steps + (wm.Step(1, 2, step_fn("dup"), "重复登记"),)
        st = wm.status(TMP, current=2, steps=dup)
        check("7.10 同版本两步 → 拒绝猜测顺序", st["action"] == "blocked", st["action"])
    finally:
        teardown()


# ---------------------------------------------------------------- [8] 接线
def test_wiring():
    print("\n[8] 接线：判据只有一份，两个启动入口 + CLI 都调它")
    api = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    orch = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("8.1 nf_api 启动时调 ensure", "workspace_migrate import ensure" in api
          and "_wm_ensure(ROOT)" in api)
    check("8.2 nf_api 拒绝时真的退出（不吞成一行日志继续写数据）",
          "sys.exit(5)" in api and "数据结构检查未通过" in api)
    check("8.2b 数据结构拒绝用 5，预算熔断仍走 run() 返回的 2（两码不混）",
          "sys.exit(5)" in orch and "sys.exit(run(" in orch)
    check("8.3 orchestrator 启动时也调同一份",
          "_wm_ensure(Path.cwd())" in orch and "数据结构检查未通过" in orch)
    check("8.4 两处都不各自实现判据（只 import ensure，不碰版本号/步骤表）",
          "DATA_SCHEMA" not in api and "DATA_SCHEMA" not in orch
          and "MIGRATIONS" not in api and "MIGRATIONS" not in orch)
    check("8.5 CLI 存在且带 --run/--yes/--dry-run",
          (REPO / "scripts" / "data_migrate.py").exists()
          and all(a in (REPO / "scripts" / "data_migrate.py").read_text(encoding="utf-8")
                  for a in ('"--run"', '"--yes"', '"--dry-run"')))
    check("8.6 本仓 MIGRATIONS 现为空（没有历史结构变更，现存书稿即 v1）",
          wm.MIGRATIONS == (), wm.MIGRATIONS)


def test_startup_gate():
    """[9] 真起一次进程：数据比代码新时，两个入口都要当场拒（不是静态接线而已）。"""
    print("\n[9] 启动闸真跑：数据比代码新 → 拒绝启动（退出码 5）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_wmig_boot_"))
    try:
        (tmp / "data" / "state").mkdir(parents=True)
        (tmp / "data" / "outline").mkdir(parents=True)
        (tmp / "data" / "outline" / "global.md").write_text("# 书\n", encoding="utf-8")
        wm._write_stamp(tmp, wm.DATA_SCHEMA + 5, "假装是未来版本写的")
        outs = {}
        for name, args, cwd in (
                ("nf_api", [str(REPO / "scripts" / "nf_api.py"), "--root", str(tmp)], str(REPO)),
                ("orchestrator", [str(REPO / "scripts" / "orchestrator.py")], str(tmp))):
            p = subprocess.run([PY] + args, cwd=cwd, capture_output=True,
                               encoding="utf-8", errors="replace", timeout=180)
            out = (p.stdout or "") + (p.stderr or "")
            outs[name] = out
            check("9.%s 退出码 5（不是 1/2，失败原因可分辨）" % name,
                  p.returncode == 5, (name, p.returncode))
            check("9.%s 说得出原因与两条出路" % name,
                  "拒绝运行" in out and "data_migrate" in out and "snapshot.py --restore" in out,
                  (name, out[-300:]))
        check("9.nf_api 没进服务（连端点列表都没打）",
              "端点:" not in outs.get("nf_api", ""))
        check("9.orchestrator 没开跑（没打印阶段横幅）",
              "阶段" not in outs.get("orchestrator", "")[:200], outs.get("orchestrator", "")[:200])
        # 书稿原样（拒绝运行的全部意义：数据没被旧代码碰过）
        stamp = wm.read_stamp(tmp)
        check("9.数据未被改动（戳仍是未来版本）", stamp["schema"] == wm.DATA_SCHEMA + 5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_baseline()
    test_fresh_workspace()
    test_ahead()
    test_migrate_chain()
    test_dry_run()
    test_unsafe_gate()
    test_failure_paths()
    test_wiring()
    test_startup_gate()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
