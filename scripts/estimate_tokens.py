# -*- coding: utf-8 -*-
"""生成前 token / 费用预估（确定性，零 LLM 调用）。

用途：GUI 在「运行阶段 / 全自动运行」前弹出预估确认框，让用户先看到
**预计还要消耗多少 token、大概花多少钱**，再决定是否开跑。

估算策略（先实测、后退化）
1. **历史优先**：logs/runs.db 的 cost_log 已记录每次真实调用的 tokens_in/out，
   同阶段历史均值 × 本次计划调用次数 = 最贴近现实的估算（会标注 basis 来源）。
2. **字符折算兜底**：无历史时按「1 token ≈ CHARS_PER_TOKEN 字符」折算
   （默认 1.5，偏保守 → 估多不估少，可在 config/system.yaml 用
   `estimate.chars_per_token` 覆盖），并对设定集等大文件施加与流水线一致的
   内联上限（INLINE_LIMIT）。

关于费用
- 参考价取自 utils/cost_tracker.RATES 刊例价；**不是账单**，真实用量以 cost_log 为准。
- 当前配置的模型不在单价表时会列进 unknown_rate_models（费用按 default 角色兜底，
  偏差可能很大），请先跑 scripts/price_wizard.py 补录。
- 下单前请以服务商官网实时价为准。

用法：
  python scripts/estimate_tokens.py                 # 全流程预估
  python scripts/estimate_tokens.py --stage 4       # 只估阶段4
  python scripts/estimate_tokens.py --json          # 输出 JSON（供 API 复用）
"""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text                       # noqa: E402
from utils.cost_tracker import RATES, estimate_cost_yuan  # noqa: E402

DEFAULT_CHARS_PER_TOKEN = 1.5
# 与直连引擎「目录内联」一致的总量上限（超限只 WARN 且静默丢文件）
INLINE_LIMIT = 220000
# stage4/5/6 的提示词是**按路径引用**设定集（由模型 read_file 按需读取），
# 不是整份内联。故对这类「按需读取」的上下文施加单次调用预算，
# 默认 40000 字符（≈2.7 万 token，与本项目 stage4 实测均值吻合）。
# 可在 config/system.yaml 用 `estimate.context_budget_chars` 覆盖。
DEFAULT_CONTEXT_BUDGET = 40000

STAGE_NAMES = {1: "素材→设定集", 2: "整体大纲", 3: "逐章大纲",
               4: "逐章写作", 5: "逻辑检查", 6: "润色", 7: "Word 成品"}

# 各阶段引用的提示词模板（字符数计入输入）
STAGE_PROMPTS = {
    1: ["stage1_materials.md", "stage1_scraps.md"],
    2: ["stage2_global_outline.md"],
    3: ["stage3_chapter_outline.md"],
    4: ["stage4_writing.md"],
    5: ["stage5_check.md"],
    6: ["stage6_polish.md"],
    7: [],
}


# ---------------------------------------------------------------- 配置 / 基础

def load_cfg():
    try:
        import yaml
        return yaml.safe_load(read_text("config/system.yaml")) or {}
    except Exception:                                       # noqa: BLE001
        return {}


def load_proj():
    try:
        import yaml
        return yaml.safe_load(read_text("config/project.yaml")) or {}
    except Exception:                                       # noqa: BLE001
        return {}


def chars_per_token(cfg=None):
    try:
        v = float(((cfg or {}).get("estimate") or {}).get("chars_per_token")
                  or DEFAULT_CHARS_PER_TOKEN)
        return v if v > 0.2 else DEFAULT_CHARS_PER_TOKEN
    except (TypeError, ValueError):
        return DEFAULT_CHARS_PER_TOKEN


def to_tokens(chars, cpt):
    """字符数 → token 数（向上取整，偏保守）。"""
    return int(math.ceil(max(0, chars) / cpt))


def context_budget(cfg=None):
    """按需读取型上下文的单次调用预算（字符）。"""
    try:
        v = float(((cfg or {}).get("estimate") or {}).get("context_budget_chars")
                  or DEFAULT_CONTEXT_BUDGET)
        return v if v > 1000 else DEFAULT_CONTEXT_BUDGET
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_BUDGET


def file_chars(path, limit=None):
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return 0
        n = len(read_text(p))
        return min(n, limit) if limit else n
    except Exception:                                       # noqa: BLE001
        return 0


