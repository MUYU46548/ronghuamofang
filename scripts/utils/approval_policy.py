# -*- coding: utf-8 -*-
"""审批门策略（**单一来源**）。

`require_approval` 的读取原先散在四处：orchestrator 的 dry-run 预演 / 实际执行 /
启动横幅，外加 nfctl 的「待审批」统计。判据复制多份，改一处忘其余就会
「预演说会停、实跑不停」或「status 里看不到待审批的 stage1」。

`approval_stages(gates)` 是**唯一入口**。
"""


def approval_stages(gates=None):
    """有效审批门集合。

    `require_approval` 是基集；**陪跑 / 自主**（`gates.material_autonomy`）是
    **三态**，刻意让「键缺失」不改行为：

      · **键缺失** → 只按 `require_approval`（老配置、测试里的 `load_config`
        替身都不带这个键，行为与改动前逐字一致）；
      · **false（陪跑）** → 并入**阶段 1**：素材归并结束后停下，等作者看冲突清单
        并拍板（`nf_set_material_status` / `material_review.py --reject`）；
      · **true（自主）** → 不额外停。

    返回**保持 `require_approval` 原有顺序**（追加的 1 排在末尾），避免改变既有
    打印顺序。
    """
    g = gates or {}
    req = list(g.get("require_approval", [2]) or [])
    if "material_autonomy" in g and not g.get("material_autonomy") and 1 not in req:
        req.append(1)
    return req
