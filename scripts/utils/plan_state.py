# -*- coding: utf-8 -*-
"""规划四态状态机（丙0）—— AI 提案必须经用户确认才能生效。

## 要解决的问题

此前「规划/想法/提议」全仓零命中。大纲修订 history、outline_advisor 建议、
沙盒产物……都是「AI 产出 → 直接落产物」，没有「AI 提案 → 用户选中 → 才生效」
的中间态。后果：AI 可以直接往产出里塞东西（10-11 十连炸的根因之一）。

## 四态定义

    idea（默认） → selected（用户选中） → written（已写进正文）
                                       ↘ dropped（已丢弃）

    idea → dropped：用户直接丢弃
    selected → idea：用户反悔，退回重审
    written 是终态（不能再改）
    dropped 可以复活回 idea（防止误丢）

## 三条铁律（对应任务书）

1. **AI 不自标 selected**：`propose()` 只产出 idea；`selected()` 是用户专属动作
   （未来集成到 GUI「规划」页签时，只有 GUI 来源可调用；CLI 工具受
   `gates.agent_mode` 守卫 —— agent_mode=true 时 non-GUI 来源调 `/plan/select`
   返回 403）。
2. **written 挂真实正文引用硬校验**：状态推进到 written 时，必须提供真实文件
   路径且文件存在 + 非空；否则拒绝推进并返回 blocker。
3. **状态转换可审计**：每次转换都记录到 `data/state/plan_audit.jsonl`
   （时间戳 + 转换前后状态 + 操作方 + 引用路径 + 原因）。

## 为什么不复用 sandbox_review.py

沙盒审核状态机是产物级（按文件路径索引）、面向「外部内容入库」。
规划状态机是想法级（按 idea_id 索引）、面向「AI 提案到内部产出」。
两者职责不同，故独立实现，不耦合。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from utils.file_io import read_text, write_text

# ---------------------------------------------------------------- 路径 / 常量

PLAN_STATE = "data/state/plan_state.json"
PLAN_AUDIT = "data/state/plan_audit.jsonl"

STATUS_IDEA = "idea"
STATUS_SELECTED = "selected"
STATUS_WRITTEN = "written"
STATUS_DROPPED = "dropped"

VALID_STATUSES = (STATUS_IDEA, STATUS_SELECTED, STATUS_WRITTEN, STATUS_DROPPED)

# 合法转换表：当前状态 → {可转状态: 是否需要引用文件}
VALID_TRANSITIONS = {
    STATUS_IDEA: {
        STATUS_SELECTED: False,   # 用户选中 → 无需文件引用
        STATUS_DROPPED: False,    # 丢弃 → 无需文件引用
    },
    STATUS_SELECTED: {
        STATUS_IDEA: False,       # 反悔退回
        STATUS_WRITTEN: True,     # 写进正文 → 必须提供真实引用
        STATUS_DROPPED: False,
    },
    STATUS_DROPPED: {
        STATUS_IDEA: False,       # 误丢复活
    },
    STATUS_WRITTEN: {},          # 终态
}

LABELS = {
    STATUS_IDEA: "提议",
    STATUS_SELECTED: "已选中",
    STATUS_WRITTEN: "已写进正文",
    STATUS_DROPPED: "已丢弃",
}


# ---------------------------------------------------------------- 状态库读写


def _state_path(root=None):
    return Path(root or PLAN_STATE)


def _audit_path(root=None):
    return Path(root or PLAN_AUDIT)


def load_state(state_path=None):
    """读状态库。损坏/缺失 → 空 dict（不抛）。"""
    p = Path(state_path or PLAN_STATE)
    if not p.exists():
        return {}
    try:
        data = json.loads(read_text(p))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_state(data, state_path=None):
    """写状态库。空数据拒绝写入（防覆盖丢失）。"""
    if not data:
        return False
    p = Path(state_path or PLAN_STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(data, ensure_ascii=False, indent=1))
    return True


def _append_audit(record, audit_path=None):
    """追加一条审计日志（行式 JSON）。"""
    p = Path(audit_path or PLAN_AUDIT)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------- 引用文件硬校验


def _validate_reference(ref_path):
    """written 态引用校验。返回 (ok, reason)。"""
    if not ref_path:
        return False, "written 态必须提供引用文件路径（空路径被拒）"
    p = Path(ref_path)
    if not p.exists():
        return False, f"引用路径不存在: {ref_path}"
    if not p.is_file():
        return False, f"引用路径不是文件: {ref_path}"
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"无法读取引用文件: {e}"
    if size == 0:
        return False, f"引用文件为空（零字节）: {ref_path}"
    return True, ""


# ---------------------------------------------------------------- 核心 API


def _gen_idea_id(title, kind="proposal"):
    """基于标题+类型+时间戳+随机量生成 ID（防重）。

    随机量是必需的（2026-10-16）：Windows 上 `datetime.now()` 时钟粒度粗，
    同一 tick 内两次 propose 同名提案会拿到相同 seed → 相同 id → 状态库按
    id 索引，后一条**静默覆盖**前一条（提案丢失且无报错）。单测 1.6 在高
    负载下三跑一挂即此；加 uuid4 后防重不再依赖时钟步进。
    """
    seed = f"{kind}:{title}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}:{uuid4().hex}"
    return f"{kind}_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def propose(title, summary, source="", kind="proposal", context=None,
            state_path=None, audit_path=None):
    """AI 提案 → idea 态（默认）。返回 (idea_id, entry)。

    `source` 标注来源（outline_advisor / sandbox_review / agent / user）。
    `context` 可选：附加元信息（如 advisor 建议编号、沙盒产物路径等）。
    """
    if not title or not title.strip():
        raise ValueError("title 不能为空（AI 提案必须有标题）")
    state = load_state(state_path)
    idea_id = _gen_idea_id(title.strip(), kind)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "id": idea_id,
        "title": title.strip(),
        "summary": summary or "",
        "status": STATUS_IDEA,
        "kind": kind,
        "source": source,
        "ref_path": "",
        "context": context or {},
        "created_at": now,
        "updated_at": now,
        "transition_history": [{"at": now, "from": "", "to": STATUS_IDEA, "by": source}],
    }
    state[idea_id] = entry
    save_state(state, state_path)
    _append_audit({
        "at": now,
        "idea_id": idea_id,
        "action": "propose",
        "from": "",
        "to": STATUS_IDEA,
        "by": source,
        "ref_path": "",
    }, audit_path or (state_path and str(Path(state_path).with_name(Path(PLAN_AUDIT).name))))
    return idea_id, entry


def transition(idea_id, new_status, operator="user", ref_path="",
               reason="", state_path=None, audit_path=None):
    """执行状态转换。返回 (ok, msg, entry)。

    校验链：
    1. idea_id 存在
    2. 当前状态 → new_status 在合法转换表内
    3. 若该转换需要引用文件 → 文件存在且非空（硬校验）
    """
    state = load_state(state_path)
    entry = state.get(idea_id)
    if entry is None:
        return False, f"idea_id 不存在: {idea_id}", None

    current = entry.get("status", STATUS_IDEA)
    allowed = VALID_TRANSITIONS.get(current, {})

    if new_status not in allowed:
        return (
            False,
            f"非法转换: {current} → {new_status}（允许: {list(allowed) or '无，终态'}）",
            entry,
        )

    # 硬校验
    if allowed.get(new_status, False):
        ok, err = _validate_reference(ref_path)
        if not ok:
            return False, f"written 引用校验失败: {err}", entry

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry["status"] = new_status
    entry["updated_at"] = now
    if ref_path:
        entry["ref_path"] = str(ref_path)
    if reason:
        entry["note"] = reason
    entry.setdefault("transition_history", []).append({
        "at": now,
        "from": current,
        "to": new_status,
        "by": operator,
        "ref_path": ref_path,
        "reason": reason,
    })

    save_state(state, state_path)
    _append_audit({
        "at": now,
        "idea_id": idea_id,
        "action": "transition",
        "from": current,
        "to": new_status,
        "by": operator,
        "ref_path": ref_path,
        "reason": reason,
    }, audit_path or (state_path and str(Path(state_path).with_name(Path(PLAN_AUDIT).name))))
    return True, f"{idea_id}: {current} → {new_status}", entry


# 用户专属快捷操作（语义糖）


def select(idea_id, operator="user", reason="", state_path=None, audit_path=None):
    """用户选中（idea → selected）。AI 禁止调用此函数。"""
    return transition(idea_id, STATUS_SELECTED, operator=operator,
                      reason=reason, state_path=state_path, audit_path=audit_path)


def write(idea_id, ref_path, operator="user", reason="", state_path=None, audit_path=None):
    """写进正文（selected → written）。硬校验引用路径。"""
    return transition(idea_id, STATUS_WRITTEN, operator=operator,
                      ref_path=ref_path, reason=reason,
                      state_path=state_path, audit_path=audit_path)


def drop(idea_id, operator="user", reason="", state_path=None, audit_path=None):
    """丢弃（idea/selected → dropped）。"""
    return transition(idea_id, STATUS_DROPPED, operator=operator,
                      reason=reason, state_path=state_path, audit_path=audit_path)


def revive(idea_id, operator="user", reason="", state_path=None, audit_path=None):
    """误丢复活（dropped → idea）。仅对 dropped 态生效。"""
    state = load_state(state_path)
    entry = state.get(idea_id)
    if entry and entry.get("status") != STATUS_DROPPED:
        return False, f"revive 仅对 dropped 生效，当前 {entry.get('status')}", entry
    return transition(idea_id, STATUS_IDEA, operator=operator,
                      reason=reason, state_path=state_path, audit_path=audit_path)


# ---------------------------------------------------------------- 查询


def list_items(status=None, state_path=None):
    """按状态筛选 / 全部列出（按 updated_at 倒序）。"""
    state = load_state(state_path)
    items = list(state.values())
    if status:
        items = [e for e in items if e.get("status") == status]
    return sorted(items, key=lambda e: e.get("updated_at") or "", reverse=True)


def get(idea_id, state_path=None):
    """单条查询。"""
    return load_state(state_path).get(idea_id)


def stats(state_path=None):
    """各状态计数。"""
    out = {s: 0 for s in VALID_STATUSES}
    for e in load_state(state_path).values():
        s = e.get("status")
        if s in out:
            out[s] += 1
    return out