def dir_chars(path, pattern="*.md", limit=None):
    d = Path(path)
    if not d.exists():
        return 0
    total = sum(file_chars(f) for f in sorted(d.glob(pattern)) if f.is_file())
    return min(total, limit) if limit else total


def prompt_chars(stage):
    return sum(file_chars(Path("prompts") / name) for name in STAGE_PROMPTS.get(stage, []))


def model_for(cfg, role):
    m = (cfg.get("model") or {}).get(role) or (cfg.get("model") or {}).get("default") or {}
    return m.get("provider") or "tokenhub", m.get("id") or "unknown"


def plan_calls(stage, cfg, proj):
    """该阶段本次预计的调用次数。"""
    if stage in (1, 2, 7):
        return {1: 1, 2: 1, 7: 0}[stage]
    n_chapters = int((proj.get("book") or {}).get("chapters") or 0) or 12
    if stage == 3:
        batch = int((cfg.get("chapter") or {}).get("batch_outline") or 10) or 10
        return max(1, math.ceil(n_chapters / batch))
    return n_chapters


# ---------------------------------------------------------------- 历史实测

def history_stats(stage, db_path="logs/runs.db"):
    """从 cost_log 取同阶段实测均值。排除 fake（token 是合成的，会污染估算）。"""
    try:
        from utils.db import RunDB
        p = Path(db_path)
        if not p.exists():
            return None
        db = RunDB(p)
        try:
            row = db.conn.execute(
                "SELECT COUNT(*), AVG(tokens_in), AVG(tokens_out)"
                " FROM cost_log WHERE stage = ? AND model != 'fake'"
                " AND (tokens_in > 0 OR tokens_out > 0)", (stage,)).fetchone()
        finally:
            db.close()
        if not row or not row[0]:
            return None
        return {"calls": int(row[0]),
                "avg_in": float(row[1] or 0),
                "avg_out": float(row[2] or 0)}
    except Exception:                                       # noqa: BLE001
        return None


# ---------------------------------------------------------------- 单阶段估算

def _mk_stage(stage, calls, t_in, t_out, cfg, role):
    provider, model = model_for(cfg, role)
    cost = estimate_cost_yuan(int(t_in), int(t_out), model=model,
                             provider=provider, role=role)
    return {
        "stage": stage,
        "name": STAGE_NAMES.get(stage, str(stage)),
        "role": role,
        "model": model,
        "provider": provider,
        "calls": int(calls),
        "tokens_in": int(t_in),
        "tokens_out": int(t_out),
        "cost_yuan": cost,
        "rate_known": provider == "hermes" or model in RATES,
    }


def estimate_stage(stage, cfg=None, proj=None, use_history=True):
    """单阶段预估。返回 dict（含 basis 明细）。"""
    cfg = cfg if cfg is not None else load_cfg()
    proj = proj if proj is not None else load_proj()
    role = {1: "architect", 2: "outliner", 3: "outliner", 4: "writer",
            5: "checker", 6: "polisher", 7: "default"}.get(stage, "default")
    calls = plan_calls(stage, cfg, proj)
    cpt = chars_per_token(cfg)

    if stage == 7 or calls == 0:
        row = _mk_stage(7, 0, 0, 0, cfg, "default")
        row.update({"basis": ["Word 转换为纯确定性处理，不消耗 token"],
                    "chars_in": 0, "chars_out": 0, "chars_per_token": cpt,
                    "source": "deterministic"})
        return row

    hist = history_stats(stage) if use_history else None
    if hist and hist["avg_in"] > 0:
        t_in = round(hist["avg_in"] * calls)
        t_out = round(hist["avg_out"] * calls)
        row = _mk_stage(stage, calls, t_in, t_out, cfg, role)
        row.update({
            "basis": ["基于历史实测：%d 次同阶段调用，平均每次输入 %d / 输出 %d token"
                      % (hist["calls"], round(hist["avg_in"]), round(hist["avg_out"])),
                      "本次计划 %d 次调用（章数/批数来自 config/project.yaml）" % calls],
            "chars_in": 0, "chars_out": 0, "chars_per_token": cpt,
            "source": "history",
            "history_calls": hist["calls"],
        })
        return row

    return _heuristic_stage(stage, cfg, proj, calls, cpt, role)


