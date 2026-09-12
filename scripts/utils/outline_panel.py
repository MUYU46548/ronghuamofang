# -*- coding: utf-8 -*-
"""大纲 GUI 面板服务层：结构化视图 / 节点级 AI 精修 / 版本 diff / 多方案拼合。

与 nf_api.py 分层：本模块只做逻辑（可被 CLI 复用），端点只负责 HTTP 与状态码。
红线遵守：
- 评分只调用 outline_review.review()，不复制/改写其评分逻辑；
- 结构解析/渲染统一走 utils.outline_struct，不另立格式规范；
- 全文精修 refine_outline.run_refine() 原样保留，节点精修是并行通道。
"""
import json
import re
import shutil
import time
from pathlib import Path

from utils.file_io import read_text, write_text
from utils import outline_struct as osr

GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"
SETTING = "data/setting/setting.json"
DRAFT_RE = re.compile(r"^global_draft_(\d+)\.md$")


# ---------------------------------------------------------------- 结构化视图

def _decorate(entries, review_entries):
    """把 review() 的评分贴到结构条目上（按出现顺序对齐）。"""
    out = []
    revs = list(review_entries or [])
    chars_cache = None
    for i, e in enumerate(entries):
        r = revs[i] if i < len(revs) else {}
        item = dict(e)
        item["score"] = r.get("score")
        item["level"] = r.get("level")
        item["flags"] = r.get("flags", {})
        item["reasons"] = r.get("reasons", [])
        if chars_cache is None:
            chars_cache = {}
        item["chars"] = osr.match_chars(e.get("text", ""), _char_names())
        out.append(item)
    return out


_CHAR_CACHE = {"path": None, "mtime": None, "names": []}


def _char_names(setting_path=SETTING):
    """角色名索引（带 mtime 缓存，避免每次请求都读盘）。"""
    p = Path(setting_path)
    try:
        mt = p.stat().st_mtime if p.exists() else None
    except OSError:
        mt = None
    if _CHAR_CACHE["path"] == str(p) and _CHAR_CACHE["mtime"] == mt:
        return _CHAR_CACHE["names"]
    names = osr.build_char_index(setting_path)
    _CHAR_CACHE.update({"path": str(p), "mtime": mt, "names": names})
    return names


def build_structure(global_path=GLOBAL, setting_path=SETTING, run_review=True):
    """解析 global.md + 贴评分，返回前端树形视图所需的完整 JSON。

    run_review=False 时跳过评分（拼合过程中的轻量预解析用）。
    """
    p = Path(global_path)
    if not p.exists():
        return {
            "exists": False,
            "error": "整体大纲不存在: " + str(global_path) + "（先跑阶段2）",
            "acts": [], "nodes": [], "plan": [],
            "expected_chapters": None, "issues": [], "summary": {},
            "raw": "", "path": str(p),
        }
    text = read_text(p)
    struct = osr.parse_global(text)

    review_nodes, review_plan, issues, summary = [], [], [], {}
    if run_review:
        try:
            import outline_review as ov
            rv = ov.review(global_path, setting_path)
            review_nodes = rv.get("nodes", [])
            review_plan = rv.get("plan", [])
            issues = rv.get("issues", [])
            summary = rv.get("summary", {})
        except Exception as e:                       # 评分失败不阻断视图
            issues = ["体检评分失败: " + type(e).__name__ + ": " + str(e)[:120]]

    # 章节规划按章节区间归属到 act（供树形三级分支）
    plan = _decorate(struct["plan"], review_plan)
    _assign_plan_acts(plan, struct["nodes"])

    return {
        "exists": True,
        "path": str(Path(global_path).resolve()),
        "title": struct["title"],
        "acts": [{"name": a, "text": (struct["acts"].get(a) or "")} for a in osr.ACT_NAMES],
        "nodes": _decorate(struct["nodes"], review_nodes),
        "plan": plan,
        "expected_chapters": struct["expected_chapters"],
        "issues": issues,
        "summary": summary,
        "raw": text,
    }


