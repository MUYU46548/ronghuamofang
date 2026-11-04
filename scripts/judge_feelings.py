# -*- coding: utf-8 -*-
"""第三条证据（M4=B）：让模型先出感受候选，交给作者终裁。

## 这不是“让模型评这一章”

模型只产**候选**：段落号 + 原句 + 它认为的感受 + 一句理由。落盘状态一律 `pending`，
终裁入口只有 `/judge/feelings/settle`（GUI-only）。它想在 payload 里写档位/结论，
`feelings.propose` 直接 400、一条都不落盘 —— 越层是这条链路唯一的硬边界。

## 三种用法

    python scripts/judge_feelings.py --chapter 3 --dry-run
        只出提示词与 token 预估，**零调用**（先看它要问什么，别盲烧）

    python scripts/judge_feelings.py --chapter 3
        调一次模型（engine: direct），把合格候选写入 data/judge/records.json
        的 `proposals.feelings`，界面判定页的“第三条证据”块会亮出待裁数

    python scripts/judge_feelings.py --chapter 3 --print
        把这一章已有的候选（含终裁状态）打出来，不调模型

## 边界

- `engine: self` / `hermes` 下本脚本**不跑真调用**（self 按设计禁调模型；hermes 走
  子会话，单次小请求没有收益）—— `--dry-run` 仍可用。理由同 book_extract。
- 章节正文取 `data/chapters/raw/NN.md`：判定对象是**原文**（回测台同口径）。
- 模型给的句子逐字校验：定位不到、段号不对、理由为空 —— 整批拒收并说清哪条不对，
  不许“部分接受”（静默丢弃比报错更糟）。
"""
import argparse
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.config_io import load_config_yaml                  # noqa: E402
from utils.judge import backtest as B                          # noqa: E402
from utils.judge import budget as BUD                          # noqa: E402
from utils.judge import feelings as F                          # noqa: E402
from utils.judge import text as T                              # noqa: E402
from utils import validator                                    # noqa: E402

PROMPT_REL = Path("prompts/judge_feelings.md")
MAX_ITEMS = 8


def read_prompt():
    p = PROMPT_REL
    if not p.exists():
        raise SystemExit("缺少提示词模板：%s" % p)
    raw = p.read_text(encoding="utf-8")
    # 剥 frontmatter（max_tokens / temperature 在这里读，正文才是提示词）
    meta, body = {}, raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2]
    return meta, body.strip()


def build_prompt(body, chapter, paragraphs, max_items):
    listing = "\n".join("%d. %s" % (i, p) for i, p in enumerate(paragraphs, 1))
    return (body.replace("{{chapter}}", str(chapter))
                .replace("{{paragraphs}}", listing)
                .replace("{{max_items}}", str(max_items)))


def parse_items(raw):
    """模型输出 → 条目列表（唯一解析器，容错围栏/思考块/断裂 JSON）。"""
    d = validator.parse_llm_json(raw)
    if d is None:
        return None, "模型输出不是合法 JSON（%s）" % (
            (validator.LAST_ERROR or {}).get("reason") or "未知原因")
    if isinstance(d, dict):
        for key in ("items", "feelings", "data", "list"):
            if isinstance(d.get(key), list):
                d = d[key]
                break
    if not isinstance(d, list):
        return None, "模型输出的顶层不是数组（收到 %s）" % type(d).__name__
    return d, ""


