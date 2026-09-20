# -*- coding: utf-8 -*-
"""progress.json 读写与断点续跑状态（架构文档 v2 5.1）。"""
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

DEFAULT_PROGRESS = {
    "project": "",
    "version": 3,
    "current_stage": 1,
    "stages": {},
    "last_modified": "",
    "budget": {"limit_yuan": 300, "spent_yuan": 0.0, "paused": False},
}

# 阶段键的唯一真源。**必须**覆盖 orchestrator.STAGES 的全部阶段号 ——
# orchestrator 的默认遍历是 `range(from_stage, 9)`，即会取到**阶段 8**
# （stage8_markdown_export，Markdown 分卷导出）。
#
# S9 缺陷（2026-09-19 由 F3 用例暴露）：此处原为 ("1"..."7")，而 orchestrator
# 会跑到阶段 8 → `orchestrator.py:213` 的 `progress.stage_status(8)` 抛
# `KeyError: '8'`。该异常穿透 `run()`，由 finally 兜底把 runs 标成 `crashed`
# —— **默认全流程走到最后一站必崩**，且崩在「断点跳过」判断上而非任何业务逻辑。
# 修法：把阶段 8 纳入键集；同时下面加 `_ensure_stage()` 做纵深防御，
# 让外部传入越界阶段号时**自动登记**而不是 KeyError。
STAGE_KEYS = ("1", "2", "3", "4", "5", "6", "7", "8")


def _new_stage(status="pending"):
    return {"status": status}


class ProgressManager:
    """progress.json 封装：加载即自动补全缺失阶段键，保存时更新 last_modified。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(read_text(self.path))
            except (json.JSONDecodeError, ValueError):
                data = {}
        else:
            data = {}
        base = deepcopy(DEFAULT_PROGRESS)
        base.update({k: v for k, v in data.items() if k in base})
        base["stages"] = {k: data.get("stages", {}).get(k, _new_stage()) for k in STAGE_KEYS}
        return base

    def save(self):
        self.data["last_modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2))

    # ---------- 阶段状态 ----------
    def stage_status(self, stage):
        return self._ensure_stage(stage)["status"]

    def _ensure_stage(self, stage):
        """纵深防御：阶段键不存在时自动登记，返回其 dict。

        STAGE_KEYS 是「已声明的阶段」，但 orchestrator 的遍历范围是
        `range(from_stage, 9)` —— 两个数字一旦再次失同步，原先直接下标
        `self.data["stages"][str(stage)]` 会抛 KeyError 崩掉整个 run
        （见 S9）。此处主动补键，把「配置不一致」降级为「多一个键」，
        而不是「全流程崩溃」。

        注意：这只是兜底，**不等于** STAGE_KEYS 可以不管 ——
        缺失的键不会出现在 GUI 阶段列表里，所以上面仍要把阶段 8 声明齐。
        """
        key = str(stage)
        if key not in self.data["stages"]:
            self.data["stages"][key] = _new_stage()
        return self.data["stages"][key]

    def set_stage(self, stage, status, **extra):
        st = self._ensure_stage(stage)
        st["status"] = status
        st.update(extra)
        self.save()

    def mark_stage_done(self, stage):
        self.set_stage(stage, "done", finished_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    def set_approved(self, stage, approved=True):
        """阶段审批门（如阶段 2 需人工确认）。"""
        self._ensure_stage(stage)["approved"] = approved
        self.save()

    def is_approved(self, stage):
        return bool(self._ensure_stage(stage).get("approved", False))

    def get_stage_retry_count(self, stage):
        """获取阶段已自动重试次数（用于 GUI 状态提示）"""
        return int(self._ensure_stage(stage).get("retry_count", 0))

    # ---------- 章节状态（阶段 4 断点） ----------
    def completed_chapters(self, stage=4):
        return list(self._ensure_stage(stage).get("completed_chapters", []))

    def mark_chapter_done(self, chapter, stage=4):
        st = self._ensure_stage(stage)
        st.setdefault("completed_chapters", [])
        if chapter not in st["completed_chapters"]:
            st["completed_chapters"].append(chapter)
            st["completed_chapters"].sort()
        self._drop_failed(st, chapter)
        self.save()

    def mark_chapter_failed(self, chapter, error, stage=4):
        st = self._ensure_stage(stage)
        st.setdefault("failed_chapters", [])
        for item in st["failed_chapters"]:
            if item["n"] == chapter:
                item["error"] = error
                item["attempts"] = item.get("attempts", 0) + 1
                self.save()
                return
        st["failed_chapters"].append({"n": chapter, "error": error, "attempts": 1})
        self.save()

    def failed_chapters(self, stage=4):
        return list(self._ensure_stage(stage).get("failed_chapters", []))

    def _drop_failed(self, st, chapter):
        st["failed_chapters"] = [f for f in st.get("failed_chapters", []) if f["n"] != chapter]

    # ---------- 审查→精修状态（P0 闭环） ----------
    def get_review_status(self, chapter, stage=4):
        """获取章节审查状态：pending / reviewed / revised / skipped。"""
        st = self._ensure_stage(stage)
        return st.get("review_status", {}).get(str(chapter), "pending")

    def set_review_status(self, chapter, status, stage=4):
        """设置章节审查状态。status: pending/reviewed/revised/skipped。"""
        st = self._ensure_stage(stage)
        st.setdefault("review_status", {})
        st["review_status"][str(chapter)] = status
        self.save()

    def get_review_report(self, stage=4):
        """获取审查报告路径（如已生成）。"""
        st = self._ensure_stage(stage)
        return st.get("review_report")

    def set_review_report(self, report_path, stage=4):
        """记录审查报告路径。"""
        st = self._ensure_stage(stage)
        st["review_report"] = report_path
        self.save()

    def set_proofread_report(self, report_path, stage=5):
        """记录校对报告路径。"""
        st = self._ensure_stage(stage)
        st["proofread_report"] = report_path
        self.save()

    def clear_review_status(self, stage=4):
        """清除全部章节审查状态（重跑审查时调用）。"""
        st = self._ensure_stage(stage)
        if "review_status" in st:
            del st["review_status"]
        if "review_report" in st:
            del st["review_report"]
        self.save()

    # ---------- 预算 ----------
    def budget(self):
        return self.data["budget"]

    def add_cost(self, yuan):
        self.data["budget"]["spent_yuan"] = round(self.data["budget"].get("spent_yuan", 0.0) + yuan, 4)
        self.save()
