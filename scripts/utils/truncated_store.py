# -*- coding: utf-8 -*-
"""残稿（被截断的正文）的可见与采纳（2026-10-20，v0.8.5）。

## 为什么

`finish_reason == "length"` 时我们不落盘、把残缺文本隔离到 `data/state/truncated/`
（这条纪律是对的：残缺的正文绝不能冒充成品进 `data/chapters/`）。但结果是
**人看不见它** —— 失败之后用户只看到「失败」，不知道模型其实已经写了 3000 字。

书童把这个叫「失败恢复候选」。我们的差别是：

- **采纳不等于完成**：采纳只把残稿写进 `raw/NN.md` 并打一个侧车标记，
  `is_chapter_complete` 见到标记仍判「未完成」（残稿就是残稿，还得走审稿）。
- **不自动采纳**：任何自动采纳都等于替作者决定"这段能要"，而截断点之后的情节
  可能是整段缺失。必须人看一眼。
- **标记可清**：用户自己改完/补齐后，点「已补齐」清掉标记，正常流程继续。

侧车标记存在 `data/state/truncated/adopted.json`，**不写进正文**（正文里加注释
会污染导出与字数统计）。
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text
from utils.truncation import TRUNC_DIR

ADOPTED_NAME = "adopted.json"
RAW_DIR = Path("data/chapters/raw")
# 采纳时单章最少字数：太短多半是刚开始就断了，写进 raw 反而挡住真正的重写
MIN_ADOPT_CHARS = 300


def _adopted_path():
    return TRUNC_DIR / ADOPTED_NAME


def adopted_marks():
    """读侧车标记 {章号(int): {from, at, chars}}；损坏一律返回 {}（不抛）。"""
    try:
        raw = json.loads(_adopted_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[int(k)] = v if isinstance(v, dict) else {"from": str(v)}
        except (TypeError, ValueError):
            continue
    return out


def _save_marks(marks):
    TRUNC_DIR.mkdir(parents=True, exist_ok=True)
    _adopted_path().write_text(
        json.dumps({str(k): v for k, v in sorted(marks.items())},
                   ensure_ascii=False, indent=1), encoding="utf-8")


def is_adopted(chapter_no):
    """该章是否「采纳自残稿且还没补齐」——`is_chapter_complete` 的判据之一。"""
    try:
        return int(chapter_no) in adopted_marks()
    except (TypeError, ValueError):
        return False


def clear_mark(chapter_no):
    """用户补齐/改完后清标记（返回是否真的清掉了）。"""
    marks = adopted_marks()
    try:
        no = int(chapter_no)
    except (TypeError, ValueError):
        return False
    if no not in marks:
        return False
    marks.pop(no, None)
    _save_marks(marks)
    return True


def _chapter_hint(name):
    """从任务名里猜章号（`stage4_ch03_...` / `ch12`）；猜不到返回 None。"""
    m = re.search(r"(?:ch|chapter|第)\s*[_-]?(\d{1,3})", name or "", re.I)
    return int(m.group(1)) if m else None


def list_items():
    """列出所有残稿（新的在前）。**零 token、只读盘。**"""
    items = []
    try:
        files = sorted(TRUNC_DIR.glob("*.txt"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return items
    marks = adopted_marks()
    adopted_from = {v.get("from"): no for no, v in marks.items()}
    for f in files:
        try:
            st = f.stat()
            text = read_text(f)
        except OSError:
            continue
        body = text.strip()
        items.append({
            "name": f.name,
            "chars": len(body),
            "at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "chapter_hint": _chapter_hint(f.stem),
            "preview": body[:120],
            # 太短的残稿不提供采纳（多半刚开始就断了）
            "adoptable": len(body) >= MIN_ADOPT_CHARS,
            "adopted_as": adopted_from.get(f.name),
        })
    return items


def read_item(name):
    """读一份残稿全文。名称只允许 basename，防 ../ 越界。"""
    safe = Path(str(name or "")).name
    if not safe or safe != str(name):
        raise ValueError("非法的残稿名")
    p = TRUNC_DIR / safe
    if not p.exists():
        raise FileNotFoundError("残稿不存在: " + safe)
    return read_text(p)


def adopt(name, chapter_no, replace=False):
    """把残稿采纳为第 `chapter_no` 章的 raw 草稿。返回 (ok, msg)。

    拒绝的三种情况，都**明说原因**（不静默跳过）：
      1. 残稿太短（< MIN_ADOPT_CHARS）
      2. 目标章已有内容且未 replace
      3. 章号越界（1-999）
    """
    try:
        no = int(chapter_no)
    except (TypeError, ValueError):
        return False, "章号必须是整数"
    if not (1 <= no <= 999):
        return False, "章号须在 1-999 之间"

    try:
        body = read_item(name).strip()
    except (ValueError, FileNotFoundError) as e:
        return False, str(e)
    if len(body) < MIN_ADOPT_CHARS:
        return False, ("残稿只有 %d 字（下限 %d）—— 太短多半是刚开始就断了，"
                       "建议直接重写这一章" % (len(body), MIN_ADOPT_CHARS))

    dst = RAW_DIR / ("%02d.md" % no)
    if dst.exists():
        old = read_text(dst).strip()
        if old and not replace:
            return False, ("第 %d 章已有 %d 字内容，未覆盖。确认要换掉时勾「允许覆盖」"
                           % (no, len(old)))

    # 补标题：残稿是被截断的**正文**，没有 `## 第N章`。不补的话，
    # 用户补齐后点「已补齐」，`is_chapter_complete` 仍会因"标题格式"判未完成
    # —— 那时用户会以为"点坏了"，实际是采纳时少了一步（2026-10-20 实测踩到）。
    import re as _re
    if not _re.search(r"^##?\s*第\s*\d+\s*章", body, _re.MULTILINE):
        body = "## 第%d章\n\n%s" % (no, body)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    write_text(dst, body + "\n")
    marks = adopted_marks()
    marks[no] = {
        "from": Path(name).name,
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "chars": len(body),
    }
    _save_marks(marks)
    return True, ("已采纳为第 %d 章草稿（%d 字）。⚠️ 它仍是**残稿**：系统不会当它完成，"
                  "请补齐后点「已补齐」，再走审稿。" % (no, len(body)))
