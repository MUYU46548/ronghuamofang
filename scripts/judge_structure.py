# -*- coding: utf-8 -*-
"""结构指标 CLI（零 token，只读；`--write` 才落盘）。

回答一个七维账本回答不了的问题：**这份稿子的对白是不是死板**。

七维评审只验“引句能否逐字命中原文 + 维不缺”，不判断好坏 —— 实测 8.14 分、
4 批 complete=True，同一份文本的 1269 句对白里只有 33 句问号，几乎全是单回合问答。
本命令把六项**确定性结构指标**算出来（问号占对白 / 连续单回合问答 / 角色句长差 /
极短独立段密度 / 对话段占比 / 平均段长），越线就标“需人工抽样”。

**不给文学分、不合成总分** —— 机器判不了“写得好不好”，那是作者的活。

用法（workdir = 项目根，Python 用项目 .venv）:

    python scripts/judge_structure.py                 # 打印摘要（不落盘）
    python scripts/judge_structure.py --json          # 机读
    python scripts/judge_structure.py --chapter 3     # 只看第 3 章
    python scripts/judge_structure.py --write         # 落盘 data/review/structure.{json,md}
    python scripts/judge_structure.py --root <其他书>

退出码：0 = 无越线项；1 = 有章节需人工抽样；2 = 读取/口径失败。
阈值只在 `data/judge/caliber.json`（J16–J21），代码里不写死。
"""
import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from utils.interp_guard import ensure_working_python  # noqa: E402

ensure_working_python("scripts/judge_structure.py")

from utils.judge import structure as st  # noqa: E402

REVIEW_DIR = "data/review"


def main():
    ap = argparse.ArgumentParser(description="阶段 5 结构指标（零 token）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="项目根目录")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--chapter", type=int, default=None, help="只看第 N 章")
    ap.add_argument("--write", action="store_true",
                    help="落盘到 data/review/structure.json + structure.md（stage5 会自动做）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    res = st.collect(root, limit=[args.chapter] if args.chapter else None)
    if not res.get("ok"):
        print("[ERROR] " + str(res.get("error")), file=sys.stderr)
        return 2

    if args.write:
        out = st.write(root / REVIEW_DIR, res)
        print("[structure] 已写入 " + str(out))

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(st.render_markdown(res))

    need = (res.get("summary") or {}).get("needs_sampling") or []
    return 1 if need else 0


if __name__ == "__main__":
    sys.exit(main())
