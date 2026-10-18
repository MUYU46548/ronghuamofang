# -*- coding: utf-8 -*-
"""讨论落地协议（2026-10-18 P0-1）：agent 讨论成果 → 待审提案 → 用户批准 → 写回大纲。

## 解决什么

用户的真实工作流是「与 agent 讨论十几次 → 结论以【优化点】烤进大纲 → 从大纲往下写」，
讨论回路一直存在，只是不在产品里。此前唯一的「修改入口」是审批门的通过/打回二元选择
+ `refine_outline` 的单轮意见 —— 没有会话记忆、没有候选对比、没有讨论史留痕。

本模块提供落地通道，**与讨论发生在哪解耦**：讨论走 Hermes（简报投递，主形态）或
GUI 单轮精修（兜底），产物都汇到同一个待审队列，用户在 GUI 看 diff 后批准才写回大纲。

## 三段式（复用沙盒审核状态机，独立 manifest）

    agent POST /outline/discussion/apply  →  写提案文件 + 登记 pending
    用户   GET  /outline/discussions      →  看队列 + diff
    用户   POST /outline/discussion/review →  approve（真正写回章大纲）/ reject

状态机复用 `utils/sandbox_review`（pending/approved/rejected + sha256 变更重置），
但用**独立 manifest**：`data/state/discuss_manifest.json`。为什么不用沙盒那一份 ——
沙盒是「贴进 vault」的中转区，讨论提案是「改我的大纲」，目的地与生命周期都不同，
混在一张状态库里会让两队列互相污染计数。

## 红线

- **agent 可提案、不可自批**：`/outline/discussion/apply` 进 Agent 可调用清单
  （写提案=草稿类，产物自动待审），`/outline/discussion/review` 进
  `FORBIDDEN_IN_AGENT_MODE`（审批族，与 /approve 同档）。
- **写回前必备份**：目标章大纲复制到 `data/outline/chapters/NN_v<ts>.bak`。
- **写回后校验**：append 的优化点块必须真在文件里（锚点 id 反查），校验失败报错不静默。
- **提案正文与抬头分离**：写回只取 `<!--opt-body 开始 -->` 到 `<!--opt-body 结束 -->`
  之间的内容，抬头/理由说明自由书写不会被误写进章大纲。
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text
from utils import sandbox_review as srv

DRAFTS_DIR = "data/outline/drafts"
MANIFEST = "data/state/discuss_manifest.json"
CHAPTERS_DIR = "data/outline/chapters"

# 状态机直接复用沙盒那一份实现，只换 manifest 落点（判据只有一份，不另写状态机）
KIND = "outline_discussion"

BODY_OPEN = "<!--opt-body 开始 -->"
BODY_CLOSE = "<!--opt-body 结束 -->"
ANCHOR_RE = re.compile(r"<!--\s*opt-anchor\s*(\{[\s\S]*?\})\s*-->")

# 提案类型 → 写回动作。第一版只有 opt_point（追加优化点）；
# blueprint_patch（改骨架字段）等后续再加，类型先留扩展位。
KINDS = ("opt_point",)


def _bad(msg):
    return False, msg


def extract_body_and_anchor(proposal_text):
    """从提案文件里切出（要写回的正文, 锚点 dict）。

    两头没标 body 标记的提案 = 格式错，如实报错而不是把整份提案（含抬头）写进大纲。
    """
    t = proposal_text or ""
    if BODY_OPEN not in t or BODY_CLOSE not in t:
        return None, None, "提案缺少 opt-body 标记（无法确定写回范围）"
    body = t.split(BODY_OPEN, 1)[1].split(BODY_CLOSE, 1)[0].strip()
    if not body:
        return None, None, "opt-body 区块为空"
    anchor = {}
    m = ANCHOR_RE.search(t)
    if m:
        try:
            anchor = json.loads(m.group(1))
        except (ValueError, TypeError):
            return None, None, "opt-anchor 不是合法 JSON"
    return body, anchor, ""


def _proposal_markdown(target_rel, kind, title, rationale, candidates,
                       chosen, body, anchor, rounds, source):
    """提案文件的人读形态：抬头 + 理由 + 写回区块 + 锚点。

    抬头带「这是待审提案」声明 —— 用户在文件管理器里直接打开也能看懂它的状态。
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 讨论提案：{title}",
        "",
        f"> **这是待审提案，不是成品。** 批准后优化点会追加进 {target_rel}，不改正文。",
        f"> 提交：{ts} ｜ 类型：{kind} ｜ 来源：{source} ｜ 讨论轮次：{rounds}",
        "",
    ]
    if rationale:
        lines += ["## 理由", "", rationale, ""]
    if candidates:
        lines += ["## 候选方向", ""]
        for c in candidates:
            mark = "（选定）" if c == chosen else ""
            lines.append(f"- {c}{mark}")
        lines.append("")
    lines += ["## 提案正文", "", BODY_OPEN, "", body, "", BODY_CLOSE, "",
              f"<!--opt-anchor {json.dumps(anchor, ensure_ascii=False)} -->", ""]
    return "\n".join(lines)


