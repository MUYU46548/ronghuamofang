# -*- coding: utf-8 -*-
"""出厂内容域：`GET /factory/list`（只读盘点）+ `POST /factory/clean`（一键清理）。

## 这条业务线在解决什么

`examples/sample-book/`（雾港核心 + 19 张素材卡）曾被试跑**手工搬进真实用户
工作区**的 `materials/raw/`，与用户自己写的素材卡**逐文件无差别**：用户既不敢删
（怕删掉自己的），也不敢留（污染 stage1 归并）。本域把「出厂内容」摊出来给用户看：

  · `GET /factory/list` —— **零副作用**：出厂清单（哪些文件是播种进来的）+
    与样例逐字节一致的素材卡命中列表 + 样例随包目录在不在。
  · `POST /factory/clean` —— 命中的文件移进 `data/books/_trash/factory__<时间戳>/`
    （保留原相对路径，可人工捞回），**不删、不动 progress.json、不动用户改过的文件**。

## 为什么 sample_root 有多个候选

样例随包（`console/package.json` 的 `extraResources` → `payload/examples`）与
nf_api 的 ROOT **不是同一个坐标系**：

  · 源码态：ROOT = 仓库根 → `ROOT/examples`；
  · 打包态：Electron 用 `--root <workspace>` 起 nf_api，ROOT = 工作区
    （工作区里**没有** examples），而样例住在安装目录 `resources/payload/examples`。

所以解析顺序是：**环境变量 `NF_SAMPLE_ROOT`**（打包态由主进程注入，最准）
→ `ROOT/payload/examples` → `ROOT/examples` → 代码根兜底（`scripts/` 的上一级）。
按存在性取第一个命中的；全都不在 → `sample_root: null`，
`GET /factory/list` 仍 200（`matches` 为空 + `sample_root_exists: false`），
**绝不因此 500** —— 「样例没随包」是可诊断状态，不是服务故障。

## 两条纪律

1. 只 `return (status, payload)`，响应统一由 `nf_api._send` 发出（三纪律之一）。
2. 路径一律 `api.ROOT / ...`（三纪律之二）：`--root` 场景下相对路径会静默读错项目。
"""
import os
from pathlib import Path

import nf_api as api
from utils import factory_manifest as fm

# GET /factory/list 的响应里最多回多少条命中（GUI 只展示前 20 条，全量在此截断）
_MAX_MATCHES = 200


def resolve_sample_root():
    """样例随包目录：环境变量 → 打包态候选 → 源码态候选 → 代码根兜底。

    返回 `(Path|None, 存在与否)`。全都不在时返回 `(None, False)`
    —— 由调用方如实回报，不当错误处理。
    """
    cands = []
    env = os.environ.get("NF_SAMPLE_ROOT")
    if env:
        cands.append(Path(env))
    cands.append(api.ROOT / "payload" / "examples")            # 打包态（ROOT 指安装目录）
    cands.append(api.ROOT / "examples")                        # 源码态（ROOT = 仓库根）
    try:
        cands.append(Path(api.__file__).resolve().parents[1] / "examples")   # 代码根兜底
    except Exception:                                          # noqa: BLE001
        pass
    for c in cands:
        try:
            if c.is_dir():
                return c, True
        except OSError:
            continue
    return None, False


def _manifest_view(workspace):
    """清单 → 响应用的精简视图；没有清单返回 None（GUI 显示「未播种」）。"""
    data = fm.read_manifest(workspace)
    if not data or not data.get("files"):
        return None
    return {"seed_version": data.get("seed_version"),
            "count": len(data.get("files") or {}),
            "generated_at": data.get("generated_at")}


def handle_factory_list(h):
    """`GET /factory/list` —— 出厂清单 + 样例命中盘点。**零副作用**（只读）。"""
    try:
        root, exists = resolve_sample_root()
        matches = fm.detect_sample_matches(api.ROOT, root) if exists else []
        return 200, {
            "ok": True,
            "manifest": _manifest_view(api.ROOT),
            "matches": matches[:_MAX_MATCHES],
            "match_count": len(matches),
            "sample_root": str(root) if root else None,
            "sample_root_exists": exists,
        }
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_factory_clean(h, body):
    """`POST /factory/clean {confirm: true}` —— 把命中样例的素材卡移进回收站。

    `body` 由 `do_POST` 传入（**不要**再调 `h._body()`，请求体是一次性流）。

    护栏顺序：confirm → 重新检测（**不信任**客户端传来的命中列表，
    否则伪造一个路径就能搬动用户文件）→ 硬边界（只准 `materials/`）→ 移动。
    """
    body = body if isinstance(body, dict) else {}
    if not body.get("confirm"):
        return 400, {"ok": False,
                     "error": "清理出厂样例是破坏性操作，必须显式确认："
                              "请在应用内确认框勾选「我已了解：仅移动与示例逐字节一致的文件」"
                              "后重试（请求体需 confirm: true）。"}
    try:
        root, exists = resolve_sample_root()
        matches = fm.detect_sample_matches(api.ROOT, root) if exists else []
        if not matches:
            return 200, {"ok": True, "moved": [], "trash_dir": None, "count": 0,
                         "message": "未检出与出厂示例一致的文件，无需清理"}
        res = fm.clean_factory(api.ROOT, matches)
        moved = res.get("moved") or []
        return 200, {"ok": True, "moved": moved, "trash_dir": res.get("trash_dir"),
                     "count": len(moved),
                     "message": "已归档 %d 个出厂样例文件到 %s（原相对路径保留，可手工捞回）"
                                % (len(moved), res.get("trash_dir"))}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/factory/list", handle_factory_list),
    ("POST", "/factory/clean", handle_factory_clean),
)