def _assign_plan_acts(plan, nodes):
    """给规划条目补 act 归属：优先用节点章节区间，缺省按全书比例切分。"""
    # 1) 先解析所有规划条目的章节号，得出全书总章数
    for item in plan:
        m = re.search(r"第\s*([\d一二三四五六七八九十百零两]+)\s*章", item.get("text", ""))
        item["chapter_no"] = _cn2int(m.group(1)) if m else None
    total = max([p["chapter_no"] or 0 for p in plan] + [0])
    total = max(total, len(plan), 1)

    # 2) 节点区间（节点含「第a-b章」时用于精确归属）
    ranges = []
    for nd in nodes:
        mm = re.findall(
            r"第\s*([\d一二三四五六七八九十百零两]+)\s*(?:[-~至]\s*([\d一二三四五六七八九十百零两]+))?\s*章",
            nd.get("text", ""))
        for a, b in mm:
            a_i = _cn2int(a)
            if not a_i:
                continue
            b_i = _cn2int(b) if b else a_i
            ranges.append((a_i, b_i or a_i))

    # 3) 归属判定
    for item in plan:
        n = item.get("chapter_no")
        if not n:
            item["act"] = ""
            continue
        hit = next(((a, b) for a, b in ranges if a <= n <= b), None)
        pos = _mid(hit) if hit else n
        item["act"] = _act_by_total(pos, total)


def _act_by_total(pos, total):
    """按全书位置切分四节：前 25% 起 / 50% 承 / 75% 转 / 其余 合。"""
    if total <= 1:
        return "起"
    f = pos / total
    if f <= 0.25:
        return "起"
    if f <= 0.50:
        return "承"
    if f <= 0.75:
        return "转"
    return "合"


