# -*- coding: utf-8 -*-
"""讨论件：把"讨论出来的结论"变成写作阶段**必须落实的约束**（2026-11-03）。

## 为什么必须另立一个构件

作者 2026-11-03 的判词是结构性的：墨坊此前只有"单发执行管线"（丢任务→跑批→出 Word），
讨论的产物最多写成一份提案、走一次**单轮审批**，然后——**没有任何东西读它**：

    $ grep -n "discuss" scripts/stage4_writing.py scripts/stage3_chapter_outline.py
    （零命中）

于是 2026-10-25“潮汐档案”实锤失败：03/05 章讨论过、也重写过，`refined/` 里还是旧稿 ——
不是"没保存"，是**讨论与正文之间根本没有通路**。

本模块补的就是这条通路，判据三条（都能变红，见 `tests/unit/test_discussion.py`）：

1. **讨论有地方落**：`data/discuss/<章号>/<主题>.md`，只追加轮次，谁说了什么都留痕；
2. **结论结构化**：作者拍板后写 `constraints.json`（必须发生 / 绝不发生 / 人物口吻 /
   伏笔处置），不是一段散文 —— 散文没法被机械核对；
3. **结论真的进正文**：写作任务的提示词里注入这块约束；且**定稿晚于正文**时，
   该章自动进"待重写"（`stale_chapters`）—— 否则改了讨论、正文还是旧稿，
   就是“潮汐档案”那次的样子。

数据布局（全部在项目根下，只写 `data/discuss/`）：

    data/discuss/03/结局要不要开信.md     # 人读的讨论稿（轮次 + 【结论】）
    data/discuss/03/结局要不要开信.json   # 机读：状态 / 约束 / 定稿时间
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

DISCUSS_DIR = Path("data") / "discuss"

# 约束的四个维度（顺序即提示词里的呈现顺序）
DIMENSIONS = (
    ("must", "必须发生"),
    ("must_not", "绝不发生"),
    ("voice", "人物口吻"),
    ("foreshadow_plant", "本章要埋的伏笔"),
    ("foreshadow_payoff", "本章要揭的伏笔"),
)

_STATUS_OPEN, _STATUS_DECIDED = "open", "decided"


# ---------------------------------------------------------------- 路径/基础
def _slug(topic: str) -> str:
    """主题 → 文件名。中文保留（可读），只清掉路径分隔符与空白。"""
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(topic or "").strip())
    s = s.strip("-.")
    return s[:60] or "讨论"


def thread_dir(chapter, root="."):
    return Path(root) / DISCUSS_DIR / ("%02d" % int(chapter))


def thread_paths(chapter, topic, root="."):
    d = thread_dir(chapter, root)
    stem = _slug(topic)
    return d / (stem + ".md"), d / (stem + ".json")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


def _write_json(p: Path, data: dict) -> None:
    from utils.file_io import write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 会话体（C20，2026-11-16）
#
# 三个文件的分工，别混：
#
#   <主题>.md           人读投影：只追加轮次，谁说了什么都留痕（判据 1，永不改写）
#   <主题>.json         机读状态：status / constraints / decided_at（定稿用）
#   <主题>.session.json  模型读真源：messages[] + 归档轮次 + 摘要锚点（可压缩）
#   <主题>.ai.json      AI 轮的结构化产物：stance / evidence / action（可被程序消费）
#
# 为什么 md 不能当模型输入：md 的轮次头带时间戳，每次追加都让整段前缀变化，
# 压成文本再塞进请求等于每轮都换前缀（缓存命中率归零）。messages[] 是协议原生
# 结构，前面几轮逐字不动 —— 见 docs/施工单-对话式改稿-20261115.md 第三节。
#
# 为什么 md 仍是真源：session 丢了/写坏了必须能从 md 重建
# （`load_session` 会回填），反过来不成立。所以 append_round **先写 md 后写 session**。

SESSION_VERSION = 1
SESSION_KEEP_DEFAULT = 12        # 默认带最近 12 轮原文（配置 discuss.session_keep 可改）

_ROUND_HEAD_RE = re.compile(
    r"### 第 (\d+) 轮 · (作者|AI|编辑)（([^）]*)）\n\n(.*?)(?=\n### 第 |\n## 【结论】|\Z)",
    re.S)
_ROLE_OF_SPEAKER = {"作者": "author", "AI": "ai", "编辑": "editor"}


def session_path(chapter, topic, root="."):
    md, _js = thread_paths(chapter, topic, root)
    return md.with_suffix(".session.json")


def ai_payload_path(chapter, topic, root="."):
    md, _js = thread_paths(chapter, topic, root)
    return md.with_suffix(".ai.json")


def _is_derived_json(p: Path) -> bool:
    """同目录里的**派生** json（会话体 / AI 轮产物）不是"一篇讨论"。

    `list_threads` 用 `glob("*.json")` 扫目录，这两个后缀也匹配 —— 不过滤的话
    讨论列表会凭空多出几行没有轮次、没有状态的空条目（点进去还读不到 md）。
    """
    name = p.name
    return name.endswith(".session.json") or name.endswith(".ai.json")


def _rounds_from_md(md: Path) -> list:
    """从人读投影里把轮次解析回来（session 丢失时的回填路径）。"""
    try:
        from utils.file_io import read_text
        body = read_text(md)
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for m in _ROUND_HEAD_RE.finditer(body or ""):
        out.append({"round": int(m.group(1)),
                    "role": _ROLE_OF_SPEAKER.get(m.group(2), "ai"),
                    "at": (m.group(3) or "").strip(),
                    "text": (m.group(4) or "").strip()})
    return sorted(out, key=lambda x: x["round"])


def load_session(chapter, topic, root="."):
    """读会话体。文件不存在/为空 → 从 md 回填（老讨论稿升级后也能被模型看见）。"""
    p = session_path(chapter, topic, root)
    data = _read_json(p)
    if not data or not isinstance(data.get("messages"), list):
        md, _js = thread_paths(chapter, topic, root)
        msgs = _rounds_from_md(md) if md.exists() else []
        data = {"version": SESSION_VERSION, "chapter": int(chapter),
                "topic": topic or "", "messages": msgs,
                "archived_rounds": [], "summary": "", "updated_at": _now()}
    data.setdefault("version", SESSION_VERSION)
    data.setdefault("archived_rounds", [])
    data.setdefault("summary", "")
    return data


def save_session(chapter, topic, data, root="."):
    p = session_path(chapter, topic, root)
    data = dict(data or {})
    data["version"] = SESSION_VERSION
    data["chapter"] = int(chapter)
    data["topic"] = topic or ""
    data["updated_at"] = _now()
    _write_json(p, data)
    return p


def append_session_turn(chapter, topic, role, text, root=".", round_no=None):
    """把一轮发言写进会话体（round_no 缺省 = 已有最大轮次 + 1）。"""
    data = load_session(chapter, topic, root)
    msgs = list(data.get("messages") or [])
    n = int(round_no if round_no is not None else
            (max([int(m.get("round") or 0) for m in msgs] or [0]) + 1))
    role = str(role or "ai").lower()
    # 幂等：同一轮号已经存在就不重复追加。注意**仍然要 save** ——
    # 走到这里说明 session 之前不存在（刚刚由 md 回填），不落盘的话
    # 每次读都要重新解析 md，"会话体"就等于从来没建过
    # （用例 Q1a 钉的就是这条：append_round 先写 md，回填时已含这一轮）。
    if not any(int(m.get("round") or 0) == n for m in msgs):
        msgs.append({"round": n, "role": role, "text": str(text or "").strip(),
                     "at": _now()})
        data["messages"] = sorted(msgs, key=lambda m: int(m.get("round") or 0))
    save_session(chapter, topic, data, root)
    return n


def session_messages(chapter, topic, root=".", keep=None):
    """取给模型看的历史消息（不含 system）。返回 (messages, omitted_rounds)。

    messages 的形状：``[{"role": "user"|"assistant", "content": str}]``。
    作者的发言是 user，AI 的发言是 assistant，编辑按 user 处理（带 [编辑] 前缀）。
    相邻同类角色会合并成一条 —— 协议允许连续同角色，但两条 user 连着会让模型
    分不清"哪句是本轮指令"，合并后语义更稳。
    """
    data = load_session(chapter, topic, root)
    allmsgs = list(data.get("messages") or [])
    keep = int(keep or SESSION_KEEP_DEFAULT)
    kept, omitted = allmsgs, []
    if keep > 0 and len(allmsgs) > keep:
        kept = allmsgs[-keep:]
        omitted = [int(m.get("round") or 0) for m in allmsgs[:-keep]]
    out = []
    for m in kept:
        role = "assistant" if str(m.get("role")) == "ai" else "user"
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        if role == "user" and str(m.get("role")) == "editor":
            text = "[编辑] " + text
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    return out, omitted


# ---------------------------------------------------------------- 开题/追加/定稿
def open_thread(chapter, topic, question="", root="."):
    """开一个讨论。返回 (md_path, json_path)。

    重复开同一个主题**不覆盖**已有的稿子（讨论是只追加的），只补一条分隔。
    """
    md, js = thread_paths(chapter, topic, root)
    md.parent.mkdir(parents=True, exist_ok=True)
    state = _read_json(js)
    if not md.exists():
        head = ("# 第 %d 章讨论：%s\n\n" % (int(chapter), topic or "未命名")) + \
               ("**待解决的问题**：%s\n\n" % question if question else "") + \
               ("> 只追加：每轮一行，作者与 AI 都写在下面。定稿时由作者拍板写【结论】。\n\n")
        from utils.file_io import write_text
        write_text(md, head)
    if not state:
        state = {"chapter": int(chapter), "topic": topic or "未命名",
                 "status": _STATUS_OPEN, "created_at": _now(), "rounds": 0,
                 "md": md.as_posix(), "decided_at": None, "constraints": None}
        _write_json(js, state)
    return md, js


def append_round(chapter, topic, role, text, root="."):
    """追加一轮发言（只追加，永不改写历史）。返回轮次号。"""
    md, js = thread_paths(chapter, topic, root)
    if not md.exists():
        open_thread(chapter, topic, root=root)
    state = _read_json(js)
    n = int(state.get("rounds") or 0) + 1
    speaker = {"author": "作者", "ai": "AI", "editor": "编辑"}.get(
        str(role or "ai").lower(), str(role or "AI"))
    from utils.file_io import append_text
    append_text(md, "\n### 第 %d 轮 · %s（%s）\n\n%s\n" % (n, speaker, _now(), str(text or "").strip()))
    state["rounds"] = n
    state["last_role"] = str(role or "ai").lower()
    state["updated_at"] = _now()
    if state.get("status") != _STATUS_DECIDED:
        state["status"] = _STATUS_OPEN
    _write_json(js, state)
    # 会话体（C20，2026-11-16）：md 是**人读投影**，session 是喂模型的视图。
    # 先写 md 再写 session：md 永远是真源，session 丢了能从 md 回填
    # （`load_session` 会做），反过来不成立 —— 所以顺序不能颠倒。
    try:
        append_session_turn(chapter, topic, role, text, root=root, round_no=n)
    except Exception:                                          # noqa: BLE001
        # 会话体只是模型的视图，写不进去不该让"这一轮"失败（正文已经在 md 里）。
        # 不打印的话，坏了没人知道 —— 所以留痕。
        import traceback
        print("[discussion] ⚠ 会话体写入失败（讨论稿已落，模型下次会看不到这一轮）：")
        traceback.print_exc()
    return n


def decide(chapter, topic, must=(), must_not=(), voice=(), foreshadow_plant=(),
           foreshadow_payoff=(), summary="", root=".", by="author"):
    """作者拍板：把讨论收敛成**约束**，落到 `constraints.json` 与题材稿的【结论】段。

    返回 (ok, msg)。没写任何一条约束视为无效（一段"我们聊过了"不算结论）。
    """
    md, js = thread_paths(chapter, topic, root)
    if not md.exists():
        return False, "没有这个讨论：%s" % md.as_posix()
    cons = {
        "must": [str(x).strip() for x in must if str(x).strip()],
        "must_not": [str(x).strip() for x in must_not if str(x).strip()],
        "voice": [str(x).strip() for x in voice if str(x).strip()],
        "foreshadow_plant": [str(x).strip() for x in foreshadow_plant if str(x).strip()],
        "foreshadow_payoff": [str(x).strip() for x in foreshadow_payoff if str(x).strip()],
    }
    if not any(cons.values()):
        return False, ("结论里一条约束都没有 —— 讨论不是结论。至少要写清"
                       "“必须发生 / 绝不发生 / 人物口吻 / 伏笔处置”中的一条。")
    state = _read_json(js) or {"chapter": int(chapter), "topic": topic}
    state.update({"status": _STATUS_DECIDED, "decided_at": _now(),
                  "decided_by": by, "constraints": cons,
                  "summary": str(summary or "").strip()})
    _write_json(js, state)

    # 人读的【结论】段（重复定稿就替换这一段，历史轮次不动）
    from utils.file_io import read_text, write_text
    body = read_text(md)
    body = re.split(r"\n## 【结论】.*\Z", body, maxsplit=1, flags=re.S)[0].rstrip() + "\n"
    lines = ["", "## 【结论】", ""]
    if summary:
        lines += [str(summary).strip(), ""]
    for key, label in DIMENSIONS:
        if cons[key]:
            lines.append("**%s**" % label)
            lines += ["- " + x for x in cons[key]]
            lines.append("")
    lines.append("> 定稿：%s（作者拍板）。这些不是建议，是本章写作的约束；"
                 "定稿晚于正文时，该章会被判为待重写。" % _now())
    write_text(md, body + "\n".join(lines) + "\n")
    return True, "已定稿：%s（%d 条约束）" % (
        ", ".join("%s %d" % (label, len(cons[key])) for key, label in DIMENSIONS if cons[key]),
        sum(len(v) for v in cons.values()))


# ---------------------------------------------------------------- 读取/渲染
def next_step(state):
    """这个讨论下一步该谁说话（③ 接着推进，2026-11-03）。

    讨论是往复的：断了以后，人和 agent 都要知道"轮到谁、问什么"，
    否则每次都要从头读一遍稿子才知道自己在哪。
    """
    st = state or {}
    if st.get("status") == _STATUS_DECIDED:
        return {"role": None, "ask": "已定稿 —— 要改就开一个新讨论，别在结论上打补丁"}
    last = str(st.get("last_role") or "")
    rounds = int(st.get("rounds") or 0)
    if rounds == 0:
        return {"role": "author", "ask": "还没人说话：先说清这一章到底在犹豫什么"}
    if last == "author":
        return {"role": "ai", "ask": "作者刚说完 —— 给出 2-3 个可执行的走向，并写清代价"}
    if last == "ai":
        return {"role": "author", "ask": "AI 已给方案 —— 作者选一个，或在界面上定稿"}
    return {"role": "author", "ask": "继续"}


def open_threads(chapter=None, root="."):
    """还没定稿的讨论（"接着聊"的入口）。"""
    return [s for s in list_threads(chapter, root) if s.get("status") != _STATUS_DECIDED]


def list_threads(chapter=None, root="."):
    """列出讨论（按章或全书）。零 token。"""
    base = Path(root) / DISCUSS_DIR
    out = []
    if not base.is_dir():
        return out
    dirs = [base / ("%02d" % int(chapter))] if chapter else sorted(
        [d for d in base.iterdir() if d.is_dir() and d.name.isdigit()])
    for d in dirs:
        if not d.is_dir():
            continue
        for js in sorted(d.glob("*.json")):
            if _is_derived_json(js):        # 会话体 / AI 轮产物不是"一篇讨论"
                continue
            st = _read_json(js)
            if st:
                out.append(st)
    return sorted(out, key=lambda s: (int(s.get("chapter") or 0), s.get("topic") or ""))


def load_constraints(chapter, root=".", only_decided=True):
    """该章所有**已定稿**讨论的约束合并（跨主题按维度拼接）。"""
    merged = {key: [] for key, _ in DIMENSIONS}
    for st in list_threads(chapter, root):
        if only_decided and st.get("status") != _STATUS_DECIDED:
            continue
        cons = st.get("constraints") or {}
        for key, _ in DIMENSIONS:
            for item in cons.get(key) or []:
                if item not in merged[key]:
                    merged[key].append(item)
    return merged


def render_for_prompt(chapter, root=".", cap=12):
    """渲染成写进任务文件的约束块（没有已定稿讨论 → 空串，不占提示词）。

    `cap`：每个维度最多列几条（防止讨论跑题成几十条把提示词顶爆）。
    """
    cons = load_constraints(chapter, root)
    if not any(cons.values()):
        return ""
    lines = ["## 本章讨论结论（作者已拍板，必须落实）", "",
             "下面这些不是建议，是**本章写作的约束**。与大纲冲突时以这里为准；"
             "确实做不到就在正文里用情节自然处理，不许装作没看见。", ""]
    for key, label in DIMENSIONS:
        items = cons[key][:cap]
        if not items:
            continue
        lines.append("**%s**" % label)
        lines += ["- " + x for x in items]
        if len(cons[key]) > cap:
            lines.append("- （另有 %d 条，见 data/discuss/）" % (len(cons[key]) - cap))
        lines.append("")
    return "\n".join(lines)


def stale_chapters(raw_dir, root=".", suffixes=(".md",)):
    """**定稿晚于正文**的章号：讨论改了、正文还是旧的 → 该重写。

    判据只看 mtime（与 `is_usable_output` 同一口径）：不靠人记得"我改过讨论"。
    返回 [(章号, 正文路径, 定稿时间)]。
    """
    out = []
    seen = set()
    raw = Path(raw_dir)
    for st in list_threads(None, root):
        if st.get("status") != _STATUS_DECIDED:
            continue
        n = int(st.get("chapter") or 0)
        decided_at = str(st.get("decided_at") or "")
        if not n or not decided_at or n in seen:
            continue
        for suf in suffixes:
            p = raw / ("%02d%s" % (n, suf))
            if not p.exists():
                continue
            try:
                if time.strftime("%Y-%m-%dT%H:%M:%S",
                                 time.localtime(p.stat().st_mtime)) < decided_at:
                    seen.add(n)      # 同一章多个讨论主题：只报一次
                    out.append((n, p, decided_at))
            except OSError:
                continue
            break
    return out
