# -*- coding: utf-8 -*-
"""截断留痕：**让「这次失败是不是被 max_tokens 截断」可判定**（2026-10-03）。

## 为什么需要它

`finish_reason == "length"` 时我们**不落盘**、把残缺文本隔离到 `data/state/truncated/`
（这条纪律本身是对的）。但留下的只有一堆**给人看**的 txt —— 机器无从判断
"刚才那次失败属于截断类"，于是 orchestrator 的 `auto_retry` 会照样重试：
**同一个 `max_tokens` 上限再跑一次，只会再截断一次**，等于白烧一倍输入 token
（几万到几十万 token 量级）。这正是本轮"止烧"要治的那类浪费。

所以：截断时**同时**写一份机器可读的标记 `data/state/truncated/last.json`，
orchestrator 重试前查一次"这次失败之后有没有新的截断标记" → 有就不重试，
并明确告诉用户该调哪个键。

判据只有这一份（`mark()` 写 / `after()` 读），调用点两处：
`utils.llm_client`（写）与 `scripts/orchestrator.py`（读）。
"""
import json
from datetime import datetime
from pathlib import Path

TRUNC_DIR = Path("data/state/truncated")
MARK_NAME = "last.json"


def mark(task_path, texts, note=""):
    """把被截断的输出与**机器可读标记**一起落盘。返回标记 dict。

    标记内容刻意只留诊断必需项：任务名、时间、字符数、说明 ——
    不放正文（正文在旁边的 txt 里，`artifacts.py --export truncated` 可整体导出）。
    """
    stamp = datetime.now()
    info = {
        "task": Path(task_path).stem if task_path else "",
        "at": stamp.strftime("%Y-%m-%dT%H:%M:%S"),
        "chars": sum(len(t or "") for t in (texts or [])),
        "note": note or "输出被 max_tokens 截断（finish_reason=length）",
    }
    try:
        TRUNC_DIR.mkdir(parents=True, exist_ok=True)
        (TRUNC_DIR / MARK_NAME).write_text(
            json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return info
    return info


def last():
    """读最近一次截断标记；没有/坏了都返回 None（**绝不抛**：它只是诊断信息）。"""
    try:
        return json.loads((TRUNC_DIR / MARK_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def after(iso_ts):
    """`iso_ts` 之后是否发生过截断。iso_ts 为空/不可解析 → 一律 False。

    为什么用时间戳而不是"文件存在"：截断标记是**累积**的（上次跑留下的还在），
    只看"存在"会让每一次失败都被误判成截断类 → 该重试的也不重试了。
    """
    if not iso_ts:
        return False
    info = last()
    if not info:
        return False
    try:
        return str(info.get("at") or "") >= str(iso_ts)
    except Exception:                                       # noqa: BLE001
        return False
