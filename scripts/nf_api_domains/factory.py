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
    try:                                                       # 随包位置反推
        from utils.app_paths import payload_example_dirs       # （不依赖 NF_SAMPLE_ROOT）
        cands.extend(payload_example_dirs())
    except Exception:                                          # noqa: BLE001
        pass
    for c in cands:
        try:
            if c.is_dir():
                return c, True
        except OSError:
            continue
    return None, False


def _book_title(path):
    """读 project.yaml 的 `book.name`；缺失/坏文件/无该键一律返回 None。

    只取这一个键 —— 出厂徽标只需要书名，整份解析反而把「配置坏掉」这种
    与本功能无关的状态带进响应。
    """
    try:
        from utils.config_io import load_config_yaml
        data = load_config_yaml(str(path), default=None) or {}
        title = ((data.get("book") or {}).get("name") or "").strip()
        return title or None
    except Exception:                                          # noqa: BLE001
        return None


def current_sample_view():
    """当前项目 vs 出厂样例的轻量比对（GUI「项目」页徽标用）。

    **判据 = 书名相等**（样例随包的 `examples/sample-book/project.yaml` 的
    `book.name` ↔ 当前 `config/project.yaml` 的 `book.name`），零 sha、
    零目录遍历 —— 与 `handle_factory_list` 的逐字节盘点分工：那边回答
    「工作区里哪些文件是机器发的」，这边只回答「当前这本书是不是示例书」。

    为什么不用逐字节判据：示例书进工作区后配置**本来就会变**（word_template、
    style_notes、模型选择都会被写进去），拿「配置没动过」当必要条件会让徽标
    在最该出现的场景里消失。书名相同 + 素材来自 examples/ 才是用户的实际
    认知（「这就是那个出厂测试项目」），而**改过书名的项目永远不会命中**
    —— 徽标误报的方向是「多提醒一次」，不是「漏掉用户数据」。
    """
    sample_root, exists = resolve_sample_root()
    out = {"title": None, "sample_title": None, "is_sample": False}
    try:
        out["title"] = _book_title(api.ROOT / "config" / "project.yaml")
    except OSError:
        out["title"] = None
    if exists and sample_root:
        # `resolve_sample_root()` 回的是**样例根目录**（`examples/`），
        # 样例书住在它下面的 `sample-book/`；源码态若直接把项目当样例根，
        # project.yaml 就在根上。两个候选都试，谁在算谁。
        for cand in (Path(sample_root) / "sample-book" / "project.yaml",
                     Path(sample_root) / "project.yaml"):
            if cand.is_file():
                out["sample_title"] = _book_title(cand)
                break
    out["is_sample"] = bool(out["title"] and out["title"] == out["sample_title"])
    return out


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
            # 当前项目是否就是出厂示例书（项目页徽标）；轻量，零 sha。
            "current": current_sample_view(),
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


def handle_factory_project(h):
    """`GET /factory/project` —— 当前工作区是否出厂测试项目 + 还原预览。

    **零副作用**（只读）。回答三件事：能不能还原（`is_factory` + `reason`）、
    源还在不在（`source_ok`）、还原会动什么（`wipe_plan`）。GUI 据此决定
    是否显示「一键还原」按钮与确认框内容；外部 Agent 可盘点，不能动手。
    """
    try:
        import utils.factory_reset as fr
        return 200, {"ok": True, **fr.describe(api.ROOT)}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_factory_project_reset(h, body):
    """`POST /factory/project/reset {confirm: true}` —— 出厂测试项目一键还原。

    三重护栏（与铺设端点同族，列入 FORBIDDEN_IN_AGENT_MODE + 端点双因子）：
    ① GUI 来源头 —— 复用 `project._gui_only`（唯一实现 `agent_guard.
    is_trusted_gui`，与 do_POST 禁用名单同一份，伪造头不算）；② `confirm: true`；
    ③ `factory_reset.reset()` 自身的标记/书名/出厂源三重自检 —— 任一不过
    **一个字节都不动**。真书没有标记，天然不可被这条路碰到。
    """
    body = body if isinstance(body, dict) else {}
    from nf_api_domains.project import _gui_only
    guard = _gui_only(h, "还原出厂测试项目")
    if guard is not None:
        return guard
    if body.get("confirm") is not True:
        return 400, {"ok": False,
                     "error": "还原出厂测试项目是破坏性操作，必须二次确认"
                              "（请求体需 confirm: true）"}
    try:
        import utils.factory_reset as fr
        ok, report = fr.reset(api.ROOT)
        status = 200 if ok else 400
        return status, {"ok": ok, **report}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/factory/list", handle_factory_list),
    ("POST", "/factory/clean", handle_factory_clean),
    ("GET", "/factory/project", handle_factory_project),
    ("POST", "/factory/project/reset", handle_factory_project_reset),
)
