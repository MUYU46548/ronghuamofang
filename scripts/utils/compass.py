# -*- coding: utf-8 -*-
"""指南针（Compass，2026-10-21，roadmap 7.3）—— 全书的终局与主线锚。

## 为什么需要它

`global.md` 定一次终身：写到第 100 章时，第 1 天写的"全书大纲"早被改得面目全非，
但没人说得清**现在这本书最终要去哪、还活跃着哪几条线**。滚动规划解决了"下一卷写什么"，
缺的是"全书往哪去"的**单一锚点**。指南针就是这个锚：

- **终局方向**：一句话 —— 这本书最终要落在哪（变更必须留 history）。
- **活跃长线**：最多 5 条真正在推进的线（>5 条等于没有主线，拒绝写入）。
- **规模估计**：预计总章数 ±30%（与实际章数偏离过大 → 告警不阻断）。

## 职责边界

- 只读拼装 + 校验 + 注入；不自动改写（方向是作者的决定）。
- 与已写章节矛盾时**告警不阻断**（compass 是方向不是硬约束；硬约束是设定集 locked）。
- 没有 compass.json → 注入块为空 + stage3 明说"未设终局方向"（不编造）。

## 判据（确定性）

- 活跃长线 >5 条 → 拒绝写入并说清（长线太多 = 没有主线）
- 终局方向为空/过短 → 拒绝
- 终局方向变更 → 自动记 `data/outline/history/compass_vN.json`
- 规模估计与 project.yaml 的 chapters 偏离 >30% → 告警（不阻断）
- 卷边界已跨过而 compass 未更新 → 注入块里带"考虑更新"提示（卷边界自动提示）
"""
import json
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

COMPASS_REL = "data/outline/compass.json"
HISTORY_DIR_REL = "data/outline/history"
MAX_THREADS = 5
MIN_DIRECTION_CHARS = 10
# 规模偏离告警阈值（±30%）：compass 说 300 章、实际配 6 章，多半有一边是错的
SCALE_TOLERANCE = 0.30


def compass_path(root="."):
    return Path(root) / COMPASS_REL


def load(root="."):
    """读指南针。缺失/损坏 → {}（调用方据此注入空块，不编造）。"""
    p = compass_path(root)
    if not p.exists():
        return {}
    try:
        d = json.loads(read_text(p))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def check(compass, total_chapters=None):
    """校验。返回 (ok, problems, warnings)。warnings 不阻断写入。"""
    problems, warnings = [], []
    direction = str(compass.get("ending_direction") or "").strip()
    if len(direction) < MIN_DIRECTION_CHARS:
        problems.append("终局方向为空或过短（少于 %d 字）—— 一句话说不清要去哪，"
                        "就还不是指南针" % MIN_DIRECTION_CHARS)
    threads = compass.get("active_threads")
    if threads is not None:
        if not isinstance(threads, list):
            problems.append("active_threads 必须是列表")
        else:
            if len(threads) > MAX_THREADS:
                problems.append("活跃长线 %d 条超过上限 %d —— 长线太多等于没有主线，"
                                "请合并或明确放弃" % (len(threads), MAX_THREADS))
            for i, t in enumerate(threads):
                if not isinstance(t, dict) or not str(t.get("text") or "").strip():
                    problems.append("第 %d 条活跃长线缺 text（一句话说清这条线是什么）" % (i + 1))
    est = compass.get("scale_estimate")
    if est is not None:
        try:
            est_n = int(est)
            if est_n <= 0:
                problems.append("规模估计必须是正整数（章数）")
            elif total_chapters:
                lo, hi = est_n * (1 - SCALE_TOLERANCE), est_n * (1 + SCALE_TOLERANCE)
                if not (lo <= int(total_chapters) <= hi):
                    warnings.append(
                        "规模估计 %d 章与实际配置 %d 章偏离超过 %d%% —— "
                        "要么更新指南针，要么核对 project.yaml 的 chapters"
                        % (est_n, int(total_chapters), int(SCALE_TOLERANCE * 100)))
        except (TypeError, ValueError):
            problems.append("规模估计必须是整数（章数）")
    return (not problems), problems, warnings


def save(compass, root=".", total_chapters=None):
    """校验 + 落盘（终局方向变更自动留 history）。返回 (ok, msg, warnings)。"""
    ok, problems, warnings = check(compass, total_chapters)
    if not ok:
        return False, "；".join(problems), warnings
    old = load(root)
    compass = dict(compass)
    new_dir = str(compass.get("ending_direction") or "").strip()
    if old and str(old.get("ending_direction") or "").strip() != new_dir:
        _archive(old, root)
        warnings.append("终局方向已变更 —— 旧版存 %s/compass_v*.json（可回溯）"
                        % HISTORY_DIR_REL)
    compass["updated_at"] = datetime.now().isoformat(timespec="seconds")
    p = compass_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(compass, ensure_ascii=False, indent=2))
    return True, "已保存", warnings


def _archive(old, root):
    """终局方向变更前把旧版存进 history/（compass_v1.json 递增）。"""
    d = Path(root) / HISTORY_DIR_REL
    d.mkdir(parents=True, exist_ok=True)
    n = 1
    while (d / ("compass_v%d.json" % n)).exists():
        n += 1
    write_text(d / ("compass_v%d.json" % n),
               json.dumps(old, ensure_ascii=False, indent=2))


def render_context(n, total, cfg=None, proj=None, root="."):
    """stage3/stage4 的注入块：终局方向 + 活跃长线 + 跨卷提示。

    没有 compass → 返回空串（明确的空白；stage3 模板会显示"未设终局方向"提示行）。
    """
    c = load(root)
    if not c:
        return ""
    from utils import volume_arc as va
    size = va.volume_size(cfg, proj)
    cur_v = va.volume_of(n, size)
    lines = ["## 指南针（全书往哪去 —— 写偏时以它为锚）"]
    d = str(c.get("ending_direction") or "").strip()
    if d:
        lines.append("- **终局方向**：%s" % d)
    threads = c.get("active_threads") or []
    if threads:
        lines.append("- **活跃长线**（%d 条，写到相关处要推进，无关处别硬塞）：" % len(threads))
        for t in threads:
            if isinstance(t, dict) and str(t.get("text") or "").strip():
                extra = ("（埋于第 %s 章）" % t["planted"]) if t.get("planted") else ""
                lines.append("  - %s%s" % (str(t["text"]).strip(), extra))
    est = c.get("scale_estimate")
    if est:
        lines.append("- **规模估计**：全书约 %s 章（现在第 %d 卷）" % (est, cur_v))
    upd_v = c.get("volume_at_update")
    try:
        if upd_v is not None and cur_v > int(upd_v) + 1:
            lines.append("> ⚠️ 已跨过第 %s 卷之后 %d 卷而指南针未更新 —— "
                         "终局方向若已漂移，请作者更新指南针（现在改最便宜）。"
                         % (upd_v, cur_v - int(upd_v)))
    except (TypeError, ValueError):
        pass
    return "\n".join(lines)


def missing_note(root="."):
    """stage3 明说"没设终局方向"的提示行（有 compass 时返回空）。"""
    if load(root):
        return ""
    return ("> （指南针未设：`data/outline/compass.json` 不存在 —— 全书终局方向"
            "尚未锚定。长篇建议尽早设一次：终局方向 + 最多 5 条活跃长线，"
            "写到第 100 章时它是唯一还能对得上的锚。）")