def _heuristic_stage(stage, cfg, proj, calls, cpt, role):
    """无历史时的字符折算兜底（对大文件施加流水线一致的内联上限）。"""
    book = proj.get("book") or {}
    chap = cfg.get("chapter") or {}
    tw = chap.get("target_words") or [1200, 3500]
    mid_words = int((int(tw[0]) + int(tw[-1])) / 2) if tw else 2200
    n_chapters = int(book.get("chapters") or 0) or 12
    p = prompt_chars(stage)
    ctx_budget = context_budget(cfg)

    materials = dir_chars((proj.get("materials") or {}).get("dir") or "materials/raw",
                          limit=INLINE_LIMIT)
    scraps = dir_chars((proj.get("materials") or {}).get("scraps_dir")
                       or "materials/original_scraps", limit=INLINE_LIMIT)
    # stage1/2/3 把素材/设定集整份内联进 prompt；stage4/5/6 只给路径由模型按需读取
    setting_inline = file_chars("data/setting/setting.json", limit=INLINE_LIMIT) or 12000
    setting = file_chars("data/setting/setting.json", limit=ctx_budget) or 12000
    global_outline = file_chars("data/outline/global.md", limit=ctx_budget) or 9000
    ch_outlines = dir_chars("data/outline/chapters", limit=INLINE_LIMIT)
    rolling = file_chars("data/summaries/rolling.md", limit=20000) or 4000
    style_ref_path = (book.get("style_reference") or "").strip()
    style_ref = file_chars(style_ref_path, limit=6000) if style_ref_path else 0
    existing = (dir_chars("data/chapters/refined") or dir_chars("data/chapters/checked")
                or dir_chars("data/chapters/raw")) or mid_words * n_chapters

    tiny = ["（无历史实测，按字符折算；跑过一次后会自动改用实测均值）"]
    if stage == 1:
        inp = materials + scraps + p
        out = max(8000, int(inp * 0.35))
        basis = tiny + ["素材 %d + 碎片 %d + 提示词 %d 字符（整份内联）"
                        % (materials, scraps, p),
                        "输出按输入 35% 估设定集"]
    elif stage == 2:
        inp = setting_inline + p + (1800 if book.get("user_outline") else 0)
        out = 10000
        basis = tiny + ["设定集 %d 字符（整份内联，上限 %d）" % (setting_inline, INLINE_LIMIT),
                        "输出按整体大纲 ~1 万字"]
    elif stage == 3:
        per_in = setting_inline + global_outline + p
        per_out = max(2000, mid_words // 6 * min(n_chapters // max(1, calls), n_chapters))
        inp, out = per_in * calls, per_out * calls
        basis = tiny + ["每次输入 = 设定集 + 整体大纲 + 提示词（每批 %d 章）"
                        % max(1, round(n_chapters / calls)),
                        "输出 ≈ 每章正文的 1/6"]
    elif stage == 4:
        per_in = setting + (ch_outlines // n_chapters if ch_outlines else mid_words // 6) \
            + rolling + style_ref + p
        per_out = mid_words
        inp, out = per_in * calls, per_out * calls
        basis = tiny + ["每章输入 = 设定集（按需读取，计 %d 字符）+ 本章大纲 + 滚动摘要"
                        " + 风格参考 + 提示词 ≈ %d 字符" % (setting, per_in),
                        "每章输出 ≈ %d 字（target_words 中位）" % per_out]
    elif stage == 5:
        per_in = existing // n_chapters + setting + p
        per_out = int(mid_words * 0.5)
        inp, out = per_in * calls, per_out * calls
        basis = tiny + ["每章输入 = 正文 + 设定集（按需读取 %d 字符）+ 提示词" % setting,
                        "每章输出 ≈ 正文 50%"]
    elif stage == 6:
        per_in = existing // n_chapters + setting + style_ref + p
        per_out = mid_words
        inp, out = per_in * calls, per_out * calls
        basis = tiny + ["每章输入 = 待润色正文 + 设定集（按需读取 %d 字符）+ 风格参考"
                        " + 提示词" % setting,
                        "每章输出 ≈ 正文等量（±20% 铁律）"]
    else:
        raise ValueError("未知阶段: %r" % stage)

    row = _mk_stage(stage, calls, to_tokens(inp, cpt), to_tokens(out, cpt), cfg, role)
    row.update({"basis": basis, "chars_in": int(inp), "chars_out": int(out),
                "chars_per_token": cpt, "source": "heuristic"})
    return row


# ---------------------------------------------------------------- 汇总

def current_spent():
    try:
        from utils.db import RunDB
        p = Path("logs/runs.db")
        if not p.exists():
            return 0.0
        db = RunDB(p)
        try:
            return float(db.conn.execute(
                "SELECT COALESCE(SUM(cost_yuan),0) FROM cost_log").fetchone()[0])
        finally:
            db.close()
    except Exception:                                       # noqa: BLE001
        return 0.0


def estimate(stages=None, cfg=None, proj=None, use_history=True):
    """全流程 / 指定阶段预估。返回可直接 JSON 化的 dict。"""
    cfg = cfg if cfg is not None else load_cfg()
    proj = proj if proj is not None else load_proj()
    want = stages or [1, 2, 3, 4, 5, 6, 7]
    rows = [estimate_stage(n, cfg, proj, use_history) for n in want]
    unknown = sorted({r["model"] for r in rows if not r["rate_known"]})
    limit = float((cfg.get("budget") or {}).get("limit_yuan") or 300)
    spent = current_spent()
    total_cost = round(sum(r["cost_yuan"] for r in rows), 4)
    hist_stages = [r["stage"] for r in rows if r.get("source") == "history"]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "chars_per_token": chars_per_token(cfg),
        "stages": rows,
        "totals": {
            "calls": sum(r["calls"] for r in rows),
            "tokens_in": sum(r["tokens_in"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
            "cost_yuan": total_cost,
            "rate_complete": not unknown,
        },
        "basis_source": ("history" if len(hist_stages) == len([r for r in rows if r["calls"]])
                         else ("mixed" if hist_stages else "heuristic")),
        "history_stages": hist_stages,
        "unknown_rate_models": unknown,
        "budget": {
            "limit_yuan": limit,
            "spent_yuan": round(spent, 4),
            "projected_yuan": round(spent + total_cost, 4),
            "projected_pct": round((spent + total_cost) / limit * 100, 1) if limit else 0.0,
            "exceeds": (spent + total_cost) > limit,
        },
        "disclaimer": "估算值（历史实测均值优先，无历史则字符折算 + 刊例价），非账单；"
                      "真实用量以 logs/runs.db 为准，下单前请以服务商官网实时价为准。",
    }


def format_text(result):
    src = {"history": "历史实测均值", "heuristic": "字符折算（无历史）",
           "mixed": "历史实测 + 字符折算混合"}.get(result["basis_source"], result["basis_source"])
    L = ["NovelForge token / 费用预估（口径：%s；字符折算系数 %s）"
         % (src, result["chars_per_token"]),
         "=" * 66,
         "%-4s %-12s %6s %12s %12s %10s" % ("阶段", "名称", "调用", "输入token", "输出token", "费用(元)")]
    for r in result["stages"]:
        L.append("%-4d %-12s %6d %12d %12d %10.4f%s"
                 % (r["stage"], r["name"], r["calls"], r["tokens_in"], r["tokens_out"],
                    r["cost_yuan"],
                    "" if r["rate_known"] else "  ← 单价未知"))
    t = result["totals"]
    L += ["-" * 66,
          "合计：%d 次调用 / 输入 %d / 输出 %d token / 约 %.2f 元"
          % (t["calls"], t["tokens_in"], t["tokens_out"], t["cost_yuan"]),
          "预算：已花 %.2f / 上限 %.2f 元；跑完预计 %.2f 元（%.1f%%）%s"
          % (result["budget"]["spent_yuan"], result["budget"]["limit_yuan"],
             result["budget"]["projected_yuan"], result["budget"]["projected_pct"],
             "  ⚠ 超预算" if result["budget"]["exceeds"] else "")]
    if result["unknown_rate_models"]:
        L.append("⚠ 以下模型无刊例价，费用按 default 角色兜底，偏差可能很大："
                 + "、".join(result["unknown_rate_models"])
                 + "（请先 python scripts/price_wizard.py 补录）")
    L.append("说明：" + result["disclaimer"])
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="生成前 token / 费用预估（确定性）")
    ap.add_argument("--stage", type=int, default=None, help="只估某一阶段（1-7）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-history", action="store_true", help="忽略历史实测，强制字符折算")
    ap.add_argument("--verbose", action="store_true", help="逐阶段打印估算依据")
    args = ap.parse_args()
    stages = [args.stage] if args.stage else None
    result = estimate(stages, use_history=not args.no_history)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))
        if args.verbose:
            print("")
            for r in result["stages"]:
                print("阶段%d 依据：" % r["stage"])
                for b in r.get("basis", []):
                    print("   - " + b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
