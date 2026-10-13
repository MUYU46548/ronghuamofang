# -*- coding: utf-8 -*-
"""stage2 大纲**分段审批**判据（丙4 · 催更姬 #3 附注）。

## 为什么

整体大纲动辄几万字，「一次读完全本才放行 stage2」的痛点是**阅读负担**，不是审批
强度。丙4 把它拆成可断点续审的若干段，**但不降低强度**：放行条件是**全部分段通过**
—— 不是「读一段放一段」，也不是「任一段过了就走」。一次读 5 万字的义务被拆成
八次读 6 千字，放行门槛一分没松。

实码坐实现状（2026-10-11 哨兵核）：全仓只有整本审批，无任何分段机制。

## 分段粒度（2026-10-13 用户拍板：按幕）

用户原话「我实际写小说从未写第XX幕」—— 所以实现按 `global.md` 的 `## ` 二级标题
切，不硬造幕。而 `prompts/stage2_global_outline.md` 的输出格式是**固定**的：

    # 《书名》整体大纲
    ## 起 / 承 / 转 / 合 / 关键节点 / 预计章节数 / 章节规划 / 要求

即天然退化成「按大节分段」，正是用户实际的结构划分。没有 `##` 时退到 `# `；
两级都没有可切标题 → 单段 → **闸不拦**（见契约 2）。

## 四条契约

1. **旧审核不为新内容背书**：状态存 `data/state/outline_segment_approval.json` 并记
   `doc_sha256`；`global.md` 一改，全部分段重置 `pending`。与沙盒审核
   「内容变了（sha 比对）自动重置」是同一条原则，不在这里破例。
2. **只在真能分段时启用**：切出 <2 段时闸直接放行 —— 此时等同整本审批，语义与
   丙4 之前**完全一致**，老流程、测试替身、没有大纲的阶段零破坏。
3. **判据只有一份**：`gate_status()` 是唯一放行判据。CLI（approve.py）与 HTTP
   （nf_api.act_approve）都调它 —— 两处各写一遍必然漂移，这是本仓的老规矩。
4. **开关 `gates.segment_approval`**：**键缺失 = 保持改动前行为**（不拦），
   与 `material_autonomy` 同一约定 —— 老配置与测试替身不受影响；本仓
   `config/system.yaml` 已显式写下 `true`。想「一次读完」就把它改 false，
   不用改代码、不用绕过谁。

用法（CLI）：`python scripts/segment_cli.py list / approve <id> / revoke <id> / status`
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_io import read_text, write_text  # noqa: E402

STATE_REL = "data/state/outline_segment_approval.json"
DOC_REL = "data/outline/global.md"

_H2 = re.compile(r"^##[ \t]+(.+?)\s*$", re.M)
_H1 = re.compile(r"^#[ \t]+(.+?)\s*$", re.M)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def split_segments(text):
    """按 `## ` 切段；不足 2 段退到 `# `；仍不足 → 单段（整篇）。

    首段起点取 0（把 H1 大标题与标题前的引子并进第一段）—— 否则卷首会变成
    「没有任何分段认领」的内容，审着审着就漏了。
    """
    if not text or not text.strip():
        return []
    ms = list(_H2.finditer(text))
    if len(ms) < 2:
        ms = list(_H1.finditer(text))
    if len(ms) < 2:
        return [{"id": "s01", "title": "（全文）", "start": 0, "end": len(text),
                 "chars": len(text)}]
    segs = []
    for i, m in enumerate(ms):
        start = m.start() if i else 0
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        segs.append({"id": "s%02d" % (i + 1), "title": m.group(1)[:60],
                     "start": start, "end": end, "chars": len(text[start:end])})
    return segs


# ---------------------------------------------------------------- 状态存取
def _state_path(root):
    return Path(root) / STATE_REL


def doc_path(root):
    return Path(root) / DOC_REL


def load_state(root):
    p = _state_path(root)
    if not p.exists():
        return {}
    try:
        data = json.loads(read_text(p))
    except Exception:                                       # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(root, state):
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(state, ensure_ascii=False, indent=1))


def _blank(segs, prev=None):
    """按当前切分造分段列表；`prev` 里同 id 同标题且已通过的**保留**。"""
    old = {x.get("id"): x for x in (prev or [])}
    out = []
    for s in segs:
        p = old.get(s["id"])
        if p and p.get("title") == s["title"] and p.get("status") == "approved":
            out.append({"id": s["id"], "title": s["title"], "chars": s["chars"],
                        "status": "approved", "approved_at": p.get("approved_at")})
        else:
            out.append({"id": s["id"], "title": s["title"], "chars": s["chars"],
                        "status": "pending", "approved_at": None})
    return out


def sync(root, force=False):
    """按当前 `global.md` 对齐分段状态。返回 `(state, reason)`。

    reason: `first` 首次建账 / `doc_changed` 内容变了（全部作废）/
    `aligned` 分段数或标题变了 / `unchanged` 没动。
    """
    doc = doc_path(root)
    text = read_text(doc) if doc.exists() else ""
    segs = split_segments(text)
    sha = _sha(text)
    st = load_state(root)
    if not st:
        reason = "first"
        st = {"version": 1, "doc_sha256": sha, "invalidations": 0,
              "segments": _blank(segs)}
    elif force or st.get("doc_sha256") != sha:
        reason = "doc_changed"
        st["invalidations"] = int(st.get("invalidations") or 0) + 1
        st["doc_sha256"] = sha
        st["segments"] = _blank(segs)
    else:
        old_n = len(st.get("segments") or [])
        st["segments"] = _blank(segs, prev=st.get("segments"))
        st["doc_sha256"] = sha
        reason = "aligned" if len(st["segments"]) != old_n else "unchanged"
    st["updated_at"] = _now()
    _save_state(root, st)
    return st, reason


def summary(root, do_sync=True):
    """分段审批概况（GUI / CLI / API 共用的读模型）。"""
    st, _ = sync(root) if do_sync else (load_state(root), "unchanged")
    segs = st.get("segments") or []
    approved = [s for s in segs if s.get("status") == "approved"]
    pending = [s for s in segs if s.get("status") != "approved"]
    return {
        "segments": segs,
        "total": len(segs),
        "approved": len(approved),
        "pending": len(pending),
        "pending_ids": [s.get("id") for s in pending],
        "all_approved": bool(segs) and not pending,
        "doc_sha256": st.get("doc_sha256", ""),
        "invalidations": int(st.get("invalidations") or 0),
        "updated_at": st.get("updated_at", ""),
        "doc_exists": doc_path(root).exists(),
    }


def set_segment(root, seg_id, approved):
    """把某段标为通过/未通过。返回 `(ok, msg)`。"""
    st, _ = sync(root)
    segs = st.get("segments") or []
    hit = next((s for s in segs if s.get("id") == seg_id), None)
    if hit is None:
        return False, ("没有分段 %s（现有：%s）" %
                       (seg_id, "、".join(s.get("id", "?") for s in segs) or "无"))
    hit["status"] = "approved" if approved else "pending"
    hit["approved_at"] = _now() if approved else None
    st["updated_at"] = _now()
    _save_state(root, st)
    return True, ("分段 %s（%s）%s" %
                  (seg_id, hit.get("title", ""), "已通过" if approved else "已撤销"))


# ---------------------------------------------------------------- 放行判据
def project_cfg():
    """读 `config/system.yaml`（**CWD 优先**，与 approve.py 同一取法）；读不到按 {} 处理。

    gate_status 的 cfg 缺省就用它 —— 否则不传 cfg 的调用方（segment_cli status）
    会因为「键缺失」被判成放行，状态页永远说「可以」，闸形同虚设。
    """
    try:
        from utils.config_io import load_config_yaml
        return load_config_yaml("config/system.yaml") or {}
    except Exception:                                       # noqa: BLE001
        return {}


def gate_status(root, cfg=None, stage=2):
    """stage2 放行判据（**唯一真源**）。返回 `(ok, human_message)`。

    ok=False 时 human_message 是给人看的一段话：还差哪几段 + 下一条命令怎么敲。
    非 stage2 恒放行；`gates.segment_approval` 键缺失或为 false 时放行；
    切不出 2 段时放行（契约 2）。`cfg=None` → 读 CWD 的 config/system.yaml。
    """
    if stage != 2:
        return True, ""
    if cfg is None:
        cfg = project_cfg()
    gates = (cfg or {}).get("gates") or {}
    if "segment_approval" not in gates:
        return True, ""                      # 键缺失 = 保持改动前行为（契约 4）
    if not gates.get("segment_approval"):
        return True, ""
    doc = doc_path(root)
    if not doc.exists():
        return True, ""                      # 大纲还没有 → 不是分段审批能管的事
    if len(split_segments(read_text(doc))) < 2:
        return True, ""                      # 契约 2：切不出两段 = 等同整本审批
    sm = summary(root)
    if not sm["total"] or sm["all_approved"]:
        return True, ""
    ids = "、".join(sm["pending_ids"][:8])
    more = " 等" if sm["pending"] > 8 else ""
    # 单行，不是段落：这条既要给 CLI 打印，也要进 GUI 的单行 toast
    # （多行文案塞进 toast 会折成一坨，命令就没人看得见了）。
    msg = ("阶段2 还有 %d/%d 段未通过（%s%s）—— 逐段读完再放行"
           "（分段不降低强度：全过才放行）：python scripts/segment_cli.py list，"
           "approve <段号> 全过后再跑 python scripts/approve.py --stage 2"
           % (sm["pending"], sm["total"], ids, more))
    return False, msg
