# -*- coding: utf-8 -*-
"""吃书：把一本已写完的书吃成结构化正典（移植自 SP-1024 七件套，2026-10-27）。

## 为什么需要它

绒花墨坊此前的设定集只有一个来路：`materials/` 里的素材卡。可作者手里常常已经有
**一本写完的书**（旧稿 / 前作 / 想借鉴的参照作品），要接着写就得先把它的角色、
伏笔、时间线、设定抽出来 —— 这正是「吃书」。原实现活在 `SP-1024` 沙盒里，
用「出提示词 → 人工贴外部平台 → 回收」跑，本仓有模型客户端，改成一条命令。

## 用法

    python scripts/eat_book.py init 旧稿.txt --name old-book      # 拆章（第X回/章/卷）
    python scripts/eat_book.py run  old-book --dry-run            # 零 token：只报规模
    python scripts/eat_book.py run  old-book                      # 真跑（花钱，走 config 的模型）
    python scripts/eat_book.py status old-book
    python scripts/eat_book.py merge old-book                     # 预演并库（只增不改）
    python scripts/eat_book.py merge old-book --yes               # 真写 setting.json（先备份）

产物在 `data/eatbook/<name>/`：`chapters/` `summaries/` `characters.json`
`settings.json` `timeline.json` `foreshadow.json` `conflicts.json` `cliche.json`
`report.md` `cost.json`。

## 三条纪律

- **断点续跑**：每章摘要落盘即算完成；重跑跳过（`--force` 才重做）。
  这是「报错自愈」在吃书上的落地 —— 一章失败不该让整本重烧。
- **零 token 可预演**：`--dry-run` 只拆章、报章数与预计调用数，不发任何请求。
- **并库只增不改**：`merge` 不覆盖用户已有的角色/设定，写前备份到
  `data/setting/history/`；默认是预演，要 `--yes` 才落盘。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.interp_guard import ensure_working_python, missing_yaml_hint  # noqa: E402

if not ensure_working_python("scripts/eat_book.py"):
    sys.stderr.write("[eat_book] 启动被解释器守卫拦下：" + missing_yaml_hint() + "\n")
    raise SystemExit(1)

from utils.config_io import load_config_yaml          # noqa: E402
from utils.eatbook import runner                      # noqa: E402
from utils.eatbook.llm import LLMCallError, make_call, new_stats  # noqa: E402


def _parse_stages(raw: str):
    if not raw:
        return None
    out = []
    for part in str(raw).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise SystemExit("--stages 只接受号码（2,3,6,8,9,1,7），收到：%r" % part)
        n = int(part)
        if n not in runner.STAGES:
            raise SystemExit("未知阶段 %d（可选 %s）" % (n, sorted(runner.STAGES)))
        out.append(n)
    return out


def cmd_init(args) -> int:
    try:
        camp = runner.split_book(args.book, name=args.name,
                                 pattern=args.pattern,
                                 max_chapters=args.max_chapters)
    except (OSError, ValueError) as e:
        print("[eat_book] 拆章失败：%s" % e)
        return 1
    manifest = runner.load_manifest(camp)
    print("[eat_book] 已拆 %d 章 → %s" % (len(manifest["chapters"]), camp))
    print("下一步：python scripts/eat_book.py run %s --dry-run" % manifest["name"])
    return 0


def _load_camp_or_exit(name: str):
    """取吃书目录；不存在时给人话（不是 traceback）。"""
    camp = runner.resolve_camp(name)
    try:
        return camp, runner.load_manifest(camp)
    except FileNotFoundError:
        print("[eat_book] 找不到吃书目录：%s" % camp)
        print("          先建：python scripts/eat_book.py init <书稿.txt> --name %s" % name)
        return None, None


def cmd_run(args) -> int:
    camp, manifest = _load_camp_or_exit(args.name)
    if camp is None:
        return 1
    stages = _parse_stages(args.stages) or list(runner.STAGES)

    if args.dry_run:
        n = len(manifest["chapters"])
        print("[eat_book] 预演（零 token）：%s" % manifest.get("name"))
        print("  章数 %d ｜ 阶段 %s" % (n, ",".join(str(s) for s in stages)))
        if 2 in stages:
            print("  ② 摘要：%d 次调用（已有摘要会跳过）" % n)
        if any(s in stages for s in (3, 8, 9)):
            batches = max(1, (n + max(1, args.batch_size) - 1) // max(1, args.batch_size))
            print("  ③⑧⑨：每批 3 次调用 × %d 批 = %d 次" % (batches, batches * 3))
        if 6 in stages:
            print("  ⑥ 台账：1 次调用（全部摘要一次登记）")
        if 1 in stages:
            tail = args.tail or max(1, n // 3)
            print("  ① 一致性：%d 次调用（后 %d 章）" % (tail, tail))
        if 7 in stages:
            print("  ⑦ 病灶扫描：0 次调用（纯本地）")
        return 0

    cfg = load_config_yaml("config/system.yaml")
    from utils.llm_client import make_client
    client = make_client(cfg, "default")
    stats = new_stats()
    try:
        call = make_call(client, max_tokens=args.max_tokens, stats=stats)
    except LLMCallError as e:
        print("[eat_book] %s" % e)
        return 1

    print("[eat_book] 开跑：%s（模型 %s）"
          % (manifest.get("name"), getattr(client, "model", "?")))
    try:
        result = runner.run(camp, call, stages=stages, force=args.force,
                            batch_size=args.batch_size,
                            consistency_tail=args.tail,
                            wordlist_path=args.wordlist, stats=stats)
    except KeyboardInterrupt:
        print("\n[eat_book] 用户中断 —— 已完成的摘要不会丢，重跑即续")
        return 4
    print("\n[eat_book] 完成：%s" % result.get("report"))
    print("[eat_book] 花费：%d 次调用 · 输入 %d / 输出 %d token · 约 %.4f 元"
          % (stats["calls"], stats["tokens_in"], stats["tokens_out"],
             stats["cost_yuan"]))
    if result.get("failed"):
        print("[eat_book] %d 项失败（详见报告；重跑只补没做的）："
              % len(result["failed"]))
        for item in result["failed"][:5]:
            print("  - %s" % item)
        return 1
    return 0


def cmd_status(args) -> int:
    camp, manifest = _load_camp_or_exit(args.name)
    if camp is None:
        return 1
    chapters = manifest["chapters"]
    done = sum(1 for c in chapters if (camp / c.get("summary", "")).exists())
    print("吃书 · %s" % manifest.get("name"))
    print("  目录：%s" % camp)
    print("  来源：%s" % manifest.get("source"))
    print("  章节：%d ｜ 已摘要：%d" % (len(chapters), done))
    for fname in ("characters.json", "settings.json", "timeline.json",
                  "foreshadow.json", "conflicts.json", "cliche.json"):
        p = camp / fname
        if not p.exists():
            print("  %-16s （未生成）" % fname)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            print("  %-16s （读不出来）" % fname)
            continue
        if isinstance(data, list):
            print("  %-16s %d 条" % (fname, len(data)))
        elif isinstance(data, dict):
            print("  %-16s %s" % (fname, {k: v for k, v in data.items()
                                          if isinstance(v, int)}))
    cost = camp / "cost.json"
    if cost.exists():
        print("  花费：%s" % cost.read_text(encoding="utf-8").replace("\n", " ")[:200])
    return 0


def cmd_merge(args) -> int:
    camp, _manifest = _load_camp_or_exit(args.name)
    if camp is None:
        return 1
    report = runner.merge_into_setting(camp, dry_run=not args.yes)
    print("[eat_book] 并库%s：%s" % ("预演" if not args.yes else "已写入",
                                  report["setting"]))
    print("  新角色 %d：%s" % (len(report["added_characters"]),
                              "、".join(report["added_characters"][:10]) or "（无）"))
    print("  新设定 %d：%s" % (len(report["added_world"]),
                              "、".join(report["added_world"][:10]) or "（无）"))
    print("  新伏笔 %d ｜ 新时间线 %d"
          % (len(report["added_fragments"]), report["added_timeline"]))
    if not args.yes:
        print("  预演不写盘；确认无误后加 --yes（会先备份 data/setting/history/）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="吃书：把一本写完的书抽成正典四表（摘要/角色/时间线/设定）+ 伏笔台账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="拆章建目录")
    p_init.add_argument("book", help="书稿路径（.txt，UTF-8 或 GB18030）")
    p_init.add_argument("--name", default="", help="吃书目录名（默认取文件名）")
    p_init.add_argument("--pattern", default="",
                        help=r"分章正则（默认 ^\s*第[0-9零一二三…]+\s*[回章节卷]）")
    p_init.add_argument("--max-chapters", type=int, default=0,
                        help="只拆前 N 章（试跑用）")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="跑管线（默认 2→3→6→8→9→1→7）")
    p_run.add_argument("name", help="吃书目录名或路径")
    p_run.add_argument("--stages", default="", help="只跑这些阶段，如 2,7")
    p_run.add_argument("--dry-run", action="store_true", help="零 token 预演")
    p_run.add_argument("--force", action="store_true", help="重做已有摘要")
    p_run.add_argument("--batch-size", type=int, default=30, help="③⑧⑨ 每批章数")
    p_run.add_argument("--tail", type=int, default=0, help="① 只查后 N 章（默认 1/3）")
    p_run.add_argument("--max-tokens", type=int, default=None, help="单次输出上限")
    p_run.add_argument("--wordlist", default=None, help="⑦ 自定义病灶词表（一行一词）")
    p_run.set_defaults(func=cmd_run)

    p_st = sub.add_parser("status", help="看见进度与各表条数")
    p_st.add_argument("name")
    p_st.set_defaults(func=cmd_status)

    p_mg = sub.add_parser("merge", help="把吃出来的卡并进 setting.json（只增不改）")
    p_mg.add_argument("name")
    p_mg.add_argument("--yes", action="store_true", help="真的写盘（默认只预演）")
    p_mg.set_defaults(func=cmd_merge)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
