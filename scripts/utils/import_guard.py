# -*- coding: utf-8 -*-
"""before-import 快照备份契约（丙3 · 催更姬 #5 落地）。

## 为什么需要它

2026-10-04 的事故：进度全清后重跑，实烧 116,413 token。事后看，破坏性操作
（换书归档 / 恢复归档 / 打回清产物）当时**没有任何强制备份点**：

- `switch_book.archive` / `restore` —— 一个快照都不打；
- `reject.py` —— 打了，但写成 `except Exception: "Snapshot failed (continuing)"`，
  快照失败照样继续删产物（fail-open）。

于是"备份"是**自觉**而非**契约**：有则记之、无则照干，失败还被吞成一行提示。
真出事时既没有可回退点，也不知道该回退到哪一条。

## 契约三条

1. **先快照，后动手**：受管操作必须在改动工作区**之前**落下快照。
2. **备份不了就不许动**：快照抛异常 → `ImportGuardError`，操作中止（fail-closed）。
   绝不"继续执行"，绝不把失败吞成一行提示。
3. **留痕可回退**：每次（成功与失败都算）追加一行审计到
   `history/import_guard.jsonl`；成功行带 `rollback` 命令
   （`python scripts/snapshot.py --restore <ID> --yes`），出事后照抄即可回退。

审计放 `history/` 而不是 `data/state/`：`switch_book.archive` 会把整个 `data/`
搬进书档，审计文件若在 data/ 下会被一起搬走 —— 工作区反而查不到该回退到哪。
`history/` 不在归档范围内，与它记录的快照待在同一处。

## 边界（刻意如此，别扩大解读）

- **审计写失败不阻断**（快照已在，回退面是完整的）；只有快照失败才阻断。
  日志写不进去不该连带把用户的数据操作卡死。
- 快照只覆盖 `snapshot.SNAPSHOT_ITEMS`（progress / setting / outline /
  chapters / summaries / merged）。`config/` 与 `materials/` 的回退靠归档机制
  本身（书档 + `_meta.json` 逐项合并），不在本契约范围内 —— 别拿它当整仓备份。
- 本模块**只提供闸门，不提供回退**：回退入口是既有的 `snapshot.py --restore`，
  不在这里重造一遍（判据写两遍必然漂移）。

用法：

    from utils.import_guard import ImportGuardError, preimport, guard

    try:
        entry = preimport("switch_book.archive", root=PROJECT_ROOT)
    except ImportGuardError as e:
        return False, str(e)          # 快照失败 → 本次操作不执行

    # 或者用上下文管理器（进入即快照，异常直接往上抛）：
    with guard("some.op", root=root):
        ...
"""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 审计落在 history/（归档不动它，且与快照同处）—— 详见模块 docstring
AUDIT_NAME = "history/import_guard.jsonl"

# 快照 label 里不许出现路径分隔符（dest = history/{ts}_{label}，带 "/" 会造子目录）
_UNSAFE_LABEL = "\\/:*?\"<>|"


class ImportGuardError(RuntimeError):
    """快照失败 —— 受管操作必须就此中止（fail-closed）。"""


def _resolve_root(root=None):
    return Path(root).resolve() if root else Path.cwd().resolve()


def _safe_label(op):
    cleaned = "".join("_" if c in _UNSAFE_LABEL else c for c in str(op))
    return cleaned[:60] or "unnamed"


def _write_audit(root, entry):
    """追加一行审计。返回 True=写成，False=写失败（调用方自行决定要不要吵）。"""
    try:
        p = Path(root) / AUDIT_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:                                       # noqa: BLE001
        return False


def preimport(op, root=None, required=True):
    """受管操作的**前置快照**（before-import）。

    成功返回审计条目（含 `snapshot_id` / `rollback`）；`required=True` 且快照
    失败时抛 `ImportGuardError`（调用方必须据此中止操作）。`required=False`
    只记账不阻断，给"顺手留个痕"的非破坏性场景用。
    """
    root = _resolve_root(root)
    entry = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "op": op,
        "root": str(root),
        "ok": False,
    }
    try:
        # 延迟 import：缺 scripts/snapshot.py 时给出可行动报错，而不是模块导入期的
        # ImportError 把调用方整个掀翻（switch_book 这类入口在临时根里跑过）。
        from snapshot import snapshot as make_snapshot
        dest = make_snapshot("before_" + _safe_label(op),
                             history_dir=str(root / "history"), root=str(root))
    except Exception as e:                                   # noqa: BLE001
        entry["error"] = f"{type(e).__name__}: {e}"
        _write_audit(root, entry)
        if required:
            raise ImportGuardError(
                f"[import-guard] 快照失败，已中止「{op}」（契约：备份不了就不许动）。\n"
                f"  原因: {entry['error']}\n"
                f"  排查: 确认 {root}/history 可写、磁盘未满；"
                f"单独跑 python scripts/snapshot.py \"{op}\" 看完整报错。"
            ) from e
        return entry

    entry.update(ok=True, snapshot_id=dest.name, snapshot_path=str(dest))
    entry["rollback"] = f"python scripts/snapshot.py --restore {dest.name} --yes"
    if not _write_audit(root, entry):
        # 审计写失败不阻断：快照已在，回退面完整（见模块 docstring「边界」）
        print(f"[import-guard] 审计写入失败（不影响回退）: {root / AUDIT_NAME}")
    return entry


@contextmanager
def guard(op, root=None, required=True):
    """`with guard(...): <破坏性操作>` —— 进入即快照，失败即抛、操作不执行。"""
    preimport(op, root=root, required=required)
    yield


def audit(op=None, root=None, limit=50):
    """读审计，**最新在前**。文件缺失/行损坏一律跳过，不抛。"""
    root = _resolve_root(root)
    p = Path(root) / AUDIT_NAME
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:                                    # noqa: BLE001
            continue
    if op:
        rows = [r for r in rows if r.get("op") == op]
    return rows[-max(1, int(limit)):][::-1]


def rollback_hint(op=None, root=None):
    """最近一次的回退命令；没有记录时给一句可行动的空状态。"""
    rows = audit(op=op, root=root, limit=1)
    if not rows:
        return "(尚无 before-import 快照记录 —— 还没跑过受管操作)"
    r = rows[0]
    if r.get("ok") and r.get("rollback"):
        return r["rollback"]
    return f"最近一次「{r.get('op')}」快照失败，无回退点: {r.get('error', '未知原因')}"


if __name__ == "__main__":
    # 只读自检入口：列审计 + 给最近一条的回退命令（不产生任何写操作）
    print("审计（最新在前）:")
    for r in audit(limit=20):
        flag = "OK  " if r.get("ok") else "FAIL"
        print(f"  [{flag}] {r.get('at')}  {r.get('op')}  "
              f"{r.get('snapshot_id') or r.get('error', '')}")
    print("回退入口:", rollback_hint())
