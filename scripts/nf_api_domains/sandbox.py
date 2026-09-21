# -*- coding: utf-8 -*-
"""沙盒审核域：队列读侧在 `outline.handle_sandbox_queue`，这里放写动作与文件预览。

## 为什么写在这里而不是 `post_misc`

`post_misc` 是「零散 POST」的垃圾桶；沙盒审核是一条完整的业务线
（配合 `utils/sandbox_review` + `scripts/sandbox_review.py` CLI），
单列一个域模块，将来加「批量通过 / 导出审核报告 / 二次确认」时不用挤别人。

## 语义红线：只动状态库，绝不动文件

本模块**只改** `data/state/sandbox_manifest.json`，不写不删沙盒里的 .md，
更不碰 vault。审核是「贴标签」而非「执行粘贴」—— 真正的入库动作始终由
用户手工完成（或未来的显式导出命令）。审错了可以 `reset` 退回，无损。

## rel_path 必须相对沙盒目录

`set_status` 的 key 是相对沙盒根的路径，所以这里**不接受绝对路径**，
并显式拒绝 `..` —— 防止 `../../data/outline/global.md` 这类越界 key
把状态错记到别的文件上（`utils/sandbox_review` 自身不做校验，因为它
只当字符串用；边界防护归 HTTP 层，这是本项目一贯的分工）。
"""

def _bad_request(msg):
    """本域自用的 400 构造（与其它域一致：{error: 文案}）。"""
    return 400, {"error": msg}


# 动作 → 状态常量名的映射（GUI 只传动作，不传内部状态字符串）
ACTIONS = {
    "approve": "APPROVED",
    "reject": "REJECTED",
    "reset": "PENDING",
}


def _bad_rel(rel):
    """相对沙盒路径的合法性检查。返回错误文案或 None。"""
    if not rel:
        return "path 必填（相对沙盒目录，见 /sandbox/queue 返回的 item.path）"
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        return "path 必须是相对沙盒目录的路径，不能是绝对路径: " + rel
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts:
        return "path 不能包含 .. : " + rel
    if not rel.lower().endswith(".md"):
        return "path 必须指向 .md 产物: " + rel
    return None


def handle_sandbox_review(h, body=None):
    """POST /sandbox/review {path, action, note?} → 变更审核状态。

    `body` 由 `do_POST` 传入（它在函数开头已 `self._body()` 读过一次）。
    **绝不要在 handler 里再调 `h._body()`** —— 请求体是一次性流，
    二次读会阻塞在 `rfile.read(n)` 上直到客户端超时（表现为「点了没反应」，
    且服务端日志一片空白，极难定位）。这是本模块唯一必须记住的调用约定。

    动作三选一（`action`），与 CLI 的 --approve/--reject/--reset 一一对应：

      approve  通过 → 可以粘贴进 vault
      reject   驳回（配 `note` 记原因）
      reset    退回待审（审错了全靠它，所以必须是无损的）

    返回体带回**更新后的队列快照**（stats + 该条目的新状态），
    这样 GUI 点一下就能就地刷新计数，不用再补一次 GET。
    """
    from utils import sandbox_review as srv

    body = body if isinstance(body, dict) else {}
    rel = str(body.get("path") or "").replace("\\", "/")
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "")

    err = _bad_rel(rel)
    if err:
        return _bad_request(err)
    if action not in ACTIONS:
        return _bad_request("action 必须是 approve / reject / reset 之一，收到: "
                           + (action or "(空)"))
    if action == "reject" and not note.strip():
        # 驳回必须写原因：没有原因的驳回，三天后自己也看不懂为什么
        return _bad_request("驳回必须填 note（说明哪里不合格）")

    status = getattr(srv, ACTIONS[action])
    ok, msg = srv.set_status(rel, status, note=note)
    if not ok:
        return 404, {"ok": False, "error": msg}

    from obsidian_bridge import get_sandbox_dir
    sandbox = get_sandbox_dir()
    return 200, {
        "ok": True,
        "message": msg,
        "action": action,
        "status": status,
        "status_label": srv.STATUS_LABELS.get(status, status),
        "item": srv.load().get(rel),
        "stats": srv.stats(),
        "orphans": srv.orphans(sandbox),
    }


def handle_sandbox_file(h):
    """GET /sandbox/file?path=<相对路径> → 只读预览沙盒产物的正文。

    为什么需要它：审核队列里点「预览」必须能看到**真实内容**，否则「通过」
    就是盲签。GUI 拿不到沙盒目录的原始路径（目录可由配置改到 Obsidian 库内），
    所以读这一侧也必须走后端。

    红线：**只读**。不写不删不改，路径经 `_bad_rel` 同一套校验
    （拒绝绝对路径、拒绝 `..`、只允许 .md），读不到就如实报错。
    """
    from pathlib import Path

    from obsidian_bridge import get_sandbox_dir

    q = h._query()
    rel = str((q.get("path") or [""])[0]).replace("\\", "/")
    err = _bad_rel(rel)
    if err:
        return _bad_request(err)

    sandbox = get_sandbox_dir()
    full = Path(sandbox) / rel
    if not full.exists():
        return 404, {"error": "沙盒中不存在该产物: " + rel}
    try:
        # 二次防线：即使用户配置把沙盒指到别处，也确认解析后的路径仍在沙盒内
        if not full.resolve().is_relative_to(Path(sandbox).resolve()):
            return _bad_request("路径越出沙盒目录: " + rel)
        content = full.read_text(encoding="utf-8")
    except OSError as e:
        return 500, {"error": "读取失败: " + str(e)[:150]}
    except UnicodeDecodeError:
        return 400, {"error": "不是 UTF-8 文本，无法预览: " + rel}

    return 200, {
        "ok": True,
        "path": rel,
        "content": content,
        "chars": len(content),
        "lines": content.count("\n") + 1,
    }


ROUTES = (
    ("POST", "/sandbox/review", handle_sandbox_review),
    ("GET", "/sandbox/file", handle_sandbox_file),
)