def main():
    ap = argparse.ArgumentParser(description="第三条证据：模型先出候选、作者终裁（M4）")
    ap.add_argument("--chapter", type=int, required=True, help="第几章（raw 稿）")
    ap.add_argument("--dry-run", action="store_true", help="只出提示词与预估，零调用")
    ap.add_argument("--print", dest="show", action="store_true", help="只看已有候选，零调用")
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS)
    ap.add_argument("--model-key", default="checker", help="config 里的模型角色")
    ap.add_argument("--show-chars", type=int, default=1200, help="dry-run 打印多少个字")
    args = ap.parse_args()

    if args.chapter <= 0:
        print("--chapter 必须是正整数")
        return 1

    if args.show:
        snap = F.snapshot(".", args.chapter)
        print("第 %d 章：候选 %d 条（待裁 %d / 采纳 %d / 推翻 %d / 驳回 %d），"
              "作者裁定 %d 条（其中来自模型 %d 条）"
              % (args.chapter, snap["total"], snap["counts"].get("pending", 0),
                 snap["counts"].get("accepted", 0), snap["counts"].get("overridden", 0),
                 snap["counts"].get("rejected", 0), snap["author_verdicts"],
                 snap["author_from_model"]))
        for x in snap["pending"]:
            print("  [待裁] #%s 第 %s 段 %s：%s —— %s"
                  % (x.get("id"), x.get("paragraph_index"), x.get("feeling"),
                     T.snippet(x.get("sentence"), 30), x.get("reason")))
        for x in snap["settled"]:
            print("  [%s] #%s 第 %s 段 %s"
                  % (x.get("status"), x.get("id"), x.get("paragraph_index"),
                     T.snippet(x.get("sentence"), 30)))
        return 0

    raw_p = Path(".") / B.RAW_DIR / ("%02d.md" % args.chapter)
    if not raw_p.exists():
        print("没有第 %d 章的 raw 稿（%s）—— 先跑写作阶段" % (args.chapter, raw_p))
        return 1
    body_text = raw_p.read_text(encoding="utf-8")
    paragraphs = T.paragraphs(body_text)
    if not paragraphs:
        print("第 %d 章正文为空，没有可挑的句子" % args.chapter)
        return 1

    meta, body = read_prompt()
    prompt = build_prompt(body, args.chapter, paragraphs, args.max_items)
    max_tokens = int(meta.get("max_tokens") or 4000)
    temperature = float(meta.get("temperature") or 0.4)
    # 输出预算：最多 max_items 条 × 每条约 60 字
    need = BUD.needed_tokens(max(200, args.max_items * 60))
    print("提示词 %d 字符 · 正文 %d 字 · 输出上限 %d tokens（按 %d 条估需 %d）"
          % (len(prompt), T.count_cn_words(body_text), max_tokens,
             args.max_items, need))

    if args.dry_run:
        print("\n--- 提示词前 %d 字（零调用）---" % args.show_chars)
        print(prompt[:args.show_chars])
        print("\n--dry-run：没有发起任何模型调用，一个字节都没写。")
        return 0

    if max_tokens < need:
        print("拒绝执行：输出上限 %d 装不下 %d（%d 条 × 约 60 字 × 系数）"
              % (max_tokens, need, args.max_items))
        return 2

    cfg = load_config_yaml("config/system.yaml") or {}
    engine = str(cfg.get("engine") or "direct").strip().lower()
    if engine != "direct":
        print("本脚本只走 engine: direct（当前 %s）。engine: self 按设计禁调模型；"
              "hermes 子会话不适合这种小请求。可先用 --dry-run 看它要问什么。" % engine)
        return 3

    from utils.llm_client import SYSTEM_PROMPT, make_client
    client = make_client(cfg, args.model_key)

    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    system = SYSTEM_PROMPT + "\n" + "\n".join(lines)
    text, usage, model_used, finish = client._post_chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": prompt}],
        temperature=temperature, max_tokens=max_tokens)
    tin = int((usage or {}).get("prompt_tokens") or 0)
    tout = int((usage or {}).get("completion_tokens") or 0)
    print("模型 %s · 输入 %d / 输出 %d tokens · finish=%s"
          % (model_used, tin, tout, finish))
    if finish == "length":
        print("输出被 max_tokens 截断（finish_reason=length）→ 候选不完整，未落盘。")
        return 2

    items, why = parse_items(text)
    if items is None:
        print("解析失败：%s" % why)
        return 4
    if not items:
        print("模型没挑出句子（输出空数组）—— 这本身是合法结果，不补造。")
        return 0

    ok, res = F.propose(".", args.chapter, items, model=str(model_used or ""),
                        note="judge_feelings.py")
    if not ok:
        print("提案被拒（一条都没落盘）：%s" % res.get("error"))
        for p in (res.get("problems") or [])[1:]:
            print("  - %s" % p)
        return 5
    print(res.get("message"))
    print("待作者终裁 %d 条 —— 在判定页“第三条证据”里采纳 / 推翻 / 驳回。"
          % int(res.get("pending") or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
