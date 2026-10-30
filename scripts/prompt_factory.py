# -*- coding: utf-8 -*-
"""出厂提示词副本：`prompts/factory/` 的同步、比对与还原判据（零 token）。

## 为什么必须有第二份（用户 2026-10-31 提的问题）

「提示词模板是否像测试用书一样有一键还原默认值的功能？如果被用户改坏了却不能
还原默认提示词，就失去了最后的保底。」

现状是**有备份链、没有保底**：`save_prompt` 每次写入都把旧版存进 `prompts/history/`，
所以「改前一步」能回退。但这解决不了三类真事故：

1. **滚动淘汰**：history 每模板只留最近 50 份（`PROMPT_HISTORY_KEEP`）。改得够多，
   最初那份（= 出厂版）被挤掉，从此回不去。
2. **手改不留痕**：用户直接用编辑器/第三方工具改 `prompts/stage4_writing.md`，
   history 里什么都不会有 —— 那才是最常见的「改坏了」。
3. **升级覆盖**：安装包升级会把 payload 里的 `prompts/` 整体换成新版出厂版，
   用户的自改随之消失；同时 history 也可能不跟着走。

所以这里把「出厂默认」固化成**独立副本** `prompts/factory/`：
它只由发版流程写入（`--sync`），用户日常保存模板**永远不会碰它**。
`POST /prompts/factory_restore` 从它还原，还原动作本身仍走 `save_prompt`
（当前版照例进 history，回滚可再撤销）。

## 纪律：`--sync` 是显式动作，不许挂进自动流程

一旦 sync 被自动调用（比如打包脚本顺手跑一次），用户在自己机器上改坏的模板
就会被当成新的「出厂默认」固化下来 —— 保底立刻失效，而且没有任何报错。
判断依据只能是人：`--sync` 会把它要改的每个文件、行数增删打出来，由操作者看。

## 用法

    python scripts/prompt_factory.py --list      # 看差异（只读，默认动作）
    python scripts/prompt_factory.py --check     # 同上，供 release-check 调用（不失败）
    python scripts/prompt_factory.py --sync      # 把当前模板定为出厂默认（需 --yes）

退出码：`--check` 恒 0（差异是信息不是错误）；`--sync` 无 `--yes` 或写失败退 1。
"""
import argparse
import io
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts"
FACTORY_DIR = PROMPTS_DIR / "factory"
# 与 nf_api.PROMPT_NAME_RE 同一判据：编辑器能改的模板 == 需要保底的范围。
NAME_RE = re.compile(r"^stage[1-7]_.*\.md$")

FACTORY_README = """# 出厂提示词副本（勿手改）

本目录是**出厂默认**提示词，由 `python scripts/prompt_factory.py --sync --yes`
在发版时从 `prompts/stage[1-7]_*.md` 固化而来。

- 界面上「还原出厂默认」读的就是这里；用户日常保存模板**不会**改动本目录。
- 想改出厂默认：改 `prompts/stage*.md` → 跑 `--sync --yes` → 提交本目录的变更。
- 直接手改本目录等于伪造出厂默认，会让「还原」变成「还原到你刚改坏的样子」。
"""


def stage_templates():
    """当前可编辑模板（白名单内，一级文件）。"""
    if not PROMPTS_DIR.is_dir():
        return []
    return sorted(p for p in PROMPTS_DIR.glob("*.md") if NAME_RE.match(p.name))


def factory_templates():
    if not FACTORY_DIR.is_dir():
        return []
    return sorted(p for p in FACTORY_DIR.glob("*.md") if NAME_RE.match(p.name))


