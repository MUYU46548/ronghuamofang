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

STAGE_KEYS = ("1", "2", "3", "4", "5", "6", "7")


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
        return self.data["stages"][str(stage)]["status"]

    def set_stage(self, stage, status, **extra):
        st = self.data["stages"][str(stage)]
        st["status"] = status
        st.update(extra)
        self.save()

    def mark_stage_done(self, stage):
        self.set_stage(stage, "done", finished_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    def set_approved(self, stage, approved=True):
        """阶段审批门（如阶段 2 需人工确认）。"""
        self.data["stages"][str(stage)]["approved"] = approved
        self.save()

    def is_approved(self, stage):
        return bool(self.data["stages"][str(stage)].get("approved", False))

    # ---------- 章节状态（阶段 4 断点） ----------
    def completed_chapters(self, stage=4):
        return list(self.data["stages"][str(stage)].get("completed_chapters", []))

    def mark_chapter_done(self, chapter, stage=4):
        st = self.data["stages"][str(stage)]
        st.setdefault("completed_chapters", [])
        if chapter not in st["completed_chapters"]:
            st["completed_chapters"].append(chapter)
            st["completed_chapters"].sort()
        self._drop_failed(st, chapter)
        self.save()

    def mark_chapter_failed(self, chapter, error, stage=4):
        st = self.data["stages"][str(stage)]
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
        return list(self.data["stages"][str(stage)].get("failed_chapters", []))

    def _drop_failed(self, st, chapter):
        st["failed_chapters"] = [f for f in st.get("failed_chapters", []) if f["n"] != chapter]

    # ---------- 审查→精修状态（P0 闭环） ----------
    def get_review_status(self, chapter, stage=4):
        """获取章节审查状态：pending / reviewed / revised / skipped。"""
        st = self.data["stages"][str(stage)]
        return st.get("review_status", {}).get(str(chapter), "pending")

    def set_review_status(self, chapter, status, stage=4):
        """设置章节审查状态。status: pending/reviewed/revised/skipped。"""
        st = self.data["stages"][str(stage)]
        st.setdefault("review_status", {})
        st["review_status"][str(chapter)] = status
        self.save()

    def get_review_report(self, stage=4):
        """获取审查报告路径（如已生成）。"""
        st = self.data["stages"][str(stage)]
        return st.get("review_report")

    def set_review_report(self, report_path, stage=4):
        """记录审查报告路径。"""
        st = self.data["stages"][str(stage)]
        st["review_report"] = report_path
        self.save()

    def clear_review_status(self, stage=4):
        """清除全部章节审查状态（重跑审查时调用）。"""
        st = self.data["stages"][str(stage)]
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
