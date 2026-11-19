# -*- coding: utf-8 -*-
"""写作 Skill 导入（2026-11-19，取代原“平台文风预设”）。

## 为什么是“用户导入”而不是“内置几套文风”

作者 2026-10-20 明确砍掉了“起点风 / 番茄风”这类平台预设：**文风只能他自己定**。
11-19 再次拍板：**只留用户导入，不自创**。所以本模块一行文风模板都不写，只提供
一个“把你自己写的规矩装进来”的口子。

## 目录与文件

    materials/skills/<id>/SKILL.md        # 唯一入口，纯 Markdown
    materials/skills/<id>/references/*.md # 可选，**不自动注入**（供人看）

frontmatter 可选，支持 `name` / `stages` / `enabled`；缺省 `stages: [write]`。

## 五道防线（“用户导入奇怪 skill 把质量拉垮”靠机制，不靠劝）

1. **只收纯文本**：目录内出现 `.py/.js/.sh/.bat/.exe` 等可执行后缀 → 整份拒装并点名文件。
   本功能永不执行任何导入物。
2. **装前必须看全文**：未在界面上点过“确认”的（或确认后文件被改过、sha 变了的）
   **一律不注入**。改一个字就要重看一遍 —— 与反向写沙盒的 sha 重审同一口径。
3. **按阶段绑定，默认不绑不收**：`stages` 只认 `write`（写稿）/ `revise`（润色），
   其他值拒装。绑定是显式的，没绑的阶段一个字都不注入。
4. **字数硬预算**：单份 ≤ `MAX_SKILL_CHARS`，一次注入合计 ≤ `MAX_TOTAL_CHARS`。
   超了不静默截断：整份跳过，并在注入块尾写明“另有 N 套未注入”。
   （理由同 `writing_rules.MAX_CHARS`：这段每章都要发一次，写成长篇论文等于每章多付一遍钱。）
5. **可一键停用 / 删除**：删除是移到 `data/state/skills_trash/`，不 `rm`；
   索引每次现扫磁盘，删了不留孤儿。

## 不改判据

注入的只是提示词。字数、正文退化、12-gram 复读、篇幅配比这些硬闸门照旧生效，
任何导入物都不能放宽它们 —— 这是“拉低质量”的最后一道，也是唯一不可谈判的一道。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from utils.file_io import read_text, write_text

SKILLS_REL = Path("materials") / "skills"
STATE_REL = Path("data") / "state" / "skills_state.json"
TRASH_REL = Path("data") / "state" / "skills_trash"
SKILL_FILE = "SKILL.md"

MAX_SKILL_CHARS = 4000
MAX_TOTAL_CHARS = 4000
MAX_FILES = 40

#: 只这两个阶段接得进提示词。plan / review 没有对应注入点，写上去就是假按钮，
#: 所以直接不接受 —— 宁可少一个选项，不要让作者以为自己绑上了。
STAGES = {"write": "写稿", "revise": "润色"}

#: 可执行后缀。命中即整份拒装（安全边界，不是可选项）。
BAD_SUFFIXES = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".zsh",
    ".ps1", ".psm1", ".bat", ".cmd", ".exe", ".dll", ".com", ".vbs", ".vbe",
    ".jar", ".bin", ".so", ".dylib", ".msi", ".scr", ".lnk", ".reg",
}

BLOCK_TITLE = "## 写作手法（作者导入；与“作品写作约束”冲突时以那一节为准）"

_ID_RE = re.compile(r"^[^\s/\\:*?\"<>|.][^\s/\\:*?\"<>|]{0,39}$")


def skills_dir(root=None):
    return (Path(root) if root else Path(".")) / SKILLS_REL


def state_path(root=None):
    return (Path(root) if root else Path(".")) / STATE_REL


def _trash_dir(root=None):
    return (Path(root) if root else Path(".")) / TRASH_REL


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _load_state(root=None):
    p = state_path(root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(read_text(p) or "{}")
    except Exception:                                          # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data, root=None):
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def valid_id(sid):
    s = str(sid or "").strip()
    if not s or s in (".", "..") or not _ID_RE.match(s):
        return False
    return True


def _parse_front(text):
    """极简 frontmatter：只认 `key: value` 行，值里的 `[a, b]` 解析成列表。

    解析失败不抛：返回空 meta 与原文 —— 缺 frontmatter 是合法用法（默认 stages=[write]），
    不该因为用户多写了一个冒号就整份用不了。
    """
    meta, body = {}, text or ""
    if not body.startswith("---"):
        return meta, body
    lines = body.split("\n")
    end = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return meta, body
    for ln in lines[1:end]:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k, v = k.strip().lower(), v.strip().strip("'\"")
        if not k:
            continue
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        meta[k] = v
    return meta, "\n".join(lines[end + 1:])


def _scan_problems(folder):
    """目录级问题（不进提示词，只给界面看）。"""
    problems = []
    if not folder.is_dir():
        return ["目录不存在"]
    files = 0
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        files += 1
        if files > MAX_FILES:
            problems.append("文件数超过 %d，拒绝扫描（导入物应当只有几份 md）" % MAX_FILES)
            break
        if p.suffix.lower() in BAD_SUFFIXES:
            try:
                rel = p.relative_to(folder).as_posix()
            except ValueError:
                rel = p.name
            problems.append("含可执行文件 %s —— 本功能只收纯文本，整份拒装" % rel)
    return problems


def _entry(sid, root=None):
    """扫一份 skill。返回 dict（含 text 原文）。任何异常都变成 problems，不抛。"""
    st_all = _load_state(root)
    st = st_all.get(sid) or {}
    folder = skills_dir(root) / sid
    out = {
        "id": sid,
        "name": st.get("name") or sid,
        "stages": list(st.get("stages") or ["write"]),
        "enabled": bool(st.get("enabled", True)),
        "chars": 0,
        "sha": "",
        "approved": False,
        "approved_at": st.get("approved_at") or "",
        "problems": [],
        "text": "",
    }
    out["problems"].extend(_scan_problems(folder))
    p = folder / SKILL_FILE
    if not p.is_file():
        out["problems"].append("缺少 %s（只认这个文件名）" % SKILL_FILE)
        return out
    try:
        raw = read_text(p) or ""
    except Exception as e:                                     # noqa: BLE001
        out["problems"].append("读不出来：%s: %s" % (type(e).__name__, str(e)[:120]))
        return out
    meta, body = _parse_front(raw.replace("\r\n", "\n"))
    # 权威顺序：**state 优先**，文件 frontmatter 只在该键还没有 state 记录时作为初值。
    # 反过来会让界面上的“停用/改绑”被文件里的 enabled: true 无声盖掉
    # （2026-11-19 单测 S10 抓到：界面点了停用，注入照旧 —— 症状是“点了没反应”）。
    if meta.get("name") and not st.get("name"):
        out["name"] = str(meta["name"])
    if meta.get("stages") is not None and not st.get("stages"):
        raw_stages = meta["stages"]
        stages = [raw_stages] if isinstance(raw_stages, str) else list(raw_stages)
        out["stages"] = [str(s).strip().lower() for s in stages if str(s).strip()]
    if meta.get("enabled") is not None and "enabled" not in st:
        out["enabled"] = str(meta["enabled"]).strip().lower() not in ("false", "0", "no")
    text = body.strip()
    out["text"] = text
    out["chars"] = len(text)
    out["sha"] = _sha(text)
    out["approved"] = bool(st.get("sha")) and st.get("sha") == out["sha"]
    if not text:
        out["problems"].append("正文是空的（只有 frontmatter？）")
    if out["chars"] > MAX_SKILL_CHARS:
        out["problems"].append(
            "正文 %d 字，超过单份上限 %d 字 —— 请删到上限以内（不截断：截断会把一条规矩砍成半句）"
            % (out["chars"], MAX_SKILL_CHARS))
    bad = [s for s in out["stages"] if s not in STAGES]
    if not out["stages"]:
        out["problems"].append("stages 是空的 —— 不绑定阶段就等于不生效，请写 write 或 revise")
    elif bad:
        out["problems"].append(
            "stages 里有不认识的阶段 %s（只接受 %s）"
            % ("、".join(bad), " / ".join(sorted(STAGES))))
    if not out["approved"]:
        if st.get("sha"):
            out["problems"].append("正文在上次确认之后被改过（sha 变了）—— 需要在界面上重新看一遍全文再确认")
        else:
            out["problems"].append("还没确认过 —— 先在界面上看全文，点确认才会注入")
    return out


def scan(root=None):
    """列全部 skill（现扫磁盘，不建索引 → 删目录不留孤儿）。"""
    base = skills_dir(root)
    if not base.is_dir():
        return []
    items = []
    for d in sorted(base.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not valid_id(d.name):
            items.append({"id": d.name, "name": d.name, "stages": [], "enabled": False,
                          "chars": 0, "sha": "", "approved": False, "approved_at": "",
                          "problems": ["目录名含非法字符（不能有空格与路径分隔符）"], "text": ""})
            continue
        items.append(_entry(d.name, root))
    return items


def get(sid, root=None):
    if not valid_id(sid):
        return None
    folder = skills_dir(root) / sid
    if not folder.is_dir():
        return None
    return _entry(sid, root)


def approve(sid, root=None):
    """作者看过全文后确认。记下当前 sha —— 之后文件一改就重新变“未确认”。"""
    e = get(sid, root)
    if not e:
        return False, "没有这份 skill：%s" % sid
    blocks = [p for p in e["problems"] if "还没确认过" not in p and "被改过" not in p]
    if blocks:
        return False, "还不能确认：" + "；".join(blocks)
    st = _load_state(root)
    rec = st.get(sid) or {}
    rec.update({"sha": e["sha"], "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "name": e["name"], "chars": e["chars"]})
    rec.setdefault("stages", e["stages"])
    rec.setdefault("enabled", e["enabled"])
    st[sid] = rec
    _save_state(st, root)
    return True, "已确认《%s》（%d 字，绑定：%s）" % (
        e["name"], e["chars"], "、".join(STAGES.get(s, s) for s in rec.get("stages") or []))


def set_binding(sid, enabled=None, stages=None, root=None):
    """改启用 / 绑定阶段。写 state，**不改作者的文件**（那是他的稿子）。"""
    e = get(sid, root)
    if not e:
        return False, "没有这份 skill：%s" % sid
    st = _load_state(root)
    rec = st.get(sid) or {"sha": "", "approved_at": ""}
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    if stages is not None:
        clean = [str(s).strip().lower() for s in stages if str(s).strip()]
        bad = [s for s in clean if s not in STAGES]
        if bad:
            return False, "不认识的阶段：%s（只接受 %s）" % (
                "、".join(bad), " / ".join(sorted(STAGES)))
        if not clean:
            return False, "stages 不能是空的（不绑定 = 不生效，要停用请用 enabled=false）"
        rec["stages"] = clean
    rec.setdefault("name", e["name"])
    st[sid] = rec
    # 保存前回读校验（项目纪律：界面说成功、盘上没变是最难查的一类）
    _save_state(st, root)
    back = _load_state(root).get(sid) or {}
    if back != rec:
        return False, "写入后回读不一致，已放弃（请检查 data/state 是否可写）"
    return True, "已更新《%s》" % e["name"]


def create(sid, text, root=None, name=None, stages=None, enabled=True):
    """从界面粘贴新建。已存在则拒绝（改文件请自己编辑，避免两边打架）。"""
    if not valid_id(sid):
        return False, "id 不合法：只用字母数字下划线短横（不能有空格、斜杠、点）"
    folder = skills_dir(root) / sid
    if folder.exists():
        return False, "已存在同名 skill：%s（要改请直接编辑文件）" % sid
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        return False, "正文是空的"
    if len(body) > MAX_SKILL_CHARS:
        return False, "正文 %d 字，超过单份上限 %d 字" % (len(body), MAX_SKILL_CHARS)
    st_list = [str(s).strip().lower() for s in (stages or ["write"]) if str(s).strip()]
    bad = [s for s in st_list if s not in STAGES]
    if bad:
        return False, "不认识的阶段：%s（只接受 %s）" % (
            "、".join(bad), " / ".join(sorted(STAGES)))
    folder.mkdir(parents=True, exist_ok=True)
    front = ["---", "name: %s" % (name or sid), "stages: [%s]" % ", ".join(st_list),
             "enabled: %s" % ("true" if enabled else "false"), "---", ""]
    write_text(folder / SKILL_FILE, "\n".join(front) + body + "\n")
    # 回读校验：写成功必须真的能从盘上读回来
    e = get(sid, root)
    if not e or e["chars"] != len(body):
        return False, "写入后回读不一致（写到 %s 失败）" % (folder / SKILL_FILE).as_posix()
    return True, "已新建《%s》，共 %d 字。**还没确认** —— 看一遍全文再点确认才会生效。" % (
        e["name"], e["chars"])


def remove(sid, root=None):
    """移到回收目录，不删。索引是现扫的，移走即消失，不留孤儿。"""
    if not valid_id(sid):
        return False, "id 不合法"
    folder = skills_dir(root) / sid
    if not folder.is_dir():
        return False, "没有这份 skill：%s" % sid
    dest = _trash_dir(root) / ("%s_%s" % (sid, time.strftime("%Y%m%d_%H%M%S")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.rename(dest)
    except OSError as e:
        return False, "移动到回收目录失败：%s: %s" % (type(e).__name__, str(e)[:120])
    st = _load_state(root)
    if st.pop(sid, None) is not None:
        _save_state(st, root)
    return True, "已移到 %s（不是删除，想恢复可以搬回去）" % dest.as_posix()


def block(stages=("write",), root=None):
    """提示词片段。没有可用 skill 时返回空串 —— 不占一个字的上下文。

    返回 (text, stats)。stats 供日志与界面复用：injected / skipped / chars。
    """
    want = {str(s).strip().lower() for s in (stages or ())}
    stats = {"injected": [], "skipped": [], "chars": 0}
    if not want:
        return "", stats
    parts, used = [], 0
    for e in scan(root):
        if not e["enabled"]:
            continue
        if not (set(e["stages"]) & want):
            continue
        # “还没确认 / 被改过”是正常中间态（界面上有标记），不刷日志；
        # 含脚本、超字数、缺 SKILL.md 这类硬问题必须出声 —— 那是装坏了。
        hard = [p for p in e["problems"]
                if "还没确认过" not in p and "被改过" not in p]
        if hard or not e["approved"]:
            if hard:
                stats["skipped"].append({"id": e["id"], "why": "；".join(hard)})
            continue
        piece = "### %s\n%s" % (e["name"], e["text"])
        if used + len(piece) > MAX_TOTAL_CHARS:
            stats["skipped"].append(
                {"id": e["id"],
                 "why": "超出合计预算 %d 字（本次已用 %d 字）" % (MAX_TOTAL_CHARS, used)})
            continue
        parts.append(piece)
        used += len(piece)
        stats["injected"].append(e["id"])
    if not parts:
        if stats["skipped"]:
            print("[skills] 有 %d 套绑定在本阶段但未注入：%s"
                  % (len(stats["skipped"]),
                     "；".join("%s（%s）" % (s["id"], s["why"]) for s in stats["skipped"])))
        return "", stats
    text = BLOCK_TITLE + "\n" + "\n\n".join(parts)
    if stats["skipped"]:
        text += ("\n\n（另有 %d 套作者导入的写法因超预算未注入：%s）"
                 % (len(stats["skipped"]), "、".join(s["id"] for s in stats["skipped"])))
    stats["chars"] = len(text)
    return text, stats
