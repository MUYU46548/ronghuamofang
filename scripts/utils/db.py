# -*- coding: utf-8 -*-
"""NovelForge SQLite 运行记录层。

管理四张表（架构文档 v2 5.2）：
  runs        —— 运行实例
  chapter_log —— 章节执行日志（含自评分）
  cost_log    —— Token/费用明细
  summary_log —— 摘要链版本
"""
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT,
  plan_json TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS chapter_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, stage INTEGER, chapter INTEGER,
  status TEXT, attempts INTEGER, quality INTEGER,
  tokens_in INTEGER, tokens_out INTEGER,
  cost_yuan REAL, error TEXT, session_id TEXT,
  UNIQUE(run_id, stage, chapter)
);
CREATE TABLE IF NOT EXISTS cost_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, stage INTEGER, chapter INTEGER,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER,
  cost_yuan REAL, called_at TEXT,
  estimated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS summary_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, rollup_index INTEGER, path TEXT, created_at TEXT
);
"""


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class RunDB:
    """SQLite 封装：惰性建表，线程内使用（单进程调度）。"""

    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    # ---------- runs ----------
    def start_run(self, plan_json=""):
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, plan_json, status) VALUES (?, ?, 'running')",
            (now_iso(), plan_json))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id, status):
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=? WHERE id=?",
            (now_iso(), status, run_id))
        self.conn.commit()

    # ---------- chapter_log ----------
    def log_chapter(self, run_id, stage, chapter, status, attempts=1, quality=None,
                    tokens_in=0, tokens_out=0, cost_yuan=0.0, error=None, session_id=None):
        self.conn.execute(
            """INSERT INTO chapter_log
               (run_id, stage, chapter, status, attempts, quality,
                tokens_in, tokens_out, cost_yuan, error, session_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id, stage, chapter) DO UPDATE SET
                 status=excluded.status, attempts=excluded.attempts,
                 quality=excluded.quality, error=excluded.error""",
            (run_id, stage, chapter, status, attempts, quality,
             tokens_in, tokens_out, cost_yuan, error, session_id))
        self.conn.commit()

    def chapter_status(self, run_id, stage, chapter):
        row = self.conn.execute(
            "SELECT status FROM chapter_log WHERE run_id=? AND stage=? AND chapter=?",
            (run_id, stage, chapter)).fetchone()
        return row[0] if row else None

    # ---------- cost_log ----------
    def log_cost(self, run_id, stage, chapter, model, tokens_in, tokens_out, cost_yuan,
                 estimated=0):
        self.conn.execute(
            "INSERT INTO cost_log (run_id, stage, chapter, model, tokens_in, tokens_out,"
            " cost_yuan, called_at, estimated) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, stage, chapter, model, tokens_in, tokens_out, cost_yuan,
             now_iso(), 1 if estimated else 0))
        self.conn.commit()

    def _migrate(self):
        """幂等迁移：为旧库 cost_log 补 estimated 列。"""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(cost_log)")]
        if "estimated" not in cols:
            self.conn.execute("ALTER TABLE cost_log ADD COLUMN estimated INTEGER DEFAULT 0")

    def sum_cost(self, run_id=None, stage=None):
        sql = "SELECT COALESCE(SUM(cost_yuan),0) FROM cost_log WHERE 1=1"
        args = []
        if run_id is not None:
            sql += " AND run_id=?"
            args.append(run_id)
        if stage is not None:
            sql += " AND stage=?"
            args.append(stage)
        return self.conn.execute(sql, args).fetchone()[0]

    # ---------- summary_log ----------
    def log_summary(self, run_id, rollup_index, path):
        self.conn.execute(
            "INSERT INTO summary_log (run_id, rollup_index, path, created_at) VALUES (?,?,?,?)",
            (run_id, rollup_index, path, now_iso()))
        self.conn.commit()

    def close(self):
        self.conn.close()