def _mid(rng):
    return max(1, int(round((rng[0] + rng[1]) / 2.0)))


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2int(s):
    """中文数字/阿拉伯数字 → int（支持 十/百 组合，够用即止）。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        else:
            return None
    return total + section + num


# ---------------------------------------------------------------- 局部精修（任务2）

def find_entry(struct, entry_id):
    """按 id 取节点/规划条目，返回 (kind, entry) 或 (None, None)。"""
    for it in struct.get("nodes", []):
        if it.get("id") == entry_id:
            return "node", it
    for it in struct.get("plan", []):
        if it.get("id") == entry_id:
            return "plan", it
    return None, None


def neighbors(struct, kind, entry_id):
    """取同类型前 1 / 后 1 条（作为修订锚点，防止跑偏）。"""
    seq = struct["nodes"] if kind == "node" else struct["plan"]
    for i, it in enumerate(seq):
        if it.get("id") == entry_id:
            return (seq[i - 1] if i - 1 >= 0 else None,
                    seq[i + 1] if i + 1 < len(seq) else None)
    return None, None


def build_node_task(cfg, proj, struct, kind, entry, prev, nxt, feedback):
    """构建局部精修任务文件内容（自包含：当前段 + 意见 + 相邻锚点 + 设定集路径）。"""
    from utils.template_loader import load_template
    book = proj.get("book", {}) or {}
    label = "关键节点" if kind == "node" else "章节规划"
    _, body = load_template("stage2_refine_node.md", {
        "entry_kind": label,
        "entry_title": entry.get("title", ""),
        "entry_text": entry.get("text", ""),
        "feedback": feedback,
        "prev_text": (prev or {}).get("text", "（无，本条为首条）"),
        "next_text": (nxt or {}).get("text", "（无，本条为末条）"),
        "path_setting": Path(SETTING).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "book_name": book.get("name", "未命名"),
    })
    return body


CLEAN_RE = re.compile(r"^```[\w]*\s*$|^```\s*$", re.M)


def clean_entry_output(text, fallback_title="", kind="node"):
    """清洗 LLM 返回的单段文本：剥围栏、只取条目行。

    返回 (entry_text, error)；entry_text 为空且 error 非空表示模型输出不可用。
    校验：必须是 `节点N：…` / `第N章：…` 形式的单行条目，且正文不得过短或像日志。
    """
    t = (text or "").replace("\r\n", "\n").strip()
    t = CLEAN_RE.sub("", t).strip()
    if not t:
        return "", "模型未返回内容"

    pat = NODE_ENTRY_RE if kind == "node" else PLAN_ENTRY_RE
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("-*").strip() if line[:1] in "-*" else line
        m = pat.match(line)
        if not m:
            continue
        tail = m.group(2).strip()
        if len(tail) < MIN_ENTRY_CHARS:
            return "", "修订结果过短（%d 字），疑似无效输出" % len(tail)
        if LOG_NOISE_RE.search(tail):
            return "", "修订结果疑似模型日志而非条目内容"
        return line, ""
    return "", "模型输出不是有效的条目格式（%s）" % ("节点N：…" if kind == "node" else "第N章：…")


# 条目行校验（清洗输出用，与 outline_struct 的解析保持一致）
NODE_ENTRY_RE = re.compile(r"^(节点\s*\d+)\s*[：:，,]?\s*(.*)$")
PLAN_ENTRY_RE = re.compile(r"^(第\s*[\d一二三四五六七八九十百零两]+\s*章)\s*[：:，,]?\s*(.*)$")
MIN_ENTRY_CHARS = 4
LOG_NOISE_RE = re.compile(r"已写出|fake|traceback|error|无法|抱歉|作为AI|我是模型", re.I)


def apply_node_refine(cfg, proj, entry_id, feedback, client=None, dry_run=False):
    """节点级 AI 精修主流程。返回 (ok, payload_or_msg)。

    流程：备份 → 定位于段 → 构任务 → LLM → 清洗 → 局部替换 → 结构校验
         → 失败回滚；成功则重评分并返回 diff。
    """
    import stage2_outline as s2
    import outline_review as ov
    import refine_outline as ro

    gp = Path(GLOBAL)
    if not gp.exists():
        return False, "整体大纲不存在: " + GLOBAL
    text = read_text(gp)
    struct = osr.parse_global(text)
    kind, entry = find_entry(struct, entry_id)
    if not entry:
        return False, "未找到条目: " + str(entry_id)
    if not (feedback or "").strip():
        return False, "修订意见不能为空"
    prev, nxt = neighbors(struct, kind, entry_id)

    # 1) 备份（与 refine_outline 同一版本号序列）
    version = ro.next_version(HISTORY_DIR)
    backup = ro.backup_current(GLOBAL, HISTORY_DIR, version)

    if dry_run:
        return True, {"dry_run": True, "version": version, "backup": str(backup),
                      "entry_id": entry_id}

    # 2) 构任务 + 调用
    client = client or _make_default_client(cfg)
    task_body = build_node_task(cfg, proj, struct, kind, entry, prev, nxt, feedback)
    task_dir = "data/state/tasks"
    task = client.write_task(task_dir, "stage2_refine_node.md", task_body)
    result = client.run_task(task)
    if result.get("exit_code") != 0:
        return False, "节点精修子会话失败（已备份 v%d）" % version

    raw_out = result.get("stdout_tail", "")
    new_text, err = clean_entry_output(raw_out, entry.get("title", ""), kind)
    if err:
        return False, err + "（已备份 v%d，大纲未改动）" % version
    if new_text == entry.get("text"):
        return False, "模型返回内容与原文一致（已备份 v%d，大纲未改动）" % version

    # 3) 局部替换
    new_full, old_text = osr.replace_entry(text, entry_id, new_text)
    if new_full is None:
        return False, "回写失败：未能定位条目 " + str(entry_id)

    write_text(gp, new_full)

    # 4) 结构校验，失败回滚
    ok, errors = s2.check_global_outline(GLOBAL)
    if not ok:
        shutil.copy2(backup, gp)                 # 回滚
        return False, "修订后大纲校验失败，已回滚： " + "; ".join(errors)

    # 5) 重新评分 + diff
    new_struct = osr.parse_global(new_full)
    try:
        rv = ov.review(GLOBAL, SETTING)
    except Exception as e:
        rv = {"nodes": [], "plan": [], "issues": ["评分失败: " + str(e)[:100]],
              "summary": {}}

    seq = new_struct["nodes"] if kind == "node" else new_struct["plan"]
    rev_seq = rv.get("nodes" if kind == "node" else "plan", [])
    idx = next((i for i, x in enumerate(seq) if x.get("id") == entry_id), -1)
    scored = rev_seq[idx] if 0 <= idx < len(rev_seq) else {}

    return True, {
        "entry_id": entry_id,
        "kind": kind,
        "version": version,
        "backup": str(backup),
        "old_text": old_text,
        "new_text": new_text,
        "score": scored.get("score"),
        "level": scored.get("level"),
        "flags": scored.get("flags", {}),
        "reasons": scored.get("reasons", []),
        "summary": rv.get("summary", {}),
        "issues": rv.get("issues", []),
        "structure": build_structure(GLOBAL, SETTING),
        "model": result.get("model", ""),
        "cost_yuan": result.get("cost_yuan", 0),
        "char_diff": char_diff(old_text, new_text),
    }


def _make_default_client(cfg):
    from utils.llm_client import make_client
    return make_client(cfg, "default")


def undo_last_refine():
    """撤销：把 history 中最新版本恢复到 global.md（保留当前版为更新版本）。"""
    import refine_outline as ro
    hist = Path(HISTORY_DIR)
    if not hist.is_dir():
        return False, "无历史版本可撤销"
    versions = [(int(m.group(1)), p)
                for p in hist.glob("global_v*.md")
                for m in [re.match(r"global_v(\d+)\.md", p.name)] if m]
    if not versions:
        return False, "无历史版本可撤销"
    versions.sort(key=lambda x: x[0])
    last_v, last_p = versions[-1]

    gp = Path(GLOBAL)
    # 先把「当前版」另存为新版本，撤销后仍可再前进
    new_v = ro.next_version(HISTORY_DIR)
    if gp.exists():
        shutil.copy2(gp, hist / ("global_v%d.md" % new_v))
    shutil.copy2(last_p, gp)
    return True, {"restored_from": "global_v%d.md" % last_v, "saved_current_as": new_v}


# ---------------------------------------------------------------- 版本 diff（任务4）

def list_versions():
    """历史版本列表（时间倒序），每版附评分摘要。"""
    hist = Path(HISTORY_DIR)
    items = []
    gp = Path(GLOBAL)
    if gp.exists():
        try:
            st = gp.stat()
            rv = _safe_review(GLOBAL)
            items.append({
                "version": 0, "label": "当前 (global.md)", "is_current": True,
                "file": GLOBAL,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "mtime": st.st_mtime,
                "score_summary": rv.get("summary", {}),
                "expected_chapters": rv.get("expected_chapters"),
                "nodes": len(rv.get("nodes", [])), "plan": len(rv.get("plan", [])),
            })
        except OSError:
            pass

    if hist.is_dir():
        for p in hist.glob("global_v*.md"):
            m = re.match(r"global_v(\d+)\.md", p.name)
            if not m:
                continue
            v = int(m.group(1))
            try:
                st = p.stat()
            except OSError:
                continue
            rv = _safe_review(p)
            items.append({
                "version": v, "label": "v%d" % v, "is_current": False,
                "file": str(p),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "mtime": st.st_mtime,
                "score_summary": rv.get("summary", {}),
                "expected_chapters": rv.get("expected_chapters"),
                "nodes": len(rv.get("nodes", [])), "plan": len(rv.get("plan", [])),
            })
    items.sort(key=lambda x: (not x["is_current"], -x["mtime"]))
    return items


def _safe_review(path):
    try:
        import outline_review as ov
        return ov.review(str(path), SETTING)
    except Exception:
        return {"summary": {}, "nodes": [], "plan": [], "issues": [], "expected_chapters": None}


def resolve_version(v):
    """把版本号解析为文件路径；0/None → 当前 global.md。非法返回 None。"""
    if v in (None, "", 0, "0"):
        return Path(GLOBAL) if Path(GLOBAL).exists() else None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    p = Path(HISTORY_DIR) / ("global_v%d.md" % n)
    return p if p.exists() else None


def diff_outlines(v1_path, v2_path):
    """结构化节点级 diff：按类型内顺序对齐，标题相同→文本 diff，未匹配→add/del。

    同时给出四节（起承转合）与预计章节数的差异、评分摘要对比。
    """
    t1 = read_text(v1_path)
    t2 = read_text(v2_path)
    s1, s2 = osr.parse_global(t1), osr.parse_global(t2)

    segments = []
    # 四节差异前置展示（有变化才输出）
    for name in osr.ACT_NAMES:
        a1 = (s1["acts"].get(name) or "").strip()
        a2 = (s2["acts"].get(name) or "").strip()
        if a1 == a2:
            continue
        if a1:
            segments.append({"type": "del", "scope": "act", "title": "## " + name, "content": a1})
        if a2:
            segments.append({"type": "add", "scope": "act", "title": "## " + name, "content": a2})

    if s1["expected_chapters"] != s2["expected_chapters"]:
        if s1["expected_chapters"] is not None:
            segments.append({"type": "del", "scope": "meta", "title": "预计章节数",
                             "content": str(s1["expected_chapters"])})
        if s2["expected_chapters"] is not None:
            segments.append({"type": "add", "scope": "meta", "title": "预计章节数",
                             "content": str(s2["expected_chapters"])})

    segments += _diff_seq(s1["nodes"], s2["nodes"], "node")
    segments += _diff_seq(s1["plan"], s2["plan"], "plan")

    return {
        "segments": segments,
        "acts_v1": s1["acts"], "acts_v2": s2["acts"],
        "score_diff": {
            "v1": _safe_review(v1_path).get("summary", {}),
            "v2": _safe_review(v2_path).get("summary", {}),
        },
        "counts": {
            "same": sum(1 for x in segments if x["type"] == "same"),
            "add": sum(1 for x in segments if x["type"] == "add"),
            "del": sum(1 for x in segments if x["type"] == "del"),
            "change": sum(1 for x in segments if x["type"] == "change"),
        },
    }


def _norm_title(t):
    return re.sub(r"\s+", "", (t or ""))


def _diff_seq(seq1, seq2, scope):
    """同类型条目按顺序做最小编辑序列，再对匹配上的做字符级 diff。"""
    import difflib
    k1 = [_norm_title(x["title"]) for x in seq1]
    k2 = [_norm_title(x["title"]) for x in seq2]
    sm = difflib.SequenceMatcher(a=k1, b=k2, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                a, b = seq1[i], seq2[j]
                if a["text"] == b["text"]:
                    out.append({"type": "same", "scope": scope, "title": a["title"],
                                "content": a["text"]})
                else:
                    out.append({"type": "change", "scope": scope, "title": a["title"],
                                "content": a["text"], "content_v2": b["text"],
                                "char_diff": char_diff(a["text"], b["text"])})
        else:
            for i in range(i1, i2):
                out.append({"type": "del", "scope": scope, "title": seq1[i]["title"],
                            "content": seq1[i]["text"]})
            for j in range(j1, j2):
                out.append({"type": "add", "scope": scope, "title": seq2[j]["title"],
                            "content": seq2[j]["text"]})
    return out


def char_diff(a, b):
    """中文按字符切分的字符级 diff（红线：不按空格切分）。

    返回 [{"op": "="|"add"|"del", "text": "..."}]，供前端高亮。
    """
    import difflib
    sm = difflib.SequenceMatcher(a=a or "", b=b or "", autojunk=False)
    parts = []
    src, dst = a or "", b or ""
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append({"op": "=", "text": src[i1:i2]})
        else:
            if i2 > i1:
                parts.append({"op": "del", "text": src[i1:i2]})
            if j2 > j1:
                parts.append({"op": "add", "text": dst[j1:j2]})
    return parts


# ---------------------------------------------------------------- 多方案（任务3）

def draft_path(n):
    return Path("data/outline") / ("global_draft_%d.md" % n)


def draft_files():
    """当前存在的 draft 文件名列表。"""
    d = Path("data/outline")
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("global_draft_*.md"))


def list_drafts():
    d = Path("data/outline")
    out = []
    if d.is_dir():
        for p in sorted(d.glob("global_draft_*.md")):
            m = DRAFT_RE.match(p.name)
            if not m:
                continue
            rv = _safe_review(p)
            struct = osr.parse_global(read_text(p))
            out.append({
                "id": int(m.group(1)),
                "name": p.name,
                "summary": rv.get("summary", {}),
                "expected_chapters": struct["expected_chapters"],
                "nodes": struct["nodes"],
                "plan": struct["plan"],
                "acts": struct["acts"],
            })
    out.sort(key=lambda x: x["id"])
    return out


def cleanup_drafts(keep=None):
    """删除临时 draft（拼合完成/用户取消时调用）。"""
    removed = []
    d = Path("data/outline")
    if not d.is_dir():
        return removed
    for p in d.glob("global_draft_*.md"):
        if keep and p.name in keep:
            continue
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def compose_from_drafts(selections, act_source=1):
    """按用户勾选把多份 draft 拼合成新的 global.md。

    selections: [{"draft": 1, "kind": "node"|"plan", "index": 0}, ...]
    act_source: 四节（起承转合）取自哪份 draft
    返回 (ok, payload_or_msg)
    """
    import stage2_outline as s2
    drafts = {d["id"]: d for d in list_drafts()}
    if not drafts:
        return False, "没有可用的 draft（先执行多方案生成）"
    if act_source not in drafts:
        act_source = sorted(drafts.keys())[0]
    base = drafts[act_source]

    nodes, plan = [], []
    for sel in selections or []:
        d = drafts.get(sel.get("draft"))
        if not d:
            continue
        kind = sel.get("kind")
        seq = d["nodes"] if kind == "node" else d["plan"]
        i = sel.get("index")
        if not (isinstance(i, int) and 0 <= i < len(seq)):
            continue
        src = seq[i]
        entry = {"title": src["title"], "text": src["text"], "tail": src["tail"]}
        if kind == "node":
            nodes.append(entry)
        else:
            plan.append(entry)

    exp = base.get("expected_chapters") or len(plan)

    # 拼合约束校验
    warns = []
    if len(nodes) < exp:
        warns.append("关键节点 %d 条 < 预计章节数 %d" % (len(nodes), exp))
    if len(plan) != exp:
        warns.append("章节规划 %d 条 ≠ 预计章节数 %d" % (len(plan), exp))

    struct = {
        "title": _title_of(act_source),
        "acts": base["acts"],
        "nodes": nodes,
        "plan": plan,
        "expected_chapters": exp,
    }
    text = osr.render_global(struct)
    write_text(GLOBAL, text)

    ok, errors = s2.check_global_outline(GLOBAL)
    if not ok:
        return False, "拼合后大纲校验失败: " + "; ".join(errors)

    return True, {
        "warnings": warns,
        "expected_chapters": exp,
        "nodes": len(nodes),
        "plan": len(plan),
        "structure": build_structure(GLOBAL, SETTING),
    }


def _title_of(draft_id):
    """从某份 draft 原文取一级标题（拼合时继承书名）。"""
    p = draft_path(draft_id)
    if p.exists():
        m = re.match(r"^#\s+(.*)$", read_text(p), re.M)
        if m:
            return m.group(1).strip()
    return "整体大纲"
