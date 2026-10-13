# -*- coding: utf-8 -*-
"""丙3 · before-import 快照备份契约回归 —— 22 断言。

## 这套用例守什么

2026-10-04 进度全清重跑实烧 116,413 token。缺的不是"有没有备份"，是**契约**：
- `switch_book.archive/restore` 当时一个快照都不打；
- `reject.py` 打了但写成 `Snapshot failed (continuing)`（fail-open，失败照删）。

`utils/import_guard.py` 把三条契约钉死，本文件逐条断言：
1. 先快照后动手（成功路径 + 快照内容非空壳）
2. 备份不了就不许动（fail-closed + 块体不执行）
3. 留痕可回退（history/import_guard.jsonl 审计 + rollback 命令）

外加两条**接线**断言：switch_book 真的在归档前落了快照（子进程实跑），
reject.py 的 fail-open 文案必须已经消失（静态）。

全程离线、零 token、只动临时目录。
用法：python tests/unit/test_import_guard.py
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import snapshot as snap_mod  # noqa: E402
from utils.import_guard import (  # noqa: E402
    AUDIT_NAME, ImportGuardError, audit, guard, preimport, rollback_hint,
)

PASS, FAIL = [], []
TMP = None
ORIG_CWD = None


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  → " + str(detail)[:200] if detail else ""))


def setup(prefix="nf_ig_"):
    global TMP, ORIG_CWD
    ORIG_CWD = os.getcwd()
    TMP = Path(tempfile.mkdtemp(prefix=prefix))
    os.chdir(TMP)
    (TMP / "data" / "setting").mkdir(parents=True)
    (TMP / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
    return TMP


def teardown():
    os.chdir(ORIG_CWD)
    shutil.rmtree(TMP, ignore_errors=True)


# ---------------------------------------------------------------- 1. 成功路径
def test_ok():
    print("\n[1] 先快照后动手（成功路径）")
    setup()
    try:
        entry = preimport("unit.ok", root=TMP)
        check("1.1 成功返回 ok=True", entry.get("ok") is True, entry)
        sid = entry.get("snapshot_id") or ""
        check("1.2 snapshot_id 带操作名（便于人眼定位）", "before_unit.ok" in sid, sid)
        check("1.3 快照目录真实存在", (TMP / "history" / sid).is_dir(), sid)
        check("1.4 快照里真有产物（不是空壳目录）",
              (TMP / "history" / sid / "data" / "setting" / "setting.json").exists())
        rb = entry.get("rollback", "")
        check("1.5 rollback 可直接抄（含 --restore 与 --yes）",
              "--restore" in rb and "--yes" in rb, rb)
        check("1.6 审计文件已落盘", (TMP / AUDIT_NAME).exists(), AUDIT_NAME)
        rows = audit(root=TMP, limit=5)
        check("1.7 审计行带 op/root/at",
              bool(rows) and rows[0].get("op") == "unit.ok"
              and bool(rows[0].get("root")) and bool(rows[0].get("at")), rows[:1])
        check("1.8 审计行带 rollback 命令",
              bool(rows) and str(rows[0].get("rollback", "")).startswith("python scripts/snapshot.py"))
        check("1.9 rollback_hint 给出最新回退命令",
              rollback_hint(op="unit.ok", root=TMP).startswith("python scripts/snapshot.py"))
        # 审计必须在 history/ 下：archive() 会把 data/ 整个搬进书档，
        # 写在 data/state/ 的审计会被一起搬走，工作区反而查不到回退点。
        check("1.10 审计在 history/（不进 data/，换书搬不走）",
              (TMP / "history" / "import_guard.jsonl").exists()
              and not (TMP / "data" / "history").exists())
    finally:
        teardown()


# ---------------------------------------------------------------- 2. root 语义
def test_root_semantics():
    print("\n[2] root 必须按调用方的根解析（否则静默空备份）")
    setup()
    elsewhere = Path(tempfile.mkdtemp(prefix="nf_ig_else_"))
    try:
        # cwd 故意指向别处 —— 旧实现用相对路径，会 copied=0 却报成功
        os.chdir(elsewhere)
        dest = snap_mod.snapshot("root_probe", history_dir=str(TMP / "history"), root=str(TMP))
        check("2.1 cwd 不在项目根时快照仍按 root 取文件",
              (dest / "data" / "setting" / "setting.json").exists(), dest)
        check("2.2 别处 cwd 没被误当项目根（没在 elsewhere 造 history）",
              not (elsewhere / "history").exists())
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(elsewhere, ignore_errors=True)
        teardown()


# ---------------------------------------------------------------- 3. fail-closed
def test_fail_closed():
    print("\n[3] 备份不了就不许动（fail-closed）")
    setup()
    orig = snap_mod.snapshot

    def boom(*_a, **_k):
        raise OSError("磁盘已满（夹具注入）")

    snap_mod.snapshot = boom
    try:
        err = None
        try:
            preimport("boom.op", root=TMP)
        except ImportGuardError as e:                      # noqa: PERF203
            err = e
        check("3.1 快照失败 → 抛 ImportGuardError（不静默继续）", err is not None)
        check("3.2 报错含可行动排查指引", err is not None and "排查" in str(err))
        rows = audit(root=TMP, limit=5)
        check("3.3 失败也留痕（ok=false）",
              bool(rows) and rows[0].get("ok") is False, rows[:1])
        check("3.4 审计记下失败原因",
              bool(rows) and "磁盘已满" in str(rows[0].get("error", "")))
        e2 = preimport("boom.op2", root=TMP, required=False)
        check("3.5 required=False 时只记账不阻断",
              e2.get("ok") is False and bool(e2.get("error")), e2)
        ran = []
        try:
            with guard("boom.op3", root=TMP):
                ran.append(1)
        except ImportGuardError:
            pass
        check("3.6 guard 失败 → 块体一次都没执行", not ran, ran)
        check("3.7 rollback_hint 如实说没有回退点",
              "快照失败" in rollback_hint(op="boom.op", root=TMP),
              rollback_hint(op="boom.op", root=TMP))
    finally:
        snap_mod.snapshot = orig
        teardown()


# ---------------------------------------------------------------- 4. 空状态
def test_empty_state():
    print("\n[4] 空状态可读（不抛、给出下一步）")
    empty = Path(tempfile.mkdtemp(prefix="nf_ig_empty_"))
    try:
        check("4.1 无审计文件 → audit 返回空列表", audit(root=empty) == [])
        check("4.2 无记录时 rollback_hint 给占位文案",
              "尚无 before-import 快照记录" in rollback_hint(root=empty),
              rollback_hint(root=empty))
        check("4.3 损坏行被跳过而不是整表读崩",
              _audit_tolerates_garbage(empty))
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def _audit_tolerates_garbage(root):
    p = Path(root) / AUDIT_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{坏行\n\n" + json.dumps({"op": "good", "ok": True}) + "\n",
                 encoding="utf-8")
    rows = audit(root=root)
    return len(rows) == 1 and rows[0].get("op") == "good"


# ---------------------------------------------------------------- 5. 接线：switch_book
def _build_switch_root():
    root = Path(tempfile.mkdtemp(prefix="nf_ig_sb_"))
    (root / "scripts").mkdir(parents=True)
    for name in ("switch_book.py", "snapshot.py"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    shutil.copytree(ROOT / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (root / "config").mkdir()
    (root / "config" / "system.yaml").write_text("engine: direct\n", encoding="utf-8")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 测试书\n  chapters: 12\n", encoding="utf-8")
    (root / "materials" / "raw").mkdir(parents=True)
    (root / "materials" / "raw" / "素材.md").write_text("素材\n", encoding="utf-8")
    (root / "data" / "setting").mkdir(parents=True)
    (root / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
    (root / "data" / "progress.json").write_text(
        json.dumps({"project": "测试书"}, ensure_ascii=False), encoding="utf-8")
    return root


def test_switch_book_wiring():
    print("\n[5] 接线：换书归档动手前真的打了快照（子进程实跑）")
    root = _build_switch_root()
    try:
        p = subprocess.run(
            [sys.executable, str(root / "scripts" / "switch_book.py"),
             "--archive", "--yes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(root), timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        check("5.1 归档成功（exit 0）", p.returncode == 0, f"rc={p.returncode} {out[:200]}")
        snaps = [d for d in (root / "history").glob("*before_switch_book.archive")
                 if d.is_dir()] if (root / "history").exists() else []
        check("5.2 归档前落下 before-import 快照", bool(snaps), str(snaps))
        check("5.3 快照含归档前的产物",
              bool(snaps) and (snaps[0] / "data" / "setting" / "setting.json").exists())
        check("5.4 输出报出回退点（用户能照抄回退）", "回退点" in out, out[-300:])
        audit_file = root / AUDIT_NAME
        check("5.5 审计文件在项目根 history/ 下", audit_file.exists())
        rows = []
        if audit_file.exists():
            for line in audit_file.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:                          # noqa: BLE001
                    continue
        check("5.6 审计 op 与回退命令齐备",
              any(r.get("op") == "switch_book.archive" and r.get("ok")
                  and str(r.get("rollback", "")).startswith("python scripts/snapshot.py")
                  for r in rows), rows)
        check("5.7 产物确实被归档（契约没挡住正常流程）",
              (root / "data" / "books" / "测试书" / "setting" / "setting.json").exists())
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 6. 接线：reject
def test_reject_wiring():
    print("\n[6] 接线：reject 的 fail-open 已经拆掉（静态断言）")
    src = (ROOT / "scripts" / "reject.py").read_text(encoding="utf-8")
    # 判据看**代码行**：那句旧文案如今只作为注释里的"改前长什么样"留档，
    # 真正要防的是它作为字符串出现在执行路径上（快照失败仍继续删产物）。
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    check("6.1 执行路径上不再有 'Snapshot failed (continuing)' 放行",
          not any("(continuing)" in l for l in code_lines))
    check("6.2 走 import_guard.preimport", "preimport(" in src)
    check("6.3 快照失败会 return False（中止打回）",
          "except ImportGuardError" in src and "return False, [str(e)]" in src)
    sb = (ROOT / "scripts" / "switch_book.py").read_text(encoding="utf-8")
    check("6.4 switch_book.archive 受管（有 guard_snapshot 开关）",
          "guard_snapshot" in sb and 'preimport("switch_book.archive"' in sb)
    check("6.5 switch_book.restore 受管", 'preimport("switch_book.restore"' in sb)
    # 契约依赖：snapshot() 必须支持按 root 解析，否则按 __file__ 定根的调用方
    # 会拿到"成功但空"的快照（静默假备份）
    import inspect
    sig = inspect.signature(snap_mod.snapshot)
    check("6.6 snapshot() 支持 root 参数", "root" in sig.parameters)


def main():
    test_ok()
    test_root_semantics()
    test_fail_closed()
    test_empty_state()
    test_switch_book_wiring()
    test_reject_wiring()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