def _read(p):
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def diff_report():
    """出厂副本 vs 当前模板。返回 [(name, kind, detail)]。

    kind: "same" / "changed" / "missing_factory" / "orphan_factory"
    """
    cur = {p.name: p for p in stage_templates()}
    fac = {p.name: p for p in factory_templates()}
    out = []
    for name in sorted(set(cur) | set(fac)):
        c, f = cur.get(name), fac.get(name)
        if c and not f:
            out.append((name, "missing_factory", "当前有、出厂副本没有 → 无法还原"))
            continue
        if f and not c:
            out.append((name, "orphan_factory", "出厂副本有、当前模板已不存在"))
            continue
        ct, ft = _read(c), _read(f)
        if ct is None or ft is None:
            out.append((name, "changed", "读不出来（编码/权限）"))
            continue
        if ct == ft:
            out.append((name, "same", ""))
        else:
            cl, fl = ct.splitlines(), ft.splitlines()
            out.append((name, "changed",
                        "当前 %d 行 / 出厂 %d 行，与出厂不一致（你可能改过）"
                        % (len(cl), len(fl))))
    return out


def cmd_list():
    rows = diff_report()
    if not rows:
        print("prompts/ 下没有可编辑模板（stage[1-7]_*.md），什么也没做。")
        return 0
    bad = [r for r in rows if r[1] != "same"]
    print("出厂副本：%s" % FACTORY_DIR)
    print("当前模板：%d 个（stage[1-7]_*.md）" % len(stage_templates()))
    print("")
    for name, kind, detail in rows:
        mark = {"same": "  =", "changed": "  ~", "missing_factory": "  +",
                "orphan_factory": "  -"}[kind]
        print("%s %s%s" % (mark, name, ("   " + detail) if detail else ""))
    print("")
    if bad:
        print("差异 %d 个。若这些是你自己改的 → 忽略；"
              "若刚改过模板并准备发版 → 跑 --sync --yes 更新出厂默认。" % len(bad))
    else:
        print("出厂副本与当前模板完全一致。")
    return 0


def cmd_check():
    """release-check 用：只报告，不失败（差异本身不代表仓库坏了）。"""
    rows = diff_report()
    missing = [r for r in rows if r[1] == "missing_factory"]
    if missing:
        print("[WARN] 有模板没有出厂副本，用户改坏后无法还原：%s"
              % ", ".join(n for n, _, _ in missing))
        print("       修法：python scripts/prompt_factory.py --sync --yes")
    changed = [r for r in rows if r[1] == "changed"]
    if changed:
        print("[WARN] %d 个模板与出厂副本不一致（本地改过就没问题；发版前需 --sync）"
              % len(changed))
    if not missing and not changed:
        print("[OK] 出厂提示词副本齐全且与当前模板一致（%d 个）" % len(rows))
    return 0


def cmd_sync(assume_yes=False):
    if not assume_yes:
        print("--sync 会把**当前模板**定为新的出厂默认。")
        print("在用户机器上误跑，等于把「改坏的样子」固化成保底 —— 所以必须显式确认。")
        print("确认无误请加 --yes：python scripts/prompt_factory.py --sync --yes")
        return 1
    rows = diff_report()
    todo = [r for r in rows if r[1] != "same"]
    if not todo:
        print("无需同步：出厂副本已与当前模板一致（%d 个）。" % len(rows))
        return 0
    FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    (FACTORY_DIR / "README.md").write_text(FACTORY_README, encoding="utf-8")
    print("同步到 %s：" % FACTORY_DIR)
    for name, kind, detail in todo:
        src = PROMPTS_DIR / name
        dst = FACTORY_DIR / name
        if src.is_file():
            shutil.copy2(src, dst)
            print("  写入 %-32s %s" % (name, detail or "新增出厂副本"))
        elif dst.is_file():
            dst.unlink()
            print("  删除 %-32s %s" % (name, detail))
    print("")
    print("已固化 %d 个模板为出厂默认（%s）。记得提交 prompts/factory/ 的变更。"
          % (len(factory_templates()), time.strftime("%Y-%m-%d %H:%M")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--list", action="store_true", help="列出差异（默认）")
    ap.add_argument("--check", action="store_true", help="release-check 口径（不失败）")
    ap.add_argument("--sync", action="store_true", help="把当前模板固化为出厂默认")
    ap.add_argument("--yes", action="store_true", help="配合 --sync 的显式确认")
    args = ap.parse_args(argv)
    if args.sync:
        return cmd_sync(args.yes)
    if args.check:
        return cmd_check()
    return cmd_list()


if __name__ == "__main__":
    sys.exit(main())
