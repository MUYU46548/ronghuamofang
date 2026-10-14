# -*- coding: utf-8 -*-
"""书稿数据的跨版本迁移机制（C3「升级断崖」的机制侧答案）。

## 它解决什么

安装版升级时，**代码与提示词会被刷新**（Electron 侧 `seedWorkspace` 比对工作区的
`.seed-version` 与 `SEED_VERSION`，force 覆盖 `scripts/` `prompts/` `templates/`，
`config/` 只补缺、绝不覆盖），而 **`data/`（书稿：设定集 / 大纲 / 章节 / 进度 /
归档）从不被碰** —— 不碰是对的，可一旦新版改了数据结构，旧书稿就变成
「程序读不读得懂全凭运气」：发布说明只能写「更早版本千万不可用」，
用户既不敢升级、也不敢再开旧书。这就是升级断崖。

本模块给数据侧补上与代码侧**对称**的那半边：

    代码 / 提示词 ── Electron `seedWorkspace`（`.seed-version` ↔ `SEED_VERSION`）
    书稿数据     ── 本模块（`data/state/workspace_schema.json` ↔ `DATA_SCHEMA`）

## 六条契约

1. **判据只有一份**：`DATA_SCHEMA` = 当前代码期望的数据结构版本，
   `MIGRATIONS` = 有序步骤表（`from_v → to_v`）。CLI（`scripts/data_migrate.py`）、
   `nf_api` 启动、`orchestrator` 启动都调 `ensure()`，不在别处各写一遍判断。
2. **数据比代码新 = 拒绝运行**（fail-closed）：新版程序写过的书稿，旧版程序
   不保证读得懂，更不保证写不坏。此时给出两条出路（升级程序 / 回退快照），
   并让启动入口拒绝继续 —— 硬跑只会把数据写坏。
3. **先快照后动手**：真迁移前过 `import_guard.preimport`（丙3 契约）——
   快照失败即中止，一个字节都不改；成功行带可直接抄的回退命令。
4. **只自动跑 safe 步骤**：`Step.safe=False`（要改写用户文件结构的）必须 CLI
   显式 `--run --yes`，启动钩子一律停在它前面并说明原因 —— 与止烧阈值
   「越界值拒绝 + 出厂预设」同一哲学：机制可以自动，动用户的东西不行。
5. **基线不改数据**：没有版本戳的既有工作区一律记为 **v1**（它们本就是 v1 时代
   的产物），只写戳、不动数据 —— 装上这套机制不会惊动任何一本书。
6. **幂等**：戳到位即 no-op；中途失败**不推进版本戳**（下次启动重试同一批步骤，
   不会出现「半迁移状态被当成已完成」）。

## 边界（别扩大解读）

- 只管 `data/` 与书稿结构。`config/` 的兼容性另说（`config_io` 的严格 loader +
  定向改写已经覆盖重复键/格式错这一类）；`.env`、`materials/` 原料不在这里。
- 快照只覆盖 `snapshot.SNAPSHOT_ITEMS`（progress / setting / outline / chapters /
  summaries / merged）—— 与丙3 同一边界，别拿它当整仓备份。
- 本模块**不提供回退**：回退入口是既有的 `snapshot.py --restore`（判据写两遍必漂移）。

用法：

    python scripts/data_migrate.py             # 状态（只读，默认）
    python scripts/data_migrate.py --run       # 执行（safe 步骤；非 safe 还要 --yes）
    python scripts/data_migrate.py --dry-run   # 预演：打不打快照、跑哪几步、动哪些文件
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_io import read_text, write_text  # noqa: E402
from utils import import_guard  # noqa: E402（模块级引用：测试要能打桩它的 preimport）

# 当前代码期望的数据结构版本。**改数据结构时在这里 +1 并加一个 Step**，
# 两处不同步 = 迁移链断裂（status() 会大声拒绝，不会静默跳过）。
DATA_SCHEMA = 1

STAMP_REL = "data/state/workspace_schema.json"


class Step:
    """一步迁移：把数据从 from_v 升到 to_v。

    `safe=True` 表示「补字段 / 建索引 / 补默认值」这类**不删不改用户内容**的
    步骤，可以在启动时自动跑；`safe=False` 表示会改写用户文件结构，必须
    CLI 显式确认（契约 4）。`fn(root)` 返回一句「这步做了什么」，抛异常即失败。
    """

    def __init__(self, from_v, to_v, fn, title, safe=True):
        self.from_v = int(from_v)
        self.to_v = int(to_v)
        self.fn = fn
        self.title = str(title)
        self.safe = bool(safe)

    def __repr__(self):                                   # pragma: no cover
        return "Step(v%d→v%d %s)" % (self.from_v, self.to_v, self.title)


# 有序步骤表。当前为空：截至 v1，本仓没有任何**改数据结构**的历史变更
# （progress.json 新键一律 `.get()` 兜底、setting.json 四顶层键未变、
#  runs.db 自带 `_migrate` 补列、新文件天然兼容），所以现存全部书稿就是 v1。
# **以后改数据结构时在这里加一条**，别指望「反正没人升级」。
MIGRATIONS = ()


# ---------------------------------------------------------------- 戳的读写
def stamp_path(root):
    return Path(root) / STAMP_REL


def read_stamp(root):
    """读版本戳；缺失/损坏一律返回 None（损坏按「没戳」处理，走基线流程）。"""
    p = stamp_path(root)
    if not p.exists():
        return None
    try:
        data = json.loads(read_text(p))
    except Exception:                                       # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["schema"] = int(data.get("schema"))
    except Exception:                                       # noqa: BLE001
        return None
    return data


def _write_stamp(root, schema, note):
    p = stamp_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = read_stamp(root) or {}
    hist = list(old.get("history") or [])
    hist.append({"at": now, "from": old.get("schema", None), "to": int(schema),
                 "note": note})
    payload = {"schema": int(schema), "updated_at": now, "note": note,
               "history": hist[-20:], "migrations": len(MIGRATIONS)}
    write_text(p, json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


# ---------------------------------------------------------------- 状态判据
def _plan(found, current, steps):
    """按「found → DATA_SCHEMA」排出要跑的步骤；返回 (plan, err)。

    plan 是有序 Step 列表（可能为空）；err 非空表示**排不出来**（缺口 /
    断链 / 重复），调用方必须拒绝执行而不是跳过 —— 静默跳过等于把用户
    的书留在半旧结构上继续跑。
    """
    found = int(found)
    plan = []
    while found < current:
        nxt = [s for s in steps if s.from_v == found]
        if not nxt:
            return [], ("版本链在 v%d 断了：没有 from_v=%d 的迁移步骤 "
                        "（当前代码期望 v%d）。补一个 Step，或把数据回退到"
                        "对应版本的快照。" % (found, found, current))
        if len(nxt) > 1:
            return [], ("v%d 有 %d 个迁移步骤（重复登记），拒绝猜测先跑哪个"
                        % (found, len(nxt)))
        plan.append(nxt[0])
        found = nxt[0].to_v
    return plan, ""


def status(root, current=None, steps=None):
    """只读状态：要不要动、动哪几步、哪几步需要人工确认。

    返回 dict：`action` ∈ `none`（已是最新）/ `baseline`（无戳，记 v1）/
    `migrate`（有步骤待跑）/ `ahead`（数据比代码新，拒绝运行）/
    `blocked`（版本链排不出来）。
    """
    current = DATA_SCHEMA if current is None else int(current)
    steps = MIGRATIONS if steps is None else tuple(steps)
    stamp = read_stamp(root)
    has_data = (Path(root) / "data").exists()
    out = {"found": (stamp or {}).get("schema"), "current": current,
           "stamp": stamp, "steps": [], "unsafe_pending": [],
           "action": "none", "message": ""}

    if stamp is None:
        # 全新工作区与既有老工作区都走这条：一律记基线，不碰数据（契约 5）
        out["action"] = "baseline"
        out["message"] = ("无数据版本戳 → 记为基线 v%d（%s；不改动任何数据）"
                          % (current, "空工作区" if not has_data else "既有书稿"))
        return out

    found = stamp["schema"]
    if found > current:
        out["action"] = "ahead"
        out["message"] = ("这份工作区的数据结构是 v%d，而本程序只认到 v%d —— "
                          "旧程序读写新书稿可能静默损坏数据，已拒绝运行。"
                          "出路两条：① 升级到更新版本的绒花墨坊；"
                          "② 回退数据快照（python scripts/snapshot.py "
                          "--restore <最近一次迁移前的快照> --yes）" % (found, current))
        return out
    if found == current:
        out["message"] = "数据结构 v%d，与代码一致，无需迁移" % current
        return out

    plan, err = _plan(found, current, steps)
    if err:
        out["action"] = "blocked"
        out["message"] = err
        return out
    out["steps"] = [{"from": s.from_v, "to": s.to_v, "title": s.title,
                     "safe": s.safe} for s in plan]
    out["unsafe_pending"] = [s.title for s in plan if not s.safe]
    out["action"] = "migrate"
    out["message"] = ("数据 v%d → v%d：待跑 %d 步%s"
                      % (found, current, len(plan),
                         "（其中 %d 步需人工确认 --yes）" % len(out["unsafe_pending"])
                         if out["unsafe_pending"] else ""))
    return out


# ---------------------------------------------------------------- 执行
def migrate(root, current=None, steps=None, dry_run=False, allow_unsafe=False):
    """执行迁移。返回 `(ok, report)`；report 是 dict（含给人看的 `lines`）。

    失败一律**不推进版本戳**（契约 6）：半迁移状态会被下次启动重试，
    而不会被当成「已经迁好了」。
    """
    current = DATA_SCHEMA if current is None else int(current)
    steps = MIGRATIONS if steps is None else tuple(steps)
    st = status(root, current=current, steps=steps)
    rep = {"action": st["action"], "applied": [], "snapshot_id": None,
           "rollback": None, "lines": [], "schema_before": st["found"],
           "schema_after": st["found"]}
    lines = rep["lines"]

    if st["action"] in ("none",):
        lines.append("[migrate] " + st["message"])
        rep["schema_after"] = st["found"]
        return True, rep

    if st["action"] in ("ahead", "blocked"):
        lines.append("[migrate] 拒绝执行：" + st["message"])
        return False, rep

    if st["action"] == "baseline":
        lines.append("[migrate] " + st["message"])
        if dry_run:
            lines.append("[migrate] dry-run：不写版本戳")
            return True, rep
        _write_stamp(root, current, "baseline")
        rep["schema_after"] = current
        lines.append("[migrate] 已记基线 v%d（只写 %s）" % (current, STAMP_REL))
        return True, rep

    # action == migrate
    plan, err = _plan(st["found"], current, steps)
    if err:                                                 # 状态刚判过，防御性再判
        lines.append("[migrate] 拒绝执行：" + err)
        return False, rep
    auto = [s for s in plan if s.safe]
    held = [s for s in plan if not s.safe]
    if held and not allow_unsafe:
        lines.append("[migrate] 停在需要人工确认的步骤前：%s（要改写你的文件结构）。"
                     "看过没问题就跑：python scripts/data_migrate.py --run --yes"
                     % "；".join(s.title for s in held))
        lines.append("[migrate] 已停在 v%d（前面的 safe 步骤也一并未执行 —— "
                     "迁移按链走，不在中间状态落戳）" % (st["found"],))
        return False, rep
    todo = plan

    if dry_run:
        lines.append("[migrate] dry-run（v%d → v%d）将依次执行：" % (st["found"], current))
        for s in todo:
            lines.append("  %s v%d→v%d%s" % ("[safe]" if s.safe else "[需确认]",
                                             s.from_v, s.to_v, s.title))
        lines.append("[migrate] 动手前会先打快照（python scripts/snapshot.py 可回退）")
        rep["schema_after"] = st["found"]
        return True, rep

    # 契约 3：先快照后动手；快照失败即中止（fail-closed，一个字节都不改）
    try:
        entry = import_guard.preimport("workspace_migrate", root=str(root))
    except import_guard.ImportGuardError as e:
        lines.append("[migrate] " + str(e))
        return False, rep
    rep["snapshot_id"] = entry.get("snapshot_id")
    rep["rollback"] = entry.get("rollback")
    lines.append("[migrate] 快照已落：%s（回退：%s）"
                 % (entry.get("snapshot_id"), entry.get("rollback")))

    v = st["found"]
    for s in todo:
        try:
            detail = s.fn(root)
        except Exception as e:                              # noqa: BLE001
            lines.append("[migrate] 停在 v%d→v%d「%s」：%s: %s"
                         % (s.from_v, s.to_v, s.title, type(e).__name__, e))
            lines.append("[migrate] 版本戳仍是 v%d（未推进），修好后重跑即可；"
                         "要整体回退：%s" % (v, rep["rollback"]))
            return False, rep
        v = s.to_v
        rep["applied"].append({"from": s.from_v, "to": s.to_v, "title": s.title})
        lines.append("[migrate] v%d→v%d %s%s" % (s.from_v, s.to_v, s.title,
                                                 ("（" + str(detail) + "）") if detail else ""))

    _write_stamp(root, current, "migrated %d step(s)" % len(todo))
    rep["schema_after"] = current
    lines.append("[migrate] 完成：数据结构 v%d → v%d（戳写在 %s）"
                 % (st["found"], current, STAMP_REL))
    return True, rep


def ensure(root, current=None, steps=None, dry_run=False):
    """启动钩子（nf_api / orchestrator 共用）。返回 `(ok, report)`。

    `ok=False` 表示**这个工作区现在不该被继续跑**（数据比代码新 / 版本链断 /
    迁移失败）—— 调用方必须据此中止启动，别把失败吞成一行日志继续写数据。
    """
    ok, rep = migrate(root, current=current, steps=steps, dry_run=dry_run)
    if not ok:
        for ln in rep["lines"]:
            print(ln)
    elif rep["action"] in ("migrate", "baseline") and rep["applied"]:
        for ln in rep["lines"]:
            print(ln)
    return ok, rep


if __name__ == "__main__":
    # 直跑 = 只读状态（真正动手请走 scripts/data_migrate.py，那里有 --run）
    ok, rep = migrate(Path.cwd(), dry_run=True)
    for ln in rep["lines"]:
        print(ln)
    sys.exit(0 if ok else 1)
