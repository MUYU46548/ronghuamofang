# -*- coding: utf-8 -*-
"""项目域（GET）：健康检查、状态快照、多书列表、项目结构树、配置读取。

## 为什么 /health 与 /state 放在「项目」域

它们回答的是同一个问题——「当前项目处于什么状态、能不能开工」。
GUI 启动时的冷启动引导、书本切换、初始化向导都读这一族。

## 关于 /project/status 里的 `stages`

`stages` 来自 `api.build_state()`，而其它字段是**文件系统探测**（路径 exists +
glob 计数）。两者刻意不合并：`stages` 是「流程走到哪」，exists 是「盘上有什么」。
实践中二者可能不一致（比如用户手删了产物但没重置状态），这是**有信息量的不一致**，
前端据此可以提示「状态与实际产物不符，建议打回重跑」。

## /config/project 的历史缺陷

该端点曾经**只有 POST 分支、没有 GET 分支**，而 GUI 用的是 GET → 一律 404
→ 初始化向导与「素材为空」提醒永远不触发。读操作必须挂在 do_GET 上。
"""
import nf_api as api


def handle_health(h):
    """存活探测 + 当前后台任务（GUI 用它判断要不要轮询）。"""
    cur = api.CURRENT["id"]
    job = api.JOBS.get(cur) if cur else None
    return 200, {"ok": True, "current_job": cur,
                 "current_kind": (job or {}).get("kind"),
                 "allow_fake": api.ALLOW_FAKE}


def handle_state(h):
    """全站状态快照（阶段进度 / 审批位 / 预算 / 质量门），GUI 主轮询入口。"""
    try:
        return 200, api.build_state()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_project_list(h):
    """多书列表（data/books/ 归档 + 当前书）。"""
    try:
        return 200, api.sb_mod.list_books()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_project_status(h):
    """项目结构树（左侧导航用）：状态 + 各产物是否存在。

    只做「存在性 + 计数」，不做内容解析——导航栏刷新频率高，
    解析内容是各页面自己的事。
    """
    try:
        project_status = {
            "book": api.load_all()[1].get("book", {}),
            "stages": api.build_state().get("stages", []),
            "setting_exists": (api.ROOT / "data" / "setting" / "setting.json").exists(),
            "outline_exists": (api.ROOT / "data" / "outline" / "global.md").exists(),
            "chapters_count": len(list((api.ROOT / "data" / "outline" / "chapters").glob("*.md")))
            if (api.ROOT / "data" / "outline" / "chapters").exists() else 0,
            "drafts_exist": (api.ROOT / "data" / "chapters" / "raw").exists()
            and any((api.ROOT / "data" / "chapters" / "raw").glob("*.md")),
            "refined_exist": (api.ROOT / "data" / "chapters" / "refined").exists()
            and any((api.ROOT / "data" / "chapters" / "refined").glob("*.md")),
            "word_exists": bool(list((api.ROOT / "output").glob("*.docx"))),
            "materials_count": len(list((api.ROOT / "materials" / "raw").glob("*")))
            if (api.ROOT / "materials" / "raw").exists() else 0,
        }
        return 200, project_status
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_project(h):
    """读取 config/project.yaml 的归一化视图（向导 / 空素材提醒靠它）。"""
    try:
        from utils.project_config import get_project_config
        return 200, {"ok": True, "config": get_project_config()}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_style_notes(h):
    """用户风格笔记（config/project.yaml 的 book.style_notes）。

    单独开一个端点而不是塞进 /config/project：风格笔记可能很长，
    且用户在写作页签里改得频繁，没必要为它重读整个 yaml。
    """
    try:
        return 200, {"ok": True, "content": api.pc_get_style_notes(),
                     "path": "config/project.yaml", "field": "book.style_notes"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/health", handle_health),
    ("GET", "/state", handle_state),
    ("GET", "/project/list", handle_project_list),
    ("GET", "/project/status", handle_project_status),
    ("GET", "/config/project", handle_config_project),
    ("GET", "/config/style_notes", handle_config_style_notes),
)
