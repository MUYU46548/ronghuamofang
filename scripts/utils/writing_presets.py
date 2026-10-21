# -*- coding: utf-8 -*-
"""作品级多套写作预设（2026-10-21，roadmap 10.3）。

## 解决什么

写作要求（文风 / 风格笔记 / 单章字数 / 文体 / 语言 / 题材）散在 `project.yaml` 里，
只能有一份。作者常想存多套来回切：冷峻悬疑风 vs 明快日常风、散文 vs 叙事 ——
改一次要手改好几个键，切回来还得记得改了什么。

预设是**可切换的层**，project.yaml 是**当前生效值**：
- 保存 = 把当前（或指定）值快照成 `data/presets/writing/<id>.json`
- 切换 = 校验预设 → **留 history** → 写回 project.yaml（走 set_book_fields，
  定向改写 + 备份 + 回读校验，不整份 dump）

## 键的选择（只收"写作要求"，不收"作品身份"）

- **收**：style / style_notes / target_words / form / language / genre
  —— 这些是"怎么写"，换风格时一起换。
- **不收**：name / chapters / author / user_outline / word_template /
  style_reference —— 书名章数作者是**作品身份**，大纲是内容本身，
  模板是基础设施；style_reference 是**工作区内路径**，换书即失效。
  把身份字段混进预设，切换会把 A 书改成 B 书的名字（2026-10-07 五连炸同族事故）。

## 判据（确定性）

- 预设出现**未知键** → 拒绝保存/应用，并列出可选键（不静默忽略：忽略等于
  用户以为存了其实没存）
- 切换后**读回 project.yaml 必须真的变**（set_book_fields 自带回读校验）
- 每次切换留 history（谁、何时、从什么值切到什么值）—— 切回上一套有据可查
- form 值必须在 writing_form.FORMS 内（不认的文体拒收，防把书切成不存在的判据）
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

PRESET_DIR = Path("data/presets/writing")
HISTORY_PATH = Path("data/presets/writing/history.jsonl")
PRESET_KEYS = ("style", "style_notes", "target_words", "form", "language", "genre")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MIN_STYLE_CHARS = 2


def preset_path(pid, root="."):
    return Path(root) / PRESET_DIR / ("%s.json" % pid)


def _validate(data, root="."):
    """校验预设内容。返回 (ok, msg, cleaned)。"""
    if not isinstance(data, dict):
        return False, "预设必须是对象（键值对）", {}
    # `_saved_at` 是本模块自己写的元数据（保存时间），不是用户内容 ——
    # 校验必须放过它，否则"保存的东西自己应用不了"（2026-10-21 测试实测抓到）。
    content = {k: v for k, v in data.items() if k != "_saved_at"}
    unknown = [k for k in content if k not in PRESET_KEYS]
    if unknown:
        return False, ("预设含不认识的键：%s —— 只收写作要求（%s）；"
                       "书名/章数/作者是作品身份，不进预设"
                       % ("、".join(unknown), "、".join(PRESET_KEYS))), {}
    cleaned = {k: content[k] for k in PRESET_KEYS if k in content}
    if not cleaned:
        return False, "预设是空的（至少要有 %s 之一）" % "、".join(PRESET_KEYS), {}
    if "style" in cleaned and len(str(cleaned["style"] or "").strip()) < MIN_STYLE_CHARS:
        return False, "style 太短（少于 %d 字）—— 一句都没写清，存了也没意义" % MIN_STYLE_CHARS, {}
    if "target_words" in cleaned:
        tw = cleaned["target_words"]
        if not (isinstance(tw, list) and len(tw) == 2
                and all(isinstance(x, int) and x > 0 for x in tw) and tw[0] <= tw[1]):
            return False, "target_words 必须是 [下限, 上限] 两个正整数", {}
    if "form" in cleaned:
        from utils import writing_form as wf
        if str(cleaned["form"]) not in wf.FORMS:
            return False, ("form=%s 不认识（可选：%s）"
                           % (cleaned["form"], "、".join(wf.FORMS))), {}
    for k in ("style_notes", "language", "genre"):
        if k in cleaned and cleaned[k] is not None \
                and not isinstance(cleaned[k], str):
            return False, "%s 必须是文本" % k, {}
    return True, "", cleaned


def check_id(pid):
    pid = str(pid or "").strip()
    if not _ID_RE.match(pid):
        return False, ("预设 id 只能小写字母/数字/中划线/下划线（32 字内），"
                       "收到：%r" % pid)
    return True, pid


def list_presets(root="."):
    d = Path(root) / PRESET_DIR
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(read_text(p))
        except (OSError, ValueError):
            out.append({"id": p.stem, "error": "解析失败"})
            continue
        out.append({
            "id": p.stem,
            "name": str(data.get("style") or "")[:40],
            "keys": [k for k in PRESET_KEYS if k in (data or {})],
            "form": (data or {}).get("form"),
        })
    return out


def current_values(root="."):
    """从 project.yaml 读当前生效值（只取预设键）。读不到的键不出现在结果里。"""
    from utils.config_io import load_config_yaml
    try:
        proj = load_config_yaml(str(Path(root) / "config" / "project.yaml")) or {}
    except Exception:                                        # noqa: BLE001
        return {}
    book = proj.get("book") or {}
    return {k: book[k] for k in PRESET_KEYS if k in book}


def save_preset(pid, data, root="."):
    """保存预设（覆盖同名）。返回 (ok, msg)。"""
    ok, pid = check_id(pid)
    if not ok:
        return False, pid
    ok, msg, cleaned = _validate(data, root)
    if not ok:
        return False, msg
    p = preset_path(pid, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cleaned["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    write_text(p, json.dumps(cleaned, ensure_ascii=False, indent=2))
    return True, "已保存预设「%s」（%d 个键）" % (pid, len(cleaned))


def apply_preset(pid, root="."):
    """切换到预设：留 history → 写回 project.yaml。返回 (ok, msg)。"""
    ok, pid = check_id(pid)
    if not ok:
        return False, pid
    p = preset_path(pid, root)
    if not p.exists():
        return False, "没有这个预设：%s（可选：%s）" % (
            pid, "、".join(x["id"] for x in list_presets(root)) or "无")
    try:
        data = json.loads(read_text(p))
    except (OSError, ValueError) as e:
        return False, "预设文件损坏：%s" % e
    ok, msg, cleaned = _validate(data, root)
    if not ok:
        return False, "预设本身不合格，拒绝应用：%s" % msg
    cleaned.pop("_saved_at", None)

    prev = current_values(root)
    # history 先落：写 project.yaml 之前就留下"从什么切走"的证据。
    # set_book_fields 自己也会备份整个 project.yaml，这里的 history 是**预设维度**的
    # （谁在何时把哪些键从什么值切到了哪个预设），两者不互相替代。
    hist = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "preset": pid,
        "prev": {k: prev.get(k) for k in cleaned if k in prev},
        "applied": cleaned,
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(Path(root) / HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False) + "\n")

    from utils.project_config import set_book_fields
    import utils.project_config as _pc
    # ⚠️ set_book_fields 按**脚本位置**定位 project.yaml 与备份目录（模块级常量）。
    # root ≠ 仓库根时（API --root 指向别的书档 / 测试临时根）会写错书、
    # 备份还会落进真实仓库的 config/history/ —— 与 foreshadow.STATUS_PATH
    # 同族的坑，这里显式重定向（用完还原）。
    _saved_yaml, _saved_bak = _pc.PROJECT_YAML, _pc.BACKUP_DIR
    _pc.PROJECT_YAML = Path(root) / "config" / "project.yaml"
    _pc.BACKUP_DIR = Path(root) / "config" / "history"
    try:
        ok, msg = set_book_fields(cleaned)
    finally:
        _pc.PROJECT_YAML, _pc.BACKUP_DIR = _saved_yaml, _saved_bak
    if not ok:
        return False, "写回 project.yaml 失败：%s" % msg
    changed = [k for k in cleaned if prev.get(k) != cleaned[k]]
    return True, ("已切换到预设「%s」：%s 生效%s"
                  % (pid, "、".join(changed) or "无变化",
                     "（上一套值在 history.jsonl，切回有据）"))


def history(root=".", limit=20):
    p = Path(root) / HISTORY_PATH
    if not p.exists():
        return []
    out = []
    for ln in read_text(p).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out[-limit:]
