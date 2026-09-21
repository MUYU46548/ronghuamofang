# -*- coding: utf-8 -*-
"""成本报告 CLI（P0.7 #8）。

汇总 logs/runs.db 的 cost_log，让预算决策有数据支撑：
- 总览：总额、estimated（估算）占比、按阶段汇总
- --by-chapter：阶段4 各章费用
- --runs：最近运行记录

用法：
  python scripts/cost_report.py            # 总览
  python scripts/cost_report.py --by-chapter
  python scripts/cost_report.py --runs 5
"""
import argparse
import sqlite3
import sys
from pathlib import Path

from utils.file_io import read_text

DB = Path("logs/runs.db")


def _conn():
    if not DB.exists():
        print(f"[cost_report] 数据库不存在: {DB}（尚无运行记录）")
        sys.exit(1)
    conn = sqlite3.connect(str(DB))
    # 幂等迁移：旧库 cost_log 无 estimated 列时补齐（与 RunDB._migrate 一致）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cost_log)")]
    if cols and "estimated" not in cols:
        conn.execute("ALTER TABLE cost_log ADD COLUMN estimated INTEGER DEFAULT 0")
        conn.commit()
    return conn


def overview():
    conn = _conn()
    cur = conn.cursor()
    total = cur.execute(
        "SELECT COALESCE(SUM(cost_yuan),0), COUNT(*), COALESCE(SUM(estimated),0)"
        " FROM cost_log").fetchone()
    by_stage = cur.execute(
        "SELECT stage, COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
        " COALESCE(SUM(cost_yuan),0), COALESCE(SUM(estimated),0)"
        " FROM cost_log GROUP BY stage ORDER BY stage").fetchall()
    # 阶段2 的迭代拆分：chapter=0 是初版生成，chapter=N(≥1) 是第 N 轮精修
    stage2_split = cur.execute(
        "SELECT CASE WHEN COALESCE(chapter,0)=0 THEN 0 ELSE 1 END AS kind,"
        " COUNT(*), COALESCE(SUM(cost_yuan),0), COALESCE(SUM(tokens_in),0),"
        " COALESCE(SUM(tokens_out),0)"
        " FROM cost_log WHERE stage=2 GROUP BY kind ORDER BY kind").fetchall()
    conn.close()

    print("===== 成本总览 =====")
    print(f"总费用: {total[0]:.4f} 元（{total[1]} 次调用，其中估算 {total[2]} 次）")
    if total[1]:
        print(f"估算占比: {total[2]/total[1]*100:.0f}%（estimated=1 表示 token 为按任务文件估算，非实测）")
    print()
    print(f"{'阶段':<6}{'调用':>5}{'tok_in':>10}{'tok_out':>10}{'费用(元)':>12}{'估算':>5}")
    print("-" * 48)
    for s, n, ti, to, c, e in by_stage:
        name = {1: "素材归并", 2: "整体大纲", 3: "逐章大纲", 4: "写作", 5: "检查", 6: "润色"}.get(s, str(s))
        print(f"{s}({name}):{n:>4}{ti:>10}{to:>10}{c:>12.4f}{e:>5}")

    # 阶段2 明细（把「初版」与「迭代」分开 —— 迭代成本这一维度此前完全缺失）
    if stage2_split:
        print()
        print("阶段2 细分（初版 vs 迭代）：")
        for kind, n, c, ti, to in stage2_split:
            label = "初版生成" if kind == 0 else "大纲迭代"
            print(f"  {label:<10}{n:>4} 次{ti:>10}{to:>10}{c:>12.4f} 元")
        iter_cost = sum(r[2] for r in stage2_split if r[0] == 1)
        if iter_cost:
            print(f"  → 迭代占比 {iter_cost/total[0]*100:.0f}%（迭代总花费 {iter_cost:.4f} 元）"
                  if total[0] else "")
    return 0


def by_outline():
    """大纲迭代逐轮费用（stage=2，chapter=版本号；chapter=0 为初版）。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT COALESCE(chapter,0), COUNT(*), COALESCE(SUM(tokens_in),0),"
        " COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost_yuan),0),"
        " COALESCE(SUM(estimated),0), MIN(called_at), MAX(called_at)"
        " FROM cost_log WHERE stage=2 GROUP BY COALESCE(chapter,0)"
        " ORDER BY COALESCE(chapter,0)").fetchall()
    conn.close()
    if not rows:
        print("[cost_report] 阶段2 无记账记录（跑过 stage2 或 refine_outline 才有）")
        return 1
    print("===== 大纲逐轮费用（stage=2；v0 = 初版生成）=====")
    print(f"{'版本':>5}{'调用':>5}{'tok_in':>10}{'tok_out':>10}{'费用(元)':>12}{'累计(元)':>12}{'估算':>5}")
    print("-" * 60)
    acc = 0.0
    for ch, n, ti, to, c, e, _mn, _mx in rows:
        acc += c
        label = "v0(初版)" if ch == 0 else f"v{ch}"
        print(f"{label:>5}{n:>5}{ti:>10}{to:>10}{c:>12.4f}{acc:>12.4f}{e:>5}")
    rounds = len([r for r in rows if r[0] != 0])
    total_iter = sum(r[4] for r in rows if r[0] != 0)
    print("-" * 60)
    print(f"迭代轮次: {rounds}   迭代总费用: {total_iter:.4f} 元"
          + (f"   平均每轮: {total_iter/rounds:.4f} 元" if rounds else ""))
    return 0


def by_chapter():
    conn = _conn()
    rows = conn.execute(
        "SELECT chapter, tokens_in, tokens_out, cost_yuan, estimated FROM cost_log"
        " WHERE stage=4 ORDER BY chapter").fetchall()
    conn.close()
    if not rows:
        print("[cost_report] 阶段4 无记账记录")
        return 1
    print("===== 阶段4 分章费用 =====")
    print(f"{'章':>4}{'tok_in':>10}{'tok_out':>10}{'费用(元)':>12}{'估算':>5}")
    print("-" * 42)
    for ch, ti, to, c, e in rows:
        print(f"{ch:>4}{ti:>10}{to:>10}{c:>12.4f}{'是' if e else '':>5}")
    return 0


def runs(limit=5):
    conn = _conn()
    rows = conn.execute(
        "SELECT id, started_at, finished_at, status, plan_json FROM runs"
        " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    if not rows:
        print("[cost_report] 无运行记录")
        return 1
    print("===== 最近运行 =====")
    for r in rows:
        cost = _run_cost(r[0])
        print(f"  #{r[0]} [{r[3]}] {r[1]} ~ {r[2] or '-'}  费用 {cost:.4f} 元  {r[4] or ''}")
    return 0


def _run_cost(run_id):
    conn = _conn()
    v = conn.execute("SELECT COALESCE(SUM(cost_yuan),0) FROM cost_log WHERE run_id=?",
                     (run_id,)).fetchone()[0]
    conn.close()
    return v


def main():
    parser = argparse.ArgumentParser(description="NovelForge 成本报告")
    parser.add_argument("--by-chapter", action="store_true", help="阶段4 分章费用")
    parser.add_argument("--by-outline", action="store_true",
                        help="阶段2 逐轮费用（初版 + 每轮大纲迭代）")
    parser.add_argument("--runs", type=int, default=0, help="最近 N 次运行（默认不显示）")
    args = parser.parse_args()
    if args.by_chapter:
        return by_chapter()
    if args.by_outline:
        return by_outline()
    if args.runs:
        runs(args.runs)
    return overview()


if __name__ == "__main__":
    sys.exit(main())
