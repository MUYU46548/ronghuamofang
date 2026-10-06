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
            "book": (api.load_all()[1] or {}).get("book", {}),
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


def _gui_only(h, what):
    """设置类写入必须来自**可信的 GUI 本体**（来源头 + 主进程签发 token 双因子）。

    为什么与 agent_mode 无关也要求：这些是**成本/安全相关的设置**。
    外部 Agent 若能改预算与止烧阈值，就能抬高自己的闸门 —— 那等于没有闸门。

    为什么不能只认 `X-Mofang-Source: gui`：该头任何本机进程都可伪造（2026-10-06
    红队复现：伪造后 /project/archive/delete 返回 200 真删成功）。判据唯一实现是
    `agent_guard.is_trusted_gui`，与 `nf_api.do_POST` 的禁用名单**共用同一份**，
    否则两道闸各自漂移就重新裂出后门。

    拒文**零通道名**（V5）：不提缺哪个头、该填什么值 —— 拒文本身是攻击面。
    """
    from utils import agent_guard
    if agent_guard.is_trusted_gui(h.headers):
        return None
    return 403, {"ok": False,
                 "error": "%s 仅允许用户本人在桌面端手动修改；本次调用不是可信的 "
                          "GUI 来源。请停手并报告用户。" % what}


def handle_config_token_limit(h):
    """读 token 级熔断阈值（budget.token_limit）+ 出厂预设 + 合法区间。

    为什么要有这个读口：engine: hermes 下金额阈值恒不生效（订阅流量记账恒 0），
    止烧全靠这里的 token 上限；要**调阈值**就必须先看得见当前值与它的边界。
    """
    try:
        from utils import cost_tracker
        cfg = api.load_all()[0]
        raw = ((cfg.get("budget") or {}).get("token_limit")) or {}
        return 200, {
            "ok": True,
            "current": cost_tracker.normalize_token_limit(raw),
            "preset": dict(cost_tracker.TOKEN_LIMIT_PRESET),
            "bounds": {k: list(v) for k, v in cost_tracker.TOKEN_LIMIT_BOUNDS.items()},
            "path": "config/system.yaml",
            "field": "budget.token_limit",
            "note": ("engine: hermes 下金额阈值无效（订阅流量记账恒 0）；"
                     "止烧靠这里的 token 上限。改完对**下一次运行**生效，"
                     "正在跑的一轮不受影响。"),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_token_limit_set(h, body):
    """写 token 级熔断阈值（合法性与出厂预设见 utils/cost_tracker）。

    校验在 cost_tracker（语义唯一来源），落盘在 config_io（定向改写 + 备份 + 回读）。
    越界值一律 400 拒绝：把 max_total_tokens 改成 0 等于**让闸门静默消失**，
    与本轮修掉的「hermes 下 ¥ 记账恒 0」是同一类坑。
    """
    guard = _gui_only(h, "止烧阈值（budget.token_limit）")
    if guard:
        return guard
    try:
        from utils import cost_tracker
        from utils.config_io import set_section_scalars
        ok, msg, values = cost_tracker.validate_token_limit(body or {})
        if not ok:
            return 400, {"ok": False, "error": msg}
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("budget", "token_limit"), values, path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        return 200, {"ok": True, "current": values, "message": "已更新止烧阈值（" + w_msg + "）",
                     "note": "对下一次运行生效；正在跑的一轮不受影响。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_agent_mode(h):
    """读取当前 Agent 模式开关（config/system.yaml 的 gates.agent_mode）。"""
    try:
        cfg = api.load_all()[0]
        return 200, {"ok": True, "agent_mode": bool(cfg.get("gates", {}).get("agent_mode", False)),
                     "path": "config/system.yaml", "field": "gates.agent_mode"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_agent_mode_set(h, body):
    """设置 Agent 模式开关（config/system.yaml 的 gates.agent_mode）。

    安全守卫：Agent 模式只能从 GUI 手动开启，外部 Agent 不得调用此端点。
    检测方式：请求必须携带 X-Mofang-Source: gui 头（由 Electron preload 注入）。

    2026-10-03：落盘改走 `utils.config_io.set_section_scalar`（原先这里自带一段
    正则替换 + 手工备份 —— 同一件事第二份实现，且它不会回读校验）。
    """
    guard = _gui_only(h, "Agent 模式")
    if guard:
        return guard
    try:
        from utils.config_io import set_section_scalar
        enable = bool(body.get("agent_mode", False))
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        ok, msg = set_section_scalar("gates", "agent_mode", enable, path=sys_yaml)
        if not ok:
            return 400, {"ok": False, "error": "写入失败：" + msg}
        return 200, {"ok": True, "agent_mode": enable,
                     "message": "Agent 模式已%s（%s）" % ("开启" if enable else "关闭", msg)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 归档查看 / 删除
# 为什么在「项目」域：/project/list 已在此，归档的「看」与「删」是它的自然延伸 ——
# 此前 GUI 每行只有「恢复」，既看不了内容也删不掉，归档变成死胡同。
#
# 路径纪律：一律 api.ROOT 拼接（AGENTS.md「路径 IO 必须经 ROOT」）。
# 不用 switch_book 的模块常量：那套常量按**脚本位置**解析，--root 场景下
# 会静默指向另一个项目 —— 与 nf_api 里 Path("data/...") 是同一类坑。

_MAX_ARCHIVE_FILE = 300_000        # 单文件预览上限（300 KB，超了只报体积不给内容）
_MAX_ARCHIVE_ENTRIES = 800         # 目录树条目上限（防归档巨大时把响应撑爆）


def _query1(h, key):
    """取查询串单值（?name=xxx）。"""
    q = h._query()
    vals = q.get(key) or []
    return str(vals[0]).strip() if vals else ""


def _books_root():
    return (api.ROOT / "data" / "books").resolve()


def _archive_dir(name):
    """书名 → 归档目录。返回 (path|None, error|None)。

    安全要点：
    · sanitize 只替换 `\\/:*?"<>|`，**不拦 `..`** —— 必须显式拒绝；
    · 解析后再用 is_relative_to 卡一次，双保险（任何漏网字符都出不了根）。
    """
    import switch_book as sb
    clean = sb.sanitize(name)
    if not name or not clean or ".." in clean or clean in (".", "未命名"):
        return None, "非法书名: %r" % (name,)
    base = _books_root()
    d = (base / clean).resolve()
    if not d.is_relative_to(base):
        return None, "路径越界: %r" % (name,)
    if not d.is_dir():
        return None, "未找到归档: %s（用 GET /project/list 查看）" % clean
    return d, None


def handle_archive_tree(h):
    """归档内容清单：`GET /project/archive/tree?name=X`。

    只读。给「归档项目查看」用 —— 想删之前先看清楚里面有什么，
    也是「恢复出来才能看」这个死胡同的替代出口。
    """
    name = _query1(h, "name")
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    entries, total, truncated = [], 0, False
    try:
        for p in sorted(d.rglob("*")):
            if p.is_dir():
                continue
            if len(entries) >= _MAX_ARCHIVE_ENTRIES:
                truncated = True
                break
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            entries.append({"path": p.relative_to(d).as_posix(), "size": size})
            total += max(size, 0)
    except OSError as e:
        return 500, {"ok": False, "error": "读取归档失败: %s" % e}
    return 200, {"ok": True, "name": name, "root": str(d),
                 "entries": entries, "count": len(entries),
                 "total_size": total, "truncated": truncated}


def handle_archive_file(h):
    """归档内单文件预览：`GET /project/archive/file?name=X&path=rel`。

    只读 + 三道闸：路径不许越出归档根、超大文件只报体积、二进制不回内容
    （下载 .docx 之类对「看一眼」没有意义，还会把 JSON 响应变成乱码）。
    """
    name, rel = _query1(h, "name"), _query1(h, "path")
    if not name or not rel:
        return 400, {"ok": False, "error": "name 与 path 均必填"}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    target = (d / rel).resolve()
    if not target.is_relative_to(d):
        return 400, {"ok": False, "error": "路径越界: %r" % rel}
    if not target.is_file():
        return 404, {"ok": False, "error": "归档内无此文件: %s" % rel}
    try:
        size = target.stat().st_size
    except OSError as e:
        return 500, {"ok": False, "error": str(e)}
    if size > _MAX_ARCHIVE_FILE:
        return 413, {"ok": False, "error": "文件过大（%d B > %d B），请用「恢复」后查看"
                                            % (size, _MAX_ARCHIVE_FILE)}
    data = target.read_bytes()
    if b"\x00" in data[:4096]:
        return 415, {"ok": False, "error": "二进制文件，不支持在线预览: %s" % rel}
    return 200, {"ok": True, "name": name, "path": rel, "size": size,
                 "content": data.decode("utf-8", errors="replace")}


def handle_archive_delete(h, body):
    """删除归档：`POST /project/archive/delete` {name, confirm, purge?}。

    破坏性操作的四道闸：
    1. **GUI 来源**（`_gui_only`）：与止烧阈值同一档，与 agent_mode 无关也拦 ——
       外部 Agent 不该有能力抹掉用户的成书数据；
    2. **confirm 必须逐字等于书名**：防手滑 / 防脚本无脑重放；
    3. **默认不真删**：移入 `data/books/_trash/<书名>__<时间戳>`（列表不再显示），
       `purge=true` 才真删 —— 用户对数据丢失极度敏感，回收站是默认形态；
    4. **返回落点路径**：真删时明确告知不可恢复，回收站时告知还能捞回来。
    """
    guard = _gui_only(h, "归档删除")
    if guard:
        return guard
    body = body or {}
    name = str(body.get("name") or "").strip()
    confirm = str(body.get("confirm") or "").strip()
    purge = bool(body.get("purge"))
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    if confirm != name:
        return 400, {"ok": False, "error": "请输入归档名「%s」以确认删除" % name}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    import shutil
    try:
        if purge:
            shutil.rmtree(str(d))
            return 200, {"ok": True, "mode": "purged", "name": name,
                         "message": "已彻底删除「%s」（%s）—— 不可恢复" % (name, d)}
        ts = api.time.strftime("%Y%m%d_%H%M%S", api.time.localtime())
        trash = _books_root() / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / ("%s__%s" % (d.name, ts))
        shutil.move(str(d), str(target))
        return 200, {"ok": True, "mode": "trashed", "name": name,
                     "trash_path": str(target),
                     "message": "已移出归档列表「%s」→ 回收站 %s（要彻底清除请手工删除该目录）"
                                % (name, target)}
    except OSError as e:
        return 500, {"ok": False, "error": "删除失败: %s" % e}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/health", handle_health),
    ("GET", "/state", handle_state),
    ("GET", "/project/list", handle_project_list),
    ("GET", "/project/status", handle_project_status),
    ("GET", "/project/archive/tree", handle_archive_tree),
    ("GET", "/project/archive/file", handle_archive_file),
    ("POST", "/project/archive/delete", handle_archive_delete),
    ("GET", "/config/project", handle_config_project),
    ("GET", "/config/style_notes", handle_config_style_notes),
    ("GET", "/config/agent_mode", handle_config_agent_mode),
    ("POST", "/config/agent_mode", handle_config_agent_mode_set),
    ("GET", "/config/token_limit", handle_config_token_limit),
    ("POST", "/config/token_limit", handle_config_token_limit_set),
)