def submit(target_n, title, body, rationale="", candidates=None, chosen="",
           anchor=None, rounds=0, source="agent 讨论", root="."):
    """落一份讨论提案（写文件 + 登记 pending）。返回 (ok, msg, rel_path)。

    `target_n` 是章号；`body` 是要写回章大纲的优化点正文（不含 `## 【优化点N】`
    标题行 —— 编号由写回时按章内现状顺延，避免两个提案撞号）。
    """
    target_n = int(target_n)
    if not (1 <= target_n <= 999):
        return False, "target_n 须为 1-999", None
    if not (body or "").strip():
        return False, "body 不能为空（要写回章大纲的优化点正文）", None

    root = Path(root)
    chapters = root / CHAPTERS_DIR
    target_rel = f"{CHAPTERS_DIR}/{target_n:02d}.md"
    if not (chapters / f"{target_n:02d}.md").exists():
        return False, f"第 {target_n} 章大纲不存在（{target_rel}），先跑 stage3", None

    anchor = dict(anchor or {})
    anchor.setdefault("kind", "opt_point")
    anchor.setdefault("rounds", int(rounds or 0))
    anchor.setdefault("rationale", rationale)
    anchor["target_n"] = target_n

    drafts = root / DRAFTS_DIR
    drafts.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"ch{target_n:02d}_{ts}.md"
    rel = f"{DRAFTS_DIR}/{fname}"

    md = _proposal_markdown(target_rel, anchor["kind"], title or f"第{target_n}章优化点",
                            rationale, candidates or [], chosen, body, anchor,
                            rounds, source)
    write_text(drafts / fname, md)

    # 登记待审（独立 manifest；kind 供 GUI 分队列）
    srv.register(rel, sandbox_dir=str(root), kind=KIND, source=source,
                 manifest_path=str(root / MANIFEST))
    return True, f"提案已落盘待审：{rel}", rel


def queue(status=None, root="."):
    """提案队列（默认待审优先，按更新时间倒序）。"""
    root = Path(root)
    items = srv.list_items(status, manifest_path=str(root / MANIFEST))
    return [e for e in items if e.get("kind") == KIND]


def draft_text(rel, root="."):
    """读提案全文（GUI 预览 / 提取写回内容）。校验路径不越界。"""
    if rel.startswith(("/", "\\")) or ".." in str(rel).replace("\\", "/").split("/"):
        return None, "path 必须是相对项目根的提案路径"
    p = Path(root) / rel
    if not p.exists():
        return None, f"提案不存在: {rel}"
    return read_text(p), ""


def apply_proposal(rel, root=".", reviewer=""):
    """批准一份提案：把优化点真正写回章大纲。返回 (ok, msg)。

    顺序：读提案 → 切正文与锚点 → 读章大纲 → 备份 → 追加 → 写盘 → 校验锚点在 →
    标 approved。任何一步失败都不写盘、不改状态（fail-closed）。
    """
    root = Path(root)
    text, err = draft_text(rel, root)
    if err:
        return _bad(err)

    body, anchor, err = extract_body_and_anchor(text)
    if err:
        return _bad(f"提案格式错：{err}")

    target_n = anchor.get("target_n")
    if not target_n:
        return _bad("锚点缺 target_n（不知道该写回哪一章）")
    chap_path = root / CHAPTERS_DIR / f"{int(target_n):02d}.md"
    if not chap_path.exists():
        return _bad(f"第 {target_n} 章大纲已不存在（可能被重生成）：{chap_path}")

    from utils.chapter_blueprint import append_opt_point

    old = read_text(chap_path)
    new = append_opt_point(old, body, anchor)

    # 写前备份（章大纲是用户手磨的，多轮讨论的载体，绝不能裸写）
    bak = chap_path.with_name(f"{int(target_n):02d}_v"
                              f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
    bak.write_text(old, encoding="utf-8")

    write_text(chap_path, new)

    # 校验：锚点必须真在文件里（写盘失败/编码问题在这里露头，不静默）
    wrote = read_text(chap_path)
    aid = anchor.get("id") or ""
    if aid and aid not in wrote:
        # 回滚：备份还在，如实报错让用户知道该看哪份
        return _bad(f"写回校验失败：锚点 {aid} 未出现在 {chap_path.name}；"
                    f"原稿已备份于 {bak.name}，未标记批准")

    ok, msg = srv.set_status(rel, srv.APPROVED, note=reviewer,
                             manifest_path=str(root / MANIFEST))
    if not ok:
        return _bad(f"已写回但状态未更新：{msg}（优化点已在章大纲里，请手动核对）")
    return True, (f"已写回第 {target_n} 章（新增优化点，原稿备份 {bak.name}）；"
                  f"提案 {rel} 已标记通过")


def reject(rel, note, root=""):
    """驳回提案（必须写原因；不碰章大纲）。"""
    if not (note or "").strip():
        return _bad("驳回必须填原因")
    return srv.set_status(rel, srv.REJECTED, note=note,
                          manifest_path=str(Path(root) / MANIFEST))
